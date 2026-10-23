"""Materialize the reviewed, inactive Task-8 FDD formula-policy artifact.

The generator is deliberately hermetic with respect to IDIS runtime code and
the Task-8 tests.  It uses only the Python standard library, the pinned OOXML
workbook bytes, and the literal reviewed policy inputs below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import unicodedata
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = Path(r"C:\Projects\IDIS\IDIS\financial Due Diligence.xlsx")
POLICY_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v1.json"
POLICY_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v1_hash_v1.json"
)
REVIEW_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v1-review-map.md"
)

WORKBOOK_SHA256 = "a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352"
WORKBOOK_BYTE_COUNT = 24_905
WORKBOOK_SAFE_ROW_COORDINATE_DIGEST = (
    "fab5ab40ab300602b5d9ee59eeb9c86451048d02e419054b0675e753a0ab0bc0"
)
WORKBOOK_SAFE_HEADER_DIGEST = "88c4ea73409b768686dd9dce7d05efe3ccb846bdb13850499caddcd55bc3229c"
POLICY_ID = "fdd-formula-policy-v1"
CANONICALIZATION_ID = "idis-task7-canonical-json-sha256-v1"
FORMULA_ENGINE_ID = "fdd-formula-engine-v1"

SHEETS = ("P&L", "Cash Flow", "Liabilities", "Assets")
SHEET_COUNTS = {"P&L": 29, "Cash Flow": 13, "Liabilities": 21, "Assets": 16}
SHEET_COLUMNS: dict[str, dict[str, int | None]] = {
    "P&L": {"term": 2, "nature": 3, "line_item": 4, "question_text": 5},
    "Cash Flow": {"term": 2, "nature": None, "line_item": 3, "question_text": 4},
    "Liabilities": {"term": 2, "nature": 3, "line_item": 4, "question_text": 5},
    "Assets": {"term": 2, "nature": 3, "line_item": 4, "question_text": 5},
}
FORMULA_ANCHORS = (
    (1, "GROSS_MARGIN", "P&L", 11, "line_item"),
    (2, "FDD_EBITDA_BRIDGE", "P&L", 23, "p_and_l_control"),
    (3, "FDD_EBIT_BRIDGE", "P&L", 27, "p_and_l_control"),
    (4, "FDD_TAX_RECONCILIATION", "P&L", 30, "p_and_l_control"),
    (5, "FDD_NET_INCOME_BRIDGE", "P&L", 32, "p_and_l_control"),
    (6, "FDD_WORKING_CAPITAL_MOVEMENT", "Cash Flow", 8, "cash_flow_control"),
    (7, "FDD_FREE_CASH_FLOW_CASH_BRIDGE", "Cash Flow", 16, "cash_flow_control"),
    (8, "FDD_DEBT_ROLL_FORWARD", "Cash Flow", 11, "debt_control"),
    (9, "FDD_ASSET_TIE_OUT", "Assets", 19, "sheet_control"),
    (10, "FDD_LIABILITY_TIE_OUT", "Liabilities", 24, "sheet_control"),
)

HASHED_COMPONENTS = (
    "metadata",
    "canonicalization_contract",
    "workbook_authority",
    "workbook_row_registry",
    "methodology_transition",
    "formula_registry",
    "debt_scope_exclusions",
    "completeness_request_registry",
    "direct_signed_convention_registry",
    "incomplete_binding_failure_schema",
    "financial_output_schema",
    "activation_assertions",
)

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"m": _MAIN_NS, "r": _REL_NS, "p": _PKG_REL_NS}


class MaterializationError(ValueError):
    """Raised with a stable safe code when materialization cannot proceed."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise MaterializationError(code)


def _validate_canonical_value(value: Any) -> None:
    if isinstance(value, float):
        raise MaterializationError("TASK8_POLICY_FLOAT_FORBIDDEN")
    if isinstance(value, str):
        _require(
            unicodedata.normalize("NFC", value) == value,
            "TASK8_POLICY_UNICODE_NOT_NFC",
        )
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, list):
        for item in value:
            _validate_canonical_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _require(isinstance(key, str), "TASK8_POLICY_NON_STRING_KEY")
            _validate_canonical_value(key)
            _validate_canonical_value(item)
        return
    raise MaterializationError("TASK8_POLICY_VALUE_TYPE_INVALID")


def _canonical_json(value: Any) -> bytes:
    _validate_canonical_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_canonical(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _render_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _question_id(
    *,
    sheet: str,
    source_row: int,
    line_item: str,
    question_text: str,
) -> str:
    seed = "|".join(
        [
            WORKBOOK_SHA256,
            "financial_dd",
            _normalize_text(sheet),
            _normalize_text(sheet),
            str(source_row),
            _normalize_text(line_item),
            _normalize_text(question_text),
        ]
    )
    return f"mq_financial_dd_{hashlib.sha256(seed.encode('utf-8')).hexdigest()}"


def _column_number(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference)
    _require(letters is not None, "TASK8_WORKBOOK_CELL_REFERENCE_INVALID")
    value = 0
    for character in letters.group(0):
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value


def _column_letters(column: int) -> str:
    result = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("m:si", _NS):
        values.append("".join(node.text or "" for node in item.findall(".//m:t", _NS)))
    return values


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall("p:Relationship", _NS)
    }
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall("m:sheets/m:sheet", _NS):
        name = sheet.attrib["name"]
        relationship_id = sheet.attrib[f"{{{_REL_NS}}}id"]
        target = targets[relationship_id].replace("\\", "/").lstrip("/")
        result.append((name, f"xl/{target}"))
    return result


def _cell_values(
    sheet_root: ElementTree.Element,
    shared_strings: Sequence[str],
) -> tuple[dict[tuple[int, int], str], int]:
    values: dict[tuple[int, int], str] = {}
    formula_count = 0
    for cell in sheet_root.findall(".//m:sheetData/m:row/m:c", _NS):
        reference = cell.attrib["r"]
        row_match = re.search(r"\d+$", reference)
        _require(row_match is not None, "TASK8_WORKBOOK_CELL_REFERENCE_INVALID")
        row = int(row_match.group(0))
        column = _column_number(reference)
        if cell.find("m:f", _NS) is not None:
            formula_count += 1
        cell_type = cell.attrib.get("t")
        value_node = cell.find("m:v", _NS)
        if cell_type == "inlineStr":
            value = "".join(node.text or "" for node in cell.findall(".//m:is/m:t", _NS))
        elif value_node is None:
            value = ""
        elif cell_type == "s":
            value = shared_strings[int(value_node.text or "0")]
        else:
            value = value_node.text or ""
        values[(row, column)] = value.strip()
    return values, formula_count


def _merged_anchors(sheet_root: ElementTree.Element) -> list[tuple[int, int, int, int]]:
    anchors: list[tuple[int, int, int, int]] = []
    for merged in sheet_root.findall("m:mergeCells/m:mergeCell", _NS):
        start, end = merged.attrib["ref"].split(":")
        start_row_match = re.search(r"\d+$", start)
        end_row_match = re.search(r"\d+$", end)
        _require(
            start_row_match is not None and end_row_match is not None,
            "TASK8_WORKBOOK_MERGE_REFERENCE_INVALID",
        )
        anchors.append(
            (
                int(start_row_match.group(0)),
                _column_number(start),
                int(end_row_match.group(0)),
                _column_number(end),
            )
        )
    return anchors


def _resolved_cell(
    values: Mapping[tuple[int, int], str],
    merges: Sequence[tuple[int, int, int, int]],
    source_row: int,
    column: int | None,
) -> tuple[str, str]:
    if column is None:
        return "", ""
    anchor_row = source_row
    anchor_column = column
    value = values.get((source_row, column), "")
    if not value:
        for min_row, min_column, max_row, max_column in merges:
            if min_row <= source_row <= max_row and min_column <= column <= max_column:
                anchor_row = min_row
                anchor_column = min_column
                value = values.get((anchor_row, anchor_column), "")
                break
    return value, f"{_column_letters(anchor_column)}{anchor_row}"


def _source_row_sha256(row: Mapping[str, Any]) -> str:
    return _sha256_canonical(
        [
            "fdd-workbook-row-v1",
            row["row_ordinal"],
            row["sheet_ordinal"],
            row["source_sheet"],
            row["source_row"],
            row["source_anchor_cell"],
            row["term"],
            row["nature"],
            row["line_item"],
            row["question_text"],
            row["methodology_question_id"],
        ]
    )


def _read_workbook_rows(workbook_path: Path) -> list[dict[str, Any]]:
    before = workbook_path.read_bytes()
    _require(len(before) == WORKBOOK_BYTE_COUNT, "TASK8_OPERATOR_WORKBOOK_SIZE_DRIFT")
    _require(
        _sha256_bytes(before) == WORKBOOK_SHA256,
        "TASK8_OPERATOR_WORKBOOK_HASH_DRIFT",
    )
    rows: list[dict[str, Any]] = []
    formula_count = 0
    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings = _read_shared_strings(archive)
        targets = _sheet_targets(archive)
        _require(
            tuple(name for name, _target in targets) == SHEETS,
            "TASK8_WORKBOOK_SHEET_DRIFT",
        )
        for sheet_ordinal, (sheet_name, target) in enumerate(targets, start=1):
            root = ElementTree.fromstring(archive.read(target))
            values, sheet_formula_count = _cell_values(root, shared_strings)
            merges = _merged_anchors(root)
            formula_count += sheet_formula_count
            sheet_rows: list[dict[str, Any]] = []
            maximum_row = max((row for row, _column in values), default=0)
            for source_row in range(4, maximum_row + 1):
                columns = SHEET_COLUMNS[sheet_name]
                term, _ = _resolved_cell(
                    values,
                    merges,
                    source_row,
                    columns["term"],
                )
                nature, _ = _resolved_cell(
                    values,
                    merges,
                    source_row,
                    columns["nature"],
                )
                line_item, _ = _resolved_cell(
                    values,
                    merges,
                    source_row,
                    columns["line_item"],
                )
                question_text, source_anchor_cell = _resolved_cell(
                    values,
                    merges,
                    source_row,
                    columns["question_text"],
                )
                if not any((term, nature, line_item, question_text)):
                    continue
                question_id = _question_id(
                    sheet=sheet_name,
                    source_row=source_row,
                    line_item=line_item,
                    question_text=question_text,
                )
                required = [
                    {
                        "formula_ordinal": ordinal,
                        "formula_type": formula_type,
                        "required": True,
                        "scope": scope,
                    }
                    for ordinal, formula_type, anchor_sheet, anchor_row, scope in (FORMULA_ANCHORS)
                    if (anchor_sheet, anchor_row) == (sheet_name, source_row)
                ]
                row = {
                    "row_ordinal": len(rows) + len(sheet_rows) + 1,
                    "sheet_ordinal": sheet_ordinal,
                    "source_sheet": sheet_name,
                    "source_row": source_row,
                    "source_anchor_cell": source_anchor_cell,
                    "term": term,
                    "nature": nature or None,
                    "line_item": line_item or None,
                    "question_text": question_text,
                    "methodology_question_id": question_id,
                    "required_calculations": required,
                }
                row["source_row_sha256"] = _source_row_sha256(row)
                sheet_rows.append(row)
            _require(
                len(sheet_rows) == SHEET_COUNTS[sheet_name],
                "TASK8_WORKBOOK_SHEET_ROW_COUNT_DRIFT",
            )
            rows.extend(sheet_rows)
    after = workbook_path.read_bytes()
    _require(before == after, "TASK8_OPERATOR_WORKBOOK_MUTATED_DURING_READ")
    _require(formula_count == 0, "TASK8_WORKBOOK_FORMULA_CELL_DRIFT")
    _require(len(rows) == 79, "TASK8_WORKBOOK_ROW_COUNT_DRIFT")
    return rows


DIRECT_SIGNED_CONVENTIONS = (
    (
        "fdd.reported_gross_margin_ratio",
        "reported gross profit as a positive fraction of revenue",
        "reported gross loss as a negative fraction",
    ),
    ("fdd.reported_ebitda", "reported EBITDA profit", "reported EBITDA loss"),
    (
        "fdd.other_non_cash_adjustment",
        "adjustment added in the EBIT bridge",
        "adjustment subtracted in the EBIT bridge",
    ),
    ("fdd.reported_ebit", "reported EBIT profit", "reported EBIT loss"),
    (
        "fdd.tax_reconciling_item",
        "increases calculated tax expense",
        "decreases calculated tax expense",
    ),
    (
        "fdd.deferred_tax_adjustment",
        "increases calculated tax expense",
        "decreases calculated tax expense",
    ),
    (
        "fdd.reported_tax_component",
        "reported tax expense",
        "reported tax benefit",
    ),
    ("fdd.reported_net_income", "reported net profit", "reported net loss"),
    (
        "fdd.reported_delta_nwc",
        "movement convention: closing operating NWC exceeds opening operating NWC",
        "movement convention: closing operating NWC is below opening operating NWC",
    ),
    (
        "fdd.nonoperating_working_capital_cash_effect",
        "cash inflow/increase",
        "cash outflow/decrease",
    ),
    (
        "fdd.reported_free_cash_flow",
        "positive free-cash-flow generation",
        "free-cash-flow outflow",
    ),
    (
        "fdd.bank_lease_financing_cash_flow",
        "financing cash inflow/draw",
        "financing repayment/outflow",
    ),
    (
        "fdd.other_financing_cash_flow",
        "financing cash inflow",
        "financing cash outflow",
    ),
    (
        "fdd.recapitalization_cash_flow",
        "recapitalization cash inflow",
        "recapitalization cash outflow",
    ),
    (
        "fdd.other_equity_cash_flow",
        "other equity cash inflow",
        "other equity cash outflow",
    ),
    (
        "fdd.reported_net_cash_flow",
        "reported increase in cash",
        "reported decrease in cash",
    ),
    (
        "fdd.debt_fx_other_noncash",
        "noncash change increasing closing debt",
        "noncash change decreasing closing debt",
    ),
)

COMPLETENESS_REQUESTS = (
    (
        "fdd.tax_reconciling_item_set_complete",
        "P&L",
        30,
        "Return Boolean true only when a cited tax reconciliation or tax schedule "
        "explicitly enumerates the complete set of permanent, rate, and discrete "
        "reconciling items for the stated period and ties that set to the reported "
        "current/deferred tax. If no cited document explicitly establishes "
        "completeness, return EVIDENCE_NOT_FOUND. Never infer completeness from "
        "silence, absence, a calculated residual, or model judgment.",
    ),
    (
        "fdd.debt_rollforward_component_set_complete",
        "Cash Flow",
        11,
        "Return Boolean true only when a cited loan or debt schedule explicitly "
        "enumerates all draws, capitalized interest or accrual, FX or other noncash "
        "changes, repayments, conversions, and writeoffs for the stated period and "
        "ties the schedule's opening and closing balances to Liabilities:9. If no "
        "cited document explicitly establishes completeness, return "
        "EVIDENCE_NOT_FOUND. Never infer completeness from silence, absence, "
        "balancing arithmetic, or model judgment.",
    ),
)


def _source(
    role: str,
    sheet: str,
    rows: int | Iterable[int],
    predicate: str,
    *,
    value_schema: str = "FddMonetaryValueV1",
    sign: str = "POSITIVE_MAGNITUDE",
    cardinality: str = "EXACTLY_ONE",
    period_role: str = "FLOW_RANGE",
    unit_rule: str = "COMMON_MONETARY_UNIT",
) -> list[dict[str, Any]]:
    row_tuple = (rows,) if isinstance(rows, int) else tuple(rows)
    return [
        {
            "binding_kind": "SOURCE",
            "role": role,
            "predicate": predicate,
            "source_sheet": sheet,
            "source_row": row,
            "value_schema": value_schema,
            "sign_transform": sign,
            "cardinality": cardinality,
            "period_role": period_role,
            "currency_rule": (
                None
                if value_schema in {"FddSignedRatioValueV1", "FddBooleanValueV1"}
                else "COMMON_ISO4217_AND_SCALE"
            ),
            "unit_rule": unit_rule,
            "direct_signed_convention_id": predicate if sign == "DIRECT_SIGNED" else None,
            "completeness_request_id": (predicate if value_schema == "FddBooleanValueV1" else None),
        }
        for row in row_tuple
    ]


def _parent(
    role: str,
    formula_type: str,
    *,
    coordinate_rule: str = "EXACT_SAME_COORDINATE",
) -> dict[str, Any]:
    return {
        "binding_kind": "PARENT_CALCULATION",
        "role": role,
        "parent_formula_type": formula_type,
        "coordinate_rule": coordinate_rule,
        "cardinality": "EXACTLY_ONE",
    }


def _output(
    name: str,
    unit: str,
    scale: int,
    *,
    primary: bool = False,
    comparison: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "unit": unit,
        "currency": None if unit == "ratio" else "COORDINATE_BASE_CURRENCY",
        "scale": scale,
        "primary": primary,
        "comparison": comparison,
    }


def _formula_sha256(formula: Mapping[str, Any]) -> str:
    return _sha256_canonical(
        [
            "fdd-formula-spec-v1",
            formula["formula_type"],
            formula["version"],
            formula["expression_id"],
            formula["expression_ast"],
            formula["dependency_tuple"],
            formula["ordered_input_specs"],
            formula["sign_rules"],
            formula["cardinality_rules"],
            formula["period_rule"],
            formula["currency_rule"],
            formula["unit_rule"],
            formula["denominator_rule"],
            formula["output_specs"],
            formula["precision"],
            formula["rounding"],
            formula["permitted_assumptions"],
        ]
    )


def _formula(
    ordinal: int,
    formula_type: str,
    expression_id: str,
    anchor_sheet: str,
    anchor_row: int,
    scope: str,
    bindings: Sequence[Mapping[str, Any]],
    dependencies: Sequence[str],
    expression_ast: list[Any],
    *,
    denominator_rule: Mapping[str, Any] | None = None,
    outputs: Sequence[Mapping[str, Any]],
    period_rule: str = "EXACT_FLOW_RANGE",
    disposition_rule: str = "EXACT_ZERO_VARIANCE_IN_BALANCE_ELSE_OUT_OF_BALANCE",
) -> dict[str, Any]:
    ordered_inputs = [
        {"binding_ordinal": index, **dict(binding)}
        for index, binding in enumerate(bindings, start=1)
    ]
    formula = {
        "formula_ordinal": ordinal,
        "formula_type": formula_type,
        "version": 1,
        "expression_id": expression_id,
        "anchor_sheet": anchor_sheet,
        "anchor_row": anchor_row,
        "scope": scope,
        "required": True,
        "expression_ast": expression_ast,
        "dependency_tuple": list(dependencies),
        "ordered_input_specs": ordered_inputs,
        "sign_rules": [
            [item["binding_ordinal"], item["sign_transform"]]
            for item in ordered_inputs
            if item["binding_kind"] == "SOURCE"
        ],
        "cardinality_rules": [
            [item["binding_ordinal"], item["cardinality"]] for item in ordered_inputs
        ],
        "period_rule": period_rule,
        "currency_rule": "EXACT_COMMON_ISO4217_NO_FX",
        "unit_rule": "EXACT_COMMON_SCALE_NO_CONVERSION",
        "denominator_rule": dict(denominator_rule) if denominator_rule else None,
        "output_specs": [dict(item) for item in outputs],
        "precision": 50,
        "rounding": "ROUND_HALF_UP",
        "permitted_assumptions": [],
        "disposition_rule": disposition_rule,
    }
    formula["formula_sha256"] = _formula_sha256(formula)
    return formula


def _formula_rows() -> list[dict[str, Any]]:
    formulas: list[dict[str, Any]] = []
    gross_bindings = [
        *_source("recurring_revenue", "P&L", 4, "fdd.recurring_revenue"),
        *_source("nonrecurring_revenue", "P&L", 5, "fdd.nonrecurring_revenue"),
        *_source(
            "cost_of_sales_components",
            "P&L",
            (7, 8, 9, 10),
            "fdd.cost_of_sales_component",
            cardinality="ORDERED_COMPONENT_SET",
        ),
        *_source(
            "reported_gross_margin_ratio",
            "P&L",
            11,
            "fdd.reported_gross_margin_ratio",
            value_schema="FddSignedRatioValueV1",
            sign="DIRECT_SIGNED",
            unit_rule="RATIO_FRACTION",
        ),
    ]
    formulas.append(
        _formula(
            1,
            "GROSS_MARGIN",
            "gross-margin-v1",
            "P&L",
            11,
            "line_item",
            gross_bindings,
            [],
            [
                [
                    "assign",
                    "operating_revenue",
                    [
                        "add",
                        ["input", "recurring_revenue"],
                        ["input", "nonrecurring_revenue"],
                    ],
                ],
                [
                    "assign",
                    "cost_of_sales",
                    ["sum", "cost_of_sales_components"],
                ],
                [
                    "assign",
                    "gross_profit",
                    [
                        "subtract",
                        ["value", "operating_revenue"],
                        ["value", "cost_of_sales"],
                    ],
                ],
                [
                    "assign",
                    "gross_margin_ratio",
                    [
                        "divide",
                        ["value", "gross_profit"],
                        ["value", "operating_revenue"],
                    ],
                ],
                [
                    "assign",
                    "gross_margin_ratio_variance",
                    [
                        "subtract",
                        ["value", "gross_margin_ratio"],
                        ["input", "reported_gross_margin_ratio"],
                    ],
                ],
            ],
            denominator_rule={
                "name": "operating_revenue",
                "constraint": "STRICTLY_POSITIVE",
            },
            outputs=[
                _output("gross_margin_ratio", "ratio", 4, primary=True),
                _output("gross_profit", "money", 2),
                _output("operating_revenue", "money", 2),
                _output("cost_of_sales", "money", 2),
                _output("reported_gross_margin_ratio", "ratio", 4),
                _output("gross_margin_ratio_variance", "ratio", 4, comparison=True),
            ],
        )
    )

    nwc_bindings = [
        *_source(
            "operating_current_assets",
            "Assets",
            (13, 14, 15, 16),
            "fdd.operating_current_asset_balance",
            cardinality="EXACTLY_TWO_OPENING_CLOSING",
            period_role="OPENING_AND_CLOSING_AS_OF",
        ),
        *_source(
            "operating_current_liabilities",
            "Liabilities",
            (12, 13, 14, 15, 16),
            "fdd.operating_current_liability_balance",
            cardinality="EXACTLY_TWO_OPENING_CLOSING",
            period_role="OPENING_AND_CLOSING_AS_OF",
        ),
        *_source(
            "reported_delta_nwc",
            "Cash Flow",
            8,
            "fdd.reported_delta_nwc",
            sign="DIRECT_SIGNED",
        ),
    ]
    formulas.append(
        _formula(
            6,
            "FDD_WORKING_CAPITAL_MOVEMENT",
            "fdd-working-capital-movement-v1",
            "Cash Flow",
            8,
            "cash_flow_control",
            nwc_bindings,
            [],
            [
                [
                    "assign",
                    "opening_operating_working_capital",
                    [
                        "subtract",
                        ["sum_opening", "operating_current_assets"],
                        ["sum_opening", "operating_current_liabilities"],
                    ],
                ],
                [
                    "assign",
                    "closing_operating_working_capital",
                    [
                        "subtract",
                        ["sum_closing", "operating_current_assets"],
                        ["sum_closing", "operating_current_liabilities"],
                    ],
                ],
                [
                    "assign",
                    "calculated_delta_nwc",
                    [
                        "subtract",
                        ["value", "closing_operating_working_capital"],
                        ["value", "opening_operating_working_capital"],
                    ],
                ],
                [
                    "assign",
                    "nwc_cash_effect",
                    ["negate", ["value", "calculated_delta_nwc"]],
                ],
                [
                    "assign",
                    "nwc_variance",
                    [
                        "subtract",
                        ["value", "calculated_delta_nwc"],
                        ["input", "reported_delta_nwc"],
                    ],
                ],
            ],
            period_rule="EXACT_OPENING_CLOSING_AS_OF_AND_MATCHING_FLOW_RANGE",
            outputs=[
                _output("calculated_delta_nwc", "money", 2, primary=True),
                _output("opening_operating_working_capital", "money", 2),
                _output("closing_operating_working_capital", "money", 2),
                _output("nwc_cash_effect", "money", 2),
                _output("reported_delta_nwc", "money", 2),
                _output("nwc_variance", "money", 2, comparison=True),
            ],
        )
    )

    cash_bindings = [
        *_source("cash_ebitda", "Cash Flow", 4, "fdd.cash_ebitda"),
        *_source(
            "cash_tax_and_nonrecurring",
            "Cash Flow",
            5,
            "fdd.cash_tax_and_nonrecurring",
        ),
        *_source("tangible_capex", "Cash Flow", 6, "fdd.tangible_capex"),
        *_source("intangible_capex", "Cash Flow", 7, "fdd.intangible_capex"),
        _parent("nwc_cash_effect", "FDD_WORKING_CAPITAL_MOVEMENT"),
        *_source(
            "nonoperating_working_capital_cash_effect",
            "Cash Flow",
            9,
            "fdd.nonoperating_working_capital_cash_effect",
            sign="DIRECT_SIGNED",
        ),
        *_source(
            "reported_free_cash_flow",
            "Cash Flow",
            10,
            "fdd.reported_free_cash_flow",
            sign="DIRECT_SIGNED",
        ),
        *_source(
            "bank_lease_financing_cash_flow",
            "Cash Flow",
            11,
            "fdd.bank_lease_financing_cash_flow",
            sign="DIRECT_SIGNED",
        ),
        *_source(
            "other_financing_cash_flow",
            "Cash Flow",
            12,
            "fdd.other_financing_cash_flow",
            sign="DIRECT_SIGNED",
        ),
        *_source(
            "recapitalization_cash_flow",
            "Cash Flow",
            13,
            "fdd.recapitalization_cash_flow",
            sign="DIRECT_SIGNED",
        ),
        *_source("dividends_paid", "Cash Flow", 14, "fdd.dividends_paid"),
        *_source(
            "other_equity_cash_flow",
            "Cash Flow",
            15,
            "fdd.other_equity_cash_flow",
            sign="DIRECT_SIGNED",
        ),
        *_source(
            "reported_net_cash_flow",
            "Cash Flow",
            16,
            "fdd.reported_net_cash_flow",
            sign="DIRECT_SIGNED",
        ),
        *_source(
            "unrestricted_cash_balance",
            "Assets",
            19,
            "fdd.unrestricted_cash_balance",
            cardinality="EXACTLY_TWO_OPENING_CLOSING",
            period_role="OPENING_AND_CLOSING_AS_OF",
        ),
    ]
    formulas.append(
        _formula(
            7,
            "FDD_FREE_CASH_FLOW_CASH_BRIDGE",
            "fdd-free-cash-flow-cash-bridge-v1",
            "Cash Flow",
            16,
            "cash_flow_control",
            cash_bindings,
            ["FDD_WORKING_CAPITAL_MOVEMENT"],
            [
                [
                    "assign",
                    "calculated_fcf",
                    [
                        "add",
                        [
                            "subtract",
                            [
                                "subtract",
                                [
                                    "subtract",
                                    ["input", "cash_ebitda"],
                                    ["input", "cash_tax_and_nonrecurring"],
                                ],
                                ["input", "tangible_capex"],
                            ],
                            ["input", "intangible_capex"],
                        ],
                        [
                            "add",
                            ["parent", "nwc_cash_effect"],
                            ["input", "nonoperating_working_capital_cash_effect"],
                        ],
                    ],
                ],
                [
                    "assign",
                    "calculated_net_cash_flow",
                    [
                        "add",
                        [
                            "subtract",
                            [
                                "add",
                                [
                                    "add",
                                    [
                                        "add",
                                        ["value", "calculated_fcf"],
                                        ["input", "bank_lease_financing_cash_flow"],
                                    ],
                                    ["input", "other_financing_cash_flow"],
                                ],
                                ["input", "recapitalization_cash_flow"],
                            ],
                            ["input", "dividends_paid"],
                        ],
                        ["input", "other_equity_cash_flow"],
                    ],
                ],
                [
                    "assign",
                    "calculated_closing_cash",
                    [
                        "add",
                        ["input_opening", "unrestricted_cash_balance"],
                        ["value", "calculated_net_cash_flow"],
                    ],
                ],
                [
                    "assign",
                    "free_cash_flow_variance",
                    [
                        "subtract",
                        ["value", "calculated_fcf"],
                        ["input", "reported_free_cash_flow"],
                    ],
                ],
                [
                    "assign",
                    "net_cash_flow_variance",
                    [
                        "subtract",
                        ["value", "calculated_net_cash_flow"],
                        ["input", "reported_net_cash_flow"],
                    ],
                ],
                [
                    "assign",
                    "closing_cash_variance",
                    [
                        "subtract",
                        ["value", "calculated_closing_cash"],
                        ["input_closing", "unrestricted_cash_balance"],
                    ],
                ],
            ],
            period_rule="EXACT_OPENING_CLOSING_AS_OF_AND_MATCHING_FLOW_RANGE",
            outputs=[
                _output("calculated_fcf", "money", 2, primary=True),
                _output("calculated_net_cash_flow", "money", 2),
                _output("calculated_closing_cash", "money", 2),
                _output("reported_free_cash_flow", "money", 2),
                _output("reported_net_cash_flow", "money", 2),
                _output("reported_closing_cash", "money", 2),
                _output("free_cash_flow_variance", "money", 2, comparison=True),
                _output("net_cash_flow_variance", "money", 2, comparison=True),
                _output("closing_cash_variance", "money", 2, comparison=True),
            ],
        )
    )

    debt_bindings = [
        *_source(
            "interest_bearing_debt_balance",
            "Liabilities",
            9,
            "fdd.interest_bearing_debt_balance",
            cardinality="EXACTLY_TWO_OPENING_CLOSING",
            period_role="OPENING_AND_CLOSING_AS_OF",
        ),
        *_source(
            "draws",
            "Cash Flow",
            (11, 12),
            "fdd.debt_draw",
            cardinality="ORDERED_ZERO_OR_MORE",
        ),
        *_source(
            "capitalized_interest",
            "Cash Flow",
            (11, 12),
            "fdd.debt_capitalized_interest",
            cardinality="ORDERED_ZERO_OR_MORE",
        ),
        *_source(
            "fx_and_other_noncash_changes",
            "Cash Flow",
            (11, 12),
            "fdd.debt_fx_other_noncash",
            sign="DIRECT_SIGNED",
            cardinality="ORDERED_ZERO_OR_MORE",
        ),
        *_source(
            "repayments",
            "Cash Flow",
            (11, 12),
            "fdd.debt_repayment",
            cardinality="ORDERED_ZERO_OR_MORE",
        ),
        *_source(
            "conversions_and_writeoffs",
            "Cash Flow",
            (11, 12),
            "fdd.debt_conversion_writeoff",
            cardinality="ORDERED_ZERO_OR_MORE",
        ),
        *_source(
            "movement_set_complete",
            "Cash Flow",
            11,
            "fdd.debt_rollforward_component_set_complete",
            value_schema="FddBooleanValueV1",
            cardinality="EXACTLY_ONE_TRUE",
            unit_rule="BOOLEAN",
        ),
    ]
    formulas.append(
        _formula(
            8,
            "FDD_DEBT_ROLL_FORWARD",
            "fdd-debt-roll-forward-v1",
            "Cash Flow",
            11,
            "debt_control",
            debt_bindings,
            [],
            [
                [
                    "assign",
                    "calculated_closing_debt",
                    [
                        "subtract",
                        [
                            "subtract",
                            [
                                "add",
                                [
                                    "add",
                                    [
                                        "add",
                                        ["input_opening", "interest_bearing_debt_balance"],
                                        ["sum", "draws"],
                                    ],
                                    ["sum", "capitalized_interest"],
                                ],
                                ["sum", "fx_and_other_noncash_changes"],
                            ],
                            ["sum", "repayments"],
                        ],
                        ["sum", "conversions_and_writeoffs"],
                    ],
                ],
                [
                    "assign",
                    "debt_variance",
                    [
                        "subtract",
                        ["value", "calculated_closing_debt"],
                        ["input_closing", "interest_bearing_debt_balance"],
                    ],
                ],
            ],
            period_rule="EXACT_OPENING_CLOSING_AS_OF_AND_MATCHING_FLOW_RANGE",
            outputs=[
                _output("calculated_closing_debt", "money", 2, primary=True),
                _output("reported_closing_debt", "money", 2),
                _output("debt_variance", "money", 2, comparison=True),
            ],
        )
    )

    ebitda_bindings = [
        *_source(
            "operating_income_components",
            "P&L",
            (4, 5, 6),
            "fdd.operating_income_component",
            cardinality="ORDERED_COMPONENT_SET",
        ),
        *_source(
            "cost_of_sales_components",
            "P&L",
            (7, 8, 9, 10),
            "fdd.cost_of_sales_component",
            cardinality="ORDERED_COMPONENT_SET",
        ),
        *_source(
            "operating_expense_components",
            "P&L",
            range(12, 23),
            "fdd.operating_expense_component",
            cardinality="ORDERED_COMPONENT_SET",
        ),
        _parent("operating_revenue", "GROSS_MARGIN"),
        *_source(
            "reported_ebitda",
            "P&L",
            23,
            "fdd.reported_ebitda",
            sign="DIRECT_SIGNED",
        ),
    ]
    formulas.append(
        _formula(
            2,
            "FDD_EBITDA_BRIDGE",
            "fdd-ebitda-bridge-v1",
            "P&L",
            23,
            "p_and_l_control",
            ebitda_bindings,
            ["GROSS_MARGIN"],
            [
                [
                    "assign",
                    "calculated_ebitda",
                    [
                        "subtract",
                        [
                            "subtract",
                            ["sum", "operating_income_components"],
                            ["sum", "cost_of_sales_components"],
                        ],
                        ["sum", "operating_expense_components"],
                    ],
                ],
                [
                    "assign",
                    "ebitda_margin_ratio",
                    [
                        "divide",
                        ["value", "calculated_ebitda"],
                        ["parent", "operating_revenue"],
                    ],
                ],
                [
                    "assign",
                    "ebitda_variance",
                    [
                        "subtract",
                        ["value", "calculated_ebitda"],
                        ["input", "reported_ebitda"],
                    ],
                ],
            ],
            denominator_rule={
                "name": "operating_revenue",
                "constraint": "STRICTLY_POSITIVE",
            },
            outputs=[
                _output("calculated_ebitda", "money", 2, primary=True),
                _output("reported_ebitda", "money", 2),
                _output("ebitda_margin_ratio", "ratio", 4),
                _output("ebitda_variance", "money", 2, comparison=True),
            ],
        )
    )

    ebit_bindings = [
        _parent("calculated_ebitda", "FDD_EBITDA_BRIDGE"),
        _parent("operating_revenue", "GROSS_MARGIN"),
        *_source("depreciation", "P&L", 24, "fdd.depreciation"),
        *_source(
            "amortization_and_impairment",
            "P&L",
            25,
            "fdd.amortization_impairment",
        ),
        *_source(
            "other_non_cash_adjustment",
            "P&L",
            26,
            "fdd.other_non_cash_adjustment",
            sign="DIRECT_SIGNED",
        ),
        *_source(
            "reported_ebit",
            "P&L",
            27,
            "fdd.reported_ebit",
            sign="DIRECT_SIGNED",
        ),
    ]
    formulas.append(
        _formula(
            3,
            "FDD_EBIT_BRIDGE",
            "fdd-ebit-bridge-v1",
            "P&L",
            27,
            "p_and_l_control",
            ebit_bindings,
            ["FDD_EBITDA_BRIDGE", "GROSS_MARGIN"],
            [
                [
                    "assign",
                    "calculated_ebit",
                    [
                        "add",
                        [
                            "subtract",
                            [
                                "subtract",
                                ["parent", "calculated_ebitda"],
                                ["input", "depreciation"],
                            ],
                            ["input", "amortization_and_impairment"],
                        ],
                        ["input", "other_non_cash_adjustment"],
                    ],
                ],
                [
                    "assign",
                    "ebit_margin_ratio",
                    [
                        "divide",
                        ["value", "calculated_ebit"],
                        ["parent", "operating_revenue"],
                    ],
                ],
                [
                    "assign",
                    "ebit_variance",
                    [
                        "subtract",
                        ["value", "calculated_ebit"],
                        ["input", "reported_ebit"],
                    ],
                ],
            ],
            denominator_rule={
                "name": "operating_revenue",
                "constraint": "STRICTLY_POSITIVE",
            },
            outputs=[
                _output("calculated_ebit", "money", 2, primary=True),
                _output("reported_ebit", "money", 2),
                _output("ebit_margin_ratio", "ratio", 4),
                _output("ebit_variance", "money", 2, comparison=True),
            ],
        )
    )

    tax_bindings = [
        _parent("calculated_ebit", "FDD_EBIT_BRIDGE"),
        *_source(
            "interest_expense_components",
            "P&L",
            (28, 29),
            "fdd.interest_expense_component",
            cardinality="ORDERED_COMPONENT_SET",
        ),
        *_source(
            "statutory_tax_rate_ratio",
            "P&L",
            30,
            "fdd.statutory_tax_rate_ratio",
            value_schema="FddRatioValueV1",
            unit_rule="RATIO_FRACTION",
        ),
        *_source(
            "tax_reconciling_items",
            "P&L",
            30,
            "fdd.tax_reconciling_item",
            sign="DIRECT_SIGNED",
            cardinality="ORDERED_ZERO_OR_MORE",
        ),
        *_source(
            "reconciling_set_complete",
            "P&L",
            30,
            "fdd.tax_reconciling_item_set_complete",
            value_schema="FddBooleanValueV1",
            cardinality="EXACTLY_ONE_TRUE",
            unit_rule="BOOLEAN",
        ),
        *_source(
            "deferred_tax_adjustment",
            "P&L",
            31,
            "fdd.deferred_tax_adjustment",
            sign="DIRECT_SIGNED",
        ),
        *_source(
            "reported_tax_components",
            "P&L",
            (30, 31),
            "fdd.reported_tax_component",
            sign="DIRECT_SIGNED",
            cardinality="ORDERED_COMPONENT_SET",
        ),
    ]
    formulas.append(
        _formula(
            4,
            "FDD_TAX_RECONCILIATION",
            "fdd-tax-reconciliation-v1",
            "P&L",
            30,
            "p_and_l_control",
            tax_bindings,
            ["FDD_EBIT_BRIDGE"],
            [
                [
                    "assign",
                    "profit_before_tax",
                    [
                        "subtract",
                        ["parent", "calculated_ebit"],
                        ["sum", "interest_expense_components"],
                    ],
                ],
                [
                    "assign",
                    "calculated_tax",
                    [
                        "add",
                        [
                            "add",
                            [
                                "multiply",
                                ["value", "profit_before_tax"],
                                ["input", "statutory_tax_rate_ratio"],
                            ],
                            ["sum", "tax_reconciling_items"],
                        ],
                        ["input", "deferred_tax_adjustment"],
                    ],
                ],
                [
                    "assign",
                    "reported_total_tax",
                    ["sum", "reported_tax_components"],
                ],
                [
                    "assign",
                    "tax_variance",
                    [
                        "subtract",
                        ["value", "calculated_tax"],
                        ["value", "reported_total_tax"],
                    ],
                ],
            ],
            denominator_rule={
                "name": "statutory_tax_rate_ratio",
                "constraint": "CLOSED_INTERVAL_0_1",
            },
            outputs=[
                _output("calculated_tax", "money", 2, primary=True),
                _output("profit_before_tax", "money", 2),
                _output("reported_total_tax", "money", 2),
                _output("tax_variance", "money", 2, comparison=True),
            ],
        )
    )

    net_bindings = [
        _parent("calculated_ebit", "FDD_EBIT_BRIDGE"),
        _parent("calculated_tax", "FDD_TAX_RECONCILIATION"),
        _parent("operating_revenue", "GROSS_MARGIN"),
        *_source(
            "interest_expense_components",
            "P&L",
            (28, 29),
            "fdd.interest_expense_component",
            cardinality="ORDERED_COMPONENT_SET",
        ),
        *_source(
            "reported_net_income",
            "P&L",
            32,
            "fdd.reported_net_income",
            sign="DIRECT_SIGNED",
        ),
    ]
    formulas.append(
        _formula(
            5,
            "FDD_NET_INCOME_BRIDGE",
            "fdd-net-income-bridge-v1",
            "P&L",
            32,
            "p_and_l_control",
            net_bindings,
            ["FDD_EBIT_BRIDGE", "FDD_TAX_RECONCILIATION", "GROSS_MARGIN"],
            [
                [
                    "assign",
                    "calculated_net_income",
                    [
                        "subtract",
                        [
                            "subtract",
                            ["parent", "calculated_ebit"],
                            ["sum", "interest_expense_components"],
                        ],
                        ["parent", "calculated_tax"],
                    ],
                ],
                [
                    "assign",
                    "net_income_margin_ratio",
                    [
                        "divide",
                        ["value", "calculated_net_income"],
                        ["parent", "operating_revenue"],
                    ],
                ],
                [
                    "assign",
                    "net_income_variance",
                    [
                        "subtract",
                        ["value", "calculated_net_income"],
                        ["input", "reported_net_income"],
                    ],
                ],
            ],
            denominator_rule={
                "name": "operating_revenue",
                "constraint": "STRICTLY_POSITIVE",
            },
            outputs=[
                _output("calculated_net_income", "money", 2, primary=True),
                _output("reported_net_income", "money", 2),
                _output("net_income_margin_ratio", "ratio", 4),
                _output("net_income_variance", "money", 2, comparison=True),
            ],
        )
    )

    asset_bindings = _source(
        "asset_component_balances",
        "Assets",
        range(4, 20),
        "fdd.asset_component_balance",
        cardinality="ORDERED_COMPONENT_SET",
        period_role="CLOSING_AS_OF",
    )
    formulas.append(
        _formula(
            9,
            "FDD_ASSET_TIE_OUT",
            "fdd-asset-tie-out-v1",
            "Assets",
            19,
            "sheet_control",
            asset_bindings,
            [],
            [
                [
                    "assign",
                    "calculated_total_assets",
                    ["sum", "asset_component_balances"],
                ]
            ],
            period_rule="EXACT_CLOSING_AS_OF",
            outputs=[
                _output("calculated_total_assets", "money", 2, primary=True),
            ],
            disposition_rule="CALCULATED_ONLY",
        )
    )

    liability_bindings = [
        *_source(
            "liability_equity_component_balances",
            "Liabilities",
            range(4, 19),
            "fdd.liability_equity_component_balance",
            cardinality="ORDERED_COMPONENT_SET",
            period_role="CLOSING_AS_OF",
        ),
        _parent(
            "calculated_total_assets",
            "FDD_ASSET_TIE_OUT",
            coordinate_rule="EXACT_SAME_AS_OF_DATE",
        ),
    ]
    formulas.append(
        _formula(
            10,
            "FDD_LIABILITY_TIE_OUT",
            "fdd-liability-tie-out-v1",
            "Liabilities",
            24,
            "sheet_control",
            liability_bindings,
            ["FDD_ASSET_TIE_OUT"],
            [
                [
                    "assign",
                    "calculated_total_liabilities_and_equity",
                    ["sum", "liability_equity_component_balances"],
                ],
                [
                    "assign",
                    "balance_sheet_variance",
                    [
                        "subtract",
                        ["parent", "calculated_total_assets"],
                        ["value", "calculated_total_liabilities_and_equity"],
                    ],
                ],
            ],
            period_rule="EXACT_CLOSING_AS_OF",
            outputs=[
                _output(
                    "calculated_total_liabilities_and_equity",
                    "money",
                    2,
                    primary=True,
                ),
                _output("calculated_total_assets", "money", 2),
                _output("balance_sheet_variance", "money", 2, comparison=True),
            ],
        )
    )
    return sorted(formulas, key=lambda row: row["formula_ordinal"])


def _metadata() -> dict[str, Any]:
    return {
        "artifact_schema": "fdd-formula-policy-v1",
        "policy_id": POLICY_ID,
        "policy_version": 1,
        "append_frozen": True,
        "activation_status": "INACTIVE",
        "canonicalization_contract_id": CANONICALIZATION_ID,
        "formula_engine_id": FORMULA_ENGINE_ID,
        "workbook_sha256": WORKBOOK_SHA256,
        "workbook_byte_count": WORKBOOK_BYTE_COUNT,
        "workbook_safe_row_coordinate_digest": WORKBOOK_SAFE_ROW_COORDINATE_DIGEST,
        "workbook_safe_header_digest": WORKBOOK_SAFE_HEADER_DIGEST,
        "question_count": 79,
        "required_question_count": 10,
        "empty_requirement_count": 69,
        "formula_count": 10,
        "expanded_source_binding_count": 107,
        "distinct_source_mapping_count": 101,
        "parent_binding_count": 9,
        "direct_signed_binding_count": 19,
        "completeness_binding_count": 2,
    }


def _canonicalization_contract() -> dict[str, Any]:
    return {
        "contract_id": CANONICALIZATION_ID,
        "encoding": "UTF-8",
        "bom": "FORBIDDEN",
        "unicode": "NFC",
        "object_key_order": "UNICODE_CODEPOINT_LEXICAL",
        "separators": [",", ":"],
        "insignificant_whitespace": "FORBIDDEN",
        "json_float": "FORBIDDEN",
        "ordered_arrays": "PRESERVED",
        "decimal": {
            "representation": "FINITE_NON_EXPONENT_BASE10_STRING",
            "trailing_fractional_zeroes": "REMOVED",
            "negative_zero": "0",
        },
        "uuid": "LOWERCASE_CANONICAL",
        "sha256": "LOWERCASE_HEX_64",
        "timestamp": "UTC_RFC3339_SIX_FRACTIONAL_DIGITS_Z",
    }


def _workbook_authority() -> dict[str, Any]:
    return {
        "schema_version": "fdd-workbook-authority-v1",
        "source_name": "configured-fdd-workbook",
        "source_type": "operator_workbook",
        "byte_count": WORKBOOK_BYTE_COUNT,
        "sha256": WORKBOOK_SHA256,
        "safe_row_coordinate_digest": WORKBOOK_SAFE_ROW_COORDINATE_DIGEST,
        "safe_header_digest": WORKBOOK_SAFE_HEADER_DIGEST,
        "header_row": 2,
        "first_data_row": 4,
        "formula_cell_count": 0,
        "sheet_registry": [
            {
                "sheet_ordinal": ordinal,
                "sheet_name": name,
                "question_count": SHEET_COUNTS[name],
            }
            for ordinal, name in enumerate(SHEETS, start=1)
        ],
    }


def _methodology_transition() -> dict[str, Any]:
    return {
        "schema_version": "fdd-methodology-transition-v1",
        "source_artifact_sha256": WORKBOOK_SHA256,
        "legacy_version_id": f"financial_dd:{WORKBOOK_SHA256}",
        "successor_version_id_preimage": [
            "financial_dd",
            WORKBOOK_SHA256,
            POLICY_ID,
            "$FDD_FORMULA_POLICY_V1_SHA256",
        ],
        "preserve_methodology_question_ids": True,
        "preserved_question_count": 79,
        "legacy_sheet_fallbacks": [
            {
                "source_sheet": "P&L",
                "calc_type": "GROSS_MARGIN",
                "required": False,
                "affected_question_count": 29,
            },
            {
                "source_sheet": "Cash Flow",
                "calc_type": "BURN_RATE",
                "required": False,
                "affected_question_count": 13,
            },
        ],
        "successor_required_map": [
            {
                "formula_ordinal": ordinal,
                "formula_type": formula_type,
                "source_sheet": sheet,
                "source_row": row,
                "required": True,
                "scope": scope,
            }
            for ordinal, formula_type, sheet, row, scope in FORMULA_ANCHORS
        ],
        "successor_required_question_count": 10,
        "successor_empty_requirement_count": 69,
        "strict_new_run_selection_only": True,
        "historical_run_selections_immutable": True,
    }


def _scope_exclusions() -> dict[str, Any]:
    return {
        "schema_version": "fdd-formula-scope-exclusions-v1",
        "rows": [
            {
                "scope_id": "fdd-debt-roll-forward-balance-scope-v1",
                "formula_type": "FDD_DEBT_ROLL_FORWARD",
                "included_balance_sources": [
                    {
                        "source_sheet": "Liabilities",
                        "source_row": 9,
                        "predicate": "fdd.interest_bearing_debt_balance",
                    }
                ],
                "excluded_balance_rows": [
                    {"source_sheet": "Liabilities", "source_row": 17},
                    {"source_sheet": "Liabilities", "source_row": 18},
                ],
                "included_movement_rows": [
                    {"source_sheet": "Cash Flow", "source_row": 11},
                    {"source_sheet": "Cash Flow", "source_row": 12},
                ],
                "excluded_rows_remain_required_answers": True,
                "unmatched_movement_disposition": "OUT_OF_BALANCE",
                "runtime_scope_expansion": "FORBIDDEN",
            },
            {
                "scope_id": "fdd-liability-equity-tie-out-scope-v1",
                "formula_type": "FDD_LIABILITY_TIE_OUT",
                "included_component_rows": [
                    {"source_sheet": "Liabilities", "source_row": row} for row in range(4, 19)
                ],
                "excluded_off_balance_sheet_rows": [
                    {"source_sheet": "Liabilities", "source_row": row} for row in range(19, 25)
                ],
                "excluded_rows_remain_required_answers": True,
                "manufactured_reported_total": "FORBIDDEN",
            },
        ],
    }


def _completeness_registry() -> dict[str, Any]:
    return {
        "schema_version": "fdd-documentary-completeness-requests-v1",
        "rows": [
            {
                "request_ordinal": ordinal,
                "predicate": predicate,
                "source_sheet": sheet,
                "source_row": row,
                "request_text": text,
                "admitted_result": "CITED_BOOLEAN_TRUE_OR_EVIDENCE_NOT_FOUND",
                "false_result": "FORBIDDEN",
                "uncited_true": "FORBIDDEN",
                "prose_only": "FORBIDDEN",
                "request_sha256": _sha256_canonical(
                    [
                        "fdd-documentary-completeness-request-v1",
                        predicate,
                        sheet,
                        row,
                        text,
                    ]
                ),
            }
            for ordinal, (predicate, sheet, row, text) in enumerate(
                COMPLETENESS_REQUESTS,
                start=1,
            )
        ],
    }


def _direct_signed_registry() -> dict[str, Any]:
    def row(
        ordinal: int,
        predicate: str,
        positive: str,
        negative: str,
    ) -> dict[str, Any]:
        request = (
            f"Interpret a positive value as {positive}; interpret a negative value "
            f"as {negative}. Return the source's reported sign under this convention "
            "only; do not infer, balance, or silently negate it."
        )
        if predicate == "fdd.reported_delta_nwc":
            request = (
                "This predicate is an operating-NWC balance movement, not its cash effect. "
                f"Interpret a positive value as {positive}; interpret a negative value as "
                f"{negative}. The formula derives cash effect as -reported_delta_nwc; "
                "never return a cash-effect sign under this movement predicate. Return the "
                "source's reported sign under this convention only; do not infer, balance, "
                "or silently negate it."
            )
        return {
            "convention_ordinal": ordinal,
            "predicate": predicate,
            "positive_value": positive,
            "negative_value": negative,
            "sign_transform": "DIRECT_SIGNED",
            "extractor_request_requirement": request,
            "convention_sha256": _sha256_canonical(
                [
                    "fdd-direct-signed-convention-v1",
                    predicate,
                    positive,
                    negative,
                    request,
                ]
            ),
        }

    return {
        "schema_version": "fdd-direct-signed-conventions-v1",
        "rows": [
            row(ordinal, predicate, positive, negative)
            for ordinal, (predicate, positive, negative) in enumerate(
                DIRECT_SIGNED_CONVENTIONS,
                start=1,
            )
        ],
        "reported_delta_nwc_cash_effect_rule": {
            "predicate": "fdd.reported_delta_nwc",
            "source_semantics": "MOVEMENT",
            "derived_cash_effect": "-reported_delta_nwc",
            "cash_effect_input_under_movement_predicate": "FORBIDDEN",
        },
    }


def _incomplete_schema() -> dict[str, Any]:
    return {
        "schema_version": "methodology-calculation-incomplete-v1",
        "code": "METHODOLOGY_CALCULATION_INCOMPLETE",
        "field_tuple": [
            "schema_version",
            "code",
            "formula_policy_id",
            "formula_policy_sha256",
            "formula_type",
            "formula_ordinal",
            "formula_sha256",
            "anchor_sheet",
            "anchor_row",
            "economic_coordinate",
            "issue_count",
            "issues",
        ],
        "issue_field_tuple": [
            "issue_ordinal",
            "issue_kind",
            "binding_ordinal",
            "role",
            "predicate",
            "source_sheet",
            "source_row",
            "economic_coordinate",
            "candidate_factual_output_ids",
        ],
        "issue_kind_enum": ["MISSING", "DUPLICATE", "AMBIGUOUS", "UNCONSUMED"],
        "missing_candidate_tuple": "EMPTY",
        "duplicate_ambiguous_candidates": "ALL_CONFLICTING_FACTUAL_OUTPUT_IDS",
        "unconsumed_candidates": "ALL_UNCONSUMED_GOVERNED_FACTUAL_OUTPUT_IDS",
        "issue_sort_tuple": [
            "formula_ordinal",
            "binding_ordinal",
            "issue_kind",
            "candidate_factual_output_ids",
        ],
        "resolution_mode": "COMPLETE_PASS_NEVER_FIRST_FAILURE",
        "private_fact_values": "FORBIDDEN",
        "claim_prose": "FORBIDDEN",
        "source_document_names": "FORBIDDEN",
    }


def _financial_output_schema() -> dict[str, Any]:
    return {
        "schema_version": "financial-diligence-row-set-v1",
        "top_level_field_tuple": [
            "schema_version",
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract",
            "financial_methodology",
            "formula_policy",
            "row_count",
            "rows",
            "reconciliation",
        ],
        "row_count": 79,
        "row_schema": {
            "schema_version": "fdd-dashboard-row-v1",
            "field_tuple": [
                "row_ordinal",
                "sheet_ordinal",
                "source_sheet",
                "source_row_number",
                "source_anchor_cell",
                "methodology_version_record_id",
                "methodology_version_id",
                "methodology_question_record_id",
                "methodology_question_id",
                "question_payload_hash",
                "question_text",
                "coverage_record_id",
                "dashboard_row_id",
                "dashboard_output_object_id",
                "disposition",
                "disposition_reason_code",
                "result_records",
                "answer_values",
                "task_gaps",
                "root_links",
                "calculations",
                "row_payload_sha256",
                "root_projection_sha256",
            ],
            "result_record_field_tuple": [
                "task_record_id",
                "result_id",
                "output_object_id",
                "output_identity_sha256",
                "output_value_sha256",
                "outcome",
                "result_sha256",
                "searched_scope_sha256",
                "provider_provenance_sha256",
            ],
            "answer_value_field_tuple": [
                "output_ordinal",
                "factual_output_record_id",
                "factual_output_object_id",
                "output_identity_sha256",
                "output_value_sha256",
                "predicate",
                "typed_value",
                "period",
                "unit",
                "value_sha256",
                "claim_link_id",
                "claim_id",
                "evidence_links",
                "sanad_links",
            ],
            "evidence_link_field_tuple": [
                "evidence_link_id",
                "evidence_id",
                "output_object_id",
                "output_identity_sha256",
                "output_value_sha256",
            ],
            "sanad_link_field_tuple": [
                "sanad_link_id",
                "sanad_id",
                "grade",
                "output_object_id",
                "output_identity_sha256",
                "output_value_sha256",
            ],
            "task_gap_field_tuple": [
                "task_record_id",
                "result_id",
                "result_output_object_id",
                "result_output_identity_sha256",
                "result_output_value_sha256",
                "code",
                "finding",
                "searched_scope_sha256",
                "provider_provenance_sha256",
            ],
            "root_link_field_tuple": [
                "root_kind",
                "root_ordinal",
                "output_object_id",
                "output_kind",
                "output_identity_sha256",
                "output_value_sha256",
                "root_link_id",
            ],
            "calculation_package": (
                "FddDeterministicCalculationV1_PLUS_CALC_SANAD_AND_NATIVE_HASHES"
            ),
        },
        "reconciliation_field_tuple": [
            "answered_row_count",
            "gap_row_count",
            "red_flag_row_count",
            "result_root_count",
            "evidence_root_count",
            "sanad_root_count",
            "calculation_root_count",
            "adverse_root_count",
            "calculation_count",
            "calculated_only_count",
            "in_balance_count",
            "out_of_balance_count",
            "row_set_sha256",
            "root_set_sha256",
            "calculation_set_sha256",
        ],
        "row_payload_preimage": [
            "financial-diligence-row-v1",
            "$identity_and_trace_tuple",
            "$disposition",
            "$disposition_reason_code",
            "$result_records",
            "$answer_values",
            "$task_gaps",
            "$calculations",
            "$root_projection_sha256",
        ],
        "identity_and_trace_tuple": [
            "row_ordinal",
            "sheet_ordinal",
            "source_sheet",
            "source_row_number",
            "source_anchor_cell",
            "methodology_version_record_id",
            "methodology_version_id",
            "methodology_question_record_id",
            "methodology_question_id",
            "question_payload_hash",
            "coverage_record_id",
            "dashboard_row_id",
            "dashboard_output_object_id",
        ],
        "row_set_preimage_tuple": [
            "ordinal",
            "dashboard_row_id",
            "row_payload_sha256",
            "root_projection_sha256",
        ],
        "calculation_set_preimage_tuple": [
            "policy_ordinal",
            "coordinate",
            "calc_id",
            "reproducibility_sha256",
        ],
        "sort_contract": {
            "rows": [
                "sheet_ordinal",
                "source_row_number",
                "methodology_question_id",
            ],
            "results": ["task_record_id", "result_id"],
            "answer_values": ["output_ordinal", "factual_output_record_id"],
            "task_gaps": ["task_record_id", "result_id"],
            "root_links": [
                "root_kind",
                "root_ordinal",
                "output_object_id",
                "root_link_id",
            ],
            "calculations": ["policy_ordinal", "coordinate", "calc_id"],
            "inputs": ["binding_ordinal"],
        },
        "unknown_fields": "FORBIDDEN",
        "embedded_self_hash": "FORBIDDEN",
    }


def _activation_assertions() -> dict[str, Any]:
    return {
        "schema_version": "fdd-formula-policy-activation-v1",
        "policy_status": "INACTIVE",
        "append_frozen": True,
        "runtime_selectable": False,
        "migration_consumable": False,
        "formula_implementation_authorized": False,
        "repository_implementation_authorized": False,
        "worker_implementation_authorized": False,
        "dashboard_implementation_authorized": False,
        "deliverable_implementation_authorized": False,
        "second_artifact_fixture_review_required": True,
        "successor_pipeline_contract": "full-strict-v22",
        "successor_pipeline_contract_status": "INACTIVE_PENDING_REVIEW",
        "legacy_pipeline_contract": "full-strict-v21",
        "legacy_pipeline_contract_immutable": True,
        "migration": "0039",
        "migration_source_authorized": False,
        "live_calls_authorized": False,
    }


def _formula_registry() -> dict[str, Any]:
    return {
        "schema_version": "fdd-formula-registry-v1",
        "formula_hash_preimage_field_tuple": [
            "formula_type",
            "version",
            "expression_id",
            "expression_ast",
            "dependency_tuple",
            "ordered_input_specs",
            "sign_rules",
            "cardinality_rules",
            "period_rule",
            "currency_rule",
            "unit_rule",
            "denominator_rule",
            "output_specs",
            "precision",
            "rounding",
            "permitted_assumptions",
        ],
        "rows": _formula_rows(),
    }


def _component_hashes(policy: Mapping[str, Any]) -> dict[str, str]:
    return {
        section: _sha256_canonical(["fdd-formula-policy-v1-component-v1", section, policy[section]])
        for section in HASHED_COMPONENTS
    }


def _policy_hash(policy: Mapping[str, Any]) -> str:
    component_hashes = _component_hashes(policy)
    return _sha256_canonical(
        [
            "fdd-formula-policy-v1",
            [[section, component_hashes[section]] for section in HASHED_COMPONENTS],
        ]
    )


def _digest_registry(policy: Mapping[str, Any]) -> dict[str, Any]:
    component_hashes = _component_hashes(policy)
    return {
        "schema_version": "fdd-formula-policy-digests-v1",
        "algorithm": "SHA-256",
        "component_order": list(HASHED_COMPONENTS),
        "component_hashes": component_hashes,
        "policy_hash_preimage": [
            "fdd-formula-policy-v1",
            [[section, component_hashes[section]] for section in HASHED_COMPONENTS],
        ],
        "fdd_formula_policy_v1_sha256": _policy_hash(policy),
    }


def build_policy(workbook_path: Path) -> dict[str, Any]:
    rows = _read_workbook_rows(workbook_path)
    policy = {
        "metadata": _metadata(),
        "canonicalization_contract": _canonicalization_contract(),
        "workbook_authority": _workbook_authority(),
        "workbook_row_registry": {
            "schema_version": "fdd-workbook-row-registry-v1",
            "rows": rows,
        },
        "methodology_transition": _methodology_transition(),
        "formula_registry": _formula_registry(),
        "debt_scope_exclusions": _scope_exclusions(),
        "completeness_request_registry": _completeness_registry(),
        "direct_signed_convention_registry": _direct_signed_registry(),
        "incomplete_binding_failure_schema": _incomplete_schema(),
        "financial_output_schema": _financial_output_schema(),
        "digest_recipe_registry": {},
        "activation_assertions": _activation_assertions(),
    }
    policy["digest_recipe_registry"] = _digest_registry(policy)
    _validate_counts(policy)
    return policy


def _validate_counts(policy: Mapping[str, Any]) -> None:
    formulas = policy["formula_registry"]["rows"]
    sources = [
        binding
        for formula in formulas
        for binding in formula["ordered_input_specs"]
        if binding["binding_kind"] == "SOURCE"
    ]
    parents = [
        binding
        for formula in formulas
        for binding in formula["ordered_input_specs"]
        if binding["binding_kind"] == "PARENT_CALCULATION"
    ]
    distinct_sources = {
        (item["source_sheet"], item["source_row"], item["predicate"]) for item in sources
    }
    direct_signed = [item for item in sources if item["sign_transform"] == "DIRECT_SIGNED"]
    completeness = [item for item in sources if item["value_schema"] == "FddBooleanValueV1"]
    _require(len(formulas) == 10, "TASK8_FDD_FORMULA_COUNT_INVALID")
    _require(len(sources) == 107, "TASK8_FDD_SOURCE_BINDING_COUNT_INVALID")
    _require(
        len(distinct_sources) == 101,
        "TASK8_FDD_DISTINCT_SOURCE_MAPPING_COUNT_INVALID",
    )
    _require(len(parents) == 9, "TASK8_FDD_PARENT_BINDING_COUNT_INVALID")
    _require(
        len(direct_signed) == 19,
        "TASK8_FDD_DIRECT_SIGNED_BINDING_COUNT_INVALID",
    )
    _require(
        len(completeness) == 2,
        "TASK8_FDD_COMPLETENESS_BINDING_COUNT_INVALID",
    )
    _require(
        [formula["formula_ordinal"] for formula in formulas] == list(range(1, 11)),
        "TASK8_FDD_FORMULA_ORDER_INVALID",
    )


def _markdown_cell(value: object) -> str:
    if value is None:
        return "—"
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
        .replace("\r", "<br>")
    )


def render_review_map(policy: Mapping[str, Any]) -> bytes:
    workbook_rows = {
        (row["source_sheet"], row["source_row"]): row
        for row in policy["workbook_row_registry"]["rows"]
    }
    lines = [
        "# Slice100 Task 8 FDD Formula Policy v1 — reviewer mapping",
        "",
        "<!-- Deterministic projection of contracts/fdd_formula_policy_v1.json. -->",
        "",
        f"- Policy ID: `{POLICY_ID}`",
        (f"- Policy SHA-256: `{policy['digest_recipe_registry']['fdd_formula_policy_v1_sha256']}`"),
        f"- Workbook SHA-256: `{WORKBOOK_SHA256}`",
        "- Formula anchors: `10`",
        "- Expanded source bindings: `107`",
        "",
    ]
    for formula in policy["formula_registry"]["rows"]:
        anchor = workbook_rows[(formula["anchor_sheet"], formula["anchor_row"])]
        lines.extend(
            [
                (f"## {formula['formula_ordinal']}. {_markdown_cell(formula['formula_type'])}"),
                "",
                "| Anchor sheet | Row | Scope | Term | Nature | Line item | Question | "
                "Anchor cell | Methodology question ID | Source-row SHA-256 |",
                "|---|---:|---|---|---|---|---|---|---|---|",
                (
                    f"| {_markdown_cell(anchor['source_sheet'])} | {anchor['source_row']} | "
                    f"{_markdown_cell(formula['scope'])} | {_markdown_cell(anchor['term'])} | "
                    f"{_markdown_cell(anchor['nature'])} | "
                    f"{_markdown_cell(anchor['line_item'])} | "
                    f"{_markdown_cell(anchor['question_text'])} | "
                    f"{_markdown_cell(anchor['source_anchor_cell'])} | "
                    f"`{anchor['methodology_question_id']}` | "
                    f"`{anchor['source_row_sha256']}` |"
                ),
                "",
                "| Binding | Role | Predicate | Sign | Cardinality | Sheet | Row | Term | "
                "Nature | Line item | Question | Anchor cell | Methodology question ID | "
                "Mapping SHA-256 |",
                "|---:|---|---|---|---|---|---:|---|---|---|---|---|---|---|",
            ]
        )
        for binding in formula["ordered_input_specs"]:
            if binding["binding_kind"] != "SOURCE":
                continue
            row = workbook_rows[(binding["source_sheet"], binding["source_row"])]
            mapping_hash = _sha256_canonical(
                [
                    "fdd-formula-source-mapping-v1",
                    formula["formula_ordinal"],
                    binding,
                    row["methodology_question_id"],
                    row["source_row_sha256"],
                ]
            )
            lines.append(
                f"| {binding['binding_ordinal']} | {_markdown_cell(binding['role'])} | "
                f"`{binding['predicate']}` | `{binding['sign_transform']}` | "
                f"`{binding['cardinality']}` | {_markdown_cell(row['source_sheet'])} | "
                f"{row['source_row']} | {_markdown_cell(row['term'])} | "
                f"{_markdown_cell(row['nature'])} | {_markdown_cell(row['line_item'])} | "
                f"{_markdown_cell(row['question_text'])} | "
                f"{_markdown_cell(row['source_anchor_cell'])} | "
                f"`{row['methodology_question_id']}` | `{mapping_hash}` |"
            )
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _fixture(
    policy: Mapping[str, Any],
    *,
    artifact_bytes: bytes,
    review_map_bytes: bytes,
) -> dict[str, Any]:
    return {
        "artifact_schema": "fdd-formula-policy-v1-hash-fixture-v1",
        "policy_id": POLICY_ID,
        "artifact_sha256": _sha256_bytes(artifact_bytes),
        "fdd_formula_policy_v1_sha256": _policy_hash(policy),
        "component_hashes": _component_hashes(policy),
        "review_map_sha256": _sha256_bytes(review_map_bytes),
        "counts": {
            "question_count": 79,
            "required_question_count": 10,
            "empty_requirement_count": 69,
            "formula_count": 10,
            "expanded_source_binding_count": 107,
            "distinct_source_mapping_count": 101,
            "parent_binding_count": 9,
            "direct_signed_binding_count": 19,
            "completeness_binding_count": 2,
        },
        "activation_status": "INACTIVE",
    }


def _has_reparse_attribute(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _validated_publication_path(path: Path) -> Path:
    candidate = Path(path).absolute()
    if candidate.exists() or candidate.is_symlink():
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            raise MaterializationError("TASK8_POLICY_PUBLICATION_PATH_INVALID") from exc
        _require(
            not candidate.is_symlink()
            and not _has_reparse_attribute(candidate)
            and stat.S_ISREG(mode),
            "TASK8_POLICY_PUBLICATION_PATH_INVALID",
        )
    current = candidate.parent
    _require(current.is_dir(), "TASK8_POLICY_PUBLICATION_PATH_INVALID")
    while True:
        _require(
            not current.is_symlink() and not _has_reparse_attribute(current),
            "TASK8_POLICY_PUBLICATION_PATH_INVALID",
        )
        if current.parent == current:
            break
        current = current.parent
    return candidate.resolve(strict=False)


def _write_exclusive(path: Path, raw: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise MaterializationError("TASK8_POLICY_PUBLICATION_TARGET_MISMATCH") from exc


def _publish_policy_outputs(
    outputs: Sequence[tuple[str, Path, bytes]],
) -> bool:
    validated = [(name, _validated_publication_path(path), raw) for name, path, raw in outputs]
    identities = [os.path.normcase(str(path)) for _name, path, _raw in validated]
    _require(
        len(identities) == len(set(identities)),
        "TASK8_POLICY_PUBLICATION_PATH_INVALID",
    )
    existing = [(name, path, raw) for name, path, raw in validated if path.exists()]
    if existing:
        _require(
            len(existing) == len(validated)
            and all(path.read_bytes() == raw for _name, path, raw in validated),
            "TASK8_POLICY_PUBLICATION_TARGET_MISMATCH",
        )
        return False
    for _name, path, raw in validated:
        _write_exclusive(path, raw)
    return True


def build_policy_publication_bundle(workbook_path: Path) -> dict[str, bytes]:
    """Build the exact frozen policy, fixture, and reviewer-map bytes."""

    policy = build_policy(workbook_path)
    artifact_bytes = _render_json(policy)
    review_map_bytes = render_review_map(policy)
    fixture_bytes = _render_json(
        _fixture(
            policy,
            artifact_bytes=artifact_bytes,
            review_map_bytes=review_map_bytes,
        )
    )
    return {
        "policy_artifact_raw": artifact_bytes,
        "policy_fixture_raw": fixture_bytes,
        "policy_review_map_raw": review_map_bytes,
    }


def materialize(workbook_path: Path) -> dict[str, Any]:
    bundle = build_policy_publication_bundle(workbook_path)
    artifact_bytes = bundle["policy_artifact_raw"]
    fixture_bytes = bundle["policy_fixture_raw"]
    review_map_bytes = bundle["policy_review_map_raw"]
    policy = json.loads(artifact_bytes)
    POLICY_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    POLICY_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_MAP.parent.mkdir(parents=True, exist_ok=True)
    _publish_policy_outputs(
        (
            ("fdd_formula_policy_fixture", POLICY_FIXTURE, fixture_bytes),
            ("fdd_formula_policy_review_map", REVIEW_MAP, review_map_bytes),
            ("fdd_formula_policy_artifact", POLICY_ARTIFACT, artifact_bytes),
        )
    )
    return {
        "artifact_sha256": _sha256_bytes(artifact_bytes),
        "policy_sha256": _policy_hash(policy),
        "review_map_sha256": _sha256_bytes(review_map_bytes),
        "fixture_sha256": _sha256_bytes(fixture_bytes),
        "publication_order": [
            "fdd_formula_policy_fixture",
            "fdd_formula_policy_review_map",
            "fdd_formula_policy_artifact",
        ],
        "commit_point": "fdd_formula_policy_artifact",
        "activation_status": "INACTIVE",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the artifact, fixture, and deterministic reviewer projection.",
    )
    return parser.parse_args()


def main() -> int:
    try:
        args = _parse_args()
        if args.write:
            result = materialize(args.workbook)
        else:
            bundle = build_policy_publication_bundle(args.workbook)
            policy = json.loads(bundle["policy_artifact_raw"])
            result = {
                "policy_sha256": _policy_hash(policy),
                "question_count": 79,
                "formula_count": 10,
                "expanded_source_binding_count": 107,
                "activation_status": "INACTIVE",
            }
    except Exception as exc:
        if isinstance(exc, MaterializationError) and re.fullmatch(
            r"TASK8_[A-Z0-9_]+",
            str(exc),
        ):
            reason_code = str(exc)
        elif isinstance(exc, OSError):
            reason_code = "TASK8_POLICY_INPUT_UNAVAILABLE"
        else:
            reason_code = "TASK8_POLICY_MATERIALIZATION_FAILED"
        print(json.dumps({"reason_code": reason_code, "result": "FAILED"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
