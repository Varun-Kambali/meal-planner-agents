"""The eval's metrics, each a small function over the case lines a run keeps.

Recipe metrics count over the recipes a plan asks for (seven a slot), so a recipe that was never
written counts as one that did not pass. A recipe passes when it was written and ended with no
failure other than a day's: a day check (`day_kcal`, `day_sat_fat_g` ...) is about the day's
total, and the graph only hangs it on a recipe to choose one to rewrite, so it is counted on the
day instead. A day passes when it ended with no failure, and a week when every recipe it asks
for and every day passes. No-waste flags are warnings, as in the app, and pass or fail nothing.

Reading metrics compare the reader's items with the labels in evals/datasets/meals.jsonl. A
labelled item is found when the reader returns its food id or one in its `also_accept`, each
reader item standing for at most one label (the largest such pairing, tried in written order).
"Asked when it should" counts a label that needs an amount or a unit as asked only when the reader
returned that food and did not match it; a label the reader did not return (left out, or named
as another food) is counted apart as unpaired, and a read that failed is left out of the measure.

Shares are kept with the counts they come from, so a reader can check every one.
"""

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence

from cases import MealLabel
from nutrition_mcp.search import PLURAL_MIN_LETTERS, normalise
from pydantic import BaseModel
from records import ReadingCase, ReadItemRecord, RecipeCase, RecipeOutcome, Usage

# Every day check's name starts with this ("day_" and the nutrient), and no recipe check's does.
DAY_CHECK_PREFIX = "day_"
# The checks `checks.check_variety` gives: a cuisine outside the plan's list, a title or most
# ingredients shared with another recipe of the slot, a cuisine repeated the next day.
VARIETY_CHECKS = frozenset(
    {"cuisine_not_in_plan", "variety_title", "variety_ingredients", "variety_cuisine"}
)
# The label statuses where the person has to be asked: no amount, or a unit nothing has.
ASK_STATUSES = frozenset({"needs_amount", "unknown_unit"})
# Decimal places a share or an error is kept to in a results file.
SHARE_PLACES = 4


def share(part: int, whole: int) -> float | None:
    """`part` of `whole`, or None when there is nothing to share out."""
    return round(part / whole, SHARE_PLACES) if whole else None


def p95(values: Sequence[float]) -> float | None:
    """The 95th percentile by nearest rank: the value at rank ceil(0.95 n) when sorted.

    No interpolation, so the result is always one of the values. Of 20 weeks, it is the second
    slowest. None when there are no values.
    """
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def mean(total: float, count: int, places: int) -> float | None:
    return round(total / count, places) if count else None


def pair[A, B](
    first: Sequence[A], second: Sequence[B], fits: Callable[[A, B], bool]
) -> list[int | None]:
    """For each item of `first`, the index of the item of `second` paired with it, or None.

    Each item of `second` is paired at most once, and as many pairs are made as can be (a
    maximum bipartite matching, found by augmenting paths). The search runs in the given order,
    so the same input always gives the same pairing.
    """
    owner: dict[int, int] = {}

    def claim(i: int, seen: set[int]) -> bool:
        for j, candidate in enumerate(second):
            if j in seen or not fits(first[i], candidate):
                continue
            seen.add(j)
            if j not in owner or claim(owner[j], seen):
                owner[j] = i
                return True
        return False

    for i in range(len(first)):
        claim(i, set())
    paired: list[int | None] = [None] * len(first)
    for j, i in owner.items():
        paired[i] = j
    return paired


# Recipes


def day_check(check: str) -> bool:
    return check.startswith(DAY_CHECK_PREFIX)


def recipe_passes(recipe: RecipeOutcome) -> bool:
    """Written, and no failure but a day's at the end."""
    return recipe.written and not any(not day_check(f.check) for f in recipe.failures)


def passes_first_time(recipe: RecipeOutcome) -> bool:
    """Written at its first attempt, and no failure but a day's the first time it was checked."""
    first = recipe.first_failures
    return first is not None and not any(not day_check(check) for check in first)


def passes_variety(recipe: RecipeOutcome) -> bool:
    """Written, and no variety failure at the end."""
    return recipe.written and not any(f.check in VARIETY_CHECKS for f in recipe.failures)


# How the reason a recipe was not written is grouped in the results. The messages carry details
# (days, field names) that would give every recipe a row of its own, so each prefix the graph
# and the baseline write maps to one kind. An error code is its own kind.
_UNWRITTEN_KINDS = (
    ("the reply was not valid JSON", "reply not valid JSON"),
    ("the reply did not fit the format", "reply did not fit the format"),
    ("the recipe for this day and slot did not fit the format", "reply did not fit the format"),
    ("the reply has ", "reply had the wrong recipes"),
    ("the reply's recipes are not for the days asked", "reply had the wrong recipes"),
    ("the reply is for slot ", "reply had the wrong recipes"),
    ("no recipe for this day and slot in the reply", "reply had the wrong recipes"),
)
_PLAIN_REASONS = frozenset({"not written", "no reply"})


def unwritten_kind(reason: str) -> str:
    """The kind of reason a recipe was not written, for counting: an error code as it is, a
    known message by its kind, anything else "other"."""
    if reason in _PLAIN_REASONS or re.fullmatch(r"[a-z_]+", reason):
        return reason
    return next((kind for prefix, kind in _UNWRITTEN_KINDS if reason.startswith(prefix)), "other")


def week_passes(case: RecipeCase) -> bool:
    return all(recipe_passes(r) for r in case.recipes) and all(not d.failures for d in case.days)


class RecipeMetrics(BaseModel):
    weeks: int
    recipes_asked: int
    recipes_written: int
    recipes_passing: int
    recipe_pass_share: float | None
    first_attempt_passing: int
    first_attempt_pass_share: float | None
    variety_passing: int
    variety_pass_share: float | None
    days: int
    days_passing: int
    day_pass_share: float | None
    weeks_passing: int
    week_pass_share: float | None
    calls: int
    calls_per_week: float | None
    input_tokens: int
    output_tokens: int
    tokens_per_week: float | None
    # LiteLLM's list price for the calls; None when any call that got a reply had no price.
    cost_usd: float | None
    cost_usd_per_week: float | None
    seconds_p95: float | None
    call_errors: dict[str, int]
    # For each recipe check, the recipes that ended failing it (day checks are counted on days).
    failing_checks: dict[str, int]
    # For each day check, the days that ended failing it.
    failing_day_checks: dict[str, int]
    no_waste_flags: int
    # Recipes never written, by the kind of reason (see `unwritten_kind`).
    unwritten_by_reason: dict[str, int]


def _usage_totals(usages: Iterable[Usage]) -> Usage:
    total = Usage()
    for usage in usages:
        total = Usage(
            calls=total.calls + usage.calls,
            input_tokens=total.input_tokens + usage.input_tokens,
            output_tokens=total.output_tokens + usage.output_tokens,
            cost_usd=total.cost_usd + usage.cost_usd,
            cost_unknown_calls=total.cost_unknown_calls + usage.cost_unknown_calls,
            errors=dict(Counter(total.errors) + Counter(usage.errors)),
        )
    return total


def _cost(usage: Usage) -> float | None:
    return None if usage.cost_unknown_calls else round(usage.cost_usd, 6)


def recipe_metrics(cases: Sequence[RecipeCase]) -> RecipeMetrics:
    """The recipe metrics over the weeks of one system (all pipeline or all baseline)."""
    recipes = [r for case in cases for r in case.recipes]
    days = [d for case in cases for d in case.days]
    usage = _usage_totals(case.usage for case in cases)
    cost = _cost(usage)
    weeks = len(cases)
    passing = sum(map(recipe_passes, recipes))
    first = sum(map(passes_first_time, recipes))
    variety = sum(map(passes_variety, recipes))
    days_passing = sum(1 for d in days if not d.failures)
    weeks_passing = sum(map(week_passes, cases))
    failing = Counter(
        check
        for r in recipes
        for check in dict.fromkeys(f.check for f in r.failures)
        if not day_check(check)
    )
    failing_days = Counter(
        check for d in days for check in dict.fromkeys(f.check for f in d.failures)
    )
    return RecipeMetrics(
        weeks=weeks,
        recipes_asked=len(recipes),
        recipes_written=sum(1 for r in recipes if r.written),
        recipes_passing=passing,
        recipe_pass_share=share(passing, len(recipes)),
        first_attempt_passing=first,
        first_attempt_pass_share=share(first, len(recipes)),
        variety_passing=variety,
        variety_pass_share=share(variety, len(recipes)),
        days=len(days),
        days_passing=days_passing,
        day_pass_share=share(days_passing, len(days)),
        weeks_passing=weeks_passing,
        week_pass_share=share(weeks_passing, weeks),
        calls=usage.calls,
        calls_per_week=mean(usage.calls, weeks, 2),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        tokens_per_week=mean(usage.input_tokens + usage.output_tokens, weeks, 1),
        cost_usd=cost,
        cost_usd_per_week=None if cost is None else mean(cost, weeks, 6),
        seconds_p95=p95([case.seconds for case in cases]),
        call_errors=dict(sorted(usage.errors.items())),
        failing_checks=dict(sorted(failing.items())),
        failing_day_checks=dict(sorted(failing_days.items())),
        no_waste_flags=sum(case.no_waste for case in cases),
        unwritten_by_reason=dict(
            sorted(
                Counter(
                    unwritten_kind(r.unwritten_reason or "not written")
                    for r in recipes
                    if not r.written
                ).items()
            )
        ),
    )


# Reading


def found_by(label_food: str, also_accept: Sequence[str], item: ReadItemRecord) -> bool:
    return item.food_id is not None and item.food_id in (label_food, *also_accept)


def _words(name: str) -> list[str]:
    """The name's words after nutrition-mcp's normalising (case, punctuation, common spellings),
    each read in the singular the way nutrition-mcp's search reads it: a final "s" dropped from a
    word of PLURAL_MIN_LETTERS letters or more."""
    return [
        word.removesuffix("s") if len(word) >= PLURAL_MIN_LETTERS else word
        for word in normalise(name).split()
    ]


def _runs_inside(part: list[str], whole: list[str]) -> bool:
    return any(whole[i : i + len(part)] == part for i in range(len(whole) - len(part) + 1))


def names_match(label: str, reported: str) -> bool:
    """Whether a name the reader reports missing is the labelled one: the words of either name
    run, whole and in order, inside the other's ("coffee" in "black coffee", not "a" in
    "raita")."""
    a, b = _words(label), _words(reported)
    return bool(a and b) and (_runs_inside(a, b) or _runs_inside(b, a))


class ReadingMetrics(BaseModel):
    meals: int
    reads_failed: int
    # Reader items with a food id, and those paired with a labelled item.
    reader_items: int
    reader_items_found: int
    food_precision: float | None
    expected_items: int
    expected_found: int
    food_recall: float | None
    # Labelled items found with the status the label gives.
    status_matched: int
    status_accuracy: float | None
    # needs_amount and unknown_unit labels in reads that did not fail; those the reader returned
    # (paired) and did not match; and those it did not return at all (left out, or named as
    # another food), which count as not asked.
    ask_labels: int
    asked: int
    asked_share: float | None
    ask_labels_unpaired: int
    # Meals whose labelled matched items give calories above 0, and the mean absolute
    # percentage error of the reader's total against them, as a fraction.
    kcal_meals: int
    kcal_mape: float | None
    missing_labels: int
    missing_reported: int
    missing_share: float | None
    calls: int
    calls_per_meal: float | None
    input_tokens: int
    output_tokens: int
    tokens_per_meal: float | None
    cost_usd: float | None
    cost_usd_per_meal: float | None
    seconds_p95: float | None
    call_errors: dict[str, int]


def reading_metrics(
    cases: Sequence[ReadingCase], labels: Mapping[str, MealLabel]
) -> ReadingMetrics:
    """The reading metrics over the meals in `cases`, each scored against its label.

    A failed read returned no items: its labelled items count as not found and its calories as 0
    against the label, but its labels that ask are left out of "asked when it should" (it is
    counted in `reads_failed` instead).
    """
    reader_items = found_reader = expected = found = status_ok = 0
    ask = asked = unpaired = missing_labels = missing_reported = 0
    errors: list[float] = []
    for case in cases:
        label = labels[case.meal]
        with_food = [item for item in case.items if item.food_id is not None]
        paired = pair(
            label.expected, with_food, lambda lab, it: found_by(lab.food_id, lab.also_accept, it)
        )
        reader_items += len(with_food)
        found_reader += sum(1 for j in paired if j is not None)
        expected += len(label.expected)
        for lab, j in zip(label.expected, paired, strict=True):
            item = with_food[j] if j is not None else None
            found += item is not None
            status_ok += item is not None and item.status == lab.status
            if lab.status in ASK_STATUSES and case.error is None:
                ask += 1
                unpaired += item is None
                asked += item is not None and item.status != "matched"
        missing_labels += len(label.missing)
        missing_reported += sum(
            1 for j in pair(label.missing, case.missing, names_match) if j is not None
        )
        if case.label_kcal is not None and case.label_kcal > 0:
            reader_kcal = case.kcal if case.kcal is not None else 0.0
            errors.append(abs(reader_kcal - case.label_kcal) / case.label_kcal)
    usage = _usage_totals(case.usage for case in cases)
    cost = _cost(usage)
    meals = len(cases)
    return ReadingMetrics(
        meals=meals,
        reads_failed=sum(1 for case in cases if case.error is not None),
        reader_items=reader_items,
        reader_items_found=found_reader,
        food_precision=share(found_reader, reader_items),
        expected_items=expected,
        expected_found=found,
        food_recall=share(found, expected),
        status_matched=status_ok,
        status_accuracy=share(status_ok, expected),
        ask_labels=ask,
        asked=asked,
        asked_share=share(asked, ask),
        ask_labels_unpaired=unpaired,
        kcal_meals=len(errors),
        kcal_mape=round(sum(errors) / len(errors), SHARE_PLACES) if errors else None,
        missing_labels=missing_labels,
        missing_reported=missing_reported,
        missing_share=share(missing_reported, missing_labels),
        calls=usage.calls,
        calls_per_meal=mean(usage.calls, meals, 2),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        tokens_per_meal=mean(usage.input_tokens + usage.output_tokens, meals, 1),
        cost_usd=cost,
        cost_usd_per_meal=None if cost is None else mean(cost, meals, 6),
        seconds_p95=p95([case.seconds for case in cases]),
        call_errors=dict(sorted(usage.errors.items())),
    )
