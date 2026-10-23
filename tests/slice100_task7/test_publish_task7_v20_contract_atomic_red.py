"""RED-first atomic-publication contracts for finalized full-strict-v20.

All filesystem state in this suite lives below ``tmp_path``.  The repository
candidate and fixture are read-only inputs to the pin test and are never
publication targets.
"""

from __future__ import annotations

import ast
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
_PUBLISHER_PATH = _REPO_ROOT / "scripts" / "publish_task7_v20_contract.py"
_V18_PUBLISHER_PATH = _REPO_ROOT / "scripts" / "publish_task7_v18_contract.py"
_AUTHORIZER_PATH = _REPO_ROOT / "scripts" / "authorize_task7_v20_contract_publication.py"
_V18_PUBLISHER_SHA256 = "e7667d6aa652d34fd78677acd36e306bc409c5f2ba7e4fe42929f4c6b8508bc4"
_RECEIPT_RAW = json.dumps(
    {"signer_id": "slice100-governance-signer-v1"},
    sort_keys=True,
).encode()
_PUBLIC_KEY_RAW = b"task7-v20-test-only-trusted-public-key"
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
    assert _PUBLISHER_PATH.is_file(), "TASK7_V20_ATOMIC_PUBLISHER_NOT_IMPLEMENTED"
    spec = importlib.util.spec_from_file_location(name, _PUBLISHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v20_direct_cli_guard_runs_only_after_v20_publication_overrides() -> None:
    publisher = _load("_task7_v20_direct_cli_guard_red")
    nested_guard = '\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'

    assert publisher._adapt_pinned_v18_publisher().count(nested_guard) == 0
    tree = ast.parse(_PUBLISHER_PATH.read_text(encoding="utf-8"))
    top_level_main_guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    ]
    assert len(top_level_main_guards) == 1


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


def _candidate() -> bytes:
    return _render(
        {
            "metadata": {
                "artifact_schema": "full-strict-v20-reviewed-preimage-v1",
                "pipeline_contract_version": "full-strict-v20",
                "append_frozen": False,
            },
            "contract": {"marker": "temporary-test-candidate"},
        }
    )


def _database_execution(publisher: ModuleType) -> dict[str, Any]:
    assert tuple(publisher._DATABASE_EXECUTION_FIELDS) == _DATABASE_EXECUTION_FIELDS
    assert len(_DATABASE_EXECUTION_FIELDS) == 31
    return {
        field: (
            publisher._DATABASE_EXECUTION_COUNTS[field]
            if field in publisher._DATABASE_EXECUTION_COUNTS
            else hashlib.sha256(field.encode()).hexdigest()
        )
        for field in _DATABASE_EXECUTION_FIELDS
    }


def _authorization(
    publisher: ModuleType,
    candidate_raw: bytes,
    *,
    census: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frozen = json.loads(candidate_raw)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render(frozen)
    pipeline_hash = hashlib.sha256(b"task7-v20-contract").hexdigest()
    return {
        "frozen_artifact_bytes": frozen_raw,
        "fixture": {
            "fixture_schema": "full-strict-v20-contract-hash-fixture-v1",
            "pipeline_contract_version": "full-strict-v20",
            "artifact_sha256": hashlib.sha256(frozen_raw).hexdigest(),
            "section_hashes": [],
            "component_hashes": {"pipeline_contract_hash": pipeline_hash},
            "activation_assertions": {"runtime_state": "INACTIVE"},
            "FULL_STRICT_V20_CONTRACT_HASH": pipeline_hash,
            "postgres_authority_census": (
                _database_execution(publisher) if census is None else census
            ),
        },
    }


def _admit_test_pins(
    publisher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(publisher, "_PUBLICATION_PINS_FINALIZED", True)
    monkeypatch.setattr(
        publisher,
        "_EXPECTED_PUBLIC_KEY_SHA256",
        hashlib.sha256(_PUBLIC_KEY_RAW).hexdigest(),
    )


def _publish(
    publisher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    artifact: Path,
    fixture: Path,
    candidate_raw: bytes,
    authorization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _admit_test_pins(publisher, monkeypatch)
    admitted = _authorization(publisher, candidate_raw) if authorization is None else authorization
    monkeypatch.setattr(
        publisher,
        "_authorize_from_pinned_module",
        lambda **_kwargs: copy.deepcopy(admitted),
    )
    return publisher.publish_candidate_atomically(
        artifact_path=artifact,
        fixture_path=fixture,
        receipt_raw=_RECEIPT_RAW,
        trusted_public_key_raw=_PUBLIC_KEY_RAW,
    )


def _transients(
    publisher: ModuleType,
    artifact: Path,
    fixture: Path,
) -> tuple[Path, ...]:
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


def test_v20_finalized_publisher_pins_v18_engine_and_exact_v20_authorizer() -> None:
    publisher = _load("_task7_v20_atomic_publication_pins_red")

    assert hashlib.sha256(_V18_PUBLISHER_PATH.read_bytes()).hexdigest() == _V18_PUBLISHER_SHA256
    assert publisher._V18_PUBLISHER_SHA256 == _V18_PUBLISHER_SHA256
    assert publisher._AUTHORIZER_PATH == _AUTHORIZER_PATH
    assert hashlib.sha256(_AUTHORIZER_PATH.read_bytes()).hexdigest() == (
        publisher._EXPECTED_AUTHORIZER_SHA256
    )
    assert publisher._PUBLICATION_PINS_FINALIZED is True


@pytest.mark.parametrize(
    "defect",
    (
        "missing-downgrade-proof",
        "missing-erasure-proof",
        "extra",
        "wrong-count",
        "malformed-downgrade-proof",
        "malformed-erasure-proof",
    ),
)
def test_v20_authorization_requires_exact_thirty_one_field_postgres_census(
    defect: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(f"_task7_v20_atomic_publication_census_{defect}")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    artifact.write_bytes(candidate_raw)
    census = _database_execution(publisher)
    if defect == "missing-downgrade-proof":
        census.pop("downgrade_retained_definition_execution_proof_sha256")
    elif defect == "missing-erasure-proof":
        census.pop("erasure_authority_execution_proof_sha256")
    elif defect == "extra":
        census["unexpected"] = 1
    elif defect == "wrong-count":
        census["current_guard_target_event_count"] -= 1
    elif defect == "malformed-downgrade-proof":
        census["downgrade_retained_definition_execution_proof_sha256"] = "not-a-hash"
    else:
        census["erasure_authority_execution_proof_sha256"] = "not-a-hash"
    authorization = _authorization(publisher, candidate_raw, census=census)

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V20_AUTHORIZATION_INVALID",
    ):
        _publish(
            publisher,
            monkeypatch,
            artifact=artifact,
            fixture=fixture,
            candidate_raw=candidate_raw,
            authorization=authorization,
        )

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))


def test_v20_publication_is_fixture_first_artifact_last_and_leaves_no_transients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load("_task7_v20_atomic_publication_success_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    artifact.write_bytes(candidate_raw)
    expected = _authorization(publisher, candidate_raw)
    events: list[str] = []
    real_fixture = publisher._publish_fixture_no_clobber
    real_artifact = publisher._commit_frozen_artifact

    def publish_fixture(stage: Path, target: Path) -> None:
        events.append("fixture")
        real_fixture(stage, target)

    def publish_artifact(stage: Path, target: Path, candidate: bytes) -> None:
        events.append("artifact")
        real_artifact(stage, target, candidate)

    monkeypatch.setattr(publisher, "_publish_fixture_no_clobber", publish_fixture)
    monkeypatch.setattr(publisher, "_commit_frozen_artifact", publish_artifact)

    result = _publish(
        publisher,
        monkeypatch,
        artifact=artifact,
        fixture=fixture,
        candidate_raw=candidate_raw,
    )

    assert events == ["fixture", "artifact"]
    assert result == expected["fixture"]
    assert artifact.read_bytes() == expected["frozen_artifact_bytes"]
    assert fixture.read_bytes() == _render(expected["fixture"], sort_keys=True)
    assert tuple(result["postgres_authority_census"]) == _DATABASE_EXECUTION_FIELDS
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))


def test_v20_precommit_failure_rolls_back_fixture_candidate_and_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load("_task7_v20_atomic_publication_precommit_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    artifact.write_bytes(candidate_raw)

    def fail_commit(_stage: Path, _target: Path, _candidate: bytes) -> None:
        raise OSError("injected precommit failure")

    monkeypatch.setattr(publisher, "_commit_frozen_artifact", fail_commit)

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V20_ATOMIC_PUBLICATION_FAILED",
    ):
        _publish(
            publisher,
            monkeypatch,
            artifact=artifact,
            fixture=fixture,
            candidate_raw=candidate_raw,
        )

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))


@pytest.mark.parametrize("crash_boundary", ("candidate-moved", "artifact-installed"))
def test_v20_retry_recovers_candidate_moved_or_artifact_installed_boundary(
    crash_boundary: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(f"_task7_v20_atomic_publication_recovery_{crash_boundary}")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    artifact.write_bytes(candidate_raw)
    expected = _authorization(publisher, candidate_raw)

    class _InjectedCrash(BaseException):
        pass

    if crash_boundary == "candidate-moved":
        real_install = publisher._install_frozen_artifact_no_clobber
        calls = 0

        def crash_after_candidate_move(stage: Path, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _InjectedCrash
            real_install(stage, target)

        monkeypatch.setattr(
            publisher,
            "_install_frozen_artifact_no_clobber",
            crash_after_candidate_move,
        )
    else:
        real_cleanup = publisher._cleanup_transients
        calls = 0

        def crash_after_artifact_install(paths: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _InjectedCrash
            real_cleanup(paths)

        monkeypatch.setattr(
            publisher,
            "_cleanup_transients",
            crash_after_artifact_install,
        )

    with pytest.raises(_InjectedCrash):
        _publish(
            publisher,
            monkeypatch,
            artifact=artifact,
            fixture=fixture,
            candidate_raw=candidate_raw,
        )

    if crash_boundary == "candidate-moved":
        assert not artifact.exists()
        assert publisher._candidate_backup_path(artifact).read_bytes() == candidate_raw
    else:
        assert artifact.read_bytes() == expected["frozen_artifact_bytes"]
    assert fixture.is_file()
    assert publisher._journal_path(artifact).is_file()

    result = _publish(
        publisher,
        monkeypatch,
        artifact=artifact,
        fixture=fixture,
        candidate_raw=candidate_raw,
    )

    assert result == expected["fixture"]
    assert artifact.read_bytes() == expected["frozen_artifact_bytes"]
    assert fixture.read_bytes() == _render(expected["fixture"], sort_keys=True)
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))


def test_v20_existing_fixture_is_never_clobbered_and_creates_no_transients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load("_task7_v20_atomic_publication_fixture_no_clobber_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    sentinel = b"operator-owned-fixture"
    artifact.write_bytes(candidate_raw)
    fixture.write_bytes(sentinel)

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V20_FIXTURE_OUTPUT_ALREADY_EXISTS",
    ):
        _publish(
            publisher,
            monkeypatch,
            artifact=artifact,
            fixture=fixture,
            candidate_raw=candidate_raw,
        )

    assert artifact.read_bytes() == candidate_raw
    assert fixture.read_bytes() == sentinel
    assert not any(path.exists() for path in _transients(publisher, artifact, fixture))


def test_v20_external_artifact_writer_is_never_clobbered_or_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load("_task7_v20_atomic_publication_artifact_no_clobber_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    external_raw = b"external-writer-owned-artifact"
    artifact.write_bytes(candidate_raw)
    real_install = publisher._install_frozen_artifact_no_clobber

    def race_with_external_writer(stage: Path, target: Path) -> None:
        target.write_bytes(external_raw)
        real_install(stage, target)

    monkeypatch.setattr(
        publisher,
        "_install_frozen_artifact_no_clobber",
        race_with_external_writer,
    )

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V20_CANDIDATE_CHANGED_DURING_PUBLICATION",
    ):
        _publish(
            publisher,
            monkeypatch,
            artifact=artifact,
            fixture=fixture,
            candidate_raw=candidate_raw,
        )

    assert artifact.read_bytes() == external_raw
    assert publisher._candidate_backup_path(artifact).read_bytes() == candidate_raw
    assert publisher._journal_path(artifact).is_file()
    assert not fixture.exists()
