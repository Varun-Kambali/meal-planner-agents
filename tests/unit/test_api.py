"""The HTTP API, driven through Starlette's test client with a scripted model and fake services."""

import asyncio
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml
from fakes import FakeNutrition
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nutrition_mcp.schemas import MealItemIn, MealResult, SearchResult
from pydantic import BaseModel
from week_helpers import (
    PLAN,
    SLOTS,
    SlotModel,
    SpyNutrition,
    good,
    model_for,
    raw,
    recipe_of,
    write_catalogue_file,
)

from meal_planner.api import app_factory, create_app
from meal_planner.config import Settings, get_settings
from meal_planner.llm import ModelPort, ModelReply
from meal_planner.log import get_logger
from meal_planner.nutrition import NutritionError, NutritionPort
from meal_planner.startup import StartupError
from meal_planner.week import WeekResult

# The fixture plan excludes brinjal, and the app refuses a plan that names a food it lacks.
EXTRA_FOODS = ("brinjal",)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [n for n in os.environ if n.startswith("MEAL_PLANNER_")]:
        monkeypatch.delenv(name)


def settings_for(folder: Path, **changes: Any) -> Settings:
    # The limit is high so that a test that is not about it does not trip over it.
    values: dict[str, Any] = {
        "catalogue_path": write_catalogue_file(folder, *EXTRA_FOODS),
        "rate_limit_per_minute": 1000,
    }
    return Settings(**(values | changes))


def make_app(
    folder: Path,
    *,
    model: ModelPort | None = None,
    nutrition: NutritionPort | None = None,
    **changes: Any,
) -> FastAPI:
    return create_app(
        settings_for(folder, **changes),
        model=model or model_for(),
        nutrition=nutrition or SpyNutrition(*EXTRA_FOODS),
    )


@contextmanager
def serving(folder: Path, **kwargs: Any) -> Iterator[TestClient]:
    with TestClient(make_app(folder, **kwargs)) as client:
        yield client


# The simple routes


def test_healthz_says_ok(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_app_factory_reads_the_settings_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEAL_PLANNER_RATE_LIMIT_PER_MINUTE", "7")
    monkeypatch.setenv("MEAL_PLANNER_CATALOGUE_PATH", str(tmp_path / "missing.yaml"))
    get_settings.cache_clear()

    app = app_factory()

    assert app.state.settings.rate_limit_per_minute == 7
    assert app.state.settings.catalogue_path == tmp_path / "missing.yaml"
    get_settings.cache_clear()


def test_every_response_carries_a_request_id(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        ids = {client.get("/healthz").headers["x-request-id"] for _ in range(3)}

    assert len(ids) == 3


def test_the_app_does_not_start_when_the_catalogue_cannot_be_loaded(tmp_path: Path) -> None:
    app = create_app(
        Settings(catalogue_path=tmp_path / "missing.yaml"),
        model=model_for(),
        nutrition=SpyNutrition(*EXTRA_FOODS),
    )

    with pytest.raises(StartupError, match="catalogue"), TestClient(app):
        pass


# The catalogue route


def mixed_app(folder: Path) -> FastAPI:
    rows = [
        ("moong_dal", "pulse", [], "vegan"),
        ("curd", "dairy", ["milk"], "veg"),
        ("wheat_flour", "grain", ["gluten"], "vegan"),
        ("egg", "egg", ["egg"], "egg"),
        ("rohu", "fish", ["fish"], "non_veg"),
        ("peanuts", "nut_seed", ["peanut"], "vegan"),
    ]
    entries = [
        {
            "id": food_id,
            "name": food_id.replace("_", " ").capitalize(),
            "category": category,
            "allergens": allergens,
            "pack_g": None,
            "keeps_days_opened": None,
        }
        for food_id, category, allergens, _ in rows
    ]
    path = folder / "mixed.yaml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    nutrition = FakeNutrition(
        {food_id: {"kcal": 100.0} for food_id, *_ in rows},
        diets={food_id: diet for food_id, _, _, diet in rows},  # type: ignore[misc]
    )
    return create_app(
        Settings(catalogue_path=path, rate_limit_per_minute=1000),
        model=model_for(),
        nutrition=nutrition,
    )


def test_catalogue_filtered_for_vegan(tmp_path: Path) -> None:
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get("/v1/catalogue?diet=vegan")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "moong_dal", "name": "Moong dal", "category": "pulse"},
        {"id": "wheat_flour", "name": "Wheat flour", "category": "grain"},
        {"id": "peanuts", "name": "Peanuts", "category": "nut_seed"},
    ]


def test_catalogue_leaves_out_every_allergen_named(tmp_path: Path) -> None:
    query = "diet=vegan&exclude_allergens=gluten&exclude_allergens=peanut"
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get(f"/v1/catalogue?{query}")

    assert [row["id"] for row in response.json()] == ["moong_dal"]


def test_catalogue_without_a_filter_lists_everything_in_catalogue_order(tmp_path: Path) -> None:
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get("/v1/catalogue")

    ids = [row["id"] for row in response.json()]
    assert ids == ["moong_dal", "curd", "wheat_flour", "egg", "rohu", "peanuts"]


def test_catalogue_rows_hold_only_id_name_and_category(tmp_path: Path) -> None:
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get("/v1/catalogue?diet=egg")

    assert all(set(row) == {"id", "name", "category"} for row in response.json())


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("diet=pescatarian", "diet"),
        ("diet=vegan&exclude_allergens=kryptonite", "exclude_allergens"),
        ("exclude_allergens=milk&exclude_allergens=kryptonite", "exclude_allergens"),
    ],
)
def test_catalogue_with_an_unknown_diet_or_allergen_is_422(
    tmp_path: Path, query: str, field: str
) -> None:
    with TestClient(mixed_app(tmp_path)) as client:
        response = client.get(f"/v1/catalogue?{query}")

    assert response.status_code == 422
    (problem, *_) = response.json()["detail"]
    assert field in problem["loc"]
    assert set(problem) == {"loc", "msg"}


# CORS


def test_no_origins_means_no_cors_headers(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.get("/healthz", headers={"Origin": "http://localhost:5173"})

    assert "access-control-allow-origin" not in response.headers


def test_a_listed_origin_is_allowed_and_can_read_the_request_id(tmp_path: Path) -> None:
    with serving(tmp_path, cors_origins=["http://localhost:5173"]) as client:
        response = client.get("/healthz", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-request-id" in exposed
    assert "retry-after" in exposed


def test_an_unlisted_origin_gets_no_allow_header(tmp_path: Path) -> None:
    with serving(tmp_path, cors_origins=["http://localhost:5173"]) as client:
        response = client.get("/healthz", headers={"Origin": "https://elsewhere.example"})

    assert "access-control-allow-origin" not in response.headers


def test_a_preflight_for_the_week_route_is_answered(tmp_path: Path) -> None:
    with serving(tmp_path, cors_origins=["http://localhost:5173"]) as client:
        response = client.options(
            "/v1/week",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "POST" in response.headers["access-control-allow-methods"]


# Writing a week

MARK = "ZXQ-MARKER-7731"


def plan_json() -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(PLAN.read_text(encoding="utf-8"))
    return plan


def parse_stream(text: str) -> list[tuple[str, dict[str, Any]]]:
    """The messages of an event stream as (event name, data), each data a complete JSON object."""
    found: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        assert set(fields) == {"event", "data"}, block[:200]
        data = json.loads(fields["data"])
        assert isinstance(data, dict)
        found.append((fields["event"], data))
    return found


def week_of(events: list[tuple[str, dict[str, Any]]]) -> WeekResult:
    name, data = events[-1]
    assert name == "week_done"
    return WeekResult.model_validate(data["week"])


def test_week_streams_events_and_done(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan_json())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_stream(response.text)
    kinds = [name for name, _ in events]
    assert kinds[0] == "slot_started"
    assert kinds[-1] == "week_done"
    assert kinds.count("slot_started") == 4
    assert kinds.count("slot_written") == 4
    assert kinds.count("recipe_checked") == 28
    assert kinds.count("week_done") == 1
    assert all(data["kind"] == name for name, data in events)
    week = week_of(events)
    assert len(week.recipes) == 28
    assert week.unwritten == []
    # A tuple-keyed dict cannot be JSON, so the warnings travel as rows.
    assert isinstance(events[-1][1]["week"]["no_waste"], list)


def test_stream_ends_cleanly_after_rate_limited_slot(tmp_path: Path) -> None:
    model = model_for(errors={"lunch": "rate_limited"})

    with serving(tmp_path, model=model) as client:
        response = client.post("/v1/week", json=plan_json())

    assert response.status_code == 200
    events = parse_stream(response.text)
    names = [name for name, _ in events]
    assert names[-1] == "week_done"
    assert "slot_failed" in names
    assert "error" not in names
    week = week_of(events)
    assert len(week.recipes) == 21
    assert [(slot, reason) for _, slot, reason in week.unwritten] == [("lunch", "rate_limited")] * 7
    assert len({day for day, _, _ in week.unwritten}) == 7


def test_invalid_rules_422_names_field(tmp_path: Path) -> None:
    plan = plan_json()
    plan["slots"][1]["kcal_share"] = 7

    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan)

    assert response.status_code == 422
    problems = response.json()["detail"]
    assert any(p["loc"] == ["body", "slots", 1, "kcal_share"] for p in problems)
    assert all(set(p) == {"loc", "msg"} for p in problems)
    assert "input" not in response.text
    assert "url" not in response.text


def test_a_rejected_value_is_never_echoed(tmp_path: Path) -> None:
    plan = plan_json()
    plan["slots"][0]["max_active_min"] = f"about {MARK}"
    plan["extra_field"] = MARK

    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan)

    assert response.status_code == 422
    assert MARK not in response.text


def test_a_body_that_is_not_json_is_422_without_an_echo(tmp_path: Path) -> None:
    with serving(tmp_path) as client:
        response = client.post(
            "/v1/week", content=f"{{{MARK}".encode(), headers={"content-type": "application/json"}
        )

    assert response.status_code == 422
    assert MARK not in response.text
    assert set(response.json()["detail"][0]) == {"loc", "msg"}


def test_a_food_the_catalogue_lacks_is_422_naming_the_field(tmp_path: Path) -> None:
    plan = plan_json()
    plan["food"]["liked_foods"] = ["paneer", "unobtainium"]

    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "food", "liked_foods"]
    assert "unobtainium" in problem["msg"]


class UnreachableNutrition(SpyNutrition):
    """nutrition-mcp gone: every call fails, and the error names no food."""

    async def search(
        self, query: str, kind: Literal["dish", "ingredient"] | None = None, limit: int = 5
    ) -> SearchResult:
        raise NutritionError("nutrition-mcp could not answer search_food: Connection closed")


def test_a_week_is_a_503_naming_the_step_when_nutrition_mcp_cannot_be_reached(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    model = model_for()
    nutrition = UnreachableNutrition(*EXTRA_FOODS)

    with serving(tmp_path, model=model, nutrition=nutrition) as client:
        response = client.post("/v1/week", json=plan_json())

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert "nutrition" in response.json()["detail"]
    assert model.calls == []
    entries = log_entries(capfd.readouterr().err)
    (failed,) = [e for e in entries if e["event"] == "nutrition_unreachable"]
    assert failed["error_type"] == "NutritionError"
    assert failed["request_id"] == response.headers["x-request-id"]


def test_rules_that_are_invalid_are_a_422_even_when_nutrition_mcp_is_down(tmp_path: Path) -> None:
    plan = plan_json()
    plan["slots"][1]["kcal_share"] = 7

    with serving(tmp_path, nutrition=UnreachableNutrition(*EXTRA_FOODS)) as client:
        response = client.post("/v1/week", json=plan)

    assert response.status_code == 422


def test_body_over_64kb_413(tmp_path: Path) -> None:
    body = json.dumps(plan_json()).encode() + b" " * 65536

    with serving(tmp_path) as client:
        response = client.post(
            "/v1/week", content=body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 413
    assert "65536" in response.json()["detail"]


def test_rate_limit_429(tmp_path: Path) -> None:
    with serving(tmp_path, rate_limit_per_minute=2) as client:
        first = client.get("/v1/catalogue")
        second = client.get("/v1/catalogue")
        third = client.post("/v1/week", json=plan_json())
        health = client.get("/healthz")

    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert 1 <= int(third.headers["retry-after"]) <= 60
    assert health.status_code == 200


class ExplodingModel:
    """A model that fails with text the user must never see in a log."""

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        raise RuntimeError(f"provider echoed the request: {MARK}")


def log_entries(captured: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


def test_a_failure_after_the_stream_starts_ends_with_one_error_event(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with serving(tmp_path, model=ExplodingModel()) as client:
        response = client.post("/v1/week", json=plan_json())

    assert response.status_code == 200
    events = parse_stream(response.text)
    name, data = events[-1]
    assert name == "error"
    assert data == {"kind": "error", "step": "week", "message": data["message"]}
    assert MARK not in data["message"]
    assert [n for n, _ in events].count("error") == 1
    assert "week_done" not in [n for n, _ in events]
    captured = capfd.readouterr()
    assert MARK not in captured.err + captured.out
    (failed,) = [e for e in log_entries(captured.err) if e["event"] == "run_failed"]
    assert failed["error_type"] == "RuntimeError"
    assert failed["task"] == "week"


def test_log_lines_written_while_the_stream_runs_carry_its_request_id(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with serving(tmp_path) as client:
        response = client.post("/v1/week", json=plan_json())

    request_id = response.headers["x-request-id"]
    entries = log_entries(capfd.readouterr().err)
    (done,) = [e for e in entries if e["event"] == "run_done"]
    assert done["request_id"] == request_id
    assert done["task"] == "week"
    assert done["input_tokens"] == 0


class LoggingModel:
    """A model that writes a log line after each call, as the real client does."""

    def __init__(self, inner: ModelPort) -> None:
        self._inner = inner

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        reply = await self._inner.structured(
            task=task, system=system, user=user, schema=schema, timeout_s=timeout_s
        )
        get_logger("meal_planner.test").info("model_call", extra={"task": task})
        return reply


def test_log_lines_written_inside_the_run_carry_the_request_id_too(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with serving(tmp_path, model=LoggingModel(model_for())) as client:
        response = client.post("/v1/week", json=plan_json())

    entries = log_entries(capfd.readouterr().err)
    calls = [e for e in entries if e["event"] == "model_call"]
    assert len(calls) == 4
    assert {e["request_id"] for e in calls} == {response.headers["x-request-id"]}


# Rules and typed text stay out of the logs


def marked_plan() -> dict[str, Any]:
    plan = plan_json()
    plan["name"] = f"Plan {MARK}"
    plan["slots"][0] = plan["slots"][0] | {"style": f"style {MARK}", "name": f"Breakfast {MARK}"}
    plan["food"]["cuisines"] = [f"South {MARK}", f"Japan {MARK}", f"Med {MARK}"]
    return plan


def renamed_replies() -> SlotModel:
    names = {
        "South Indian": f"South {MARK}",
        "Japanese": f"Japan {MARK}",
        "Mediterranean": f"Med {MARK}",
    }

    def renamed(text: str) -> str:
        for old, new in names.items():
            text = text.replace(old, new)
        return text

    return SlotModel({s: {"write_slot": [renamed(good(s))]} for s in SLOTS})


class Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    """Every logging record, including those of our own loggers, which do not propagate."""
    collector = Collector()
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


def assert_no_marker(records: list[logging.LogRecord], captured: Any) -> None:
    """`captured` is what capfd.readouterr() returned: the process's own stdout and stderr."""
    assert MARK not in captured.err
    assert MARK not in captured.out
    for record in records:
        assert MARK not in record.getMessage()
        assert MARK not in json.dumps(vars(record), default=str), record.name


def test_no_rules_text_in_logs(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    with serving(tmp_path, model=renamed_replies()) as client:
        response = client.post("/v1/week", json=marked_plan())

    assert MARK in response.text  # the plan's own words do come back to the person who sent them
    assert response.status_code == 200
    assert parse_stream(response.text)[-1][0] == "week_done"
    assert records
    assert_no_marker(records, capfd.readouterr())


def test_no_rules_text_in_logs_when_the_rules_are_rejected(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    plan = marked_plan()
    plan["food"]["liked_foods"] = [f"liked_{MARK}"]
    plan["slots"][1]["max_active_min"] = f"soon {MARK}"

    with serving(tmp_path) as client:
        bad_values = client.post("/v1/week", json=plan)
        not_json = client.post(
            "/v1/week", content=MARK.encode(), headers={"content-type": "application/json"}
        )

    assert bad_values.status_code == 422
    assert not_json.status_code == 422
    assert_no_marker(records, capfd.readouterr())


def test_no_rules_text_in_logs_when_the_run_fails(
    tmp_path: Path,
    records: list[logging.LogRecord],
    capfd: pytest.CaptureFixture[str],
) -> None:
    with serving(tmp_path, model=ExplodingModel()) as client:
        client.post("/v1/week", json=marked_plan())

    assert_no_marker(records, capfd.readouterr())


# A client that goes away


@pytest.mark.anyio
async def test_a_client_that_goes_away_stops_the_run(tmp_path: Path) -> None:
    """The ASGI app is driven by hand: the test client cannot disconnect part way through."""
    release = asyncio.Event()
    model = model_for(gates={slot: release for slot in SLOTS})
    app = make_app(tmp_path, model=model)
    body = json.dumps(plan_json()).encode()
    disconnect = asyncio.Event()
    pending = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if pending:
            return pending.pop(0)
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/week",
        "raw_path": b"/v1/week",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("203.0.113.9", 4000),
        "server": ("testserver", 80),
    }

    async with app.router.lifespan_context(app), asyncio.timeout(10):
        run = asyncio.create_task(app(scope, receive, send))  # type: ignore[arg-type]
        while model.in_flight < len(SLOTS):
            await asyncio.sleep(0)
        disconnect.set()
        await run
        # The calls are cancelled with the run, each at its next step in the loop.
        while model.in_flight:
            await asyncio.sleep(0)
        release.set()
        await asyncio.sleep(0)

    assert len(model.calls) == len(SLOTS)
    assert not any(b"week_done" in m.get("body", b"") for m in sent)


# Writing one recipe again


def a_week(folder: Path) -> WeekResult:
    with serving(folder) as client:
        response = client.post("/v1/week", json=plan_json())
    return week_of(parse_stream(response.text))


def replace_body(week: WeekResult, day: str = "wed", slot_id: str = "lunch") -> dict[str, Any]:
    """What the browser sends: the plan, each recipe as the model wrote it, and which to redo."""
    return {
        "rules": plan_json(),
        "recipes": [
            {
                "day": r.spec.day,
                "slot_id": r.spec.slot.id,
                "draft": r.draft.model_dump(mode="json"),
                "attempts": r.attempts,
            }
            for r in week.recipes
        ],
        "day": day,
        "slot_id": slot_id,
    }


def lunch_again(title: str = "Rajma rice, the other way") -> SlotModel:
    recipe = recipe_of("lunch", "wed") | {"title": title}
    return SlotModel({"lunch": {"write_slot": [raw({"slot_id": "lunch", "recipes": [recipe]})]}})


def test_replace_streams_one_new_recipe_and_keeps_the_rest(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    model = lunch_again()

    with serving(tmp_path, model=model) as client:
        response = client.post("/v1/week/replace", json=replace_body(week))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_stream(response.text)
    names = [name for name, _ in events]
    assert names[0] == "slot_started"
    assert names[-1] == "week_done"
    assert names.count("slot_started") == 1
    assert events[0][1]["slot_id"] == "lunch"
    assert events[0][1]["days"] == ["wed"]
    new = week_of(events)
    assert new.calls == 1
    before = {(r.spec.day, r.spec.slot.id): r for r in week.recipes}
    after = {(r.spec.day, r.spec.slot.id): r for r in new.recipes}
    assert after[("wed", "lunch")].draft.title == "Rajma rice, the other way"
    assert all(after[k] == before[k] for k in before if k != ("wed", "lunch"))


def test_replace_works_the_numbers_out_again_and_takes_none_from_the_client(
    tmp_path: Path,
) -> None:
    week = a_week(tmp_path)
    nutrition = SpyNutrition(*EXTRA_FOODS)
    body = replace_body(week)

    with serving(tmp_path, model=lunch_again(), nutrition=nutrition) as client:
        client.post("/v1/week/replace", json=body)

    # One meal per recipe sent, and one for the recipe that was written.
    assert len(nutrition.meals) == 28 + 1


@pytest.mark.parametrize("field", ["nutrition", "failures", "spec"])
def test_replace_refuses_a_recipe_that_brings_its_own_numbers(tmp_path: Path, field: str) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"][0][field] = {"kcal": 1}

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    assert any(p["loc"][:3] == ["body", "recipes", 0] for p in response.json()["detail"])


def test_replace_for_a_slot_the_plan_lacks_is_422_naming_it(tmp_path: Path) -> None:
    week = a_week(tmp_path)

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=replace_body(week, slot_id="brunch"))

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "slot_id"]
    assert "brunch" in problem["msg"]


def test_replace_with_a_recipe_for_a_slot_the_plan_lacks_is_422_naming_it(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"][3]["slot_id"] = "brunch"

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "recipes", 3, "slot_id"]
    assert "brunch" in problem["msg"]
    assert body["recipes"][3]["day"] in problem["msg"]


def test_replace_with_the_same_day_and_slot_twice_is_422_naming_it(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"].append(body["recipes"][5])

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "recipes", 28]
    assert body["recipes"][5]["slot_id"] in problem["msg"]
    assert body["recipes"][5]["day"] in problem["msg"]


def test_replace_with_a_draft_for_another_day_than_its_entry_is_422(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["recipes"][0]["draft"]["day"] = "sun"

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "recipes", 0, "draft", "day"]


def test_replace_with_an_unknown_food_in_the_rules_is_422(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = replace_body(week)
    body["rules"]["food"]["exclude_foods"] = ["unobtainium"]

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post("/v1/week/replace", json=body)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "rules", "food", "exclude_foods"]


class DownNutrition(SpyNutrition):
    """nutrition-mcp when it cannot be asked at all: the error names no food."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        raise NutritionError("nutrition-mcp could not answer compute_meal: Connection closed")


def test_replace_is_a_503_naming_the_step_when_the_numbers_cannot_be_worked_out(
    tmp_path: Path,
) -> None:
    week = a_week(tmp_path)
    model = lunch_again()

    with serving(tmp_path, model=model, nutrition=DownNutrition(*EXTRA_FOODS)) as client:
        response = client.post("/v1/week/replace", json=replace_body(week))

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert "recompute" in response.json()["detail"]
    assert model.calls == []


class RefusingNutrition(SpyNutrition):
    """nutrition-mcp refusing one food, as it does for a recipe it cannot work out."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        if any(item.food_id == "tofu" for item in items):
            raise NutritionError("item 1 (tofu): refused", "tofu")
        return await super().meal(items)


def test_replace_treats_a_recipe_nutrition_mcp_refuses_as_one_without_numbers(
    tmp_path: Path,
) -> None:
    week = a_week(tmp_path)
    with_tofu = {
        (r.spec.day, r.spec.slot.id)
        for r in week.recipes
        if any(i.id == "tofu" for i in r.draft.ingredients)
    }
    assert with_tofu

    with serving(
        tmp_path, model=lunch_again(), nutrition=RefusingNutrition(*EXTRA_FOODS)
    ) as client:
        response = client.post("/v1/week/replace", json=replace_body(week))

    assert response.status_code == 200
    new = week_of(parse_stream(response.text))
    without_numbers = {(r.spec.day, r.spec.slot.id) for r in new.recipes if r.nutrition is None}
    assert without_numbers == with_tofu - {("wed", "lunch")}


def test_replace_has_its_own_larger_body_limit(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = json.dumps(replace_body(week)).encode()
    padded = body + b" " * (65536 - len(body) + 100)
    plan = json.dumps(plan_json()).encode()
    json_header = {"content-type": "application/json"}

    with serving(tmp_path, model=lunch_again()) as client:
        week_too_big = client.post("/v1/week", content=plan + b" " * 65536, headers=json_header)
        replace_ok = client.post("/v1/week/replace", content=padded, headers=json_header)
        replace_too_big = client.post(
            "/v1/week/replace", content=body + b" " * 131072, headers=json_header
        )

    assert len(padded) > 65536
    assert week_too_big.status_code == 413
    assert replace_ok.status_code == 200
    assert replace_too_big.status_code == 413
    assert "131072" in replace_too_big.json()["detail"]


def test_replace_cuts_off_a_chunked_body_at_its_limit(tmp_path: Path) -> None:
    week = a_week(tmp_path)
    body = json.dumps(replace_body(week)).encode()
    parts = [body[i : i + 10000] for i in range(0, len(body), 10000)]
    parts += [b" " * 10000] * 14

    with serving(tmp_path, model=lunch_again()) as client:
        response = client.post(
            "/v1/week/replace", content=iter(parts), headers={"content-type": "application/json"}
        )

    assert sum(len(part) for part in parts) > 131072
    assert response.status_code == 413


def test_replace_ends_with_an_error_event_when_the_run_fails(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    week = a_week(tmp_path)
    capfd.readouterr()

    with serving(tmp_path, model=ExplodingModel()) as client:
        response = client.post("/v1/week/replace", json=replace_body(week))

    events = parse_stream(response.text)
    name, data = events[-1]
    assert name == "error"
    assert data["step"] == "replace"
    assert MARK not in response.text
    captured = capfd.readouterr()
    assert MARK not in captured.err + captured.out
    (failed,) = [e for e in log_entries(captured.err) if e["event"] == "run_failed"]
    assert failed["task"] == "replace"
