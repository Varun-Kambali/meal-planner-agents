"""The HTTP API: a stateless FastAPI app over the week graph.

Nothing about the user is kept. The rules arrive with each request, the answer streams back as
server-sent events, and the app writes no log line that holds them or any error text that could.
Invalid input is a 422 and an unreachable nutrition-mcp before a stream starts is a 503; once a
stream has started its status is sent, so a failure after that is a last `error` event.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
from meal_planner.nutrition import NutritionPort
from meal_planner.problems import problems_from
from meal_planner.rules import Allergen, Diet
from meal_planner.startup import Services, start_services

logger = get_logger(__name__)

REPLACE_PATH = "/v1/week/replace"


class CatalogueRow(BaseModel):
    id: str
    name: str
    category: Category


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


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

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/catalogue", response_model=list[CatalogueRow])
    async def catalogue(
        services: Annotated[Services, Depends(_services)],
        diet: Diet = "non_veg",
        exclude_allergens: Annotated[list[Allergen] | None, Query()] = None,
    ) -> list[CatalogueRow]:
        items = services.catalogue.allowed(diet, exclude_allergens or ())
        return [CatalogueRow(id=i.id, name=i.name, category=i.category) for i in items]

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
