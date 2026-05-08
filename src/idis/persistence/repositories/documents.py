"""Document repositories for persisted ingestion corpus.

Provides tenant-scoped Postgres access to document artifacts, parsed documents,
and document spans. All operations rely on RLS through SET LOCAL tenant context.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from idis.persistence.db import set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection


class PostgresDocumentsRepository:
    """Tenant-scoped repository for parsed ingestion corpus records."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with a connection and tenant context."""
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create_artifact(
        self,
        *,
        doc_id: str,
        deal_id: str,
        doc_type: str,
        title: str,
        source_system: str,
        version_id: str,
        ingested_at: datetime,
        sha256: str | None,
        uri: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a document artifact row."""
        now = datetime.now(UTC)
        payload = metadata or {}
        self._conn.execute(
            text(
                """
                INSERT INTO document_artifacts (
                    doc_id, tenant_id, deal_id, doc_type, title, source_system,
                    version_id, ingested_at, sha256, uri, metadata, created_at, updated_at
                )
                VALUES (
                    :doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                    :version_id, :ingested_at, :sha256, :uri, CAST(:metadata AS JSONB),
                    :created_at, :updated_at
                )
                """
            ),
            {
                "doc_id": doc_id,
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "doc_type": doc_type,
                "title": title,
                "source_system": source_system,
                "version_id": version_id,
                "ingested_at": ingested_at,
                "sha256": sha256,
                "uri": uri,
                "metadata": json.dumps(payload),
                "created_at": now,
                "updated_at": now,
            },
        )
        return {
            "doc_id": doc_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "doc_type": doc_type,
            "title": title,
            "source_system": source_system,
            "version_id": version_id,
            "ingested_at": ingested_at.isoformat().replace("+00:00", "Z"),
            "sha256": sha256,
            "uri": uri,
            "metadata": payload,
        }

    def create_document(
        self,
        *,
        document_id: str,
        deal_id: str,
        doc_id: str,
        doc_type: str,
        parse_status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a parsed document row."""
        now = datetime.now(UTC)
        payload = metadata or {}
        self._conn.execute(
            text(
                """
                INSERT INTO documents (
                    document_id, tenant_id, deal_id, doc_id, doc_type,
                    parse_status, metadata, created_at, updated_at
                )
                VALUES (
                    :document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                    :parse_status, CAST(:metadata AS JSONB), :created_at, :updated_at
                )
                """
            ),
            {
                "document_id": document_id,
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "doc_id": doc_id,
                "doc_type": doc_type,
                "parse_status": parse_status,
                "metadata": json.dumps(payload),
                "created_at": now,
                "updated_at": now,
            },
        )
        return {
            "document_id": document_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "doc_id": doc_id,
            "doc_type": doc_type,
            "parse_status": parse_status,
            "metadata": payload,
        }

    def create_document_span(
        self,
        *,
        span_id: str,
        deal_id: str,
        document_id: str,
        span_type: str,
        locator: dict[str, Any],
        text_excerpt: str | None,
        content_hash: str | None,
    ) -> dict[str, Any]:
        """Create one document span row."""
        now = datetime.now(UTC)
        self._conn.execute(
            text(
                """
                INSERT INTO document_spans (
                    span_id, tenant_id, deal_id, document_id, span_type,
                    locator, text_excerpt, content_hash, created_at, updated_at
                )
                VALUES (
                    :span_id, :tenant_id, :deal_id, :document_id, :span_type,
                    CAST(:locator AS JSONB), :text_excerpt, :content_hash,
                    :created_at, :updated_at
                )
                """
            ),
            {
                "span_id": span_id,
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "document_id": document_id,
                "span_type": span_type,
                "locator": json.dumps(locator),
                "text_excerpt": text_excerpt,
                "content_hash": content_hash,
                "created_at": now,
                "updated_at": now,
            },
        )
        return {
            "span_id": span_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "document_id": document_id,
            "span_type": span_type,
            "locator": locator,
            "text_excerpt": text_excerpt,
            "content_hash": content_hash,
        }

    def list_documents_by_deal(
        self,
        deal_id: str,
        *,
        parsed_only: bool = True,
    ) -> list[dict[str, Any]]:
        """List documents for a deal in deterministic order."""
        result = self._conn.execute(
            text(
                """
                SELECT documents.document_id, documents.tenant_id, documents.deal_id,
                       documents.doc_id, documents.doc_type, documents.parse_status,
                       documents.metadata AS document_metadata,
                       documents.created_at, documents.updated_at,
                       document_artifacts.title AS document_name,
                       document_artifacts.sha256, document_artifacts.uri,
                       document_artifacts.metadata AS artifact_metadata
                FROM documents
                JOIN document_artifacts ON document_artifacts.doc_id = documents.doc_id
                WHERE documents.deal_id = :deal_id
                  AND (:parsed_only = false OR documents.parse_status = 'PARSED')
                ORDER BY documents.document_id ASC
                """
            ),
            {"deal_id": deal_id, "parsed_only": parsed_only},
        )
        return [self._document_row_to_dict(row) for row in result.fetchall()]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        """Get a parsed document by ID."""
        result = self._conn.execute(
            text(
                """
                SELECT documents.document_id, documents.tenant_id, documents.deal_id,
                       documents.doc_id, documents.doc_type, documents.parse_status,
                       documents.metadata AS document_metadata,
                       documents.created_at, documents.updated_at,
                       document_artifacts.title AS document_name,
                       document_artifacts.sha256, document_artifacts.uri,
                       document_artifacts.metadata AS artifact_metadata
                FROM documents
                JOIN document_artifacts ON document_artifacts.doc_id = documents.doc_id
                WHERE documents.document_id = :document_id
                """
            ),
            {"document_id": document_id},
        ).fetchone()
        return None if result is None else self._document_row_to_dict(result)

    def list_spans_by_document(self, *, deal_id: str, document_id: str) -> list[dict[str, Any]]:
        """List spans for one deal/document in deterministic order."""
        result = self._conn.execute(
            text(
                """
                SELECT span_id, tenant_id, deal_id, document_id, span_type,
                       locator, text_excerpt, content_hash, created_at, updated_at
                FROM document_spans
                WHERE deal_id = :deal_id AND document_id = :document_id
                ORDER BY span_id ASC
                """
            ),
            {"deal_id": deal_id, "document_id": document_id},
        )
        return [self._span_row_to_dict(row) for row in result.fetchall()]

    def _document_row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert a joined document row to a stable dict."""
        mapping = row._mapping
        metadata = _json_value(mapping["document_metadata"])
        artifact_metadata = _json_value(mapping["artifact_metadata"])
        document_name = (
            metadata.get("name")
            or metadata.get("document_name")
            or mapping["document_name"]
            or str(mapping["document_id"])
        )
        return {
            "document_id": str(mapping["document_id"]),
            "tenant_id": str(mapping["tenant_id"]),
            "deal_id": str(mapping["deal_id"]),
            "doc_id": str(mapping["doc_id"]),
            "doc_type": str(mapping["doc_type"]),
            "parse_status": str(mapping["parse_status"]),
            "metadata": metadata,
            "source_metadata": artifact_metadata,
            "document_name": str(document_name),
            "sha256": mapping["sha256"],
            "uri": mapping["uri"],
            "created_at": _isoformat(mapping["created_at"]),
            "updated_at": _isoformat(mapping["updated_at"]),
        }

    def _span_row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert a document span row to a stable dict."""
        mapping = row._mapping
        return {
            "span_id": str(mapping["span_id"]),
            "tenant_id": str(mapping["tenant_id"]),
            "deal_id": str(mapping["deal_id"]),
            "document_id": str(mapping["document_id"]),
            "span_type": str(mapping["span_type"]),
            "locator": _json_value(mapping["locator"]),
            "text_excerpt": mapping["text_excerpt"],
            "content_hash": mapping["content_hash"],
            "created_at": _isoformat(mapping["created_at"]),
            "updated_at": _isoformat(mapping["updated_at"]),
        }


def _json_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return value if isinstance(value, dict) else {}


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat().replace("+00:00", "Z"))
    return str(value)
