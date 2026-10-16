"""Extraction service module for IDIS.

Provides ExtractionService for claim extraction from document spans with:
- Fail-closed behavior when extractor not configured
- Extraction Confidence Gate enforcement
- No-Free-Facts validation on extracted claims
- Audit event emission for extraction lifecycle
- Document-type-specific chunking (PDF, XLSX, DOCX, PPTX)
- Deterministic confidence scoring
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
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


def __getattr__(name: str) -> Any:
    """Resolve service exports without loading claims for pure extraction imports."""
    if name not in {
        "ExtractionResult",
        "ExtractionService",
        "ExtractionServiceError",
        "ExtractorNotConfiguredError",
        "LowConfidenceExtractionError",
    }:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("idis.services.extraction.service"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List public exports without eagerly importing the service."""
    return sorted(set(globals()) | set(__all__))
