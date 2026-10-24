"""The meal-planner command: arguments, progress on stderr, the week as JSON, and its exit codes."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from week_helpers import (
    PLAN,
    SLOTS,
    SlotModel,
    SpyNutrition,
    changed,
    good,
    model_for,
    repair_reply,
    without_protein,
    write_catalogue_file,
)

from meal_planner.cli import main
from meal_planner.config import Settings
from meal_planner.llm import ModelReply
from meal_planner.week_events import WeekResult

MARK = "ZXQ-MARKER-5512"
EXTRA_FOODS = ("brinjal",)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [n for n in os.environ if n.startswith("MEAL_PLANNER_")]:
        monkeypatch.delenv(name)


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
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["name"] = f"Plan {MARK}"
    plan["slots"][0] = plan["slots"][0] | {"style": f"style {MARK}", "name": f"Breakfast {MARK}"}
    plan["food"]["cuisines"] = [f"South {MARK}", f"Japan {MARK}", f"Med {MARK}"]
    names = {
        "South Indian": f"South {MARK}",
        "Japanese": f"Japan {MARK}",
        "Mediterranean": f"Med {MARK}",
    }

    def renamed(text: str) -> str:
        for old, new in names.items():
            text = text.replace(old, new)
        return text

    model = SlotModel({s: {"write_slot": [renamed(good(s))]} for s in SLOTS})
    path = tmp_path / "marked.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "week.json"

    code = run_cli(tmp_path, "week", str(path), "--out", str(out), model=model)

    captured = capsys.readouterr()
    assert code == 0
    assert MARK in out.read_text(encoding="utf-8")
    assert MARK not in captured.err
    assert MARK not in captured.out


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


class ExplodingModel:
    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        raise RuntimeError(f"provider echoed the request: {MARK}")


def test_a_run_that_fails_says_only_what_kind_of_failure_it_was(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(tmp_path, "week", str(rules_file(tmp_path)), model=ExplodingModel())

    captured = capsys.readouterr()
    assert code == 1
    assert MARK not in captured.err + captured.out
    assert "RuntimeError" in captured.err
    assert "Traceback" not in captured.err


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
