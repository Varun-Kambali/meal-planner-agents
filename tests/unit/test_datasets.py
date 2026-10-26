"""The eval datasets: 20 synthetic plans for the recipe eval and the typed meals the reading eval
scores against.

These tests read the files only. tests/integration/test_datasets_live.py checks every label's
grams and every missing name against the real nutrition-mcp.
"""

import os
import re
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from dataset_helpers import PLANS, meal_rows, plan_paths, plans
from dataset_version import DATASETS, VERSION_FILE, dataset_version
from nutrition_mcp.loader import build_registry

from meal_planner.config import Settings
from meal_planner.rules import Diet, PlanRules

# Plans


def test_twenty_plans_validate() -> None:
    paths = plan_paths()

    assert len(paths) == 20
    for path in paths:
        # A plan id is the file name, so it is a slug like every other id in the rules.
        assert re.fullmatch(r"[a-z][a-z0-9_]*", path.stem), path.name
        PlanRules.model_validate_json(path.read_text(encoding="utf-8"))
    names = [rules.name for _, rules in plans()]
    assert len(set(names)) == len(names)


def test_plans_cover_every_diet_and_three_or_more_gear_sets() -> None:
    rules = [r for _, r in plans()]

    assert {r.food.diet for r in rules} == set(get_args(Diet))
    assert len({frozenset(r.gear) for r in rules}) >= 3


# Regional cuisines of India, as the plans name them.
INDIAN_REGIONAL = {
    "andhra",
    "bengali",
    "chettinad",
    "goan",
    "gujarati",
    "kerala",
    "konkani",
    "maharashtrian",
    "odia",
    "punjabi",
    "rajasthani",
    "south indian",
    "tamil",
}


def test_plans_cover_day_types_slots_times_allergens_and_cuisines() -> None:
    rules = [r for _, r in plans()]
    active = [slot.max_active_min for r in rules for slot in r.slots]
    cuisines = {c.casefold() for r in rules for c in r.food.cuisines}

    assert {len(r.day_types) for r in rules} == {1, 2, 3, 4}
    assert {len(r.slots) for r in rules} == {3, 4, 5}
    assert (min(active), max(active)) == (5, 45)
    assert sum(1 for r in rules if r.food.exclude_allergens) >= 5
    assert {"japanese", "korean", "mediterranean"} <= cuisines
    assert len(cuisines & INDIAN_REGIONAL) >= 5


def test_two_plans_are_marked_hard_and_the_readme_says_why() -> None:
    hard = [path.stem for path in plan_paths() if path.stem.startswith("hard_")]
    readme = (PLANS / "README.md").read_text(encoding="utf-8")

    assert len(hard) == 2
    for plan_id in hard:
        assert plan_id in readme


def catalogue_ids() -> set[str]:
    rows = yaml.safe_load(Settings().catalogue_path.read_text(encoding="utf-8"))
    return {row["id"] for row in rows}


def test_plan_ids_in_catalogue() -> None:
    known = catalogue_ids()
    unknown = [
        (plan_id, food_id)
        for plan_id, rules in plans()
        for food_id in [*rules.food.liked_foods, *rules.food.exclude_foods]
        if food_id not in known
    ]

    assert unknown == []


# Meals

STATUSES = {"matched", "needs_amount", "unknown_unit"}
ROW_KEYS = {"id", "text", "expected", "missing", "note"}
ITEM_KEYS = {"food_id", "amount", "unit", "grams", "status"}


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def shape_problems(row: dict[str, Any]) -> list[str]:
    """What is wrong with a row's shape, as README.md describes it."""
    problems: list[str] = []
    if set(row) != ROW_KEYS:
        return [f"keys {sorted(row)}"]
    if not isinstance(row["text"], str) or not row["text"].strip():
        problems.append("text is empty")
    elif len(row["text"]) > Settings().max_meal_text_chars:
        problems.append("text is longer than the API takes")
    if not isinstance(row["note"], str) or not row["note"].strip():
        problems.append("note is empty")
    if not all(isinstance(name, str) and name.strip() for name in row["missing"]):
        problems.append("missing holds something that is not a name")
    if not row["expected"] and not row["missing"]:
        problems.append("no food at all")
    for item in row["expected"]:
        food_id = item.get("food_id")
        if set(item) - {"also_accept"} != ITEM_KEYS:
            problems.append(f"{food_id}: keys {sorted(item)}")
            continue
        status = item["status"]
        amount, unit, grams = item["amount"], item["unit"], item["grams"]
        if status == "matched":
            ok = is_number(amount) and isinstance(unit, str) and is_number(grams)
        elif status == "unknown_unit":
            ok = is_number(amount) and isinstance(unit, str) and grams is None
        elif status == "needs_amount":
            ok = amount is None and unit is None and grams is None
        else:
            ok = False
        if not ok:
            problems.append(f"{food_id}: status {status!r} with {amount!r} {unit!r} {grams!r}")
        if "also_accept" in item:
            others = item["also_accept"]
            if not others or food_id in others or not all(isinstance(o, str) for o in others):
                problems.append(f"{food_id}: also_accept must list other food ids")
    return problems


def test_meal_rows_have_the_documented_shape() -> None:
    rows = meal_rows()
    ids = [row["id"] for row in rows]

    assert 45 <= len(rows) <= 55
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"m\d{2}", meal_id) for meal_id in ids)
    assert {row["id"]: shape_problems(row) for row in rows if shape_problems(row)} == {}


def test_meal_labels_resolve() -> None:
    folders = [Path(p) for p in Settings().nutrition_data_dirs.split(os.pathsep) if p]
    known = {food.id for food in build_registry(folders).foods()}
    unknown = [
        (row["id"], food_id)
        for row in meal_rows()
        for item in row["expected"]
        for food_id in [item["food_id"], *item.get("also_accept", [])]
        if food_id not in known
    ]

    assert unknown == []


def test_at_least_8_meals_have_missing_foods() -> None:
    assert sum(1 for row in meal_rows() if row["missing"]) >= 8


def items_with(status: str) -> list[dict[str, Any]]:
    return [item for row in meal_rows() for item in row["expected"] if item["status"] == status]


def test_meals_cover_vague_amounts_unknown_units_metric_units_and_hindi_names() -> None:
    rows = meal_rows()
    units = {item["unit"] for item in items_with("matched")}
    words = {word for row in rows for word in re.findall(r"[a-z]+", row["text"].casefold())}

    assert len(items_with("needs_amount")) >= 10
    assert len(items_with("unknown_unit")) >= 5
    assert {"kg", "litre"} <= units
    assert sum(1 for row in rows if len(row["expected"]) + len(row["missing"]) >= 3) >= 5
    assert {"roti", "dal", "sabzi", "anda"} <= words


# Carried from M3. Each phrase, the food it names and the right answer for it.
CARRIED = {
    "2/3 rotis": ("chapati", "needs_amount", None),
    "half plate idli": ("idli", "matched", 40),
    "half plate rice": ("steamed_rice", "unknown_unit", None),
    "a bowl of dal": ("dal_tadka", "unknown_unit", None),
}


@pytest.mark.parametrize("phrase", CARRIED)
def test_carried_meals_are_labelled_by_what_the_person_meant(phrase: str) -> None:
    food_id, status, grams = CARRIED[phrase]
    (row,) = [row for row in meal_rows() if phrase in row["text"]]
    (item,) = [item for item in row["expected"] if item["food_id"] == food_id]

    assert (item["status"], item["grams"]) == (status, grams)


def test_a_bowl_of_dal_accepts_any_dal_dish() -> None:
    (row,) = [row for row in meal_rows() if "a bowl of dal" in row["text"]]
    (dal,) = [item for item in row["expected"] if item["food_id"] == "dal_tadka"]

    assert {"musur_dal", "cholar_dal", "dal_makhani"} <= set(dal["also_accept"])


# Flax and til wait for Varun's review of their aliases in the food data.
HELD_BACK_IDS = {"flax_seeds", "sesame_seeds"}
HELD_BACK_WORDS = {"flax", "flaxseed", "linseed", "alsi", "agase", "til", "sesame", "ellu"}


def test_no_meal_names_flax_or_til() -> None:
    rows = meal_rows()
    ids = {
        food_id
        for row in rows
        for item in row["expected"]
        for food_id in [item["food_id"], *item.get("also_accept", [])]
    }
    words = {word for row in rows for word in re.findall(r"[a-z]+", row["text"].casefold())}

    assert not ids & HELD_BACK_IDS
    assert not words & HELD_BACK_WORDS


# No real person's data

# Values from a private reference are kept out of the repository. They are listed one per line in
# this local file, which .gitignore keeps out of git: a test that listed them would publish them.
FORBIDDEN_LOCAL = DATASETS / ".forbidden-local.txt"
NAME_SPELLINGS = ("varun", "kambali")


def dataset_texts() -> dict[str, str]:
    """Every committed dataset file by its path, lower case. The local file is not one."""
    return {
        path.relative_to(DATASETS).as_posix(): path.read_text(encoding="utf-8").casefold()
        for path in sorted(DATASETS.rglob("*"))
        if path.is_file() and path != FORBIDDEN_LOCAL
    }


def test_no_real_person_data() -> None:
    found = [
        (name, spelling)
        for name, text in dataset_texts().items()
        for spelling in NAME_SPELLINGS
        if spelling in text
    ]

    assert found == []


def plan_numbers(rules: PlanRules) -> set[float]:
    """Every number a plan sets: day targets, each slot's calories and protein, and times."""
    numbers: set[float] = set()
    for day in rules.day_types:
        numbers |= {day.kcal, day.protein_g, day.carbs_g, day.fat_g}
        numbers |= {day.sat_fat_g_max, day.fibre_g_min, *(t.value for t in day.extra)}
        for slot in rules.slots:
            numbers.add(round(day.kcal * slot.kcal_share))
            numbers.add(round(day.protein_g * slot.protein_share))
    for slot in rules.slots:
        numbers |= {slot.max_active_min, slot.max_total_min}
    return numbers


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def test_no_real_person_data_from_the_local_list() -> None:
    if not FORBIDDEN_LOCAL.exists():
        pytest.skip("local forbidden-values file absent")
    lines = [line.strip() for line in FORBIDDEN_LOCAL.read_text(encoding="utf-8").splitlines()]
    values = [line for line in lines if line and not line.startswith("#")]
    numbers = {float(v.replace(",", "")) for v in values if NUMBER.fullmatch(v)}
    phrases = [v.casefold() for v in values if not NUMBER.fullmatch(v)]

    # Only the file or plan is named, so a failure does not print a value from the list.
    files_with_a_phrase = [
        name for name, text in dataset_texts().items() if any(p in text for p in phrases)
    ]
    plans_with_a_number = [plan_id for plan_id, rules in plans() if plan_numbers(rules) & numbers]

    assert files_with_a_phrase == []
    assert plans_with_a_number == []


# The dataset version


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_dataset_version_hashes_the_plans_and_meals_but_not_the_readmes(tmp_path: Path) -> None:
    write(tmp_path / "plans" / "a.json", "{}")
    write(tmp_path / "meals.jsonl", "{}\n")
    write(tmp_path / "README.md", "about the data")
    first = dataset_version(tmp_path)

    write(tmp_path / "README.md", "about the data, reworded")
    assert dataset_version(tmp_path) == first
    write(tmp_path / "meals.jsonl", '{"id": "m01"}\n')
    second = dataset_version(tmp_path)
    assert second != first
    write(tmp_path / "plans" / "b.json", "{}")
    assert dataset_version(tmp_path) not in (first, second)
    assert re.fullmatch(r"[0-9a-f]{12}", first)


def test_version_file_is_current_else_run_evals_dataset_version_py() -> None:
    written = VERSION_FILE.read_text(encoding="utf-8").strip()

    assert written == dataset_version(), (
        "evals/datasets/VERSION is stale: run `uv run python evals/dataset_version.py`"
    )
