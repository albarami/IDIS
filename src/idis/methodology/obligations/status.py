"""Per-shard counts for the status command. The command never writes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.models_records import Finding, ParentRecord, ValidationReport
from idis.methodology.obligations.register_io import Register, parent_shard
from idis.methodology.obligations.runs import account_parent
from idis.methodology.obligations.vocab import (
    QUOTE_FIELDS,
    SHARD_ORDER,
    ChildStatus,
    Code,
    DispositionKind,
    FindingSeverity,
    Shard,
)

_WORKBOOK_SCOPES = frozenset({"workbook", "parents", "global", "manifest", "policy"})
_FINANCIAL_SHARDS = frozenset({Shard.PL, Shard.CF, Shard.LIAB, Shard.ASSET})


@dataclass(frozen=True, slots=True)
class ShardCounts:
    """Counts for one shard. Maps are sorted pairs so text and JSON agree."""

    shard: str
    parents: int
    parents_with_active_child: int
    active_children: int
    children_by_derivation: tuple[tuple[str, int], ...]
    quotes: int
    material_runs: int
    undispositioned_runs: int
    uncovered_dispositioned_runs: int
    unfocused_covered_segments: int
    marker_occurrences: int
    unaccounted_markers: int
    dispositions_by_kind: tuple[tuple[str, int], ...]
    ambiguities: int
    cross_row_instructions: int
    errors_by_code: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, object]:
        """JSON object for one shard. Source text is not included."""
        return {
            "active_children": self.active_children,
            "ambiguities": self.ambiguities,
            "children_by_derivation": dict(self.children_by_derivation),
            "cross_row_instructions": self.cross_row_instructions,
            "dispositions_by_kind": dict(self.dispositions_by_kind),
            "errors_by_code": dict(self.errors_by_code),
            "marker_occurrences": self.marker_occurrences,
            "material_runs": self.material_runs,
            "parents": self.parents,
            "parents_with_active_child": self.parents_with_active_child,
            "quotes": self.quotes,
            "shard": self.shard,
            "unaccounted_markers": self.unaccounted_markers,
            "uncovered_dispositioned_runs": self.uncovered_dispositioned_runs,
            "undispositioned_runs": self.undispositioned_runs,
            "unfocused_covered_segments": self.unfocused_covered_segments,
        }


def selected_shards(names: list[str] | None) -> tuple[Shard, ...]:
    """Return the requested shards in shard order, or every shard."""
    if not names:
        return SHARD_ORDER
    try:
        requested = {Shard(name) for name in names}
    except ValueError as exc:
        raise Gl02Error(Code.GL02_USAGE.value) from exc
    return tuple(shard for shard in SHARD_ORDER if shard in requested)


def shard_counts(
    register: Register,
    report: ValidationReport,
    shards: tuple[Shard, ...],
) -> tuple[ShardCounts, ...]:
    """Count one row per shard. Workbook-level errors are repeated on each row."""
    errors = _errors_by_shard(register, report)
    return tuple(_one_shard(register, shard, errors[shard]) for shard in shards)


def status_payload(rows: tuple[ShardCounts, ...], ledger: dict[str, object]) -> dict[str, object]:
    """JSON object for the status command."""
    return {"ledger": ledger, "shards": [row.as_dict() for row in rows]}


def render_status_text(rows: tuple[ShardCounts, ...], ledger: dict[str, object]) -> str:
    """One line per shard, with no source text."""
    suffix = _ledger_suffix(ledger)
    lines = [f"{_line(row)} {suffix}" for row in rows]
    lines.append("")
    return "\n".join(lines)


def ledger_status(register: Register) -> dict[str, object]:
    """Sealed flag, last sequence, and whether the current tree matches the seal."""
    from idis.methodology.obligations.jsonl import tree_hash
    from idis.methodology.obligations.ledger import ledger_manifest

    summary = ledger_manifest(register)
    last_hash = summary["last_tree_hash"]
    matches = isinstance(last_hash, str) and last_hash == tree_hash(register.root)
    return {
        "last_seal_seq": summary["last_seal_seq"],
        "sealed": summary["sealed"],
        "tree_matches_seal": matches,
    }


def _ledger_suffix(ledger: dict[str, object]) -> str:
    sealed = 1 if ledger["sealed"] is True else 0
    matches = 1 if ledger["tree_matches_seal"] is True else 0
    sequence = ledger["last_seal_seq"]
    shown = "none" if sequence is None else str(sequence)
    return f"sealed={sealed} last_seal_seq={shown} tree_matches_seal={matches}"


def _one_shard(register: Register, shard: Shard, errors: Counter[str]) -> ShardCounts:
    parents = [parent for parent in register.parents if parent_shard(parent) is shard]
    children = [child for file_shard, child in register.children if file_shard is shard]
    active = [child for child in children if child.status is ChildStatus.ACTIVE]
    derivation = Counter(child.derivation.value for child in children)
    active_ids = {child.parent_id for child in active}
    accounted = _field_counts(register, parents)
    dispositions = Counter(
        item.disposition for file_shard, item in register.dispositions if file_shard is shard
    )
    return ShardCounts(
        shard=shard.value,
        parents=len(parents),
        parents_with_active_child=sum(parent.parent_id in active_ids for parent in parents),
        active_children=len(active),
        children_by_derivation=_pairs(derivation),
        quotes=sum(len(child.source_quotes) for child in children),
        material_runs=accounted[0],
        undispositioned_runs=accounted[1],
        uncovered_dispositioned_runs=accounted[2],
        unfocused_covered_segments=accounted[3],
        marker_occurrences=accounted[4],
        unaccounted_markers=accounted[5],
        dispositions_by_kind=_pairs(dispositions),
        ambiguities=sum(1 for file_shard, _item in register.ambiguities if file_shard is shard),
        cross_row_instructions=sum(
            1 for file_shard, _item in register.cross_rows if file_shard is shard
        ),
        errors_by_code=_pairs(errors),
    )


def _field_counts(
    register: Register, parents: list[ParentRecord]
) -> tuple[int, int, int, int, int, int]:
    children = tuple(child for _shard, child in register.children)
    dispositions = tuple(item for _shard, item in register.dispositions)
    material = 0
    undispositioned = 0
    dispositioned = 0
    unfocused = 0
    markers = 0
    unaccounted = 0
    for parent in parents:
        for field in account_parent(parent, children, dispositions):
            keys = {
                (item.start, item.end, item.text_sha256)
                for item in dispositions
                if item.kind is DispositionKind.RUN
                and item.parent_id == parent.parent_id
                and item.field == field.field
            }
            for run in field.runs:
                if not run.material:
                    continue
                material += 1
                if (run.start, run.end, run.text_sha256) in keys:
                    dispositioned += 1
                else:
                    undispositioned += 1
            markers += len(field.markers)
            for finding in field.findings:
                if finding.code == Code.GL02_SEGMENT_NOT_FOCUSED.value:
                    unfocused += 1
                elif finding.code == Code.GL02_MARKER_UNACCOUNTED.value:
                    unaccounted += 1
    return material, undispositioned, dispositioned, unfocused, markers, unaccounted


def _errors_by_shard(register: Register, report: ValidationReport) -> dict[Shard, Counter[str]]:
    owners = _owners(register)
    counts = {shard: Counter[str]() for shard in SHARD_ORDER}
    for finding in report.findings:
        if finding.severity is not FindingSeverity.ERROR:
            continue
        for shard in _finding_shards(finding, owners):
            counts[shard][finding.code] += 1
    return counts


def _owners(register: Register) -> dict[str, Shard]:
    owners: dict[str, Shard] = {}
    for parent in register.parents:
        shard = parent_shard(parent)
        owners[parent.parent_id] = shard
        owners[parent.public_coordinate] = shard
    for _file_shard, child in register.children:
        owner = owners.get(child.parent_id)
        if owner is not None:
            owners[child.child_id] = owner
    return owners


def _finding_shards(finding: Finding, owners: dict[str, Shard]) -> frozenset[Shard]:
    found: set[Shard] = set()
    for value in (finding.scope, *finding.ids):
        if _is_span(value):
            continue
        if value in {shard.value for shard in Shard}:
            found.add(Shard(value))
            continue
        shard = owners.get(value)
        if shard is not None:
            found.add(shard)
    if found:
        return frozenset(found)
    if finding.scope == "commercial":
        return frozenset({Shard.CDD})
    if finding.scope == "financial":
        return _FINANCIAL_SHARDS
    if finding.scope in _WORKBOOK_SCOPES:
        return frozenset(SHARD_ORDER)
    return frozenset()


def _is_span(value: str) -> bool:
    field, separator, rest = value.partition(":")
    if separator != ":" or field not in QUOTE_FIELDS:
        return False
    start, marker, end = rest.partition(":")
    return marker == ":" and start.isdigit() and end.isdigit() and ":" not in end


def _pairs(counts: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple((name, counts[name]) for name in sorted(counts))


def _line(row: ShardCounts) -> str:
    return " ".join(
        (
            f"shard={row.shard}",
            f"parents={row.parents}",
            f"parents_with_active_child={row.parents_with_active_child}",
            f"active_children={row.active_children}",
            f"children_by_derivation={_fmt(row.children_by_derivation)}",
            f"quotes={row.quotes}",
            f"material_runs={row.material_runs}",
            f"undispositioned_runs={row.undispositioned_runs}",
            f"uncovered_dispositioned_runs={row.uncovered_dispositioned_runs}",
            f"unfocused_covered_segments={row.unfocused_covered_segments}",
            f"marker_occurrences={row.marker_occurrences}",
            f"unaccounted_markers={row.unaccounted_markers}",
            f"dispositions_by_kind={_fmt(row.dispositions_by_kind)}",
            f"ambiguities={row.ambiguities}",
            f"cross_row_instructions={row.cross_row_instructions}",
            f"errors_by_code={_fmt(row.errors_by_code)}",
        )
    )


def _fmt(pairs: tuple[tuple[str, int], ...]) -> str:
    return ",".join(f"{name}:{count}" for name, count in pairs)
