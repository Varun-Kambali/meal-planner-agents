import logging
import os
from collections.abc import Iterator

import pytest

from meal_planner.config import TRACING_VARIABLES, get_settings

# LiteLLM fetches its price list from GitHub when it is imported, unless told to use the copy
# that ships with it. This runs before any test module imports litellm, so no test needs the
# network. meal_planner.llm sets the same variable for the app.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
# meal_planner.llm sets these two before it imports litellm, but a test file can import litellm
# first. Without them LiteLLM would load a .env found by walking up from its install folder, and
# log at the level that file sets. They are the values meal_planner.llm uses.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
os.environ.setdefault("LITELLM_LOG", "ERROR")


@pytest.fixture(autouse=True)
def tracing_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's shell setting must not reach a test: the app refuses to start with LangSmith
    tracing on, and Langfuse keys would send traces. A test that needs one sets it itself."""
    langfuse = [name for name in os.environ if name.startswith("LANGFUSE_")]
    for name in [*TRACING_VARIABLES, *langfuse]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A MEAL_PLANNER_ variable in a developer's shell must not change a test's settings, and
    get_settings, which caches, starts and ends each test empty."""
    for name in [n for n in os.environ if n.startswith("MEAL_PLANNER_")]:
        monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    """Every logging record, including those of our own loggers, which do not propagate."""
    collector = _Collector()
    loggers = [logging.getLogger()] + [
        logging.getLogger(name)
        for name in list(logging.root.manager.loggerDict)
        if name.startswith("meal_planner")
    ]
    root_level = logging.getLogger().level
    logging.getLogger().setLevel(logging.DEBUG)
    for logger in loggers:
        logger.addHandler(collector)
    yield collector.records
    for logger in loggers:
        logger.removeHandler(collector)
    logging.getLogger().setLevel(root_level)
