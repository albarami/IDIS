"""Tests for RunOrchestrator DEBATE step wiring — Phase 6.

Covers:
- Happy path: debate_fn stub returns deterministic output → DEBATE step COMPLETED
- Fail-closed: debate_fn is None → DEBATE step FAILED with ValueError
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import StepName, StepStatus
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator

TENANT_A = "11111111-1111-1111-1111-111111111111"


def _stub_extract(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic extraction stub."""
    return {
        "status": "COMPLETED",
        "created_claim_ids": ["claim-001", "claim-002"],
        "chunk_count": 1,
        "unique_claim_count": 2,
        "conflict_count": 0,
    }


def _stub_grade(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    audit_sink: Any,
) -> dict[str, Any]:
    """Deterministic grading stub."""
    return {
        "graded_count": len(created_claim_ids),
        "failed_count": 0,
        "total_defects": 0,
        "all_failed": False,
    }


def _stub_calc(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_types: list[Any] | None = None,
) -> dict[str, Any]:
    """Deterministic calc stub."""
    return {
        "calc_ids": ["calc-001", "calc-002"],
        "reproducibility_hashes": ["hash-aaa", "hash-bbb"],
    }


def _stub_debate(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
) -> dict[str, Any]:
    """Deterministic debate stub returning fixed output."""
    return {
        "debate_id": run_id,
        "stop_reason": "MAX_ROUNDS",
        "round_number": 5,
        "muhasabah_passed": True,
        "agent_output_count": 10,
    }


def _make_documents() -> list[dict[str, Any]]:
    """Return minimal ingested document list."""
    return [
        {
            "document_id": "doc-001",
            "doc_type": "PDF",
            "document_name": "test.pdf",
            "spans": [
                {
                    "span_id": "span-001",
                    "text_excerpt": "Revenue was $5M.",
                    "locator": {"page": 1},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]


@pytest.fixture(autouse=True)
def _clear_stores() -> None:
    """Reset in-memory stores before each test."""
    clear_run_steps_store()


class TestDebateStepHappyPath:
    """DEBATE step completes when debate_fn is provided and returns valid output."""

    def test_full_run_with_debate_fn_completes_all_five_steps(self) -> None:
        """FULL run with debate_fn completes INGEST_CHECK→EXTRACT→GRADE→CALC→DEBATE."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="FULL",
            documents=_make_documents(),
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
            debate_fn=_stub_debate,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "COMPLETED"
        assert len(result.steps) == 5

        expected_names = [
            StepName.INGEST_CHECK,
            StepName.EXTRACT,
            StepName.GRADE,
            StepName.CALC,
            StepName.DEBATE,
        ]
        for i, step in enumerate(result.steps):
            assert step.step_name == expected_names[i]
            assert step.status == StepStatus.COMPLETED
            assert step.step_order == i
            assert step.started_at is not None
            assert step.finished_at is not None

    def test_debate_step_records_output_refs(self) -> None:
        """DEBATE step result_summary contains debate_id, stop_reason, muhasabah_passed."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        run_id = str(uuid.uuid4())
        ctx = RunContext(
            run_id=run_id,
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="FULL",
            documents=_make_documents(),
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
            debate_fn=_stub_debate,
        )

        result = orchestrator.execute(ctx)

        debate_steps = [s for s in result.steps if s.step_name == StepName.DEBATE]
        assert len(debate_steps) == 1
        summary = debate_steps[0].result_summary
        assert summary["debate_id"] == run_id
        assert summary["stop_reason"] == "MAX_ROUNDS"
        assert summary["round_number"] == 5
        assert summary["muhasabah_passed"] is True
        assert summary["agent_output_count"] == 10

    def test_debate_step_receives_accumulated_claim_and_calc_ids(self) -> None:
        """DEBATE step receives created_claim_ids and calc_ids from prior steps."""
        received_args: dict[str, Any] = {}

        def capturing_debate(
            *,
            run_id: str,
            tenant_id: str,
            deal_id: str,
            created_claim_ids: list[str],
            calc_ids: list[str],
        ) -> dict[str, Any]:
            received_args["created_claim_ids"] = created_claim_ids
            received_args["calc_ids"] = calc_ids
            return {
                "debate_id": run_id,
                "stop_reason": "MAX_ROUNDS",
                "round_number": 1,
                "muhasabah_passed": True,
                "agent_output_count": 0,
            }

        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="FULL",
            documents=_make_documents(),
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
            debate_fn=capturing_debate,
        )

        result = orchestrator.execute(ctx)
        assert result.status == "COMPLETED"
        assert received_args["created_claim_ids"] == ["claim-001", "claim-002"]
        assert received_args["calc_ids"] == ["calc-001", "calc-002"]


class TestDebateStepFailClosed:
    """DEBATE step fails closed when debate_fn is None."""

    def test_full_run_without_debate_fn_fails_at_debate(self) -> None:
        """FULL run without debate_fn records DEBATE as FAILED with ValueError."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="FULL",
            documents=_make_documents(),
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
            debate_fn=None,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.error_code == "VALUEERROR"
        assert "debate_fn not provided" in (result.error_message or "")

        completed = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed) == 4
        assert [s.step_name for s in completed] == [
            StepName.INGEST_CHECK,
            StepName.EXTRACT,
            StepName.GRADE,
            StepName.CALC,
        ]

        failed = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].step_name == StepName.DEBATE
        assert failed[0].error_code == "VALUEERROR"

    def test_snapshot_mode_unaffected_by_debate_fn(self) -> None:
        """SNAPSHOT mode completes without debate_fn (DEBATE not in SNAPSHOT_STEPS)."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="SNAPSHOT",
            documents=_make_documents(),
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "COMPLETED"
        assert len(result.steps) == 4
        assert all(s.status == StepStatus.COMPLETED for s in result.steps)
