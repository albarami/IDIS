"""IDIS Domain Models — Pydantic models for persistence entities.

Phase 3.1: Ingestion Gate storage primitives.
"""

from idis.models.deal_artifact import ArtifactType, DealArtifact
from idis.models.document import Document, DocumentType, ParseStatus
from idis.models.document_span import DocumentSpan, SpanLocator, SpanType

__all__ = [
    "ArtifactType",
    "DealArtifact",
    "Document",
    "DocumentSpan",
    "DocumentType",
    "ParseStatus",
    "SpanLocator",
    "SpanType",
]
