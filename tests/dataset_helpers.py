"""Reading the eval datasets in evals/datasets, for the tests that check them."""

import json
from pathlib import Path
from typing import Any

from dataset_version import DATASETS

from meal_planner.rules import PlanRules

PLANS = DATASETS / "plans"
MEALS = DATASETS / "meals.jsonl"


def plan_paths() -> list[Path]:
    return sorted(PLANS.glob("*.json"))


def plans() -> list[tuple[str, PlanRules]]:
    """Each plan's id (its file name without .json) and its rules, in file name order."""
    return [
        (path.stem, PlanRules.model_validate_json(path.read_text(encoding="utf-8")))
        for path in plan_paths()
    ]


def meal_rows() -> list[dict[str, Any]]:
    """The rows of meals.jsonl, one JSON object a line."""
    lines = MEALS.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
