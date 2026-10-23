"""The model client: parsing, the LiteLLM wrapper with its call stubbed, and record and replay."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import litellm
import pytest
from pydantic import BaseModel

from meal_planner import log
from meal_planner.llm import (
    FakeModel,
    LiteLLMModel,
    ModelPort,
    ModelReply,
    RecordingModel,
    ReplayModel,
)

pytestmark = pytest.mark.anyio

# The default model is in LiteLLM's price list and supports a JSON schema; the other two are
# not mapped, so one has no price and the other takes the plain JSON-object fallback.
MODEL = "gemini/gemini-3.1-flash-lite"
NO_SCHEMA_MODEL = "ollama/llama3"
UNMAPPED_MODEL = "unmapped/model"

SYSTEM = "You write recipes."
# Text that must never reach a log line.
USER = "Vegetarian, no onions, allergic to cashews"
REPLY = '{"name": "Rajma", "servings": 4}'


class Dish(BaseModel):
    name: str
    servings: int


def rate_limit() -> Exception:
    return litellm.RateLimitError(message="429", llm_provider="gemini", model=MODEL)


def timeout() -> Exception:
    return litellm.Timeout(message="timed out", model=MODEL, llm_provider="gemini")


def server_error() -> Exception:
    return litellm.InternalServerError(message="500", llm_provider="gemini", model=MODEL)


def unavailable() -> Exception:
    return litellm.ServiceUnavailableError(message="503", llm_provider="gemini", model=MODEL)


def bad_gateway() -> Exception:
    return litellm.BadGatewayError(message="502", llm_provider="openai", model=MODEL)


def api_error(status_code: int) -> Exception:
    """The generic error LiteLLM raises for a status it has no class for."""
    return litellm.APIError(
        status_code=status_code, message=str(status_code), llm_provider="anthropic", model=MODEL
    )


def bad_key() -> Exception:
    return litellm.AuthenticationError(message="bad key", llm_provider="gemini", model=MODEL)


def completion(
    content: str | None,
    *,
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
    model: str = MODEL,
) -> litellm.ModelResponse:
    # The price is looked up from the model the response names, as it is on a real reply.
    return litellm.ModelResponse(
        model=model,
        choices=[{"message": {"content": content, "role": "assistant"}, "finish_reason": "stop"}],
        usage=litellm.Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


class StubCompletion:
    """Stands in for litellm.acompletion: raises or returns each outcome in turn."""

    def __init__(self, *outcomes: Exception | litellm.ModelResponse) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> litellm.ModelResponse:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Sleeps:
    """Stands in for asyncio.sleep so backoff costs no time, and remembers the delays."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def client(
    stub: StubCompletion, sleeps: Sleeps | None = None, *, model: str = MODEL, max_retries: int = 3
) -> LiteLLMModel:
    return LiteLLMModel(
        model,
        max_retries=max_retries,
        acompletion=stub,
        sleep=sleeps if sleeps is not None else Sleeps(),
    )


async def ask(model: ModelPort, task: str = "write", user: str = USER) -> ModelReply[Dish]:
    return await model.structured(task=task, system=SYSTEM, user=user, schema=Dish, timeout_s=30.0)


def log_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()]


# --- FakeModel and parsing -------------------------------------------------------------------


async def test_fake_parses_valid_json() -> None:
    reply = await ask(FakeModel({"write": [REPLY]}))

    assert reply.value == Dish(name="Rajma", servings=4)
    assert reply.error is None
    assert reply.raw == REPLY


async def test_fake_returns_replies_in_order_per_task() -> None:
    fake = FakeModel(
        {
            "write": ['{"name": "Rajma", "servings": 4}', '{"name": "Kadhi", "servings": 2}'],
            "read": ['{"name": "Dal", "servings": 1}'],
        }
    )

    first = await ask(fake, "write")
    other = await ask(fake, "read")
    second = await ask(fake, "write")

    assert [r.value.name for r in (first, other, second) if r.value] == ["Rajma", "Dal", "Kadhi"]


async def test_fake_remembers_what_it_was_asked() -> None:
    fake = FakeModel({"write": [REPLY]})

    await ask(fake)

    assert fake.calls == [("write", SYSTEM, USER)]


async def test_fake_with_no_reply_left_fails_loudly() -> None:
    fake = FakeModel({"write": [REPLY]})
    await ask(fake)

    with pytest.raises(LookupError, match="write"):
        await ask(fake)
    with pytest.raises(LookupError, match="read"):
        await ask(fake, "read")


async def test_fake_does_not_use_up_the_callers_lists() -> None:
    replies = {"write": [REPLY]}
    await ask(FakeModel(replies))

    assert replies == {"write": [REPLY]}


async def test_malformed_json_is_error_not_raise() -> None:
    reply = await ask(FakeModel({"write": ["not json at all"]}))

    assert reply.value is None
    assert reply.error == "invalid_json"
    assert reply.raw == "not json at all"


async def test_missing_field_is_error() -> None:
    reply = await ask(FakeModel({"write": ['{"name": "Rajma"}']}))

    assert reply.value is None
    assert reply.error == "schema_mismatch"


@pytest.mark.parametrize("raw", ['{"name": "Rajma", "servings": "many"}', "[1, 2]", "null"])
async def test_wrong_shape_is_error(raw: str) -> None:
    reply = await ask(FakeModel({"write": [raw]}))

    assert reply.value is None
    assert reply.error == "schema_mismatch"


async def test_empty_reply_is_invalid_json() -> None:
    reply = await ask(FakeModel({"write": [""]}))

    assert reply.value is None
    assert reply.error == "invalid_json"


async def test_every_model_has_the_port_methods() -> None:
    fake = FakeModel({})

    assert isinstance(fake, ModelPort)
    assert isinstance(client(StubCompletion()), ModelPort)
    assert isinstance(RecordingModel(fake, Path("unused.jsonl")), ModelPort)


# --- LiteLLMModel ----------------------------------------------------------------------------


async def test_reply_carries_value_tokens_cost_and_model() -> None:
    stub = StubCompletion(completion(REPLY, prompt_tokens=120, completion_tokens=45))

    reply = await ask(client(stub))

    assert reply.value == Dish(name="Rajma", servings=4)
    assert reply.error is None
    assert reply.raw == REPLY
    assert (reply.input_tokens, reply.output_tokens) == (120, 45)
    assert reply.cost_usd is not None
    assert reply.cost_usd > 0
    assert reply.latency_ms >= 0
    assert reply.model == MODEL


async def test_request_asks_for_the_schema_with_one_try_per_call() -> None:
    stub = StubCompletion(completion(REPLY))

    await client(stub).structured(
        task="write", system=SYSTEM, user=USER, schema=Dish, timeout_s=12.5
    )

    (call,) = stub.calls
    assert call["model"] == MODEL
    assert call["messages"] == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER},
    ]
    assert call["response_format"] is Dish
    assert call["timeout"] == 12.5
    # LiteLLM's own retries would multiply ours.
    assert call["num_retries"] == 0


async def test_model_without_schema_support_gets_the_schema_in_the_prompt() -> None:
    stub = StubCompletion(completion(REPLY))

    reply = await ask(client(stub, model=NO_SCHEMA_MODEL))

    (call,) = stub.calls
    assert call["response_format"] == {"type": "json_object"}
    system = call["messages"][0]["content"]
    assert system.startswith(SYSTEM)
    assert "servings" in system
    assert reply.value == Dish(name="Rajma", servings=4)


async def test_retry_then_rate_limited() -> None:
    stub = StubCompletion(rate_limit(), rate_limit(), rate_limit())
    sleeps = Sleeps()

    reply = await ask(client(stub, sleeps))

    assert reply.value is None
    assert reply.error == "rate_limited"
    assert len(stub.calls) == 3
    # Backoff doubles, and there is no wait after the last try.
    assert len(sleeps.delays) == 2
    assert sleeps.delays[1] == 2 * sleeps.delays[0] > 0


async def test_retry_then_timeout() -> None:
    reply = await ask(client(StubCompletion(timeout(), timeout(), timeout())))

    assert reply.error == "timeout"


async def test_builtin_timeout_counts_as_timeout() -> None:
    stub = StubCompletion(TimeoutError(), TimeoutError(), TimeoutError())

    reply = await ask(client(stub))

    assert reply.error == "timeout"
    assert len(stub.calls) == 3


@pytest.mark.parametrize(
    "make_error",
    [
        server_error,
        unavailable,
        bad_gateway,
        lambda: api_error(500),
        lambda: api_error(504),
        lambda: api_error(529),
    ],
    ids=["500", "503", "502", "api-500", "api-504", "api-529"],
)
async def test_server_errors_are_retried_then_reported(make_error: Any) -> None:
    stub = StubCompletion(make_error(), make_error(), make_error())

    reply = await ask(client(stub))

    assert reply.error == "server_error"
    assert len(stub.calls) == 3


@pytest.mark.parametrize(
    ("status_code", "code"), [(429, "rate_limited"), (408, "timeout"), (599, "server_error")]
)
async def test_a_status_with_no_class_is_read_by_its_number(status_code: int, code: str) -> None:
    stub = StubCompletion(*[api_error(status_code)] * 3)

    reply = await ask(client(stub))

    assert reply.error == code
    assert len(stub.calls) == 3


@pytest.mark.parametrize("status_code", [400, 404, 499])
async def test_a_status_below_500_is_not_retried(status_code: int) -> None:
    stub = StubCompletion(api_error(status_code))

    reply = await ask(client(stub))

    assert reply.error == "model_error"
    assert len(stub.calls) == 1


async def test_a_transient_error_then_success_gives_the_value() -> None:
    stub = StubCompletion(rate_limit(), completion(REPLY, prompt_tokens=5, completion_tokens=3))
    sleeps = Sleeps()

    reply = await ask(client(stub, sleeps))

    assert reply.value == Dish(name="Rajma", servings=4)
    assert reply.error is None
    assert (reply.input_tokens, reply.output_tokens) == (5, 3)
    assert len(sleeps.delays) == 1


async def test_max_retries_is_the_number_of_tries() -> None:
    stub = StubCompletion(rate_limit(), rate_limit())

    reply = await ask(client(stub, max_retries=2))

    assert reply.error == "rate_limited"
    assert len(stub.calls) == 2


async def test_a_refused_request_is_not_retried() -> None:
    stub = StubCompletion(bad_key())
    sleeps = Sleeps()

    reply = await ask(client(stub, sleeps))

    assert reply.value is None
    assert reply.error == "model_error"
    assert len(stub.calls) == 1
    assert sleeps.delays == []


async def test_a_malformed_reply_is_returned_not_retried() -> None:
    stub = StubCompletion(completion("Sorry, I cannot do that."))

    reply = await ask(client(stub))

    assert reply.value is None
    assert reply.error == "invalid_json"
    assert reply.raw == "Sorry, I cannot do that."
    assert (reply.input_tokens, reply.output_tokens) == (11, 7)
    assert len(stub.calls) == 1


async def test_a_reply_with_no_text_is_an_error() -> None:
    reply = await ask(client(StubCompletion(completion(None))))

    assert reply.value is None
    assert reply.error == "invalid_json"
    assert reply.raw == ""


async def test_a_model_with_no_price_has_no_cost() -> None:
    stub = StubCompletion(completion(REPLY, model=UNMAPPED_MODEL))

    reply = await ask(client(stub, model=UNMAPPED_MODEL))

    assert reply.value == Dish(name="Rajma", servings=4)
    assert reply.cost_usd is None


def test_a_client_must_try_at_least_once() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        LiteLLMModel(MODEL, max_retries=0)


# --- Logging ---------------------------------------------------------------------------------


async def test_log_line_has_no_prompt_text(capsys: pytest.CaptureFixture[str]) -> None:
    stub = StubCompletion(completion(REPLY, prompt_tokens=120, completion_tokens=45))

    await ask(client(stub))

    captured = capsys.readouterr().err
    assert "cashews" not in captured
    assert SYSTEM not in captured
    assert "Rajma" not in captured
    (line,) = [json.loads(text) for text in captured.splitlines()]
    assert line["task"] == "write"
    assert line["model"] == MODEL
    assert line["input_tokens"] == 120
    assert line["output_tokens"] == 45
    assert line["latency_ms"] >= 0
    assert line["cost_usd"] > 0
    assert line["request_id"] is None


async def test_log_line_names_the_request(capsys: pytest.CaptureFixture[str]) -> None:
    token = log.request_id_var.set("req-123")
    try:
        await ask(client(StubCompletion(completion(REPLY))))
    finally:
        log.request_id_var.reset(token)

    (line,) = log_lines(capsys)
    assert line["request_id"] == "req-123"


async def test_failed_call_is_logged_with_its_error_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await ask(client(StubCompletion(rate_limit(), rate_limit(), rate_limit())))

    (line,) = log_lines(capsys)
    assert line["level"] == "WARNING"
    assert line["error"] == "rate_limited"
    assert line["input_tokens"] == 0


async def test_provider_error_text_stays_out_of_the_log(
    capsys: pytest.CaptureFixture[str],
) -> None:
    echoed = litellm.BadRequestError(
        message=f"bad request: {USER}", model=MODEL, llm_provider="gemini"
    )

    reply = await ask(client(StubCompletion(echoed)))

    assert reply.error == "model_error"
    captured = capsys.readouterr().err
    assert "cashews" not in captured
    (line,) = [json.loads(text) for text in captured.splitlines()]
    assert line["error_type"] == "BadRequestError"


def test_logger_drops_fields_it_does_not_know(capsys: pytest.CaptureFixture[str]) -> None:
    logger = log.get_logger("meal_planner.test_allowlist")

    logger.info("event", extra={"task": "write", "user": USER, "raw": REPLY})

    (line,) = log_lines(capsys)
    assert line["event"] == "event"
    assert line["task"] == "write"
    assert "user" not in line
    assert "raw" not in line


def test_get_logger_adds_one_handler_however_often_it_is_asked() -> None:
    first = log.get_logger("meal_planner.test_once")
    second = log.get_logger("meal_planner.test_once")

    assert first is second
    assert len(first.handlers) == 1


# --- Record and replay -----------------------------------------------------------------------


def recorded(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_recording_then_replay_returns_the_same_reply(tmp_path: Path) -> None:
    path = tmp_path / "recordings" / "smoke.jsonl"
    stub = StubCompletion(completion(REPLY, prompt_tokens=120, completion_tokens=45))
    live = await ask(RecordingModel(client(stub), path))

    replayed = await ask(ReplayModel(path))

    assert replayed.value == live.value == Dish(name="Rajma", servings=4)
    assert replayed.raw == live.raw
    assert replayed.error is None
    assert (replayed.input_tokens, replayed.output_tokens) == (120, 45)
    assert replayed.cost_usd == live.cost_usd
    assert replayed.model == MODEL


async def test_a_recording_is_one_json_line_per_call_without_the_prompt(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    stub = StubCompletion(completion(REPLY), completion(REPLY))
    model = RecordingModel(client(stub), path)

    await ask(model, "write")
    await ask(model, "read", user="a different prompt")

    first, second = recorded(path)
    assert set(first) == {
        "key",
        "task",
        "raw",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "model",
    }
    assert (first["task"], second["task"]) == ("write", "read")
    assert first["raw"] == REPLY
    assert first["key"] != second["key"]
    assert "cashews" not in path.read_text()


async def test_recording_appends_to_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    await ask(RecordingModel(FakeModel({"write": [REPLY]}), path))

    await ask(RecordingModel(FakeModel({"write": [REPLY]}), path), user="another")

    assert len(recorded(path)) == 2


async def test_recording_skips_a_call_that_got_no_reply(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    stub = StubCompletion(rate_limit(), rate_limit(), rate_limit())

    reply = await ask(RecordingModel(client(stub), path))

    assert reply.error == "rate_limited"
    assert not path.exists()


async def test_recording_keeps_a_malformed_reply_so_it_replays_as_one(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    await ask(RecordingModel(FakeModel({"write": ["oops"]}), path))

    replayed = await ask(ReplayModel(path))

    assert replayed.value is None
    assert replayed.error == "invalid_json"
    assert replayed.raw == "oops"


async def test_replay_miss_is_error_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    await ask(RecordingModel(FakeModel({"write": [REPLY]}), path))

    miss = await ask(ReplayModel(path), user="a prompt nobody recorded")

    assert miss.value is None
    assert miss.error == "no_recording"


async def test_replay_is_keyed_by_task_and_prompt(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    await ask(RecordingModel(FakeModel({"write": [REPLY]}), path))
    replay = ReplayModel(path)

    other_task = await ask(replay, "read")
    other_system = await replay.structured(
        task="write", system="another system", user=USER, schema=Dish, timeout_s=1.0
    )
    hit = await ask(replay)

    assert other_task.error == "no_recording"
    assert other_system.error == "no_recording"
    assert hit.error is None


async def test_the_split_between_system_and_user_is_part_of_the_key(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    await RecordingModel(FakeModel({"write": [REPLY]}), path).structured(
        task="write", system="ab", user="c", schema=Dish, timeout_s=1.0
    )

    moved = await ReplayModel(path).structured(
        task="write", system="a", user="bc", schema=Dish, timeout_s=1.0
    )

    assert moved.error == "no_recording"


async def test_replay_gives_a_repeated_prompt_its_recordings_in_order(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    one, two = '{"name": "One", "servings": 1}', '{"name": "Two", "servings": 2}'
    fake = FakeModel({"write": [one, two]})
    recorder = RecordingModel(fake, path)
    await ask(recorder)
    await ask(recorder)
    replay = ReplayModel(path)

    names = [(await ask(replay)).value for _ in range(3)]

    assert names == [Dish(name="One", servings=1), Dish(name="Two", servings=2), None]
    assert (await ask(replay)).error == "no_recording"


async def test_replay_of_a_missing_file_fails_at_once(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ReplayModel(tmp_path / "missing.jsonl")


# LiteLLM's own logging


def test_litellm_loggers_stay_silent_even_when_the_environment_asks_for_debug() -> None:
    """LiteLLM can log the request it sends, so importing our client switches its loggers off."""
    code = (
        "import logging, meal_planner.llm\n"
        "for name in ('LiteLLM', 'LiteLLM Router', 'LiteLLM Proxy'):\n"
        "    logger = logging.getLogger(name)\n"
        "    logger.debug('PROMPT-TEXT')\n"
        "    logger.warning('PROMPT-TEXT')\n"
        "    logger.error('PROMPT-TEXT')\n"
    )
    env = {**os.environ, "LITELLM_LOG": "DEBUG", "LITELLM_LOCAL_MODEL_COST_MAP": "True"}

    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )

    assert "PROMPT-TEXT" not in done.stdout + done.stderr
