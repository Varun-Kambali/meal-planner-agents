# Case study: meal-planner-agents

Written at kickoff on 2026-10-21, updated 2026-10-24 for the recipe pipeline.

## Why: the problem

I follow a meal plan I wrote myself. Writing it took research, and the result is less a list of meals than a set of rules: daily targets for training days and rest days, a style for each meal (breakfast is overnight oats set the night before, lunch is an air-fryer dish cooked the evening before, dinner is one pot), the gear I own, how much time I have, and a rule that I don't buy a jar or a bunch of something I will barely use.

The rules were the quick part. The slow part was recipes. Every slot needs a recipe that hits its numbers, uses what I have, takes little effort, and doesn't taste like yesterday's. I wanted every recipe different and every recipe on target, and I got there, but only because I spent far too long on it.

What's available today doesn't do this job:

1. **Recipes online are written for someone else.** Instagram, YouTube and food blogs give recipes that assume another person's kitchen, pantry and time, with no link to my targets.
2. **Chat assistants guess the numbers.** They write a recipe in seconds, but the nutrition comes from memory. In the grounding run of my previous project, [nutrition-mcp](https://github.com/Varun-Kambali/nutrition-mcp), a free Gemini model answering from memory missed calories by 16.0% on average, and only 38% of its calorie answers were within 10% ([results](https://github.com/Varun-Kambali/nutrition-mcp/blob/main/evals/results/summary.md)). Nothing checks its recipe against the plan's rules (an allergen, a pan I don't own, a 40-minute method for a 10-minute slot) before I cook it.
3. **Following the plan is a separate problem.** Once the week starts, I need to see whether what I actually ate matches what I planned, including the days I ate something else.

## Why I built it

Work and my own laziness had let bad habits set in around health and fitness, and I decided to turn that around. Exercise is one part of it, but if the body doesn't get the right nutrition, it gets sick no matter what I do in the gym. So I had to understand each part of a meal: macros, a calorie deficit, volume eating, what to eat first and how to structure a plate.

Eating well turned out to depend on planning well, without spending too long in the kitchen: fresh ingredients, balanced meals with fruit and vegetables, attention to micronutrients, and tracking, which matters most of all. I wanted recipes that work with the utensils I have and the energy I have left after work, use as few ingredients as possible, and still give the best result: roasting, the air fryer and parchment instead of frying in a lot of oil, with ideas from Japanese and Mediterranean eating as well as Indian. Eggs beaten with onion, peppers and spinach and baked on parchment, with black coffee and a banana, beat standing over a pan. Recipes like that should be easy to make without being the same generic ones, and the whole thing should be a plan that is disciplined and easy to follow without much thinking.

nutrition-mcp was the first half: sourced, computed nutrition numbers for the food people in India eat. This project is the second half: using those numbers for one person's plan. I also wanted to prove things my resume doesn't show yet: orchestrating several model steps with LangGraph, using evals as a gate in CI, tracing cost and latency with Langfuse, consuming an MCP server from an agent, and owning a product from the UI to the deployment.

## What: the solution

meal-planner-agents takes the rules of a meal plan the user already follows and writes a week of recipes that fit them: one per slot per day, all different, each checked against the plan in code. Then it tracks the week: the user marks each meal as eaten, changed, swapped or skipped, types what they had when they went off plan, and sees each day and the week against the targets. Every nutrition number comes from nutrition-mcp over MCP, never from the model.

In scope for v1:

- **Plan rules:** day types with targets, slots with their style and time limits, kitchen gear, food rules, cuisines to draw on, shopping days.
- **A week of recipes** that are easy to cook with little oil, use few ingredients, and draw on Indian, Japanese and Mediterranean cooking as the rules ask.
- **Hard checks in code:** slot and day targets, allergens and excluded foods, gear, time, variety within a slot, and a no-waste check.
- **Tracking** with one tap per meal, typed swaps read into foods and amounts, and day and week views.
- **Evals** of the recipes against a single-call baseline, and of the meal reader against labelled meals.

Out of scope for v1, and why:

- **Writing the plan itself.** The user brings it, which keeps v1 small enough to finish properly.
- **Health conditions** such as diabetes or a diagnosed deficiency. They need condition-aware rules and reference intakes, and getting them wrong has real cost. v1 follows the plan's own targets.
- **Shopping lists, a prep calendar, rotation across weeks, supplements and habits.** Each is its own feature, for after v1 ships.
- **Medical advice of any kind.** The app never suggests a medicine, a supplement or a dose.

## Who and where: users and fit

- **People who follow a plan,** one they wrote like me or one from a dietitian or coach, enter its rules once, get the recipe work done for them, and can see whether they're sticking to it.
- **Dietitians and coaches** are the larger fit. A dietitian already writes rules for each client; with this, the rules file becomes the hand-over, and the client gets recipes and tracking that keep to it. At that scale the system needs accounts and a server-side store, a licensed food table loaded through nutrition-mcp's custom datasets, and a paid model with caching. The checks and the graph don't change.

## How: design and trade-offs

A stateless FastAPI service runs a LangGraph graph: it expands the rules into slots, writes each slot's 7 recipes in one model call (slots in parallel), gets the numbers from nutrition-mcp, checks in plain Python, and sends failing recipes back with their failures, at most 3 times. Two more routes read a typed meal and add up a meal log. The diagram is in [architecture.md](architecture.md).

The decisions that mattered:

1. **Follow an existing plan instead of writing one** ([0001](decisions/0001-follow-an-existing-plan.md)). The first idea also covered shopping, prep and rotation: several products. Starting from the user's plan keeps v1 to the part that took longest.
2. **The model writes, code checks** ([0002](decisions/0002-model-writes-code-checks.md)). A model checking its own recipe lost, because its arithmetic is the problem nutrition-mcp measured. A fixed list of dishes lost too, with no variety. One call per slot sits between one call per recipe, where no call sees the others, and the single call that is the eval's baseline.
3. **New foods go through nutrition-mcp's dataset folder** ([0003](decisions/0003-foods-through-nutrition-mcp-datasets.md)). They come from USDA FoodData Central as a custom dataset. Editing nutrition-mcp would tie this project to its releases, and a second source would split where numbers come from.
4. **The user's data stays in the browser** ([0004](decisions/0004-user-data-in-the-browser.md)). Accounts and a database would mean login and food logs on a public demo; browser storage with a file export leaves nothing to leak.
5. **Code fits portions, and the writer is shown nutrition** ([0005](decisions/0005-code-fits-portions.md)). The writer guessed grams, and repair guessed again. Now code scales an off-target recipe's grams by one factor and nutrition-mcp works it out again, so code never multiplies a nutrient, and the writer sees each ingredient's nutrition per 100 g. More repair rounds lost: they spend calls on arithmetic. A gram solver lost: it would replace the writer's proportions.
6. **The model reads the meal; code checks amounts and units** ([0006](decisions/0006-reading-a-typed-meal.md)). The model lists the typed foods, amounts and units, nutrition-mcp's search finds each food, and one model call picks among its options where the search leaves a name open. Code settles the unit, and a vague amount or a range asks the person. The model returning ids and grams lost: it would invent the numbers this project avoids. Matching without a model lost: it cannot split "2 rotis, a bowl of dal and 2 eggs".
7. **The log holds foods and amounts, never numbers** ([0007](decisions/0007-the-log-holds-foods-and-amounts.md)). The server works out every eaten meal again with nutrition-mcp and holds each day to its own day type, so a number in an edited or old file is never trusted.
8. **A day check that cannot tell says so** ([0009](decisions/0009-unsettled-day-checks.md)). It passes, fails or is unsettled, and only a failure goes to repair, since no rewrite supplies a missing value. A food's total fat bounds its saturated fat. Counting a missing value as 0 lost: days would pass on numbers nobody measured.

## Results

The newest full run (gemini-3.1-flash-lite, commit 50aab53) covered 20 synthetic plans and 53 typed meals. Rows copied from [`evals/results/summary.md`](../evals/results/summary.md), which has every measure.

| Measure | Baseline | This project |
|---|---|---|
| Recipes passing every hard check (share) | 8.3% (46 of 553) | 86.6% (479 of 553) |
| Recipes passing every hard check at their first attempt (share) | 8.3% (46 of 553) | 46.7% (258 of 553) |
| Days passing every check (share) | 2.1% (3 of 140) | 51.4% (72 of 140) |
| Weeks where every recipe and day passes (share) | 0.0% (0 of 20) | 5.0% (1 of 20) |
| Model calls per week (mean) | 1.0 | 12.6 |

| Measure | All meals |
|---|---|
| Food recall | 92.0% (80 of 87) |
| Asked when it should (share) | 93.8% (15 of 16) |
| Calorie error against the label (MAPE) | 6.4% over 42 meals |

The single call guesses grams and misses slot calories on most recipes. Portion fitting gets the pipeline to 258 at the first attempt, and repair to 479. Days are the weak point (see Limits). An earlier run, before checks could be unsettled, passed 52 days on 14.2 calls a week. One read timed out at the API; it and the five meals the reader cannot reach today make most of the calorie error. One run of one free model on synthetic data, so the spread between runs is unknown.

## Limits, and what better inputs would change

- **Time is the model's claim.** Minutes are the recipe's own, checked against the slot's limits, never measured.
- **A style like "no-cook" is free text** that no code checks, though the prompts call it binding.
- **Missing values leave days unsettled.** With no fibre value for several dals or curry leaves, 28 of the 140 days can be shown neither to meet their fibre target nor to miss it.
- **Scaling is uniform,** so an egg can land on a fraction of a piece.
- **A household measure with no sourced weight is not converted:** for a bowl or a pav the reader asks for a katori or grams.
- **Which dish a general name means is the model's pick** among nutrition-mcp's options. In the reading eval, 80 of the reader's 82 foods were the labelled ones.
- **An amount counts only if its words are in what was typed,** so one the model rewrites asks the person.
- **The as-planned share counts a meal with no entry as not as planned.**
- **Glycemic load is often unknown.** USDA foods carry none; a licensed table would fill it in.
- **The catalogue limits variety.** A larger licensed table widens it without code changes.
- **Free-tier models.** A stronger model is a config change, and the eval measures what it buys.
- **Browser storage.** Clearing the browser loses the data unless it was exported.

## What I learned

[Written as the milestones land.]
