from __future__ import annotations

import hashlib
import json
from pathlib import Path

from idis.contracts import GOVERNED_CONTRACT_RELATIVE_PATHS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_RELATIVE = Path("contracts/full_strict_v21_reviewed_preimage_v1.json")
_ARTIFACT = _REPO_ROOT / _ARTIFACT_RELATIVE


def test_frozen_v21_is_the_only_governed_full_strict_contract_lock_target() -> None:
    full_strict_targets = tuple(
        path for path in GOVERNED_CONTRACT_RELATIVE_PATHS if path.name.startswith("full_strict_v")
    )
    assert full_strict_targets == (_ARTIFACT_RELATIVE,)

    artifact_sha256 = hashlib.sha256(_ARTIFACT.read_bytes()).hexdigest()
    assert artifact_sha256 == "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"

    lock = json.loads((_REPO_ROOT / "contracts/contract_lock.json").read_text("utf-8"))
    locked_full_strict = {
        path: digest
        for path, digest in lock["files"].items()
        if Path(path).name.startswith("full_strict_v")
    }
    assert locked_full_strict == {
        _ARTIFACT_RELATIVE.as_posix(): artifact_sha256,
    }


def test_every_materialized_contract_artifact_has_lf_checkout_semantics() -> None:
    attributes = set((_REPO_ROOT / ".gitattributes").read_text("utf-8").splitlines())
    for version in range(2, 22):
        expected = f"contracts/full_strict_v{version}_reviewed_preimage_v1.json text eol=lf"
        assert expected in attributes
