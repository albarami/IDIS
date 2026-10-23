"""Characterize lossless-only downgrade refusal for populated Task-6 state."""

from __future__ import annotations

import json
from typing import Any

import pytest
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.slice100_task2b._support import (
    disposable_postgres,
    downgrade,
    migrate,
    tenant_catalog_snapshot,
    tenant_table_inventory,
)
from tests.slice100_task6._legacy_0037_planning import seed_legacy_0037_planned_graph
from tests.slice100_task6.test_migration_0037_red import TASK6_TABLES
from tests.slice100_task6.test_postgres_0037_red import (
    CANONICAL_CLAIM_IDENTITY,
    CANONICAL_FACTUAL_OUTPUT,
    CLAIM_ID,
)
from tests.slice100_task6.test_postgres_0037_red import (
    DEAL_ID as CANONICAL_DEAL_ID,
)
from tests.slice100_task6.test_postgres_0037_red import (
    TENANT_A as CANONICAL_TENANT_ID,
)
from tests.slice100_task6.test_postgres_task6_write_fencing_red import (
    _planned_graph_snapshot,
    _task6_write_counts,
)

_CATALOG_TABLES = (*TASK6_TABLES, "claims", "evidence_items", "sanads")


def _current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def _task6_catalog_snapshot(connection: Connection) -> tuple[Any, ...]:
    """Capture every Task-6-owned relation, repair, policy, trigger, and function."""
    params = {"table_names": list(_CATALOG_TABLES)}
    columns = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT table_name, column_name, ordinal_position, data_type,
                       udt_name, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY(CAST(:table_names AS text[]))
                ORDER BY table_name, ordinal_position
                """
            ),
            params,
        ).fetchall()
    )
    constraints = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT relation.relname, constraint_row.conname,
                       constraint_row.contype,
                       pg_get_constraintdef(constraint_row.oid, true)
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS relation
                  ON relation.oid = constraint_row.conrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relname = ANY(CAST(:table_names AS text[]))
                ORDER BY relation.relname, constraint_row.conname
                """
            ),
            params,
        ).fetchall()
    )
    indexes = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT tablename, indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = ANY(CAST(:table_names AS text[]))
                ORDER BY tablename, indexname
                """
            ),
            params,
        ).fetchall()
    )
    triggers = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT event_object_table, trigger_name, event_manipulation,
                       action_timing, action_orientation, action_statement
                FROM information_schema.triggers
                WHERE trigger_schema = 'public'
                  AND event_object_table = ANY(CAST(:table_names AS text[]))
                ORDER BY event_object_table, trigger_name, event_manipulation
                """
            ),
            params,
        ).fetchall()
    )
    functions = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT procedure_row.proname,
                       pg_get_function_identity_arguments(procedure_row.oid),
                       pg_get_functiondef(procedure_row.oid)
                FROM pg_proc AS procedure_row
                JOIN pg_namespace AS namespace
                  ON namespace.oid = procedure_row.pronamespace
                WHERE namespace.nspname = 'public'
                  AND procedure_row.proname LIKE 'idis_task6_%'
                ORDER BY procedure_row.proname,
                         pg_get_function_identity_arguments(procedure_row.oid)
                """
            )
        ).fetchall()
    )
    policies = tuple(
        row for row in tenant_catalog_snapshot(connection) if str(row[0]) in _CATALOG_TABLES
    )
    return columns, constraints, indexes, triggers, functions, policies


def _canonical_claim_snapshot(connection: Connection) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT claim_id, tenant_id, deal_id, claim_class, claim_text,
                       predicate, value::text, normalization_rule_version,
                       normalized_predicate, normalized_subject,
                       normalized_period, normalized_unit, value_sha256,
                       created_at
                FROM claims
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND claim_id = CAST(:claim_id AS uuid)
                ORDER BY claim_id
                """
            ),
            {
                "tenant_id": CANONICAL_TENANT_ID,
                "deal_id": CANONICAL_DEAL_ID,
                "claim_id": CLAIM_ID,
            },
        ).fetchall()
    )


def _assert_0037_and_all_task6_tables(connection: Connection) -> None:
    assert _current_revision(connection) == "0037"
    assert set(TASK6_TABLES) <= set(tenant_table_inventory(connection))


def test_populated_planning_graph_refuses_downgrade_without_any_mutation() -> None:
    with disposable_postgres() as database:
        seed_legacy_0037_planned_graph(database)
        assert _task6_write_counts(database) == (100, 100, 0, 0, 0, 0, 0, 0, 0)

        with database.admin_engine.connect() as connection:
            _assert_0037_and_all_task6_tables(connection)
            before_catalog = _task6_catalog_snapshot(connection)
        before_rows = _planned_graph_snapshot(database)
        assert len(before_rows) == 200

        with pytest.raises(RuntimeError, match="durable extraction rows exist"):
            downgrade(database.admin_engine, "0036")

        # A new connection proves the failed Alembic transaction left no partial DDL.
        with database.admin_engine.connect() as connection:
            _assert_0037_and_all_task6_tables(connection)
            assert _task6_catalog_snapshot(connection) == before_catalog
        assert _planned_graph_snapshot(database) == before_rows
        assert _task6_write_counts(database) == (100, 100, 0, 0, 0, 0, 0, 0, 0)


def test_canonical_extension_refuses_downgrade_with_task6_tables_empty() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        identity = CANONICAL_CLAIM_IDENTITY
        output = CANONICAL_FACTUAL_OUTPUT
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO tenants (tenant_id, name)
                    VALUES (CAST(:tenant_id AS uuid), 'Canonical extension tenant');
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (
                        CAST(:deal_id AS uuid), CAST(:tenant_id AS uuid),
                        'Fictional canonical extension deal', now()
                    );
                    INSERT INTO claims (
                        claim_id, tenant_id, deal_id, claim_class, claim_text,
                        predicate, value, created_at,
                        normalization_rule_version, normalized_predicate,
                        normalized_subject, normalized_period, normalized_unit,
                        value_sha256
                    ) VALUES (
                        CAST(:claim_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid), :claim_class, :claim_text,
                        :predicate, CAST(:claim_value AS jsonb), now(),
                        :normalization_rule_version, :normalized_predicate,
                        :normalized_subject, :normalized_period, :normalized_unit,
                        :value_sha256
                    )
                    """
                ),
                {
                    "tenant_id": CANONICAL_TENANT_ID,
                    "deal_id": CANONICAL_DEAL_ID,
                    "claim_id": CLAIM_ID,
                    "claim_class": str(output.claim_class),
                    "claim_text": output.claim_text,
                    "predicate": output.predicate,
                    "claim_value": json.dumps(output.value.model_dump(mode="json")),
                    "normalization_rule_version": identity.normalization_rule_version,
                    "normalized_predicate": identity.normalized_predicate,
                    "normalized_subject": identity.normalized_subject,
                    "normalized_period": identity.normalized_period,
                    "normalized_unit": identity.normalized_unit,
                    "value_sha256": identity.value_sha256,
                },
            )

        assert _task6_write_counts(database) == (0,) * 9
        with database.admin_engine.connect() as connection:
            _assert_0037_and_all_task6_tables(connection)
            before_catalog = _task6_catalog_snapshot(connection)
            before_claim = _canonical_claim_snapshot(connection)
        assert len(before_claim) == 1

        with pytest.raises(RuntimeError, match="canonical extensions are populated"):
            downgrade(database.admin_engine, "0036")

        # Reopen after the failed downgrade to prove revision, DDL, and data survived.
        with database.admin_engine.connect() as connection:
            _assert_0037_and_all_task6_tables(connection)
            assert _task6_catalog_snapshot(connection) == before_catalog
            assert _canonical_claim_snapshot(connection) == before_claim
        assert _task6_write_counts(database) == (0,) * 9
