"""scripts/build_foods.py, offline. The FDC records under tests/fixtures/usda/ are real records
(public domain) trimmed to the fields the build reads.
"""

import csv
import json
from pathlib import Path
from typing import Any

import build_foods
import httpx
import pytest
import yaml
from build_foods import BuildError, Paths
from nutrition_mcp.loader import bundled_dir, load_dataset
from nutrition_mcp.source_mapping import usda_to_nutrients

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "usda"
OATS = "173904"
OLIVE_OIL = "171413"
SOURCES = [
    {
        "id": "usda_fdc_sr_legacy",
        "name": "USDA FoodData Central, SR Legacy",
        "url": "https://fdc.nal.usda.gov",
        "license": "Public domain",
        "version": "SR Legacy 2018-04",
        "retrieved": "2026-10-23",
        "attribution": "U.S. Department of Agriculture, Agricultural Research Service.",
    }
]


def record(fdc_id: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{fdc_id}.json").read_text(encoding="utf-8"))
    return data


def map_row(food_id: str, fdc_id: str, diet: str = "vegan") -> dict[str, str]:
    return {
        "id": food_id,
        "name": food_id.replace("_", " ").capitalize(),
        "aliases": "",
        "diet": diet,
        "fdc_id": fdc_id,
        "notes": "",
    }


def without_nutrient(food: dict[str, Any], nutrient_id: int) -> dict[str, Any]:
    kept = [n for n in food["foodNutrients"] if n["nutrient"]["id"] != nutrient_id]
    return food | {"foodNutrients": kept}


# Mapping one record


def test_build_row_uses_nutrition_mcp_mapping() -> None:
    food = record(OATS)

    built = build_foods.build_row(map_row("rolled_oats", OATS), food)

    carbs = built.ingredient.per_100g.carbs_available_g
    assert carbs == usda_to_nutrients(food).carbs_available_g
    # Carbohydrate by difference (67.7 g) less fibre (10.1 g): available carbohydrate.
    assert carbs == pytest.approx(57.6)
    assert built.ingredient.per_100g == usda_to_nutrients(food)
    assert built.ingredient.source_record == OATS


def test_density_is_set_for_liquids_only() -> None:
    food = record(OLIVE_OIL)

    liquid = build_foods.build_row(map_row("olive_oil", OLIVE_OIL), food)
    solid = build_foods.build_row(map_row("not_a_liquid", OLIVE_OIL), food)

    # The record's cup portion: 216 g in 236.588 ml.
    assert liquid.ingredient.density_g_per_ml == pytest.approx(216 / 236.588)
    assert solid.ingredient.density_g_per_ml is None


def test_source_id_follows_the_records_data_type() -> None:
    built = build_foods.build_row(map_row("rolled_oats", OATS), record(OATS))

    assert built.ingredient.source_id == "usda_fdc_sr_legacy"


def test_a_branded_record_stops_the_build() -> None:
    branded = record(OATS) | {"dataType": "Branded"}

    with pytest.raises(BuildError, match=r"rolled_oats.*Branded"):
        build_foods.build_row(map_row("rolled_oats", OATS), branded)


def test_a_value_the_record_lacks_is_written_as_an_empty_cell(tmp_path: Path) -> None:
    no_fibre = without_nutrient(record(OATS), 1079)
    built = [build_foods.build_row(map_row("rolled_oats", OATS), no_fibre)]
    (tmp_path / "dishes").mkdir()
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump(SOURCES), encoding="utf-8")

    build_foods.write_ingredients(built, tmp_path / "ingredients.csv")

    with (tmp_path / "ingredients.csv").open(encoding="utf-8", newline="") as handle:
        (row,) = list(csv.DictReader(handle))
    # Without fibre there is no available carbohydrate either; neither is stored as 0.
    assert row["fibre_g"] == ""
    assert row["carbs_available_g"] == ""
    assert row["kcal"] == "379"
    _, (ingredient,), _ = load_dataset(tmp_path)
    assert ingredient.per_100g.fibre_g is None


@pytest.mark.parametrize(
    ("value", "text"),
    [(None, ""), (0.0, "0"), (57.599999, "57.6"), (328.0, "328"), (-0.0, "0")],
)
def test_format_number(value: float | None, text: str) -> None:
    assert build_foods.format_number(value) == text


# The map


def bundled_ids() -> set[str]:
    with (bundled_dir() / "ingredients.csv").open(encoding="utf-8", newline="") as handle:
        return {row["id"] for row in csv.DictReader(handle)}


def test_ids_do_not_clash_with_bundled() -> None:
    ids = {row["id"] for row in build_foods.read_map(build_foods.PATHS.food_map)}

    assert ids
    assert not ids & bundled_ids()


def test_a_clash_with_a_bundled_id_stops_the_build() -> None:
    with pytest.raises(BuildError, match="'egg'"):
        build_foods.check_no_clash([map_row("egg", "1"), map_row("tofu", "2")], {"egg"})


def write_map(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=build_foods.MAP_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_map_with_a_repeated_id_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "food_map.csv"
    write_map(path, [map_row("tofu", "1"), map_row("tofu", "2")])

    with pytest.raises(BuildError, match="tofu"):
        build_foods.read_map(path)


def test_map_with_a_bad_fdc_id_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "food_map.csv"
    write_map(path, [map_row("tofu", "17244 8")])

    with pytest.raises(BuildError, match="fdc_id"):
        build_foods.read_map(path)


def test_map_with_another_header_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "food_map.csv"
    path.write_text("id,name,record\ntofu,Tofu,172448\n", encoding="utf-8")

    with pytest.raises(BuildError, match="header"):
        build_foods.read_map(path)


# Portions


@pytest.mark.parametrize(
    ("portion", "text"),
    [
        # SR Legacy puts the unit, and any form, in the modifier.
        (
            {"amount": 1.0, "modifier": "cup, sliced", "measureUnit": {"name": "undetermined"}},
            "1 cup, sliced",
        ),
        ({"amount": 0.33, "modifier": "cup", "measureUnit": {"name": "undetermined"}}, "0.33 cup"),
        # Foundation names the unit in measureUnit.
        ({"amount": 1.0, "modifier": "chopped", "measureUnit": {"name": "cup"}}, "1 cup, chopped"),
        ({"amount": 1.0, "measureUnit": {"name": "RACC"}}, "1 RACC"),
        # Survey (FNDDS) writes a code in the modifier and the words in portionDescription.
        (
            {
                "modifier": "64452",
                "portionDescription": "1 scoop, NFS",
                "measureUnit": {"name": "undetermined"},
            },
            "1 scoop, NFS",
        ),
    ],
)
def test_portion_text_reads_each_dataset_layout(portion: dict[str, Any], text: str) -> None:
    assert build_foods.portion_text(portion) == text


def test_portion_rows_list_every_portion_of_the_record() -> None:
    rows = build_foods.portion_rows("rolled_oats", "food_file", record(OATS))

    assert rows == [
        {
            "id": "rolled_oats",
            "role": "food_file",
            "fdc_id": OATS,
            "data_type": "SR Legacy",
            "record": "Cereals, oats, regular and quick, not fortified, dry",
            "portion": "1 cup",
            "gram_weight": "81",
        },
        {
            "id": "rolled_oats",
            "role": "food_file",
            "fdc_id": OATS,
            "data_type": "SR Legacy",
            "record": "Cereals, oats, regular and quick, not fortified, dry",
            "portion": "0.33 cup",
            "gram_weight": "27",
        },
    ]


def test_a_record_without_portions_still_gets_a_review_row() -> None:
    rows = build_foods.portion_rows("rolled_oats", "food_file", record(OATS) | {"foodPortions": []})

    assert [(r["portion"], r["gram_weight"]) for r in rows] == [("none listed", "")]


def test_measured_bundled_rows_resolve_to_their_fdc_records(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    catalogue.write_text(
        yaml.safe_dump(
            [
                {"id": "egg", "measures": {"piece": 50}},
                {"id": "potato", "measures": {"piece": 150}},
                {"id": "white_rice"},
                {"id": "rolled_oats", "measures": {"cup": 81}},
            ]
        ),
        encoding="utf-8",
    )

    records, without = build_foods.measured_bundled_records(catalogue)

    # Potato is a CoFID row, so it has no FDC portions; rolled oats is not a bundled row.
    assert records == {"egg": "171287"}
    assert without == ["potato"]


# Fetching


class NoNetwork(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request to {request.url}")


def cache_fixture(cache: Path, fdc_id: str) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{fdc_id}.json").write_text(
        (FIXTURES / f"{fdc_id}.json").read_text(encoding="utf-8"), encoding="utf-8"
    )


def test_fetch_reads_the_cache_without_calling_the_api(tmp_path: Path) -> None:
    cache_fixture(tmp_path, OATS)

    with httpx.Client(transport=NoNetwork()) as client:
        food = build_foods.fetch_usda(client, OATS, tmp_path, refresh=False)

    assert food["description"].startswith("Cereals, oats")


def test_fetch_sends_the_key_in_a_header_and_caches_the_record(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=record(OATS))

    headers = {"X-Api-Key": "test-key"}
    with httpx.Client(transport=httpx.MockTransport(answer), headers=headers) as client:
        build_foods.fetch_usda(client, OATS, tmp_path, refresh=False)

    (request,) = seen
    assert request.url.path == f"/fdc/v1/food/{OATS}"
    # A key in the URL would end up in any error message that prints it.
    assert "test-key" not in str(request.url)
    assert request.headers["X-Api-Key"] == "test-key"
    assert json.loads((tmp_path / f"{OATS}.json").read_text(encoding="utf-8"))["fdcId"] == 173904


def test_a_cached_record_for_another_id_stops_the_build(tmp_path: Path) -> None:
    (tmp_path / "999.json").write_text(json.dumps(record(OATS)), encoding="utf-8")

    with (
        httpx.Client(transport=NoNetwork()) as client,
        pytest.raises(BuildError, match="173904"),
    ):
        build_foods.fetch_usda(client, "999", tmp_path, refresh=False)


# The whole build


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    """A repository layout in tmp_path with oats and olive oil mapped and cached."""
    paths = Paths.under(tmp_path)
    write_map(
        paths.food_map,
        [map_row("rolled_oats", OATS), map_row("olive_oil", OLIVE_OIL)],
    )
    paths.catalogue.parent.mkdir(parents=True)
    paths.catalogue.write_text(yaml.safe_dump([{"id": "rolled_oats"}]), encoding="utf-8")
    (paths.foods_dir / "dishes").mkdir(parents=True)
    (paths.foods_dir / "sources.yaml").write_text(yaml.safe_dump(SOURCES), encoding="utf-8")
    cache_fixture(paths.cache, OATS)
    cache_fixture(paths.cache, OLIVE_OIL)
    monkeypatch.setattr(build_foods, "PATHS", paths)
    monkeypatch.setattr(build_foods, "EXTRA_PORTION_RECORDS", {})
    monkeypatch.setattr(build_foods, "LIQUIDS", frozenset({"olive_oil"}))
    monkeypatch.delenv("USDA_FDC_API_KEY", raising=False)
    return paths


def test_main_rebuilds_from_the_cache_without_a_key(repo: Paths) -> None:
    assert build_foods.main([]) == 0

    _, ingredients, _ = load_dataset(repo.foods_dir)
    assert [i.id for i in ingredients] == ["rolled_oats", "olive_oil"]
    with repo.portions.open(encoding="utf-8", newline="") as handle:
        portions = list(csv.DictReader(handle))
    assert {p["id"] for p in portions} == {"rolled_oats", "olive_oil"}
    assert len(portions) == len(record(OATS)["foodPortions"]) + len(
        record(OLIVE_OIL)["foodPortions"]
    )


def test_main_needs_the_key_for_a_record_not_cached(
    repo: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo.cache / f"{OLIVE_OIL}.json").unlink()

    assert build_foods.main([]) == 1

    assert "USDA_FDC_API_KEY" in capsys.readouterr().err
    assert not (repo.foods_dir / "ingredients.csv").exists()


def test_a_liquid_missing_from_the_map_stops_the_build(
    repo: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_foods, "LIQUIDS", frozenset({"olive_oil", "olve_oil"}))

    with pytest.raises(BuildError, match="olve_oil"):
        build_foods.main([])


def test_a_source_missing_from_sources_yaml_stops_the_build(repo: Paths) -> None:
    (repo.foods_dir / "sources.yaml").write_text("[]\n", encoding="utf-8")

    with pytest.raises(BuildError, match="usda_fdc_sr_legacy"):
        build_foods.main([])
