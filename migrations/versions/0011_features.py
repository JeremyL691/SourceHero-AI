"""0011_features: source reliability and read-only share links."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_features"
down_revision: str | None = "0010_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("reliability", sa.String(16), nullable=False, server_default="unknown"),
    )
    op.create_check_constraint(
        "ck_sources_reliability",
        "sources",
        "reliability IN ('unknown', 'low', 'medium', 'high')",
    )
    op.add_column("sources", sa.Column("reliability_note", sa.Text(), nullable=True))
    op.create_table(
        "share_links",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('wiki_page', 'answer')", name="ck_share_links_kind"),
    )


def downgrade() -> None:
    op.drop_table("share_links")
    op.drop_column("sources", "reliability_note")
    op.drop_constraint("ck_sources_reliability", "sources", type_="check")
    op.drop_column("sources", "reliability")
