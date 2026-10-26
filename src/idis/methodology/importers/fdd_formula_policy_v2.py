"""Closed importer for the frozen Task-8 FDD formula-policy v2 registry."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO, Never, cast

from idis.methodology.importers.fdd_excel import (
    FddWorkbookProjection,
    _question,
    validated_production_fdd_workbook_projection,
)
from idis.methodology.models import (
    FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID,
    FDD_FORMULA_POLICY_V2_POLICY_SHA256,
    FDD_FORMULA_POLICY_V2_WORKBOOK_SHA256,
    FddFormulaRequirement,
    FddFormulaScope,
    FddFormulaType,
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologyType,
    MethodologyVersion,
)

FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256 = (
    "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
)
FDD_FORMULA_POLICY_V2_WORKBOOK_BYTE_COUNT = 24_905

_POLICY_ID = "fdd-formula-policy-v2"
_POLICY_HASH_KEY = "fdd_formula_policy_v2_sha256"
_POLICY_HASH_PLACEHOLDER = "$FDD_FORMULA_POLICY_V2_SHA256"
_COMPONENT_HASH_DOMAIN = "fdd-formula-policy-v2-component-v1"
_COMPONENT_ORDER = (
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
_SHEET_ORDINALS = {"P&L": 1, "Cash Flow": 2, "Liabilities": 3, "Assets": 4}
_ROW_KEYS = {
    "row_ordinal",
    "sheet_ordinal",
    "source_sheet",
    "source_row",
    "source_anchor_cell",
    "term",
    "nature",
    "line_item",
    "question_text",
    "methodology_question_id",
    "required_calculations",
    "source_row_sha256",
}
_REQUIREMENT_KEYS = {"formula_ordinal", "formula_type", "required", "scope"}


class FddFormulaPolicyV2Error(ValueError):
    """Leakage-safe, stable failure from the sole policy-v2 constructor."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> Never:
    raise FddFormulaPolicyV2Error(code) from None


def _canonical_value(value: Any) -> None:
    if isinstance(value, float):
        _fail("FDD_FORMULA_POLICY_V2_POLICY_MISMATCH")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            _fail("FDD_FORMULA_POLICY_V2_POLICY_MISMATCH")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, list):
        for item in value:
            _canonical_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("FDD_FORMULA_POLICY_V2_POLICY_MISMATCH")
            _canonical_value(key)
            _canonical_value(item)
        return
    _fail("FDD_FORMULA_POLICY_V2_POLICY_MISMATCH")


def _canonical_json(value: Any) -> bytes:
    _canonical_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail("FDD_FORMULA_POLICY_V2_POLICY_MISMATCH")


def _sha256_canonical(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _read_bytes(
    value: bytes | Path | str | BinaryIO,
    *,
    code: str,
) -> tuple[bytes, Path | None]:
    if isinstance(value, bytes):
        return value, None
    if isinstance(value, (Path, str)):
        path = Path(value)
        try:
            return path.read_bytes(), path
        except OSError:
            _fail(code)
    try:
        raw = value.read()
    except Exception:
        _fail(code)
    if not isinstance(raw, bytes):
        _fail(code)
    return raw, None


def _object(raw: bytes) -> dict[str, Any]:
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeError, json.JSONDecodeError):
        _fail("FDD_FORMULA_POLICY_V2_ARTIFACT_MISMATCH")
    if not isinstance(value, dict):
        _fail("FDD_FORMULA_POLICY_V2_ARTIFACT_MISMATCH")
    try:
        rendered = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
            "utf-8"
        )
    except (TypeError, ValueError):
        _fail("FDD_FORMULA_POLICY_V2_ARTIFACT_MISMATCH")
    if rendered != raw:
        _fail("FDD_FORMULA_POLICY_V2_ARTIFACT_MISMATCH")
    return cast(dict[str, Any], value)


def _validated_policy(raw: bytes) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256:
        _fail("FDD_FORMULA_POLICY_V2_ARTIFACT_MISMATCH")
    policy = _object(raw)
    try:
        metadata = policy["metadata"]
        authority = policy["workbook_authority"]
        transition = policy["methodology_transition"]
        digest = policy["digest_recipe_registry"]
        component_order = tuple(digest["component_order"])
    except (KeyError, TypeError):
        _fail("FDD_FORMULA_POLICY_V2_POLICY_MISMATCH")
    if (
        not isinstance(metadata, Mapping)
        or not isinstance(authority, Mapping)
        or not isinstance(transition, Mapping)
        or not isinstance(digest, Mapping)
        or component_order != _COMPONENT_ORDER
        or metadata.get("artifact_schema") != _POLICY_ID
        or metadata.get("policy_id") != _POLICY_ID
        or metadata.get("policy_version") != 2
        or metadata.get("append_frozen") is not True
        or metadata.get("activation_status") != "INACTIVE"
        or metadata.get("workbook_sha256") != FDD_FORMULA_POLICY_V2_WORKBOOK_SHA256
        or metadata.get("workbook_byte_count") != FDD_FORMULA_POLICY_V2_WORKBOOK_BYTE_COUNT
        or metadata.get("question_count") != 79
        or metadata.get("required_question_count") != 10
        or metadata.get("empty_requirement_count") != 69
        or metadata.get("formula_count") != 10
        or authority.get("source_name") != "configured-fdd-workbook"
        or authority.get("source_type") != "operator_workbook"
        or authority.get("byte_count") != FDD_FORMULA_POLICY_V2_WORKBOOK_BYTE_COUNT
        or authority.get("sha256") != FDD_FORMULA_POLICY_V2_WORKBOOK_SHA256
        or authority.get("formula_cell_count") != 0
        or transition.get("source_artifact_sha256") != FDD_FORMULA_POLICY_V2_WORKBOOK_SHA256
        or transition.get("legacy_version_id")
        != f"financial_dd:{FDD_FORMULA_POLICY_V2_WORKBOOK_SHA256}"
        or transition.get("successor_version_id_preimage")
        != [
            "financial_dd",
            FDD_FORMULA_POLICY_V2_WORKBOOK_SHA256,
            _POLICY_ID,
            _POLICY_HASH_PLACEHOLDER,
        ]
        or transition.get("preserve_methodology_question_ids") is not True
        or transition.get("preserved_question_count") != 79
        or transition.get("successor_required_question_count") != 10
        or transition.get("successor_empty_requirement_count") != 69
        or transition.get("strict_new_run_selection_only") is not True
        or transition.get("historical_run_selections_immutable") is not True
    ):
        _fail("FDD_FORMULA_POLICY_V2_POLICY_MISMATCH")

    try:
        component_hashes = {
            section: _sha256_canonical([_COMPONENT_HASH_DOMAIN, section, policy[section]])
            for section in _COMPONENT_ORDER
        }
    except (KeyError, TypeError):
        _fail("FDD_FORMULA_POLICY_V2_POLICY_MISMATCH")
    preimage = [
        _POLICY_ID,
        [[section, component_hashes[section]] for section in _COMPONENT_ORDER],
    ]
    if (
        digest.get("schema_version") != "fdd-formula-policy-digests-v1"
        or digest.get("algorithm") != "SHA-256"
        or digest.get("component_hashes") != component_hashes
        or digest.get("policy_hash_preimage") != preimage
        or digest.get(_POLICY_HASH_KEY) != FDD_FORMULA_POLICY_V2_POLICY_SHA256
        or _sha256_canonical(preimage) != FDD_FORMULA_POLICY_V2_POLICY_SHA256
    ):
        _fail("FDD_FORMULA_POLICY_V2_POLICY_MISMATCH")
    return policy


def _source_row_hash(row: Mapping[str, Any]) -> str:
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


def _validated_join(
    policy: Mapping[str, Any],
    workbook: FddWorkbookProjection,
) -> tuple[tuple[FddFormulaRequirement, ...], ...]:
    try:
        rows = policy["workbook_row_registry"]["rows"]
        transition_map = policy["methodology_transition"]["successor_required_map"]
        formula_rows = policy["formula_registry"]["rows"]
    except (KeyError, TypeError):
        _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
    if (
        not isinstance(rows, list)
        or len(rows) != 79
        or not isinstance(transition_map, list)
        or len(transition_map) != 10
        or not isinstance(formula_rows, list)
        or len(formula_rows) != 10
        or workbook.source_hash != FDD_FORMULA_POLICY_V2_WORKBOOK_SHA256
        or workbook.source_byte_count != FDD_FORMULA_POLICY_V2_WORKBOOK_BYTE_COUNT
        or len(workbook.rows) != 79
    ):
        _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")

    requirements_by_question: list[tuple[FddFormulaRequirement, ...]] = []
    required_projection: list[dict[str, Any]] = []
    for ordinal, (row, workbook_row) in enumerate(
        zip(rows, workbook.rows, strict=True),
        start=1,
    ):
        if not isinstance(row, Mapping) or set(row) != _ROW_KEYS:
            _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
        source_sheet = workbook_row.source_sheet
        expected_question = {
            "row_ordinal": ordinal,
            "sheet_ordinal": workbook_row.sheet_ordinal,
            "source_sheet": source_sheet,
            "source_row": workbook_row.source_row,
            "source_anchor_cell": workbook_row.source_anchor_cell,
            "term": workbook_row.term,
            "nature": workbook_row.nature,
            "line_item": workbook_row.line_item,
            "question_text": workbook_row.question_text,
            "methodology_question_id": workbook_row.methodology_question_id,
        }
        if (
            workbook_row.row_ordinal != ordinal
            or workbook_row.sheet_ordinal != _SHEET_ORDINALS.get(source_sheet)
            or any(row.get(key) != expected for key, expected in expected_question.items())
            or row.get("source_row_sha256") != _source_row_hash(row)
        ):
            _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
        raw_requirements = row.get("required_calculations")
        if not isinstance(raw_requirements, list):
            _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
        parsed: list[FddFormulaRequirement] = []
        for raw_requirement in raw_requirements:
            if (
                not isinstance(raw_requirement, Mapping)
                or set(raw_requirement) != _REQUIREMENT_KEYS
            ):
                _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
            try:
                requirement = FddFormulaRequirement.model_validate(raw_requirement)
            except (TypeError, ValueError):
                _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
            parsed.append(requirement)
            required_projection.append(
                {
                    "formula_ordinal": requirement.formula_ordinal,
                    "formula_type": requirement.formula_type.value,
                    "source_sheet": source_sheet,
                    "source_row": workbook_row.source_row,
                    "required": requirement.required,
                    "scope": requirement.scope.value,
                }
            )
        requirements_by_question.append(tuple(parsed))

    required_projection.sort(key=lambda item: item["formula_ordinal"])
    formula_projection: list[dict[str, Any]] = []
    for formula in formula_rows:
        if not isinstance(formula, Mapping):
            _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
        formula_projection.append(
            {
                "formula_ordinal": formula.get("formula_ordinal"),
                "formula_type": formula.get("formula_type"),
                "source_sheet": formula.get("anchor_sheet"),
                "source_row": formula.get("anchor_row"),
                "required": formula.get("required"),
                "scope": formula.get("scope"),
            }
        )
    if (
        required_projection != transition_map
        or formula_projection != transition_map
        or {row["formula_type"] for row in required_projection}
        != {item.value for item in FddFormulaType}
        or {row["scope"] for row in required_projection} != {item.value for item in FddFormulaScope}
        or [row["formula_ordinal"] for row in required_projection] != list(range(1, 11))
        or any(row["required"] is not True for row in required_projection)
        or sum(bool(row["required_calculations"]) for row in rows) != 10
    ):
        _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
    return tuple(requirements_by_question)


def import_fdd_formula_policy_v2_registry(
    workbook: bytes | Path | str | BinaryIO,
    *,
    policy_artifact: bytes | Path | str | BinaryIO,
) -> MethodologyRegistry:
    """Produce the sole policy-v2 successor after all frozen inputs agree."""

    policy_raw, _ = _read_bytes(
        policy_artifact,
        code="FDD_FORMULA_POLICY_V2_ARTIFACT_MISMATCH",
    )
    policy = _validated_policy(policy_raw)
    try:
        workbook_projection = validated_production_fdd_workbook_projection(workbook)
    except Exception:
        _fail("FDD_FORMULA_POLICY_V2_WORKBOOK_MISMATCH")
    if (
        workbook_projection.source_byte_count != FDD_FORMULA_POLICY_V2_WORKBOOK_BYTE_COUNT
        or workbook_projection.source_hash != FDD_FORMULA_POLICY_V2_WORKBOOK_SHA256
    ):
        _fail("FDD_FORMULA_POLICY_V2_WORKBOOK_MISMATCH")

    # This completes the full policy/workbook join before the first successor
    # MethodologyQuestion is instantiated.
    requirement_rows = _validated_join(policy, workbook_projection)
    successor_questions: list[MethodologyQuestion] = []
    for workbook_row, requirements in zip(
        workbook_projection.rows,
        requirement_rows,
        strict=True,
    ):
        try:
            question = _question(
                source_hash=workbook_projection.source_hash,
                source_name="configured-fdd-workbook",
                source_type="operator_workbook",
                version_id=FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID,
                sheet_name=workbook_row.source_sheet,
                row_number=workbook_row.source_row,
                term=workbook_row.term,
                nature=workbook_row.nature,
                line_item=workbook_row.line_item,
                question_text=workbook_row.question_text,
                source_anchor_cell=workbook_row.source_anchor_cell,
                required_calculations=requirements,
            )
        except (TypeError, ValueError):
            _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
        if question.methodology_question_id != workbook_row.methodology_question_id:
            _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
        successor_questions.append(question)

    try:
        successor = MethodologyRegistry(
            methodology_id="financial_dd",
            methodology_type=MethodologyType.FINANCIAL_DD,
            versions=(
                MethodologyVersion(
                    methodology_id="financial_dd",
                    methodology_version_id=FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID,
                    methodology_type=MethodologyType.FINANCIAL_DD,
                    version_label=cast(str, policy["metadata"]["policy_id"]),
                    source_hash=workbook_projection.source_hash,
                    source_name="configured-fdd-workbook",
                    questions=tuple(successor_questions),
                ),
            ),
        )
    except (TypeError, ValueError):
        _fail("FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID")
    return successor


__all__ = [
    "FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256",
    "FDD_FORMULA_POLICY_V2_WORKBOOK_BYTE_COUNT",
    "FddFormulaPolicyV2Error",
    "import_fdd_formula_policy_v2_registry",
]
