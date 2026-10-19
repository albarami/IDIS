"""Operator-backed proof for frozen-v21 migration and erasure authority.

Administrative connections are limited to disposable-database setup and
Alembic mechanics.  Catalog, readiness, role, guard, and erasure behavior are
observed through the real application or dedicated-erasure login.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sys
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from sqlalchemy import Connection, Engine, bindparam, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from tests._operator_preconditions import requires_operator_postgres
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    downgrade,
    grant_application_access,
    migrate,
    set_tenant_context,
)
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals

from idis.audit.postgres_sink import PostgresAuditSink
from idis.compliance import task7_erasure
from idis.runtime.dependency_probes import collect_erasure_executor_facts

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)
_MIGRATIONS_DIR = _REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions"
_RUNNER = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_OWNER = "idis_erasure_function_owner"
_EXECUTOR = "idis_erasure_executor"
_GUARD_OWNER = "idis_task7_guard_function_owner"
_SELECT_UPDATE = ("SELECT", "UPDATE")
_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _load_migration() -> ModuleType:
    paths = tuple(sorted(_MIGRATIONS_DIR.glob("0038_*.py")))
    assert len(paths) == 1, "Task 7 requires exactly one linear migration 0038"
    name = "_task7_migration_0038_v21_operator_red"
    spec = importlib.util.spec_from_file_location(name, paths[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _require_frozen_v21_runtime() -> tuple[dict[str, Any], dict[str, Any]]:
    missing = tuple(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_ARTIFACT_PATH, _FIXTURE_PATH)
        if not path.is_file()
    )
    assert not missing, "TASK7_V21_POSTGRES_AUTHORITY_NOT_PUBLISHED:" + ",".join(missing)
    artifact_raw = _ARTIFACT_PATH.read_bytes()
    artifact = _load_json(_ARTIFACT_PATH)
    fixture = _load_json(_FIXTURE_PATH)
    assert artifact["metadata"]["append_frozen"] is True
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v21"
    assert fixture["fixture_schema"] == "full-strict-v21-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == "full-strict-v21"
    assert fixture["artifact_sha256"] == hashlib.sha256(artifact_raw).hexdigest()
    assert (
        fixture["component_hashes"]["pipeline_contract_hash"]
        == fixture["FULL_STRICT_V21_CONTRACT_HASH"]
    )
    census = fixture["postgres_authority_census"]
    assert census["admin_case_execution_count"] == 0
    assert census["application_case_execution_count"] == 219
    assert census["legal_execution_count"] == 73
    assert census["illegal_rejection_count"] == 73
    assert census["direct_sql_rejection_count"] == 73

    migration = _load_migration()
    readiness = importlib.import_module("idis.runtime.dependency_readiness")
    assert migration.FULL_STRICT_V21_ACTIVATION_STATE == "INACTIVE"
    assert callable(migration._run_v21_guard_authority_preflight)
    assert callable(migration._install_v21_current_guard_authority)
    assert callable(migration._remove_v21_guard_and_erasure_authority)
    assert not hasattr(migration, "FULL_STRICT_V20_CONTRACT_HASH")
    assert not hasattr(migration, "FULL_STRICT_V19_CONTRACT_HASH")
    assert hasattr(readiness, "Task7GuardOwnerProbeFacts")
    assert hasattr(readiness, "Task7ErasureRunnerProbeFacts")
    assert hasattr(readiness, "TASK7_V21_CURRENT_ERASURE_RELATIONS")
    assert readiness.task7_v21_erasure_runner_split_is_valid()
    return artifact, fixture


def _erasure_row(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    section = artifact["erasure_execution_registry"]
    assert isinstance(section, Mapping)
    rows = section["rows"]
    assert isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], Mapping)
    return cast(Mapping[str, Any], rows[0])


def _decode_hex_tuple(value: object) -> tuple[str, ...]:
    assert isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    assert all(isinstance(item, str) for item in value)
    return tuple(bytes.fromhex(cast(str, item)).decode("utf-8") for item in value)


def _provision_setup_principals(database: DisposablePostgres) -> None:
    with database.admin_engine.begin() as connection:
        provision_exact_erasure_principals(connection)
        connection.exec_driver_sql(
            """
            DO $task7$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname='idis_task7_guard_function_owner'
              ) THEN
                CREATE ROLE idis_task7_guard_function_owner
                  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                  NOINHERIT NOBYPASSRLS;
              END IF;
            END
            $task7$;
            """
        )


_V21_OPERATOR_POSTGRES_FAILURE = (
    "TASK7_V21_OPERATOR_DATABASE_CONFIGURATION_MISSING:IDIS_ERASURE_DATABASE_URL"
)


def _required_erasure_url() -> str:
    value = os.environ.get("IDIS_ERASURE_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1" or (
        os.environ.get("IDIS_REQUIRE_OPERATOR_POSTGRES") == "1"
    ):
        pytest.fail(_V21_OPERATOR_POSTGRES_FAILURE, pytrace=False)
    raise AssertionError("operator postgres gate did not record IDIS_ERASURE_DATABASE_URL")


def _executor_engine(database: DisposablePostgres) -> Engine:
    configured = make_url(_required_erasure_url())
    admin = database.admin_engine.url
    assert configured.username == _EXECUTOR
    assert (configured.host, configured.port) == (admin.host, admin.port)
    engine = create_engine(
        configured.set(database=database.database_name),
        pool_pre_ping=True,
    )
    with engine.connect() as connection:
        _assert_executor_role(connection)
    return engine


@contextmanager
def _v19_database() -> Iterator[tuple[DisposablePostgres, Engine]]:
    """Compatibility export used by the historical-name API composition test."""

    _require_frozen_v21_runtime()
    with disposable_postgres() as database:
        _provision_setup_principals(database)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        executor = _executor_engine(database)
        try:
            yield database, executor
        finally:
            executor.dispose()


def _assert_app_role(connection: Connection, expected_role: str) -> None:
    row = connection.execute(
        text(
            """
            SELECT session_user::text,current_user::text,
                   role.rolsuper,role.rolbypassrls,role.rolcanlogin,
                   role.rolcreatedb,role.rolcreaterole,role.rolinherit
            FROM pg_catalog.pg_roles AS role
            WHERE role.rolname=current_user
            """
        )
    ).one()
    assert tuple(row) == (
        expected_role,
        expected_role,
        False,
        False,
        True,
        False,
        False,
        False,
    )


def _assert_executor_role(connection: Connection) -> None:
    row = connection.execute(
        text(
            """
            SELECT session_user::text,current_user::text,
                   role.rolsuper,role.rolbypassrls,role.rolcanlogin,
                   role.rolcreatedb,role.rolcreaterole,role.rolinherit
            FROM pg_catalog.pg_roles AS role
            WHERE role.rolname=current_user
            """
        )
    ).one()
    assert tuple(row) == (
        _EXECUTOR,
        _EXECUTOR,
        False,
        False,
        True,
        False,
        False,
        False,
    )


def _authority_snapshot(
    connection: Connection,
    *,
    app_role: str,
) -> tuple[tuple[Any, ...], ...]:
    _assert_app_role(connection, app_role)
    return tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT 'routine',namespace.nspname,procedure.proname,
                       pg_catalog.pg_get_function_identity_arguments(procedure.oid),
                       owner.rolname,procedure.prosecdef,procedure.proconfig::text,
                       procedure.proacl::text,
                       pg_catalog.pg_get_functiondef(procedure.oid)
                FROM pg_catalog.pg_proc AS procedure
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=procedure.pronamespace
                JOIN pg_catalog.pg_roles AS owner ON owner.oid=procedure.proowner
                WHERE namespace.nspname='public'
                  AND (
                    procedure.proname='idis_task7_execute_approved_erasure'
                    OR procedure.proname LIKE 'idis_task7_%'
                  )
                UNION ALL
                SELECT 'relation-acl',namespace.nspname,relation.relname,
                       grantee.rolname,grantor.rolname,acl.is_grantable,
                       acl.privilege_type,NULL,''
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) AS acl
                JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                JOIN pg_catalog.pg_roles AS grantor ON grantor.oid=acl.grantor
                WHERE namespace.nspname='public'
                  AND relation.relkind IN ('r','p')
                  AND grantee.rolname LIKE 'idis_erasure_%'
                UNION ALL
                SELECT 'column-acl',namespace.nspname,relation.relname,
                       attribute.attname || ':' || grantee.rolname,
                       grantor.rolname,acl.is_grantable,
                       acl.privilege_type,NULL,''
                FROM pg_catalog.pg_attribute AS attribute
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid=attribute.attrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) AS acl
                JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                JOIN pg_catalog.pg_roles AS grantor ON grantor.oid=acl.grantor
                WHERE namespace.nspname='public'
                  AND attribute.attnum>0 AND NOT attribute.attisdropped
                  AND grantee.rolname LIKE 'idis_erasure_%'
                UNION ALL
                SELECT 'schema-acl',namespace.nspname,'',
                       CASE
                         WHEN acl.grantee=0 THEN 'PUBLIC'
                         ELSE grantee.rolname
                       END,
                       grantor.rolname,acl.is_grantable,
                       acl.privilege_type,NULL,''
                FROM pg_catalog.pg_namespace AS namespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(namespace.nspacl) AS acl
                LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                JOIN pg_catalog.pg_roles AS grantor ON grantor.oid=acl.grantor
                WHERE namespace.nspname='public'
                UNION ALL
                SELECT 'trigger',namespace.nspname,relation.relname,
                       trigger.tgname,owner.rolname,false,
                       trigger.tgenabled::text,NULL,
                       pg_catalog.pg_get_triggerdef(trigger.oid,true)
                FROM pg_catalog.pg_trigger AS trigger
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid=trigger.tgrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                JOIN pg_catalog.pg_proc AS procedure
                  ON procedure.oid=trigger.tgfoid
                JOIN pg_catalog.pg_roles AS owner ON owner.oid=procedure.proowner
                WHERE namespace.nspname='public'
                  AND NOT trigger.tgisinternal
                  AND procedure.proname LIKE 'idis_task7_%'
                ORDER BY 1,2,3,4,5,6,7,8,9
                """
            )
        ).fetchall()
    )


def _direct_erasure_schema_acl_entries(
    connection: Connection,
    *,
    app_role: str,
) -> tuple[tuple[str, str, bool], ...]:
    """Read only explicit public-schema ACL entries for the two erasure roles."""

    _assert_app_role(connection, app_role)
    return tuple(
        (str(row.grantee), str(row.privilege_type), bool(row.is_grantable))
        for row in connection.execute(
            text(
                """
                SELECT grantee.rolname AS grantee,
                       acl.privilege_type,
                       acl.is_grantable
                FROM pg_catalog.pg_namespace AS namespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(namespace.nspacl) AS acl
                JOIN pg_catalog.pg_roles AS grantee
                  ON grantee.oid=acl.grantee
                WHERE namespace.nspname='public'
                  AND grantee.rolname IN (:owner,:executor)
                ORDER BY grantee.rolname,acl.privilege_type,acl.is_grantable
                """
            ),
            {"owner": _OWNER, "executor": _EXECUTOR},
        )
    )


def _owner_acl_projection(
    connection: Connection,
    *,
    app_role: str,
) -> dict[str, object]:
    _assert_app_role(connection, app_role)
    posture = connection.execute(
        text(
            """
            SELECT pg_catalog.has_schema_privilege(:owner,'public','USAGE'),
                   pg_catalog.has_schema_privilege(:owner,'public','CREATE'),
                   (
                     SELECT count(*)
                     FROM pg_catalog.pg_class AS relation
                     JOIN pg_catalog.pg_roles AS owner
                       ON owner.oid=relation.relowner
                     WHERE owner.rolname=:owner
                   ),
                   (
                     SELECT count(*)
                     FROM pg_catalog.pg_namespace AS namespace
                     CROSS JOIN LATERAL pg_catalog.aclexplode(
                       COALESCE(
                         namespace.nspacl,
                         pg_catalog.acldefault('n',namespace.nspowner)
                       )
                     ) AS acl
                     JOIN pg_catalog.pg_roles AS grantee
                       ON grantee.oid=acl.grantee
                     WHERE namespace.nspname='public'
                       AND grantee.rolname=:owner
                       AND acl.privilege_type='USAGE'
                   ),
                   (
                     SELECT count(*)
                     FROM pg_catalog.pg_namespace AS namespace
                     CROSS JOIN LATERAL pg_catalog.aclexplode(
                       COALESCE(
                         namespace.nspacl,
                         pg_catalog.acldefault('n',namespace.nspowner)
                       )
                     ) AS acl
                     JOIN pg_catalog.pg_roles AS grantee
                       ON grantee.oid=acl.grantee
                     WHERE namespace.nspname='public'
                       AND grantee.rolname=:owner
                       AND acl.privilege_type<>'USAGE'
                   )
            """
        ),
        {"owner": _OWNER},
    ).one()
    table_counts = {
        str(row.privilege_type): int(row.relation_count)
        for row in connection.execute(
            text(
                """
                SELECT privilege.privilege_type,count(*)::integer AS relation_count
                FROM (
                  VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),
                         ('TRUNCATE'),('REFERENCES'),('TRIGGER')
                ) AS privilege(privilege_type)
                JOIN pg_catalog.pg_class AS relation
                  ON relation.relkind IN ('r','p')
                 AND pg_catalog.has_table_privilege(
                       :owner,relation.oid,privilege.privilege_type
                     )
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                 AND namespace.nspname='public'
                GROUP BY privilege.privilege_type
                ORDER BY privilege.privilege_type
                """
            ),
            {"owner": _OWNER},
        )
    }
    column_rows = tuple(
        (
            str(row.relation_name),
            str(row.column_name),
            str(row.privilege_type),
            False,
        )
        for row in connection.execute(
            text(
                """
                SELECT relation.relname AS relation_name,
                       attribute.attname AS column_name,
                       privilege.privilege_type
                FROM (
                  VALUES ('SELECT'),('UPDATE')
                ) AS privilege(privilege_type)
                JOIN pg_catalog.pg_class AS relation
                  ON relation.relkind IN ('r','p')
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                 AND namespace.nspname='public'
                JOIN pg_catalog.pg_attribute AS attribute
                  ON attribute.attrelid=relation.oid
                 AND attribute.attnum>0
                 AND NOT attribute.attisdropped
                WHERE pg_catalog.has_column_privilege(
                        :owner,relation.oid,attribute.attname,
                        privilege.privilege_type
                      )
                  AND NOT pg_catalog.has_table_privilege(
                        :owner,relation.oid,privilege.privilege_type
                      )
                ORDER BY relation.relname,attribute.attname,privilege.privilege_type
                """
            ),
            {"owner": _OWNER},
        )
    )
    delete_relations = tuple(
        str(row.relation_name)
        for row in connection.execute(
            text(
                """
                SELECT relation.relname AS relation_name
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                WHERE namespace.nspname='public'
                  AND relation.relkind IN ('r','p')
                  AND pg_catalog.has_table_privilege(
                        :owner,relation.oid,'DELETE'
                      )
                ORDER BY relation.relname
                """
            ),
            {"owner": _OWNER},
        )
    )
    return {
        "schema_usage": bool(posture[0]),
        "schema_create": bool(posture[1]),
        "owned_relation_count": int(posture[2]),
        "schema_direct_usage_acl_count": int(posture[3]),
        "schema_unexpected_acl_count": int(posture[4]),
        "table_counts": {
            privilege: table_counts.get(privilege, 0) for privilege in _TABLE_PRIVILEGES
        },
        "column_rows": column_rows,
        "delete_relations": delete_relations,
    }


def _artifact_owner_projection(artifact: Mapping[str, Any]) -> dict[str, object]:
    row = _erasure_row(artifact)
    statements = _decode_hex_tuple(row["current_acl_sql_utf8_hex_tuple"])
    column_rows: list[tuple[str, str, str, bool]] = []
    delete_relations: list[str] = []
    for statement in statements:
        for privilege in _SELECT_UPDATE:
            prefix = f"GRANT {privilege} ("
            suffix = f" TO {_OWNER}"
            if statement.startswith(prefix) and statement.endswith(suffix):
                columns, relation = statement.removeprefix(prefix).split(
                    ") ON TABLE public.",
                    maxsplit=1,
                )
                relation = relation.removesuffix(suffix)
                column_rows.extend(
                    (relation, column, privilege, False) for column in columns.split(",")
                )
        delete_prefix = "GRANT DELETE ON TABLE public."
        delete_suffix = f" TO {_OWNER}"
        if statement.startswith(delete_prefix) and statement.endswith(delete_suffix):
            delete_relations.append(
                statement.removeprefix(delete_prefix).removesuffix(delete_suffix)
            )
    return {
        "schema_usage": f"GRANT USAGE ON SCHEMA public TO {_OWNER}" in statements,
        "schema_create": f"GRANT CREATE ON SCHEMA public TO {_OWNER}" in statements,
        "owned_relation_count": 0,
        "schema_direct_usage_acl_count": sum(
            statement == f"GRANT USAGE ON SCHEMA public TO {_OWNER}" for statement in statements
        ),
        "schema_unexpected_acl_count": sum(
            statement.startswith("GRANT ")
            and " ON SCHEMA public " in statement
            and statement.endswith(f" TO {_OWNER}")
            and statement != f"GRANT USAGE ON SCHEMA public TO {_OWNER}"
            for statement in statements
        ),
        "table_counts": {
            privilege: sum(
                statement.startswith(f"GRANT {privilege} ON TABLE public.")
                and statement.endswith(f" TO {_OWNER}")
                for statement in statements
            )
            for privilege in _TABLE_PRIVILEGES
        },
        "column_rows": tuple(sorted(column_rows)),
        "delete_relations": tuple(sorted(delete_relations)),
    }


def _function_acl(
    connection: Connection,
    *,
    app_role: str,
) -> tuple[bool, bool, tuple[str, ...]]:
    _assert_app_role(connection, app_role)
    row = connection.execute(
        text(
            """
            SELECT pg_catalog.has_function_privilege(
                       CAST(:app_role AS name),
                       procedure.oid,
                       'EXECUTE'
                   ) AS app_execute,
                   pg_catalog.has_function_privilege(
                       CAST(:executor AS name),
                       procedure.oid,
                       'EXECUTE'
                   ) AS executor_execute,
                   ARRAY(
                     SELECT CASE
                              WHEN acl.grantee=0 THEN 'PUBLIC'
                              ELSE grantee.rolname
                            END
                     FROM pg_catalog.aclexplode(
                       COALESCE(
                         procedure.proacl,
                         pg_catalog.acldefault('f',procedure.proowner)
                       )
                     ) AS acl
                     LEFT JOIN pg_catalog.pg_roles AS grantee
                       ON grantee.oid=acl.grantee
                     WHERE acl.privilege_type='EXECUTE'
                       AND acl.grantee<>procedure.proowner
                     ORDER BY 1
                   ) AS execute_grantees
            FROM pg_catalog.pg_proc AS procedure
            WHERE procedure.oid=pg_catalog.to_regprocedure(:signature)
            """
        ),
        {
            "app_role": app_role,
            "executor": _EXECUTOR,
            "signature": _RUNNER,
        },
    ).one()
    return bool(row.app_execute), bool(row.executor_execute), tuple(row.execute_grantees)


def _seed_request(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
) -> None:
    with database.app_engine.begin() as connection:
        _assert_app_role(connection, database.role_name)
        set_tenant_context(connection, tenant_id)
        connection.execute(
            text(
                """
                INSERT INTO tenants (tenant_id,name)
                VALUES (CAST(:tenant_id AS uuid),'Task 7 v19 operator tenant');
                INSERT INTO deals (deal_id,tenant_id,name,created_at)
                VALUES (
                  CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),
                  'Fictional Task 7 v19 deal',statement_timestamp()
                );
                INSERT INTO erasure_requests (
                  tenant_id,request_id,deal_id,status,requested_by,requested_at,
                  reason_hash,reason_length,counts
                ) VALUES (
                  CAST(:tenant_id AS uuid),CAST(:request_id AS uuid),
                  CAST(:deal_id AS uuid),'REQUESTED','task7-v19-operator',
                  statement_timestamp(),:reason_hash,1,
                  :counts
                )
                """
            ).bindparams(bindparam("counts", type_=JSONB)),
            {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "request_id": request_id,
                "reason_hash": "a" * 64,
                "counts": {"object_rows_reconciled": 0},
            },
        )


def _persist_attempted_event(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
) -> dict[str, Any]:
    lineage = task7_erasure.derive_attempt_lineage(
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
    )
    target_sha256 = task7_erasure.object_target_set_sha256(
        tenant_id=tenant_id,
        deal_id=deal_id,
        ordered_uris=(),
    )
    event = task7_erasure.build_erasure_audit_event(
        tenant_id=tenant_id,
        deal_id=deal_id,
        actor_id="task7-v19-operator-admin",
        occurred_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        lineage=lineage,
        event_type="task7.erasure.attempted",
        object_target_set_sha256=target_sha256,
        counts=None,
    )
    with database.app_engine.begin() as connection:
        _assert_app_role(connection, database.role_name)
        set_tenant_context(connection, tenant_id)
        PostgresAuditSink().emit_in_tx(connection, event)
    return cast(dict[str, Any], event)


def _state(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
) -> tuple[tuple[Any, ...], int, tuple[tuple[str, str], ...]]:
    with database.app_engine.begin() as connection:
        _assert_app_role(connection, database.role_name)
        set_tenant_context(connection, tenant_id)
        request = tuple(
            connection.execute(
                text(
                    """
                    SELECT status,executed_by,executed_at,counts
                    FROM erasure_requests
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND request_id=CAST(:request_id AS uuid)
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "request_id": request_id,
                },
            ).one()
        )
        deal_count = int(
            connection.execute(
                text(
                    """
                    SELECT count(*) FROM deals
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                    """
                ),
                {"tenant_id": tenant_id, "deal_id": deal_id},
            ).scalar_one()
        )
        events = tuple(
            (str(row.event_id), str(row.event_type))
            for row in connection.execute(
                text(
                    """
                    SELECT event_id,event_type FROM audit_events
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND event #>> '{payload,safe,request_id}'=:request_id
                    ORDER BY event_id
                    """
                ),
                {"tenant_id": tenant_id, "request_id": request_id},
            )
        )
        return request, deal_count, events


def _invoke(
    engine: Engine,
    *,
    request_id: str,
    tenant_id: str,
    deal_id: str,
) -> Mapping[str, Any]:
    with engine.begin() as connection:
        if engine.url.username == _EXECUTOR:
            _assert_executor_role(connection)
        else:
            assert engine.url.username is not None
            _assert_app_role(connection, engine.url.username)
        value = connection.execute(
            text(
                """
                SELECT public.idis_task7_execute_approved_erasure(
                  CAST(:request_id AS uuid),
                  CAST(:tenant_id AS uuid),
                  CAST(:deal_id AS uuid)
                )
                """
            ),
            {
                "request_id": request_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
            },
        ).scalar_one()
    assert isinstance(value, Mapping)
    return value


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)


def test_v21_upgrade_downgrade_upgrade_restores_exact_authority_as_app_role() -> None:
    _require_frozen_v21_runtime()
    with disposable_postgres() as database:
        _provision_setup_principals(database)
        migrate(database.admin_engine, "0037")
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with database.app_engine.connect() as connection:
            first = _authority_snapshot(connection, app_role=database.role_name)
            assert any(row[2] == "idis_task7_execute_approved_erasure" for row in first)
            direct_schema_acl = _direct_erasure_schema_acl_entries(
                connection,
                app_role=database.role_name,
            )
            assert direct_schema_acl == (
                (_EXECUTOR, "USAGE", False),
                (_OWNER, "USAGE", False),
            )
            assert tuple(row for row in direct_schema_acl if row[0] == _EXECUTOR) == (
                (_EXECUTOR, "USAGE", False),
            )

        downgrade(database.admin_engine, "0037")
        with database.app_engine.connect() as connection:
            _assert_app_role(connection, database.role_name)
            assert (
                _direct_erasure_schema_acl_entries(
                    connection,
                    app_role=database.role_name,
                )
                == ()
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_catalog.pg_proc "
                        "WHERE oid=pg_catalog.to_regprocedure(:signature)"
                    ),
                    {"signature": _RUNNER},
                ).scalar_one()
                == 0
            )

        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with database.app_engine.connect() as connection:
            restored = _authority_snapshot(connection, app_role=database.role_name)
        assert restored == first


@requires_operator_postgres(
    "IDIS_ERASURE_DATABASE_URL",
    failure_code=_V21_OPERATOR_POSTGRES_FAILURE,
)
def test_v21_owner_acl_and_readiness_are_catalog_proven_without_admin_assertions() -> None:
    artifact, _fixture = _require_frozen_v21_runtime()
    with _v19_database() as (database, executor):
        with database.app_engine.connect() as connection:
            actual_owner = _owner_acl_projection(
                connection,
                app_role=database.role_name,
            )
            app_execute, executor_execute, execute_grantees = _function_acl(
                connection,
                app_role=database.role_name,
            )
        assert actual_owner == _artifact_owner_projection(artifact)
        assert actual_owner["table_counts"] == {
            "SELECT": 0,
            "INSERT": 0,
            "UPDATE": 0,
            "DELETE": 48,
            "TRUNCATE": 0,
            "REFERENCES": 0,
            "TRIGGER": 0,
        }
        assert app_execute is False
        assert executor_execute is True
        assert execute_grantees == (_EXECUTOR,)

        facts = collect_erasure_executor_facts(
            app_connection_factory=database.app_engine.connect,
            erasure_connection_factory=executor.connect,
        )
        readiness = importlib.import_module("idis.runtime.dependency_readiness")
        result = readiness.check_erasure_executor_readiness(
            env={"IDIS_ERASURE_DATABASE_URL": str(executor.url)},
            expected_role="api",
            probe=lambda: facts,
        )
        assert facts.authenticated_role == _EXECUTOR
        assert facts.effective_role == _EXECUTOR
        assert facts.app_role == database.role_name
        assert isinstance(facts.owner_role, readiness.ErasureRoleFacts)
        assert facts.owner_role.role_name == _OWNER
        assert facts.owner_role.can_login is False
        assert facts.owner_role.superuser is False
        assert facts.owner_role.bypass_rls is False
        assert facts.owner_role.create_database is False
        assert facts.owner_role.create_role is False
        assert facts.owner_role.inherit is False
        assert facts.membership_count == 0
        assert facts.executor_table_privilege_count == 0
        assert facts.executor_column_privilege_count == 0
        assert facts.executor_schema_usage is True
        assert facts.executor_schema_create is False
        assert facts.executor_schema_direct_acl_count == 1
        assert facts.executor_other_direct_execute_count == 0
        assert facts.function_count == 1
        assert facts.function_owner == _OWNER
        assert facts.function_security_definer is True
        assert facts.function_search_path == ("pg_catalog", "pg_temp")
        assert facts.executor_has_execute is True
        assert facts.public_has_execute is False
        assert facts.app_role_has_execute is False
        assert facts.unexpected_execute_grant_count == 0
        assert isinstance(
            facts.function_owner_update_acl,
            readiness.ErasureFunctionOwnerUpdateAclFacts,
        )
        assert facts.function_owner_update_acl.relation_tuple == ("erasure_requests",)
        assert facts.function_owner_update_acl.column_update_acl_count == 5
        assert facts.function_owner_update_acl.ordered_column_tuple == (
            "request_id",
            "status",
            "executed_by",
            "executed_at",
            "counts",
        )
        assert facts.function_owner_update_acl.grantable_acl_count == 0
        assert facts.function_owner_update_acl.table_update_privilege_count == 0
        assert isinstance(facts.guard_owner, readiness.Task7GuardOwnerProbeFacts)
        assert facts.guard_owner.role is not None
        assert facts.guard_owner.role.role_name == _GUARD_OWNER
        assert isinstance(facts.runner_contract, readiness.Task7ErasureRunnerProbeFacts)
        delete_relations = cast(tuple[str, ...], actual_owner["delete_relations"])
        current_relations = tuple(cast(list[str], _erasure_row(artifact)["current_relation_tuple"]))
        assert current_relations == readiness.TASK7_V21_CURRENT_ERASURE_RELATIONS
        assert tuple(sorted(current_relations)) == delete_relations
        assert facts.runner_contract.activation_state == "CURRENT48_ACTIVE_FUTURE38_FROZEN"
        assert facts.runner_contract.delete_relation_tuple == current_relations
        assert facts.runner_contract.result_relation_tuple == current_relations
        assert facts.runner_contract.future_relation_tuple_present == ()
        assert result.may_proceed is True, result.code
        assert result.code is None


@requires_operator_postgres(
    "IDIS_ERASURE_DATABASE_URL",
    failure_code=_V21_OPERATOR_POSTGRES_FAILURE,
)
def test_v21_function_precondition_atomic_terminal_and_crash_open_state() -> None:
    artifact, _fixture = _require_frozen_v21_runtime()
    contract = tuple(_erasure_row(artifact)["object_reconciliation_contract_tuple"])
    tenant_id, deal_id, request_id = (str(uuid.uuid4()) for _ in range(3))

    with _v19_database() as (database, executor):
        _seed_request(
            database,
            tenant_id=tenant_id,
            deal_id=deal_id,
            request_id=request_id,
        )
        before_attempt = _state(
            database,
            tenant_id=tenant_id,
            deal_id=deal_id,
            request_id=request_id,
        )
        with pytest.raises(DBAPIError) as missing_attempt:
            _invoke(
                executor,
                request_id=request_id,
                tenant_id=tenant_id,
                deal_id=deal_id,
            )
        assert _sqlstate(missing_attempt.value) == "23514"
        assert "TASK7_ERASURE_ATTEMPTED_EVENT_INVALID" in str(missing_attempt.value.orig)
        assert (
            _state(
                database,
                tenant_id=tenant_id,
                deal_id=deal_id,
                request_id=request_id,
            )
            == before_attempt
        )

        attempted = _persist_attempted_event(
            database,
            tenant_id=tenant_id,
            deal_id=deal_id,
            request_id=request_id,
        )
        before_app_direct = _state(
            database,
            tenant_id=tenant_id,
            deal_id=deal_id,
            request_id=request_id,
        )
        with pytest.raises(DBAPIError) as app_denied:
            _invoke(
                database.app_engine,
                request_id=request_id,
                tenant_id=tenant_id,
                deal_id=deal_id,
            )
        assert _sqlstate(app_denied.value) == "42501"
        assert (
            _state(
                database,
                tenant_id=tenant_id,
                deal_id=deal_id,
                request_id=request_id,
            )
            == before_app_direct
        )

        result = _invoke(
            executor,
            request_id=request_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
        )
        persisted, deal_count, events = _state(
            database,
            tenant_id=tenant_id,
            deal_id=deal_id,
            request_id=request_id,
        )

    assert result["status"] == "EXECUTED"
    assert result["executed_by"] == "task7-v19-operator-admin"
    assert result["counts"]["object_rows_reconciled"] == 0
    assert result["counts"]["deals"] == 1
    assert "objects_deleted" not in result["counts"]
    assert persisted[0] == "EXECUTED"
    assert persisted[1] == result["executed_by"]
    assert persisted[2] is not None
    assert persisted[3] == result["counts"]
    assert deal_count == 0
    assert events == ((attempted["event_id"], "task7.erasure.attempted"),)
    assert (
        "EXECUTED_OPEN_ATTEMPT_RECONSTRUCTS_SUCCEEDED_FROM_PERSISTED_COUNTS_AND_EXECUTED_AT"
        in contract
    )
