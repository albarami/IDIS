"""Extraction service module for IDIS.

Provides ExtractionService for claim extraction from document spans with:
- Fail-closed behavior when extractor not configured
- Extraction Confidence Gate enforcement
- No-Free-Facts validation on extracted claims
- Audit event emission for extraction lifecycle
- Document-type-specific chunking (PDF, XLSX, DOCX, PPTX)
- Deterministic confidence scoring
"""

from idis.services.extraction.chunking import (
    ChunkingService,
    ExtractionChunk,
    UnsupportedDocumentTypeError,
)
from idis.services.extraction.confidence import (
    CONFIDENCE_ACCEPT_WITH_FLAG,
    CONFIDENCE_AUTO_ACCEPT,
    CONFIDENCE_HUMAN_REVIEW,
    ConfidenceScorer,
    SourceTier,
)
from idis.services.extraction.service import (
    ExtractionResult,
    ExtractionService,
    ExtractionServiceError,
    ExtractorNotConfiguredError,
    LowConfidenceExtractionError,
)

__all__ = [
    "CONFIDENCE_ACCEPT_WITH_FLAG",
    "CONFIDENCE_AUTO_ACCEPT",
    "CONFIDENCE_HUMAN_REVIEW",
    "ChunkingService",
    "ConfidenceScorer",
    "ExtractionChunk",
    "ExtractionResult",
    "ExtractionService",
    "ExtractionServiceError",
    "ExtractorNotConfiguredError",
    "LowConfidenceExtractionError",
    "SourceTier",
    "UnsupportedDocumentTypeError",
]
