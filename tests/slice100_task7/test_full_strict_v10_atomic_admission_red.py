"""RED-first crash-safe atomic admission contracts for full-strict-v10."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
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
_V10_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v10_reviewed_preimage_v1.json"
_V10_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v10_contract_fixture.py"
_REPO_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v10_contract_hash_v1.json"
)

_CENSUS_ENVELOPE_SCHEMA = "task7-v10-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA = "task7-v10-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN = "task7-v10-postgres-census-signable-v1"
_HARNESS_IDENTITY = "task7-v10-current-guard-pre-freeze-artifact-sql-on-0037-v1"
_HARNESS_SHA256 = hashlib.sha256(_HARNESS_IDENTITY.encode("utf-8")).hexdigest()


def _load_generator(name: str) -> ModuleType:
    assert _V10_GENERATOR.is_file()
    spec = importlib.util.spec_from_file_location(name, _V10_GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


def _candidate_bytes() -> bytes:
    raw = _V10_ARTIFACT.read_bytes()
    artifact = json.loads(raw)
    assert artifact["metadata"]["append_frozen"] is False
    return raw


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
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    return {
        "schema_version": _CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": candidate["metadata"]["artifact_schema"],
        "pipeline_contract_version": "full-strict-v10",
        "candidate_artifact_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(frozen_raw).hexdigest(),
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
    signer_id = "task7-v10-test-census-signer-v1"
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
    return _render(receipt), payload


def _keypair() -> tuple[Ed25519PrivateKey, bytes]:
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_key_raw


def _write_candidate(tmp_path: Path) -> tuple[Path, Path, bytes]:
    candidate_raw = _candidate_bytes()
    artifact_path = tmp_path / _V10_ARTIFACT.name
    fixture_path = tmp_path / _REPO_FIXTURE.name
    artifact_path.write_bytes(candidate_raw)
    return artifact_path, fixture_path, candidate_raw


def _admit(
    *,
    generator: ModuleType,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    public_key_raw: bytes,
) -> dict[str, Any]:
    return generator.admit_candidate_atomically(
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        receipt_raw=receipt_raw,
        trusted_public_key_raw=public_key_raw,
    )


def _expected_fixture(
    *,
    generator: ModuleType,
    frozen_raw: bytes,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    expected = generator.derive_contract_bytes(frozen_raw, validate=True)
    expected["postgres_authority_census"] = payload["database_execution"]
    return expected, generator._render_fixture(expected).encode("utf-8")


def _valid_admission_inputs(
    tmp_path: Path,
    name: str,
) -> tuple[
    ModuleType,
    Path,
    Path,
    bytes,
    bytes,
    dict[str, Any],
]:
    generator = _load_generator(name)
    artifact_path, fixture_path, candidate_raw = _write_candidate(tmp_path)
    private_key, public_key_raw = _keypair()
    receipt_raw, payload = _signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )
    return (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        {"payload": payload, "public_key_raw": public_key_raw},
    )


def _assert_terminal(
    *,
    generator: ModuleType,
    artifact_path: Path,
    fixture_path: Path,
    candidate_raw: bytes,
    payload: dict[str, Any],
) -> None:
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    expected, expected_raw = _expected_fixture(
        generator=generator,
        frozen_raw=frozen_raw,
        payload=payload,
    )
    assert artifact_path.read_bytes() == frozen_raw
    assert fixture_path.read_bytes() == expected_raw
    assert json.loads(fixture_path.read_text(encoding="utf-8")) == expected


def _write_child_inputs(
    *,
    tmp_path: Path,
    receipt_raw: bytes,
    public_key_raw: bytes,
) -> tuple[Path, Path]:
    receipt_path = tmp_path / "census-receipt.json"
    public_key_path = tmp_path / "trusted-public.pem"
    receipt_path.write_bytes(receipt_raw)
    public_key_path.write_bytes(public_key_raw)
    return receipt_path, public_key_path


def test_v10_repository_candidate_is_unfrozen_and_exact_fixture_is_absent() -> None:
    assert (
        json.loads(_V10_ARTIFACT.read_text(encoding="utf-8"))["metadata"]["append_frozen"] is False
    )
    assert not _REPO_FIXTURE.exists()


def test_v10_signed_receipt_binds_candidate_frozen_state_and_0037_harness() -> None:
    generator = _load_generator("_task7_v10_atomic_receipt_binding")
    candidate_raw = _candidate_bytes()
    private_key, public_key_raw = _keypair()
    receipt_raw, expected = _signed_receipt(
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

    assert payload == expected
    assert payload["database_execution"]["base_schema_revision"] == "0037"
    assert payload["database_execution"]["harness_sha256"] == _HARNESS_SHA256
    assert (
        payload["prospective_frozen_artifact_sha256"]
        == hashlib.sha256(generator.derive_prospective_frozen_bytes(candidate_raw)).hexdigest()
    )


def _wrong_candidate_binding(payload: dict[str, Any]) -> None:
    payload["candidate_artifact_sha256"] = "0" * 64


def _incomplete_census(payload: dict[str, Any]) -> None:
    payload["database_execution"]["direct_sql_rejection_count"] = 72


@pytest.mark.parametrize(
    "defect",
    (
        "missing",
        "bad_candidate_binding",
        "incorrect_signature",
        "incomplete_census",
    ),
)
def test_v10_invalid_receipt_has_zero_mutation(
    tmp_path: Path,
    defect: str,
) -> None:
    generator = _load_generator(f"_task7_v10_atomic_invalid_{defect}")
    artifact_path, fixture_path, candidate_raw = _write_candidate(tmp_path)
    private_key, public_key_raw = _keypair()
    mutation = (
        _wrong_candidate_binding
        if defect == "bad_candidate_binding"
        else _incomplete_census
        if defect == "incomplete_census"
        else None
    )
    signing_key = Ed25519PrivateKey.generate() if defect == "incorrect_signature" else None
    receipt_raw, _ = _signed_receipt(
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


def test_v10_existing_fixture_is_exclusive_and_never_overwritten(
    tmp_path: Path,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(tmp_path, "_task7_v10_existing_fixture")
    sentinel = b"pre-existing-fixture"
    fixture_path.write_bytes(sentinel)

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V10_FIXTURE_OUTPUT_ALREADY_EXISTS",
    ):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )

    assert artifact_path.read_bytes() == candidate_raw
    assert fixture_path.read_bytes() == sentinel


def test_v10_valid_receipt_publishes_fixture_first_and_artifact_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(tmp_path, "_task7_v10_publish_order")
    real_replace = os.replace
    destinations: list[Path] = []

    def record_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        destinations.append(Path(destination))
        real_replace(source, destination)

    monkeypatch.setattr(generator.os, "replace", record_replace)
    _admit(
        generator=generator,
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        receipt_raw=receipt_raw,
        public_key_raw=context["public_key_raw"],
    )

    assert destinations[:2] == [fixture_path, artifact_path]
    _assert_terminal(
        generator=generator,
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        candidate_raw=candidate_raw,
        payload=context["payload"],
    )


def test_v10_caught_artifact_publish_failure_rolls_back_to_candidate_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(tmp_path, "_task7_v10_caught_second_publish")
    real_replace = os.replace
    destinations: list[Path] = []

    def fail_second_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        destinations.append(Path(destination))
        if len(destinations) == 2:
            raise OSError("injected artifact commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(generator.os, "replace", fail_second_replace)
    with pytest.raises(generator.ContractArtifactError):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )

    assert destinations[:2] == [fixture_path, artifact_path]
    assert artifact_path.read_bytes() == candidate_raw
    assert not fixture_path.exists()


def test_v10_systemexit_after_fixture_publish_recovers_with_same_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(tmp_path, "_task7_v10_systemexit_recovery")
    real_replace = os.replace
    publications = 0

    def exit_after_fixture(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        nonlocal publications
        real_replace(source, destination)
        publications += 1
        if publications == 1:
            raise SystemExit(73)

    monkeypatch.setattr(generator.os, "replace", exit_after_fixture)
    with pytest.raises(SystemExit, match="73"):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )

    assert artifact_path.read_bytes() == candidate_raw
    assert fixture_path.is_file()
    monkeypatch.setattr(generator.os, "replace", real_replace)

    _admit(
        generator=generator,
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        receipt_raw=receipt_raw,
        public_key_raw=context["public_key_raw"],
    )
    _assert_terminal(
        generator=generator,
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        candidate_raw=candidate_raw,
        payload=context["payload"],
    )


@pytest.mark.parametrize("defect", ("bad_signature", "wrong_trust_root"))
def test_v10_intermediate_fixture_recovery_requires_same_valid_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(
        tmp_path,
        f"_task7_v10_intermediate_recovery_{defect}",
    )
    real_replace = os.replace
    publications = 0

    def exit_after_fixture(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        nonlocal publications
        real_replace(source, destination)
        publications += 1
        if publications == 1:
            raise SystemExit(74)

    monkeypatch.setattr(generator.os, "replace", exit_after_fixture)
    with pytest.raises(SystemExit, match="74"):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )
    monkeypatch.setattr(generator.os, "replace", real_replace)
    fixture_raw = fixture_path.read_bytes()

    invalid_receipt = receipt_raw
    public_key_raw = context["public_key_raw"]
    if defect == "bad_signature":
        receipt = json.loads(receipt_raw)
        receipt["signature_base64"] = base64.b64encode(b"\0" * 64).decode()
        invalid_receipt = _render(receipt)
    else:
        _, public_key_raw = _keypair()

    with pytest.raises(generator.ContractArtifactError):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=invalid_receipt,
            public_key_raw=public_key_raw,
        )

    assert artifact_path.read_bytes() == candidate_raw
    assert fixture_path.read_bytes() == fixture_raw


def test_v10_terminal_recovery_requires_exact_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        _candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(tmp_path, "_task7_v10_terminal_journal_recovery")
    real_replace = os.replace
    publications = 0

    def exit_after_artifact(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        nonlocal publications
        real_replace(source, destination)
        publications += 1
        if publications == 2:
            raise SystemExit(75)

    monkeypatch.setattr(generator.os, "replace", exit_after_artifact)
    with pytest.raises(SystemExit, match="75"):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )
    monkeypatch.setattr(generator.os, "replace", real_replace)

    artifact_raw = artifact_path.read_bytes()
    fixture_raw = fixture_path.read_bytes()
    journal_path = generator._journal_path(artifact_path, fixture_path)
    corrupt_journal_raw = journal_path.read_bytes() + b" "
    journal_path.write_bytes(corrupt_journal_raw)

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V10_RECOVERY_JOURNAL_MISMATCH",
    ):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )

    assert artifact_path.read_bytes() == artifact_raw
    assert fixture_path.read_bytes() == fixture_raw
    assert journal_path.read_bytes() == corrupt_journal_raw


def test_v10_unjournaled_frozen_without_fixture_is_not_admitted(
    tmp_path: Path,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(tmp_path, "_task7_v10_legacy_frozen_recovery")
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    artifact_path.write_bytes(frozen_raw)

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V10_RECOVERY_JOURNAL_REQUIRED",
    ):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )

    assert artifact_path.read_bytes() == frozen_raw
    assert not fixture_path.exists()
    assert not generator._journal_path(artifact_path, fixture_path).exists()


def test_v10_frozen_without_fixture_recovers_only_with_exact_preexisting_journal(
    tmp_path: Path,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(tmp_path, "_task7_v10_journaled_frozen_recovery")
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    artifact_path.write_bytes(frozen_raw)
    _expected, fixture_raw = _expected_fixture(
        generator=generator,
        frozen_raw=frozen_raw,
        payload=context["payload"],
    )
    journal_path = generator._journal_path(artifact_path, fixture_path)
    journal_path.write_bytes(
        generator._journal_bytes(
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            candidate_raw=candidate_raw,
            frozen_raw=frozen_raw,
            fixture_raw=fixture_raw,
            receipt_raw=receipt_raw,
            trusted_public_key_raw=context["public_key_raw"],
        )
    )

    _admit(
        generator=generator,
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        receipt_raw=receipt_raw,
        public_key_raw=context["public_key_raw"],
    )

    _assert_terminal(
        generator=generator,
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        candidate_raw=candidate_raw,
        payload=context["payload"],
    )
    assert not journal_path.exists()


def test_v10_corrupt_frozen_artifact_never_enters_recovery(
    tmp_path: Path,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(tmp_path, "_task7_v10_corrupt_frozen")
    frozen = json.loads(generator.derive_prospective_frozen_bytes(candidate_raw))
    frozen["metadata"]["pipeline_contract_version"] = "full-strict-v10-corrupt"
    corrupt_raw = _render(frozen)
    artifact_path.write_bytes(corrupt_raw)

    with pytest.raises(generator.ContractArtifactError):
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )

    assert artifact_path.read_bytes() == corrupt_raw
    assert not fixture_path.exists()


@pytest.mark.parametrize("exit_boundary", (1, 2))
def test_v10_real_child_os_exit_after_each_publish_boundary(
    tmp_path: Path,
    exit_boundary: int,
) -> None:
    case = tmp_path / f"boundary-{exit_boundary}"
    case.mkdir()
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(
        case,
        f"_task7_v10_child_boundary_parent_{exit_boundary}",
    )
    receipt_path, public_key_path = _write_child_inputs(
        tmp_path=case,
        receipt_raw=receipt_raw,
        public_key_raw=context["public_key_raw"],
    )
    child_code = f"""
import importlib.util
import os
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
s = importlib.util.spec_from_file_location("_task7_v10_child", p)
m = importlib.util.module_from_spec(s)
sys.modules[s.name] = m
s.loader.exec_module(m)
real = m.os.replace
count = [0]

def injected(src, dst):
    real(src, dst)
    count[0] += 1
    if count[0] == {exit_boundary}:
        os._exit({80 + exit_boundary})

m.os.replace = injected
m.admit_candidate_atomically(
    artifact_path=pathlib.Path(sys.argv[2]),
    fixture_path=pathlib.Path(sys.argv[3]),
    receipt_raw=pathlib.Path(sys.argv[4]).read_bytes(),
    trusted_public_key_raw=pathlib.Path(sys.argv[5]).read_bytes(),
)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            str(_V10_GENERATOR),
            str(artifact_path),
            str(fixture_path),
            str(receipt_path),
            str(public_key_path),
        ],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 80 + exit_boundary

    if exit_boundary == 1:
        assert artifact_path.read_bytes() == candidate_raw
        assert fixture_path.is_file()
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )
    else:
        journal_path = generator._journal_path(artifact_path, fixture_path)
        artifact_stage = generator._stage_path(artifact_path)
        fixture_stage = generator._stage_path(fixture_path)
        terminal_artifact_raw = artifact_path.read_bytes()
        terminal_fixture_raw = fixture_path.read_bytes()
        assert journal_path.is_file()
        _admit(
            generator=generator,
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            receipt_raw=receipt_raw,
            public_key_raw=context["public_key_raw"],
        )
        assert artifact_path.read_bytes() == terminal_artifact_raw
        assert fixture_path.read_bytes() == terminal_fixture_raw
        assert not any(path.exists() for path in (artifact_stage, fixture_stage, journal_path))
        with pytest.raises(
            generator.ContractArtifactError,
            match="TASK7_V10_FIXTURE_OUTPUT_ALREADY_EXISTS",
        ):
            _admit(
                generator=generator,
                artifact_path=artifact_path,
                fixture_path=fixture_path,
                receipt_raw=receipt_raw,
                public_key_raw=context["public_key_raw"],
            )
        assert artifact_path.read_bytes() == terminal_artifact_raw
        assert fixture_path.read_bytes() == terminal_fixture_raw
        assert not journal_path.exists()
    _assert_terminal(
        generator=generator,
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        candidate_raw=candidate_raw,
        payload=context["payload"],
    )


def test_v10_concurrent_admission_has_exactly_one_commit_winner(
    tmp_path: Path,
) -> None:
    (
        generator,
        artifact_path,
        fixture_path,
        candidate_raw,
        receipt_raw,
        context,
    ) = _valid_admission_inputs(tmp_path, "_task7_v10_concurrency_parent")
    receipt_path, public_key_path = _write_child_inputs(
        tmp_path=tmp_path,
        receipt_raw=receipt_raw,
        public_key_raw=context["public_key_raw"],
    )
    gate_path = tmp_path / "start.gate"
    child_code = (
        "import pathlib,subprocess,sys,time;"
        "gate=pathlib.Path(sys.argv[1]);"
        "\nwhile not gate.exists(): time.sleep(0.005)\n"
        "raise SystemExit(subprocess.run(sys.argv[2:],check=False).returncode)"
    )
    command = [
        sys.executable,
        "-c",
        child_code,
        str(gate_path),
        sys.executable,
        str(_V10_GENERATOR),
        "--artifact",
        str(artifact_path),
        "--output",
        str(fixture_path),
        "--authority-census-receipt",
        str(receipt_path),
        "--trusted-public-key",
        str(public_key_path),
    ]
    children = [
        subprocess.Popen(
            command,
            cwd=_REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    gate_path.write_text("go\n", encoding="ascii")
    outcomes = [child.communicate(timeout=30) + (child.returncode,) for child in children]

    assert sorted(outcome[2] for outcome in outcomes) == [0, 2]
    _assert_terminal(
        generator=generator,
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        candidate_raw=candidate_raw,
        payload=context["payload"],
    )
