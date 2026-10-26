"""RED-first crash-safe admission contracts for full-strict-v13."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.slice100_task7 import _v13_atomic_admission_support as support

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_task7_v13_contract_fixture.py"
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v13_reviewed_preimage_v1.json"
_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v13_contract_hash_v1.json"
)


def _load_generator(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_copy(tmp_path: Path) -> tuple[Path, Path, bytes]:
    raw = _ARTIFACT.read_bytes()
    assert json.loads(raw)["metadata"]["append_frozen"] is False
    artifact = tmp_path / _ARTIFACT.name
    fixture = tmp_path / _FIXTURE.name
    artifact.write_bytes(raw)
    return artifact, fixture, raw


def _valid_inputs(
    tmp_path: Path,
    name: str,
) -> tuple[ModuleType, Path, Path, bytes, bytes, bytes, dict[str, Any]]:
    generator = _load_generator(name)
    artifact, fixture, candidate_raw = _candidate_copy(tmp_path)
    private_key, public_key_raw = support.keypair()
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


def _expected_fixture(
    *,
    generator: ModuleType,
    candidate_raw: bytes,
    payload: dict[str, Any],
) -> tuple[bytes, bytes]:
    frozen = generator.derive_prospective_frozen_bytes(candidate_raw)
    fixture = generator.derive_contract_bytes(frozen, validate=True)
    fixture["postgres_authority_census"] = payload["database_execution"]
    return frozen, generator._render_fixture(fixture).encode("utf-8")


def test_v13_repository_targets_remain_unfrozen_inactive_and_unpublished() -> None:
    artifact = json.loads(_ARTIFACT.read_bytes())
    assert artifact["metadata"]["append_frozen"] is False
    assert artifact["activation_assertions"]["status"] == "INACTIVE"
    assert not _FIXTURE.exists()


def test_v13_admission_paths_are_lf_pinned() -> None:
    attributes = (_REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert (
        "contracts/full_strict_v13_reviewed_preimage_v1.json text eol=lf" in attributes.splitlines()
    )
    assert (
        "tests/slice100_task7/fixtures/full_strict_v13_contract_hash_v1.json text eol=lf"
        in attributes.splitlines()
    )


def test_v13_cli_requires_all_four_admission_inputs() -> None:
    generator = _load_generator("_task7_v13_atomic_cli_red")
    parser_source = _GENERATOR_PATH.read_text(encoding="utf-8")
    for switch in (
        "--artifact",
        "--output",
        "--authority-census-receipt",
        "--trusted-public-key",
    ):
        assert switch in parser_source
    assert generator.main(["--artifact", str(_ARTIFACT)]) == 2
    assert json.loads(_ARTIFACT.read_bytes())["metadata"]["append_frozen"] is False
    assert not _FIXTURE.exists()


def test_v13_signed_full_receipt_binds_harness_counts_digests_and_candidate(
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
    ) = _valid_inputs(tmp_path, "_task7_v13_atomic_receipt_red")

    payload = generator.verify_postgres_census_receipt(
        receipt_raw=receipt_raw,
        artifact_raw=candidate_raw,
        trusted_public_key_raw=public_key_raw,
    )

    assert payload == expected_payload
    result = payload["census_result"]
    execution = payload["database_execution"]
    assert result["harness_identity"] == support.HARNESS_IDENTITY
    assert execution["application_case_execution_count"] == 219
    assert execution["admin_case_execution_count"] == 0
    assert execution["current_guard_target_event_count"] == 192
    assert execution["guard_owner_column_acl_count"] == 366
    assert all(
        isinstance(value, str) and len(value) == 64
        for key, value in execution.items()
        if key.endswith("_sha256")
    )


def _wrong_harness(payload: dict[str, Any]) -> None:
    payload["census_result"]["harness_identity"] = "wrong-harness"


def _wrong_count(payload: dict[str, Any]) -> None:
    payload["database_execution"]["application_case_execution_count"] = 218


def _wrong_digest(payload: dict[str, Any]) -> None:
    payload["census_result"]["case_result_sha256"] = "0" * 64


def _wrong_candidate(payload: dict[str, Any]) -> None:
    payload["candidate_artifact_sha256"] = "f" * 64


@pytest.mark.parametrize(
    "mutator",
    (_wrong_harness, _wrong_count, _wrong_digest, _wrong_candidate),
)
def test_v13_invalid_signed_receipt_has_zero_mutation(
    tmp_path: Path,
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    generator = _load_generator(f"_task7_v13_atomic_invalid_{mutator.__name__}")
    artifact, fixture, candidate_raw = _candidate_copy(tmp_path)
    private_key, public_key_raw = support.keypair()
    receipt_raw, _payload = support.signed_receipt(
        generator=generator,
        candidate_raw=candidate_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
        mutate_payload=mutator,
    )

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V13_POSTGRES_CENSUS_RECEIPT_INVALID",
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


def test_v13_valid_receipt_publishes_fixture_then_frozen_artifact_on_temp_copies(
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
    ) = _valid_inputs(tmp_path, "_task7_v13_atomic_success_red")
    real_replace = generator.os.replace
    observed: list[Path] = []

    def record_replace(source: Path, destination: Path) -> None:
        observed.append(Path(destination))
        real_replace(source, destination)

    monkeypatch.setattr(generator.os, "replace", record_replace)
    result = _admit(
        generator=generator,
        artifact=artifact,
        fixture=fixture,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    frozen_raw, fixture_raw = _expected_fixture(
        generator=generator,
        candidate_raw=candidate_raw,
        payload=payload,
    )

    assert observed == [fixture, artifact]
    assert artifact.read_bytes() == frozen_raw
    assert fixture.read_bytes() == fixture_raw
    assert result["postgres_authority_census"] == payload["database_execution"]
    assert not generator._journal_path(artifact, fixture).exists()
    assert not generator._stage_path(artifact).exists()
    assert not generator._stage_path(fixture).exists()
    assert json.loads(_ARTIFACT.read_bytes())["metadata"]["append_frozen"] is False
    assert not _FIXTURE.exists()


def test_v13_artifact_publish_failure_rolls_back_candidate_only(
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
    ) = _valid_inputs(tmp_path, "_task7_v13_atomic_rollback_red")
    real_replace = generator.os.replace
    calls = 0

    def fail_second_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected artifact publish failure")
        real_replace(source, destination)

    monkeypatch.setattr(generator.os, "replace", fail_second_replace)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V13_ATOMIC_ADMISSION_FAILED",
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
    assert not generator._journal_path(artifact, fixture).exists()
    assert not generator._stage_path(artifact).exists()
    assert not generator._stage_path(fixture).exists()


def test_v13_interrupt_after_fixture_publish_recovers_only_with_same_receipt(
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
    ) = _valid_inputs(tmp_path, "_task7_v13_atomic_interrupt_red")
    real_replace = generator.os.replace
    calls = 0

    def interrupt_after_first(source: Path, destination: Path) -> None:
        nonlocal calls
        real_replace(source, destination)
        calls += 1
        if calls == 1:
            raise SystemExit(91)

    monkeypatch.setattr(generator.os, "replace", interrupt_after_first)
    with pytest.raises(SystemExit, match="91"):
        _admit(
            generator=generator,
            artifact=artifact,
            fixture=fixture,
            receipt_raw=receipt_raw,
            public_key_raw=public_key_raw,
        )
    monkeypatch.setattr(generator.os, "replace", real_replace)

    assert artifact.read_bytes() == candidate_raw
    assert fixture.exists()
    assert generator._journal_path(artifact, fixture).exists()
    _admit(
        generator=generator,
        artifact=artifact,
        fixture=fixture,
        receipt_raw=receipt_raw,
        public_key_raw=public_key_raw,
    )
    frozen_raw, fixture_raw = _expected_fixture(
        generator=generator,
        candidate_raw=candidate_raw,
        payload=payload,
    )
    assert artifact.read_bytes() == frozen_raw
    assert fixture.read_bytes() == fixture_raw
    assert not generator._journal_path(artifact, fixture).exists()


def test_v13_concurrent_cli_admission_has_one_commit_winner(tmp_path: Path) -> None:
    (
        generator,
        artifact,
        fixture,
        candidate_raw,
        receipt_raw,
        public_key_raw,
        payload,
    ) = _valid_inputs(tmp_path, "_task7_v13_atomic_concurrency_parent_red")
    receipt = tmp_path / "receipt.json"
    public_key = tmp_path / "trusted-public.pem"
    receipt.write_bytes(receipt_raw)
    public_key.write_bytes(public_key_raw)
    command = [
        sys.executable,
        str(_GENERATOR_PATH),
        "--artifact",
        str(artifact),
        "--output",
        str(fixture),
        "--authority-census-receipt",
        str(receipt),
        "--trusted-public-key",
        str(public_key),
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
    outcomes = [child.communicate(timeout=45) + (child.returncode,) for child in children]

    assert sorted(outcome[2] for outcome in outcomes) == [0, 2]
    frozen_raw, fixture_raw = _expected_fixture(
        generator=generator,
        candidate_raw=candidate_raw,
        payload=payload,
    )
    assert artifact.read_bytes() == frozen_raw
    assert fixture.read_bytes() == fixture_raw
    assert json.loads(_ARTIFACT.read_bytes())["metadata"]["append_frozen"] is False
    assert not _FIXTURE.exists()


def test_v13_lock_and_journal_are_artifact_directory_sidecars(tmp_path: Path) -> None:
    generator = _load_generator("_task7_v13_atomic_sidecars_red")
    artifact, fixture, _raw = _candidate_copy(tmp_path)

    lock = generator._lock_path(artifact, fixture)
    journal = generator._journal_path(artifact, fixture)

    assert lock.parent == artifact.parent
    assert journal.parent == artifact.parent
    assert lock.name.startswith(f".{artifact.name}.")
    assert journal.name.startswith(f".{artifact.name}.")
