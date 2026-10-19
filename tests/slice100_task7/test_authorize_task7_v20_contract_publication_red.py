"""RED-first fail-closed authorization seam for full-strict-v20 publication."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from tests._private_artifacts import requires_private_artifact

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUTHORIZER = _REPO_ROOT / "scripts" / "authorize_task7_v20_contract_publication.py"
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_VOID_V19_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v20_preimage.py"
_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v20_contract_fixture.py"
_V18_AUTHORIZER = _REPO_ROOT / "scripts" / "authorize_task7_v18_contract_publication.py"
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v20_current_guard_census.py"
_SIGNED_RECEIPT = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task7_v20_current_guard_postgres_census_receipt.json"
)
_TRUSTED_PUBLIC_KEY = Path(
    os.environ.get(
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH",
        r"C:\IDIS-secrets\keys\governance-signing-public.pem",
    )
)

_ARTIFACT_SHA256 = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_FROZEN_ARTIFACT_SHA256 = "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"
_MATERIALIZER_SHA256 = "f49b9f4e427505909715c7a84f6ecb88b1a1fb2082f5c652f15da5cffbdee509"
_GENERATOR_SHA256 = "3ac5826bfe9909bbedaee6a3984fe9201edd2043dab7bf547c4d81887cd16c9d"
_V18_AUTHORIZER_SHA256 = "ae2937339c663cb46012307993938e90e7260910840ff9edeedaebc5a9ae8a6c"
_RUNNER_SHA256 = "823230359182e1a0b38483c96f268577bf6fd9f9a5a23d8b2dba1752ca94d217"
_SOURCE_MANIFEST_SHA256 = "9375b1cbbafafca1c3d4263c43d0f7a4fa32b2df66acc42bf3d00447600404f2"
_SIGNED_RECEIPT_SHA256 = "7b39bde8b6746921fa144edf84a5d075617bd9ca8fe09afa078467fce0f20f98"
_TRUSTED_PUBLIC_KEY_SHA256 = "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"

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
    "downgrade_retained_definition_execution_proof_sha256",
    "erasure_authority_execution_proof_sha256",
)


def _load(name: str) -> ModuleType:
    assert _AUTHORIZER.is_file(), "TASK7_V20_PUBLICATION_AUTHORIZER_NOT_IMPLEMENTED"
    spec = importlib.util.spec_from_file_location(name, _AUTHORIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_raw() -> bytes:
    frozen_raw = _ARTIFACT.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == _FROZEN_ARTIFACT_SHA256
    value = json.loads(frozen_raw)
    assert value["metadata"]["append_frozen"] is True
    value["metadata"]["append_frozen"] = False
    raw = (json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == _ARTIFACT_SHA256
    return raw


def _artifact() -> dict[str, Any]:
    value = json.loads(_candidate_raw())
    assert value["metadata"] == {
        **value["metadata"],
        "artifact_schema": "full-strict-v20-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v20",
        "append_frozen": False,
    }
    return cast(dict[str, Any], value)


def _operator_backed_inputs() -> tuple[bytes, bytes, bytes]:
    artifact_raw = _candidate_raw()
    receipt_raw = _SIGNED_RECEIPT.read_bytes()
    public_key_raw = _TRUSTED_PUBLIC_KEY.read_bytes()
    assert hashlib.sha256(artifact_raw).hexdigest() == _ARTIFACT_SHA256
    assert hashlib.sha256(receipt_raw).hexdigest() == _SIGNED_RECEIPT_SHA256
    assert hashlib.sha256(public_key_raw).hexdigest() == _TRUSTED_PUBLIC_KEY_SHA256
    return artifact_raw, receipt_raw, public_key_raw


def _set_finalized_pins(
    authorizer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    runner_sha256: str = _RUNNER_SHA256,
    source_manifest_sha256: str = _SOURCE_MANIFEST_SHA256,
    receipt_sha256: str = _SIGNED_RECEIPT_SHA256,
) -> None:
    monkeypatch.setattr(authorizer, "_EXPECTED_V20_RUNNER_SHA256", runner_sha256)
    monkeypatch.setattr(
        authorizer,
        "_EXPECTED_V20_SOURCE_MANIFEST_SHA256",
        source_manifest_sha256,
    )
    monkeypatch.setattr(
        authorizer,
        "_EXPECTED_V20_CENSUS_RECEIPT_SHA256",
        receipt_sha256,
    )
    monkeypatch.setattr(authorizer, "_PUBLICATION_INPUT_PINS_FINALIZED", True)


def _bind_receipt_candidate_manifest(
    authorizer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    """Revalidate the signed candidate row after the live artifact is frozen."""

    runner = authorizer._runner()
    artifact = _artifact()
    manifest_sha256 = runner._receipt_materialization_source_sha256(artifact)
    assert manifest_sha256 == _SOURCE_MANIFEST_SHA256
    base_projection = runner._BASE._database_execution
    validator = base_projection.__globals__["validate_database_execution_projection"]
    monkeypatch.setitem(
        validator.__globals__,
        "materialization_source_manifest_sha256",
        lambda: manifest_sha256,
    )
    monkeypatch.setattr(authorizer, "_runner", lambda: runner)
    return runner


def test_v20_authorizer_pins_exact_candidate_materializer_generator_and_v18_baseline() -> None:
    authorizer = _load("_task7_v20_authorizer_pins_red")

    assert authorizer._V20_ARTIFACT_PATH == _ARTIFACT
    assert authorizer._V20_ARTIFACT_SHA256 == _ARTIFACT_SHA256
    assert hashlib.sha256(_MATERIALIZER.read_bytes()).hexdigest() == _MATERIALIZER_SHA256
    assert authorizer._V20_MATERIALIZER_SHA256 == _MATERIALIZER_SHA256
    assert hashlib.sha256(_GENERATOR.read_bytes()).hexdigest() == _GENERATOR_SHA256
    assert authorizer._V20_ADMISSION_GENERATOR_SHA256 == _GENERATOR_SHA256
    assert hashlib.sha256(_V18_AUTHORIZER.read_bytes()).hexdigest() == _V18_AUTHORIZER_SHA256
    assert authorizer._V18_AUTHORIZER_SHA256 == _V18_AUTHORIZER_SHA256


@requires_private_artifact("task7_v20_current_guard_postgres_census_receipt.json")
def test_v20_authorizer_finalizes_exact_census_runner_source_and_receipt_pins() -> None:
    authorizer = _load("_task7_v20_authorizer_final_pins_red")

    assert hashlib.sha256(_RUNNER.read_bytes()).hexdigest() == _RUNNER_SHA256
    assert hashlib.sha256(_SIGNED_RECEIPT.read_bytes()).hexdigest() == (_SIGNED_RECEIPT_SHA256)
    assert authorizer._EXPECTED_V20_RUNNER_SHA256 == _RUNNER_SHA256
    assert authorizer._EXPECTED_V20_SOURCE_MANIFEST_SHA256 == (_SOURCE_MANIFEST_SHA256)
    assert authorizer._EXPECTED_V20_CENSUS_RECEIPT_SHA256 == (_SIGNED_RECEIPT_SHA256)
    assert authorizer._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 == (_TRUSTED_PUBLIC_KEY_SHA256)
    assert authorizer._PUBLICATION_INPUT_PINS_FINALIZED is True


def test_v20_authorizer_never_selects_the_void_v19_candidate() -> None:
    authorizer = _load("_task7_v20_authorizer_v19_rejection_red")
    void_v19 = json.loads(_VOID_V19_ARTIFACT.read_bytes())

    assert not hasattr(authorizer, "_V19_ARTIFACT_PATH")
    assert authorizer._V20_ARTIFACT_SCHEMA == "full-strict-v20-reviewed-preimage-v1"
    assert authorizer._V20_CONTRACT_VERSION == "full-strict-v20"
    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V20_CONTRACT_ARTIFACT_INVALID",
    ):
        authorizer._validated_candidate(void_v19)


@requires_private_artifact("task7_v20_current_guard_postgres_census_receipt.json")
def test_real_v20_receipt_verifies_31_field_projection_and_authorizes_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v20_authorizer_real_receipt_red")
    artifact_raw, receipt_raw, public_key_raw = _operator_backed_inputs()
    runner = _bind_receipt_candidate_manifest(authorizer, monkeypatch)

    payload = authorizer.verify_postgres_census_receipt(
        receipt_raw=receipt_raw,
        artifact_raw=artifact_raw,
        trusted_public_key_raw=public_key_raw,
    )
    assert tuple(payload) == (
        "schema_version",
        "result",
        "artifact_schema",
        "pipeline_contract_version",
        "candidate_artifact_sha256",
        "prospective_frozen_artifact_sha256",
        "database_execution",
        "census_result",
    )
    assert payload["schema_version"] == "task7-v20-postgres-census-v1"
    assert payload["result"] == "PASS"
    database_execution = cast(dict[str, Any], payload["database_execution"])
    assert len(database_execution) == 31
    assert tuple(database_execution) == _DATABASE_EXECUTION_FIELDS
    census_result = cast(dict[str, Any], payload["census_result"])
    assert database_execution["source_admission_execution_proof_sha256"] == (
        runner._digest(census_result["source_admission_execution_proof"])
    )
    assert database_execution["receipt_equality_execution_proof_sha256"] == (
        runner._digest(census_result["receipt_equality_execution_proof"])
    )

    authorization = authorizer.authorize_fixture_publication(
        candidate_artifact=_artifact(),
        verified_census_payload=payload,
    )
    frozen = json.loads(authorization["frozen_artifact_bytes"])
    fixture = cast(dict[str, Any], authorization["fixture"])
    assert frozen["metadata"]["append_frozen"] is True
    assert frozen["metadata"]["pipeline_contract_version"] == "full-strict-v20"
    assert fixture["pipeline_contract_version"] == "full-strict-v20"
    assert fixture["postgres_authority_census"] == database_execution


def test_v20_runner_pin_drift_fails_before_runner_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v20_authorizer_runner_drift_red")
    _set_finalized_pins(authorizer, monkeypatch, runner_sha256="0" * 64)

    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V20_PUBLICATION_HELPER_DRIFT",
    ):
        authorizer._runner()


@requires_private_artifact("task7_v20_current_guard_postgres_census_receipt.json")
def test_v20_receipt_pin_drift_fails_before_signature_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v20_authorizer_receipt_drift_red")
    artifact_raw, receipt_raw, public_key_raw = _operator_backed_inputs()
    _set_finalized_pins(authorizer, monkeypatch, receipt_sha256="0" * 64)

    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        authorizer.verify_postgres_census_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )


@requires_private_artifact("task7_v20_current_guard_postgres_census_receipt.json")
def test_v20_source_manifest_pin_drift_rejects_the_real_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v20_authorizer_source_manifest_drift_red")
    artifact_raw, receipt_raw, public_key_raw = _operator_backed_inputs()
    _set_finalized_pins(authorizer, monkeypatch, source_manifest_sha256="0" * 64)

    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        authorizer.verify_postgres_census_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )


@requires_private_artifact("task7_v20_current_guard_postgres_census_receipt.json")
def test_v20_signed_payload_drift_cannot_authorize_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v20_authorizer_payload_drift_red")
    artifact_raw, receipt_raw, public_key_raw = _operator_backed_inputs()
    _set_finalized_pins(authorizer, monkeypatch)
    _bind_receipt_candidate_manifest(authorizer, monkeypatch)
    payload = authorizer.verify_postgres_census_receipt(
        receipt_raw=receipt_raw,
        artifact_raw=artifact_raw,
        trusted_public_key_raw=public_key_raw,
    )
    changed = copy.deepcopy(payload)
    changed["database_execution"]["complete_execution_sha256"] = "0" * 64

    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        authorizer.authorize_fixture_publication(
            candidate_artifact=_artifact(),
            verified_census_payload=changed,
        )


@requires_private_artifact("task7_v20_current_guard_postgres_census_receipt.json")
def test_v20_untrusted_public_key_cannot_verify_the_real_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v20_authorizer_public_key_drift_red")
    artifact_raw, receipt_raw, _public_key_raw = _operator_backed_inputs()
    _set_finalized_pins(authorizer, monkeypatch)

    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V20_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        authorizer.verify_postgres_census_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=b"untrusted-public-key",
        )
