"""Contracts for the reviewer-resolved prescan lineage and image-only page set."""

from __future__ import annotations

import copy
import hashlib

import pytest

from tests.slice100_task4.test_watermark_batch_attestation_red import (
    ACTIVE_NOW,
    APPROVED_AT,
    EXPIRES_AT,
    SIGNER_ID,
    _code,
    _error_type,
    _manifest,
    _prescan_receipt,
    _private_and_public_pem,
    _sha256,
    _verify,
)


def _image_only_pages() -> list[dict[str, object]]:
    pages: list[dict[str, object]] = []
    for index, (sequence, page_number) in enumerate(
        ((105, 4), (105, 7), (109, 54), (110, 60), (113, 4), (113, 27), (113, 51), (122, 2)),
        start=1,
    ):
        pages.append(
            {
                "safe_alias": f"company-002-document-{sequence:05d}.pdf",
                "content_sha256": f"{index:x}" * 64,
                "page_number": page_number,
                "page_content_sha256": hashlib.sha256(
                    f"fictional-reviewed-page:{sequence}:{page_number}".encode()
                ).hexdigest(),
                "ocr_terminal_proof_sha256": hashlib.sha256(
                    f"fictional-ocr-proof:{sequence}:{page_number}".encode()
                ).hexdigest(),
                "watermark_classifier_receipt_sha256": hashlib.sha256(
                    f"fictional-watermark-proof:{sequence}:{page_number}".encode()
                ).hexdigest(),
                "reviewer_disposition": "SUBSTANTIVE_IMAGE",
            }
        )
    return pages


def _resolved_prescan() -> dict[str, object]:
    receipt = _prescan_receipt(status="COMPLETE", unresolved_count=0)
    pages = _image_only_pages()
    receipt.update(
        {
            "prior_blocked_prescan_receipt_sha256": "b" * 64,
            "prior_blocked_prescan_file_sha256": "c" * 64,
            "prior_unresolved_set_sha256": "d" * 64,
            "reviewed_image_only_page_count": len(pages),
            "reviewed_image_only_pages": pages,
            "reviewed_image_only_set_sha256": _sha256(pages),
            "reviewer_approval": {
                "decision": "APPROVED",
                "reviewer_id": "fictional-reviewer-v1",
                "approved_at": APPROVED_AT,
                "expires_at": EXPIRES_AT,
            },
        }
    )
    return receipt


def test_resolved_prescan_is_cross_bound_and_exposes_only_safe_image_page_records() -> None:
    private_pem, public_pem = _private_and_public_pem()
    resolved = _resolved_prescan()
    manifest = _manifest(private_pem, prescan_receipt=resolved)

    verified = _verify(manifest, public_pem=public_pem, now=ACTIVE_NOW)

    assert verified.batch.prescan_receipt_sha256 == _sha256(resolved)
    assert tuple(verified.reviewed_image_only_pages) == tuple(_image_only_pages())
    assert verified.reviewed_image_only_set_sha256 == _sha256(_image_only_pages())
    summary = verified.safe_summary()
    assert summary["reviewed_image_only_page_count"] == 8
    assert summary["reviewed_image_only_set_sha256"] == _sha256(_image_only_pages())
    assert "reviewed_image_only_pages" not in summary


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt["reviewed_image_only_pages"].pop(),
        lambda receipt: receipt["reviewed_image_only_pages"].append(
            copy.deepcopy(receipt["reviewed_image_only_pages"][0])
        ),
        lambda receipt: receipt["reviewed_image_only_pages"][0].__setitem__(
            "reviewer_disposition", "WATERMARK_ONLY"
        ),
        lambda receipt: receipt["reviewed_image_only_pages"][0].__setitem__(
            "page_content_sha256", "not-a-digest"
        ),
    ],
)
def test_resolved_prescan_mutation_fails_closed(mutation: object) -> None:
    private_pem, public_pem = _private_and_public_pem()
    resolved = _resolved_prescan()
    assert callable(mutation)
    mutation(resolved)
    # Recompute the outer digest/signature through the fixture: failures below
    # therefore prove resolution admission, not a stale signature.
    manifest = _manifest(private_pem, prescan_receipt=resolved)

    with pytest.raises(_error_type()) as caught:
        _verify(manifest, public_pem=public_pem, trusted_signer_id=SIGNER_ID)
    assert _code(caught.value) in {
        "WATERMARK_BATCH_ATTESTATION_INVALID",
        "WATERMARK_BATCH_ATTESTATION_MISMATCH",
    }
