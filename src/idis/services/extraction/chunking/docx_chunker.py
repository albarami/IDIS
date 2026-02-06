"""DOCX chunker — groups PARAGRAPH spans by section with table separation.

Chunking strategy per spec §3.3:
- Group PARAGRAPH spans by section (heading + following paragraphs).
- Section boundaries detected from heading-like spans (short, title-case text).
- Table spans (CELL type with table key in locator) get their own chunk.
- Locator: {section: "Heading Text", para_range: [X, Y]} or {table: T}.
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

HEADING_MAX_WORDS = 10


def _is_heading(text: str) -> bool:
    """Detect if text looks like a section heading.

    Heuristic: short text (≤10 words) that is either title-case, all-caps,
    or ends without sentence-ending punctuation.

    Args:
        text: Span text content.

    Returns:
        True if text appears to be a heading.
    """
    stripped = text.strip()
    if not stripped:
        return False
    words = stripped.split()
    if len(words) > HEADING_MAX_WORDS:
        return False
    if stripped.isupper():
        return True
    if stripped.istitle():
        return True
    return stripped[-1] not in ".!?:;" and len(words) <= 5


class DocxChunker:
    """Groups DOCX spans by section with table separation."""

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
        """Group DOCX spans into extraction-ready chunks by section.

        Args:
            spans: List of span dicts from DOCX parser.
            document_id: Parent document UUID.

        Returns:
            List of ExtractionChunk objects sorted by locator.
        """
        valid_spans = _filter_valid_spans(spans)
        if not valid_spans:
            return []

        table_spans: list[dict[str, Any]] = []
        para_spans: list[dict[str, Any]] = []

        for span in valid_spans:
            locator = span.get("locator", {})
            if "table" in locator:
                table_spans.append(span)
            else:
                para_spans.append(span)

        para_spans.sort(key=lambda s: locator_sort_key(s.get("locator", {})))

        chunks: list[ExtractionChunk] = []
        chunks.extend(self._chunk_paragraphs(para_spans, document_id=document_id))
        chunks.extend(self._chunk_tables(table_spans, document_id=document_id))

        return chunks

    def _chunk_paragraphs(
        self,
        para_spans: list[dict[str, Any]],
        *,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Group paragraph spans into section-based chunks.

        Detects heading-like text as section boundaries.
        If total tokens for a section exceed max_tokens, the section is split.

        Args:
            para_spans: Sorted paragraph spans.
            document_id: Parent document UUID.

        Returns:
            List of chunks grouped by section.
        """
        if not para_spans:
            return []

        sections = self._split_into_sections(para_spans)
        chunks: list[ExtractionChunk] = []

        for section_name, section_spans in sections:
            chunks.extend(
                self._chunk_section(
                    section_spans,
                    section_name=section_name,
                    document_id=document_id,
                )
            )

        return chunks

    def _split_into_sections(
        self,
        para_spans: list[dict[str, Any]],
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        """Split paragraph spans into sections by heading detection.

        Args:
            para_spans: Sorted paragraph spans.

        Returns:
            List of (section_name, spans) tuples.
        """
        sections: list[tuple[str, list[dict[str, Any]]]] = []
        current_name = "default"
        current_spans: list[dict[str, Any]] = []

        for span in para_spans:
            text = span.get("text_excerpt", "").strip()
            if _is_heading(text) and current_spans:
                sections.append((current_name, current_spans))
                current_name = text
                current_spans = [span]
            elif _is_heading(text) and not current_spans:
                current_name = text
                current_spans = [span]
            else:
                current_spans.append(span)

        if current_spans:
            sections.append((current_name, current_spans))

        return sections

    def _chunk_section(
        self,
        section_spans: list[dict[str, Any]],
        *,
        section_name: str,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Chunk a single section, splitting if over token limit.

        Args:
            section_spans: Spans in this section.
            section_name: Section heading text.
            document_id: Parent document UUID.

        Returns:
            One or more chunks for this section.
        """
        chunks: list[ExtractionChunk] = []
        current_group: list[dict[str, Any]] = []
        current_tokens = 0
        part_idx = 0

        for span in section_spans:
            span_tokens = estimate_tokens(span.get("text_excerpt", ""))

            if span_tokens > self._max_tokens and not current_group:
                sub_chunks = _hard_split_span(
                    span,
                    self._max_tokens,
                    section_name,
                    part_idx,
                    document_id,
                )
                chunks.extend(sub_chunks)
                part_idx += len(sub_chunks)
                continue

            if current_group and current_tokens + span_tokens > self._max_tokens:
                group_start = current_group[0].get("locator", {}).get("paragraph", 0)
                group_end = current_group[-1].get("locator", {}).get("paragraph", 0)
                locator: dict[str, Any] = {
                    "section": section_name,
                    "para_range": [group_start, group_end],
                }
                if part_idx > 0:
                    locator["part"] = part_idx
                chunks.append(
                    _make_chunk(
                        spans_group=current_group,
                        locator=locator,
                        document_id=document_id,
                    )
                )
                part_idx += 1
                current_group = []
                current_tokens = 0

            current_group.append(span)
            current_tokens += span_tokens

        if current_group:
            group_start = current_group[0].get("locator", {}).get("paragraph", 0)
            group_end = current_group[-1].get("locator", {}).get("paragraph", 0)
            locator = {
                "section": section_name,
                "para_range": [group_start, group_end],
            }
            if part_idx > 0:
                locator["part"] = part_idx
            chunks.append(
                _make_chunk(
                    spans_group=current_group,
                    locator=locator,
                    document_id=document_id,
                )
            )

        return chunks

    def _chunk_tables(
        self,
        table_spans: list[dict[str, Any]],
        *,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Group table cell spans by table index into separate chunks.

        Args:
            table_spans: Spans with table key in locator.
            document_id: Parent document UUID.

        Returns:
            One chunk per table.
        """
        if not table_spans:
            return []

        tables: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
        for span in table_spans:
            table_idx = span.get("locator", {}).get("table", 0)
            tables[table_idx].append(span)

        chunks: list[ExtractionChunk] = []
        for table_idx in sorted(tables.keys()):
            group = sorted(
                tables[table_idx],
                key=lambda s: locator_sort_key(s.get("locator", {})),
            )
            chunks.append(
                _make_chunk(
                    spans_group=group,
                    locator={"table": table_idx},
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
    section_name: str,
    part_start: int,
    document_id: str,
) -> list[ExtractionChunk]:
    """Hard-split a single oversized span by words.

    Args:
        span: The oversized span dict.
        max_tokens: Maximum tokens per chunk.
        section_name: Section name for locator.
        part_start: Starting part index.
        document_id: Parent document UUID.

    Returns:
        List of chunks, each within max_tokens.
    """
    text = span.get("text_excerpt", "")
    span_id = span.get("span_id", "")
    para_idx = span.get("locator", {}).get("paragraph", 0)
    words = text.split()
    max_words = int(max_tokens / 1.3)
    chunks: list[ExtractionChunk] = []
    idx = 0
    part = part_start

    while idx < len(words):
        segment_words = words[idx : idx + max_words]
        segment_text = " ".join(segment_words)
        locator: dict[str, Any] = {
            "section": section_name,
            "para_range": [para_idx, para_idx],
            "part": part,
        }
        span_ids = (span_id,)
        chunks.append(
            ExtractionChunk(
                chunk_id=deterministic_chunk_id(document_id, locator, span_ids),
                document_id=document_id,
                span_ids=span_ids,
                content=segment_text,
                locator=locator_sort_key(locator),
                doc_type="DOCX",
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
        doc_type="DOCX",
        token_estimate=estimate_tokens(content),
    )
