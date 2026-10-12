"""Provider presets and effective settings.

Merge order is env -> preset defaults -> `app_settings` (the database wins), so an
operator can change a setting in the UI without editing `.env`, and a preset supplies
sane values for everything the operator did not specify.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import DomainValidationError
from app.llm.types import LLMConfigError
from app.models.user import AppSetting

SETTINGS_KEY = "llm"
FREE_ROUTER_MODEL = "openrouter/free"


@dataclass(frozen=True)
class Preset:
    name: str
    provider: str
    base_url: str = ""
    model_fast: str = ""
    model_smart: str = ""
    note: str = ""
    # Tried in order when the primary model is retired (404/410) or keeps failing (3x 5xx).
    fallback_models: tuple[str, ...] = ()
    extra_body: dict[str, Any] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Used only when the operator has not set the value; 0 means "no preset opinion".
    default_request_budget: int = 0
    default_max_concurrency: int = 0


PRESETS: dict[str, Preset] = {
    "opencode-go": Preset(
        name="opencode-go",
        provider="openai_chat",
        base_url="https://opencode.ai/zen/go/v1",
        model_fast="deepseek-v4-flash",
        model_smart="glm-5.3",
        note=(
            "Models are grouped by endpoint: chat/completions models use "
            "provider 'openai_chat', /messages models use 'anthropic_messages', "
            "and /responses models use 'openai_responses'. Model IDs are confirmed "
            "by calling /models."
        ),
    ),
    "openai": Preset(
        name="openai",
        provider="openai_responses",
        base_url="https://api.openai.com/v1",
        note="Model IDs are chosen by the operator via 'Fetch models'.",
    ),
    "openrouter": Preset(
        name="openrouter",
        provider="openai_chat",
        base_url="https://openrouter.ai/api/v1",
        model_fast="nvidia/nemotron-3-super-120b-a12b:free",
        model_smart="nvidia/nemotron-3-super-120b-a12b:free",
        fallback_models=("google/gemma-4-31b-it:free", FREE_ROUTER_MODEL),
        extra_body={"reasoning": {"effort": "low", "exclude": True}},
        extra_headers={"X-Title": "SourceHero"},
        default_request_budget=800,
        default_max_concurrency=2,
        note=(
            "Free-tier models only: every model id must end in ':free' or be 'openrouter/free', "
            "unless LLM_ALLOW_PAID=true. Uses OPENROUTER_API_KEY when no LLM API key is set."
        ),
    ),
    "fake": Preset(
        name="fake",
        provider="fake",
        model_fast="fake-fast",
        model_smart="fake-smart",
        note="Runs entirely offline on heuristic implementations.",
    ),
}


@dataclass
class EffectiveLLM:
    provider: str
    base_url: str = ""
    model_fast: str = ""
    model_smart: str = ""
    auth: str = "api_key"
    auth_header: str = "authorization"
    json_mode: str = "auto"
    extra_headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 120.0
    daily_token_budget: int = 2_000_000
    preset: str = ""
    fallback_models: tuple[str, ...] = ()
    extra_body: dict[str, Any] = field(default_factory=dict)
    max_concurrency: int = 4
    request_budget: int = 0


_settings_version: int = 1
_settings_lock = threading.Lock()


def bump_settings_version() -> int:
    global _settings_version
    with _settings_lock:
        _settings_version += 1
        return _settings_version


def get_settings_version() -> int:
    with _settings_lock:
        return _settings_version


def read_db_overrides(db: Session | None) -> dict:
    if db is None:
        return {}
    row = db.get(AppSetting, SETTINGS_KEY)
    return dict(row.value or {}) if row is not None else {}


def write_db_overrides(db: Session, values: dict) -> None:
    row = db.get(AppSetting, SETTINGS_KEY)
    merged = {**read_db_overrides(db), **values}
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=merged))
    else:
        row.value = merged
    db.commit()
    bump_settings_version()


def clear_db_overrides(db: Session) -> None:
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is not None:
        db.delete(row)
        db.commit()
    bump_settings_version()


def effective_llm_settings(db: Session | None = None, settings: Settings | None = None) -> EffectiveLLM:
    """env -> preset -> database, in that order of increasing precedence."""
    settings = settings or get_settings()
    preset = PRESETS.get(settings.llm_preset) if settings.llm_preset else None
    if settings.llm_preset and preset is None:
        raise DomainValidationError(
            f"unknown LLM preset {settings.llm_preset!r}; available: {', '.join(sorted(PRESETS))}",
            code="bad_preset",
        )

    provider = settings.llm_provider
    base_url = settings.llm_base_url
    model_fast = settings.llm_model_fast
    model_smart = settings.llm_model_smart
    if preset is not None:
        # A preset exists to fill in the blanks, and `fake` is the shipped default for
        # LLM_PROVIDER. So `fake` here means "not chosen": picking a preset in the
        # settings page has to actually switch the provider, or the control does
        # nothing. An explicitly configured non-fake provider still wins.
        if provider == "fake":
            provider = preset.provider
        base_url = base_url or preset.base_url
        model_fast = model_fast or preset.model_fast
        model_smart = model_smart or preset.model_smart

    if provider == "fake" and not (model_fast and model_smart):
        # Report real model names for usage and readiness rather than an empty
        # string, so `llm_calls` rows say something a reader can interpret.
        model_fast = model_fast or PRESETS["fake"].model_fast
        model_smart = model_smart or PRESETS["fake"].model_smart

    preset_headers = dict(preset.extra_headers) if preset is not None else {}
    # The operator's own values win over the preset's defaults, key by key.
    extra_body = {
        **(preset.extra_body if preset is not None else {}),
        **settings.json_object("llm_extra_body"),
    }
    headers = {**preset_headers, **settings.extra_headers}

    overrides = read_db_overrides(db)
    return EffectiveLLM(
        provider=overrides.get("provider", provider),
        base_url=overrides.get("base_url", base_url),
        model_fast=overrides.get("model_fast", model_fast),
        model_smart=overrides.get("model_smart", model_smart),
        auth=overrides.get("auth", settings.llm_auth),
        auth_header=overrides.get("auth_header", settings.llm_auth_header),
        json_mode=overrides.get("json_mode", settings.llm_json_mode),
        extra_headers=overrides.get("extra_headers", headers),
        timeout=overrides.get("timeout", settings.llm_timeout_seconds),
        daily_token_budget=overrides.get("daily_token_budget", settings.llm_daily_token_budget),
        preset=settings.llm_preset,
        fallback_models=preset.fallback_models if preset is not None else (),
        extra_body=extra_body,
        max_concurrency=_setting_or_preset(
            settings, "llm_max_concurrency", preset.default_max_concurrency if preset else 0
        ),
        request_budget=_setting_or_preset(
            settings, "llm_daily_request_budget", preset.default_request_budget if preset else 0
        ),
    )


def _setting_or_preset(settings: Settings, field_name: str, preset_value: int) -> int:
    """The operator's value if they set it (0 included), else the preset's value when it has one."""
    if field_name in settings.model_fields_set or not preset_value:
        return int(getattr(settings, field_name))
    return preset_value


def is_free_model(model: str) -> bool:
    return model == FREE_ROUTER_MODEL or model.endswith(":free")


def enforce_free_models(effective: EffectiveLLM, *, allow_paid: bool) -> None:
    """On OpenRouter every model, fallbacks included, must be free unless explicitly allowed."""
    if allow_paid:
        return
    if effective.preset != "openrouter" and "openrouter.ai" not in effective.base_url:
        return
    for model in (effective.model_fast, effective.model_smart, *effective.fallback_models):
        if model and not is_free_model(model):
            raise LLMConfigError(
                f"model {model!r} is not free on OpenRouter; set LLM_ALLOW_PAID=true to allow paid models",
                code="paid_model",
            )


def redact(effective: EffectiveLLM, settings: Settings, db: Session | None) -> dict:
    """The settings payload, with every secret reduced to a boolean."""
    from app.llm.credentials.store import credential_status

    out = {
        "provider": effective.provider,
        "base_url": effective.base_url,
        "model_fast": effective.model_fast,
        "model_smart": effective.model_smart,
        "auth": effective.auth,
        "auth_header": effective.auth_header,
        "json_mode": effective.json_mode,
        "daily_token_budget": effective.daily_token_budget,
        "preset": effective.preset,
        "api_key_from_env": bool(
            settings.llm_api_key or (settings.llm_preset == "openrouter" and settings.openrouter_api_key)
        ),
        "token_command_set": bool(settings.llm_token_command),
        "presets": sorted(PRESETS),
    }
    if db is not None:
        out.update(credential_status(db, effective.preset or "custom"))
    return out


__all__ = [
    "PRESETS",
    "SETTINGS_KEY",
    "EffectiveLLM",
    "Preset",
    "bump_settings_version",
    "clear_db_overrides",
    "effective_llm_settings",
    "enforce_free_models",
    "is_free_model",
    "get_settings_version",
    "read_db_overrides",
    "redact",
    "write_db_overrides",
]
