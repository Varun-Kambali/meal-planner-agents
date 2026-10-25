"""The `meal-planner` command, without the web server: `week` writes a week from a rules file
and `read` reads a meal typed in plain words.

Progress goes to stderr one line per event. It holds the kind of event, slot ids (the plan's own
slugs, such as "breakfast"), days, an attempt number and the names of checks, never the plan's
free text or what the model wrote. The week goes to `--out`, or to stdout; what `read` finds goes
to stdout. Exit codes: 0 a week was written (recipes that still fail are in it) or a meal was
read, 1 the app could not start or the run failed, 2 the rules or the text could not be used,
130 the run was stopped with Ctrl-C.

Settings come from the environment only; the command does not read a .env file, so run it with
`uv run --env-file .env meal-planner ...` to use one.
"""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from meal_planner.config import Settings
from meal_planner.llm import ModelPort
from meal_planner.nutrition import NutritionPort
from meal_planner.problems import Problem, meal_text_problems, problems_from, unknown_foods, where
from meal_planner.reader import ReadError, ReadResult, read_meal
from meal_planner.rules import PlanRules
from meal_planner.startup import Services, StartupError, start_services
from meal_planner.week import run_week
from meal_planner.week_events import (
    RecipeChecked,
    RepairStarted,
    SlotFailed,
    SlotStarted,
    SlotWritten,
    WeekDone,
    WeekEvent,
    WeekResult,
)

PROG = "meal-planner"


def _say(text: str) -> None:
    print(text, file=sys.stderr)


def _refuse(problems: Sequence[Problem], source: str) -> int:
    for problem in problems:
        field = where(problem)
        _say(f"{PROG}: {source}: {field + ': ' if field else ''}{problem['msg']}")
    return 2


def progress_line(event: WeekEvent) -> str:
    """One line for an event: the app's own names and the plan's slot ids, no free text."""
    match event:
        case SlotStarted() | SlotWritten() | RepairStarted():
            return (
                f"{event.kind} {event.slot_id} days={','.join(event.days)} attempt={event.attempt}"
            )
        case SlotFailed():
            final = "yes" if event.final else "no"
            days = ",".join(event.days)
            return f"{event.kind} {event.slot_id} days={days} attempt={event.attempt} final={final}"
        case RecipeChecked():
            names = ",".join(failure.check for failure in event.failures) or "none"
            return (
                f"{event.kind} {event.day} {event.slot_id} attempt={event.attempt} failures={names}"
            )
        case WeekDone():
            week = event.week
            return (
                f"{event.kind} recipes={len(week.recipes)} unwritten={len(week.unwritten)} "
                f"calls={week.calls}"
            )


def _read_rules(path: Path) -> PlanRules | int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _say(f"{PROG}: cannot read {path}: {exc.strerror or type(exc).__name__}")
        return 2
    try:
        return PlanRules.model_validate_json(text)
    except ValidationError as exc:
        return _refuse(problems_from(exc.errors()), str(path))


async def _with_services[T](
    settings: Settings,
    model: ModelPort | None,
    nutrition: NutritionPort | None,
    work: Callable[[Services], Awaitable[T | int]],
) -> T | int:
    """Start the services, run `work` with them, and turn a failure into an exit code."""
    try:
        async with start_services(settings, model=model, nutrition=nutrition) as services:
            return await work(services)
    except StartupError as exc:
        _say(f"{PROG}: {exc}")
        return 1
    except Exception as exc:
        # The exception's text can quote the request, so only its class is shown.
        _say(f"{PROG}: the run stopped before it finished ({type(exc).__name__})")
        return 1


async def _write_week(rules: PlanRules, settings: Settings, services: Services) -> WeekResult | int:
    problems = unknown_foods(rules, services.catalogue, under=())
    if problems:
        return _refuse(problems, "rules")
    week: WeekResult | None = None
    async for event in run_week(
        rules,
        model=services.model,
        nutrition=services.nutrition,
        catalogue=services.catalogue,
        settings=settings,
    ):
        _say(progress_line(event))
        if isinstance(event, WeekDone):
            week = event.week
    assert week is not None  # run_week always ends with week_done
    return week


async def _read_text(text: str, settings: Settings, services: Services) -> ReadResult | int:
    try:
        return await read_meal(
            text,
            model=services.model,
            nutrition=services.nutrition,
            catalogue=services.catalogue,
            settings=settings,
        )
    except ReadError as exc:
        # The message names the step and holds nothing that was typed.
        _say(f"{PROG}: {exc}")
        return 1


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    model: ModelPort | None = None,
    nutrition: NutritionPort | None = None,
) -> int:
    """Run the command. `settings`, `model` and `nutrition` are for tests, which pass fakes."""
    parser = argparse.ArgumentParser(
        prog=PROG, description="Write recipes for a meal plan, and read the meals you eat."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    week = commands.add_parser("week", help="write a week of recipes for a rules file")
    week.add_argument("rules", type=Path, help="the plan rules, as JSON")
    week.add_argument("--out", type=Path, help="write the week here instead of to stdout")
    read = commands.add_parser("read", help="read a meal typed in plain words")
    read.add_argument("text", help="what you ate, in your own words (quote it)")
    args = parser.parse_args(argv)

    if args.command == "read":
        return _read(args.text, settings or Settings(), model, nutrition)
    return _week(args.rules, args.out, settings, model, nutrition)


def _week(
    rules_path: Path,
    out: Path | None,
    settings: Settings | None,
    model: ModelPort | None,
    nutrition: NutritionPort | None,
) -> int:
    rules = _read_rules(rules_path)
    if isinstance(rules, int):
        return rules
    settings = settings or Settings()
    try:
        result = asyncio.run(
            _with_services(
                settings, model, nutrition, lambda services: _write_week(rules, settings, services)
            )
        )
    except KeyboardInterrupt:
        return 130
    if isinstance(result, int):
        return result

    text = result.model_dump_json(indent=2) + "\n"
    if out is None:
        sys.stdout.write(text)
        return 0
    try:
        out.write_text(text, encoding="utf-8")
    except OSError as exc:
        _say(f"{PROG}: cannot write {out}: {exc.strerror or type(exc).__name__}")
        return 1
    _say(f"wrote {out}")
    return 0


def _read(
    text: str, settings: Settings, model: ModelPort | None, nutrition: NutritionPort | None
) -> int:
    problems = meal_text_problems(text, settings, under=())
    if problems:
        return _refuse(problems, "read")
    try:
        result = asyncio.run(
            _with_services(
                settings, model, nutrition, lambda services: _read_text(text, settings, services)
            )
        )
    except KeyboardInterrupt:
        return 130
    if isinstance(result, int):
        return result
    sys.stdout.write(result.model_dump_json(indent=2) + "\n")
    return 0
