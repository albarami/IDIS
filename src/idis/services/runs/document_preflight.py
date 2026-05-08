"""Run-scoped document classification and parser triage preflight."""

from __future__ import annotations

from typing import Any

from idis.models.document_classification import (
    DocumentClassification,
    DocumentSupportStatus,
    DocumentTriageStatus,
)
from idis.models.document_preflight import (
    DocumentPreflightReason,
    DocumentPreflightResult,
    DocumentPreflightSpanReference,
    DocumentPreflightStatus,
    RunDocumentPreflightDecision,
)
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult
from idis.services.documents.classification_service import InMemoryDocumentClassificationService
from idis.services.documents.classifier import DocumentDescriptor


class InMemoryRunDocumentPreflightService:
    """Classify and triage a persisted run corpus without external calls."""

    def __init__(
        self,
        classification_service: InMemoryDocumentClassificationService | None = None,
    ) -> None:
        """Initialize the preflight service."""
        self._classification_service = (
            classification_service or InMemoryDocumentClassificationService()
        )

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        corpus: list[dict[str, Any]],
    ) -> tuple[DocumentPreflightResult, list[dict[str, Any]]]:
        """Run deterministic document preflight over a full persisted corpus."""
        decisions: list[RunDocumentPreflightDecision] = []
        eligible_documents: list[dict[str, Any]] = []

        for index, raw_document in enumerate(corpus, start=1):
            document = dict(raw_document)
            if not document.get("document_id"):
                document["document_id"] = str(document.get("doc_id") or f"document-{index}")
            self._validate_document_scope(document, tenant_id=tenant_id, deal_id=deal_id)
            spans = list(document.get("spans") or [])
            self._validate_spans_scope(
                spans,
                tenant_id=tenant_id,
                deal_id=deal_id,
                document_id=str(document.get("document_id", "")),
            )

            descriptor = _descriptor_for_document(
                document,
                tenant_id=tenant_id,
                deal_id=deal_id,
            )
            parse_result = _parse_result_from_metadata(document)
            classification = self._classification_service.classify_one(
                descriptor,
                parse_result=parse_result,
            )
            source_spans = [_span_reference(span, descriptor.document_id) for span in spans]
            usable = _is_eligible(classification, source_spans)
            reason = _reason_for(classification, has_source_spans=bool(source_spans))

            decisions.append(
                RunDocumentPreflightDecision(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    document_id=descriptor.document_id,
                    classification_id=classification.classification_id or "",
                    support_status=classification.support_status,
                    triage_status=classification.triage_status,
                    usable_for_methodology_extraction=usable,
                    reason=reason,
                    reason_codes=classification.reason_codes,
                    warning_codes=classification.parser_capability.warnings,
                    source_spans=source_spans,
                    fdd_category=classification.fdd_category,
                    cdd_category=classification.cdd_category,
                    methodology_target_areas=classification.methodology_target_areas,
                )
            )
            if usable:
                eligible_documents.append(document)

        status = _status_for(decisions)
        result = DocumentPreflightResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=status,
            decisions=decisions,
        )
        return result, eligible_documents

    def _validate_document_scope(
        self,
        document: dict[str, Any],
        *,
        tenant_id: str,
        deal_id: str,
    ) -> None:
        doc_tenant_id = document.get("tenant_id")
        doc_deal_id = document.get("deal_id")
        if doc_tenant_id is not None and str(doc_tenant_id) != tenant_id:
            raise ValueError("preflight document tenant scope mismatch")
        if doc_deal_id is not None and str(doc_deal_id) != deal_id:
            raise ValueError("preflight document deal scope mismatch")

    def _validate_spans_scope(
        self,
        spans: list[dict[str, Any]],
        *,
        tenant_id: str,
        deal_id: str,
        document_id: str,
    ) -> None:
        for span in spans:
            span_tenant_id = span.get("tenant_id")
            span_deal_id = span.get("deal_id")
            span_document_id = span.get("document_id")
            if span_tenant_id is not None and str(span_tenant_id) != tenant_id:
                raise ValueError("preflight span tenant scope mismatch")
            if span_deal_id is not None and str(span_deal_id) != deal_id:
                raise ValueError("preflight span deal scope mismatch")
            if span_document_id is not None and str(span_document_id) != document_id:
                raise ValueError("preflight span document scope mismatch")


def _descriptor_for_document(
    document: dict[str, Any],
    *,
    tenant_id: str,
    deal_id: str,
) -> DocumentDescriptor:
    metadata = dict(document.get("metadata") or {})
    spans = list(document.get("spans") or [])
    filename = (
        str(document.get("document_name") or "")
        or str(metadata.get("name") or "")
        or str(document.get("uri") or "")
        or str(document.get("document_id"))
    )
    return DocumentDescriptor(
        tenant_id=tenant_id,
        deal_id=deal_id,
        document_id=str(document["document_id"]),
        filename=filename,
        file_size_bytes=int(metadata.get("size_bytes") or metadata.get("file_size_bytes") or 0),
        artifact_doc_type=str(document.get("doc_type") or ""),
        title=str(document.get("document_name") or ""),
        detected_format=str(
            metadata.get("detected_format")
            or metadata.get("parser_doc_type")
            or document.get("doc_type")
            or ""
        ),
        parsed_span_texts=[
            str(span.get("text_excerpt") or "")
            for span in spans
            if str(span.get("text_excerpt") or "").strip()
        ],
        metadata=metadata,
    )


def _parse_result_from_metadata(document: dict[str, Any]) -> ParseResult | None:
    metadata = dict(document.get("metadata") or {})
    error_codes = [str(code) for code in metadata.get("parse_error_codes", [])]
    warning_codes = [str(code) for code in metadata.get("parse_warning_codes", [])]
    parser_doc_type = str(
        metadata.get("parser_doc_type")
        or metadata.get("detected_format")
        or document.get("doc_type")
        or "UNKNOWN"
    ).upper()
    if parser_doc_type not in {"PDF", "XLSX", "DOCX", "PPTX", "UNKNOWN"}:
        parser_doc_type = "UNKNOWN"

    if not error_codes and not warning_codes:
        return None

    errors = []
    for code in error_codes:
        try:
            parse_code = ParseErrorCode(code)
        except ValueError:
            parse_code = ParseErrorCode.INTERNAL_ERROR
        errors.append(ParseError(code=parse_code, message=code))

    return ParseResult(
        doc_type=parser_doc_type,  # type: ignore[arg-type]
        success=not errors,
        errors=errors,
        warnings=warning_codes,
    )


def _span_reference(span: dict[str, Any], document_id: str) -> DocumentPreflightSpanReference:
    locator: dict[str, Any] = span["locator"] if isinstance(span.get("locator"), dict) else {}
    return DocumentPreflightSpanReference(
        span_id=str(span["span_id"]),
        document_id=str(span.get("document_id") or document_id),
        locator=locator,
        span_type=str(span["span_type"]),
        content_hash=str(span["content_hash"]) if span.get("content_hash") else None,
    )


def _is_eligible(
    classification: DocumentClassification,
    source_spans: list[DocumentPreflightSpanReference],
) -> bool:
    return (
        classification.usable_for_methodology_extraction
        and classification.support_status
        in {DocumentSupportStatus.SUPPORTED, DocumentSupportStatus.PARTIALLY_SUPPORTED}
        and classification.triage_status
        in {DocumentTriageStatus.READY, DocumentTriageStatus.PARTIAL}
        and bool(source_spans)
    )


def _reason_for(
    classification: DocumentClassification,
    *,
    has_source_spans: bool,
) -> DocumentPreflightReason:
    if classification.support_status == DocumentSupportStatus.ENCRYPTED:
        return DocumentPreflightReason.ENCRYPTED_SOURCE
    if classification.support_status == DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY:
        return DocumentPreflightReason.OCR_REQUIRED
    if classification.support_status == DocumentSupportStatus.TOO_LARGE:
        return DocumentPreflightReason.TOO_LARGE
    if classification.support_status == DocumentSupportStatus.CORRUPTED:
        return DocumentPreflightReason.CORRUPTED_SOURCE
    if classification.support_status == DocumentSupportStatus.UNSUPPORTED:
        return DocumentPreflightReason.UNSUPPORTED_SOURCE
    if classification.support_status == DocumentSupportStatus.CONVERSION_REQUIRED:
        return DocumentPreflightReason.CONVERSION_REQUIRED
    if classification.support_status == DocumentSupportStatus.UNKNOWN:
        return DocumentPreflightReason.UNKNOWN_PARSER_STATUS
    if not has_source_spans:
        return DocumentPreflightReason.MISSING_SPANS
    if classification.triage_status == DocumentTriageStatus.PARTIAL:
        return DocumentPreflightReason.PARTIAL_SUPPORT
    return DocumentPreflightReason.READY


def _status_for(
    decisions: list[RunDocumentPreflightDecision],
) -> DocumentPreflightStatus:
    if not decisions:
        return DocumentPreflightStatus.FAILED
    usable_count = sum(1 for decision in decisions if decision.usable_for_methodology_extraction)
    if usable_count == 0:
        return DocumentPreflightStatus.FAILED
    if usable_count < len(decisions):
        return DocumentPreflightStatus.PARTIAL
    if any(decision.reason == DocumentPreflightReason.PARTIAL_SUPPORT for decision in decisions):
        return DocumentPreflightStatus.PARTIAL
    return DocumentPreflightStatus.COMPLETED
