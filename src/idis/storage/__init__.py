"""IDIS Object Storage Abstraction.

Provides tenant-isolated object storage with SHA256 tracking,
versioning primitives, and observability hooks.

Backends:
- FilesystemObjectStore: Local filesystem (dev/test)
- S3ObjectStore: AWS S3 compatible (production) - planned

Environment Variables:
    IDIS_OBJECT_STORE_BACKEND: "filesystem" or "s3" (default: "filesystem")
    IDIS_OBJECT_STORE_BASE_DIR: Base directory for filesystem backend
        (default: OS temp dir / idis_objects)
"""

from idis.storage.errors import (
    ObjectNotFoundError,
    ObjectStorageError,
    PathTraversalError,
)
from idis.storage.models import StoredObject, StoredObjectMetadata
from idis.storage.object_store import ObjectStore

__all__ = [
    "ObjectStore",
    "StoredObject",
    "StoredObjectMetadata",
    "ObjectStorageError",
    "ObjectNotFoundError",
    "PathTraversalError",
]
