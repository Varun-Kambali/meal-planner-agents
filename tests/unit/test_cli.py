"""The meal-planner command: arguments, progress on stderr, the week as JSON, and its exit codes."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from app_helpers import MARK, ExplodingNutrition, marked_plan, renamed_replies
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

from meal_planner.cli import main
from meal_planner.config import Settings
from meal_planner.llm import RATE_LIMITED, FakeModel, ModelReply
from meal_planner.reader import ReadResult
from meal_planner.week_events import WeekResult

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


def test_the_installed_command_has_help_for_reading() -> None:
    done = subprocess.run([command(), "read", "--help"], capture_output=True, text=True)

    assert done.returncode == 0
    assert "text" in done.stdout
