"""Slice 29 tests for the private real_example gate harness."""

from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any

import pytest

from idis.api.errors import IdisHttpError
from idis.api.routes.documents import _reject_unsupported_upload_format
from idis.evaluation.real_example_gate import GateMode, ParseAttempt, run_real_example_gate
from idis.models.document_classification import (
    DocumentSupportStatus,
    DocumentTriageStatus,
    ParserCapability,
)


def test_inventory_only_is_deterministic_and_safe(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Nexx Confidential Board Pack"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "secret_customer_pipeline.xlsx").write_bytes(b"not a workbook")
    (root / "founder interview.mp4").write_bytes(b"fake video")
    ledger_path = tmp_path / "ledger.json"

    first = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.INVENTORY_ONLY,
        emit_progress=False,
    )
    second = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.INVENTORY_ONLY,
        emit_progress=False,
    )

    assert first == second
    assert first["total_files"] == 2
    assert first["counts_by_extension"] == {".mp4": 1, ".xlsx": 1}
    assert first["counts_by_status"] == {"inventoried": 2}
    assert first["counts_by_parser_outcome"] == {"not_attempted": 2}
    _assert_safe_json(first, forbidden=[str(root), "Nexx", "secret_customer", "founder"])
    _assert_ledger_is_private(ledger_path)


def test_inventory_hashing_streams_without_path_read_bytes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    confidential = root / "confidential large model.xlsx"
    confidential.write_bytes(b"streamed bytes only")

    def fail_read_bytes(_: Path) -> bytes:
        raise AssertionError("inventory must not load whole files with Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.INVENTORY_ONLY,
        emit_progress=False,
    )

    assert summary["total_files"] == 1
    assert summary["counts_by_extension"] == {".xlsx": 1}
    assert summary["counts_by_status"] == {"inventoried": 1}
    _assert_safe_json(summary, forbidden=[str(root), "confidential"])


def test_parse_supported_attempts_only_supported_extensions_and_records_reasons(
    tmp_path: Path,
    capsys: Any,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    supported = root / "confidential model.xlsx"
    unsupported = {
        ".mp4": root / "management interview.mp4",
        ".png": root / "scanned invoice.png",
        ".csv": root / "plain notes.csv",
        ".zip": root / "exported data room.zip",
    }
    supported.write_bytes(b"fake workbook")
    for path in unsupported.values():
        path.write_bytes(b"not parsed")
    attempted_extensions: list[str] = []

    def parse_attempt(path: Path) -> ParseAttempt:
        print("confidential parser diagnostic")
        attempted_extensions.append(path.suffix.lower())
        return ParseAttempt.parsed()

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        parse_attempt_fn=parse_attempt,
    )

    assert attempted_extensions == [".xlsx"]
    assert summary["counts_by_status"] == {
        "deferred": 2,
        "parsed": 1,
        "unsupported": 2,
    }
    assert summary["counts_by_parser_outcome"] == {
        "not_attempted": 4,
        "success": 1,
    }
    assert summary["counts_by_reason_code"] == {
        "conversion_required": 1,
        "ocr_required": 1,
        "parsed": 1,
        "unsupported_format": 2,
    }
    captured = capsys.readouterr()
    assert "confidential parser diagnostic" not in captured.out
    assert "confidential parser diagnostic" not in captured.err
    _assert_safe_json(summary, forbidden=[str(root), "confidential", "management", "scanned"])


def test_parse_supported_parses_html_and_text_without_leaking_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Confidential Notes"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "secret data room export.html").write_text(
        "<html><body><h1>SLICE35 HTML SECRET</h1></body></html>",
        encoding="utf-8",
    )
    (confidential_dir / "secret investment notes.txt").write_text(
        "SLICE35 TXT SECRET\n",
        encoding="utf-8",
    )

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
    )

    assert summary["counts_by_status"] == {"parsed": 2}
    assert summary["counts_by_parser_outcome"] == {"success": 2}
    assert summary["counts_by_reason_code"] == {"parsed": 2}
    _assert_safe_json(
        summary,
        forbidden=[
            str(root),
            "Confidential",
            "secret",
            "SLICE35",
            "HTML",
            "TXT",
            "investment",
        ],
    )


def test_parse_supported_retries_stale_html_text_unsupported_ledger_entries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Confidential Notes Resume"
    confidential_dir.mkdir(parents=True)
    html_bytes = b"<html><body><p>SLICE35 HTML RESUME</p></body></html>"
    text_bytes = b"SLICE35 TXT RESUME\n"
    (confidential_dir / "secret old export.html").write_bytes(html_bytes)
    (confidential_dir / "secret old notes.txt").write_bytes(text_bytes)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    hashlib.sha256(html_bytes).hexdigest(): {
                        "by_extension": {
                            ".html": {
                                "extension": ".html",
                                "size_bytes": len(html_bytes),
                                "status": "unsupported",
                                "parser_outcome": "not_attempted",
                                "reason_code": "unsupported_format",
                            }
                        }
                    },
                    hashlib.sha256(text_bytes).hexdigest(): {
                        "by_extension": {
                            ".txt": {
                                "extension": ".txt",
                                "size_bytes": len(text_bytes),
                                "status": "unsupported",
                                "parser_outcome": "not_attempted",
                                "reason_code": "unsupported_format",
                            }
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    summary = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
    )

    assert summary["counts_by_status"] == {"parsed": 2}
    assert summary["counts_by_parser_outcome"] == {"success": 2}
    assert summary["counts_by_reason_code"] == {"parsed": 2}
    _assert_ledger_is_private(ledger_path)
    _assert_safe_json(summary, forbidden=[str(root), "Confidential", "secret", "SLICE35"])


def test_parse_supported_defers_too_large_text_without_reading_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential large notes.txt").write_text("do not read", encoding="utf-8")

    def too_large_capability(**_: object) -> ParserCapability:
        return ParserCapability(
            file_type="TXT",
            support_status=DocumentSupportStatus.TOO_LARGE,
            triage_status=DocumentTriageStatus.TOO_LARGE,
            reason_codes=["file_too_large"],
            usable_without_conversion=False,
        )

    def fail_read_bytes(_: Path) -> bytes:
        raise AssertionError("too-large text files must defer before reading bytes")

    monkeypatch.setattr(
        "idis.evaluation.real_example_gate.capability_for_document",
        too_large_capability,
    )
    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
    )

    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_parser_outcome"] == {"not_attempted": 1}
    assert summary["counts_by_reason_code"] == {"file_too_large": 1}
    _assert_safe_json(summary, forbidden=[str(root), "confidential", "do not read"])


def test_default_upload_admission_still_rejects_html_and_text_bytes() -> None:
    with pytest.raises(IdisHttpError):
        _reject_unsupported_upload_format(
            b"<html><body>SLICE35 HTML</body></html>",
            "data-room-export.html",
        )
    with pytest.raises(IdisHttpError):
        _reject_unsupported_upload_format(b"SLICE35 TXT", "notes.txt")


def test_parse_supported_reports_no_text_pdf_as_ocr_required_without_leaks(
    tmp_path: Path,
    capsys: Any,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Confidential Scanned Data Room"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "confidential scanned appendix.pdf").write_bytes(_create_image_only_pdf())

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
    )

    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_parser_outcome"] == {"ocr_required": 1}
    assert summary["counts_by_reason_code"] == {"ocr_required": 1}
    captured = capsys.readouterr()
    assert "confidential scanned appendix" not in captured.out
    assert "confidential scanned appendix" not in captured.err
    _assert_safe_json(summary, forbidden=[str(root), "Confidential", "confidential", "scanned"])


def test_parse_supported_keeps_no_text_docx_as_non_ocr_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Confidential Empty Office Files"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "confidential empty notes.docx").write_bytes(_create_empty_docx())

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
    )

    assert summary["counts_by_status"] == {"failed": 1}
    assert summary["counts_by_parser_outcome"] == {"error": 1}
    assert summary["counts_by_reason_code"] == {"no_text_extracted": 1}
    _assert_safe_json(summary, forbidden=[str(root), "Confidential", "confidential", "empty"])


def test_parse_supported_retries_stale_non_pdf_ocr_required_ledger_entry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    docx_bytes = _create_empty_docx()
    (root / "confidential empty notes.docx").write_bytes(docx_bytes)
    sha256 = hashlib.sha256(docx_bytes).hexdigest()
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    sha256: {
                        "by_extension": {
                            ".docx": {
                                "extension": ".docx",
                                "size_bytes": len(docx_bytes),
                                "status": "deferred",
                                "parser_outcome": "ocr_required",
                                "reason_code": "ocr_required",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    summary = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
    )

    assert summary["counts_by_parser_outcome"] == {"error": 1}
    assert summary["counts_by_reason_code"] == {"no_text_extracted": 1}
    _assert_ledger_is_private(ledger_path)


def test_retryable_timeout_does_not_permanently_skip_hash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "slow confidential.pdf").write_bytes(b"%PDF-1.4\nslow")
    ledger_path = tmp_path / "ledger.json"

    def slow_parse(_: Path) -> ParseAttempt:
        time.sleep(0.05)
        return ParseAttempt.parsed()

    first = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        per_file_timeout_seconds=0.001,
        emit_progress=False,
        parse_attempt_fn=slow_parse,
    )

    assert first["counts_by_status"] == {"timed_out": 1}
    assert first["counts_by_parser_outcome"] == {"timeout": 1}
    assert first["counts_by_reason_code"] == {"parse_timeout": 1}

    attempts: list[str] = []

    def successful_retry(path: Path) -> ParseAttempt:
        attempts.append(path.suffix.lower())
        return ParseAttempt.parsed()

    second = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        per_file_timeout_seconds=1,
        emit_progress=False,
        parse_attempt_fn=successful_retry,
    )

    assert attempts == [".pdf"]
    assert second["counts_by_status"] == {"parsed": 1}
    assert second["counts_by_parser_outcome"] == {"success": 1}
    assert second["counts_by_reason_code"] == {"parsed": 1}
    _assert_ledger_is_private(ledger_path)


def test_successful_terminal_result_resumes_by_hash(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential support.pdf").write_bytes(b"%PDF-1.4\nsafe")
    ledger_path = tmp_path / "ledger.json"

    first = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        parse_attempt_fn=lambda _: ParseAttempt.parsed(),
    )

    def unexpected_parse(path: Path) -> ParseAttempt:
        raise AssertionError(f"resume should not re-parse {path}")

    second = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        parse_attempt_fn=unexpected_parse,
    )

    assert first["counts_by_status"] == {"parsed": 1}
    assert second["counts_by_status"] == {"parsed": 1}
    assert second["counts_by_parser_outcome"] == {"resumed": 1}
    _assert_ledger_is_private(ledger_path)


def test_production_subprocess_timeout_is_safe(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential slow.pdf").write_bytes(b"%PDF-1.4\n")

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        per_file_timeout_seconds=0.000001,
        emit_progress=False,
    )

    assert summary["counts_by_status"] == {"timed_out": 1}
    assert summary["counts_by_parser_outcome"] == {"timeout": 1}
    assert summary["counts_by_reason_code"] == {"parse_timeout": 1}
    captured = capsys.readouterr()
    assert "confidential" not in captured.out
    assert "confidential" not in captured.err


def test_xlsm_is_deferred_not_parsed_in_slice_29(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "macro confidential model.xlsm").write_bytes(b"PK\x03\x04not a workbook")
    attempted: list[str] = []

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        parse_attempt_fn=lambda path: (
            attempted.append(path.suffix.lower()) or ParseAttempt.parsed()
        ),
    )

    assert attempted == []
    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_reason_code"] == {"unsupported_in_slice_29": 1}


def test_same_hash_different_extensions_resume_independently(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    shared_bytes = b"PK\x03\x04same content"
    (root / "a confidential model.xlsx").write_bytes(shared_bytes)
    (root / "z confidential macro model.xlsm").write_bytes(shared_bytes)
    ledger_path = tmp_path / "ledger.json"
    attempted: list[str] = []

    first = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        parse_attempt_fn=lambda path: (
            attempted.append(path.suffix.lower()) or ParseAttempt.parsed()
        ),
    )
    second = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        parse_attempt_fn=lambda path: (
            attempted.append(path.suffix.lower()) or ParseAttempt.parsed()
        ),
    )

    assert attempted == [".xlsx"]
    assert first["counts_by_reason_code"] == {"parsed": 1, "unsupported_in_slice_29": 1}
    assert second["counts_by_parser_outcome"] == {"resumed": 2}
    assert second["counts_by_reason_code"] == {"parsed": 1, "unsupported_in_slice_29": 1}
    _assert_ledger_is_private(ledger_path)


def test_legacy_flat_ledger_entry_is_preserved_during_extension_split(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    shared_bytes = b"PK\x03\x04same legacy content"
    sha256 = hashlib.sha256(shared_bytes).hexdigest()
    (root / "a confidential model.xlsx").write_bytes(shared_bytes)
    (root / "z confidential macro model.xlsm").write_bytes(shared_bytes)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    sha256: {
                        "extension": ".xlsx",
                        "size_bytes": len(shared_bytes),
                        "status": "parsed",
                        "parser_outcome": "success",
                        "reason_code": "parsed",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    first = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        parse_attempt_fn=lambda _: ParseAttempt.parsed(),
    )
    second = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        parse_attempt_fn=lambda path: (_ for _ in ()).throw(
            AssertionError(f"resume should not re-parse {path}")
        ),
    )
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))

    assert first["counts_by_parser_outcome"] == {"not_attempted": 1, "resumed": 1}
    assert first["counts_by_reason_code"] == {"parsed": 1, "unsupported_in_slice_29": 1}
    assert second["counts_by_parser_outcome"] == {"resumed": 2}
    assert set(payload["entries"][sha256]["by_extension"]) == {".xlsx", ".xlsm"}
    _assert_ledger_is_private(ledger_path)


def test_max_runtime_and_memory_controls_defer_without_leaking_paths(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential appendix.docx").write_bytes(b"fake docx")

    runtime_summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "runtime-ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        max_runtime_seconds=0,
        emit_progress=False,
    )
    memory_summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "memory-ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        max_memory_mb=0,
        emit_progress=False,
    )

    assert runtime_summary["counts_by_status"] == {"deferred": 1}
    assert runtime_summary["counts_by_reason_code"] == {"max_runtime_exceeded": 1}
    assert memory_summary["counts_by_status"] == {"deferred": 1}
    assert memory_summary["counts_by_reason_code"] == {"max_memory_exceeded": 1}
    _assert_safe_json(runtime_summary, forbidden=[str(root), "confidential"])
    _assert_safe_json(memory_summary, forbidden=[str(root), "confidential"])


def _assert_safe_json(summary: dict[str, object], *, forbidden: list[str]) -> None:
    encoded = json.dumps(summary, sort_keys=True)
    assert '"root_path"' not in encoded
    assert '"filename"' not in encoded
    assert '"path"' not in encoded
    assert '"sha256"' not in encoded
    for token in forbidden:
        assert token not in encoded


def _assert_ledger_is_private(ledger_path: Path) -> None:
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    encoded = json.dumps(payload, sort_keys=True)
    assert '"filename"' not in encoded
    assert '"root_path"' not in encoded
    assert '"local_path"' not in encoded
    assert '"relative_path"' not in encoded


def _create_image_only_pdf() -> bytes:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab not installed")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.rect(72, 650, 144, 72, stroke=1, fill=0)
    c.save()
    return buffer.getvalue()


def _create_empty_docx() -> bytes:
    from docx import Document

    buffer = io.BytesIO()
    Document().save(buffer)
    return buffer.getvalue()
