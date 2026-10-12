"""Application settings, loaded from the environment / `.env`.

Every field here has the same name (lower snake_case) as its key in `.env.example`,
and the default below is identical to the value in `.env.example`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET_KEY = "dev-insecure-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    # --- core ---
    sourcehero_env: Literal["development", "test", "production"] = "development"
    sourcehero_secret_key: str = DEFAULT_SECRET_KEY
    db_port: int = 54339
    api_port: int = 8787
    web_port: int = 3787
    database_url: str = "postgresql+psycopg://sourcehero:sourcehero_dev@127.0.0.1:54339/sourcehero"
    test_database_url: str = "postgresql+psycopg://sourcehero:sourcehero_dev@127.0.0.1:54339/sourcehero_test"
    eval_database_url: str = "postgresql+psycopg://sourcehero:sourcehero_dev@127.0.0.1:54339/sourcehero_eval"
    e2e_database_url: str = "postgresql+psycopg://sourcehero:sourcehero_dev@127.0.0.1:54339/sourcehero_e2e"
    e2e_api_port: int = 8797
    e2e_web_port: int = 3797
    # Kept as str on purpose: pydantic would try to JSON-parse a comma-separated list.
    cors_origins: str = "http://127.0.0.1:3787,http://localhost:3787"
    public_web_url: str = "http://localhost:3787"
    data_dir: str = "./data"

    # --- auth ---
    auth_mode: Literal["local", "supabase"] = "local"
    allow_registration: bool = True
    jwt_ttl_hours: int = 168
    supabase_url: str = ""
    supabase_jwt_audience: str = "authenticated"

    # --- storage ---
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket: str = "sourcehero-files"
    s3_region: str = "auto"

    # --- ingestion ---
    fetch_allow_private: bool = False
    http_timeout_seconds: float = 20.0
    http_max_bytes: int = 10485760
    respect_robots: bool = True
    per_host_min_interval_seconds: float = 1.0
    user_agent: str = "SourceHero/2.0 (+https://github.com/JeremyL691/SourceHero-AI)"

    # --- jobs ---
    jobs_inline: bool = False
    worker_concurrency: int = 2
    worker_poll_seconds: float = 1.0
    job_stale_seconds: int = 300
    job_debounce_scale: float = 1.0
    scheduler_tick_seconds: float = 30.0
    weekly_diff_day: int = 0
    weekly_diff_time: str = "08:00"

    # --- embeddings / retrieval ---
    embedding_provider: Literal["hashing", "fastembed", "openai_compat"] = "hashing"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    embedding_cache_dir: str = "./.cache/fastembed"
    rerank_provider: Literal["none", "fastembed"] = "none"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    retrieval_candidates: int = 50
    rrf_k: int = 60

    # --- claim thresholds; blank means "use the per-embedder default in code" ---
    cluster_sim: float | None = None
    pair_sim: float | None = None
    dup_sim: float | None = None
    carry_sim: float | None = None
    topic_sim: float | None = None
    max_judge_pairs_per_claim: int = 5

    # --- llm ---
    llm_provider: Literal["fake", "openai_chat", "openai_responses", "anthropic_messages"] = "fake"
    llm_preset: str = ""
    llm_base_url: str = ""
    llm_auth: Literal["api_key", "command", "oauth"] = "api_key"
    llm_api_key: str = ""
    llm_token_command: str = ""
    llm_token_ttl_seconds: int = 300
    llm_extra_headers: str = ""  # JSON object, parsed in code
    llm_model_fast: str = ""
    llm_model_smart: str = ""
    llm_json_mode: Literal["auto", "json_object", "prompt"] = "auto"
    llm_auth_header: Literal["authorization", "x-api-key"] = "authorization"
    llm_max_concurrency: int = 4
    llm_timeout_seconds: float = 120.0
    llm_daily_token_budget: int = 2000000
    llm_cassette_mode: Literal["off", "replay", "record"] = "off"  # K1.4: offline record and replay
    llm_cassette_path: str = ""
    openrouter_api_key: str = ""  # K3.1: used when the openrouter preset has no llm_api_key
    llm_extra_body: str = ""  # JSON object merged into every request body (K3.1)
    llm_allow_paid: bool = False  # the free-model guard refuses paid models unless this is set
    llm_daily_request_budget: int = 0  # requests per UTC day across the instance; 0 means unlimited

    # --- oauth ---
    oauth_authorize_url: str = ""
    oauth_token_url: str = ""
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_scopes: str = ""
    oauth_redirect_uri: str = "http://localhost:8787/v1/settings/llm/oauth/callback"
    oauth_extra_authorize_params: str = ""  # JSON object, parsed in code

    @model_validator(mode="after")
    def validate_production(self) -> Settings:
        """Refuse to boot production with the shipped default secret."""
        if self.sourcehero_env == "production" and self.sourcehero_secret_key == DEFAULT_SECRET_KEY:
            raise ValueError(
                "SOURCEHERO_SECRET_KEY is still the shipped default. "
                "Set a strong random value before running in production."
            )
        return self

    # --- derived helpers -------------------------------------------------

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def fernet_key(self) -> bytes:
        digest = hashlib.sha256(self.sourcehero_secret_key.encode() + b"fernet").digest()
        return base64.urlsafe_b64encode(digest)

    @property
    def jwt_secret(self) -> str:
        """Derive a 32-byte HMAC key from the configured secret.

        HS256 wants at least 256 bits (RFC 7518 3.2); the shipped dev default is
        shorter, so hash rather than warning on every single token.
        """
        return hashlib.sha256(self.sourcehero_secret_key.encode() + b"jwt").hexdigest()

    def json_object(self, field: str) -> dict:
        """Parse a JSON-object-valued env field, tolerating blank/invalid input."""
        raw = getattr(self, field, "") or ""
        if not raw.strip():
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @property
    def extra_headers(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self.json_object("llm_extra_headers").items()}

    @property
    def extra_authorize_params(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self.json_object("oauth_extra_authorize_params").items()}

    @property
    def is_test(self) -> bool:
        return self.sourcehero_env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Tests and settings mutations call this so the next read re-parses `.env`."""
    get_settings.cache_clear()


__all__ = ["DEFAULT_SECRET_KEY", "Settings", "get_settings", "reset_settings_cache"]
