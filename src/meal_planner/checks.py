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
import re
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Sequence
from typing import Literal

from nutrition_mcp.models import DIET_SUITABLE_FOR, Nutrients
from nutrition_mcp.nutrients import NUTRIENT_KEYS
from nutrition_mcp.nutrition import sum_nutrients
from nutrition_mcp.schemas import DECIMALS, MealResult

from meal_planner.catalogue import Catalogue
from meal_planner.config import Settings
from meal_planner.recipes import CheckedRecipe, DayReport, DraftRecipe, Failure
from meal_planner.rules import DAYS, DayKey, DayType, PlanRules, Slot, SlotSpec

_Direction = Literal["over", "under", "unknown"]

# Slack for floating point, so a value exactly on a limit passes whatever the last bit of
# `target * tolerance` is. nutrition-mcp reports two decimals, far coarser than this.
_EPS = 1e-9

# What the messages call each nutrient nutrition-mcp reports, and its unit. The writer's prompts
# use the same words.
LABELS: dict[str, tuple[str, str]] = {
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

# Rounding for messages. A number is shown in whole units when that is within `_SHOWN_WITHIN` of
# it, and with one or two decimals when it is not, so a limit of 2.4 ug is not shown as 3 and
# 17.9 mg is not shown as 17. Rounding runs the way that keeps a miss a miss ("550 kcal, at most
# 550" never appears) and gives the writer a number it can aim at: up for a floor, down for a cap
# and for what a recipe has.
_SHOWN_WITHIN = 0.02


def _shown(x: float, whole: Callable[[float], float]) -> str:
    """`x` to the fewest decimals (none, one or two) that keep it within `_SHOWN_WITHIN`, with
    `whole` turning a scaled value into the whole number to keep."""
    for places in (0, 1, 2):
        scale = 10**places
        kept = whole(x * scale) / scale
        if abs(kept - x) <= _SHOWN_WITHIN * abs(x) + _EPS or places == 2:
            break
    return f"{kept:.{places}f}"


def _up(x: float) -> str:
    return _shown(x, lambda scaled: math.ceil(scaled - _EPS))


def _down(x: float) -> str:
    return _shown(x, lambda scaled: math.floor(scaled + _EPS))


def _n(x: float) -> str:
    return _shown(x, round)


def _foods(food_ids: Iterable[str]) -> str:
    """Foods as a person reads them: "mustard_oil" is "mustard oil"."""
    return ", ".join(food_id.replace("_", " ") for food_id in food_ids)


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

    `scope` is "slot" or "day". `missing` holds the ids of the foods with no value for the
    nutrient, and is None when the value is complete; when it is not None, `value` is a lower
    bound. The message names those foods, and nothing else, in `_foods` form.
    """
    name, unit = LABELS[key]
    lack = f" (no value for {_foods(missing)})" if missing else ""
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
    """Only gear the user owns, active and total minutes within the slot's limits, and no more
    active minutes than total minutes, since the total includes the active time."""
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
    if draft.active_min > draft.total_min:
        failures.append(
            Failure(
                check="time",
                message=f"active time {draft.active_min} min is more than the total time "
                f"{draft.total_min} min; the total includes the active time",
            )
        )
    return failures


def _gear_names(names: Iterable[str]) -> str:
    return ", ".join(name.replace("_", " ") for name in names)


# A nutrition figure in the model's words: a number beside a nutrition word, either way round,
# with or without a unit ("450 kcal", "28 g protein", "A 400-kcal dinner", "25+ g protein",
# "protein: 28 g", "fat less than 10 g", "~30g of protein").
_NUTRIENT = (
    r"(?:k?cals?|kj|calories?|protein|carb(?:ohydrate)?s?|(?:sat(?:urated)?\s+)?fat|fib(?:re|er)"
    r"|sugars?|sodium|salt)"
)
# The whole number, so "12 minutes" is never read as "1" followed by something else.
_NUMBER = r"(?<![\w.,])\d+(?:[.,]\d+)*(?![.,]?\d)"
_MASS = r"g|gms?|grams?|mg|milligrams?|mcg|ug|µg"
_UNIT = rf"(?:k?cals?|kj|{_MASS}|%)(?![a-z])"
# What can join a number to its unit or word: a space, a hyphen ("400-kcal") or a plus ("25+ g").
_JOIN = r"(?:-|\s*\+\s*|\s*)"
# What can join a unit to the word: a space, a hyphen ("30g-protein") or a plus tight to the unit
# ("25g+ protein"), but not a spaced plus, so the "+" in "100 g + salt and pepper" does not tie the
# 100 g to the salt.
_JOIN_AFTER_UNIT = r"(?:-|\+\s*|\s*)"
# Words that can stand between a nutrition word and its number: "protein of 28 g", "fat under 9",
# "protein at least 30 g". Longer phrases come first so "no more than" is not cut at "more than".
_LINK = (
    r"(?:[:=(~-]\s*|(?:of|is|about|around|approx(?:imately)?|roughly|nearly|only|just"
    r"|at\s+least|at\s+most|at|no\s+more\s+than|less\s+than|more\s+than|up\s+to"
    r"|under|over|below|above)\s+)"
)
# After "salt 2" or "sugar 1", a measure, a fraction or a time means an amount or a moment, not a
# figure: "salt 2 tsp", "sugar 1/2 cup", "salt 2 minutes". A slash before a word is a rate
# ("calories 400/day"), which is one.
_NOT_A_FIGURE = (
    r"(?:/\s*\d|(?:tsps?|tbsps?|teaspoons?|tablespoons?|cups?|ml|l|litres?|liters?|pinch(?:es)?"
    r"|secs?|seconds?|mins?|minutes?|hrs?|hours?|days?|times?|pieces?|whistles?)(?![a-z]))"
)
# "fat-free" and "protein-rich" describe a food, not a quantity: "100 g fat-free yogurt". It is
# one only when the number reaches the word by spaces alone, with at most a mass between them, and
# a food follows the hyphenated word, so "30 g protein-rich.", "a 400-calorie-controlled plan" and
# "150 kcal sugar-free snack" (an energy, not a mass) are still figures.
_DESCRIBES = rf"(?!{_NUMBER}\s*(?:(?:{_MASS})(?![a-z])\s*)?(?:of\s+)?{_NUTRIENT}-[a-z]+\s+[a-z])"
_NUMBER_THEN_WORD = (
    rf"{_DESCRIBES}{_NUMBER}{_JOIN}(?:{_UNIT}{_JOIN_AFTER_UNIT})?(?:of\s+)?{_NUTRIENT}(?![a-z])"
)
_WORD_THEN_NUMBER = (
    rf"(?<![a-z]){_NUTRIENT}\s*{_LINK}*~?{_NUMBER}(?:\s*{_UNIT})?(?!\s*{_NOT_A_FIGURE})"
)
_FIGURE = re.compile(f"{_NUMBER_THEN_WORD}|{_WORD_THEN_NUMBER}", re.IGNORECASE)


def check_model_figures(draft: DraftRecipe) -> list[Failure]:
    """No nutrition figure in the text the user reads: the title, the steps, store, swap, fits.

    The model is never asked for a figure, and one it writes anyway would be a guess shown next
    to the numbers nutrition-mcp worked out. A field that names one fails, quoting what it says.
    """
    fields = [
        ("title", draft.title),
        *((f"step {n}", step) for n, step in enumerate(draft.steps, start=1)),
        ("store", draft.store),
        ("swap", draft.swap),
        ("fits", draft.fits),
    ]
    failures: list[Failure] = []
    for name, text in fields:
        found = list(dict.fromkeys(match.group().strip() for match in _FIGURE.finditer(text)))
        if found:
            quoted = ", ".join(f"'{phrase}'" for phrase in found)
            failures.append(
                Failure(
                    check="model_figure",
                    message=f"{name} names a nutrition figure ({quoted}); nutrition figures are "
                    "worked out in code and must be left out of the text",
                )
            )
    return failures


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


def add_up_meals(
    meals: Sequence[tuple[MealResult | None, Sequence[str]]],
) -> tuple[Nutrients, dict[str, list[str]]]:
    """Add up what nutrition-mcp says each meal has, and name the foods that lack each nutrient.

    A meal comes with the ids of the foods it is made of. The second result names, for each
    nutrient, the ids of the foods with no value for it, so its total is a lower bound. The
    names are always foods: nutrition-mcp's own list, or all the foods of a meal it could not
    work out (None), which then counts as lacking every nutrient so the sum cannot pass as if it
    were not there. A meal whose total is None for a nutrient without nutrition-mcp saying why
    names its foods too. With no meals, nothing is known.
    """
    parts: list[tuple[str, Nutrients]] = []
    incomplete: dict[str, list[str]] = {}
    for position, (meal, foods) in enumerate(meals):
        if meal is None:
            for key in NUTRIENT_KEYS:
                _add_names(incomplete, key, foods)
            continue
        parts.append((str(position), meal.total))
        for key in NUTRIENT_KEYS:
            if key in meal.total_incomplete:
                _add_names(incomplete, key, meal.total_incomplete[key])
            elif meal.total.get(key) is None:
                _add_names(incomplete, key, foods)

    summed, _ = sum_nutrients(parts)
    # Two decimals, as nutrition-mcp reports them, so the sum carries no float noise.
    total = Nutrients.model_validate(
        {key: None if value is None else round(value, DECIMALS) for key, value in summed}
    )
    return total, incomplete


def check_day(
    day: DayKey, recipes: list[CheckedRecipe], day_type: DayType, s: Settings
) -> DayReport:
    """Add up the day's recipes and hold the sum to the day type.

    Recipes of other days in `recipes` are left out. A recipe nutrition-mcp could not compute
    counts as lacking every nutrient in all its ingredients (see `add_up_meals`).
    """
    todays = [r for r in recipes if r.spec.day == day]
    total, incomplete = add_up_meals(
        [(r.nutrition, [i.id for i in r.draft.ingredients]) for r in todays]
    )
    failures, soft = check_day_totals(total, incomplete, day_type, s)
    return DayReport(day=day, totals=dict(total), failures=failures, soft=soft)


def _add_names(into: dict[str, list[str]], key: str, names: Iterable[str]) -> None:
    known = into.setdefault(key, [])
    new = [name for name in dict.fromkeys(names) if name not in known]
    known.extend(new)


def worst_recipe_for_day(
    report: DayReport, recipes: list[CheckedRecipe], day_type: DayType
) -> CheckedRecipe:
    """The recipe of the report's day whose rewrite is most likely to fix its first failing check.

    The first failing check, in nutrient order, sets the nutrient and the direction. A recipe is
    ranked by how many units it is above its slot's share when the day is over, and below its
    share when the day is under: kcal and protein have a share per slot, so a 5 kcal miss on a
    50 kcal snack ranks below a 72 kcal miss on a dinner. A nutrient with no share per slot
    (saturated fat, fibre, an extra target) counts as a share of 0, so a day over its cap blames
    the recipe holding the most of it and a day under its floor the recipe holding the least.

    An amount that is not known (a recipe with no numbers, or no value for the nutrient) never
    ranks first when the day is over, because it cannot be shown to cause the excess, and always
    does when the day is under. A day that is within its limits as far as is known but cannot be
    confirmed, because a recipe lacks a value, blames a recipe that lacks it.

    A tie goes to the earlier slot of the day, then to the first in `recipes`.
    """
    todays = sorted(
        (r for r in recipes if r.spec.day == report.day), key=lambda r: r.spec.slot.eat_at
    )
    if not todays:
        raise ValueError(f"no recipes for {report.day} to choose from")
    if not report.failures:
        raise ValueError(f"{report.day} passes every day check, so no recipe is to blame")
    key = report.failures[0].check.removeprefix("day_")
    direction = _direction(key, report.totals.get(key), day_type)
    return max(todays, key=lambda r: _blame(r, key, direction))


def _direction(key: str, total: float | None, day_type: DayType) -> _Direction:
    """Whether a day's total is over a maximum, under a minimum, or neither (or not known).

    Calories are held to their target here, not to the tolerance around it: a day that failed on
    calories is outside the band, so the side of the target it is on is the side it missed on.
    """
    if total is None:
        return "unknown"
    limits: list[tuple[str, float]] = []
    match key:
        case "kcal":
            limits = [("min", day_type.kcal), ("max", day_type.kcal)]
        case "protein_g":
            limits = [("min", day_type.protein_g)]
        case "sat_fat_g":
            limits = [("max", day_type.sat_fat_g_max)]
        case "fibre_g":
            limits = [("min", day_type.fibre_g_min)]
    limits += [(t.kind, t.value) for t in day_type.extra if t.nutrient == key]
    if any(kind == "max" and total > value for kind, value in limits):
        return "over"
    if any(kind == "min" and total < value for kind, value in limits):
        return "under"
    return "unknown"


def _blame(recipe: CheckedRecipe, key: str, direction: _Direction) -> float:
    """How much of the miss in `key` the recipe is, larger meaning more to blame."""
    meal = recipe.nutrition
    held = None if meal is None else meal.total.get(key)
    if direction == "unknown":
        lacks = meal is None or held is None or key in meal.total_incomplete
        return 1.0 if lacks else 0.0
    if held is None:
        return -math.inf if direction == "over" else math.inf
    share = {"kcal": recipe.spec.kcal, "protein_g": recipe.spec.protein_g}.get(key, 0.0)
    return held - share if direction == "over" else share - held


# Week checks


def spreads_cuisines(rules: PlanRules) -> bool:
    """Whether the plan lists more than one cuisine, so a cuisine may not repeat the next day.

    Cuisines are counted in plain form, so a plan that spells one cuisine two ways lists one.
    """
    return len({_plain(cuisine) for cuisine in rules.food.cuisines}) > 1


def check_variety(
    recipes: list[CheckedRecipe],
    rules: PlanRules,
    s: Settings,
    prefer: Sequence[Collection[tuple[DayKey, str]]] = (),
) -> dict[tuple[DayKey, str], list[Failure]]:
    """Cuisines from the plan's list, no two recipes of one slot alike, no cuisine twice in a row.

    A recipe's cuisine must be one of the plan's. Cuisines, like titles, are compared in plain
    form (see `_plain`), so "South-Indian" is the listed "South Indian". Two recipes of a slot are
    alike when the titles match or the ingredient ids overlap by a Jaccard similarity at or above
    `variety_jaccard_fail_at`. When the plan lists more than one cuisine, two recipes on days next
    to each other may not use the same listed cuisine.

    A clash between two recipes goes to one of them, under its (day, slot id), worded for it: it
    is the one to rewrite. `prefer` holds sets of (day, slot id) in order: the first set that has
    one recipe of the pair and not the other picks that one. When none tells them apart, the later
    day gets the clash.
    """
    listed: dict[str, str] = {}
    for cuisine in rules.food.cuisines:
        listed.setdefault(_plain(cuisine), cuisine)
    spread = spreads_cuisines(rules)
    by_slot: dict[str, list[CheckedRecipe]] = defaultdict(list)
    for recipe in recipes:
        by_slot[recipe.spec.slot.id].append(recipe)

    def key(recipe: CheckedRecipe) -> tuple[DayKey, str]:
        return (recipe.spec.day, recipe.spec.slot.id)

    def blame_earlier(earlier: CheckedRecipe, later: CheckedRecipe) -> bool:
        for keys in prefer:
            if (key(earlier) in keys) != (key(later) in keys):
                return key(earlier) in keys
        return False

    found: dict[tuple[DayKey, str], list[Failure]] = {}

    def add(recipe: CheckedRecipe, failures: list[Failure]) -> None:
        if failures:
            found.setdefault(key(recipe), []).extend(failures)

    for in_slot in by_slot.values():
        in_week_order = sorted(in_slot, key=lambda r: DAYS.index(r.spec.day))
        # The plan's spelling of each recipe's cuisine, or None when it is not one of them.
        matched = [listed.get(_plain(r.draft.cuisine)) for r in in_week_order]
        for position, later in enumerate(in_week_order):
            if matched[position] is None:
                add(later, [_unlisted_cuisine(later, rules.food.cuisines)])
            for before, earlier in enumerate(in_week_order[:position]):
                blamed, other = (earlier, later)
                if not blame_earlier(earlier, later):
                    blamed, other = (later, earlier)
                add(blamed, _alike(blamed, other, s))
                if spread and matched[before] == matched[position]:
                    add(blamed, _same_cuisine_next_day(blamed, other, matched[before]))
    return found


def _unlisted_cuisine(recipe: CheckedRecipe, cuisines: Sequence[str]) -> Failure:
    return Failure(
        check="cuisine_not_in_plan",
        message=f"cuisine '{recipe.draft.cuisine}' is not one of the plan's cuisines "
        f"({', '.join(cuisines)}); use one of them",
    )


def _alike(blamed: CheckedRecipe, other: CheckedRecipe, s: Settings) -> list[Failure]:
    """How `blamed` is alike `other`, worded for `blamed`, which is the one to rewrite."""
    failures: list[Failure] = []
    them = f"the {other.spec.day} recipe '{other.draft.title}'"
    if _plain(blamed.draft.title) == _plain(other.draft.title):
        failures.append(
            Failure(
                check="variety_title",
                message=f"same title as {them}; make a different dish and name it so",
            )
        )
    first = {i.id for i in blamed.draft.ingredients}
    second = {i.id for i in other.draft.ingredients}
    shared = first & second
    union = first | second
    similarity = len(shared) / len(union)
    if similarity >= s.variety_jaccard_fail_at - _EPS:
        failures.append(
            Failure(
                check="variety_ingredients",
                message=f"shares {len(shared)} of {len(union)} ingredients with {them} "
                f"(similarity {similarity:.2f}, it must stay under "
                f"{s.variety_jaccard_fail_at:.2f}); use mostly different ingredients",
            )
        )
    return failures


def _same_cuisine_next_day(
    blamed: CheckedRecipe, other: CheckedRecipe, cuisine: str | None
) -> list[Failure]:
    """`cuisine` is the plan's spelling of the cuisine both recipes use, or None when it is not
    one of the plan's: two cuisines outside the list are not a repeat."""
    gap = DAYS.index(blamed.spec.day) - DAYS.index(other.spec.day)
    if abs(gap) == 1 and cuisine is not None:
        side = "before" if gap == 1 else "after"
        return [
            Failure(
                check="variety_cuisine",
                message=f"cuisine '{cuisine}' repeats the day {side} ({other.spec.day}); "
                "the plan lists other cuisines, so use a different one",
            )
        ]
    return []


def _plain(text: str) -> str:
    """Lowercase, punctuation turned into spaces and runs of spaces collapsed.

    "Masala-Oats" and "masala oats!" are then the same title, and "South-Indian" the listed
    "South Indian". Nothing is joined: "dal-tadka" is two words, as written.
    """
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def check_no_waste(
    week: Sequence[CheckedRecipe], catalogue: Catalogue, rules: PlanRules, s: Settings
) -> dict[tuple[str, DayKey], Failure]:
    """Flag a short-lived ingredient that a shopping window uses too little of to finish a pack.

    The plan's shopping days split the week into windows. One runs from a shopping day to the day
    before the next, and the days before the first shopping day belong to the last window, which
    wraps past Sunday. Whatever is bought on the first day of a window has to be used inside it,
    so each window is judged on its own: the grams of an ingredient in its recipes are set
    against the ingredient's pack. A window that uses none of it buys none and is not flagged.

    Only an ingredient that keeps fewer than `no_waste_keeps_days` once opened counts, and only
    one the catalogue gives a pack size and shelf life for, since the rest are used by the pinch
    or keep. The result is keyed by (ingredient id, first day of the window), so one ingredient
    can fail in more than one window.
    """
    window_of = _shopping_windows(rules.shopping_days)
    used: dict[tuple[str, DayKey], float] = defaultdict(float)
    for recipe in week:
        start = window_of[recipe.spec.day]
        for ingredient in recipe.draft.ingredients:
            used[(ingredient.id, start)] += ingredient.grams

    found: dict[tuple[str, DayKey], Failure] = {}
    for (food_id, start), grams in used.items():
        item = catalogue.items.get(food_id)
        if item is None or item.pack_g is None or item.keeps_days_opened is None:
            continue
        if item.keeps_days_opened >= s.no_waste_keeps_days:
            continue
        enough = item.pack_g * s.no_waste_min_pack_share
        if grams < enough - _EPS:
            days = item.keeps_days_opened
            found[(food_id, start)] = Failure(
                check="no_waste",
                message=f"{item.name} ({food_id}): the shopping window "
                f"{_window_text(window_of, start)} uses {_down(grams)} g, "
                f"{_down(grams / item.pack_g * 100)}% of a {_n(item.pack_g)} g pack that keeps "
                f"{days} day{'' if days == 1 else 's'} once opened; use at least {_up(enough)} g "
                "in those days or pick an ingredient that keeps longer",
            )
    return found


def _shopping_windows(shopping_days: Iterable[DayKey]) -> dict[DayKey, DayKey]:
    """The first day of the shopping window each day of the week falls in."""
    starts = sorted(set(shopping_days), key=DAYS.index)
    window_of: dict[DayKey, DayKey] = {}
    for position, day in enumerate(DAYS):
        began = [start for start in starts if DAYS.index(start) <= position]
        window_of[day] = began[-1] if began else starts[-1]
    return window_of


def _window_text(window_of: dict[DayKey, DayKey], start: DayKey) -> str:
    """The days of a window in the order they come, such as "sat to tue" or a single "mon"."""
    first = DAYS.index(start)
    days = sorted(
        (day for day in DAYS if window_of[day] == start),
        key=lambda day: (DAYS.index(day) - first) % len(DAYS),
    )
    return days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"
