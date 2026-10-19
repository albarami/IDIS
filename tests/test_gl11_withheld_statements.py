"""Emitter tests for analysis statements that carry no statement-level refs.

A ref-less statement stays out of factual sections. The screening snapshot records
the omission through missing information, and the IC memo records it as a
non-factual fact. A report with no such statements records nothing.
"""

from __future__ import annotations

from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    AnalysisMuhasabahRecord,
)
from idis.analysis.scoring.models import (
    DimensionScore,
    RoutingAction,
    ScoreBand,
    Scorecard,
    ScoreDimension,
    Stage,
)
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.models.deliverables import DeliverableFact, DeliverableSection, ICMemo, ScreeningSnapshot

_TIMESTAMP = "2026-10-19T18:00:00Z"
_STATEMENT = "The company expanded into three new cities last year."
_CITED_STATEMENT = "Revenue reached the cited annual figure."
_WITHHELD_ONE = "1 analysis statements withheld: no statement-level evidence references"
_WITHHELD_MARKER = "analysis statements withheld"

_SNAPSHOT_FACTUAL_SECTIONS = (
    "summary_section",
    "key_metrics_section",
    "red_flags_section",
)
_MEMO_FACTUAL_SECTIONS = (
    "executive_summary",
    "company_overview",
    "market_analysis",
    "financials",
    "team_assessment",
    "risks_and_mitigations",
    "recommendation",
    "truth_dashboard_summary",
    "scenario_analysis",
)


def _muhasabah() -> AnalysisMuhasabahRecord:
    """Build a subjective self-accounting record with no supporting ids."""
    return AnalysisMuhasabahRecord(
        agent_id="financial-agent-01",
        output_id="output-financial-agent-01",
        supported_claim_ids=[],
        supported_calc_ids=[],
        evidence_summary="No statement-level evidence was selected",
        counter_hypothesis="The narrative may not be supported by the corpus",
        falsifiability_tests=[{"test": "request_source", "description": "request source"}],
        uncertainties=[{"area": "missing_evidence", "description": "missing evidence"}],
        failure_modes=["missing_evidence"],
        confidence=0.3,
        confidence_justification="No statement-level evidence was selected",
        timestamp=_TIMESTAMP,
        is_subjective=True,
    )


def _report(*, sections: dict[str, object]) -> AgentReport:
    """Build one financial report whose supported id lists are empty."""
    return AgentReport(
        agent_id="financial-agent-01",
        agent_type="financial_agent",
        supported_claim_ids=[],
        supported_calc_ids=[],
        analysis_sections=sections,
        risks=[],
        questions_for_founder=[],
        confidence=0.3,
        confidence_justification="No statement-level evidence was selected",
        muhasabah=_muhasabah(),
    )


def _context() -> AnalysisContext:
    """Build the analysis context the builders require."""
    return AnalysisContext(
        deal_id="deal-001",
        tenant_id="tenant-001",
        run_id="run-001",
        claim_ids=frozenset(),
        calc_ids=frozenset(),
    )


def _scorecard() -> Scorecard:
    """Build a scorecard with one claim id on each dimension."""
    dimensions = {
        dimension: DimensionScore(
            dimension=dimension,
            score=0.8,
            rationale=f"Rationale for {dimension.value}",
            supported_claim_ids=["claim-001"],
            supported_calc_ids=["calc-001"],
            confidence=0.8,
            confidence_justification="Score uses the dimension claim",
            muhasabah=_muhasabah(),
        )
        for dimension in ScoreDimension
    }
    return Scorecard(
        stage=Stage.SERIES_A,
        dimension_scores=dimensions,
        composite_score=80.0,
        score_band=ScoreBand.HIGH,
        routing=RoutingAction.INVEST,
    )


def _generator() -> DeliverablesGenerator:
    """Build a generator whose sink is unused by the two builder methods."""
    return DeliverablesGenerator(audit_sink=InMemoryAuditSink())


def _section_facts(section: DeliverableSection | None) -> list[DeliverableFact]:
    """Return the facts on a section, or an empty list when the section is absent."""
    if section is None:
        return []
    return list(section.facts)


def _named_section_facts(
    deliverable: ScreeningSnapshot | ICMemo,
    names: tuple[str, ...],
) -> list[DeliverableFact]:
    """Return facts from the named sections of a snapshot or memo."""
    facts: list[DeliverableFact] = []
    for name in names:
        facts.extend(_section_facts(getattr(deliverable, name)))
    return facts


def _all_memo_facts(memo: ICMemo) -> list[DeliverableFact]:
    """Return every fact on the memo, including additional sections."""
    facts = _named_section_facts(memo, _MEMO_FACTUAL_SECTIONS)
    for section in memo.additional_sections:
        facts.extend(section.facts)
    return facts


def _snapshot_for(sections: dict[str, object]) -> ScreeningSnapshot:
    """Build a screening snapshot from one financial report."""
    return _generator()._build_screening_snapshot(
        ctx=_context(),
        reports_by_type={"financial_agent": _report(sections=sections)},
        scorecard=_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id="del-withheld-snapshot",
    )


def _memo_for(sections: dict[str, object]) -> ICMemo:
    """Build an IC memo from one financial report."""
    return _generator()._build_ic_memo(
        ctx=_context(),
        reports_by_type={"financial_agent": _report(sections=sections)},
        scorecard=_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id="del-withheld-memo",
    )


def test_snapshot_records_ref_less_statement_as_withheld_count() -> None:
    """A string statement with no refs is absent from factual sections and counted."""
    snapshot = _snapshot_for({"narrative": _STATEMENT})
    factual_texts = [
        fact.text for fact in _named_section_facts(snapshot, _SNAPSHOT_FACTUAL_SECTIONS)
    ]
    missing_texts = [fact.text for fact in snapshot.missing_info_section.facts]

    assert _STATEMENT not in factual_texts
    assert _WITHHELD_ONE in missing_texts
    withheld = next(
        fact for fact in snapshot.missing_info_section.facts if fact.text == _WITHHELD_ONE
    )
    assert withheld.is_factual is False


def test_memo_records_ref_less_statement_as_nonfactual_fact() -> None:
    """A string statement with no refs is absent from factual sections and counted."""
    memo = _memo_for({"narrative": _STATEMENT})
    factual_texts = [fact.text for fact in _named_section_facts(memo, _MEMO_FACTUAL_SECTIONS)]
    withheld = [fact for fact in _all_memo_facts(memo) if fact.text == _WITHHELD_ONE]

    assert _STATEMENT not in factual_texts
    assert len(withheld) == 1
    assert withheld[0].is_factual is False


def test_referenced_statement_records_no_withheld_entry() -> None:
    """A statement that carries its own claim ref produces no withheld record."""
    sections = {
        "findings": [
            {
                "text": _CITED_STATEMENT,
                "claim_refs": ["claim-001"],
                "calc_refs": ["calc-001"],
            }
        ]
    }
    snapshot = _snapshot_for(sections)
    memo = _memo_for(sections)

    snapshot_factual = [
        fact.text for fact in _named_section_facts(snapshot, _SNAPSHOT_FACTUAL_SECTIONS)
    ]
    memo_texts = [fact.text for fact in _all_memo_facts(memo)]
    missing_texts = [fact.text for fact in snapshot.missing_info_section.facts]

    assert _CITED_STATEMENT in snapshot_factual
    assert _CITED_STATEMENT in memo_texts
    assert all(_WITHHELD_MARKER not in text for text in missing_texts)
    assert all(_WITHHELD_MARKER not in text for text in memo_texts)
