"""Task 4 RED contract for required STT plus deterministic frame-OCR evidence.

All media, model, transcript, and OCR values in this module are fictional.  The
tests use the existing injected worker boundary; they never invoke ffmpeg,
faster-whisper, Tesseract, a network provider, or the real data room.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from idis.parsers import media
from idis.parsers.base import ParseErrorCode
from idis.parsers.media import (
    FasterWhisperMediaAdapter,
    FasterWhisperMediaConfig,
    MediaConfig,
    parse_media,
)

_POLICY = "periodic-plus-scenes-v1"
_SCENE_THRESHOLD = 0.20
_MAX_SAMPLES = 600
_MAX_WIDTH = 1_920
_MAX_HEIGHT = 1_080
_DURATION_MS = 3_000
_SCHEDULE_MS = (0, 500, 1_500, 2_500, 2_999)


class _FakeMonotonicClock:
    def __init__(self) -> None:
        self._now = 100.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _stt_evidence(segments: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "source": "media_transcript",
            "start_ms": segment["start_ms"],
            "end_ms": segment["end_ms"],
            "content_hash": _text_sha256(str(segment["text"])),
        }
        for segment in segments
        if str(segment["text"]).strip()
    ]


def _frame_evidence(segments: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "source": "video_frame_ocr",
            "sample_index": segment["sample_index"],
            "start_ms": segment["start_ms"],
            "end_ms": segment["end_ms"],
            "confidence": segment["confidence"],
            "content_hash": _text_sha256(str(segment["text"])),
        }
        for segment in segments
        if str(segment["text"]).strip()
    ]


def _worker_payload(
    *,
    stt_positive: bool,
    frame_positive: bool,
) -> dict[str, object]:
    stt_segments: list[dict[str, object]] = []
    if stt_positive:
        stt_segments.append(
            {
                "start_ms": 0,
                "end_ms": 900,
                "text": "fictional spoken evidence",
            }
        )
    frame_segments: list[dict[str, object]] = []
    if frame_positive:
        frame_segments.append(
            {
                "sample_index": 1,
                "start_ms": 500,
                "end_ms": 501,
                "text": "fictional on-screen evidence",
                "confidence": 0.82,
            }
        )
    stt_evidence = _stt_evidence(stt_segments)
    frame_evidence = _frame_evidence(frame_segments)
    confidence_values = [float(item["confidence"]) for item in frame_segments]
    return {
        "status": "success",
        "duration_ms": _DURATION_MS,
        "segments": stt_segments,
        "frame_sample_ms": list(_SCHEDULE_MS),
        "frame_ocr_segments": frame_segments,
        "media_stt_receipt": {
            "schema_version": "media-stt-receipt-v1",
            "status": "completed",
            "segment_count": len(stt_segments),
            "evidence_span_count": len(stt_evidence),
            "evidence_sha256": _canonical_sha256(stt_evidence),
        },
        "media_frame_ocr_receipt": {
            "schema_version": "media-frame-ocr-receipt-v1",
            "status": "completed",
            "sampling_policy": _POLICY,
            "scene_threshold": _SCENE_THRESHOLD,
            "max_sample_count": _MAX_SAMPLES,
            "max_frame_width": _MAX_WIDTH,
            "max_frame_height": _MAX_HEIGHT,
            "duration_ms": _DURATION_MS,
            "scheduled_sample_count": len(_SCHEDULE_MS),
            "completed_sample_count": len(_SCHEDULE_MS),
            "nonempty_sample_count": len(frame_evidence),
            "evidence_span_count": len(frame_evidence),
            "schedule_sha256": _canonical_sha256(list(_SCHEDULE_MS)),
            "evidence_sha256": _canonical_sha256(frame_evidence),
            "confidence_count": len(confidence_values),
            "mean_confidence": (
                round(sum(confidence_values) / len(confidence_values), 4)
                if confidence_values
                else None
            ),
            "minimum_confidence": min(confidence_values) if confidence_values else None,
            "maximum_confidence": max(confidence_values) if confidence_values else None,
        },
    }


def _model_dir(tmp_path: Path) -> Path:
    model = tmp_path / "fictional-local-model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.bin").write_bytes(b"fictional-model")
    return model


def _parse_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    *,
    clock: _FakeMonotonicClock | None = None,
    worker_runner: Callable[[Callable[..., None], tuple[object, ...], float], dict[str, object]]
    | None = None,
    timeout_seconds: float = 10.0,
) -> Any:
    monkeypatch.setattr(media.importlib.metadata, "version", lambda _name: "1.2.3")
    adapter = FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_path=str(_model_dir(tmp_path)),
            allow_model_download=False,
            max_duration_seconds=300,
        ),
        binary_resolver=lambda _name: "fictional-binary",
        duration_probe=lambda _source, _config, _remaining: _DURATION_MS / 1_000,
        model_fingerprinter=lambda _path, _remaining: hashlib.sha256(
            b"fictional-model"
        ).hexdigest(),
        worker_runner=worker_runner or (lambda _target, _args, _remaining: payload),
        monotonic_clock=clock,
    )
    return parse_media(
        b"fictional-private-media",
        media_config=MediaConfig(
            enabled=True,
            adapter=adapter,
            timeout_seconds=timeout_seconds,
        ),
    )


def _frame_module() -> Any:
    try:
        return importlib.import_module("idis.parsers.video_frame_ocr")
    except ModuleNotFoundError:
        pytest.fail("Task 4 deterministic video frame-OCR module is not implemented", pytrace=False)


def _single_error_code(result: Any) -> ParseErrorCode:
    assert result.success is False
    assert result.spans == []
    assert result.metadata == {}
    assert len(result.errors) == 1
    return result.errors[0].code


def test_periodic_plus_scenes_schedule_is_sorted_deduplicated_and_clamped() -> None:
    module = _frame_module()
    build = getattr(module, "build_periodic_plus_scenes_schedule", None)
    assert callable(build), "Task 4 deterministic frame sampling is not implemented"

    schedule = build(
        duration_ms=3_000,
        scene_transition_ms=(-20, 500, 1_250, 1_250, 3_500),
    )

    assert tuple(schedule) == (0, 500, 1_250, 1_500, 2_500, 2_999)
    assert tuple(schedule) == tuple(sorted(set(schedule)))


def test_sampling_policy_uses_plan_pinned_threshold_sample_and_frame_caps() -> None:
    module = _frame_module()

    assert module.FRAME_SAMPLING_POLICY == _POLICY
    assert module.FRAME_SCENE_THRESHOLD == _SCENE_THRESHOLD
    assert module.MAX_FRAME_SAMPLES == _MAX_SAMPLES
    assert module.MAX_FRAME_WIDTH == _MAX_WIDTH
    assert module.MAX_FRAME_HEIGHT == _MAX_HEIGHT


def test_sampling_never_truncates_when_more_than_600_candidates_exist() -> None:
    module = _frame_module()
    build = getattr(module, "build_periodic_plus_scenes_schedule", None)
    assert callable(build), "Task 4 deterministic frame sampling is not implemented"

    with pytest.raises(Exception) as caught:  # noqa: B017 - public code is the contract
        build(
            duration_ms=300_000,
            scene_transition_ms=tuple(range(1, 1_203, 2)),
        )

    assert getattr(caught.value, "code", None) == "MEDIA_FRAME_SAMPLE_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    ("stt_positive", "frame_positive", "expected_sources"),
    [
        (True, False, ["media_transcript"]),
        (False, True, ["video_frame_ocr"]),
        (True, True, ["media_transcript", "video_frame_ocr"]),
    ],
)
def test_success_requires_both_completed_receipts_and_only_one_nonempty_track(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stt_positive: bool,
    frame_positive: bool,
    expected_sources: list[str],
) -> None:
    result = _parse_payload(
        tmp_path,
        monkeypatch,
        _worker_payload(stt_positive=stt_positive, frame_positive=frame_positive),
    )

    assert result.success is True
    assert [span.locator["source"] for span in result.spans] == expected_sources
    assert result.metadata["media_transcription_performed"] is True
    assert result.metadata["media_frame_ocr_performed"] is True
    assert result.metadata["media_stt_receipt"]["schema_version"] == "media-stt-receipt-v1"
    assert result.metadata["media_stt_receipt"]["status"] == "completed"
    assert (
        result.metadata["media_frame_ocr_receipt"]["schema_version"] == "media-frame-ocr-receipt-v1"
    )
    assert result.metadata["media_frame_ocr_receipt"]["status"] == "completed"
    assert result.metadata["media_frame_ocr_receipt"]["sampling_policy"] == _POLICY
    assert result.metadata["media_frame_ocr_receipt"]["scene_threshold"] == _SCENE_THRESHOLD
    assert result.metadata["media_frame_ocr_receipt"]["max_sample_count"] == _MAX_SAMPLES


def test_both_empty_tracks_fail_with_exact_media_no_extractable_evidence_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _parse_payload(
        tmp_path,
        monkeypatch,
        _worker_payload(stt_positive=False, frame_positive=False),
    )

    assert _single_error_code(result) is ParseErrorCode.MEDIA_NO_EXTRACTABLE_EVIDENCE
    assert result.errors[0].code.value == "media_no_extractable_evidence"


def test_legacy_stt_only_worker_payload_is_not_completed_looking_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "status": "success",
        "segments": [{"start_ms": 0, "end_ms": 900, "text": "legacy transcript must not clear"}],
    }

    result = _parse_payload(tmp_path, monkeypatch, payload)

    assert _single_error_code(result) is ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE


@pytest.mark.parametrize(
    ("receipt_key", "field", "replacement", "expected_code_name"),
    [
        ("media_stt_receipt", "evidence_span_count", 9, "MEDIA_TRANSCRIPTION_FAILED"),
        ("media_stt_receipt", "evidence_sha256", "0" * 64, "MEDIA_TRANSCRIPTION_FAILED"),
        ("media_frame_ocr_receipt", "completed_sample_count", 4, "MEDIA_FRAME_OCR_INCOMPLETE"),
        ("media_frame_ocr_receipt", "schedule_sha256", "0" * 64, "MEDIA_FRAME_OCR_INCOMPLETE"),
        ("media_frame_ocr_receipt", "evidence_span_count", 9, "MEDIA_FRAME_OCR_INCOMPLETE"),
        ("media_frame_ocr_receipt", "evidence_sha256", "0" * 64, "MEDIA_FRAME_OCR_INCOMPLETE"),
    ],
)
def test_worker_receipt_counts_and_hashes_are_rederived_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_key: str,
    field: str,
    replacement: object,
    expected_code_name: str,
) -> None:
    payload = _worker_payload(stt_positive=True, frame_positive=True)
    receipt = payload[receipt_key]
    assert isinstance(receipt, dict)
    receipt[field] = replacement

    result = _parse_payload(tmp_path, monkeypatch, payload)

    assert _single_error_code(result) is getattr(ParseErrorCode, expected_code_name)


@pytest.mark.parametrize(
    ("status", "expected_code_name"),
    [
        ("unavailable", "MEDIA_FRAME_OCR_UNAVAILABLE"),
        ("timeout", "MEDIA_FRAME_OCR_TIMEOUT"),
        ("failed", "MEDIA_FRAME_OCR_FAILED"),
        ("incomplete", "MEDIA_FRAME_OCR_INCOMPLETE"),
    ],
)
def test_frame_track_failure_cannot_be_masked_by_a_positive_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_code_name: str,
) -> None:
    payload = _worker_payload(stt_positive=True, frame_positive=False)
    frame_receipt = payload["media_frame_ocr_receipt"]
    assert isinstance(frame_receipt, dict)
    frame_receipt["status"] = status

    result = _parse_payload(tmp_path, monkeypatch, payload)

    assert _single_error_code(result) is getattr(ParseErrorCode, expected_code_name)


def test_stt_and_frame_work_share_one_aggregate_deadline_without_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeMonotonicClock()
    observed_worker_budget: list[float] = []
    payload = _worker_payload(stt_positive=True, frame_positive=True)

    def duration_probe(
        _source: object,
        _config: FasterWhisperMediaConfig,
        remaining_seconds: float,
    ) -> float:
        del remaining_seconds
        clock.advance(0.20)
        return _DURATION_MS / 1_000

    fingerprint_calls = 0

    def fingerprint(_path: str | Path, _remaining: float) -> str:
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        clock.advance(0.15)
        return hashlib.sha256(b"fictional-model").hexdigest()

    def dual_track_worker(
        _target: Callable[..., None],
        _args: tuple[object, ...],
        remaining_seconds: float,
    ) -> dict[str, object]:
        observed_worker_budget.append(remaining_seconds)
        clock.advance(0.35)  # STT phase
        clock.advance(0.35)  # frame scan/decode/OCR phase
        return payload

    monkeypatch.setattr(media.importlib.metadata, "version", lambda _name: "1.2.3")
    adapter = FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_path=str(_model_dir(tmp_path)),
            allow_model_download=False,
            max_duration_seconds=300,
        ),
        binary_resolver=lambda _name: "fictional-binary",
        duration_probe=duration_probe,
        model_fingerprinter=fingerprint,
        worker_runner=dual_track_worker,
        monotonic_clock=clock,
    )

    result = parse_media(
        b"fictional-private-media",
        media_config=MediaConfig(enabled=True, adapter=adapter, timeout_seconds=1.0),
    )

    assert observed_worker_budget == pytest.approx([0.65])
    assert fingerprint_calls == 1
    assert _single_error_code(result) is ParseErrorCode.MEDIA_TRANSCRIPTION_TIMEOUT
