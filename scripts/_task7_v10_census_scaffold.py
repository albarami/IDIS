"""Artifact-driven PostgreSQL scaffold for the Task 7 v10 pre-freeze census.

The schema-materialization mechanics are byte-identical to the reviewed v9
scaffold.  This adapter keeps that historical implementation immutable while
admitting only a v10 artifact.  Only the metadata value used by the historical
scaffold's entry guard is projected; every DDL and guard SQL byte still comes
from the supplied v10 artifact.
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

_HISTORICAL_SCAFFOLD_SHA256: Final = (
    "7ed00e544d3bc92803bd933c1e083829a92d62f6eddad82c594a10fe6a8f645c"
)


def verify_historical_scaffold_bytes(raw: bytes) -> None:
    """Fail closed unless the reused historical mechanics are exact."""

    if hashlib.sha256(raw).hexdigest() != _HISTORICAL_SCAFFOLD_SHA256:
        raise RuntimeError("TASK7_V10_HISTORICAL_CENSUS_SCAFFOLD_DRIFT")


def _load_historical_scaffold() -> ModuleType:
    path = Path(__file__).with_name("_task7_v9_census_scaffold.py")
    raw = path.read_bytes()
    verify_historical_scaffold_bytes(raw)
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V10_HISTORICAL_CENSUS_SCAFFOLD_INVALID") from exc
    spec = importlib.util.spec_from_loader(
        "_task7_v9_census_scaffold_core_for_v10",
        loader=None,
        origin=str(path),
    )
    if spec is None:
        raise RuntimeError("TASK7_V10_CENSUS_SCAFFOLD_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[spec.name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


_CORE = _load_historical_scaffold()


def _validated_core_projection(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Return the historical-entry projection without altering SQL bytes."""

    metadata = artifact.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != "full-strict-v10-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v10"
        or metadata.get("append_frozen") is not False
    ):
        raise RuntimeError("TASK7_V10_CENSUS_ARTIFACT_VERSION_MISMATCH")
    projected = copy.deepcopy(dict(artifact))
    projected_metadata = dict(projected["metadata"])
    projected_metadata["pipeline_contract_version"] = "full-strict-v9"
    projected["metadata"] = projected_metadata
    return projected


def install_artifact_sql_scaffold(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    """Install exact v10 DDL/guard bytes over a disposable 0037 database."""

    _CORE.install_artifact_sql_scaffold(
        connection,
        _validated_core_projection(artifact),
    )


def catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    """Return the deterministic catalog projection of exact v10 bytes."""

    _validated_core_projection(artifact)
    return cast(
        tuple[tuple[str, ...], ...],
        _CORE.catalog_projection(connection, artifact),
    )


def disable_all_current_user_triggers(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    """Disable the 73 reviewed v10 triggers inside the caller transaction."""

    _validated_core_projection(artifact)
    _CORE.disable_all_current_user_triggers(connection, artifact)


def enable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    """Enable one verified current target trigger."""

    _CORE.enable_target_trigger(connection, table_name, trigger_name)


__all__ = [
    "catalog_projection",
    "disable_all_current_user_triggers",
    "enable_target_trigger",
    "install_artifact_sql_scaffold",
    "verify_historical_scaffold_bytes",
]
