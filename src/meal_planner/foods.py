"""Foods for the screen that changes a wrongly matched food: what a name finds, and the numbers
of a meal as it is edited.

Both are nutrition-mcp's answers read through the catalogue, with no model call and no number
made here. A search offers each food with the units an amount can use for it, the units the
meal reader accepts (`Catalogue.valid_units`); a meal is worked out by the function the summary
uses (`summary.meal_of`), so an item has the same numbers here as in the day view.
"""

from nutrition_mcp.models import Kind
from nutrition_mcp.search import MAX_SEARCH_LIMIT
from pydantic import BaseModel

from meal_planner.catalogue import Catalogue
from meal_planner.nutrition import NutritionError, NutritionPort, nutrition_unusable
from meal_planner.reader import OPTIONS

# A search offers as many foods as the reader chooses between, unless asked for more, and for no
# more than nutrition-mcp returns.
DEFAULT_LIMIT = OPTIONS
MAX_LIMIT = MAX_SEARCH_LIMIT


class FoodOption(BaseModel):
    """A food a name found, and the units an amount of it can be given in."""

    id: str
    name: str
    kind: Kind
    # nutrition-mcp's units for the food, then the catalogue's measures for it: the units the
    # reader lists for a food, and the ones it and the summary accept.
    units: list[str]


async def food_options(
    query: str, limit: int, *, nutrition: NutritionPort, catalogue: Catalogue
) -> list[FoodOption]:
    """The foods nutrition-mcp finds for `query`, best match first; none when it has none.

    The caller has checked the query (`problems.food_query_problems`) and the limit. Raises
    NutritionUnusable when nutrition-mcp cannot be used; its message holds nothing of the query.
    """
    try:
        found = await nutrition.search(query, limit=limit)
    except NutritionError as exc:
        raise nutrition_unusable(exc) from exc
    return [
        FoodOption(
            id=food.id,
            name=food.name,
            kind=food.kind,
            units=catalogue.valid_units(food.id, food.units),
        )
        for food in found.results
    ]
