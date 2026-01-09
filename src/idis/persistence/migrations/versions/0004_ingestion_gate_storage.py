"""Ingestion Gate storage: document_artifacts, documents, document_spans tables with RLS.

Revision ID: 0004
Revises: 0003
Create Date: 2026-01-09

Phase 3.1 Task 1.1: Storage primitives for ingestion pipeline.
Creates tenant-scoped tables aligned to OpenAPI v6.3 DocumentArtifact schema:
- document_artifacts: Document metadata (OpenAPI DocumentArtifact)
- documents: Parsed representation state
- document_spans: Evidence spans with stable locators

RLS:
- NULLIF hardening consistent with Phase 2.10.8
- Fail-closed: SELECT returns 0 rows when tenant unset
- Fail-closed: INSERT/UPDATE blocked by WITH CHECK

Foreign Keys:
- document_artifacts.deal_id -> deals(deal_id)
- documents.deal_id -> deals(deal_id)
- documents.doc_id -> document_artifacts(doc_id)
- document_spans.document_id -> documents(document_id) ON DELETE CASCADE
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ingestion gate tables with RLS policies."""

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_artifacts (
            doc_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            doc_type TEXT NOT NULL,
            title TEXT NOT NULL,
            source_system TEXT NOT NULL,
            version_id TEXT NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sha256 TEXT,
            uri TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_doc_type CHECK (
                doc_type IN ('PITCH_DECK', 'FINANCIAL_MODEL', 'DATA_ROOM_FILE',
                             'TRANSCRIPT', 'TERM_SHEET', 'OTHER')
            )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_artifacts_tenant_deal
        ON document_artifacts (tenant_id, deal_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_artifacts_sha256
        ON document_artifacts (sha256)
        WHERE sha256 IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            document_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            doc_id UUID NOT NULL REFERENCES document_artifacts(doc_id),
            doc_type TEXT NOT NULL,
            parse_status TEXT NOT NULL DEFAULT 'PENDING',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_document_doc_type CHECK (
                doc_type IN ('PDF', 'PPTX', 'XLSX', 'DOCX', 'AUDIO', 'VIDEO')
            ),
            CONSTRAINT valid_parse_status CHECK (
                parse_status IN ('PENDING', 'PARSED', 'FAILED')
            )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_documents_tenant_deal
        ON documents (tenant_id, deal_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_documents_doc_id
        ON documents (doc_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_documents_parse_status
        ON documents (tenant_id, parse_status)
        WHERE parse_status = 'PENDING'
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_spans (
            span_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            document_id UUID NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
            span_type TEXT NOT NULL,
            locator JSONB NOT NULL,
            text_excerpt TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_span_type CHECK (
                span_type IN ('PAGE_TEXT', 'PARAGRAPH', 'CELL', 'TIMECODE')
            )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_spans_tenant_doc
        ON document_spans (tenant_id, document_id)
        """
    )

    op.execute("ALTER TABLE document_artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_artifacts FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE document_spans ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_spans FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY document_artifacts_tenant_isolation ON document_artifacts
        USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        """
    )

    op.execute(
        """
        CREATE POLICY documents_tenant_isolation ON documents
        USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        """
    )

    op.execute(
        """
        CREATE POLICY document_spans_tenant_isolation ON document_spans
        USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    """Drop ingestion gate tables and RLS policies."""

    op.execute("DROP POLICY IF EXISTS document_spans_tenant_isolation ON document_spans")
    op.execute("DROP POLICY IF EXISTS documents_tenant_isolation ON documents")
    op.execute("DROP POLICY IF EXISTS document_artifacts_tenant_isolation ON document_artifacts")

    op.execute("DROP TABLE IF EXISTS document_spans")
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP TABLE IF EXISTS document_artifacts")
