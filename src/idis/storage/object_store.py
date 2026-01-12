"""IDIS Object Storage interface definition.

Provides the ObjectStore protocol/interface that all storage backends must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from idis.storage.models import StoredObject, StoredObjectMetadata


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
