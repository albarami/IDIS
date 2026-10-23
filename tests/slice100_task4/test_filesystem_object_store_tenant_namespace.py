"""Tenant namespace aliases must never cross object-store isolation boundaries."""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import pytest

from idis.storage.errors import PathTraversalError
from idis.storage.filesystem_store import FilesystemObjectStore

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_KEY = "fictional/namespace-proof.bin"
_TENANT_B_BYTES = b"fictional-tenant-b-object"


@contextmanager
def _directory_alias(*, alias: Path, target: Path, kind: str) -> Iterator[None]:
    """Create a real directory alias, including an unprivileged Windows junction."""
    if kind == "junction":
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    else:
        try:
            alias.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlinks are unavailable: {type(exc).__name__}")

    try:
        yield
    finally:
        if alias.exists() or alias.is_symlink():
            if os.name == "nt":
                alias.rmdir()
            else:
                alias.unlink()


_ALIAS_KINDS = ("symlink", "junction") if os.name == "nt" else ("symlink",)


@pytest.mark.parametrize("alias_kind", _ALIAS_KINDS)
@pytest.mark.parametrize(
    "operation",
    ("put", "put_stream", "get", "head", "list_versions", "delete"),
)
def test_tenant_directory_alias_is_rejected_for_every_object_operation(
    tmp_path: Path,
    *,
    alias_kind: str,
    operation: str,
) -> None:
    """An A-to-B alias inside the base must not become tenant A's namespace."""
    store = FilesystemObjectStore(base_dir=tmp_path)
    tenant_b_metadata = store.put(_TENANT_B, _KEY, _TENANT_B_BYTES)
    tenant_a_dir = tmp_path / _TENANT_A
    tenant_b_dir = tmp_path / _TENANT_B

    with (
        _directory_alias(alias=tenant_a_dir, target=tenant_b_dir, kind=alias_kind),
        pytest.raises(PathTraversalError, match="tenant namespace"),
    ):
        if operation == "put":
            store.put(_TENANT_A, _KEY, b"fictional-tenant-a-object")
        elif operation == "put_stream":
            payload = b"fictional-tenant-a-stream"
            store.put_stream(
                _TENANT_A,
                _KEY,
                BytesIO(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_size_bytes=len(payload),
            )
        elif operation == "get":
            store.get(_TENANT_A, _KEY)
        elif operation == "head":
            store.head(_TENANT_A, _KEY)
        elif operation == "list_versions":
            store.list_versions(_TENANT_A, _KEY)
        else:
            store.delete(_TENANT_A, _KEY)

    tenant_b_object = store.get(
        _TENANT_B,
        _KEY,
        version_id=tenant_b_metadata.version_id,
    )
    assert tenant_b_object.body == _TENANT_B_BYTES
    assert tenant_b_object.metadata.tenant_id == _TENANT_B
