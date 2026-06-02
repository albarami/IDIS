"""Slice81 Task 3 — build_real_example_parse_readiness_summary projection.

TDD RED-first: imports a projection that does not exist yet. The projection wraps the
gate's PARSE_SUPPORTED safe aggregate and adds evidence-class + deferral-class counts +
a parse_ready verdict, WITHOUT mutating ``_safe_summary``. Deterministic synthetic corpus
+ injected ``parse_attempt_fn`` (no real parser/OCR/media binary in CI). Safe aggregate
only — no per-file records, paths, filenames, or content.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from idis.evaluation.real_example_gate import (
    GateMode,
    build_real_example_parse_readiness_summary,
    run_real_example_gate,
)
from idis.evaluation.real_example_gate_runtime import ParseAttempt

_STUB_FILES: dict[str, bytes] = {
    "confidential-deal.pdf": b"%PDF-1.4 synthetic stub",
    "notes.txt": b"hello world",
    "scan.png": b"\x89PNG\r\n\x1a\n synthetic stub",
    "board-call.mp4": b"\x00\x00\x00\x18ftypmp42",
    "table.csv": b"a,b,c\n1,2,3\n",
}

_EXPECTED_KEYS = {
    "safe_summary",
    "source",
    "mode",
    "total_files",
    "processed_files",
    "ledger_entry_count",
    "counts_by_extension",
    "counts_by_status",
    "counts_by_parser_outcome",
    "counts_by_reason_code",
    "counts_by_evidence_class",
    "counts_by_deferral_class",
    "unintended_deferral_reason_codes",
    "parse_ready",
}


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "real_example"
    root.mkdir()
    for name, data in _STUB_FILES.items():
        (root / name).write_bytes(data)
    return root


def test_projection_returns_required_safe_fields(tmp_path: Path) -> None:
    result = build_real_example_parse_readiness_summary(
        root=_corpus(tmp_path),
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert set(result.keys()) >= _EXPECTED_KEYS
    assert result["safe_summary"] is True
    assert result["mode"] == "parse_supported"


def test_projection_all_intended_is_parse_ready(tmp_path: Path) -> None:
    result = build_real_example_parse_readiness_summary(
        root=_corpus(tmp_path),
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    # parsed x2 (pdf/txt) + ocr_required + conversion_required + unsupported_format = all intended
    assert result["counts_by_deferral_class"] == {"intended": 5}
    assert result["unintended_deferral_reason_codes"] == {}
    assert result["parse_ready"] is True
    assert result["counts_by_evidence_class"] == {
        "IMAGE": 1,
        "MEDIA": 1,
        "OTHER": 1,
        "PDF": 1,
        "WEB_TEXT": 1,
    }


def test_projection_injected_parser_failed_is_not_ready(tmp_path: Path) -> None:
    result = build_real_example_parse_readiness_summary(
        root=_corpus(tmp_path),
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.failed(reason_code="parser_failed"),
    )
    assert result["parse_ready"] is False
    assert result["counts_by_deferral_class"].get("unintended", 0) >= 1
    assert result["unintended_deferral_reason_codes"].get("parser_failed") == 2


def test_projection_unknown_format_is_not_ready(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "mystery.xyz").write_bytes(b"???")
    result = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert result["counts_by_reason_code"].get("unknown_format") == 1
    assert result["parse_ready"] is False
    assert result["unintended_deferral_reason_codes"].get("unknown_format") == 1


def test_projection_is_leak_safe_and_keys_are_bounded(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    result = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    blob = json.dumps(result, default=str)
    assert str(root) not in blob
    assert "confidential-deal" not in blob
    assert "board-call" not in blob
    assert "scan" not in blob

    assert set(result.keys()) == _EXPECTED_KEYS
    assert set(result["counts_by_evidence_class"]) <= {
        "PDF",
        "SPREADSHEET",
        "DOCUMENT",
        "PRESENTATION",
        "WEB_TEXT",
        "IMAGE",
        "MEDIA",
        "OTHER",
    }
    assert set(result["counts_by_deferral_class"]) <= {"intended", "unintended"}


def test_safe_summary_is_not_mutated_by_projection(tmp_path: Path) -> None:
    base = run_real_example_gate(
        root=_corpus(tmp_path),
        ledger_path=tmp_path / "base_ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert set(base.keys()) == {
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
    assert "counts_by_evidence_class" not in base
    assert "parse_ready" not in base


def test_projection_reports_ledger_entry_count_and_honors_resume(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    first = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=ledger_path,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert first["ledger_entry_count"] >= 1
    second = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=ledger_path,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert second["counts_by_parser_outcome"].get("resumed", 0) >= 1


def test_projection_is_parse_supported_only() -> None:
    # The projection cannot be pointed at INVENTORY_ONLY (no mode parameter).
    params = inspect.signature(build_real_example_parse_readiness_summary).parameters
    assert "mode" not in params
