"""Filesystem object versions must stay bound to their requested identity."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from idis.storage.errors import ObjectNotFoundError, PathTraversalError
from idis.storage.filesystem_store import FilesystemObjectStore

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_KEY = "fictional/version-identity.bin"


def _object_dir(root: Path, version_id: str) -> Path:
    [metadata_path] = root.rglob(f"{version_id}.meta.json")
    return metadata_path.parent


def _metadata_path(root: Path, version_id: str) -> Path:
    [metadata_path] = root.rglob(f"{version_id}.meta.json")
    return metadata_path


def _tamper_metadata(path: Path, field: str, value: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("operation", ("get", "head", "delete"))
@pytest.mark.parametrize(
    "unsafe_version_id",
    (
        "../escape",
        "..\\escape",
        "/absolute/escape",
        "11111111111111111111111111111111",
        "11111111-1111-1111-1111-111111111111/escape",
        "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
    ),
)
def test_explicit_version_id_must_be_a_canonical_uuid(
    tmp_path: Path,
    operation: str,
    unsafe_version_id: str,
) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_A, _KEY, b"fictional-safe-object")

    with pytest.raises(PathTraversalError, match="Invalid object version identifier") as exc:
        getattr(store, operation)(
            _TENANT_A,
            _KEY,
            version_id=unsafe_version_id,
        )

    assert unsafe_version_id not in str(exc.value)
    assert store.get(_TENANT_A, _KEY, version_id=stored.version_id).body == (
        b"fictional-safe-object"
    )


@pytest.mark.parametrize("operation", ("get", "head"))
@pytest.mark.parametrize(
    "poisoned_pointer",
    (
        "../escape",
        "..\\escape",
        "11111111111111111111111111111111",
        "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
        "11111111-1111-1111-1111-111111111111\n",
    ),
)
def test_latest_pointer_must_contain_a_canonical_uuid(
    tmp_path: Path,
    operation: str,
    poisoned_pointer: str,
) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_A, _KEY, b"fictional-safe-object")
    obj_dir = _object_dir(tmp_path, stored.version_id)
    (obj_dir / "_latest").write_text(poisoned_pointer, encoding="utf-8")

    with pytest.raises(PathTraversalError, match="Invalid object version identifier") as exc:
        getattr(store, operation)(_TENANT_A, _KEY)

    assert poisoned_pointer not in str(exc.value)


def test_poisoned_latest_pointer_cannot_delete_a_valid_version(tmp_path: Path) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_A, _KEY, b"fictional-safe-object")
    obj_dir = _object_dir(tmp_path, stored.version_id)
    (obj_dir / "_latest").write_text("../escape", encoding="utf-8")

    with pytest.raises(PathTraversalError, match="Invalid object version identifier"):
        store.delete(_TENANT_A, _KEY, version_id=stored.version_id)

    assert (obj_dir / f"{stored.version_id}.meta.json").is_file()
    assert (obj_dir / f"{stored.version_id}.data").is_file()


def test_non_utf8_latest_pointer_raises_only_the_safe_typed_error(tmp_path: Path) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_A, _KEY, b"fictional-safe-object")
    obj_dir = _object_dir(tmp_path, stored.version_id)
    (obj_dir / "_latest").write_bytes(b"\xff")

    with pytest.raises(PathTraversalError, match="Invalid object version identifier") as exc:
        store.get(_TENANT_A, _KEY)

    assert "decode" not in str(exc.value).lower()


@pytest.mark.parametrize("operation", ("get", "head", "delete"))
@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("tenant_id", _TENANT_B),
        ("key", "fictional/different-key.bin"),
        ("version_id", "33333333-3333-3333-3333-333333333333"),
    ),
)
def test_loaded_metadata_must_match_the_requested_object_identity(
    tmp_path: Path,
    operation: str,
    field: str,
    replacement: str,
) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_A, _KEY, b"fictional-safe-object")
    metadata_path = _metadata_path(tmp_path, stored.version_id)
    content_path = metadata_path.with_name(f"{stored.version_id}.data")
    _tamper_metadata(metadata_path, field, replacement)

    with pytest.raises(ObjectNotFoundError, match="Version not found"):
        getattr(store, operation)(
            _TENANT_A,
            _KEY,
            version_id=stored.version_id,
        )

    assert metadata_path.is_file()
    assert content_path.is_file()


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("tenant_id", _TENANT_B),
        ("key", "fictional/different-key.bin"),
        ("version_id", "33333333-3333-3333-3333-333333333333"),
    ),
)
def test_list_versions_excludes_metadata_with_a_mismatched_identity(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    stored = store.put(_TENANT_A, _KEY, b"fictional-safe-object")
    _tamper_metadata(_metadata_path(tmp_path, stored.version_id), field, replacement)

    assert store.list_versions(_TENANT_A, _KEY) == []


def test_stream_retry_does_not_return_metadata_with_a_mismatched_version(
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    payload = b"fictional-stream-object"
    first = store.put_stream(_TENANT_A, _KEY, BytesIO(payload))
    _tamper_metadata(
        _metadata_path(tmp_path, first.version_id),
        "version_id",
        str(uuid4()),
    )

    second = store.put_stream(_TENANT_A, _KEY, BytesIO(payload))

    assert UUID(second.version_id)
    assert second.version_id != first.version_id
    assert second.tenant_id == _TENANT_A
    assert second.key == _KEY


def test_valid_canonical_missing_version_preserves_not_found_error(tmp_path: Path) -> None:
    store = FilesystemObjectStore(base_dir=tmp_path)
    store.put(_TENANT_A, _KEY, b"fictional-safe-object")
    missing_version = str(uuid4())

    with pytest.raises(ObjectNotFoundError) as exc:
        store.get(_TENANT_A, _KEY, version_id=missing_version)

    assert exc.value.version_id == missing_version
