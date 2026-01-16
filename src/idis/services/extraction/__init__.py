"""Extraction service module for IDIS.

Provides ExtractionService for claim extraction from document spans with:
- Fail-closed behavior when extractor not configured
- Extraction Confidence Gate enforcement
- No-Free-Facts validation on extracted claims
- Audit event emission for extraction lifecycle
"""

from idis.services.extraction.service import (
    ExtractionResult,
    ExtractionService,
    ExtractionServiceError,
    ExtractorNotConfiguredError,
    LowConfidenceExtractionError,
)

__all__ = [
    "ExtractionResult",
    "ExtractionService",
    "ExtractionServiceError",
    "ExtractorNotConfiguredError",
    "LowConfidenceExtractionError",
]
