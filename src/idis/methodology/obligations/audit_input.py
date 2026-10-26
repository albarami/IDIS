"""Omission-audit input. Blind output contains no authored obligation text."""

from __future__ import annotations

from pathlib import Path

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.models_records import ParentRecord
from idis.methodology.obligations.register_io import Register, load_register, parent_shard
from idis.methodology.obligations.review_common import (
    cross_rows_for,
    disposed_text,
    merged_for,
    merged_source,
    ordered_parents,
    path_inside,
    source_coordinate,
)
from idis.methodology.obligations.runs import field_text
from idis.methodology.obligations.vocab import (
    QUOTE_FIELDS,
    ChildStatus,
    Code,
    PlanMinimumCode,
    Shard,
    plan_code_shard,
)


def command_audit_input(
    private_root: Path,
    *,
    stage: str,
    shard: str,
    out_file: Path | None = None,
) -> str:
    """Write one shard's audit input and return counts only."""
    if stage not in {"blind", "compare"}:
        raise Gl02Error(Code.GL02_USAGE.value)
    try:
        selected = Shard(shard)
    except ValueError as exc:
        raise Gl02Error(Code.GL02_USAGE.value) from exc
    root = Path(private_root)
    target = root / "omission_audit_input" / f"{selected.value}-{stage}.md"
    if out_file is not None:
        target = path_inside(root, out_file)
    register = load_register(root)
    text = _document(register, selected, stage)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    parents = ordered_parents(register, (selected,))
    size = target.stat().st_size
    return f"stage={stage} shard={selected.value} parents={len(parents)} bytes={size}\n"


def _document(register: Register, shard: Shard, stage: str) -> str:
    lines = ["# Omission audit input", "", f"stage={stage}", f"shard={shard.value}", ""]
    for parent in ordered_parents(register, (shard,)):
        lines.extend(_parent(register, parent, stage))
    return "\n".join(lines).rstrip() + "\n"


def _parent(register: Register, parent: ParentRecord, stage: str) -> list[str]:
    lines = [
        f"## {parent.public_coordinate}",
        "",
        f"sheet={parent.sheet or ''}",
        f"row_ordinal={parent.row_ordinal}",
        "",
    ]
    for field in QUOTE_FIELDS:
        lines.append(f"{field}:")
        value = field_text(parent, field)
        lines.append("" if value is None else value)
        lines.append("")
    lines.append("merged_instructions:")
    for merged in merged_for(register, parent):
        coordinate, text = merged_source(register, merged)
        lines.append(f"- id={merged.instruction_id} source={coordinate}")
        lines.append(text)
    lines.append("cross_row_instructions:")
    for instruction in cross_rows_for(register, parent):
        source = source_coordinate(register, instruction.source_parent_id)
        targets = ",".join(
            source_coordinate(register, parent_id) for parent_id in instruction.target_parent_ids
        )
        lines.append(
            f"- id={instruction.instruction_id} kind={instruction.kind.value} "
            f"source={source} targets={targets}"
        )
        if stage == "compare":
            lines.append(instruction.source_quote.text)
            if instruction.note:
                lines.append(instruction.note)
    anchors = _anchors(register, parent)
    bindings = _bindings(register, parent)
    lines.append("policy_anchors=" + ",".join(anchors))
    lines.append("policy_bindings=" + ",".join(bindings))
    lines.append("plan_minimum_codes=" + ",".join(_plan_codes(parent)))
    if stage == "compare":
        lines.extend(_authored(register, parent))
    lines.append("")
    return lines


def _authored(register: Register, parent: ParentRecord) -> list[str]:
    lines = ["active_children:"]
    children = [
        child
        for _shard, child in register.children
        if child.parent_id == parent.parent_id and child.status is ChildStatus.ACTIVE
    ]
    for child in sorted(children, key=lambda item: item.child_id):
        lines.append(f"- id={child.child_id} derivation={child.derivation.value}")
        lines.append("  statement:")
        lines.append(child.obligation_statement)
        lines.append("  quotes:")
        for quote in child.source_quotes:
            lines.append(f"  - field={quote.field} text={quote.text}")
    lines.append("dispositions:")
    rows = [item for _shard, item in register.dispositions if item.parent_id == parent.parent_id]
    rows.sort(key=lambda item: (item.kind.value, item.field, item.start, item.end))
    for item in rows:
        lines.append(f"- kind={item.kind.value} field={item.field}")
        lines.append(disposed_text(register, item))
    identifiers = sorted(
        item.ambiguity_id
        for _shard, item in register.ambiguities
        if parent.parent_id in item.affected_parent_ids
    )
    lines.append("ambiguity_ids=" + ",".join(identifiers))
    return lines


def _anchors(register: Register, parent: ParentRecord) -> list[str]:
    for view in register.policy_views:
        if view.parent_id == parent.parent_id:
            return [str(item.formula_ordinal) for item in view.anchors]
    return []


def _bindings(register: Register, parent: ParentRecord) -> list[str]:
    for view in register.policy_views:
        if view.parent_id == parent.parent_id:
            return [f"{item.formula_ordinal}:{item.binding_ordinal}" for item in view.bindings]
    return []


def _plan_codes(parent: ParentRecord) -> list[str]:
    if parent.sheet is None:
        return []
    shard = parent_shard(parent)
    return [code.value for code in PlanMinimumCode if plan_code_shard(code.value) is shard]
