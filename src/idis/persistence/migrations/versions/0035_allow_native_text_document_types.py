"""Allow truthful HTML and TEXT parsed-document types.

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-16

The parser has native HTML/TEXT support.  Persisting either result as PDF is
false provenance, so Task 4 widens only the existing document-type check.  A
downgrade refuses to relabel or strand truthful HTML/TEXT rows.
"""

from __future__ import annotations

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


_PRE_TASK4_TYPES = "'PDF', 'PPTX', 'XLSX', 'DOCX', 'AUDIO', 'VIDEO', 'IMAGE'"
_TASK4_TYPES = f"{_PRE_TASK4_TYPES}, 'HTML', 'TEXT'"


def _replace_constraint(*, allowed_types: str) -> None:
    op.execute("ALTER TABLE documents DROP CONSTRAINT valid_document_doc_type")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT valid_document_doc_type "
        f"CHECK (doc_type IN ({allowed_types}))"
    )


def upgrade() -> None:
    """Admit the two native text parser identities without changing any row."""
    _replace_constraint(allowed_types=_TASK4_TYPES)


def downgrade() -> None:
    """Restore the prior constraint only when no truthful row would be lost."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM documents
                WHERE doc_type IN ('HTML', 'TEXT')
            ) THEN
                RAISE EXCEPTION
                    'TASK4_NATIVE_DOCUMENT_TYPES_BLOCK_DOWNGRADE: HTML/TEXT rows exist'
                    USING ERRCODE = '23514';
            END IF;
        END
        $$
        """
    )
    _replace_constraint(allowed_types=_PRE_TASK4_TYPES)
