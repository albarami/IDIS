"""RED-first signed, crash-safe atomic admission contracts for v16."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from tests.slice100_task7 import _v16_atomic_admission_support as support

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_task7_v16_contract_fixture.py"
ARTIFACT_PATH = REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
FIXTURE_PATH = (
    REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v16_contract_hash_v1.json"
)
_GENERATOR_MODULE: ModuleType | None = None


def _load_generator(name: str) -> ModuleType:
    global _GENERATOR_MODULE
    del name
    if _GENERATOR_MODULE is not None:
        return _GENERATOR_MODULE
    module_name = "_task7_v16_atomic_admission_generator_under_test"
    spec = importlib.util.spec_from_file_location(module_name, GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _GENERATOR_MODULE = module
    return module


def _candidate_copy(tmp_path: Path) -> tuple[Path, Path, bytes]:
    repository_raw = ARTIFACT_PATH.read_bytes()
    generator = _load_generator("_task7_v16_atomic_candidate_copy")
    repository_artifact = json.loads(repository_raw)
    candidate_raw = (
        generator._candidate_from_frozen(repository_raw)
        if repository_artifact["metadata"]["append_frozen"] is True
        else repository_raw
    )
    assert json.loads(candidate_raw)["metadata"]["append_frozen"] is False
    artifact = tmp_path / ARTIFACT_PATH.name
    fixture = tmp_path / FIXTURE_PATH.name
    artifact.write_bytes(candidate_raw)
    return artifact, fixture, candidate_raw


def _valid_inputs(
    tmp_path: Path,
    name: str,
) -> tuple[ModuleType, Path, Path, bytes, bytes, bytes, dict[str, Any]]:
    generator = _load_generator(name)
    artifact, fixture, candidate_raw = _candidate_copy(tmp_path)
    private_key, public_key_raw = support.keypair()
    generator._EXPECTED_CENSUS_SIGNER_ID = "task7-v16-test-census-signer-v1"
    generator._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 = hashlib.sha256(public_key_raw).hexdigest()
    receipt_raw, payload = support.signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )
    return (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        payload,
    )


def test_v16_repository_targets_are_atomically_frozen_but_inactive() -> None:
    artifact = json.loads(ARTIFACT_PATH.read_bytes())
    fixture_raw = FIXTURE_PATH.read_bytes()
    fixture = json.loads(fixture_raw)

    # Freeze publication does not activate the separately gated runtime.
    assert artifact["metadata"]["append_frozen"] is True
    assert fixture["pipeline_contract_version"] == "full-strict-v16"
    assert fixture["artifact_sha256"] == hashlib.sha256(ARTIFACT_PATH.read_bytes()).hexdigest()
    assert (
        fixture["FULL_STRICT_V16_CONTRACT_HASH"]
        == "dadf149672f80d66a913cb6459c2465d512adfb05ce8031624c9a5e725ac65f5"
    )


def test_v16_verifier_accepts_exact_six_eight_sixty_six_receipt(
    tmp_path: Path,
) -> None:
    (
        generator,
        _artifact,
        _fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        expected_payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_atomic_receipt_red")

    payload = generator.verify_postgres_census_receipt(
        receipt_raw=receipt_raw,
        artifact_raw=candidate_raw,
        trusted_public_key_raw=public_key_raw,
    )

    assert payload == expected_payload
    assert len(payload) == 8
    assert len(payload["census_result"]) == 66
    assert len(payload["database_execution"]) == 26


def _admit(
    *,
    generator: ModuleType,
    artifact: Path,
    fixture: Path,
    receipt_raw: bytes,
    public_key_raw: bytes,
) -> dict[str, Any]:
    return generator.admit_candidate_atomically(
        artifact_path=artifact,
        fixture_path=fixture,
        receipt_raw=receipt_raw,
        trusted_public_key_raw=public_key_raw,
    )


def _expected_outputs(
    *,
    generator: ModuleType,
    candidate_raw: bytes,
    payload: Mapping[str, Any],
) -> tuple[bytes, bytes, dict[str, Any]]:
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    fixture_value = generator.derive_contract_bytes(frozen_raw)
    base_fields = frozenset(fixture_value)
    fixture_value["postgres_authority_census"] = payload["database_execution"]
    assert frozenset(fixture_value) == base_fields | {"postgres_authority_census"}
    fixture_raw = generator._render_fixture(fixture_value).encode("utf-8")
    return frozen_raw, fixture_raw, fixture_value


def _sidecar_paths(
    *,
    generator: ModuleType,
    artifact: Path,
    fixture: Path,
) -> tuple[Path, Path, Path, Path]:
    return (
        generator._lock_path(artifact),
        generator._journal_path(artifact),
        generator._stage_path(artifact),
        generator._stage_path(fixture),
    )


def _snapshot_directory(path: Path) -> dict[str, bytes | None]:
    return {
        child.name: (child.read_bytes() if child.is_file() else None)
        for child in sorted(path.iterdir())
    }


def _resigned_semantic_tamper(
    *,
    generator: ModuleType,
    candidate_raw: bytes,
    mutator: Callable[[dict[str, Any]], None],
) -> tuple[bytes, bytes]:
    private_key, public_key_raw = support.keypair()
    generator._EXPECTED_CENSUS_SIGNER_ID = "task7-v16-test-census-signer-v1"
    generator._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 = hashlib.sha256(public_key_raw).hexdigest()
    receipt_raw, _payload = support.signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )
    receipt = json.loads(receipt_raw)
    mutator(receipt)
    return (
        support.resign(
            generator=generator,
            receipt=receipt,
            private_key=private_key,
            public_key_raw=public_key_raw,
            recompute_result=True,
        ),
        public_key_raw,
    )


def _wrong_semantic_count(receipt: dict[str, Any]) -> None:
    receipt["payload"]["census_result"]["application_case_execution_count"] = 218


def _fabricated_nested_marker(receipt: dict[str, Any]) -> None:
    result = receipt["payload"]["census_result"]
    rejected = next(row for row in result["case_outcome_tuple"] if row["case_kind"] == "ILLEGAL")
    rejected["authority_rejection_marker"] = "FABRICATED_AUTHORITY_MARKER"


@pytest.mark.parametrize(
    "mutator",
    (_wrong_semantic_count, _fabricated_nested_marker),
)
def test_v16_resigned_semantic_receipt_is_rejected_before_any_sidecar(
    tmp_path: Path,
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    generator = _load_generator(f"_task7_v16_resigned_{mutator.__name__}")
    artifact, fixture, candidate_raw = _candidate_copy(tmp_path)
    receipt_raw, public_key_raw = _resigned_semantic_tamper(
        generator=generator,
        candidate_raw=candidate_raw,
        mutator=mutator,
    )
    before = _snapshot_directory(tmp_path)

    with pytest.raises(
        generator.ContractArtifactError,
        match=r"^TASK7_V16_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert _snapshot_directory(tmp_path) == before


@pytest.mark.parametrize(
    "defect",
    (
        "signature",
        "unknown-envelope",
        "missing-envelope",
        "duplicate-envelope",
        "oversized",
        "malformed",
        "wrong-key",
        "non-ed25519-key",
        "blank-signer",
        "runner-hash",
    ),
)
def test_v16_malformed_or_untrusted_receipt_is_secret_safe_and_zero_mutation(
    tmp_path: Path,
    defect: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _load_generator(f"_task7_v16_receipt_defect_{defect}")
    artifact, fixture, candidate_raw = _candidate_copy(tmp_path)
    private_key, public_key_raw = support.keypair()
    generator._EXPECTED_CENSUS_SIGNER_ID = "task7-v16-test-census-signer-v1"
    generator._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 = hashlib.sha256(public_key_raw).hexdigest()
    receipt_raw, _payload = support.signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )
    receipt = json.loads(receipt_raw)
    supplied_key = public_key_raw
    if defect == "signature":
        signature = bytearray(base64.b64decode(receipt["signature_base64"]))
        signature[0] ^= 1
        receipt["signature_base64"] = base64.b64encode(signature).decode("ascii")
        receipt_raw = support.render(receipt)
    elif defect == "unknown-envelope":
        receipt["unknown"] = "must-not-be-accepted"
        receipt_raw = support.render(receipt)
    elif defect == "missing-envelope":
        del receipt["payload_sha256"]
        receipt_raw = support.render(receipt)
    elif defect == "duplicate-envelope":
        receipt_raw = receipt_raw.replace(
            b'{\n  "schema_version":',
            b'{\n  "schema_version": "duplicate",\n  "schema_version":',
            1,
        )
    elif defect == "oversized":
        receipt_raw = b"{" + b" " * (generator._MAX_CENSUS_RECEIPT_BYTES + 1)
    elif defect == "malformed":
        receipt_raw = b'{"private":"DO_NOT_ECHO","payload":'
    elif defect == "wrong-key":
        _wrong_private, supplied_key = support.keypair()
    elif defect == "non-ed25519-key":
        supplied_key = (
            generate_private_key(public_exponent=65537, key_size=2048)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
    elif defect == "blank-signer":
        receipt["signer_id"] = " "
        receipt_raw = support.resign(
            generator=generator,
            receipt=receipt,
            private_key=private_key,
            public_key_raw=public_key_raw,
        )
    elif defect == "runner-hash":
        monkeypatch.setattr(generator, "_CENSUS_RUNNER_SHA256", "0" * 64)
    else:  # pragma: no cover - parameter exhaustiveness
        raise AssertionError(defect)
    before = _snapshot_directory(tmp_path)

    with pytest.raises(generator.ContractArtifactError) as exc_info:
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=supplied_key,
        )

    assert str(exc_info.value).startswith("TASK7_V16_POSTGRES_CENSUS_RECEIPT_INVALID")
    assert "DO_NOT_ECHO" not in str(exc_info.value)
    assert _snapshot_directory(tmp_path) == before


def test_v16_runner_is_hash_pinned_and_cli_requires_four_inputs() -> None:
    generator = _load_generator("_task7_v16_atomic_cli_contract")
    source = GENERATOR_PATH.read_text(encoding="utf-8")
    assert generator._CENSUS_RUNNER_SHA256 == support.RUNNER_SHA256
    assert hashlib.sha256(support.RUNNER_PATH.read_bytes()).hexdigest() == (
        generator._CENSUS_RUNNER_SHA256
    )
    for switch in (
        "--artifact",
        "--output",
        "--authority-census-receipt",
        "--trusted-public-key",
    ):
        assert switch in source
    with pytest.raises(SystemExit):
        generator._parse_args(["--artifact", str(ARTIFACT_PATH)])


def test_v16_exact_attacker_receipt_and_paired_key_are_not_trust_authority(
    tmp_path: Path,
) -> None:
    generator = _load_generator("_task7_v16_paired_attacker_key")
    artifact, fixture, candidate_raw = _candidate_copy(tmp_path)
    attacker_private, attacker_public = support.keypair()
    receipt_raw, _payload = support.signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=attacker_private,
        public_key_raw=attacker_public,
    )
    generator._EXPECTED_CENSUS_SIGNER_ID = "slice100-governance-signer-v1"
    generator._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 = (
        "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
    )
    before = _snapshot_directory(tmp_path)

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=attacker_public,
        )

    assert _snapshot_directory(tmp_path) == before


def test_v16_wrong_signer_is_rejected_even_with_pinned_test_key(
    tmp_path: Path,
) -> None:
    generator = _load_generator("_task7_v16_wrong_signer")
    artifact, fixture, candidate_raw = _candidate_copy(tmp_path)
    private_key, public_key_raw = support.keypair()
    receipt_raw, _payload = support.signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )
    generator._EXPECTED_CENSUS_SIGNER_ID = "slice100-governance-signer-v1"
    generator._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 = hashlib.sha256(public_key_raw).hexdigest()
    before = _snapshot_directory(tmp_path)

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert _snapshot_directory(tmp_path) == before


@pytest.mark.parametrize("attack", ("module-injection", "path-reread-swap"))
def test_v16_runner_loader_executes_only_the_exact_hashed_buffer(
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    generator = _load_generator(f"_task7_v16_runner_exact_bytes_{attack}")
    fake = ModuleType("_task7_v16_atomic_admission_census_verifier")
    fake.__file__ = str(support.RUNNER_PATH)
    fake.verify_signed_receipt = lambda **_kwargs: {"attacker": True}
    monkeypatch.setattr(generator, "_CENSUS_RUNNER_MODULE", None)
    if attack == "module-injection":
        monkeypatch.setitem(
            sys.modules,
            "_task7_v16_atomic_admission_census_verifier",
            fake,
        )
    else:
        monkeypatch.setattr(generator, "_load_script", lambda *_args: fake)

    loaded = generator._load_census_runner()

    assert loaded is not fake
    source = inspect.getsource(generator._load_census_runner)
    assert "compile(" in source
    assert "_load_script" not in source
    assert "sys.modules.get" not in source


def test_v16_test_receipt_support_also_compiles_the_exact_hashed_buffer() -> None:
    source = inspect.getsource(support.load_runner)
    assert "compile(" in source
    assert "spec_from_file_location" not in source


def test_v16_valid_receipt_publishes_fixture_first_and_artifact_as_commit_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_artifact_before = ARTIFACT_PATH.read_bytes()
    repository_fixture_before = FIXTURE_PATH.read_bytes()
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_atomic_success")
    events: list[str] = []
    real_fixture_publish = generator._atomic_publish_no_clobber
    real_artifact_publish = generator._replace_artifact_stage

    def record_fixture(stage: Path, target: Path) -> None:
        events.append("fixture")
        real_fixture_publish(stage, target)

    def record_artifact(stage: Path, target: Path, expected: bytes) -> None:
        events.append("artifact")
        real_artifact_publish(stage, target, expected)

    monkeypatch.setattr(generator, "_atomic_publish_no_clobber", record_fixture)
    monkeypatch.setattr(generator, "_replace_artifact_stage", record_artifact)

    result = _admit(
        generator=generator,
        artifact=artifact,
        fixture=fixture,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    frozen_raw, fixture_raw, expected = _expected_outputs(
        generator=generator,
        candidate_raw=candidate_raw,
        payload=payload,
    )

    assert events == ["fixture", "artifact"]
    assert artifact.read_bytes() == frozen_raw
    assert fixture.read_bytes() == fixture_raw
    assert result == expected
    assert result["postgres_authority_census"] == payload["database_execution"]
    assert "census_result" not in result
    lock, journal, artifact_stage, fixture_stage = _sidecar_paths(
        generator=generator,
        artifact=artifact,
        fixture=fixture,
    )
    assert lock.is_file()
    assert not any(path.exists() for path in (journal, artifact_stage, fixture_stage))
    # Post-freeze lifecycle: the temp publisher proof must not mutate committed targets.
    assert ARTIFACT_PATH.read_bytes() == repository_artifact_before
    assert FIXTURE_PATH.read_bytes() == repository_fixture_before
    assert json.loads(repository_artifact_before)["metadata"]["append_frozen"] is True


def test_v16_lock_is_stable_artifact_identity_and_all_transients_are_local(
    tmp_path: Path,
) -> None:
    generator = _load_generator("_task7_v16_atomic_identity_paths")
    artifact, fixture_a, _candidate_raw = _candidate_copy(tmp_path)
    fixture_parent = tmp_path / "other"
    fixture_parent.mkdir()
    fixture_b = fixture_parent / "other-fixture.json"

    lock_a = generator._lock_path(artifact)
    lock_b = generator._lock_path(artifact, fixture_b)
    journal = generator._journal_path(artifact)
    artifact_stage = generator._stage_path(artifact)
    fixture_stage = generator._stage_path(fixture_b)

    assert lock_a == lock_b
    assert lock_a.parent == artifact.parent
    assert journal.parent == artifact.parent
    assert artifact_stage.parent == artifact.parent
    assert fixture_stage.parent == fixture_b.parent
    assert fixture_stage.parent != artifact_stage.parent
    assert fixture_a != fixture_b


def test_v16_journal_contains_only_safe_hashes_and_no_path_or_receipt_content(
    tmp_path: Path,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_atomic_safe_journal")
    frozen_raw, fixture_raw, _expected = _expected_outputs(
        generator=generator,
        candidate_raw=candidate_raw,
        payload=payload,
    )
    raw = generator._journal_bytes(
        artifact_path=artifact,
        fixture_path=fixture,
        candidate_raw=candidate_raw,
        frozen_raw=frozen_raw,
        fixture_raw=fixture_raw,
        receipt_raw=receipt_raw,
        trusted_public_key_raw=public_key_raw,
    )
    journal = json.loads(raw)

    assert journal["schema_version"] == "task7-v16-atomic-admission-journal-v1"
    for key, value in journal.items():
        if key == "schema_version":
            continue
        assert key.endswith("_sha256")
        assert isinstance(value, str) and len(value) == 64
    serialized = raw.decode("utf-8")
    assert artifact.name not in serialized
    assert fixture.name not in serialized
    assert str(artifact.parent) not in serialized
    assert "signer_id" not in serialized
    assert "payload" not in serialized


def test_v16_existing_fixture_sentinel_is_never_overwritten(tmp_path: Path) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_atomic_no_clobber")
    sentinel = b"operator-owned-sentinel\n"
    fixture.write_bytes(sentinel)

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_FIXTURE_OUTPUT_ALREADY_EXISTS",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert artifact.read_bytes() == candidate_raw
    assert fixture.read_bytes() == sentinel


def test_v16_atomic_no_clobber_primitive_never_replaces_existing_target(
    tmp_path: Path,
) -> None:
    generator = _load_generator("_task7_v16_atomic_primitive_no_clobber")
    stage = tmp_path / ".stage"
    target = tmp_path / "target.json"
    stage.write_bytes(b"new")
    target.write_bytes(b"sentinel")

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_FIXTURE_OUTPUT_ALREADY_EXISTS",
    ):
        generator._atomic_publish_no_clobber(stage, target)

    assert stage.read_bytes() == b"new"
    assert target.read_bytes() == b"sentinel"


@pytest.mark.parametrize("kind", ("journal", "artifact-stage", "fixture-stage"))
def test_v16_mismatched_recovery_material_is_rejected_without_masking(
    tmp_path: Path,
    kind: str,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        payload,
    ) = _valid_inputs(tmp_path, f"_task7_v16_mismatch_{kind}")
    frozen_raw, fixture_raw, _expected = _expected_outputs(
        generator=generator,
        candidate_raw=candidate_raw,
        payload=payload,
    )
    journal = generator._journal_path(artifact)
    journal.write_bytes(
        generator._journal_bytes(
            artifact_path=artifact,
            fixture_path=fixture,
            candidate_raw=candidate_raw,
            frozen_raw=frozen_raw,
            fixture_raw=fixture_raw,
            receipt_raw=receipt_raw,
            trusted_public_key_raw=public_key_raw,
        )
    )
    target = {
        "journal": journal,
        "artifact-stage": generator._stage_path(artifact),
        "fixture-stage": generator._stage_path(fixture),
    }[kind]
    target.write_bytes(b"mismatch\n")
    before = _snapshot_directory(tmp_path)

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_(RECOVERY_JOURNAL|ARTIFACT_STAGE|FIXTURE_STAGE)_MISMATCH",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    after = _snapshot_directory(tmp_path)
    stable_lock = generator._lock_path(artifact)
    assert after.pop(stable_lock.name) == b"\0"
    assert after == before


def test_v16_candidate_mutation_before_fixture_publish_is_fenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_candidate_mutation")
    real_write = generator._write_durable_exact
    mutated = candidate_raw + b" "
    writes = 0

    def mutate_after_stage(
        path: Path,
        content: bytes,
        *,
        mismatch_code: str,
    ) -> bool:
        nonlocal writes
        created = real_write(path, content, mismatch_code=mismatch_code)
        writes += 1
        if writes == 3:
            artifact.write_bytes(mutated)
        return created

    monkeypatch.setattr(generator, "_write_durable_exact", mutate_after_stage)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_CANDIDATE_CHANGED_DURING_ADMISSION",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert artifact.read_bytes() == mutated
    assert not fixture.exists()


def test_v16_frozen_artifact_without_exact_journal_is_not_recovery(
    tmp_path: Path,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_unjournaled_frozen")
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    artifact.write_bytes(frozen_raw)

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_RECOVERY_JOURNAL_REQUIRED",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert artifact.read_bytes() == frozen_raw
    assert not fixture.exists()


def test_v16_corrupt_frozen_artifact_is_rejected_before_sidecars(
    tmp_path: Path,
) -> None:
    generator, artifact, fixture, candidate_raw, receipt_raw, public_key_raw, _payload = (
        _valid_inputs(tmp_path, "_task7_v16_corrupt_frozen")
    )
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    artifact.write_bytes(frozen_raw[:-2] + b"x\n")
    before = _snapshot_directory(tmp_path)

    with pytest.raises(generator.ContractArtifactError):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert _snapshot_directory(tmp_path) == before


def test_v16_caught_artifact_publish_failure_rolls_back_exact_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_caught_artifact_failure")

    def fail_artifact(_stage: Path, _target: Path, _expected: bytes) -> None:
        raise OSError("injected artifact publish failure")

    monkeypatch.setattr(generator, "_replace_artifact_stage", fail_artifact)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_ATOMIC_ADMISSION_FAILED",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    lock, journal, artifact_stage, fixture_stage = _sidecar_paths(
        generator=generator,
        artifact=artifact,
        fixture=fixture,
    )
    assert lock.is_file()
    assert not any(path.exists() for path in (journal, artifact_stage, fixture_stage))


def test_v16_rollback_failure_is_diagnosable_and_preserves_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_rollback_failure")

    def fail_artifact(_stage: Path, _target: Path, _expected: bytes) -> None:
        raise OSError("injected artifact publish failure")

    def fail_rollback(_path: Path, _expected: bytes) -> None:
        raise OSError("injected rollback failure")

    monkeypatch.setattr(generator, "_replace_artifact_stage", fail_artifact)
    monkeypatch.setattr(generator, "_remove_published_fixture", fail_rollback)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_ATOMIC_ADMISSION_ROLLBACK_FAILED",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert artifact.read_bytes() == candidate_raw
    assert fixture.exists()
    assert generator._journal_path(artifact).is_file()


def test_v16_post_link_failure_rolls_back_fixture_before_journal_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_post_link_failure")
    real_flush = generator._flush_existing_file

    def fail_after_fixture_link(path: Path) -> None:
        if path == fixture:
            raise OSError("injected post-link flush failure")
        real_flush(path)

    monkeypatch.setattr(generator, "_flush_existing_file", fail_after_fixture_link)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_ATOMIC_ADMISSION_FAILED",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not generator._journal_path(artifact).exists()
    assert not generator._stage_path(artifact).exists()
    assert not generator._stage_path(fixture).exists()


def test_v16_recovery_artifact_failure_rolls_back_preexisting_exact_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_recovery_fixture_ownership")
    frozen_raw, fixture_raw, _expected = _expected_outputs(
        generator=generator,
        candidate_raw=candidate_raw,
        payload=payload,
    )
    journal = generator._journal_path(artifact)
    journal.write_bytes(
        generator._journal_bytes(
            artifact_path=artifact,
            fixture_path=fixture,
            candidate_raw=candidate_raw,
            frozen_raw=frozen_raw,
            fixture_raw=fixture_raw,
            receipt_raw=receipt_raw,
            trusted_public_key_raw=public_key_raw,
        )
    )
    fixture.write_bytes(fixture_raw)

    def fail_artifact(_stage: Path, _target: Path, _expected: bytes) -> None:
        raise OSError("injected recovery artifact failure")

    monkeypatch.setattr(generator, "_replace_artifact_stage", fail_artifact)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_ATOMIC_ADMISSION_FAILED",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not journal.exists()
    assert not generator._stage_path(artifact).exists()
    assert not generator._stage_path(fixture).exists()


def test_v16_cleanup_failure_is_never_suppressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact,
        fixture,
        _candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_cleanup_failure")
    journal = generator._journal_path(artifact)
    real_remove = generator._remove_transient

    def fail_journal_cleanup(path: Path, expected: bytes | None = None) -> None:
        if path == journal:
            raise OSError("injected cleanup failure")
        real_remove(path, expected)

    monkeypatch.setattr(generator, "_remove_transient", fail_journal_cleanup)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_ATOMIC_ADMISSION_CLEANUP_FAILED",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert journal.is_file()
    assert fixture.is_file()
    assert json.loads(artifact.read_bytes())["metadata"]["append_frozen"] is True


@pytest.mark.parametrize(
    "path_defect",
    (
        "identical",
        "missing-fixture-parent",
        "artifact-directory",
        "fixture-directory",
        "artifact-symlink",
        "fixture-symlink",
        "reparse-parent",
    ),
)
def test_v16_path_preflight_rejects_alias_nonregular_and_untrusted_paths(
    tmp_path: Path,
    path_defect: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, f"_task7_v16_path_{path_defect}")
    if path_defect == "identical":
        fixture = artifact
    elif path_defect == "missing-fixture-parent":
        fixture = tmp_path / "missing" / fixture.name
    elif path_defect == "artifact-directory":
        artifact.unlink()
        artifact.mkdir()
    elif path_defect == "fixture-directory":
        fixture.mkdir()
    elif path_defect == "artifact-symlink":
        real = tmp_path / "real-artifact.json"
        artifact.replace(real)
        try:
            artifact.symlink_to(real)
        except OSError:
            pytest.skip("symlink creation unavailable")
    elif path_defect == "fixture-symlink":
        real = tmp_path / "real-fixture.json"
        real.write_bytes(b"sentinel")
        try:
            fixture.symlink_to(real)
        except OSError:
            pytest.skip("symlink creation unavailable")
    elif path_defect == "reparse-parent":
        real_check = generator._path_is_reparse_or_symlink

        def parent_is_reparse(path: Path) -> bool:
            return path == fixture.parent or real_check(path)

        monkeypatch.setattr(
            generator,
            "_path_is_reparse_or_symlink",
            parent_is_reparse,
        )
    else:  # pragma: no cover - parameter exhaustiveness
        raise AssertionError(path_defect)
    before_artifact = (
        artifact.read_bytes() if artifact.is_file() and not artifact.is_symlink() else candidate_raw
    )

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_ADMISSION_PATH_INVALID",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    if artifact.is_file() and not artifact.is_symlink():
        assert artifact.read_bytes() == before_artifact
    assert not generator._journal_path(artifact).exists()
    assert not generator._stage_path(fixture).exists()


def test_v16_windows_durability_uses_write_through_without_parent_fsync_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _load_generator("_task7_v16_windows_durability")
    if os.name != "nt":
        pytest.skip("Windows-specific durability contract")
    target = tmp_path / "durable.bin"
    calls: list[Path] = []
    real_open = generator._write_through_create_new
    source = inspect.getsource(real_open)

    def record_open(path: Path, content: bytes) -> None:
        calls.append(path)
        real_open(path, content)

    monkeypatch.setattr(generator, "_write_through_create_new", record_open)
    assert generator._write_durable_exact(
        target,
        b"durable",
        mismatch_code="MISMATCH",
    )

    assert calls == [target]
    assert target.read_bytes() == b"durable"
    assert generator._sync_parent_directory(target) == ("WINDOWS_DIRECTORY_FLUSH_NOT_CLAIMED")
    assert "FILE_FLAG_WRITE_THROUGH" in source
    assert "FlushFileBuffers" in source


def _write_cli_inputs(
    *,
    tmp_path: Path,
    receipt_raw: bytes,
    public_key_raw: bytes,
) -> tuple[Path, Path]:
    receipt = tmp_path / "postgres-census-receipt.json"
    public_key = tmp_path / "trusted-census-public.pem"
    receipt.write_bytes(receipt_raw)
    public_key.write_bytes(public_key_raw)
    return receipt, public_key


def _cli_command(
    *,
    artifact: Path,
    fixture: Path,
    receipt: Path,
    public_key: Path,
) -> list[str]:
    script = textwrap.dedent(
        """
        import hashlib
        import importlib.util
        import json
        import pathlib
        import sys

        generator_path = pathlib.Path(sys.argv[1])
        receipt_path = pathlib.Path(sys.argv[4])
        public_key_path = pathlib.Path(sys.argv[5])
        spec = importlib.util.spec_from_file_location(
            "_task7_v16_authorized_cli_child",
            generator_path,
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        receipt = json.loads(receipt_path.read_bytes())
        public_key_raw = public_key_path.read_bytes()
        module._EXPECTED_CENSUS_SIGNER_ID = receipt["signer_id"]
        module._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 = hashlib.sha256(
            public_key_raw
        ).hexdigest()
        raise SystemExit(
            module.main(
                [
                    "--artifact",
                    sys.argv[2],
                    "--output",
                    sys.argv[3],
                    "--authority-census-receipt",
                    sys.argv[4],
                    "--trusted-public-key",
                    sys.argv[5],
                ]
            )
        )
        """
    )
    return [
        sys.executable,
        "-c",
        script,
        str(GENERATOR_PATH),
        str(artifact),
        str(fixture),
        str(receipt),
        str(public_key),
    ]


def _crash_child_command(
    *,
    artifact: Path,
    fixture: Path,
    receipt: Path,
    public_key: Path,
    phase: str,
) -> list[str]:
    script = textwrap.dedent(
        """
        import hashlib
        import importlib.util
        import json
        import os
        import pathlib
        import sys

        generator_path = pathlib.Path(sys.argv[1])
        artifact = pathlib.Path(sys.argv[2])
        fixture = pathlib.Path(sys.argv[3])
        receipt = pathlib.Path(sys.argv[4])
        public_key = pathlib.Path(sys.argv[5])
        phase = sys.argv[6]
        spec = importlib.util.spec_from_file_location("_task7_v16_crash_child", generator_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        receipt_value = json.loads(receipt.read_bytes())
        public_key_raw = public_key.read_bytes()
        module._EXPECTED_CENSUS_SIGNER_ID = receipt_value["signer_id"]
        module._EXPECTED_CENSUS_PUBLIC_KEY_SHA256 = hashlib.sha256(
            public_key_raw
        ).hexdigest()

        real_write = module._write_durable_exact
        def crash_write(path, content, *, mismatch_code):
            result = real_write(path, content, mismatch_code=mismatch_code)
            path = pathlib.Path(path)
            selected = {
                "journal": module._journal_path(artifact),
                "fixture-stage": module._stage_path(fixture),
                "artifact-stage": module._stage_path(artifact),
            }.get(phase)
            if selected is not None and path == selected:
                os._exit(81)
            return result
        module._write_durable_exact = crash_write

        real_fixture = module._atomic_publish_no_clobber
        def crash_fixture(stage, target):
            real_fixture(stage, target)
            if phase == "fixture":
                os._exit(82)
        module._atomic_publish_no_clobber = crash_fixture

        real_artifact = module._replace_artifact_stage
        def crash_artifact(stage, target, expected):
            real_artifact(stage, target, expected)
            if phase == "artifact":
                os._exit(83)
        module._replace_artifact_stage = crash_artifact

        module.admit_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=receipt.read_bytes(),
            trusted_public_key_raw=public_key.read_bytes(),
        )
        """
    )
    return [
        sys.executable,
        "-c",
        script,
        str(GENERATOR_PATH),
        str(artifact),
        str(fixture),
        str(receipt),
        str(public_key),
        phase,
    ]


@pytest.mark.parametrize(
    ("phase", "exit_code"),
    (
        ("journal", 81),
        ("fixture-stage", 81),
        ("artifact-stage", 81),
        ("fixture", 82),
        ("artifact", 83),
    ),
)
def test_v16_os_exit_at_every_durable_boundary_recovers_with_exact_receipt(
    tmp_path: Path,
    phase: str,
    exit_code: int,
) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        payload,
    ) = _valid_inputs(tmp_path, f"_task7_v16_crash_parent_{phase}")
    receipt, public_key = _write_cli_inputs(
        tmp_path=tmp_path,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env.pop("IDIS_STRICT_DOTENV_PATH", None)

    crashed = subprocess.run(
        _crash_child_command(
            artifact=artifact,
            fixture=fixture,
            receipt=receipt,
            public_key=public_key,
            phase=phase,
        ),
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert crashed.returncode == exit_code
    assert generator._journal_path(artifact).is_file()

    result = _admit(
        generator=generator,
        artifact=artifact,
        fixture=fixture,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    frozen_raw, fixture_raw, expected = _expected_outputs(
        generator=generator,
        candidate_raw=candidate_raw,
        payload=payload,
    )
    assert result == expected
    assert artifact.read_bytes() == frozen_raw
    assert fixture.read_bytes() == fixture_raw
    lock, journal, artifact_stage, fixture_stage = _sidecar_paths(
        generator=generator,
        artifact=artifact,
        fixture=fixture,
    )
    assert lock.is_file()
    assert not any(path.exists() for path in (journal, artifact_stage, fixture_stage))


@pytest.mark.parametrize("different", ("receipt", "key"))
def test_v16_interrupted_recovery_rejects_different_receipt_or_key(
    tmp_path: Path,
    different: str,
) -> None:
    (
        generator,
        artifact,
        fixture,
        _candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, f"_task7_v16_recovery_binding_{different}")
    receipt_path, public_key_path = _write_cli_inputs(
        tmp_path=tmp_path,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env.pop("IDIS_STRICT_DOTENV_PATH", None)
    crashed = subprocess.run(
        _crash_child_command(
            artifact=artifact,
            fixture=fixture,
            receipt=receipt_path,
            public_key=public_key_path,
            phase="fixture",
        ),
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert crashed.returncode == 82
    if different == "receipt":
        supplied_receipt = receipt_raw + b"\n"
        supplied_key = public_key_raw
    else:
        alternate_private, supplied_key = support.keypair()
        supplied_receipt, _payload = support.signed_receipt(
            generator=generator,
            candidate_raw=artifact.read_bytes(),
            private_key=alternate_private,
            public_key_raw=supplied_key,
        )

    expected_code = (
        "TASK7_V16_RECOVERY_JOURNAL_MISMATCH"
        if different == "receipt"
        else "TASK7_V16_POSTGRES_CENSUS_RECEIPT_INVALID"
    )
    with pytest.raises(generator.ContractArtifactError, match=expected_code):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=supplied_receipt,
            public_key_raw=supplied_key,
        )

    _admit(
        generator=generator,
        artifact=artifact,
        fixture=fixture,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    assert json.loads(artifact.read_bytes())["metadata"]["append_frozen"] is True
    assert fixture.is_file()


def test_v16_concurrent_cli_has_exactly_one_commit_winner(tmp_path: Path) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_concurrent_cli")
    receipt, public_key = _write_cli_inputs(
        tmp_path=tmp_path,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    command = _cli_command(
        artifact=artifact,
        fixture=fixture,
        receipt=receipt,
        public_key=public_key,
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env.pop("IDIS_STRICT_DOTENV_PATH", None)
    children = [
        subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outcomes = [child.communicate(timeout=90) + (child.returncode,) for child in children]

    assert sorted(outcome[2] for outcome in outcomes) == [0, 2]
    frozen_raw, fixture_raw, _expected = _expected_outputs(
        generator=generator,
        candidate_raw=candidate_raw,
        payload=payload,
    )
    assert artifact.read_bytes() == frozen_raw
    assert fixture.read_bytes() == fixture_raw
    assert generator._lock_path(artifact).is_file()


def test_v16_same_artifact_different_fixture_paths_share_one_lock(
    tmp_path: Path,
) -> None:
    (
        generator,
        artifact,
        fixture_a,
        _candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_different_fixture_lock")
    fixture_b = tmp_path / "alternate-fixture.json"
    receipt, public_key = _write_cli_inputs(
        tmp_path=tmp_path,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    assert generator._lock_path(artifact, fixture_a) == generator._lock_path(
        artifact,
        fixture_b,
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env.pop("IDIS_STRICT_DOTENV_PATH", None)
    children = [
        subprocess.Popen(
            _cli_command(
                artifact=artifact,
                fixture=fixture,
                receipt=receipt,
                public_key=public_key,
            ),
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for fixture in (fixture_a, fixture_b)
    ]
    outcomes = [child.communicate(timeout=90) + (child.returncode,) for child in children]

    assert sorted(outcome[2] for outcome in outcomes) == [0, 2]
    assert sum(path.is_file() for path in (fixture_a, fixture_b)) == 1
    assert generator._lock_path(artifact).is_file()


def test_v16_completed_admission_is_not_replayed_without_journal(
    tmp_path: Path,
) -> None:
    (
        generator,
        artifact,
        fixture,
        _candidate_raw,
        receipt_raw,
        public_key_raw,
        _payload,
    ) = _valid_inputs(tmp_path, "_task7_v16_no_replay")
    _admit(
        generator=generator,
        artifact=artifact,
        fixture=fixture,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    artifact_after = artifact.read_bytes()
    fixture_after = fixture.read_bytes()
    lock_after = generator._lock_path(artifact).read_bytes()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_RECOVERY_JOURNAL_REQUIRED",
    ):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )

    assert artifact.read_bytes() == artifact_after
    assert fixture.read_bytes() == fixture_after
    assert generator._lock_path(artifact).read_bytes() == lock_after
