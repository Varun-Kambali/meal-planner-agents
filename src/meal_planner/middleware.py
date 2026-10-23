"""What every request passes through before a route sees it: an id, a size limit, a rate limit.

These are plain ASGI middleware, not Starlette's BaseHTTPMiddleware: that one runs the app in
another task and buffers around streams, and the week is a stream.
"""

import math
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping

from fastapi import HTTPException
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from meal_planner.log import get_logger, request_id_var

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContext:
    """Gives each request an id, in the log context and the response, and catches what escapes.

    An error nobody handled becomes a 500 that says nothing about it. Only the exception's class
    is logged: its message can hold what the user sent, and the server's own traceback would
    print it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            logger.error("request_failed", extra={"error_type": type(exc).__name__})
            if not started:
                response = JSONResponse({"detail": "internal error"}, status_code=500)
                await response(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


class BodyLimit:
    """Refuses a body over the limit with a 413, counting the bytes that actually arrive.

    A length header over the limit is refused without reading anything. A header under it, or
    none at all (a chunked body), is no promise, so the bytes are counted as the route reads them.
    `per_path` gives a route its own limit.
    """

    def __init__(self, app: ASGIApp, *, limit: int, per_path: Mapping[str, int] | None = None):
        self.app = app
        self.limit = limit
        self.per_path = per_path or {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.per_path.get(scope["path"], self.limit)
        detail = f"the request body is over {limit} bytes"
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            await JSONResponse({"detail": detail}, status_code=413)(scope, receive, send)
            return

        read = 0

        async def counting_receive() -> Message:
            nonlocal read
            message = await receive()
            if message["type"] == "http.request":
                read += len(message.get("body", b""))
                if read > limit:
                    # FastAPI lets an HTTPException from the body read through; any other error
                    # there becomes a 400.
                    raise HTTPException(status_code=413, detail=detail)
            return message

        await self.app(scope, counting_receive, send)


class RateLimiter:
    """Allows each client `limit` requests in any `window_s` seconds, counted in memory.

    The count is per process and is lost on a restart, which is enough for one small server.
    """

    def __init__(
        self,
        limit: int,
        *,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._swept = clock()

    def check(self, client: str) -> int | None:
        """None if the request is allowed and counted, else the whole seconds to wait."""
        now = self._clock()
        self._forget_quiet_clients(now)
        hits = self._hits.setdefault(client, deque())
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            # A refused request is not counted, so asking again does not push the time back.
            return max(1, math.ceil(hits[0] + self._window - now))
        hits.append(now)
        return None

    def tracked(self) -> int:
        return len(self._hits)

    def _forget_quiet_clients(self, now: float) -> None:
        """Drop clients with nothing in the window, at most once a window."""
        if now - self._swept < self._window:
            return
        self._swept = now
        quiet = [c for c, hits in self._hits.items() if not hits or hits[-1] <= now - self._window]
        for client in quiet:
            del self._hits[client]


class RateLimit:
    """Answers 429 with Retry-After to a client over its allowance on the paths under `prefix`."""

    def __init__(self, app: ASGIApp, *, limiter: RateLimiter, prefix: str) -> None:
        self.app = app
        self.limiter = limiter
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        wait = self.limiter.check(client[0] if client else "unknown")
        if wait is not None:
            response = JSONResponse(
                {"detail": "too many requests, try again later"},
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
