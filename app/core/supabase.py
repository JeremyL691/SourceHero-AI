"""Supabase JWT verification.

JWKS is fetched with httpx (PyJWT's `PyJWKClient` reaches for urllib internally, which
respx cannot intercept, and it refetches on every call). The key set is cached for ten
minutes per audience/issuer.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from jwt import PyJWK

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

JWKS_TTL_SECONDS = 600
_algorithms = ["RS256", "ES256"]

_cache: dict[str, dict[str, Any]] = {}
_negative_kid_cache: dict[str, float] = {}


def _jwks_url(settings: Any) -> str:
    base = settings.supabase_url.rstrip("/")
    return f"{base}/auth/v1/.well-known/jwks.json"


def _fetch_keys(settings: Any) -> list[PyJWK]:
    cached = _cache.get("keys")
    if cached and cached["expires"] > time.monotonic():
        return cached["value"]
    resp = httpx.get(_jwks_url(settings), timeout=10.0)
    resp.raise_for_status()
    keys = [PyJWK.from_dict(k) for k in resp.json().get("keys", [])]
    _cache["keys"] = {"value": keys, "expires": time.monotonic() + JWKS_TTL_SECONDS}
    return keys


def clear_jwks_cache() -> None:
    _cache.clear()
    _negative_kid_cache.clear()


def verify_supabase_jwt(token: str) -> str | None:
    """Return the `sub` of a valid Supabase access token, else None.

    Never raises: an unverifiable token is simply not an identity.
    """
    settings = get_settings()
    if not settings.supabase_url:
        return None
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if kid and kid in _negative_kid_cache and _negative_kid_cache[kid] > time.monotonic():
            return None
        keys = _fetch_keys(settings)
    except Exception:  # noqa: BLE001 - network or malformed header
        log.warning("supabase: could not load JWKS", exc_info=True)
        return None

    matched_kid = False
    for key in keys:
        if kid and getattr(key, "key_id", None) != kid:
            continue
        matched_kid = True
        try:
            expected_iss = f"{settings.supabase_url.rstrip('/')}/auth/v1"
            payload = jwt.decode(
                token,
                key.key,
                algorithms=_algorithms,
                audience=settings.supabase_jwt_audience or None,
                issuer=expected_iss,
                options={
                    "verify_aud": bool(settings.supabase_jwt_audience),
                    "verify_iss": True,
                },
            )
        except jwt.InvalidTokenError:
            continue
        sub = payload.get("sub")
        return str(sub) if sub else None

    if kid and not matched_kid:
        _negative_kid_cache[kid] = time.monotonic() + 60.0

    return None


__all__ = ["clear_jwks_cache", "verify_supabase_jwt"]
