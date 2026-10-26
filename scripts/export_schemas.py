"""Write the JSON Schemas the frontend is built from to schemas/.

Run `uv run python scripts/export_schemas.py` after changing src/meal_planner/rules.py,
summary.py, week_events.py or the models they use, and commit the result. CI regenerates them
and fails on a difference.

- `plan-rules.json`: the plan rules a person enters. The frontend's types are generated from it.
- `export.json`: the file a person saves and loads again, with their rules, week and meal log.
  The browser checks a file against it before it loads it.
"""

import json
from pathlib import Path

from pydantic import BaseModel

from meal_planner.rules import PlanRules
from meal_planner.summary import Export

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMAS: dict[str, type[BaseModel]] = {"plan-rules": PlanRules, "export": Export}


def schema_texts() -> dict[str, str]:
    """The text of each schema file, by file name."""
    return {
        f"{name}.json": json.dumps(
            {"$schema": DIALECT, **model.model_json_schema()}, indent=2, ensure_ascii=False
        )
        + "\n"
        for name, model in SCHEMAS.items()
    }


def main() -> None:
    SCHEMAS_DIR.mkdir(exist_ok=True)
    for file_name, text in schema_texts().items():
        (SCHEMAS_DIR / file_name).write_text(text, encoding="utf-8")
        print(f"wrote schemas/{file_name}")


if __name__ == "__main__":
    main()
