"""Config-gated media transcription parser boundary for private gate readiness."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import logging
import math
import multiprocessing as mp
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from queue import Empty
from typing import Any, Protocol, cast

from idis.parsers.base import ParseError, ParseErrorCode, ParseLimits, ParseResult, SpanDraft
from idis.parsers.ocr import OcrConfig, TesseractOcrAdapter
from idis.parsers.video_frame_ocr import (
    FRAME_SAMPLING_POLICY,
    FRAME_SCENE_THRESHOLD,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_SAMPLES,
    MAX_FRAME_WIDTH,
    VideoFrameOcrError,
    VideoFrameOcrSegment,
    build_frame_ocr_receipt,
    build_periodic_plus_scenes_schedule,
    canonical_sha256,
    extract_video_frame_ocr,
    failed_frame_ocr_receipt,
    text_sha256,
)

MAX_MEDIA_SEGMENTS = 500
MAX_MEDIA_SEGMENT_TEXT_CHARS = 20_000
MAX_MEDIA_DURATION_SECONDS = 600.0
FASTER_WHISPER_ADAPTER_NAME = "faster-whisper"
FASTER_WHISPER_REQUIRED_MODEL_FILES = frozenset({"model.bin", "config.json"})
FASTER_WHISPER_RUNTIME_PROVENANCE_KEY = "media_runtime_provenance"
FASTER_WHISPER_RUNTIME_PROVENANCE_SCHEMA_VERSION = "faster-whisper-runtime-provenance-v2"
FASTER_WHISPER_ADAPTER_CONTRACT_VERSION = "idis-faster-whisper-adapter-v1"
FASTER_WHISPER_CPU_THREADS = 4
FASTER_WHISPER_NUM_WORKERS = 1
_FASTER_WHISPER_DISTRIBUTION_NAME = "faster-whisper"
_SAFE_RUNTIME_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")
_MEDIA_CLEANUP_RESERVE_MAX_SECONDS = 0.25
_MEDIA_PROCESS_REAP_SECONDS = 0.20
_LOG = logging.getLogger(__name__)


class MediaError(Exception):
    """Base class for safe media adapter failures."""


class MediaTimeoutError(MediaError):
    """Raised by media adapters when transcription exceeds a runtime budget."""


class MediaUnavailableError(MediaError):
    """Raised when media conversion/transcription dependencies are unavailable."""


class MediaDurationExceededError(MediaError):
    """Raised when media duration exceeds the configured private gate bound."""


class MediaModelMutationError(MediaError):
    """Raised when a local model cannot retain one immutable inference fingerprint."""


class MediaFrameOcrFailure(MediaError):
    """Raised when the required video frame-evidence track fails closed."""

    def __init__(self, code: ParseErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class FasterWhisperModelStatus(StrEnum):
    """Safe faster-whisper model availability states."""

    LOCAL_MODEL_READY = "local_model_ready"
    DOWNLOAD_ALLOWED = "download_allowed"
    MODEL_UNAVAILABLE = "model_unavailable"


@dataclass(frozen=True, slots=True)
class MediaSegmentText:
    """Transcribed text for one media time segment."""

    start_ms: int
    end_ms: int
    text: str


class MediaAdapter(Protocol):
    """Adapter interface for opt-in private media transcription implementations."""

    def extract_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[MediaSegmentText]:
        """Return transcribed text by time segment for media bytes."""


@dataclass(frozen=True, slots=True)
class MediaConfig:
    """Config-gated media execution settings."""

    enabled: bool = False
    adapter: MediaAdapter | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class FasterWhisperMediaConfig:
    """Config-gated faster-whisper runtime settings."""

    model_name: str | None = None
    model_path: str | None = None
    allow_model_download: bool = False
    language: str = "en"
    compute_type: str = "int8"
    max_duration_seconds: float = MAX_MEDIA_DURATION_SECONDS
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    frame_ocr_config: OcrConfig | None = None
    _validated_duration_seconds: float | None = field(default=None, repr=False, compare=False)
    _dual_track_required: bool = field(default=False, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class FasterWhisperModelProbe:
    """Path-free faster-whisper model probe result."""

    status: FasterWhisperModelStatus
    can_attempt: bool


@dataclass(frozen=True, slots=True)
class FasterWhisperRuntimeProvenance:
    """Path-free proof binding one transcription to its local model and config."""

    runtime_package_version: str
    model_files_sha256: str
    cpu_threads: int
    num_workers: int
    config_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the only JSONB-safe public representation."""
        return {
            "schema_version": FASTER_WHISPER_RUNTIME_PROVENANCE_SCHEMA_VERSION,
            "adapter": FASTER_WHISPER_ADAPTER_NAME,
            "adapter_contract_version": FASTER_WHISPER_ADAPTER_CONTRACT_VERSION,
            "runtime_package_version": self.runtime_package_version,
            "model_source": "local",
            "model_files_sha256": self.model_files_sha256,
            "cpu_threads": self.cpu_threads,
            "num_workers": self.num_workers,
            "config_sha256": self.config_sha256,
        }


@dataclass(frozen=True, slots=True)
class FasterWhisperExtraction:
    """Per-call extraction result; avoids mutable adapter provenance state."""

    segments: list[MediaSegmentText]
    runtime_provenance: FasterWhisperRuntimeProvenance | None
    frame_ocr_segments: list[VideoFrameOcrSegment] = field(default_factory=list)
    duration_ms: int | None = None
    media_stt_receipt: dict[str, object] | None = None
    media_frame_ocr_receipt: dict[str, object] | None = None
    dual_track_completed: bool = False


MediaSource = bytes | str | Path
FasterWhisperWorkerTarget = Callable[
    [MediaSource, FasterWhisperMediaConfig, float, Any],
    None,
]
BinaryResolver = Callable[[str], str | None]
DurationProbe = Callable[[MediaSource, FasterWhisperMediaConfig, float], float]
ModelFingerprinter = Callable[[str | Path, float], str]
MediaWorkerRunner = Callable[
    [Callable[..., None], tuple[object, ...], float],
    dict[str, object],
]
MonotonicClock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class _MediaDeadline:
    """One monotonic wall-clock deadline shared by every adapter phase."""

    expires_at: float
    clock: MonotonicClock

    @classmethod
    def start(cls, *, timeout_seconds: float, clock: MonotonicClock) -> _MediaDeadline:
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise MediaTimeoutError("Media transcription timed out")
        return cls(expires_at=clock() + timeout_seconds, clock=clock)

    def remaining_seconds(self) -> float:
        """Return the remaining budget or fail once the common deadline expires."""
        remaining = self.expires_at - self.clock()
        if not math.isfinite(remaining) or remaining <= 0:
            raise MediaTimeoutError("Media transcription timed out")
        return remaining


class FasterWhisperMediaAdapter:
    """Process-isolated private media adapter backed by faster-whisper."""

    def __init__(
        self,
        *,
        config: FasterWhisperMediaConfig,
        worker_target: FasterWhisperWorkerTarget | None = None,
        binary_resolver: BinaryResolver | None = None,
        duration_probe: DurationProbe | None = None,
        model_fingerprinter: ModelFingerprinter | None = None,
        worker_runner: MediaWorkerRunner | None = None,
        monotonic_clock: MonotonicClock | None = None,
    ) -> None:
        self._config = config
        self._worker_target_is_default = worker_target is None
        self._worker_target = worker_target or _faster_whisper_worker
        self._binary_resolver = binary_resolver or shutil.which
        self._duration_probe = duration_probe or _ffprobe_duration_seconds
        self._model_fingerprinter = model_fingerprinter
        self._worker_runner = worker_runner
        self._monotonic_clock = monotonic_clock or time.monotonic

    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        """Return transcribed text for bounded media bytes."""
        return self._extract_source(
            data,
            timeout_seconds=timeout_seconds,
            require_dual_track=False,
        ).segments

    def extract_text_with_provenance(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> FasterWhisperExtraction:
        """Return strict dual-track evidence and immutable runtime provenance."""
        return self._extract_source(
            data,
            timeout_seconds=timeout_seconds,
            require_dual_track=True,
        )

    def extract_file(
        self,
        source_path: str | Path,
        *,
        timeout_seconds: float,
    ) -> list[MediaSegmentText]:
        """Transcribe one caller-owned file without copying its complete payload.

        The resolved path is passed unchanged to both ffprobe and the isolated
        faster-whisper worker.  The caller retains ownership and cleanup of the
        upload spool.
        """
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise MediaError("Media source file is unavailable")
        return self._extract_source(
            path,
            timeout_seconds=timeout_seconds,
        ).segments

    def extract_file_with_provenance(
        self,
        source_path: str | Path,
        *,
        timeout_seconds: float,
    ) -> FasterWhisperExtraction:
        """Extract strict dual-track evidence from a caller-owned path."""
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise MediaError("Media source file is unavailable")
        return self._extract_source(
            path,
            timeout_seconds=timeout_seconds,
            require_dual_track=True,
        )

    def _extract_source(
        self,
        source: MediaSource,
        *,
        timeout_seconds: float,
        require_dual_track: bool = False,
    ) -> FasterWhisperExtraction:
        deadline = _MediaDeadline.start(
            timeout_seconds=timeout_seconds,
            clock=self._monotonic_clock,
        )
        ffmpeg_path = self._binary_resolver(self._config.ffmpeg_binary)
        deadline.remaining_seconds()
        if ffmpeg_path is None:
            raise MediaUnavailableError("ffmpeg is unavailable")
        ffprobe_path = self._binary_resolver(self._config.ffprobe_binary)
        deadline.remaining_seconds()
        if ffprobe_path is None:
            raise MediaUnavailableError("ffprobe is unavailable")
        model_probe = probe_faster_whisper_model(self._config)
        deadline.remaining_seconds()
        if not model_probe.can_attempt:
            raise MediaUnavailableError("Media transcription model is unavailable")

        duration_seconds = _validated_media_duration_seconds(
            self._duration_probe(
                source,
                self._config,
                deadline.remaining_seconds(),
            ),
            max_duration_seconds=self._config.max_duration_seconds,
        )
        deadline.remaining_seconds()

        runtime_provenance = _capture_local_runtime_provenance(
            self._config,
            configured_timeout_seconds=timeout_seconds,
            deadline=deadline,
            model_fingerprinter=self._model_fingerprinter,
        )

        worker_timeout_seconds = deadline.remaining_seconds()
        dual_track_required = require_dual_track and self._worker_target_is_default
        worker_config = replace(
            self._config,
            _validated_duration_seconds=duration_seconds,
            _dual_track_required=dual_track_required,
        )
        worker_args: tuple[object, ...] = (
            source,
            worker_config,
            worker_timeout_seconds,
        )
        if self._worker_runner is None:
            payload = _run_media_worker(
                target=self._worker_target,
                args=worker_args,
                deadline=deadline,
            )
        else:
            payload = self._worker_runner(
                self._worker_target,
                worker_args,
                worker_timeout_seconds,
            )
            deadline.remaining_seconds()
        post_inference_provenance = _capture_local_runtime_provenance(
            self._config,
            configured_timeout_seconds=timeout_seconds,
            deadline=deadline,
            model_fingerprinter=self._model_fingerprinter,
        )
        if post_inference_provenance != runtime_provenance:
            raise MediaModelMutationError("Media transcription model changed during inference")
        extraction = _extraction_from_worker_payload(
            payload,
            require_dual_track=dual_track_required,
        )
        return replace(
            extraction,
            runtime_provenance=runtime_provenance,
        )


def extract_media_file(
    adapter: MediaAdapter,
    source_path: str | Path,
    *,
    timeout_seconds: float,
) -> list[MediaSegmentText]:
    """Transcribe a caller-owned media path through the path-capable adapter."""
    extract_file = getattr(adapter, "extract_file", None)
    if callable(extract_file):
        return cast(
            list[MediaSegmentText],
            extract_file(source_path, timeout_seconds=timeout_seconds),
        )
    # Injectable adapters used at the parser boundary may implement the original
    # method name while accepting a path. Production construction still admits
    # only FasterWhisperMediaAdapter.
    return adapter.extract_text(source_path, timeout_seconds=timeout_seconds)  # type: ignore[arg-type]


def parse_media(
    data: bytes,
    limits: ParseLimits | None = None,
    media_config: MediaConfig | None = None,
) -> ParseResult:
    """Parse media bytes through an explicit private transcription adapter."""
    if limits is None:
        limits = ParseLimits()

    if len(data) > limits.max_bytes:
        return ParseResult(
            doc_type="MEDIA",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.MAX_SIZE_EXCEEDED,
                    message=f"File size {len(data)} bytes exceeds limit {limits.max_bytes}",
                    details={"size": len(data), "limit": limits.max_bytes},
                )
            ],
        )

    if media_config is None or not media_config.enabled or media_config.adapter is None:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_UNAVAILABLE,
            "Media transcription unavailable",
        )
    if media_config.timeout_seconds <= 0:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_TIMEOUT,
            "Media transcription timed out",
        )

    try:
        if isinstance(media_config.adapter, FasterWhisperMediaAdapter):
            extraction = media_config.adapter.extract_text_with_provenance(
                data,
                timeout_seconds=media_config.timeout_seconds,
            )
            if extraction.dual_track_completed:
                return _parse_dual_track_extraction(extraction)
            return _parse_media_segments(
                extraction.segments,
                runtime_provenance=extraction.runtime_provenance,
            )
        segments = media_config.adapter.extract_text(
            data,
            timeout_seconds=media_config.timeout_seconds,
        )
        return _parse_media_segments(segments)
    except MediaTimeoutError:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_TIMEOUT,
            "Media transcription timed out",
        )
    except MediaUnavailableError:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_UNAVAILABLE,
            "Media transcription unavailable",
        )
    except MediaDurationExceededError:
        return _media_error(
            ParseErrorCode.MEDIA_DURATION_EXCEEDED,
            "Media duration exceeds configured limit",
        )
    except MediaFrameOcrFailure as exc:
        return _media_error(exc.code, _media_frame_error_message(exc.code))
    except MediaError:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
            "Media transcription failed",
        )
    except Exception:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
            "Media transcription failed",
        )


def parse_media_file(
    source_path: str | Path,
    *,
    size_bytes: int,
    limits: ParseLimits | None = None,
    media_config: MediaConfig | None = None,
) -> ParseResult:
    """Parse an already-spooled media file without materializing it as bytes."""
    if limits is None:
        limits = ParseLimits()
    if size_bytes > limits.max_bytes:
        return ParseResult(
            doc_type="MEDIA",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.MAX_SIZE_EXCEEDED,
                    message=f"File size {size_bytes} bytes exceeds limit {limits.max_bytes}",
                    details={"size": size_bytes, "limit": limits.max_bytes},
                )
            ],
        )
    if size_bytes < 0:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
            "Media source size is invalid",
        )
    try:
        actual_size = Path(source_path).stat().st_size
    except OSError:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
            "Media source file is unavailable",
        )
    if actual_size != size_bytes:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
            "Media source size does not match the validated upload size",
        )
    if media_config is None or not media_config.enabled or media_config.adapter is None:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_UNAVAILABLE,
            "Media transcription unavailable",
        )
    if media_config.timeout_seconds <= 0:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_TIMEOUT,
            "Media transcription timed out",
        )
    try:
        if isinstance(media_config.adapter, FasterWhisperMediaAdapter):
            extraction = media_config.adapter.extract_file_with_provenance(
                source_path,
                timeout_seconds=media_config.timeout_seconds,
            )
            if extraction.dual_track_completed:
                return _parse_dual_track_extraction(extraction)
            return _parse_media_segments(
                extraction.segments,
                runtime_provenance=extraction.runtime_provenance,
            )
        segments = extract_media_file(
            media_config.adapter,
            source_path,
            timeout_seconds=media_config.timeout_seconds,
        )
        return _parse_media_segments(segments)
    except MediaTimeoutError:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_TIMEOUT,
            "Media transcription timed out",
        )
    except MediaUnavailableError:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_UNAVAILABLE,
            "Media transcription unavailable",
        )
    except MediaDurationExceededError:
        return _media_error(
            ParseErrorCode.MEDIA_DURATION_EXCEEDED,
            "Media duration exceeds configured limit",
        )
    except MediaFrameOcrFailure as exc:
        return _media_error(exc.code, _media_frame_error_message(exc.code))
    except MediaError:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
            "Media transcription failed",
        )
    except Exception:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
            "Media transcription failed",
        )


def _parse_media_segments(
    segments: list[MediaSegmentText],
    *,
    runtime_provenance: FasterWhisperRuntimeProvenance | None = None,
) -> ParseResult:
    if len(segments) > MAX_MEDIA_SEGMENTS:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
            "Media transcription returned invalid segment results",
        )

    spans: list[SpanDraft] = []
    total_text_length = 0
    previous_end_ms = -1
    for segment in segments:
        if not _valid_segment(segment=segment, previous_end_ms=previous_end_ms):
            return _media_error(
                ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
                "Media transcription returned invalid segment results",
            )
        previous_end_ms = segment.end_ms
        stripped = segment.text.strip()
        if not stripped:
            continue
        total_text_length += len(stripped)
        spans.append(
            SpanDraft(
                span_type="TIMECODE",
                locator={
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "source": "media_transcript",
                },
                text_excerpt=stripped,
                content_hash=_compute_content_hash(stripped),
            )
        )

    if not spans:
        return _media_error(
            ParseErrorCode.MEDIA_NO_TEXT_EXTRACTED,
            "Media transcription completed but no extractable text was found",
        )

    metadata: dict[str, Any] = {
        "span_count": len(spans),
        "total_text_length": total_text_length,
        "media_transcription_performed": True,
        "media_segment_count": len(segments),
    }
    if runtime_provenance is not None:
        metadata[FASTER_WHISPER_RUNTIME_PROVENANCE_KEY] = runtime_provenance.to_dict()
    return ParseResult(
        doc_type="MEDIA",
        success=True,
        spans=spans,
        metadata=metadata,
    )


def _parse_dual_track_extraction(extraction: FasterWhisperExtraction) -> ParseResult:
    """Materialize independently ordered STT and frame-OCR evidence tracks."""
    if (
        extraction.media_stt_receipt is None
        or extraction.media_frame_ocr_receipt is None
        or extraction.duration_ms is None
    ):
        return _media_error(
            ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE,
            "Video frame OCR completion receipt is missing",
        )

    spans: list[SpanDraft] = []
    total_text_length = 0
    previous_end_ms = -1
    for segment in extraction.segments:
        if not _valid_segment(segment=segment, previous_end_ms=previous_end_ms):
            return _media_error(
                ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
                "Media transcription returned invalid segment results",
            )
        previous_end_ms = segment.end_ms
        stripped = segment.text.strip()
        if not stripped:
            continue
        total_text_length += len(stripped)
        spans.append(
            SpanDraft(
                span_type="TIMECODE",
                locator={
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "source": "media_transcript",
                },
                text_excerpt=stripped,
                content_hash=_compute_content_hash(stripped),
            )
        )

    previous_sample_index = -1
    for frame_segment in extraction.frame_ocr_segments:
        if (
            frame_segment.sample_index <= previous_sample_index
            or frame_segment.start_ms < 0
            or frame_segment.end_ms <= frame_segment.start_ms
            or frame_segment.end_ms > extraction.duration_ms
            or len(frame_segment.text) > MAX_MEDIA_SEGMENT_TEXT_CHARS
        ):
            return _media_error(
                ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE,
                "Video frame OCR returned invalid evidence spans",
            )
        previous_sample_index = frame_segment.sample_index
        stripped = frame_segment.text.strip()
        if not stripped:
            continue
        total_text_length += len(stripped)
        spans.append(
            SpanDraft(
                span_type="TIMECODE",
                locator={
                    "start_ms": frame_segment.start_ms,
                    "end_ms": frame_segment.end_ms,
                    "source": "video_frame_ocr",
                    "sample_index": frame_segment.sample_index,
                    "confidence": frame_segment.confidence,
                },
                text_excerpt=stripped,
                content_hash=_compute_content_hash(stripped),
            )
        )

    if not spans:
        return _media_error(
            ParseErrorCode.MEDIA_NO_EXTRACTABLE_EVIDENCE,
            "Media completed both evidence tracks but yielded no extractable evidence",
        )

    stt_receipt = dict(extraction.media_stt_receipt)
    if extraction.runtime_provenance is not None:
        stt_receipt["runtime_provenance_sha256"] = canonical_sha256(
            extraction.runtime_provenance.to_dict()
        )
    metadata: dict[str, Any] = {
        "span_count": len(spans),
        "total_text_length": total_text_length,
        "media_transcription_performed": True,
        "media_frame_ocr_performed": True,
        "media_segment_count": len(extraction.segments),
        "media_frame_ocr_segment_count": len(extraction.frame_ocr_segments),
        "media_duration_ms": extraction.duration_ms,
        "media_stt_receipt": stt_receipt,
        "media_frame_ocr_receipt": dict(extraction.media_frame_ocr_receipt),
    }
    if extraction.runtime_provenance is not None:
        metadata[FASTER_WHISPER_RUNTIME_PROVENANCE_KEY] = extraction.runtime_provenance.to_dict()
    return ParseResult(
        doc_type="MEDIA",
        success=True,
        spans=spans,
        metadata=metadata,
    )


def _valid_segment(*, segment: MediaSegmentText, previous_end_ms: int) -> bool:
    if not isinstance(segment.start_ms, int) or not isinstance(segment.end_ms, int):
        return False
    if segment.start_ms < 0 or segment.end_ms <= segment.start_ms:
        return False
    if segment.start_ms < previous_end_ms:
        return False
    if not isinstance(segment.text, str):
        return False
    return len(segment.text) <= MAX_MEDIA_SEGMENT_TEXT_CHARS


def probe_faster_whisper_model(config: FasterWhisperMediaConfig) -> FasterWhisperModelProbe:
    """Return a path-free model readiness status for faster-whisper."""
    if config.model_path:
        model_path = Path(config.model_path).expanduser()
        if _valid_local_faster_whisper_model_dir(model_path):
            return FasterWhisperModelProbe(
                status=FasterWhisperModelStatus.LOCAL_MODEL_READY,
                can_attempt=True,
            )
        return FasterWhisperModelProbe(
            status=FasterWhisperModelStatus.MODEL_UNAVAILABLE,
            can_attempt=False,
        )
    if config.model_name and config.allow_model_download:
        return FasterWhisperModelProbe(
            status=FasterWhisperModelStatus.DOWNLOAD_ALLOWED,
            can_attempt=True,
        )
    return FasterWhisperModelProbe(
        status=FasterWhisperModelStatus.MODEL_UNAVAILABLE,
        can_attempt=False,
    )


def fingerprint_local_faster_whisper_model(
    model_path: str | Path,
    *,
    timeout_check: Callable[[], object] | None = None,
) -> str:
    """Hash a complete local model tree without exposing its path or filenames.

    The digest algorithm intentionally matches the strict STT health probe: each
    sorted relative path, declared size, and complete file body is included.  A
    file-set or stat change during the read is rejected instead of producing a
    completed-looking proof over a moving model.  Runtime callers provide the
    common media-deadline check so large model trees cannot escape the budget.
    """
    try:
        _check_fingerprint_timeout(timeout_check)
        configured_root = Path(model_path).expanduser()
        if configured_root.is_symlink():
            raise MediaModelMutationError("Local media model file set is invalid")
        root = configured_root.resolve(strict=True)
        if not root.is_dir():
            raise MediaModelMutationError("Local media model file set is invalid")
        _check_fingerprint_timeout(timeout_check)
        files = sorted(path for path in root.rglob("*") if path.is_file())
        _check_fingerprint_timeout(timeout_check)
        relative_paths = tuple(path.relative_to(root).as_posix() for path in files)
        if (
            not files
            or not FASTER_WHISPER_REQUIRED_MODEL_FILES.issubset(relative_paths)
            or any(path.is_symlink() for path in files)
        ):
            raise MediaModelMutationError("Local media model file set is invalid")

        digest = hashlib.sha256()
        for path, relative_path_text in zip(files, relative_paths, strict=True):
            _check_fingerprint_timeout(timeout_check)
            before = path.stat()
            relative_path = relative_path_text.encode("utf-8")
            digest.update(len(relative_path).to_bytes(8, "big"))
            digest.update(relative_path)
            digest.update(before.st_size.to_bytes(8, "big"))
            with path.open("rb") as model_file:
                while True:
                    _check_fingerprint_timeout(timeout_check)
                    chunk = model_file.read(1024 * 1024)
                    _check_fingerprint_timeout(timeout_check)
                    if not chunk:
                        break
                    digest.update(chunk)
            after = path.stat()
            _check_fingerprint_timeout(timeout_check)
            if _model_stat_signature(before) != _model_stat_signature(after):
                raise MediaModelMutationError("Local media model changed while fingerprinting")

        post_files = sorted(path for path in root.rglob("*") if path.is_file())
        _check_fingerprint_timeout(timeout_check)
        post_relative_paths = tuple(path.relative_to(root).as_posix() for path in post_files)
        if post_relative_paths != relative_paths or any(path.is_symlink() for path in post_files):
            raise MediaModelMutationError("Local media model file set changed")
        return digest.hexdigest()
    except MediaModelMutationError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise MediaModelMutationError("Local media model could not be fingerprinted") from exc


def _check_fingerprint_timeout(timeout_check: Callable[[], object] | None) -> None:
    if timeout_check is not None:
        timeout_check()


def _model_stat_signature(stat_result: os.stat_result) -> tuple[int, int, int]:
    return (stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_ino)


def _capture_local_runtime_provenance(
    config: FasterWhisperMediaConfig,
    *,
    configured_timeout_seconds: float,
    deadline: _MediaDeadline,
    model_fingerprinter: ModelFingerprinter | None,
) -> FasterWhisperRuntimeProvenance | None:
    """Capture one path-free local-model/config snapshot for an inference call."""
    deadline.remaining_seconds()
    if config.model_path is None:
        return None
    if config.model_name is not None or config.allow_model_download:
        raise MediaModelMutationError("Local media model admission is invalid")
    if (
        not math.isfinite(float(configured_timeout_seconds))
        or configured_timeout_seconds <= 0
        or not math.isfinite(float(config.max_duration_seconds))
        or config.max_duration_seconds <= 0
        or type(FASTER_WHISPER_CPU_THREADS) is not int
        or FASTER_WHISPER_CPU_THREADS <= 0
        or type(FASTER_WHISPER_NUM_WORKERS) is not int
        or FASTER_WHISPER_NUM_WORKERS <= 0
    ):
        raise MediaModelMutationError("Media inference configuration is invalid")
    try:
        runtime_package_version = importlib.metadata.version(_FASTER_WHISPER_DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError as exc:
        raise MediaUnavailableError("Media transcription runtime is unavailable") from exc
    deadline.remaining_seconds()
    if not _SAFE_RUNTIME_VERSION_RE.fullmatch(runtime_package_version):
        raise MediaUnavailableError("Media transcription runtime version is invalid")

    if model_fingerprinter is None:
        model_files_sha256 = fingerprint_local_faster_whisper_model(
            config.model_path,
            timeout_check=deadline.remaining_seconds,
        )
    else:
        model_files_sha256 = model_fingerprinter(
            config.model_path,
            deadline.remaining_seconds(),
        )
    deadline.remaining_seconds()
    config_payload = {
        "schema_version": "faster-whisper-inference-config-v1",
        "adapter_contract_version": FASTER_WHISPER_ADAPTER_CONTRACT_VERSION,
        "runtime_package_version": runtime_package_version,
        "model_source": "local",
        "allow_model_download": False,
        "language": config.language,
        "compute_type": config.compute_type,
        "cpu_threads": FASTER_WHISPER_CPU_THREADS,
        "num_workers": FASTER_WHISPER_NUM_WORKERS,
        "max_duration_seconds": float(config.max_duration_seconds),
        "timeout_seconds": float(configured_timeout_seconds),
        "ffmpeg_binary_sha256": hashlib.sha256(config.ffmpeg_binary.encode("utf-8")).hexdigest(),
        "ffprobe_binary_sha256": hashlib.sha256(config.ffprobe_binary.encode("utf-8")).hexdigest(),
        "device": "cpu",
        "beam_size": 1,
        "vad_filter": False,
        "frame_ocr": _frame_ocr_config_provenance(config.frame_ocr_config),
    }
    try:
        encoded_config = json.dumps(
            config_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MediaModelMutationError("Media inference configuration is invalid") from exc
    deadline.remaining_seconds()
    return FasterWhisperRuntimeProvenance(
        runtime_package_version=runtime_package_version,
        model_files_sha256=model_files_sha256,
        cpu_threads=FASTER_WHISPER_CPU_THREADS,
        num_workers=FASTER_WHISPER_NUM_WORKERS,
        config_sha256=hashlib.sha256(encoded_config).hexdigest(),
    )


def _frame_ocr_config_provenance(config: OcrConfig | None) -> dict[str, object] | None:
    """Return path-free OCR/sampling settings covered by media config provenance."""
    if config is None:
        return None
    adapter = config.adapter
    adapter_config: dict[str, object]
    if isinstance(adapter, TesseractOcrAdapter):
        adapter_config = {
            "adapter": "tesseract",
            "dpi": adapter.dpi,
            "language": adapter.language,
        }
    else:
        adapter_config = {"adapter": "non-production" if adapter is not None else "missing"}
    return {
        **adapter_config,
        "enabled": config.enabled,
        "per_frame_timeout_seconds": float(config.timeout_seconds),
        "sampling_policy": FRAME_SAMPLING_POLICY,
        "scene_threshold": FRAME_SCENE_THRESHOLD,
        "max_sample_count": MAX_FRAME_SAMPLES,
        "max_frame_width": MAX_FRAME_WIDTH,
        "max_frame_height": MAX_FRAME_HEIGHT,
    }


def _valid_local_faster_whisper_model_dir(model_path: Path) -> bool:
    if not model_path.exists() or not model_path.is_dir():
        return False
    return all(
        (model_path / filename).is_file() for filename in FASTER_WHISPER_REQUIRED_MODEL_FILES
    )


def _run_media_worker(
    *,
    target: Callable[..., None],
    args: tuple[object, ...],
    deadline: _MediaDeadline,
) -> dict[str, object]:
    """Drain one bounded result while the worker is alive, then release all handles."""
    queue: mp.Queue[dict[str, object]] = mp.Queue(maxsize=1)
    process = mp.Process(target=target, args=(*args, queue))
    started = False
    execution_deadline = _media_execution_deadline(deadline)
    try:
        execution_deadline.remaining_seconds()
        process.start()
        started = True
        payload = _receive_media_payload(
            queue=queue,
            process=process,
            deadline=execution_deadline,
        )
        process.join(execution_deadline.remaining_seconds())
        if process.is_alive():
            raise MediaTimeoutError("Media transcription timed out")
        deadline.remaining_seconds()
        return payload
    finally:
        try:
            _close_media_process(process=process, started=started, deadline=deadline)
        finally:
            _close_media_queue(queue)


def _media_execution_deadline(deadline: _MediaDeadline) -> _MediaDeadline:
    """Reserve bounded time from the shared deadline for child/queue cleanup."""
    remaining = deadline.remaining_seconds()
    cleanup_reserve = min(_MEDIA_CLEANUP_RESERVE_MAX_SECONDS, remaining / 4.0)
    return _MediaDeadline(
        expires_at=deadline.expires_at - cleanup_reserve,
        clock=deadline.clock,
    )


def _receive_media_payload(
    *,
    queue: mp.Queue[dict[str, object]],
    process: mp.Process,
    deadline: _MediaDeadline,
) -> dict[str, object]:
    while True:
        remaining = deadline.remaining_seconds()
        try:
            return queue.get(timeout=min(0.05, remaining))
        except Empty:
            if process.is_alive():
                continue
            process.join()
            try:
                return queue.get_nowait()
            except (EOFError, Empty, OSError, ValueError) as exc:
                raise MediaError("Media transcription worker produced no result") from exc
        except (EOFError, OSError, ValueError) as exc:
            raise MediaError("Media transcription worker produced no result") from exc


def _close_media_process(
    *,
    process: mp.Process,
    started: bool,
    deadline: _MediaDeadline,
) -> None:
    """Terminate a worker and close it once the post-kill reap observes exit."""
    if started and process.is_alive():
        _terminate_process_tree(
            process,
            timeout_seconds=_remaining_cleanup_seconds(deadline) / 2.0,
        )
    if started:
        process.join(timeout=_remaining_cleanup_seconds(deadline) / 2.0)
        if process.is_alive():
            with contextlib.suppress(OSError, ValueError):
                process.kill()
            process.join(
                timeout=max(
                    _remaining_cleanup_seconds(deadline),
                    _MEDIA_PROCESS_REAP_SECONDS,
                )
            )
    if not started or not process.is_alive():
        with contextlib.suppress(OSError, ValueError):
            process.close()
    else:
        _LOG.error("MEDIA_WORKER_HANDLE_UNREAPED", extra={"pid": process.pid})


def _close_media_queue(queue: mp.Queue[dict[str, object]]) -> None:
    # The parent never puts into this queue. Cancelling its feeder join before
    # close prevents a dead child/pipe from escaping the shared media deadline.
    with contextlib.suppress(OSError, ValueError):
        queue.cancel_join_thread()
    with contextlib.suppress(OSError, ValueError):
        queue.close()


def _remaining_cleanup_seconds(deadline: _MediaDeadline) -> float:
    """Return remaining cleanup budget without raising after deadline expiry."""
    remaining = deadline.expires_at - deadline.clock()
    if not math.isfinite(remaining) or remaining <= 0:
        return 0.0
    return remaining


def _extraction_from_worker_payload(
    payload: dict[str, object],
    *,
    require_dual_track: bool,
) -> FasterWhisperExtraction:
    """Validate process output and independently rederive both track receipts."""
    segments = _segments_from_worker_payload(payload)
    if not require_dual_track:
        return FasterWhisperExtraction(
            segments=segments,
            runtime_provenance=None,
        )

    raw_frame_receipt = payload.get("media_frame_ocr_receipt")
    if not isinstance(raw_frame_receipt, dict):
        raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
    _raise_for_frame_receipt_status(raw_frame_receipt)

    raw_stt_receipt = payload.get("media_stt_receipt")
    if not isinstance(raw_stt_receipt, dict):
        raise MediaError("Media transcription completion receipt is missing")
    _validate_stt_receipt(raw_stt_receipt, segments=segments)

    try:
        duration_ms = _required_positive_int(payload.get("duration_ms"))
    except ValueError as exc:
        raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE) from exc
    raw_sample_ms = payload.get("frame_sample_ms")
    if not isinstance(raw_sample_ms, list):
        raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
    sample_ms = _validated_frame_schedule(raw_sample_ms, duration_ms=duration_ms)

    raw_frame_segments = payload.get("frame_ocr_segments")
    if not isinstance(raw_frame_segments, list):
        raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
    frame_segments = _frame_segments_from_payload(
        raw_frame_segments,
        sample_ms=sample_ms,
        duration_ms=duration_ms,
    )
    _validate_frame_receipt(
        raw_frame_receipt,
        duration_ms=duration_ms,
        sample_ms=sample_ms,
        segments=frame_segments,
    )
    return FasterWhisperExtraction(
        segments=segments,
        runtime_provenance=None,
        frame_ocr_segments=frame_segments,
        duration_ms=duration_ms,
        media_stt_receipt=dict(raw_stt_receipt),
        media_frame_ocr_receipt=dict(raw_frame_receipt),
        dual_track_completed=True,
    )


def _segments_from_worker_payload(payload: dict[str, object]) -> list[MediaSegmentText]:
    status = payload.get("status")
    if status == "success":
        raw_segments = payload.get("segments")
        if not isinstance(raw_segments, list):
            raise MediaError("Media transcription worker returned malformed segments")
        segments: list[MediaSegmentText] = []
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                raise MediaError("Media transcription worker returned malformed segment")
            try:
                segments.append(
                    MediaSegmentText(
                        start_ms=int(raw_segment["start_ms"]),
                        end_ms=int(raw_segment["end_ms"]),
                        text=str(raw_segment["text"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise MediaError("Media transcription worker returned malformed segment") from exc
        return segments
    if status == "timeout":
        raise MediaTimeoutError("Media transcription timed out")
    if status == "unavailable":
        raise MediaUnavailableError("Media transcription unavailable")
    raise MediaError("Media transcription failed")


def _validate_stt_receipt(
    receipt: dict[object, object],
    *,
    segments: list[MediaSegmentText],
) -> None:
    evidence = _stt_evidence_payload(segments)
    expected = {
        "schema_version": "media-stt-receipt-v1",
        "status": "completed",
        "segment_count": len(segments),
        "evidence_span_count": len(evidence),
        "evidence_sha256": canonical_sha256(evidence),
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise MediaError("Media transcription completion receipt is invalid")


def _stt_evidence_payload(segments: list[MediaSegmentText]) -> list[dict[str, object]]:
    return [
        {
            "source": "media_transcript",
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
            "content_hash": text_sha256(segment.text),
        }
        for segment in segments
        if segment.text.strip()
    ]


def _raise_for_frame_receipt_status(receipt: dict[object, object]) -> None:
    status = receipt.get("status")
    if status == "completed":
        return
    explicit_code = receipt.get("error_code")
    if explicit_code == "MEDIA_FRAME_SAMPLE_LIMIT_EXCEEDED":
        raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_SAMPLE_LIMIT_EXCEEDED)
    code_by_status = {
        "unavailable": ParseErrorCode.MEDIA_FRAME_OCR_UNAVAILABLE,
        "timeout": ParseErrorCode.MEDIA_FRAME_OCR_TIMEOUT,
        "failed": ParseErrorCode.MEDIA_FRAME_OCR_FAILED,
        "incomplete": ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE,
    }
    raise MediaFrameOcrFailure(
        code_by_status.get(str(status), ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
    )


def _validated_frame_schedule(raw_sample_ms: list[object], *, duration_ms: int) -> tuple[int, ...]:
    if (
        not raw_sample_ms
        or len(raw_sample_ms) > MAX_FRAME_SAMPLES
        or any(isinstance(value, bool) or not isinstance(value, int) for value in raw_sample_ms)
    ):
        raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
    sample_ms = tuple(cast(int, value) for value in raw_sample_ms)
    if (
        sample_ms != tuple(sorted(set(sample_ms)))
        or sample_ms[0] != 0
        or sample_ms[-1] != duration_ms - 1
        or any(value < 0 or value >= duration_ms for value in sample_ms)
    ):
        raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
    try:
        required_periodic_samples = set(
            build_periodic_plus_scenes_schedule(duration_ms=duration_ms)
        )
    except VideoFrameOcrError as exc:
        code = getattr(ParseErrorCode, exc.code, ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
        raise MediaFrameOcrFailure(code) from exc
    if not required_periodic_samples.issubset(sample_ms):
        raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
    return sample_ms


def _frame_segments_from_payload(
    raw_segments: list[object],
    *,
    sample_ms: tuple[int, ...],
    duration_ms: int,
) -> list[VideoFrameOcrSegment]:
    segments: list[VideoFrameOcrSegment] = []
    previous_sample_index = -1
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict):
            raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
        try:
            sample_index = _required_nonnegative_int(raw_segment.get("sample_index"))
            start_ms = _required_nonnegative_int(raw_segment.get("start_ms"))
            end_ms = _required_positive_int(raw_segment.get("end_ms"))
            text = raw_segment["text"]
            confidence = raw_segment.get("confidence")
        except (KeyError, TypeError, ValueError) as exc:
            raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE) from exc
        if (
            sample_index <= previous_sample_index
            or sample_index >= len(sample_ms)
            or start_ms != sample_ms[sample_index]
            or end_ms != min(duration_ms, start_ms + 1)
            or not isinstance(text, str)
            or not text.strip()
            or len(text) > MAX_MEDIA_SEGMENT_TEXT_CHARS
            or not _valid_frame_confidence(confidence)
        ):
            raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)
        previous_sample_index = sample_index
        segments.append(
            VideoFrameOcrSegment(
                sample_index=sample_index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text.strip(),
                confidence=float(confidence) if confidence is not None else None,
            )
        )
    return segments


def _validate_frame_receipt(
    receipt: dict[object, object],
    *,
    duration_ms: int,
    sample_ms: tuple[int, ...],
    segments: list[VideoFrameOcrSegment],
) -> None:
    expected = build_frame_ocr_receipt(
        duration_ms=duration_ms,
        sample_ms=sample_ms,
        segments=segments,
        completed_sample_count=len(sample_ms),
    )
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise MediaFrameOcrFailure(ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE)


def _valid_frame_confidence(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0


def _required_nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("required nonnegative integer is invalid")
    return value


def _required_positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("required positive integer is invalid")
    return value


def _ffprobe_duration_seconds(
    source: MediaSource,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
) -> float:
    cleanup_path: str | None = None
    if isinstance(source, bytes):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
            temp_file.write(source)
            temp_path = temp_file.name
        cleanup_path = temp_path
    else:
        temp_path = str(Path(source))
    try:
        result = subprocess.run(
            [
                config.ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                temp_path,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=min(timeout_seconds, 10.0),
        )
    except FileNotFoundError as exc:
        raise MediaUnavailableError("ffprobe is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaTimeoutError("Media duration probe timed out") from exc
    finally:
        if cleanup_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(cleanup_path)

    if result.returncode != 0:
        raise MediaError("Media duration probe failed")
    try:
        duration_seconds = float(result.stdout.strip())
    except ValueError as exc:
        raise MediaError("Media duration probe returned malformed output") from exc
    return _validated_media_duration_seconds(
        duration_seconds,
        max_duration_seconds=config.max_duration_seconds,
    )


def _validated_media_duration_seconds(
    duration_seconds: float,
    *,
    max_duration_seconds: float,
) -> float:
    """Admit only finite positive media durations inside the configured bound."""
    if isinstance(duration_seconds, bool) or isinstance(max_duration_seconds, bool):
        raise MediaError("Media duration probe returned invalid duration")
    try:
        normalized_duration = float(duration_seconds)
        normalized_limit = float(max_duration_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MediaError("Media duration probe returned invalid duration") from exc
    if (
        not math.isfinite(normalized_duration)
        or normalized_duration <= 0
        or not math.isfinite(normalized_limit)
        or normalized_limit <= 0
    ):
        raise MediaError("Media duration probe returned invalid duration")
    if normalized_duration > normalized_limit:
        raise MediaDurationExceededError("Media duration exceeds configured limit")
    return normalized_duration


def _faster_whisper_worker(
    source: MediaSource,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    deadline = time.monotonic() + timeout_seconds

    def remaining_seconds() -> float:
        remaining = deadline - time.monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            raise MediaTimeoutError("Media evidence extraction timed out")
        return remaining

    try:
        with _suppress_output():
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                _put_payload(queue, {"status": "unavailable"})
                return

            model_ref = config.model_path or config.model_name
            if model_ref is None:
                _put_payload(queue, {"status": "unavailable"})
                return

            cleanup_path: str | None = None
            if isinstance(source, bytes):
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
                    temp_file.write(source)
                    temp_path = temp_file.name
                cleanup_path = temp_path
            else:
                temp_path = str(Path(source))
            try:
                if deadline - time.monotonic() <= 0:
                    _put_payload(queue, {"status": "timeout"})
                    return
                model = WhisperModel(
                    model_ref,
                    device="cpu",
                    compute_type=config.compute_type,
                    cpu_threads=FASTER_WHISPER_CPU_THREADS,
                    num_workers=FASTER_WHISPER_NUM_WORKERS,
                    local_files_only=not config.allow_model_download,
                )
                segments, _info = model.transcribe(
                    temp_path,
                    language=config.language,
                    beam_size=1,
                    vad_filter=False,
                )
                payload_segments = [
                    {
                        "start_ms": int(float(segment.start) * 1000),
                        "end_ms": int(float(segment.end) * 1000),
                        "text": str(segment.text),
                    }
                    for segment in segments
                ]
                remaining_seconds()
                if not config._dual_track_required:
                    _put_payload(queue, {"status": "success", "segments": payload_segments})
                    return

                if config._validated_duration_seconds is None:
                    frame_failure = failed_frame_ocr_receipt(
                        code="MEDIA_FRAME_OCR_INCOMPLETE",
                        duration_ms=0,
                    )
                    _put_payload(
                        queue,
                        {
                            "status": "success",
                            "segments": payload_segments,
                            "media_frame_ocr_receipt": frame_failure,
                        },
                    )
                    return

                duration_ms = max(1, int(round(config._validated_duration_seconds * 1_000)))
                stt_segments = [
                    MediaSegmentText(
                        start_ms=cast(int, item["start_ms"]),
                        end_ms=cast(int, item["end_ms"]),
                        text=str(item["text"]),
                    )
                    for item in payload_segments
                ]
                stt_receipt = _build_stt_receipt(stt_segments)
                try:
                    frame_extraction = extract_video_frame_ocr(
                        temp_path,
                        duration_ms=duration_ms,
                        ffmpeg_binary=config.ffmpeg_binary,
                        ocr_config=config.frame_ocr_config,
                        remaining_seconds=remaining_seconds,
                    )
                except MediaTimeoutError:
                    _put_payload(
                        queue,
                        {
                            "status": "success",
                            "duration_ms": duration_ms,
                            "segments": payload_segments,
                            "media_stt_receipt": stt_receipt,
                            "media_frame_ocr_receipt": failed_frame_ocr_receipt(
                                code="MEDIA_FRAME_OCR_TIMEOUT",
                                duration_ms=duration_ms,
                            ),
                        },
                    )
                    return
                except VideoFrameOcrError as exc:
                    _put_payload(
                        queue,
                        {
                            "status": "success",
                            "duration_ms": duration_ms,
                            "segments": payload_segments,
                            "media_stt_receipt": stt_receipt,
                            "media_frame_ocr_receipt": failed_frame_ocr_receipt(
                                code=exc.code,
                                duration_ms=duration_ms,
                            ),
                        },
                    )
                    return
                remaining_seconds()
                _put_payload(
                    queue,
                    {
                        "status": "success",
                        "duration_ms": duration_ms,
                        "segments": payload_segments,
                        "frame_sample_ms": list(frame_extraction.sample_ms),
                        "frame_ocr_segments": [
                            segment.to_payload() for segment in frame_extraction.segments
                        ],
                        "media_stt_receipt": stt_receipt,
                        "media_frame_ocr_receipt": dict(frame_extraction.receipt),
                    },
                )
            finally:
                if cleanup_path is not None:
                    with contextlib.suppress(OSError):
                        os.unlink(cleanup_path)
    except MediaTimeoutError:
        _put_payload(queue, {"status": "timeout"})
    except Exception:
        _put_payload(queue, {"status": "failed"})


def _build_stt_receipt(segments: list[MediaSegmentText]) -> dict[str, object]:
    evidence = _stt_evidence_payload(segments)
    return {
        "schema_version": "media-stt-receipt-v1",
        "status": "completed",
        "segment_count": len(segments),
        "evidence_span_count": len(evidence),
        "evidence_sha256": canonical_sha256(evidence),
    }


def _put_payload(queue: Any, payload: dict[str, object]) -> None:
    queue.put(payload)


def _terminate_process_tree(process: mp.Process, *, timeout_seconds: float) -> None:
    pid = process.pid
    if pid is None:
        process.terminate()
        return
    try:
        import psutil

        root = psutil.Process(pid)
        children = root.children(recursive=True)
        for child in children:
            child.terminate()
        _gone, alive = psutil.wait_procs(
            children,
            timeout=max(0.0, timeout_seconds),
        )
        for child in alive:
            child.kill()
    except Exception:
        process.terminate()
        return
    process.terminate()


def _suppress_output() -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def suppress() -> Iterator[None]:
        with (
            open(os.devnull, "w", encoding="utf-8") as devnull,
            contextlib.redirect_stdout(devnull),
            contextlib.redirect_stderr(devnull),
        ):
            yield

    return suppress()


def _media_error(code: ParseErrorCode, message: str) -> ParseResult:
    return ParseResult(
        doc_type="MEDIA",
        success=False,
        errors=[ParseError(code=code, message=message, details={})],
    )


def _media_frame_error_message(code: ParseErrorCode) -> str:
    return {
        ParseErrorCode.MEDIA_FRAME_OCR_UNAVAILABLE: "Video frame OCR is unavailable",
        ParseErrorCode.MEDIA_FRAME_OCR_TIMEOUT: "Video frame OCR timed out",
        ParseErrorCode.MEDIA_FRAME_OCR_FAILED: "Video frame OCR failed",
        ParseErrorCode.MEDIA_FRAME_OCR_INCOMPLETE: "Video frame OCR did not complete",
        ParseErrorCode.MEDIA_FRAME_SAMPLE_LIMIT_EXCEEDED: (
            "Video frame schedule exceeds the admitted sample limit"
        ),
    }.get(code, "Video frame OCR failed")


def _compute_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
