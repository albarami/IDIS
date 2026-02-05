"""DocumentSpan model — extracted evidence spans with stable locators.

Aligned to Data Model §3.3 (document_spans table).

A DocumentSpan represents an addressable portion of a Document
that can serve as evidence for claims. Spans have stable locators
(JSON) that allow reproducible citation.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SpanType(StrEnum):
    """Type of document span.

    Aligned to Data Model §3.3 span_type column.
    """

    PAGE_TEXT = "PAGE_TEXT"
    PARAGRAPH = "PARAGRAPH"
    CELL = "CELL"
    TIMECODE = "TIMECODE"


class SpanLocator(BaseModel):
    """Stable locator for a document span.

    Provides reproducible addressing for different span types:
    - PAGE_TEXT: {page: int, bbox?: [x1, y1, x2, y2]}
    - PARAGRAPH: {page: int, paragraph_index: int}
    - CELL: {sheet: str, cell: str} (e.g., {sheet: "P&L", cell: "B12"})
    - TIMECODE: {t_ms: int} (milliseconds from start)

    The locator is stored as JSONB in Postgres for flexibility.
    """

    model_config = ConfigDict(
        extra="allow",
        frozen=False,
    )

    page: Annotated[int | None, Field(default=None, ge=1, description="Page number (1-indexed)")]
    bbox: Annotated[
        list[float] | None,
        Field(default=None, min_length=4, max_length=4, description="Bounding box [x1,y1,x2,y2]"),
    ]
    paragraph_index: Annotated[
        int | None,
        Field(default=None, ge=0, description="Paragraph index within page"),
    ]
    sheet: Annotated[str | None, Field(default=None, description="Spreadsheet sheet name")]
    cell: Annotated[str | None, Field(default=None, description="Cell reference (e.g., B12)")]
    t_ms: Annotated[
        int | None,
        Field(default=None, ge=0, description="Timecode in milliseconds"),
    ]


class DocumentSpan(BaseModel):
    """Extracted evidence span from a document.

    Represents an addressable portion of a parsed document
    that can be cited as evidence for claims.

    Attributes:
        span_id: Unique identifier (UUID).
        tenant_id: Tenant scope (required for RLS).
        document_id: Parent document reference.
        span_type: Type of span (PAGE_TEXT, CELL, etc.).
        locator: Stable JSON locator for reproducible citation.
        text_excerpt: Extracted text content (may be truncated).
        created_at: Record creation timestamp.
        updated_at: Record update timestamp.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        str_strip_whitespace=True,
    )

    span_id: Annotated[UUID, Field(description="Unique span identifier")]
    tenant_id: Annotated[UUID, Field(description="Tenant scope for RLS isolation")]
    document_id: Annotated[UUID, Field(description="Parent document reference")]
    span_type: Annotated[SpanType, Field(description="Span classification")]
    locator: Annotated[
        dict[str, Any],
        Field(description="Stable JSON locator for citation"),
    ]
    text_excerpt: Annotated[
        str | None,
        Field(default=None, description="Extracted text content"),
    ]
    created_at: Annotated[datetime, Field(description="Record creation timestamp")]
    updated_at: Annotated[datetime, Field(description="Record update timestamp")]
