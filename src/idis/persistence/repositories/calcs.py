"""Calculations repositories for Postgres persistence.

Tenant-scoped repositories over the tables introduced by migration 0005:

- deterministic_calculations → CalculationsRepository
- calc_sanads              → CalcSanadsRepository

Both use `SET LOCAL idis.tenant_id` via `set_tenant_local()` (same
pattern the deals/claims/documents repos use), so every read and
write is automatically scoped by the RLS policy the migration
installs. Rows are returned as plain dicts with ISO-8601 UTC
timestamps.

Scope (Sprint 2, Task 12):
No API routes, no ingestion/document logic, no FULL-mode work. This
module only provides the persistence surface the SNAPSHOT CALC step
writes to.
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

    from idis.models.calc_sanad import CalcSanad
    from idis.models.deterministic_calculation import DeterministicCalculation

logger = logging.getLogger(__name__)


def _iso(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return value


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def ensure_tenant_row(conn: Connection, tenant_id: str, *, name: str | None = None) -> None:
    """Idempotently ensure a row exists in `tenants` for `tenant_id`.

    Required because `deterministic_calculations.tenant_id` and
    `calc_sanads.tenant_id` are the only tables with a FK into the
    `tenants` registry; any code path that persists into them must
    have a parent row in place. The `tenants` table is not
    RLS-protected, so the same app-role connection that performs the
    calc INSERTs can also do this upsert.

    `name` defaults to the tenant_id itself — a stable, opaque label —
    so the upsert never depends on caller context that might not be
    available inside a worker thread. Real tenant onboarding flows can
    refresh the name later without affecting the FK.
    """
    conn.execute(
        text(
            """
            INSERT INTO tenants (tenant_id, name, created_at)
            VALUES (:t, :n, now())
            ON CONFLICT (tenant_id) DO NOTHING
            """
        ),
        {"t": tenant_id, "n": name or tenant_id},
    )


class CalculationsRepository:
    """Tenant-scoped repository for `deterministic_calculations`."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(self, calc: DeterministicCalculation) -> dict[str, Any]:
        """Persist a DeterministicCalculation row."""
        inputs_json = json.dumps(
            {
                "claim_ids": list(calc.inputs.claim_ids),
                "values": {k: str(v) for k, v in calc.inputs.values.items()},
                "metadata": dict(calc.inputs.metadata or {}),
            }
        )
        output_json = json.dumps(
            {
                "primary_value": str(calc.output.primary_value),
                "secondary_values": {
                    k: str(v) for k, v in (calc.output.secondary_values or {}).items()
                },
                "unit": calc.output.unit,
                "currency": calc.output.currency,
            }
        )
        self._conn.execute(
            text(
                """
                INSERT INTO deterministic_calculations (
                    calc_id, tenant_id, deal_id, calc_type,
                    inputs, formula_hash, code_version,
                    output, reproducibility_hash,
                    created_at, updated_at
                ) VALUES (
                    :calc_id, :tenant_id, :deal_id, :calc_type,
                    CAST(:inputs AS JSONB), :formula_hash, :code_version,
                    CAST(:output AS JSONB), :reproducibility_hash,
                    :created_at, :updated_at
                )
                """
            ),
            {
                "calc_id": str(calc.calc_id),
                "tenant_id": str(calc.tenant_id),
                "deal_id": str(calc.deal_id),
                "calc_type": calc.calc_type.value,
                "inputs": inputs_json,
                "formula_hash": calc.formula_hash,
                "code_version": calc.code_version,
                "output": output_json,
                "reproducibility_hash": calc.reproducibility_hash,
                "created_at": calc.created_at,
                "updated_at": calc.updated_at,
            },
        )
        return {
            "calc_id": str(calc.calc_id),
            "tenant_id": str(calc.tenant_id),
            "deal_id": str(calc.deal_id),
            "calc_type": calc.calc_type.value,
            "reproducibility_hash": calc.reproducibility_hash,
            "output_value": str(calc.output.primary_value),
        }

    def list_by_deal(self, deal_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            text(
                """
                SELECT calc_id, tenant_id, deal_id, calc_type,
                       inputs, formula_hash, code_version, output,
                       reproducibility_hash, created_at, updated_at
                FROM deterministic_calculations
                WHERE deal_id = :d
                ORDER BY calc_id ASC
                """
            ),
            {"d": deal_id},
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "calc_id": str(row.calc_id),
            "tenant_id": str(row.tenant_id),
            "deal_id": str(row.deal_id),
            "calc_type": row.calc_type,
            "inputs": _coerce_json(row.inputs) or {},
            "formula_hash": row.formula_hash,
            "code_version": row.code_version,
            "output": _coerce_json(row.output) or {},
            "reproducibility_hash": row.reproducibility_hash,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }


class CalcSanadsRepository:
    """Tenant-scoped repository for `calc_sanads`."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(self, calc_sanad: CalcSanad) -> dict[str, Any]:
        """Persist a CalcSanad row."""
        explanation_json = json.dumps(
            [
                {
                    "step": entry.step,
                    "input_grade": entry.input_grade.value if entry.input_grade else None,
                    "claim_id": entry.claim_id,
                    "is_material": entry.is_material,
                    "impact": entry.impact,
                }
                for entry in (calc_sanad.explanation or [])
            ]
        )
        now = datetime.now(UTC)
        self._conn.execute(
            text(
                """
                INSERT INTO calc_sanads (
                    calc_sanad_id, tenant_id, calc_id,
                    input_claim_ids, input_min_sanad_grade, calc_grade,
                    explanation, created_at, updated_at
                ) VALUES (
                    :calc_sanad_id, :tenant_id, :calc_id,
                    CAST(:input_claim_ids AS JSONB), :input_min, :calc_grade,
                    CAST(:explanation AS JSONB), :created_at, :updated_at
                )
                """
            ),
            {
                "calc_sanad_id": str(calc_sanad.calc_sanad_id),
                "tenant_id": str(calc_sanad.tenant_id),
                "calc_id": str(calc_sanad.calc_id),
                "input_claim_ids": json.dumps(list(calc_sanad.input_claim_ids)),
                "input_min": calc_sanad.input_min_sanad_grade.value,
                "calc_grade": calc_sanad.calc_grade.value,
                "explanation": explanation_json,
                "created_at": calc_sanad.created_at or now,
                "updated_at": calc_sanad.updated_at or now,
            },
        )
        return {
            "calc_sanad_id": str(calc_sanad.calc_sanad_id),
            "calc_id": str(calc_sanad.calc_id),
            "calc_grade": calc_sanad.calc_grade.value,
        }
