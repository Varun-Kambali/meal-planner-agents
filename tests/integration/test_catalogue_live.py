"""The shipped catalogue against the real nutrition-mcp server, started as a subprocess.

The server loads its bundled data and this project's food file, so this needs no keys and no
network.
"""

import pytest

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
    assert catalogue.grams("egg", 2, "piece") == 100.0
    assert catalogue.get("rolled_oats").diet == "vegan"
    assert catalogue.get("chicken_breast").diet == "non_veg"
    assert catalogue.get("honey").diet == "veg"
    assert catalogue.get("kimchi").diet == "non_veg"
    assert catalogue.grams("banana", 1, "piece") == 118.0
    # A liquid's spoons come from nutrition-mcp, not from the catalogue.
    assert "tbsp" in catalogue.get("olive_oil").units
    assert catalogue.grams("olive_oil", 1, "tbsp") is None
