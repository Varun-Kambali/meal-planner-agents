"""The results file, summary.md and the thresholds check, on fixture results."""

import json
from pathlib import Path
from typing import Any

import pytest
from eval_helpers import FIXED_LABELS, FIXED_READS, FIXED_WEEKS
from records import ReadingCase, RecipeCase
from results import (
    Results,
    RunInfo,
    Switches,
    ThresholdsError,
    build_results,
    build_summary,
    check_thresholds,
    load_thresholds,
    results_stem,
    write_results,
)


def info(**changes: Any) -> RunInfo:
    base: dict[str, Any] = {
        "date": "2026-10-27",
        "model": "gemini/gemini-3.1-flash-lite",
        "commit": "abc1234",
        "dirty": False,
        "dataset_version": "4a35ea1576a9",
        "litellm_version": "1.105.1",
        "suite": "all",
        "slice": "full",
        "plans": ["a", "b", "c"],
        "meals": ["m1", "m2"],
        "switches": Switches(fit_portions=True, writer_nutrient_hints=True),
        "replayed": False,
        "settings": {"max_repair_attempts": 3},
    }
    return RunInfo.model_validate(base | changes)


def baseline_of(case: RecipeCase) -> RecipeCase:
    return case.model_copy(update={"system": "baseline", "case": f"recipes/baseline/{case.plan}"})


ALL_CASES: list[RecipeCase | ReadingCase] = [
    *FIXED_WEEKS,
    *(baseline_of(c) for c in FIXED_WEEKS),
    *FIXED_READS,
]


def results(**changes: Any) -> Results:
    return build_results(
        info(**changes), ALL_CASES, FIXED_LABELS, finished_at="2026-10-27T10:00:00+00:00"
    )


def table_rows(markdown: str) -> list[list[str]]:
    """The cells of every table row that is not a header separator, in order."""
    rows = []
    for line in markdown.splitlines():
        if line.startswith("|") and not set(line) <= set("|- "):
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


# The results file


def test_the_name_has_the_date_model_and_commit() -> None:
    assert results_stem(info()) == "2026-10-27_gemini-gemini-3.1-flash-lite_abc1234"
    assert results_stem(info(suite="reading")) == (
        "2026-10-27_gemini-gemini-3.1-flash-lite_abc1234_reading"
    )
    assert results_stem(info(slice="smoke")) == (
        "2026-10-27_gemini-gemini-3.1-flash-lite_abc1234_smoke"
    )


def test_the_results_file_holds_the_run_and_every_metric(tmp_path: Path) -> None:
    path = write_results(tmp_path, results())

    data = json.loads(path.read_text())
    assert path.name == "2026-10-27_gemini-gemini-3.1-flash-lite_abc1234.json"
    for key in ("date", "model", "commit", "dataset_version", "litellm_version", "switches"):
        assert key in data
    assert data["switches"] == {"fit_portions": True, "writer_nutrient_hints": True}
    assert data["recipes"]["pipeline"]["recipe_pass_share"] == 0.5714
    assert data["recipes"]["baseline"]["recipes_passing"] == 4
    assert data["recipes"]["pipeline"]["calls"] == 9
    assert data["recipes"]["pipeline"]["cost_usd"] == 0.03
    assert data["reading"]["all"]["food_recall"] == 0.5
    assert data["reading"]["all"]["input_tokens"] == 60
    assert Results.model_validate(data) == results()


def test_reading_is_also_given_for_the_expected_misses_and_the_rest() -> None:
    labels = dict(FIXED_LABELS)
    labels["m01"] = FIXED_LABELS["m1"].model_copy(update={"id": "m01"})
    reads = [
        FIXED_READS[0].model_copy(update={"meal": "m01", "case": "reading/m01"}),
        FIXED_READS[1],
    ]

    built = build_results(
        info(meals=["m01", "m2"], plans=[], suite="reading"),
        reads,
        labels,
        finished_at="2026-10-27T10:00:00+00:00",
    )

    assert built.reading is not None
    assert built.reading.all.meals == 2
    assert built.reading.expected_misses is not None
    assert built.reading.expected_misses.meals == 1
    assert built.reading.expected_misses.food_recall == 1.0
    assert built.reading.others is not None
    assert built.reading.others.meals == 1
    assert built.recipes is None


def test_results_need_every_case_the_run_asked_for() -> None:
    with pytest.raises(ValueError, match="recipes/baseline/c, reading/m2"):
        build_results(
            info(),
            [c for c in ALL_CASES if c.case not in ("recipes/baseline/c", "reading/m2")],
            FIXED_LABELS,
            finished_at="2026-10-27T10:00:00+00:00",
        )


def test_results_refuse_a_case_twice() -> None:
    with pytest.raises(ValueError, match="more than once: reading/m1"):
        build_results(
            info(), [*ALL_CASES, FIXED_READS[0]], FIXED_LABELS, finished_at="2026-10-27T10:00"
        )


# summary.md


def test_the_summary_says_where_its_numbers_come_from(tmp_path: Path) -> None:
    write_results(tmp_path, results())

    summary = build_summary(tmp_path)

    assert summary is not None
    assert "`2026-10-27_gemini-gemini-3.1-flash-lite_abc1234.json`" in summary
    assert "commit abc1234" in summary
    assert "dataset 4a35ea1576a9" in summary
    assert "LiteLLM 1.105.1" in summary
    assert "free tier bills nothing" in summary


def test_the_summary_shows_shares_with_their_counts(tmp_path: Path) -> None:
    write_results(tmp_path, results())

    rows = {row[0]: row[1:] for row in table_rows(build_summary(tmp_path) or "")}

    assert rows["Recipes passing every hard check (share)"] == ["57.1% (4 of 7)", "57.1% (4 of 7)"]
    assert rows["Weeks where every recipe and day passes (share)"] == [
        "33.3% (1 of 3)",
        "33.3% (1 of 3)",
    ]
    assert rows["Cost per week at list price (mean)"] == ["$0.01", "$0.01"]
    assert rows["Seconds per week (p95)"] == ["30.0", "30.0"]
    assert rows["Food recall"][0] == "50.0% (2 of 4)"
    assert rows["Calorie error against the label (MAPE)"][0] == "75.0% over 2 meals"


def test_the_summary_uses_the_newest_full_clean_live_run_for_each_suite(tmp_path: Path) -> None:
    old = results(commit="0000001")
    new = results(commit="0000002").model_copy(update={"finished_at": "2026-10-28T00:00:00"})
    smoke = results(commit="0000003", slice="smoke").model_copy(
        update={"finished_at": "2026-10-29T00:00:00"}
    )
    dirty = results(commit="0000004", dirty=True).model_copy(
        update={"finished_at": "2026-10-29T00:00:00"}
    )
    replayed = results(commit="0000005", replayed=True).model_copy(
        update={"finished_at": "2026-10-29T00:00:00"}
    )
    for each in (old, new, smoke, dirty, replayed):
        write_results(tmp_path, each)

    summary = build_summary(tmp_path) or ""

    assert "commit 0000002" in summary
    for other in ("0000001", "0000003", "0000004", "0000005"):
        assert other not in summary


def test_the_summary_takes_each_suite_from_its_own_newest_file(tmp_path: Path) -> None:
    write_results(tmp_path, results(commit="1111111"))
    reading_only = build_results(
        info(commit="2222222", suite="reading", plans=[]),
        FIXED_READS,
        FIXED_LABELS,
        finished_at="2026-10-28T00:00:00",
    )
    write_results(tmp_path, reading_only)

    summary = build_summary(tmp_path) or ""

    recipes_part, reading_part = summary.split("## Reading")
    assert "commit 1111111" in recipes_part
    assert "commit 2222222" in reading_part


def test_no_full_run_gives_no_summary(tmp_path: Path) -> None:
    write_results(tmp_path, results(slice="smoke"))

    assert build_summary(tmp_path) is None


def test_a_file_that_is_not_a_results_file_is_named(tmp_path: Path) -> None:
    (tmp_path / "notes.json").write_text("{}")

    with pytest.raises(ValueError, match=r"notes\.json"):
        build_summary(tmp_path)


# Thresholds


def write_thresholds(folder: Path, text: str) -> Path:
    path = folder / "thresholds.toml"
    path.write_text(text)
    return path


def test_metrics_at_or_above_their_minimum_and_below_their_maximum_pass(tmp_path: Path) -> None:
    path = write_thresholds(
        tmp_path,
        '[min]\n"recipes.pipeline.recipe_pass_share" = 0.5714\n'
        '"reading.all.food_recall" = 0.4\n'
        '[max]\n"reading.all.kcal_mape" = 0.75\n',
    )

    assert check_thresholds(results(), load_thresholds(path)) == []


def test_a_metric_that_is_missing_or_not_measured_fails(tmp_path: Path) -> None:
    path = write_thresholds(
        tmp_path,
        '[min]\n"recipes.pipeline.no_such_metric" = 0.1\n'
        '"reading.expected_misses.food_recall" = 0.1\n',
    )

    failures = check_thresholds(results(), load_thresholds(path))

    assert failures == [
        "recipes.pipeline.no_such_metric is not in the results",
        "reading.expected_misses.food_recall was not measured in this run",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "[min]\nx = 'high'\n",
        "[least]\nx = 1\n",
        "[min]\nx = true\n",
        "not toml at all [",
        "min = 3\n",
        "[min]\nx = inf\n",
    ],
)
def test_a_thresholds_file_that_cannot_be_read_is_refused(tmp_path: Path, text: str) -> None:
    with pytest.raises(ThresholdsError):
        load_thresholds(write_thresholds(tmp_path, text))


def test_a_threshold_on_something_that_is_not_a_number_fails(tmp_path: Path) -> None:
    path = write_thresholds(tmp_path, '[min]\n"recipes.pipeline.call_errors" = 1\n')

    assert check_thresholds(results(), load_thresholds(path)) == [
        "recipes.pipeline.call_errors is not a number"
    ]


def test_counts_with_nothing_to_count_and_unknown_costs_read_as_such(tmp_path: Path) -> None:
    unpriced = [
        c.model_copy(update={"usage": c.usage.model_copy(update={"cost_unknown_calls": 1})})
        for c in ALL_CASES
    ]
    built = build_results(info(), unpriced, FIXED_LABELS, finished_at="2026-10-27T10:00")
    write_results(tmp_path, built)

    rows = {row[0]: row[1:] for row in table_rows(build_summary(tmp_path) or "")}

    assert rows["Cost per week at list price (mean)"] == ["not known", "not known"]
    assert rows["Cost per meal at list price (mean)"] == ["not known", "not known"]


def test_a_share_with_nothing_to_count_says_so(tmp_path: Path) -> None:
    labels = {"m1": FIXED_LABELS["m1"].model_copy(update={"missing": []})}
    built = build_results(
        info(suite="reading", plans=[], meals=["m1"]),
        [FIXED_READS[0]],
        labels,
        finished_at="2026-10-27T10:00",
    )
    write_results(tmp_path, built)

    rows = {row[0]: row[1:] for row in table_rows(build_summary(tmp_path) or "")}

    # All meals, and the other meals (m1 is not an expected miss).
    assert rows["Foods not in the data reported missing (share)"] == [
        "none to count",
        "none to count",
    ]
