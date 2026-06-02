"""Slice81 Task 4 — parse-readiness acceptance proof + thin --parse-readiness CLI flag.

Acceptance: ``real_example`` parse readiness has zero unintended deferrals before
downstream live work is accepted. Proven on a deterministic synthetic corpus + injected
``parse_attempt_fn`` (no real parser/OCR/media binary in CI). The CLI tests exercise the
new ``--parse-readiness`` flag (RED until implemented) and guard that the existing CLI is
unchanged when the flag is absent. Safe aggregate only — no per-file records or leaks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from idis.evaluation.real_example_gate import (
    GateMode,
    build_real_example_parse_readiness_summary,
    main,
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

_PROJECTION_KEYS = {
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


# --- projection-level acceptance ---


def test_parse_readiness_acceptance_ready_with_only_intended_blockers(tmp_path: Path) -> None:
    result = build_real_example_parse_readiness_summary(
        root=_corpus(tmp_path),
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    # parsed PDF/text + OCR-required image + conversion-required media + unsupported CSV.
    assert result["parse_ready"] is True
    assert result["counts_by_deferral_class"] == {"intended": 5}
    assert result["unintended_deferral_reason_codes"] == {}
    assert result["counts_by_evidence_class"] == {
        "IMAGE": 1,
        "MEDIA": 1,
        "OTHER": 1,
        "PDF": 1,
        "WEB_TEXT": 1,
    }
    # counts by parser status + blocker reason present.
    assert result["counts_by_status"]["parsed"] == 2
    assert result["counts_by_reason_code"]["ocr_required"] == 1
    assert result["counts_by_reason_code"]["conversion_required"] == 1


@pytest.mark.parametrize(
    "reason_code",
    ["parser_failed", "media_transcription_failed", "media_transcription_timeout"],
)
def test_parse_readiness_acceptance_not_ready_with_unintended_deferral(
    tmp_path: Path, reason_code: str
) -> None:
    result = build_real_example_parse_readiness_summary(
        root=_corpus(tmp_path),
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.failed(reason_code=reason_code),
    )
    assert result["parse_ready"] is False
    assert result["counts_by_deferral_class"].get("unintended", 0) >= 1
    assert result["unintended_deferral_reason_codes"].get(reason_code) == 2  # pdf + txt


def test_parse_readiness_acceptance_unknown_format_is_not_ready(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "mystery.xyz").write_bytes(b"???")
    result = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    assert result["parse_ready"] is False
    assert result["unintended_deferral_reason_codes"].get("unknown_format") == 1


def test_parse_readiness_projection_is_leak_safe(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    result = build_real_example_parse_readiness_summary(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )
    blob = json.dumps(result, default=str)
    for marker in ("confidential-deal", "board-call", "scan", "notes", "table", str(root)):
        assert marker not in blob
    assert set(result.keys()) == _PROJECTION_KEYS
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


def test_inventory_only_is_not_parse_readiness_surface(tmp_path: Path) -> None:
    summary = run_real_example_gate(
        root=_corpus(tmp_path),
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.INVENTORY_ONLY,
    )
    assert summary["mode"] == "inventory_only"
    for absent in ("parse_ready", "counts_by_evidence_class", "counts_by_deferral_class"):
        assert absent not in summary


# --- CLI ---


def test_cli_parse_readiness_flag_emits_projection(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    captured_kwargs: dict[str, object] = {}

    def fake_projection(**kwargs: object) -> dict[str, object]:
        captured_kwargs.update(kwargs)
        return {
            "safe_summary": True,
            "source": "real_example_private_parse_readiness",
            "mode": "parse_supported",
            "total_files": 1,
            "processed_files": 1,
            "ledger_entry_count": 1,
            "counts_by_extension": {".pdf": 1},
            "counts_by_status": {"parsed": 1},
            "counts_by_parser_outcome": {"success": 1},
            "counts_by_reason_code": {"parsed": 1},
            "counts_by_evidence_class": {"PDF": 1},
            "counts_by_deferral_class": {"intended": 1},
            "unintended_deferral_reason_codes": {},
            "parse_ready": True,
        }

    monkeypatch.setattr(
        "idis.evaluation.real_example_gate.build_real_example_parse_readiness_summary",
        fake_projection,
    )

    exit_code = main(
        ["--parse-readiness", "--root", str(tmp_path), "--ledger", str(tmp_path / "l")]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["parse_ready"] is True
    assert payload["mode"] == "parse_supported"
    assert "counts_by_evidence_class" in payload
    assert str(captured_kwargs["root"]) == str(tmp_path)
    assert str(tmp_path) not in out


def test_cli_without_parse_readiness_returns_safe_summary(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    def fake_gate(**kwargs: object) -> dict[str, object]:
        return {
            "gate": "real_example_private_v1",
            "safe_summary": True,
            "mode": "parse_supported",
            "total_files": 0,
            "processed_files": 0,
            "ledger_entry_count": 0,
            "counts_by_extension": {},
            "counts_by_status": {},
            "counts_by_parser_outcome": {},
            "counts_by_reason_code": {},
        }

    monkeypatch.setattr("idis.evaluation.real_example_gate.run_real_example_gate", fake_gate)

    exit_code = main(["--parse-supported", "--safe-summary", "--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["gate"] == "real_example_private_v1"
    assert "parse_ready" not in payload
    assert "counts_by_evidence_class" not in payload


def test_cli_parse_readiness_output_is_leak_safe(tmp_path: Path, capsys: Any) -> None:
    # Real CLI over OCR/media/unsupported files only (all gated pre-parse -> no real binary).
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "CONFIDENTIAL-DEAL-scan.png").write_bytes(b"\x89PNG\r\n\x1a\n stub")
    (root / "secret-board-call.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (root / "private-table.csv").write_bytes(b"a,b\n1,2\n")

    exit_code = main(
        ["--parse-readiness", "--root", str(root), "--ledger", str(tmp_path / "ledger.json")]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    payload = json.loads(out)
    assert "parse_ready" in payload
    assert "counts_by_evidence_class" in payload
    for marker in ("CONFIDENTIAL-DEAL", "secret-board-call", "private-table", str(root)):
        assert marker not in out
