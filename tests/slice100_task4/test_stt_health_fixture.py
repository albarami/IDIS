"""Task 4 RED contract for the fixed, real-inference STT health fixture.

These tests do not run faster-whisper.  The production inference boundary is
exercised with an injected deterministic runner while the committed fixture,
manifest, hashes, normalization, timecode checks, and no-override contract are
all real.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from idis.parsers.media import FasterWhisperMediaConfig, MediaSegmentText
from idis.parsers.ocr import OcrConfig, TesseractOcrAdapter
from idis.parsers.video_frame_ocr import (
    VideoFrameOcrExtraction,
    VideoFrameOcrSegment,
    build_frame_ocr_receipt,
    build_periodic_plus_scenes_schedule,
)
from idis.services import media_health

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "runtime_health" / "stt-human-en-v1.mp4"
MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "runtime_health" / "stt-human-en-v1.json"

FIXTURE_SHA256 = "4c5e8d89642cfc9658b2649362a4eda53e753fa1875a8c0c96d0aafe14fdf370"
MANIFEST_SHA256 = "3a394d3bd0852cdbd61793445a264b1c78b0df164deeb1973601696032ea81b7"
PROVENANCE_SHA256 = "1225d236926961f1f6fc8b2c6f96d599349fd0dbb56c21119bb9c311ab2d0148"
EXPECTED_PHRASE = "media health check one two three"
FRAME_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "runtime_health" / "frame-ocr-health-v1.mp4"
FRAME_MANIFEST_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "runtime_health" / "frame-ocr-health-v1.json"
)
FRAME_FIXTURE_SHA256 = "581a4dc749d576662859019d2006c354b03094733e1156fcf91a96f90df6643c"
FRAME_MANIFEST_SHA256 = "266d1bf36adb38c62fcd49e070cb7dc6453ef33d3a332a564453c58ec8a92ef3"
FRAME_PROVENANCE_SHA256 = "998c66f026f466989e7ad9cdfd225e90fdcb6ef57684936bc85947f37dd0d709"
FRAME_EXPECTED_PHRASE = "media frame ocr health one two three"


def _required(name: str) -> Any:
    value = getattr(media_health, name, None)
    assert value is not None, f"Task 4 production contract is missing media_health.{name}"
    return value


def _manifest_value(manifest: object, name: str) -> object:
    if isinstance(manifest, Mapping):
        return manifest.get(name)
    return getattr(manifest, name, None)


def _status_value(result: object) -> str:
    status = getattr(result, "status", None)
    return str(getattr(status, "value", status)).lower()


def _safe_result_text(result: object) -> str:
    dump_json = getattr(result, "model_dump_json", None)
    if callable(dump_json):
        return str(dump_json())
    safe_summary = getattr(result, "safe_summary", None)
    if callable(safe_summary):
        return json.dumps(safe_summary(), sort_keys=True, default=str)
    return json.dumps(getattr(result, "__dict__", {}), sort_keys=True, default=str)


class _InferenceRunner:
    """Signature-tolerant injected runner; no model or provider is executed."""

    def __init__(self, segments: list[MediaSegmentText]) -> None:
        self._segments = segments
        self.fixture_paths: list[Path] = []
        self.configs: list[FasterWhisperMediaConfig] = []

    def __call__(self, *args: object, **kwargs: object) -> list[MediaSegmentText]:
        fixture = kwargs.get("fixture_path") or kwargs.get("media_path") or kwargs.get("path")
        config = kwargs.get("config")
        if fixture is None and args:
            fixture = args[0]
        if config is None:
            config = next(
                (item for item in args if isinstance(item, FasterWhisperMediaConfig)),
                None,
            )
        assert isinstance(fixture, (str, Path)), "inference runner must receive the fixed file path"
        assert isinstance(config, FasterWhisperMediaConfig)
        self.fixture_paths.append(Path(fixture).resolve())
        self.configs.append(config)
        return list(self._segments)


def _probe_env(model_path: Path) -> dict[str, str]:
    return {
        "IDIS_MEDIA_ADAPTER": "faster-whisper",
        "IDIS_MEDIA_STT_MODEL_PATH": str(model_path),
        "IDIS_MEDIA_STT_ALLOW_DOWNLOAD": "false",
        "IDIS_MEDIA_TIMEOUT_SECONDS": "30",
        "IDIS_MEDIA_LANGUAGE": "en",
        "IDIS_MEDIA_COMPUTE_TYPE": "int8",
        "IDIS_MEDIA_MAX_DURATION_SECONDS": "600",
    }


def _frame_probe_env() -> dict[str, str]:
    return {
        "IDIS_MEDIA_TIMEOUT_SECONDS": "900",
        "IDIS_MEDIA_MAX_DURATION_SECONDS": "300",
        "IDIS_OCR_ENABLED": "true",
        "IDIS_OCR_ADAPTER": "tesseract",
        "IDIS_OCR_MAX_PAGES": "72",
        "IDIS_OCR_TIMEOUT_SECONDS": "120",
        "IDIS_OCR_DPI": "200",
        "IDIS_OCR_LANGUAGE": "eng",
    }


class _FrameOcrRunner:
    """Capture the production frame-OCR boundary without executing a binary."""

    def __init__(
        self,
        *,
        completed_delta: int = 0,
        phrase: str | None = FRAME_EXPECTED_PHRASE,
    ) -> None:
        self._completed_delta = completed_delta
        self._phrase = phrase
        self.fixture_paths: list[Path] = []
        self.ffmpeg_binaries: list[str] = []
        self.ocr_configs: list[OcrConfig] = []

    def __call__(self, *args: object, **kwargs: object) -> VideoFrameOcrExtraction:
        fixture = kwargs.get("source_path")
        duration_ms = kwargs.get("duration_ms")
        ffmpeg_binary = kwargs.get("ffmpeg_binary")
        ocr_config = kwargs.get("ocr_config")
        remaining_seconds = kwargs.get("remaining_seconds")
        if fixture is None and args:
            fixture = args[0]
        assert isinstance(fixture, (str, Path))
        assert isinstance(duration_ms, int)
        assert isinstance(ffmpeg_binary, str)
        assert isinstance(ocr_config, OcrConfig)
        assert callable(remaining_seconds)
        assert remaining_seconds() > 0

        self.fixture_paths.append(Path(fixture).resolve())
        self.ffmpeg_binaries.append(ffmpeg_binary)
        self.ocr_configs.append(ocr_config)
        schedule = build_periodic_plus_scenes_schedule(duration_ms=duration_ms)
        segments = (
            tuple(
                VideoFrameOcrSegment(
                    sample_index=index,
                    start_ms=sample_ms,
                    end_ms=min(duration_ms, sample_ms + 1),
                    text=self._phrase,
                    confidence=0.95,
                )
                for index, sample_ms in enumerate(schedule)
            )
            if self._phrase is not None
            else ()
        )
        receipt = build_frame_ocr_receipt(
            duration_ms=duration_ms,
            sample_ms=schedule,
            segments=segments,
            completed_sample_count=len(schedule) + self._completed_delta,
        )
        return VideoFrameOcrExtraction(
            sample_ms=schedule,
            segments=segments,
            receipt=receipt,
        )


def _model_dir(tmp_path: Path) -> Path:
    model = tmp_path / "fictional-local-stt-model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.bin").write_bytes(b"fictional-model-health-fixture")
    return model


def test_committed_stt_fixture_and_manifest_hashes_are_literal() -> None:
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest() == MANIFEST_SHA256
    assert _required("PINNED_STT_FIXTURE_SHA256") == FIXTURE_SHA256
    assert _required("PINNED_STT_MANIFEST_SHA256") == MANIFEST_SHA256


def test_committed_frame_ocr_fixture_and_manifest_hashes_are_literal() -> None:
    assert hashlib.sha256(FRAME_FIXTURE_PATH.read_bytes()).hexdigest() == FRAME_FIXTURE_SHA256
    assert hashlib.sha256(FRAME_MANIFEST_PATH.read_bytes()).hexdigest() == FRAME_MANIFEST_SHA256
    assert _required("PINNED_FRAME_OCR_FIXTURE_SHA256") == FRAME_FIXTURE_SHA256
    assert _required("PINNED_FRAME_OCR_MANIFEST_SHA256") == FRAME_MANIFEST_SHA256


def test_frame_ocr_manifest_loader_verifies_project_owned_provenance() -> None:
    manifest = _required("load_pinned_frame_ocr_health_manifest")()

    assert _manifest_value(manifest, "schema_version") == ("frame-ocr-health-fixture-manifest-v1")
    assert _manifest_value(manifest, "fixture_id") == "frame-ocr-health-v1"
    assert _manifest_value(manifest, "file_sha256") == FRAME_FIXTURE_SHA256
    assert _manifest_value(manifest, "duration_ms") == 3000
    assert _manifest_value(manifest, "width") == 1280
    assert _manifest_value(manifest, "height") == 720
    assert _manifest_value(manifest, "frame_rate") == "5/1"
    assert _manifest_value(manifest, "sampling_policy") == "periodic-plus-scenes-v1"
    assert _manifest_value(manifest, "expected_normalized_phrase") == FRAME_EXPECTED_PHRASE
    provenance = _manifest_value(manifest, "license_evidence")
    assert isinstance(provenance, str)
    assert hashlib.sha256(provenance.encode("utf-8")).hexdigest() == FRAME_PROVENANCE_SHA256
    assert _manifest_value(manifest, "license_evidence_sha256") == FRAME_PROVENANCE_SHA256


def test_manifest_loader_is_bom_safe_and_verifies_provenance() -> None:
    # The operator-provided JSON intentionally has a UTF-8 BOM.  Loading text as
    # plain utf-8 and then json.loads is not sufficient; the production loader
    # must validate the pinned bytes and parse them BOM-safely.
    assert MANIFEST_PATH.read_bytes().startswith(b"\xef\xbb\xbf")
    manifest = _required("load_pinned_stt_health_manifest")()

    assert _manifest_value(manifest, "schema_version") == "stt-health-fixture-manifest-v1"
    assert _manifest_value(manifest, "fixture_id") == "stt-human-en-v1"
    assert _manifest_value(manifest, "file_sha256") == FIXTURE_SHA256
    assert _manifest_value(manifest, "duration_ms") == 3399
    assert _manifest_value(manifest, "language") == "en"
    assert _manifest_value(manifest, "expected_normalized_phrase") == EXPECTED_PHRASE
    provenance = _manifest_value(manifest, "license_evidence")
    assert isinstance(provenance, str)
    assert hashlib.sha256(provenance.encode("utf-8")).hexdigest() == PROVENANCE_SHA256
    assert _manifest_value(manifest, "license_evidence_sha256") == PROVENANCE_SHA256


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MEDIA, health check 1-2-3!", EXPECTED_PHRASE),
        ("Media health check 1 2 3", EXPECTED_PHRASE),
        ("  media health check one two three  ", EXPECTED_PHRASE),
        ("Edie's media health check one two three", "edie s media health check one two three"),
        ("media health check one two three extra", "media health check one two three extra"),
    ],
)
def test_stt_phrase_normalization_is_exact_not_fuzzy(raw: str, expected: str) -> None:
    normalize: Callable[[str], str] = _required("normalize_stt_health_phrase")
    assert normalize(raw) == expected
    assert (normalize(raw) == EXPECTED_PHRASE) is (expected == EXPECTED_PHRASE)


def test_inference_probe_has_fixed_fixture_and_safe_success_evidence(tmp_path: Path) -> None:
    check = _required("check_media_inference_health")
    parameters = inspect.signature(check).parameters
    for forbidden_override in ("fixture_path", "manifest_path", "fixture_root", "asset_path"):
        assert forbidden_override not in parameters

    runner = _InferenceRunner(
        [
            MediaSegmentText(start_ms=0, end_ms=900, text="Media health"),
            MediaSegmentText(start_ms=900, end_ms=2000, text="check 1-2-3."),
        ]
    )
    model = _model_dir(tmp_path)
    result = check(env=_probe_env(model), inference_runner=runner)

    assert _status_value(result) == "healthy"
    assert getattr(result, "may_proceed", None) is True
    assert runner.fixture_paths == [FIXTURE_PATH.resolve()]
    assert runner.configs[0].allow_model_download is False
    assert getattr(result, "fixture_sha256", None) == FIXTURE_SHA256
    assert getattr(result, "manifest_sha256", None) == MANIFEST_SHA256
    assert getattr(result, "segment_count", None) == 2
    assert getattr(result, "first_start_ms", None) == 0
    assert getattr(result, "final_end_ms", None) == 2000

    safe = _safe_result_text(result)
    assert str(FIXTURE_PATH) not in safe
    assert str(model) not in safe
    assert "Media health" not in safe
    assert "check 1-2-3" not in safe


@pytest.mark.parametrize(
    "segments",
    [
        [],
        [MediaSegmentText(start_ms=1501, end_ms=2000, text=EXPECTED_PHRASE)],
        [MediaSegmentText(start_ms=0, end_ms=3900, text=EXPECTED_PHRASE)],
        [
            MediaSegmentText(start_ms=1000, end_ms=1500, text="media health"),
            MediaSegmentText(start_ms=900, end_ms=2000, text="check one two three"),
        ],
        [MediaSegmentText(start_ms=0, end_ms=2000, text="Edie's health check one two three")],
    ],
)
def test_inference_probe_fails_closed_on_phrase_or_timing_drift(
    tmp_path: Path,
    segments: list[MediaSegmentText],
) -> None:
    check = _required("check_media_inference_health")
    result = check(
        env=_probe_env(_model_dir(tmp_path)),
        inference_runner=_InferenceRunner(segments),
    )

    assert _status_value(result) == "failed"
    assert getattr(result, "may_proceed", None) is False
    assert getattr(result, "error_code", None) == "MEDIA_INFERENCE_PROBE_FAILED"


def test_inference_probe_rejects_download_or_named_model_fallback(tmp_path: Path) -> None:
    check = _required("check_media_inference_health")
    env = _probe_env(_model_dir(tmp_path))
    env["IDIS_MEDIA_STT_ALLOW_DOWNLOAD"] = "true"

    result = check(
        env=env,
        inference_runner=_InferenceRunner(
            [MediaSegmentText(start_ms=0, end_ms=2000, text=EXPECTED_PHRASE)]
        ),
    )

    assert _status_value(result) == "failed"
    assert getattr(result, "may_proceed", None) is False
    assert getattr(result, "error_code", None) == "MEDIA_INFERENCE_PROBE_FAILED"


def test_frame_ocr_probe_uses_fixed_fixture_and_exact_phrase_evidence() -> None:
    check = _required("check_media_frame_ocr_health")
    assert _required("MediaFrameOcrHealthCheck") is not None
    parameters = inspect.signature(check).parameters
    for forbidden_override in ("fixture_path", "manifest_path", "fixture_root", "asset_path"):
        assert forbidden_override not in parameters

    runner = _FrameOcrRunner()
    result = check(
        env=_frame_probe_env(),
        binary_resolver=lambda binary: "C:\\safe-tools\\ffmpeg.exe" if binary == "ffmpeg" else None,
        frame_ocr_runner=runner,
    )

    assert _status_value(result) == "healthy"
    assert getattr(result, "may_proceed", None) is True
    assert getattr(result, "error_code", None) is None
    assert getattr(result, "fixture_sha256", None) == FRAME_FIXTURE_SHA256
    assert getattr(result, "manifest_sha256", None) == FRAME_MANIFEST_SHA256
    assert getattr(result, "sampling_policy", None) == "periodic-plus-scenes-v1"
    assert getattr(result, "duration_ms", None) == 3000
    assert getattr(result, "scheduled_sample_count", 0) > 0
    assert getattr(result, "completed_sample_count", None) == getattr(
        result, "scheduled_sample_count", None
    )
    assert getattr(result, "nonempty_sample_count", None) == getattr(
        result, "scheduled_sample_count", None
    )
    assert getattr(result, "evidence_span_count", None) == getattr(
        result, "scheduled_sample_count", None
    )
    assert len(getattr(result, "schedule_sha256", "")) == 64
    assert len(getattr(result, "config_sha256", "")) == 64

    assert runner.fixture_paths == [FRAME_FIXTURE_PATH.resolve()]
    assert runner.ffmpeg_binaries == ["C:\\safe-tools\\ffmpeg.exe"]
    ocr_config = runner.ocr_configs[0]
    assert ocr_config.enabled is True
    assert ocr_config.max_pages == 72
    assert ocr_config.timeout_seconds == 120
    assert isinstance(ocr_config.adapter, TesseractOcrAdapter)
    assert ocr_config.adapter.dpi == 200
    assert ocr_config.adapter.language == "eng"

    safe = _safe_result_text(result)
    assert str(FRAME_FIXTURE_PATH) not in safe
    assert "safe-tools" not in safe
    assert FRAME_EXPECTED_PHRASE not in safe


@pytest.mark.parametrize("phrase", [None, "fictional phrase drift"])
def test_frame_ocr_probe_rejects_missing_or_drifted_phrase(phrase: str | None) -> None:
    check = _required("check_media_frame_ocr_health")
    result = check(
        env=_frame_probe_env(),
        binary_resolver=lambda _binary: "ffmpeg",
        frame_ocr_runner=_FrameOcrRunner(phrase=phrase),
    )

    assert _status_value(result) == "failed"
    assert getattr(result, "may_proceed", None) is False
    assert getattr(result, "error_code", None) == "MEDIA_FRAME_OCR_PROBE_FAILED"


def test_frame_ocr_probe_rejects_an_incomplete_receipt() -> None:
    check = _required("check_media_frame_ocr_health")
    result = check(
        env=_frame_probe_env(),
        binary_resolver=lambda _binary: "ffmpeg",
        frame_ocr_runner=_FrameOcrRunner(completed_delta=-1),
    )

    assert _status_value(result) == "failed"
    assert getattr(result, "may_proceed", None) is False
    assert getattr(result, "error_code", None) == "MEDIA_FRAME_OCR_PROBE_FAILED"


@pytest.mark.parametrize(
    ("env_update", "resolved_binary"),
    [
        ({"IDIS_OCR_ENABLED": "false"}, "ffmpeg"),
        ({"IDIS_OCR_ADAPTER": "fictional-ocr"}, "ffmpeg"),
        ({"IDIS_OCR_MAX_PAGES": "71"}, "ffmpeg"),
        ({"IDIS_MEDIA_TIMEOUT_SECONDS": "0"}, "ffmpeg"),
        ({"IDIS_MEDIA_MAX_DURATION_SECONDS": "3"}, "ffmpeg"),
        ({}, None),
    ],
)
def test_frame_ocr_probe_fails_closed_on_unadmitted_runtime(
    env_update: dict[str, str],
    resolved_binary: str | None,
) -> None:
    check = _required("check_media_frame_ocr_health")
    env = _frame_probe_env()
    env.update(env_update)
    result = check(
        env=env,
        binary_resolver=lambda _binary: resolved_binary,
        frame_ocr_runner=_FrameOcrRunner(),
    )

    assert _status_value(result) == "failed"
    assert getattr(result, "may_proceed", None) is False
    assert getattr(result, "error_code", None) == "MEDIA_FRAME_OCR_PROBE_FAILED"


def test_frame_ocr_probe_enforces_one_monotonic_deadline() -> None:
    check = _required("check_media_frame_ocr_health")
    ticks = iter((10.0, 910.1))
    result = check(
        env=_frame_probe_env(),
        binary_resolver=lambda _binary: "ffmpeg",
        frame_ocr_runner=_FrameOcrRunner(),
        monotonic=lambda: next(ticks),
    )

    assert _status_value(result) == "failed"
    assert getattr(result, "may_proceed", None) is False
    assert getattr(result, "error_code", None) == "MEDIA_FRAME_OCR_PROBE_FAILED"
