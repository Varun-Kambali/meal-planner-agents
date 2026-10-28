"""The one client every other module uses to talk to nutrition-mcp.

nutrition-mcp runs as a subprocess over stdio and is the only source of nutrition numbers.
Results come back as nutrition-mcp's own models, validated from each tool's structured output.
Nothing here logs a tool's arguments, which can hold what the user typed.
"""

import re
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Protocol, Self, runtime_checkable

from mcp import Client, MCPError, StdioServerParameters
from mcp.types import CallToolResult, TextContent
from nutrition_mcp.schemas import MealItemIn, MealResult, NutritionResult, SearchResult
from pydantic import BaseModel, ValidationError

SERVER_COMMAND = "nutrition-mcp"
DATA_DIRS_VARIABLE = "NUTRITION_MCP_DATA_DIRS"
# Seconds a call may take before the SDK gives up. Lookups are in-memory, so this only
# fires when the server hangs.
CALL_TIMEOUT_S = 30.0

# The SDK puts this in front of the message a tool raised, which is the text worth keeping.
_SDK_PREFIX = re.compile(r"^Error executing tool \w+: ")
# The two messages nutrition-mcp writes that hold what a caller can act on: "item 2 (toor_dal):
# ..." names the meal item that failed, and "...; valid units: g, katori" lists the units a
# food takes. The unit list ends at the next ";" or the end of the message. The message also echoes
# the unit as it was typed, and typed text can say "valid units: " too, so the list that counts is
# the last one: nutrition-mcp writes its own at the very end.
_ITEM_FOOD = re.compile(r"^item \d+ \(([^)]+)\):")
_VALID_UNITS = re.compile(r"valid units: ([^;]+)")


class NutritionError(Exception):
    """nutrition-mcp refused a call, could not be asked, or sent a result that could not be read.

    A refusal carries nutrition-mcp's own message, and `food_id` when one food is to blame; a
    refusal whose message names no food has no `food_id`. `unreachable` is set only when
    nutrition-mcp could not be asked at all (the connection failed or timed out), which says
    nothing about the foods asked about. `unreadable` is set only when it answered with a result
    that could not be read. A refusal leaves both False.
    """

    def __init__(
        self,
        message: str,
        food_id: str | None = None,
        valid_units: list[str] | None = None,
        *,
        unreachable: bool = False,
        unreadable: bool = False,
    ) -> None:
        super().__init__(message)
        self.food_id = food_id
        self.valid_units = valid_units or []
        self.unreachable = unreachable
        self.unreadable = unreadable


def refusal(message: str, food_id: str | None = None) -> NutritionError:
    """The error for a call nutrition-mcp refused, from the message it wrote.

    `food_id` is the food the call was about, when one food was asked about. A meal's message
    names the item that failed instead, which is read from it. The units the message lists are
    the food's valid units. Both the real client and the test fake build their errors here, so
    they cannot read a message differently.
    """
    if food_id is None and (item := _ITEM_FOOD.match(message)):
        food_id = item.group(1)
    listed = _VALID_UNITS.findall(message)
    return NutritionError(message, food_id, listed[-1].split(", ") if listed else [])


@runtime_checkable
class NutritionPort(Protocol):
    """What the rest of the app needs from nutrition-mcp, so tests can stand in a fake.

    Runtime-checkable so a test can confirm a fake still has every method after a change here.
    """

    async def search(
        self,
        query: str,
        kind: Literal["dish", "ingredient"] | None = None,
        limit: int = 5,
    ) -> SearchResult: ...

    async def nutrition(self, food_id: str, amount: float, unit: str) -> NutritionResult: ...

    async def meal(self, items: list[MealItemIn]) -> MealResult: ...


class _Session(Protocol):
    """The part of an MCP client session that NutritionClient uses."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult: ...


def _server_command() -> str:
    """The nutrition-mcp script of this project's own environment, not whichever is on PATH."""
    bin_dir = str(Path(sys.executable).parent)
    found = shutil.which(SERVER_COMMAND, path=bin_dir) or shutil.which(SERVER_COMMAND)
    if found is None:
        raise FileNotFoundError(
            f"the {SERVER_COMMAND} command is not installed in {bin_dir} or on PATH"
        )
    return found


def server_parameters(data_dirs: str) -> StdioServerParameters:
    """How to start the server. `data_dirs` is nutrition-mcp's own format, empty for none.

    The SDK gives the subprocess only a short list of safe variables (HOME, PATH and the like)
    plus `env`. That keeps API keys out of it, and it means a NUTRITION_MCP_DATA_DIRS set in
    this process is ignored: empty `data_dirs` always means the bundled data only.
    """
    env = {DATA_DIRS_VARIABLE: data_dirs} if data_dirs else {}
    return StdioServerParameters(command=_server_command(), env=env)


def _message(result: CallToolResult) -> str:
    text = " ".join(block.text for block in result.content if isinstance(block, TextContent))
    text = _SDK_PREFIX.sub("", text)
    return text or "nutrition-mcp returned an error with no text"


class NutritionClient:
    """Calls nutrition-mcp's tools over an open session. `start` opens one."""

    def __init__(self, session: _Session) -> None:
        self._session = session

    @classmethod
    @asynccontextmanager
    async def start(cls, data_dirs: str) -> AsyncIterator[Self]:
        """Run nutrition-mcp over stdio for the length of the block."""
        params = server_parameters(data_dirs)
        async with Client(params, read_timeout_seconds=CALL_TIMEOUT_S) as session:
            yield cls(session)

    async def search(
        self,
        query: str,
        kind: Literal["dish", "ingredient"] | None = None,
        limit: int = 5,
    ) -> SearchResult:
        arguments: dict[str, Any] = {"query": query, "limit": limit}
        if kind is not None:
            arguments["kind"] = kind
        return await self._call("search_food", arguments, SearchResult)

    async def nutrition(self, food_id: str, amount: float, unit: str) -> NutritionResult:
        arguments = {"food_id": food_id, "amount": amount, "unit": unit}
        return await self._call("get_nutrition", arguments, NutritionResult, food_id)

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        arguments = {"items": [item.model_dump() for item in items]}
        return await self._call("compute_meal", arguments, MealResult)

    async def _call[T: BaseModel](
        self, tool: str, arguments: dict[str, Any], result: type[T], food_id: str | None = None
    ) -> T:
        """The tool's result as `result`, or a NutritionError. The arguments are never in its
        message, because they can hold what the user typed."""
        not_read = f"nutrition-mcp's result for {tool} could not be read"
        try:
            answer = await self._session.call_tool(tool, arguments)
        except (MCPError, OSError, TimeoutError) as exc:
            # A request that timed out, or a connection that closed: the SDK raises these instead
            # of returning a result. Cancellation is a BaseException and passes through.
            raise NutritionError(
                f"nutrition-mcp could not answer {tool}: {_detail(exc)}", unreachable=True
            ) from exc
        except (ValidationError, RuntimeError) as exc:
            # The SDK checks a result against the protocol (ValidationError) and against the
            # tool's output schema (RuntimeError). Its message can quote the result, which can
            # echo the arguments, so it stays on the cause.
            raise NutritionError(not_read, unreadable=True) from exc
        if answer.is_error:
            raise refusal(_message(answer), food_id)
        content = answer.structured_content
        if not isinstance(content, dict):
            raise NutritionError(
                f"nutrition-mcp sent no structured content for {tool}", unreadable=True
            )
        try:
            return result.model_validate(content)
        except ValidationError as exc:
            raise NutritionError(not_read, unreadable=True) from exc


def _detail(exc: Exception) -> str:
    """What went wrong, on one line. str() of an MCPError is the repr of its arguments."""
    text = exc.message if isinstance(exc, MCPError) else str(exc)
    return " ".join(text.split()) or type(exc).__name__
