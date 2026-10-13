"""Prometheus instruments, scrape-time collectors and the bearer-protected /metrics route."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.core import metrics as metrics_mod
from app.core import time as clock
from app.core.config import get_settings
from app.core.metrics import JobsQueueCollector, LLMUsageCollector, metrics_router
from app.core.middleware import RequestContextMiddleware
from app.jobs import queue as q
from app.jobs import worker as worker_mod
from app.jobs.registry import job_handler
from app.models.llm import LLMRun

TOKEN = "scrape-token-for-tests"


def _metrics_client() -> TestClient:
    app = FastAPI()
    app.include_router(metrics_router)
    return TestClient(app)


def _ping_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    return TestClient(app)


def _family_samples(collector, sample_name: str) -> list:
    return [s for fam in collector.collect() for s in fam.samples if s.name == sample_name]


# --- E. /metrics route ------------------------------------------------------------


def test_metrics_is_404_when_no_token_is_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "")

    response = _metrics_client().get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 404


def test_metrics_is_401_without_or_with_wrong_bearer_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", TOKEN)
    client = _metrics_client()

    missing = client.get("/metrics")
    wrong = client.get("/metrics", headers={"Authorization": "Bearer not-the-token"})
    wrong_scheme = client.get("/metrics", headers={"Authorization": f"Basic {TOKEN}"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert wrong_scheme.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"


def test_metrics_is_200_with_prometheus_text_for_correct_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", TOKEN)

    response = _metrics_client().get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "# TYPE http_requests_total counter" in body
    assert "# TYPE http_request_duration_seconds histogram" in body
    assert "# TYPE jobs_queue gauge" in body
    assert "# TYPE job_duration_seconds histogram" in body


def test_metrics_route_is_outside_v1():
    paths = {getattr(r, "path", None) for r in metrics_router.routes}

    assert paths == {"/metrics"}


# --- D. Request instruments fed by the middleware ----------------------------------


def test_http_counter_and_duration_use_route_template_and_increment_per_request():
    labels = {"method": "GET", "route": "/ping", "status": "200"}
    before = REGISTRY.get_sample_value("http_requests_total", labels) or 0
    before_duration = (
        REGISTRY.get_sample_value("http_request_duration_seconds_count", {"route": "/ping"}) or 0
    )

    client = _ping_client()
    client.get("/ping")
    client.get("/ping")

    assert REGISTRY.get_sample_value("http_requests_total", labels) == before + 2
    assert (
        REGISTRY.get_sample_value("http_request_duration_seconds_count", {"route": "/ping"})
        == before_duration + 2
    )


def test_unmatched_requests_share_one_bounded_label():
    labels = {"method": "GET", "route": "unmatched", "status": "404"}
    before = REGISTRY.get_sample_value("http_requests_total", labels) or 0

    _ping_client().get("/definitely/not/a/route/12345")

    assert REGISTRY.get_sample_value("http_requests_total", labels) == before + 1


# --- D. Job duration observed by the worker ----------------------------------------


@job_handler("test_metrics_job_kind")
def _metrics_job(ctx, payload: dict) -> dict:
    return {}


def test_job_duration_is_observed_with_kind_and_status(fresh_db, no_inline_jobs):
    labels = {"kind": "test_metrics_job_kind", "status": "succeeded"}
    before = REGISTRY.get_sample_value("job_duration_seconds_count", labels) or 0

    q.enqueue(fresh_db, "test_metrics_job_kind", {})
    fresh_db.commit()
    assert worker_mod._run_one(fresh_db, "w-metrics", drain=True, kinds={"test_metrics_job_kind"}) is True

    assert REGISTRY.get_sample_value("job_duration_seconds_count", labels) == before + 1


# --- D. Scrape-time collectors read Postgres ---------------------------------------


def test_jobs_queue_gauge_reports_due_and_deferred_counts(fresh_db, no_inline_jobs):
    before = {s.labels["state"]: s.value for s in _family_samples(JobsQueueCollector(), "jobs_queue")}

    q.enqueue(fresh_db, "test_metrics_job_kind", {})
    q.enqueue(fresh_db, "test_metrics_job_kind", {}, run_after=clock.utcnow() + timedelta(hours=1))
    fresh_db.commit()

    after = {s.labels["state"]: s.value for s in _family_samples(JobsQueueCollector(), "jobs_queue")}
    assert after["queued"] == before.get("queued", 0) + 1
    assert after["deferred"] == before.get("deferred", 0) + 1
    assert set(after) == {"queued", "running", "deferred"}


def test_llm_calls_and_tokens_are_read_from_llm_calls(fresh_db):
    provider = "prov-metrics-test"
    fresh_db.add_all(
        [
            LLMRun(task="t", provider=provider, model="m", prompt_tokens=7, completion_tokens=3, status="ok"),
            LLMRun(
                task="t", provider=provider, model="m", prompt_tokens=1, completion_tokens=0, status="error"
            ),
        ]
    )
    fresh_db.commit()

    collector = LLMUsageCollector()
    calls = {
        (s.labels["provider"], s.labels["status"]): s.value
        for s in _family_samples(collector, "llm_calls_total")
    }
    tokens = {
        (s.labels["provider"], s.labels["kind"]): s.value
        for s in _family_samples(collector, "llm_tokens_total")
    }
    assert calls[(provider, "ok")] == 1
    assert calls[(provider, "error")] == 1
    assert tokens[(provider, "prompt")] == 8
    assert tokens[(provider, "completion")] == 3


@pytest.mark.parametrize("collector_cls", [JobsQueueCollector, LLMUsageCollector])
def test_collectors_do_not_fail_the_scrape_when_database_is_down(monkeypatch, collector_cls):
    def broken_session():
        raise RuntimeError("database is down")

    monkeypatch.setattr(metrics_mod, "SessionLocal", broken_session)

    assert list(collector_cls().collect()) == []
