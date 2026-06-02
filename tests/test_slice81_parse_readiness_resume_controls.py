"""Slice81 Task 5 — resume ledger + timeout/memory confirmation through the projection.

Confirmation coverage (no production change). Drives the parse-readiness projection over a
deterministic synthetic corpus + injected ``parse_attempt_fn`` to confirm:
  * the projection reports ``ledger_entry_count`` and honors resume (terminal entries resume);
  * retryable/unintended reasons are NOT treated as terminal (re-attempted, not stale);
  * media uses the ``media-no-read:`` ledger key (bytes never read) and counts safely;
  * timeout flows through as ``parse_timeout`` -> unintended -> parse_ready False;
  * memory exceeded flows through as ``max_memory_exceeded`` -> unintended (forced via a
    test-only monkeypatch of the gate's imported probe; no dependence on real memory growth);
  * the projection JSON never leaks paths/filenames/content.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from idis.evaluation.real_example_gate import build_real_example_parse_readiness_summary
from idis.evaluation.real_example_gate_ledger import load_ledger
from idis.evaluation.real_example_gate_runtime import ParseAttempt, memory_exceeded

_STUB_FILES: dict[str, bytes] = {
    "confidential-deal.pdf": b"%PDF-1.4 synthetic stub",
    "notes.txt": b"hello world",
    "scan.png": b"\x89PNG\r\n\x1a\n synthetic stub",
    "board-call.mp4": b"\x00\x00\x00\x18ftypmp42",
    "table.csv": b"a,b,c\n1,2,3\n",
}
_FORBIDDEN_MARKERS = ("confidential-deal", "board-call", "scan", "notes", "table")


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "real_example"
    root.mkdir()
    for name, data in _STUB_FILES.items():
        (root / name).write_bytes(data)
    return root


def _assert_no_leak(payload: dict[str, object], *, root: Path) -> None:
    blob = json.dumps(payload, default=str)
    assert str(root) not in blob
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in blob


# --- 1. ledger_entry_count + resume of terminal entries ---


def test_projection_reports_ledger_count_and_resumes_terminal(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    first = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=ledger_path,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert first["ledger_entry_count"] == 5
    assert first["parse_ready"] is True

    second = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=ledger_path,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    # All 5 terminal intended outcomes resume on the second run.
    assert second["counts_by_parser_outcome"].get("resumed", 0) == 5
    assert second["parse_ready"] is True
    _assert_no_leak(second, root=root)


# --- 2. retryable/unintended reasons are not terminal (re-attempted, not stale) ---


def test_retryable_reason_is_reattempted_not_terminal(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    first = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=ledger_path,
        parse_attempt_fn=lambda _path: ParseAttempt.failed(reason_code="parser_failed"),
    )
    assert first["parse_ready"] is False
    assert first["unintended_deferral_reason_codes"].get("parser_failed") == 2

    # Second run: retryable parser_failed entries are NOT terminal -> re-attempted (succeed
    # here). The stale retryable reason must not be silently carried forward.
    second = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=ledger_path,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert second["counts_by_reason_code"].get("parser_failed", 0) == 0
    assert second["counts_by_parser_outcome"].get("success", 0) == 2  # pdf/txt re-attempted
    assert second["counts_by_parser_outcome"].get("resumed", 0) == 3  # png/mp4/csv intended
    assert second["parse_ready"] is True


# --- 3. media-no-read ledger key + safe media counting ---


def test_media_uses_no_read_key_and_projection_counts_media_safely(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    result = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=ledger_path,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert result["counts_by_extension"].get(".mp4") == 1
    assert result["counts_by_evidence_class"].get("MEDIA") == 1

    # Ledger keys media by a content-free media-no-read digest; raw media name/path absent.
    ledger = load_ledger(ledger_path)
    assert any(key.startswith("media-no-read:") for key in ledger["entries"])
    raw = ledger_path.read_text(encoding="utf-8")
    assert "board-call" not in raw
    assert str(root) not in raw
    _assert_no_leak(result, root=root)


# --- 4. timeout flows through the projection as unintended ---


def test_timeout_flows_through_projection_as_unintended(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    # Injecting a timed-out attempt routes through run_injected_parse_with_timeout ->
    # parse_timeout (no brittle sleep needed).
    result = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.timed_out(),
    )
    assert result["counts_by_reason_code"].get("parse_timeout") == 2  # pdf/txt
    assert result["unintended_deferral_reason_codes"].get("parse_timeout") == 2
    assert result["parse_ready"] is False
    _assert_no_leak(result, root=root)


# --- 5. memory exceeded flows through the projection as unintended ---


def test_memory_exceeded_flows_through_projection_as_unintended(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Portable truth: an unbounded budget never fires.
    assert memory_exceeded(None) is False
    # Characterized limitation: on this Windows dev env `_current_memory_mb()` returns 0.0,
    # so a positive max_memory_mb may not fire locally (it does on POSIX/CI via ru_maxrss).
    # To prove the reason-code flows through the projection deterministically, force the
    # gate's imported memory probe True (no dependence on real process memory growth).
    monkeypatch.setattr("idis.evaluation.real_example_gate.memory_exceeded", lambda _max: True)

    root = _corpus(tmp_path)
    result = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        max_memory_mb=1,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    # Only supported files (pdf/txt) reach the memory check; png/mp4/csv stay intended.
    assert result["counts_by_reason_code"].get("max_memory_exceeded") == 2
    assert result["unintended_deferral_reason_codes"].get("max_memory_exceeded") == 2
    assert result["parse_ready"] is False
    _assert_no_leak(result, root=root)
