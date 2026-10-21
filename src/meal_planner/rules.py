"""The plan rules a user enters, and the expansion of a week into day-and-slot targets."""

import math
from datetime import time
from typing import Annotated, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

DayKey = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
Diet = Literal["veg", "egg", "non_veg", "vegan"]
Allergen = Literal[
    "gluten",
    "milk",
    "egg",
    "peanut",
    "tree_nuts",
    "soy",
    "fish",
    "crustaceans",
    "sesame",
    "mustard",
]
Gear = Literal[
    "air_fryer",
    "pressure_cooker",
    "microwave",
    "oven",
    "stovetop_pan",
    "kadhai",
    "blender",
    "jar_with_lid",
    "rice_cooker",
    "steamer",
    "kettle",
]
CookWhen = Literal["fresh", "evening_before", "night_before"]
# The keys nutrition-mcp reports, in its order.
NutrientKey = Literal[
    "kcal",
    "protein_g",
    "fat_g",
    "sat_fat_g",
    "carbs_available_g",
    "sugars_g",
    "fibre_g",
    "sodium_mg",
    "iron_mg",
    "calcium_mg",
    "zinc_mg",
    "folate_ug",
    "vit_a_ug",
    "vit_b12_ug",
    "vit_c_mg",
    "vit_d_ug",
]

# The order of the week. A dict from the user may list its days in any order.
DAYS: tuple[DayKey, ...] = get_args(DayKey)

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,29}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]

# Slot shares are typed by hand, so they get a small margin to add up to a whole day.
SHARE_SUM_TOLERANCE = 0.001


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NutrientTarget(_Model):
    """A daily target for one nutrient: at least `value` (min) or at most `value` (max)."""

    nutrient: NutrientKey
    kind: Literal["min", "max"]
    value: float = Field(gt=0)


class DayType(_Model):
    """One kind of day, such as a training day, with the daily targets it is held to."""

    id: Slug
    name: Name
    kcal: float = Field(gt=0)
    protein_g: float = Field(gt=0)
    carbs_g: float = Field(gt=0)
    fat_g: float = Field(gt=0)
    sat_fat_g_max: float = Field(gt=0)
    fibre_g_min: float = Field(gt=0)
    extra: list[NutrientTarget] = []


class Slot(_Model):
    """One meal of the day, with its share of the day's calories and protein."""

    id: Slug
    name: Name
    eat_at: time
    kcal_share: float = Field(gt=0, le=1)
    protein_share: float = Field(ge=0, le=1)
    style: str = Field(min_length=1, max_length=80)
    cook_when: CookWhen
    max_active_min: int = Field(ge=1, le=240)
    max_total_min: int = Field(ge=1, le=1440)


class FoodRules(_Model):
    """Diet, allergens and foods to avoid or favour, and the cuisines to draw on."""

    diet: Diet
    exclude_allergens: list[Allergen]
    # Catalogue ids. Whether they exist is checked where the catalogue is loaded.
    exclude_foods: list[str]
    liked_foods: list[str]
    cuisines: list[Label] = Field(min_length=1, max_length=10)


class PlanRules(_Model):
    """Everything a user enters about the plan they follow."""

    version: Literal[1]
    name: Name
    day_types: list[DayType] = Field(min_length=1, max_length=4)
    # Which day type each day of the week is, by day type id. The schema says all seven days
    # are needed (seven distinct keys from the seven allowed); the validator names the missing ones.
    week: dict[DayKey, str] = Field(json_schema_extra={"minProperties": 7})
    slots: list[Slot] = Field(min_length=1, max_length=8)
    gear: list[Gear] = Field(min_length=1)
    food: FoodRules
    shopping_days: list[DayKey] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def _day_types_and_week_agree(self) -> Self:
        ids = [d.id for d in self.day_types]
        _require_unique(ids, "day_types")
        missing = [day for day in DAYS if day not in self.week]
        if missing:
            raise ValueError(f"week: must name all seven days, missing: {', '.join(missing)}")
        unknown = [f"{day}: '{self.week[day]}'" for day in DAYS if self.week[day] not in ids]
        if unknown:
            raise ValueError(
                f"week: day type not defined in day_types ({', '.join(unknown)}); "
                f"day_types has: {', '.join(ids)}"
            )
        return self

    @model_validator(mode="after")
    def _slots_add_up(self) -> Self:
        _require_unique([s.id for s in self.slots], "slots")
        _require_shares_add_up([s.kcal_share for s in self.slots], "kcal_share")
        _require_shares_add_up([s.protein_share for s in self.slots], "protein_share")
        return self


def _require_unique(ids: list[str], field: str) -> None:
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ValueError(f"{field}: ids must be unique, repeated: {', '.join(repeated)}")


def _require_shares_add_up(shares: list[float], field: str) -> None:
    total = sum(shares)
    if not math.isclose(total, 1.0, abs_tol=SHARE_SUM_TOLERANCE):
        raise ValueError(f"slots: {field} values must add up to 1, they add up to {total:.4g}")


class SlotSpec(_Model):
    """One slot on one day, with the calories and protein its share of that day type comes to."""

    day: DayKey
    day_type: str
    slot: Slot
    kcal: float
    protein_g: float


def expand(rules: PlanRules) -> list[SlotSpec]:
    day_types = {d.id: d for d in rules.day_types}
    specs: list[SlotSpec] = []
    for day in DAYS:
        day_type = day_types[rules.week[day]]
        specs.extend(
            SlotSpec(
                day=day,
                day_type=day_type.id,
                slot=slot,
                kcal=day_type.kcal * slot.kcal_share,
                protein_g=day_type.protein_g * slot.protein_share,
            )
            for slot in rules.slots
        )
    return specs
