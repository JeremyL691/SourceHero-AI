"""Job API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class JobOut(BaseModel):
    id: int
    user_id: uuid.UUID | None = None
    kind: str
    payload: dict = {}
    status: str
    priority: int
    run_after: datetime
    attempts: int
    max_attempts: int
    locked_by: str | None = None
    last_error: str | None = None
    result: dict | None = None
    dedupe_key: str | None = None
    parent_job_id: int | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class JobActionOut(BaseModel):
    job_id: int
    status: str
    cancelled: bool | None = None


__all__ = ["JobActionOut", "JobOut"]
