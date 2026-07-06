"""Tenant-scoped repositories for durable Layer-2 IC challenge output (Slice93).

Persists the safe-shape rows from :mod:`idis.models.layer2_durability` into the
migration-0022 tables. The challenge is keyed by its deterministic UUID5
``challenge_id``; findings are keyed by the composite (tenant_id, run_id,
finding_id) because ``finding_id`` is a prefixed / LLM-supplied string. Both
upserts are idempotent (ON CONFLICT), so retries and resumed runs never
duplicate rows. The Postgres repository follows the canonical conventions
(``set_tenant_local`` + NULLIF RLS); the in-memory twin keeps non-database runs
and the default test suite hermetic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from idis.models.layer2_durability import Layer2ChallengeRow, Layer2FindingRow
from idis.persistence.db import is_postgres_configured, set_tenant_local


class PostgresLayer2ChallengeRepository:
    """Persist and list durable Layer-2 challenge/finding rows in Postgres."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with tenant-scoped connection."""
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def _require_tenant_match(self, row_tenant_id: str) -> None:
        if row_tenant_id != self._tenant_id:
            raise ValueError("row tenant_id does not match repository tenant scope")

    def upsert_challenge(self, row: Layer2ChallengeRow) -> dict[str, Any]:
        """Insert or update one durable challenge row (idempotent by challenge_id)."""
        self._require_tenant_match(row.tenant_id)
        now = datetime.now(UTC)
        stored = self._conn.execute(
            text(
                """
                INSERT INTO layer2_ic_challenges (
                    challenge_id, tenant_id, deal_id, run_id, source_debate_id,
                    status, safe_summary, created_at, updated_at
                )
                VALUES (
                    :challenge_id, :tenant_id, :deal_id, :run_id, :source_debate_id,
                    :status, CAST(:safe_summary AS jsonb), :now, :now
                )
                ON CONFLICT (challenge_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    safe_summary = EXCLUDED.safe_summary,
                    updated_at = EXCLUDED.updated_at
                RETURNING challenge_id
                """
            ),
            {
                "challenge_id": row.challenge_id,
                "tenant_id": row.tenant_id,
                "deal_id": row.deal_id,
                "run_id": row.run_id,
                "source_debate_id": row.source_debate_id,
                "status": row.status,
                "safe_summary": json.dumps(row.safe_summary, sort_keys=True),
                "now": now,
            },
        ).one()
        return {"challenge_id": str(stored.challenge_id), "tenant_id": row.tenant_id}

    def upsert_finding(self, row: Layer2FindingRow) -> dict[str, Any]:
        """Insert or update one durable finding row (idempotent by tenant/run/finding)."""
        self._require_tenant_match(row.tenant_id)
        now = datetime.now(UTC)
        stored = self._conn.execute(
            text(
                """
                INSERT INTO layer2_ic_findings (
                    finding_id, tenant_id, deal_id, run_id, challenge_id, finding_type,
                    severity, category, supported_claim_ids, supported_calc_ids, graph_ref_ids,
                    rag_ref_ids, enrichment_ref_ids, created_at, updated_at
                )
                VALUES (
                    :finding_id, :tenant_id, :deal_id, :run_id, :challenge_id, :finding_type,
                    :severity, :category, CAST(:supported_claim_ids AS jsonb),
                    CAST(:supported_calc_ids AS jsonb), CAST(:graph_ref_ids AS jsonb),
                    CAST(:rag_ref_ids AS jsonb), CAST(:enrichment_ref_ids AS jsonb), :now, :now
                )
                ON CONFLICT (tenant_id, run_id, finding_id)
                DO UPDATE SET
                    finding_type = EXCLUDED.finding_type,
                    severity = EXCLUDED.severity,
                    category = EXCLUDED.category,
                    supported_claim_ids = EXCLUDED.supported_claim_ids,
                    supported_calc_ids = EXCLUDED.supported_calc_ids,
                    graph_ref_ids = EXCLUDED.graph_ref_ids,
                    rag_ref_ids = EXCLUDED.rag_ref_ids,
                    enrichment_ref_ids = EXCLUDED.enrichment_ref_ids,
                    updated_at = EXCLUDED.updated_at
                RETURNING finding_id
                """
            ),
            {
                "finding_id": row.finding_id,
                "tenant_id": row.tenant_id,
                "deal_id": row.deal_id,
                "run_id": row.run_id,
                "challenge_id": row.challenge_id,
                "finding_type": row.finding_type,
                "severity": row.severity,
                "category": row.category,
                "supported_claim_ids": json.dumps(row.supported_claim_ids),
                "supported_calc_ids": json.dumps(row.supported_calc_ids),
                "graph_ref_ids": json.dumps(row.graph_ref_ids),
                "rag_ref_ids": json.dumps(row.rag_ref_ids),
                "enrichment_ref_ids": json.dumps(row.enrichment_ref_ids),
                "now": now,
            },
        ).one()
        return {"finding_id": str(stored.finding_id), "tenant_id": row.tenant_id}

    def list_challenges(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped durable challenge rows for a run, ordered by challenge_id."""
        rows = self._conn.execute(
            text(
                """
                SELECT challenge_id, tenant_id, deal_id, run_id, source_debate_id,
                       status, safe_summary
                FROM layer2_ic_challenges
                WHERE run_id = :run_id
                ORDER BY challenge_id
                """
            ),
            {"run_id": run_id},
        ).fetchall()
        return [
            {
                "challenge_id": str(item.challenge_id),
                "tenant_id": str(item.tenant_id),
                "deal_id": str(item.deal_id),
                "run_id": str(item.run_id),
                "source_debate_id": item.source_debate_id,
                "status": item.status,
                "safe_summary": item.safe_summary,
            }
            for item in rows
        ]

    def list_findings(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped durable finding rows for a run, ordered by finding_id."""
        rows = self._conn.execute(
            text(
                """
                SELECT finding_id, tenant_id, deal_id, run_id, challenge_id, finding_type,
                       severity, category, supported_claim_ids, supported_calc_ids,
                       graph_ref_ids, rag_ref_ids, enrichment_ref_ids
                FROM layer2_ic_findings
                WHERE run_id = :run_id
                ORDER BY finding_id
                """
            ),
            {"run_id": run_id},
        ).fetchall()
        return [
            {
                "finding_id": item.finding_id,
                "tenant_id": str(item.tenant_id),
                "deal_id": str(item.deal_id),
                "run_id": str(item.run_id),
                "challenge_id": str(item.challenge_id),
                "finding_type": item.finding_type,
                "severity": item.severity,
                "category": item.category,
                "supported_claim_ids": item.supported_claim_ids,
                "supported_calc_ids": item.supported_calc_ids,
                "graph_ref_ids": item.graph_ref_ids,
                "rag_ref_ids": item.rag_ref_ids,
                "enrichment_ref_ids": item.enrichment_ref_ids,
            }
            for item in rows
        ]


_in_memory_challenge_store: dict[str, dict[str, Any]] = {}
_in_memory_finding_store: dict[str, dict[str, Any]] = {}


class InMemoryLayer2ChallengeRepository:
    """In-memory Layer-2 challenge repository twin for tests/local fallback."""

    def __init__(self, tenant_id: str) -> None:
        """Initialize tenant-scoped in-memory repository."""
        self._tenant_id = tenant_id

    def _require_tenant_match(self, row_tenant_id: str) -> None:
        if row_tenant_id != self._tenant_id:
            raise ValueError("row tenant_id does not match repository tenant scope")

    def upsert_challenge(self, row: Layer2ChallengeRow) -> dict[str, Any]:
        """Insert or update one challenge row in memory (idempotent by challenge_id)."""
        self._require_tenant_match(row.tenant_id)
        _in_memory_challenge_store[row.challenge_id] = row.model_dump(mode="json")
        return {"challenge_id": row.challenge_id, "tenant_id": row.tenant_id}

    def upsert_finding(self, row: Layer2FindingRow) -> dict[str, Any]:
        """Insert or update one finding row in memory (composite tenant/run/finding key)."""
        self._require_tenant_match(row.tenant_id)
        key = f"{row.tenant_id}|{row.run_id}|{row.finding_id}"
        _in_memory_finding_store[key] = row.model_dump(mode="json")
        return {"finding_id": row.finding_id, "tenant_id": row.tenant_id}

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

    def list_challenges(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped in-memory challenge rows for a run, ordered by challenge_id."""
        return self._list(_in_memory_challenge_store, run_id=run_id, sort_key="challenge_id")

    def list_findings(self, *, run_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped in-memory finding rows for a run, ordered by finding_id."""
        return self._list(_in_memory_finding_store, run_id=run_id, sort_key="finding_id")


def clear_in_memory_layer2_challenge_store() -> None:
    """Clear the in-memory Layer-2 challenge stores. For tests only."""
    _in_memory_challenge_store.clear()
    _in_memory_finding_store.clear()


def get_layer2_challenge_repository(
    conn: Connection | None,
    tenant_id: str,
) -> PostgresLayer2ChallengeRepository | InMemoryLayer2ChallengeRepository:
    """Return the appropriate Layer-2 challenge repository."""
    if conn is not None and is_postgres_configured():
        return PostgresLayer2ChallengeRepository(conn, tenant_id)
    return InMemoryLayer2ChallengeRepository(tenant_id)
