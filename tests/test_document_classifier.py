"""Tests for deterministic FDD/CDD document classifier."""

from __future__ import annotations

import hashlib

from idis.models.document_classification import (
    CddDocumentCategory,
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
)
from idis.services.documents.classifier import DocumentDescriptor, classify_document

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"


def _descriptor(filename: str, spans: list[str] | None = None) -> DocumentDescriptor:
    stable_id = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:16]
    return DocumentDescriptor(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        document_id=f"doc-{stable_id}",
        filename=filename,
        file_size_bytes=1024,
        parsed_span_texts=spans or [],
    )


def test_financial_model_xlsx_classifies_as_financial_schedule_model() -> None:
    result = classify_document(_descriptor("synthetic_financial_model.xlsx"))

    assert result.fdd_category == FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL
    assert result.cdd_category == CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS
    assert "P&L" in result.methodology_target_areas
    assert result.usable_for_methodology_extraction is True


def test_pl_schedule_classifies_as_pl_support() -> None:
    result = classify_document(_descriptor("synthetic_pl_schedule.xlsx", ["revenue gross margin"]))

    assert result.fdd_category == FddDocumentCategory.PL_SUPPORT
    assert "P&L" in result.methodology_target_areas


def test_cash_flow_schedule_classifies_as_cash_flow_support() -> None:
    result = classify_document(_descriptor("synthetic_cash_flow_schedule.xlsx"))

    assert result.fdd_category == FddDocumentCategory.CASH_FLOW_SUPPORT
    assert "Cash Flow" in result.methodology_target_areas


def test_balance_sheet_schedule_classifies_as_balance_sheet_support() -> None:
    result = classify_document(_descriptor("synthetic_balance_sheet_schedule.xlsx"))

    assert result.fdd_category == FddDocumentCategory.BALANCE_SHEET_SUPPORT
    assert "Assets" in result.methodology_target_areas


def test_cap_table_classifies_as_cap_table_financing() -> None:
    result = classify_document(_descriptor("synthetic_cap_table.xlsx"))

    assert result.fdd_category == FddDocumentCategory.CAP_TABLE_FINANCING


def test_customer_contract_classifies_as_customer_evidence_and_contracts() -> None:
    result = classify_document(_descriptor("synthetic_customer_contract.docx"))

    assert result.fdd_category == FddDocumentCategory.CUSTOMER_CONTRACT
    assert result.cdd_category == CddDocumentCategory.COMMERCIAL_CONTRACTS
    assert CddDocumentCategory.CUSTOMER_EVIDENCE in result.secondary_cdd_categories


def test_market_research_deck_classifies_as_market_research() -> None:
    result = classify_document(_descriptor("synthetic_market_research_deck.pptx"))

    assert result.cdd_category == CddDocumentCategory.MARKET_RESEARCH
    assert "Market" in result.methodology_target_areas


def test_product_technical_doc_classifies_as_product_technology() -> None:
    result = classify_document(_descriptor("synthetic_product_technical_overview.docx"))

    assert result.cdd_category == CddDocumentCategory.PRODUCT_TECHNOLOGY


def test_competitor_analysis_classifies_as_competitive_landscape() -> None:
    result = classify_document(_descriptor("synthetic_competitor_analysis.pdf"))

    assert result.cdd_category == CddDocumentCategory.COMPETITIVE_LANDSCAPE


def test_sales_pipeline_sheet_classifies_as_sales_pipeline() -> None:
    result = classify_document(_descriptor("synthetic_sales_pipeline.xlsx"))

    assert result.cdd_category == CddDocumentCategory.SALES_PIPELINE


def test_unknown_file_has_low_confidence_unknown_categories() -> None:
    result = classify_document(_descriptor("synthetic_misc.bin"))

    assert result.fdd_category == FddDocumentCategory.UNKNOWN
    assert result.cdd_category == CddDocumentCategory.UNKNOWN
    assert result.confidence < 0.5


def test_unsupported_file_sets_unsupported_source_triage() -> None:
    result = classify_document(_descriptor("synthetic_video.mp4"))

    assert result.support_status == DocumentSupportStatus.CONVERSION_REQUIRED
    assert result.triage_status == DocumentTriageStatus.CONVERSION_REQUIRED
    assert result.usable_for_methodology_extraction is False
