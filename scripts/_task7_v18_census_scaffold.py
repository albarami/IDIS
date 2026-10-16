"""Hash-pinned v18 adapter over the immutable v17 PostgreSQL scaffold."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from sqlalchemy.engine import Connection

_V17_SCAFFOLD_PATH: Final = Path(__file__).with_name("_task7_v17_census_scaffold.py")
_V17_SCAFFOLD_SHA256: Final = "2cde9e1927defc64e2a19924371c9d678c2f8fc0b9e2adb54d77ab84bf867157"


def _load_pinned_v17_scaffold() -> ModuleType:
    raw = _V17_SCAFFOLD_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _V17_SCAFFOLD_SHA256:
        raise RuntimeError("TASK7_V18_V17_CENSUS_SCAFFOLD_DRIFT")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V18_V17_CENSUS_SCAFFOLD_INVALID") from exc
    source = source.replace("V17", "V18").replace("v17", "v18")
    name = "_task7_v18_hash_pinned_v17_scaffold_core"
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
        origin=str(_V17_SCAFFOLD_PATH),
    )
    if spec is None:
        raise RuntimeError("TASK7_V18_V17_CENSUS_SCAFFOLD_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(Path(__file__).resolve())
    sys.modules[name] = module
    exec(compile(source, str(_V17_SCAFFOLD_PATH), "exec"), module.__dict__)  # noqa: S102
    return module


_CORE = _load_pinned_v17_scaffold()
GUARD_OWNER_ROLE: Final = _CORE.GUARD_OWNER_ROLE


def provision_guard_owner(connection: Connection) -> None:
    _CORE.provision_guard_owner(connection)


def install_artifact_sql_scaffold(
    connection: Connection,
    artifact: Mapping[str, Any],
    *,
    application_role: str,
) -> None:
    _CORE.install_artifact_sql_scaffold(
        connection,
        artifact,
        application_role=application_role,
    )


def catalog_projection(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    return cast(tuple[tuple[str, ...], ...], _CORE.catalog_projection(connection, artifact))


def disable_all_current_user_triggers(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    _CORE.disable_all_current_user_triggers(connection, artifact)


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
    _CORE.disable_target_trigger(connection, table_name, trigger_name)


def current_function_rows(
    artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    return cast(tuple[Mapping[str, Any], ...], _CORE.current_function_rows(artifact))


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
