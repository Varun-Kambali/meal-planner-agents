"""What the tracing tests share: a tracer that sends to memory, and its spans read back as data.

The tracer is the one `setup_tracing` builds, with an in-memory exporter in place of the one
that would send to Langfuse, so nothing leaves the process. Each tracer gets a public key of its
own, because the SDK keeps one set of resources per key for the life of the process.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from meal_planner.config import Settings
from meal_planner.tracing import Tracer, setup_tracing

_METADATA = "langfuse.observation.metadata."


@dataclass(frozen=True)
class Observation:
    """One exported span, as Langfuse reads it."""

    name: str
    kind: str | None
    id: int
    parent: int | None
    trace: int
    input: Any
    output: Any
    metadata: dict[str, Any]
    model: str | None
    usage: dict[str, Any] | None
    cost: dict[str, Any] | None
    level: str | None
    duration_ns: int
    scope: str | None
    # Everything the exporter was handed for this span, as text, to search for a marker.
    sent: str


def _json(value: Any) -> Any:
    return None if value is None else json.loads(str(value))


def observation(span: ReadableSpan) -> Observation:
    attributes = dict(span.attributes or {})
    assert span.start_time is not None and span.end_time is not None
    return Observation(
        name=span.name,
        kind=_text(attributes.get("langfuse.observation.type")),
        id=span.context.span_id,
        parent=span.parent.span_id if span.parent else None,
        trace=span.context.trace_id,
        input=_json(attributes.get("langfuse.observation.input")),
        output=_json(attributes.get("langfuse.observation.output")),
        metadata={
            key.removeprefix(_METADATA): _json(value)
            for key, value in attributes.items()
            if key.startswith(_METADATA)
        },
        model=_text(attributes.get("langfuse.observation.model.name")),
        usage=_json(attributes.get("langfuse.observation.usage_details")),
        cost=_json(attributes.get("langfuse.observation.cost_details")),
        level=_text(attributes.get("langfuse.observation.level")),
        duration_ns=span.end_time - span.start_time,
        scope=span.instrumentation_scope.name if span.instrumentation_scope else None,
        sent=span.to_json(),
    )


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True)
class Sent:
    """A tracer and the exporter its spans go to."""

    tracer: Tracer
    exporter: InMemorySpanExporter

    def observations(self) -> list[Observation]:
        """Every span sent so far, after the tracer has flushed what it holds."""
        self.tracer.flush()
        return [observation(span) for span in self.exporter.get_finished_spans()]

    def named(self, name: str) -> list[Observation]:
        return [o for o in self.observations() if o.name == name]


def tracing_settings(**changes: Any) -> Settings:
    """Settings with Langfuse keys, a public key that no other tracer in the process has."""
    values: dict[str, Any] = {
        "langfuse_public_key": f"pk-lf-test-{uuid4().hex}",
        "langfuse_secret_key": "sk-lf-test",
    }
    return Settings(**(values | changes))


@contextmanager
def sending() -> Iterator[Sent]:
    """A tracer that sends to memory, shut down at the end of the block."""
    exporter = InMemorySpanExporter()
    tracer = setup_tracing(tracing_settings(), exporter=exporter)
    assert tracer is not None
    try:
        yield Sent(tracer, exporter)
    finally:
        tracer.shutdown()
        tracer.provider.shutdown()
