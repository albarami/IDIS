"""Pinned, path-private Poppler runtime identity for strict PDF rendering.

``pdf2image`` accepts a directory and then selects ``pdftoppm``/``pdfinfo``
itself.  Strict execution therefore admits those binaries as one co-located
runtime and carries that immutable authority into parser workers.  Filesystem
paths never appear in the safe projection or ``repr``.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

PINNED_POPPLER_VERSION = "25.12.0"
POPPLER_RUNTIME_IDENTITY_SCHEMA_VERSION = "poppler-runtime-identity-v1"

_WINDOWS_PLATFORM_NAME = "Windows"
_WINDOWS_PDFTOPPM_NAME = "pdftoppm.exe"
_WINDOWS_PDFINFO_NAME = "pdfinfo.exe"
_WINDOWS_POPPLER_LIBRARY_NAME = "poppler.dll"
_POSIX_PDFTOPPM_NAME = "pdftoppm"
_POSIX_PDFINFO_NAME = "pdfinfo"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_VERSION_PATTERN = re.compile(
    rb"(?:pdftoppm|pdfinfo)\s+version\s+([0-9]+\.[0-9]+\.[0-9]+)",
    flags=re.IGNORECASE,
)
_VERSION_PROBE_TIMEOUT_SECONDS = 10
_MAX_VERSION_OUTPUT_BYTES = 16_384
_HASH_CHUNK_BYTES = 1024 * 1024

BinaryResolver = Callable[[str], str | None]
VersionProbe = Callable[[Path], str]
FileHasher = Callable[[Path], str]


class PopplerRuntimeBindingError(RuntimeError):
    """Fail-closed Poppler admission error carrying only a stable safe code."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _runtime_identity_sha256(
    *,
    version: str,
    pdftoppm_sha256: str,
    poppler_dll_sha256: str,
) -> str:
    payload = {
        "schema_version": POPPLER_RUNTIME_IDENTITY_SCHEMA_VERSION,
        "poppler_version": version,
        "pdftoppm_sha256": pdftoppm_sha256,
        "poppler_dll_sha256": poppler_dll_sha256,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class PinnedPopplerRuntime:
    """Immutable execution binding with an explicitly safe public projection."""

    version: str
    pdftoppm_sha256: str
    poppler_dll_sha256: str
    runtime_identity_sha256: str
    _pdftoppm_path: Path = field(repr=False)
    _pdfinfo_path: Path = field(repr=False)
    _poppler_library_path: Path = field(repr=False)
    _platform_name: str = field(repr=False)

    def __post_init__(self) -> None:
        expected_identity = _runtime_identity_sha256(
            version=self.version,
            pdftoppm_sha256=self.pdftoppm_sha256,
            poppler_dll_sha256=self.poppler_dll_sha256,
        )
        paths = (
            self._pdftoppm_path,
            self._pdfinfo_path,
            self._poppler_library_path,
        )
        if (
            self.version != PINNED_POPPLER_VERSION
            or not _SHA256_PATTERN.fullmatch(self.pdftoppm_sha256)
            or not _SHA256_PATTERN.fullmatch(self.poppler_dll_sha256)
            or self.runtime_identity_sha256 != expected_identity
            or not _SHA256_PATTERN.fullmatch(self.runtime_identity_sha256)
            or any(not isinstance(path, Path) or not path.is_absolute() for path in paths)
            or not isinstance(self._platform_name, str)
            or not self._platform_name
        ):
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")

    @property
    def pdftoppm_path(self) -> Path:
        """Return the private, canonical executable authority for worker use."""

        return self._pdftoppm_path

    @property
    def pdfinfo_path(self) -> Path:
        """Return the private, canonical metadata executable authority."""

        return self._pdfinfo_path

    @property
    def poppler_library_path(self) -> Path:
        """Return the private, canonical Poppler implementation library authority."""

        return self._poppler_library_path

    @property
    def binary_dir(self) -> Path:
        """Return the verified directory expected by ``pdf2image``."""

        return self._pdftoppm_path.parent

    @property
    def platform_name(self) -> str:
        """Return the safe platform identity used to validate executable names."""

        return self._platform_name

    def safe_provenance(self) -> dict[str, str]:
        """Return the only persistable projection; filesystem paths are excluded."""

        return {
            "poppler_version": self.version,
            "poppler_executable_sha256": self.pdftoppm_sha256,
            "poppler_runtime_identity_sha256": self.runtime_identity_sha256,
        }


def _binary_names(platform_name: str) -> tuple[str, str]:
    if platform_name == _WINDOWS_PLATFORM_NAME:
        # ``shutil.which('pdftoppm')`` may prefer a .CMD shim on Windows.  The
        # explicit suffix admits the executable that pdf2image will launch.
        return _WINDOWS_PDFTOPPM_NAME, _WINDOWS_PDFINFO_NAME
    return _POSIX_PDFTOPPM_NAME, _POSIX_PDFINFO_NAME


def _default_binary_resolver(binary_name: str) -> str | None:
    return shutil.which(binary_name)


def _canonical_file(path_value: str | Path, *, missing_code: str) -> Path:
    try:
        resolved = Path(path_value).expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise OSError
    except (OSError, RuntimeError, ValueError):
        raise PopplerRuntimeBindingError(missing_code) from None
    return resolved


def _default_version_probe(executable: Path) -> str:
    try:
        completed = subprocess.run(
            [str(executable), "-v"],
            capture_output=True,
            timeout=_VERSION_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE") from None
    if completed.returncode != 0:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")
    output = (completed.stdout + b"\n" + completed.stderr)[:_MAX_VERSION_OUTPUT_BYTES]
    match = _VERSION_PATTERN.search(output)
    if match is None:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    try:
        return match.group(1).decode("ascii")
    except UnicodeDecodeError:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID") from None


def _stat_signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _default_file_hasher(path: Path) -> str:
    try:
        before = _stat_signature(path)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
        after = _stat_signature(path)
    except OSError:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE") from None
    if before != after:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_DRIFTED")
    return digest.hexdigest()


def _validate_component_layout(
    *,
    pdftoppm_path: Path,
    pdfinfo_path: Path,
    poppler_library_path: Path,
    platform_name: str,
    error_code: str,
) -> None:
    expected_pdftoppm, expected_pdfinfo = _binary_names(platform_name)
    if (
        pdftoppm_path.name.casefold() != expected_pdftoppm.casefold()
        or pdfinfo_path.name.casefold() != expected_pdfinfo.casefold()
        or poppler_library_path.name.casefold() != _WINDOWS_POPPLER_LIBRARY_NAME.casefold()
        or pdftoppm_path.parent != pdfinfo_path.parent
        or pdftoppm_path.parent != poppler_library_path.parent
    ):
        raise PopplerRuntimeBindingError(error_code)


def resolve_pinned_poppler_runtime(
    *,
    binary_resolver: BinaryResolver | None = None,
    version_probe: VersionProbe | None = None,
    file_hasher: FileHasher | None = None,
    platform_name: str | None = None,
) -> PinnedPopplerRuntime:
    """Resolve and admit the exact co-located Poppler 25.12.0 runtime."""

    admitted_platform = platform.system() if platform_name is None else platform_name
    pdftoppm_name, pdfinfo_name = _binary_names(admitted_platform)
    resolve = binary_resolver or _default_binary_resolver
    raw_pdftoppm = resolve(pdftoppm_name)
    raw_pdfinfo = resolve(pdfinfo_name)
    if raw_pdftoppm is None or raw_pdfinfo is None:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")
    pdftoppm_path = _canonical_file(raw_pdftoppm, missing_code="POPPLER_RUNTIME_UNAVAILABLE")
    pdfinfo_path = _canonical_file(raw_pdfinfo, missing_code="POPPLER_RUNTIME_UNAVAILABLE")
    poppler_library_path = _canonical_file(
        pdftoppm_path.parent / _WINDOWS_POPPLER_LIBRARY_NAME,
        missing_code="POPPLER_RUNTIME_UNAVAILABLE",
    )
    _validate_component_layout(
        pdftoppm_path=pdftoppm_path,
        pdfinfo_path=pdfinfo_path,
        poppler_library_path=poppler_library_path,
        platform_name=admitted_platform,
        error_code="POPPLER_RUNTIME_IDENTITY_INVALID",
    )

    probe_version = version_probe or _default_version_probe
    try:
        versions = (probe_version(pdftoppm_path), probe_version(pdfinfo_path))
    except PopplerRuntimeBindingError:
        raise
    except Exception:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID") from None
    if versions != (PINNED_POPPLER_VERSION, PINNED_POPPLER_VERSION):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_VERSION_MISMATCH")

    hash_file = file_hasher or _default_file_hasher
    try:
        pdftoppm_sha256 = hash_file(pdftoppm_path)
        poppler_dll_sha256 = hash_file(poppler_library_path)
    except PopplerRuntimeBindingError:
        raise
    except Exception:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID") from None
    if (
        not isinstance(pdftoppm_sha256, str)
        or not _SHA256_PATTERN.fullmatch(pdftoppm_sha256)
        or not isinstance(poppler_dll_sha256, str)
        or not _SHA256_PATTERN.fullmatch(poppler_dll_sha256)
    ):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    runtime_identity_sha256 = _runtime_identity_sha256(
        version=PINNED_POPPLER_VERSION,
        pdftoppm_sha256=pdftoppm_sha256,
        poppler_dll_sha256=poppler_dll_sha256,
    )
    return PinnedPopplerRuntime(
        version=PINNED_POPPLER_VERSION,
        pdftoppm_sha256=pdftoppm_sha256,
        poppler_dll_sha256=poppler_dll_sha256,
        runtime_identity_sha256=runtime_identity_sha256,
        _pdftoppm_path=pdftoppm_path,
        _pdfinfo_path=pdfinfo_path,
        _poppler_library_path=poppler_library_path,
        _platform_name=admitted_platform,
    )


def verify_pinned_poppler_runtime(
    binding: PinnedPopplerRuntime,
    *,
    version_probe: VersionProbe | None = None,
    file_hasher: FileHasher | None = None,
) -> None:
    """Revalidate an admitted binding inside a child immediately before render."""

    if not isinstance(binding, PinnedPopplerRuntime):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    try:
        pdftoppm_path = _canonical_file(
            binding.pdftoppm_path,
            missing_code="POPPLER_RUNTIME_DRIFTED",
        )
        pdfinfo_path = _canonical_file(
            binding.pdfinfo_path,
            missing_code="POPPLER_RUNTIME_DRIFTED",
        )
        poppler_library_path = _canonical_file(
            binding.poppler_library_path,
            missing_code="POPPLER_RUNTIME_DRIFTED",
        )
        if (
            pdftoppm_path != binding.pdftoppm_path
            or pdfinfo_path != binding.pdfinfo_path
            or poppler_library_path != binding.poppler_library_path
        ):
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_DRIFTED")
        _validate_component_layout(
            pdftoppm_path=pdftoppm_path,
            pdfinfo_path=pdfinfo_path,
            poppler_library_path=poppler_library_path,
            platform_name=binding.platform_name,
            error_code="POPPLER_RUNTIME_DRIFTED",
        )
        probe_version = version_probe or _default_version_probe
        if (
            probe_version(pdftoppm_path) != PINNED_POPPLER_VERSION
            or probe_version(pdfinfo_path) != PINNED_POPPLER_VERSION
        ):
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_DRIFTED")
        hash_file = file_hasher or _default_file_hasher
        pdftoppm_sha256 = hash_file(pdftoppm_path)
        poppler_dll_sha256 = hash_file(poppler_library_path)
        runtime_identity_sha256 = _runtime_identity_sha256(
            version=PINNED_POPPLER_VERSION,
            pdftoppm_sha256=pdftoppm_sha256,
            poppler_dll_sha256=poppler_dll_sha256,
        )
        if (
            pdftoppm_sha256 != binding.pdftoppm_sha256
            or poppler_dll_sha256 != binding.poppler_dll_sha256
            or runtime_identity_sha256 != binding.runtime_identity_sha256
        ):
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_DRIFTED")
    except PopplerRuntimeBindingError:
        raise
    except Exception:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_DRIFTED") from None


__all__ = [
    "PINNED_POPPLER_VERSION",
    "POPPLER_RUNTIME_IDENTITY_SCHEMA_VERSION",
    "PinnedPopplerRuntime",
    "PopplerRuntimeBindingError",
    "resolve_pinned_poppler_runtime",
    "verify_pinned_poppler_runtime",
]
