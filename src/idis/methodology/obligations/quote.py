"""Exact and folded quote location."""

from __future__ import annotations

from dataclasses import dataclass

from idis.methodology.obligations.segments import fold_with_spans
from idis.methodology.obligations.vocab import Code


@dataclass(frozen=True, slots=True)
class QuoteHit:
    """A resolved half-open span."""

    start: int
    end: int
    occurrence: int
    folded_only: bool


def exact_spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Return non-overlapping left-to-right exact matches."""
    if needle == "":
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            break
        spans.append((found, found + len(needle)))
        start = found + len(needle)
    return spans


def resolve_in_text(text: str, needle: str, occurrence: int) -> QuoteHit | str:
    """Resolve one quote. A string return is a ``GL02_`` code."""
    if needle == "":
        return Code.GL02_QUOTE_NOT_FOUND.value
    matches = exact_spans(text, needle)
    if occurrence < 0:
        return Code.GL02_QUOTE_NOT_FOUND.value
    if occurrence < len(matches):
        start, end = matches[occurrence]
        return QuoteHit(start=start, end=end, occurrence=occurrence, folded_only=False)
    if matches:
        return Code.GL02_QUOTE_NOT_FOUND.value
    folded = _folded_spans(text, needle)
    if folded:
        return Code.GL02_QUOTE_FOLD_MATCH_ONLY.value
    return Code.GL02_QUOTE_NOT_FOUND.value


def quote_failure_span(text: str, needle: str, occurrence: int) -> tuple[int, int]:
    """Return a span for a quote that did not resolve exactly.

    The first fold-only match is returned when the needle has no exact match.
    An occurrence past the exact matches, or no match at all, returns ``(0, 0)``.
    ``(0, 0)`` is not a located span: a non-empty match always has ``end > start``.
    """
    if needle == "" or occurrence < 0:
        return (0, 0)
    matches = exact_spans(text, needle)
    if occurrence < len(matches):
        return matches[occurrence]
    if matches:
        return (0, 0)
    folded = _folded_spans(text, needle)
    if folded:
        return folded[0]
    return (0, 0)


def locate_phrase(text: str, needle: str) -> tuple[QuoteHit, ...] | str:
    """Find a phrase for the quote command.

    Exact matches are returned in order. When none exist, a unique folded
    match is returned. Several folded matches return ``GL02_QUOTE_FOLD_AMBIGUOUS``.
    """
    if needle == "":
        return Code.GL02_QUOTE_NOT_FOUND.value
    matches = exact_spans(text, needle)
    if matches:
        return tuple(
            QuoteHit(start=start, end=end, occurrence=index, folded_only=False)
            for index, (start, end) in enumerate(matches)
        )
    folded = _folded_spans(text, needle)
    if len(folded) == 1:
        start, end = folded[0]
        return (QuoteHit(start=start, end=end, occurrence=0, folded_only=True),)
    if len(folded) > 1:
        return Code.GL02_QUOTE_FOLD_AMBIGUOUS.value
    return Code.GL02_QUOTE_NOT_FOUND.value


def _folded_spans(text: str, needle: str) -> list[tuple[int, int]]:
    folded_text, spans = fold_with_spans(text)
    folded_needle, _needle_spans = fold_with_spans(needle)
    if folded_needle.strip() == "":
        return []
    found: list[tuple[int, int]] = []
    start = 0
    while True:
        index = folded_text.find(folded_needle, start)
        if index < 0:
            break
        end = index + len(folded_needle)
        raw_start = spans[index][0]
        raw_end = spans[end - 1][1]
        found.append((raw_start, raw_end))
        start = end
    return found
