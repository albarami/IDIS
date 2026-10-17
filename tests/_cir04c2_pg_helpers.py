"""Real synthetic storage parents for legacy PostgreSQL compatibility tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Connection, text

from idis.persistence.repositories.documents import PostgresDocumentsRepository


def create_sanad_evidence_parents(
    conn: Connection, *, tenant_id: str, deal_id: str, claim_id: str
) -> tuple[str, str]:
    """Create two real same-scope evidence rows, without native admission claims."""
    artifact_id, document_id, span_id, primary_id, corroborating_id = (
        str(uuid.uuid4()) for _ in range(5)
    )
    documents = PostgresDocumentsRepository(conn, tenant_id)
    documents.create_artifact(
        doc_id=artifact_id,
        deal_id=deal_id,
        doc_type="DATA_ROOM_FILE",
        title="Synthetic Sanad Source",
        source_system="synthetic-c2",
        version_id="v1",
        ingested_at=datetime.now(UTC),
        sha256="a" * 64,
        uri=None,
    )
    documents.create_document(
        document_id=document_id,
        deal_id=deal_id,
        doc_id=artifact_id,
        doc_type="PDF",
        parse_status="PARSED",
    )
    documents.create_document_span(
        span_id=span_id,
        deal_id=deal_id,
        document_id=document_id,
        span_type="PAGE_TEXT",
        locator={"page": 1},
        text_excerpt="Synthetic evidence",
        content_hash="a" * 64,
    )
    for evidence_id in (primary_id, corroborating_id):
        conn.execute(
            text(
                "INSERT INTO evidence_items "
                "(evidence_id,tenant_id,deal_id,claim_id,source_span_id,source_grade) "
                "VALUES (CAST(:evidence_id AS uuid),CAST(:tenant_id AS uuid),"
                "CAST(:deal_id AS uuid),CAST(:claim_id AS uuid),CAST(:span_id AS uuid),'B')"
            ),
            {
                "evidence_id": evidence_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "claim_id": claim_id,
                "span_id": span_id,
            },
        )
    return primary_id, corroborating_id
