"""The reader against the real nutrition-mcp server and the shipped catalogue.

The model is scripted, so this needs no key. The server loads its bundled data and this
project's food file, so it needs no network.
"""

import datetime

import pytest
from reader_helpers import reading, said
from week_helpers import plan_rules

from meal_planner.catalogue import Catalogue, load_catalogue
from meal_planner.config import Settings
from meal_planner.llm import FakeModel
from meal_planner.nutrition import NutritionClient, NutritionPort
from meal_planner.reader import ReadResult, read_meal
from meal_planner.summary import LogEntry, LogItem, MealLog, summarise

pytestmark = pytest.mark.anyio

TEXT = "2 rotis, 2 eggs, a banana, 2 pav, a plate of pulao and 200gm chicken"


async def read_the_meal(
    nutrition: NutritionPort, catalogue: Catalogue, settings: Settings
) -> tuple[ReadResult, FakeModel]:
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
    result = await read_meal(
        TEXT, model=model, nutrition=nutrition, catalogue=catalogue, settings=settings
    )
    return result, model


async def test_a_meal_is_read_with_the_servers_foods_units_and_the_catalogues_measures() -> None:
    settings = Settings()
    async with NutritionClient.start(settings.nutrition_data_dirs) as nutrition:
        catalogue = await load_catalogue(settings.catalogue_path, nutrition)

        result, model = await read_the_meal(nutrition, catalogue, settings)

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


async def test_what_the_reader_matched_is_a_log_the_summary_accepts() -> None:
    """The items the person confirms are logged as the reader returned them, catalogue measures
    and all, and the summary works them out to the reader's total."""
    settings = Settings()
    async with NutritionClient.start(settings.nutrition_data_dirs) as nutrition:
        catalogue = await load_catalogue(settings.catalogue_path, nutrition)
        result, _ = await read_the_meal(nutrition, catalogue, settings)
        matched = [item for item in result.items if item.status == "matched"]
        items = [
            LogItem(food_id=item.food_id or "", amount=item.amount or 0, unit=item.unit or "")
            for item in matched
        ]
        log = MealLog(
            version=1,
            entries=[
                LogEntry(
                    date=datetime.date(2026, 10, 26),
                    slot_id="lunch",
                    status="swapped",
                    items=items,
                )
            ],
        )

        summary = await summarise(
            plan_rules(), log, nutrition=nutrition, catalogue=catalogue, settings=settings
        )

    # The egg and the banana are logged in pieces, which nutrition-mcp has only from the catalogue.
    assert [(i.food_id, i.amount, i.unit) for i in items] == [
        ("chapati", 2, "roti"),
        ("egg", 2, "piece"),
        ("banana", 1, "piece"),
        ("chicken_breast", 200, "gm"),
    ]
    assert result.total is not None
    (day,) = summary.days
    assert day.totals["kcal"] == pytest.approx(result.total.total.kcal, abs=0.1)
    assert day.totals["protein_g"] == pytest.approx(result.total.total.protein_g, abs=0.1)
