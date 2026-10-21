"""The one client every model call goes through, and the stand-ins tests and the smoke eval use.

A call asks for JSON that fits a Pydantic model and always comes back as a ModelReply: a reply
that cannot be used carries an `error` code and no value, and nothing here raises for it. That
keeps the repair loop and the API in charge of what a bad reply means. The model is only ever
asked for words (recipes, a reading of typed text), never for a nutrition number.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# LiteLLM downloads its price list from GitHub when it is imported, unless told to use the copy
# that ships with the package. Set before the import so a test or a cold start never waits on
# the network.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from litellm.exceptions import APIConnectionError, RateLimitError, Timeout
from pydantic import BaseModel, ValidationError

from meal_planner.log import get_logger

# On a failed call LiteLLM prints a provider list to stdout, which only adds noise.
litellm.suppress_debug_info = True

logger = get_logger(__name__)

# Seconds to wait after the first failed try. Each wait after that is twice the last.
BACKOFF_BASE_S = 1.0

# The codes a ModelReply.error can hold.
INVALID_JSON = "invalid_json"
SCHEMA_MISMATCH = "schema_mismatch"
RATE_LIMITED = "rate_limited"
TIMEOUT = "timeout"
SERVER_ERROR = "server_error"
MODEL_ERROR = "model_error"
NO_RECORDING = "no_recording"


@dataclass(frozen=True)
class ModelReply[T]:
    """What came back from one call. `value` is None exactly when `error` is set."""

    value: T | None
    raw: str
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # None when the model has no price in LiteLLM's list.
    cost_usd: float | None = None
    latency_ms: float = 0.0
    model: str = ""


@runtime_checkable
class ModelPort(Protocol):
    """What the rest of the app needs from a model, so a fake or a replay can stand in.

    Runtime-checkable so a test can confirm a stand-in still has the method after a change here.
    """

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]: ...


def parse_reply[T: BaseModel](raw: str, schema: type[T]) -> tuple[T | None, str | None]:
    """The reply read as `schema`, or the error code for why it could not be."""
    try:
        return schema.model_validate_json(raw), None
    except ValidationError as exc:
        # Only the error types are read, so none of the reply's text is carried further.
        broken_json = any(item["type"] == "json_invalid" for item in exc.errors())
        return None, INVALID_JSON if broken_json else SCHEMA_MISMATCH


def _classify(exc: Exception) -> tuple[str, bool]:
    """The error code for a failed call, and whether trying again can help."""
    # LiteLLM has a class for some statuses and a generic APIError(status_code=...) for the rest,
    # and which one a status gets depends on the provider, so the number decides too.
    status = getattr(exc, "status_code", None)
    # Timeout is a kind of APIConnectionError, so it has to be asked about first.
    if isinstance(exc, Timeout | TimeoutError) or status == 408:
        return TIMEOUT, True
    if isinstance(exc, RateLimitError) or status == 429:
        return RATE_LIMITED, True
    if isinstance(exc, APIConnectionError) or (isinstance(status, int) and status >= 500):
        return SERVER_ERROR, True
    return MODEL_ERROR, False


def _text(response: Any) -> str:
    choices = response.choices
    return str(choices[0].message.content or "") if choices else ""


def _cost(response: Any) -> float | None:
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:
        # LiteLLM raises a plain Exception, or a BadRequestError, for a model it has no price for.
        return None


class LiteLLMModel:
    """Calls a model through LiteLLM, asking for JSON that fits the schema.

    `max_retries` is how many times a call is tried in all. A rate limit (429), a server error
    (5xx), a dropped connection or a timeout is tried again after a wait that doubles each time;
    once the tries are used up the reply's error is "rate_limited", "timeout" or "server_error".
    Any other refusal (a bad key, a rejected request) is not retried and gives "model_error".
    A reply that is not valid JSON for the schema is returned as such and not retried: asking
    again is the repair loop's decision.

    `acompletion` and `sleep` are there for tests to replace.
    """

    def __init__(
        self,
        model: str,
        max_retries: int = 3,
        *,
        acompletion: Callable[..., Awaitable[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries is the number of tries and must be at least 1")
        self.model = model
        self._max_tries = max_retries
        self._acompletion = acompletion or litellm.acompletion
        self._sleep = sleep
        self._takes_schema = litellm.supports_response_schema(model=model)

    def _request[T: BaseModel](
        self, system: str, user: str, schema: type[T], timeout_s: float
    ) -> dict[str, Any]:
        response_format: Any = schema
        if not self._takes_schema:
            # The provider cannot be handed a schema, so ask for any JSON object and say which.
            response_format = {"type": "json_object"}
            system = (
                f"{system}\n\nReply with one JSON object that fits this JSON schema:\n"
                f"{json.dumps(schema.model_json_schema())}"
            )
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": response_format,
            "timeout": timeout_s,
            # The tries are counted here; LiteLLM retrying inside each would multiply them.
            "num_retries": 0,
        }

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        started = time.perf_counter()
        request = self._request(system, user, schema, timeout_s)
        response: Any = None
        error: str | None = None
        error_type: str | None = None
        for attempt in range(self._max_tries):
            if attempt:
                await self._sleep(BACKOFF_BASE_S * 2 ** (attempt - 1))
            try:
                response = await self._acompletion(**request)
                error = error_type = None
                break
            except Exception as exc:  # LiteLLM raises many types; _classify sorts them
                error, retryable = _classify(exc)
                error_type = type(exc).__name__
                if not retryable:
                    break

        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if response is None:
            reply = ModelReply[T](
                value=None, raw="", error=error, latency_ms=latency_ms, model=self.model
            )
        else:
            raw = _text(response)
            value, error = parse_reply(raw, schema)
            usage = response.usage
            reply = ModelReply(
                value=value,
                raw=raw,
                error=error,
                input_tokens=int(usage.prompt_tokens or 0),
                output_tokens=int(usage.completion_tokens or 0),
                cost_usd=_cost(response),
                latency_ms=latency_ms,
                model=self.model,
            )

        # Only the exception's class is logged: a provider can echo the request in its message.
        logger.log(
            logging.WARNING if reply.error else logging.INFO,
            "model_call",
            extra={
                "task": task,
                "model": self.model,
                "input_tokens": reply.input_tokens,
                "output_tokens": reply.output_tokens,
                "latency_ms": reply.latency_ms,
                "cost_usd": reply.cost_usd,
                **({"error": reply.error} if reply.error else {}),
                **({"error_type": error_type} if error_type else {}),
            },
        )
        return reply


class FakeModel:
    """A model for tests: canned raw replies, in order for each task, parsed like the real client.

    A call for a task with no reply left raises, so a test fails on a call it did not expect.
    """

    def __init__(self, replies: dict[str, list[str]]) -> None:
        self._replies = {task: list(raws) for task, raws in replies.items()}
        # (task, system, user) for every call, so a test can check what it was asked.
        self.calls: list[tuple[str, str, str]] = []

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        self.calls.append((task, system, user))
        if not self._replies.get(task):
            raise LookupError(f"FakeModel has no reply left for task '{task}'")
        raw = self._replies[task].pop(0)
        value, error = parse_reply(raw, schema)
        return ModelReply(value=value, raw=raw, error=error, cost_usd=0.0, model="fake")


class Recording(BaseModel):
    """One line of a recording: a model call, minus the prompt, which holds the user's rules."""

    key: str
    task: str
    raw: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    model: str


def recording_key(system: str, user: str) -> str:
    # A separator, so that moving text from the system prompt to the user prompt is a new key.
    return hashlib.sha256(f"{system}\0{user}".encode()).hexdigest()


class RecordingModel:
    """Wraps a model and appends each reply it gets to a JSON-lines file, for ReplayModel.

    The file is appended to, never cleared: delete it to record afresh. A call that got no reply
    at all (rate limited, timed out) is not recorded, as there is nothing to replay.
    """

    def __init__(self, inner: ModelPort, path: Path) -> None:
        self._inner = inner
        self._path = path

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        reply = await self._inner.structured(
            task=task, system=system, user=user, schema=schema, timeout_s=timeout_s
        )
        if reply.raw:
            line = Recording(
                key=recording_key(system, user),
                task=task,
                raw=reply.raw,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost_usd=reply.cost_usd,
                model=reply.model,
            )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as file:
                file.write(line.model_dump_json() + "\n")
        return reply


class ReplayModel:
    """Answers from a recording, without a key or the network.

    A call is found by its task and a hash of its prompts. A prompt recorded twice is answered
    with its two recordings in order. A call with no recording left gets error "no_recording"
    rather than an exception, so a smoke run can report every miss instead of stopping at one.
    """

    def __init__(self, path: Path) -> None:
        self._recorded: dict[tuple[str, str], list[Recording]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = Recording.model_validate_json(line)
                self._recorded.setdefault((entry.task, entry.key), []).append(entry)
        self._used: dict[tuple[str, str], int] = {}

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], timeout_s: float
    ) -> ModelReply[T]:
        key = (task, recording_key(system, user))
        used = self._used.get(key, 0)
        entries = self._recorded.get(key, [])
        if used >= len(entries):
            return ModelReply(value=None, raw="", error=NO_RECORDING, model="replay")
        self._used[key] = used + 1
        entry = entries[used]
        value, error = parse_reply(entry.raw, schema)
        return ModelReply(
            value=value,
            raw=entry.raw,
            error=error,
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            cost_usd=entry.cost_usd,
            model=entry.model,
        )
