"""Stand-ins for the app's services, for unit tests. Import them with `from fakes import ...`."""

from collections.abc import Mapping
from typing import Literal

from nutrition_mcp.models import Diet, Kind, Nutrients
from nutrition_mcp.nutrition import glycemic_load, scale, sum_nutrients
from nutrition_mcp.schemas import (
    DECIMALS,
    FoodSummary,
    MealItemIn,
    MealResult,
    NutritionResult,
    SearchResult,
)

from meal_planner.nutrition import NutritionError


class FakeNutrition:
    """nutrition-mcp without the server: real result models, built from per-100 g values.

    `per_100g` maps a food id to its nutrients per 100 g, using nutrition-mcp's nutrient names
    (kcal, protein_g, carbs_available_g ...). A nutrient a food leaves out is reported as
    missing, the way the server reports one. Every food takes grams; `units` adds the others a
    test needs, as grams in one of each. A food is a vegetarian ingredient with no glycemic
    index unless `diets`, `kinds` or `gi` say otherwise.
    """

    def __init__(
        self,
        per_100g: Mapping[str, Mapping[str, float]],
        *,
        units: Mapping[str, Mapping[str, float]] | None = None,
        diets: Mapping[str, Diet] | None = None,
        kinds: Mapping[str, Kind] | None = None,
        gi: Mapping[str, float] | None = None,
    ) -> None:
        self._per_100g = per_100g
        self._units = units or {}
        self._diets = diets or {}
        self._kinds = kinds or {}
        self._gi = gi or {}

    def _summary(self, food_id: str) -> FoodSummary:
        return FoodSummary(
            id=food_id,
            kind=self._kinds.get(food_id, "ingredient"),
            name=food_id.replace("_", " ").capitalize(),
            aliases=[],
            regions=[],
            diet=self._diets.get(food_id, "veg"),
            source_ids=["fake"],
            units={"g": 1.0, **self._units.get(food_id, {})},
        )

    async def search(
        self,
        query: str,
        kind: Literal["dish", "ingredient"] | None = None,
        limit: int = 5,
    ) -> SearchResult:
        words = query.lower().split()
        matches = [
            self._summary(food_id)
            for food_id in self._per_100g
            if all(word in food_id.replace("_", " ") for word in words)
            and kind in (None, self._kinds.get(food_id, "ingredient"))
        ]
        return SearchResult(
            query=query,
            results=matches[:limit],
            total=len(matches),
            suggestions=[],
            note=None if matches else "not in the data: report the food as missing",
        )

    async def nutrition(self, food_id: str, amount: float, unit: str) -> NutritionResult:
        return self._portion(food_id, amount, unit)[0]

    def _portion(self, food_id: str, amount: float, unit: str) -> tuple[NutritionResult, Nutrients]:
        """The result, and the nutrients before rounding, which is what a meal adds up."""
        if food_id not in self._per_100g:
            raise NutritionError(f"unknown food id '{food_id}'", food_id)
        summary = self._summary(food_id)
        if unit not in summary.units:
            valid = list(summary.units)
            message = f"unknown unit '{unit}' for {food_id}; valid units: {', '.join(valid)}"
            raise NutritionError(message, food_id, valid)

        grams = amount * summary.units[unit]
        exact = scale(Nutrients.model_validate(self._per_100g[food_id]), grams)
        shown = Nutrients.model_validate(
            {key: None if value is None else round(value, DECIMALS) for key, value in exact}
        )
        gi = self._gi.get(food_id)
        result = NutritionResult(
            food=summary,
            amount=amount,
            unit=unit,
            grams=round(grams, DECIMALS),
            measure_assumption="" if unit == "g" else f"1 {unit} = {summary.units[unit]:g} g",
            nutrients=shown,
            incomplete={},
            raw_ingredient_totals=[],
            gi=gi,
            gi_ref=None,
            glycemic_load=glycemic_load(gi, exact.carbs_available_g),
            oil_assumption=None,
            sources=["fake"],
        )
        return result, exact

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        portions = [self._portion(item.food_id, item.amount, item.unit) for item in items]
        total, lacking = sum_nutrients([(result.food.id, exact) for result, exact in portions])
        loads = [result.glycemic_load for result, _ in portions]
        known = [load for load in loads if load is not None]
        return MealResult(
            items=[result for result, _ in portions],
            total=Nutrients.model_validate(
                {key: None if value is None else round(value, DECIMALS) for key, value in total}
            ),
            total_incomplete=lacking,
            glycemic_load=round(sum(known), 1) if known else None,
            glycemic_load_missing=[
                result.food.id
                for (result, _), load in zip(portions, loads, strict=True)
                if load is None
            ],
        )
