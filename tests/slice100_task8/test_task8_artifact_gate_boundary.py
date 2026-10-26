"""Guard the stop between Task-8.1 v2/v24 bytes and runtime implementation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tests.slice100_task8 import conftest as task8_conftest
from tests.slice100_task8._task8_void_contract_hash_evidence import (
    VOID_V23_ARTIFACT_SHA256,
    VOID_V23_FIXTURE_SHA256,
)

from . import _v24_independent_oracle as independent
from . import _v24_structural_oracle as structural

POLICY_ARTIFACT = structural.POLICY_V2_ARTIFACT
POLICY_FIXTURE = (
    structural.REPO_ROOT
    / "tests"
    / "slice100_task8"
    / "fixtures"
    / "fdd_formula_policy_v2_hash_v1.json"
)
V23_ARTIFACT = structural.V23_DRAFT_ARTIFACT
V23_FIXTURE = (
    structural.REPO_ROOT
    / "tests"
    / "slice100_task8"
    / "fixtures"
    / "full_strict_v23_contract_hash_v1.json"
)
V24_ARTIFACT = structural.V24_ARTIFACT
V24_FIXTURE = independent.V24_FIXTURE
CONTRACT_LOCK = structural.REPO_ROOT / "contracts" / "contract_lock.json"
MIGRATION_DIR = structural.REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions"


def _load_object(path: Path, missing_code: str) -> dict[str, Any]:
    if not path.is_file():
        raise AssertionError(f"{missing_code}:{path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{missing_code}_INVALID")
    return value


def test_rejected_v23_draft_is_byte_frozen_but_not_published_or_selectable() -> None:
    assert hashlib.sha256(V23_ARTIFACT.read_bytes()).hexdigest() == VOID_V23_ARTIFACT_SHA256
    assert hashlib.sha256(V23_FIXTURE.read_bytes()).hexdigest() == VOID_V23_FIXTURE_SHA256
    v23 = _load_object(V23_ARTIFACT, "TASK8_VOID_V23_ARTIFACT_MISSING")
    assert v23["metadata"]["activation_status"] == "INACTIVE"
    assert v23["activation_assertions"]["runtime_selectable"] is False
    assert not V23_ARTIFACT.with_name(f".{V23_ARTIFACT.name}.task8-gate.lock").exists()
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=V23_ARTIFACT,
        fixture_path=V23_FIXTURE,
    )


def test_literal_gate_requires_frozen_inactive_v2_and_v24_artifact_families() -> None:
    policy = _load_object(POLICY_ARTIFACT, "TASK8_POLICY_V2_ARTIFACT_NOT_IMPLEMENTED")
    policy_fixture = _load_object(POLICY_FIXTURE, "TASK8_POLICY_V2_FIXTURE_NOT_IMPLEMENTED")
    v24 = _load_object(V24_ARTIFACT, "TASK8_V24_ARTIFACT_NOT_IMPLEMENTED")
    v24_fixture = _load_object(V24_FIXTURE, "TASK8_V24_FIXTURE_NOT_IMPLEMENTED")

    assert policy["metadata"]["artifact_schema"] == "fdd-formula-policy-v2"
    assert policy["metadata"]["append_frozen"] is True
    assert policy["metadata"]["activation_status"] == "INACTIVE"
    assert policy["activation_assertions"]["runtime_selectable"] is False
    assert policy["activation_assertions"]["migration_consumable"] is False
    assert (
        policy_fixture["artifact_sha256"]
        == hashlib.sha256(POLICY_ARTIFACT.read_bytes()).hexdigest()
    )
    assert v24["metadata"]["append_frozen"] is True
    assert v24["metadata"]["pipeline_contract_version"] == "full-strict-v24"
    assert v24["metadata"]["activation_status"] == "INACTIVE"
    assert v24_fixture["pipeline_contract_version"] == "full-strict-v24"
    assert v24_fixture["activation_status"] == "INACTIVE"
    assert v24_fixture["artifact_sha256"] == hashlib.sha256(V24_ARTIFACT.read_bytes()).hexdigest()


def test_migration_0039_does_not_exist_before_the_v24_artifact_review() -> None:
    assert not list(MIGRATION_DIR.glob("0039*.py"))


def test_task8_runtime_models_and_composition_are_absent_before_the_v24_gate() -> None:
    forbidden_identifiers = (
        "FddDeterministicCalculationV1",
        "FddCalcInputsV1",
        "FddCalcOutputV1",
        "FddSignedRatioValueV1",
        "fdd-formula-engine-v1",
        "METHODOLOGY_CALCULATION_INCOMPLETE",
    )
    offenders: list[str] = []
    source_root = structural.REPO_ROOT / "src" / "idis"
    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(identifier in text for identifier in forbidden_identifiers):
            offenders.append(path.relative_to(structural.REPO_ROOT).as_posix())
    assert not offenders, "TASK8_RUNTIME_IMPLEMENTED_BEFORE_V24_GATE"


def test_inactive_and_rejected_task8_artifacts_are_absent_from_runtime_lock() -> None:
    lock = _load_object(CONTRACT_LOCK, "TASK8_CONTRACT_LOCK_MISSING")
    locked_files = set(lock["files"])

    assert "contracts/full_strict_v21_reviewed_preimage_v1.json" in locked_files
    assert {
        "contracts/fdd_formula_policy_v1.json",
        "contracts/fdd_formula_policy_v2.json",
        "contracts/full_strict_v22_reviewed_preimage_v1.json",
        "contracts/full_strict_v23_reviewed_preimage_v1.json",
        "contracts/full_strict_v24_reviewed_preimage_v1.json",
    }.isdisjoint(locked_files)
