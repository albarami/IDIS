"""IDIS Filesystem Object Storage backend.

Provides local filesystem storage for development and testing with:
- Tenant isolation via physical directory namespacing
- Path traversal protection
- SHA256 content hashing
- Content-addressed versioning with "latest" pointer

Environment Variables:
    IDIS_OBJECT_STORE_BASE_DIR: Base directory for storage
        (default: tempfile.gettempdir() / idis_objects)
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from idis.storage.errors import (
    ObjectIntegrityError,
    ObjectNotFoundError,
    PathTraversalError,
    StorageBackendError,
)
from idis.storage.models import StoredObject, StoredObjectMetadata
from idis.storage.object_store import BinaryReader, ObjectStore, StreamSource
from idis.storage.tracing import traced_storage_operation

logger = logging.getLogger(__name__)

IDIS_OBJECT_STORE_BASE_DIR_ENV = "IDIS_OBJECT_STORE_BASE_DIR"

_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_SAFE_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_\-./]+$")

_LATEST_POINTER = "_latest"
_METADATA_SUFFIX = ".meta.json"
_CONTENT_SUFFIX = ".data"
_STREAM_READ_CHUNK_BYTES = 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_WRITE_LOCK_FILENAME = ".put-stream.lock"


def _validate_tenant_id(tenant_id: str) -> bool:
    """Validate that tenant_id is a valid UUID format."""
    return bool(_UUID_PATTERN.match(tenant_id))


def _is_canonical_version_id(version_id: object) -> bool:
    """Return whether a version identifier is the canonical UUID spelling."""
    if not isinstance(version_id, str):
        return False
    try:
        return str(uuid.UUID(version_id)) == version_id
    except (AttributeError, ValueError):
        return False


def _validate_version_id(version_id: object, *, tenant_id: str, key: str) -> str:
    """Reject unsafe or non-canonical version identifiers before path creation."""
    if not _is_canonical_version_id(version_id):
        raise PathTraversalError(
            message="Invalid object version identifier",
            tenant_id=tenant_id,
            key=key,
        )
    return cast(str, version_id)


def _is_path_traversal(key: str) -> bool:
    """Check if a key contains path traversal sequences.

    Detects:
    - ".." segments
    - Absolute paths (starting with / or drive letters like C:)
    - Backslashes (Windows path separators)
    - Null bytes
    - Keys that resolve outside base directory
    """
    # Empty key is traversal attempt
    if not key:
        return not key  # equivalent to True for empty string

    # Null byte injection
    has_null_byte = "\x00" in key
    if has_null_byte:
        return has_null_byte

    # Windows backslash
    has_backslash = "\\" in key
    if has_backslash:
        return has_backslash

    # Absolute path indicators
    starts_absolute = key.startswith("/") or key.startswith("~")
    if starts_absolute:
        return starts_absolute

    # Windows drive letter (e.g., C:)
    has_drive_letter = len(key) >= 2 and key[1] == ":"
    if has_drive_letter:
        return has_drive_letter

    # Check for .. traversal in segments
    segments = key.replace("\\", "/").split("/")
    has_dotdot = any(segment == ".." for segment in segments)
    if has_dotdot:
        return has_dotdot

    return not bool(_SAFE_KEY_PATTERN.match(key))


def _validate_key(key: str, tenant_id: str) -> None:
    """Validate object key and raise if invalid."""
    if _is_path_traversal(key):
        raise PathTraversalError(
            message="Invalid key: path traversal or unsafe characters detected",
            tenant_id=tenant_id,
            key=key,
        )


def _compute_sha256(data: bytes) -> str:
    """Compute SHA256 hash of data and return as hex string."""
    return hashlib.sha256(data).hexdigest()


def _normalized_resolved_path(path: Path) -> Path:
    """Normalize equivalent Windows extended-length paths for containment checks."""
    resolved = str(path.resolve())
    if os.name == "nt":
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = f"\\\\{resolved[8:]}"
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[4:]
        resolved = os.path.normcase(os.path.normpath(resolved))
    return Path(resolved)


def _normalized_absolute_path(path: Path) -> Path:
    """Normalize a path lexically, without following filesystem aliases."""
    absolute = os.path.abspath(path)
    if os.name == "nt":
        if absolute.startswith("\\\\?\\UNC\\"):
            absolute = f"\\\\{absolute[8:]}"
        elif absolute.startswith("\\\\?\\"):
            absolute = absolute[4:]
        absolute = os.path.normcase(os.path.normpath(absolute))
    return Path(absolute)


@contextmanager
def _exclusive_object_write_lock(
    obj_dir: Path,
    *,
    tenant_id: str,
    key: str,
) -> Iterator[None]:
    """Serialize version publication across threads and worker processes."""
    lock_path = obj_dir / _WRITE_LOCK_FILENAME
    acquired = False
    try:
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl = cast(Any, importlib.import_module("fcntl"))
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            acquired = True
            try:
                yield
            finally:
                if acquired:
                    lock_file.seek(0)
                    try:
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl = cast(Any, importlib.import_module("fcntl"))
                            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except OSError as exc:
                        logger.warning(
                            "Failed to release filesystem object write lock: %s",
                            type(exc).__name__,
                        )
    except OSError as exc:
        raise StorageBackendError(
            message="Failed to acquire filesystem object write lock",
            tenant_id=tenant_id,
            key=key,
            cause=exc,
        ) from exc


class FilesystemObjectStore(ObjectStore):
    """Filesystem-based object storage implementation.

    Objects are stored in a directory structure:
        {base_dir}/{tenant_id}/{key_hash}/
            _latest                 # pointer to latest version_id
            {version_id}.data       # content
            {version_id}.meta.json  # metadata

    Version IDs are UUIDs (not content-addressed) to allow storing
    identical content as separate versions.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        """Initialize filesystem storage.

        Args:
            base_dir: Base directory for storage. If None, uses
                IDIS_OBJECT_STORE_BASE_DIR env var or OS temp directory.
        """
        if base_dir is None:
            base_dir = os.environ.get(IDIS_OBJECT_STORE_BASE_DIR_ENV)

        if base_dir is None:
            base_dir = Path(tempfile.gettempdir()) / "idis_objects"
        else:
            base_dir = Path(base_dir)

        self._base_dir = base_dir.resolve()
        logger.debug("FilesystemObjectStore initialized with base_dir=%s", self._base_dir)

    @property
    def backend_name(self) -> str:
        """Return the backend identifier."""
        return "filesystem"

    @property
    def base_dir(self) -> Path:
        """Return the base directory path."""
        return self._base_dir

    def _get_tenant_dir(self, tenant_id: str) -> Path:
        """Get the directory for a tenant, validating tenant_id."""
        if not _validate_tenant_id(tenant_id):
            raise StorageBackendError(
                message=f"Invalid tenant_id format: {tenant_id}",
                tenant_id=tenant_id,
            )
        return self._base_dir / tenant_id

    def _get_object_dir(self, tenant_id: str, key: str) -> Path:
        """Get the directory for an object, validating inputs.

        Uses a hash of the key to create a safe filesystem path.
        """
        _validate_key(key, tenant_id)
        tenant_dir = self._get_tenant_dir(tenant_id)
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        safe_key = re.sub(r"[^a-zA-Z0-9_\-]", "_", key)[:64]
        return tenant_dir / f"{safe_key}_{key_hash}"

    def _ensure_resolved_within_base(self, path: Path, tenant_id: str, key: str) -> Path:
        """Reject paths that escape or alias the requested tenant namespace.

        Merely remaining under ``base_dir`` is insufficient: a symlink or Windows
        junction named for tenant A could otherwise resolve to tenant B's directory.
        The canonical path must therefore retain the exact lexical tenant identity.
        """
        tenant_dir = self._get_tenant_dir(tenant_id)
        expected_base = _normalized_absolute_path(self._base_dir)
        expected_tenant_dir = _normalized_absolute_path(tenant_dir)
        expected_path = _normalized_absolute_path(path)
        try:
            expected_tenant_dir.relative_to(expected_base)
            expected_path.relative_to(expected_tenant_dir)
        except ValueError as e:
            raise PathTraversalError(
                message="Path is outside the requested tenant namespace",
                tenant_id=tenant_id,
                key=key,
            ) from e

        try:
            resolved_tenant_dir = _normalized_resolved_path(tenant_dir)
            resolved = _normalized_resolved_path(path)
        except (OSError, RuntimeError) as e:
            raise PathTraversalError(
                message="Path cannot be resolved safely inside the tenant namespace",
                tenant_id=tenant_id,
                key=key,
            ) from e

        if resolved_tenant_dir != expected_tenant_dir or resolved != expected_path:
            raise PathTraversalError(
                message="Path resolves through an alias or reparse point in the tenant namespace",
                tenant_id=tenant_id,
                key=key,
            )

        try:
            resolved.relative_to(resolved_tenant_dir)
        except ValueError as e:
            raise PathTraversalError(
                message="Path resolves outside the requested tenant namespace",
                tenant_id=tenant_id,
                key=key,
            ) from e
        return resolved

    def _read_latest_pointer(
        self,
        obj_dir: Path,
        *,
        tenant_id: str,
        key: str,
    ) -> str | None:
        """Read the latest version pointer file."""
        latest_file = obj_dir / _LATEST_POINTER
        if not latest_file.exists():
            return None
        try:
            version_id = latest_file.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise PathTraversalError(
                message="Invalid object version identifier",
                tenant_id=tenant_id,
                key=key,
            ) from exc
        except OSError:
            return None
        return _validate_version_id(version_id, tenant_id=tenant_id, key=key)

    def _write_latest_pointer(self, obj_dir: Path, version_id: str) -> None:
        """Write the latest version pointer file atomically."""
        latest_file = obj_dir / _LATEST_POINTER
        tmp_file = obj_dir / f"_latest.{uuid.uuid4().hex}.tmp"
        try:
            tmp_file.write_text(version_id, encoding="utf-8")
            tmp_file.replace(latest_file)
        except OSError as e:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            raise StorageBackendError(
                message=f"Failed to write latest pointer: {e}",
                cause=e,
            ) from e

    def _read_metadata(
        self,
        obj_dir: Path,
        version_id: str,
        *,
        tenant_id: str,
        key: str,
    ) -> StoredObjectMetadata | None:
        """Read metadata only when its complete object identity is bound."""
        version_id = _validate_version_id(version_id, tenant_id=tenant_id, key=key)
        meta_file = obj_dir / f"{version_id}{_METADATA_SUFFIX}"
        if not meta_file.exists():
            return None
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            metadata = StoredObjectMetadata.from_dict(data)
        except (OSError, ValueError, TypeError, KeyError) as e:
            logger.warning("Failed to read metadata %s: %s", meta_file, e)
            return None
        if (
            metadata.tenant_id != tenant_id
            or metadata.key != key
            or metadata.version_id != version_id
        ):
            logger.warning("Object metadata identity does not match its requested namespace")
            return None
        return metadata

    def _write_metadata(
        self,
        obj_dir: Path,
        version_id: str,
        metadata: StoredObjectMetadata,
    ) -> None:
        """Write metadata for a specific version atomically."""
        meta_file = obj_dir / f"{version_id}{_METADATA_SUFFIX}"
        tmp_file = obj_dir / f"{version_id}.meta.{uuid.uuid4().hex}.tmp"
        try:
            tmp_file.write_text(
                json.dumps(metadata.to_dict(), indent=2),
                encoding="utf-8",
            )
            tmp_file.replace(meta_file)
        except OSError as e:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            raise StorageBackendError(
                message=f"Failed to write metadata: {e}",
                cause=e,
            ) from e

    def _read_content(
        self,
        obj_dir: Path,
        version_id: str,
        *,
        metadata: StoredObjectMetadata,
        tenant_id: str,
        key: str,
        collect_body: bool = True,
    ) -> tuple[bool, bytes | None]:
        """Read content only when bytes, metadata, and file identity stay bound."""
        content_file = obj_dir / f"{version_id}{_CONTENT_SUFFIX}"
        try:
            initial_path_stat = os.stat(content_file, follow_symlinks=False)
        except FileNotFoundError:
            return False, None
        except OSError as exc:
            raise ObjectIntegrityError(
                tenant_id=tenant_id,
                key=key,
                cause=exc,
            ) from exc

        if not stat.S_ISREG(initial_path_stat.st_mode):
            raise ObjectIntegrityError(tenant_id=tenant_id, key=key)

        try:
            digest = hashlib.sha256()
            body = bytearray() if collect_body else None
            observed_size = 0
            with content_file.open("rb") as content:
                opened_before = os.fstat(content.fileno())
                path_before = os.stat(content_file, follow_symlinks=False)
                if (
                    not stat.S_ISREG(path_before.st_mode)
                    or self._read_stat_signature(initial_path_stat)
                    != self._read_stat_signature(path_before)
                    or self._read_stat_signature(path_before)
                    != self._read_stat_signature(opened_before)
                ):
                    raise ObjectIntegrityError(tenant_id=tenant_id, key=key)
                while True:
                    chunk = content.read(_STREAM_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
                    observed_size += len(chunk)
                    if body is not None:
                        body.extend(chunk)
                opened_after = os.fstat(content.fileno())
            path_after = os.stat(content_file, follow_symlinks=False)
        except ObjectIntegrityError:
            raise
        except OSError as exc:
            raise ObjectIntegrityError(
                tenant_id=tenant_id,
                key=key,
                cause=exc,
            ) from exc

        expected_sha256 = metadata.sha256.lower()
        if (
            not _SHA256_PATTERN.fullmatch(expected_sha256)
            or observed_size != metadata.size_bytes
            or digest.hexdigest() != expected_sha256
            or self._read_stat_signature(opened_before) != self._read_stat_signature(opened_after)
            or self._read_stat_signature(opened_after) != self._read_stat_signature(path_after)
        ):
            raise ObjectIntegrityError(tenant_id=tenant_id, key=key)
        return True, bytes(body) if body is not None else None

    def _iter_verified_content(
        self,
        obj_dir: Path,
        version_id: str,
        *,
        metadata: StoredObjectMetadata,
        tenant_id: str,
        key: str,
    ) -> Iterator[bytes]:
        """Yield bounded chunks while preserving published-file identity."""
        content_file = obj_dir / f"{version_id}{_CONTENT_SUFFIX}"
        try:
            initial_path_stat = os.stat(content_file, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(
                message="Version content not found",
                tenant_id=tenant_id,
                key=key,
                version_id=version_id,
            ) from exc
        except OSError as exc:
            raise ObjectIntegrityError(
                tenant_id=tenant_id,
                key=key,
                cause=exc,
            ) from exc

        if not stat.S_ISREG(initial_path_stat.st_mode):
            raise ObjectIntegrityError(tenant_id=tenant_id, key=key)

        try:
            digest = hashlib.sha256()
            observed_size = 0
            with content_file.open("rb") as content:
                opened_before = os.fstat(content.fileno())
                path_before = os.stat(content_file, follow_symlinks=False)
                if (
                    not stat.S_ISREG(path_before.st_mode)
                    or self._read_stat_signature(initial_path_stat)
                    != self._read_stat_signature(path_before)
                    or self._read_stat_signature(path_before)
                    != self._read_stat_signature(opened_before)
                ):
                    raise ObjectIntegrityError(tenant_id=tenant_id, key=key)
                while True:
                    chunk = content.read(_STREAM_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
                    observed_size += len(chunk)
                    if observed_size > metadata.size_bytes:
                        raise ObjectIntegrityError(tenant_id=tenant_id, key=key)
                    yield chunk
                opened_after = os.fstat(content.fileno())
            path_after = os.stat(content_file, follow_symlinks=False)
        except (ObjectIntegrityError, ObjectNotFoundError):
            raise
        except OSError as exc:
            raise ObjectIntegrityError(
                tenant_id=tenant_id,
                key=key,
                cause=exc,
            ) from exc

        expected_sha256 = metadata.sha256.lower()
        if (
            not _SHA256_PATTERN.fullmatch(expected_sha256)
            or observed_size != metadata.size_bytes
            or digest.hexdigest() != expected_sha256
            or self._read_stat_signature(opened_before) != self._read_stat_signature(opened_after)
            or self._read_stat_signature(opened_after) != self._read_stat_signature(path_after)
        ):
            raise ObjectIntegrityError(tenant_id=tenant_id, key=key)

    @staticmethod
    def _read_stat_signature(file_stat: Any) -> tuple[int, int, int, int, int]:
        """Return mutation-sensitive identity fields for a durable object read."""
        return (
            int(file_stat.st_dev),
            int(file_stat.st_ino),
            int(file_stat.st_mode),
            int(file_stat.st_size),
            int(file_stat.st_mtime_ns),
        )

    def _write_content(self, obj_dir: Path, version_id: str, data: bytes) -> None:
        """Write content for a specific version atomically."""
        content_file = obj_dir / f"{version_id}{_CONTENT_SUFFIX}"
        tmp_file = obj_dir / f"{version_id}.data.{uuid.uuid4().hex}.tmp"
        try:
            tmp_file.write_bytes(data)
            tmp_file.replace(content_file)
        except OSError as e:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            raise StorageBackendError(
                message=f"Failed to write content: {e}",
                cause=e,
            ) from e

    @traced_storage_operation("put")
    def put(
        self,
        tenant_id: str,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> StoredObjectMetadata:
        """Store an object."""
        obj_dir = self._get_object_dir(tenant_id, key)
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)

        try:
            obj_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StorageBackendError(
                message=f"Failed to create object directory: {e}",
                tenant_id=tenant_id,
                key=key,
                cause=e,
            ) from e
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)

        version_id = str(uuid.uuid4())
        sha256 = _compute_sha256(data)
        now = datetime.now(UTC)

        metadata = StoredObjectMetadata(
            tenant_id=tenant_id,
            key=key,
            version_id=version_id,
            sha256=sha256,
            size_bytes=len(data),
            content_type=content_type,
            created_at=now,
        )

        self._write_content(obj_dir, version_id, data)
        self._write_metadata(obj_dir, version_id, metadata)
        self._write_latest_pointer(obj_dir, version_id)

        logger.debug(
            "Stored object: tenant=%s key=%s version=%s sha256=%s",
            tenant_id,
            key,
            version_id,
            sha256,
        )

        return metadata

    @staticmethod
    def _validate_stream_expectations(
        *,
        tenant_id: str,
        key: str,
        expected_sha256: str | None,
        expected_size_bytes: int | None,
    ) -> tuple[str | None, int | None]:
        """Normalize fail-closed stream integrity expectations."""
        normalized_sha256: str | None = None
        if expected_sha256 is not None:
            if not isinstance(expected_sha256, str) or not _SHA256_PATTERN.fullmatch(
                expected_sha256
            ):
                raise StorageBackendError(
                    message="Expected SHA-256 must be exactly 64 hexadecimal characters",
                    tenant_id=tenant_id,
                    key=key,
                )
            normalized_sha256 = expected_sha256.lower()

        if expected_size_bytes is not None and (
            isinstance(expected_size_bytes, bool)
            or not isinstance(expected_size_bytes, int)
            or expected_size_bytes < 0
        ):
            raise StorageBackendError(
                message="Expected object size must be a non-negative integer",
                tenant_id=tenant_id,
                key=key,
            )
        return normalized_sha256, expected_size_bytes

    def _stream_source_to_temp(
        self,
        *,
        obj_dir: Path,
        tenant_id: str,
        key: str,
        source: StreamSource,
        expected_size_bytes: int | None,
    ) -> tuple[Path, str, int]:
        """Copy a source into one unpublished file using bounded reads."""
        temp_path = obj_dir / f".stream-{uuid.uuid4().hex}.tmp"
        try:
            with ExitStack() as stack:
                reader: BinaryReader
                if isinstance(source, (str, os.PathLike)):
                    reader = stack.enter_context(Path(source).open("rb"))
                else:
                    reader = source

                digest = hashlib.sha256()
                size_bytes = 0
                descriptor = os.open(
                    temp_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                try:
                    output_file = os.fdopen(descriptor, "wb")
                except BaseException:
                    with suppress(OSError):
                        os.close(descriptor)
                    raise
                output = stack.enter_context(output_file)
                while True:
                    raw_chunk = reader.read(_STREAM_READ_CHUNK_BYTES)
                    if not isinstance(raw_chunk, (bytes, bytearray, memoryview)):
                        raise StorageBackendError(
                            message="Stream source returned a non-binary chunk",
                            tenant_id=tenant_id,
                            key=key,
                        )
                    chunk = memoryview(raw_chunk)
                    if chunk.nbytes == 0:
                        break
                    if chunk.nbytes > _STREAM_READ_CHUNK_BYTES:
                        raise StorageBackendError(
                            message="Stream source violated the bounded-read contract",
                            tenant_id=tenant_id,
                            key=key,
                        )
                    next_size = size_bytes + chunk.nbytes
                    if expected_size_bytes is not None and next_size > expected_size_bytes:
                        raise StorageBackendError(
                            message="Stream exceeded the expected object size",
                            tenant_id=tenant_id,
                            key=key,
                        )
                    digest.update(chunk)
                    written = 0
                    while written < chunk.nbytes:
                        count = output.write(chunk[written:])
                        if count is None or count <= 0:
                            raise StorageBackendError(
                                message="Stream write made no progress",
                                tenant_id=tenant_id,
                                key=key,
                            )
                        written += count
                    size_bytes = next_size
                output.flush()
                os.fsync(output.fileno())
        except StorageBackendError:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)
            raise StorageBackendError(
                message="Failed to stage streamed object content",
                tenant_id=tenant_id,
                key=key,
                cause=exc,
            ) from exc

        return temp_path, digest.hexdigest(), size_bytes

    def _matching_stream_version(
        self,
        *,
        obj_dir: Path,
        tenant_id: str,
        key: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata | None:
        """Find one already-published version matching streamed content."""
        for version_id in self._list_version_ids(
            obj_dir,
            tenant_id=tenant_id,
            key=key,
        ):
            metadata = self._read_metadata(
                obj_dir,
                version_id,
                tenant_id=tenant_id,
                key=key,
            )
            if metadata is None:
                continue
            content_path = obj_dir / f"{version_id}{_CONTENT_SUFFIX}"
            if (
                metadata.tenant_id == tenant_id
                and metadata.key == key
                and metadata.sha256 == sha256
                and metadata.size_bytes == size_bytes
                and self._stream_content_matches(
                    content_path,
                    expected_sha256=sha256,
                    expected_size_bytes=size_bytes,
                )
            ):
                return metadata
        return None

    @staticmethod
    def _stream_content_matches(
        content_path: Path,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> bool:
        """Verify immutable published bytes before treating a retry as idempotent."""
        try:
            path_before = os.stat(content_path, follow_symlinks=False)
            if not stat.S_ISREG(path_before.st_mode):
                return False
            digest = hashlib.sha256()
            size_bytes = 0
            with content_path.open("rb") as content:
                opened_before = os.fstat(content.fileno())
                if (
                    opened_before.st_size != expected_size_bytes
                    or FilesystemObjectStore._read_stat_signature(path_before)
                    != FilesystemObjectStore._read_stat_signature(opened_before)
                ):
                    return False
                while True:
                    chunk = content.read(_STREAM_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size_bytes += len(chunk)
                opened_after = os.fstat(content.fileno())
            path_after = os.stat(content_path, follow_symlinks=False)
            return (
                size_bytes == expected_size_bytes
                and digest.hexdigest() == expected_sha256
                and stat.S_ISREG(path_after.st_mode)
                and FilesystemObjectStore._read_stat_signature(opened_before)
                == FilesystemObjectStore._read_stat_signature(opened_after)
                and FilesystemObjectStore._read_stat_signature(opened_after)
                == FilesystemObjectStore._read_stat_signature(path_after)
            )
        except OSError:
            return False

    def _publish_stream_temp(
        self,
        *,
        obj_dir: Path,
        tenant_id: str,
        key: str,
        temp_path: Path,
        sha256: str,
        size_bytes: int,
        content_type: str | None,
    ) -> StoredObjectMetadata:
        """Publish staged content atomically while holding the object lock."""
        matching = self._matching_stream_version(
            obj_dir=obj_dir,
            tenant_id=tenant_id,
            key=key,
            sha256=sha256,
            size_bytes=size_bytes,
        )
        if matching is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as exc:
                raise StorageBackendError(
                    message="Failed to remove idempotent stream retry content",
                    tenant_id=tenant_id,
                    key=key,
                    cause=exc,
                ) from exc
            return matching

        version_id = str(uuid.uuid4())
        metadata = StoredObjectMetadata(
            tenant_id=tenant_id,
            key=key,
            version_id=version_id,
            sha256=sha256,
            size_bytes=size_bytes,
            content_type=content_type,
            created_at=datetime.now(UTC),
        )
        content_path = obj_dir / f"{version_id}{_CONTENT_SUFFIX}"
        metadata_path = obj_dir / f"{version_id}{_METADATA_SUFFIX}"
        content_published = False
        try:
            temp_path.replace(content_path)
            content_published = True
            self._write_metadata(obj_dir, version_id, metadata)
            self._write_latest_pointer(obj_dir, version_id)
        except Exception as exc:
            with suppress(OSError):
                metadata_path.unlink(missing_ok=True)
            if content_published:
                with suppress(OSError):
                    content_path.unlink(missing_ok=True)
            if isinstance(exc, StorageBackendError):
                raise
            raise StorageBackendError(
                message="Failed to publish streamed object content",
                tenant_id=tenant_id,
                key=key,
                cause=exc,
            ) from exc
        return metadata

    @traced_storage_operation("put_stream")
    def put_stream(
        self,
        tenant_id: str,
        key: str,
        source: StreamSource,
        *,
        content_type: str | None = None,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
    ) -> StoredObjectMetadata:
        """Store a bounded stream with atomic, same-hash retry semantics."""
        normalized_sha256, normalized_size = self._validate_stream_expectations(
            tenant_id=tenant_id,
            key=key,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
        )
        obj_dir = self._get_object_dir(tenant_id, key)
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)
        try:
            obj_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageBackendError(
                message="Failed to create object directory",
                tenant_id=tenant_id,
                key=key,
                cause=exc,
            ) from exc
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)

        temp_path: Path | None = None
        try:
            temp_path, actual_sha256, actual_size = self._stream_source_to_temp(
                obj_dir=obj_dir,
                tenant_id=tenant_id,
                key=key,
                source=source,
                expected_size_bytes=normalized_size,
            )
            if normalized_size is not None and actual_size != normalized_size:
                raise StorageBackendError(
                    message="Stream size does not match the expected object size",
                    tenant_id=tenant_id,
                    key=key,
                )
            if normalized_sha256 is not None and actual_sha256 != normalized_sha256:
                raise StorageBackendError(
                    message="Stream SHA-256 does not match the expected digest",
                    tenant_id=tenant_id,
                    key=key,
                )

            with _exclusive_object_write_lock(
                obj_dir,
                tenant_id=tenant_id,
                key=key,
            ):
                metadata = self._publish_stream_temp(
                    obj_dir=obj_dir,
                    tenant_id=tenant_id,
                    key=key,
                    temp_path=temp_path,
                    sha256=actual_sha256,
                    size_bytes=actual_size,
                    content_type=content_type,
                )
            logger.debug(
                "Streamed object stored: tenant=%s key=%s version=%s sha256=%s",
                tenant_id,
                key,
                metadata.version_id,
                metadata.sha256,
            )
            return metadata
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(
                        "Failed to remove unpublished stream temp file: %s",
                        type(exc).__name__,
                    )

    @traced_storage_operation("get")
    def get(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> StoredObject:
        """Retrieve an object."""
        obj_dir = self._get_object_dir(tenant_id, key)
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)
        if version_id is not None:
            version_id = _validate_version_id(version_id, tenant_id=tenant_id, key=key)

        if not obj_dir.exists():
            raise ObjectNotFoundError(
                message="Object not found",
                tenant_id=tenant_id,
                key=key,
            )

        if version_id is None:
            version_id = self._read_latest_pointer(
                obj_dir,
                tenant_id=tenant_id,
                key=key,
            )
            if version_id is None:
                raise ObjectNotFoundError(
                    message="Object has no versions",
                    tenant_id=tenant_id,
                    key=key,
                )

        metadata = self._read_metadata(
            obj_dir,
            version_id,
            tenant_id=tenant_id,
            key=key,
        )
        if metadata is None:
            raise ObjectNotFoundError(
                message="Version not found",
                tenant_id=tenant_id,
                key=key,
                version_id=version_id,
            )

        content_found, content = self._read_content(
            obj_dir,
            version_id,
            metadata=metadata,
            tenant_id=tenant_id,
            key=key,
        )
        if not content_found or content is None:
            raise ObjectNotFoundError(
                message="Version content not found",
                tenant_id=tenant_id,
                key=key,
                version_id=version_id,
            )

        return StoredObject(metadata=metadata, body=content)

    @traced_storage_operation("verify_integrity")
    def verify_integrity(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> StoredObjectMetadata:
        """Stream-verify one published object without materializing its body."""
        metadata = self.head(tenant_id, key, version_id=version_id)
        obj_dir = self._get_object_dir(tenant_id, key)
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)
        content_found, _body = self._read_content(
            obj_dir,
            metadata.version_id,
            metadata=metadata,
            tenant_id=tenant_id,
            key=key,
            collect_body=False,
        )
        if not content_found:
            raise ObjectNotFoundError(
                message="Version content not found",
                tenant_id=tenant_id,
                key=key,
                version_id=metadata.version_id,
            )
        return metadata

    @traced_storage_operation("open_verified_stream")
    def open_verified_stream(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> tuple[StoredObjectMetadata, Iterator[bytes]]:
        """Return metadata plus a bounded iterator over one exact version."""
        metadata = self.head(tenant_id, key, version_id=version_id)
        obj_dir = self._get_object_dir(tenant_id, key)
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)
        chunks = self._iter_verified_content(
            obj_dir,
            metadata.version_id,
            metadata=metadata,
            tenant_id=tenant_id,
            key=key,
        )
        return metadata, chunks

    @traced_storage_operation("head")
    def head(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> StoredObjectMetadata:
        """Get object metadata without retrieving content."""
        obj_dir = self._get_object_dir(tenant_id, key)
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)
        if version_id is not None:
            version_id = _validate_version_id(version_id, tenant_id=tenant_id, key=key)

        if not obj_dir.exists():
            raise ObjectNotFoundError(
                message="Object not found",
                tenant_id=tenant_id,
                key=key,
            )

        if version_id is None:
            version_id = self._read_latest_pointer(
                obj_dir,
                tenant_id=tenant_id,
                key=key,
            )
            if version_id is None:
                raise ObjectNotFoundError(
                    message="Object has no versions",
                    tenant_id=tenant_id,
                    key=key,
                )

        metadata = self._read_metadata(
            obj_dir,
            version_id,
            tenant_id=tenant_id,
            key=key,
        )
        if metadata is None:
            raise ObjectNotFoundError(
                message="Version not found",
                tenant_id=tenant_id,
                key=key,
                version_id=version_id,
            )

        return metadata

    @traced_storage_operation("delete")
    def delete(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> None:
        """Delete an object or specific version."""
        obj_dir = self._get_object_dir(tenant_id, key)
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)
        if version_id is not None:
            version_id = _validate_version_id(version_id, tenant_id=tenant_id, key=key)

        if not obj_dir.exists():
            raise ObjectNotFoundError(
                message="Object not found",
                tenant_id=tenant_id,
                key=key,
            )

        if version_id is None:
            import shutil

            try:
                shutil.rmtree(obj_dir)
                logger.debug("Deleted all versions: tenant=%s key=%s", tenant_id, key)
            except OSError as e:
                raise StorageBackendError(
                    message=f"Failed to delete object directory: {e}",
                    tenant_id=tenant_id,
                    key=key,
                    cause=e,
                ) from e
        else:
            metadata = self._read_metadata(
                obj_dir,
                version_id,
                tenant_id=tenant_id,
                key=key,
            )
            if metadata is None:
                raise ObjectNotFoundError(
                    message="Version not found",
                    tenant_id=tenant_id,
                    key=key,
                    version_id=version_id,
                )

            meta_file = obj_dir / f"{version_id}{_METADATA_SUFFIX}"
            content_file = obj_dir / f"{version_id}{_CONTENT_SUFFIX}"
            current_latest = self._read_latest_pointer(
                obj_dir,
                tenant_id=tenant_id,
                key=key,
            )

            try:
                meta_file.unlink(missing_ok=True)
                content_file.unlink(missing_ok=True)
            except OSError as e:
                raise StorageBackendError(
                    message=f"Failed to delete version files: {e}",
                    tenant_id=tenant_id,
                    key=key,
                    cause=e,
                ) from e

            if current_latest == version_id:
                versions = self._list_version_ids(
                    obj_dir,
                    tenant_id=tenant_id,
                    key=key,
                )
                if versions:
                    self._write_latest_pointer(obj_dir, versions[0])
                else:
                    latest_file = obj_dir / _LATEST_POINTER
                    latest_file.unlink(missing_ok=True)

            logger.debug(
                "Deleted version: tenant=%s key=%s version=%s",
                tenant_id,
                key,
                version_id,
            )

    def _list_version_ids(
        self,
        obj_dir: Path,
        *,
        tenant_id: str,
        key: str,
    ) -> list[str]:
        """List all version IDs in an object directory, newest first."""
        versions: list[tuple[datetime, str]] = []

        try:
            for f in obj_dir.iterdir():
                if f.name.endswith(_METADATA_SUFFIX):
                    version_id = f.name[: -len(_METADATA_SUFFIX)]
                    if not _is_canonical_version_id(version_id):
                        continue
                    meta = self._read_metadata(
                        obj_dir,
                        version_id,
                        tenant_id=tenant_id,
                        key=key,
                    )
                    if meta:
                        versions.append((meta.created_at, version_id))
        except OSError:
            return []

        versions.sort(key=lambda x: x[0], reverse=True)
        return [v[1] for v in versions]

    @traced_storage_operation("list_versions")
    def list_versions(
        self,
        tenant_id: str,
        key: str,
    ) -> list[StoredObjectMetadata]:
        """List all versions of an object."""
        obj_dir = self._get_object_dir(tenant_id, key)
        self._ensure_resolved_within_base(obj_dir, tenant_id, key)

        if not obj_dir.exists():
            return []

        result: list[StoredObjectMetadata] = []
        version_ids = self._list_version_ids(
            obj_dir,
            tenant_id=tenant_id,
            key=key,
        )

        for version_id in version_ids:
            meta = self._read_metadata(
                obj_dir,
                version_id,
                tenant_id=tenant_id,
                key=key,
            )
            if meta:
                result.append(meta)

        return result
