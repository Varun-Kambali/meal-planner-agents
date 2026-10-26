"""What the API and command-line tests share: a marked plan, a nutrition-mcp that breaks, and the
checks that nothing the user typed reaches a log.

A marker is a string that cannot occur by chance. A test puts it into what the user sends and then
looks for it in every place it must not appear.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from nutrition_mcp.schemas import MealItemIn, MealResult
from week_helpers import (
    PLAN,
    SLOTS,
    SlotModel,
    SpyNutrition,
    good,
    model_for,
    write_catalogue_file,
)

from meal_planner.api import create_app
from meal_planner.config import Settings
from meal_planner.llm import ModelPort
from meal_planner.nutrition import NutritionPort

MARK = "ZXQ-MARKER-7731"


# The fixture plan excludes brinjal, and the app refuses a plan that names a food it lacks.
EXTRA_FOODS = ("brinjal",)


def settings_for(folder: Path, **changes: Any) -> Settings:
    # The limit is high so that a test that is not about it does not trip over it.
    values: dict[str, Any] = {
        "catalogue_path": write_catalogue_file(folder, *EXTRA_FOODS),
        "rate_limit_per_minute": 1000,
    }
    return Settings(**(values | changes))


def make_app(
    folder: Path,
    *,
    model: ModelPort | None = None,
    nutrition: NutritionPort | None = None,
    **changes: Any,
) -> FastAPI:
    return create_app(
        settings_for(folder, **changes),
        model=model or model_for(),
        nutrition=nutrition or SpyNutrition(*EXTRA_FOODS),
    )


@contextmanager
def serving(folder: Path, **kwargs: Any) -> Iterator[TestClient]:
    with TestClient(make_app(folder, **kwargs)) as client:
        yield client


class ExplodingNutrition(SpyNutrition):
    """A nutrition-mcp stand-in that breaks its contract part way through a run: after `after`
    meals it raises a plain exception, with text the user must never see in a log.

    A model that raises no longer ends a run (the slot is refused instead), so this is how a
    test reaches the path where the run itself fails after the stream has started.
    """

    def __init__(self, *extra_ids: str, after: int = 0) -> None:
        super().__init__(*extra_ids)
        self.after = after

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        if len(self.meals) >= self.after:
            raise RuntimeError(f"stand-in echoed the request: {MARK}")
        return await super().meal(items)


def plan_json() -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(PLAN.read_text(encoding="utf-8"))
    return plan


def marked_plan() -> dict[str, Any]:
    """The fixture plan with the marker in its name, its first slot and its cuisines."""
    plan = plan_json()
    plan["name"] = f"Plan {MARK}"
    plan["slots"][0] = plan["slots"][0] | {"style": f"style {MARK}", "name": f"Breakfast {MARK}"}
    plan["food"]["cuisines"] = [f"South {MARK}", f"Japan {MARK}", f"Med {MARK}"]
    plan["day_types"][0] = plan["day_types"][0] | {"name": f"Day {MARK}"}
    return plan


def renamed(text: str) -> str:
    """Canned model text with the cuisines the canned replies name replaced by the marked ones."""
    names = {
        "South Indian": f"South {MARK}",
        "Japanese": f"Japan {MARK}",
        "Mediterranean": f"Med {MARK}",
    }
    for old, new in names.items():
        text = text.replace(old, new)
    return text


def renamed_replies() -> SlotModel:
    """Model replies for the marked plan, so that every recipe is for a cuisine of the plan."""
    return SlotModel({s: {"write_slot": [renamed(good(s))]} for s in SLOTS})


def log_entries(captured: str) -> list[dict[str, Any]]:
    """The JSON log lines in what a test captured from stderr."""
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


def assert_no_marker(records: list[logging.LogRecord], captured: Any, mark: str = MARK) -> None:
    """`captured` is what capfd.readouterr() returned: the process's own stdout and stderr."""
    assert mark not in captured.err
    assert mark not in captured.out
    for record in records:
        assert mark not in record.getMessage()
        assert mark not in json.dumps(vars(record), default=str), record.name
