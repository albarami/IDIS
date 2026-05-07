"""Deterministic FDD/CDD document classifier."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from idis.models.document_classification import (
    CddDocumentCategory,
    ClassificationEvidence,
    DocumentClassification,
    DocumentClassificationSource,
    DocumentSupportStatus,
    FddDocumentCategory,
)
from idis.parsers.base import ParseResult
from idis.services.documents.parser_capabilities import triage_document


class DocumentDescriptor(BaseModel):
    """Synthetic-safe descriptor used by deterministic classification."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str
    deal_id: str
    document_id: str
    filename: str
    file_size_bytes: int = Field(ge=0)
    artifact_doc_type: str | None = None
    title: str | None = None
    detected_format: str | None = None
    parsed_span_texts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id", "deal_id", "document_id", "filename")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("parsed_span_texts")
    @classmethod
    def _span_texts_not_blank(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


_FDD_TARGET_AREAS: dict[FddDocumentCategory, list[str]] = {
    FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL: ["P&L", "Cash Flow", "Liabilities", "Assets"],
    FddDocumentCategory.PL_SUPPORT: ["P&L"],
    FddDocumentCategory.CASH_FLOW_SUPPORT: ["Cash Flow"],
    FddDocumentCategory.BALANCE_SHEET_SUPPORT: ["Assets", "Liabilities"],
    FddDocumentCategory.CAP_TABLE_FINANCING: ["Liabilities", "Assets"],
    FddDocumentCategory.CUSTOMER_CONTRACT: ["P&L"],
    FddDocumentCategory.LEGAL_CORPORATE: ["Liabilities"],
    FddDocumentCategory.BANK_PAYMENT: ["Cash Flow"],
    FddDocumentCategory.TAX_ACCOUNTING: ["P&L", "Liabilities"],
    FddDocumentCategory.PRODUCT_IP: ["Assets"],
    FddDocumentCategory.MARKET_RESEARCH: ["P&L"],
    FddDocumentCategory.HR_TEAM: ["P&L"],
    FddDocumentCategory.UNSUPPORTED: ["Unsupported"],
    FddDocumentCategory.UNKNOWN: ["Unknown"],
}

_CDD_TARGET_AREAS: dict[CddDocumentCategory, list[str]] = {
    CddDocumentCategory.MARKET_RESEARCH: ["Market"],
    CddDocumentCategory.CUSTOMER_EVIDENCE: ["Customers"],
    CddDocumentCategory.SALES_PIPELINE: ["GTM / Sales", "Revenue Quality"],
    CddDocumentCategory.PRICING_UNIT_ECONOMICS: ["Pricing / Unit Economics"],
    CddDocumentCategory.PRODUCT_TECHNOLOGY: ["Product / Technology"],
    CddDocumentCategory.COMPETITIVE_LANDSCAPE: ["Competition"],
    CddDocumentCategory.GTM_DISTRIBUTION: ["GTM / Sales"],
    CddDocumentCategory.MANAGEMENT_TEAM: ["Management / Team"],
    CddDocumentCategory.COMMERCIAL_CONTRACTS: ["Commercial Contracts", "Customers"],
    CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS: ["Business Plan Assumptions"],
    CddDocumentCategory.COMMERCIAL_RISKS: ["Commercial Risks"],
    CddDocumentCategory.UNSUPPORTED: ["Unsupported"],
    CddDocumentCategory.UNKNOWN: ["Unknown"],
}


def classify_document(
    descriptor: DocumentDescriptor,
    *,
    parse_result: ParseResult | None = None,
) -> DocumentClassification:
    """Classify one document using deterministic metadata/span rules only."""
    capability = triage_document(descriptor, parse_result=parse_result)
    fdd_category, cdd_category, secondary_fdd, secondary_cdd, reason_code = _classify_categories(
        descriptor
    )

    if capability.support_status in {
        DocumentSupportStatus.UNSUPPORTED,
        DocumentSupportStatus.CONVERSION_REQUIRED,
        DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY,
        DocumentSupportStatus.ENCRYPTED,
        DocumentSupportStatus.TOO_LARGE,
        DocumentSupportStatus.CORRUPTED,
    }:
        if fdd_category == FddDocumentCategory.UNKNOWN:
            fdd_category = FddDocumentCategory.UNSUPPORTED
        if cdd_category == CddDocumentCategory.UNKNOWN:
            cdd_category = CddDocumentCategory.UNSUPPORTED

    methodology_target_areas = _methodology_target_areas(fdd_category, cdd_category)
    reason_codes = _dedupe([reason_code, *capability.reason_codes])
    confidence = _confidence_for(
        reason_code=reason_code,
        capability_status=capability.support_status,
        has_span_evidence=bool(descriptor.parsed_span_texts),
    )

    evidence_source = (
        DocumentClassificationSource.SPAN
        if reason_code.startswith("span_")
        else DocumentClassificationSource.RULE
    )
    usable = capability.support_status in {
        DocumentSupportStatus.SUPPORTED,
        DocumentSupportStatus.PARTIALLY_SUPPORTED,
    }

    return DocumentClassification(
        tenant_id=descriptor.tenant_id,
        deal_id=descriptor.deal_id,
        document_id=descriptor.document_id,
        fdd_category=fdd_category,
        cdd_category=cdd_category,
        secondary_fdd_categories=secondary_fdd,
        secondary_cdd_categories=secondary_cdd,
        confidence=confidence,
        evidence=[
            ClassificationEvidence(
                source=evidence_source,
                reason_code=reason_code,
                description=f"Deterministic synthetic-safe rule matched {reason_code}",
            )
        ],
        parser_capability=capability,
        triage_status=capability.triage_status,
        support_status=capability.support_status,
        usable_for_methodology_extraction=usable,
        methodology_target_areas=methodology_target_areas,
        reason_codes=reason_codes,
    )


def _classify_categories(
    descriptor: DocumentDescriptor,
) -> tuple[
    FddDocumentCategory,
    CddDocumentCategory,
    list[FddDocumentCategory],
    list[CddDocumentCategory],
    str,
]:
    signal_text = _classification_signal_text(descriptor)

    if _has_all(signal_text, "financial", "model"):
        return (
            FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
            CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
            [FddDocumentCategory.PL_SUPPORT, FddDocumentCategory.CASH_FLOW_SUPPORT],
            [CddDocumentCategory.PRICING_UNIT_ECONOMICS],
            "filename_financial_model",
        )
    if "pl" in signal_text or "p&l" in signal_text or "profit loss" in signal_text:
        return (
            FddDocumentCategory.PL_SUPPORT,
            CddDocumentCategory.UNKNOWN,
            [],
            [],
            "span_pl_support" if descriptor.parsed_span_texts else "filename_pl_support",
        )
    if "cash flow" in signal_text or "cash-flow" in signal_text:
        return (
            FddDocumentCategory.CASH_FLOW_SUPPORT,
            CddDocumentCategory.UNKNOWN,
            [],
            [],
            "filename_cash_flow_support",
        )
    if "balance sheet" in signal_text or "asset" in signal_text:
        return (
            FddDocumentCategory.BALANCE_SHEET_SUPPORT,
            CddDocumentCategory.UNKNOWN,
            [],
            [],
            "filename_balance_sheet_support",
        )
    if "cap table" in signal_text or "capitalization" in signal_text:
        return (
            FddDocumentCategory.CAP_TABLE_FINANCING,
            CddDocumentCategory.UNKNOWN,
            [],
            [],
            "filename_cap_table_financing",
        )
    if "customer contract" in signal_text or _has_all(signal_text, "customer", "contract"):
        return (
            FddDocumentCategory.CUSTOMER_CONTRACT,
            CddDocumentCategory.COMMERCIAL_CONTRACTS,
            [],
            [CddDocumentCategory.CUSTOMER_EVIDENCE],
            "filename_customer_contract",
        )
    if "market research" in signal_text or _has_all(signal_text, "market", "research"):
        return (
            FddDocumentCategory.MARKET_RESEARCH,
            CddDocumentCategory.MARKET_RESEARCH,
            [],
            [],
            "filename_market_research",
        )
    if "product" in signal_text or "technical" in signal_text or "technology" in signal_text:
        return (
            FddDocumentCategory.PRODUCT_IP,
            CddDocumentCategory.PRODUCT_TECHNOLOGY,
            [],
            [],
            "filename_product_technology",
        )
    if "competitor" in signal_text or "competitive" in signal_text:
        return (
            FddDocumentCategory.UNKNOWN,
            CddDocumentCategory.COMPETITIVE_LANDSCAPE,
            [],
            [],
            "filename_competitive_landscape",
        )
    if "sales pipeline" in signal_text or "pipeline" in signal_text:
        return (
            FddDocumentCategory.UNKNOWN,
            CddDocumentCategory.SALES_PIPELINE,
            [],
            [],
            "filename_sales_pipeline",
        )
    return (
        FddDocumentCategory.UNKNOWN,
        CddDocumentCategory.UNKNOWN,
        [],
        [],
        "unknown_document_type",
    )


def _classification_signal_text(descriptor: DocumentDescriptor) -> str:
    parts = [
        descriptor.filename,
        descriptor.title or "",
        descriptor.artifact_doc_type or "",
        " ".join(descriptor.parsed_span_texts[:10]),
    ]
    return " ".join(parts).replace("_", " ").replace("-", " ").lower()


def _methodology_target_areas(
    fdd_category: FddDocumentCategory,
    cdd_category: CddDocumentCategory,
) -> list[str]:
    return _dedupe([*_FDD_TARGET_AREAS[fdd_category], *_CDD_TARGET_AREAS[cdd_category]])


def _confidence_for(
    *,
    reason_code: str,
    capability_status: DocumentSupportStatus,
    has_span_evidence: bool,
) -> float:
    if reason_code == "unknown_document_type":
        return 0.2
    if capability_status not in {
        DocumentSupportStatus.SUPPORTED,
        DocumentSupportStatus.PARTIALLY_SUPPORTED,
    }:
        return 0.35
    return 0.9 if has_span_evidence else 0.82


def _has_all(text: str, *terms: str) -> bool:
    return all(term in text for term in terms)


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
