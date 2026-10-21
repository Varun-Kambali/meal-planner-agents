# 0002. The model writes recipes; code checks every number and rule
Date: 2026-10-21 · Status: accepted

## Context
A recipe has to hit its slot's calories and protein, keep to the diet and allergen rules, use only gear the user owns and fit the time limit, and a week of them has to stay varied. Models are good at combining ingredients and flavours. They are unreliable at arithmetic: in nutrition-mcp's grounding run, a free Gemini model answering from memory missed calories by 16.0% on average ([results](https://github.com/Varun-Kambali/nutrition-mcp/blob/main/evals/results/summary.md)). They also break rules without saying so.

## Decision
The model writes each recipe as structured output: ingredient ids from the catalogue with grams, steps, minutes, gear. It is never asked for nutrition numbers, and none it writes is used. nutrition-mcp's `compute_meal` gives every recipe's nutrients. Plain Python functions check each recipe, each day and the week, and return failures in words the model can act on. A failing recipe goes back to the model with its failures, at most 3 times; after that it is shown with the failures listed.

The pipeline is a LangGraph graph: expand the rules into slots, write each slot's 7 recipes in one call with the slots in parallel, compute, check, repair, report.

## Alternatives considered
- **Ask the model to check its own recipe.** Its arithmetic is the problem being solved, so its check can't be trusted.
- **Pick from a fixed list of dishes.** Deterministic and easy to check, but it can't fit a slot's style and time limit or give a week of different recipes.
- **One model call per recipe.** 28 calls for a four-slot week, and each call can't see the other recipes in its slot, so variety suffers.
- **One call for the whole week.** Cheapest, and it is the baseline the eval compares against. A single failure means redoing everything.

## Consequences
Every number a user sees has a source, and every rule has a test at its edges. The checks are pure functions, so they are cheap to test thoroughly. A week costs at most one writing call plus three repair calls per slot, which keeps it inside free-tier quotas. The model can only use catalogue ingredients, so the catalogue's size limits variety. Times are what the recipe states; code can check them against the limit but not measure them.
