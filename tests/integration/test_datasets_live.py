"""The eval datasets against the real nutrition-mcp server and the shipped catalogue.

A matched label's grams are what the reader gets for its food, amount and unit:
`Catalogue.meal_item` with nutrition-mcp's own units for the food, then nutrition-mcp's grams. A
label that asks for a unit has a unit neither of them has. A name labelled missing has no search
result. So no label is an estimate, and a change to the data that moves one fails here. The
server loads its bundled data and this project's food file, so this needs no keys and no network.
"""

from typing import Any

import pytest
from dataset_helpers import meal_rows, plans

from meal_planner.catalogue import Catalogue, check_rules_against, load_catalogue
from meal_planner.config import Settings
from meal_planner.nutrition import NutritionClient, NutritionPort
from meal_planner.reader import OPTIONS

pytestmark = pytest.mark.anyio

# How far a food in also_accept may be from the label's grams for the same amount and unit, as a
# share of them. A glass of skimmed milk is 0.4% heavier than a glass of whole milk.
ALSO_ACCEPT_GRAMS_SHARE = 0.01


async def grams_for(
    food_id: str, amount: float, unit: str, nutrition: NutritionPort, catalogue: Catalogue
) -> float | None:
    """The grams the reader gets for the item, or None when neither nutrition-mcp nor the
    catalogue has the unit for the food."""
    own_units = (await nutrition.nutrition(food_id, 100, "g")).food.units
    item = catalogue.meal_item(food_id, amount, unit, own_units)
    if item is None:
        return None
    return (await nutrition.nutrition(item.food_id, item.amount, item.unit)).grams


async def item_problems(
    item: dict[str, Any], nutrition: NutritionPort, catalogue: Catalogue
) -> list[str]:
    food_id, amount, unit = item["food_id"], item["amount"], item["unit"]
    others = item.get("also_accept", [])
    if item["status"] == "needs_amount":
        # Each food is in nutrition-mcp: asking for 100 g of one it lacks raises.
        for food in (food_id, *others):
            await nutrition.nutrition(food, 100, "g")
        return []
    if item["status"] == "unknown_unit":
        problems = [
            f"{food}: '{unit}' is a unit the reader can use"
            for food in (food_id, *others)
            if await grams_for(food, amount, unit, nutrition, catalogue) is not None
        ]
        return problems
    grams = await grams_for(food_id, amount, unit, nutrition, catalogue)
    if grams is None or grams != pytest.approx(item["grams"], abs=0.01):
        return [f"{food_id}: {amount} {unit} is {grams} g, labelled {item['grams']}"]
    problems = []
    for food in others:
        other = await grams_for(food, amount, unit, nutrition, catalogue)
        if other is None or abs(other - grams) > grams * ALSO_ACCEPT_GRAMS_SHARE:
            problems.append(f"{food} (also accepted): {amount} {unit} is {other} g, not {grams}")
    return problems


async def test_every_meal_label_is_what_nutrition_mcp_and_the_catalogue_give() -> None:
    settings = Settings()
    problems: dict[str, list[str]] = {}
    async with NutritionClient.start(settings.nutrition_data_dirs) as nutrition:
        catalogue = await load_catalogue(settings.catalogue_path, nutrition)
        for row in meal_rows():
            found: list[str] = []
            for item in row["expected"]:
                found += await item_problems(item, nutrition, catalogue)
            for name in row["missing"]:
                # The reader searches for as many options as it shows the model.
                results = (await nutrition.search(name, limit=OPTIONS)).results
                if results:
                    ids = [food.id for food in results]
                    found.append(f"'{name}' is labelled missing but the search finds {ids}")
            if found:
                problems[row["id"]] = found

    assert problems == {}


async def test_every_plan_fits_the_shipped_catalogue() -> None:
    settings = Settings()
    async with NutritionClient.start(settings.nutrition_data_dirs) as nutrition:
        catalogue = await load_catalogue(settings.catalogue_path, nutrition)

    problems: dict[str, list[str]] = {}
    for plan_id, rules in plans():
        found = [unknown.message for unknown in check_rules_against(rules, catalogue)]
        allowed = {item.id for item in catalogue.allowed_for(rules)}
        # A liked food the plan's own diet or allergens rule out would be a contradiction.
        found += [f"liked '{f}' is not allowed" for f in rules.food.liked_foods if f not in allowed]
        if found:
            problems[plan_id] = found

    assert problems == {}
