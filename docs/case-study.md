# Case study: meal-planner-agents

Written at kickoff on 2026-10-21, before any code, and agreed with the plan. It is updated as each milestone lands, and the results arrive with the evals.

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

The browser keeps the user's rules, recipes and log. A stateless FastAPI service runs a LangGraph graph: it expands the rules into slots, writes each slot's 7 recipes in one model call (all slots in parallel), gets every recipe's numbers from nutrition-mcp, checks recipes, days and the week in plain Python, and sends failing recipes back with the exact failures, at most 3 times. The diagram is in [architecture.md](architecture.md).

The decisions that mattered:

1. **Follow an existing plan instead of writing one** ([0001](decisions/0001-follow-an-existing-plan.md)). The original idea also covered shopping, prep and rotation, which is several products. Starting from the plan the user already has keeps v1 to the part that actually took me the longest.
2. **The model writes, code checks** ([0002](decisions/0002-model-writes-code-checks.md)). Asking the model to check its own recipe lost, because its arithmetic is the problem nutrition-mcp measured. Picking recipes from a fixed list of dishes lost too: it can't fit a slot's style and limits, and it can't give variety.
3. **One model call per slot for the whole week** instead of one per recipe or one for everything. Per recipe means 28 calls and no view of the other recipes, so variety suffers. One call for everything is the baseline the eval compares against.
4. **New foods go through nutrition-mcp's dataset folder** ([0003](decisions/0003-foods-through-nutrition-mcp-datasets.md)). My recipes need oats, fruit, seeds, olive oil, whey and chicken, which nutrition-mcp doesn't bundle. Adding them to nutrition-mcp itself would mix this project's needs into its release, and a second nutrition source in this project would split where numbers come from.
5. **The user's data stays in the browser** ([0004](decisions/0004-user-data-in-the-browser.md)). Accounts and a database would mean building login and holding people's food logs on a public demo. A stateless API with browser storage and a file export is less to build and leaves nothing to leak.

## Results

[The recipe eval and the reading eval run in M4. Their table will be copied here from `evals/results/summary.md`.]

## Limits, and what better inputs would change

- **Time is the model's claim.** Active and total minutes are what the recipe states, checked against the slot's limits but not measured. Cooking a sample of recipes is the only real check.
- **Glycemic load is often unknown.** USDA data has no glycemic index, so any meal with a food from this project's food file has no glycemic load. A licensed GI table loaded through nutrition-mcp would fill this in.
- **The catalogue limits variety.** The model can only use foods that have sourced numbers. A larger licensed food table widens the recipes without code changes.
- **Free-tier models.** Quality and speed depend on the free Gemini tier. A stronger model is a config change, and the eval measures what it buys.
- **Browser storage.** Clearing the browser loses the data unless it was exported.

## What I learned

[Written as the milestones land.]
