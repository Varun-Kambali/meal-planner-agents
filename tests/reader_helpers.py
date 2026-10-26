"""What the meal reader's tests share: a nutrition-mcp with the foods typed meals name, a
catalogue, and the replies a model gives when it reads a meal.

The foods are the ones nutrition-mcp holds under these ids, with the names, aliases, kinds and
units its search lists (a live test checks every one against the server). The nutrients are made
up and round, so a test can add them up by hand.
"""

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from fakes import FakeNutrition
from nutrition_mcp.models import Kind
from nutrition_mcp.schemas import MealItemIn, MealResult, NutritionResult, SearchResult

from meal_planner.catalogue import Catalogue, CatalogueItem

# kcal and protein per 100 g.
PER_100G: dict[str, dict[str, float]] = {
    "chapati": {"kcal": 300.0, "protein_g": 9.0},
    "dal_tadka": {"kcal": 120.0, "protein_g": 6.0},
    "toor_dal": {"kcal": 340.0, "protein_g": 22.0},
    "moong_dal": {"kcal": 350.0, "protein_g": 24.0},
    "white_rice": {"kcal": 130.0, "protein_g": 2.5},
    "chicken_breast": {"kcal": 165.0, "protein_g": 31.0},
    "egg": {"kcal": 143.0, "protein_g": 12.5},
    "dimer_dalna": {"kcal": 130.0, "protein_g": 8.0},
    "pav": {"kcal": 270.0, "protein_g": 8.0},
    "whole_milk": {"kcal": 60.0, "protein_g": 3.2},
    "curd": {"kcal": 60.0, "protein_g": 3.1},
    "curd_rice": {"kcal": 110.0, "protein_g": 3.0},
    "idli": {"kcal": 120.0, "protein_g": 4.0},
}
NAMES = {
    "chapati": "Chapati",
    "dal_tadka": "Dal tadka",
    "toor_dal": "Toor dal (split pigeon peas)",
    "moong_dal": "Moong dal (split yellow mung beans)",
    "white_rice": "Chawal (white rice, raw)",
    "chicken_breast": "Chicken breast (skinless, boneless)",
    "egg": "Anda (egg, whole, raw)",
    "dimer_dalna": "Dimer dalna (egg curry)",
    "pav": "Pav (soft white bread roll)",
    "whole_milk": "Doodh (whole milk)",
    "curd": "Dahi (curd)",
    "curd_rice": "Curd rice",
    "idli": "Idli",
}
ALIASES = {
    "chapati": ["roti", "phulka", "fulka", "poli", "rotli", "ruti"],
    "dal_tadka": ["tadka dal", "dal fry", "arhar dal tadka"],
    "toor_dal": [
        "arhar dal",
        "tur dal",
        "tuvar dal",
        "togari bele",
        "thuvaram paruppu",
        "split pigeon peas",
        "pigeon pea dal",
    ],
    "moong_dal": [
        "mung dal",
        "yellow moong dal",
        "split moong",
        "pasi paruppu",
        "hesaru bele",
        "split mung beans",
    ],
    "white_rice": ["white rice", "raw rice", "arisi", "akki"],
    "chicken_breast": ["chicken breast", "boneless chicken breast", "chicken breast fillet"],
    "egg": ["egg", "eggs", "ande", "dim", "motte", "muttai"],
    "dimer_dalna": ["dimer dalna", "egg curry", "anda curry", "dim er dalna"],
    "pav": ["pao", "ladi pav", "soft white bread roll"],
    "whole_milk": ["whole milk", "paal", "haalu", "dudh"],
    "curd": ["curd", "whole milk yogurt", "plain whole-milk yogurt", "thayir", "mosaru", "doi"],
    "curd_rice": ["mosaranna", "thayir sadam", "dahi chawal"],
    "idli": ["idly", "idlis"],
}
UNITS = {
    "chapati": {"roti": 40.0, "piece": 40.0, "serving": 80.0},
    "dal_tadka": {"katori": 150.0, "serving": 150.0},
    "dimer_dalna": {"katori": 150.0, "serving": 300.0},
    "whole_milk": {"ml": 1.03, "tbsp": 15.47, "tsp": 5.16, "cup": 247.51, "glass": 257.83},
    "curd_rice": {"katori": 150.0, "serving": 210.0},
    "idli": {"piece": 40.0, "plate": 80.0, "serving": 120.0},
}
KINDS: dict[str, Kind] = {
    "chapati": "dish",
    "dal_tadka": "dish",
    "dimer_dalna": "dish",
    "curd_rice": "dish",
    "idli": "dish",
}

# The catalogue's own measures: the whole egg, which nutrition-mcp has only in grams.
CATALOGUE_MEASURES = {"egg": {"piece": 50.0}}


class ReaderNutrition(FakeNutrition):
    """The foods above, remembering every call as (method, what it was asked about)."""

    def __init__(self) -> None:
        super().__init__(PER_100G, units=UNITS, names=NAMES, aliases=ALIASES, kinds=KINDS)
        self.asked: list[tuple[str, str]] = []

    async def search(
        self, query: str, kind: Literal["dish", "ingredient"] | None = None, limit: int = 5
    ) -> SearchResult:
        self.asked.append(("search", query))
        return await super().search(query, kind, limit)

    async def nutrition(self, food_id: str, amount: float, unit: str) -> NutritionResult:
        self.asked.append(("nutrition", f"{food_id} {amount:g} {unit}"))
        return await super().nutrition(food_id, amount, unit)

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        self.asked.append(("meal", ", ".join(f"{i.food_id} {i.amount:g} {i.unit}" for i in items)))
        return await super().meal(items)


def catalogue_item(food_id: str, **fields: Any) -> CatalogueItem:
    return CatalogueItem.model_validate(
        {
            "id": food_id,
            "name": NAMES[food_id],
            "category": "other",
            "allergens": [],
            "pack_g": None,
            "keeps_days_opened": None,
            "diet": "veg",
            "units": ["g", *UNITS.get(food_id, {})],
            "per_100g": {"kcal": PER_100G[food_id]["kcal"]},
            **fields,
        }
    )


def reader_catalogue() -> Catalogue:
    """Egg and pav are the recipe ingredients among the foods; chapati and the rest are not."""
    return Catalogue(
        {
            "egg": catalogue_item("egg", measures=CATALOGUE_MEASURES["egg"]),
            "pav": catalogue_item("pav"),
        }
    )


def write_reader_catalogue(folder: Path) -> Path:
    """`reader_catalogue()` as a catalogue.yaml, for an app that loads it with ReaderNutrition."""
    rows = [
        {
            "id": food_id,
            "name": NAMES[food_id],
            "category": "other",
            "allergens": [],
            "pack_g": None,
            "keeps_days_opened": None,
            **({"measures": CATALOGUE_MEASURES[food_id]} if food_id in CATALOGUE_MEASURES else {}),
        }
        for food_id in ("egg", "pav")
    ]
    path = folder / "reader_catalogue.yaml"
    path.write_text(yaml.safe_dump(rows), encoding="utf-8")
    return path


# What a model says


def said(
    name: str, amount: float | None, unit: str | None, amount_text: str = ""
) -> dict[str, Any]:
    """One food as the model reads it from the text."""
    return {"name": name, "amount": amount, "unit": unit, "amount_text": amount_text}


def reading(*items: dict[str, Any]) -> str:
    """The raw reply to the reading of a meal."""
    return json.dumps({"items": list(items)})


def choosing(*options: int | None) -> str:
    """The raw reply to the choice of a food for each name the search left open: the option
    picked for each name in the order asked, or None for none of them."""
    picks = [{"food": food, "option": option} for food, option in enumerate(options)]
    return json.dumps({"picks": picks})
