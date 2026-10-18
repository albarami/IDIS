"""Catalog proof for development revision 0039 on a disposable database."""

from __future__ import annotations

import inspect
import json
import re
import sys
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from scripts.check_migration_ceiling import AUTHORIZED_HEAD
from scripts.run_cir04_restart_reference_dev_migration import (
    DEV0039_VERSIONS_DIR,
    upgrade_disposable_to_dev_0039,
)
from sqlalchemy import Connection, text

from idis.runtime.dependency_probes import (
    _REVISION_0037_TENANT_RLS_TABLES,
    _TASK7_TENANT_RLS_TABLES,
    TENANT_RLS_TABLES,
)
from tests.slice100_task2b._support import DisposablePostgres, disposable_postgres, migrate
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals
from tests.test_cir04_restart_reference_dev_admission import _dev0039_migration

_ROOT = Path(__file__).resolve().parents[1]
_PREIMAGE = _ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_TYPE_NAMES = {
    "UUID": "uuid",
    "TEXT": "text",
    "INTEGER": "integer",
    "BIGINT": "bigint",
    "TIMESTAMPTZ": "timestamp with time zone",
}
_FORBIDDEN = (
    "IDIS_DATABASE_URL",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT_SHA256",
)


def _preimage() -> tuple[dict[str, object], str, list[str]]:
    payload = json.loads(_PREIMAGE.read_text(encoding="utf-8"))
    row = payload["future_reference_authority_registry"]["rows"][14]
    authority = next(
        item
        for item in payload["sql_authority_registry"]["rows"]
        if item["authority_id"] == "approved-erasure-append-only-v1"
    )
    function_sql = bytes.fromhex(authority["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    erasure = payload["erasure_execution_registry"]["rows"][0]
    census = [item[0] for item in erasure["future_activation_census_tuple"]]
    return row, function_sql, census


def _row(value: object) -> list[object]:
    if not isinstance(value, list):
        raise AssertionError("PREIMAGE_ROW_SHAPE")
    return value


def _policy_text(sql: str) -> str:
    return re.sub(r"[\s()]", "", sql.replace("::text", ""))


def _dollar_body(sql: str) -> str:
    opening = sql.index("$")
    closing = sql.index("$", opening + 1)
    tag = sql[opening : closing + 1]
    return "".join(sql[closing + 1 : sql.rindex(tag)].split())


def _ensure_guard_owner(connection: Connection) -> None:
    connection.exec_driver_sql("""
        DO $task7$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
            WHERE rolname = 'idis_task7_guard_function_owner'
          ) THEN
            CREATE ROLE idis_task7_guard_function_owner
              NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
        END
        $task7$;
        """)


def _downgrade_to_0038(connection: Connection) -> None:
    import idis.persistence.migrations as migrations_pkg

    script = str(Path(migrations_pkg.__file__).resolve().parent)
    config = Config()
    config.set_main_option("script_location", script)
    config.set_main_option(
        "version_locations",
        f"{Path(script) / 'versions'} {DEV0039_VERSIONS_DIR}",
    )
    config.attributes["connection"] = connection
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        command.downgrade(config, "0038")
    finally:
        sys.dont_write_bytecode = previous


def _install(database: DisposablePostgres) -> None:
    with database.admin_engine.begin() as connection:
        provision_exact_erasure_principals(connection)
        _ensure_guard_owner(connection)
    migrate(database.admin_engine, "0038")
    upgrade_disposable_to_dev_0039(database)


def test_row_14_catalog_and_empty_only_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _FORBIDDEN:
        monkeypatch.delenv(name, raising=False)
    row, function_sql, census = _preimage()
    with disposable_postgres() as database:
        _install(database)
        with database.admin_engine.connect() as connection:
            _assert_columns(connection, row)
            _assert_kind_check(connection, row)
            _assert_foreign_keys(connection, row)
            _assert_uniques_and_indexes(connection, row)
            _assert_rls_and_privileges(connection, row, database.role_name)
            _assert_guard(connection, function_sql, database.role_name)
            _assert_census(connection, census)
        assert len(TENANT_RLS_TABLES) == 64
        assert len(_REVISION_0037_TENANT_RLS_TABLES) == 55
        assert len(_TASK7_TENANT_RLS_TABLES) == 9
        assert AUTHORIZED_HEAD == "0038"
        _assert_downgrade(database)


def _assert_columns(connection: Connection, row: dict[str, object]) -> None:
    found = list(
        connection.execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'step_receipt_input_references' "
                "ORDER BY ordinal_position"
            )
        )
    )
    expected = []
    for column in _row(row["column_tuple"]):
        cells = _row(column)
        name = str(cells[1])
        definition = str(cells[2])
        type_name = definition.split()[0]
        expected.append(
            (
                name,
                _TYPE_NAMES[type_name],
                "NO" if "NOT NULL" in definition else "YES",
            )
        )
    assert [(item[0], item[1], item[2]) for item in found] == expected
    assert "statement_timestamp()" in str(found[-1][3])


def _assert_kind_check(connection: Connection, row: dict[str, object]) -> None:
    definition = connection.execute(
        text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
        {"name": "ck_v2_receipt_input_ref_kind"},
    ).scalar_one()
    found = set(re.findall(r"'([A-Z0-9_]+)'", definition))
    assert found == {str(item) for item in _row(row["reference_kind_tuple"])}
    assert len(found) == 32


def _assert_foreign_keys(connection: Connection, row: dict[str, object]) -> None:
    found = {
        (str(item.conname), str(item.parent_name), item.confupdtype, item.confdeltype)
        for item in connection.execute(
            text(
                "SELECT constraint_row.conname AS conname, "
                "parent_row.relname AS parent_name, "
                "constraint_row.confupdtype AS confupdtype, "
                "constraint_row.confdeltype AS confdeltype "
                "FROM pg_constraint AS constraint_row "
                "JOIN pg_class AS child_row ON child_row.oid = constraint_row.conrelid "
                "JOIN pg_class AS parent_row ON parent_row.oid = constraint_row.confrelid "
                "WHERE child_row.relname = 'step_receipt_input_references' "
                "AND constraint_row.contype = 'f'"
            )
        )
    }
    expected = {
        (str(_row(item)[0]), str(_row(item)[2]), "a", "a")
        for item in _row(row["foreign_key_tuple"])
    }
    assert found == expected


def _assert_uniques_and_indexes(connection: Connection, row: dict[str, object]) -> None:
    uniques = {
        str(item)
        for item in connection.execute(
            text(
                "SELECT constraint_row.conname FROM pg_constraint AS constraint_row "
                "JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid "
                "WHERE table_row.relname = 'step_receipt_input_references' "
                "AND constraint_row.contype IN ('p', 'u')"
            )
        ).scalars()
    }
    assert uniques == {str(_row(item)[0]) for item in _row(row["unique_tuple"])}
    indexes = {
        str(item.name): str(item.definition)
        for item in connection.execute(
            text(
                "SELECT index_row.relname AS name, pg_get_indexdef(index_row.oid) AS definition "
                "FROM pg_index AS index_meta "
                "JOIN pg_class AS index_row ON index_row.oid = index_meta.indexrelid "
                "JOIN pg_class AS table_row ON table_row.oid = index_meta.indrelid "
                "WHERE table_row.relname = 'step_receipt_input_references' "
                "AND NOT index_meta.indisunique"
            )
        )
    }
    for index in _row(row["index_tuple"]):
        cells = _row(index)
        definition = indexes[str(cells[0])]
        predicate = cells[2]
        for column in _row(cells[1]):
            assert str(column) in definition
        if predicate:
            assert str(predicate) in definition


def _assert_rls_and_privileges(connection: Connection, row: dict[str, object], role: str) -> None:
    flags = connection.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'step_receipt_input_references'"
        )
    ).one()
    assert tuple(flags) == (True, True)
    policy = connection.execute(
        text(
            "SELECT policy_row.polname, pg_get_expr(policy_row.polqual, policy_row.polrelid), "
            "pg_get_expr(policy_row.polwithcheck, policy_row.polrelid) "
            "FROM pg_policy AS policy_row "
            "JOIN pg_class AS table_row ON table_row.oid = policy_row.polrelid "
            "WHERE table_row.relname = 'step_receipt_input_references'"
        )
    ).one()
    statement = str(
        next(
            item
            for item in _row(_row(row["rls_tuple"])[0])
            if str(item).startswith("CREATE POLICY")
        )
    )
    expression = statement.split(" USING ", 1)[1].split(" WITH CHECK ", 1)[0]
    check = statement.split(" WITH CHECK ", 1)[1]
    assert policy[0] == "step_receipt_input_references_tenant_isolation"
    assert _policy_text(str(policy[1])) == _policy_text(expression)
    assert _policy_text(str(policy[2])) == _policy_text(check)
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert connection.execute(
            text(
                "SELECT has_table_privilege(CAST(:role AS name), "
                "'public.step_receipt_input_references', CAST(:privilege AS text))"
            ),
            {"role": role, "privilege": privilege},
        ).scalar_one()
        assert not connection.execute(
            text(
                "SELECT has_table_privilege('public', "
                "'public.step_receipt_input_references', CAST(:privilege AS text))"
            ),
            {"privilege": privilege},
        ).scalar_one()


def _assert_guard(connection: Connection, function_sql: str, role: str) -> None:
    definition = connection.execute(
        text(
            "SELECT pg_get_functiondef("
            "'public.idis_task7_approved_erasure_append_only_v1()'::regprocedure)"
        )
    ).scalar_one()
    assert _dollar_body(definition) == _dollar_body(function_sql)
    posture = connection.execute(
        text(
            "SELECT owner_role.rolname, procedure.prosecdef, procedure.proconfig "
            "FROM pg_proc AS procedure "
            "JOIN pg_namespace AS namespace_row ON namespace_row.oid = procedure.pronamespace "
            "JOIN pg_roles AS owner_role ON owner_role.oid = procedure.proowner "
            "WHERE namespace_row.nspname = 'public' "
            "AND procedure.proname = 'idis_task7_approved_erasure_append_only_v1'"
        )
    ).one()
    assert posture[0] == "idis_erasure_function_owner"
    assert posture[1] is True
    assert any("pg_catalog" in item and "pg_temp" in item for item in posture[2])
    signature = "public.idis_task7_approved_erasure_append_only_v1()"
    assert not connection.execute(
        text("SELECT has_function_privilege('public', :signature, 'EXECUTE')"),
        {"signature": signature},
    ).scalar_one()
    assert not connection.execute(
        text("SELECT has_function_privilege(CAST(:role AS name), :signature, 'EXECUTE')"),
        {"role": role, "signature": signature},
    ).scalar_one()
    triggers = list(
        connection.execute(
            text(
                "SELECT trigger_row.tgname FROM pg_trigger AS trigger_row "
                "JOIN pg_proc AS procedure ON procedure.oid = trigger_row.tgfoid "
                "JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid "
                "WHERE procedure.proname = 'idis_task7_approved_erasure_append_only_v1' "
                "AND table_row.relname = 'step_receipt_input_references' "
                "AND NOT trigger_row.tgisinternal"
            )
        ).scalars()
    )
    assert triggers == [_dev0039_migration()._TRIGGER_CATALOG]
    posture_bits = connection.execute(
        text(
            "SELECT trigger_row.tgtype, procedure.prosrc "
            "FROM pg_trigger AS trigger_row "
            "JOIN pg_proc AS procedure ON procedure.oid = trigger_row.tgfoid "
            "JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid "
            "WHERE procedure.proname = 'idis_task7_approved_erasure_append_only_v1' "
            "AND table_row.relname = 'step_receipt_input_references' "
            "AND NOT trigger_row.tgisinternal"
        )
    ).one()
    tgtype = int(posture_bits[0])
    assert tgtype & (1 << 1)
    assert tgtype & (1 << 0)
    assert tgtype & (1 << 2)
    assert tgtype & (1 << 3)
    assert tgtype & (1 << 4)
    assert (tgtype & (1 << 5)) == 0
    assert (tgtype & (1 << 6)) == 0
    reviewed_source = function_sql[function_sql.index("$$") + 2 : function_sql.rindex("$$")]
    assert posture_bits[1] == reviewed_source


def _assert_census(connection: Connection, census: list[str]) -> None:
    present = [
        name
        for name in census
        if connection.execute(
            text("SELECT to_regclass(:qualified) IS NOT NULL"),
            {"qualified": f"public.{name}"},
        ).scalar_one()
    ]
    assert present == ["step_receipt_input_references"]
    assert len(census) == 38


def _assert_downgrade(database: DisposablePostgres) -> None:
    tenant = str(uuid.uuid4())
    with database.admin_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET LOCAL session_replication_role = 'replica'"))
        connection.execute(
            text("SELECT set_config('idis.tenant_id', :tenant, true)"),
            {"tenant": tenant},
        )
        connection.execute(
            text(
                "INSERT INTO public.step_receipt_input_references ("
                "receipt_reference_id, tenant_id, deal_id, run_id, step_output_id, "
                "consumer_output_object_id, producer_type, producer_input_binding_id, "
                "producer_identity_sha256, producer_value_sha256, reference_kind, "
                "reference_ordinal, lease_owner_id, lease_fencing_token"
                ") VALUES ("
                "CAST(:id AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid), "
                "CAST(:run AS uuid), CAST(:step AS uuid), CAST(:consumer AS uuid), "
                "'INPUT_BINDING', CAST(:binding AS uuid), :digest, :digest, "
                "'SOURCE_INVENTORY', 0, CAST(:lease AS uuid), 1)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant": tenant,
                "deal": str(uuid.uuid4()),
                "run": str(uuid.uuid4()),
                "step": str(uuid.uuid4()),
                "consumer": str(uuid.uuid4()),
                "binding": str(uuid.uuid4()),
                "digest": "a" * 64,
                "lease": str(uuid.uuid4()),
            },
        )
        with pytest.raises(RuntimeError, match="^DEV0039_DOWNGRADE_NONEMPTY$"):
            _downgrade_to_0038(connection)
        assert connection.execute(
            text("SELECT to_regclass('public.step_receipt_input_references')")
        ).scalar_one()
        transaction.rollback()
    with database.admin_engine.begin() as connection:
        _downgrade_to_0038(connection)
        assert list(
            connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars()
        ) == ["0038"]
        assert (
            connection.execute(
                text("SELECT to_regclass('public.step_receipt_input_references')")
            ).scalar_one()
            is None
        )
        assert not connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_policy WHERE polname = "
                "'step_receipt_input_references_tenant_isolation')"
            )
        ).scalar_one()
        assert not connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname::text = :name)"),
            {"name": _dev0039_migration()._TRIGGER_CATALOG},
        ).scalar_one()


def test_downgrade_check_uses_truncated_trigger_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stored trigger name is 63 bytes, so text equality rejects the full name."""
    for name in _FORBIDDEN:
        monkeypatch.delenv(name, raising=False)
    migration = _dev0039_migration()
    full_name = migration._TRIGGER
    with disposable_postgres() as database:
        _install(database)
        with database.admin_engine.connect() as connection:
            stored = str(
                connection.execute(
                    text(
                        "SELECT trigger_row.tgname::text "
                        "FROM pg_trigger AS trigger_row "
                        "JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid "
                        "WHERE table_row.relname = 'step_receipt_input_references' "
                        "AND NOT trigger_row.tgisinternal"
                    )
                ).scalar_one()
            )
            text_hit = bool(
                connection.execute(
                    text("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname::text = :name)"),
                    {"name": full_name},
                ).scalar_one()
            )
    source = "".join(inspect.getsource(migration._assert_removed).split())
    catalog_constant = getattr(migration, "_TRIGGER_CATALOG", None)
    assert (
        text_hit is False
        and stored == full_name[:63]
        and len(full_name) == 68
        and '{"trigger":_TRIGGER_CATALOG,"policy":_POLICY}' in source
        and catalog_constant == stored
    ), f"text_hit={text_hit} stored={stored} full_len={len(full_name)} constant={catalog_constant}"
