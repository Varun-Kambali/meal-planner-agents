"""What the model writes for a slot, and what the checks make of each recipe, day and week."""

from typing import Annotated

from nutrition_mcp.schemas import MealResult
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from meal_planner.rules import CookWhen, DayKey, Gear, SlotSpec

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Cuisine = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Step = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftIngredient(_Model):
    """One catalogue ingredient and how much of it, in grams."""

    id: str
    # The bound stops a model that writes 5000 g, or 0 g, from passing as a real recipe. It is
    # not a nutrition rule: calories and protein are checked on the whole recipe.
    grams: float = Field(gt=0, le=1000)


class DraftRecipe(_Model):
    """A recipe as the model writes it. It carries no nutrition numbers: those come from code."""

    day: DayKey
    title: Title
    cuisine: Cuisine
    ingredients: list[DraftIngredient] = Field(min_length=1, max_length=15)
    steps: list[Step] = Field(min_length=1, max_length=10)
    active_min: int
    total_min: int
    gear: list[Gear]
    cook_when: CookWhen
    store: str
    swap: str
    fits: str


class SlotDraft(_Model):
    """One model reply: the recipes it wrote for one slot, one per day it was asked about."""

    slot_id: str
    recipes: list[DraftRecipe]


class Failure(_Model):
    """A rule a recipe or day misses, in words the writer can act on."""

    check: str
    message: str


class CheckedRecipe(_Model):
    """A draft with its numbers from nutrition-mcp and the checks it has failed so far."""

    spec: SlotSpec
    draft: DraftRecipe
    # None when nutrition-mcp could not compute the recipe (for example an unknown id).
    nutrition: MealResult | None
    failures: list[Failure]
    attempts: int
    # The factor code applied to the model's grams for this attempt; 1.0 when it did not scale.
    # The draft holds the grams after scaling.
    portion_scale: float = 1.0


class DayReport(_Model):
    """A day's totals against its day type. Keys are nutrition-mcp's nutrient names."""

    day: DayKey
    totals: dict[str, float | None]
    failures: list[Failure]
    # Carbohydrate and fat as a fraction of their target over (+) or under (-), such as 0.3 for 30%
    # over. They are reported and never fail a day. A nutrient whose total is not fully known has
    # no entry, because a deviation worked out from part of a total would be wrong.
    soft: dict[str, float]
