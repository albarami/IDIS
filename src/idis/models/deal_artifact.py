"""DealArtifact model — raw file/connector object metadata.

Aligned to Data Model §3.2 (deal_artifacts table).
Maps to OpenAPI "DocumentArtifact" concept.

A DealArtifact represents the raw, unprocessed artifact ingested from
a connector (DocSend, Drive, Dropbox, SharePoint) or direct upload.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ArtifactType(str, Enum):
    """Type of artifact ingested.

    Aligned to Data Model §3.2 artifact_type column.
    """

    PITCH_DECK = "PITCH_DECK"
    FIN_MODEL = "FIN_MODEL"
    DATA_ROOM = "DATA_ROOM"
    TRANSCRIPT = "TRANSCRIPT"
    NOTE = "NOTE"


class ConnectorType(str, Enum):
    """Source connector type.

    Aligned to Data Model §3.2 connector_type column.
    """

    DOCSEND = "DocSend"
    DRIVE = "Drive"
    DROPBOX = "Dropbox"
    SHAREPOINT = "SharePoint"
    UPLOAD = "Upload"


class DealArtifact(BaseModel):
    """Raw artifact metadata for a deal.

    Represents the original file or connector object before parsing.
    Immutable after creation (append-only pattern).

    Attributes:
        artifact_id: Unique identifier (UUID).
        tenant_id: Tenant scope (required for RLS).
        deal_id: Parent deal reference.
        artifact_type: Classification (PITCH_DECK, FIN_MODEL, etc.).
        storage_uri: Object storage URI (s3://... or blob://...).
        connector_type: Source connector (DocSend, Drive, etc.) or None for uploads.
        connector_ref: Provider-specific file ID or link.
        sha256: Content hash for integrity verification.
        version_label: Optional version string (e.g., "v2", "final").
        ingested_at: Timestamp when artifact was ingested.
        created_at: Record creation timestamp.
        updated_at: Record update timestamp.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        str_strip_whitespace=True,
    )

    artifact_id: Annotated[UUID, Field(description="Unique artifact identifier")]
    tenant_id: Annotated[UUID, Field(description="Tenant scope for RLS isolation")]
    deal_id: Annotated[UUID, Field(description="Parent deal reference")]
    artifact_type: Annotated[ArtifactType, Field(description="Artifact classification")]
    storage_uri: Annotated[str, Field(min_length=1, description="Object storage URI")]
    connector_type: Annotated[
        ConnectorType | None,
        Field(default=None, description="Source connector type"),
    ]
    connector_ref: Annotated[
        str | None,
        Field(default=None, description="Provider file ID or link"),
    ]
    sha256: Annotated[str, Field(min_length=64, max_length=64, description="SHA-256 content hash")]
    version_label: Annotated[
        str | None,
        Field(default=None, description="Optional version string"),
    ]
    ingested_at: Annotated[datetime, Field(description="Ingestion timestamp")]
    created_at: Annotated[datetime, Field(description="Record creation timestamp")]
    updated_at: Annotated[datetime, Field(description="Record update timestamp")]
