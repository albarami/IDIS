"""Task 4 RED contract for bounded path-based MP4 transcription.

The large-file path must hand one already-spooled file to ffprobe and the
faster-whisper worker.  It must not pickle hundreds of MiB to a child process
or make separate temporary copies for duration probing and transcription.
"""

from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path
from typing import Any

import pytest

from idis.parsers import media
from idis.parsers.base import ParseErrorCode, ParseLimits
from idis.parsers.media import (
    FasterWhisperMediaAdapter,
    FasterWhisperMediaConfig,
    MediaConfig,
    MediaSegmentText,
    MediaTimeoutError,
    parse_media,
)

_MEDIA_MARKER = b"fictional-path-media-fixture"


def _required(name: str) -> Any:
    value = getattr(media, name, None)
    assert value is not None, f"Task 4 production contract is missing media.{name}"
    return value


def _path_success_worker(
    source_path: object,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    """Spawn-safe worker that fails unless it receives the original path."""
    del config, timeout_seconds
    if not isinstance(source_path, (str, Path)):
        queue.put({"status": "failed"})
        return
    try:
        with Path(source_path).open("rb") as stream:
            marker = stream.read(len(_MEDIA_MARKER))
    except OSError:
        queue.put({"status": "failed"})
        return
    if marker != _MEDIA_MARKER:
        queue.put({"status": "failed"})
        return
    queue.put(
        {
            "status": "success",
            "segments": [
                {"start_ms": 0, "end_ms": 900, "text": "fictional segment one"},
                {"start_ms": 900, "end_ms": 1800, "text": "fictional segment two"},
            ],
        }
    )


def _path_slow_worker(
    source_path: object,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del source_path, config, timeout_seconds, queue
    time.sleep(5)


class _PathDurationProbe:
    def __init__(self, expected_path: Path) -> None:
        self.expected_path = expected_path.resolve()
        self.paths: list[Path] = []

    def __call__(
        self,
        source_path: object,
        config: FasterWhisperMediaConfig,
        timeout_seconds: float,
    ) -> float:
        del config, timeout_seconds
        assert isinstance(source_path, (str, Path))
        resolved = Path(source_path).resolve()
        self.paths.append(resolved)
        assert resolved == self.expected_path
        return 1.8


class _OverlapAdapter:
    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        del data, timeout_seconds
        return [
            MediaSegmentText(start_ms=0, end_ms=1000, text="fictional first"),
            MediaSegmentText(start_ms=500, end_ms=900, text="fictional overlap"),
        ]


def _model_dir(tmp_path: Path) -> Path:
    model = tmp_path / "fictional-model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.bin").write_bytes(b"fictional-model")
    return model


def _media_file(tmp_path: Path) -> Path:
    path = tmp_path / "fictional-video.mp4"
    path.write_bytes(_MEDIA_MARKER + b"\x00" * 64)
    return path


def _adapter(
    tmp_path: Path,
    source_path: Path,
    *,
    worker_target: Any = _path_success_worker,
) -> tuple[FasterWhisperMediaAdapter, _PathDurationProbe]:
    duration_probe = _PathDurationProbe(source_path)
    adapter = FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_path=str(_model_dir(tmp_path)),
            allow_model_download=False,
            max_duration_seconds=10,
        ),
        binary_resolver=lambda _name: "fictional-binary",
        duration_probe=duration_probe,
        worker_target=worker_target,
    )
    return adapter, duration_probe


def test_ffprobe_and_worker_consume_same_source_path_without_temp_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _media_file(tmp_path)
    adapter, duration_probe = _adapter(tmp_path, source)
    before = {path.resolve() for path in tmp_path.rglob("*")}

    def forbidden_tempfile(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("path-based media must not create another full-file temp copy")

    monkeypatch.setattr(media.tempfile, "NamedTemporaryFile", forbidden_tempfile)
    extract_file = _required("extract_media_file")
    segments = extract_file(adapter, source, timeout_seconds=2)

    assert duration_probe.paths == [source.resolve()]
    assert [(segment.start_ms, segment.end_ms) for segment in segments] == [
        (0, 900),
        (900, 1800),
    ]
    assert {path.resolve() for path in tmp_path.rglob("*")} == before


def test_parse_media_file_produces_ordered_timecode_spans_without_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _media_file(tmp_path)
    adapter, _duration_probe = _adapter(tmp_path, source)

    def forbidden_read_bytes(self: Path) -> bytes:
        raise AssertionError(f"strict media path must not materialize all bytes: {self.suffix}")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    parse_media_file = _required("parse_media_file")
    result = parse_media_file(
        source,
        size_bytes=source.stat().st_size,
        limits=ParseLimits(max_bytes=1024),
        media_config=MediaConfig(enabled=True, adapter=adapter, timeout_seconds=2),
    )

    assert result.success is True
    assert [span.span_type for span in result.spans] == ["TIMECODE", "TIMECODE"]
    assert [span.locator for span in result.spans] == [
        {"start_ms": 0, "end_ms": 900, "source": "media_transcript"},
        {"start_ms": 900, "end_ms": 1800, "source": "media_transcript"},
    ]


def test_parse_media_rejects_overlapping_or_regressing_timecodes() -> None:
    result = parse_media(
        b"fictional-media",
        media_config=MediaConfig(enabled=True, adapter=_OverlapAdapter(), timeout_seconds=1),
    )

    assert result.success is False
    assert [error.code for error in result.errors] == [ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED]


def test_path_media_timeout_terminates_worker_and_keeps_caller_owned_spool(
    tmp_path: Path,
) -> None:
    source = _media_file(tmp_path)
    adapter, _duration_probe = _adapter(tmp_path, source, worker_target=_path_slow_worker)
    extract_file = _required("extract_media_file")
    before_pids = {process.pid for process in mp.active_children()}

    started = time.monotonic()
    with pytest.raises(MediaTimeoutError):
        extract_file(adapter, source, timeout_seconds=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert source.exists(), "the upload/spool owner, not the adapter, controls source cleanup"
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        leaked = [
            process
            for process in mp.active_children()
            if process.pid not in before_pids and process.is_alive()
        ]
        if not leaked:
            break
        time.sleep(0.01)
    assert not [
        process
        for process in mp.active_children()
        if process.pid not in before_pids and process.is_alive()
    ]


def test_parse_media_file_rejects_declared_size_before_adapter_execution(tmp_path: Path) -> None:
    source = _media_file(tmp_path)
    adapter, duration_probe = _adapter(tmp_path, source)
    parse_media_file = _required("parse_media_file")

    result = parse_media_file(
        source,
        size_bytes=1025,
        limits=ParseLimits(max_bytes=1024),
        media_config=MediaConfig(enabled=True, adapter=adapter, timeout_seconds=2),
    )

    assert result.success is False
    assert [error.code for error in result.errors] == [ParseErrorCode.MAX_SIZE_EXCEEDED]
    assert duration_probe.paths == []
