"""Span identifiers for quote findings."""

from __future__ import annotations

from idis.methodology.obligations.findings import make_finding, span_token
from idis.methodology.obligations.models_authored import ChildObligation
from idis.methodology.obligations.models_records import Finding
from idis.methodology.obligations.quote import quote_failure_span, resolve_in_text
from idis.methodology.obligations.register_io import Register
from idis.methodology.obligations.vocab import QUOTE_FIELDS, Code


def quote_finding_ids(
    *identity: str,
    field: str,
    text: str | None,
    needle: str,
    occurrence: int,
) -> tuple[str, ...]:
    """Append ``field:start:end`` to the identities for one failed quote.

    A quote that cannot be located uses ``0:0``. A fold-only match uses the
    first folded span. A non-empty located span always has ``end > start``.
    """
    if text is None:
        start, end = 0, 0
    else:
        start, end = quote_failure_span(text, needle, occurrence)
    return (*identity, span_token(field, start, end))


def child_quote_findings(register: Register, child: ChildObligation) -> list[Finding]:
    """Findings for quotes that do not resolve inside the cited parent field."""
    findings: list[Finding] = []
    for quote in child.source_quotes:
        if quote.field not in QUOTE_FIELDS:
            findings.append(
                make_finding(Code.GL02_QUOTE_FIELD_INVALID.value, ids=(child.child_id, quote.field))
            )
            continue
        cited_id = child.parent_id if quote.parent_id is None else quote.parent_id
        cited = register.parent(cited_id)
        if cited is None:
            findings.append(
                make_finding(
                    Code.GL02_QUOTE_NOT_FOUND.value,
                    ids=quote_finding_ids(
                        child.child_id,
                        cited_id,
                        field=quote.field,
                        text=None,
                        needle=quote.text,
                        occurrence=quote.occurrence,
                    ),
                )
            )
            continue
        text = {
            "question": cited.question_raw,
            "line_item": cited.line_item_raw,
            "term": cited.term_raw,
            "nature": cited.nature_raw,
        }[quote.field]
        if text is None:
            findings.append(
                make_finding(
                    Code.GL02_QUOTE_NOT_FOUND.value,
                    ids=quote_finding_ids(
                        child.child_id,
                        cited.public_coordinate,
                        field=quote.field,
                        text=None,
                        needle=quote.text,
                        occurrence=quote.occurrence,
                    ),
                )
            )
            continue
        resolved = resolve_in_text(text, quote.text, quote.occurrence)
        if isinstance(resolved, str):
            findings.append(
                make_finding(
                    resolved,
                    ids=quote_finding_ids(
                        child.child_id,
                        cited.public_coordinate,
                        field=quote.field,
                        text=text,
                        needle=quote.text,
                        occurrence=quote.occurrence,
                    ),
                )
            )
    return findings
