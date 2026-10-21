"""Derived-record and report models for the obligation register."""

from __future__ import annotations

from enum import StrEnum
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, model_validator

from idis.methodology.obligations.vocab import (
    CellRole,
    FindingSeverity,
    ParentKind,
)


class ObligationModel(BaseModel):
    """Strict frozen record. Extra keys are rejected and values are not coerced."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="wrap")
    @classmethod
    def _enums_from_stored_values(cls, value: object, handler):  # type: ignore[no-untyped-def]
        """Accept stored enum values and JSON lists while keeping other values strict."""
        if isinstance(value, dict):
            value = {
                key: _coerce_annotation(cls.model_fields[key].annotation, item)
                if key in cls.model_fields
                else item
                for key, item in value.items()
            }
        return handler(value)


class SourceCell(ObligationModel):
    """One stored cell or merge member on a production sheet."""

    cell_id: str
    sheet: str
    sheet_ordinal: int
    row: int
    column: int
    column_letter: str
    role: CellRole
    raw_text: str | None
    raw_sha256: str | None
    merge_range: str | None = None
    merge_anchor: str | None = None
    is_merge_anchor: bool = False
    resolved_from: str | None = None
    edge_whitespace: bool
    newline_count: int


class CellRef(ObligationModel):
    """Trace from a parent role back to the cell that holds it."""

    cell_id: str
    merge_anchor: str | None = None
    merge_range: str | None = None


class ReportMappingRef(ObligationModel):
    """Template report mapping copied onto a commercial parent."""

    report_section: str
    report_subsection: str | None = None


class CommercialContext(ObligationModel):
    """Commercial template fields that are not workbook text."""

    report_mapping: ReportMappingRef
    template_role: tuple[str, ...]
    target_document_categories: tuple[str, ...]
    governed_categories: tuple[str, ...]


class ParentRecord(ObligationModel):
    """One financial or commercial parent item."""

    parent_id: str
    public_coordinate: str
    parent_kind: ParentKind
    methodology_version_id: str
    source_hash: str
    sheet: str | None = None
    source_row: int | None = None
    row_ordinal: int
    sheet_ordinal: int | None = None
    term_raw: str | None = None
    nature_raw: str | None = None
    line_item_raw: str | None = None
    question_raw: str
    term_cell: CellRef | None = None
    nature_cell: CellRef | None = None
    line_item_cell: CellRef | None = None
    question_cell: CellRef | None = None
    question_raw_sha256: str
    question_normalized: str
    question_normalized_sha256: str
    merged_instruction_ids: tuple[str, ...] = ()
    edge_whitespace_roles: tuple[str, ...] = ()
    commercial_context: CommercialContext | None = None


class MergedInstruction(ObligationModel):
    """A question-column merge that spans more than one row."""

    instruction_id: str
    sheet: str
    sheet_code: str
    anchor_cell: str
    merge_range: str
    covered_rows: tuple[int, ...]
    covered_parent_ids: tuple[str, ...]


class PolicyAnchorView(ObligationModel):
    """One formula anchor sitting on a parent row."""

    formula_ordinal: int
    formula_type: str


class PolicyBindingView(ObligationModel):
    """One source binding that cites a parent row."""

    formula_ordinal: int
    binding_ordinal: int
    role: str
    predicate: str | None = None
    sign_transform: str | None = None
    cardinality: str | None = None
    period_role: str | None = None


class PolicyViewRecord(ObligationModel):
    """Anchors and source bindings that cite one parent."""

    parent_id: str
    anchors: tuple[PolicyAnchorView, ...] = ()
    bindings: tuple[PolicyBindingView, ...] = ()


class EdgeWhitespaceCell(ObligationModel):
    """Cell id and role for a raw value whose edges differ from its stripped form."""

    cell_id: str
    role: CellRole


class SheetCount(ObligationModel):
    """One sheet and an integer count."""

    sheet: str
    count: int


class FeatureCensus(ObligationModel):
    """Workbook features that can carry instructions outside plain cell text."""

    comments: int = 0
    hyperlinks: int = 0
    hidden_rows: int = 0
    hidden_columns: int = 0
    data_validations: int = 0
    conditional_formats: int = 0
    images: int = 0
    defined_names: int = 0
    formula_cells: int = 0
    non_string_stored_values: int = 0
    non_visible_sheets: int = 0
    other_role_stored_cells: int = 0


class InputFingerprint(ObligationModel):
    """Hash and byte count of one extractor input."""

    sha256: str
    byte_count: int


class ExtractionReport(ObligationModel):
    """Private census written beside the derived JSONL files."""

    register_schema: str
    register_version: str
    workbook: InputFingerprint
    template: InputFingerprint
    category_map: InputFingerprint
    policy: InputFingerprint | None = None
    sheet_names: tuple[str, ...]
    data_row_counts: tuple[SheetCount, ...]
    merged_range_counts: tuple[SheetCount, ...]
    feature_census: FeatureCensus
    edge_whitespace_cells: tuple[EdgeWhitespaceCell, ...] = ()
    parity_failure_ids: tuple[str, ...] = ()
    anchor_count: int = 0
    source_binding_count: int = 0
    non_source_input_spec_count: int = 0
    finding_codes: tuple[str, ...] = ()


class Finding(ObligationModel):
    """One validator result. The message is a fixed template of ids and counts."""

    code: str
    severity: FindingSeverity
    scope: str
    ids: tuple[str, ...] = ()
    count: int
    message: str


class ValidationReport(ObligationModel):
    """Result of validating a private root."""

    findings: tuple[Finding, ...]
    skipped_checks: tuple[str, ...]
    register_complete: bool
    skipped_invariants: tuple[tuple[str, str], ...] = ()


def _coerce_annotation(annotation: object, value: object) -> object:
    if value is None:
        return None
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        for option in get_args(annotation):
            if option is type(None):
                continue
            try:
                return _coerce_annotation(option, value)
            except ValueError:
                continue
        return value
    if origin is tuple and isinstance(value, list):
        inner = get_args(annotation)[0] if get_args(annotation) else str
        return tuple(_coerce_annotation(inner, item) for item in value)
    if isinstance(annotation, type) and issubclass(annotation, StrEnum) and isinstance(value, str):
        return annotation(value)
    return value
