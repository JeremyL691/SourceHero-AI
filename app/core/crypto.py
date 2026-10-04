"""Symmetric encryption for stored provider credentials (Fernet, key derived from
`SOURCEHERO_SECRET_KEY`)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key)


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode()


def try_decrypt(ciphertext: bytes) -> str | None:
    """Return None instead of raising: a credential encrypted under an older secret
    should read as "not configured", not take the settings page down."""
    try:
        return decrypt(ciphertext)
    except (InvalidToken, ValueError):
        return None


__all__ = ["decrypt", "encrypt", "try_decrypt"]
