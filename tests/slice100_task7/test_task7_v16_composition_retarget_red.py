"""RED-first proof that production composition consumes only frozen v18."""

from __future__ import annotations

from pathlib import Path

from tests.slice100_task7 import _void_contract_hash_evidence as void_evidence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSITION_PATHS = (
    _REPO_ROOT / "src/idis/persistence/repositories/runs.py",
    _REPO_ROOT / "src/idis/persistence/repositories/strict_preflight_proofs.py",
    _REPO_ROOT / "src/idis/pipeline/worker.py",
    _REPO_ROOT / "src/idis/services/runs/execution.py",
    _REPO_ROOT / "src/idis/services/runs/orchestrator.py",
)
_BLOCKLISTED_V7_TOKENS = (
    "full-strict-v7",
    "FULL_STRICT_V7",
    "full_strict_v7",
    void_evidence.VOID_V7_CONTRACT_SHA256,
    void_evidence.VOID_V7_ARTIFACT_SHA256,
    void_evidence.VOID_V7_FIXTURE_SHA256,
)


def test_production_composition_contains_no_blocklisted_v7_authority() -> None:
    offenders = {
        path.relative_to(_REPO_ROOT).as_posix(): tuple(
            token for token in _BLOCKLISTED_V7_TOKENS if token in path.read_text("utf-8")
        )
        for path in _COMPOSITION_PATHS
    }
    assert not {path: tokens for path, tokens in offenders.items() if tokens}


def test_production_composition_names_frozen_v21_explicitly() -> None:
    for path in _COMPOSITION_PATHS:
        source = path.read_text("utf-8")
        assert "full-strict-v21" in source or "FULL_STRICT_V21" in source, path
