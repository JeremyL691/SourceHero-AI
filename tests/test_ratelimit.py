"""Fixed-window rate limits: the (limit+1)th call in a window is a 429, the window rolls over,
callers are counted separately, and the flag off means no limit at all."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.core.ratelimit import enforce, window_start
from app.jobs.scheduler import purge_rate_limits
from app.models.ratelimit import RateLimit

pytestmark = pytest.mark.db

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)  # aligned to a 60-second window
GHOST = {"email": "nobody@test.local", "password": "wrong-password-x"}


@pytest.fixture
def limits_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "rate_limit_login", 2)
    monkeypatch.setattr(settings, "rate_limit_capture", 2)
    monkeypatch.setattr(settings, "rate_limit_upload", 1)
    monkeypatch.setattr(settings, "rate_limit_verify", 1)
    monkeypatch.setattr(settings, "rate_limit_ask", 1)
    return settings


def test_login_over_limit_is_429_with_retry_after_in_body(client, limits_on, freeze_time):
    freeze_time.set(T0 + timedelta(seconds=15))
    assert client.post("/v1/auth/login", json=GHOST).status_code == 401
    assert client.post("/v1/auth/login", json=GHOST).status_code == 401

    resp = client.post("/v1/auth/login", json=GHOST)
    assert resp.status_code == 429, resp.text
    assert resp.json()["error"]["code"] == "rate_limited"
    # 15s into a 60s window leaves 45s until it ends.
    assert resp.json()["error"]["details"]["retry_after"] == 45


def test_429_carries_retry_after_header(client, limits_on, freeze_time):
    freeze_time.set(T0 + timedelta(seconds=15))
    for _ in range(2):
        client.post("/v1/auth/login", json=GHOST)
    resp = client.post("/v1/auth/login", json=GHOST)
    assert resp.status_code == 429, resp.text
    assert resp.headers["Retry-After"] == "45"


def test_window_rolls_over_and_calls_succeed_again(client, limits_on, freeze_time):
    freeze_time.set(T0 + timedelta(seconds=15))
    for _ in range(3):
        client.post("/v1/auth/login", json=GHOST)
    assert client.post("/v1/auth/login", json=GHOST).status_code == 429

    freeze_time.set(T0 + timedelta(seconds=61))  # the next window
    resp = client.post("/v1/auth/login", json=GHOST)
    assert resp.status_code == 401, resp.text  # counted again, under the limit


def test_limit_is_per_email(client, limits_on, freeze_time):
    freeze_time.set(T0)
    for _ in range(3):
        client.post("/v1/auth/login", json=GHOST)
    other = {"email": "someone-else@test.local", "password": "wrong-password-x"}
    assert client.post("/v1/auth/login", json=other).status_code == 401


def test_flag_off_means_no_limit(client, monkeypatch, freeze_time):
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_login", 1)
    assert settings.rate_limit_enabled is False
    freeze_time.set(T0)
    statuses = [client.post("/v1/auth/login", json=GHOST).status_code for _ in range(5)]
    assert statuses == [401] * 5


def test_enforce_does_nothing_when_disabled(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    for _ in range(5):
        enforce(db, "off:key", 1, now=T0)
    assert db.query(RateLimit).filter(RateLimit.key == "off:key").count() == 0


def test_enforce_counts_inside_one_window_only(db, limits_on):
    enforce(db, "k:win", 2, now=T0)
    enforce(db, "k:win", 2, now=T0 + timedelta(seconds=30))
    with pytest.raises(Exception) as info:
        enforce(db, "k:win", 2, now=T0 + timedelta(seconds=45))
    assert info.value.code == "rate_limited"
    assert info.value.retry_after == 15
    # The next window starts with a fresh counter.
    enforce(db, "k:win", 2, now=T0 + timedelta(seconds=60))


def test_window_start_truncates_to_the_window():
    assert window_start(T0 + timedelta(seconds=59), 60) == T0
    assert window_start(T0 + timedelta(seconds=60), 60) == T0 + timedelta(seconds=60)


def test_capture_limit_is_per_caller(make_client, limits_on, freeze_time, no_inline_jobs):
    freeze_time.set(T0)
    alice = make_client(email="alice-rl@test.local")
    bob = make_client(email="bob-rl@test.local")
    body = {"text": "a short note"}

    assert alice.post("/v1/captures", json=body).status_code == 201
    assert alice.post("/v1/captures", json=body).status_code == 201
    blocked = alice.post("/v1/captures", json=body)
    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["error"]["code"] == "rate_limited"

    # Bob has his own counter for the same route.
    assert bob.post("/v1/captures", json=body).status_code == 201


def test_upload_verify_and_ask_are_limited(make_client, limits_on, freeze_time, no_inline_jobs):
    freeze_time.set(T0)
    user = make_client(email="routes-rl@test.local")

    up = {"file": ("note.md", b"# hello\n\nsome text", "text/markdown")}
    assert user.post("/v1/sources/upload", files=up).status_code != 429
    assert user.post("/v1/sources/upload", files=up).status_code == 429

    assert user.post("/v1/verify", json={"text": "The sky is blue."}).status_code != 429
    assert user.post("/v1/verify", json={"text": "The sky is blue."}).status_code == 429

    assert user.post("/v1/ask?stream=false", json={"question": "What is this?"}).status_code != 429
    assert user.post("/v1/ask?stream=false", json={"question": "What is this?"}).status_code == 429


def test_purge_removes_windows_older_than_two_lengths(db, limits_on):
    old = T0 - timedelta(seconds=3 * 60)
    inside = T0 - timedelta(seconds=60)  # still within two window lengths
    db.add_all(
        [
            RateLimit(key="p:old", window_start=old, count=5),
            RateLimit(key="p:inside", window_start=inside, count=5),
            RateLimit(key="p:now", window_start=T0, count=5),
        ]
    )
    db.flush()

    removed = purge_rate_limits(db, T0)

    assert removed == 1
    keys = {r.key for r in db.query(RateLimit).all()}
    assert keys == {"p:inside", "p:now"}
