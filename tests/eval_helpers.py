"""Hand-made eval results whose metrics are known, shared by the runner and results tests."""

from typing import Any

from cases import LabelItem, MealLabel
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
