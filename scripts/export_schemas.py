"""Write the JSON Schemas the frontend is built from to schemas/.

Run `uv run python scripts/export_schemas.py` after changing src/meal_planner/rules.py,
summary.py, week_events.py, api.py or the models they use, and commit the result. CI regenerates
them and fails on a difference. The frontend generates its types from every file here
(`npm run types` in frontend/), and CI fails when those are out of date too.

- `plan-rules.json`: the plan rules a person enters, and the body of `POST /v1/week`.
- `export.json`: the file a person saves and loads again, with their rules, week and meal log.
  The browser checks a file against it before it loads it.
- `api/`: the other bodies the API takes and returns, one file each, so that the frontend's
  client is typed from the server's models.
"""

import json
from pathlib import Path
from typing import Any

from nutrition_mcp.schemas import MealResult
from pydantic import TypeAdapter

from meal_planner.api import (
    CatalogueRow,
    ComputeRequest,
    ReadRequest,
    ReplaceRequest,
    SummaryRequest,
)
from meal_planner.foods import FoodOption
from meal_planner.problems import Problem
from meal_planner.reader import ReadResult
from meal_planner.rules import PlanRules
from meal_planner.summary import Export, WeekSummary
from meal_planner.week_events import StreamEvent

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
DIALECT = "https://json-schema.org/draft/2020-12/schema"

# File name (without .json) to the type it describes, and the title for a type that has none of
# its own (a list or a union). The frontend names each generated type by its schema's title.
SCHEMAS: dict[str, tuple[Any, str | None]] = {
    "plan-rules": (PlanRules, None),
    "export": (Export, None),
    "api/replace-request": (ReplaceRequest, None),
    "api/stream-event": (StreamEvent, "StreamEvent"),
    "api/read-request": (ReadRequest, None),
    "api/read-result": (ReadResult, None),
    "api/summary-request": (SummaryRequest, None),
    "api/week-summary": (WeekSummary, None),
    "api/food-search": (list[FoodOption], "FoodSearchResult"),
    "api/compute-request": (ComputeRequest, None),
    "api/meal-result": (MealResult, None),
    "api/catalogue": (list[CatalogueRow], "Catalogue"),
    # One problem of a 422: where it is and what is wrong.
    "api/problem": (Problem, None),
}


def schema_texts() -> dict[str, str]:
    """The text of each schema file, by its path under schemas/."""
    texts: dict[str, str] = {}
    for name, (kind, title) in SCHEMAS.items():
        schema = TypeAdapter(kind).json_schema()
        head: dict[str, Any] = {"$schema": DIALECT}
        if title is not None:
            head["title"] = title
        texts[f"{name}.json"] = json.dumps({**head, **schema}, indent=2, ensure_ascii=False) + "\n"
    return texts


def main() -> None:
    for file_name, text in schema_texts().items():
        path = SCHEMAS_DIR / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote schemas/{file_name}")


if __name__ == "__main__":
    main()
