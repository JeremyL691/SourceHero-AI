"""Schedule API schemas. A schedule fires daily, or weekly when `day_of_week` is set."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ScheduleIn(BaseModel):
    job_type: str = Field(..., max_length=64)
    name: str = Field("", max_length=200)
    config: dict = {}
    day_of_week: int | None = Field(None, ge=0, le=6, description="0 = Monday; omit for daily")
    hour: int = Field(0, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)


class SchedulePatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    config: dict | None = None
    enabled: bool | None = None
    day_of_week: int | None = Field(None, ge=0, le=6)
    hour: int | None = Field(None, ge=0, le=23)
    minute: int | None = Field(None, ge=0, le=59)


class ScheduleOut(BaseModel):
    id: int
    name: str
    job_type: str
    config: dict = {}
    enabled: bool
    cron_day_of_week: int | None = None
    cron_hour: int
    cron_minute: int
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


__all__ = ["ScheduleIn", "ScheduleOut", "SchedulePatch"]
