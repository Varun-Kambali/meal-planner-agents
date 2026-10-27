"""Each eval metric on hand-made case lines where the answer is known, and the check names the
metrics rely on, checked against what checks.py actually gives."""

from typing import Any

import pytest
from cases import LabelItem, MealLabel
from metrics import (
    DAY_CHECK_PREFIX,
    VARIETY_CHECKS,
    names_match,
    p95,
    pair,
    reading_metrics,
    recipe_metrics,
    share,
)
from nutrition_mcp.models import Nutrients
from nutrition_mcp.schemas import MealResult
from records import (
    DayOutcome,
    FailureRecord,
    ReadingCase,
    ReadItemRecord,
    RecipeCase,
    RecipeOutcome,
    Usage,
    first_checks,
    recipe_case,
)
from week_helpers import canned, catalogue, plan_rules

from meal_planner.checks import (
    check_catalogue,
    check_day_totals,
    check_gear_and_time,
    check_model_figures,
    check_slot_targets,
    check_variety,
)
from meal_planner.config import Settings
from meal_planner.recipes import CheckedRecipe, DraftRecipe, Failure
from meal_planner.rules import DayKey, expand
from meal_planner.week_events import RecipeChecked, WeekResult


def fail(*checks: str) -> list[FailureRecord]:
    return [FailureRecord(check=c, message=c) for c in checks]


def written(*final: str, first: list[str] | None = None, day: DayKey = "mon") -> RecipeOutcome:
    return RecipeOutcome(
        day=day,
        slot_id="lunch",
        written=True,
        failures=fail(*final),
        first_failures=list(final) if first is None else first,
    )


def unwritten(day: DayKey = "mon") -> RecipeOutcome:
    return RecipeOutcome(day=day, slot_id="lunch", written=False, unwritten_reason="timeout")


def week(
    recipes: list[RecipeOutcome],
    days: list[list[str]] | None = None,
    *,
    usage: Usage | None = None,
    seconds: float = 1.0,
) -> RecipeCase:
    return RecipeCase(
        case="recipes/pipeline/p",
        system="pipeline",
        plan="p",
        seconds=seconds,
        usage=usage or Usage(),
        recipes=recipes,
        days=[DayOutcome(day="mon", failures=fail(*checks)) for checks in (days or [[]])],
        no_waste=0,
    )


# Small pieces


def test_share_keeps_four_places_and_is_none_for_nothing() -> None:
    assert share(2, 3) == 0.6667
    assert share(0, 5) == 0.0
    assert share(0, 0) is None


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], None), ([5.0], 5.0), ([3.0, 1.0, 2.0], 3.0), ([float(i) for i in range(1, 21)], 19.0)],
)
def test_p95_is_the_nearest_rank(values: list[float], expected: float | None) -> None:
    assert p95(values) == expected


def test_pair_finds_the_most_pairs_and_uses_each_item_once() -> None:
    accepts = [{"x", "y"}, {"x"}]

    paired = pair(accepts, ["x", "y"], lambda a, b: b in a)

    # A greedy pass would give the first label "x" and leave the second with nothing.
    assert paired == [1, 0]


def test_pair_leaves_an_item_unpaired_when_nothing_fits() -> None:
    assert pair(["a", "b"], ["b"], lambda a, b: a == b) == [None, 0]
    assert pair(["a", "a"], ["a"], lambda a, b: a == b) == [0, None]


@pytest.mark.parametrize(
    ("label", "reported", "same"),
    [
        ("black coffee", "coffee", True),
        ("coffee", "Black Coffee", True),
        ("medu vada", "Medu vadas", True),
        ("momo", "momos", True),
        ("sabzi", "aloo sabzi", True),
        ("chicken biryani", "biryani", True),
        ("paneer tikka", "paneer tikka pieces", True),
        ("raita", "a", False),
        ("chai", "chaix", False),
        ("tea", "steak", False),
        ("miso soup", "soup miso", False),
        ("chai", "poha", False),
        ("chai", "", False),
        ("", "", False),
    ],
)
def test_a_missing_name_matches_when_the_words_of_one_run_inside_the_other(
    label: str, reported: str, same: bool
) -> None:
    assert names_match(label, reported) is same


# Recipe metrics


def test_a_day_failure_on_a_recipe_does_not_fail_the_recipe_but_fails_the_day() -> None:
    case = week([written("day_kcal")], days=[["day_kcal"]])

    metrics = recipe_metrics([case])

    assert (metrics.recipes_passing, metrics.days_passing, metrics.weeks_passing) == (1, 0, 0)
    assert metrics.failing_checks == {}
    assert metrics.failing_day_checks == {"day_kcal": 1}


def test_an_unwritten_recipe_counts_against_every_recipe_share() -> None:
    metrics = recipe_metrics([week([written(), unwritten()])])

    assert metrics.recipes_asked == 2
    assert metrics.recipes_written == 1
    assert metrics.recipe_pass_share == 0.5
    assert metrics.first_attempt_pass_share == 0.5
    assert metrics.variety_pass_share == 0.5
    assert metrics.week_pass_share == 0.0


def test_first_attempt_needs_a_recipe_at_the_first_attempt() -> None:
    # Written only at the second attempt (its slot's first reply was unusable), then passed.
    late = written(first=None)
    late = late.model_copy(update={"first_failures": None})

    metrics = recipe_metrics([week([late, written(first=["slot_kcal"])])])

    assert (metrics.recipes_passing, metrics.first_attempt_passing) == (2, 0)


def test_variety_counts_only_variety_checks() -> None:
    recipes = [written(check) for check in sorted(VARIETY_CHECKS)] + [written("slot_kcal")]

    metrics = recipe_metrics([week(recipes)])

    assert metrics.variety_passing == 1
    assert metrics.recipes_passing == 0
    assert metrics.failing_checks == {**dict.fromkeys(sorted(VARIETY_CHECKS), 1), "slot_kcal": 1}


def test_a_check_failed_twice_counts_the_recipe_once() -> None:
    metrics = recipe_metrics([week([written("unknown_ingredient", "unknown_ingredient")])])

    assert metrics.failing_checks == {"unknown_ingredient": 1}


def test_cost_is_unknown_when_a_call_that_got_a_reply_had_no_price() -> None:
    priced = Usage(calls=2, cost_usd=0.004)
    unpriced = Usage(calls=1, cost_unknown_calls=1)

    known = recipe_metrics([week([written()], usage=priced)] * 2)
    unknown = recipe_metrics([week([written()], usage=priced), week([written()], usage=unpriced)])

    assert (known.cost_usd, known.cost_usd_per_week) == (0.008, 0.004)
    assert (unknown.cost_usd, unknown.cost_usd_per_week) == (None, None)
    assert unknown.calls == 3


def test_call_errors_are_added_up_across_weeks() -> None:
    a = Usage(calls=2, errors={"invalid_json": 1})
    b = Usage(calls=3, errors={"invalid_json": 1, "timeout": 1})

    metrics = recipe_metrics([week([written()], usage=a), week([written()], usage=b)])

    assert metrics.call_errors == {"invalid_json": 2, "timeout": 1}


def test_no_weeks_gives_no_shares() -> None:
    metrics = recipe_metrics([])

    assert metrics.recipe_pass_share is None
    assert metrics.seconds_p95 is None
    assert metrics.calls_per_week is None


# Reading metrics


def label(meal: str, expected: list[dict[str, Any]], missing: list[str] | None = None) -> MealLabel:
    return MealLabel(
        id=meal,
        text="typed",
        expected=[LabelItem(amount=None, unit=None, grams=None, **e) for e in expected],
        missing=missing or [],
        note="",
    )


def read(
    meal: str,
    items: list[tuple[str | None, str]],
    *,
    missing: list[str] | None = None,
    kcal: float | None = None,
    label_kcal: float | None = None,
    error: str | None = None,
) -> ReadingCase:
    return ReadingCase(
        case=f"reading/{meal}",
        meal=meal,
        seconds=1.0,
        usage=Usage(calls=1),
        error=error,
        items=[
            ReadItemRecord(
                name=food_id or "x",
                food_id=food_id,
                status=status,
                amount=None,
                unit=None,
                grams=None,
            )
            for food_id, status in items
        ],
        missing=missing or [],
        kcal=kcal,
        label_kcal=label_kcal,
    )


def test_also_accept_finds_a_label() -> None:
    labels = {
        "m": label(
            "m", [{"food_id": "dal_tadka", "status": "matched", "also_accept": ["musur_dal"]}]
        )
    }

    metrics = reading_metrics([read("m", [("musur_dal", "matched")])], labels)

    assert (metrics.expected_found, metrics.reader_items_found) == (1, 1)


def test_a_food_the_reader_names_twice_is_found_once() -> None:
    labels = {"m": label("m", [{"food_id": "egg", "status": "matched"}])}

    metrics = reading_metrics([read("m", [("egg", "matched"), ("egg", "matched")])], labels)

    assert (metrics.reader_items, metrics.reader_items_found) == (2, 1)
    assert metrics.food_precision == 0.5
    assert metrics.food_recall == 1.0


def test_a_missing_item_is_left_out_of_precision() -> None:
    labels = {"m": label("m", [{"food_id": "egg", "status": "matched"}], ["black coffee"])}

    metrics = reading_metrics(
        [read("m", [("egg", "matched"), (None, "missing")], missing=["coffee"])], labels
    )

    assert metrics.reader_items == 1
    assert metrics.food_precision == 1.0
    assert (metrics.missing_labels, metrics.missing_reported) == (1, 1)


def test_one_reported_name_counts_for_one_label_only() -> None:
    labels = {"m": label("m", [], ["aloo sabzi", "bhindi sabzi"])}

    metrics = reading_metrics([read("m", [], missing=["sabzi"])], labels)

    assert metrics.missing_reported == 1


@pytest.mark.parametrize(
    ("status", "asked"),
    [("matched", 0), ("needs_amount", 1), ("unknown_unit", 1)],
)
def test_asked_counts_a_label_that_asks_unless_the_reader_matched_it(
    status: str, asked: int
) -> None:
    labels = {"m": label("m", [{"food_id": "chapati", "status": "needs_amount"}])}

    metrics = reading_metrics([read("m", [("chapati", status)])], labels)

    assert (metrics.ask_labels, metrics.asked) == (1, asked)
    assert metrics.status_matched == (1 if status == "needs_amount" else 0)


def test_a_failed_reads_ask_labels_are_left_out_of_asked() -> None:
    labels = {
        "m": label(
            "m",
            [
                {"food_id": "chapati", "status": "needs_amount"},
                {"food_id": "pav", "status": "unknown_unit"},
            ],
        )
    }

    metrics = reading_metrics([read("m", [], error="the model could not be reached")], labels)

    assert metrics.reads_failed == 1
    assert (metrics.ask_labels, metrics.asked, metrics.asked_share) == (0, 0, None)
    assert metrics.ask_labels_unpaired == 0


def test_an_ask_label_the_reader_left_out_is_unpaired_and_not_asked() -> None:
    labels = {"m": label("m", [{"food_id": "chapati", "status": "needs_amount"}])}

    metrics = reading_metrics([read("m", [("dal_tadka", "matched")])], labels)

    assert (metrics.ask_labels, metrics.asked, metrics.ask_labels_unpaired) == (1, 0, 1)
    assert metrics.asked_share == 0.0


def test_an_ask_label_returned_as_another_food_with_an_amount_is_unpaired_and_not_asked() -> None:
    # The person gave no amount for the rice; the reader named another rice and gave it one.
    labels = {"m": label("m", [{"food_id": "steamed_rice", "status": "needs_amount"}])}

    metrics = reading_metrics([read("m", [("basmati_rice", "matched")])], labels)

    assert (metrics.ask_labels, metrics.asked, metrics.ask_labels_unpaired) == (1, 0, 1)


def test_a_meal_whose_label_has_no_calories_is_left_out_of_the_error() -> None:
    labels = {
        "a": label("a", [{"food_id": "x", "status": "needs_amount"}]),
        "b": label("b", [{"food_id": "y", "status": "matched"}]),
    }
    cases = [
        read("a", [], kcal=500.0, label_kcal=None),
        read("b", [("y", "matched")], kcal=90.0, label_kcal=100.0),
    ]

    metrics = reading_metrics(cases, labels)

    assert metrics.kcal_meals == 1
    assert metrics.kcal_mape == 0.1


def test_a_reader_total_of_nothing_counts_as_no_calories() -> None:
    labels = {"m": label("m", [{"food_id": "y", "status": "matched"}])}

    metrics = reading_metrics([read("m", [], kcal=None, label_kcal=250.0)], labels)

    assert metrics.kcal_mape == 1.0


# The check names the metrics rely on


def checked(day: DayKey, slot_id: str, **changes: Any) -> CheckedRecipe:
    rules = plan_rules()
    spec = next(s for s in expand(rules) if s.day == day and s.slot.id == slot_id)
    recipe = next(r for r in canned(slot_id)["recipes"] if r["day"] == day) | changes
    return CheckedRecipe(
        spec=spec, draft=DraftRecipe.model_validate(recipe), nutrition=None, failures=[], attempts=1
    )


def test_every_check_variety_gives_is_a_variety_check() -> None:
    monday = checked("mon", "lunch")
    tuesday = checked(
        "tue",
        "lunch",
        title=monday.draft.title,
        ingredients=[i.model_dump() for i in monday.draft.ingredients],
        cuisine=monday.draft.cuisine,
    )
    wednesday = checked("wed", "lunch", cuisine="Martian")

    found = check_variety([monday, tuesday, wednesday], plan_rules(), Settings())

    names = {f.check for failures in found.values() for f in failures}
    assert names == VARIETY_CHECKS


def test_day_checks_and_only_day_checks_start_with_the_day_prefix() -> None:
    rules = plan_rules()
    nothing_known = Nutrients.model_validate({})
    day_failures, _ = check_day_totals(nothing_known, {}, rules.day_types[0], Settings())
    recipe = checked("mon", "lunch", title="A 450 kcal bowl", gear=["oven"], active_min=999)
    ghost = DraftRecipe.model_validate(
        recipe.draft.model_dump() | {"ingredients": [{"id": "ghost", "grams": 10}]}
    )
    too_little = MealResult.model_validate(
        {
            "items": [],
            "total": {"kcal": 1.0, "protein_g": 0.0},
            "total_incomplete": {},
            "glycemic_load": None,
            "glycemic_load_missing": [],
        }
    )
    recipe_failures: list[Failure] = [
        *check_catalogue(ghost, catalogue(), rules),
        *check_slot_targets(too_little, recipe.spec, Settings()),
        *check_gear_and_time(recipe.draft, rules, recipe.spec.slot),
        *check_model_figures(recipe.draft),
    ]

    assert day_failures
    assert all(f.check.startswith(DAY_CHECK_PREFIX) for f in day_failures)
    assert {f.check for f in recipe_failures} >= {
        "unknown_ingredient",
        "slot_kcal",
        "slot_protein_g",
        "gear",
        "active_min",
        "time",
        "model_figure",
    }
    assert not any(f.check.startswith(DAY_CHECK_PREFIX) for f in recipe_failures)


# Building the lines


def test_first_checks_keeps_the_first_event_and_only_at_attempt_one() -> None:
    events = [
        RecipeChecked(
            day="mon",
            slot_id="lunch",
            attempt=1,
            failures=[Failure(check="slot_kcal", message="m")],
        ),
        RecipeChecked(day="mon", slot_id="lunch", attempt=2, failures=[]),
        RecipeChecked(day="tue", slot_id="lunch", attempt=2, failures=[]),
    ]

    assert first_checks(events) == {("mon", "lunch"): ["slot_kcal"], ("tue", "lunch"): None}


def test_a_week_line_lists_every_recipe_asked_for_in_week_order() -> None:
    rules = plan_rules()
    kept = [checked("mon", "breakfast"), checked("mon", "lunch")]
    result = WeekResult(
        recipes=kept,
        days=[],
        no_waste={},
        unwritten=[("tue", "lunch", "rate_limited")],
        calls=1,
        input_tokens=0,
        output_tokens=0,
        cost_usd=None,
    )

    line = recipe_case(
        system="baseline",
        plan="p",
        rules=rules,
        week=result,
        first=None,
        usage=Usage(),
        seconds=1.23456,
    )

    assert len(line.recipes) == 28
    assert [(r.day, r.slot_id) for r in line.recipes[:2]] == [
        ("mon", "breakfast"),
        ("mon", "lunch"),
    ]
    tuesday_lunch = next(r for r in line.recipes if (r.day, r.slot_id) == ("tue", "lunch"))
    assert (tuesday_lunch.written, tuesday_lunch.unwritten_reason) == (False, "rate_limited")
    assert line.recipes[0].first_failures == []
    assert line.seconds == 1.235
    assert line.case == "recipes/baseline/p"
