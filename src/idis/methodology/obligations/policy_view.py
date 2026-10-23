"""Policy-v2 view built from plain JSON. This module does not import a policy constructor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.findings import make_finding
from idis.methodology.obligations.jsonl import sha256_bytes
from idis.methodology.obligations.models_records import (
    Finding,
    InputFingerprint,
    ParentRecord,
    PolicyAnchorView,
    PolicyBindingView,
    PolicyViewRecord,
)
from idis.methodology.obligations.vocab import Code

_SOURCE_KIND = "SOURCE"


@dataclass(frozen=True, slots=True)
class PolicyBuild:
    """Policy view plus the counts the extractor reports."""

    records: tuple[PolicyViewRecord, ...]
    fingerprint: InputFingerprint
    anchor_count: int
    source_binding_count: int
    non_source_input_spec_count: int
    findings: tuple[Finding, ...]


def read_policy(path: Path) -> tuple[object, InputFingerprint]:
    """Read a policy file as JSON with duplicate-key rejection."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Gl02Error(Code.GL02_IO.value) from exc
    fingerprint = InputFingerprint(sha256=sha256_bytes(raw), byte_count=len(raw))
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Gl02Error(Code.GL02_IO.value) from exc
    return payload, fingerprint


def build_policy_view(path: Path, parents: tuple[ParentRecord, ...]) -> PolicyBuild:
    """Build one view record per parent that carries an anchor or a source binding."""
    payload, fingerprint = read_policy(path)
    if not isinstance(payload, dict):
        raise Gl02Error(Code.GL02_IO.value)
    by_position = {
        (parent.sheet, parent.source_row): parent
        for parent in parents
        if parent.sheet is not None and parent.source_row is not None
    }
    findings: list[Finding] = []
    anchors: dict[str, list[PolicyAnchorView]] = {}
    bindings: dict[str, list[PolicyBindingView]] = {}
    anchor_count = 0
    source_count = 0
    non_source = 0

    for row in _rows(payload, "workbook_row_registry"):
        parent = _cited_parent(row, by_position, "source_sheet", "source_row")
        question_id = row.get("methodology_question_id")
        if parent is None or not isinstance(question_id, str) or question_id != parent.parent_id:
            findings.append(_mismatch(row, question_id if isinstance(question_id, str) else None))

    for row in _rows(payload, "formula_registry"):
        anchor_parent = _cited_parent(row, by_position, "anchor_sheet", "anchor_row")
        ordinal = _int(row.get("formula_ordinal"))
        formula_type = row.get("formula_type")
        if anchor_parent is None or ordinal is None or not isinstance(formula_type, str):
            findings.append(_mismatch(row, anchor_parent.parent_id if anchor_parent else None))
        else:
            anchor_count += 1
            anchors.setdefault(anchor_parent.parent_id, []).append(
                PolicyAnchorView(formula_ordinal=ordinal, formula_type=formula_type)
            )
        for spec in _specs(row):
            kind = spec.get("binding_kind")
            if kind != _SOURCE_KIND:
                non_source += 1
                continue
            source_count += 1
            source_parent = _cited_parent(spec, by_position, "source_sheet", "source_row")
            binding_ordinal = _int(spec.get("binding_ordinal"))
            role = spec.get("role")
            if (
                source_parent is None
                or ordinal is None
                or binding_ordinal is None
                or not isinstance(role, str)
            ):
                findings.append(_mismatch(spec, source_parent.parent_id if source_parent else None))
                continue
            bindings.setdefault(source_parent.parent_id, []).append(
                PolicyBindingView(
                    formula_ordinal=ordinal,
                    binding_ordinal=binding_ordinal,
                    role=role,
                    predicate=_optional_str(spec.get("predicate")),
                    sign_transform=_optional_str(spec.get("sign_transform")),
                    cardinality=_optional_str(spec.get("cardinality")),
                    period_role=_optional_str(spec.get("period_role")),
                )
            )

    records: list[PolicyViewRecord] = []
    for parent_id in sorted(set(anchors) | set(bindings)):
        records.append(
            PolicyViewRecord(
                parent_id=parent_id,
                anchors=tuple(
                    sorted(anchors.get(parent_id, []), key=lambda item: item.formula_ordinal)
                ),
                bindings=tuple(
                    sorted(
                        bindings.get(parent_id, []),
                        key=lambda item: (item.formula_ordinal, item.binding_ordinal),
                    )
                ),
            )
        )
    return PolicyBuild(
        records=tuple(records),
        fingerprint=fingerprint,
        anchor_count=anchor_count,
        source_binding_count=source_count,
        non_source_input_spec_count=non_source,
        findings=tuple(findings),
    )


def _rows(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    section = payload.get(key)
    if not isinstance(section, dict):
        raise Gl02Error(Code.GL02_IO.value)
    rows = section.get("rows")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise Gl02Error(Code.GL02_IO.value)
    return rows


def _specs(row: dict[str, object]) -> list[dict[str, object]]:
    specs = row.get("ordered_input_specs", [])
    if not isinstance(specs, list) or not all(isinstance(item, dict) for item in specs):
        raise Gl02Error(Code.GL02_IO.value)
    return specs


def _cited_parent(
    row: dict[str, object],
    by_position: dict[tuple[str, int], ParentRecord],
    sheet_key: str,
    row_key: str,
) -> ParentRecord | None:
    sheet = row.get(sheet_key)
    source_row = _int(row.get(row_key))
    if not isinstance(sheet, str) or source_row is None:
        return None
    return by_position.get((sheet, source_row))


def _mismatch(row: dict[str, object], parent_id: str | None) -> Finding:
    sheet = row.get("source_sheet", row.get("anchor_sheet"))
    source_row = row.get("source_row", row.get("anchor_row"))
    identity = parent_id or (f"{sheet}:{source_row}" if isinstance(sheet, str) else "policy-row")
    return make_finding(Code.GL02_POLICY_ROW_MISMATCH.value, scope="policy", ids=(identity,))


def _int(value: object) -> int | None:
    if type(value) is int:
        return value
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is int:
        return str(value)
    if isinstance(value, str):
        return value
    raise Gl02Error(Code.GL02_JSONL_FLOAT.value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Gl02Error(Code.GL02_JSONL_DUPLICATE_KEY.value)
        result[key] = value
    return result
