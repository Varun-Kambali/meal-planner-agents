"""data/foods against the real nutrition-mcp server, started as a subprocess.

The server reads the committed CSV, so these tests need no USDA key and no network.
"""

import csv
from pathlib import Path

import build_foods
import pytest

from meal_planner.nutrition import NutritionClient

pytestmark = pytest.mark.anyio

FOODS = build_foods.PATHS.foods_dir


async def test_foods_load_in_nutrition_mcp() -> None:
    async with NutritionClient.start(str(FOODS)) as client:
        result = await client.nutrition("rolled_oats", 40, "g")
        # The bundled foods are still there next to the food file.
        assert (await client.nutrition("atta", 100, "g")).grams == 100

    assert result.grams == 40
    assert result.food.diet == "vegan"
    assert result.food.source_ids == ["usda_fdc_sr_legacy"]
    assert result.nutrients.kcal is not None
    assert result.nutrients.kcal > 0


async def test_liquids_take_spoon_and_cup_measures() -> None:
    async with NutritionClient.start(str(FOODS)) as client:
        oil = await client.nutrition("olive_oil", 1, "tbsp")
        oats = await client.nutrition("rolled_oats", 100, "g")

    assert {"ml", "tbsp", "tsp", "cup"} <= set(oil.food.units)
    assert 10 < oil.grams < 16
    # A dry food has no density, so it takes grams only from nutrition-mcp.
    assert oats.food.units == {"g": 1.0}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_food_file_matches_the_map() -> None:
    """A map change without a rebuild, or a hand edit to the CSV, fails here."""
    mapped = build_foods.read_map(build_foods.PATHS.food_map)
    written = read_csv(FOODS / "ingredients.csv")

    assert [(r["id"], r["name"], r["aliases"], r["diet"], r["source_record"]) for r in written] == [
        (r["id"], r["name"], r["aliases"], r["diet"], r["fdc_id"]) for r in mapped
    ]


def test_portions_review_covers_every_mapped_record() -> None:
    reviewed = {(r["id"], r["fdc_id"]) for r in read_csv(build_foods.PATHS.portions)}
    mapped = {(r["id"], r["fdc_id"]) for r in build_foods.read_map(build_foods.PATHS.food_map)}

    assert mapped <= reviewed
