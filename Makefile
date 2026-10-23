COV_MIN ?= 80
# make foods reads USDA_FDC_API_KEY from this file when it exists. Cached records need no key.
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

eval eval-smoke run:
	@echo "make $@ is added in a later milestone"; exit 1

docker:
	docker build -t meal-planner-agents .
