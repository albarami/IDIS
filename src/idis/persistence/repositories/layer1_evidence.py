"""Tenant-scoped repositories for durable Layer-1 evidence trust court output (Slice92).

Persists the safe-shape rows from :mod:`idis.models.layer1_durability` into the
migration-0021 tables. Deterministic primary keys (package_id / finding_id /
record_id) make every upsert idempotent, so retries and resumed runs never
duplicate rows. The Postgres repository follows the canonical conventions
(``set_tenant_local`` + NULLIF RLS + ON CONFLICT upsert); the in-memory twin
keeps non-database runs and the default test suite hermetic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from idis.models.layer1_durability import (
    EvidenceTrustFindingRow,
    MuhasabahRecordRow,
    ValidatedEvidencePackageRow,
)
from idis.persistence.db import is_postgres_configured, set_tenant_local


class PostgresLayer1EvidenceRepository:
    """Persist and list durable Layer-1 court/VEP/Muḥāsabah rows in Postgres."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with tenant-scoped connection."""
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def _require_tenant_match(self, row_tenant_id: str) -> None:
        if row_tenant_id != self._tenant_id:
            raise ValueError("row tenant_id does not match repository tenant scope")

    def upsert_validated_evidence_package(self, row: ValidatedEvidencePackageRow) -> dict[str, Any]:
        """Insert or update one durable VEP candidate row (idempotent by package_id)."""
        self._require_tenant_match(row.tenant_id)
        now = datetime.now(UTC)
        stored = self._conn.execute(
            text(
                """
                INSERT INTO validated_evidence_packages (
                    package_id, tenant_id, deal_id, run_id, court_id, dashboard_id,
                    status, safe_summary, created_at, updated_at
                )
                VALUES (
                    :package_id, :tenant_id, :deal_id, :run_id, :court_id, :dashboard_id,
                    :status, CAST(:safe_summary AS jsonb), :now, :now
                )
                ON CONFLICT (package_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    safe_summary = EXCLUDED.safe_summary,
                    updated_at = EXCLUDED.updated_at
                RETURNING package_id
                """
            ),
            {
                "package_id": row.package_id,
                "tenant_id": row.tenant_id,
                "deal_id": row.deal_id,
                "run_id": row.run_id,
                "court_id": row.court_id,
                "dashboard_id": row.dashboard_id,
                "status": row.status,
                "safe_summary": json.dumps(row.safe_summary, sort_keys=True),
                "now": now,
            },
        ).one()
        return {"package_id": str(stored.package_id), "tenant_id": row.tenant_id}

    def upsert_evidence_trust_finding(self, row: EvidenceTrustFindingRow) -> dict[str, Any]:
        """Insert or update one durable court finding row.

        Idempotent by the composite (tenant_id, run_id, finding_id) key — finding
        ids carry only 48 bits of entropy, so a bare finding_id key could collide
        across tenants/runs.
        """
        self._require_tenant_match(row.tenant_id)
        now = datetime.now(UTC)
        stored = self._conn.execute(
            text(
                """
                INSERT INTO evidence_trust_findings (
                    finding_id, tenant_id, deal_id, run_id, court_id, finding_type,
                    claim_id, evidence_ids, sanad_id, calc_ids, defect_ids,
                    reason_codes, created_at, updated_at
                )
                VALUES (
                    :finding_id, :tenant_id, :deal_id, :run_id, :court_id, :finding_type,
                    :claim_id, CAST(:evidence_ids AS jsonb), :sanad_id,
                    CAST(:calc_ids AS jsonb), CAST(:defect_ids AS jsonb),
                    CAST(:reason_codes AS jsonb), :now, :now
                )
                ON CONFLICT (tenant_id, run_id, finding_id)
                DO UPDATE SET
                    finding_type = EXCLUDED.finding_type,
                    reason_codes = EXCLUDED.reason_codes,
                    updated_at = EXCLUDED.updated_at
                RETURNING finding_id
                """
            ),
            {
                "finding_id": row.finding_id,
                "tenant_id": row.tenant_id,
                "deal_id": row.deal_id,
                "run_id": row.run_id,
                "court_id": row.court_id,
                "finding_type": row.finding_type,
                "claim_id": row.claim_id,
                "evidence_ids": json.dumps(row.evidence_ids),
                "sanad_id": row.sanad_id,
                "calc_ids": json.dumps(row.calc_ids),
                "defect_ids": json.dumps(row.defect_ids),
                "reason_codes": json.dumps(row.reason_codes),
                "now": now,
            },
        ).one()
        return {"finding_id": str(stored.finding_id), "tenant_id": row.tenant_id}

    def upsert_muhasabah_record(self, row: MuhasabahRecordRow) -> dict[str, Any]:
        """Insert or update one durable Muḥāsabah row (idempotent by record_id)."""
        self._require_tenant_match(row.tenant_id)
        now = datetime.now(UTC)
        stored = self._conn.execute(
            text(
                """
                INSERT INTO muhasabah_records (
                    record_id, tenant_id, deal_id, run_id, source_step, agent_id,
                    output_id, confidence, is_subjective, supported_claim_ids,
                    supported_calc_ids, uncertainties, record_timestamp,
                    created_at, updated_at
                )
                VALUES (
                    :record_id, :tenant_id, :deal_id, :run_id, :source_step, :agent_id,
                    :output_id, :confidence, :is_subjective,
                    CAST(:supported_claim_ids AS jsonb), CAST(:supported_calc_ids AS jsonb),
                    CAST(:uncertainties AS jsonb), :record_timestamp, :now, :now
                )
                ON CONFLICT (record_id)
                DO UPDATE SET
                    confidence = EXCLUDED.confidence,
                    is_subjective = EXCLUDED.is_subjective,
                    supported_claim_ids = EXCLUDED.supported_claim_ids,
                    supported_calc_ids = EXCLUDED.supported_calc_ids,
                    uncertainties = EXCLUDED.uncertainties,
                    record_timestamp = EXCLUDED.record_timestamp,
                    updated_at = EXCLUDED.updated_at
                RETURNING record_id
                """
            ),
            {
                "record_id": row.record_id,
                "tenant_id": row.tenant_id,
                "deal_id": row.deal_id,
                "run_id": row.run_id,
                "source_step": row.source_step,
                "agent_id": row.agent_id,
                "output_id": row.output_id,
                "confidence": row.confidence,
                "is_subjective": row.is_subjective,
                "supported_claim_ids": json.dumps(row.supported_claim_ids),
                "supported_calc_ids": json.dumps(row.supported_calc_ids),
                "uncertainties": json.dumps(row.uncertainties, sort_keys=True),
                "record_timestamp": row.record_timestamp,
                "now": now,
            },
        ).one()
        return {"record_id": str(stored.record_id), "tenant_id": row.tenant_id}

    def list_validated_evidence_packages(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped durable VEP rows for a run, ordered by package_id."""
        rows = self._conn.execute(
            text(
                """
                SELECT package_id, tenant_id, deal_id, run_id, court_id, dashboard_id,
                       status, safe_summary
                FROM validated_evidence_packages
                WHERE run_id = :run_id
                ORDER BY package_id
                """
            ),
            {"run_id": run_id},
        ).fetchall()
        return [
            {
                "package_id": str(item.package_id),
                "tenant_id": str(item.tenant_id),
                "deal_id": str(item.deal_id),
                "run_id": str(item.run_id),
                "court_id": str(item.court_id),
                "dashboard_id": str(item.dashboard_id),
                "status": item.status,
                "safe_summary": item.safe_summary,
            }
            for item in rows
        ]

    def list_evidence_trust_findings(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped durable finding rows for a run, ordered by finding_id."""
        rows = self._conn.execute(
            text(
                """
                SELECT finding_id, tenant_id, deal_id, run_id, court_id, finding_type,
                       claim_id, evidence_ids, sanad_id, calc_ids, defect_ids, reason_codes
                FROM evidence_trust_findings
                WHERE run_id = :run_id
                ORDER BY finding_id
                """
            ),
            {"run_id": run_id},
        ).fetchall()
        return [
            {
                "finding_id": str(item.finding_id),
                "tenant_id": str(item.tenant_id),
                "deal_id": str(item.deal_id),
                "run_id": str(item.run_id),
                "court_id": str(item.court_id),
                "finding_type": item.finding_type,
                "claim_id": str(item.claim_id),
                "evidence_ids": item.evidence_ids,
                "sanad_id": str(item.sanad_id) if item.sanad_id is not None else None,
                "calc_ids": item.calc_ids,
                "defect_ids": item.defect_ids,
                "reason_codes": item.reason_codes,
            }
            for item in rows
        ]

    def list_muhasabah_records(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped durable Muḥāsabah rows for a run, ordered by record_id."""
        rows = self._conn.execute(
            text(
                """
                SELECT record_id, tenant_id, deal_id, run_id, source_step, agent_id,
                       output_id, confidence, is_subjective, supported_claim_ids,
                       supported_calc_ids, uncertainties, record_timestamp
                FROM muhasabah_records
                WHERE run_id = :run_id
                ORDER BY record_id
                """
            ),
            {"run_id": run_id},
        ).fetchall()
        return [
            {
                "record_id": str(item.record_id),
                "tenant_id": str(item.tenant_id),
                "deal_id": str(item.deal_id),
                "run_id": str(item.run_id),
                "source_step": item.source_step,
                "agent_id": item.agent_id,
                "output_id": item.output_id,
                "confidence": float(item.confidence),
                "is_subjective": bool(item.is_subjective),
                "supported_claim_ids": item.supported_claim_ids,
                "supported_calc_ids": item.supported_calc_ids,
                "uncertainties": item.uncertainties,
                "record_timestamp": item.record_timestamp,
            }
            for item in rows
        ]


_in_memory_vep_store: dict[str, dict[str, Any]] = {}
_in_memory_finding_store: dict[str, dict[str, Any]] = {}
_in_memory_muhasabah_store: dict[str, dict[str, Any]] = {}


class InMemoryLayer1EvidenceRepository:
    """In-memory Layer-1 evidence repository twin for tests/local fallback."""

    def __init__(self, tenant_id: str) -> None:
        """Initialize tenant-scoped in-memory repository."""
        self._tenant_id = tenant_id

    def _require_tenant_match(self, row_tenant_id: str) -> None:
        if row_tenant_id != self._tenant_id:
            raise ValueError("row tenant_id does not match repository tenant scope")

    def upsert_validated_evidence_package(self, row: ValidatedEvidencePackageRow) -> dict[str, Any]:
        """Insert or update one VEP row in memory (idempotent by package_id)."""
        self._require_tenant_match(row.tenant_id)
        _in_memory_vep_store[row.package_id] = row.model_dump(mode="json")
        return {"package_id": row.package_id, "tenant_id": row.tenant_id}

    def upsert_evidence_trust_finding(self, row: EvidenceTrustFindingRow) -> dict[str, Any]:
        """Insert or update one finding row in memory.

        Keyed by the composite (tenant_id, run_id, finding_id) — mirroring the
        Postgres primary key — so 48-bit finding ids can never absorb another
        tenant's or run's row.
        """
        self._require_tenant_match(row.tenant_id)
        key = f"{row.tenant_id}|{row.run_id}|{row.finding_id}"
        _in_memory_finding_store[key] = row.model_dump(mode="json")
        return {"finding_id": row.finding_id, "tenant_id": row.tenant_id}

    def upsert_muhasabah_record(self, row: MuhasabahRecordRow) -> dict[str, Any]:
        """Insert or update one Muḥāsabah row in memory (idempotent by record_id)."""
        self._require_tenant_match(row.tenant_id)
        _in_memory_muhasabah_store[row.record_id] = row.model_dump(mode="json")
        return {"record_id": row.record_id, "tenant_id": row.tenant_id}

    def _list(
        self, store: dict[str, dict[str, Any]], *, run_id: str, sort_key: str
    ) -> list[dict[str, Any]]:
        return sorted(
            [
                item
                for item in store.values()
                if item["tenant_id"] == self._tenant_id and item["run_id"] == run_id
            ],
            key=lambda item: str(item[sort_key]),
        )

    def list_validated_evidence_packages(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped in-memory VEP rows for a run, ordered by package_id."""
        return self._list(_in_memory_vep_store, run_id=run_id, sort_key="package_id")

    def list_evidence_trust_findings(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped in-memory finding rows for a run, ordered by finding_id."""
        return self._list(_in_memory_finding_store, run_id=run_id, sort_key="finding_id")

    def list_muhasabah_records(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped in-memory Muḥāsabah rows for a run, ordered by record_id."""
        return self._list(_in_memory_muhasabah_store, run_id=run_id, sort_key="record_id")


def clear_in_memory_layer1_evidence_store() -> None:
    """Clear the in-memory Layer-1 evidence stores. For tests only."""
    _in_memory_vep_store.clear()
    _in_memory_finding_store.clear()
    _in_memory_muhasabah_store.clear()


def get_layer1_evidence_repository(
    conn: Connection | None,
    tenant_id: str,
) -> PostgresLayer1EvidenceRepository | InMemoryLayer1EvidenceRepository:
    """Return the appropriate Layer-1 evidence repository."""
    if conn is not None and is_postgres_configured():
        return PostgresLayer1EvidenceRepository(conn, tenant_id)
    return InMemoryLayer1EvidenceRepository(tenant_id)
