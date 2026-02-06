"""Chunking service module for grouping document spans into extraction-ready chunks.

Provides document-type-specific chunkers that group parsed spans into
chunks sized for LLM context windows, preserving span IDs for provenance.
"""

from idis.services.extraction.chunking.base import (
    Chunker,
    ExtractionChunk,
    UnsupportedDocumentTypeError,
    estimate_tokens,
    locator_sort_key,
)
from idis.services.extraction.chunking.service import ChunkingService

__all__ = [
    "Chunker",
    "ChunkingService",
    "ExtractionChunk",
    "UnsupportedDocumentTypeError",
    "estimate_tokens",
    "locator_sort_key",
]
