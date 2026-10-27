"""The results file a run writes, summary.md built from those files, and the thresholds check.

A results file is `evals/results/YYYY-MM-DD_<model>_<commit>.json` for a full run of both suites;
a run of one suite or of a slice adds `_<suite>` and `_<slice>` to the name. It records what the
numbers can be traced to (date, model, commit, dataset version, the pipeline's switches and the
settings that decide a pass, the LiteLLM version) and every metric with its counts, calls,
tokens and cost.

summary.md is rebuilt from the newest full, clean, live results file for each suite: a smoke or
partial run, a run from a working tree with changes, and a replay never feed it.
"""

import math
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from cases import EXPECTED_MISSES, MealLabel, Slice, Suite
from metrics import ReadingMetrics, RecipeMetrics, reading_metrics, recipe_metrics
from pydantic import BaseModel, ConfigDict, ValidationError
from records import ReadingCase, RecipeCase, reading_case_id, recipe_case_id

RESULTS_DIR = Path(__file__).resolve().parent / "results"
SUMMARY_NAME = "summary.md"
HEADLINE = "Recipes passing every hard check (share)"


class Switches(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fit_portions: bool
    writer_nutrient_hints: bool


class RunInfo(BaseModel):
    """What a run is: the run folder's run.json, and the head of its results file."""

    model_config = ConfigDict(extra="forbid")

    # The day the run started, which a resumed run keeps.
    date: str
    model: str
    commit: str
    # Whether the code the run used had changes not in `commit`.
    dirty: bool
    dataset_version: str
    litellm_version: str
    suite: Suite
    slice: Slice
    plans: list[str]
    meals: list[str]
    switches: Switches
    replayed: bool
    # The settings that decide whether a recipe or day passes and how hard the pipeline tries.
    settings: dict[str, float | int | bool | str]


class RecipeResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline: RecipeMetrics
    pipeline: RecipeMetrics


class ReadingResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all: ReadingMetrics
    # The meals in cases.EXPECTED_MISSES, and the rest; None when the run had none of them.
    expected_misses: ReadingMetrics | None
    others: ReadingMetrics | None


class Results(RunInfo):
    finished_at: str
    recipes: RecipeResults | None
    reading: ReadingResults | None


def results_stem(info: RunInfo) -> str:
    """The results file's name without .json, which the run's folder is named after too."""
    model = re.sub(r"[^a-z0-9.]+", "-", info.model.lower()).strip("-")
    stem = f"{info.date}_{model}_{info.commit}"
    if info.suite != "all":
        stem += f"_{info.suite}"
    if info.slice != "full":
        stem += f"_{info.slice}"
    return stem


# Each plan is written by the pipeline first, then by the baseline.
SYSTEMS = ("pipeline", "baseline")


def expected_cases(info: RunInfo) -> list[str]:
    """The ids of every case the run takes, in the order it runs them."""
    plans = [recipe_case_id(system, plan) for plan in info.plans for system in SYSTEMS]
    return [*plans, *(reading_case_id(meal) for meal in info.meals)]


def build_results(
    info: RunInfo,
    cases: Sequence[RecipeCase | ReadingCase],
    labels: Mapping[str, MealLabel],
    *,
    finished_at: str,
) -> Results:
    """Every metric of a finished run. Raises ValueError when a case is missing or doubled."""
    seen = [case.case for case in cases]
    twice = sorted({c for c in seen if seen.count(c) > 1})
    if twice:
        raise ValueError(f"cases recorded more than once: {', '.join(twice)}")
    missing = [c for c in expected_cases(info) if c not in seen]
    if missing:
        raise ValueError(f"the run has not finished these cases: {', '.join(missing)}")
    wanted = set(expected_cases(info))
    kept = [case for case in cases if case.case in wanted]
    weeks = [c for c in kept if isinstance(c, RecipeCase)]
    reads = [c for c in kept if isinstance(c, ReadingCase)]
    recipes = None
    if weeks:
        recipes = RecipeResults(
            baseline=recipe_metrics([w for w in weeks if w.system == "baseline"]),
            pipeline=recipe_metrics([w for w in weeks if w.system == "pipeline"]),
        )
    reading = None
    if reads:
        misses = [r for r in reads if r.meal in EXPECTED_MISSES]
        others = [r for r in reads if r.meal not in EXPECTED_MISSES]
        reading = ReadingResults(
            all=reading_metrics(reads, labels),
            expected_misses=reading_metrics(misses, labels) if misses else None,
            others=reading_metrics(others, labels) if others else None,
        )
    return Results(**info.model_dump(), finished_at=finished_at, recipes=recipes, reading=reading)


def write_results(folder: Path, results: Results) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{results_stem(results)}.json"
    path.write_text(results.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


# summary.md


def _counted(part: int, whole: int) -> str:
    if not whole:
        return "none to count"
    return f"{part / whole * 100:.1f}% ({part} of {whole})"


def _number(value: float | None, places: int = 1) -> str:
    return "not known" if value is None else f"{value:,.{places}f}"


def _dollars(value: float | None) -> str:
    if value is None:
        return "not known"
    shown = f"{value:.6f}".rstrip("0")
    whole, _, decimals = shown.partition(".")
    return f"${whole}.{decimals.ljust(2, '0')}"


def _source(name: str, results: Results, cases: str) -> str:
    switches = results.switches
    return (
        f"From `{name}`: model {results.model}, commit {results.commit}, dataset "
        f"{results.dataset_version}, {cases}, started {results.date}. Pipeline switches: portion "
        f"fitting {'on' if switches.fit_portions else 'off'}, nutrient hints "
        f"{'on' if switches.writer_nutrient_hints else 'off'}. LiteLLM {results.litellm_version}."
    )


def _table(header: list[str], rows: Iterable[list[str]]) -> list[str]:
    lines = [f"| {' | '.join(header)} |", f"|{'|'.join('---' for _ in header)}|"]
    return lines + [f"| {' | '.join(row)} |" for row in rows]


Row = tuple[str, Callable[[Any], str]]

RECIPE_ROWS: list[Row] = [
    (HEADLINE, lambda m: _counted(m.recipes_passing, m.recipes_asked)),
    (
        "Recipes passing every hard check at their first attempt (share)",
        lambda m: _counted(m.first_attempt_passing, m.recipes_asked),
    ),
    (
        "Recipes passing the variety checks (share)",
        lambda m: _counted(m.variety_passing, m.recipes_asked),
    ),
    ("Days passing every check (share)", lambda m: _counted(m.days_passing, m.days)),
    (
        "Weeks where every recipe and day passes (share)",
        lambda m: _counted(m.weeks_passing, m.weeks),
    ),
    ("Recipes written (share)", lambda m: _counted(m.recipes_written, m.recipes_asked)),
    ("Model calls per week (mean)", lambda m: _number(m.calls_per_week)),
    ("Tokens per week (mean)", lambda m: _number(m.tokens_per_week)),
    ("Cost per week at list price (mean)", lambda m: _dollars(m.cost_usd_per_week)),
    ("Seconds per week (p95)", lambda m: _number(m.seconds_p95)),
]

READING_ROWS: list[Row] = [
    ("Meals", lambda m: str(m.meals)),
    ("Food precision", lambda m: _counted(m.reader_items_found, m.reader_items)),
    ("Food recall", lambda m: _counted(m.expected_found, m.expected_items)),
    ("Status accuracy", lambda m: _counted(m.status_matched, m.expected_items)),
    ("Asked when it should (share)", lambda m: _counted(m.asked, m.ask_labels)),
    (
        "Labels that ask, food not returned by the reader",
        lambda m: f"{m.ask_labels_unpaired} of {m.ask_labels}",
    ),
    (
        "Calorie error against the label (MAPE)",
        lambda m: (
            "none to count"
            if m.kcal_mape is None
            else f"{m.kcal_mape * 100:.1f}% over {m.kcal_meals} meals"
        ),
    ),
    (
        "Foods not in the data reported missing (share)",
        lambda m: _counted(m.missing_reported, m.missing_labels),
    ),
    ("Reads that failed", lambda m: f"{m.reads_failed} of {m.meals}"),
    ("Model calls per meal (mean)", lambda m: _number(m.calls_per_meal, 2)),
    ("Tokens per meal (mean)", lambda m: _number(m.tokens_per_meal)),
    ("Cost per meal at list price (mean)", lambda m: _dollars(m.cost_usd_per_meal)),
    ("Seconds per meal (p95)", lambda m: _number(m.seconds_p95)),
]


def _cost_note(results: Results) -> str:
    return (
        f"Cost is LiteLLM {results.litellm_version}'s list price for these calls: what they "
        "would cost on the paid tier. Gemini's free tier bills nothing for them."
    )


def _recipes_section(name: str, results: Results) -> list[str]:
    assert results.recipes is not None
    base, pipe = results.recipes.baseline, results.recipes.pipeline
    checks = sorted({*base.failing_checks, *pipe.failing_checks})
    days = sorted({*base.failing_day_checks, *pipe.failing_day_checks})
    lines = [
        "## Recipes",
        "",
        _source(name, results, f"{base.weeks} plans"),
        "",
        *_table(
            ["Measure", "Baseline", "This project"],
            ([label, show(base), show(pipe)] for label, show in RECIPE_ROWS),
        ),
        "",
        "The baseline writes the whole week in one model call, from the same plan lines and "
        "ingredient list the pipeline's writer gets, and its recipes go through the same checks "
        "with no portion fitting and no repair. Every share counts over the recipes the plans "
        "ask for, so a recipe that was never written counts as failing. A day's own checks are "
        "counted on the day. " + _cost_note(results),
    ]
    if checks:
        lines += [
            "",
            "Recipes that ended failing each check:",
            "",
            *_table(
                ["Check", "Baseline", "This project"],
                (
                    [
                        f"`{c}`",
                        str(base.failing_checks.get(c, 0)),
                        str(pipe.failing_checks.get(c, 0)),
                    ]
                    for c in checks
                ),
            ),
        ]
    reasons = sorted({*base.unwritten_by_reason, *pipe.unwritten_by_reason})
    if reasons:
        lines += [
            "",
            "Recipes not written, by reason:",
            "",
            *_table(
                ["Reason", "Baseline", "This project"],
                (
                    [
                        r,
                        str(base.unwritten_by_reason.get(r, 0)),
                        str(pipe.unwritten_by_reason.get(r, 0)),
                    ]
                    for r in reasons
                ),
            ),
        ]
    if days:
        lines += [
            "",
            "Days that ended failing each check:",
            "",
            *_table(
                ["Check", "Baseline", "This project"],
                (
                    [
                        f"`{c}`",
                        str(base.failing_day_checks.get(c, 0)),
                        str(pipe.failing_day_checks.get(c, 0)),
                    ]
                    for c in days
                ),
            ),
        ]
    return lines


def _reading_section(name: str, results: Results) -> list[str]:
    assert results.reading is not None
    groups = [
        ("All meals", results.reading.all),
        (f"Expected misses ({', '.join(EXPECTED_MISSES)})", results.reading.expected_misses),
        ("Other meals", results.reading.others),
    ]
    shown = [(title, metrics) for title, metrics in groups if metrics is not None]
    return [
        "## Reading",
        "",
        _source(name, results, f"{results.reading.all.meals} typed meals"),
        "",
        *_table(
            ["Measure", *(title for title, _ in shown)],
            ([label, *(show(m) for _, m in shown)] for label, show in READING_ROWS),
        ),
        "",
        "A labelled food is found when the reader returns its food id or one the label also "
        "accepts. A label that needs an amount or a unit counts as asked when the reader returned "
        "that food and did not match it; one whose food the reader did not return is counted on "
        "its own row, and a read that failed is left out of both. The expected misses name foods "
        "the reader cannot reach today (see `evals/datasets/README.md`). " + _cost_note(results),
    ]


def _counts(results: Results) -> bool:
    return results.slice == "full" and not results.dirty and not results.replayed


def build_summary(folder: Path) -> str | None:
    """summary.md from the newest full, clean, live results file for each suite in `folder`, or
    None when there is none. Raises ValueError naming a .json file that is not a results file."""
    found: list[tuple[str, Results]] = []
    for path in sorted(folder.glob("*.json")):
        try:
            found.append((path.name, Results.model_validate_json(path.read_text(encoding="utf-8"))))
        except ValidationError as exc:
            raise ValueError(
                f"{path.name} is not a results file: {exc.error_count()} problems"
            ) from exc
    usable = [(name, r) for name, r in found if _counts(r)]
    newest_recipes = max(
        ((n, r) for n, r in usable if r.recipes is not None),
        key=lambda f: f[1].finished_at,
        default=None,
    )
    newest_reading = max(
        ((n, r) for n, r in usable if r.reading is not None),
        key=lambda f: f[1].finished_at,
        default=None,
    )
    if newest_recipes is None and newest_reading is None:
        return None
    lines = [
        "# Eval results",
        "",
        "Rebuilt by `evals/run_evals.py` from the newest full run of each suite. Do not edit it "
        "by hand: every number here is in the results file named under its heading.",
        "",
    ]
    lines += (
        _recipes_section(*newest_recipes)
        if newest_recipes is not None
        else ["## Recipes", "", "No full run of the recipe suite yet."]
    )
    lines += [""]
    lines += (
        _reading_section(*newest_reading)
        if newest_reading is not None
        else ["## Reading", "", "No full run of the reading suite yet."]
    )
    return "\n".join(lines) + "\n"


def write_summary(folder: Path) -> Path | None:
    text = build_summary(folder)
    if text is None:
        return None
    path = folder / SUMMARY_NAME
    path.write_text(text, encoding="utf-8")
    return path


# Thresholds


class ThresholdsError(ValueError):
    """The thresholds file cannot be read."""


Thresholds = dict[str, dict[str, float]]


def load_thresholds(path: Path) -> Thresholds:
    """A TOML file with a [min] table, a [max] table or both, each mapping a metric's dotted path
    in the results file (such as "recipes.pipeline.recipe_pass_share") to a number."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ThresholdsError(f"{path} is not valid TOML: {exc}") from exc
    unknown = sorted(set(data) - {"min", "max"})
    if unknown:
        raise ThresholdsError(f"{path}: only [min] and [max] tables are read, not {unknown}")
    thresholds: Thresholds = {}
    for kind in ("min", "max"):
        table = data.get(kind, {})
        if not isinstance(table, dict):
            raise ThresholdsError(f"{path}: [{kind}] must be a table")
        for name, value in table.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ThresholdsError(f"{path}: {kind} {name} must be a number")
            if not math.isfinite(value):
                raise ThresholdsError(f"{path}: {kind} {name} must be finite")
        thresholds[kind] = {name: float(value) for name, value in table.items()}
    return thresholds


_ABSENT = object()


def _lookup(data: Any, dotted: str) -> Any:
    """The value at a dotted path, None when a part of the way was not measured (a group the run
    had no meals for), or _ABSENT when the path names nothing in a results file."""
    for part in dotted.split("."):
        if data is None:
            return None
        if not isinstance(data, dict) or part not in data:
            return _ABSENT
        data = data[part]
    return data


def check_thresholds(results: Results, thresholds: Thresholds) -> list[str]:
    """One line for each metric below its minimum or above its maximum, or not there to check."""
    data = results.model_dump()
    problems: list[str] = []
    for kind, table in thresholds.items():
        for name, limit in table.items():
            value = _lookup(data, name)
            if value is _ABSENT:
                problems.append(f"{name} is not in the results")
            elif value is None:
                problems.append(f"{name} was not measured in this run")
            elif isinstance(value, bool) or not isinstance(value, int | float):
                problems.append(f"{name} is not a number")
            elif kind == "min" and value < limit:
                problems.append(f"{name} is {value}, below its minimum {limit}")
            elif kind == "max" and value > limit:
                problems.append(f"{name} is {value}, above its maximum {limit}")
    return problems
