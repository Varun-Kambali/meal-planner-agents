"""Fitting a recipe's grams to its slot's calories, in code.

The writer knows no ingredient's energy, so it guesses grams and often lands outside the slot's
band. Scaling every gram by one factor keeps the dish the writer chose and moves its calories to
the slot's share. The arithmetic here is on grams only: the scaled recipe goes back to
nutrition-mcp for its numbers, and no nutrient is ever multiplied in code.
"""

import math

from meal_planner.config import Settings
from meal_planner.recipes import CheckedRecipe, DraftIngredient, DraftRecipe

# Slack for floating point, so a recipe exactly on the edge of the band is inside it, as in
# check_slot_targets.
_EPS = 1e-9

# The most one ingredient may weigh, as DraftIngredient allows.
_MAX_GRAMS = 1000


def _scaled_grams(grams: float, factor: float) -> int:
    """Whole grams, halves rounded up, and never less than 1 g."""
    return max(1, math.floor(grams * factor + 0.5))


def portion_factor(recipe: CheckedRecipe, s: Settings) -> float | None:
    """The factor that brings the recipe's calories to its slot's share, or None to leave it.

    None when the recipe has no numbers, when its calories are not fully known (a lower bound is
    not its energy), when they are already inside the slot's band, when the factor is outside
    `portion_scale_min` and `portion_scale_max`, or when an ingredient would pass 1000 g.
    """
    if recipe.nutrition is None:
        return None
    kcal = recipe.nutrition.total.get("kcal")
    if kcal is None or kcal <= 0 or "kcal" in recipe.nutrition.total_incomplete:
        return None
    target = recipe.spec.kcal
    tolerance = s.recipe_kcal_tolerance
    if target * (1 - tolerance) - _EPS <= kcal <= target * (1 + tolerance) + _EPS:
        return None
    factor = target / kcal
    if not s.portion_scale_min <= factor <= s.portion_scale_max:
        return None
    if any(_scaled_grams(i.grams, factor) > _MAX_GRAMS for i in recipe.draft.ingredients):
        return None
    return factor


def scale_draft(draft: DraftRecipe, factor: float) -> DraftRecipe:
    """The draft with every gram times `factor`, in whole grams. Nothing else changes.

    Take the factor from `portion_factor`, which guarantees no ingredient passes 1000 g. A factor
    from anywhere else can ask for more than a DraftIngredient allows, and that raises.
    """
    ingredients = [
        DraftIngredient(id=i.id, grams=_scaled_grams(i.grams, factor)) for i in draft.ingredients
    ]
    return draft.model_copy(update={"ingredients": ingredients})
