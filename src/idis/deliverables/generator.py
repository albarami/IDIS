"""Deliverables Generator — v6.3 Phase 10

Orchestrates generation of the full deliverables bundle:
1. ScreeningSnapshot (existing builder + bridging from AgentReport)
2. ICMemo (existing builder + bridging from AgentReport)
3. TruthDashboard (new)
4. QABrief (new)
5. DeclineLetter (new, only when routing=DECLINE)

Trust invariants:
- Preconditions: scorecard present, all 8 agent reports present
- Fail-closed on missing inputs, NFF violations, audit sink failures
- Audit events: deliverable.generation.started|completed|failed
- Audit sink failure is fatal (AuditSinkError propagated)
- No LLM writing step
- Deterministic ordering throughout
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from idis.analysis.models import AgentReport, AnalysisBundle, AnalysisContext
from idis.analysis.scoring.models import RoutingAction, Scorecard, ScoreDimension
from idis.audit.sink import AuditSink, AuditSinkError
from idis.deliverables.decline_letter import DeclineLetterBuilder
from idis.deliverables.memo import ICMemoBuilder
from idis.deliverables.qa_brief import QABriefBuilder
from idis.deliverables.screening import ScreeningSnapshotBuilder
from idis.deliverables.truth_dashboard import TruthDashboardBuilder
from idis.models.deliverables import (
    DeclineLetter,
    DeliverablesBundle,
    ICMemo,
    QABrief,
    ScreeningSnapshot,
    TruthDashboard,
)
from idis.validators.deliverable import (
    DeliverableValidationError,
    validate_deliverable_no_free_facts,
)

logger = logging.getLogger(__name__)

REQUIRED_AGENT_TYPES: frozenset[str] = frozenset(
    {
        "financial_agent",
        "historian_agent",
        "market_agent",
        "risk_officer_agent",
        "sector_specialist_agent",
        "team_agent",
        "technical_agent",
        "terms_agent",
    }
)

_AGENT_TYPE_TO_MEMO_SECTION: dict[str, str] = {
    "financial_agent": "financials",
    "market_agent": "market_analysis",
    "team_agent": "team_assessment",
    "risk_officer_agent": "risks",
    "technical_agent": "company_overview",
    "terms_agent": "recommendation",
    "historian_agent": "executive_summary",
    "sector_specialist_agent": "market_analysis",
}

_AGENT_TYPE_TO_SNAPSHOT_SECTION: dict[str, str] = {
    "financial_agent": "metrics",
    "market_agent": "summary",
    "team_agent": "summary",
    "risk_officer_agent": "red_flags",
    "technical_agent": "summary",
    "terms_agent": "metrics",
    "historian_agent": "summary",
    "sector_specialist_agent": "summary",
}

_DIMENSION_TO_TOPIC: dict[str, str] = {
    dim.value: dim.value.replace("_", " ").title() for dim in ScoreDimension
}


class DeliverablesGeneratorError(Exception):
    """Raised when the deliverables generator encounters a fatal error."""

    def __init__(self, message: str, code: str = "GENERATOR_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DeliverablesGenerator:
    """Orchestrates generation of the full deliverables bundle.

    Bridges analysis agent reports + scorecard → existing and new builders.
    Emits audit events with fatal sink behavior.
    """

    def __init__(
        self,
        *,
        audit_sink: AuditSink,
    ) -> None:
        """Initialize the generator.

        Args:
            audit_sink: Audit event sink (fail-closed on failure).
        """
        self._audit_sink = audit_sink

    def generate(
        self,
        *,
        ctx: AnalysisContext,
        bundle: AnalysisBundle,
        scorecard: Scorecard,
        deal_name: str,
        generated_at: str,
        deliverable_id_prefix: str,
    ) -> DeliverablesBundle:
        """Generate the full deliverables bundle.

        Args:
            ctx: Analysis context with registries.
            bundle: Validated agent reports from all 8 specialist agents.
            scorecard: Scored scorecard with routing decision.
            deal_name: Human-readable deal name.
            generated_at: ISO timestamp (passed in, not generated).
            deliverable_id_prefix: Prefix for deliverable IDs (e.g. "del-run001").

        Returns:
            DeliverablesBundle with all deliverables.

        Raises:
            DeliverablesGeneratorError: On missing inputs or validation failure.
            AuditSinkError: On audit sink failure (fatal).
        """
        self._emit_audit(
            "deliverable.generation.started",
            {
                "deal_id": ctx.deal_id,
                "tenant_id": ctx.tenant_id,
                "run_id": ctx.run_id,
                "routing": scorecard.routing.value if scorecard is not None else "UNKNOWN",
            },
        )

        try:
            if scorecard is None:
                raise DeliverablesGeneratorError(
                    message="Scorecard is required and must not be None",
                    code="MISSING_SCORECARD",
                )

            reports_by_type = self._validate_preconditions(bundle, scorecard)

            screening = self._build_screening_snapshot(
                ctx=ctx,
                reports_by_type=reports_by_type,
                scorecard=scorecard,
                deal_name=deal_name,
                generated_at=generated_at,
                deliverable_id=f"{deliverable_id_prefix}-screening",
            )

            memo = self._build_ic_memo(
                ctx=ctx,
                reports_by_type=reports_by_type,
                scorecard=scorecard,
                deal_name=deal_name,
                generated_at=generated_at,
                deliverable_id=f"{deliverable_id_prefix}-memo",
            )

            truth = self._build_truth_dashboard(
                ctx=ctx,
                reports_by_type=reports_by_type,
                scorecard=scorecard,
                deal_name=deal_name,
                generated_at=generated_at,
                deliverable_id=f"{deliverable_id_prefix}-truth",
            )

            qa = self._build_qa_brief(
                ctx=ctx,
                reports_by_type=reports_by_type,
                deal_name=deal_name,
                generated_at=generated_at,
                deliverable_id=f"{deliverable_id_prefix}-qa",
            )

            decline: DeclineLetter | None = None
            if scorecard.routing == RoutingAction.DECLINE:
                decline = self._build_decline_letter(
                    ctx=ctx,
                    reports_by_type=reports_by_type,
                    scorecard=scorecard,
                    deal_name=deal_name,
                    generated_at=generated_at,
                    deliverable_id=f"{deliverable_id_prefix}-decline",
                )

            self._validate_nff(screening, memo, truth, qa, decline)

            result = DeliverablesBundle(
                deal_id=ctx.deal_id,
                tenant_id=ctx.tenant_id,
                run_id=ctx.run_id,
                screening_snapshot=screening,
                ic_memo=memo,
                truth_dashboard=truth,
                qa_brief=qa,
                decline_letter=decline,
                generated_at=generated_at,
            )

            self._emit_audit(
                "deliverable.generation.completed",
                {
                    "deal_id": ctx.deal_id,
                    "tenant_id": ctx.tenant_id,
                    "run_id": ctx.run_id,
                    "routing": scorecard.routing.value,
                    "has_decline_letter": decline is not None,
                    "deliverable_count": 5 if decline else 4,
                },
            )

            return result

        except AuditSinkError:
            raise
        except DeliverablesGeneratorError as exc:
            self._emit_audit(
                "deliverable.generation.failed",
                {
                    "deal_id": ctx.deal_id,
                    "tenant_id": ctx.tenant_id,
                    "run_id": ctx.run_id,
                    "error_type": exc.code,
                    "error": exc.message,
                },
            )
            raise
        except Exception as exc:
            self._emit_audit(
                "deliverable.generation.failed",
                {
                    "deal_id": ctx.deal_id,
                    "tenant_id": ctx.tenant_id,
                    "run_id": ctx.run_id,
                    "error_type": "INTERNAL_ERROR",
                    "error": str(exc),
                },
            )
            raise DeliverablesGeneratorError(
                message=f"Unexpected error during generation: {exc}",
                code="INTERNAL_ERROR",
            ) from exc

    def _validate_preconditions(
        self,
        bundle: AnalysisBundle,
        scorecard: Scorecard,
    ) -> dict[str, AgentReport]:
        """Validate all required inputs are present. Fail-closed.

        Args:
            bundle: Analysis bundle with agent reports.
            scorecard: Scored scorecard.

        Returns:
            Dict of agent_type → AgentReport for easy lookup.

        Raises:
            DeliverablesGeneratorError: If any precondition fails.
        """
        if not scorecard.dimension_scores:
            raise DeliverablesGeneratorError(
                message="Scorecard has no dimension scores",
                code="MISSING_SCORECARD",
            )

        reports_by_type: dict[str, AgentReport] = {}
        for report in bundle.reports:
            reports_by_type[report.agent_type] = report

        present_types = frozenset(reports_by_type.keys())
        missing = REQUIRED_AGENT_TYPES - present_types
        if missing:
            missing_sorted = sorted(missing)
            raise DeliverablesGeneratorError(
                message=f"Missing required agent reports: {missing_sorted}",
                code="MISSING_AGENT_REPORTS",
            )

        return reports_by_type

    def _extract_facts_from_report(
        self,
        report: AgentReport,
    ) -> list[dict[str, Any]]:
        """Extract facts from an agent report's analysis_sections.

        Bridges AgentReport.analysis_sections → DeliverableFact-compatible dicts.
        Each section entry with text becomes a fact grounded to the report's refs.
        """
        facts: list[dict[str, Any]] = []
        sections = report.analysis_sections

        if isinstance(sections, dict):
            for key in sorted(sections.keys()):
                value = sections[key]
                if isinstance(value, str) and value.strip():
                    facts.append(
                        {
                            "text": value.strip(),
                            "claim_refs": list(report.supported_claim_ids),
                            "calc_refs": list(report.supported_calc_ids),
                        }
                    )
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item.strip():
                            facts.append(
                                {
                                    "text": item.strip(),
                                    "claim_refs": list(report.supported_claim_ids),
                                    "calc_refs": list(report.supported_calc_ids),
                                }
                            )
                        elif isinstance(item, dict) and item.get("text"):
                            facts.append(
                                {
                                    "text": item["text"],
                                    "claim_refs": item.get(
                                        "claim_refs",
                                        list(report.supported_claim_ids),
                                    ),
                                    "calc_refs": item.get(
                                        "calc_refs",
                                        list(report.supported_calc_ids),
                                    ),
                                    "sanad_grade": item.get("sanad_grade"),
                                    "confidence": item.get("confidence"),
                                }
                            )

        return facts

    def _build_screening_snapshot(
        self,
        *,
        ctx: AnalysisContext,
        reports_by_type: dict[str, AgentReport],
        scorecard: Scorecard,
        deal_name: str,
        generated_at: str,
        deliverable_id: str,
    ) -> ScreeningSnapshot:
        """Build ScreeningSnapshot by bridging agent reports to existing builder."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id=deliverable_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            deal_name=deal_name,
            generated_at=generated_at,
        )

        for agent_type in sorted(reports_by_type.keys()):
            report = reports_by_type[agent_type]
            section = _AGENT_TYPE_TO_SNAPSHOT_SECTION.get(agent_type, "summary")
            facts = self._extract_facts_from_report(report)

            for fact in facts:
                if section == "metrics":
                    builder.add_metric_fact(**fact)
                elif section == "red_flags":
                    builder.add_red_flag_fact(**fact)
                else:
                    builder.add_summary_fact(**fact)

        builder.add_missing_info(
            text=f"Composite score: {scorecard.composite_score:.1f} ({scorecard.score_band.value})",
        )

        for agent_type in sorted(reports_by_type.keys()):
            report = reports_by_type[agent_type]
            for q in report.questions_for_founder:
                builder.add_missing_info(text=q)

        return builder.build()

    def _build_ic_memo(
        self,
        *,
        ctx: AnalysisContext,
        reports_by_type: dict[str, AgentReport],
        scorecard: Scorecard,
        deal_name: str,
        generated_at: str,
        deliverable_id: str,
    ) -> ICMemo:
        """Build ICMemo by bridging agent reports to existing builder."""
        builder = ICMemoBuilder(
            deliverable_id=deliverable_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            deal_name=deal_name,
            generated_at=generated_at,
        )

        for agent_type in sorted(reports_by_type.keys()):
            report = reports_by_type[agent_type]
            section = _AGENT_TYPE_TO_MEMO_SECTION.get(agent_type, "company_overview")
            facts = self._extract_facts_from_report(report)

            for fact in facts:
                kwargs = {
                    "text": fact["text"],
                    "claim_refs": fact.get("claim_refs"),
                    "calc_refs": fact.get("calc_refs"),
                    "sanad_grade": fact.get("sanad_grade"),
                    "confidence": fact.get("confidence"),
                }
                if section == "executive_summary":
                    builder.add_executive_summary_fact(**kwargs)
                elif section == "market_analysis":
                    builder.add_market_analysis_fact(**kwargs)
                elif section == "financials":
                    builder.add_financials_fact(**kwargs)
                elif section == "team_assessment":
                    builder.add_team_assessment_fact(**kwargs)
                elif section == "risks":
                    builder.add_risks_fact(**kwargs)
                elif section == "recommendation":
                    builder.add_recommendation_fact(**kwargs)
                else:
                    builder.add_company_overview_fact(**kwargs)

        all_claim_refs: list[str] = []
        all_calc_refs: list[str] = []
        for ds in scorecard.dimension_scores.values():
            all_claim_refs.extend(ds.supported_claim_ids)
            all_calc_refs.extend(ds.supported_calc_ids)
        builder.add_truth_dashboard_fact(
            text=f"Composite score: {scorecard.composite_score:.1f} "
            f"({scorecard.score_band.value}) → {scorecard.routing.value}",
            claim_refs=sorted(set(all_claim_refs)),
            calc_refs=sorted(set(all_calc_refs)),
        )

        grade_dist: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
        builder.set_sanad_grade_distribution(grade_dist)

        return builder.build()

    def _build_truth_dashboard(
        self,
        *,
        ctx: AnalysisContext,
        reports_by_type: dict[str, AgentReport],
        scorecard: Scorecard,
        deal_name: str,
        generated_at: str,
        deliverable_id: str,
    ) -> TruthDashboard:
        """Build TruthDashboard from scorecard dimensions and agent report data."""
        builder = TruthDashboardBuilder(
            deliverable_id=deliverable_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            deal_name=deal_name,
            generated_at=generated_at,
        )

        for dim in sorted(scorecard.dimension_scores.keys(), key=lambda d: d.value):
            ds = scorecard.dimension_scores[dim]
            verdict = self._score_to_verdict(ds.score)
            builder.add_row(
                dimension=dim.value,
                assertion=ds.rationale,
                verdict=verdict,
                claim_refs=list(ds.supported_claim_ids),
                calc_refs=list(ds.supported_calc_ids),
                sanad_grade=None,
                confidence=ds.confidence,
            )

        builder.add_summary_fact(
            text=f"Truth Dashboard: {len(scorecard.dimension_scores)} dimensions evaluated, "
            f"composite {scorecard.composite_score:.1f} ({scorecard.score_band.value})",
            is_subjective=True,
        )

        return builder.build()

    def _build_qa_brief(
        self,
        *,
        ctx: AnalysisContext,
        reports_by_type: dict[str, AgentReport],
        deal_name: str,
        generated_at: str,
        deliverable_id: str,
    ) -> QABrief:
        """Build QABrief from agent reports' questions_for_founder."""
        builder = QABriefBuilder(
            deliverable_id=deliverable_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            deal_name=deal_name,
            generated_at=generated_at,
        )

        total_questions = 0
        for agent_type in sorted(reports_by_type.keys()):
            report = reports_by_type[agent_type]
            topic = agent_type.replace("_agent", "").replace("_", " ").title()

            for question in report.questions_for_founder:
                builder.add_item(
                    agent_type=agent_type,
                    topic=topic,
                    question=question,
                    claim_refs=list(report.supported_claim_ids),
                    calc_refs=list(report.supported_calc_ids),
                )
                total_questions += 1

        builder.add_summary_fact(
            text=f"QA Brief: {total_questions} questions from {len(reports_by_type)} agents",
            is_subjective=True,
        )

        return builder.build()

    def _build_decline_letter(
        self,
        *,
        ctx: AnalysisContext,
        reports_by_type: dict[str, AgentReport],
        scorecard: Scorecard,
        deal_name: str,
        generated_at: str,
        deliverable_id: str,
    ) -> DeclineLetter:
        """Build DeclineLetter from scorecard + risk agent reports."""
        builder = DeclineLetterBuilder(
            deliverable_id=deliverable_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            deal_name=deal_name,
            generated_at=generated_at,
            composite_score=scorecard.composite_score,
            score_band=scorecard.score_band.value,
        )

        for dim in sorted(scorecard.dimension_scores.keys(), key=lambda d: d.value):
            ds = scorecard.dimension_scores[dim]
            if ds.score < 0.55:
                builder.add_rationale_fact(
                    text=f"{dim.value}: {ds.rationale} (score: {ds.score:.2f})",
                    claim_refs=list(ds.supported_claim_ids),
                    calc_refs=list(ds.supported_calc_ids),
                    confidence=ds.confidence,
                )

        for agent_type in sorted(reports_by_type.keys()):
            report = reports_by_type[agent_type]
            for risk in report.risks:
                builder.add_concern_fact(
                    text=f"[{agent_type}] {risk.description} ({risk.severity.value})",
                    claim_refs=list(risk.claim_ids),
                    calc_refs=list(risk.calc_ids),
                )

        for agent_type in sorted(reports_by_type.keys()):
            report = reports_by_type[agent_type]
            for q in report.questions_for_founder:
                builder.add_missing_info(text=q)

        return builder.build()

    def _validate_nff(
        self,
        screening: ScreeningSnapshot,
        memo: ICMemo,
        truth: TruthDashboard,
        qa: QABrief,
        decline: DeclineLetter | None,
    ) -> None:
        """Validate No-Free-Facts for all deliverables. Fail-closed.

        Args:
            screening: Screening snapshot to validate.
            memo: IC memo to validate.
            truth: Truth dashboard to validate.
            qa: QA brief to validate.
            decline: Decline letter to validate (if present).

        Raises:
            DeliverablesGeneratorError: If NFF validation fails.
        """
        deliverables: list[Any] = [screening, memo, truth, qa]
        if decline is not None:
            deliverables.append(decline)

        for deliverable in deliverables:
            try:
                validate_deliverable_no_free_facts(deliverable, raise_on_failure=True)
            except DeliverableValidationError as exc:
                raise DeliverablesGeneratorError(
                    message=(
                        f"NFF validation failed for "
                        f"{getattr(deliverable, 'deliverable_type', 'unknown')}: "
                        f"{exc.message}"
                    ),
                    code="NFF_VIOLATION",
                ) from exc

        self._validate_qa_items_grounded(qa)

    @staticmethod
    def _validate_qa_items_grounded(qa: QABrief) -> None:
        """Validate that all QA items have evidence grounding.

        The general-purpose NFF validator relaxes QA items (they are questions),
        but the generator enforces stricter grounding: every QA item must trace
        back to at least one claim or calc that prompted the question.

        Args:
            qa: The QA brief to validate.

        Raises:
            DeliverablesGeneratorError: If any QA item lacks evidence refs.
        """
        items = getattr(qa, "items", []) or []
        for i, item in enumerate(items):
            item_claim_refs = getattr(item, "claim_refs", []) or []
            item_calc_refs = getattr(item, "calc_refs", []) or []
            if not item_claim_refs and not item_calc_refs:
                question = getattr(item, "question", "")
                display = question[:50] + "..." if len(question) > 50 else question
                raise DeliverablesGeneratorError(
                    message=(
                        f"QA Brief item [{i}] has no claim_refs or calc_refs — "
                        f"NFF violation at generator boundary. "
                        f"Question: '{display}'"
                    ),
                    code="NFF_VIOLATION",
                )

    @staticmethod
    def _score_to_verdict(score: float) -> str:
        """Convert a dimension score to a truth verdict.

        Args:
            score: Dimension score 0.0-1.0.

        Returns:
            Verdict string: CONFIRMED, DISPUTED, UNVERIFIED, or REFUTED.
        """
        if score >= 0.75:
            return "CONFIRMED"
        if score >= 0.55:
            return "DISPUTED"
        if score >= 0.35:
            return "UNVERIFIED"
        return "REFUTED"

    def _emit_audit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an audit event. Fail-closed on sink failure.

        Args:
            event_type: Audit event type identifier.
            data: Event payload.

        Raises:
            AuditSinkError: If the audit sink fails.
        """
        event = {
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            **data,
        }
        try:
            self._audit_sink.emit(event)
        except AuditSinkError:
            raise
        except Exception as exc:
            raise AuditSinkError(f"Audit sink failure for event '{event_type}': {exc}") from exc
