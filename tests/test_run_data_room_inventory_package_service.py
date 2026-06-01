"""Tests for Slice 16 data-room inventory package service."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from idis.models.data_room_inventory_package_materialization import (
    DataRoomInventoryFileStatus,
    DataRoomInventoryPackageConstructionStatus,
    DataRoomInventoryReason,
)
from idis.services.runs.data_room_inventory_package import (
    InMemoryRunDataRoomInventoryPackageService,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)
from tests.test_xlsx_parser import create_test_xlsx


def test_recursive_scan_classifies_supported_deferred_and_blocked_files(tmp_path: Path) -> None:
    _write_fixture_tree(tmp_path)

    result, packages, corpus = InMemoryRunDataRoomInventoryPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=tmp_path,
    )

    package = packages[0]
    by_path = {file.relative_path: file for file in package.files}
    summary = result.to_run_step_summary()

    assert result.construction_status == DataRoomInventoryPackageConstructionStatus.COMPLETED
    # Slice78: HTML is canonical-supported -> supported alongside the xlsx.
    assert package.supported_document_ids == [
        by_path["Finance/Model.xlsx"].document_id,
        by_path["Notes/overview.html"].document_id,
    ]
    assert len(corpus) == 2
    assert {document["document_id"] for document in corpus} == {
        by_path["Finance/Model.xlsx"].document_id,
        by_path["Notes/overview.html"].document_id,
    }
    assert by_path["Finance/Model.xlsx"].file_status == DataRoomInventoryFileStatus.SUPPORTED
    assert by_path["Media/Demo.mp4"].file_status == DataRoomInventoryFileStatus.DEFERRED
    assert by_path["Scans/screenshot.png"].file_status == DataRoomInventoryFileStatus.DEFERRED
    assert by_path["Notes/overview.html"].file_status == DataRoomInventoryFileStatus.SUPPORTED
    assert by_path["Broken/corrupt.pdf"].file_status == DataRoomInventoryFileStatus.BLOCKED
    assert DataRoomInventoryReason.CONVERSION_REQUIRED.value in (
        by_path["Media/Demo.mp4"].reason_codes
    )
    assert DataRoomInventoryReason.OCR_REQUIRED.value in (
        by_path["Scans/screenshot.png"].reason_codes
    )
    assert DataRoomInventoryReason.SUPPORTED_PARSER_AVAILABLE.value in (
        by_path["Notes/overview.html"].reason_codes
    )
    assert DataRoomInventoryReason.PARSER_FAILED.value in (
        by_path["Broken/corrupt.pdf"].reason_codes
    )
    assert "ARR was $5M" not in str(summary)
    assert "text_excerpt" not in str(summary)
    assert "file_contents" not in str(summary)


def test_unicode_paths_are_preserved_safely_without_raw_content(tmp_path: Path) -> None:
    unicode_file = tmp_path / "战略合作" / "模型.xlsx"
    unicode_file.parent.mkdir(parents=True)
    unicode_file.write_bytes(create_test_xlsx({"Sheet1": [["ARR was $5M"]]}))

    result, packages, _corpus = InMemoryRunDataRoomInventoryPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=tmp_path,
    )

    file_record = packages[0].files[0]
    summary = result.to_run_step_summary()

    assert file_record.relative_path == "战略合作/模型.xlsx"
    assert file_record.path_hash
    assert file_record.file_status == DataRoomInventoryFileStatus.SUPPORTED
    assert "战略合作/模型.xlsx" in str(summary)
    assert "ARR was $5M" not in str(summary)


def test_missing_root_fails_closed_without_package() -> None:
    result, packages, corpus = InMemoryRunDataRoomInventoryPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=Path("missing-data-room"),
    )

    assert result.construction_status == DataRoomInventoryPackageConstructionStatus.FAILED
    assert packages == []
    assert corpus == []
    assert result.rejections[0].reason == DataRoomInventoryReason.ROOT_NOT_FOUND


def test_no_root_is_explicit_noop_for_existing_document_flow() -> None:
    result, packages, corpus = InMemoryRunDataRoomInventoryPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=None,
    )

    assert result.construction_status == DataRoomInventoryPackageConstructionStatus.COMPLETED
    assert packages == []
    assert corpus == []
    assert result.to_run_step_summary()["reason_codes"] == [
        DataRoomInventoryReason.NO_DATA_ROOM_ROOT.value
    ]


def test_no_text_pdf_is_ocr_required_but_empty_docx_is_not(tmp_path: Path) -> None:
    (tmp_path / "Scans").mkdir()
    (tmp_path / "Scans" / "scanned.pdf").write_bytes(_create_image_only_pdf())
    (tmp_path / "Office").mkdir()
    (tmp_path / "Office" / "empty.docx").write_bytes(_create_empty_docx())

    _result, packages, corpus = InMemoryRunDataRoomInventoryPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=tmp_path,
    )

    by_path = {file.relative_path: file for file in packages[0].files}

    assert corpus == []
    assert by_path["Scans/scanned.pdf"].file_status == DataRoomInventoryFileStatus.DEFERRED
    assert by_path["Scans/scanned.pdf"].reason_codes == [DataRoomInventoryReason.OCR_REQUIRED.value]
    assert by_path["Office/empty.docx"].file_status == DataRoomInventoryFileStatus.BLOCKED
    assert by_path["Office/empty.docx"].reason_codes == [
        DataRoomInventoryReason.PARSER_FAILED.value
    ]


def test_real_example_fixture_inventory_has_mixed_formats_and_nested_paths() -> None:
    fixture_root = _real_example_root()
    if fixture_root is None:
        pytest.skip("real_example fixture is not available in this checkout")

    result, packages, _corpus = InMemoryRunDataRoomInventoryPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=fixture_root,
        parse_supported_files=False,
    )

    summary = packages[0].to_summary()
    by_extension = summary.by_extension

    assert result.construction_status == DataRoomInventoryPackageConstructionStatus.COMPLETED
    assert summary.file_count >= 267
    assert by_extension[".pdf"] >= 223
    assert by_extension[".xlsx"] >= 27
    assert by_extension[".mp4"] >= 8
    assert by_extension[".png"] >= 2
    assert by_extension[".html"] >= 1
    assert by_extension[".txt"] >= 1
    assert any("/" in file.relative_path for file in packages[0].files)
    assert any(file.relative_path for file in packages[0].files if not file.relative_path.isascii())


def _write_fixture_tree(root: Path) -> None:
    (root / "Finance").mkdir()
    (root / "Finance" / "Model.xlsx").write_bytes(create_test_xlsx({"Sheet1": [["ARR was $5M"]]}))
    (root / "Media").mkdir()
    (root / "Media" / "Demo.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (root / "Scans").mkdir()
    (root / "Scans" / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "Notes").mkdir()
    (root / "Notes" / "overview.html").write_text("<html>secret</html>", encoding="utf-8")
    (root / "Broken").mkdir()
    (root / "Broken" / "corrupt.pdf").write_bytes(b"%PDF-corrupt")


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


def _real_example_root() -> Path | None:
    candidates = [
        Path.cwd() / "real_example",
        Path.cwd().parent / "real_example",
        Path("C:/Projects/IDIS/IDIS/real_example"),
        Path("C:/Projects/IDIS/real_example"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None
