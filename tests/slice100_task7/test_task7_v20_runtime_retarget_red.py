"""Historical-v20 proof after active Task-7 composition retargets to frozen v21."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import idis.contracts as contracts
from idis.pipeline import contract_registry, task7_producer_selectors_generated
from idis.runtime import dependency_readiness

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v20_contract_hash_v1.json"
)
_EXPECTED_ARTIFACT_SHA256 = "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"
_EXPECTED_FIXTURE_SHA256 = "b8ba48b067a52ab5015ae1edca4943597670e1e2b828ccb640fccbda1b1207e3"
_EXPECTED_CONTRACT_SHA256 = "0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178"

_ACTIVE_COMPOSITION_PATHS = (
    _REPO_ROOT / "src" / "idis" / "persistence" / "repositories" / "runs.py",
    _REPO_ROOT / "src" / "idis" / "persistence" / "repositories" / "strict_preflight_proofs.py",
    _REPO_ROOT / "src" / "idis" / "persistence" / "repositories" / "task7_completion.py",
    _REPO_ROOT / "src" / "idis" / "persistence" / "repositories" / "task7_run_graph.py",
    _REPO_ROOT / "src" / "idis" / "pipeline" / "worker.py",
    _REPO_ROOT / "src" / "idis" / "services" / "runs" / "execution.py",
    _REPO_ROOT / "src" / "idis" / "services" / "runs" / "orchestrator.py",
    _REPO_ROOT / "src" / "idis" / "runtime" / "dependency_probes.py",
)


def _fixture() -> dict[str, object]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_runtime_registry_binds_exact_frozen_v20_and_keeps_activation_separate() -> None:
    fixture = _fixture()

    assert contract_registry.FULL_STRICT_V20_ARTIFACT_SHA256 == _EXPECTED_ARTIFACT_SHA256
    assert contract_registry.FULL_STRICT_V20_FIXTURE_SHA256 == _EXPECTED_FIXTURE_SHA256
    assert contract_registry.FULL_STRICT_V20_CONTRACT_HASH == _EXPECTED_CONTRACT_SHA256
    assert hashlib.sha256(_ARTIFACT_PATH.read_bytes()).hexdigest() == _EXPECTED_ARTIFACT_SHA256
    assert hashlib.sha256(_FIXTURE_PATH.read_bytes()).hexdigest() == _EXPECTED_FIXTURE_SHA256
    assert fixture["artifact_sha256"] == contract_registry.FULL_STRICT_V20_ARTIFACT_SHA256
    assert (
        fixture["FULL_STRICT_V20_CONTRACT_HASH"] == contract_registry.FULL_STRICT_V20_CONTRACT_HASH
    )

    strict_versions = {
        version
        for version in contract_registry.PIPELINE_CONTRACTS
        if version.startswith("full-strict-")
    }
    assert strict_versions == {
        "full-strict-v16",
        "full-strict-v17",
        "full-strict-v18",
        "full-strict-v20",
        "full-strict-v21",
    }
    assert all(
        contract_registry.PIPELINE_CONTRACTS[version].state
        is contract_registry.PipelineContractState.INACTIVE
        for version in strict_versions
    )
    assert (
        contract_registry.PIPELINE_CONTRACTS["full-strict-v20"].contract_hash
        == _EXPECTED_CONTRACT_SHA256
    )

    with pytest.raises(contract_registry.PipelineContractError) as v18_error:
        contract_registry.require_pipeline_contract_for_admission(
            registry=contract_registry.PIPELINE_CONTRACTS,
            contract_version="full-strict-v18",
            proof_contract_hash=contract_registry.FULL_STRICT_V18_CONTRACT_HASH,
        )
    assert v18_error.value.code == contract_registry.PIPELINE_CONTRACT_UNAVAILABLE
    assert "superseded" in str(v18_error.value)

    with pytest.raises(contract_registry.PipelineContractError) as v20_error:
        contract_registry.require_pipeline_contract_for_admission(
            registry=contract_registry.PIPELINE_CONTRACTS,
            contract_version="full-strict-v20",
            proof_contract_hash=_EXPECTED_CONTRACT_SHA256,
        )
    assert v20_error.value.code == contract_registry.PIPELINE_CONTRACT_UNAVAILABLE
    assert "superseded" in str(v20_error.value)


def test_generated_selector_authority_is_the_exact_frozen_v21_projection() -> None:
    assert task7_producer_selectors_generated.PIPELINE_CONTRACT_VERSION == "full-strict-v21"
    assert task7_producer_selectors_generated.PIPELINE_CONTRACT_HASH == (
        contract_registry.FULL_STRICT_V21_CONTRACT_HASH
    )
    assert task7_producer_selectors_generated.SOURCE_ARTIFACT_SHA256 == (
        contract_registry.FULL_STRICT_V21_ARTIFACT_SHA256
    )


def test_contract_lock_governs_v21_instead_of_superseded_v20() -> None:
    governed = set(contracts.GOVERNED_CONTRACT_RELATIVE_PATHS)

    assert Path("contracts/full_strict_v21_reviewed_preimage_v1.json") in governed
    assert Path("contracts/full_strict_v20_reviewed_preimage_v1.json") not in governed


def test_active_composition_names_v21_explicitly() -> None:
    for path in _ACTIVE_COMPOSITION_PATHS:
        source = path.read_text(encoding="utf-8")
        assert any(
            marker in source for marker in ("full-strict-v21", "FULL_STRICT_V21", "TASK7_V21")
        ), path


def test_readiness_uses_v21_authority_with_historical_aliases_only() -> None:
    assert dependency_readiness.task7_v21_erasure_runner_split_is_valid()
    assert len(dependency_readiness.TASK7_V21_CURRENT_ERASURE_RELATIONS) == 48
    assert len(dependency_readiness.TASK7_V21_FUTURE_ERASURE_RELATIONS) == 38
    assert len(dependency_readiness.TASK7_V21_COMPLETE_ERASURE_RELATIONS) == 86
    assert len(dependency_readiness.TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES) == 24

    # Frozen v20/v18 names remain import-compatible evidence, not active selectors.
    assert (
        dependency_readiness.TASK7_V20_CURRENT_ERASURE_RELATIONS
        == dependency_readiness.TASK7_V21_CURRENT_ERASURE_RELATIONS
    )
    assert (
        dependency_readiness.TASK7_V18_CURRENT_ERASURE_RELATIONS
        == dependency_readiness.TASK7_V20_CURRENT_ERASURE_RELATIONS
    )
    assert (
        dependency_readiness.TASK7_V18_CURRENT_GUARD_ROUTINE_SIGNATURES
        == dependency_readiness.TASK7_V20_CURRENT_GUARD_ROUTINE_SIGNATURES
    )
