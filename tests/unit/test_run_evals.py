"""The eval runner: metrics on fixed results, the slices, the results file, summary.md, --check,
--resume and a replay that needs no model key."""

from pathlib import Path
from typing import Any

import pytest
from cases import (
    EXPECTED_MISSES,
    SMOKE_MEALS,
    SMOKE_PLANS,
    SelectionError,
    load_meals,
    load_plans,
    select,
)
from eval_helpers import FIXED_LABELS, FIXED_READS, FIXED_WEEKS
from metrics import reading_metrics, recipe_metrics
from records import FailureRecord
from results import (
    Results,
    RunInfo,
    Switches,
    build_results,
    build_summary,
    check_thresholds,
    load_thresholds,
    write_results,
)


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


# summary.md and --check


def fixed_results(**changes: Any) -> Results:
    info = RunInfo(
        date="2026-10-27",
        model="gemini/gemini-3.1-flash-lite",
        commit="abc1234",
        dirty=False,
        dataset_version="4a35ea1576a9",
        litellm_version="1.105.1",
        suite="all",
        slice="full",
        plans=["a", "b", "c"],
        meals=["m1", "m2"],
        switches=Switches(fit_portions=True, writer_nutrient_hints=True),
        replayed=False,
        settings={},
    ).model_copy(update=changes)
    baseline = [
        week.model_copy(
            update={
                "system": "baseline",
                "case": f"recipes/baseline/{week.plan}",
                # The baseline's a2 is never repaired: it ends failing slot_kcal.
                "recipes": [
                    r.model_copy(
                        update={"failures": [FailureRecord(check="slot_kcal", message="m")]}
                    )
                    if r.first_failures == ["slot_kcal"]
                    else r
                    for r in week.recipes
                ],
            }
        )
        for week in FIXED_WEEKS
    ]
    return build_results(
        info,
        [*FIXED_WEEKS, *baseline, *FIXED_READS],
        FIXED_LABELS,
        finished_at="2026-10-27T10:00:00+00:00",
    )


def test_summary_first_row_is_headline(tmp_path: Path) -> None:
    write_results(tmp_path, fixed_results())

    summary = build_summary(tmp_path)

    assert summary is not None
    lines = summary.splitlines()
    first_table = next(i for i, line in enumerate(lines) if line.startswith("|"))
    header = [cell.strip() for cell in lines[first_table].strip("|").split("|")]
    first_row = [cell.strip() for cell in lines[first_table + 2].strip("|").split("|")]
    assert header == ["Measure", "Baseline", "This project"]
    assert first_row == [
        "Recipes passing every hard check (share)",
        "42.9% (3 of 7)",
        "57.1% (4 of 7)",
    ]


def test_check_fails_below_threshold(tmp_path: Path) -> None:
    thresholds = tmp_path / "thresholds.toml"
    thresholds.write_text(
        "[min]\n"
        '"recipes.pipeline.recipe_pass_share" = 0.6\n'
        '"recipes.pipeline.variety_pass_share" = 0.7\n'
        '"reading.all.food_recall" = 0.75\n'
        "[max]\n"
        '"reading.all.kcal_mape" = 0.5\n'
    )

    problems = check_thresholds(fixed_results(), load_thresholds(thresholds))

    assert problems == [
        "recipes.pipeline.recipe_pass_share is 0.5714, below its minimum 0.6",
        "reading.all.food_recall is 0.5, below its minimum 0.75",
        "reading.all.kcal_mape is 0.75, above its maximum 0.5",
    ]
