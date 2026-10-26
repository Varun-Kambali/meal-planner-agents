"""The shipped catalogue against the real nutrition-mcp server, started as a subprocess.

The server loads its bundled data and this project's food file, so this needs no keys and no
network.
"""

import pytest
from nutrition_mcp.schemas import MealItemIn

from meal_planner.catalogue import load_catalogue
from meal_planner.config import Settings
from meal_planner.nutrition import NutritionClient

pytestmark = pytest.mark.anyio


async def test_every_catalogue_id_resolves() -> None:
    settings = Settings()
    async with NutritionClient.start(settings.nutrition_data_dirs) as client:
        catalogue = await load_catalogue(settings.catalogue_path, client)

    assert catalogue.items
    for item in catalogue.items.values():
        assert "g" in item.units, item.id
    # Spot checks that diet and the household measures come through from the two sources, for
    # a bundled food and for foods from the food file.
    assert catalogue.get("egg").diet == "egg"
    assert catalogue.get("rohu").diet == "non_veg"
    assert catalogue.meal_item("egg", 2, "piece") == MealItemIn(food_id="egg", amount=100, unit="g")
    assert catalogue.get("rolled_oats").diet == "vegan"
    assert catalogue.get("chicken_breast").diet == "non_veg"
    assert catalogue.get("honey").diet == "veg"
    assert catalogue.get("kimchi").diet == "non_veg"
    banana = MealItemIn(food_id="banana", amount=118, unit="g")
    assert catalogue.meal_item("banana", 1, "piece") == banana
    # A liquid's spoons come from nutrition-mcp, not from the catalogue.
    assert "tbsp" in catalogue.get("olive_oil").units
    olive_oil = MealItemIn(food_id="olive_oil", amount=1, unit="tbsp")
    assert catalogue.meal_item("olive_oil", 1, "tbsp") == olive_oil
