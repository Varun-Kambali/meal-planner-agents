"""Tracing to Langfuse, with nothing the user wrote or set in it, and off unless both keys are set.

A week, a replacement and a meal read are each one trace. A week's trace has a span for each step
of the graph and a generation for each model call, with the model, its tokens, cost and latency;
the check step counts the checks that failed. A read's trace holds its model calls. The design and
why it is this way: docs/decisions/0010-masked-langfuse-tracing.md.

Masking: every value is passed through `mask` before it is handed to the SDK, and the SDK runs
`mask` again as its mask hook, so a value handed to it some other way is masked too. `mask` keeps
a string only under a field named in ID_FIELDS.

Only spans made here are sent. The SDK is built on OpenTelemetry, and by default it would become
the process's global tracer provider and send other libraries' spans that look like model calls.
Here it gets a provider of its own, which no other library knows of, and a filter that drops any
span the SDK did not make. Every span is made by this code: no LiteLLM callback or LangChain
handler is used, as those would send prompts, replies and the graph's state as they are.

Tracing never fails a request. Each call into the SDK goes through `_quietly`, which logs an
error by its class only and goes on.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Literal

from langfuse import Langfuse
from langfuse.span_filter import is_langfuse_span
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from pydantic import BaseModel

from meal_planner.config import Settings
from meal_planner.llm import ModelPort, ModelReply
from meal_planner.log import get_logger

logger = get_logger(__name__)

MASKED = "[masked]"

# The fields whose strings are kept, because each holds an id or a code this app defines: food ids
# (nutrition-mcp's and the catalogue's), the plan's own slot and day type ids, days of the week, a
# read item's status, and error codes and exception class names. A string anywhere else is masked,
# however much it looks like an id: a cuisine such as "south_indian" would pass for one.
ID_FIELDS = frozenset({"id", "food_id", "slot_id", "day", "days", "status", "error", "error_type"})

Level = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]


def mask(value: object) -> object:
    """`value` with every string replaced by MASKED, except those under a field in ID_FIELDS.

    Numbers, booleans and None are kept. Lists, tuples, sets, mappings and pydantic models are
    walked; a list under an id field keeps its strings, a mapping is judged by its own fields.
    Mapping keys are kept: they are field names, days, nutrients or check names wherever this code
    builds a mapping, and a mapping keyed by text the user wrote must not be passed in. Anything
    else is masked whole. Masking a masked value changes nothing.
    """
    return _masked(value, keep=False)


def _masked(value: object, keep: bool) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if keep else MASKED
    if isinstance(value, BaseModel):
        try:
            value = value.model_dump(mode="json")
        except Exception:
            return MASKED
    if isinstance(value, Mapping):
        return {_key(key): _masked(item, key in ID_FIELDS) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_masked(item, keep) for item in value]
    return MASKED


def _key(key: object) -> str:
    return str(key) if isinstance(key, str | int | float | bool) else MASKED


def _mask_hook(*, data: Any, **_: Any) -> Any:
    """The SDK's mask hook, called with each input, output and metadata before they are set."""
    return mask(data)


def _quietly[T](action: Callable[[], T]) -> T | None:
    """Run one call into the SDK. An error is logged by its class, never its text, and swallowed."""
    try:
        return action()
    except Exception as exc:
        logger.warning("tracing_failed", extra={"error_type": type(exc).__name__})
        return None


class Span:
    """A span of a trace, or nothing at all when tracing is off (NO_SPAN).

    What a step records with `note` and `count` is kept here and sent, masked, as the span's
    metadata when it ends, so steps running at once can add to one span. `set_output` is sent the
    same way.
    """

    def __init__(self, observation: Any) -> None:
        # A Langfuse span or generation; None when tracing is off or the SDK failed to start one.
        self._observation = observation
        self._notes: dict[str, Any] = {}
        self._output: object = None
        self._level: Level | None = None
        # Fields only a generation has: model, usage_details and cost_details.
        self._generation: dict[str, Any] = {}

    def note(self, **fields: object) -> None:
        if self._observation is not None:
            self._notes.update(fields)

    def count(self, field: str, name: str) -> None:
        """Add one to `name` in the counts kept under `field`, such as a check that failed."""
        if self._observation is not None:
            counts = self._notes.setdefault(field, {})
            counts[name] = counts.get(name, 0) + 1

    def set_output(self, value: object) -> None:
        if self._observation is not None:
            self._output = value

    def warn(self) -> None:
        if self._level is None:
            self._level = "WARNING"

    def failed(self, exc: BaseException) -> None:
        """Record that `exc` ended the step: its class only, as its text can hold a prompt.

        The first class recorded is kept, so a span shows the cause and not what it became.
        A cancellation is a warning; any other exception an error.
        """
        if self._observation is None:
            return
        self._notes.setdefault("error_type", type(exc).__name__)
        if isinstance(exc, Exception):
            self._level = "ERROR"
        else:
            self.warn()

    @contextmanager
    def child(self, name: str, **input: object) -> Iterator["Span"]:
        """A span inside this one, which is the current span for the length of the block."""
        span = self._start(name, "span", input)
        token = _current.set(span)
        try:
            yield span
        except BaseException as exc:
            span.failed(exc)
            raise
        finally:
            _reset(token)
            span.end()

    @contextmanager
    def generation(self, task: str, *, system: str, user: str) -> Iterator["Call"]:
        """A model call inside this span. The prompts are sent masked."""
        call = Call(self._start_observation(task, "generation", {"system": system, "user": user}))
        try:
            yield call
        except BaseException as exc:
            call.failed(exc)
            raise
        finally:
            call.end()

    def end(self) -> None:
        """Send what was recorded and end the span. A span ends once; later calls do nothing."""
        observation, self._observation = self._observation, None
        if observation is None:
            return
        _quietly(
            lambda: observation.update(
                output=None if self._output is None else mask(self._output),
                metadata=mask(self._notes) if self._notes else None,
                level=self._level,
                **self._generation,
            )
        )
        _quietly(observation.end)

    def _start(self, name: str, as_type: str, input: Mapping[str, object]) -> "Span":
        observation = self._start_observation(name, as_type, input)
        return NO_SPAN if observation is None else Span(observation)

    def _start_observation(self, name: str, as_type: str, input: Mapping[str, object]) -> Any:
        parent = self._observation
        if parent is None:
            return None
        return _quietly(
            lambda: parent.start_observation(
                name=name, as_type=as_type, input=mask(dict(input)) if input else None
            )
        )


class Call(Span):
    """A generation: one model call, with its model, tokens, cost and latency."""

    def reply(self, reply: ModelReply[Any]) -> None:
        if self._observation is None:
            return
        self._output = reply.value
        self._generation = {
            "model": reply.model or None,
            "usage_details": {"input": reply.input_tokens, "output": reply.output_tokens},
            "cost_details": None if reply.cost_usd is None else {"total": reply.cost_usd},
        }
        self.note(latency_ms=reply.latency_ms)
        if reply.error:
            self.note(error=reply.error)
            self.warn()
        if reply.error_type:
            self.note(error_type=reply.error_type)


# What NO_SPAN starts is NO_SPAN, so code can trace without asking whether tracing is on.
NO_SPAN = Span(None)
_current: ContextVar[Span] = ContextVar("meal_planner_span", default=NO_SPAN)


def current() -> Span:
    """The span a step is running in, or NO_SPAN outside a trace."""
    return _current.get()


def _reset(token: Token[Span]) -> None:
    try:
        _current.reset(token)
    except ValueError:
        # The token was made in another context (a generator closed from elsewhere).
        _current.set(NO_SPAN)


class Tracer:
    """The Langfuse client and the tracer provider that is its own, for the life of the app."""

    def __init__(self, client: Langfuse, provider: TracerProvider) -> None:
        self.client = client
        self.provider = provider

    def start(self, name: str, **input: object) -> Span:
        """The first span of a new trace. It is not made current; end it with `end`."""
        observation = _quietly(
            lambda: self.client.start_observation(
                name=name, as_type="span", input=mask(input) if input else None
            )
        )
        return NO_SPAN if observation is None else Span(observation)

    def flush(self) -> None:
        """Send every span that has ended. It waits for Langfuse, so call it at the end of work."""
        _quietly(self.client.flush)

    def shutdown(self) -> None:
        """Send what is left and stop the client's threads."""
        _quietly(self.client.shutdown)


def start_trace(tracer: Tracer | None, name: str, **input: object) -> Span:
    """A new trace's first span, or NO_SPAN when tracing is off."""
    return NO_SPAN if tracer is None else tracer.start(name, **input)


@contextmanager
def trace(tracer: Tracer | None, name: str, **input: object) -> Iterator[Span]:
    """A new trace whose first span is the current span for the length of the block."""
    root = start_trace(tracer, name, **input)
    token = _current.set(root)
    try:
        yield root
    except BaseException as exc:
        root.failed(exc)
        raise
    finally:
        _reset(token)
        root.end()


def setup_tracing(settings: Settings, *, exporter: SpanExporter | None = None) -> Tracer | None:
    """The tracer, or None when LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY is unset or blank.

    Without both keys no client is made, so nothing is sent and no thread is started. `exporter`
    replaces the one that sends to Langfuse; tests pass one that keeps spans in memory.
    """
    public_key = (settings.langfuse_public_key or "").strip()
    secret = settings.langfuse_secret_key
    secret_key = secret.get_secret_value().strip() if secret is not None else ""
    if not public_key or not secret_key:
        return None
    try:
        provider = TracerProvider()
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=(settings.langfuse_base_url or "").strip() or None,
            tracer_provider=provider,
            should_export_span=is_langfuse_span,
            mask=_mask_hook,
            span_exporter=exporter,
        )
    except Exception as exc:
        logger.error("tracing_off", extra={"error_type": type(exc).__name__})
        return None
    logger.info("tracing_on")
    return Tracer(client, provider)


class TracedModel:
    """Wraps a model and records each call as a generation in the current span.

    Outside a trace there is no current span, and a call is passed on with nothing recorded. An
    exception from the inner model is recorded by its class and raised again.
    """

    def __init__(self, inner: ModelPort) -> None:
        self._inner = inner

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        with current().generation(task, system=system, user=user) as call:
            reply = await self._inner.structured(
                task=task, system=system, user=user, schema=schema, timeout_s=timeout_s
            )
            call.reply(reply)
        return reply
