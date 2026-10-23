"""Build data/foods/ingredients.csv from USDA FoodData Central records picked by hand.

Usage: `make foods`. USDA_FDC_API_KEY must be set (see .env.example) whenever a record is not
in .cache/usda/ yet; cached records rebuild everything offline. `--refresh` fetches every
record again.

data_build/food_map.csv names one FDC record per food. The script fetches each record, maps it
with nutrition_mcp.source_mapping, the module nutrition-mcp's own build uses, so units and the
carbohydrate definition match its bundled data, and writes data/foods/ingredients.csv in
nutrition-mcp's custom-dataset format. It also writes data_build/portions_review.csv with every
household portion FDC lists for those records, for the bundled rows that carry a catalogue
measure, and for the few records fetched only for a portion, so each measure in
data/catalogue.yaml can be checked against its source. Read the printed review table against
each record before committing: the right food, raw and not cooked, the part that is eaten.
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import httpx
import yaml
from nutrition_mcp.loader import INGREDIENT_COLUMNS, bundled_dir
from nutrition_mcp.models import Ingredient
from nutrition_mcp.source_mapping import usda_density_g_per_ml, usda_to_nutrients

ROOT = Path(__file__).resolve().parent.parent
USDA_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food/{fdc_id}"
KEY_VARIABLE = "USDA_FDC_API_KEY"

MAP_COLUMNS = ["id", "name", "aliases", "diet", "fdc_id", "notes"]
PORTION_COLUMNS = ["id", "role", "fdc_id", "data_type", "record", "portion", "gram_weight"]

# One source per FDC dataset, because each has its own version. nutrition-mcp's bundled data
# already uses "usda_fdc", and source ids must be unique across every loaded folder. Branded
# and survey records are left out: a branded label is one product, and a survey record is
# computed from SR Legacy records that can be used directly.
SOURCE_IDS = {"SR Legacy": "usda_fdc_sr_legacy", "Foundation": "usda_fdc_foundation"}

# Foods poured or spooned as liquids. Only these get a density, which gives them ml, tbsp, tsp,
# cup and glass in nutrition-mcp, as its own build does for milk, oils and ghee.
LIQUIDS = frozenset({"honey", "olive_oil", "skimmed_milk", "soy_sauce"})

# Records fetched only for a household portion that the food's own record lacks. Each is a
# survey record built from the same SR Legacy record, so the food is the same.
EXTRA_PORTION_RECORDS: dict[str, str] = {
    # The SR Legacy chia record lists only 1 oz; this one has a cup.
    "chia_seeds": "2707590",
    # The SR Legacy whey record lists only 1/3 cup; this one has scoops.
    "whey_protein": "2710742",
}


class BuildError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Paths:
    food_map: Path
    catalogue: Path
    foods_dir: Path
    portions: Path
    cache: Path

    @classmethod
    def under(cls, root: Path) -> Self:
        return cls(
            food_map=root / "data_build" / "food_map.csv",
            catalogue=root / "data" / "catalogue.yaml",
            foods_dir=root / "data" / "foods",
            portions=root / "data_build" / "portions_review.csv",
            cache=root / ".cache" / "usda",
        )


PATHS = Paths.under(ROOT)


@dataclass(frozen=True, slots=True)
class Built:
    ingredient: Ingredient
    data_type: str
    description: str


def read_map(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != MAP_COLUMNS:
            raise BuildError(f"{path}: header must be {','.join(MAP_COLUMNS)}")
        rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    seen: set[str] = set()
    for line, row in enumerate(rows, start=2):
        if row["id"] in seen:
            raise BuildError(f"{path}:{line}: duplicate id {row['id']!r}")
        seen.add(row["id"])
        if not row["fdc_id"].isdigit():
            raise BuildError(f"{path}:{line}: fdc_id must be an FDC ID, got {row['fdc_id']!r}")
    return rows


def bundled_rows() -> dict[str, dict[str, str]]:
    """nutrition-mcp's bundled ingredients, by id, as its CSV has them."""
    with (bundled_dir() / "ingredients.csv").open(encoding="utf-8", newline="") as handle:
        return {row["id"]: row for row in csv.DictReader(handle)}


def check_no_clash(rows: Sequence[Mapping[str, str]], bundled_ids: set[str]) -> None:
    # nutrition-mcp refuses to start on a repeated id; failing here says why before any fetch.
    clashes = sorted(row["id"] for row in rows if row["id"] in bundled_ids)
    if clashes:
        raise BuildError(
            "ids already in nutrition-mcp's bundled data: "
            + ", ".join(f"'{food_id}'" for food_id in clashes)
        )


def measured_bundled_records(catalogue: Path) -> tuple[dict[str, str], list[str]]:
    """FDC ids of the bundled rows that carry a catalogue measure, and the ids with none.

    A bundled row from CoFID has no FDC record, so its measure cannot be checked this way.
    """
    entries = yaml.safe_load(catalogue.read_text(encoding="utf-8")) or []
    bundled = bundled_rows()
    records: dict[str, str] = {}
    without: list[str] = []
    for entry in entries:
        food_id = entry.get("id")
        if not entry.get("measures") or food_id not in bundled:
            continue
        row = bundled[food_id]
        if row["source_id"] == "usda_fdc":
            records[food_id] = row["source_record"]
        else:
            without.append(food_id)
    return records, without


def _retrying_get(client: httpx.Client, url: str) -> httpx.Response:
    # The FDC API answers 503 now and then; a short wait is usually enough.
    for attempt in range(4):
        try:
            response = client.get(url)
        except httpx.TransportError:
            if attempt == 3:
                raise
        else:
            if response.status_code < 500 or attempt == 3:
                response.raise_for_status()
                return response
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def fetch_usda(client: httpx.Client, fdc_id: str, cache: Path, refresh: bool) -> dict[str, Any]:
    cached = cache / f"{fdc_id}.json"
    if cached.exists() and not refresh:
        data: dict[str, Any] = json.loads(cached.read_text(encoding="utf-8"))
    else:
        response = _retrying_get(client, USDA_FOOD_URL.format(fdc_id=fdc_id))
        data = response.json()
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_text(response.text, encoding="utf-8")
    if str(data.get("fdcId")) != fdc_id:
        raise BuildError(f"{cached}: holds FDC ID {data.get('fdcId')}, expected {fdc_id}")
    return data


def build_row(row: Mapping[str, str], food: Mapping[str, Any]) -> Built:
    data_type = str(food.get("dataType"))
    source_id = SOURCE_IDS.get(data_type)
    if source_id is None:
        raise BuildError(
            f"{row['id']}: FDC {row['fdc_id']} is a {data_type} record; "
            f"use one of {', '.join(SOURCE_IDS)}"
        )
    density = usda_density_g_per_ml(food) if row["id"] in LIQUIDS else None
    # Validating with the dataset model means the written CSV is known to load.
    ingredient = Ingredient.model_validate(
        {
            "id": row["id"],
            "name": row["name"],
            "aliases": [alias.strip() for alias in row["aliases"].split("|") if alias.strip()],
            "diet": row["diet"],
            "source_id": source_id,
            "source_record": row["fdc_id"],
            "per_100g": usda_to_nutrients(food),
            "density_g_per_ml": density,
        }
    )
    return Built(ingredient, data_type, str(food["description"]))


def format_number(value: float | None, places: int = 4) -> str:
    # Fixed places, then trailing zeros dropped: 47.779999 -> "47.78", 328.0 -> "328".
    if value is None:
        return ""
    text = f"{round(value, places):.{places}f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def portion_text(portion: Mapping[str, Any]) -> str:
    """A portion as FDC describes it: "1 cup, sliced", "0.33 cup", "1 scoop, NFS".

    Survey records put the words in portionDescription and a code in modifier. SR Legacy
    records leave the unit "undetermined" and put it in modifier. Foundation records name the
    unit and may add a form in modifier.
    """
    described = str(portion.get("portionDescription") or "").strip()
    if described:
        return described
    unit = str(portion.get("measureUnit", {}).get("name", "")).strip()
    modifier = str(portion.get("modifier") or "").strip()
    amount = portion.get("amount")
    parts = [format_number(float(amount)) if amount is not None else ""]
    if unit and unit != "undetermined":
        parts.append(unit + ("," if modifier else ""))
    if modifier:
        parts.append(modifier)
    return " ".join(part for part in parts if part)


def portion_rows(food_id: str, role: str, food: Mapping[str, Any]) -> list[dict[str, str]]:
    base = {
        "id": food_id,
        "role": role,
        "fdc_id": str(food["fdcId"]),
        "data_type": str(food["dataType"]),
        "record": str(food["description"]),
    }
    portions = sorted(
        food.get("foodPortions", []), key=lambda portion: portion.get("sequenceNumber", 0)
    )
    if not portions:
        return [base | {"portion": "none listed", "gram_weight": ""}]
    return [
        base
        | {
            "portion": portion_text(portion),
            "gram_weight": format_number(float(portion["gramWeight"])),
        }
        for portion in portions
    ]


def write_ingredients(built: Sequence[Built], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(INGREDIENT_COLUMNS)
        for item in built:
            ingredient = item.ingredient
            cells = {
                "id": ingredient.id,
                "name": ingredient.name,
                "aliases": "|".join(ingredient.aliases),
                "diet": ingredient.diet,
                "source_id": ingredient.source_id,
                "source_record": ingredient.source_record,
                # USDA has no glycemic index.
                "gi": "",
                "gi_ref": "",
                "density_g_per_ml": format_number(ingredient.density_g_per_ml),
            }
            for key, value in ingredient.per_100g:
                cells[key] = format_number(value)
            writer.writerow([cells[column] for column in INGREDIENT_COLUMNS])


def write_portions(rows: Sequence[Mapping[str, str]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PORTION_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def check_sources_listed(built: Sequence[Built], sources_path: Path) -> None:
    listed = {entry["id"] for entry in yaml.safe_load(sources_path.read_text("utf-8")) or []}
    missing = sorted({item.ingredient.source_id for item in built} - listed)
    if missing:
        raise BuildError(f"{sources_path}: add an entry for {', '.join(missing)}")


def print_review(built: Sequence[Built]) -> None:
    header = ("id", "record", "description", "kcal", "protein", "density", "gaps")
    rows = []
    for item in built:
        values = item.ingredient.per_100g
        gaps = [key for key, value in values if value is None]
        rows.append(
            (
                item.ingredient.id,
                item.ingredient.source_record,
                f"{item.description} [{item.data_type}]",
                format_number(values.kcal, 1),
                format_number(values.protein_g, 1),
                format_number(item.ingredient.density_g_per_ml, 3),
                ", ".join(gaps),
            )
        )
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        cells = (cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        print("  ".join(cells).rstrip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--refresh", action="store_true", help="fetch every FDC record again")
    args = parser.parse_args(argv)
    paths = PATHS

    rows = read_map(paths.food_map)
    unknown = LIQUIDS - {row["id"] for row in rows}
    if unknown:
        raise BuildError(f"LIQUIDS names ids not in the map: {', '.join(sorted(unknown))}")
    check_no_clash(rows, set(bundled_rows()))
    bundled_records, no_record = measured_bundled_records(paths.catalogue)

    wanted = [
        *((row["id"], "food_file", row["fdc_id"]) for row in rows),
        *((food_id, "bundled", fdc_id) for food_id, fdc_id in bundled_records.items()),
        *((food_id, "portion_only", fdc_id) for food_id, fdc_id in EXTRA_PORTION_RECORDS.items()),
    ]
    key = os.environ.get(KEY_VARIABLE)
    # Cached records rebuild the files offline; only a fetch needs the key.
    uncached = {
        fdc_id
        for _, _, fdc_id in wanted
        if args.refresh or not (paths.cache / f"{fdc_id}.json").exists()
    }
    if uncached and not key:
        print(
            f"{KEY_VARIABLE} is not set and {len(uncached)} FDC records need fetching. "
            "See .env.example.",
            file=sys.stderr,
        )
        return 1

    built: list[Built] = []
    portions: list[dict[str, str]] = []
    publications: dict[str, set[str]] = defaultdict(set)
    by_id = {row["id"]: row for row in rows}
    # The key goes in a header, never the URL, so it cannot end up in logs or error messages.
    with httpx.Client(headers={"X-Api-Key": key or ""}, timeout=60) as client:
        for food_id, role, fdc_id in wanted:
            food = fetch_usda(client, fdc_id, paths.cache, args.refresh)
            portions.extend(portion_rows(food_id, role, food))
            if role == "food_file":
                built.append(build_row(by_id[food_id], food))
                publications[str(food["dataType"])].add(str(food["publicationDate"]))

    check_sources_listed(built, paths.foods_dir / "sources.yaml")
    write_ingredients(built, paths.foods_dir / "ingredients.csv")
    write_portions(portions, paths.portions)
    print_review(built)
    print(f"\nWrote {len(built)} foods to {paths.foods_dir / 'ingredients.csv'}")
    print(f"Wrote {len(portions)} portions to {paths.portions}")
    for data_type, dates in sorted(publications.items()):
        print(f"FDC {data_type} publication dates: {', '.join(sorted(dates))}")
    if no_record:
        print(f"Catalogue measures with no FDC record to check against: {', '.join(no_record)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
