"""Task 4 fail-closed duration validation for real MP4 transcription."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from idis.parsers import media
from idis.parsers.media import (
    FasterWhisperMediaAdapter,
    FasterWhisperMediaConfig,
    MediaDurationExceededError,
    MediaError,
)


def _model_dir(tmp_path: Path) -> Path:
    model = tmp_path / "fictional-model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.bin").write_bytes(b"fictional-model")
    return model


def _source_file(tmp_path: Path) -> Path:
    source = tmp_path / "fictional-video.mp4"
    source.write_bytes(b"fictional-media")
    return source


def _adapter(
    tmp_path: Path,
    *,
    duration_probe: Any | None = None,
    worker_called: list[bool],
) -> FasterWhisperMediaAdapter:
    def forbidden_model_fingerprinter(_path: Any, _timeout: float) -> str:
        raise AssertionError("invalid media duration reached model fingerprinting")

    def forbidden_inference(
        _target: Any,
        _args: tuple[object, ...],
        _timeout_seconds: float,
    ) -> dict[str, object]:
        worker_called.append(True)
        raise AssertionError("invalid media duration reached faster-whisper inference")

    return FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_path=str(_model_dir(tmp_path)),
            allow_model_download=False,
            max_duration_seconds=10.0,
        ),
        binary_resolver=lambda _name: "fictional-binary",
        duration_probe=duration_probe,
        model_fingerprinter=forbidden_model_fingerprinter,
        worker_runner=forbidden_inference,
    )


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0])
def test_injected_duration_probe_rejects_invalid_values_before_inference(
    tmp_path: Path,
    duration: float,
) -> None:
    worker_called: list[bool] = []
    adapter = _adapter(
        tmp_path,
        duration_probe=lambda _source, _config, _timeout: duration,
        worker_called=worker_called,
    )

    with pytest.raises(MediaError, match="invalid") as exc_info:
        adapter.extract_file(_source_file(tmp_path), timeout_seconds=2.0)

    assert type(exc_info.value) is MediaError
    assert worker_called == []


def test_injected_duration_probe_rejects_value_above_configured_limit_before_inference(
    tmp_path: Path,
) -> None:
    worker_called: list[bool] = []
    adapter = _adapter(
        tmp_path,
        duration_probe=lambda _source, _config, _timeout: 10.001,
        worker_called=worker_called,
    )

    with pytest.raises(MediaDurationExceededError):
        adapter.extract_file(_source_file(tmp_path), timeout_seconds=2.0)

    assert worker_called == []


@pytest.mark.parametrize("stdout", ["nan\n", "inf\n", "-inf\n", "0\n", "-0.25\n"])
def test_ffprobe_stdout_rejects_invalid_duration_before_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    worker_called: list[bool] = []
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout),
    )
    adapter = _adapter(tmp_path, worker_called=worker_called)

    with pytest.raises(MediaError, match="invalid") as exc_info:
        adapter.extract_file(_source_file(tmp_path), timeout_seconds=2.0)

    assert type(exc_info.value) is MediaError
    assert worker_called == []


def test_ffprobe_stdout_rejects_duration_above_configured_limit_before_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_called: list[bool] = []
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="10.001\n"),
    )
    adapter = _adapter(tmp_path, worker_called=worker_called)

    with pytest.raises(MediaDurationExceededError):
        adapter.extract_file(_source_file(tmp_path), timeout_seconds=2.0)

    assert worker_called == []
