"""Writes frontend/src/test/recorded-week.json: a plan from the eval datasets with the week the API
streams for it, as the browser saves them.

The model's replies come from the smoke recording, so there is no key and no model call;
nutrition-mcp runs for real, so every number is its own. The accessibility check
(frontend/scripts/a11y.ts) opens the plan, week and recipe screens on this state. Run it again
when the week's format changes:

    uv run python scripts/record_week_fixture.py
"""

import json
import os
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from meal_planner.api import create_app
from meal_planner.config import Settings
from meal_planner.llm import ReplayModel

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "evals" / "datasets" / "plans" / "kerala_egg_no_dairy.json"
RECORDING = REPO / "evals" / "recordings" / "smoke.jsonl"
OUT = REPO / "frontend" / "src" / "test" / "recorded-week.json"


def recorded_week(rules: dict[str, Any]) -> dict[str, Any]:
    """The week from the stream's last event."""
    app = create_app(Settings(), model=ReplayModel(RECORDING))
    with TestClient(app) as client, client.stream("POST", "/v1/week", json=rules) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data:"):
                event: dict[str, Any] = json.loads(line.removeprefix("data:"))
                if event["kind"] == "week_done":
                    week: dict[str, Any] = event["week"]
                    return week
    raise SystemExit("the stream ended without the week")


def main() -> None:
    # A plan from the datasets is not personal, but nothing here is to be traced either.
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        os.environ.pop(name, None)
    rules = json.loads(PLAN.read_text(encoding="utf-8"))
    state = {
        "version": 1,
        "rules": rules,
        "week": recorded_week(rules),
        "log": {"version": 1, "entries": []},
    }
    OUT.write_text(json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
