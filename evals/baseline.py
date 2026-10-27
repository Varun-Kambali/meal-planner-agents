"""The single-call baseline the recipe eval measures the pipeline against.

One model call writes the whole week (task `baseline_week`), given what the writer is given: the
plan's lines for each slot and the ingredients the plan allows, with their nutrition per 100 g
when `writer_nutrient_hints` is on (`week_prompts.baseline_prompt`). Its recipes then get their
numbers from nutrition-mcp and go through every check the pipeline's last round runs
(`week.evaluate_week`). Nothing is fitted and nothing is repaired: that is what the pipeline adds.

A recipe in the reply that does not fit the format costs only that recipe, not the week. That is
kinder to the baseline than the pipeline is to a slot (one bad recipe fails the slot's whole
reply there), so the comparison does not lean towards the pipeline.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from meal_planner.catalogue import Catalogue
from meal_planner.config import Settings
from meal_planner.llm import INVALID_JSON, SCHEMA_MISMATCH, ModelPort
from meal_planner.nutrition import NutritionPort
from meal_planner.recipes import CheckedRecipe, DraftRecipe, SlotDraft
from meal_planner.rules import DayKey, PlanRules, expand
from meal_planner.week import evaluate_week, with_numbers
from meal_planner.week_events import WeekResult
from meal_planner.week_prompts import baseline_prompt

TASK = "baseline_week"

Key = tuple[DayKey, str]

# Why a recipe the plan asks for is not in the week.
NOT_JSON = "the reply was not valid JSON"
WRONG_SHAPE = "the reply did not fit the format"
NOT_IN_REPLY = "no recipe for this day and slot in the reply"
RECIPE_WRONG_SHAPE = "the recipe for this day and slot did not fit the format"


class BaselineWeek(BaseModel):
    """The reply the baseline asks for: each slot with its recipes, as the writer writes one."""

    model_config = ConfigDict(extra="forbid")

    slots: list[SlotDraft]


@dataclass(frozen=True)
class BaselineDrafts:
    """What the one call gave: a draft for each (day, slot id) it wrote, the reason for each it
    did not, and what the call used."""

    drafts: dict[Key, DraftRecipe]
    unwritten: dict[Key, str]
    calls: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    # Recipes in the reply for a day or slot the plan does not ask for, or a second one for a day
    # and slot already written. They are left out.
    ignored: int = 0


async def write_week_single_call(
    rules: PlanRules, *, model: ModelPort, catalogue: Catalogue, settings: Settings
) -> BaselineDrafts:
    """Ask for every recipe of the week in one call, and read what came back.

    The call may take a slot's timeout for each slot, since it writes as many recipes as all the
    pipeline's first-round calls together.
    """
    system, user = baseline_prompt(rules, settings, catalogue.allowed_for(rules))
    timeout_s = settings.slot_call_timeout_s * len(rules.slots)
    reply = await model.structured(
        task=TASK, system=system, user=user, schema=BaselineWeek, timeout_s=timeout_s
    )
    asked = [(spec.day, spec.slot.id) for spec in expand(rules)]
    usage: dict[str, Any] = {
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "cost_usd": reply.cost_usd,
    }
    if reply.value is not None:
        found, dropped = _pairs(reply.value.slots), set[tuple[str, str]]()
    elif reply.error in (INVALID_JSON, SCHEMA_MISMATCH):
        read = _read_leniently(reply.raw)
        if isinstance(read, str):
            return BaselineDrafts({}, dict.fromkeys(asked, read), **usage)
        found, dropped = read
    else:
        # A rate limit, a timeout or a refusal that the model client has already retried.
        return BaselineDrafts({}, dict.fromkeys(asked, reply.error or "no reply"), **usage)

    drafts: dict[Key, DraftRecipe] = {}
    ignored = 0
    for key, draft in found:
        if key in asked and key not in drafts:
            drafts[key] = draft
        else:
            ignored += 1
    unwritten = {
        key: RECIPE_WRONG_SHAPE if key in dropped else NOT_IN_REPLY
        for key in asked
        if key not in drafts
    }
    return BaselineDrafts(drafts, unwritten, ignored=ignored, **usage)


def _pairs(slots: list[SlotDraft]) -> list[tuple[Key, DraftRecipe]]:
    return [((recipe.day, slot.slot_id), recipe) for slot in slots for recipe in slot.recipes]


def _read_leniently(raw: str) -> tuple[list[tuple[Key, DraftRecipe]], set[tuple[str, str]]] | str:
    """The recipes in a reply that does not fit the format as a whole, each read on its own.

    Returns the recipes that fit, with the (day, slot id) of each one that did not where its day
    and slot can be read, or the reason nothing could be read.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return NOT_JSON
    slots = data.get("slots") if isinstance(data, dict) else None
    if not isinstance(slots, list):
        return WRONG_SHAPE
    found: list[tuple[Key, DraftRecipe]] = []
    dropped: set[tuple[str, str]] = set()
    for slot in slots:
        slot_id = slot.get("slot_id") if isinstance(slot, dict) else None
        recipes = slot.get("recipes") if isinstance(slot, dict) else None
        if not isinstance(slot_id, str) or not isinstance(recipes, list):
            continue
        for recipe in recipes:
            try:
                draft = DraftRecipe.model_validate(recipe)
            except ValidationError:
                day = recipe.get("day") if isinstance(recipe, dict) else None
                if isinstance(day, str):
                    dropped.add((day, slot_id))
                continue
            found.append(((draft.day, slot_id), draft))
    return found, dropped


async def check_baseline_week(
    rules: PlanRules,
    written: BaselineDrafts,
    *,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
) -> WeekResult:
    """The baseline's week with numbers from nutrition-mcp and the pipeline's checks.

    Grams are used as written, whatever `fit_portions` says, and no recipe is repaired. Raises
    NutritionError when nutrition-mcp cannot be reached, because then the week's numbers are not
    known and scoring it would count the outage against the baseline.
    """
    specs = {(spec.day, spec.slot.id): spec for spec in expand(rules)}
    unchecked = [
        CheckedRecipe(spec=specs[key], draft=draft, nutrition=None, failures=[], attempts=1)
        for key, draft in written.drafts.items()
    ]
    numbered = await asyncio.gather(
        *(
            with_numbers(r, nutrition=nutrition, catalogue=catalogue, raise_if_unreachable=True)
            for r in unchecked
        )
    )
    recipes, days, no_waste = evaluate_week(numbered, rules, catalogue=catalogue, settings=settings)
    order = list(specs)
    return WeekResult(
        recipes=recipes,
        days=days,
        no_waste=no_waste,
        unwritten=[
            (day, slot_id, written.unwritten[(day, slot_id)])
            for day, slot_id in sorted(written.unwritten, key=order.index)
        ],
        calls=written.calls,
        input_tokens=written.input_tokens,
        output_tokens=written.output_tokens,
        cost_usd=written.cost_usd,
    )
