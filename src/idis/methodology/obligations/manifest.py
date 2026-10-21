"""Public and private coverage manifests.

The public manifest's keys are ``PUBLIC_MANIFEST_KEYS``. Every string value must
match the format pinned for its key. Per-text hashes and free text are rejected.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.jsonl import canonical_json, sha256_bytes, sha256_text
from idis.methodology.obligations.models_records import ParentRecord, ValidationReport
from idis.methodology.obligations.register_io import DERIVED_NAMES, Register
from idis.methodology.obligations.runs import account_parent
from idis.methodology.obligations.vocab import (
    REGISTER_SCHEMA,
    REGISTER_VERSION,
    SHEET_NAMES,
    SKIPPED_CHECKS,
    ChildStatus,
    Code,
    Derivation,
    DispositionKind,
    InvariantStatus,
    ParentKind,
    Shard,
)

_HEX = re.compile(r"^[0-9a-f]{64}$")
_COORDINATE = re.compile(r"^(?:FDD-(?:PL|CF|LIAB|ASSET)-\d{3}|CDD-[A-Z0-9_]+)$")
_PARENT_ID = re.compile(r"^mq_(?:financial_dd_[0-9a-f]{64}|commercial_dd_[a-z0-9_]+)$")
_FIELDS = frozenset({"question", "line_item", "term", "nature"})
_DERIVATIONS = frozenset(item.value for item in Derivation)
_TOPICS = frozenset(
    {
        "CF_TAX_CONDITIONAL",
        "FCF_CONVERSION_DIRECTION",
        "ZERO_EBITDA",
        "ST_FINANCING_LABELS",
        "ASSETS_ROW19_NEGATION",
        "JURISDICTIONAL_TAX",
        "DA_CAPEX_HEURISTIC",
        "OTHER",
    }
)
_INVARIANT_IDS = frozenset(
    {
        "I-01",
        "I-02",
        "I-03",
        "I-04",
        "I-05",
        "I-06",
        "I-06b",
        "I-07",
        "I-08",
        "I-09",
        "I-10",
        "I-11",
        "I-12",
        "I-13",
        "I-14",
        "I-15",
        "I-16",
        "I-17",
        "I-18",
    }
)
_STATUSES = frozenset(item.value for item in InvariantStatus)
_FINDING_CODES = frozenset(item.value for item in Code)

# key -> format name. object and list keys contain further allowlisted keys.
PUBLIC_MANIFEST_KEYS: dict[str, str] = {
    "schema": "schema",
    "register_version": "version",
    "inputs": "object",
    "files": "list",
    "counts": "object",
    "sheets": "list",
    "commercial_parents": "list",
    "invariants": "list",
    "workbook_sha256": "hex",
    "workbook_byte_count": "int",
    "template_sha256": "hex",
    "template_byte_count": "int",
    "category_map_sha256": "hex",
    "category_map_byte_count": "int",
    "policy_sha256": "hex",
    "policy_byte_count": "int",
    "file_name": "file_name",
    "sha256": "hex",
    "record_count": "int",
    "parents_financial": "int",
    "parents_commercial": "int",
    "merged_ranges": "int",
    "merged_instructions": "int",
    "cross_row_instructions": "int",
    "children_active": "int",
    "children_retired": "int",
    "quotes": "int",
    "material_runs": "int",
    "dispositioned_runs": "int",
    "marker_occurrences": "int",
    "marker_dispositions": "int",
    "edge_whitespace_cells": "int",
    "other_cells": "int",
    "joint_answer_children": "int",
    "children_by_derivation": "list",
    "ambiguities_by_topic": "list",
    "warnings_by_code": "list",
    "derivation": "derivation",
    "count": "int",
    "topic_code": "topic",
    "code": "code",
    "sheet": "sheet",
    "question_aggregate_sha256": "hex",
    "parents": "list",
    "public_coordinate": "coordinate",
    "parent_id": "parent_id",
    "active_children": "int",
    "dispositions": "int",
    "fields": "list",
    "field": "field",
    "covered_characters": "int",
    "text_characters": "int",
    "longest_quote_characters": "int",
    "word_count": "int",
    "invariant_id": "invariant",
    "status": "status",
}


def build_public_manifest(register: Register) -> dict[str, object]:
    """Build the public manifest, including invariant status from a validation."""
    from idis.methodology.obligations.validate import validate_register

    report = validate_register(register.root)
    return assemble_public_manifest(register, report)


def build_private_manifest(register: Register) -> dict[str, object]:
    """Build the private manifest. It may carry per-text hashes and is not public-safe."""
    public = build_public_manifest(register)
    private = dict(public)
    private["question_raw_sha256"] = [
        {"parent_id": parent.parent_id, "question_raw_sha256": parent.question_raw_sha256}
        for parent in register.parents
    ]
    return private


def assemble_public_manifest(register: Register, report: ValidationReport) -> dict[str, object]:
    """Assemble a public manifest from a register and a validation report."""
    return {
        "schema": REGISTER_SCHEMA,
        "register_version": REGISTER_VERSION,
        "inputs": _inputs(register),
        "files": _files(register.root),
        "counts": _counts(register, report),
        "sheets": _sheets(register),
        "commercial_parents": _parent_summaries(
            [parent for parent in register.parents if parent.parent_kind is ParentKind.COMMERCIAL],
            register,
        ),
        "invariants": _invariants(report),
    }


def public_manifest_bytes(manifest: dict[str, object]) -> bytes:
    """Canonical UTF-8 bytes of a public manifest, including the final newline."""
    return (canonical_json(manifest) + "\n").encode("utf-8")


def assert_public_safe(manifest: object) -> None:
    """Raise ``GL02_MANIFEST_NOT_PUBLIC_SAFE`` when a key or string is not allowlisted."""
    try:
        _walk(manifest, None)
    except Gl02Error:
        raise
    except (TypeError, ValueError) as exc:
        raise Gl02Error(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value) from exc


def _walk(value: object, key: str | None) -> None:
    if key is not None:
        kind = PUBLIC_MANIFEST_KEYS[key]
        if kind == "object" and not isinstance(value, dict):
            raise Gl02Error(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value)
        if kind == "list" and not isinstance(value, list):
            raise Gl02Error(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value)
        if kind == "int" and type(value) is not int:
            raise Gl02Error(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value)
        if kind not in {"object", "list", "int"} and not isinstance(value, str):
            raise Gl02Error(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value)
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key not in PUBLIC_MANIFEST_KEYS:
                raise Gl02Error(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value)
            _walk(child, child_key)
        return
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                raise Gl02Error(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value)
            _walk(item, None)
        return
    if type(value) is int or isinstance(value, str):
        if isinstance(value, str) and (key is None or not _string_ok(key, value)):
            raise Gl02Error(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value)
        return
    raise Gl02Error(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value)


def _string_ok(key: str, value: str) -> bool:
    kind = PUBLIC_MANIFEST_KEYS.get(key)
    if kind == "schema":
        return value == REGISTER_SCHEMA
    if kind == "version":
        return value == REGISTER_VERSION
    if kind == "hex":
        return _HEX.fullmatch(value) is not None
    if kind == "coordinate":
        return _COORDINATE.fullmatch(value) is not None
    if kind == "parent_id":
        return _PARENT_ID.fullmatch(value) is not None
    if kind == "derivation":
        return value in _DERIVATIONS
    if kind == "field":
        return value in _FIELDS
    if kind == "sheet":
        return value in SHEET_NAMES
    if kind == "file_name":
        return value in _FILE_NAMES
    if kind == "invariant":
        return value in _INVARIANT_IDS
    if kind == "status":
        return value in _STATUSES
    if kind == "topic":
        return value in _TOPICS
    if kind == "code":
        return value in _FINDING_CODES or value in SKIPPED_CHECKS
    return False


def _inputs(register: Register) -> dict[str, object]:
    report = register.extraction_report
    payload: dict[str, object] = {
        "workbook_sha256": report.workbook.sha256,
        "workbook_byte_count": report.workbook.byte_count,
        "template_sha256": report.template.sha256,
        "template_byte_count": report.template.byte_count,
        "category_map_sha256": report.category_map.sha256,
        "category_map_byte_count": report.category_map.byte_count,
    }
    if report.policy is not None:
        payload["policy_sha256"] = report.policy.sha256
        payload["policy_byte_count"] = report.policy.byte_count
    return payload


def _files(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in DERIVED_NAMES:
        path = root / "derived" / name
        if path.is_file():
            rows.append(_file_row(f"derived/{name}", path))
    for kind in (
        "profiles",
        "children",
        "cross_row",
        "dispositions",
        "ambiguities",
        "omission_audit",
    ):
        for shard in Shard:
            path = root / "authored" / kind / f"{shard.value}.jsonl"
            if path.is_file():
                rows.append(_file_row(f"authored/{kind}/{shard.value}.jsonl", path))
    for relative in (
        "authored/role_aliases/CDD.jsonl",
        "authored/baseline/XS.jsonl",
        "authored/views/XS.jsonl",
        "ledger/issued_ids.jsonl",
        "ledger/seals.jsonl",
    ):
        path = root / relative
        if path.is_file():
            rows.append(_file_row(relative, path))
    return rows


def _file_row(name: str, path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    count = (
        1 if name.endswith(".json") else sum(1 for line in raw.decode("utf-8").split("\n") if line)
    )
    return {"file_name": name, "sha256": sha256_bytes(raw), "record_count": count}


def _counts(register: Register, report: ValidationReport) -> dict[str, object]:
    children = [child for _shard, child in register.children]
    active = [child for child in children if child.status is ChildStatus.ACTIVE]
    derivation = Counter(child.derivation.value for child in children)
    topics = Counter(record.topic_code.value for _shard, record in register.ambiguities)
    warnings = Counter(
        finding.code for finding in report.findings if finding.severity.value == "warning"
    )
    material_runs = 0
    dispositioned = 0
    markers = 0
    marker_dispositions = sum(
        1 for _shard, item in register.dispositions if item.kind is DispositionKind.MARKER
    )
    quoted = sum(len(child.source_quotes) for child in children)
    for parent in register.parents:
        for field in account_parent(
            parent,
            tuple(children),
            tuple(item for _shard, item in register.dispositions),
        ):
            material_runs += field.metrics.material_runs
            dispositioned += field.metrics.dispositioned_material_runs
            markers += len(field.markers)
    return {
        "parents_financial": sum(
            parent.parent_kind is ParentKind.FINANCIAL for parent in register.parents
        ),
        "parents_commercial": sum(
            parent.parent_kind is ParentKind.COMMERCIAL for parent in register.parents
        ),
        "merged_ranges": sum(item.count for item in register.extraction_report.merged_range_counts),
        "merged_instructions": len(register.merged_instructions),
        "cross_row_instructions": len(register.cross_rows),
        "children_active": len(active),
        "children_retired": sum(child.status is ChildStatus.RETIRED for child in children),
        "quotes": quoted,
        "material_runs": material_runs,
        "dispositioned_runs": dispositioned,
        "marker_occurrences": markers,
        "marker_dispositions": marker_dispositions,
        "edge_whitespace_cells": len(register.extraction_report.edge_whitespace_cells),
        "other_cells": register.extraction_report.feature_census.other_role_stored_cells,
        "joint_answer_children": sum(1 for child in active if child.joint_answer_reason),
        "children_by_derivation": [
            {"derivation": name, "count": derivation[name]} for name in sorted(derivation)
        ],
        "ambiguities_by_topic": [
            {"topic_code": name, "count": topics[name]} for name in sorted(topics)
        ],
        "warnings_by_code": [{"code": name, "count": warnings[name]} for name in sorted(warnings)],
    }


def _sheets(register: Register) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sheet in SHEET_NAMES:
        parents = [parent for parent in register.parents if parent.sheet == sheet]
        digest = sha256_text(
            "\n".join(sorted(parent.question_raw_sha256 for parent in parents)) + "\n"
        )
        rows.append(
            {
                "sheet": sheet,
                "question_aggregate_sha256": digest,
                "parents": _parent_summaries(parents, register),
            }
        )
    return rows


def _parent_summaries(parents: list[ParentRecord], register: Register) -> list[dict[str, object]]:
    children = tuple(child for _shard, child in register.children)
    dispositions = tuple(item for _shard, item in register.dispositions)
    rows: list[dict[str, object]] = []
    for parent in parents:
        owned = [child for child in children if child.parent_id == parent.parent_id]
        active = [child for child in owned if child.status is ChildStatus.ACTIVE]
        derivation = Counter(child.derivation.value for child in owned)
        fields = account_parent(parent, children, dispositions)
        rows.append(
            {
                "public_coordinate": parent.public_coordinate,
                "parent_id": parent.parent_id,
                "active_children": len(active),
                "children_by_derivation": [
                    {"derivation": name, "count": derivation[name]} for name in sorted(derivation)
                ],
                "material_runs": sum(field.metrics.material_runs for field in fields),
                "dispositions": sum(
                    1 for item in dispositions if item.parent_id == parent.parent_id
                ),
                "fields": [
                    {
                        "field": field.field,
                        "covered_characters": field.metrics.covered_characters,
                        "text_characters": field.metrics.text_characters,
                        "longest_quote_characters": field.metrics.longest_quote_characters,
                        "word_count": field.metrics.word_count,
                        "material_runs": field.metrics.material_runs,
                        "dispositioned_runs": field.metrics.dispositioned_material_runs,
                    }
                    for field in fields
                    if field.metrics.text_characters or field.segments
                ],
            }
        )
    return rows


def _invariants(report: ValidationReport) -> list[dict[str, str]]:
    from idis.methodology.obligations.validate import invariant_status

    return [
        {"invariant_id": invariant_id, "status": invariant_status(invariant_id, report)}
        for invariant_id in (
            "I-01",
            "I-02",
            "I-03",
            "I-04",
            "I-05",
            "I-06",
            "I-06b",
            "I-07",
            "I-08",
            "I-09",
            "I-10",
            "I-11",
            "I-12",
            "I-13",
            "I-14",
            "I-15",
            "I-16",
            "I-17",
            "I-18",
        )
    ]


def _file_names() -> frozenset[str]:
    names = {f"derived/{name}" for name in DERIVED_NAMES}
    for kind in (
        "profiles",
        "children",
        "cross_row",
        "dispositions",
        "ambiguities",
        "omission_audit",
    ):
        for shard in Shard:
            names.add(f"authored/{kind}/{shard.value}.jsonl")
    names.update(
        {
            "authored/role_aliases/CDD.jsonl",
            "authored/baseline/XS.jsonl",
            "authored/views/XS.jsonl",
            "ledger/issued_ids.jsonl",
            "ledger/seals.jsonl",
        }
    )
    return frozenset(names)


_FILE_NAMES = _file_names()
