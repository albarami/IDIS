"""Frozen-v20 history and current-v21 admission for migration 0038.

The v20 artifact/fixture remain immutable historical evidence. Migration 0038
consumes only the atomically published v21 authority, while behavioral proof
execution remains outside the admin role.
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

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v20_contract_hash_v1.json"
)
_CURRENT_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_CURRENT_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)
_MIGRATIONS_DIR = _REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions"
_VERSION = "full-strict-v20"
_CURRENT_VERSION = "full-strict-v21"
_RUNNER_SIGNATURE = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_OWNER = "idis_erasure_function_owner"
_EXECUTOR = "idis_erasure_executor"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

_SELECT_AUDIT = (
    "GRANT SELECT (event_id,tenant_id,event_type,event) ON TABLE "
    "public.audit_events TO idis_erasure_function_owner"
)
_SELECT_REQUEST = (
    "GRANT SELECT (request_id,tenant_id,deal_id,status,executed_by,executed_at,counts) "
    "ON TABLE public.erasure_requests TO idis_erasure_function_owner"
)
_UPDATE_REQUEST = (
    "GRANT UPDATE (request_id,status,executed_by,executed_at,counts) ON TABLE "
    "public.erasure_requests TO idis_erasure_function_owner"
)
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


def _require_frozen_v20() -> tuple[dict[str, Any], dict[str, Any]]:
    missing = tuple(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_ARTIFACT_PATH, _FIXTURE_PATH)
        if not path.is_file()
    )
    assert not missing, "TASK7_V20_MIGRATION_AUTHORITY_NOT_PUBLISHED:" + ",".join(missing)
    artifact_raw = _ARTIFACT_PATH.read_bytes()
    fixture_raw = _FIXTURE_PATH.read_bytes()
    artifact = _load_json(_ARTIFACT_PATH)
    fixture = _load_json(_FIXTURE_PATH)
    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v20-reviewed-preimage-v1",
        "pipeline_contract_version": _VERSION,
        "append_frozen": True,
    }
    assert fixture["fixture_schema"] == "full-strict-v20-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == _VERSION
    assert fixture["artifact_sha256"] == hashlib.sha256(artifact_raw).hexdigest()
    assert (
        fixture["component_hashes"]["pipeline_contract_hash"]
        == fixture["FULL_STRICT_V20_CONTRACT_HASH"]
    )
    assert _SHA256_RE.fullmatch(hashlib.sha256(fixture_raw).hexdigest())
    return artifact, fixture


def _require_frozen_v21() -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_raw = _CURRENT_ARTIFACT_PATH.read_bytes()
    fixture_raw = _CURRENT_FIXTURE_PATH.read_bytes()
    artifact = _load_json(_CURRENT_ARTIFACT_PATH)
    fixture = _load_json(_CURRENT_FIXTURE_PATH)
    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v21-reviewed-preimage-v1",
        "pipeline_contract_version": _CURRENT_VERSION,
        "append_frozen": True,
    }
    assert fixture["fixture_schema"] == "full-strict-v21-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == _CURRENT_VERSION
    assert fixture["artifact_sha256"] == hashlib.sha256(artifact_raw).hexdigest()
    assert (
        fixture["component_hashes"]["pipeline_contract_hash"]
        == fixture["FULL_STRICT_V21_CONTRACT_HASH"]
    )
    assert _SHA256_RE.fullmatch(hashlib.sha256(fixture_raw).hexdigest())
    return artifact, fixture


def _migration() -> ModuleType:
    paths = tuple(sorted(_MIGRATIONS_DIR.glob("0038_*.py")))
    assert len(paths) == 1, "Task 7 requires exactly one linear migration 0038"
    name = "_task7_migration_0038_v20_authority_red"
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


def _erasure_row(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = _rows(artifact, "erasure_execution_registry")
    assert len(rows) == 1
    return rows[0]


def _acl_registry(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    sql = artifact["sql_authority_registry"]
    assert isinstance(sql, Mapping)
    acl = sql["guard_function_acl_registry"]
    assert isinstance(acl, Mapping)
    return cast(Mapping[str, Any], acl)


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


def _compact_sql(statements: Sequence[str]) -> str:
    return "".join(" ".join(statements).casefold().split())


def test_0038_v20_requires_one_append_frozen_artifact_and_signed_fixture() -> None:
    artifact, fixture = _require_frozen_v20()

    assert fixture["activation_assertions"] == artifact["activation_assertions"]
    assert fixture["FULL_STRICT_V20_CONTRACT_HASH"] == (
        "0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178"
    )
    assert hashlib.sha256(_ARTIFACT_PATH.read_bytes()).hexdigest() == (
        "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"
    )
    assert hashlib.sha256(_FIXTURE_PATH.read_bytes()).hexdigest() == (
        "b8ba48b067a52ab5015ae1edca4943597670e1e2b828ccb640fccbda1b1207e3"
    )


def test_0038_exposes_v21_and_no_superseded_runtime_authority_names() -> None:
    _require_frozen_v21()
    migration = _migration()

    assert migration.revision == "0038"
    assert migration.down_revision == "0037"
    assert migration.FULL_STRICT_V21_ACTIVATION_STATE == "INACTIVE"
    assert callable(migration._run_v21_guard_authority_preflight)
    assert callable(migration._install_v21_current_guard_authority)
    assert callable(migration._remove_v21_guard_and_erasure_authority)
    assert not hasattr(migration, "_SUPERSEDED_V18_CURRENT_GUARD_BODY_SQL_V1")
    assert not hasattr(migration, "_SUPERSEDED_V18_CURRENT_ERASURE_BODY_SQL_V1")
    assert not hasattr(migration, "_SUPERSEDED_V18_CURRENT_ERASURE_ACL_SQL_V1")
    for version in ("V20", "V19", "V18", "V17", "V16"):
        assert not hasattr(migration, f"FULL_STRICT_{version}_CONTRACT_HASH")
        assert not hasattr(migration, f"_run_{version.casefold()}_guard_authority_preflight")
        assert not hasattr(migration, f"_install_{version.casefold()}_current_guard_authority")
        assert not hasattr(
            migration,
            f"_remove_{version.casefold()}_guard_and_erasure_authority",
        )


def test_0038_identity_and_component_hashes_come_only_from_v21_fixture() -> None:
    _artifact, fixture = _require_frozen_v21()
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


def test_0038_embeds_exact_v21_guard_erasure_acl_and_downgrade_bytes() -> None:
    artifact, _fixture = _require_frozen_v21()
    migration = _migration()
    acl = _acl_registry(artifact)
    erasure = _erasure_row(artifact)
    downgrade = acl["downgrade_authority"]
    assert isinstance(downgrade, Mapping)

    body = _current_guard_body(artifact)
    install = _decode_hex_tuple(acl["installation_sql_utf8_hex_tuple"])
    down = _decode_hex_tuple(downgrade["ordered_sql_utf8_hex_tuple"])
    erasure_body = _decode_hex_tuple(erasure["current_body_sql_utf8_hex_tuple"])
    erasure_acl = _decode_hex_tuple(erasure["current_acl_sql_utf8_hex_tuple"])

    assert (len(body), len(install), len(down), len(erasure_body), len(erasure_acl)) == (
        102,
        213,
        190,
        1,
        209,
    )
    assert body == migration.TASK7_CURRENT_GUARD_BODY_SQL_V1
    assert install == migration.TASK7_CURRENT_GUARD_ACL_INSTALL_SQL_V1
    assert down == migration.TASK7_CURRENT_GUARD_ACL_DOWNGRADE_SQL_V1
    assert erasure_body == migration._CURRENT_ERASURE_BODY_SQL_V1
    assert erasure_acl == migration._CURRENT_ERASURE_ACL_SQL_V1
    assert (
        _decode_hex_tuple(erasure["legacy_guard_upgrade_sql_utf8_hex_tuple"])
        == migration._LEGACY_GUARD_UPGRADE_SQL_V1
    )
    assert (
        _decode_hex_tuple(erasure["legacy_guard_downgrade_sql_utf8_hex_tuple"])
        == migration._LEGACY_GUARD_DOWNGRADE_SQL_V1
    )
    assert (
        _decode_hex_tuple(erasure["hold_lock_upgrade_sql_utf8_hex_tuple"])
        == migration._HOLD_LOCK_UPGRADE_SQL_V1
    )
    assert (
        _decode_hex_tuple(erasure["hold_lock_downgrade_sql_utf8_hex_tuple"])
        == migration._HOLD_LOCK_DOWNGRADE_SQL_V1
    )
    assert tuple(erasure["current_relation_tuple"]) == migration._CURRENT_ERASURE_RELATIONS_V1


def test_0038_v21_census_constants_match_the_signed_application_role_proof() -> None:
    artifact, fixture = _require_frozen_v21()
    migration = _migration()
    acl = _acl_registry(artifact)
    current_rows = tuple(
        row
        for row in cast(list[Mapping[str, Any]], acl["function_rows"])
        if row["install_scope"] == "CURRENT_0038"
    )
    relation_columns = cast(
        list[tuple[str, list[str]]],
        cast(Mapping[str, Any], acl["guard_owner_role"])["select_relation_column_tuple"],
    )
    census = fixture["postgres_authority_census"]
    derived = {
        "current_routine_catalog_count": len(current_rows),
        "current_guard_target_count": sum(
            len(cast(list[object], row["target_trigger_tuple"])) for row in current_rows
        ),
        "current_guard_target_event_count": sum(
            len(cast(list[object], row["target_event_coordinate_tuple"])) for row in current_rows
        ),
        "guard_owner_column_acl_relation_count": len(relation_columns),
        "guard_owner_column_acl_count": sum(
            len(columns) for _relation, columns in relation_columns
        ),
    }

    assert derived == _EXPECTED_CENSUS
    assert {key: census[key] for key in _EXPECTED_CENSUS} == _EXPECTED_CENSUS
    assert census["admin_case_execution_count"] == 0
    assert census["application_case_execution_count"] == 219
    assert census["case_outcome_count"] == 219
    assert census["legal_execution_count"] == 73
    assert census["illegal_rejection_count"] == 73
    assert census["direct_sql_rejection_count"] == 73
    assert census["application_direct_helper_execute_count"] == 0
    assert census["public_direct_helper_execute_count"] == 0
    assert _SHA256_RE.fullmatch(census["erasure_authority_execution_proof_sha256"])
    assert migration.TASK7_CURRENT_GUARD_ROUTINE_COUNT == 24
    assert migration.TASK7_CURRENT_GUARD_TARGET_COUNT == 73
    assert migration.TASK7_CURRENT_GUARD_EVENT_COUNT == 193
    assert migration.TASK7_GUARD_PARENT_RELATION_COUNT == 36
    assert migration.TASK7_GUARD_PARENT_COLUMN_GRANT_COUNT == 424


def test_0038_v21_erasure_acl_is_column_limited_and_has_no_app_delete_path() -> None:
    artifact, _fixture = _require_frozen_v21()
    migration = _migration()
    erasure = _erasure_row(artifact)
    statements = _decode_hex_tuple(erasure["current_acl_sql_utf8_hex_tuple"])
    grants = tuple(statement for statement in statements if statement.startswith("GRANT "))
    current_relations = tuple(cast(list[str], erasure["current_relation_tuple"]))
    delete_grants = tuple(
        statement for statement in grants if statement.startswith("GRANT DELETE ON TABLE public.")
    )

    assert _SELECT_AUDIT in statements
    assert _SELECT_REQUEST in statements
    assert _UPDATE_REQUEST in statements
    assert (
        tuple(
            statement.removeprefix("GRANT DELETE ON TABLE public.").removesuffix(f" TO {_OWNER}")
            for statement in delete_grants
        )
        == current_relations
    )
    assert tuple(statement for statement in grants if statement.startswith("GRANT UPDATE ")) == (
        _UPDATE_REQUEST,
    )
    assert not any(statement.startswith("GRANT INSERT ") for statement in grants)
    assert not any(" TO PUBLIC" in statement for statement in grants)
    assert not any(" TO idis_app" in statement for statement in grants)
    assert f"GRANT EXECUTE ON FUNCTION {_RUNNER_SIGNATURE} TO {_EXECUTOR}" in statements
    assert f"REVOKE ALL ON FUNCTION {_RUNNER_SIGNATURE} FROM PUBLIC" in statements
    assert statements == migration._CURRENT_ERASURE_ACL_SQL_V1


def test_0038_v21_runner_requires_attempted_evidence_and_persists_terminal_state() -> None:
    artifact, _fixture = _require_frozen_v21()
    migration = _migration()
    body = _decode_hex_tuple(_erasure_row(artifact)["current_body_sql_utf8_hex_tuple"])
    compact = _compact_sql(body)

    assert body == migration._CURRENT_ERASURE_BODY_SQL_V1
    assert compact.count("frompublic.audit_events") == 1
    assert "'task7.erasure.attempted'" in compact
    assert "'attempted'" in compact
    assert "object_rows_reconciled" in compact
    assert "objects_deleted" not in compact
    assert "status='executed'" in compact
    assert "executed_by=" in compact
    assert "executed_at=pg_catalog.statement_timestamp()" in compact
    assert "counts=" in compact
    assert "updatepublic.erasure_requests" in compact
    assert "returningstatus,executed_by,executed_at,counts" in compact
    assert compact.index("frompublic.audit_events") < compact.index("deletefrompublic.")
    assert compact.rindex("deletefrompublic.") < compact.index("updatepublic.erasure_requests")
    assert compact.index("updatepublic.erasure_requests") < compact.rindex("returning")


def test_0038_v21_helpers_preserve_exact_upgrade_downgrade_upgrade_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, _fixture = _require_frozen_v21()
    migration = _migration()
    acl = _acl_registry(artifact)
    install = _decode_hex_tuple(acl["installation_sql_utf8_hex_tuple"])
    downgrade = acl["downgrade_authority"]
    assert isinstance(downgrade, Mapping)
    down = _decode_hex_tuple(downgrade["ordered_sql_utf8_hex_tuple"])
    restore_offset = cast(list[str], downgrade["ordered_sql_utf8_hex_tuple"]).index(
        cast(str, downgrade["zero_privilege_assertion_sql_utf8_hex"])
    )
    body = migration.TASK7_CURRENT_GUARD_BODY_SQL_V1
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


def test_0038_v21_downgrade_revokes_the_complete_non_deletion_acl_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration()
    executed: list[str] = []
    monkeypatch.setattr(migration, "_assert_erasure_roles_ready", lambda: None)
    monkeypatch.setattr(migration, "_execute_authority_tuple", lambda _statements: None)
    monkeypatch.setattr(migration.op, "execute", lambda statement: executed.append(str(statement)))

    migration._remove_erasure_authority()

    for relation in ("erasure_requests", "legal_holds", "document_artifacts", "audit_events"):
        assert (
            f"REVOKE ALL PRIVILEGES ON TABLE public.{relation} FROM idis_erasure_function_owner"
        ) in executed
    assert ("REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_function_owner") in executed
    assert "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_executor" in executed
    assert ("DROP FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)") in executed


def test_0038_v21_downgrade_zeroing_is_derived_from_the_bound_install_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration()
    expected = tuple(
        statement
        for statement in migration._CURRENT_ERASURE_ACL_SQL_V1
        if statement.startswith(
            (
                "REVOKE ALL PRIVILEGES ON SCHEMA ",
                "REVOKE ALL PRIVILEGES ON TABLE ",
                "REVOKE ALL PRIVILEGES ON SEQUENCE ",
            )
        )
    )
    assert migration._current_erasure_bound_zeroing_sql_v1() == expected
    assert expected[:2] == (
        "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_function_owner",
        "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_executor",
    )

    monkeypatch.setattr(
        migration,
        "_CURRENT_ERASURE_ACL_SQL_V1",
        (
            *migration._CURRENT_ERASURE_ACL_SQL_V1,
            "GRANT SELECT ON TABLE public.unbound_relation TO idis_erasure_function_owner",
        ),
    )
    with pytest.raises(RuntimeError, match="TASK7_ERASURE_ACL_ZEROING_CLOSURE_INVALID"):
        migration._current_erasure_bound_zeroing_sql_v1()


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


def test_0038_downgrade_refuses_v21_evidence_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_frozen_v21()
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
    for superseded in (
        "full-strict-v20",
        "full-strict-v19",
        "full-strict-v18",
        "full-strict-v17",
        "full-strict-v16",
    ):
        assert superseded not in select
