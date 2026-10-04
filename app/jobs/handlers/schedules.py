"""Re-export of the schedule handlers so `register_all` has one import per module."""

from __future__ import annotations

from app.jobs.schedules import refresh_source_job  # noqa: F401

__all__ = ["refresh_source_job"]
