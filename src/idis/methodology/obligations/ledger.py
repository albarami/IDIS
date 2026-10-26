"""Ledger, seal and rule I-16. The caller passes ``now``; the library reads no clock."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.findings import make_finding
from idis.methodology.obligations.jsonl import read_jsonl, tree_hash
from idis.methodology.obligations.ledger_store import commit_ledger, ledger_lines, write_snapshot
from idis.methodology.obligations.models_authored import (
    ChildObligation,
    LedgerRecord,
    SealCounts,
    SealRecord,
)
from idis.methodology.obligations.models_records import Finding, ParentRecord, ValidationReport
from idis.methodology.obligations.register_io import Register, load_register, parent_shard
from idis.methodology.obligations.vocab import (
    SHARD_ORDER,
    SKIPPED_LEDGER_ABSENT,
    ChildStatus,
    Code,
    FindingSeverity,
    Shard,
)

_NOW = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_I16_ERROR_CODES = frozenset(
    {
        Code.GL02_LEDGER_FILE_INVALID.value,
        Code.GL02_LEDGER_ID_MISSING.value,
        Code.GL02_LEDGER_ID_MOVED.value,
        Code.GL02_LEDGER_INTEGRITY.value,
    }
)
_SHARD_NAMES = frozenset(shard.value for shard in Shard)


def ledger_manifest(register: Register) -> dict[str, object]:
    """Public ``ledger`` object. Hashes and counts only."""
    if register.ledger_invalid or not register.seals:
        return {
            "issued_ids": 0 if register.ledger_invalid else len(register.ledger),
            "last_seal_seq": None,
            "last_tree_hash": None,
            "seal_count": 0,
            "sealed": False,
        }
    last = max(register.seals, key=lambda item: item.seq)
    return {
        "issued_ids": len(register.ledger),
        "last_seal_seq": last.seq,
        "last_tree_hash": last.tree_hash,
        "seal_count": len(register.seals),
        "sealed": True,
    }


def sealed_version(private_root: Path | str) -> str | None:
    """Return the last seal's tree hash, or None when no seal can be read."""
    path = Path(private_root) / "ledger" / "seals.jsonl"
    if not path.is_file():
        return None
    try:
        seals = read_jsonl(path, SealRecord)
    except Gl02Error:
        return None
    if not seals:
        return None
    return max(seals, key=lambda item: item.seq).tree_hash


def check_ledger(register: Register, limit: frozenset[str] | None) -> tuple[list[Finding], bool]:
    """I-16. Return findings and whether the check was skipped.

    A missing ledger is skipped. A ledgered id must still exist under the same
    parent. Shard-scoped validation checks the ids of the parents in scope.
    """
    if register.ledger_invalid:
        return [make_finding(Code.GL02_LEDGER_FILE_INVALID.value, scope="ledger")], False
    issued = register.root / "ledger" / "issued_ids.jsonl"
    seals = register.root / "ledger" / "seals.jsonl"
    if not issued.is_file() and not seals.is_file():
        return [], True
    if not issued.is_file() or not seals.is_file():
        return [make_finding(Code.GL02_LEDGER_FILE_INVALID.value, scope="ledger")], False
    findings = _structure(register)
    findings.extend(_identities(register, limit))
    findings.extend(_tree_changed(register))
    return findings, False


def seal_block_reason(
    report: ValidationReport,
    *,
    shard_names: list[str] | None,
    owners: dict[str, Shard],
) -> str | None:
    """Return the one-line refusal reason, or None when a seal may proceed.

    A full seal refuses any error, any cross-shard pending item, and any skip
    other than the absent ledger. A shard seal refuses an error owned by a
    selected shard, an error that names no shard, and every I-16 error. An
    error whose owners are all outside the selection is ignored.
    """
    selected = None if not shard_names else frozenset(Shard(name) for name in shard_names)
    for finding in report.findings:
        if finding.severity is not FindingSeverity.ERROR:
            continue
        if selected is None or _blocks_shard(finding, owners, selected):
            return finding.message
    if selected is not None:
        return None
    for finding in report.findings:
        if finding.code == Code.PENDING_CROSS_SHARD.value:
            return finding.message
    for skip in report.skipped_checks:
        if skip != SKIPPED_LEDGER_ABSENT:
            return f"skipped={skip}"
    return None


def command_seal(
    private_root: Path,
    *,
    workbook: Path | None = None,
    policy: Path | None = None,
    shards: list[str] | None = None,
    snapshot_root: Path | None = None,
    now: str,
) -> tuple[int, str]:
    """Seal a private root. Return the exit code and the one-line result."""
    if _NOW.fullmatch(now) is None:
        raise Gl02Error(Code.GL02_USAGE.value)
    root = Path(private_root)
    selected = _selected_shards(shards)
    snapshots = _snapshot_root(root, snapshot_root)
    from idis.methodology.obligations.validate import validate_register

    report = validate_register(
        root,
        workbook_path=workbook,
        policy_path=policy,
        shards=None if selected is None else [item.value for item in selected],
    )
    register = load_register(root)
    reason = seal_block_reason(
        report,
        shard_names=None if selected is None else [item.value for item in selected],
        owners=_owners(register),
    )
    if reason is not None:
        return 1, reason + "\n"
    current = tree_hash(root)
    scope = _scope_value(selected)
    last = _last_seal(register)
    refusal = _ledger_write_refusal(register)
    if refusal is not None:
        return 1, refusal
    if last is not None and last.tree_hash == current and _same_scope(last, scope):
        return 0, f"UNCHANGED seq={last.seq}\n"
    return _write_seal(register, report, current, scope, snapshots, now)


def _write_seal(
    register: Register,
    report: ValidationReport,
    current: str,
    scope: str | tuple[str, ...],
    snapshots: Path,
    now: str,
) -> tuple[int, str]:
    refusal = _ledger_write_refusal(register)
    if refusal is not None:
        return 1, refusal
    sequence = 1 if not register.seals else max(item.seq for item in register.seals) + 1
    parents = _scope_parents(register, scope)
    parent_ids = {parent.parent_id for parent in parents}
    children = [child for _shard, child in register.children if child.parent_id in parent_ids]
    known = {item.child_id for item in register.ledger}
    fresh = sorted(
        (child for child in children if child.child_id not in known),
        key=lambda item: item.child_id,
    )
    records = [
        LedgerRecord(
            child_id=child.child_id,
            parent_id=child.parent_id,
            public_coordinate=register.parents_by_id[child.parent_id].public_coordinate,
            first_seal=sequence,
        )
        for child in fresh
    ]
    counts = _counts(register, parents, children, len(records))
    name = f"{now.replace('-', '').replace(':', '')}-{current[:12]}"
    destination = snapshots / name
    snapshots.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise Gl02Error(Code.GL02_IO.value)
    seal = SealRecord(
        seq=sequence,
        tree_hash=current,
        created_utc=now,
        scope=scope,
        counts=counts,
        snapshot=name,
        workbook_sha256=register.extraction_report.workbook.sha256,
        register_complete=report.register_complete,
    )
    issued_path = register.root / "ledger" / "issued_ids.jsonl"
    seals_path = register.root / "ledger" / "seals.jsonl"
    old_issued = issued_path.read_bytes() if issued_path.is_file() else b""
    old_seals = seals_path.read_bytes() if seals_path.is_file() else b""
    new_issued = old_issued + ledger_lines(records)
    new_seals = old_seals + ledger_lines((seal,))
    partial = snapshots / f".{name}.partial"
    placed = False
    try:
        write_snapshot(register.root, partial, new_issued, old_seals)
        os.replace(partial, destination)
        placed = True
        commit_ledger(issued_path, seals_path, old_issued, new_issued, new_seals)
    except OSError:
        shutil.rmtree(partial, ignore_errors=True)
        if placed:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return 0, _success_line(sequence, counts, current, name)


def _ledger_write_refusal(register: Register) -> str | None:
    """Refuse a seal that would append onto an invalid or inconsistent ledger."""
    if register.ledger_invalid:
        return make_finding(Code.GL02_LEDGER_FILE_INVALID.value, scope="ledger").message + "\n"
    findings = _structure(register)
    if not findings:
        return None
    return findings[0].message + "\n"


def _success_line(sequence: int, counts: SealCounts, current: str, name: str) -> str:
    return (
        f"seq={sequence} parents={counts.parents} children={counts.children} "
        f"active_children={counts.active_children} retired_children={counts.retired_children} "
        f"new_ids={counts.new_ids} ambiguities={counts.ambiguities} "
        f"cross_row_instructions={counts.cross_row_instructions} "
        f"dispositions={counts.dispositions} tree_hash={current} snapshot={name}\n"
    )


def _counts(
    register: Register,
    parents: list[ParentRecord],
    children: list[ChildObligation],
    new_ids: int,
) -> SealCounts:
    parent_ids = {parent.parent_id for parent in parents}
    active = sum(child.status is ChildStatus.ACTIVE for child in children)
    ambiguities = sum(
        1
        for _shard, item in register.ambiguities
        if any(parent_id in parent_ids for parent_id in item.affected_parent_ids)
    )
    cross_rows = sum(
        1
        for _shard, item in register.cross_rows
        if item.source_parent_id in parent_ids
        or any(parent_id in parent_ids for parent_id in item.target_parent_ids)
    )
    dispositions = sum(1 for _shard, item in register.dispositions if item.parent_id in parent_ids)
    return SealCounts(
        parents=len(parents),
        children=len(children),
        active_children=active,
        retired_children=len(children) - active,
        new_ids=new_ids,
        ambiguities=ambiguities,
        cross_row_instructions=cross_rows,
        dispositions=dispositions,
    )


def _structure(register: Register) -> list[Finding]:
    findings: list[Finding] = []
    sequences = [item.seq for item in register.seals]
    if sequences != list(range(1, len(sequences) + 1)):
        findings.append(
            make_finding(Code.GL02_LEDGER_INTEGRITY.value, scope="ledger", ids=("seq",))
        )
    known = set(sequences)
    seen: set[str] = set()
    grouped: list[int] = []
    buckets: dict[int, list[str]] = {}
    for record in register.ledger:
        if record.first_seal not in known:
            findings.append(
                make_finding(
                    Code.GL02_LEDGER_INTEGRITY.value,
                    scope="ledger",
                    ids=(record.child_id, str(record.first_seal)),
                )
            )
        if record.child_id in seen:
            findings.append(
                make_finding(
                    Code.GL02_LEDGER_INTEGRITY.value, scope="ledger", ids=(record.child_id,)
                )
            )
        seen.add(record.child_id)
        if grouped and grouped[-1] == record.first_seal:
            buckets[record.first_seal].append(record.child_id)
        elif record.first_seal in buckets:
            findings.append(
                make_finding(
                    Code.GL02_LEDGER_INTEGRITY.value,
                    scope="ledger",
                    ids=(str(record.first_seal),),
                )
            )
        else:
            grouped.append(record.first_seal)
            buckets[record.first_seal] = [record.child_id]
    for seal, identifiers in buckets.items():
        if identifiers != sorted(identifiers):
            findings.append(
                make_finding(Code.GL02_LEDGER_INTEGRITY.value, scope="ledger", ids=(str(seal),))
            )
    for seal_record in register.seals:
        hash_ok = _HEX64.fullmatch(seal_record.tree_hash) is not None
        time_ok = _NOW.fullmatch(seal_record.created_utc) is not None
        if hash_ok and time_ok:
            continue
        findings.append(
            make_finding(
                Code.GL02_LEDGER_FILE_INVALID.value,
                scope="ledger",
                ids=(str(seal_record.seq),),
            )
        )
    findings.extend(_seal_id_counts(register))
    return findings


def _seal_id_counts(register: Register) -> list[Finding]:
    """The ids of ``first_seal`` k must number seal k's ``counts.new_ids``."""
    tallies: dict[int, int] = {}
    for record in register.ledger:
        tallies[record.first_seal] = tallies.get(record.first_seal, 0) + 1
    findings: list[Finding] = []
    for seal_record in register.seals:
        if tallies.get(seal_record.seq, 0) == seal_record.counts.new_ids:
            continue
        findings.append(
            make_finding(
                Code.GL02_LEDGER_FILE_INVALID.value,
                scope="ledger",
                ids=(str(seal_record.seq),),
            )
        )
    return findings


def _identities(register: Register, limit: frozenset[str] | None) -> list[Finding]:
    by_id: dict[str, list[str]] = {}
    for _shard, child in register.children:
        by_id.setdefault(child.child_id, []).append(child.parent_id)
    findings: list[Finding] = []
    for record in register.ledger:
        if limit is not None and record.parent_id not in limit:
            continue
        parents = by_id.get(record.child_id, [])
        if not parents:
            findings.append(
                make_finding(
                    Code.GL02_LEDGER_ID_MISSING.value,
                    scope="ledger",
                    ids=(record.child_id, record.parent_id),
                )
            )
            continue
        if len(parents) > 1:
            findings.append(
                make_finding(
                    Code.GL02_LEDGER_INTEGRITY.value, scope="ledger", ids=(record.child_id,)
                )
            )
            continue
        if parents[0] != record.parent_id:
            findings.append(
                make_finding(
                    Code.GL02_LEDGER_ID_MOVED.value,
                    scope="ledger",
                    ids=(record.child_id, record.parent_id),
                )
            )
    return findings


def _tree_changed(register: Register) -> list[Finding]:
    if not register.seals:
        return []
    last = max(register.seals, key=lambda item: item.seq)
    if tree_hash(register.root) == last.tree_hash:
        return []
    return [
        make_finding(
            Code.GL02_LEDGER_TREE_CHANGED.value,
            severity=FindingSeverity.INFO,
            scope="ledger",
            ids=(str(last.seq),),
        )
    ]


def _scope_parents(register: Register, scope: str | tuple[str, ...]) -> list[ParentRecord]:
    if scope == "full":
        return list(register.parents)
    selected = {Shard(item) for item in scope}
    return [parent for parent in register.parents if parent_shard(parent) in selected]


def _scope_value(selected: tuple[Shard, ...] | None) -> str | tuple[str, ...]:
    if selected is None:
        return "full"
    return tuple(shard.value for shard in selected)


def _same_scope(record: SealRecord, scope: str | tuple[str, ...]) -> bool:
    if isinstance(scope, str):
        return record.scope == scope
    return tuple(record.scope) == scope


def _last_seal(register: Register) -> SealRecord | None:
    if register.ledger_invalid or not register.seals:
        return None
    return max(register.seals, key=lambda item: item.seq)


def _selected_shards(names: list[str] | None) -> tuple[Shard, ...] | None:
    if not names:
        return None
    try:
        requested = {Shard(name) for name in names}
    except ValueError as exc:
        raise Gl02Error(Code.GL02_USAGE.value) from exc
    return tuple(shard for shard in SHARD_ORDER if shard in requested)


def _snapshot_root(root: Path, requested: Path | None) -> Path:
    """Return the snapshot directory. It is created only when a seal writes."""
    base = root.parent / f"{root.name}-snapshots" if requested is None else Path(requested)
    resolved_root = root.resolve()
    resolved = base.resolve()
    if resolved == resolved_root or resolved_root in resolved.parents:
        raise Gl02Error(Code.GL02_USAGE.value)
    return base


def _owners(register: Register) -> dict[str, Shard]:
    owners: dict[str, Shard] = {}
    for parent in register.parents:
        shard = parent_shard(parent)
        owners[parent.parent_id] = shard
        owners[parent.public_coordinate] = shard
    for _shard, child in register.children:
        owner = owners.get(child.parent_id)
        if owner is not None:
            owners[child.child_id] = owner
    return owners


def _blocks_shard(finding: Finding, owners: dict[str, Shard], selected: frozenset[Shard]) -> bool:
    """True when a shard seal must refuse this error."""
    if finding.code in _I16_ERROR_CODES:
        return True
    named = _named_shards(finding, owners)
    if not named:
        return True
    return bool(named & selected)


def _named_shards(finding: Finding, owners: dict[str, Shard]) -> set[Shard]:
    named: set[Shard] = set()
    for value in (finding.scope, *finding.ids):
        if value in _SHARD_NAMES:
            named.add(Shard(value))
        elif value in owners:
            named.add(owners[value])
    return named
