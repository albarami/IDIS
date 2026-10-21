"""Extraction census on fictional workbooks."""

from __future__ import annotations

from pathlib import Path

from idis.methodology.obligations.extract import extract_register
from idis.methodology.obligations.jsonl import read_jsonl
from idis.methodology.obligations.models_records import ParentRecord, SourceCell
from idis.methodology.obligations.vocab import (
    EXPECTED_DATA_ROW_COUNTS,
    EXPECTED_MERGED_RANGE_COUNTS,
    Code,
    ParentKind,
)
from tests.gl02_register.support import (
    feature_workbook_bytes,
    structural_workbook_bytes,
    whitespace_workbook_bytes,
)
from tests.slice100_task5._workbook_factory import production_fdd_workbook_bytes


def test_structural_extract_is_deterministic(tmp_path: Path) -> None:
    workbook = tmp_path / "fictional.xlsx"
    workbook.write_bytes(structural_workbook_bytes())
    first = tmp_path / "one"
    second = tmp_path / "two"
    first_report = extract_register(workbook, first)
    second_report = extract_register(workbook, second)
    assert first_report.model_dump() == second_report.model_dump()
    for name in (
        "source_cells.jsonl",
        "parents.jsonl",
        "merged_instructions.jsonl",
        "policy_view.jsonl",
        "extraction_report.private.json",
    ):
        assert (first / "derived" / name).read_bytes() == (second / "derived" / name).read_bytes()
    counts = {item.sheet: item.count for item in first_report.merged_range_counts}
    assert counts == EXPECTED_MERGED_RANGE_COUNTS
    rows = {item.sheet: item.count for item in first_report.data_row_counts}
    assert rows == EXPECTED_DATA_ROW_COUNTS
    assert first_report.parity_failure_ids == ()
    assert Code.GL02_IMPORTER_PARITY_FAILED.value not in first_report.finding_codes


def test_merge_children_resolve_from_the_anchor(tmp_path: Path) -> None:
    workbook = tmp_path / "fictional.xlsx"
    workbook.write_bytes(structural_workbook_bytes())
    root = tmp_path / "root"
    extract_register(workbook, root)
    cells = {
        cell.cell_id: cell
        for cell in read_jsonl(root / "derived" / "source_cells.jsonl", SourceCell)
    }
    child = cells["Liabilities!E5"]
    assert child.raw_text is None
    assert child.resolved_from == "Liabilities!E4"
    assert child.is_merge_anchor is False
    anchor = cells["Liabilities!E4"]
    assert anchor.is_merge_anchor is True
    assert anchor.raw_text
    parents = read_jsonl(root / "derived" / "parents.jsonl", ParentRecord)
    financial = [parent for parent in parents if parent.parent_kind is ParentKind.FINANCIAL]
    commercial = [parent for parent in parents if parent.parent_kind is ParentKind.COMMERCIAL]
    assert len(financial) == 79
    assert len(commercial) == 21
    assert financial[0].public_coordinate == "FDD-PL-004"
    assert commercial[0].public_coordinate.startswith("CDD-")
    covered = [
        parent
        for parent in financial
        if parent.public_coordinate in {"FDD-LIAB-004", "FDD-LIAB-005", "FDD-LIAB-006"}
    ]
    assert covered
    assert all(parent.merged_instruction_ids for parent in covered)


def test_factory_merge_children_are_empty_and_match_the_importer(tmp_path: Path) -> None:
    """This openpyxl build clears merged children, so the factory workbook matches the importer."""
    workbook = tmp_path / "fictional.xlsx"
    workbook.write_bytes(production_fdd_workbook_bytes())
    report = extract_register(workbook, tmp_path / "root")
    assert report.parity_failure_ids == ()


def test_feature_census_and_outside_cell(tmp_path: Path) -> None:
    workbook = tmp_path / "fictional.xlsx"
    workbook.write_bytes(feature_workbook_bytes())
    report = extract_register(workbook, tmp_path / "root")
    census = report.feature_census
    assert census.comments == 1
    assert census.hidden_rows == 1
    assert census.non_string_stored_values == 1
    assert census.other_role_stored_cells >= 1
    assert Code.GL02_SOURCE_FEATURE_PRESENT.value in report.finding_codes
    assert Code.GL02_SOURCE_OTHER_CELL.value in report.finding_codes


def test_edge_whitespace_is_recorded_without_text(tmp_path: Path) -> None:
    workbook = tmp_path / "fictional.xlsx"
    workbook.write_bytes(whitespace_workbook_bytes())
    report = extract_register(workbook, tmp_path / "root")
    roles = {item.role.value for item in report.edge_whitespace_cells}
    assert {"term", "nature", "line_item", "question"} <= roles
    assert all(" " not in item.cell_id for item in report.edge_whitespace_cells)
    cells = read_jsonl(tmp_path / "root" / "derived" / "source_cells.jsonl", SourceCell)
    assert any(cell.newline_count for cell in cells)
