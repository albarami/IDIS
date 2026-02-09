"""Tests for Deliverables Generator — v6.3 Phase 10

Tests:
- Generator creates full bundle with all 5 deliverables when routing=DECLINE
- Generator creates bundle with 4 deliverables when routing!=DECLINE
- Fail-closed on missing scorecard
- Fail-closed on missing agent reports
- NFF violations fail closed
- Audit sink failure is fatal
- Determinism: same inputs produce same outputs
- Audit events emitted correctly
"""

from __future__ import annotations

from typing import Any

import pytest

from idis.analysis.models import (
    AgentReport,
    AnalysisBundle,
    AnalysisContext,
    AnalysisMuhasabahRecord,
    Risk,
    RiskSeverity,
)
from idis.analysis.scoring.models import (
    DimensionScore,
    RoutingAction,
    ScoreBand,
    Scorecard,
    ScoreDimension,
    Stage,
)
from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.deliverables.generator import (
    REQUIRED_AGENT_TYPES,
    DeliverablesGenerator,
    DeliverablesGeneratorError,
)
from idis.models.deliverables import DeliverablesBundle

_TIMESTAMP = "2026-02-09T10:00:00Z"

_AGENT_TYPES = sorted(REQUIRED_AGENT_TYPES)


def _make_muhasabah(agent_id: str) -> AnalysisMuhasabahRecord:
    """Create a valid muhasabah record for testing."""
    return AnalysisMuhasabahRecord(
        agent_id=agent_id,
        output_id=f"output-{agent_id}",
        supported_claim_ids=["claim-001"],
        supported_calc_ids=["calc-001"],
        evidence_summary="Test evidence",
        counter_hypothesis="Test counter",
        falsifiability_tests=[{"test": "falsify", "description": "test"}],
        uncertainties=[{"area": "test", "description": "test uncertainty"}],
        failure_modes=["mode-1"],
        confidence=0.8,
        confidence_justification="Test justification",
        timestamp=_TIMESTAMP,
    )


def _make_report(agent_type: str) -> AgentReport:
    """Create a valid agent report for testing."""
    return AgentReport(
        agent_id=f"{agent_type}-01",
        agent_type=agent_type,
        supported_claim_ids=["claim-001", "claim-002"],
        supported_calc_ids=["calc-001"],
        analysis_sections={
            "summary": f"Analysis from {agent_type}",
            "findings": [
                {"text": f"Finding 1 from {agent_type}", "claim_refs": ["claim-001"]},
                {"text": f"Finding 2 from {agent_type}", "claim_refs": ["claim-002"]},
            ],
        },
        risks=[
            Risk(
                risk_id=f"risk-{agent_type}-01",
                description=f"Risk from {agent_type}",
                severity=RiskSeverity.MEDIUM,
                claim_ids=["claim-001"],
            ),
        ],
        questions_for_founder=[f"Question from {agent_type}?"],
        confidence=0.8,
        confidence_justification="Test justification",
        muhasabah=_make_muhasabah(f"{agent_type}-01"),
    )


def _make_context() -> AnalysisContext:
    """Create a valid analysis context for testing."""
    return AnalysisContext(
        deal_id="deal-001",
        tenant_id="tenant-001",
        run_id="run-001",
        claim_ids=frozenset({"claim-001", "claim-002"}),
        calc_ids=frozenset({"calc-001"}),
        company_name="Acme Corp",
        stage="SERIES_A",
        sector="Fintech",
    )


def _make_bundle() -> AnalysisBundle:
    """Create a valid analysis bundle with all 8 agent reports."""
    reports = [_make_report(at) for at in _AGENT_TYPES]
    return AnalysisBundle(
        deal_id="deal-001",
        tenant_id="tenant-001",
        run_id="run-001",
        reports=reports,
        timestamp=_TIMESTAMP,
    )


def _make_dimension_score(dim: ScoreDimension, score: float) -> DimensionScore:
    """Create a valid dimension score for testing."""
    return DimensionScore(
        dimension=dim,
        score=score,
        rationale=f"Rationale for {dim.value}",
        supported_claim_ids=["claim-001"],
        supported_calc_ids=["calc-001"],
        confidence=0.8,
        confidence_justification="Test justification",
        muhasabah=_make_muhasabah(f"scorer-{dim.value}"),
    )


def _make_scorecard(
    routing: RoutingAction = RoutingAction.INVEST,
    composite: float = 80.0,
) -> Scorecard:
    """Create a valid scorecard for testing."""
    if routing == RoutingAction.DECLINE:
        composite = 40.0
        band = ScoreBand.LOW
        score_val = 0.4
    elif routing == RoutingAction.HOLD:
        composite = 60.0
        band = ScoreBand.MEDIUM
        score_val = 0.6
    else:
        composite = 80.0
        band = ScoreBand.HIGH
        score_val = 0.8

    dim_scores = {dim: _make_dimension_score(dim, score_val) for dim in ScoreDimension}

    return Scorecard(
        stage=Stage.SERIES_A,
        dimension_scores=dim_scores,
        composite_score=composite,
        score_band=band,
        routing=routing,
    )


class TestDeliverablesGeneratorHappyPath:
    """Tests for the happy path of DeliverablesGenerator."""

    def test_generates_bundle_with_4_deliverables_on_invest(self) -> None:
        """Test generator produces 4 deliverables when routing=INVEST."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        result = generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(routing=RoutingAction.INVEST),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        assert isinstance(result, DeliverablesBundle)
        assert result.screening_snapshot is not None
        assert result.ic_memo is not None
        assert result.truth_dashboard is not None
        assert result.qa_brief is not None
        assert result.decline_letter is None
        assert result.deal_id == "deal-001"
        assert result.tenant_id == "tenant-001"
        assert result.run_id == "run-001"

    def test_generates_bundle_with_5_deliverables_on_decline(self) -> None:
        """Test generator produces 5 deliverables when routing=DECLINE."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        result = generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(routing=RoutingAction.DECLINE),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        assert isinstance(result, DeliverablesBundle)
        assert result.decline_letter is not None
        assert result.decline_letter.deliverable_type == "DECLINE_LETTER"
        assert result.decline_letter.composite_score == 40.0
        assert result.decline_letter.score_band == "LOW"

    def test_generates_bundle_with_hold_no_decline(self) -> None:
        """Test generator produces 4 deliverables when routing=HOLD."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        result = generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(routing=RoutingAction.HOLD),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        assert result.decline_letter is None

    def test_deliverable_types_correct(self) -> None:
        """Test all deliverables have correct type literals."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        result = generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(routing=RoutingAction.DECLINE),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        assert result.screening_snapshot.deliverable_type == "SCREENING_SNAPSHOT"
        assert result.ic_memo.deliverable_type == "IC_MEMO"
        assert result.truth_dashboard.deliverable_type == "TRUTH_DASHBOARD"
        assert result.qa_brief.deliverable_type == "QA_BRIEF"
        assert result.decline_letter is not None
        assert result.decline_letter.deliverable_type == "DECLINE_LETTER"

    def test_deliverable_ids_use_prefix(self) -> None:
        """Test all deliverable IDs use the provided prefix."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        result = generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        assert result.screening_snapshot.deliverable_id.startswith("del-run001-")
        assert result.ic_memo.deliverable_id.startswith("del-run001-")
        assert result.truth_dashboard.deliverable_id.startswith("del-run001-")
        assert result.qa_brief.deliverable_id.startswith("del-run001-")


class TestDeliverablesGeneratorDeterminism:
    """Tests for determinism in deliverables generation."""

    def test_same_inputs_produce_same_outputs(self) -> None:
        """Test that same inputs produce identical outputs."""
        sink1 = InMemoryAuditSink()
        sink2 = InMemoryAuditSink()
        gen1 = DeliverablesGenerator(audit_sink=sink1)
        gen2 = DeliverablesGenerator(audit_sink=sink2)

        ctx = _make_context()
        bundle = _make_bundle()
        scorecard = _make_scorecard()

        r1 = gen1.generate(
            ctx=ctx,
            bundle=bundle,
            scorecard=scorecard,
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )
        r2 = gen2.generate(
            ctx=ctx,
            bundle=bundle,
            scorecard=scorecard,
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        assert r1.screening_snapshot == r2.screening_snapshot
        assert r1.ic_memo == r2.ic_memo
        assert r1.truth_dashboard == r2.truth_dashboard
        assert r1.qa_brief == r2.qa_brief

    def test_truth_dashboard_rows_ordered(self) -> None:
        """Test that truth dashboard rows are ordered deterministically."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        result = generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        rows = result.truth_dashboard.rows
        for i in range(len(rows) - 1):
            assert (rows[i].dimension, rows[i].assertion) <= (
                rows[i + 1].dimension,
                rows[i + 1].assertion,
            )

    def test_qa_brief_items_ordered(self) -> None:
        """Test that QA brief items are ordered deterministically."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        result = generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        items = result.qa_brief.items
        for i in range(len(items) - 1):
            assert (items[i].topic, items[i].agent_type, items[i].question) <= (
                items[i + 1].topic,
                items[i + 1].agent_type,
                items[i + 1].question,
            )


class TestDeliverablesGeneratorFailClosed:
    """Tests for fail-closed behavior."""

    def test_missing_agent_reports_raises(self) -> None:
        """Test that missing agent reports cause fail-closed error."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        partial_bundle = AnalysisBundle(
            deal_id="deal-001",
            tenant_id="tenant-001",
            run_id="run-001",
            reports=[_make_report("financial_agent"), _make_report("market_agent")],
            timestamp=_TIMESTAMP,
        )

        with pytest.raises(DeliverablesGeneratorError) as exc_info:
            generator.generate(
                ctx=_make_context(),
                bundle=partial_bundle,
                scorecard=_make_scorecard(),
                deal_name="Acme Corp",
                generated_at=_TIMESTAMP,
                deliverable_id_prefix="del-run001",
            )

        assert exc_info.value.code == "MISSING_AGENT_REPORTS"
        assert "Missing required agent reports" in exc_info.value.message

    def test_empty_bundle_raises(self) -> None:
        """Test that empty bundle causes fail-closed error."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        empty_bundle = AnalysisBundle(
            deal_id="deal-001",
            tenant_id="tenant-001",
            run_id="run-001",
            reports=[],
            timestamp=_TIMESTAMP,
        )

        with pytest.raises(DeliverablesGeneratorError) as exc_info:
            generator.generate(
                ctx=_make_context(),
                bundle=empty_bundle,
                scorecard=_make_scorecard(),
                deal_name="Acme Corp",
                generated_at=_TIMESTAMP,
                deliverable_id_prefix="del-run001",
            )

        assert exc_info.value.code == "MISSING_AGENT_REPORTS"

    def test_requires_all_8_agent_types(self) -> None:
        """Test that exactly 8 agent types are required."""
        assert len(REQUIRED_AGENT_TYPES) == 8


class TestDeliverablesGeneratorAudit:
    """Tests for audit event emission."""

    def test_audit_started_and_completed_emitted(self) -> None:
        """Test that started and completed audit events are emitted."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        event_types = [e["event_type"] for e in sink.events]
        assert "deliverable.generation.started" in event_types
        assert "deliverable.generation.completed" in event_types

    def test_audit_failed_emitted_on_error(self) -> None:
        """Test that failed audit event is emitted on generator error."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        partial_bundle = AnalysisBundle(
            deal_id="deal-001",
            tenant_id="tenant-001",
            run_id="run-001",
            reports=[_make_report("financial_agent")],
            timestamp=_TIMESTAMP,
        )

        with pytest.raises(DeliverablesGeneratorError):
            generator.generate(
                ctx=_make_context(),
                bundle=partial_bundle,
                scorecard=_make_scorecard(),
                deal_name="Acme Corp",
                generated_at=_TIMESTAMP,
                deliverable_id_prefix="del-run001",
            )

        event_types = [e["event_type"] for e in sink.events]
        assert "deliverable.generation.started" in event_types
        assert "deliverable.generation.failed" in event_types

    def test_audit_sink_failure_is_fatal(self) -> None:
        """Test that audit sink failure raises AuditSinkError (fatal)."""

        class FailingSink:
            def emit(self, event: dict[str, Any]) -> None:
                raise AuditSinkError("Sink down")

        generator = DeliverablesGenerator(audit_sink=FailingSink())  # type: ignore[arg-type]

        with pytest.raises(AuditSinkError):
            generator.generate(
                ctx=_make_context(),
                bundle=_make_bundle(),
                scorecard=_make_scorecard(),
                deal_name="Acme Corp",
                generated_at=_TIMESTAMP,
                deliverable_id_prefix="del-run001",
            )

    def test_audit_events_contain_deal_and_tenant(self) -> None:
        """Test that all audit events contain deal_id and tenant_id."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        for event in sink.events:
            assert event["deal_id"] == "deal-001"
            assert event["tenant_id"] == "tenant-001"

    def test_completed_event_has_deliverable_count(self) -> None:
        """Test that completed event includes deliverable count."""
        sink = InMemoryAuditSink()
        generator = DeliverablesGenerator(audit_sink=sink)

        generator.generate(
            ctx=_make_context(),
            bundle=_make_bundle(),
            scorecard=_make_scorecard(routing=RoutingAction.INVEST),
            deal_name="Acme Corp",
            generated_at=_TIMESTAMP,
            deliverable_id_prefix="del-run001",
        )

        completed = [
            e for e in sink.events if e["event_type"] == "deliverable.generation.completed"
        ]
        assert len(completed) == 1
        assert completed[0]["deliverable_count"] == 4
        assert completed[0]["has_decline_letter"] is False
