"""Starting the app: what must be true first, and the one-line reason when it is not."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml
from fakes import FakeNutrition
from mcp import MCPError
from week_helpers import SpyNutrition, model_for, write_catalogue_file

from meal_planner.config import Settings
from meal_planner.llm import FakeModel
from meal_planner.nutrition import NutritionClient, NutritionError
from meal_planner.startup import StartupError, check_tracing_off, start_services

pytestmark = pytest.mark.anyio

TRACING = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2")


def settings_for(folder: Path) -> Settings:
    return Settings(catalogue_path=write_catalogue_file(folder))


# Tracing


@pytest.mark.parametrize("name", TRACING)
@pytest.mark.parametrize("value", ["true", "True", "1", "yes", " on "])
def test_tracing_switched_on_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(StartupError, match=name) as raised:
        check_tracing_off()

    assert "\n" not in str(raised.value)
    assert "LangSmith" in str(raised.value)


@pytest.mark.parametrize("value", ["false", "0", "", "no", "off"])
def test_tracing_switched_off_does_not_stop_the_start(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    for name in TRACING:
        monkeypatch.setenv(name, value)

    check_tracing_off()


async def test_tracing_is_checked_even_when_a_model_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    with pytest.raises(StartupError, match="LANGSMITH_TRACING"):
        async with start_services(
            settings_for(tmp_path), model=model_for(), nutrition=SpyNutrition()
        ):
            pass


# The model's key


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("no_keys")
async def test_a_missing_key_is_named(tmp_path: Path) -> None:
    with pytest.raises(StartupError) as raised:
        async with start_services(settings_for(tmp_path), nutrition=SpyNutrition()):
            pass

    message = str(raised.value)
    assert "GEMINI_API_KEY" in message
    assert "GOOGLE_API_KEY" in message
    assert "gemini/gemini-3.1-flash-lite" in message
    assert "\n" not in message


@pytest.mark.usefixtures("no_keys")
async def test_another_providers_key_does_not_stand_in_and_is_never_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-appear")

    with pytest.raises(StartupError) as raised:
        async with start_services(settings_for(tmp_path), nutrition=SpyNutrition()):
            pass

    assert "sk-must-not-appear" not in str(raised.value)


@pytest.mark.usefixtures("no_keys")
async def test_the_models_own_key_lets_the_start_go_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    async with start_services(settings_for(tmp_path), nutrition=SpyNutrition()) as services:
        assert services.model.model == "gemini/gemini-3.1-flash-lite"  # type: ignore[attr-defined]


@pytest.mark.usefixtures("no_keys")
async def test_a_model_given_by_the_caller_needs_no_key(tmp_path: Path) -> None:
    model = model_for()

    async with start_services(settings_for(tmp_path), model=model, nutrition=SpyNutrition()) as s:
        assert s.model is model


@pytest.mark.usefixtures("no_keys")
async def test_a_run_that_makes_no_model_call_needs_no_key(tmp_path: Path) -> None:
    async with start_services(
        settings_for(tmp_path), nutrition=SpyNutrition(), needs_model=False
    ) as services:
        assert services.catalogue.items


@pytest.mark.usefixtures("no_keys")
async def test_a_model_litellm_has_no_variable_for_is_let_through(tmp_path: Path) -> None:
    # LiteLLM says nothing is missing for a model it does not know, and a guess would be wrong.
    settings = Settings(model="unmapped/model", catalogue_path=write_catalogue_file(tmp_path))

    async with start_services(settings, nutrition=SpyNutrition()):
        pass


# nutrition-mcp


def failing_start(error: BaseException) -> object:
    @asynccontextmanager
    async def start(data_dirs: str) -> AsyncIterator[NutritionClient]:
        raise error
        yield  # pragma: no cover

    return start


async def test_nutrition_mcp_that_does_not_start_gives_the_innermost_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    nested = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [
            ExceptionGroup(
                "unhandled errors in a TaskGroup", [MCPError(-32000, "Connection closed")]
            )
        ],
    )
    monkeypatch.setattr(NutritionClient, "start", failing_start(nested))

    with pytest.raises(StartupError) as raised:
        async with start_services(settings_for(tmp_path), model=model_for()):
            pass

    message = str(raised.value)
    assert message.startswith("nutrition-mcp did not start")
    assert "Connection closed" in message
    assert "-32000" not in message
    assert "ExceptionGroup" not in message
    assert "\n" not in message


async def test_nutrition_mcp_that_is_not_installed_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    error = FileNotFoundError("the nutrition-mcp command is not installed in /x/bin or on PATH")
    monkeypatch.setattr(NutritionClient, "start", failing_start(error))

    with pytest.raises(StartupError, match="not installed"):
        async with start_services(settings_for(tmp_path), model=model_for()):
            pass


async def test_a_nutrition_client_given_by_the_caller_is_not_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(NutritionClient, "start", failing_start(AssertionError("started")))
    nutrition = SpyNutrition()

    async with start_services(settings_for(tmp_path), model=model_for(), nutrition=nutrition) as s:
        assert s.nutrition is nutrition


async def test_a_started_nutrition_client_is_closed_when_the_block_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def start(data_dirs: str) -> AsyncIterator[SpyNutrition]:
        events.append(f"start {data_dirs == Settings().nutrition_data_dirs}")
        yield SpyNutrition()
        events.append("stop")

    monkeypatch.setattr(NutritionClient, "start", start)

    async with start_services(settings_for(tmp_path), model=model_for()):
        events.append("inside")

    assert events == ["start True", "inside", "stop"]


# The catalogue


async def test_a_catalogue_id_nutrition_mcp_does_not_know_is_named(tmp_path: Path) -> None:
    path = write_catalogue_file(tmp_path)
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows.append({**rows[0], "id": "unobtainium"})
    path.write_text(yaml.safe_dump(rows), encoding="utf-8")

    with pytest.raises(StartupError, match="unobtainium") as raised:
        async with start_services(
            Settings(catalogue_path=path), model=model_for(), nutrition=SpyNutrition()
        ):
            pass

    assert "\n" not in str(raised.value)


async def test_a_catalogue_file_that_is_not_yaml_gives_one_line(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.yaml"
    path.write_text("- id: [unclosed\n  name: x\n", encoding="utf-8")

    with pytest.raises(StartupError) as raised:
        async with start_services(
            Settings(catalogue_path=path), model=model_for(), nutrition=SpyNutrition()
        ):
            pass

    assert "\n" not in str(raised.value)
    assert "catalogue" in str(raised.value)


class DeadNutrition(FakeNutrition):
    async def nutrition(self, food_id: str, amount: float, unit: str):  # type: ignore[no-untyped-def,override]
        raise NutritionError(
            "nutrition-mcp could not answer get_nutrition: Connection closed", unreachable=True
        )


async def test_nutrition_mcp_going_away_while_the_catalogue_loads_is_a_start_failure(
    tmp_path: Path,
) -> None:
    with pytest.raises(StartupError, match="Connection closed") as raised:
        async with start_services(
            settings_for(tmp_path), model=FakeModel({}), nutrition=DeadNutrition({})
        ):
            pass

    assert str(raised.value).startswith("nutrition-mcp")


async def test_the_services_hold_the_loaded_catalogue(tmp_path: Path) -> None:
    async with start_services(
        settings_for(tmp_path), model=model_for(), nutrition=SpyNutrition()
    ) as services:
        assert "sugar" in services.catalogue.items
        assert services.catalogue.get("sugar").units == ["g"]
