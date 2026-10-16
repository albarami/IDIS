"""Runtime authority proof preserving strict history while selecting frozen v21."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from tests.slice100_task7 import _void_contract_hash_evidence as void_evidence

from idis.pipeline import (
    contract_registry,
    task7_edges,
    task7_producer_selectors_generated,
    task7_semantics,
    task7_usage,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CURRENT_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)
_V18_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v18_contract_hash_v1.json"
)
_V16_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v16_contract_hash_v1.json"
)
_EXPECTED_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_EXPECTED_FIXTURE_SHA256 = "da79338f41e0555d1304c162e249246086db26b20421626dd9c0d51690fd7773"
_EXPECTED_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
_V18_EXPECTED_ARTIFACT_SHA256 = "554409ac36f3defeceda209e9e59e7587e4df630b06b5dc049ba9958f60abd1c"
_V18_EXPECTED_FIXTURE_SHA256 = "9efef72429745ac8ccce91f4aea541a1bb958638e83bf08c9eccbc5eabd03a0c"
_V18_EXPECTED_CONTRACT_SHA256 = "ad8e0a8fd012af34f34d8fd4b66643d2d31a8a4f436dd3a8021af9a188da378c"
_V16_EXPECTED_FIXTURE_SHA256 = "999dafef745feaa56448e0a84ebed3aff37729c3d85f98267ae72a19d702ab79"
_OWNED_RUNTIME_PATHS = (
    *_REPO_ROOT.glob("src/idis/pipeline/task7_*.py"),
    _REPO_ROOT / "src" / "idis" / "pipeline" / "contract_registry.py",
    *_REPO_ROOT.glob("src/idis/persistence/repositories/task7_*.py"),
)


def _fixture(path: Path = _CURRENT_FIXTURE_PATH) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_runtime_registry_binds_exact_frozen_v21_and_stays_inactive() -> None:
    fixture = _fixture()

    assert contract_registry.FULL_STRICT_V21_ARTIFACT_SHA256 == (_EXPECTED_ARTIFACT_SHA256)
    assert contract_registry.FULL_STRICT_V21_FIXTURE_SHA256 == (_EXPECTED_FIXTURE_SHA256)
    assert contract_registry.FULL_STRICT_V21_CONTRACT_HASH == (_EXPECTED_CONTRACT_SHA256)
    assert hashlib.sha256(_CURRENT_FIXTURE_PATH.read_bytes()).hexdigest() == (
        contract_registry.FULL_STRICT_V21_FIXTURE_SHA256
    )
    assert fixture["artifact_sha256"] == (contract_registry.FULL_STRICT_V21_ARTIFACT_SHA256)
    assert fixture["FULL_STRICT_V21_CONTRACT_HASH"] == (
        contract_registry.FULL_STRICT_V21_CONTRACT_HASH
    )

    # This assertion tracks the active registry surface, not frozen version history.
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
    for version in strict_versions:
        assert (
            contract_registry.PIPELINE_CONTRACTS[version].state
            is contract_registry.PipelineContractState.INACTIVE
        )
    strict = contract_registry.PIPELINE_CONTRACTS["full-strict-v21"]
    assert strict.contract_hash == _EXPECTED_CONTRACT_SHA256

    with pytest.raises(contract_registry.PipelineContractError) as caught:
        contract_registry.require_pipeline_contract_for_admission(
            registry=contract_registry.PIPELINE_CONTRACTS,
            contract_version="full-strict-v21",
            proof_contract_hash=_EXPECTED_CONTRACT_SHA256,
        )
    assert caught.value.code == contract_registry.PIPELINE_CONTRACT_UNAVAILABLE


def test_historical_v18_pins_remain_exact_and_superseded() -> None:
    fixture = _fixture(_V18_FIXTURE_PATH)

    assert contract_registry.FULL_STRICT_V18_ARTIFACT_SHA256 == (_V18_EXPECTED_ARTIFACT_SHA256)
    assert contract_registry.FULL_STRICT_V18_FIXTURE_SHA256 == (_V18_EXPECTED_FIXTURE_SHA256)
    assert contract_registry.FULL_STRICT_V18_CONTRACT_HASH == (_V18_EXPECTED_CONTRACT_SHA256)
    assert fixture["artifact_sha256"] == _V18_EXPECTED_ARTIFACT_SHA256
    assert fixture["FULL_STRICT_V18_CONTRACT_HASH"] == _V18_EXPECTED_CONTRACT_SHA256

    with pytest.raises(contract_registry.PipelineContractError) as caught:
        contract_registry.require_pipeline_contract_for_admission(
            registry=contract_registry.PIPELINE_CONTRACTS,
            contract_version="full-strict-v18",
            proof_contract_hash=_V18_EXPECTED_CONTRACT_SHA256,
        )
    assert caught.value.code == contract_registry.PIPELINE_CONTRACT_UNAVAILABLE
    assert "superseded" in str(caught.value)


def test_historical_v16_activation_census_remains_an_exact_fixture_projection() -> None:
    fixture = _fixture(_V16_FIXTURE_PATH)
    census = contract_registry.FULL_STRICT_V16_ACTIVATION_CENSUS_V1

    assert census.contract_version == "full-strict-v16"
    assert census.contract_hash == fixture["FULL_STRICT_V16_CONTRACT_HASH"]
    assert census.artifact_sha256 == fixture["artifact_sha256"]
    assert census.fixture_sha256 == _V16_EXPECTED_FIXTURE_SHA256
    assert dict(census.component_hashes) == fixture["component_hashes"]
    assert census.section_hashes == tuple(
        (row["ordinal"], row["name"], row["sha256"]) for row in fixture["section_hashes"]
    )


def test_runtime_selector_authority_is_bound_to_v21() -> None:
    assert task7_producer_selectors_generated.PIPELINE_CONTRACT_VERSION == ("full-strict-v21")
    assert task7_producer_selectors_generated.PIPELINE_CONTRACT_HASH == (_EXPECTED_CONTRACT_SHA256)
    assert task7_producer_selectors_generated.SOURCE_ARTIFACT_SHA256 == (_EXPECTED_ARTIFACT_SHA256)


def test_runtime_conservation_wall_and_all_27_restart_cuts_remain_exact() -> None:
    strict = contract_registry.PIPELINE_CONTRACTS["full-strict-v21"]

    assert len(strict.steps) == 28
    assert tuple(step.step_order for step in strict.steps) == tuple(range(28))
    assert len(strict.steps) - 1 == 27
    assert len(task7_semantics.STEP_SEMANTIC_REGISTRY) == 28
    assert len(task7_edges.EDGE_RULE_REGISTRY) == 48
    assert len(task7_usage.USAGE_ROUTE_REGISTRY) == 1_756
    assert len(task7_usage.USAGE_EXTRACTOR_REGISTRY) == 19


def test_owned_runtime_source_contains_no_blocklisted_v7_or_v8_lock() -> None:
    forbidden = (
        "full-strict-v7",
        "FULL_STRICT_V7",
        "full-strict-v8",
        "FULL_STRICT_V8",
        void_evidence.VOID_V7_CONTRACT_SHA256,
        void_evidence.VOID_V7_ARTIFACT_SHA256,
        void_evidence.VOID_V7_FIXTURE_SHA256,
    )
    offenders = {
        path.relative_to(_REPO_ROOT).as_posix(): tuple(
            token for token in forbidden if token in path.read_text(encoding="utf-8")
        )
        for path in _OWNED_RUNTIME_PATHS
        if path.is_file()
    }

    assert not {path: tokens for path, tokens in offenders.items() if tokens}
