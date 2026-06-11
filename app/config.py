from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME = "SourceHero"


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("SOURCEHERO_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "postgresql://sourcehero:sourcehero_dev@localhost:5432/sourcehero")
    http_timeout: int = int(os.getenv("SOURCEHERO_HTTP_TIMEOUT", "20"))
    api_port: int = int(os.getenv("SOURCEHERO_API_PORT", "8000"))
    dashboard_port: int = int(os.getenv("SOURCEHERO_DASHBOARD_PORT", "8501"))
    user_agent: str = "SourceHeroAI/0.7.0 (+cloud)"
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    openai_model: str | None = os.getenv("OPENAI_MODEL") or None

    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    supabase_service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")

    r2_account_id: str = os.getenv("R2_ACCOUNT_ID", "")
    r2_access_key_id: str = os.getenv("R2_ACCESS_KEY_ID", "")
    r2_secret_access_key: str = os.getenv("R2_SECRET_ACCESS_KEY", "")
    r2_bucket_name: str = os.getenv("R2_BUCKET_NAME", "sourcehero-files")
    r2_public_url: str = os.getenv("R2_PUBLIC_URL", "")
    r2_endpoint: str = os.getenv("R2_ENDPOINT", "")

    cors_origins: list[str] = field(default_factory=lambda: [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ])

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_local_dev(self) -> bool:
        return self.env == "development"

    def ensure_dirs(self) -> None:
        pass


settings = Settings()
