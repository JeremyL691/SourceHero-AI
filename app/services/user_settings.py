from __future__ import annotations

import os
from typing import Any

from app.openai_models import DEFAULT_TEXT_MODEL

DEFAULT_MODEL = DEFAULT_TEXT_MODEL


def effective_openai_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or None


def effective_openai_model() -> str:
    return os.getenv("OPENAI_MODEL") or DEFAULT_MODEL


def public_settings() -> dict[str, Any]:
    key = effective_openai_key()
    return {
        "openai_configured": bool(key),
        "openai_key_preview": (key[:4] + "..." + key[-4:]) if key and len(key) > 12 else None,
        "openai_key_source": "env" if key else None,
        "openai_model": effective_openai_model(),
    }


def save_user_config(values: dict[str, Any]) -> dict[str, Any]:
    return public_settings()


def clear_openai_key() -> None:
    pass
