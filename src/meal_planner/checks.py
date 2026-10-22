"""The rules a recipe, a day and a week are held to, as plain functions.

Nothing here calls a model or nutrition-mcp: each function takes numbers that are already
computed and returns `Failure`s whose messages say what to change. A passing check returns an
empty list.

Two conventions run through the nutrition checks:

- A nutrient that is `None` is not known, and is never read as 0.
- nutrition-mcp reports a total as a lower bound when some food in it has no value for the
  nutrient (`total_incomplete`). Below a minimum, a lower bound fails with "part not known":
  the missing part might have made up the gap, but it cannot be counted. Above a minimum it
  passes, since adding food only raises it. Against a maximum it cannot pass, because the missing
  part might push it over: it fails as "not known", or as over the maximum if the known part
  already is.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence

from nutrition_mcp.models import DIET_SUITABLE_FOR, Nutrients
from nutrition_mcp.nutrients import NUTRIENT_KEYS
from nutrition_mcp.nutrition import sum_nutrients
from nutrition_mcp.schemas import DECIMALS, MealResult

from meal_planner.catalogue import Catalogue
from meal_planner.config import Settings
from meal_planner.recipes import CheckedRecipe, DayReport, DraftRecipe, Failure
from meal_planner.rules import DAYS, DayKey, DayType, PlanRules, Slot, SlotSpec

# Slack for floating point, so a value exactly on a limit passes whatever the last bit of
# `target * tolerance` is. nutrition-mcp reports two decimals, far coarser than this.
_EPS = 1e-9

# What the messages call each nutrient nutrition-mcp reports, and its unit.
_LABELS: dict[str, tuple[str, str]] = {
    "kcal": ("calories", "kcal"),
    "protein_g": ("protein", "g"),
    "fat_g": ("fat", "g"),
    "sat_fat_g": ("saturated fat", "g"),
    "carbs_available_g": ("carbohydrate", "g"),
    "sugars_g": ("sugars", "g"),
    "fibre_g": ("fibre", "g"),
    "sodium_mg": ("sodium", "mg"),
    "iron_mg": ("iron", "mg"),
    "calcium_mg": ("calcium", "mg"),
    "zinc_mg": ("zinc", "mg"),
    "folate_ug": ("folate", "ug"),
    "vit_a_ug": ("vitamin A", "ug"),
    "vit_b12_ug": ("vitamin B12", "ug"),
    "vit_c_mg": ("vitamin C", "mg"),
    "vit_d_ug": ("vitamin D", "ug"),
}

# Rounding for messages. Messages show whole numbers, rounded so a miss never reads as a pass
# ("550 kcal, at most 550") and so the number the writer is asked to reach is one it can aim at.


def _up(x: float) -> int:
    return math.ceil(x - _EPS)


def _down(x: float) -> int:
    return math.floor(x + _EPS)


def _n(x: float) -> str:
    return f"{x:.0f}"


def _need(floor: float | None, cap: float | None, unit: str) -> str:
    if floor is not None and cap is not None:
        return f"needs {_up(floor)} to {_down(cap)} {unit}"
    if floor is not None:
        return f"needs at least {_up(floor)} {unit}"
    assert cap is not None
    return f"allows at most {_down(cap)} {unit}"


def _verdict(
    scope: str,
    key: str,
    value: float | None,
    missing: list[str] | None,
    *,
    floor: float | None = None,
    cap: float | None = None,
    note: str = "",
) -> Failure | None:
    """Whether `value` meets its floor and cap, as a failure that says how it does not.

    `scope` is "slot" or "day". `missing` names the foods with no value for the nutrient, and
    is None when the value is complete; when it is not None, `value` is a lower bound.
    """
    name, unit = _LABELS[key]
    lack = f" (no value for {', '.join(missing)})" if missing else ""
    partial = missing is not None
    message: str | None = None
    if value is None:
        message = f"{name} not known{lack}, {scope} {_need(floor, cap, unit)}"
    elif floor is not None and value < floor - _EPS:
        lead = f"{name} {_down(value)} {unit}"
        if partial:
            lead = f"{name} at least {_down(value)} {unit}, part not known{lack}"
        message = f"{lead}, {scope} needs at least {_up(floor)} {unit}"
    elif cap is not None and value > cap + _EPS:
        lead = f"{name} {_up(value)} {unit}"
        if partial:
            lead = f"{name} at least {_up(value)} {unit}, part not known{lack}"
        message = f"{lead}, {scope} allows at most {_down(cap)} {unit}"
    elif cap is not None and partial:
        message = (
            f"{name} not known{lack}; the part known is {_n(value)} {unit} "
            f"and {scope} allows at most {_down(cap)} {unit}"
        )
    if message is None:
        return None
    return Failure(check=f"{scope}_{key}", message=message + note)


def _kcal_note(target: float, tolerance: float) -> str:
    return f" ({_n(target)} kcal target, within {tolerance * 100:g}%)"


# Recipe checks


def check_catalogue(draft: DraftRecipe, catalogue: Catalogue, rules: PlanRules) -> list[Failure]:
    """Every ingredient must be in the catalogue and allowed by the plan's food rules.

    An ingredient that breaks several rules gets one failure for each, so one repair can fix
    them all. An id written twice is reported once.
    """
    food = rules.food
    suits = DIET_SUITABLE_FOR[food.diet]
    failures: list[Failure] = []
    for food_id in dict.fromkeys(i.id for i in draft.ingredients):
        item = catalogue.items.get(food_id)
        if item is None:
            failures.append(
                Failure(
                    check="unknown_ingredient",
                    message=f"'{food_id}' is not in the ingredient list; use only ids from it",
                )
            )
            continue
        if food_id in food.exclude_foods:
            failures.append(
                Failure(
                    check="excluded_food",
                    message=f"'{food_id}' is on the plan's list of foods to avoid; leave it out",
                )
            )
        banned = [a for a in item.allergens if a in food.exclude_allergens]
        if banned:
            failures.append(
                Failure(
                    check="excluded_allergen",
                    message=f"'{food_id}' contains {', '.join(banned)}, which the plan excludes",
                )
            )
        if item.diet not in suits:
            failures.append(
                Failure(
                    check="diet",
                    message=f"'{food_id}' is a {item.diet} food and the plan's diet is {food.diet}",
                )
            )
    return failures


def check_slot_targets(draft_nutrition: MealResult, spec: SlotSpec, s: Settings) -> list[Failure]:
    """Calories within the tolerance of the slot's share, and protein at least a set part of it."""
    total = draft_nutrition.total
    incomplete = draft_nutrition.total_incomplete
    tolerance = s.recipe_kcal_tolerance
    found = [
        _verdict(
            "slot",
            "kcal",
            total.get("kcal"),
            incomplete.get("kcal"),
            floor=spec.kcal * (1 - tolerance),
            cap=spec.kcal * (1 + tolerance),
            note=_kcal_note(spec.kcal, tolerance),
        )
    ]
    # A slot with no protein share asks for no protein, so even an unknown total meets it.
    if spec.protein_g > 0:
        share = s.recipe_protein_min_share
        found.append(
            _verdict(
                "slot",
                "protein_g",
                total.get("protein_g"),
                incomplete.get("protein_g"),
                floor=spec.protein_g * share,
                note=f" ({share * 100:g}% of its {_n(spec.protein_g)} g share)",
            )
        )
    return [f for f in found if f is not None]


def check_gear_and_time(draft: DraftRecipe, rules: PlanRules, slot: Slot) -> list[Failure]:
    """Only gear the user owns, and active and total minutes within the slot's limits."""
    failures: list[Failure] = []
    owned = set(rules.gear)
    missing = [g for g in dict.fromkeys(draft.gear) if g not in owned]
    if missing:
        failures.append(
            Failure(
                check="gear",
                message=f"gear not owned: {_gear_names(missing)}; "
                f"the plan's kitchen has {_gear_names(rules.gear)}",
            )
        )
    if draft.active_min > slot.max_active_min:
        failures.append(
            Failure(
                check="active_min",
                message=f"active time {draft.active_min} min, "
                f"slot allows at most {slot.max_active_min} min",
            )
        )
    if draft.total_min > slot.max_total_min:
        failures.append(
            Failure(
                check="total_min",
                message=f"total time {draft.total_min} min, "
                f"slot allows at most {slot.max_total_min} min",
            )
        )
    return failures


def _gear_names(names: Iterable[str]) -> str:
    return ", ".join(name.replace("_", " ") for name in names)


# Day checks


def check_day_totals(
    total: Nutrients,
    incomplete: dict[str, list[str]],
    day_type: DayType,
    s: Settings,
) -> tuple[list[Failure], dict[str, float]]:
    """A day's totals against its day type, and how far carbohydrate and fat are from target.

    Calories must be within the tolerance of the target, protein at least the target, saturated
    fat at most its cap, fibre at least its floor, and each extra target met. `incomplete` names,
    for each nutrient, the foods that have no value for it, so its total is a lower bound.

    The second result holds carbohydrate and fat as a fraction of their target over (positive) or
    under (negative), such as 0.3 for 30% over. They never fail a day. A nutrient whose total is
    not fully known has no entry.
    """
    tolerance = s.day_kcal_tolerance

    def verdict(
        key: str, *, floor: float | None = None, cap: float | None = None, note: str = ""
    ) -> Failure | None:
        return _verdict(
            "day", key, total.get(key), incomplete.get(key), floor=floor, cap=cap, note=note
        )

    found = [
        verdict(
            "kcal",
            floor=day_type.kcal * (1 - tolerance),
            cap=day_type.kcal * (1 + tolerance),
            note=_kcal_note(day_type.kcal, tolerance),
        ),
        verdict("protein_g", floor=day_type.protein_g),
        verdict("sat_fat_g", cap=day_type.sat_fat_g_max),
        verdict("fibre_g", floor=day_type.fibre_g_min),
        *(
            verdict(
                target.nutrient,
                floor=target.value if target.kind == "min" else None,
                cap=target.value if target.kind == "max" else None,
            )
            for target in day_type.extra
        ),
    ]

    soft: dict[str, float] = {}
    for key, target in (("carbs_available_g", day_type.carbs_g), ("fat_g", day_type.fat_g)):
        value = total.get(key)
        if value is not None and key not in incomplete:
            soft[key] = (value - target) / target
    return [f for f in found if f is not None], soft


def check_day(
    day: DayKey, recipes: list[CheckedRecipe], day_type: DayType, s: Settings
) -> DayReport:
    """Add up the day's recipes and hold the sum to the day type.

    Recipes of other days in `recipes` are left out. A recipe nutrition-mcp could not compute
    counts as lacking every nutrient, so the day cannot pass as if it were not there.
    """
    parts: list[tuple[str, Nutrients]] = []
    incomplete: dict[str, list[str]] = {}
    for recipe in (r for r in recipes if r.spec.day == day):
        title = recipe.draft.title
        meal = recipe.nutrition
        if meal is None:
            for key in NUTRIENT_KEYS:
                _add_names(incomplete, key, [title])
            continue
        parts.append((title, meal.total))
        for key in NUTRIENT_KEYS:
            if key in meal.total_incomplete:
                _add_names(incomplete, key, meal.total_incomplete[key])
            elif meal.total.get(key) is None:
                _add_names(incomplete, key, [title])

    summed, _ = sum_nutrients(parts)
    # Two decimals, as nutrition-mcp reports them, so the sum carries no float noise.
    total = Nutrients.model_validate(
        {key: None if value is None else round(value, DECIMALS) for key, value in summed}
    )
    failures, soft = check_day_totals(total, incomplete, day_type, s)
    return DayReport(day=day, totals=dict(total), failures=failures, soft=soft)


def _add_names(into: dict[str, list[str]], key: str, names: Iterable[str]) -> None:
    known = into.setdefault(key, [])
    new = [name for name in dict.fromkeys(names) if name not in known]
    known.extend(new)


def worst_recipe_for_day(report: DayReport, recipes: list[CheckedRecipe]) -> CheckedRecipe:
    """The recipe of the report's day to rewrite when the day misses a target.

    That is the one furthest from its slot's calories, as a fraction of the share, so a snack
    10% off ranks above a dinner 5% off. A tie goes to the larger protein shortfall, as a
    fraction of the share, and a full tie to the first. A recipe with no numbers is furthest of
    all, as it is what keeps the day from being known.
    """
    todays = [r for r in recipes if r.spec.day == report.day]
    if not todays:
        raise ValueError(f"no recipes for {report.day} to choose from")
    return max(todays, key=_distance_from_share)


def _distance_from_share(recipe: CheckedRecipe) -> tuple[float, float]:
    meal = recipe.nutrition
    if meal is None:
        return math.inf, math.inf
    spec = recipe.spec
    kcal = meal.total.kcal
    protein = meal.total.protein_g
    off = math.inf if kcal is None else abs(kcal - spec.kcal) / spec.kcal
    short = 0.0
    if spec.protein_g > 0:
        short = 1.0 if protein is None else max(0.0, spec.protein_g - protein) / spec.protein_g
    return off, short


# Week checks


def check_variety(
    recipes: list[CheckedRecipe], rules: PlanRules, s: Settings
) -> dict[tuple[DayKey, str], list[Failure]]:
    """No two recipes of one slot alike, and no cuisine twice in a row.

    A recipe is alike an earlier one in its slot when the titles match (ignoring case and
    punctuation) or the ingredient ids overlap by a Jaccard similarity at or above
    `variety_jaccard_fail_at`. When the plan lists more than one cuisine, a recipe may not use
    the cuisine of the day before. The later recipe of a pair gets the failure, under its
    (day, slot id), because it is the one to rewrite.
    """
    spread_cuisines = len(rules.food.cuisines) > 1
    by_slot: dict[str, list[CheckedRecipe]] = defaultdict(list)
    for recipe in recipes:
        by_slot[recipe.spec.slot.id].append(recipe)

    found: dict[tuple[DayKey, str], list[Failure]] = {}
    for slot_id, in_slot in by_slot.items():
        in_week_order = sorted(in_slot, key=lambda r: DAYS.index(r.spec.day))
        for position, later in enumerate(in_week_order):
            failures: list[Failure] = []
            for earlier in in_week_order[:position]:
                failures += _alike(earlier, later, s)
                if spread_cuisines:
                    failures += _same_cuisine_next_day(earlier, later)
            if failures:
                found.setdefault((later.spec.day, slot_id), []).extend(failures)
    return found


def _alike(earlier: CheckedRecipe, later: CheckedRecipe, s: Settings) -> list[Failure]:
    failures: list[Failure] = []
    before = f"the {earlier.spec.day} recipe '{earlier.draft.title}'"
    if _plain(earlier.draft.title) == _plain(later.draft.title):
        failures.append(
            Failure(
                check="variety_title",
                message=f"same title as {before}; make a different dish and name it so",
            )
        )
    first = {i.id for i in earlier.draft.ingredients}
    second = {i.id for i in later.draft.ingredients}
    shared = first & second
    union = first | second
    similarity = len(shared) / len(union)
    if similarity >= s.variety_jaccard_fail_at - _EPS:
        failures.append(
            Failure(
                check="variety_ingredients",
                message=f"shares {len(shared)} of {len(union)} ingredients with {before} "
                f"(similarity {similarity:.2f}, it must stay under "
                f"{s.variety_jaccard_fail_at:.2f}); use mostly different ingredients",
            )
        )
    return failures


def _same_cuisine_next_day(earlier: CheckedRecipe, later: CheckedRecipe) -> list[Failure]:
    day_before = DAYS.index(later.spec.day) - DAYS.index(earlier.spec.day) == 1
    if day_before and _plain(earlier.draft.cuisine) == _plain(later.draft.cuisine):
        return [
            Failure(
                check="variety_cuisine",
                message=f"cuisine '{earlier.draft.cuisine}' repeats the day before "
                f"({earlier.spec.day}); the plan lists other cuisines, so use a different one",
            )
        ]
    return []


def _plain(text: str) -> str:
    """Lowercase, punctuation dropped and spaces collapsed, so near-identical titles match."""
    kept = "".join(c for c in text.lower() if c.isalnum() or c.isspace())
    return " ".join(kept.split())


def check_no_waste(
    week: Sequence[CheckedRecipe], catalogue: Catalogue, rules: PlanRules, s: Settings
) -> dict[str, Failure]:
    """Flag a short-lived ingredient the week uses too little of to finish a pack.

    The grams of each ingredient are added up over the whole week and set against its pack. Only
    an ingredient that keeps fewer than `no_waste_keeps_days` once opened counts, and only one
    the catalogue gives a pack size and shelf life for, since the rest are used by the pinch or
    keep. The result is keyed by ingredient id.

    `rules` is taken for the plan's shopping days, which do not change the arithmetic yet: the
    week is treated as one shopping period.
    """
    used: dict[str, float] = defaultdict(float)
    for recipe in week:
        for ingredient in recipe.draft.ingredients:
            used[ingredient.id] += ingredient.grams

    found: dict[str, Failure] = {}
    for food_id, grams in used.items():
        item = catalogue.items.get(food_id)
        if item is None or item.pack_g is None or item.keeps_days_opened is None:
            continue
        if item.keeps_days_opened >= s.no_waste_keeps_days:
            continue
        enough = item.pack_g * s.no_waste_min_pack_share
        if grams < enough - _EPS:
            days = item.keeps_days_opened
            found[food_id] = Failure(
                check="no_waste",
                message=f"week uses {_down(grams)} g of {item.name} ({food_id}), "
                f"{_down(grams / item.pack_g * 100)}% of a {_n(item.pack_g)} g pack that keeps "
                f"{days} day{'' if days == 1 else 's'} once opened; use at least {_up(enough)} g "
                "across the week or pick an ingredient that keeps longer",
            )
    return found
