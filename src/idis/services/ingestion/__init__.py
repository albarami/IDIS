"""IDIS Ingestion Service — document ingestion and span generation.

Provides:
- IngestionService: Orchestrates document ingestion with tenant isolation
- SpanGenerator: Converts parser SpanDrafts to model-ready DocumentSpans
- IngestionResult: Structured result from ingestion operations
- IngestionError: Structured error for ingestion failures
"""

from idis.services.ingestion.service import (
    IngestionContext,
    IngestionError,
    IngestionErrorCode,
    IngestionResult,
    IngestionService,
)
from idis.services.ingestion.span_generator import SpanGenerator

__all__ = [
    "IngestionContext",
    "IngestionError",
    "IngestionErrorCode",
    "IngestionResult",
    "IngestionService",
    "SpanGenerator",
]
