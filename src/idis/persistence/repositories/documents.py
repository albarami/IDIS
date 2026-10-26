"""Document repositories for persisted ingestion corpus.

Provides tenant-scoped Postgres access to document artifacts, parsed documents,
and document spans. All operations rely on RLS through SET LOCAL tenant context.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

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
                    version_id, ingested_at, sha256, uri, metadata, created_at, updated_at,
                    source_binding_state, source_lineage_object_id, source_corpus_receipt_id
                )
                VALUES (
                    :doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                    :version_id, :ingested_at, :sha256, :uri, CAST(:metadata AS JSONB),
                    :created_at, :updated_at, 'UNBOUND', NULL, NULL
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
                    parse_status, metadata, created_at, updated_at,
                    source_binding_state, source_lineage_object_id, source_corpus_receipt_id
                )
                VALUES (
                    :document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                    :parse_status, CAST(:metadata AS JSONB), :created_at, :updated_at,
                    'UNBOUND', NULL, NULL
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
                    locator, text_excerpt, content_hash, created_at, updated_at,
                    source_binding_state, source_lineage_object_id, source_corpus_receipt_id,
                    parse_attempt_id
                )
                VALUES (
                    :span_id, :tenant_id, :deal_id, :document_id, :span_type,
                    CAST(:locator AS JSONB), :text_excerpt, :content_hash,
                    :created_at, :updated_at, 'UNBOUND', NULL, NULL, NULL
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

    def create_runtime_parse_attempt(
        self,
        *,
        parse_attempt_id: str,
        deal_id: str,
        document_id: str,
        parser_name: str,
        parser_version: str,
        input_sha256: str,
        receipt_sha256: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        """Insert one actual ordinal-one execution, accepting only an exact replay."""
        values = {
            "parse_attempt_id": parse_attempt_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "document_id": document_id,
            "parser_name": parser_name,
            "parser_version": parser_version,
            "input_sha256": input_sha256,
            "receipt_sha256": receipt_sha256,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
        }
        self._conn.execute(
            text(
                "INSERT INTO document_parse_attempts "
                "(parse_attempt_id,tenant_id,deal_id,document_id,"
                "attempt_ordinal,parser_name,parser_version,input_sha256,receipt_sha256,status,"
                "started_at,finished_at,source_binding_state,source_lineage_object_id,"
                "source_corpus_receipt_id,write_origin) VALUES "
                "(:parse_attempt_id,:tenant_id,:deal_id,"
                ":document_id,1,:parser_name,:parser_version,:input_sha256,:receipt_sha256,:status,"
                ":started_at,:finished_at,'UNBOUND',NULL,NULL,'RUNTIME') "
                "ON CONFLICT DO NOTHING"
            ),
            values,
        )
        rows = (
            self._conn.execute(
                text(
                    "SELECT * FROM document_parse_attempts WHERE tenant_id=:tenant_id "
                    "AND deal_id=:deal_id AND document_id=:document_id"
                ),
                values,
            )
            .mappings()
            .all()
        )
        if len(rows) != 1:
            raise ValueError("runtime parse attempt has conflicting document history")
        row = rows[0]
        for key, expected in values.items():
            observed = str(row[key]) if key.endswith("_id") else row[key]
            if observed != expected:
                raise ValueError("runtime parse attempt replay does not match")
        if (
            row["attempt_ordinal"] != 1
            or row["write_origin"] != "RUNTIME"
            or row["source_binding_state"] != "UNBOUND"
        ):
            raise ValueError("runtime parse attempt is not writable staging")

    def acquire_watermark_company_admission_lock(
        self,
        *,
        deal_id: str,
        company_alias: str,
    ) -> None:
        """Serialize a company's classifier-cap check with its document write.

        The lock is transaction-scoped, so the caller's subsequent receipt count
        and insert form one admission boundary without adding a schema object.
        """

        lock_material = (
            f"watermark-page-cap-v1:{self._tenant_id}:{deal_id}:{company_alias}"
        ).encode()
        lock_key = int.from_bytes(
            hashlib.sha256(lock_material).digest()[:8],
            byteorder="big",
            signed=True,
        )
        self._conn.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

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
                       document_artifacts.doc_type AS artifact_doc_type,
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
                       document_artifacts.doc_type AS artifact_doc_type,
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

    def load_frozen_documents_for_run(
        self, *, deal_id: str, document_ids: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        """Require exact current freeze membership, never silently drop staging.

        Installed source-freeze FK/projection/immutability guards remain the
        identity authority. This read neither invents roots nor rehashes them.
        Inventory methods deliberately remain able to show UNBOUND uploads.
        """
        try:
            selected = tuple(sorted(str(UUID(value)) for value in document_ids))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("FROZEN_SOURCE_REQUIRED") from exc
        if not selected or len(set(selected)) != len(selected):
            raise ValueError("FROZEN_SOURCE_REQUIRED")
        rows = (
            self._conn.execute(
                text("""
            SELECT d.document_id::text
            FROM documents d
            JOIN document_artifacts a ON a.tenant_id=d.tenant_id
              AND a.deal_id=d.deal_id AND a.doc_id=d.doc_id
            JOIN data_room_package_files f ON f.tenant_id=d.tenant_id
              AND f.deal_id=d.deal_id AND f.document_id=d.document_id AND f.doc_id=a.doc_id
            JOIN data_room_packages p ON p.tenant_id=f.tenant_id
              AND p.deal_id=f.deal_id AND p.package_id=f.package_id
            JOIN source_lineage_objects root ON root.tenant_id=d.tenant_id
              AND root.deal_id=d.deal_id
              AND root.source_lineage_object_id=d.source_lineage_object_id
              AND root.corpus_receipt_id=p.package_id AND root.object_kind='DOCUMENT'
              AND root.write_origin='RUNTIME'
            WHERE d.tenant_id=CAST(:tenant_id AS uuid) AND d.deal_id=CAST(:deal_id AS uuid)
              AND d.document_id=ANY(CAST(:document_ids AS uuid[]))
              AND d.source_binding_state='BOUND' AND a.source_binding_state='BOUND'
              AND f.source_binding_state='BOUND' AND p.source_binding_state='BOUND'
              AND p.status='SEALED' AND d.source_corpus_receipt_id=p.package_id
              AND a.source_corpus_receipt_id=p.package_id
              AND f.source_corpus_receipt_id=p.package_id
              AND EXISTS (
                SELECT 1 FROM document_parse_attempts attempt
                WHERE attempt.tenant_id=d.tenant_id AND attempt.deal_id=d.deal_id
                  AND attempt.document_id=d.document_id AND attempt.source_binding_state='BOUND'
                  AND attempt.source_corpus_receipt_id=p.package_id
                  AND attempt.write_origin='RUNTIME'
              )
              AND NOT EXISTS (
                SELECT 1 FROM document_spans span
                WHERE span.tenant_id=d.tenant_id AND span.deal_id=d.deal_id
                  AND span.document_id=d.document_id AND (
                    span.source_binding_state<>'BOUND'
                    OR span.source_corpus_receipt_id IS DISTINCT FROM p.package_id
                    OR NOT EXISTS (
                      SELECT 1 FROM document_parse_attempts attempt
                      WHERE attempt.tenant_id=span.tenant_id AND attempt.deal_id=span.deal_id
                        AND attempt.document_id=span.document_id
                        AND attempt.parse_attempt_id=span.parse_attempt_id
                        AND attempt.source_binding_state='BOUND'
                        AND attempt.source_corpus_receipt_id=p.package_id
                    )
                  )
              )
            ORDER BY d.document_id
        """),
                {"tenant_id": self._tenant_id, "deal_id": deal_id, "document_ids": list(selected)},
            )
            .scalars()
            .all()
        )
        if tuple(rows) != selected:
            raise ValueError("FROZEN_SOURCE_REQUIRED")
        documents = [self.get_document(document_id) for document_id in selected]
        if any(document is None for document in documents):
            raise ValueError("FROZEN_SOURCE_REQUIRED")
        return [document for document in documents if document is not None]

    def get_document_by_artifact_id(self, doc_id: str) -> dict[str, Any] | None:
        """Get the sole parsed document bound to a durable public artifact ID.

        ``documents.doc_id`` is indexed but not unique in the current schema.  A
        bounded, totally ordered two-row read makes duplicate lineage fail closed
        instead of letting PostgreSQL plan order choose a completed-looking row.
        """
        result = self._conn.execute(
            text(
                """
                SELECT documents.document_id, documents.tenant_id, documents.deal_id,
                       documents.doc_id, documents.doc_type, documents.parse_status,
                       documents.metadata AS document_metadata,
                       documents.created_at, documents.updated_at,
                       document_artifacts.doc_type AS artifact_doc_type,
                       document_artifacts.title AS document_name,
                       document_artifacts.sha256, document_artifacts.uri,
                       document_artifacts.metadata AS artifact_metadata
                FROM documents
                JOIN document_artifacts ON document_artifacts.doc_id = documents.doc_id
                WHERE documents.doc_id = :doc_id
                ORDER BY documents.document_id ASC
                LIMIT 2
                """
            ),
            {"doc_id": doc_id},
        )
        rows = result.fetchall()
        if len(rows) > 1:
            raise RuntimeError("Artifact document lineage resolved to multiple durable rows")
        return None if not rows else self._document_row_to_dict(rows[0])

    def get_document_by_inventory_file_id(
        self,
        *,
        deal_id: str,
        inventory_file_id: str,
    ) -> dict[str, Any] | None:
        """Get a parsed document by data-room inventory provenance."""
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
                  AND document_artifacts.metadata ->> 'inventory_file_id' = :inventory_file_id
                ORDER BY documents.created_at ASC, documents.document_id ASC
                LIMIT 2
                """
            ),
            {"deal_id": deal_id, "inventory_file_id": inventory_file_id},
        )
        rows = result.fetchall()
        if len(rows) > 1:
            raise RuntimeError("Inventory file lineage resolved to multiple durable rows")
        return None if not rows else self._document_row_to_dict(rows[0])

    def list_spans_by_document(self, *, deal_id: str, document_id: str) -> list[dict[str, Any]]:
        """List spans for one deal/document in deterministic order."""
        result = self._conn.execute(
            text(
                """
                SELECT span_id, tenant_id, deal_id, document_id, span_type,
                       locator, text_excerpt, content_hash, created_at, updated_at
                FROM document_spans
                WHERE deal_id = :deal_id AND document_id = :document_id
                ORDER BY
                    CASE
                        WHEN span_type = 'TIMECODE'
                         AND jsonb_typeof(locator -> 'start_ms') = 'number'
                        THEN (locator ->> 'start_ms')::BIGINT
                        ELSE NULL
                    END ASC NULLS LAST,
                    span_id ASC
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
            "artifact_doc_type": str(mapping.get("artifact_doc_type", mapping["doc_type"])),
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
