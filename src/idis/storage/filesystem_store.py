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
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from idis.storage.errors import (
    ObjectNotFoundError,
    PathTraversalError,
    StorageBackendError,
)
from idis.storage.models import StoredObject, StoredObjectMetadata
from idis.storage.object_store import ObjectStore
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


def _validate_tenant_id(tenant_id: str) -> bool:
    """Validate that tenant_id is a valid UUID format."""
    return bool(_UUID_PATTERN.match(tenant_id))


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
        """Ensure a path resolves within the base directory (defense in depth)."""
        resolved = path.resolve()
        try:
            resolved.relative_to(self._base_dir.resolve())
        except ValueError as e:
            raise PathTraversalError(
                message="Path resolves outside storage base directory",
                tenant_id=tenant_id,
                key=key,
            ) from e
        return resolved

    def _read_latest_pointer(self, obj_dir: Path) -> str | None:
        """Read the latest version pointer file."""
        latest_file = obj_dir / _LATEST_POINTER
        if not latest_file.exists():
            return None
        try:
            return latest_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None

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

    def _read_metadata(self, obj_dir: Path, version_id: str) -> StoredObjectMetadata | None:
        """Read metadata for a specific version."""
        meta_file = obj_dir / f"{version_id}{_METADATA_SUFFIX}"
        if not meta_file.exists():
            return None
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            return StoredObjectMetadata.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to read metadata %s: %s", meta_file, e)
            return None

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

    def _read_content(self, obj_dir: Path, version_id: str) -> bytes | None:
        """Read content for a specific version."""
        content_file = obj_dir / f"{version_id}{_CONTENT_SUFFIX}"
        if not content_file.exists():
            return None
        try:
            return content_file.read_bytes()
        except OSError as e:
            logger.warning("Failed to read content %s: %s", content_file, e)
            return None

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

        if not obj_dir.exists():
            raise ObjectNotFoundError(
                message="Object not found",
                tenant_id=tenant_id,
                key=key,
            )

        if version_id is None:
            version_id = self._read_latest_pointer(obj_dir)
            if version_id is None:
                raise ObjectNotFoundError(
                    message="Object has no versions",
                    tenant_id=tenant_id,
                    key=key,
                )

        metadata = self._read_metadata(obj_dir, version_id)
        if metadata is None:
            raise ObjectNotFoundError(
                message="Version not found",
                tenant_id=tenant_id,
                key=key,
                version_id=version_id,
            )

        content = self._read_content(obj_dir, version_id)
        if content is None:
            raise ObjectNotFoundError(
                message="Version content not found",
                tenant_id=tenant_id,
                key=key,
                version_id=version_id,
            )

        return StoredObject(metadata=metadata, body=content)

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

        if not obj_dir.exists():
            raise ObjectNotFoundError(
                message="Object not found",
                tenant_id=tenant_id,
                key=key,
            )

        if version_id is None:
            version_id = self._read_latest_pointer(obj_dir)
            if version_id is None:
                raise ObjectNotFoundError(
                    message="Object has no versions",
                    tenant_id=tenant_id,
                    key=key,
                )

        metadata = self._read_metadata(obj_dir, version_id)
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
            meta_file = obj_dir / f"{version_id}{_METADATA_SUFFIX}"
            content_file = obj_dir / f"{version_id}{_CONTENT_SUFFIX}"

            if not meta_file.exists() and not content_file.exists():
                raise ObjectNotFoundError(
                    message="Version not found",
                    tenant_id=tenant_id,
                    key=key,
                    version_id=version_id,
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

            current_latest = self._read_latest_pointer(obj_dir)
            if current_latest == version_id:
                versions = self._list_version_ids(obj_dir)
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

    def _list_version_ids(self, obj_dir: Path) -> list[str]:
        """List all version IDs in an object directory, newest first."""
        versions: list[tuple[datetime, str]] = []

        try:
            for f in obj_dir.iterdir():
                if f.name.endswith(_METADATA_SUFFIX):
                    version_id = f.name[: -len(_METADATA_SUFFIX)]
                    meta = self._read_metadata(obj_dir, version_id)
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
        version_ids = self._list_version_ids(obj_dir)

        for version_id in version_ids:
            meta = self._read_metadata(obj_dir, version_id)
            if meta:
                result.append(meta)

        return result
