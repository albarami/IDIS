"""PDF chunker — groups PAGE_TEXT spans by page with token-aware splitting.

Chunking strategy per spec §3.1:
- Group PAGE_TEXT spans by page number into one chunk per page.
- If a page exceeds max_tokens, split at line boundaries with overlap.
- Spans with locator containing 'table' key get their own chunk per table.
- Locator: {page: N}.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from idis.services.extraction.chunking.base import (
    DEFAULT_MAX_TOKENS,
    OVERLAP_TOKENS,
    ExtractionChunk,
    deterministic_chunk_id,
    estimate_tokens,
    locator_sort_key,
)

logger = logging.getLogger(__name__)


class PdfChunker:
    """Groups PDF spans by page with token-aware splitting."""

    def __init__(self, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        """Initialize with configurable max token limit.

        Args:
            max_tokens: Maximum tokens per chunk (default 500).
        """
        self._max_tokens = max_tokens

    def chunk(
        self,
        spans: list[dict[str, Any]],
        *,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Group PDF spans into extraction-ready chunks.

        Args:
            spans: List of span dicts from PDF parser.
            document_id: Parent document UUID.

        Returns:
            List of ExtractionChunk objects sorted by locator.
        """
        valid_spans = _filter_valid_spans(spans)
        if not valid_spans:
            return []

        table_spans: list[dict[str, Any]] = []
        page_spans: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)

        for span in valid_spans:
            locator = span.get("locator", {})
            if "table" in locator or "table_id" in locator:
                table_spans.append(span)
            else:
                page_num = locator.get("page", 0)
                page_spans[page_num].append(span)

        chunks: list[ExtractionChunk] = []

        for page_num in sorted(page_spans.keys()):
            page_group = sorted(
                page_spans[page_num],
                key=lambda s: locator_sort_key(s.get("locator", {})),
            )
            chunks.extend(self._chunk_page(page_group, page_num=page_num, document_id=document_id))

        for table_span in sorted(table_spans, key=lambda s: locator_sort_key(s.get("locator", {}))):
            chunk = _make_chunk(
                spans_group=[table_span],
                locator={"page": table_span.get("locator", {}).get("page", 0), "table": True},
                document_id=document_id,
                doc_type="PDF",
            )
            chunks.append(chunk)

        return chunks

    def _chunk_page(
        self,
        page_group: list[dict[str, Any]],
        *,
        page_num: int,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Chunk a single page's spans, splitting if over token limit.

        Args:
            page_group: All spans on this page (sorted).
            page_num: The page number.
            document_id: Parent document UUID.

        Returns:
            One or more chunks for this page.
        """
        combined_text = "\n".join(s.get("text_excerpt", "") for s in page_group)
        total_tokens = estimate_tokens(combined_text)

        if total_tokens <= self._max_tokens:
            return [
                _make_chunk(
                    spans_group=page_group,
                    locator={"page": page_num},
                    document_id=document_id,
                    doc_type="PDF",
                )
            ]

        return self._split_page_spans(page_group, page_num=page_num, document_id=document_id)

    def _split_page_spans(
        self,
        page_group: list[dict[str, Any]],
        *,
        page_num: int,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Split oversized page into multiple chunks with overlap.

        Args:
            page_group: All spans on this page.
            page_num: The page number.
            document_id: Parent document UUID.

        Returns:
            Multiple chunks with OVERLAP_TOKENS overlap.
        """
        chunks: list[ExtractionChunk] = []
        current_spans: list[dict[str, Any]] = []
        current_tokens = 0
        part_idx = 0

        for span in page_group:
            span_tokens = estimate_tokens(span.get("text_excerpt", ""))

            if span_tokens > self._max_tokens and not current_spans:
                sub_chunks = _hard_split_span(
                    span,
                    self._max_tokens,
                    page_num,
                    part_idx,
                    document_id,
                )
                chunks.extend(sub_chunks)
                part_idx += len(sub_chunks)
                continue

            if current_spans and current_tokens + span_tokens > self._max_tokens:
                chunks.append(
                    _make_chunk(
                        spans_group=current_spans,
                        locator={"page": page_num, "part": part_idx},
                        document_id=document_id,
                        doc_type="PDF",
                    )
                )
                part_idx += 1

                overlap_spans: list[dict[str, Any]] = []
                overlap_tokens = 0
                for s in reversed(current_spans):
                    s_tokens = estimate_tokens(s.get("text_excerpt", ""))
                    if overlap_tokens + s_tokens > OVERLAP_TOKENS:
                        break
                    overlap_spans.insert(0, s)
                    overlap_tokens += s_tokens

                current_spans = list(overlap_spans)
                current_tokens = overlap_tokens

            current_spans.append(span)
            current_tokens += span_tokens

        if current_spans:
            chunks.append(
                _make_chunk(
                    spans_group=current_spans,
                    locator={"page": page_num, "part": part_idx},
                    document_id=document_id,
                    doc_type="PDF",
                )
            )

        return chunks


def _filter_valid_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter spans that have text_excerpt, logging warnings for skipped."""
    valid: list[dict[str, Any]] = []
    for span in spans:
        text = span.get("text_excerpt")
        if not text or not text.strip():
            logger.warning(
                "Skipping span %s: no text_excerpt (fail-closed, not crash)",
                span.get("span_id", "unknown"),
            )
            continue
        valid.append(span)
    return valid


def _hard_split_span(
    span: dict[str, Any],
    max_tokens: int,
    page_num: int,
    part_start: int,
    document_id: str,
) -> list[ExtractionChunk]:
    """Hard-split a single oversized span by words.

    Args:
        span: The oversized span dict.
        max_tokens: Maximum tokens per chunk.
        page_num: Page number for locator.
        part_start: Starting part index.
        document_id: Parent document UUID.

    Returns:
        List of chunks, each within max_tokens.
    """
    text = span.get("text_excerpt", "")
    span_id = span.get("span_id", "")
    words = text.split()
    max_words = int(max_tokens / 1.3)
    chunks: list[ExtractionChunk] = []
    idx = 0
    part = part_start

    while idx < len(words):
        segment_words = words[idx : idx + max_words]
        segment_text = " ".join(segment_words)
        locator = {"page": page_num, "part": part}
        span_ids = (span_id,)
        chunks.append(
            ExtractionChunk(
                chunk_id=deterministic_chunk_id(document_id, locator, span_ids),
                document_id=document_id,
                span_ids=span_ids,
                content=segment_text,
                locator=locator_sort_key(locator),
                doc_type="PDF",
                token_estimate=estimate_tokens(segment_text),
            )
        )
        idx += max_words
        part += 1

    return chunks


def _make_chunk(
    *,
    spans_group: list[dict[str, Any]],
    locator: dict[str, Any],
    document_id: str,
    doc_type: str,
) -> ExtractionChunk:
    """Create an ExtractionChunk from a group of spans.

    Args:
        spans_group: Spans in this chunk (must be non-empty).
        locator: Chunk-level locator dict.
        document_id: Parent document UUID.
        doc_type: Document type string.

    Returns:
        ExtractionChunk with combined content and provenance.
    """
    content = "\n".join(s.get("text_excerpt", "") for s in spans_group)
    span_ids = tuple(s.get("span_id", "") for s in spans_group)

    return ExtractionChunk(
        chunk_id=deterministic_chunk_id(document_id, locator, span_ids),
        document_id=document_id,
        span_ids=span_ids,
        content=content,
        locator=locator_sort_key(locator),
        doc_type=doc_type,
        token_estimate=estimate_tokens(content),
    )
