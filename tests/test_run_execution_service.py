"""Tests for the canonical run execution service."""

from __future__ import annotations

from typing import Any

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import RunContext

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


class FakeRunsRepository:
    """Runs repository test double that records status transitions."""

    def __init__(self, *, claim_succeeds: bool = True) -> None:
        self.claim_succeeds = claim_succeeds
        self.mark_running_calls: list[str] = []
        self.completed: list[tuple[str, str, str | None]] = []

    def try_mark_running(self, run_id: str) -> bool:
        """Pretend to atomically claim a queued run."""
        self.mark_running_calls.append(run_id)
        return self.claim_succeeds

    def complete(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> None:
        """Record terminal run status updates."""
        self.completed.append((run_id, status, finished_at))


def _documents() -> list[dict[str, Any]]:
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


def _run_context(calls: list[str]) -> RunContext:
    def extract_fn(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        calls.append("extract")
        return {
            "created_claim_ids": ["claim-001"],
            "chunk_count": len(documents[0]["spans"]),
            "unique_claim_count": 1,
            "conflict_count": 0,
        }

    def grade_fn(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        audit_sink: Any,
    ) -> dict[str, Any]:
        calls.append("grade")
        return {
            "graded_count": len(created_claim_ids),
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        }

    def calc_fn(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        calc_types: list[Any] | None = None,
    ) -> dict[str, Any]:
        calls.append("calc")
        return {
            "calc_ids": [],
            "reproducibility_hashes": [],
            "persisted_count": 0,
            "blocked_candidates": [{"reason": "no_eligible_inputs"}],
        }

    return RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=_documents(),
        extract_fn=extract_fn,
        grade_fn=grade_fn,
        calc_fn=calc_fn,
    )


def test_run_execution_service_claims_run_before_orchestration_and_completes() -> None:
    """Canonical execution must claim RUNNING before dispatching orchestrator steps."""
    calls: list[str] = []
    runs_repo = FakeRunsRepository()
    run_steps_repo = InMemoryRunStepsRepository(TENANT_ID)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )

    result = service.execute(_run_context(calls))

    assert result.claimed is True
    assert result.status == "SUCCEEDED"
    assert runs_repo.mark_running_calls == [RUN_ID]
    assert [call[0] for call in runs_repo.completed] == [RUN_ID]
    assert [call[1] for call in runs_repo.completed] == ["SUCCEEDED"]
    assert runs_repo.completed[0][2] is not None
    assert calls == ["extract", "grade", "calc"]


def test_run_execution_service_does_not_execute_when_run_was_already_claimed() -> None:
    """A failed QUEUED -> RUNNING transition must prevent duplicate execution."""
    calls: list[str] = []
    runs_repo = FakeRunsRepository(claim_succeeds=False)
    run_steps_repo = InMemoryRunStepsRepository(TENANT_ID)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )

    result = service.execute(_run_context(calls))

    assert result.claimed is False
    assert result.status == "NOT_CLAIMED"
    assert runs_repo.mark_running_calls == [RUN_ID]
    assert runs_repo.completed == []
    assert calls == []
