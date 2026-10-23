"""Shared readers for the private review pack and the audit-input export."""

from __future__ import annotations

from pathlib import Path

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.models_authored import (
    CrossRowInstruction,
    Disposition,
    ParentProfile,
)
from idis.methodology.obligations.models_records import MergedInstruction, ParentRecord
from idis.methodology.obligations.register_io import Register, parent_shard
from idis.methodology.obligations.runs import field_text
from idis.methodology.obligations.status import selected_shards
from idis.methodology.obligations.vocab import QUOTE_FIELDS, Code, Shard

_HASHED_DIRECTORIES = ("authored", "derived", "ledger")


def refuse_hashed_directory(private_root: Path, path: Path) -> None:
    """Refuse a review-pack directory inside ``authored``, ``derived`` or ``ledger``."""
    resolved = path.resolve()
    root = private_root.resolve()
    for name in _HASHED_DIRECTORIES:
        forbidden = (root / name).resolve()
        if resolved == forbidden or forbidden in resolved.parents:
            raise Gl02Error(Code.GL02_USAGE.value)


def path_inside(private_root: Path, path: Path) -> Path:
    """Resolve ``path`` and refuse it unless it is strictly inside ``private_root``."""
    root = private_root.resolve()
    target = path.resolve()
    if target == root or root not in target.parents:
        raise Gl02Error(Code.GL02_USAGE.value)
    return target


def ordered_parents(register: Register, shards: tuple[Shard, ...]) -> list[ParentRecord]:
    """Parents of the named shards in sheet and row order."""
    selected = set(shards)
    parents = [parent for parent in register.parents if parent_shard(parent) in selected]
    return sorted(parents, key=_parent_key)


def shard_selection(names: list[str] | None) -> tuple[Shard, ...]:
    """Shards in canonical order. An unknown code is a usage error."""
    return selected_shards(names)


def merged_for(register: Register, parent: ParentRecord) -> list[MergedInstruction]:
    """Merged instructions that cover this parent, in instruction-id order."""
    rows = [
        item for item in register.merged_instructions if parent.parent_id in item.covered_parent_ids
    ]
    return sorted(rows, key=lambda item: item.instruction_id)


def cross_rows_for(register: Register, parent: ParentRecord) -> list[CrossRowInstruction]:
    """Cross-row instructions whose source or target is this parent."""
    rows = [
        item
        for _shard, item in register.cross_rows
        if item.source_parent_id == parent.parent_id or parent.parent_id in item.target_parent_ids
    ]
    return sorted(rows, key=lambda item: item.instruction_id)


def merged_source(register: Register, instruction: MergedInstruction) -> tuple[str, str]:
    """Coordinate and verbatim question text of a merged instruction's anchor row."""
    covered = [register.parent(parent_id) for parent_id in instruction.covered_parent_ids]
    parents = [parent for parent in covered if parent is not None]
    if not parents:
        return "", ""
    anchor = min(parents, key=lambda item: (item.source_row or 0, item.public_coordinate))
    return anchor.public_coordinate, anchor.question_raw


def source_coordinate(register: Register, parent_id: str) -> str:
    """Public coordinate for a parent id, or the id when the parent is absent."""
    parent = register.parent(parent_id)
    if parent is None:
        return parent_id
    return parent.public_coordinate


def disposed_text(register: Register, item: Disposition) -> str:
    """Source slice named by a disposition. An unknown span is empty."""
    parent = register.parent(item.parent_id)
    if parent is None or item.field not in QUOTE_FIELDS:
        return ""
    source = field_text(parent, item.field) or ""
    if item.start < 0 or item.end > len(source) or item.start > item.end:
        return ""
    return source[item.start : item.end]


def profile_for(register: Register, parent_id: str) -> ParentProfile | None:
    """Profile of one parent, when the shard file has one."""
    for _shard, profile in register.profiles:
        if profile.parent_id == parent_id:
            return profile
    return None


def _parent_key(parent: ParentRecord) -> tuple[int, int, str]:
    return (parent.sheet_ordinal or 0, parent.row_ordinal, parent.public_coordinate)
