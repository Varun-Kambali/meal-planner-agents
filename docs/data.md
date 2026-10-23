# Data notes

nutrition-mcp bundles Indian dishes and 66 raw ingredients. The plans this project writes recipes for also need oats, fruit, seeds, nuts, olive oil, whey, chicken, tofu and a few more, so this project keeps its own food file in `data/foods/`, in nutrition-mcp's custom-dataset format. nutrition-mcp loads it at startup through `NUTRITION_MCP_DATA_DIRS` and serves its numbers like its own ([ADR 0003](decisions/0003-foods-through-nutrition-mcp-datasets.md)). This page covers where the rows come from, how each record was chosen, how values are mapped, and what is missing. Licenses and attribution are in [`data/foods/LICENSE.md`](../data/foods/LICENSE.md).

## Source

Every row is a USDA FoodData Central record from the SR Legacy dataset (public domain), source id `usda_fdc_sr_legacy` in `data/foods/sources.yaml`. nutrition-mcp's bundled data already uses the id `usda_fdc` for the same dataset, and a source id has to be unique across every folder nutrition-mcp loads, so the food file has its own entry. No row uses a Branded (label) record, a Survey (FNDDS) record or an Indian brand's label values.

## Building the food file

`data_build/food_map.csv` names one FDC record per food:

| Column | Holds |
|---|---|
| `id` | Lowercase slug used by recipes, the catalogue and the tools. It must not repeat an id in nutrition-mcp's bundled data |
| `name` | The Indian name, with the English name in parentheses where they differ |
| `aliases` | Other names and spellings, separated by `\|`, for nutrition-mcp's search |
| `diet` | `vegan`, `veg` (dairy, honey), `egg` or `non_veg` |
| `fdc_id` | The FoodData Central ID |
| `notes` | Why this record and not another. Not copied to `ingredients.csv` |

`make foods` runs `scripts/build_foods.py`, which:

1. Fetches each record from `https://api.nal.usda.gov/fdc/v1/food/{fdcId}` and caches it in `.cache/usda/` (git-ignored). The key is read from `USDA_FDC_API_KEY`, which `make foods` loads from `.env` when that file exists, and is sent in the `X-Api-Key` header, never in the URL. Only records that are not cached need it; `--refresh` fetches all of them again.
2. Maps each record with `usda_to_nutrients` and `usda_density_g_per_ml` from `nutrition_mcp.source_mapping`, the module nutrition-mcp's own build uses, so units, the carbohydrate definition and the density rule match the bundled data. Each row is validated with nutrition-mcp's `Ingredient` model, so the written CSV is known to load.
3. Writes `data/foods/ingredients.csv`, with an empty cell for a value the record does not report.
4. Writes `data_build/portions_review.csv`: every household portion FDC lists for each mapped record, for each bundled nutrition-mcp row that has a measure in `data/catalogue.yaml`, and for the two records fetched only for a portion (see [Household measures](#household-measures)).
5. Prints a review table: id, record, the record's own description, kcal, protein, density and the missing nutrients.

The build stops on an id that clashes with nutrition-mcp's bundled data, a repeated id, a record from a dataset other than SR Legacy or Foundation, a source missing from `sources.yaml`, and a cached file that holds another record. From the cache it writes the same bytes every time, so `make foods` followed by an empty `git diff` shows the committed file matches its records. `tests/integration/test_foods_load.py` checks that each row's id, name, aliases, diet and record in `ingredients.csv` match the map, and starts nutrition-mcp with the folder to read a food back. CI has no copy of the records, so it cannot check the values themselves.

## Choosing a record

1. **Form first.** The record has to be the food as it goes into the recipe: raw, the edible part, dry grain rather than cooked, unroasted seeds, unflavoured powder.
2. **SR Legacy over Foundation when Foundation lacks nutrients.** Foundation records are newer but often leave out nutrients nutrition-mcp reports. For oats, chia, iceberg lettuce, cabbage and orange-fleshed sweet potato the Foundation record has no fibre value, so available carbohydrate could not be computed; the Foundation olive oils report fat only as Total fat (NLEA, 1085), not as total lipid (1004), and have no energy value, so kcal and fat would be empty. Every food here had an SR Legacy record of the right form with fewer gaps, so every row is SR Legacy. The notes name the Foundation record that was passed over.
3. **Unenriched and unfortified where USDA has both**, as nutrition-mcp does. Fortification of Indian oats and milk varies by brand, and an addition that may not be in the food should not be counted.
4. **The variety sold in India where USDA splits a food.** The orange is the mandarin (santra, Nagpur orange, kinnow). The lettuce is iceberg. The mushroom is the white button mushroom, not UV-treated.

The full list, as reviewed against each record on 2026-10-23:

| Food | FDC ID | FDC description | Diet | Why this record |
|---|---|---|---|---|
| `rolled_oats` | 173904 | Cereals, oats, regular and quick, not fortified, dry | vegan | Dry and unfortified. Foundation 2346396 has no fibre value |
| `brown_rice` | 169703 | Rice, brown, long-grain, raw | vegan | Raw. Brown rice is not enriched. Foundation 2512380 has no saturated fat, sugars or folate value |
| `quinoa` | 168874 | Quinoa, uncooked | vegan | Uncooked. Foundation has quinoa flour only |
| `skimmed_milk` | 173432 | Milk, nonfat, fluid, without added vitamin A and vitamin D (fat free or skim) | veg | Without added vitamins, like nutrition-mcp's whole milk |
| `greek_yogurt` | 170903 | Yogurt, Greek, plain, lowfat | veg | Plain, 1.9 g fat per 100 g, between the nonfat (170894) and whole-milk (171304) records |
| `mozzarella` | 171244 | Cheese, mozzarella, low moisture, part-skim | veg | The block mozzarella sold for pizza and grating |
| `whey_protein` | 173180 | Beverages, Protein powder whey based | veg | Unflavoured whey powder, 78 g protein per 100 g. 173177, named an isolate, is a formulated mix with 29 g carbohydrate and added vitamins |
| `egg_white` | 172183 | Egg, white, raw, fresh | egg | Foundation 747997 has no mineral or vitamin values |
| `chicken_breast` | 171077 | Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw | non_veg | Raw, without added solution. Foundation 2646170 has no folate, vitamin A, B12 or vitamin D value |
| `tofu` | 172448 | Tofu, firm, prepared with calcium sulfate and magnesium chloride (nigari) | vegan | The more complete of the two SR firm-tofu records |
| `banana` | 173944 | Bananas, raw | vegan | Ripe banana. The green cooking banana is nutrition-mcp's `raw_banana` |
| `apple` | 171688 | Apples, raw, with skin | vegan | All varieties, with skin. The Foundation apples are single US varieties with gaps |
| `orange` | 169105 | Tangerines, (mandarin oranges), raw | vegan | Indian oranges are mandarins. Sweet oranges are 169097 |
| `frozen_blueberries` | 173950 | Blueberries, frozen, unsweetened | vegan | Unsweetened; the sweetened pack is 173951 |
| `pomegranate` | 169134 | Pomegranates, raw | vegan | Arils with their seeds |
| `papaya` | 169926 | Papayas, raw | vegan | Ripe, edible part |
| `sweet_potato` | 168482 | Sweet potato, raw, unprepared | vegan | Raw. Foundation 2346404 has no fibre value |
| `broccoli` | 170379 | Broccoli, raw | vegan | Foundation 747447 has no B12 or vitamin D value |
| `mushrooms` | 169251 | Mushrooms, white, raw | vegan | White button, not UV-treated (169376) |
| `lettuce` | 169248 | Lettuce, iceberg (includes crisphead types), raw | vegan | Iceberg; romaine is 169247 |
| `cabbage` | 169975 | Cabbage, raw | vegan | Green cabbage. Foundation 2346407 has no fibre value |
| `kimchi` | 170392 | Cabbage, kimchi | non_veg | The only kimchi record; the survey record 2710077 is built from it. Tagged non_veg, see below |
| `chia_seeds` | 170554 | Seeds, chia seeds, dried | vegan | Foundation 2710819 has no fibre value |
| `flax_seeds` | 169414 | Seeds, flaxseed | vegan | Covers whole and ground seed; named ground because the catalogue's spoon measures are ground weights. Foundation 2262075 (ground) has more gaps |
| `pumpkin_seeds` | 170556 | Seeds, pumpkin and squash seed kernels, dried | vegan | Unroasted, unsalted kernels |
| `sunflower_seeds` | 170562 | Seeds, sunflower seed kernels, dried | vegan | Unroasted, unsalted kernels |
| `sesame_seeds` | 170150 | Seeds, sesame seeds, whole, dried | vegan | Whole (unhulled) seed |
| `walnuts` | 170187 | Nuts, walnuts, english | vegan | Kernels. Foundation 2346394 has more gaps |
| `almonds` | 170567 | Nuts, almonds | vegan | Whole kernels with skin. Foundation 2346393 has more gaps |
| `peanut_butter` | 174266 | Peanut butter, smooth style, with salt | vegan | Smooth and salted; unsalted is 172470 |
| `olive_oil` | 171413 | Oil, olive, salad or cooking | vegan | The Foundation olive oils have no energy value and give fat only as Total fat (NLEA), not total lipid |
| `soy_sauce` | 174277 | Soy sauce made from soy and wheat (shoyu) | vegan | Brewed soy sauce. Tamari is 174278 |
| `honey` | 169640 | Honey | veg | The only honey record in SR Legacy or Foundation |

`notes` in the map gives each choice in full, including the alternatives' numbers where they differ enough to matter.

## Mapping

Values are mapped exactly as nutrition-mcp maps its bundled USDA rows, because the same function does it. [nutrition-mcp's data notes](https://github.com/Varun-Kambali/nutrition-mcp/blob/v1.0.0/docs/data.md) list the USDA nutrient ID behind each column. In short:

- **Carbohydrate** is available carbohydrate: USDA's carbohydrate by difference (1005) minus total dietary fibre (1079), floored at 0. Missing when either is missing.
- **Vitamin A** is retinol activity equivalents (1106). **Folate** is total folate in µg (1177), not dietary folate equivalents.
- **Energy** is the record's own kcal (1008), on SR Legacy's Atwater factors, which count fibre at the carbohydrate factor. It is stored as published and not recomputed.
- A value in another unit than nutrition-mcp's (vitamin D in IU, for example) stops the build.

**Density.** `density_g_per_ml` is set only for foods poured or spooned as liquids: honey, olive oil, skimmed milk and soy sauce. It is the record's cup portion (or tablespoon, when there is no cup) divided by its volume, so nutrition-mcp gives these foods ml, tbsp, tsp, cup and glass. Other foods take grams from nutrition-mcp, plus any household measure in `data/catalogue.yaml`.

## Household measures

`data/catalogue.yaml` turns a household unit into grams where nutrition-mcp has no such unit for a food, so "2 bananas" or "1 tbsp chia" can be counted. nutrition-mcp has no per-piece measures for ingredients, and gives spoons and cups only to foods with a density, so the four liquids above list none in the catalogue (the catalogue refuses a unit nutrition-mcp already has).

Each measure is an FDC portion weight, and the comment above it names the record and the portion. `data_build/portions_review.csv` lists every portion of every record the build reads, so a measure can be checked without the API. Where a record lists a cup but not the spoon a recipe uses, a tablespoon is a sixteenth of the cup (pumpkin and sunflower seeds) and the comment says so. Two records were fetched only for a portion, because the food's own record lacks one: the survey record for chia seeds (2707590, built from 170554, for its cup) and the one for whey powder (2710742, built from 173180, for its scoop). Whey scoops differ by brand: the same record lists 20.5 g, 26 g and 32 g, and the catalogue uses the 26 g scoop it does not tie to a brand.

The measures that were in the catalogue before the food file were checked against their records' portions:

| Food and unit | Grams | FDC portion | Result |
|---|---|---|---|
| `egg`, piece | 50 | 171287: 1 large, 50 g | Matches |
| `butter`, tbsp | 14.2 | 173410: 1 tbsp, 14.2 g | Matches |
| `onion`, piece | 110 | 170000: 1 medium (2-1/2" dia), 110 g | Matches |
| `tomato`, piece | 123 | 170457: 1 medium whole (2-3/5" dia), 123 g | Matches |
| `carrot`, piece | 61 | 170393: 1 medium, 61 g | Matches |
| `capsicum`, piece | 119 | 170427: 1 medium, 119 g | Matches |
| `garlic`, clove | 3 | 169230: 1 clove, 3 g | Matches |
| `sugar`, tsp | 4.2 | 169655: 1 tsp, 4.2 g | Matches |
| `sugar`, tbsp | 12.6 | None; the record lists tsp, cup, cube and packet | Kept as three of its teaspoons, marked as such |
| `green_chilli`, piece | 6 | 170497: 1 pepper, 45 g | Kept as a household estimate: the record's pepper is a larger variety than an Indian green chilli |
| `potato`, piece | 150 | None; the bundled row is from CoFID | Kept as a household estimate |

## What is missing

**No glycemic index.** USDA has no GI, so no row here has one, and a meal with any of these foods has no glycemic load. nutrition-mcp reports it as missing, and the day view says so instead of estimating it.

**Missing values.** Every row has all 16 nutrients except:

| Food | Missing |
|---|---|
| `quinoa` | sugars_g, vit_c_mg |
| `chia_seeds` | sugars_g, vit_a_ug, vit_d_ug |

A dish or meal with one of these foods reports the nutrient as incomplete and names the food.

**Values to read with care**, all kept as published:

- `sweet_potato`: the record is orange-fleshed (709 µg vitamin A per 100 g), and the row is named "Sweet potato (orange-fleshed)" without the local names. Shakarkandi sold in India is often white or pale-fleshed, with far less beta-carotene, so vitamin A reads high for it; a pale-fleshed sweet potato would need its own row.
- `sesame_seeds`: whole seed, 975 mg calcium per 100 g. Hulled sesame (169412) has 60 mg, so calcium reads high for a recipe made with hulled til.
- `kimchi`: neither record says whether it holds fish sauce or salted shrimp, and most kimchi on sale holds one or both. So the food is tagged `non_veg`, and the catalogue gives it the fish and crustacean allergens: a wrong tag on the safe side only leaves kimchi out of a plan, while a missing one would let it into a vegetarian or fish-free one. A vegan kimchi would need its own row. The record also has 0 mg vitamin C.
- `tofu`: firm tofu varies. 172448 has 9 g protein and 201 mg calcium per 100 g; the denser 172475 has 17 g and 683 mg. A pack that lists about 15 g protein is closer to 172475.
- `whey_protein`: unflavoured. Flavoured powders add sugar or sweetener, and brands differ. The catalogue tags it soy as well as milk, because many whey powders add soy lecithin.
- `mozzarella`: tagged veg on the assumption of a microbial or vegetable rennet.
- `honey`: tagged veg, not vegan, because vegans usually leave honey out.
- `peanut_butter`: salted, with the sugar commercial peanut butter carries (10.49 g sugars per 100 g against 4.72 g in raw peanuts).
- `orange`: the mandarin. A sweet orange (169097) has about twice the vitamin C.

**Plan foods left out.** Makhana (fox nut) and soya chunks (textured soy protein) have no SR Legacy or Foundation record (searched 2026-10-23); soy flour is a different form. Ragi and poha are missing from USDA too, as nutrition-mcp's data notes record. Hummus is left out because a recipe can list its own chickpeas, oil and lemon, and ready-made hummus varies by brand.
