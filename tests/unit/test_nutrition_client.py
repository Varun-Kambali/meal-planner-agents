"""NutritionClient against a stand-in session, and FakeNutrition, which later tests build on."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fakes import UNIT_SPELLINGS, FakeNutrition
from mcp import MCPError
from mcp.types import CallToolResult, TextContent
from nutrition_mcp.schemas import MealItemIn, MealResult, NutritionResult, SearchResult
from pydantic import ValidationError

from meal_planner.nutrition import (
    NutritionClient,
    NutritionError,
    NutritionPort,
    server_parameters,
)

pytestmark = pytest.mark.anyio

PER_100G = {
    "rajma": {"kcal": 330.0, "protein_g": 22.0, "carbs_available_g": 40.0},
    "ghee": {"kcal": 900.0, "fat_g": 100.0},
}


class StandInSession:
    """Answers every call with one canned result and remembers what was asked."""

    def __init__(self, result: CallToolResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        self.calls.append((name, arguments))
        return self.result


def tool_error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def structured(model: SearchResult | NutritionResult | MealResult) -> CallToolResult:
    return CallToolResult(content=[], structured_content=model.model_dump(mode="json"))


# The client


async def test_tool_error_maps_to_nutrition_error() -> None:
    message = "unknown unit 'katori' for rajma; valid units: g, katori"
    client = NutritionClient(StandInSession(tool_error(message)))

    with pytest.raises(NutritionError) as caught:
        await client.nutrition("rajma", 1, "katori")

    assert str(caught.value) == message
    assert caught.value.food_id == "rajma"
    assert caught.value.valid_units == ["g", "katori"]
    assert caught.value.unreachable is False


async def test_valid_units_are_read_from_the_end_of_the_message() -> None:
    """The message echoes the unit as typed, and the typed text can say "valid units: " too."""
    echoed = "unknown unit 'x; valid units: evil' for rajma; valid units: g, katori"
    client = NutritionClient(StandInSession(tool_error(echoed)))

    with pytest.raises(NutritionError) as caught:
        await client.nutrition("rajma", 1, "x; valid units: evil")

    assert caught.value.valid_units == ["g", "katori"]
    assert str(caught.value) == echoed


async def test_valid_units_in_a_meal_error_are_read_from_the_end_too() -> None:
    message = "item 1 (rajma): unknown unit 'valid units: evil' for rajma; valid units: g, katori"
    client = NutritionClient(StandInSession(tool_error(message)))

    with pytest.raises(NutritionError) as caught:
        await client.meal([MealItemIn(food_id="rajma", amount=1, unit="valid units: evil")])

    assert caught.value.food_id == "rajma"
    assert caught.value.valid_units == ["g", "katori"]


async def test_meal_error_names_the_failing_food() -> None:
    # The SDK puts "Error executing tool <name>: " in front of what the tool raised.
    message = "item 2 (test_rotti): unknown food id 'test_rotti'; close matches: test_roti"
    sdk_message = f"Error executing tool compute_meal: {message}"
    client = NutritionClient(StandInSession(tool_error(sdk_message)))

    with pytest.raises(NutritionError) as caught:
        await client.meal([MealItemIn(food_id="test_rotti", amount=1, unit="piece")])

    assert str(caught.value) == message
    assert caught.value.food_id == "test_rotti"
    assert caught.value.valid_units == []
    assert caught.value.unreachable is False


async def test_error_without_text_still_raises() -> None:
    client = NutritionClient(StandInSession(CallToolResult(content=[], is_error=True)))

    with pytest.raises(NutritionError, match="no text") as caught:
        await client.search("rajma")
    assert caught.value.unreachable is False


async def test_a_refusal_without_a_food_id_is_not_unreachable() -> None:
    """nutrition-mcp answered, so the server is there: only the food is unknown to the caller."""
    message = "the meal has no items"
    client = NutritionClient(StandInSession(tool_error(message)))

    with pytest.raises(NutritionError) as caught:
        await client.meal([MealItemIn(food_id="rajma", amount=100, unit="g")])

    assert str(caught.value) == message
    assert caught.value.food_id is None
    assert caught.value.unreachable is False


async def call_each_tool(client: NutritionClient, tool: str) -> None:
    if tool == "search_food":
        await client.search("rajma")
    elif tool == "get_nutrition":
        await client.nutrition("rajma", 150, "g")
    else:
        await client.meal([MealItemIn(food_id="rajma", amount=150, unit="g")])


TOOLS = ["search_food", "get_nutrition", "compute_meal"]


@pytest.mark.parametrize("tool", TOOLS)
async def test_a_result_that_does_not_fit_its_model_is_a_nutrition_error(tool: str) -> None:
    session = StandInSession(
        CallToolResult(content=[], structured_content={"query": "rajma", "results": "rajma"})
    )

    with pytest.raises(NutritionError) as caught:
        await call_each_tool(NutritionClient(session), tool)

    assert str(caught.value) == f"nutrition-mcp's result for {tool} could not be read"
    assert caught.value.food_id is None
    assert caught.value.unreachable is False
    assert isinstance(caught.value.__cause__, ValidationError)


def sdk_validation_error() -> ValidationError:
    try:
        SearchResult.model_validate({"query": "rajma"})
    except ValidationError as exc:
        return exc
    raise AssertionError("the result was expected not to fit")


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize(
    "error",
    [
        sdk_validation_error(),
        RuntimeError("Invalid structured content returned by tool x: 'rajma' is not a number"),
    ],
    ids=["protocol", "output_schema"],
)
async def test_the_sdk_refusing_a_result_is_a_nutrition_error(tool: str, error: Exception) -> None:
    """The SDK checks a result against the protocol and against the tool's output schema."""
    client = NutritionClient(BrokenSession(error))

    with pytest.raises(NutritionError) as caught:
        await call_each_tool(client, tool)

    assert str(caught.value) == f"nutrition-mcp's result for {tool} could not be read"
    assert caught.value.unreachable is False
    assert caught.value.__cause__ is error
    # The arguments, and the result that may echo them, stay out of the message.
    assert "rajma" not in str(caught.value)


class BrokenSession:
    """A session whose calls fail the way a dead or stuck connection does."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        raise self.error


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        (MCPError(-32000, "Connection closed"), "Connection closed"),
        (MCPError(-32001, "Timed out while waiting for response"), "Timed out"),
        (TimeoutError(), "TimeoutError"),
        (BrokenPipeError("pipe closed"), "pipe closed"),
    ],
    ids=["closed", "request_timeout", "send_timeout", "os_error"],
)
async def test_transport_failure_maps_to_nutrition_error(error: Exception, shown: str) -> None:
    client = NutritionClient(BrokenSession(error))

    with pytest.raises(NutritionError) as caught:
        await client.meal([MealItemIn(food_id="rajma", amount=100, unit="g")])

    assert "compute_meal" in str(caught.value)
    assert shown in str(caught.value)
    # No food is to blame and no unit would help, unlike a refusal of one item.
    assert caught.value.food_id is None
    assert caught.value.valid_units == []
    assert caught.value.unreachable is True
    assert caught.value.__cause__ is error
    # What the user asked about is not repeated in the message.
    assert "rajma" not in str(caught.value)


async def test_an_mcp_error_is_named_by_its_message_not_its_code() -> None:
    client = NutritionClient(BrokenSession(MCPError(-32000, "Connection\n closed")))

    with pytest.raises(NutritionError) as caught:
        await client.meal([MealItemIn(food_id="rajma", amount=100, unit="g")])

    assert str(caught.value) == "nutrition-mcp could not answer compute_meal: Connection closed"


async def test_every_call_names_its_tool_when_the_connection_fails() -> None:
    client = NutritionClient(BrokenSession(MCPError(-32000, "Connection closed")))

    with pytest.raises(NutritionError, match="search_food"):
        await client.search("rajma")
    with pytest.raises(NutritionError, match="get_nutrition") as caught:
        await client.nutrition("rajma", 1, "g")
    assert caught.value.food_id is None


async def test_cancelling_a_call_is_not_turned_into_an_error() -> None:
    client = NutritionClient(BrokenSession(asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await client.search("rajma")


async def test_missing_structured_content_is_an_error() -> None:
    client = NutritionClient(StandInSession(CallToolResult(content=[])))

    with pytest.raises(NutritionError, match="structured"):
        await client.search("rajma")


async def test_calls_send_only_the_arguments_given() -> None:
    fake = FakeNutrition(PER_100G)
    session = StandInSession(structured(await fake.search("rajma")))
    client = NutritionClient(session)

    await client.search("rajma")
    await client.search("rajma", kind="dish", limit=2)

    assert session.calls == [
        ("search_food", {"query": "rajma", "limit": 5}),
        ("search_food", {"query": "rajma", "kind": "dish", "limit": 2}),
    ]


async def test_results_are_validated_into_nutrition_mcp_models() -> None:
    fake = FakeNutrition(PER_100G)
    session = StandInSession(structured(await fake.nutrition("rajma", 150, "g")))
    client = NutritionClient(session)

    result = await client.nutrition("rajma", 150, "g")

    assert isinstance(result, NutritionResult)
    assert result == await fake.nutrition("rajma", 150, "g")
    assert session.calls == [("get_nutrition", {"food_id": "rajma", "amount": 150, "unit": "g"})]

    items = [MealItemIn(food_id="rajma", amount=150, unit="g")]
    session.result = structured(await fake.meal(items))
    meal = await client.meal(items)

    assert isinstance(meal, MealResult)
    assert session.calls[-1] == (
        "compute_meal",
        {"items": [{"food_id": "rajma", "amount": 150.0, "unit": "g"}]},
    )


# How the server is started


def test_server_gets_data_dirs_only_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    # A variable in this process must not leak through: empty means the bundled data only.
    monkeypatch.setenv("NUTRITION_MCP_DATA_DIRS", "/elsewhere")

    assert "NUTRITION_MCP_DATA_DIRS" not in (server_parameters("").env or {})
    assert server_parameters("/a:/b").env == {"NUTRITION_MCP_DATA_DIRS": "/a:/b"}


def test_server_command_is_the_installed_script() -> None:
    command = Path(server_parameters("").command)

    assert command.name.startswith("nutrition-mcp")
    assert command.exists()


def test_missing_server_command_says_where_it_looked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meal_planner.nutrition.shutil.which", lambda *_args, **_kwargs: None)

    with pytest.raises(FileNotFoundError, match="nutrition-mcp command is not installed"):
        server_parameters("")


# FakeNutrition


def test_fake_and_client_both_provide_the_port() -> None:
    assert isinstance(FakeNutrition(PER_100G), NutritionPort)
    assert isinstance(NutritionClient(StandInSession(CallToolResult(content=[]))), NutritionPort)


async def test_fake_search_filters_and_reports_the_total() -> None:
    fake = FakeNutrition(PER_100G, kinds={"rajma": "dish"})

    found = await fake.search("rajma")
    assert [food.id for food in found.results] == ["rajma"]
    assert found.results[0].kind == "dish"
    assert found.total == 1
    assert found.note is None

    assert (await fake.search("rajma", kind="ingredient")).results == []
    missing = await fake.search("nothing like it")
    assert missing.results == []
    assert missing.note is not None

    two = FakeNutrition({**PER_100G, "rajma_chawal": PER_100G["rajma"]})
    cut = await two.search("rajma", limit=1)
    assert (len(cut.results), cut.total) == (1, 2)


async def test_fake_scales_per_100g_values_by_grams() -> None:
    fake = FakeNutrition(PER_100G, diets={"ghee": "veg", "rajma": "vegan"})

    result = await fake.nutrition("rajma", 150, "g")

    assert result.grams == 150
    assert result.nutrients.kcal == 495
    assert result.nutrients.protein_g == 33
    assert result.nutrients.fat_g is None
    assert result.food.diet == "vegan"
    assert result.food.units == {"g": 1.0}


async def test_fake_takes_the_units_a_test_gives_it() -> None:
    fake = FakeNutrition(PER_100G, units={"rajma": {"katori": 120.0}})

    result = await fake.nutrition("rajma", 2, "katori")

    assert result.grams == 240
    assert result.food.units == {"g": 1.0, "katori": 120.0}


async def test_fake_reads_unit_spellings_as_the_server_does() -> None:
    fake = FakeNutrition(
        {"chapati": {"kcal": 300.0}}, units={"chapati": {"roti": 40.0, "piece": 40.0}}
    )

    for spelling, canonical in UNIT_SPELLINGS.items():
        spelled = await fake.nutrition("chapati", 2, spelling)
        plain = await fake.nutrition("chapati", 2, canonical)
        assert spelled.grams == plain.grams > 0, spelling
        assert spelled.nutrients == plain.nutrients, spelling
        # The result echoes the unit as it was typed, and the note names the unit it meant.
        assert spelled.unit == spelling
        assert spelled.measure_assumption == plain.measure_assumption

    meal = await fake.meal([MealItemIn(food_id="chapati", amount=2, unit="Rotis")])
    assert meal.items[0].grams == 80

    with pytest.raises(NutritionError) as caught:
        await fake.nutrition("chapati", 1, "Katoris")
    assert caught.value.valid_units == ["g", "roti", "piece"]


async def test_fake_raises_the_same_errors_as_the_client() -> None:
    fake = FakeNutrition(PER_100G, units={"rajma": {"katori": 120.0}})

    with pytest.raises(NutritionError) as unknown_food:
        await fake.nutrition("nope", 1, "g")
    assert unknown_food.value.food_id == "nope"

    with pytest.raises(NutritionError) as unknown_unit:
        await fake.nutrition("rajma", 1, "roti")
    assert unknown_unit.value.food_id == "rajma"
    assert unknown_unit.value.valid_units == ["g", "katori"]

    with pytest.raises(NutritionError) as in_meal:
        await fake.meal(
            [
                MealItemIn(food_id="ghee", amount=5, unit="g"),
                MealItemIn(food_id="x", amount=1, unit="g"),
            ]
        )
    assert in_meal.value.food_id == "x"


async def test_fake_meal_sums_items_and_names_what_is_missing() -> None:
    fake = FakeNutrition(PER_100G, gi={"rajma": 29.0})
    items = [
        MealItemIn(food_id="rajma", amount=100, unit="g"),
        MealItemIn(food_id="ghee", amount=10, unit="g"),
    ]

    meal = await fake.meal(items)

    assert meal.total.kcal == 330 + 90
    assert meal.total.protein_g == 22
    assert meal.total.fat_g == 10
    # Only ghee reports fat, and only rajma reports protein: each total is then a floor.
    assert meal.total_incomplete["protein_g"] == ["ghee"]
    assert meal.total_incomplete["fat_g"] == ["rajma"]
    assert "kcal" not in meal.total_incomplete
    assert meal.glycemic_load == 11.6
    assert meal.glycemic_load_missing == ["ghee"]
    assert [item.food.id for item in meal.items] == ["rajma", "ghee"]
