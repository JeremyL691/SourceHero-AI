"""0005_temporal: watches and belief diffs.

`claims.status` and `claim_events.observed_at` were already created in 0004_claims
with the temporal model in mind; nothing here duplicates them.
"""

from __future__ import annotations

from alembic import op

revision: str = "0005_temporal"
down_revision: str | None = "0004_claims"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 384
TABLES = ("belief_diffs", "watches")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE watches (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            kind VARCHAR(32) NOT NULL,
            source_id UUID REFERENCES sources(id) ON DELETE CASCADE,
            query TEXT,
            -- Lets a query watch find similar claims by kNN instead of text match.
            embedding vector({EMBEDDING_DIM}),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_checked_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_watches_user_id ON watches (user_id)")
    op.execute("CREATE INDEX ix_watches_source_id ON watches (source_id)")
    op.execute("CREATE INDEX ix_watches_user_kind ON watches (user_id, kind)")

    op.execute(
        """
        CREATE TABLE belief_diffs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            scope_kind VARCHAR(32) NOT NULL DEFAULT 'all',
            scope_id UUID,
            period_start TIMESTAMPTZ NOT NULL,
            period_end TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            digest_md TEXT NOT NULL DEFAULT '',
            digest_method VARCHAR(120) NOT NULL DEFAULT 'template',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_belief_diffs_user_id ON belief_diffs (user_id)")
    op.execute(
        "CREATE INDEX ix_belief_diffs_period ON belief_diffs (user_id, period_start, period_end)"
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
