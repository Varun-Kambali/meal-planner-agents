FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# nutrition-mcp is installed from a git tag, and uv fetches it with the git command.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 app
COPY --from=build --chown=app:app /app/.venv /app/.venv
# The package is installed into the venv, so its own paths do not lead to a source checkout:
# the food files and the catalogue are copied here and found through these variables.
COPY --chown=app:app data /app/data
ENV PATH="/app/.venv/bin:$PATH" \
    MEAL_PLANNER_NUTRITION_DATA_DIRS=/app/data/foods \
    MEAL_PLANNER_CATALOGUE_PATH=/app/data/catalogue.yaml \
    PORT=8000
USER app
EXPOSE 8000
# The probe uses Python's standard library, since the slim image has no curl. The app starts
# nutrition-mcp before it answers, so the first check waits for that.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=4)"]
# Needs the model's key in the environment (GEMINI_API_KEY for the default model). Same server
# as `make run-api`, with the access log off because its line holds the query string, which
# holds a plan's diet and allergens. A host that sets PORT is followed.
#
# Client addresses: the rate limit counts per address. uvicorn reads X-Forwarded-For only from
# the addresses in FORWARDED_ALLOW_IPS, which by default are loopback, so a visitor cannot
# choose the address the limit counts. Behind a host's proxy every visitor would share the
# proxy's address, so the deployment sets FORWARDED_ALLOW_IPS to that proxy ('*' only where the
# app cannot be reached except through it). It is not set here.
CMD ["sh", "-c", "exec uvicorn --factory meal_planner.api:app_factory --host 0.0.0.0 --port \"$PORT\" --no-access-log"]
