"""RED-first contracts for the full-strict-v20 PostgreSQL census.

V20 changes only the hash-bound erasure owner SELECT closure.  The census
must preserve v19's external helper-owner composition and prove the two new
RETURNING columns without changing statement or relation counts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_SCAFFOLD = _REPO_ROOT / "scripts" / "_task7_v20_census_scaffold.py"
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v20_current_guard_census.py"
_V19_SCAFFOLD = _REPO_ROOT / "scripts" / "_task7_v19_census_scaffold.py"
_V19_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v19_current_guard_census.py"

_CANDIDATE_SHA256 = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_FROZEN_SHA256 = "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"
_RUNNER_SIGNATURE = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_EXPECTED_SELECT = (
    "GRANT SELECT "
    "(request_id,tenant_id,deal_id,status,executed_by,executed_at,counts) "
    "ON TABLE public.erasure_requests TO idis_erasure_function_owner"
)
_FORBIDDEN_COLUMN_ACL_FALLBACK = (
    "pg_catalog.aclexplode(\n"
    "                  COALESCE(attribute.attacl,'{}'::aclitem[])\n"
    "                )"
)
_DIRECT_COLUMN_ACL_EXPANSION = (
    "pg_catalog.aclexplode(\n                  attribute.attacl\n                )"
)
_FORBIDDEN_RAW_BASE_PROJECTION = (
    "projection = {field: result.get(field) for field in _BASE_DATABASE_EXECUTION_FIELDS}"
)
_FORBIDDEN_RAW_BASE_EXPECTED = (
    "expected = {field: result.get(field) for field in _BASE_DATABASE_EXECUTION_FIELDS}"
)
_DERIVED_BASE_PROJECTION = "projection = dict(_BASE._database_execution(result, artifact))"
_DERIVED_BASE_EXPECTED = "expected = dict(_BASE._database_execution(result, artifact))"


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK7_V20_CENSUS_MODULE_NOT_IMPLEMENTED:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_raw() -> bytes:
    frozen_raw = _ARTIFACT.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == _FROZEN_SHA256
    value = cast(dict[str, Any], json.loads(frozen_raw))
    assert value["metadata"]["append_frozen"] is True
    value["metadata"]["append_frozen"] = False
    raw = (json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == _CANDIDATE_SHA256
    return raw


def _artifact() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_candidate_raw()))


def test_v20_scaffold_pins_v19_and_decodes_exact_current_acl() -> None:
    scaffold = _load(_SCAFFOLD, "_task7_v20_scaffold_red")
    artifact = _artifact()

    assert scaffold._V19_SCAFFOLD_PATH == _V19_SCAFFOLD
    assert hashlib.sha256(_V19_SCAFFOLD.read_bytes()).hexdigest() == scaffold._V19_SCAFFOLD_SHA256
    authority = scaffold.erasure_authority(artifact)
    assert authority["runner_function_signature"] == _RUNNER_SIGNATURE
    body, acl = scaffold.current_erasure_sql(artifact)
    assert len(body) == 1
    assert len(acl) == 208
    assert _EXPECTED_SELECT in acl
    assert acl[-1] == (f"GRANT EXECUTE ON FUNCTION {_RUNNER_SIGNATURE} TO idis_erasure_executor")


def test_v20_runner_pins_candidate_and_materialization_closure() -> None:
    runner = _load(_RUNNER, "_task7_v20_runner_pins_red")

    assert runner._V19_RUNNER_PATH == _V19_RUNNER
    assert hashlib.sha256(_V19_RUNNER.read_bytes()).hexdigest() == runner._V19_RUNNER_SHA256
    assert runner._V20_CANDIDATE_SHA256 == _CANDIDATE_SHA256
    # The predecessor validator is intentionally a no-return admission seam.
    runner._validate_v20_candidate(_artifact())
    manifest = runner._verify_materialization_source_manifest(candidate_raw=_candidate_raw())
    assert tuple(row[0] for row in manifest) == (
        "contracts/full_strict_v20_reviewed_preimage_v1.json",
        "scripts/_task7_v20_census_scaffold.py",
        "scripts/materialize_full_strict_v20_preimage.py",
        "scripts/generate_task7_v20_contract_fixture.py",
        "scripts/task7_v17_receipt_guard_sql.py",
        "scripts/task7_v17_source_projection_sql.py",
    )


def test_v20_runner_removes_zero_dimensional_column_acl_fallbacks() -> None:
    runner = _load(_RUNNER, "_task7_v20_column_acl_null_semantics_red")
    adapted = runner._adapt_pinned_v19_runner()

    assert _FORBIDDEN_COLUMN_ACL_FALLBACK not in adapted
    assert adapted.count(_DIRECT_COLUMN_ACL_EXPANSION) == 2
    assert runner._normalize_column_acl_null_semantics(adapted) == adapted

    malformed = adapted.replace(
        _DIRECT_COLUMN_ACL_EXPANSION,
        "pg_catalog.aclexplode(\n"
        "                  COALESCE(attribute.attacl,ARRAY[]::aclitem[])\n"
        "                )",
        1,
    )
    with pytest.raises(
        RuntimeError,
        match="TASK7_V20_COLUMN_ACL_NULL_SEMANTICS_INVALID",
    ):
        runner._normalize_column_acl_null_semantics(malformed)


@pytest.mark.parametrize(
    ("derived_assignment", "raw_assignment"),
    (
        (_DERIVED_BASE_PROJECTION, _FORBIDDEN_RAW_BASE_PROJECTION),
        (_DERIVED_BASE_EXPECTED, _FORBIDDEN_RAW_BASE_EXPECTED),
    ),
)
def test_v20_database_projection_derives_the_inherited_proof_hashes(
    derived_assignment: str,
    raw_assignment: str,
) -> None:
    runner = _load(_RUNNER, "_task7_v20_database_projection_derivation_red")
    adapted = runner._adapt_pinned_v19_runner()

    assert _FORBIDDEN_RAW_BASE_PROJECTION not in adapted
    assert _FORBIDDEN_RAW_BASE_EXPECTED not in adapted
    # Receipt projection and its validator use distinct assignment targets.
    assert adapted.count(_DERIVED_BASE_PROJECTION) == 1
    assert adapted.count(_DERIVED_BASE_EXPECTED) == 1
    assert runner._normalize_database_execution_projection_semantics(adapted) == adapted

    malformed = adapted.replace(
        derived_assignment,
        raw_assignment,
        1,
    )
    with pytest.raises(
        RuntimeError,
        match="TASK7_V20_DATABASE_EXECUTION_PROJECTION_INVALID",
    ):
        runner._normalize_database_execution_projection_semantics(malformed)


def test_v20_acl_census_counts_current_and_future_without_literal_drift() -> None:
    runner = _load(_RUNNER, "_task7_v20_full_acl_counts_red")

    assert runner.erasure_acl_census_counts(_artifact()) == {
        "current_statement_count": 208,
        "current_select_relation_count": 51,
        "current_select_column_count": 116,
        "future_statement_count": 360,
        "future_select_relation_count": 89,
        "future_select_column_count": 192,
    }


def test_v20_erasure_expectations_prove_two_added_select_columns_only() -> None:
    runner = _load(_RUNNER, "_task7_v20_acl_counts_red")
    artifact = _artifact()

    expected = runner.erasure_authority_execution_expectations(artifact)
    acl_expected = runner._artifact_acl_expectations(artifact)
    assert expected["owner_select_relation_count"] == 51
    assert expected["owner_select_column_count"] == 116
    assert expected["current_relation_count"] == 48
    assert expected["owner_update_column_tuple"] == [
        "request_id",
        "status",
        "executed_by",
        "executed_at",
        "counts",
    ]
    request_acl = next(
        row for row in acl_expected["select_relation_column_tuple"] if row[0] == "erasure_requests"
    )
    assert request_acl == [
        "erasure_requests",
        "counts",
        "deal_id",
        "executed_at",
        "executed_by",
        "request_id",
        "status",
        "tenant_id",
    ]


def test_v20_candidate_loads_through_exact_nested_execution_engine(tmp_path: Path) -> None:
    runner = _load(_RUNNER, "_task7_v20_nested_execution_red")
    candidate_path = tmp_path / _ARTIFACT.name
    candidate_path.write_bytes(_candidate_raw())
    engine = runner._load_execution_engine(candidate_path)
    try:
        assert engine.RESULT_SCHEMA == "task7-v20-current-guard-app-role-census-result-v1"
        coordinates = engine.HARNESS_SOURCE_EXECUTION_COORDINATES
        assert any(row[-1] == runner._V20_ADAPTED_V11_SHA256 for row in coordinates)
        assert any(row[-1] == runner._V20_ADAPTED_V9_SHA256 for row in coordinates)
    finally:
        engine._HARNESS_SOURCE_STORE.close()


def test_v20_scaffold_composition_preserves_external_helper_owner_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_RUNNER, "_task7_v20_scaffold_order_red")
    calls: list[str] = []

    class ExecutionEngine:
        @staticmethod
        def provision_exact_erasure_principals(_admin: object) -> None:
            calls.append("principals")

        @staticmethod
        def _install_external_erasure_helper_acl(
            _admin: object,
            _artifact: object,
        ) -> None:
            calls.append("helper_owner_acl")

        @staticmethod
        def _grant_external_erasure_helper_read_closure(_admin: object) -> None:
            calls.append("helper_read_closure")

    class Scaffold:
        @staticmethod
        def install_artifact_sql_scaffold(
            _admin: object,
            _artifact: object,
            *,
            application_role: str,
        ) -> None:
            assert application_role == "idis_t2b_app_0123456789abcdef"
            calls.append("artifact_scaffold")

        @staticmethod
        def install_current_erasure_authority(
            _admin: object,
            _artifact: object,
        ) -> None:
            calls.append("current_erasure_authority")

    runner._install_v20_erasure_authority_scaffold(
        execution_engine=ExecutionEngine,
        scaffold=Scaffold,
        admin=object(),
        artifact=_artifact(),
        application_role="idis_t2b_app_0123456789abcdef",
    )
    assert calls == [
        "principals",
        "artifact_scaffold",
        "helper_owner_acl",
        "helper_read_closure",
        "current_erasure_authority",
    ]


def test_v20_runner_rejects_v19_artifact() -> None:
    runner = _load(_RUNNER, "_task7_v20_reject_v19_red")
    v19 = json.loads(
        (_REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json").read_bytes()
    )
    with pytest.raises(
        runner.CensusError,
        match="TASK7_V20_CENSUS_ARTIFACT_VERSION_MISMATCH",
    ):
        runner._validate_v20_candidate(v19)
