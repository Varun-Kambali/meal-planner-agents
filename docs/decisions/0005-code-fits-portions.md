# 0005. Code fits portions, and the writer is shown nutrition per 100 g
Date: 2026-10-24 · Status: accepted

## Context
[0002](0002-model-writes-code-checks.md) has the model choose ingredients and grams and code check the result. The first live week on the office-weekday plan showed that split is too coarse for calories. It was one run of the real model, not an eval, so it gives no numbers to quote here, but most days failed at least one check:

- **Calories did not converge in repair.** The writer knows no ingredient's energy, so it guesses grams. The repair call says "calories 565 kcal, slot needs at least 597" and it guesses again, often just as far off on the other side.
- **Saturated fat and sodium went over.** The writer saw ingredient names only, so it could not know which ones carry them. Saturated fat was over the day's cap on several days and sodium over on training days.
- **The slot's style was often ignored.** An air-fryer dinner or a no-cook snack did not come out as one.

## Decision
Three changes, the first two each behind its own switch so the M4 eval can compare with and without.

**Code fits the portions** (`MEAL_PLANNER_FIT_PORTIONS`, default on). After nutrition-mcp returns a recipe's numbers, a recipe whose calories are outside the slot's band (the same tolerance the slot check uses) gets every ingredient's grams multiplied by one factor, the slot's calories over the recipe's. Grams are rounded to whole grams, at least 1 g. The scaled recipe goes back to nutrition-mcp for its numbers; no nutrient is ever multiplied in code. Scaling applies only when the recipe's calories are fully known, the factor is within `MEAL_PLANNER_PORTION_SCALE_MIN` and `MEAL_PLANNER_PORTION_SCALE_MAX` (0.5 and 2.0), and no ingredient would pass 1000 g. A factor outside those bounds means the dish is wrong, not the amounts, so the recipe goes to repair as before. Only recipes written or repaired in the run are scaled: the recipes a replacement keeps are never touched. Scaling is not an attempt and has no event of its own. `CheckedRecipe.portion_scale` records the factor.

**The writer sees nutrition** (`MEAL_PLANNER_WRITER_NUTRIENT_HINTS`, default on). Each ingredient line in the writing and repair prompts carries calories, protein, saturated fat, fibre and sodium per 100 g, kept from the call nutrition-mcp already answers when the catalogue loads. Each day line adds the slot's share of that day type's saturated-fat cap, fibre floor and extra targets, with caps rounded down and floors up. The prompt says the numbers are for choosing amounts and must not be reported back. The model is still never asked for a number, and none it writes is used.

**The prompts say three more things**, whatever the switches: the grams are the weight of the ingredient as listed, the slot's style is binding, and the steps name ingredients without amounts, because the amounts are in the ingredient list and code may change them.

## Alternatives considered
- **More repair rounds.** Each is another model call per slot, and the writer still guesses the grams. It spends the free tier on arithmetic the model is bad at.
- **Ask the model for the numbers, or to check its own.** That is the guessing [0002](0002-model-writes-code-checks.md) avoids.
- **A solver that chooses the grams.** It would hit the targets exactly, but it needs rules for what makes a dish (a spoon of oil, not a cup), and it would replace the writer's proportions instead of keeping them. Scaling keeps the dish and moves only its size. A solver could still be added later for what scaling cannot fix.

## Consequences
The grams in a finished recipe can differ from the ones the model wrote, and every ingredient scales by the same factor, oil and spices included. A piece-measured ingredient can land on a fraction of a piece, and a small ingredient moves a little with the rounding. The steps are the model's text and are not rewritten. The prompts tell it to name ingredients without amounts, but a step from the model may still name one, and then it can disagree with the scaled list. Scaling also moves protein, saturated fat and sodium by the same factor, so the recipe, day and week checks run on the scaled numbers and repair handles what is left. A recipe that only needed a different size no longer costs a repair call, and a scaled recipe costs a second `compute_meal` call. If that call fails, the recipe stays as the writer gave it with its first numbers. Each writing and repair prompt grows by one short line per ingredient. Both switches stay in place so the eval can report the writer with and without each.
