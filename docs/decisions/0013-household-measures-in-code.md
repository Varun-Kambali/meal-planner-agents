# 0013. Household measures are worked out on the server after portion fitting, never in prompts
Date: 2026-11-06 · Status: accepted

## Context
The requirements ask for each recipe's ingredients in grams, "and a household measure where one is known". When the recipe page was first built, the API sent grams only, so the page showed "200 g milk" where a cook reads a glass or a cup.

The grams a recipe leaves with are often not the grams the writer gave. Code scales an off-target recipe to its slot's calories ([0005](0005-code-fits-portions.md)), and a replacement keeps the other recipes' grams as they were. The grams in one katori, piece, cup or glass of a food are data: nutrition-mcp has its own units for many foods, and `data/catalogue.yaml` adds measures from USDA portion weights for the rest. The browser has neither; `GET /v1/catalogue` gives it ids, names and categories.

The smoke eval that gates every PR replays recorded model replies, found by a hash of the prompts ([0008](0008-smoke-replay-gates-every-pr.md)). Any change to what the model is sent makes the replay miss and needs a new recording with a key.

## Decision
`measures.py` picks one household measure for each ingredient when the week is reported, from the grams the recipe ends with. Nothing about it is sent to the model, and no call is made for it.

- The units are the food's `valid_units`: nutrition-mcp's, with its grams in one of each kept when the catalogue loads, then the catalogue's.
- g and ml are never a measure, since the grams are shown already. A unit fits when its rounded amount is above 0 and within 15% of the grams. Counted units (piece, roti, clove, leaf, slice) round to the nearest half, others to the nearest quarter.
- Of the units that fit, the one with the most grams in one whose amount is at least a half wins; when none reaches a half, the one with the fewest grams in one. With no unit that fits, there is no measure.

The first rule took the first unit that fitted, in `valid_units` order, and gave 200 g of milk as 13 tbsp, because tbsp comes before cup. The rule above replaced it on 2026-11-06. On the real data it gives 200 g milk as ¾ glass, 4 g oil as ¾ tsp, 25 g sugar as 2 tbsp and 140 g mushrooms as 2 cups. `tests/unit/test_measures.py` tests each edge: 15% over and under, the rounding, a tie, half a unit, and grams and millilitres refused.

Each recipe carries `measures`, one per ingredient or null, in the week stream and the export file. The field is optional in the file, so an export saved before it still loads, and the export's `version` stays 1. The replace request has no field for it, and one sent is refused (`test_replace_refuses_measures_from_the_browser_as_a_field_it_does_not_take`): the server works it out again, as it does the numbers.

## Alternatives considered
- **Ask the writer for a measure with each ingredient.** It would change every prompt and so the smoke recording, the measure would be the model's guess at a weight, and it would describe the writer's grams, not the fitted ones.
- **Work it out in the browser.** The browser has no grams per unit, and sending them with the catalogue would put a second copy of nutrition-mcp's data in the page.
- **Keep the first unit that fits.** Simpler, and it reads "13 tbsp" for a glass of milk.

## Consequences
Every measure follows from a sourced weight and the recipe's final grams, and the prompts, the recording and the eval numbers did not change. The recipe page and its print show the measure after the grams; the week screen lists no ingredients, so it shows none.

Liquids read in glasses where nutrition-mcp's glass is bigger than its cup (200 g milk is ¾ glass, not ¾ cup). Whether cups would read better is a data preference for Varun, left open. A file edited by hand can carry a `measures` list of the wrong length; it loads, and the page shows grams alone where an entry is missing, since the import check does not compare the two lengths.
