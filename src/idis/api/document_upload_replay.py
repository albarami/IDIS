"""Pure invariant check before replaying a confirmed no-substantive-content response."""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Never
from uuid import UUID

from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION,
    WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE,
    DocumentNoSubstantiveContentAttestationError,
    VerifiedDocumentNoSubstantiveContentAttestation,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    validate_document_no_substantive_content_receipt,
    validate_document_no_substantive_content_receipt_v2,
)

IDEMPOTENCY_REPLAY_INVARIANT_FAILED = "IDEMPOTENCY_REPLAY_INVARIANT_FAILED"


class DocumentUploadReplayInvariantError(RuntimeError):
    """A cached upload response no longer matches current durable truth."""

    code = IDEMPOTENCY_REPLAY_INVARIANT_FAILED

    def __init__(self) -> None:
        super().__init__(self.code)


def _fail() -> Never:
    raise DocumentUploadReplayInvariantError from None


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str) or value != value.lower():
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _same(expected: str, actual: object) -> bool:
    return isinstance(actual, str) and hmac.compare_digest(expected, actual)


def _same_mapping(expected: Mapping[str, object], actual: object) -> bool:
    """Compare JSON-safe receipt mappings without Python's bool/int equality alias."""

    if not isinstance(actual, Mapping):
        return False
    try:
        expected_json = json.dumps(
            dict(expected),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        actual_json = json.dumps(
            dict(actual),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected_json, actual_json)


def _durable_span_pages(spans: list[object], *, page_count: int) -> set[int]:
    """Require every persisted mixed-document span to retain a bounded page locator."""

    pages: set[int] = set()
    for span in spans:
        if not isinstance(span, Mapping):
            _fail()
        locator = span.get("locator")
        if not isinstance(locator, Mapping):
            _fail()
        page_number = locator.get("page", locator.get("page_number"))
        if type(page_number) is not int or page_number < 1 or page_number > page_count:
            _fail()
        pages.add(page_number)
    return pages


def validate_upload_replay_invariant(
    *,
    cached_response: Mapping[str, object],
    durable_document: Mapping[str, object],
    attestation: (
        VerifiedDocumentNoSubstantiveContentAttestation
        | VerifiedDocumentNoSubstantiveContentAttestationManifest
    ),
    tenant_id: str,
    source_tree_fingerprint: str,
    safe_alias: str,
    content_sha256: str,
    now: datetime,
) -> None:
    """Reject a cached 201 unless current trust and durable state still agree."""

    try:
        durable_span_count = (
            durable_document.get("span_count") if isinstance(durable_document, Mapping) else None
        )
        if (
            not isinstance(cached_response, Mapping)
            or cached_response.get("status_code") != 201
            or cached_response.get("media_type") != "application/json"
            or not isinstance(durable_document, Mapping)
            or not _canonical_uuid(tenant_id)
            or not _same(tenant_id, durable_document.get("tenant_id"))
            or type(durable_span_count) is not int
            or durable_span_count < 0
        ):
            _fail()
        assert isinstance(durable_span_count, int)
        durable_spans = durable_document.get("spans")
        if not isinstance(durable_spans, list) or len(durable_spans) != durable_span_count:
            _fail()

        body = cached_response.get("body")
        metadata = durable_document.get("metadata")
        if not isinstance(body, Mapping) or not isinstance(metadata, Mapping):
            _fail()
        response_document_id = body.get("document_id")
        durable_document_id = durable_document.get("document_id")
        response_artifact_id = body.get("doc_id")
        durable_artifact_id = durable_document.get("doc_id")
        response_deal_id = body.get("deal_id")
        durable_deal_id = durable_document.get("deal_id")
        if (
            not _canonical_uuid(response_document_id)
            or not _canonical_uuid(durable_document_id)
            or not _same(str(response_document_id), durable_document_id)
            or not _canonical_uuid(response_artifact_id)
            or not _canonical_uuid(durable_artifact_id)
            or not _same(str(durable_artifact_id), response_artifact_id)
            or not _canonical_uuid(response_deal_id)
            or not _canonical_uuid(durable_deal_id)
            or not _same(str(durable_deal_id), response_deal_id)
            or body.get("parse_status") != durable_document.get("parse_status")
            or not _same(safe_alias, body.get("title"))
            or not _same(content_sha256, body.get("sha256"))
        ):
            _fail()
        receipt = metadata.get("document_no_substantive_content_receipt")
        if not isinstance(receipt, Mapping):
            _fail()

        response_metadata = body.get("metadata")
        response_source_metadata = body.get("source_metadata")
        if (
            not isinstance(response_metadata, Mapping)
            or not isinstance(response_source_metadata, Mapping)
            or not _same_mapping(
                receipt,
                response_metadata.get("document_no_substantive_content_receipt"),
            )
            or not _same(
                source_tree_fingerprint,
                response_source_metadata.get("source_tree_fingerprint"),
            )
            or not _same(safe_alias, response_source_metadata.get("upload_filename"))
        ):
            _fail()

        if receipt.get("schema_version") == NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION:
            if not isinstance(
                attestation,
                VerifiedDocumentNoSubstantiveContentAttestationManifest,
            ):
                _fail()
            attestation.exemplar.require_active(now=now)
            if now < attestation.batch.approved_at or now >= attestation.batch.expires_at:
                raise DocumentNoSubstantiveContentAttestationError(
                    WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE
                )
            validate_document_no_substantive_content_receipt_v2(
                receipt=receipt,
                verified_manifest=attestation.runtime_summary(),
                tenant_id=tenant_id,
                source_tree_fingerprint=source_tree_fingerprint,
                safe_alias=safe_alias,
                content_sha256=content_sha256,
                now=now,
            )
            whole_document_terminal = receipt.get("whole_document_terminal")
            substantive_span_count = receipt.get("substantive_span_count")
            if (
                not isinstance(whole_document_terminal, bool)
                or type(substantive_span_count) is not int
                or substantive_span_count != durable_span_count
            ):
                _fail()
            if whole_document_terminal:
                if (
                    durable_document.get("parse_status") != "FAILED"
                    or body.get("terminal_disposition") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
                    or substantive_span_count != 0
                ):
                    _fail()
            elif (
                durable_document.get("parse_status") != "PARSED"
                or body.get("terminal_disposition") is not None
                or substantive_span_count < 1
            ):
                _fail()
            if not whole_document_terminal:
                page_count = receipt.get("page_count")
                classifications = receipt.get("watermark_page_classifications")
                if type(page_count) is not int or not isinstance(classifications, list):
                    _fail()
                classified_pages = {
                    classification.get("page_number")
                    for classification in classifications
                    if isinstance(classification, Mapping)
                }
                span_pages = _durable_span_pages(durable_spans, page_count=page_count)
                if (
                    any(type(page) is not int for page in classified_pages)
                    or classified_pages & span_pages
                    or classified_pages | span_pages != set(range(1, page_count + 1))
                ):
                    _fail()
        else:
            if not isinstance(attestation, VerifiedDocumentNoSubstantiveContentAttestation):
                _fail()
            if (
                durable_document.get("parse_status") != "FAILED"
                or body.get("terminal_disposition") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
                or durable_span_count != 0
            ):
                _fail()
            validate_document_no_substantive_content_receipt(
                receipt=receipt,
                attestation=attestation,
                tenant_id=tenant_id,
                source_tree_fingerprint=source_tree_fingerprint,
                safe_alias=safe_alias,
                content_sha256=content_sha256,
                now=now,
            )
    except DocumentNoSubstantiveContentAttestationError:
        _fail()


__all__ = [
    "DocumentUploadReplayInvariantError",
    "IDEMPOTENCY_REPLAY_INVARIANT_FAILED",
    "validate_upload_replay_invariant",
]
