"""Append-only evidence contract for the frozen full-strict-v20 publication."""

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
_REJECTED_RECEIPT = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task7_v20_current_guard_postgres_census_receipt.rejected-null-proof-projection.json"
)
_ACCEPTED_RECEIPT = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task7_v20_current_guard_postgres_census_receipt.json"
)
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v20_contract_hash_v1.json"
)
_PLAN = _REPO_ROOT / "docs" / "plans" / "2026-07-15-slice100-strict-real-example-live-dry-run.md"
_DESIGN = _REPO_ROOT / "docs" / "plans" / "2026-07-21-slice100-task7-receipt-edge-schema-design.md"

_CANDIDATE_SHA256 = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_REJECTED_RECEIPT_SHA256 = "e3d20d8417749090b1e69bd9470ae5e188498ccc18cd90403a20191d90597ef3"
_ACCEPTED_RECEIPT_SHA256 = "7b39bde8b6746921fa144edf84a5d075617bd9ca8fe09afa078467fce0f20f98"
_FROZEN_SHA256 = "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"
_FIXTURE_SHA256 = "b8ba48b067a52ab5015ae1edca4943597670e1e2b828ccb640fccbda1b1207e3"
_CONTRACT_SHA256 = "0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178"
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


def test_rejected_and_accepted_receipts_are_distinct_append_only_evidence() -> None:
    records = _records(_RECONCILIATION)
    events = [record["event"] for record in records]
    rejected = records[events.index("V20_SIGNED_CENSUS_RECEIPT_REJECTED_NULL_PROOF_PROJECTION")]
    accepted = records[events.index("V20_APP_ROLE_CENSUS_REEXECUTED_AND_SIGNED")]

    assert events.index(rejected["event"]) < events.index(accepted["event"])
    assert _sha256(_REJECTED_RECEIPT) == _REJECTED_RECEIPT_SHA256
    assert _sha256(_ACCEPTED_RECEIPT) == _ACCEPTED_RECEIPT_SHA256
    assert rejected["signed_receipt_sha256"] == _REJECTED_RECEIPT_SHA256
    assert rejected["signature_verified"] is True
    assert rejected["source_admission_proof_root_present"] is True
    assert rejected["receipt_equality_proof_root_present"] is True
    assert rejected["source_admission_projected_digest"] is None
    assert rejected["receipt_equality_projected_digest"] is None
    assert rejected["publication_rejection_code"] == "TASK7_V20_AUTHORIZATION_INVALID"
    assert accepted["signed_receipt_sha256"] == _ACCEPTED_RECEIPT_SHA256
    assert accepted["application_role_case_count"] == 219
    assert accepted["admin_case_count"] == 0


def test_frozen_artifact_fixture_and_history_bind_the_same_v20_authority() -> None:
    fixture = json.loads(_FIXTURE.read_bytes())
    artifact = json.loads(_ARTIFACT.read_bytes())
    reconciliation = _records(_RECONCILIATION)
    history = _records(_HISTORY)
    frozen = next(row for row in reconciliation if row["event"] == "V20_ATOMIC_FREEZE_ADMITTED")
    successor = next(row for row in history if row["event"] == "V20_ATOMIC_FREEZE_ADMITTED")

    assert _sha256(_ARTIFACT) == _FROZEN_SHA256
    assert _sha256(_FIXTURE) == _FIXTURE_SHA256
    assert artifact["metadata"]["append_frozen"] is True
    assert fixture["artifact_sha256"] == _FROZEN_SHA256
    assert fixture["FULL_STRICT_V20_CONTRACT_HASH"] == _CONTRACT_SHA256
    for row in (frozen, successor):
        assert row["source_candidate_artifact_sha256"] == _CANDIDATE_SHA256
        assert row["artifact_sha256"] == _FROZEN_SHA256
        assert row["fixture_sha256"] == _FIXTURE_SHA256
        assert row.get("pipeline_contract_sha256", row.get("contract_sha256")) == _CONTRACT_SHA256
        assert row.get("append_frozen", row.get("append_freeze_reached")) is True
        assert row["runtime_state"] == "INACTIVE"
        assert row["runtime_selectable"] is False
        assert row["task7_full_cut_claim_allowed"] is False
        assert row["task11_reproof_required"] is True
        assert tuple(row["producer_blocked_rule_ids"]) == _TASK11_RULE_IDS


def test_plan_and_design_record_freeze_without_claiming_activation_or_full_cut() -> None:
    plan = _PLAN.read_text("utf-8")
    design = _DESIGN.read_text("utf-8")

    assert "### 1.68 Task-7 full-strict-v20 signed census and atomic freeze" in plan
    assert "### 20.30 Frozen full-strict-v20 publication evidence" in design
    for value in (
        _REJECTED_RECEIPT_SHA256,
        _ACCEPTED_RECEIPT_SHA256,
        _FROZEN_SHA256,
        _FIXTURE_SHA256,
        _CONTRACT_SHA256,
    ):
        assert value in plan
        assert value in design
    # Task 7 is now reviewer-approved; the durable boundary is inactive v21
    # plus the still-undischarged Task-11 full-cut proof, not the old review status.
    assert "Task 7 is authorized for local commit" in plan
    assert "V21 remains `INACTIVE`" in plan
    assert "Task 11 still owns the exact" in plan
