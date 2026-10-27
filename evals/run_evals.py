"""Run the evals: the pipeline against the single-call baseline on the plans, and the reader on
the typed meals. Every nutrition number comes from nutrition-mcp, started as the app starts it.

    uv run --env-file .env python evals/run_evals.py --suite all          # needs the model's key
    uv run python evals/run_evals.py --smoke --replay evals/recordings/smoke.jsonl \\
        --check evals/thresholds.toml                                      # needs no key

Each finished case is kept in `<out>/runs/<name>/cases.jsonl`. A run that stops (the model's
rate limit, nutrition-mcp out of reach, Ctrl-C) keeps them: run the same command again with
`--resume <that folder>` to finish it. A finished run writes `<out>/<name>.json`, and a full one
rebuilds `<out>/summary.md`.

Progress goes to stdout, one line a case, with ids and counts only. nutrition-mcp writes its own
log to stderr.

Exit codes: 0 done (and every metric within its threshold with --check); 1 a metric is outside
its threshold, a replay found a call with no recording, or the run could not start or failed;
2 the arguments, a named file or a resumed folder cannot be used; 3 the run stopped early and
can be resumed; 130 stopped with Ctrl-C, and can be resumed.
"""

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from importlib import metadata
from pathlib import Path

from cases import MealLabel, SelectionError, load_meals, load_plans, select
from dataset_version import dataset_version
from pydantic import ValidationError
from results import (
    RESULTS_DIR,
    Results,
    RunInfo,
    Switches,
    Thresholds,
    ThresholdsError,
    build_results,
    check_thresholds,
    load_thresholds,
    results_stem,
    write_results,
    write_summary,
)
from runner import CaseLog, CaseRecorder, RunStopped, run_cases

from meal_planner.config import REPO_ROOT, Settings
from meal_planner.llm import NO_RECORDING, LiteLLMModel, ModelPort, Recording, ReplayModel
from meal_planner.nutrition import NutritionPort
from meal_planner.startup import StartupError, check_model_key, start_services

PROG = "run_evals"
RUN_FILE = "run.json"
CASES_FILE = "cases.jsonl"
PENDING_FILE = "recording-pending.jsonl"
# The settings that decide whether a recipe or day passes, and how hard the pipeline tries. The
# data paths are recorded beside them (`data_paths`).
RECORDED_SETTINGS = (
    "recipe_kcal_tolerance",
    "recipe_protein_min_share",
    "day_kcal_tolerance",
    "max_repair_attempts",
    "variety_jaccard_fail_at",
    "no_waste_keeps_days",
    "no_waste_min_pack_share",
    "portion_scale_min",
    "portion_scale_max",
    "max_concurrent_model_calls",
    "slot_call_timeout_s",
    "reader_call_timeout_s",
)
# What may differ between a run and its resumption: the day it is resumed and whether the tree
# has changes (which only ever turns a run dirty).
_MAY_CHANGE_ON_RESUME = {"date", "dirty"}
# Paths whose changes would make the code differ from the commit. Results and recordings are
# what a run writes, so they are left out.
_CODE_PATHS = (
    "src",
    "evals",
    "data",
    "pyproject.toml",
    "uv.lock",
    ":(exclude)evals/results",
    ":(exclude)evals/recordings",
)


class UsageError(Exception):
    """The arguments, or a file they name, cannot be used. Exit code 2."""


def _err(text: str) -> None:
    print(f"{PROG}: {text}", file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG, description="Run the recipe and reading evals and write their results."
    )
    parser.add_argument("--suite", choices=["recipes", "reading", "all"], default="all")
    parser.add_argument("--model", help="the LiteLLM model id (default: the app's setting)")
    parser.add_argument("--limit", type=int, help="keep the first N plans and the first N meals")
    parser.add_argument("--only", help="comma-separated plan and meal ids to run")
    parser.add_argument("--smoke", action="store_true", help="run the smoke slice")
    parser.add_argument("--record", type=Path, help="record every model reply to this file")
    parser.add_argument("--replay", type=Path, help="answer from this recording, with no key")
    parser.add_argument("--check", type=Path, help="a thresholds TOML file to hold metrics to")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="where results go")
    parser.add_argument("--resume", type=Path, help="a run folder to finish")
    parser.add_argument("--fit-portions", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--writer-nutrient-hints", action=argparse.BooleanOptionalAction, default=None
    )
    return parser


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.strip()


def git_commit() -> str:
    return _git("rev-parse", "--short=7", "HEAD") or "unknown"


def git_dirty() -> bool:
    """Whether the code a run uses differs from the commit; True when git cannot say."""
    status = _git("status", "--porcelain", "--untracked-files=all", "--", *_CODE_PATHS)
    return status is None or status != ""


def data_paths(settings: Settings) -> tuple[dict[str, str], bool]:
    """The catalogue and nutrition-mcp's extra data folders as a results file records them, and
    whether they are all inside the repository.

    A path inside the repository is written relative to it, so a results file names the file
    the commit holds and no folder of this machine. One outside it is written as given; such a
    run cannot be traced to the commit, and is marked dirty so it never feeds summary.md.
    """

    def shown(path: Path) -> tuple[str, bool]:
        try:
            return path.resolve().relative_to(REPO_ROOT).as_posix(), True
        except ValueError:
            return str(path), False

    catalogue, catalogue_inside = shown(settings.catalogue_path)
    folders = [shown(Path(part)) for part in settings.nutrition_data_dirs.split(os.pathsep) if part]
    recorded = {
        "catalogue_path": catalogue,
        "nutrition_data_dirs": os.pathsep.join(path for path, _ in folders),
    }
    return recorded, catalogue_inside and all(inside for _, inside in folders)


def recorded_model(path: Path) -> str:
    """The one model a recording's calls were made with."""
    models = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            models.add(Recording.model_validate_json(line).model)
    if not models:
        raise UsageError(f"{path} holds no recorded calls")
    if len(models) > 1:
        raise UsageError(f"{path} holds calls from more than one model: {sorted(models)}")
    return models.pop()


def _thresholds(path: Path | None) -> Thresholds | None:
    if path is None:
        return None
    if not path.exists():
        raise UsageError(f"{path} does not exist: the thresholds are set from the first full run")
    try:
        return load_thresholds(path)
    except ThresholdsError as exc:
        raise UsageError(str(exc)) from exc


def _resume_info(folder: Path, wanted: RunInfo) -> RunInfo:
    """The run folder's run.json, if it describes the run asked for now."""
    path = folder / RUN_FILE
    if not path.exists():
        raise UsageError(f"{folder} is not a run folder: it has no {RUN_FILE}")
    try:
        kept = RunInfo.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise UsageError(f"{path} cannot be read: {exc.error_count()} problems") from exc
    differ = [
        name
        for name in RunInfo.model_fields
        if name not in _MAY_CHANGE_ON_RESUME and getattr(kept, name) != getattr(wanted, name)
    ]
    if differ:
        raise UsageError(
            f"{folder} was a different run; these differ now: {', '.join(differ)}. "
            "Resume with the command that started it, at the same commit."
        )
    return kept.model_copy(update={"dirty": kept.dirty or wanted.dirty})


def _settings(args: argparse.Namespace, base: Settings, model_id: str) -> Settings:
    update: dict[str, object] = {"model": model_id}
    if args.fit_portions is not None:
        update["fit_portions"] = args.fit_portions
    if args.writer_nutrient_hints is not None:
        update["writer_nutrient_hints"] = args.writer_nutrient_hints
    return base.model_copy(update=update)


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    model: ModelPort | None = None,
    nutrition: NutritionPort | None = None,
) -> int:
    """Run the evals. `settings`, `model` and `nutrition` are for tests, which pass stand-ins."""
    args = _parser().parse_args(argv)
    try:
        return _run(args, settings or Settings(), model, nutrition)
    except UsageError as exc:
        _err(str(exc))
        return 2


def _run(
    args: argparse.Namespace,
    base_settings: Settings,
    model: ModelPort | None,
    nutrition: NutritionPort | None,
) -> int:
    if args.record is not None and args.replay is not None:
        raise UsageError("--record and --replay cannot be used together")
    if args.replay is not None and args.model is not None:
        raise UsageError("--model cannot be used with --replay: the model is the recording's")
    if args.replay is not None and not args.replay.exists():
        raise UsageError(
            f"{args.replay} does not exist: record it first with --record {args.replay} "
            "(this calls the model with its key)"
        )
    thresholds = _thresholds(args.check)
    if args.record is not None and args.record.exists() and args.resume is None:
        raise UsageError(f"{args.record} exists: delete it to record afresh, or pass --resume")

    model_id = recorded_model(args.replay) if args.replay else (args.model or base_settings.model)
    settings = _settings(args, base_settings, model_id)
    plans, labels = load_plans(), load_meals()
    try:
        selection = select(
            args.suite,
            list(plans),
            list(labels),
            smoke=args.smoke,
            only=[i.strip() for i in args.only.split(",") if i.strip()] if args.only else None,
            limit=args.limit,
        )
    except SelectionError as exc:
        raise UsageError(str(exc)) from exc

    # The key is looked for before a run folder is made, so a run without it leaves nothing.
    if args.replay is not None:
        inner: ModelPort = ReplayModel(args.replay)
    elif model is not None:
        inner = model
    else:
        try:
            check_model_key(settings.model)
        except StartupError as exc:
            _err(str(exc))
            return 1
        inner = LiteLLMModel(settings.model)

    paths, inside = data_paths(settings)
    info = RunInfo(
        date=date.today().isoformat(),
        model=model_id,
        commit=git_commit(),
        dirty=git_dirty() or not inside,
        dataset_version=dataset_version(),
        litellm_version=metadata.version("litellm"),
        suite=args.suite,
        slice=selection.slice,
        plans=selection.plans,
        meals=selection.meals,
        switches=Switches(
            fit_portions=settings.fit_portions,
            writer_nutrient_hints=settings.writer_nutrient_hints,
        ),
        replayed=args.replay is not None,
        settings={**{name: getattr(settings, name) for name in RECORDED_SETTINGS}, **paths},
    )
    if args.resume is not None:
        folder = args.resume
        info = _resume_info(folder, info)
    else:
        folder = args.out / "runs" / results_stem(info)
        # A folder with no finished case (the services did not start, say) is started afresh.
        if (folder / CASES_FILE).exists():
            raise UsageError(
                f"{folder} exists: finish it with --resume {folder}, or delete it to start again"
            )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / RUN_FILE).write_text(info.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if info.dirty:
        _err(
            "the code or data differ from the commit, so these results will not feed "
            "summary.md; commit first, with the data inside the repository, for results that "
            "can be published"
        )

    recorder = (
        CaseRecorder(inner, args.record, folder / PENDING_FILE) if args.record is not None else None
    )
    try:
        log = CaseLog(folder / CASES_FILE)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    hint = f"finished cases are kept in {folder}; run the same command with --resume {folder}"

    async def work() -> str | None:
        """Run the cases; the reason the run stopped early, or None when it finished. The stop is
        caught inside the services' block, whose task groups would wrap it in a group."""
        async with start_services(settings, model=inner, nutrition=nutrition) as services:
            try:
                await run_cases(
                    selection,
                    plans,
                    labels,
                    model=services.model,
                    nutrition=services.nutrition,
                    catalogue=services.catalogue,
                    settings=settings,
                    log=log,
                    recorder=recorder,
                )
            except RunStopped as exc:
                return str(exc)
        return None

    try:
        stopped = asyncio.run(work())
    except BaseException as exc:
        # nutrition-mcp's client runs in task groups, which can wrap what was raised in a group.
        inner_exc = _innermost(exc)
        if isinstance(inner_exc, KeyboardInterrupt):
            _err(f"stopped; {hint}")
            return 130
        if not isinstance(inner_exc, Exception):
            raise
        if isinstance(inner_exc, StartupError):
            _err(str(inner_exc))
            return 1
        # The text of an exception can quote a prompt, so only its class is shown.
        _err(f"the run stopped before it finished ({type(inner_exc).__name__}); {hint}")
        return 1
    if stopped is not None:
        _err(f"stopped: {stopped}; {hint}")
        return 3

    return _finish(args, info, log, labels, thresholds)


def _innermost(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


def _finish(
    args: argparse.Namespace,
    info: RunInfo,
    log: CaseLog,
    labels: Mapping[str, MealLabel],
    thresholds: Thresholds | None,
) -> int:
    finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    results = build_results(info, log.records, labels, finished_at=finished_at)
    path = write_results(args.out, results)
    print(f"wrote {path}")
    if info.slice == "full":
        summary = write_summary(args.out)
        if summary is not None:
            print(f"wrote {summary}")
    _print_headline(results)
    code = 0
    missed = sum(record.usage.errors.get(NO_RECORDING, 0) for record in log.records)
    if missed:
        _err(f"{missed} calls had no recording: the prompts changed since it was made")
        code = 1
    if thresholds is not None:
        problems = check_thresholds(results, thresholds)
        for problem in problems:
            _err(f"threshold not met: {problem}")
        if problems:
            code = 1
        else:
            print(f"every metric in {args.check} is within its threshold")
    return code


def _print_headline(results: Results) -> None:
    if results.recipes is not None:
        for system in ("baseline", "pipeline"):
            m = getattr(results.recipes, system)
            print(
                f"recipes, {system}: {m.recipes_passing} of {m.recipes_asked} pass every hard "
                f"check, {m.weeks_passing} of {m.weeks} weeks pass"
            )
    if results.reading is not None:
        m = results.reading.all
        print(
            f"reading: {m.expected_found} of {m.expected_items} labelled foods found, "
            f"{m.reads_failed} of {m.meals} reads failed"
        )


if __name__ == "__main__":
    sys.exit(main())
