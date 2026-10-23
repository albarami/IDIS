"""RED real-PostgreSQL proof for Task 5 durable methodology state."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    downgrade,
    grant_application_access,
    migrate,
    set_tenant_context,
    tenant_catalog_snapshot,
    tenant_table_inventory,
)
from tests.slice100_task5.test_methodology_migration_0036_red import (
    EXPECTED_TENANT_TABLES,
    TASK5_TABLES,
)

EXPECTED_COLUMNS = {
    "methodology_versions": {
        "tenant_id",
        "methodology_version_record_id",
        "methodology_version_id",
        "methodology_id",
        "methodology_type",
        "version_label",
        "source_type",
        "source_alias",
        "source_sha256",
        "source_byte_count",
        "source_contract_hash",
        "source_trace_digest",
        "registry_hash",
        "version_hash",
        "question_count",
        "source_section_count",
        "header_row",
        "status",
        "imported_by_actor_id",
        "import_audit_event_id",
        "imported_at",
        "frozen_by_actor_id",
        "freeze_audit_event_id",
        "frozen_at",
    },
    "methodology_questions": {
        "tenant_id",
        "methodology_question_record_id",
        "methodology_version_record_id",
        "methodology_version_id",
        "methodology_question_id",
        "methodology_id",
        "methodology_type",
        "question_ordinal",
        "section",
        "source_sheet_or_section",
        "source_row_number",
        "source_anchor_cell",
        "source_sha256",
        "semantic_row_hash",
        "question_payload_hash",
        "question_payload",
        "created_at",
    },
    "run_methodology_selections": {
        "selection_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "commercial_dd_version_record_id",
        "commercial_methodology_version_id",
        "commercial_version_hash",
        "financial_dd_version_record_id",
        "financial_methodology_version_id",
        "financial_version_hash",
        "selection_hash",
        "selected_question_count",
        "created_by_actor_id",
        "selection_audit_event_id",
        "created_at",
    },
    "methodology_coverage_records": {
        "coverage_record_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "selection_id",
        "methodology_version_record_id",
        "methodology_version_id",
        "methodology_question_record_id",
        "methodology_question_id",
        "methodology_type",
        "status",
        "reason_code",
        "evidence_links",
        "answer",
        "conflict_ids",
        "defect_ids",
        "created_at",
        "updated_at",
    },
}

EXPECTED_FOREIGN_KEYS = {
    "fk_methodology_questions_version",
    "fk_run_methodology_selections_run_scope",
    "fk_run_methodology_selections_cdd_version",
    "fk_run_methodology_selections_fdd_version",
    "fk_methodology_coverage_selection_scope",
    "fk_methodology_coverage_question",
}
EXPECTED_PRIMARY_KEYS = {
    "methodology_versions": ("methodology_version_record_id",),
    "methodology_questions": ("methodology_question_record_id",),
    "run_methodology_selections": ("selection_id",),
    "methodology_coverage_records": ("coverage_record_id",),
}
EXPECTED_INVARIANT_CONSTRAINTS = {
    "ck_methodology_versions_family_contract",
    "ck_methodology_versions_freeze_state",
    "ck_methodology_versions_hashes",
    "uq_methodology_versions_tenant_external_id",
    "uq_methodology_versions_tenant_record_hash",
    "ck_methodology_questions_trace",
    "uq_methodology_questions_semantic_row",
    "uq_methodology_questions_source_trace",
    "ck_run_methodology_selections_exact_pair",
    "uq_run_methodology_selections_tenant_run",
    "ck_methodology_coverage_payloads",
    "uq_methodology_coverage_run_question",
}
EXPECTED_TRIGGERS = {
    "trg_methodology_versions_guard",
    "trg_methodology_questions_immutable",
    "trg_run_methodology_selections_guard",
    "trg_methodology_coverage_scope",
}


def _insert_imported_version(connection: Any, tenant_id: str) -> str:
    record_id = str(uuid.uuid4())
    connection.execute(
        text(
            """
            INSERT INTO methodology_versions (
                tenant_id, methodology_version_record_id, methodology_version_id,
                methodology_id, methodology_type,
                version_label, source_type, source_alias, source_sha256,
                source_byte_count, source_contract_hash, source_trace_digest,
                registry_hash, version_hash, question_count, source_section_count,
                header_row, status, imported_by_actor_id, import_audit_event_id
            ) VALUES (
                CAST(:tenant_id AS uuid), CAST(:record_id AS uuid),
                'commercial_dd:test-v1', 'commercial_dd',
                'commercial_dd', 'test-v1', 'checked_in_template',
                'checked-in-commercial-template', :source_sha256, 1,
                :source_contract_hash, :source_trace_digest, :registry_hash,
                :version_hash, 21, 9, NULL, 'IMPORTED', 'task5-test-actor',
                CAST(:audit_id AS uuid)
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "record_id": record_id,
            "source_sha256": "a" * 64,
            "source_contract_hash": "b" * 64,
            "source_trace_digest": "c" * 64,
            "registry_hash": "d" * 64,
            "version_hash": "e" * 64,
            "audit_id": str(uuid.uuid4()),
        },
    )
    return record_id


def _catalog_policy_rows(database: DisposablePostgres) -> dict[str, tuple[Any, ...]]:
    with database.admin_engine.connect() as connection:
        return {
            str(row[0]): tuple(row)
            for row in tenant_catalog_snapshot(connection)
            if str(row[0]) in TASK5_TABLES
        }


def _assert_task5_policies(database: DisposablePostgres) -> None:
    rows = _catalog_policy_rows(database)
    assert set(rows) == set(TASK5_TABLES)
    for table, row in rows.items():
        _name, enabled, forced, policy, permissive, command, roles, using, check = row
        assert enabled is True, table
        assert forced is True, table
        assert policy == f"{table}_tenant_isolation", table
        assert permissive is True, table
        assert command == "*", table
        assert roles == "{0}", table
        assert using == check, table
        normalized = " ".join(str(using).lower().split())
        assert "nullif(" in normalized, table
        assert "is not null" in normalized, table
        assert "tenant_id =" in normalized, table
        assert "current_setting('idis.tenant_id'::text, true)" in normalized, table


def test_0036_upgrade_downgrade_upgrade_is_exact_on_an_empty_database() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0035")
        with database.admin_engine.connect() as connection:
            before = tenant_catalog_snapshot(connection)

        migrate(database.admin_engine, "0036")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0036"
            assert tenant_table_inventory(connection) == EXPECTED_TENANT_TABLES
        _assert_task5_policies(database)

        downgrade(database.admin_engine, "0035")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0035"
            assert tenant_catalog_snapshot(connection) == before
            remaining = set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public'"
                    )
                ).scalars()
            )
            assert set(TASK5_TABLES).isdisjoint(remaining)

        migrate(database.admin_engine, "0036")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0036"
            assert tenant_table_inventory(connection) == EXPECTED_TENANT_TABLES
        _assert_task5_policies(database)


def test_0036_downgrade_refuses_to_discard_any_task5_row() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0036")
        tenant_id = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO tenants (tenant_id, name) VALUES (CAST(:id AS uuid), 'Task5')"),
                {"id": tenant_id},
            )
            _insert_imported_version(connection, tenant_id)

        with pytest.raises(Exception, match="cannot downgrade Task 5"):
            downgrade(database.admin_engine, "0035")

        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0036"
            assert (
                connection.execute(text("SELECT count(*) FROM methodology_versions")).scalar_one()
                == 1
            )


def test_0036_catalog_has_the_exact_columns_and_composite_tenant_fks() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0036")
        with database.admin_engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = ANY(CAST(:tables AS text[]))
                    ORDER BY table_name, ordinal_position
                    """
                ),
                {"tables": list(TASK5_TABLES)},
            ).fetchall()
            actual: dict[str, set[str]] = {table: set() for table in TASK5_TABLES}
            for table, column in rows:
                actual[str(table)].add(str(column))
            assert actual == EXPECTED_COLUMNS

            foreign_keys = set(
                connection.execute(
                    text(
                        """
                        SELECT conname
                        FROM pg_constraint
                        WHERE contype = 'f'
                          AND conrelid IN (
                              SELECT to_regclass('public.' || table_name)
                              FROM unnest(CAST(:tables AS text[])) AS names(table_name)
                          )
                        """
                    ),
                    {"tables": list(TASK5_TABLES)},
                ).scalars()
            )
            assert foreign_keys >= EXPECTED_FOREIGN_KEYS

            primary_key_rows = connection.execute(
                text(
                    """
                    SELECT tc.table_name, kcu.column_name, kcu.ordinal_position
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON kcu.constraint_schema = tc.constraint_schema
                     AND kcu.constraint_name = tc.constraint_name
                    WHERE tc.constraint_schema = 'public'
                      AND tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_name = ANY(CAST(:tables AS text[]))
                    ORDER BY tc.table_name, kcu.ordinal_position
                    """
                ),
                {"tables": list(TASK5_TABLES)},
            ).fetchall()
            actual_primary_keys: dict[str, list[str]] = {table: [] for table in TASK5_TABLES}
            for table, column, _ordinal in primary_key_rows:
                actual_primary_keys[str(table)].append(str(column))
            assert {table: tuple(columns) for table, columns in actual_primary_keys.items()} == (
                EXPECTED_PRIMARY_KEYS
            )
            invariant_constraints = set(
                connection.execute(
                    text(
                        """
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid IN (
                            SELECT to_regclass('public.' || table_name)
                            FROM unnest(CAST(:tables AS text[])) AS names(table_name)
                        )
                        """
                    ),
                    {"tables": list(TASK5_TABLES)},
                ).scalars()
            )
            assert invariant_constraints >= EXPECTED_INVARIANT_CONSTRAINTS
            triggers = set(
                connection.execute(
                    text(
                        """
                        SELECT tgname
                        FROM pg_trigger
                        WHERE NOT tgisinternal
                          AND tgrelid IN (
                              SELECT to_regclass('public.' || table_name)
                              FROM unnest(CAST(:tables AS text[])) AS names(table_name)
                          )
                        """
                    ),
                    {"tables": list(TASK5_TABLES)},
                ).scalars()
            )
            assert triggers >= EXPECTED_TRIGGERS


def test_version_question_and_run_selection_identity_rows_are_database_immutable() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0036")
        tenant_id = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO tenants (tenant_id, name) VALUES (CAST(:id AS uuid), 'Task5')"),
                {"id": tenant_id},
            )
            _insert_imported_version(connection, tenant_id)

        with database.admin_engine.connect() as connection:
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        "UPDATE methodology_versions SET version_label='tampered' "
                        "WHERE tenant_id=CAST(:tenant AS uuid)"
                    ),
                    {"tenant": tenant_id},
                )
            connection.rollback()
            with pytest.raises(DBAPIError):
                connection.execute(
                    text("DELETE FROM methodology_versions WHERE tenant_id=CAST(:tenant AS uuid)"),
                    {"tenant": tenant_id},
                )
            connection.rollback()


def test_question_source_hash_drift_and_duplicate_semantic_rows_fail_in_postgres() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0036")
        tenant_id = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO tenants (tenant_id, name) VALUES (CAST(:id AS uuid), 'Task5')"),
                {"id": tenant_id},
            )
            version_record_id = _insert_imported_version(connection, tenant_id)

        insert_question = text(
            """
            INSERT INTO methodology_questions (
                tenant_id, methodology_question_record_id,
                methodology_version_record_id, methodology_version_id,
                methodology_question_id, methodology_id, methodology_type,
                question_ordinal, section, source_sheet_or_section,
                source_row_number, source_anchor_cell, source_sha256,
                semantic_row_hash, question_payload_hash, question_payload
            ) VALUES (
                CAST(:tenant AS uuid), CAST(:question_record AS uuid),
                CAST(:version_record AS uuid), 'commercial_dd:test-v1',
                :question_id, 'commercial_dd', 'commercial_dd', :ordinal,
                'Commercial', 'Commercial', NULL, NULL, :source_hash,
                :semantic_hash, :payload_hash,
                '{"schema_version":"methodology-question-v1"}'::jsonb
            )
            """
        )
        base = {
            "tenant": tenant_id,
            "version_record": version_record_id,
            "question_record": str(uuid.uuid4()),
            "question_id": "mq_commercial_dd_test_0001",
            "ordinal": 1,
            "source_hash": "b" * 64,
            "semantic_hash": "1" * 64,
            "payload_hash": "2" * 64,
        }
        with database.admin_engine.connect() as connection:
            with pytest.raises(DBAPIError):
                connection.execute(insert_question, base)
            connection.rollback()

            valid = {**base, "source_hash": "a" * 64}
            connection.execute(insert_question, valid)
            connection.commit()

            duplicate = {
                **valid,
                "question_record": str(uuid.uuid4()),
                "question_id": "mq_commercial_dd_test_0002",
                "ordinal": 2,
                "payload_hash": "3" * 64,
            }
            with pytest.raises(DBAPIError):
                connection.execute(insert_question, duplicate)
            connection.rollback()


@dataclass(frozen=True)
class _RlsDatabase:
    database: DisposablePostgres
    tenant_a: str
    tenant_b: str


@pytest.fixture(scope="module")
def task5_rls_database() -> Iterator[_RlsDatabase]:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0036")
        grant_application_access(database)
        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants (tenant_id, name) VALUES "
                    "(CAST(:a AS uuid), 'Task5 A'), (CAST(:b AS uuid), 'Task5 B')"
                ),
                {"a": tenant_a, "b": tenant_b},
            )
            # The isolated RLS fixture uses trigger-disabled, constraint-valid rows;
            # lifecycle and immutability are proved independently above.
            for table in TASK5_TABLES:
                connection.exec_driver_sql(f'ALTER TABLE "{table}" DISABLE TRIGGER USER')
            _seed_complete_task5_graph(connection, tenant_a)
            for table in reversed(TASK5_TABLES):
                connection.exec_driver_sql(f'ALTER TABLE "{table}" ENABLE TRIGGER USER')
        yield _RlsDatabase(database=database, tenant_a=tenant_a, tenant_b=tenant_b)


def _seed_complete_task5_graph(connection: Any, tenant_id: str) -> None:
    deal_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    selection_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO deals (deal_id,tenant_id,name,created_at) "
            "VALUES (CAST(:deal AS uuid),CAST(:tenant AS uuid),'Task5 deal',now())"
        ),
        {"deal": deal_id, "tenant": tenant_id},
    )
    connection.execute(
        text(
            "INSERT INTO runs (run_id,tenant_id,deal_id,mode,status,started_at,created_at) "
            "VALUES (CAST(:run AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
            "'FULL','QUEUED',now(),now())"
        ),
        {"run": run_id, "tenant": tenant_id, "deal": deal_id},
    )
    first_question_records: dict[str, str] = {}
    versions = (
        (
            str(uuid.uuid4()),
            "commercial_dd:test-v1",
            "commercial_dd",
            "a" * 64,
            "e" * 64,
            21,
            9,
            None,
        ),
        (
            str(uuid.uuid4()),
            "financial_dd:test-v1",
            "financial_dd",
            "f" * 64,
            "9" * 64,
            79,
            4,
            2,
        ),
    )
    for (
        version_record_id,
        version_id,
        methodology_type,
        source_hash,
        version_hash,
        count,
        sections,
        header,
    ) in versions:
        connection.execute(
            text(
                """
                INSERT INTO methodology_versions (
                    tenant_id, methodology_version_record_id, methodology_version_id,
                    methodology_id, methodology_type,
                    version_label, source_type, source_alias, source_sha256,
                    source_byte_count, source_contract_hash, source_trace_digest,
                    registry_hash, version_hash, question_count, source_section_count,
                    header_row, status, imported_by_actor_id, import_audit_event_id,
                    frozen_by_actor_id, freeze_audit_event_id, frozen_at
                ) VALUES (
                    CAST(:tenant AS uuid), CAST(:version_record_id AS uuid),
                    :version_id, :methodology_type,
                    :methodology_type, 'test-v1', :source_type, 'configured-source',
                    :source_hash, 1, :contract_hash, :trace_hash, :registry_hash,
                    :version_hash, :question_count, :section_count, :header_row,
                    'FROZEN', 'task5-test-actor', CAST(:import_audit AS uuid),
                    'task5-test-actor', CAST(:freeze_audit AS uuid), now()
                )
                """
            ),
            {
                "tenant": tenant_id,
                "version_record_id": version_record_id,
                "version_id": version_id,
                "methodology_type": methodology_type,
                "source_type": (
                    "operator_workbook"
                    if methodology_type == "financial_dd"
                    else "checked_in_template"
                ),
                "source_hash": source_hash,
                "contract_hash": "1" * 64,
                "trace_hash": "2" * 64,
                "registry_hash": "3" * 64,
                "version_hash": version_hash,
                "question_count": count,
                "section_count": sections,
                "header_row": header,
                "import_audit": str(uuid.uuid4()),
                "freeze_audit": str(uuid.uuid4()),
            },
        )
        for ordinal in range(1, count + 1):
            question_id = f"mq_{methodology_type}_{ordinal:04d}"
            question_record_id = str(uuid.uuid4())
            if ordinal == 1:
                first_question_records[methodology_type] = question_record_id
            connection.execute(
                text(
                    """
                    INSERT INTO methodology_questions (
                        tenant_id, methodology_question_record_id,
                        methodology_version_record_id, methodology_version_id,
                        methodology_question_id,
                        methodology_id, methodology_type, question_ordinal, section,
                        source_sheet_or_section, source_row_number, source_anchor_cell,
                        source_sha256,
                        semantic_row_hash, question_payload_hash, question_payload
                    ) VALUES (
                        CAST(:tenant AS uuid), CAST(:question_record_id AS uuid),
                        CAST(:version_record_id AS uuid), :version_id, :question_id,
                        :methodology_type, :methodology_type, :ordinal, :section,
                        :section, :row_number, :anchor_cell, :source_hash, :semantic_hash,
                        :payload_hash, CAST(:payload AS jsonb)
                    )
                    """
                ),
                {
                    "tenant": tenant_id,
                    "question_record_id": question_record_id,
                    "version_record_id": version_record_id,
                    "version_id": version_id,
                    "question_id": question_id,
                    "methodology_type": methodology_type,
                    "ordinal": ordinal,
                    "section": "P&L" if methodology_type == "financial_dd" else "Commercial",
                    "row_number": ordinal + 3 if methodology_type == "financial_dd" else None,
                    "anchor_cell": (
                        f"D{ordinal + 3}" if methodology_type == "financial_dd" else None
                    ),
                    "source_hash": source_hash,
                    "semantic_hash": f"{ordinal:064x}",
                    "payload_hash": f"{ordinal + count:064x}",
                    "payload": '{"schema_version":"methodology-question-v1"}',
                },
            )
    connection.execute(
        text(
            """
            INSERT INTO run_methodology_selections (
                selection_id, tenant_id, deal_id, run_id,
                commercial_dd_version_record_id,
                commercial_methodology_version_id, commercial_version_hash,
                financial_dd_version_record_id,
                financial_methodology_version_id, financial_version_hash,
                selection_hash, selected_question_count, created_by_actor_id,
                selection_audit_event_id
            ) VALUES (
                CAST(:selection AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                CAST(:run AS uuid), CAST(:commercial_record_id AS uuid),
                'commercial_dd:test-v1', :commercial_hash,
                CAST(:financial_record_id AS uuid), 'financial_dd:test-v1',
                :financial_hash, :selection_hash, 100,
                'task5-test-actor', CAST(:audit AS uuid)
            )
            """
        ),
        {
            "selection": selection_id,
            "tenant": tenant_id,
            "deal": deal_id,
            "run": run_id,
            "commercial_record_id": versions[0][0],
            "commercial_hash": "e" * 64,
            "financial_record_id": versions[1][0],
            "financial_hash": "9" * 64,
            "selection_hash": "8" * 64,
            "audit": str(uuid.uuid4()),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO methodology_coverage_records (
                coverage_record_id, tenant_id, deal_id, run_id, selection_id,
                methodology_version_record_id, methodology_version_id,
                methodology_question_record_id, methodology_question_id,
                methodology_type, status, evidence_links, answer,
                conflict_ids, defect_ids
            ) VALUES (
                'mc_000000000000000000000001', CAST(:tenant AS uuid),
                CAST(:deal AS uuid), CAST(:run AS uuid), CAST(:selection AS uuid),
                CAST(:version_record_id AS uuid), 'commercial_dd:test-v1',
                CAST(:question_record_id AS uuid), 'mq_commercial_dd_0001',
                'commercial_dd', 'not_started', '[]'::jsonb, NULL,
                ARRAY[]::text[], ARRAY[]::text[]
            )
            """
        ),
        {
            "tenant": tenant_id,
            "deal": deal_id,
            "run": run_id,
            "selection": selection_id,
            "version_record_id": versions[0][0],
            "question_record_id": first_question_records["commercial_dd"],
        },
    )


@pytest.mark.parametrize(
    ("table", "assignment"),
    (
        ("methodology_versions", "version_label='tampered'"),
        ("methodology_questions", "section='tampered'"),
        ("run_methodology_selections", f"selection_hash='{'7' * 64}'"),
    ),
)
def test_frozen_version_question_and_selection_rows_reject_update_and_delete(
    task5_rls_database: _RlsDatabase,
    table: str,
    assignment: str,
) -> None:
    with task5_rls_database.database.admin_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(text(f'UPDATE "{table}" SET {assignment}'))
        connection.rollback()
        with pytest.raises(DBAPIError):
            connection.execute(text(f'DELETE FROM "{table}"'))
        connection.rollback()


@pytest.mark.parametrize("table", TASK5_TABLES)
def test_all_four_tables_deny_cross_tenant_and_unset_reads(
    task5_rls_database: _RlsDatabase,
    table: str,
) -> None:
    with task5_rls_database.database.app_engine.connect() as connection:
        set_tenant_context(connection, task5_rls_database.tenant_a)
        assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() > 0
        connection.rollback()
    with task5_rls_database.database.app_engine.connect() as connection:
        set_tenant_context(connection, task5_rls_database.tenant_b)
        assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() == 0
        connection.rollback()
    with task5_rls_database.database.app_engine.connect() as connection:
        assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() == 0
        connection.rollback()


@pytest.mark.parametrize("table", TASK5_TABLES)
def test_all_four_tables_deny_cross_tenant_and_unset_writes(
    task5_rls_database: _RlsDatabase,
    table: str,
) -> None:
    database = task5_rls_database.database
    before: int
    with database.admin_engine.connect() as connection:
        before = int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())

    with database.app_engine.connect() as connection:
        set_tenant_context(connection, task5_rls_database.tenant_a)
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    f'UPDATE "{table}" SET tenant_id=CAST(:tenant_b AS uuid) '
                    "WHERE tenant_id=CAST(:tenant_a AS uuid)"
                ),
                {
                    "tenant_a": task5_rls_database.tenant_a,
                    "tenant_b": task5_rls_database.tenant_b,
                },
            )
        connection.rollback()

    with database.app_engine.connect() as connection:
        result = connection.execute(
            text(
                f'UPDATE "{table}" SET tenant_id=CAST(:tenant_b AS uuid) '
                "WHERE tenant_id=CAST(:tenant_a AS uuid)"
            ),
            {
                "tenant_a": task5_rls_database.tenant_a,
                "tenant_b": task5_rls_database.tenant_b,
            },
        )
        assert result.rowcount == 0
        connection.rollback()

    with database.admin_engine.connect() as connection:
        assert (
            int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()) == before
        )
