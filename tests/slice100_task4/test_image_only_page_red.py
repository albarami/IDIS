"""RED contracts for truthful zero-text, non-watermark image-only pages.

All fixture identities and hashes are fictional.  The contract admits an
image-only page without blocking only when another page has durable substantive
evidence; a document made entirely of such pages remains failed for review.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from types import ModuleType
from typing import Any

import pytest

from idis.api.routes.documents import _document_summary_from_durable_row

RUNTIME_API_MISSING = "TASK4_IMAGE_ONLY_PAGE_API_MISSING"
RECEIPT_SCHEMA = "image-only-page-receipt-v1"
CURRENT_RECEIPT_SCHEMA = "image-only-page-receipt-v2"
EVALUATION_SCHEMA = "image-only-page-evaluation-v1"
AGGREGATE_SCHEMA = "image-only-page-safe-aggregate-v1"
SPOT_CHECK_STATUS = "IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED"
REVIEWED_STATUS = "REVIEWED_SUBSTANTIVE_IMAGE"
DOCUMENT_REVIEW_REQUIRED = "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED"
RECONCILIATION_INCOMPLETE = "DATA_ROOM_RECONCILIATION_INCOMPLETE"

TENANT_ID = "10000000-0000-4000-8000-000000000001"
DEAL_ID = "20000000-0000-4000-8000-000000000001"
DOCUMENT_ID = "30000000-0000-4000-8000-000000000001"
ARTIFACT_ID = "40000000-0000-4000-8000-000000000001"
SAFE_ALIAS = "company-001-document-00017.pdf"
CONTENT_SHA256 = hashlib.sha256(b"fictional-parent-pdf").hexdigest()
OCR_TERMINAL_PROOF_SHA256 = hashlib.sha256(b"fictional-complete-zero-text-ocr").hexdigest()
WATERMARK_BELOW_THRESHOLD_PROOF_SHA256 = hashlib.sha256(
    b"fictional-below-watermark-threshold"
).hexdigest()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _target() -> ModuleType:
    name = "idis.services.ingestion.image_only_page"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        pytest.fail(f"{RUNTIME_API_MISSING}: {name}", pytrace=False)


def _required_api(name: str) -> Any:
    module = _target()
    value = getattr(module, name, None)
    if value is None:
        pytest.fail(f"{RUNTIME_API_MISSING}: {module.__name__}.{name}", pytrace=False)
    return value


def _error_type() -> type[Exception]:
    value = _required_api("ImageOnlyPageContractError")
    assert isinstance(value, type) and issubclass(value, Exception)
    return value


def _safe_locator(*, page_number: int, content_sha256: str = CONTENT_SHA256) -> dict[str, Any]:
    locator_sha256 = hashlib.sha256(
        f"{content_sha256}\x00safe-pdf-page-v1\x00{page_number}".encode("ascii")
    ).hexdigest()
    return {
        "scheme": "safe-pdf-page-v1",
        "page_number": page_number,
        "locator_sha256": locator_sha256,
    }


def _receipt(
    *,
    page_number: int = 2,
    content_sha256: str = CONTENT_SHA256,
    occurrence_scope: str = "RUNTIME_UNSEEN",
) -> dict[str, Any]:
    review_status = (
        REVIEWED_STATUS if occurrence_scope == "REVIEWER_CONFIRMED" else SPOT_CHECK_STATUS
    )
    return {
        "schema_version": RECEIPT_SCHEMA,
        # Preserve the reviewer-approved terminal-class identity exactly.
        "classification": "image_only_page",
        "content_sha256": content_sha256,
        "page_number": page_number,
        "page_content_sha256": hashlib.sha256(
            f"fictional-image-only-page:{content_sha256}:{page_number}".encode("ascii")
        ).hexdigest(),
        "safe_page_locator": _safe_locator(
            page_number=page_number,
            content_sha256=content_sha256,
        ),
        "native_text_character_count": 0,
        "primary_ocr_token_count": 0,
        "rescue_accepted_token_count": 0,
        "ocr_terminal_proof_sha256": OCR_TERMINAL_PROOF_SHA256,
        "watermark_classifier_outcome": "BELOW_THRESHOLD",
        "watermark_classifier_receipt_sha256": (WATERMARK_BELOW_THRESHOLD_PROOF_SHA256),
        "emitted_span_count": 0,
        "emitted_text_character_count": 0,
        "occurrence_scope": occurrence_scope,
        "review_status": review_status,
    }


def _substantive_span(*, page_number: int = 1) -> dict[str, Any]:
    return {
        "page_number": page_number,
        "span_type": "PAGE_TEXT",
        "content_hash": hashlib.sha256(
            f"fictional-substantive-span:{CONTENT_SHA256}:{page_number}".encode("ascii")
        ).hexdigest(),
        "locator_sha256": hashlib.sha256(
            f"fictional-durable-span-locator:{CONTENT_SHA256}:{page_number}".encode("ascii")
        ).hexdigest(),
        "persistence_status": "DURABLE",
    }


def _expected_evaluation(
    receipts: list[dict[str, Any]],
    *,
    page_count: int,
    substantive_span_count: int,
) -> dict[str, Any]:
    blocked = substantive_span_count == 0
    reviewed_count = sum(
        receipt["occurrence_scope"] == "REVIEWER_CONFIRMED" for receipt in receipts
    )
    runtime_unseen_count = len(receipts) - reviewed_count
    return {
        "schema_version": EVALUATION_SCHEMA,
        "content_sha256": CONTENT_SHA256,
        "page_count": page_count,
        "image_only_page_count": len(receipts),
        "substantive_span_count": substantive_span_count,
        "parse_status": "FAILED" if blocked else "PARSED",
        "may_proceed": not blocked,
        "terminal_code": DOCUMENT_REVIEW_REQUIRED if blocked else None,
        "review_status": SPOT_CHECK_STATUS if runtime_unseen_count else REVIEWED_STATUS,
        "reviewer_confirmed_page_count": reviewed_count,
        "runtime_unseen_page_count": runtime_unseen_count,
        "post_run_spot_check_required_page_count": runtime_unseen_count,
        "status_counts": {
            REVIEWED_STATUS: reviewed_count,
            SPOT_CHECK_STATUS: runtime_unseen_count,
        },
        "safe_page_locators": [receipt["safe_page_locator"] for receipt in receipts],
        "ordered_page_receipts_sha256": _digest(receipts),
    }


def _evaluate(
    receipts: list[dict[str, Any]],
    *,
    page_count: int,
    durable_substantive_spans: list[dict[str, Any]],
) -> dict[str, Any]:
    evaluator = _required_api("evaluate_image_only_page_receipts")
    result = evaluator(
        receipts=receipts,
        content_sha256=CONTENT_SHA256,
        page_count=page_count,
        durable_substantive_spans=durable_substantive_spans,
    )
    assert isinstance(result, dict)
    return result


def _durable_row(
    *,
    receipts: list[dict[str, Any]],
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "doc_id": ARTIFACT_ID,
        "document_id": DOCUMENT_ID,
        "deal_id": DEAL_ID,
        "artifact_doc_type": "DATA_ROOM_FILE",
        "doc_type": "PDF",
        "document_name": SAFE_ALIAS,
        "created_at": "2026-07-17T12:00:00Z",
        "parse_status": evaluation["parse_status"],
        "sha256": CONTENT_SHA256,
        "uri": "tenant-safe/fictional-object.pdf",
        "source_metadata": {
            "source_system": "slice100",
            "upload_filename": SAFE_ALIAS,
        },
        "metadata": {
            "image_only_page_receipts": receipts,
            "image_only_page_evaluation": evaluation,
        },
    }


def test_schema_and_status_constants_are_stable() -> None:
    # Fresh governed parses emit v2; the v1 fixtures below prove durable read compatibility.
    assert _required_api("IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION") == CURRENT_RECEIPT_SCHEMA
    assert _required_api("LEGACY_IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION") == RECEIPT_SCHEMA
    assert _required_api("IMAGE_ONLY_PAGE_EVALUATION_SCHEMA_VERSION") == EVALUATION_SCHEMA
    assert _required_api("IMAGE_ONLY_PAGE_SAFE_AGGREGATE_SCHEMA_VERSION") == AGGREGATE_SCHEMA
    assert _required_api("IMAGE_ONLY_PAGE_POST_RUN_SPOT_CHECK_REQUIRED") == SPOT_CHECK_STATUS
    assert _required_api("IMAGE_ONLY_PAGE_REVIEWED_SUBSTANTIVE_IMAGE") == REVIEWED_STATUS
    assert _required_api("IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED") == DOCUMENT_REVIEW_REQUIRED


def test_one_image_only_page_is_non_blocking_only_beside_real_substantive_evidence() -> None:
    receipts = [_receipt(page_number=2)]

    result = _evaluate(
        receipts,
        page_count=2,
        durable_substantive_spans=[_substantive_span(page_number=1)],
    )

    assert result == _expected_evaluation(
        receipts,
        page_count=2,
        substantive_span_count=1,
    )
    page = receipts[0]
    assert page["content_sha256"] == CONTENT_SHA256
    assert page["page_number"] == 2
    assert len(page["page_content_sha256"]) == 64
    assert page["safe_page_locator"] == _safe_locator(page_number=2)
    assert page["review_status"] == SPOT_CHECK_STATUS
    assert page["emitted_span_count"] == 0
    assert page["emitted_text_character_count"] == 0
    assert not ({"text", "text_excerpt", "spans"} & set(page))


def test_reviewer_confirmed_page_carries_no_post_run_spot_check_debt() -> None:
    receipts = [_receipt(page_number=2, occurrence_scope="REVIEWER_CONFIRMED")]

    result = _evaluate(
        receipts,
        page_count=2,
        durable_substantive_spans=[_substantive_span(page_number=1)],
    )

    assert result == _expected_evaluation(
        receipts,
        page_count=2,
        substantive_span_count=1,
    )
    assert result["review_status"] == REVIEWED_STATUS
    assert result["reviewer_confirmed_page_count"] == 1
    assert result["runtime_unseen_page_count"] == 0
    assert result["post_run_spot_check_required_page_count"] == 0


@pytest.mark.parametrize(
    "spans",
    [
        [],
        [_substantive_span(page_number=2)],
        [{**_substantive_span(), "content_hash": "not-a-digest"}],
        [{**_substantive_span(), "persistence_status": "PENDING"}],
        [{**_substantive_span(), "page_number": 0}],
    ],
    ids=(
        "no-substantive-evidence",
        "same-page-overlap",
        "malformed-content-hash",
        "not-durable",
        "out-of-range-page",
    ),
)
def test_mixed_document_claim_cannot_be_created_from_unproven_or_overlapping_span_proofs(
    spans: list[dict[str, Any]],
) -> None:
    with pytest.raises(_error_type()) as caught:
        _evaluate(
            [_receipt(page_number=2)],
            page_count=2,
            durable_substantive_spans=spans,
        )

    assert getattr(caught.value, "code", None) == RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize(
    "occurrence_scope",
    ["REVIEWER_CONFIRMED", "RUNTIME_UNSEEN"],
)
def test_all_image_only_document_remains_failed_with_review_required_code(
    occurrence_scope: str,
) -> None:
    receipts = [
        _receipt(page_number=1, occurrence_scope=occurrence_scope),
        _receipt(page_number=2, occurrence_scope=occurrence_scope),
    ]

    result = _evaluate(
        receipts,
        page_count=2,
        durable_substantive_spans=[],
    )

    assert result == _expected_evaluation(
        receipts,
        page_count=2,
        substantive_span_count=0,
    )
    assert result["parse_status"] == "FAILED"
    assert result["may_proceed"] is False
    assert result["terminal_code"] == DOCUMENT_REVIEW_REQUIRED
    assert result["image_only_page_count"] == result["page_count"]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", "image-only-page-receipt-v0"),
        ("classification", "watermark_only"),
        ("content_sha256", "0" * 64),
        ("page_number", 0),
        ("page_content_sha256", "not-a-digest"),
        ("native_text_character_count", 1),
        ("primary_ocr_token_count", 1),
        ("rescue_accepted_token_count", 1),
        ("ocr_terminal_proof_sha256", "not-a-digest"),
        ("watermark_classifier_outcome", "MATCHED_EXEMPLAR"),
        ("watermark_classifier_receipt_sha256", "not-a-digest"),
        ("emitted_span_count", 1),
        ("emitted_text_character_count", 1),
        ("occurrence_scope", "UNTRACKED"),
        ("review_status", "REVIEWED"),
    ],
)
def test_receipt_mutation_fails_closed(field: str, replacement: object) -> None:
    receipt = {**_receipt(), field: replacement}

    with pytest.raises(_error_type()) as caught:
        _evaluate(
            [receipt],
            page_count=2,
            durable_substantive_spans=[_substantive_span()],
        )

    assert getattr(caught.value, "code", None) == RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize(
    ("occurrence_scope", "review_status"),
    [
        ("REVIEWER_CONFIRMED", SPOT_CHECK_STATUS),
        ("RUNTIME_UNSEEN", REVIEWED_STATUS),
    ],
)
def test_occurrence_scope_and_review_status_are_cross_bound(
    occurrence_scope: str,
    review_status: str,
) -> None:
    receipt = _receipt(occurrence_scope=occurrence_scope)
    receipt["review_status"] = review_status

    with pytest.raises(_error_type()) as caught:
        _evaluate(
            [receipt],
            page_count=2,
            durable_substantive_spans=[_substantive_span()],
        )

    assert getattr(caught.value, "code", None) == RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize(
    "locator_mutation",
    [
        {"page_number": 1},
        {"scheme": "private-pdf-path-v1"},
        {"locator_sha256": "0" * 64},
        {"source_path": "fictional-private-file.pdf"},
    ],
)
def test_safe_page_locator_is_exact_and_cannot_carry_private_locator_material(
    locator_mutation: dict[str, object],
) -> None:
    receipt = _receipt()
    receipt["safe_page_locator"].update(locator_mutation)

    with pytest.raises(_error_type()) as caught:
        _evaluate(
            [receipt],
            page_count=2,
            durable_substantive_spans=[_substantive_span()],
        )

    assert getattr(caught.value, "code", None) == RECONCILIATION_INCOMPLETE


def test_duplicate_page_receipt_fails_instead_of_inflating_safe_aggregates() -> None:
    receipt = _receipt()

    with pytest.raises(_error_type()) as caught:
        _evaluate(
            [receipt, copy.deepcopy(receipt)],
            page_count=2,
            durable_substantive_spans=[_substantive_span()],
        )

    assert getattr(caught.value, "code", None) == RECONCILIATION_INCOMPLETE


def test_public_projector_preserves_exact_safe_receipt_and_rejects_private_fields() -> None:
    projector = _required_api("project_safe_image_only_page_receipts")
    receipts = [_receipt()]

    assert projector(receipts) == receipts

    for private_key in (
        "text",
        "text_excerpt",
        "spans",
        "source_path",
        "private_filename",
        "render_bytes",
        "signature_base64",
    ):
        poisoned = copy.deepcopy(receipts)
        poisoned[0][private_key] = "fictional-private-canary@example.invalid"
        assert projector(poisoned) is None


def test_mixed_public_document_stays_parsed_and_exposes_only_safe_page_proof() -> None:
    receipts = [_receipt(page_number=2)]
    evaluation = _expected_evaluation(
        receipts,
        page_count=2,
        substantive_span_count=1,
    )

    summary = _document_summary_from_durable_row(
        _durable_row(receipts=receipts, evaluation=evaluation)
    )

    assert summary.parse_status == "PARSED"
    assert summary.terminal_disposition is None
    assert summary.metadata == {
        "image_only_page_receipts": receipts,
        "image_only_page_evaluation": evaluation,
    }
    assert "fictional-private-canary" not in summary.model_dump_json()


def test_all_image_only_public_document_exposes_failed_review_state_not_fake_success() -> None:
    receipts = [_receipt(page_number=1), _receipt(page_number=2)]
    evaluation = _expected_evaluation(
        receipts,
        page_count=2,
        substantive_span_count=0,
    )

    summary = _document_summary_from_durable_row(
        _durable_row(receipts=receipts, evaluation=evaluation)
    )

    assert summary.parse_status == "FAILED"
    assert summary.terminal_disposition == DOCUMENT_REVIEW_REQUIRED
    assert summary.metadata == {
        "image_only_page_receipts": receipts,
        "image_only_page_evaluation": evaluation,
    }


def test_future_occurrences_roll_up_to_safe_post_run_spot_check_aggregates() -> None:
    evaluator = _required_api("evaluate_image_only_page_receipts")
    aggregate = _required_api("build_image_only_page_safe_aggregate")
    first_receipts = [_receipt(page_number=2, occurrence_scope="REVIEWER_CONFIRMED")]
    second_content_sha256 = hashlib.sha256(b"fictional-second-parent-pdf").hexdigest()
    second_receipts = [
        _receipt(page_number=1, content_sha256=second_content_sha256),
        _receipt(page_number=2, content_sha256=second_content_sha256),
    ]
    first = evaluator(
        receipts=first_receipts,
        content_sha256=CONTENT_SHA256,
        page_count=2,
        durable_substantive_spans=[_substantive_span()],
    )
    second = evaluator(
        receipts=second_receipts,
        content_sha256=second_content_sha256,
        page_count=2,
        durable_substantive_spans=[],
    )

    result = aggregate(evaluations=[first, second])

    assert result == {
        "schema_version": AGGREGATE_SCHEMA,
        "document_count": 2,
        "image_only_page_count": 3,
        "mixed_document_count": 1,
        "review_blocked_document_count": 1,
        "reviewer_confirmed_page_count": 1,
        "runtime_unseen_page_count": 2,
        "post_run_spot_check_required_page_count": 2,
        "status_counts": {
            REVIEWED_STATUS: 1,
            SPOT_CHECK_STATUS: 2,
        },
        "ordered_evaluations_sha256": _digest([first, second]),
    }
    serialized = json.dumps(result, sort_keys=True)
    for forbidden in (
        "text_excerpt",
        "source_path",
        "private_filename",
        "render_bytes",
        "signature_base64",
        "fictional-private-canary",
    ):
        assert forbidden not in serialized
