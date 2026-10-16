"""Hash-pinned PostgreSQL scaffold for the unfrozen full-strict-v20 census.

V20 changes only the erasure owner's column-limited SELECT closure.  The
73 current guard targets, four constructors, runner body, relation census,
and statement census are unchanged from v19, so their setup mechanics are
delegated after an exact metadata projection.  The v20 ACL bytes themselves
remain the authority installed by this module.
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

_V19_SCAFFOLD_PATH: Final = Path(__file__).with_name("_task7_v19_census_scaffold.py")
_V19_SCAFFOLD_SHA256: Final = "a7460f38928b52ed5f31b2838e28c0fbdab5d8134d38b67845da0a695e808b8f"
_V20_ARTIFACT_SCHEMA: Final = "full-strict-v20-reviewed-preimage-v1"
_V20_CONTRACT_VERSION: Final = "full-strict-v20"
_V19_ARTIFACT_SCHEMA: Final = "full-strict-v19-reviewed-preimage-v1"
_V19_CONTRACT_VERSION: Final = "full-strict-v19"


def _load_pinned_v19_scaffold() -> ModuleType:
    raw = _V19_SCAFFOLD_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V19_SCAFFOLD_SHA256:
        raise RuntimeError("TASK7_V20_V19_CENSUS_SCAFFOLD_DRIFT")
    name = "_task7_v20_hash_pinned_v19_scaffold_core"
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
        origin=str(_V19_SCAFFOLD_PATH),
    )
    if spec is None:
        raise RuntimeError("TASK7_V20_V19_CENSUS_SCAFFOLD_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(_V19_SCAFFOLD_PATH)
    sys.modules[name] = module
    exec(compile(raw, str(_V19_SCAFFOLD_PATH), "exec"), module.__dict__)  # noqa: S102
    return module


_V19 = _load_pinned_v19_scaffold()
GUARD_OWNER_ROLE: Final = cast(str, _V19.GUARD_OWNER_ROLE)


def _project_metadata_to_v19(artifact: Mapping[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(dict(artifact))
    metadata = projected.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _V20_ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _V20_CONTRACT_VERSION
        or metadata.get("append_frozen") is not False
    ):
        raise RuntimeError("TASK7_V20_CENSUS_ARTIFACT_VERSION_MISMATCH")
    replacement = dict(metadata)
    replacement["artifact_schema"] = _V19_ARTIFACT_SCHEMA
    replacement["pipeline_contract_version"] = _V19_CONTRACT_VERSION
    projected["metadata"] = replacement
    return projected


def erasure_authority(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    return cast(
        Mapping[str, Any],
        _V19.erasure_authority(_project_metadata_to_v19(artifact)),
    )


def current_erasure_sql(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return cast(
        tuple[tuple[str, ...], tuple[str, ...]],
        _V19.current_erasure_sql(_project_metadata_to_v19(artifact)),
    )


def install_current_erasure_authority(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    _V19.install_current_erasure_authority(
        connection,
        _project_metadata_to_v19(artifact),
    )


def provision_guard_owner(connection: Connection) -> None:
    _V19.provision_guard_owner(connection)


def install_artifact_sql_scaffold(
    connection: Connection,
    artifact: Mapping[str, Any],
    *,
    application_role: str,
) -> None:
    _V19.install_artifact_sql_scaffold(
        connection,
        _project_metadata_to_v19(artifact),
        application_role=application_role,
    )


def catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    return cast(
        tuple[tuple[str, ...], ...],
        _V19.catalog_projection(connection, _project_metadata_to_v19(artifact)),
    )


def disable_all_current_user_triggers(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    _V19.disable_all_current_user_triggers(
        connection,
        _project_metadata_to_v19(artifact),
    )


def enable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    _V19.enable_target_trigger(connection, table_name, trigger_name)


def disable_target_trigger(
    connection: Connection,
    table_name: str,
    trigger_name: str,
) -> None:
    _V19.disable_target_trigger(connection, table_name, trigger_name)


def current_function_rows(
    artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    return cast(
        tuple[Mapping[str, Any], ...],
        _V19.current_function_rows(_project_metadata_to_v19(artifact)),
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
