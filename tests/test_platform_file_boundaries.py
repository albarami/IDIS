"""Native file boundaries must stay platform-gated and process-exclusive."""

from __future__ import annotations

import importlib
import multiprocessing
import sys
from multiprocessing.connection import Connection
from pathlib import Path
from types import SimpleNamespace

import pytest

from idis.storage.filesystem_store import _exclusive_object_write_lock

_TENANT = "11111111-1111-1111-1111-111111111111"
_KEY = "fictional/native-lock.bin"


@pytest.mark.parametrize(
    ("module_name", "loader_name", "error_name", "code"),
    (
        (
            "idis.runtime.secure_files",
            "_windows_libraries",
            "SecureFileError",
            "SECURE_FILE_INVALID",
        ),
        (
            "idis.tools.real_example_source_lookup",
            "_windows_kernel32",
            "SourceLookupError",
            "SOURCE_LOOKUP_TTY_REQUIRED",
        ),
    ),
)
def test_windows_loader_rejects_unsupported_platform_before_native_access(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    loader_name: str,
    error_name: str,
    code: str,
) -> None:
    module = importlib.import_module(module_name)

    def forbidden_native_load(*args: object, **kwargs: object) -> None:
        raise AssertionError("native library accessed on an unsupported platform")

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"), raising=False)
    monkeypatch.setattr(module.ctypes, "WinDLL", forbidden_native_load, raising=False)

    with pytest.raises(getattr(module, error_name), match=f"^{code}$"):
        getattr(module, loader_name)()


def _native_lock_contender(obj_dir: str, connection: Connection) -> None:
    """Probe the real parent lock without blocking, then acquire after release."""
    lock_path = Path(obj_dir) / ".put-stream.lock"
    with lock_path.open("r+b") as lock_file:
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                connection.send("blocked")
            else:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                connection.send("unprotected")
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                connection.send("blocked")
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                connection.send("unprotected")
    if not connection.poll(15) or connection.recv() != "released":
        raise AssertionError("parent did not release native lock")
    with _exclusive_object_write_lock(Path(obj_dir), tenant_id=_TENANT, key=_KEY):
        connection.send("acquired")
    connection.close()


@pytest.mark.parametrize("body_raises", (False, True))
def test_object_write_lock_excludes_other_process_and_releases_after_body(
    tmp_path: Path, body_raises: bool
) -> None:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    contender = context.Process(target=_native_lock_contender, args=(str(tmp_path), child))
    try:
        try:
            with _exclusive_object_write_lock(tmp_path, tenant_id=_TENANT, key=_KEY):
                contender.start()
                child.close()
                assert parent.poll(15), "native lock probe did not complete"
                assert parent.recv() == "blocked"
                if body_raises:
                    raise ValueError("fictional write failure")
        except ValueError as exc:
            assert body_raises
            assert str(exc) == "fictional write failure"
        parent.send("released")
        assert parent.poll(15), "native lock was not released"
        assert parent.recv() == "acquired"
        contender.join(timeout=15)
        assert contender.exitcode == 0
        assert (tmp_path / ".put-stream.lock").read_bytes() == b"\0"
    finally:
        parent.close()
        child.close()
        if contender.pid is not None:
            contender.join(timeout=5)
            if contender.is_alive():
                contender.terminate()
                contender.join(timeout=5)
