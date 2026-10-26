"""What the model is told when it writes a slot's recipes and when it repairs them."""

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources

from meal_planner.catalogue import CatalogueItem
from meal_planner.checks import LABELS, spreads_cuisines
from meal_planner.config import Settings
from meal_planner.recipes import CheckedRecipe
from meal_planner.rules import DayType, PlanRules, Slot, SlotSpec, expand

# Slack for the whole-number ranges shown to the writer, as in checks.py.
_EPS = 1e-9

# Prompts


@dataclass(frozen=True)
class Job:
    """One model call: write the recipes for `specs`, or rewrite those in `fix`."""

    slot_id: str
    specs: list[SlotSpec]
    attempt: int
    # Why the slot's last reply was refused, for the model to avoid.
    rejected: str | None
    fix: list[CheckedRecipe]
    # The slot's recipes this call leaves alone, which the new ones must stay different from.
    others: list[CheckedRecipe]


@cache
def template(name: str) -> str:
    """The text of a file in `prompts/`."""
    return resources.files("meal_planner").joinpath("prompts", name).read_text(encoding="utf-8")


# The nutrients shown beside each ingredient when the writer gets hints: nutrition-mcp's key, how
# a value is read out, and the name used when there is no value.
_HINT_COLUMNS = (
    ("kcal", "kcal", "kcal"),
    ("protein_g", "g protein", "protein"),
    ("sat_fat_g", "g sat fat", "sat fat"),
    ("fibre_g", "g fibre", "fibre"),
    ("sodium_mg", "mg sodium", "sodium"),
)

# What the heading says each line is made of, and the note under it, for each setting.
_FORMAT = "id: name"
_FORMAT_WITH_HINTS = "id: name, then nutrition per 100 g"
_PER_100G_NOTE = (
    "The nutrition figures are there for choosing amounts. "
    "They must not be reported back in the reply.\n\n"
)


def _catalogue_line(item: CatalogueItem, hints: bool) -> str:
    if not hints:
        return f"{item.id}: {item.name}"
    # Whole numbers; a nutrient nutrition-mcp has no value for reads "fibre n/a", never 0.
    shown = ", ".join(
        f"{name} n/a" if (value := item.per_100g.get(key)) is None else f"{value:.0f} {label}"
        for key, label, name in _HINT_COLUMNS
    )
    return f"{item.id}: {item.name} (per 100 g: {shown})"


# The rule against a cuisine on two days in a row, as each prompt words it. It is left out when
# the plan lists one cuisine, because then the check lets every recipe use it.
_CUISINE_RULE = {
    "writer.md": " Do not use the same cuisine on two days in a row.",
    "repair.md": " Do not give it the same cuisine as the day before or the day after.",
    "baseline.md": " Within a slot, do not use the same cuisine on two days in a row.",
}


def _system(name: str, rules: PlanRules, allowed: list[CatalogueItem], hints: bool) -> str:
    values = {
        "catalogue": "\n".join(_catalogue_line(item, hints) for item in allowed),
        "catalogue_format": _FORMAT_WITH_HINTS if hints else _FORMAT,
        "per_100g_note": _PER_100G_NOTE if hints else "",
        "cuisine_rule": _CUISINE_RULE[name] if spreads_cuisines(rules) else "",
    }
    # One pass, so text that looks like a placeholder inside a value is not replaced again.
    return re.sub(
        r"\{\{(catalogue|catalogue_format|per_100g_note|cuisine_rule)\}\}",
        lambda m: values[m[1]],
        template(name),
    )


def quote(text: str) -> str:
    """The text as a JSON string with angle brackets escaped, so it cannot close the data block."""
    return json.dumps(text, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")


def _ident(text: str) -> str:
    return text if re.fullmatch(r"[a-z][a-z0-9_]*", text) else quote(text)


def _target(spec: SlotSpec, s: Settings) -> str:
    low = math.ceil(spec.kcal * (1 - s.recipe_kcal_tolerance) - _EPS)
    high = math.floor(spec.kcal * (1 + s.recipe_kcal_tolerance) + _EPS)
    text = f"{low} to {high} kcal (target {round(spec.kcal)})"
    if spec.protein_g > 0:
        least = math.ceil(spec.protein_g * s.recipe_protein_min_share - _EPS)
        return f"{text}, at least {least} g protein"
    return f"{text}, no protein target"


def _share_of_limits(day_type: DayType, share: float) -> str:
    """The slot's share of the day type's saturated-fat cap, fibre floor and extra targets.

    A cap is rounded down and a floor up, so the slots' shares together never allow more than the
    cap or ask for less than the floor.
    """
    limits = [
        ("sat_fat_g", "max", day_type.sat_fat_g_max),
        ("fibre_g", "min", day_type.fibre_g_min),
        *((t.nutrient, t.kind, t.value) for t in day_type.extra),
    ]
    parts: list[str] = []
    for key, kind, value in limits:
        name, unit = LABELS[key]
        if kind == "max":
            parts.append(f"{name} at most {math.floor(value * share + _EPS)} {unit}")
        else:
            parts.append(f"{name} at least {math.ceil(value * share - _EPS)} {unit}")
    return ", ".join(parts)


def _slot_lines(
    rules: PlanRules,
    settings: Settings,
    allowed: list[CatalogueItem],
    slot: Slot,
    specs: Sequence[SlotSpec],
) -> list[str]:
    """The slot, the plan's kitchen and tastes, and the target for each day, as data lines.

    Only liked foods the writer may use are listed: one the plan's diet, allergens or excluded
    foods rule out would invite a recipe that fails the catalogue check.
    """
    food = rules.food
    day_types = {d.id: d for d in rules.day_types}
    allowed_ids = {item.id for item in allowed}
    liked = [food_id for food_id in food.liked_foods if food_id in allowed_ids]

    def day_line(spec: SlotSpec) -> str:
        line = f"- {spec.day}: {_target(spec, settings)}"
        if not settings.writer_nutrient_hints:
            return line
        limits = _share_of_limits(day_types[spec.day_type], slot.kcal_share)
        return f"{line}; the slot's share of the day's limits: {limits}"

    return [
        f"slot_id: {quote(slot.id)}",
        f"slot_name: {quote(slot.name)}",
        f"style: {quote(slot.style)}",
        f"cook_when: {slot.cook_when}",
        f"max_active_min: {slot.max_active_min}",
        f"max_total_min: {slot.max_total_min}",
        f"diet: {food.diet}",
        f"gear: {', '.join(rules.gear)}",
        f"cuisines: {', '.join(quote(c) for c in food.cuisines)}",
        f"liked_foods: {', '.join(quote(f) for f in liked) or 'none'}",
        "days:",
        *(day_line(spec) for spec in specs),
    ]


def _summary(recipe: CheckedRecipe) -> str:
    draft = recipe.draft
    ids = ", ".join(_ident(i.id) for i in draft.ingredients)
    return (
        f"- {recipe.spec.day}: {quote(draft.title)}, cuisine {quote(draft.cuisine)}, "
        f"ingredients {ids}"
    )


def _fix_lines(recipe: CheckedRecipe) -> list[str]:
    draft = recipe.draft
    grams = ", ".join(f"{_ident(i.id)} {i.grams:g} g" for i in draft.ingredients)
    lines = [
        f"- day: {recipe.spec.day}",
        f"  title: {quote(draft.title)}",
        f"  cuisine: {quote(draft.cuisine)}",
        f"  ingredients: {grams}",
        f"  minutes: active {draft.active_min}, total {draft.total_min}",
        f"  gear: {', '.join(draft.gear) or 'none'}",
    ]
    if recipe.nutrition is not None:
        # Worked out by nutrition-mcp, so a rewrite can see how far it has to move.
        total = recipe.nutrition.total
        known = [
            f"{value:.0f} {label}"
            for key, label in (("kcal", "kcal"), ("protein_g", "g protein"))
            if (value := total.get(key)) is not None
        ]
        if known:
            lines.append(f"  now comes to: {', '.join(known)}")
    return [
        *lines,
        "  failed checks:",
        *(f"  - {quote(failure.message)}" for failure in recipe.failures),
    ]


def _user(
    rules: PlanRules, settings: Settings, allowed: list[CatalogueItem], job: Job, closing: str
) -> str:
    slot = next(s for s in rules.slots if s.id == job.slot_id)
    lines = _slot_lines(rules, settings, allowed, slot, job.specs)
    if job.fix:
        lines += ["recipes to fix:", *(line for r in job.fix for line in _fix_lines(r))]
    if job.others:
        lines += [
            "other recipes in this slot this week (the new ones must differ from these):",
            *(_summary(r) for r in job.others),
        ]
    text = "\n".join(["<data>", *lines, "</data>"])
    if job.rejected:
        text += f"\nYour previous reply was rejected: {quote(job.rejected)}. Fix that."
    return f"{text}\n{closing}"


def writer_prompt(
    rules: PlanRules, settings: Settings, allowed: list[CatalogueItem], job: Job
) -> tuple[str, str]:
    n = len(job.specs)
    closing = f'Write {n} recipe{"" if n == 1 else "s"}, one for each day under "days".'
    system = _system("writer.md", rules, allowed, settings.writer_nutrient_hints)
    return system, _user(rules, settings, allowed, job, closing)


def repair_prompt(
    rules: PlanRules, settings: Settings, allowed: list[CatalogueItem], job: Job
) -> tuple[str, str]:
    n = len(job.fix)
    closing = f'Fix the {n} recipe{"" if n == 1 else "s"} under "recipes to fix", same days.'
    system = _system("repair.md", rules, allowed, settings.writer_nutrient_hints)
    return system, _user(rules, settings, allowed, job, closing)


def baseline_prompt(
    rules: PlanRules, settings: Settings, allowed: list[CatalogueItem]
) -> tuple[str, str]:
    """The eval's single-call baseline: every recipe of the week asked for in one call.

    For each slot, the data block holds the lines the writer gets when it writes that slot's
    week, and the system prompt lists the ingredients as the writer's does, so the baseline is
    told what the pipeline is told. Only the instructions differ, to ask for the whole week.
    """
    specs = expand(rules)
    lines: list[str] = []
    for number, slot in enumerate(rules.slots, start=1):
        mine = [spec for spec in specs if spec.slot.id == slot.id]
        lines += [
            f"slot {number} of {len(rules.slots)}",
            *_slot_lines(rules, settings, allowed, slot, mine),
        ]
    n = len(specs)
    closing = (
        f"Write {n} recipe{'' if n == 1 else 's'}: for each slot, one for each day under its "
        '"days", in one reply.'
    )
    system = _system("baseline.md", rules, allowed, settings.writer_nutrient_hints)
    return system, "\n".join(["<data>", *lines, "</data>", closing])
