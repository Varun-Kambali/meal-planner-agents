import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from meal_planner.rules import PlanRules, expand

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "plans" / "office_weekday.json"

Plan = dict[str, Any]


def load_plan() -> Plan:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def with_shares(plan: Plan, *, kcal: list[float], protein: list[float]) -> Plan:
    """Keep the first len(kcal) slots and give them these shares."""
    plan = copy.deepcopy(plan)
    plan["slots"] = plan["slots"][: len(kcal)]
    for slot, kcal_share, protein_share in zip(plan["slots"], kcal, protein, strict=True):
        slot["kcal_share"] = kcal_share
        slot["protein_share"] = protein_share
    return plan


def set_at(plan: Plan, path: tuple[str | int, ...], value: Any) -> Plan:
    plan = copy.deepcopy(plan)
    node: Any = plan
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return plan


def test_fixture_plan_loads() -> None:
    rules = PlanRules.model_validate(load_plan())

    assert rules.name == "Office weekday"
    assert [d.id for d in rules.day_types] == ["training", "rest"]
    assert [s.id for s in rules.slots] == ["breakfast", "lunch", "snack", "dinner"]
    assert rules.week["tue"] == "rest"
    assert rules.slots[0].eat_at.hour == 8


def test_kcal_shares_must_sum_to_one() -> None:
    plan = with_shares(load_plan(), kcal=[0.3, 0.3, 0.3], protein=[0.4, 0.3, 0.3])

    with pytest.raises(ValidationError, match="kcal_share") as err:
        PlanRules.model_validate(plan)

    assert "protein_share" not in str(err.value)


def test_protein_shares_must_sum_to_one() -> None:
    plan = with_shares(load_plan(), kcal=[0.4, 0.3, 0.3], protein=[0.3, 0.3, 0.3])

    with pytest.raises(ValidationError, match="protein_share") as err:
        PlanRules.model_validate(plan)

    assert "kcal_share" not in str(err.value)


@pytest.mark.parametrize(
    ("last_share", "ok"),
    [(0.2505, True), (0.2495, True), (0.2515, False), (0.2485, False)],
)
def test_share_sum_tolerance_edges(last_share: float, ok: bool) -> None:
    shares = [0.25, 0.25, 0.25, last_share]
    plan = with_shares(load_plan(), kcal=shares, protein=shares)

    if ok:
        PlanRules.model_validate(plan)
    else:
        with pytest.raises(ValidationError, match="kcal_share"):
            PlanRules.model_validate(plan)


def test_week_needs_all_seven_days() -> None:
    plan = load_plan()
    del plan["week"]["sun"]

    with pytest.raises(ValidationError, match="sun"):
        PlanRules.model_validate(plan)


def test_week_rejects_a_day_that_is_not_a_day() -> None:
    plan = set_at(load_plan(), ("week", "funday"), "rest")

    with pytest.raises(ValidationError, match="funday"):
        PlanRules.model_validate(plan)


def test_week_names_known_day_type() -> None:
    plan = set_at(load_plan(), ("week", "wed"), "cheat_day")

    with pytest.raises(ValidationError, match="cheat_day"):
        PlanRules.model_validate(plan)


def test_slot_ids_unique() -> None:
    plan = set_at(load_plan(), ("slots", 3, "id"), "lunch")

    with pytest.raises(ValidationError, match="slots: ids must be unique, repeated: lunch"):
        PlanRules.model_validate(plan)


def test_day_type_ids_unique() -> None:
    plan = load_plan()
    plan["day_types"][1]["id"] = "training"
    # Point the whole week at the surviving id, so the repeated id is the only problem.
    plan["week"] = dict.fromkeys(plan["week"], "training")

    with pytest.raises(ValidationError, match="day_types: ids must be unique, repeated: training"):
        PlanRules.model_validate(plan)


@pytest.mark.parametrize(
    ("nutrient", "field"),
    [
        ("kcal", "kcal"),
        ("protein_g", "protein_g"),
        ("sat_fat_g", "sat_fat_g_max"),
        ("fibre_g", "fibre_g_min"),
        ("carbs_available_g", "carbs_g"),
        ("fat_g", "fat_g"),
    ],
)
def test_an_extra_target_may_not_repeat_a_target_the_day_type_already_has(
    nutrient: str, field: str
) -> None:
    plan = set_at(load_plan(), ("day_types", 1, "extra", 0, "nutrient"), nutrient)

    with pytest.raises(ValidationError) as err:
        PlanRules.model_validate(plan)

    (error,) = err.value.errors()
    assert error["loc"] == ("day_types", 1, "extra")
    assert error["msg"] == (
        f"Value error, extra: '{nutrient}' repeats a target the day type already has; "
        f"set it in {field} and take it out of extra"
    )


def test_every_repeated_core_target_is_named_at_once() -> None:
    extra = [
        {"nutrient": "kcal", "kind": "max", "value": 2500},
        {"nutrient": "iron_mg", "kind": "min", "value": 15},
        {"nutrient": "fat_g", "kind": "max", "value": 90},
    ]
    plan = set_at(load_plan(), ("day_types", 0, "extra"), extra)

    with pytest.raises(ValidationError) as err:
        PlanRules.model_validate(plan)

    (error,) = err.value.errors()
    assert "'kcal'" in error["msg"]
    assert "'fat_g'" in error["msg"]
    assert "iron_mg" not in error["msg"]


def test_an_extra_target_may_not_be_listed_twice_for_one_nutrient() -> None:
    extra = [
        {"nutrient": "calcium_mg", "kind": "min", "value": 1000},
        {"nutrient": "calcium_mg", "kind": "max", "value": 2500},
    ]
    plan = set_at(load_plan(), ("day_types", 0, "extra"), extra)

    with pytest.raises(ValidationError) as err:
        PlanRules.model_validate(plan)

    (error,) = err.value.errors()
    assert error["loc"] == ("day_types", 0, "extra")
    assert error["msg"] == (
        "Value error, extra: 'calcium_mg' is listed more than once; "
        "a day type has one extra target for each nutrient"
    )


def test_extra_targets_for_other_nutrients_are_accepted() -> None:
    rules = PlanRules.model_validate(load_plan())

    assert [t.nutrient for t in rules.day_types[0].extra] == ["calcium_mg", "sodium_mg"]


@pytest.mark.parametrize(
    "path",
    [
        ("surprise",),
        ("day_types", 0, "surprise"),
        ("day_types", 0, "extra", 0, "surprise"),
        ("slots", 0, "surprise"),
        ("food", "surprise"),
    ],
)
def test_unknown_field_rejected(path: tuple[str | int, ...]) -> None:
    plan = set_at(load_plan(), path, 1)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlanRules.model_validate(plan)


@pytest.mark.parametrize(
    ("path", "value", "field"),
    [
        (("version",), 2, "version"),
        (("name",), "", "name"),
        (("name",), "x" * 61, "name"),
        (("day_types", 0, "id"), "Training Day", "id"),
        (("day_types", 0, "kcal"), 0, "kcal"),
        (("day_types", 0, "protein_g"), -1, "protein_g"),
        (("day_types", 0, "fibre_g_min"), 0, "fibre_g_min"),
        (("day_types", 0, "extra", 0, "nutrient"), "vit_k_ug", "nutrient"),
        (("day_types", 0, "extra", 0, "kind"), "exactly", "kind"),
        (("day_types", 0, "extra", 0, "value"), 0, "value"),
        (("slots", 0, "kcal_share"), 0, "kcal_share"),
        (("slots", 0, "kcal_share"), 1.1, "kcal_share"),
        (("slots", 0, "protein_share"), -0.1, "protein_share"),
        (("slots", 0, "style"), "", "style"),
        (("slots", 0, "style"), "x" * 81, "style"),
        (("slots", 0, "cook_when"), "tomorrow", "cook_when"),
        (("slots", 0, "eat_at"), "breakfast time", "eat_at"),
        (("slots", 0, "max_active_min"), 0, "max_active_min"),
        (("slots", 0, "max_active_min"), 241, "max_active_min"),
        (("slots", 0, "max_total_min"), 1441, "max_total_min"),
        (("gear",), [], "gear"),
        (("gear",), ["flamethrower"], "gear"),
        (("food", "diet"), "carnivore", "diet"),
        (("food", "exclude_allergens"), ["kale"], "exclude_allergens"),
        (("food", "cuisines"), [], "cuisines"),
        (("food", "cuisines"), [f"c{i}" for i in range(11)], "cuisines"),
        (("food", "cuisines"), ["x" * 41], "cuisines"),
        (("shopping_days",), [], "shopping_days"),
        (("shopping_days",), ["mon", "caturday"], "shopping_days"),
        (("slots",), [], "slots"),
        (("day_types",), [], "day_types"),
    ],
)
def test_bad_value_names_the_field(path: tuple[str | int, ...], value: Any, field: str) -> None:
    with pytest.raises(ValidationError) as err:
        PlanRules.model_validate(set_at(load_plan(), path, value))

    # Judged by where the error points, not by its text: a rule that runs later can mention
    # the same word and make a missing bound look guarded.
    assert any(field in e["loc"] for e in err.value.errors())


def test_at_most_eight_slots() -> None:
    plan = load_plan()
    # Shares that add up, so the number of slots is the only problem.
    plan["slots"] = [
        {**plan["slots"][0], "id": f"s{i}", "kcal_share": 1 / 9, "protein_share": 1 / 9}
        for i in range(9)
    ]

    with pytest.raises(ValidationError, match="at most 8 items") as err:
        PlanRules.model_validate(plan)

    assert [(e["type"], e["loc"]) for e in err.value.errors()] == [("too_long", ("slots",))]


def test_at_most_four_day_types() -> None:
    plan = load_plan()
    plan["day_types"] = [{**plan["day_types"][0], "id": f"d{i}"} for i in range(5)]
    # Every day uses a defined day type, so the number of day types is the only problem.
    plan["week"] = dict.fromkeys(plan["week"], "d0")

    with pytest.raises(ValidationError, match="at most 4 items") as err:
        PlanRules.model_validate(plan)

    assert [(e["type"], e["loc"]) for e in err.value.errors()] == [("too_long", ("day_types",))]


def test_expand_gives_seven_times_slots() -> None:
    plan = load_plan()
    # A week written out of order must still expand Monday first.
    plan["week"] = dict(reversed(plan["week"].items()))

    specs = expand(PlanRules.model_validate(plan))

    assert len(specs) == 28
    assert [(s.day, s.slot.id) for s in specs[:5]] == [
        ("mon", "breakfast"),
        ("mon", "lunch"),
        ("mon", "snack"),
        ("mon", "dinner"),
        ("tue", "breakfast"),
    ]
    assert (specs[-1].day, specs[-1].slot.id) == ("sun", "dinner")
    assert [s.day for s in specs[::4]] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def test_expand_uses_day_type_targets() -> None:
    specs = {(s.day, s.slot.id): s for s in expand(PlanRules.model_validate(load_plan()))}

    rest_lunch = specs[("tue", "lunch")]
    assert rest_lunch.day_type == "rest"
    assert rest_lunch.kcal == pytest.approx(675)
    assert rest_lunch.protein_g == pytest.approx(39)

    training_snack = specs[("mon", "snack")]
    assert training_snack.day_type == "training"
    assert training_snack.kcal == pytest.approx(397.5)
    assert training_snack.protein_g == pytest.approx(15)
