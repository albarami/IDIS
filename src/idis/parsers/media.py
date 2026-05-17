"""Config-gated media transcription parser boundary for private gate readiness."""

from __future__ import annotations

import contextlib
import hashlib
import multiprocessing as mp
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from queue import Empty
from typing import Any, Protocol

from idis.parsers.base import ParseError, ParseErrorCode, ParseLimits, ParseResult, SpanDraft

MAX_MEDIA_SEGMENTS = 500
MAX_MEDIA_SEGMENT_TEXT_CHARS = 20_000
MAX_MEDIA_DURATION_SECONDS = 600.0
FASTER_WHISPER_ADAPTER_NAME = "faster-whisper"
FASTER_WHISPER_REQUIRED_MODEL_FILES = frozenset({"model.bin", "config.json"})


class MediaError(Exception):
    """Base class for safe media adapter failures."""


class MediaTimeoutError(MediaError):
    """Raised by media adapters when transcription exceeds a runtime budget."""


class MediaUnavailableError(MediaError):
    """Raised when media conversion/transcription dependencies are unavailable."""


class MediaDurationExceededError(MediaError):
    """Raised when media duration exceeds the configured private gate bound."""


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


@dataclass(frozen=True, slots=True)
class FasterWhisperModelProbe:
    """Path-free faster-whisper model probe result."""

    status: FasterWhisperModelStatus
    can_attempt: bool


FasterWhisperWorkerTarget = Callable[
    [bytes, FasterWhisperMediaConfig, float, Any],
    None,
]
BinaryResolver = Callable[[str], str | None]
DurationProbe = Callable[[bytes, FasterWhisperMediaConfig, float], float]


class FasterWhisperMediaAdapter:
    """Process-isolated private media adapter backed by faster-whisper."""

    def __init__(
        self,
        *,
        config: FasterWhisperMediaConfig,
        worker_target: FasterWhisperWorkerTarget | None = None,
        binary_resolver: BinaryResolver | None = None,
        duration_probe: DurationProbe | None = None,
    ) -> None:
        self._config = config
        self._worker_target = worker_target or _faster_whisper_worker
        self._binary_resolver = binary_resolver or shutil.which
        self._duration_probe = duration_probe or _ffprobe_duration_seconds

    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        """Return transcribed text for bounded media bytes."""
        if timeout_seconds <= 0:
            raise MediaTimeoutError("Media transcription timed out")
        if self._binary_resolver(self._config.ffmpeg_binary) is None:
            raise MediaUnavailableError("ffmpeg is unavailable")
        if self._binary_resolver(self._config.ffprobe_binary) is None:
            raise MediaUnavailableError("ffprobe is unavailable")
        if not probe_faster_whisper_model(self._config).can_attempt:
            raise MediaUnavailableError("Media transcription model is unavailable")

        duration_seconds = self._duration_probe(data, self._config, timeout_seconds)
        if duration_seconds > self._config.max_duration_seconds:
            raise MediaDurationExceededError("Media duration exceeds configured limit")

        queue: mp.Queue[dict[str, object]] = mp.Queue(maxsize=1)
        process = mp.Process(
            target=self._worker_target,
            args=(data, self._config, timeout_seconds, queue),
        )
        process.start()
        process.join(timeout_seconds)
        if process.is_alive():
            _terminate_process_tree(process)
            process.join()
            raise MediaTimeoutError("Media transcription timed out")

        payload = _read_worker_payload(queue)
        return _segments_from_worker_payload(payload)


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


def _parse_media_segments(segments: list[MediaSegmentText]) -> ParseResult:
    if len(segments) > MAX_MEDIA_SEGMENTS:
        return _media_error(
            ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
            "Media transcription returned invalid segment results",
        )

    spans: list[SpanDraft] = []
    total_text_length = 0
    previous_start_ms = -1
    for segment in segments:
        if not _valid_segment(segment=segment, previous_start_ms=previous_start_ms):
            return _media_error(
                ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED,
                "Media transcription returned invalid segment results",
            )
        previous_start_ms = segment.start_ms
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

    return ParseResult(
        doc_type="MEDIA",
        success=True,
        spans=spans,
        metadata={
            "span_count": len(spans),
            "total_text_length": total_text_length,
            "media_transcription_performed": True,
            "media_segment_count": len(segments),
        },
    )


def _valid_segment(*, segment: MediaSegmentText, previous_start_ms: int) -> bool:
    if not isinstance(segment.start_ms, int) or not isinstance(segment.end_ms, int):
        return False
    if segment.start_ms < 0 or segment.end_ms < segment.start_ms:
        return False
    if segment.start_ms < previous_start_ms:
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


def _valid_local_faster_whisper_model_dir(model_path: Path) -> bool:
    if not model_path.exists() or not model_path.is_dir():
        return False
    return all(
        (model_path / filename).is_file() for filename in FASTER_WHISPER_REQUIRED_MODEL_FILES
    )


def _read_worker_payload(queue: mp.Queue[dict[str, object]]) -> dict[str, object]:
    try:
        return queue.get(timeout=0.5)
    except Empty as exc:
        raise MediaError("Media transcription worker produced no result") from exc


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


def _ffprobe_duration_seconds(
    data: bytes,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
) -> float:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
        temp_file.write(data)
        temp_path = temp_file.name
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
            timeout=max(1.0, min(timeout_seconds, 10.0)),
        )
    except FileNotFoundError as exc:
        raise MediaUnavailableError("ffprobe is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaTimeoutError("Media duration probe timed out") from exc
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)

    if result.returncode != 0:
        raise MediaError("Media duration probe failed")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise MediaError("Media duration probe returned malformed output") from exc


def _faster_whisper_worker(
    data: bytes,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    deadline = time.monotonic() + timeout_seconds
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

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
                temp_file.write(data)
                temp_path = temp_file.name
            try:
                if deadline - time.monotonic() <= 0:
                    _put_payload(queue, {"status": "timeout"})
                    return
                model = WhisperModel(
                    model_ref,
                    device="cpu",
                    compute_type=config.compute_type,
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
                _put_payload(queue, {"status": "success", "segments": payload_segments})
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(temp_path)
    except Exception:
        _put_payload(queue, {"status": "failed"})


def _put_payload(queue: Any, payload: dict[str, object]) -> None:
    queue.put(payload)


def _terminate_process_tree(process: mp.Process) -> None:
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
        root.terminate()
        _gone, alive = psutil.wait_procs([*children, root], timeout=2)
        for child in alive:
            child.kill()
    except Exception:
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


def _compute_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
