"""0003_answers: stored answers and verification records."""

from __future__ import annotations

from alembic import op

revision: str = "0003_answers"
down_revision: str | None = "0002_llm"
branch_labels = None
depends_on = None

TABLES = ("verifications", "answers")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE answers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            question TEXT NOT NULL,
            answer_md TEXT NOT NULL DEFAULT '',
            sentences JSONB NOT NULL DEFAULT '[]'::jsonb,
            citations JSONB NOT NULL DEFAULT '[]'::jsonb,
            retrieval JSONB NOT NULL DEFAULT '{}'::jsonb,
            method VARCHAR(120) NOT NULL DEFAULT 'heuristic',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_answers_user_id ON answers (user_id)")
    op.execute("CREATE INDEX ix_answers_created_at ON answers (created_at)")

    op.execute(
        """
        CREATE TABLE verifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            input_text TEXT NOT NULL,
            result JSONB NOT NULL DEFAULT '{}'::jsonb,
            method VARCHAR(120) NOT NULL DEFAULT 'heuristic',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_verifications_user_id ON verifications (user_id)")
    op.execute("CREATE INDEX ix_verifications_created_at ON verifications (created_at)")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")