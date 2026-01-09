"""IDIS Object Storage error types.

Provides typed exceptions for storage operations following IDIS error patterns.
All errors are fail-closed: operations that cannot complete safely raise errors.
"""

from __future__ import annotations


class ObjectStorageError(Exception):
    """Base exception for object storage operations.

    Raised when a storage operation fails. Subclasses provide more specific
    error types for different failure modes.

    Attributes:
        message: Human-readable error message.
        tenant_id: Tenant ID associated with the operation (if applicable).
        key: Object key associated with the operation (if applicable).
    """

    def __init__(
        self,
        message: str,
        *,
        tenant_id: str | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.tenant_id = tenant_id
        self.key = key

    def __str__(self) -> str:
        parts = [self.message]
        if self.tenant_id:
            parts.append(f"tenant_id={self.tenant_id}")
        if self.key:
            parts.append(f"key={self.key}")
        return " ".join(parts)


class ObjectNotFoundError(ObjectStorageError):
    """Raised when an object is not found in storage.

    This error indicates the object does not exist or the specified version
    does not exist. May also be raised for tenant isolation violations
    (object exists but belongs to different tenant).
    """

    def __init__(
        self,
        message: str = "Object not found",
        *,
        tenant_id: str | None = None,
        key: str | None = None,
        version_id: str | None = None,
    ) -> None:
        super().__init__(message, tenant_id=tenant_id, key=key)
        self.version_id = version_id


class PathTraversalError(ObjectStorageError):
    """Raised when an object key contains path traversal sequences.

    This is a security error indicating an attempt to escape the storage
    sandbox via keys like "../", absolute paths, or other traversal patterns.
    """

    def __init__(
        self,
        message: str = "Invalid key: path traversal detected",
        *,
        tenant_id: str | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(message, tenant_id=tenant_id, key=key)


class StorageBackendError(ObjectStorageError):
    """Raised when the storage backend cannot complete an operation.

    This error indicates the backend itself failed (e.g., disk full,
    permission denied, I/O error) rather than a logical error like
    object not found.
    """

    def __init__(
        self,
        message: str = "Storage backend error",
        *,
        tenant_id: str | None = None,
        key: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, tenant_id=tenant_id, key=key)
        self.cause = cause
