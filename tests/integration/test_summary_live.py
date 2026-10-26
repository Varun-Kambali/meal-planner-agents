"""The meal log the README summarises, against the real nutrition-mcp server and the shipped
catalogue. Nothing here needs a key or the network, because adding up a log calls no model.
"""

import datetime

import pytest
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
