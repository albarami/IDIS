"""Tests for RunOrchestrator CALC step wiring — Phase 5 deterministic gate.

Covers:
- CALC step completes with deterministic output IDs and hashes
- Missing calc_fn fails closed (no silent success)
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
    """Deterministic extraction stub returning fixed claim IDs."""
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
    """Deterministic grading stub returning success summary."""
    return {
        "graded_count": len(created_claim_ids),
        "failed_count": 0,
        "total_defects": 0,
        "all_failed": False,
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


class TestCalcStepCompletedWithOutputRefs:
    """test_calc_step_completed_with_output_refs."""

    def test_calc_step_completed_with_output_refs(self) -> None:
        """CALC step completes with calc_ids and reproducibility_hashes in result."""
        expected_calc_ids = ["calc-aaa", "calc-bbb"]
        expected_hashes = ["sha256-111", "sha256-222"]

        def stub_calc(
            *,
            run_id: str,
            tenant_id: str,
            deal_id: str,
            created_claim_ids: list[str],
            calc_types: list[Any] | None = None,
        ) -> dict[str, Any]:
            return {
                "calc_ids": expected_calc_ids,
                "reproducibility_hashes": expected_hashes,
            }

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
            calc_fn=stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "SUCCEEDED"

        calc_steps = [s for s in result.steps if s.step_name == StepName.CALC]
        assert len(calc_steps) == 1

        calc_step = calc_steps[0]
        assert calc_step.status == StepStatus.COMPLETED
        assert calc_step.result_summary["calc_ids"] == expected_calc_ids
        assert calc_step.result_summary["reproducibility_hashes"] == expected_hashes
        assert calc_step.started_at is not None
        assert calc_step.finished_at is not None


class TestCalcStepMissingCalcFnFailsClosed:
    """test_calc_step_missing_calc_fn_fails_closed."""

    def test_calc_step_missing_calc_fn_fails_closed(self) -> None:
        """ctx.calc_fn=None causes CALC step to FAIL; no silent success."""
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
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"

        calc_steps = [s for s in result.steps if s.step_name == StepName.CALC]
        assert len(calc_steps) == 1

        calc_step = calc_steps[0]
        assert calc_step.status == StepStatus.FAILED
        assert calc_step.error_code == "VALUEERROR"
        assert "calc_fn not provided" in (calc_step.error_message or "")

        completed_steps = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed_steps) == 3
        assert [s.step_name for s in completed_steps] == [
            StepName.INGEST_CHECK,
            StepName.EXTRACT,
            StepName.GRADE,
        ]
