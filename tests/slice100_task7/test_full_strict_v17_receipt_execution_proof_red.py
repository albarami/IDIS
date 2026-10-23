"""RED-first contracts for the v17 receipt-equality PostgreSQL proof."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.slice100_task7.test_full_strict_v17_census_scaffolding_red import (
    _stage_prefreeze_source_set,
    _unfrozen_v17,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V17_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v17_current_guard_census.py"
_RECEIPT_HELPER = _REPO_ROOT / "scripts" / "task7_v17_receipt_guard_sql.py"
_V17_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v17_contract_hash_v1.json"
)

_PROOF_SCHEMA = "task7-v17-receipt-equality-execution-proof-v1"
_PROOF_DIGEST_DOMAIN = "task7-v17-receipt-equality-execution-proof-v1"
_PROOF_INVALID = "TASK7_V17_RECEIPT_PROOF_INVALID"
_REJECTION_SQLSTATE = "23514"
_REJECTION_MARKER = "CONSUMER_RECEIPT_INCOMPLETE"
_BASE_CASE_COUNT = 219
_SUPPLEMENTAL_CASE_COUNT = 119
_ACCEPT_COUNT = 30
_REJECT_COUNT = 89

_SCENARIO_KIND_COUNTS = {
    "EXACT_SET": 28,
    "OMISSION": 83,
    "EXTRA_DRIFT": 1,
    "OUTPUT_KIND_DRIFT": 1,
    "OUTPUT_ORDINAL_DRIFT": 1,
    "SEMANTIC_VERSION_DRIFT": 1,
    "SEMANTIC_HASH_DRIFT": 1,
    "NATIVE_CONSUMER_TARGET": 1,
    "SCOPE_EXCLUSION": 2,
}
_TUPLE_DRIFT_KINDS = (
    "EXTRA_DRIFT",
    "OUTPUT_KIND_DRIFT",
    "OUTPUT_ORDINAL_DRIFT",
    "SEMANTIC_VERSION_DRIFT",
    "SEMANTIC_HASH_DRIFT",
)
_SCOPE_EXCLUSION_IDENTITIES = (
    "SCOPE:LEGACY_CONTRACT",
    "SCOPE:NON_RESUMABLE",
)
_DATABASE_EXECUTION_FIELDS = (
    "application_case_execution_count",
    "admin_case_execution_count",
    "admin_fixture_helper_call_count",
    "current_guard_target_count",
    "current_guard_target_event_count",
    "case_outcome_count",
    "legal_execution_count",
    "illegal_rejection_count",
    "direct_sql_rejection_count",
    "constructor_schema_proof_count",
    "current_routine_catalog_count",
    "guard_owner_column_acl_relation_count",
    "guard_owner_column_acl_count",
    "application_direct_helper_execute_count",
    "public_direct_helper_execute_count",
    "unexpected_outcome_count",
    "catalog_projection_sha256",
    "case_result_sha256",
    "constructor_roundtrip_sha256",
    "admin_fixture_helper_projection_sha256",
    "manifest_parity_sha256",
    "pg_trigger_inventory_sha256",
    "trigger_event_coverage_sha256",
    "guard_acl_projection_sha256",
    "rls_context_proof_sha256",
    "complete_execution_sha256",
    "source_admission_execution_proof_sha256",
    "receipt_equality_execution_proof_sha256",
    "materialization_source_manifest_sha256",
)


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file()
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _artifact() -> dict[str, Any]:
    _raw, value = _unfrozen_v17()
    return value


def _runner(name: str) -> ModuleType:
    return _load_module(_V17_RUNNER, name)


def _canonical_coordinates(artifact: Mapping[str, Any]) -> tuple[tuple[Any, ...], ...]:
    helper = _load_module(
        _RECEIPT_HELPER,
        "_task7_v17_receipt_execution_proof_helper",
    )
    return tuple(
        tuple(row)
        for row in helper.expected_receipt_rows(
            artifact["semantic_step_registry"]["rows"],
        )
    )


def _case_rows(plan: object) -> tuple[Mapping[str, Any], ...]:
    assert isinstance(plan, Sequence)
    assert not isinstance(plan, (str, bytes, bytearray))
    assert all(isinstance(row, Mapping) for row in plan)
    return tuple(plan)


def _rehash(runner: ModuleType, proof: dict[str, Any]) -> dict[str, Any]:
    proof["execution_sha256"] = runner._digest(
        [
            _PROOF_DIGEST_DOMAIN,
            {key: value for key, value in proof.items() if key != "execution_sha256"},
        ]
    )
    return proof


def _valid_proof(
    runner: ModuleType,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    plan = _case_rows(runner.receipt_equality_execution_plan(artifact))
    outcomes: list[dict[str, Any]] = []
    for case in plan:
        outcomes.append(
            {
                "scenario_id": case["scenario_id"],
                "scenario_kind": case["scenario_kind"],
                "scenario_sha256": runner._digest(["task7-v17-receipt-case-v1", case]),
                "outcome": case["expected_outcome"],
                "sqlstate": (
                    _REJECTION_SQLSTATE if case["expected_outcome"] == "REJECTED" else None
                ),
                "rejection_marker": (
                    _REJECTION_MARKER if case["expected_outcome"] == "REJECTED" else None
                ),
                "zero_mutation": case["expected_outcome"] == "REJECTED",
            }
        )
    proof = {
        "schema_version": _PROOF_SCHEMA,
        "executed_as_application_role": True,
        "database_actor": {
            "role_name": "idis_app_receipt_proof",
            "session_user": "idis_app_receipt_proof",
            "is_superuser": False,
            "bypass_rls": False,
        },
        "canonical_coordinate_count": 83,
        "aggregate_coordinate_count": 28,
        "subrole_coordinate_count": 55,
        "case_execution_count": _SUPPLEMENTAL_CASE_COUNT,
        "acceptance_count": _ACCEPT_COUNT,
        "rejection_count": _REJECT_COUNT,
        "case_outcome_tuple": outcomes,
        "execution_sha256": "",
    }
    return _rehash(runner, proof)


def _negative_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(row for row in rows if row["outcome"] == "REJECTED")


def _database_result(
    runner: ModuleType,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    expectations = runner.execution_expectations(artifact)
    acl = artifact["sql_authority_registry"]["guard_function_acl_registry"]["guard_owner_role"][
        "select_relation_column_tuple"
    ]
    fixture = json.loads(_V17_FIXTURE.read_bytes())
    persisted_census = fixture["postgres_authority_census"]
    result: dict[str, Any] = {
        **expectations,
        "admin_case_execution_count": 0,
        "admin_fixture_helper_call_count": 36,
        "case_outcome_count": 219,
        "legal_execution_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "current_routine_catalog_count": len(runner._current_function_rows(artifact)),
        "guard_owner_column_acl_relation_count": len(acl),
        "guard_owner_column_acl_count": sum(len(row[1]) for row in acl),
        "application_direct_helper_execute_count": 0,
        "public_direct_helper_execute_count": 0,
        "unexpected_outcome_count": 0,
        "source_admission_execution_proof": {"proof": "source"},
        "receipt_equality_execution_proof": {"proof": "receipt"},
        "materialization_source_manifest_sha256": (
            persisted_census["materialization_source_manifest_sha256"]
        ),
    }
    for ordinal, field in enumerate(_DATABASE_EXECUTION_FIELDS):
        if field.endswith("_sha256") and field not in result:
            result[field] = f"{ordinal % 16:x}" * 64
    return result


def test_receipt_equality_execution_plan_is_derived_from_the_production_helper() -> None:
    artifact = _artifact()
    runner = _runner("_task7_v17_receipt_execution_plan_red")
    plan = _case_rows(runner.receipt_equality_execution_plan(artifact))
    expected_coordinates = _canonical_coordinates(artifact)

    assert len(expected_coordinates) == 83
    assert len({row[0] for row in expected_coordinates}) == 28
    exact = plan[:28]
    grouped = {
        step_name: tuple(row for row in expected_coordinates if row[0] == step_name)
        for step_name in dict.fromkeys(row[0] for row in expected_coordinates)
    }
    assert tuple(row["step_name"] for row in exact) == tuple(grouped)
    assert tuple(
        tuple(tuple(coordinate) for coordinate in row["coordinate_tuple"]) for row in exact
    ) == tuple(grouped.values())


def test_receipt_equality_execution_plan_is_a_closed_ordered_119_case_census() -> None:
    artifact = _artifact()
    runner = _runner("_task7_v17_receipt_execution_case_census_red")
    plan = runner.receipt_equality_execution_plan(artifact)
    coordinates = _canonical_coordinates(artifact)
    rows = _case_rows(plan)

    assert (
        runner.execution_expectations(artifact)["application_case_execution_count"]
        == _BASE_CASE_COUNT
    )
    assert len(rows) == _SUPPLEMENTAL_CASE_COUNT
    assert Counter(row["scenario_kind"] for row in rows) == _SCENARIO_KIND_COUNTS
    assert len({row["scenario_id"] for row in rows}) == 119

    exact = tuple(row for row in rows if row["scenario_kind"] == "EXACT_SET")
    omission = tuple(row for row in rows if row["scenario_kind"] == "OMISSION")
    drift = tuple(
        row
        for row in rows
        if row["scenario_kind"]
        in {
            "EXTRA_DRIFT",
            "OUTPUT_KIND_DRIFT",
            "OUTPUT_ORDINAL_DRIFT",
            "SEMANTIC_VERSION_DRIFT",
            "SEMANTIC_HASH_DRIFT",
        }
    )
    native = tuple(row for row in rows if row["scenario_kind"] == "NATIVE_CONSUMER_TARGET")
    scope = tuple(row for row in rows if row["scenario_kind"] == "SCOPE_EXCLUSION")

    semantic_step_order = tuple(row[1] for row in artifact["semantic_step_registry"]["rows"])
    assert tuple(row["step_name"] for row in exact) == semantic_step_order
    grouped = {
        step_name: tuple(row for row in coordinates if row[0] == step_name)
        for step_name in semantic_step_order
    }
    omitted_by_case = tuple(
        next(
            coordinate
            for coordinate in grouped[row["step_name"]]
            if coordinate not in tuple(tuple(value) for value in row["coordinate_tuple"])
        )
        for row in omission
    )
    assert omitted_by_case == coordinates
    assert tuple(row["scenario_kind"] for row in drift) == _TUPLE_DRIFT_KINDS
    assert len(native) == 1
    assert native[0]["scenario_id"] == "CONSUMER:NATIVE_ROW_TARGET"
    assert tuple(row["scenario_id"] for row in scope) == _SCOPE_EXCLUSION_IDENTITIES


def test_every_receipt_equality_scenario_is_digestible_by_the_proof_canonicalizer() -> None:
    artifact = _artifact()
    runner = _runner("_task7_v17_receipt_execution_scenario_digest_red")
    plan = _case_rows(runner.receipt_equality_execution_plan(artifact))

    assert all(
        len(runner._digest(["task7-v17-receipt-case-v1", scenario])) == 64 for scenario in plan
    )


def test_every_receipt_equality_negative_has_the_one_fail_closed_signature() -> None:
    artifact = _artifact()
    runner = _runner("_task7_v17_receipt_execution_negative_signature_red")
    plan = _case_rows(runner.receipt_equality_execution_plan(artifact))
    proof = _valid_proof(runner, artifact)
    outcomes = proof["case_outcome_tuple"]
    assert isinstance(outcomes, Sequence)
    negatives = _negative_rows(outcomes)

    assert len(negatives) == _REJECT_COUNT
    assert all(row["sqlstate"] == _REJECTION_SQLSTATE for row in negatives)
    assert all(row["rejection_marker"] == _REJECTION_MARKER for row in negatives)
    accepts = tuple(row for row in outcomes if row["outcome"] == "ACCEPTED")
    assert len(accepts) == _ACCEPT_COUNT
    assert all(row["sqlstate"] is None for row in accepts)
    assert all(row["rejection_marker"] is None for row in accepts)
    assert Counter(row["expected_outcome"] for row in plan) == {
        "ACCEPTED": 30,
        "REJECTED": 89,
    }


def test_receipt_equality_execution_proof_accepts_only_the_closed_app_role_result() -> None:
    artifact = _artifact()
    runner = _runner("_task7_v17_receipt_execution_valid_proof_red")
    proof = _valid_proof(runner, artifact)

    assert (
        runner.validate_receipt_equality_execution_proof(
            proof,
            artifact=artifact,
        )
        == proof
    )
    assert runner.execution_expectations(artifact)["application_case_execution_count"] == 219
    assert proof["case_execution_count"] == 119
    assert proof["database_actor"] == {
        "role_name": "idis_app_receipt_proof",
        "session_user": "idis_app_receipt_proof",
        "is_superuser": False,
        "bypass_rls": False,
    }


@pytest.mark.parametrize(
    "tamper",
    (
        "identity",
        "order",
        "marker",
        "sqlstate",
        "rolsuper",
        "rolbypassrls",
        "digest",
    ),
)
def test_receipt_equality_execution_proof_rejects_tampering(tamper: str) -> None:
    artifact = _artifact()
    runner = _runner(f"_task7_v17_receipt_execution_tamper_{tamper}_red")
    proof = _valid_proof(runner, artifact)
    outcomes = proof["case_outcome_tuple"]
    assert isinstance(outcomes, list)
    negative_index = next(
        index for index, row in enumerate(outcomes) if row["outcome"] == "REJECTED"
    )

    if tamper == "identity":
        outcomes[0]["scenario_id"] = "tampered-case-identity"
    elif tamper == "order":
        outcomes[0], outcomes[1] = outcomes[1], outcomes[0]
    elif tamper == "marker":
        outcomes[negative_index]["rejection_marker"] = "WRONG_MARKER"
    elif tamper == "sqlstate":
        outcomes[negative_index]["sqlstate"] = "40001"
    elif tamper == "rolsuper":
        proof["database_actor"]["is_superuser"] = True
    elif tamper == "rolbypassrls":
        proof["database_actor"]["bypass_rls"] = True
    else:
        proof["execution_sha256"] = "0" * 64

    if tamper != "digest":
        _rehash(runner, proof)
    with pytest.raises(runner.CensusError, match=_PROOF_INVALID):
        runner.validate_receipt_equality_execution_proof(
            proof,
            artifact=artifact,
        )


def test_receipt_equality_execution_proof_rejects_count_masking() -> None:
    artifact = _artifact()
    runner = _runner("_task7_v17_receipt_execution_count_mask_red")
    proof = _valid_proof(runner, artifact)
    proof["case_execution_count"] = 338
    _rehash(runner, proof)

    with pytest.raises(runner.CensusError, match=_PROOF_INVALID):
        runner.validate_receipt_equality_execution_proof(
            proof,
            artifact=artifact,
        )


def test_receipt_equality_execution_proof_rejects_unknown_or_missing_outcomes() -> None:
    artifact = _artifact()
    runner = _runner("_task7_v17_receipt_execution_outcome_closure_red")

    missing = _valid_proof(runner, artifact)
    missing["case_outcome_tuple"].pop()
    _rehash(runner, missing)
    with pytest.raises(runner.CensusError, match=_PROOF_INVALID):
        runner.validate_receipt_equality_execution_proof(
            missing,
            artifact=artifact,
        )

    extra = _valid_proof(runner, artifact)
    unexpected = copy.deepcopy(extra["case_outcome_tuple"][-1])
    unexpected["scenario_id"] = "unexpected-extra-case"
    extra["case_outcome_tuple"].append(unexpected)
    _rehash(runner, extra)
    with pytest.raises(runner.CensusError, match=_PROOF_INVALID):
        runner.validate_receipt_equality_execution_proof(
            extra,
            artifact=artifact,
        )


def test_database_execution_conserves_all_26_legacy_fields_plus_three_v17_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact()
    runner = _runner("_task7_v17_database_execution_closure_red")
    _stage_prefreeze_source_set(runner, tmp_path=tmp_path, monkeypatch=monkeypatch)
    result = _database_result(runner, artifact)
    projection = runner._database_execution(result, artifact)

    assert tuple(projection) == _DATABASE_EXECUTION_FIELDS
    assert "census_result" not in projection
    assert projection["current_guard_target_event_count"] == 193
    assert projection["source_admission_execution_proof_sha256"] == runner._digest(
        result["source_admission_execution_proof"]
    )
    assert projection["receipt_equality_execution_proof_sha256"] == runner._digest(
        result["receipt_equality_execution_proof"]
    )
    assert (
        projection["materialization_source_manifest_sha256"]
        == (result["materialization_source_manifest_sha256"])
    )
    runner.validate_database_execution_projection(
        projection=projection,
        result=result,
        artifact=artifact,
    )


@pytest.mark.parametrize(
    "tamper",
    ("missing", "extra", "legacy_value", "v17_value", "census_result"),
)
def test_database_execution_rejects_any_projection_masking(
    tamper: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact()
    runner = _runner(f"_task7_v17_database_execution_tamper_{tamper}_red")
    _stage_prefreeze_source_set(runner, tmp_path=tmp_path, monkeypatch=monkeypatch)
    result = _database_result(runner, artifact)
    projection = dict(runner._database_execution(result, artifact))

    if tamper == "missing":
        projection.pop("catalog_projection_sha256")
    elif tamper == "extra":
        projection["unexpected"] = "0" * 64
    elif tamper == "legacy_value":
        projection["current_guard_target_event_count"] = 192
    elif tamper == "v17_value":
        projection["receipt_equality_execution_proof_sha256"] = "0" * 64
    else:
        projection["census_result"] = dict(result)

    with pytest.raises(
        runner.CensusError,
        match="TASK7_V17_DATABASE_EXECUTION_PROJECTION_INVALID",
    ):
        runner.validate_database_execution_projection(
            projection=projection,
            result=result,
            artifact=artifact,
        )
