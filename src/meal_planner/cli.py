"""The `meal-planner` command: write a week from a rules file, without the web server.

Progress goes to stderr one line per event and holds only names the app made up (the kind of
event, a slot id, days, an attempt number, the names of checks), never the plan's words. The week
goes to `--out`, or to stdout. Exit codes: 0 a week was written (recipes that still fail are in
it), 1 the app could not start or the run failed, 2 the rules could not be used.

Settings come from the environment only; the command does not read a .env file, so run it with
`uv run --env-file .env meal-planner ...` to use one.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from meal_planner.config import Settings
from meal_planner.llm import ModelPort
from meal_planner.nutrition import NutritionPort
from meal_planner.problems import Problem, problems_from, unknown_foods, where
from meal_planner.rules import PlanRules
from meal_planner.startup import StartupError, start_services
from meal_planner.week import (
    RecipeChecked,
    RepairStarted,
    SlotFailed,
    SlotStarted,
    SlotWritten,
    WeekDone,
    WeekEvent,
    WeekResult,
    run_week,
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
    """One line for an event, made only of names the app chose."""
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


async def _write_week(
    rules: PlanRules,
    settings: Settings,
    model: ModelPort | None,
    nutrition: NutritionPort | None,
) -> WeekResult | int:
    try:
        async with start_services(settings, model=model, nutrition=nutrition) as services:
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
    except StartupError as exc:
        _say(f"{PROG}: {exc}")
        return 1
    except Exception as exc:
        # The exception's text can quote the request, so only its class is shown.
        _say(f"{PROG}: the run stopped before it finished ({type(exc).__name__})")
        return 1
    assert week is not None  # run_week always ends with week_done
    return week


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    model: ModelPort | None = None,
    nutrition: NutritionPort | None = None,
) -> int:
    """Run the command. `settings`, `model` and `nutrition` are for tests, which pass fakes."""
    parser = argparse.ArgumentParser(prog=PROG, description="Write recipes for a meal plan.")
    commands = parser.add_subparsers(dest="command", required=True)
    week = commands.add_parser("week", help="write a week of recipes for a rules file")
    week.add_argument("rules", type=Path, help="the plan rules, as JSON")
    week.add_argument("--out", type=Path, help="write the week here instead of to stdout")
    args = parser.parse_args(argv)

    rules = _read_rules(args.rules)
    if isinstance(rules, int):
        return rules
    try:
        result = asyncio.run(_write_week(rules, settings or Settings(), model, nutrition))
    except KeyboardInterrupt:
        return 130
    if isinstance(result, int):
        return result

    text = result.model_dump_json(indent=2) + "\n"
    if args.out is None:
        sys.stdout.write(text)
        return 0
    try:
        args.out.write_text(text, encoding="utf-8")
    except OSError as exc:
        _say(f"{PROG}: cannot write {args.out}: {exc.strerror or type(exc).__name__}")
        return 1
    _say(f"wrote {args.out}")
    return 0
