"""Real-PostgreSQL catalog and per-table isolation proof for migration 0034."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from idis.runtime.dependency_probes import collect_postgres_facts
from tests.slice100_task2b._support import (
    TENANT_TABLES,
    DisposablePostgres,
    disposable_postgres,
    downgrade,
    grant_application_access,
    migrate,
    seed_all_tenant_tables,
    set_strict_proof_insert_trigger,
    set_tenant_context,
    tenant_catalog_snapshot,
    tenant_table_inventory,
)


def _policy_by_table(snapshot: tuple[tuple[Any, ...], ...]) -> dict[str, tuple[Any, ...]]:
    return {str(row[0]): row for row in snapshot}


def _normalized_expression(expression: str) -> str:
    return re.sub(r"\s+", " ", expression).strip().lower()


def _assert_normalized_catalog(database: DisposablePostgres) -> None:
    with database.admin_engine.connect() as connection:
        assert tenant_table_inventory(connection) == TENANT_TABLES
        snapshot = tenant_catalog_snapshot(connection)
    assert len(snapshot) == len(TENANT_TABLES)

    for row in snapshot:
        table, enabled, forced, policy, permissive, command, roles, using, check = row
        assert enabled is True, table
        assert forced is True, table
        assert policy is not None, table
        assert permissive is True, table
        assert command == "*", table
        assert roles == "{0}", table
        assert using is not None, table
        assert check is not None, table
        using_normalized = _normalized_expression(str(using))
        check_normalized = _normalized_expression(str(check))
        assert using_normalized == check_normalized, table
        assert "current_setting('idis.tenant_id'::text, true)" in using_normalized, table
        assert "nullif(" in using_normalized, table
        assert "is not null" in using_normalized, table
        assert "tenant_id =" in using_normalized, table
        assert "::uuid" in using_normalized, table


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "pgcode", None) or getattr(error.orig, "sqlstate", None)


def _assert_rls_denial(error: DBAPIError, table: str) -> None:
    assert _sqlstate(error) == "42501", table
    assert "row-level security" in str(error.orig).lower(), table


def test_revision_0033_reproduces_the_approved_reference_defects() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0033")
        with database.admin_engine.connect() as connection:
            policies = _policy_by_table(tenant_catalog_snapshot(connection))
            assert tenant_table_inventory(connection) == TENANT_TABLES

        tenants = policies["tenants"]
        assert tenants[1] is False
        assert tenants[2] is False
        assert tenants[3] is None

        runs = policies["runs"]
        assert runs[2] is False
        assert runs[3] == "tenant_isolation_runs"
        assert runs[7] is not None
        assert runs[8] is None

        claims = policies["claims"]
        assert claims[3] == "claims_tenant_isolation"
        assert "nullif" not in _normalized_expression(str(claims[7]))
        assert "is not null" not in _normalized_expression(str(claims[7]))


def test_0034_upgrade_downgrade_upgrade_restores_0033_exactly() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0033")
        with database.admin_engine.connect() as connection:
            before = tenant_catalog_snapshot(connection)

        migrate(database.admin_engine, "0034")
        _assert_normalized_catalog(database)

        downgrade(database.admin_engine, "0033")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0033"
            assert tenant_catalog_snapshot(connection) == before

        migrate(database.admin_engine, "0034")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0034"
        _assert_normalized_catalog(database)


@dataclass(frozen=True)
class _NormalizedDatabase:
    database: DisposablePostgres
    tenant_a: str
    tenant_b: str
    insertable_columns: dict[str, tuple[str, ...]]


@pytest.fixture(scope="module")
def normalized_database() -> Iterator[_NormalizedDatabase]:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0034")
        grant_application_access(database)
        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            seed_all_tenant_tables(connection, tenant_a)
            rows = connection.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name IN (
                          SELECT table_name
                          FROM information_schema.columns
                          WHERE table_schema = 'public' AND column_name = 'tenant_id'
                      )
                      AND is_generated = 'NEVER'
                    ORDER BY table_name, ordinal_position
                    """
                )
            ).fetchall()
        insertable_columns: dict[str, list[str]] = {table: [] for table in TENANT_TABLES}
        for table, column in rows:
            insertable_columns[str(table)].append(str(column))
        yield _NormalizedDatabase(
            database=database,
            tenant_a=tenant_a,
            tenant_b=tenant_b,
            insertable_columns={
                table: tuple(columns) for table, columns in insertable_columns.items()
            },
        )


def test_application_role_is_neither_superuser_nor_rls_bypass(
    normalized_database: _NormalizedDatabase,
) -> None:
    with normalized_database.database.app_engine.connect() as connection:
        row = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert tuple(row) == (False, False)


def test_runtime_readiness_probe_accepts_the_real_0034_catalog_and_app_role() -> None:
    """Exercise Task 2's production probe against pg_catalog, not a catalog double."""
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0034")
        grant_application_access(database)
        tenant_id = str(uuid.uuid4())
        isolation_tenant_id = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants (tenant_id, name) VALUES "
                    "(CAST(:tenant_id AS uuid), 'probe tenant'), "
                    "(CAST(:isolation_tenant_id AS uuid), 'probe isolation tenant')"
                ),
                {
                    "tenant_id": tenant_id,
                    "isolation_tenant_id": isolation_tenant_id,
                },
            )

        facts = collect_postgres_facts(
            connection_factory=database.app_engine.begin,
            tenant_id=tenant_id,
            isolation_tenant_id=isolation_tenant_id,
            expected_schema_revision="0034",
            required_rls_tables=frozenset(TENANT_TABLES),
        )

    assert facts.connected is True
    assert facts.schema_at_head is True
    assert facts.app_role_superuser is False
    assert facts.app_role_bypass_rls is False
    assert facts.forced_rls is True
    assert facts.tenant_context_ok is True
    assert facts.cross_tenant_denied is True


def test_runtime_readiness_rejects_policy_on_distinct_quoted_identifier() -> None:
    """A quoted uppercase identifier must not alias the real tenant_id in admission."""
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0034")
        grant_application_access(database)
        tenant_id = str(uuid.uuid4())
        isolation_tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants (tenant_id, name) VALUES "
                    "(CAST(:tenant_id AS uuid), 'quoted probe tenant'), "
                    "(CAST(:isolation_tenant_id AS uuid), 'quoted probe isolation tenant')"
                ),
                {
                    "tenant_id": tenant_id,
                    "isolation_tenant_id": isolation_tenant_id,
                },
            )
            connection.exec_driver_sql('ALTER TABLE deals ADD COLUMN "TENANT_ID" uuid')
            connection.exec_driver_sql('DROP POLICY "deals_tenant_isolation" ON deals')
            connection.exec_driver_sql(
                """
                CREATE POLICY "deals_tenant_isolation" ON deals
                AS PERMISSIVE FOR ALL TO PUBLIC
                USING (
                    NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                    AND "TENANT_ID" =
                        NULLIF(current_setting('idis.tenant_id', true), '')::uuid
                )
                WITH CHECK (
                    NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                    AND "TENANT_ID" =
                        NULLIF(current_setting('idis.tenant_id', true), '')::uuid
                )
                """
            )
            connection.execute(
                text(
                    "INSERT INTO deals "
                    '(deal_id, tenant_id, "TENANT_ID", name, created_at) '
                    "VALUES (CAST(:deal_id AS uuid), CAST(:isolation_tenant_id AS uuid), "
                    "CAST(:tenant_id AS uuid), 'quoted policy leak canary', now())"
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": tenant_id,
                    "isolation_tenant_id": isolation_tenant_id,
                },
            )

        with database.app_engine.begin() as connection:
            set_tenant_context(connection, tenant_id)
            leaked = connection.execute(
                text(
                    "SELECT count(*) FROM deals "
                    "WHERE deal_id = CAST(:deal_id AS uuid) "
                    "AND tenant_id = CAST(:isolation_tenant_id AS uuid)"
                ),
                {"deal_id": deal_id, "isolation_tenant_id": isolation_tenant_id},
            ).scalar_one()
        assert leaked == 1, "the mutation must demonstrate a real cross-tenant leak"

        facts = collect_postgres_facts(
            connection_factory=database.app_engine.begin,
            tenant_id=tenant_id,
            isolation_tenant_id=isolation_tenant_id,
            expected_schema_revision="0034",
            required_rls_tables=frozenset(TENANT_TABLES),
        )

    assert facts.connected is True
    assert facts.schema_at_head is True
    assert facts.app_role_superuser is False
    assert facts.app_role_bypass_rls is False
    assert facts.forced_rls is False
    assert facts.tenant_context_ok is True
    assert facts.cross_tenant_denied is True


def test_migrated_catalog_has_exactly_one_canonical_policy_per_table(
    normalized_database: _NormalizedDatabase,
) -> None:
    _assert_normalized_catalog(normalized_database.database)


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_every_table_allows_same_tenant_read_and_denies_other_or_unset_reads(
    normalized_database: _NormalizedDatabase,
    table: str,
) -> None:
    database = normalized_database.database
    with database.app_engine.connect() as connection:
        set_tenant_context(connection, normalized_database.tenant_a)
        same_tenant_count = connection.execute(
            text(f'SELECT count(*) FROM "{table}" WHERE tenant_id = CAST(:tenant AS uuid)'),
            {"tenant": normalized_database.tenant_a},
        ).scalar_one()
        connection.rollback()

    with database.app_engine.connect() as connection:
        set_tenant_context(connection, normalized_database.tenant_b)
        cross_tenant_count = connection.execute(
            text(f'SELECT count(*) FROM "{table}" WHERE tenant_id = CAST(:tenant AS uuid)'),
            {"tenant": normalized_database.tenant_a},
        ).scalar_one()
        connection.rollback()

    with database.app_engine.connect() as connection:
        unset_count = connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
        connection.rollback()

    with database.app_engine.connect() as connection:
        set_tenant_context(connection, "")
        empty_count = connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
        connection.rollback()

    assert same_tenant_count == 1, table
    assert cross_tenant_count == 0, table
    assert unset_count == 0, table
    assert empty_count == 0, table


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_every_table_rejects_cross_tenant_and_unset_context_writes(
    normalized_database: _NormalizedDatabase,
    table: str,
) -> None:
    database = normalized_database.database
    columns = normalized_database.insertable_columns[table]
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    selected_columns = ", ".join(
        "CAST(:target_tenant AS uuid)" if column == "tenant_id" else f'"{column}"'
        for column in columns
    )
    clone_insert = text(
        f'INSERT INTO "{table}" ({quoted_columns}) '
        f'SELECT {selected_columns} FROM "{table}" '
        "WHERE tenant_id = CAST(:source_tenant AS uuid) LIMIT 1"
    )
    minimal_insert = text(
        f'INSERT INTO "{table}" (tenant_id) VALUES (CAST(:source_tenant AS uuid))'
    )

    if table == "strict_preflight_proofs":
        with database.admin_engine.begin() as connection:
            set_strict_proof_insert_trigger(connection, enabled=False)
    try:
        with database.app_engine.connect() as connection:
            privileges = connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, :table, 'SELECT'), "
                    "has_table_privilege(current_user, :table, 'INSERT')"
                ),
                {"table": f"public.{table}"},
            ).one()
            assert tuple(privileges) == (True, True), table
            connection.rollback()

        with database.app_engine.connect() as connection:
            transaction = connection.begin()
            set_tenant_context(connection, normalized_database.tenant_a)
            with pytest.raises(DBAPIError) as raised:
                connection.execute(
                    clone_insert,
                    {
                        "source_tenant": normalized_database.tenant_a,
                        "target_tenant": normalized_database.tenant_b,
                    },
                )
            _assert_rls_denial(raised.value, table)
            transaction.rollback()

        with database.app_engine.connect() as connection:
            transaction = connection.begin()
            with pytest.raises(DBAPIError) as raised:
                connection.execute(
                    minimal_insert,
                    {"source_tenant": normalized_database.tenant_a},
                )
            _assert_rls_denial(raised.value, table)
            transaction.rollback()

        with database.app_engine.connect() as connection:
            transaction = connection.begin()
            set_tenant_context(connection, "")
            with pytest.raises(DBAPIError) as raised:
                connection.execute(
                    minimal_insert,
                    {"source_tenant": normalized_database.tenant_a},
                )
            _assert_rls_denial(raised.value, table)
            transaction.rollback()
    finally:
        if table == "strict_preflight_proofs":
            with database.admin_engine.begin() as connection:
                set_strict_proof_insert_trigger(connection, enabled=True)

    with database.admin_engine.connect() as connection:
        assert (
            connection.execute(
                text(f'SELECT count(*) FROM "{table}" WHERE tenant_id = CAST(:tenant_a AS uuid)'),
                {"tenant_a": normalized_database.tenant_a},
            ).scalar_one()
            == 1
        ), table
