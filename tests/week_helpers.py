"""What the week and API tests share: the fixture plan, a scripted model and a fake nutrition-mcp.

Every fixture food has the same profile, so a week built from the canned replies passes every
check, and a test changes one reply to make one thing fail.
"""

import asyncio
import dataclasses
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fakes import FakeNutrition
from nutrition_mcp.schemas import MealItemIn, MealResult
from pydantic import BaseModel

from meal_planner.catalogue import Catalogue, CatalogueItem
from meal_planner.llm import FakeModel, ModelReply
from meal_planner.rules import PlanRules

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLAN = FIXTURES / "plans" / "office_weekday.json"
SLOTS = ("breakfast", "lunch", "snack", "dinner")

# Every fixture food has the same profile, so a recipe's numbers depend only on its total grams.
# The canned replies (tests/fixtures/replies) size each recipe to its slot's calories, which
# makes the week pass every check. `sugar` has no protein and twice the calories, for recipes
# that must miss their protein share.
PROFILE = {
    "kcal": 200,
    "protein_g": 14,
    "fat_g": 6,
    "sat_fat_g": 1,
    "carbs_available_g": 24,
    "fibre_g": 5,
    "sodium_mg": 100,
    "calcium_mg": 100,
    "iron_mg": 2,
}


def plan_rules(**changes: Any) -> PlanRules:
    plan = json.loads(PLAN.read_text(encoding="utf-8")) | changes
    return PlanRules.model_validate(plan)


def canned(slot_id: str) -> dict[str, Any]:
    """The reply for a slot that passes every check, as a dict to change."""
    data: dict[str, Any] = json.loads(
        (FIXTURES / "replies" / f"{slot_id}.json").read_text(encoding="utf-8")
    )
    return data


def raw(reply: dict[str, Any]) -> str:
    return json.dumps(reply)


def recipe_of(slot_id: str, day: str) -> dict[str, Any]:
    found: dict[str, Any] = next(r for r in canned(slot_id)["recipes"] if r["day"] == day)
    return found


def repair_reply(slot_id: str, *days: str) -> str:
    return raw({"slot_id": slot_id, "recipes": [recipe_of(slot_id, day) for day in days]})


def without_protein(recipe: dict[str, Any]) -> dict[str, Any]:
    """The recipe made of sugar alone, at half the grams so the calories stay the same."""
    grams = sum(i["grams"] for i in recipe["ingredients"]) / 2
    return recipe | {"ingredients": [{"id": "sugar", "grams": round(grams, 2)}]}


def scaled(recipe: dict[str, Any], factor: float) -> dict[str, Any]:
    ingredients = [{**i, "grams": round(i["grams"] * factor, 2)} for i in recipe["ingredients"]]
    return recipe | {"ingredients": ingredients}


def changed(slot_id: str, change: Callable[[dict[str, Any]], dict[str, Any]], *days: str) -> str:
    """The slot's canned reply with `change` applied to the recipes of `days` (all if none)."""
    reply = canned(slot_id)
    reply["recipes"] = [change(r) if not days or r["day"] in days else r for r in reply["recipes"]]
    return raw(reply)


def good(slot_id: str) -> str:
    return raw(canned(slot_id))


def item(food_id: str, *, pack_g: float | None = None, keeps: int | None = None) -> CatalogueItem:
    return CatalogueItem.model_validate(
        {
            "id": food_id,
            "name": food_id.replace("_", " ").capitalize(),
            "category": "other",
            "allergens": [],
            "pack_g": pack_g,
            "keeps_days_opened": keeps,
            "diet": "veg",
            "units": ["g"],
        }
    )


FOOD_IDS = sorted(
    {i["id"] for s in SLOTS for r in canned(s)["recipes"] for i in r["ingredients"]} | {"sugar"}
)


def catalogue(*extra: CatalogueItem) -> Catalogue:
    items = [item(food_id) for food_id in FOOD_IDS]
    return Catalogue({i.id: i for i in [*items, *extra]})


def foods() -> dict[str, dict[str, float]]:
    return {food_id: PROFILE for food_id in FOOD_IDS} | {
        "sugar": PROFILE | {"kcal": 400, "protein_g": 0}
    }


class SpyNutrition(FakeNutrition):
    """FakeNutrition that remembers the food ids of every meal it was asked about."""

    def __init__(self) -> None:
        super().__init__(foods())
        self.meals: list[list[str]] = []

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        self.meals.append([i.food_id for i in items])
        return await super().meal(items)


class SlotModel:
    """Answers each slot from its own queue of replies, whatever order the slots call in.

    `replies` maps a slot id to the raw replies for each task. A slot in `errors` gets that
    error code on every call, as a model that was rate limited does; "slot/task" as the key
    limits it to one task. A slot in `gates` does not
    answer until its event is set. `peak` is the most calls that were in flight together.
    """

    def __init__(
        self,
        replies: dict[str, dict[str, list[str]]],
        *,
        errors: dict[str, str] | None = None,
        tokens: tuple[int, int, float | None] = (0, 0, 0.0),
        gates: dict[str, asyncio.Event] | None = None,
    ) -> None:
        self._queues = {slot: FakeModel(tasks) for slot, tasks in replies.items()}
        self._errors = errors or {}
        self._tokens = tokens
        self._gates = gates or {}
        self.calls: list[tuple[str, str, str]] = []
        self.in_flight = 0
        self.peak = 0

    def of_task(self, task: str) -> list[tuple[str, str]]:
        return [(system, user) for t, system, user in self.calls if t == task]

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        found = re.search(r'slot_id: "(\w+)"', user)
        assert found, f"no slot id in the prompt: {user[:200]}"
        slot_id = found.group(1)
        self.calls.append((task, system, user))
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            # Gives the other slots' calls time to start, so overlap shows in `peak`.
            await asyncio.sleep(0.005)
            if slot_id in self._gates:
                await self._gates[slot_id].wait()
            error = self._errors.get(f"{slot_id}/{task}") or self._errors.get(slot_id)
            if error:
                return ModelReply(value=None, raw="", error=error)
            reply = await self._queues[slot_id].structured(
                task=task, system=system, user=user, schema=schema, timeout_s=timeout_s
            )
            tokens_in, tokens_out, cost = self._tokens
            return dataclasses.replace(
                reply, input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=cost
            )
        finally:
            self.in_flight -= 1


def model_for(
    writes: dict[str, list[str]] | None = None,
    repairs: dict[str, list[str]] | None = None,
    **kwargs: Any,
) -> SlotModel:
    """A model whose slots answer with `writes` (default: the passing reply) then `repairs`."""
    writes = writes or {}
    repairs = repairs or {}
    return SlotModel(
        {
            s: {
                "write_slot": writes.get(s, [good(s)]),
                "repair_slot": repairs.get(s, []),
            }
            for s in SLOTS
        },
        **kwargs,
    )
