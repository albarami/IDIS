"""Void-v19 history and current-v21 authority contract for migration 0038.

Full-strict-v19 was never signed, frozen, fixture-published, migration-consumed,
or runtime-selected.  Its exact unfrozen candidate bytes remain historical
evidence only; every migration-facing assertion is bound to frozen v21.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from idis import contracts
from idis.pipeline import contract_registry

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V19_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_V19_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v19_contract_hash_v1.json"
)
_V21_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_V21_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)
_MIGRATIONS_DIR = _REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions"
_V19_CANDIDATE_SHA256 = "21bb93882a2d316bbf899cdae282c231b99a1aeea2239c2966954bfa7f4789c4"
_V21 = "full-strict-v21"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _require_frozen_v21() -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_raw = _V21_ARTIFACT_PATH.read_bytes()
    fixture_raw = _V21_FIXTURE_PATH.read_bytes()
    artifact = _load_json(_V21_ARTIFACT_PATH)
    fixture = _load_json(_V21_FIXTURE_PATH)

    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v21-reviewed-preimage-v1",
        "pipeline_contract_version": _V21,
        "append_frozen": True,
    }
    assert fixture["fixture_schema"] == "full-strict-v21-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == _V21
    assert fixture["artifact_sha256"] == hashlib.sha256(artifact_raw).hexdigest()
    assert _SHA256_RE.fullmatch(hashlib.sha256(fixture_raw).hexdigest())
    return artifact, fixture


def _migration() -> ModuleType:
    paths = tuple(sorted(_MIGRATIONS_DIR.glob("0038_*.py")))
    assert len(paths) == 1, "Task 7 requires exactly one linear migration 0038"
    name = "_task7_migration_0038_v19_void_history_red"
    spec = importlib.util.spec_from_file_location(name, paths[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _rows(artifact: Mapping[str, Any], section_name: str) -> tuple[Mapping[str, Any], ...]:
    section = artifact[section_name]
    assert isinstance(section, Mapping)
    rows = section["rows"]
    assert isinstance(rows, list)
    assert all(isinstance(row, Mapping) for row in rows)
    return tuple(cast(Mapping[str, Any], row) for row in rows)


def _acl_registry(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    sql = artifact["sql_authority_registry"]
    assert isinstance(sql, Mapping)
    acl = sql["guard_function_acl_registry"]
    assert isinstance(acl, Mapping)
    return cast(Mapping[str, Any], acl)


def _erasure_row(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = _rows(artifact, "erasure_execution_registry")
    assert len(rows) == 1
    return rows[0]


def _decode_hex_tuple(value: object) -> tuple[str, ...]:
    assert isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    assert all(isinstance(item, str) for item in value)
    return tuple(bytes.fromhex(cast(str, item)).decode("utf-8") for item in value)


def _current_guard_body(artifact: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        bytes.fromhex(cast(str, encoded)).decode("utf-8")
        for row in _rows(artifact, "sql_authority_registry")
        if str(row["authority_id"]).startswith("current-")
        for encoded in cast(list[object], row["body_sql_utf8_hex_tuple"])
    )


def test_v19_candidate_is_exact_unfrozen_void_history_without_fixture() -> None:
    raw = _V19_ARTIFACT_PATH.read_bytes()
    artifact = _load_json(_V19_ARTIFACT_PATH)

    assert hashlib.sha256(raw).hexdigest() == _V19_CANDIDATE_SHA256
    assert artifact["metadata"]["artifact_schema"] == "full-strict-v19-reviewed-preimage-v1"
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v19"
    assert artifact["metadata"]["append_frozen"] is False
    assert not _V19_FIXTURE_PATH.exists()


def test_v19_never_enters_contract_lock_or_runtime_registry() -> None:
    generated_files = contracts.build_lock_document(_REPO_ROOT)["files"]
    committed_files = _load_json(_REPO_ROOT / contracts.CONTRACT_LOCK_RELATIVE_PATH)["files"]
    v19_relative = _V19_ARTIFACT_PATH.relative_to(_REPO_ROOT).as_posix()

    assert v19_relative not in generated_files
    assert v19_relative not in committed_files
    assert "full-strict-v19" not in contract_registry.PIPELINE_CONTRACTS


def test_0038_exposes_v21_and_no_void_v19_authority_names() -> None:
    _require_frozen_v21()
    migration = _migration()

    assert migration.revision == "0038"
    assert migration.down_revision == "0037"
    assert migration.FULL_STRICT_V21_ACTIVATION_STATE == "INACTIVE"
    assert callable(migration._run_v21_guard_authority_preflight)
    assert callable(migration._install_v21_current_guard_authority)
    assert callable(migration._remove_v21_guard_and_erasure_authority)
    assert not hasattr(migration, "FULL_STRICT_V20_CONTRACT_HASH")
    assert not hasattr(migration, "_run_v20_guard_authority_preflight")
    assert not hasattr(migration, "_install_v20_current_guard_authority")
    assert not hasattr(migration, "_remove_v20_guard_and_erasure_authority")
    assert not hasattr(migration, "FULL_STRICT_V19_CONTRACT_HASH")
    assert not hasattr(migration, "_run_v19_guard_authority_preflight")
    assert not hasattr(migration, "_install_v19_current_guard_authority")
    assert not hasattr(migration, "_remove_v19_guard_and_erasure_authority")


def test_0038_identity_and_component_hashes_come_only_from_v21_fixture() -> None:
    _artifact, fixture = _require_frozen_v21()
    migration = _migration()

    assert fixture["FULL_STRICT_V21_CONTRACT_HASH"] == migration.FULL_STRICT_V21_CONTRACT_HASH
    assert fixture["artifact_sha256"] == migration._REVIEWED_ARTIFACT_SHA256
    assert (
        hashlib.sha256(_V21_FIXTURE_PATH.read_bytes()).hexdigest()
        == migration._REVIEWED_FIXTURE_SHA256
    )
    assert (
        fixture["component_hashes"]["task7_ddl_registry_hash"] == migration.TASK7_DDL_REGISTRY_HASH
    )
    assert (
        fixture["component_hashes"]["sql_authority_registry_hash"]
        == migration.TASK7_SQL_AUTHORITY_REGISTRY_HASH
    )


def test_0038_current_guard_bytes_come_from_frozen_v21() -> None:
    v21, _fixture = _require_frozen_v21()
    migration = _migration()
    v21_acl = _acl_registry(v21)
    v21_downgrade = cast(Mapping[str, Any], v21_acl["downgrade_authority"])

    assert _current_guard_body(v21) == migration.TASK7_CURRENT_GUARD_BODY_SQL_V1
    assert (
        _decode_hex_tuple(v21_acl["installation_sql_utf8_hex_tuple"])
        == migration.TASK7_CURRENT_GUARD_ACL_INSTALL_SQL_V1
    )
    assert (
        _decode_hex_tuple(v21_downgrade["ordered_sql_utf8_hex_tuple"])
        == migration.TASK7_CURRENT_GUARD_ACL_DOWNGRADE_SQL_V1
    )


def test_0038_current_erasure_bytes_are_v21_and_not_void_v19() -> None:
    v19 = _load_json(_V19_ARTIFACT_PATH)
    v21, _fixture = _require_frozen_v21()
    migration = _migration()

    assert (
        _decode_hex_tuple(_erasure_row(v21)["current_body_sql_utf8_hex_tuple"])
        == migration._CURRENT_ERASURE_BODY_SQL_V1
    )
    assert (
        _decode_hex_tuple(_erasure_row(v21)["current_acl_sql_utf8_hex_tuple"])
        == migration._CURRENT_ERASURE_ACL_SQL_V1
    )
    assert (
        _decode_hex_tuple(_erasure_row(v19)["current_acl_sql_utf8_hex_tuple"])
        != migration._CURRENT_ERASURE_ACL_SQL_V1
    )


def test_0038_uses_only_the_v21_signed_application_role_census() -> None:
    artifact, fixture = _require_frozen_v21()
    census = fixture["postgres_authority_census"]
    acl = _acl_registry(artifact)
    current_rows = tuple(
        row
        for row in cast(list[Mapping[str, Any]], acl["function_rows"])
        if row["install_scope"] == "CURRENT_0038"
    )

    assert census["admin_case_execution_count"] == 0
    assert census["application_case_execution_count"] == 219
    assert census["case_outcome_count"] == 219
    assert census["current_routine_catalog_count"] == len(current_rows)
    assert _SHA256_RE.fullmatch(census["complete_execution_sha256"])


def test_0038_helpers_are_v21_only() -> None:
    migration = _migration()

    assert callable(migration._run_v21_guard_authority_preflight)
    assert callable(migration._install_v21_current_guard_authority)
    assert callable(migration._remove_v21_guard_and_erasure_authority)
    assert not hasattr(migration, "_run_v20_guard_authority_preflight")
    assert not hasattr(migration, "_install_v20_current_guard_authority")
    assert not hasattr(migration, "_remove_v20_guard_and_erasure_authority")
    assert not hasattr(migration, "_run_v19_guard_authority_preflight")
    assert not hasattr(migration, "_install_v19_current_guard_authority")
    assert not hasattr(migration, "_remove_v19_guard_and_erasure_authority")


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar_one(self) -> bool:
        return self._value


class _CapturingBind:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object) -> _ScalarResult:
        rendered = str(statement)
        self.statements.append(rendered)
        return _ScalarResult(rendered.lstrip().startswith("SELECT"))


def test_0038_downgrade_refuses_v21_evidence_and_never_selects_void_v19(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration()
    bind = _CapturingBind()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    with pytest.raises(
        RuntimeError,
        match="cannot downgrade Task 7 while full-strict-v21 runtime evidence exists",
    ):
        migration.assert_downgrade_is_lossless()

    assert len(bind.statements) == 2
    lock, select = bind.statements
    assert lock.lstrip().startswith("LOCK TABLE")
    assert select.lstrip().startswith("SELECT")
    assert "pipeline_contract_version='full-strict-v21'" in select
    assert "full-strict-v20" not in select
    assert "full-strict-v19" not in select
