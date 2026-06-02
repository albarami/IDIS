"""Slice81 Task 1 — characterization of the CURRENT private parse-readiness gate.

RED-as-discovery: pins existing behavior so later tasks change it deliberately. Uses a
synthetic on-disk corpus + an injected ``parse_attempt_fn`` so no real parser/OCR/media
binary runs in CI. **No production code is changed by Task 1.**

Synthetic corpus (extension → gate path under PARSE_SUPPORTED):
  .pdf  -> supported parser -> reaches parse_attempt_fn
  .txt  -> text parser      -> reaches parse_attempt_fn
  .png  -> OCR-required (OCR disabled) -> deferred/ocr_required (gated BEFORE the fn)
  .mp4  -> media disabled    -> deferred/conversion_required (gated BEFORE the fn)
  .csv  -> unsupported       -> unsupported/unsupported_format (gated BEFORE the fn)

Locks (per Slice81 plan §2): intended blockers vs the retryable/unintended set
(``RETRYABLE_REASON_CODES``), resume-ledger safety, timeout/memory controls, and that
``_safe_summary`` has no readiness/evidence-class fields yet (justifying the separate
projection in Tasks 2-3). GREEN-on-arrival = current truth confirmed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from idis.evaluation.real_example_gate import GateMode, run_real_example_gate
from idis.evaluation.real_example_gate_ledger import (
    RETRYABLE_REASON_CODES,
    load_ledger,
    terminal_ledger_entry,
)
from idis.evaluation.real_example_gate_runtime import (
    ParseAttempt,
    memory_exceeded,
    run_injected_parse_with_timeout,
)

_STUB_FILES: dict[str, bytes] = {
    "confidential-deal.pdf": b"%PDF-1.4 synthetic stub",
    "notes.txt": b"hello world",
    "scan.png": b"\x89PNG\r\n\x1a\n synthetic stub",
    "board-call.mp4": b"\x00\x00\x00\x18ftypmp42",
    "table.csv": b"a,b,c\n1,2,3\n",
}


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "real_example"
    root.mkdir()
    for name, data in _STUB_FILES.items():
        (root / name).write_bytes(data)
    return root


# --- 1. PARSE_SUPPORTED safe summary current truth ---


def test_parse_supported_safe_summary_current_truth(tmp_path: Path) -> None:
    root = _corpus(tmp_path)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )

    assert summary["counts_by_extension"] == {".csv": 1, ".mp4": 1, ".pdf": 1, ".png": 1, ".txt": 1}
    assert summary["counts_by_status"] == {"deferred": 2, "parsed": 2, "unsupported": 1}
    assert summary["counts_by_parser_outcome"] == {"not_attempted": 3, "success": 2}
    assert summary["counts_by_reason_code"] == {
        "conversion_required": 1,
        "ocr_required": 1,
        "parsed": 2,
        "unsupported_format": 1,
    }
    # safe aggregate: no root path or filename leaks
    blob = json.dumps(summary, default=str)
    assert str(root) not in blob
    assert "confidential-deal" not in blob


# --- 2. injected parser_failed is recorded as-is and is a retryable/unintended reason ---


def test_injected_parser_failed_is_recorded_and_is_retryable(tmp_path: Path) -> None:
    root = _corpus(tmp_path)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        parse_attempt_fn=lambda _path: ParseAttempt.failed(reason_code="parser_failed"),
    )

    # .pdf + .txt reach the injected fn and fail; the others stay intended blockers.
    assert summary["counts_by_reason_code"].get("parser_failed") == 2
    assert summary["counts_by_status"].get("failed") == 2
    assert summary["counts_by_reason_code"].get("ocr_required") == 1
    assert summary["counts_by_reason_code"].get("conversion_required") == 1

    # Current truth: parser_failed is in the retryable/unintended set, but the gate summary
    # does NOT yet classify it (no deferral-class field). Classifier arrives in Task 2-3.
    assert "parser_failed" in RETRYABLE_REASON_CODES
    assert "counts_by_deferral_class" not in summary


# --- 3. resume ledger: terminal recorded + resumed, retryable not terminal, safe ---


def test_resume_ledger_records_terminal_skips_retryable_and_is_safe(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    ledger_path = tmp_path / "ledger.json"

    run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )

    # Ledger is hash-keyed and safe: filenames/paths/content absent (extensions are safe).
    raw = ledger_path.read_text(encoding="utf-8")
    assert "confidential-deal" not in raw
    assert "scan.png" not in raw
    assert "board-call" not in raw
    assert str(root) not in raw
    ledger = load_ledger(ledger_path)
    assert ledger["version"] == 1
    assert ledger["entries"]
    # Keys are safe hashes: a 64-char sha256 for read files, or a "media-no-read:" prefixed
    # digest for media (the gate deliberately does NOT read media bytes). No filenames/paths.
    assert all(len(key) == 64 or key.startswith("media-no-read:") for key in ledger["entries"])

    # Second run over the same ledger resumes terminal entries.
    run2 = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert run2["counts_by_parser_outcome"].get("resumed", 0) >= 1

    # Pure-function truth: a retryable reason is NOT terminal; an intended blocker IS.
    entries: dict[str, object] = {
        "h": {
            "by_extension": {
                ".pdf": {
                    "extension": ".pdf",
                    "status": "failed",
                    "parser_outcome": "error",
                    "reason_code": "parser_failed",
                },
                ".csv": {
                    "extension": ".csv",
                    "status": "unsupported",
                    "parser_outcome": "not_attempted",
                    "reason_code": "unsupported_format",
                },
            }
        }
    }
    assert terminal_ledger_entry(entries=entries, sha256="h", extension=".pdf") is None
    assert terminal_ledger_entry(entries=entries, sha256="h", extension=".csv") is not None


# --- 4. timeout / memory controls map to retryable/unintended reasons (helper-level) ---


def test_timeout_and_memory_controls_are_retryable_reasons() -> None:
    # Memory budget helper: None means unbounded -> never exceeded (portable truth).
    # NOTE (characterized limitation): on this Windows dev env the live RSS probe
    # `_current_memory_mb()` returns 0.0, so a positive `max_memory_mb` cap is effectively
    # not enforced locally (it IS enforced on POSIX/CI via resource.getrusage ru_maxrss).
    assert memory_exceeded(None) is False

    # Per-file timeout: the gate threads ``per_file_timeout_seconds`` into this helper. A parse
    # overrunning the deadline yields a parse_timeout outcome. Characterized at the helper level
    # (a tiny deterministic 50ms vs 10ms margin) to avoid brittle full-gate process timing.
    def _slow(_path: Path) -> ParseAttempt:
        time.sleep(0.05)
        return ParseAttempt.parsed()

    timed = run_injected_parse_with_timeout(Path("x"), parse_attempt_fn=_slow, timeout_seconds=0.01)
    assert timed.status == "timed_out"
    assert timed.reason_code == "parse_timeout"

    # Both transient controls are retryable/unintended (classifier not yet implemented).
    assert {"parse_timeout", "max_memory_exceeded"} <= RETRYABLE_REASON_CODES


# --- 5. _safe_summary shape has no readiness/evidence-class fields yet ---


def test_safe_summary_shape_has_no_readiness_fields_yet(tmp_path: Path) -> None:
    root = _corpus(tmp_path)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )

    assert set(summary.keys()) == {
        "gate",
        "safe_summary",
        "mode",
        "total_files",
        "processed_files",
        "ledger_entry_count",
        "counts_by_extension",
        "counts_by_status",
        "counts_by_parser_outcome",
        "counts_by_reason_code",
    }
    for absent in (
        "evidence_class",
        "counts_by_evidence_class",
        "parse_ready",
        "counts_by_deferral_class",
        "unintended_deferrals",
        "unintended_deferral_reason_codes",
    ):
        assert absent not in summary
    assert summary["mode"] == "parse_supported"
    assert summary["safe_summary"] is True


# --- 6. INVENTORY_ONLY guard: never classifies parse readiness ---


def test_inventory_only_does_not_classify_parse_readiness(tmp_path: Path) -> None:
    root = _corpus(tmp_path)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.INVENTORY_ONLY,
    )

    assert summary["mode"] == "inventory_only"
    assert summary["counts_by_reason_code"] == {"inventory_only": 5}
    assert summary["counts_by_status"] == {"inventoried": 5}
    assert summary["counts_by_parser_outcome"] == {"not_attempted": 5}
    for absent in (
        "ocr_required",
        "conversion_required",
        "unsupported_format",
        "parsed",
        "parser_failed",
    ):
        assert absent not in summary["counts_by_reason_code"]
