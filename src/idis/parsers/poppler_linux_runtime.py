"""Linux prefix admission for the governed Poppler runtime.

Exactly one function, :func:`admit_linux_poppler_prefix`, decides whether a
tree is admitted. Other modules dispatch to it or reject an already admitted
Linux binding; they do not reimplement the closed set.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from idis.parsers.poppler_runtime import (
    PINNED_POPPLER_VERSION,
    PinnedPopplerRuntime,
    PopplerRuntimeBindingError,
)

LINUX_PREFIX_IDENTITY_SCHEMA_VERSION = "poppler-linux-prefix-identity-v1"
PINNED_POPPLER_LINUX_SOURCE_SHA256 = (
    "c18b40eb36b1a0c5b86e29ca054bf0770304583da4f2cdd42fe86eca6a20de48"
)
PINNED_POPPLER_LINUX_SOURCE_SIZE_BYTES = 1992456
PINNED_POPPLER_LINUX_BUILDER_RECIPE_SHA256 = (
    "e0ff366be570c7e30c5d354a51af83f76b6c2d05ee2bce73a43e68db8f9ae7fd"
)
PINNED_POPPLER_LINUX_PREFIX = "/opt/idis-native/poppler-25.12.0"
PINNED_POPPLER_LINUX_LIBRARY_NAME = "libpoppler.so.155.0.0"
_MANIFEST_RELATIVE = "share/idis/poppler-linux-prefix-identity-v1.json"
_INTERPRETER = "/lib64/ld-linux-x86-64.so.2"
_CHILD_PATH = "/usr/bin:/bin"
_CHILD_LOCALE = "C.UTF-8"
_FONTCONFIG_DIRNAME = "idis-poppler-fontconfig"
_HASH_CHUNK_BYTES = 1024 * 1024
_ELF_PATHS = frozenset(
    {
        "bin/pdfattach",
        "bin/pdfdetach",
        "bin/pdffonts",
        "bin/pdfimages",
        "bin/pdfinfo",
        "bin/pdfseparate",
        "bin/pdftocairo",
        "bin/pdftohtml",
        "bin/pdftoppm",
        "bin/pdftops",
        "bin/pdftotext",
        "bin/pdfunite",
        "lib/libpoppler.so.155.0.0",
    }
)
_FILE_PATHS = _ELF_PATHS | frozenset(
    {
        "lib/pkgconfig/poppler.pc",
        "share/man/man1/pdfattach.1",
        "share/man/man1/pdfdetach.1",
        "share/man/man1/pdffonts.1",
        "share/man/man1/pdfimages.1",
        "share/man/man1/pdfinfo.1",
        "share/man/man1/pdfseparate.1",
        "share/man/man1/pdftocairo.1",
        "share/man/man1/pdftohtml.1",
        "share/man/man1/pdftoppm.1",
        "share/man/man1/pdftops.1",
        "share/man/man1/pdftotext.1",
        "share/man/man1/pdfunite.1",
    }
)
_SYMLINK_TARGETS = {
    "lib/libpoppler.so": "libpoppler.so.155",
    "lib/libpoppler.so.155": "libpoppler.so.155.0.0",
}
_REQUIRED_FILES = (
    "bin/pdftoppm",
    "bin/pdfinfo",
    "lib/libpoppler.so.155.0.0",
)
_MANIFEST_KEYS = frozenset(
    {
        "files",
        "poppler_builder_recipe_sha256",
        "poppler_source_sha256",
        "poppler_source_size_bytes",
        "poppler_version",
        "prefix",
        "rpath",
        "schema_version",
        "symlinks",
    }
)
_PT_LOAD = 1
_PT_DYNAMIC = 2
_PT_INTERP = 3
_DT_NULL = 0
_DT_STRTAB = 5
_DT_STRSZ = 10
_DT_RPATH = 15
_DT_RUNPATH = 29

VersionProbe = Callable[[Path], str]


@dataclass(frozen=True, slots=True)
class _ElfDynamic:
    rpath: str | None
    runpath_present: bool
    interpreter: str | None


@dataclass(frozen=True, slots=True, repr=False)
class PinnedLinuxPopplerRuntime:
    """Immutable Linux prefix binding. Paths stay out of ``repr``."""

    version: str
    runtime_identity_sha256: str
    pdftoppm_sha256: str
    pdfinfo_sha256: str
    poppler_libpoppler_sha256: str
    poppler_source_sha256: str
    poppler_builder_recipe_sha256: str
    prefix: str
    rpath: str
    _pdftoppm_path: Path
    _pdfinfo_path: Path
    _poppler_library_path: Path

    def __post_init__(self) -> None:
        from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

        paths = (self._pdftoppm_path, self._pdfinfo_path, self._poppler_library_path)
        if (
            self.version != PINNED_POPPLER_VERSION
            or self.runtime_identity_sha256 != pinned_linux_poppler_identity_sha256(self)
            or any(not isinstance(path, Path) or not path.is_absolute() for path in paths)
            or self._poppler_library_path.name != PINNED_POPPLER_LINUX_LIBRARY_NAME
        ):
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")

    @property
    def pdftoppm_path(self) -> Path:
        """Return the admitted ``pdftoppm`` inside the prefix."""

        return self._pdftoppm_path

    @property
    def pdfinfo_path(self) -> Path:
        """Return the admitted ``pdfinfo`` inside the prefix."""

        return self._pdfinfo_path

    @property
    def poppler_library_path(self) -> Path:
        """Return the regular file ``libpoppler.so.155.0.0``."""

        return self._poppler_library_path

    @property
    def binary_dir(self) -> Path:
        """Return the prefix ``bin`` directory consumed by pdf2image."""

        return self._pdftoppm_path.parent

    def safe_provenance(self) -> dict[str, str]:
        """Return the path-free Linux projection. It is not the persisted record."""

        return {
            "poppler_version": self.version,
            "poppler_linux_pdftoppm_sha256": self.pdftoppm_sha256,
            "poppler_linux_pdfinfo_sha256": self.pdfinfo_sha256,
            "poppler_libpoppler_sha256": self.poppler_libpoppler_sha256,
            "poppler_linux_source_sha256": self.poppler_source_sha256,
            "poppler_linux_builder_recipe_sha256": self.poppler_builder_recipe_sha256,
            "poppler_linux_identity_sha256": self.runtime_identity_sha256,
        }

    def __repr__(self) -> str:
        return (
            "PinnedLinuxPopplerRuntime("
            f"version={self.version!r}, "
            f"runtime_identity_sha256={self.runtime_identity_sha256!r})"
        )


def pinned_linux_poppler_identity_sha256(binding: PinnedLinuxPopplerRuntime) -> str:
    """Recompute the Linux canonical digest from the binding's payload fields."""

    return _identity_sha256(
        version=binding.version,
        source_sha256=binding.poppler_source_sha256,
        recipe_sha256=binding.poppler_builder_recipe_sha256,
        prefix=binding.prefix,
        pdftoppm_sha256=binding.pdftoppm_sha256,
        pdfinfo_sha256=binding.pdfinfo_sha256,
        library_sha256=binding.poppler_libpoppler_sha256,
        rpath=binding.rpath,
    )


def _identity_sha256(
    *,
    version: str,
    source_sha256: str,
    recipe_sha256: str,
    prefix: str,
    pdftoppm_sha256: str,
    pdfinfo_sha256: str,
    library_sha256: str,
    rpath: str,
) -> str:
    payload = {
        "schema_version": LINUX_PREFIX_IDENTITY_SCHEMA_VERSION,
        "poppler_version": version,
        "poppler_source_sha256": source_sha256,
        "poppler_builder_recipe_sha256": recipe_sha256,
        "prefix": prefix,
        "pdftoppm_sha256": pdftoppm_sha256,
        "pdfinfo_sha256": pdfinfo_sha256,
        "poppler_libpoppler_sha256": library_sha256,
        "rpath": rpath,
        "runpath_absent": True,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_governed_worker_child = False
_governed_worker_child_pid: int | None = None


def mark_governed_worker_child() -> None:
    """Record that this process is the governed worker child.

    The worker entry calls this once. The call records nothing when
    ``multiprocessing.parent_process()`` is None. ``apply_to_process=True``
    also requires this flag and a recorded pid equal to ``os.getpid()``.
    An environment variable is not this marker.
    """

    global _governed_worker_child, _governed_worker_child_pid
    import multiprocessing

    if multiprocessing.parent_process() is None:
        return
    _governed_worker_child_pid = os.getpid()
    _governed_worker_child = True


def _in_governed_worker_child() -> bool:
    if not _governed_worker_child:
        return False
    if _governed_worker_child_pid != os.getpid():
        return False
    import multiprocessing

    return multiprocessing.parent_process() is not None


def install_poppler_child_environment(
    binding: object,
    *,
    apply_to_process: bool,
) -> dict[str, str] | None:
    """Return the Linux child mapping, applying it only in a governed child.

    The Windows type is a no-op: this returns ``None`` and does not mutate the
    process. A Linux binding returns the mapping pdf2image will copy.
    ``apply_to_process`` has no default. ``True`` mutates this process only
    inside a governed worker child; otherwise it raises
    ``POPPLER_RUNTIME_PARENT_MUTATION_FORBIDDEN`` before any change. ``chdir``
    stays inside that guarded child and is never applied in a parent.
    """

    if type(binding) is PinnedPopplerRuntime:
        return None
    if type(binding) is not PinnedLinuxPopplerRuntime:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    if apply_to_process and not _in_governed_worker_child():
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_PARENT_MUTATION_FORBIDDEN")
    mapping = _child_mapping(binding.poppler_library_path.parent)
    if apply_to_process:
        os.environ.update(mapping)
        os.environ.pop("LD_PRELOAD", None)
        os.environ.pop("LD_AUDIT", None)
        os.chdir(mapping["HOME"])
    return mapping


def probe_linux_poppler_version(executable: Path) -> str:
    """Run ``executable -v`` with the child mapping and without the parent env."""

    import subprocess

    mapping = _child_mapping(executable.resolve().parent.parent / "lib")
    try:
        completed = subprocess.run(
            [str(executable), "-v"],
            capture_output=True,
            timeout=10,
            check=False,
            env=mapping,
            cwd=mapping["HOME"],
        )
    except (OSError, subprocess.SubprocessError):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE") from None
    if completed.returncode != 0:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")
    output = (completed.stdout + b"\n" + completed.stderr)[:16384]
    marker = b"version "
    index = output.lower().find(marker)
    if index < 0:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    token = output[index + len(marker) :].split()[0]
    try:
        return token.decode("ascii")
    except UnicodeDecodeError:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID") from None


def admit_linux_poppler_prefix(
    prefix: Path,
    *,
    version_probe: VersionProbe | None = None,
    file_hasher: Callable[[Path], str] | None = None,
) -> PinnedLinuxPopplerRuntime:
    """Admit one closed Linux prefix, or raise a stable binding code.

    This is the only function that decides whether a tree is admitted.
    """

    if file_hasher is not None and not callable(file_hasher):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    try:
        resolved = Path(prefix).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE") from None
    if not resolved.is_dir():
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")
    for relative in _REQUIRED_FILES:
        if not _is_regular_file(resolved / relative):
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")
    manifest_path = resolved / _MANIFEST_RELATIVE
    if not _is_regular_file(manifest_path):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    manifest = _read_manifest(manifest_path)
    expected_rpath = str(resolved / "lib")
    if (
        manifest.get("schema_version") != LINUX_PREFIX_IDENTITY_SCHEMA_VERSION
        or manifest.get("poppler_version") != PINNED_POPPLER_VERSION
        or manifest.get("poppler_source_sha256") != PINNED_POPPLER_LINUX_SOURCE_SHA256
        or manifest.get("poppler_builder_recipe_sha256")
        != PINNED_POPPLER_LINUX_BUILDER_RECIPE_SHA256
        or type(manifest.get("poppler_source_size_bytes")) is not int
        or manifest.get("poppler_source_size_bytes") != PINNED_POPPLER_LINUX_SOURCE_SIZE_BYTES
        or manifest.get("prefix") != str(resolved)
        or manifest.get("rpath") != expected_rpath
    ):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    expected_files = _manifest_files(manifest)
    expected_links = _manifest_links(manifest)
    found_files, found_links, found_dirs = _walk_prefix(resolved)
    if _MANIFEST_RELATIVE not in found_files:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    found_files.pop(_MANIFEST_RELATIVE)
    if (
        set(found_files) != _FILE_PATHS
        or set(expected_files) != _FILE_PATHS
        or found_links != _SYMLINK_TARGETS
        or expected_links != _SYMLINK_TARGETS
        or found_dirs != _expected_directories()
    ):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    hashes: dict[str, str] = {}
    for relative, path in found_files.items():
        digest = _content_sha256(path, file_hasher)
        hashes[relative] = digest
        if digest != expected_files[relative]:
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    for relative in _ELF_PATHS:
        notes = _read_elf_dynamic(found_files[relative])
        if (
            notes is None
            or notes.runpath_present
            or notes.rpath != expected_rpath
            or notes.rpath is None
            or "$ORIGIN" in notes.rpath
        ):
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
        if relative.startswith("bin/") and notes.interpreter != _INTERPRETER:
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    probe = version_probe or probe_linux_poppler_version
    try:
        versions = (
            probe(found_files["bin/pdftoppm"]),
            probe(found_files["bin/pdfinfo"]),
        )
    except PopplerRuntimeBindingError:
        raise
    except Exception:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID") from None
    if versions != (PINNED_POPPLER_VERSION, PINNED_POPPLER_VERSION):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_VERSION_MISMATCH")
    digest = _identity_sha256(
        version=PINNED_POPPLER_VERSION,
        source_sha256=PINNED_POPPLER_LINUX_SOURCE_SHA256,
        recipe_sha256=PINNED_POPPLER_LINUX_BUILDER_RECIPE_SHA256,
        prefix=str(resolved),
        pdftoppm_sha256=hashes["bin/pdftoppm"],
        pdfinfo_sha256=hashes["bin/pdfinfo"],
        library_sha256=hashes["lib/libpoppler.so.155.0.0"],
        rpath=expected_rpath,
    )
    return PinnedLinuxPopplerRuntime(
        version=PINNED_POPPLER_VERSION,
        runtime_identity_sha256=digest,
        pdftoppm_sha256=hashes["bin/pdftoppm"],
        pdfinfo_sha256=hashes["bin/pdfinfo"],
        poppler_libpoppler_sha256=hashes["lib/libpoppler.so.155.0.0"],
        poppler_source_sha256=PINNED_POPPLER_LINUX_SOURCE_SHA256,
        poppler_builder_recipe_sha256=PINNED_POPPLER_LINUX_BUILDER_RECIPE_SHA256,
        prefix=str(resolved),
        rpath=expected_rpath,
        _pdftoppm_path=found_files["bin/pdftoppm"],
        _pdfinfo_path=found_files["bin/pdfinfo"],
        _poppler_library_path=found_files["lib/libpoppler.so.155.0.0"],
    )


def verify_pinned_linux_poppler_runtime(
    binding: PinnedLinuxPopplerRuntime,
    *,
    version_probe: VersionProbe | None = None,
    file_hasher: Callable[[Path], str] | None = None,
) -> None:
    """Re-admit the bound prefix and reject any drift from that binding."""

    if type(binding) is not PinnedLinuxPopplerRuntime:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    prefix = binding.pdftoppm_path.parent.parent
    try:
        fresh = admit_linux_poppler_prefix(
            prefix,
            version_probe=version_probe,
            file_hasher=file_hasher,
        )
    except PopplerRuntimeBindingError as exc:
        if exc.code == "POPPLER_RUNTIME_UNAVAILABLE":
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_DRIFTED") from None
        if exc.code == "POPPLER_RUNTIME_IDENTITY_INVALID":
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_DRIFTED") from None
        raise
    if (
        fresh.runtime_identity_sha256 != binding.runtime_identity_sha256
        or fresh.pdftoppm_path != binding.pdftoppm_path
        or fresh.pdfinfo_path != binding.pdfinfo_path
        or fresh.poppler_library_path != binding.poppler_library_path
    ):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_DRIFTED")


def _child_mapping(lib_dir: Path) -> dict[str, str]:
    directory = _fontconfig_directory()
    return {
        "HOME": str(directory),
        "LC_ALL": _CHILD_LOCALE,
        "LD_LIBRARY_PATH": str(lib_dir),
        "PATH": _CHILD_PATH,
        "XDG_CACHE_HOME": str(directory),
    }


def _fontconfig_directory() -> Path:
    """Return a private fontconfig home, or refuse a planted directory."""

    runner = os.environ.get("RUNNER_TEMP", "").strip()
    base = Path(runner) if runner else Path("/tmp")
    directory = base / _FONTCONFIG_DIRNAME
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        directory.mkdir(mode=0o700, parents=True)
        os.chmod(directory, 0o700)
        info = os.lstat(directory)
    except OSError:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID") from None
    _require_private_fontconfig(info)
    return directory


def _require_private_fontconfig(info: os.stat_result) -> None:
    """Reject a symlink, a foreign owner, or a mode other than 0700."""

    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return
    if info.st_uid != geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")


def _content_sha256(path: Path, file_hasher: Callable[[Path], str] | None) -> str:
    """Hash one admitted file, using the injected hasher when one was supplied."""

    if file_hasher is None:
        return _sha256_file(path)
    try:
        digest = file_hasher(path)
    except PopplerRuntimeBindingError:
        raise
    except Exception:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID") from None
    if type(digest) is not str:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    return digest


def _is_regular_file(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE") from None
    return digest.hexdigest()


def _read_manifest(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID") from None
    if type(payload) is not dict or set(payload) != _MANIFEST_KEYS:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    return payload


def _manifest_files(manifest: Mapping[str, object]) -> dict[str, str]:
    rows = manifest.get("files")
    if type(rows) is not list:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    found: dict[str, str] = {}
    for row in rows:
        if type(row) is not dict or set(row) != {"elf", "path", "sha256"}:
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
        path = row.get("path")
        digest = row.get("sha256")
        elf = row.get("elf")
        if (
            type(path) is not str
            or type(digest) is not str
            or type(elf) is not bool
            or len(digest) != 64
            or (path in _ELF_PATHS) is not elf
        ):
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
        found[path] = digest
    return found


def _manifest_links(manifest: Mapping[str, object]) -> dict[str, str]:
    rows = manifest.get("symlinks")
    if type(rows) is not list:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    found: dict[str, str] = {}
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "target"}:
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
        path = row.get("path")
        target = row.get("target")
        if type(path) is not str or type(target) is not str:
            raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
        found[path] = target
    return found


def _expected_directories() -> set[str]:
    relatives = set(_FILE_PATHS) | set(_SYMLINK_TARGETS) | {_MANIFEST_RELATIVE}
    directories: set[str] = set()
    for relative in relatives:
        parent = Path(relative).parent.as_posix()
        while parent not in {"", "."}:
            directories.add(parent)
            parent = Path(parent).parent.as_posix()
    return directories


def _walk_prefix(prefix: Path) -> tuple[dict[str, Path], dict[str, str], set[str]]:
    files: dict[str, Path] = {}
    links: dict[str, str] = {}
    directories: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(prefix, followlinks=False):
        current = Path(dirpath)
        relative_dir = current.relative_to(prefix).as_posix()
        if relative_dir != ".":
            directories.add(relative_dir)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            child = current / name
            relative = child.relative_to(prefix).as_posix()
            if child.is_symlink():
                links[relative] = os.readlink(child)
            elif _is_regular_file(child):
                files[relative] = child.resolve()
            else:
                raise PopplerRuntimeBindingError("POPPLER_RUNTIME_IDENTITY_INVALID")
    return files, links, directories


def _read_elf_dynamic(path: Path) -> _ElfDynamic | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return None
    try:
        _type, machine, _version, _entry, phoff, _shoff, _flags, _ehsize, phentsize, phnum = (
            struct.unpack_from("<HHIQQQIHHH", data, 16)
        )
    except struct.error:
        return None
    if machine != 62 or phoff <= 0 or phentsize < 56 or phnum <= 0:
        return None
    loads: list[tuple[int, int, int]] = []
    dynamic_offset: int | None = None
    dynamic_size = 0
    interpreter: str | None = None
    for index in range(phnum):
        start = phoff + index * phentsize
        try:
            kind, _flags, offset, vaddr, _paddr, filesz, _memsz, _align = struct.unpack_from(
                "<IIQQQQQQ",
                data,
                start,
            )
        except struct.error:
            return None
        if kind == _PT_LOAD:
            loads.append((offset, vaddr, filesz))
        elif kind == _PT_DYNAMIC:
            dynamic_offset = offset
            dynamic_size = filesz
        elif kind == _PT_INTERP:
            interpreter = _cstring(data, offset)
    if dynamic_offset is None:
        return None
    strtab: int | None = None
    rpath_indexes: list[int] = []
    runpath_present = False
    cursor = dynamic_offset
    end = dynamic_offset + dynamic_size
    while cursor + 16 <= end and cursor + 16 <= len(data):
        try:
            tag, value = struct.unpack_from("<qQ", data, cursor)
        except struct.error:
            return None
        cursor += 16
        if tag == _DT_NULL:
            break
        if tag == _DT_STRTAB:
            strtab = value
        elif tag == _DT_STRSZ:
            continue
        elif tag == _DT_RPATH:
            rpath_indexes.append(value)
        elif tag == _DT_RUNPATH:
            runpath_present = True
    if strtab is None or len(rpath_indexes) != 1:
        return _ElfDynamic(rpath=None, runpath_present=runpath_present, interpreter=interpreter)
    file_offset = _vaddr_to_offset(loads, strtab)
    if file_offset is None:
        return None
    rpath = _cstring(data, file_offset + rpath_indexes[0])
    return _ElfDynamic(rpath=rpath, runpath_present=runpath_present, interpreter=interpreter)


def _vaddr_to_offset(loads: list[tuple[int, int, int]], vaddr: int) -> int | None:
    for offset, base, size in loads:
        if base <= vaddr < base + size:
            return offset + (vaddr - base)
    return None


def _cstring(data: bytes, offset: int) -> str | None:
    if offset < 0 or offset >= len(data):
        return None
    end = data.find(b"\x00", offset)
    if end < 0:
        return None
    try:
        return data[offset:end].decode("ascii")
    except UnicodeDecodeError:
        return None


__all__ = [
    "LINUX_PREFIX_IDENTITY_SCHEMA_VERSION",
    "PINNED_POPPLER_LINUX_BUILDER_RECIPE_SHA256",
    "PINNED_POPPLER_LINUX_LIBRARY_NAME",
    "PINNED_POPPLER_LINUX_PREFIX",
    "PINNED_POPPLER_LINUX_SOURCE_SHA256",
    "PINNED_POPPLER_LINUX_SOURCE_SIZE_BYTES",
    "PinnedLinuxPopplerRuntime",
    "admit_linux_poppler_prefix",
    "install_poppler_child_environment",
    "mark_governed_worker_child",
    "pinned_linux_poppler_identity_sha256",
    "probe_linux_poppler_version",
    "verify_pinned_linux_poppler_runtime",
]
