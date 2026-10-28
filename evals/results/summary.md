# Eval results

Rebuilt by `evals/run_evals.py` from the newest full run of each suite. Do not edit it by hand: every number here is in the results file named under its heading.

## Recipes

From `2026-10-27_gemini-gemini-3.1-flash-lite_b593c44.json`: model gemini/gemini-3.1-flash-lite, commit b593c44, dataset 4a35ea1576a9, 20 plans, started 2026-10-27. Pipeline switches: portion fitting on, nutrient hints on. LiteLLM 1.105.1.

| Measure | Baseline | This project |
|---|---|---|
| Recipes passing every hard check (share) | 8.7% (48 of 553) | 81.6% (451 of 553) |
| Recipes passing every hard check at their first attempt (share) | 8.7% (48 of 553) | 47.0% (260 of 553) |
| Recipes passing the variety checks (share) | 63.3% (350 of 553) | 91.7% (507 of 553) |
| Days passing every check (share) | 0.7% (1 of 140) | 37.1% (52 of 140) |
| Weeks where every recipe and day passes (share) | 0.0% (0 of 20) | 5.0% (1 of 20) |
| Recipes written (share) | 100.0% (553 of 553) | 100.0% (553 of 553) |
| Model calls per week (mean) | 1.0 | 14.2 |
| Tokens per week (mean) | 11,645.5 | 96,752.2 |
| Cost per week at list price (mean) | $0.009104 | $0.043386 |
| Seconds per week (p95) | 19.0 | 65.9 |

The baseline writes the whole week in one model call, from the same plan lines and ingredient list the pipeline's writer gets, and its recipes go through the same checks with no portion fitting and no repair. Every share counts over the recipes the plans ask for, so a recipe that was never written counts as failing. A day's own checks are counted on the day. Cost is LiteLLM 1.105.1's list price for these calls: what they would cost on the paid tier. Gemini's free tier bills nothing for them.

Recipes that ended failing each check:

| Check | Baseline | This project |
|---|---|---|
| `gear` | 8 | 2 |
| `slot_kcal` | 476 | 13 |
| `slot_protein_g` | 77 | 50 |
| `unknown_ingredient` | 3 | 2 |
| `variety_cuisine` | 25 | 38 |
| `variety_ingredients` | 185 | 12 |
| `variety_title` | 3 | 0 |

Days that ended failing each check:

| Check | Baseline | This project |
|---|---|---|
| `day_calcium_mg` | 17 | 18 |
| `day_fibre_g` | 64 | 44 |
| `day_kcal` | 125 | 15 |
| `day_protein_g` | 12 | 16 |
| `day_sat_fat_g` | 80 | 50 |
| `day_sodium_mg` | 0 | 4 |

## Reading

From `2026-10-27_gemini-gemini-3.1-flash-lite_b593c44.json`: model gemini/gemini-3.1-flash-lite, commit b593c44, dataset 4a35ea1576a9, 53 typed meals, started 2026-10-27. Pipeline switches: portion fitting on, nutrient hints on. LiteLLM 1.105.1.

| Measure | All meals | Expected misses (m01, m02, m03, m15, m52) | Other meals |
|---|---|---|---|
| Meals | 53 | 5 | 48 |
| Food precision | 97.6% (83 of 85) | 75.0% (6 of 8) | 100.0% (77 of 77) |
| Food recall | 95.4% (83 of 87) | 66.7% (6 of 9) | 98.7% (77 of 78) |
| Status accuracy | 94.3% (82 of 87) | 66.7% (6 of 9) | 97.4% (76 of 78) |
| Asked when it should (share) | 93.8% (15 of 16) | 50.0% (1 of 2) | 100.0% (14 of 14) |
| Labels that ask, food not returned by the reader | 1 of 16 | 1 of 2 | 0 of 14 |
| Calorie error against the label (MAPE) | 4.0% over 42 meals | 29.3% over 5 meals | 0.6% over 37 meals |
| Foods not in the data reported missing (share) | 100.0% (19 of 19) | 100.0% (1 of 1) | 100.0% (18 of 18) |
| Reads that failed | 0 of 53 | 0 of 5 | 0 of 48 |
| Model calls per meal (mean) | 1.13 | 1.20 | 1.12 |
| Tokens per meal (mean) | 832.6 | 866.2 | 829.1 |
| Cost per meal at list price (mean) | $0.000319 | $0.000333 | $0.000318 |
| Seconds per meal (p95) | 8.5 | 4.4 | 8.5 |

A labelled food is found when the reader returns its food id or one the label also accepts. A label that needs an amount or a unit counts as asked when the reader returned that food and did not match it; one whose food the reader did not return is counted on its own row, and a read that failed is left out of both. The expected misses name foods the reader cannot reach today (see `evals/datasets/README.md`). Cost is LiteLLM 1.105.1's list price for these calls: what they would cost on the paid tier. Gemini's free tier bills nothing for them.
