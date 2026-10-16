"""Hash-pinned PostgreSQL scaffold for the unfrozen full-strict-v21 census.

The 73 current guard targets, four constructors, runner body, relation census,
and all table privileges are unchanged from v20.  This adapter delegates those
mechanics after an exact metadata projection while preserving the v21 erasure
ACL bytes as the installed authority.
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

_V20_SCAFFOLD_PATH: Final = Path(__file__).with_name("_task7_v20_census_scaffold.py")
_V20_SCAFFOLD_SHA256: Final = "52c9d21718005f591b4de897864145a7a6322330b10d7ef45c290a993096b866"
_V21_ARTIFACT_SCHEMA: Final = "full-strict-v21-reviewed-preimage-v1"
_V21_CONTRACT_VERSION: Final = "full-strict-v21"
_V20_ARTIFACT_SCHEMA: Final = "full-strict-v20-reviewed-preimage-v1"
_V20_CONTRACT_VERSION: Final = "full-strict-v20"


def _load_pinned_v20_scaffold() -> ModuleType:
    raw = _V20_SCAFFOLD_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V20_SCAFFOLD_SHA256:
        raise RuntimeError("TASK7_V21_V20_CENSUS_SCAFFOLD_DRIFT")
    name = "_task7_v21_hash_pinned_v20_scaffold_core"
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
        origin=str(_V20_SCAFFOLD_PATH),
    )
    if spec is None:
        raise RuntimeError("TASK7_V21_V20_CENSUS_SCAFFOLD_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(_V20_SCAFFOLD_PATH)
    sys.modules[name] = module
    exec(compile(raw, str(_V20_SCAFFOLD_PATH), "exec"), module.__dict__)  # noqa: S102
    return module


_V20 = _load_pinned_v20_scaffold()
GUARD_OWNER_ROLE: Final = cast(str, _V20.GUARD_OWNER_ROLE)


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


def _decode_sql_tuple(value: object, *, field: str) -> tuple[str, ...]:
    """Decode one canonical, non-empty tuple of reviewed SQL bytes."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not value:
        raise RuntimeError(f"TASK7_V21_ERASURE_SQL_INVALID:{field}")
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
        raise RuntimeError(f"TASK7_V21_ERASURE_SQL_INVALID:{field}") from exc
    return tuple(result)


def _project_metadata_to_v20(artifact: Mapping[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(dict(artifact))
    metadata = projected.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V21_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V21_CONTRACT_VERSION
        or metadata.get("append_frozen") is not False
    ):
        raise RuntimeError("TASK7_V21_CENSUS_ARTIFACT_VERSION_MISMATCH")
    replacement = dict(metadata)
    replacement["artifact_schema"] = _V20_ARTIFACT_SCHEMA
    replacement["pipeline_contract_version"] = _V20_CONTRACT_VERSION
    projected["metadata"] = replacement
    return projected


def erasure_authority(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    return cast(
        Mapping[str, Any],
        _V20.erasure_authority(_project_metadata_to_v20(artifact)),
    )


def current_erasure_sql(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Decode the exact v21 CURRENT48 function and 209-statement ACL."""

    row = erasure_authority(artifact)
    body = _decode_sql_tuple(
        row.get("current_body_sql_utf8_hex_tuple"),
        field="current_body_sql_utf8_hex_tuple",
    )
    acl = _decode_sql_tuple(
        row.get("current_acl_sql_utf8_hex_tuple"),
        field="current_acl_sql_utf8_hex_tuple",
    )
    if len(body) != 1 or len(acl) != 209:
        raise RuntimeError("TASK7_V21_ERASURE_AUTHORITY_INVALID")
    return body, acl


def install_current_erasure_authority(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    """Install only the reviewed v21 CURRENT48 runner and ACL."""

    transport = _LiteralSqlTransport(connection)
    body, acl = current_erasure_sql(artifact)
    for statement in (*body, *acl):
        transport.exec_driver_sql(statement)


def provision_guard_owner(connection: Connection) -> None:
    _V20.provision_guard_owner(connection)


def install_artifact_sql_scaffold(
    connection: Connection,
    artifact: Mapping[str, Any],
    *,
    application_role: str,
) -> None:
    _V20.install_artifact_sql_scaffold(
        connection,
        _project_metadata_to_v20(artifact),
        application_role=application_role,
    )


def catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    return cast(
        tuple[tuple[str, ...], ...],
        _V20.catalog_projection(connection, _project_metadata_to_v20(artifact)),
    )


def disable_all_current_user_triggers(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    _V20.disable_all_current_user_triggers(
        connection,
        _project_metadata_to_v20(artifact),
    )


def enable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    _V20.enable_target_trigger(connection, table_name, trigger_name)


def disable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    _V20.disable_target_trigger(connection, table_name, trigger_name)


def current_function_rows(
    artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    return cast(
        tuple[Mapping[str, Any], ...],
        _V20.current_function_rows(_project_metadata_to_v20(artifact)),
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
