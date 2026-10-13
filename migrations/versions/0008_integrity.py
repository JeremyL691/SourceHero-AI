"""0008_integrity: cleanup orphan rows, add FKs and unique chunk constraint."""

from __future__ import annotations

from alembic import op

revision: str = "0008_integrity"
down_revision: str | None = "0007_evals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Clean up existing orphan rows before adding foreign keys
    op.execute(
        """
        DELETE FROM claim_relations
        WHERE a_claim_id NOT IN (SELECT id FROM claims)
           OR b_claim_id NOT IN (SELECT id FROM claims);
        """
    )
    op.execute(
        """
        DELETE FROM claim_events
        WHERE claim_id NOT IN (SELECT id FROM claims);
        """
    )
    op.execute(
        """
        UPDATE claims
        SET superseded_by = NULL
        WHERE superseded_by IS NOT NULL
          AND superseded_by NOT IN (SELECT id FROM claims);
        """
    )
    op.execute(
        """
        UPDATE claims
        SET cluster_id = NULL
        WHERE cluster_id IS NOT NULL
          AND cluster_id NOT IN (SELECT id FROM claim_clusters);
        """
    )
    op.execute(
        """
        UPDATE documents
        SET current_version_id = NULL
        WHERE current_version_id IS NOT NULL
          AND current_version_id NOT IN (SELECT id FROM document_versions);
        """
    )

    # 2. Add foreign keys
    op.execute(
        """
        ALTER TABLE claim_relations
        ADD CONSTRAINT fk_claim_relations_a_claim
        FOREIGN KEY (a_claim_id) REFERENCES claims(id) ON DELETE CASCADE;
        """
    )
    op.execute(
        """
        ALTER TABLE claim_relations
        ADD CONSTRAINT fk_claim_relations_b_claim
        FOREIGN KEY (b_claim_id) REFERENCES claims(id) ON DELETE CASCADE;
        """
    )
    op.execute(
        """
        ALTER TABLE claim_events
        ADD CONSTRAINT fk_claim_events_claim
        FOREIGN KEY (claim_id) REFERENCES claims(id) ON DELETE CASCADE;
        """
    )
    op.execute(
        """
        ALTER TABLE claims
        ADD CONSTRAINT fk_claims_superseded_by
        FOREIGN KEY (superseded_by) REFERENCES claims(id) ON DELETE SET NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE claims
        ADD CONSTRAINT fk_claims_cluster_id
        FOREIGN KEY (cluster_id) REFERENCES claim_clusters(id) ON DELETE SET NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE documents
        ADD CONSTRAINT fk_documents_current_version
        FOREIGN KEY (current_version_id) REFERENCES document_versions(id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;
        """
    )

    # 3. Clean any duplicate chunks and add unique constraint on chunks(version_id, chunk_index)
    op.execute(
        """
        DELETE FROM chunks
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (PARTITION BY version_id, chunk_index ORDER BY id) AS rnum
                FROM chunks
            ) t WHERE t.rnum > 1
        );
        """
    )
    op.execute(
        """
        ALTER TABLE chunks
        ADD CONSTRAINT uq_chunks_version_chunk_index
        UNIQUE (version_id, chunk_index);
        """
    )

    # 4. Correct any reversed superseded relation directions
    op.execute(
        """
        UPDATE claim_relations cr
        SET direction = CASE
            WHEN c_a.superseded_by = cr.b_claim_id THEN 'b_to_a'
            WHEN c_b.superseded_by = cr.a_claim_id THEN 'a_to_b'
            ELSE cr.direction
        END
        FROM claims c_a, claims c_b
        WHERE cr.a_claim_id = c_a.id
          AND cr.b_claim_id = c_b.id
          AND cr.relation = 'supersedes'
          AND (c_a.superseded_by = cr.b_claim_id OR c_b.superseded_by = cr.a_claim_id);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE chunks DROP CONSTRAINT IF EXISTS uq_chunks_version_chunk_index;")
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS fk_documents_current_version;")
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS fk_claims_cluster_id;")
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS fk_claims_superseded_by;")
    op.execute("ALTER TABLE claim_events DROP CONSTRAINT IF EXISTS fk_claim_events_claim;")
    op.execute("ALTER TABLE claim_relations DROP CONSTRAINT IF EXISTS fk_claim_relations_b_claim;")
    op.execute("ALTER TABLE claim_relations DROP CONSTRAINT IF EXISTS fk_claim_relations_a_claim;")
