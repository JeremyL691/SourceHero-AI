"""0006_wiki: topics, pages, revisions, provenance, links, lint findings.

Also adds the `claim_clusters.topic_id` foreign key, which could not be declared in
0004 because `topics` did not exist yet.
"""

from __future__ import annotations

from alembic import op

revision: str = "0006_wiki"
down_revision: str | None = "0005_temporal"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 384
TABLES = (
    "lint_findings",
    "wiki_links",
    "wiki_page_claims",
    "wiki_page_revisions",
    "wiki_pages",
    "topics",
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE topics (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID NOT NULL,
            slug VARCHAR(160) NOT NULL,
            label TEXT NOT NULL,
            centroid vector({EMBEDDING_DIM}),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_topics_user_slug UNIQUE (user_id, slug)
        )
        """
    )
    op.execute("CREATE INDEX ix_topics_user_id ON topics (user_id)")
    op.execute(
        "CREATE INDEX ix_topics_centroid ON topics USING HNSW (centroid vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    op.execute(
        """
        CREATE TABLE wiki_pages (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID NOT NULL,
            topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            slug VARCHAR(160) NOT NULL,
            title TEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'dirty',
            body JSONB NOT NULL DEFAULT '{}'::jsonb,
            body_md TEXT NOT NULL DEFAULT '',
            input_hash CHAR(64) NOT NULL DEFAULT '',
            compiled_input JSONB NOT NULL DEFAULT '{}'::jsonb,
            revision INTEGER NOT NULL DEFAULT 0,
            compiled_at TIMESTAMPTZ,
            compile_method VARCHAR(80) NOT NULL DEFAULT '',
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_wiki_pages_user_slug UNIQUE (user_id, slug)
        )
        """
    )
    op.execute("CREATE INDEX ix_wiki_pages_user_id ON wiki_pages (user_id)")
    op.execute("CREATE INDEX ix_wiki_pages_topic_id ON wiki_pages (topic_id)")
    # The compile loop only ever looks at pages that still need work.
    op.execute("CREATE INDEX ix_wiki_pages_status ON wiki_pages (user_id, status)")

    op.execute(
        """
        CREATE TABLE wiki_page_revisions (
            id BIGSERIAL PRIMARY KEY,
            page_id BIGINT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,
            body JSONB NOT NULL DEFAULT '{}'::jsonb,
            body_md TEXT NOT NULL DEFAULT '',
            input_hash CHAR(64) NOT NULL DEFAULT '',
            change_summary TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_wiki_revisions UNIQUE (page_id, revision)
        )
        """
    )
    op.execute("CREATE INDEX ix_wiki_page_revisions_page_id ON wiki_page_revisions (page_id)")

    op.execute(
        """
        CREATE TABLE wiki_page_claims (
            page_id BIGINT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
            claim_id BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            PRIMARY KEY (page_id, claim_id)
        )
        """
    )
    # The reverse lookup ("which pages cite this claim?") is the point of the table.
    op.execute("CREATE INDEX ix_wiki_page_claims_claim ON wiki_page_claims (claim_id)")

    op.execute(
        """
        CREATE TABLE wiki_links (
            from_page_id BIGINT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
            to_page_id BIGINT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
            anchor TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (from_page_id, to_page_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE lint_findings (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID NOT NULL,
            page_id BIGINT REFERENCES wiki_pages(id) ON DELETE CASCADE,
            kind VARCHAR(64) NOT NULL,
            severity VARCHAR(16) NOT NULL DEFAULT 'info',
            detail JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_lint_findings_user_id ON lint_findings (user_id)")
    op.execute("CREATE INDEX ix_lint_findings_page_id ON lint_findings (page_id)")
    op.execute(
        "CREATE INDEX ix_lint_findings_user_severity ON lint_findings (user_id, severity)"
    )

    # `claim_clusters.topic_id` existed as a plain column in 0004; now it can point home.
    op.execute(
        "ALTER TABLE claim_clusters ADD CONSTRAINT fk_claim_clusters_topic "
        "FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE claim_clusters DROP CONSTRAINT IF EXISTS fk_claim_clusters_topic")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
