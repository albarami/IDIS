"""Base types for chunking — shared across all document-type chunkers.

Provides:
- ExtractionChunk: Immutable chunk ready for LLM extraction
- Chunker: Protocol for document-type-specific chunkers
- UnsupportedDocumentTypeError: Fail-closed error for unknown doc types
- estimate_tokens: Deterministic token estimation (no tokenizer dependency)
- locator_sort_key: Canonical JSON sort key for deterministic ordering
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 500
OVERLAP_TOKENS = 50
TOKEN_MULTIPLIER = 1.3
CHUNK_ID_NAMESPACE = uuid.UUID("a3f1b2c4-5d6e-7f80-9a1b-2c3d4e5f6a7b")


class UnsupportedDocumentTypeError(Exception):
    """Raised when doc_type is not supported by any chunker (fail-closed)."""

    def __init__(self, doc_type: str) -> None:
        self.doc_type = doc_type
        super().__init__(
            f"Unsupported document type: '{doc_type}'. No chunker available (fail-closed)."
        )


@dataclass(frozen=True)
class ExtractionChunk:
    """Immutable chunk of document content ready for LLM extraction.

    Attributes:
        chunk_id: Unique identifier (UUID).
        document_id: Parent document reference.
        span_ids: Source span UUIDs (provenance chain).
        content: Combined text for extraction.
        locator: Chunk-level locator (e.g., {page: 3} or {sheet: "P&L"}).
        doc_type: PDF, XLSX, DOCX, PPTX.
        token_estimate: Approximate token count.
    """

    chunk_id: str
    document_id: str
    span_ids: tuple[str, ...]
    content: str
    locator: str
    doc_type: str
    token_estimate: int


class Chunker(Protocol):
    """Protocol for document-type-specific chunkers."""

    def chunk(
        self,
        spans: list[dict[str, Any]],
        *,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Group spans into extraction-ready chunks.

        Args:
            spans: List of span dicts with text_excerpt and locator.
            document_id: Parent document UUID.

        Returns:
            List of ExtractionChunk objects in deterministic order.
        """
        ...


def estimate_tokens(text: str) -> int:
    """Estimate token count from text without tokenizer dependency.

    Uses word count * 1.3 as a conservative approximation.

    Args:
        text: Input text string.

    Returns:
        Estimated token count (always >= 0).
    """
    if not text:
        return 0
    return int(len(text.split()) * TOKEN_MULTIPLIER)


def deterministic_chunk_id(
    document_id: str,
    locator: dict[str, Any],
    span_ids: tuple[str, ...],
) -> str:
    """Derive a deterministic chunk ID from stable inputs.

    Uses UUIDv5(namespace, document_id + canonical_locator + sorted_span_ids)
    so identical inputs always produce the same chunk_id.

    Args:
        document_id: Parent document UUID.
        locator: Chunk-level locator dict.
        span_ids: Source span UUIDs.

    Returns:
        Deterministic UUID string.
    """
    canonical_locator = json.dumps(locator, sort_keys=True, separators=(",", ":"))
    sorted_spans = ",".join(sorted(span_ids))
    name = f"{document_id}|{canonical_locator}|{sorted_spans}"
    return str(uuid.uuid5(CHUNK_ID_NAMESPACE, name))


def locator_sort_key(locator: dict[str, Any]) -> str:
    """Generate a canonical JSON string for deterministic sort ordering.

    Args:
        locator: Locator dictionary.

    Returns:
        Canonical JSON string (sorted keys, no whitespace).
    """
    return json.dumps(locator, sort_keys=True, separators=(",", ":"))
