"""RED contracts for the real, read-only 79-row FDD importer."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from idis.methodology.importers.fdd_excel import FDDImporterError, import_fdd_workbook
from idis.methodology.models import MethodologyType
from tests.slice100_task5._workbook_factory import (
    CASH_FLOW_EMPTY_LINE_ITEM_ROW,
    LIABILITIES_MERGED_QUESTION_RANGES,
    SHEET_ORDER,
    SHEET_ROW_COUNTS,
    production_fdd_workbook_bytes,
)

EXPECTED_REAL_WORKBOOK_SHA256 = "a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352"


def _assert_import_invalid(workbook: bytes) -> None:
    with pytest.raises(FDDImporterError) as raised:
        import_fdd_workbook(workbook)
    assert getattr(raised.value, "code", None) == "FDD_IMPORT_INVALID"


def test_production_shaped_workbook_imports_exactly_79_traced_rows() -> None:
    registry = import_fdd_workbook(production_fdd_workbook_bytes())
    version = registry.current_version

    assert registry.methodology_type is MethodologyType.FINANCIAL_DD
    assert len(version.questions) == 79
    assert version.source_hash == version.questions[0].source_trace.source_hash
    assert all(
        question.source_trace.source_type == "operator_workbook" for question in version.questions
    )
    assert all(
        question.source_trace.sheet_or_section in SHEET_ORDER for question in version.questions
    )
    assert all(question.source_trace.row_number is not None for question in version.questions)
    assert {
        sheet: sum(
            question.source_trace.sheet_or_section == sheet for question in version.questions
        )
        for sheet in SHEET_ORDER
    } == SHEET_ROW_COUNTS


def test_merged_question_children_inherit_anchor_prose_and_keep_row_trace() -> None:
    registry = import_fdd_workbook(production_fdd_workbook_bytes())
    by_row = {
        question.source_trace.row_number: question
        for question in registry.current_version.questions
        if question.source_trace.sheet_or_section == "Liabilities"
    }

    for first_row, last_row in LIABILITIES_MERGED_QUESTION_RANGES:
        anchor = by_row[first_row]
        assert anchor.source_trace.anchor_cell == f"E{first_row}"
        for child_row in range(first_row + 1, last_row + 1):
            child = by_row[child_row]
            assert child.question_text == anchor.question_text
            assert child.source_trace.row_number == child_row
            assert child.source_trace.anchor_cell == f"E{first_row}"
            assert child.line_item != anchor.line_item
    assert all(question.question_text.strip() for question in registry.current_version.questions)


def test_question_source_anchor_cell_distinguishes_merged_and_unmerged_rows() -> None:
    registry = import_fdd_workbook(production_fdd_workbook_bytes())
    by_coordinate = {
        (question.source_trace.sheet_or_section, question.source_trace.row_number): question
        for question in registry.current_version.questions
    }

    assert by_coordinate[("Liabilities", 4)].source_trace.source_anchor_cell == "E4"
    assert by_coordinate[("Liabilities", 5)].source_trace.source_anchor_cell == "E4"
    assert by_coordinate[("Liabilities", 6)].source_trace.source_anchor_cell == "E4"
    assert by_coordinate[("Liabilities", 17)].source_trace.source_anchor_cell == "E17"
    assert by_coordinate[("Liabilities", 18)].source_trace.source_anchor_cell == "E17"
    assert by_coordinate[("P&L", 4)].source_trace.source_anchor_cell == "E4"
    assert by_coordinate[("Cash Flow", 4)].source_trace.source_anchor_cell == "D4"
    assert by_coordinate[("Assets", 4)].source_trace.source_anchor_cell == "E4"


def test_production_import_uses_safe_source_alias_not_filename_or_path(tmp_path: Path) -> None:
    private_name = "private-operator-methodology-name.xlsx"
    workbook_path = tmp_path / private_name
    workbook_path.write_bytes(production_fdd_workbook_bytes())

    registry = import_fdd_workbook(workbook_path)
    serialized = registry.to_deterministic_json()

    assert registry.current_version.source_name == "configured-fdd-workbook"
    assert {
        question.source_trace.source_name for question in registry.current_version.questions
    } == {"configured-fdd-workbook"}
    assert private_name not in serialized
    assert str(workbook_path) not in serialized


def test_observed_cash_flow_row_16_may_have_an_empty_line_item() -> None:
    registry = import_fdd_workbook(production_fdd_workbook_bytes())
    cash_flow = [
        question
        for question in registry.current_version.questions
        if question.source_trace.sheet_or_section == "Cash Flow"
    ]
    rows_with_empty_line_item = {
        question.source_trace.row_number for question in cash_flow if not question.line_item
    }

    assert rows_with_empty_line_item == {CASH_FLOW_EMPTY_LINE_ITEM_ROW}


@pytest.mark.parametrize("row_delta", [-1, 1], ids=["78-rows", "80-rows"])
def test_78_or_80_rows_fail_closed_with_stable_code(row_delta: int) -> None:
    _assert_import_invalid(production_fdd_workbook_bytes(row_delta=row_delta))


@pytest.mark.parametrize("header_row", [1, 3], ids=["header-row-1", "header-row-3"])
def test_header_must_be_exactly_excel_row_2(header_row: int) -> None:
    _assert_import_invalid(production_fdd_workbook_bytes(header_row=header_row))


def test_semantically_duplicate_rows_fail_even_when_row_numbers_differ() -> None:
    _assert_import_invalid(production_fdd_workbook_bytes(duplicate_row=True))


def test_missing_sheet_fails_with_the_common_import_code() -> None:
    _assert_import_invalid(production_fdd_workbook_bytes(missing_sheet="Assets"))


def test_extra_sheet_fails_with_the_common_import_code() -> None:
    _assert_import_invalid(production_fdd_workbook_bytes(extra_sheet=True))


def test_reordered_sheets_fail_with_the_common_import_code() -> None:
    _assert_import_invalid(
        production_fdd_workbook_bytes(sheet_order=("Cash Flow", "P&L", "Liabilities", "Assets"))
    )


def test_populated_row_missing_line_item_trace_fails_closed() -> None:
    _assert_import_invalid(production_fdd_workbook_bytes(missing_line_item_trace=True))


def test_question_identity_binds_the_complete_workbook_hash() -> None:
    first = import_fdd_workbook(production_fdd_workbook_bytes(source_marker="fixture-v1"))
    second = import_fdd_workbook(production_fdd_workbook_bytes(source_marker="fixture-v2"))

    assert first.current_version.source_hash != second.current_version.source_hash
    assert (
        first.current_version.questions[0].methodology_question_id
        != second.current_version.questions[0].methodology_question_id
    )


def test_path_import_is_read_only_and_hash_stable(tmp_path: Path) -> None:
    workbook_path = tmp_path / "fictional-fdd.xlsx"
    workbook_path.write_bytes(production_fdd_workbook_bytes())
    before = hashlib.sha256(workbook_path.read_bytes()).hexdigest()

    registry = import_fdd_workbook(workbook_path)

    after = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    assert before == after == registry.current_version.source_hash


def test_operator_configured_real_workbook_matches_safe_contract() -> None:
    configured = os.environ.get("IDIS_FDD_METHODOLOGY_PATH", "").strip()
    if not configured:
        pytest.skip("operator FDD workbook is not configured")
    path = Path(configured)
    if not path.is_file():
        pytest.skip("operator FDD workbook is unavailable")

    before_bytes = path.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    registry = import_fdd_workbook(path)
    after_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    assert before_hash == EXPECTED_REAL_WORKBOOK_SHA256
    assert after_hash == before_hash
    assert len(registry.current_version.questions) == 79
    assert registry.current_version.source_hash == before_hash
