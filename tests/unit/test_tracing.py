"""Tracing to Langfuse: off without keys, masked always, and never the reason a request fails.

Every tracer here sends to an in-memory exporter (tracing_helpers), so no test reaches Langfuse.
"""

import contextvars
import json
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel, field_serializer
from tracing_helpers import Sent, sending, tracing_settings
from week_helpers import canned, plan_rules

from meal_planner import tracing
from meal_planner.config import Settings
from meal_planner.llm import FakeModel, ModelReply
from meal_planner.recipes import SlotDraft
from meal_planner.tracing import MASKED, TracedModel, mask, setup_tracing

pytestmark = pytest.mark.anyio

MARK = "ZXQ-TRACE-4417"


@pytest.fixture
def sent() -> Iterator[Sent]:
    with sending() as found:
        yield found


class Reply(BaseModel):
    words: str
    count: int


class StubModel:
    """A model whose every call gets the same reply, with tokens, cost and latency."""

    def __init__(self, reply: ModelReply[Any]) -> None:
        self.reply = reply

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        return self.reply


class Raising:
    """A model that raises, as a broken stand-in or a bug in a library might."""

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        raise LookupError(f"echoed the prompt: {MARK}")


def costed_reply() -> ModelReply[Reply]:
    return ModelReply(
        value=Reply(words=f"said {MARK}", count=3),
        raw=json.dumps({"words": f"said {MARK}", "count": 3}),
        input_tokens=120,
        output_tokens=45,
        cost_usd=0.00021,
        latency_ms=812.5,
        model="gemini/gemini-3.1-flash-lite",
    )


# Off without keys


def no_client(**_: object) -> None:
    raise AssertionError("a Langfuse client was created without keys")


@pytest.mark.parametrize(
    "keys",
    [
        {},
        {"langfuse_public_key": "pk-lf-1"},
        {"langfuse_secret_key": "sk-lf-1"},
        {"langfuse_public_key": "", "langfuse_secret_key": ""},
        {"langfuse_public_key": "pk-lf-1", "langfuse_secret_key": "  "},
        {"langfuse_public_key": " ", "langfuse_secret_key": "sk-lf-1"},
    ],
    ids=["none", "public_only", "secret_only", "empty", "blank_secret", "blank_public"],
)
def test_tracing_off_without_keys(monkeypatch: pytest.MonkeyPatch, keys: dict[str, str]) -> None:
    monkeypatch.setattr(tracing, "Langfuse", no_client)

    assert setup_tracing(Settings(**keys)) is None


def test_tracing_keys_come_from_langfuse_variable_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-env")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-env")
    monkeypatch.setenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")

    settings = Settings()

    assert settings.langfuse_public_key == "pk-lf-env"
    assert settings.langfuse_secret_key is not None
    assert settings.langfuse_secret_key.get_secret_value() == "sk-lf-env"
    assert settings.langfuse_base_url == "https://us.cloud.langfuse.com"
    # The secret is not shown when the settings are printed.
    assert "sk-lf-env" not in repr(settings)


async def test_a_traced_call_with_no_trace_open_sends_nothing(sent: Sent) -> None:
    model = TracedModel(StubModel(costed_reply()))

    await model.structured(task="read_meal", system="s", user="u", schema=Reply, timeout_s=1)

    assert sent.observations() == []


def test_a_client_that_cannot_be_built_leaves_tracing_off(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(**_: object) -> None:
        raise ValueError(f"bad configuration {MARK}")

    monkeypatch.setattr(tracing, "Langfuse", broken)

    assert setup_tracing(tracing_settings()) is None
    err = capsys.readouterr().err
    assert '"event": "tracing_off"' in err
    assert '"error_type": "ValueError"' in err
    assert MARK not in err


# The mask


def test_mask_removes_cuisines_and_typed_text() -> None:
    rules = plan_rules()
    typed = "2 rotis and a bowl of rajma, south indian style"

    masked = mask({"rules": rules, "text": typed})

    assert isinstance(masked, dict)
    assert masked["text"] == MASKED
    food = masked["rules"]["food"]
    assert food["cuisines"] == [MASKED] * len(rules.food.cuisines)
    # Strings that look like ids are masked unless their field holds ids.
    assert food["diet"] == MASKED
    assert food["liked_foods"] == [MASKED] * len(rules.food.liked_foods)
    assert masked["rules"]["name"] == MASKED
    slot = masked["rules"]["slots"][0]
    assert (slot["id"], slot["name"], slot["style"]) == ("breakfast", MASKED, MASKED)
    assert slot["eat_at"] == MASKED
    # Numbers, counts and ids stay.
    assert slot["kcal_share"] == rules.slots[0].kcal_share
    assert slot["max_active_min"] == rules.slots[0].max_active_min
    assert masked["rules"]["day_types"][0]["id"] == rules.day_types[0].id
    assert masked["rules"]["day_types"][0]["kcal"] == rules.day_types[0].kcal
    assert masked["rules"]["version"] == 1
    # Every day's type is a string under a day, not under an id field.
    assert set(masked["rules"]["week"].values()) == {MASKED}
    assert typed not in json.dumps(masked)
    for cuisine in rules.food.cuisines:
        assert cuisine not in json.dumps(masked)


def test_mask_keeps_ids_and_numbers_of_a_reply_and_masks_its_words() -> None:
    draft = SlotDraft.model_validate(canned("breakfast"))

    masked = mask(draft)

    assert isinstance(masked, dict)
    assert masked["slot_id"] == "breakfast"
    first, recipe = masked["recipes"][0], draft.recipes[0]
    assert first["day"] == recipe.day
    assert first["ingredients"] == [{"id": i.id, "grams": i.grams} for i in recipe.ingredients]
    assert first["active_min"] == recipe.active_min
    for field in ("title", "cuisine", "cook_when", "store", "swap", "fits"):
        assert first[field] == MASKED
    assert set(first["steps"]) == {MASKED}
    assert set(first["gear"]) == {MASKED}


def test_mask_masks_model_prompts() -> None:
    assert mask({"system": "You write recipes.", "user": "<data>x</data>"}) == {
        "system": MASKED,
        "user": MASKED,
    }


class Strange:
    def __str__(self) -> str:
        return MARK


@pytest.mark.parametrize(
    ("value", "masked"),
    [
        ("plain text", MASKED),
        ("", MASKED),
        (None, None),
        (True, True),
        (0, 0),
        (2.5, 2.5),
        (["a", 1, None], [MASKED, 1, None]),
        (("a", 2), [MASKED, 2]),
        ({"id": "rajma"}, {"id": "rajma"}),
        ({"food_id": "rajma", "name": "Rajma"}, {"food_id": "rajma", "name": MASKED}),
        ({"days": ["mon", "tue"]}, {"days": ["mon", "tue"]}),
        ({"slot_id": ["a", ["b"]]}, {"slot_id": ["a", ["b"]]}),
        # A mapping under an id field is walked on its own fields.
        ({"id": {"name": "x", "id": "y"}}, {"id": {"name": MASKED, "id": "y"}}),
        (
            {"error": "model_error", "error_type": "LookupError"},
            {
                "error": "model_error",
                "error_type": "LookupError",
            },
        ),
        ({"cuisine": "south_indian"}, {"cuisine": MASKED}),
        ({"counts": {"ran": 2}}, {"counts": {"ran": 2}}),
        (Strange(), MASKED),
        ({"id": Strange()}, {"id": MASKED}),
        (b"bytes", MASKED),
    ],
)
def test_mask_edges(value: object, masked: object) -> None:
    assert mask(value) == masked


class Unserialisable(BaseModel):
    count: int

    @field_serializer("count")
    def _refuse(self, count: int) -> int:
        raise ValueError(MARK)


def test_a_model_that_cannot_be_dumped_is_masked_whole() -> None:
    assert mask({"id": "x", "model": Unserialisable(count=1)}) == {"id": "x", "model": MASKED}


def test_mask_twice_is_mask_once() -> None:
    once = mask({"rules": plan_rules(), "draft": SlotDraft.model_validate(canned("lunch"))})

    assert mask(once) == once


def test_mask_ids_are_one_list() -> None:
    assert {"id", "food_id", "slot_id", "day", "days", "status", "error", "error_type"} == set(
        tracing.ID_FIELDS
    )


# What reaches Langfuse


async def test_trace_has_cost_and_latency(sent: Sent) -> None:
    model = TracedModel(StubModel(costed_reply()))

    with tracing.trace(sent.tracer, "read", text=f"two rotis {MARK}", text_length=12):
        reply = await model.structured(
            task="read_meal", system="sys", user=f"user {MARK}", schema=Reply, timeout_s=5
        )

    assert reply == costed_reply()
    (root,) = sent.named("read")
    (call,) = sent.named("read_meal")
    assert call.kind == "generation"
    assert call.parent == root.id and call.trace == root.trace
    assert call.model == "gemini/gemini-3.1-flash-lite"
    assert call.usage == {"input": 120, "output": 45}
    assert call.cost == {"total": 0.00021}
    assert call.metadata["latency_ms"] == 812.5
    assert call.duration_ns > 0
    assert call.input == {"system": MASKED, "user": MASKED}
    assert call.output == {"words": MASKED, "count": 3}
    assert root.input == {"text": MASKED, "text_length": 12}
    assert root.parent is None


async def test_a_reply_with_an_error_is_a_warning_with_its_code(sent: Sent) -> None:
    failed = ModelReply[Reply](
        value=None, raw="", error="rate_limited", error_type="RateLimitError", model="m"
    )
    model = TracedModel(StubModel(failed))

    with tracing.trace(sent.tracer, "read"):
        await model.structured(task="read_meal", system="s", user="u", schema=Reply, timeout_s=5)

    (call,) = sent.named("read_meal")
    assert call.level == "WARNING"
    assert call.metadata["error"] == "rate_limited"
    assert call.metadata["error_type"] == "RateLimitError"
    assert call.output is None
    assert call.cost is None


async def test_a_model_that_raises_is_an_error_with_its_class_only(sent: Sent) -> None:
    model = TracedModel(Raising())

    with pytest.raises(LookupError), tracing.trace(sent.tracer, "read"):
        await model.structured(task="read_meal", system="s", user="u", schema=Reply, timeout_s=5)

    (call,) = sent.named("read_meal")
    (root,) = sent.named("read")
    assert call.level == "ERROR"
    assert call.metadata["error_type"] == "LookupError"
    assert root.level == "ERROR"
    assert root.metadata["error_type"] == "LookupError"
    for found in sent.observations():
        assert MARK not in found.sent


async def test_a_traced_model_answers_as_its_inner_model() -> None:
    inner = FakeModel({"read_meal": ['{"words": "x", "count": 1}']})

    reply = await TracedModel(inner).structured(
        task="read_meal", system="s", user="u", schema=Reply, timeout_s=1
    )

    assert reply.value == Reply(words="x", count=1)
    assert inner.calls == [("read_meal", "s", "u")]


def test_the_mask_hook_masks_what_is_passed_to_the_sdk_directly(sent: Sent) -> None:
    span = sent.tracer.client.start_observation(
        name="direct", input={"text": MARK}, metadata={"note": MARK, "id": "kept"}
    )
    span.update(output=[MARK])
    span.end()

    (found,) = sent.named("direct")
    assert found.input == {"text": MASKED}
    assert found.output == [MASKED]
    assert found.metadata == {"note": MASKED, "id": "kept"}
    assert MARK not in found.sent


def test_spans_of_other_libraries_are_not_sent(sent: Sent) -> None:
    # A library that traces with OpenTelemetry uses the global provider, which is not the
    # tracer's own.
    assert otel_trace.get_tracer_provider() is not sent.tracer.provider
    with otel_trace.get_tracer("other.library").start_as_current_span("global"):
        pass
    # Even on the tracer's own provider, a span from another instrumentation scope is dropped.
    with sent.tracer.provider.get_tracer("litellm").start_as_current_span(f"own {MARK}"):
        pass
    with tracing.trace(sent.tracer, "read"):
        pass

    assert [(o.name, o.scope) for o in sent.observations()] == [("read", "langfuse-sdk")]


def test_child_spans_nest_and_become_current(sent: Sent) -> None:
    with tracing.trace(sent.tracer, "week") as root:
        assert tracing.current() is root
        with root.child("compute", recipes=2) as step:
            assert tracing.current() is step
            step.count("recount", "ran")
            step.count("recount", "ran")
            step.count("recount", "failed")
            step.note(recipes=2)
        assert tracing.current() is root
    assert tracing.current() is tracing.NO_SPAN

    (week,) = sent.named("week")
    (compute,) = sent.named("compute")
    assert compute.parent == week.id
    assert compute.input == {"recipes": 2}
    assert compute.metadata == {"recount": {"ran": 2, "failed": 1}, "recipes": 2}


def test_a_trace_closed_in_another_context_still_ends(sent: Sent) -> None:
    """Leaving the block from another context (a generator closed elsewhere) must not raise."""
    block = tracing.trace(sent.tracer, "read")
    contextvars.copy_context().run(block.__enter__)

    block.__exit__(None, None, None)

    assert tracing.current() is tracing.NO_SPAN
    assert [o.name for o in sent.observations()] == ["read"]


def test_the_no_span_records_nothing() -> None:
    span = tracing.NO_SPAN

    span.note(anything=MARK)
    span.count("recount", "ran")
    with span.child("compute") as child:
        assert child is tracing.NO_SPAN
    span.end()

    assert tracing.current() is tracing.NO_SPAN


def test_tracing_is_off_when_no_tracer_is_given() -> None:
    with tracing.trace(None, "read") as root:
        assert root is tracing.NO_SPAN


# Tracing never fails a request


class BrokenClient:
    """A Langfuse client whose every call raises, with text that must not reach a log."""

    def start_observation(self, **_: object) -> None:
        raise RuntimeError(f"sdk broke on {MARK}")

    def flush(self) -> None:
        raise RuntimeError(f"flush broke on {MARK}")

    def shutdown(self) -> None:
        raise RuntimeError(f"shutdown broke on {MARK}")


async def test_an_sdk_that_raises_does_not_fail_the_call(
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = tracing.Tracer(BrokenClient(), provider=None)  # type: ignore[arg-type]
    model = TracedModel(StubModel(costed_reply()))

    with tracing.trace(broken, "read") as root:
        with root.child("step") as step:
            step.note(x=1)
        reply = await model.structured(task="t", system="s", user="u", schema=Reply, timeout_s=1)
    broken.flush()
    broken.shutdown()

    assert reply == costed_reply()
    err = capsys.readouterr().err
    assert '"event": "tracing_failed"' in err
    assert '"error_type": "RuntimeError"' in err
    assert MARK not in err


class FailingObservation:
    """An observation that starts but whose update and end raise."""

    def start_observation(self, **_: object) -> "FailingObservation":
        return FailingObservation()

    def update(self, **_: object) -> None:
        raise RuntimeError(MARK)

    def end(self, **_: object) -> None:
        raise RuntimeError(MARK)


async def test_an_observation_that_cannot_end_does_not_fail_the_call(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FailingObservation()
    broken = tracing.Tracer(client, provider=None)  # type: ignore[arg-type]

    with tracing.trace(broken, "read") as root, root.child("step"):
        pass

    err = capsys.readouterr().err
    assert err.count('"event": "tracing_failed"') >= 2
    assert MARK not in err


def test_an_exporter_that_fails_does_not_fail_the_flush(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Failing(InMemorySpanExporter):
        def export(self, spans: Any) -> Any:
            raise RuntimeError(MARK)

    tracer = setup_tracing(tracing_settings(), exporter=Failing())
    assert tracer is not None
    with tracing.trace(tracer, "read"):
        pass

    tracer.flush()
    tracer.shutdown()
    tracer.provider.shutdown()
