"""GET /v1/foods/search: the foods nutrition-mcp has under a name, for the screen that changes a
wrongly matched food.

The app is the one the reader tests use: nutrition-mcp holds the foods in reader_helpers, and the
catalogue gives the egg a `piece` that nutrition-mcp lacks. No test scripts a model, because the
route calls none.
"""

import logging
from pathlib import Path
from typing import Literal

import pytest
from app_helpers import MARK, assert_no_marker, log_entries, serving_foods
from nutrition_mcp.schemas import SearchResult
from reader_helpers import ReaderNutrition

from meal_planner.llm import FakeModel
from meal_planner.nutrition import NutritionError

SEARCH = "/v1/foods/search"


def searched(nutrition: ReaderNutrition) -> list[str]:
    """What nutrition-mcp was asked to search for since the app started. Starting asks it about
    the catalogue's foods, which is not a search."""
    return [what for how, what in nutrition.asked if how == "search"]


def test_a_name_comes_back_as_foods_with_the_units_an_amount_can_use(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.get(SEARCH, params={"q": "roti"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == [
        {
            "id": "chapati",
            "name": "Chapati",
            "kind": "dish",
            "units": ["g", "roti", "piece", "serving"],
        }
    ]


def test_the_units_are_the_ones_the_reader_lists_nutrition_mcps_then_the_catalogues(
    tmp_path: Path,
) -> None:
    """nutrition-mcp has the egg in grams only. The catalogue's piece works, so it is offered."""
    with serving_foods(tmp_path) as client:
        egg = client.get(SEARCH, params={"q": "egg"}).json()
        pav = client.get(SEARCH, params={"q": "pav"}).json()

    assert [(f["id"], f["kind"], f["units"]) for f in egg] == [
        ("egg", "ingredient", ["g", "piece"]),
        ("dimer_dalna", "dish", ["g", "katori", "serving"]),
    ]
    # In the catalogue with no measure, and a unit of its own: nothing is added.
    assert [(f["id"], f["units"]) for f in pav] == [("pav", ["g"])]


def test_the_results_are_nutrition_mcps_in_its_order(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.get(SEARCH, params={"q": "dal"})

    assert [f["id"] for f in response.json()] == ["dal_tadka", "toor_dal", "moong_dal"]


def test_a_name_nutrition_mcp_lacks_is_an_empty_list_not_a_guess(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.get(SEARCH, params={"q": "pulao"})

    assert response.status_code == 200
    assert response.json() == []


def test_the_search_asks_nutrition_mcp_for_five_foods_unless_told_otherwise(
    tmp_path: Path,
) -> None:
    nutrition = AskedForLimit()

    with serving_foods(tmp_path, nutrition) as client:
        default = client.get(SEARCH, params={"q": "dal"})
        two = client.get(SEARCH, params={"q": "dal", "limit": 2})
        most = client.get(SEARCH, params={"q": "dal", "limit": 50})

    assert [len(r.json()) for r in (default, two, most)] == [3, 2, 3]
    assert nutrition.limits == [5, 2, 50]


class AskedForLimit(ReaderNutrition):
    def __init__(self) -> None:
        super().__init__()
        self.limits: list[int] = []

    async def search(
        self, query: str, kind: Literal["dish", "ingredient"] | None = None, limit: int = 5
    ) -> SearchResult:
        self.limits.append(limit)
        return await super().search(query, kind, limit)


@pytest.mark.parametrize("limit", ["0", "-1", "51", "many", "2.5", ""])
def test_a_limit_outside_one_to_fifty_is_422_naming_the_limit(tmp_path: Path, limit: str) -> None:
    nutrition = ReaderNutrition()

    with serving_foods(tmp_path, nutrition) as client:
        response = client.get(SEARCH, params={"q": "dal", "limit": limit})

    assert response.status_code == 422
    assert [p["loc"] for p in response.json()["detail"]] == [["query", "limit"]]
    assert searched(nutrition) == []


def test_the_search_sees_the_text_without_the_spaces_around_it(tmp_path: Path) -> None:
    nutrition = ReaderNutrition()

    with serving_foods(tmp_path, nutrition) as client:
        response = client.get(SEARCH, params={"q": "  roti \n"})

    assert response.status_code == 200
    assert searched(nutrition) == ["roti"]


# A query that cannot be a food name is refused before nutrition-mcp is asked


@pytest.mark.parametrize(
    ("params", "said"),
    [
        ({}, "Field required"),
        ({"q": ""}, "type a food name: the text is blank"),
        ({"q": "   \n\t"}, "type a food name: the text is blank"),
        ({"q": "?! -- ()"}, "type a food name: the text has no letter or digit"),
        ({"q": "x" * 101}, "the text has 101 characters and the most is 100"),
    ],
)
def test_a_blank_unsearchable_or_long_query_is_422_without_asking_nutrition_mcp(
    tmp_path: Path, params: dict[str, str], said: str
) -> None:
    nutrition = ReaderNutrition()

    with serving_foods(tmp_path, nutrition) as client:
        response = client.get(SEARCH, params=params)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem == {"loc": ["query", "q"], "msg": said}
    assert searched(nutrition) == []


def test_a_query_of_the_longest_length_is_searched_and_spaces_around_it_are_not_counted(
    tmp_path: Path,
) -> None:
    nutrition = ReaderNutrition()

    with serving_foods(tmp_path, nutrition) as client:
        response = client.get(SEARCH, params={"q": " " + "x" * 100 + "  "})

    assert response.status_code == 200
    assert searched(nutrition) == ["x" * 100]


def test_a_query_over_the_limit_does_not_come_back_in_the_422(tmp_path: Path) -> None:
    with serving_foods(tmp_path) as client:
        response = client.get(SEARCH, params={"q": MARK * 20})

    assert response.status_code == 422
    assert MARK not in response.text


# The limits every /v1 route has


def test_the_search_route_is_rate_limited(tmp_path: Path) -> None:
    with serving_foods(tmp_path, rate_limit_per_minute=1) as client:
        first = client.get(SEARCH, params={"q": "roti"})
        second = client.get(SEARCH, params={"q": "roti"})

    assert (first.status_code, second.status_code) == (200, 429)
    assert 1 <= int(second.headers["retry-after"]) <= 60


def test_the_search_calls_no_model(tmp_path: Path) -> None:
    model = FakeModel({})

    with serving_foods(tmp_path, model=model) as client:
        client.get(SEARCH, params={"q": "roti"})
        client.get(SEARCH, params={"q": "pulao"})

    assert model.calls == []


# nutrition-mcp failing


class SearchDown(ReaderNutrition):
    """nutrition-mcp gone once the app has started: every search fails as unreachable."""

    async def search(
        self, query: str, kind: Literal["dish", "ingredient"] | None = None, limit: int = 5
    ) -> SearchResult:
        raise NutritionError("nutrition-mcp could not answer search_food", unreachable=True)


class SearchUnreadable(ReaderNutrition):
    async def search(
        self, query: str, kind: Literal["dish", "ingredient"] | None = None, limit: int = 5
    ) -> SearchResult:
        raise NutritionError("nutrition-mcp's result for search_food could not be read")


def test_nutrition_mcp_being_down_is_a_503_naming_the_step(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with serving_foods(tmp_path, SearchDown()) as client:
        response = client.get(SEARCH, params={"q": "roti"})

    assert response.status_code == 503
    assert response.json() == {"detail": "nutrition-mcp could not be reached (step: nutrition)"}
    (failed,) = [
        e for e in log_entries(capfd.readouterr().err) if e["event"] == "food_search_failed"
    ]
    assert (failed["task"], failed["error_type"]) == ("food_search", "NutritionUnusable")
    assert failed["request_id"] == response.headers["x-request-id"]


def test_an_answer_that_cannot_be_read_is_a_502_naming_the_step(tmp_path: Path) -> None:
    with serving_foods(tmp_path, SearchUnreadable()) as client:
        response = client.get(SEARCH, params={"q": "roti"})

    assert response.status_code == 502
    assert response.json() == {
        "detail": "nutrition-mcp sent an answer that could not be used (step: nutrition)"
    }


# Typed text stays out of the logs


class TextEchoed(ReaderNutrition):
    """nutrition-mcp breaking its contract: a plain exception with the text in it."""

    async def search(
        self, query: str, kind: Literal["dish", "ingredient"] | None = None, limit: int = 5
    ) -> SearchResult:
        raise RuntimeError(f"stand-in echoed the request: {query}")


class DownWithText(ReaderNutrition):
    async def search(
        self, query: str, kind: Literal["dish", "ingredient"] | None = None, limit: int = 5
    ) -> SearchResult:
        raise NutritionError(f"nutrition-mcp could not answer {query}", unreachable=True)


def app_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    """The records the app wrote. The test client logs each request's URL, and the query string
    is part of it; that is the test's own line, not the app's."""
    return [record for record in records if not record.name.startswith("httpx")]


@pytest.mark.parametrize(
    ("nutrition", "status"), [(ReaderNutrition, 200), (DownWithText, 503), (TextEchoed, 500)]
)
def test_no_typed_text_in_logs(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
    nutrition: type[ReaderNutrition],
    status: int,
) -> None:
    with serving_foods(tmp_path, nutrition()) as client:
        response = client.get(SEARCH, params={"q": f"roti {MARK}"})

    assert response.status_code == status
    assert MARK not in response.text
    kept = app_records(records)
    assert kept
    assert_no_marker(kept, capfd.readouterr())


def test_no_typed_text_in_logs_when_the_query_is_rejected(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    with serving_foods(tmp_path) as client:
        too_long = client.get(SEARCH, params={"q": MARK * 20})
        no_letters = client.get(SEARCH, params={"q": "?!" + "!" * 10})
        bad_limit = client.get(SEARCH, params={"q": MARK, "limit": MARK})

    assert [r.status_code for r in (too_long, no_letters, bad_limit)] == [422, 422, 422]
    assert MARK not in too_long.text + bad_limit.text
    assert_no_marker(app_records(records), capfd.readouterr())
