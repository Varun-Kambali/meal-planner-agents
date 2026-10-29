# Requirements

Status: agreed at kickoff, 2026-10-21. The reasoning behind the project is in [case-study.md](case-study.md); the design is in [architecture.md](architecture.md).

## Problem

People who already follow a meal plan still have to turn every slot of it into something to cook. A plan says "lunch: an air-fryer dish, about a quarter of the day's calories, at least 40 g protein, no peanuts". Someone has to write a recipe that fits, then another for the next day that fits just as well and tastes different, and keep that up all week. Doing that by hand takes a long time. Recipes from Instagram, YouTube and food blogs are written for someone else's kitchen, pantry and time. A chat assistant writes recipes quickly, but it guesses the nutrition numbers: a free Gemini model answering from memory missed calories by 16.0% on average in [nutrition-mcp's grounding run](https://github.com/Varun-Kambali/nutrition-mcp/blob/main/evals/results/summary.md). Nothing checks its recipes against the plan's rules before the user cooks them. Once the week starts, there is no simple way to see whether the meals eaten match the plan.

## Users

1. **A person who follows a plan they wrote themselves,** with fitness goals and little time or energy to cook. v1 is built around this user.
2. **A person whose dietitian or coach gave them a plan.** They enter its rules once.
3. **A dietitian or coach** who writes the rules for a client. v1 supports this only through exporting and importing the rules file; a dashboard for many clients is out of scope.
4. **Developers** who need a model to generate something under hard limits. The pattern here (the model writes, code checks every number against a data server over MCP, failures go back with reasons) carries over to other generation tasks.

## Definition of done

v1 is done when a person could rely on it: every MVP box below is ticked, it runs from a fresh clone by following the README, the tests and the smoke eval pass in CI, and nothing breaks on the basic paths (bad rules, a model timeout, a typed food that isn't in the data). Progress is tracked against the milestones, not dates.

## MVP features

### The plan: rules the user enters

- [ ] **Plan rules,** validated by a Pydantic model with a matching JSON Schema, entered through a form or imported as a JSON file:
  - **Day types** (for example a training day and a rest day), each with daily targets: calories, protein (a minimum), carbohydrate, fat, saturated fat (a maximum) and fibre (a minimum). Optional daily targets for any other nutrient nutrition-mcp reports, such as iron or calcium.
  - **The week:** which day type each day of the week is.
  - **Slots** per day (breakfast, lunch, snack, dinner, or the user's own names), each with an eating time, the share of the day's calories and protein it carries, a style (for example "overnight oats" or "air-fryer dish"), when it gets cooked (fresh, the evening before, the night before with no cooking in the morning), and limits on active and total minutes.
  - **Kitchen:** the gear the user owns, from a fixed list (air fryer, pressure cooker, microwave, oven, stovetop pan, blender, jar with lid, and so on).
  - **Food rules:** diet type (`veg`, `egg`, `non_veg`, `vegan`), allergen groups to exclude, single foods to exclude, foods the user likes, and cuisines to draw on (for example South Indian, Japanese, Mediterranean).
  - **Shopping days** per week, used by the no-waste check.
- [ ] **Validation messages** name the field and the problem. Slot shares that don't add up to 100% of the day are an error.

### Recipes for a week

- [ ] **Write the week:** one recipe per slot per day, for one serving. Each recipe has a title, a cuisine, ingredients with grams (and a household measure where one is known), short numbered steps, active and total minutes, the gear it uses, when to cook it, how to store it, one swap, and one line on how it fits the plan.
- [ ] **Ingredients only from the catalogue** (nutrition-mcp's bundled ingredients plus this project's own food file). The writer is given the catalogue, and an ingredient outside it fails the recipe.
- [ ] **Every number from nutrition-mcp, through code.** Each recipe's 16 nutrients and its glycemic load come from nutrition-mcp's `compute_meal`. No nutrition number the model writes is ever shown to the user.
- [ ] **Hard checks in code** on every recipe and every day:
  - The recipe's calories are within 10% of its slot's share, and its protein is at least 90% of its share.
  - The day's calories are within 5% of the target, protein is at or above the target, saturated fat is at or below its cap, fibre is at or above its floor, and each optional nutrient target (set as a minimum or a maximum) is met.
  - Carbohydrate and fat are reported against their targets but don't fail a day: with calories and protein fixed, they follow from the ingredients, and gating on them too would leave little room for variety.
  - No excluded food, no excluded allergen group (from a fixed list: gluten, milk, egg, peanut, tree nuts, soy, fish, crustaceans, sesame, mustard), nothing outside the diet type.
  - Only gear the user owns. Active and total minutes, as the recipe states them, within the slot's limits.
  - **Variety:** no two recipes in the same slot in one week are too similar (ingredient overlap above a threshold kept in config, or the same title), and cuisines spread across the week from the plan's list.
  - **No waste:** an ingredient that keeps less than a set number of days once opened, and that the week uses only a small part of its usual pack, is flagged.
  - The tolerances and thresholds live in config, with tests at their edges.
- [ ] **Repair, bounded.** A recipe or day that fails goes back to the model with the exact failures, up to 3 attempts. After that the recipe is shown with its failures listed, never as passing.
- [ ] **Replace one recipe.** The user can ask for a different recipe in one slot. The same checks run, and the rest of the week stays as it is.
- [ ] **Live progress.** The UI shows each slot as it is written, checked and repaired, streamed over server-sent events.
- [ ] **Printable pages:** one recipe per A4 page, grams and short steps, printed from the browser.

### Tracking

- [ ] **One tap per planned meal:** as planned, a different amount (half, one and a half, or grams), swapped, or skipped.
- [ ] **Swapped:** the user types what they ate in plain words ("2 rotis and a katori of dal at the canteen"). The model reads it into foods and amounts, the foods come only from nutrition-mcp's search results, nutrition-mcp computes the numbers, and the user confirms or edits each item before it is saved. A food the data doesn't have is shown as missing and left out of the totals, never replaced by another food.
- [ ] **Day view:** eaten against target for every nutrient the plan targets, plus glycemic load per meal when every item in it has one. Otherwise it reads "not known" with the number of items that lack it.
- [ ] **Week view:** the share of planned meals eaten as planned, and the days that hit target.

### Data

- [ ] **This project's food file:** the ingredients the plans need that nutrition-mcp lacks (oats, most fruit, olive oil, seeds, whey, chicken, tofu and others), taken from USDA FoodData Central (public domain) in nutrition-mcp's custom-dataset format and loaded through `NUTRITION_MCP_DATA_DIRS`. Each row records its FoodData Central record. Every match is reviewed by hand. A food with no good match is left out; no value is invented.
- [ ] **Catalogue:** for every ingredient the writer may use, its id, display name, shop category, allergen groups, a typical pack size, how many days it keeps once opened, and grams per household measure (piece, tbsp, cup) from USDA portion data where nutrition-mcp has no measure for it, so "2 eggs" or "1 tbsp chia" can be turned into grams. No nutrition values live here. At startup every id is checked against nutrition-mcp, so a typo stops startup with the id named.
- [ ] **The user's data stays in their browser:** plan rules, the week's recipes and the meal log, with export and import as one JSON file. The log stores food ids and amounts; the server recomputes numbers from nutrition-mcp when it builds a summary. The server stores nothing about the user.

### Backend

- [ ] **FastAPI, stateless.** Endpoints to write a week (streaming), replace one recipe, read a typed meal, and summarise a day or a week from a log. Each request carries everything it needs.
- [ ] **nutrition-mcp `v1.0.0`** is a pinned dependency, run as a stdio subprocess. Only its read-only tools are called.
- [ ] **One model client** (LiteLLM) with timeouts, retries with backoff and structured output parsed into Pydantic models. The model id lives in config; the default is a free-tier Gemini model.
- [ ] **Rate limits and request size limits** on the public endpoints; CORS set to the frontend's origin.
- [ ] **Structured JSON logs** with the request id, model, tokens, latency and cost of every model call, and never the user's rules or typed text.

### Observability

- [ ] **Langfuse traces** for every run: each step, the model, tokens, cost, latency, and which checks passed or failed. The user's rules and typed text are masked in traces. Tracing is off unless its keys are set.

### Evaluation

- [ ] **Recipe eval:** 20 synthetic plans in `evals/datasets/plans/`, shaped like real plans with no personal data. The full pipeline against a single-call baseline (one model call with the same rules and catalogue, no checks and no repair), both scored by the same code checks. Measures: the share of recipes that pass every recipe check (the headline; a day's checks are measured on the days' own rows), the share of weeks where every recipe and day passes, the variety pass rate, the pass rate before and after repair, tokens and cost per week, and p95 time per week.
- [ ] **Reading eval:** about 50 typed meals written by hand and labelled with their foods and grams, including foods the data doesn't have. Measures: food match (precision and recall), calorie error against the label, and the share of not-in-data foods reported missing.
- [ ] **Smoke eval in CI:** a fixed slice replayed from recorded model responses, so CI costs nothing and gives the same result every time, with thresholds in `evals/thresholds.toml` that fail the build on a regression.
- [ ] **Full runs on demand** (`make eval`), with results committed with the date, model, commit, dataset version and cost.

### Frontend

- [ ] **A React app (Vite, TypeScript)** built from a saved design system: the plan form, the week with live progress, recipe pages, tracking and the day and week views. Keyboard-usable, visible focus, WCAG 2 AA contrast, usable at phone width.

### Quality

- [ ] Unit tests with every model and nutrition-mcp call mocked. Integration tests against the real nutrition-mcp over stdio. At least 80% coverage on core logic.
- [ ] CI runs lint, the type check, tests, the smoke eval, gitleaks, the Docker build with a smoke run of the container, and the frontend's lint, type check, build and pa11y checks.

### Docs

- [ ] README per the template, with a demo GIF, the results table, a Mermaid diagram, a quickstart and limitations.
- [ ] The case study, kept current. An ADR for each decision.
- [ ] `portfolio.json` and the `portfolio` repo topic.

## Non-goals for v1

- Writing a plan or targets from scratch. The user brings the rules. (nutrition-mcp's `compute_targets` could later fill in calorie and protein targets for someone who has none.)
- Health conditions such as diabetes or a diagnosed deficiency, condition-aware rules, and reference intakes by age and sex.
- Shopping lists, a prep calendar, batch cooking across days, and rotating recipes across weeks.
- Supplements, water, sleep and other habits.
- Budgets and prices.
- Photo logging, barcode scanning and packaged foods.
- Accounts, login, sync between devices, and a dashboard for dietitians.
- A mobile app, and recipes for more than one person.

## Anti-goals: v1 fails if any of these happen, even if it runs

- A nutrition number reaches the user without coming from nutrition-mcp through code.
- A recipe that breaks a hard rule is shown as passing, or a failure is hidden.
- A typed food that isn't in the data is quietly swapped for another food.
- A recipe uses an ingredient outside the catalogue.
- Anything about the user is stored on the server, or sent to traces or logs unmasked.
- The app suggests a medicine, a supplement or a dose, or infers a health condition.
- The README claims a result that isn't in a committed results file.

## Constraints

- **Progress, not dates.** Milestones finish when their checks pass.
- **Cost: free only.** The Gemini free tier, a free USDA FoodData Central key for the food-file build, and Langfuse's free cloud tier (or no tracing). Keys live in `.env`, never in the repo. Every full model run is agreed first, because it uses a personal key.
- **Free-tier quotas:** a week must fit the model's free requests-per-minute and per-day limits. Check the current limits before the pipeline milestone.
- **No local Docker:** images are built and smoke-tested in CI.
- **Licenses:** USDA data is public domain; nutrition-mcp's data keeps its own attributions, served by its sources resource. Code is MIT.
- **Public-safe:** synthetic plans and typed meals only. No real person's plan, targets or health details.
- **Deploying** a public demo happens only after an explicit yes, on free platforms, with rate limits and a spend cap.

## Success metrics

1. **Headline:** the share of recipes that pass every recipe check, pipeline against single call, over the 20 synthetic plans. Days are measured on their own rows.
2. The variety pass rate, and the pass rate before and after repair.
3. Reading accuracy on typed meals.
4. Cost and p95 time per week.
5. Every MVP box ticked from a fresh clone.

## Milestones

| # | Name | Done when |
|---|---|---|
| M1 | Foundations | Repo scaffolded with CI. The plan-rules model and schema, the catalogue, this project's food file built and loaded by nutrition-mcp, and the nutrition-mcp client with its startup check are tested. Kickoff docs committed |
| M2 | Recipe pipeline | For a plan file, the graph writes a week from the CLI and from the streaming API, with every hard check and the bounded repair in code, plus replacing one recipe. Unit tests with a mocked model pass; one live week on a synthetic plan is reviewed |
| M3 | Tracking | Reading a typed meal, and day and week summaries, work through the API, with tests for missing foods, unknown units and bad logs |
| M4 | Evals and tracing | 20 synthetic plans and about 50 typed meals committed. Baseline and pipeline runs committed with date, model and commit. The smoke eval blocks a PR on regression. Langfuse traces show cost and latency per week |
| M5 | UI and ship | Design system saved, React UI done and checked at desktop and phone widths with the keyboard, browser storage with export and import, Docker image smoke-tested in CI, demo deployed (after a yes), README with the demo GIF and numbers, case study final, `v1.0.0` tagged |

## Open items

- How many ingredients the food file needs. The synthetic plans and the first recipe runs set this.
- Hosting the API with nutrition-mcp as a subprocess on a free tier (Hugging Face Spaces is the default). Check its limits before M5.
