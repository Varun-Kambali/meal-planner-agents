"""The reader against the real nutrition-mcp server and the shipped catalogue.

The model is scripted, so this needs no key. The server loads its bundled data and this
project's food file, so it needs no network.
"""

import pytest
from reader_helpers import reading, said

from meal_planner.catalogue import load_catalogue
from meal_planner.config import Settings
from meal_planner.llm import FakeModel
from meal_planner.nutrition import NutritionClient
from meal_planner.reader import read_meal

pytestmark = pytest.mark.anyio


async def test_a_meal_is_read_with_the_servers_foods_units_and_the_catalogues_measures() -> None:
    text = "2 rotis, 2 eggs, a banana, 2 pav, a plate of pulao and 200gm chicken"
    # No reply for choose_food: every name here is settled without asking the model.
    model = FakeModel(
        {
            "read_meal": [
                reading(
                    said("roti", 2, "roti", "2"),
                    said("egg", 2, None, "2"),
                    said("banana", 1, "piece", "a"),
                    said("pav", 2, None, "2"),
                    said("pulao", 1, "plate", "a plate"),
                    said("chicken", 200, "gm", "200gm"),
                )
            ],
            "choose_food": [],
        }
    )
    settings = Settings()
    async with NutritionClient.start(settings.nutrition_data_dirs) as nutrition:
        catalogue = await load_catalogue(settings.catalogue_path, nutrition)

        result = await read_meal(
            text, model=model, nutrition=nutrition, catalogue=catalogue, settings=settings
        )

    roti, egg, banana, pav, pulao, chicken = result.items
    # nutrition-mcp's roti unit: 40 g.
    assert (roti.status, roti.food_id, roti.grams) == ("matched", "chapati", 80)
    # nutrition-mcp has the egg and the banana in grams only; the catalogue has the piece.
    assert (egg.status, egg.food_id, egg.grams) == ("matched", "egg", 100)
    assert (banana.status, banana.food_id, banana.grams) == ("matched", "banana", 118)
    # Nothing sources a weight for a pav, so it is not made up.
    assert (pav.status, pav.food_id, pav.valid_units) == ("unknown_unit", "pav", ["g"])
    assert (pulao.status, pulao.food_id) == ("missing", None)
    assert (chicken.status, chicken.food_id, chicken.grams) == ("matched", "chicken_breast", 200)
    assert result.missing == ["pulao"]
    assert [call[0] for call in model.calls] == ["read_meal"]

    assert result.total is not None
    parts = [item.nutrition for item in result.items if item.nutrition is not None]
    assert [item.food.id for item in result.total.items] == [p.food.id for p in parts]
    assert result.total.total.kcal == pytest.approx(
        sum(p.nutrients.kcal or 0 for p in parts), abs=0.1
    )
