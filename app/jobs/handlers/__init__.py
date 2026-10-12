"""Job handlers.

Every kind the pipeline can enqueue is registered here.
"""

from __future__ import annotations

_MODULES = (
    "app.jobs.handlers.core",
    "app.jobs.handlers.schedules",
    "app.jobs.handlers.ingestion",
    "app.jobs.handlers.temporal",
    "app.jobs.handlers.wiki",
)


def register_all() -> None:
    """Import every handler module for its registration side effects.

    Called by the worker at start-up and by the demo seed's drain loop. Registration
    is idempotent: re-importing a module is a no-op.
    """
    import importlib

    for module in _MODULES:
        importlib.import_module(module)


__all__ = ["register_all"]
