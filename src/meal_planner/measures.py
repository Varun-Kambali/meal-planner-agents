"""The household measure shown beside an ingredient's grams, such as "185 g, 1 cup".

It is worked out here, in code, from the final grams and the units known for the food: the grams
in one of each come from nutrition-mcp (kept on the catalogue item when the catalogue loads) and
from the catalogue's own measures, in the order `Catalogue.valid_units` gives them. Nothing is
asked of nutrition-mcp or the model, and a measure never reaches a prompt.

The rule: a unit fits when its rounded amount is above 0 and comes to within 15% of the
ingredient's grams; g and ml never do. A counted unit rounds to the nearest half and any other
unit to the nearest quarter, ties going up. Of the units that fit, the measure is the one with the
most grams in one whose amount is at least a half, so 200 g of milk is 3/4 glass rather than 13
tbsp; when none reaches a half, it is the one with the fewest grams in one. Units with the same
grams in one keep the `valid_units` order. When no unit fits there is no measure, so "1 cup" never
stands for half a cup.
"""

import math
from collections.abc import Mapping
from typing import NamedTuple

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

# The least amount of a unit that is measured in it when a bigger unit fits too: a half.
_LEAST_OF_A_BIG_UNIT = 0.5

# Slack for floating point, so a measure exactly 15% off is inside the tolerance.
_EPS = 1e-9


class _Fit(NamedTuple):
    unit: str
    per_unit: float
    amount: float


def _rounded(amount: float, step: float) -> float:
    """The amount to the nearest `step`, a tie going up."""
    return math.floor(amount / step + 0.5) * step


def _fits(grams: float, unit_grams: Mapping[str, float]) -> list[_Fit]:
    """Each unit that fits `grams`, with its rounded amount, in the order of `unit_grams`."""
    found: list[_Fit] = []
    for unit, per_unit in unit_grams.items():
        if unit in _METRIC:
            continue
        step = _COUNTED_STEP if unit in COUNTED_UNITS else _OTHER_STEP
        amount = _rounded(grams / per_unit, step)
        if amount > 0 and abs(amount * per_unit - grams) <= MEASURE_TOLERANCE * grams + _EPS:
            found.append(_Fit(unit, per_unit, amount))
    return found


def household_measure(grams: float, unit_grams: Mapping[str, float]) -> HouseholdMeasure | None:
    """The measure for `grams` by the rule above, or None when no unit fits.

    `unit_grams` maps each unit to the grams in one, in `valid_units` order, which settles a tie:
    `max` and `min` keep the first of equal items.
    """
    fits = _fits(grams, unit_grams)
    if not fits:
        return None
    reaching = [fit for fit in fits if fit.amount >= _LEAST_OF_A_BIG_UNIT]
    if reaching:
        chosen = max(reaching, key=lambda fit: fit.per_unit)
    else:
        chosen = min(fits, key=lambda fit: fit.per_unit)
    return HouseholdMeasure(amount=chosen.amount, unit=chosen.unit)


def recipe_measures(draft: DraftRecipe, catalogue: Catalogue) -> list[HouseholdMeasure | None]:
    """A measure or None for each ingredient of the draft, in its order. A food the catalogue
    lacks has no units, so it has no measure."""
    return [household_measure(i.grams, catalogue.unit_grams(i.id)) for i in draft.ingredients]


def with_measures(recipe: CheckedRecipe, catalogue: Catalogue) -> CheckedRecipe:
    """The recipe with a measure worked out for each ingredient from the grams it has now."""
    return recipe.model_copy(update={"measures": recipe_measures(recipe.draft, catalogue)})
