"""The committed JSON Schemas are the ones the models give, so the frontend checks against what
the server accepts. CI runs the script and fails on a difference too; this says so sooner."""

import json

import pytest
from export_schemas import SCHEMAS_DIR, schema_texts

from meal_planner.summary import Export
from meal_planner.week_events import StreamError

# The export file the frontend's tests load, written by the API with the test fakes.
FRONTEND_SAMPLE = SCHEMAS_DIR.parent / "frontend" / "src" / "test" / "export-sample.json"

API_SCHEMAS = [
    "api/catalogue.json",
    "api/compute-request.json",
    "api/food-search.json",
    "api/meal-result.json",
    "api/problem.json",
    "api/read-request.json",
    "api/read-result.json",
    "api/replace-request.json",
    "api/stream-event.json",
    "api/summary-request.json",
    "api/week-summary.json",
]


@pytest.mark.parametrize("file_name", sorted(schema_texts()))
def test_the_committed_schema_is_the_models_schema(file_name: str) -> None:
    committed = (SCHEMAS_DIR / file_name).read_text(encoding="utf-8")

    assert committed == schema_texts()[file_name], "run: uv run python scripts/export_schemas.py"


def test_every_schema_is_written_to_schemas() -> None:
    assert sorted(schema_texts()) == sorted(["export.json", "plan-rules.json", *API_SCHEMAS])
    written = sorted(p.relative_to(SCHEMAS_DIR).as_posix() for p in SCHEMAS_DIR.rglob("*.json"))
    assert written == sorted(schema_texts())


def test_the_export_schema_describes_a_file() -> None:
    schema = json.loads(schema_texts()["export.json"])

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"version", "rules", "week", "log"}
    assert schema["properties"]["version"]["const"] == 1
    assert {"PlanRules", "MealLog", "LogEntry", "LogItem", "WeekResult"} <= set(schema["$defs"])


def test_the_frontend_sample_export_is_a_file_the_server_accepts() -> None:
    """The browser's storage tests start from this file and change one thing at a time, so it
    has to be a file the server accepts as it is."""
    Export.model_validate_json(FRONTEND_SAMPLE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("file_name", API_SCHEMAS)
def test_every_api_schema_has_a_dialect_and_a_title(file_name: str) -> None:
    """The frontend names each generated type by its schema's title, so none may be missing."""
    schema = json.loads(schema_texts()[file_name])

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"]


def test_a_title_names_one_schema() -> None:
    """Two files with one title would give the frontend two types with one name."""
    titles = [json.loads(text)["title"] for text in schema_texts().values()]

    assert len(titles) == len(set(titles))


def test_the_stream_schema_has_every_event_the_api_sends() -> None:
    schema = json.loads(schema_texts()["api/stream-event.json"])
    kinds = {
        schema["$defs"][ref["$ref"].removeprefix("#/$defs/")]["properties"]["kind"]["const"]
        for ref in schema["oneOf"]
    }

    assert kinds == {
        "slot_started",
        "slot_written",
        "recipe_checked",
        "repair_started",
        "slot_failed",
        "week_done",
        "error",
    }


def test_a_list_route_is_described_as_a_list() -> None:
    for file_name, item in [
        ("api/catalogue.json", "CatalogueRow"),
        ("api/food-search.json", "FoodOption"),
    ]:
        schema = json.loads(schema_texts()[file_name])
        assert schema["type"] == "array"
        assert schema["items"] == {"$ref": f"#/$defs/{item}"}


def test_the_stream_error_names_the_step_and_nothing_else() -> None:
    error = StreamError(step="replace", message="the run stopped before it finished")

    assert json.loads(error.model_dump_json()) == {
        "kind": "error",
        "step": "replace",
        "message": "the run stopped before it finished",
    }
