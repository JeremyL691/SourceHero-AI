"""Fixed-window rate limits, counted in Postgres so every API process shares them.

Off unless `rate_limit_enabled` is set. Callers pass the limit for their route, and the
key identifies the caller (`"<route>:<user id>"` or, for login, the email or client host).

ponytail: one upsert per request, no Redis. Ceiling: a hot key is one row lock; move to
Redis if that shows up in profiles.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.config import get_settings
from app.core.errors import BudgetExceededError
from app.models.ratelimit import RateLimit


class RateLimited(BudgetExceededError):
    """429 with `code="rate_limited"`. `retry_after` is seconds until the window ends."""

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message, code="rate_limited", details={"retry_after": retry_after})
        self.retry_after = retry_after
        self.headers = {"Retry-After": str(retry_after)}


def window_start(now: datetime, window_seconds: int) -> datetime:
    ts = int(now.timestamp())
    return datetime.fromtimestamp(ts - ts % window_seconds, UTC)


def enforce(db: Session, key: str, limit: int, now: datetime | None = None) -> None:
    """Count one call against `key`; raise `RateLimited` once the window holds more than `limit`."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    now = now or clock.utcnow()
    window = settings.rate_limit_window_seconds
    start = window_start(now, window)

    table = RateLimit.__table__
    stmt = (
        pg_insert(table)
        .values(key=key, window_start=start, count=1)
        .on_conflict_do_update(
            index_elements=["key", "window_start"],
            set_={"count": table.c.count + 1},
        )
        .returning(table.c.count)
    )
    count = db.execute(stmt).scalar_one()
    # Commit now: the count must survive when the request itself then fails or is rolled back.
    db.commit()

    if count > limit:
        seconds_left = (start + timedelta(seconds=window) - now).total_seconds()
        raise RateLimited("too many requests; try again later", retry_after=max(1, math.ceil(seconds_left)))


__all__ = ["RateLimited", "enforce", "window_start"]
