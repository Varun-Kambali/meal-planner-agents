"""POST /v1/week: a week written as a stream of events, what it refuses, and the logs it writes."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Literal

import pytest
from app_helpers import (
    EXTRA_FOODS,
    MARK,
    ExplodingNutrition,
    assert_no_marker,
    log_entries,
    make_app,
    marked_plan,
    parse_stream,
    plan_json,
    renamed_replies,
    serving,
    week_of,
)
from nutrition_mcp.schemas import SearchResult
from pydantic import BaseModel
from week_helpers import (
    SLOTS,
    SpyNutrition,
    model_for,
)

from meal_planner.llm import ModelPort, ModelReply
from meal_planner.log import get_logger
from meal_planner.nutrition import NutritionError

# Writing a week


def test_week_streams_events_and_done(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan_json())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_stream(response.text)
    kinds = [name for name, _ in events]
    assert kinds[0] == "slot_started"
    assert kinds[-1] == "week_done"
    assert kinds.count("slot_started") == 4
    assert kinds.count("slot_written") == 4
    assert kinds.count("recipe_checked") == 28
    assert kinds.count("week_done") == 1
    assert all(data["kind"] == name for name, data in events)
    week = week_of(events)
    assert len(week.recipes) == 28
    assert week.unwritten == []
    # A tuple-keyed dict cannot be JSON, so the warnings travel as rows.
    assert isinstance(events[-1][1]["week"]["no_waste"], list)


def test_stream_ends_cleanly_after_rate_limited_slot(tmp_path: Path) -> None:
    model = model_for(errors={"lunch": "rate_limited"})

    with serving(tmp_path, model=model) as client:
        response = client.post("/v1/week", json=plan_json())

    assert response.status_code == 200
    events = parse_stream(response.text)
    names = [name for name, _ in events]
    assert names[-1] == "week_done"
    assert "slot_failed" in names
    assert "error" not in names
    week = week_of(events)
    assert len(week.recipes) == 21
    assert [(slot, reason) for _, slot, reason in week.unwritten] == [("lunch", "rate_limited")] * 7
    assert len({day for day, _, _ in week.unwritten}) == 7


def test_invalid_rules_422_names_field(tmp_path: Path) -> None:
    plan = plan_json()
    plan["slots"][1]["kcal_share"] = 7

    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan)

    assert response.status_code == 422
    problems = response.json()["detail"]
    assert any(p["loc"] == ["body", "slots", 1, "kcal_share"] for p in problems)
    assert all(set(p) == {"loc", "msg"} for p in problems)
    assert "input" not in response.text
    assert "url" not in response.text


def test_a_rejected_value_is_never_echoed(tmp_path: Path) -> None:
    plan = plan_json()
    plan["slots"][0]["max_active_min"] = f"about {MARK}"
    plan["extra_field"] = MARK

    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan)

    assert response.status_code == 422
    assert MARK not in response.text


def test_a_body_that_is_not_json_is_422_without_an_echo(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.post(
            "/v1/week", content=f"{{{MARK}".encode(), headers={"content-type": "application/json"}
        )

    assert response.status_code == 422
    assert MARK not in response.text
    assert set(response.json()["detail"][0]) == {"loc", "msg"}


def test_a_food_the_catalogue_lacks_is_422_naming_the_field(tmp_path: Path) -> None:
    plan = plan_json()
    plan["food"]["liked_foods"] = ["paneer", "unobtainium"]

    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "food", "liked_foods"]
    assert "unobtainium" in problem["msg"]


class UnreachableNutrition(SpyNutrition):
    """nutrition-mcp gone: every call fails as unreachable."""

    async def search(
        self, query: str, kind: Literal["dish", "ingredient"] | None = None, limit: int = 5
    ) -> SearchResult:
        raise NutritionError(
            "nutrition-mcp could not answer search_food: Connection closed", unreachable=True
        )


def test_a_week_is_a_503_naming_the_step_when_nutrition_mcp_cannot_be_reached(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    model = model_for()
    nutrition = UnreachableNutrition(*EXTRA_FOODS)

    with serving(tmp_path, model=model, nutrition=nutrition) as client:
        response = client.post("/v1/week", json=plan_json())

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert "nutrition" in response.json()["detail"]
    assert model.calls == []
    entries = log_entries(capfd.readouterr().err)
    (failed,) = [e for e in entries if e["event"] == "nutrition_unreachable"]
    assert failed["error_type"] == "NutritionError"
    assert failed["request_id"] == response.headers["x-request-id"]


def test_rules_that_are_invalid_are_a_422_even_when_nutrition_mcp_is_down(tmp_path: Path) -> None:
    plan = plan_json()
    plan["slots"][1]["kcal_share"] = 7

    with serving(tmp_path, nutrition=UnreachableNutrition(*EXTRA_FOODS)) as client:
        response = client.post("/v1/week", json=plan)

    assert response.status_code == 422


def test_body_over_64kb_413(tmp_path: Path) -> None:
    body = json.dumps(plan_json()).encode() + b" " * 65536

    with serving(tmp_path) as client:
        response = client.post(
            "/v1/week", content=body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 413
    assert "65536" in response.json()["detail"]


def test_rate_limit_429(tmp_path: Path) -> None:
    with serving(tmp_path, rate_limit_per_minute=2) as client:
        first = client.get("/v1/catalogue")
        second = client.get("/v1/catalogue")
        third = client.post("/v1/week", json=plan_json())
        health = client.get("/healthz")

    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert 1 <= int(third.headers["retry-after"]) <= 60
    assert health.status_code == 200


def test_a_failure_after_the_stream_starts_ends_with_one_error_event(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with serving(tmp_path, nutrition=ExplodingNutrition(*EXTRA_FOODS)) as client:
        response = client.post("/v1/week", json=plan_json())

    assert response.status_code == 200
    events = parse_stream(response.text)
    name, data = events[-1]
    assert name == "error"
    assert data == {"kind": "error", "step": "week", "message": data["message"]}
    assert MARK not in data["message"]
    assert [n for n, _ in events].count("error") == 1
    assert "week_done" not in [n for n, _ in events]
    captured = capfd.readouterr()
    assert MARK not in captured.err + captured.out
    (failed,) = [e for e in log_entries(captured.err) if e["event"] == "run_failed"]
    assert failed["error_type"] == "RuntimeError"
    assert failed["task"] == "week"


def test_log_lines_written_while_the_stream_runs_carry_its_request_id(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan_json())

    request_id = response.headers["x-request-id"]
    entries = log_entries(capfd.readouterr().err)
    (done,) = [e for e in entries if e["event"] == "run_done"]
    assert done["request_id"] == request_id
    assert done["task"] == "week"
    assert done["input_tokens"] == 0


class LoggingModel:
    """A model that writes a log line after each call, as the real client does."""

    def __init__(self, inner: ModelPort) -> None:
        self._inner = inner

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        reply = await self._inner.structured(
            task=task, system=system, user=user, schema=schema, timeout_s=timeout_s
        )
        get_logger("meal_planner.test").info("model_call", extra={"task": task})
        return reply


def test_log_lines_written_inside_the_run_carry_the_request_id_too(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with serving(tmp_path, model=LoggingModel(model_for())) as client:
        response = client.post("/v1/week", json=plan_json())

    entries = log_entries(capfd.readouterr().err)
    calls = [e for e in entries if e["event"] == "model_call"]
    assert len(calls) == 4
    assert {e["request_id"] for e in calls} == {response.headers["x-request-id"]}


# Rules and typed text stay out of the logs


def test_no_rules_text_in_logs(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    with serving(tmp_path, model=renamed_replies()) as client:
        response = client.post("/v1/week", json=marked_plan())

    assert MARK in response.text  # the plan's own words do come back to the person who sent them
    assert response.status_code == 200
    assert parse_stream(response.text)[-1][0] == "week_done"
    assert records
    assert_no_marker(records, capfd.readouterr())


def test_no_rules_text_in_logs_when_the_rules_are_rejected(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    plan = marked_plan()
    plan["food"]["liked_foods"] = [f"liked_{MARK}"]
    plan["slots"][1]["max_active_min"] = f"soon {MARK}"

    with serving(tmp_path) as client:
        bad_values = client.post("/v1/week", json=plan)
        not_json = client.post(
            "/v1/week", content=MARK.encode(), headers={"content-type": "application/json"}
        )

    assert bad_values.status_code == 422
    assert not_json.status_code == 422
    assert_no_marker(records, capfd.readouterr())


def test_no_rules_text_in_logs_when_the_run_fails(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    with serving(tmp_path, nutrition=ExplodingNutrition(*EXTRA_FOODS)) as client:
        client.post("/v1/week", json=marked_plan())

    assert_no_marker(records, capfd.readouterr())


# A client that goes away


@pytest.mark.anyio
async def test_a_client_that_goes_away_stops_the_run(tmp_path: Path) -> None:
    """The ASGI app is driven by hand: the test client cannot disconnect part way through."""
    release = asyncio.Event()
    model = model_for(gates={slot: release for slot in SLOTS})
    app = make_app(tmp_path, model=model)
    body = json.dumps(plan_json()).encode()
    disconnect = asyncio.Event()
    pending = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if pending:
            return pending.pop(0)
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/week",
        "raw_path": b"/v1/week",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("203.0.113.9", 4000),
        "server": ("testserver", 80),
    }

    async with app.router.lifespan_context(app), asyncio.timeout(10):
        run = asyncio.create_task(app(scope, receive, send))  # type: ignore[arg-type]
        while model.in_flight < len(SLOTS):
            await asyncio.sleep(0)
        disconnect.set()
        await run
        # The calls are cancelled with the run, each at its next step in the loop.
        while model.in_flight:
            await asyncio.sleep(0)
        release.set()
        await asyncio.sleep(0)

    assert len(model.calls) == len(SLOTS)
    assert not any(b"week_done" in m.get("body", b"") for m in sent)
