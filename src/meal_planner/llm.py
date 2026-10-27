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
import math
import os
import random as _random
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# LiteLLM downloads its price list from GitHub when it is imported, unless told to use the copy
# that ships with the package. Set before the import so a test or a cold start never waits on
# the network.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
# In its default mode LiteLLM calls load_dotenv() on import, which reads the first .env found by
# walking up from its own install folder. Keys and settings would then come from a file nobody
# passed in. They must come from the process environment only, which `make run` and the command
# get through uv's --env-file. Set LITELLM_MODE=DEV to bring the old behaviour back.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
# LiteLLM sets its log level from this variable when it is imported, and at DEBUG it logs the
# whole request. A value in the environment is overridden: the prompts hold the user's rules.
os.environ["LITELLM_LOG"] = "ERROR"

import litellm
from litellm.exceptions import APIConnectionError, RateLimitError, Timeout
from pydantic import BaseModel, ValidationError

from meal_planner.log import get_logger

# On a failed call LiteLLM prints a provider list to stdout, which only adds noise.
litellm.suppress_debug_info = True
# LiteLLM's own loggers can carry the request or the reply, so they are switched off whatever
# their level. A failed call still gets a `model_call` line below, with the error code and the
# exception's class.
for _name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
    logging.getLogger(_name).disabled = True

logger = get_logger(__name__)

# Seconds to wait after the first failed try. Each wait after that is twice the last.
BACKOFF_BASE_S = 1.0
# Each wait is made longer by up to this share of itself, drawn at random, so that calls which
# failed together (the slots of a week are written in parallel) do not all try again at once.
BACKOFF_JITTER_SHARE = 0.5
# The longest wait a provider's Retry-After is followed for; a longer one is cut to this, so a
# call never sits idle for minutes. The tries still run out after `max_retries`.
RETRY_AFTER_CAP_S = 60.0

# The codes a ModelReply.error can hold.
INVALID_JSON = "invalid_json"
SCHEMA_MISMATCH = "schema_mismatch"
RATE_LIMITED = "rate_limited"
TIMEOUT = "timeout"
SERVER_ERROR = "server_error"
MODEL_ERROR = "model_error"
NO_RECORDING = "no_recording"
# The codes of a call that got no reply at all, so there is nothing a recording could hold.
NO_REPLY = frozenset({RATE_LIMITED, TIMEOUT, SERVER_ERROR, MODEL_ERROR, NO_RECORDING})


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


def missing_api_key(model: str) -> list[str]:
    """The environment variables LiteLLM reads for `model`'s key that are not set.

    Empty when the key is there, and also when LiteLLM has no variable to name for the model (a
    local one, say), because then there is nothing to tell the caller to set.
    """
    found = litellm.validate_environment(model=model)
    return [] if found["keys_in_environment"] else list(found["missing_keys"])


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


# How long a provider asks the caller to wait. Gemini puts it in the error body, as a RetryInfo
# detail such as "retryDelay": "17s", rather than in a Retry-After header.
_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


def retry_after_s(exc: Exception) -> float | None:
    """The seconds the provider asked for before the next try, or None when it asked for none.

    Read from the response's retry-after-ms or Retry-After header (seconds or an HTTP date), then
    from Gemini's retryDelay in the error body. A value that is not a wait (a word, a negative or
    endless number) counts as none. A date in the past is no wait at all.
    """
    headers = _headers(exc)
    if (ms := _seconds(headers.get("retry-after-ms"))) is not None:
        return ms / 1000
    header = headers.get("retry-after")
    if (seconds := _seconds(header)) is not None:
        return seconds
    if header:
        try:
            when = parsedate_to_datetime(header)
        except (TypeError, ValueError):
            when = None
        if when is not None and when.tzinfo is not None:
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
    response = getattr(exc, "response", None)
    for text in (str(exc), getattr(response, "text", "") if response is not None else ""):
        if isinstance(text, str) and (found := _RETRY_DELAY.search(text)):
            return float(found.group(1))
    return None


def _headers(exc: Exception) -> dict[str, str]:
    """The response's headers, with lower-case names, and any LiteLLM attached to the error."""
    found: dict[str, str] = {}
    response = getattr(exc, "response", None)
    for source in (getattr(response, "headers", None), getattr(exc, "headers", None)):
        if isinstance(source, Mapping):
            found.update({str(name).lower(): str(value) for name, value in source.items()})
    return found


def _seconds(value: str | None) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def backoff_s(tries: int, exc: Exception, draw: float) -> float:
    """The wait before the next try, after `tries` tries that failed with `exc` the last time.

    `draw` is a random number in [0, 1). Without a Retry-After the wait doubles from
    BACKOFF_BASE_S and is made longer by up to BACKOFF_JITTER_SHARE of itself. With one, it is
    what the provider asked for, at most RETRY_AFTER_CAP_S, plus up to one BACKOFF_BASE_S, so
    calls told the same time do not come back together.
    """
    asked = retry_after_s(exc)
    if asked is not None:
        return min(asked, RETRY_AFTER_CAP_S) + BACKOFF_BASE_S * draw
    return BACKOFF_BASE_S * 2.0 ** (tries - 1) * (1 + BACKOFF_JITTER_SHARE * draw)


# A provider's reply is read field by field with getattr, because not every provider fills every
# field: one with no message is an empty reply, and one with no usage counts zero tokens.


def _text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    first = choices[0] if isinstance(choices, list | tuple) and choices else None
    content = getattr(getattr(first, "message", None), "content", None)
    return str(content or "")


def _tokens(response: Any, name: str) -> int:
    count = getattr(getattr(response, "usage", None), name, None)
    return int(count) if isinstance(count, int | float) and math.isfinite(count) else 0


def _cost(response: Any) -> float | None:
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:
        # LiteLLM raises a plain Exception, or a BadRequestError, for a model it has no price for.
        return None


class LiteLLMModel:
    """Calls a model through LiteLLM, asking for JSON that fits the schema.

    `max_retries` is how many times a call is tried in all. A rate limit (429), a server error
    (5xx), a dropped connection or a timeout is tried again after a wait (`backoff_s`): the time
    the provider asked for in a Retry-After, up to a cap, or else a wait that doubles each time,
    each with a random part so parallel calls spread out. Once the tries are used up the reply's
    error is "rate_limited", "timeout" or "server_error".
    Any other refusal (a bad key, a rejected request) is not retried and gives "model_error".
    A reply that is not valid JSON for the schema is returned as such and not retried: asking
    again is the repair loop's decision.

    `acompletion`, `sleep` and `random` (a number in [0, 1) for each wait) are there for tests
    to replace.
    """

    def __init__(
        self,
        model: str,
        max_retries: int = 3,
        *,
        acompletion: Callable[..., Awaitable[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random: Callable[[], float] = _random.random,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries is the number of tries and must be at least 1")
        self.model = model
        self._max_tries = max_retries
        self._acompletion = acompletion or litellm.acompletion
        self._sleep = sleep
        self._random = random
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
            try:
                response = await self._acompletion(**request)
                error = error_type = None
                break
            except Exception as exc:  # LiteLLM raises many types; _classify sorts them
                error, retryable = _classify(exc)
                error_type = type(exc).__name__
                if not retryable or attempt + 1 == self._max_tries:
                    break
                await self._sleep(backoff_s(attempt + 1, exc, self._random()))

        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if response is None:
            reply = ModelReply[T](
                value=None, raw="", error=error, latency_ms=latency_ms, model=self.model
            )
        else:
            raw = _text(response)
            value, error = parse_reply(raw, schema)
            reply = ModelReply(
                value=value,
                raw=raw,
                error=error,
                input_tokens=_tokens(response, "prompt_tokens"),
                output_tokens=_tokens(response, "completion_tokens"),
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
    """One line of a recording: a model call, minus the prompt, which holds the user's rules.

    `prompt_sha256` is the hash of the prompts that ReplayModel finds the reply by. It is not a
    credential, and its name says so: a field called `key` holding 64 hex characters is flagged
    as an API key by secret scanners.
    """

    prompt_sha256: str
    task: str
    raw: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    model: str


def recording_key(system: str, user: str) -> str:
    # A separator, so that moving text from the system prompt to the user prompt is a new hash.
    return hashlib.sha256(f"{system}\0{user}".encode()).hexdigest()


class RecordingModel:
    """Wraps a model and appends each reply it gets to a JSON-lines file, for ReplayModel.

    The file is appended to, never cleared: delete it to record afresh. Every reply is recorded,
    one with no text too, so that a replay gives the same outcome as the call did ("invalid_json"
    for an empty reply, not "no_recording"). A call that got no reply at all (rate limited, timed
    out, refused) is not recorded, as there is nothing to replay.
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
        if reply.error not in NO_REPLY:
            line = Recording(
                prompt_sha256=recording_key(system, user),
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
                self._recorded.setdefault((entry.task, entry.prompt_sha256), []).append(entry)
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
