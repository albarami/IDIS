"""Re-export Extractor protocol from service.py (single source of truth).

The Extractor protocol is defined in service.py to avoid circular imports.
This module re-exports it for convenience.
"""

from idis.services.extraction.service import ExtractedClaimDraft, Extractor

__all__ = [
    "ExtractedClaimDraft",
    "Extractor",
]
