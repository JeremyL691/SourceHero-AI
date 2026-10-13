"""0007_evals: a record of each evaluation run."""

from __future__ import annotations

from alembic import op

revision: str = "0007_evals"
down_revision: str | None = "0006_wiki"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eval_runs (
            id SERIAL PRIMARY KEY,
            suite VARCHAR(120) NOT NULL,
            mode VARCHAR(32) NOT NULL DEFAULT 'offline',
            metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            report_path TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_eval_runs_created_at ON eval_runs (created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eval_runs")
