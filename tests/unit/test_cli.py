"""The meal-planner command: arguments, progress on stderr, the week as JSON, and its exit codes."""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from app_helpers import MARK, ExplodingNutrition, marked_plan, renamed_replies
from nutrition_mcp.schemas import MealItemIn, MealResult
from reader_helpers import ReaderNutrition, reading, said, write_reader_catalogue
from week_helpers import (
    PLAN,
    SpyNutrition,
    changed,
    model_for,
    repair_reply,
    without_protein,
    write_catalogue_file,
)

from meal_planner import tracing
from meal_planner.cli import main, progress_line
from meal_planner.config import Settings
from meal_planner.llm import RATE_LIMITED, FakeModel, ModelReply
from meal_planner.nutrition import NutritionError
from meal_planner.reader import ReadResult
from meal_planner.recipes import DayReport, Failure
from meal_planner.rules import DayKey
from meal_planner.summary import WeekSummary
from meal_planner.week_events import WeekDone, WeekResult

EXTRA_FOODS = ("brinjal",)


def rules_file(folder: Path, **changes: Any) -> Path:
    plan = json.loads(PLAN.read_text(encoding="utf-8")) | changes
    path = folder / "rules.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def run_cli(
    folder: Path, *args: str, model: Any = None, nutrition: Any = None, **settings: Any
) -> int:
    values = {"catalogue_path": write_catalogue_file(folder, *EXTRA_FOODS)} | settings
    return main(
        list(args),
        settings=Settings(**values),
        model=model or model_for(),
        nutrition=nutrition or SpyNutrition(*EXTRA_FOODS),
    )


def lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


# A week


def test_the_week_is_written_to_the_out_file_with_progress_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "week.json"

    code = run_cli(tmp_path, "week", str(rules_file(tmp_path)), "--out", str(out))

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    week = WeekResult.model_validate_json(out.read_text(encoding="utf-8"))
    assert len(week.recipes) == 28
    progress = lines(captured.err)
    assert progress[0].startswith("slot_started ")
    assert sum(line.startswith("recipe_checked ") for line in progress) == 28
    assert progress[-2].startswith("week_done ")
    assert progress[-2].endswith(" day_failures=none day_unsettled=none")
    assert progress[-1] == f"wrote {out}"


def test_without_out_the_week_goes_to_stdout_and_only_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(tmp_path, "week", str(rules_file(tmp_path)))

    captured = capsys.readouterr()
    assert code == 0
    assert len(WeekResult.model_validate_json(captured.out).recipes) == 28
    assert "week_done" in captured.err
    assert "{" not in captured.err


def test_progress_lines_name_the_slot_the_days_the_attempt_and_the_failing_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = model_for(
        writes={"breakfast": [changed("breakfast", without_protein, "mon")]},
        repairs={"breakfast": [repair_reply("breakfast", "mon")]},
    )

    code = run_cli(tmp_path, "week", str(rules_file(tmp_path)), model=model)

    progress = lines(capsys.readouterr().err)
    assert code == 0
    assert "slot_started breakfast days=mon,tue,wed,thu,fri,sat,sun attempt=1" in progress
    assert "slot_written breakfast days=mon,tue,wed,thu,fri,sat,sun attempt=1" in progress
    assert "repair_started breakfast days=mon attempt=2" in progress
    failing = [line for line in progress if line.startswith("recipe_checked mon breakfast")]
    assert failing[0].startswith("recipe_checked mon breakfast attempt=1 failures=")
    assert failing[0] != "recipe_checked mon breakfast attempt=1 failures=none"
    assert failing[-1] == "recipe_checked mon breakfast attempt=2 failures=none"


def test_the_last_line_names_the_checks_each_day_fails_and_those_it_leaves_unsettled() -> None:
    def day(key: DayKey, failures: list[str], unsettled: list[str]) -> DayReport:
        return DayReport(
            day=key,
            totals={},
            failures=[Failure(check=c, message="no value for curry leaves") for c in failures],
            unsettled=[Failure(check=c, message="no value for rohu") for c in unsettled],
            soft={},
        )

    week = WeekResult(
        recipes=[],
        days=[
            day("mon", ["day_kcal", "day_fibre_g"], []),
            day("tue", [], []),
            day("wed", [], ["day_sat_fat_g"]),
        ],
        no_waste={},
        unwritten=[],
        calls=5,
        input_tokens=0,
        output_tokens=0,
        cost_usd=None,
    )

    assert progress_line(WeekDone(week=week)) == (
        "week_done recipes=0 unwritten=0 calls=5 day_failures=mon:day_kcal,mon:day_fibre_g "
        "day_unsettled=wed:day_sat_fat_g"
    )


def test_a_slot_that_could_not_be_written_is_reported_and_the_week_still_comes_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(
        tmp_path,
        "week",
        str(rules_file(tmp_path)),
        model=model_for(errors={"lunch": "rate_limited"}),
    )

    captured = capsys.readouterr()
    week = WeekResult.model_validate_json(captured.out)
    assert code == 0
    assert len(week.unwritten) == 7
    assert "slot_failed lunch days=mon,tue,wed,thu,fri,sat,sun attempt=1 final=yes" in captured.err
    assert "unwritten=7" in captured.err


def test_the_plans_words_never_reach_the_terminal_except_in_the_week_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "marked.json"
    path.write_text(json.dumps(marked_plan()), encoding="utf-8")
    out = tmp_path / "week.json"

    code = run_cli(tmp_path, "week", str(path), "--out", str(out), model=renamed_replies())

    captured = capsys.readouterr()
    assert code == 0
    assert MARK in out.read_text(encoding="utf-8")
    assert MARK not in captured.err
    assert MARK not in captured.out


# Reading a meal


def meal_model(*replies: str) -> FakeModel:
    return FakeModel({"read_meal": list(replies), "choose_food": []})


def read_cli(folder: Path, text: str, model: Any, nutrition: Any = None, **settings: Any) -> int:
    return run_cli(
        folder,
        "read",
        text,
        model=model,
        nutrition=nutrition or ReaderNutrition(),
        **({"catalogue_path": write_reader_catalogue(folder)} | settings),
    )


def test_read_prints_the_result_as_json_and_nothing_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = meal_model(reading(said("roti", 2, "roti", "2"), said("pulao", None, None, "some")))

    code = read_cli(tmp_path, "2 rotis and some pulao", model)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    result = ReadResult.model_validate_json(captured.out)
    assert [(item.name, item.status) for item in result.items] == [
        ("roti", "matched"),
        ("pulao", "missing"),
    ]
    assert result.missing == ["pulao"]
    assert result.total is not None
    assert result.total.total.kcal == 240


@pytest.mark.parametrize(
    ("text", "said_of"),
    [
        pytest.param("", "blank", id="empty"),
        pytest.param("  \n", "blank", id="spaces"),
        pytest.param("x" * 501, "501", id="long"),
    ],
)
def test_text_that_cannot_be_used_exits_2_naming_the_field_and_asks_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, said_of: str
) -> None:
    model = meal_model(reading())

    code = read_cli(tmp_path, text, model)

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err.startswith("meal-planner: read: text: ")
    assert said_of in captured.err
    assert "xxxx" not in captured.err
    assert model.calls == []


def test_the_text_limit_is_the_setting_here_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert read_cli(tmp_path, "a" * 11, meal_model(reading()), max_meal_text_chars=10) == 2
    assert "10" in capsys.readouterr().err
    assert read_cli(tmp_path, "a" * 10, meal_model(reading()), max_meal_text_chars=10) == 0


def test_a_model_that_cannot_be_reached_exits_1_naming_the_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Down:
        async def structured(self, **_: Any) -> Any:
            return ModelReply(value=None, raw="", error=RATE_LIMITED)

    code = read_cli(tmp_path, f"2 rotis {MARK}", Down())

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert lines(captured.err) == ["meal-planner: the model could not be reached (step: read_meal)"]


def test_a_read_that_breaks_says_only_what_kind_of_failure_it_was(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Breaking(ReaderNutrition):
        async def search(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(f"stand-in echoed the request: {MARK}")

    model = meal_model(reading(said("roti", 2, "roti", "2")))

    code = read_cli(tmp_path, f"2 rotis {MARK}", model, Breaking())

    captured = capsys.readouterr()
    assert code == 1
    assert MARK not in captured.err + captured.out
    assert "RuntimeError" in captured.err
    assert "Traceback" not in captured.err


def test_a_failed_start_exits_1_with_one_line_for_a_read_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    code = read_cli(tmp_path, "2 rotis", meal_model(reading()))

    err = lines(capsys.readouterr().err)
    assert code == 1
    assert len(err) == 1
    assert "LANGSMITH_TRACING" in err[0]


def test_ctrl_c_while_reading_exits_with_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(coroutine: Any) -> None:
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("meal_planner.cli.asyncio.run", interrupted)

    assert read_cli(tmp_path, "2 rotis", meal_model(reading())) == 130


def test_the_typed_text_never_reaches_the_terminal_except_in_the_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = meal_model(reading(said("roti", 2, "roti", MARK), said(f"food {MARK}", 1, "g", MARK)))

    code = read_cli(tmp_path, f"2 rotis and food {MARK}", model)

    captured = capsys.readouterr()
    assert code == 0
    assert MARK in captured.out
    assert MARK not in captured.err


# Adding up a log


MON, TUE = "2026-10-26", "2026-10-27"


def eaten(food_id: str = "oats", amount: float = 100, unit: str = "g") -> dict[str, Any]:
    return {"food_id": food_id, "amount": amount, "unit": unit}


def log_entry(
    date: str, slot_id: str, status: str = "as_planned", *items: dict[str, Any]
) -> dict[str, Any]:
    foods = [] if status == "skipped" else list(items) or [eaten()]
    return {"date": date, "slot_id": slot_id, "status": status, "items": foods}


def log_file(folder: Path, *entries: dict[str, Any]) -> Path:
    path = folder / "log.json"
    path.write_text(json.dumps({"version": 1, "entries": list(entries)}), encoding="utf-8")
    return path


def summary_cli(folder: Path, log: Path, rules: Path | None = None, **kwargs: Any) -> int:
    return run_cli(folder, "summary", str(rules or rules_file(folder)), str(log), **kwargs)


def test_summary_prints_the_week_summary_as_json_and_nothing_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = log_file(
        tmp_path,
        log_entry(MON, "breakfast", "as_planned", eaten("oats", 100), eaten("paneer", 100)),
        log_entry(MON, "lunch", "skipped"),
        log_entry(TUE, "dinner", "swapped", eaten("white_rice", 200)),
    )
    model = model_for()

    code = summary_cli(tmp_path, log, model=model)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    summary = WeekSummary.model_validate_json(captured.out)
    assert (summary.planned_meals, summary.as_planned_share) == (8, 1 / 8)
    assert [(day.day_type, day.totals["kcal"]) for day in summary.days] == [
        ("training", 400),
        ("rest", 400),
    ]
    assert model.calls == []


def test_summary_reads_a_unit_the_reader_reads_through_a_catalogue_measure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = log_file(tmp_path, log_entry(MON, "breakfast", "as_planned", eaten("egg", 2, "piece")))

    code = summary_cli(
        tmp_path,
        log,
        nutrition=ReaderNutrition(),
        catalogue_path=write_reader_catalogue(tmp_path),
    )

    assert code == 0
    (day,) = WeekSummary.model_validate_json(capsys.readouterr().out).days
    assert day.totals["kcal"] == 143


def test_a_log_nutrition_mcp_refuses_exits_2_naming_each_entry_as_the_api_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = log_file(
        tmp_path,
        log_entry(MON, "breakfast"),
        log_entry(TUE, "lunch", "as_planned", eaten(), eaten("ghost", 50)),
        log_entry(TUE, "dinner", "as_planned", eaten(unit="bowl")),
    )

    code = summary_cli(tmp_path, log)

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    ghost, bowl = lines(captured.err)
    assert ghost.startswith(
        f"meal-planner: {log}: entries.1.items.1: "
        "2026-10-27 lunch: item 2 (ghost): unknown food id 'ghost'"
    )
    assert bowl.startswith(
        f"meal-planner: {log}: entries.2.items.0: "
        "2026-10-27 dinner: item 1 (oats): unknown unit 'bowl'"
    )


def test_a_slot_the_plan_does_not_have_exits_2_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = log_file(tmp_path, log_entry(MON, "breakfast"), log_entry(TUE, "brunch"))

    code = summary_cli(tmp_path, log)

    assert code == 2
    assert lines(capsys.readouterr().err) == [
        f"meal-planner: {log}: entries.1.slot_id: 2026-10-27 brunch: the plan has no slot "
        "'brunch'; its slots are breakfast, lunch, snack, dinner"
    ]


@pytest.mark.parametrize(
    ("entries", "said_of"),
    [
        pytest.param(
            [log_entry(MON, "lunch"), log_entry("2026-11-02", "lunch")],
            "entries: Value error, entries: the dates run from 2026-10-26 to 2026-11-02",
            id="eight-days",
        ),
        pytest.param(
            [log_entry(MON, "lunch") | {"status": "ate_it"}], "entries.0.status: ", id="status"
        ),
        pytest.param(
            [log_entry(MON, "lunch", "skipped") | {"status": "swapped"}],
            "entries.0: Value error, 2026-10-26 lunch: a meal with status 'swapped'",
            id="no-foods",
        ),
    ],
)
def test_a_log_that_does_not_validate_exits_2_naming_the_field_and_starts_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    entries: list[dict[str, Any]],
    said_of: str,
) -> None:
    nutrition = SpyNutrition(*EXTRA_FOODS)

    code = summary_cli(tmp_path, log_file(tmp_path, *entries), nutrition=nutrition)

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert f"{tmp_path / 'log.json'}: {said_of}" in captured.err
    assert nutrition.meals == []


@pytest.mark.parametrize(("content", "said_of"), [("{not json", "JSON"), (None, "log.json")])
def test_a_log_file_that_is_not_json_or_not_there_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None, said_of: str
) -> None:
    path = tmp_path / "log.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")

    code = summary_cli(tmp_path, path)

    assert code == 2
    assert said_of in capsys.readouterr().err


def test_invalid_rules_for_a_summary_exit_2_naming_the_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["slots"][1]["kcal_share"] = 7
    rules = tmp_path / "bad.json"
    rules.write_text(json.dumps(plan), encoding="utf-8")

    code = summary_cli(tmp_path, log_file(tmp_path, log_entry(MON, "lunch")), rules)

    assert code == 2
    assert "slots.1.kcal_share" in capsys.readouterr().err


class MealDown(SpyNutrition):
    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError(f"nutrition-mcp could not answer {MARK}", unreachable=True)


def test_nutrition_mcp_going_away_exits_1_naming_the_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = log_file(tmp_path, log_entry(MON, "lunch"))

    code = summary_cli(tmp_path, log, nutrition=MealDown(*EXTRA_FOODS))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert lines(captured.err) == [
        "meal-planner: nutrition-mcp could not be reached (step: nutrition)"
    ]


def test_a_summary_that_breaks_says_only_what_kind_of_failure_it_was(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = log_file(tmp_path, log_entry(MON, "lunch"))

    code = summary_cli(tmp_path, log, nutrition=ExplodingNutrition(*EXTRA_FOODS))

    captured = capsys.readouterr()
    assert code == 1
    assert MARK not in captured.err + captured.out
    assert "RuntimeError" in captured.err


MODEL_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def without_model_keys(monkeypatch: pytest.MonkeyPatch, folder: Path) -> Settings:
    """No model key in the environment, and settings for the default model."""
    for name in MODEL_KEYS:
        monkeypatch.delenv(name, raising=False)
    return Settings(catalogue_path=write_catalogue_file(folder, *EXTRA_FOODS))


def test_summary_needs_no_model_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # It makes no model call, so it starts with no model given and no key set.
    settings = without_model_keys(monkeypatch, tmp_path)
    log = log_file(tmp_path, log_entry(MON, "lunch"))

    code = main(
        ["summary", str(rules_file(tmp_path)), str(log)],
        settings=settings,
        nutrition=SpyNutrition(*EXTRA_FOODS),
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert WeekSummary.model_validate_json(captured.out).planned_meals == 4


def test_a_summary_is_not_traced_even_with_the_langfuse_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # It makes no model call, so there is nothing to trace: no client is made and none is logged.
    made: list[dict[str, Any]] = []

    def client(**values: Any) -> None:
        made.append(values)
        raise RuntimeError("no Langfuse client in this test")

    monkeypatch.setattr(tracing, "Langfuse", client)
    log = log_file(tmp_path, log_entry(MON, "lunch"))

    code = summary_cli(
        tmp_path, log, langfuse_public_key="pk-lf-cli", langfuse_secret_key="sk-lf-cli"
    )

    captured = capsys.readouterr()
    assert code == 0
    assert made == []
    assert captured.err == ""
    assert WeekSummary.model_validate_json(captured.out).planned_meals == 4


@pytest.mark.parametrize("command", ["week", "read"])
def test_the_commands_that_call_a_model_still_need_its_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    settings = without_model_keys(monkeypatch, tmp_path)
    argument = str(rules_file(tmp_path)) if command == "week" else "2 rotis"

    code = main([command, argument], settings=settings, nutrition=SpyNutrition(*EXTRA_FOODS))

    (line,) = lines(capsys.readouterr().err)
    assert code == 1
    assert "GEMINI_API_KEY" in line


def test_ctrl_c_while_adding_up_exits_with_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(coroutine: Any) -> None:
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("meal_planner.cli.asyncio.run", interrupted)

    assert summary_cli(tmp_path, log_file(tmp_path, log_entry(MON, "lunch"))) == 130


def test_the_rules_and_the_log_never_reach_a_log_line(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rules = tmp_path / "marked.json"
    rules.write_text(json.dumps(marked_plan()), encoding="utf-8")
    good = log_file(tmp_path, log_entry(MON, "breakfast"), log_entry(MON, "lunch", "skipped"))

    assert summary_cli(tmp_path, good, rules) == 0
    worked_out = capsys.readouterr()
    # The summary holds no word of the plan.
    assert MARK not in worked_out.out + worked_out.err

    # A value that does not validate is not echoed. An id nutrition-mcp refuses is named to the
    # person who ran the command, as the API's 422 names it, and is in no log line.
    bad_date = log_file(tmp_path, log_entry(MON, "lunch") | {"date": MARK})
    assert summary_cli(tmp_path, bad_date, rules) == 2
    assert MARK not in capsys.readouterr().err
    ghost = log_file(tmp_path, log_entry(MON, "lunch", "as_planned", eaten(f"ghost_{MARK}")))
    assert summary_cli(tmp_path, ghost, rules) == 2
    assert MARK in capsys.readouterr().err

    assert records
    for record in records:
        assert MARK not in record.getMessage()
        assert MARK not in json.dumps(vars(record), default=str), record.name


# Input that cannot be used


def test_invalid_rules_exit_non_zero_naming_the_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["slots"][1]["kcal_share"] = 7
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    model = model_for()

    code = run_cli(tmp_path, "week", str(path), model=model)

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "slots.1.kcal_share" in captured.err
    assert model.calls == []


def test_a_rejected_value_is_not_echoed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["slots"][0]["max_active_min"] = f"about {MARK}"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    code = run_cli(tmp_path, "week", str(path))

    assert code == 2
    assert MARK not in capsys.readouterr().err


def test_a_file_that_is_not_json_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "rules.json"
    path.write_text("{not json", encoding="utf-8")

    code = run_cli(tmp_path, "week", str(path))

    assert code == 2
    assert "JSON" in capsys.readouterr().err


def test_a_file_that_is_not_there_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(tmp_path, "week", str(tmp_path / "nope.json"))

    assert code == 2
    assert "nope.json" in capsys.readouterr().err


def test_a_food_the_catalogue_lacks_is_named_by_its_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["food"]["liked_foods"] = ["unobtainium"]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    model = model_for()

    code = run_cli(tmp_path, "week", str(path), model=model)

    err = capsys.readouterr().err
    assert code == 2
    assert "food.liked_foods" in err
    assert "unobtainium" in err
    assert model.calls == []


def test_a_command_the_cli_lacks_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["month", "rules.json"])

    assert raised.value.code == 2


# Starting and running


def test_a_failed_start_exits_non_zero_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    code = run_cli(tmp_path, "week", str(rules_file(tmp_path)))

    err = lines(capsys.readouterr().err)
    assert code == 1
    assert len(err) == 1
    assert err[0].startswith("meal-planner: ")
    assert "LANGSMITH_TRACING" in err[0]


def test_a_run_that_fails_says_only_what_kind_of_failure_it_was(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    nutrition = ExplodingNutrition(*EXTRA_FOODS)

    code = run_cli(tmp_path, "week", str(rules_file(tmp_path)), nutrition=nutrition)

    captured = capsys.readouterr()
    assert code == 1
    assert MARK not in captured.err + captured.out
    assert "RuntimeError" in captured.err
    assert "Traceback" not in captured.err


def test_ctrl_c_exits_with_130(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(coroutine: Any) -> None:
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("meal_planner.cli.asyncio.run", interrupted)

    assert run_cli(tmp_path, "week", str(rules_file(tmp_path))) == 130


def test_a_week_that_cannot_be_written_to_the_out_path_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "no-such-folder" / "week.json"

    code = run_cli(tmp_path, "week", str(rules_file(tmp_path)), "--out", str(out))

    assert code == 1
    assert "week.json" in capsys.readouterr().err


# The installed command


def command() -> str:
    return str(Path(sys.executable).parent / "meal-planner")


def test_the_installed_command_exits_with_the_code_main_returns(tmp_path: Path) -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["slots"][1]["kcal_share"] = 7
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    done = subprocess.run([command(), "week", str(path)], capture_output=True, text=True)

    assert done.returncode == 2
    assert "slots.1.kcal_share" in done.stderr


def test_the_installed_command_has_help() -> None:
    done = subprocess.run([command(), "week", "--help"], capture_output=True, text=True)

    assert done.returncode == 0
    assert "--out" in done.stdout


def test_the_installed_command_has_help_for_a_summary() -> None:
    done = subprocess.run([command(), "summary", "--help"], capture_output=True, text=True)

    assert done.returncode == 0
    assert "log" in done.stdout
    assert "rules" in done.stdout


def test_the_installed_command_has_help_for_reading() -> None:
    done = subprocess.run([command(), "read", "--help"], capture_output=True, text=True)

    assert done.returncode == 0
    assert "text" in done.stdout
