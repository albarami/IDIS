from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ._postgres_rls_fixtures import TENANT_A_ID, TENANT_B_ID

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine


class TestDeterministicCalculationsRLS:
    """Tests for deterministic_calculations table RLS tenant isolation (Phase 4.1)."""

    def _create_tenant(self, conn: Connection, tenant_id: str) -> None:
        """Helper to create a tenant for FK constraint."""
        now = datetime.now(UTC)
        conn.execute(
            text(
                """
                INSERT INTO tenants (tenant_id, name, created_at)
                VALUES (:tenant_id, :name, :created_at)
                ON CONFLICT (tenant_id) DO NOTHING
                """
            ),
            {
                "tenant_id": tenant_id,
                "name": "Test Tenant",
                "created_at": now,
            },
        )

    def _create_deal(self, conn: Connection, tenant_id: str, deal_id: str) -> None:
        """Helper to create a tenant and deal for FK constraints."""
        now = datetime.now(UTC)
        self._create_tenant(conn, tenant_id)
        conn.execute(
            text(
                """
                INSERT INTO deals (deal_id, tenant_id, name, created_at)
                VALUES (:deal_id, :tenant_id, :name, :created_at)
                """
            ),
            {
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "name": "Test Deal",
                "created_at": now,
            },
        )

    def test_deterministic_calculations_same_tenant_read_allowed(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert calc under tenant A, read under tenant A => 1 row."""
        deal_id = str(uuid.uuid4())
        calc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal(conn, TENANT_A_ID, deal_id)
            conn.execute(
                text(
                    """
                    INSERT INTO deterministic_calculations
                    (calc_id, tenant_id, deal_id, calc_type, inputs, formula_hash,
                     code_version, output, reproducibility_hash, created_at, updated_at)
                    VALUES (:calc_id, :tenant_id, :deal_id, :calc_type, :inputs, :formula_hash,
                            :code_version, :output, :reproducibility_hash, :created_at, :updated_at)
                    """
                ),
                {
                    "calc_id": calc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "calc_type": "RUNWAY",
                    "inputs": json.dumps({"values": {"cash": "1000000"}}),
                    "formula_hash": "abc123hash",
                    "code_version": "1.0.0",
                    "output": json.dumps({"primary_value": "20.0000"}),
                    "reproducibility_hash": "def456hash",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            result = conn.execute(
                text(
                    "SELECT calc_id, calc_type FROM deterministic_calculations "
                    "WHERE calc_id = :calc_id"
                ),
                {"calc_id": calc_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1, "RLS should allow same-tenant calc reads"
        assert rows[0].calc_type == "RUNWAY"

    def test_deterministic_calculations_cross_tenant_read_blocked(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert calc under tenant A, read under tenant B => 0 rows."""
        deal_id = str(uuid.uuid4())
        calc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal(conn, TENANT_A_ID, deal_id)
            conn.execute(
                text(
                    """
                    INSERT INTO deterministic_calculations
                    (calc_id, tenant_id, deal_id, calc_type, inputs, formula_hash,
                     code_version, output, reproducibility_hash, created_at, updated_at)
                    VALUES (:calc_id, :tenant_id, :deal_id, :calc_type, :inputs, :formula_hash,
                            :code_version, :output, :reproducibility_hash, :created_at, :updated_at)
                    """
                ),
                {
                    "calc_id": calc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "calc_type": "RUNWAY",
                    "inputs": json.dumps({"values": {"cash": "1000000"}}),
                    "formula_hash": "abc123hash",
                    "code_version": "1.0.0",
                    "output": json.dumps({"primary_value": "20.0000"}),
                    "reproducibility_hash": "def456hash",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_B_ID}'"))
            result = conn.execute(
                text("SELECT calc_id FROM deterministic_calculations WHERE calc_id = :calc_id"),
                {"calc_id": calc_id},
            )
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should block cross-tenant calc reads"

    def test_deterministic_calculations_fail_closed_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """No SET LOCAL => SELECT returns 0 rows (fail closed)."""
        deal_id = str(uuid.uuid4())
        calc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal(conn, TENANT_A_ID, deal_id)
            conn.execute(
                text(
                    """
                    INSERT INTO deterministic_calculations
                    (calc_id, tenant_id, deal_id, calc_type, inputs, formula_hash,
                     code_version, output, reproducibility_hash, created_at, updated_at)
                    VALUES (:calc_id, :tenant_id, :deal_id, :calc_type, :inputs, :formula_hash,
                            :code_version, :output, :reproducibility_hash, :created_at, :updated_at)
                    """
                ),
                {
                    "calc_id": calc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "calc_type": "RUNWAY",
                    "inputs": json.dumps({"values": {"cash": "1000000"}}),
                    "formula_hash": "abc123hash",
                    "code_version": "1.0.0",
                    "output": json.dumps({"primary_value": "20.0000"}),
                    "reproducibility_hash": "def456hash",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            result = conn.execute(text("SELECT calc_id FROM deterministic_calculations"))
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should return 0 rows without tenant context"

    def test_deterministic_calculations_insert_blocked_without_tenant_context(
        self, app_engine: Engine, admin_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT without SET LOCAL should be blocked by RLS WITH CHECK."""
        deal_id = str(uuid.uuid4())
        calc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO tenants (tenant_id, name, created_at)
                    VALUES (:tenant_id, :name, :created_at)
                    ON CONFLICT (tenant_id) DO NOTHING
                    """
                ),
                {
                    "tenant_id": TENANT_A_ID,
                    "name": "Test Tenant",
                    "created_at": now,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Test Deal",
                    "created_at": now,
                },
            )

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO deterministic_calculations
                    (calc_id, tenant_id, deal_id, calc_type, inputs, formula_hash,
                     code_version, output, reproducibility_hash, created_at, updated_at)
                    VALUES (:calc_id, :tenant_id, :deal_id, :calc_type, :inputs, :formula_hash,
                            :code_version, :output, :reproducibility_hash, :created_at, :updated_at)
                    """
                ),
                {
                    "calc_id": calc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "calc_type": "RUNWAY",
                    "inputs": json.dumps({"values": {"cash": "1000000"}}),
                    "formula_hash": "abc123hash",
                    "code_version": "1.0.0",
                    "output": json.dumps({"primary_value": "20.0000"}),
                    "reproducibility_hash": "def456hash",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"INSERT without tenant context should be blocked by RLS, got: {exc_info.value}"

    def test_deterministic_calculations_rls_blocks_mismatched_tenant_insert(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT with tenant context A but tenant_id=B must be blocked by WITH CHECK."""
        deal_id = str(uuid.uuid4())
        calc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal(conn, TENANT_A_ID, deal_id)

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO deterministic_calculations
                    (calc_id, tenant_id, deal_id, calc_type, inputs, formula_hash,
                     code_version, output, reproducibility_hash, created_at, updated_at)
                    VALUES (:calc_id, :tenant_id, :deal_id, :calc_type, :inputs, :formula_hash,
                            :code_version, :output, :reproducibility_hash, :created_at, :updated_at)
                    """
                ),
                {
                    "calc_id": calc_id,
                    "tenant_id": TENANT_B_ID,  # Mismatched: context=A, value=B
                    "deal_id": deal_id,
                    "calc_type": "RUNWAY",
                    "inputs": json.dumps({"values": {"cash": "1000000"}}),
                    "formula_hash": "abc123hash",
                    "code_version": "1.0.0",
                    "output": json.dumps({"primary_value": "20.0000"}),
                    "reproducibility_hash": "def456hash",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"Mismatched tenant INSERT should be blocked by RLS WITH CHECK: {exc_info.value}"
