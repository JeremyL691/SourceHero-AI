"""Prometheus metrics and the `/metrics` endpoint.

Request and job instruments are updated where the work happens (the middleware and the
worker). Queue and LLM figures are read from Postgres at scrape time by collectors, so
they stay correct across processes without any cross-process bookkeeping.

`/metrics` is outside `/v1` and answers 404 unless `METRICS_TOKEN` is set.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from sqlalchemy import func, select

from app.core import time as clock
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models.job import Job
from app.models.llm import LLMRun

log = get_logger(__name__)

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by method, route template and status.",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by route template.",
    ["route"],
)
JOB_DURATION = Histogram(
    "job_duration_seconds",
    "Wall time of one job attempt, by kind and outcome.",
    ["kind", "status"],
)


class JobsQueueCollector:
    """`jobs_queue{state}` for all users, with the same definitions as /v1/jobs/summary."""

    def collect(self):
        family = GaugeMetricFamily("jobs_queue", "Jobs by state across all users.", labels=["state"])
        try:
            db = SessionLocal()
            try:
                now = clock.utcnow()
                row = db.execute(
                    select(
                        func.count().filter(Job.status == "queued", Job.run_after <= now),
                        func.count().filter(Job.status == "running"),
                        func.count().filter(Job.status == "queued", Job.run_after > now),
                    ).select_from(Job)
                ).one()
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - a scrape must not fail because the database did
            log.warning("metrics: jobs_queue unavailable", exc_info=True)
            return
        family.add_metric(["queued"], row[0] or 0)
        family.add_metric(["running"], row[1] or 0)
        family.add_metric(["deferred"], row[2] or 0)
        yield family


class LLMUsageCollector:
    """`llm_calls_total{provider,status}` and `llm_tokens_total{provider,kind}` from llm_calls."""

    def collect(self):
        try:
            db = SessionLocal()
            try:
                rows = db.execute(
                    select(
                        LLMRun.provider,
                        LLMRun.status,
                        func.count(),
                        func.coalesce(func.sum(LLMRun.prompt_tokens), 0),
                        func.coalesce(func.sum(LLMRun.completion_tokens), 0),
                    ).group_by(LLMRun.provider, LLMRun.status)
                ).all()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            log.warning("metrics: llm usage unavailable", exc_info=True)
            return

        calls = CounterMetricFamily(
            "llm_calls", "LLM calls recorded in llm_calls.", labels=["provider", "status"]
        )
        tokens = CounterMetricFamily(
            "llm_tokens", "LLM tokens recorded in llm_calls.", labels=["provider", "kind"]
        )
        by_provider: dict[str, list[int]] = {}
        for provider, status, count, prompt, completion in rows:
            calls.add_metric([provider, status], count)
            totals = by_provider.setdefault(provider, [0, 0])
            totals[0] += int(prompt)
            totals[1] += int(completion)
        for provider, (prompt, completion) in by_provider.items():
            tokens.add_metric([provider, "prompt"], prompt)
            tokens.add_metric([provider, "completion"], completion)
        yield calls
        yield tokens


REGISTRY.register(JobsQueueCollector())
REGISTRY.register(LLMUsageCollector())

metrics_router = APIRouter()


@metrics_router.get("/metrics", tags=["observability"], summary="Prometheus metrics (bearer token)")
def metrics(request: Request) -> Response:
    token = get_settings().metrics_token
    if not token:
        raise HTTPException(status_code=404, detail="Not Found")
    scheme, _, supplied = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip().encode(), token.encode()):
        raise HTTPException(status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


__all__ = [
    "HTTP_DURATION",
    "HTTP_REQUESTS",
    "JOB_DURATION",
    "JobsQueueCollector",
    "LLMUsageCollector",
    "metrics_router",
]
