# Eval datasets

| File | What it holds |
|---|---|
| `plans/*.json` | 20 synthetic plans for the recipe eval. `plans/README.md` lists them and says which two are meant to fail. |
| `meals.jsonl` | 53 typed meals for the reading eval, each labelled with the foods, amounts and grams the person meant. |
| `VERSION` | A short hash of the plans and meals, which every results file records. |

Everything here is made up. No plan or meal is a real person's, and `tests/unit/test_datasets.py` checks the files for names and for values from a private plan kept out of the repository.

## meals.jsonl

One JSON object a line:

```json
{"id": "m04", "text": "a bowl of dal with 2 rotis",
 "expected": [
   {"food_id": "dal_tadka", "amount": 1, "unit": "bowl", "grams": null, "status": "unknown_unit",
    "also_accept": ["musur_dal", "cholar_dal", "dal_makhani", "dalma"]},
   {"food_id": "chapati", "amount": 2, "unit": "piece", "grams": 80, "status": "matched"}],
 "missing": [],
 "note": "..."}
```

- `id`: `m01` to `m53`.
- `text`: what the person typed, in plain Indian English, as they would type it.
- `expected`: each food the person ate that nutrition-mcp has, in the order written. A food the text says was not eaten is left out.
  - `food_id`: the nutrition-mcp food the person meant.
  - `status`: what the reader should return for it, using the reader's own statuses (`src/meal_planner/reader.py`):
    - `matched`: the food, an amount and a unit the reader can use are all there.
    - `needs_amount`: the amount is missing, vague ("some", "a little", "a few", "a handful") or a range ("2-3", "2 or 3", and "2/3", which in Indian English means two or three). The person is asked.
    - `unknown_unit`: there is an amount, but neither nutrition-mcp nor the catalogue has the unit for that food (a bowl, a plate of rice, one pav, one cucumber, a katori of curd). The person is asked for a unit.
  - `amount` and `unit`: as the person gave them, written the way the reader writes them: lower case and singular, `g` for every spelling of grams, `kg` and `litre` as typed, and `piece` for a count with no unit word ("2 rotis"). Both are null for `needs_amount`, where the right answer is to ask. For `unknown_unit` they are the amount and the unit that was typed.
  - `grams`: for `matched` only, the grams the reader gets for that food, amount and unit. Null otherwise.
  - `also_accept` (only on some items): other food ids that are right too, for a general name the data has several foods for ("dal", "milk"). For a `matched` item, each gives the same grams within 1% for the same amount and unit, so `grams` stands for all of them.
- `missing`: the names of foods the person ate that are not in nutrition-mcp's data, in the order written, lower case and singular.
- `note`: why the label is what it is.

### How the labels were set

- **By what the person meant, not by what the reader does today.** Three labels name a food the reader cannot reach yet, and the eval counts them against it until the reader or the data changes: `m03` (cooked rice is sixth in a search for "rice", and the reader shows the model five options), `m15` ("rajma" finds the dry bean first, and a katori of rajma at a meal is the curry), and `m52` (a search for "boiled egg" finds nothing, so the reader has to name the food "egg").
- **Grams are never estimates.** A matched label's grams are what `Catalogue.meal_item` and nutrition-mcp give for its food, amount and unit: nutrition-mcp's own unit for the food first, then the catalogue's measure, with kilograms and litres read as grams and millilitres. `tests/integration/test_datasets_live.py` checks every one against the real server, checks that an `unknown_unit` label's unit is one the reader cannot use, and checks that every name in `missing` gets no search result.
- **A missing name finds nothing.** A name that nutrition-mcp's search answers with a different food ("pizza" finds mozzarella through "pizza cheese", "bread" finds pav, "salad" finds lettuce) is left out of the set: no label for it would be right under these rules.
- **No flax or til.** Meals with flax seeds or sesame wait for a review of those foods' aliases in the food data.

### What is in it

53 meals with 87 labelled items: 71 matched, 11 that need an amount and 5 with an unknown unit, plus 19 foods in 12 meals that are not in the data. 44 meals name more than one food. Some use Hindi names (roti, dal, chawal, dahi, doodh, anda, sabzi), and the units include katori, plate, piece, cup, glass, tbsp, tsp, scoop, ml, g, kg and litre. `m04`, `m26` and `m36` use `also_accept`. The four meals carried over from building the reader are `m01` ("2/3 rotis"), `m02` ("half plate idli"), `m03` ("half plate rice") and `m04` ("a bowl of dal"). `m28` holds an instruction inside the typed text, which the reader has to read as data.

## VERSION

`evals/dataset_version.py` hashes `plans/*.json` and `meals.jsonl`, each file's path, length and bytes in sorted path order, with SHA-256, and keeps the first 12 hex digits. The READMEs are left out, because rewording a description changes no result. After changing a plan or a meal, rewrite the file:

```sh
uv run python evals/dataset_version.py
```

`make test` fails while `VERSION` is stale.
