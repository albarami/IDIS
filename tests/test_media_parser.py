"""Tests for config-gated private media parsing."""

from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path
from typing import Any

import pytest

from idis.api.errors import IdisHttpError
from idis.api.routes.documents import _reject_unsupported_upload_format
from idis.parsers.base import ParseErrorCode
from idis.parsers.media import (
    FasterWhisperMediaAdapter,
    FasterWhisperMediaConfig,
    FasterWhisperModelStatus,
    MediaConfig,
    MediaSegmentText,
    parse_media,
    probe_faster_whisper_model,
)
from idis.parsers.registry import parse_bytes


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


def _successful_faster_whisper_worker(
    data: bytes,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del timeout_seconds
    assert data == b"synthetic private mp4"
    assert config.language == "en"
    queue.put(
        {
            "status": "success",
            "segments": [
                {
                    "start_ms": 1000,
                    "end_ms": 2400,
                    "text": "SYNTHETIC MEDIA TRANSCRIPT",
                }
            ],
        }
    )


def _named_model_faster_whisper_worker(
    data: bytes,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del data, timeout_seconds
    assert config.model_name == "tiny.en"
    assert config.model_path is None
    assert config.allow_model_download is True
    queue.put(
        {
            "status": "success",
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "SYNTHETIC NAMED MODEL TRANSCRIPT",
                }
            ],
        }
    )


def _empty_faster_whisper_worker(
    data: bytes,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del data, config, timeout_seconds
    queue.put({"status": "success", "segments": []})


def _failed_faster_whisper_worker(
    data: bytes,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del data, config, timeout_seconds
    queue.put({"status": "failed"})


def _slow_faster_whisper_worker(
    data: bytes,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del data, config, timeout_seconds, queue
    time.sleep(5)


def test_default_upload_admission_still_rejects_mp4_bytes() -> None:
    with pytest.raises(IdisHttpError):
        _reject_unsupported_upload_format(b"\x00\x00\x00\x18ftypmp42", "demo.mp4")


def test_global_parser_registry_does_not_admit_mp4_bytes() -> None:
    result = parse_bytes(b"\x00\x00\x00\x18ftypmp42", filename="synthetic.mp4")

    assert result.success is False
    assert result.doc_type == "UNKNOWN"
    assert [error.code for error in result.errors] == [ParseErrorCode.UNSUPPORTED_FORMAT]


def test_faster_whisper_dependency_probe_is_importable() -> None:
    assert importlib.util.find_spec("faster_whisper") is not None


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


def test_faster_whisper_adapter_missing_ffmpeg_or_ffprobe_returns_unavailable() -> None:
    adapter = FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(model_path="synthetic-model"),
        binary_resolver=lambda _: None,
    )

    result = parse_media(
        b"synthetic private mp4",
        media_config=MediaConfig(enabled=True, adapter=adapter, timeout_seconds=10),
    )

    assert result.success is False
    assert [error.code for error in result.errors] == [
        ParseErrorCode.MEDIA_TRANSCRIPTION_UNAVAILABLE
    ]
    assert "synthetic private mp4" not in str(result.to_dict())


def test_faster_whisper_adapter_missing_model_with_download_disabled_is_unavailable() -> None:
    worker_called = False

    def unexpected_worker(
        data: bytes,
        config: FasterWhisperMediaConfig,
        timeout_seconds: float,
        queue: Any,
    ) -> None:
        nonlocal worker_called
        del data, config, timeout_seconds, queue
        worker_called = True

    adapter = FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_name="tiny.en",
            allow_model_download=False,
        ),
        binary_resolver=lambda _: "synthetic-binary",
        worker_target=unexpected_worker,
    )

    result = parse_media(
        b"synthetic private mp4",
        media_config=MediaConfig(enabled=True, adapter=adapter, timeout_seconds=10),
    )

    assert result.success is False
    assert [error.code for error in result.errors] == [
        ParseErrorCode.MEDIA_TRANSCRIPTION_UNAVAILABLE
    ]
    assert worker_called is False


def test_faster_whisper_model_probe_is_path_free_and_download_gated(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    invalid_model_path = tmp_path / "empty-model"
    missing = probe_faster_whisper_model(
        FasterWhisperMediaConfig(model_path=str(model_path), allow_model_download=False)
    )
    invalid_model_path.mkdir()
    invalid = probe_faster_whisper_model(
        FasterWhisperMediaConfig(model_path=str(invalid_model_path), allow_model_download=False)
    )
    model_path.mkdir()
    (model_path / "model.bin").write_text("synthetic model", encoding="utf-8")
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    local = probe_faster_whisper_model(
        FasterWhisperMediaConfig(model_path=str(model_path), allow_model_download=False)
    )
    named_disabled = probe_faster_whisper_model(
        FasterWhisperMediaConfig(model_name="tiny.en", allow_model_download=False)
    )
    named_allowed = probe_faster_whisper_model(
        FasterWhisperMediaConfig(model_name="tiny.en", allow_model_download=True)
    )

    assert missing.can_attempt is False
    assert missing.status == FasterWhisperModelStatus.MODEL_UNAVAILABLE
    assert invalid.can_attempt is False
    assert invalid.status == FasterWhisperModelStatus.MODEL_UNAVAILABLE
    assert local.can_attempt is True
    assert local.status == FasterWhisperModelStatus.LOCAL_MODEL_READY
    assert named_disabled.can_attempt is False
    assert named_allowed.can_attempt is True
    assert named_allowed.status == FasterWhisperModelStatus.DOWNLOAD_ALLOWED
    assert str(model_path) not in str(local)


def test_faster_whisper_adapter_named_model_download_is_explicit_and_injectable() -> None:
    adapter = FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_name="tiny.en",
            allow_model_download=True,
        ),
        binary_resolver=lambda _: "synthetic-binary",
        duration_probe=lambda _data, _config, _timeout: 1.0,
        worker_target=_named_model_faster_whisper_worker,
    )

    result = parse_media(
        b"synthetic private mp4",
        media_config=MediaConfig(enabled=True, adapter=adapter, timeout_seconds=10),
    )

    assert result.success is True
    assert [span.span_type for span in result.spans] == ["TIMECODE"]
    assert "synthetic private mp4" not in str(result.to_dict())


def test_faster_whisper_adapter_timeout_failure_empty_and_success_are_safe(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "model.bin").write_text("synthetic model", encoding="utf-8")
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    base_config = FasterWhisperMediaConfig(
        model_path=str(model_path),
        allow_model_download=False,
        max_duration_seconds=60,
    )

    timeout_result = parse_media(
        b"synthetic private mp4",
        media_config=MediaConfig(
            enabled=True,
            adapter=FasterWhisperMediaAdapter(
                config=base_config,
                binary_resolver=lambda _: "synthetic-binary",
                duration_probe=lambda _data, _config, _timeout: 1.0,
                worker_target=_slow_faster_whisper_worker,
            ),
            timeout_seconds=0.05,
        ),
    )
    failed_result = parse_media(
        b"synthetic private mp4",
        media_config=MediaConfig(
            enabled=True,
            adapter=FasterWhisperMediaAdapter(
                config=base_config,
                binary_resolver=lambda _: "synthetic-binary",
                duration_probe=lambda _data, _config, _timeout: 1.0,
                worker_target=_failed_faster_whisper_worker,
            ),
            timeout_seconds=10,
        ),
    )
    empty_result = parse_media(
        b"synthetic private mp4",
        media_config=MediaConfig(
            enabled=True,
            adapter=FasterWhisperMediaAdapter(
                config=base_config,
                binary_resolver=lambda _: "synthetic-binary",
                duration_probe=lambda _data, _config, _timeout: 1.0,
                worker_target=_empty_faster_whisper_worker,
            ),
            timeout_seconds=10,
        ),
    )
    success_result = parse_media(
        b"synthetic private mp4",
        media_config=MediaConfig(
            enabled=True,
            adapter=FasterWhisperMediaAdapter(
                config=base_config,
                binary_resolver=lambda _: "synthetic-binary",
                duration_probe=lambda _data, _config, _timeout: 1.0,
                worker_target=_successful_faster_whisper_worker,
            ),
            timeout_seconds=10,
        ),
    )

    assert [error.code for error in timeout_result.errors] == [
        ParseErrorCode.MEDIA_TRANSCRIPTION_TIMEOUT
    ]
    assert [error.code for error in failed_result.errors] == [
        ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED
    ]
    assert [error.code for error in empty_result.errors] == [ParseErrorCode.MEDIA_NO_TEXT_EXTRACTED]
    assert success_result.success is True
    assert [span.span_type for span in success_result.spans] == ["TIMECODE"]
    assert [span.locator for span in success_result.spans] == [
        {"start_ms": 1000, "end_ms": 2400, "source": "media_transcript"}
    ]
    assert "synthetic private mp4" not in str(success_result.to_dict())


def test_faster_whisper_adapter_duration_too_long_defers_before_transcription(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "model.bin").write_text("synthetic model", encoding="utf-8")
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    worker_called = False

    def unexpected_worker(
        data: bytes,
        config: FasterWhisperMediaConfig,
        timeout_seconds: float,
        queue: Any,
    ) -> None:
        nonlocal worker_called
        del data, config, timeout_seconds, queue
        worker_called = True

    adapter = FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_path=str(model_path),
            allow_model_download=False,
            max_duration_seconds=1,
        ),
        binary_resolver=lambda _: "synthetic-binary",
        duration_probe=lambda _data, _config, _timeout: 2.0,
        worker_target=unexpected_worker,
    )

    result = parse_media(
        b"synthetic private mp4",
        media_config=MediaConfig(enabled=True, adapter=adapter, timeout_seconds=10),
    )

    assert worker_called is False
    assert result.success is False
    assert [error.code for error in result.errors] == [ParseErrorCode.MEDIA_DURATION_EXCEEDED]


@pytest.mark.skipif(
    os.environ.get("IDIS_RUN_REAL_MEDIA_STT") != "1"
    or not os.environ.get("IDIS_MEDIA_STT_MODEL_PATH"),
    reason="real media STT integration is opt-in and requires a local model path",
)
def test_optional_real_faster_whisper_integration_requires_explicit_env() -> None:
    model_path = os.environ["IDIS_MEDIA_STT_MODEL_PATH"]
    adapter = FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_path=model_path,
            allow_model_download=False,
            max_duration_seconds=5,
        )
    )

    result = parse_media(
        b"not a real mp4 fixture",
        media_config=MediaConfig(enabled=True, adapter=adapter, timeout_seconds=5),
    )

    assert result.success is False
    assert [error.code for error in result.errors] in (
        [ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED],
        [ParseErrorCode.MEDIA_NO_TEXT_EXTRACTED],
    )


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
