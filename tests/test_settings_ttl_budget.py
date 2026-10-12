"""K6.4: settings-page support -- token-command TTL and today's usage against the budgets."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.llm.router import effective_llm_settings
from app.models.llm import LLMRun

pytestmark = pytest.mark.db


@pytest.fixture
def admin_client(make_client):
    return make_client(email=f"admin{uuid.uuid4().hex[:10]}@test.local", admin=True)


def _add_run(db, *, when: datetime, prompt: int = 0, completion: int = 0, status: str = "ok") -> None:
    db.add(
        LLMRun(
            task="ttl_budget_test",
            provider="fake",
            model="m",
            prompt_tokens=prompt,
            completion_tokens=completion,
            cache_hit=False,
            status=status,
            created_at=when,
        )
    )
    db.commit()


def test_command_credential_ttl_is_reported_by_settings(admin_client):
    resp = admin_client.post(
        "/v1/settings/llm/credentials",
        json={"kind": "command", "value": "echo tok", "ttl_seconds": 120},
    )
    assert resp.status_code == 200, resp.text
    assert admin_client.get("/v1/settings/llm").json()["token_ttl_seconds"] == 120


def test_command_credential_without_ttl_reports_the_env_default(admin_client, db):
    from app.core.config import get_settings

    admin_client.post("/v1/settings/llm/credentials", json={"kind": "command", "value": "echo tok"})
    body = admin_client.get("/v1/settings/llm").json()
    assert body["token_ttl_seconds"] == get_settings().llm_token_ttl_seconds


@pytest.mark.parametrize("ttl", [-1, 86401, 1.5, "120", True])
def test_out_of_range_or_non_integer_ttl_is_rejected(admin_client, ttl):
    resp = admin_client.post(
        "/v1/settings/llm/credentials",
        json={"kind": "command", "value": "echo tok", "ttl_seconds": ttl},
    )
    assert 400 <= resp.status_code < 500, resp.text
    assert resp.json()["error"]["code"] == "bad_ttl"


def test_usage_today_counts_only_todays_calls(admin_client, db):
    before = admin_client.get("/v1/usage").json()["today"]
    now = datetime.now(UTC)

    _add_run(db, when=now, prompt=10, completion=5)
    _add_run(db, when=now, status="budget")  # rejected before the call: no tokens, not a request
    _add_run(db, when=now - timedelta(days=1), prompt=1000, completion=1000)  # yesterday: not counted

    today = admin_client.get("/v1/usage").json()["today"]
    assert today["requests"] - before["requests"] == 1
    assert today["tokens"] - before["tokens"] == 15

    effective = effective_llm_settings(db)
    assert today["request_budget"] == effective.request_budget
    assert today["token_budget"] == effective.daily_token_budget
