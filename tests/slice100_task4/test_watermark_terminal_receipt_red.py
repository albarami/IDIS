"""RED contracts for page-level watermark provenance and terminal receipt v2."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pytest

from idis.api.routes.documents import _document_summary_from_durable_row

RUNTIME_API_MISSING = "TASK4_WATERMARK_RUNTIME_API_MISSING"
TENANT_ID = "10000000-0000-4000-8000-000000000001"
DEAL_ID = "20000000-0000-4000-8000-000000000001"
DOCUMENT_ID = "30000000-0000-4000-8000-000000000001"
ARTIFACT_ID = "40000000-0000-4000-8000-000000000001"
SOURCE_TREE_FINGERPRINT = hashlib.sha256(b"fictional-watermark-source-tree").hexdigest()
ATTESTED_ALIAS = "company-002-document-00053.pdf"
RUNTIME_UNSEEN_ALIAS = "company-002-document-00054.pdf"
ATTESTED_CONTENT_SHA256 = hashlib.sha256(b"fictional-attested-watermark-page").hexdigest()
RUNTIME_UNSEEN_CONTENT_SHA256 = hashlib.sha256(
    b"fictional-runtime-unseen-watermark-page"
).hexdigest()
POLICY_ID = "exemplar-watermark-page-v1"
POLICY_SHA256 = hashlib.sha256(b"fictional-watermark-policy-v1").hexdigest()
EXEMPLAR_ENVELOPE_SHA256 = hashlib.sha256(b"fictional-exemplar-envelope").hexdigest()
EXEMPLAR_BITMAP_SHA256 = hashlib.sha256(b"fictional-packed-exemplar-bitmap").hexdigest()
MANIFEST_SHA256 = hashlib.sha256(b"fictional-manifest-v2").hexdigest()
BATCH_ENVELOPE_SHA256 = hashlib.sha256(b"fictional-watermark-batch-envelope").hexdigest()
CANDIDATE_SET_SHA256 = hashlib.sha256(b"fictional-watermark-candidate-set").hexdigest()
PUBLIC_KEY_SHA256 = hashlib.sha256(b"fictional-public-key").hexdigest()
OCR_TERMINAL_PROOF_SHA256 = hashlib.sha256(b"fictional-complete-ocr-proof").hexdigest()
NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


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


def _runtime_module() -> ModuleType:
    return importlib.import_module("idis.services.ingestion.no_substantive_content_attestation")


def _required_api(name: str) -> Any:
    value = getattr(_runtime_module(), name, None)
    if value is None:
        pytest.fail(f"{RUNTIME_API_MISSING}: {_runtime_module().__name__}.{name}", pytrace=False)
    return value


def _classification(
    *,
    batch_membership: str = "ATTESTED",
    page_number: int = 1,
    safe_alias: str | None = None,
    content_sha256: str | None = None,
) -> dict[str, Any]:
    alias = safe_alias or (
        ATTESTED_ALIAS if batch_membership == "ATTESTED" else RUNTIME_UNSEEN_ALIAS
    )
    content_hash = content_sha256 or (
        ATTESTED_CONTENT_SHA256 if batch_membership == "ATTESTED" else RUNTIME_UNSEEN_CONTENT_SHA256
    )
    runtime_unseen = batch_membership == "RUNTIME_UNSEEN"
    return {
        "schema_version": "watermark-page-classification-v1",
        "classification": "watermark_only",
        "content_sha256": content_hash,
        "page_number": page_number,
        "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
        "exemplar_reference": {
            "exemplar_id": f"{ATTESTED_ALIAS}:page-1",
            "content_sha256": ATTESTED_CONTENT_SHA256,
            "page_number": 1,
            "attestation_envelope_sha256": EXEMPLAR_ENVELOPE_SHA256,
            "canonical_bitmap_sha256": EXEMPLAR_BITMAP_SHA256,
            "classifier_policy_sha256": POLICY_SHA256,
        },
        "transform_chain": [
            "poppler-200dpi",
            "convert-grayscale-L",
            "measure-near-white-ge-245",
            "autocontrast-cutoff-0",
            "measure-foreground-lt-224",
            "resize-grayscale-128x128-LANCZOS",
            "binary-foreground-lt-224",
            "foreground-dice",
        ],
        "transform_bounds": {
            "near_white_fraction_min": "0.970000",
            "near_white_fraction_max": "0.985000",
            "foreground_ink_fraction_min": "0.015000",
            "foreground_ink_fraction_max": "0.030000",
            "similarity_min": "0.800000",
        },
        "confidence_distribution": {
            "near_white_fraction": "0.978048",
            "foreground_ink_fraction": "0.022133",
        },
        "similarity_metric": "foreground_dice",
        "similarity_score": "0.819404",
        "similarity_numerator": 13_424,
        "similarity_denominator": 16_383,
        "canonical_render_sha256": hashlib.sha256(
            f"fictional-canonical-render:{alias}:{page_number}".encode()
        ).hexdigest(),
        "ocr_terminal_proof_sha256": OCR_TERMINAL_PROOF_SHA256,
        "batch_membership": batch_membership,
        "confirmation_source": (
            "signed_exemplar_runtime_match" if runtime_unseen else "reviewer_batch"
        ),
        "post_run_review_required": runtime_unseen,
        "review_status": (
            "WATERMARK_RUNTIME_CANDIDATE_REVIEW_REQUIRED" if runtime_unseen else "BATCH_ATTESTED"
        ),
    }


def _manifest_summary(*, include_runtime_unseen: bool = False) -> dict[str, Any]:
    candidates = [
        {
            "safe_alias": ATTESTED_ALIAS,
            "content_sha256": ATTESTED_CONTENT_SHA256,
            "page_number": 1,
            "canonical_render_sha256": _classification()["canonical_render_sha256"],
            "similarity_score": "0.819404",
        }
    ]
    if include_runtime_unseen:
        candidates.append(
            {
                "safe_alias": RUNTIME_UNSEEN_ALIAS,
                "content_sha256": RUNTIME_UNSEEN_CONTENT_SHA256,
                "page_number": 1,
                "canonical_render_sha256": _classification(batch_membership="RUNTIME_UNSEEN")[
                    "canonical_render_sha256"
                ],
                "similarity_score": "0.819404",
            }
        )
    return {
        "schema_version": "document-no-substantive-content-attestation-manifest-v2",
        "status": "verified",
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "classifier_policy_id": POLICY_ID,
        "classifier_policy_sha256": POLICY_SHA256,
        "exemplar_safe_alias": ATTESTED_ALIAS,
        "exemplar_page_number": 1,
        "exemplar_content_sha256": ATTESTED_CONTENT_SHA256,
        "exemplar_envelope_sha256": EXEMPLAR_ENVELOPE_SHA256,
        "exemplar_canonical_bitmap_sha256": EXEMPLAR_BITMAP_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "batch_envelope_sha256": BATCH_ENVELOPE_SHA256,
        "candidate_set_sha256": CANDIDATE_SET_SHA256,
        "public_key_sha256": PUBLIC_KEY_SHA256,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "company_file_counts": {"company-001": 58, "company-002": 209},
        "signature_verified": True,
    }


def _receipt_v2(
    *,
    batch_membership: str = "ATTESTED",
    whole_document_terminal: bool = True,
    classifications: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = classifications or [_classification(batch_membership=batch_membership)]
    first = rows[0]
    substantive_span_count = 0 if whole_document_terminal else 1
    receipt = {
        "schema_version": "document-no-substantive-content-receipt-v2",
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "safe_alias": (ATTESTED_ALIAS if batch_membership == "ATTESTED" else RUNTIME_UNSEEN_ALIAS),
        "content_sha256": first["content_sha256"],
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "manifest_schema_version": "document-no-substantive-content-attestation-manifest-v2",
        "manifest_sha256": MANIFEST_SHA256,
        "batch_envelope_sha256": BATCH_ENVELOPE_SHA256,
        "candidate_set_sha256": CANDIDATE_SET_SHA256,
        "classifier_policy_id": POLICY_ID,
        "classifier_policy_sha256": POLICY_SHA256,
        "exemplar_envelope_sha256": EXEMPLAR_ENVELOPE_SHA256,
        "exemplar_canonical_bitmap_sha256": EXEMPLAR_BITMAP_SHA256,
        "public_key_sha256": PUBLIC_KEY_SHA256,
        "signature_verified": True,
        "page_count": 1 if whole_document_terminal else 2,
        "classified_page_count": len(rows),
        "substantive_span_count": substantive_span_count,
        "whole_document_terminal": whole_document_terminal,
        "watermark_page_classifications": rows,
        "watermark_page_classifications_sha256": _digest(rows),
    }
    return receipt


def _validate_v2(
    receipt: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
) -> None:
    validator = _required_api("validate_document_no_substantive_content_receipt_v2")
    validator(
        receipt=receipt,
        verified_manifest=manifest or _manifest_summary(),
        tenant_id=TENANT_ID,
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        safe_alias=receipt["safe_alias"],
        content_sha256=receipt["content_sha256"],
        now=NOW,
    )


def _durable_row(receipt: dict[str, Any], *, parse_status: str) -> dict[str, Any]:
    return {
        "doc_id": ARTIFACT_ID,
        "document_id": DOCUMENT_ID,
        "deal_id": DEAL_ID,
        "artifact_doc_type": "DATA_ROOM_FILE",
        "doc_type": "PDF",
        "document_name": receipt["safe_alias"],
        "created_at": "2026-07-17T12:00:00Z",
        "parse_status": parse_status,
        "sha256": receipt["content_sha256"],
        "uri": "tenant-safe/object.pdf",
        "source_metadata": {
            "source_system": "slice100",
            "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
            "upload_filename": receipt["safe_alias"],
        },
        "metadata": {"document_no_substantive_content_receipt": receipt},
    }


def test_v2_constants_and_exact_attested_page_provenance_validate() -> None:
    assert _required_api("NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION") == (
        "document-no-substantive-content-receipt-v2"
    )
    assert _required_api("WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION") == (
        "watermark-page-classification-v1"
    )
    assert _required_api("WATERMARK_PAGE_SANITY_CAP_EXCEEDED") == (
        "WATERMARK_PAGE_SANITY_CAP_EXCEEDED"
    )
    receipt = _receipt_v2()

    _validate_v2(receipt)

    page = receipt["watermark_page_classifications"][0]
    assert page["batch_membership"] == "ATTESTED"
    assert page["confirmation_source"] == "reviewer_batch"
    assert page["post_run_review_required"] is False
    assert page["review_status"] == "BATCH_ATTESTED"
    assert page["similarity_score"] == "0.819404"
    assert page["transform_bounds"]["similarity_min"] == "0.800000"
    assert not ({"text", "text_excerpt", "spans", "signature"} & set(page))


@pytest.mark.parametrize(
    ("exemplar_id", "exemplar_page_number"),
    [
        pytest.param(
            "company-001-document-00001.pdf:page-1",
            1,
            id="different-valid-safe-alias",
        ),
        pytest.param(
            f"{ATTESTED_ALIAS}:page-2",
            2,
            id="different-valid-page",
        ),
    ],
)
def test_v2_exemplar_identity_is_bound_to_signed_manifest_not_shape_only(
    exemplar_id: str,
    exemplar_page_number: int,
) -> None:
    receipt = _receipt_v2()
    exemplar = receipt["watermark_page_classifications"][0]["exemplar_reference"]
    exemplar["exemplar_id"] = exemplar_id
    exemplar["page_number"] = exemplar_page_number
    receipt["watermark_page_classifications_sha256"] = _digest(
        receipt["watermark_page_classifications"]
    )
    error_type = _required_api("DocumentNoSubstantiveContentAttestationError")

    with pytest.raises(error_type) as caught:
        _validate_v2(receipt)

    assert caught.value.code == "WATERMARK_BATCH_ATTESTATION_MISMATCH"


def test_runtime_unseen_page_is_admitted_only_with_explicit_review_status() -> None:
    receipt = _receipt_v2(batch_membership="RUNTIME_UNSEEN")

    _validate_v2(receipt)

    page = receipt["watermark_page_classifications"][0]
    assert page["batch_membership"] == "RUNTIME_UNSEEN"
    assert page["confirmation_source"] == "signed_exemplar_runtime_match"
    assert page["post_run_review_required"] is True
    assert page["review_status"] == "WATERMARK_RUNTIME_CANDIDATE_REVIEW_REQUIRED"


@pytest.mark.parametrize(
    ("base_membership", "mutation", "manifest", "expected_code"),
    [
        (
            "RUNTIME_UNSEEN",
            {"batch_membership": "ATTESTED", "confirmation_source": "reviewer_batch"},
            _manifest_summary(include_runtime_unseen=False),
            "WATERMARK_BATCH_ATTESTATION_MISMATCH",
        ),
        (
            "RUNTIME_UNSEEN",
            {
                "batch_membership": "RUNTIME_UNSEEN",
                "confirmation_source": "signed_exemplar_runtime_match",
            },
            _manifest_summary(include_runtime_unseen=True),
            "WATERMARK_BATCH_ATTESTATION_MISMATCH",
        ),
        (
            "ATTESTED",
            {
                "batch_membership": "ATTESTED",
                "confirmation_source": "signed_exemplar_runtime_match",
            },
            _manifest_summary(),
            "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED",
        ),
        (
            "RUNTIME_UNSEEN",
            {
                "batch_membership": "RUNTIME_UNSEEN",
                "post_run_review_required": False,
            },
            _manifest_summary(),
            "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED",
        ),
    ],
)
def test_membership_confirmation_and_current_batch_are_cross_bound(
    base_membership: str,
    mutation: dict[str, object],
    manifest: dict[str, Any],
    expected_code: str,
) -> None:
    receipt = _receipt_v2(batch_membership=base_membership)
    receipt["watermark_page_classifications"][0].update(mutation)
    if base_membership == "RUNTIME_UNSEEN" and mutation.get("batch_membership") == "ATTESTED":
        receipt["safe_alias"] = RUNTIME_UNSEEN_ALIAS
        receipt["content_sha256"] = RUNTIME_UNSEEN_CONTENT_SHA256
    receipt["watermark_page_classifications_sha256"] = _digest(
        receipt["watermark_page_classifications"]
    )

    error_type = _required_api("DocumentNoSubstantiveContentAttestationError")
    with pytest.raises(error_type) as caught:
        _validate_v2(receipt, manifest=manifest)

    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"classified_page_count": 2}, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"),
        ({"substantive_span_count": 1}, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED"),
        ({"signature_verified": False}, "WATERMARK_BATCH_ATTESTATION_MISMATCH"),
    ],
)
def test_terminal_receipt_count_text_and_signature_contradictions_fail_closed(
    mutation: dict[str, object],
    expected_code: str,
) -> None:
    receipt = {**_receipt_v2(), **mutation}
    error_type = _required_api("DocumentNoSubstantiveContentAttestationError")

    with pytest.raises(error_type) as caught:
        _validate_v2(receipt)

    assert caught.value.code == expected_code


def test_duplicate_page_claim_is_not_reinterpreted_as_two_exclusions() -> None:
    page = _classification()
    receipt = _receipt_v2(classifications=[page, copy.deepcopy(page)])
    error_type = _required_api("DocumentNoSubstantiveContentAttestationError")

    with pytest.raises(error_type) as caught:
        _validate_v2(receipt)

    assert caught.value.code == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"


def test_safe_projector_accepts_exact_v2_and_rejects_private_material() -> None:
    projector = _required_api("project_safe_document_no_substantive_content_receipt")
    receipt = _receipt_v2()

    assert projector(receipt) == receipt

    for private_key in (
        "source_path",
        "private_filename",
        "raw_text",
        "signature_base64",
        "packed_exemplar_bitmap",
    ):
        poisoned = {**receipt, private_key: "private-canary@example.invalid"}
        assert projector(poisoned) is None


def test_mixed_document_stays_parsed_and_does_not_gain_document_terminal_disposition() -> None:
    receipt = _receipt_v2(whole_document_terminal=False)
    row = _durable_row(receipt, parse_status="PARSED")

    summary = _document_summary_from_durable_row(row)

    assert summary.parse_status == "PARSED"
    assert summary.terminal_disposition is None
    assert summary.metadata == {"document_no_substantive_content_receipt": receipt}
    assert receipt["classified_page_count"] == 1
    assert receipt["substantive_span_count"] == 1


def test_all_classified_document_remains_failed_zero_span_explicit_terminal() -> None:
    receipt = _receipt_v2(whole_document_terminal=True)
    row = _durable_row(receipt, parse_status="FAILED")

    summary = _document_summary_from_durable_row(row)

    assert summary.parse_status == "FAILED"
    assert summary.terminal_disposition == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
    assert summary.metadata == {"document_no_substantive_content_receipt": receipt}
    assert receipt["classified_page_count"] == receipt["page_count"]
    assert receipt["substantive_span_count"] == 0


def test_manual_v1_receipt_remains_projectable_without_becoming_batch_evidence() -> None:
    from tests.slice100_task4.test_no_substantive_content_security_red import (
        _terminal_receipt,
    )

    projector = _required_api("project_safe_document_no_substantive_content_receipt")
    receipt = _terminal_receipt()

    assert projector(receipt) == receipt
    assert receipt["schema_version"] == "document-no-substantive-content-receipt-v1"
    assert "batch_membership" not in receipt
    assert "watermark_page_classifications" not in receipt
