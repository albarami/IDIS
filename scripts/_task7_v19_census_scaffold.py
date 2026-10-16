"""Hash-pinned PostgreSQL scaffold for the unfrozen full-strict-v19 census.

The 73 current guard targets and four production constructors are unchanged
from v18.  Their setup mechanics are therefore delegated to the immutable v18
scaffold after a metadata-only projection.  The new v19 erasure function and
its ACL are decoded and installed from the v19 artifact itself.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from sqlalchemy.engine import Connection

_V18_SCAFFOLD_PATH: Final = Path(__file__).with_name("_task7_v18_census_scaffold.py")
_V18_SCAFFOLD_SHA256: Final = "f789c074b850f94f80d0740e7934c787a943739fe6fb008c3b54ea7c02bd5702"
_V19_ARTIFACT_SCHEMA: Final = "full-strict-v19-reviewed-preimage-v1"
_V19_CONTRACT_VERSION: Final = "full-strict-v19"
_V18_ARTIFACT_SCHEMA: Final = "full-strict-v18-reviewed-preimage-v1"
_V18_CONTRACT_VERSION: Final = "full-strict-v18"
_RUNNER_SIGNATURE: Final = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_READ_CLOSURE: Final = (
    "erasure_requests",
    "deterministic_calculations",
    "runs",
    "human_gates",
    "legal_holds",
    "document_artifacts",
    "audit_events",
)


def _load_pinned_v18_scaffold() -> ModuleType:
    raw = _V18_SCAFFOLD_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V18_SCAFFOLD_SHA256:
        raise RuntimeError("TASK7_V19_V18_CENSUS_SCAFFOLD_DRIFT")
    name = "_task7_v19_hash_pinned_v18_scaffold_core"
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
        origin=str(_V18_SCAFFOLD_PATH),
    )
    if spec is None:
        raise RuntimeError("TASK7_V19_V18_CENSUS_SCAFFOLD_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(_V18_SCAFFOLD_PATH)
    sys.modules[name] = module
    exec(compile(raw, str(_V18_SCAFFOLD_PATH), "exec"), module.__dict__)  # noqa: S102
    return module


_V18 = _load_pinned_v18_scaffold()
GUARD_OWNER_ROLE: Final = cast(str, _V18.GUARD_OWNER_ROLE)


class _LiteralSqlTransport:
    """Escape DBAPI percent metacharacters without changing server SQL."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def exec_driver_sql(
        self,
        statement: str,
        *multiparams: Any,
        **params: Any,
    ) -> Any:
        return self._connection.exec_driver_sql(
            statement.replace("%", "%%"),
            *multiparams,
            **params,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _project_metadata_to_v18(artifact: Mapping[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(dict(artifact))
    metadata = projected.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V19_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V19_CONTRACT_VERSION
        or metadata.get("append_frozen") is not False
    ):
        raise RuntimeError("TASK7_V19_CENSUS_ARTIFACT_VERSION_MISMATCH")
    replacement = dict(metadata)
    replacement["artifact_schema"] = _V18_ARTIFACT_SCHEMA
    replacement["pipeline_contract_version"] = _V18_CONTRACT_VERSION
    projected["metadata"] = replacement
    return projected


def _decode_sql_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not value:
        raise RuntimeError(f"TASK7_V19_ERASURE_SQL_INVALID:{field}")
    result: list[str] = []
    try:
        for encoded in value:
            if not isinstance(encoded, str) or not encoded or len(encoded) % 2:
                raise ValueError("invalid hex")
            statement = bytes.fromhex(encoded).decode("utf-8")
            if statement != " ".join(statement.split()):
                raise ValueError("noncanonical SQL")
            result.append(statement)
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError(f"TASK7_V19_ERASURE_SQL_INVALID:{field}") from exc
    return tuple(result)


def erasure_authority(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the one exact v19 erasure authority row."""

    _project_metadata_to_v18(artifact)
    try:
        section = artifact["erasure_execution_registry"]
        rows = section["rows"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("TASK7_V19_ERASURE_AUTHORITY_INVALID") from exc
    if (
        not isinstance(rows, Sequence)
        or isinstance(rows, (str, bytes, bytearray))
        or len(rows) != 1
        or not isinstance(rows[0], Mapping)
    ):
        raise RuntimeError("TASK7_V19_ERASURE_AUTHORITY_INVALID")
    row = cast(Mapping[str, Any], rows[0])
    if (
        row.get("execution_version") != "2"
        or row.get("runner_function_signature") != _RUNNER_SIGNATURE
        or tuple(row.get("declared_read_relation_tuple", ())) != _READ_CLOSURE
        or len(tuple(row.get("current_relation_tuple", ()))) != 48
    ):
        raise RuntimeError("TASK7_V19_ERASURE_AUTHORITY_INVALID")
    return row


def current_erasure_sql(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Decode the exact CURRENT48 function and ACL bytes."""

    row = erasure_authority(artifact)
    body = _decode_sql_tuple(
        row.get("current_body_sql_utf8_hex_tuple"),
        field="current_body_sql_utf8_hex_tuple",
    )
    acl = _decode_sql_tuple(
        row.get("current_acl_sql_utf8_hex_tuple"),
        field="current_acl_sql_utf8_hex_tuple",
    )
    if len(body) != 1 or len(acl) != 208:
        raise RuntimeError("TASK7_V19_ERASURE_AUTHORITY_INVALID")
    return body, acl


def install_current_erasure_authority(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    """Install only the reviewed v19 CURRENT48 runner and ACL."""

    transport = _LiteralSqlTransport(connection)
    body, acl = current_erasure_sql(artifact)
    for statement in (*body, *acl):
        transport.exec_driver_sql(statement)


def provision_guard_owner(connection: Connection) -> None:
    _V18.provision_guard_owner(connection)


def install_artifact_sql_scaffold(
    connection: Connection,
    artifact: Mapping[str, Any],
    *,
    application_role: str,
) -> None:
    _V18.install_artifact_sql_scaffold(
        connection,
        _project_metadata_to_v18(artifact),
        application_role=application_role,
    )


def catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    return cast(
        tuple[tuple[str, ...], ...],
        _V18.catalog_projection(connection, _project_metadata_to_v18(artifact)),
    )


def disable_all_current_user_triggers(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    _V18.disable_all_current_user_triggers(
        connection,
        _project_metadata_to_v18(artifact),
    )


def enable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    _V18.enable_target_trigger(connection, table_name, trigger_name)


def disable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    _V18.disable_target_trigger(connection, table_name, trigger_name)


def current_function_rows(
    artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    return cast(
        tuple[Mapping[str, Any], ...],
        _V18.current_function_rows(_project_metadata_to_v18(artifact)),
    )


__all__ = [
    "GUARD_OWNER_ROLE",
    "catalog_projection",
    "current_erasure_sql",
    "current_function_rows",
    "disable_all_current_user_triggers",
    "disable_target_trigger",
    "enable_target_trigger",
    "erasure_authority",
    "install_artifact_sql_scaffold",
    "install_current_erasure_authority",
    "provision_guard_owner",
]
