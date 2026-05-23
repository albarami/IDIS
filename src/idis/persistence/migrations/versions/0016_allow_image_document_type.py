"""Allow IMAGE parsed document type for OCR image ingestion.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-23
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow persisted parser documents to represent image OCR sources."""
    op.execute(
        """
        ALTER TABLE documents
        DROP CONSTRAINT IF EXISTS valid_document_doc_type
        """
    )
    op.execute(
        """
        ALTER TABLE documents
        ADD CONSTRAINT valid_document_doc_type CHECK (
            doc_type IN ('PDF', 'PPTX', 'XLSX', 'DOCX', 'AUDIO', 'VIDEO', 'IMAGE')
        )
        """
    )


def downgrade() -> None:
    """Restore the pre-Slice58 parsed document type constraint."""
    op.execute(
        """
        ALTER TABLE documents
        DROP CONSTRAINT IF EXISTS valid_document_doc_type
        """
    )
    op.execute(
        """
        ALTER TABLE documents
        ADD CONSTRAINT valid_document_doc_type CHECK (
            doc_type IN ('PDF', 'PPTX', 'XLSX', 'DOCX', 'AUDIO', 'VIDEO')
        )
        """
    )
