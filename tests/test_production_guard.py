"""Production guard rails: refuse unsafe settings, and flip risky switches off by default."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_SECRET_KEY, Settings

GOOD_SECRET = "k7-production-secret-that-is-long-enough-32b"
GOOD_DB_URL = "postgresql+psycopg://sourcehero:r3al-pr0d-pass@db.internal:5432/sourcehero"


@pytest.fixture(autouse=True)
def _clean_guard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicit env values count as "set" for the guard, so keep the shell from leaking in.
    monkeypatch.delenv("ALLOW_REGISTRATION", raising=False)
    monkeypatch.delenv("DOCS_ENABLED", raising=False)


def _prod(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "sourcehero_env": "production",
        "sourcehero_secret_key": GOOD_SECRET,
        "database_url": GOOD_DB_URL,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_production_refuses_a_secret_shorter_than_32_bytes():
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        _prod(sourcehero_secret_key="x" * 31)


def test_secret_length_is_counted_in_utf8_bytes_not_characters():
    # 16 characters but 32 bytes: passes the byte rule.
    assert len(_prod(sourcehero_secret_key="é" * 16).sourcehero_secret_key) == 16
    # 16 two-byte characters is exactly 32 bytes, 15 is 30 bytes and must fail.
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        _prod(sourcehero_secret_key="é" * 15)


def test_production_refuses_the_development_database_password():
    dev_url = "postgresql+psycopg://sourcehero:sourcehero_dev@db.internal:5432/sourcehero"
    with pytest.raises(ValidationError, match="DATABASE_URL") as info:
        _prod(database_url=dev_url)
    # The message names the variable but never echoes the password.
    assert "sourcehero_dev" not in str(info.value)


def test_production_accepts_a_real_database_password():
    assert _prod().database_url == GOOD_DB_URL


def test_production_rejects_an_unparseable_database_url_with_a_named_error():
    with pytest.raises(ValidationError, match="DATABASE_URL is not a valid database URL"):
        _prod(database_url="not a url at all: [")


def test_registration_defaults_to_off_in_production():
    assert _prod().allow_registration is False


def test_explicit_registration_value_is_respected_in_production():
    assert _prod(allow_registration=True).allow_registration is True


def test_explicit_registration_env_is_respected_in_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_REGISTRATION", "true")
    s = Settings(
        _env_file=None,
        sourcehero_env="production",
        sourcehero_secret_key=GOOD_SECRET,
        database_url=GOOD_DB_URL,
    )
    assert s.allow_registration is True


def test_registration_default_stays_on_outside_production():
    s = Settings(_env_file=None, sourcehero_env="development")
    assert s.allow_registration is True


def test_docs_default_to_off_in_production():
    assert _prod().docs_enabled is False


def test_explicit_docs_true_turns_docs_on_in_production(monkeypatch: pytest.MonkeyPatch):
    assert _prod(docs_enabled=True).docs_enabled is True
    monkeypatch.setenv("DOCS_ENABLED", "true")
    s = Settings(
        _env_file=None,
        sourcehero_env="production",
        sourcehero_secret_key=GOOD_SECRET,
        database_url=GOOD_DB_URL,
    )
    assert s.docs_enabled is True


def test_docs_default_stays_on_outside_production():
    assert Settings(_env_file=None, sourcehero_env="test").docs_enabled is True


def test_default_secret_is_still_refused_before_the_length_rule():
    with pytest.raises(ValidationError, match="shipped default"):
        _prod(sourcehero_secret_key=DEFAULT_SECRET_KEY)
