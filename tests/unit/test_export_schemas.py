"""The committed JSON Schemas are the ones the models give, so the frontend checks against what
the server accepts. CI runs the script and fails on a difference too; this says so sooner."""

import json

import pytest
from export_schemas import SCHEMAS_DIR, schema_texts


@pytest.mark.parametrize("file_name", ["plan-rules.json", "export.json"])
def test_the_committed_schema_is_the_models_schema(file_name: str) -> None:
    committed = (SCHEMAS_DIR / file_name).read_text(encoding="utf-8")

    assert committed == schema_texts()[file_name], "run: uv run python scripts/export_schemas.py"


def test_every_schema_is_written_to_schemas() -> None:
    assert sorted(schema_texts()) == ["export.json", "plan-rules.json"]
    assert sorted(p.name for p in SCHEMAS_DIR.glob("*.json")) == sorted(schema_texts())


def test_the_export_schema_describes_a_file() -> None:
    schema = json.loads(schema_texts()["export.json"])

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"version", "rules", "week", "log"}
    assert schema["properties"]["version"]["const"] == 1
    assert {"PlanRules", "MealLog", "LogEntry", "LogItem", "WeekResult"} <= set(schema["$defs"])
