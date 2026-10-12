"""Logging setup shared by the API, the worker and scripts.

Every record passes through `ContextFilter`, which stamps `request_id`, `job_id` and
`user` from contextvars, so a log line can be tied back to the request or job that
produced it. `log_format=json` renders one JSON object per line; `text` is for humans.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_CONFIGURED = False

TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Set by the request middleware and the worker; read by `ContextFilter`.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_var: ContextVar[int | None] = ContextVar("job_id", default=None)
# Hashed user id for job context (requests derive theirs from `request_state_var`).
user_var: ContextVar[str | None] = ContextVar("user", default=None)
# The `request.state` mapping of the current HTTP request; the auth dependency writes
# `user_id` into it once the caller is known.
request_state_var: ContextVar[dict[str, Any] | None] = ContextVar("request_state", default=None)

# Extra attributes passed by the access log and copied into JSON lines.
_EXTRA_KEYS = ("method", "route", "status", "duration_ms")


def hash_user_id(raw: str) -> str:
    """The only form of a user id that may appear in logs or metrics."""
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _current_user() -> str | None:
    state = request_state_var.get()
    if state and state.get("user_id"):
        return hash_user_id(str(state["user_id"]))
    return user_var.get()


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.job_id = job_id_var.get()
        record.user = _current_user()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "user": getattr(record, "user", None),
            "job_id": getattr(record, "job_id", None),
            "exc": self.formatException(record.exc_info) if record.exc_info else None,
        }
        for key in _EXTRA_KEYS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str, ensure_ascii=False)


def make_handler(log_format: str) -> logging.Handler:
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.addFilter(ContextFilter())
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT, "%H:%M:%S"))
    return handler


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    from app.core.config import get_settings

    root = logging.getLogger()
    root.handlers = [make_handler(get_settings().log_format)]
    root.setLevel(level)
    # These two are chatty at INFO and carry no information we need.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


__all__ = [
    "ContextFilter",
    "JsonFormatter",
    "configure_logging",
    "get_logger",
    "hash_user_id",
    "job_id_var",
    "make_handler",
    "request_id_var",
    "request_state_var",
    "user_var",
]
