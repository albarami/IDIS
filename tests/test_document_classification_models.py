"""Tests for Phase 2.3 document classification models."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from idis.models.document_classification import (
    CddDocumentCategory,
    ClassificationEvidence,
    DocumentClassification,
    DocumentClassificationSource,
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
    ParserCapability,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
DOCUMENT_ID = "33333333-3333-3333-3333-333333333333"


def _capability() -> ParserCapability:
    return ParserCapability(
        file_type="XLSX",
        parser_name="xlsx",
        support_status=DocumentSupportStatus.PARTIALLY_SUPPORTED,
        triage_status=DocumentTriageStatus.PARTIAL,
        reason_codes=["xlsx_partial_table_semantics"],
    )


def _classification() -> DocumentClassification:
    return DocumentClassification(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        document_id=DOCUMENT_ID,
        fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
        secondary_fdd_categories=[FddDocumentCategory.PL_SUPPORT],
        secondary_cdd_categories=[CddDocumentCategory.PRICING_UNIT_ECONOMICS],
        confidence=0.91,
        evidence=[
            ClassificationEvidence(
                source=DocumentClassificationSource.RULE,
                reason_code="filename_financial_model",
                description="Synthetic filename indicates financial model",
            )
        ],
        parser_capability=_capability(),
        triage_status=DocumentTriageStatus.PARTIAL,
        support_status=DocumentSupportStatus.PARTIALLY_SUPPORTED,
        usable_for_methodology_extraction=True,
        methodology_target_areas=["P&L", "Cash Flow", "Business Plan Assumptions"],
        reason_codes=["filename_financial_model", "xlsx_partial_table_semantics"],
    )


def test_valid_classification_model() -> None:
    """A valid classification carries category, parser, triage, and evidence metadata."""
    classification = _classification()

    assert classification.fdd_category == FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL
    assert classification.cdd_category == CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS
    assert classification.classification_id.startswith("dc_")
    assert classification.usable_for_methodology_extraction is True


def test_valid_fdd_and_cdd_categories() -> None:
    """Required FDD/CDD document categories are first-class enums."""
    assert FddDocumentCategory.PL_SUPPORT.value == "pl_support"
    assert FddDocumentCategory.CASH_FLOW_SUPPORT.value == "cash_flow_support"
    assert CddDocumentCategory.MARKET_RESEARCH.value == "market_research"
    assert CddDocumentCategory.COMMERCIAL_RISKS.value == "commercial_risks"


def test_invalid_category_fails() -> None:
    """Invalid category strings fail schema validation."""
    payload = _classification().model_dump(mode="json")
    payload["fdd_category"] = "not_a_category"

    with pytest.raises(ValidationError):
        DocumentClassification.model_validate(payload)


def test_confidence_range_is_enforced() -> None:
    """Classification confidence is bounded between 0 and 1."""
    payload = _classification().model_dump(mode="json")
    payload["confidence"] = 1.01

    with pytest.raises(ValidationError):
        DocumentClassification.model_validate(payload)


def test_reason_codes_cannot_be_blank() -> None:
    """Reason codes must be machine-readable and nonblank."""
    with pytest.raises(ValidationError):
        ClassificationEvidence(
            source=DocumentClassificationSource.RULE,
            reason_code="   ",
            description="blank reason",
        )

    payload = _classification().model_dump(mode="json")
    payload["reason_codes"] = ["filename_financial_model", "   "]
    with pytest.raises(ValidationError):
        DocumentClassification.model_validate(payload)


def test_tenant_deal_document_ids_are_required() -> None:
    """Classification records are tenant/deal/document scoped."""
    payload = _classification().model_dump(mode="json")
    payload["tenant_id"] = ""

    with pytest.raises(ValidationError):
        DocumentClassification.model_validate(payload)


def test_deterministic_serialization() -> None:
    """Classification serialization is stable for audit/replay."""
    first = _classification().to_deterministic_json()
    second = _classification().to_deterministic_json()

    assert first == second
    assert json.loads(first)["classification_id"].startswith("dc_")
