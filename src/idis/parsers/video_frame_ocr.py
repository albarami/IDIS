"""Deterministic, bounded OCR extraction from sampled video frames.

Frames exist only as in-memory PNG payloads.  The caller owns the media source
and one aggregate deadline spanning scene discovery, decode, OCR, and cleanup.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from idis.parsers.ocr import (
    OcrConfig,
    OcrError,
    OcrTimeoutError,
    OcrUnavailableError,
    overall_mean_confidence,
)

FRAME_SAMPLING_POLICY = "periodic-plus-scenes-v1"
FRAME_SCENE_THRESHOLD = 0.20
FRAME_PERIOD_MS = 1_000
MAX_FRAME_SAMPLES = 600
MAX_FRAME_WIDTH = 1_920
MAX_FRAME_HEIGHT = 1_080
FRAME_OCR_RECEIPT_SCHEMA_VERSION = "media-frame-ocr-receipt-v1"

_SCENE_TIMESTAMP_RE = re.compile(r"\bpts_time:(-?(?:\d+(?:\.\d*)?|\.\d+))")


class VideoFrameOcrError(Exception):
    """Safe video-frame OCR failure carrying one stable public code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VideoFrameOcrSegment:
    """Text evidence extracted from one scheduled frame."""

    sample_index: int
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None

    def to_payload(self) -> dict[str, object]:
        """Return the process-transport representation."""
        return {
            "sample_index": self.sample_index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class VideoFrameOcrExtraction:
    """Completed frame schedule, evidence, and re-verifiable receipt."""

    sample_ms: tuple[int, ...]
    segments: tuple[VideoFrameOcrSegment, ...]
    receipt: Mapping[str, object]


def build_periodic_plus_scenes_schedule(
    *,
    duration_ms: int,
    scene_transition_ms: Iterable[int | float] = (),
) -> tuple[int, ...]:
    """Build the plan-pinned deterministic schedule or fail instead of truncating."""
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms <= 0:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_FAILED",
            "Video duration is invalid for deterministic frame sampling",
        )

    last_interior_ms = duration_ms - 1
    candidates: set[int] = {0, last_interior_ms}
    for bucket_start_ms in range(0, duration_ms, FRAME_PERIOD_MS):
        midpoint_ms = bucket_start_ms + (FRAME_PERIOD_MS // 2)
        if midpoint_ms < duration_ms:
            candidates.add(midpoint_ms)

    for transition in scene_transition_ms:
        if isinstance(transition, bool):
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_FAILED",
                "Scene-transition timestamp is invalid",
            )
        try:
            numeric_transition = float(transition)
        except (TypeError, ValueError, OverflowError) as exc:
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_FAILED",
                "Scene-transition timestamp is invalid",
            ) from exc
        if not math.isfinite(numeric_transition):
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_FAILED",
                "Scene-transition timestamp is invalid",
            )
        rounded_transition = int(round(numeric_transition))
        candidates.add(min(last_interior_ms, max(0, rounded_transition)))

    schedule = tuple(sorted(candidates))
    if len(schedule) > MAX_FRAME_SAMPLES:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_SAMPLE_LIMIT_EXCEEDED",
            "Deterministic video-frame schedule exceeds the admitted sample limit",
        )
    return schedule


def extract_video_frame_ocr(
    source_path: str | Path,
    *,
    duration_ms: int,
    ffmpeg_binary: str,
    ocr_config: OcrConfig | None,
    remaining_seconds: Callable[[], float],
) -> VideoFrameOcrExtraction:
    """Run scene discovery, bounded frame decode, and admitted Tesseract OCR."""
    if (
        ocr_config is None
        or not ocr_config.enabled
        or ocr_config.adapter is None
        or not callable(getattr(ocr_config.adapter, "extract_image_text", None))
    ):
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_UNAVAILABLE",
            "Video frame OCR is unavailable",
        )

    source = Path(source_path)
    scene_transition_ms = _discover_scene_transitions(
        source,
        ffmpeg_binary=ffmpeg_binary,
        remaining_seconds=remaining_seconds,
    )
    schedule = build_periodic_plus_scenes_schedule(
        duration_ms=duration_ms,
        scene_transition_ms=scene_transition_ms,
    )

    completed_sample_count = 0
    segments: list[VideoFrameOcrSegment] = []
    for sample_index, sample_ms in enumerate(schedule):
        frame = _decode_frame_png(
            source,
            sample_ms=sample_ms,
            is_last_interior=sample_ms == duration_ms - 1,
            ffmpeg_binary=ffmpeg_binary,
            remaining_seconds=remaining_seconds,
        )
        try:
            pages = ocr_config.adapter.extract_image_text(
                frame,
                timeout_seconds=min(
                    float(ocr_config.timeout_seconds),
                    remaining_seconds(),
                ),
            )
        except OcrTimeoutError as exc:
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_TIMEOUT",
                "Video frame OCR timed out",
            ) from exc
        except OcrUnavailableError as exc:
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_UNAVAILABLE",
                "Video frame OCR is unavailable",
            ) from exc
        except OcrError as exc:
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_FAILED",
                "Video frame OCR failed",
            ) from exc
        except (RuntimeError, TypeError, ValueError) as exc:
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_FAILED",
                "Video frame OCR failed",
            ) from exc
        finally:
            # Explicitly release the private frame before the next deadline check.
            frame = b""

        remaining_seconds()
        completed_sample_count += 1
        text = "\n".join(page.text.strip() for page in pages if page.text.strip())
        if not text:
            continue
        confidence = overall_mean_confidence(pages)
        if confidence is None:
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_INCOMPLETE",
                "Non-empty video frame OCR lacks confidence provenance",
            )
        segments.append(
            VideoFrameOcrSegment(
                sample_index=sample_index,
                start_ms=sample_ms,
                end_ms=min(duration_ms, sample_ms + 1),
                text=text,
                confidence=confidence,
            )
        )

    if completed_sample_count != len(schedule):
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_INCOMPLETE",
            "Video frame OCR did not complete every scheduled sample",
        )
    receipt = build_frame_ocr_receipt(
        duration_ms=duration_ms,
        sample_ms=schedule,
        segments=segments,
        completed_sample_count=completed_sample_count,
    )
    return VideoFrameOcrExtraction(
        sample_ms=schedule,
        segments=tuple(segments),
        receipt=receipt,
    )


def build_frame_ocr_receipt(
    *,
    duration_ms: int,
    sample_ms: Sequence[int],
    segments: Sequence[VideoFrameOcrSegment],
    completed_sample_count: int,
) -> dict[str, object]:
    """Build a safe receipt whose counts and hashes can be rederived by the parent."""
    evidence = frame_evidence_payload(segments)
    confidences = [segment.confidence for segment in segments if segment.confidence is not None]
    return {
        "schema_version": FRAME_OCR_RECEIPT_SCHEMA_VERSION,
        "status": "completed",
        "sampling_policy": FRAME_SAMPLING_POLICY,
        "scene_threshold": FRAME_SCENE_THRESHOLD,
        "max_sample_count": MAX_FRAME_SAMPLES,
        "max_frame_width": MAX_FRAME_WIDTH,
        "max_frame_height": MAX_FRAME_HEIGHT,
        "duration_ms": duration_ms,
        "scheduled_sample_count": len(sample_ms),
        "completed_sample_count": completed_sample_count,
        "nonempty_sample_count": len(evidence),
        "evidence_span_count": len(evidence),
        "schedule_sha256": canonical_sha256(list(sample_ms)),
        "evidence_sha256": canonical_sha256(evidence),
        "confidence_count": len(confidences),
        "mean_confidence": (round(sum(confidences) / len(confidences), 4) if confidences else None),
        "minimum_confidence": min(confidences) if confidences else None,
        "maximum_confidence": max(confidences) if confidences else None,
    }


def failed_frame_ocr_receipt(*, code: str, duration_ms: int) -> dict[str, object]:
    """Return a safe non-completed receipt for one stable frame-track failure."""
    status_by_code = {
        "MEDIA_FRAME_OCR_UNAVAILABLE": "unavailable",
        "MEDIA_FRAME_OCR_TIMEOUT": "timeout",
        "MEDIA_FRAME_OCR_INCOMPLETE": "incomplete",
    }
    return {
        "schema_version": FRAME_OCR_RECEIPT_SCHEMA_VERSION,
        "status": status_by_code.get(code, "failed"),
        "error_code": code,
        "sampling_policy": FRAME_SAMPLING_POLICY,
        "scene_threshold": FRAME_SCENE_THRESHOLD,
        "max_sample_count": MAX_FRAME_SAMPLES,
        "max_frame_width": MAX_FRAME_WIDTH,
        "max_frame_height": MAX_FRAME_HEIGHT,
        "duration_ms": duration_ms,
    }


def frame_evidence_payload(
    segments: Sequence[VideoFrameOcrSegment],
) -> list[dict[str, object]]:
    """Return the canonical frame evidence covered by the receipt digest."""
    return [
        {
            "source": "video_frame_ocr",
            "sample_index": segment.sample_index,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
            "confidence": segment.confidence,
            "content_hash": text_sha256(segment.text),
        }
        for segment in segments
        if segment.text.strip()
    ]


def canonical_sha256(value: object) -> str:
    """Hash a JSON-safe value using the receipt canonicalization contract."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def text_sha256(text: str) -> str:
    """Hash normalized evidence text exactly as persisted in a span."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _discover_scene_transitions(
    source_path: Path,
    *,
    ffmpeg_binary: str,
    remaining_seconds: Callable[[], float],
) -> tuple[int, ...]:
    timeout_seconds = remaining_seconds()
    try:
        result = subprocess.run(
            [
                ffmpeg_binary,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(source_path),
                "-vf",
                f"select=gt(scene\\,{FRAME_SCENE_THRESHOLD:.2f}),showinfo",
                "-an",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_UNAVAILABLE",
            "ffmpeg is unavailable for video frame OCR",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_TIMEOUT",
            "Video scene discovery timed out",
        ) from exc
    except OSError as exc:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_FAILED",
            "Video scene discovery failed",
        ) from exc
    remaining_seconds()
    if result.returncode != 0:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_FAILED",
            "Video scene discovery failed",
        )
    stderr = result.stderr.decode("utf-8", errors="replace")
    return tuple(
        int(round(float(match.group(1)) * 1_000)) for match in _SCENE_TIMESTAMP_RE.finditer(stderr)
    )


def _decode_frame_png(
    source_path: Path,
    *,
    sample_ms: int,
    is_last_interior: bool,
    ffmpeg_binary: str,
    remaining_seconds: Callable[[], float],
) -> bytes:
    timeout_seconds = remaining_seconds()
    seek_seconds = f"{sample_ms / 1_000:.3f}"
    command = [
        ffmpeg_binary,
        "-v",
        "error",
        "-nostdin",
        "-ss",
        seek_seconds,
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        "-vf",
        (
            f"scale=w='min(iw,{MAX_FRAME_WIDTH})':h='min(ih,{MAX_FRAME_HEIGHT})':"
            "force_original_aspect_ratio=decrease:flags=lanczos"
        ),
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_UNAVAILABLE",
            "ffmpeg is unavailable for video frame OCR",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_TIMEOUT",
            "Video frame decode timed out",
        ) from exc
    except OSError as exc:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_FAILED",
            "Video frame decode failed",
        ) from exc
    remaining_seconds()

    # Some containers expose a format duration fractionally beyond their final
    # presentation timestamp.  A deterministic end-relative retry still binds
    # the evidence locator to the approved last-interior schedule point.
    if result.returncode == 0 and not result.stdout and is_last_interior:
        retry = command.copy()
        seek_index = retry.index("-ss")
        retry[seek_index : seek_index + 2] = ["-sseof", "-1"]
        filter_index = retry.index("-vf") + 1
        retry[filter_index] = f"reverse,{retry[filter_index]}"
        try:
            result = subprocess.run(
                retry,
                check=False,
                capture_output=True,
                timeout=remaining_seconds(),
            )
        except subprocess.TimeoutExpired as exc:
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_TIMEOUT",
                "Last video frame decode timed out",
            ) from exc
        except OSError as exc:
            raise VideoFrameOcrError(
                "MEDIA_FRAME_OCR_FAILED",
                "Last video frame decode failed",
            ) from exc
        remaining_seconds()

    if result.returncode != 0 or not result.stdout:
        raise VideoFrameOcrError(
            "MEDIA_FRAME_OCR_INCOMPLETE",
            "A scheduled video frame could not be decoded",
        )
    return result.stdout
