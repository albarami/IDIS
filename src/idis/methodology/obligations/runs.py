"""Runs, focus, markers and disposition accounting."""

from __future__ import annotations

import re
from dataclasses import dataclass

from idis.methodology.obligations.findings import make_finding, span_token
from idis.methodology.obligations.jsonl import sha256_text
from idis.methodology.obligations.models_authored import (
    ChildObligation,
    Disposition,
)
from idis.methodology.obligations.models_records import Finding, ParentRecord
from idis.methodology.obligations.quote import resolve_in_text
from idis.methodology.obligations.segments import (
    Segment,
    contains_digit,
    contains_directive,
    content_and_qualifier_counts,
    split_segments,
    trim_span,
)
from idis.methodology.obligations.vocab import (
    LABEL_FIELDS,
    MATERIAL_LABEL_MIN_CONTENT_TOKENS,
    MATERIAL_RUN_MIN_CONTENT_TOKENS,
    QUOTE_FIELDS,
    REFERENCE_MARKERS,
    SCOPE_POINTER_MARKERS,
    ChildStatus,
    Code,
    Derivation,
    DispositionKind,
    MarkerDisposition,
    RunDisposition,
)

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ResolvedSpan:
    """A quote that resolved to offsets."""

    child_id: str
    parent_id: str
    field: str
    start: int
    end: int
    derivation: Derivation
    joint_answer_reason: str | None


@dataclass(frozen=True, slots=True)
class TextRun:
    """One uncovered run inside a segment."""

    start: int
    end: int
    text_sha256: str
    content_tokens: int
    qualifier_count: int
    material: bool


@dataclass(frozen=True, slots=True)
class MarkerHit:
    """One marker occurrence. ``text`` is retained for the quote command only."""

    start: int
    end: int
    text_sha256: str
    tier: str
    marker: str
    text: str


@dataclass(frozen=True, slots=True)
class FieldMetrics:
    """Integer metrics for one field."""

    covered_characters: int
    text_characters: int
    longest_quote_characters: int
    word_count: int
    material_runs: int
    dispositioned_material_runs: int


@dataclass(frozen=True, slots=True)
class FieldAccounting:
    """Accounting for one parent field."""

    field: str
    segments: tuple[Segment, ...]
    runs: tuple[TextRun, ...]
    markers: tuple[MarkerHit, ...]
    quotes: tuple[ResolvedSpan, ...]
    metrics: FieldMetrics
    findings: tuple[Finding, ...]


def field_text(parent: ParentRecord, field: str) -> str | None:
    """Return the raw text of a parent field."""
    mapping = {
        "question": parent.question_raw,
        "line_item": parent.line_item_raw,
        "term": parent.term_raw,
        "nature": parent.nature_raw,
    }
    return mapping.get(field)


def is_material(text: str, *, label: bool) -> bool:
    """Apply the question or label materiality rule.

    A question mark, a directive word, a qualifier or a digit makes the text
    material at any length. Otherwise the question threshold is 4 content
    tokens and the label threshold is 5.
    """
    if text.rstrip().endswith("?"):
        return True
    if contains_directive(text):
        return True
    content, qualifiers = content_and_qualifier_counts(text)
    if qualifiers or contains_digit(text):
        return True
    threshold = MATERIAL_LABEL_MIN_CONTENT_TOKENS if label else MATERIAL_RUN_MIN_CONTENT_TOKENS
    return content >= threshold


def account_field(
    parent: ParentRecord,
    field: str,
    children: tuple[ChildObligation, ...],
    dispositions: tuple[Disposition, ...],
) -> FieldAccounting:
    """Account one field of one parent."""
    from idis.methodology.obligations.runs_focus import _metrics

    text = field_text(parent, field)
    if text is None:
        empty = FieldMetrics(0, 0, 0, 0, 0, 0)
        return FieldAccounting(field, (), (), (), (), empty, ())
    quotes = _resolved_quotes(parent.parent_id, field, text, children)
    segments = tuple(split_segments(text))
    runs = tuple(_uncovered_runs(text, segments, quotes, label=field in LABEL_FIELDS))
    markers = tuple(_markers(text, field))
    field_dispositions = tuple(
        item for item in dispositions if item.parent_id == parent.parent_id and item.field == field
    )
    findings = _disposition_findings(
        parent,
        field,
        runs,
        markers,
        quotes,
        segments,
        text,
        field_dispositions,
        children,
    )
    metrics = _metrics(text, quotes, runs, field_dispositions)
    return FieldAccounting(field, segments, runs, markers, quotes, metrics, tuple(findings))


def account_parent(
    parent: ParentRecord,
    children: tuple[ChildObligation, ...],
    dispositions: tuple[Disposition, ...],
) -> tuple[FieldAccounting, ...]:
    """Account every text field of a parent."""
    return tuple(account_field(parent, field, children, dispositions) for field in QUOTE_FIELDS)


def _resolved_quotes(
    parent_id: str,
    field: str,
    text: str,
    children: tuple[ChildObligation, ...],
) -> tuple[ResolvedSpan, ...]:
    spans: list[ResolvedSpan] = []
    for child in children:
        if child.status is not ChildStatus.ACTIVE:
            continue
        for quote in child.source_quotes:
            cited = child.parent_id if quote.parent_id is None else quote.parent_id
            if cited != parent_id or quote.field != field:
                continue
            resolved = resolve_in_text(text, quote.text, quote.occurrence)
            if isinstance(resolved, str):
                continue
            spans.append(
                ResolvedSpan(
                    child_id=child.child_id,
                    parent_id=child.parent_id,
                    field=field,
                    start=resolved.start,
                    end=resolved.end,
                    derivation=child.derivation,
                    joint_answer_reason=child.joint_answer_reason,
                )
            )
    return tuple(spans)


def _uncovered_runs(
    text: str,
    segments: tuple[Segment, ...],
    quotes: tuple[ResolvedSpan, ...],
    *,
    label: bool,
) -> list[TextRun]:
    from idis.methodology.obligations.runs_focus import _merge, _subtract

    covered = _merge([(item.start, item.end) for item in quotes])
    runs: list[TextRun] = []
    for segment in segments:
        for start, end in _subtract((segment.start, segment.end), covered):
            left, right = trim_span(text, start, end)
            if left >= right:
                continue
            piece = text[left:right]
            content, qualifiers = content_and_qualifier_counts(piece)
            runs.append(
                TextRun(
                    start=left,
                    end=right,
                    text_sha256=sha256_text(piece),
                    content_tokens=content,
                    qualifier_count=qualifiers,
                    material=is_material(piece, label=label),
                )
            )
    return runs


def _markers(text: str, field: str) -> list[MarkerHit]:
    from idis.methodology.obligations.runs_focus import _pattern

    del field
    hits: list[MarkerHit] = []
    for tier, markers in (
        ("reference", REFERENCE_MARKERS),
        ("scope", SCOPE_POINTER_MARKERS),
    ):
        for marker in markers:
            pattern = _pattern(marker)
            for match in pattern.finditer(text):
                piece = text[match.start() : match.end()]
                hits.append(
                    MarkerHit(
                        start=match.start(),
                        end=match.end(),
                        text_sha256=sha256_text(piece),
                        tier=tier,
                        marker=marker,
                        text=piece,
                    )
                )
    hits.sort(key=lambda item: (item.start, item.end, item.tier, item.marker))
    return hits


def _disposition_findings(
    parent: ParentRecord,
    field: str,
    runs: tuple[TextRun, ...],
    markers: tuple[MarkerHit, ...],
    quotes: tuple[ResolvedSpan, ...],
    segments: tuple[Segment, ...],
    text: str,
    dispositions: tuple[Disposition, ...],
    children: tuple[ChildObligation, ...],
) -> list[Finding]:
    from idis.methodology.obligations.runs_focus import _breadth_warnings, focus_findings_for_field

    findings: list[Finding] = []
    scope = parent.public_coordinate
    run_keys = {(item.start, item.end, item.text_sha256) for item in runs}
    marker_keys = {(item.start, item.end, item.text_sha256) for item in markers}
    matched_runs: set[tuple[int, int, str]] = set()
    matched_markers: set[tuple[int, int, str]] = set()
    active_ids = {
        child.child_id
        for child in children
        if child.status is ChildStatus.ACTIVE and child.parent_id == parent.parent_id
    }
    seen_dispositions: set[tuple[str, int, int]] = set()
    for item in dispositions:
        identity = (item.kind.value, item.start, item.end)
        if identity in seen_dispositions:
            findings.append(
                make_finding(
                    Code.GL02_DISPOSITION_DUPLICATE.value,
                    scope=scope,
                    ids=(parent.parent_id, span_token(field, item.start, item.end)),
                )
            )
        seen_dispositions.add(identity)
        key = (item.start, item.end, item.text_sha256)
        if item.kind is DispositionKind.RUN:
            if item.disposition not in {value.value for value in RunDisposition}:
                findings.append(
                    make_finding(
                        Code.GL02_DISPOSITION_KIND_INVALID.value,
                        scope=scope,
                        ids=(parent.parent_id,),
                    )
                )
                continue
            if key not in run_keys:
                findings.append(
                    make_finding(
                        Code.GL02_DISPOSITION_STALE.value,
                        scope=scope,
                        ids=_span_ids(
                            parent.parent_id, field, item.start, item.end, item.child_ids
                        ),
                    )
                )
                continue
            matched_runs.add(key)
            if (
                item.disposition == RunDisposition.RESTATES_CHILD.value
                and not set(item.child_ids) <= active_ids
            ):
                findings.append(
                    make_finding(
                        Code.GL02_DISPOSITION_RESTATES_UNLINKED.value,
                        scope=scope,
                        ids=(parent.parent_id, *item.child_ids),
                    )
                )
            if item.disposition == RunDisposition.RESTATES_CHILD.value and not item.child_ids:
                findings.append(
                    make_finding(
                        Code.GL02_DISPOSITION_RESTATES_UNLINKED.value,
                        scope=scope,
                        ids=(parent.parent_id,),
                    )
                )
        elif item.kind is DispositionKind.MARKER:
            if item.disposition not in {value.value for value in MarkerDisposition}:
                findings.append(
                    make_finding(
                        Code.GL02_DISPOSITION_KIND_INVALID.value,
                        scope=scope,
                        ids=(parent.parent_id,),
                    )
                )
                continue
            if key not in marker_keys:
                findings.append(
                    make_finding(
                        Code.GL02_DISPOSITION_STALE.value,
                        scope=scope,
                        ids=_span_ids(
                            parent.parent_id, field, item.start, item.end, item.child_ids
                        ),
                    )
                )
                continue
            matched_markers.add(key)
        else:
            findings.append(
                make_finding(
                    Code.GL02_DISPOSITION_KIND_INVALID.value, scope=scope, ids=(parent.parent_id,)
                )
            )
    for run in runs:
        key = (run.start, run.end, run.text_sha256)
        if run.material and key not in matched_runs:
            findings.append(
                make_finding(
                    Code.GL02_RUN_UNDISPOSITIONED.value,
                    scope=scope,
                    ids=(parent.parent_id, span_token(field, run.start, run.end)),
                    count=1,
                )
            )
    for marker in markers:
        key = (marker.start, marker.end, marker.text_sha256)
        if key in matched_markers:
            continue
        if _marker_quoted(marker, quotes):
            continue
        findings.append(
            make_finding(
                Code.GL02_MARKER_UNACCOUNTED.value,
                scope=scope,
                ids=(parent.parent_id, span_token(field, marker.start, marker.end)),
            )
        )
    findings.extend(focus_findings_for_field(parent, field, text, quotes))
    findings.extend(_breadth_warnings(parent, text, segments, quotes, dispositions, runs, markers))
    return findings


def _span_ids(
    parent_id: str,
    field: str,
    start: int,
    end: int,
    child_ids: tuple[str, ...] = (),
) -> tuple[str, ...]:
    return (parent_id, *child_ids, span_token(field, start, end))


def _marker_quoted(marker: MarkerHit, quotes: tuple[ResolvedSpan, ...]) -> bool:
    for quote in quotes:
        if marker.start < quote.start or marker.end > quote.end:
            continue
        if (
            marker.tier == "reference"
            and quote.derivation is not Derivation.CROSS_REFERENCE_APPLIED
        ):
            continue
        return True
    return False
