"""IDIS Object Storage data models.

Provides typed dataclasses for object storage metadata and objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StoredObjectMetadata:
    """Metadata for a stored object.

    Attributes:
        tenant_id: UUID of the tenant owning this object.
        key: Logical key/path of the object within the tenant namespace.
        version_id: Unique identifier for this version of the object.
            For content-addressed storage, this is the SHA256 hash.
        sha256: SHA256 hash of the object content (hex string).
        size_bytes: Size of the object content in bytes.
        content_type: MIME type of the content (e.g., "application/json").
        created_at: Timestamp when this version was created.
    """

    tenant_id: str
    key: str
    version_id: str
    sha256: str
    size_bytes: int
    content_type: str | None
    created_at: datetime

    def to_dict(self) -> dict[str, str | int | None]:
        """Convert metadata to dictionary for JSON serialization."""
        return {
            "tenant_id": self.tenant_id,
            "key": self.key,
            "version_id": self.version_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, str | int | None]) -> StoredObjectMetadata:
        """Create metadata from dictionary."""
        created_at_raw = data.get("created_at")
        if isinstance(created_at_raw, str):
            created_at = datetime.fromisoformat(created_at_raw)
        elif isinstance(created_at_raw, datetime):
            created_at = created_at_raw
        else:
            created_at = datetime.utcnow()

        size_bytes_raw = data.get("size_bytes")
        size_bytes = int(size_bytes_raw) if size_bytes_raw is not None else 0

        content_type_raw = data.get("content_type")
        content_type = str(content_type_raw) if content_type_raw else None

        return cls(
            tenant_id=str(data["tenant_id"]),
            key=str(data["key"]),
            version_id=str(data["version_id"]),
            sha256=str(data["sha256"]),
            size_bytes=size_bytes,
            content_type=content_type,
            created_at=created_at,
        )


@dataclass(frozen=True)
class StoredObject:
    """A stored object with metadata and body content.

    Attributes:
        metadata: Object metadata (tenant, key, version, sha256, etc.).
        body: Object content as bytes.
    """

    metadata: StoredObjectMetadata
    body: bytes
