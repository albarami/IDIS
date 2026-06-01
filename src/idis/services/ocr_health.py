"""Strict OCR runtime health checks (tesseract/poppler/python deps/tessdata).

Mirrors the shape of ``rag/pgvector_health.py`` / ``rag/embedding_health.py``: an
env-driven check with injectable probes that returns a sanitized Pydantic result
which never exposes raw paths, env values, OCR text, or secrets.

OCR stays config-gated and off by default; ``DISABLED`` is an expected (non-error)
state. This module performs NO wiring into strict readiness / provisioning truth
(Task 3) and implements NO confidence diagnostics (Task 4).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

IDIS_OCR_ENABLED_ENV = "IDIS_OCR_ENABLED"
IDIS_OCR_ADAPTER_ENV = "IDIS_OCR_ADAPTER"
IDIS_OCR_LANGUAGE_ENV = "IDIS_OCR_LANGUAGE"
TESSDATA_PREFIX_ENV = "TESSDATA_PREFIX"

DEFAULT_OCR_ADAPTER = "tesseract"
DEFAULT_OCR_LANGUAGE = "eng"

TESSERACT_BINARY = "tesseract"
POPPLER_PDFINFO_BINARY = "pdfinfo"

# (import name, safe public identifier) — only the safe identifier is ever surfaced.
_REQUIRED_MODULES: tuple[tuple[str, str], ...] = (
    ("pytesseract", "pytesseract"),
    ("pdf2image", "pdf2image"),
    ("PIL", "pillow"),
)

_MAX_ERROR_LENGTH = 240
_RUNTIME_PROBE_TIMEOUT_SECONDS = 10
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]+")
_WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s]+")
_UNIX_PATH_PATTERN = re.compile(r"(?<!\w)/[^/\s]+(?:/[^/\s]*)+")


class OcrHealthProbeError(Exception):
    """Raised by the default runtime probe; carries no command output or paths."""


class OcrBinaryResolver(Protocol):
    """Resolve an executable path for a binary name (presence probe)."""

    def __call__(self, binary_name: str) -> str | None:
        """Return a path when the binary is available, else ``None``."""


class OcrModuleProbe(Protocol):
    """Return whether a Python module is importable."""

    def __call__(self, module_name: str) -> bool:
        """Return True when the module can be imported."""


class OcrLanguageProbe(Protocol):
    """Return whether tessdata for a language is available."""

    def __call__(self, language: str) -> bool:
        """Return True when language data is available."""


class OcrRuntimeProbe(Protocol):
    """Run a minimal OCR runtime check; raise on timeout/command failure."""

    def __call__(self) -> None:
        """Return ``None`` on success; raise on failure."""


class OcrCommandRunner(Protocol):
    """Run a bounded command; raise on non-zero exit, timeout, or missing binary."""

    def __call__(self, args: Sequence[str]) -> None:
        """Return ``None`` on success; raise on failure (no output is surfaced)."""


class OcrHealthStatus(StrEnum):
    """Safe OCR health status values."""

    HEALTHY = "healthy"
    DISABLED = "disabled"
    MISSING_DEPENDENCIES = "missing_dependencies"
    FAILED = "failed"


class OcrHealthCheck(BaseModel):
    """Sanitized OCR runtime health-check result."""

    model_config = ConfigDict(extra="forbid")

    status: OcrHealthStatus
    enabled: bool
    missing_dependencies: list[str] = Field(default_factory=list)
    error: str | None = None

    @classmethod
    def disabled(cls) -> OcrHealthCheck:
        """Return the expected off-by-default result (OCR not enabled)."""
        return cls(status=OcrHealthStatus.DISABLED, enabled=False)

    @classmethod
    def healthy(cls) -> OcrHealthCheck:
        """Return a successful health result."""
        return cls(status=OcrHealthStatus.HEALTHY, enabled=True)

    @classmethod
    def missing(cls, *, dependencies: list[str]) -> OcrHealthCheck:
        """Return a missing-dependencies result with safe identifiers only."""
        return cls(
            status=OcrHealthStatus.MISSING_DEPENDENCIES,
            enabled=True,
            missing_dependencies=sorted(set(dependencies)),
        )

    @classmethod
    def failed(cls, *, error: str | None = None) -> OcrHealthCheck:
        """Return a sanitized failed health result."""
        return cls(
            status=OcrHealthStatus.FAILED,
            enabled=True,
            error=_sanitize_error(error) or "OCR health check failed.",
        )


def _truthy_env(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_error(error: str | None) -> str | None:
    if error is None:
        return None
    sanitized = _SECRET_PATTERN.sub("[redacted]", error)
    sanitized = _WINDOWS_PATH_PATTERN.sub("[redacted]", sanitized)
    sanitized = _UNIX_PATH_PATTERN.sub("[redacted]", sanitized)
    return sanitized[:_MAX_ERROR_LENGTH]


def _default_binary_resolver(binary_name: str) -> str | None:
    import shutil

    return shutil.which(binary_name)


def _default_module_probe(module_name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _make_default_language_probe(env: Mapping[str, str]) -> Callable[[str], bool]:
    """Build a tessdata probe bound to the supplied environment mapping."""

    def _probe(language: str) -> bool:
        prefix = str(env.get(TESSDATA_PREFIX_ENV, "")).strip()
        if not prefix:
            return False
        candidates = (
            os.path.join(prefix, f"{language}.traineddata"),
            os.path.join(prefix, "tessdata", f"{language}.traineddata"),
        )
        return any(os.path.isfile(candidate) for candidate in candidates)

    return _probe


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
        raise OcrHealthProbeError(type(exc).__name__) from None
    if completed.returncode != 0:
        raise OcrHealthProbeError("nonzero_exit")


def _build_default_runtime_probe(
    command_runner: Callable[[Sequence[str]], None],
) -> Callable[[], None]:
    """Build the default runtime probe: bounded ``--version`` checks for both binaries."""

    def _probe() -> None:
        command_runner([TESSERACT_BINARY, "--version"])
        command_runner([POPPLER_PDFINFO_BINARY, "-v"])

    return _probe


def check_ocr_health(
    *,
    env: Mapping[str, str] | None = None,
    binary_resolver: Callable[[str], str | None] | None = None,
    module_probe: Callable[[str], bool] | None = None,
    language_probe: Callable[[str], bool] | None = None,
    runtime_probe: Callable[[], None] | None = None,
    command_runner: Callable[[Sequence[str]], None] | None = None,
) -> OcrHealthCheck:
    """Check OCR runtime readiness without exposing paths, env values, or content.

    Args:
        env: Environment mapping to inspect. Defaults to ``os.environ``.
        binary_resolver: Injectable binary presence probe (defaults to ``shutil.which``).
        module_probe: Injectable Python-module importability probe.
        language_probe: Injectable tessdata/language availability probe.
        runtime_probe: Optional injectable runtime probe; raises on timeout/failure.
            Defaults to bounded ``tesseract --version`` / ``pdfinfo -v`` checks.
        command_runner: Injectable command executor used by the default runtime
            probe (defaults to a bounded, output-suppressing subprocess runner).

    Returns:
        Sanitized OCR health-check result.
    """
    values = os.environ if env is None else env
    if not _truthy_env(str(values.get(IDIS_OCR_ENABLED_ENV, ""))):
        return OcrHealthCheck.disabled()

    adapter = str(values.get(IDIS_OCR_ADAPTER_ENV, DEFAULT_OCR_ADAPTER)).strip().lower()
    if adapter != DEFAULT_OCR_ADAPTER:
        return OcrHealthCheck.failed(
            error=f"Unsupported OCR adapter. Allowed adapter: {DEFAULT_OCR_ADAPTER}."
        )

    resolve = binary_resolver or _default_binary_resolver
    has_module = module_probe or _default_module_probe
    has_language = language_probe or _make_default_language_probe(values)
    language = (
        str(values.get(IDIS_OCR_LANGUAGE_ENV, DEFAULT_OCR_LANGUAGE)).strip() or DEFAULT_OCR_LANGUAGE
    )

    missing: list[str] = []
    if resolve(TESSERACT_BINARY) is None:
        missing.append("tesseract")
    if resolve(POPPLER_PDFINFO_BINARY) is None:
        missing.append("poppler")
    for module_name, safe_name in _REQUIRED_MODULES:
        if not has_module(module_name):
            missing.append(safe_name)
    if not has_language(language):
        missing.append("tessdata")

    if missing:
        return OcrHealthCheck.missing(dependencies=missing)

    runtime = runtime_probe or _build_default_runtime_probe(
        command_runner or _default_command_runner
    )
    try:
        runtime()
    except Exception as exc:
        # Only the exception class name is surfaced — never the message body
        # (which could contain paths or OCR content).
        return OcrHealthCheck.failed(error=type(exc).__name__)

    return OcrHealthCheck.healthy()
