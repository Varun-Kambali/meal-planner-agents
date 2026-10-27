"""Running the eval's cases one at a time, and keeping each one as it finishes.

A recipe case writes a week for a plan, through the pipeline (`run_week`) or the single-call
baseline; a reading case reads one typed meal (`read_meal`). Every model call a case makes is
counted (`MeteredModel`), and the case's line goes to the run's cases.jsonl the moment it ends,
so a run that stops keeps what it finished and `--resume` carries on from there.

A run stops (`RunStopped`) when the model's rate limit is reached, after the model client's own
tries, or when nutrition-mcp cannot be reached: both say nothing about the pipeline, the
baseline or the reader, so the case is not scored and is run again on resume. Any other trouble
(a reply that cannot be used, a timeout) is part of the case's result.
"""

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from baseline import check_baseline_week, write_week_single_call
from cases import MealLabel, Selection
from metrics import recipe_passes
from nutrition_mcp.schemas import MealItemIn
from pydantic import BaseModel, ValidationError
from records import (
    CASE,
    CaseRecord,
    ReadingCase,
    RecipeCase,
    Usage,
    first_checks,
    reading_case,
    reading_case_id,
    recipe_case,
    recipe_case_id,
)
from results import SYSTEMS

from meal_planner.catalogue import Catalogue
from meal_planner.config import Settings
from meal_planner.llm import (
    RATE_LIMITED,
    SERVER_ERROR,
    TIMEOUT,
    ModelPort,
    ModelReply,
    RecordingModel,
)
from meal_planner.nutrition import NutritionError, NutritionPort
from meal_planner.reader import ReadError, ReadResult, read_meal
from meal_planner.rules import PlanRules
from meal_planner.week import NUTRITION_UNAVAILABLE, run_week
from meal_planner.week_events import RecipeChecked, WeekDone, WeekResult

RATE_LIMIT_STOP = "the model's rate limit was reached"
NUTRITION_STOP = "nutrition-mcp could not be reached"
# What a call that raised instead of replying is counted under.
RAISED = "raised"


class RunStopped(Exception):
    """The run cannot go on for now. The cases already finished are kept."""


class MeteredModel:
    """Passes calls through to a model and adds up what each one used."""

    def __init__(self, inner: ModelPort) -> None:
        self._inner = inner
        self.usage = Usage()

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        try:
            reply = await self._inner.structured(
                task=task, system=system, user=user, schema=schema, timeout_s=timeout_s
            )
        except Exception:
            self.usage.calls += 1
            self.usage.add_error(RAISED)
            raise
        self.usage.add(reply)
        return reply


def _stop_if_rate_limited(usage: Usage) -> None:
    if usage.errors.get(RATE_LIMITED):
        raise RunStopped(RATE_LIMIT_STOP)


# The cases


async def pipeline_case(
    plan: str,
    rules: PlanRules,
    *,
    model: ModelPort,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
) -> RecipeCase:
    """The pipeline's week for a plan: write, check, repair, as the app runs it."""
    metered = MeteredModel(model)
    checked: list[RecipeChecked] = []
    week: WeekResult | None = None
    started = time.perf_counter()
    async for event in run_week(
        rules, model=metered, nutrition=nutrition, catalogue=catalogue, settings=settings
    ):
        if isinstance(event, RecipeChecked):
            checked.append(event)
        elif isinstance(event, WeekDone):
            week = event.week
    seconds = time.perf_counter() - started
    assert week is not None  # run_week always ends with week_done
    _stop_if_rate_limited(metered.usage)
    if any(f.check == NUTRITION_UNAVAILABLE for r in week.recipes for f in r.failures):
        raise RunStopped(NUTRITION_STOP)
    return recipe_case(
        system="pipeline",
        plan=plan,
        rules=rules,
        week=week,
        first=first_checks(checked),
        usage=metered.usage,
        seconds=seconds,
    )


async def baseline_case(
    plan: str,
    rules: PlanRules,
    *,
    model: ModelPort,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
) -> RecipeCase:
    """The baseline's week for a plan: one call, then the same checks, no fitting, no repair."""
    metered = MeteredModel(model)
    started = time.perf_counter()
    written = await write_week_single_call(
        rules, model=metered, catalogue=catalogue, settings=settings
    )
    _stop_if_rate_limited(metered.usage)
    try:
        week = await check_baseline_week(
            rules, written, nutrition=nutrition, catalogue=catalogue, settings=settings
        )
    except NutritionError as exc:
        raise RunStopped(NUTRITION_STOP) from exc
    seconds = time.perf_counter() - started
    return recipe_case(
        system="baseline",
        plan=plan,
        rules=rules,
        week=week,
        first=None,
        usage=metered.usage,
        seconds=seconds,
    )


async def label_kcal(label: MealLabel, nutrition: NutritionPort) -> float | None:
    """The calories of the meal's labelled matched items, from nutrition-mcp's compute_meal on
    their grams; None when the label has no matched item."""
    items = [
        MealItemIn(food_id=item.food_id, amount=item.grams, unit="g")
        for item in label.expected
        if item.status == "matched" and item.grams is not None
    ]
    if not items:
        return None
    try:
        meal = await nutrition.meal(items)
    except NutritionError as exc:
        if exc.unreachable:
            raise RunStopped(NUTRITION_STOP) from exc
        raise
    return meal.total.get("kcal")


async def reading_case_run(
    label: MealLabel,
    *,
    model: ModelPort,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
) -> ReadingCase:
    """The reader on one typed meal, with the label's calories to score it against."""
    labelled = await label_kcal(label, nutrition)
    metered = MeteredModel(model)
    result: ReadResult | None = None
    error: str | None = None
    started = time.perf_counter()
    try:
        result = await read_meal(
            label.text, model=metered, nutrition=nutrition, catalogue=catalogue, settings=settings
        )
    except ReadError as exc:
        _stop_if_rate_limited(metered.usage)
        model_down = bool(metered.usage.errors.keys() & {TIMEOUT, SERVER_ERROR})
        if exc.unavailable and not model_down:
            # Nothing the model did made it unavailable, so nutrition-mcp was out of reach.
            raise RunStopped(NUTRITION_STOP) from exc
        error = str(exc)
    seconds = time.perf_counter() - started
    return reading_case(
        meal=label.id,
        result=result,
        error=error,
        label_kcal=labelled,
        usage=metered.usage,
        seconds=seconds,
    )


# Keeping finished cases


class CaseLog:
    """The run's cases.jsonl: one line for each finished case, appended as it finishes.

    A last line with no newline was cut off mid-write; it is dropped, and its case runs again.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: list[CaseRecord] = []
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            text = text[: text.rfind("\n") + 1]
            path.write_text(text, encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            try:
                self.records.append(CASE.validate_json(line))
            except ValidationError as exc:
                raise ValueError(f"{path}, line {number}, is not a case line") from exc

    @property
    def done(self) -> set[str]:
        return {record.case for record in self.records}

    def append(self, record: CaseRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(CASE.dump_json(record).decode() + "\n")
        self.records.append(record)


class CaseRecorder:
    """Records each case's model calls apart, and adds them to the recording only once the case
    has finished. A case that stopped part-way runs again on resume, and its first calls would
    otherwise be in the recording twice, with the abandoned replies first."""

    def __init__(self, inner: ModelPort, path: Path, pending: Path) -> None:
        self._inner = inner
        self._path = path
        self._pending = pending

    def start(self) -> ModelPort:
        self._pending.unlink(missing_ok=True)
        return RecordingModel(self._inner, self._pending)

    def keep(self) -> None:
        if self._pending.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as file:
                file.write(self._pending.read_text(encoding="utf-8"))
            self._pending.unlink()


def describe(record: CaseRecord) -> str:
    """One progress line for a finished case: ids, counts and codes, no model text."""
    usage = record.usage
    if isinstance(record, RecipeCase):
        passing = sum(map(recipe_passes, record.recipes))
        outcome = f"{passing} of {len(record.recipes)} recipes pass"
    else:
        outcome = "read failed" if record.error else f"{len(record.items)} items"
    return f"{record.case}: {outcome}, {usage.calls} calls, {record.seconds:.1f} s"


async def run_cases(
    selection: Selection,
    plans: Mapping[str, PlanRules],
    labels: Mapping[str, MealLabel],
    *,
    model: ModelPort,
    nutrition: NutritionPort,
    catalogue: Catalogue,
    settings: Settings,
    log: CaseLog,
    recorder: CaseRecorder | None = None,
    say: Callable[[str], None] = print,
) -> None:
    """Run every case of the selection that the log does not hold yet, in order."""
    done = log.done
    for plan in selection.plans:
        for system in SYSTEMS:
            if recipe_case_id(system, plan) in done:
                continue
            run = pipeline_case if system == "pipeline" else baseline_case
            record: CaseRecord = await run(
                plan,
                plans[plan],
                model=recorder.start() if recorder else model,
                nutrition=nutrition,
                catalogue=catalogue,
                settings=settings,
            )
            _finish(record, log, recorder, say)
    for meal in selection.meals:
        if reading_case_id(meal) in done:
            continue
        record = await reading_case_run(
            labels[meal],
            model=recorder.start() if recorder else model,
            nutrition=nutrition,
            catalogue=catalogue,
            settings=settings,
        )
        _finish(record, log, recorder, say)


def _finish(
    record: CaseRecord, log: CaseLog, recorder: CaseRecorder | None, say: Callable[[str], None]
) -> None:
    if recorder is not None:
        recorder.keep()
    log.append(record)
    say(describe(record))
