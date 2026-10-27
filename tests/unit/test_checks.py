import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import time
from pathlib import Path
from typing import Any

import pytest
from nutrition_mcp.models import Diet, Nutrients
from nutrition_mcp.nutrients import NUTRIENT_KEYS
from nutrition_mcp.nutrition import sum_nutrients
from nutrition_mcp.schemas import FoodSummary, MealResult, NutritionResult

from meal_planner.catalogue import Catalogue, CatalogueItem
from meal_planner.checks import (
    LABELS,
    add_up_meals,
    check_catalogue,
    check_day,
    check_day_totals,
    check_gear_and_time,
    check_model_figures,
    check_no_waste,
    check_slot_targets,
    check_variety,
    sat_fat_ceiling,
    worst_recipe_for_day,
)
from meal_planner.config import Settings
from meal_planner.recipes import CheckedRecipe, DayReport, DraftIngredient, DraftRecipe, Failure
from meal_planner.rules import (
    CORE_TARGET_FIELDS,
    DayKey,
    DayType,
    NutrientTarget,
    PlanRules,
    Slot,
    SlotSpec,
)

PLAN = Path(__file__).resolve().parent.parent / "fixtures" / "plans" / "office_weekday.json"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in [n for n in os.environ if n.startswith("MEAL_PLANNER_")]:
        monkeypatch.delenv(name)
    yield


# Builders


def settings(**changes: Any) -> Settings:
    return Settings(**changes)


def plan_rules(
    *, gear: list[str] | None = None, shopping: list[str] | None = None, **food: Any
) -> PlanRules:
    """The fixture plan (veg, peanut and brinjal excluded, three cuisines) with changes."""
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["food"] = {**plan["food"], **food}
    if gear is not None:
        plan["gear"] = gear
    if shopping is not None:
        plan["shopping_days"] = shopping
    return PlanRules.model_validate(plan)


def open_rules(diet: Diet = "non_veg", **food: Any) -> PlanRules:
    """Rules that exclude nothing by name or allergen, unless `food` says so."""
    return plan_rules(**({"diet": diet, "exclude_allergens": [], "exclude_foods": []} | food))


def slot(**changes: Any) -> Slot:
    base: dict[str, Any] = {
        "id": "breakfast",
        "name": "Breakfast",
        "eat_at": time(8, 0),
        "kcal_share": 0.25,
        "protein_share": 0.25,
        "style": "overnight oats",
        "cook_when": "night_before",
        "max_active_min": 30,
        "max_total_min": 60,
    }
    return Slot.model_validate(base | changes)


def spec(
    *,
    day: DayKey = "mon",
    kcal: float = 500,
    protein_g: float = 40,
    slot_id: str = "breakfast",
    eat_at: int = 8,
) -> SlotSpec:
    return SlotSpec(
        day=day,
        day_type="training",
        slot=slot(id=slot_id, name=slot_id.capitalize(), eat_at=time(eat_at, 0)),
        kcal=kcal,
        protein_g=protein_g,
    )


def meal(
    total: Mapping[str, float | None] | None = None,
    incomplete: Mapping[str, list[str]] | None = None,
) -> MealResult:
    """nutrition-mcp's answer for a recipe, with the totals a test sets and nothing else."""
    return MealResult(
        items=[],
        total=Nutrients.model_validate(total or {}),
        total_incomplete=dict(incomplete or {}),
        glycemic_load=None,
        glycemic_load_missing=[],
    )


def draft(
    ids: Sequence[str] = ("oats",),
    *,
    day: DayKey = "mon",
    title: str = "Masala oats",
    cuisine: str = "South Indian",
    gear: list[str] | None = None,
    active_min: int = 10,
    total_min: int = 20,
) -> DraftRecipe:
    return DraftRecipe.model_validate(
        {
            "day": day,
            "title": title,
            "cuisine": cuisine,
            "ingredients": [{"id": food_id, "grams": 50} for food_id in ids],
            "steps": ["Mix and eat."],
            "active_min": active_min,
            "total_min": total_min,
            "gear": gear or [],
            "cook_when": "night_before",
            "store": "Fridge.",
            "swap": "Any grain.",
            "fits": "Carries breakfast.",
        }
    )


def checked(
    ids: Sequence[str] = ("oats",),
    *,
    day: DayKey = "mon",
    slot_id: str = "breakfast",
    title: str = "Masala oats",
    cuisine: str = "South Indian",
    kcal: float = 500,
    protein_g: float = 40,
    eat_at: int = 8,
    nutrition: MealResult | None = None,
) -> CheckedRecipe:
    return CheckedRecipe(
        spec=spec(day=day, kcal=kcal, protein_g=protein_g, slot_id=slot_id, eat_at=eat_at),
        draft=draft(ids, day=day, title=title, cuisine=cuisine),
        nutrition=nutrition,
        failures=[],
        attempts=1,
    )


def item(
    food_id: str,
    *,
    diet: Diet = "veg",
    allergens: Sequence[str] = (),
    pack_g: float | None = None,
    keeps_days_opened: int | None = None,
    name: str | None = None,
) -> CatalogueItem:
    return CatalogueItem.model_validate(
        {
            "id": food_id,
            "name": name or food_id.replace("_", " ").capitalize(),
            "category": "other",
            "allergens": list(allergens),
            "pack_g": pack_g,
            "keeps_days_opened": keeps_days_opened,
            "diet": diet,
            "units": ["g"],
            "per_100g": dict.fromkeys(NUTRIENT_KEYS),
        }
    )


def catalogue(*items: CatalogueItem) -> Catalogue:
    return Catalogue({i.id: i for i in items})


def day_type(**changes: Any) -> DayType:
    base: dict[str, Any] = {
        "id": "training",
        "name": "Training day",
        "kcal": 2000,
        "protein_g": 100,
        "carbs_g": 250,
        "fat_g": 60,
        "sat_fat_g_max": 20,
        "fibre_g_min": 35,
    }
    return DayType.model_validate(base | changes)


def day_total(**changes: float | None) -> Nutrients:
    """A day that meets every target of `day_type()`, with changes."""
    base: dict[str, float | None] = {
        "kcal": 2000,
        "protein_g": 100,
        "fat_g": 60,
        "sat_fat_g": 15,
        "carbs_available_g": 250,
        "fibre_g": 35,
    }
    return Nutrients.model_validate(base | changes)


def checks_of(failures: list[Failure]) -> list[str]:
    return [f.check for f in failures]


def only(failures: list[Failure]) -> Failure:
    assert len(failures) == 1, failures
    return failures[0]


# The names the checks carry for a nutrient, so the nutrient key is the source of the check name.


def test_every_nutrient_key_can_be_a_day_target() -> None:
    """Each key nutrition-mcp reports has a name and a unit for the messages."""
    assert set(LABELS) == set(NUTRIENT_KEYS)
    # The day type's own targets are fields of it, and the rules refuse them as extras.
    for key in (k for k in NUTRIENT_KEYS if k not in CORE_TARGET_FIELDS):
        target = NutrientTarget(nutrient=key, kind="min", value=10)
        failures, _, _ = check_day_totals(
            day_total(**{key: 1}), {}, day_type(extra=[target]), settings()
        )

        assert f"day_{key}" in checks_of(failures)


# Catalogue


def test_a_recipe_of_allowed_ingredients_passes() -> None:
    cat = catalogue(item("oats"), item("milk", allergens=["milk"]))

    assert check_catalogue(draft(["oats", "milk"]), cat, plan_rules()) == []


def test_unknown_id_is_named() -> None:
    failure = only(
        check_catalogue(draft(["oats", "dragonfruit"]), catalogue(item("oats")), plan_rules())
    )

    assert failure.check == "unknown_ingredient"
    assert "'dragonfruit'" in failure.message


def test_excluded_food_is_named() -> None:
    cat = catalogue(item("brinjal"))

    failure = only(check_catalogue(draft(["brinjal"]), cat, plan_rules()))

    assert failure.check == "excluded_food"
    assert "'brinjal'" in failure.message


def test_excluded_allergen_named() -> None:
    cat = catalogue(item("peanut_butter", allergens=["peanut"]))

    failure = only(check_catalogue(draft(["peanut_butter"]), cat, plan_rules()))

    assert failure.check == "excluded_allergen"
    assert "'peanut_butter'" in failure.message
    assert "peanut" in failure.message


def test_every_excluded_allergen_on_an_ingredient_is_named() -> None:
    cat = catalogue(item("pesto", allergens=["milk", "tree_nuts", "soy"]))
    rules = open_rules(exclude_allergens=["milk", "tree_nuts"])

    failure = only(check_catalogue(draft(["pesto"]), cat, rules))

    assert "milk" in failure.message
    assert "tree_nuts" in failure.message
    assert "soy" not in failure.message


def test_an_allergen_the_plan_allows_passes() -> None:
    cat = catalogue(item("milk", allergens=["milk"]))

    assert check_catalogue(draft(["milk"]), cat, open_rules(exclude_allergens=["peanut"])) == []


def test_vegan_rejects_curd() -> None:
    cat = catalogue(item("curd", diet="veg"))

    failure = only(check_catalogue(draft(["curd"]), cat, open_rules("vegan")))

    assert failure.check == "diet"
    assert "'curd'" in failure.message
    assert "veg" in failure.message
    assert "vegan" in failure.message


@pytest.mark.parametrize(
    ("plan_diet", "food_diet", "passes"),
    [
        ("vegan", "vegan", True),
        ("vegan", "veg", False),
        ("vegan", "egg", False),
        ("vegan", "non_veg", False),
        ("veg", "vegan", True),
        ("veg", "veg", True),
        ("veg", "egg", False),
        ("veg", "non_veg", False),
        ("egg", "vegan", True),
        ("egg", "veg", True),
        ("egg", "egg", True),
        ("egg", "non_veg", False),
        ("non_veg", "vegan", True),
        ("non_veg", "veg", True),
        ("non_veg", "egg", True),
        ("non_veg", "non_veg", True),
    ],
)
def test_diet_rules_agree_with_the_catalogue_filter(
    plan_diet: Diet, food_diet: Diet, passes: bool
) -> None:
    """The check and Catalogue.allowed_for read the same diet table, so the writer is never
    offered an ingredient the check then refuses."""
    cat = catalogue(item("food", diet=food_diet))
    rules = open_rules(plan_diet)

    offered = [i.id for i in cat.allowed_for(rules)] == ["food"]

    assert (check_catalogue(draft(["food"]), cat, rules) == []) is passes
    assert offered is passes


def test_every_rule_an_ingredient_breaks_is_reported() -> None:
    cat = catalogue(item("curd", diet="veg", allergens=["milk"]))
    rules = open_rules("vegan", exclude_allergens=["milk"], exclude_foods=["curd"])

    failures = check_catalogue(draft(["curd"]), cat, rules)

    assert checks_of(failures) == ["excluded_food", "excluded_allergen", "diet"]


def test_an_ingredient_written_twice_is_reported_once() -> None:
    failures = check_catalogue(
        draft(["brinjal", "brinjal"]), catalogue(item("brinjal")), plan_rules()
    )

    assert checks_of(failures) == ["excluded_food"]


def test_each_bad_ingredient_gets_its_own_failure_in_recipe_order() -> None:
    cat = catalogue(item("brinjal"), item("peanut_butter", allergens=["peanut"]))

    failures = check_catalogue(draft(["peanut_butter", "ghost", "brinjal"]), cat, plan_rules())

    assert checks_of(failures) == ["excluded_allergen", "unknown_ingredient", "excluded_food"]


# Slot targets (share: 500 kcal and 40 g protein; limits 450 to 550 kcal and 36 g)


@pytest.mark.parametrize(
    ("kcal", "passes"),
    [(450, True), (449, False), (500, True), (550, True), (550.2, False), (551, False)],
)
def test_slot_kcal_at_10_percent_passes_and_just_over_fails(kcal: float, passes: bool) -> None:
    failures = check_slot_targets(meal({"kcal": kcal, "protein_g": 40}), spec(), settings())

    assert (failures == []) is passes
    if not passes:
        assert checks_of(failures) == ["slot_kcal"]


def test_slot_kcal_message_names_the_numbers() -> None:
    failure = only(check_slot_targets(meal({"kcal": 551, "protein_g": 40}), spec(), settings()))

    assert "calories 551 kcal" in failure.message
    assert "slot allows at most 550 kcal" in failure.message
    assert "500 kcal" in failure.message
    assert "10%" in failure.message


def test_slot_kcal_too_low_says_at_least() -> None:
    failure = only(check_slot_targets(meal({"kcal": 400, "protein_g": 40}), spec(), settings()))

    assert "calories 400 kcal, slot needs at least 450 kcal" in failure.message


@pytest.mark.parametrize(
    ("protein", "passes"), [(36, True), (36.5, True), (35.9, False), (0, False)]
)
def test_slot_protein_at_90_percent(protein: float, passes: bool) -> None:
    failures = check_slot_targets(meal({"kcal": 500, "protein_g": protein}), spec(), settings())

    assert (failures == []) is passes
    if not passes:
        assert checks_of(failures) == ["slot_protein_g"]


def test_slot_protein_message_reads_like_an_instruction() -> None:
    failure = only(check_slot_targets(meal({"kcal": 500, "protein_g": 31}), spec(), settings()))

    assert failure.message.startswith("protein 31 g, slot needs at least 36 g")
    assert "90%" in failure.message


def test_rounding_never_hides_a_miss() -> None:
    """35.9 g against 36 g would read as equal if both were rounded to the nearest gram."""
    protein = only(check_slot_targets(meal({"kcal": 500, "protein_g": 35.9}), spec(), settings()))
    kcal = only(check_slot_targets(meal({"kcal": 550.4, "protein_g": 40}), spec(), settings()))

    assert "protein 35.9 g, slot needs at least 36 g" in protein.message
    assert "calories 551 kcal, slot allows at most 550 kcal" in kcal.message


def test_a_limit_that_is_not_a_whole_number_is_shown_on_the_safe_side() -> None:
    """A share of 41 g gives a floor of 36.9 g: asking for 37 g always passes."""
    failure = only(
        check_slot_targets(meal({"kcal": 500, "protein_g": 30}), spec(protein_g=41), settings())
    )

    assert "slot needs at least 37 g" in failure.message


def test_both_misses_are_reported() -> None:
    failures = check_slot_targets(meal({"kcal": 700, "protein_g": 10}), spec(), settings())

    assert checks_of(failures) == ["slot_kcal", "slot_protein_g"]


def test_slot_tolerances_come_from_settings() -> None:
    s = settings(recipe_kcal_tolerance=0.2, recipe_protein_min_share=0.5)

    assert check_slot_targets(meal({"kcal": 600, "protein_g": 20}), spec(), s) == []
    assert checks_of(check_slot_targets(meal({"kcal": 601, "protein_g": 19.9}), spec(), s)) == [
        "slot_kcal",
        "slot_protein_g",
    ]


def test_a_value_on_the_limit_passes_despite_float_noise() -> None:
    """3 * 0.1 is 0.30000000000000004 and 457.4 * 1.15 is 526.0099999999999 in floats."""
    low = settings(recipe_protein_min_share=0.1)
    wide = settings(recipe_kcal_tolerance=0.15)

    assert check_slot_targets(meal({"kcal": 500, "protein_g": 0.3}), spec(protein_g=3), low) == []
    assert check_slot_targets(meal({"kcal": 526.01, "protein_g": 40}), spec(kcal=457.4), wide) == []


def test_a_slot_with_no_protein_share_needs_no_protein() -> None:
    no_protein = spec(protein_g=0)

    assert check_slot_targets(meal({"kcal": 500, "protein_g": None}), no_protein, settings()) == []
    assert check_slot_targets(meal({"kcal": 500, "protein_g": 0}), no_protein, settings()) == []


def test_slot_nutrient_that_is_none_is_not_read_as_zero() -> None:
    failures = check_slot_targets(meal({"kcal": None, "protein_g": None}), spec(), settings())

    assert checks_of(failures) == ["slot_kcal", "slot_protein_g"]
    assert all("not known" in f.message for f in failures)


def test_slot_incomplete_protein_below_the_floor_fails_part_not_known() -> None:
    nutrition = meal({"kcal": 500, "protein_g": 30}, {"protein_g": ["mystery_flour"]})

    failure = only(check_slot_targets(nutrition, spec(), settings()))

    assert failure.check == "slot_protein_g"
    assert "part not known" in failure.message
    assert "mystery flour" in failure.message
    assert "at least 30 g" in failure.message


def test_slot_incomplete_protein_above_the_floor_passes() -> None:
    nutrition = meal({"kcal": 500, "protein_g": 38}, {"protein_g": ["mystery_flour"]})

    assert check_slot_targets(nutrition, spec(), settings()) == []


def test_slot_incomplete_kcal_inside_the_band_cannot_be_confirmed() -> None:
    nutrition = meal({"kcal": 480, "protein_g": 40}, {"kcal": ["mystery_flour"]})

    failure = only(check_slot_targets(nutrition, spec(), settings()))

    assert failure.check == "slot_kcal"
    assert failure.message.startswith("calories not known")
    assert "mystery flour" in failure.message
    assert "480 kcal" in failure.message


def test_slot_incomplete_kcal_already_over_the_band_fails_as_over() -> None:
    nutrition = meal({"kcal": 600, "protein_g": 40}, {"kcal": ["mystery_flour"]})

    failure = only(check_slot_targets(nutrition, spec(), settings()))

    assert "at least 600 kcal" in failure.message
    assert "slot allows at most 550 kcal" in failure.message


# Gear and time (slot allows 30 active and 60 total minutes)


def test_owned_gear_and_minutes_within_limits_pass() -> None:
    rules = plan_rules(gear=["air_fryer", "jar_with_lid"])

    recipe = draft(gear=["air_fryer", "jar_with_lid"], active_min=30, total_min=60)

    assert check_gear_and_time(recipe, rules, slot()) == []


def test_a_recipe_with_no_gear_passes() -> None:
    assert check_gear_and_time(draft(gear=[]), plan_rules(), slot()) == []


def test_gear_not_owned() -> None:
    rules = plan_rules(gear=["air_fryer", "jar_with_lid"])

    failure = only(check_gear_and_time(draft(gear=["oven", "air_fryer"]), rules, slot()))

    assert failure.check == "gear"
    assert failure.message == "gear not owned: oven; the plan's kitchen has air fryer, jar with lid"


def test_each_missing_piece_of_gear_is_named_once() -> None:
    rules = plan_rules(gear=["air_fryer"])

    failure = only(check_gear_and_time(draft(gear=["oven", "kadhai", "oven"]), rules, slot()))

    assert failure.message == "gear not owned: oven, kadhai; the plan's kitchen has air fryer"


@pytest.mark.parametrize(
    ("active", "total", "expected"),
    [
        (30, 60, []),
        (31, 60, ["active_min"]),
        (30, 61, ["total_min"]),
        (45, 90, ["active_min", "total_min"]),
    ],
)
def test_minutes_over_limit(active: int, total: int, expected: list[str]) -> None:
    failures = check_gear_and_time(draft(active_min=active, total_min=total), plan_rules(), slot())

    assert checks_of(failures) == expected


def test_minutes_message_names_the_numbers() -> None:
    failures = check_gear_and_time(draft(active_min=45, total_min=90), plan_rules(), slot())

    assert failures[0].message == "active time 45 min, slot allows at most 30 min"
    assert failures[1].message == "total time 90 min, slot allows at most 60 min"


def test_gear_and_minutes_are_reported_together() -> None:
    rules = plan_rules(gear=["jar_with_lid"])

    failures = check_gear_and_time(draft(gear=["oven"], active_min=99, total_min=20), rules, slot())

    assert checks_of(failures) == ["gear", "active_min", "time"]


@pytest.mark.parametrize(
    ("active", "total", "expected"),
    [(20, 20, []), (21, 20, ["time"]), (0, 0, []), (1, 0, ["time"])],
)
def test_active_minutes_may_not_exceed_total_minutes(
    active: int, total: int, expected: list[str]
) -> None:
    failures = check_gear_and_time(draft(active_min=active, total_min=total), plan_rules(), slot())

    assert checks_of(failures) == expected


def test_the_time_message_says_the_total_includes_the_active_time() -> None:
    failure = only(check_gear_and_time(draft(active_min=25, total_min=15), plan_rules(), slot()))

    assert failure.message == (
        "active time 25 min is more than the total time 15 min; the total includes the active time"
    )


# Nutrition figures in the model's text


def with_text(**fields: Any) -> DraftRecipe:
    return draft().model_copy(update=fields)


# Each phrase of the review, then the same figure the other way round, and one for every word.
FIGURES = [
    "450 kcal",
    "kcal 450",
    "28 g protein",
    "protein 28 g",
    "~30g of protein",
    "protein ~30g",
    "under 10 g sat fat",
    "sat fat under 10 g",
    "1200 mg sodium",
    "sodium: 1200 mg",
    "1,200 mg sodium",
    "180 cal",
    "about 400 calories",
    "calories 400",
    "1 calorie",
    "45 g carbs",
    "carbs 45 g",
    "12 g carb",
    "50 g carbohydrate",
    "carbohydrates of 50 g",
    "9 g fat",
    "fat 9",
    "4 g saturated fat",
    "saturated fat at 4 g",
    "6 g fibre",
    "fibre 6 g",
    "6 g fiber",
    "8 g sugar",
    "sugars 8 g",
    "2 g salt",
    "salt 2 g",
    "1.5 g FIBRE",
    "20% protein",
    "A 400-calorie breakfast",
    "A 400-kcal dinner",
    "30g-protein shake",
    "Packs 25+ g protein",
    "Packs 25 + g protein",
    "protein at least 30 g",
    "fat less than 10 g",
    "calories up to 400",
    "saturated fat at most 7 g",
    "sodium at most 575 mg",
    "fibre no more than 9 g",
    "30 g protein-rich",
    "A 400-calorie-controlled plan",
    "your 1,800-calorie-a-day plan",
    "a 400-kcal-per-meal cap",
    "30g+ protein",
    "Packs 25g+ protein",
    "calories 400/day",
    "kcal 450/serving",
    "protein 30/meal",
    "a 150 kcal sugar-free snack",
    "180 cal fat-free biscuit",
    "450 kcal protein-rich shake",
    "800 kj fat-free yogurt",
]

# Each phrase that can stand between a nutrition word and its number.
LIMIT_PHRASES = [
    "at least",
    "at most",
    "less than",
    "more than",
    "up to",
    "under",
    "over",
    "no more than",
    "below",
    "above",
]

# Numbers that are not nutrition, and nutrition words with no number.
NOT_FIGURES = [
    "10 minutes",
    "2 cups",
    "180 C",
    "200 ml water",
    "step 3",
    "high in protein",
    "1 tsp salt",
    "add salt 2 minutes before the end",
    "season with salt, then rest 5 minutes",
    "low in fat and sugar",
    "serves 2. Protein comes from the paneer",
    "a 2-minute rest",
    "serves 1",
    "Day 2 lunch",
    "a 10-minute soak",
    "Simmer 20-30 minutes",
    "2+ cups of water",
    "add salt at least 5 minutes before the end",
    "salt up to 2 tsp",
    "protein bar, then 2-3 tbsp curd",
    "100 g fat-free yogurt",
    "Take 2 protein-rich rotis",
    "2 sugar-free biscuits",
    "salt-free",
    "Salt 1/2 tsp",
    "sugar 1/2 cup",
    "salt up to 1/2 tsp",
    "Paneer 100 g + salt and pepper",
    "100 g protein-rich paneer",
]


@pytest.mark.parametrize("phrase", FIGURES)
def test_a_nutrition_figure_in_the_text_fails(phrase: str) -> None:
    failure = only(check_model_figures(with_text(fits=f"Light, {phrase}, and filling.")))

    assert failure.check == "model_figure"
    # The quote is the figure: the number and its word, without a lead-in such as "under".
    quoted = re.findall(r"'([^']+)'", failure.message)
    assert len(quoted) == 1
    assert quoted[0] in phrase
    assert re.search(r"\d", quoted[0])


@pytest.mark.parametrize("limit", LIMIT_PHRASES)
def test_a_limit_between_the_word_and_the_number_still_fails(limit: str) -> None:
    text = f"Light, protein {limit} 30 g, and filling."
    failure = only(check_model_figures(with_text(fits=text)))

    assert failure.message.startswith("fits names a nutrition figure ('protein ")


@pytest.mark.parametrize(
    ("phrase", "quoted"),
    [("~30g of protein", "30g of protein"), ("under 10 g sat fat", "10 g sat fat")],
)
def test_the_quote_starts_at_the_number(phrase: str, quoted: str) -> None:
    failure = only(check_model_figures(with_text(fits=f"Light, {phrase}, and filling.")))

    assert f"('{quoted}')" in failure.message


@pytest.mark.parametrize("text", NOT_FIGURES)
def test_numbers_that_are_not_nutrition_pass(text: str) -> None:
    assert check_model_figures(with_text(fits=text, steps=[text], store=text, swap=text)) == []


def test_every_field_the_user_reads_is_checked() -> None:
    recipe = with_text(
        title="Oats with 30 g protein",
        steps=["Soak the oats.", "Top with curd for 10 g protein."],
        store="Keeps a day; 450 kcal a bowl.",
        swap="Tofu for paneer saves 5 g fat.",
        fits="Carries 32 g protein.",
    )

    failures = check_model_figures(recipe)

    assert checks_of(failures) == ["model_figure"] * 5
    assert [f.message.split(" ", 2)[:2] for f in failures] == [
        ["title", "names"],
        ["step", "2"],
        ["store", "names"],
        ["swap", "names"],
        ["fits", "names"],
    ]


def test_the_message_names_the_field_quotes_the_phrase_and_says_why() -> None:
    failure = only(check_model_figures(with_text(fits="Carries 32 g protein and 6 g fibre.")))

    assert failure.message == (
        "fits names a nutrition figure ('32 g protein', '6 g fibre'); nutrition figures are "
        "worked out in code and must be left out of the text"
    )


def test_a_recipe_in_plain_words_passes() -> None:
    assert check_model_figures(draft()) == []


# Day totals (day_type(): 2000 kcal, 100 g protein, 20 g sat fat cap, 35 g fibre floor)


def test_a_day_that_meets_every_target_passes() -> None:
    assert check_day_totals(day_total(), {}, day_type(), settings())[0] == []


@pytest.mark.parametrize(
    ("kcal", "passes"),
    [(1900, True), (1899, False), (2000, True), (2100, True), (2101, False), (2100.4, False)],
)
def test_day_kcal_5_percent(kcal: float, passes: bool) -> None:
    failures, _, _ = check_day_totals(day_total(kcal=kcal), {}, day_type(), settings())

    assert (failures == []) is passes
    if not passes:
        assert checks_of(failures) == ["day_kcal"]


def test_day_kcal_message_names_the_numbers() -> None:
    failure = only(check_day_totals(day_total(kcal=2300), {}, day_type(), settings())[0])

    assert failure.message.startswith("calories 2300 kcal, day allows at most 2100 kcal")
    assert "2000 kcal" in failure.message
    assert "5%" in failure.message


def test_day_kcal_tolerance_comes_from_settings() -> None:
    s = settings(day_kcal_tolerance=0.1)

    assert check_day_totals(day_total(kcal=2200), {}, day_type(), s)[0] == []


@pytest.mark.parametrize(("protein", "passes"), [(100, True), (120, True), (99.9, False)])
def test_day_protein_is_a_floor_at_the_target(protein: float, passes: bool) -> None:
    failures, _, _ = check_day_totals(day_total(protein_g=protein), {}, day_type(), settings())

    assert (failures == []) is passes
    if not passes:
        assert only(failures).message.startswith("protein 99 g, day needs at least 100 g")


@pytest.mark.parametrize(("sat_fat", "passes"), [(0, True), (20, True), (20.5, False)])
def test_day_sat_fat_cap(sat_fat: float, passes: bool) -> None:
    failures, _, _ = check_day_totals(day_total(sat_fat_g=sat_fat), {}, day_type(), settings())

    assert (failures == []) is passes
    if not passes:
        failure = only(failures)
        assert failure.check == "day_sat_fat_g"
        assert failure.message.startswith("saturated fat 20.5 g, day allows at most 20 g")


@pytest.mark.parametrize(("fibre", "passes"), [(35, True), (60, True), (34.5, False)])
def test_day_fibre_floor(fibre: float, passes: bool) -> None:
    failures, _, _ = check_day_totals(day_total(fibre_g=fibre), {}, day_type(), settings())

    assert (failures == []) is passes
    if not passes:
        failure = only(failures)
        assert failure.check == "day_fibre_g"
        assert failure.message.startswith("fibre 34 g, day needs at least 35 g")


def test_extra_target_min_and_max() -> None:
    extra = [
        NutrientTarget(nutrient="iron_mg", kind="min", value=18),
        NutrientTarget(nutrient="sodium_mg", kind="max", value=2000),
    ]
    day = day_type(extra=extra)

    on_the_line, _, _ = check_day_totals(day_total(iron_mg=18, sodium_mg=2000), {}, day, settings())
    iron, _, _ = check_day_totals(day_total(iron_mg=17.9, sodium_mg=1500), {}, day, settings())
    sodium, _, _ = check_day_totals(day_total(iron_mg=30, sodium_mg=2001), {}, day, settings())

    assert on_the_line == []
    assert only(iron).check == "day_iron_mg"
    assert only(iron).message.startswith("iron 17.9 mg, day needs at least 18 mg")
    assert only(sodium).check == "day_sodium_mg"
    assert only(sodium).message.startswith("sodium 2001 mg, day allows at most 2000 mg")


def test_an_extra_target_with_no_value_is_not_read_as_zero() -> None:
    extra = [NutrientTarget(nutrient="iron_mg", kind="min", value=18)]

    failures, _, _ = check_day_totals(day_total(), {}, day_type(extra=extra), settings())

    assert only(failures).message.startswith("iron not known")


def test_a_cap_on_a_nutrient_with_no_value_is_not_read_as_zero() -> None:
    extra = [NutrientTarget(nutrient="sodium_mg", kind="max", value=2000)]

    failures, _, _ = check_day_totals(day_total(), {}, day_type(extra=extra), settings())

    assert only(failures).message.startswith("sodium not known")


def test_every_miss_on_a_day_is_reported_in_order() -> None:
    extra = [NutrientTarget(nutrient="iron_mg", kind="min", value=18)]
    total = day_total(kcal=1500, protein_g=50, sat_fat_g=30, fibre_g=10, iron_mg=5)

    failures, _, _ = check_day_totals(total, {}, day_type(extra=extra), settings())

    assert checks_of(failures) == [
        "day_kcal",
        "day_protein_g",
        "day_sat_fat_g",
        "day_fibre_g",
        "day_iron_mg",
    ]


def test_carbs_and_fat_are_soft() -> None:
    failures, _, soft = check_day_totals(day_total(fat_g=78), {}, day_type(), settings())

    assert failures == []
    assert soft["fat_g"] == 0.3
    assert soft["carbs_available_g"] == 0


def test_soft_deviation_is_negative_under_target() -> None:
    _, _, soft = check_day_totals(
        day_total(carbs_available_g=200, fat_g=30), {}, day_type(), settings()
    )

    assert soft == {"carbs_available_g": -0.2, "fat_g": -0.5}


def test_soft_leaves_out_what_is_not_fully_known() -> None:
    unknown, _, soft_unknown = check_day_totals(day_total(fat_g=None), {}, day_type(), settings())
    partial, _, soft_partial = check_day_totals(
        day_total(fat_g=30), {"fat_g": ["mystery_flour"]}, day_type(), settings()
    )

    assert unknown == partial == []
    assert "fat_g" not in soft_unknown
    assert "fat_g" not in soft_partial
    assert "carbs_available_g" in soft_partial


@pytest.mark.parametrize("key", ["kcal", "protein_g", "sat_fat_g", "fibre_g"])
def test_a_day_nutrient_that_is_none_is_never_zero(key: str) -> None:
    failures, _, _ = check_day_totals(day_total(**{key: None}), {}, day_type(), settings())

    failure = only(failures)
    assert "not known" in failure.message
    assert "part not known" not in failure.message


def test_incomplete_total_below_floor_is_unsettled() -> None:
    """The foods with no value might make up the gap, and nothing says how much they hold."""
    failures, unsettled, _ = check_day_totals(
        day_total(fibre_g=20), {"fibre_g": ["mystery_flour", "odd_seed"]}, day_type(), settings()
    )

    assert failures == []
    assert only(unsettled) == Failure(
        check="day_fibre_g",
        message="fibre not known (no value for mystery flour, odd seed); the part known is 20 g "
        "and day needs at least 35 g",
    )


@pytest.mark.parametrize("fibre", [35, 40])
def test_incomplete_total_at_or_above_floor_passes(fibre: float) -> None:
    failures, unsettled, _ = check_day_totals(
        day_total(fibre_g=fibre), {"fibre_g": ["mystery_flour"]}, day_type(), settings()
    )

    assert (failures, unsettled) == ([], [])


def test_incomplete_total_under_a_cap_with_no_ceiling_is_unsettled() -> None:
    failures, unsettled, _ = check_day_totals(
        day_total(sat_fat_g=12), {"sat_fat_g": ["mystery_flour"]}, day_type(), settings()
    )

    assert failures == []
    assert only(unsettled) == Failure(
        check="day_sat_fat_g",
        message="saturated fat not known (no value for mystery flour); the part known is 12 g "
        "and day allows at most 20 g",
    )


def test_incomplete_total_already_over_a_cap_fails_as_over() -> None:
    failures, unsettled, _ = check_day_totals(
        day_total(sat_fat_g=25),
        {"sat_fat_g": ["mystery_flour"]},
        day_type(),
        settings(),
        ceilings={"sat_fat_g": 30},
    )

    assert unsettled == []
    assert only(failures) == Failure(
        check="day_sat_fat_g",
        message="saturated fat at least 25 g, part not known (no value for mystery flour), "
        "day allows at most 20 g",
    )


@pytest.mark.parametrize(
    ("ceiling", "unsettled_checks"),
    [(12, []), (19.9, []), (20, []), (20 + 1e-10, []), (20.01, ["day_sat_fat_g"])],
)
def test_a_ceiling_within_the_cap_settles_an_incomplete_total(
    ceiling: float, unsettled_checks: list[str]
) -> None:
    failures, unsettled, _ = check_day_totals(
        day_total(sat_fat_g=12),
        {"sat_fat_g": ["rohu"]},
        day_type(),
        settings(),
        ceilings={"sat_fat_g": ceiling},
    )

    assert failures == []
    assert checks_of(unsettled) == unsettled_checks


def test_an_unsettled_cap_names_the_ceiling() -> None:
    _, unsettled, _ = check_day_totals(
        day_total(sat_fat_g=6),
        {"sat_fat_g": ["rohu", "curry_leaves"]},
        day_type(sat_fat_g_max=24),
        settings(),
        ceilings={"sat_fat_g": 30.7},
    )

    assert only(unsettled).message == (
        "saturated fat not known (no value for rohu, curry leaves); the part known is 6 g, "
        "at most 31 g if all their fat is saturated, and day allows at most 24 g"
    )


def test_a_total_with_no_value_for_any_food_is_unsettled_and_counts_as_zero() -> None:
    lacking = {"sat_fat_g": ["rohu"], "fibre_g": ["moong_dal"]}
    total = day_total(sat_fat_g=None, fibre_g=None)

    failures, unsettled, _ = check_day_totals(total, lacking, day_type(), settings())
    _, bounded, _ = check_day_totals(
        total, lacking, day_type(), settings(), ceilings={"sat_fat_g": 2.1}
    )
    _, too_high, _ = check_day_totals(
        total, lacking, day_type(), settings(), ceilings={"sat_fat_g": 25}
    )

    assert failures == []
    assert [f.message for f in unsettled] == [
        "saturated fat not known (no value for rohu), day allows at most 20 g",
        "fibre not known (no value for moong dal), day needs at least 35 g",
    ]
    assert checks_of(bounded) == ["day_fibre_g"]
    assert too_high[0].message == (
        "saturated fat not known (no value for rohu); at most 25 g if all their fat is "
        "saturated, and day allows at most 20 g"
    )


def test_a_ceiling_under_a_floor_fails_it() -> None:
    failures, unsettled, _ = check_day_totals(
        day_total(fibre_g=20),
        {"fibre_g": ["moong_dal"]},
        day_type(),
        settings(),
        ceilings={"fibre_g": 30},
    )

    assert unsettled == []
    assert only(failures) == Failure(
        check="day_fibre_g",
        message="fibre at most 30 g, part not known (no value for moong dal), "
        "day needs at least 35 g",
    )


def test_a_ceiling_never_changes_the_verdict_on_a_complete_total() -> None:
    over, _, _ = check_day_totals(
        day_total(sat_fat_g=21), {}, day_type(), settings(), ceilings={"sat_fat_g": 5}
    )
    under, _, _ = check_day_totals(
        day_total(sat_fat_g=19), {}, day_type(), settings(), ceilings={"sat_fat_g": 50}
    )

    assert checks_of(over) == ["day_sat_fat_g"]
    assert under == []


def test_incomplete_kcal_inside_the_band_is_unsettled() -> None:
    failures, unsettled, _ = check_day_totals(
        day_total(kcal=2050), {"kcal": ["mystery_flour"]}, day_type(), settings()
    )

    assert failures == []
    assert only(unsettled).message == (
        "calories not known (no value for mystery flour); the part known is 2050 kcal "
        "and day needs 1900 to 2100 kcal (2000 kcal target, within 5%)"
    )


def test_incomplete_kcal_below_the_band_is_unsettled() -> None:
    failures, unsettled, _ = check_day_totals(
        day_total(kcal=1500), {"kcal": ["mystery_flour"]}, day_type(), settings()
    )

    assert failures == []
    assert only(unsettled).message.startswith(
        "calories not known (no value for mystery flour); the part known is 1500 kcal"
    )


def test_incomplete_kcal_over_the_band_fails() -> None:
    failures, unsettled, _ = check_day_totals(
        day_total(kcal=2200), {"kcal": ["mystery_flour"]}, day_type(), settings()
    )

    assert unsettled == []
    assert only(failures).message.startswith("calories at least 2200 kcal, part not known")


@pytest.mark.parametrize(
    ("kcal", "ceiling", "verdict"),
    [
        (1950, 2050, "passes"),
        (1900, 2100, "passes"),
        (1850, 2050, "unsettled"),
        (1950, 2150, "unsettled"),
        (1500, 1800, "fails"),
        (2150, 2300, "fails"),
    ],
)
def test_incomplete_kcal_with_a_ceiling_is_held_to_both_ends_of_the_band(
    kcal: float, ceiling: float, verdict: str
) -> None:
    failures, unsettled, _ = check_day_totals(
        day_total(kcal=kcal),
        {"kcal": ["mystery_flour"]},
        day_type(),
        settings(),
        ceilings={"kcal": ceiling},
    )

    found = "fails" if failures else "unsettled" if unsettled else "passes"
    assert found == verdict


def test_incomplete_extra_min_is_read_like_a_floor() -> None:
    extra = [NutrientTarget(nutrient="iron_mg", kind="min", value=18)]
    day = day_type(extra=extra)

    short = check_day_totals(day_total(iron_mg=10), {"iron_mg": ["x"]}, day, settings())
    enough = check_day_totals(day_total(iron_mg=19), {"iron_mg": ["x"]}, day, settings())

    assert short[0] == []
    assert only(short[1]).message.startswith("iron not known (no value for x); the part known")
    assert enough[:2] == ([], [])


def test_a_nutrient_with_no_value_and_no_named_source_still_fails() -> None:
    failures, _, _ = check_day_totals(day_total(fibre_g=None), {}, day_type(), settings())

    assert only(failures).message == "fibre not known, day needs at least 35 g"


def test_a_cap_alone_reads_at_most_when_not_known() -> None:
    failures, _, _ = check_day_totals(day_total(sat_fat_g=None), {}, day_type(), settings())

    assert only(failures).message == "saturated fat not known, day allows at most 20 g"


def test_a_band_reads_as_a_range_when_not_known() -> None:
    failures, _, _ = check_day_totals(day_total(kcal=None), {}, day_type(), settings())

    assert only(failures).message.startswith("calories not known, day needs 1900 to 2100 kcal")


# How many decimals a number in a message needs to be true


B12_FLOOR = [NutrientTarget(nutrient="vit_b12_ug", kind="min", value=2.4)]
B12_CAP = [NutrientTarget(nutrient="vit_b12_ug", kind="max", value=2.4)]


def test_a_small_floor_and_total_keep_their_decimals() -> None:
    failures, _, _ = check_day_totals(
        day_total(vit_b12_ug=1.2), {}, day_type(extra=B12_FLOOR), settings()
    )

    assert only(failures).message == "vitamin B12 1.2 ug, day needs at least 2.4 ug"


def test_a_small_total_just_under_its_floor_is_not_rounded_up_to_it() -> None:
    failures, _, _ = check_day_totals(
        day_total(vit_b12_ug=2.39), {}, day_type(extra=B12_FLOOR), settings()
    )

    assert only(failures).message == "vitamin B12 2.39 ug, day needs at least 2.4 ug"


def test_a_small_cap_and_total_keep_their_decimals() -> None:
    failures, _, _ = check_day_totals(
        day_total(vit_b12_ug=2.6), {}, day_type(extra=B12_CAP), settings()
    )

    assert only(failures).message == "vitamin B12 2.6 ug, day allows at most 2.4 ug"


def test_a_small_cap_with_a_part_known_keeps_its_decimals() -> None:
    _, unsettled, _ = check_day_totals(
        day_total(vit_b12_ug=2.2), {"vit_b12_ug": ["curd"]}, day_type(extra=B12_CAP), settings()
    )

    assert only(unsettled).message == (
        "vitamin B12 not known (no value for curd); the part known is 2.2 ug "
        "and day allows at most 2.4 ug"
    )


def test_a_small_floor_that_is_not_known_keeps_its_decimals() -> None:
    failures, _, _ = check_day_totals(day_total(), {}, day_type(extra=B12_FLOOR), settings())

    assert only(failures).message == "vitamin B12 not known, day needs at least 2.4 ug"


def test_a_small_slot_share_keeps_its_decimals() -> None:
    failures = check_slot_targets(
        meal({"kcal": 500, "protein_g": 1.5}), spec(protein_g=2.5), settings()
    )

    assert only(failures).message == (
        "protein 1.5 g, slot needs at least 2.25 g (90% of its 2.5 g share)"
    )


@pytest.mark.parametrize(
    ("iron", "shown"),
    [(17.9, "iron 17.9 mg"), (17.04, "iron 17 mg"), (9.95, "iron 9.9 mg"), (14, "iron 14 mg")],
)
def test_a_total_is_shown_to_the_decimals_it_needs(iron: float, shown: str) -> None:
    extra = [NutrientTarget(nutrient="iron_mg", kind="min", value=18)]

    failures, _, _ = check_day_totals(
        day_total(iron_mg=iron), {}, day_type(extra=extra), settings()
    )

    assert only(failures).message == f"{shown}, day needs at least 18 mg"


def test_large_numbers_stay_whole() -> None:
    failures, _, _ = check_day_totals(day_total(kcal=1850.4), {}, day_type(), settings())

    assert only(failures).message == (
        "calories 1850 kcal, day needs at least 1900 kcal (2000 kcal target, within 5%)"
    )


def test_a_limit_that_is_not_whole_is_rounded_the_way_that_keeps_a_miss_a_miss() -> None:
    """Up for a floor and down for a cap, so reaching the number shown reaches the limit."""
    protein = check_slot_targets(
        meal({"kcal": 500, "protein_g": 20}), spec(protein_g=41), settings()
    )
    sodium = [NutrientTarget(nutrient="sodium_mg", kind="max", value=2000.4)]
    over, _, _ = check_day_totals(day_total(sodium_mg=2001), {}, day_type(extra=sodium), settings())

    assert only(protein).message.startswith("protein 20 g, slot needs at least 37 g")
    assert only(over).message == "sodium 2001 mg, day allows at most 2000 mg"


# The day from its recipes


def recipes_for(
    day: DayKey,
    *totals: Mapping[str, float | None],
    incomplete: Mapping[str, list[str]] | None = None,
) -> list[CheckedRecipe]:
    return [
        checked(
            [f"food_{n}"],
            day=day,
            slot_id=f"slot_{n}",
            title=f"Dish {day} {n}",
            nutrition=meal(t, incomplete if n == 0 else None),
        )
        for n, t in enumerate(totals)
    ]


def test_check_day_adds_the_recipes_up() -> None:
    parts = recipes_for(
        "mon",
        {
            "kcal": 700,
            "protein_g": 40,
            "fat_g": 20,
            "sat_fat_g": 5,
            "carbs_available_g": 90,
            "fibre_g": 10,
        },
        {
            "kcal": 1300,
            "protein_g": 60,
            "fat_g": 40,
            "sat_fat_g": 10,
            "carbs_available_g": 160,
            "fibre_g": 25,
        },
    )

    report = check_day("mon", parts, day_type(), settings())

    assert report.day == "mon"
    assert report.failures == []
    assert report.totals["kcal"] == 2000
    assert report.totals["protein_g"] == 100
    assert report.totals["zinc_mg"] is None
    assert report.soft == {"carbs_available_g": 0, "fat_g": 0}


def test_check_day_reports_the_failures_of_the_sum() -> None:
    parts = recipes_for("mon", {"kcal": 700, "protein_g": 40}, {"kcal": 1300, "protein_g": 40})

    report = check_day("mon", parts, day_type(), settings())

    assert "day_protein_g" in checks_of(report.failures)
    assert "protein 80 g, day needs at least 100 g" in report.failures[0].message


def test_check_day_counts_only_that_day() -> None:
    week = recipes_for("mon", {"kcal": 2000, "protein_g": 100, "sat_fat_g": 1, "fibre_g": 40})
    week += recipes_for("tue", {"kcal": 2000, "protein_g": 100, "sat_fat_g": 1, "fibre_g": 40})

    report = check_day("tue", week, day_type(), settings())

    assert report.day == "tue"
    assert report.totals["kcal"] == 2000
    assert report.failures == []


def test_check_day_rounds_sums_to_what_nutrition_mcp_reports() -> None:
    parts = recipes_for("mon", {"kcal": 0.1}, {"kcal": 0.2})

    assert check_day("mon", parts, day_type(), settings()).totals["kcal"] == 0.3


def test_check_day_treats_a_recipe_total_with_no_value_as_part_not_known() -> None:
    full = {"kcal": 1000, "protein_g": 50, "sat_fat_g": 5, "fibre_g": 20}
    parts = recipes_for("mon", full, {**full, "fibre_g": None}, {**full, "fibre_g": 2})

    report = check_day("mon", parts, day_type(), settings())

    assert report.totals["fibre_g"] == 22
    assert "day_fibre_g" not in checks_of(report.failures)
    fibre = only([f for f in report.unsettled if f.check == "day_fibre_g"])
    assert fibre.message.startswith("fibre not known (no value for food 1); the part known is 22 g")
    assert "Dish mon 1" not in fibre.message


def test_check_day_carries_what_nutrition_mcp_says_is_incomplete() -> None:
    full = {"kcal": 1000, "protein_g": 50, "sat_fat_g": 5, "fibre_g": 10}
    parts = recipes_for("mon", full, full, incomplete={"fibre_g": ["mystery_flour"]})

    report = check_day("mon", parts, day_type(), settings())

    fibre = only([f for f in report.unsettled if f.check == "day_fibre_g"])
    assert "(no value for mystery flour)" in fibre.message


def test_check_day_without_the_numbers_of_a_recipe_is_not_a_clean_day() -> None:
    parts = recipes_for("mon", {"kcal": 2000, "protein_g": 100, "sat_fat_g": 1, "fibre_g": 40})
    parts.append(checked(["ghost"], day="mon", slot_id="dinner", title="Ghost curry"))

    report = check_day("mon", parts, day_type(), settings())

    assert report.totals["kcal"] == 2000
    # The known part already meets the protein and fibre floors; the ghost curry could still
    # push calories and saturated fat over, and nothing bounds what it holds.
    assert report.failures == []
    assert checks_of(report.unsettled) == ["day_kcal", "day_sat_fat_g"]
    assert "Ghost curry" not in report.unsettled[0].message
    assert "(no value for ghost)" in report.unsettled[0].message
    assert report.soft == {}


def test_a_list_of_foods_with_no_value_holds_foods_and_never_recipe_titles() -> None:
    full = {"kcal": 1000, "protein_g": 50, "sat_fat_g": 5, "fibre_g": 10}
    parts = [
        checked(
            ["mystery_flour", "odd_seed"],
            slot_id="breakfast",
            title="Seed bread",
            nutrition=meal(full, {"fibre_g": ["mystery_flour"]}),
        ),
        checked(["ghost_curd", "ghost_rice"], slot_id="dinner", title="Ghost curry"),
    ]

    report = check_day("mon", parts, day_type(), settings())

    fibre = only([f for f in report.unsettled if f.check == "day_fibre_g"])
    assert fibre.message == (
        "fibre not known (no value for mystery flour, ghost curd, ghost rice); the part known is "
        "10 g and day needs at least 35 g"
    )
    assert all(
        "Seed bread" not in f.message and "Ghost curry" not in f.message
        for f in [*report.failures, *report.unsettled]
    )


def test_a_total_with_no_value_and_no_named_source_names_the_foods_of_the_meal() -> None:
    full = {"kcal": 1000, "protein_g": 50, "sat_fat_g": 5, "fibre_g": 10}
    parts = [
        checked(["oats", "dates"], slot_id="breakfast", nutrition=meal({**full, "fibre_g": None}))
    ]

    report = check_day("mon", parts, day_type(), settings())

    fibre = only([f for f in report.unsettled if f.check == "day_fibre_g"])
    assert fibre.message == "fibre not known (no value for oats, dates), day needs at least 35 g"


def test_check_day_settles_saturated_fat_through_the_total_fat_of_the_foods_that_lack_it() -> None:
    """A day shaped like the eval's: 6 g of saturated fat known, and curry leaves and rohu with
    a total fat and no saturated fat, as CoFID 2021 gives them. At most 8.17 g, under 24 g."""
    lunch = plate(
        portion("paneer_ish", 100, fat_g=10, sat_fat_g=6),
        portion("curry_leaves", 5, **CURRY_LEAVES),
        portion("rohu", 150, **ROHU),
    )
    day = day_type(sat_fat_g_max=24)
    fishy = checked(["paneer_ish", "curry_leaves", "rohu"], slot_id="lunch", nutrition=lunch)
    no_fat = lunch.model_copy(
        update={
            "items": [
                i.model_copy(update={"nutrients": i.nutrients.model_copy(update={"fat_g": None})})
                if i.food.id == "rohu"
                else i
                for i in lunch.items
            ]
        }
    )

    report = check_day("mon", [fishy], day, settings())
    unbounded = check_day("mon", [fishy.model_copy(update={"nutrition": no_fat})], day, settings())

    assert report.totals["sat_fat_g"] == 6
    assert "day_sat_fat_g" not in checks_of([*report.failures, *report.unsettled])
    assert "day_sat_fat_g" in checks_of(unbounded.unsettled)


# Adding meals up, for a day of recipes and for a day of meals eaten


def test_add_up_meals_adds_the_totals_and_rounds_to_what_nutrition_mcp_reports() -> None:
    meals = [
        (meal({"kcal": 0.1, "protein_g": 5}), ["a"]),
        (meal({"kcal": 0.2, "protein_g": 7.25}), ["b"]),
    ]

    total, _ = add_up_meals(meals)

    assert total.kcal == 0.3
    assert total.protein_g == 12.25
    assert total.fibre_g is None


def test_add_up_meals_has_nothing_to_name_when_every_food_has_a_value() -> None:
    full = {key: 1.0 for key in NUTRIENT_KEYS}

    total, incomplete = add_up_meals([(meal(full), ["a"]), (meal(full), ["b"])])

    assert total.kcal == 2
    assert incomplete == {}


def test_add_up_meals_names_the_foods_a_meal_lacks_for_each_nutrient() -> None:
    full = {key: 1.0 for key in NUTRIENT_KEYS}
    meals = [
        (meal(full, {"fibre_g": ["curd"]}), ["curd", "oats"]),
        (meal(full, {"fibre_g": ["curd", "ghee"], "kcal": ["ghee"]}), ["curd", "ghee"]),
    ]

    total, incomplete = add_up_meals(meals)

    assert total.fibre_g == 2
    assert incomplete == {"fibre_g": ["curd", "ghee"], "kcal": ["ghee"]}


def test_add_up_meals_counts_a_meal_without_numbers_as_lacking_everything_in_it() -> None:
    meals = [(meal({"kcal": 300}), ["rice"]), (None, ["ghost_curd", "ghost_rice"])]

    total, incomplete = add_up_meals(meals)

    assert total.kcal == 300
    assert set(incomplete) == set(NUTRIENT_KEYS)
    assert incomplete["kcal"] == ["ghost_curd", "ghost_rice"]


def test_add_up_meals_with_no_meals_knows_nothing() -> None:
    total, incomplete = add_up_meals([])

    assert all(value is None for _, value in total)
    assert incomplete == {}


def test_check_day_with_no_recipes_knows_nothing() -> None:
    report = check_day("mon", [], day_type(), settings())

    assert all(v is None for v in report.totals.values())
    assert checks_of(report.failures) == [
        "day_kcal",
        "day_protein_g",
        "day_sat_fat_g",
        "day_fibre_g",
    ]
    assert report.soft == {}


# The most saturated fat a day can hold, from nutrition-mcp's numbers for each food


def portion(
    food_id: str,
    grams: float,
    incomplete: Mapping[str, list[str]] | None = None,
    **per_100g: float | None,
) -> NutritionResult:
    """nutrition-mcp's answer for `grams` of a food with the nutrients per 100 g a test sets."""
    return NutritionResult(
        food=FoodSummary(
            id=food_id,
            kind="ingredient",
            name=food_id.replace("_", " ").capitalize(),
            aliases=[],
            regions=[],
            diet="veg",
            source_ids=["fake"],
            units={"g": 1.0},
        ),
        amount=grams,
        unit="g",
        grams=grams,
        measure_assumption=f"{grams:g} g",
        nutrients=Nutrients.model_validate(
            {
                key: None if value is None else round(value * grams / 100, 2)
                for key, value in per_100g.items()
            }
        ),
        incomplete=dict(incomplete or {}),
        raw_ingredient_totals=[],
        gi=None,
        gi_ref=None,
        glycemic_load=None,
        oil_assumption=None,
        sources=["fake"],
    )


def plate(*items: NutritionResult) -> MealResult:
    """A meal of `items` with the totals nutrition-mcp gives: each nutrient summed over the items
    that have it, and the foods that lack it (or a dish's ingredients that do) named."""
    total, _ = sum_nutrients([(i.food.id, i.nutrients) for i in items])
    lacking: dict[str, list[str]] = {}
    for key in NUTRIENT_KEYS:
        for i in items:
            names = i.incomplete.get(key) or ([i.food.id] if i.nutrients.get(key) is None else [])
            lacking.setdefault(key, []).extend(names)
    return MealResult(
        items=list(items),
        total=Nutrients.model_validate(
            {key: None if value is None else round(value, 2) for key, value in total}
        ),
        total_incomplete={key: names for key, names in lacking.items() if names},
        glycemic_load=None,
        glycemic_load_missing=[],
    )


# CoFID 2021 gives these foods a total fat and no saturated fat, as nutrition-mcp reports them.
CURRY_LEAVES = {"fat_g": 1.3}
ROHU = {"fat_g": 1.4}


def test_the_ceiling_counts_saturated_fat_where_it_is_known() -> None:
    assert sat_fat_ceiling([plate(portion("paneer", 100, fat_g=24.5, sat_fat_g=15.43))]) == 15.43


def test_the_ceiling_counts_total_fat_for_a_food_with_no_saturated_fat() -> None:
    assert sat_fat_ceiling([plate(portion("rohu", 150, **ROHU))]) == 2.1


def test_the_ceiling_adds_up_every_food_of_every_meal_to_two_decimals() -> None:
    lunch = plate(portion("paneer", 100, fat_g=24.5, sat_fat_g=15.43), portion("rohu", 150, **ROHU))
    dinner = plate(portion("curry_leaves", 5, **CURRY_LEAVES), portion("oil", 0.1, sat_fat_g=20))

    # 15.43 + 2.1 + 0.07 + 0.02, with no float noise.
    assert sat_fat_ceiling([lunch, dinner]) == 17.62


def test_a_food_with_neither_value_leaves_no_ceiling() -> None:
    lunch = plate(portion("paneer", 100, fat_g=24.5, sat_fat_g=15.43), portion("mystery", 50))

    assert sat_fat_ceiling([lunch]) is None


def test_a_meal_with_no_numbers_leaves_no_ceiling() -> None:
    assert sat_fat_ceiling([plate(portion("rohu", 150, **ROHU)), None]) is None


def test_saturated_fat_that_is_only_part_known_falls_back_to_a_complete_total_fat() -> None:
    thali = portion("thali", 200, {"sat_fat_g": ["ghee"]}, fat_g=10, sat_fat_g=3)

    assert sat_fat_ceiling([plate(thali)]) == 20


def test_total_fat_that_is_only_part_known_leaves_no_ceiling() -> None:
    thali = portion("thali", 200, {"sat_fat_g": ["ghee"], "fat_g": ["ghee"]}, fat_g=10, sat_fat_g=3)

    assert sat_fat_ceiling([plate(thali)]) is None


# Which recipe to blame for a day

# Breakfast, lunch and dinner share a 1000 kcal day. Each dish has a share of the day's calories
# and protein and an actual amount; what a test does not set is on target, so one check fails.
BLAME_DAY = day_type(kcal=1000, protein_g=100, sat_fat_g_max=20, fibre_g_min=35)
HOURS = {"breakfast": 8, "lunch": 13, "dinner": 20}
SHARE = {"breakfast": 200, "lunch": 300, "dinner": 500}


def dish(
    slot_id: str,
    *,
    share: float | None = None,
    kcal: float | None = None,
    protein_share: float = 34,
    incomplete: Mapping[str, list[str]] | None = None,
    **nutrients: float | None,
) -> CheckedRecipe:
    """A recipe of `slot_id` on Monday whose numbers are on target unless changed. It is one food
    of 100 g, so nutrition-mcp's numbers for the food are the recipe's."""
    planned = SHARE[slot_id] if share is None else share
    totals: dict[str, float | None] = {
        "kcal": planned if kcal is None else kcal,
        "protein_g": 34,
        "sat_fat_g": 1,
        "fibre_g": 12,
    } | nutrients
    return checked(
        [f"{slot_id}_food"],
        slot_id=slot_id,
        title=f"Dish {slot_id}",
        kcal=planned,
        protein_g=protein_share,
        eat_at=HOURS.get(slot_id, 17),
        nutrition=plate(portion(f"{slot_id}_food", 100, incomplete, **totals)),
    )


def blamed(
    dishes: list[CheckedRecipe], first_failure: str, day: DayType = BLAME_DAY
) -> CheckedRecipe:
    report = check_day("mon", dishes, day, settings())
    assert report.failures[0].check == first_failure
    return worst_recipe_for_day(report, dishes, day)


def test_over_on_calories_blames_the_most_kcal_above_share_not_the_biggest_percentage() -> None:
    """The snack is 10% over its 50 kcal share and the dinner 9% over its 800: the dinner holds
    72 kcal of the excess and the snack 5, so rewriting the dinner is what brings the day down."""
    snack = dish("snack", share=50, kcal=55)
    dinner = dish("dinner", share=800, kcal=872)
    breakfast = dish("breakfast", share=150)

    assert blamed([breakfast, snack, dinner], "day_kcal") is dinner


def test_over_on_calories_ignores_a_recipe_furthest_under_its_share() -> None:
    breakfast = dish("breakfast", share=100, kcal=10)
    dinner = dish("dinner", share=800, kcal=960)
    lunch = dish("lunch", share=100)

    assert blamed([breakfast, lunch, dinner], "day_kcal") is dinner


def test_under_on_calories_blames_the_most_kcal_below_share() -> None:
    breakfast = dish("breakfast", share=100, kcal=20)
    dinner = dish("dinner", share=800, kcal=650)
    lunch = dish("lunch", share=100, kcal=100)

    assert blamed([breakfast, lunch, dinner], "day_kcal") is dinner


def test_under_on_calories_ignores_a_recipe_over_its_share() -> None:
    breakfast = dish("breakfast", share=200, kcal=400)
    lunch = dish("lunch", share=300, kcal=100)
    dinner = dish("dinner", share=500, kcal=380)

    assert blamed([breakfast, lunch, dinner], "day_kcal") is lunch


def test_under_on_protein_blames_the_most_grams_below_its_protein_share() -> None:
    """The breakfast has the least protein (5 g), the dinner the largest gap to its share."""
    breakfast = dish("breakfast", protein_share=10, protein_g=5)
    lunch = dish("lunch", protein_share=45, protein_g=30)
    dinner = dish("dinner", protein_share=45, protein_g=25)

    assert blamed([breakfast, lunch, dinner], "day_protein_g") is dinner


def test_over_on_saturated_fat_blames_the_recipe_holding_the_most() -> None:
    """Breakfast is first in the day and on its calories, but the ghee is in the lunch."""
    breakfast = dish("breakfast", sat_fat_g=1)
    lunch = dish("lunch", sat_fat_g=30)
    dinner = dish("dinner", sat_fat_g=5)

    assert blamed([breakfast, lunch, dinner], "day_sat_fat_g") is lunch


def test_under_on_fibre_blames_the_recipe_holding_the_least() -> None:
    breakfast = dish("breakfast", fibre_g=12)
    lunch = dish("lunch", fibre_g=3)
    dinner = dish("dinner", fibre_g=8)

    assert blamed([breakfast, lunch, dinner], "day_fibre_g") is lunch


@pytest.mark.parametrize(
    ("target", "amounts", "expected"),
    [
        (NutrientTarget(nutrient="iron_mg", kind="min", value=18), (8, 2, 5), "lunch"),
        (NutrientTarget(nutrient="sodium_mg", kind="max", value=2000), (900, 700, 1200), "dinner"),
    ],
)
def test_an_extra_target_blames_by_its_own_direction(
    target: NutrientTarget, amounts: tuple[float, float, float], expected: str
) -> None:
    day = day_type(kcal=1000, protein_g=100, extra=[target])
    key = target.nutrient
    dishes = [
        dish(slot_id, **{key: amount})
        for slot_id, amount in zip(("breakfast", "lunch", "dinner"), amounts, strict=True)
    ]

    assert blamed(dishes, f"day_{key}", day).spec.slot.id == expected


def test_a_recipe_with_no_numbers_is_blamed_first_when_the_day_is_short() -> None:
    """Its missing amount might be the gap. check_day leaves a partial total under a floor
    unsettled unless a ceiling shows it short, so the report is built by hand."""
    breakfast = dish("breakfast")
    lunch = dish("lunch")
    dinner = checked(["ghost"], slot_id="dinner", kcal=500, eat_at=20)
    report = DayReport(
        day="mon",
        totals={"kcal": 500},
        failures=[Failure(check="day_kcal", message="calories at most 900 kcal")],
        unsettled=[],
        soft={},
    )

    assert worst_recipe_for_day(report, [breakfast, lunch, dinner], BLAME_DAY) is dinner


def test_a_recipe_with_an_unknown_amount_is_not_blamed_when_the_day_is_over() -> None:
    breakfast = dish("breakfast", sat_fat_g=1)
    lunch = dish("lunch", sat_fat_g=None)
    dinner = dish("dinner", sat_fat_g=30)

    assert blamed([breakfast, lunch, dinner], "day_sat_fat_g") is dinner


def test_a_day_the_checks_cannot_settle_has_nothing_to_blame() -> None:
    """11 g of saturated fat is known, under the 20 g cap, but the lunch has no value and no fat
    to bound it by: the day is unsettled, and no recipe is rewritten for it."""
    dishes = [
        dish("breakfast", sat_fat_g=5),
        dish("lunch", sat_fat_g=None),
        dish("dinner", sat_fat_g=6),
    ]

    report = check_day("mon", dishes, BLAME_DAY, settings())

    assert report.failures == []
    assert checks_of(report.unsettled) == ["day_sat_fat_g"]
    with pytest.raises(ValueError, match="passes every day check"):
        worst_recipe_for_day(report, dishes, BLAME_DAY)


def test_a_failure_whose_total_is_within_its_limits_is_blamed_on_no_recipe() -> None:
    """No failure check_day gives is like this: a total within its limits either passes or, when
    a food lacks a value, is unsettled."""
    dishes = [dish("breakfast"), dish("lunch", sat_fat_g=None), dish("dinner")]
    report = DayReport(
        day="mon",
        totals={"sat_fat_g": 2},
        failures=[Failure(check="day_sat_fat_g", message="saturated fat not known")],
        unsettled=[],
        soft={},
    )

    with pytest.raises(ValueError, match="mon's sat_fat_g total is within its limits"):
        worst_recipe_for_day(report, dishes, BLAME_DAY)


def test_a_day_short_whatever_its_missing_amount_holds_blames_a_recipe_that_lacks_it() -> None:
    """A total with no value for any food counts as 0, as the day checks count it, so a ceiling
    under the floor makes the day under it."""
    dishes = [dish("breakfast", fibre_g=None), dish("lunch", fibre_g=None), dish("dinner")]
    report = DayReport(
        day="mon",
        totals={"fibre_g": None},
        failures=[Failure(check="day_fibre_g", message="fibre at most 30 g, part not known")],
        unsettled=[],
        soft={},
    )

    assert worst_recipe_for_day(report, list(reversed(dishes)), BLAME_DAY) is dishes[0]


def test_a_tie_goes_to_the_earlier_slot_whatever_the_list_order() -> None:
    breakfast = dish("breakfast", sat_fat_g=15)
    lunch = dish("lunch", sat_fat_g=1)
    dinner = dish("dinner", sat_fat_g=15)

    assert blamed([dinner, lunch, breakfast], "day_sat_fat_g") is breakfast


def test_slots_at_the_same_time_tie_in_list_order() -> None:
    first = dish("snack", share=500, protein_g=60, sat_fat_g=30)
    second = dish("tea", share=500, protein_g=60, sat_fat_g=30)

    assert blamed([second, first], "day_sat_fat_g") is second


def test_the_first_failing_check_in_nutrient_order_decides() -> None:
    """Calories and fibre both fail. Calories come first, so the recipe 150 kcal under its share
    is blamed, not the breakfast that ties the dinner for the least fibre."""
    breakfast = dish("breakfast", share=200, kcal=200, fibre_g=0)
    lunch = dish("lunch", share=300, kcal=250, fibre_g=10)
    dinner = dish("dinner", share=500, kcal=350, fibre_g=0)
    dishes = [breakfast, lunch, dinner]

    report = check_day("mon", dishes, BLAME_DAY, settings())

    assert checks_of(report.failures) == ["day_kcal", "day_fibre_g"]
    assert worst_recipe_for_day(report, dishes, BLAME_DAY) is dinner


def test_worst_recipe_is_chosen_from_the_reports_day_only() -> None:
    other_day = checked(["x"], day="tue", slot_id="lunch", kcal=300, nutrition=meal({"kcal": 900}))
    this_day = dish("dinner", share=500, kcal=900)
    report = DayReport(
        day="mon",
        totals={"kcal": 1500},
        failures=[Failure(check="day_kcal", message="calories 1500 kcal")],
        unsettled=[],
        soft={},
    )

    assert worst_recipe_for_day(report, [other_day, this_day], BLAME_DAY) is this_day


def test_worst_recipe_needs_a_recipe_on_that_day() -> None:
    report = DayReport(day="wed", totals={}, failures=[], unsettled=[], soft={})

    with pytest.raises(ValueError, match="wed"):
        worst_recipe_for_day(report, [dish("lunch")], BLAME_DAY)


def test_worst_recipe_needs_a_failing_day_check() -> None:
    dishes = [dish("breakfast"), dish("lunch"), dish("dinner")]
    report = check_day("mon", dishes, BLAME_DAY, settings())

    assert report.failures == []
    with pytest.raises(ValueError, match="mon"):
        worst_recipe_for_day(report, dishes, BLAME_DAY)


# Variety


def pair(
    first: Sequence[str],
    second: Sequence[str],
    *,
    slot_id: str = "breakfast",
    days: tuple[DayKey, DayKey] = ("mon", "tue"),
    cuisines: tuple[str, str] = ("South Indian", "Japanese"),
) -> list[CheckedRecipe]:
    return [
        checked(first, day=days[0], slot_id=slot_id, title="First dish", cuisine=cuisines[0]),
        checked(second, day=days[1], slot_id=slot_id, title="Second dish", cuisine=cuisines[1]),
    ]


SIX = ["a", "b", "c", "d", "e", "f"]


def test_variety_jaccard_0_6_fails() -> None:
    five_of_seven = pair(SIX, ["a", "b", "c", "d", "e", "g"])
    three_of_nine = pair(SIX, ["a", "b", "c", "g", "h", "i"])

    similar = check_variety(five_of_seven, plan_rules(), settings())
    different = check_variety(three_of_nine, plan_rules(), settings())

    failure = only(similar[("tue", "breakfast")])
    assert failure.check == "variety_ingredients"
    assert "0.71" in failure.message
    assert different == {}


def test_a_similarity_of_exactly_the_threshold_fails() -> None:
    three_of_five = pair(["a", "b", "c", "d"], ["a", "b", "c", "e"])
    two_of_five = pair(["a", "b", "c"], ["a", "d", "e"])

    assert set(check_variety(three_of_five, plan_rules(), settings())) == {("tue", "breakfast")}
    assert check_variety(two_of_five, plan_rules(), settings()) == {}


def test_the_threshold_comes_from_settings() -> None:
    recipes = pair(["a", "b", "c", "d"], ["a", "b", "c", "e"])

    assert check_variety(recipes, plan_rules(), settings(variety_jaccard_fail_at=0.61)) == {}
    assert check_variety(recipes, plan_rules(), settings(variety_jaccard_fail_at=0.6)) != {}


def test_the_failure_names_the_earlier_recipe_and_the_numbers() -> None:
    failures = check_variety(
        pair(["a", "b", "c", "d"], ["a", "b", "c", "e"]), plan_rules(), settings()
    )

    message = only(failures[("tue", "breakfast")]).message
    assert "mon" in message
    assert "First dish" in message
    assert "3 of 5" in message
    assert "0.60" in message


def test_the_same_ingredients_in_another_order_are_the_same_set() -> None:
    recipes = pair(["a", "b", "c"], ["c", "b", "a", "a"])

    assert ("tue", "breakfast") in check_variety(recipes, plan_rules(), settings())


def test_same_title_fails() -> None:
    recipes = [
        checked(["a", "b"], day="mon", title="Masala Oats!", cuisine="South Indian"),
        checked(["x", "y"], day="wed", title="  masala   oats ", cuisine="Japanese"),
    ]

    failure = only(check_variety(recipes, plan_rules(), settings())[("wed", "breakfast")])

    assert failure.check == "variety_title"
    assert "Masala Oats!" in failure.message


def test_different_titles_pass() -> None:
    recipes = [
        checked(["a"], day="mon", title="Masala oats", cuisine="South Indian"),
        checked(["x"], day="wed", title="Masala oat bowl", cuisine="Japanese"),
    ]

    assert check_variety(recipes, plan_rules(), settings()) == {}


def test_the_later_day_gets_the_failure_whatever_the_list_order() -> None:
    recipes = pair(["a", "b", "c"], ["a", "b", "c"], cuisines=("South Indian", "Japanese"))

    failures = check_variety(list(reversed(recipes)), plan_rules(), settings())

    assert set(failures) == {("tue", "breakfast")}


def test_a_recipe_similar_to_two_earlier_ones_hears_about_both() -> None:
    recipes = [
        checked(["a", "b", "c"], day="mon", title="One", cuisine="South Indian"),
        checked(["a", "b", "c", "d"], day="wed", title="Two", cuisine="Japanese"),
        checked(["a", "b", "c", "e"], day="fri", title="Three", cuisine="Mediterranean"),
    ]

    failures = check_variety(recipes, plan_rules(), settings())

    assert set(failures) == {("wed", "breakfast"), ("fri", "breakfast")}
    assert len(failures[("wed", "breakfast")]) == 1
    assert len(failures[("fri", "breakfast")]) == 2


def test_variety_is_judged_within_a_slot() -> None:
    recipes = [
        checked(["a", "b"], day="mon", slot_id="lunch", title="Same", cuisine="South Indian"),
        checked(["a", "b"], day="wed", slot_id="dinner", title="Same", cuisine="South Indian"),
    ]

    assert check_variety(recipes, plan_rules(), settings()) == {}


def test_cuisine_repeated_on_consecutive_days_fails() -> None:
    recipes = pair(["a"], ["x"], cuisines=("South Indian", "south indian."))

    failure = only(check_variety(recipes, plan_rules(), settings())[("tue", "breakfast")])

    assert failure.check == "variety_cuisine"
    assert "South Indian" in failure.message
    assert "mon" in failure.message


def test_cuisine_repeated_after_a_gap_passes() -> None:
    recipes = pair(["a"], ["x"], days=("mon", "wed"), cuisines=("South Indian", "South Indian"))

    assert check_variety(recipes, plan_rules(), settings()) == {}


def test_cuisine_is_not_compared_from_sunday_to_monday() -> None:
    recipes = pair(["a"], ["x"], days=("sun", "mon"), cuisines=("Japanese", "Japanese"))

    assert check_variety(recipes, plan_rules(), settings()) == {}


def test_cuisine_may_repeat_when_the_plan_lists_one() -> None:
    recipes = pair(["a"], ["x"], cuisines=("South Indian", "South Indian"))

    assert check_variety(recipes, plan_rules(cuisines=["South Indian"]), settings()) == {}


def test_a_cuisine_outside_the_plans_list_fails() -> None:
    recipe = checked(["a"], cuisine="Italian")

    failures = check_variety([recipe], plan_rules(), settings())

    failure = only(failures[("mon", "breakfast")])
    assert failure.check == "cuisine_not_in_plan"
    assert "'Italian'" in failure.message
    assert "South Indian, Japanese, Mediterranean" in failure.message


def test_a_cuisine_outside_a_list_of_one_fails() -> None:
    recipe = checked(["a"], cuisine="Japanese")

    failures = check_variety([recipe], plan_rules(cuisines=["South Indian"]), settings())

    assert checks_of(failures[("mon", "breakfast")]) == ["cuisine_not_in_plan"]


@pytest.mark.parametrize(
    "written",
    ["South Indian", "south indian", "South-Indian", "  SOUTH   INDIAN. ", "south_indian"],
)
def test_a_cuisine_matches_the_plans_list_whatever_its_spelling(written: str) -> None:
    assert check_variety([checked(["a"], cuisine=written)], plan_rules(), settings()) == {}


def test_a_near_synonym_does_not_get_past_the_list() -> None:
    recipes = pair(["a"], ["x"], cuisines=("South Indian", "Tamil"))

    failures = check_variety(recipes, plan_rules(), settings())

    assert set(failures) == {("tue", "breakfast")}
    assert only(failures[("tue", "breakfast")]).check == "cuisine_not_in_plan"


def test_the_repeat_rule_compares_the_plans_entry_not_the_spelling() -> None:
    recipes = pair(["a"], ["x"], cuisines=("South Indian", "SOUTH-INDIAN"))

    failure = only(check_variety(recipes, plan_rules(), settings())[("tue", "breakfast")])

    assert failure.check == "variety_cuisine"
    assert "'South Indian'" in failure.message


def test_two_unlisted_cuisines_in_a_row_are_not_a_repeat() -> None:
    recipes = pair(["a"], ["x"], cuisines=("Italian", "Italian"))

    failures = check_variety(recipes, plan_rules(), settings())

    assert checks_of(failures[("mon", "breakfast")]) == ["cuisine_not_in_plan"]
    assert checks_of(failures[("tue", "breakfast")]) == ["cuisine_not_in_plan"]


def test_a_plan_listing_one_cuisine_twice_does_not_count_as_two() -> None:
    rules = plan_rules(cuisines=["South Indian", "south-indian"])
    recipes = pair(["a"], ["x"], cuisines=("South Indian", "South Indian"))

    assert check_variety(recipes, rules, settings()) == {}


def test_a_repeat_names_the_first_listing_of_a_cuisine_the_plan_spells_twice() -> None:
    rules = plan_rules(cuisines=["South Indian", "south-indian", "Japanese"])
    recipes = pair(["a"], ["x"], cuisines=("south indian", "SOUTH-INDIAN"))

    failure = only(check_variety(recipes, rules, settings())[("tue", "breakfast")])

    assert "'South Indian'" in failure.message


def test_punctuation_separates_words_in_a_title_it_does_not_join_them() -> None:
    recipes = [
        checked(["a"], day="mon", title="Rice-Bowl", cuisine="South Indian"),
        checked(["x"], day="wed", title="Ricebowl", cuisine="Japanese"),
    ]

    assert check_variety(recipes, plan_rules(), settings()) == {}


def test_titles_that_differ_in_punctuation_are_the_same_title() -> None:
    recipes = [
        checked(["a"], day="mon", title="Masala-Oats", cuisine="South Indian"),
        checked(["x"], day="wed", title="Masala Oats", cuisine="Japanese"),
        checked(["y"], day="fri", title="masala, oats!", cuisine="Mediterranean"),
    ]

    failures = check_variety(recipes, plan_rules(), settings())

    assert checks_of(failures[("wed", "breakfast")]) == ["variety_title"]
    assert checks_of(failures[("fri", "breakfast")]) == ["variety_title", "variety_title"]


def test_a_day_can_fail_on_ingredients_and_cuisine_together() -> None:
    recipes = pair(["a", "b"], ["a", "b"], cuisines=("Japanese", "Japanese"))

    failures = check_variety(recipes, plan_rules(), settings())[("tue", "breakfast")]

    assert checks_of(failures) == ["variety_ingredients", "variety_cuisine"]


def clash() -> list[CheckedRecipe]:
    """Monday and Tuesday alike in ingredients and cuisine."""
    return pair(["a", "b", "c"], ["a", "b", "c"], cuisines=("Japanese", "Japanese"))


MON, TUE = ("mon", "breakfast"), ("tue", "breakfast")


def test_a_clash_goes_to_the_recipe_the_caller_prefers() -> None:
    failures = check_variety(clash(), plan_rules(), settings(), prefer=[{MON}])

    assert set(failures) == {MON}
    alike, cuisine = failures[MON]
    # Worded from Monday's side: Tuesday is the recipe it must differ from.
    assert alike.check == "variety_ingredients"
    assert "with the tue recipe 'Second dish'" in alike.message
    assert cuisine.check == "variety_cuisine"
    assert cuisine.message == (
        "cuisine 'Japanese' repeats the day after (tue); "
        "the plan lists other cuisines, so use a different one"
    )


def test_a_title_clash_on_the_earlier_recipe_names_the_later_one() -> None:
    recipes = [
        checked(["a"], day="mon", title="Masala oats", cuisine="South Indian"),
        checked(["x"], day="wed", title="Masala Oats", cuisine="Japanese"),
    ]

    failures = check_variety(recipes, plan_rules(), settings(), prefer=[{MON}])

    assert only(failures[MON]).message == (
        "same title as the wed recipe 'Masala Oats'; make a different dish and name it so"
    )


@pytest.mark.parametrize(
    ("prefer", "blamed"),
    [
        ([{MON}, {TUE}], MON),
        ([{TUE}, {MON}], TUE),
        ([{MON, TUE}, {MON}], MON),
        ([set(), {MON}], MON),
        ([{MON, TUE}, {MON, TUE}], TUE),
        ([], TUE),
    ],
    ids=["first_set", "first_set_other_way", "second_set", "empty_first", "neither", "none"],
)
def test_the_first_set_that_tells_the_pair_apart_decides(
    prefer: list[set[tuple[DayKey, str]]], blamed: tuple[DayKey, str]
) -> None:
    failures = check_variety(clash(), plan_rules(), settings(), prefer=prefer)

    assert set(failures) == {blamed}
    assert checks_of(failures[blamed]) == ["variety_ingredients", "variety_cuisine"]


def test_each_pair_is_blamed_on_its_own() -> None:
    """Wednesday has Monday's title and Tuesday's ingredients; only Monday is preferred."""
    recipes = [
        checked(["a", "b"], day="mon", title="One", cuisine="South Indian"),
        checked(["x", "y"], day="tue", title="Two", cuisine="Japanese"),
        checked(["x", "y", "z"], day="wed", title="One", cuisine="Mediterranean"),
    ]

    failures = check_variety(recipes, plan_rules(), settings(), prefer=[{MON}])

    assert checks_of(failures[MON]) == ["variety_title"]
    assert "the wed recipe 'One'" in failures[MON][0].message
    # The Tuesday and Wednesday pair has no preference, so the later day takes it.
    assert set(failures) == {MON, ("wed", "breakfast")}
    assert checks_of(failures[("wed", "breakfast")]) == ["variety_ingredients"]
    assert "the tue recipe 'Two'" in failures[("wed", "breakfast")][0].message


def test_a_cuisine_outside_the_list_stays_on_its_own_recipe() -> None:
    recipes = pair(["a"], ["x"], cuisines=("Thai", "Japanese"))

    failures = check_variety(recipes, plan_rules(), settings(), prefer=[{TUE}])

    assert checks_of(failures[MON]) == ["cuisine_not_in_plan"]
    assert TUE not in failures


def test_a_varied_week_has_no_failures() -> None:
    cuisines = ["South Indian", "Japanese", "Mediterranean"]
    days: list[DayKey] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    recipes = [
        checked(
            [f"food_{n}", f"food_{n + 7}", "salt"],
            day=day,
            title=f"Dish {n}",
            cuisine=cuisines[n % 3],
        )
        for n, day in enumerate(days)
    ]

    assert check_variety(recipes, plan_rules(), settings()) == {}


# No waste. A shopping window runs from a shopping day to the day before the next one, wrapping
# past Sunday, and its failures are keyed by (ingredient id, first day of the window).


def spinach_cat() -> Catalogue:
    return catalogue(
        item("spinach", pack_g=250, keeps_days_opened=3, name="Spinach"),
        item("rice", pack_g=1000, keeps_days_opened=365),
        item("salt"),
    )


def one_trip() -> PlanRules:
    """One shopping day, Monday: the whole week is a single window."""
    return plan_rules(shopping=["mon"])


def on(day: DayKey, food_id: str, grams: float) -> CheckedRecipe:
    recipe = checked([food_id], day=day, title=f"{food_id} on {day}")
    recipe.draft.ingredients = [DraftIngredient(id=food_id, grams=grams)]
    return recipe


def week_using(**grams: float) -> list[CheckedRecipe]:
    """One recipe per ingredient from Monday on, so a week's total for an id is the grams given."""
    days: list[DayKey] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return [
        on(day, food_id, total) for day, (food_id, total) in zip(days, grams.items(), strict=False)
    ]


def test_no_waste_flags_half_used_spinach() -> None:
    failures = check_no_waste(week_using(spinach=100), spinach_cat(), one_trip(), settings())

    assert set(failures) == {("spinach", "mon")}
    failure = failures[("spinach", "mon")]
    assert failure.check == "no_waste"
    assert "Spinach" in failure.message
    assert "mon to sun" in failure.message
    assert "100 g" in failure.message
    assert "250 g" in failure.message
    assert "3 days" in failure.message
    assert "40%" in failure.message


def test_no_waste_ignores_long_life_jar() -> None:
    assert check_no_waste(week_using(rice=50), spinach_cat(), one_trip(), settings()) == {}


@pytest.mark.parametrize(
    ("grams", "flagged"), [(124, True), (124.9, True), (125, False), (400, False)]
)
def test_no_waste_at_half_a_pack(grams: float, flagged: bool) -> None:
    failures = check_no_waste(week_using(spinach=grams), spinach_cat(), one_trip(), settings())

    assert (failures != {}) is flagged


def test_no_waste_adds_a_window_up_across_recipes() -> None:
    recipes = [on("mon", "spinach", 60), on("thu", "spinach", 70)]

    assert check_no_waste(recipes, spinach_cat(), one_trip(), settings()) == {}


def test_no_waste_adds_up_an_id_listed_twice_in_one_recipe() -> None:
    recipe = checked(["spinach"])
    recipe.draft.ingredients = [
        DraftIngredient(id="spinach", grams=70),
        DraftIngredient(id="spinach", grams=70),
    ]

    assert check_no_waste([recipe], spinach_cat(), one_trip(), settings()) == {}


def test_no_waste_shelf_life_edge() -> None:
    keeps_6 = catalogue(item("herb", pack_g=100, keeps_days_opened=6))
    keeps_7 = catalogue(item("herb", pack_g=100, keeps_days_opened=7))

    assert ("herb", "mon") in check_no_waste(week_using(herb=10), keeps_6, one_trip(), settings())
    assert check_no_waste(week_using(herb=10), keeps_7, one_trip(), settings()) == {}


def test_no_waste_leaves_out_items_with_no_pack_or_shelf_life() -> None:
    cat = catalogue(
        item("pinch_spice"),
        item("no_pack", keeps_days_opened=2),
        item("no_life", pack_g=100),
    )
    recipes = week_using(pinch_spice=1, no_pack=1, no_life=1)

    assert check_no_waste(recipes, cat, one_trip(), settings()) == {}


def test_no_waste_leaves_out_ids_the_catalogue_does_not_have() -> None:
    assert check_no_waste(week_using(ghost=5), spinach_cat(), one_trip(), settings()) == {}


def test_no_waste_thresholds_come_from_settings() -> None:
    recipes = week_using(spinach=100)

    wider = settings(no_waste_keeps_days=3)
    stricter = settings(no_waste_min_pack_share=0.4)

    assert check_no_waste(recipes, spinach_cat(), one_trip(), wider) == {}
    assert check_no_waste(recipes, spinach_cat(), one_trip(), stricter) == {}


def test_no_waste_reports_every_ingredient_it_flags() -> None:
    cat = catalogue(
        item("spinach", pack_g=250, keeps_days_opened=3),
        item("curd", pack_g=400, keeps_days_opened=5),
    )

    failures = check_no_waste(week_using(spinach=50, curd=100), cat, one_trip(), settings())

    assert set(failures) == {("spinach", "mon"), ("curd", "mon")}


def test_no_waste_message_says_how_much_to_use() -> None:
    failures = check_no_waste(week_using(spinach=100), spinach_cat(), one_trip(), settings())

    assert "at least 125 g" in failures[("spinach", "mon")].message


def test_an_empty_week_wastes_nothing() -> None:
    assert check_no_waste([], spinach_cat(), one_trip(), settings()) == {}


def test_one_shopping_day_makes_the_whole_week_one_window_starting_that_day() -> None:
    """Monday and Sunday both fall in the window that starts on Wednesday, 7 days long."""
    rules = plan_rules(shopping=["wed"])

    enough = check_no_waste(
        [on("mon", "spinach", 60), on("sun", "spinach", 70)], spinach_cat(), rules, settings()
    )
    short = check_no_waste(
        [on("mon", "spinach", 50), on("sun", "spinach", 50)], spinach_cat(), rules, settings()
    )

    assert enough == {}
    assert set(short) == {("spinach", "wed")}
    assert "wed to tue" in short[("spinach", "wed")].message


def test_two_shopping_days_judge_each_window_on_its_own() -> None:
    """Shopping on Tuesday and Sunday: Tue to Sat uses 200 g and passes, Sun to Mon uses 100 g."""
    rules = plan_rules(shopping=["sun", "tue"])
    recipes = [
        on("sun", "spinach", 50),
        on("mon", "spinach", 50),
        on("wed", "spinach", 100),
        on("fri", "spinach", 100),
    ]

    failures = check_no_waste(recipes, spinach_cat(), rules, settings())

    assert set(failures) == {("spinach", "sun")}
    message = failures[("spinach", "sun")].message
    assert "sun to mon" in message
    assert "100 g" in message
    assert "250 g" in message


def test_the_days_before_the_first_shopping_day_belong_to_the_last_window() -> None:
    """Shopping on Wednesday and Saturday: Saturday's window runs Sat, Sun, Mon, Tue."""
    rules = plan_rules(shopping=["wed", "sat"])

    carried = check_no_waste(
        [on("sat", "spinach", 30), on("tue", "spinach", 100)], spinach_cat(), rules, settings()
    )
    short = check_no_waste(
        [on("sun", "spinach", 40), on("mon", "spinach", 40)], spinach_cat(), rules, settings()
    )

    assert carried == {}
    assert set(short) == {("spinach", "sat")}
    assert "sat to tue" in short[("spinach", "sat")].message


def test_an_ingredient_can_fail_in_two_windows() -> None:
    rules = plan_rules(shopping=["mon", "thu"])
    recipes = [on("tue", "spinach", 50), on("fri", "spinach", 50)]

    failures = check_no_waste(recipes, spinach_cat(), rules, settings())

    assert set(failures) == {("spinach", "mon"), ("spinach", "thu")}
    assert "mon to wed" in failures[("spinach", "mon")].message
    assert "thu to sun" in failures[("spinach", "thu")].message


def test_a_window_that_uses_none_of_an_ingredient_is_not_flagged() -> None:
    rules = plan_rules(shopping=["mon", "thu"])

    assert check_no_waste([on("tue", "spinach", 200)], spinach_cat(), rules, settings()) == {}


def test_the_order_and_repeats_of_shopping_days_do_not_matter() -> None:
    recipes = [on("sun", "spinach", 50), on("wed", "spinach", 100)]
    plain = check_no_waste(recipes, spinach_cat(), plan_rules(shopping=["tue", "sun"]), settings())

    shuffled = check_no_waste(
        recipes, spinach_cat(), plan_rules(shopping=["sun", "tue", "sun"]), settings()
    )

    assert set(plain) == set(shuffled) == {("spinach", "sun"), ("spinach", "tue")}


def test_a_window_of_one_day_is_named_by_that_day() -> None:
    rules = plan_rules(shopping=["mon", "tue"])

    failures = check_no_waste([on("mon", "spinach", 100)], spinach_cat(), rules, settings())

    message = failures[("spinach", "mon")].message
    assert "window mon uses" in message
