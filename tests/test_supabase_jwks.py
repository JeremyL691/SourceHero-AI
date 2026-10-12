"""Supabase JWKS verification: success path, negative cache for failed fetches, retry window."""

from __future__ import annotations

import json
import time as real_time
import uuid
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import supabase
from app.core.config import get_settings
from app.core.supabase import clear_jwks_cache, verify_supabase_jwt

SUPABASE_URL = "https://proj.supabase.co"
JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
KID = "test-kid-1"
SUB = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _fresh_jwks_state(monkeypatch):
    clear_jwks_cache()
    monkeypatch.setattr(get_settings(), "supabase_url", SUPABASE_URL)
    yield
    clear_jwks_cache()


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def clock(monkeypatch):
    """Replace the `time` module seen by app.core.supabase with a controllable monotonic clock."""
    state = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(supabase, "time", SimpleNamespace(monotonic=lambda: state.now))
    return state


def _jwks_body(signing_key) -> dict:
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    public_jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return {"keys": [public_jwk]}


def _token(signing_key, sub: str = SUB) -> str:
    now = int(real_time.time())
    return jwt.encode(
        {
            "sub": sub,
            "aud": "authenticated",
            "iss": f"{SUPABASE_URL}/auth/v1",
            "iat": now,
            "exp": now + 600,
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KID},
    )


def test_jwks_fetch_success_accepts_token(respx_mock, signing_key):
    route = respx_mock.get(JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks_body(signing_key)))

    assert verify_supabase_jwt(_token(signing_key)) == SUB
    assert route.call_count == 1


def test_failed_jwks_fetch_returns_none_and_is_negatively_cached(respx_mock, signing_key, clock):
    route = respx_mock.get(JWKS_URL).mock(return_value=httpx.Response(500))
    token = _token(signing_key)

    assert verify_supabase_jwt(token) is None
    assert route.call_count == 1

    # Inside the 60 second window: 401 (None) with no further HTTP call.
    clock.now += 30.0
    assert verify_supabase_jwt(token) is None
    assert verify_supabase_jwt(token) is None
    assert route.call_count == 1


def test_failed_jwks_fetch_is_retried_after_sixty_seconds(respx_mock, signing_key, clock):
    route = respx_mock.get(JWKS_URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json=_jwks_body(signing_key))]
    )
    token = _token(signing_key)

    assert verify_supabase_jwt(token) is None
    assert route.call_count == 1

    clock.now += 60.5
    assert verify_supabase_jwt(token) == SUB
    assert route.call_count == 2
