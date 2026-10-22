COV_MIN ?= 80

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

eval eval-smoke run foods:
	@echo "make $@ is added in a later milestone"; exit 1

docker:
	docker build -t meal-planner-agents .
