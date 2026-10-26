"""Reads a meal a person typed into nutrition-mcp foods, amounts and numbers.

The model's part is small. It lists the foods the text names with the amount and unit as typed
(task `read_meal`), and when nutrition-mcp's search finds several foods for a name and the top
one is not that name, it picks one by number (task `choose_food`). It is never asked for a
nutrition figure, and nothing it adds to a reply is used. Code decides the rest:

- A food is nutrition-mcp's top search result when it is the only one or its name, an alias or
  its id is the name typed; with several, the model picks one; a name with no result, or a pick
  that is none of the results, is `missing` and left out of the total.
- An amount that is missing (no words for it in the text), vague ("some", "a handful") or a range
  ("2-3") is `needs_amount` whatever number the model returned. The person is asked, never given
  a guess.
- A unit nutrition-mcp has for the food goes to it. Otherwise the catalogue's measure for the
  food turns the amount into grams, if it has one; the summary reads a logged unit the same way
  (`Catalogue.meal_item`). Otherwise the item is `unknown_unit`, with the units that would work.
  No measure is made up: "2 pav" has no sourced weight, so it stays unknown.
- Every number comes from nutrition-mcp. The meal's total adds the matched items only.

The text goes to the model as data, quoted so that it cannot close the data block. Nothing here
logs it, and a ReadError names the step that failed and nothing the person wrote.
"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from nutrition_mcp.schemas import FoodSummary, MealItemIn, MealResult, NutritionResult
from nutrition_mcp.search import MAX_QUERY_LENGTH, normalise
from pydantic import BaseModel, Field

from meal_planner.catalogue import Catalogue
from meal_planner.config import Settings
from meal_planner.llm import MODEL_ERROR, RATE_LIMITED, SERVER_ERROR, TIMEOUT, ModelPort, ModelReply
from meal_planner.log import get_logger
from meal_planner.nutrition import NutritionError, NutritionPort
from meal_planner.week_prompts import quote, template

logger = get_logger(__name__)

# The most foods one text may name.
MAX_ITEMS = 20
# Foods nutrition-mcp's search returns for a name, and so the most the model chooses between.
OPTIONS = 5
# The unit of a number with no unit word: "2 eggs" is two pieces.
COUNTED = "piece"
# More than this of any unit is over nutrition-mcp's 5000 g limit. The bound also keeps the
# arithmetic finite.
MAX_AMOUNT = 100_000

# A range of amounts: "2-3", "2 to 3", "2 or 3", "two or three". The dashes are a hyphen, an en
# dash and an em dash, as people type them between two numbers.
_NUMBER = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"
_DASHES = "-" + chr(0x2013) + chr(0x2014)
_RANGE = re.compile(rf"\b{_NUMBER}(?:\s*[{_DASHES}]\s*|\s+(?:to|or)\s+){_NUMBER}\b", re.IGNORECASE)
# Words for an amount that is not a number: "some", "a little", "a bit", "a few", "a handful".
_VAGUE = re.compile(r"\b(?:some|little|bit|few|handfuls?|lots?)\b", re.IGNORECASE)

Status = Literal["matched", "needs_amount", "unknown_unit", "missing"]


class ExtractedItem(BaseModel):
    """One food as the model read it from the text. None of it is a nutrition figure."""

    name: str
    amount: float | None
    unit: str | None
    # The words the person used for the amount, which the code reads for a range or a vague word.
    amount_text: str


class Extracted(BaseModel):
    items: list[ExtractedItem] = Field(max_length=MAX_ITEMS)


class Choice(BaseModel):
    """The number of the search result that is the food, or None when none of them is."""

    index: int | None


class ReadItem(BaseModel):
    """One food of the meal and what became of it.

    `matched`: the food and a usable amount were found, and `grams` and `nutrition` are set.
    `needs_amount`: the food was found but the amount is missing, vague, a range or beyond the
    limit; `valid_units` lists what could be used to give one. `unknown_unit`: the food was
    found but the unit is not one nutrition-mcp or the catalogue has for it; `valid_units` lists
    those that are. `missing`: the food is not in nutrition-mcp's data.
    """

    # As the model read it, in the person's words.
    name: str
    food_id: str | None
    food_name: str | None
    # The amount and unit that were used, as typed; None when the amount is not known.
    amount: float | None
    unit: str | None
    grams: float | None
    status: Status
    valid_units: list[str]
    nutrition: NutritionResult | None


class ReadResult(BaseModel):
    text_length: int
    items: list[ReadItem]
    # The matched items added up by nutrition-mcp; None when none matched.
    total: MealResult | None
    # The names of the foods nutrition-mcp does not have, in the order written.
    missing: list[str]


class ReadError(Exception):
    """The meal could not be read. The message names the step and holds nothing that was typed.

    `unavailable` is set when a service could not be reached, so that trying again later can
    help; otherwise a service answered with something that could not be used.
    """

    def __init__(self, message: str, *, unavailable: bool) -> None:
        super().__init__(message)
        self.unavailable = unavailable


@dataclass(frozen=True)
class _Deps:
    model: ModelPort
    nutrition: NutritionPort
    catalogue: Catalogue
    settings: Settings


async def read_meal(
    text: str,
    *,
    model: ModelPort,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
) -> ReadResult:
    """The foods in `text`, each with its amount in grams and nutrition-mcp's numbers.

    The caller has checked the text's length. Raises ReadError when the model or nutrition-mcp
    cannot be used.
    """
    deps = _Deps(model, nutrition, catalogue, settings)
    prompt = "\n".join(["<data>", f"text: {quote(text)}", "</data>", "List the foods in the text."])
    extracted = await _ask(
        deps, task="read_meal", system=template("reader.md"), user=prompt, schema=Extracted
    )
    items: list[ReadItem] = []
    to_total: list[MealItemIn] = []
    for item in extracted.items:
        # A food with no name is nothing the person typed.
        if item.name.strip():
            read, meal_item = await _read_item(item, deps)
            items.append(read)
            if meal_item is not None:
                to_total.append(meal_item)
    return ReadResult(
        text_length=len(text),
        items=items,
        total=await _total(to_total, deps) if to_total else None,
        missing=[item.name for item in items if item.status == "missing"],
    )


async def _read_item(item: ExtractedItem, deps: _Deps) -> tuple[ReadItem, MealItemIn | None]:
    """The item, and what the meal's total is to ask nutrition-mcp about it if it matched."""
    name = item.name.strip()
    typed_unit = (item.unit or "").strip() or None
    amount = _stated_amount(item)
    food = await _find_food(name, deps)
    if food is None:
        return _unread(name, None, amount, typed_unit, "missing"), None
    valid = _valid_units(food, deps.catalogue, food.units)
    if amount is None:
        return _unread(name, food, None, typed_unit, "needs_amount", valid), None
    unit = typed_unit or COUNTED
    # The search listed the food's units, so the unit is settled before nutrition-mcp is asked.
    meal_item = deps.catalogue.meal_item(food.id, amount, unit, food.units)
    if meal_item is None:
        return _unread(name, food, amount, unit, "unknown_unit", valid), None
    answer = await _nutrition_for(deps, meal_item)
    if isinstance(answer, NutritionError):
        # The amount itself was refused: more than 5000 g.
        return _unread(name, food, None, unit, "needs_amount", valid), None
    read = ReadItem(
        name=name,
        food_id=food.id,
        food_name=food.name,
        amount=amount,
        unit=unit,
        grams=answer.grams,
        status="matched",
        valid_units=[],
        nutrition=answer,
    )
    return read, meal_item


def _unread(
    name: str,
    food: FoodSummary | None,
    amount: float | None,
    unit: str | None,
    status: Status,
    valid_units: Iterable[str] = (),
) -> ReadItem:
    return ReadItem(
        name=name,
        food_id=food.id if food else None,
        food_name=food.name if food else None,
        amount=amount,
        unit=unit,
        grams=None,
        status=status,
        valid_units=list(valid_units),
        nutrition=None,
    )


def _stated_amount(item: ExtractedItem) -> float | None:
    """The amount the person gave, or None when they gave none a meal can be worked out from.

    Decided here and not by the model: no words for an amount ("roti and dal"), a range or a
    vague word in them cancels whatever number the model chose.
    """
    amount = item.amount
    if amount is None or not math.isfinite(amount) or not 0 < amount <= MAX_AMOUNT:
        return None
    words = item.amount_text.strip()
    if not words or _VAGUE.search(words) or _RANGE.search(words):
        return None
    return amount


def _valid_units(food: FoodSummary, catalogue: Catalogue, own: Iterable[str]) -> list[str]:
    """`own`, the units nutrition-mcp has for the food, then the catalogue's measures for it."""
    units = list(own)
    row = catalogue.items.get(food.id)
    return [*units, *(unit for unit in (row.measures if row else {}) if unit not in units)]


# Finding the food


async def _find_food(name: str, deps: _Deps) -> FoodSummary | None:
    if not normalise(name) or len(name) > MAX_QUERY_LENGTH:
        # nutrition-mcp refuses a query with no letter or digit in it, or a long one. No food
        # has such a name.
        return None
    try:
        found = await deps.nutrition.search(name, limit=OPTIONS)
    except NutritionError as exc:
        raise _nutrition_failed(exc) from exc
    options = found.results
    if not options:
        return None
    if len(options) == 1 or _is_named(options[0], name):
        return options[0]
    return await _choose(name, options, deps)


def _is_named(food: FoodSummary, name: str) -> bool:
    """Whether `name` is one of the food's names or its id, spelled as nutrition-mcp reads them."""
    return normalise(name) in {normalise(n) for n in (food.name, food.id, *food.aliases)}


async def _choose(name: str, options: list[FoodSummary], deps: _Deps) -> FoodSummary | None:
    """The option the model says is the food named, or None."""
    lines = [f"{number}: {_describe(food)}" for number, food in enumerate(options)]
    user = "\n".join(
        ["<data>", f"food: {quote(name)}", "options:", *lines, "</data>", "Choose the food."]
    )
    choice = await _ask(
        deps, task="choose_food", system=template("choose_food.md"), user=user, schema=Choice
    )
    index = choice.index
    # A number outside the list is no choice. Python would read -1 as the last one.
    return options[index] if index is not None and 0 <= index < len(options) else None


def _describe(food: FoodSummary) -> str:
    others = ", ".join(quote(alias) for alias in food.aliases)
    return f"{quote(food.name)}, {food.kind}" + (f", also called {others}" if others else "")


# Asking the services


async def _ask[T: BaseModel](
    deps: _Deps, *, task: str, system: str, user: str, schema: type[T]
) -> T:
    """One model call read as `schema`, or a ReadError that names the step."""
    reply: ModelReply[T]
    try:
        reply = await deps.model.structured(
            task=task,
            system=system,
            user=user,
            schema=schema,
            timeout_s=deps.settings.reader_call_timeout_s,
        )
    except Exception as exc:
        # The model port promises a reply, never an exception. If one comes anyway it is a model
        # that failed. Only the class is logged: the message could hold the prompt.
        logger.error("model_call_raised", extra={"task": task, "error_type": type(exc).__name__})
        reply = ModelReply(value=None, raw="", error=MODEL_ERROR)
    if reply.value is None:
        if reply.error in (RATE_LIMITED, TIMEOUT, SERVER_ERROR):
            raise ReadError(f"the model could not be reached (step: {task})", unavailable=True)
        raise ReadError(f"the model's reply could not be used (step: {task})", unavailable=False)
    return reply.value


async def _nutrition_for(deps: _Deps, item: MealItemIn) -> NutritionResult | NutritionError:
    """nutrition-mcp's numbers for the item, or its refusal of the amount or the unit.

    A refusal names the food it is about. One that names none is an answer that could not be
    read, and that, like a nutrition-mcp that cannot be reached, is a ReadError.
    """
    try:
        return await deps.nutrition.nutrition(item.food_id, item.amount, item.unit)
    except NutritionError as exc:
        if exc.unreachable or exc.food_id is None:
            raise _nutrition_failed(exc) from exc
        return exc


async def _total(items: list[MealItemIn], deps: _Deps) -> MealResult:
    try:
        return await deps.nutrition.meal(items)
    except NutritionError as exc:
        raise _nutrition_failed(exc) from exc


def _nutrition_failed(exc: NutritionError) -> ReadError:
    if exc.unreachable:
        return ReadError("nutrition-mcp could not be reached (step: nutrition)", unavailable=True)
    return ReadError(
        "nutrition-mcp sent an answer that could not be used (step: nutrition)", unavailable=False
    )
