"""Safe HTML/TXT parser used by the private data-room gate."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from typing import Literal

from idis.parsers.base import ParseError, ParseErrorCode, ParseLimits, ParseResult, SpanDraft


def parse_html_text(
    data: bytes,
    *,
    is_html: bool,
    limits: ParseLimits | None = None,
) -> ParseResult:
    """Parse plain text or visible HTML text into deterministic spans."""
    if limits is None:
        limits = ParseLimits()

    doc_type: Literal["HTML", "TEXT"] = "HTML" if is_html else "TEXT"
    if len(data) > limits.max_bytes:
        return ParseResult(
            doc_type=doc_type,
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.MAX_SIZE_EXCEEDED,
                    message=f"File size {len(data)} bytes exceeds limit {limits.max_bytes}",
                    details={"size": len(data), "limit": limits.max_bytes},
                )
            ],
        )

    text = data.decode("utf-8-sig", errors="replace")
    spans = _html_spans(text) if is_html else _text_spans(text)
    if not spans:
        return ParseResult(
            doc_type=doc_type,
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.NO_TEXT_EXTRACTED,
                    message="No extractable text found",
                    details={},
                )
            ],
        )

    return ParseResult(
        doc_type=doc_type,
        success=True,
        spans=spans,
        metadata={
            "span_count": len(spans),
            "total_text_length": sum(len(s.text_excerpt) for s in spans),
        },
    )


def _text_spans(text: str) -> list[SpanDraft]:
    spans: list[SpanDraft] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        spans.append(
            SpanDraft(
                span_type="PARAGRAPH",
                locator={"line": line_number, "source": "text"},
                text_excerpt=stripped,
                content_hash=_compute_content_hash(stripped),
            )
        )
    return spans


def _html_spans(text: str) -> list[SpanDraft]:
    extractor = _VisibleTextExtractor()
    extractor.feed(text)
    extractor.close()
    spans: list[SpanDraft] = []
    for node_number, value in enumerate(extractor.visible_text, start=1):
        spans.append(
            SpanDraft(
                span_type="PARAGRAPH",
                locator={"node": node_number, "source": "html"},
                text_excerpt=value,
                content_hash=_compute_content_hash(value),
            )
        )
    return spans


class _VisibleTextExtractor(HTMLParser):
    _IGNORED_TAGS = frozenset({"script", "style", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_text: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in self._IGNORED_TAGS:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._IGNORED_TAGS and self._ignored_depth > 0:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return
        stripped = " ".join(data.split())
        if stripped:
            self.visible_text.append(stripped)


def _compute_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
