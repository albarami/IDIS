"""Baseline and view checks for the obligation register."""

from __future__ import annotations

from idis.methodology.obligations.checks_support import (
    _ambiguity_link,
    _closure,
    _resolve_ref,
)
from idis.methodology.obligations.findings import make_finding
from idis.methodology.obligations.models_authored import BaselineExpectation
from idis.methodology.obligations.models_records import Finding
from idis.methodology.obligations.register_io import Register
from idis.methodology.obligations.vocab import Code, FindingSeverity


def check_baseline(register: Register, limit: frozenset[str] | None) -> list[Finding]:
    """I-17. The register satisfies authored baseline expectations."""
    findings: list[Finding] = []
    covered = {
        parent_id for item in register.merged_instructions for parent_id in item.covered_parent_ids
    }
    for _shard, expectation in register.baseline:
        kind = expectation.kind.value
        if kind == "merged_instruction":
            findings.extend(_merged_baseline(register, expectation, covered, limit))
            continue
        if kind == "cross_row_instruction":
            findings.extend(_cross_row_baseline(register, expectation, limit))
            continue
        findings.extend(_named_baseline(register, expectation, limit))
    return findings


def _merged_baseline(
    register: Register,
    expectation: BaselineExpectation,
    covered: set[str],
    limit: frozenset[str] | None,
) -> list[Finding]:
    parent = register.parents_by_coordinate.get(expectation.source_coordinate) or register.parent(
        expectation.source_coordinate
    )
    source_id = None if parent is None else parent.parent_id
    target_ids = [
        resolved
        for item in expectation.target_coordinates
        if (resolved := _resolve_ref(register, item)) is not None
    ]
    known = [item for item in (source_id, *target_ids) if item is not None]
    outside = _outside_parents(known, limit)
    if outside:
        return _pending_baseline(expectation.expectation_id, outside)
    if parent is None or parent.parent_id not in covered:
        return [make_finding(Code.GL02_BASELINE_UNMET.value, ids=(expectation.expectation_id,))]
    return []


def _cross_row_baseline(
    register: Register,
    expectation: BaselineExpectation,
    limit: frozenset[str] | None,
) -> list[Finding]:
    source = _resolve_ref(register, expectation.source_coordinate)
    targets = [_resolve_ref(register, item) for item in expectation.target_coordinates]
    known = [item for item in (source, *targets) if item is not None]
    outside = _outside_parents(known, limit)
    if outside:
        return _pending_baseline(expectation.expectation_id, outside)
    matched = [
        record
        for _file_shard, record in register.cross_rows
        if (source is not None and record.source_parent_id == source)
        and set(record.target_parent_ids) >= {item for item in targets if item is not None}
    ]
    if source is None or not matched or any(item is None for item in targets):
        return [make_finding(Code.GL02_BASELINE_UNMET.value, ids=(expectation.expectation_id,))]
    return []


def _named_baseline(
    register: Register,
    expectation: BaselineExpectation,
    limit: frozenset[str] | None,
) -> list[Finding]:
    findings: list[Finding] = []
    topic = expectation.source_coordinate
    target_ids = [
        resolved
        for item in expectation.target_coordinates
        if (resolved := _resolve_ref(register, item))
    ]
    if len(target_ids) != len(expectation.target_coordinates):
        return [make_finding(Code.GL02_BASELINE_UNMET.value, ids=(expectation.expectation_id,))]
    for parent_id in target_ids:
        if limit is not None and parent_id not in limit:
            findings.append(
                make_finding(
                    Code.PENDING_CROSS_SHARD.value,
                    severity=FindingSeverity.INFO,
                    scope="baseline",
                    ids=(expectation.expectation_id, parent_id),
                )
            )
            continue
        linked = _ambiguity_link(register, parent_id, topic)
        if not linked:
            findings.append(
                make_finding(
                    Code.GL02_BASELINE_UNMET.value, ids=(expectation.expectation_id, parent_id)
                )
            )
    return findings


def _outside_parents(parent_ids: list[str], limit: frozenset[str] | None) -> list[str]:
    if limit is None:
        return []
    outside: list[str] = []
    for parent_id in parent_ids:
        if parent_id in limit or parent_id in outside:
            continue
        outside.append(parent_id)
    return outside


def _pending_baseline(expectation_id: str, parent_ids: list[str]) -> list[Finding]:
    return [
        make_finding(
            Code.PENDING_CROSS_SHARD.value,
            severity=FindingSeverity.INFO,
            scope="baseline",
            ids=(expectation_id, parent_id),
        )
        for parent_id in parent_ids
    ]


def check_views(register: Register) -> tuple[list[Finding], dict[str, frozenset[str]]]:
    """I-18. Every view parent exists. Returns the closure of each view."""
    findings: list[Finding] = []
    closures: dict[str, frozenset[str]] = {}
    for _shard, view in register.views:
        resolved: list[str] = []
        for ref in view.parent_ids:
            parent_id = _resolve_ref(register, ref)
            if parent_id is None:
                findings.append(make_finding(Code.GL02_VIEW_INVALID.value, ids=(view.view_id, ref)))
                continue
            resolved.append(parent_id)
        if view.status.value == "agreed" and not (view.agreed_by and view.agreed_by.strip()):
            findings.append(make_finding(Code.GL02_VIEW_INVALID.value, ids=(view.view_id,)))
        closures[view.view_id] = _closure(register, resolved, view.closure.value)
    return findings, closures
