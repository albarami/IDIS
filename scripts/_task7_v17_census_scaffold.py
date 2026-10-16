"""Thin PostgreSQL scaffold adapter for the unfrozen full-strict-v17 census.

V17 changes only hash-bound current guard bytes and one existing trigger's
event set.  Schema, RLS, roles, target relations, and installation mechanics
remain the append-frozen v16 authority.  This module therefore loads the exact
v16 scaffold bytes and projects only v17 metadata to v16 while installing the
unaltered v17 SQL/ACL payload.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from sqlalchemy.engine import Connection

_V16_SCAFFOLD_SHA256: Final = "07da0191eca16bab01dbe15ad6fc9f6ad883fdc3106fc989eb7d233a4a6de7bb"
_V17_ARTIFACT_SCHEMA: Final = "full-strict-v17-reviewed-preimage-v1"
_V17_CONTRACT_VERSION: Final = "full-strict-v17"
_V16_ARTIFACT_SCHEMA: Final = "full-strict-v16-reviewed-preimage-v1"
_V16_CONTRACT_VERSION: Final = "full-strict-v16"


def _load_pinned_v16_scaffold() -> ModuleType:
    path = Path(__file__).with_name("_task7_v16_census_scaffold.py")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V16_SCAFFOLD_SHA256:
        raise RuntimeError("TASK7_V17_V16_CENSUS_SCAFFOLD_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V17_V16_CENSUS_SCAFFOLD_INVALID") from exc
    name = "_task7_v16_census_scaffold_core_for_v17"
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        raise RuntimeError("TASK7_V17_V16_CENSUS_SCAFFOLD_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


_V16 = _load_pinned_v16_scaffold()
GUARD_OWNER_ROLE: Final = _V16.GUARD_OWNER_ROLE


class _LiteralSqlTransport:
    """Escape DBAPI percent metacharacters without changing server-side SQL."""

    def __init__(self, connection: Any) -> None:
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


def _project_metadata_to_v16(artifact: Mapping[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(dict(artifact))
    metadata = projected.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V17_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V17_CONTRACT_VERSION
        or metadata.get("append_frozen") is not False
    ):
        raise RuntimeError("TASK7_V17_CENSUS_ARTIFACT_VERSION_MISMATCH")
    projected_metadata = dict(metadata)
    projected_metadata["artifact_schema"] = _V16_ARTIFACT_SCHEMA
    projected_metadata["pipeline_contract_version"] = _V16_CONTRACT_VERSION
    projected["metadata"] = projected_metadata
    return projected


def provision_guard_owner(connection: Connection) -> None:
    _V16.provision_guard_owner(connection)


def install_artifact_sql_scaffold(
    connection: Connection,
    artifact: Mapping[str, Any],
    *,
    application_role: str,
) -> None:
    """Install exact v17 SQL through the hash-pinned v16 schema/RLS mechanics."""

    _V16.install_artifact_sql_scaffold(
        _LiteralSqlTransport(connection),
        _project_metadata_to_v16(artifact),
        application_role=application_role,
    )


def catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    return cast(
        tuple[tuple[str, ...], ...],
        _V16.catalog_projection(connection, _project_metadata_to_v16(artifact)),
    )


def disable_all_current_user_triggers(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    _V16.disable_all_current_user_triggers(
        connection,
        _project_metadata_to_v16(artifact),
    )


def enable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    _V16.enable_target_trigger(connection, table_name, trigger_name)


def disable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    _V16.disable_target_trigger(connection, table_name, trigger_name)


def current_function_rows(
    artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    projected = _project_metadata_to_v16(artifact)
    registry = _V16._validated_registry(projected)
    rows = tuple(row for row in registry["function_rows"] if row["install_scope"] == "CURRENT_0038")
    signatures = tuple(str(row["function_signature"]) for row in rows)
    if not rows or len(signatures) != len(set(signatures)):
        raise RuntimeError("TASK7_V17_CURRENT_FUNCTION_INVENTORY_MISMATCH")
    return cast(tuple[Mapping[str, Any], ...], rows)


__all__ = [
    "GUARD_OWNER_ROLE",
    "catalog_projection",
    "current_function_rows",
    "disable_all_current_user_triggers",
    "disable_target_trigger",
    "enable_target_trigger",
    "install_artifact_sql_scaffold",
    "provision_guard_owner",
]
