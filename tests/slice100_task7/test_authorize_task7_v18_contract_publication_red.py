"""RED-first authorization of the signed v18 census for atomic publication."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUTHORIZER = _REPO_ROOT / "scripts" / "authorize_task7_v18_contract_publication.py"
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v18_current_guard_census.py"
_ADMISSION_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v18_contract_fixture.py"
_V17_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v17_contract_fixture.py"

_CANDIDATE_SHA256 = "2fd34ce570e582e07a2fc88e1be016464f1972c0907b1900ca085ed807532038"
_FROZEN_SHA256 = "554409ac36f3defeceda209e9e59e7587e4df630b06b5dc049ba9958f60abd1c"
_RUNNER_SHA256 = "10988bb9cbbc368b496c7d9e55177b5d7390219d8d38dc70e40e10583a6cf006"
_ADMISSION_GENERATOR_SHA256 = "4a9aba57523ce2baaeb8341fcd131789d4842cae17cd5471f22e858c496aa3f3"
_V17_GENERATOR_SHA256 = "f5df862fd05ad57ccaae4f829add8e90e5a810f0eaa992721e87c2b1317ebace"
_TRUSTED_PUBLIC_KEY_SHA256 = "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"


def _load(name: str) -> ModuleType:
    assert _AUTHORIZER.is_file(), "TASK7_V18_PUBLICATION_AUTHORIZER_NOT_IMPLEMENTED"
    spec = importlib.util.spec_from_file_location(name, _AUTHORIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _artifact() -> tuple[bytes, dict[str, Any]]:
    frozen_raw = _ARTIFACT.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == _FROZEN_SHA256
    value = json.loads(frozen_raw)
    assert isinstance(value, dict) and value["metadata"]["append_frozen"] is True
    value["metadata"]["append_frozen"] = False
    raw = (json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == _CANDIDATE_SHA256
    return raw, value


class _FakeRunner:
    """Closed verifier seam; real runner pins and signed execution are separate tests."""

    validated = False

    @classmethod
    def validate_execution_contract(
        cls,
        *,
        result: object,
        artifact: object,
    ) -> None:
        assert isinstance(result, dict)
        assert result["downgrade_retained_definition_execution_proof"] == {
            "execution_sha256": "a" * 64
        }
        assert isinstance(artifact, dict)
        cls.validated = True

    @staticmethod
    def _database_execution(
        result: object,
        artifact: object,
    ) -> dict[str, Any]:
        assert isinstance(result, dict)
        assert isinstance(artifact, dict)
        return {
            "complete_execution_sha256": result["complete_execution_sha256"],
            "materialization_source_manifest_sha256": result[
                "materialization_source_manifest_sha256"
            ],
        }


def _payload(authorizer: ModuleType) -> tuple[dict[str, Any], dict[str, Any]]:
    raw, artifact = _artifact()
    frozen = authorizer.derive_prospective_frozen_bytes(raw)
    result = {
        "complete_execution_sha256": "b" * 64,
        "materialization_source_manifest_sha256": "c" * 64,
        "downgrade_retained_definition_execution_proof": {"execution_sha256": "a" * 64},
    }
    payload = {
        "schema_version": "task7-v18-postgres-census-v1",
        "result": "PASS",
        "artifact_schema": "full-strict-v18-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v18",
        "candidate_artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(frozen).hexdigest(),
        "database_execution": _FakeRunner._database_execution(result, artifact),
        "census_result": result,
    }
    return artifact, payload


def test_authorizer_pins_the_complete_noncyclic_publication_closure() -> None:
    authorizer = _load("_task7_v18_publication_pins_red")

    assert hashlib.sha256(_ARTIFACT.read_bytes()).hexdigest() == _FROZEN_SHA256
    assert hashlib.sha256(_RUNNER.read_bytes()).hexdigest() == _RUNNER_SHA256
    assert (
        hashlib.sha256(_ADMISSION_GENERATOR.read_bytes()).hexdigest() == _ADMISSION_GENERATOR_SHA256
    )
    assert hashlib.sha256(_V17_GENERATOR.read_bytes()).hexdigest() == _V17_GENERATOR_SHA256
    assert authorizer._V18_ARTIFACT_SHA256 == _CANDIDATE_SHA256
    assert authorizer._EXPECTED_V18_RUNNER_SHA256 == _RUNNER_SHA256
    assert authorizer._V18_ADMISSION_GENERATOR_SHA256 == _ADMISSION_GENERATOR_SHA256
    assert authorizer._V17_GENERATOR_SHA256 == _V17_GENERATOR_SHA256
    assert authorizer._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 == _TRUSTED_PUBLIC_KEY_SHA256


def test_v18_fixture_derivation_is_deterministic_and_version_bound() -> None:
    authorizer = _load("_task7_v18_publication_derivation_red")
    raw, _artifact_value = _artifact()

    frozen = authorizer.derive_prospective_frozen_bytes(raw)
    assert frozen != raw
    assert json.loads(frozen)["metadata"]["append_frozen"] is True
    assert json.loads(raw)["metadata"]["append_frozen"] is False

    fixture = authorizer.derive_contract_bytes(frozen)
    assert fixture["fixture_schema"] == "full-strict-v18-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == "full-strict-v18"
    assert fixture["artifact_sha256"] == hashlib.sha256(frozen).hexdigest()
    assert (
        fixture["FULL_STRICT_V18_CONTRACT_HASH"]
        == fixture["component_hashes"]["pipeline_contract_hash"]
    )
    assert len(fixture["FULL_STRICT_V18_CONTRACT_HASH"]) == 64
    assert authorizer.derive_contract_bytes(frozen) == fixture


def test_signed_payload_authorizes_exact_frozen_bytes_and_database_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v18_publication_authorization_red")
    artifact, payload = _payload(authorizer)
    monkeypatch.setattr(authorizer, "_runner", lambda: _FakeRunner)

    authorization = authorizer.authorize_fixture_publication(
        candidate_artifact=artifact,
        verified_census_payload=payload,
    )

    assert _FakeRunner.validated is True
    assert json.loads(authorization["frozen_artifact_bytes"])["metadata"]["append_frozen"] is True
    assert authorization["fixture"]["postgres_authority_census"] == payload["database_execution"]
    assert (
        authorization["fixture"]["postgres_authority_census"] is not payload["database_execution"]
    )
    assert "census_result" not in authorization["fixture"]


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-payload",
        "wrong-version",
        "wrong-candidate",
        "wrong-frozen",
        "missing-proof",
        "database-projection-drift",
    ),
)
def test_publication_fails_closed_for_receipt_or_proof_drift(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load(f"_task7_v18_publication_reject_{mutation}")
    artifact, payload = _payload(authorizer)
    monkeypatch.setattr(authorizer, "_runner", lambda: _FakeRunner)

    changed: dict[str, Any] | None = copy.deepcopy(payload)
    if mutation == "missing-payload":
        changed = None
    elif mutation == "wrong-version":
        assert changed is not None
        changed["pipeline_contract_version"] = "full-strict-v17"
    elif mutation == "wrong-candidate":
        assert changed is not None
        changed["candidate_artifact_sha256"] = "0" * 64
    elif mutation == "wrong-frozen":
        assert changed is not None
        changed["prospective_frozen_artifact_sha256"] = "0" * 64
    elif mutation == "missing-proof":
        assert changed is not None
        changed["census_result"].pop("downgrade_retained_definition_execution_proof")
    else:
        assert changed is not None
        changed["database_execution"]["complete_execution_sha256"] = "0" * 64

    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V18_POSTGRES_CENSUS_RECEIPT_INVALID"
        if changed is not None
        else "TASK7_V18_POSTGRES_CENSUS_RECEIPT_REQUIRED",
    ):
        authorizer.authorize_fixture_publication(
            candidate_artifact=artifact,
            verified_census_payload=changed,
        )


def test_verifier_rejects_wrong_signer_or_unpinned_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v18_publication_trust_red")

    class _SignatureAcceptingRunner:
        @staticmethod
        def verify_signed_receipt(**_kwargs: object) -> dict[str, str]:
            return {"result": "PASS"}

    monkeypatch.setattr(authorizer, "_runner", lambda: _SignatureAcceptingRunner)
    receipt = json.dumps(
        {
            "schema_version": "task7-v18-postgres-census-envelope-v1",
            "signer_id": "wrong-signer",
        }
    ).encode()
    with pytest.raises(
        authorizer.ContractPublicationError,
        match="TASK7_V18_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        authorizer.verify_postgres_census_receipt(
            receipt_raw=receipt,
            artifact_raw=b"{}",
            trusted_public_key_raw=b"untrusted",
        )
