"""Writes frontend/src/test/recorded-tracking.json: a meal log over the recorded week, and the
API's replies the log screens ask for, for the accessibility check to stand in for the API.

The log is made from frontend/src/test/recorded-week.json (write that first with
scripts/record_week_fixture.py): meals as planned, in a different amount, swapped and skipped,
on three dates a week apart at most. The typed meal is one from the smoke slice, so the reader's
model replies come from the smoke recording, with no key and no model call; nutrition-mcp runs for
real, so every number is its own. Run it again when the log's or the summary's format changes:

    uv run python scripts/record_tracking_fixture.py
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
RECORDING = REPO / "evals" / "recordings" / "smoke.jsonl"
WEEK = REPO / "frontend" / "src" / "test" / "recorded-week.json"
OUT = REPO / "frontend" / "src" / "test" / "recorded-tracking.json"
# Meal m47 of the smoke slice: a food found, one with no amount, and one the data lacks.
TYPED = "3 chapatis, bhindi sabzi and some curd"
# The name the swap flow's search starts from for the food with no amount.
SEARCHED = "curd"


def planned(week: dict[str, Any], day: str, slot: str, factor: float = 1) -> list[dict[str, Any]]:
    """The recipe's ingredients at their grams times `factor`, as the log screen logs them."""
    recipe = next(
        r for r in week["recipes"] if r["spec"]["day"] == day and r["spec"]["slot"]["id"] == slot
    )
    if recipe["nutrition"] is None:
        raise SystemExit(f"{day} {slot} has no numbers, so the log screen would not log it")
    return [
        {
            "food_id": item["id"],
            "amount": item["grams"] if factor == 1 else round(item["grams"] * factor, 2),
            "unit": "g",
        }
        for item in recipe["draft"]["ingredients"]
    ]


def entry(date: str, slot: str, status: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"date": date, "slot_id": slot, "status": status, "items": items}


def checked(response: Any) -> Any:
    response.raise_for_status()
    return response.json()


def main() -> None:
    # Nothing here is personal, but nothing is to be traced either.
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        os.environ.pop(name, None)
    state = json.loads(WEEK.read_text(encoding="utf-8"))
    rules, week = state["rules"], state["week"]
    app = create_app(Settings(), model=ReplayModel(RECORDING))
    with TestClient(app) as client:
        read = checked(client.post("/v1/log/read", json={"text": TYPED}))
        found = [item for item in read["items"] if item["status"] == "matched"]
        eaten = [
            {"food_id": item["food_id"], "amount": item["amount"], "unit": item["unit"]}
            for item in found
        ]
        search = checked(client.get("/v1/foods/search", params={"q": SEARCHED}))
        swapped = [*eaten, {"food_id": search[0]["id"], "amount": 100, "unit": "g"}]
        compute = checked(client.post("/v1/log/compute", json={"items": swapped}))
        half_dinner = planned(week, "mon", "dinner", 0.5)
        log = {
            "version": 1,
            "entries": [
                entry("2026-11-02", "breakfast", "as_planned", planned(week, "mon", "breakfast")),
                entry("2026-11-02", "lunch", "swapped", swapped),
                entry("2026-11-02", "snack", "skipped", []),
                entry("2026-11-02", "dinner", "different_amount", half_dinner),
                entry("2026-11-03", "breakfast", "as_planned", planned(week, "tue", "breakfast")),
                entry("2026-11-03", "lunch", "skipped", []),
                entry("2026-11-08", "lunch", "as_planned", planned(week, "sun", "lunch")),
            ],
        }
        summary = checked(client.post("/v1/summary", json={"rules": rules, "log": log}))
    fixture = {
        "log": log,
        "read": {"text": TYPED, "reply": read},
        "search": {"q": SEARCHED, "reply": search},
        "compute": {"items": swapped, "reply": compute},
        "summary": summary,
    }
    OUT.write_text(json.dumps(fixture, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
