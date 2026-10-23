"""RED-first durable reduction for successful ``ocr_rescue`` spans."""

from __future__ import annotations

import hashlib
import json

import pytest

from idis.evaluation.real_example_terminal_reconciliation import (
    TerminalIngestionReconciliationError,
    _safe_durable_span_receipts,
)
from idis.parsers.ocr import OCR_RESCUE_POLICY_SHA256

DOCUMENT_ID = "11111111-1111-1111-1111-111111111111"


def _provenance() -> dict[str, object]:
    return {
        "policy_version": "pdf-ocr-rescue-v1",
        "policy_sha256": OCR_RESCUE_POLICY_SHA256,
        "winner_rotation_degrees": 0,
        "winner_transform_id": "autocontrast-c0",
        "winner_psm": 6,
        "winner_word_count": 5,
        "winner_mean_confidence": 0.95,
        "winner_median_confidence": 0.95,
        "winner_minimum_confidence": 0.91,
        "winner_maximum_confidence": 0.98,
        "winner_confidence_histogram_counts": {
            "below_0_50": 0,
            "from_0_50_to_below_0_60": 0,
            "from_0_60_to_below_0_80": 0,
            "at_or_above_0_80": 5,
        },
        "winner_confidence_values_sha256": "a" * 64,
        "winner_fraction_at_or_above_0_50": 1.0,
        "attempted_candidate_count": 24,
        "supporting_candidate_count": 4,
        "minimum_clique_agreement": 0.9,
        "mean_clique_agreement": 0.95,
        "candidate_receipt_sha256": "b" * 64,
        "normalized_page_text_sha256": "c" * 64,
    }


def _span_page() -> dict[str, object]:
    text = "project owned faint evidence"
    return {
        "total": 1,
        "items": [
            {
                "span_id": "22222222-2222-2222-2222-222222222222",
                "document_id": DOCUMENT_ID,
                "span_type": "PAGE_TEXT",
                "text_excerpt": text,
                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                "locator": {
                    "page": 1,
                    "line": 1,
                    "source": "ocr_rescue",
                    **_provenance(),
                },
            }
        ],
    }


def test_rescued_span_produces_distinct_page_span_and_receipt_census() -> None:
    spans, proof = _safe_durable_span_receipts(
        _span_page(),
        document_id=DOCUMENT_ID,
        is_media=False,
        extension="pdf",
        ocr_performed=True,
    )

    assert len(spans) == 1
    assert proof is not None
    assert proof["ocr_page_count"] == 1
    assert proof["ocr_span_count"] == 1
    assert proof["ocr_rescue_page_count"] == 1
    assert proof["ocr_rescue_span_count"] == 1
    expected_receipt = [{"page": 1, **_provenance()}]
    expected_digest = hashlib.sha256(
        json.dumps(
            expected_receipt,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert proof["ocr_rescue_receipt_sha256"] == expected_digest


def test_rescued_span_with_drifted_policy_hash_fails_closed() -> None:
    page = _span_page()
    page["items"][0]["locator"]["policy_sha256"] = "f" * 64  # type: ignore[index]

    with pytest.raises(TerminalIngestionReconciliationError):
        _safe_durable_span_receipts(
            page,
            document_id=DOCUMENT_ID,
            is_media=False,
            extension="pdf",
            ocr_performed=True,
        )
