"""The single source of "now".

Business code never calls `datetime.now()` directly; it calls `utcnow()`. Tests freeze
it by monkeypatching `app.core.time.utcnow`.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["utcnow"]