"""RED-first fail-closed authorization seam for full-strict-v21 publication."""

from __future__ import annotations

import copy
import gc
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUTHORIZER = _REPO_ROOT / "scripts" / "authorize_task7_v21_contract_publication.py"
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v21_preimage.py"
_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v21_contract_fixture.py"
_V20_AUTHORIZER = _REPO_ROOT / "scripts" / "authorize_task7_v20_contract_publication.py"
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v21_current_guard_census.py"
_SIGNED_RECEIPT = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task7_v21_current_guard_postgres_census_receipt.json"
)
_TRUSTED_PUBLIC_KEY = Path(
    os.environ.get(
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH",
        r"C:\IDIS-secrets\keys\governance-signing-public.pem",
    )
)

_CANDIDATE_SHA256 = "6812ddcc97086d1d5089c8af41d94a9e4a9aede17db143f4f461dce3f3cf800b"
_FROZEN_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_MATERIALIZER_SHA256 = "f038230702e846d1ee79025abdaa6b08f416702645f6d90f9d72f7ae64996946"
_GENERATOR_SHA256 = "fc7865f40942a6ea0e7eb2c5cd577acf1e33b6250d30bec7396859a4b18b93c1"
_V20_AUTHORIZER_SHA256 = "631f873b533931ebb30c42d509b51536c2ddd1077d944fb48a73e536592be207"
_RUNNER_SHA256 = "d772e5a7341b36463ef130cd237b24744de481b1d2f0ec43d860143297228412"
_SOURCE_MANIFEST_SHA256 = "2d6edf590a4b4ac439be408be69731f64e79c25af764d9c879e1f14fde6c371c"
_SIGNED_RECEIPT_SHA256 = "3f36f7b66a53aa74047608f842e29988a7d6cc745904e34fdea681655946513f"
_TRUSTED_PUBLIC_KEY_SHA256 = "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"

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


@pytest.fixture(autouse=True)
def _release_hash_pinned_helper_modules() -> None:
    """Bound memory while repeatedly loading the multi-megabyte contract chain."""

    yield
    for name in tuple(sys.modules):
        if name.startswith("_task7_"):
            sys.modules.pop(name, None)
    gc.collect()


def _render(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _load(name: str) -> ModuleType:
    assert _AUTHORIZER.is_file(), "TASK7_V21_PUBLICATION_AUTHORIZER_NOT_IMPLEMENTED"
    spec = importlib.util.spec_from_file_location(name, _AUTHORIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_raw() -> bytes:
    raw = _ARTIFACT.read_bytes()
    value = cast(dict[str, Any], json.loads(raw))
    if value["metadata"]["append_frozen"] is True:
        assert hashlib.sha256(raw).hexdigest() == _FROZEN_ARTIFACT_SHA256
        value["metadata"]["append_frozen"] = False
        raw = _render(value)
    assert value["metadata"]["append_frozen"] is False
    assert hashlib.sha256(raw).hexdigest() == _CANDIDATE_SHA256
    return raw


def _artifact() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_candidate_raw()))


def _operator_backed_inputs() -> tuple[bytes, bytes, bytes]:
    if not _SIGNED_RECEIPT.is_file():
        pytest.skip("the operator-backed v21 signed receipt is not available")
    if not _TRUSTED_PUBLIC_KEY.is_file():
        pytest.skip("the operator-backed governance public key is not available")
    artifact_raw = _candidate_raw()
    receipt_raw = _SIGNED_RECEIPT.read_bytes()
    public_key_raw = _TRUSTED_PUBLIC_KEY.read_bytes()
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
    monkeypatch.setattr(authorizer, "_EXPECTED_V21_RUNNER_SHA256", runner_sha256)
    monkeypatch.setattr(
        authorizer,
        "_EXPECTED_V21_SOURCE_MANIFEST_SHA256",
        source_manifest_sha256,
    )
    monkeypatch.setattr(
        authorizer,
        "_EXPECTED_V21_CENSUS_RECEIPT_SHA256",
        receipt_sha256,
    )
    monkeypatch.setattr(authorizer, "_PUBLICATION_INPUT_PINS_FINALIZED", True)


def test_v21_authorizer_pins_candidate_construction_and_v20_derivation_baseline() -> None:
    authorizer = _load("_task7_v21_authorizer_pins_red")

    assert authorizer._V21_ARTIFACT_PATH == _ARTIFACT
    assert authorizer._V21_ARTIFACT_SHA256 == _CANDIDATE_SHA256
    assert hashlib.sha256(_MATERIALIZER.read_bytes()).hexdigest() == _MATERIALIZER_SHA256
    assert authorizer._V21_MATERIALIZER_SHA256 == _MATERIALIZER_SHA256
    assert hashlib.sha256(_GENERATOR.read_bytes()).hexdigest() == _GENERATOR_SHA256
    assert authorizer._V21_ADMISSION_GENERATOR_SHA256 == _GENERATOR_SHA256
    assert hashlib.sha256(_V20_AUTHORIZER.read_bytes()).hexdigest() == (_V20_AUTHORIZER_SHA256)
    assert authorizer._V20_AUTHORIZER_SHA256 == _V20_AUTHORIZER_SHA256


def test_v21_authorizer_finalizes_exact_census_and_trust_root_pins() -> None:
    authorizer = _load("_task7_v21_authorizer_final_pins_red")

    assert hashlib.sha256(_RUNNER.read_bytes()).hexdigest() == _RUNNER_SHA256
    assert hashlib.sha256(_SIGNED_RECEIPT.read_bytes()).hexdigest() == (_SIGNED_RECEIPT_SHA256)
    assert authorizer._EXPECTED_V21_RUNNER_SHA256 == _RUNNER_SHA256
    assert authorizer._EXPECTED_V21_SOURCE_MANIFEST_SHA256 == (_SOURCE_MANIFEST_SHA256)
    assert authorizer._EXPECTED_V21_CENSUS_RECEIPT_SHA256 == (_SIGNED_RECEIPT_SHA256)
    assert authorizer._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 == (_TRUSTED_PUBLIC_KEY_SHA256)
    assert authorizer._PUBLICATION_INPUT_PINS_FINALIZED is True


def test_real_v21_receipt_verifies_and_authorizes_exact_fixture() -> None:
    authorizer = _load("_task7_v21_authorizer_real_receipt_red")
    artifact_raw, receipt_raw, public_key_raw = _operator_backed_inputs()
    candidate = _artifact()

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
    assert payload["schema_version"] == "task7-v21-postgres-census-v1"
    assert payload["result"] == "PASS"
    assert payload["candidate_artifact_sha256"] == _CANDIDATE_SHA256
    assert payload["prospective_frozen_artifact_sha256"] == (_FROZEN_ARTIFACT_SHA256)
    database_execution = cast(dict[str, Any], payload["database_execution"])
    assert tuple(database_execution) == _DATABASE_EXECUTION_FIELDS
    assert len(database_execution) == 31

    authorization = authorizer.authorize_fixture_publication(
        candidate_artifact=_artifact(),
        verified_census_payload=payload,
    )
    frozen_raw = cast(bytes, authorization["frozen_artifact_bytes"])
    fixture = cast(dict[str, Any], authorization["fixture"])
    expected_frozen = copy.deepcopy(candidate)
    expected_frozen["metadata"]["append_frozen"] = True
    assert frozen_raw == _render(expected_frozen)
    assert hashlib.sha256(frozen_raw).hexdigest() == _FROZEN_ARTIFACT_SHA256
    assert json.loads(frozen_raw)["metadata"]["append_frozen"] is True
    assert fixture["fixture_schema"] == "full-strict-v21-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == "full-strict-v21"
    assert fixture["artifact_sha256"] == _FROZEN_ARTIFACT_SHA256
    assert fixture["FULL_STRICT_V21_CONTRACT_HASH"] == _CONTRACT_SHA256
    assert fixture["component_hashes"]["pipeline_contract_hash"] == _CONTRACT_SHA256
    assert fixture["postgres_authority_census"] == database_execution


def test_v21_runner_pin_drift_fails_before_runner_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v21_authorizer_runner_drift_red")
    _set_finalized_pins(authorizer, monkeypatch, runner_sha256="0" * 64)

    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V21_PUBLICATION_HELPER_DRIFT",
    ):
        authorizer._runner()


def test_v21_receipt_pin_drift_fails_before_signature_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v21_authorizer_receipt_drift_red")
    artifact_raw, receipt_raw, public_key_raw = _operator_backed_inputs()
    _set_finalized_pins(authorizer, monkeypatch, receipt_sha256="0" * 64)

    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        authorizer.verify_postgres_census_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )


def test_v21_source_manifest_pin_drift_rejects_the_real_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v21_authorizer_source_manifest_drift_red")
    artifact_raw, receipt_raw, public_key_raw = _operator_backed_inputs()
    _set_finalized_pins(authorizer, monkeypatch, source_manifest_sha256="0" * 64)

    class ReceiptBoundSourceClosure:
        @staticmethod
        def verify_receipt_materialization_source_closure(
            _artifact_raw: bytes,
        ) -> dict[str, Any]:
            return {
                "materialization_source_manifest_tuple": [],
                "materialization_source_manifest_sha256": _SOURCE_MANIFEST_SHA256,
            }

    monkeypatch.setattr(authorizer, "_runner", lambda: ReceiptBoundSourceClosure())
    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        authorizer.verify_postgres_census_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )


def test_v21_untrusted_public_key_cannot_verify_the_real_receipt() -> None:
    authorizer = _load("_task7_v21_authorizer_public_key_drift_red")
    artifact_raw, receipt_raw, _public_key_raw = _operator_backed_inputs()

    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V21_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        authorizer.verify_postgres_census_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=b"untrusted-public-key",
        )
