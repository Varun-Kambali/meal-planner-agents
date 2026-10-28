"""Reads a meal a person typed into nutrition-mcp foods, amounts and numbers.

The model's part is small. It lists the foods the text names with the amount and unit as typed
(task `read_meal`), and for the names where nutrition-mcp's search finds several foods and the
top one is not that name, it picks one by number for each, all in one call (task `choose_food`).
So a read makes at most two model calls. The model is never asked for a nutrition figure, and
nothing it adds to a reply is used. Code decides the rest:

- A food is nutrition-mcp's top search result when it is the only one or its name, an alias or
  its id is the name typed; with several, the model picks one; a name with no result, or a pick
  that is none of the results, is `missing` and left out of the total.
- An amount that is missing (the model's words for it are not in the text the person typed),
  vague ("some", "a handful") or a range ("2-3") is `needs_amount` whatever number the model
  returned. So is one whose number the words do not give: the code reads a number from the words
  ("a" is 1, "half" is 0.5, "1 1/2" is 1.5), and the model's number is used only when it is
  that number. The person is asked, never given a guess.
- Kilograms and litres are read as grams and millilitres. A unit nutrition-mcp has for the food
  goes to it. Otherwise the catalogue's measure for the food turns the amount into grams, if it
  has one; the summary reads a logged unit the same way (`Catalogue.meal_item`). Otherwise the
  item is `unknown_unit`, with the units that would work. No measure is made up: "2 pav" has no
  sourced weight, so it stays unknown.
- Every number comes from nutrition-mcp. The meal's total adds the matched items only.

The text goes to the model as data, quoted so that it cannot close the data block. Nothing here
logs it, and a ReadError names the step that failed and nothing the person wrote. With a tracer
a read is one trace, "read", holding its model calls; the text and every word in the result are
masked there (tracing.py).
"""

import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from nutrition_mcp.schemas import FoodSummary, MealItemIn, MealResult, NutritionResult
from nutrition_mcp.search import MAX_QUERY_LENGTH, normalise
from nutrition_mcp.units import MAX_GRAMS
from pydantic import BaseModel, Field

from meal_planner.catalogue import Catalogue
from meal_planner.config import Settings
from meal_planner.data_block import quote, template
from meal_planner.llm import MODEL_ERROR, RATE_LIMITED, SERVER_ERROR, TIMEOUT, ModelPort, ModelReply
from meal_planner.log import get_logger
from meal_planner.nutrition import NutritionError, NutritionPort
from meal_planner.tracing import Tracer, current, trace

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
# How nutrition-mcp refuses an amount over its limit: "126 roti comes to 5040 g, over the limit
# of 5000 g".
_OVER_LIMIT = re.compile(rf"comes to \S+ g, over the limit of {MAX_GRAMS:g} g$")

# A range of amounts: "2-3", "2 to 3", "2 or 3", "two or three". The dashes are a hyphen, an en
# dash and an em dash, as people type them between two numbers.
_NUMBER = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"
_DASHES = "-" + chr(0x2013) + chr(0x2014)
_RANGE = re.compile(rf"\b{_NUMBER}(?:\s*[{_DASHES}]\s*|\s+(?:to|or)\s+){_NUMBER}\b", re.IGNORECASE)
# Words for an amount that is not a number: "some", "a little", "a bit", "a few", "a handful",
# "several", "many", "plenty".
_VAGUE = re.compile(
    r"\b(?:some|little|bit|few|handfuls?|lots?|several|many|plenty)\b", re.IGNORECASE
)
# The amounts the code reads from an amount's words, to check the model's number against them.
# "a" and "an" are one. Any other number word (a Hindi one, "couple", "thirteen") is not read.
_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "half": 0.5,
    "a half": 0.5,
    "quarter": 0.25,
    "a quarter": 0.25,
    "dozen": 12,
    "a dozen": 12,
    "one and a half": 1.5,
}
# What the words start with: a mixed number or fraction ("1 1/2", "3/4"), a number ("200", "1.5"),
# or a number word, the longest first so that "a half" is not read as "a".
_AMOUNT_START = re.compile(
    r"(?:(?P<whole>\d+) )?(?P<top>\d+)/(?P<bottom>\d+)|(?P<digits>\d+(?:\.\d+)?)|(?P<word>"
    + "|".join(re.escape(word) for word in sorted(_NUMBER_WORDS, key=len, reverse=True))
    + r")\b"
)
_ORDINALS = ("fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
# The words after the number that make it a different one. They join or divide ("two and a half",
# "twelve to fifteen"), count in parts ("three quarters", "one fourth", "one point five"), scale
# it ("two hundred", "1 lakh") or make a range in Hindi ("2 se 3", "1 ya 2", and the same in
# Devanagari). So does any number word of the table after it ("one two"). Digits, signs and
# letters of other scripts after the number do the same ("1,000 g", "2~3", "1 & 1/2"), and are
# found by `_plain`. Whole words, not a pattern: a Devanagari vowel sign is not a word
# character, so a word boundary would not be found after "या".
_NUMBER_GOES_ON = frozenset(
    {
        "and",
        "or",
        "to",
        "aur",
        "se",
        "ya",
        "और",
        "या",
        "से",
        "point",
        "couple",
        "pair",
        "halves",
        "quarters",
        "third",
        "thirds",
        "hundred",
        "hundreds",
        "thousand",
        "thousands",
        "lakh",
        "lakhs",
        "crore",
        "crores",
        "million",
        "millions",
    }
    | {f"{ordinal}{plural}" for ordinal in _ORDINALS for plural in ("", "s")}
    # "a" and "an" are left out: "half a katori" is half.
    | {word for word in _NUMBER_WORDS if " " not in word and word not in ("a", "an")}
)
# The words for an amount may not run on into a longer word or number in the text: no letter
# touches a letter they start or end with, and no digit or decimal point touches a digit ("2" is
# not in "12" or "2.5"). A full stop that ends a sentence may follow a number.
_LETTER_BEFORE = r"(?<![^\W\d_])"
_LETTER_AFTER = r"(?![^\W\d_])"
_DIGIT_BEFORE = r"(?<![\d.])"
_DIGIT_AFTER = r"(?!\.?\d)"

Status = Literal["matched", "needs_amount", "unknown_unit", "missing"]


class ExtractedItem(BaseModel):
    """One food as the model read it from the text. None of it is a nutrition figure."""

    name: str
    amount: float | None
    unit: str | None
    # The words the person used for the amount, which the code looks for in the typed text and
    # reads for a range, a vague word and the number they give.
    amount_text: str


class Extracted(BaseModel):
    items: list[ExtractedItem] = Field(max_length=MAX_ITEMS)


class Pick(BaseModel):
    """For the name numbered `food`, the number of the search result that is the food, or None
    when none of them is."""

    food: int
    option: int | None


class Choices(BaseModel):
    """One pick for each name the search left open."""

    picks: list[Pick]


class ReadItem(BaseModel):
    """One food of the meal and what became of it.

    `matched`: the food and a usable amount were found, and `grams` and `nutrition` are set.
    `needs_amount`: the food was found but the amount is missing, vague, a range, not the number
    its words give, or beyond the limit; `valid_units` lists what could be used to give one.
    `unknown_unit`: the food was found but the unit is not one nutrition-mcp or the catalogue has
    for it; `valid_units` lists those that are. `missing`: the food is not in nutrition-mcp's
    data.
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
    tracer: Tracer | None = None,
) -> ReadResult:
    """The foods in `text`, each with its amount in grams and nutrition-mcp's numbers.

    The caller has checked the text's length. Raises ReadError when the model or nutrition-mcp
    cannot be used.
    """
    with trace(tracer, "read", text=text, text_length=len(text)) as root:
        result = await _read(text, _Deps(model, nutrition, catalogue, settings))
        root.set_output(result)
    return result


async def _read(text: str, deps: _Deps) -> ReadResult:
    prompt = "\n".join(["<data>", f"text: {quote(text)}", "</data>", "List the foods in the text."])
    extracted = await _ask(
        deps, task="read_meal", system=template("reader.md"), user=prompt, schema=Extracted
    )
    # A food with no name is nothing the person typed.
    named = [item for item in extracted.items if item.name.strip()]
    foods = await _find_foods([item.name.strip() for item in named], deps)
    items: list[ReadItem] = []
    to_total: list[MealItemIn] = []
    for item, food in zip(named, foods, strict=True):
        read, meal_item = await _read_item(item, food, text, deps)
        items.append(read)
        if meal_item is not None:
            to_total.append(meal_item)
    return ReadResult(
        text_length=len(text),
        items=items,
        total=await _total(to_total, deps) if to_total else None,
        missing=[item.name for item in items if item.status == "missing"],
    )


async def _read_item(
    item: ExtractedItem, food: FoodSummary | None, text: str, deps: _Deps
) -> tuple[ReadItem, MealItemIn | None]:
    """The item, and what the meal's total is to ask nutrition-mcp about it if it matched.

    `food` is the food found for its name, or None when there is none.
    """
    name = item.name.strip()
    typed_unit = (item.unit or "").strip() or None
    amount = _stated_amount(item, text)
    if food is None:
        return _unread(name, None, amount, typed_unit, "missing"), None
    valid = deps.catalogue.valid_units(food.id, food.units)
    if amount is None:
        return _unread(name, food, None, typed_unit, "needs_amount", valid), None
    unit = typed_unit or COUNTED
    # The search listed the food's units, so the unit is settled before nutrition-mcp is asked.
    meal_item = deps.catalogue.meal_item(food.id, amount, unit, food.units)
    if meal_item is None:
        return _unread(name, food, amount, unit, "unknown_unit", valid), None
    answer = await _nutrition_for(deps, meal_item)
    if answer is None:
        # nutrition-mcp refused the amount as over its limit.
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


def _stated_amount(item: ExtractedItem, text: str) -> float | None:
    """The amount the person gave, or None when they gave none a meal can be worked out from.

    Decided here and not by the model. The model's words for the amount have to be in the text
    the person typed, so a number it made up with words to match ("roti and dal" read as amount
    1, "1") is not used. No such words, a range or a vague word in them cancels whatever number
    the model chose. So does a number the words do not give: the code reads one from the words,
    and the model's amount is used only when it is that number ("a" is 1, not 5).
    """
    amount = item.amount
    if amount is None or not math.isfinite(amount) or not 0 < amount <= MAX_AMOUNT:
        return None
    words = _folded(item.amount_text)
    if not words or not _typed(words, _folded(text)):
        return None
    if _VAGUE.search(words) or _RANGE.search(words):
        return None
    read = _amount_in(words)
    if read is None or not math.isclose(read, amount, rel_tol=0, abs_tol=1e-9):
        return None
    return amount


def _folded(text: str) -> str:
    """Lower case, with every run of spaces read as one space."""
    return " ".join(text.casefold().split())


def _amount_in(words: str) -> float | None:
    """The number an amount's words start with, or None when they hold none the code reads.

    `words` are folded. A number may be followed by a unit or other letters ("200gm", "1.5l",
    "a katori"). The table of number words is short on purpose: any other number word reads as
    no number, and so does a number that goes on into one the table has no value for ("two and
    a half", "1,000 g", "1 litre 500 ml", "two hundred"). An item with no number to compare
    asks for an amount, and an item that asks never has a quantity invented for it. The words
    can still go on in a way listed nowhere here, so this narrows what the model can get wrong
    and does not rule it out.
    """
    start = _AMOUNT_START.match(words)
    if start is None:
        return None
    after = words[start.end() :]
    if not all(map(_plain, after)) or not _NUMBER_GOES_ON.isdisjoint(
        after.replace(".", " ").split()
    ):
        return None
    if start["word"]:
        return _NUMBER_WORDS[start["word"]]
    if start["digits"]:
        return float(start["digits"])
    bottom = float(start["bottom"])
    if bottom == 0:
        return None
    return float(start["whole"] or 0) + float(start["top"]) / bottom


def _plain(char: str) -> bool:
    """Whether a character after the number is one a unit or a word is made of: a space, a full
    stop, or a Latin or Devanagari letter (with the vowel signs of Devanagari). Anything else, a
    digit, a comma, a sign or a letter of another script, may carry on the number: the code has
    no number words for those scripts to look for."""
    if char.isspace() or char == ".":
        return True
    return unicodedata.category(char)[0] in "LM" and unicodedata.name(char, "").startswith(
        ("LATIN", "DEVANAGARI")
    )


def _typed(words: str, text: str) -> bool:
    """Whether `words` are in `text` on their own, and not only inside a longer number or word,
    the way "2" is in "12" and "2.5" and "one" is in "honey"."""
    before = _LETTER_BEFORE if words[0].isalpha() else _DIGIT_BEFORE if words[0].isdigit() else ""
    after = _LETTER_AFTER if words[-1].isalpha() else _DIGIT_AFTER if words[-1].isdigit() else ""
    return re.search(before + re.escape(words) + after, text) is not None


# Finding the food


async def _find_foods(names: list[str], deps: _Deps) -> list[FoodSummary | None]:
    """The food for each name, or None when nutrition-mcp has none by that name.

    The search settles a name when it finds one food or the top one is that name. The names it
    leaves open go to the model together, in one call, and a name typed twice is asked about once.
    """
    found = [await _search(name, deps) for name in names]
    asked: dict[str, tuple[str, list[FoodSummary]]] = {}
    for name, options in zip(names, found, strict=True):
        if options and not _settled(name, options):
            asked.setdefault(normalise(name), (name, options))
    chosen = dict(zip(asked, await _choose(list(asked.values()), deps), strict=True))
    foods: list[FoodSummary | None] = []
    for name, options in zip(names, found, strict=True):
        if not options:
            foods.append(None)
        elif _settled(name, options):
            foods.append(options[0])
        else:
            foods.append(chosen[normalise(name)])
    return foods


async def _search(name: str, deps: _Deps) -> list[FoodSummary]:
    if not normalise(name) or len(name) > MAX_QUERY_LENGTH:
        # nutrition-mcp refuses a query with no letter or digit in it, or a long one. No food
        # has such a name.
        return []
    try:
        found = await deps.nutrition.search(name, limit=OPTIONS)
    except NutritionError as exc:
        raise _nutrition_failed(exc) from exc
    return found.results


def _settled(name: str, options: list[FoodSummary]) -> bool:
    """Whether the search found one food, or a top one that is the name. `options` is not empty."""
    return len(options) == 1 or _is_named(options[0], name)


def _is_named(food: FoodSummary, name: str) -> bool:
    """Whether `name` is one of the food's names or its id, spelled as nutrition-mcp reads them."""
    return normalise(name) in {normalise(n) for n in (food.name, food.id, *food.aliases)}


async def _choose(
    asked: list[tuple[str, list[FoodSummary]]], deps: _Deps
) -> list[FoodSummary | None]:
    """The option the model says is the food for each name, or None. One call for all of them.

    The model sees each name and its options, and nothing else the person typed.
    """
    if not asked:
        return []
    lines: list[str] = []
    for number, (name, options) in enumerate(asked):
        lines += [f"food {number}: {quote(name)}", "options:"]
        lines += [f"{option}: {_describe(food)}" for option, food in enumerate(options)]
    user = "\n".join(["<data>", *lines, "</data>", "Choose the food for each name."])
    reply = await _ask(
        deps, task="choose_food", system=template("choose_food.md"), user=user, schema=Choices
    )
    said: dict[int, set[int | None]] = {}
    for pick in reply.picks:
        said.setdefault(pick.food, set()).add(pick.option)
    return [_option(options, said.get(number, set())) for number, (_, options) in enumerate(asked)]


def _option(options: list[FoodSummary], picked: set[int | None]) -> FoodSummary | None:
    """The option picked for a name. No pick, picks that disagree, None and a number outside the
    list are all no choice; Python would read -1 as the last one."""
    index = next(iter(picked)) if len(picked) == 1 else None
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
        # that failed. Only the class is logged and traced: the message could hold the prompt.
        logger.error("model_call_raised", extra={"task": task, "error_type": type(exc).__name__})
        current().failed(exc)
        reply = ModelReply(value=None, raw="", error=MODEL_ERROR)
    if reply.value is None:
        if reply.error in (RATE_LIMITED, TIMEOUT, SERVER_ERROR):
            raise ReadError(f"the model could not be reached (step: {task})", unavailable=True)
        raise ReadError(f"the model's reply could not be used (step: {task})", unavailable=False)
    return reply.value


async def _nutrition_for(deps: _Deps, item: MealItemIn) -> NutritionResult | None:
    """nutrition-mcp's numbers for the item, or None when it refuses the amount as over its limit.

    The food and the unit were settled before it was asked, so any other refusal, an answer that
    could not be read and a nutrition-mcp that cannot be reached are each a ReadError.
    """
    try:
        return await deps.nutrition.nutrition(item.food_id, item.amount, item.unit)
    except NutritionError as exc:
        if not exc.unreachable and exc.food_id is not None and _OVER_LIMIT.search(str(exc)):
            return None
        raise _nutrition_failed(exc) from exc


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
