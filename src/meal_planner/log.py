"""Logging: one JSON object per line on stderr.

Logs never hold what the user typed or the rules they set, so a line carries only the fields
named in FIELDS. Anything else passed in `extra` is dropped by the formatter, which makes a
leak a change to this file rather than a slip at a call site. The message itself is written by
our own code and has to stay free of user text too.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# The API sets this once per request, so every line of one request shares an id.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

FIELDS = (
    "task",
    "model",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "cost_usd",
    "error",
    "error_type",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        entry.update({name: getattr(record, name) for name in FIELDS if hasattr(record, name)})
        return json.dumps(entry)


class _StderrHandler(logging.Handler):
    """Writes to whatever sys.stderr is at the time, so a test's capture sees the line."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(handler, _StderrHandler) for handler in logger.handlers):
        handler = _StderrHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # The handler above is the only place these lines go; a parent's would print them twice.
        logger.propagate = False
    return logger
