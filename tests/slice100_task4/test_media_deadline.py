"""Task 4 contract for one wall-clock media transcription deadline.

The test uses only injected phases and a deterministic clock.  It never loads
faster-whisper, invokes ffprobe, or reads the operator's model.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from idis.parsers.base import ParseErrorCode
from idis.parsers.media import (
    FasterWhisperMediaAdapter,
    FasterWhisperMediaConfig,
    MediaConfig,
    MediaTimeoutError,
    fingerprint_local_faster_whisper_model,
    parse_media,
)


class _FakeMonotonicClock:
    def __init__(self) -> None:
        self._now = 100.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _model_dir(tmp_path: Path) -> Path:
    model = tmp_path / "fictional-local-model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.bin").write_bytes(b"fictional-model")
    return model


def test_media_phases_share_one_monotonic_deadline(tmp_path: Path) -> None:
    """Cumulative phase time, not any individual phase, exhausts the budget."""
    clock = _FakeMonotonicClock()
    phase_budgets: list[tuple[str, float]] = []
    fingerprint_calls = 0

    def duration_probe(
        _source: object,
        _config: FasterWhisperMediaConfig,
        remaining_seconds: float,
    ) -> float:
        phase_budgets.append(("duration", remaining_seconds))
        clock.advance(0.20)
        return 0.9

    def fingerprint(_model_path: str | Path, remaining_seconds: float) -> str:
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        phase = "pre_fingerprint" if fingerprint_calls == 1 else "post_fingerprint"
        phase_budgets.append((phase, remaining_seconds))
        clock.advance(0.20 if fingerprint_calls == 1 else 0.41)
        return hashlib.sha256(b"stable-fictional-model").hexdigest()

    def worker_runner(
        _target: Callable[..., None],
        args: tuple[object, ...],
        remaining_seconds: float,
    ) -> dict[str, object]:
        assert args[2] == remaining_seconds
        phase_budgets.append(("worker", remaining_seconds))
        clock.advance(0.20)
        return {
            "status": "success",
            "segments": [{"start_ms": 0, "end_ms": 900, "text": "must not escape timeout"}],
        }

    adapter = FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_path=str(_model_dir(tmp_path)),
            max_duration_seconds=10,
        ),
        binary_resolver=lambda _name: "fictional-binary",
        duration_probe=duration_probe,
        model_fingerprinter=fingerprint,
        worker_runner=worker_runner,
        monotonic_clock=clock,
    )

    result = parse_media(
        b"fictional-media",
        media_config=MediaConfig(enabled=True, adapter=adapter, timeout_seconds=1.0),
    )

    assert result.success is False
    assert result.spans == []
    assert result.metadata == {}
    assert [error.code for error in result.errors] == [ParseErrorCode.MEDIA_TRANSCRIPTION_TIMEOUT]
    assert [name for name, _budget in phase_budgets] == [
        "duration",
        "pre_fingerprint",
        "worker",
        "post_fingerprint",
    ]
    assert [budget for _name, budget in phase_budgets] == pytest.approx([1.0, 0.8, 0.6, 0.4])
    assert "must not escape timeout" not in str(result.to_dict())


def test_local_model_fingerprint_checks_the_runtime_deadline_while_reading(
    tmp_path: Path,
) -> None:
    """The production tree hash polls the common deadline during file reads."""
    model = _model_dir(tmp_path)
    checks = 0

    def timeout_check() -> None:
        nonlocal checks
        checks += 1
        if checks >= 6:
            raise MediaTimeoutError("safe timeout")

    with pytest.raises(MediaTimeoutError, match="safe timeout"):
        fingerprint_local_faster_whisper_model(
            model,
            timeout_check=timeout_check,
        )

    assert checks >= 6
