"""Task 4 RED contract for restart-safe dual-track media preflight.

The fixtures are fictional durable rows.  They prove that a legacy STT flag is
not sufficient and that either completed evidence track may be the non-empty
one without turning the other required receipt into a deferral.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from idis.models.document_preflight import DocumentPreflightStatus
from idis.services.runs.document_preflight import InMemoryRunDocumentPreflightService

_TENANT_ID = "11111111-1111-4111-8111-111111111111"
_DEAL_ID = "22222222-2222-4222-8222-222222222222"
_RUN_ID = "33333333-3333-4333-8333-333333333333"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document(*, stt_positive: bool, frame_positive: bool) -> dict[str, Any]:
    document_id = "44444444-4444-4444-8444-444444444444"
    spans: list[dict[str, Any]] = []
    stt_evidence: list[dict[str, Any]] = []
    frame_evidence: list[dict[str, Any]] = []
    if stt_positive:
        content_hash = hashlib.sha256(b"fictional spoken evidence").hexdigest()
        locator = {"source": "media_transcript", "start_ms": 0, "end_ms": 900}
        spans.append(
            {
                "span_id": "55555555-5555-4555-8555-555555555555",
                "tenant_id": _TENANT_ID,
                "deal_id": _DEAL_ID,
                "document_id": document_id,
                "span_type": "TIMECODE",
                "locator": locator,
                "text_excerpt": "fictional spoken evidence",
                "content_hash": content_hash,
            }
        )
        stt_evidence.append({**locator, "content_hash": content_hash})
    if frame_positive:
        content_hash = hashlib.sha256(b"fictional visual evidence").hexdigest()
        locator = {
            "source": "video_frame_ocr",
            "sample_index": 1,
            "start_ms": 500,
            "end_ms": 501,
            "confidence": 0.82,
        }
        spans.append(
            {
                "span_id": "66666666-6666-4666-8666-666666666666",
                "tenant_id": _TENANT_ID,
                "deal_id": _DEAL_ID,
                "document_id": document_id,
                "span_type": "TIMECODE",
                "locator": locator,
                "text_excerpt": "fictional visual evidence",
                "content_hash": content_hash,
            }
        )
        frame_evidence.append({**locator, "content_hash": content_hash})
    schedule = [0, 500, 1_500, 2_500, 2_999]
    metadata = {
        "parser_doc_type": "MEDIA",
        "detected_format": "MEDIA",
        "parser_runtime_status": "completed",
        "span_count": len(spans),
        "parse_error_codes": [],
        "parse_warning_codes": [],
        "media_transcription_performed": True,
        "media_frame_ocr_performed": True,
        "media_stt_receipt": {
            "schema_version": "media-stt-receipt-v1",
            "status": "completed",
            "segment_count": len(stt_evidence),
            "evidence_span_count": len(stt_evidence),
            "evidence_sha256": _digest(stt_evidence),
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
            "scheduled_sample_count": len(schedule),
            "completed_sample_count": len(schedule),
            "nonempty_sample_count": len(frame_evidence),
            "evidence_span_count": len(frame_evidence),
            "schedule_sha256": _digest(schedule),
            "evidence_sha256": _digest(frame_evidence),
            "confidence_count": len(frame_evidence),
            "mean_confidence": 0.82 if frame_evidence else None,
            "minimum_confidence": 0.82 if frame_evidence else None,
            "maximum_confidence": 0.82 if frame_evidence else None,
        },
    }
    return {
        "tenant_id": _TENANT_ID,
        "deal_id": _DEAL_ID,
        "document_id": document_id,
        "doc_type": "VIDEO",
        "parse_status": "PARSED",
        "document_name": "fictional-video.mp4",
        "metadata": metadata,
        "spans": spans,
    }


def _run(document: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    return InMemoryRunDocumentPreflightService().run(
        tenant_id=_TENANT_ID,
        deal_id=_DEAL_ID,
        run_id=_RUN_ID,
        corpus=[document],
    )


def test_restart_preflight_accepts_stt_positive_frame_empty() -> None:
    result, eligible = _run(_document(stt_positive=True, frame_positive=False))

    assert result.status == DocumentPreflightStatus.COMPLETED
    # Classification may add an orthogonal descriptor code; the parser proof is the invariant.
    assert "media_evidence_completed" in result.decisions[0].reason_codes
    assert len(eligible) == 1


def test_restart_preflight_accepts_stt_empty_frame_positive() -> None:
    result, eligible = _run(_document(stt_positive=False, frame_positive=True))

    assert result.status == DocumentPreflightStatus.COMPLETED
    # Classification may add an orthogonal descriptor code; the parser proof is the invariant.
    assert "media_evidence_completed" in result.decisions[0].reason_codes
    assert len(eligible) == 1


def test_restart_preflight_rejects_legacy_stt_only_metadata() -> None:
    document = _document(stt_positive=True, frame_positive=False)
    metadata = document["metadata"]
    assert isinstance(metadata, dict)
    metadata.pop("media_frame_ocr_performed")
    metadata.pop("media_stt_receipt")
    metadata.pop("media_frame_ocr_receipt")

    result, eligible = _run(document)

    assert result.status == DocumentPreflightStatus.FAILED
    assert eligible == []
    assert "media_evidence_completed" not in result.decisions[0].reason_codes


def test_restart_preflight_rejects_receipt_span_count_drift() -> None:
    document = _document(stt_positive=True, frame_positive=True)
    metadata = document["metadata"]
    assert isinstance(metadata, dict)
    receipt = metadata["media_frame_ocr_receipt"]
    assert isinstance(receipt, dict)
    receipt["evidence_span_count"] = 9

    result, eligible = _run(document)

    assert result.status == DocumentPreflightStatus.FAILED
    assert eligible == []
