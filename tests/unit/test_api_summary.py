"""POST /v1/summary: a log, and the rules it was kept under, added up against the plan.

The app is the one the other API tests use, with the fixture plan and its foods. Every fixture
food has the same profile: 100 g is 200 kcal, 14 g protein, 5 g fibre and 100 mg calcium, so a
test can add a day up by hand. Monday 26 October 2026 is a training day (2650 kcal) and Tuesday
a rest day (2250 kcal).
"""

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
    plan_json,
    serving,
)
from nutrition_mcp.schemas import MealItemIn, MealResult
from week_helpers import SpyNutrition

from meal_planner.nutrition import NutritionError
from meal_planner.summary import WeekSummary

MON, TUE, WED = "2026-10-26", "2026-10-27", "2026-10-28"


def food(food_id: str = "oats", grams: float = 100) -> dict[str, Any]:
    return {"food_id": food_id, "amount": grams, "unit": "g"}


def entry(
    date: str, slot_id: str, status: str = "as_planned", *items: dict[str, Any]
) -> dict[str, Any]:
    foods = [] if status == "skipped" else list(items) or [food()]
    return {"date": date, "slot_id": slot_id, "status": status, "items": foods}


def body(*entries: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"rules": rules or plan_json(), "log": {"version": 1, "entries": list(entries)}}


def test_a_log_comes_back_as_a_week_summary(tmp_path: Path) -> None:
    log = body(
        entry(MON, "breakfast", "as_planned", food("oats", 100), food("paneer", 100)),
        entry(MON, "lunch", "skipped"),
        entry(TUE, "dinner", "swapped", food("white_rice", 200)),
    )

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=log)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    summary = WeekSummary.model_validate(response.json())
    assert (summary.planned_meals, summary.as_planned_share, summary.days_on_target) == (
        8,
        1 / 8,
        0,
    )
    monday, tuesday = summary.days
    assert (monday.day_type, monday.totals["kcal"], monday.totals["protein_g"]) == (
        "training",
        400,
        28,
    )
    assert monday.targets["kcal"] == 2650
    assert monday.failures[0].message == (
        "calories 400 kcal, day needs at least 2518 kcal (2650 kcal target, within 5%)"
    )
    assert monday.glycemic_load == {"breakfast": None}
    assert monday.gl_missing == {"breakfast": 2}
    assert (tuesday.day_type, tuesday.totals["kcal"]) == ("rest", 400)
    assert tuesday.targets["kcal"] == 2250
    assert tuesday.failures[0].message == (
        "calories 400 kcal, day needs at least 2138 kcal (2250 kcal target, within 5%)"
    )


def test_the_summary_is_held_to_the_tolerance_in_the_settings(tmp_path: Path) -> None:
    log = body(entry(MON, "dinner", "as_planned", food("oats", 1000)))  # 2000 kcal on a 2650 day

    with serving(tmp_path, day_kcal_tolerance=0.3) as client:
        response = client.post("/v1/summary", json=log)

    day = WeekSummary.model_validate(response.json()).days[0]
    assert "day_kcal" not in [f.check for f in day.failures]


def test_an_empty_log_is_an_empty_summary(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=body())

    assert response.json() == {
        "days": [],
        "as_planned_share": 0.0,
        "days_on_target": 0,
        "planned_meals": 0,
    }


# A log that does not fit is refused with the entry named


def test_unknown_food_id_422_names_entry(tmp_path: Path) -> None:
    log = body(
        entry(MON, "breakfast"),
        entry(TUE, "lunch", "as_planned", food(), food("ghost", 50)),
    )

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=log)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert set(problem) == {"loc", "msg"}
    assert problem["loc"] == ["body", "log", "entries", 1, "items", 1]
    assert problem["msg"].startswith("2026-10-27 lunch: item 2 (ghost): unknown food id 'ghost'")


def test_every_entry_nutrition_mcp_refuses_is_named(tmp_path: Path) -> None:
    log = body(
        entry(MON, "breakfast", "as_planned", food("ghost")),
        entry(MON, "lunch"),
        entry(MON, "dinner", "as_planned", food() | {"unit": "bowl"}),
    )

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=log)

    first, second = response.json()["detail"]
    assert first["loc"] == ["body", "log", "entries", 0, "items", 0]
    assert second["loc"] == ["body", "log", "entries", 2, "items", 0]
    assert second["msg"].startswith("2026-10-26 dinner: item 1 (oats): unknown unit 'bowl'")


def test_a_slot_the_plan_does_not_have_is_422_naming_it(tmp_path: Path) -> None:
    log = body(entry(MON, "breakfast"), entry(TUE, "brunch"))

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=log)

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "loc": ["body", "log", "entries", 1, "slot_id"],
            "msg": "2026-10-27 brunch: the plan has no slot 'brunch'; "
            "its slots are breakfast, lunch, snack, dinner",
        }
    ]


def test_log_over_7_days_rejected(tmp_path: Path) -> None:
    log = body(entry(MON, "lunch"), entry("2026-11-02", "lunch"))

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=log)

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "loc": ["body", "log", "entries"],
            "msg": "Value error, entries: the dates run from 2026-10-26 to 2026-11-02, "
            "which is 8 days; a log covers at most 7 consecutive days",
        }
    ]


def test_a_slot_listed_twice_on_a_date_is_422_naming_it(tmp_path: Path) -> None:
    log = body(entry(MON, "lunch"), entry(TUE, "lunch"), entry(MON, "lunch", "swapped"))

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=log)

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "loc": ["body", "log", "entries"],
            "msg": "Value error, entries: 2026-10-26 lunch is listed more than once",
        }
    ]


def test_a_skipped_meal_with_foods_is_422_naming_it(tmp_path: Path) -> None:
    log = body(entry(MON, "breakfast"), entry(MON, "lunch") | {"status": "skipped"})

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=log)

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "loc": ["body", "log", "entries", 1],
            "msg": "Value error, 2026-10-26 lunch: a skipped meal lists no foods",
        }
    ]


def test_a_meal_that_was_eaten_with_no_foods_is_422_naming_it(tmp_path: Path) -> None:
    log = body(entry(MON, "lunch", "skipped") | {"status": "swapped"})

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=log)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "log", "entries", 0]
    assert problem["msg"].startswith("Value error, 2026-10-26 lunch: a meal with status 'swapped'")


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        ({"surprise": 1}, ["body", "surprise"]),
        ({"log": {"version": 2, "entries": []}}, ["body", "log", "version"]),
        ({"log": {"version": 1}}, ["body", "log", "entries"]),
        ({"log": None}, ["body", "log"]),
        ({"rules": None}, ["body", "rules"]),
    ],
)
def test_a_body_that_does_not_fit_is_422_at_the_field(
    tmp_path: Path, change: dict[str, Any], loc: list[str]
) -> None:
    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=body() | change)

    assert response.status_code == 422
    assert [p["loc"] for p in response.json()["detail"]] == [loc]
    assert all(set(p) == {"loc", "msg"} for p in response.json()["detail"])


def test_rules_that_are_invalid_are_422_naming_the_field(tmp_path: Path) -> None:
    plan = plan_json()
    plan["day_types"][0]["extra"].append({"nutrient": "kcal", "kind": "max", "value": 3000})

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=body(entry(MON, "lunch"), rules=plan))

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "rules", "day_types", 0, "extra"]
    assert "'kcal' repeats a target the day type already has" in problem["msg"]


def test_a_body_that_is_not_json_is_422(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.post(
            "/v1/summary", content=b"{not json", headers={"content-type": "application/json"}
        )

    assert response.status_code == 422


# The limits every /v1 route has


def test_a_summary_body_over_the_limit_is_413(tmp_path: Path) -> None:
    raw_body = b'{"rules": {}, "log": {}}' + b" " * 65536

    with serving(tmp_path) as client:
        response = client.post(
            "/v1/summary", content=raw_body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 413


def test_the_summary_route_is_rate_limited(tmp_path: Path) -> None:
    with serving(tmp_path, rate_limit_per_minute=1) as client:
        first = client.post("/v1/summary", json=body())
        second = client.post("/v1/summary", json=body())

    assert (first.status_code, second.status_code) == (200, 429)
    assert 1 <= int(second.headers["retry-after"]) <= 60


# nutrition-mcp failing


class MealDown(SpyNutrition):
    """nutrition-mcp gone once the app has started: adding a meal up fails as unreachable."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError("nutrition-mcp could not answer compute_meal", unreachable=True)


class MealUnreadable(SpyNutrition):
    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError("nutrition-mcp's result for compute_meal could not be read")


def test_nutrition_mcp_being_down_is_a_503_naming_the_step(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with serving(tmp_path, nutrition=MealDown(*EXTRA_FOODS)) as client:
        response = client.post("/v1/summary", json=body(entry(MON, "lunch")))

    assert response.status_code == 503
    assert response.json() == {"detail": "nutrition-mcp could not be reached (step: nutrition)"}
    (failed,) = [e for e in log_entries(capfd.readouterr().err) if e["event"] == "summary_failed"]
    assert failed["error_type"] == "SummaryError"
    assert failed["request_id"] == response.headers["x-request-id"]


def test_an_answer_that_cannot_be_read_is_a_502_naming_the_step(tmp_path: Path) -> None:
    with serving(tmp_path, nutrition=MealUnreadable(*EXTRA_FOODS)) as client:
        response = client.post("/v1/summary", json=body(entry(MON, "lunch")))

    assert response.status_code == 502
    assert response.json() == {
        "detail": "nutrition-mcp sent an answer that could not be used (step: nutrition)"
    }


# Rules and logs stay out of the logs


def marked_body(*entries: dict[str, Any]) -> dict[str, Any]:
    return body(*entries, rules=marked_plan())


def test_no_rules_text_in_logs_when_a_summary_is_worked_out(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    log = marked_body(entry(MON, "breakfast"), entry(MON, "lunch", "skipped"))

    with serving(tmp_path) as client:
        response = client.post("/v1/summary", json=log)

    assert response.status_code == 200
    assert MARK not in response.text  # the summary holds no word of the plan
    assert_no_marker(records, capfd.readouterr())


def test_no_log_text_in_logs_when_the_log_is_rejected(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    ghost = food(f"ghost_{MARK}") | {"unit": f"unit_{MARK}"}
    rejected = [
        marked_body(entry(MON, "breakfast", "as_planned", ghost)),
        marked_body(entry(MON, f"slot_{MARK}")),
        marked_body(entry(MON, "lunch"), entry(f"{MON}", "lunch", f"status_{MARK}")),
        marked_body(entry(MON, "lunch", "skipped") | {"status": "swapped", "note": MARK}),
        marked_body(entry(MON, "lunch") | {"date": MARK}),
        marked_body(entry(MON, "lunch", "as_planned", food(f"x{MARK}" * 10))),
    ]

    with serving(tmp_path) as client:
        responses = [client.post("/v1/summary", json=log) for log in rejected]
        not_json = client.post(
            "/v1/summary", content=MARK.encode(), headers={"content-type": "application/json"}
        )

    assert [r.status_code for r in responses] == [422] * len(rejected)
    assert not_json.status_code == 422
    # The entry is named to the person who sent it, so the 422 may say its ids. The logs may not.
    assert MARK in responses[0].text
    assert MARK not in not_json.text
    assert_no_marker(records, capfd.readouterr())


def test_no_log_text_in_logs_when_nutrition_mcp_breaks(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    with serving(tmp_path, nutrition=ExplodingNutrition(*EXTRA_FOODS)) as client:
        response = client.post("/v1/summary", json=marked_body(entry(MON, "lunch")))

    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    assert_no_marker(records, capfd.readouterr())


class DownWithText(SpyNutrition):
    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError(f"nutrition-mcp could not answer {MARK}", unreachable=True)


def test_no_log_text_in_logs_when_nutrition_mcp_cannot_be_reached(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    with serving(tmp_path, nutrition=DownWithText(*EXTRA_FOODS)) as client:
        response = client.post("/v1/summary", json=marked_body(entry(MON, "lunch")))

    assert response.status_code == 503
    assert MARK not in response.text
    assert_no_marker(records, capfd.readouterr())
