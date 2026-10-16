"""Application-role PostgreSQL scaffold for the unfrozen full-strict-v12 census.

The historical scaffold remains hash-pinned DDL machinery.  This adapter adds
only the v12 authority-owned concerns: the fixed guard-function owner, current
RLS installation for the generated application role, and the hash-bound
function/column ACL installation.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from sqlalchemy import text
from sqlalchemy.engine import Connection

GUARD_OWNER_ROLE: Final = "idis_task7_guard_function_owner"
_HISTORICAL_SCAFFOLD_SHA256: Final = (
    "1e1b40c3b1515aac23dae130e36a533bd846a64a1bc99a49ffef17e1a576811f"
)
_IDENTIFIER_RE: Final = re.compile(r"[a-z][a-z0-9_]*")
_CURRENT_SCOPE: Final = "CURRENT_0038"
_CREATE_POLICY_RE: Final = re.compile(
    r"CREATE POLICY (?P<policy>[a-z][a-z0-9_]*) "
    r"ON (?P<table>[a-z][a-z0-9_]*)\b"
)


def _identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise RuntimeError("TASK7_V12_CENSUS_IDENTIFIER_INVALID")
    return value


def verify_historical_scaffold_bytes(raw: bytes) -> None:
    """Fail closed if the reused v11 mechanics change."""

    if hashlib.sha256(raw).hexdigest() != _HISTORICAL_SCAFFOLD_SHA256:
        raise RuntimeError("TASK7_V12_HISTORICAL_CENSUS_SCAFFOLD_DRIFT")


def _load_historical_scaffold() -> ModuleType:
    path = Path(__file__).with_name("_task7_v11_census_scaffold.py")
    raw = path.read_bytes()
    verify_historical_scaffold_bytes(raw)
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V12_HISTORICAL_CENSUS_SCAFFOLD_INVALID") from exc
    spec = importlib.util.spec_from_loader(
        "_task7_v11_census_scaffold_core_for_v12",
        loader=None,
        origin=str(path),
    )
    if spec is None:
        raise RuntimeError("TASK7_V12_CENSUS_SCAFFOLD_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[spec.name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


_CORE = _load_historical_scaffold()


def _validated_registry(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = artifact.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != "full-strict-v12-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v12"
        or metadata.get("append_frozen") is not False
    ):
        raise RuntimeError("TASK7_V12_CENSUS_ARTIFACT_VERSION_MISMATCH")
    sql_registry = artifact.get("sql_authority_registry")
    if not isinstance(sql_registry, Mapping):
        raise RuntimeError("TASK7_V12_GUARD_ACL_REGISTRY_MISSING")
    registry = sql_registry.get("guard_function_acl_registry")
    if not isinstance(registry, Mapping):
        raise RuntimeError("TASK7_V12_GUARD_ACL_REGISTRY_MISSING")
    owner = registry.get("guard_owner_role")
    if not isinstance(owner, Mapping) or owner.get("role_name") != GUARD_OWNER_ROLE:
        raise RuntimeError("TASK7_V12_GUARD_OWNER_ROLE_MISMATCH")
    return registry


def _validated_core_projection(artifact: Mapping[str, Any]) -> dict[str, Any]:
    _validated_registry(artifact)
    projected = copy.deepcopy(dict(artifact))
    projected_metadata = dict(projected["metadata"])
    projected_metadata["artifact_schema"] = "full-strict-v11-reviewed-preimage-v1"
    projected_metadata["pipeline_contract_version"] = "full-strict-v11"
    projected["metadata"] = projected_metadata
    return projected


def provision_guard_owner(connection: Connection) -> None:
    """Provision the fixed cluster role without credentials or memberships."""

    connection.exec_driver_sql(
        f"""
        DO $task7$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
            WHERE rolname = '{GUARD_OWNER_ROLE}'
          ) THEN
            CREATE ROLE {GUARD_OWNER_ROLE}
              NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
        END
        $task7$;
        """
    )
    posture = connection.execute(
        text(
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolbypassrls,rolreplication "
            "FROM pg_catalog.pg_roles WHERE rolname=:role_name"
        ),
        {"role_name": GUARD_OWNER_ROLE},
    ).one()
    if tuple(posture) != (False, False, False, False, False, False, False):
        raise RuntimeError("TASK7_V12_GUARD_OWNER_POSTURE_INVALID")
    memberships = int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_auth_members m "
                "JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member "
                "JOIN pg_catalog.pg_roles granted_role ON granted_role.oid=m.roleid "
                "WHERE member_role.rolname=:role_name "
                "OR granted_role.rolname=:role_name"
            ),
            {"role_name": GUARD_OWNER_ROLE},
        ).scalar_one()
    )
    if memberships:
        raise RuntimeError("TASK7_V12_GUARD_OWNER_MEMBERSHIP_FORBIDDEN")


def _decode_sql_tuple(values: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(
        values,
        (str, bytes, bytearray),
    ):
        raise RuntimeError(f"TASK7_V12_GUARD_ACL_FIELD_INVALID:{field}")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise RuntimeError(f"TASK7_V12_GUARD_ACL_FIELD_INVALID:{field}")
        try:
            statement = bytes.fromhex(value).decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"TASK7_V12_GUARD_ACL_SQL_INVALID:{field}") from exc
        if not statement.strip():
            raise RuntimeError(f"TASK7_V12_GUARD_ACL_SQL_EMPTY:{field}")
        result.append(statement)
    return tuple(result)


def _install_current_rls(
    connection: Connection,
    artifact: Mapping[str, Any],
    *,
    application_role: str,
) -> None:
    role = _identifier(application_role)
    for row in artifact["current_ddl_registry"]["rows"]:
        for statement in row["rls_tuple"]:
            if not isinstance(statement, str):
                raise RuntimeError("TASK7_V12_CENSUS_RLS_SQL_INVALID")
            rendered = statement.replace("<configured_app_role>", role)
            if "<configured_app_role>" in rendered:
                raise RuntimeError("TASK7_V12_CENSUS_RLS_ROLE_UNRESOLVED")
            policy = _CREATE_POLICY_RE.match(rendered)
            if policy is not None:
                connection.exec_driver_sql(
                    "DROP POLICY IF EXISTS "
                    f"{_identifier(policy.group('policy'))} ON "
                    f"{_identifier(policy.group('table'))}"
                )
            connection.exec_driver_sql(rendered)


def _guard_acl_statements(artifact: Mapping[str, Any]) -> tuple[str, ...]:
    registry = _validated_registry(artifact)
    return _decode_sql_tuple(
        registry["installation_sql_utf8_hex_tuple"],
        field="installation_sql_utf8_hex_tuple",
    )


def _run_guard_acl_preflight(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    statements = _guard_acl_statements(artifact)
    if (
        len(statements) < 4
        or not all(statement.startswith("DO $$") for statement in statements[:3])
        or not statements[3].startswith("REVOKE ALL ON SCHEMA public")
    ):
        raise RuntimeError("TASK7_V12_GUARD_ACL_PREFLIGHT_ORDER_INVALID")
    for statement in statements[:3]:
        connection.exec_driver_sql(statement)


def _install_guard_acl(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    for statement in _guard_acl_statements(artifact)[3:]:
        connection.exec_driver_sql(statement)


def install_artifact_sql_scaffold(
    connection: Connection,
    artifact: Mapping[str, Any],
    *,
    application_role: str,
) -> None:
    """Install current artifact SQL, canonical RLS, and exact v12 ACL bytes."""

    provision_guard_owner(connection)
    _run_guard_acl_preflight(connection, artifact)
    _CORE.install_artifact_sql_scaffold(
        connection,
        _validated_core_projection(artifact),
    )
    _install_current_rls(
        connection,
        artifact,
        application_role=application_role,
    )
    _install_guard_acl(connection, artifact)


def catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    projected = _validated_core_projection(artifact)
    return cast(
        tuple[tuple[str, ...], ...],
        _CORE.catalog_projection(connection, projected),
    )


def disable_all_current_user_triggers(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    _CORE.disable_all_current_user_triggers(
        connection,
        _validated_core_projection(artifact),
    )


def enable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    _CORE.enable_target_trigger(connection, table_name, trigger_name)


def disable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    connection.exec_driver_sql(
        f"ALTER TABLE public.{_identifier(table_name)} DISABLE TRIGGER {_identifier(trigger_name)}"
    )


def current_function_rows(
    artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    registry = _validated_registry(artifact)
    rows = tuple(row for row in registry["function_rows"] if row["install_scope"] == _CURRENT_SCOPE)
    if len(rows) != 19:
        raise RuntimeError("TASK7_V12_CURRENT_FUNCTION_INVENTORY_MISMATCH")
    return rows


__all__ = [
    "GUARD_OWNER_ROLE",
    "catalog_projection",
    "current_function_rows",
    "disable_all_current_user_triggers",
    "disable_target_trigger",
    "enable_target_trigger",
    "install_artifact_sql_scaffold",
    "provision_guard_owner",
    "verify_historical_scaffold_bytes",
]
