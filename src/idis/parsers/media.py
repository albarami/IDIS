"""Config-gated media transcription parser boundary for private gate readiness."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from idis.parsers.base import ParseError, ParseErrorCode, ParseLimits, ParseResult, SpanDraft

MAX_MEDIA_SEGMENTS = 500
MAX_MEDIA_SEGMENT_TEXT_CHARS = 20_000


class MediaError(Exception):
    """Base class for safe media adapter failures."""


class MediaTimeoutError(MediaError):
    """Raised by media adapters when transcription exceeds a runtime budget."""


class MediaUnavailableError(MediaError):
    """Raised when media conversion/transcription dependencies are unavailable."""


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


def _media_error(code: ParseErrorCode, message: str) -> ParseResult:
    return ParseResult(
        doc_type="MEDIA",
        success=False,
        errors=[ParseError(code=code, message=message, details={})],
    )


def _compute_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
