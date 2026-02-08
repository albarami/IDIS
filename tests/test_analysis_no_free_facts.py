"""Tests for analysis No-Free-Facts validator."""

from __future__ import annotations

from datetime import UTC, datetime

from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    AnalysisMuhasabahRecord,
    EnrichmentRef,
    Risk,
    RiskSeverity,
)
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator

CLAIM_1 = "00000000-0000-0000-0000-000000000001"
CLAIM_2 = "00000000-0000-0000-0000-000000000002"
CALC_1 = "00000000-0000-0000-0000-000000000010"
CALC_2 = "00000000-0000-0000-0000-000000000020"
ENRICHMENT_1 = "enrich-1"
UNKNOWN_CLAIM = "00000000-0000-0000-0000-999999999999"
UNKNOWN_CALC = "00000000-0000-0000-0000-888888888888"
UNKNOWN_ENRICHMENT = "enrich-unknown"
TIMESTAMP = datetime.now(UTC).isoformat()


def _make_context(
    *,
    claim_ids: frozenset[str] | None = None,
    calc_ids: frozenset[str] | None = None,
    enrichment_refs: dict[str, EnrichmentRef] | None = None,
) -> AnalysisContext:
    return AnalysisContext(
        deal_id="deal-1",
        tenant_id="tenant-1",
        run_id="run-1",
        claim_ids=claim_ids if claim_ids is not None else frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=calc_ids if calc_ids is not None else frozenset({CALC_1, CALC_2}),
        enrichment_refs=enrichment_refs or {},
    )


def _make_muhasabah(
    claim_ids: list[str] | None = None,
    calc_ids: list[str] | None = None,
    is_subjective: bool = False,
) -> AnalysisMuhasabahRecord:
    return AnalysisMuhasabahRecord(
        agent_id="agent-1",
        output_id="output-1",
        supported_claim_ids=claim_ids if claim_ids is not None else [CLAIM_1],
        supported_calc_ids=calc_ids if calc_ids is not None else [],
        evidence_summary="Evidence summary",
        counter_hypothesis="Counter hypothesis",
        falsifiability_tests=[],
        uncertainties=[],
        failure_modes=[],
        confidence=0.70,
        confidence_justification="Justified",
        timestamp=TIMESTAMP,
        is_subjective=is_subjective,
    )


def _make_report(
    *,
    claim_ids: list[str] | None = None,
    calc_ids: list[str] | None = None,
    enrichment_ref_ids: list[str] | None = None,
    risks: list[Risk] | None = None,
    muhasabah: AnalysisMuhasabahRecord | None = None,
) -> AgentReport:
    return AgentReport(
        agent_id="agent-1",
        agent_type="example_agent",
        supported_claim_ids=claim_ids if claim_ids is not None else [CLAIM_1],
        supported_calc_ids=calc_ids if calc_ids is not None else [CALC_1],
        analysis_sections={"summary": "Test analysis"},
        risks=risks if risks is not None else [],
        questions_for_founder=["Question?"],
        confidence=0.70,
        confidence_justification="Justified",
        muhasabah=muhasabah
        or _make_muhasabah(
            claim_ids=claim_ids if claim_ids is not None else [CLAIM_1],
            calc_ids=calc_ids if calc_ids is not None else [CALC_1],
        ),
        enrichment_ref_ids=enrichment_ref_ids or [],
    )


class TestUnknownClaimId:
    """Unknown claim_id must fail closed."""

    def test_unknown_claim_id_fails(self) -> None:
        report = _make_report(
            claim_ids=[CLAIM_1, UNKNOWN_CLAIM],
            muhasabah=_make_muhasabah(claim_ids=[CLAIM_1]),
        )
        ctx = _make_context()
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert not result.passed
        codes = {e.code for e in result.errors}
        assert "NFF_UNKNOWN_CLAIM_ID" in codes

    def test_unknown_claim_in_muhasabah_fails(self) -> None:
        report = _make_report(
            claim_ids=[CLAIM_1],
            muhasabah=_make_muhasabah(claim_ids=[CLAIM_1, UNKNOWN_CLAIM]),
        )
        ctx = _make_context()
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert not result.passed
        codes = {e.code for e in result.errors}
        assert "NFF_UNKNOWN_CLAIM_ID" in codes


class TestUnknownCalcId:
    """Unknown calc_id must fail closed."""

    def test_unknown_calc_id_fails(self) -> None:
        report = _make_report(
            calc_ids=[CALC_1, UNKNOWN_CALC],
            muhasabah=_make_muhasabah(claim_ids=[CLAIM_1], calc_ids=[CALC_1]),
        )
        ctx = _make_context()
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert not result.passed
        codes = {e.code for e in result.errors}
        assert "NFF_UNKNOWN_CALC_ID" in codes

    def test_unknown_calc_in_muhasabah_fails(self) -> None:
        report = _make_report(
            calc_ids=[CALC_1],
            muhasabah=_make_muhasabah(claim_ids=[CLAIM_1], calc_ids=[UNKNOWN_CALC]),
        )
        ctx = _make_context()
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert not result.passed
        codes = {e.code for e in result.errors}
        assert "NFF_UNKNOWN_CALC_ID" in codes


class TestEnrichmentProvenance:
    """Enrichment refs must have complete provenance."""

    def test_unknown_enrichment_ref_fails(self) -> None:
        report = _make_report(enrichment_ref_ids=[UNKNOWN_ENRICHMENT])
        ctx = _make_context()
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert not result.passed
        codes = {e.code for e in result.errors}
        assert "NFF_UNKNOWN_ENRICHMENT_REF" in codes

    def test_enrichment_missing_provider_id_fails(self) -> None:
        """Enrichment ref with empty provider_id must be rejected."""
        incomplete_ref = EnrichmentRef.model_construct(
            ref_id=ENRICHMENT_1,
            provider_id="",
            source_id="src-1",
        )
        ctx = _make_context(
            enrichment_refs={ENRICHMENT_1: incomplete_ref},
        )
        report = _make_report(enrichment_ref_ids=[ENRICHMENT_1])
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert not result.passed
        codes = {e.code for e in result.errors}
        assert "NFF_ENRICHMENT_MISSING_PROVIDER_ID" in codes

    def test_enrichment_missing_source_id_fails(self) -> None:
        """Enrichment ref with empty source_id must be rejected."""
        incomplete_ref = EnrichmentRef.model_construct(
            ref_id=ENRICHMENT_1,
            provider_id="pitchbook",
            source_id="",
        )
        ctx = _make_context(
            enrichment_refs={ENRICHMENT_1: incomplete_ref},
        )
        report = _make_report(enrichment_ref_ids=[ENRICHMENT_1])
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert not result.passed
        codes = {e.code for e in result.errors}
        assert "NFF_ENRICHMENT_MISSING_SOURCE_ID" in codes

    def test_enrichment_with_complete_provenance_passes(self) -> None:
        valid_ref = EnrichmentRef(
            ref_id=ENRICHMENT_1,
            provider_id="pitchbook",
            source_id="pb-company-12345",
        )
        ctx = _make_context(
            enrichment_refs={ENRICHMENT_1: valid_ref},
        )
        report = _make_report(enrichment_ref_ids=[ENRICHMENT_1])
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed


class TestUnknownRefsInRisks:
    """Unknown refs inside Risk objects must fail."""

    def test_unknown_claim_in_risk_fails(self) -> None:
        risk = Risk(
            risk_id="r-1",
            description="Bad risk",
            severity=RiskSeverity.HIGH,
            claim_ids=[UNKNOWN_CLAIM],
        )
        report = _make_report(risks=[risk])
        ctx = _make_context()
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert not result.passed
        codes = {e.code for e in result.errors}
        assert "NFF_UNKNOWN_CLAIM_ID" in codes

    def test_unknown_enrichment_in_risk_fails(self) -> None:
        risk = Risk(
            risk_id="r-1",
            description="Bad risk",
            severity=RiskSeverity.HIGH,
            claim_ids=[CLAIM_1],
            enrichment_ref_ids=[UNKNOWN_ENRICHMENT],
        )
        report = _make_report(risks=[risk])
        ctx = _make_context()
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert not result.passed
        codes = {e.code for e in result.errors}
        assert "NFF_UNKNOWN_ENRICHMENT_REF" in codes


class TestFullyGrounded:
    """Fully grounded report must pass."""

    def test_fully_grounded_report_passes(self) -> None:
        valid_ref = EnrichmentRef(
            ref_id=ENRICHMENT_1,
            provider_id="pitchbook",
            source_id="pb-company-12345",
        )
        ctx = _make_context(
            enrichment_refs={ENRICHMENT_1: valid_ref},
        )
        risk = Risk(
            risk_id="r-1",
            description="Concentration risk",
            severity=RiskSeverity.MEDIUM,
            claim_ids=[CLAIM_1],
            calc_ids=[CALC_1],
            enrichment_ref_ids=[ENRICHMENT_1],
        )
        report = _make_report(
            claim_ids=[CLAIM_1, CLAIM_2],
            calc_ids=[CALC_1, CALC_2],
            enrichment_ref_ids=[ENRICHMENT_1],
            risks=[risk],
            muhasabah=_make_muhasabah(
                claim_ids=[CLAIM_1, CLAIM_2],
                calc_ids=[CALC_1, CALC_2],
            ),
        )
        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed
