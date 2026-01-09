"""DocumentArtifact model — document metadata aligned to OpenAPI DocumentArtifact schema.

Aligned to OpenAPI v6.3 DocumentArtifact schema.
Represents raw document metadata attached to a deal.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocType(str, Enum):
    """Document type classification.

    Aligned to OpenAPI DocumentArtifact.doc_type enum.
    """

    PITCH_DECK = "PITCH_DECK"
    FINANCIAL_MODEL = "FINANCIAL_MODEL"
    DATA_ROOM_FILE = "DATA_ROOM_FILE"
    TRANSCRIPT = "TRANSCRIPT"
    TERM_SHEET = "TERM_SHEET"
    OTHER = "OTHER"


class DocumentArtifact(BaseModel):
    """Document artifact metadata per OpenAPI DocumentArtifact schema.

    Represents document metadata attached to a deal, used for ingestion
    tracking and document management.

    Attributes:
        doc_id: Unique document identifier (UUID).
        tenant_id: Tenant scope (required for RLS).
        deal_id: Parent deal reference.
        doc_type: Document classification (PITCH_DECK, FINANCIAL_MODEL, etc.).
        title: Human-readable document title.
        source_system: Origin system (DocSend, Drive, etc.).
        version_id: Version identifier string.
        ingested_at: Timestamp when document was ingested.
        sha256: Optional content hash for integrity verification.
        uri: Optional storage or external URI.
        metadata: Flexible document metadata.
        created_at: Record creation timestamp.
        updated_at: Record update timestamp.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        str_strip_whitespace=True,
    )

    doc_id: Annotated[UUID, Field(description="Unique document identifier")]
    tenant_id: Annotated[UUID, Field(description="Tenant scope for RLS isolation")]
    deal_id: Annotated[UUID, Field(description="Parent deal reference")]
    doc_type: Annotated[DocType, Field(description="Document classification")]
    title: Annotated[str, Field(min_length=1, description="Document title")]
    source_system: Annotated[str, Field(min_length=1, description="Origin system")]
    version_id: Annotated[str, Field(min_length=1, description="Version identifier")]
    ingested_at: Annotated[datetime, Field(description="Ingestion timestamp")]
    sha256: Annotated[
        str | None,
        Field(default=None, min_length=64, max_length=64, description="SHA-256 hash"),
    ]
    uri: Annotated[
        str | None,
        Field(default=None, description="Storage or external URI"),
    ]
    metadata: Annotated[
        dict[str, Any],
        Field(default_factory=dict, description="Flexible document metadata"),
    ]
    created_at: Annotated[datetime, Field(description="Record creation timestamp")]
    updated_at: Annotated[datetime, Field(description="Record update timestamp")]
