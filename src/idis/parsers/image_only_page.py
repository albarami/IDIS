"""Fail-closed contracts for zero-text, non-watermark image-only PDF pages.

This module is pure: it validates already-produced parser/classifier receipts
and durable span proofs.  It never performs OCR, creates text, fabricates a
span, reads a file, or calls an external service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any, Never, TypeGuard

from idis.parsers.ocr import (
    OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    OCR_RESCUE_MATRIX,
    OcrError,
    ocr_page_execution_receipt_sha256,
    validate_observed_ocr_render_admission_payload,
    validate_ocr_page_execution_receipt_payload,
)

LEGACY_IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION = "image-only-page-receipt-v1"
IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION = "image-only-page-receipt-v2"
IMAGE_ONLY_PAGE_EVALUATION_SCHEMA_VERSION = "image-only-page-evaluation-v1"
IMAGE_ONLY_PAGE_SAFE_AGGREGATE_SCHEMA_VERSION = "image-only-page-safe-aggregate-v1"
IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED = "IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED"
IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE = "REVIEWED_SUBSTANTIVE_IMAGE"
IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED = "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED"
DATA_ROOM_RECONCILIATION_INCOMPLETE = "DATA_ROOM_RECONCILIATION_INCOMPLETE"

_CLASSIFICATION = "image_only_page"
_WATERMARK_OUTCOME = "BELOW_THRESHOLD"
_REVIEWER_CONFIRMED_SCOPE = "REVIEWER_CONFIRMED"
_RUNTIME_UNSEEN_SCOPE = "RUNTIME_UNSEEN"
_LOCATOR_SCHEME = "safe-pdf-page-v1"
_SUBSTANTIVE_SPAN_TYPES = frozenset({"PAGE_TEXT", "PARAGRAPH"})

_LEGACY_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "classification",
        "content_sha256",
        "page_number",
        "page_content_sha256",
        "safe_page_locator",
        "native_text_character_count",
        "primary_ocr_token_count",
        "rescue_accepted_token_count",
        "ocr_terminal_proof_sha256",
        "watermark_classifier_outcome",
        "watermark_classifier_receipt_sha256",
        "emitted_span_count",
        "emitted_text_character_count",
        "occurrence_scope",
        "review_status",
    }
)
_RECEIPT_FIELDS = _LEGACY_RECEIPT_FIELDS | frozenset(
    {"ocr_page_execution_receipt", "ocr_render_admission"}
)
_LOCATOR_FIELDS = frozenset({"scheme", "page_number", "locator_sha256"})
_SPAN_FIELDS = frozenset(
    {
        "page_number",
        "span_type",
        "content_hash",
        "locator_sha256",
        "persistence_status",
    }
)
_EVALUATION_FIELDS = frozenset(
    {
        "schema_version",
        "content_sha256",
        "page_count",
        "image_only_page_count",
        "substantive_span_count",
        "parse_status",
        "may_proceed",
        "terminal_code",
        "review_status",
        "reviewer_confirmed_page_count",
        "runtime_unseen_page_count",
        "post_run_spot_check_required_page_count",
        "status_counts",
        "safe_page_locators",
        "ordered_page_receipts_sha256",
    }
)


class ImageOnlyPageContractError(RuntimeError):
    """Secret-safe image-only contract failure with one stable code."""

    def __init__(self, code: str = DATA_ROOM_RECONCILIATION_INCOMPLETE) -> None:
        self.code = code
        super().__init__(code)


def _fail() -> Never:
    raise ImageOnlyPageContractError from None


def _is_plain_int(value: object, *, minimum: int = 0) -> TypeGuard[int]:
    return type(value) is int and value >= minimum


def _require_plain_int(value: object, *, minimum: int = 0) -> int:
    if not _is_plain_int(value, minimum=minimum):
        _fail()
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _same(expected: str, actual: object) -> bool:
    return isinstance(actual, str) and hmac.compare_digest(expected, actual)


def _canonical_json_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail()
    return hashlib.sha256(encoded).hexdigest()


def _locator_sha256(*, content_sha256: str, page_number: int) -> str:
    material = f"{content_sha256}\x00{_LOCATOR_SCHEME}\x00{page_number}".encode("ascii")
    return hashlib.sha256(material).hexdigest()


def _validated_locator(
    value: object,
    *,
    content_sha256: str,
    page_number: int,
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _LOCATOR_FIELDS:
        _fail()
    expected_digest = _locator_sha256(
        content_sha256=content_sha256,
        page_number=page_number,
    )
    if (
        value.get("scheme") != _LOCATOR_SCHEME
        or value.get("page_number") != page_number
        or not _same(expected_digest, value.get("locator_sha256"))
    ):
        _fail()
    return {
        "scheme": _LOCATOR_SCHEME,
        "page_number": page_number,
        "locator_sha256": expected_digest,
    }


def _validated_receipt(
    value: object,
    *,
    content_sha256: str,
    page_count: int | None,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail()
    schema_version = value.get("schema_version")
    expected_fields = (
        _RECEIPT_FIELDS
        if schema_version == IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION
        else _LEGACY_RECEIPT_FIELDS
        if schema_version == LEGACY_IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION
        else None
    )
    if expected_fields is None or set(value) != expected_fields:
        _fail()
    page_number = value.get("page_number")
    occurrence_scope = value.get("occurrence_scope")
    expected_review_status = (
        IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE
        if occurrence_scope == _REVIEWER_CONFIRMED_SCOPE
        else IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED
        if occurrence_scope == _RUNTIME_UNSEEN_SCOPE
        else None
    )
    if (
        value.get("classification") != _CLASSIFICATION
        or not _same(content_sha256, value.get("content_sha256"))
        or not _is_plain_int(page_number, minimum=1)
        or page_count is not None
        and page_number > page_count
        or not _is_sha256(value.get("page_content_sha256"))
        or value.get("native_text_character_count") != 0
        or type(value.get("native_text_character_count")) is not int
        or value.get("primary_ocr_token_count") != 0
        or type(value.get("primary_ocr_token_count")) is not int
        or value.get("rescue_accepted_token_count") != 0
        or type(value.get("rescue_accepted_token_count")) is not int
        or not _is_sha256(value.get("ocr_terminal_proof_sha256"))
        or value.get("watermark_classifier_outcome") != _WATERMARK_OUTCOME
        or not _is_sha256(value.get("watermark_classifier_receipt_sha256"))
        or value.get("emitted_span_count") != 0
        or type(value.get("emitted_span_count")) is not int
        or value.get("emitted_text_character_count") != 0
        or type(value.get("emitted_text_character_count")) is not int
        or expected_review_status is None
        or value.get("review_status") != expected_review_status
    ):
        _fail()
    assert isinstance(page_number, int)
    locator = _validated_locator(
        value.get("safe_page_locator"),
        content_sha256=content_sha256,
        page_number=page_number,
    )
    validated: dict[str, object] = {
        "schema_version": schema_version,
        "classification": _CLASSIFICATION,
        "content_sha256": content_sha256,
        "page_number": page_number,
        "page_content_sha256": value["page_content_sha256"],
        "safe_page_locator": locator,
        "native_text_character_count": 0,
        "primary_ocr_token_count": 0,
        "rescue_accepted_token_count": 0,
        "ocr_terminal_proof_sha256": value["ocr_terminal_proof_sha256"],
        "watermark_classifier_outcome": _WATERMARK_OUTCOME,
        "watermark_classifier_receipt_sha256": value["watermark_classifier_receipt_sha256"],
        "emitted_span_count": 0,
        "emitted_text_character_count": 0,
        "occurrence_scope": occurrence_scope,
        "review_status": expected_review_status,
    }
    if schema_version == IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION:
        try:
            page_execution = validate_ocr_page_execution_receipt_payload(
                value.get("ocr_page_execution_receipt")
            )
            render_admission = validate_observed_ocr_render_admission_payload(
                value.get("ocr_render_admission"),
                page_number=page_number,
            )
            terminal_proof_sha256 = ocr_page_execution_receipt_sha256(page_execution)
        except OcrError:
            _fail()
        if (
            page_execution.page_number != page_number
            or page_execution.outcome != "no_consensus"
            or page_execution.primary_completed is not True
            or page_execution.primary_token_count != 0
            or page_execution.rescue_attempted_candidate_count != len(OCR_RESCUE_MATRIX)
            or page_execution.rescue_completed_candidate_count != len(OCR_RESCUE_MATRIX)
            or page_execution.accepted_text is not False
            or page_execution.accepted_text_token_count != 0
            or page_execution.accepted_text_span_count != 0
            or page_execution.accepted_text_codepoint_count != 0
            or not _same(terminal_proof_sha256, value.get("ocr_terminal_proof_sha256"))
            or render_admission.schema_version != OCR_RENDER_ADMISSION_SCHEMA_VERSION
            or render_admission.page_number != page_number
            or not render_admission.has_observed_render
        ):
            _fail()
        validated["ocr_page_execution_receipt"] = page_execution.to_payload()
        validated["ocr_render_admission"] = render_admission.to_span_dict()
    return validated


def _validated_receipts(
    receipts: object,
    *,
    content_sha256: str,
    page_count: int | None,
) -> list[dict[str, object]]:
    if not isinstance(receipts, list) or not receipts:
        _fail()
    validated: list[dict[str, object]] = []
    previous_page = 0
    for value in receipts:
        receipt = _validated_receipt(
            value,
            content_sha256=content_sha256,
            page_count=page_count,
        )
        page_number = receipt["page_number"]
        assert isinstance(page_number, int)
        if page_number <= previous_page:
            _fail()
        previous_page = page_number
        validated.append(receipt)
    return validated


def _validated_spans(
    spans: object,
    *,
    page_count: int,
    image_only_pages: frozenset[int],
) -> list[dict[str, object]]:
    if not isinstance(spans, list):
        _fail()
    validated: list[dict[str, object]] = []
    identities: set[tuple[object, ...]] = set()
    for value in spans:
        if not isinstance(value, Mapping) or set(value) != _SPAN_FIELDS:
            _fail()
        page_number = value.get("page_number")
        identity = (
            page_number,
            value.get("span_type"),
            value.get("content_hash"),
            value.get("locator_sha256"),
        )
        if (
            not _is_plain_int(page_number, minimum=1)
            or page_number > page_count
            or page_number in image_only_pages
            or value.get("span_type") not in _SUBSTANTIVE_SPAN_TYPES
            or not _is_sha256(value.get("content_hash"))
            or not _is_sha256(value.get("locator_sha256"))
            or value.get("persistence_status") != "DURABLE"
            or identity in identities
        ):
            _fail()
        identities.add(identity)
        validated.append(dict(value))
    return validated


def evaluate_image_only_page_receipts(
    *,
    receipts: object,
    content_sha256: str,
    page_count: int,
    durable_substantive_spans: object,
    excluded_watermark_page_numbers: object = (),
) -> dict[str, Any]:
    """Evaluate exact page receipts without inventing content or success."""

    if not _is_sha256(content_sha256) or not _is_plain_int(page_count, minimum=1):
        _fail()
    validated_receipts = _validated_receipts(
        receipts,
        content_sha256=content_sha256,
        page_count=page_count,
    )
    image_only_pages = frozenset(
        _require_plain_int(receipt["page_number"], minimum=1) for receipt in validated_receipts
    )
    if not isinstance(excluded_watermark_page_numbers, (tuple, list, set, frozenset)):
        _fail()
    watermark_pages = frozenset(
        _require_plain_int(page_number, minimum=1)
        for page_number in excluded_watermark_page_numbers
    )
    if (
        len(watermark_pages) != len(excluded_watermark_page_numbers)
        or any(page_number > page_count for page_number in watermark_pages)
        or watermark_pages & image_only_pages
    ):
        _fail()
    spans = _validated_spans(
        durable_substantive_spans,
        page_count=page_count,
        image_only_pages=image_only_pages | watermark_pages,
    )
    substantive_pages = {_require_plain_int(span["page_number"], minimum=1) for span in spans}
    if image_only_pages | watermark_pages | substantive_pages != set(range(1, page_count + 1)):
        _fail()

    blocked = not spans
    if blocked and watermark_pages:
        # A zero-span document containing substantive imagery plus watermark-only
        # pages still requires an explicit document-level reviewer disposition.
        _fail()
    if blocked and len(image_only_pages) != page_count:
        _fail()
    if not blocked and len(image_only_pages) >= page_count:
        _fail()
    reviewer_confirmed_count = sum(
        receipt["occurrence_scope"] == _REVIEWER_CONFIRMED_SCOPE for receipt in validated_receipts
    )
    runtime_unseen_count = len(validated_receipts) - reviewer_confirmed_count

    return {
        "schema_version": IMAGE_ONLY_PAGE_EVALUATION_SCHEMA_VERSION,
        "content_sha256": content_sha256,
        "page_count": page_count,
        "image_only_page_count": len(validated_receipts),
        "substantive_span_count": len(spans),
        "parse_status": "FAILED" if blocked else "PARSED",
        "may_proceed": not blocked,
        "terminal_code": IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED if blocked else None,
        "review_status": (
            IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED
            if runtime_unseen_count
            else IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE
        ),
        "reviewer_confirmed_page_count": reviewer_confirmed_count,
        "runtime_unseen_page_count": runtime_unseen_count,
        "post_run_spot_check_required_page_count": runtime_unseen_count,
        "status_counts": {
            IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE: reviewer_confirmed_count,
            IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED: runtime_unseen_count,
        },
        "safe_page_locators": [receipt["safe_page_locator"] for receipt in validated_receipts],
        "ordered_page_receipts_sha256": _canonical_json_sha256(validated_receipts),
    }


def project_safe_image_only_page_receipts(value: object) -> list[dict[str, object]] | None:
    """Return exact text-free receipts, or ``None`` for any unsafe shape."""

    try:
        if not isinstance(value, list) or not value:
            _fail()
        first = value[0]
        if not isinstance(first, Mapping) or not _is_sha256(first.get("content_sha256")):
            _fail()
        content_sha256 = str(first["content_sha256"])
        return _validated_receipts(
            value,
            content_sha256=content_sha256,
            page_count=None,
        )
    except ImageOnlyPageContractError:
        return None


def _project_safe_evaluation(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != _EVALUATION_FIELDS:
        return None
    content_sha256 = value.get("content_sha256")
    page_count = value.get("page_count")
    image_only_count = value.get("image_only_page_count")
    substantive_count = value.get("substantive_span_count")
    reviewer_confirmed_count = value.get("reviewer_confirmed_page_count")
    runtime_unseen_count = value.get("runtime_unseen_page_count")
    post_run_count = value.get("post_run_spot_check_required_page_count")
    locators = value.get("safe_page_locators")
    expected_review_status = (
        IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED
        if runtime_unseen_count
        else IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE
    )
    if (
        value.get("schema_version") != IMAGE_ONLY_PAGE_EVALUATION_SCHEMA_VERSION
        or not _is_sha256(content_sha256)
        or not _is_plain_int(page_count, minimum=1)
        or not _is_plain_int(image_only_count, minimum=1)
        or image_only_count > page_count
        or not _is_plain_int(substantive_count)
        or not _is_plain_int(reviewer_confirmed_count)
        or not _is_plain_int(runtime_unseen_count)
        or reviewer_confirmed_count + runtime_unseen_count != image_only_count
        or post_run_count != runtime_unseen_count
        or value.get("review_status") != expected_review_status
        or value.get("status_counts")
        != {
            IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE: reviewer_confirmed_count,
            IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED: runtime_unseen_count,
        }
        or not _is_sha256(value.get("ordered_page_receipts_sha256"))
        or not isinstance(locators, list)
        or len(locators) != image_only_count
    ):
        return None
    assert isinstance(content_sha256, str)
    assert isinstance(page_count, int)
    projected_locators: list[dict[str, object]] = []
    previous_page = 0
    try:
        for locator in locators:
            if not isinstance(locator, Mapping):
                return None
            page_number = locator.get("page_number")
            if (
                not _is_plain_int(page_number, minimum=1)
                or page_number > page_count
                or page_number <= previous_page
            ):
                return None
            assert isinstance(page_number, int)
            projected_locators.append(
                _validated_locator(
                    locator,
                    content_sha256=content_sha256,
                    page_number=page_number,
                )
            )
            previous_page = page_number
    except ImageOnlyPageContractError:
        return None

    blocked = substantive_count == 0
    if blocked:
        coherent = (
            value.get("parse_status") == "FAILED"
            and value.get("may_proceed") is False
            and value.get("terminal_code") == IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED
            and image_only_count == page_count
        )
    else:
        coherent = (
            value.get("parse_status") == "PARSED"
            and value.get("may_proceed") is True
            and value.get("terminal_code") is None
            and image_only_count < page_count
        )
    if not coherent:
        return None
    return {
        "schema_version": IMAGE_ONLY_PAGE_EVALUATION_SCHEMA_VERSION,
        "content_sha256": content_sha256,
        "page_count": page_count,
        "image_only_page_count": image_only_count,
        "substantive_span_count": substantive_count,
        "parse_status": value["parse_status"],
        "may_proceed": value["may_proceed"],
        "terminal_code": value["terminal_code"],
        "review_status": expected_review_status,
        "reviewer_confirmed_page_count": reviewer_confirmed_count,
        "runtime_unseen_page_count": runtime_unseen_count,
        "post_run_spot_check_required_page_count": post_run_count,
        "status_counts": {
            IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE: reviewer_confirmed_count,
            IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED: runtime_unseen_count,
        },
        "safe_page_locators": projected_locators,
        "ordered_page_receipts_sha256": value["ordered_page_receipts_sha256"],
    }


def project_safe_image_only_page_metadata(
    *,
    receipts: object,
    evaluation: object,
    parse_status: object,
) -> dict[str, object] | None:
    """Project a cross-bound receipt/evaluation pair for public summaries."""

    safe_receipts = project_safe_image_only_page_receipts(receipts)
    safe_evaluation = _project_safe_evaluation(evaluation)
    if safe_receipts is None or safe_evaluation is None:
        return None
    reviewer_confirmed_count = sum(
        receipt["occurrence_scope"] == _REVIEWER_CONFIRMED_SCOPE for receipt in safe_receipts
    )
    runtime_unseen_count = len(safe_receipts) - reviewer_confirmed_count
    if (
        safe_evaluation["parse_status"] != parse_status
        or safe_evaluation["content_sha256"] != safe_receipts[0]["content_sha256"]
        or safe_evaluation["image_only_page_count"] != len(safe_receipts)
        or safe_evaluation["safe_page_locators"]
        != [receipt["safe_page_locator"] for receipt in safe_receipts]
        or safe_evaluation["ordered_page_receipts_sha256"] != _canonical_json_sha256(safe_receipts)
        or safe_evaluation["reviewer_confirmed_page_count"] != reviewer_confirmed_count
        or safe_evaluation["runtime_unseen_page_count"] != runtime_unseen_count
        or any(
            _require_plain_int(receipt["page_number"], minimum=1)
            > _require_plain_int(safe_evaluation["page_count"], minimum=1)
            for receipt in safe_receipts
        )
    ):
        return None
    return {
        "image_only_page_receipts": safe_receipts,
        "image_only_page_evaluation": safe_evaluation,
    }


def build_image_only_page_safe_aggregate(*, evaluations: object) -> dict[str, object]:
    """Derive safe post-run counts from exact, unique document evaluations."""

    if not isinstance(evaluations, list) or not evaluations:
        _fail()
    projected: list[dict[str, Any]] = []
    content_hashes: set[str] = set()
    for value in evaluations:
        evaluation = _project_safe_evaluation(value)
        if evaluation is None:
            _fail()
        content_sha256 = str(evaluation["content_sha256"])
        if content_sha256 in content_hashes:
            _fail()
        content_hashes.add(content_sha256)
        projected.append(evaluation)

    image_only_page_count = sum(
        _require_plain_int(evaluation["image_only_page_count"], minimum=1)
        for evaluation in projected
    )
    reviewer_confirmed_count = sum(
        _require_plain_int(evaluation["reviewer_confirmed_page_count"]) for evaluation in projected
    )
    runtime_unseen_count = sum(
        _require_plain_int(evaluation["runtime_unseen_page_count"]) for evaluation in projected
    )
    mixed_count = sum(evaluation["parse_status"] == "PARSED" for evaluation in projected)
    blocked_count = sum(evaluation["parse_status"] == "FAILED" for evaluation in projected)
    return {
        "schema_version": IMAGE_ONLY_PAGE_SAFE_AGGREGATE_SCHEMA_VERSION,
        "document_count": len(projected),
        "image_only_page_count": image_only_page_count,
        "mixed_document_count": mixed_count,
        "review_blocked_document_count": blocked_count,
        "reviewer_confirmed_page_count": reviewer_confirmed_count,
        "runtime_unseen_page_count": runtime_unseen_count,
        "post_run_spot_check_required_page_count": runtime_unseen_count,
        "status_counts": {
            IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE: reviewer_confirmed_count,
            IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED: runtime_unseen_count,
        },
        "ordered_evaluations_sha256": _canonical_json_sha256(projected),
    }


__all__ = [
    "DATA_ROOM_RECONCILIATION_INCOMPLETE",
    "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED",
    "IMAGE_ONLY_PAGE_EVALUATION_SCHEMA_VERSION",
    "IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED",
    "IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION",
    "IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE",
    "IMAGE_ONLY_PAGE_SAFE_AGGREGATE_SCHEMA_VERSION",
    "LEGACY_IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION",
    "ImageOnlyPageContractError",
    "build_image_only_page_safe_aggregate",
    "evaluate_image_only_page_receipts",
    "project_safe_image_only_page_metadata",
    "project_safe_image_only_page_receipts",
]
