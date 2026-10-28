"""The HTTP API as a whole: health, settings, request ids, the catalogue route and CORS.

Each route has a file of its own (test_api_week, test_api_replace, test_api_read and
test_api_summary). All of them drive the app through Starlette's test client with a scripted model
and fake services (app_helpers)."""

from pathlib import Path

import pytest
import yaml
from app_helpers import (
    EXTRA_FOODS,
    serving,
)
from fakes import FakeNutrition
from fastapi import FastAPI
from fastapi.testclient import TestClient
from week_helpers import (
    SpyNutrition,
    model_for,
)

from meal_planner.api import app_factory, create_app
from meal_planner.config import Settings
from meal_planner.startup import StartupError

# The simple routes


def test_healthz_says_ok(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_app_factory_reads_the_settings_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEAL_PLANNER_RATE_LIMIT_PER_MINUTE", "7")
    monkeypatch.setenv("MEAL_PLANNER_CATALOGUE_PATH", str(tmp_path / "missing.yaml"))

    app = app_factory()

    assert app.state.settings.rate_limit_per_minute == 7
    assert app.state.settings.catalogue_path == tmp_path / "missing.yaml"


def test_every_response_carries_a_request_id(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        ids = {client.get("/healthz").headers["x-request-id"] for _ in range(3)}

    assert len(ids) == 3


def test_the_app_does_not_start_when_the_catalogue_cannot_be_loaded(tmp_path: Path) -> None:
    app = create_app(
        Settings(catalogue_path=tmp_path / "missing.yaml"),
        model=model_for(),
        nutrition=SpyNutrition(*EXTRA_FOODS),
    )

    with pytest.raises(StartupError, match="catalogue"), TestClient(app):
        pass


# The catalogue route


def mixed_app(folder: Path) -> FastAPI:
    rows = [
        ("moong_dal", "pulse", [], "vegan"),
        ("curd", "dairy", ["milk"], "veg"),
        ("wheat_flour", "grain", ["gluten"], "vegan"),
        ("egg", "egg", ["egg"], "egg"),
        ("rohu", "fish", ["fish"], "non_veg"),
        ("peanuts", "nut_seed", ["peanut"], "vegan"),
    ]
    entries = [
        {
            "id": food_id,
            "name": food_id.replace("_", " ").capitalize(),
            "category": category,
            "allergens": allergens,
            "pack_g": None,
            "keeps_days_opened": None,
        }
        for food_id, category, allergens, _ in rows
    ]
    path = folder / "mixed.yaml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    nutrition = FakeNutrition(
        {food_id: {"kcal": 100.0} for food_id, *_ in rows},
        diets={food_id: diet for food_id, _, _, diet in rows},  # type: ignore[misc]
    )
    return create_app(
        Settings(catalogue_path=path, rate_limit_per_minute=1000),
        model=model_for(),
        nutrition=nutrition,
    )


def test_catalogue_filtered_for_vegan(tmp_path: Path) -> None:
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get("/v1/catalogue?diet=vegan")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "moong_dal", "name": "Moong dal", "category": "pulse"},
        {"id": "wheat_flour", "name": "Wheat flour", "category": "grain"},
        {"id": "peanuts", "name": "Peanuts", "category": "nut_seed"},
    ]


def test_catalogue_leaves_out_every_allergen_named(tmp_path: Path) -> None:
    query = "diet=vegan&exclude_allergens=gluten&exclude_allergens=peanut"
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get(f"/v1/catalogue?{query}")

    assert [row["id"] for row in response.json()] == ["moong_dal"]


def test_catalogue_without_a_filter_lists_everything_in_catalogue_order(tmp_path: Path) -> None:
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get("/v1/catalogue")

    ids = [row["id"] for row in response.json()]
    assert ids == ["moong_dal", "curd", "wheat_flour", "egg", "rohu", "peanuts"]


def test_catalogue_rows_hold_only_id_name_and_category(tmp_path: Path) -> None:
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get("/v1/catalogue?diet=egg")

    assert all(set(row) == {"id", "name", "category"} for row in response.json())


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("diet=pescatarian", "diet"),
        ("diet=vegan&exclude_allergens=kryptonite", "exclude_allergens"),
        ("exclude_allergens=milk&exclude_allergens=kryptonite", "exclude_allergens"),
    ],
)
def test_catalogue_with_an_unknown_diet_or_allergen_is_422(
    tmp_path: Path, query: str, field: str
) -> None:
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get(f"/v1/catalogue?{query}")

    assert response.status_code == 422
    (problem, *_) = response.json()["detail"]
    assert field in problem["loc"]
    assert set(problem) == {"loc", "msg"}


# CORS


def test_no_origins_means_no_cors_headers(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.get("/healthz", headers={"Origin": "http://localhost:5173"})

    assert "access-control-allow-origin" not in response.headers


def test_a_listed_origin_is_allowed_and_can_read_the_request_id(tmp_path: Path) -> None:
    with serving(tmp_path, cors_origins=["http://localhost:5173"]) as client:
        response = client.get("/healthz", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-request-id" in exposed
    assert "retry-after" in exposed


def test_an_unlisted_origin_gets_no_allow_header(tmp_path: Path) -> None:
    with serving(tmp_path, cors_origins=["http://localhost:5173"]) as client:
        response = client.get("/healthz", headers={"Origin": "https://elsewhere.example"})

    assert "access-control-allow-origin" not in response.headers


def test_a_preflight_for_the_week_route_is_answered(tmp_path: Path) -> None:
    with serving(tmp_path, cors_origins=["http://localhost:5173"]) as client:
        response = client.options(
            "/v1/week",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "POST" in response.headers["access-control-allow-methods"]
