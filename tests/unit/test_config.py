import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from meal_planner.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in [n for n in os.environ if n.startswith("MEAL_PLANNER_")]:
        monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults() -> None:
    s = Settings()

    assert s.model == "gemini/gemini-3.1-flash-lite"
    assert s.recipe_kcal_tolerance == 0.10
    assert s.recipe_protein_min_share == 0.90
    assert s.day_kcal_tolerance == 0.05
    assert s.max_repair_attempts == 3
    assert s.variety_jaccard_fail_at == 0.6
    assert s.no_waste_keeps_days == 7
    assert s.no_waste_min_pack_share == 0.5
    assert s.max_concurrent_model_calls == 4
    assert s.slot_call_timeout_s == 120
    assert s.fit_portions is True
    assert s.portion_scale_min == 0.5
    assert s.portion_scale_max == 2.0
    assert s.writer_nutrient_hints is True
    assert s.max_meal_text_chars == 500
    assert s.max_body_bytes == 65536
    assert s.max_replace_body_bytes == 131072
    assert s.rate_limit_per_minute == 10
    assert s.cors_origins == []


def test_the_accuracy_switches_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEAL_PLANNER_FIT_PORTIONS", "false")
    monkeypatch.setenv("MEAL_PLANNER_WRITER_NUTRIENT_HINTS", "false")

    s = Settings()

    assert s.fit_portions is False
    assert s.writer_nutrient_hints is False


def test_catalogue_path_defaults_to_the_repo_data_folder() -> None:
    path = Settings().catalogue_path

    assert path.is_absolute()
    assert path.parts[-2:] == ("data", "catalogue.yaml")
    assert (path.parent.parent / "pyproject.toml").is_file()


def test_nutrition_data_dirs_defaults_to_the_repo_food_file() -> None:
    folder = Path(Settings().nutrition_data_dirs)

    assert folder.is_absolute()
    assert folder.parts[-2:] == ("data", "foods")
    assert (folder / "ingredients.csv").is_file()
    assert folder.parent == Settings().catalogue_path.parent


def test_nutrition_data_dirs_can_be_set_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty means nutrition-mcp's bundled data only.
    monkeypatch.setenv("MEAL_PLANNER_NUTRITION_DATA_DIRS", "")

    assert Settings().nutrition_data_dirs == ""


def test_environment_overrides_use_the_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEAL_PLANNER_MODEL", "gemini/other")
    monkeypatch.setenv("MEAL_PLANNER_MAX_REPAIR_ATTEMPTS", "1")
    monkeypatch.setenv("MEAL_PLANNER_CATALOGUE_PATH", "/srv/catalogue.yaml")
    monkeypatch.setenv("MEAL_PLANNER_NUTRITION_DATA_DIRS", "/srv/foods:/srv/more")
    monkeypatch.setenv("MODEL", "ignored")

    s = Settings()

    assert s.model == "gemini/other"
    assert s.max_repair_attempts == 1
    assert s.catalogue_path == Path("/srv/catalogue.yaml")
    assert s.nutrition_data_dirs == "/srv/foods:/srv/more"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MEAL_PLANNER_DAY_KCAL_TOLERANCE", "five percent"),
        ("MEAL_PLANNER_DAY_KCAL_TOLERANCE", "0"),
        ("MEAL_PLANNER_MAX_REPAIR_ATTEMPTS", "-1"),
        ("MEAL_PLANNER_MAX_BODY_BYTES", "0"),
        ("MEAL_PLANNER_MAX_REPLACE_BODY_BYTES", "0"),
        ("MEAL_PLANNER_RATE_LIMIT_PER_MINUTE", "0"),
        ("MEAL_PLANNER_MAX_CONCURRENT_MODEL_CALLS", "0"),
        ("MEAL_PLANNER_SLOT_CALL_TIMEOUT_S", "0"),
        ("MEAL_PLANNER_PORTION_SCALE_MIN", "0"),
        ("MEAL_PLANNER_PORTION_SCALE_MIN", "1.5"),
        ("MEAL_PLANNER_PORTION_SCALE_MAX", "0.9"),
    ],
)
def test_bad_environment_value_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=name.removeprefix("MEAL_PLANNER_").lower()):
        Settings()


@pytest.mark.parametrize(
    ("value", "origins"),
    [
        ("http://localhost:5173", ["http://localhost:5173"]),
        (
            "http://localhost:5173, https://meals.example.org ,",
            ["http://localhost:5173", "https://meals.example.org"],
        ),
        ("", []),
        (" , ", []),
    ],
)
def test_cors_origins_are_a_comma_separated_list(
    monkeypatch: pytest.MonkeyPatch, value: str, origins: list[str]
) -> None:
    monkeypatch.setenv("MEAL_PLANNER_CORS_ORIGINS", value)

    assert Settings().cors_origins == origins


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    first = get_settings()
    monkeypatch.setenv("MEAL_PLANNER_MODEL", "changed-later")

    assert get_settings() is first
    assert get_settings().model == "gemini/gemini-3.1-flash-lite"
