"""Finalized atomic-publication admission seam for full-strict-v20."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PUBLISHER = _REPO_ROOT / "scripts" / "publish_task7_v20_contract.py"
_V18_PUBLISHER = _REPO_ROOT / "scripts" / "publish_task7_v18_contract.py"
_AUTHORIZER = _REPO_ROOT / "scripts" / "authorize_task7_v20_contract_publication.py"
_V18_PUBLISHER_SHA256 = "e7667d6aa652d34fd78677acd36e306bc409c5f2ba7e4fe42929f4c6b8508bc4"


def _load(name: str) -> ModuleType:
    assert _PUBLISHER.is_file(), "TASK7_V20_ATOMIC_PUBLISHER_NOT_IMPLEMENTED"
    spec = importlib.util.spec_from_file_location(name, _PUBLISHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v20_publisher_pins_v18_atomic_engine_and_only_the_v20_authorizer() -> None:
    publisher = _load("_task7_v20_publisher_pins_red")

    assert hashlib.sha256(_V18_PUBLISHER.read_bytes()).hexdigest() == _V18_PUBLISHER_SHA256
    assert publisher._V18_PUBLISHER_SHA256 == _V18_PUBLISHER_SHA256
    assert publisher._AUTHORIZER_PATH == _AUTHORIZER
    assert hashlib.sha256(_AUTHORIZER.read_bytes()).hexdigest() == (
        publisher._EXPECTED_AUTHORIZER_SHA256
    )
    assert "authorize_task7_v19_contract_publication.py" not in _PUBLISHER.read_text(
        encoding="utf-8"
    )


def test_v20_publisher_is_enabled_only_after_census_pins_are_final() -> None:
    publisher = _load("_task7_v20_publisher_finalized_red")

    assert publisher._PUBLICATION_PINS_FINALIZED is True
    assert publisher._require_publication_pins() is None


def test_v20_publication_rejects_missing_targets_without_filesystem_mutation(
    tmp_path: Path,
) -> None:
    publisher = _load("_task7_v20_publisher_missing_targets_red")
    artifact = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"

    with pytest.raises(
        publisher.PublicationError,
        match="TASK7_V20_PUBLICATION_PATH_INVALID",
    ):
        publisher.publish_candidate_atomically(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=b"not consulted",
            trusted_public_key_raw=b"not consulted",
        )

    assert not artifact.exists()
    assert not fixture.exists()
    assert tuple(tmp_path.iterdir()) == ()
