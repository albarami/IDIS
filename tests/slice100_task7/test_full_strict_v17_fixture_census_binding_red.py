"""RED-first binding of the signed v17 census into the frozen fixture."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.slice100_task7.test_full_strict_v17_census_scaffolding_red import (
    _semantic_proof,
    _stage_prefreeze_source_set,
    _unfrozen_v17,
)
from tests.slice100_task7.test_full_strict_v17_receipt_execution_proof_red import (
    _valid_proof as _valid_receipt_proof,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v17_contract_fixture.py"
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v17_current_guard_census.py"
_DATABASE_EXECUTION_KEYS = {
    "admin_case_execution_count",
    "admin_fixture_helper_call_count",
    "admin_fixture_helper_projection_sha256",
    "application_case_execution_count",
    "application_direct_helper_execute_count",
    "case_outcome_count",
    "case_result_sha256",
    "catalog_projection_sha256",
    "complete_execution_sha256",
    "constructor_roundtrip_sha256",
    "constructor_schema_proof_count",
    "current_guard_target_count",
    "current_guard_target_event_count",
    "current_routine_catalog_count",
    "direct_sql_rejection_count",
    "guard_acl_projection_sha256",
    "guard_owner_column_acl_count",
    "guard_owner_column_acl_relation_count",
    "illegal_rejection_count",
    "legal_execution_count",
    "manifest_parity_sha256",
    "materialization_source_manifest_sha256",
    "pg_trigger_inventory_sha256",
    "public_direct_helper_execute_count",
    "receipt_equality_execution_proof_sha256",
    "rls_context_proof_sha256",
    "source_admission_execution_proof_sha256",
    "trigger_event_coverage_sha256",
    "unexpected_outcome_count",
}


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _artifact() -> tuple[bytes, dict[str, Any]]:
    return _unfrozen_v17()


def _authorized_inputs(
    *,
    generator_name: str,
    runner_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, dict[str, Any], dict[str, Any]]:
    generator = _load(_GENERATOR, generator_name)
    runner = _load(_RUNNER, runner_name)
    _stage_prefreeze_source_set(runner, tmp_path=tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setattr(generator, "_runner", lambda: runner)
    raw, artifact = _artifact()
    result = {
        **runner.execution_expectations(artifact),
        "admin_case_execution_count": 0,
        "admin_fixture_helper_call_count": 36,
        "case_outcome_count": 219,
        "legal_execution_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "current_routine_catalog_count": 24,
        "guard_owner_column_acl_relation_count": 36,
        "guard_owner_column_acl_count": 424,
        "application_direct_helper_execute_count": 0,
        "public_direct_helper_execute_count": 0,
        "unexpected_outcome_count": 0,
        "catalog_projection_sha256": "1" * 64,
        "case_result_sha256": "2" * 64,
        "constructor_roundtrip_sha256": "3" * 64,
        "admin_fixture_helper_projection_sha256": "4" * 64,
        "manifest_parity_sha256": "5" * 64,
        "pg_trigger_inventory_sha256": "6" * 64,
        "trigger_event_coverage_sha256": "7" * 64,
        "guard_acl_projection_sha256": "8" * 64,
        "rls_context_proof_sha256": "9" * 64,
        "complete_execution_sha256": "a" * 64,
        "v17_adapter_source_sha256": hashlib.sha256(_RUNNER.read_bytes()).hexdigest(),
        "source_admission_execution_proof": _semantic_proof(),
        "receipt_equality_execution_proof": _valid_receipt_proof(runner, artifact),
        "materialization_source_manifest_tuple": (
            runner.materialization_source_manifest_evidence()
        ),
        "materialization_source_manifest_sha256": (runner.materialization_source_manifest_sha256()),
    }
    database_execution = runner._database_execution(result, artifact)
    payload = {
        "schema_version": "task7-v17-postgres-census-v1",
        "result": "PASS",
        "artifact_schema": "full-strict-v17-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v17",
        "candidate_artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(
            generator.derive_prospective_frozen_bytes(raw)
        ).hexdigest(),
        "database_execution": database_execution,
        "census_result": result,
    }
    return generator, artifact, payload


def test_authorized_v17_fixture_embeds_the_exact_verified_database_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signature-gated census must remain evidence after fixture publication."""

    generator, artifact, payload = _authorized_inputs(
        generator_name="_task7_v17_fixture_census_generator_red",
        runner_name="_task7_v17_fixture_census_runner_red",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    database_execution = payload["database_execution"]
    assert isinstance(database_execution, dict)
    assert set(database_execution) == _DATABASE_EXECUTION_KEYS

    authorization = generator.authorize_fixture_publication(
        candidate_artifact=artifact,
        verified_census_payload=payload,
    )

    assert authorization["fixture"]["postgres_authority_census"] == database_execution
    assert authorization["fixture"]["postgres_authority_census"] is not database_execution
    assert "census_result" not in authorization["fixture"]
    assert len(authorization["fixture"]) == 8


@pytest.mark.parametrize("defect", ("missing", "extra", "tampered", "runner-source"))
def test_fixture_authorization_rejects_database_execution_shape_or_value_drift(
    defect: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator, artifact, payload = _authorized_inputs(
        generator_name=f"_task7_v17_fixture_census_generator_{defect}_red",
        runner_name=f"_task7_v17_fixture_census_runner_{defect}_red",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    database_execution = dict(payload["database_execution"])
    if defect == "missing":
        database_execution.pop("case_result_sha256")
    elif defect == "extra":
        database_execution["unknown_execution_proof"] = "0" * 64
    elif defect == "tampered":
        database_execution["receipt_equality_execution_proof_sha256"] = "0" * 64
    else:
        payload["census_result"]["v17_adapter_source_sha256"] = "0" * 64
    payload["database_execution"] = database_execution

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V17_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        generator.authorize_fixture_publication(
            candidate_artifact=artifact,
            verified_census_payload=payload,
        )


def test_census_verifier_rejects_a_valid_key_signature_with_the_wrong_signer_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _load(_GENERATOR, "_task7_v17_fixture_signer_binding_red")

    class SignatureAcceptingRunner:
        @staticmethod
        def verify_signed_receipt(**_kwargs: object) -> dict[str, str]:
            return {"result": "PASS"}

    monkeypatch.setattr(generator, "_runner", lambda: SignatureAcceptingRunner)
    wrong_signer_receipt = json.dumps(
        {
            "schema_version": "task7-v17-postgres-census-envelope-v1",
            "signer_id": "different-valid-key-label",
        }
    ).encode("utf-8")

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V17_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        generator.verify_postgres_census_receipt(
            receipt_raw=wrong_signer_receipt,
            artifact_raw=b"{}",
            trusted_public_key_raw=b"test-public-key",
        )


def test_census_verifier_rejects_an_unpinned_public_key_even_with_the_right_signer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _load(_GENERATOR, "_task7_v17_fixture_public_key_binding_red")

    class SignatureAcceptingRunner:
        @staticmethod
        def verify_signed_receipt(**_kwargs: object) -> dict[str, str]:
            return {"result": "PASS"}

    approved_key = b"approved-test-public-key"
    monkeypatch.setattr(generator, "_runner", lambda: SignatureAcceptingRunner)
    monkeypatch.setattr(
        generator,
        "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256",
        hashlib.sha256(approved_key).hexdigest(),
        raising=False,
    )
    right_signer_receipt = json.dumps(
        {
            "schema_version": "task7-v17-postgres-census-envelope-v1",
            "signer_id": "slice100-governance-signer-v1",
        }
    ).encode("utf-8")

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V17_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        generator.verify_postgres_census_receipt(
            receipt_raw=right_signer_receipt,
            artifact_raw=b"{}",
            trusted_public_key_raw=b"attacker-public-key",
        )


def test_generator_rejects_census_runner_bytes_that_differ_from_the_approved_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _load(_GENERATOR, "_task7_v17_fixture_runner_source_pin_red")
    approved = _RUNNER.read_bytes()
    drifted_runner = tmp_path / _RUNNER.name
    drifted_runner.write_bytes(approved + b"\n# unauthorized post-census drift\n")
    monkeypatch.setattr(generator, "_V17_RUNNER_PATH", drifted_runner)
    monkeypatch.setattr(
        generator,
        "_EXPECTED_V17_RUNNER_SHA256",
        hashlib.sha256(approved).hexdigest(),
        raising=False,
    )

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V17_PINNED_HELPER_DRIFT",
    ):
        generator._runner()
