"""Ingestion Gate storage: deal_artifacts, documents, document_spans tables with RLS.

Revision ID: 0004
Revises: 0003
Create Date: 2026-01-09

Phase 3.1 Task 1.1: Storage primitives for ingestion pipeline.
Creates tenant-scoped tables per Data Model §3.2-3.3:
- deal_artifacts: Raw artifact metadata (files/connectors)
- documents: Parsed representation state
- document_spans: Evidence spans with stable locators

RLS:
- NULLIF hardening consistent with Phase 2.10.8
- Fail-closed: SELECT returns 0 rows when tenant unset
- Fail-closed: INSERT/UPDATE blocked by WITH CHECK

Foreign Keys:
- deal_artifacts.deal_id -> deals(deal_id)
- documents.deal_id -> deals(deal_id)
- documents.artifact_id -> deal_artifacts(artifact_id)
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
        CREATE TABLE IF NOT EXISTS deal_artifacts (
            artifact_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            artifact_type TEXT NOT NULL,
            storage_uri TEXT NOT NULL,
            connector_type TEXT,
            connector_ref TEXT,
            sha256 TEXT NOT NULL,
            version_label TEXT,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_artifact_type CHECK (
                artifact_type IN ('PITCH_DECK', 'FIN_MODEL', 'DATA_ROOM', 'TRANSCRIPT', 'NOTE')
            ),
            CONSTRAINT valid_connector_type CHECK (
                connector_type IS NULL OR
                connector_type IN ('DocSend', 'Drive', 'Dropbox', 'SharePoint', 'Upload')
            )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_deal_artifacts_tenant_deal
        ON deal_artifacts (tenant_id, deal_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_deal_artifacts_sha256
        ON deal_artifacts (sha256)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            document_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            artifact_id UUID NOT NULL REFERENCES deal_artifacts(artifact_id),
            doc_type TEXT NOT NULL,
            parse_status TEXT NOT NULL DEFAULT 'PENDING',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_doc_type CHECK (
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
        CREATE INDEX IF NOT EXISTS idx_documents_artifact
        ON documents (artifact_id)
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

    op.execute("ALTER TABLE deal_artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE deal_artifacts FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE document_spans ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_spans FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY deal_artifacts_tenant_isolation ON deal_artifacts
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
    op.execute("DROP POLICY IF EXISTS deal_artifacts_tenant_isolation ON deal_artifacts")

    op.execute("DROP TABLE IF EXISTS document_spans")
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP TABLE IF EXISTS deal_artifacts")
