"""The synthetic meal log that the README summarises, so that its command line keeps running.

`tests/fixtures/logs/office_weekday_log.json` is a made-up log for the fixture plan. Nothing in it
is a real person's meals. These tests hold it to the plan and to the shipped catalogue, and
summarise it with a fake nutrition-mcp. A live test (tests/integration/test_summary_live.py) runs
it against the real server.
"""

import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fakes import FakeNutrition
from nutrition_mcp.schemas import MealItemIn, MealResult
from week_helpers import FIXTURES, PROFILE, item, plan_rules

from meal_planner.catalogue import Catalogue
from meal_planner.config import Settings
from meal_planner.summary import MealLog, log_problems, summarise

pytestmark = pytest.mark.anyio

LOG = FIXTURES / "logs" / "office_weekday_log.json"
SHIPPED_CATALOGUE = Path(__file__).resolve().parents[2] / "data" / "catalogue.yaml"

# Units nutrition-mcp itself has for these foods, besides grams. The log uses them as written, and
# the fake needs them to accept the log. Any other unit in the log is one of the catalogue's
# measures, which turn into grams before nutrition-mcp is asked.
NUTRITION_UNITS = {
    "whole_milk": {"ml": 1.03},
    "olive_oil": {"tbsp": 13.5},
}


def shipped_rows() -> dict[str, dict[str, Any]]:
    rows = yaml.safe_load(SHIPPED_CATALOGUE.read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


def the_log() -> MealLog:
    return MealLog.model_validate_json(LOG.read_text(encoding="utf-8"))


def catalogue_of(log: MealLog) -> Catalogue:
    """The shipped catalogue's rows for the foods in the log, with the fake's units."""
    rows = shipped_rows()
    foods = {i.food_id for entry in log.entries for i in entry.items}
    return Catalogue(
        {
            food: item(food).model_copy(
                update={
                    "units": ["g", *NUTRITION_UNITS.get(food, {})],
                    "measures": rows[food].get("measures", {}),
                }
            )
            for food in foods
        }
    )


class AskedNutrition(FakeNutrition):
    """FakeNutrition that remembers the items of every meal it was asked about."""

    def __init__(self, food_ids: set[str]) -> None:
        super().__init__({food: PROFILE for food in food_ids}, units=NUTRITION_UNITS)
        self.asked: list[MealItemIn] = []

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        self.asked += items
        return await super().meal(items)


def test_the_log_is_one_for_the_plan_and_uses_every_status() -> None:
    log = the_log()

    assert log_problems(plan_rules(), log) == []
    assert {entry.status for entry in log.entries} == {
        "as_planned",
        "different_amount",
        "swapped",
        "skipped",
    }


def test_it_names_only_foods_of_the_shipped_catalogue_in_units_the_reader_returns() -> None:
    rows = shipped_rows()
    for entry in the_log().entries:
        for logged in entry.items:
            assert logged.food_id in rows, logged.food_id
            allowed = {
                "g",
                *NUTRITION_UNITS.get(logged.food_id, {}),
                *rows[logged.food_id].get("measures", {}),
            }
            assert logged.unit in allowed, f"{logged.food_id}: {logged.unit}"


async def test_it_summarises_with_the_fake_and_takes_the_catalogues_measures() -> None:
    log = the_log()
    nutrition = AskedNutrition({i.food_id for entry in log.entries for i in entry.items})

    summary = await summarise(
        plan_rules(),
        log,
        nutrition=nutrition,
        catalogue=catalogue_of(log),
        settings=Settings(),
    )

    assert [(day.date, day.day_type) for day in summary.days] == [
        (datetime.date(2026, 10, 26), "training"),
        (datetime.date(2026, 10, 27), "rest"),
        (datetime.date(2026, 10, 28), "training"),
    ]
    assert (summary.planned_meals, summary.as_planned_share) == (12, 0.5)
    # A skipped meal has no foods to add up, so it has no glycemic load either.
    assert set(summary.days[1].glycemic_load) == {"breakfast", "snack", "dinner"}
    # A piece and a cup go to nutrition-mcp as grams; its own units go as written.
    assert MealItemIn(food_id="banana", amount=118, unit="g") in nutrition.asked
    assert MealItemIn(food_id="broccoli", amount=91, unit="g") in nutrition.asked
    assert MealItemIn(food_id="whole_milk", amount=250, unit="ml") in nutrition.asked
    assert {i.unit for i in nutrition.asked} <= {"g", "ml", "tbsp"}
