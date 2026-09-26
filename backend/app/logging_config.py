"""Structured logging: one JSON object per line, on stdout, never in a file.

A container's filesystem is ephemeral and the log shipper reads stdout, so stdout is the only
sink. Every line carries the request_id of the request being served (null outside a request).
"""

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.config import get_settings

# Set by the request middleware. A ContextVar (not a global) so concurrent requests never see
# each other's id; Starlette copies the context into its threadpool, so sync handlers see it too.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

DEFAULT_LEVEL = "INFO"

# Fields the formatter owns. An `extra=` key with one of these names is dropped, so a caller
# (or an attacker-influenced value) cannot forge the request_id or the message.
_RESERVED = frozenset(
    {"timestamp", "level", "logger", "message", "request_id", "exception", "stack"}
)

# Everything a plain LogRecord carries; whatever else is on a record came from `extra=`.
# color_message is uvicorn's ANSI-coloured duplicate of the message.
_STANDARD_ATTRIBUTES = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES and key not in _RESERVED:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        # default=str: a log call must never fail because an extra value is not serialisable.
        return json.dumps(entry, default=str)


class JsonStdoutHandler(logging.Handler):
    """Writes to whatever sys.stdout is at emit time, not at configure time, so it keeps
    working when a test runner or a supervisor swaps the stream."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = sys.stdout
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)


def resolve_log_level() -> str:
    """LOG_LEVEL from settings, or from the environment when settings cannot be built (for
    example a missing DATABASE_URL). Logging must not be the thing that stops the app booting."""
    try:
        return get_settings().log_level
    except ValidationError:
        return os.environ.get("LOG_LEVEL", DEFAULT_LEVEL)


def configure_logging(level: str = DEFAULT_LEVEL) -> None:
    """Send all logging, uvicorn's included, through one JSON handler on stdout.

    Idempotent: calling it again only updates the level.
    """
    normalised = level.upper()
    valid = normalised in logging.getLevelNamesMapping()
    root = logging.getLogger()
    # Other handlers on the root logger (pytest's caplog, for one) are deliberately left alone.
    if not any(isinstance(handler, JsonStdoutHandler) for handler in root.handlers):
        handler = JsonStdoutHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(normalised if valid else DEFAULT_LEVEL)

    # uvicorn installs its own plain-text handlers and stops propagation. Hand its loggers to the
    # root handler instead, and let them inherit the level, so LOG_LEVEL is the single knob.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(logging.NOTSET)

    if not valid:
        logging.getLogger(__name__).warning(
            "unknown LOG_LEVEL, using %s", DEFAULT_LEVEL, extra={"requested_level": level}
        )
