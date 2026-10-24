"""The graph that writes a week of recipes, checks them in code and repairs the ones that fail.

The steps are the ones in docs/architecture.md, "Writing a week":

    expand -> write_slot (one model call per slot, all slots at once)
           -> compute (one nutrition-mcp call per recipe, a second for a recipe scaled to its slot)
           -> check -> repair_slot (one call per slot with failing recipes) -> compute -> check ...
           -> report

A slot is seven recipes written in one call. Each pass through write or repair is a round.
After the first round a slot gets at most `max_repair_attempts` more, so a week costs at most
one writing call plus that many repair calls per slot, and a recipe is tried at most
`1 + max_repair_attempts` times. A reply that cannot be used (broken JSON, an amount out of
range, the wrong recipes for the days asked) fails the attempt for the whole slot and the rest
of the week goes on. A rate limit or timeout is not tried again here: the model client has
already retried it, so the slot is reported unwritten with the reason.

Progress goes out as events while the graph runs. The model is only ever asked for words and
for grams of catalogue ingredients; every nutrition number comes from nutrition-mcp.
"""

import asyncio
import json
import math
import operator
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Overwrite, Send
from nutrition_mcp.schemas import MealItemIn
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_serializer,
    field_validator,
)

from meal_planner.catalogue import Catalogue, CatalogueItem
from meal_planner.checks import (
    LABELS,
    check_catalogue,
    check_day,
    check_gear_and_time,
    check_no_waste,
    check_slot_targets,
    check_variety,
    worst_recipe_for_day,
)
from meal_planner.config import Settings
from meal_planner.llm import INVALID_JSON, SCHEMA_MISMATCH, ModelPort, ModelReply
from meal_planner.nutrition import NutritionError, NutritionPort
from meal_planner.portions import portion_factor, scale_draft
from meal_planner.recipes import CheckedRecipe, DayReport, DraftRecipe, Failure, SlotDraft
from meal_planner.rules import DAYS, DayKey, DayType, PlanRules, Slot, SlotSpec, expand

_RecipeKey = tuple[DayKey, str]

# The check name of a recipe that nutrition-mcp refused. Every other failure is worked out again
# on each pass; this one is carried, because the refusal is not repeated.
NUTRITION_FAILED = "nutrition_failed"

# Slack for the whole-number ranges shown to the writer, as in checks.py.
_EPS = 1e-9

# Events


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlotStarted(_Event):
    """A model call to write recipes for a slot has begun. `attempt` counts the slot's calls."""

    kind: Literal["slot_started"] = "slot_started"
    slot_id: str
    days: list[DayKey]
    attempt: int


class SlotWritten(_Event):
    """The model's reply for the slot had the recipes asked for. They have not been checked."""

    kind: Literal["slot_written"] = "slot_written"
    slot_id: str
    days: list[DayKey]
    attempt: int


class RecipeChecked(_Event):
    """A recipe was checked, or its failures changed. `attempt` counts this recipe's tries."""

    kind: Literal["recipe_checked"] = "recipe_checked"
    day: DayKey
    slot_id: str
    attempt: int
    failures: list[Failure]


class RepairStarted(_Event):
    """A model call to rewrite a slot's failing recipes has begun."""

    kind: Literal["repair_started"] = "repair_started"
    slot_id: str
    days: list[DayKey]
    attempt: int


class SlotFailed(_Event):
    """A model call gave nothing usable. `final` is false when the slot will be tried again."""

    kind: Literal["slot_failed"] = "slot_failed"
    slot_id: str
    days: list[DayKey]
    attempt: int
    reason: str
    final: bool


class _NoWasteRow(BaseModel):
    ingredient: str
    window_start: DayKey
    failure: Failure


class WeekResult(BaseModel):
    """The week as the graph leaves it: every recipe with its numbers, and what still fails."""

    model_config = ConfigDict(extra="forbid")

    recipes: list[CheckedRecipe]
    days: list[DayReport]
    # Keyed by the ingredient and the first day of the shopping window. These are warnings: no
    # recipe is to blame, so nothing is repaired for them.
    no_waste: dict[tuple[str, DayKey], Failure]
    # (day, slot id, reason) for each recipe asked for and not delivered. For a replacement the
    # old recipe stays in `recipes`.
    unwritten: list[tuple[DayKey, str, str]]
    calls: int
    input_tokens: int
    output_tokens: int
    # None when the model has no price in LiteLLM's list.
    cost_usd: float | None

    # JSON has no tuple keys, so the warnings travel as a list of rows and come back as a dict.
    @field_validator("no_waste", mode="before")
    @classmethod
    def _rows_to_dict(cls, value: Any) -> Any:
        if isinstance(value, list):
            rows = [_NoWasteRow.model_validate(row) for row in value]
            return {(row.ingredient, row.window_start): row.failure for row in rows}
        return value

    @field_serializer("no_waste", when_used="json")
    def _dict_to_rows(self, value: dict[tuple[str, DayKey], Failure]) -> list[_NoWasteRow]:
        return [
            _NoWasteRow(ingredient=food_id, window_start=start, failure=failure)
            for (food_id, start), failure in value.items()
        ]


class WeekDone(_Event):
    """The last event of every run."""

    kind: Literal["week_done"] = "week_done"
    week: WeekResult


WeekEvent = Annotated[
    SlotStarted | SlotWritten | RecipeChecked | RepairStarted | SlotFailed | WeekDone,
    Field(discriminator="kind"),
]
_EVENTS: TypeAdapter[WeekEvent] = TypeAdapter(WeekEvent)

# Graph state and the work handed to a node


@dataclass(frozen=True)
class _Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


def _add_usage(a: _Usage, b: _Usage) -> _Usage:
    known = [c for c in (a.cost_usd, b.cost_usd) if c is not None]
    return _Usage(
        a.calls + b.calls,
        a.input_tokens + b.input_tokens,
        a.output_tokens + b.output_tokens,
        sum(known) if known else None,
    )


@dataclass(frozen=True)
class _Refusal:
    """Why a call gave no recipes. `final` when asking again cannot help."""

    reason: str
    final: bool
    attempt: int


@dataclass(frozen=True)
class _Job:
    """One model call: write the recipes for `specs`, or rewrite those in `fix`."""

    slot_id: str
    specs: list[SlotSpec]
    attempt: int
    # Why the slot's last reply was refused, for the model to avoid.
    rejected: str | None
    fix: list[CheckedRecipe]
    # The slot's recipes this call leaves alone, which the new ones must stay different from.
    others: list[CheckedRecipe]


@dataclass(frozen=True)
class _Send:
    """What a node that makes a model call receives. LangGraph names its input type's fields as
    channels, so the job travels inside one field that no state key shares."""

    job: _Job


def _merge[K, V](old: dict[K, V], new: dict[K, V]) -> dict[K, V]:
    return {**old, **new}


class _State(TypedDict):
    # The week to keep (a replacement is made inside the rest of the week); empty for a new week.
    previous: dict[_RecipeKey, CheckedRecipe]
    specs: dict[_RecipeKey, SlotSpec]
    # The recipes this run may write or rewrite.
    scope: frozenset[_RecipeKey]
    recipes: Annotated[dict[_RecipeKey, CheckedRecipe], _merge]
    # Recipes written since the last check, which need their numbers and an event.
    fresh: Annotated[list[_RecipeKey], operator.add]
    refused: Annotated[dict[_RecipeKey, _Refusal], _merge]
    unwritten: dict[_RecipeKey, str]
    usage: Annotated[_Usage, _add_usage]
    work: list[_Job]
    # How many rounds after the first have been started.
    round: int
    days: list[DayReport]
    no_waste: dict[tuple[str, DayKey], Failure]


@dataclass(frozen=True)
class _Deps:
    """What every node needs and none changes: it is passed as the graph's runtime context."""

    rules: PlanRules
    model: ModelPort
    nutrition: NutritionPort
    catalogue: Catalogue
    settings: Settings
    # Set when one recipe is being replaced.
    only: _RecipeKey | None
    # Every model call waits here, so the slots do not all hit a rate limit at once.
    gate: asyncio.Semaphore
    allowed: list[CatalogueItem]


# Prompts


@cache
def _template(name: str) -> str:
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


def _system(name: str, allowed: list[CatalogueItem], hints: bool) -> str:
    values = {
        "catalogue": "\n".join(_catalogue_line(item, hints) for item in allowed),
        "catalogue_format": _FORMAT_WITH_HINTS if hints else _FORMAT,
        "per_100g_note": _PER_100G_NOTE if hints else "",
    }
    # One pass, so text that looks like a placeholder inside a value is not replaced again.
    return re.sub(
        r"\{\{(catalogue|catalogue_format|per_100g_note)\}\}",
        lambda m: values[m[1]],
        _template(name),
    )


def _quote(text: str) -> str:
    """The text as a JSON string with angle brackets escaped, so it cannot close the data block."""
    return json.dumps(text, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")


def _ident(text: str) -> str:
    return text if re.fullmatch(r"[a-z][a-z0-9_]*", text) else _quote(text)


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


def _slot_lines(deps: _Deps, slot: Slot, specs: Sequence[SlotSpec]) -> list[str]:
    """The slot, the plan's kitchen and tastes, and the target for each day, as data lines."""
    food = deps.rules.food
    day_types = {d.id: d for d in deps.rules.day_types}

    def day_line(spec: SlotSpec) -> str:
        line = f"- {spec.day}: {_target(spec, deps.settings)}"
        if not deps.settings.writer_nutrient_hints:
            return line
        limits = _share_of_limits(day_types[spec.day_type], slot.kcal_share)
        return f"{line}; the slot's share of the day's limits: {limits}"

    return [
        f"slot_id: {_quote(slot.id)}",
        f"slot_name: {_quote(slot.name)}",
        f"style: {_quote(slot.style)}",
        f"cook_when: {slot.cook_when}",
        f"max_active_min: {slot.max_active_min}",
        f"max_total_min: {slot.max_total_min}",
        f"diet: {food.diet}",
        f"gear: {', '.join(deps.rules.gear)}",
        f"cuisines: {', '.join(_quote(c) for c in food.cuisines)}",
        f"liked_foods: {', '.join(_quote(f) for f in food.liked_foods) or 'none'}",
        "days:",
        *(day_line(spec) for spec in specs),
    ]


def _summary(recipe: CheckedRecipe) -> str:
    draft = recipe.draft
    ids = ", ".join(_ident(i.id) for i in draft.ingredients)
    return (
        f"- {recipe.spec.day}: {_quote(draft.title)}, cuisine {_quote(draft.cuisine)}, "
        f"ingredients {ids}"
    )


def _fix_lines(recipe: CheckedRecipe) -> list[str]:
    draft = recipe.draft
    grams = ", ".join(f"{_ident(i.id)} {i.grams:g} g" for i in draft.ingredients)
    lines = [
        f"- day: {recipe.spec.day}",
        f"  title: {_quote(draft.title)}",
        f"  cuisine: {_quote(draft.cuisine)}",
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
        *(f"  - {_quote(failure.message)}" for failure in recipe.failures),
    ]


def _user(deps: _Deps, job: _Job, closing: str) -> str:
    slot = next(s for s in deps.rules.slots if s.id == job.slot_id)
    lines = _slot_lines(deps, slot, job.specs)
    if job.fix:
        lines += ["recipes to fix:", *(line for r in job.fix for line in _fix_lines(r))]
    if job.others:
        lines += [
            "other recipes in this slot this week (the new ones must differ from these):",
            *(_summary(r) for r in job.others),
        ]
    text = "\n".join(["<data>", *lines, "</data>"])
    if job.rejected:
        text += f"\nYour previous reply was rejected: {_quote(job.rejected)}. Fix that."
    return f"{text}\n{closing}"


def _writer_prompt(deps: _Deps, job: _Job) -> tuple[str, str]:
    n = len(job.specs)
    closing = f'Write {n} recipe{"" if n == 1 else "s"}, one for each day under "days".'
    system = _system("writer.md", deps.allowed, deps.settings.writer_nutrient_hints)
    return system, _user(deps, job, closing)


def _repair_prompt(deps: _Deps, job: _Job) -> tuple[str, str]:
    n = len(job.fix)
    closing = f'Fix the {n} recipe{"" if n == 1 else "s"} under "recipes to fix", same days.'
    system = _system("repair.md", deps.allowed, deps.settings.writer_nutrient_hints)
    return system, _user(deps, job, closing)


# Reading a reply


def _why_unreadable(raw: str) -> str:
    """Where a reply that does not fit the format goes wrong, without repeating what it says."""
    problems: list[str] = []
    try:
        SlotDraft.model_validate_json(raw)
    except ValidationError as exc:
        found = exc.errors(include_url=False, include_input=False, include_context=False)
        if any(e["type"] == "json_invalid" for e in found):
            return "the reply was not valid JSON"
        problems = [f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in found[:3]]
    return "the reply did not fit the format: " + "; ".join(problems)


def _wrong_recipes(draft: SlotDraft, job: _Job) -> str | None:
    """What is wrong with the recipes for the days asked, or None when they are the ones asked."""
    if draft.slot_id != job.slot_id:
        return f"the reply is for slot '{draft.slot_id[:40]}' but '{job.slot_id}' was asked for"
    asked = [spec.day for spec in job.specs]
    got = [recipe.day for recipe in draft.recipes]
    missing = [day for day in asked if day not in got]
    repeated = sorted({day for day in got if got.count(day) > 1})
    unasked = sorted({day for day in got if day not in asked})
    if not (missing or repeated or unasked) and len(got) == len(asked):
        return None
    head = (
        f"the reply has {len(got)} recipes but {len(asked)} were asked for"
        if len(got) != len(asked)
        else "the reply's recipes are not for the days asked"
    )
    details = [
        f"{label}: {', '.join(days)}"
        for label, days in (
            ("missing", missing),
            ("repeated", repeated),
            ("not asked for", unasked),
        )
        if days
    ]
    return f"{head} ({'; '.join(details)})" if details else head


def _read(
    reply: ModelReply[SlotDraft], job: _Job, last_try: bool
) -> tuple[list[DraftRecipe], None] | tuple[None, _Refusal]:
    """The recipes in a reply, or why there are none."""
    if reply.error in (INVALID_JSON, SCHEMA_MISMATCH):
        return None, _Refusal(_why_unreadable(reply.raw), last_try, job.attempt)
    if reply.value is None:
        # A rate limit, a timeout or a server error, which the model client has already retried.
        return None, _Refusal(reply.error or "no_reply", True, job.attempt)
    if problem := _wrong_recipes(reply.value, job):
        return None, _Refusal(problem, last_try, job.attempt)
    return reply.value.recipes, None


async def _ask(
    deps: _Deps, task: str, prompt: tuple[str, str], job: _Job, started: WeekEvent
) -> tuple[list[DraftRecipe], None, _Usage] | tuple[None, _Refusal, _Usage]:
    """One model call, at most `max_concurrent_model_calls` at a time, read into recipes."""
    system, user = prompt
    emit = get_stream_writer()
    async with deps.gate:
        emit(started)
        reply = await deps.model.structured(
            task=task,
            system=system,
            user=user,
            schema=SlotDraft,
            timeout_s=deps.settings.slot_call_timeout_s,
        )
    usage = _Usage(1, reply.input_tokens, reply.output_tokens, reply.cost_usd)
    days = [spec.day for spec in job.specs]
    last_try = job.attempt > deps.settings.max_repair_attempts
    recipes, refusal = _read(reply, job, last_try)
    if refusal is not None:
        emit(
            SlotFailed(
                slot_id=job.slot_id,
                days=days,
                attempt=job.attempt,
                reason=refusal.reason,
                final=refusal.final,
            )
        )
        return None, refusal, usage
    assert recipes is not None
    emit(SlotWritten(slot_id=job.slot_id, days=days, attempt=job.attempt))
    return recipes, None, usage


# Nodes


async def _expand(state: _State, *, runtime: Runtime[_Deps]) -> dict[str, Any]:
    deps = runtime.context
    specs = {(spec.day, spec.slot.id): spec for spec in expand(deps.rules)}
    scope = frozenset(specs) if deps.only is None else frozenset({deps.only})
    kept = {key: recipe for key, recipe in state["previous"].items() if key not in scope}
    work = [
        _Job(
            slot_id=slot.id,
            specs=[specs[(day, slot.id)] for day in DAYS if (day, slot.id) in scope],
            attempt=1,
            rejected=None,
            fix=[],
            others=[kept[(day, slot.id)] for day in DAYS if (day, slot.id) in kept],
        )
        for slot in deps.rules.slots
        if any((day, slot.id) in scope for day in DAYS)
    ]
    return {"specs": specs, "scope": scope, "recipes": kept, "work": work}


async def _write_slot(send: _Send, *, runtime: Runtime[_Deps]) -> dict[str, Any]:
    deps, job = runtime.context, send.job
    days = [spec.day for spec in job.specs]
    started = SlotStarted(slot_id=job.slot_id, days=days, attempt=job.attempt)
    drafts, refusal, usage = await _ask(deps, "write_slot", _writer_prompt(deps, job), job, started)
    if drafts is None:
        return {"refused": {(spec.day, job.slot_id): refusal for spec in job.specs}, "usage": usage}
    by_day = {spec.day: spec for spec in job.specs}
    written = {
        (draft.day, job.slot_id): CheckedRecipe(
            spec=by_day[draft.day], draft=draft, nutrition=None, failures=[], attempts=job.attempt
        )
        for draft in drafts
    }
    return {"recipes": written, "fresh": list(written), "usage": usage}


async def _repair_slot(send: _Send, *, runtime: Runtime[_Deps]) -> dict[str, Any]:
    deps, job = runtime.context, send.job
    started = RepairStarted(
        slot_id=job.slot_id, days=[spec.day for spec in job.specs], attempt=job.attempt
    )
    drafts, refusal, usage = await _ask(
        deps, "repair_slot", _repair_prompt(deps, job), job, started
    )
    old = {recipe.spec.day: recipe for recipe in job.fix}
    if drafts is None:
        # The recipes stay as they were, one try used up, and the next call hears why.
        return {
            "recipes": {
                (day, job.slot_id): r.model_copy(update={"attempts": r.attempts + 1})
                for day, r in old.items()
            },
            "refused": {(day, job.slot_id): refusal for day in old},
            "usage": usage,
        }
    rewritten = {
        (draft.day, job.slot_id): CheckedRecipe(
            spec=old[draft.day].spec,
            draft=draft,
            nutrition=None,
            failures=[],
            attempts=old[draft.day].attempts + 1,
        )
        for draft in drafts
    }
    return {"recipes": rewritten, "fresh": list(rewritten), "usage": usage}


async def with_numbers(
    recipe: CheckedRecipe,
    *,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    raise_if_unreachable: bool = False,
) -> CheckedRecipe:
    """The recipe with its numbers from nutrition-mcp, or with the reason it has none.

    A recipe nutrition-mcp refuses gets no numbers and a `nutrition_failed` failure. When
    nutrition-mcp could not be asked at all (the error names no food), the graph treats that the
    same way, so one bad moment fails recipes and not the week. A caller that has no week to
    protect passes `raise_if_unreachable` to get the error instead.
    """
    ingredients = recipe.draft.ingredients
    if any(i.id not in catalogue.items for i in ingredients):
        # The catalogue check names the id; nutrition-mcp is not asked about it.
        return recipe
    items = [MealItemIn(food_id=i.id, amount=i.grams, unit="g") for i in ingredients]
    try:
        meal = await nutrition.meal(items)
    except NutritionError as exc:
        if raise_if_unreachable and exc.food_id is None:
            raise
        failure = Failure(
            check=NUTRITION_FAILED, message=f"nutrition-mcp could not work out this recipe: {exc}"
        )
        return recipe.model_copy(update={"failures": [failure]})
    return recipe.model_copy(update={"nutrition": meal})


async def _numbered(recipe: CheckedRecipe, deps: _Deps) -> CheckedRecipe:
    """The recipe with its numbers, scaled to its slot's calories first if that is called for.

    Scaling changes grams only. The scaled recipe is worked out again by nutrition-mcp, so every
    number still comes from it. If that second call fails, the recipe stays as the writer gave
    it, with the numbers it already has: fitting is an improvement, never a reason to fail.
    """
    numbered = await with_numbers(recipe, nutrition=deps.nutrition, catalogue=deps.catalogue)
    if not deps.settings.fit_portions:
        return numbered
    factor = portion_factor(numbered, deps.settings)
    if factor is None:
        return numbered
    fitted = numbered.model_copy(
        update={
            "draft": scale_draft(numbered.draft, factor),
            "nutrition": None,
            "portion_scale": factor,
        }
    )
    fitted = await with_numbers(fitted, nutrition=deps.nutrition, catalogue=deps.catalogue)
    return fitted if fitted.nutrition is not None else numbered


async def _compute(state: _State, *, runtime: Runtime[_Deps]) -> dict[str, Any]:
    # Only recipes written or rewritten in this run are in `fresh`, so the ones a replacement
    # keeps are never scaled.
    keys = state["fresh"]
    deps = runtime.context
    numbered = await asyncio.gather(*(_numbered(state["recipes"][key], deps) for key in keys))
    return {"recipes": dict(zip(keys, numbered, strict=True))}


@dataclass(frozen=True)
class _Evaluation:
    recipes: dict[_RecipeKey, CheckedRecipe]
    days: list[DayReport]
    no_waste: dict[tuple[str, DayKey], Failure]


def _ref(recipe: CheckedRecipe) -> str:
    """How `check_variety` names the earlier recipe of a pair in its messages."""
    return f"the {recipe.spec.day} recipe '{recipe.draft.title}'"


def _seen_from(failure: Failure, new: CheckedRecipe, other: CheckedRecipe) -> Failure:
    """A clash `check_variety` put on `other`, which comes later, worded for `new`, the earlier."""
    if failure.check == "variety_cuisine":
        message = (
            f"cuisine '{other.draft.cuisine}' repeats the day after ({other.spec.day}); "
            "the plan lists other cuisines, so use a different one"
        )
    else:
        message = failure.message.replace(_ref(new), _ref(other))
    return Failure(check=failure.check, message=message)


def _variety(
    recipes: dict[_RecipeKey, CheckedRecipe], deps: _Deps, repairable: frozenset[_RecipeKey]
) -> dict[_RecipeKey, list[Failure]]:
    """The variety failures, each on a recipe this run is able to rewrite.

    `check_variety` puts a clash on the later recipe of the pair. When one recipe is written
    again among others that stay, a clash with a later one would land on a recipe nobody
    rewrites, and the new one would pass. So the kept recipes are checked without the new one,
    and a failure that only appears with it moves to it, worded as seen from the new recipe.
    """
    rules, s = deps.rules, deps.settings
    found = check_variety(list(recipes.values()), rules, s)
    kept = {key: recipe for key, recipe in recipes.items() if key not in repairable}
    if not kept or len(kept) == len(recipes):
        return found
    without_new = check_variety(list(kept.values()), rules, s)
    for key in kept:
        old = list(without_new.get(key, []))
        stay: list[Failure] = []
        added: list[Failure] = []
        for failure in found.get(key, []):
            if failure in old:
                old.remove(failure)
                stay.append(failure)
            else:
                added.append(failure)
        # Variety is within a slot, so the new recipe is the one of this slot that can change.
        new_keys = [k for k in recipes if k in repairable and k[1] == key[1]]
        if added and len(new_keys) == 1:
            found[key] = stay
            found.setdefault(new_keys[0], []).extend(
                _seen_from(f, recipes[new_keys[0]], recipes[key]) for f in added
            )
    return found


def _evaluate(
    recipes: dict[_RecipeKey, CheckedRecipe], deps: _Deps, repairable: frozenset[_RecipeKey]
) -> _Evaluation:
    """Every check on the week as it stands, with the failures each recipe is to fix.

    Failures only go to recipes in `repairable`, the ones this run may rewrite: a replacement
    is checked inside the rest of the week, and what clashes with the rest is its to fix. A day
    that misses its targets is blamed on one of them, once per failed check, only when every
    slot of the day is there and none of them fails on its own: those are rewritten first,
    and the day is then worked out again.
    """
    rules, s = deps.rules, deps.settings
    found: dict[_RecipeKey, list[Failure]] = {}
    for key, recipe in recipes.items():
        # A refusal is only carried while there are no numbers.
        failures = (
            []
            if recipe.nutrition is not None
            else [f for f in recipe.failures if f.check == NUTRITION_FAILED]
        )
        failures += check_catalogue(recipe.draft, deps.catalogue, rules)
        if recipe.nutrition is not None:
            failures += check_slot_targets(recipe.nutrition, recipe.spec, s)
        failures += check_gear_and_time(recipe.draft, rules, recipe.spec.slot)
        found[key] = failures
    for key, failures in _variety(recipes, deps, repairable).items():
        found[key] += failures

    day_types = {d.id: d for d in rules.day_types}
    reports: list[DayReport] = []
    for day in DAYS:
        todays = {key: r for key, r in recipes.items() if r.spec.day == day}
        day_type = day_types[rules.week[day]]
        report = check_day(day, list(todays.values()), day_type, s)
        reports.append(report)
        mine = {key: r for key, r in todays.items() if key in repairable}
        whole = len(todays) == len(rules.slots)
        if report.failures and whole and mine and not any(found[key] for key in mine):
            for failure in report.failures:
                one = report.model_copy(update={"failures": [failure]})
                worst = worst_recipe_for_day(one, list(mine.values()), day_type)
                found[(worst.spec.day, worst.spec.slot.id)].append(failure)

    return _Evaluation(
        recipes={key: r.model_copy(update={"failures": found[key]}) for key, r in recipes.items()},
        days=reports,
        no_waste=check_no_waste(list(recipes.values()), deps.catalogue, rules, s),
    )


def _rank(deps: _Deps) -> Callable[[_RecipeKey], int]:
    """A recipe's place in the week: by day, then by the plan's order of slots."""
    slot_ids = [slot.id for slot in deps.rules.slots]

    def rank(key: _RecipeKey) -> int:
        day, slot_id = key
        slot = slot_ids.index(slot_id) if slot_id in slot_ids else len(slot_ids)
        return DAYS.index(day) * (len(slot_ids) + 1) + slot

    return rank


def _settle_unwritten(
    state: _State, deps: _Deps, attempt: int, rounds_left: bool
) -> tuple[list[_Job], dict[_RecipeKey, str], dict[_RecipeKey, CheckedRecipe]]:
    """Ask again for a slot with no recipes, or give up on it and say why.

    Giving up on a replacement puts the old recipe back, so a failed replacement never takes a
    recipe out of the week.
    """
    rank = _rank(deps)
    jobs: list[_Job] = []
    gave_up: dict[_RecipeKey, str] = {}
    restored: dict[_RecipeKey, CheckedRecipe] = {}
    for slot in deps.rules.slots:
        missing = sorted(
            (k for k in state["scope"] if k[1] == slot.id and k not in state["recipes"]), key=rank
        )
        if not missing:
            continue
        refusal = state["refused"].get(missing[0]) or _Refusal("not written", True, attempt - 1)
        if rounds_left and not refusal.final:
            others = [
                state["recipes"][(day, slot.id)]
                for day in DAYS
                if (day, slot.id) in state["recipes"]
            ]
            specs = [state["specs"][key] for key in missing]
            jobs.append(_Job(slot.id, specs, attempt, refusal.reason, [], others))
            continue
        for key in missing:
            gave_up[key] = refusal.reason
            if key in state["previous"]:
                restored[key] = state["previous"][key]
    return jobs, gave_up, restored


def _repair_jobs(
    state: _State,
    deps: _Deps,
    evaluation: _Evaluation,
    repairable: frozenset[_RecipeKey],
    attempt: int,
    busy: set[str],
) -> list[_Job]:
    """One job for each slot not already being written whose recipes still fail."""
    rank = _rank(deps)
    jobs: list[_Job] = []
    for slot in deps.rules.slots:
        failing = [
            evaluation.recipes[key]
            for key in sorted(repairable, key=rank)
            if key[1] == slot.id and key in evaluation.recipes and evaluation.recipes[key].failures
        ]
        # A call that gave nothing because the model could not be reached was already retried
        # by the model client: leave the slot, and its recipes keep the failures they have.
        gave_out = any(r.final for key, r in state["refused"].items() if key[1] == slot.id)
        if slot.id in busy or gave_out or not failing:
            continue
        fixing = {(r.spec.day, slot.id) for r in failing}
        others = [
            evaluation.recipes[(day, slot.id)]
            for day in DAYS
            if (day, slot.id) in evaluation.recipes and (day, slot.id) not in fixing
        ]
        # If the last call for this slot was refused for what it said, the next one hears why.
        refusals = (state["refused"].get((r.spec.day, slot.id)) for r in failing)
        rejected = next((f.reason for f in refusals if f and f.attempt == attempt - 1), None)
        jobs.append(_Job(slot.id, [r.spec for r in failing], attempt, rejected, failing, others))
    return jobs


def _announce(state: _State, deps: _Deps, evaluation: _Evaluation) -> None:
    """Say which recipes were checked for the first time, or whose failures have changed."""
    emit = get_stream_writer()
    fresh = set(state["fresh"])
    for key in sorted(evaluation.recipes, key=_rank(deps)):
        new, old = evaluation.recipes[key], state["recipes"].get(key)
        if key in fresh or (old is not None and old.failures != new.failures):
            emit(
                RecipeChecked(
                    day=key[0], slot_id=key[1], attempt=new.attempts, failures=new.failures
                )
            )


async def _check(state: _State, *, runtime: Runtime[_Deps]) -> dict[str, Any]:
    deps = runtime.context
    rounds_left = state["round"] < deps.settings.max_repair_attempts
    # The first round was the writing, so the next call is the slot's second at the earliest.
    attempt = state["round"] + 2

    work, gave_up, restored = _settle_unwritten(state, deps, attempt, rounds_left)
    unwritten = {**state["unwritten"], **gave_up}
    repairable = state["scope"] - unwritten.keys()
    evaluation = _evaluate({**state["recipes"], **restored}, deps, repairable)
    _announce(state, deps, evaluation)
    if rounds_left:
        busy = {job.slot_id for job in work}
        work += _repair_jobs(state, deps, evaluation, repairable, attempt, busy)

    return {
        "recipes": evaluation.recipes,
        "days": evaluation.days,
        "no_waste": evaluation.no_waste,
        "unwritten": unwritten,
        "fresh": Overwrite([]),
        "work": work,
        "round": state["round"] + (1 if work else 0),
    }


async def _report(state: _State, *, runtime: Runtime[_Deps]) -> dict[str, Any]:
    rank = _rank(runtime.context)
    usage = state["usage"]
    week = WeekResult(
        recipes=[state["recipes"][key] for key in sorted(state["recipes"], key=rank)],
        days=state["days"],
        no_waste=state["no_waste"],
        unwritten=[
            (day, slot_id, state["unwritten"][(day, slot_id)])
            for day, slot_id in sorted(state["unwritten"], key=rank)
        ],
        calls=usage.calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
    )
    get_stream_writer()(WeekDone(week=week))
    return {}


def _route(state: _State) -> list[Send] | Literal["report"]:
    if not state["work"]:
        return "report"
    return [
        Send("repair_slot" if job.fix else "write_slot", _Send(job=job)) for job in state["work"]
    ]


def _build() -> CompiledStateGraph[_State, _Deps, _State, _State]:
    graph = StateGraph(_State, context_schema=_Deps)
    graph.add_node("expand", _expand)
    # LangGraph's type stubs only accept a node that takes the graph's own state, and these two
    # take the payload of a Send. At run time that is supported and tested.
    graph.add_node("write_slot", _write_slot)  # type: ignore[call-overload]
    graph.add_node("repair_slot", _repair_slot)  # type: ignore[call-overload]
    graph.add_node("compute", _compute)
    graph.add_node("check", _check)
    graph.add_node("report", _report)
    graph.add_edge(START, "expand")
    graph.add_conditional_edges("expand", _route, ["write_slot", "report"])
    graph.add_edge("write_slot", "compute")
    graph.add_edge("repair_slot", "compute")
    graph.add_edge("compute", "check")
    graph.add_conditional_edges("check", _route, ["write_slot", "repair_slot", "report"])
    graph.add_edge("report", END)
    return graph.compile()


_GRAPH = _build()

# Running it


async def _stream(
    rules: PlanRules,
    previous: dict[_RecipeKey, CheckedRecipe],
    only: _RecipeKey | None,
    *,
    model: ModelPort,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
) -> AsyncIterator[WeekEvent]:
    deps = _Deps(
        rules=rules,
        model=model,
        nutrition=nutrition,
        catalogue=catalogue,
        settings=settings,
        only=only,
        gate=asyncio.Semaphore(settings.max_concurrent_model_calls),
        allowed=catalogue.allowed_for(rules),
    )
    start: _State = {
        "previous": previous,
        "specs": {},
        "scope": frozenset(),
        "recipes": {},
        "fresh": [],
        "refused": {},
        "unwritten": {},
        "usage": _Usage(),
        "work": [],
        "round": 0,
        "days": [],
        "no_waste": {},
    }
    # Each round is three steps (call, compute, check), and LangGraph stops a run at 25 steps.
    config: RunnableConfig = {"recursion_limit": 3 * settings.max_repair_attempts + 15}
    async for event in _GRAPH.astream(start, config, context=deps, stream_mode="custom"):
        yield _EVENTS.validate_python(event)


async def run_week(
    rules: PlanRules,
    *,
    model: ModelPort,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
) -> AsyncIterator[WeekEvent]:
    """Write, check and repair a week for the plan, yielding progress events.

    The last event is always `week_done`, also when slots could not be written.
    """
    async for event in _stream(
        rules, {}, None, model=model, nutrition=nutrition, catalogue=catalogue, settings=settings
    ):
        yield event


async def replace_recipe(
    rules: PlanRules,
    week: WeekResult,
    day: DayKey,
    slot_id: str,
    *,
    model: ModelPort,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
) -> AsyncIterator[WeekEvent]:
    """Write one recipe again inside the rest of the week, yielding progress events.

    The other recipes are not sent to the model or changed. They are there for the day and
    variety checks and to show the writer what to stay different from. The old recipe leaves the
    week before the new one is checked, and stays in the result if no new one could be written.
    """
    if slot_id not in {slot.id for slot in rules.slots}:
        raise ValueError(f"the plan has no slot '{slot_id}'")
    previous = {(r.spec.day, r.spec.slot.id): r for r in week.recipes}
    async for event in _stream(
        rules,
        previous,
        (day, slot_id),
        model=model,
        nutrition=nutrition,
        catalogue=catalogue,
        settings=settings,
    ):
        yield event
