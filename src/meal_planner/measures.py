"""The household measure shown beside an ingredient's grams, such as "185 g, 1 cup".

It is worked out here, in code, from the final grams and the units known for the food: the grams
in one of each come from nutrition-mcp (kept on the catalogue item when the catalogue loads) and
from the catalogue's own measures, in the order `Catalogue.valid_units` gives them. Nothing is
asked of nutrition-mcp or the model, and a measure never reaches a prompt.

The rule: the measure is the first of those units, g and ml left out, whose rounded amount is
above 0 and comes to within 15% of the ingredient's grams. A counted unit rounds to the nearest
half and any other unit to the nearest quarter, ties going up. When no unit fits there is no
measure, so "1 cup" never stands for half a cup.
"""

import math
from collections.abc import Mapping

from meal_planner.catalogue import Catalogue
from meal_planner.recipes import CheckedRecipe, DraftRecipe, HouseholdMeasure

# How far the rounded measure, in grams, may be from the ingredient's grams, as a part of them.
MEASURE_TOLERANCE = 0.15

# Metric units: the grams are already shown, so neither is a household measure.
_METRIC = frozenset({"g", "ml"})

# Units counted out one by one, which round to the nearest half. These are the piece-like units
# in nutrition-mcp's data (piece, roti) and in data/catalogue.yaml (piece, clove, leaf, slice).
# Every other unit there is spooned, poured or served (tsp, tbsp, cup, glass, scoop, katori,
# plate, serving) and rounds to the nearest quarter.
COUNTED_UNITS = frozenset({"piece", "roti", "clove", "leaf", "slice"})
_COUNTED_STEP = 0.5
_OTHER_STEP = 0.25

# Slack for floating point, so a measure exactly 15% off is inside the tolerance.
_EPS = 1e-9


def _rounded(amount: float, step: float) -> float:
    """The amount to the nearest `step`, a tie going up."""
    return math.floor(amount / step + 0.5) * step


def household_measure(grams: float, unit_grams: Mapping[str, float]) -> HouseholdMeasure | None:
    """The first unit in `unit_grams` (unit to grams in one, in the order to try) that fits
    `grams` by the rule above, with its rounded amount, or None when none fits."""
    for unit, per_unit in unit_grams.items():
        if unit in _METRIC:
            continue
        step = _COUNTED_STEP if unit in COUNTED_UNITS else _OTHER_STEP
        amount = _rounded(grams / per_unit, step)
        if amount > 0 and abs(amount * per_unit - grams) <= MEASURE_TOLERANCE * grams + _EPS:
            return HouseholdMeasure(amount=amount, unit=unit)
    return None


def recipe_measures(draft: DraftRecipe, catalogue: Catalogue) -> list[HouseholdMeasure | None]:
    """A measure or None for each ingredient of the draft, in its order. A food the catalogue
    lacks has no units, so it has no measure."""
    return [household_measure(i.grams, catalogue.unit_grams(i.id)) for i in draft.ingredients]


def with_measures(recipe: CheckedRecipe, catalogue: Catalogue) -> CheckedRecipe:
    """The recipe with a measure worked out for each ingredient from the grams it has now."""
    return recipe.model_copy(update={"measures": recipe_measures(recipe.draft, catalogue)})
