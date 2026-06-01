"""HTML/TEXT chunker — groups html_text PARAGRAPH spans into extraction chunks.

Used for documents parsed by ``idis.parsers.html_text`` (doc_type HTML/TEXT).
Pure, deterministic text grouping over the persisted spans' ``text_excerpt`` and
``locator`` only: spans are sorted by canonical locator and greedily grouped up to
``max_tokens``; an oversized single span is hard-split by words. No provider, LLM,
OCR, media, or network calls.
"""

from __future__ import annotations

import logging
from typing import Any

from idis.services.extraction.chunking.base import (
    DEFAULT_MAX_TOKENS,
    ExtractionChunk,
    deterministic_chunk_id,
    estimate_tokens,
    locator_sort_key,
)

logger = logging.getLogger(__name__)


class TextChunker:
    """Groups HTML/TEXT paragraph spans into deterministic token-bounded chunks."""

    def __init__(self, *, doc_type: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        self._doc_type = doc_type.upper().strip()
        self._max_tokens = max_tokens

    def chunk(
        self,
        spans: list[dict[str, Any]],
        *,
        document_id: str,
    ) -> list[ExtractionChunk]:
        """Group spans into extraction-ready chunks in deterministic locator order."""
        valid = [
            span
            for span in spans
            if isinstance(span.get("text_excerpt"), str) and span["text_excerpt"].strip()
        ]
        if not valid:
            return []
        valid.sort(key=lambda s: locator_sort_key(s.get("locator", {})))

        chunks: list[ExtractionChunk] = []
        group: list[dict[str, Any]] = []
        group_tokens = 0
        part = 0

        for span in valid:
            span_tokens = estimate_tokens(span.get("text_excerpt", ""))
            if span_tokens > self._max_tokens:
                if group:
                    chunks.append(self._make_chunk(group, part, document_id))
                    part += 1
                    group = []
                    group_tokens = 0
                split = self._hard_split(span, part, document_id)
                chunks.extend(split)
                part += len(split)
                continue
            if group and group_tokens + span_tokens > self._max_tokens:
                chunks.append(self._make_chunk(group, part, document_id))
                part += 1
                group = []
                group_tokens = 0
            group.append(span)
            group_tokens += span_tokens

        if group:
            chunks.append(self._make_chunk(group, part, document_id))

        return chunks

    def _make_chunk(
        self,
        spans_group: list[dict[str, Any]],
        part: int,
        document_id: str,
    ) -> ExtractionChunk:
        content = "\n".join(span.get("text_excerpt", "") for span in spans_group)
        span_ids = tuple(span.get("span_id", "") for span in spans_group)
        locator: dict[str, Any] = {"part": part}
        return ExtractionChunk(
            chunk_id=deterministic_chunk_id(document_id, locator, span_ids),
            document_id=document_id,
            span_ids=span_ids,
            content=content,
            locator=locator_sort_key(locator),
            doc_type=self._doc_type,
            token_estimate=estimate_tokens(content),
        )

    def _hard_split(
        self,
        span: dict[str, Any],
        part_start: int,
        document_id: str,
    ) -> list[ExtractionChunk]:
        text = span.get("text_excerpt", "")
        span_ids = (span.get("span_id", ""),)
        words = text.split()
        max_words = max(1, int(self._max_tokens / 1.3))
        chunks: list[ExtractionChunk] = []
        part = part_start
        idx = 0
        while idx < len(words):
            segment_text = " ".join(words[idx : idx + max_words])
            locator: dict[str, Any] = {"part": part}
            chunks.append(
                ExtractionChunk(
                    chunk_id=deterministic_chunk_id(document_id, locator, span_ids),
                    document_id=document_id,
                    span_ids=span_ids,
                    content=segment_text,
                    locator=locator_sort_key(locator),
                    doc_type=self._doc_type,
                    token_estimate=estimate_tokens(segment_text),
                )
            )
            idx += max_words
            part += 1
        return chunks
