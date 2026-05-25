"""Slice 62 vector embeddings table with pgvector and tenant RLS.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# Keep aligned with src/idis/services/rag/constants.py VECTOR_EMBEDDING_DIMENSIONS.
VECTOR_EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    """Enable pgvector and create tenant-scoped vector_embeddings storage."""
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS vector_embeddings (
            embedding_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            run_id UUID,
            source_type TEXT NOT NULL,
            source_id UUID NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dimensions INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            embedding vector({VECTOR_EMBEDDING_DIMENSIONS}) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT vector_embeddings_unique_source UNIQUE (
                tenant_id, source_type, source_id, content_hash
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vector_embeddings_tenant_deal
        ON vector_embeddings (tenant_id, deal_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vector_embeddings_hnsw
        ON vector_embeddings
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute("ALTER TABLE vector_embeddings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vector_embeddings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY vector_embeddings_tenant_isolation ON vector_embeddings
        USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    """Drop vector_embeddings table. Extension may remain installed."""
    op.execute("DROP POLICY IF EXISTS vector_embeddings_tenant_isolation ON vector_embeddings")
    op.execute("DROP TABLE IF EXISTS vector_embeddings")
