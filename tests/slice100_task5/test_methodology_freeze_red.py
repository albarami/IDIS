"""RED model-boundary contracts for immutable methodology versions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idis.methodology.models import (
    AssignedAgent,
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologySourceTrace,
    MethodologyType,
    MethodologyVersion,
    ReportMapping,
    RequiredEvidence,
)


def _question(
    *,
    methodology_type: MethodologyType = MethodologyType.FINANCIAL_DD,
    source_hash: str = "a" * 64,
    source_row_number: int | None = 4,
) -> MethodologyQuestion:
    methodology_id = methodology_type.value
    version_id = f"{methodology_id}:v1"
    return MethodologyQuestion(
        methodology_id=methodology_id,
        methodology_version_id=version_id,
        methodology_question_id=f"mq_{methodology_id}_fictional_0001",
        methodology_type=methodology_type,
        section="Fictional section",
        sheet_or_source_section="P&L",
        source_row_number=source_row_number,
        term="fictional term",
        nature="fictional nature",
        line_item="fictional line",
        question_text="Review fictional evidence.",
        required_evidence=[
            RequiredEvidence(evidence_type="fictional_support", description="Fictional support")
        ],
        target_document_categories=["financial_statement"],
        assigned_agents=[
            AssignedAgent(role="financial_agent", responsibility="Review fictional evidence")
        ],
        report_mapping=ReportMapping(report_section="Fictional section"),
        validation_requirements=["requires_claim_or_evidence"],
        source_trace=MethodologySourceTrace(
            source_type="operator_workbook",
            source_name="configured-fdd-workbook",
            source_hash=source_hash,
            sheet_or_section="P&L",
            row_number=source_row_number,
        ),
    )


def _version(*, source_hash: str = "a" * 64) -> MethodologyVersion:
    return MethodologyVersion(
        methodology_id="financial_dd",
        methodology_version_id="financial_dd:v1",
        methodology_type=MethodologyType.FINANCIAL_DD,
        version_label="operator-workbook-v1",
        source_hash=source_hash,
        source_name="configured-fdd-workbook",
        questions=[_question(source_hash=source_hash)],
    )


def test_methodology_version_model_is_deeply_immutable() -> None:
    version = _version()

    with pytest.raises((AttributeError, TypeError, ValidationError)):
        version.version_label = "tampered"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError, ValidationError)):
        version.questions.append(_question())
    question = version.questions[0]
    for collection in (
        question.required_evidence,
        question.target_document_categories,
        question.required_calculations,
        question.assigned_agents,
        question.red_flag_rules,
        question.validation_requirements,
    ):
        assert isinstance(collection, tuple)
        with pytest.raises(AttributeError):
            collection.append(object())  # type: ignore[attr-defined]


def test_registry_version_collection_is_immutable() -> None:
    registry = MethodologyRegistry(
        methodology_id="financial_dd",
        methodology_type=MethodologyType.FINANCIAL_DD,
        versions=[_version()],
    )

    with pytest.raises((AttributeError, TypeError, ValidationError)):
        registry.versions.clear()


def test_version_rejects_question_source_hash_drift() -> None:
    with pytest.raises(ValidationError, match="source_hash"):
        MethodologyVersion(
            methodology_id="financial_dd",
            methodology_version_id="financial_dd:v1",
            methodology_type=MethodologyType.FINANCIAL_DD,
            version_label="operator-workbook-v1",
            source_hash="a" * 64,
            source_name="configured-fdd-workbook",
            questions=[_question(source_hash="b" * 64)],
        )


def test_financial_question_requires_sheet_and_excel_row_trace() -> None:
    with pytest.raises(ValidationError, match="row"):
        _question(source_row_number=None)


@pytest.mark.parametrize("bad_hash", ["short", "A" * 64, "g" * 64])
def test_methodology_source_hash_is_lowercase_sha256(bad_hash: str) -> None:
    with pytest.raises(ValidationError, match="source_hash"):
        _version(source_hash=bad_hash)
