"""Loader-level negative for a real planted ``libjpeg.so.62``.

H3-4's container phase must assert exactly one collected and passed test in
this module. Off-container, that same test is collected. When
``IDIS_REQUIRE_LOADER_NEGATIVE`` is ``1`` and this host cannot execute, the
test fails with the reason. Otherwise it records
``loader_negative=NOT_EXECUTED: <reason>`` and prints one fixed line. This
module does not use ``pytest.skip``.

On a Linux host with ``ldd``, a system ``libjpeg.so.62``, and the governed
prefix ``/opt/idis-native/poppler-25.12.0/bin/pdftoppm``, the test copies the
real library bytes into a plant directory. Plain ``ldd`` with that directory
in ``LD_LIBRARY_PATH`` must resolve to the plant. The helper environment, after
pdf2image's ``poppler_path + ":" + existing LD_LIBRARY_PATH`` concatenation,
must not.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

_PREFIX = Path("/opt/idis-native/poppler-25.12.0")
_PDFTOPPM = _PREFIX / "bin" / "pdftoppm"
_LIBJPEG_NAME = "libjpeg.so.62"
_LIBJPEG_CANDIDATES = (
    Path("/usr/lib/x86_64-linux-gnu") / _LIBJPEG_NAME,
    Path("/lib/x86_64-linux-gnu") / _LIBJPEG_NAME,
    Path("/usr/lib64") / _LIBJPEG_NAME,
    Path("/usr/lib") / _LIBJPEG_NAME,
)


def _system_libjpeg() -> Path | None:
    for candidate in _LIBJPEG_CANDIDATES:
        if candidate.is_symlink() or candidate.is_file():
            return candidate
    return None


def _inapplicable_reason() -> str | None:
    """Return why this host cannot execute the loader negative."""

    if platform.system() != "Linux":
        return "cannot execute: host platform is not Linux"
    if shutil.which("ldd") is None:
        return "cannot execute: ldd is not on PATH"
    if _system_libjpeg() is None:
        return "cannot execute: system libjpeg.so.62 was not found"
    if not _PDFTOPPM.is_file():
        return "cannot execute: governed prefix pdftoppm is absent"
    return None


def test_planted_libjpeg_is_not_loaded_under_helper_environment(
    tmp_path: Path,
    record_property: pytest.RecordPropertyFixture,
) -> None:
    """A real plant resolves under plain ldd and not under the helper env."""

    reason = _inapplicable_reason()
    if reason is not None:
        if os.environ.get("IDIS_REQUIRE_LOADER_NEGATIVE") == "1":
            pytest.fail(reason)
        record_property("loader_negative", f"NOT_EXECUTED: {reason}")
        print(f"LOADER_NEGATIVE NOT_EXECUTED: {reason}")
        return
    _assert_real_plant_resolution(tmp_path)


def _assert_real_plant_resolution(tmp_path: Path) -> None:
    from idis.parsers.poppler_linux_runtime import install_poppler_child_environment
    from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime

    source = _system_libjpeg()
    assert source is not None
    plant_dir = tmp_path / "plant"
    plant_dir.mkdir()
    plant = plant_dir / _LIBJPEG_NAME
    plant.write_bytes(source.resolve().read_bytes())
    inherited = os.environ.copy()
    inherited["LD_LIBRARY_PATH"] = str(plant_dir)
    positive = _ldd(_PDFTOPPM, env=inherited, cwd=plant_dir)
    assert str(plant) in _libjpeg_line(positive)
    binding = resolve_pinned_poppler_runtime()
    child = install_poppler_child_environment(binding, apply_to_process=False)
    assert child is not None
    existing = child.get("LD_LIBRARY_PATH", "")
    # pdf2image.pdf2image: poppler_path + ":" + env.get("LD_LIBRARY_PATH", "")
    effective = dict(child)
    effective["LD_LIBRARY_PATH"] = str(binding.binary_dir) + ":" + existing
    assert str(plant_dir) not in effective["LD_LIBRARY_PATH"]
    assert inherited["LD_LIBRARY_PATH"] == str(plant_dir)
    negative = _ldd(binding.pdftoppm_path, env=effective, cwd=plant_dir)
    assert str(plant) not in negative
    assert str(plant_dir) not in negative
    assert _LIBJPEG_NAME in negative


def _ldd(binary: Path, *, env: dict[str, str], cwd: Path) -> str:
    completed = subprocess.run(
        ["ldd", str(binary)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout + completed.stderr


def _libjpeg_line(output: str) -> str:
    return next(line for line in output.splitlines() if _LIBJPEG_NAME in line)
