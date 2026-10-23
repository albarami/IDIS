"""Strict media/STT runtime health checks (ffmpeg/ffprobe/faster-whisper/model).

Mirrors `ocr_health.py` (and the `rag/*_health.py` pattern): an env-driven check with
injectable probes that returns a sanitized Pydantic result which never exposes raw
paths, env values, transcript text, model names, command output, or secrets.

Media stays config-gated and off by default; ``DISABLED`` (no `IDIS_MEDIA_ADAPTER`) is
an expected non-error state. This module is standalone strict health: the wiring into
strict readiness / provisioning truth lives in the ``services/runs`` layer, not here.
The generic check does not load models, while the explicit pinned-fixture checks run
real STT inference and real ffmpeg/Tesseract frame OCR for strict acceptance.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from idis.parsers.media import (
    FasterWhisperMediaAdapter,
    FasterWhisperMediaConfig,
    FasterWhisperModelStatus,
    MediaSegmentText,
    extract_media_file,
    probe_faster_whisper_model,
)
from idis.parsers.ocr import OcrConfig, TesseractOcrAdapter
from idis.parsers.video_frame_ocr import (
    FRAME_OCR_RECEIPT_SCHEMA_VERSION,
    FRAME_PERIOD_MS,
    FRAME_SAMPLING_POLICY,
    FRAME_SCENE_THRESHOLD,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_SAMPLES,
    MAX_FRAME_WIDTH,
    VideoFrameOcrExtraction,
    VideoFrameOcrSegment,
    build_frame_ocr_receipt,
    build_periodic_plus_scenes_schedule,
    canonical_sha256,
    extract_video_frame_ocr,
)

IDIS_MEDIA_ADAPTER_ENV = "IDIS_MEDIA_ADAPTER"
IDIS_MEDIA_STT_MODEL_PATH_ENV = "IDIS_MEDIA_STT_MODEL_PATH"
IDIS_MEDIA_STT_MODEL_NAME_ENV = "IDIS_MEDIA_STT_MODEL_NAME"
IDIS_MEDIA_STT_ALLOW_DOWNLOAD_ENV = "IDIS_MEDIA_STT_ALLOW_DOWNLOAD"
IDIS_MEDIA_TIMEOUT_SECONDS_ENV = "IDIS_MEDIA_TIMEOUT_SECONDS"
IDIS_MEDIA_LANGUAGE_ENV = "IDIS_MEDIA_LANGUAGE"
IDIS_MEDIA_COMPUTE_TYPE_ENV = "IDIS_MEDIA_COMPUTE_TYPE"
IDIS_MEDIA_MAX_DURATION_SECONDS_ENV = "IDIS_MEDIA_MAX_DURATION_SECONDS"

DEFAULT_MEDIA_ADAPTER = "faster-whisper"

FFMPEG_BINARY = "ffmpeg"
FFPROBE_BINARY = "ffprobe"

# (import name, safe public identifier) — only the safe identifier is ever surfaced.
_REQUIRED_MODULES: tuple[tuple[str, str], ...] = (("faster_whisper", "faster_whisper"),)

_MAX_ERROR_LENGTH = 240
_RUNTIME_PROBE_TIMEOUT_SECONDS = 10
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]+")
_WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s]+")
_UNIX_PATH_PATTERN = re.compile(r"(?<!\w)/[^/\s]+(?:/[^/\s]*)+")

PINNED_STT_FIXTURE_SHA256 = "4c5e8d89642cfc9658b2649362a4eda53e753fa1875a8c0c96d0aafe14fdf370"
PINNED_STT_MANIFEST_SHA256 = "3a394d3bd0852cdbd61793445a264b1c78b0df164deeb1973601696032ea81b7"
PINNED_FRAME_OCR_FIXTURE_SHA256 = "581a4dc749d576662859019d2006c354b03094733e1156fcf91a96f90df6643c"
PINNED_FRAME_OCR_MANIFEST_SHA256 = (
    "266d1bf36adb38c62fcd49e070cb7dc6453ef33d3a332a564453c58ec8a92ef3"
)
_PINNED_STT_PROVENANCE_SHA256 = "1225d236926961f1f6fc8b2c6f96d599349fd0dbb56c21119bb9c311ab2d0148"
_PINNED_STT_EXPECTED_PHRASE = "media health check one two three"
_PINNED_STT_SCHEMA_VERSION = "stt-health-fixture-manifest-v1"
_PINNED_STT_FIXTURE_ID = "stt-human-en-v1"
_PINNED_FRAME_OCR_PROVENANCE_SHA256 = (
    "998c66f026f466989e7ad9cdfd225e90fdcb6ef57684936bc85947f37dd0d709"
)
_PINNED_FRAME_OCR_EXPECTED_PHRASE = "media frame ocr health one two three"
_PINNED_FRAME_OCR_SCHEMA_VERSION = "frame-ocr-health-fixture-manifest-v1"
_PINNED_FRAME_OCR_FIXTURE_ID = "frame-ocr-health-v1"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PINNED_STT_FIXTURE_PATH = (
    _PROJECT_ROOT / "tests" / "fixtures" / "runtime_health" / "stt-human-en-v1.mp4"
)
_PINNED_STT_MANIFEST_PATH = (
    _PROJECT_ROOT / "tests" / "fixtures" / "runtime_health" / "stt-human-en-v1.json"
)
_PINNED_FRAME_OCR_FIXTURE_PATH = (
    _PROJECT_ROOT / "tests" / "fixtures" / "runtime_health" / "frame-ocr-health-v1.mp4"
)
_PINNED_FRAME_OCR_MANIFEST_PATH = (
    _PROJECT_ROOT / "tests" / "fixtures" / "runtime_health" / "frame-ocr-health-v1.json"
)


class MediaHealthProbeError(Exception):
    """Raised by the default runtime probe; carries no command output or paths."""


class MediaBinaryResolver(Protocol):
    """Resolve an executable path for a binary name (presence probe)."""

    def __call__(self, binary_name: str) -> str | None:
        """Return a path when the binary is available, else ``None``."""


class MediaModuleProbe(Protocol):
    """Return whether a Python module is importable."""

    def __call__(self, module_name: str) -> bool:
        """Return True when the module can be imported."""


class MediaModelProbe(Protocol):
    """Return whether an STT model is available for the supplied environment."""

    def __call__(self, env: Mapping[str, str]) -> bool:
        """Return True when a local model is ready or download is allowed."""


class MediaRuntimeProbe(Protocol):
    """Run a minimal media runtime check; raise on timeout/command failure."""

    def __call__(self) -> None:
        """Return ``None`` on success; raise on failure."""


class MediaCommandRunner(Protocol):
    """Run a bounded command; raise on non-zero exit, timeout, or missing binary."""

    def __call__(self, args: Sequence[str]) -> None:
        """Return ``None`` on success; raise on failure (no output is surfaced)."""


class MediaHealthStatus(StrEnum):
    """Safe media/STT health status values."""

    HEALTHY = "healthy"
    DISABLED = "disabled"
    MISSING_DEPENDENCIES = "missing_dependencies"
    FAILED = "failed"


class MediaHealthCheck(BaseModel):
    """Sanitized media/STT runtime health-check result."""

    model_config = ConfigDict(extra="forbid")

    status: MediaHealthStatus
    enabled: bool
    missing_dependencies: list[str] = Field(default_factory=list)
    error: str | None = None

    @classmethod
    def disabled(cls) -> MediaHealthCheck:
        """Return the expected off-by-default result (no media adapter configured)."""
        return cls(status=MediaHealthStatus.DISABLED, enabled=False)

    @classmethod
    def healthy(cls) -> MediaHealthCheck:
        """Return a successful health result."""
        return cls(status=MediaHealthStatus.HEALTHY, enabled=True)

    @classmethod
    def missing(cls, *, dependencies: list[str]) -> MediaHealthCheck:
        """Return a missing-dependencies result with safe identifiers only."""
        return cls(
            status=MediaHealthStatus.MISSING_DEPENDENCIES,
            enabled=True,
            missing_dependencies=sorted(set(dependencies)),
        )

    @classmethod
    def failed(cls, *, error: str | None = None) -> MediaHealthCheck:
        """Return a sanitized failed health result."""
        return cls(
            status=MediaHealthStatus.FAILED,
            enabled=True,
            error=_sanitize_error(error) or "Media health check failed.",
        )


class PinnedSttHealthManifest(BaseModel):
    """Validated manifest for the non-operator-selectable STT health asset."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    fixture_id: str
    file_sha256: str
    duration_ms: int
    language: str
    expected_normalized_phrase: str
    normalization: str
    license_evidence: str
    license_evidence_sha256: str


class PinnedFrameOcrHealthManifest(BaseModel):
    """Validated manifest for the fixed project-owned visual health asset."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    fixture_id: str
    file_name: str
    file_sha256: str
    duration_ms: int
    width: int
    height: int
    frame_rate: str
    expected_normalized_phrase: str
    normalization: str
    sampling_policy: str
    generation_recipe: str
    provenance_note: str
    license_evidence: str
    license_evidence_sha256: str


class MediaInferenceHealthCheck(BaseModel):
    """Content-free result of the explicit pinned-fixture inference probe."""

    model_config = ConfigDict(extra="forbid")

    status: MediaHealthStatus
    may_proceed: bool
    error_code: str | None = None
    fixture_sha256: str | None = None
    manifest_sha256: str | None = None
    model_path_sha256: str | None = None
    model_files_sha256: str | None = None
    segment_count: int = 0
    first_start_ms: int | None = None
    final_end_ms: int | None = None

    @classmethod
    def healthy(
        cls,
        *,
        segment_count: int,
        first_start_ms: int,
        final_end_ms: int,
        model_path_sha256: str,
        model_files_sha256: str,
    ) -> MediaInferenceHealthCheck:
        """Return safe aggregate proof for a successful inference probe."""
        return cls(
            status=MediaHealthStatus.HEALTHY,
            may_proceed=True,
            fixture_sha256=PINNED_STT_FIXTURE_SHA256,
            manifest_sha256=PINNED_STT_MANIFEST_SHA256,
            model_path_sha256=model_path_sha256,
            model_files_sha256=model_files_sha256,
            segment_count=segment_count,
            first_start_ms=first_start_ms,
            final_end_ms=final_end_ms,
        )

    @classmethod
    def failed(cls) -> MediaInferenceHealthCheck:
        """Return the single content-free failure shape."""
        return cls(
            status=MediaHealthStatus.FAILED,
            may_proceed=False,
            error_code="MEDIA_INFERENCE_PROBE_FAILED",
        )


class MediaFrameOcrHealthCheck(BaseModel):
    """Content-free result of real frame OCR over the pinned media fixture."""

    model_config = ConfigDict(extra="forbid")

    status: MediaHealthStatus
    may_proceed: bool
    error_code: str | None = None
    fixture_sha256: str | None = None
    manifest_sha256: str | None = None
    config_sha256: str | None = None
    receipt_schema_version: str | None = None
    sampling_policy: str | None = None
    duration_ms: int | None = None
    scheduled_sample_count: int = 0
    completed_sample_count: int = 0
    nonempty_sample_count: int = 0
    evidence_span_count: int = 0
    schedule_sha256: str | None = None

    @classmethod
    def healthy(
        cls,
        *,
        config_sha256: str,
        duration_ms: int,
        scheduled_sample_count: int,
        completed_sample_count: int,
        nonempty_sample_count: int,
        evidence_span_count: int,
        schedule_sha256: str,
    ) -> MediaFrameOcrHealthCheck:
        """Return safe aggregate proof for a completed frame schedule."""
        return cls(
            status=MediaHealthStatus.HEALTHY,
            may_proceed=True,
            fixture_sha256=PINNED_FRAME_OCR_FIXTURE_SHA256,
            manifest_sha256=PINNED_FRAME_OCR_MANIFEST_SHA256,
            config_sha256=config_sha256,
            receipt_schema_version=FRAME_OCR_RECEIPT_SCHEMA_VERSION,
            sampling_policy=FRAME_SAMPLING_POLICY,
            duration_ms=duration_ms,
            scheduled_sample_count=scheduled_sample_count,
            completed_sample_count=completed_sample_count,
            nonempty_sample_count=nonempty_sample_count,
            evidence_span_count=evidence_span_count,
            schedule_sha256=schedule_sha256,
        )

    @classmethod
    def failed(cls) -> MediaFrameOcrHealthCheck:
        """Return the single content-free failure shape."""
        return cls(
            status=MediaHealthStatus.FAILED,
            may_proceed=False,
            error_code="MEDIA_FRAME_OCR_PROBE_FAILED",
        )


def _sanitize_error(error: str | None) -> str | None:
    if error is None:
        return None
    sanitized = _SECRET_PATTERN.sub("[redacted]", error)
    sanitized = _WINDOWS_PATH_PATTERN.sub("[redacted]", sanitized)
    sanitized = _UNIX_PATH_PATTERN.sub("[redacted]", sanitized)
    return sanitized[:_MAX_ERROR_LENGTH]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _optional(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _default_binary_resolver(binary_name: str) -> str | None:
    import shutil

    return shutil.which(binary_name)


def _default_module_probe(module_name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _default_model_probe(env: Mapping[str, str]) -> bool:
    """Determine STT model availability via path/flag only — never loads a model."""
    from idis.parsers.media import FasterWhisperMediaConfig, probe_faster_whisper_model

    config = FasterWhisperMediaConfig(
        model_path=_optional(env, IDIS_MEDIA_STT_MODEL_PATH_ENV),
        model_name=_optional(env, IDIS_MEDIA_STT_MODEL_NAME_ENV),
        allow_model_download=_truthy(env.get(IDIS_MEDIA_STT_ALLOW_DOWNLOAD_ENV)),
    )
    return probe_faster_whisper_model(config).can_attempt


def _default_command_runner(args: Sequence[str]) -> None:
    """Run a bounded version command, surfacing no stdout/stderr/paths on failure."""
    import subprocess

    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            timeout=_RUNTIME_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaHealthProbeError(type(exc).__name__) from None
    if completed.returncode != 0:
        raise MediaHealthProbeError("nonzero_exit")


def _build_default_runtime_probe(
    command_runner: Callable[[Sequence[str]], None],
) -> Callable[[], None]:
    """Build the default runtime probe: bounded ``-version`` checks for both binaries."""

    def _probe() -> None:
        command_runner([FFMPEG_BINARY, "-version"])
        command_runner([FFPROBE_BINARY, "-version"])

    return _probe


def load_pinned_stt_health_manifest() -> PinnedSttHealthManifest:
    """Load and cryptographically validate the fixed project-owned STT fixture."""
    try:
        manifest_bytes = _PINNED_STT_MANIFEST_PATH.read_bytes()
        fixture_bytes = _PINNED_STT_FIXTURE_PATH.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != PINNED_STT_MANIFEST_SHA256:
            raise ValueError("manifest hash mismatch")
        if hashlib.sha256(fixture_bytes).hexdigest() != PINNED_STT_FIXTURE_SHA256:
            raise ValueError("fixture hash mismatch")

        raw_manifest: Any = json.loads(manifest_bytes.decode("utf-8-sig"))
        manifest: PinnedSttHealthManifest = PinnedSttHealthManifest.model_validate(raw_manifest)
        provenance_hash = hashlib.sha256(manifest.license_evidence.encode("utf-8")).hexdigest()
        expected_values = (
            manifest.schema_version == _PINNED_STT_SCHEMA_VERSION,
            manifest.fixture_id == _PINNED_STT_FIXTURE_ID,
            manifest.file_sha256 == PINNED_STT_FIXTURE_SHA256,
            2_000 <= manifest.duration_ms <= 5_000,
            manifest.language == "en",
            manifest.expected_normalized_phrase == _PINNED_STT_EXPECTED_PHRASE,
            bool(manifest.normalization.strip()),
            bool(manifest.license_evidence.strip()),
            manifest.license_evidence_sha256 == _PINNED_STT_PROVENANCE_SHA256,
            provenance_hash == _PINNED_STT_PROVENANCE_SHA256,
        )
        if not all(expected_values):
            raise ValueError("manifest contract mismatch")
        return manifest
    except Exception as exc:
        raise MediaHealthProbeError("Pinned STT health fixture validation failed") from exc


def load_pinned_frame_ocr_health_manifest() -> PinnedFrameOcrHealthManifest:
    """Load and cryptographically validate the fixed visual frame-OCR asset."""
    try:
        manifest_bytes = _PINNED_FRAME_OCR_MANIFEST_PATH.read_bytes()
        fixture_bytes = _PINNED_FRAME_OCR_FIXTURE_PATH.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != PINNED_FRAME_OCR_MANIFEST_SHA256:
            raise ValueError("manifest hash mismatch")
        if hashlib.sha256(fixture_bytes).hexdigest() != PINNED_FRAME_OCR_FIXTURE_SHA256:
            raise ValueError("fixture hash mismatch")

        raw_manifest: Any = json.loads(manifest_bytes.decode("utf-8"))
        manifest: PinnedFrameOcrHealthManifest = PinnedFrameOcrHealthManifest.model_validate(
            raw_manifest
        )
        provenance_hash = hashlib.sha256(manifest.license_evidence.encode("utf-8")).hexdigest()
        expected_values = (
            manifest.schema_version == _PINNED_FRAME_OCR_SCHEMA_VERSION,
            manifest.fixture_id == _PINNED_FRAME_OCR_FIXTURE_ID,
            manifest.file_name == _PINNED_FRAME_OCR_FIXTURE_PATH.name,
            manifest.file_sha256 == PINNED_FRAME_OCR_FIXTURE_SHA256,
            manifest.duration_ms == 3_000,
            manifest.width == 1_280,
            manifest.height == 720,
            manifest.frame_rate == "5/1",
            manifest.expected_normalized_phrase == _PINNED_FRAME_OCR_EXPECTED_PHRASE,
            bool(manifest.normalization.strip()),
            manifest.sampling_policy == FRAME_SAMPLING_POLICY,
            bool(manifest.generation_recipe.strip()),
            bool(manifest.provenance_note.strip()),
            manifest.license_evidence_sha256 == _PINNED_FRAME_OCR_PROVENANCE_SHA256,
            provenance_hash == _PINNED_FRAME_OCR_PROVENANCE_SHA256,
        )
        if not all(expected_values):
            raise ValueError("manifest contract mismatch")
        return manifest
    except Exception as exc:
        raise MediaHealthProbeError("Pinned frame OCR fixture validation failed") from exc


def normalize_stt_health_phrase(text: str) -> str:
    """Normalize the pinned phrase exactly; no fuzzy or substring matching."""
    tokens = re.sub(r"[^a-z0-9]+", " ", text.casefold()).split()
    number_words = {"1": "one", "2": "two", "3": "three"}
    return " ".join(number_words.get(token, token) for token in tokens)


def _build_inference_config(
    env: Mapping[str, str],
    manifest: PinnedSttHealthManifest,
) -> tuple[FasterWhisperMediaConfig, float, str, str]:
    adapter = str(env.get(IDIS_MEDIA_ADAPTER_ENV, "")).strip().lower()
    model_path = _optional(env, IDIS_MEDIA_STT_MODEL_PATH_ENV)
    model_name = _optional(env, IDIS_MEDIA_STT_MODEL_NAME_ENV)
    allow_download = _truthy(env.get(IDIS_MEDIA_STT_ALLOW_DOWNLOAD_ENV))
    if adapter != DEFAULT_MEDIA_ADAPTER:
        raise ValueError("media adapter is not admitted")
    if model_path is None or model_name is not None or allow_download:
        raise ValueError("a download-disabled local model path is required")

    timeout_seconds = float(env.get(IDIS_MEDIA_TIMEOUT_SECONDS_ENV, "30"))
    max_duration_seconds = float(env.get(IDIS_MEDIA_MAX_DURATION_SECONDS_ENV, "600"))
    language = str(env.get(IDIS_MEDIA_LANGUAGE_ENV, "en")).strip()
    compute_type = str(env.get(IDIS_MEDIA_COMPUTE_TYPE_ENV, "int8")).strip()
    if timeout_seconds <= 0 or max_duration_seconds < manifest.duration_ms / 1000:
        raise ValueError("media inference bounds are invalid")
    if not language or not compute_type:
        raise ValueError("media inference configuration is incomplete")

    config = FasterWhisperMediaConfig(
        model_path=model_path,
        allow_model_download=False,
        language=language,
        compute_type=compute_type,
        max_duration_seconds=max_duration_seconds,
    )
    if probe_faster_whisper_model(config).status is not FasterWhisperModelStatus.LOCAL_MODEL_READY:
        raise ValueError("local media model is unavailable")
    model_path_sha256, model_files_sha256 = _fingerprint_local_model(Path(model_path))
    return config, timeout_seconds, model_path_sha256, model_files_sha256


def _fingerprint_local_model(model_path: Path) -> tuple[str, str]:
    """Bind the local model path and complete file set without exposing either."""
    root = model_path.expanduser().resolve()
    path_sha256 = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files or any(path.is_symlink() for path in files):
        raise ValueError("local media model file set is invalid")

    digest = hashlib.sha256()
    for path in files:
        relative_path = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as model_file:
            while chunk := model_file.read(1024 * 1024):
                digest.update(chunk)
    return path_sha256, digest.hexdigest()


def _default_inference_runner(
    *,
    fixture_path: Path,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
) -> list[MediaSegmentText]:
    adapter = FasterWhisperMediaAdapter(config=config)
    return extract_media_file(adapter, fixture_path, timeout_seconds=timeout_seconds)


def _validated_inference_evidence(
    segments: object,
    *,
    manifest: PinnedSttHealthManifest,
) -> tuple[int, int, int]:
    if not isinstance(segments, list) or not segments or len(segments) > 500:
        raise ValueError("media inference produced invalid segments")

    previous_end_ms = -1
    transcript_parts: list[str] = []
    for segment in segments:
        if not isinstance(segment, MediaSegmentText):
            raise ValueError("media inference produced invalid segments")
        if (
            not isinstance(segment.start_ms, int)
            or not isinstance(segment.end_ms, int)
            or segment.start_ms < 0
            or segment.end_ms <= segment.start_ms
            or segment.start_ms < previous_end_ms
            or not isinstance(segment.text, str)
            or not segment.text.strip()
        ):
            raise ValueError("media inference produced invalid segments")
        previous_end_ms = segment.end_ms
        transcript_parts.append(segment.text.strip())

    first_start_ms = segments[0].start_ms
    final_end_ms = segments[-1].end_ms
    if first_start_ms > 1_500 or final_end_ms > manifest.duration_ms + 500:
        raise ValueError("media inference timing is outside the admitted window")
    normalized = normalize_stt_health_phrase(" ".join(transcript_parts))
    if normalized != manifest.expected_normalized_phrase:
        raise ValueError("media inference phrase mismatch")
    return len(segments), first_start_ms, final_end_ms


def check_media_inference_health(
    *,
    env: Mapping[str, str] | None = None,
    inference_runner: Callable[..., list[MediaSegmentText]] | None = None,
) -> MediaInferenceHealthCheck:
    """Run explicit real inference over the immutable, project-owned health asset."""
    values = os.environ if env is None else env
    try:
        manifest = load_pinned_stt_health_manifest()
        (
            config,
            timeout_seconds,
            model_path_sha256,
            model_files_sha256,
        ) = _build_inference_config(values, manifest)
        runner = inference_runner or _default_inference_runner
        segments = runner(
            fixture_path=_PINNED_STT_FIXTURE_PATH,
            config=config,
            timeout_seconds=timeout_seconds,
        )
        post_path_sha256, post_files_sha256 = _fingerprint_local_model(
            Path(config.model_path or "")
        )
        if post_path_sha256 != model_path_sha256 or post_files_sha256 != model_files_sha256:
            raise ValueError("local media model changed during inference")
        segment_count, first_start_ms, final_end_ms = _validated_inference_evidence(
            segments,
            manifest=manifest,
        )
        return MediaInferenceHealthCheck.healthy(
            segment_count=segment_count,
            first_start_ms=first_start_ms,
            final_end_ms=final_end_ms,
            model_path_sha256=model_path_sha256,
            model_files_sha256=model_files_sha256,
        )
    except Exception:
        return MediaInferenceHealthCheck.failed()


_STRICT_FRAME_MEDIA_TIMEOUT_SECONDS = 900.0
_STRICT_FRAME_MEDIA_MAX_DURATION_SECONDS = 300.0
_STRICT_FRAME_OCR_MAX_PAGES = 72
_FRAME_OCR_REQUIRED_ENV = (
    "IDIS_MEDIA_TIMEOUT_SECONDS",
    "IDIS_MEDIA_MAX_DURATION_SECONDS",
    "IDIS_OCR_ENABLED",
    "IDIS_OCR_ADAPTER",
    "IDIS_OCR_MAX_PAGES",
    "IDIS_OCR_TIMEOUT_SECONDS",
    "IDIS_OCR_DPI",
    "IDIS_OCR_LANGUAGE",
)


def _build_frame_ocr_health_config(
    env: Mapping[str, str],
    *,
    manifest: PinnedFrameOcrHealthManifest,
) -> tuple[OcrConfig, float, str]:
    """Build and hash the exact strict frame-OCR configuration."""
    from idis.services.ingestion.defaults import build_default_ocr_config

    if any(_optional(env, name) is None for name in _FRAME_OCR_REQUIRED_ENV):
        raise ValueError("frame OCR configuration is incomplete")
    if not _truthy(env.get("IDIS_OCR_ENABLED")):
        raise ValueError("frame OCR is disabled")
    if str(env.get("IDIS_OCR_ADAPTER", "")).strip().lower() != "tesseract":
        raise ValueError("frame OCR adapter is not admitted")
    if str(env.get("IDIS_OCR_LANGUAGE", "")).strip() != "eng":
        raise ValueError("frame OCR language is not admitted")

    try:
        max_pages = int(str(env["IDIS_OCR_MAX_PAGES"]).strip())
        dpi = int(str(env["IDIS_OCR_DPI"]).strip())
        ocr_timeout_seconds = float(str(env["IDIS_OCR_TIMEOUT_SECONDS"]).strip())
        media_timeout_seconds = float(str(env[IDIS_MEDIA_TIMEOUT_SECONDS_ENV]).strip())
        max_duration_seconds = float(str(env[IDIS_MEDIA_MAX_DURATION_SECONDS_ENV]).strip())
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("frame OCR numeric configuration is invalid") from exc

    if (
        max_pages != _STRICT_FRAME_OCR_MAX_PAGES
        or dpi <= 0
        or not math.isfinite(ocr_timeout_seconds)
        or ocr_timeout_seconds <= 0
        or not math.isfinite(media_timeout_seconds)
        or media_timeout_seconds != _STRICT_FRAME_MEDIA_TIMEOUT_SECONDS
        or not math.isfinite(max_duration_seconds)
        or max_duration_seconds != _STRICT_FRAME_MEDIA_MAX_DURATION_SECONDS
        or max_duration_seconds < manifest.duration_ms / 1_000
    ):
        raise ValueError("frame OCR bounds are not admitted")

    ocr_config = build_default_ocr_config(env)
    if (
        ocr_config is None
        or not ocr_config.enabled
        or ocr_config.max_pages != max_pages
        or ocr_config.timeout_seconds != ocr_timeout_seconds
        or not isinstance(ocr_config.adapter, TesseractOcrAdapter)
        or ocr_config.adapter.dpi != dpi
        or ocr_config.adapter.language != "eng"
    ):
        raise ValueError("frame OCR runtime configuration drifted")

    config_sha256 = canonical_sha256(
        {
            "receipt_schema_version": FRAME_OCR_RECEIPT_SCHEMA_VERSION,
            "sampling_policy": FRAME_SAMPLING_POLICY,
            "scene_threshold": FRAME_SCENE_THRESHOLD,
            "frame_period_ms": FRAME_PERIOD_MS,
            "max_sample_count": MAX_FRAME_SAMPLES,
            "max_frame_width": MAX_FRAME_WIDTH,
            "max_frame_height": MAX_FRAME_HEIGHT,
            "media_timeout_seconds": media_timeout_seconds,
            "media_max_duration_seconds": max_duration_seconds,
            "ocr_enabled": True,
            "ocr_adapter": "tesseract",
            "ocr_language": "eng",
            "ocr_max_pages": max_pages,
            "ocr_timeout_seconds": ocr_timeout_seconds,
            "ocr_dpi": dpi,
        }
    )
    return ocr_config, media_timeout_seconds, config_sha256


def _validate_frame_ocr_health_extraction(
    extraction: object,
    *,
    duration_ms: int,
    expected_normalized_phrase: str,
) -> dict[str, object]:
    """Re-derive a complete receipt and bind the project-authored phrase."""
    if not isinstance(extraction, VideoFrameOcrExtraction):
        raise ValueError("frame OCR returned an invalid extraction")
    sample_ms = extraction.sample_ms
    segments = extraction.segments
    if (
        not isinstance(sample_ms, tuple)
        or not sample_ms
        or len(sample_ms) > MAX_FRAME_SAMPLES
        or any(isinstance(value, bool) or not isinstance(value, int) for value in sample_ms)
        or sample_ms != tuple(sorted(set(sample_ms)))
        or any(value < 0 or value >= duration_ms for value in sample_ms)
    ):
        raise ValueError("frame OCR returned an invalid schedule")

    baseline_schedule = build_periodic_plus_scenes_schedule(duration_ms=duration_ms)
    if not set(baseline_schedule).issubset(sample_ms):
        raise ValueError("frame OCR schedule omitted a deterministic sample")
    if not isinstance(segments, tuple):
        raise ValueError("frame OCR returned invalid evidence")
    if len(segments) != len(sample_ms):
        raise ValueError("frame OCR health phrase is missing from a scheduled sample")

    seen_sample_indexes: set[int] = set()
    for segment in segments:
        if not isinstance(segment, VideoFrameOcrSegment):
            raise ValueError("frame OCR returned invalid evidence")
        if (
            isinstance(segment.sample_index, bool)
            or not isinstance(segment.sample_index, int)
            or segment.sample_index < 0
            or segment.sample_index >= len(sample_ms)
            or segment.sample_index in seen_sample_indexes
            or segment.start_ms != sample_ms[segment.sample_index]
            or segment.end_ms != min(duration_ms, segment.start_ms + 1)
            or not isinstance(segment.text, str)
            or not segment.text.strip()
            or normalize_stt_health_phrase(segment.text) != expected_normalized_phrase
            or segment.confidence is None
            or not math.isfinite(float(segment.confidence))
            or not 0 <= segment.confidence <= 1
        ):
            raise ValueError("frame OCR returned invalid evidence")
        seen_sample_indexes.add(segment.sample_index)

    expected_receipt = build_frame_ocr_receipt(
        duration_ms=duration_ms,
        sample_ms=sample_ms,
        segments=segments,
        completed_sample_count=len(sample_ms),
    )
    try:
        supplied_receipt = dict(extraction.receipt)
    except (TypeError, ValueError) as exc:
        raise ValueError("frame OCR receipt is invalid") from exc
    if supplied_receipt != expected_receipt:
        raise ValueError("frame OCR receipt is incomplete or drifted")
    return expected_receipt


def _strict_frame_receipt_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("frame OCR receipt count is invalid")
    return value


def check_media_frame_ocr_health(
    *,
    env: Mapping[str, str] | None = None,
    binary_resolver: Callable[[str], str | None] | None = None,
    frame_ocr_runner: Callable[..., VideoFrameOcrExtraction] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> MediaFrameOcrHealthCheck:
    """Run real ffmpeg/Tesseract frame OCR over the immutable health asset.

    The visual fixture carries only project-authored health text, so every
    deterministic sample must return the exact normalized phrase. Real-video
    acceptance separately allows either evidence track to be empty while
    requiring at least one non-empty track per video.
    """
    values = os.environ if env is None else env
    try:
        manifest = load_pinned_frame_ocr_health_manifest()
        ocr_config, timeout_seconds, config_sha256 = _build_frame_ocr_health_config(
            values,
            manifest=manifest,
        )
        clock = monotonic or time.monotonic
        started_at = float(clock())
        if not math.isfinite(started_at):
            raise ValueError("frame OCR monotonic clock is invalid")
        deadline = started_at + timeout_seconds

        def remaining_seconds() -> float:
            remaining = deadline - float(clock())
            if not math.isfinite(remaining) or remaining <= 0:
                raise TimeoutError("frame OCR health deadline expired")
            return remaining

        resolve = binary_resolver or _default_binary_resolver
        ffmpeg_binary = resolve(FFMPEG_BINARY)
        remaining_seconds()
        if not isinstance(ffmpeg_binary, str) or not ffmpeg_binary.strip():
            raise ValueError("ffmpeg is unavailable")

        runner = frame_ocr_runner or extract_video_frame_ocr
        extraction = runner(
            source_path=_PINNED_FRAME_OCR_FIXTURE_PATH,
            duration_ms=manifest.duration_ms,
            ffmpeg_binary=ffmpeg_binary,
            ocr_config=ocr_config,
            remaining_seconds=remaining_seconds,
        )
        remaining_seconds()
        receipt = _validate_frame_ocr_health_extraction(
            extraction,
            duration_ms=manifest.duration_ms,
            expected_normalized_phrase=manifest.expected_normalized_phrase,
        )
        scheduled_sample_count = _strict_frame_receipt_int(receipt["scheduled_sample_count"])
        completed_sample_count = _strict_frame_receipt_int(receipt["completed_sample_count"])
        nonempty_sample_count = _strict_frame_receipt_int(receipt["nonempty_sample_count"])
        evidence_span_count = _strict_frame_receipt_int(receipt["evidence_span_count"])
        return MediaFrameOcrHealthCheck.healthy(
            config_sha256=config_sha256,
            duration_ms=manifest.duration_ms,
            scheduled_sample_count=scheduled_sample_count,
            completed_sample_count=completed_sample_count,
            nonempty_sample_count=nonempty_sample_count,
            evidence_span_count=evidence_span_count,
            schedule_sha256=str(receipt["schedule_sha256"]),
        )
    except Exception:
        return MediaFrameOcrHealthCheck.failed()


def check_media_health(
    *,
    env: Mapping[str, str] | None = None,
    binary_resolver: Callable[[str], str | None] | None = None,
    module_probe: Callable[[str], bool] | None = None,
    model_probe: Callable[[Mapping[str, str]], bool] | None = None,
    runtime_probe: Callable[[], None] | None = None,
    command_runner: Callable[[Sequence[str]], None] | None = None,
) -> MediaHealthCheck:
    """Check media/STT runtime readiness without exposing paths, env values, or content.

    Args:
        env: Environment mapping to inspect. Defaults to ``os.environ``.
        binary_resolver: Injectable binary presence probe (defaults to ``shutil.which``).
        module_probe: Injectable Python-module importability probe.
        model_probe: Injectable STT-model availability probe (path/flag only).
        runtime_probe: Optional injectable runtime probe; raises on timeout/failure.
            Defaults to bounded ``ffmpeg -version`` / ``ffprobe -version`` checks.
        command_runner: Injectable command executor used by the default runtime probe
            (defaults to a bounded, output-suppressing subprocess runner).

    Returns:
        Sanitized media/STT health-check result.
    """
    values = os.environ if env is None else env
    adapter = str(values.get(IDIS_MEDIA_ADAPTER_ENV, "")).strip().lower()
    if not adapter:
        return MediaHealthCheck.disabled()
    if adapter != DEFAULT_MEDIA_ADAPTER:
        return MediaHealthCheck.failed(
            error=f"Unsupported media adapter. Allowed adapter: {DEFAULT_MEDIA_ADAPTER}."
        )

    resolve = binary_resolver or _default_binary_resolver
    has_module = module_probe or _default_module_probe
    has_model = model_probe or _default_model_probe

    missing: list[str] = []
    if resolve(FFMPEG_BINARY) is None:
        missing.append("ffmpeg")
    if resolve(FFPROBE_BINARY) is None:
        missing.append("ffprobe")
    for module_name, safe_name in _REQUIRED_MODULES:
        if not has_module(module_name):
            missing.append(safe_name)
    if not has_model(values):
        missing.append("media_model")

    if missing:
        return MediaHealthCheck.missing(dependencies=missing)

    runtime = runtime_probe or _build_default_runtime_probe(
        command_runner or _default_command_runner
    )
    try:
        runtime()
    except Exception as exc:
        # Only the exception class name is surfaced — never the message body
        # (which could contain paths, model names, or transcript content).
        return MediaHealthCheck.failed(error=type(exc).__name__)

    return MediaHealthCheck.healthy()
