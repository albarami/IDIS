"""Published filesystem objects must be verified again on every read."""

from __future__ import annotations

import hashlib
import os
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from idis.storage import errors as storage_errors
from idis.storage.filesystem_store import FilesystemObjectStore

_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_KEY = "fictional/read-integrity.bin"
_PAYLOAD = b"fictional durable bytes that must remain immutable"


def _content_path(root: Path, version_id: str) -> Path:
    [path] = root.rglob(f"{version_id}.data")
    return path


def _assert_typed_integrity_failure(store: FilesystemObjectStore) -> None:
    expected_type = getattr(storage_errors, "ObjectIntegrityError", None)
    assert expected_type is not None, "object reads need a typed integrity failure"
    with pytest.raises(expected_type) as exc:
        store.get(_TENANT_ID, _KEY)

    assert exc.value.code == "OBJECT_INTEGRITY_FAILED"
    rendered = str(exc.value)
    assert _PAYLOAD.decode("ascii") not in rendered
    assert "durable bytes" not in rendered


def test_get_rejects_same_size_published_byte_corruption(tmp_path: Path) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_ID, _KEY, _PAYLOAD)
    path = _content_path(tmp_path, stored.version_id)
    corrupted = bytearray(path.read_bytes())
    corrupted[len(corrupted) // 2] ^= 0x01
    path.write_bytes(corrupted)

    _assert_typed_integrity_failure(store)


def test_get_rejects_published_size_drift(tmp_path: Path) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_ID, _KEY, _PAYLOAD)
    path = _content_path(tmp_path, stored.version_id)
    path.write_bytes(_PAYLOAD[:-1])

    _assert_typed_integrity_failure(store)


def test_get_rejects_a_file_that_changes_during_the_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching final digest is insufficient if the opened file's stat changed."""
    store = FilesystemObjectStore(base_dir=tmp_path)
    store.put(_TENANT_ID, _KEY, _PAYLOAD)
    real_fstat = os.fstat
    calls = 0

    def drifting_fstat(fd: int) -> object:
        nonlocal calls
        current = real_fstat(fd)
        calls += 1
        if calls != 2:
            return current
        return SimpleNamespace(
            st_dev=current.st_dev,
            st_ino=current.st_ino,
            st_mode=current.st_mode,
            st_size=current.st_size,
            st_mtime_ns=current.st_mtime_ns + 1,
            st_ctime_ns=current.st_ctime_ns,
        )

    monkeypatch.setattr("idis.storage.filesystem_store.os.fstat", drifting_fstat)

    _assert_typed_integrity_failure(store)
    assert calls >= 2


def test_verify_integrity_rehashes_in_bounded_reads_without_returning_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_ID, _KEY, _PAYLOAD)

    def forbidden_whole_file_read(_path: Path) -> bytes:
        raise AssertionError("integrity verification attempted a whole-file read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_whole_file_read)

    receipt = store.verify_integrity(_TENANT_ID, _KEY, version_id=stored.version_id)

    assert receipt == stored
    assert not hasattr(receipt, "body")


def test_verify_integrity_rejects_corrupted_durable_bytes(tmp_path: Path) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_ID, _KEY, _PAYLOAD)
    path = _content_path(tmp_path, stored.version_id)
    path.write_bytes(b"x" * len(_PAYLOAD))

    expected_type = storage_errors.ObjectIntegrityError
    with pytest.raises(expected_type) as exc:
        store.verify_integrity(_TENANT_ID, _KEY, version_id=stored.version_id)

    assert exc.value.code == "OBJECT_INTEGRITY_FAILED"


def test_open_verified_stream_reads_in_bounded_chunks_without_get_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x" * ((2 * 1024 * 1024) + 17)
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_ID, _KEY, payload)
    content_path = _content_path(tmp_path, stored.version_id)
    real_path_open = Path.open
    observed_read_sizes: list[int] = []

    class _BoundedReader:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __enter__(self) -> _BoundedReader:
            self._inner.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._inner.__exit__(*args)

        def fileno(self) -> int:
            return self._inner.fileno()

        def read(self, size: int = -1) -> bytes:
            observed_read_sizes.append(size)
            assert 0 < size <= 1024 * 1024
            return self._inner.read(size)

    def bounded_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        opened = real_path_open(path, *args, **kwargs)
        if path == content_path and args and args[0] == "rb":
            return _BoundedReader(opened)
        return opened

    def forbidden_get(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("verified stream called the materializing get path")

    monkeypatch.setattr(Path, "open", bounded_open)
    monkeypatch.setattr(store, "get", forbidden_get)

    metadata, chunks = store.open_verified_stream(
        _TENANT_ID,
        _KEY,
        version_id=stored.version_id,
    )

    assert metadata == stored
    assert b"".join(chunks) == payload
    assert observed_read_sizes
    assert len(observed_read_sizes) >= 4


def test_stream_retry_rebinds_open_file_to_published_path_after_atomic_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry cannot accept old open bytes after the published path is replaced."""
    store = FilesystemObjectStore(base_dir=tmp_path)
    first = store.put_stream(
        _TENANT_ID,
        _KEY,
        BytesIO(_PAYLOAD),
        expected_sha256=hashlib.sha256(_PAYLOAD).hexdigest(),
        expected_size_bytes=len(_PAYLOAD),
    )
    content_path = _content_path(tmp_path, first.version_id)
    retired_path = content_path.with_name(f".{first.version_id}.retired")
    replacement_path = content_path.with_name(f".{first.version_id}.replacement")
    replacement_path.write_bytes(b"x" * len(_PAYLOAD))

    real_path_open = Path.open
    replaced = False

    def replace_after_path_admission(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        nonlocal replaced
        if path == content_path and mode == "rb" and not replaced:
            content_path.replace(retired_path)
            replacement_path.replace(content_path)
            replaced = True
            path = retired_path
        return real_path_open(
            path,
            mode=mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "open", replace_after_path_admission)

    second = store.put_stream(
        _TENANT_ID,
        _KEY,
        BytesIO(_PAYLOAD),
        expected_sha256=hashlib.sha256(_PAYLOAD).hexdigest(),
        expected_size_bytes=len(_PAYLOAD),
    )

    assert replaced is True
    assert second.version_id != first.version_id
    assert store.get(_TENANT_ID, _KEY).body == _PAYLOAD
