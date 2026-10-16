"""Deterministic, source-bound Financial DD workbook importer."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from idis.methodology.ids import generate_methodology_question_id, normalize_text
from idis.methodology.models import (
    AssignedAgent,
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologySourceTrace,
    MethodologyType,
    MethodologyVersion,
    RedFlagRule,
    ReportMapping,
    RequiredCalculation,
    RequiredEvidence,
)

REQUIRED_SHEETS = ("P&L", "Cash Flow", "Liabilities", "Assets")
EXPECTED_ROW_COUNTS: dict[str, int] = {
    "P&L": 29,
    "Cash Flow": 13,
    "Liabilities": 21,
    "Assets": 16,
}
HEADER_ROW = 2
FIRST_DATA_ROW = 4
PRODUCTION_SOURCE_NAME = "configured-fdd-workbook"
PRODUCTION_HEADERS: dict[str, tuple[str, ...]] = {
    "P&L": ("", "Term", "Nature", "Profit & Loss line", "Usual Due Diligence Questions"),
    "Cash Flow": ("", "Term", "Cash Flow line", "Usual Due Diligence Questions"),
    "Liabilities": (
        "",
        "Term",
        "Nature",
        "Balance Sheet line",
        "Usual Due Diligence Questions",
    ),
    "Assets": (
        "",
        "Term",
        "Nature",
        "Balance Sheet line",
        "Usual Due Diligence Questions",
    ),
}
PRODUCTION_COLUMNS: dict[str, dict[str, int | None]] = {
    "P&L": {"term": 2, "nature": 3, "line_item": 4, "question": 5},
    "Cash Flow": {"term": 2, "nature": None, "line_item": 3, "question": 4},
    "Liabilities": {"term": 2, "nature": 3, "line_item": 4, "question": 5},
    "Assets": {"term": 2, "nature": 3, "line_item": 4, "question": 5},
}

# Retained solely for the pre-Task-5 synthetic unit fixtures.  This shape is
# distinct from, and can never be frozen as, the production operator workbook.
LEGACY_COLUMNS = (
    "Term",
    "Nature",
    "Financial statement line",
    "Usual Due Diligence Questions",
)


class FDDImporterError(ValueError):
    """Raised when an FDD workbook cannot be imported safely."""

    code = "FDD_IMPORT_INVALID"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "FDD_IMPORT_INVALID"


def import_fdd_workbook(workbook: bytes | Path | str | BinaryIO) -> MethodologyRegistry:
    """Import a production FDD workbook or the isolated legacy test dialect."""
    workbook_bytes, source_name, source_path = _read_workbook(workbook)
    source_hash = hashlib.sha256(workbook_bytes).hexdigest()
    try:
        loaded = load_workbook(BytesIO(workbook_bytes), data_only=True, read_only=False)
    except Exception as exc:
        raise FDDImporterError("invalid workbook shape") from exc

    try:
        _require_exact_sheet_inventory(loaded.sheetnames)
        if _has_production_header_marker(loaded):
            registry = _import_production_workbook(
                loaded,
                source_hash=source_hash,
            )
        elif _has_legacy_header_marker(loaded):
            registry = _import_legacy_fixture(
                loaded,
                source_hash=source_hash,
                source_name=source_name,
            )
        else:
            raise FDDImporterError("invalid workbook shape: header row must be 2")
    except FDDImporterError:
        raise
    except Exception as exc:
        raise FDDImporterError("invalid workbook shape") from exc
    finally:
        loaded.close()

    if source_path is not None:
        try:
            after_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise FDDImporterError("source workbook changed during import") from exc
        if after_hash != source_hash:
            raise FDDImporterError("source workbook changed during import")
    return registry


def _import_production_workbook(
    loaded: Workbook,
    *,
    source_hash: str,
) -> MethodologyRegistry:
    questions: list[MethodologyQuestion] = []
    seen_semantic_rows: set[tuple[str, ...]] = set()
    version_id = f"financial_dd:{source_hash}"

    for sheet_name in REQUIRED_SHEETS:
        sheet = loaded[sheet_name]
        _require_exact_header(sheet, sheet_name)
        _require_blank_separator_row(sheet)
        rows = _production_rows(sheet, sheet_name)
        if len(rows) != EXPECTED_ROW_COUNTS[sheet_name]:
            raise FDDImporterError(
                f"invalid row count in {sheet_name}: expected {EXPECTED_ROW_COUNTS[sheet_name]}"
            )
        for row_number, term, nature, line_item, question_text, question_anchor in rows:
            semantic_key = tuple(
                normalize_text(value)
                for value in (sheet_name, term, nature or "", line_item or "", question_text)
            )
            if semantic_key in seen_semantic_rows:
                raise FDDImporterError("duplicate methodology_question_id")
            seen_semantic_rows.add(semantic_key)
            questions.append(
                _question(
                    source_hash=source_hash,
                    source_name=PRODUCTION_SOURCE_NAME,
                    source_type="operator_workbook",
                    version_id=version_id,
                    sheet_name=sheet_name,
                    row_number=row_number,
                    term=term,
                    nature=nature,
                    line_item=line_item,
                    question_text=question_text,
                    source_anchor_cell=question_anchor,
                )
            )

    if len(questions) != 79:
        raise FDDImporterError("invalid row count: expected 79")
    return _registry(
        source_hash=source_hash,
        source_name=PRODUCTION_SOURCE_NAME,
        source_type="operator_workbook",
        version_label="operator-workbook-v1",
        questions=questions,
    )


def _production_rows(
    sheet: Worksheet,
    sheet_name: str,
) -> list[tuple[int, str, str | None, str | None, str, str]]:
    columns = PRODUCTION_COLUMNS[sheet_name]
    rows: list[tuple[int, str, str | None, str | None, str, str]] = []
    for row_number in range(FIRST_DATA_ROW, sheet.max_row + 1):
        term = _resolved_cell(sheet, row_number, columns["term"])
        nature = _resolved_cell(sheet, row_number, columns["nature"])
        line_item = _resolved_cell(sheet, row_number, columns["line_item"])
        question_text, question_anchor = _resolved_cell_with_anchor(
            sheet, row_number, columns["question"]
        )
        if not any((term, nature, line_item, question_text)):
            continue
        if not term:
            raise FDDImporterError(f"missing term trace at {sheet_name}:{row_number}")
        if sheet_name != "Cash Flow" and not nature:
            raise FDDImporterError(f"missing nature trace at {sheet_name}:{row_number}")
        if not question_text:
            raise FDDImporterError(f"empty question text at {sheet_name}:{row_number}")
        if not line_item and not (sheet_name == "Cash Flow" and row_number == 16):
            raise FDDImporterError(f"missing line item trace at {sheet_name}:{row_number}")
        rows.append(
            (
                row_number,
                term,
                nature or None,
                line_item or None,
                question_text,
                question_anchor,
            )
        )
    return rows


def _resolved_cell(sheet: Worksheet, row_number: int, column: int | None) -> str:
    return _resolved_cell_with_anchor(sheet, row_number, column)[0]


def _resolved_cell_with_anchor(
    sheet: Worksheet,
    row_number: int,
    column: int | None,
) -> tuple[str, str]:
    if column is None:
        return "", ""
    anchor_row = row_number
    anchor_column = column
    value = sheet.cell(row=row_number, column=column).value
    if value is None:
        for merged in sheet.merged_cells.ranges:
            if (
                merged.min_row <= row_number <= merged.max_row
                and merged.min_col <= column <= merged.max_col
            ):
                anchor_row = merged.min_row
                anchor_column = merged.min_col
                value = sheet.cell(row=merged.min_row, column=merged.min_col).value
                break
    anchor_cell = f"{get_column_letter(anchor_column)}{anchor_row}"
    return ("" if value is None else str(value).strip()), anchor_cell


def _require_exact_header(sheet: Worksheet, sheet_name: str) -> None:
    expected = PRODUCTION_HEADERS[sheet_name]
    observed = tuple(
        ""
        if sheet.cell(row=HEADER_ROW, column=index).value is None
        else str(sheet.cell(row=HEADER_ROW, column=index).value).strip()
        for index in range(1, sheet.max_column + 1)
    )
    if observed != expected:
        raise FDDImporterError(f"invalid header row in {sheet_name}")


def _require_blank_separator_row(sheet: Worksheet) -> None:
    if any(_plain_cell(sheet, 3, column) for column in range(1, sheet.max_column + 1)):
        raise FDDImporterError("invalid workbook shape: row 3 must be blank")


def _plain_cell(sheet: Worksheet, row_number: int, column: int) -> str:
    value = sheet.cell(row=row_number, column=column).value
    return "" if value is None else str(value).strip()


def _has_production_header_marker(loaded: Workbook) -> bool:
    for sheet_name in REQUIRED_SHEETS:
        sheet = loaded[sheet_name]
        values = {
            str(sheet.cell(row=HEADER_ROW, column=column).value).strip()
            for column in range(1, sheet.max_column + 1)
            if sheet.cell(row=HEADER_ROW, column=column).value is not None
        }
        if "Term" in values or "Usual Due Diligence Questions" in values:
            return True
    return False


def _has_legacy_header_marker(loaded: Workbook) -> bool:
    for sheet_name in REQUIRED_SHEETS:
        sheet = loaded[sheet_name]
        values = {
            str(sheet.cell(row=1, column=column).value).strip()
            for column in range(1, sheet.max_column + 1)
            if sheet.cell(row=1, column=column).value is not None
        }
        if "Term" in values or "Usual Due Diligence Questions" in values:
            return True
    return False


def _import_legacy_fixture(
    loaded: Workbook,
    *,
    source_hash: str,
    source_name: str,
) -> MethodologyRegistry:
    questions: list[MethodologyQuestion] = []
    seen_semantic_rows: set[tuple[str, ...]] = set()
    version_id = f"financial_dd:{source_hash}"
    for sheet_name in REQUIRED_SHEETS:
        sheet = loaded[sheet_name]
        headers = [
            "" if value is None else str(value).strip()
            for value in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
        ]
        indexes = _legacy_column_indexes(headers, sheet_name)
        for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            term = _tuple_cell(row, indexes["Term"])
            nature = _tuple_cell(row, indexes["Nature"])
            line_item = _tuple_cell(row, indexes["Financial statement line"])
            question_text = _tuple_cell(row, indexes["Usual Due Diligence Questions"])
            if not question_text:
                raise FDDImporterError(f"empty question text at {sheet_name}:{row_number}")
            semantic_key = tuple(
                normalize_text(value)
                for value in (sheet_name, term, nature, line_item, question_text)
            )
            if semantic_key in seen_semantic_rows:
                raise FDDImporterError("duplicate methodology_question_id")
            seen_semantic_rows.add(semantic_key)
            questions.append(
                _question(
                    source_hash=source_hash,
                    source_name=source_name,
                    source_type="synthetic_workbook",
                    version_id=version_id,
                    sheet_name=sheet_name,
                    row_number=row_number,
                    term=term,
                    nature=nature or None,
                    line_item=line_item or None,
                    question_text=question_text,
                )
            )
    return _registry(
        source_hash=source_hash,
        source_name=source_name,
        source_type="synthetic_workbook",
        version_label="synthetic_excel",
        questions=questions,
    )


def _question(
    *,
    source_hash: str,
    source_name: str,
    source_type: str,
    version_id: str,
    sheet_name: str,
    row_number: int,
    term: str,
    nature: str | None,
    line_item: str | None,
    question_text: str,
    source_anchor_cell: str | None = None,
) -> MethodologyQuestion:
    question_id = generate_methodology_question_id(
        methodology_type=MethodologyType.FINANCIAL_DD,
        section=sheet_name,
        sheet_or_section=sheet_name,
        row_number=row_number,
        line_item=line_item or "",
        question_text=question_text,
        source_hash=source_hash,
    )
    return MethodologyQuestion(
        methodology_id="financial_dd",
        methodology_version_id=version_id,
        methodology_question_id=question_id,
        methodology_type=MethodologyType.FINANCIAL_DD,
        section=sheet_name,
        sheet_or_source_section=sheet_name,
        source_row_number=row_number,
        term=term,
        nature=nature,
        line_item=line_item,
        question_text=question_text,
        required_evidence=(
            RequiredEvidence(
                evidence_type="financial_support",
                description=f"Evidence supporting {line_item or sheet_name}",
            ),
        ),
        target_document_categories=("financial_statement", "workbook_support"),
        required_calculations=_required_calculations_for_sheet(sheet_name),
        assigned_agents=(
            AssignedAgent(
                role="financial_agent",
                responsibility=f"Answer FDD question for {sheet_name}",
            ),
        ),
        red_flag_rules=(
            RedFlagRule(
                rule_id=f"{sheet_name.lower().replace(' ', '_')}_missing_support",
                description="Question lacks supporting source evidence",
                severity="HIGH",
            ),
        ),
        report_mapping=ReportMapping(report_section=sheet_name),
        validation_requirements=("requires_claim_or_evidence",),
        source_trace=MethodologySourceTrace(
            source_type=source_type,
            source_name=source_name,
            source_hash=source_hash,
            sheet_or_section=sheet_name,
            row_number=row_number,
            source_anchor_cell=source_anchor_cell,
        ),
    )


def _registry(
    *,
    source_hash: str,
    source_name: str,
    source_type: str,
    version_label: str,
    questions: list[MethodologyQuestion],
) -> MethodologyRegistry:
    version_id = f"financial_dd:{source_hash}"
    ordered = tuple(
        sorted(
            questions,
            key=lambda question: (
                REQUIRED_SHEETS.index(question.source_trace.sheet_or_section),
                question.source_trace.row_number or 0,
                question.methodology_question_id,
            ),
        )
    )
    version = MethodologyVersion(
        methodology_id="financial_dd",
        methodology_version_id=version_id,
        methodology_type=MethodologyType.FINANCIAL_DD,
        version_label=version_label,
        source_hash=source_hash,
        source_name=source_name,
        questions=ordered,
    )
    # Keep the source kind reachable only through row traces; the registry itself
    # intentionally carries no path or operator profile detail.
    _ = source_type
    return MethodologyRegistry(
        methodology_id="financial_dd",
        methodology_type=MethodologyType.FINANCIAL_DD,
        versions=(version,),
    )


def _read_workbook(
    workbook: bytes | Path | str | BinaryIO,
) -> tuple[bytes, str, Path | None]:
    if isinstance(workbook, bytes):
        return workbook, "workbook_bytes.xlsx", None
    if isinstance(workbook, (Path, str)):
        path = Path(workbook)
        try:
            return path.read_bytes(), path.name, path
        except OSError as exc:
            raise FDDImporterError("invalid workbook source") from exc
    try:
        return workbook.read(), "workbook_stream.xlsx", None
    except Exception as exc:
        raise FDDImporterError("invalid workbook source") from exc


def _require_exact_sheet_inventory(sheet_names: list[str]) -> None:
    observed = tuple(sheet_names)
    missing = [name for name in REQUIRED_SHEETS if name not in observed]
    if missing:
        raise FDDImporterError(f"missing required sheet: {missing[0]}")
    if observed != REQUIRED_SHEETS:
        raise FDDImporterError("invalid workbook shape: sheet order or inventory")


def _legacy_column_indexes(headers: list[str], sheet_name: str) -> dict[str, int]:
    indexes: dict[str, int] = {}
    for required in LEGACY_COLUMNS:
        if required not in headers:
            raise FDDImporterError(f"missing required column {required} in {sheet_name}")
        indexes[required] = headers.index(required)
    return indexes


def _tuple_cell(row: tuple[object, ...], index: int) -> str:
    if index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def _required_calculations_for_sheet(sheet_name: str) -> tuple[RequiredCalculation, ...]:
    if sheet_name == "P&L":
        return (RequiredCalculation(calc_type="GROSS_MARGIN", required=False),)
    if sheet_name == "Cash Flow":
        return (RequiredCalculation(calc_type="BURN_RATE", required=False),)
    return ()
