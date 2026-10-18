"""Tests for RunOrchestrator step ledger - Phase 5 orchestration.

Module-wide harness ``_clear_stores`` sets ``IDIS_REQUIRE_FULL_LIVE=0`` and
removes ``IDIS_DATABASE_URL`` before every test, including held cases. This
module does not replace ``asyncio.run`` or ``asyncio.to_thread``; that override
lives in the pipeline-worker harness. Step-level corpus positives that inject
documents into in-memory deal state are component injections, not proof that
production can freeze those corpora.

Covers:
- SNAPSHOT records seven steps in order
  (DATA_ROOM_INVENTORY_PACKAGE -> DATA_ROOM_INGESTION_HANDOFF -> INGEST_CHECK -> DOCUMENT_PREFLIGHT
  -> METHODOLOGY_COVERAGE_INIT -> EXTRACT -> GRADE -> CALC)
- Step errors persisted and returned
- FULL completes all 12 steps in correct order
- Cross-tenant run step read returns 404 (no existence leak)
- Audit failure aborts run fail-closed

Updated for Phase 3.0 Slice 4: FULL mode now has 12 steps.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from pathlib import Path
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
from tests._cir04c3_required_frozen_preview import (
    CapturingRequiredPostgresAuditSink,
    required_frozen_preview,
)
from tests.abac_seed import seed_deal_access

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
    resolved_spans = (
        spans
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
        ]
    )
    resolved_metadata = (
        metadata
        if metadata is not None
        else {
            "parser_doc_type": "DOCX",
            "detected_format": "DOCX",
            "parser_runtime_status": "completed",
            "span_count": len(resolved_spans),
            "parse_error_codes": [],
            "parse_warning_codes": [],
        }
    )
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
        "metadata": resolved_metadata,
        "source_metadata": {},
        "spans": resolved_spans,
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
def _clear_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset in-memory stores before each test."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
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

    def test_cross_tenant_run_step_read_returns_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Cross-tenant GET /v1/runs/{runId} returns 404 with no existence leak."""
        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())
        api_keys = _make_api_keys()
        api_keys[API_KEY_A]["tenant_id"] = tenant_a
        api_keys[API_KEY_B]["tenant_id"] = tenant_b
        with required_frozen_preview(monkeypatch) as preview_db:
            preview_db.add_tenant(tenant_a, "me-south-1")
            preview_db.add_tenant(tenant_b, "me-south-1")
            monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys))
            audit_sink = InMemoryAuditSink()
            app = create_app(
                audit_sink=CapturingRequiredPostgresAuditSink(audit_sink),
                service_region="me-south-1",
            )
            client = TestClient(app)

            create_resp = client.post(
                "/v1/deals",
                json={"name": "Test Deal", "company_name": "TestCo"},
                headers={"X-IDIS-API-Key": API_KEY_A},
            )
            assert create_resp.status_code == 201
            deal_id = create_resp.json()["deal_id"]
            seed_deal_access(tenant_a, deal_id, "actor-a")
            preview_db.prepare_pdf(tenant_a, deal_id, tmp_path / "preview-cross-tenant")

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


def _open_preview_package(
    preview_db: Any,
    *,
    tenant_id: str,
    deal_id: str,
    root: Path,
    payloads: list[bytes],
) -> list[str]:
    """Ingest bytes into one OPEN manifest package. Does not freeze or attest."""
    from idis.audit.postgres_sink import PostgresAuditSink
    from idis.persistence.repositories.data_room_packages import PostgresDataRoomPackagesRepository
    from idis.persistence.repositories.documents import PostgresDocumentsRepository
    from idis.services.data_room.package_service import create_data_room_package
    from idis.services.ingestion import IngestionContext, IngestionService
    from idis.storage.compliant_store import ComplianceEnforcedStore
    from idis.storage.filesystem_store import FilesystemObjectStore
    from idis.tools.preview_source_preparation import create_preview_preparation

    preparation = create_preview_preparation(
        tenant_id=tenant_id,
        deal_id=deal_id,
        files=[("pdf", payload) for payload in payloads],
    )
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=root))
    uploaded: list[str] = []
    with preview_db.engine.begin() as conn:
        service = IngestionService(
            compliant_store=store,
            audit_sink=PostgresAuditSink(),
            db_conn=conn,
        )
        ctx = IngestionContext(
            tenant_id=uuid.UUID(tenant_id), actor_id="actor-a", request_id="c3b3"
        )
        for sequence, payload in enumerate(payloads):
            result = service.ingest_bytes(
                ctx=ctx,
                deal_id=uuid.UUID(deal_id),
                filename=preparation.files[sequence].generated_upload_name,
                media_type="application/pdf",
                data=payload,
                metadata=preparation.upload_metadata(sequence),
            )
            assert result.document_id is not None
            uploaded.append(str(result.document_id))
        documents_repository = PostgresDocumentsRepository(conn, tenant_id)
        documents = []
        for document_id in uploaded:
            document = documents_repository.get_document(document_id)
            if document is None:
                raise AssertionError("uploaded document was not persisted")
            documents.append(document)
        manifest = preparation.build_manifest(documents)
        create_data_room_package(
            repo=PostgresDataRoomPackagesRepository(conn, tenant_id),
            tenant_id=tenant_id,
            deal_id=deal_id,
            created_by_actor_id="actor-a",
            created_by_actor_type="SERVICE",
            document_ids=uploaded,
            documents=documents,
            source_manifest=manifest,
        )
    return uploaded


def _install_watermark_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    tenant_id: str,
    fingerprint: str,
    pdf_bytes: bytes,
    safe_alias: str,
) -> None:
    """Configure the app authority before create_app reads the signer environment."""
    from tests._cir04c3_exclusion_signer import (
        configure_watermark_runtime,
        write_signed_watermark_authority,
    )

    configure_watermark_runtime(
        monkeypatch,
        write_signed_watermark_authority(
            tmp_path,
            tenant_id=tenant_id,
            safe_alias=safe_alias,
            source_tree_fingerprint=fingerprint,
            pdf_bytes=pdf_bytes,
        ),
    )
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))


def _open_signed_company_package(
    engine: Any,
    *,
    tenant_id: str,
    deal_id: str,
    root: Path,
    members: list[tuple[str, bytes]],
    source_tree_fingerprint: str,
) -> list[str]:
    """Ingest named company-001 members, including FAILED exclusions, into one package."""
    from idis.audit.postgres_sink import PostgresAuditSink
    from idis.persistence.repositories.data_room_packages import PostgresDataRoomPackagesRepository
    from idis.persistence.repositories.documents import PostgresDocumentsRepository
    from idis.services.data_room.package_service import create_data_room_package
    from idis.services.ingestion import IngestionContext, IngestionService
    from idis.storage.compliant_store import ComplianceEnforcedStore
    from idis.storage.filesystem_store import FilesystemObjectStore
    from tests._cir04c3_exclusion_signer import (
        build_bound_source_manifest,
        watermark_service_options,
    )

    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=root))
    uploaded: list[str] = []
    with engine.begin() as conn:
        service = IngestionService(
            compliant_store=store,
            audit_sink=PostgresAuditSink(),
            db_conn=conn,
            **watermark_service_options(),
        )
        ctx = IngestionContext(
            tenant_id=uuid.UUID(tenant_id),
            actor_id="actor-a",
            request_id="c3-batch4-exclusion",
        )
        for filename, payload in members:
            result = service.ingest_bytes(
                ctx=ctx,
                deal_id=uuid.UUID(deal_id),
                filename=filename,
                media_type="application/pdf",
                data=payload,
                metadata={
                    "source_system": "c3-watermark-exclusion",
                    "upload_filename": filename,
                    "source_tree_fingerprint": source_tree_fingerprint,
                },
            )
            assert result.document_id is not None
            uploaded.append(str(result.document_id))
        documents_repository = PostgresDocumentsRepository(conn, tenant_id)
        documents = []
        for document_id in uploaded:
            document = documents_repository.get_document(document_id)
            if document is None:
                raise AssertionError("uploaded document was not persisted")
            documents.append(document)
        manifest = build_bound_source_manifest(
            documents,
            source_tree_fingerprint=source_tree_fingerprint,
        )
        ordered_ids = [str(item.document_id) for item in manifest.files]
        create_data_room_package(
            repo=PostgresDataRoomPackagesRepository(conn, tenant_id),
            tenant_id=tenant_id,
            deal_id=deal_id,
            created_by_actor_id="actor-a",
            created_by_actor_type="SERVICE",
            document_ids=ordered_ids,
            documents=documents,
            source_manifest=manifest,
        )
    return ordered_ids


def _assert_registered_exclusion_ref(engine: Any, *, tenant_id: str, document_id: str) -> None:
    """The persisted v2 receipt is wrapped in the one registered v1 ref."""
    from sqlalchemy import text

    from idis.persistence.db import set_tenant_local
    from idis.persistence.repositories.task7_source_freeze import _SOURCE_SCHEMA_AUTHORITIES

    with engine.begin() as conn:
        set_tenant_local(conn, tenant_id)
        metadata = conn.execute(
            text("SELECT metadata FROM documents WHERE document_id=CAST(:document AS uuid)"),
            {"document": document_id},
        ).scalar_one()
        file_status = conn.execute(
            text(
                """
                SELECT files.file_status
                FROM data_room_package_files AS files
                JOIN documents
                  ON documents.deal_id = files.deal_id
                JOIN document_artifacts
                  ON document_artifacts.doc_id = documents.doc_id
                 AND document_artifacts.sha256 = files.sha256
                WHERE documents.document_id = CAST(:document AS uuid)
                """
            ),
            {"document": document_id},
        ).scalar_one()
    receipt = metadata["document_no_substantive_content_receipt"]
    schema_sha256 = _SOURCE_SCHEMA_AUTHORITIES[
        ("no-substantive-content-receipt", "document-no-substantive-content-receipt-v1")
    ]
    assert metadata["applicable_schema_refs"] == [
        {
            "schema_id": "no-substantive-content-receipt",
            "schema_version": "document-no-substantive-content-receipt-v1",
            "schema_sha256": schema_sha256,
            "validated_payload": receipt,
        }
    ]
    assert receipt["schema_version"] == "document-no-substantive-content-receipt-v2"
    assert receipt["whole_document_terminal"] is True
    assert file_status == "blocked"


def _assert_exclusion_runtime_attempt(
    engine: Any,
    *,
    tenant_id: str,
    document_id: str,
    content: bytes,
) -> None:
    """The exclusion member has one runtime attempt over the stored projection."""
    from sqlalchemy import text

    from idis.persistence.db import set_tenant_local
    from idis.persistence.repositories.task7_source_freeze import (
        _sha256_json,
        _terminal_document_projection,
    )

    content_sha256 = hashlib.sha256(content).hexdigest()
    with engine.begin() as conn:
        set_tenant_local(conn, tenant_id)
        rows = (
            conn.execute(
                text(
                    "SELECT * FROM document_parse_attempts "
                    "WHERE document_id = CAST(:document AS uuid)"
                ),
                {"document": document_id},
            )
            .mappings()
            .all()
        )
        document = (
            conn.execute(
                text(
                    "SELECT doc_type, parse_status, metadata FROM documents "
                    "WHERE document_id = CAST(:document AS uuid)"
                ),
                {"document": document_id},
            )
            .mappings()
            .one()
        )
        artifact_metadata = conn.execute(
            text(
                """
                SELECT artifacts.metadata
                FROM document_artifacts AS artifacts
                JOIN documents ON documents.doc_id = artifacts.doc_id
                WHERE documents.document_id = CAST(:document AS uuid)
                """
            ),
            {"document": document_id},
        ).scalar_one()
    assert len(rows) == 1
    attempt = rows[0]
    assert attempt["write_origin"] == "RUNTIME"
    assert attempt["status"] == "FAILED"
    assert attempt["attempt_ordinal"] == 1
    assert attempt["input_sha256"] == content_sha256
    assert attempt["parser_name"] == "idis-ingestion/pre-ocr-watermark/pdf"
    assert str(attempt["parser_version"]).startswith("idis-watermark-pre-ocr-runtime-v1:")
    assert "tesseract" not in str(attempt["parser_name"])
    assert "pypdf" not in str(attempt["parser_version"])
    projection = _terminal_document_projection(
        document["metadata"],
        doc_type=document["doc_type"],
        parse_status=document["parse_status"],
        spans=[],
    )
    assert attempt["receipt_sha256"] == _sha256_json(
        [
            "terminal-document-metadata-v2",
            document["doc_type"],
            document["parse_status"],
            projection,
        ]
    )
    assert "source_preparation_prerequisite" not in document["metadata"]
    assert "source_preparation_prerequisite" not in artifact_metadata
    assert document["metadata"]["parser_mode"] == "ocr"


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
        seed_deal_access(TENANT_A, deal_id, "actor-a")

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
        tmp_path: Path,
    ) -> None:
        """A package of one signed watermark exclusion fails at DOCUMENT_PREFLIGHT."""
        from tests._cir04c3_exclusion_signer import EXCLUSION_UPLOAD_NAME, watermark_only_pdf

        tenant_id = str(uuid.uuid4())
        api_keys = _make_api_keys()
        api_keys[API_KEY_A]["tenant_id"] = tenant_id
        pdf_bytes = watermark_only_pdf()
        fingerprint = hashlib.sha256(b"c3-batch4-all-failed-tree").hexdigest()
        with required_frozen_preview(monkeypatch) as preview_db:
            preview_db.add_tenant(tenant_id, "me-south-1")
            _install_watermark_authority(
                monkeypatch,
                tmp_path,
                tenant_id=tenant_id,
                fingerprint=fingerprint,
                pdf_bytes=pdf_bytes,
                safe_alias=EXCLUSION_UPLOAD_NAME,
            )
            monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys))
            app = create_app(
                audit_sink=CapturingRequiredPostgresAuditSink(InMemoryAuditSink()),
                service_region="me-south-1",
            )
            client = TestClient(app)
            create_resp = client.post(
                "/v1/deals",
                json={"name": "Failed Corpus Deal", "company_name": "TestCo"},
                headers={"X-IDIS-API-Key": API_KEY_A},
            )
            assert create_resp.status_code == 201
            deal_id = create_resp.json()["deal_id"]
            seed_deal_access(tenant_id, deal_id, "actor-a")
            document_ids = _open_signed_company_package(
                preview_db.engine,
                tenant_id=tenant_id,
                deal_id=deal_id,
                root=tmp_path / "objects",
                members=[(EXCLUSION_UPLOAD_NAME, pdf_bytes)],
                source_tree_fingerprint=fingerprint,
            )
            _assert_registered_exclusion_ref(
                preview_db.engine,
                tenant_id=tenant_id,
                document_id=document_ids[0],
            )
            _assert_exclusion_runtime_attempt(
                preview_db.engine,
                tenant_id=tenant_id,
                document_id=document_ids[0],
                content=pdf_bytes,
            )

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

    def test_api_all_failed_package_without_attestation_is_refused_before_run_creation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An all-failed encrypted package is refused before a run is created."""
        from sqlalchemy import text

        from idis.persistence.db import set_tenant_local
        from tests.test_pdf_parser import create_encrypted_test_pdf

        tenant_id = str(uuid.uuid4())
        api_keys = _make_api_keys()
        api_keys[API_KEY_A]["tenant_id"] = tenant_id
        with required_frozen_preview(monkeypatch) as preview_db:
            preview_db.add_tenant(tenant_id, "me-south-1")
            monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys))
            app = create_app(
                audit_sink=CapturingRequiredPostgresAuditSink(InMemoryAuditSink()),
                service_region="me-south-1",
            )
            client = TestClient(app)
            create_resp = client.post(
                "/v1/deals",
                json={"name": "Unattested Failed Corpus", "company_name": "TestCo"},
                headers={"X-IDIS-API-Key": API_KEY_A},
            )
            assert create_resp.status_code == 201
            deal_id = create_resp.json()["deal_id"]
            seed_deal_access(tenant_id, deal_id, "actor-a")
            _open_preview_package(
                preview_db,
                tenant_id=tenant_id,
                deal_id=deal_id,
                root=tmp_path / "unattested",
                payloads=[
                    create_encrypted_test_pdf(
                        ["PUBLIC SYNTHETIC UNATTESTED"],
                        user_password=secrets.token_hex(8),
                    )
                ],
            )
            run_resp = client.post(
                f"/v1/deals/{deal_id}/runs",
                json={"mode": "SNAPSHOT"},
                headers={"X-IDIS-API-Key": API_KEY_A},
            )
            assert run_resp.status_code == 409
            body = run_resp.json()
            assert body["code"] == "SOURCE_PREPARATION_PREREQUISITE_UNMET"
            assert body.get("details") is None
            with preview_db.engine.begin() as conn:
                set_tenant_local(conn, tenant_id)
                assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0

    def test_api_mixed_corpus_continues_with_eligible_docs_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """One parsed PDF plus one signed exclusion continues through CALC."""
        from tests._cir04c3_exclusion_signer import (
            EXCLUSION_UPLOAD_NAME,
            PARSED_UPLOAD_NAME,
            watermark_only_pdf,
        )
        from tests.test_pdf_parser import create_test_pdf

        tenant_id = str(uuid.uuid4())
        api_keys = _make_api_keys()
        api_keys[API_KEY_A]["tenant_id"] = tenant_id
        exclusion_bytes = watermark_only_pdf()
        parsed_bytes = create_test_pdf(["PUBLIC SYNTHETIC MIXED CORPUS REVENUE"])
        fingerprint = hashlib.sha256(b"c3-batch4-mixed-tree").hexdigest()
        with required_frozen_preview(monkeypatch) as preview_db:
            preview_db.add_tenant(tenant_id, "me-south-1")
            _install_watermark_authority(
                monkeypatch,
                tmp_path,
                tenant_id=tenant_id,
                fingerprint=fingerprint,
                pdf_bytes=exclusion_bytes,
                safe_alias=EXCLUSION_UPLOAD_NAME,
            )
            monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys))
            app = create_app(
                audit_sink=CapturingRequiredPostgresAuditSink(InMemoryAuditSink()),
                service_region="me-south-1",
            )
            client = TestClient(app)
            create_resp = client.post(
                "/v1/deals",
                json={"name": "Mixed Corpus Deal", "company_name": "TestCo"},
                headers={"X-IDIS-API-Key": API_KEY_A},
            )
            assert create_resp.status_code == 201
            deal_id = create_resp.json()["deal_id"]
            seed_deal_access(tenant_id, deal_id, "actor-a")
            document_ids = _open_signed_company_package(
                preview_db.engine,
                tenant_id=tenant_id,
                deal_id=deal_id,
                root=tmp_path / "objects",
                members=[
                    (EXCLUSION_UPLOAD_NAME, exclusion_bytes),
                    (PARSED_UPLOAD_NAME, parsed_bytes),
                ],
                source_tree_fingerprint=fingerprint,
            )
            _assert_registered_exclusion_ref(
                preview_db.engine,
                tenant_id=tenant_id,
                document_id=document_ids[0],
            )
            _assert_exclusion_runtime_attempt(
                preview_db.engine,
                tenant_id=tenant_id,
                document_id=document_ids[0],
                content=exclusion_bytes,
            )

            run_resp = client.post(
                f"/v1/deals/{deal_id}/runs",
                json={"mode": "SNAPSHOT"},
                headers={"X-IDIS-API-Key": API_KEY_A},
            )
            from sqlalchemy import text

            from idis.persistence.db import set_tenant_local

            assert run_resp.status_code == 202
            with preview_db.engine.begin() as conn:
                set_tenant_local(conn, tenant_id)
                preflight_summary = conn.execute(
                    text(
                        "SELECT result_summary FROM run_steps "
                        "WHERE run_id = CAST(:run_id AS uuid) "
                        "AND step_name = 'DOCUMENT_PREFLIGHT'"
                    ),
                    {"run_id": run_resp.json()["run_id"]},
                ).scalar_one()
            if isinstance(preflight_summary, str):
                preflight_summary = json.loads(preflight_summary)
            extraction_document_ids = preflight_summary["eligible_document_ids"]

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
        assert extraction_document_ids == [document_ids[1]]

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
        seed_deal_access(TENANT_A, deal_id, "actor-a")
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
        tmp_path: Path,
    ) -> None:
        """Selected document refs should narrow the corpus before extraction."""
        tenant_a = str(uuid.uuid4())
        api_keys = _make_api_keys()
        api_keys[API_KEY_A]["tenant_id"] = tenant_a
        with required_frozen_preview(monkeypatch) as preview_db:
            preview_db.add_tenant(tenant_a, "me-south-1")
            monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys))
            captured_documents: list[dict[str, Any]] = []

            def capture_extract(**kwargs: Any) -> dict[str, Any]:
                captured_documents.extend(kwargs["documents"])
                return _stub_extract(**kwargs)

            monkeypatch.setattr("idis.api.routes.runs._run_snapshot_extraction", capture_extract)
            audit_sink = InMemoryAuditSink()
            app = create_app(
                audit_sink=CapturingRequiredPostgresAuditSink(audit_sink),
                service_region="me-south-1",
            )
            client = TestClient(app)

            create_resp = client.post(
                "/v1/deals",
                json={"name": "Selected Corpus Deal", "company_name": "TestCo"},
                headers={"X-IDIS-API-Key": API_KEY_A},
            )
            assert create_resp.status_code == 201
            deal_id = create_resp.json()["deal_id"]
            seed_deal_access(tenant_a, deal_id, "actor-a")
            preview_db.prepare_pdf(tenant_a, deal_id, tmp_path / "pkg-1")
            pkg_2 = preview_db.prepare_pdf(tenant_a, deal_id, tmp_path / "pkg-2")
            doc_2 = str(preview_db.package_rows(tenant_a, deal_id, pkg_2)[0]["document_id"])

            run_resp = client.post(
                f"/v1/deals/{deal_id}/runs",
                json={
                    "mode": "SNAPSHOT",
                    "source": {"type": "deal_documents", "document_ids": [doc_2]},
                },
                headers={"X-IDIS-API-Key": API_KEY_A},
            )

            assert run_resp.status_code == 202
            assert [doc["document_id"] for doc in captured_documents] == [doc_2]

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
        seed_deal_access(TENANT_A, deal_id, "actor-a")
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


def test_invalid_attestation_authority_is_a_server_fault_not_a_tenant_prerequisite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong authority type is a server fault, not SOURCE_PREPARATION_PREREQUISITE_UNMET."""
    from types import SimpleNamespace

    from idis.api.routes.runs import _freeze_task7_selected_source_packages
    from idis.models.source_package_manifest import (
        SourcePackageManifest,
        SourcePackageManifestFile,
        canonical_source_manifest_sha256,
    )

    document_id = uuid.uuid4()
    digest = "a" * 64
    other = "b" * 64
    payload = {
        "schema_version": "source-package-manifest-v1",
        "company_alias": "company-999",
        "deal_alias": "deal-999",
        "document_set_alias": "documents-999",
        "run_alias": "run-999",
        "ledger_alias": "ledger-999",
        "package_alias": "package-999",
        "source_tree_fingerprint": other,
        "file_count": 1,
        "total_bytes": 4,
        "files": [
            SourcePackageManifestFile(
                sequence=0,
                generated_upload_name="company-999-document-00001.pdf",
                source_locator_sha256=digest,
                content_sha256=other,
                size_bytes=4,
                extension="pdf",
                document_id=document_id,
                doc_id=uuid.uuid4(),
            ).model_dump(mode="json")
        ],
    }
    payload["manifest_sha256"] = canonical_source_manifest_sha256(payload)
    manifest = SourcePackageManifest.model_validate(payload)

    class _Rows:
        def fetchall(self) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    _mapping={
                        "package_id": str(uuid.uuid4()),
                        "source_manifest": manifest.model_dump(mode="json"),
                    }
                )
            ]

    class _Conn:
        def execute(self, *_args: object, **_kwargs: object) -> _Rows:
            return _Rows()

    monkeypatch.setattr(
        "idis.services.ingestion.no_substantive_content_attestation."
        "load_document_no_substantive_content_attestation",
        lambda **_kwargs: object(),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(db_conn=_Conn()),
        app=SimpleNamespace(state=SimpleNamespace(strict_runtime_profile=None)),
    )
    with pytest.raises(RuntimeError, match="NO_SUBSTANTIVE_CONTENT_ATTESTATION_AUTHORITY_INVALID"):
        _freeze_task7_selected_source_packages(
            request=request,
            tenant_id=str(uuid.uuid4()),
            deal_id=str(uuid.uuid4()),
            selected_document_ids=(str(document_id),),
        )


def _preview_client(
    monkeypatch: pytest.MonkeyPatch,
    preview_db: Any,
    tenant_id: str,
) -> tuple[TestClient, str]:
    api_keys = _make_api_keys()
    api_keys[API_KEY_A]["tenant_id"] = tenant_id
    preview_db.add_tenant(tenant_id, "me-south-1")
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys))
    app = create_app(
        audit_sink=CapturingRequiredPostgresAuditSink(InMemoryAuditSink()),
        service_region="me-south-1",
    )
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/v1/deals",
        json={"name": "Authority Fault Deal", "company_name": "TestCo"},
        headers={"X-IDIS-API-Key": API_KEY_A},
    )
    assert created.status_code == 201
    deal_id = created.json()["deal_id"]
    seed_deal_access(tenant_id, deal_id, "actor-a")
    return client, deal_id


def test_api_invalid_attestation_authority_returns_internal_error_and_creates_no_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Clients see a sanitised 500 and no run when the authority object is the wrong type."""
    from sqlalchemy import text

    from idis.persistence.db import set_tenant_local

    tenant_id = str(uuid.uuid4())
    with required_frozen_preview(monkeypatch) as preview_db:
        client, deal_id = _preview_client(monkeypatch, preview_db, tenant_id)
        package_id = preview_db.prepare_pdf(tenant_id, deal_id, tmp_path / "authority")
        document_id = str(preview_db.package_rows(tenant_id, deal_id, package_id)[0]["document_id"])
        monkeypatch.setattr(
            "idis.services.ingestion.no_substantive_content_attestation."
            "load_document_no_substantive_content_attestation",
            lambda **_kwargs: object(),
        )
        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={
                "mode": "SNAPSHOT",
                "source": {"type": "deal_documents", "document_ids": [document_id]},
            },
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "INTERNAL_ERROR"
        assert body.get("details") is None
        assert "NO_SUBSTANTIVE_CONTENT_ATTESTATION_AUTHORITY_INVALID" not in response.text
        with preview_db.engine.begin() as conn:
            set_tenant_local(conn, tenant_id)
            assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0


def test_api_unmapped_source_freeze_error_is_sanitised_and_commits_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unmapped freeze code is a sanitised 500 and leaves the package unchanged."""
    from sqlalchemy import text

    from idis.persistence.db import set_tenant_local
    from idis.persistence.repositories.task7_source_freeze import Task7SourceFreezeError

    tenant_id = str(uuid.uuid4())

    def _raise_unmapped(**_kwargs: object) -> None:
        raise Task7SourceFreezeError("TASK7_UNMAPPED_SENTINEL")

    with required_frozen_preview(monkeypatch) as preview_db:
        client, deal_id = _preview_client(monkeypatch, preview_db, tenant_id)
        package_id = preview_db.prepare_pdf(tenant_id, deal_id, tmp_path / "unmapped")
        before = preview_db.package_rows(tenant_id, deal_id, package_id)[0]
        document_id = str(before["document_id"])
        monkeypatch.setattr(
            "idis.persistence.repositories.task7_source_freeze.freeze_source_package",
            _raise_unmapped,
        )
        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={
                "mode": "SNAPSHOT",
                "source": {"type": "deal_documents", "document_ids": [document_id]},
            },
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "INTERNAL_ERROR"
        assert body.get("details") is None
        assert body["message"] == "An internal error occurred"
        assert "TASK7_UNMAPPED_SENTINEL" not in response.text
        after = preview_db.package_rows(tenant_id, deal_id, package_id)[0]
        assert after["status"] == before["status"] == "SEALED"
        assert after["source_binding_state"] == before["source_binding_state"] == "BOUND"
        with preview_db.engine.begin() as conn:
            set_tenant_local(conn, tenant_id)
            assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0


def test_api_source_freeze_conflict_uses_a_retry_message_without_internal_codes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SOURCE_FREEZE_CONFLICT tells the caller to retry and hides the freezer code."""
    from idis.persistence.repositories.task7_source_freeze import (
        TASK7_SOURCE_FREEZE_FENCED,
        Task7SourceFreezeError,
    )

    tenant_id = str(uuid.uuid4())

    def _raise_fenced(**_kwargs: object) -> None:
        raise Task7SourceFreezeError(TASK7_SOURCE_FREEZE_FENCED)

    with required_frozen_preview(monkeypatch) as preview_db:
        client, deal_id = _preview_client(monkeypatch, preview_db, tenant_id)
        package_id = preview_db.prepare_pdf(tenant_id, deal_id, tmp_path / "fenced")
        document_id = str(preview_db.package_rows(tenant_id, deal_id, package_id)[0]["document_id"])
        monkeypatch.setattr(
            "idis.persistence.repositories.task7_source_freeze.freeze_source_package",
            _raise_fenced,
        )
        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={
                "mode": "SNAPSHOT",
                "source": {"type": "deal_documents", "document_ids": [document_id]},
            },
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "SOURCE_FREEZE_CONFLICT"
    assert body["message"] == "Source freeze conflict; retry the request"
    assert body.get("details") is None
    assert "TASK7" not in response.text
