"""The household measure shown beside an ingredient's grams, at the edges of its rule."""

import pytest
from week_helpers import item

from meal_planner.catalogue import Catalogue
from meal_planner.measures import (
    COUNTED_UNITS,
    MEASURE_TOLERANCE,
    household_measure,
    recipe_measures,
)
from meal_planner.recipes import DraftRecipe, HouseholdMeasure


def measure(amount: float, unit: str) -> HouseholdMeasure:
    return HouseholdMeasure(amount=amount, unit=unit)


def test_the_tolerance_is_15_percent() -> None:
    assert MEASURE_TOLERANCE == 0.15


def test_the_counted_units_are_the_piece_like_units_in_the_data() -> None:
    assert {"piece", "roti", "clove", "leaf", "slice"} == COUNTED_UNITS


def test_grams_that_are_a_whole_number_of_a_unit_get_it() -> None:
    assert household_measure(100, {"g": 1.0, "piece": 50.0}) == measure(2, "piece")


def test_a_measure_exactly_15_percent_over_the_grams_is_given() -> None:
    # 100 g is 0.87 of a 115 g piece, which rounds to 1 piece: 115 g, 15% over.
    assert household_measure(100, {"piece": 115.0}) == measure(1, "piece")


def test_a_measure_just_over_15_percent_over_the_grams_is_not() -> None:
    assert household_measure(100, {"piece": 115.5}) is None


def test_a_measure_exactly_15_percent_under_the_grams_is_given() -> None:
    # 100 g is 1.18 pieces of 85 g, which rounds to 1 piece: 85 g, 15% under.
    assert household_measure(100, {"piece": 85.0}) == measure(1, "piece")


def test_a_measure_just_over_15_percent_under_the_grams_is_not() -> None:
    assert household_measure(100, {"piece": 84.9}) is None


def test_counted_units_round_to_the_nearest_half() -> None:
    assert household_measure(75, {"piece": 50.0}) == measure(1.5, "piece")
    # 62 g is 1.24 pieces: the nearest half is 1 piece, 50 g, which is 19% under.
    assert household_measure(62, {"piece": 50.0}) is None


def test_other_units_round_to_the_nearest_quarter() -> None:
    # The same 1.24 of a unit is 1.25 cups, 62.5 g.
    assert household_measure(62, {"cup": 50.0}) == measure(1.25, "cup")
    assert household_measure(10, {"tbsp": 13.0}) == measure(0.75, "tbsp")


def test_a_tie_rounds_up() -> None:
    # 2.25 pieces: 2 pieces and 2.5 pieces are both within 15%; the half goes up.
    assert household_measure(22.5, {"piece": 10.0}) == measure(2.5, "piece")
    # 1.125 cups: 1 cup and 1.25 cups are both within 15%; the eighth goes up.
    assert household_measure(112.5, {"cup": 100.0}) == measure(1.25, "cup")


def test_a_unit_that_rounds_to_0_is_skipped() -> None:
    assert household_measure(0.6, {"tsp": 5.0}) is None
    # A cup of 240 g rounds to 0 cups for 5 g, so the next unit is tried.
    assert household_measure(5, {"cup": 240.0, "tsp": 5.0}) == measure(1, "tsp")


def test_of_the_units_that_fit_the_biggest_with_at_least_half_of_one_wins() -> None:
    # 1 tbsp and 3 tsp both fit 13 g; the tablespoon holds more, in either order.
    assert household_measure(13, {"tbsp": 13.0, "tsp": 4.33}) == measure(1, "tbsp")
    assert household_measure(13, {"tsp": 4.33, "tbsp": 13.0}) == measure(1, "tbsp")


def test_a_liquid_is_measured_in_the_biggest_unit_that_fits() -> None:
    # 200 g of milk: 13 tbsp, 38.75 tsp, 3/4 cup and 3/4 glass all fit; the glass is biggest.
    milk = {"g": 1.0, "ml": 1.03, "tbsp": 15.47, "tsp": 5.16, "cup": 247.51, "glass": 257.83}
    assert household_measure(200, milk) == measure(0.75, "glass")


def test_an_amount_of_exactly_half_a_unit_counts() -> None:
    # 1/2 cup is 100 g; 6.75 tbsp fit too, but the cup is bigger and reaches 1/2.
    assert household_measure(100, {"tbsp": 15.0, "cup": 200.0}) == measure(0.5, "cup")


def test_a_unit_that_fits_at_under_half_of_one_gives_way_to_one_that_reaches_it() -> None:
    # 1/4 cup fits 100 g, but under 1/2 of a cup; 6.75 tbsp fit and reach it.
    assert household_measure(100, {"cup": 400.0, "tbsp": 15.0}) == measure(6.75, "tbsp")


def test_when_no_unit_that_fits_reaches_half_the_smallest_wins() -> None:
    # 1/4 cup and 1/4 glass both fit 100 g, neither reaches 1/2: the cup holds less.
    assert household_measure(100, {"glass": 410.0, "cup": 400.0}) == measure(0.25, "cup")


def test_a_tie_keeps_the_order_given() -> None:
    assert household_measure(240, {"cup": 240.0, "mug": 240.0}) == measure(1, "cup")
    assert household_measure(240, {"mug": 240.0, "cup": 240.0}) == measure(1, "mug")
    # Below 1/2 too.
    assert household_measure(60, {"cup": 240.0, "mug": 240.0}) == measure(0.25, "cup")


def test_a_unit_that_does_not_fit_gives_way_to_one_that_does() -> None:
    # 5 g of oil is 0.36 tbsp, which rounds to 0.25 tbsp, 3.5 g: 31% under. 1 tsp is 4.6 g.
    oil = {"g": 1.0, "ml": 0.92, "tbsp": 13.82, "tsp": 4.61, "cup": 221.14, "glass": 230.35}
    assert household_measure(5, oil) == measure(1, "tsp")


def test_grams_and_millilitres_are_never_a_household_measure() -> None:
    assert household_measure(100, {"g": 1.0}) is None
    assert household_measure(103, {"g": 1.0, "ml": 1.03}) is None


def test_no_unit_that_fits_is_no_measure() -> None:
    assert household_measure(2, {"g": 1.0, "piece": 6.0}) is None
    assert household_measure(100, {}) is None


def draft(*ingredients: tuple[str, float]) -> DraftRecipe:
    return DraftRecipe.model_validate(
        {
            "day": "mon",
            "title": "Egg and rice",
            "cuisine": "South Indian",
            "ingredients": [{"id": food_id, "grams": grams} for food_id, grams in ingredients],
            "steps": ["Cook the brown_rice.", "Boil the egg."],
            "active_min": 10,
            "total_min": 20,
            "gear": ["stovetop_pan"],
            "cook_when": "fresh",
            "store": "Eat it fresh.",
            "swap": "Use white rice.",
            "fits": "Rice and an egg.",
        }
    )


def catalogue_with_measures() -> Catalogue:
    egg = item("egg").model_copy(update={"measures": {"piece": 50.0}})
    rice = item("brown_rice").model_copy(update={"measures": {"cup": 185.0}})
    salt = item("salt")
    return Catalogue({food.id: food for food in (egg, rice, salt)})


def test_a_recipe_has_one_measure_for_each_ingredient_in_its_order() -> None:
    recipe = draft(("brown_rice", 185), ("egg", 100), ("salt", 1), ("egg", 60))

    assert recipe_measures(recipe, catalogue_with_measures()) == [
        measure(1, "cup"),
        measure(2, "piece"),
        # Salt has grams only.
        None,
        # 60 g is 1.2 eggs, which rounds to 1 egg: 50 g is 17% under.
        None,
    ]


def test_a_food_the_catalogue_lacks_has_no_measure() -> None:
    recipe = draft(("egg", 50), ("mystery", 100))

    assert recipe_measures(recipe, catalogue_with_measures()) == [measure(1, "piece"), None]


@pytest.mark.parametrize("amount", [0, -1])
def test_a_measure_is_more_than_nothing(amount: float) -> None:
    with pytest.raises(ValueError):
        HouseholdMeasure(amount=amount, unit="cup")
