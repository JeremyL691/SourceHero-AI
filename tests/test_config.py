"""Settings: env parsing, derived keys, and the production guard rail."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_SECRET_KEY, Settings


def test_blank_optional_fields_fall_back_to_defaults():
    # `env_ignore_empty=True` is what makes `CLUSTER_SIM=` mean "unset".
    s = Settings(cluster_sim=None, llm_extra_headers="", cors_origins="http://a, http://b")
    assert s.cluster_sim is None
    assert s.extra_headers == {}
    assert s.cors_origin_list == ["http://a", "http://b"]


def test_defaults_match_env_example():
    """The plan requires Settings field defaults to equal .env.example exactly.

    Compares the *declared* defaults, not a constructed instance -- the test
    session sets env vars (SOURCEHERO_ENV=test, DATABASE_URL, ...) that would
    otherwise mask the defaults.
    """
    from pathlib import Path

    env_file = Path(__file__).resolve().parents[1] / ".env.example"
    expected: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        expected[k.strip()] = v.strip()

    checked = 0
    for key, value in expected.items():
        field = Settings.model_fields.get(key.lower())
        assert field is not None, f"{key} has no Settings field"
        if value == "":
            # Blank in .env.example means "unset" (or an intentionally empty string).
            assert field.default in (None, ""), f"{key} should default to unset, got {field.default!r}"
            continue
        # Compare through the same coercion pydantic would apply to the raw string,
        # so `true`/`True` and `30`/`30.0` are not false positives.
        as_text = _coerced_text(field, value)
        assert as_text == _coerced_text(field, str(field.default)), (
            f"{key}: .env.example says {value!r} but the Settings default is {field.default!r}"
        )
        checked += 1
    assert checked > 40, f"only compared {checked} keys -- the parse must be broken"


def _coerced_text(field, raw: str) -> str:
    """Render `raw` the way the field would hold it, for apples-to-apples comparison."""
    if field.annotation is bool:
        return str(raw.strip().lower() == "true")
    if field.annotation in (int, float):
        return str(type(field.default)(raw))
    if field.annotation is str or field.annotation is None:
        return raw
    return str(raw)


def test_fernet_and_jwt_keys_are_derived_from_the_secret():
    s = Settings(_env_file=None, sourcehero_secret_key="a-secret", sourcehero_env="test")
    assert s.fernet_key != Settings(sourcehero_secret_key="other").fernet_key
    assert len(s.fernet_key) == 44
    # HS256 needs >=256 bits; the derived key is 32 bytes of hex regardless of how
    # short the configured secret is.
    assert len(s.jwt_secret) == 64


def test_production_refuses_the_default_secret():
    with pytest.raises(ValidationError, match="SOURCEHERO_SECRET_KEY"):
        Settings(_env_file=None, sourcehero_env="production", sourcehero_secret_key=DEFAULT_SECRET_KEY)


def test_production_accepts_a_real_secret():
    s = Settings(_env_file=None, sourcehero_env="production", sourcehero_secret_key="not-the-default")
    assert s.sourcehero_env == "production"


def test_corrupt_json_field_is_ignored_rather_than_raising():
    s = Settings(_env_file=None, llm_extra_headers="{not json")
    assert s.extra_headers == {}
