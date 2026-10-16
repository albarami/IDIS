"""Real-PostgreSQL proof for the v11 target-field/schema-qualification class fix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from tests.slice100_task2b._support import disposable_postgres, migrate
from tests.slice100_task7 import _v11_postgres_scaffold as scaffold

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v11_reviewed_preimage_v1.json"
_TARGET_AUTHORITIES = {
    "current-receipt-equality-v1",
    "current-append-only-lease-v1",
    "current-source-bound-v1",
    "current-task6-immutable-replacement-v1",
}


def _artifact() -> dict[str, Any]:
    value = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v11_pinned_target_schema_authority_equals_0037_plus_artifact_catalog() -> None:
    from scripts import generate_task7_v11_contract_fixture as generator

    artifact = _artifact()
    expected = generator.target_schema_authority()
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            scaffold.install_artifact_sql_scaffold(connection, artifact)
            target_tables = [row[0] for row in expected]
            rows = connection.execute(
                text(
                    "SELECT table_name,column_name "
                    "FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=ANY(:tables) "
                    "ORDER BY table_name,ordinal_position"
                ),
                {"tables": target_tables},
            ).fetchall()

    actual_by_table: dict[str, list[str]] = {table: [] for table in target_tables}
    for table_name, column_name in rows:
        actual_by_table[str(table_name)].append(str(column_name))
    actual = tuple(
        (table_name, tuple(actual_by_table[table_name])) for table_name in sorted(actual_by_table)
    )
    assert actual == expected


def test_v11_target_and_event_authority_equal_all_installed_trigger_coordinates() -> None:
    from scripts import generate_task7_v11_contract_fixture as generator

    artifact = _artifact()
    expected_targets = generator.target_trigger_authority(artifact)
    expected_events = generator.target_event_authority(artifact)
    assert len(expected_targets) == len(set(expected_targets)) == 73
    assert len(expected_events) == len(set(expected_events)) == 192

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            scaffold.install_artifact_sql_scaffold(connection, artifact)
            catalog_rows = connection.execute(
                text(
                    "SELECT c.relname,t.tgname,t.tgtype "
                    "FROM pg_catalog.pg_trigger t "
                    "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND NOT t.tgisinternal"
                )
            ).all()

    catalog = {(str(table), str(trigger)): int(tgtype) for table, trigger, tgtype in catalog_rows}
    actual_events: list[tuple[str, str, str, str]] = []
    for authority, table, trigger in expected_targets:
        tgtype = catalog[(table, trigger)]
        for event, bit in (("INSERT", 4), ("UPDATE", 16), ("DELETE", 8)):
            if tgtype & bit:
                actual_events.append((authority, table, trigger, event))
    assert tuple(sorted(actual_events)) == tuple(sorted(expected_events))


def test_v11_task6_lease_replace_then_exact_0037_restore_round_trip() -> None:
    artifact = _artifact()
    signature = "'public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)'::regprocedure"
    query = text(
        "SELECT pg_catalog.pg_get_functiondef(p.oid),p.proconfig,p.proowner "
        "FROM pg_catalog.pg_proc p WHERE p.oid=" + signature
    )
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            before = connection.execute(query).one()
            connection.exec_driver_sql(scaffold._decode_lease_upgrade(artifact))
            replaced = connection.execute(query).one()
            assert replaced != before
            assert replaced[1] == ["search_path=pg_catalog, pg_temp"]
            assert "FROM public.runs r" in replaced[0]
            connection.exec_driver_sql(scaffold._decode_lease_restoration(artifact))
            restored = connection.execute(query).one()

    assert restored == before


def test_v11_corrected_authorities_execute_all_affected_legal_targets() -> None:
    """The old 11 field-pair failures and Task-6 helper failure are absent."""

    from scripts import generate_task7_v11_contract_fixture as generator
    from scripts import run_task7_v10_current_guard_census as runner

    artifact = _artifact()
    engine = runner._ENGINE
    target_rows = [
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] in _TARGET_AUTHORITIES
    ]
    targets = tuple(
        (
            str(row["authority_id"]),
            str(table_name),
            str(trigger_name),
        )
        for row in target_rows
        for table_name, trigger_name in row["target_tuple"]
    )
    assert len(targets) == 27

    generator._validate_current_guard_target_field_closure(artifact)
    generator._validate_locked_search_path_call_closure(artifact)
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            scaffold.install_artifact_sql_scaffold(connection, artifact)
            scaffold.disable_all_current_user_triggers(connection, artifact)
            relation_names = tuple(
                dict.fromkeys(
                    [
                        *(row["table_name"] for row in artifact["current_ddl_registry"]["rows"]),
                        *(
                            table_name
                            for table_name, _columns in (generator.target_schema_authority())
                        ),
                        "run_methodology_selections",
                        "methodology_questions",
                    ]
                )
            )
            engine._drop_non_guard_constraints(
                connection,
                relation_names=relation_names,
            )
            engine._disable_target_relations(connection, targets)
            columns = engine._column_inventory(connection)

        with scaffold.v11_execution_seam(engine):
            outcomes = tuple(
                engine._run_one_case(
                    engine=database.admin_engine,
                    artifact=artifact,
                    columns=columns,
                    authority_id=authority_id,
                    table_name=table_name,
                    guard_name=guard_name,
                    case_kind="LEGAL",
                )
                for authority_id, table_name, guard_name in targets
            )

    assert len(outcomes) == 27
    assert all(outcome["outcome"] == "ACCEPTED" for outcome in outcomes)
