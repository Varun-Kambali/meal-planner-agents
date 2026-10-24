"""Fitting a recipe's grams to its slot's calories: pure functions, no model and no network."""

import pytest
from nutrition_mcp.models import Nutrients
from nutrition_mcp.schemas import MealResult
from pydantic import ValidationError
from week_helpers import plan_rules, recipe_of

from meal_planner.config import Settings
from meal_planner.portions import portion_factor, scale_draft
from meal_planner.recipes import CheckedRecipe, DraftIngredient, DraftRecipe
from meal_planner.rules import SlotSpec, expand

# Monday on the fixture plan is a training day (2650 kcal): breakfast's share is 662.5 kcal, so
# its band is 596.25 to 728.75, and lunch's share is 795 kcal.
SPECS = {(s.day, s.slot.id): s for s in expand(plan_rules())}
BREAKFAST = SPECS[("mon", "breakfast")]
LUNCH = SPECS[("mon", "lunch")]


def draft(*grams: float) -> DraftRecipe:
    base = DraftRecipe.model_validate(recipe_of("breakfast", "mon"))
    return base.model_copy(
        update={
            "ingredients": [DraftIngredient(id=f"food{n}", grams=g) for n, g in enumerate(grams)]
        }
    )


def checked(
    kcal: float | None,
    *,
    spec: SlotSpec = BREAKFAST,
    grams: tuple[float, ...] = (300.0,),
    incomplete: bool = False,
    has_numbers: bool = True,
) -> CheckedRecipe:
    meal = MealResult(
        items=[],
        total=Nutrients(kcal=kcal),
        total_incomplete={"kcal": ["mystery"]} if incomplete else {},
        glycemic_load=None,
        glycemic_load_missing=[],
    )
    return CheckedRecipe(
        spec=spec,
        draft=draft(*grams),
        nutrition=meal if has_numbers else None,
        failures=[],
        attempts=1,
    )


# portion_factor


def test_factor_brings_kcal_to_share() -> None:
    factor = portion_factor(checked(565), Settings())

    assert factor == pytest.approx(662.5 / 565)


def test_no_factor_inside_band() -> None:
    assert portion_factor(checked(640), Settings()) is None


@pytest.mark.parametrize(
    ("kcal", "expect_factor"),
    [(596.25, False), (596.0, True), (728.75, False), (729.0, True)],
    ids=["low_edge", "just_under", "high_edge", "just_over"],
)
def test_the_band_is_the_one_the_slot_check_uses(kcal: float, expect_factor: bool) -> None:
    factor = portion_factor(checked(kcal), Settings())

    assert (factor is not None) is expect_factor


def test_band_follows_the_tolerance_setting() -> None:
    # 640 is inside 10% of 662.5 and outside 2%.
    assert portion_factor(checked(640), Settings(recipe_kcal_tolerance=0.02)) == pytest.approx(
        662.5 / 640
    )


@pytest.mark.parametrize("kcal", [300, 1700], ids=["too_low", "too_high"])
def test_no_factor_outside_bounds(kcal: float) -> None:
    # 795 / 300 is 2.65 and 795 / 1700 is 0.47, past the default bounds of 2 and 0.5.
    assert portion_factor(checked(kcal, spec=LUNCH), Settings()) is None


@pytest.mark.parametrize(("kcal", "factor"), [(397.5, 2.0), (1590, 0.5)], ids=["max", "min"])
def test_the_bounds_themselves_are_allowed(kcal: float, factor: float) -> None:
    assert portion_factor(checked(kcal, spec=LUNCH), Settings()) == pytest.approx(factor)


def test_bounds_follow_the_settings() -> None:
    settings = Settings(portion_scale_min=0.8, portion_scale_max=1.15)

    # 662.5 / 565 is 1.17 and 662.5 / 900 is 0.74, past those bounds.
    assert portion_factor(checked(565), settings) is None
    assert portion_factor(checked(900), settings) is None
    assert portion_factor(checked(580), settings) == pytest.approx(662.5 / 580)
    assert portion_factor(checked(800), settings) == pytest.approx(662.5 / 800)


@pytest.mark.parametrize(
    "recipe",
    [
        checked(None),
        checked(565, incomplete=True),
        checked(565, has_numbers=False),
        checked(0),
    ],
    ids=["kcal_missing", "kcal_incomplete", "no_numbers", "zero_kcal"],
)
def test_no_factor_when_kcal_missing_or_incomplete(recipe: CheckedRecipe) -> None:
    # A lower bound is not the recipe's energy, so the grams are not scaled by it.
    assert portion_factor(recipe, Settings()) is None


def test_other_nutrients_being_incomplete_does_not_stop_scaling() -> None:
    recipe = checked(565)
    assert recipe.nutrition is not None
    partial = recipe.nutrition.model_copy(update={"total_incomplete": {"fibre_g": ["mystery"]}})

    assert portion_factor(recipe.model_copy(update={"nutrition": partial}), Settings()) is not None


def test_no_factor_past_1000_g() -> None:
    # 441 kcal for a 795 share is a factor of 1.80, which takes 600 g to 1081 g.
    assert portion_factor(checked(441, spec=LUNCH, grams=(600, 50)), Settings()) is None
    assert portion_factor(checked(441, spec=LUNCH, grams=(500, 50)), Settings()) is not None


def test_1000_g_itself_is_allowed() -> None:
    assert portion_factor(checked(397.5, spec=LUNCH, grams=(500,)), Settings()) == 2.0
    assert portion_factor(checked(397.5, spec=LUNCH, grams=(501,)), Settings()) is None


# scale_draft


@pytest.mark.parametrize(
    ("grams", "factor", "expected"),
    [
        (1, 0.5, 1),
        (0.3, 1.0, 1),
        (113.4, 1.0, 113),
        (113.6, 1.0, 114),
        (225, 0.5, 113),
        (100, 1.17, 117),
        (500, 2.0, 1000),
    ],
    ids=["keeps_1_g", "under_1_g", "rounds_down", "rounds_up", "half_goes_up", "scales", "to_1000"],
)
def test_scale_rounds_and_keeps_1_g(grams: float, factor: float, expected: int) -> None:
    (ingredient,) = scale_draft(draft(grams), factor).ingredients

    assert ingredient.grams == expected


def test_scale_changes_only_the_grams() -> None:
    original = draft(120, 40.5, 7)

    scaled = scale_draft(original, 1.25)

    assert [i.grams for i in scaled.ingredients] == [150, 51, 9]
    assert [i.id for i in scaled.ingredients] == ["food0", "food1", "food2"]
    assert scaled.model_copy(update={"ingredients": original.ingredients}) == original
    # The draft it was given is left as it was.
    assert [i.grams for i in original.ingredients] == [120, 40.5, 7]


def test_a_factor_that_passes_1000_g_is_refused_not_clipped() -> None:
    # portion_factor never gives such a factor; a caller that made one up gets an error.
    with pytest.raises(ValidationError, match="grams"):
        scale_draft(draft(600), 2.0)
