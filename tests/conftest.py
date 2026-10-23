import os

import pytest

# LiteLLM fetches its price list from GitHub when it is imported, unless told to use the copy
# that ships with it. This runs before any test module imports litellm, so no test needs the
# network. meal_planner.llm sets the same variable for the app.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# The app refuses to start with these on, and LangGraph would send traces to LangSmith, so a
# developer's shell setting must not reach a test. A test that needs one sets it itself.
TRACING_VARIABLES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)


@pytest.fixture(autouse=True)
def tracing_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in TRACING_VARIABLES:
        monkeypatch.delenv(name, raising=False)
