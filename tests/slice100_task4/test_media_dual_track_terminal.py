"""Task 4 RED contract for durable dual-track media receipt reconciliation.

The 267-row fixture is synthetic and carries only safe aliases and digests.  In
particular, the alternative positive-track layouts below prove that the real
source observation must never become a fixed acceptance split.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from idis.evaluation import real_example_terminal_reconciliation as reconciliation
from tests.slice100_task4.test_terminal_reconciliation import (
    EXPECTED_MEDIA_RUNTIME_PROVENANCE,
    _reconcile,
    _source_companies,
    _terminal_rows,
)

_SCHEDULE_MS = (0, 500, 1_500, 2_500, 2_999)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stt_evidence(spans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source": "media_transcript",
            "start_ms": span["locator"]["start_ms"],
            "end_ms": span["locator"]["end_ms"],
            "content_hash": span["content_hash"],
        }
        for span in spans
        if span["locator"].get("source") == "media_transcript"
    ]


def _frame_evidence(spans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source": "video_frame_ocr",
            "sample_index": span["locator"]["sample_index"],
            "start_ms": span["locator"]["start_ms"],
            "end_ms": span["locator"]["end_ms"],
            "confidence": span["locator"]["confidence"],
            "content_hash": span["content_hash"],
        }
        for span in spans
        if span["locator"].get("source") == "video_frame_ocr"
    ]


def _stamp_dual_track_receipts(
    row: dict[str, Any],
    *,
    media_index: int,
    stt_positive: bool,
    frame_positive: bool,
) -> None:
    original_span = copy.deepcopy(row["durable_spans"][0])
    original_span["locator"] = {
        "source": "media_transcript",
        "start_ms": 0,
        "end_ms": 1_000,
    }
    spans: list[dict[str, Any]] = [original_span] if stt_positive else []
    if frame_positive:
        spans.append(
            {
                "span_id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"https://fictional.invalid/frame/{media_index}",
                    )
                ),
                "document_id": row["document_id"],
                "span_type": "TIMECODE",
                "locator": {
                    "source": "video_frame_ocr",
                    "sample_index": 1,
                    "start_ms": 500,
                    "end_ms": 501,
                    "confidence": 0.82,
                },
                "content_hash": hashlib.sha256(
                    f"fictional-frame-evidence-{media_index}".encode()
                ).hexdigest(),
            }
        )

    stt_evidence = _stt_evidence(spans)
    frame_evidence = _frame_evidence(spans)
    confidences = [float(item["confidence"]) for item in frame_evidence]
    runtime_sha256 = _canonical_sha256(EXPECTED_MEDIA_RUNTIME_PROVENANCE)
    row.update(
        {
            "reason_codes": ["media_evidence_completed"],
            "media_transcription_performed": True,
            "media_frame_ocr_performed": True,
            "durable_spans": spans,
            "media_stt_receipt": {
                "schema_version": "media-stt-receipt-v1",
                "status": "completed",
                "segment_count": len(stt_evidence),
                "evidence_span_count": len(stt_evidence),
                "evidence_sha256": _canonical_sha256(stt_evidence),
                "runtime_provenance_sha256": runtime_sha256,
            },
            "media_frame_ocr_receipt": {
                "schema_version": "media-frame-ocr-receipt-v1",
                "status": "completed",
                "sampling_policy": "periodic-plus-scenes-v1",
                "scene_threshold": 0.20,
                "max_sample_count": 600,
                "max_frame_width": 1_920,
                "max_frame_height": 1_080,
                "duration_ms": 3_000,
                "scheduled_sample_count": len(_SCHEDULE_MS),
                "completed_sample_count": len(_SCHEDULE_MS),
                "nonempty_sample_count": len(frame_evidence),
                "evidence_span_count": len(frame_evidence),
                "schedule_sha256": _canonical_sha256(list(_SCHEDULE_MS)),
                "evidence_sha256": _canonical_sha256(frame_evidence),
                "confidence_count": len(confidences),
                "mean_confidence": (
                    round(sum(confidences) / len(confidences), 4) if confidences else None
                ),
                "minimum_confidence": min(confidences) if confidences else None,
                "maximum_confidence": max(confidences) if confidences else None,
            },
        }
    )


def _dual_track_rows(
    layout: Callable[[int], tuple[bool, bool]],
) -> tuple[tuple[Any, Any], list[dict[str, Any]]]:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    media_index = 0
    for row in rows:
        if row["extension"] != "mp4":
            continue
        stt_positive, frame_positive = layout(media_index)
        _stamp_dual_track_receipts(
            row,
            media_index=media_index,
            stt_positive=stt_positive,
            frame_positive=frame_positive,
        )
        media_index += 1
    assert media_index == 8
    return companies, rows


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(lambda _index: (True, False), id="eight-stt-positive-zero-frame-positive"),
        pytest.param(lambda _index: (False, True), id="zero-stt-positive-eight-frame-positive"),
        pytest.param(lambda _index: (True, True), id="eight-positive-in-both-tracks"),
        pytest.param(lambda index: (index % 3 != 0, index % 3 == 0), id="arbitrary-safe-split"),
    ],
)
def test_terminal_acceptance_has_no_fixed_positive_track_split(
    layout: Callable[[int], tuple[bool, bool]],
) -> None:
    companies, rows = _dual_track_rows(layout)

    summary = _reconcile(companies, rows)

    assert summary["status"] == "ready"


def test_legacy_stt_only_completed_looking_rows_are_rejected() -> None:
    companies = _source_companies()
    legacy_rows = _terminal_rows(companies)

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile(companies, legacy_rows)

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_both_empty_receipts_fail_with_exact_media_no_extractable_evidence_code() -> None:
    companies, rows = _dual_track_rows(lambda _index: (True, False))
    media_row = next(row for row in rows if row["extension"] == "mp4")
    _stamp_dual_track_receipts(
        media_row,
        media_index=0,
        stt_positive=False,
        frame_positive=False,
    )

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile(companies, rows)

    assert caught.value.code == "MEDIA_NO_EXTRACTABLE_EVIDENCE"


@pytest.mark.parametrize("receipt_key", ["media_stt_receipt", "media_frame_ocr_receipt"])
def test_terminal_reconciliation_requires_both_receipts(receipt_key: str) -> None:
    companies, rows = _dual_track_rows(lambda _index: (True, True))
    media_row = next(row for row in rows if row["extension"] == "mp4")
    media_row.pop(receipt_key)

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile(companies, rows)

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize(
    ("receipt_key", "field", "replacement"),
    [
        ("media_stt_receipt", "status", "partial"),
        ("media_stt_receipt", "evidence_span_count", 99),
        ("media_stt_receipt", "evidence_sha256", "0" * 64),
        ("media_frame_ocr_receipt", "status", "partial"),
        ("media_frame_ocr_receipt", "completed_sample_count", 4),
        ("media_frame_ocr_receipt", "nonempty_sample_count", 99),
        ("media_frame_ocr_receipt", "evidence_span_count", 99),
        ("media_frame_ocr_receipt", "evidence_sha256", "0" * 64),
        ("media_frame_ocr_receipt", "schedule_sha256", "not-a-digest"),
        ("media_frame_ocr_receipt", "sampling_policy", "fictional-policy"),
    ],
)
def test_terminal_rederives_receipt_counts_and_evidence_hashes(
    receipt_key: str,
    field: str,
    replacement: object,
) -> None:
    companies, rows = _dual_track_rows(lambda _index: (True, True))
    media_row = next(row for row in rows if row["extension"] == "mp4")
    receipt = media_row[receipt_key]
    assert isinstance(receipt, dict)
    receipt[field] = replacement

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile(companies, rows)

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_terminal_checks_monotonicity_per_track_and_allows_cross_track_overlap() -> None:
    companies, rows = _dual_track_rows(lambda _index: (True, True))

    summary = _reconcile(companies, rows)

    assert summary["status"] == "ready"


def test_overlapping_timecodes_within_frame_track_are_rejected() -> None:
    companies, rows = _dual_track_rows(lambda _index: (False, True))
    media_row = next(row for row in rows if row["extension"] == "mp4")
    media_row["durable_spans"].append(
        {
            "span_id": str(uuid.uuid4()),
            "document_id": media_row["document_id"],
            "span_type": "TIMECODE",
            "locator": {
                "source": "video_frame_ocr",
                "sample_index": 2,
                "start_ms": 500,
                "end_ms": 502,
                "confidence": 0.75,
            },
            "content_hash": hashlib.sha256(b"fictional-overlap").hexdigest(),
        }
    )
    frame_evidence = _frame_evidence(media_row["durable_spans"])
    frame_receipt = media_row["media_frame_ocr_receipt"]
    frame_receipt["nonempty_sample_count"] = len(frame_evidence)
    frame_receipt["evidence_span_count"] = len(frame_evidence)
    frame_receipt["evidence_sha256"] = _canonical_sha256(frame_evidence)
    frame_receipt["confidence_count"] = len(frame_evidence)
    frame_receipt["mean_confidence"] = 0.785
    frame_receipt["minimum_confidence"] = 0.75
    frame_receipt["maximum_confidence"] = 0.82

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile(companies, rows)

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE
