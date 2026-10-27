"""The eval's cases: the plans and typed meals in evals/datasets, and which of them a run takes.

A plan is a recipe case (written twice, by the pipeline and by the baseline) and a meal a reading
case. A run takes every case (a full run), the smoke slice, or the cases `--only` names or
`--limit` keeps (a partial run). Only full runs feed evals/results/summary.md.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dataset_version import DATASETS
from pydantic import BaseModel, ConfigDict

from meal_planner.rules import PlanRules

PLANS_DIR = DATASETS / "plans"
MEALS_FILE = DATASETS / "meals.jsonl"

Suite = Literal["recipes", "reading", "all"]
Slice = Literal["full", "smoke", "partial"]
Status = Literal["matched", "needs_amount", "unknown_unit"]

# The smoke slice CI replays from a recording. Three plans: a non-vegetarian one with fish and two
# day types, an egg one with two cuisines (so the cuisine-repeat check runs) and two day types
# that is one of the coconut-heavy plans watched for saturated fat, and one of the two plans that
# are meant to fail (vegan, no heat, soy and peanut excluded). They have 10 slots between them,
# which keeps the recording small. All three exclude an allergen.
SMOKE_PLANS = ("bengali_fish_home", "hard_vegan_jar_breakfast", "kerala_egg_no_dairy")
# Ten meals with every label status and a food the data lacks: a range (m01), units nothing has
# (m03, m04), also_accept (m04, m09), litres, millilitres and a scoop (m09, m10), the instruction
# inside the typed text (m28), vague amounts (m36), only foods the data lacks (m43), all of
# matched, needs_amount and missing in one meal (m47), and three of the expected misses (m01,
# m03, m52).
SMOKE_MEALS = ("m01", "m03", "m04", "m09", "m10", "m28", "m36", "m43", "m47", "m52")
# Meals whose labels name a food the reader cannot reach today (see evals/datasets/README.md,
# "How the labels were set"). The reading metrics are given for them as a group of their own as
# well as in the totals, so a reader can tell those misses from the rest.
EXPECTED_MISSES = ("m01", "m02", "m03", "m15", "m52")


class LabelItem(BaseModel):
    """One food a typed meal names that nutrition-mcp has, as evals/datasets/README.md says."""

    model_config = ConfigDict(extra="forbid")

    food_id: str
    amount: float | None
    unit: str | None
    grams: float | None
    status: Status
    also_accept: list[str] = []


class MealLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    expected: list[LabelItem]
    missing: list[str]
    note: str


def load_plans(folder: Path = PLANS_DIR) -> dict[str, PlanRules]:
    """Each plan by its id (its file name without .json), in id order."""
    return {
        path.stem: PlanRules.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(folder.glob("*.json"))
    }


def load_meals(path: Path = MEALS_FILE) -> dict[str, MealLabel]:
    """Each labelled meal by its id, in file order."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {row["id"]: MealLabel.model_validate(row) for row in rows}


@dataclass(frozen=True)
class Selection:
    plans: list[str]
    meals: list[str]
    slice: Slice


class SelectionError(ValueError):
    """The ids or flags asked for do not pick out a set of cases."""


def select(
    suite: Suite,
    plan_ids: list[str],
    meal_ids: list[str],
    *,
    smoke: bool = False,
    only: list[str] | None = None,
    limit: int | None = None,
) -> Selection:
    """The cases a run takes, in dataset order.

    `smoke` takes the smoke slice and `only` the ids it names (plan ids and meal ids together);
    they cannot be combined. `limit` then keeps the first N of each suite. The suite leaves out
    the other suite's cases. A run is "full" when it takes every case of its suites, "smoke" when
    it takes the smoke slice as defined, and "partial" otherwise.
    """
    if smoke and only is not None:
        raise SelectionError("--smoke and --only cannot be used together")
    if limit is not None and limit < 1:
        raise SelectionError("--limit must be at least 1")
    plans, meals = list(plan_ids), list(meal_ids)
    kind: Slice = "full"
    if smoke:
        plans = [p for p in plan_ids if p in SMOKE_PLANS]
        meals = [m for m in meal_ids if m in SMOKE_MEALS]
        absent = [i for i in (*SMOKE_PLANS, *SMOKE_MEALS) if i not in (*plans, *meals)]
        if absent:
            raise SelectionError(f"the smoke slice names cases the datasets lack: {absent}")
        kind = "smoke"
    elif only is not None:
        unknown = [i for i in only if i not in plan_ids and i not in meal_ids]
        if unknown:
            raise SelectionError(f"--only names no plan or meal: {', '.join(unknown)}")
        plans = [p for p in plan_ids if p in only]
        meals = [m for m in meal_ids if m in only]
        kind = "partial"
    if suite == "recipes":
        meals = []
    elif suite == "reading":
        plans = []
    if limit is not None and (len(plans) > limit or len(meals) > limit):
        plans, meals = plans[:limit], meals[:limit]
        kind = "partial"
    if not plans and not meals:
        raise SelectionError(f"no cases left to run for suite '{suite}'")
    return Selection(plans, meals, kind)
