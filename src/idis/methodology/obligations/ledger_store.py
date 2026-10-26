"""Atomic ledger and snapshot bytes. Callers decide whether a seal may write."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from idis.methodology.obligations.jsonl import canonical_json, dump_model
from idis.methodology.obligations.models_authored import LedgerRecord, SealRecord

_TREE_FOLDERS = ("authored", "derived")


def commit_ledger(
    issued_path: Path,
    seals_path: Path,
    old_issued: bytes,
    new_issued: bytes,
    new_seals: bytes,
) -> None:
    """Replace the ledger files. A failure restores the previous issued-id bytes."""
    wrote_issued = False
    try:
        if new_issued != old_issued or not issued_path.is_file():
            _replace_bytes(issued_path, new_issued)
            wrote_issued = True
        _replace_bytes(seals_path, new_seals)
    except OSError:
        if wrote_issued:
            if old_issued:
                _replace_bytes(issued_path, old_issued)
            elif issued_path.is_file():
                issued_path.unlink()
        raise


def write_snapshot(root: Path, partial: Path, issued: bytes, old_seals: bytes) -> None:
    """Copy the hashed tree and the pre-seal ledger into a partial snapshot."""
    if partial.exists():
        shutil.rmtree(partial)
    for folder in _TREE_FOLDERS:
        _copy_folder(root / folder, partial / folder)
    ledger = partial / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "issued_ids.jsonl").write_bytes(issued)
    if old_seals:
        (ledger / "seals.jsonl").write_bytes(old_seals)


def ledger_lines(
    records: tuple[LedgerRecord, ...] | tuple[SealRecord, ...] | list[LedgerRecord],
) -> bytes:
    """Return canonical JSONL bytes for ledger records, or empty bytes."""
    if not records:
        return b""
    return "".join(canonical_json(dump_model(record)) + "\n" for record in records).encode("utf-8")


def _copy_folder(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    files = [
        path for path in source.rglob("*") if path.is_file() and "__pycache__" not in path.parts
    ]
    for path in sorted(files, key=lambda item: item.relative_to(source).as_posix()):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def _replace_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
