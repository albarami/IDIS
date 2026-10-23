"""Frozen-v18 history and current-v21 authority contract for migration 0038.

The append-frozen v18 artifact remains immutable historical evidence, but it
is superseded and cannot be selected by migration 0038.  Every migration-facing
assertion below is bound to the atomically published v21 artifact and fixture.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V18_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V18_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v18_contract_hash_v1.json"
)
_CURRENT_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_CURRENT_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)
_MIGRATIONS_DIR = _REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions"
_V18 = "full-strict-v18"
_CURRENT_VERSION = "full-strict-v21"
_FIRST_RETAINED_RESTORE_SHA256 = "c589089618af8d486acb41f046f1bd6969cff9279e6abfd4bdaa4b006752655e"

_EXPECTED_CENSUS = {
    "current_routine_catalog_count": 24,
    "current_guard_target_count": 73,
    "current_guard_target_event_count": 193,
    "guard_owner_column_acl_relation_count": 36,
    "guard_owner_column_acl_count": 424,
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _v18_fixture() -> dict[str, Any]:
    assert _V18_FIXTURE_PATH.is_file(), (
        "immutable full-strict-v18 history requires its published signed fixture"
    )
    return _load_json(_V18_FIXTURE_PATH)


def _current_fixture() -> dict[str, Any]:
    assert _CURRENT_FIXTURE_PATH.is_file(), (
        "migration 0038 cannot consume full-strict-v21 until its signed census "
        "fixture has been atomically published"
    )
    fixture = _load_json(_CURRENT_FIXTURE_PATH)
    assert fixture["fixture_schema"] == "full-strict-v21-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == _CURRENT_VERSION
    return fixture


def _migration_path() -> Path:
    paths = tuple(sorted(_MIGRATIONS_DIR.glob("0038_*.py")))
    assert len(paths) == 1, "Task 7 requires exactly one linear migration 0038"
    return paths[0]


def _migration() -> ModuleType:
    path = _migration_path()
    name = "_task7_migration_0038_v18_authority_red"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _decoded(values: list[str]) -> tuple[str, ...]:
    return tuple(bytes.fromhex(value).decode("utf-8") for value in values)


def _authority() -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = _load_json(_CURRENT_ARTIFACT_PATH)
    return artifact, artifact["sql_authority_registry"]["guard_function_acl_registry"]


def _current_body(artifact: dict[str, Any]) -> tuple[str, ...]:
    rows = (
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if str(row["authority_id"]).startswith("current-")
    )
    return tuple(
        bytes.fromhex(encoded).decode("utf-8")
        for row in rows
        for encoded in row["body_sql_utf8_hex_tuple"]
    )


def test_frozen_v18_artifact_and_fixture_remain_immutable_history() -> None:
    fixture = _v18_fixture()
    artifact_raw = _V18_ARTIFACT_PATH.read_bytes()
    artifact = json.loads(artifact_raw)

    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v18-reviewed-preimage-v1",
        "pipeline_contract_version": _V18,
        "append_frozen": True,
    }
    assert fixture["fixture_schema"] == "full-strict-v18-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == _V18
    assert fixture["artifact_sha256"] == hashlib.sha256(artifact_raw).hexdigest()
    assert (
        fixture["component_hashes"]["pipeline_contract_hash"]
        == fixture["FULL_STRICT_V18_CONTRACT_HASH"]
    )


def test_0038_exposes_only_the_current_v21_contract_authority_surface() -> None:
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
    assert not hasattr(migration, "FULL_STRICT_V18_CONTRACT_HASH")
    assert not hasattr(migration, "_run_v18_guard_authority_preflight")
    assert not hasattr(migration, "_install_v18_current_guard_authority")
    assert not hasattr(migration, "_remove_v18_guard_and_erasure_authority")
    assert not hasattr(migration, "FULL_STRICT_V17_CONTRACT_HASH")
    assert not hasattr(migration, "_run_v17_guard_authority_preflight")
    assert not hasattr(migration, "_install_v17_current_guard_authority")
    assert not hasattr(migration, "_remove_v17_guard_and_erasure_authority")
    assert not hasattr(migration, "FULL_STRICT_V16_CONTRACT_HASH")
    assert not hasattr(migration, "_run_v16_guard_authority_preflight")
    assert not hasattr(migration, "_install_v16_current_guard_authority")
    assert not hasattr(migration, "_remove_v16_guard_and_erasure_authority")


def test_0038_identity_is_derived_from_the_published_v21_fixture() -> None:
    fixture = _current_fixture()
    migration = _migration()

    assert fixture["FULL_STRICT_V21_CONTRACT_HASH"] == migration.FULL_STRICT_V21_CONTRACT_HASH
    assert fixture["artifact_sha256"] == migration._REVIEWED_ARTIFACT_SHA256
    assert (
        hashlib.sha256(_CURRENT_FIXTURE_PATH.read_bytes()).hexdigest()
        == migration._REVIEWED_FIXTURE_SHA256
    )
    assert (
        fixture["component_hashes"]["task7_ddl_registry_hash"] == migration.TASK7_DDL_REGISTRY_HASH
    )
    assert (
        fixture["component_hashes"]["sql_authority_registry_hash"]
        == migration.TASK7_SQL_AUTHORITY_REGISTRY_HASH
    )


def test_0038_embeds_the_exact_v21_current_guard_acl_and_downgrade_bytes() -> None:
    artifact, acl = _authority()
    migration = _migration()
    downgrade = acl["downgrade_authority"]

    assert len(_current_body(artifact)) == 102
    assert _current_body(artifact) == migration.TASK7_CURRENT_GUARD_BODY_SQL_V1
    assert (
        _decoded(acl["installation_sql_utf8_hex_tuple"])
        == migration.TASK7_CURRENT_GUARD_ACL_INSTALL_SQL_V1
    )
    assert (
        _decoded(downgrade["ordered_sql_utf8_hex_tuple"])
        == migration.TASK7_CURRENT_GUARD_ACL_DOWNGRADE_SQL_V1
    )


def test_0038_census_matches_the_signed_fixture_and_artifact_projection() -> None:
    fixture = _current_fixture()
    artifact, acl = _authority()
    migration = _migration()
    current_rows = tuple(
        row for row in acl["function_rows"] if row["install_scope"] == "CURRENT_0038"
    )
    relation_columns = acl["guard_owner_role"]["select_relation_column_tuple"]
    census = fixture["postgres_authority_census"]

    derived = {
        "current_routine_catalog_count": len(current_rows),
        "current_guard_target_count": sum(len(row["target_trigger_tuple"]) for row in current_rows),
        "current_guard_target_event_count": sum(
            len(row["target_event_coordinate_tuple"]) for row in current_rows
        ),
        "guard_owner_column_acl_relation_count": len(relation_columns),
        "guard_owner_column_acl_count": sum(
            len(columns) for _relation, columns in relation_columns
        ),
    }
    assert derived == _EXPECTED_CENSUS
    assert {key: census[key] for key in _EXPECTED_CENSUS} == _EXPECTED_CENSUS
    assert (
        _EXPECTED_CENSUS["current_routine_catalog_count"]
        == migration.TASK7_CURRENT_GUARD_ROUTINE_COUNT
    )
    assert (
        _EXPECTED_CENSUS["current_guard_target_count"] == migration.TASK7_CURRENT_GUARD_TARGET_COUNT
    )
    assert (
        _EXPECTED_CENSUS["current_guard_target_event_count"]
        == migration.TASK7_CURRENT_GUARD_EVENT_COUNT
    )
    assert (
        _EXPECTED_CENSUS["guard_owner_column_acl_relation_count"]
        == migration.TASK7_GUARD_PARENT_RELATION_COUNT
    )
    assert (
        _EXPECTED_CENSUS["guard_owner_column_acl_count"]
        == migration.TASK7_GUARD_PARENT_COLUMN_GRANT_COUNT
    )
    assert (
        artifact["sql_authority_registry"]["expected_sha256"]
        == fixture["component_hashes"]["sql_authority_registry_hash"]
    )


def test_0038_downgrade_replays_all_eight_exact_retained_definitions() -> None:
    _artifact, acl = _authority()
    migration = _migration()
    downgrade = acl["downgrade_authority"]
    ordered = _decoded(downgrade["ordered_sql_utf8_hex_tuple"])
    retained_definitions = _decoded(downgrade["retained_function_restore_sql_utf8_hex_tuple"])

    assert len(ordered) == 190
    assert len(retained_definitions) == 8
    assert len(downgrade["retained_function_signature_tuple"]) == 8
    assert len(acl["retained_0037_catalog_projection_tuple"]) == 8
    assert ordered == migration.TASK7_CURRENT_GUARD_ACL_DOWNGRADE_SQL_V1
    assert all(definition in ordered for definition in retained_definitions)
    assert " LANGUAGE " in f" {retained_definitions[0]} "
    assert (
        hashlib.sha256(retained_definitions[0].encode("utf-8")).hexdigest()
        == _FIRST_RETAINED_RESTORE_SHA256
    )


def test_0038_authority_helpers_execute_exact_upgrade_downgrade_upgrade_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the migration helpers without substituting artifact bytes at runtime."""

    _artifact, acl = _authority()
    migration = _migration()
    install = _decoded(acl["installation_sql_utf8_hex_tuple"])
    body = migration.TASK7_CURRENT_GUARD_BODY_SQL_V1
    downgrade = acl["downgrade_authority"]
    down = _decoded(downgrade["ordered_sql_utf8_hex_tuple"])
    restore_offset = downgrade["ordered_sql_utf8_hex_tuple"].index(
        downgrade["zero_privilege_assertion_sql_utf8_hex"]
    )
    events: list[tuple[str, tuple[str, ...] | None]] = []

    monkeypatch.setattr(
        migration,
        "_install_current_erasure_authority",
        lambda: events.append(("erasure-up", None)),
    )
    monkeypatch.setattr(
        migration,
        "_remove_erasure_authority",
        lambda: events.append(("erasure-down", None)),
    )
    monkeypatch.setattr(
        migration,
        "_execute_authority_tuple",
        lambda statements: events.append(("sql", tuple(statements))),
    )

    migration._run_v21_guard_authority_preflight()
    migration._install_v21_current_guard_authority()
    migration._remove_v21_guard_and_erasure_authority()
    migration._run_v21_guard_authority_preflight()
    migration._install_v21_current_guard_authority()

    assert events == [
        ("sql", install[:3]),
        ("erasure-up", None),
        ("sql", body),
        ("sql", install[3:]),
        ("sql", down[:restore_offset]),
        ("erasure-down", None),
        ("sql", down[restore_offset:]),
        ("sql", install[:3]),
        ("erasure-up", None),
        ("sql", body),
        ("sql", install[3:]),
    ]


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar_one(self) -> bool:
        return self._value


class _CapturingBind:
    def __init__(self, *, evidence_exists: bool) -> None:
        self.evidence_exists = evidence_exists
        self.statements: list[str] = []

    def execute(self, statement: object) -> _ScalarResult:
        rendered = str(statement)
        self.statements.append(rendered)
        return _ScalarResult(self.evidence_exists and rendered.lstrip().startswith("SELECT"))


def test_0038_downgrade_refuses_v21_or_graph_evidence_and_never_selects_superseded_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration()
    bind = _CapturingBind(evidence_exists=True)
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    with pytest.raises(
        RuntimeError,
        match="cannot downgrade Task 7 while full-strict-v21 runtime evidence exists",
    ):
        migration.assert_downgrade_is_lossless()

    select = next(
        statement for statement in bind.statements if statement.lstrip().startswith("SELECT")
    )
    assert "pipeline_contract_version='full-strict-v21'" in select
    assert "full-strict-v20" not in select
    assert "full-strict-v18" not in select
    assert "full-strict-v17" not in select
    assert "full-strict-v16" not in select
    for relation in (
        "run_output_objects",
        "run_input_bindings",
        "run_step_outputs",
        "run_applicability_decisions",
        "run_expected_consumer_edges",
        "run_consumer_receipts",
        "deliverable_manifest_references",
    ):
        assert f"FROM {relation}" in select
