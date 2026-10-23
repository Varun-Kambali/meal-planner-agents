"""The HTTP API: a stateless FastAPI app over the week graph.

Nothing about the user is kept. The rules arrive with each request, the answer streams back as
server-sent events, and the app writes no log line that holds them or any error text that could.
Invalid input is a 422 and an unreachable nutrition-mcp before a stream starts is a 503; once a
stream has started its status is sent, so a failure after that is a last `error` event.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from meal_planner.catalogue import Category
from meal_planner.config import Settings
from meal_planner.llm import ModelPort
from meal_planner.log import get_logger
from meal_planner.middleware import (
    REQUEST_ID_HEADER,
    BodyLimit,
    RateLimit,
    RateLimiter,
    RequestContext,
)
from meal_planner.nutrition import NutritionError, NutritionPort
from meal_planner.problems import Problem, problems_from, unknown_foods
from meal_planner.recipes import CheckedRecipe, DraftRecipe
from meal_planner.rules import Allergen, DayKey, Diet, PlanRules, Slug, expand
from meal_planner.startup import Services, start_services
from meal_planner.week import (
    WeekDone,
    WeekEvent,
    WeekResult,
    replace_recipe,
    run_week,
    with_numbers,
)

logger = get_logger(__name__)

REPLACE_PATH = "/v1/week/replace"
# A week has a recipe for each of 7 days and each slot, and a plan has at most 8 slots.
MAX_WEEK_RECIPES = 7 * 8

router = APIRouter()


class CatalogueRow(BaseModel):
    id: str
    name: str
    category: Category


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplaceEntry(_Body):
    """A recipe of the week as the model wrote it.

    There is no field for its numbers or its failures: the server works those out again, because
    anything the browser echoes back could have been changed on the way.
    """

    day: DayKey
    slot_id: Slug
    draft: DraftRecipe
    # How many times the recipe has been tried, which the new recipe's count continues from.
    attempts: int = Field(default=1, ge=1)


class ReplaceRequest(_Body):
    rules: PlanRules
    recipes: list[ReplaceEntry] = Field(max_length=MAX_WEEK_RECIPES)
    # The recipe to write again.
    day: DayKey
    slot_id: Slug


@dataclass(frozen=True)
class _Replacement:
    rules: PlanRules
    week: WeekResult
    day: DayKey
    slot_id: str


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _invalid(problems: list[Problem]) -> RequestValidationError:
    return RequestValidationError([{**problem, "type": "value_error"} for problem in problems])


def _require_known_foods(rules: PlanRules, services: Services, under: tuple[str, ...]) -> None:
    problems = unknown_foods(rules, services.catalogue, under=under)
    if problems:
        raise _invalid(problems)


async def _checked_rules(
    rules: PlanRules, services: Annotated[Services, Depends(_services)]
) -> PlanRules:
    """The rules, if every food they name is in the catalogue. This runs before a stream starts."""
    _require_known_foods(rules, services, ("body",))
    return rules


async def _replacement(
    body: ReplaceRequest, services: Annotated[Services, Depends(_services)]
) -> _Replacement:
    """The week as a WeekResult, rebuilt from the plan and the drafts, before a stream starts.

    The specs come from the plan and the numbers from nutrition-mcp, so nothing in the week that
    the replacement is checked against was supplied by the browser except the recipes' words.
    """
    rules = body.rules
    _require_known_foods(rules, services, ("body", "rules"))
    specs = {(spec.day, spec.slot.id): spec for spec in expand(rules)}
    problems: list[Problem] = []
    if (body.day, body.slot_id) not in specs:
        problems.append(
            {"loc": ["body", "slot_id"], "msg": f"the plan has no slot '{body.slot_id}'"}
        )
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(body.recipes):
        key = (entry.day, entry.slot_id)
        where: list[str | int] = ["body", "recipes", index]
        if key not in specs:
            message = f"the plan has no slot '{entry.slot_id}' for {entry.day}"
            problems.append({"loc": [*where, "slot_id"], "msg": message})
        elif key in seen:
            problems.append({"loc": where, "msg": f"{entry.day} {entry.slot_id} is listed twice"})
        elif entry.draft.day != entry.day:
            message = f"the recipe is for {entry.draft.day} but its entry is for {entry.day}"
            problems.append({"loc": [*where, "draft", "day"], "msg": message})
        seen.add(key)
    if problems:
        raise _invalid(problems)

    recipes: list[CheckedRecipe] = []
    try:
        for entry in body.recipes:
            unchecked = CheckedRecipe(
                spec=specs[(entry.day, entry.slot_id)],
                draft=entry.draft,
                nutrition=None,
                failures=[],
                attempts=entry.attempts,
            )
            recipes.append(
                await with_numbers(
                    unchecked,
                    nutrition=services.nutrition,
                    catalogue=services.catalogue,
                    raise_if_unreachable=True,
                )
            )
    except NutritionError as exc:
        # The message can name a food from the drafts, so only the class is logged.
        logger.error(
            "recompute_failed", extra={"task": "replace", "error_type": type(exc).__name__}
        )
        detail = (
            "nutrition-mcp could not be reached to work out the recipes' numbers (step: recompute)"
        )
        raise HTTPException(status_code=503, detail=detail) from exc
    week = WeekResult(
        recipes=recipes,
        days=[],
        no_waste={},
        unwritten=[],
        calls=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=None,
    )
    return _Replacement(rules, week, body.day, body.slot_id)


async def _cleanup() -> AsyncIterator[AsyncExitStack]:
    """An exit stack that unwinds once the response is over, however it ended."""
    async with AsyncExitStack() as stack:
        yield stack


async def _stream(
    step: str, run: AsyncGenerator[WeekEvent], settings: Settings, cleanup: AsyncExitStack
) -> AsyncIterator[ServerSentEvent]:
    """The run's events as server-sent events, always ending with `week_done` or `error`.

    Status 200 is sent before the first event, so a run that raises can only say so in the
    stream. The event holds no text from the exception, which can quote the request, and the log
    line holds only its class.

    A client that goes away cancels the response while this generator is parked at a yield,
    where nothing is raised in it. Closing the run stops the graph, and with it the model calls,
    as soon as the response is over; leaving that to garbage collection would not.
    """
    cleanup.push_async_callback(run.aclose)
    started = time.perf_counter()
    try:
        async with aclosing(run) as events:
            async for event in events:
                yield ServerSentEvent(raw_data=event.model_dump_json(), event=event.kind)
                if isinstance(event, WeekDone):
                    week = event.week
                    logger.info(
                        "run_done",
                        extra={
                            "task": step,
                            "model": settings.model,
                            "input_tokens": week.input_tokens,
                            "output_tokens": week.output_tokens,
                            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                            "cost_usd": week.cost_usd,
                        },
                    )
    except asyncio.CancelledError:
        logger.info("run_cancelled", extra={"task": step})
        raise
    except Exception as exc:
        logger.error("run_failed", extra={"task": step, "error_type": type(exc).__name__})
        error = {"kind": "error", "step": step, "message": "the run stopped before it finished"}
        yield ServerSentEvent(raw_data=json.dumps(error), event="error")


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/catalogue", response_model=list[CatalogueRow])
async def get_catalogue(
    services: Annotated[Services, Depends(_services)],
    diet: Diet = "non_veg",
    exclude_allergens: Annotated[list[Allergen] | None, Query()] = None,
) -> list[CatalogueRow]:
    """The ingredients a plan form can offer, for a diet and the allergens to avoid."""
    items = services.catalogue.allowed(diet, exclude_allergens or ())
    return [CatalogueRow(id=i.id, name=i.name, category=i.category) for i in items]


@router.post("/v1/week", response_class=EventSourceResponse)
async def write_week(
    rules: Annotated[PlanRules, Depends(_checked_rules)],
    services: Annotated[Services, Depends(_services)],
    settings: Annotated[Settings, Depends(_settings)],
    cleanup: Annotated[AsyncExitStack, Depends(_cleanup)],
) -> AsyncIterable[ServerSentEvent]:
    run = run_week(
        rules,
        model=services.model,
        nutrition=services.nutrition,
        catalogue=services.catalogue,
        settings=settings,
    )
    async for message in _stream("week", run, settings, cleanup):
        yield message


@router.post(REPLACE_PATH, response_class=EventSourceResponse)
async def write_one_again(
    replacement: Annotated[_Replacement, Depends(_replacement)],
    services: Annotated[Services, Depends(_services)],
    settings: Annotated[Settings, Depends(_settings)],
    cleanup: Annotated[AsyncExitStack, Depends(_cleanup)],
) -> AsyncIterable[ServerSentEvent]:
    run = replace_recipe(
        replacement.rules,
        replacement.week,
        replacement.day,
        replacement.slot_id,
        model=services.model,
        nutrition=services.nutrition,
        catalogue=services.catalogue,
        settings=settings,
    )
    async for message in _stream("replace", run, settings, cleanup):
        yield message


def create_app(
    settings: Settings,
    *,
    model: ModelPort | None = None,
    nutrition: NutritionPort | None = None,
) -> FastAPI:
    """The app. It starts nutrition-mcp and loads the catalogue when it starts serving.

    A `model` or `nutrition` passed in is used as it is, which is how tests run without a key or
    a subprocess. The checks on what is passed in are the ones `start_services` makes.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with start_services(settings, model=model, nutrition=nutrition) as services:
            app.state.services = services
            yield

    app = FastAPI(title="meal-planner-agents", lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's own body repeats the rejected input, which is the user's rules.
        return JSONResponse({"detail": problems_from(exc.errors())}, status_code=422)

    app.include_router(router)
    _add_middleware(app, settings)
    return app


def _add_middleware(app: FastAPI, settings: Settings) -> None:
    """Each one added wraps the ones before it, so the last is the first to see a request.

    CORS is last so that a refusal from the others still carries its headers, and a preflight is
    answered before the rate limit counts it. The request id comes next so that every other
    refusal has one.
    """
    app.add_middleware(
        BodyLimit,
        limit=settings.max_body_bytes,
        per_path={REPLACE_PATH: settings.max_replace_body_bytes},
    )
    app.add_middleware(
        RateLimit, limiter=RateLimiter(settings.rate_limit_per_minute), prefix="/v1/"
    )
    app.add_middleware(RequestContext)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
            expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
        )
