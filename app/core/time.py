"""The single source of "now".

Business code never calls `datetime.now()` directly. Import the *module*, not the
function:

    from app.core import time as clock
    now = clock.utcnow()

Importing `utcnow` directly would bind it at import time, and the test suite's
`freeze_time` fixture monkeypatches `app.core.time.utcnow` -- the already-bound name
would keep pointing at the real clock.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["utcnow"]
