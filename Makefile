COV_MIN ?= 80
# make foods and make run read their keys from this file when it exists. Cached records need no key.
ENV_FILE ?= .env

.PHONY: setup lint test eval eval-smoke run run-api run-frontend docker foods

setup:
	uv sync --all-groups
	uv run pre-commit install
	npm --prefix frontend ci

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

# The coverage gate measures evals/ as well as the app, since it computes the published numbers.
test:
	uv run pytest --cov=src --cov=evals --cov-fail-under=$(COV_MIN)

foods:
	uv run $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE)) --group build-data python scripts/build_foods.py

# The API and the browser app on this machine: open http://127.0.0.1:5173. Ctrl-C stops both.
run:
	$(MAKE) -j2 run-api run-frontend

# The API on this machine only. It starts nutrition-mcp and needs the model's key (GEMINI_API_KEY
# for the default model) in the environment or in ENV_FILE.
# --no-access-log: uvicorn's access line includes the query string, which holds the diet and the
# allergens of a plan, and logs never hold a plan's rules. Keep it off wherever this is deployed.
run-api:
	uv run $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE)) uvicorn --factory meal_planner.api:app_factory --host 127.0.0.1 --port 8000 --no-access-log

# The Vite dev server. It sends /v1 calls to run-api on 127.0.0.1:8000, so no CORS setting is
# needed here.
run-frontend:
	npm --prefix frontend run dev

# The full eval: every plan through the pipeline and the single-call baseline, and every typed
# meal through the reader. It calls the model with its key (GEMINI_API_KEY for the default model,
# from the environment or ENV_FILE). A run that stops keeps what it finished; finish it with
# make eval EVAL_ARGS="--resume evals/results/runs/<name>".
EVAL_ARGS ?=
eval:
	uv run $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE)) python evals/run_evals.py --suite all $(EVAL_ARGS)

# The smoke slice replayed from its recording and held to the thresholds: no key and no network.
# Its results go to .eval-smoke/, which each run starts afresh.
SMOKE_RECORDING ?= evals/recordings/smoke.jsonl
THRESHOLDS ?= evals/thresholds.toml
eval-smoke:
	rm -rf .eval-smoke
	uv run python evals/run_evals.py --smoke --replay $(SMOKE_RECORDING) --check $(THRESHOLDS) --out .eval-smoke

docker:
	docker build -t meal-planner-agents .
