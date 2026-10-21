import csv
import json
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml
from fakes import FakeNutrition
from nutrition_mcp.models import Diet as NutritionDiet

from meal_planner.catalogue import Catalogue, CatalogueError, check_rules_against, load_catalogue
from meal_planner.config import Settings
from meal_planner.rules import PlanRules

pytestmark = pytest.mark.anyio

PLAN = Path(__file__).resolve().parent.parent / "fixtures" / "plans" / "office_weekday.json"


def entry(food_id: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": food_id,
        "name": food_id.replace("_", " ").capitalize(),
        "category": "other",
        "allergens": [],
        "pack_g": None,
        "keeps_days_opened": None,
    }
    return base | fields


def write_catalogue(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "catalogue.yaml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


def fake_nutrition(
    *food_ids: str,
    diets: Mapping[str, NutritionDiet] | None = None,
    units: Mapping[str, Mapping[str, float]] | None = None,
) -> FakeNutrition:
    return FakeNutrition(
        {food_id: {"kcal": 100.0} for food_id in food_ids}, diets=diets, units=units
    )


async def load(
    tmp_path: Path,
    entries: list[dict[str, Any]],
    nutrition: FakeNutrition | None = None,
) -> Catalogue:
    ids = [e["id"] for e in entries if "id" in e]
    return await load_catalogue(
        write_catalogue(tmp_path, entries), nutrition or fake_nutrition(*ids)
    )


def rules_with(**food: Any) -> PlanRules:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["food"] = {**plan["food"], **food}
    return PlanRules.model_validate(plan)


def food_rules(**food: Any) -> PlanRules:
    """Rules that exclude nothing unless `food` says so."""
    return rules_with(
        **{"diet": "non_veg", "exclude_allergens": [], "exclude_foods": [], "liked_foods": []}
        | food
    )


# Loading


async def test_load_takes_diet_and_units_from_nutrition(tmp_path: Path) -> None:
    nutrition = fake_nutrition("egg", diets={"egg": "egg"}, units={"egg": {"piece": 50}})

    catalogue = await load(tmp_path, [entry("egg", category="egg")], nutrition)

    item = catalogue.get("egg")
    assert item.diet == "egg"
    assert item.units == ["g", "piece"]


async def test_typo_stops_load(tmp_path: Path) -> None:
    nutrition = fake_nutrition("rolled_oats")

    with pytest.raises(CatalogueError, match="rolld_oats"):
        await load(tmp_path, [entry("rolld_oats")], nutrition)


async def test_every_unknown_id_is_named(tmp_path: Path) -> None:
    nutrition = fake_nutrition("atta")
    entries = [entry("rolld_oats"), entry("atta"), entry("paner")]

    with pytest.raises(CatalogueError) as caught:
        await load(tmp_path, entries, nutrition)

    assert "rolld_oats" in str(caught.value)
    assert "paner" in str(caught.value)
    assert "'atta'" not in str(caught.value)


async def test_bad_value_names_the_id_and_field(tmp_path: Path) -> None:
    with pytest.raises(CatalogueError) as caught:
        await load(tmp_path, [entry("atta"), entry("besan", pack_g=-500)])

    message = str(caught.value)
    assert "besan" in message
    assert "pack_g" in message


async def test_misspelt_field_names_the_id_and_field(tmp_path: Path) -> None:
    broken = entry("atta") | {"allergen": ["gluten"]}

    with pytest.raises(CatalogueError) as caught:
        await load(tmp_path, [broken])

    assert "atta" in str(caught.value)
    assert "allergen" in str(caught.value)


async def test_unknown_allergen_group_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CatalogueError) as caught:
        await load(tmp_path, [entry("atta", allergens=["wheat"])])

    assert "atta" in str(caught.value)
    assert "allergens" in str(caught.value)


async def test_entry_without_an_id_is_located_by_position(tmp_path: Path) -> None:
    nameless = {k: v for k, v in entry("atta").items() if k != "id"}

    with pytest.raises(CatalogueError, match=r"entry 2.*id"):
        await load(tmp_path, [entry("besan"), nameless], fake_nutrition("besan"))


async def test_repeated_id_stops_load(tmp_path: Path) -> None:
    with pytest.raises(CatalogueError, match="atta"):
        await load(tmp_path, [entry("atta"), entry("atta")])


async def test_measure_nutrition_mcp_already_has_stops_load(tmp_path: Path) -> None:
    # Two sources for one unit could disagree, and nutrition-mcp's would win without notice.
    nutrition = fake_nutrition("sugar", units={"sugar": {"tsp": 4.2}})

    with pytest.raises(CatalogueError, match=r"sugar.*tsp"):
        await load(tmp_path, [entry("sugar", measures={"tsp": 4.0})], nutrition)


@pytest.mark.parametrize("measures", [{"g": 1.0}, {"Piece": 50.0}, {"piece": 0}, {"piece": -50}])
async def test_unusable_measures_are_rejected(tmp_path: Path, measures: dict[str, float]) -> None:
    with pytest.raises(CatalogueError, match="measures"):
        await load(tmp_path, [entry("egg", measures=measures)])


async def test_missing_file_is_a_catalogue_error(tmp_path: Path) -> None:
    with pytest.raises(CatalogueError, match="nowhere"):
        await load_catalogue(tmp_path / "nowhere.yaml", fake_nutrition())


async def test_invalid_yaml_is_a_catalogue_error(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.yaml"
    path.write_text("- {id: atta, name: Atta\n", encoding="utf-8")

    with pytest.raises(CatalogueError, match="not valid YAML"):
        await load_catalogue(path, fake_nutrition("atta"))


async def test_a_file_that_is_not_a_list_is_a_catalogue_error(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.yaml"
    path.write_text("atta: {name: Atta}\n", encoding="utf-8")

    with pytest.raises(CatalogueError, match="list"):
        await load_catalogue(path, fake_nutrition("atta"))


# Choosing what the writer may use


async def mixed_catalogue(tmp_path: Path) -> Catalogue:
    nutrition = fake_nutrition(
        "moong_dal",
        "curd",
        "egg",
        "rohu",
        "peanuts",
        diets={
            "moong_dal": "vegan",
            "curd": "veg",
            "egg": "egg",
            "rohu": "non_veg",
            "peanuts": "vegan",
        },
    )
    entries = [
        entry("moong_dal", category="pulse"),
        entry("curd", category="dairy", allergens=["milk"]),
        entry("egg", category="egg", allergens=["egg"]),
        entry("rohu", category="fish", allergens=["fish"]),
        entry("peanuts", category="nut_seed", allergens=["peanut"]),
    ]
    return await load(tmp_path, entries, nutrition)


def ids(catalogue: Catalogue, rules: PlanRules) -> list[str]:
    return [item.id for item in catalogue.allowed_for(rules)]


async def test_allowed_for_vegan_drops_curd_and_egg(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)

    allowed = ids(catalogue, food_rules(diet="vegan"))

    assert allowed == ["moong_dal", "peanuts"]


@pytest.mark.parametrize(
    ("diet", "expected"),
    [
        ("vegan", ["moong_dal", "peanuts"]),
        ("veg", ["moong_dal", "curd", "peanuts"]),
        ("egg", ["moong_dal", "curd", "egg", "peanuts"]),
        ("non_veg", ["moong_dal", "curd", "egg", "rohu", "peanuts"]),
    ],
)
async def test_each_diet_allows_its_own_foods_and_the_stricter_ones(
    tmp_path: Path, diet: str, expected: list[str]
) -> None:
    catalogue = await mixed_catalogue(tmp_path)

    assert ids(catalogue, food_rules(diet=diet)) == expected


async def test_allowed_for_drops_peanut_allergen(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)

    allowed = ids(catalogue, food_rules(exclude_allergens=["peanut"]))

    assert allowed == ["moong_dal", "curd", "egg", "rohu"]


async def test_allowed_for_drops_a_food_with_any_excluded_allergen(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)

    allowed = ids(catalogue, food_rules(exclude_allergens=["milk", "fish"]))

    assert allowed == ["moong_dal", "egg", "peanuts"]


async def test_allowed_for_drops_excluded_foods(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)

    allowed = ids(catalogue, food_rules(exclude_foods=["rohu", "curd"]))

    assert allowed == ["moong_dal", "egg", "peanuts"]


async def test_allowed_for_applies_every_rule_at_once(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)
    rules = food_rules(diet="egg", exclude_allergens=["egg"], exclude_foods=["curd"])

    assert ids(catalogue, rules) == ["moong_dal", "peanuts"]


async def test_get_unknown_id_names_it(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)

    with pytest.raises(CatalogueError, match="chicken"):
        catalogue.get("chicken")


# Household measures


async def two_eggs_catalogue(tmp_path: Path) -> Catalogue:
    entries = [
        entry("egg", category="egg", measures={"piece": 50}),
        entry("moong_dal", category="pulse"),
    ]
    return await load(tmp_path, entries)


async def test_grams_two_eggs(tmp_path: Path) -> None:
    catalogue = await two_eggs_catalogue(tmp_path)

    assert catalogue.grams("egg", 2, "piece") == 100.0


@pytest.mark.parametrize("unit", ["Piece", " piece ", "pieces"])
async def test_grams_reads_units_the_way_nutrition_mcp_does(tmp_path: Path, unit: str) -> None:
    catalogue = await two_eggs_catalogue(tmp_path)

    assert catalogue.grams("egg", 3, unit) == 150.0


async def test_grams_is_none_for_a_unit_the_catalogue_lacks(tmp_path: Path) -> None:
    catalogue = await two_eggs_catalogue(tmp_path)

    assert catalogue.grams("egg", 1, "katori") is None
    assert catalogue.grams("moong_dal", 1, "piece") is None


async def test_grams_for_an_unknown_id_names_it(tmp_path: Path) -> None:
    catalogue = await two_eggs_catalogue(tmp_path)

    with pytest.raises(CatalogueError, match="duck_egg"):
        catalogue.grams("duck_egg", 1, "piece")


# Rules against the catalogue


async def test_rules_with_unknown_liked_food_reported(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)
    rules = food_rules(liked_foods=["moong_dal", "tofoo"])

    problems = check_rules_against(rules, catalogue)

    assert len(problems) == 1
    assert "tofoo" in problems[0]
    assert "liked_foods" in problems[0]


async def test_rules_with_unknown_excluded_food_reported(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)
    rules = food_rules(exclude_foods=["rohu", "brinjall"])

    problems = check_rules_against(rules, catalogue)

    assert len(problems) == 1
    assert "brinjall" in problems[0]
    assert "exclude_foods" in problems[0]


async def test_each_unknown_id_gets_its_own_message(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)
    rules = food_rules(exclude_foods=["brinjall"], liked_foods=["tofoo", "paner"])

    problems = check_rules_against(rules, catalogue)

    assert len(problems) == 3
    assert "brinjall" in problems[0]
    assert "tofoo" in problems[1]
    assert "paner" in problems[2]


async def test_rules_naming_only_known_foods_report_nothing(tmp_path: Path) -> None:
    catalogue = await mixed_catalogue(tmp_path)
    rules = food_rules(exclude_foods=["rohu"], liked_foods=["moong_dal"])

    assert check_rules_against(rules, catalogue) == []


# The shipped file


def bundled_ingredient_ids() -> set[str]:
    path = files("nutrition_mcp") / "data" / "bundled" / "ingredients.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["id"] for row in csv.DictReader(handle)}


# Baking soda is left out of the catalogue on purpose. Anything else nutrition-mcp adds in a
# later version needs a catalogue row, and this test says so.
LEFT_OUT_ON_PURPOSE = {"baking_soda"}


def test_shipped_catalogue_covers_nutrition_mcps_bundled_ingredients() -> None:
    path = Settings().catalogue_path
    listed = {row["id"] for row in yaml.safe_load(path.read_text(encoding="utf-8"))}

    assert bundled_ingredient_ids() - LEFT_OUT_ON_PURPOSE <= listed
    assert not LEFT_OUT_ON_PURPOSE & listed
