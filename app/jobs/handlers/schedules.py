"""Re-export of the schedule handlers so `register_all` has one import per module."""

from __future__ import annotations

from app.ingestion.pipeline import ingest_source_job as refresh_source_job

__all__ = ["refresh_source_job"]
