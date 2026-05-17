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
from idis.evaluation.real_example_gate import (
    GateMode,
    ParseAttempt,
    _media_adapter_attemptable,
    _media_model_policy_key,
    main,
    run_real_example_gate,
)
from idis.evaluation.real_example_gate_ledger import media_policy_key
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


def test_inventory_only_does_not_stream_mp4_body(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential founder interview.mp4").write_bytes(b"do not stream")
    original_open = Path.open

    def fail_mp4_open(path: Path, mode: str = "r", *args: object, **kwargs: object) -> Any:
        if path.suffix.lower() == ".mp4" and "r" in mode:
            raise AssertionError("inventory must not stream MP4 bodies")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_mp4_open)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.INVENTORY_ONLY,
        emit_progress=False,
    )

    assert summary["total_files"] == 1
    assert summary["counts_by_extension"] == {".mp4": 1}
    assert summary["counts_by_status"] == {"inventoried": 1}
    _assert_safe_json(summary, forbidden=[str(root), "confidential", "do not stream"])


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


def test_parse_supported_retries_stale_pdf_ocr_result_when_policy_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    pdf_bytes = b"%PDF-1.4\npolicy-sensitive"
    (root / "confidential scanned appendix.pdf").write_bytes(pdf_bytes)
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    sha256: {
                        "by_extension": {
                            ".pdf": {
                                "extension": ".pdf",
                                "size_bytes": len(pdf_bytes),
                                "status": "deferred",
                                "parser_outcome": "ocr_no_text_extracted",
                                "reason_code": "ocr_no_text_extracted",
                                "ocr_policy_key": "pdf-ocr:v1:max_pages=1:timeout=20.0:dpi=200",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    attempts: list[str] = []

    summary = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        ocr_enabled=True,
        ocr_max_pages=2,
        ocr_timeout_seconds=20,
        ocr_dpi=200,
        parse_attempt_fn=lambda path: attempts.append(path.suffix.lower()) or ParseAttempt.parsed(),
    )

    assert attempts == [".pdf"]
    assert summary["counts_by_status"] == {"parsed": 1}
    assert summary["counts_by_reason_code"] == {"parsed": 1}
    _assert_ledger_is_private(ledger_path)
    _assert_safe_json(summary, forbidden=[str(root), "confidential", "policy-sensitive"])


def test_parse_supported_resumes_pdf_ocr_result_when_policy_is_unchanged(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    pdf_bytes = b"%PDF-1.4\nunchanged-policy"
    (root / "confidential scanned appendix.pdf").write_bytes(pdf_bytes)
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    policy_key = "pdf-ocr:v1:max_pages=2:timeout=20.0:dpi=200"
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    sha256: {
                        "by_extension": {
                            ".pdf": {
                                "extension": ".pdf",
                                "size_bytes": len(pdf_bytes),
                                "status": "deferred",
                                "parser_outcome": "ocr_no_text_extracted",
                                "reason_code": "ocr_no_text_extracted",
                                "ocr_policy_key": policy_key,
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
        ocr_enabled=True,
        ocr_max_pages=2,
        ocr_timeout_seconds=20,
        ocr_dpi=200,
        parse_attempt_fn=lambda path: (_ for _ in ()).throw(
            AssertionError(f"unchanged OCR policy should resume {path}")
        ),
    )

    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_parser_outcome"] == {"resumed": 1}
    assert summary["counts_by_reason_code"] == {"ocr_no_text_extracted": 1}
    _assert_ledger_is_private(ledger_path)
    _assert_safe_json(summary, forbidden=[str(root), "confidential", "unchanged-policy"])


def test_media_enabled_classifies_mp4_dependency_missing_without_leaks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Confidential Media"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "secret founder interview.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42 SLICE37 MEDIA SECRET"
    )

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_timeout_seconds=20,
    )

    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_parser_outcome"] == {"media_required": 1}
    assert summary["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    assert "parsed" not in summary["counts_by_status"]
    assert "parsed" not in summary["counts_by_reason_code"]
    _assert_safe_json(
        summary,
        forbidden=[str(root), "Confidential", "secret", "founder", "SLICE37", "MEDIA"],
    )


def test_media_enabled_no_adapter_does_not_read_mp4_body(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential interview.mp4").write_bytes(b"do not read")
    original_open = Path.open

    def fail_mp4_open(path: Path, mode: str = "r", *args: object, **kwargs: object) -> Any:
        if path.suffix.lower() == ".mp4" and "r" in mode:
            raise AssertionError("no-adapter MP4 path must not read media bytes")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_mp4_open)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
    )

    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_parser_outcome"] == {"media_required": 1}
    assert summary["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    _assert_safe_json(summary, forbidden=[str(root), "confidential", "do not read"])


def test_media_enabled_faster_whisper_without_model_does_not_read_mp4_body(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential interview.mp4").write_bytes(b"do not read")
    original_open = Path.open

    def fail_mp4_open(path: Path, mode: str = "r", *args: object, **kwargs: object) -> Any:
        if path.suffix.lower() == ".mp4" and "r" in mode:
            raise AssertionError("unconfigured faster-whisper path must not read media bytes")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_mp4_open)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_adapter="faster-whisper",
        media_model_name="tiny.en",
        media_allow_model_download=False,
    )

    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_parser_outcome"] == {"media_required": 1}
    assert summary["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    _assert_safe_json(summary, forbidden=[str(root), "confidential", "do not read"])


def test_media_enabled_missing_model_path_does_not_read_mp4_body_or_leak_path(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential interview.mp4").write_bytes(b"MISSING MODEL PATH MEDIA SECRET")
    missing_model_path = tmp_path / "secret-local-model"
    original_open = Path.open

    def fail_mp4_open(path: Path, mode: str = "r", *args: object, **kwargs: object) -> Any:
        if path.suffix.lower() == ".mp4" and "r" in mode:
            raise AssertionError("missing model path must defer before reading media bytes")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("idis.evaluation.real_example_gate.shutil.which", lambda _: "binary")
    monkeypatch.setattr(Path, "open", fail_mp4_open)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_adapter="faster-whisper",
        media_model_path=str(missing_model_path),
        media_allow_model_download=False,
    )

    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_parser_outcome"] == {"media_required": 1}
    assert summary["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    _assert_safe_json(
        summary,
        forbidden=[
            str(root),
            str(missing_model_path),
            "secret-local-model",
            "MISSING MODEL PATH",
        ],
    )


def test_media_enabled_invalid_model_directory_does_not_read_mp4_body_or_leak_path(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential interview.mp4").write_bytes(b"INVALID MODEL DIR MEDIA SECRET")
    invalid_model_path = tmp_path / "secret-empty-model"
    invalid_model_path.mkdir()
    original_open = Path.open

    def fail_mp4_open(path: Path, mode: str = "r", *args: object, **kwargs: object) -> Any:
        if path.suffix.lower() == ".mp4" and "r" in mode:
            raise AssertionError("invalid model path must defer before reading media bytes")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("idis.evaluation.real_example_gate.shutil.which", lambda _: "binary")
    monkeypatch.setattr(Path, "open", fail_mp4_open)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_adapter="faster-whisper",
        media_model_path=str(invalid_model_path),
        media_allow_model_download=False,
    )

    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_parser_outcome"] == {"media_required": 1}
    assert summary["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    _assert_safe_json(
        summary,
        forbidden=[
            str(root),
            str(invalid_model_path),
            "secret-empty-model",
            "INVALID MODEL DIR",
        ],
    )


def test_media_adapter_attemptable_accepts_valid_local_model_path_without_download(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "model.bin").write_text("synthetic model metadata", encoding="utf-8")
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("idis.evaluation.real_example_gate.shutil.which", lambda _: "binary")

    assert (
        _media_adapter_attemptable(
            media_adapter="faster-whisper",
            media_model_name=None,
            media_model_path=str(model_path),
            media_allow_model_download=False,
        )
        is True
    )


def test_media_enabled_default_output_is_aggregate_only(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Confidential Media"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "secret founder interview.mp4").write_bytes(b"NO PROGRESS SECRET")

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        media_enabled=True,
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert summary["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    _assert_safe_json(summary, forbidden=[str(root), "Confidential", "secret", "NO PROGRESS"])


@pytest.mark.parametrize("media_enabled", [False, True])
def test_defers_too_large_mp4_before_reading_body_regardless_of_media_setting(
    tmp_path: Path,
    monkeypatch: Any,
    media_enabled: bool,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential large interview.mp4").write_bytes(b"do not parse")
    original_open = Path.open

    def too_large_capability(**_: object) -> ParserCapability:
        return ParserCapability(
            file_type="MP4",
            support_status=DocumentSupportStatus.TOO_LARGE,
            triage_status=DocumentTriageStatus.TOO_LARGE,
            reason_codes=["file_too_large"],
            usable_without_conversion=False,
        )

    def fail_mp4_open(path: Path, mode: str = "r", *args: object, **kwargs: object) -> Any:
        if path.suffix.lower() == ".mp4" and "r" in mode:
            raise AssertionError("too-large MP4 files must defer before reading bytes")
        return original_open(path, mode, *args, **kwargs)

    def fail_read_bytes(_: Path) -> bytes:
        raise AssertionError("too-large MP4 files must defer before reading bytes")

    monkeypatch.setattr(
        "idis.evaluation.real_example_gate.capability_for_document",
        too_large_capability,
    )
    monkeypatch.setattr(Path, "open", fail_mp4_open)
    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / f"ledger-{media_enabled}.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=media_enabled,
    )

    assert summary["counts_by_status"] == {"deferred": 1}
    assert summary["counts_by_parser_outcome"] == {"not_attempted": 1}
    assert summary["counts_by_reason_code"] == {"file_too_large": 1}
    _assert_safe_json(summary, forbidden=[str(root), "confidential", "do not parse"])


def test_media_enabled_retries_stale_mp4_result_when_policy_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential interview.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42 stale media policy"
    )
    ledger_path = tmp_path / "ledger.json"

    first = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_timeout_seconds=10,
    )

    second = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_timeout_seconds=20,
    )

    assert first["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    assert second["counts_by_status"] == {"deferred": 1}
    assert second["counts_by_parser_outcome"] == {"media_required": 1}
    assert second["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    _assert_ledger_is_private(ledger_path)
    _assert_safe_json(second, forbidden=[str(root), "confidential", "stale media policy"])


def test_media_enabled_retries_stale_mp4_conversion_required_when_policy_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential interview.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42 stale conversion policy"
    )
    ledger_path = tmp_path / "ledger.json"

    first = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=False,
    )

    second = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_timeout_seconds=20,
    )

    assert first["counts_by_reason_code"] == {"conversion_required": 1}
    assert second["counts_by_status"] == {"deferred": 1}
    assert second["counts_by_parser_outcome"] == {"media_required": 1}
    assert second["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    _assert_ledger_is_private(ledger_path)
    _assert_safe_json(second, forbidden=[str(root), "confidential", "stale conversion policy"])


def test_media_enabled_resumes_mp4_result_when_policy_is_unchanged(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential interview.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42 unchanged media policy"
    )
    ledger_path = tmp_path / "ledger.json"

    first = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_timeout_seconds=20,
    )

    second = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_timeout_seconds=20,
    )

    assert first["counts_by_parser_outcome"] == {"media_required": 1}
    assert second["counts_by_status"] == {"deferred": 1}
    assert second["counts_by_parser_outcome"] == {"resumed": 1}
    assert second["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    _assert_ledger_is_private(ledger_path)
    _assert_safe_json(second, forbidden=[str(root), "confidential", "unchanged media policy"])


def test_media_policy_key_includes_enabled_adapter_timeout_and_version() -> None:
    disabled = media_policy_key(
        media_enabled=False,
        media_adapter_available=False,
        media_adapter_name="none",
        media_timeout_seconds=20,
    )
    no_adapter = media_policy_key(
        media_enabled=True,
        media_adapter_available=False,
        media_adapter_name="none",
        media_timeout_seconds=20,
    )
    provisioned_adapter = media_policy_key(
        media_enabled=True,
        media_adapter_available=True,
        media_adapter_name="fake-v1",
        media_timeout_seconds=20,
    )
    longer_timeout = media_policy_key(
        media_enabled=True,
        media_adapter_available=True,
        media_adapter_name="fake-v1",
        media_timeout_seconds=30,
    )
    faster_whisper = media_policy_key(
        media_enabled=True,
        media_adapter_available=True,
        media_adapter_name="faster-whisper",
        media_timeout_seconds=20,
        media_model_key="name:tiny.en",
        media_allow_model_download=False,
        media_language="en",
        media_compute_type="int8",
        media_max_duration_seconds=60,
    )
    different_model = media_policy_key(
        media_enabled=True,
        media_adapter_available=True,
        media_adapter_name="faster-whisper",
        media_timeout_seconds=20,
        media_model_key="name:base.en",
        media_allow_model_download=False,
        media_language="en",
        media_compute_type="int8",
        media_max_duration_seconds=60,
    )

    assert disabled is not None
    assert "enabled=false" in disabled
    assert "adapter_available=false" in no_adapter
    assert "adapter=none" in no_adapter
    assert "timeout=20.0" in no_adapter
    assert "max_timeout=120.0" in no_adapter
    assert "max_bytes=52428800" in no_adapter
    assert "max_segments=500" in no_adapter
    assert "max_segment_text_chars=20000" in no_adapter
    assert "v1" in no_adapter
    assert "model=name:tiny.en" in faster_whisper
    assert "allow_download=false" in faster_whisper
    assert "language=en" in faster_whisper
    assert "compute=int8" in faster_whisper
    assert "max_duration=60.0" in faster_whisper
    assert (
        len(
            {
                disabled,
                no_adapter,
                provisioned_adapter,
                longer_timeout,
                faster_whisper,
                different_model,
            }
        )
        == 6
    )


def test_media_model_policy_key_changes_when_local_model_path_state_changes(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    missing_key = _media_model_policy_key(media_model_name=None, media_model_path=str(model_path))
    model_path.write_text("synthetic model state v1", encoding="utf-8")
    present_key = _media_model_policy_key(media_model_name=None, media_model_path=str(model_path))
    time.sleep(0.001)
    model_path.write_text("synthetic model state v2 with different size", encoding="utf-8")
    changed_key = _media_model_policy_key(media_model_name=None, media_model_path=str(model_path))

    assert missing_key.startswith("path-missing-sha256:")
    assert present_key.startswith("file-sha256:")
    assert len({missing_key, present_key, changed_key}) == 3


def test_media_model_policy_key_changes_when_local_model_directory_child_changes(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    child_file = model_path / "model.bin"
    child_file.write_text("synthetic child state v1", encoding="utf-8")
    present_key = _media_model_policy_key(media_model_name=None, media_model_path=str(model_path))
    time.sleep(0.001)
    child_file.write_text("synthetic child state v2 with different size", encoding="utf-8")
    changed_key = _media_model_policy_key(media_model_name=None, media_model_path=str(model_path))

    assert present_key.startswith("dir-sha256:")
    assert changed_key.startswith("dir-sha256:")
    assert present_key != changed_key


def test_cli_env_parsing_accepts_stt_model_path_name_and_download_policy(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    captured_kwargs: dict[str, object] = {}
    model_path = tmp_path / "model"
    model_path.mkdir()

    def fake_gate(**kwargs: object) -> dict[str, object]:
        captured_kwargs.update(kwargs)
        return {
            "gate": "real_example_private_v1",
            "safe_summary": True,
            "mode": "inventory_only",
            "total_files": 0,
            "processed_files": 0,
            "ledger_entry_count": 0,
            "counts_by_extension": {},
            "counts_by_status": {},
            "counts_by_parser_outcome": {},
            "counts_by_reason_code": {},
        }

    monkeypatch.setenv("IDIS_MEDIA_ADAPTER", "faster-whisper")
    monkeypatch.setenv("IDIS_MEDIA_STT_MODEL_PATH", str(model_path))
    monkeypatch.setenv("IDIS_MEDIA_STT_MODEL_NAME", "tiny.en")
    monkeypatch.setenv("IDIS_MEDIA_STT_ALLOW_DOWNLOAD", "1")
    monkeypatch.setattr("idis.evaluation.real_example_gate.run_real_example_gate", fake_gate)

    exit_code = main(["--inventory-only", "--root", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured_kwargs["media_adapter"] == "faster-whisper"
    assert captured_kwargs["media_model_path"] == str(model_path)
    assert captured_kwargs["media_model_name"] == "tiny.en"
    assert captured_kwargs["media_allow_model_download"] is True
    assert str(model_path) not in output


def test_cli_env_parsing_ignores_legacy_download_env_without_stt_opt_in(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    captured_kwargs: dict[str, object] = {}

    def fake_gate(**kwargs: object) -> dict[str, object]:
        captured_kwargs.update(kwargs)
        return {
            "gate": "real_example_private_v1",
            "safe_summary": True,
            "mode": "inventory_only",
            "total_files": 0,
            "processed_files": 0,
            "ledger_entry_count": 0,
            "counts_by_extension": {},
            "counts_by_status": {},
            "counts_by_parser_outcome": {},
            "counts_by_reason_code": {},
        }

    monkeypatch.delenv("IDIS_MEDIA_STT_ALLOW_DOWNLOAD", raising=False)
    monkeypatch.setenv("IDIS_MEDIA_ALLOW_MODEL_DOWNLOAD", "1")
    monkeypatch.setattr("idis.evaluation.real_example_gate.run_real_example_gate", fake_gate)

    exit_code = main(["--inventory-only", "--root", str(tmp_path)])

    capsys.readouterr()
    assert exit_code == 0
    assert captured_kwargs["media_allow_model_download"] is False


def test_media_disabled_retries_stale_media_unavailable_entry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "confidential interview.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42 disabled media policy"
    )
    ledger_path = tmp_path / "ledger.json"

    first = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=True,
        media_timeout_seconds=20,
    )

    second = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        media_enabled=False,
    )

    assert first["counts_by_reason_code"] == {"media_transcription_unavailable": 1}
    assert second["counts_by_status"] == {"deferred": 1}
    assert second["counts_by_parser_outcome"] == {"not_attempted": 1}
    assert second["counts_by_reason_code"] == {"conversion_required": 1}
    _assert_ledger_is_private(ledger_path)
    _assert_safe_json(second, forbidden=[str(root), "confidential", "disabled media policy"])


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
