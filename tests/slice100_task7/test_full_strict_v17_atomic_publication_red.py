"""RED-first atomic publication contract for full-strict-v17.

The repository artifact and fixture are never publication targets in this
suite.  Every state-machine proof runs against temporary candidate copies and
an injected authorization seam that has the same closed shape as the verified
v17 generator result.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import os
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PUBLISHER_PATH = _REPO_ROOT / "scripts" / "publish_task7_v17_contract.py"
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_task7_v17_contract_fixture.py"
_EXPECTED_SIGNER = "slice100-governance-signer-v1"
_RECEIPT_RAW = (json.dumps({"signer_id": _EXPECTED_SIGNER}, sort_keys=True) + "\n").encode()
_PUBLIC_KEY_RAW = b"test-only-trusted-public-key"
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
_MODULE: ModuleType | None = None
_AUTHORIZATION_SEAM_LOCK = threading.RLock()


def _load_publisher() -> ModuleType:
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    spec = importlib.util.spec_from_file_location(
        "_task7_v17_atomic_publisher_under_test",
        _PUBLISHER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cast(Any, module)._EXPECTED_PUBLIC_KEY_SHA256 = hashlib.sha256(_PUBLIC_KEY_RAW).hexdigest()
    _MODULE = module
    return module


def _render(value: dict[str, Any], *, sort_keys: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=sort_keys,
        )
        + "\n"
    ).encode()


def _candidate(*, version: str = "full-strict-v17", frozen: bool = False) -> bytes:
    return _render(
        {
            "metadata": {
                "artifact_schema": f"{version}-reviewed-preimage-v1",
                "pipeline_contract_version": version,
                "append_frozen": frozen,
            },
            "contract": {"marker": "temporary-test-candidate"},
        }
    )


def _database_execution() -> dict[str, Any]:
    counts = {
        "application_case_execution_count": 219,
        "admin_case_execution_count": 0,
        "admin_fixture_helper_call_count": 36,
        "current_guard_target_count": 73,
        "current_guard_target_event_count": 193,
        "case_outcome_count": 219,
        "legal_execution_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "constructor_schema_proof_count": 4,
        "current_routine_catalog_count": 24,
        "guard_owner_column_acl_relation_count": 36,
        "guard_owner_column_acl_count": 424,
        "application_direct_helper_execute_count": 0,
        "public_direct_helper_execute_count": 0,
        "unexpected_outcome_count": 0,
    }
    return {
        field: counts[field] if field in counts else hashlib.sha256(field.encode()).hexdigest()
        for field in _DATABASE_EXECUTION_FIELDS
    }


def _authorization(
    candidate_raw: bytes,
    *,
    census: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = json.loads(candidate_raw)
    frozen = copy.deepcopy(candidate)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render(frozen)
    pipeline_hash = hashlib.sha256(b"contract").hexdigest()
    fixture = {
        "fixture_schema": "full-strict-v17-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v17",
        "artifact_sha256": hashlib.sha256(frozen_raw).hexdigest(),
        "section_hashes": [],
        "component_hashes": {"pipeline_contract_hash": pipeline_hash},
        "activation_assertions": {"runtime_state": "INACTIVE"},
        "FULL_STRICT_V17_CONTRACT_HASH": pipeline_hash,
        "postgres_authority_census": _database_execution() if census is None else census,
    }
    return {"frozen_artifact_bytes": frozen_raw, "fixture": fixture}


def _authorizer(candidate_raw: bytes) -> Callable[..., dict[str, Any]]:
    expected = _authorization(candidate_raw)

    def authorize(
        *,
        artifact_raw: bytes,
        receipt_raw: bytes,
        trusted_public_key_raw: bytes,
    ) -> dict[str, Any]:
        assert artifact_raw == candidate_raw
        assert receipt_raw == _RECEIPT_RAW
        assert trusted_public_key_raw == _PUBLIC_KEY_RAW
        return copy.deepcopy(expected)

    return authorize


def _targets(tmp_path: Path, candidate_raw: bytes | None = None) -> tuple[Path, Path]:
    artifact = tmp_path / "full_strict_v17_reviewed_preimage_v1.json"
    fixture = tmp_path / "full_strict_v17_contract_hash_v1.json"
    artifact.write_bytes(_candidate() if candidate_raw is None else candidate_raw)
    return artifact, fixture


def _publish(
    publisher: ModuleType,
    artifact: Path,
    fixture: Path,
    candidate_raw: bytes,
) -> dict[str, Any]:
    with _AUTHORIZATION_SEAM_LOCK:
        publisher_any = cast(Any, publisher)
        original = publisher_any._authorize_from_generator
        publisher_any._authorize_from_generator = _authorizer(candidate_raw)
        try:
            return cast(
                dict[str, Any],
                publisher.publish_candidate_atomically(
                    artifact_path=artifact,
                    fixture_path=fixture,
                    receipt_raw=_RECEIPT_RAW,
                    trusted_public_key_raw=_PUBLIC_KEY_RAW,
                ),
            )
        finally:
            publisher_any._authorize_from_generator = original


def _transients(publisher: ModuleType, artifact: Path, fixture: Path) -> tuple[Path, ...]:
    journal = publisher._journal_path(artifact)
    artifact_stage = publisher._stage_path(artifact)
    fixture_stage = publisher._stage_path(fixture)
    return (
        journal,
        artifact_stage,
        fixture_stage,
        publisher._candidate_backup_path(artifact),
        publisher._prep_path(journal),
        publisher._prep_path(artifact_stage),
        publisher._prep_path(fixture_stage),
    )


def test_v17_atomic_publisher_is_separate_and_cli_is_receipt_gated() -> None:
    assert _PUBLISHER_PATH.is_file()
    source = _PUBLISHER_PATH.read_text(encoding="utf-8")
    assert "generate_task7_v17_contract_fixture.py" in source
    for switch in (
        "--artifact",
        "--output",
        "--authority-census-receipt",
        "--trusted-public-key",
    ):
        assert switch in source
    assert (
        "_authorizer"
        not in inspect.signature(_load_publisher().publish_candidate_atomically).parameters
    )
    assert "authorizer" not in inspect.signature(_load_publisher()._derive_plan).parameters


def test_v17_publisher_rejects_generator_bytes_outside_the_approved_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    approved = _GENERATOR_PATH.read_bytes()
    drifted_generator = tmp_path / _GENERATOR_PATH.name
    drifted_generator.write_bytes(approved + b"\n# unauthorized publication drift\n")
    monkeypatch.setattr(publisher, "_GENERATOR_PATH", drifted_generator)
    monkeypatch.setattr(
        publisher,
        "_EXPECTED_GENERATOR_SHA256",
        hashlib.sha256(approved).hexdigest(),
        raising=False,
    )

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_AUTHORIZER_UNAVAILABLE",
    ):
        publisher._load_generator()


def test_v17_publisher_consumes_generator_authorization_after_receipt_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    expected = _authorization(candidate_raw)
    events: list[str] = []

    class FakeGenerator:
        @staticmethod
        def verify_postgres_census_receipt(**kwargs: bytes) -> dict[str, Any]:
            events.append("verify")
            assert kwargs == {
                "receipt_raw": _RECEIPT_RAW,
                "artifact_raw": candidate_raw,
                "trusted_public_key_raw": _PUBLIC_KEY_RAW,
            }
            return {"verified": True}

        @staticmethod
        def authorize_fixture_publication(**kwargs: Any) -> dict[str, Any]:
            events.append("authorize")
            assert kwargs["candidate_artifact"] == json.loads(candidate_raw)
            assert kwargs["verified_census_payload"] == {"verified": True}
            return copy.deepcopy(expected)

    monkeypatch.setattr(publisher, "_load_generator", lambda: FakeGenerator)

    result = publisher._authorize_from_generator(
        artifact_raw=candidate_raw,
        receipt_raw=_RECEIPT_RAW,
        trusted_public_key_raw=_PUBLIC_KEY_RAW,
    )

    assert events == ["verify", "authorize"]
    assert result == expected


@pytest.mark.parametrize(
    "defect",
    ("missing", "extra", "wrong-count", "malformed-hash"),
)
def test_v17_authorized_fixture_requires_exact_twenty_nine_field_census(
    tmp_path: Path,
    defect: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    census = _database_execution()
    if defect == "missing":
        census.pop("catalog_projection_sha256")
    elif defect == "extra":
        census["unexpected"] = 1
    elif defect == "wrong-count":
        census["current_guard_target_event_count"] = 192
    else:
        census["complete_execution_sha256"] = "not-a-hash"
    authorization = _authorization(candidate_raw, census=census)

    monkeypatch.setattr(
        publisher,
        "_authorize_from_generator",
        lambda **_kwargs: authorization,
    )
    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_AUTHORIZATION_INVALID",
    ):
        publisher.publish_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=_RECEIPT_RAW,
            trusted_public_key_raw=_PUBLIC_KEY_RAW,
        )

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not publisher._journal_path(artifact).exists()


def test_v17_valid_authorization_publishes_fixture_first_and_artifact_as_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    frozen_v16 = tmp_path / "full_strict_v16_reviewed_preimage_v1.json"
    frozen_v16.write_bytes(_candidate(version="full-strict-v16", frozen=True))
    v16_before = frozen_v16.read_bytes()
    events: list[str] = []
    real_fixture = publisher._publish_fixture_no_clobber
    real_artifact = publisher._commit_frozen_artifact

    def publish_fixture(stage: Path, target: Path) -> None:
        events.append("fixture")
        real_fixture(stage, target)

    def publish_artifact(
        stage: Path,
        target: Path,
        expected_candidate: bytes,
    ) -> None:
        events.append("artifact")
        real_artifact(stage, target, expected_candidate)

    monkeypatch.setattr(publisher, "_publish_fixture_no_clobber", publish_fixture)
    monkeypatch.setattr(publisher, "_commit_frozen_artifact", publish_artifact)

    result = _publish(publisher, artifact, fixture, candidate_raw)
    expected = _authorization(candidate_raw)
    expected_fixture_raw = _render(expected["fixture"], sort_keys=True)

    assert events == ["fixture", "artifact"]
    assert artifact.read_bytes() == expected["frozen_artifact_bytes"]
    assert fixture.read_bytes() == expected_fixture_raw
    assert result == expected["fixture"]
    assert tuple(result["postgres_authority_census"]) == _DATABASE_EXECUTION_FIELDS
    assert publisher._lock_path(artifact).is_file()
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))
    assert frozen_v16.read_bytes() == v16_before


def test_v17_frozen_v16_is_rejected_before_authorization_or_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    v16_raw = _candidate(version="full-strict-v16", frozen=True)
    artifact, fixture = _targets(tmp_path, v16_raw)
    called = False

    def forbidden(**_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return _authorization(_candidate())

    monkeypatch.setattr(publisher, "_authorize_from_generator", forbidden)
    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_CANDIDATE_REQUIRED",
    ):
        publisher.publish_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=_RECEIPT_RAW,
            trusted_public_key_raw=_PUBLIC_KEY_RAW,
        )

    assert not called
    assert artifact.read_bytes() == v16_raw
    assert not fixture.exists()
    assert not publisher._lock_path(artifact).exists()


def test_v17_wrong_signer_is_rejected_before_authorization_or_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    wrong_receipt = b'{"signer_id":"other-valid-key-signer"}\n'
    called = False

    def forbidden(**_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return _authorization(candidate_raw)

    monkeypatch.setattr(publisher, "_authorize_from_generator", forbidden)
    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_POSTGRES_CENSUS_SIGNER_INVALID",
    ):
        publisher.publish_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=wrong_receipt,
            trusted_public_key_raw=_PUBLIC_KEY_RAW,
        )

    assert not called
    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not publisher._lock_path(artifact).exists()


def test_v17_unpinned_public_key_is_rejected_before_authorization_or_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    called = False

    def forbidden(**_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return _authorization(candidate_raw)

    monkeypatch.setattr(publisher, "_authorize_from_generator", forbidden)
    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_POSTGRES_CENSUS_KEY_INVALID",
    ):
        publisher.publish_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=_RECEIPT_RAW,
            trusted_public_key_raw=b"attacker-controlled-public-key",
        )

    assert not called
    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not publisher._lock_path(artifact).exists()


@pytest.mark.parametrize("defect", ("frozen-bytes", "fixture-artifact-hash"))
def test_v17_authorization_is_independently_bound_to_exact_candidate(
    tmp_path: Path,
    defect: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    authorization = _authorization(candidate_raw)
    if defect == "frozen-bytes":
        authorization["frozen_artifact_bytes"] += b" "
    else:
        authorization["fixture"]["artifact_sha256"] = "0" * 64

    monkeypatch.setattr(
        publisher,
        "_authorize_from_generator",
        lambda **_kwargs: authorization,
    )
    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_AUTHORIZATION_INVALID",
    ):
        publisher.publish_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=_RECEIPT_RAW,
            trusted_public_key_raw=_PUBLIC_KEY_RAW,
        )

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not publisher._journal_path(artifact).exists()


def test_v17_existing_fixture_is_never_overwritten(tmp_path: Path) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    sentinel = b"operator-owned-sentinel"
    fixture.write_bytes(sentinel)

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_FIXTURE_OUTPUT_ALREADY_EXISTS",
    ):
        _publish(publisher, artifact, fixture, candidate_raw)

    assert artifact.read_bytes() == candidate_raw
    assert fixture.read_bytes() == sentinel


def test_v17_candidate_change_before_fixture_publication_is_fenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    changed = candidate_raw + b" "
    real_write = publisher._write_durable_exact
    writes = 0

    def mutate_after_staging(
        path: Path,
        content: bytes,
        *,
        mismatch_code: str,
    ) -> bool:
        nonlocal writes
        created = cast(bool, real_write(path, content, mismatch_code=mismatch_code))
        writes += 1
        if writes == 3:
            artifact.write_bytes(changed)
        return created

    monkeypatch.setattr(publisher, "_write_durable_exact", mutate_after_staging)

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_CANDIDATE_CHANGED_DURING_PUBLICATION",
    ):
        _publish(publisher, artifact, fixture, candidate_raw)

    assert artifact.read_bytes() == changed
    assert not fixture.exists()


def test_v17_external_writer_during_final_install_survives_without_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    external_raw = b"external-writer-owned-bytes"
    original_install = getattr(
        publisher,
        "_install_frozen_artifact_no_clobber",
        None,
    )
    install_calls = 0

    def inject_external_write(stage: Path, target: Path) -> None:
        nonlocal install_calls
        install_calls += 1
        target.write_bytes(external_raw)
        if original_install is None:
            raise AssertionError("publisher never exposed the no-clobber install seam")
        original_install(stage, target)

    monkeypatch.setattr(
        publisher,
        "_install_frozen_artifact_no_clobber",
        inject_external_write,
        raising=False,
    )

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_CANDIDATE_CHANGED_DURING_PUBLICATION",
    ):
        _publish(publisher, artifact, fixture, candidate_raw)

    assert install_calls == 1
    assert artifact.read_bytes() == external_raw
    assert publisher._candidate_backup_path(artifact).read_bytes() == candidate_raw
    assert publisher._journal_path(artifact).is_file()
    assert not fixture.exists()

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_CANDIDATE_REQUIRED",
    ):
        _publish(publisher, artifact, fixture, candidate_raw)
    assert artifact.read_bytes() == external_raw


def test_v17_journal_is_safe_hash_only_and_binds_both_target_identities(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    authorization = _authorization(candidate_raw)
    fixture_raw = _render(authorization["fixture"], sort_keys=True)
    raw = publisher._journal_bytes(
        artifact_path=artifact,
        fixture_path=fixture,
        candidate_raw=candidate_raw,
        frozen_raw=authorization["frozen_artifact_bytes"],
        fixture_raw=fixture_raw,
        receipt_raw=_RECEIPT_RAW,
        trusted_public_key_raw=_PUBLIC_KEY_RAW,
    )
    journal = json.loads(raw)

    assert journal["schema_version"] == "task7-v17-atomic-publication-journal-v2"
    assert journal["candidate_backup_sha256"] == hashlib.sha256(candidate_raw).hexdigest()
    assert (
        journal["candidate_backup_identity_sha256"]
        == hashlib.sha256(
            publisher._normalized_path(publisher._candidate_backup_path(artifact)).encode()
        ).hexdigest()
    )
    for key, value in journal.items():
        if key == "schema_version":
            continue
        assert key.endswith("_sha256")
        assert isinstance(value, str) and len(value) == 64
    text = raw.decode()
    assert artifact.name not in text
    assert fixture.name not in text
    assert str(tmp_path) not in text
    assert _EXPECTED_SIGNER not in text
    assert "payload" not in text


@pytest.mark.parametrize(
    "defect",
    ("identical", "missing-parent", "artifact-symlink", "reparse-parent"),
)
def test_v17_publication_paths_must_be_distinct_regular_and_nonreparse(
    tmp_path: Path,
    defect: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    if defect == "identical":
        fixture = artifact
    elif defect == "missing-parent":
        fixture = tmp_path / "missing" / fixture.name
    elif defect == "artifact-symlink":
        real = tmp_path / "real-artifact.json"
        artifact.replace(real)
        try:
            artifact.symlink_to(real)
        except OSError:
            pytest.skip("symlink creation unavailable")
    else:
        real_check = publisher._path_is_reparse_or_symlink

        def classify(path: Path) -> bool:
            return path == fixture.parent or real_check(path)

        monkeypatch.setattr(publisher, "_path_is_reparse_or_symlink", classify)

    monkeypatch.setattr(
        publisher,
        "_authorize_from_generator",
        _authorizer(candidate_raw),
    )
    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_PUBLICATION_PATH_INVALID",
    ):
        publisher.publish_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=_RECEIPT_RAW,
            trusted_public_key_raw=_PUBLIC_KEY_RAW,
        )

    assert not publisher._journal_path(artifact).exists()


def test_v17_durable_create_uses_platform_flush_contract(tmp_path: Path) -> None:
    publisher = _load_publisher()
    target = tmp_path / "durable.bin"

    assert publisher._write_durable_exact(
        target,
        b"durable",
        mismatch_code="MISMATCH",
    )
    assert target.read_bytes() == b"durable"
    source = inspect.getsource(publisher._write_through_create_new)
    if os.name == "nt":
        assert "FILE_FLAG_WRITE_THROUGH" in source
        assert "FlushFileBuffers" in source
        assert publisher._sync_parent_directory(target) == ("WINDOWS_DIRECTORY_FLUSH_NOT_CLAIMED")
    else:
        assert "O_DSYNC" in source
        assert "os.fsync" in source
        assert publisher._sync_parent_directory(target) == "POSIX_DIRECTORY_FSYNC"


def test_v17_precommit_failure_rolls_back_fixture_and_transients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)

    def fail_commit(_stage: Path, _target: Path, _expected: bytes) -> None:
        raise OSError("injected commit failure")

    monkeypatch.setattr(publisher, "_commit_frozen_artifact", fail_commit)

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_ATOMIC_PUBLICATION_FAILED",
    ):
        _publish(publisher, artifact, fixture, candidate_raw)

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))


def test_v17_failed_final_install_restores_only_the_absent_candidate_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)

    def fail_install(_stage: Path, target: Path) -> None:
        assert not target.exists()
        raise OSError("injected no-clobber install failure")

    monkeypatch.setattr(
        publisher,
        "_install_frozen_artifact_no_clobber",
        fail_install,
    )
    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_ATOMIC_PUBLICATION_FAILED",
    ):
        _publish(publisher, artifact, fixture, candidate_raw)

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))


class _SimulatedCrash(BaseException):
    pass


@pytest.mark.parametrize("sidecar_kind", ("journal", "fixture-stage", "artifact-stage"))
def test_v17_torn_prep_never_becomes_authoritative_and_retry_rebuilds(
    tmp_path: Path,
    sidecar_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    authoritative = {
        "journal": publisher._journal_path(artifact),
        "fixture-stage": publisher._stage_path(fixture),
        "artifact-stage": publisher._stage_path(artifact),
    }[sidecar_kind]
    prep = authoritative.with_name(f".{authoritative.name}.task7-v17-publication-prep")
    real_create = publisher._write_through_create_new
    injected = False

    def tear_prep(path: Path, content: bytes) -> None:
        nonlocal injected
        if path == prep and not injected:
            injected = True
            path.write_bytes(content[: max(1, len(content) // 3)])
            raise _SimulatedCrash
        real_create(path, content)

    monkeypatch.setattr(publisher, "_write_through_create_new", tear_prep)
    with pytest.raises(_SimulatedCrash):
        _publish(publisher, artifact, fixture, candidate_raw)

    assert injected
    assert prep.is_file()
    assert not authoritative.exists()

    monkeypatch.setattr(publisher, "_write_through_create_new", real_create)
    result = _publish(publisher, artifact, fixture, candidate_raw)

    assert result == _authorization(candidate_raw)["fixture"]
    assert artifact.read_bytes() == _authorization(candidate_raw)["frozen_artifact_bytes"]
    assert fixture.is_file()
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))


@pytest.mark.parametrize(
    "phase",
    (
        "journal",
        "fixture-stage",
        "artifact-stage",
        "fixture",
        "candidate-moved",
        "artifact",
    ),
)
def test_v17_every_durable_boundary_is_journal_recoverable(
    tmp_path: Path,
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    journal = publisher._journal_path(artifact)
    fixture_stage = publisher._stage_path(fixture)
    artifact_stage = publisher._stage_path(artifact)
    real_write = publisher._write_durable_exact
    real_fixture = publisher._publish_fixture_no_clobber
    real_install = publisher._install_frozen_artifact_no_clobber
    real_artifact = publisher._commit_frozen_artifact

    def crash_write(
        path: Path,
        content: bytes,
        *,
        mismatch_code: str,
    ) -> bool:
        result = cast(bool, real_write(path, content, mismatch_code=mismatch_code))
        selected = {
            "journal": journal,
            "fixture-stage": fixture_stage,
            "artifact-stage": artifact_stage,
        }.get(phase)
        if path == selected:
            raise _SimulatedCrash
        return result

    def crash_fixture(stage: Path, target: Path) -> None:
        real_fixture(stage, target)
        if phase == "fixture":
            raise _SimulatedCrash

    def crash_artifact(stage: Path, target: Path, expected: bytes) -> None:
        real_artifact(stage, target, expected)
        if phase == "artifact":
            raise _SimulatedCrash

    def crash_install(stage: Path, target: Path) -> None:
        if phase == "candidate-moved":
            assert not target.exists()
            assert publisher._candidate_backup_path(target).read_bytes() == candidate_raw
            raise _SimulatedCrash
        real_install(stage, target)

    monkeypatch.setattr(publisher, "_write_durable_exact", crash_write)
    monkeypatch.setattr(publisher, "_publish_fixture_no_clobber", crash_fixture)
    monkeypatch.setattr(
        publisher,
        "_install_frozen_artifact_no_clobber",
        crash_install,
    )
    monkeypatch.setattr(publisher, "_commit_frozen_artifact", crash_artifact)
    with pytest.raises(_SimulatedCrash):
        _publish(publisher, artifact, fixture, candidate_raw)
    assert journal.is_file()
    if phase == "candidate-moved":
        assert not artifact.exists()
        assert publisher._candidate_backup_path(artifact).read_bytes() == candidate_raw
        assert fixture.is_file()
    elif phase == "artifact":
        assert artifact.read_bytes() == _authorization(candidate_raw)["frozen_artifact_bytes"]
        assert publisher._candidate_backup_path(artifact).read_bytes() == candidate_raw

    monkeypatch.setattr(publisher, "_write_durable_exact", real_write)
    monkeypatch.setattr(publisher, "_publish_fixture_no_clobber", real_fixture)
    monkeypatch.setattr(
        publisher,
        "_install_frozen_artifact_no_clobber",
        real_install,
    )
    monkeypatch.setattr(publisher, "_commit_frozen_artifact", real_artifact)
    result = _publish(publisher, artifact, fixture, candidate_raw)

    assert result == _authorization(candidate_raw)["fixture"]
    assert artifact.read_bytes() == _authorization(candidate_raw)["frozen_artifact_bytes"]
    assert fixture.is_file()
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))


def test_v17_frozen_artifact_without_exact_journal_is_not_replayed(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)
    artifact.write_bytes(_authorization(candidate_raw)["frozen_artifact_bytes"])

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V17_RECOVERY_JOURNAL_REQUIRED",
    ):
        _publish(publisher, artifact, fixture, candidate_raw)

    assert not fixture.exists()


def test_v17_concurrent_publishers_have_exactly_one_commit_winner(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    candidate_raw = _candidate()
    artifact, fixture = _targets(tmp_path, candidate_raw)

    def attempt() -> str:
        try:
            _publish(publisher, artifact, fixture, candidate_raw)
        except publisher.PublicationError as exc:
            return cast(str, exc.code)
        return "COMMITTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _ordinal: attempt(), range(2)))

    assert outcomes == ["COMMITTED", "TASK7_V17_RECOVERY_JOURNAL_REQUIRED"]
    assert artifact.read_bytes() == _authorization(candidate_raw)["frozen_artifact_bytes"]
    assert fixture.is_file()
