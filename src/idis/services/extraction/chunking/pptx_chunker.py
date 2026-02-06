"""PPTX chunker — groups spans by slide.

Chunking strategy per spec §3.4:
- Group all spans on the same slide into one chunk.
- If a slide exceeds max_tokens, split by shape boundaries.
- Locator: {slide: N}.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from idis.services.extraction.chunking.base import (
    DEFAULT_MAX_TOKENS,
    ExtractionChunk,
    deterministic_chunk_id,
    estimate_tokens,
    locator_sort_key,
)

logger = logging.getLogger(__name__)


class PptxChunker:
    """Groups PPTX spans by slide."""

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
        """Group PPTX spans into extraction-ready chunks by slide.

        Args:
            spans: List of span dicts from PPTX parser.
            document_id: Parent document UUID.

        Returns:
            List of ExtractionChunk objects sorted by slide number.
        """
        valid_spans = _filter_valid_spans(spans)
        if not valid_spans:
            return []

        slide_spans: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
        for span in valid_spans:
            locator = span.get("locator", {})
            slide_num = locator.get("slide", 0)
            slide_spans[slide_num].append(span)

        chunks: list[ExtractionChunk] = []
        for slide_num in sorted(slide_spans.keys()):
            group = sorted(
                slide_spans[slide_num],
                key=lambda s: locator_sort_key(s.get("locator", {})),
            )
            chunks.extend(self._chunk_slide(group, slide_num=slide_num, document_id=document_id))

        return chunks

    def _chunk_slide(
        self,
        slide_group: list[dict[str, Any]],
        *,
        slide_num: int,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Chunk a single slide's spans, splitting if over token limit.

        Args:
            slide_group: All spans on this slide (sorted).
            slide_num: The slide number.
            document_id: Parent document UUID.

        Returns:
            One or more chunks for this slide.
        """
        combined_text = "\n".join(s.get("text_excerpt", "") for s in slide_group)
        total_tokens = estimate_tokens(combined_text)

        if total_tokens <= self._max_tokens:
            return [
                _make_chunk(
                    spans_group=slide_group,
                    locator={"slide": slide_num},
                    document_id=document_id,
                )
            ]

        chunks: list[ExtractionChunk] = []
        current_spans: list[dict[str, Any]] = []
        current_tokens = 0
        part_idx = 0

        for span in slide_group:
            span_tokens = estimate_tokens(span.get("text_excerpt", ""))

            if span_tokens > self._max_tokens and not current_spans:
                sub_chunks = _hard_split_span(
                    span,
                    self._max_tokens,
                    slide_num,
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
                        locator={"slide": slide_num, "part": part_idx},
                        document_id=document_id,
                    )
                )
                part_idx += 1
                current_spans = []
                current_tokens = 0

            current_spans.append(span)
            current_tokens += span_tokens

        if current_spans:
            locator: dict[str, Any] = {"slide": slide_num}
            if part_idx > 0:
                locator["part"] = part_idx
            chunks.append(
                _make_chunk(
                    spans_group=current_spans,
                    locator=locator,
                    document_id=document_id,
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
    slide_num: int,
    part_start: int,
    document_id: str,
) -> list[ExtractionChunk]:
    """Hard-split a single oversized span by words.

    Args:
        span: The oversized span dict.
        max_tokens: Maximum tokens per chunk.
        slide_num: Slide number for locator.
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
        locator = {"slide": slide_num, "part": part}
        span_ids = (span_id,)
        chunks.append(
            ExtractionChunk(
                chunk_id=deterministic_chunk_id(
                    document_id,
                    locator,
                    span_ids,
                ),
                document_id=document_id,
                span_ids=span_ids,
                content=segment_text,
                locator=locator_sort_key(locator),
                doc_type="PPTX",
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
) -> ExtractionChunk:
    """Create an ExtractionChunk from a group of spans.

    Args:
        spans_group: Spans in this chunk (must be non-empty).
        locator: Chunk-level locator dict.
        document_id: Parent document UUID.

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
        doc_type="PPTX",
        token_estimate=estimate_tokens(content),
    )
