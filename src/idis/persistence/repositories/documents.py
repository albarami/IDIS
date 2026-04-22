"""Documents repositories for Postgres persistence.

Provides tenant-scoped repositories over the ingestion-gate tables introduced
in migration 0004:

- document_artifacts  -> DocumentArtifactsRepository
- documents           -> DocumentsRepository
- document_spans      -> DocumentSpansRepository

All operations rely on Postgres RLS with the `idis.tenant_id` GUC, set by
`set_tenant_local()` in the constructor (same pattern as
deals/claims/evidence repos). Rows are returned as plain dicts with
ISO-8601 UTC timestamps (matches the style used by DealsRepository).

Scope note (Sprint 1 Wave 2, Task 5):
No API routes, services, or orchestration code is touched by this module.
Wiring into IngestionService / document routes is Task 6+.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from idis.persistence.db import set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class DocumentArtifactNotFoundError(Exception):
    """Raised when a document artifact is not found for the current tenant."""

    def __init__(self, doc_id: str, tenant_id: str) -> None:
        self.doc_id = doc_id
        self.tenant_id = tenant_id
        super().__init__(f"DocumentArtifact {doc_id} not found for tenant {tenant_id}")


class DocumentNotFoundError(Exception):
    """Raised when a document is not found for the current tenant."""

    def __init__(self, document_id: str, tenant_id: str) -> None:
        self.document_id = document_id
        self.tenant_id = tenant_id
        super().__init__(f"Document {document_id} not found for tenant {tenant_id}")


def _iso_utc(value: Any) -> Any:
    """Serialize a datetime to ISO-8601 with trailing `Z`; pass other values through."""
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return value


def _coerce_json(value: Any) -> Any:
    """JSONB columns may surface as dicts or raw strings depending on driver."""
    if isinstance(value, str):
        return json.loads(value)
    return value


class DocumentArtifactsRepository:
    """Tenant-scoped repository for the `document_artifacts` table.

    All operations enforce RLS via SET LOCAL idis.tenant_id. Callers must
    use a SQLAlchemy Connection that is (or will become) inside a
    transaction before SET LOCAL takes effect; this matches the pattern
    used by the other Postgres repositories in this package.
    """

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(
        self,
        *,
        doc_id: str,
        deal_id: str,
        doc_type: str,
        title: str,
        source_system: str,
        version_id: str,
        sha256: str | None = None,
        uri: str | None = None,
        metadata: dict[str, Any] | None = None,
        ingested_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert a DocumentArtifact row.

        Column defaults from migration 0004 cover ingested_at/created_at/
        updated_at/metadata, but we set them explicitly so the returned
        dict reflects the persisted values without a follow-up SELECT.
        """
        now = datetime.now(UTC)
        effective_ingested_at = ingested_at or now
        metadata_json = json.dumps(metadata or {})

        self._conn.execute(
            text(
                """
                INSERT INTO document_artifacts (
                    doc_id, tenant_id, deal_id, doc_type, title, source_system,
                    version_id, ingested_at, sha256, uri, metadata,
                    created_at, updated_at
                ) VALUES (
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
                "ingested_at": effective_ingested_at,
                "sha256": sha256,
                "uri": uri,
                "metadata": metadata_json,
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
            "ingested_at": _iso_utc(effective_ingested_at),
            "sha256": sha256,
            "uri": uri,
            "metadata": metadata or {},
            "created_at": _iso_utc(now),
            "updated_at": _iso_utc(now),
        }

    def get(self, doc_id: str) -> dict[str, Any] | None:
        """Get an artifact by id. Returns None if missing or cross-tenant (RLS)."""
        row = self._conn.execute(
            text(
                """
                SELECT doc_id, tenant_id, deal_id, doc_type, title, source_system,
                       version_id, ingested_at, sha256, uri, metadata,
                       created_at, updated_at
                FROM document_artifacts
                WHERE doc_id = :doc_id
                """
            ),
            {"doc_id": doc_id},
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_by_deal(
        self,
        deal_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List artifacts for a deal (tenant-scoped via RLS).

        Deterministic order on `(created_at ASC, doc_id ASC)` so batch
        inserts with identical timestamps still sort stably. Cursor is
        the last seen `doc_id` of the previous page.
        """
        effective_limit = min(max(1, limit), 200)

        if cursor:
            stmt = text(
                """
                SELECT doc_id, tenant_id, deal_id, doc_type, title, source_system,
                       version_id, ingested_at, sha256, uri, metadata,
                       created_at, updated_at
                FROM document_artifacts
                WHERE deal_id = :deal_id AND doc_id > :cursor
                ORDER BY created_at ASC, doc_id ASC
                LIMIT :limit
                """
            )
            rows = self._conn.execute(
                stmt,
                {"deal_id": deal_id, "cursor": cursor, "limit": effective_limit + 1},
            ).fetchall()
        else:
            stmt = text(
                """
                SELECT doc_id, tenant_id, deal_id, doc_type, title, source_system,
                       version_id, ingested_at, sha256, uri, metadata,
                       created_at, updated_at
                FROM document_artifacts
                WHERE deal_id = :deal_id
                ORDER BY created_at ASC, doc_id ASC
                LIMIT :limit
                """
            )
            rows = self._conn.execute(
                stmt, {"deal_id": deal_id, "limit": effective_limit + 1}
            ).fetchall()

        items = [self._row_to_dict(row) for row in rows[:effective_limit]]
        next_cursor: str | None = None
        if len(rows) > effective_limit:
            next_cursor = items[-1]["doc_id"]
        return items, next_cursor

    def delete(self, doc_id: str) -> bool:
        """Delete an artifact and every document/span chained off it.

        Order: delete dependent documents (document_spans cascades via
        ON DELETE CASCADE on the spans FK), then delete the artifact row.
        Tenant isolation is enforced by RLS on each statement.

        Returns True if the artifact row was removed, False otherwise.
        """
        self._conn.execute(
            text("DELETE FROM documents WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )
        result = self._conn.execute(
            text("DELETE FROM document_artifacts WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )
        return bool(result.rowcount)

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "doc_id": str(row.doc_id),
            "tenant_id": str(row.tenant_id),
            "deal_id": str(row.deal_id),
            "doc_type": row.doc_type,
            "title": row.title,
            "source_system": row.source_system,
            "version_id": row.version_id,
            "ingested_at": _iso_utc(row.ingested_at),
            "sha256": row.sha256,
            "uri": row.uri,
            "metadata": _coerce_json(row.metadata) or {},
            "created_at": _iso_utc(row.created_at),
            "updated_at": _iso_utc(row.updated_at),
        }


class DocumentsRepository:
    """Tenant-scoped repository for the `documents` table."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(
        self,
        *,
        document_id: str,
        deal_id: str,
        doc_id: str,
        doc_type: str,
        parse_status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a Document row."""
        now = datetime.now(UTC)
        metadata_json = json.dumps(metadata or {})

        self._conn.execute(
            text(
                """
                INSERT INTO documents (
                    document_id, tenant_id, deal_id, doc_id, doc_type,
                    parse_status, metadata, created_at, updated_at
                ) VALUES (
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
                "metadata": metadata_json,
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
            "metadata": metadata or {},
            "created_at": _iso_utc(now),
            "updated_at": _iso_utc(now),
        }

    def get(self, document_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            text(
                """
                SELECT document_id, tenant_id, deal_id, doc_id, doc_type,
                       parse_status, metadata, created_at, updated_at
                FROM documents
                WHERE document_id = :document_id
                """
            ),
            {"document_id": document_id},
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_by_doc_id(self, doc_id: str) -> list[dict[str, Any]]:
        """Return every Document row whose source artifact is `doc_id`.

        One artifact can produce multiple documents (e.g. archive extraction).
        Deterministic order on `(created_at ASC, document_id ASC)`.
        Tenant-scoped via RLS.
        """
        rows = self._conn.execute(
            text(
                """
                SELECT document_id, tenant_id, deal_id, doc_id, doc_type,
                       parse_status, metadata, created_at, updated_at
                FROM documents
                WHERE doc_id = :doc_id
                ORDER BY created_at ASC, document_id ASC
                """
            ),
            {"doc_id": doc_id},
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_by_deal(
        self,
        deal_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List documents for a deal (tenant-scoped via RLS).

        Deterministic ordering on `(created_at ASC, document_id ASC)` so
        batch inserts with identical timestamps still sort stably.
        Cursor is the last seen `document_id` of the previous page.
        """
        effective_limit = min(max(1, limit), 200)

        if cursor:
            stmt = text(
                """
                SELECT document_id, tenant_id, deal_id, doc_id, doc_type,
                       parse_status, metadata, created_at, updated_at
                FROM documents
                WHERE deal_id = :deal_id AND document_id > :cursor
                ORDER BY created_at ASC, document_id ASC
                LIMIT :limit
                """
            )
            rows = self._conn.execute(
                stmt,
                {"deal_id": deal_id, "cursor": cursor, "limit": effective_limit + 1},
            ).fetchall()
        else:
            stmt = text(
                """
                SELECT document_id, tenant_id, deal_id, doc_id, doc_type,
                       parse_status, metadata, created_at, updated_at
                FROM documents
                WHERE deal_id = :deal_id
                ORDER BY created_at ASC, document_id ASC
                LIMIT :limit
                """
            )
            rows = self._conn.execute(
                stmt, {"deal_id": deal_id, "limit": effective_limit + 1}
            ).fetchall()

        items = [self._row_to_dict(row) for row in rows[:effective_limit]]
        next_cursor: str | None = None
        if len(rows) > effective_limit:
            next_cursor = items[-1]["document_id"]
        return items, next_cursor

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "document_id": str(row.document_id),
            "tenant_id": str(row.tenant_id),
            "deal_id": str(row.deal_id),
            "doc_id": str(row.doc_id),
            "doc_type": row.doc_type,
            "parse_status": row.parse_status,
            "metadata": _coerce_json(row.metadata) or {},
            "created_at": _iso_utc(row.created_at),
            "updated_at": _iso_utc(row.updated_at),
        }


class DocumentSpansRepository:
    """Tenant-scoped repository for the `document_spans` table."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create_many(self, spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Batch-insert spans for one or more documents.

        Each span dict requires: span_id, document_id, span_type, locator.
        Optional: text_excerpt. tenant_id is filled from the repo's bound tenant.

        Returns the persisted rows as dicts (with ISO timestamps) in the
        same order they were supplied.
        """
        if not spans:
            return []

        now = datetime.now(UTC)
        payload = []
        for span in spans:
            payload.append(
                {
                    "span_id": span["span_id"],
                    "tenant_id": self._tenant_id,
                    "document_id": span["document_id"],
                    "span_type": span["span_type"],
                    "locator": json.dumps(span.get("locator") or {}),
                    "text_excerpt": span.get("text_excerpt"),
                    "created_at": now,
                    "updated_at": now,
                }
            )

        self._conn.execute(
            text(
                """
                INSERT INTO document_spans (
                    span_id, tenant_id, document_id, span_type, locator,
                    text_excerpt, created_at, updated_at
                ) VALUES (
                    :span_id, :tenant_id, :document_id, :span_type,
                    CAST(:locator AS JSONB), :text_excerpt, :created_at, :updated_at
                )
                """
            ),
            payload,
        )

        return [
            {
                "span_id": span["span_id"],
                "tenant_id": self._tenant_id,
                "document_id": span["document_id"],
                "span_type": span["span_type"],
                "locator": span.get("locator") or {},
                "text_excerpt": span.get("text_excerpt"),
                "created_at": _iso_utc(now),
                "updated_at": _iso_utc(now),
            }
            for span in spans
        ]

    def list_by_document(self, document_id: str) -> list[dict[str, Any]]:
        """Return spans for a document (tenant-scoped) in deterministic order.

        Order is `(created_at ASC, span_id ASC)`. Identical timestamps within
        a batch fall back to span_id, which is sufficient for stable
        round-trip replay of an ingest.
        """
        rows = self._conn.execute(
            text(
                """
                SELECT span_id, tenant_id, document_id, span_type, locator,
                       text_excerpt, created_at, updated_at
                FROM document_spans
                WHERE document_id = :document_id
                ORDER BY created_at ASC, span_id ASC
                """
            ),
            {"document_id": document_id},
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "span_id": str(row.span_id),
            "tenant_id": str(row.tenant_id),
            "document_id": str(row.document_id),
            "span_type": row.span_type,
            "locator": _coerce_json(row.locator) or {},
            "text_excerpt": row.text_excerpt,
            "created_at": _iso_utc(row.created_at),
            "updated_at": _iso_utc(row.updated_at),
        }
