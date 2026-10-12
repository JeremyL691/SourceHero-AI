"""K5.3: the schedules API -- validation, run history after a tick, and tenant isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.routing import Mount

from app.api.routers.schedules import router as schedules_router
from app.core.db import get_db
from app.ingestion.sources import create_source
from app.jobs.scheduler import tick
from app.main import API_PREFIX, create_app
from app.models.job import ScheduledJob
from app.models.user import User

pytestmark = pytest.mark.db

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
PASSWORD = "test-password-123"


@pytest.fixture(autouse=True)
def _manual_queue(no_inline_jobs):
    """Ticks enqueue; nothing may execute the handlers inline."""


def _app(db: Session) -> FastAPI:
    """`create_app()` with the schedules router served, bound to `db`.

    Mounted here only if `create_app` does not already serve it, so this file works
    before and after the router is wired into app/main.py. The MCP mount at "/" is
    the last route `create_app` adds and would shadow a late route, so the new one
    is moved in front of it.
    """
    app = create_app()
    if f"{API_PREFIX}/schedules" not in app.openapi()["paths"]:
        app.include_router(schedules_router, prefix=API_PREFIX)
        routes = app.router.routes
        mount_at = next(i for i, r in enumerate(routes) if isinstance(r, Mount))
        routes.insert(mount_at, routes.pop())
    app.dependency_overrides[get_db] = lambda: db
    return app


def _user_client(db: Session, email: str) -> tuple[TestClient, uuid.UUID]:
    """An authenticated client bound to `db`."""
    app = _app(db)
    client = TestClient(app)
    resp = client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    client.headers.update({"Authorization": f"Bearer {resp.json()['access_token']}"})
    row = db.get(User, uuid.UUID(resp.json()["user"]["id"]))
    if row is not None and row.is_admin:
        row.is_admin = False  # the first account in an empty schema is auto-admin
        db.flush()
    return client, uuid.UUID(resp.json()["user"]["id"])


@pytest.fixture
def owner(db):
    return _user_client(db, f"sched{uuid.uuid4().hex[:10]}@test.local")


@pytest.fixture
def stranger(db):
    return _user_client(db, f"other{uuid.uuid4().hex[:10]}@test.local")


def _create(client: TestClient, **overrides) -> dict:
    body = {"job_type": "plan_topics", "name": "nightly", "hour": 3, "minute": 30, **overrides}
    resp = client.post(f"{API_PREFIX}/schedules", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- create / list ---------------------------------------------------------


def test_create_returns_schedule_with_next_run_at(owner, db):
    client, _ = owner
    body = _create(client, job_type="refresh_source", config={"note": "x"}, day_of_week=0)

    assert body["job_type"] == "refresh_source"
    assert body["name"] == "nightly"
    assert body["config"] == {"note": "x"}
    assert body["enabled"] is True
    assert body["cron_day_of_week"] == 0
    assert (body["cron_hour"], body["cron_minute"]) == (3, 30)
    assert body["next_run_at"] is not None
    next_run = datetime.fromisoformat(body["next_run_at"])
    assert next_run > datetime.now(UTC)
    assert (next_run.hour, next_run.minute, next_run.weekday()) == (3, 30, 0)

    listed = client.get(f"{API_PREFIX}/schedules").json()
    assert [s["id"] for s in listed] == [body["id"]]


def test_unknown_job_type_is_422(owner):
    client, _ = owner
    resp = client.post(f"{API_PREFIX}/schedules", json={"job_type": "rm -rf /"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "bad_job_type"


@pytest.mark.parametrize(
    "bad",
    [
        {"hour": 24},
        {"hour": -1},
        {"minute": 60},
        {"day_of_week": 7},
        {"day_of_week": -1},
    ],
)
def test_invalid_cron_fields_are_422(owner, bad):
    client, _ = owner
    resp = client.post(f"{API_PREFIX}/schedules", json={"job_type": "plan_topics", **bad})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- source_id ownership -----------------------------------------------------


def test_source_id_must_belong_to_the_caller(owner, stranger, db):
    client, _ = owner
    _, stranger_id = stranger
    theirs = create_source(
        db, stranger_id, source_type="webpage", name="Theirs", url="https://example.test/t"
    )
    mine = create_source(db, owner[1], source_type="webpage", name="Mine", url="https://example.test/m")

    denied = client.post(
        f"{API_PREFIX}/schedules",
        json={"job_type": "refresh_source", "config": {"source_id": str(theirs.id)}},
    )
    assert denied.status_code == 404
    assert client.get(f"{API_PREFIX}/schedules").json() == []

    allowed = client.post(
        f"{API_PREFIX}/schedules",
        json={"job_type": "refresh_source", "config": {"source_id": str(mine.id)}},
    )
    assert allowed.status_code == 201
    assert allowed.json()["config"] == {"source_id": str(mine.id)}


def test_malformed_source_id_is_422(owner):
    client, _ = owner
    resp = client.post(
        f"{API_PREFIX}/schedules",
        json={"job_type": "refresh_source", "config": {"source_id": "not-a-uuid"}},
    )
    assert resp.status_code == 422


# --- update / delete -------------------------------------------------------


def test_patch_changes_time_and_pauses_then_resumes(owner):
    client, _ = owner
    created = _create(client)

    paused = client.patch(f"{API_PREFIX}/schedules/{created['id']}", json={"enabled": False})
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False
    assert paused.json()["next_run_at"] is None

    moved = client.patch(
        f"{API_PREFIX}/schedules/{created['id']}", json={"enabled": True, "hour": 5, "minute": 0}
    )
    assert moved.status_code == 200
    body = moved.json()
    assert (body["cron_hour"], body["cron_minute"]) == (5, 0)
    assert body["next_run_at"] is not None
    assert datetime.fromisoformat(body["next_run_at"]).hour == 5


def test_patch_with_foreign_source_is_404(owner, stranger, db):
    client, _ = owner
    created = _create(client, job_type="refresh_source")
    _, stranger_id = stranger
    theirs = create_source(db, stranger_id, source_type="webpage", name="T", url="https://example.test/x")

    resp = client.patch(
        f"{API_PREFIX}/schedules/{created['id']}",
        json={"config": {"source_id": str(theirs.id)}},
    )
    assert resp.status_code == 404
    assert client.get(f"{API_PREFIX}/schedules").json()[0]["config"] == {}


def test_delete_removes_the_schedule(owner):
    client, _ = owner
    created = _create(client)

    assert client.delete(f"{API_PREFIX}/schedules/{created['id']}").status_code == 204
    assert client.get(f"{API_PREFIX}/schedules").json() == []
    assert client.delete(f"{API_PREFIX}/schedules/{created['id']}").status_code == 404


# --- runs and the scheduler tick ---------------------------------------------


def test_one_tick_adds_exactly_one_run(owner, db):
    client, _ = owner
    created = _create(client, job_type="belief_diff")
    assert client.get(f"{API_PREFIX}/schedules/{created['id']}/runs").json() == []

    job = db.get(ScheduledJob, created["id"])
    job.next_run_at = T0 - timedelta(hours=1)  # make it due at the frozen tick time
    db.commit()

    counts = tick(db, now=T0)
    assert counts["scheduled_jobs"] == 1

    runs = client.get(f"{API_PREFIX}/schedules/{created['id']}/runs").json()
    assert len(runs) == 1
    assert runs[0]["kind"] == "belief_diff"
    assert runs[0]["status"] == "queued"
    assert runs[0]["dedupe_key"].startswith(f"sched:{created['id']}:")


def test_runs_only_list_this_schedules_jobs(owner, db):
    client, _ = owner
    first = _create(client, job_type="belief_diff")
    second = _create(client, job_type="plan_topics")
    for sched in (first, second):
        job = db.get(ScheduledJob, sched["id"])
        job.next_run_at = T0 - timedelta(hours=1)
    db.commit()
    tick(db, now=T0)

    runs = client.get(f"{API_PREFIX}/schedules/{first['id']}/runs").json()
    assert [r["kind"] for r in runs] == ["belief_diff"]


# --- tenant isolation ------------------------------------------------------


def test_another_users_schedule_is_404_on_every_route(owner, stranger, db):
    client, _ = owner
    other, _ = stranger
    theirs = _create(other)
    url = f"{API_PREFIX}/schedules/{theirs['id']}"

    assert client.patch(url, json={"enabled": False}).status_code == 404
    assert client.delete(url).status_code == 404
    assert client.get(f"{url}/runs").status_code == 404

    # The failed attempts changed nothing, and the owner still sees it.
    assert [s["id"] for s in other.get(f"{API_PREFIX}/schedules").json()] == [theirs["id"]]
    assert other.get(f"{API_PREFIX}/schedules").json()[0]["enabled"] is True
    assert client.get(f"{API_PREFIX}/schedules").json() == []


def test_unauthenticated_requests_are_rejected(db):
    anon = TestClient(_app(db))
    assert anon.get(f"{API_PREFIX}/schedules").status_code == 401
    assert anon.post(f"{API_PREFIX}/schedules", json={"job_type": "plan_topics"}).status_code == 401
