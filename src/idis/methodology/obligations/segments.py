"""Segments, tokens and the fold used by quote resolution.

``split_segments`` tiles its input. Splits happen first at ``\\r\\n``, a lone
``\\r`` or ``\\n`` (the terminator stays with the left piece), then after
``.``, ``?``, ``!`` or ``;`` when zero or more closing characters and then
whitespace follow, except after a pinned non-breaking abbreviation, then
inside each outermost parenthesis group that holds at least three top-level
comma-separated items.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_DIGIT = re.compile(r"\d+")
_SENTENCE_END = frozenset(".?!;")
_CLOSERS = frozenset(")]\"'\u2019\u201d")
_FOLD_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
    }
)


@dataclass(frozen=True, slots=True)
class Segment:
    """A half-open span of the original text."""

    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class TokenSpan:
    """One token and whether it counts as content or as a qualifier."""

    text: str
    start: int
    end: int
    content: bool
    qualifier: bool


def split_segments(text: str) -> list[Segment]:
    """Split ``text`` into segments that concatenate back to ``text``."""
    if text == "":
        return []
    parts = _split_newlines(text, 0)
    sentence_parts: list[Segment] = []
    for part in parts:
        sentence_parts.extend(_split_sentences(part))
    listed: list[Segment] = []
    for part in sentence_parts:
        listed.extend(_split_lists(part))
    return listed


def trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Return the span with leading and trailing whitespace removed."""
    raw = text[start:end]
    if raw == "":
        return start, start
    left = len(raw) - len(raw.lstrip())
    right = len(raw.rstrip())
    return start + left, start + right


def tokenize(text: str, start: int = 0) -> list[TokenSpan]:
    """Tokenize ``text``; offsets are shifted by ``start``."""
    from idis.methodology.obligations.vocab import QUALIFIER_TOKENS, STOPWORDS

    spans: list[TokenSpan] = []
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        folded = token.casefold()
        qualifier = folded in QUALIFIER_TOKENS or _DIGIT.fullmatch(token) is not None
        content = len(token) >= 2 and folded not in STOPWORDS
        spans.append(
            TokenSpan(
                text=token,
                start=start + match.start(),
                end=start + match.end(),
                content=content,
                qualifier=qualifier,
            )
        )
    return spans


def content_and_qualifier_counts(text: str) -> tuple[int, int]:
    """Return content-token and qualifier-token counts for a trimmed string."""
    tokens = tokenize(text)
    content = sum(1 for token in tokens if token.content)
    qualifiers = sum(1 for token in tokens if token.qualifier)
    return content, qualifiers


def contains_digit(text: str) -> bool:
    """Return whether ``text`` contains a digit character."""
    return any(character.isdigit() for character in text)


def contains_directive(text: str) -> bool:
    """Return whether ``text`` contains a whole directive word."""
    from idis.methodology.obligations.vocab import DIRECTIVE_TOKENS

    return any(token.text.casefold() in DIRECTIVE_TOKENS for token in tokenize(text))


def fold_with_spans(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Fold text and map each folded character back to a raw half-open span.

    Whitespace runs, including non-breaking spaces and line breaks, become one
    space. Typographic quotes and dashes become ASCII. Matching is casefolded.
    """
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character.isspace() or character == "\u00a0":
            end = index + 1
            while end < length and (text[end].isspace() or text[end] == "\u00a0"):
                end += 1
            chars.append(" ")
            spans.append((index, end))
            index = end
            continue
        mapped = character.translate(_FOLD_TRANSLATION)
        folded = mapped.casefold()
        for out_character in folded:
            chars.append(out_character)
            spans.append((index, index + 1))
        index += 1
    return "".join(chars), spans


def fold_text(text: str) -> str:
    """Return the folded form of ``text``."""
    folded, _spans = fold_with_spans(text)
    return folded


def escape_text(text: str) -> str:
    """Show non-ASCII and control characters as escapes."""
    pieces: list[str] = []
    for character in text:
        codepoint = ord(character)
        if character == "\\":
            pieces.append("\\\\")
        elif character == "\n":
            pieces.append("\\n")
        elif character == "\r":
            pieces.append("\\r")
        elif character == "\t":
            pieces.append("\\t")
        elif codepoint < 32 or codepoint > 126:
            pieces.append(f"\\u{codepoint:04x}")
        else:
            pieces.append(character)
    return "".join(pieces)


def _split_newlines(text: str, base: int) -> list[Segment]:
    segments: list[Segment] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("\r\n", index):
            end = index + 2
        elif text[index] in "\r\n":
            end = index + 1
        else:
            index += 1
            continue
        segments.append(Segment(base + start, base + end, text[start:end]))
        start = end
        index = end
    if start < length:
        segments.append(Segment(base + start, base + length, text[start:]))
    return segments


def _split_sentences(segment: Segment) -> list[Segment]:
    text = segment.text
    cuts = [0]
    length = len(text)
    for index in range(length):
        if text[index] not in _SENTENCE_END:
            continue
        cursor = index + 1
        while cursor < length and text[cursor] in _CLOSERS:
            cursor += 1
        if cursor >= length or not text[cursor].isspace():
            continue
        if _abbreviation_before(text, index):
            continue
        cuts.append(cursor)
    if len(cuts) == 1:
        return [segment]
    bounds = [*cuts, len(text)]
    pieces: list[Segment] = []
    for left, right in zip(bounds, bounds[1:], strict=False):
        if left == right:
            continue
        pieces.append(Segment(segment.start + left, segment.start + right, text[left:right]))
    return pieces


def _split_lists(segment: Segment) -> list[Segment]:
    groups = _outermost_groups(segment.text)
    for open_index, close_index in groups:
        inner = segment.text[open_index + 1 : close_index]
        items = _comma_pieces(inner)
        if items is None:
            continue
        pieces: list[Segment] = []
        before = segment.text[: open_index + 1]
        cursor = segment.start
        if before:
            pieces.append(Segment(cursor, cursor + len(before), before))
            cursor += len(before)
        for item in items:
            pieces.append(Segment(cursor, cursor + len(item), item))
            cursor += len(item)
        after = segment.text[close_index:]
        if after:
            pieces.append(Segment(segment.start + close_index, segment.end, after))
        expanded: list[Segment] = []
        for piece in pieces:
            expanded.extend(_split_lists(piece))
        return expanded
    return [segment]


def _abbreviation_before(text: str, period_index: int) -> bool:
    if text[period_index] != ".":
        return False
    from idis.methodology.obligations.vocab import NON_BREAKING_ABBREVIATIONS

    for abbreviation in sorted(NON_BREAKING_ABBREVIATIONS, key=len, reverse=True):
        start = period_index + 1 - len(abbreviation)
        if start < 0:
            continue
        if text[start : period_index + 1].casefold() != abbreviation:
            continue
        if start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
            continue
        return True
    return False


def _outermost_groups(text: str) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    depth = 0
    start: int | None = None
    for index, character in enumerate(text):
        if character == "(":
            if depth == 0:
                start = index
            depth += 1
        elif character == ")" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                groups.append((start, index))
                start = None
    return groups


def _comma_pieces(inner: str) -> list[str] | None:
    depth = 0
    commas: list[int] = []
    for index, character in enumerate(inner):
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            commas.append(index)
    if len(commas) < 2:
        return None
    pieces = [inner[0 : commas[0]]]
    boundaries = [*commas, len(inner)]
    for left, right in zip(boundaries, boundaries[1:], strict=False):
        pieces.append(inner[left:right])
    return pieces
