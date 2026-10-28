"""The HTTP API: a stateless FastAPI app over the week graph.

Nothing about the user is kept. The rules arrive with each request, the answer streams back as
server-sent events, and the app writes no log line that holds them or any error text that could.
Invalid input is a 422 and an unreachable nutrition-mcp before a stream starts is a 503; once a
stream has started its status is sent, so a failure after that is a last `error` event.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from meal_planner.catalogue import Category
from meal_planner.config import Settings, get_settings
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
from meal_planner.problems import Problem, meal_text_problems, problems_from, unknown_foods
from meal_planner.reader import ReadError, ReadResult, read_meal
from meal_planner.recipes import CheckedRecipe, DraftRecipe
from meal_planner.rules import Allergen, DayKey, Diet, PlanRules, Slug, expand
from meal_planner.startup import Services, start_services
from meal_planner.summary import LogRejected, MealLog, SummaryError, WeekSummary, summarise
from meal_planner.week import replace_recipe, run_week, with_numbers
from meal_planner.week_events import WeekDone, WeekEvent, WeekResult

logger = get_logger(__name__)

REPLACE_PATH = "/v1/week/replace"
READ_PATH = "/v1/log/read"
SUMMARY_PATH = "/v1/summary"
# A week has a recipe for each of 7 days and each slot, and a plan has at most 8 slots.
MAX_WEEK_RECIPES = 7 * 8
# The most times one recipe is tried: the writing, then `max_repair_attempts` repairs, which is 3
# by default. A deployment that raises that setting has to raise this too.
MAX_ATTEMPTS = 4

router = APIRouter()


class CatalogueRow(BaseModel):
    id: str
    name: str
    category: Category


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadRequest(_Body):
    text: str


class SummaryRequest(_Body):
    rules: PlanRules
    log: MealLog


class ReplaceEntry(_Body):
    """A recipe of the week as the model wrote it.

    There is no field for its numbers or its failures: the server works those out again, because
    anything the browser echoes back could have been changed on the way.
    """

    day: DayKey
    slot_id: Slug
    draft: DraftRecipe
    # How many times the recipe has been tried. A recipe that is kept keeps its count in the
    # result; the recipe being replaced starts again at 1.
    attempts: int = Field(default=1, ge=1, le=MAX_ATTEMPTS)


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


async def _require_nutrition(services: Services) -> None:
    """Ask nutrition-mcp something small, so a week is not started with nobody to check it.

    The graph turns a dead nutrition-mcp into failing recipes and repairs them, which spends
    model calls on nothing. The question holds no text from the request.
    """
    try:
        await services.nutrition.search("rice", limit=1)
    except NutritionError as exc:
        logger.error("nutrition_unreachable", extra={"error_type": type(exc).__name__})
        detail = "nutrition-mcp could not be reached (step: nutrition)"
        raise HTTPException(status_code=503, detail=detail) from exc


async def _checked_rules(
    rules: PlanRules, services: Annotated[Services, Depends(_services)]
) -> PlanRules:
    """The rules, once every food they name is in the catalogue and nutrition-mcp answers.

    This runs before a stream starts, so a refusal is a status code. It runs only for rules that
    passed validation, so invalid rules are a 422 whether or not nutrition-mcp is up.
    """
    _require_known_foods(rules, services, ("body",))
    await _require_nutrition(services)
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
        # with_numbers raises only an error marked `unreachable`; a refusal, named or not, leaves
        # that recipe without numbers. The message can name a food from the drafts, so only the
        # class is logged.
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


async def _checked_text(
    body: ReadRequest, settings: Annotated[Settings, Depends(_settings)]
) -> str:
    """The typed meal, once it is known to be neither blank nor longer than the limit."""
    problems = meal_text_problems(body.text, settings)
    if problems:
        raise _invalid(problems)
    return body.text


async def _cleanup() -> AsyncIterator[AsyncExitStack]:
    """An exit stack that unwinds once the response is over, however it ended."""
    async with AsyncExitStack() as stack:
        yield stack


async def _pump(
    run: AsyncIterator[WeekEvent], events: asyncio.Queue[WeekEvent | Exception | None]
) -> None:
    """Move the run's events into a queue, then None, or the exception that ended the run."""
    try:
        async for event in run:
            events.put_nowait(event)
        events.put_nowait(None)
    except Exception as exc:
        events.put_nowait(exc)


async def _stop(pump: asyncio.Task[None]) -> None:
    """End the run if it is still going, and wait until its calls are cancelled."""
    pump.cancel()
    await asyncio.wait({pump})


async def _stream(
    step: str, run: AsyncIterator[WeekEvent], settings: Settings, cleanup: AsyncExitStack
) -> AsyncIterator[ServerSentEvent]:
    """The run's events as server-sent events, always ending with `week_done` or `error`.

    Status 200 is sent before the first event, so a run that raises can only say so in the
    stream. The event holds no text from the exception, which can quote the request, and the log
    line holds only its class.

    The run goes on in a task of its own, and the response ending stops that task through
    `cleanup`. A client that goes away cancels the response, and the cancellation reaches this
    generator, if it reaches it at all, while it waits for the next event or is parked at a
    yield. Cancelling LangGraph from there does not work: the cancellation is repeated at every
    await, which interrupts LangGraph's own cleanup and leaves its model calls running.
    """
    events: asyncio.Queue[WeekEvent | Exception | None] = asyncio.Queue()
    pump = asyncio.create_task(_pump(run, events))
    cleanup.push_async_callback(_stop, pump)
    started = time.perf_counter()
    try:
        while (item := await events.get()) is not None:
            if isinstance(item, Exception):
                raise item
            yield ServerSentEvent(raw_data=item.model_dump_json(), event=item.kind)
            if isinstance(item, WeekDone):
                week = item.week
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
        tracer=services.tracer,
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
        tracer=services.tracer,
    )
    async for message in _stream("replace", run, settings, cleanup):
        yield message


@router.post(READ_PATH, response_model=ReadResult)
async def read_typed_meal(
    text: Annotated[str, Depends(_checked_text)],
    services: Annotated[Services, Depends(_services)],
    settings: Annotated[Settings, Depends(_settings)],
) -> ReadResult:
    """What a person ate, typed in their own words, as nutrition-mcp foods and numbers."""
    try:
        return await read_meal(
            text,
            model=services.model,
            nutrition=services.nutrition,
            catalogue=services.catalogue,
            settings=settings,
            tracer=services.tracer,
        )
    except ReadError as exc:
        # The message names the step and holds nothing that was typed.
        logger.error("read_failed", extra={"task": "read", "error_type": type(exc).__name__})
        raise HTTPException(status_code=503 if exc.unavailable else 502, detail=str(exc)) from exc


@router.post(SUMMARY_PATH, response_model=WeekSummary)
async def summarise_log(
    body: SummaryRequest,
    services: Annotated[Services, Depends(_services)],
    settings: Annotated[Settings, Depends(_settings)],
) -> WeekSummary:
    """What was eaten, day by day, added up by nutrition-mcp and held to the plan's targets."""
    try:
        return await summarise(
            body.rules,
            body.log,
            nutrition=services.nutrition,
            catalogue=services.catalogue,
            settings=settings,
        )
    except LogRejected as exc:
        # The problems are located from the log, which sits at body.log. They name the entry by
        # its date and slot, and the message is nutrition-mcp's own for a food it refused.
        problems: list[Problem] = [
            {"loc": ["body", "log", *problem["loc"]], "msg": problem["msg"]}
            for problem in exc.problems
        ]
        raise _invalid(problems) from exc
    except SummaryError as exc:
        # The message names the step and holds nothing from the log.
        logger.error("summary_failed", extra={"task": "summary", "error_type": type(exc).__name__})
        raise HTTPException(status_code=503 if exc.unavailable else 502, detail=str(exc)) from exc


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


def app_factory() -> FastAPI:
    """The app with settings from the environment, for `uvicorn --factory`."""
    return create_app(get_settings())


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
