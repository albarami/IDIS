"""Add deal scope and content hash to document_spans.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-08
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add run-loader scope fields for persisted document spans."""
    op.execute("ALTER TABLE document_spans ADD COLUMN IF NOT EXISTS deal_id UUID")
    op.execute("ALTER TABLE document_spans ADD COLUMN IF NOT EXISTS content_hash TEXT")
    op.execute(
        """
        UPDATE document_spans AS spans
        SET deal_id = documents.deal_id
        FROM documents
        WHERE spans.document_id = documents.document_id
          AND spans.deal_id IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE document_spans
        ADD CONSTRAINT document_spans_deal_id_fkey
        FOREIGN KEY (deal_id) REFERENCES deals(deal_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_spans_tenant_deal_doc
        ON document_spans (tenant_id, deal_id, document_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_spans_content_hash
        ON document_spans (tenant_id, content_hash)
        WHERE content_hash IS NOT NULL
        """
    )


def downgrade() -> None:
    """Remove run-loader scope fields from document_spans."""
    op.execute("DROP INDEX IF EXISTS idx_document_spans_content_hash")
    op.execute("DROP INDEX IF EXISTS idx_document_spans_tenant_deal_doc")
    op.execute(
        """
        ALTER TABLE document_spans
        DROP CONSTRAINT IF EXISTS document_spans_deal_id_fkey
        """
    )
    op.execute("ALTER TABLE document_spans DROP COLUMN IF EXISTS content_hash")
    op.execute("ALTER TABLE document_spans DROP COLUMN IF EXISTS deal_id")
