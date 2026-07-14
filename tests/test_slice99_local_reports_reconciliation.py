"""Slice99 Task 4 - .local_reports reconciliation log (RED-first, Q1 boundaries).

Pins the reconciliation-log contract:

1. Entries are schema-validated, append-only, deterministic (canonical JSONL) and carry ONLY
   the Q1-approved fields: logical artifact type/id, sha256, created_at, safe aggregate counts,
   and blocker/status codes.
2. The private-gate entry point (``run_real_example_gate``) writes through the reconciliation
   logger: completing a gate run appends an entry whose sha256 matches the safe summary.
3. Unsafe values - raw paths, drive letters, URLs, non-hex hashes, secret-named or non-integer
   counts, lowercase/pathy codes - are REJECTED fail-closed (no partial writes).

Never records raw private filenames, paths, text, object keys, prompt transcripts, provider
payloads, or secrets. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from idis.evaluation.local_reports_log import (
    DEFAULT_RECONCILIATION_LOG_PATH,
    ReconciliationEntryError,
    append_reconciliation_entry,
    read_reconciliation_log,
)
from idis.evaluation.real_example_gate import run_real_example_gate

_CREATED_AT = "2026-07-14T00:00:00Z"
_SHA = "a" * 64


def _append(log_path: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "artifact_type": "real_example_gate_summary",
        "artifact_id": "real_example_gate:inventory",
        "sha256": _SHA,
        "counts": {"files_total": 3, "parsed": 2},
        "status_code": "GATE_COMPLETED",
        "blocker_codes": ["OCR_REQUIRED"],
        "created_at": _CREATED_AT,
        "log_path": log_path,
    }
    kwargs.update(overrides)
    return append_reconciliation_entry(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. schema-valid, append-only, deterministic
# ---------------------------------------------------------------------------


def test_append_writes_canonical_schema_valid_line(tmp_path: Path) -> None:
    log_path = tmp_path / "reconciliation_log.jsonl"

    entry = _append(log_path)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == entry
    assert parsed["schema_version"] == 1
    assert parsed["artifact_type"] == "real_example_gate_summary"
    assert parsed["sha256"] == _SHA
    assert parsed["created_at"] == _CREATED_AT
    assert parsed["counts"] == {"files_total": 3, "parsed": 2}
    assert parsed["status_code"] == "GATE_COMPLETED"
    assert parsed["blocker_codes"] == ["OCR_REQUIRED"]
    # canonical serialization: sorted keys, compact separators (deterministic line)
    assert lines[0] == json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def test_append_is_append_only(tmp_path: Path) -> None:
    log_path = tmp_path / "reconciliation_log.jsonl"

    _append(log_path)
    first_line = log_path.read_text(encoding="utf-8").splitlines()[0]
    _append(log_path, artifact_id="real_example_gate:parse", counts={"files_total": 5})

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == first_line, "existing entries must never be rewritten"
    entries = read_reconciliation_log(log_path)
    assert [e["artifact_id"] for e in entries] == [
        "real_example_gate:inventory",
        "real_example_gate:parse",
    ]


def test_default_log_path_is_local_reports_jsonl() -> None:
    assert Path(".local_reports") / "reconciliation_log.jsonl" == DEFAULT_RECONCILIATION_LOG_PATH


# ---------------------------------------------------------------------------
# 2. fail-closed rejection of unsafe fields (no partial writes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"artifact_id": "C:/private/deal_room/file"},
        {"artifact_id": "..\\..\\secret"},
        {"artifact_id": "s3://bucket/object-key"},
        {"artifact_id": "deal room notes.txt\n"},
        {"artifact_type": "raw/path/type"},
        {"sha256": "not-a-hash"},
        {"sha256": "A" * 64},
        {"counts": {"api_key": 1}},
        {"counts": {"parsed": "two"}},
        {"counts": {"Bad Key": 2}},
        {"status_code": "lower_case"},
        {"status_code": "PATH/CODE"},
        {"blocker_codes": ["ok/path"]},
        {"created_at": "yesterday"},
    ],
)
def test_unsafe_entries_are_rejected_fail_closed(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    log_path = tmp_path / "reconciliation_log.jsonl"

    with pytest.raises(ReconciliationEntryError):
        _append(log_path, **overrides)

    assert not log_path.exists(), "a rejected entry must not leave partial writes"


# ---------------------------------------------------------------------------
# 3. the private-gate entry point writes through the logger
# ---------------------------------------------------------------------------


def _run_gate(tmp_path: Path) -> tuple[dict[str, object], Path]:
    root = tmp_path / "data_room"
    root.mkdir()
    (root / "note_one.txt").write_text("safe fixture text", encoding="utf-8")
    (root / "note_two.txt").write_text("more safe fixture text", encoding="utf-8")
    ledger_path = tmp_path / ".local_reports" / "real_example_gate_ledger.json"

    summary = run_real_example_gate(root=root, ledger_path=ledger_path)
    return summary, ledger_path.parent / "reconciliation_log.jsonl"


def test_gate_completion_appends_reconciliation_entry(tmp_path: Path) -> None:
    summary, log_path = _run_gate(tmp_path)

    entries = read_reconciliation_log(log_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["artifact_type"] == "real_example_gate_summary"
    expected_sha = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert entry["sha256"] == expected_sha
    assert entry["status_code"] == "GATE_COMPLETED"
    assert isinstance(entry["counts"], dict) and entry["counts"]
    assert all(isinstance(v, int) for v in entry["counts"].values())


def test_gate_reconciliation_log_never_leaks_paths_or_filenames(tmp_path: Path) -> None:
    _, log_path = _run_gate(tmp_path)

    raw = log_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in raw, "log must not leak the private root path"
    assert "data_room" not in raw, "log must not leak private directory names"
    assert "note_one" not in raw, "log must not leak private filenames"


def test_gate_reruns_append_not_rewrite(tmp_path: Path) -> None:
    root = tmp_path / "data_room"
    root.mkdir()
    (root / "note.txt").write_text("safe fixture text", encoding="utf-8")
    ledger_path = tmp_path / ".local_reports" / "real_example_gate_ledger.json"

    run_real_example_gate(root=root, ledger_path=ledger_path)
    run_real_example_gate(root=root, ledger_path=ledger_path)

    entries = read_reconciliation_log(ledger_path.parent / "reconciliation_log.jsonl")
    assert len(entries) == 2
