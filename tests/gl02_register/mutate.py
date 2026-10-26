"""Copy and edit fictional registers in tests."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from idis.methodology.obligations.jsonl import read_jsonl, write_json, write_jsonl
from idis.methodology.obligations.models_authored import ChildObligation, ParentProfile
from idis.methodology.obligations.models_records import ExtractionReport, ParentRecord
from idis.methodology.obligations.register_io import load_register, parent_shard
from idis.methodology.obligations.validate import validate_register
from idis.methodology.obligations.vocab import ParentKind


def finding_codes(
    root: Path,
    *,
    workbook_path: Path | None = None,
    policy_path: Path | None = None,
    shards: list[str] | None = None,
    view: str | None = None,
) -> set[str]:
    """Return the finding codes for one validation."""
    report = validate_register(
        root,
        workbook_path=workbook_path,
        policy_path=policy_path,
        shards=shards,
        view=view,
    )
    return {item.code for item in report.findings}


def clone_register(root: Path, destination: Path) -> Path:
    """Copy a private root."""
    shutil.copytree(root, destination)
    return destination


def parent_sort_key(record: ParentRecord) -> tuple[int, int, int, str]:
    """Sort key documented for parent records."""
    kind = 0 if record.parent_kind is ParentKind.FINANCIAL else 1
    return (kind, record.sheet_ordinal or 0, record.row_ordinal, record.parent_id)


def replace_report(root: Path, report: ExtractionReport) -> None:
    """Rewrite the private extraction report."""
    write_json(root / "derived" / "extraction_report.private.json", report.model_dump(mode="json"))


def update_child(
    root: Path, coordinate: str, transform: Callable[[ChildObligation], ChildObligation]
) -> None:
    """Replace the child of one public coordinate."""
    register = load_register(root)
    parent = register.parents_by_coordinate[coordinate]
    shard = parent_shard(parent)
    path = root / "authored" / "children" / f"{shard.value}.jsonl"
    children = [
        transform(child) if child.parent_id == parent.parent_id else child
        for child in read_jsonl(path, ChildObligation)
    ]
    write_jsonl(path, children, key=lambda item: item.child_id)


def update_profile(
    root: Path, coordinate: str, transform: Callable[[ParentProfile], ParentProfile]
) -> None:
    """Replace the profile of one public coordinate."""
    register = load_register(root)
    parent = register.parents_by_coordinate[coordinate]
    shard = parent_shard(parent)
    path = root / "authored" / "profiles" / f"{shard.value}.jsonl"
    profiles = [
        transform(profile) if profile.parent_id == parent.parent_id else profile
        for profile in read_jsonl(path, ParentProfile)
    ]
    write_jsonl(path, profiles, key=lambda item: item.parent_id)
