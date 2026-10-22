from typing import Any

import pytest
from fakes import FakeNutrition
from nutrition_mcp.schemas import MealItemIn
from pydantic import ValidationError

from meal_planner.recipes import CheckedRecipe, DraftIngredient, DraftRecipe, SlotDraft
from meal_planner.rules import Slot, SlotSpec

pytestmark = pytest.mark.anyio


def recipe_fields(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "day": "mon",
        "title": "Masala oats",
        "cuisine": "North Indian",
        "ingredients": [{"id": "oats", "grams": 60}],
        "steps": ["Soak the oats overnight."],
        "active_min": 5,
        "total_min": 480,
        "gear": ["jar_with_lid"],
        "cook_when": "night_before",
        "store": "Fridge, 2 days.",
        "swap": "Rolled oats for quinoa flakes.",
        "fits": "Carries the breakfast protein.",
    }
    return base | changes


def test_a_complete_recipe_validates() -> None:
    recipe = DraftRecipe.model_validate(recipe_fields())

    assert recipe.ingredients == [DraftIngredient(id="oats", grams=60)]


@pytest.mark.parametrize("grams", [0, -5, 1000.5, 5000])
def test_grams_outside_zero_to_1000_are_refused(grams: float) -> None:
    with pytest.raises(ValidationError, match="grams"):
        DraftIngredient(id="oats", grams=grams)


def test_a_thousand_grams_is_allowed() -> None:
    assert DraftIngredient(id="milk", grams=1000).grams == 1000


@pytest.mark.parametrize(
    "changes",
    [
        {"ingredients": []},
        {"ingredients": [{"id": f"food_{n}", "grams": 10} for n in range(16)]},
        {"steps": []},
        {"steps": ["Stir."] * 11},
        {"steps": ["x" * 201]},
        {"steps": ["   "]},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 81},
        {"cuisine": "x" * 41},
        {"day": "someday"},
        {"gear": ["wok"]},
        {"cook_when": "later"},
        {"surprise": "extra field"},
    ],
)
def test_out_of_bounds_recipes_are_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DraftRecipe.model_validate(recipe_fields(**changes))


def test_fifteen_ingredients_ten_steps_and_the_longest_title_are_allowed() -> None:
    recipe = DraftRecipe.model_validate(
        recipe_fields(
            ingredients=[{"id": f"food_{n}", "grams": 10} for n in range(15)],
            steps=["x" * 200] * 10,
            title="x" * 80,
            cuisine="y" * 40,
        )
    )

    assert len(recipe.ingredients) == 15


def test_a_slot_draft_holds_the_recipes_of_one_reply() -> None:
    draft = SlotDraft.model_validate(
        {"slot_id": "breakfast", "recipes": [recipe_fields(), recipe_fields(day="tue")]}
    )

    assert [r.day for r in draft.recipes] == ["mon", "tue"]


async def test_a_checked_recipe_round_trips_through_json() -> None:
    nutrition = await FakeNutrition({"oats": {"kcal": 380.0}}).meal(
        [MealItemIn(food_id="oats", amount=60, unit="g")]
    )
    spec = SlotSpec(
        day="mon",
        day_type="rest",
        slot=Slot(
            id="breakfast",
            name="Breakfast",
            eat_at="08:00",
            kcal_share=1,
            protein_share=1,
            style="overnight oats",
            cook_when="night_before",
            max_active_min=10,
            max_total_min=600,
        ),
        kcal=500,
        protein_g=30,
    )
    checked = CheckedRecipe(
        spec=spec,
        draft=DraftRecipe.model_validate(recipe_fields()),
        nutrition=nutrition,
        failures=[],
        attempts=1,
    )

    assert CheckedRecipe.model_validate_json(checked.model_dump_json()) == checked
