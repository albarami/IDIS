"""Document classification and parser triage models."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FddDocumentCategory(StrEnum):
    """Financial due diligence document categories."""

    FINANCIAL_SCHEDULE_MODEL = "financial_schedule_model"
    PL_SUPPORT = "pl_support"
    CASH_FLOW_SUPPORT = "cash_flow_support"
    BALANCE_SHEET_SUPPORT = "balance_sheet_support"
    CAP_TABLE_FINANCING = "cap_table_financing"
    CUSTOMER_CONTRACT = "customer_contract"
    LEGAL_CORPORATE = "legal_corporate"
    BANK_PAYMENT = "bank_payment"
    TAX_ACCOUNTING = "tax_accounting"
    PRODUCT_IP = "product_ip"
    MARKET_RESEARCH = "market_research"
    HR_TEAM = "hr_team"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class CddDocumentCategory(StrEnum):
    """Commercial due diligence document categories."""

    MARKET_RESEARCH = "market_research"
    CUSTOMER_EVIDENCE = "customer_evidence"
    SALES_PIPELINE = "sales_pipeline"
    PRICING_UNIT_ECONOMICS = "pricing_unit_economics"
    PRODUCT_TECHNOLOGY = "product_technology"
    COMPETITIVE_LANDSCAPE = "competitive_landscape"
    GTM_DISTRIBUTION = "gtm_distribution"
    MANAGEMENT_TEAM = "management_team"
    COMMERCIAL_CONTRACTS = "commercial_contracts"
    BUSINESS_PLAN_ASSUMPTIONS = "business_plan_assumptions"
    COMMERCIAL_RISKS = "commercial_risks"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class DocumentSupportStatus(StrEnum):
    """Parser/support status for a source document."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    ENCRYPTED = "encrypted"
    SCANNED_OR_IMAGE_ONLY = "scanned_or_image_only"
    TOO_LARGE = "too_large"
    CORRUPTED = "corrupted"
    CONVERSION_REQUIRED = "conversion_required"
    UNKNOWN = "unknown"


class DocumentTriageStatus(StrEnum):
    """Document triage status for classification and future extraction readiness."""

    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    CONVERSION_REQUIRED = "conversion_required"
    UNSUPPORTED_SOURCE = "unsupported_source"
    OCR_REQUIRED = "ocr_required"
    TOO_LARGE = "too_large"
    UNKNOWN = "unknown"


class DocumentClassificationSource(StrEnum):
    """Evidence source for a classification decision."""

    RULE = "rule"
    SPAN = "span"
    ARTIFACT_METADATA = "artifact_metadata"
    MANUAL = "manual"
    FUTURE_LLM = "future_llm"


class ClassificationBaseModel(BaseModel):
    """Base model for classification data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ClassificationEvidence(ClassificationBaseModel):
    """Reasoned evidence supporting a document classification."""

    source: DocumentClassificationSource
    reason_code: str
    description: str

    @field_validator("reason_code", "description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class ParserCapability(ClassificationBaseModel):
    """Parser capability and triage information for a document."""

    file_type: str
    parser_name: str | None = None
    support_status: DocumentSupportStatus
    triage_status: DocumentTriageStatus
    reason_codes: list[str]
    warnings: list[str] = Field(default_factory=list)
    requires_conversion: bool = False
    requires_ocr: bool = False
    usable_without_conversion: bool = True

    @field_validator("file_type")
    @classmethod
    def _file_type_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("file_type must not be blank")
        return value.strip().upper()

    @field_validator("reason_codes", "warnings")
    @classmethod
    def _list_items_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _set_usability(self) -> ParserCapability:
        if self.requires_conversion or self.requires_ocr:
            self.usable_without_conversion = False
        return self


class DocumentClassification(ClassificationBaseModel):
    """Classification result for one tenant/deal/document scoped source."""

    tenant_id: str
    deal_id: str
    document_id: str
    classification_id: str | None = None
    fdd_category: FddDocumentCategory
    cdd_category: CddDocumentCategory
    secondary_fdd_categories: list[FddDocumentCategory] = Field(default_factory=list)
    secondary_cdd_categories: list[CddDocumentCategory] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[ClassificationEvidence]
    parser_capability: ParserCapability
    triage_status: DocumentTriageStatus
    support_status: DocumentSupportStatus
    usable_for_methodology_extraction: bool
    methodology_target_areas: list[str]
    reason_codes: list[str]

    @field_validator("tenant_id", "deal_id", "document_id")
    @classmethod
    def _required_ids_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must not be blank")
        return value.strip()

    @field_validator("evidence", "methodology_target_areas", "reason_codes")
    @classmethod
    def _non_empty_lists(cls, value: list[object]) -> list[object]:
        if not value:
            raise ValueError("list must not be empty")
        return value

    @field_validator("methodology_target_areas", "reason_codes")
    @classmethod
    def _string_list_items_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _ensure_classification_id(self) -> DocumentClassification:
        if not self.classification_id:
            seed = "|".join([self.tenant_id, self.deal_id, self.document_id])
            digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
            self.classification_id = f"dc_{digest}"
        return self

    def to_deterministic_json(self) -> str:
        """Serialize the classification deterministically."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class DocumentClassificationSummary(ClassificationBaseModel):
    """Aggregate document classification summary."""

    tenant_id: str
    deal_id: str
    total_documents: int
    by_fdd_category: dict[str, int]
    by_cdd_category: dict[str, int]
    by_support_status: dict[str, int]
    by_triage_status: dict[str, int]


class DocumentClassificationBlockerSummary(ClassificationBaseModel):
    """Aggregate blocked/unsupported document summary."""

    tenant_id: str
    deal_id: str
    total_blocked: int
    by_reason_code: dict[str, int]
