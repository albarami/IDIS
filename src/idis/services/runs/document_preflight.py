"""Run-scoped document classification and parser triage preflight."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from typing import Any, Literal, TypeAlias, cast

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
    document_preflight_is_eligible_state,
    document_preflight_reason_for_state,
    document_preflight_status_for_decisions,
)
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult
from idis.services.documents.classification_service import InMemoryDocumentClassificationService
from idis.services.documents.classifier import DocumentDescriptor

_PreflightParseType: TypeAlias = Literal[
    "PDF",
    "XLSX",
    "DOCX",
    "PPTX",
    "HTML",
    "TEXT",
    "IMAGE",
    "MEDIA",
    "UNKNOWN",
]
_PERSISTED_DOCUMENT_TYPE_BY_PARSER_TYPE = {
    "PDF": "PDF",
    "XLSX": "XLSX",
    "DOCX": "DOCX",
    "PPTX": "PPTX",
    "HTML": "HTML",
    "TEXT": "TEXT",
    "IMAGE": "IMAGE",
    "MEDIA": "VIDEO",
}


class InMemoryRunDocumentPreflightService:
    """Classify and triage a persisted run corpus without external calls."""

    def __init__(
        self,
        classification_service: InMemoryDocumentClassificationService | None = None,
        no_substantive_content_receipt_verifier: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> None:
        """Initialize the preflight service."""
        self._classification_service = (
            classification_service or InMemoryDocumentClassificationService()
        )
        self._no_substantive_content_receipt_verifier = no_substantive_content_receipt_verifier

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

            confirmed_decision = self._confirmed_no_substantive_content_decision(
                document,
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
            )
            if confirmed_decision is not None:
                decisions.append(confirmed_decision)
                continue

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
                    secondary_fdd_categories=classification.secondary_fdd_categories,
                    secondary_cdd_categories=classification.secondary_cdd_categories,
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

    def _confirmed_no_substantive_content_decision(
        self,
        document: Mapping[str, Any],
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
    ) -> RunDocumentPreflightDecision | None:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping) or not isinstance(
            metadata.get("document_no_substantive_content_receipt"), Mapping
        ):
            return None
        verifier = self._no_substantive_content_receipt_verifier
        if verifier is None:
            return None
        try:
            verified = verifier(document)
        except Exception:
            verified = False
        if not verified:
            return None
        document_id = str(document.get("document_id") or "")
        classification_id = (
            "dc_"
            + hashlib.sha256(
                "|".join((tenant_id, deal_id, document_id)).encode("utf-8")
            ).hexdigest()[:24]
        )
        return RunDocumentPreflightDecision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            document_id=document_id,
            classification_id=classification_id,
            support_status=DocumentSupportStatus.EXCLUDED_CONFIRMED,
            triage_status=DocumentTriageStatus.EXCLUDED_CONFIRMED,
            usable_for_methodology_extraction=False,
            reason=DocumentPreflightReason.DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
            reason_codes=[DocumentPreflightReason.DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED.value],
            warning_codes=[],
            source_spans=[],
            secondary_fdd_categories=[],
            secondary_cdd_categories=[],
            methodology_target_areas=[],
        )

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
    parser_evidence_keys = (
        "parser_runtime_status",
        "span_count",
        "parse_error_codes",
        "parse_warning_codes",
    )
    if "parse_status" not in document and not any(key in metadata for key in parser_evidence_keys):
        # Compatibility is intentionally limited to non-persisted orchestrator inputs.
        # Repository-loaded rows always carry parse_status and must fail closed below.
        return None
    raw_error_codes = metadata.get("parse_error_codes")
    raw_warning_codes = metadata.get("parse_warning_codes")
    code_lists_valid = all(
        isinstance(value, list) and all(isinstance(code, str) and bool(code) for code in value)
        for value in (raw_error_codes, raw_warning_codes)
    )
    error_codes = (
        [code for code in raw_error_codes if isinstance(code, str)]
        if isinstance(raw_error_codes, list)
        else []
    )
    warning_codes = (
        [code for code in raw_warning_codes if isinstance(code, str)]
        if isinstance(raw_warning_codes, list)
        else []
    )
    parser_doc_type = str(
        metadata.get("parser_doc_type")
        or metadata.get("detected_format")
        or document.get("doc_type")
        or "UNKNOWN"
    ).upper()
    if parser_doc_type not in {
        "PDF",
        "XLSX",
        "DOCX",
        "PPTX",
        "HTML",
        "TEXT",
        "IMAGE",
        "MEDIA",
        "UNKNOWN",
    }:
        parser_doc_type = "UNKNOWN"
    persisted_doc_type = str(document.get("doc_type") or "").upper()
    expected_persisted_doc_type = _PERSISTED_DOCUMENT_TYPE_BY_PARSER_TYPE.get(parser_doc_type)
    canonical_type_matches = (
        expected_persisted_doc_type is not None
        and persisted_doc_type == expected_persisted_doc_type
    )

    extracted_span_count = metadata.get("span_count")
    durable_spans = document.get("spans")
    has_extracted_spans = (
        isinstance(extracted_span_count, int)
        and not isinstance(extracted_span_count, bool)
        and extracted_span_count > 0
        and isinstance(durable_spans, list)
        and len(durable_spans) == extracted_span_count
    )
    errors: list[ParseError] = []
    for code in error_codes:
        try:
            parse_code = ParseErrorCode(code)
        except ValueError:
            parse_code = ParseErrorCode.INTERNAL_ERROR
        errors.append(ParseError(code=parse_code, message=code))
    if not canonical_type_matches:
        errors = [
            ParseError(
                code=ParseErrorCode.CORRUPTED_FILE,
                message="Persisted parser identity does not match canonical document type",
            )
        ]

    has_parser_evidence = any(key in metadata for key in parser_evidence_keys)
    if has_parser_evidence and not code_lists_valid and not errors:
        errors.append(
            ParseError(
                code=ParseErrorCode.CORRUPTED_FILE,
                message="Persisted parser code-list proof is incomplete",
            )
        )
    persisted_success = (
        str(document.get("parse_status", "")).upper() == "PARSED"
        and metadata.get("parser_runtime_status") == "completed"
    )
    has_exact_success_evidence = (
        persisted_success
        and canonical_type_matches
        and has_extracted_spans
        and code_lists_valid
        and not errors
        and not warning_codes
    )
    is_exact_native_success = (
        parser_doc_type in {"PDF", "XLSX", "DOCX", "PPTX", "HTML", "TEXT"}
        and has_exact_success_evidence
    )
    has_successful_image_ocr = (
        parser_doc_type == "IMAGE"
        and metadata.get("ocr_performed") is True
        and has_exact_success_evidence
    )
    has_successful_media_evidence = (
        parser_doc_type == "MEDIA"
        and _durable_media_receipts_match(metadata, durable_spans)
        and has_exact_success_evidence
    )
    if not (
        errors
        or warning_codes
        or is_exact_native_success
        or has_successful_image_ocr
        or has_successful_media_evidence
    ):
        errors.append(
            ParseError(
                code=ParseErrorCode.CORRUPTED_FILE,
                message=(
                    "Persisted parser span proof is incomplete"
                    if has_parser_evidence
                    else "Persisted parser evidence is missing"
                ),
            )
        )

    return ParseResult(
        doc_type=cast(_PreflightParseType, parser_doc_type),
        success=not errors,
        metadata=metadata,
        errors=errors,
        warnings=warning_codes,
    )


def _durable_media_receipts_match(metadata: Mapping[str, Any], spans: Any) -> bool:
    """Rebind restart-loaded media receipts to their durable evidence tracks."""
    stt_receipt = metadata.get("media_stt_receipt")
    frame_receipt = metadata.get("media_frame_ocr_receipt")
    if (
        metadata.get("media_transcription_performed") is not True
        or metadata.get("media_frame_ocr_performed") is not True
        or not isinstance(stt_receipt, Mapping)
        or not isinstance(frame_receipt, Mapping)
        or not isinstance(spans, list)
        or stt_receipt.get("schema_version") != "media-stt-receipt-v1"
        or stt_receipt.get("status") != "completed"
        or frame_receipt.get("schema_version") != "media-frame-ocr-receipt-v1"
        or frame_receipt.get("status") != "completed"
        or frame_receipt.get("sampling_policy") != "periodic-plus-scenes-v1"
    ):
        return False

    stt_evidence: list[dict[str, Any]] = []
    frame_evidence: list[dict[str, Any]] = []
    prior_end = {"media_transcript": -1, "video_frame_ocr": -1}
    for span in spans:
        if not isinstance(span, Mapping) or span.get("span_type") != "TIMECODE":
            return False
        locator = span.get("locator")
        content_hash = span.get("content_hash")
        if (
            not isinstance(locator, Mapping)
            or not isinstance(content_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None
        ):
            return False
        source = locator.get("source")
        start_ms = locator.get("start_ms")
        end_ms = locator.get("end_ms")
        if (
            source not in prior_end
            or type(start_ms) is not int
            or type(end_ms) is not int
            or start_ms < 0
            or end_ms <= start_ms
            or start_ms < prior_end[source]
        ):
            return False
        prior_end[source] = end_ms
        if source == "media_transcript":
            if set(locator) != {"source", "start_ms", "end_ms"}:
                return False
            stt_evidence.append(
                {
                    "source": source,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "content_hash": content_hash,
                }
            )
            continue
        if set(locator) != {
            "source",
            "sample_index",
            "start_ms",
            "end_ms",
            "confidence",
        }:
            return False
        sample_index = locator.get("sample_index")
        confidence = locator.get("confidence")
        if (
            type(sample_index) is not int
            or sample_index < 0
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            return False
        frame_evidence.append(
            {
                "source": source,
                "sample_index": sample_index,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "confidence": float(confidence),
                "content_hash": content_hash,
            }
        )

    stt_count = stt_receipt.get("evidence_span_count")
    frame_count = frame_receipt.get("evidence_span_count")
    completed_count = frame_receipt.get("completed_sample_count")
    scheduled_count = frame_receipt.get("scheduled_sample_count")
    if (
        type(stt_count) is not int
        or type(frame_count) is not int
        or type(completed_count) is not int
        or type(scheduled_count) is not int
        or stt_count != len(stt_evidence)
        or frame_count != len(frame_evidence)
        or completed_count != scheduled_count
        or scheduled_count <= 0
        or stt_receipt.get("evidence_sha256") != _canonical_media_evidence(stt_evidence)
        or frame_receipt.get("evidence_sha256") != _canonical_media_evidence(frame_evidence)
        or frame_receipt.get("nonempty_sample_count") != len(frame_evidence)
        or frame_receipt.get("confidence_count") != len(frame_evidence)
    ):
        return False
    return bool(stt_evidence or frame_evidence)


def _canonical_media_evidence(value: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    return document_preflight_is_eligible_state(
        support_status=classification.support_status,
        triage_status=classification.triage_status,
        parser_usable_without_conversion=(
            classification.usable_for_methodology_extraction
            and classification.parser_capability.usable_without_conversion
        ),
        has_source_spans=bool(source_spans),
    )


def _reason_for(
    classification: DocumentClassification,
    *,
    has_source_spans: bool,
) -> DocumentPreflightReason:
    return document_preflight_reason_for_state(
        support_status=classification.support_status,
        triage_status=classification.triage_status,
        has_source_spans=has_source_spans,
    )


def _status_for(
    decisions: list[RunDocumentPreflightDecision],
) -> DocumentPreflightStatus:
    return document_preflight_status_for_decisions(decisions)
