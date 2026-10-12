"""0009_revision_method: record how each wiki revision was compiled."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009_revision_method"
down_revision: str | None = "0008_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wiki_page_revisions", sa.Column("compile_method", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("wiki_page_revisions", "compile_method")
