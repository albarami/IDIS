"""Claims repository for Postgres persistence.

Provides tenant-scoped CRUD operations for claims, sanads, and defects with RLS enforcement.
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


class ClaimNotFoundError(Exception):
    """Raised when a claim is not found."""

    def __init__(self, claim_id: str, tenant_id: str) -> None:
        self.claim_id = claim_id
        self.tenant_id = tenant_id
        super().__init__(f"Claim {claim_id} not found for tenant {tenant_id}")


class ClaimsRepository:
    """Repository for claim persistence operations.

    All operations are tenant-scoped via RLS.
    """

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with connection and tenant context."""
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(
        self,
        *,
        claim_id: str,
        deal_id: str,
        claim_class: str,
        claim_text: str,
        predicate: str | None = None,
        value: dict[str, Any] | None = None,
        sanad_id: str | None = None,
        claim_grade: str = "D",
        corroboration: dict[str, Any] | None = None,
        claim_verdict: str = "UNVERIFIED",
        claim_action: str = "VERIFY",
        defect_ids: list[str] | None = None,
        materiality: str = "MEDIUM",
        ic_bound: bool = False,
        primary_span_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new claim."""
        now = datetime.now(UTC)
        corroboration = corroboration or {"level": "AHAD", "independent_chain_count": 1}

        self._conn.execute(
            text(
                """
                INSERT INTO claims (
                    claim_id, tenant_id, deal_id, claim_class, claim_text,
                    predicate, value, sanad_id, claim_grade, corroboration,
                    claim_verdict, claim_action, defect_ids, materiality,
                    ic_bound, primary_span_id, created_at, updated_at
                ) VALUES (
                    :claim_id, :tenant_id, :deal_id, :claim_class, :claim_text,
                    :predicate, CAST(:value AS JSONB), :sanad_id, :claim_grade,
                    CAST(:corroboration AS JSONB), :claim_verdict, :claim_action,
                    CAST(:defect_ids AS JSONB), :materiality, :ic_bound,
                    :primary_span_id, :created_at, NULL
                )
                """
            ),
            {
                "claim_id": claim_id,
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "claim_class": claim_class,
                "claim_text": claim_text,
                "predicate": predicate,
                "value": json.dumps(value) if value else None,
                "sanad_id": sanad_id,
                "claim_grade": claim_grade,
                "corroboration": json.dumps(corroboration),
                "claim_verdict": claim_verdict,
                "claim_action": claim_action,
                "defect_ids": json.dumps(defect_ids or []),
                "materiality": materiality,
                "ic_bound": ic_bound,
                "primary_span_id": primary_span_id,
                "created_at": now,
            },
        )

        return {
            "claim_id": claim_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "claim_class": claim_class,
            "claim_text": claim_text,
            "predicate": predicate,
            "value": value,
            "sanad_id": sanad_id,
            "claim_grade": claim_grade,
            "corroboration": corroboration,
            "claim_verdict": claim_verdict,
            "claim_action": claim_action,
            "defect_ids": defect_ids or [],
            "materiality": materiality,
            "ic_bound": ic_bound,
            "primary_span_id": primary_span_id,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "updated_at": None,
        }

    def get(self, claim_id: str) -> dict[str, Any] | None:
        """Get a claim by ID."""
        result = self._conn.execute(
            text(
                """
                SELECT claim_id, tenant_id, deal_id, claim_class, claim_text,
                       predicate, value, sanad_id, claim_grade, corroboration,
                       claim_verdict, claim_action, defect_ids, materiality,
                       ic_bound, primary_span_id, created_at, updated_at
                FROM claims
                WHERE claim_id = :claim_id
                """
            ),
            {"claim_id": claim_id},
        ).fetchone()

        if result is None:
            return None

        return self._row_to_dict(result)

    def list_by_deal(
        self,
        deal_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List claims for a deal."""
        effective_limit = min(max(1, limit), 200)

        if cursor:
            result = self._conn.execute(
                text(
                    """
                    SELECT claim_id, tenant_id, deal_id, claim_class, claim_text,
                           predicate, value, sanad_id, claim_grade, corroboration,
                           claim_verdict, claim_action, defect_ids, materiality,
                           ic_bound, primary_span_id, created_at, updated_at
                    FROM claims
                    WHERE deal_id = :deal_id AND claim_id > :cursor
                    ORDER BY claim_id
                    LIMIT :limit
                    """
                ),
                {"deal_id": deal_id, "cursor": cursor, "limit": effective_limit + 1},
            ).fetchall()
        else:
            result = self._conn.execute(
                text(
                    """
                    SELECT claim_id, tenant_id, deal_id, claim_class, claim_text,
                           predicate, value, sanad_id, claim_grade, corroboration,
                           claim_verdict, claim_action, defect_ids, materiality,
                           ic_bound, primary_span_id, created_at, updated_at
                    FROM claims
                    WHERE deal_id = :deal_id
                    ORDER BY claim_id
                    LIMIT :limit
                    """
                ),
                {"deal_id": deal_id, "limit": effective_limit + 1},
            ).fetchall()

        claims = [self._row_to_dict(row) for row in result[:effective_limit]]

        next_cursor = None
        if len(result) > effective_limit:
            next_cursor = claims[-1]["claim_id"]

        return claims, next_cursor

    def update_grade(
        self,
        claim_id: str,
        *,
        claim_grade: str | None = None,
        sanad_id: str | None = None,
    ) -> None:
        """Update claim_grade and/or sanad_id for a claim.

        Args:
            claim_id: Claim UUID.
            claim_grade: New grade letter (A/B/C/D).
            sanad_id: Sanad UUID to link.
        """
        sets: list[str] = []
        params: dict[str, Any] = {"claim_id": claim_id}
        if claim_grade is not None:
            sets.append("claim_grade = :claim_grade")
            params["claim_grade"] = claim_grade
        if sanad_id is not None:
            sets.append("sanad_id = :sanad_id")
            params["sanad_id"] = sanad_id
        if not sets:
            return
        sets.append("updated_at = NOW()")
        sql = f"UPDATE claims SET {', '.join(sets)} WHERE claim_id = :claim_id"
        self._conn.execute(text(sql), params)

    def delete(self, claim_id: str) -> bool:
        """Delete a claim by ID."""
        result = self._conn.execute(
            text("DELETE FROM claims WHERE claim_id = :claim_id"),
            {"claim_id": claim_id},
        )
        return result.rowcount > 0

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert database row to dict."""
        created_at = row.created_at
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat().replace("+00:00", "Z")

        updated_at = row.updated_at
        if updated_at is not None and hasattr(updated_at, "isoformat"):
            updated_at = updated_at.isoformat().replace("+00:00", "Z")

        value = row.value
        if isinstance(value, str):
            value = json.loads(value)

        corroboration = row.corroboration
        if isinstance(corroboration, str):
            corroboration = json.loads(corroboration)

        defect_ids = row.defect_ids
        if isinstance(defect_ids, str):
            defect_ids = json.loads(defect_ids)

        primary_span_id = getattr(row, "primary_span_id", None)

        return {
            "claim_id": str(row.claim_id),
            "tenant_id": str(row.tenant_id),
            "deal_id": str(row.deal_id),
            "claim_class": row.claim_class,
            "claim_text": row.claim_text,
            "predicate": row.predicate,
            "value": value,
            "sanad_id": str(row.sanad_id) if row.sanad_id else None,
            "claim_grade": row.claim_grade,
            "corroboration": corroboration,
            "claim_verdict": row.claim_verdict,
            "claim_action": row.claim_action,
            "defect_ids": defect_ids or [],
            "materiality": row.materiality,
            "ic_bound": row.ic_bound,
            "primary_span_id": str(primary_span_id) if primary_span_id else None,
            "created_at": created_at,
            "updated_at": updated_at,
        }


class SanadsRepository:
    """Repository for sanad persistence operations."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with connection and tenant context."""
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(
        self,
        *,
        sanad_id: str,
        claim_id: str,
        deal_id: str,
        primary_evidence_id: str,
        corroborating_evidence_ids: list[str] | None = None,
        transmission_chain: list[dict[str, Any]] | None = None,
        computed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new sanad."""
        now = datetime.now(UTC)

        self._conn.execute(
            text(
                """
                INSERT INTO sanads (
                    sanad_id, tenant_id, claim_id, deal_id, primary_evidence_id,
                    corroborating_evidence_ids, transmission_chain, computed,
                    created_at, updated_at
                ) VALUES (
                    :sanad_id, :tenant_id, :claim_id, :deal_id, :primary_evidence_id,
                    CAST(:corroborating_evidence_ids AS JSONB),
                    CAST(:transmission_chain AS JSONB),
                    CAST(:computed AS JSONB), :created_at, NULL
                )
                """
            ),
            {
                "sanad_id": sanad_id,
                "tenant_id": self._tenant_id,
                "claim_id": claim_id,
                "deal_id": deal_id,
                "primary_evidence_id": primary_evidence_id,
                "corroborating_evidence_ids": json.dumps(corroborating_evidence_ids or []),
                "transmission_chain": json.dumps(transmission_chain or []),
                "computed": json.dumps(computed or {}),
                "created_at": now,
            },
        )

        return {
            "sanad_id": sanad_id,
            "tenant_id": self._tenant_id,
            "claim_id": claim_id,
            "deal_id": deal_id,
            "primary_evidence_id": primary_evidence_id,
            "corroborating_evidence_ids": corroborating_evidence_ids or [],
            "transmission_chain": transmission_chain or [],
            "computed": computed or {},
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "updated_at": None,
        }

    def get(self, sanad_id: str) -> dict[str, Any] | None:
        """Get a sanad by ID."""
        result = self._conn.execute(
            text(
                """
                SELECT sanad_id, tenant_id, claim_id, deal_id, primary_evidence_id,
                       corroborating_evidence_ids, transmission_chain, computed,
                       created_at, updated_at
                FROM sanads
                WHERE sanad_id = :sanad_id
                """
            ),
            {"sanad_id": sanad_id},
        ).fetchone()

        if result is None:
            return None

        return self._row_to_dict(result)

    def get_by_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Get sanad by claim ID."""
        result = self._conn.execute(
            text(
                """
                SELECT sanad_id, tenant_id, claim_id, deal_id, primary_evidence_id,
                       corroborating_evidence_ids, transmission_chain, computed,
                       created_at, updated_at
                FROM sanads
                WHERE claim_id = :claim_id
                """
            ),
            {"claim_id": claim_id},
        ).fetchone()

        if result is None:
            return None

        return self._row_to_dict(result)

    def update(
        self,
        sanad_id: str,
        *,
        corroborating_evidence_ids: list[str] | None = None,
        transmission_chain: list[dict[str, Any]] | None = None,
        computed: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing sanad."""
        existing = self.get(sanad_id)
        if existing is None:
            return None

        now = datetime.now(UTC)
        new_corr = (
            corroborating_evidence_ids
            if corroborating_evidence_ids is not None
            else existing["corroborating_evidence_ids"]
        )
        new_chain = (
            transmission_chain if transmission_chain is not None else existing["transmission_chain"]
        )
        new_computed = computed if computed is not None else existing["computed"]

        self._conn.execute(
            text(
                """
                UPDATE sanads SET
                    corroborating_evidence_ids = CAST(:corroborating_evidence_ids AS JSONB),
                    transmission_chain = CAST(:transmission_chain AS JSONB),
                    computed = CAST(:computed AS JSONB),
                    updated_at = :updated_at
                WHERE sanad_id = :sanad_id
                """
            ),
            {
                "sanad_id": sanad_id,
                "corroborating_evidence_ids": json.dumps(new_corr),
                "transmission_chain": json.dumps(new_chain),
                "computed": json.dumps(new_computed),
                "updated_at": now,
            },
        )

        return {
            **existing,
            "corroborating_evidence_ids": new_corr,
            "transmission_chain": new_chain,
            "computed": new_computed,
            "updated_at": now.isoformat().replace("+00:00", "Z"),
        }

    def list_by_deal(
        self,
        deal_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List sanads for a deal."""
        effective_limit = min(max(1, limit), 200)

        if cursor:
            result = self._conn.execute(
                text(
                    """
                    SELECT sanad_id, tenant_id, claim_id, deal_id, primary_evidence_id,
                           corroborating_evidence_ids, transmission_chain, computed,
                           created_at, updated_at
                    FROM sanads
                    WHERE deal_id = :deal_id AND sanad_id > :cursor
                    ORDER BY sanad_id
                    LIMIT :limit
                    """
                ),
                {"deal_id": deal_id, "cursor": cursor, "limit": effective_limit + 1},
            ).fetchall()
        else:
            result = self._conn.execute(
                text(
                    """
                    SELECT sanad_id, tenant_id, claim_id, deal_id, primary_evidence_id,
                           corroborating_evidence_ids, transmission_chain, computed,
                           created_at, updated_at
                    FROM sanads
                    WHERE deal_id = :deal_id
                    ORDER BY sanad_id
                    LIMIT :limit
                    """
                ),
                {"deal_id": deal_id, "limit": effective_limit + 1},
            ).fetchall()

        sanads = [self._row_to_dict(row) for row in result[:effective_limit]]

        next_cursor = None
        if len(result) > effective_limit:
            next_cursor = sanads[-1]["sanad_id"]

        return sanads, next_cursor

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert database row to dict."""
        created_at = row.created_at
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat().replace("+00:00", "Z")

        updated_at = row.updated_at
        if updated_at is not None and hasattr(updated_at, "isoformat"):
            updated_at = updated_at.isoformat().replace("+00:00", "Z")

        corroborating_evidence_ids = row.corroborating_evidence_ids
        if isinstance(corroborating_evidence_ids, str):
            corroborating_evidence_ids = json.loads(corroborating_evidence_ids)

        transmission_chain = row.transmission_chain
        if isinstance(transmission_chain, str):
            transmission_chain = json.loads(transmission_chain)

        computed = row.computed
        if isinstance(computed, str):
            computed = json.loads(computed)

        return {
            "sanad_id": str(row.sanad_id),
            "tenant_id": str(row.tenant_id),
            "claim_id": str(row.claim_id),
            "deal_id": str(row.deal_id),
            "primary_evidence_id": row.primary_evidence_id,
            "corroborating_evidence_ids": corroborating_evidence_ids or [],
            "transmission_chain": transmission_chain or [],
            "computed": computed or {},
            "created_at": created_at,
            "updated_at": updated_at,
        }


class DefectsRepository:
    """Repository for defect persistence operations."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with connection and tenant context."""
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(
        self,
        *,
        defect_id: str,
        claim_id: str | None,
        deal_id: str | None,
        defect_type: str,
        severity: str,
        description: str,
        cure_protocol: str,
        status: str = "OPEN",
        waiver_reason: str | None = None,
        waived_by: str | None = None,
        cured_by: str | None = None,
        cured_reason: str | None = None,
    ) -> dict[str, Any]:
        """Create a new defect."""
        now = datetime.now(UTC)

        self._conn.execute(
            text(
                """
                INSERT INTO defects (
                    defect_id, tenant_id, claim_id, deal_id, defect_type, severity,
                    description, cure_protocol, status, waiver_reason, waived_by,
                    cured_by, cured_reason, waived, waived_at, cured_at,
                    created_at, updated_at
                ) VALUES (
                    :defect_id, :tenant_id, :claim_id, :deal_id, :defect_type, :severity,
                    :description, :cure_protocol, :status, :waiver_reason, :waived_by,
                    :cured_by, :cured_reason, :waived, NULL, NULL, :created_at, NULL
                )
                """
            ),
            {
                "defect_id": defect_id,
                "tenant_id": self._tenant_id,
                "claim_id": claim_id,
                "deal_id": deal_id,
                "defect_type": defect_type,
                "severity": severity,
                "description": description,
                "cure_protocol": cure_protocol,
                "status": status,
                "waiver_reason": waiver_reason,
                "waived_by": waived_by,
                "cured_by": cured_by,
                "cured_reason": cured_reason,
                "waived": False,
                "created_at": now,
            },
        )

        return {
            "defect_id": defect_id,
            "tenant_id": self._tenant_id,
            "claim_id": claim_id,
            "deal_id": deal_id,
            "defect_type": defect_type,
            "severity": severity,
            "description": description,
            "cure_protocol": cure_protocol,
            "status": status,
            "waiver_reason": waiver_reason,
            "waived_by": waived_by,
            "cured_by": cured_by,
            "cured_reason": cured_reason,
            "waived": False,
            "waived_at": None,
            "cured_at": None,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "updated_at": None,
        }

    def get(self, defect_id: str) -> dict[str, Any] | None:
        """Get a defect by ID."""
        result = self._conn.execute(
            text(
                """
                SELECT defect_id, tenant_id, claim_id, deal_id, defect_type, severity,
                       description, cure_protocol, status, waiver_reason, waived_by,
                       cured_by, cured_reason, waived, waived_at, cured_at,
                       created_at, updated_at
                FROM defects
                WHERE defect_id = :defect_id
                """
            ),
            {"defect_id": defect_id},
        ).fetchone()

        if result is None:
            return None

        return self._row_to_dict(result)

    def list_by_claim(
        self,
        claim_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List defects for a claim."""
        effective_limit = min(max(1, limit), 200)

        if cursor:
            result = self._conn.execute(
                text(
                    """
                    SELECT defect_id, tenant_id, claim_id, deal_id, defect_type, severity,
                           description, cure_protocol, status, waiver_reason, waived_by,
                           cured_by, cured_reason, waived, waived_at, cured_at,
                           created_at, updated_at
                    FROM defects
                    WHERE claim_id = :claim_id AND defect_id > :cursor
                    ORDER BY defect_id
                    LIMIT :limit
                    """
                ),
                {"claim_id": claim_id, "cursor": cursor, "limit": effective_limit + 1},
            ).fetchall()
        else:
            result = self._conn.execute(
                text(
                    """
                    SELECT defect_id, tenant_id, claim_id, deal_id, defect_type, severity,
                           description, cure_protocol, status, waiver_reason, waived_by,
                           cured_by, cured_reason, waived, waived_at, cured_at,
                           created_at, updated_at
                    FROM defects
                    WHERE claim_id = :claim_id
                    ORDER BY defect_id
                    LIMIT :limit
                    """
                ),
                {"claim_id": claim_id, "limit": effective_limit + 1},
            ).fetchall()

        defects = [self._row_to_dict(row) for row in result[:effective_limit]]

        next_cursor = None
        if len(result) > effective_limit:
            next_cursor = defects[-1]["defect_id"]

        return defects, next_cursor

    def list_by_deal(
        self,
        deal_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List defects for a deal."""
        effective_limit = min(max(1, limit), 200)

        if cursor:
            result = self._conn.execute(
                text(
                    """
                    SELECT defect_id, tenant_id, claim_id, deal_id, defect_type, severity,
                           description, cure_protocol, status, waiver_reason, waived_by,
                           cured_by, cured_reason, waived, waived_at, cured_at,
                           created_at, updated_at
                    FROM defects
                    WHERE deal_id = :deal_id AND defect_id > :cursor
                    ORDER BY defect_id
                    LIMIT :limit
                    """
                ),
                {"deal_id": deal_id, "cursor": cursor, "limit": effective_limit + 1},
            ).fetchall()
        else:
            result = self._conn.execute(
                text(
                    """
                    SELECT defect_id, tenant_id, claim_id, deal_id, defect_type, severity,
                           description, cure_protocol, status, waiver_reason, waived_by,
                           cured_by, cured_reason, waived, waived_at, cured_at,
                           created_at, updated_at
                    FROM defects
                    WHERE deal_id = :deal_id
                    ORDER BY defect_id
                    LIMIT :limit
                    """
                ),
                {"deal_id": deal_id, "limit": effective_limit + 1},
            ).fetchall()

        defects = [self._row_to_dict(row) for row in result[:effective_limit]]

        next_cursor = None
        if len(result) > effective_limit:
            next_cursor = defects[-1]["defect_id"]

        return defects, next_cursor

    def update(
        self,
        defect_id: str,
        *,
        description: str | None = None,
        status: str | None = None,
        waiver_reason: str | None = None,
        waived_by: str | None = None,
        cured_by: str | None = None,
        cured_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing defect."""
        existing = self.get(defect_id)
        if existing is None:
            return None

        now = datetime.now(UTC)
        new_desc = description if description is not None else existing["description"]
        new_status = status if status is not None else existing["status"]
        new_waiver_reason = (
            waiver_reason if waiver_reason is not None else existing.get("waiver_reason")
        )
        new_waived_by = waived_by if waived_by is not None else existing.get("waived_by")
        new_cured_by = cured_by if cured_by is not None else existing.get("cured_by")
        new_cured_reason = (
            cured_reason if cured_reason is not None else existing.get("cured_reason")
        )
        new_waived = new_status == "WAIVED"
        new_waived_at = (
            now
            if new_status == "WAIVED" and not existing.get("waived")
            else existing.get("waived_at")
        )
        new_cured_at = (
            now
            if new_status == "CURED" and existing.get("status") != "CURED"
            else existing.get("cured_at")
        )

        self._conn.execute(
            text(
                """
                UPDATE defects SET
                    description = :description,
                    status = :status,
                    waiver_reason = :waiver_reason,
                    waived_by = :waived_by,
                    cured_by = :cured_by,
                    cured_reason = :cured_reason,
                    waived = :waived,
                    waived_at = :waived_at,
                    cured_at = :cured_at,
                    updated_at = :updated_at
                WHERE defect_id = :defect_id
                """
            ),
            {
                "defect_id": defect_id,
                "description": new_desc,
                "status": new_status,
                "waiver_reason": new_waiver_reason,
                "waived_by": new_waived_by,
                "cured_by": new_cured_by,
                "cured_reason": new_cured_reason,
                "waived": new_waived,
                "waived_at": new_waived_at if isinstance(new_waived_at, datetime) else None,
                "cured_at": new_cured_at if isinstance(new_cured_at, datetime) else None,
                "updated_at": now,
            },
        )

        waived_at_str = None
        if isinstance(new_waived_at, datetime):
            waived_at_str = new_waived_at.isoformat().replace("+00:00", "Z")
        elif isinstance(new_waived_at, str):
            waived_at_str = new_waived_at

        cured_at_str = None
        if isinstance(new_cured_at, datetime):
            cured_at_str = new_cured_at.isoformat().replace("+00:00", "Z")
        elif isinstance(new_cured_at, str):
            cured_at_str = new_cured_at

        return {
            **existing,
            "description": new_desc,
            "status": new_status,
            "waiver_reason": new_waiver_reason,
            "waived_by": new_waived_by,
            "cured_by": new_cured_by,
            "cured_reason": new_cured_reason,
            "waived": new_waived,
            "waived_at": waived_at_str,
            "cured_at": cured_at_str,
            "updated_at": now.isoformat().replace("+00:00", "Z"),
        }

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert database row to dict."""
        created_at = row.created_at
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat().replace("+00:00", "Z")

        updated_at = row.updated_at
        if updated_at is not None and hasattr(updated_at, "isoformat"):
            updated_at = updated_at.isoformat().replace("+00:00", "Z")

        waived_at = getattr(row, "waived_at", None)
        if waived_at is not None and hasattr(waived_at, "isoformat"):
            waived_at = waived_at.isoformat().replace("+00:00", "Z")

        cured_at = getattr(row, "cured_at", None)
        if cured_at is not None and hasattr(cured_at, "isoformat"):
            cured_at = cured_at.isoformat().replace("+00:00", "Z")

        return {
            "defect_id": str(row.defect_id),
            "tenant_id": str(row.tenant_id),
            "claim_id": str(row.claim_id) if row.claim_id else None,
            "deal_id": str(getattr(row, "deal_id", None))
            if getattr(row, "deal_id", None)
            else None,
            "defect_type": row.defect_type,
            "severity": row.severity,
            "description": row.description,
            "cure_protocol": row.cure_protocol,
            "status": getattr(row, "status", "OPEN"),
            "waiver_reason": getattr(row, "waiver_reason", None),
            "waived_by": getattr(row, "waived_by", None),
            "cured_by": getattr(row, "cured_by", None),
            "cured_reason": getattr(row, "cured_reason", None),
            "waived": getattr(row, "waived", False),
            "waived_at": waived_at,
            "cured_at": cured_at,
            "created_at": created_at,
            "updated_at": updated_at,
        }


_claims_in_memory_store: dict[str, dict[str, Any]] = {}
_sanad_in_memory_store: dict[str, dict[str, Any]] = {}
_defects_in_memory_store: dict[str, dict[str, Any]] = {}
_evidence_in_memory_store: dict[str, dict[str, Any]] = {}


class InMemoryClaimsRepository:
    """In-memory fallback repository for claims."""

    def __init__(self, tenant_id: str) -> None:
        """Initialize with tenant context."""
        self._tenant_id = tenant_id

    def create(
        self,
        *,
        claim_id: str,
        deal_id: str,
        claim_class: str,
        claim_text: str,
        predicate: str | None = None,
        value: dict[str, Any] | None = None,
        sanad_id: str | None = None,
        claim_grade: str = "D",
        corroboration: dict[str, Any] | None = None,
        claim_verdict: str = "UNVERIFIED",
        claim_action: str = "VERIFY",
        defect_ids: list[str] | None = None,
        materiality: str = "MEDIUM",
        ic_bound: bool = False,
        primary_span_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new claim in memory."""
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        corroboration = corroboration or {"level": "AHAD", "independent_chain_count": 1}

        claim = {
            "claim_id": claim_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "claim_class": claim_class,
            "claim_text": claim_text,
            "predicate": predicate,
            "value": value,
            "sanad_id": sanad_id,
            "claim_grade": claim_grade,
            "corroboration": corroboration,
            "claim_verdict": claim_verdict,
            "claim_action": claim_action,
            "defect_ids": defect_ids or [],
            "materiality": materiality,
            "ic_bound": ic_bound,
            "primary_span_id": primary_span_id,
            "created_at": now,
            "updated_at": None,
        }

        _claims_in_memory_store[claim_id] = claim
        return claim

    def get(self, claim_id: str) -> dict[str, Any] | None:
        """Get a claim by ID from memory."""
        claim = _claims_in_memory_store.get(claim_id)
        if claim is None or claim.get("tenant_id") != self._tenant_id:
            return None
        return claim

    def list_by_deal(
        self,
        deal_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List claims for a deal from memory."""
        claims = [
            c
            for c in _claims_in_memory_store.values()
            if c.get("tenant_id") == self._tenant_id and c.get("deal_id") == deal_id
        ]
        claims.sort(key=lambda x: x["claim_id"])

        if cursor:
            claims = [c for c in claims if c["claim_id"] > cursor]

        effective_limit = min(max(1, limit), 200)
        items = claims[:effective_limit]

        next_cursor = None
        if len(claims) > effective_limit:
            next_cursor = items[-1]["claim_id"]

        return items, next_cursor

    def update_grade(
        self,
        claim_id: str,
        *,
        claim_grade: str | None = None,
        sanad_id: str | None = None,
    ) -> None:
        """Update claim_grade and/or sanad_id for a claim in memory.

        Args:
            claim_id: Claim UUID.
            claim_grade: New grade letter (A/B/C/D).
            sanad_id: Sanad UUID to link.
        """
        claim = _claims_in_memory_store.get(claim_id)
        if claim is None or claim.get("tenant_id") != self._tenant_id:
            return
        if claim_grade is not None:
            claim["claim_grade"] = claim_grade
        if sanad_id is not None:
            claim["sanad_id"] = sanad_id

    def delete(self, claim_id: str) -> bool:
        """Delete a claim from memory."""
        claim = _claims_in_memory_store.get(claim_id)
        if claim is None or claim.get("tenant_id") != self._tenant_id:
            return False
        del _claims_in_memory_store[claim_id]
        return claim_id not in _claims_in_memory_store


class InMemorySanadsRepository:
    """In-memory fallback repository for sanads."""

    def __init__(self, tenant_id: str) -> None:
        """Initialize with tenant context."""
        self._tenant_id = tenant_id

    def create(
        self,
        *,
        sanad_id: str,
        claim_id: str,
        deal_id: str,
        primary_evidence_id: str,
        corroborating_evidence_ids: list[str] | None = None,
        transmission_chain: list[dict[str, Any]] | None = None,
        computed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new sanad in memory."""
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        sanad = {
            "sanad_id": sanad_id,
            "tenant_id": self._tenant_id,
            "claim_id": claim_id,
            "deal_id": deal_id,
            "primary_evidence_id": primary_evidence_id,
            "corroborating_evidence_ids": corroborating_evidence_ids or [],
            "transmission_chain": transmission_chain or [],
            "computed": computed or {},
            "created_at": now,
            "updated_at": None,
        }
        _sanad_in_memory_store[sanad_id] = sanad
        return sanad

    def get(self, sanad_id: str) -> dict[str, Any] | None:
        """Get a sanad by ID from memory."""
        sanad = _sanad_in_memory_store.get(sanad_id)
        if sanad is None or sanad.get("tenant_id") != self._tenant_id:
            return None
        return sanad

    def get_by_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Get sanad by claim ID from memory."""
        for sanad in _sanad_in_memory_store.values():
            if sanad.get("claim_id") == claim_id and sanad.get("tenant_id") == self._tenant_id:
                return sanad
        return None

    def update(
        self,
        sanad_id: str,
        *,
        corroborating_evidence_ids: list[str] | None = None,
        transmission_chain: list[dict[str, Any]] | None = None,
        computed: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing sanad in memory."""
        existing = self.get(sanad_id)
        if existing is None:
            return None

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if corroborating_evidence_ids is not None:
            existing["corroborating_evidence_ids"] = corroborating_evidence_ids
        if transmission_chain is not None:
            existing["transmission_chain"] = transmission_chain
        if computed is not None:
            existing["computed"] = computed
        existing["updated_at"] = now

        _sanad_in_memory_store[sanad_id] = existing
        return existing

    def list_by_deal(
        self,
        deal_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List sanads for a deal from memory."""
        sanads = [
            s
            for s in _sanad_in_memory_store.values()
            if s.get("tenant_id") == self._tenant_id and s.get("deal_id") == deal_id
        ]
        sanads.sort(key=lambda x: x["sanad_id"])

        if cursor:
            sanads = [s for s in sanads if s["sanad_id"] > cursor]

        effective_limit = min(max(1, limit), 200)
        items = sanads[:effective_limit]

        next_cursor = None
        if len(sanads) > effective_limit:
            next_cursor = items[-1]["sanad_id"]

        return items, next_cursor


class InMemoryDefectsRepository:
    """In-memory fallback repository for defects."""

    def __init__(self, tenant_id: str) -> None:
        """Initialize with tenant context."""
        self._tenant_id = tenant_id

    def create(
        self,
        *,
        defect_id: str,
        claim_id: str | None,
        deal_id: str | None,
        defect_type: str,
        severity: str,
        description: str,
        cure_protocol: str,
        status: str = "OPEN",
        waiver_reason: str | None = None,
        waived_by: str | None = None,
        cured_by: str | None = None,
        cured_reason: str | None = None,
    ) -> dict[str, Any]:
        """Create a new defect in memory."""
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        defect = {
            "defect_id": defect_id,
            "tenant_id": self._tenant_id,
            "claim_id": claim_id,
            "deal_id": deal_id,
            "defect_type": defect_type,
            "severity": severity,
            "description": description,
            "cure_protocol": cure_protocol,
            "status": status,
            "waiver_reason": waiver_reason,
            "waived_by": waived_by,
            "cured_by": cured_by,
            "cured_reason": cured_reason,
            "waived": False,
            "waived_at": None,
            "cured_at": None,
            "created_at": now,
            "updated_at": None,
        }
        _defects_in_memory_store[defect_id] = defect
        return defect

    def get(self, defect_id: str) -> dict[str, Any] | None:
        """Get a defect by ID from memory."""
        defect = _defects_in_memory_store.get(defect_id)
        if defect is None or defect.get("tenant_id") != self._tenant_id:
            return None
        return defect

    def list_by_claim(
        self,
        claim_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List defects for a claim from memory."""
        defects = [
            d
            for d in _defects_in_memory_store.values()
            if d.get("tenant_id") == self._tenant_id and d.get("claim_id") == claim_id
        ]
        defects.sort(key=lambda x: x["defect_id"])

        if cursor:
            defects = [d for d in defects if d["defect_id"] > cursor]

        effective_limit = min(max(1, limit), 200)
        items = defects[:effective_limit]

        next_cursor = None
        if len(defects) > effective_limit:
            next_cursor = items[-1]["defect_id"]

        return items, next_cursor

    def list_by_deal(
        self,
        deal_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List defects for a deal from memory."""
        defects = [
            d
            for d in _defects_in_memory_store.values()
            if d.get("tenant_id") == self._tenant_id and d.get("deal_id") == deal_id
        ]
        defects.sort(key=lambda x: x["defect_id"])

        if cursor:
            defects = [d for d in defects if d["defect_id"] > cursor]

        effective_limit = min(max(1, limit), 200)
        items = defects[:effective_limit]

        next_cursor = None
        if len(defects) > effective_limit:
            next_cursor = items[-1]["defect_id"]

        return items, next_cursor

    def update(
        self,
        defect_id: str,
        *,
        description: str | None = None,
        status: str | None = None,
        waiver_reason: str | None = None,
        waived_by: str | None = None,
        cured_by: str | None = None,
        cured_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing defect in memory."""
        existing = self.get(defect_id)
        if existing is None:
            return None

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if description is not None:
            existing["description"] = description
        if status is not None:
            existing["status"] = status
            if status == "WAIVED" and not existing.get("waived"):
                existing["waived"] = True
                existing["waived_at"] = now
            if status == "CURED" and existing.get("status") != "CURED":
                existing["cured_at"] = now
        if waiver_reason is not None:
            existing["waiver_reason"] = waiver_reason
        if waived_by is not None:
            existing["waived_by"] = waived_by
        if cured_by is not None:
            existing["cured_by"] = cured_by
        if cured_reason is not None:
            existing["cured_reason"] = cured_reason
        existing["updated_at"] = now

        _defects_in_memory_store[defect_id] = existing
        return existing


def clear_claims_in_memory_store() -> None:
    """Clear the in-memory claims store. For testing only."""
    _claims_in_memory_store.clear()


def clear_sanad_in_memory_store() -> None:
    """Clear the in-memory sanad store. For testing only."""
    _sanad_in_memory_store.clear()


def clear_defects_in_memory_store() -> None:
    """Clear the in-memory defects store. For testing only."""
    _defects_in_memory_store.clear()


def clear_evidence_in_memory_store() -> None:
    """Clear the in-memory evidence store. For testing only."""
    _evidence_in_memory_store.clear()


def clear_all_claims_stores() -> None:
    """Clear all in-memory stores. For testing only."""
    clear_claims_in_memory_store()
    clear_sanad_in_memory_store()
    clear_defects_in_memory_store()
    clear_evidence_in_memory_store()


def seed_claim_in_memory(claim_data: dict[str, Any]) -> None:
    """Seed a claim into the in-memory store. For testing only."""
    _claims_in_memory_store[claim_data["claim_id"]] = claim_data


def seed_sanad_in_memory(sanad_data: dict[str, Any]) -> None:
    """Seed a sanad into the in-memory store. For testing only."""
    _sanad_in_memory_store[sanad_data["sanad_id"]] = sanad_data


def seed_defect_in_memory(defect_data: dict[str, Any]) -> None:
    """Seed a defect into the in-memory store. For testing only."""
    _defects_in_memory_store[defect_data["defect_id"]] = defect_data


def seed_evidence_in_memory(evidence_data: dict[str, Any]) -> None:
    """Seed evidence into the in-memory store. For testing only."""
    _evidence_in_memory_store[evidence_data["evidence_id"]] = evidence_data


class InMemoryEvidenceRepository:
    """In-memory fallback repository for evidence items."""

    def __init__(self, tenant_id: str) -> None:
        """Initialize with tenant context.

        Args:
            tenant_id: Tenant UUID for scoping.
        """
        self._tenant_id = tenant_id

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
        """Create an evidence record in memory.

        Args:
            evidence_id: UUID for the evidence item.
            tenant_id: Tenant UUID.
            deal_id: Deal UUID.
            claim_id: Parent claim UUID.
            source_span_id: Source span UUID.
            source_grade: Sanad grade (default D).
            verification_status: Verification state.

        Returns:
            Created evidence dict.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        evidence = {
            "evidence_id": evidence_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "claim_id": claim_id,
            "source_span_id": source_span_id,
            "source_grade": source_grade,
            "verification_status": verification_status,
            "created_at": now,
        }
        _evidence_in_memory_store[evidence_id] = evidence
        return evidence

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        """Get evidence by ID.

        Args:
            evidence_id: UUID of the evidence item.

        Returns:
            Evidence dict or None.
        """
        ev = _evidence_in_memory_store.get(evidence_id)
        if ev is None or ev.get("tenant_id") != self._tenant_id:
            return None
        return ev

    def get_by_claim(self, claim_id: str) -> list[dict[str, Any]]:
        """Get all evidence items for a claim.

        Args:
            claim_id: Parent claim UUID.

        Returns:
            List of evidence dicts for this claim and tenant.
        """
        return [
            ev
            for ev in _evidence_in_memory_store.values()
            if ev.get("claim_id") == claim_id and ev.get("tenant_id") == self._tenant_id
        ]
