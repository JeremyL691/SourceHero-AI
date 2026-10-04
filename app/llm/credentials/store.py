"""Encrypted credential storage."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt, encrypt, try_decrypt
from app.core.errors import ConflictError
from app.models.llm import ProviderCredential

KINDS = ("api_key", "oauth", "command")


def save_credential(db: Session, provider: str, kind: str, payload: dict) -> ProviderCredential:
    """Store (replacing) one credential of `kind` for `provider`."""
    if kind not in KINDS:
        raise ConflictError(f"unknown credential kind {kind!r}", code="bad_credential_kind")
    blob = encrypt(json.dumps(payload))
    existing = db.execute(
        select(ProviderCredential).where(
            ProviderCredential.provider == provider, ProviderCredential.kind == kind
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.encrypted_payload = blob
        db.commit()
        db.refresh(existing)
        return existing

    row = ProviderCredential(provider=provider, kind=kind, encrypted_payload=blob)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def load_credential(db: Session, provider: str, kind: str = "api_key") -> dict | None:
    """Decrypt and return the payload, or None when absent/unreadable."""
    row = db.execute(
        select(ProviderCredential).where(
            ProviderCredential.provider == provider, ProviderCredential.kind == kind
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    plaintext = try_decrypt(row.encrypted_payload)
    if plaintext is None:
        # Encrypted under a different SOURCEHERO_SECRET_KEY. Reads as "not
        # configured" rather than taking the settings page down.
        return None
    try:
        return json.loads(plaintext)
    except json.JSONDecodeError:
        return None


def delete_credential(db: Session, provider: str, kind: str | None = None) -> int:
    kinds = (kind,) if kind else KINDS
    rows = (
        db.execute(
            select(ProviderCredential).where(
                ProviderCredential.provider == provider, ProviderCredential.kind.in_(kinds)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)


def credential_status(db: Session, provider: str) -> dict:
    """Presence flags only -- never the secret itself."""
    out: dict[str, bool] = {}
    for kind in KINDS:
        row = db.execute(
            select(ProviderCredential).where(
                ProviderCredential.provider == provider, ProviderCredential.kind == kind
            )
        ).scalar_one_or_none()
        out[f"has_{kind}"] = row is not None
        if kind == "oauth" and row is not None:
            out["oauth_expires_at"] = row.expires_at.isoformat() if row.expires_at else None
    return out


__all__ = [
    "KINDS",
    "credential_status",
    "decrypt",
    "delete_credential",
    "encrypt",
    "load_credential",
    "save_credential",
]
