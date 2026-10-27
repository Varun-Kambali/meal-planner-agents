"""What a run keeps for each case it finishes: one JSON line in the run's cases.jsonl.

A line holds the case's id, the ids of its inputs, what each metric needs from the output, the
model calls with their tokens and cost, and the seconds the case took. The metrics are worked
out from these lines alone, so a results file can be rebuilt from a run's folder.
"""

from collections import Counter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from meal_planner.llm import NO_REPLY, ModelReply
from meal_planner.reader import ReadResult
from meal_planner.recipes import Failure
from meal_planner.rules import DayKey, PlanRules, expand
from meal_planner.week_events import RecipeChecked, WeekResult


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(_Record):
    """The model calls a case made.

    `cost_usd` adds up LiteLLM's computed cost of each call that has one. `cost_unknown_calls`
    counts calls that got a reply and no cost (a model LiteLLM has no price for), which makes the
    case's cost unknown. A call that got no reply (rate limited, timed out, refused, or missing
    from a recording) counts as a call, costs nothing and is counted under its error code.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_unknown_calls: int = 0
    errors: dict[str, int] = {}

    def add(self, reply: ModelReply[object]) -> None:
        self.calls += 1
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        if reply.cost_usd is not None:
            self.cost_usd += reply.cost_usd
        elif reply.error not in NO_REPLY:
            self.cost_unknown_calls += 1
        if reply.error is not None:
            self.add_error(reply.error)

    def add_error(self, code: str) -> None:
        self.errors = dict(Counter(self.errors) + Counter({code: 1}))


class FailureRecord(_Record):
    check: str
    message: str


class RecipeOutcome(_Record):
    """One recipe the plan asks for, written or not, and how it ended."""

    day: DayKey
    slot_id: str
    written: bool
    # Why the recipe was not written; None when it was.
    unwritten_reason: str | None = None
    title: str | None = None
    cuisine: str | None = None
    # (food id, grams) as the recipe ended, after any portion fitting.
    ingredients: list[tuple[str, float]] = []
    kcal: float | None = None
    protein_g: float | None = None
    attempts: int = 0
    portion_scale: float = 1.0
    # The failures it ended with, as the graph (or `evaluate_week` for the baseline) gave them.
    failures: list[FailureRecord] = []
    # The check names it had the first time it was checked, when that was its first attempt;
    # None when it had no recipe at its first attempt. The baseline's only check is its first.
    first_failures: list[str] | None = None


class DayOutcome(_Record):
    day: DayKey
    failures: list[FailureRecord]
    # The checks the day could be shown neither to meet nor to miss, because a food in it has no
    # value for the nutrient. Empty in a line written before days could be unsettled.
    unsettled: list[FailureRecord] = []


class RecipeCase(_Record):
    """A week written for one plan, by the pipeline or by the baseline."""

    kind: Literal["recipes"] = "recipes"
    case: str
    system: Literal["pipeline", "baseline"]
    plan: str
    seconds: float
    usage: Usage
    recipes: list[RecipeOutcome]
    days: list[DayOutcome]
    no_waste: int


class ReadItemRecord(_Record):
    name: str
    food_id: str | None
    status: Literal["matched", "needs_amount", "unknown_unit", "missing"]
    amount: float | None
    unit: str | None
    grams: float | None


class ReadingCase(_Record):
    """A typed meal read by the reader."""

    kind: Literal["reading"] = "reading"
    case: str
    meal: str
    seconds: float
    usage: Usage
    # The ReadError's message when the read failed; it names the step and nothing typed.
    error: str | None
    items: list[ReadItemRecord]
    missing: list[str]
    # The reader's total calories for the matched items: None when it matched none or failed.
    kcal: float | None
    # The labelled matched items' calories, from nutrition-mcp; None when the label has none.
    label_kcal: float | None


CaseRecord = Annotated[RecipeCase | ReadingCase, Field(discriminator="kind")]
CASE: TypeAdapter[CaseRecord] = TypeAdapter(CaseRecord)


def recipe_case_id(system: str, plan: str) -> str:
    return f"recipes/{system}/{plan}"


def reading_case_id(meal: str) -> str:
    return f"reading/{meal}"


def first_checks(events: list[RecipeChecked]) -> dict[tuple[DayKey, str], list[str] | None]:
    """For each recipe, the check names of the first time it was checked, if that was its first
    attempt, else None. `events` are in the order the run gave them."""
    first: dict[tuple[DayKey, str], list[str] | None] = {}
    for event in events:
        key = (event.day, event.slot_id)
        if key not in first:
            first[key] = [f.check for f in event.failures] if event.attempt == 1 else None
    return first


def _failures(failures: list[Failure]) -> list[FailureRecord]:
    return [FailureRecord(check=f.check, message=f.message) for f in failures]


def recipe_case(
    *,
    system: Literal["pipeline", "baseline"],
    plan: str,
    rules: PlanRules,
    week: WeekResult,
    first: dict[tuple[DayKey, str], list[str] | None] | None,
    usage: Usage,
    seconds: float,
) -> RecipeCase:
    """The line for a week. `first` is None for the baseline, whose only check is its first."""
    by_key = {(r.spec.day, r.spec.slot.id): r for r in week.recipes}
    reasons = {(day, slot_id): reason for day, slot_id, reason in week.unwritten}
    outcomes: list[RecipeOutcome] = []
    for spec in expand(rules):
        key = (spec.day, spec.slot.id)
        recipe = by_key.get(key)
        if recipe is None:
            outcomes.append(
                RecipeOutcome(
                    day=spec.day,
                    slot_id=spec.slot.id,
                    written=False,
                    unwritten_reason=reasons.get(key, "not written"),
                )
            )
            continue
        total = recipe.nutrition.total if recipe.nutrition is not None else None
        checks = [f.check for f in recipe.failures]
        outcomes.append(
            RecipeOutcome(
                day=spec.day,
                slot_id=spec.slot.id,
                written=True,
                title=recipe.draft.title,
                cuisine=recipe.draft.cuisine,
                ingredients=[(i.id, i.grams) for i in recipe.draft.ingredients],
                kcal=total.get("kcal") if total is not None else None,
                protein_g=total.get("protein_g") if total is not None else None,
                attempts=recipe.attempts,
                portion_scale=recipe.portion_scale,
                failures=_failures(recipe.failures),
                first_failures=checks if first is None else first.get(key),
            )
        )
    return RecipeCase(
        case=recipe_case_id(system, plan),
        system=system,
        plan=plan,
        seconds=round(seconds, 3),
        usage=usage,
        recipes=outcomes,
        days=[
            DayOutcome(day=d.day, failures=_failures(d.failures), unsettled=_failures(d.unsettled))
            for d in week.days
        ],
        no_waste=len(week.no_waste),
    )


def reading_case(
    *,
    meal: str,
    result: ReadResult | None,
    error: str | None,
    label_kcal: float | None,
    usage: Usage,
    seconds: float,
) -> ReadingCase:
    """The line for a typed meal. `result` is None when the read failed with `error`."""
    items = [] if result is None else result.items
    total = result.total if result is not None else None
    return ReadingCase(
        case=reading_case_id(meal),
        meal=meal,
        seconds=round(seconds, 3),
        usage=usage,
        error=error,
        items=[
            ReadItemRecord(
                name=item.name,
                food_id=item.food_id,
                status=item.status,
                amount=item.amount,
                unit=item.unit,
                grams=item.grams,
            )
            for item in items
        ],
        missing=[] if result is None else list(result.missing),
        kcal=total.total.get("kcal") if total is not None else None,
        label_kcal=label_kcal,
    )
