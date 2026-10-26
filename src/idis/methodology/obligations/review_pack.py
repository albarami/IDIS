"""Private review pack. The command prints file names, byte counts and counts."""

from __future__ import annotations

from pathlib import Path

from idis.methodology.obligations.jsonl import tree_hash
from idis.methodology.obligations.ledger import ledger_manifest
from idis.methodology.obligations.models_authored import ChildObligation, ParentProfile
from idis.methodology.obligations.models_records import ParentRecord, ValidationReport
from idis.methodology.obligations.register_io import (
    Register,
    load_register,
    parent_shard,
    resolve_child,
)
from idis.methodology.obligations.review_common import (
    cross_rows_for,
    disposed_text,
    merged_for,
    merged_source,
    ordered_parents,
    path_inside,
    profile_for,
    refuse_hashed_directory,
    shard_selection,
    source_coordinate,
)
from idis.methodology.obligations.review_pack_xlsx import write_shard_workbook
from idis.methodology.obligations.runs import field_text
from idis.methodology.obligations.vocab import QUOTE_FIELDS, ChildStatus, FindingSeverity, Shard


def command_review_pack(
    private_root: Path,
    *,
    out_dir: Path | None = None,
    shards: list[str] | None = None,
) -> str:
    """Write the private review pack and return a count summary."""
    root = Path(private_root)
    target = path_inside(root, root / "review_pack" if out_dir is None else out_dir)
    refuse_hashed_directory(root, target)
    selected = shard_selection(shards)
    register = load_register(root)
    from idis.methodology.obligations.validate import validate_register

    report = validate_register(root)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for shard in selected:
        has_parent = any(parent_shard(parent) is shard for parent in register.parents)
        if not has_parent and not _shard_records(register, shard):
            continue
        markdown = target / shard.value / "obligations.md"
        _write(markdown, _shard_markdown(register, shard))
        workbook = target / f"{shard.value}.xlsx"
        write_shard_workbook(workbook, register, shard)
        written.extend((markdown, workbook))
    readme = target / "README.md"
    _write(readme, _readme(register, report, selected, [readme, *written]))
    written.append(readme)
    return _stdout(target, written, register, selected)


def _shard_markdown(register: Register, shard: Shard) -> str:
    lines = [f"# {shard.value}", ""]
    for parent in ordered_parents(register, (shard,)):
        lines.extend(_parent_block(register, parent))
    return "\n".join(lines).rstrip() + "\n"


def _parent_block(register: Register, parent: ParentRecord) -> list[str]:
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
    lines.extend(_merged_block(register, parent))
    lines.extend(_cross_block(register, parent))
    lines.extend(_profile_block(profile_for(register, parent.parent_id)))
    children = [child for _shard, child in register.children if child.parent_id == parent.parent_id]
    active = sorted(
        (child for child in children if child.status is ChildStatus.ACTIVE),
        key=lambda item: item.child_id,
    )
    retired = sorted(
        (child for child in children if child.status is not ChildStatus.ACTIVE),
        key=lambda item: item.child_id,
    )
    profile = profile_for(register, parent.parent_id)
    lines.append("active_children:")
    for child in active:
        lines.extend(_child_block(register, child, profile))
    lines.append("retired_children:")
    for child in retired:
        lines.append(f"- id={child.child_id} reason={child.retired_reason or ''}")
    lines.extend(_ambiguity_block(register, parent))
    lines.extend(_disposition_block(register, parent))
    lines.append("")
    return lines


def _merged_block(register: Register, parent: ParentRecord) -> list[str]:
    lines = ["merged_instructions:"]
    for item in merged_for(register, parent):
        coordinate, text = merged_source(register, item)
        rows = ",".join(str(row) for row in item.covered_rows)
        lines.append(f"- id={item.instruction_id} source={coordinate} rows={rows}")
        lines.append(text)
    lines.append("")
    return lines


def _cross_block(register: Register, parent: ParentRecord) -> list[str]:
    lines = ["cross_row_instructions:"]
    for item in cross_rows_for(register, parent):
        source = source_coordinate(register, item.source_parent_id)
        targets = ",".join(
            source_coordinate(register, item_id) for item_id in item.target_parent_ids
        )
        lines.append(
            f"- id={item.instruction_id} kind={item.kind.value} source={source} "
            f"field={item.source_field} targets={targets}"
        )
        lines.append(item.source_quote.text)
        if item.note:
            lines.append(item.note)
    lines.append("")
    return lines


def _profile_block(profile: ParentProfile | None) -> list[str]:
    if profile is None:
        return ["profile=absent", ""]
    specialists = profile.default_specialists
    names = (
        () if specialists is None else tuple(item.value for item in specialists.registered_types)
    )
    role = (
        ""
        if specialists is None or specialists.template_role is None
        else specialists.template_role
    )
    destination = profile.default_output_destination
    destination_text = ""
    if destination is not None:
        destination_text = (
            f"{destination.template}:{destination.report_section}:"
            f"{destination.report_subsection or ''}"
        )
    capability = (
        "" if profile.default_capability is None else profile.default_capability.status.value
    )
    analyst = (
        profile.default_analyst_review.required
        if profile.default_analyst_review is not None
        else False
    )
    fund = (
        ""
        if profile.default_fund_feedback_status is None
        else profile.default_fund_feedback_status.value
    )
    return [
        "profile:",
        f"specialists={','.join(names)}",
        f"template_role={role}",
        f"destination={destination_text}",
        f"capability={capability}",
        f"analyst_review_required={int(analyst)}",
        f"fund_feedback={fund}",
        f"summary={profile.parent_summary_note or ''}",
        "",
    ]


def _child_block(
    register: Register, child: ChildObligation, profile: ParentProfile | None
) -> list[str]:
    resolved = resolve_child(child, profile)
    specialist = resolved.responsible_specialist
    names = () if specialist is None else tuple(item.value for item in specialist.registered_types)
    destination = resolved.output_destination
    destination_text = ""
    if destination is not None:
        destination_text = (
            f"{destination.template}:{destination.report_section}:"
            f"{destination.report_subsection or ''}"
        )
    evidence_types = ",".join(item.evidence_type.value for item in child.acceptable_evidence)
    categories = ",".join(
        category for item in child.acceptable_evidence for category in item.document_categories
    )
    independence = ",".join(item.independence.value for item in child.acceptable_evidence)
    lines = [
        f"- id={child.child_id} derivation={child.derivation.value}",
        f"  specialists={','.join(names)}",
        f"  independence={independence}",
        f"  destination={destination_text}",
        f"  ambiguities={','.join(child.ambiguity_ids)}",
        f"  cross_row={','.join(child.cross_row_instruction_ids)}",
    ]
    if child.derivation_rationale:
        lines.append(f"  rationale={child.derivation_rationale}")
    lines.append("  statement:")
    lines.append(child.obligation_statement)
    lines.append("  quotes:")
    for quote in child.source_quotes:
        cited = quote.parent_id or child.parent_id
        lines.append(
            f"  - field={quote.field} parent={source_coordinate(register, cited)} text={quote.text}"
        )
    lines.append("  evidence_types=" + evidence_types)
    lines.append("  document_categories=" + categories)
    lines.append("  calculation_rules:")
    for rule in child.calculation_rules:
        lines.append(
            f"  - kind={rule.kind.value} code={rule.rule_code or ''} "
            f"formula_ordinal={_num(rule.formula_ordinal)} "
            f"binding_ordinal={_num(rule.binding_ordinal)} note={rule.note or ''}"
        )
    lines.append("  acceptance_case:")
    lines.append(child.acceptance_case)
    return lines


def _ambiguity_block(register: Register, parent: ParentRecord) -> list[str]:
    lines = ["ambiguities:"]
    rows = [
        item
        for _shard, item in register.ambiguities
        if parent.parent_id in item.affected_parent_ids
    ]
    for item in sorted(rows, key=lambda record: record.ambiguity_id):
        lines.append(
            f"- id={item.ambiguity_id} topic={item.topic_code.value} "
            f"owner={item.decision_owner.value} status={item.status.value}"
        )
        lines.append(item.description)
    return lines


def _disposition_block(register: Register, parent: ParentRecord) -> list[str]:
    lines = ["dispositions:"]
    rows = [item for _shard, item in register.dispositions if item.parent_id == parent.parent_id]
    for kind in sorted({item.kind.value for item in rows}):
        lines.append(f"kind={kind}")
        chosen = [item for item in rows if item.kind.value == kind]
        chosen.sort(key=lambda item: (item.field, item.start, item.end, item.disposition))
        for item in chosen:
            lines.append(
                f"- field={item.field} disposition={item.disposition} "
                f"start={item.start} end={item.end}"
            )
            lines.append(disposed_text(register, item))
            if item.note:
                lines.append(item.note)
    return lines


def _readme(
    register: Register,
    report: ValidationReport,
    shards: tuple[Shard, ...],
    files: list[Path],
) -> str:
    summary = ledger_manifest(register)
    current = tree_hash(register.root)
    sealed = summary["sealed"] is True
    last_hash = summary["last_tree_hash"]
    matches = isinstance(last_hash, str) and last_hash == current
    lines = ["# Review pack", "", "counts:"]
    for shard in shards:
        lines.append(_count_line(register, shard))
    lines.extend(["", "seal:"])
    if sealed and isinstance(last_hash, str):
        lines.append(f"tree_hash={last_hash}")
        lines.append(f"sequence={summary['last_seal_seq']}")
    else:
        lines.append("tree_hash=not sealed")
        lines.append("sequence=not sealed")
    lines.append(f"tree_matches_seal={'yes' if matches else 'no'}")
    lines.extend(["", "errors:"])
    errors: dict[str, int] = {}
    for finding in report.findings:
        if finding.severity is FindingSeverity.ERROR:
            errors[finding.code] = errors.get(finding.code, 0) + 1
    for code in sorted(errors):
        lines.append(f"{code}={errors[code]}")
    lines.extend(["", "skipped:"])
    lines.extend(report.skipped_checks)
    lines.extend(["", "files:"])
    root = files[-1].parent if files else register.root
    names = sorted(path.relative_to(root).as_posix() for path in files)
    lines.extend(names)
    lines.extend(
        [
            "",
            "reading:",
            "Read the counts, then one shard file in sheet and row order.",
            "Each parent repeats the stored source wording, the children,",
            "and the accounting decisions.",
            "The workbook repeats those rows for filtering.",
            "This directory is private.",
            "",
        ]
    )
    return "\n".join(lines)


def _count_line(register: Register, shard: Shard) -> str:
    parents = [parent for parent in register.parents if parent_shard(parent) is shard]
    parent_ids = {parent.parent_id for parent in parents}
    children = [child for _file, child in register.children if child.parent_id in parent_ids]
    active = sum(child.status is ChildStatus.ACTIVE for child in children)
    ambiguities = sum(1 for file_shard, _item in register.ambiguities if file_shard is shard)
    dispositions = sum(1 for file_shard, _item in register.dispositions if file_shard is shard)
    cross_rows = sum(1 for file_shard, _item in register.cross_rows if file_shard is shard)
    return (
        f"shard={shard.value} parents={len(parents)} active_children={active} "
        f"retired_children={len(children) - active} ambiguities={ambiguities} "
        f"dispositions={dispositions} cross_row_instructions={cross_rows}"
    )


def _stdout(root: Path, files: list[Path], register: Register, shards: tuple[Shard, ...]) -> str:
    lines = []
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        lines.append(f"{path.relative_to(root).as_posix()} bytes={path.stat().st_size}")
    parents = sum(1 for parent in register.parents if parent_shard(parent) in set(shards))
    children = 0
    for parent in register.parents:
        if parent_shard(parent) not in set(shards):
            continue
        children += sum(
            1 for _shard, child in register.children if child.parent_id == parent.parent_id
        )
    lines.append(f"parents={parents} children={children} files={len(files)}")
    lines.append("")
    return "\n".join(lines)


def _shard_records(register: Register, shard: Shard) -> bool:
    return any(
        file_shard is shard
        for file_shard, _item in (
            *register.ambiguities,
            *register.dispositions,
            *register.cross_rows,
            *register.children,
        )
    )


def _num(value: int | None) -> str:
    if value is None:
        return ""
    return str(value)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
