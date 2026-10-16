"""RED-first signed-census authorization for full-strict-v19 publication."""

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
_AUTHORIZER = _REPO_ROOT / "scripts" / "authorize_task7_v19_contract_publication.py"
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v19_current_guard_census.py"
_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v19_contract_fixture.py"
_CANDIDATE_SHA256 = "21bb93882a2d316bbf899cdae282c231b99a1aeea2239c2966954bfa7f4789c4"
_TRUSTED_KEY_SHA256 = "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"


def _load(name: str) -> ModuleType:
    assert _AUTHORIZER.is_file(), "TASK7_V19_PUBLICATION_AUTHORIZER_NOT_IMPLEMENTED"
    spec = importlib.util.spec_from_file_location(name, _AUTHORIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _artifact() -> tuple[bytes, dict[str, Any]]:
    raw = _ARTIFACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _CANDIDATE_SHA256
    value = json.loads(raw)
    assert value["metadata"]["append_frozen"] is False
    return raw, value


class _FakeRunner:
    @staticmethod
    def validate_execution_contract(*, result: object, artifact: object) -> None:
        assert isinstance(result, dict)
        assert isinstance(artifact, dict)
        assert "downgrade_retained_definition_execution_proof" in result
        assert "erasure_authority_execution_proof" in result

    @staticmethod
    def _database_execution(result: object, artifact: object) -> dict[str, Any]:
        assert isinstance(result, dict)
        assert isinstance(artifact, dict)
        return {
            "complete_execution_sha256": result["complete_execution_sha256"],
            "downgrade_retained_definition_execution_proof_sha256": "d" * 64,
            "erasure_authority_execution_proof_sha256": "e" * 64,
            "materialization_source_manifest_sha256": result[
                "materialization_source_manifest_sha256"
            ],
        }


def _payload(authorizer: ModuleType) -> tuple[dict[str, Any], dict[str, Any]]:
    raw, artifact = _artifact()
    frozen = authorizer.derive_prospective_frozen_bytes(raw)
    result = {
        "complete_execution_sha256": "a" * 64,
        "materialization_source_manifest_sha256": "b" * 64,
        "downgrade_retained_definition_execution_proof": {"execution_sha256": "c" * 64},
        "erasure_authority_execution_proof": {"execution_sha256": "d" * 64},
    }
    payload = {
        "schema_version": "task7-v19-postgres-census-v1",
        "result": "PASS",
        "artifact_schema": "full-strict-v19-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v19",
        "candidate_artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(frozen).hexdigest(),
        "database_execution": _FakeRunner._database_execution(result, artifact),
        "census_result": result,
    }
    return artifact, payload


def test_v19_authorizer_pins_candidate_runner_generator_and_trust_root() -> None:
    authorizer = _load("_task7_v19_authorizer_pins_red")

    assert authorizer._V19_ARTIFACT_SHA256 == _CANDIDATE_SHA256
    assert hashlib.sha256(_RUNNER.read_bytes()).hexdigest() == (
        authorizer._EXPECTED_V19_RUNNER_SHA256
    )
    assert hashlib.sha256(_GENERATOR.read_bytes()).hexdigest() == (
        authorizer._V19_ADMISSION_GENERATOR_SHA256
    )
    assert authorizer._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 == _TRUSTED_KEY_SHA256


def test_v19_fixture_derivation_is_deterministic_version_bound_and_does_not_publish() -> None:
    authorizer = _load("_task7_v19_authorizer_derivation_red")
    raw, _value = _artifact()

    frozen = authorizer.derive_prospective_frozen_bytes(raw)
    assert _ARTIFACT.read_bytes() == raw
    assert json.loads(raw)["metadata"]["append_frozen"] is False
    assert json.loads(frozen)["metadata"]["append_frozen"] is True

    fixture = authorizer.derive_contract_bytes(frozen)
    assert fixture["fixture_schema"] == "full-strict-v19-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == "full-strict-v19"
    assert fixture["artifact_sha256"] == hashlib.sha256(frozen).hexdigest()
    assert (
        fixture["FULL_STRICT_V19_CONTRACT_HASH"]
        == fixture["component_hashes"]["pipeline_contract_hash"]
    )
    assert authorizer.derive_contract_bytes(frozen) == fixture
    assert _ARTIFACT.read_bytes() == raw


def test_v19_authorization_requires_both_proofs_and_exact_database_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load("_task7_v19_authorizer_both_proofs_red")
    artifact, payload = _payload(authorizer)
    monkeypatch.setattr(authorizer, "_runner", lambda: _FakeRunner)

    authorization = authorizer.authorize_fixture_publication(
        candidate_artifact=artifact,
        verified_census_payload=payload,
    )
    assert json.loads(authorization["frozen_artifact_bytes"])["metadata"]["append_frozen"] is True
    assert authorization["fixture"]["postgres_authority_census"] == payload["database_execution"]
    assert "census_result" not in authorization["fixture"]

    for missing in (
        "downgrade_retained_definition_execution_proof",
        "erasure_authority_execution_proof",
    ):
        changed = copy.deepcopy(payload)
        changed["census_result"].pop(missing)
        with pytest.raises(
            authorizer.ContractPublicationError,
            match="TASK7_V19_POSTGRES_CENSUS_RECEIPT_INVALID",
        ):
            authorizer.authorize_fixture_publication(
                candidate_artifact=artifact,
                verified_census_payload=changed,
            )


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-payload",
        "wrong-version",
        "wrong-candidate",
        "wrong-frozen",
        "database-projection-drift",
    ),
)
def test_v19_publication_authorization_fails_closed_for_receipt_drift(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load(f"_task7_v19_authorizer_reject_{mutation}")
    artifact, payload = _payload(authorizer)
    monkeypatch.setattr(authorizer, "_runner", lambda: _FakeRunner)
    changed: dict[str, Any] | None = copy.deepcopy(payload)
    if mutation == "missing-payload":
        changed = None
    elif mutation == "wrong-version":
        assert changed is not None
        changed["pipeline_contract_version"] = "full-strict-v18"
    elif mutation == "wrong-candidate":
        assert changed is not None
        changed["candidate_artifact_sha256"] = "0" * 64
    elif mutation == "wrong-frozen":
        assert changed is not None
        changed["prospective_frozen_artifact_sha256"] = "0" * 64
    else:
        assert changed is not None
        changed["database_execution"]["erasure_authority_execution_proof_sha256"] = "0" * 64

    with pytest.raises(
        authorizer.ContractPublicationError,
        match=(
            "TASK7_V19_POSTGRES_CENSUS_RECEIPT_REQUIRED"
            if changed is None
            else "TASK7_V19_POSTGRES_CENSUS_RECEIPT_INVALID"
        ),
    ):
        authorizer.authorize_fixture_publication(
            candidate_artifact=artifact,
            verified_census_payload=changed,
        )


@pytest.mark.parametrize("live_candidate_state", ("missing", "frozen"))
def test_real_v19_authorizer_revalidates_receipt_bytes_after_candidate_move_or_commit(
    live_candidate_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer = _load(f"_task7_v19_authorizer_recovery_{live_candidate_state}")
    runner = authorizer._runner()
    artifact = {"metadata": {"append_frozen": False}}
    artifact_raw = (json.dumps(artifact, indent=2) + "\n").encode()
    frozen_raw = (json.dumps({"metadata": {"append_frozen": True}}, indent=2) + "\n").encode()
    helper_raw = b"pinned helper\n"
    artifact_relative = "contracts/candidate.json"
    helper_relative = "scripts/helper.py"
    helper_path = tmp_path / helper_relative
    helper_path.parent.mkdir(parents=True)
    helper_path.write_bytes(helper_raw)
    if live_candidate_state == "frozen":
        live_artifact = tmp_path / artifact_relative
        live_artifact.parent.mkdir(parents=True)
        live_artifact.write_bytes(frozen_raw)
    manifest = (
        (
            artifact_relative,
            len(artifact_raw),
            hashlib.sha256(artifact_raw).hexdigest(),
            "unfrozen-candidate",
        ),
        (
            helper_relative,
            len(helper_raw),
            hashlib.sha256(helper_raw).hexdigest(),
            "helper",
        ),
    )
    monkeypatch.setattr(runner, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "_V19_MATERIALIZATION_SOURCE_MANIFEST", manifest)
    monkeypatch.setattr(
        runner,
        "_V19_CANDIDATE_SHA256",
        hashlib.sha256(artifact_raw).hexdigest(),
    )
    monkeypatch.setattr(runner, "_validate_v19_candidate", lambda value: dict(value))
    closure = runner.verify_receipt_materialization_source_closure(artifact_raw)
    payload = {
        "census_result": {
            **closure,
            "downgrade_retained_definition_execution_proof": {},
            "erasure_authority_execution_proof": {},
        }
    }

    class _RecoveryRunner:
        verify_receipt_materialization_source_closure = staticmethod(
            runner.verify_receipt_materialization_source_closure
        )

        @staticmethod
        def verify_signed_receipt(**_kwargs: object) -> dict[str, Any]:
            return payload

        @staticmethod
        def validate_execution_contract(**_kwargs: object) -> None:
            return None

    key = b"test public key"
    monkeypatch.setattr(authorizer, "_runner", lambda: _RecoveryRunner)
    monkeypatch.setattr(
        authorizer,
        "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256",
        hashlib.sha256(key).hexdigest(),
    )
    receipt = json.dumps({"signer_id": "slice100-governance-signer-v1"}).encode()

    assert (
        authorizer.verify_postgres_census_receipt(
            receipt_raw=receipt,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=key,
        )
        == payload
    )
