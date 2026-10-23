"""The ingredients the recipe writer may use, and the facts about them that are not nutrition.

`data/catalogue.yaml` holds allergen groups, pack size, shelf life once opened and grams per
household measure. Diet and the units nutrition-mcp accepts are read from nutrition-mcp when the
file is loaded, so they have one source. Loading also checks every id against nutrition-mcp, which
turns a typo in the file into a startup failure that names the id.
"""

from collections.abc import Container, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import yaml
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

    def grams(self, food_id: str, amount: float, unit: str) -> float | None:
        """Grams in `amount` of a household unit, or None when this catalogue has no such unit.

        None means "ask nutrition-mcp": a unit it accepts for the food is passed through to it.
        Units are read as nutrition-mcp reads them: case and spaces ignored, a plural accepted.
        """
        measures = self.get(food_id).measures
        word = unit.strip().lower()
        for candidate in (word, word.removesuffix("s"), word.removesuffix("es")):
            if candidate in measures:
                return amount * measures[candidate]
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
            food = (await nutrition.nutrition(entry.id, 100, "g")).food
        except NutritionError as exc:
            if exc.food_id is None:
                # The server could not be asked: that is not a wrong id, and saying so for
                # every row would hide the real fault.
                raise
            problems.append(f"'{entry.id}' is not a food id in nutrition-mcp")
            continue
        duplicated = [unit for unit in entry.measures if unit in food.units]
        if duplicated:
            problems.append(
                f"'{entry.id}': nutrition-mcp already has {', '.join(duplicated)} for this food, "
                "so remove it from measures"
            )
            continue
        items[entry.id] = CatalogueItem(
            **entry.model_dump(), diet=food.diet, units=list(food.units)
        )
    if problems:
        raise _error(path, problems)
    return Catalogue(items)


def check_rules_against(rules: PlanRules, catalogue: Catalogue) -> list[str]:
    """One message for each excluded or liked food that is not in the catalogue."""
    food = rules.food
    return [
        f"food.{field}: '{food_id}' is not in the catalogue"
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
