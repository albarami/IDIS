"""Evidence repository for Postgres persistence and in-memory fallback.

Provides tenant-scoped CRUD operations for evidence items with RLS enforcement.
The InMemoryEvidenceRepository lives in claims.py (legacy); this module adds
the Postgres implementation and factory function for Phase 7.A cutover.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy import text

from idis.persistence.db import is_postgres_configured, set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


@runtime_checkable
class EvidenceRepo(Protocol):
    """Structural interface for evidence repositories.

    Both InMemoryEvidenceRepository and PostgresEvidenceRepository
    satisfy this protocol. Use this type in function signatures
    that accept either backend.
    """

    def create(
        self,
        *,
        evidence_id: str,
        tenant_id: str,
        deal_id: str,
        claim_id: str,
        source_span_id: str,
        source_grade: str = ...,
        verification_status: str = ...,
    ) -> dict[str, Any]: ...

    def get(self, evidence_id: str) -> dict[str, Any] | None: ...

    def get_by_claim(self, claim_id: str) -> list[dict[str, Any]]: ...


class PostgresEvidenceRepository:
    """Tenant-scoped Postgres repository for evidence items.

    All operations enforce RLS via SET LOCAL idis.tenant_id.

    Args:
        conn: SQLAlchemy connection (must be in a transaction).
        tenant_id: Tenant UUID string for RLS scoping.
    """

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with connection and tenant context.

        Args:
            conn: SQLAlchemy connection (must be in a transaction).
            tenant_id: Tenant UUID string for RLS scoping.
        """
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(
        self,
        *,
        evidence_id: str,
        tenant_id: str,
        deal_id: str,
        claim_id: str,
        source_span_id: str,
        source_grade: str = "D",
        verification_status: str = "UNVERIFIED",
    ) -> dict[str, Any]:
        """Create an evidence record in Postgres.

        Args:
            evidence_id: UUID for the evidence item.
            tenant_id: Tenant UUID.
            deal_id: Deal UUID.
            claim_id: Parent claim UUID.
            source_span_id: Source span UUID.
            source_grade: Sanad grade (default D).
            verification_status: Verification state (not stored in DB, kept for API compat).

        Returns:
            Created evidence dict.
        """
        now = datetime.now(UTC)
        self._conn.execute(
            text(
                """
                INSERT INTO evidence_items
                    (evidence_id, tenant_id, deal_id, claim_id,
                     source_span_id, source_grade, created_at)
                VALUES
                    (:evidence_id, :tenant_id, :deal_id, :claim_id,
                     :source_span_id, :source_grade, :created_at)
                """
            ),
            {
                "evidence_id": evidence_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "claim_id": claim_id,
                "source_span_id": source_span_id,
                "source_grade": source_grade,
                "created_at": now,
            },
        )
        return {
            "evidence_id": evidence_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "claim_id": claim_id,
            "source_span_id": source_span_id,
            "source_grade": source_grade,
            "verification_status": verification_status,
            "created_at": now.isoformat().replace("+00:00", "Z"),
        }

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        """Get evidence by ID.

        RLS ensures only evidence for the current tenant is visible.

        Args:
            evidence_id: UUID of the evidence item.

        Returns:
            Evidence dict or None.
        """
        result = self._conn.execute(
            text(
                """
                SELECT evidence_id, tenant_id, deal_id, claim_id,
                       source_span_id, source_grade, created_at
                FROM evidence_items
                WHERE evidence_id = :evidence_id
                """
            ),
            {"evidence_id": evidence_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def get_by_claim(self, claim_id: str) -> list[dict[str, Any]]:
        """Get all evidence items for a claim.

        RLS ensures only evidence for the current tenant is visible.

        Args:
            claim_id: Parent claim UUID.

        Returns:
            List of evidence dicts for this claim and tenant.
        """
        result = self._conn.execute(
            text(
                """
                SELECT evidence_id, tenant_id, deal_id, claim_id,
                       source_span_id, source_grade, created_at
                FROM evidence_items
                WHERE claim_id = :claim_id
                ORDER BY evidence_id
                """
            ),
            {"claim_id": claim_id},
        )
        return [self._row_to_dict(row) for row in result.fetchall()]

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert database row to dict."""
        created_at = row.created_at
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat().replace("+00:00", "Z")

        return {
            "evidence_id": str(row.evidence_id),
            "tenant_id": str(row.tenant_id),
            "deal_id": str(row.deal_id),
            "claim_id": str(row.claim_id),
            "source_span_id": str(row.source_span_id),
            "source_grade": row.source_grade,
            "created_at": created_at,
        }


def get_evidence_repository(
    conn: Connection | None,
    tenant_id: str,
) -> Any:
    """Factory to get appropriate evidence repository.

    Returns Postgres repository if configured, otherwise in-memory fallback.

    Args:
        conn: SQLAlchemy connection (can be None for in-memory).
        tenant_id: Tenant UUID string.

    Returns:
        Repository instance (PostgresEvidenceRepository or InMemoryEvidenceRepository).
    """
    if conn is not None and is_postgres_configured():
        return PostgresEvidenceRepository(conn, tenant_id)

    from idis.persistence.repositories.claims import InMemoryEvidenceRepository

    return InMemoryEvidenceRepository(tenant_id)
