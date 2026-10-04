"""0004_claims: the claim ledger, its clusters, relations and the event stream.

`claim_clusters.topic_id` is created here as a plain nullable column with **no**
foreign key; 0006_wiki adds the constraint once `topics` exists.
"""

from __future__ import annotations

from alembic import op

revision: str = "0004_claims"
down_revision: str | None = "0003_answers"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 384
TABLES = ("claim_events", "claim_relations", "claim_clusters", "claims")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE claims (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID NOT NULL,
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            version_id BIGINT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            -- SET NULL: re-indexing a version must not delete the claim, only its
            -- link to a chunk that no longer exists.
            chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL,
            statement TEXT NOT NULL,
            quote TEXT NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            subject TEXT NOT NULL DEFAULT '',
            predicate TEXT NOT NULL DEFAULT '',
            object TEXT NOT NULL DEFAULT '',
            qualifiers JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            claim_type VARCHAR(32) NOT NULL DEFAULT 'fact',
            numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
            polarity SMALLINT NOT NULL DEFAULT 1,
            asserted_at TIMESTAMPTZ NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            valid_to TIMESTAMPTZ,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            superseded_by BIGINT,
            cluster_id BIGINT,
            embedding vector({EMBEDDING_DIM}),
            claim_hash CHAR(64) NOT NULL,
            extractor VARCHAR(120) NOT NULL DEFAULT 'heuristic',
            prompt_version VARCHAR(40),
            confidence REAL NOT NULL DEFAULT 0.5,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_claims_identity UNIQUE (user_id, version_id, claim_hash)
        )
        """
    )
    op.execute("CREATE INDEX ix_claims_user_id ON claims (user_id)")
    op.execute("CREATE INDEX ix_claims_document_id ON claims (document_id)")
    op.execute("CREATE INDEX ix_claims_version_id ON claims (version_id)")
    op.execute("CREATE INDEX ix_claims_chunk_id ON claims (chunk_id)")
    op.execute("CREATE INDEX ix_claims_cluster_id ON claims (cluster_id)")
    op.execute("CREATE INDEX ix_claims_asserted_at ON claims (asserted_at)")
    op.execute("CREATE INDEX ix_claims_status ON claims (user_id, status)")
    op.execute(
        "CREATE INDEX ix_claims_statement_trgm ON claims USING GIN (statement gin_trgm_ops)"
    )
    op.execute(
        f"CREATE INDEX ix_claims_embedding ON claims USING HNSW (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    op.execute(
        f"""
        CREATE TABLE claim_clusters (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            label_source VARCHAR(32) NOT NULL DEFAULT 'keywords',
            centroid vector({EMBEDDING_DIM}),
            size INTEGER NOT NULL DEFAULT 0,
            doc_count INTEGER NOT NULL DEFAULT 0,
            conflict_count INTEGER NOT NULL DEFAULT 0,
            -- No foreign key yet; 0006_wiki adds it once topics exists.
            topic_id BIGINT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_claim_clusters_user_id ON claim_clusters (user_id)")
    op.execute("CREATE INDEX ix_claim_clusters_topic_id ON claim_clusters (topic_id)")
    op.execute(
        f"CREATE INDEX ix_claim_clusters_centroid ON claim_clusters "
        "USING HNSW (centroid vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    op.execute(
        """
        CREATE TABLE claim_relations (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID NOT NULL,
            a_claim_id BIGINT NOT NULL,
            b_claim_id BIGINT NOT NULL,
            relation VARCHAR(32) NOT NULL,
            direction VARCHAR(16),
            confidence REAL NOT NULL DEFAULT 0.5,
            method VARCHAR(80) NOT NULL DEFAULT 'heuristic',
            signals JSONB NOT NULL DEFAULT '{}'::jsonb,
            rationale TEXT NOT NULL DEFAULT '',
            user_label VARCHAR(32),
            labeled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_claim_relations_user_id ON claim_relations (user_id)")
    op.execute("CREATE INDEX ix_claim_relations_a_claim_id ON claim_relations (a_claim_id)")
    op.execute("CREATE INDEX ix_claim_relations_b_claim_id ON claim_relations (b_claim_id)")
    # One row per unordered pair, however many times the pipeline re-derives it.
    op.execute(
        "CREATE UNIQUE INDEX ix_claim_relations_pair "
        "ON claim_relations (user_id, a_claim_id, b_claim_id)"
    )
    op.execute(
        "CREATE INDEX ix_claim_relations_unresolved "
        "ON claim_relations (user_id, relation) WHERE resolved_at IS NULL"
    )

    op.execute(
        """
        CREATE TABLE claim_events (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID NOT NULL,
            claim_id BIGINT NOT NULL,
            event VARCHAR(32) NOT NULL,
            at TIMESTAMPTZ NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            related_claim_id BIGINT,
            version_id BIGINT,
            meta JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_claim_events_user_id ON claim_events (user_id)")
    op.execute("CREATE INDEX ix_claim_events_claim_id ON claim_events (claim_id)")
    # The timeline and belief diff both read by (user, world time).
    op.execute("CREATE INDEX ix_claim_events_user_at ON claim_events (user_id, at)")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")