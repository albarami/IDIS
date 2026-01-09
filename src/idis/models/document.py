"""Document model — parsed representation metadata.

Aligned to Data Model §3.3 (documents table).

A Document represents the parsed state of a DealArtifact.
One artifact may produce one document (e.g., PDF) or multiple
documents (e.g., ZIP archive extraction).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentType(str, Enum):
    """Parsed document type.

    Aligned to Data Model §3.3 doc_type column.
    """

    PDF = "PDF"
    PPTX = "PPTX"
    XLSX = "XLSX"
    DOCX = "DOCX"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"


class ParseStatus(str, Enum):
    """Document parse status.

    Aligned to Data Model §3.3 parse_status column.
    """

    PENDING = "PENDING"
    PARSED = "PARSED"
    FAILED = "FAILED"


class Document(BaseModel):
    """Parsed document representation metadata.

    Represents the result of parsing a DealArtifact.
    Tracks parsing status and document-level metadata.

    Attributes:
        document_id: Unique identifier (UUID).
        tenant_id: Tenant scope (required for RLS).
        deal_id: Parent deal reference.
        artifact_id: Source artifact reference.
        doc_type: Document format (PDF, PPTX, etc.).
        parse_status: Current parsing state.
        metadata: Flexible document metadata (page count, dimensions, etc.).
        created_at: Record creation timestamp.
        updated_at: Record update timestamp.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        str_strip_whitespace=True,
    )

    document_id: Annotated[UUID, Field(description="Unique document identifier")]
    tenant_id: Annotated[UUID, Field(description="Tenant scope for RLS isolation")]
    deal_id: Annotated[UUID, Field(description="Parent deal reference")]
    artifact_id: Annotated[UUID, Field(description="Source artifact reference")]
    doc_type: Annotated[DocumentType, Field(description="Document format type")]
    parse_status: Annotated[
        ParseStatus,
        Field(default=ParseStatus.PENDING, description="Parsing state"),
    ]
    metadata: Annotated[
        dict[str, Any],
        Field(default_factory=dict, description="Document metadata (page count, etc.)"),
    ]
    created_at: Annotated[datetime, Field(description="Record creation timestamp")]
    updated_at: Annotated[datetime, Field(description="Record update timestamp")]
