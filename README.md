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

## Status

Milestones 1 to 3 of 5 are built: the foundations, the recipe pipeline, and tracking (reading a typed meal, and day and week totals). They run from the command line and from an HTTP API on your own machine. Milestone 4 is next: the evals and tracing. The browser app and a deployed demo come with milestone 5, so there are no results yet.

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
- [0006](docs/decisions/0006-reading-a-typed-meal.md) The model names the foods in a typed meal; nutrition-mcp and code decide the rest.
- [0007](docs/decisions/0007-the-log-holds-foods-and-amounts.md) The meal log holds foods and amounts, and the server works out the numbers.

## License

MIT.
