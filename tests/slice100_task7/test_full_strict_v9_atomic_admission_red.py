"""RED-first contracts for full-strict-v9's atomic append-freeze gate.

The executable PostgreSQL census runs over the unfrozen candidate on a
disposable database at revision 0037 plus the exact artifact-SQL harness.  One
signed receipt binds both those candidate bytes and the bytes that would result
from changing only ``metadata.append_frozen`` to ``true``.  Admission then
publishes the frozen artifact and its derived fixture as one recoverable file
transaction; migration 0038 is deliberately not part of this pre-freeze proof.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V9_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v9_reviewed_preimage_v1.json"
_V9_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v9_contract_fixture.py"

_CENSUS_ENVELOPE_SCHEMA = "task7-v9-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA = "task7-v9-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN = "task7-v9-postgres-census-signable-v1"
_HARNESS_IDENTITY = "task7-v9-current-guard-pre-freeze-artifact-sql-on-0037-v1"
_HARNESS_SHA256 = hashlib.sha256(_HARNESS_IDENTITY.encode("utf-8")).hexdigest()


def _load_generator(name: str) -> ModuleType:
    assert _V9_GENERATOR.is_file()
    spec = importlib.util.spec_from_file_location(name, _V9_GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_artifact(artifact: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            artifact,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _unfrozen_candidate_bytes() -> bytes:
    artifact = json.loads(_V9_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(artifact, dict)
    artifact["metadata"]["append_frozen"] = False
    return _render_artifact(artifact)


def _prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    artifact = json.loads(candidate_raw)
    assert isinstance(artifact, dict)
    assert artifact["metadata"]["append_frozen"] is False
    artifact["metadata"]["append_frozen"] = True
    return _render_artifact(artifact)


def _database_execution() -> dict[str, Any]:
    return {
        "database_product": "PostgreSQL",
        "server_version_num": 160010,
        "base_schema_revision": "0037",
        "harness_identity": _HARNESS_IDENTITY,
        "harness_sha256": _HARNESS_SHA256,
        "current_guard_target_count": 73,
        "legal_execution_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "constructor_schema_proof_count": 4,
        "unexpected_outcome_count": 0,
        "catalog_projection_sha256": "a" * 64,
        "case_result_sha256": "b" * 64,
        "constructor_roundtrip_sha256": "c" * 64,
        "manifest_parity_sha256": "d" * 64,
    }


def _payload(
    *,
    generator: ModuleType,
    candidate_raw: bytes,
) -> dict[str, Any]:
    candidate = generator._decode_artifact(candidate_raw)
    prospective_raw = _prospective_frozen_bytes(candidate_raw)
    return {
        "schema_version": _CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": candidate["metadata"]["artifact_schema"],
        "pipeline_contract_version": "full-strict-v9",
        "candidate_artifact_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(prospective_raw).hexdigest(),
        "authority_inventory": generator.derive_authority_inventory(candidate),
        "database_execution": _database_execution(),
    }


def _signed_receipt(
    *,
    generator: ModuleType,
    candidate_raw: bytes,
    private_key: Ed25519PrivateKey,
    public_key_raw: bytes,
    mutate_payload: Callable[[dict[str, Any]], None] | None = None,
    signing_key: Ed25519PrivateKey | None = None,
) -> tuple[bytes, dict[str, Any]]:
    payload = _payload(generator=generator, candidate_raw=candidate_raw)
    if mutate_payload is not None:
        mutate_payload(payload)
    canonical = generator._canonical_bytes
    payload_sha256 = hashlib.sha256(canonical(payload)).hexdigest()
    public_key_sha256 = hashlib.sha256(public_key_raw).hexdigest()
    signer_id = "task7-v9-test-census-signer-v1"
    signature = (signing_key or private_key).sign(
        canonical(
            [
                _CENSUS_SIGNING_DOMAIN,
                signer_id,
                payload_sha256,
                public_key_sha256,
                payload,
            ]
        )
    )
    receipt = {
        "schema_version": _CENSUS_ENVELOPE_SCHEMA,
        "signer_id": signer_id,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_key_sha256,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    return _render_artifact(receipt), payload


def _keypair() -> tuple[Ed25519PrivateKey, bytes]:
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_key_raw


def _admit(
    *,
    generator: ModuleType,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    public_key_raw: bytes,
) -> None:
    admit = getattr(generator, "admit_candidate_atomically", None)
    assert callable(admit), (
        "v9 fixture generator lacks the approved atomic candidate admission operation"
    )
    admit(
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        receipt_raw=receipt_raw,
        trusted_public_key_raw=public_key_raw,
    )


def _write_candidate(tmp_path: Path) -> tuple[Path, Path, bytes]:
    candidate_raw = _unfrozen_candidate_bytes()
    artifact_path = tmp_path / "full_strict_v9_candidate.json"
    fixture_path = tmp_path / "full_strict_v9_fixture.json"
    artifact_path.write_bytes(candidate_raw)
    return artifact_path, fixture_path, candidate_raw


def test_v9_prefreeze_candidate_has_no_admissible_fixture(
    tmp_path: Path,
) -> None:
    artifact_path, fixture_path, candidate_raw = _write_candidate(tmp_path)

    assert json.loads(candidate_raw)["metadata"]["append_frozen"] is False
    assert artifact_path.read_bytes() == candidate_raw
    assert not fixture_path.exists()


def test_v9_census_receipt_binds_0037_harness_and_both_artifact_states() -> None:
    generator = _load_generator("_task7_v9_atomic_receipt_binding_red")
    candidate_raw = _unfrozen_candidate_bytes()
    private_key, public_key_raw = _keypair()
    receipt_raw, expected_payload = _signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )

    payload = generator.verify_postgres_census_receipt(
        receipt_raw=receipt_raw,
        artifact_raw=candidate_raw,
        trusted_public_key_raw=public_key_raw,
    )

    assert payload == expected_payload
    assert payload["database_execution"]["base_schema_revision"] == "0037"
    assert payload["database_execution"]["harness_identity"] == _HARNESS_IDENTITY
    assert payload["database_execution"]["harness_sha256"] == _HARNESS_SHA256
    assert payload["candidate_artifact_sha256"] == hashlib.sha256(candidate_raw).hexdigest()
    assert (
        payload["prospective_frozen_artifact_sha256"]
        == hashlib.sha256(_prospective_frozen_bytes(candidate_raw)).hexdigest()
    )


def _wrong_candidate_binding(payload: dict[str, Any]) -> None:
    payload["candidate_artifact_sha256"] = "0" * 64


def _incomplete_execution(payload: dict[str, Any]) -> None:
    payload["database_execution"]["direct_sql_rejection_count"] = 72


@pytest.mark.parametrize(
    "defect",
    (
        "missing",
        "bad_candidate_binding",
        "incorrect_signature",
        "incomplete_execution",
    ),
)
def test_v9_invalid_census_never_mutates_candidate_or_emits_fixture(
    tmp_path: Path,
    defect: str,
) -> None:
    generator = _load_generator(f"_task7_v9_atomic_rejection_{defect}_red")
    artifact_path, fixture_path, candidate_raw = _write_candidate(tmp_path)
    private_key, public_key_raw = _keypair()
    mutation = (
        _wrong_candidate_binding
        if defect == "bad_candidate_binding"
        else _incomplete_execution
        if defect == "incomplete_execution"
        else None
    )
    signing_key = Ed25519PrivateKey.generate() if defect == "incorrect_signature" else None
    receipt_raw, _payload_value = _signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
        mutate_payload=mutation,
        signing_key=signing_key,
    )
    if defect == "missing":
        receipt_raw = b""

    with pytest.raises(generator.ContractArtifactError):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert artifact_path.read_bytes() == candidate_raw
    assert not fixture_path.exists()
    assert {path.name for path in tmp_path.iterdir()} == {artifact_path.name}


def test_v9_valid_receipt_atomically_freezes_artifact_and_exact_fixture(
    tmp_path: Path,
) -> None:
    generator = _load_generator("_task7_v9_atomic_admission_green_target")
    artifact_path, fixture_path, candidate_raw = _write_candidate(tmp_path)
    private_key, public_key_raw = _keypair()
    receipt_raw, payload = _signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )

    _admit(
        generator=generator,
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )

    frozen_raw = artifact_path.read_bytes()
    assert frozen_raw == _prospective_frozen_bytes(candidate_raw)
    assert json.loads(frozen_raw)["metadata"]["append_frozen"] is True
    assert fixture_path.is_file()
    expected_fixture = generator.derive_contract_bytes(
        frozen_raw,
        validate=True,
    )
    expected_fixture["postgres_authority_census"] = payload["database_execution"]
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture == expected_fixture
    assert fixture["artifact_sha256"] == hashlib.sha256(frozen_raw).hexdigest()
    assert fixture["artifact_sha256"] != hashlib.sha256(candidate_raw).hexdigest()


def test_v9_second_publish_failure_rolls_back_both_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _load_generator("_task7_v9_atomic_second_publish_red")
    artifact_path, fixture_path, candidate_raw = _write_candidate(tmp_path)
    private_key, public_key_raw = _keypair()
    receipt_raw, _payload_value = _signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def fail_only_second_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        replacements.append((Path(source), Path(destination)))
        if len(replacements) == 2:
            raise OSError("injected second publish failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_only_second_replace)

    with pytest.raises(generator.ContractArtifactError):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert len(replacements) >= 2, "the admission did not reach both publishes"
    assert artifact_path.read_bytes() == candidate_raw
    assert not fixture_path.exists()
    assert {path.name for path in tmp_path.iterdir()} == {artifact_path.name}
