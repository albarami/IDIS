"""Catalog proof for development revision 0039 on a disposable database."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from scripts.check_migration_ceiling import AUTHORIZED_HEAD
from scripts.run_cir04_restart_reference_dev_migration import (
    DEV0039_VERSIONS_DIR,
    upgrade_disposable_to_dev_0039,
)
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from idis.audit.postgres_sink import PostgresAuditSink
from idis.models.run_step import RunStep
from idis.persistence.repositories.task7_completion import (
    PostgresTask7CompletionPort,
    Task7PostgresPortError,
)
from idis.persistence.repositories.task7_run_graph import (
    PostgresTask7RunGraphRepository,
    RunOutputObjectRecord,
    RunStepOutputRecord,
)
from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH
from idis.pipeline.task7_completion import (
    STEP_OUTPUT_MISSING,
    Task7CompletionError,
    Task7CompletionFinalizationService,
    Task7LeaseScope,
)
from idis.pipeline.task7_edges import ProducerSelectorSpec
from idis.pipeline.task7_semantics import STEP_SEMANTIC_REGISTRY, SemanticValidationResult
from idis.pipeline.task7_source_receipt_builders import (
    _FrozenBinding,
    _FrozenManifest,
    _FrozenRoot,
    _FrozenSourceGraph,
    _handoff_payload,
    _inventory_payload,
    _OutputProducer,
    prepare_frozen_source_command,
)
from idis.pipeline.task7_usage import USAGE_ROUTE_REGISTRY
from idis.runtime.dependency_probes import (
    _REVISION_0037_TENANT_RLS_TABLES,
    _TASK7_TENANT_RLS_TABLES,
    TENANT_RLS_TABLES,
)
from idis.runtime.dependency_readiness import TASK7_V21_FUTURE_ERASURE_RELATIONS
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals
from tests.slice100_task7.test_task7_restart_rehydration_red import (
    _claim,
    _seed_durable_restart_run,
    _task7_graph_digest,
)
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


class _InjectedReferenceRollbackPort(PostgresTask7CompletionPort):
    """Lets the row-14 insert run, then aborts the completion transaction."""

    def persist_step_receipt_input_references(
        self,
        *,
        scope: object,
        step: RunStep,
        semantic_result: SemanticValidationResult,
    ) -> None:
        super().persist_step_receipt_input_references(
            scope=scope,
            step=step,
            semantic_result=semantic_result,
        )
        raise RuntimeError("INJECTED_REFERENCE_ROLLBACK")


def test_absent_generic_extraction_relation_is_fail_closed_on_0039(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterisation: route 10's relation is absent, so the loader fails closed."""
    for name in _FORBIDDEN:
        monkeypatch.delenv(name, raising=False)
    with disposable_postgres() as database:
        _install(database)
        with database.admin_engine.connect() as connection:
            port = PostgresTask7CompletionPort(
                connection,
                "11111111-1111-4111-8111-111111111111",
                PostgresAuditSink(),
                120,
            )
            with pytest.raises(Task7PostgresPortError) as error:
                port._require_relation("generic_extraction_input_references")
    assert error.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"


_DERIVE_TENANT = "11111111-1111-4111-8111-111111111111"
_OUTSIDE_FUTURE_SET = "not_a_reviewed_relation"


def _derive_scope() -> Task7LeaseScope:
    return Task7LeaseScope(
        tenant_id=_DERIVE_TENANT,
        deal_id="22222222-2222-4222-8222-222222222222",
        run_id="33333333-3333-4333-8333-333333333333",
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=FULL_STRICT_V21_CONTRACT_HASH,
        pipeline_resume_state="RESUMABLE",
        lease_owner_id="44444444-4444-4444-8444-444444444444",
        lease_fencing_token=1,
    )


def _derive_port(connection: Connection) -> PostgresTask7CompletionPort:
    return PostgresTask7CompletionPort(connection, _DERIVE_TENANT, PostgresAuditSink(), 120)


def _migrate_0038(database: DisposablePostgres) -> None:
    with database.admin_engine.begin() as connection:
        provision_exact_erasure_principals(connection)
        _ensure_guard_owner(connection)
    migrate(database.admin_engine, "0038")


def _catalog_versions(connection: Connection) -> list[str]:
    return [
        str(value)
        for value in connection.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        ).scalars()
    ]


def _single_relation_spec(relation: str) -> ProducerSelectorSpec:
    return ProducerSelectorSpec(
        rule_ordinal=0,
        rule_id="C6_R1",
        rule_version="v1",
        selector_ordinal=0,
        parameter_tuple=(),
        selector_ast=(),
        selector_sha256="a" * 64,
        base_selector_id="c6-r1",
        relations=(relation,),
        joins=(),
        projection=(
            "INPUT_BINDING",
            "binding_id",
            "selected_identity_sha256",
            "binding_sha256",
        ),
        predicate_ast=(),
        order_by=(),
        producer_condition="",
    )


def test_derive_edges_absent_future_relation_fails_closed_on_0038(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On 0038 an absent future relation still raises PIPELINE_CONTRACT_UNAVAILABLE."""
    for name in _FORBIDDEN:
        monkeypatch.delenv(name, raising=False)
    with disposable_postgres() as database:
        _migrate_0038(database)
        with database.admin_engine.connect() as connection:
            assert _catalog_versions(connection) == ["0038"]
            port = _derive_port(connection)
            with pytest.raises(Task7PostgresPortError) as error:
                port._derive_edges(_derive_scope())
    assert error.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"


def test_derive_edges_absent_core_relation_fails_closed_on_0039(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A core 0038 relation reported absent on 0039 fails closed."""
    for name in _FORBIDDEN:
        monkeypatch.delenv(name, raising=False)
    with disposable_postgres() as database:
        _install(database)
        with database.admin_engine.connect() as connection:
            assert _catalog_versions(connection) == ["0039"]
            port = _derive_port(connection)
            real = port._relation_exists

            def forced(relation: str) -> bool:
                if relation == "run_input_bindings":
                    return False
                return bool(real(relation))

            monkeypatch.setattr(port, "_relation_exists", forced)
            with pytest.raises(Task7PostgresPortError) as error:
                port._derive_edges(_derive_scope())
    assert error.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"


def test_tolerated_absent_relations_equal_reviewed_future_set_on_0039(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On 0039 the relations whose absence is tolerated are the reviewed future set."""
    for name in _FORBIDDEN:
        monkeypatch.delenv(name, raising=False)
    _row, _function_sql, census = _preimage()
    reviewed = set(census)
    assert reviewed == set(TASK7_V21_FUTURE_ERASURE_RELATIONS)
    assert len(reviewed) == 38
    assert len(TENANT_RLS_TABLES) == 64
    candidates = reviewed | set(TENANT_RLS_TABLES) | {_OUTSIDE_FUTURE_SET}
    with disposable_postgres() as database:
        _install(database)
        with database.admin_engine.connect() as connection:
            assert _catalog_versions(connection) == ["0039"]
            port = _derive_port(connection)
            real = port._relation_exists
            tolerated: set[str] = set()
            for relation in sorted(candidates):

                def forced(name: str, *, target: str = relation) -> bool:
                    if name == target:
                        return False
                    return bool(real(name))

                monkeypatch.setattr(port, "_relation_exists", forced)
                try:
                    rows = port._selector_rows(_single_relation_spec(relation))
                except Task7PostgresPortError as exc:
                    assert exc.code == "PIPELINE_CONTRACT_UNAVAILABLE"
                else:
                    assert rows == ()
                    tolerated.add(relation)
    assert tolerated == reviewed


def _binding(
    *,
    binding_id: str,
    kind: str,
    identity: str,
    content: str,
    package_id: str,
) -> _FrozenBinding:
    return _FrozenBinding(
        binding_id=binding_id,
        source_object_kind=kind,
        selected_identity_sha256=identity,
        selected_content_sha256=content,
        binding_sha256="55" * 32,
        corpus_receipt_id=package_id,
    )


def _root(
    *,
    object_id: str,
    kind: str,
    disposition: str,
    identity: str,
    content: str,
) -> _FrozenRoot:
    return _FrozenRoot(
        source_lineage_object_id=object_id,
        object_kind=kind,
        source_disposition=disposition,
        object_identity_sha256=identity,
        content_sha256=content,
    )


def _inventory_graph(disposition: str) -> _FrozenSourceGraph:
    package_id = "11111111-1111-4111-8111-111111111111"
    return _FrozenSourceGraph(
        roots=(
            _root(
                object_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                kind="PACKAGE",
                disposition="CORPUS",
                identity="11" * 32,
                content="44" * 32,
            ),
            _root(
                object_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                kind="PACKAGE_FILE",
                disposition=disposition,
                identity="22" * 32,
                content="33" * 32,
            ),
        ),
        bindings=(
            _binding(
                binding_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                kind="PACKAGE",
                identity="11" * 32,
                content="44" * 32,
                package_id=package_id,
            ),
            _binding(
                binding_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                kind="PACKAGE_FILE",
                identity="22" * 32,
                content="33" * 32,
                package_id=package_id,
            ),
        ),
        manifest=_FrozenManifest(file_count=1, total_bytes=1),
        inventory_receipt=None,
    )


def _handoff_graph(
    *,
    disposition: str = "EVIDENCE_BEARING",
    content: str = "77" * 32,
    selected: str = "77" * 32,
) -> _FrozenSourceGraph:
    return _FrozenSourceGraph(
        roots=(
            _root(
                object_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                kind="ARTIFACT",
                disposition=disposition,
                identity="88" * 32,
                content=content,
            ),
        ),
        bindings=(
            _binding(
                binding_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
                kind="ARTIFACT",
                identity="88" * 32,
                content=selected,
                package_id="12121212-1212-4121-8121-121212121212",
            ),
        ),
        manifest=_FrozenManifest(file_count=1, total_bytes=1),
        inventory_receipt=_OutputProducer(
            output_object_id="13131313-1313-4131-8131-131313131313",
            native_identity_sha256="ab" * 32,
            native_output_sha256="cd" * 32,
        ),
    )


def test_builder_counts_content_mismatch_and_refuses_unknown_disposition() -> None:
    """Mismatch is counted; a disposition outside the freeze pair is refused."""
    mismatched = _handoff_payload(_handoff_graph(content="aa" * 32, selected="bb" * 32))
    assert mismatched["object_hash_mismatch_count"] == 1
    assert _handoff_payload(_handoff_graph())["object_hash_mismatch_count"] == 0
    with pytest.raises(Task7CompletionError) as deferred:
        _inventory_payload(_inventory_graph("DEFERRED"))
    assert deferred.value.code == STEP_OUTPUT_MISSING
    with pytest.raises(Task7CompletionError) as unresolved:
        _handoff_payload(_handoff_graph(disposition="UNRESOLVED"))
    assert unresolved.value.code == STEP_OUTPUT_MISSING


def test_step_receipt_reference_loader_rejects_sibling_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row-14 row for consumer A must not satisfy a load for consumer B."""
    for name in _FORBIDDEN:
        monkeypatch.delenv(name, raising=False)
    with disposable_postgres() as database:
        _install(database)
        grant_application_access(database)
        seeded = _seed_durable_restart_run(database, label="sibling-consumer")
        owner_id = str(uuid.uuid4())
        claimed = _claim(database, scope=seeded, owner_id=owner_id)
        scope = replace(
            seeded,
            lease_owner_id=owner_id,
            lease_fencing_token=int(claimed["lease_fencing_token"]),
        )
        with database.app_engine.connect() as connection:
            port = PostgresTask7CompletionPort(
                connection, scope.tenant_id, PostgresAuditSink(), 120
            )
            port.establish_tenant_rls(scope=scope)
            edge = next(row for row in port._derive_edges(scope) if row.route_ordinal == 0)
            step = next(
                row for row in port.list_canonical_steps(scope=scope) if row.step_order == 0
            )
            spec = STEP_SEMANTIC_REGISTRY[step.step_name.value]
            identity_sha256 = "ab" * 32
            value_sha256 = "cd" * 32
            output_object_id = str(
                port._derive_output_id(
                    scope=scope,
                    output_class="STEP_RECEIPT",
                    output_kind=spec.aggregate_receipt_kind,
                    identity_sha256=identity_sha256,
                    value_sha256=value_sha256,
                )
            )
            step_output_id = str(
                port._derive_step_output_id(
                    scope=scope,
                    step=step,
                    subrole="__step__",
                    output_kind=spec.aggregate_receipt_kind,
                    ordinal=0,
                    output_object_id=output_object_id,
                )
            )
            summary_sha256 = hashlib.sha256(b"sibling-consumer-fixture").hexdigest()
            graph = PostgresTask7RunGraphRepository(connection, scope.tenant_id)
            graph.insert_run_output_object(
                RunOutputObjectRecord(
                    output_object_id=output_object_id,
                    tenant_id=scope.tenant_id,
                    deal_id=scope.deal_id,
                    run_id=scope.run_id,
                    output_class="STEP_RECEIPT",
                    output_kind=spec.aggregate_receipt_kind,
                    output_schema_version=spec.semantic_rule_version,
                    native_identity_sha256=identity_sha256,
                    native_output_sha256=value_sha256,
                    pipeline_contract_version=scope.pipeline_contract_version,
                    pipeline_contract_hash=scope.pipeline_contract_hash,
                    write_origin="RUNTIME",
                    lease_owner_id=scope.lease_owner_id,
                    lease_fencing_token=scope.lease_fencing_token,
                )
            )
            graph.insert_run_step_output(
                RunStepOutputRecord(
                    step_output_id=step_output_id,
                    tenant_id=scope.tenant_id,
                    deal_id=scope.deal_id,
                    run_id=scope.run_id,
                    step_id=step.step_id,
                    output_object_id=output_object_id,
                    step_name=step.step_name.value,
                    subrole="__step__",
                    output_kind=spec.aggregate_receipt_kind,
                    output_ordinal=0,
                    semantic_rule_version=spec.semantic_rule_version,
                    semantic_rule_hash=spec.semantic_rule_hash,
                    summary_sha256=summary_sha256,
                    lease_owner_id=scope.lease_owner_id,
                    lease_fencing_token=scope.lease_fencing_token,
                )
            )
            route = USAGE_ROUTE_REGISTRY[edge.route_ordinal]
            connection.execute(
                text(
                    """
                    INSERT INTO step_receipt_input_references (
                        receipt_reference_id, tenant_id, deal_id, run_id,
                        step_output_id, consumer_output_object_id, producer_type,
                        producer_input_binding_id, producer_output_object_id,
                        producer_identity_sha256, producer_value_sha256,
                        reference_kind, reference_ordinal, lease_owner_id,
                        lease_fencing_token
                    ) VALUES (
                        CAST(:receipt_reference_id AS uuid),
                        CAST(:tenant_id AS uuid), CAST(:deal_id AS uuid),
                        CAST(:run_id AS uuid), CAST(:step_output_id AS uuid),
                        CAST(:consumer_output_object_id AS uuid), :producer_type,
                        CAST(:producer_input_binding_id AS uuid), NULL,
                        :producer_identity_sha256, :producer_value_sha256,
                        :reference_kind, :reference_ordinal,
                        CAST(:lease_owner_id AS uuid), :lease_fencing_token
                    )
                    """
                ),
                {
                    "receipt_reference_id": str(uuid.uuid4()),
                    "tenant_id": scope.tenant_id,
                    "deal_id": scope.deal_id,
                    "run_id": scope.run_id,
                    "step_output_id": step_output_id,
                    "consumer_output_object_id": output_object_id,
                    "producer_type": edge.producer.producer_type,
                    "producer_input_binding_id": str(edge.producer.producer_id),
                    "producer_identity_sha256": edge.producer.producer_identity_sha256,
                    "producer_value_sha256": edge.producer.producer_value_sha256,
                    "reference_kind": route.reference_kind,
                    "reference_ordinal": 0,
                    "lease_owner_id": scope.lease_owner_id,
                    "lease_fencing_token": scope.lease_fencing_token,
                },
            )
            same = port._load_usage_occurrences(
                scope=scope,
                edge=edge,
                consumer_output_object_id=output_object_id,
                consumer_identity_sha256=identity_sha256,
                route_ordinal=edge.route_ordinal,
                consumer_step_output_id=step_output_id,
            )
            sibling = port._load_usage_occurrences(
                scope=scope,
                edge=edge,
                consumer_output_object_id=str(uuid.uuid4()),
                consumer_identity_sha256=identity_sha256,
                route_ordinal=edge.route_ordinal,
                consumer_step_output_id=step_output_id,
            )
            sibling_output = port._load_usage_occurrences(
                scope=scope,
                edge=edge,
                consumer_output_object_id=output_object_id,
                consumer_identity_sha256=identity_sha256,
                route_ordinal=edge.route_ordinal,
                consumer_step_output_id=str(uuid.uuid4()),
            )
    assert len(same) == 1
    assert sibling == ()
    assert sibling_output == ()


def test_dev_0039_reference_write_failure_rolls_back_graph_step_and_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reference-write failure leaves the pre-call graph and step unchanged."""
    for name in _FORBIDDEN:
        monkeypatch.delenv(name, raising=False)
    with disposable_postgres() as database:
        maintenance_before = _maintenance_alembic_versions()
        _install(database)
        grant_application_access(database)
        seeded = _seed_durable_restart_run(database, label="reference-rollback")
        owner_id = str(uuid.uuid4())
        claimed = _claim(database, scope=seeded, owner_id=owner_id)
        scope = replace(
            seeded,
            lease_owner_id=owner_id,
            lease_fencing_token=int(claimed["lease_fencing_token"]),
        )
        command = prepare_frozen_source_command(
            engine=database.app_engine, scope=scope, step_order=0
        )
        digest_before = _task7_graph_digest(database, scope)
        outputs_before = _relation_count(database, "run_output_objects", scope.run_id)
        service = Task7CompletionFinalizationService(
            connection_factory=database.app_engine.begin,
            port_factory=lambda connection, tenant_id: _InjectedReferenceRollbackPort(
                connection,
                tenant_id,
                PostgresAuditSink(),
                120,
            ),
        )
        with pytest.raises(Task7CompletionError) as caught:
            service.complete_step(command)
        cause = caught.value.__cause__
        assert isinstance(cause, RuntimeError)
        assert str(cause) == "INJECTED_REFERENCE_ROLLBACK"
        assert _step_status(database, scope.run_id, 0) == "RUNNING"
        assert _relation_count(database, "step_receipt_input_references", scope.run_id) == 0
        assert _relation_count(database, "run_output_objects", scope.run_id) == outputs_before
        assert _task7_graph_digest(database, scope) == digest_before
    assert _maintenance_alembic_versions() == maintenance_before


def _relation_count(database: DisposablePostgres, relation: str, run_id: str) -> int:
    with database.admin_engine.connect() as connection:
        return int(
            connection.execute(
                text(f"SELECT count(*) FROM {relation} WHERE run_id = CAST(:run_id AS uuid)"),
                {"run_id": run_id},
            ).scalar_one()
        )


def _step_status(database: DisposablePostgres, run_id: str, step_order: int) -> str:
    with database.admin_engine.connect() as connection:
        return str(
            connection.execute(
                text(
                    "SELECT status FROM run_steps "
                    "WHERE run_id = CAST(:run_id AS uuid) AND step_order = :step_order"
                ),
                {"run_id": run_id, "step_order": step_order},
            ).scalar_one()
        )


def _maintenance_alembic_versions() -> list[str]:
    engine = create_engine(make_url(os.environ["IDIS_DATABASE_ADMIN_URL"]))
    try:
        with engine.connect() as connection:
            present = connection.execute(
                text("SELECT to_regclass('public.alembic_version')")
            ).scalar()
            if present is None:
                return []
            return [
                str(row[0])
                for row in connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                )
            ]
    finally:
        engine.dispose()
