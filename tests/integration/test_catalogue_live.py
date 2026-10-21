"""The shipped catalogue against the real nutrition-mcp server, started as a subprocess.

The server loads its bundled data, so this needs no keys and no network.
"""

import pytest

from meal_planner.catalogue import load_catalogue
from meal_planner.config import Settings
from meal_planner.nutrition import NutritionClient

pytestmark = pytest.mark.anyio


async def test_every_catalogue_id_resolves() -> None:
    async with NutritionClient.start("") as client:
        catalogue = await load_catalogue(Settings().catalogue_path, client)

    assert catalogue.items
    for item in catalogue.items.values():
        assert "g" in item.units, item.id
    # Spot checks that diet and the household measures come through from the two sources.
    assert catalogue.get("egg").diet == "egg"
    assert catalogue.get("rohu").diet == "non_veg"
    assert catalogue.grams("egg", 2, "piece") == 100.0
