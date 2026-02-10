"""Tests for RunOrchestrator new steps wiring — Phase X.

Covers:
- Step ordering: ENRICHMENT before DEBATE in FULL mode
- FULL vs SNAPSHOT enforcement: new steps not in SNAPSHOT
- Missing callable → fail-closed for each new step
- Resume skips completed steps for new steps
- Correct result_summary content per step (ENRICHMENT, ANALYSIS, SCORING, DELIVERABLES)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import (
    FULL_ONLY_STEPS,
    FULL_STEPS,
    IMPLEMENTED_STEPS,
    SNAPSHOT_STEPS,
    STEP_ORDER,
    StepName,
    StepStatus,
)
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
) -> dict[str, Any]:
    """Deterministic debate stub."""
    return {
        "debate_id": run_id,
        "stop_reason": "MAX_ROUNDS",
        "round_number": 5,
        "muhasabah_passed": True,
        "agent_output_count": 10,
    }


def _stub_analysis(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    enrichment_refs: dict[str, Any],
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
) -> dict[str, Any]:
    """Deterministic deliverables stub."""
    return {
        "deliverable_count": 4,
        "types": ["IC_MEMO", "QA_BRIEF", "SCREENING_SNAPSHOT", "TRUTH_DASHBOARD"],
        "deliverable_ids": ["del-001", "del-002", "del-003", "del-004"],
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


def _make_full_ctx(**overrides: Any) -> RunContext:
    """Build a FULL RunContext with all callables. Override specific fields via kwargs."""
    defaults: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": TENANT_A,
        "deal_id": str(uuid.uuid4()),
        "mode": "FULL",
        "documents": _make_documents(),
        "extract_fn": _stub_extract,
        "grade_fn": _stub_grade,
        "calc_fn": _stub_calc,
        "enrich_fn": _stub_enrichment,
        "debate_fn": _stub_debate,
        "analysis_fn": _stub_analysis,
        "scoring_fn": _stub_scoring,
        "deliverables_fn": _stub_deliverables,
    }
    defaults.update(overrides)
    return RunContext(**defaults)


@pytest.fixture(autouse=True)
def _clear_stores() -> None:
    """Reset in-memory stores before each test."""
    clear_run_steps_store()


class TestStepOrderingConstants:
    """Verify step ordering constants match the v6.3 state machine."""

    def test_enrichment_before_debate_in_full_steps(self) -> None:
        """ENRICHMENT appears before DEBATE in FULL_STEPS."""
        enrich_idx = FULL_STEPS.index(StepName.ENRICHMENT)
        debate_idx = FULL_STEPS.index(StepName.DEBATE)
        assert enrich_idx < debate_idx

    def test_full_steps_canonical_order(self) -> None:
        """FULL_STEPS matches the v6.3 canonical order exactly."""
        assert FULL_STEPS == [
            StepName.INGEST_CHECK,
            StepName.EXTRACT,
            StepName.GRADE,
            StepName.CALC,
            StepName.ENRICHMENT,
            StepName.DEBATE,
            StepName.ANALYSIS,
            StepName.SCORING,
            StepName.DELIVERABLES,
        ]

    def test_step_order_dict_consistent_with_full_steps(self) -> None:
        """STEP_ORDER indices match FULL_STEPS positions."""
        for i, step in enumerate(FULL_STEPS):
            assert STEP_ORDER[step] == i

    def test_new_steps_in_implemented(self) -> None:
        """All new steps are in IMPLEMENTED_STEPS."""
        for step in [
            StepName.ENRICHMENT,
            StepName.ANALYSIS,
            StepName.SCORING,
            StepName.DELIVERABLES,
        ]:
            assert step in IMPLEMENTED_STEPS

    def test_full_only_steps_correct(self) -> None:
        """FULL_ONLY_STEPS contains exactly the FULL-only steps."""
        expected = frozenset(set(FULL_STEPS) - set(SNAPSHOT_STEPS))
        assert expected == FULL_ONLY_STEPS


class TestFullVsSnapshotEnforcement:
    """FULL-only steps must not execute in SNAPSHOT mode."""

    def test_snapshot_has_only_four_steps(self) -> None:
        """SNAPSHOT mode completes with only 4 steps (no FULL-only steps)."""
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
        step_names = {s.step_name for s in result.steps}
        for full_only in FULL_ONLY_STEPS:
            assert full_only not in step_names

    def test_full_has_nine_steps(self) -> None:
        """FULL mode completes with all 9 steps."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx()
        result = orchestrator.execute(ctx)

        assert result.status == "COMPLETED"
        assert len(result.steps) == 9


class TestMissingCallableFailClosed:
    """Each new step fails closed when its callable is None."""

    def test_missing_enrich_fn_fails_at_enrichment(self) -> None:
        """FULL run without enrich_fn fails at ENRICHMENT."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx(enrich_fn=None)
        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.error_code == "VALUEERROR"
        assert "enrich_fn not provided" in (result.error_message or "")

        failed = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].step_name == StepName.ENRICHMENT

    def test_missing_analysis_fn_fails_at_analysis(self) -> None:
        """FULL run without analysis_fn fails at ANALYSIS."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx(analysis_fn=None)
        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert "analysis_fn not provided" in (result.error_message or "")

        failed = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].step_name == StepName.ANALYSIS

    def test_missing_scoring_fn_fails_at_scoring(self) -> None:
        """FULL run without scoring_fn fails at SCORING."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx(scoring_fn=None)
        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert "scoring_fn not provided" in (result.error_message or "")

        failed = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].step_name == StepName.SCORING

    def test_missing_deliverables_fn_fails_at_deliverables(self) -> None:
        """FULL run without deliverables_fn fails at DELIVERABLES."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx(deliverables_fn=None)
        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert "deliverables_fn not provided" in (result.error_message or "")

        failed = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].step_name == StepName.DELIVERABLES


class TestResumeSkipsCompletedSteps:
    """Resuming a run skips already-completed steps including new ones."""

    def test_resume_skips_completed_enrichment(self) -> None:
        """If ENRICHMENT is already COMPLETED, orchestrator skips it on resume."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        run_id = str(uuid.uuid4())
        ctx = _make_full_ctx(run_id=run_id)

        result1 = orchestrator.execute(ctx)
        assert result1.status == "COMPLETED"
        assert len(result1.steps) == 9

        call_count = {"enrichment": 0}
        original_enrich = _stub_enrichment

        def counting_enrich(**kwargs: Any) -> dict[str, Any]:
            call_count["enrichment"] += 1
            return original_enrich(**kwargs)

        ctx2 = _make_full_ctx(run_id=run_id, enrich_fn=counting_enrich)
        result2 = orchestrator.execute(ctx2)

        assert result2.status == "COMPLETED"
        assert call_count["enrichment"] == 0


class TestResultSummaryContracts:
    """Each new step writes the correct result_summary keys."""

    def test_enrichment_result_summary(self) -> None:
        """ENRICHMENT step result_summary has provider_count, result_count, blocked_count."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx()
        result = orchestrator.execute(ctx)

        enrich_steps = [s for s in result.steps if s.step_name == StepName.ENRICHMENT]
        assert len(enrich_steps) == 1
        summary = enrich_steps[0].result_summary
        assert "provider_count" in summary
        assert "result_count" in summary
        assert "blocked_count" in summary
        assert isinstance(summary["provider_count"], int)
        assert isinstance(summary["result_count"], int)
        assert isinstance(summary["blocked_count"], int)

    def test_analysis_result_summary(self) -> None:
        """ANALYSIS step result_summary has agent_count, report_ids, bundle_id."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx()
        result = orchestrator.execute(ctx)

        analysis_steps = [s for s in result.steps if s.step_name == StepName.ANALYSIS]
        assert len(analysis_steps) == 1
        summary = analysis_steps[0].result_summary
        assert "agent_count" in summary
        assert "report_ids" in summary
        assert "bundle_id" in summary
        assert isinstance(summary["agent_count"], int)
        assert isinstance(summary["report_ids"], list)
        assert isinstance(summary["bundle_id"], str)

    def test_scoring_result_summary(self) -> None:
        """SCORING step result_summary has composite_score, band, routing."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx()
        result = orchestrator.execute(ctx)

        scoring_steps = [s for s in result.steps if s.step_name == StepName.SCORING]
        assert len(scoring_steps) == 1
        summary = scoring_steps[0].result_summary
        assert "composite_score" in summary
        assert "band" in summary
        assert "routing" in summary
        assert isinstance(summary["composite_score"], float)
        assert isinstance(summary["band"], str)
        assert isinstance(summary["routing"], str)

    def test_deliverables_result_summary(self) -> None:
        """DELIVERABLES step result_summary has deliverable_count, types, deliverable_ids."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx()
        result = orchestrator.execute(ctx)

        del_steps = [s for s in result.steps if s.step_name == StepName.DELIVERABLES]
        assert len(del_steps) == 1
        summary = del_steps[0].result_summary
        assert "deliverable_count" in summary
        assert "types" in summary
        assert "deliverable_ids" in summary
        assert isinstance(summary["deliverable_count"], int)
        assert isinstance(summary["types"], list)
        assert isinstance(summary["deliverable_ids"], list)


class TestEnrichmentStepDataFlow:
    """Verify enrichment step data flows to downstream steps."""

    def test_analysis_receives_enrichment_refs(self) -> None:
        """ANALYSIS step callable receives enrichment_refs from ENRICHMENT step."""
        received: dict[str, Any] = {}

        def enrichment_with_refs(
            *,
            run_id: str,
            tenant_id: str,
            deal_id: str,
            created_claim_ids: list[str],
            calc_ids: list[str],
        ) -> dict[str, Any]:
            return {
                "provider_count": 1,
                "result_count": 1,
                "blocked_count": 0,
                "enrichment_refs": {
                    "ref-001": {
                        "ref_id": "ref-001",
                        "provider_id": "edgar",
                        "source_id": "CIK-12345",
                    }
                },
            }

        def capturing_analysis(
            *,
            run_id: str,
            tenant_id: str,
            deal_id: str,
            created_claim_ids: list[str],
            calc_ids: list[str],
            enrichment_refs: dict[str, Any],
        ) -> dict[str, Any]:
            received["enrichment_refs"] = enrichment_refs
            return {
                "agent_count": 8,
                "report_ids": ["r1"],
                "bundle_id": "b1",
            }

        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = _make_full_ctx(
            enrich_fn=enrichment_with_refs,
            analysis_fn=capturing_analysis,
        )
        result = orchestrator.execute(ctx)

        assert result.status == "COMPLETED"
        assert "ref-001" in received["enrichment_refs"]
        ref = received["enrichment_refs"]["ref-001"]
        assert ref["provider_id"] == "edgar"
        assert ref["source_id"] == "CIK-12345"
