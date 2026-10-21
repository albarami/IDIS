"""Rule I-19. Authored text may cite only a parent coordinate or a child id."""

from __future__ import annotations

import re

from idis.methodology.obligations.findings import make_finding
from idis.methodology.obligations.models_authored import (
    Ambiguity,
    ChildObligation,
    CrossRowInstruction,
    Disposition,
    ParentProfile,
)
from idis.methodology.obligations.models_records import Finding
from idis.methodology.obligations.register_io import Register
from idis.methodology.obligations.vocab import (
    CHILD_ID_BODY,
    PUBLIC_COORDINATE_BODY,
    ChildStatus,
    Code,
    FindingSeverity,
    Shard,
)

_CHILD_TOKEN = re.compile(rf"(?<![A-Za-z0-9]){CHILD_ID_BODY}(?![A-Za-z0-9])")
_COORDINATE_TOKEN = re.compile(rf"(?<![A-Za-z0-9]){PUBLIC_COORDINATE_BODY}(?![A-Za-z0-9])")
_COORDINATE_EXACT = re.compile(rf"^{PUBLIC_COORDINATE_BODY}$")
_SHARD_CODES = {"PL": Shard.PL, "CF": Shard.CF, "LIAB": Shard.LIAB, "ASSET": Shard.ASSET}


def check_references(
    register: Register,
    limit: frozenset[str] | None,
    shards: frozenset[Shard] | None,
) -> list[Finding]:
    """I-19. Every cited coordinate or child id in scope resolves.

    A retired target is a warning. A missing target is an error. A target
    parent outside a limited validation is ``PENDING_CROSS_SHARD``.
    """
    children: dict[str, list[ChildObligation]] = {}
    for _shard, child in register.children:
        children.setdefault(child.child_id, []).append(child)
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    for record_id, parent_ids, texts in _records(register):
        if not _record_in_scope(parent_ids, limit):
            continue
        for token in _tokens(texts):
            code, severity = _classify(register, token, children, limit, shards)
            if code is None or severity is None:
                continue
            key = (record_id, token, code)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                make_finding(code, severity=severity, scope="reference", ids=(record_id, token))
            )
    return findings


def find_reference_tokens(text: str) -> tuple[str, ...]:
    """Return boundary-safe child ids and coordinates in ``text``.

    A child id is one token. The coordinate inside it is not a second token.
    A match followed by a letter or digit is not a token.
    """
    return _tokens((text,))


def _tokens(texts: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    found: list[str] = []
    for text in texts:
        child_spans = [match.span() for match in _CHILD_TOKEN.finditer(text)]
        found.extend(match.group(0) for match in _CHILD_TOKEN.finditer(text))
        for match in _COORDINATE_TOKEN.finditer(text):
            span = match.span()
            if any(start <= span[0] and span[1] <= end for start, end in child_spans):
                continue
            found.append(match.group(0))
    return tuple(sorted(set(found)))


def _classify(
    register: Register,
    token: str,
    children: dict[str, list[ChildObligation]],
    limit: frozenset[str] | None,
    shards: frozenset[Shard] | None,
) -> tuple[str | None, FindingSeverity | None]:
    if token.startswith("GL02-"):
        return _classify_child(register, token, children, limit, shards)
    parent = register.parents_by_coordinate.get(token)
    if parent is None:
        if _outside_shard(coordinate_shard(token), shards):
            return Code.PENDING_CROSS_SHARD.value, FindingSeverity.INFO
        return Code.GL02_REFERENCE_UNRESOLVED.value, FindingSeverity.ERROR
    if _outside_parent(parent.parent_id, limit):
        return Code.PENDING_CROSS_SHARD.value, FindingSeverity.INFO
    return None, None


def _classify_child(
    register: Register,
    token: str,
    children: dict[str, list[ChildObligation]],
    limit: frozenset[str] | None,
    shards: frozenset[Shard] | None,
) -> tuple[str | None, FindingSeverity | None]:
    matches = children.get(token, [])
    if matches:
        parent_ids = [item.parent_id for item in matches]
        if limit is not None and all(parent_id not in limit for parent_id in parent_ids):
            return Code.PENDING_CROSS_SHARD.value, FindingSeverity.INFO
        if all(item.status is ChildStatus.RETIRED for item in matches):
            return Code.GL02_REFERENCE_RETIRED.value, FindingSeverity.WARNING
        return None, None
    coordinate = _child_coordinate(token)
    parent = None if coordinate is None else register.parents_by_coordinate.get(coordinate)
    if parent is not None and _outside_parent(parent.parent_id, limit):
        return Code.PENDING_CROSS_SHARD.value, FindingSeverity.INFO
    if parent is None and _outside_shard(coordinate_shard(coordinate), shards):
        return Code.PENDING_CROSS_SHARD.value, FindingSeverity.INFO
    return Code.GL02_REFERENCE_UNRESOLVED.value, FindingSeverity.ERROR


def coordinate_shard(coordinate: str | None) -> Shard | None:
    """Return the shard named by a public coordinate."""
    if coordinate is None:
        return None
    if coordinate.startswith("CDD-"):
        return Shard.CDD
    parts = coordinate.split("-")
    if len(parts) < 3 or parts[0] != "FDD":
        return None
    return _SHARD_CODES.get(parts[1])


def _child_coordinate(child_id: str) -> str | None:
    body = child_id[len("GL02-") :]
    head, separator, tail = body.rpartition("-C")
    if separator != "-C" or len(tail) != 3 or not tail.isdigit():
        return None
    if _COORDINATE_EXACT.fullmatch(head) is None:
        return None
    return head


def _outside_parent(parent_id: str, limit: frozenset[str] | None) -> bool:
    return limit is not None and parent_id not in limit


def _outside_shard(shard: Shard | None, shards: frozenset[Shard] | None) -> bool:
    return shards is not None and shard is not None and shard not in shards


def _record_in_scope(parent_ids: tuple[str, ...], limit: frozenset[str] | None) -> bool:
    if limit is None:
        return True
    return any(parent_id in limit for parent_id in parent_ids)


def _records(register: Register) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    rows: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for _shard, profile in register.profiles:
        rows.append((profile.parent_id, (profile.parent_id,), _profile_texts(profile)))
    for _shard, child in register.children:
        rows.append((child.child_id, (child.parent_id,), tuple(_child_texts(child))))
    for _shard, disposition in register.dispositions:
        record_id = (
            f"disposition:{disposition.parent_id}:{disposition.field}:"
            f"{disposition.start}:{disposition.end}"
        )
        rows.append((record_id, (disposition.parent_id,), _disposition_texts(disposition)))
    for _shard, ambiguity in register.ambiguities:
        rows.append(
            (
                ambiguity.ambiguity_id,
                ambiguity.affected_parent_ids,
                _ambiguity_texts(ambiguity),
            )
        )
    for _shard, instruction in register.cross_rows:
        parents = (instruction.source_parent_id, *instruction.target_parent_ids)
        rows.append((instruction.instruction_id, parents, _cross_row_texts(instruction)))
    return tuple(rows)


def _child_texts(child: ChildObligation) -> list[str]:
    texts = [child.obligation_statement, child.acceptance_case, *child.completeness_conditions]
    if child.derivation_rationale:
        texts.append(child.derivation_rationale)
    if child.judgment_sought:
        texts.append(child.judgment_sought)
    if child.retired_reason:
        texts.append(child.retired_reason)
    if child.fund_feedback is not None and child.fund_feedback.note:
        texts.append(child.fund_feedback.note)
    for fact in child.required_facts:
        texts.append(fact.description)
        texts.extend(fact.needs)
    for evidence in child.acceptable_evidence:
        texts.append(evidence.description)
    if child.producer is not None:
        texts.append(child.producer.detail)
    if child.consumer is not None:
        texts.append(child.consumer.detail)
    if child.capability is not None:
        texts.extend(child.capability.dependencies)
        if child.capability.note:
            texts.append(child.capability.note)
    for rule in child.calculation_rules:
        if rule.note:
            texts.append(rule.note)
    return texts


def _profile_texts(profile: ParentProfile) -> tuple[str, ...]:
    texts: list[str] = []
    if profile.parent_summary_note:
        texts.append(profile.parent_summary_note)
    if profile.default_producer is not None:
        texts.append(profile.default_producer.detail)
    if profile.default_consumer is not None:
        texts.append(profile.default_consumer.detail)
    if profile.default_capability is not None and profile.default_capability.note:
        texts.append(profile.default_capability.note)
    return tuple(texts)


def _disposition_texts(item: Disposition) -> tuple[str, ...]:
    if item.note:
        return (item.note,)
    return ()


def _ambiguity_texts(item: Ambiguity) -> tuple[str, ...]:
    """Text fields of an ambiguity record. ``description`` is the only one."""
    return (item.description,)


def _cross_row_texts(item: CrossRowInstruction) -> tuple[str, ...]:
    if item.note:
        return (item.note,)
    return ()
