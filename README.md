# meal-planner-agents

Work in progress. Writes a week of recipes for the meal plan you already follow, checks every nutrition number and rule in code through [nutrition-mcp](https://github.com/Varun-Kambali/nutrition-mcp), and tracks the meals you eat against the plan.

[![CI](https://github.com/Varun-Kambali/meal-planner-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/Varun-Kambali/meal-planner-agents/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) · **[Case study](docs/case-study.md)**

## Problem

Following a meal plan means turning every slot of it into something to cook: a recipe that hits the slot's numbers, uses the gear you own, fits the time you have, and doesn't taste like yesterday's. Doing that by hand for a week takes a long time. Recipes online are written for someone else's kitchen, and chat assistants guess the nutrition numbers. Details are in the [case study](docs/case-study.md).

## Solution (planned for v1)

- Enter your plan's rules once: daily targets, what each meal should be, your gear, time limits and food rules.
- A LangGraph pipeline writes a week of recipes, gets every number from nutrition-mcp over MCP, checks each recipe and day in plain Python, and sends failures back for a bounded repair.
- Track each meal with one tap, type what you ate when you went off plan, and see each day and the week against your targets.
- Your data stays in your browser.

Every feature, with what it must do, is in [docs/requirements.md](docs/requirements.md). The design is in [docs/architecture.md](docs/architecture.md).

## Results

The first full run of `make eval`: model gemini/gemini-3.1-flash-lite, commit b593c44, dataset 4a35ea1576a9, 2026-10-27. Every number below is in [`evals/results/2026-10-27_gemini-gemini-3.1-flash-lite_b593c44.json`](evals/results/2026-10-27_gemini-gemini-3.1-flash-lite_b593c44.json), and [`evals/results/summary.md`](evals/results/summary.md) has these tables with the checks that failed.

**Recipes.** Each of 20 synthetic plans gets a week from the agent pipeline and from a baseline that writes the whole week in one model call, with the same plan lines and ingredient list and no portion fitting or repair. The same code checks both.

| Measure | Baseline | This project |
|---|---|---|
| Recipes passing every hard check (share) | 8.7% (48 of 553) | 81.6% (451 of 553) |
| Recipes passing every hard check at their first attempt (share) | 8.7% (48 of 553) | 47.0% (260 of 553) |
| Recipes passing the variety checks (share) | 63.3% (350 of 553) | 91.7% (507 of 553) |
| Days passing every check (share) | 0.7% (1 of 140) | 37.1% (52 of 140) |
| Weeks where every recipe and day passes (share) | 0.0% (0 of 20) | 5.0% (1 of 20) |
| Recipes written (share) | 100.0% (553 of 553) | 100.0% (553 of 553) |
| Model calls per week (mean) | 1.0 | 14.2 |
| Tokens per week (mean) | 11,645.5 | 96,752.2 |
| Cost per week at list price (mean) | $0.009104 | $0.043386 |
| Seconds per week (p95) | 19.0 | 65.9 |

**Reading.** 53 typed meals, each labelled with its foods and amounts, every label checked against nutrition-mcp. Five name a food the reader cannot reach today, and are shown on their own.

| Measure | All meals | Expected misses (m01, m02, m03, m15, m52) | Other meals |
|---|---|---|---|
| Meals | 53 | 5 | 48 |
| Food precision | 97.6% (83 of 85) | 75.0% (6 of 8) | 100.0% (77 of 77) |
| Food recall | 95.4% (83 of 87) | 66.7% (6 of 9) | 98.7% (77 of 78) |
| Status accuracy | 94.3% (82 of 87) | 66.7% (6 of 9) | 97.4% (76 of 78) |
| Asked when it should (share) | 93.8% (15 of 16) | 50.0% (1 of 2) | 100.0% (14 of 14) |
| Labels that ask, food not returned by the reader | 1 of 16 | 1 of 2 | 0 of 14 |
| Calorie error against the label (MAPE) | 4.0% over 42 meals | 29.3% over 5 meals | 0.6% over 37 meals |
| Foods not in the data reported missing (share) | 100.0% (19 of 19) | 100.0% (1 of 1) | 100.0% (18 of 18) |
| Reads that failed | 0 of 53 | 0 of 5 | 0 of 48 |
| Model calls per meal (mean) | 1.13 | 1.20 | 1.12 |
| Tokens per meal (mean) | 832.6 | 866.2 | 829.1 |
| Cost per meal at list price (mean) | $0.000319 | $0.000333 | $0.000318 |
| Seconds per meal (p95) | 8.5 | 4.4 | 8.5 |

What this shows: the single call misses slot calories on most recipes, because it guesses grams. Portion fitting in code gets the pipeline to 260 recipes at the first attempt, and repair takes it to 451. Days are the weak point: 52 of 140 pass. The saturated-fat and fibre checks fail the most days (50 and 44), mostly because a food in the day, such as curry leaves, rohu or a dal, has no value for that nutrient in the food data. The check cannot then show the day is within its limit, so it fails it. The pipeline spends 14.2 model calls a week to the baseline's one. Cost is LiteLLM's list price for the paid tier; the free tier billed nothing.

What it does not show: this is one run of one free-tier model on synthetic plans and meals, so the spread between runs is unknown.

CI replays a recorded slice of three plans and ten meals on every PR, with no key, and fails when a number drops ([0008](docs/decisions/0008-smoke-replay-gates-every-pr.md)).

## Status

Milestones 1 to 3 of 5 are built: the foundations, the recipe pipeline, and tracking (reading a typed meal, and day and week totals). They run from the command line and from an HTTP API on your own machine. Milestone 4 is under way: the evals are built and their first results are above, and tracing is next. The browser app and a deployed demo come with milestone 5.

```bash
git clone https://github.com/Varun-Kambali/meal-planner-agents && cd meal-planner-agents
make setup
make test
```

Each command and the API start nutrition-mcp themselves. `week`, `read` and the API also need the model's key: copy `.env.example` to `.env` and set `GEMINI_API_KEY`. `summary` calls no model and needs no key.

```bash
uv run --env-file .env meal-planner week tests/fixtures/plans/office_weekday.json --out week.json
uv run --env-file .env meal-planner read "2 rotis and a katori of dal"
uv run meal-planner summary tests/fixtures/plans/office_weekday.json tests/fixtures/logs/office_weekday_log.json
make run   # the API on 127.0.0.1:8000
```

`week` writes a week of recipes for a rules file, `read` turns a typed meal into foods and numbers, and `summary` adds up a meal log against the plan. The log in the example is made up, for the same plan. The API does the same through `POST /v1/week` (streamed, with `POST /v1/week/replace` to write one recipe again), `POST /v1/log/read` and `POST /v1/summary`. Details are in [docs/architecture.md](docs/architecture.md).

## Design decisions

- [0001](docs/decisions/0001-follow-an-existing-plan.md) Follow the plan the user already has instead of writing one.
- [0002](docs/decisions/0002-model-writes-code-checks.md) The model writes recipes; code checks every number and rule.
- [0003](docs/decisions/0003-foods-through-nutrition-mcp-datasets.md) New foods reach nutrition-mcp through its custom-dataset folder.
- [0004](docs/decisions/0004-user-data-in-the-browser.md) The user's data stays in the browser, and the API keeps no state.
- [0005](docs/decisions/0005-code-fits-portions.md) Code fits portions, and the writer is shown nutrition per 100 g.
- [0006](docs/decisions/0006-reading-a-typed-meal.md) The model reads a typed meal and picks among nutrition-mcp's options; code checks amounts and units, and nutrition-mcp gives the numbers.
- [0007](docs/decisions/0007-the-log-holds-foods-and-amounts.md) The meal log holds foods and amounts, and the server works out the numbers.
- [0008](docs/decisions/0008-smoke-replay-gates-every-pr.md) A replay of the smoke slice gates every PR, held to its own numbers.

## License

MIT.
