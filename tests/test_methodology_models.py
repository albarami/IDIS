"""Tests for unified methodology registry models."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from idis.methodology.ids import generate_methodology_question_id
from idis.methodology.models import (
    AssignedAgent,
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologySourceTrace,
    MethodologyType,
    MethodologyVersion,
    RedFlagRule,
    ReportMapping,
    RequiredCalculation,
    RequiredEvidence,
)


def _question(methodology_type: MethodologyType) -> MethodologyQuestion:
    return MethodologyQuestion(
        methodology_id=f"{methodology_type.value}_synthetic",
        methodology_version_id=f"{methodology_type.value}_synthetic:v1",
        methodology_question_id=generate_methodology_question_id(
            methodology_type=methodology_type,
            section="Quality of Revenue",
            sheet_or_section="P&L",
            row_number=2,
            line_item="Revenue",
            question_text="What is normalized recurring revenue?",
        ),
        methodology_type=methodology_type,
        section="Quality of Revenue",
        sheet_or_source_section="P&L",
        source_row_number=2,
        term="QoR",
        nature="Financial",
        line_item="Revenue",
        question_text="What is normalized recurring revenue?",
        required_evidence=[
            RequiredEvidence(
                evidence_type="trial_balance",
                description="Monthly revenue support",
                min_count=1,
            )
        ],
        target_document_categories=["financial_statement", "trial_balance"],
        required_calculations=[
            RequiredCalculation(calc_type="GROSS_MARGIN", required=True)
        ],
        assigned_agents=[
            AssignedAgent(role="financial_agent", responsibility="Validate revenue")
        ],
        red_flag_rules=[
            RedFlagRule(
                rule_id="rev_missing_support",
                description="Revenue has no source support",
                severity="HIGH",
            )
        ],
        report_mapping=ReportMapping(
            report_section="Quality of Revenue",
            report_subsection="Normalized Revenue",
        ),
        validation_requirements=["requires_claim_or_evidence"],
        source_trace=MethodologySourceTrace(
            source_type="synthetic_workbook",
            source_name="fdd_synthetic.xlsx",
            source_hash="a" * 64,
            sheet_or_section="P&L",
            row_number=2,
        ),
    )


def test_valid_financial_dd_question() -> None:
    """Financial DD questions are explicit structured data."""
    question = _question(MethodologyType.FINANCIAL_DD)

    assert question.methodology_type == MethodologyType.FINANCIAL_DD
    assert question.methodology_question_id.startswith("mq_financial_dd_")
    assert question.source_trace.sheet_or_section == "P&L"


def test_valid_commercial_dd_question() -> None:
    """Commercial DD uses the same registry shape as FDD."""
    question = _question(MethodologyType.COMMERCIAL_DD)

    assert question.methodology_type == MethodologyType.COMMERCIAL_DD
    assert question.methodology_question_id.startswith("mq_commercial_dd_")
    assert question.required_evidence[0].evidence_type == "trial_balance"


def test_registry_serialization_is_deterministic() -> None:
    """Registry JSON serialization must be stable for audit/versioning."""
    version = MethodologyVersion(
        methodology_id="financial_dd_synthetic",
        methodology_version_id="financial_dd_synthetic:v1",
        methodology_type=MethodologyType.FINANCIAL_DD,
        version_label="v1",
        source_hash="a" * 64,
        questions=[_question(MethodologyType.FINANCIAL_DD)],
    )
    registry = MethodologyRegistry(
        methodology_id="financial_dd_synthetic",
        methodology_type=MethodologyType.FINANCIAL_DD,
        versions=[version],
    )

    first = registry.to_deterministic_json()
    second = registry.to_deterministic_json()

    assert first == second
    parsed = json.loads(first)
    assert parsed["methodology_id"] == "financial_dd_synthetic"
    assert registry.registry_hash == MethodologyRegistry.model_validate(parsed).registry_hash


def test_required_evidence_validation() -> None:
    """Required evidence must be meaningful and enforceable."""
    with pytest.raises(ValidationError):
        RequiredEvidence(evidence_type="", description=" ", min_count=0)


def test_required_calculation_validation() -> None:
    """Required calculation entries must identify a calculation type."""
    with pytest.raises(ValidationError):
        RequiredCalculation(calc_type="", required=True)


def test_assigned_agent_validation() -> None:
    """Assigned agents need explicit roles and responsibilities."""
    with pytest.raises(ValidationError):
        AssignedAgent(role="", responsibility="")


def test_report_mapping_validation() -> None:
    """Questions must map to a report section."""
    with pytest.raises(ValidationError):
        ReportMapping(report_section="", report_subsection="sub")


def test_stable_methodology_question_id_format_and_determinism() -> None:
    """Stable IDs should not depend on import order or runtime randomness."""
    first = generate_methodology_question_id(
        methodology_type=MethodologyType.FINANCIAL_DD,
        section="P&L",
        sheet_or_section="P&L",
        row_number=2,
        line_item="Revenue",
        question_text="What is normalized recurring revenue?",
    )
    second = generate_methodology_question_id(
        methodology_type=MethodologyType.FINANCIAL_DD,
        section="P&L",
        sheet_or_section="P&L",
        row_number=2,
        line_item="Revenue",
        question_text="What is normalized recurring revenue?",
    )

    assert first == second
    assert first.startswith("mq_financial_dd_")
    assert len(first.split("_")[-1]) == 16


def test_methodology_version_requires_questions() -> None:
    """A methodology version without questions is invalid registry data."""
    with pytest.raises(ValidationError):
        MethodologyVersion(
            methodology_id="financial_dd_synthetic",
            methodology_version_id="financial_dd_synthetic:v1",
            methodology_type=MethodologyType.FINANCIAL_DD,
            version_label="v1",
            source_hash="a" * 64,
            questions=[],
        )


def test_registry_rejects_mismatched_version_identity() -> None:
    """Registry, version, and question identity fields must stay aligned."""
    commercial_version = MethodologyVersion(
        methodology_id="commercial_dd_synthetic",
        methodology_version_id="commercial_dd_synthetic:v1",
        methodology_type=MethodologyType.COMMERCIAL_DD,
        version_label="v1",
        source_hash="b" * 64,
        questions=[_question(MethodologyType.COMMERCIAL_DD)],
    )

    with pytest.raises(ValidationError):
        MethodologyRegistry(
            methodology_id="financial_dd_synthetic",
            methodology_type=MethodologyType.FINANCIAL_DD,
            versions=[commercial_version],
        )
