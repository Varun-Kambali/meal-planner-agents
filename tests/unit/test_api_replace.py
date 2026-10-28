"""POST /v1/week/replace: one recipe written again inside the rest of the week."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from app_helpers import (
    EXTRA_FOODS,
    MARK,
    ExplodingNutrition,
    assert_no_marker,
    log_entries,
    marked_plan,
    parse_stream,
    plan_json,
    renamed,
    renamed_replies,
    serving,
    week_of,
)
from nutrition_mcp.schemas import MealItemIn, MealResult
from week_helpers import (
    SlotModel,
    SpyNutrition,
    raw,
    recipe_of,
)

from meal_planner.api import MAX_ATTEMPTS
from meal_planner.config import Settings
from meal_planner.nutrition import NutritionError
from meal_planner.week_events import WeekResult

# Writing one recipe again


def a_week(folder: Path) -> WeekResult:
    with serving(folder) as client:
        response = client.post("/v1/week", json=plan_json())
    return week_of(parse_stream(response.text))


def replace_body(week: WeekResult, day: str = "wed", slot_id: str = "lunch") -> dict[str, Any]:
    """What the browser sends: the plan, each recipe as the model wrote it, and which to redo."""
    return {
        "rules": plan_json(),
        "recipes": [
            {
                "day": r.spec.day,
                "slot_id": r.spec.slot.id,
                "draft": r.draft.model_dump(mode="json"),
                "attempts": r.attempts,
            }
            for r in week.recipes
        ],
        "day": day,
        "slot_id": slot_id,
    }


def lunch_again(title: str = "Rajma rice, the other way") -> SlotModel:
    recipe = recipe_of("lunch", "wed") | {"title": title}
    return SlotModel({"lunch": {"write_slot": [raw({"slot_id": "lunch", "recipes": [recipe]})]}})


def test_replace_streams_one_new_recipe_and_keeps_the_rest(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    model = lunch_again()

    with serving(tmp_path, model=model) as client:
        response = client.post("/v1/week/replace", json=replace_body(week))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_stream(response.text)
    names = [name for name, _ in events]
    assert names[0] == "slot_started"
    assert names[-1] == "week_done"
    assert names.count("slot_started") == 1
    assert events[0][1]["slot_id"] == "lunch"
    assert events[0][1]["days"] == ["wed"]
    new = week_of(events)
    assert new.calls == 1
    before = {(r.spec.day, r.spec.slot.id): r for r in week.recipes}
    after = {(r.spec.day, r.spec.slot.id): r for r in new.recipes}
    assert after[("wed", "lunch")].draft.title == "Rajma rice, the other way"
    assert all(after[k] == before[k] for k in before if k != ("wed", "lunch"))
    # The recipe that was replaced starts again at its first attempt.
    assert after[("wed", "lunch")].attempts == 1


def test_replace_keeps_the_attempts_of_the_recipes_it_leaves_alone(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"][0]["attempts"] = MAX_ATTEMPTS

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    new = week_of(parse_stream(response.text))
    assert new.recipes[0].attempts == MAX_ATTEMPTS


@pytest.mark.parametrize("attempts", [0, MAX_ATTEMPTS + 1, 1000])
def test_replace_refuses_attempts_outside_what_a_recipe_can_have(
    tmp_path: Path, attempts: int
) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"][2]["attempts"] = attempts

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "recipes", 2, "attempts"]


def test_the_attempts_bound_is_one_try_plus_the_default_repair_attempts() -> None:
    assert 1 + Settings().max_repair_attempts == MAX_ATTEMPTS


def test_replace_works_the_numbers_out_again_and_takes_none_from_the_client(
    tmp_path: Path,
) -> None:
    week = a_week(tmp_path)
    nutrition = SpyNutrition(*EXTRA_FOODS)
    body = replace_body(week)

    with serving(tmp_path, model=lunch_again(), nutrition=nutrition) as client:
        client.post("/v1/week/replace", json=body)

    # One meal per recipe sent, and one for the recipe that was written.
    assert len(nutrition.meals) == 28 + 1


@pytest.mark.parametrize("field", ["nutrition", "failures", "spec"])
def test_replace_refuses_a_recipe_that_brings_its_own_numbers(tmp_path: Path, field: str) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"][0][field] = {"kcal": 1}

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    assert any(p["loc"][:3] == ["body", "recipes", 0] for p in response.json()["detail"])


def test_replace_for_a_slot_the_plan_lacks_is_422_naming_it(tmp_path: Path) -> None:
    week = a_week(tmp_path)

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=replace_body(week, slot_id="brunch"))

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "slot_id"]
    assert "brunch" in problem["msg"]


def test_replace_with_a_recipe_for_a_slot_the_plan_lacks_is_422_naming_it(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"][3]["slot_id"] = "brunch"

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "recipes", 3, "slot_id"]
    assert "brunch" in problem["msg"]
    assert body["recipes"][3]["day"] in problem["msg"]


def test_replace_with_the_same_day_and_slot_twice_is_422_naming_it(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"].append(body["recipes"][5])

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "recipes", 28]
    assert body["recipes"][5]["slot_id"] in problem["msg"]
    assert body["recipes"][5]["day"] in problem["msg"]


def test_replace_with_a_draft_for_another_day_than_its_entry_is_422(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"][0]["draft"]["day"] = "sun"

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "recipes", 0, "draft", "day"]


def test_replace_with_an_unknown_food_in_the_rules_is_422(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["rules"]["food"]["exclude_foods"] = ["unobtainium"]

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "rules", "food", "exclude_foods"]


class DownNutrition(SpyNutrition):
    """nutrition-mcp when it cannot be asked at all: the error is marked unreachable."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError(
            "nutrition-mcp could not answer compute_meal: Connection closed", unreachable=True
        )


def test_replace_is_a_503_naming_the_step_when_the_numbers_cannot_be_worked_out(
    tmp_path: Path,
) -> None:
    week = a_week(tmp_path)
    model = lunch_again()

    with serving(tmp_path, model=model, nutrition=DownNutrition(*EXTRA_FOODS)) as client:
        response = client.post("/v1/week/replace", json=replace_body(week))

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert "recompute" in response.json()["detail"]
    assert model.calls == []


class RefusingNutrition(SpyNutrition):
    """nutrition-mcp refusing one food, as it does for a recipe it cannot work out."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        if any(item.food_id == "tofu" for item in items):
            raise NutritionError("item 1 (tofu): refused", "tofu")
        return await super().meal(items)


def test_replace_treats_a_recipe_nutrition_mcp_refuses_as_one_without_numbers(
    tmp_path: Path,
) -> None:
    week = a_week(tmp_path)
    with_tofu = {
        (r.spec.day, r.spec.slot.id)
        for r in week.recipes
        if any(i.id == "tofu" for i in r.draft.ingredients)
    }
    assert with_tofu

    with serving(
        tmp_path, model=lunch_again(), nutrition=RefusingNutrition(*EXTRA_FOODS)
    ) as client:
        response = client.post("/v1/week/replace", json=replace_body(week))

    assert response.status_code == 200
    new = week_of(parse_stream(response.text))
    without_numbers = {(r.spec.day, r.spec.slot.id) for r in new.recipes if r.nutrition is None}
    assert without_numbers == with_tofu - {("wed", "lunch")}


class RefusingWithoutAName(SpyNutrition):
    """nutrition-mcp refusing a recipe with a message that names no food."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        if any(item.food_id == "tofu" for item in items):
            raise NutritionError("compute_meal could not work out this meal")
        return await super().meal(items)


def test_replace_does_not_read_a_refusal_naming_no_food_as_nutrition_mcp_being_down(
    tmp_path: Path,
) -> None:
    week = a_week(tmp_path)

    with serving(
        tmp_path, model=lunch_again(), nutrition=RefusingWithoutAName(*EXTRA_FOODS)
    ) as client:
        response = client.post("/v1/week/replace", json=replace_body(week))

    assert response.status_code == 200
    new = week_of(parse_stream(response.text))
    assert any(r.nutrition is None for r in new.recipes)


def test_replace_has_its_own_larger_body_limit(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = json.dumps(replace_body(week)).encode()
    padded = body + b" " * (65536 - len(body) + 100)
    plan = json.dumps(plan_json()).encode()
    json_header = {"content-type": "application/json"}

    with serving(tmp_path, model=lunch_again()) as client:
        week_too_big = client.post("/v1/week", content=plan + b" " * 65536, headers=json_header)
        replace_ok = client.post("/v1/week/replace", content=padded, headers=json_header)
        replace_too_big = client.post(
            "/v1/week/replace", content=body + b" " * 131072, headers=json_header
        )

    assert len(padded) > 65536
    assert week_too_big.status_code == 413
    assert replace_ok.status_code == 200
    assert replace_too_big.status_code == 413
    assert "131072" in replace_too_big.json()["detail"]


def test_replace_cuts_off_a_chunked_body_at_its_limit(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = json.dumps(replace_body(week)).encode()
    parts = [body[i : i + 10000] for i in range(0, len(body), 10000)]
    parts += [b" " * 10000] * 14

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post(
            "/v1/week/replace", content=iter(parts), headers={"content-type": "application/json"}
        )

    assert sum(len(part) for part in parts) > 131072
    assert response.status_code == 413


def test_replace_ends_with_an_error_event_when_the_run_fails(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    week = a_week(tmp_path)
    capfd.readouterr()
    # The week is worked out again before the stream starts; the new recipe is not.
    nutrition = ExplodingNutrition(*EXTRA_FOODS, after=len(week.recipes))

    with serving(tmp_path, model=lunch_again(), nutrition=nutrition) as client:
        response = client.post("/v1/week/replace", json=replace_body(week))

    events = parse_stream(response.text)
    name, data = events[-1]
    assert name == "error"
    assert data["step"] == "replace"
    assert MARK not in response.text
    captured = capfd.readouterr()
    assert MARK not in captured.err + captured.out
    (failed,) = [e for e in log_entries(captured.err) if e["event"] == "run_failed"]
    assert failed["task"] == "replace"


# Recipes replaced stay out of the logs


def marked_week(folder: Path) -> WeekResult:
    """A week written for the marked plan, so a replacement can be sent for it."""
    with serving(folder, model=renamed_replies()) as client:
        response = client.post("/v1/week", json=marked_plan())
    return week_of(parse_stream(response.text))


def marked_replace_body(week: WeekResult) -> dict[str, Any]:
    return replace_body(week) | {"rules": marked_plan()}


def marked_lunch_again() -> SlotModel:
    recipe = recipe_of("lunch", "wed") | {"title": "Rajma rice, the other way"}
    reply = renamed(raw({"slot_id": "lunch", "recipes": [recipe]}))
    return SlotModel({"lunch": {"write_slot": [reply]}})


def test_no_rules_text_in_logs_when_a_recipe_is_replaced(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    week = marked_week(tmp_path)
    records.clear()
    capfd.readouterr()

    with serving(tmp_path, model=marked_lunch_again()) as client:
        response = client.post("/v1/week/replace", json=marked_replace_body(week))

    assert MARK in response.text
    assert parse_stream(response.text)[-1][0] == "week_done"
    captured = capfd.readouterr()
    assert [e["task"] for e in log_entries(captured.err) if e["event"] == "run_done"] == ["replace"]
    assert records
    assert_no_marker(records, captured)


def test_no_rules_text_in_logs_when_a_replacement_is_rejected(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    week = marked_week(tmp_path)
    body = marked_replace_body(week)
    body["rules"]["slots"][1]["max_active_min"] = f"soon {MARK}"
    body["recipes"][0]["attempts"] = f"twice {MARK}"
    records.clear()
    capfd.readouterr()

    with serving(tmp_path) as client:
        bad_values = client.post("/v1/week/replace", json=body)
        not_json = client.post(
            "/v1/week/replace", content=MARK.encode(), headers={"content-type": "application/json"}
        )

    assert (bad_values.status_code, not_json.status_code) == (422, 422)
    assert MARK not in bad_values.text + not_json.text
    assert_no_marker(records, capfd.readouterr())


def test_no_rules_text_in_logs_when_a_replacement_run_fails(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    week = marked_week(tmp_path)
    nutrition = ExplodingNutrition(*EXTRA_FOODS, after=len(week.recipes))
    records.clear()
    capfd.readouterr()

    with serving(tmp_path, model=marked_lunch_again(), nutrition=nutrition) as client:
        response = client.post("/v1/week/replace", json=marked_replace_body(week))

    assert parse_stream(response.text)[-1][0] == "error"
    captured = capfd.readouterr()
    assert [e["task"] for e in log_entries(captured.err) if e["event"] == "run_failed"] == [
        "replace"
    ]
    assert_no_marker(records, captured)
