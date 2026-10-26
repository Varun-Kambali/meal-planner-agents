"""Day and week summaries, worked out from a log with a fake nutrition-mcp.

The foods are made up and round, so a test can add a day up by hand. Dates are the week of
Monday 26 October 2026; the fixture plan makes Monday, Wednesday and Friday training days and the
rest rest days.
"""

import datetime
import json
import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fakes import FakeNutrition
from nutrition_mcp.nutrients import NUTRIENT_KEYS
from nutrition_mcp.schemas import MealItemIn, MealResult
from pydantic import ValidationError
from reader_helpers import ReaderNutrition, reader_catalogue
from week_helpers import SLOTS, SpyNutrition, catalogue, item, model_for, plan_rules

from meal_planner.catalogue import Catalogue
from meal_planner.config import Settings
from meal_planner.nutrition import NutritionError
from meal_planner.reader import MAX_ITEMS
from meal_planner.rules import PlanRules
from meal_planner.summary import (
    MAX_FOODS,
    Export,
    LogRejected,
    MealLog,
    MealStatus,
    SummaryError,
    WeekSummary,
    log_problems,
    summarise,
)
from meal_planner.week import run_week
from meal_planner.week_events import WeekDone, WeekResult

pytestmark = pytest.mark.anyio

MON = datetime.date(2026, 10, 26)
WEEK = [MON + datetime.timedelta(days=n) for n in range(7)]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in [n for n in os.environ if n.startswith("MEAL_PLANNER_")]:
        monkeypatch.delenv(name)
    yield


# Foods per 100 g. A rest day wants 2250 kcal (2137.5 to 2362.5), 130 g protein, at most 25 g
# saturated fat, 30 g fibre and 15 mg iron, and 1000 g of `perfect` is exactly that.
FOODS: dict[str, dict[str, float]] = {
    "rice": {
        "kcal": 130,
        "protein_g": 2.7,
        "fat_g": 0.3,
        "sat_fat_g": 0.1,
        "carbs_available_g": 28,
        "fibre_g": 0.4,
    },
    "dal": {
        "kcal": 120,
        "protein_g": 7,
        "fat_g": 3,
        "sat_fat_g": 0.5,
        "carbs_available_g": 15,
        "fibre_g": 5,
    },
    "ghee": {"kcal": 900, "fat_g": 100, "sat_fat_g": 60, "carbs_available_g": 0},
    "mystery_flour": {"kcal": 300, "protein_g": 10, "carbs_available_g": 60},
    "perfect": {
        "kcal": 225,
        "protein_g": 13,
        "fat_g": 7.5,
        "sat_fat_g": 2,
        "carbs_available_g": 27,
        "fibre_g": 3.5,
        "iron_mg": 1.6,
    },
}
# Rice and dal have a glycemic index; ghee and the rest have none.
GI = {"rice": 73.0, "dal": 30.0}
NAMES = {"mystery_flour": "Mystery flour"}


class Spy(FakeNutrition):
    """The foods above, remembering every meal it was asked to add up."""

    def __init__(self) -> None:
        super().__init__(FOODS, gi=GI, units={"rice": {"katori": 150.0}}, names=NAMES)
        self.meals: list[list[tuple[str, float, str]]] = []

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        self.meals.append([(i.food_id, i.amount, i.unit) for i in items])
        return await super().meal(items)


def rice(grams: float = 100) -> dict[str, Any]:
    return {"food_id": "rice", "amount": grams, "unit": "g"}


def entry(
    day: datetime.date,
    slot_id: str,
    status: MealStatus = "as_planned",
    *items: dict[str, Any],
) -> dict[str, Any]:
    """A log entry as the browser sends it. A meal that was eaten is a plate of rice unless
    `items` say otherwise, and a skipped one has nothing."""
    foods = [] if status == "skipped" else list(items) or [rice()]
    return {"date": day.isoformat(), "slot_id": slot_id, "status": status, "items": foods}


def log_of(*entries: dict[str, Any]) -> MealLog:
    return MealLog.model_validate({"version": 1, "entries": list(entries)})


async def summary_of(
    log: MealLog,
    *,
    rules: PlanRules | None = None,
    nutrition: FakeNutrition | None = None,
    catalogue: Catalogue | None = None,
    **settings: Any,
) -> WeekSummary:
    return await summarise(
        rules or plan_rules(),
        log,
        nutrition=nutrition or Spy(),
        catalogue=catalogue or Catalogue({}),
        settings=Settings(**settings),
    )


def full_week(status_of: Callable[[int], MealStatus]) -> MealLog:
    """Every slot of every day of the week, 28 entries, with the status `status_of` gives the
    entry at that position."""
    entries = [
        entry(day, slot, status_of(position))
        for position, (day, slot) in enumerate((d, s) for d in WEEK for s in SLOTS)
    ]
    return log_of(*entries)


# Share of planned meals eaten as planned


async def test_as_planned_share() -> None:
    statuses: list[MealStatus] = (
        ["as_planned"] * 21 + ["different_amount"] * 3 + ["swapped"] * 2 + ["skipped"] * 2
    )

    summary = await summary_of(full_week(lambda position: statuses[position]))

    assert summary.planned_meals == 28
    assert summary.as_planned_share == 0.75
    assert [d.date for d in summary.days] == WEEK


async def test_a_slot_with_no_entry_is_not_as_planned() -> None:
    log = log_of(
        entry(MON, "breakfast"),
        entry(MON, "lunch"),
        entry(WEEK[1], "dinner"),
        entry(WEEK[1], "snack", "swapped"),
    )

    summary = await summary_of(log)

    # Two dates of four slots each, and three of the eight were eaten as planned.
    assert summary.planned_meals == 8
    assert summary.as_planned_share == 3 / 8


async def test_a_log_with_no_entries_plans_nothing() -> None:
    summary = await summary_of(log_of())

    assert summary == WeekSummary(days=[], as_planned_share=0.0, days_on_target=0, planned_meals=0)


# Totals


async def test_a_day_adds_up_its_meals() -> None:
    log = log_of(
        entry(
            MON,
            "breakfast",
            "as_planned",
            rice(100),
            {"food_id": "dal", "amount": 100, "unit": "g"},
        ),
        entry(MON, "lunch", "different_amount", rice(200)),
    )

    (day,) = (await summary_of(log)).days

    assert day.date == MON
    # 100 g rice, 100 g dal and 200 g rice.
    assert day.totals["kcal"] == 510
    assert day.totals["protein_g"] == 15.1
    assert day.totals["fibre_g"] == 6.2
    assert day.totals["iron_mg"] is None
    assert set(day.totals) == set(NUTRIENT_KEYS)


async def test_every_meal_is_worked_out_again_by_nutrition_mcp() -> None:
    nutrition = Spy()
    log = log_of(
        entry(
            MON, "breakfast", "as_planned", rice(100), {"food_id": "dal", "amount": 1, "unit": "g"}
        ),
        entry(MON, "lunch", "swapped", {"food_id": "rice", "amount": 2, "unit": "katori"}),
    )

    await summary_of(log, nutrition=nutrition)

    assert nutrition.meals == [
        [("rice", 100, "g"), ("dal", 1, "g")],
        [("rice", 2, "katori")],
    ]


async def test_a_unit_the_reader_reads_through_a_catalogue_measure_is_summarised_in_grams() -> None:
    # The reader returns "2 eggs" as egg, 2, piece: nutrition-mcp has the egg in grams only, and
    # the catalogue says a piece is 50 g. The summary has to take what the reader returns.
    nutrition = ReaderNutrition()
    eggs = {"food_id": "egg", "amount": 2, "unit": "piece"}
    rotis = {"food_id": "chapati", "amount": 2, "unit": "Rotis"}
    log = log_of(entry(MON, "breakfast", "as_planned", eggs, rotis))

    (day,) = (await summary_of(log, nutrition=nutrition, catalogue=reader_catalogue())).days

    # The pieces go to nutrition-mcp as 100 g. Chapati is not in the catalogue, so its unit goes
    # as it was logged and nutrition-mcp reads it.
    assert nutrition.asked == [("meal", "egg 100 g, chapati 2 Rotis")]
    assert day.totals["kcal"] == 143 + 240


async def test_kilograms_and_litres_are_sent_in_grams_and_millilitres() -> None:
    nutrition = ReaderNutrition()
    chicken = {"food_id": "chicken_breast", "amount": 0.5, "unit": "kg"}
    milk = {"food_id": "whole_milk", "amount": 0.25, "unit": "litre"}
    log = log_of(entry(MON, "lunch", "as_planned", chicken, milk))

    (day,) = (await summary_of(log, nutrition=nutrition, catalogue=reader_catalogue())).days

    assert nutrition.asked == [("meal", "chicken_breast 500 g, whole_milk 250 ml")]
    assert day.totals["kcal"] == pytest.approx(825 + 154.5)


async def test_a_unit_nobody_has_is_refused_in_nutrition_mcps_words() -> None:
    log = log_of(
        entry(MON, "breakfast", "as_planned", {"food_id": "egg", "amount": 1, "unit": "cup"})
    )

    with pytest.raises(LogRejected) as err:
        await summary_of(log, nutrition=ReaderNutrition(), catalogue=reader_catalogue())

    (problem,) = err.value.problems
    assert problem["loc"] == ["entries", 0, "items", 0]
    assert problem["msg"] == (
        "2026-10-26 breakfast: item 1 (egg): unknown unit 'cup' for egg; valid units: g"
    )


async def test_skipped_meal_counts_zero_items_not_missing() -> None:
    nutrition = Spy()
    breakfast = entry(MON, "breakfast", "as_planned", rice(100))
    with_skip = log_of(breakfast, entry(MON, "lunch", "skipped"))

    (day,) = (await summary_of(with_skip, nutrition=nutrition)).days
    (alone,) = (await summary_of(log_of(breakfast))).days

    # The skipped lunch is not sent, and the day reads as if it had not been logged: its numbers
    # are not made partial, and no failure says anything is not known because of it.
    assert nutrition.meals == [[("rice", 100, "g")]]
    assert day.totals["kcal"] == 130
    assert day.totals["fibre_g"] == 0.4
    assert (day.totals, day.failures, day.soft) == (alone.totals, alone.failures, alone.soft)
    assert not any("lunch" in f.message for f in day.failures)
    assert day.glycemic_load == {"breakfast": 20.4}
    assert day.gl_missing == {"breakfast": 0}


async def test_a_day_of_skipped_meals_ate_nothing() -> None:
    log = log_of(entry(MON, "breakfast", "skipped"), entry(MON, "dinner", "skipped"))

    (day,) = (await summary_of(log)).days

    assert day.totals["kcal"] == 0
    assert day.totals["protein_g"] == 0
    assert day.totals["iron_mg"] == 0
    assert day.glycemic_load == {}
    assert day.gl_missing == {}
    assert day.failures[0].message.startswith("calories 0 kcal, day needs at least")
    assert day.soft == {"carbs_available_g": -1.0, "fat_g": -1.0}


async def test_a_nutrient_no_food_has_a_value_for_is_unknown_not_zero() -> None:
    log = log_of(
        entry(
            MON, "breakfast", "as_planned", {"food_id": "mystery_flour", "amount": 100, "unit": "g"}
        )
    )

    (day,) = (await summary_of(log)).days

    assert day.totals["fibre_g"] is None
    assert day.totals["kcal"] == 300
    fibre = [f for f in day.failures if f.check == "day_fibre_g"]
    assert [f.message for f in fibre] == [
        "fibre not known (no value for mystery flour), day needs at least 35 g"
    ]
    assert (
        "fat_g" not in day.soft
    )  # no value for fat: a deviation of part of a total would be wrong


async def test_failures_name_foods_as_a_person_reads_them() -> None:
    log = log_of(
        entry(
            MON, "breakfast", "as_planned", {"food_id": "mystery_flour", "amount": 100, "unit": "g"}
        ),
        entry(MON, "lunch", "as_planned", rice(100)),
    )

    (day,) = (await summary_of(log)).days

    fibre = [f.message for f in day.failures if f.check == "day_fibre_g"]
    assert fibre == [
        "fibre at least 0.4 g, part not known (no value for mystery flour), day needs at least 35 g"
    ]
    assert "mystery_flour" not in " ".join(f.message for f in day.failures)


async def test_the_day_is_held_to_the_tolerance_in_the_settings() -> None:
    log = log_of(entry(MON, "breakfast", "as_planned", rice(1000)))  # 1300 kcal on a 2650 day

    default = (await summary_of(log)).days[0]
    wide = (await summary_of(log, day_kcal_tolerance=0.6)).days[0]

    assert "day_kcal" in [f.check for f in default.failures]
    assert "day_kcal" not in [f.check for f in wide.failures]


async def test_days_are_in_date_order_whatever_the_order_of_the_log() -> None:
    log = log_of(
        entry(WEEK[2], "lunch"),
        entry(MON, "dinner"),
        entry(WEEK[2], "breakfast"),
        entry(MON, "snack"),
    )

    summary = await summary_of(log)

    assert [d.date for d in summary.days] == [MON, WEEK[2]]
    assert list(summary.days[0].glycemic_load) == ["snack", "dinner"]  # the plan's order of slots
    assert list(summary.days[1].glycemic_load) == ["breakfast", "lunch"]


async def test_days_on_target_counts_the_days_that_meet_every_target() -> None:
    log = log_of(
        entry(WEEK[1], "dinner", "as_planned", {"food_id": "perfect", "amount": 1000, "unit": "g"}),
        entry(MON, "breakfast", "as_planned", rice(100)),
    )

    summary = await summary_of(log)

    tuesday, monday = summary.days[1], summary.days[0]
    assert tuesday.date == WEEK[1]
    assert tuesday.failures == []
    assert tuesday.soft == {"carbs_available_g": 0.0, "fat_g": 0.0}
    assert monday.failures != []
    assert summary.days_on_target == 1


# Glycemic load


async def test_gl_unknown_when_any_item_lacks_gi() -> None:
    log = log_of(
        entry(
            MON,
            "breakfast",
            "as_planned",
            rice(100),
            {"food_id": "ghee", "amount": 10, "unit": "g"},
        ),
        entry(
            MON, "lunch", "as_planned", rice(100), {"food_id": "dal", "amount": 100, "unit": "g"}
        ),
        entry(MON, "snack", "as_planned", {"food_id": "ghee", "amount": 5, "unit": "g"}),
    )

    (day,) = (await summary_of(log)).days

    # Rice has a load of 20.4 but the ghee beside it has none, so the meal's load is not shown
    # as 20.4. Lunch has a load for every item.
    assert day.glycemic_load == {"breakfast": None, "lunch": 24.9, "snack": None}
    assert day.gl_missing == {"breakfast": 1, "lunch": 0, "snack": 1}


# What the log may hold


def test_log_over_7_days_rejected() -> None:
    entries = [entry(MON, "lunch"), entry(MON + datetime.timedelta(days=7), "lunch")]

    with pytest.raises(ValidationError) as err:
        log_of(*entries)

    (error,) = err.value.errors()
    assert error["loc"] == ("entries",)
    assert error["msg"] == (
        "Value error, entries: the dates run from 2026-10-26 to 2026-11-02, which is 8 days; "
        "a log covers at most 7 consecutive days"
    )


def test_seven_days_that_do_not_start_on_a_monday_are_a_log() -> None:
    first = datetime.date(2026, 10, 28)
    log = log_of(entry(first, "lunch"), entry(first + datetime.timedelta(days=6), "lunch"))

    assert len(log.entries) == 2


def test_the_same_slot_on_the_same_date_twice_is_rejected_naming_it() -> None:
    with pytest.raises(ValidationError) as err:
        log_of(entry(MON, "lunch"), entry(WEEK[1], "lunch"), entry(MON, "lunch", "swapped"))

    (error,) = err.value.errors()
    assert error["loc"] == ("entries",)
    assert error["msg"] == "Value error, entries: 2026-10-26 lunch is listed more than once"


def test_a_skipped_meal_may_not_list_foods() -> None:
    bad = entry(MON, "lunch", "as_planned") | {"status": "skipped"}

    with pytest.raises(ValidationError) as err:
        log_of(entry(MON, "breakfast"), bad)

    (error,) = err.value.errors()
    assert error["loc"] == ("entries", 1)
    assert error["msg"] == "Value error, 2026-10-26 lunch: a skipped meal lists no foods"


@pytest.mark.parametrize("status", ["as_planned", "different_amount", "swapped"])
def test_a_meal_that_was_eaten_needs_foods(status: str) -> None:
    bad = entry(MON, "lunch", "skipped") | {"status": status}

    with pytest.raises(ValidationError) as err:
        log_of(bad)

    (error,) = err.value.errors()
    assert error["loc"] == ("entries", 0)
    assert error["msg"] == (
        f"Value error, 2026-10-26 lunch: a meal with status '{status}' needs at least one food; "
        "mark a meal that was not eaten as 'skipped'"
    )


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        ({"status": "ate_it"}, ("entries", 0, "status")),
        ({"date": "26/10/2026"}, ("entries", 0, "date")),
        ({"surprise": 1}, ("entries", 0, "surprise")),
        ({"items": [rice() | {"amount": 0}]}, ("entries", 0, "items", 0, "amount")),
        ({"items": [rice() | {"amount": -5}]}, ("entries", 0, "items", 0, "amount")),
        ({"items": [rice() | {"unit": ""}]}, ("entries", 0, "items", 0, "unit")),
        ({"items": [rice() | {"food_id": ""}]}, ("entries", 0, "items", 0, "food_id")),
        ({"items": [rice() | {"food_id": "x" * 65}]}, ("entries", 0, "items", 0, "food_id")),
        ({"items": [rice() | {"unit": "x" * 65}]}, ("entries", 0, "items", 0, "unit")),
        ({"items": [rice() | {"surprise": 1}]}, ("entries", 0, "items", 0, "surprise")),
        ({"items": [rice()] * 21}, ("entries", 0, "items")),
    ],
)
def test_a_bad_entry_is_refused_at_the_field(change: dict[str, Any], loc: tuple[Any, ...]) -> None:
    with pytest.raises(ValidationError) as err:
        log_of(entry(MON, "lunch") | change)

    assert [e["loc"] for e in err.value.errors()] == [loc]


def test_a_meal_holds_as_many_foods_as_the_reader_reads_from_one_text() -> None:
    assert MAX_FOODS == MAX_ITEMS == 20


def test_twenty_foods_are_a_meal() -> None:
    assert len(log_of(entry(MON, "lunch", "as_planned", *[rice()] * 20)).entries[0].items) == 20


def test_the_log_has_a_version_and_nothing_else() -> None:
    with pytest.raises(ValidationError) as old:
        MealLog.model_validate({"version": 2, "entries": []})
    with pytest.raises(ValidationError) as extra:
        MealLog.model_validate({"version": 1, "entries": [], "notes": "x"})
    with pytest.raises(ValidationError) as missing:
        MealLog.model_validate({"entries": []})

    assert [e["loc"] for e in old.value.errors()] == [("version",)]
    assert [e["loc"] for e in extra.value.errors()] == [("notes",)]
    assert [e["loc"] for e in missing.value.errors()] == [("version",)]


def test_a_slot_the_plan_does_not_have_is_named() -> None:
    log = log_of(entry(MON, "breakfast"), entry(WEEK[1], "brunch"))

    assert log_problems(plan_rules(), log) == [
        {
            "loc": ["entries", 1, "slot_id"],
            "msg": "2026-10-27 brunch: the plan has no slot 'brunch'; "
            "its slots are breakfast, lunch, snack, dinner",
        }
    ]


async def test_a_slot_the_plan_does_not_have_is_rejected_before_nutrition_mcp_is_asked() -> None:
    nutrition = Spy()
    log = log_of(entry(MON, "breakfast"), entry(WEEK[1], "brunch"), entry(WEEK[2], "elevenses"))

    with pytest.raises(LogRejected) as err:
        await summary_of(log, nutrition=nutrition)

    assert [p["loc"] for p in err.value.problems] == [
        ["entries", 1, "slot_id"],
        ["entries", 2, "slot_id"],
    ]
    assert nutrition.meals == []


async def test_unknown_food_id_422_names_entry() -> None:
    log = log_of(
        entry(MON, "breakfast"),
        entry(
            WEEK[1], "lunch", "as_planned", rice(), {"food_id": "ghost", "amount": 1, "unit": "g"}
        ),
    )

    with pytest.raises(LogRejected) as err:
        await summary_of(log)

    (problem,) = err.value.problems
    assert problem["loc"] == ["entries", 1, "items", 1]
    assert problem["msg"].startswith("2026-10-27 lunch: item 2 (ghost): unknown food id 'ghost'")


async def test_every_refused_entry_is_named_not_only_the_first() -> None:
    log = log_of(
        entry(MON, "breakfast", "as_planned", {"food_id": "ghost", "amount": 1, "unit": "g"}),
        entry(MON, "lunch"),
        entry(MON, "dinner", "as_planned", {"food_id": "rice", "amount": 1, "unit": "bowl"}),
    )

    with pytest.raises(LogRejected) as err:
        await summary_of(log)

    first, second = err.value.problems
    assert first["loc"] == ["entries", 0, "items", 0]
    assert first["msg"].startswith("2026-10-26 breakfast: item 1 (ghost)")
    assert second["loc"] == ["entries", 2, "items", 0]
    assert second["msg"].startswith("2026-10-26 dinner: item 1 (rice): unknown unit 'bowl'")
    assert "valid units: g, katori" in second["msg"]


class Unreachable(Spy):
    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError("could not answer compute_meal", unreachable=True)


class Unreadable(Spy):
    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError("nutrition-mcp's result for compute_meal could not be read")


async def test_nutrition_mcp_being_down_is_an_error_naming_the_step() -> None:
    with pytest.raises(SummaryError, match=r"could not be reached \(step: nutrition\)") as err:
        await summary_of(log_of(entry(MON, "lunch")), nutrition=Unreachable())

    assert err.value.unavailable is True


async def test_an_answer_that_cannot_be_read_is_not_taken_for_a_bad_food() -> None:
    with pytest.raises(SummaryError, match=r"could not be used \(step: nutrition\)") as err:
        await summary_of(log_of(entry(MON, "lunch")), nutrition=Unreadable())

    assert err.value.unavailable is False


# Targets


async def test_targets_follow_day_type() -> None:
    # Monday is a rest day in this plan, so a Monday is held to the rest day's targets.
    rules = plan_rules(week={**plan_rules().week, "mon": "rest"})
    log = log_of(entry(MON, "lunch"), entry(WEEK[2], "lunch"))

    monday, wednesday = (await summary_of(log, rules=rules)).days

    assert (monday.date.weekday(), monday.day_type) == (0, "rest")
    assert monday.targets == {
        "kcal": 2250,
        "protein_g": 130,
        "sat_fat_g": 25,
        "fibre_g": 30,
        "carbs_available_g": 270,
        "fat_g": 75,
        "iron_mg": 15,
    }
    assert (wednesday.date.weekday(), wednesday.day_type) == (2, "training")
    assert wednesday.targets == {
        "kcal": 2650,
        "protein_g": 150,
        "sat_fat_g": 28,
        "fibre_g": 35,
        "carbs_available_g": 340,
        "fat_g": 80,
        "calcium_mg": 1000,
        "sodium_mg": 2300,
    }


async def test_the_failures_of_a_day_are_those_of_its_own_day_type() -> None:
    log = log_of(entry(MON, "lunch"), entry(WEEK[1], "lunch"))

    monday, tuesday = (await summary_of(log)).days

    assert monday.failures[0].message == (
        "calories 130 kcal, day needs at least 2518 kcal (2650 kcal target, within 5%)"
    )
    assert tuesday.failures[0].message == (
        "calories 130 kcal, day needs at least 2138 kcal (2250 kcal target, within 5%)"
    )


# The export


async def a_week() -> WeekResult:
    cucumber = item("cucumber", pack_g=1000, keeps=3)
    events = [
        event
        async for event in run_week(
            plan_rules(),
            model=model_for(),
            nutrition=SpyNutrition(),
            catalogue=Catalogue({**catalogue().items, "cucumber": cucumber}),
            settings=Settings(),
        )
    ]
    done = events[-1]
    assert isinstance(done, WeekDone)
    return done.week


async def test_export_round_trip() -> None:
    week = await a_week()
    assert week.no_waste  # the warnings travel as rows in JSON, so they are part of the trip
    log = log_of(entry(MON, "breakfast"), entry(MON, "lunch", "skipped"))
    export = Export(version=1, rules=plan_rules(), week=week, log=log)

    text = export.model_dump_json()
    again = Export.model_validate_json(text)

    assert again == export
    assert again.model_dump_json() == text
    assert isinstance(json.loads(text)["week"]["no_waste"], list)


def test_export_round_trip_without_a_week() -> None:
    export = Export(version=1, rules=plan_rules(), week=None, log=log_of(entry(MON, "lunch")))

    again = Export.model_validate_json(export.model_dump_json())

    assert again == export
    assert again.week is None


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        ({"version": 2}, ("version",)),
        ({"surprise": 1}, ("surprise",)),
        (
            {"log": {"version": 1, "entries": [{"date": "2026-10-26"}]}},
            ("log", "entries", 0, "slot_id"),
        ),
    ],
)
def test_an_export_that_does_not_fit_is_refused_at_the_field(
    change: dict[str, Any], loc: tuple[Any, ...]
) -> None:
    export = {
        "version": 1,
        "rules": json.loads(plan_rules().model_dump_json()),
        "week": None,
        "log": {"version": 1, "entries": []},
    }

    with pytest.raises(ValidationError) as err:
        Export.model_validate(export | change)

    assert loc in [e["loc"] for e in err.value.errors()]


def test_the_schema_of_an_export_says_how_the_warnings_are_written_in_json() -> None:
    """The browser checks a file against the schema, and the file has the warnings as rows."""
    schema = Export.model_json_schema()

    no_waste = schema["$defs"]["WeekResult"]["properties"]["no_waste"]
    assert no_waste["type"] == "array"
    assert no_waste["items"] == {"$ref": "#/$defs/NoWasteRow"}
    assert schema["properties"]["version"] == {"const": 1, "title": "Version", "type": "integer"}
