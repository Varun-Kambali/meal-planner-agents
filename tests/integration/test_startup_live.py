"""Starting the app against the real nutrition-mcp, which needs no keys and no network."""

from pathlib import Path

import pytest
from week_helpers import model_for, write_catalogue_file

from meal_planner.config import Settings
from meal_planner.startup import StartupError, start_services

pytestmark = pytest.mark.anyio


async def test_the_real_catalogue_loads_against_the_real_server() -> None:
    async with start_services(Settings(), model=model_for()) as services:
        assert "rajma" in services.catalogue.items


async def test_a_data_folder_that_is_not_there_stops_the_start_with_one_line(
    tmp_path: Path,
) -> None:
    settings = Settings(
        nutrition_data_dirs=str(tmp_path / "missing"),
        catalogue_path=write_catalogue_file(tmp_path),
    )

    with pytest.raises(StartupError) as raised:
        async with start_services(settings, model=model_for()):
            pass

    message = str(raised.value)
    assert message.startswith("nutrition-mcp did not start: ")
    assert "Connection closed" in message
    assert "\n" not in message
