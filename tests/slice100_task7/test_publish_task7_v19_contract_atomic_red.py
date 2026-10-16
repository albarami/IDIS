"""RED-first crash-safe publication contracts for full-strict-v19."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PUBLISHER = _REPO_ROOT / "scripts" / "publish_task7_v19_contract.py"
_V18_PUBLISHER = _REPO_ROOT / "scripts" / "publish_task7_v18_contract.py"
_AUTHORIZER = _REPO_ROOT / "scripts" / "authorize_task7_v19_contract_publication.py"
_V18_PUBLISHER_SHA256 = "e7667d6aa652d34fd78677acd36e306bc409c5f2ba7e4fe42929f4c6b8508bc4"


def _load(name: str) -> ModuleType:
    assert _PUBLISHER.is_file(), "TASK7_V19_ATOMIC_PUBLISHER_NOT_IMPLEMENTED"
    spec = importlib.util.spec_from_file_location(name, _PUBLISHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _candidate() -> bytes:
    return (
        json.dumps(
            {
                "metadata": {
                    "artifact_schema": "full-strict-v19-reviewed-preimage-v1",
                    "pipeline_contract_version": "full-strict-v19",
                    "append_frozen": False,
                }
            },
            indent=2,
        )
        + "\n"
    ).encode()


def _authorization(candidate_raw: bytes) -> dict[str, Any]:
    frozen = json.loads(candidate_raw)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = (json.dumps(frozen, indent=2) + "\n").encode()
    census_fields = {
        field: (
            publisher_value
            if not field.endswith("_sha256")
            else hashlib.sha256(field.encode()).hexdigest()
        )
        for field, publisher_value in ()
    }
    del census_fields
    return {
        "frozen_artifact_bytes": frozen_raw,
        "fixture": {
            "fixture_schema": "full-strict-v19-contract-hash-fixture-v1",
            "pipeline_contract_version": "full-strict-v19",
            "artifact_sha256": hashlib.sha256(frozen_raw).hexdigest(),
            "section_hashes": [],
            "component_hashes": {"pipeline_contract_hash": "a" * 64},
            "activation_assertions": {},
            "FULL_STRICT_V19_CONTRACT_HASH": "a" * 64,
            "postgres_authority_census": {},
        },
    }


def test_v19_publisher_hash_pins_the_immutable_v18_atomic_engine_and_v19_authorizer() -> None:
    publisher = _load("_task7_v19_publisher_pins_red")

    assert hashlib.sha256(_V18_PUBLISHER.read_bytes()).hexdigest() == _V18_PUBLISHER_SHA256
    assert publisher._V18_PUBLISHER_SHA256 == _V18_PUBLISHER_SHA256
    assert publisher._AUTHORIZER_PATH == _AUTHORIZER
    assert hashlib.sha256(_AUTHORIZER.read_bytes()).hexdigest() == (
        publisher._EXPECTED_AUTHORIZER_SHA256
    )
    assert publisher._PUBLICATION_PINS_FINALIZED is True


def test_v19_publication_is_fixture_first_artifact_last_and_leaves_no_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load("_task7_v19_publisher_success_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    artifact.write_bytes(candidate_raw)
    authorization = _authorization(candidate_raw)
    census = {
        field: (
            publisher._DATABASE_EXECUTION_COUNTS[field]
            if field in publisher._DATABASE_EXECUTION_COUNTS
            else hashlib.sha256(field.encode()).hexdigest()
        )
        for field in publisher._DATABASE_EXECUTION_FIELDS
    }
    authorization["fixture"]["postgres_authority_census"] = census
    receipt = json.dumps(
        {"signer_id": "slice100-governance-signer-v1"},
    ).encode()
    key = b"trusted"
    monkeypatch.setattr(
        publisher,
        "_EXPECTED_PUBLIC_KEY_SHA256",
        hashlib.sha256(key).hexdigest(),
    )
    monkeypatch.setattr(
        publisher,
        "_authorize_from_pinned_module",
        lambda **_kwargs: authorization,
    )
    order: list[str] = []
    real_fixture = publisher._publish_fixture_no_clobber
    real_artifact = publisher._commit_frozen_artifact

    def fixture_first(stage: Path, target: Path) -> None:
        order.append("fixture")
        real_fixture(stage, target)

    def artifact_last(stage: Path, target: Path, expected: bytes) -> None:
        order.append("artifact")
        real_artifact(stage, target, expected)

    monkeypatch.setattr(publisher, "_publish_fixture_no_clobber", fixture_first)
    monkeypatch.setattr(publisher, "_commit_frozen_artifact", artifact_last)

    result = publisher.publish_candidate_atomically(
        artifact_path=artifact,
        fixture_path=fixture,
        receipt_raw=receipt,
        trusted_public_key_raw=key,
    )

    assert order == ["fixture", "artifact"]
    assert result == authorization["fixture"]
    assert json.loads(artifact.read_bytes())["metadata"]["append_frozen"] is True
    assert fixture.is_file()
    assert not tuple(tmp_path.glob(".*publication-*"))


def test_v19_publication_rolls_back_fixture_and_candidate_on_precommit_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load("_task7_v19_publisher_rollback_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    artifact.write_bytes(candidate_raw)
    authorization = _authorization(candidate_raw)
    authorization["fixture"]["postgres_authority_census"] = {
        field: (
            publisher._DATABASE_EXECUTION_COUNTS[field]
            if field in publisher._DATABASE_EXECUTION_COUNTS
            else hashlib.sha256(field.encode()).hexdigest()
        )
        for field in publisher._DATABASE_EXECUTION_FIELDS
    }
    receipt = json.dumps(
        {"signer_id": "slice100-governance-signer-v1"},
    ).encode()
    key = b"trusted"
    monkeypatch.setattr(
        publisher,
        "_EXPECTED_PUBLIC_KEY_SHA256",
        hashlib.sha256(key).hexdigest(),
    )
    monkeypatch.setattr(
        publisher,
        "_authorize_from_pinned_module",
        lambda **_kwargs: authorization,
    )

    def crash_before_commit(_stage: Path, _target: Path, _expected: bytes) -> None:
        raise OSError("injected precommit crash")

    monkeypatch.setattr(publisher, "_commit_frozen_artifact", crash_before_commit)

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V19_ATOMIC_PUBLICATION_FAILED",
    ):
        publisher.publish_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=receipt,
            trusted_public_key_raw=key,
        )

    assert artifact.read_bytes() == candidate_raw
    assert not fixture.exists()
    assert not tuple(tmp_path.glob(".*publication-*"))


@pytest.mark.parametrize(
    "crash_boundary",
    ("candidate-moved", "artifact-installed"),
)
def test_v19_publication_recovers_moved_or_committed_candidate_without_live_source_rewrite(
    crash_boundary: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(f"_task7_v19_publisher_recovery_{crash_boundary}")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    candidate_raw = _candidate()
    artifact.write_bytes(candidate_raw)
    authorization = _authorization(candidate_raw)
    authorization["fixture"]["postgres_authority_census"] = {
        field: (
            publisher._DATABASE_EXECUTION_COUNTS[field]
            if field in publisher._DATABASE_EXECUTION_COUNTS
            else hashlib.sha256(field.encode()).hexdigest()
        )
        for field in publisher._DATABASE_EXECUTION_FIELDS
    }
    receipt = json.dumps(
        {"signer_id": "slice100-governance-signer-v1"},
    ).encode()
    key = b"trusted"
    monkeypatch.setattr(
        publisher,
        "_EXPECTED_PUBLIC_KEY_SHA256",
        hashlib.sha256(key).hexdigest(),
    )
    monkeypatch.setattr(
        publisher,
        "_authorize_from_pinned_module",
        lambda **_kwargs: authorization,
    )

    class _InjectedCrash(BaseException):
        pass

    if crash_boundary == "candidate-moved":
        real_install = publisher._install_frozen_artifact_no_clobber
        calls = 0

        def crash_after_move(stage: Path, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _InjectedCrash
            real_install(stage, target)

        monkeypatch.setattr(
            publisher,
            "_install_frozen_artifact_no_clobber",
            crash_after_move,
        )
    else:
        real_cleanup = publisher._cleanup_transients
        calls = 0

        def crash_after_commit(paths: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _InjectedCrash
            real_cleanup(paths)

        monkeypatch.setattr(publisher, "_cleanup_transients", crash_after_commit)

    with pytest.raises(_InjectedCrash):
        publisher.publish_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=receipt,
            trusted_public_key_raw=key,
        )

    if crash_boundary == "candidate-moved":
        assert not artifact.exists()
        assert publisher._candidate_backup_path(artifact).read_bytes() == candidate_raw
    else:
        assert json.loads(artifact.read_bytes())["metadata"]["append_frozen"] is True
    assert fixture.is_file()
    assert publisher._journal_path(artifact).is_file()

    result = publisher.publish_candidate_atomically(
        artifact_path=artifact,
        fixture_path=fixture,
        receipt_raw=receipt,
        trusted_public_key_raw=key,
    )

    assert result == authorization["fixture"]
    assert json.loads(artifact.read_bytes())["metadata"]["append_frozen"] is True
    assert fixture.is_file()
    assert not tuple(tmp_path.glob(".*publication-*"))
