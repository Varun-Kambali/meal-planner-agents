# Plans for the recipe eval

Twenty plan rules files, each in the format the app takes (`PlanRules` in `src/meal_planner/rules.py`). The recipe eval writes a week for each one, through the pipeline and through the single-call baseline, and scores both with the same checks in code.

Every plan here is made up. None is a real person's plan, and no target, share or time in them comes from one. `tests/unit/test_datasets.py` checks that the files validate, that they cover the spread below, and that every liked or excluded food is in `data/catalogue.yaml`. `tests/integration/test_datasets_live.py` checks them against the catalogue as loaded from nutrition-mcp, including that no plan likes a food its own diet or allergens rule out.

## The plans

A plan's id is its file name. Active minutes are the range of `max_active_min` across its slots.

| Plan | Diet | Day types | Slots | Gear | Active minutes | Cuisines | Allergens excluded | Hard |
|---|---|---|---|---|---|---|---|---|
| `bengali_fish_home` | non_veg | 2 | 3 | pressure cooker, kadhai, stovetop pan | 15-45 | Bengali | peanut | no |
| `egg_hostel_microwave` | egg | 1 | 3 | microwave, kettle | 5-10 | North Indian | peanut, tree nuts | no |
| `egg_weekend_batch_cook` | egg | 2 | 5 | oven, stovetop pan, blender, air fryer, kettle | 5-45 | Mediterranean, Maharashtrian | mustard, fish | no |
| `fat_loss_four_meals` | non_veg | 2 | 4 | stovetop pan, pressure cooker | 10-25 | North Indian, South Indian, Mediterranean, East Asian | none | no |
| `goan_coastal_no_shellfish` | non_veg | 2 | 4 | kadhai, pressure cooker, stovetop pan, blender | 5-40 | Goan, Konkani | crustaceans | no |
| `gujarati_no_onion_garlic` | veg | 1 | 5 | pressure cooker, kadhai, steamer, blender | 5-35 | Gujarati | none | no |
| `hard_kettle_high_protein` | veg | 1 | 3 | kettle | 5-10 | North Indian | milk | yes |
| `hard_vegan_jar_breakfast` | vegan | 1 | 3 | jar with lid | 5-10 | Mediterranean | soy, peanut | yes |
| `japanese_home_vegan` | vegan | 1 | 3 | rice cooker, stovetop pan, steamer, kettle | 15-35 | Japanese | peanut | no |
| `kerala_egg_no_dairy` | egg | 2 | 4 | pressure cooker, kadhai, steamer | 10-40 | Kerala, South Indian | milk | no |
| `korean_japanese_vegan` | vegan | 3 | 4 | rice cooker, steamer, stovetop pan, kettle | 5-25 | Korean, Japanese | sesame | no |
| `korean_quick_non_veg` | non_veg | 2 | 4 | rice cooker, stovetop pan, microwave | 5-25 | Korean | sesame | no |
| `maharashtrian_egg_home` | egg | 2 | 4 | pressure cooker, stovetop pan, microwave, kadhai | 10-40 | Maharashtrian | sesame | no |
| `mediterranean_student_veg` | veg | 1 | 3 | microwave, kettle, jar with lid | 5-15 | Mediterranean | tree nuts | no |
| `odia_bengali_gluten_free` | veg | 1 | 4 | pressure cooker, kadhai | 10-40 | Odia, Bengali | gluten | no |
| `punjabi_rajasthani_gluten_free` | veg | 2 | 4 | pressure cooker, kadhai, air fryer | 15-35 | Punjabi, Rajasthani | gluten | no |
| `punjabi_strength_split` | non_veg | 3 | 5 | air fryer, pressure cooker, stovetop pan, oven, blender | 5-35 | Punjabi, Mediterranean | none | no |
| `south_indian_office_veg` | veg | 1 | 4 | pressure cooker, kadhai, steamer, stovetop pan | 10-40 | South Indian, Kerala | none | no |
| `tamil_andhra_family` | non_veg | 3 | 5 | pressure cooker, kadhai, steamer, stovetop pan, blender, rice cooker | 5-40 | Tamil, Andhra, Chettinad | tree nuts, peanut | no |
| `vegan_runner_four_day_types` | vegan | 4 | 5 | pressure cooker, blender, oven, stovetop pan, rice cooker | 5-35 | South Indian, Mediterranean, Japanese | none | no |

Together they cover all four diets, one to four day types, three to five slots, active-time limits from 5 to 45 minutes, 19 different gear sets, allergen exclusions in fifteen plans, and regional Indian, Japanese, Korean and Mediterranean cooking.

## How the numbers were set

Each day type's carbs follow from its calories, protein and fat at 4, 4 and 9 kcal a gram, rounded to 5 g, so the targets add up. Saturated fat caps come to 11% of calories or less, and fibre floors run from 25 g to 45 g. Slot shares were chosen per plan, so the slots of a day do not all get the same share.

## The two hard plans

Both are meant to fail. They test that a week the catalogue, gear or time cannot meet ends with its recipes marked failing and the shortfall stated, rather than a hang or a silent pass.

- **`hard_vegan_jar_breakfast`**: a vegan breakfast that has to carry 60 g of protein (40% of a 150 g day) in about 475 kcal, which is half its energy from protein. It is made in a jar in 5 active minutes with no heat, and soy and peanut are excluded, so tofu, soy sauce, peanuts and peanut butter are out and pulses cannot be cooked. Pumpkin seeds, the most protein-dense seed in the catalogue, give about 25 g of protein for those calories. The only foods left with enough protein per calorie are raw mushrooms and leafy greens, about 2 kg of them for one breakfast. Lunch and dinner are no-cook jars as well.
- **`hard_kettle_high_protein`**: 175 g of protein in 1,400 kcal, again half the day's energy from protein, on a vegetarian diet with milk excluded. That leaves no paneer, curd, Greek yogurt or whey, and eggs are out by diet. The only gear is a kettle, so pulses cannot be pressure-cooked or simmered. Of what is left, 175 g of protein from tofu is about 1,500 kcal, more than the whole day, and from mushrooms it is over 5 kg a day. The day also asks for 1,000 mg of calcium without dairy.

## Changing a plan

Edit the JSON, run `make test`, then rewrite the dataset version with `uv run python evals/dataset_version.py`. The results files record that version, so a number can be traced to the plans it was measured on.
