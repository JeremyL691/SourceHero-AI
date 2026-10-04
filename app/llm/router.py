"""Provider presets and effective settings.

Merge order is env -> preset defaults -> `app_settings` (the database wins), so an
operator can change a setting in the UI without editing `.env`, and a preset supplies
sane values for everything the operator did not specify.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import DomainValidationError
from app.models.user import AppSetting

SETTINGS_KEY = "llm"


@dataclass(frozen=True)
class Preset:
    name: str
    provider: str
    base_url: str = ""
    model_fast: str = ""
    model_smart: str = ""
    note: str = ""


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


def clear_db_overrides(db: Session) -> None:
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is not None:
        db.delete(row)
        db.commit()


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

    overrides = read_db_overrides(db)
    return EffectiveLLM(
        provider=overrides.get("provider", provider),
        base_url=overrides.get("base_url", base_url),
        model_fast=overrides.get("model_fast", model_fast),
        model_smart=overrides.get("model_smart", model_smart),
        auth=overrides.get("auth", settings.llm_auth),
        auth_header=overrides.get("auth_header", settings.llm_auth_header),
        json_mode=overrides.get("json_mode", settings.llm_json_mode),
        extra_headers=overrides.get("extra_headers", settings.extra_headers),
        timeout=overrides.get("timeout", settings.llm_timeout_seconds),
        daily_token_budget=overrides.get("daily_token_budget", settings.llm_daily_token_budget),
        preset=settings.llm_preset,
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
        "api_key_from_env": bool(settings.llm_api_key),
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
    "clear_db_overrides",
    "effective_llm_settings",
    "read_db_overrides",
    "redact",
    "write_db_overrides",
]
