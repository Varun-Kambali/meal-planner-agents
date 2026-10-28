"""What a week, a replacement and a meal read send to Langfuse, from the graph, the API and the CLI.

Every tracer sends to memory (tracing_helpers). The marker tests put a string that cannot occur by
chance into the plan, the replies and the typed text, and look for it in every span sent.
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from app_helpers import EXTRA_FOODS, MARK, marked_plan, plan_json, renamed_replies, serving
from nutrition_mcp.schemas import MealItemIn, MealResult
from pydantic import BaseModel
from reader_helpers import (
    ReaderNutrition,
    choosing,
    reader_catalogue,
    reading,
    said,
    write_reader_catalogue,
)
from tracing_helpers import Observation, Sent, sending
from week_helpers import (
    SLOTS,
    SlotModel,
    SpyNutrition,
    catalogue,
    changed,
    good,
    model_for,
    plan_rules,
    recipe_of,
    repair_reply,
    scaled,
    without_protein,
    write_catalogue_file,
)

from meal_planner import startup, tracing
from meal_planner.cli import main
from meal_planner.config import Settings
from meal_planner.llm import FakeModel, ModelPort, ModelReply
from meal_planner.nutrition import NutritionError
from meal_planner.reader import ReadError, read_meal
from meal_planner.rules import DAYS
from meal_planner.startup import start_services
from meal_planner.tracing import MASKED, TracedModel, Tracer
from meal_planner.week import replace_recipe, run_week
from meal_planner.week_events import WeekDone, WeekResult

pytestmark = pytest.mark.anyio

STEPS = ("expand", "write_slot", "repair_slot", "compute", "check", "report")


@pytest.fixture
def sent() -> Iterator[Sent]:
    with sending() as found:
        yield found


async def week(
    tracer: Tracer | None,
    model: ModelPort | None = None,
    *,
    nutrition: Any = None,
    **settings: Any,
) -> WeekResult:
    done: WeekResult | None = None
    async for event in run_week(
        plan_rules(),
        model=TracedModel(model or model_for()),
        nutrition=nutrition or SpyNutrition(),
        catalogue=catalogue(),
        settings=Settings(**settings),
        tracer=tracer,
    ):
        if isinstance(event, WeekDone):
            done = event.week
    assert done is not None
    return done


def children(observations: list[Observation], parent: Observation) -> list[Observation]:
    return [o for o in observations if o.parent == parent.id]


# A week


async def test_a_week_is_one_trace_with_a_span_per_step(sent: Sent) -> None:
    result = await week(sent.tracer)

    found = sent.observations()
    (root,) = [o for o in found if o.parent is None]
    assert root.name == "week"
    assert {o.trace for o in found} == {root.trace}
    steps = children(found, root)
    assert sorted(o.name for o in steps) == sorted(
        ["expand", "compute", "check", "report", *["write_slot"] * len(SLOTS)]
    )
    assert all(o.kind == "span" for o in steps)
    # Each slot's writing span holds its one model call.
    for step in (o for o in steps if o.name == "write_slot"):
        (call,) = children(found, step)
        assert (call.name, call.kind) == ("write_slot", "generation")
        assert step.input["slot_id"] in SLOTS
        assert step.input["days"] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        assert step.input["attempt"] == 1
        assert step.metadata["recipes"] == 7
        assert call.input == {"system": MASKED, "user": MASKED}
        assert call.output["slot_id"] == step.input["slot_id"]
    assert root.output["recipes"] == len(result.recipes) == 28
    assert root.output["unwritten"] == 0
    assert root.output["calls"] == 4
    assert root.output["failing_recipes"] == 0
    assert root.output["failed_day_checks"] == {}
    assert root.input["rules"]["slots"][0]["id"] == "breakfast"


async def test_the_check_span_names_the_checks_that_failed(sent: Sent) -> None:
    model = model_for(
        writes={"breakfast": [changed("breakfast", without_protein, "mon")]},
        repairs={"breakfast": [repair_reply("breakfast", "mon")]},
    )

    await week(sent.tracer, model)

    first, second = sent.named("check")
    assert first.metadata["failed_recipe_checks"] == {"slot_protein_g": 1}
    assert second.metadata["failed_recipe_checks"] == {}
    repair_step, repair = sorted(sent.named("repair_slot"), key=lambda o: o.kind != "span")
    assert repair.kind == "generation" and repair.parent == repair_step.id
    assert repair_step.input == {"slot_id": "breakfast", "days": ["mon"], "attempt": 2}
    assert repair_step.metadata["recipes"] == 1
    assert {o.parent for o in sent.named("check")} == {repair_step.parent}
    (root,) = sent.named("week")
    assert root.output["calls"] == 5


async def test_a_week_that_still_fails_says_which_checks_on_its_root(sent: Sent) -> None:
    model = model_for(writes={"breakfast": [changed("breakfast", without_protein, "mon")]})

    await week(sent.tracer, model, max_repair_attempts=0)

    (root,) = sent.named("week")
    assert root.output["failing_recipes"] == 1
    assert root.output["failed_recipe_checks"] == {"slot_protein_g": 1}


async def test_the_compute_span_says_whether_each_recount_ran(
    sent: Sent, capsys: pytest.CaptureFixture[str]
) -> None:
    # Monday's breakfast is 30% over its calories, so its grams are fitted and worked out again.
    model = model_for(writes={"breakfast": [changed("breakfast", lambda r: scaled(r, 1.3), "mon")]})

    await week(sent.tracer, model)

    (compute,) = sent.named("compute")
    assert compute.metadata["recount"] == {"ran": 1, "not_needed": 27}
    assert compute.metadata["numbers"] == {"ok": 28}
    err = capsys.readouterr().err
    (line,) = [json.loads(x) for x in err.splitlines() if '"recipes_numbered"' in x]
    assert line["task"] == "compute"
    assert line["recount"] == {"ran": 1, "not_needed": 27}


class RefusesTheRecount(SpyNutrition):
    """Refuses the second meal it is asked about with Monday breakfast's foods: the recount."""

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        ids = [i.food_id for i in items]
        target = [i["id"] for i in recipe_of("breakfast", "mon")["ingredients"]]
        if ids == target and target in self.meals:
            self.meals.append(ids)
            raise NutritionError("compute_meal could not work out this meal")
        return await super().meal(items)


async def test_a_recount_that_fails_is_counted_and_logged(
    sent: Sent, capsys: pytest.CaptureFixture[str]
) -> None:
    model = model_for(writes={"breakfast": [changed("breakfast", lambda r: scaled(r, 1.3), "mon")]})

    await week(sent.tracer, model, nutrition=RefusesTheRecount())

    first = sent.named("compute")[0]
    assert first.metadata["recount"] == {"failed": 1, "not_needed": 27}
    err = capsys.readouterr().err
    lines = [json.loads(x) for x in err.splitlines() if '"recipes_numbered"' in x]
    assert lines[0]["recount"] == {"failed": 1, "not_needed": 27}


async def test_no_recount_line_when_no_recipe_needed_one(
    sent: Sent, capsys: pytest.CaptureFixture[str]
) -> None:
    await week(sent.tracer)

    (compute,) = sent.named("compute")
    assert compute.metadata["recount"] == {"not_needed": 28}
    assert "recipes_numbered" not in capsys.readouterr().err


async def test_with_fitting_off_the_recount_is_counted_as_off(sent: Sent) -> None:
    await week(sent.tracer, fit_portions=False)

    (compute,) = sent.named("compute")
    assert compute.metadata["recount"] == {"off": 28}


class UnreadableOnce(SpyNutrition):
    """Sends one result that cannot be read, as a schema mismatch would, then answers."""

    def __init__(self) -> None:
        super().__init__()
        self.sent_bad = False

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        if not self.sent_bad:
            self.sent_bad = True
            self.meals.append([i.food_id for i in items])
            error = NutritionError(
                "nutrition-mcp's result for compute_meal could not be read", unreadable=True
            )
            raise error from RuntimeError(f"Invalid structured content {MARK}")
        return await super().meal(items)


async def test_a_result_that_cannot_be_read_is_logged_and_counted(
    sent: Sent, capsys: pytest.CaptureFixture[str]
) -> None:
    await week(sent.tracer, nutrition=UnreadableOnce())

    first = sent.named("compute")[0]
    assert first.metadata["unreadable"] == {"RuntimeError": 1}
    assert first.metadata["numbers"] == {"ok": 27, "nutrition_failed": 1}
    err = capsys.readouterr().err
    (line,) = [json.loads(x) for x in err.splitlines() if '"nutrition_unreadable"' in x]
    assert line["level"] == "ERROR"
    assert line["error_type"] == "RuntimeError"
    assert MARK not in err


class RaisesForLunch:
    """A model whose call for lunch raises, as a bug in a library or a stand-in might."""

    def __init__(self) -> None:
        self.inner = model_for()

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        if 'slot_id: "lunch"' in user:
            raise RuntimeError(f"Vegetarian, no onions {MARK}")
        return await self.inner.structured(
            task=task, system=system, user=user, schema=schema, timeout_s=timeout_s
        )


async def test_a_model_call_that_raises_is_recorded_by_its_class(
    sent: Sent, capsys: pytest.CaptureFixture[str]
) -> None:
    result = await week(sent.tracer, RaisesForLunch())

    assert len(result.unwritten) == 7
    found = sent.observations()
    lunch = next(
        o
        for o in found
        if o.kind == "span" and o.name == "write_slot" and o.input["slot_id"] == "lunch"
    )
    assert lunch.level == "ERROR"
    assert lunch.metadata["error"] == "model_error"
    assert lunch.metadata["error_type"] == "RuntimeError"
    (call,) = children(found, lunch)
    assert (call.level, call.metadata["error_type"]) == ("ERROR", "RuntimeError")
    err = capsys.readouterr().err
    assert '"error_type": "RuntimeError"' in err
    for observation in found:
        assert MARK not in observation.sent
    assert MARK not in err


async def test_a_refused_reply_is_recorded_with_its_code(sent: Sent) -> None:
    model = model_for(errors={"lunch": "rate_limited"})

    await week(sent.tracer, model)

    found = sent.observations()
    lunch = next(
        o
        for o in found
        if o.kind == "span" and o.name == "write_slot" and o.input["slot_id"] == "lunch"
    )
    assert lunch.metadata["error"] == "rate_limited"
    assert lunch.metadata["refused"] is True
    assert lunch.metadata["final"] is True
    (call,) = children(found, lunch)
    assert (call.level, call.metadata["error"]) == ("WARNING", "rate_limited")


async def test_a_replacement_is_a_trace_of_its_own(sent: Sent) -> None:
    previous = await week(None)
    lunch = recipe_of("lunch", "wed") | {"title": "Rajma rice, the other way"}
    reply = json.dumps({"slot_id": "lunch", "recipes": [lunch]})
    model = FakeModel({"write_slot": [reply]})

    async for _ in replace_recipe(
        plan_rules(),
        previous,
        "wed",
        "lunch",
        model=TracedModel(model),
        nutrition=SpyNutrition(),
        catalogue=catalogue(),
        settings=Settings(),
        tracer=sent.tracer,
    ):
        pass

    found = sent.observations()
    (root,) = [o for o in found if o.parent is None]
    assert root.name == "replace"
    assert (root.input["day"], root.input["slot_id"]) == ("wed", "lunch")
    assert root.output["recipes"] == 28
    step, call = sorted(sent.named("write_slot"), key=lambda o: o.kind or "", reverse=True)
    assert (step.kind, step.parent, step.input["days"]) == ("span", root.id, ["wed"])
    assert (call.kind, call.parent) == ("generation", step.id)


async def test_a_week_that_is_cancelled_ends_its_trace(sent: Sent) -> None:
    """The API cancels a run whose client went away; its trace ends with a warning."""
    started = asyncio.Event()
    held = asyncio.Event()
    model = SlotModel({s: {"write_slot": [good(s)]} for s in SLOTS}, gates={"lunch": held})

    async def consume() -> None:
        async for _ in run_week(
            plan_rules(),
            model=TracedModel(model),
            nutrition=SpyNutrition(),
            catalogue=catalogue(),
            settings=Settings(),
            tracer=sent.tracer,
        ):
            started.set()

    task = asyncio.create_task(consume())
    await asyncio.wait(
        {task, asyncio.create_task(started.wait())}, timeout=5, return_when="FIRST_COMPLETED"
    )
    assert started.is_set() and not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    (root,) = sent.named("week")
    assert root.level == "WARNING"
    assert root.metadata["error_type"] == "CancelledError"
    assert root.output is None


async def test_a_week_untraced_and_traced_come_out_the_same(sent: Sent) -> None:
    model = model_for(
        writes={"breakfast": [changed("breakfast", without_protein, "mon")]},
        repairs={"breakfast": [repair_reply("breakfast", "mon")]},
    )
    again = model_for(
        writes={"breakfast": [changed("breakfast", without_protein, "mon")]},
        repairs={"breakfast": [repair_reply("breakfast", "mon")]},
    )

    assert await week(sent.tracer, model) == await week(None, again)


# A read


async def test_a_read_is_one_trace_with_its_model_calls(sent: Sent) -> None:
    text = f"2 rotis and a katori of dal {MARK}"
    model = FakeModel(
        {
            "read_meal": [
                reading(said("roti", 2, "roti", "2"), said("dal", 1, "katori", "a katori"))
            ],
            "choose_food": [choosing(0)],
        }
    )

    result = await read_meal(
        text,
        model=TracedModel(model),
        nutrition=ReaderNutrition(),
        catalogue=reader_catalogue(),
        settings=Settings(),
        tracer=sent.tracer,
    )

    found = sent.observations()
    (root,) = [o for o in found if o.parent is None]
    assert root.name == "read"
    assert root.input == {"text": MASKED, "text_length": len(text)}
    calls = children(found, root)
    assert [(o.name, o.kind) for o in calls] == [
        ("read_meal", "generation"),
        ("choose_food", "generation"),
    ]
    items = root.output["items"]
    assert [(i["name"], i["food_id"], i["status"]) for i in items] == [
        (MASKED, "chapati", "matched"),
        (MASKED, "dal_tadka", "matched"),
    ]
    assert items[0]["grams"] == result.items[0].grams
    assert items[1]["unit"] == MASKED
    # The food's own units (nutrition-mcp's "katori" among them) are data, not what was typed.
    for observation in found:
        assert MARK not in observation.sent
        assert "rotis" not in observation.sent
        assert "a katori" not in observation.sent


async def test_a_read_that_fails_ends_its_trace_with_the_class(sent: Sent) -> None:
    model = FakeModel({"read_meal": ["not json"], "choose_food": []})

    with pytest.raises(ReadError):
        await read_meal(
            "2 rotis",
            model=TracedModel(model),
            nutrition=ReaderNutrition(),
            catalogue=reader_catalogue(),
            settings=Settings(),
            tracer=sent.tracer,
        )

    (root,) = sent.named("read")
    assert root.level == "ERROR"
    assert root.metadata["error_type"] == "ReadError"
    (call,) = sent.named("read_meal")
    assert (call.level, call.metadata["error"]) == ("WARNING", "invalid_json")


async def test_a_read_untraced_is_unchanged() -> None:
    model = FakeModel({"read_meal": [reading(said("roti", 2, "roti", "2"))], "choose_food": []})

    result = await read_meal(
        "2 rotis",
        model=model,
        nutrition=ReaderNutrition(),
        catalogue=reader_catalogue(),
        settings=Settings(),
    )

    assert [i.food_id for i in result.items] == ["chapati"]


# Tracing never fails a request


class BrokenClient:
    def start_observation(self, **_: object) -> None:
        raise RuntimeError(MARK)

    def flush(self) -> None:
        raise RuntimeError(MARK)

    def shutdown(self) -> None:
        raise RuntimeError(MARK)


async def test_a_broken_tracer_does_not_change_a_week_or_a_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = Tracer(BrokenClient(), provider=None)  # type: ignore[arg-type]

    assert await week(broken) == await week(None)
    result = await read_meal(
        "2 rotis",
        model=TracedModel(
            FakeModel({"read_meal": [reading(said("roti", 2, "roti", "2"))], "choose_food": []})
        ),
        nutrition=ReaderNutrition(),
        catalogue=reader_catalogue(),
        settings=Settings(),
        tracer=broken,
    )

    assert result.items[0].status == "matched"
    err = capsys.readouterr().err
    assert '"event": "tracing_failed"' in err
    assert MARK not in err


# Both entry points


@pytest.fixture
def traced_startup(monkeypatch: pytest.MonkeyPatch, sent: Sent) -> Sent:
    """start_services builds this test's tracer, from the keys in the settings it is given."""

    def setup(settings: Settings) -> Tracer | None:
        assert settings.langfuse_public_key == "pk-lf-app"
        return sent.tracer

    monkeypatch.setattr(startup, "setup_tracing", setup)
    return sent


def keyed(**values: Any) -> dict[str, Any]:
    return {"langfuse_public_key": "pk-lf-app", "langfuse_secret_key": "sk-lf-app"} | values


async def test_services_carry_the_tracer_and_trace_the_model(
    traced_startup: Sent, tmp_path: Path
) -> None:
    settings = Settings(**keyed(catalogue_path=write_catalogue_file(tmp_path)))
    model = model_for()

    async with start_services(settings, model=model, nutrition=SpyNutrition()) as services:
        assert services.tracer is traced_startup.tracer
        assert isinstance(services.model, TracedModel)


async def test_services_without_keys_have_no_tracer_and_the_model_as_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tracing, "Langfuse", lambda **_: pytest.fail("a client was made"))
    settings = Settings(catalogue_path=write_catalogue_file(tmp_path))
    model = model_for()

    async with start_services(settings, model=model, nutrition=SpyNutrition()) as services:
        assert services.tracer is None
        assert services.model is model


async def test_services_shut_the_tracer_down_when_they_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stopped: list[str] = []

    class Recorded(Tracer):
        def shutdown(self) -> None:
            stopped.append("shutdown")

    monkeypatch.setattr(startup, "setup_tracing", lambda _: Recorded(None, None))  # type: ignore[arg-type]
    settings = Settings(**keyed(catalogue_path=write_catalogue_file(tmp_path)))

    async with start_services(settings, model=model_for(), nutrition=SpyNutrition()):
        assert stopped == []

    assert stopped == ["shutdown"]


def spans_without_flushing(sent: Sent) -> list[str]:
    """The names of the spans the exporter has, without asking the tracer to flush first."""
    return [span.name for span in sent.exporter.get_finished_spans()]


def test_the_cli_traces_a_read_and_sends_it_before_it_exits(
    traced_startup: Sent, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = FakeModel({"read_meal": [reading(said("roti", 2, "roti", "2"))], "choose_food": []})

    code = main(
        ["read", f"2 rotis {MARK}"],
        settings=Settings(**keyed(catalogue_path=write_reader_catalogue(tmp_path))),
        model=model,
        nutrition=ReaderNutrition(),
    )

    assert code == 0
    assert sorted(spans_without_flushing(traced_startup)) == ["read", "read_meal"]
    assert all(MARK not in o.sent for o in traced_startup.observations())
    assert MARK not in capsys.readouterr().err


def test_the_cli_traces_a_week(
    traced_startup: Sent, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(plan_json()), encoding="utf-8")

    code = main(
        ["week", str(rules), "--out", str(tmp_path / "week.json")],
        settings=Settings(**keyed(catalogue_path=write_catalogue_file(tmp_path, *EXTRA_FOODS))),
        model=model_for(),
        nutrition=SpyNutrition(*EXTRA_FOODS),
    )

    assert code == 0
    names = spans_without_flushing(traced_startup)
    assert names.count("week") == 1
    assert names.count("write_slot") == 2 * len(SLOTS)


def test_the_api_traces_a_week_with_no_plan_text_and_sends_it_on_shutdown(
    traced_startup: Sent, tmp_path: Path
) -> None:
    with serving(tmp_path, model=renamed_replies(), **keyed(rate_limit_per_minute=1000)) as client:
        response = client.post("/v1/week", json=marked_plan())
        assert response.status_code == 200
        assert "week_done" in response.text

    names = spans_without_flushing(traced_startup)
    assert names.count("week") == 1
    for observation in traced_startup.observations():
        assert MARK not in observation.sent, observation.name


def test_the_api_traces_a_read_with_no_typed_text(traced_startup: Sent, tmp_path: Path) -> None:
    model = FakeModel(
        {"read_meal": [reading(said("roti", 2, "roti", "2"), said(MARK, 1, MARK, "1"))]}
    )

    with serving(
        tmp_path,
        model=model,
        nutrition=ReaderNutrition(),
        **keyed(catalogue_path=write_reader_catalogue(tmp_path)),
    ) as client:
        response = client.post("/v1/log/read", json={"text": f"2 rotis and 1 {MARK}"})
        assert response.status_code == 200

    names = spans_without_flushing(traced_startup)
    assert sorted(names) == ["read", "read_meal"]
    for observation in traced_startup.observations():
        assert MARK not in observation.sent


def test_the_api_traces_a_replacement_with_no_plan_text(
    traced_startup: Sent, tmp_path: Path
) -> None:
    lunch = recipe_of("lunch", "wed") | {"title": f"Rajma rice {MARK}"}
    reply = json.dumps({"slot_id": "lunch", "recipes": [lunch]})
    model = FakeModel({"write_slot": [reply]})
    body = replacement_body(marked_plan())

    with serving(tmp_path, model=model, **keyed()) as client:
        response = client.post("/v1/week/replace", json=body)
        assert response.status_code == 200
        assert "week_done" in response.text

    assert spans_without_flushing(traced_startup).count("replace") == 1
    for observation in traced_startup.observations():
        assert MARK not in observation.sent, observation.name


def replacement_body(plan: dict[str, Any]) -> dict[str, Any]:
    """A replacement request for Wednesday's lunch, with the canned week as the rest of it."""
    recipes = [
        {"day": day, "slot_id": slot, "draft": recipe_of(slot, day)}
        for slot in SLOTS
        for day in DAYS
    ]
    return {"rules": plan, "recipes": recipes, "day": "wed", "slot_id": "lunch"}


def test_the_marker_is_not_the_mask() -> None:
    # The marker tests would pass for nothing if the marker were the mask's own text.
    assert MARK not in MASKED
