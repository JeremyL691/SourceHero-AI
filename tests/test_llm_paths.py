"""LLM call sites must honour the settings page and the daily budget (REVIEW LLM-7).

The provider override saved by the settings page lives in `app_settings`. A call site
that builds its client without `db` never sees it, and its budget check is skipped, so
these tests assert that the overridden endpoint is the one actually called and that a
spent budget stops the call before it leaves the process.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.core import time as clock
from app.core.config import get_settings
from app.llm.router import write_db_overrides
from app.llm.types import BudgetExceededError, LLMError
from app.models.llm import LLMRun
from app.models.user import User

pytestmark = pytest.mark.db

OVERRIDE_BASE = "https://llm.test/v1"
COMPLETION_URL = f"{OVERRIDE_BASE}/chat/completions"


@pytest.fixture
def db(fresh_db):
    """The LLM client reads budgets and saved overrides on its own connections, so these rows must be committed."""
    return fresh_db


def _user(db) -> User:
    user = User(email=f"u_{uuid.uuid4().hex[:8]}@test.local", password_hash="argon2...")
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def overridden_provider(db, monkeypatch):
    """The settings page has switched the provider to an OpenAI-compatible endpoint."""
    monkeypatch.setattr(get_settings(), "llm_api_key", "sk-test-key")
    write_db_overrides(
        db,
        {
            "provider": "openai_chat",
            "base_url": OVERRIDE_BASE,
            "model_fast": "fast-model",
            "model_smart": "smart-model",
        },
    )


def _completion_route():
    # Text that never validates against any schema, so nothing is written to the LLM
    # cache: a cached success would let a later budget test skip the budget check.
    return respx.post(COMPLETION_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
    )


def _spend_budget(db, user: User, budget: int) -> None:
    write_db_overrides(db, {"daily_token_budget": budget})
    db.add(
        LLMRun(
            user_id=user.id,
            task="t",
            provider="openai_chat",
            model="m",
            prompt_tokens=budget * 10,
            completion_tokens=0,
            status="ok",
            created_at=clock.utcnow(),
        )
    )
    db.commit()


# --- verifier -------------------------------------------------------------


@respx.mock
def test_verifier_llm_pass_uses_the_overridden_provider(db, overridden_provider):
    from app.verify.verifier import verify_text

    user = _user(db)
    route = _completion_route()

    verify_text(db, user.id, "Halcyon Cells delivered 999 Wh/kg at launch.", mode="smart")

    assert route.call_count >= 1, "the verifier ignored the saved provider override"


@respx.mock
def test_verifier_llm_pass_refuses_when_budget_is_spent(db, overridden_provider):
    from app.verify.verifier import verify_text

    user = _user(db)
    _spend_budget(db, user, budget=100)
    route = _completion_route()

    result = verify_text(db, user.id, "Halcyon Cells delivered 999 Wh/kg at launch.", mode="smart")

    assert route.call_count == 0, "a spent budget must stop the call before it is sent"
    assert result.method.startswith("heuristic(fallback:budget"), result.method


# --- belief diff digest ---------------------------------------------------

_EMPTY_PAYLOAD = {
    "new_claims": [],
    "superseded": [],
    "retracted": [],
    "new_conflicts": [],
    "resolved": [],
    "stale_notes": [],
    "stats": {},
}


@respx.mock
def test_belief_digest_uses_the_overridden_provider(db, overridden_provider):
    from app.temporal.belief_diff import render_digest

    user = _user(db)
    route = _completion_route()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 2, 1)

    _, method = render_digest(_EMPTY_PAYLOAD, start=start, end=end, db=db, user_id=user.id)

    assert route.call_count >= 1, "the belief digest ignored the saved provider override"
    assert not method.startswith("llm:"), "an unparseable body must not be accepted as a digest"


@respx.mock
def test_belief_digest_refuses_when_budget_is_spent(db, overridden_provider):
    from app.temporal.belief_diff import render_digest

    user = _user(db)
    _spend_budget(db, user, budget=100)
    route = _completion_route()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 2, 1)

    _, method = render_digest(_EMPTY_PAYLOAD, start=start, end=end, db=db, user_id=user.id)

    assert route.call_count == 0
    assert method == "template(fallback:budget_exhausted)", method


# --- wiki compiler --------------------------------------------------------


@respx.mock
def test_wiki_compile_uses_the_overridden_provider(db, overridden_provider):
    from app.wiki.compiler import _llm_compile

    user = _user(db)
    page = SimpleNamespace(title="Halcyon Cells", slug="halcyon-cells", user_id=user.id)
    route = _completion_route()

    with pytest.raises(LLMError):  # the unparseable body is rejected, after one call
        _llm_compile(db, page, [], [])

    assert route.call_count >= 1, "the wiki compiler ignored the saved provider override"


@respx.mock
def test_wiki_compile_refuses_when_budget_is_spent(db, overridden_provider):
    from app.wiki.compiler import _llm_compile

    user = _user(db)
    _spend_budget(db, user, budget=100)
    page = SimpleNamespace(title="Halcyon Cells", slug="halcyon-cells", user_id=user.id)
    route = _completion_route()

    with pytest.raises(BudgetExceededError, match="budget"):
        _llm_compile(db, page, [], [])

    assert route.call_count == 0
