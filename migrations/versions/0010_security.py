"""0010_security: token versions for logout-all, and the rate-limit window table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010_security"
down_revision: str | None = "0009_revision_method"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("key", "window_start", name="pk_rate_limits"),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")
    op.drop_column("users", "token_version")
