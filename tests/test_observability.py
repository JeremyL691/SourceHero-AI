"""K7.1: JSON logs, request context, job context and the worker healthcheck."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.core import logging as obs_logging
from app.core.config import get_settings
from app.core.logging import (
    ContextFilter,
    JsonFormatter,
    configure_logging,
    job_id_var,
    make_handler,
    request_id_var,
)
from app.core.middleware import RequestContextMiddleware
from app.jobs import queue as q
from app.jobs import worker as worker_mod
from app.jobs.registry import job_handler

RAW_USER = "7b2f4c1e-0000-4000-8000-000000000001"


@contextmanager
def _captured(logger_name: str, log_format: str = "json") -> Iterator[io.StringIO]:
    """Send one logger's records through the real handler factory into a buffer."""
    logger = logging.getLogger(logger_name)
    stream = io.StringIO()
    handler = make_handler(log_format)
    handler.setStream(stream)
    old_level, old_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield stream
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _app(user_id: str | None = None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    async def ping() -> dict:
        return {"request_id": request_id_var.get()}

    @app.get("/items/{item_id}")
    async def item(item_id: int, request: Request) -> dict:
        if user_id:  # stands in for the auth dependency, which writes request.state.user_id
            request.state.user_id = user_id
        return {"request_id": request_id_var.get()}

    return app


# --- A. JSON formatter and filter -------------------------------------------------


def test_json_line_parses_and_carries_request_id():
    token = request_id_var.set("req-json-1")
    try:
        with _captured("tests.obs.json") as stream:
            logging.getLogger("tests.obs.json").info("hello %s", "world")
    finally:
        request_id_var.reset(token)

    (rec,) = _lines(stream)
    assert rec["msg"] == "hello world"
    assert rec["level"] == "INFO"
    assert rec["logger"] == "tests.obs.json"
    assert rec["request_id"] == "req-json-1"
    assert rec["user"] is None
    assert rec["job_id"] is None
    assert rec["exc"] is None
    assert datetime.fromisoformat(rec["ts"]).utcoffset() is not None


def test_json_line_carries_exception_text():
    with _captured("tests.obs.exc") as stream:
        try:
            raise ValueError("kaboom")
        except ValueError:
            logging.getLogger("tests.obs.exc").exception("failed")

    (rec,) = _lines(stream)
    assert rec["level"] == "ERROR"
    assert "ValueError: kaboom" in rec["exc"]


def test_text_format_is_not_json():
    with _captured("tests.obs.text", log_format="text") as stream:
        logging.getLogger("tests.obs.text").info("plain line")

    line = stream.getvalue().strip()
    assert "plain line" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_configure_logging_uses_json_when_configured(monkeypatch):
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    monkeypatch.setattr(get_settings(), "log_format", "json")
    monkeypatch.setattr(obs_logging, "_CONFIGURED", False)  # monkeypatch restores the flag
    try:
        configure_logging("INFO")
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert any(isinstance(f, ContextFilter) for f in root.handlers[0].filters)
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


# --- B. Request context middleware -------------------------------------------------


def test_valid_request_id_is_echoed_and_visible_to_handler():
    response = TestClient(_app()).get("/ping", headers={"X-Request-ID": "abc-123.DEF_~"})

    assert response.headers["X-Request-ID"] == "abc-123.DEF_~"
    assert response.json()["request_id"] == "abc-123.DEF_~"


def test_missing_request_id_is_generated_as_uuid():
    response = TestClient(_app()).get("/ping")

    generated = response.headers["X-Request-ID"]
    assert uuid.UUID(generated).version == 4
    assert response.json()["request_id"] == generated


@pytest.mark.parametrize("bad", ["x" * 129, "has space", "tab\there"])
def test_unacceptable_request_id_is_replaced(bad):
    response = TestClient(_app()).get("/ping", headers={"X-Request-ID": bad})

    generated = response.headers["X-Request-ID"]
    assert generated != bad
    assert uuid.UUID(generated).version == 4


def test_request_id_of_exactly_128_characters_is_kept():
    value = "r" * 128
    response = TestClient(_app()).get("/ping", headers={"X-Request-ID": value})

    assert response.headers["X-Request-ID"] == value


def test_access_log_is_one_json_line_with_template_status_duration_and_hashed_user():
    with _captured("app.access") as stream:
        response = TestClient(_app(user_id=RAW_USER)).get("/items/42", headers={"X-Request-ID": "req-log-1"})

    (raw_line,) = stream.getvalue().splitlines()
    rec = json.loads(raw_line)
    assert rec["logger"] == "app.access"
    assert rec["method"] == "GET"
    assert rec["route"] == "/items/{item_id}"
    assert rec["status"] == 200
    assert rec["duration_ms"] >= 0
    assert rec["request_id"] == "req-log-1" == response.headers["X-Request-ID"]
    assert rec["user"] == hashlib.sha256(RAW_USER.encode()).hexdigest()[:12]
    # Neither the raw user id nor the raw path may reach the log.
    assert RAW_USER not in raw_line
    assert "/items/42" not in raw_line


def test_access_log_has_no_user_for_anonymous_request():
    with _captured("app.access") as stream:
        TestClient(_app()).get("/ping")

    (rec,) = _lines(stream)
    assert rec["user"] is None
    assert rec["route"] == "/ping"


def test_unmatched_route_is_labelled_unmatched_without_raw_path():
    with _captured("app.access") as stream:
        response = TestClient(_app()).get("/no/such/path/secret-123")

    raw_line = stream.getvalue().strip()
    rec = json.loads(raw_line)
    assert response.status_code == 404
    assert rec["route"] == "unmatched"
    assert rec["status"] == 404
    assert "secret-123" not in raw_line


# --- C. Job context ---------------------------------------------------------------


@job_handler("test_obs_logs_context")
def _logs_context_handler(ctx, payload: dict) -> dict:
    ctx.logger.info("obs handler ran")
    return {}


def test_enqueue_inside_request_stores_request_id(fresh_db, no_inline_jobs):
    token = request_id_var.set("req-enqueue-1")
    try:
        job = q.enqueue(fresh_db, "test_obs_logs_context", {"x": 1})
    finally:
        request_id_var.reset(token)
    fresh_db.commit()

    fresh_db.expire_all()
    stored = fresh_db.get(type(job), job.id).payload
    assert stored["_meta"] == {"request_id": "req-enqueue-1"}
    assert stored["x"] == 1


def test_enqueue_outside_request_leaves_payload_unchanged(fresh_db, no_inline_jobs):
    job = q.enqueue(fresh_db, "test_obs_logs_context", {"x": 2})
    fresh_db.commit()

    fresh_db.expire_all()
    assert fresh_db.get(type(job), job.id).payload == {"x": 2}


def test_worker_log_carries_request_and_job_id(fresh_db, no_inline_jobs):
    token = request_id_var.set("req-worker-1")
    try:
        job = q.enqueue(fresh_db, "test_obs_logs_context", {})
    finally:
        request_id_var.reset(token)
    job_id = job.id
    fresh_db.commit()

    with _captured("app.jobs.worker") as stream:
        assert worker_mod._run_one(fresh_db, "w-obs", drain=True, kinds={"test_obs_logs_context"}) is True

    records = _lines(stream)
    handler_lines = [r for r in records if r["msg"] == "obs handler ran"]
    outcome_lines = [r for r in records if r["msg"].startswith(f"job test_obs_logs_context#{job_id} ok")]
    assert len(handler_lines) == 1 and len(outcome_lines) == 1
    for rec in (handler_lines[0], outcome_lines[0]):
        assert rec["request_id"] == "req-worker-1"
        assert rec["job_id"] == job_id
        assert rec["logger"] == "app.jobs.worker"
    # The context is scoped to the job: nothing leaks back into the worker thread.
    assert request_id_var.get() is None
    assert job_id_var.get() is None


# --- F. Worker healthcheck --------------------------------------------------------


@pytest.fixture
def heartbeat_dir(tmp_path, monkeypatch) -> Iterator[str]:
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    yield str(tmp_path)


def test_healthcheck_is_healthy_with_fresh_heartbeat(heartbeat_dir):
    worker_mod.write_heartbeat()

    assert worker_mod.healthcheck() == 0


def test_healthcheck_is_unhealthy_with_stale_heartbeat(heartbeat_dir):
    worker_mod.write_heartbeat()
    stale = time.time() - 300
    os.utime(worker_mod.heartbeat_path(), (stale, stale))

    assert worker_mod.healthcheck() == 1


def test_healthcheck_is_unhealthy_without_heartbeat_file(heartbeat_dir):
    assert worker_mod.healthcheck() == 1


def test_healthcheck_is_unhealthy_when_database_is_down(heartbeat_dir, monkeypatch):
    worker_mod.write_heartbeat()
    dead = create_engine(
        "postgresql+psycopg://nobody:nope@127.0.0.1:1/none", connect_args={"connect_timeout": 2}
    )
    monkeypatch.setattr(worker_mod, "get_engine", lambda: dead)
    try:
        assert worker_mod.healthcheck() == 1
    finally:
        dead.dispose()


def test_worker_loop_heartbeat_is_written_by_touch(heartbeat_dir):
    worker_mod._touch_heartbeat()

    assert worker_mod.heartbeat_path().exists()
    assert abs(float(worker_mod.heartbeat_path().read_text()) - time.time()) < 60
