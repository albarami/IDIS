"""Tests for synthetic FDD Excel methodology importer."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from idis.methodology.importers.fdd_excel import (
    FDDImporterError,
    import_fdd_workbook,
)
from idis.methodology.models import MethodologyType

REQUIRED_SHEETS = ("P&L", "Cash Flow", "Liabilities", "Assets")
HEADERS = (
    "Term",
    "Nature",
    "Financial statement line",
    "Usual Due Diligence Questions",
)


def _synthetic_workbook_bytes(
    *,
    missing_sheet: str | None = None,
    missing_column: str | None = None,
    empty_question: bool = False,
    duplicate_question: bool = False,
    mixed_case_question: bool = False,
) -> bytes:
    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    for sheet_name in REQUIRED_SHEETS:
        if sheet_name == missing_sheet:
            continue
        sheet = workbook.create_sheet(sheet_name)
        headers = [header for header in HEADERS if header != missing_column]
        sheet.append(headers)
        if empty_question and sheet_name == "P&L":
            question = ""
        elif mixed_case_question and sheet_name == "P&L":
            question = "  Review P&L Mixed CASE Support  "
        elif duplicate_question and sheet_name == "P&L":
            question = "Review duplicated support"
        else:
            question = f"Review {sheet_name} support"
        line_item = "Revenue" if duplicate_question else f"{sheet_name} line item"
        row = {
            "Term": f"{sheet_name} term",
            "Nature": "Financial",
            "Financial statement line": line_item,
            "Usual Due Diligence Questions": question,
        }
        sheet.append([row[header] for header in headers])
        if duplicate_question and sheet_name == "P&L":
            sheet.append([row[header] for header in headers])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_imports_synthetic_workbook_successfully() -> None:
    """Importer reads sanitized synthetic workbook bytes into registry data."""
    registry = import_fdd_workbook(_synthetic_workbook_bytes())

    assert registry.methodology_type == MethodologyType.FINANCIAL_DD
    assert registry.methodology_id == "financial_dd"
    assert len(registry.current_version.questions) == 4
    assert registry.current_version.source_hash


def test_validates_all_four_required_sheets() -> None:
    """Missing any required FDD worksheet fails closed."""
    with pytest.raises(FDDImporterError, match="missing required sheet"):
        import_fdd_workbook(_synthetic_workbook_bytes(missing_sheet="Assets"))


def test_validates_required_columns() -> None:
    """Missing workbook columns fail closed before producing a registry."""
    with pytest.raises(FDDImporterError, match="missing required column"):
        import_fdd_workbook(_synthetic_workbook_bytes(missing_column="Nature"))


def test_produces_deterministic_registry_hash() -> None:
    """Repeated imports of the same workbook bytes have stable IDs and hashes."""
    workbook_bytes = _synthetic_workbook_bytes()

    first = import_fdd_workbook(workbook_bytes)
    second = import_fdd_workbook(workbook_bytes)

    assert first.registry_hash == second.registry_hash
    assert [
        question.methodology_question_id
        for question in first.current_version.questions
    ] == [
        question.methodology_question_id
        for question in second.current_version.questions
    ]


def test_preserves_sheet_and_row_traceability() -> None:
    """Imported questions preserve source sheet and row trace metadata."""
    registry = import_fdd_workbook(_synthetic_workbook_bytes())
    question = next(
        item
        for item in registry.current_version.questions
        if item.source_trace.sheet_or_section == "Assets"
    )

    assert question.source_trace.sheet_or_section == "Assets"
    assert question.source_trace.row_number == 2
    assert question.source_trace.source_hash == registry.current_version.source_hash
    assert question.sheet_or_source_section == "Assets"


def test_preserves_source_question_text_while_ids_use_trace_context() -> None:
    """Importer preserves source wording and uses trace context in stable IDs."""
    registry = import_fdd_workbook(_synthetic_workbook_bytes(mixed_case_question=True))
    pl_question = next(
        item
        for item in registry.current_version.questions
        if item.source_trace.sheet_or_section == "P&L"
    )

    assert pl_question.question_text == "Review P&L Mixed CASE Support"
    assert pl_question.methodology_question_id != next(
        item
        for item in registry.current_version.questions
        if item.source_trace.sheet_or_section == "Cash Flow"
    ).methodology_question_id


def test_duplicate_question_id_fails_closed() -> None:
    """Duplicate generated methodology question IDs fail closed."""
    with pytest.raises(FDDImporterError, match="duplicate methodology_question_id"):
        import_fdd_workbook(_synthetic_workbook_bytes(duplicate_question=True))


def test_empty_question_text_fails_closed() -> None:
    """Empty methodology question text must not enter the registry."""
    with pytest.raises(FDDImporterError, match="empty question text"):
        import_fdd_workbook(_synthetic_workbook_bytes(empty_question=True))


def test_invalid_workbook_shape_fails_closed() -> None:
    """Invalid workbook bytes fail closed without producing registry data."""
    with pytest.raises(FDDImporterError, match="invalid workbook shape"):
        import_fdd_workbook(b"not an xlsx workbook")


def test_importer_accepts_path_without_real_confidential_workbook(tmp_path: Path) -> None:
    """Importer supports paths while tests only use sanitized synthetic files."""
    workbook_path = tmp_path / "fdd_synthetic.xlsx"
    workbook_path.write_bytes(_synthetic_workbook_bytes())

    registry = import_fdd_workbook(workbook_path)

    assert registry.current_version.source_name == "fdd_synthetic.xlsx"
    assert "financial Due Diligence.xlsx" not in registry.to_deterministic_json()
