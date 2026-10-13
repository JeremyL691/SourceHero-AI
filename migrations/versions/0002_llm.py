"""0002_llm: provider credentials, OAuth state, the call log and the response cache."""

from __future__ import annotations

from alembic import op

revision: str = "0002_llm"
down_revision: str | None = "0001_baseline"
branch_labels = None
depends_on = None

TABLES = ("llm_cache", "llm_calls", "oauth_states", "provider_credentials")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE provider_credentials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider VARCHAR(64) NOT NULL,
            kind VARCHAR(32) NOT NULL,
            encrypted_payload BYTEA NOT NULL,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # One credential of each kind per provider, replaced rather than accumulated.
    op.execute(
        "CREATE UNIQUE INDEX uq_provider_credentials_kind "
        "ON provider_credentials (provider, kind)"
    )

    op.execute(
        """
        CREATE TABLE oauth_states (
            state VARCHAR(128) PRIMARY KEY,
            code_verifier TEXT NOT NULL,
            provider VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_oauth_states_created_at ON oauth_states (created_at)")

    op.execute(
        """
        CREATE TABLE llm_calls (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID,
            task VARCHAR(80) NOT NULL,
            provider VARCHAR(64) NOT NULL,
            model VARCHAR(120) NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
            status VARCHAR(32) NOT NULL DEFAULT 'ok',
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_llm_calls_user_id ON llm_calls (user_id)")
    # The usage view groups by (task, day) and scans recency.
    op.execute("CREATE INDEX ix_llm_calls_task_created ON llm_calls (task, created_at)")

    op.execute(
        """
        CREATE TABLE llm_cache (
            key CHAR(64) PRIMARY KEY,
            task VARCHAR(80) NOT NULL,
            model VARCHAR(120) NOT NULL,
            response JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")