"""XLSX chunker — groups CELL spans by sheet with token-aware splitting.

Chunking strategy per spec §3.2:
- Group CELL spans by sheet name into one chunk per sheet.
- If sheet exceeds max_tokens, split by row ranges.
- Locator: {sheet: "Name"}.
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


class XlsxChunker:
    """Groups XLSX spans by sheet with token-aware splitting."""

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
        """Group XLSX spans into extraction-ready chunks by sheet.

        Args:
            spans: List of span dicts from XLSX parser.
            document_id: Parent document UUID.

        Returns:
            List of ExtractionChunk objects sorted by sheet name.
        """
        valid_spans = _filter_valid_spans(spans)
        if not valid_spans:
            return []

        sheet_spans: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for span in valid_spans:
            locator = span.get("locator", {})
            sheet_name = locator.get("sheet", "unknown")
            sheet_spans[sheet_name].append(span)

        chunks: list[ExtractionChunk] = []
        for sheet_name in sorted(sheet_spans.keys()):
            group = sorted(
                sheet_spans[sheet_name],
                key=lambda s: locator_sort_key(s.get("locator", {})),
            )
            chunks.extend(self._chunk_sheet(group, sheet_name=sheet_name, document_id=document_id))

        return chunks

    def _chunk_sheet(
        self,
        sheet_group: list[dict[str, Any]],
        *,
        sheet_name: str,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Chunk a single sheet's spans, splitting by row ranges if needed.

        Args:
            sheet_group: All spans on this sheet (sorted by locator).
            sheet_name: The sheet name.
            document_id: Parent document UUID.

        Returns:
            One or more chunks for this sheet.
        """
        combined_text = "\n".join(s.get("text_excerpt", "") for s in sheet_group)
        total_tokens = estimate_tokens(combined_text)

        if total_tokens <= self._max_tokens:
            return [
                _make_chunk(
                    spans_group=sheet_group,
                    locator={"sheet": sheet_name},
                    document_id=document_id,
                )
            ]

        return self._split_sheet_by_rows(
            sheet_group, sheet_name=sheet_name, document_id=document_id
        )

    def _split_sheet_by_rows(
        self,
        sheet_group: list[dict[str, Any]],
        *,
        sheet_name: str,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Split oversized sheet into row-range chunks.

        Args:
            sheet_group: All spans on this sheet.
            sheet_name: The sheet name.
            document_id: Parent document UUID.

        Returns:
            Multiple chunks split by row ranges.
        """
        chunks: list[ExtractionChunk] = []
        current_spans: list[dict[str, Any]] = []
        current_tokens = 0
        range_start = 0
        range_idx = 0

        for span in sheet_group:
            span_tokens = estimate_tokens(span.get("text_excerpt", ""))
            row = span.get("locator", {}).get("row", 0)

            if span_tokens > self._max_tokens and not current_spans:
                sub_chunks = _hard_split_span(
                    span,
                    self._max_tokens,
                    sheet_name,
                    range_idx,
                    document_id,
                )
                chunks.extend(sub_chunks)
                range_idx += len(sub_chunks)
                range_start = row + 1
                continue

            if current_spans and current_tokens + span_tokens > self._max_tokens:
                range_end = current_spans[-1].get("locator", {}).get("row", range_start)
                chunks.append(
                    _make_chunk(
                        spans_group=current_spans,
                        locator={
                            "sheet": sheet_name,
                            "row_range": [range_start, range_end],
                            "part": range_idx,
                        },
                        document_id=document_id,
                    )
                )
                range_idx += 1
                current_spans = []
                current_tokens = 0
                range_start = row

            current_spans.append(span)
            current_tokens += span_tokens

        if current_spans:
            range_end = current_spans[-1].get("locator", {}).get("row", range_start)
            locator: dict[str, Any] = {"sheet": sheet_name}
            if range_idx > 0:
                locator["row_range"] = [range_start, range_end]
                locator["part"] = range_idx
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
    sheet_name: str,
    part_start: int,
    document_id: str,
) -> list[ExtractionChunk]:
    """Hard-split a single oversized span by words.

    Args:
        span: The oversized span dict.
        max_tokens: Maximum tokens per chunk.
        sheet_name: Sheet name for locator.
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
        locator = {"sheet": sheet_name, "part": part}
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
                doc_type="XLSX",
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
        doc_type="XLSX",
        token_estimate=estimate_tokens(content),
    )
