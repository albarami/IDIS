"""Tests for RunOrchestrator resume/idempotency — Phase 4 orchestration.

Covers:
- Retry EXTRACT step is idempotent (no duplicate claims)
- Retry GRADE step is idempotent (no duplicate sanads)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import StepName
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator

TENANT_A = "11111111-1111-1111-1111-111111111111"


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


class TestRetryExtractStepIdempotentNoDuplicateClaims:
    """test_retry_extract_step_idempotent_no_duplicate_claims."""

    def test_retry_extract_step_idempotent_no_duplicate_claims(self) -> None:
        """Re-running orchestrator skips completed EXTRACT; no duplicate claims."""
        extract_call_count = 0

        def counting_extract(
            *,
            run_id: str,
            tenant_id: str,
            deal_id: str,
            documents: list[dict[str, Any]],
        ) -> dict[str, Any]:
            nonlocal extract_call_count
            extract_call_count += 1
            return {
                "status": "COMPLETED",
                "created_claim_ids": ["claim-001", "claim-002"],
                "chunk_count": 1,
                "unique_claim_count": 2,
                "conflict_count": 0,
            }

        def stub_grade(
            *,
            run_id: str,
            tenant_id: str,
            deal_id: str,
            created_claim_ids: list[str],
            audit_sink: Any,
        ) -> dict[str, Any]:
            return {
                "graded_count": len(created_claim_ids),
                "failed_count": 0,
                "total_defects": 0,
                "all_failed": False,
            }

        run_id = str(uuid.uuid4())
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=run_id,
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="SNAPSHOT",
            documents=_make_documents(),
            extract_fn=counting_extract,
            grade_fn=stub_grade,
        )

        result1 = orchestrator.execute(ctx)
        assert result1.status == "COMPLETED"
        assert extract_call_count == 1

        result2 = orchestrator.execute(ctx)
        assert result2.status == "COMPLETED"
        assert extract_call_count == 1, "EXTRACT must not re-run when already COMPLETED"

        extract_steps = [s for s in result2.steps if s.step_name == StepName.EXTRACT]
        assert len(extract_steps) == 1, "Only one EXTRACT step record should exist"


class TestRetryGradeStepIdempotentNoDuplicateSanads:
    """test_retry_grade_step_idempotent_no_duplicate_sanads."""

    def test_retry_grade_step_idempotent_no_duplicate_sanads(self) -> None:
        """Re-running orchestrator skips completed GRADE; no duplicate sanads."""
        grade_call_count = 0

        def stub_extract(
            *,
            run_id: str,
            tenant_id: str,
            deal_id: str,
            documents: list[dict[str, Any]],
        ) -> dict[str, Any]:
            return {
                "status": "COMPLETED",
                "created_claim_ids": ["claim-001"],
                "chunk_count": 1,
                "unique_claim_count": 1,
                "conflict_count": 0,
            }

        def counting_grade(
            *,
            run_id: str,
            tenant_id: str,
            deal_id: str,
            created_claim_ids: list[str],
            audit_sink: Any,
        ) -> dict[str, Any]:
            nonlocal grade_call_count
            grade_call_count += 1
            return {
                "graded_count": len(created_claim_ids),
                "failed_count": 0,
                "total_defects": 0,
                "all_failed": False,
            }

        run_id = str(uuid.uuid4())
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=run_id,
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="SNAPSHOT",
            documents=_make_documents(),
            extract_fn=stub_extract,
            grade_fn=counting_grade,
        )

        result1 = orchestrator.execute(ctx)
        assert result1.status == "COMPLETED"
        assert grade_call_count == 1

        result2 = orchestrator.execute(ctx)
        assert result2.status == "COMPLETED"
        assert grade_call_count == 1, "GRADE must not re-run when already COMPLETED"

        grade_steps = [s for s in result2.steps if s.step_name == StepName.GRADE]
        assert len(grade_steps) == 1, "Only one GRADE step record should exist"
