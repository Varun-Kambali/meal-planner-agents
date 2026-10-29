"""POST /v1/log/compute: the numbers of foods as they are edited, before they are saved.

The app is the one the reader tests use, so a unit the reader reads here is read the way the
summary reads it. No test scripts a model, because the route calls none.
"""

import logging
from pathlib import Path
from typing import Any

import pytest
from app_helpers import MARK, assert_no_marker, log_entries, plan_json, serving_foods
from nutrition_mcp.schemas import MealItemIn, MealResult
from reader_helpers import ReaderNutrition

from meal_planner.llm import FakeModel
from meal_planner.nutrition import NutritionError
from meal_planner.summary import MAX_FOODS, WeekSummary

COMPUTE = "/v1/log/compute"

ROTIS = {"food_id": "chapati", "amount": 2, "unit": "roti"}
EGGS = {"food_id": "egg", "amount": 2, "unit": "piece"}


def meals_asked(nutrition: ReaderNutrition) -> list[str]:
    """What nutrition-mcp was asked to add up since the app started."""
    return [what for how, what in nutrition.asked if how == "meal"]


def test_foods_come_back_with_each_ones_numbers_and_the_meals_total(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.post(COMPUTE, json={"items": [ROTIS, EGGS]})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    meal = MealResult.model_validate(response.json())
    roti, egg = meal.items
    assert (roti.food.id, roti.grams, roti.nutrients.kcal) == ("chapati", 80, 240)
    # nutrition-mcp has the egg in grams only. The catalogue says a piece is 50 g.
    assert (egg.food.id, egg.grams, egg.nutrients.kcal) == ("egg", 100, 143)
    assert (meal.total.kcal, meal.total.protein_g) == (383, 19.7)


def test_a_single_food_is_a_meal_of_one(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.post(COMPUTE, json={"items": [ROTIS]})

    meal = MealResult.model_validate(response.json())
    assert [item.food.id for item in meal.items] == ["chapati"]
    assert meal.total.kcal == meal.items[0].nutrients.kcal


def test_kilograms_and_litres_are_sent_in_grams_and_millilitres(tmp_path: Path) -> None:
    nutrition = ReaderNutrition()
    chicken = {"food_id": "chicken_breast", "amount": 0.5, "unit": "kg"}
    milk = {"food_id": "whole_milk", "amount": 0.25, "unit": "litre"}

    with serving_foods(tmp_path, nutrition) as client:
        response = client.post(COMPUTE, json={"items": [chicken, milk]})

    assert response.status_code == 200
    assert meals_asked(nutrition) == ["chicken_breast 500 g, whole_milk 250 ml"]
    assert MealResult.model_validate(response.json()).total.kcal == pytest.approx(825 + 154.5)


def test_a_food_has_the_numbers_here_that_it_has_in_the_day_view(tmp_path: Path) -> None:
    """Both read the units through the same function, so the edit screen and the day agree."""
    items = [ROTIS, EGGS, {"food_id": "whole_milk", "amount": 0.25, "unit": "litre"}]
    log = {
        "version": 1,
        "entries": [
            {
                "date": "2026-10-26",
                "slot_id": "breakfast",
                "status": "swapped",
                "items": items,
            }
        ],
    }

    with serving_foods(tmp_path) as client:
        computed = MealResult.model_validate(client.post(COMPUTE, json={"items": items}).json())
        summary = client.post("/v1/summary", json={"rules": plan_json(), "log": log})

    (day,) = WeekSummary.model_validate(summary.json()).days
    assert computed.total.kcal == day.totals["kcal"]
    assert computed.total.protein_g == day.totals["protein_g"]
    assert computed.glycemic_load == day.glycemic_load["breakfast"]


def test_the_same_foods_give_the_same_numbers_each_time(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        first = client.post(COMPUTE, json={"items": [ROTIS, EGGS]})
        second = client.post(COMPUTE, json={"items": [ROTIS, EGGS]})

    assert first.json() == second.json()


def test_a_meal_of_the_most_foods_is_worked_out(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.post(COMPUTE, json={"items": [ROTIS] * MAX_FOODS})

    assert response.status_code == 200
    assert len(response.json()["items"]) == MAX_FOODS


# Foods nutrition-mcp refuses are named by their place in the list


def test_an_unknown_food_is_422_naming_the_item(tmp_path: Path) -> None:
    ghost = {"food_id": "ghost", "amount": 50, "unit": "g"}

    with serving_foods(tmp_path) as client:
        response = client.post(COMPUTE, json={"items": [ROTIS, ghost]})

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert set(problem) == {"loc", "msg"}
    assert problem["loc"] == ["body", "items", 1]
    assert problem["msg"].startswith("item 2 (ghost): unknown food id 'ghost'")


@pytest.mark.parametrize(
    ("food", "valid"),
    [
        # In the catalogue with a measure nutrition-mcp lacks: its units, then the catalogue's.
        ({"food_id": "egg", "amount": 1, "unit": "cup"}, "g, piece"),
        # In the catalogue with no measure.
        ({"food_id": "pav", "amount": 1, "unit": "cup"}, "g"),
        # Not in the catalogue: nutrition-mcp's own units.
        ({"food_id": "chapati", "amount": 1, "unit": "cup"}, "g, roti, piece, serving"),
    ],
)
def test_a_unit_the_food_does_not_take_is_422_naming_the_item_and_the_units_that_work(
    tmp_path: Path, food: dict[str, Any], valid: str
) -> None:
    with serving_foods(tmp_path) as client:
        response = client.post(COMPUTE, json={"items": [ROTIS, food]})

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "items", 1]
    assert problem["msg"].endswith(f"valid units: {valid}")


def test_the_item_named_is_the_one_refused_when_a_food_is_listed_twice(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.post(COMPUTE, json={"items": [EGGS, EGGS | {"unit": "cup"}]})

    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "items", 1]
    assert problem["msg"].startswith("item 2 (egg)")


def test_an_amount_over_the_limit_is_422_naming_the_item(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.post(COMPUTE, json={"items": [ROTIS | {"amount": 1000}]})

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "items", 0]
    assert "over the limit of 5000 g" in problem["msg"]


# A body that does not fit is 422 at the field


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        ({}, ["body", "items"]),
        ({"items": []}, ["body", "items"]),
        ({"items": [ROTIS] * (MAX_FOODS + 1)}, ["body", "items"]),
        ({"items": ROTIS}, ["body", "items"]),
        ({"items": [ROTIS | {"amount": 0}]}, ["body", "items", 0, "amount"]),
        ({"items": [ROTIS | {"amount": -2}]}, ["body", "items", 0, "amount"]),
        ({"items": [ROTIS | {"amount": "2 rotis"}]}, ["body", "items", 0, "amount"]),
        ({"items": [{"food_id": "chapati", "amount": 2}]}, ["body", "items", 0, "unit"]),
        ({"items": [ROTIS | {"unit": ""}]}, ["body", "items", 0, "unit"]),
        ({"items": [ROTIS | {"food_id": "x" * 65}]}, ["body", "items", 0, "food_id"]),
        ({"items": [ROTIS | {"grams": 80}]}, ["body", "items", 0, "grams"]),
        ({"items": [ROTIS], "rules": {}}, ["body", "rules"]),
    ],
)
def test_a_body_that_does_not_fit_is_422_at_the_field_without_asking_nutrition_mcp(
    tmp_path: Path, body: dict[str, Any], loc: list[str | int]
) -> None:
    nutrition = ReaderNutrition()

    with serving_foods(tmp_path, nutrition) as client:
        response = client.post(COMPUTE, json=body)

    assert response.status_code == 422
    assert [p["loc"] for p in response.json()["detail"]] == [loc]
    assert all(set(p) == {"loc", "msg"} for p in response.json()["detail"])
    assert meals_asked(nutrition) == []


def test_a_body_that_is_not_json_is_422(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.post(
            COMPUTE, content=b"{not json", headers={"content-type": "application/json"}
        )

    assert response.status_code == 422


# The limits every /v1 route has


def test_a_compute_body_over_the_limit_is_413(tmp_path: Path) -> None:
    raw_body = b'{"items": []}' + b" " * 65536

    with serving_foods(tmp_path) as client:
        response = client.post(
            COMPUTE, content=raw_body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 413


def test_the_compute_route_is_rate_limited(tmp_path: Path) -> None:
    with serving_foods(tmp_path, rate_limit_per_minute=1) as client:
        first = client.post(COMPUTE, json={"items": [ROTIS]})
        second = client.post(COMPUTE, json={"items": [ROTIS]})

    assert (first.status_code, second.status_code) == (200, 429)
    assert 1 <= int(second.headers["retry-after"]) <= 60


def test_the_compute_calls_no_model(tmp_path: Path) -> None:
    model = FakeModel({})

    with serving_foods(tmp_path, model=model) as client:
        client.post(COMPUTE, json={"items": [ROTIS, EGGS]})
        client.post(COMPUTE, json={"items": [{"food_id": "ghost", "amount": 1, "unit": "g"}]})

    assert model.calls == []


# nutrition-mcp failing


class MealDown(ReaderNutrition):
    """nutrition-mcp gone once the app has started: adding a meal up fails as unreachable."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError("nutrition-mcp could not answer compute_meal", unreachable=True)


class MealUnreadable(ReaderNutrition):
    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError("nutrition-mcp's result for compute_meal could not be read")


def test_nutrition_mcp_being_down_is_a_503_naming_the_step(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with serving_foods(tmp_path, MealDown()) as client:
        response = client.post(COMPUTE, json={"items": [ROTIS]})

    assert response.status_code == 503
    assert response.json() == {"detail": "nutrition-mcp could not be reached (step: nutrition)"}
    (failed,) = [e for e in log_entries(capfd.readouterr().err) if e["event"] == "compute_failed"]
    assert (failed["task"], failed["error_type"]) == ("compute", "SummaryError")
    assert failed["request_id"] == response.headers["x-request-id"]


def test_an_answer_that_cannot_be_read_is_a_502_naming_the_step(tmp_path: Path) -> None:
    with serving_foods(tmp_path, MealUnreadable()) as client:
        response = client.post(COMPUTE, json={"items": [ROTIS]})

    assert response.status_code == 502
    assert response.json() == {
        "detail": "nutrition-mcp sent an answer that could not be used (step: nutrition)"
    }


# Typed text stays out of the logs


class Exploding(ReaderNutrition):
    """nutrition-mcp breaking its contract: a plain exception with the request's text in it."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise RuntimeError(f"stand-in echoed the request: {items[-1].unit}")


class DownWithText(ReaderNutrition):
    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError(f"nutrition-mcp could not answer {MARK}", unreachable=True)


@pytest.mark.parametrize(
    ("nutrition", "status"), [(ReaderNutrition, 422), (DownWithText, 503), (Exploding, 500)]
)
def test_no_food_text_in_logs(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
    nutrition: type[ReaderNutrition],
    status: int,
) -> None:
    # The id and the unit are the sender's own, so a 422 may say them; the logs may not.
    marked = {"food_id": "chapati", "amount": 2, "unit": f"unit_{MARK}"}

    with serving_foods(tmp_path, nutrition()) as client:
        response = client.post(COMPUTE, json={"items": [ROTIS, marked]})

    assert response.status_code == status
    assert (MARK in response.text) == (status == 422)
    assert records
    assert_no_marker(records, capfd.readouterr())


def test_no_food_text_in_logs_when_the_body_is_rejected(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    bodies = [
        {"items": [ROTIS | {"unit": MARK * 40}]},
        {"items": [ROTIS | {"amount": MARK}]},
        {"items": [], "note": MARK},
    ]

    with serving_foods(tmp_path) as client:
        responses = [client.post(COMPUTE, json=body) for body in bodies]
        # A field name that is not allowed comes back at its place in the 422, as the sender
        # wrote it. The values never do.
        extra_field = client.post(COMPUTE, json={"items": [ROTIS | {MARK: 1}]})
        not_json = client.post(
            COMPUTE, content=MARK.encode(), headers={"content-type": "application/json"}
        )

    assert [r.status_code for r in (*responses, extra_field)] == [422] * (len(bodies) + 1)
    assert MARK not in not_json.text + "".join(r.text for r in responses)
    assert_no_marker(records, capfd.readouterr())
