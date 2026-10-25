"""NutritionClient against the real nutrition-mcp server, started as a subprocess.

The server loads its bundled data, so these tests need no keys and no network.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from fakes import UNIT_SPELLINGS, FakeNutrition
from nutrition_mcp.schemas import MealItemIn, NutritionResult

from meal_planner.nutrition import NutritionClient, NutritionError, NutritionPort

pytestmark = pytest.mark.anyio

EXTRA_FOODS = Path(__file__).parent.parent / "fixtures" / "extra_foods"


@pytest.fixture
async def live() -> AsyncIterator[NutritionClient]:
    async with NutritionClient.start("") as client:
        yield client


async def test_search_finds_rajma(live: NutritionClient) -> None:
    found = await live.search("rajma")

    assert found.results
    assert any("rajma" in food.id for food in found.results)
    assert all(food.units["g"] == 1.0 for food in found.results)


async def test_nutrition_two_rotis(live: NutritionClient) -> None:
    result = await live.nutrition("chapati", 2, "roti")

    assert result.grams > 0
    assert result.food.diet in ("veg", "vegan")
    assert "roti" in result.food.units


async def test_meal_totals_match_items(live: NutritionClient) -> None:
    meal = await live.meal(
        [
            MealItemIn(food_id="chapati", amount=2, unit="roti"),
            MealItemIn(food_id="rajma", amount=100, unit="g"),
        ]
    )

    item_kcal = sum(item.nutrients.kcal or 0 for item in meal.items)
    assert meal.total.kcal == pytest.approx(item_kcal, abs=0.01)
    assert [item.food.id for item in meal.items] == ["chapati", "rajma"]


async def test_unknown_unit_lists_valid_units(live: NutritionClient) -> None:
    with pytest.raises(NutritionError) as caught:
        await live.nutrition("chapati", 1, "gallon")

    assert caught.value.food_id == "chapati"
    assert {"g", "roti"} <= set(caught.value.valid_units)


async def test_unit_spellings_the_fake_accepts_work_live(live: NutritionClient) -> None:
    # FakeNutrition reads these the way the server does; this keeps the two from drifting.
    for spelling, canonical in UNIT_SPELLINGS.items():
        spelled = await live.nutrition("chapati", 2, spelling)
        plain = await live.nutrition("chapati", 2, canonical)
        assert spelled.grams == plain.grams > 0, spelling


async def test_meal_error_names_the_failing_food(live: NutritionClient) -> None:
    items = [
        MealItemIn(food_id="chapati", amount=1, unit="roti"),
        MealItemIn(food_id="chapati", amount=1, unit="gallon"),
    ]

    with pytest.raises(NutritionError) as caught:
        await live.meal(items)

    assert caught.value.food_id == "chapati"
    assert "roti" in caught.value.valid_units


async def test_unknown_food_is_a_nutrition_error(live: NutritionClient) -> None:
    with pytest.raises(NutritionError) as caught:
        await live.nutrition("no_such_food_xyz", 1, "g")

    assert caught.value.food_id == "no_such_food_xyz"
    assert caught.value.valid_units == []


async def test_parallel_calls_share_one_server(live: NutritionClient) -> None:
    # The graph writes slots in parallel, and every slot calls through the same client.
    amounts = [10, 20, 30, 40, 50]
    results: dict[int, NutritionResult] = {}

    async def look_up(amount: int) -> None:
        results[amount] = await live.nutrition("chapati", amount, "g")

    async with anyio.create_task_group() as group:
        for amount in amounts:
            group.start_soon(look_up, amount)

    assert {amount: result.grams for amount, result in results.items()} == {
        amount: amount for amount in amounts
    }


async def test_data_dirs_adds_foods_to_the_bundled_data() -> None:
    async with NutritionClient.start("") as bundled:
        assert not (await bundled.search("test quinoa")).results

    async with NutritionClient.start(str(EXTRA_FOODS)) as extended:
        found = await extended.search("test quinoa")
        assert [food.id for food in found.results] == ["test_quinoa"]
        assert (await extended.nutrition("test_quinoa", 100, "g")).nutrients.kcal == 120
        # The bundled foods are still there.
        assert (await extended.search("rajma")).results


# FakeNutrition says what the server says. A call is made to both and the refusals compared.


@pytest.fixture
def fake_chapati() -> FakeNutrition:
    return FakeNutrition(
        {"chapati": {"kcal": 300.0}},
        units={"chapati": {"roti": 40.0, "piece": 40.0, "serving": 80.0}},
    )


async def refused(port: NutritionPort, call: str, *args: object) -> NutritionError:
    with pytest.raises(NutritionError) as caught:
        await getattr(port, call)(*args)
    return caught.value


@pytest.mark.parametrize(
    ("call", "args"),
    [
        ("nutrition", ("no_such_food_xyz", 1, "g")),
        ("nutrition", ("chapati", 1, "gallon")),
        ("nutrition", ("chapati", 6000, "g")),
        ("nutrition", ("chapati", 1000, "roti")),
        ("meal", ([MealItemIn(food_id="chapati", amount=1, unit="gallon")],)),
        ("meal", ([MealItemIn(food_id="no_such_food_xyz", amount=1, unit="g")],)),
        ("meal", ([MealItemIn(food_id="chapati", amount=125.01, unit="roti")],)),
        ("search", ("!!",)),
    ],
)
async def test_fake_refuses_in_the_servers_words(
    live: NutritionClient, fake_chapati: FakeNutrition, call: str, args: tuple[object, ...]
) -> None:
    real = await refused(live, call, *args)
    fake = await refused(fake_chapati, call, *args)

    assert str(fake) == str(real)
    assert (fake.food_id, fake.valid_units) == (real.food_id, real.valid_units)
    assert fake.unreachable is real.unreachable is False


@pytest.mark.parametrize(
    ("call", "args"),
    [
        ("nutrition", ("chapati", 0, "g")),
        ("nutrition", ("chapati", -1, "roti")),
        ("meal", ([],)),
        ("search", ("x" * 101,)),
        ("search", ("roti", None, 51)),
    ],
)
async def test_fake_refuses_what_the_schema_refuses_in_the_servers_first_lines(
    live: NutritionClient, fake_chapati: FakeNutrition, call: str, args: tuple[object, ...]
) -> None:
    # The server's message goes on with a link to Pydantic's documentation, and quotes only the
    # start of a long value.
    real = await refused(live, call, *args)
    fake = await refused(fake_chapati, call, *args)

    assert str(fake).splitlines()[:2] == str(real).splitlines()[:2]
    assert (fake.food_id, fake.valid_units) == (real.food_id, real.valid_units)


async def test_fake_reads_units_and_words_the_assumption_as_the_server_does(
    live: NutritionClient, fake_chapati: FakeNutrition
) -> None:
    for amount, unit in [(2, "roti"), (1.5, " Rotis"), (3, "pieces"), (150, "grams"), (0.123, "g")]:
        real = await live.nutrition("chapati", amount, unit)
        fake = await fake_chapati.nutrition("chapati", amount, unit)

        assert (fake.grams, fake.measure_assumption) == (real.grams, real.measure_assumption)
        assert fake.food.units == real.food.units


async def test_fake_reads_an_es_plural_as_the_server_does(live: NutritionClient) -> None:
    fake = FakeNutrition({"whole_milk": {"kcal": 60.0}}, units={"whole_milk": {"glass": 257.83}})

    real = await live.nutrition("whole_milk", 2, "Glasses")
    stand_in = await fake.nutrition("whole_milk", 2, "Glasses")

    # The server multiplies the unrounded grams in a glass; the fake has the rounded ones a
    # search result lists.
    assert stand_in.grams == pytest.approx(real.grams, abs=0.02)
    # A unit the server works out from a density has a note after the grams; the fake adds none.
    assert real.measure_assumption.startswith("2 glass = 515.7 g")
    assert stand_in.measure_assumption == "2 glass = 515.7 g"


async def test_roti_is_the_name_the_server_gives_chapati(live: NutritionClient) -> None:
    # The reader's tests build their chapati the same way, so this keeps them honest.
    found = await live.search("rotis")

    assert [food.id for food in found.results] == ["chapati"]
    assert found.results[0].units == {"g": 1.0, "roti": 40.0, "piece": 40.0, "serving": 80.0}
    assert "roti" in found.results[0].aliases
