"""The request id, the body limit and the rate limit, on a small app that shows what they do."""

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from meal_planner.log import request_id_var
from meal_planner.middleware import BodyLimit, RateLimit, RateLimiter, RequestContext

LIMIT = 100


class Note(BaseModel):
    text: str


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(clock: Clock | None = None, rate: int = 3) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/size")
    async def size(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    @app.post("/v1/note")
    async def note(body: Note) -> dict[str, int]:
        return {"chars": len(body.text)}

    @app.post("/v1/big")
    async def big(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    @app.get("/v1/id")
    async def which(request: Request) -> dict[str, str | None]:
        return {"id": request_id_var.get()}

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("SECRET-TEXT-IN-AN-ERROR")

    app.add_middleware(BodyLimit, limit=LIMIT, per_path={"/v1/big": 1000})
    limiter = RateLimiter(rate, clock=clock or Clock())
    app.add_middleware(RateLimit, limiter=limiter, prefix="/v1/")
    app.add_middleware(RequestContext)
    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(build(rate=1000)) as c:
        yield c


def chunks(*parts: bytes) -> Iterator[bytes]:
    yield from parts


# Request id


def test_every_response_has_a_fresh_request_id(client: TestClient) -> None:
    first = client.get("/healthz").headers["x-request-id"]
    second = client.get("/healthz").headers["x-request-id"]

    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert first != second


def test_the_handler_sees_the_same_id_the_client_gets(client: TestClient) -> None:
    response = client.get("/v1/id")

    assert response.json() == {"id": response.headers["x-request-id"]}


def test_the_id_is_not_left_set_after_the_request(client: TestClient) -> None:
    client.get("/v1/id")

    assert request_id_var.get() is None


def test_an_error_response_has_an_id_too(client: TestClient) -> None:
    response = client.post("/v1/size", content=b"x" * (LIMIT + 1))

    assert response.status_code == 413
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


def test_an_unhandled_error_is_a_500_that_says_nothing_and_logs_only_its_class(
    client: TestClient, capfd: pytest.CaptureFixture[str]
) -> None:
    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    assert "x-request-id" in response.headers
    captured = capfd.readouterr()
    assert "SECRET-TEXT" not in captured.err + captured.out
    lines = [json.loads(line) for line in captured.err.splitlines() if line.startswith("{")]
    (line,) = [entry for entry in lines if entry["event"] == "request_failed"]
    assert line["error_type"] == "RuntimeError"
    assert line["request_id"] == response.headers["x-request-id"]


# Body limit


def test_a_body_at_the_limit_goes_through(client: TestClient) -> None:
    response = client.post("/v1/size", content=b"x" * LIMIT)

    assert response.json() == {"bytes": LIMIT}


def test_a_body_over_the_limit_by_its_length_header_is_413(client: TestClient) -> None:
    response = client.post("/v1/size", content=b"x" * (LIMIT + 1))

    assert response.status_code == 413
    assert str(LIMIT) in response.json()["detail"]


def test_a_chunked_body_over_the_limit_is_cut_off_at_the_limit(client: TestClient) -> None:
    response = client.post("/v1/size", content=chunks(*[b"x" * 40] * 3))

    assert "content-length" not in response.request.headers
    assert response.status_code == 413


def test_a_chunked_body_under_the_limit_goes_through(client: TestClient) -> None:
    response = client.post("/v1/size", content=chunks(*[b"x" * 40] * 2))

    assert response.json() == {"bytes": 80}


def test_a_body_that_lies_about_its_length_is_still_cut_off(client: TestClient) -> None:
    # The header says it is small; the bytes that arrive are not.
    response = client.post(
        "/v1/size", content=chunks(*[b"x" * 60] * 2), headers={"content-length": "10"}
    )

    assert response.status_code == 413


def test_the_limit_applies_to_a_body_a_model_reads_too(client: TestClient) -> None:
    text = "y" * LIMIT
    response = client.post("/v1/note", json={"text": text})

    assert response.status_code == 413


def test_a_chunked_body_a_model_reads_is_a_413_and_not_a_400(client: TestClient) -> None:
    body = json.dumps({"text": "y" * LIMIT}).encode()
    response = client.post(
        "/v1/note",
        content=chunks(body[:60], body[60:]),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413


def test_a_route_can_have_its_own_limit(client: TestClient) -> None:
    assert client.post("/v1/big", content=b"x" * 900).json() == {"bytes": 900}
    assert client.post("/v1/big", content=b"x" * 1001).status_code == 413
    assert client.post("/v1/big", content=chunks(*[b"x" * 400] * 3)).status_code == 413


# Rate limit


def test_the_requests_over_the_limit_get_429_with_retry_after() -> None:
    clock = Clock()
    with TestClient(build(clock, rate=3)) as client:
        statuses = [client.get("/v1/id").status_code for _ in range(5)]
        blocked = client.get("/v1/id")

    assert statuses == [200, 200, 200, 429, 429]
    assert blocked.status_code == 429
    assert re.fullmatch(r"[1-9][0-9]?", blocked.headers["retry-after"])
    assert int(blocked.headers["retry-after"]) <= 60
    assert "x-request-id" in blocked.headers
    assert isinstance(blocked.json()["detail"], str)


def test_retry_after_counts_down_to_the_oldest_request_leaving_the_window() -> None:
    clock = Clock()
    with TestClient(build(clock, rate=2)) as client:
        client.get("/v1/id")
        clock.now += 20
        client.get("/v1/id")
        clock.now += 10.5

        blocked = client.get("/v1/id")

        # The first request was 30.5 s ago, so it leaves the window in 29.5 s: 30 whole seconds.
        assert blocked.headers["retry-after"] == "30"

        clock.now += 29.5
        assert client.get("/v1/id").status_code == 200


def test_a_blocked_request_does_not_extend_the_wait() -> None:
    clock = Clock()
    with TestClient(build(clock, rate=1)) as client:
        client.get("/v1/id")
        for _ in range(5):
            clock.now += 1
            assert client.get("/v1/id").status_code == 429
        clock.now = 1000.0 + 60

        assert client.get("/v1/id").status_code == 200


def test_healthz_and_other_paths_are_not_limited() -> None:
    with TestClient(build(rate=1)) as client:
        client.get("/v1/id")

        assert client.get("/v1/id").status_code == 429
        assert [client.get("/healthz").status_code for _ in range(5)] == [200] * 5


def test_each_client_address_has_its_own_allowance() -> None:
    app = build(rate=1)
    with (
        TestClient(app, client=("10.0.0.1", 5000)) as first,
        TestClient(app, client=("10.0.0.2", 5000)) as second,
    ):
        assert first.get("/v1/id").status_code == 200
        assert first.get("/v1/id").status_code == 429
        assert second.get("/v1/id").status_code == 200


def test_the_limiter_forgets_clients_that_have_gone_quiet() -> None:
    clock = Clock()
    limiter = RateLimiter(1, clock=clock)
    for number in range(2000):
        limiter.check(f"10.0.{number // 250}.{number % 250}")
    clock.now += 61

    limiter.check("10.9.9.9")

    assert limiter.tracked() <= 2


def test_the_limiter_says_how_long_to_wait() -> None:
    clock = Clock()
    limiter = RateLimiter(2, window_s=60, clock=clock)

    results: list[Any] = [limiter.check("a"), limiter.check("a")]
    clock.now += 0.25
    results.append(limiter.check("a"))

    assert results == [None, None, 60]
