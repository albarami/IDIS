"""IDIS Object Storage interface definition.

Provides the ObjectStore protocol/interface that all storage backends must implement.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Protocol, TypeAlias

from idis.storage.models import StoredObject, StoredObjectMetadata


class BinaryReader(Protocol):
    """Minimal bounded-read interface accepted by streaming object stores."""

    def read(self, size: int = -1) -> bytes:
        """Read at most ``size`` bytes from the source."""
        ...


StreamSource: TypeAlias = BinaryReader | str | os.PathLike[str]


class ObjectStore(ABC):
    """Abstract base class for object storage backends.

    All implementations must provide tenant-isolated storage with:
    - SHA256 content hashing
    - Version tracking (content-addressed or explicit)
    - Path traversal protection
    - Safe attribute emission for observability

    Implementations:
    - FilesystemObjectStore: Local filesystem (dev/test)
    - S3ObjectStore: AWS S3 compatible (production) - planned
    """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Return the backend identifier for observability.

        Returns:
            Backend name string (e.g., "filesystem", "s3").
        """
        ...

    @abstractmethod
    def put(
        self,
        tenant_id: str,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> StoredObjectMetadata:
        """Store an object.

        Args:
            tenant_id: UUID of the tenant.
            key: Logical key/path for the object.
            data: Object content as bytes.
            content_type: Optional MIME type of the content.

        Returns:
            Metadata for the stored object including version_id and sha256.

        Raises:
            PathTraversalError: If key contains traversal sequences.
            StorageBackendError: If the backend cannot complete the write.
        """
        ...

    @abstractmethod
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
        """Store an object from a bounded reader or filesystem path.

        Implementations must consume readers with positive, bounded ``read``
        sizes, validate any supplied size/hash expectations before publishing,
        and leave no partial object version on failure. Repeating the operation
        for the same tenant/key/content hash is idempotent and returns the
        existing version metadata.

        Args:
            tenant_id: UUID of the tenant.
            key: Logical key/path for the object.
            source: Binary reader or path opened by the backend without a
                whole-file read.
            content_type: Optional MIME type of the content.
            expected_sha256: Optional lowercase or uppercase SHA-256 hex digest.
            expected_size_bytes: Optional exact source size.

        Returns:
            Metadata for the published or idempotently reused object version.

        Raises:
            PathTraversalError: If key contains traversal sequences.
            StorageBackendError: If reading, validation, or publication fails.
        """
        ...

    @abstractmethod
    def get(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> StoredObject:
        """Retrieve an object.

        Args:
            tenant_id: UUID of the tenant.
            key: Logical key/path of the object.
            version_id: Optional specific version to retrieve.
                If None, returns the latest version.

        Returns:
            StoredObject with metadata and body content.

        Raises:
            ObjectNotFoundError: If object or version does not exist.
            PathTraversalError: If key contains traversal sequences.
            StorageBackendError: If the backend cannot complete the read.
        """
        ...

    @abstractmethod
    def verify_integrity(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> StoredObjectMetadata:
        """Re-hash durable bytes in bounded chunks without returning their body.

        The read must verify exact size and SHA-256 against persisted metadata and
        fail if file/object identity changes during the verification window.
        """
        ...

    @abstractmethod
    def open_verified_stream(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> tuple[StoredObjectMetadata, Iterator[bytes]]:
        """Open a bounded, identity-bound stream for one published version.

        The iterator must use positive bounded reads, re-hash bytes while they
        are yielded, and fail if size, digest, or file/object identity changes.
        It must never materialize the complete object body.
        """
        ...

    @abstractmethod
    def head(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> StoredObjectMetadata:
        """Get object metadata without retrieving content.

        Args:
            tenant_id: UUID of the tenant.
            key: Logical key/path of the object.
            version_id: Optional specific version to query.
                If None, returns metadata for the latest version.

        Returns:
            StoredObjectMetadata for the object.

        Raises:
            ObjectNotFoundError: If object or version does not exist.
            PathTraversalError: If key contains traversal sequences.
            StorageBackendError: If the backend cannot complete the operation.
        """
        ...

    @abstractmethod
    def delete(
        self,
        tenant_id: str,
        key: str,
        *,
        version_id: str | None = None,
    ) -> None:
        """Delete an object or specific version.

        Args:
            tenant_id: UUID of the tenant.
            key: Logical key/path of the object.
            version_id: Optional specific version to delete.
                If None, deletes all versions (the entire object).

        Raises:
            ObjectNotFoundError: If object or version does not exist.
            PathTraversalError: If key contains traversal sequences.
            StorageBackendError: If the backend cannot complete the deletion.
        """
        ...

    @abstractmethod
    def list_versions(
        self,
        tenant_id: str,
        key: str,
    ) -> list[StoredObjectMetadata]:
        """List all versions of an object.

        Args:
            tenant_id: UUID of the tenant.
            key: Logical key/path of the object.

        Returns:
            List of StoredObjectMetadata for all versions, ordered by
            created_at descending (newest first). Empty list if object
            does not exist or backend does not support versioning.

        Raises:
            PathTraversalError: If key contains traversal sequences.
            StorageBackendError: If the backend cannot complete the listing.
        """
        ...
