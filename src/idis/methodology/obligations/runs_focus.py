"""Focus, breadth and span arithmetic for run accounting."""

from __future__ import annotations

import re

from idis.methodology.obligations.findings import make_finding, span_token
from idis.methodology.obligations.models_authored import ChildObligation, Disposition
from idis.methodology.obligations.models_records import Finding, ParentRecord
from idis.methodology.obligations.runs import (
    _WORD,
    FieldMetrics,
    MarkerHit,
    ResolvedSpan,
    TextRun,
    _resolved_quotes,
    field_text,
    is_material,
)
from idis.methodology.obligations.segments import (
    Segment,
    TokenSpan,
    split_segments,
    tokenize,
    trim_span,
)
from idis.methodology.obligations.vocab import (
    DISPOSITION_HEAVY_DENOMINATOR,
    DISPOSITION_HEAVY_NUMERATOR,
    FOCUS_MAX_REACH,
    LABEL_FIELDS,
    PREFIX_MARKERS,
    QUOTE_BROAD_SEGMENT_MIN,
    QUOTE_FIELDS,
    Code,
    DispositionKind,
    FindingSeverity,
    RunDisposition,
)


def _focus_for(
    parent: ParentRecord,
    text: str,
    segments: tuple[Segment, ...],
    quotes: tuple[ResolvedSpan, ...],
    *,
    field: str,
    label: bool,
) -> list[Finding]:
    """Focus findings for covered material segments.

    A material segment that no child covers is left to the run rule.
    """
    material, reach, reasons, coverage = _coverage(text, segments, quotes, label=label)
    findings: list[Finding] = []
    for index, segment in enumerate(material):
        covers = coverage.get(index, [])
        if not covers:
            continue
        focused = any(reach[child_id] <= FOCUS_MAX_REACH for child_id in covers)
        reasoned = all(_nonempty(reasons.get(child_id)) for child_id in covers)
        if focused or reasoned:
            continue
        left, right = trim_span(text, segment.start, segment.end)
        findings.append(
            make_finding(
                Code.GL02_SEGMENT_NOT_FOCUSED.value,
                scope=parent.public_coordinate,
                ids=(parent.parent_id, *covers, span_token(field, left, right)),
            )
        )
    return findings


def _coverage(
    text: str,
    segments: tuple[Segment, ...],
    quotes: tuple[ResolvedSpan, ...],
    *,
    label: bool,
) -> tuple[list[Segment], dict[str, int], dict[str, str | None], dict[int, list[str]]]:
    """Return material segments, per-child reach, reasons and covering child ids."""
    material = [segment for segment in segments if _segment_material(text, segment, label=label)]
    coverage: dict[int, list[str]] = {}
    child_segments: dict[str, set[int]] = {}
    reasons: dict[str, str | None] = {}
    for index, segment in enumerate(material):
        tokens = _segment_tokens(text, segment)
        for quote in quotes:
            if not _covers(text, segment, tokens, quote):
                continue
            coverage.setdefault(index, []).append(quote.child_id)
            child_segments.setdefault(quote.child_id, set()).add(index)
            reasons[quote.child_id] = quote.joint_answer_reason
    reach = {child_id: len(indexes) for child_id, indexes in child_segments.items()}
    return material, reach, reasons, coverage


def joint_reason_findings(
    parents: tuple[ParentRecord, ...],
    children: tuple[ChildObligation, ...],
    *,
    limit: frozenset[str] | None,
) -> list[Finding]:
    """I-08. A child reaching more than two material segments needs a reason."""
    findings: list[Finding] = []
    for parent in parents:
        if limit is not None and parent.parent_id not in limit:
            continue
        for field in QUOTE_FIELDS:
            text = field_text(parent, field)
            if not text:
                continue
            quotes = _resolved_quotes(parent.parent_id, field, text, children)
            _material, reach, reasons, _coverage_map = _coverage(
                text,
                tuple(split_segments(text)),
                quotes,
                label=field in LABEL_FIELDS,
            )
            for child_id, count in reach.items():
                if count <= FOCUS_MAX_REACH or _nonempty(reasons.get(child_id)):
                    continue
                findings.append(
                    make_finding(
                        Code.GL02_JOINT_REASON_MISSING.value,
                        scope=parent.public_coordinate,
                        ids=(child_id, field),
                    )
                )
    return findings


def _segment_material(text: str, segment: Segment, *, label: bool) -> bool:
    left, right = trim_span(text, segment.start, segment.end)
    if left >= right:
        return False
    return is_material(text[left:right], label=label)


def _segment_tokens(text: str, segment: Segment) -> list[TokenSpan]:
    left, right = trim_span(text, segment.start, segment.end)
    if left >= right:
        return []
    return [token for token in tokenize(text[left:right], left) if token.content]


def _covers(text: str, segment: Segment, tokens: list[TokenSpan], quote: ResolvedSpan) -> bool:
    del text
    if not tokens:
        left, right = segment.start, segment.end
        return quote.start < right and quote.end > left
    covered = sum(1 for token in tokens if token.start < quote.end and token.end > quote.start)
    return covered * 2 >= len(tokens)


def _breadth_warnings(
    parent: ParentRecord,
    text: str,
    segments: tuple[Segment, ...],
    quotes: tuple[ResolvedSpan, ...],
    dispositions: tuple[Disposition, ...],
    runs: tuple[TextRun, ...],
    markers: tuple[MarkerHit, ...],
) -> list[Finding]:
    findings: list[Finding] = []
    if len(segments) >= QUOTE_BROAD_SEGMENT_MIN:
        for quote in quotes:
            if (quote.end - quote.start) * 2 > len(text):
                findings.append(
                    make_finding(
                        Code.GL02_QUOTE_TOO_BROAD.value,
                        severity=FindingSeverity.WARNING,
                        scope=parent.public_coordinate,
                        ids=(parent.parent_id, quote.child_id),
                    )
                )
                break
    run_keys = {(item.start, item.end, item.text_sha256) for item in runs}
    marker_keys = {(item.start, item.end, item.text_sha256) for item in markers}
    heavy_spans = [
        (item.start, item.end)
        for item in dispositions
        if _counts_toward_heavy(item, run_keys, marker_keys)
    ]
    heavy = _covered_length(_merge(heavy_spans))
    if text and heavy * DISPOSITION_HEAVY_DENOMINATOR > len(text) * DISPOSITION_HEAVY_NUMERATOR:
        findings.append(
            make_finding(
                Code.GL02_DISPOSITION_HEAVY.value,
                severity=FindingSeverity.WARNING,
                scope=parent.public_coordinate,
                ids=(parent.parent_id,),
            )
        )
    return findings


def _counts_toward_heavy(
    item: Disposition,
    run_keys: set[tuple[int, int, str]],
    marker_keys: set[tuple[int, int, str]],
) -> bool:
    """Heavy warnings count current rationale and non-obligation spans only."""
    if item.disposition not in {
        RunDisposition.NOT_AN_OBLIGATION.value,
        RunDisposition.RATIONALE_ONLY.value,
    }:
        return False
    key = (item.start, item.end, item.text_sha256)
    if item.kind is DispositionKind.RUN:
        return key in run_keys
    if item.kind is DispositionKind.MARKER:
        return key in marker_keys
    return False


def _metrics(
    text: str,
    quotes: tuple[ResolvedSpan, ...],
    runs: tuple[TextRun, ...],
    dispositions: tuple[Disposition, ...],
) -> FieldMetrics:
    spans = [(item.start, item.end) for item in quotes]
    longest = max((item.end - item.start for item in quotes), default=0)
    material = [run for run in runs if run.material]
    material_keys = {(run.start, run.end, run.text_sha256) for run in material}
    dispositioned = sum(
        1
        for item in dispositions
        if item.kind is DispositionKind.RUN
        and (item.start, item.end, item.text_sha256) in material_keys
    )
    return FieldMetrics(
        covered_characters=_covered_length(_merge(spans)),
        text_characters=len(text),
        longest_quote_characters=longest,
        word_count=len(_WORD.findall(text)),
        material_runs=len(material),
        dispositioned_material_runs=dispositioned,
    )


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted((start, end) for start, end in spans if end > start)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _subtract(span: tuple[int, int], covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    start, end = span
    pieces = [(start, end)]
    for cover_start, cover_end in covered:
        next_pieces: list[tuple[int, int]] = []
        for left, right in pieces:
            if cover_end <= left or cover_start >= right:
                next_pieces.append((left, right))
                continue
            if cover_start > left:
                next_pieces.append((left, cover_start))
            if cover_end < right:
                next_pieces.append((cover_end, right))
        pieces = next_pieces
    return [(left, right) for left, right in pieces if right > left]


def _covered_length(spans: list[tuple[int, int]]) -> int:
    return sum(end - start for start, end in spans)


def _nonempty(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def _pattern(marker: str) -> re.Pattern[str]:
    if marker in PREFIX_MARKERS:
        body = re.escape(marker) + r"\w*"
    else:
        body = r"\s+".join(re.escape(part) for part in marker.split(" "))
    return re.compile(rf"(?i)(?<!\w){body}(?!\w)")


def focus_findings_for_field(
    parent: ParentRecord,
    field: str,
    text: str,
    quotes: tuple[ResolvedSpan, ...],
) -> list[Finding]:
    """Focus findings for one field, using that field's materiality rule."""
    segments = tuple(split_segments(text))
    return _focus_for(parent, text, segments, quotes, field=field, label=field in LABEL_FIELDS)
