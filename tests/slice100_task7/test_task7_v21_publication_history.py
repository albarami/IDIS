"""Append-only evidence contract for frozen full-strict-v21 publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECONCILIATION = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task7_contract_reconciliation_log.jsonl"
)
_HISTORY = _REPO_ROOT / ".local_reports" / "slice100" / "task7_v17_contract_history.jsonl"
_RECEIPT = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task7_v21_current_guard_postgres_census_receipt.json"
)
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)
_PLAN = _REPO_ROOT / "docs" / "plans" / "2026-07-15-slice100-strict-real-example-live-dry-run.md"
_DESIGN = _REPO_ROOT / "docs" / "plans" / "2026-07-21-slice100-task7-receipt-edge-schema-design.md"

_CANDIDATE_SHA256 = "6812ddcc97086d1d5089c8af41d94a9e4a9aede17db143f4f461dce3f3cf800b"
_RECEIPT_SHA256 = "3f36f7b66a53aa74047608f842e29988a7d6cc745904e34fdea681655946513f"
_FROZEN_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_FIXTURE_SHA256 = "da79338f41e0555d1304c162e249246086db26b20421626dd9c0d51690fd7773"
_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
_RUNNER_SHA256 = "d772e5a7341b36463ef130cd237b24744de481b1d2f0ec43d860143297228412"
_SOURCE_MANIFEST_SHA256 = "2d6edf590a4b4ac439be408be69731f64e79c25af764d9c879e1f14fde6c371c"
_TASK11_RULE_IDS = (
    "E028_PROVIDER_OUTCOME_COMMON",
    "E029_PROVIDER_FACT_SEMANTIC",
    "E030_PROVIDER_FACT_DELIVERABLES",
    "E031_GRAPH_INTERNAL_DOWNSTREAM",
    "E032_RAG_INTERNAL_DOWNSTREAM",
    "E033_GRAPH_EXTERNAL_DOWNSTREAM",
    "E034_RAG_EXTERNAL_DOWNSTREAM",
    "E035_CONFLICT_INTERNAL_DOWNSTREAM",
    "E036_CONFLICT_EXTERNAL_DOWNSTREAM",
    "E048_PROVIDER_FACT_EXTERNAL_CONTEXT",
)


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v21_receipt_freeze_and_history_bind_one_authority() -> None:
    reconciliation = _records(_RECONCILIATION)
    history = _records(_HISTORY)
    signed = next(
        row for row in reconciliation if row["event"] == "V21_APP_ROLE_CENSUS_REEXECUTED_AND_SIGNED"
    )
    frozen = next(row for row in reconciliation if row["event"] == "V21_ATOMIC_FREEZE_ADMITTED")
    successor = next(row for row in history if row["event"] == "V21_ATOMIC_FREEZE_ADMITTED")
    artifact = json.loads(_ARTIFACT.read_bytes())
    fixture = json.loads(_FIXTURE.read_bytes())

    assert _sha256(_RECEIPT) == _RECEIPT_SHA256
    assert _sha256(_ARTIFACT) == _FROZEN_SHA256
    assert _sha256(_FIXTURE) == _FIXTURE_SHA256
    assert artifact["metadata"]["append_frozen"] is True
    assert fixture["artifact_sha256"] == _FROZEN_SHA256
    assert fixture["FULL_STRICT_V21_CONTRACT_HASH"] == _CONTRACT_SHA256
    assert signed["census_runner_sha256"] == _RUNNER_SHA256
    assert signed["materialization_source_manifest_sha256"] == (_SOURCE_MANIFEST_SHA256)
    assert signed["signed_receipt_sha256"] == _RECEIPT_SHA256
    assert signed["application_role_case_count"] == 219
    assert signed["admin_case_count"] == 0
    for row in (frozen, successor):
        assert row["source_candidate_artifact_sha256"] == _CANDIDATE_SHA256
        assert row["artifact_sha256"] == _FROZEN_SHA256
        assert row["fixture_sha256"] == _FIXTURE_SHA256
        assert row.get("pipeline_contract_sha256", row.get("contract_sha256")) == (_CONTRACT_SHA256)
        assert row["runtime_state"] == "INACTIVE"
        assert row["runtime_selectable"] is False
        assert row["task7_full_cut_claim_allowed"] is False
        assert row["task11_reproof_required"] is True
        assert tuple(row["producer_blocked_rule_ids"]) == _TASK11_RULE_IDS


def test_v21_history_records_failed_harness_attempts_without_receipt_fabrication() -> None:
    reconciliation = _records(_RECONCILIATION)
    blocked = next(
        row
        for row in reconciliation
        if row["event"] == "V21_PRE_FREEZE_CENSUS_HARNESS_DEFECTS_CORRECTED"
    )

    assert blocked["candidate_artifact_sha256"] == _CANDIDATE_SHA256
    assert blocked["signed_receipt_generated"] is False
    assert blocked["failed_reason_codes"] == [
        "TASK7_V19_ERASURE_AUTHORITY_INVALID",
        "TASK7_V21_ERASURE_AUTHORITY_EXECUTION_INVALID",
    ]
    assert blocked["postgres_semantics_changed"] is False
    assert blocked["contract_bytes_changed"] is False


def test_plan_and_design_record_v21_without_claiming_full_cut_completion() -> None:
    plan = _PLAN.read_text("utf-8")
    design = _DESIGN.read_text("utf-8")

    assert "### 1.69 Task-7 full-strict-v21 executable schema-USAGE closure" in plan
    assert "### 20.31 Frozen full-strict-v21 publication evidence" in design
    for value in (
        _CANDIDATE_SHA256,
        _RECEIPT_SHA256,
        _FROZEN_SHA256,
        _FIXTURE_SHA256,
        _CONTRACT_SHA256,
    ):
        assert value in plan
        assert value in design
    assert "Task 11" in plan
    assert "all 27 restart cuts" in plan
    assert "INACTIVE" in design
