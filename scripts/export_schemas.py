"""Write the JSON Schema for the plan rules to schemas/.

Run `uv run python scripts/export_schemas.py` after changing src/meal_planner/rules.py and
commit the result. The frontend's types are generated from it.
"""

import json
from pathlib import Path

from meal_planner.rules import PlanRules

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
DIALECT = "https://json-schema.org/draft/2020-12/schema"


def main() -> None:
    SCHEMAS_DIR.mkdir(exist_ok=True)
    schema = {"$schema": DIALECT, **PlanRules.model_json_schema()}
    text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
    (SCHEMAS_DIR / "plan-rules.json").write_text(text, encoding="utf-8")
    print("wrote schemas/plan-rules.json")


if __name__ == "__main__":
    main()
