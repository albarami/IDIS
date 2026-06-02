"""Strict media/STT runtime health checks (ffmpeg/ffprobe/faster-whisper/model).

Mirrors `ocr_health.py` (and the `rag/*_health.py` pattern): an env-driven check with
injectable probes that returns a sanitized Pydantic result which never exposes raw
paths, env values, transcript text, model names, command output, or secrets.

Media stays config-gated and off by default; ``DISABLED`` (no `IDIS_MEDIA_ADAPTER`) is
an expected non-error state. This module is standalone strict health: the wiring into
strict readiness / provisioning truth lives in the ``services/runs`` layer, not here. It
does NOT load faster-whisper or model files (model availability is determined via a
path/flag probe only).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

IDIS_MEDIA_ADAPTER_ENV = "IDIS_MEDIA_ADAPTER"
IDIS_MEDIA_STT_MODEL_PATH_ENV = "IDIS_MEDIA_STT_MODEL_PATH"
IDIS_MEDIA_STT_MODEL_NAME_ENV = "IDIS_MEDIA_STT_MODEL_NAME"
IDIS_MEDIA_STT_ALLOW_DOWNLOAD_ENV = "IDIS_MEDIA_STT_ALLOW_DOWNLOAD"

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
