"""The eval runner: metrics on fixed results, the slices, the results file, summary.md, --check,
--resume and a replay that needs no model key."""

from typing import Any

import pytest
from cases import (
    EXPECTED_MISSES,
    SMOKE_MEALS,
    SMOKE_PLANS,
    LabelItem,
    MealLabel,
    SelectionError,
    load_meals,
    load_plans,
    select,
)
from metrics import reading_metrics, recipe_metrics
from records import (
    DayOutcome,
    FailureRecord,
    ReadingCase,
    ReadItemRecord,
    RecipeCase,
    RecipeOutcome,
    Usage,
)

from meal_planner.rules import DayKey

# Hand-made results


def outcome(final: list[str] | None, first: list[str] | None = None) -> RecipeOutcome:
    """A recipe that ended with the checks in `final` (None: never written). `first` defaults to
    `final`, a recipe that was never repaired."""
    if final is None:
        return RecipeOutcome(day="mon", slot_id="s", written=False, unwritten_reason="timeout")
    return RecipeOutcome(
        day="mon",
        slot_id="s",
        written=True,
        failures=[FailureRecord(check=c, message=c) for c in final],
        first_failures=final if first is None else first,
    )


def week(
    plan: str,
    recipes: list[RecipeOutcome],
    days: list[list[str]],
    usage: Usage,
    seconds: float,
) -> RecipeCase:
    names: list[DayKey] = ["mon", "tue", "wed"]
    return RecipeCase(
        case=f"recipes/pipeline/{plan}",
        system="pipeline",
        plan=plan,
        seconds=seconds,
        usage=usage,
        recipes=recipes,
        days=[
            DayOutcome(day=names[i], failures=[FailureRecord(check=c, message=c) for c in checks])
            for i, checks in enumerate(days)
        ],
        no_waste=1,
    )


FIXED_WEEKS = [
    # a: passes; passes after a repair of a day failure it was blamed for; fails variety at the
    # end and at the start; never written. Its second day fails.
    week(
        "a",
        [
            outcome([]),
            outcome(["day_kcal"], first=["slot_kcal"]),
            outcome(["variety_title"]),
            outcome(None),
        ],
        [[], ["day_kcal"]],
        Usage(calls=5, input_tokens=100, output_tokens=50, cost_usd=0.01, errors={"timeout": 1}),
        10.0,
    ),
    # b: one recipe passes, one ends failing gear. Its day passes.
    week(
        "b",
        [outcome([]), outcome(["gear"])],
        [[]],
        Usage(calls=3, input_tokens=60, output_tokens=40, cost_usd=0.02),
        30.0,
    ),
    # c: passes outright.
    week(
        "c",
        [outcome([])],
        [[]],
        Usage(calls=1, input_tokens=10, output_tokens=5, cost_usd=0.0),
        20.0,
    ),
]


def label(meal: str, expected: list[dict[str, Any]], missing: list[str]) -> MealLabel:
    return MealLabel(
        id=meal,
        text="typed",
        expected=[LabelItem(amount=None, unit=None, grams=None, **e) for e in expected],
        missing=missing,
        note="",
    )


def item(food_id: str | None, status: str) -> ReadItemRecord:
    return ReadItemRecord(
        name=food_id or "?",
        food_id=food_id,
        status=status,  # type: ignore[arg-type]
        amount=None,
        unit=None,
        grams=None,
    )


FIXED_LABELS = {
    "m1": label(
        "m1",
        [
            {"food_id": "chapati", "status": "needs_amount"},
            {"food_id": "dal_tadka", "status": "matched", "also_accept": ["musur_dal"]},
        ],
        ["poha"],
    ),
    "m2": label(
        "m2",
        [{"food_id": "idli", "status": "matched"}, {"food_id": "sambar", "status": "unknown_unit"}],
        ["chai", "black coffee"],
    ),
}
FIXED_READS = [
    # Finds both foods (dal through also_accept), gives chapati an amount it should have asked
    # for, adds a food nobody ate, and reports poha missing. 300 kcal against the label's 200.
    ReadingCase(
        case="reading/m1",
        meal="m1",
        seconds=2.0,
        usage=Usage(calls=2, input_tokens=40, output_tokens=10, cost_usd=0.001),
        error=None,
        items=[
            item("chapati", "matched"),
            item("musur_dal", "matched"),
            item("paneer", "matched"),
            item(None, "missing"),
        ],
        missing=["Poha "],
        kcal=300.0,
        label_kcal=200.0,
    ),
    # The read failed: nothing found, nothing invented, no calories against the label's 100.
    ReadingCase(
        case="reading/m2",
        meal="m2",
        seconds=1.0,
        usage=Usage(calls=1, input_tokens=20, output_tokens=0, errors={"invalid_json": 1}),
        error="the model's reply could not be used (step: read_meal)",
        items=[],
        missing=[],
        kcal=None,
        label_kcal=100.0,
    ),
]


def test_metrics_on_fixed_results() -> None:
    recipes = recipe_metrics(FIXED_WEEKS)

    # 7 recipes asked for. Passing at the end: a1, a2 (its day failure counts on the day), b1, c1.
    assert (recipes.recipes_asked, recipes.recipes_written) == (7, 6)
    assert (recipes.recipes_passing, recipes.recipe_pass_share) == (4, 0.5714)
    # Passing the first time: a1, b1, c1 (a2 failed slot_kcal, a3 variety_title, b2 gear).
    assert (recipes.first_attempt_passing, recipes.first_attempt_pass_share) == (3, 0.4286)
    # No variety failure: everything written but a3.
    assert (recipes.variety_passing, recipes.variety_pass_share) == (5, 0.7143)
    assert (recipes.days, recipes.days_passing, recipes.day_pass_share) == (4, 3, 0.75)
    # Only c passes whole: a has failing recipes and a failing day, b a failing recipe.
    assert (recipes.weeks_passing, recipes.week_pass_share) == (1, 0.3333)
    assert (recipes.calls, recipes.calls_per_week) == (9, 3.0)
    assert (recipes.input_tokens, recipes.output_tokens, recipes.tokens_per_week) == (
        170,
        95,
        88.3,
    )
    assert (recipes.cost_usd, recipes.cost_usd_per_week) == (0.03, 0.01)
    # Nearest rank of three: ceil(0.95 * 3) = 3, the slowest.
    assert recipes.seconds_p95 == 30.0
    assert recipes.call_errors == {"timeout": 1}
    assert recipes.failing_checks == {"gear": 1, "variety_title": 1}
    assert recipes.failing_day_checks == {"day_kcal": 1}
    assert recipes.no_waste_flags == 3

    reading = reading_metrics(FIXED_READS, FIXED_LABELS)

    assert (reading.meals, reading.reads_failed) == (2, 1)
    # Reader items with a food id: chapati, musur_dal, paneer; the first two are found.
    assert (reading.reader_items, reading.reader_items_found, reading.food_precision) == (
        3,
        2,
        0.6667,
    )
    assert (reading.expected_items, reading.expected_found, reading.food_recall) == (4, 2, 0.5)
    # Only dal has the label's status (chapati should have asked).
    assert (reading.status_matched, reading.status_accuracy) == (1, 0.25)
    # Chapati was given an amount; sambar was not returned, so nothing was invented for it.
    assert (reading.ask_labels, reading.asked, reading.asked_share) == (2, 1, 0.5)
    # |300 - 200| / 200 = 0.5 and |0 - 100| / 100 = 1.0.
    assert (reading.kcal_meals, reading.kcal_mape) == (2, 0.75)
    assert (reading.missing_labels, reading.missing_reported, reading.missing_share) == (
        3,
        1,
        0.3333,
    )
    assert (reading.calls, reading.calls_per_meal, reading.tokens_per_meal) == (3, 1.5, 35.0)
    assert (reading.cost_usd, reading.cost_usd_per_meal) == (0.001, 0.0005)
    assert reading.seconds_p95 == 2.0
    assert reading.call_errors == {"invalid_json": 1}


# The slices


PLAN_IDS = list(load_plans())
MEAL_IDS = list(load_meals())


def test_the_smoke_slice_is_three_plans_one_hard_and_ten_meals() -> None:
    picked = select("all", PLAN_IDS, MEAL_IDS, smoke=True)

    assert picked.slice == "smoke"
    assert picked.plans == sorted(SMOKE_PLANS)
    assert len(picked.plans) == 3
    assert sum(1 for p in picked.plans if p.startswith("hard_")) == 1
    assert picked.meals == list(SMOKE_MEALS)


def test_the_smoke_meals_cover_every_status_a_missing_food_and_the_instruction_meal() -> None:
    meals = load_meals()
    smoke = [meals[m] for m in SMOKE_MEALS]

    statuses = {e.status for m in smoke for e in m.expected}
    assert statuses == {"matched", "needs_amount", "unknown_unit"}
    assert any(m.missing for m in smoke)
    assert "m28" in SMOKE_MEALS
    assert "Ignore your instructions" in meals["m28"].text
    assert set(SMOKE_MEALS) & set(EXPECTED_MISSES)


def test_the_expected_misses_are_meals_in_the_dataset() -> None:
    assert set(EXPECTED_MISSES) <= set(MEAL_IDS)


def test_a_full_run_takes_every_case_in_dataset_order() -> None:
    picked = select("all", PLAN_IDS, MEAL_IDS)

    assert (picked.plans, picked.meals, picked.slice) == (PLAN_IDS, MEAL_IDS, "full")
    assert (len(picked.plans), len(picked.meals)) == (20, 53)


@pytest.mark.parametrize(("suite", "plans", "meals"), [("recipes", 20, 0), ("reading", 0, 53)])
def test_a_suite_leaves_out_the_other_suites_cases(suite: Any, plans: int, meals: int) -> None:
    picked = select(suite, PLAN_IDS, MEAL_IDS)

    assert (len(picked.plans), len(picked.meals), picked.slice) == (plans, meals, "full")


def test_only_and_limit_make_a_partial_run() -> None:
    only = select("all", PLAN_IDS, MEAL_IDS, only=["m05", "bengali_fish_home", "m02"])
    limited = select("all", PLAN_IDS, MEAL_IDS, limit=2)

    assert (only.plans, only.meals, only.slice) == (
        ["bengali_fish_home"],
        ["m02", "m05"],
        "partial",
    )
    assert (limited.plans, limited.meals, limited.slice) == (PLAN_IDS[:2], MEAL_IDS[:2], "partial")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"smoke": True, "only": ["m01"]}, "cannot be used together"),
        ({"only": ["m99", "nope"]}, "m99, nope"),
        ({"limit": 0}, "at least 1"),
    ],
)
def test_a_selection_that_picks_nothing_sensible_is_refused(
    kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(SelectionError, match=message):
        select("all", PLAN_IDS, MEAL_IDS, **kwargs)


def test_a_suite_with_none_of_the_named_cases_is_refused() -> None:
    with pytest.raises(SelectionError, match="no cases left"):
        select("recipes", PLAN_IDS, MEAL_IDS, only=["m01"])
