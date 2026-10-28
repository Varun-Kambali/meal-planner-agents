"""What has to be true before the app answers, in one place for the API and the command line.

Starting runs nutrition-mcp, loads the catalogue against it and settles which model to use. Each
way it can fail ends in a StartupError whose message is one line, so a person who has just run
the command sees the reason and not a traceback of library internals.
"""

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from mcp import MCPError

from meal_planner.catalogue import Catalogue, CatalogueError, load_catalogue
from meal_planner.config import TRACING_VARIABLES, Settings
from meal_planner.llm import LiteLLMModel, ModelPort, missing_api_key
from meal_planner.nutrition import NutritionClient, NutritionError, NutritionPort

_TRUE = frozenset({"1", "true", "yes", "on"})


class StartupError(Exception):
    """The app cannot start. The message is one line and says why."""


@dataclass(frozen=True)
class Services:
    """What a run needs: the model, nutrition-mcp and the catalogue loaded against it."""

    model: ModelPort
    nutrition: NutritionPort
    catalogue: Catalogue


def check_tracing_off() -> None:
    for name in TRACING_VARIABLES:
        if os.environ.get(name, "").strip().lower() in _TRUE:
            raise StartupError(
                f"{name} is on, and LangGraph would send the plan rules to LangSmith: unset {name}"
            )


def check_model_key(model: str) -> None:
    missing = missing_api_key(model)
    if missing:
        raise StartupError(
            f"the model '{model}' needs these environment variables, which are not set: "
            f"{', '.join(missing)}"
        )


@asynccontextmanager
async def start_services(
    settings: Settings,
    *,
    model: ModelPort | None = None,
    nutrition: NutritionPort | None = None,
    needs_model: bool = True,
) -> AsyncIterator[Services]:
    """Check the environment, start nutrition-mcp and load the catalogue, until the block ends.

    A `model` or `nutrition` passed in is used as it is: the model's key is not looked for, and
    nutrition-mcp is not started. Tests pass fakes this way. `needs_model` is False for work that
    makes no model call, such as adding up a meal log, and then the key is not looked for either.
    """
    check_tracing_off()
    if model is None:
        if needs_model:
            check_model_key(settings.model)
        model = LiteLLMModel(settings.model)
    async with AsyncExitStack() as stack:
        if nutrition is None:
            nutrition = await _start_nutrition(stack, settings)
        yield Services(model, nutrition, await _load_catalogue(settings, nutrition))


async def _start_nutrition(stack: AsyncExitStack, settings: Settings) -> NutritionClient:
    try:
        return await stack.enter_async_context(NutritionClient.start(settings.nutrition_data_dirs))
    except Exception as exc:
        # The SDK raises its task groups' errors nested one in another, and the reason that is
        # worth reading is at the bottom. nutrition-mcp's own message went to stderr as it exited.
        raise StartupError(
            f"nutrition-mcp did not start: {_one_line(_innermost(exc))} (its own message is above)"
        ) from exc


async def _load_catalogue(settings: Settings, nutrition: NutritionPort) -> Catalogue:
    try:
        return await load_catalogue(settings.catalogue_path, nutrition)
    except CatalogueError as exc:
        raise StartupError(f"the ingredient catalogue cannot be used: {_one_line(exc)}") from exc
    except NutritionError as exc:
        # Raised only when the server could not be asked or its answer could not be read,
        # not for an id it refused.
        raise StartupError(f"nutrition-mcp stopped answering: {_one_line(exc)}") from exc


def _innermost(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


def _one_line(exc: BaseException) -> str:
    # str() of an MCPError is the repr of its arguments, code and all.
    text = exc.message if isinstance(exc, MCPError) else str(exc)
    return " ".join(text.split()) or type(exc).__name__
