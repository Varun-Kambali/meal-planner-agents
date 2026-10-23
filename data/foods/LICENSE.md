# Licenses for the food file

The code in this repository is MIT licensed (see `LICENSE` at the repository root). The files in this folder carry the licenses of the sources they come from. Each row of `ingredients.csv` names its source in `source_id`, and `sources.yaml` holds the full entry for that source.

| File | Source (`source_id`) | License | Attribution |
|---|---|---|---|
| `ingredients.csv`, rows with `usda_fdc_sr_legacy` | USDA FoodData Central, SR Legacy | Public domain | U.S. Department of Agriculture, Agricultural Research Service. FoodData Central. fdc.nal.usda.gov. |
| `sources.yaml`, this file | Written for this project | MIT, as the code | Varun Kambali, meal-planner-agents |

## Changes made to the source data

- Values are per 100 g of the food as described in the source record. Each row keeps the FDC ID in `source_record`, so a value can be checked against the original at `https://fdc.nal.usda.gov/food-details/<FDC ID>/nutrients`.
- Carbohydrate by difference has total dietary fibre subtracted, to give available carbohydrate, as in nutrition-mcp's bundled data.
- A nutrient the record does not report is an empty cell, never 0.
- Names, aliases and diet tags were added for this project.
- `density_g_per_ml` is computed from the record's cup or tablespoon portion, and only for foods poured or spooned as liquids.

`docs/data.md` in the repository describes how each record was chosen.
