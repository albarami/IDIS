"""Tests for config-gated private media parsing."""

from __future__ import annotations

import pytest

from idis.api.errors import IdisHttpError
from idis.api.routes.documents import _reject_unsupported_upload_format
from idis.parsers.base import ParseErrorCode
from idis.parsers.media import MediaConfig, MediaSegmentText, parse_media


class _SuccessfulMediaAdapter:
    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        assert data == b"private mp4 bytes"
        assert timeout_seconds == 10
        return [
            MediaSegmentText(
                start_ms=1000,
                end_ms=2500,
                text="SLICE37 PRIVATE MEDIA TRANSCRIPT",
            )
        ]


class _TimeoutMediaAdapter:
    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        del data, timeout_seconds
        from idis.parsers.media import MediaTimeoutError

        raise MediaTimeoutError("media transcription timed out")


class _FailedMediaAdapter:
    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        del data, timeout_seconds
        from idis.parsers.media import MediaError

        raise MediaError("media transcription failed")


def test_default_upload_admission_still_rejects_mp4_bytes() -> None:
    with pytest.raises(IdisHttpError):
        _reject_unsupported_upload_format(b"\x00\x00\x00\x18ftypmp42", "demo.mp4")


def test_media_parser_disabled_returns_safe_unavailable() -> None:
    result = parse_media(b"private mp4 bytes")

    encoded = str(result.to_dict())
    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [
        ParseErrorCode.MEDIA_TRANSCRIPTION_UNAVAILABLE
    ]
    assert result.errors[0].details == {}
    assert "private mp4 bytes" not in encoded


def test_media_adapter_success_creates_safe_timecode_spans() -> None:
    result = parse_media(
        b"private mp4 bytes",
        media_config=MediaConfig(
            enabled=True,
            adapter=_SuccessfulMediaAdapter(),
            timeout_seconds=10,
        ),
    )

    assert result.success is True
    assert result.doc_type == "MEDIA"
    assert result.errors == []
    assert [span.span_type for span in result.spans] == ["TIMECODE"]
    assert [span.locator for span in result.spans] == [
        {"start_ms": 1000, "end_ms": 2500, "source": "media_transcript"}
    ]
    assert result.metadata["media_transcription_performed"] is True


def test_media_adapter_timeout_and_failure_are_structured_and_safe() -> None:
    timeout_result = parse_media(
        b"confidential timeout media",
        media_config=MediaConfig(enabled=True, adapter=_TimeoutMediaAdapter()),
    )
    failed_result = parse_media(
        b"confidential failed media",
        media_config=MediaConfig(enabled=True, adapter=_FailedMediaAdapter()),
    )

    timeout_encoded = str(timeout_result.to_dict())
    failed_encoded = str(failed_result.to_dict())
    assert [error.code for error in timeout_result.errors] == [
        ParseErrorCode.MEDIA_TRANSCRIPTION_TIMEOUT
    ]
    assert [error.code for error in failed_result.errors] == [
        ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED
    ]
    assert timeout_result.errors[0].details == {}
    assert failed_result.errors[0].details == {}
    assert "confidential timeout media" not in timeout_encoded
    assert "confidential failed media" not in failed_encoded


def test_media_adapter_segment_results_are_validated_before_span_creation() -> None:
    class InvalidMediaAdapter:
        def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
            del data, timeout_seconds
            return [MediaSegmentText(start_ms=2500, end_ms=1000, text="invalid")]

    result = parse_media(
        b"private mp4 bytes",
        media_config=MediaConfig(enabled=True, adapter=InvalidMediaAdapter()),
    )

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED]
    assert result.errors[0].details == {}
