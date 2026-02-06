"""ChunkingService — routes documents to the correct chunker by doc_type.

Fail-closed on unknown doc_type (no silent fallback).
"""

from __future__ import annotations

import logging
from typing import Any

from idis.services.extraction.chunking.base import (
    DEFAULT_MAX_TOKENS,
    Chunker,
    ExtractionChunk,
    UnsupportedDocumentTypeError,
)
from idis.services.extraction.chunking.docx_chunker import DocxChunker
from idis.services.extraction.chunking.pdf_chunker import PdfChunker
from idis.services.extraction.chunking.pptx_chunker import PptxChunker
from idis.services.extraction.chunking.xlsx_chunker import XlsxChunker

logger = logging.getLogger(__name__)


class ChunkingService:
    """Routes documents to the correct chunker by doc_type.

    Supported types: PDF, XLSX, DOCX, PPTX.
    Unknown doc_type raises UnsupportedDocumentTypeError (fail-closed).
    """

    def __init__(self, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        """Initialize with configurable max token limit.

        Args:
            max_tokens: Maximum tokens per chunk, passed to all chunkers.
        """
        self._chunkers: dict[str, Chunker] = {
            "PDF": PdfChunker(max_tokens=max_tokens),
            "XLSX": XlsxChunker(max_tokens=max_tokens),
            "DOCX": DocxChunker(max_tokens=max_tokens),
            "PPTX": PptxChunker(max_tokens=max_tokens),
        }

    def chunk_spans(
        self,
        spans: list[dict[str, Any]],
        *,
        document_id: str,
        doc_type: str,
    ) -> list[ExtractionChunk]:
        """Route to appropriate chunker by doc_type.

        Args:
            spans: List of span dicts with text_excerpt and locator.
            document_id: Parent document UUID.
            doc_type: Document type (PDF, XLSX, DOCX, PPTX).

        Returns:
            List of ExtractionChunk objects in deterministic order.

        Raises:
            UnsupportedDocumentTypeError: If doc_type is not supported.
        """
        normalized_type = doc_type.upper().strip()
        chunker = self._chunkers.get(normalized_type)

        if chunker is None:
            raise UnsupportedDocumentTypeError(doc_type)

        return chunker.chunk(spans, document_id=document_id)
