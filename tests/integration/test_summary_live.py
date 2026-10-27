"""The meal log the README summarises, against the real nutrition-mcp server and the shipped
catalogue. Nothing here needs a key or the network, because adding up a log calls no model.
"""

import datetime

import pytest
from nutrition_mcp.schemas import MealItemIn
from week_helpers import FIXTURES, plan_rules

from meal_planner.catalogue import load_catalogue
from meal_planner.config import Settings
from meal_planner.nutrition import NutritionClient
from meal_planner.summary import MealLog, summarise

pytestmark = pytest.mark.anyio

LOG = FIXTURES / "logs" / "office_weekday_log.json"


async def test_the_log_the_readme_summarises_is_accepted_by_the_real_server() -> None:
    settings = Settings()
    log = MealLog.model_validate_json(LOG.read_text(encoding="utf-8"))
    async with NutritionClient.start(settings.nutrition_data_dirs) as nutrition:
        catalogue = await load_catalogue(settings.catalogue_path, nutrition)

        summary = await summarise(
            plan_rules(), log, nutrition=nutrition, catalogue=catalogue, settings=settings
        )

    assert [day.date for day in summary.days] == [
        datetime.date(2026, 10, 26),
        datetime.date(2026, 10, 27),
        datetime.date(2026, 10, 28),
    ]
    assert (summary.planned_meals, summary.as_planned_share) == (12, 0.5)


async def test_rohu_and_curry_leaves_keep_to_the_saturated_fat_cap_by_their_total_fat() -> None:
    """CoFID 2021 has a total fat and no saturated fat for rohu and curry leaves, so the day's
    saturated fat is a lower bound. Their total fat bounds it from above, under the cap."""
    settings = Settings()
    monday = datetime.date(2026, 10, 26)
    log = MealLog.model_validate(
        {
            "version": 1,
            "entries": [
                {
                    "date": monday.isoformat(),
                    "slot_id": "lunch",
                    "status": "as_planned",
                    "items": [
                        {"food_id": "paneer", "amount": 30, "unit": "g"},
                        {"food_id": "rohu", "amount": 150, "unit": "g"},
                        {"food_id": "curry_leaves", "amount": 5, "unit": "g"},
                    ],
                }
            ],
        }
    )
    async with NutritionClient.start(settings.nutrition_data_dirs) as nutrition:
        catalogue = await load_catalogue(settings.catalogue_path, nutrition)
        lunch = await nutrition.meal(
            [
                MealItemIn(food_id=item.food_id, amount=item.amount, unit=item.unit)
                for item in log.entries[0].items
            ]
        )

        summary = await summarise(
            plan_rules(), log, nutrition=nutrition, catalogue=catalogue, settings=settings
        )

    assert lunch.total_incomplete["sat_fat_g"] == ["rohu", "curry_leaves"]
    (day,) = summary.days
    assert day.totals["sat_fat_g"] == lunch.total.sat_fat_g
    assert (day.totals["sat_fat_g"] or 0) < 6
    checks = [f.check for f in [*day.failures, *day.unsettled]]
    assert "day_sat_fat_g" not in checks
