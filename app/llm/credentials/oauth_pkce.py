"""OAuth2 authorization-code + PKCE, written for no particular vendor.

Values come from settings; a human supplies the endpoints. Nothing here reads another
tool's credential store, and the settings page is where the operator points it at
whatever provider they actually use.

The state row is the CSRF guard: the callback can only complete a flow this process
started, and only within ten minutes.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.config import Settings
from app.core.errors import ConflictError, DomainValidationError
from app.core.logging import get_logger
from app.llm.credentials.store import save_credential
from app.models.llm import OAuthState

log = get_logger(__name__)

STATE_TTL_MINUTES = 10
REQUIRED_FIELDS = (
    "oauth_authorize_url",
    "oauth_token_url",
    "oauth_client_id",
    "oauth_redirect_uri",
)


def missing_fields(settings: Settings) -> list[str]:
    return [name for name in REQUIRED_FIELDS if not getattr(settings, name, "")]


def _challenge(verifier: str) -> str:
    """base64url(sha256(verifier)) with padding removed, per RFC 7636."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def start_authorization(db: Session, settings: Settings, provider: str = "custom") -> str:
    """Create the authorize URL and remember the state. Returns the URL to visit."""
    missing = missing_fields(settings)
    if missing:
        raise ConflictError(
            f"OAuth is not configured; set: {', '.join(missing)}",
            code="oauth_not_configured",
            details={"missing": missing},
        )

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    db.add(OAuthState(state=state, code_verifier=verifier, provider=provider))
    db.commit()

    params = {
        "response_type": "code",
        "client_id": settings.oauth_client_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }
    if settings.oauth_scopes:
        params["scope"] = settings.oauth_scopes
    params.update(settings.extra_authorize_params)

    separator = "&" if "?" in settings.oauth_authorize_url else "?"
    return f"{settings.oauth_authorize_url}{separator}{urlencode(params)}"


def complete_authorization(
    db: Session, settings: Settings, *, code: str, state: str, provider: str = "custom"
) -> dict:
    """Exchange the authorization code for a token and store it encrypted."""
    row = db.get(OAuthState, state)
    if row is None:
        raise DomainValidationError("unknown or already-used OAuth state", code="bad_oauth_state")

    age = clock.utcnow() - row.created_at
    if age > timedelta(minutes=STATE_TTL_MINUTES):
        db.delete(row)
        db.commit()
        raise DomainValidationError("OAuth state has expired", code="oauth_state_expired")

    # Single use: delete before the exchange so a replayed callback cannot re-run it.
    verifier = row.code_verifier
    db.delete(row)
    db.commit()

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oauth_redirect_uri,
        "client_id": settings.oauth_client_id,
        "code_verifier": verifier,
    }
    if settings.oauth_client_secret:
        data["client_secret"] = settings.oauth_client_secret

    try:
        resp = httpx.post(settings.oauth_token_url, data=data, timeout=30.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise DomainValidationError(f"token exchange failed: {exc}", code="oauth_exchange_failed") from exc

    payload = dict(resp.json())
    if "expires_in" in payload:
        from datetime import UTC, datetime

        payload["expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=int(payload["expires_in"]))
        ).isoformat()
    save_credential(db, provider, "oauth", payload)
    return {"connected": True, "expires_at": payload.get("expires_at")}


__all__ = [
    "REQUIRED_FIELDS",
    "STATE_TTL_MINUTES",
    "complete_authorization",
    "missing_fields",
    "start_authorization",
]
