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
                    ic_bound, created_at, updated_at
                ) VALUES (
                    :claim_id, :tenant_id, :deal_id, :claim_class, :claim_text,
                    :predicate, CAST(:value AS JSONB), :sanad_id, :claim_grade,
                    CAST(:corroboration AS JSONB), :claim_verdict, :claim_action,
                    CAST(:defect_ids AS JSONB), :materiality, :ic_bound, :created_at, NULL
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
                       ic_bound, created_at, updated_at
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
                           ic_bound, created_at, updated_at
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
                           ic_bound, created_at, updated_at
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

    def get(self, defect_id: str) -> dict[str, Any] | None:
        """Get a defect by ID."""
        result = self._conn.execute(
            text(
                """
                SELECT defect_id, tenant_id, claim_id, defect_type, severity,
                       description, cure_protocol, waived, waived_by, waived_at,
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

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert database row to dict."""
        created_at = row.created_at
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat().replace("+00:00", "Z")

        updated_at = row.updated_at
        if updated_at is not None and hasattr(updated_at, "isoformat"):
            updated_at = updated_at.isoformat().replace("+00:00", "Z")

        waived_at = row.waived_at
        if waived_at is not None and hasattr(waived_at, "isoformat"):
            waived_at = waived_at.isoformat().replace("+00:00", "Z")

        return {
            "defect_id": str(row.defect_id),
            "tenant_id": str(row.tenant_id),
            "claim_id": str(row.claim_id) if row.claim_id else None,
            "defect_type": row.defect_type,
            "severity": row.severity,
            "description": row.description,
            "cure_protocol": row.cure_protocol,
            "waived": row.waived,
            "waived_by": row.waived_by,
            "waived_at": waived_at,
            "created_at": created_at,
            "updated_at": updated_at,
        }


_claims_in_memory_store: dict[str, dict[str, Any]] = {}
_sanad_in_memory_store: dict[str, dict[str, Any]] = {}
_defects_in_memory_store: dict[str, dict[str, Any]] = {}


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


class InMemoryDefectsRepository:
    """In-memory fallback repository for defects."""

    def __init__(self, tenant_id: str) -> None:
        """Initialize with tenant context."""
        self._tenant_id = tenant_id

    def get(self, defect_id: str) -> dict[str, Any] | None:
        """Get a defect by ID from memory."""
        defect = _defects_in_memory_store.get(defect_id)
        if defect is None or defect.get("tenant_id") != self._tenant_id:
            return None
        return defect


def clear_claims_in_memory_store() -> None:
    """Clear the in-memory claims store. For testing only."""
    _claims_in_memory_store.clear()


def clear_sanad_in_memory_store() -> None:
    """Clear the in-memory sanad store. For testing only."""
    _sanad_in_memory_store.clear()


def clear_defects_in_memory_store() -> None:
    """Clear the in-memory defects store. For testing only."""
    _defects_in_memory_store.clear()


def clear_all_claims_stores() -> None:
    """Clear all in-memory stores. For testing only."""
    clear_claims_in_memory_store()
    clear_sanad_in_memory_store()
    clear_defects_in_memory_store()


def seed_claim_in_memory(claim_data: dict[str, Any]) -> None:
    """Seed a claim into the in-memory store. For testing only."""
    _claims_in_memory_store[claim_data["claim_id"]] = claim_data


def seed_sanad_in_memory(sanad_data: dict[str, Any]) -> None:
    """Seed a sanad into the in-memory store. For testing only."""
    _sanad_in_memory_store[sanad_data["sanad_id"]] = sanad_data


def seed_defect_in_memory(defect_data: dict[str, Any]) -> None:
    """Seed a defect into the in-memory store. For testing only."""
    _defects_in_memory_store[defect_data["defect_id"]] = defect_data
