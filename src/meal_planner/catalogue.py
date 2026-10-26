"""The ingredients the recipe writer may use, and the facts about them that are not nutrition.

`data/catalogue.yaml` holds allergen groups, pack size, shelf life once opened and grams per
household measure. Diet, the units nutrition-mcp accepts and the nutrients per 100 g are read
from nutrition-mcp when the file is loaded, so they have one source. Loading also checks every
id against nutrition-mcp, which turns a typo in the file into a startup failure that names the id.
"""

import sys
from collections.abc import Container, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, NamedTuple

import yaml
from nutrition_mcp.schemas import MealItemIn
from nutrition_mcp.units import normalise_unit
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
from pydantic_core import ErrorDetails

from meal_planner.nutrition import NutritionError, NutritionPort
from meal_planner.rules import Allergen, Diet, Name, PlanRules, Slug

Category = Literal[
    "grain",
    "pulse",
    "dairy",
    "egg",
    "meat",
    "fish",
    "veg",
    "fruit",
    "nut_seed",
    "oil",
    "spice",
    "sauce",
    "sweetener",
    "other",
]

# Each diet, with the diets of the foods it may eat.
_DIETS_ALLOWED: dict[Diet, frozenset[Diet]] = {
    "vegan": frozenset({"vegan"}),
    "veg": frozenset({"veg", "vegan"}),
    "egg": frozenset({"egg", "veg", "vegan"}),
    "non_veg": frozenset({"non_veg", "egg", "veg", "vegan"}),
}

Grams = Annotated[float, Field(gt=0, allow_inf_nan=False)]
# A household unit is one lowercase word, so it reads the way nutrition-mcp reads units.
Unit = Annotated[str, StringConstraints(pattern=r"^[a-z]+$")]


class CatalogueError(Exception):
    """The catalogue file or an id in it is wrong. The message names the id."""


class CatalogueEntry(BaseModel):
    """One row of `data/catalogue.yaml`."""

    model_config = ConfigDict(extra="forbid")

    id: Slug
    name: Name
    # The shop aisle.
    category: Category
    allergens: list[Allergen]
    # Both are written out, with null for what a recipe uses by the pinch, so a row that forgot
    # them fails instead of passing as "no pack".
    pack_g: Grams | None
    keeps_days_opened: Annotated[int, Field(ge=1)] | None
    # Grams in one household unit, only for units nutrition-mcp does not have for the food.
    measures: dict[Unit, Grams] = {}

    @field_validator("measures")
    @classmethod
    def _grams_are_not_a_measure(cls, measures: dict[str, float]) -> dict[str, float]:
        if "g" in measures:
            raise ValueError("g is the gram itself; a measure is a household unit such as piece")
        return measures


class CatalogueItem(CatalogueEntry):
    """A row plus what nutrition-mcp says about the food, filled in when the catalogue loads."""

    diet: Diet
    # Every unit nutrition-mcp accepts for the food, "g" first.
    units: list[str]
    # The food's 16 nutrients for 100 g, by nutrition-mcp's key. None where it has no value.
    per_100g: dict[str, float | None]


@dataclass(frozen=True)
class Catalogue:
    items: dict[str, CatalogueItem]

    def get(self, food_id: str) -> CatalogueItem:
        item = self.items.get(food_id)
        if item is None:
            raise CatalogueError(f"'{food_id}' is not in the catalogue")
        return item

    def allowed(
        self,
        diet: Diet,
        exclude_allergens: Iterable[Allergen] = (),
        exclude_foods: Container[str] = (),
    ) -> list[CatalogueItem]:
        """The items a diet allows, less the allergens and foods named, in catalogue order."""
        diets = _DIETS_ALLOWED[diet]
        avoided = set(exclude_allergens)
        return [
            item
            for item in self.items.values()
            if item.diet in diets
            and avoided.isdisjoint(item.allergens)
            and item.id not in exclude_foods
        ]

    def allowed_for(self, rules: PlanRules) -> list[CatalogueItem]:
        """The items the diet, the allergens and the excluded foods leave, in catalogue order."""
        food = rules.food
        return self.allowed(food.diet, food.exclude_allergens, food.exclude_foods)

    def meal_item(
        self, food_id: str, amount: float, unit: str, own_units: Container[str] | None = None
    ) -> MealItemIn | None:
        """What to ask nutrition-mcp about for `amount` of `unit` of a food, or None when neither
        nutrition-mcp nor the catalogue has the unit for it.

        nutrition-mcp's own unit for the food comes first and is sent by its own name. Otherwise
        the catalogue's measure for the food turns the amount into grams. `own_units` are the
        units nutrition-mcp has for the food; when they are not given, the catalogue's copy of
        them is used, and a food the catalogue lacks goes to nutrition-mcp as it is, to be read or
        refused there. The reader and the summary both send units through here, so a meal the
        reader matched is one the summary accepts.
        """
        row = self.items.get(food_id)
        own = own_units if own_units is not None else (row.units if row else None)
        if own is None:
            return MealItemIn(food_id=food_id, amount=amount, unit=unit)
        name = find_unit(own, unit)
        if name is not None:
            return MealItemIn(food_id=food_id, amount=amount, unit=name)
        measure = find_unit(row.measures, unit) if row else None
        if row is None or measure is None:
            return None
        # Kept a number when the product is too large for a float, so that nutrition-mcp refuses
        # it as over its limit, as it does any large amount.
        grams = min(amount * row.measures[measure], sys.float_info.max)
        return MealItemIn(food_id=food_id, amount=grams, unit="g")


def find_unit(units: Container[str], unit: str) -> str | None:
    """The unit in `units` that `unit` names, read the way nutrition-mcp reads units, or None.

    Case and spaces are ignored and gram spellings are g (nutrition-mcp's own `normalise_unit`),
    then a plural is accepted. The loop is a copy of the private `_find_unit` in nutrition-mcp's
    units.py.
    """
    word = normalise_unit(unit)
    for candidate in (word, word.removesuffix("s"), word.removesuffix("es")):
        if candidate in units:
            return candidate
    return None


async def load_catalogue(path: Path, nutrition: NutritionPort) -> Catalogue:
    """Read the file and check each id against nutrition-mcp, which supplies diet and units.

    Reports every problem it finds in one CatalogueError, so one run shows all of them.
    """
    entries, problems = _read_entries(path)
    items: dict[str, CatalogueItem] = {}
    for entry in entries:
        try:
            # 100 g is a valid amount for every food, so a refusal here means the id is unknown.
            per_100g = await nutrition.nutrition(entry.id, 100, "g")
        except NutritionError as exc:
            if exc.unreachable or exc.food_id is None:
                # The server could not be asked, or its answer was not about this food: that is
                # not a wrong id, and saying so for every row would hide the real fault.
                raise
            problems.append(f"'{entry.id}' is not a food id in nutrition-mcp")
            continue
        food = per_100g.food
        duplicated = [unit for unit in entry.measures if unit in food.units]
        if duplicated:
            problems.append(
                f"'{entry.id}': nutrition-mcp already has {', '.join(duplicated)} for this food, "
                "so remove it from measures"
            )
            continue
        items[entry.id] = CatalogueItem(
            **entry.model_dump(),
            diet=food.diet,
            units=list(food.units),
            per_100g=dict(per_100g.nutrients),
        )
    if problems:
        raise _error(path, problems)
    return Catalogue(items)


class UnknownFood(NamedTuple):
    """A food the plan names that the catalogue lacks, and where in the rules it is named."""

    # The path to the list of foods, such as ("food", "liked_foods").
    loc: tuple[str, ...]
    message: str


def check_rules_against(rules: PlanRules, catalogue: Catalogue) -> list[UnknownFood]:
    """One entry for each excluded or liked food that is not in the catalogue."""
    food = rules.food
    return [
        UnknownFood(("food", field), f"'{food_id}' is not in the catalogue")
        for field, food_ids in (
            ("exclude_foods", food.exclude_foods),
            ("liked_foods", food.liked_foods),
        )
        for food_id in food_ids
        if food_id not in catalogue.items
    ]


def _read_entries(path: Path) -> tuple[list[CatalogueEntry], list[str]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise _error(path, [f"cannot be read: {err.strerror or err}"]) from err
    except yaml.YAMLError as err:
        raise _error(path, [f"is not valid YAML: {err}"]) from err
    if not isinstance(raw, list):
        raise _error(path, ["must be a list of ingredients, one entry each"])

    entries: list[CatalogueEntry] = []
    problems: list[str] = []
    for position, row in enumerate(raw, start=1):
        try:
            entries.append(CatalogueEntry.model_validate(row))
        except ValidationError as err:
            problems.append(f"{_label(row, position)}: {_fields(err.errors())}")
    seen: set[str] = set()
    for entry in entries:
        if entry.id in seen:
            problems.append(f"'{entry.id}' is listed more than once")
        seen.add(entry.id)
    return entries, problems


def _label(row: object, position: int) -> str:
    """How to point at a row: by its id when it has a readable one, else by its place."""
    if isinstance(row, dict) and isinstance(row.get("id"), str):
        return f"'{row['id']}'"
    return f"entry {position}"


def _fields(errors: list[ErrorDetails]) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc']) or 'row'}: {e['msg']}" for e in errors
    )


def _error(path: Path, problems: list[str]) -> CatalogueError:
    return CatalogueError(f"{path}: " + "; ".join(problems))
