# Case study: meal-planner-agents

Written at kickoff on 2026-10-21 and updated on 2026-10-24 for the recipe pipeline. The results arrive with the evals.

## Why: the problem

I follow a meal plan I wrote myself. Writing it took research, and it is less a list of meals than a set of rules: daily targets for training days and rest days, a style for each meal (overnight oats, an air-fryer lunch cooked the evening before, a one-pot dinner), the gear I own, how much time I have, and a rule that I don't buy a jar or a bunch of something I will barely use.

The rules were the quick part. The slow part was recipes: every slot needs one that hits its numbers, uses what I have, takes little effort and doesn't taste like yesterday's. I got every recipe different and on target, but only by spending far too long on it.

What's available today doesn't do this job:

1. **Recipes online are written for someone else.** They assume another person's kitchen, pantry and time, and have no link to my targets.
2. **Chat assistants guess the numbers.** In the grounding run of my previous project, [nutrition-mcp](https://github.com/Varun-Kambali/nutrition-mcp), a free Gemini model answering from memory missed calories by 16.0% on average, and only 38% of its calorie answers were within 10% ([results](https://github.com/Varun-Kambali/nutrition-mcp/blob/main/evals/results/summary.md)). Nothing checks its recipe against the plan's rules before I cook it.
3. **Following the plan is a separate problem.** Once the week starts, I need to see whether what I ate matches what I planned, including the days I ate something else.

## Why I built it

Work and my own laziness had let bad habits set in around health and fitness, and I decided to turn that around. Exercise is one part of it, but if the body doesn't get the right nutrition, it gets sick no matter what I do in the gym. So I had to understand each part of a meal: macros, a calorie deficit, volume eating, what to eat first and how to structure a plate.

Eating well turned out to depend on planning well, without spending too long in the kitchen: fresh ingredients, balanced meals with fruit and vegetables, attention to micronutrients, and tracking, which matters most of all. I wanted recipes that work with the utensils I have and the energy I have left after work, use as few ingredients as possible, and still give the best result: roasting, the air fryer and parchment instead of frying in a lot of oil, with ideas from Japanese and Mediterranean eating as well as Indian. Eggs beaten with onion, peppers and spinach and baked on parchment, with black coffee and a banana, beat standing over a pan. Recipes like that should be easy to make without being the same generic ones, and the whole thing should be a plan that is disciplined and easy to follow without much thinking.

nutrition-mcp was the first half: sourced, computed nutrition numbers for the food people in India eat. This project is the second half: using those numbers for one person's plan. I also wanted to prove things my resume doesn't show yet: orchestrating several model steps with LangGraph, using evals as a gate in CI, tracing cost and latency with Langfuse, consuming an MCP server from an agent, and owning a product from the UI to the deployment.

## What: the solution

meal-planner-agents takes the rules of a meal plan the user already follows and writes a week of recipes that fit them: one per slot per day, all different, each checked against the plan in code. It then tracks the week: the user marks each meal as eaten, changed, swapped or skipped, types what they ate off plan, and sees each day and the week against the targets. Every nutrition number comes from nutrition-mcp over MCP, never from the model. Built so far: the rules, the recipe pipeline with its checks, an HTTP API and a command line.

Out of scope for v1, and why:

- **Writing the plan itself.** The user brings it, which keeps v1 small enough to finish properly.
- **Health conditions and medical advice.** Diabetes or a diagnosed deficiency needs condition-aware rules, and an error costs too much. The app never suggests a medicine, a supplement or a dose.
- **Shopping lists, a prep calendar, rotation across weeks, supplements and habits.** Each is its own feature.

## Who and where: users and fit

- **People who follow a plan,** their own or a dietitian's, enter its rules once and get the recipe work done.
- **Dietitians and coaches** are the larger fit: the rules file becomes the hand-over. At that scale the system needs accounts and a server-side store, a licensed food table loaded through nutrition-mcp's custom datasets, and a paid model with caching. The checks and the graph don't change.

## How: design and trade-offs

A stateless FastAPI service runs a LangGraph graph: it expands the rules into slots, writes each slot's 7 recipes in one model call (all slots in parallel), gets the numbers from nutrition-mcp, checks recipes, days and the week in plain Python, and sends failing recipes back with the exact failures, at most 3 times. The diagram is in [architecture.md](architecture.md).

The decisions that mattered:

1. **Follow an existing plan instead of writing one** ([0001](decisions/0001-follow-an-existing-plan.md)). The original idea also covered shopping, prep and rotation, which is several products. Starting from the user's plan keeps v1 to the part that took me longest.
2. **The model writes, code checks** ([0002](decisions/0002-model-writes-code-checks.md)). Asking the model to check its own recipe lost, because its arithmetic is the problem nutrition-mcp measured. A fixed list of dishes lost too: no variety, and no fit to a slot's style.
3. **One model call per slot for the week.** One call per recipe is 28 calls for a four-slot plan and none sees the others, so variety suffers. One call for everything is the eval's baseline.
4. **New foods go through nutrition-mcp's dataset folder** ([0003](decisions/0003-foods-through-nutrition-mcp-datasets.md)). Recipes need foods it doesn't bundle, so they come from USDA FoodData Central as a custom dataset. The writer may only use ingredients in a catalogue file, and at startup every id in it is checked against nutrition-mcp, so a typo stops the app instead of reaching a recipe. Changing nutrition-mcp itself would tie this project to its releases; a second source here would split where numbers come from.
5. **The user's data stays in the browser** ([0004](decisions/0004-user-data-in-the-browser.md)). Accounts and a database would mean login and people's food logs on a public demo. A stateless API with browser storage and a file export leaves nothing to leak.
6. **Code fits portions, and the writer is shown nutrition** ([0005](decisions/0005-code-fits-portions.md)). The first live week (one run, not an evaluation) showed the writer can't land on calories: it knows no ingredient's energy, so it guesses grams, and repair guesses again. Now code scales every gram of an off-target recipe by one factor and nutrition-mcp works the scaled recipe out again, so code never multiplies a nutrient. The writer also sees each ingredient's nutrition per 100 g and the slot's share of the day's limits. More repair rounds lost: they spend model calls on arithmetic the model is bad at. A solver that picks the grams lost too: it would replace the writer's proportions, where scaling keeps the dish and moves only its size. Each change has a switch, so the eval can measure the writer with and without it.

## Results

[The recipe eval and the reading eval run in M4. Their table will be copied here from `evals/results/summary.md`.]

No results yet. The weeks I have run with a real model were not evaluations, so I quote nothing from them.

## Limits, and what better inputs would change

- **Time is the model's claim.** Minutes are what the recipe states, checked against the slot's limits but never measured.
- **A slot's style is not checked.** "No-cook" is free text. The prompts call it binding, but no code reads it, so a recipe can ignore it and pass.
- **Saturated fat is the hardest rule** on a high-protein vegetarian plan, in the live weeks so far. The writer now sees each ingredient's saturated fat; the eval will show how much that helps.
- **Scaling is uniform.** Oil and spices scale with everything else, so a piece-counted ingredient such as an egg can land on a fraction of a piece.
- **Glycemic load is often unknown.** USDA data has no glycemic index, so a meal with any food from the food file has none; a licensed table loaded through nutrition-mcp would fill it in.
- **The catalogue limits variety.** A larger licensed food table widens the recipes without code changes.
- **Free-tier models.** A stronger model is a config change, and the eval measures what it buys.
- **Browser storage.** Clearing the browser loses the data unless it was exported.

## What I learned

[Written as the milestones land.]
