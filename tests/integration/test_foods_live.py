"""The food search and meal compute routes against the real nutrition-mcp server and the shipped
catalogue. Nothing here needs a key or the network, because neither route calls a model.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from nutrition_mcp.schemas import MealResult
from week_helpers import model_for

from meal_planner.api import create_app
from meal_planner.config import Settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(Settings(rate_limit_per_minute=1000), model=model_for())
    with TestClient(app) as found:
        yield found


def test_a_search_for_dal_returns_food_ids_with_units(client: TestClient) -> None:
    response = client.get("/v1/foods/search", params={"q": "dal"})

    assert response.status_code == 200
    foods = response.json()
    assert 1 <= len(foods) <= 5
    assert "dal_tadka" in [food["id"] for food in foods]
    for food in foods:
        assert set(food) == {"id", "name", "kind", "units"}
        assert food["kind"] in ("dish", "ingredient")
        assert food["units"][0] == "g"


def test_the_units_of_a_search_are_the_servers_then_the_catalogues(client: TestClient) -> None:
    """The server has the egg in grams only; the catalogue's piece is offered after them."""
    response = client.get("/v1/foods/search", params={"q": "egg", "limit": 3})

    units = {food["id"]: food["units"] for food in response.json()}
    assert units["egg"] == ["g", "piece"]
    assert units["dimer_dalna"] == ["g", "katori", "serving"]


def test_a_food_the_data_lacks_is_an_empty_list(client: TestClient) -> None:
    response = client.get("/v1/foods/search", params={"q": "pulao"})

    assert (response.status_code, response.json()) == (200, [])


def test_two_pieces_of_chapati_come_back_as_grams_and_kcal(client: TestClient) -> None:
    items = [{"food_id": "chapati", "amount": 2, "unit": "piece"}]

    response = client.post("/v1/log/compute", json={"items": items})

    assert response.status_code == 200
    meal = MealResult.model_validate(response.json())
    (chapati,) = meal.items
    assert (chapati.food.id, chapati.grams) == ("chapati", 80)
    assert chapati.nutrients.kcal is not None and chapati.nutrients.kcal > 0
    assert meal.total.kcal == chapati.nutrients.kcal


def test_a_catalogue_measure_is_read_as_the_summary_reads_it(client: TestClient) -> None:
    items = [
        {"food_id": "egg", "amount": 2, "unit": "piece"},
        {"food_id": "chicken_breast", "amount": 0.2, "unit": "kg"},
    ]

    response = client.post("/v1/log/compute", json={"items": items})

    meal = MealResult.model_validate(response.json())
    assert [item.grams for item in meal.items] == [100, 200]
    # What nutrition-mcp was asked about: the piece became grams, and so did the kilograms.
    assert [(item.amount, item.unit) for item in meal.items] == [(100, "g"), (200, "g")]


def test_the_servers_refusals_name_the_item_and_the_units_that_work(client: TestClient) -> None:
    gallon = {"food_id": "chapati", "amount": 2, "unit": "gallon"}
    ghost = {"food_id": "nonesuch", "amount": 1, "unit": "g"}
    egg = {"food_id": "egg", "amount": 1, "unit": "piece"}

    unit = client.post("/v1/log/compute", json={"items": [egg, gallon]})
    food = client.post("/v1/log/compute", json={"items": [egg, ghost]})

    assert unit.status_code == food.status_code == 422
    (problem,) = unit.json()["detail"]
    assert problem["loc"] == ["body", "items", 1]
    assert problem["msg"].endswith("valid units: g, roti, piece, serving")
    (problem,) = food.json()["detail"]
    assert problem["loc"] == ["body", "items", 1]
    assert problem["msg"].startswith("item 2 (nonesuch): unknown food id 'nonesuch'")


def test_a_food_id_with_a_bracket_is_a_422_naming_the_item(client: TestClient) -> None:
    """The id is the sender's own and can hold any character; the real server's message quotes
    it as it is."""
    egg = {"food_id": "egg", "amount": 1, "unit": "piece"}
    bracketed = {"food_id": "a) b", "amount": 1, "unit": "g"}

    response = client.post("/v1/log/compute", json={"items": [egg, bracketed]})

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "items", 1]
    assert problem["msg"].startswith("item 2 (a) b): unknown food id 'a) b'")
