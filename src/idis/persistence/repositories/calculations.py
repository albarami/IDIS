"""Repositories for deterministic calculations and CalcSanad records."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from idis.models.calc_sanad import CalcSanad
from idis.models.deterministic_calculation import DeterministicCalculation
from idis.persistence.db import is_postgres_configured, set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection


class PostgresCalculationsRepository:
    """Tenant-scoped Postgres repository for deterministic calculations."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with RLS tenant context."""
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(
        self,
        *,
        calculation: DeterministicCalculation,
        calc_sanad: CalcSanad,
    ) -> None:
        """Persist a deterministic calculation and its CalcSanad."""
        calc_data = calculation.to_db_dict()
        sanad_data = _json_safe(calc_sanad.to_db_dict())

        self._conn.execute(
            text(
                """
                INSERT INTO deterministic_calculations (
                    calc_id, tenant_id, deal_id, calc_type, inputs, formula_hash,
                    code_version, output, reproducibility_hash, created_at, updated_at
                ) VALUES (
                    :calc_id, :tenant_id, :deal_id, :calc_type, CAST(:inputs AS JSONB),
                    :formula_hash, :code_version, CAST(:output AS JSONB),
                    :reproducibility_hash, :created_at, :updated_at
                )
                """
            ),
            {
                **calc_data,
                "inputs": json.dumps(calc_data["inputs"], sort_keys=True),
                "output": json.dumps(calc_data["output"], sort_keys=True),
            },
        )
        self._conn.execute(
            text(
                """
                INSERT INTO calc_sanads (
                    calc_sanad_id, tenant_id, calc_id, input_claim_ids,
                    input_min_sanad_grade, calc_grade, explanation, created_at, updated_at
                ) VALUES (
                    :calc_sanad_id, :tenant_id, :calc_id,
                    CAST(:input_claim_ids AS JSONB), :input_min_sanad_grade,
                    :calc_grade, CAST(:explanation AS JSONB), :created_at, :updated_at
                )
                """
            ),
            {
                **sanad_data,
                "input_claim_ids": json.dumps(sanad_data["input_claim_ids"], sort_keys=True),
                "explanation": json.dumps(sanad_data["explanation"], sort_keys=True),
            },
        )

    def list_by_deal(self, deal_id: str) -> list[dict[str, Any]]:
        """List deterministic calculations for a deal."""
        result = self._conn.execute(
            text(
                """
                SELECT calc_id, tenant_id, deal_id, calc_type, inputs, formula_hash,
                       code_version, output, reproducibility_hash, created_at, updated_at
                FROM deterministic_calculations
                WHERE deal_id = :deal_id
                ORDER BY created_at, calc_id
                """
            ),
            {"deal_id": deal_id},
        )
        return [_row_to_dict(row) for row in result.fetchall()]

    def list_calc_sanads_by_deal(self, deal_id: str) -> list[dict[str, Any]]:
        """List CalcSanads joined through calculations for a deal."""
        result = self._conn.execute(
            text(
                """
                SELECT cs.calc_sanad_id, cs.tenant_id, cs.calc_id, cs.input_claim_ids,
                       cs.input_min_sanad_grade, cs.calc_grade, cs.explanation,
                       cs.created_at, cs.updated_at
                FROM calc_sanads cs
                JOIN deterministic_calculations dc ON dc.calc_id = cs.calc_id
                WHERE dc.deal_id = :deal_id
                ORDER BY cs.created_at, cs.calc_sanad_id
                """
            ),
            {"deal_id": deal_id},
        )
        return [_row_to_dict(row) for row in result.fetchall()]


_in_memory_calculations_store: dict[str, dict[str, Any]] = {}
_in_memory_calc_sanads_store: dict[str, dict[str, Any]] = {}


class InMemoryCalculationsRepository:
    """In-memory deterministic calculations repository for tests/local fallback."""

    def __init__(self, tenant_id: str) -> None:
        """Initialize tenant-scoped in-memory repository."""
        self._tenant_id = tenant_id

    def create(
        self,
        *,
        calculation: DeterministicCalculation,
        calc_sanad: CalcSanad,
    ) -> None:
        """Persist calculation and CalcSanad in memory."""
        calc_data = _json_safe(calculation.to_db_dict())
        sanad_data = _json_safe(calc_sanad.to_db_dict())
        _in_memory_calculations_store[calculation.calc_id] = calc_data
        _in_memory_calc_sanads_store[calc_sanad.calc_sanad_id] = sanad_data

    def list_by_deal(self, deal_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped in-memory calculations for a deal."""
        return sorted(
            [
                item
                for item in _in_memory_calculations_store.values()
                if item["tenant_id"] == self._tenant_id and item["deal_id"] == deal_id
            ],
            key=lambda item: item["calc_id"],
        )

    def list_calc_sanads_by_deal(self, deal_id: str) -> list[dict[str, Any]]:
        """List tenant-scoped in-memory CalcSanads for a deal."""
        calc_ids = {item["calc_id"] for item in self.list_by_deal(deal_id)}
        return sorted(
            [
                item
                for item in _in_memory_calc_sanads_store.values()
                if item["tenant_id"] == self._tenant_id and item["calc_id"] in calc_ids
            ],
            key=lambda item: item["calc_sanad_id"],
        )


def clear_in_memory_calculations_store() -> None:
    """Clear in-memory calculation stores. For tests only."""
    _in_memory_calculations_store.clear()
    _in_memory_calc_sanads_store.clear()


def get_calculations_repository(
    conn: Connection | None,
    tenant_id: str,
) -> PostgresCalculationsRepository | InMemoryCalculationsRepository:
    """Return the appropriate calculations repository."""
    if conn is not None and is_postgres_configured():
        return PostgresCalculationsRepository(conn, tenant_id)
    return InMemoryCalculationsRepository(tenant_id)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)
