"""Historical immutability proof for superseded full-strict-v16.

Revision 0038 consumes only frozen v18.  The previously frozen v16 evidence
remains byte-identical for audit history but is never exposed as a selectable
migration authority.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "full_strict_v16_contract_hash_v1.json"
_MIGRATION_PATH = (
    _REPO_ROOT
    / "src"
    / "idis"
    / "persistence"
    / "migrations"
    / "versions"
    / "0038_full_strict_v16_receipts_and_edges.py"
)

_ARTIFACT_SHA256 = "0db5c955e02eb580ba4481d6555fca67b9402c31e11fe3359479feb422ae8f35"
_FIXTURE_SHA256 = "999dafef745feaa56448e0a84ebed3aff37729c3d85f98267ae72a19d702ab79"
_CONTRACT_HASH = "dadf149672f80d66a913cb6459c2465d512adfb05ce8031624c9a5e725ac65f5"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location(
        "task7_migration_0038_v16_history",
        _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_superseded_v16_evidence_remains_append_frozen_and_byte_identical() -> None:
    artifact = _load_json(_ARTIFACT_PATH)
    fixture = _load_json(_FIXTURE_PATH)

    assert hashlib.sha256(_ARTIFACT_PATH.read_bytes()).hexdigest() == _ARTIFACT_SHA256
    assert hashlib.sha256(_FIXTURE_PATH.read_bytes()).hexdigest() == _FIXTURE_SHA256
    assert artifact["metadata"]["append_frozen"] is True
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v16"
    assert fixture["pipeline_contract_version"] == "full-strict-v16"
    assert fixture["FULL_STRICT_V16_CONTRACT_HASH"] == _CONTRACT_HASH


def test_0038_never_exposes_or_selects_the_superseded_v16_authority() -> None:
    migration = _load_migration()
    source = _MIGRATION_PATH.read_text(encoding="utf-8")

    assert not hasattr(migration, "FULL_STRICT_V16_CONTRACT_HASH")
    assert not hasattr(migration, "FULL_STRICT_V16_ACTIVATION_STATE")
    assert not hasattr(migration, "_run_v16_guard_authority_preflight")
    assert not hasattr(migration, "_install_v16_current_guard_authority")
    assert not hasattr(migration, "_remove_v16_guard_and_erasure_authority")
    assert "full-strict-v16" not in source
