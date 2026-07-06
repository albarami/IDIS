"""Tests for RunOrchestrator step ledger — Phase 5 orchestration.

Covers:
- SNAPSHOT records seven steps in order
  (DATA_ROOM_INVENTORY_PACKAGE → DATA_ROOM_INGESTION_HANDOFF → INGEST_CHECK → DOCUMENT_PREFLIGHT
  → METHODOLOGY_COVERAGE_INIT → EXTRACT → GRADE → CALC)
- Step errors persisted and returned
- FULL completes all 12 steps in correct order
- Cross-tenant run step read returns 404 (no existence leak)
- Audit failure aborts run fail-closed

Updated for Phase 3.0 Slice 4: FULL mode now has 12 steps.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.models.extraction_task import ExtractionTaskPlanningRunResult
from idis.models.run_step import STEP_ORDER, RunStep, StepName, StepStatus
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
API_KEY_A = "test-key-orch-a"
API_KEY_B = "test-key-orch-b"


def _make_api_keys() -> dict[str, dict[str, Any]]:
    """Build API key config for two tenants."""
    return {
        API_KEY_A: {
            "tenant_id": TENANT_A,
            "actor_id": "actor-a",
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
        API_KEY_B: {
            "tenant_id": TENANT_B,
            "actor_id": "actor-b",
            "name": "Tenant B",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
    }


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


def _stub_calc(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_types: list[Any] | None = None,
) -> dict[str, Any]:
    """Deterministic calc stub returning fixed calc IDs."""
    return {
        "calc_ids": ["calc-001", "calc-002"],
        "reproducibility_hashes": ["hash-aaa", "hash-bbb"],
    }


def test_compute_final_status_ignores_failed_run_lifecycle_step() -> None:
    """Lifecycle ledger failures are not executable pipeline failures."""
    steps = [
        RunStep(
            step_id=str(uuid.uuid4()),
            run_id="run-status",
            tenant_id=TENANT_A,
            step_name=StepName.RUN_LIFECYCLE,
            step_order=STEP_ORDER[StepName.RUN_LIFECYCLE],
            status=StepStatus.FAILED,
            error_code="STRICT_FULL_LIVE_BLOCKED",
        ),
        RunStep(
            step_id=str(uuid.uuid4()),
            run_id="run-status",
            tenant_id=TENANT_A,
            step_name=StepName.EXTRACT,
            step_order=STEP_ORDER[StepName.EXTRACT],
            status=StepStatus.COMPLETED,
        ),
    ]

    assert RunOrchestrator._compute_final_status(steps) == "SUCCEEDED"


def test_compute_final_status_still_fails_on_failed_executable_step() -> None:
    """Executable step failures must remain fail-closed."""
    steps = [
        RunStep(
            step_id=str(uuid.uuid4()),
            run_id="run-status",
            tenant_id=TENANT_A,
            step_name=StepName.RUN_LIFECYCLE,
            step_order=STEP_ORDER[StepName.RUN_LIFECYCLE],
            status=StepStatus.FAILED,
            error_code="STRICT_FULL_LIVE_BLOCKED",
        ),
        RunStep(
            step_id=str(uuid.uuid4()),
            run_id="run-status",
            tenant_id=TENANT_A,
            step_name=StepName.EXTRACT,
            step_order=STEP_ORDER[StepName.EXTRACT],
            status=StepStatus.FAILED,
            error_code="EXTRACT_FAILED",
        ),
    ]

    assert RunOrchestrator._compute_final_status(steps) == "FAILED"


def _stub_extract_failing(**kwargs: Any) -> dict[str, Any]:
    """Extraction stub that always raises."""
    raise RuntimeError("Extraction service unavailable")


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


def _make_preflight_document(
    *,
    document_id: str,
    parse_status: str = "PARSED",
    metadata: dict[str, Any] | None = None,
    spans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a full persisted-corpus document for preflight tests."""
    return {
        "tenant_id": TENANT_A,
        "deal_id": "deal-preflight",
        "document_id": document_id,
        "doc_id": f"artifact-{document_id}",
        "doc_type": "DOCX",
        "parse_status": parse_status,
        "document_name": f"{document_id}.docx",
        "sha256": "a" * 64,
        "uri": f"deals/{document_id}.docx",
        "metadata": metadata or {},
        "source_metadata": {},
        "spans": spans
        if spans is not None
        else [
            {
                "span_id": f"span-{document_id}",
                "tenant_id": TENANT_A,
                "deal_id": "deal-preflight",
                "document_id": document_id,
                "span_type": "PARAGRAPH",
                "locator": {"paragraph": 1},
                "text_excerpt": "Highly sensitive raw revenue sentence.",
                "content_hash": "b" * 64,
            }
        ],
    }


def _make_failed_preflight_document() -> dict[str, Any]:
    """Return a failed persisted document with safe parser metadata only."""
    return _make_preflight_document(
        document_id="doc-failed",
        parse_status="FAILED",
        metadata={
            "parse_error_codes": ["encrypted_pdf"],
            "parse_warning_codes": [],
            "detected_format": "PDF",
            "parser_doc_type": "PDF",
        },
        spans=[],
    )


@pytest.fixture(autouse=True)
def _clear_stores() -> None:
    """Reset in-memory stores before each test."""
    clear_deals_store()
    clear_runs_store()
    clear_run_steps_store()


class TestSnapshotRecordsSixStepsInOrder:
    """test_snapshot_records_five_steps_in_order."""

    def test_snapshot_records_four_steps_in_order(self) -> None:
        """SNAPSHOT run records coverage init after preflight and before EXTRACT."""
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

        assert result.status == "SUCCEEDED"
        assert len(result.steps) == 8

        expected_names = [
            StepName.DATA_ROOM_INVENTORY_PACKAGE,
            StepName.DATA_ROOM_INGESTION_HANDOFF,
            StepName.INGEST_CHECK,
            StepName.DOCUMENT_PREFLIGHT,
            StepName.METHODOLOGY_COVERAGE_INIT,
            StepName.EXTRACT,
            StepName.GRADE,
            StepName.CALC,
        ]
        for i, step in enumerate(result.steps):
            assert step.step_name == expected_names[i]
            assert step.status == StepStatus.COMPLETED
            assert step.step_order == STEP_ORDER[step.step_name]
            assert step.started_at is not None
            assert step.finished_at is not None


class TestSnapshotStepErrorsPersistedAndReturned:
    """test_snapshot_step_errors_persisted_and_returned."""

    def test_snapshot_step_errors_persisted_and_returned(self) -> None:
        """When EXTRACT fails, step error_code and error_message are persisted."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="SNAPSHOT",
            documents=_make_documents(),
            extract_fn=_stub_extract_failing,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.error_code == "RUNTIMEERROR"
        assert result.block_reason is None

        failed_steps = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed_steps) == 1
        failed = failed_steps[0]
        assert failed.step_name == StepName.EXTRACT
        assert failed.error_code == "RUNTIMEERROR"
        assert "unavailable" in (failed.error_message or "").lower()
        assert failed.finished_at is not None

        completed_steps = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed_steps) == 5
        assert completed_steps[0].step_name == StepName.DATA_ROOM_INVENTORY_PACKAGE
        assert completed_steps[1].step_name == StepName.DATA_ROOM_INGESTION_HANDOFF
        assert completed_steps[2].step_name == StepName.INGEST_CHECK
        assert completed_steps[3].step_name == StepName.DOCUMENT_PREFLIGHT
        assert completed_steps[4].step_name == StepName.METHODOLOGY_COVERAGE_INIT

    def test_empty_documents_sets_no_ingested_documents_block_reason(self) -> None:
        """Empty corpus is an intentional blocked condition, not a generic runtime error."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="SNAPSHOT",
            documents=[],
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.block_reason == "NO_INGESTED_DOCUMENTS"
        assert result.error_code == "NO_INGESTED_DOCUMENTS"

        failed_steps = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed_steps) == 1
        assert failed_steps[0].step_name == StepName.INGEST_CHECK
        assert failed_steps[0].error_code == "NO_INGESTED_DOCUMENTS"

    def test_no_usable_preflight_corpus_sets_no_usable_documents_block_reason(self) -> None:
        """Failed/no-span corpus rows fail at DOCUMENT_PREFLIGHT, not INGEST_CHECK."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_failed_preflight_document()],
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.block_reason == "NO_USABLE_DOCUMENTS"
        assert result.error_code == "NO_USABLE_DOCUMENTS"
        assert [step.step_name for step in result.steps] == [
            StepName.DATA_ROOM_INVENTORY_PACKAGE,
            StepName.DATA_ROOM_INGESTION_HANDOFF,
            StepName.INGEST_CHECK,
            StepName.DOCUMENT_PREFLIGHT,
        ]
        assert StepName.METHODOLOGY_COVERAGE_INIT not in {step.step_name for step in result.steps}

    def test_mixed_corpus_sends_only_eligible_documents_to_extract(self) -> None:
        """Broken documents must not leak into EXTRACT inputs."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        seen_documents: list[dict[str, Any]] = []

        def recording_extract(**kwargs: Any) -> dict[str, Any]:
            seen_documents.extend(kwargs["documents"])
            return _stub_extract(**kwargs)

        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)
        usable = _make_preflight_document(document_id="doc-usable")

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[usable, _make_failed_preflight_document()],
            extract_fn=recording_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "SUCCEEDED"
        assert [doc["document_id"] for doc in seen_documents] == ["doc-usable"]
        assert ctx.documents == [usable]

    def test_document_preflight_step_summary_has_no_raw_span_text(self) -> None:
        """Run step summary keeps safe span references, not text excerpts."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "SUCCEEDED"
        preflight_step = next(
            step for step in result.steps if step.step_name == StepName.DOCUMENT_PREFLIGHT
        )
        assert "Highly sensitive raw revenue sentence" not in str(preflight_step.result_summary)
        assert "text_excerpt" not in str(preflight_step.result_summary)

    def test_methodology_coverage_init_summary_has_safe_identifiers_only(self) -> None:
        """Coverage init summary stores IDs and counts, not document or question text."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "SUCCEEDED"
        coverage_step = next(
            step for step in result.steps if step.step_name == StepName.METHODOLOGY_COVERAGE_INIT
        )
        assert "Highly sensitive raw revenue sentence" not in str(coverage_step.result_summary)
        assert "text_excerpt" not in str(coverage_step.result_summary)
        assert "question_text" not in str(coverage_step.result_summary)
        assert coverage_step.result_summary["methodology_id"] == "commercial_dd"
        assert coverage_step.result_summary["coverage_record_ids"]
        assert ctx.methodology_coverage_records

    def test_generic_preflight_runtime_failure_does_not_become_block_reason(self) -> None:
        """Unexpected preflight exceptions are not intentional business blockers."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        def failing_preflight(**kwargs: Any) -> Any:
            raise RuntimeError("classification dependency failed")

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            document_preflight_fn=failing_preflight,
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.error_code == "RUNTIMEERROR"
        assert result.block_reason is None
        assert result.steps[-1].step_name == StepName.DOCUMENT_PREFLIGHT

    def test_generic_coverage_init_runtime_failure_does_not_become_block_reason(self) -> None:
        """Unexpected coverage-init exceptions are not intentional business blockers."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        def failing_coverage_init(**kwargs: Any) -> Any:
            raise RuntimeError("coverage service failed")

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            methodology_coverage_init_fn=failing_coverage_init,
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.error_code == "RUNTIMEERROR"
        assert result.block_reason is None
        assert result.steps[-1].step_name == StepName.METHODOLOGY_COVERAGE_INIT

    def test_resume_reattaches_completed_coverage_records_without_rerunning_init_fn(self) -> None:
        """A resumed context keeps in-memory coverage available after skipping the step."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)
        run_id = str(uuid.uuid4())

        first_ctx = RunContext(
            run_id=run_id,
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )
        first_result = orchestrator.execute(first_ctx)
        assert first_result.status == "SUCCEEDED"

        init_calls = 0

        def counting_coverage_init(**kwargs: Any) -> Any:
            nonlocal init_calls
            init_calls += 1
            raise AssertionError("completed coverage init should not rerun injected init fn")

        resumed_ctx = RunContext(
            run_id=run_id,
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            methodology_coverage_init_fn=counting_coverage_init,
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        resumed_result = orchestrator.execute(resumed_ctx)

        assert resumed_result.status == "SUCCEEDED"
        assert init_calls == 0
        assert resumed_ctx.methodology_coverage_records

    def test_resume_reattaches_completed_planned_tasks_without_injected_planner(self) -> None:
        """A resumed FULL context keeps planned tasks available after skipping planning."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)
        run_id = str(uuid.uuid4())

        first_ctx = RunContext(
            run_id=run_id,
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="FULL",
            documents=[],
            deal_metadata={"tenant_id": TENANT_A, "company_name": "Acme Corp"},
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
            enrich_fn=_stub_enrichment,
            debate_fn=_stub_debate,
            layer2_ic_challenge_fn=_stub_layer2_ic_challenge,
            analysis_fn=_stub_analysis,
            scoring_fn=_stub_scoring,
            deliverables_fn=_stub_deliverables,
        )
        first_result = orchestrator.execute(first_ctx)
        assert first_result.status == "SUCCEEDED"
        assert first_ctx.methodology_extraction_tasks

        planning_calls = 0

        def forbidden_planning_fn(**kwargs: Any) -> Any:
            nonlocal planning_calls
            planning_calls += 1
            raise AssertionError("completed task planning should not call injected planner")

        resumed_ctx = RunContext(
            run_id=run_id,
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="FULL",
            documents=[],
            deal_metadata={"tenant_id": TENANT_A, "company_name": "Acme Corp"},
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            methodology_extraction_task_planning_fn=forbidden_planning_fn,
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
            enrich_fn=_stub_enrichment,
            debate_fn=_stub_debate,
            layer2_ic_challenge_fn=_stub_layer2_ic_challenge,
            analysis_fn=_stub_analysis,
            scoring_fn=_stub_scoring,
            deliverables_fn=_stub_deliverables,
        )

        resumed_result = orchestrator.execute(resumed_ctx)

        assert resumed_result.status == "SUCCEEDED"
        assert planning_calls == 0
        assert resumed_ctx.methodology_coverage_records
        assert resumed_ctx.methodology_extraction_tasks

    def test_zero_extraction_tasks_fail_closed_with_business_blocker(self) -> None:
        """A truly empty task plan is an intentional blocked condition."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        def empty_planning_fn(**kwargs: Any) -> Any:
            return (
                ExtractionTaskPlanningRunResult.from_tasks(
                    tenant_id=kwargs["tenant_id"],
                    deal_id=kwargs["deal_id"],
                    run_id=kwargs["run_id"],
                    tasks=[],
                ),
                [],
            )

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="FULL",
            documents=[],
            deal_metadata={"tenant_id": TENANT_A, "company_name": "Acme Corp"},
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            methodology_extraction_task_planning_fn=empty_planning_fn,
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
            enrich_fn=_stub_enrichment,
            debate_fn=_stub_debate,
            layer2_ic_challenge_fn=_stub_layer2_ic_challenge,
            analysis_fn=_stub_analysis,
            scoring_fn=_stub_scoring,
            deliverables_fn=_stub_deliverables,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.error_code == "NO_ELIGIBLE_EXTRACTION_TASKS"
        assert result.block_reason == "NO_ELIGIBLE_EXTRACTION_TASKS"
        assert result.steps[-1].step_name == StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING
        assert result.steps[-1].result_summary["status"] == "FAILED"


def _stub_enrichment(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
) -> dict[str, Any]:
    """Deterministic enrichment stub returning zero results."""
    return {
        "provider_count": 0,
        "result_count": 0,
        "blocked_count": 0,
        "enrichment_refs": {},
    }


def _stub_debate(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    rag_retrieval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic debate stub returning fixed output."""
    return {
        "debate_id": run_id,
        "stop_reason": "MAX_ROUNDS",
        "round_number": 5,
        "muhasabah_passed": True,
        "agent_output_count": 10,
    }


def _stub_layer2_ic_challenge(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    debate_summary: dict[str, Any],
    created_claim_ids: list[str],
    calc_ids: list[str],
    graph_evidence: dict[str, Any] | None = None,
    rag_evidence: dict[str, Any] | None = None,
    enrichment_refs: dict[str, Any] | None = None,
    vep_package_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministic Layer 2 IC challenge stub returning safe refs only."""
    return {
        "status": "completed",
        "layer2_challenge_ids": [f"layer2-{run_id[:8]}"],
        "source_debate_ids": [str(debate_summary["debate_id"])],
        "claim_ids": sorted(created_claim_ids),
        "calc_ids": sorted(calc_ids),
        "graph_ref_ids": sorted((graph_evidence or {}).get("retrieval_ids", [])),
        "rag_ref_ids": sorted((rag_evidence or {}).get("match_ids", [])),
        "enrichment_ref_ids": sorted((enrichment_refs or {}).keys()),
        "finding_count": 1,
        "unresolved_question_count": 1,
        "muhasabah_passed": True,
    }


def _stub_analysis(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    enrichment_refs: dict[str, Any],
    rag_retrieval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic analysis stub."""
    return {
        "agent_count": 8,
        "report_ids": ["report-001"],
        "bundle_id": f"bundle-{run_id[:8]}",
    }


def _stub_scoring(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    analysis_bundle: Any,
    analysis_context: Any,
) -> dict[str, Any]:
    """Deterministic scoring stub."""
    return {
        "composite_score": 72.5,
        "band": "MEDIUM",
        "routing": "HOLD",
    }


def _stub_deliverables(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    analysis_bundle: Any,
    analysis_context: Any,
    scorecard: Any,
    graph_evidence: dict[str, Any] | None = None,
    rag_evidence: dict[str, Any] | None = None,
    layer2_evidence: dict[str, Any] | None = None,
    enrichment_evidence: dict[str, Any] | None = None,
    vep_evidence: dict[str, Any] | None = None,
    run_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic deliverables stub."""
    return {
        "deliverable_count": 4,
        "types": ["IC_MEMO", "QA_BRIEF", "SCREENING_SNAPSHOT", "TRUTH_DASHBOARD"],
        "deliverable_ids": ["del-001", "del-002", "del-003", "del-004"],
    }


class TestFullCompletesAllSteps:
    """test_full_completes_all_nine_steps."""

    def test_full_completes_all_twenty_seven_steps(self) -> None:
        """FULL run completes all 28 steps in canonical order."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="FULL",
            documents=_make_documents(),
            deal_metadata={"tenant_id": TENANT_A, "company_name": "Acme Corp"},
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
            enrich_fn=_stub_enrichment,
            debate_fn=_stub_debate,
            layer2_ic_challenge_fn=_stub_layer2_ic_challenge,
            analysis_fn=_stub_analysis,
            scoring_fn=_stub_scoring,
            deliverables_fn=_stub_deliverables,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "SUCCEEDED"
        assert result.block_reason is None

        completed = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed) == 28
        assert [s.step_name for s in completed] == [
            StepName.DATA_ROOM_INVENTORY_PACKAGE,
            StepName.DATA_ROOM_INGESTION_HANDOFF,
            StepName.INGEST_CHECK,
            StepName.DOCUMENT_PREFLIGHT,
            StepName.METHODOLOGY_COVERAGE_INIT,
            StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING,
            StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION,
            StepName.METHODOLOGY_CLAIM_MATERIALIZATION,
            StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION,
            StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING,
            StepName.METHODOLOGY_DETERMINISTIC_CALCULATION,
            StepName.METHODOLOGY_TRUTH_DASHBOARD,
            StepName.METHODOLOGY_EVIDENCE_TRUST_COURT,
            StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
            StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN,
            StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE,
            StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE,
            StepName.EXTRACT,
            StepName.GRADE,
            StepName.CALC,
            StepName.GRAPH_EVIDENCE,
            StepName.RAG_EVIDENCE,
            StepName.ENRICHMENT,
            StepName.DEBATE,
            StepName.LAYER2_IC_CHALLENGE,
            StepName.ANALYSIS,
            StepName.SCORING,
            StepName.DELIVERABLES,
        ]
        planning_step = next(
            step
            for step in completed
            if step.step_name == StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING
        )
        assert planning_step.result_summary["task_ids"]
        assert ctx.methodology_extraction_tasks
        layer2_step = next(
            step for step in completed if step.step_name == StepName.LAYER2_IC_CHALLENGE
        )
        assert layer2_step.result_summary["source_debate_ids"]
        assert "raw_text" not in json.dumps(layer2_step.result_summary, sort_keys=True)


class TestCrossTenantRunStepReadReturns404:
    """test_cross_tenant_run_step_read_returns_404."""

    def test_cross_tenant_run_step_read_returns_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cross-tenant GET /v1/runs/{runId} returns 404 with no existence leak."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        audit_sink = InMemoryAuditSink()
        app = create_app(audit_sink=audit_sink, service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Test Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        app.state.deal_documents[deal_id] = _make_documents()

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert run_resp.status_code == 202
        run_id = run_resp.json()["run_id"]

        cross_resp = client.get(
            f"/v1/runs/{run_id}",
            headers={"X-IDIS-API-Key": API_KEY_B},
        )
        assert cross_resp.status_code == 404
        body = cross_resp.json()
        assert body["code"] == "NOT_FOUND"
        details = body.get("details") or {}
        assert "run_id" not in details


class TestStartRunPreflightCorpusBehavior:
    """API start-run behavior for full preflight corpus checks."""

    def test_api_no_corpus_fails_before_run_creation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No corpus rows still returns NO_INGESTED_DOCUMENTS before a run response exists."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        audit_sink = InMemoryAuditSink()
        app = create_app(audit_sink=audit_sink, service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "No Corpus Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )

        assert run_resp.status_code == 400
        body = run_resp.json()
        assert body["code"] == "NO_INGESTED_DOCUMENTS"
        assert "run_id" not in body

    def test_api_corpus_exists_but_no_usable_docs_fails_document_preflight(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No usable docs creates a run and fails at DOCUMENT_PREFLIGHT."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        audit_sink = InMemoryAuditSink()
        app = create_app(audit_sink=audit_sink, service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Failed Corpus Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        failed_doc = _make_failed_preflight_document()
        failed_doc["deal_id"] = deal_id
        app.state.deal_documents[deal_id] = [failed_doc]

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )

        assert run_resp.status_code == 202
        body = run_resp.json()
        assert body["status"] == "FAILED"
        assert body["block_reason"] == "NO_USABLE_DOCUMENTS"
        assert [step["step_name"] for step in body["steps"]] == [
            "DATA_ROOM_INVENTORY_PACKAGE",
            "DATA_ROOM_INGESTION_HANDOFF",
            "INGEST_CHECK",
            "DOCUMENT_PREFLIGHT",
        ]

    def test_api_mixed_corpus_continues_with_eligible_docs_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mixed usable/unusable corpus should continue through extraction."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        audit_sink = InMemoryAuditSink()
        app = create_app(audit_sink=audit_sink, service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Mixed Corpus Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        usable_doc = _make_preflight_document(document_id="doc-usable")
        usable_doc["deal_id"] = deal_id
        usable_doc["spans"][0]["deal_id"] = deal_id
        failed_doc = _make_failed_preflight_document()
        failed_doc["deal_id"] = deal_id
        app.state.deal_documents[deal_id] = [usable_doc, failed_doc]

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )

        assert run_resp.status_code == 202
        body = run_resp.json()
        assert body["status"] == "SUCCEEDED"
        assert body["block_reason"] is None
        assert [step["step_name"] for step in body["steps"]] == [
            "DATA_ROOM_INVENTORY_PACKAGE",
            "DATA_ROOM_INGESTION_HANDOFF",
            "INGEST_CHECK",
            "DOCUMENT_PREFLIGHT",
            "METHODOLOGY_COVERAGE_INIT",
            "EXTRACT",
            "GRADE",
            "CALC",
        ]

    def test_api_rejects_unknown_and_path_like_run_source_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Public runs API must not accept local path or unrecognized source fields."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Strict Source Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        app.state.deal_documents[deal_id] = [_make_preflight_document(document_id="doc-1")]

        unknown_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT", "surprise": True},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        path_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={
                "mode": "SNAPSHOT",
                "source": {
                    "type": "deal_documents",
                    "document_ids": ["doc-1"],
                    "data_room_root_path": "C:/unsafe/data-room",
                },
            },
            headers={"X-IDIS-API-Key": API_KEY_A},
        )

        assert unknown_resp.status_code == 400
        assert unknown_resp.json()["code"] == "INVALID_REQUEST"
        assert path_resp.status_code == 400
        assert path_resp.json()["code"] == "INVALID_REQUEST"

    def test_api_source_selects_only_requested_durable_documents(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Selected document refs should narrow the corpus before extraction."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        captured_documents: list[dict[str, Any]] = []

        def capture_extract(**kwargs: Any) -> dict[str, Any]:
            captured_documents.extend(kwargs["documents"])
            return _stub_extract(**kwargs)

        monkeypatch.setattr("idis.api.routes.runs._run_snapshot_extraction", capture_extract)
        app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Selected Corpus Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        doc_1 = _make_preflight_document(document_id="doc-1")
        doc_2 = _make_preflight_document(document_id="doc-2")
        for doc in (doc_1, doc_2):
            doc["deal_id"] = deal_id
            doc["spans"][0]["deal_id"] = deal_id
        app.state.deal_documents[deal_id] = [doc_1, doc_2]

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={
                "mode": "SNAPSHOT",
                "source": {"type": "deal_documents", "document_ids": ["doc-2"]},
            },
            headers={"X-IDIS-API-Key": API_KEY_A},
        )

        assert run_resp.status_code == 202
        assert [doc["document_id"] for doc in captured_documents] == ["doc-2"]

    def test_api_source_rejects_missing_or_cross_deal_documents(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Selected document IDs must exist in the target deal corpus before run creation."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Missing Source Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        app.state.deal_documents[deal_id] = [_make_preflight_document(document_id="doc-present")]

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={
                "mode": "SNAPSHOT",
                "source": {"type": "deal_documents", "document_ids": ["doc-missing"]},
            },
            headers={"X-IDIS-API-Key": API_KEY_A},
        )

        assert run_resp.status_code == 400
        body = run_resp.json()
        assert body["code"] == "INVALID_RUN_SOURCE"
        assert "run_id" not in body


class TestAuditFailureAbortsRunFailClosed:
    """test_audit_failure_aborts_run_fail_closed."""

    def test_audit_failure_aborts_run_fail_closed(self) -> None:
        """AuditSinkError during step execution propagates as 500 AUDIT_FAILURE."""

        class FailingAuditSink:
            """Audit sink that raises on every emit call."""

            def emit(self, event: dict[str, Any]) -> None:
                """Always fail."""
                raise AuditSinkError("Disk full")

        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(
            audit_sink=FailingAuditSink(),
            run_steps_repo=repo,
        )

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

        with pytest.raises(AuditSinkError, match="Disk full"):
            orchestrator.execute(ctx)
