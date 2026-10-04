"""Job handlers.

Every kind the pipeline can enqueue is registered here. Milestones replace the
placeholder handlers with real implementations; the placeholders are honest about
being placeholders so a half-finished pipeline cannot look finished.
"""

from __future__ import annotations

from app.jobs.handlers import core  # noqa: F401 - registers maintenance
from app.jobs.handlers import schedules as schedule_handlers  # noqa: F401


def register_all() -> None:
    """Import every handler module for its registration side effects."""
    import importlib

    for module in (
        "app.jobs.handlers.core",
        "app.jobs.handlers.schedules",
    ):
        importlib.import_module(module)


__all__ = ["register_all"]
