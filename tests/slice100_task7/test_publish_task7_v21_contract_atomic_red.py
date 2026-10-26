"""RED-first atomic-publication contracts for full-strict-v21.

All publication targets are temporary.  The repository candidate and signed
receipt remain read-only inputs until the separately authorized real publish.
"""

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
_PUBLISHER = _REPO_ROOT / "scripts" / "publish_task7_v21_contract.py"
_RECEIPT_RAW = b'{"signer_id":"slice100-governance-signer-v1"}'
_PUBLIC_KEY_RAW = b"task7-v21-test-only-public-key"


def _load(name: str) -> ModuleType:
    assert _PUBLISHER.is_file(), "TASK7_V21_ATOMIC_PUBLISHER_NOT_IMPLEMENTED"
    spec = importlib.util.spec_from_file_location(name, _PUBLISHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
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


def _candidate() -> bytes:
    return _render(
        {
            "metadata": {
                "artifact_schema": "full-strict-v21-reviewed-preimage-v1",
                "pipeline_contract_version": "full-strict-v21",
                "append_frozen": False,
            },
            "contract": {"marker": "temporary-v21-test-candidate"},
        }
    )


def _database_execution(publisher: ModuleType) -> dict[str, Any]:
    assert len(publisher._DATABASE_EXECUTION_FIELDS) == 31
    return {
        field: (
            publisher._DATABASE_EXECUTION_COUNTS[field]
            if field in publisher._DATABASE_EXECUTION_COUNTS
            else hashlib.sha256(field.encode()).hexdigest()
        )
        for field in publisher._DATABASE_EXECUTION_FIELDS
    }


def _authorization(publisher: ModuleType, candidate_raw: bytes) -> dict[str, Any]:
    frozen = json.loads(candidate_raw)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render(frozen)
    contract_hash = hashlib.sha256(b"task7-v21-test-contract").hexdigest()
    return {
        "frozen_artifact_bytes": frozen_raw,
        "fixture": {
            "fixture_schema": "full-strict-v21-contract-hash-fixture-v1",
            "pipeline_contract_version": "full-strict-v21",
            "artifact_sha256": hashlib.sha256(frozen_raw).hexdigest(),
            "section_hashes": [],
            "component_hashes": {"pipeline_contract_hash": contract_hash},
            "activation_assertions": {"runtime_state": "INACTIVE"},
            "FULL_STRICT_V21_CONTRACT_HASH": contract_hash,
            "postgres_authority_census": _database_execution(publisher),
        },
    }


def _publish(
    publisher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    artifact: Path,
    fixture: Path,
    candidate_raw: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = _authorization(publisher, candidate_raw)
    monkeypatch.setattr(publisher, "_PUBLICATION_PINS_FINALIZED", True)
    monkeypatch.setattr(
        publisher,
        "_EXPECTED_PUBLIC_KEY_SHA256",
        hashlib.sha256(_PUBLIC_KEY_RAW).hexdigest(),
    )
    monkeypatch.setattr(
        publisher,
        "_authorize_from_pinned_module",
        lambda **_kwargs: copy.deepcopy(expected),
    )
    result = publisher.publish_candidate_atomically(
        artifact_path=artifact,
        fixture_path=fixture,
        receipt_raw=_RECEIPT_RAW,
        trusted_public_key_raw=_PUBLIC_KEY_RAW,
    )
    return result, expected


def test_v21_publication_is_fixture_first_artifact_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load("_task7_v21_atomic_order_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    artifact.write_bytes(candidate_raw)
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

    result, expected = _publish(
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


def test_v21_existing_fixture_is_never_clobbered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load("_task7_v21_fixture_no_clobber_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    sentinel = b"operator-owned-v21-fixture"
    artifact.write_bytes(candidate_raw)
    fixture.write_bytes(sentinel)

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V21_FIXTURE_OUTPUT_ALREADY_EXISTS",
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


def test_v21_precommit_failure_restores_candidate_and_removes_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load("_task7_v21_precommit_rollback_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    artifact.write_bytes(candidate_raw)

    def fail_commit(_stage: Path, _target: Path, _candidate: bytes) -> None:
        raise OSError("injected v21 precommit failure")

    monkeypatch.setattr(publisher, "_commit_frozen_artifact", fail_commit)
    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V21_ATOMIC_PUBLICATION_FAILED",
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
