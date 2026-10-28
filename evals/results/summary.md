# Eval results

Rebuilt by `evals/run_evals.py` from the newest full run of each suite. Do not edit it by hand: every number here is in the results file named under its heading.

## Recipes

From `2026-10-27_gemini-gemini-3.1-flash-lite_50aab53.json`: model gemini/gemini-3.1-flash-lite, commit 50aab53, dataset 4a35ea1576a9, 20 plans, started 2026-10-27. Pipeline switches: portion fitting on, nutrient hints on. LiteLLM 1.105.1.

| Measure | Baseline | This project |
|---|---|---|
| Recipes passing every hard check (share) | 8.3% (46 of 553) | 86.6% (479 of 553) |
| Recipes passing every hard check at their first attempt (share) | 8.3% (46 of 553) | 46.7% (258 of 553) |
| Recipes passing the variety checks (share) | 65.1% (360 of 553) | 94.4% (522 of 553) |
| Days passing every check (share) | 2.1% (3 of 140) | 51.4% (72 of 140) |
| Days the checks could not settle (share) | 1.4% (2 of 140) | 20.0% (28 of 140) |
| Weeks where every recipe and day passes (share) | 0.0% (0 of 20) | 5.0% (1 of 20) |
| Weeks with no failing recipe or day (share) | 0.0% (0 of 20) | 30.0% (6 of 20) |
| Recipes written (share) | 98.7% (546 of 553) | 100.0% (553 of 553) |
| Model calls per week (mean) | 1.0 | 12.6 |
| Tokens per week (mean) | 11,654.2 | 85,323.7 |
| Cost per week at list price (mean) | $0.009117 | $0.040083 |
| Seconds per week (p95) | 58.3 | 143.6 |

The baseline writes the whole week in one model call, from the same plan lines and ingredient list the pipeline's writer gets, and its recipes go through the same checks with no portion fitting and no repair. Every share counts over the recipes the plans ask for, so a recipe that was never written counts as failing. A day's own checks are counted on the day. A day check is unsettled when a food in the day has no value for the nutrient in nutrition-mcp's data, or a recipe in it has no numbers from nutrition-mcp, so the day can be shown neither to meet the target nor to miss it: the day does not pass, and no recipe is rewritten for it. Cost is LiteLLM 1.105.1's list price for these calls: what they would cost on the paid tier. Gemini's free tier bills nothing for them.

Recipes that ended failing each check:

| Check | Baseline | This project |
|---|---|---|
| `gear` | 21 | 0 |
| `slot_kcal` | 460 | 6 |
| `slot_protein_g` | 79 | 41 |
| `unknown_ingredient` | 2 | 0 |
| `variety_cuisine` | 29 | 26 |
| `variety_ingredients` | 165 | 8 |

Recipes not written, by reason:

| Reason | Baseline | This project |
|---|---|---|
| reply had the wrong recipes | 7 | 0 |

Days that ended failing each check:

| Check | Baseline | This project |
|---|---|---|
| `day_calcium_mg` | 21 | 10 |
| `day_fibre_g` | 27 | 4 |
| `day_kcal` | 124 | 6 |
| `day_protein_g` | 18 | 15 |
| `day_sat_fat_g` | 53 | 21 |

Days that ended unsettled on each check:

| Check | Baseline | This project |
|---|---|---|
| `day_fibre_g` | 52 | 53 |
| `day_kcal` | 2 | 0 |
| `day_sat_fat_g` | 1 | 0 |

A day is counted on each check it left unsettled, also when it fails another check, so these counts can be larger than the days the checks could not settle.

## Reading

From `2026-10-27_gemini-gemini-3.1-flash-lite_50aab53.json`: model gemini/gemini-3.1-flash-lite, commit 50aab53, dataset 4a35ea1576a9, 53 typed meals, started 2026-10-27. Pipeline switches: portion fitting on, nutrient hints on. LiteLLM 1.105.1.

| Measure | All meals | Expected misses (m01, m02, m03, m15, m52) | Other meals |
|---|---|---|---|
| Meals | 53 | 5 | 48 |
| Food precision | 97.6% (80 of 82) | 75.0% (6 of 8) | 100.0% (74 of 74) |
| Food recall | 92.0% (80 of 87) | 66.7% (6 of 9) | 94.9% (74 of 78) |
| Status accuracy | 90.8% (79 of 87) | 66.7% (6 of 9) | 93.6% (73 of 78) |
| Asked when it should (share) | 93.8% (15 of 16) | 50.0% (1 of 2) | 100.0% (14 of 14) |
| Labels that ask, food not returned by the reader | 1 of 16 | 1 of 2 | 0 of 14 |
| Calorie error against the label (MAPE) | 6.4% over 42 meals | 29.3% over 5 meals | 3.3% over 37 meals |
| Foods not in the data reported missing (share) | 100.0% (19 of 19) | 100.0% (1 of 1) | 100.0% (18 of 18) |
| Reads that failed | 1 of 53 | 0 of 5 | 1 of 48 |
| Model calls per meal (mean) | 1.15 | 1.20 | 1.15 |
| Tokens per meal (mean) | 817.8 | 856.6 | 813.8 |
| Cost per meal at list price (mean) | $0.000304 | $0.000318 | $0.000303 |
| Seconds per meal (p95) | 43.7 | 16.7 | 43.7 |

A labelled food is found when the reader returns its food id or one the label also accepts. A label that needs an amount or a unit counts as asked when the reader returned that food and did not match it; one whose food the reader did not return is counted on its own row, and a read that failed is left out of both. The expected misses name foods the reader cannot reach today (see `evals/datasets/README.md`). Cost is LiteLLM 1.105.1's list price for these calls: what they would cost on the paid tier. Gemini's free tier bills nothing for them.
