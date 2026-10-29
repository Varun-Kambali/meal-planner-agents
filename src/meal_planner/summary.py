"""The meal log, and the day and week totals it comes to against the plan.

The log is what the browser keeps of the week: for each planned meal, how it went and the foods
eaten, as ids, amounts and units. It holds no number. A summary works out every meal that was
eaten again with nutrition-mcp, adds up each day with the same function the day checks use and
holds it to the targets of its day type, so a day eaten and a day written are judged alike.

A log that does not fit is refused with the entry named by its date and slot, never read around:
an entry whose slot the plan lacks, a food nutrition-mcp does not know or refuses an amount or
unit of, a date that makes the log longer than a week. Nothing here logs what the log says.
"""

import datetime
from collections import defaultdict
from typing import Literal, Self

from nutrition_mcp.models import Nutrients
from nutrition_mcp.nutrients import NUTRIENT_KEYS
from nutrition_mcp.schemas import MAX_NAME_LENGTH, MealItemIn, MealResult
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from meal_planner.catalogue import Catalogue
from meal_planner.checks import add_up_meals, check_day_totals, sat_fat_ceiling
from meal_planner.config import Settings
from meal_planner.nutrition import NutritionError, NutritionPort
from meal_planner.problems import Problem
from meal_planner.recipes import Failure
from meal_planner.rules import CORE_TARGET_FIELDS, DAYS, DayType, PlanRules
from meal_planner.week_events import WeekResult

MealStatus = Literal["as_planned", "different_amount", "swapped", "skipped"]

# A log covers at most this many consecutive days, a week.
LOG_DAYS = 7
# The most foods one meal lists, as many as the meal reader reads from one typed text. A test
# holds the two together.
MAX_FOODS = 20


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LogItem(_Model):
    """One food that was eaten: an id from nutrition-mcp, how many of a unit, and the unit."""

    food_id: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    # Not infinite or NaN, which a hand-edited file can hold and JSON Schema cannot say.
    amount: float = Field(gt=0, allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class LogEntry(_Model):
    """One planned meal, on a date: how it went and what was eaten.

    A skipped meal lists nothing. Any other lists the foods that were eaten, at most as many as
    one typed meal can hold.
    """

    date: datetime.date
    slot_id: str
    status: MealStatus
    items: list[LogItem] = Field(max_length=MAX_FOODS)

    @model_validator(mode="after")
    def _items_match_the_status(self) -> Self:
        where = f"{self.date.isoformat()} {self.slot_id}"
        if self.status == "skipped" and self.items:
            raise ValueError(f"{where}: a skipped meal lists no foods")
        if self.status != "skipped" and not self.items:
            raise ValueError(
                f"{where}: a meal with status '{self.status}' needs at least one food; "
                "mark a meal that was not eaten as 'skipped'"
            )
        return self


class MealLog(_Model):
    """What was eaten over at most seven consecutive days, one entry for each slot of a date."""

    version: Literal[1]
    entries: list[LogEntry]

    @field_validator("entries")
    @classmethod
    def _entries_fit_together(cls, entries: list[LogEntry]) -> list[LogEntry]:
        problems: list[str] = []
        seen: set[tuple[datetime.date, str]] = set()
        twice: list[str] = []
        for entry in entries:
            key = (entry.date, entry.slot_id)
            if key in seen:
                twice.append(f"{entry.date.isoformat()} {entry.slot_id}")
            seen.add(key)
        if twice:
            names = list(dict.fromkeys(twice))
            verb = "is" if len(names) == 1 else "are"
            problems.append(f"{', '.join(names)} {verb} listed more than once")
        if entries:
            first = min(entry.date for entry in entries)
            last = max(entry.date for entry in entries)
            days = (last - first).days + 1
            if days > LOG_DAYS:
                problems.append(
                    f"the dates run from {first.isoformat()} to {last.isoformat()}, "
                    f"which is {days} days; a log covers at most {LOG_DAYS} consecutive days"
                )
        if problems:
            raise ValueError(f"entries: {'; '.join(problems)}")
        return entries


class Export(_Model):
    """Everything the browser keeps, as the one file a person saves and loads again."""

    version: Literal[1]
    rules: PlanRules
    week: WeekResult | None
    log: MealLog


class DaySummary(_Model):
    """One date of the log against the targets of its day type.

    `totals`, `failures` and `unsettled` come from the functions the day checks use, so they read
    the same. A total is None when no food eaten has a value for the nutrient. The two dicts by
    slot cover the meals of the day that were eaten.
    """

    date: datetime.date
    # The id of the day type the plan gives this date's weekday.
    day_type: str
    # Nutrition-mcp's nutrient names. A nutrient no food has a value for is None, never 0.
    totals: dict[str, float | None]
    targets: dict[str, float]
    failures: list[Failure]
    # Checks the day can be shown neither to meet nor to miss, because a food eaten has no value
    # for the nutrient; see DayReport. A meal nutrition-mcp cannot work out refuses the log or
    # ends the summary instead, so here a missing value is the only cause. Never a failure, and
    # never on target.
    unsettled: list[Failure]
    # Carbohydrate and fat as a fraction over (+) or under (-) their target; see DayReport.
    soft: dict[str, float]
    # A meal's glycemic load, or None when an item in it has none, and then how many do not.
    glycemic_load: dict[str, float | None]
    gl_missing: dict[str, int]


class WeekSummary(_Model):
    """The days of a log, and how much of the plan was followed."""

    days: list[DaySummary]
    # Meals eaten as planned over the meals the plan has on every date from the log's first to its
    # last. A slot with no entry, also on a date with none, was not eaten as planned.
    as_planned_share: float
    # Days with no failure and nothing unsettled: shown to meet every target.
    days_on_target: int
    # Days with no failure and at least one check that could not be settled.
    days_unsettled: int
    planned_meals: int


class LogRejected(Exception):
    """The log does not fit the plan, or nutrition-mcp refused a food in it.

    Each problem says where and what, with the location counted from the log (such as
    ["entries", 3, "items", 0]). The message holds nothing the log said.
    """

    def __init__(self, problems: list[Problem]) -> None:
        super().__init__(f"the log has {len(problems)} problem(s)")
        self.problems = problems


class SummaryError(Exception):
    """nutrition-mcp could not be used. The message names the step and holds nothing from the log.

    `unavailable` is set when it could not be reached, so that trying again later can help;
    otherwise it answered with something that could not be used.
    """

    def __init__(self, message: str, *, unavailable: bool) -> None:
        super().__init__(message)
        self.unavailable = unavailable


class MealRefused(Exception):
    """nutrition-mcp refused a food, an amount or a unit of a meal, which is the sender's to fix.

    `reason` is nutrition-mcp's own message, with the units a person may give for the food, and
    `item` is the place of the item to blame, counting from 0 (None when it cannot be told).
    """

    def __init__(self, reason: str, item: int | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.item = item


def log_problems(rules: PlanRules, log: MealLog) -> list[Problem]:
    """A problem for each entry whose slot the plan does not have.

    Every day type of a plan shares its slots, so a slot is on every date or on none.
    """
    slots = [slot.id for slot in rules.slots]
    return [
        {
            "loc": ["entries", index, "slot_id"],
            "msg": f"{entry.date.isoformat()} {entry.slot_id}: the plan has no slot "
            f"'{entry.slot_id}'; its slots are {', '.join(slots)}",
        }
        for index, entry in enumerate(log.entries)
        if entry.slot_id not in slots
    ]


async def summarise(
    rules: PlanRules,
    log: MealLog,
    *,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
) -> WeekSummary:
    """Each date of the log against its day type, and the share eaten as planned.

    A unit is read as the meal reader reads it (`Catalogue.meal_item`): kilograms and litres as
    grams and millilitres, and a catalogue measure for a food nutrition-mcp has no such unit for
    in grams. Raises LogRejected for an entry the plan or nutrition-mcp does not accept. A slot
    the plan lacks is reported first, without asking nutrition-mcp. Once there is none, every
    entry nutrition-mcp refuses is reported at once. Raises SummaryError when nutrition-mcp
    cannot be used.
    """
    problems = log_problems(rules, log)
    if problems:
        raise LogRejected(problems)

    slot_order = {slot.id: position for position, slot in enumerate(rules.slots)}
    ordered = sorted(
        enumerate(log.entries), key=lambda pair: (pair[1].date, slot_order[pair[1].slot_id])
    )
    meals: dict[int, MealResult] = {}
    refused: list[Problem] = []
    for index, entry in ordered:
        # A skipped meal has no foods to add up; it is not a meal with numbers missing.
        if entry.status == "skipped":
            continue
        try:
            meals[index] = await meal_of(entry.items, nutrition=nutrition, catalogue=catalogue)
        except MealRefused as exc:
            loc: list[str | int] = ["entries", index, "items"]
            refused.append(
                {
                    "loc": loc if exc.item is None else [*loc, exc.item],
                    "msg": f"{entry.date.isoformat()} {entry.slot_id}: {exc.reason}",
                }
            )
    if refused:
        raise LogRejected(refused)

    day_types = {day_type.id: day_type for day_type in rules.day_types}
    by_date: dict[datetime.date, list[tuple[LogEntry, MealResult | None]]] = defaultdict(list)
    for index, entry in ordered:
        by_date[entry.date].append((entry, meals.get(index)))
    days = [
        _summarise_day(date, entries, day_types[rules.week[DAYS[date.weekday()]]], settings)
        for date, entries in by_date.items()
    ]

    # Every day type of a plan has the plan's slots, so each date plans as many meals as the plan
    # has slots, whether or not the log has an entry for each. The dates run from the log's first
    # to its last, so a day with no entries between two logged ones was not eaten as planned.
    span = (days[-1].date - days[0].date).days + 1 if days else 0
    planned_meals = span * len(rules.slots)
    as_planned = sum(1 for entry in log.entries if entry.status == "as_planned")
    return WeekSummary(
        days=days,
        as_planned_share=as_planned / planned_meals if planned_meals else 0.0,
        days_on_target=sum(1 for day in days if not day.failures and not day.unsettled),
        days_unsettled=sum(1 for day in days if not day.failures and day.unsettled),
        planned_meals=planned_meals,
    )


def _summarise_day(
    date: datetime.date,
    entries: list[tuple[LogEntry, MealResult | None]],
    day_type: DayType,
    settings: Settings,
) -> DaySummary:
    """One date. `entries` are in the order of the plan's slots, and a skipped entry has no meal.

    Saturated fat is bounded by `sat_fat_ceiling` over the meals eaten, as a day of recipes is.
    """
    eaten = [(entry, meal) for entry, meal in entries if meal is not None]
    if eaten:
        total, incomplete = add_up_meals(
            [(meal, [item.food_id for item in entry.items]) for entry, meal in eaten]
        )
    else:
        # A day of skipped meals is a day on which nothing was eaten, which is known: 0, not None.
        total, incomplete = Nutrients.model_validate(dict.fromkeys(NUTRIENT_KEYS, 0.0)), {}
    ceiling = sat_fat_ceiling([meal for _, meal in eaten])
    failures, unsettled, soft = check_day_totals(
        total,
        incomplete,
        day_type,
        settings,
        ceilings=None if ceiling is None else {"sat_fat_g": ceiling},
    )
    return DaySummary(
        date=date,
        day_type=day_type.id,
        totals=dict(total),
        targets=_targets(day_type),
        failures=failures,
        unsettled=unsettled,
        soft=soft,
        glycemic_load={
            entry.slot_id: None if meal.glycemic_load_missing else meal.glycemic_load
            for entry, meal in eaten
        },
        gl_missing={entry.slot_id: len(meal.glycemic_load_missing) for entry, meal in eaten},
    )


def _to_send(item: LogItem, catalogue: Catalogue) -> MealItemIn:
    """The food as nutrition-mcp is asked about it. A unit neither nutrition-mcp nor the
    catalogue has goes as it was logged, for nutrition-mcp to refuse in its own words."""
    sent = catalogue.meal_item(item.food_id, item.amount, item.unit)
    return sent or MealItemIn(food_id=item.food_id, amount=item.amount, unit=item.unit)


def _targets(day_type: DayType) -> dict[str, float]:
    """The day type's targets by nutrient: its own fields, then the extra ones."""
    targets = {nutrient: getattr(day_type, field) for nutrient, field in CORE_TARGET_FIELDS.items()}
    return targets | {target.nutrient: target.value for target in day_type.extra}


async def meal_of(
    items: list[LogItem], *, nutrition: NutritionPort, catalogue: Catalogue
) -> MealResult:
    """nutrition-mcp's numbers for the foods of one meal, each unit read as the reader reads it.

    This is what the summary adds up a log with, so a meal has the same numbers wherever it is
    worked out. Raises MealRefused for a food, amount or unit nutrition-mcp refuses, and
    SummaryError when nutrition-mcp cannot be used. Neither message holds anything from `items`
    except what nutrition-mcp says about the food it refused.
    """
    try:
        return await nutrition.meal([_to_send(item, catalogue) for item in items])
    except NutritionError as exc:
        raise _refusal(items, exc, catalogue) from exc


def nutrition_unusable(exc: NutritionError) -> SummaryError:
    """The error for a nutrition-mcp that could not be reached, or sent an answer that no one
    can do anything with. It says the step and holds nothing the caller sent."""
    if exc.unreachable:
        message = "nutrition-mcp could not be reached (step: nutrition)"
        return SummaryError(message, unavailable=True)
    message = "nutrition-mcp sent an answer that could not be used (step: nutrition)"
    return SummaryError(message, unavailable=False)


def _refusal(
    items: list[LogItem], exc: NutritionError, catalogue: Catalogue
) -> MealRefused | SummaryError:
    """MealRefused for a meal nutrition-mcp refused over one of its foods, which the sender is to
    fix. Any other failure is nutrition-mcp's, and is a SummaryError.

    A unit it refused for a catalogue food lists the catalogue's measures too, as the reader
    does, since they work as well. nutrition-mcp ends its message with its own list.
    """
    if exc.unreachable:
        return nutrition_unusable(exc)
    at = _refused_item(items, exc)
    food_id = items[at].food_id if at is not None else exc.food_id
    if food_id is None:
        return nutrition_unusable(exc)
    reason = str(exc)
    if exc.valid_units and food_id in catalogue.items:
        before, listed, _ = reason.rpartition("valid units: ")
        reason = before + listed + ", ".join(catalogue.valid_units(food_id))
    return MealRefused(reason, at)


def _refused_item(items: list[LogItem], exc: NutritionError) -> int | None:
    """The place in `items` of the item nutrition-mcp refused, or None when it cannot be told.

    nutrition-mcp counts the item it failed on and puts the food it was sent in front of its
    message. That is trusted when the food is the one sent at that place, whatever characters the
    id holds, since the message cannot be cut up to find it. Otherwise the first item with the
    food the error names is the best guess.
    """
    at = exc.item
    if (
        at is not None
        and 0 <= at < len(items)
        and str(exc).startswith(f"item {at + 1} ({items[at].food_id}):")
    ):
        return at
    return next((n for n, item in enumerate(items) if item.food_id == exc.food_id), None)
