"""Token sources: where a bearer token comes from.

Three ways, matching `LLM_AUTH`:
  * `api_key`  -- a stored key, or `LLM_API_KEY` from the environment;
  * `command`  -- a trusted local command that prints a token (for any external tool
                  that can mint one). Configured by a human; nothing here reads any
                  other tool's credential store;
  * `oauth`    -- a generic OAuth2 authorization-code + PKCE flow.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.llm.credentials.store import load_credential, save_credential
from app.llm.providers.base import TokenSource

log = get_logger(__name__)

EXPIRY_MARGIN_SECONDS = 60


def _stored(db: Session | None, provider: str, kind: str) -> dict | None:
    """A stored credential, read on the ledger pool. A read on the caller's session would leave that
    session inside a transaction for the whole LLM call."""
    if db is None:
        return None
    from app.core.db import LedgerSessionLocal

    with LedgerSessionLocal() as s:
        return load_credential(s, provider, kind)


def _store(db: Session | None, provider: str, kind: str, payload: dict) -> None:
    """Save a credential on the ledger pool, for the same reason as `_stored`."""
    if db is None:
        return
    from app.core.db import LedgerSessionLocal

    with LedgerSessionLocal() as s:
        save_credential(s, provider, kind, payload)


@dataclass
class StaticTokenSource(TokenSource):
    """A literal key: the stored credential if there is one, else `LLM_API_KEY`."""

    db: Session | None
    provider: str
    env_api_key: str

    def get_token(self) -> str | None:
        if self.db is not None:
            payload = _stored(self.db, self.provider, "api_key") or {}
            value = payload.get("value") or payload.get("api_key")
            if value:
                return str(value)
        return self.env_api_key or None

    def invalidate(self) -> None:
        return None


@dataclass
class CommandTokenSource(TokenSource):
    """Runs a command and reads the token from stdout.

    The result is cached for `LLM_TOKEN_TTL_SECONDS`; `invalidate()` drops the cache
    so a 401 triggers exactly one fresh mint.
    """

    command: str = ""
    ttl_seconds: int = 300
    db: Session | None = None
    provider: str = ""
    _token: str | None = None
    _fetched_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def get_token(self) -> str | None:
        with self._lock:
            fresh = (time.monotonic() - self._fetched_at) < self.ttl_seconds
            if self._token and fresh:
                return self._token
            self._token = self._run()
            self._fetched_at = time.monotonic()
            return self._token

    def _run(self) -> str | None:
        cmd = self.command
        if not cmd and self.db is not None:
            payload = _stored(self.db, self.provider, "command") or {}
            cmd = str(payload.get("value") or payload.get("command") or "")
        if not cmd or not cmd.strip():
            return None
        try:
            proc = subprocess.run(  # noqa: S603 - the command is operator-configured
                shlex.split(cmd),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("token command failed: %s", exc)
            return None
        token = (proc.stdout or "").strip()
        return token or None

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._fetched_at = 0.0


@dataclass
class OAuthPKCETokenSource(TokenSource):
    """Generic OAuth2 authorization-code + PKCE, refreshing before expiry.

    Nothing here is written for one specific vendor: the authorize/token endpoints
    and the client id come from settings, and any extra authorize parameters are a
    JSON object the operator supplies.
    """

    db: Session
    provider: str
    settings: Settings
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def get_token(self) -> str | None:
        with self._lock:
            payload = _stored(self.db, self.provider, "oauth") or {}
            token = payload.get("access_token")
            if not token:
                return None
            expires_at = payload.get("expires_at")
            if expires_at and _seconds_until(expires_at) > EXPIRY_MARGIN_SECONDS:
                return str(token)
            refreshed = self._refresh(payload)
            return refreshed

    def _refresh(self, payload: dict) -> str | None:
        refresh_token = payload.get("refresh_token")
        settings = self.settings
        if not refresh_token or not settings.oauth_token_url:
            return str(payload["access_token"]) if payload.get("access_token") else None
        try:
            resp = httpx.post(
                settings.oauth_token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": settings.oauth_client_id,
                    **(
                        {"client_secret": settings.oauth_client_secret}
                        if settings.oauth_client_secret
                        else {}
                    ),
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("oauth refresh failed: %s", exc)
            return str(payload["access_token"])

        data = resp.json()
        merged = {**payload, **data}
        if data.get("expires_in"):
            merged["expires_at"] = (
                datetime.now(UTC) + timedelta(seconds=int(data["expires_in"]))
            ).isoformat()
        _store(self.db, self.provider, "oauth", merged)
        return str(merged.get("access_token") or "")

    def invalidate(self) -> None:
        # The next get_token() re-reads the row and refreshes when it has expired; a
        # forced refresh here is what the 401 path needs.
        payload = _stored(self.db, self.provider, "oauth") or {}
        if payload.get("refresh_token"):
            self._refresh(payload)


def _seconds_until(iso: str) -> float:
    try:
        when = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (when - datetime.now(UTC)).total_seconds()


def command_token_ttl(db: Session | None, provider: str, settings: Settings, *, ledger: bool = False) -> int:
    """The token-command TTL: the stored credential's `ttl_seconds` if set, else the env default."""
    if db is not None:
        payload = _stored(db, provider, "command") if ledger else load_credential(db, provider, "command")
        stored = (payload or {}).get("ttl_seconds")
        if isinstance(stored, int) and not isinstance(stored, bool):
            return stored
    return settings.llm_token_ttl_seconds


def make_token_source(
    settings: Settings, db: Session | None = None, effective: Any = None
) -> TokenSource | None:
    """Build the token source for `LLM_AUTH` / effective settings."""
    from app.llm.router import read_db_overrides_on_ledger

    overrides = read_db_overrides_on_ledger(db)
    auth = getattr(effective, "auth", None) or overrides.get("auth") or settings.llm_auth
    provider = (
        getattr(effective, "preset", None) or overrides.get("provider") or settings.llm_preset or "custom"
    )

    if auth == "command":
        cmd = ""
        if db is not None:
            payload = _stored(db, provider, "command") or {}
            cmd = str(payload.get("value") or payload.get("command") or "")
        if not cmd:
            cmd = settings.llm_token_command
        return CommandTokenSource(
            command=cmd,
            ttl_seconds=command_token_ttl(db, provider, settings, ledger=True),
            db=db,
            provider=provider,
        )
    if auth == "oauth":
        if db is None:
            # No session means no credential store; the provider will simply be
            # unauthenticated and the caller falls back to the heuristic.
            return None
        return OAuthPKCETokenSource(db=db, provider=provider, settings=settings)
    return StaticTokenSource(db=db, provider=provider, env_api_key=settings.llm_api_key)


__all__ = [
    "CommandTokenSource",
    "OAuthPKCETokenSource",
    "StaticTokenSource",
    "command_token_ttl",
    "make_token_source",
]
