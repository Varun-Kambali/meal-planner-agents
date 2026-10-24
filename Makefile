COV_MIN ?= 80
# make foods and make run read their keys from this file when it exists. Cached records need no key.
ENV_FILE ?= .env

.PHONY: setup lint test eval eval-smoke run docker foods

setup:
	uv sync --all-groups
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest --cov=src --cov-fail-under=$(COV_MIN)

foods:
	uv run $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE)) --group build-data python scripts/build_foods.py

# The API on this machine only. It starts nutrition-mcp and needs the model's key (GEMINI_API_KEY
# for the default model) in the environment or in ENV_FILE.
# --no-access-log: uvicorn's access line includes the query string, which holds the diet and the
# allergens of a plan, and logs never hold a plan's rules. Keep it off wherever this is deployed.
run:
	uv run $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE)) uvicorn --factory meal_planner.api:app_factory --host 127.0.0.1 --port 8000 --no-access-log

eval eval-smoke:
	@echo "make $@ is added in a later milestone"; exit 1

docker:
	docker build -t meal-planner-agents .
