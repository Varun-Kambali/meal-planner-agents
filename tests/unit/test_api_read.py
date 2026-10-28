"""POST /v1/log/read: a typed meal read into nutrition-mcp foods, amounts and numbers."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import pytest
from app_helpers import (
    MARK,
    assert_no_marker,
    log_entries,
    serving,
)
from fastapi.testclient import TestClient
from nutrition_mcp.schemas import MealItemIn, MealResult, SearchResult
from pydantic import BaseModel
from reader_helpers import ReaderNutrition, reading, said, write_reader_catalogue

from meal_planner.llm import RATE_LIMITED, SCHEMA_MISMATCH, FakeModel, ModelPort, ModelReply
from meal_planner.nutrition import NutritionError, NutritionPort
from meal_planner.reader import ReadResult

# The meal reader route


def reader_model(*replies: str, choices: tuple[str, ...] = ()) -> FakeModel:
    return FakeModel({"read_meal": list(replies), "choose_food": list(choices)})


@contextmanager
def reading_meals(
    folder: Path, model: ModelPort, nutrition: NutritionPort | None = None, **changes: Any
) -> Iterator[TestClient]:
    """The app with the foods typed meals name, for the reader."""
    catalogue = write_reader_catalogue(folder)
    nutrition = nutrition or ReaderNutrition()
    with serving(
        folder, model=model, nutrition=nutrition, catalogue_path=catalogue, **changes
    ) as c:
        yield c


def test_a_typed_meal_comes_back_as_foods_amounts_and_a_total(tmp_path: Path) -> None:
    text = "2 rotis and some pulao"
    model = reader_model(
        reading(said("roti", 2, "roti", "2"), said("pulao", None, None, "some")),
    )

    with reading_meals(tmp_path, model) as client:
        response = client.post("/v1/log/read", json={"text": text})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    result = ReadResult.model_validate(response.json())
    assert result.text_length == len(text)
    assert [(item.name, item.status) for item in result.items] == [
        ("roti", "matched"),
        ("pulao", "missing"),
    ]
    assert result.items[0].grams == 80
    assert result.missing == ["pulao"]
    assert result.total is not None
    assert result.total.total.kcal == 240


@pytest.mark.parametrize(
    "body",
    [{"text": ""}, {"text": "  \n "}, {}, {"text": 5}, {"text": "2 rotis", "rules": {}}],
)
def test_text_that_is_empty_or_not_text_is_422_without_a_model_call(
    tmp_path: Path, body: dict[str, Any]
) -> None:
    model = reader_model()

    with reading_meals(tmp_path, model) as client:
        response = client.post("/v1/log/read", json=body)

    assert response.status_code == 422
    assert all(set(p) == {"loc", "msg"} for p in response.json()["detail"])
    assert all(p["loc"][:1] == ["body"] for p in response.json()["detail"])
    assert model.calls == []


def test_text_over_500_is_422(tmp_path: Path) -> None:
    model = reader_model(reading())

    with reading_meals(tmp_path, model) as client:
        too_long = client.post("/v1/log/read", json={"text": "x" * 501})
        longest = client.post("/v1/log/read", json={"text": "x" * 500})

    assert too_long.status_code == 422
    (problem,) = too_long.json()["detail"]
    assert problem["loc"] == ["body", "text"]
    assert "500" in problem["msg"]
    assert "xxxxx" not in too_long.text
    assert longest.status_code == 200
    assert len(model.calls) == 1


def test_the_text_limit_is_the_setting(tmp_path: Path) -> None:
    with reading_meals(tmp_path, reader_model(reading()), max_meal_text_chars=10) as client:
        too_long = client.post("/v1/log/read", json={"text": "a" * 11})
        fits = client.post("/v1/log/read", json={"text": "a" * 10})

    assert (too_long.status_code, fits.status_code) == (422, 200)
    assert "10" in too_long.json()["detail"][0]["msg"]


def test_a_read_body_over_the_limit_is_413(tmp_path: Path) -> None:
    body = json.dumps({"text": "2 rotis"}).encode() + b" " * 65536
    model = reader_model()

    with reading_meals(tmp_path, model) as client:
        response = client.post(
            "/v1/log/read", content=body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 413
    assert model.calls == []


def test_the_read_route_is_rate_limited(tmp_path: Path) -> None:
    with reading_meals(tmp_path, reader_model(reading()), rate_limit_per_minute=1) as client:
        first = client.post("/v1/log/read", json={"text": "nothing"})
        second = client.post("/v1/log/read", json={"text": "nothing"})

    assert (first.status_code, second.status_code) == (200, 429)
    assert 1 <= int(second.headers["retry-after"]) <= 60


class AnsweringWith:
    """A model that answers every call with an error, or with a reply that cannot be read."""

    def __init__(self, error: str) -> None:
        self.error = error

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        return ModelReply(value=None, raw="", error=self.error)


def test_a_model_that_cannot_be_reached_is_a_503_naming_the_step(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with reading_meals(tmp_path, AnsweringWith(RATE_LIMITED)) as client:
        response = client.post("/v1/log/read", json={"text": "2 rotis"})

    assert response.status_code == 503
    assert response.json() == {"detail": "the model could not be reached (step: read_meal)"}
    (failed,) = [e for e in log_entries(capfd.readouterr().err) if e["event"] == "read_failed"]
    assert failed["error_type"] == "ReadError"
    assert failed["request_id"] == response.headers["x-request-id"]


def test_a_reply_that_cannot_be_used_is_a_502_naming_the_step(tmp_path: Path) -> None:
    with reading_meals(tmp_path, AnsweringWith(SCHEMA_MISMATCH)) as client:
        response = client.post("/v1/log/read", json={"text": "2 rotis"})

    assert response.status_code == 502
    assert response.json() == {"detail": "the model's reply could not be used (step: read_meal)"}


class SearchDown(ReaderNutrition):
    """nutrition-mcp gone once the app has started: every search fails as unreachable."""

    async def search(
        self, query: str, kind: Literal["dish", "ingredient"] | None = None, limit: int = 5
    ) -> SearchResult:
        raise NutritionError("nutrition-mcp could not answer search_food", unreachable=True)


def test_nutrition_mcp_being_down_is_a_503_naming_the_step(tmp_path: Path) -> None:
    model = reader_model(reading(said("roti", 2, "roti", "2")))

    with reading_meals(tmp_path, model, SearchDown()) as client:
        response = client.post("/v1/log/read", json={"text": "2 rotis"})

    assert response.status_code == 503
    assert response.json() == {"detail": "nutrition-mcp could not be reached (step: nutrition)"}


class FailingOnce:
    """A model whose first call is rate limited and which answers every call after that."""

    def __init__(self, *replies: str) -> None:
        self.answering = reader_model(*replies)
        self.failed = False

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        if not self.failed:
            self.failed = True
            return ModelReply(value=None, raw="", error=RATE_LIMITED)
        return await self.answering.structured(
            task=task, system=system, user=user, schema=schema, timeout_s=timeout_s
        )


def test_a_failing_read_does_not_stop_the_next_one(tmp_path: Path) -> None:
    model = FailingOnce(reading(said("roti", 1, "roti", "a")))

    with reading_meals(tmp_path, model) as client:
        failed = client.post("/v1/log/read", json={"text": "a roti"})
        again = client.post("/v1/log/read", json={"text": "a roti"})

    assert failed.status_code == 503
    assert again.status_code == 200
    (item,) = ReadResult.model_validate(again.json()).items
    assert (item.status, item.food_id, item.grams) == ("matched", "chapati", 40)


# Typed meals stay out of the logs


class TypedTextEchoed(ReaderNutrition):
    """nutrition-mcp breaking its contract part way: a plain exception with the text in it."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise RuntimeError(f"stand-in echoed the request: {MARK}")


class ModelEchoing:
    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        raise RuntimeError(f"the provider echoed the prompt: {user}")


def test_no_typed_text_in_logs(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    text = f"2 rotis, a plate of pulao and 3 {MARK}"
    model = reader_model(
        reading(
            said("roti", 2, "roti", "2"),
            said("pulao", 1, "plate", "a plate"),
            # A food that is not in the data, and a unit that is refused in a message quoting it.
            said(f"food {MARK}", 3, f"unit {MARK}", f"3 {MARK}"),
            said("curd", 1, f"katori {MARK}", MARK),
        )
    )

    with reading_meals(tmp_path, model) as client:
        response = client.post("/v1/log/read", json={"text": text})

    assert response.status_code == 200
    # The words come back to the person who typed them, in the response and nowhere else.
    assert MARK in response.text
    assert records
    assert_no_marker(records, capfd.readouterr())


def test_no_typed_text_in_logs_when_the_text_is_rejected(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    with reading_meals(tmp_path, reader_model()) as client:
        too_long = client.post("/v1/log/read", json={"text": MARK * 40})
        wrong_type = client.post("/v1/log/read", json={"text": [MARK]})
        extra = client.post("/v1/log/read", json={"text": "2 rotis", "note": MARK})
        not_json = client.post(
            "/v1/log/read", content=MARK.encode(), headers={"content-type": "application/json"}
        )

    for response in (too_long, wrong_type, extra, not_json):
        assert response.status_code == 422
        assert MARK not in response.text
    assert_no_marker(records, capfd.readouterr())


def test_no_typed_text_in_logs_when_the_model_fails(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    with reading_meals(tmp_path, ModelEchoing()) as client:
        response = client.post("/v1/log/read", json={"text": f"2 rotis {MARK}"})

    assert response.status_code == 502
    assert MARK not in response.text
    assert_no_marker(records, capfd.readouterr())


def test_no_typed_text_in_logs_when_nutrition_mcp_breaks(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    model = reader_model(reading(said("roti", 2, "roti", "2")))

    with reading_meals(tmp_path, model, TypedTextEchoed()) as client:
        response = client.post("/v1/log/read", json={"text": f"2 rotis {MARK}"})

    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    assert_no_marker(records, capfd.readouterr())
