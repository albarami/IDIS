"""Independent disposable-PostgreSQL census for Task-8 v24 authorities."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_SCHEMA = "task8-v24-postgres-authority-census-v1"
_RECEIPT_SCHEMA = "task8-v24-postgres-authority-census-receipt-v1"
_AUTHORITY_IDS = (
    "dashboard-root-equation-v2",
    "dashboard-partial-native-root-exact-one-v1",
    "task8-current48-dashboard-erasure-refusal-v1",
)
_REFUSAL_CODE = "TASK8_DASHBOARD_ERASURE_FUTURE38_REQUIRED"
_REFUSAL_SQLSTATE = "23514"
_DASHBOARD_REJECTION_CODE = "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"
_PARTIAL_EXACT_ONE_CODE = "STEP_OUTPUT_MISSING"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AUTHORITY_EXECUTION_SCHEMA = "task8-v24-hash-bound-authority-execution-v1"
_ACL_FUNCTION_HASH_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_REGISTRY_HASH_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"
_ERASURE_OWNER = "idis_erasure_function_owner"
_GUARD_OWNER = "idis_task7_guard_function_owner"
_EXACT_DASHBOARD_ERASURE_ACL = (
    "GRANT SELECT (tenant_id,deal_id) ON TABLE "
    "public.methodology_truth_dashboard_rows TO idis_erasure_function_owner"
)
PIPELINE_CONTRACT_VERSION = "full-strict-v24"
FULL_STRICT_V24_CONTRACT_HASH = "f05f17f98ea6a8d0356cef5939b7395fef294eb490a77cf8aeecf1ece25dd164"
CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION = "full-strict-v21"
CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH = (
    "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
)
_RUN_STEP_NAMESPACE = uuid.UUID("d26b136c-a25e-53b7-acb4-84798eaec9d8")
_STEP_OUTPUT_NAMESPACE = uuid.UUID("1b789979-1603-5f0e-bb46-0a79a623a2b7")
_DASHBOARD_ROW_NAMESPACE = uuid.UUID("6aef4159-c49a-560a-9f92-6c6089d7f7c3")
_DASHBOARD_ROOT_LINK_NAMESPACE = uuid.UUID("58a9370f-69f9-5873-b6b7-508272aee80e")
_OUTPUT_OBJECT_NAMESPACE = uuid.UUID("b3ef55a1-2eef-5712-836c-4870bc3ef6d4")
_CENSUS_FIXTURE_NAMESPACE = uuid.UUID("5812f9bc-1b42-5f18-bd70-94907cd6c93a")
_FIXTURE_OCCURRED_AT = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
_CURRENT_STEP_ROWS = (
    (
        4,
        "METHODOLOGY_COVERAGE_INIT",
        "5b157c8329a0a1c229f6ffb3ec2cfc0c0be1c6fb85f0dc0a669883aa1eb75dbc",
    ),
    (
        5,
        "METHODOLOGY_EXTRACTION_TASK_PLANNING",
        "07e0980ebfa04d3aae5042ec39b07200cacc87cc5f8ad40db6338faa56bb38af",
    ),
    (
        6,
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION",
        "0550f5d7335852771e830ae9f376a9633813a24fd7afc677c32c45ee1446dded",
    ),
    (
        7,
        "METHODOLOGY_CLAIM_MATERIALIZATION",
        "63ae11aa758eea76bfa4931cc35ea6bd2b42a827d58b7f058f000db2e439792c",
    ),
    (
        8,
        "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION",
        "7ca82a99ce4bf6782d76b17fdc8434bf98fc92e1c4626bcf2121004bed63f94e",
    ),
    (
        9,
        "METHODOLOGY_SANAD_CREATION_LINKING_GRADING",
        "ca4f4b8c77d274999f9593829ff76cbe0839e05f82082a37d8554cc1bff2dd52",
    ),
    (
        10,
        "METHODOLOGY_DETERMINISTIC_CALCULATION",
        "2db75b6695f26cfbf7f09ef0a440da557c15eb9b960be86d78f62f70412617ed",
    ),
    (
        11,
        "METHODOLOGY_TRUTH_DASHBOARD",
        "29a48e733fe07251ed0bb509311ddfbb36936876913c84ce149e79cd5336eea9",
    ),
)
_NATIVE_OUTPUT_STEP_BY_KIND = {
    "METHODOLOGY_COVERAGE_IDENTITY": 4,
    "METHODOLOGY_EXTRACTION_TASK": 5,
    "METHODOLOGY_EXTRACTION_RESULT": 6,
    "METHODOLOGY_FACTUAL_OUTPUT": 6,
    "METHODOLOGY_CLAIM_LINK": 7,
    "METHODOLOGY_EVIDENCE_LINK": 8,
    "METHODOLOGY_SANAD_LINK": 9,
    "METHODOLOGY_DEFECT_LINK": 9,
    "METHODOLOGY_CALCULATION": 10,
    "METHODOLOGY_TRUTH_DASHBOARD_ROW": 11,
}


class Task8V24CensusError(RuntimeError):
    """Raised with a stable safe code when the census cannot be trusted."""


def _fail(code: str) -> None:
    raise Task8V24CensusError(code)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def complete_execution_sha256(
    case_result_sha256: str,
    catalog_projection_sha256: str,
    race_result_sha256: str,
    rls_behavior_sha256: str,
    partial_authority_udu_sha256: str,
    erasure_authority_udu_sha256: str,
) -> str:
    """Derive the one shared complete-execution digest preimage."""

    fields = (
        case_result_sha256,
        catalog_projection_sha256,
        race_result_sha256,
        rls_behavior_sha256,
        partial_authority_udu_sha256,
        erasure_authority_udu_sha256,
    )
    if any(not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None for value in fields):
        _fail("TASK8_V24_CENSUS_EXECUTION_DIGEST_INVALID")
    return _canonical_sha256(["task8-v24-census-execution-v1", *fields])


def _uuid5_from_preimage(namespace: uuid.UUID, value: object) -> str:
    return str(uuid.uuid5(namespace, _canonical_sha256(value)))


def build_current_run_step_plan(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> tuple[dict[str, object], ...]:
    """Return the exact current-0038 step coordinates used by the census run."""

    try:
        scope = tuple(str(uuid.UUID(value)) for value in (tenant_id, deal_id, run_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_SCOPE_INVALID") from exc
    rows: list[dict[str, object]] = []
    for step_order, step_name, semantic_hash in _CURRENT_STEP_ROWS:
        step_id = _uuid5_from_preimage(
            _RUN_STEP_NAMESPACE,
            [
                "run-step-v1",
                *scope,
                CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
                CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
                step_name,
                step_order,
            ],
        )
        rows.append(
            {
                "step_id": step_id,
                "tenant_id": scope[0],
                "deal_id": scope[1],
                "run_id": scope[2],
                "pipeline_contract_version": CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
                "pipeline_contract_hash": CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
                "step_order": step_order,
                "step_name": step_name,
                "semantic_contract_version": "2",
                "semantic_contract_hash": semantic_hash,
            }
        )
    return tuple(rows)


def build_seed_native_output_manifest() -> dict[str, object]:
    """Bind every Task-8 census native kind to its reviewed producer step."""

    output_kinds = tuple(_NATIVE_OUTPUT_STEP_BY_KIND)
    payload = {
        "schema_version": "task8-v24-census-native-output-manifest-v1",
        "output_kind_tuple": output_kinds,
        "producer_step_tuple": [
            [output_kind, _NATIVE_OUTPUT_STEP_BY_KIND[output_kind]] for output_kind in output_kinds
        ],
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def _dashboard_authority_sql(v24: Mapping[str, Any]) -> str:
    try:
        authority = next(
            row
            for row in v24["sql_authority_registry"]["rows"]
            if row.get("authority_id") == "dashboard-root-equation-v2"
        )
        statements = tuple(
            bytes.fromhex(value).decode("utf-8") for value in authority["body_sql_utf8_hex_tuple"]
        )
    except (KeyError, StopIteration, TypeError, UnicodeError, ValueError) as exc:
        raise Task8V24CensusError("TASK8_V24_DASHBOARD_REQUIREMENT_BINDING_INVALID") from exc
    if (
        authority.get("expected_sha256")
        != "513ca92f3e3c1c610af724413a1342743fc09b781fcb6fe6d6481b50f8570708"
        or hashlib.sha256("\n".join(statements).encode("utf-8")).hexdigest()
        != "6a6113a72f6e68011e7d6c992060faccf63201c3dd76153b61e5a56386cff34c"
    ):
        _fail("TASK8_V24_DASHBOARD_REQUIREMENT_BINDING_INVALID")
    return "\n".join(statements)


def validate_v24_dashboard_requirement_binding(
    v24: Mapping[str, Any],
    financial_registry: object,
) -> None:
    """Fail before SQL execution when policy-v2 requirement keys do not bind."""

    try:
        version = financial_registry.current_version
        questions = tuple(version.questions)
        requirements = tuple(
            requirement.model_dump(mode="json")
            for question in questions
            for requirement in question.required_calculations
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise Task8V24CensusError("TASK8_V24_DASHBOARD_REQUIREMENT_BINDING_INVALID") from exc
    expected_keys = {"formula_ordinal", "formula_type", "required", "scope"}
    dashboard_sql = _dashboard_authority_sql(v24)
    if (
        len(questions) != 79
        or len(requirements) != 10
        or any(set(item) != expected_keys for item in requirements)
        or any(item["required"] is not True for item in requirements)
        or dashboard_sql.count("item->>'calc_type'") != 3
        or "item->>'formula_type'" not in dashboard_sql
        or "item->>'formula_ordinal'" not in dashboard_sql
    ):
        _fail("TASK8_V24_DASHBOARD_REQUIREMENT_BINDING_INVALID")


def derive_v24_dashboard_identity(**inputs: Any) -> dict[str, object]:
    """Derive the reviewed acyclic five-stage dashboard identity chain."""

    required = {
        "tenant_id",
        "deal_id",
        "run_id",
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "coverage_record_id",
        "methodology_version_record_id",
        "methodology_question_record_id",
        "question_payload_hash",
        "row_disposition",
        "disposition_reason_code",
        "row_trace_prefix",
        "result_records",
        "answer_values",
        "task_gaps",
        "calculations",
        "roots",
    }
    if set(inputs) != required or not isinstance(inputs["roots"], list):
        _fail("TASK8_V24_CENSUS_IDENTITY_INPUT_INVALID")
    tenant_id = str(inputs["tenant_id"])
    deal_id = str(inputs["deal_id"])
    run_id = str(inputs["run_id"])
    pipeline_version = str(inputs["pipeline_contract_version"])
    pipeline_hash = str(inputs["pipeline_contract_hash"])
    coverage_record_id = str(inputs["coverage_record_id"])
    dashboard_row_id = _uuid5_from_preimage(
        _DASHBOARD_ROW_NAMESPACE,
        [
            "methodology-truth-dashboard-row-v2",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            coverage_record_id,
            inputs["methodology_version_record_id"],
            inputs["methodology_question_record_id"],
            inputs["question_payload_hash"],
        ],
    )
    root_link_tuple: list[list[object]] = []
    for root in sorted(
        inputs["roots"],
        key=lambda value: (
            str(value["root_kind"]),
            int(value["root_ordinal"]),
            str(value["root_output_object_id"]),
        ),
    ):
        link_id = _uuid5_from_preimage(
            _DASHBOARD_ROOT_LINK_NAMESPACE,
            [
                "methodology-truth-dashboard-root-link-v1",
                tenant_id,
                deal_id,
                run_id,
                dashboard_row_id,
                root["root_kind"],
                root["root_ordinal"],
                root["root_output_object_id"],
            ],
        )
        root_link_tuple.append(
            [
                root["root_kind"],
                root["root_ordinal"],
                root["root_output_object_id"],
                root["root_output_object_kind"],
                link_id,
            ]
        )
    projection_sha256 = _canonical_sha256(
        [
            "methodology-truth-dashboard-root-projection-v2",
            tenant_id,
            deal_id,
            run_id,
            dashboard_row_id,
            coverage_record_id,
            inputs["row_disposition"],
            len(root_link_tuple),
            root_link_tuple,
        ]
    )
    row_payload_sha256 = _canonical_sha256(
        [
            "financial-diligence-row-v2",
            [*inputs["row_trace_prefix"], coverage_record_id, dashboard_row_id],
            inputs["row_disposition"],
            inputs["disposition_reason_code"],
            inputs["result_records"],
            inputs["answer_values"],
            inputs["task_gaps"],
            inputs["calculations"],
            projection_sha256,
        ]
    )
    output_kind = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
    native_identity_sha256 = _canonical_sha256(
        [
            "native-identity-v1",
            tenant_id,
            deal_id,
            run_id,
            output_kind,
            [dashboard_row_id],
        ]
    )
    native_output_sha256 = _canonical_sha256(
        [
            "native-output-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            output_kind,
            [dashboard_row_id],
            [
                coverage_record_id,
                projection_sha256,
                inputs["row_disposition"],
                row_payload_sha256,
            ],
        ]
    )
    output_object_id = _uuid5_from_preimage(
        _OUTPUT_OBJECT_NAMESPACE,
        [
            "run-output-object-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            "NATIVE_ROW",
            output_kind,
            native_identity_sha256,
            native_output_sha256,
        ],
    )
    return {
        "dashboard_row_id": dashboard_row_id,
        "root_link_tuple": root_link_tuple,
        "root_projection_sha256": projection_sha256,
        "row_payload_sha256": row_payload_sha256,
        "native_identity_sha256": native_identity_sha256,
        "native_output_sha256": native_output_sha256,
        "output_object_id": output_object_id,
    }


def _fixture_uuid(label: str, *coordinates: object) -> str:
    return _uuid5_from_preimage(
        _CENSUS_FIXTURE_NAMESPACE,
        ["task8-v24-postgres-census-fixture-v1", label, *coordinates],
    )


_fixture_uuid_ordinal = 0


def _reset_fixture_uuid_sequence() -> None:
    global _fixture_uuid_ordinal
    _fixture_uuid_ordinal = 0


def _next_fixture_uuid() -> uuid.UUID:
    """Return the next deterministic disposable-fixture UUID."""

    global _fixture_uuid_ordinal
    _fixture_uuid_ordinal += 1
    return uuid.UUID(_fixture_uuid("ordered-fixture-id", _fixture_uuid_ordinal))


def _normalize_methodology_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    return re.sub(r"\s+", " ", value.strip().lower())


def _derive_methodology_question_seed(
    source: Mapping[str, Any],
    *,
    tenant_id: str,
    calculation_required: bool,
) -> dict[str, Any]:
    methodology_type = source.get("methodology_type")
    ordinal = source.get("ordinal")
    if (
        methodology_type not in {"commercial_dd", "financial_dd"}
        or not isinstance(ordinal, int)
        or ordinal <= 0
        or not isinstance(calculation_required, bool)
    ):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    try:
        canonical_tenant_id = str(uuid.UUID(tenant_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_QUESTION_SEED_INVALID") from exc
    required_calculations = source.get("required_calculations")
    if not isinstance(required_calculations, list) or any(
        not isinstance(item, Mapping) for item in required_calculations
    ):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    scenario_calculations = [
        {**dict(item), "required": calculation_required} for item in required_calculations
    ]
    if not scenario_calculations:
        scenario_calculations = [
            {
                "calc_type": "TASK8_CENSUS_BRIDGE",
                "required": calculation_required,
            }
        ]
    source_row_number = source.get("source_row_number")
    source_anchor_cell = source.get("source_anchor_cell")
    if methodology_type == "commercial_dd":
        source_row_number = None
        source_anchor_cell = None
    elif (
        not isinstance(source_row_number, int)
        or source_row_number <= 0
        or not isinstance(source_anchor_cell, str)
        or re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", source_anchor_cell) is None
    ):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    source_sha256 = source.get("source_row_sha256")
    question_id = source.get("methodology_question_id")
    source_sheet = source.get("source_sheet")
    section = source.get("section")
    question_text = source.get("question_text")
    if (
        not isinstance(source_sha256, str)
        or _SHA256_RE.fullmatch(source_sha256) is None
        or not isinstance(question_id, str)
        or not question_id.strip()
        or not isinstance(source_sheet, str)
        or not source_sheet.strip()
        or not isinstance(section, str)
        or not section.strip()
        or not isinstance(question_text, str)
        or not question_text.strip()
    ):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    methodology_version_id = f"task8-census-{methodology_type}-v1"
    version_record_id = _fixture_uuid(
        "methodology-version-record",
        canonical_tenant_id,
        methodology_type,
    )
    question_record_id = _fixture_uuid(
        "methodology-question-record",
        canonical_tenant_id,
        methodology_type,
        question_id,
    )
    question_payload = {
        "methodology_id": methodology_type,
        "methodology_version_id": methodology_version_id,
        "methodology_question_id": question_id,
        "methodology_type": methodology_type,
        "section": section,
        "sheet_or_source_section": source_sheet,
        "source_row_number": source_row_number,
        "term": source.get("term"),
        "nature": source.get("nature"),
        "line_item": source.get("line_item"),
        "question_text": question_text,
        "required_calculations": scenario_calculations,
        "source_trace": {
            "source_type": (
                "operator_workbook" if methodology_type == "financial_dd" else "checked_in_template"
            ),
            "source_name": (
                "configured-fdd-workbook"
                if methodology_type == "financial_dd"
                else "task8-census-commercial-template"
            ),
            "source_hash": source_sha256,
            "sheet_or_section": source_sheet,
            "row_number": source_row_number,
            "source_anchor_cell": source_anchor_cell,
        },
    }
    semantic_hash_preimage = {
        "methodology_type": methodology_type,
        "section": _normalize_methodology_text(section),
        "sheet_or_source_section": _normalize_methodology_text(source_sheet),
        "term": _normalize_methodology_text(source.get("term")),
        "nature": _normalize_methodology_text(source.get("nature")),
        "line_item": _normalize_methodology_text(source.get("line_item")),
        "question_text": _normalize_methodology_text(question_text),
    }
    return {
        "schema_version": "task8-v24-methodology-question-seed-v1",
        "tenant_id": canonical_tenant_id,
        "methodology_version_record_id": version_record_id,
        "methodology_question_record_id": question_record_id,
        "methodology_version_id": methodology_version_id,
        "methodology_question_id": question_id,
        "methodology_id": methodology_type,
        "methodology_type": methodology_type,
        "question_ordinal": ordinal,
        "section": section,
        "source_sheet_or_section": source_sheet,
        "source_row_number": source_row_number,
        "source_anchor_cell": source_anchor_cell,
        "source_sha256": source_sha256,
        "semantic_hash_preimage": semantic_hash_preimage,
        "semantic_row_hash": _canonical_sha256(semantic_hash_preimage),
        "question_payload": question_payload,
        "question_payload_hash": _canonical_sha256(question_payload),
        "source_row_preimage": dict(source),
    }


def build_methodology_question_seed_rows(
    policy: Mapping[str, Any],
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Derive the sole 21+79 question-row fixture contract."""

    metadata = policy.get("metadata")
    row_registry = policy.get("workbook_row_registry")
    financial_rows = row_registry.get("rows") if isinstance(row_registry, Mapping) else None
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("policy_id") != "fdd-formula-policy-v2"
        or not isinstance(financial_rows, list)
        or len(financial_rows) != 79
    ):
        _fail("TASK8_V24_CENSUS_POLICY_INVALID")
    source_rows: list[dict[str, Any]] = [
        {
            "methodology_type": "commercial_dd",
            "ordinal": ordinal,
            "section": "Task8 census commercial",
            "source_sheet": f"CDD {ordinal:02d}",
            "source_row_number": None,
            "source_anchor_cell": None,
            "source_anchor_label": f"CDD!{ordinal}",
            "term": f"Commercial term {ordinal:02d}",
            "nature": "Commercial",
            "line_item": f"Commercial line item {ordinal:02d}",
            "question_text": f"Task8 census commercial question {ordinal:02d}",
            "methodology_question_id": f"mq_commercial_dd_task8_census_{ordinal:04d}",
            "required_calculations": [],
            "source_row_sha256": _canonical_sha256(["task8-v24-cdd-census-row-v1", ordinal]),
        }
        for ordinal in range(1, 22)
    ]
    for row in financial_rows:
        if not isinstance(row, Mapping):
            _fail("TASK8_V24_CENSUS_POLICY_INVALID")
        source_rows.append(
            {
                "methodology_type": "financial_dd",
                "ordinal": row.get("row_ordinal"),
                "section": row.get("source_sheet"),
                "source_sheet": row.get("source_sheet"),
                "source_row_number": row.get("source_row"),
                "source_anchor_cell": row.get("source_anchor_cell"),
                "source_anchor_label": (
                    f"{row.get('source_sheet')}!{row.get('source_anchor_cell')}"
                ),
                "term": row.get("term"),
                "nature": row.get("nature"),
                "line_item": row.get("line_item"),
                "question_text": row.get("question_text"),
                "methodology_question_id": row.get("methodology_question_id"),
                "required_calculations": row.get("required_calculations"),
                "source_row_sha256": row.get("source_row_sha256"),
            }
        )
    rows = [
        _derive_methodology_question_seed(
            source,
            tenant_id=tenant_id,
            calculation_required=False,
        )
        for source in source_rows
    ]
    if (
        len(rows) != 100
        or len({row["methodology_question_record_id"] for row in rows}) != 100
        or any(
            len(
                {
                    row["semantic_row_hash"]
                    for row in rows
                    if row["methodology_type"] == methodology_type
                }
            )
            != expected
            for methodology_type, expected in (("commercial_dd", 21), ("financial_dd", 79))
        )
    ):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    return rows


def _question_seed_for_calculation_requirement(
    question_seed: Mapping[str, Any],
    *,
    required: bool,
) -> dict[str, Any]:
    source = question_seed.get("source_row_preimage")
    tenant_id = question_seed.get("tenant_id")
    if not isinstance(source, Mapping) or not isinstance(tenant_id, str):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    return _derive_methodology_question_seed(
        source,
        tenant_id=tenant_id,
        calculation_required=required,
    )


def build_v24_dashboard_seed_plan(
    artifact: Mapping[str, Any],
    policy_raw: bytes,
    workbook_raw: bytes,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Build the safe plan from the two production methodology constructors."""

    from idis.methodology.importers.cdd_json import import_cdd_template
    from idis.methodology.importers.fdd_formula_policy_v2 import (
        import_fdd_formula_policy_v2_registry,
    )
    from idis.models.methodology_coverage import MethodologyCoverageRecord
    from idis.persistence.repositories.methodologies import (
        methodology_question_payload_hashes,
        methodology_source_trace_digest,
        methodology_version_hash,
    )

    _validate_v24(artifact)
    if not isinstance(policy_raw, bytes) or not isinstance(workbook_raw, bytes):
        _fail("TASK8_V24_CENSUS_POLICY_INVALID")
    try:
        canonical_scope = tuple(str(uuid.UUID(value)) for value in (tenant_id, deal_id, run_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_SCOPE_INVALID") from exc
    cdd_template = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "idis"
        / "methodology"
        / "templates"
        / "commercial_dd_v1.json"
    )
    try:
        cdd_raw = cdd_template.read_bytes()
    except OSError as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_CDD_TEMPLATE_INVALID") from exc
    if hashlib.sha256(cdd_raw).hexdigest() != (
        "57cfec98436514cae717e359fa872504a7b9e1115c7bc12a4561e0989c4281fc"
    ):
        _fail("TASK8_V24_CENSUS_CDD_TEMPLATE_INVALID")
    try:
        commercial = import_cdd_template(cdd_template)
        financial = import_fdd_formula_policy_v2_registry(
            workbook_raw,
            policy_artifact=policy_raw,
        )
    except Exception as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_POLICY_INVALID") from exc
    validate_v24_dashboard_requirement_binding(artifact, financial)

    def registry_identity(registry: Any) -> dict[str, object]:
        question_hashes = methodology_question_payload_hashes(registry)
        return {
            "methodology_type": registry.methodology_type.value,
            "methodology_version_id": registry.current_version.methodology_version_id,
            "source_sha256": registry.current_version.source_hash,
            "registry_sha256": hashlib.sha256(
                registry.to_deterministic_json().encode("utf-8")
            ).hexdigest(),
            "version_sha256": methodology_version_hash(registry),
            "source_trace_sha256": methodology_source_trace_digest(registry),
            "question_count": len(question_hashes),
            "ordered_question_hashes_sha256": _canonical_sha256(list(question_hashes)),
        }

    registry_rows = (commercial, financial)
    rows: list[dict[str, object]] = []
    coverage_ordinal = 0
    for registry in registry_rows:
        version = registry.current_version
        version_record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "idis:methodology-version:"
                f"{canonical_scope[0]}:{version.methodology_version_id}:{version.source_hash}",
            )
        )
        for question_ordinal, question in enumerate(version.questions, start=1):
            coverage_ordinal += 1
            question_record_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "idis:methodology-question:"
                    f"{canonical_scope[0]}:{version_record_id}:"
                    f"{question.methodology_question_id}",
                )
            )
            question_payload = question.model_dump(mode="json")
            question_payload_sha256 = _canonical_sha256(question_payload)
            coverage = MethodologyCoverageRecord(
                tenant_id=canonical_scope[0],
                deal_id=canonical_scope[1],
                run_id=canonical_scope[2],
                methodology_id=question.methodology_id,
                methodology_version_id=question.methodology_version_id,
                methodology_question_id=question.methodology_question_id,
                methodology_type=question.methodology_type,
                section=question.section,
            )
            dashboard_row_id = _uuid5_from_preimage(
                _DASHBOARD_ROW_NAMESPACE,
                [
                    "methodology-truth-dashboard-row-v2",
                    *canonical_scope,
                    CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
                    CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
                    coverage.coverage_record_id,
                    version_record_id,
                    question_record_id,
                    question_payload_sha256,
                ],
            )
            requirements = [
                requirement.model_dump(mode="json")
                for requirement in question.required_calculations
            ]
            rows.append(
                {
                    "coverage_ordinal": coverage_ordinal,
                    "methodology_type": question.methodology_type.value,
                    "question_ordinal": question_ordinal,
                    "methodology_version_id": question.methodology_version_id,
                    "methodology_version_record_id": version_record_id,
                    "methodology_question_id": question.methodology_question_id,
                    "methodology_question_record_id": question_record_id,
                    "coverage_record_id": coverage.coverage_record_id,
                    "question_payload_sha256": question_payload_sha256,
                    "source_trace_sha256": _canonical_sha256(
                        question.source_trace.model_dump(mode="json")
                    ),
                    "required_calculation_tuple": requirements,
                    "required_calculation_count": len(requirements),
                    "base_row_disposition": "ANSWERED" if requirements else "GAP",
                    "dashboard_row_id": dashboard_row_id,
                }
            )
    commercial_count = sum(row["methodology_type"] == "commercial_dd" for row in rows)
    financial_count = sum(row["methodology_type"] == "financial_dd" for row in rows)
    required_count = sum(row["required_calculation_count"] for row in rows)
    if (
        len(rows) != 100
        or commercial_count != 21
        or financial_count != 79
        or required_count != 10
        or len({row["coverage_record_id"] for row in rows}) != 100
        or len({row["dashboard_row_id"] for row in rows}) != 100
    ):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    payload = {
        "schema_version": "task8-v24-dashboard-seed-plan-v2",
        "tenant_id": canonical_scope[0],
        "deal_id": canonical_scope[1],
        "run_id": canonical_scope[2],
        "pipeline_contract_version": CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
        "pipeline_contract_hash": CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
        "coverage_row_count": len(rows),
        "commercial_row_count": commercial_count,
        "financial_row_count": financial_count,
        "required_calculation_count": required_count,
        "commercial_registry_identity": registry_identity(commercial),
        "financial_registry_identity": registry_identity(financial),
        "row_tuple": rows,
    }
    plan = {**payload, "expected_sha256": _canonical_sha256(payload)}
    validate_governed_seed_hashes(plan)
    return plan


def validate_governed_seed_hashes(plan: Mapping[str, Any]) -> None:
    """Independently rederive every governed identity available before SQL."""

    payload = {key: value for key, value in plan.items() if key != "expected_sha256"}
    rows = plan.get("row_tuple")
    if (
        plan.get("schema_version") != "task8-v24-dashboard-seed-plan-v2"
        or plan.get("expected_sha256") != _canonical_sha256(payload)
        or not isinstance(rows, list)
        or len(rows) != 100
    ):
        _fail("TASK8_V24_CENSUS_GOVERNED_SEED_INVALID")
    try:
        tenant_id, deal_id, run_id = (
            str(uuid.UUID(str(plan[key]))) for key in ("tenant_id", "deal_id", "run_id")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_GOVERNED_SEED_INVALID") from exc
    registry_by_type = {
        str(plan["commercial_registry_identity"]["methodology_type"]): plan[
            "commercial_registry_identity"
        ],
        str(plan["financial_registry_identity"]["methodology_type"]): plan[
            "financial_registry_identity"
        ],
    }
    for row in rows:
        try:
            methodology_type = str(row["methodology_type"])
            registry = registry_by_type[methodology_type]
            version_id = str(row["methodology_version_id"])
            question_id = str(row["methodology_question_id"])
            source_sha256 = str(registry["source_sha256"])
            expected_version_record_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"idis:methodology-version:{tenant_id}:{version_id}:{source_sha256}",
                )
            )
            expected_question_record_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "idis:methodology-question:"
                    f"{tenant_id}:{expected_version_record_id}:{question_id}",
                )
            )
            expected_coverage_record_id = (
                "mc_"
                + hashlib.sha256(
                    "|".join((tenant_id, deal_id, run_id, version_id, question_id)).encode("utf-8")
                ).hexdigest()[:24]
            )
            expected_dashboard_row_id = _uuid5_from_preimage(
                _DASHBOARD_ROW_NAMESPACE,
                [
                    "methodology-truth-dashboard-row-v2",
                    tenant_id,
                    deal_id,
                    run_id,
                    CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
                    CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
                    expected_coverage_record_id,
                    expected_version_record_id,
                    expected_question_record_id,
                    row["question_payload_sha256"],
                ],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise Task8V24CensusError("TASK8_V24_CENSUS_GOVERNED_SEED_INVALID") from exc
        if (
            row.get("methodology_version_record_id") != expected_version_record_id
            or row.get("methodology_question_record_id") != expected_question_record_id
            or row.get("coverage_record_id") != expected_coverage_record_id
            or row.get("dashboard_row_id") != expected_dashboard_row_id
        ):
            _fail("TASK8_V24_CENSUS_GOVERNED_SEED_INVALID")
        for key, value in row.items():
            if (
                key.endswith("_sha256")
                and isinstance(value, str)
                and (_SHA256_RE.fullmatch(value) is None or len(set(value)) == 1)
            ):
                _fail(f"TASK8_V24_CENSUS_GOVERNED_SEED_INVALID:{key}")


def build_seed_fk_closure_manifest(v24: Mapping[str, Any]) -> dict[str, object]:
    """Declare the complete parent surface the production-shaped seed requires."""

    _validate_v24(v24)
    required_parents = (
        "tenants",
        "deals",
        "runs",
        "run_steps",
        "data_room_packages",
        "document_artifacts",
        "documents",
        "document_parse_attempts",
        "document_spans",
        "source_lineage_objects",
        "methodology_versions",
        "methodology_questions",
        "run_methodology_selections",
        "methodology_coverage_records",
        "methodology_extraction_tasks",
        "methodology_extraction_task_spans",
        "methodology_extraction_attempts",
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
        "methodology_result_claim_links",
        "claims",
        "methodology_result_evidence_links",
        "evidence_items",
        "methodology_result_sanad_links",
        "sanads",
        "methodology_result_defect_links",
        "defects",
        "run_output_objects",
        "run_step_outputs",
    )
    payload = {
        "schema_version": "task8-v24-seed-fk-closure-manifest-v1",
        "required_parent_relation_tuple": required_parents,
        "session_replication_role_bypass_count": 0,
        "expected_fk_violation_count": 0,
        "observer_role": "application",
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def _observe_seed_fk_closure(
    application_url: str,
    *,
    expected_application_role: str,
    tenant_id: uuid.UUID,
    deal_id: uuid.UUID,
) -> dict[str, object]:
    """Force all deferred keys and observe the seeded closure as the app role."""

    connection = _connect(application_url)
    try:
        with connection.cursor() as cursor:
            observer_role = _attest_connection_role_posture(
                cursor,
                expected_application_role,
            )
            _set_tenant(cursor, tenant_id)
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                """
                SELECT count(*)
                  FROM pg_catalog.pg_constraint AS c
                  JOIN pg_catalog.pg_class AS child ON child.oid=c.conrelid
                  JOIN pg_catalog.pg_namespace AS n ON n.oid=child.relnamespace
                 WHERE n.nspname='public'
                   AND c.contype='f'
                   AND NOT c.convalidated
                """
            )
            unvalidated_fk_count = int(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM public.methodology_coverage_records c
                     WHERE c.tenant_id=%s AND c.deal_id=%s
                       AND NOT EXISTS (
                           SELECT 1 FROM public.run_methodology_selections s
                            WHERE s.tenant_id=c.tenant_id
                              AND s.deal_id=c.deal_id
                              AND s.run_id=c.run_id
                              AND s.selection_id=c.selection_id
                       )
                    UNION ALL
                    SELECT 1 FROM public.methodology_extraction_tasks t
                     WHERE t.tenant_id=%s AND t.deal_id=%s
                       AND (
                           NOT EXISTS (
                               SELECT 1 FROM public.methodology_coverage_records c
                                WHERE c.tenant_id=t.tenant_id
                                  AND c.deal_id=t.deal_id
                                  AND c.run_id=t.run_id
                                  AND c.coverage_record_id=t.coverage_record_id
                           )
                           OR NOT EXISTS (
                               SELECT 1 FROM public.documents d
                                WHERE d.tenant_id=t.tenant_id
                                  AND d.deal_id=t.deal_id
                                  AND d.document_id=t.document_id
                           )
                       )
                    UNION ALL
                    SELECT 1 FROM public.methodology_extraction_results r
                     WHERE r.tenant_id=%s AND r.deal_id=%s
                       AND (
                           NOT EXISTS (
                               SELECT 1 FROM public.methodology_extraction_tasks t
                                WHERE t.tenant_id=r.tenant_id
                                  AND t.deal_id=r.deal_id
                                  AND t.run_id=r.run_id
                                  AND t.task_record_id=r.task_record_id
                           )
                           OR NOT EXISTS (
                               SELECT 1 FROM public.methodology_extraction_attempts a
                                WHERE a.tenant_id=r.tenant_id
                                  AND a.deal_id=r.deal_id
                                  AND a.run_id=r.run_id
                                  AND a.attempt_id=r.attempt_id
                           )
                       )
                    UNION ALL
                    SELECT 1 FROM public.methodology_result_evidence_links e
                     WHERE e.tenant_id=%s AND e.deal_id=%s
                       AND (
                           NOT EXISTS (
                               SELECT 1 FROM public.methodology_result_claim_links c
                                WHERE c.tenant_id=e.tenant_id
                                  AND c.deal_id=e.deal_id
                                  AND c.run_id=e.run_id
                                  AND c.claim_link_id=e.claim_link_id
                           )
                           OR NOT EXISTS (
                               SELECT 1 FROM public.evidence_items i
                                WHERE i.tenant_id=e.tenant_id
                                  AND i.deal_id=e.deal_id
                                  AND i.evidence_id=e.evidence_id
                           )
                       )
                    UNION ALL
                    SELECT 1 FROM public.methodology_result_sanad_links s
                     WHERE s.tenant_id=%s AND s.deal_id=%s
                       AND NOT EXISTS (
                           SELECT 1 FROM public.sanads p
                            WHERE p.tenant_id=s.tenant_id
                              AND p.deal_id=s.deal_id
                              AND p.sanad_id=s.sanad_id
                       )
                    UNION ALL
                    SELECT 1 FROM public.methodology_result_defect_links d
                     WHERE d.tenant_id=%s AND d.deal_id=%s
                       AND NOT EXISTS (
                           SELECT 1 FROM public.defects p
                            WHERE p.tenant_id=d.tenant_id
                              AND p.deal_id=d.deal_id
                              AND p.defect_id=d.defect_id
                       )
                    UNION ALL
                    SELECT 1 FROM public.run_step_outputs so
                     WHERE so.tenant_id=%s AND so.deal_id=%s
                       AND (
                           NOT EXISTS (
                               SELECT 1 FROM public.run_steps s
                                WHERE s.tenant_id=so.tenant_id
                                  AND s.run_id=so.run_id
                                  AND s.step_id=so.step_id
                           )
                           OR NOT EXISTS (
                               SELECT 1 FROM public.run_output_objects ro
                                WHERE ro.tenant_id=so.tenant_id
                                  AND ro.deal_id=so.deal_id
                                  AND ro.run_id=so.run_id
                                  AND ro.output_object_id=so.output_object_id
                           )
                       )
                )
                """,
                tuple(value for _ in range(7) for value in (tenant_id, deal_id)),
            )
            has_orphan = bool(cursor.fetchone()[0])
        connection.rollback()
    finally:
        connection.close()
    if unvalidated_fk_count != 0 or has_orphan:
        _fail("TASK8_V24_CENSUS_SEED_FK_CLOSURE_INVALID")
    payload = {
        "schema_version": "task8-v24-seed-fk-closure-observation-v1",
        "observer_role": observer_role,
        "unvalidated_fk_count": unvalidated_fk_count,
        "orphan_row_count": int(has_orphan),
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def _persist_task5_seed_plan(
    connection: Any,
    *,
    scope: _CaseScope,
    dashboard_seed_plan: Mapping[str, Any],
    commercial_registry: Any,
    financial_registry: Any,
    run_output_object_ids: Mapping[str, str],
) -> tuple[Any, tuple[Any, ...]]:
    """Persist the exact constructor-backed Task-5 plan through production APIs."""

    from idis.methodology.models import MethodologyType
    from idis.models.methodology_coverage import MethodologyCoverageRecord
    from idis.persistence.repositories.methodologies import (
        PostgresMethodologiesRepository,
        methodology_source_trace_digest,
    )

    plan_rows = dashboard_seed_plan.get("row_tuple")
    if (
        dashboard_seed_plan.get("schema_version") != "task8-v24-dashboard-seed-plan-v2"
        or not isinstance(plan_rows, list)
        or len(plan_rows) != 100
        or dashboard_seed_plan.get("expected_sha256")
        != _canonical_sha256(
            {key: value for key, value in dashboard_seed_plan.items() if key != "expected_sha256"}
        )
    ):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    expected_output_ids = {
        str(row["coverage_record_id"]): str(run_output_object_ids[str(row["coverage_record_id"])])
        for row in plan_rows
        if str(row["coverage_record_id"]) in run_output_object_ids
    }
    if len(expected_output_ids) != 100:
        _fail("TASK8_V24_CENSUS_NATIVE_ROOT_INVALID")

    repository = PostgresMethodologiesRepository(connection, str(scope.tenant_id))
    cdd_import = repository.import_registry(
        registry=commercial_registry,
        source_type="checked_in_template",
        source_alias=str(commercial_registry.current_version.source_name),
        source_byte_count=(
            Path(__file__).resolve().parents[1]
            / "src"
            / "idis"
            / "methodology"
            / "templates"
            / "commercial_dd_v1.json"
        )
        .stat()
        .st_size,
        source_contract_hash=("e42ae90f9f8b34657ba022c056cb3a0d4ef20006b89cc71cd1dfbe82b56b9b9a"),
        source_trace_digest=methodology_source_trace_digest(commercial_registry),
        imported_by_actor_id="task8-v24-census-operator",
        import_audit_event_id=_fixture_uuid("cdd-import-audit", scope.tenant_id),
    )
    fdd_import = repository.import_registry(
        registry=financial_registry,
        source_type="operator_workbook",
        source_alias="configured-fdd-workbook",
        source_byte_count=24_905,
        source_contract_hash=("ed191040179c85ee2298b9d53df024600e069a8a66ba758b7ec50ddb76d1612e"),
        source_trace_digest=methodology_source_trace_digest(financial_registry),
        imported_by_actor_id="task8-v24-census-operator",
        import_audit_event_id=_fixture_uuid("fdd-import-audit", scope.tenant_id),
    )
    cdd_frozen = repository.freeze_version(
        methodology_version_record_id=cdd_import.methodology_version_record_id,
        expected_source_sha256=commercial_registry.current_version.source_hash,
        expected_registry=commercial_registry,
        frozen_by_actor_id="task8-v24-census-reviewer",
        freeze_audit_event_id=_fixture_uuid("cdd-freeze-audit", scope.tenant_id),
    )
    fdd_frozen = repository.freeze_version(
        methodology_version_record_id=fdd_import.methodology_version_record_id,
        expected_source_sha256=financial_registry.current_version.source_hash,
        expected_registry=financial_registry,
        frozen_by_actor_id="task8-v24-census-reviewer",
        freeze_audit_event_id=_fixture_uuid("fdd-freeze-audit", scope.tenant_id),
    )
    selection = repository.create_run_selection(
        deal_id=str(scope.deal_id),
        run_id=str(scope.run_id),
        commercial_dd_version_record_id=cdd_frozen.methodology_version_record_id,
        financial_dd_version_record_id=fdd_frozen.methodology_version_record_id,
        created_by_actor_id="task8-v24-census-operator",
        selection_audit_event_id=_fixture_uuid("selection-audit", scope.run_id),
    )
    records: list[MethodologyCoverageRecord] = []
    for registry in (commercial_registry, financial_registry):
        for question in registry.current_version.questions:
            records.append(
                MethodologyCoverageRecord(
                    tenant_id=str(scope.tenant_id),
                    deal_id=str(scope.deal_id),
                    run_id=str(scope.run_id),
                    methodology_id=question.methodology_id,
                    methodology_version_id=question.methodology_version_id,
                    methodology_question_id=question.methodology_question_id,
                    methodology_type=MethodologyType(question.methodology_type),
                    section=question.section,
                )
            )
    persisted = repository.insert_coverage_records(
        records=records,
        run_output_object_ids=expected_output_ids,
    )
    expected_plan_ids = tuple(str(row["coverage_record_id"]) for row in plan_rows)
    if tuple(record.coverage_record_id for record in persisted) != tuple(sorted(expected_plan_ids)):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    return selection, tuple(persisted)


def _validate_v24(artifact: Mapping[str, Any]) -> None:
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict):
        _fail("TASK8_V24_CENSUS_ARTIFACT_INVALID")
    if (
        metadata.get("artifact_schema") != "full-strict-v24-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v24"
        or metadata.get("append_frozen") is not True
        or metadata.get("activation_status") != "INACTIVE"
    ):
        _fail("TASK8_V24_CENSUS_ARTIFACT_INVALID")
    section = artifact.get("task8_fdd_contract")
    if not isinstance(section, dict):
        _fail("TASK8_V24_CENSUS_ARTIFACT_INVALID")
    dashboard = section.get("dashboard_contract")
    partial = section.get("partial_exact_one_contract")
    refusal = section.get("erasure_refusal_contract")
    if (
        not isinstance(dashboard, dict)
        or dashboard.get("authority_id") != _AUTHORITY_IDS[0]
        or not isinstance(partial, dict)
        or partial.get("authority_id") != _AUTHORITY_IDS[1]
        or not isinstance(refusal, dict)
        or refusal.get("reason_code") != _REFUSAL_CODE
        or refusal.get("sqlstate") != _REFUSAL_SQLSTATE
    ):
        _fail("TASK8_V24_CENSUS_ARTIFACT_INVALID")
    authority_registry = artifact.get("sql_authority_registry")
    if not isinstance(authority_registry, dict):
        _fail("TASK8_V24_CENSUS_ARTIFACT_INVALID")
    authority_ids = {
        row.get("authority_id")
        for row in authority_registry.get("rows", [])
        if isinstance(row, dict)
    }
    if not set(_AUTHORITY_IDS[:2]).issubset(authority_ids):
        _fail("TASK8_V24_CENSUS_ARTIFACT_INVALID")


def _decode_sql_tuple(value: object, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _fail(code)
    decoded: list[str] = []
    try:
        for encoded in value:
            if not isinstance(encoded, str) or not encoded or len(encoded) % 2:
                raise ValueError("invalid hex")
            statement = bytes.fromhex(encoded).decode("utf-8")
            if statement != " ".join(statement.split()):
                raise ValueError("noncanonical SQL")
            decoded.append(statement)
    except (UnicodeError, ValueError) as exc:
        raise Task8V24CensusError(code) from exc
    return tuple(decoded)


def _sql_authority_row_hash(row: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        [
            "full-strict-v22-sql-authority-row-v1",
            {key: value for key, value in row.items() if key != "expected_sha256"},
        ]
    )


def _guard_acl_function_row_hash(row: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    return hashlib.sha256(_ACL_FUNCTION_HASH_DOMAIN + _canonical_json(payload)).hexdigest()


def _erasure_execution_row_hash(row: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        [
            "full-strict-v22-erasure-execution-row-v1",
            {key: value for key, value in row.items() if key != "expected_sha256"},
        ]
    )


def _guard_select_sql(function_row: Mapping[str, Any]) -> tuple[str, ...]:
    owner = function_row.get("owner_role")
    if owner != _GUARD_OWNER:
        _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
    direct = function_row.get("direct_read_column_tuple")
    if not isinstance(direct, list) or not direct:
        _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
    statements: list[str] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in direct:
        if (
            not isinstance(item, list)
            or len(item) != 3
            or not isinstance(item[0], str)
            or not isinstance(item[1], list)
            or not item[1]
            or any(
                not isinstance(column, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", column) is None
                for column in item[1]
            )
            or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", item[0]) is None
        ):
            _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
        relation = item[0]
        columns = tuple(item[1])
        key = (relation, columns)
        if key in seen:
            _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
        seen.add(key)
        statements.append(
            f"GRANT SELECT ({','.join(columns)}) ON TABLE public.{relation} TO {_GUARD_OWNER}"
        )
    return tuple(statements)


def build_hash_bound_authority_execution(v24: Mapping[str, Any]) -> dict[str, Any]:
    """Decode and bind the only SQL bytes the Task-8 census may install."""

    _validate_v24(v24)
    sql_registry = v24["sql_authority_registry"]
    rows = sql_registry.get("rows")
    guard_registry = sql_registry.get("guard_function_acl_registry")
    if not isinstance(rows, list) or not isinstance(guard_registry, dict):
        _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
    guard_rows = guard_registry.get("function_rows")
    if not isinstance(guard_rows, list):
        _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")

    sql_bindings: list[dict[str, Any]] = []
    for authority_id in _AUTHORITY_IDS[:2]:
        authority_matches = [
            row for row in rows if isinstance(row, dict) and row.get("authority_id") == authority_id
        ]
        guard_matches = [
            row
            for row in guard_rows
            if isinstance(row, dict) and row.get("authority_id") == authority_id
        ]
        if len(authority_matches) != 1 or not guard_matches:
            _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
        authority = authority_matches[0]
        guard_matches.sort(key=lambda row: row.get("function_ordinal", -1))
        if authority.get("expected_sha256") != _sql_authority_row_hash(authority) or any(
            guard.get("expected_sha256") != _guard_acl_function_row_hash(guard)
            for guard in guard_matches
        ):
            _fail("TASK8_V24_CENSUS_AUTHORITY_HASH_INVALID")
        body_hex = authority.get("body_sql_utf8_hex_tuple")
        _decode_sql_tuple(body_hex, code="TASK8_V24_CENSUS_AUTHORITY_SQL_INVALID")
        target_tuple = authority.get("target_tuple")
        if not isinstance(target_tuple, list) or not target_tuple:
            _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
        guard_bindings: list[dict[str, Any]] = []
        for guard in guard_matches:
            function_kind = guard.get("function_kind")
            target_triggers = guard.get("target_trigger_tuple")
            event_coordinates = guard.get("target_event_coordinate_tuple")
            if (
                function_kind not in {"HELPER", "TRIGGER_GUARD"}
                or not isinstance(target_triggers, list)
                or not isinstance(event_coordinates, list)
            ):
                _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
            if function_kind == "HELPER":
                if target_triggers or event_coordinates:
                    _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
            elif target_triggers != target_tuple or len(event_coordinates) != 3 * len(
                target_triggers
            ):
                _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
            owner_sql_hex = guard.get("owner_sql_utf8_hex")
            if not isinstance(owner_sql_hex, str):
                _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
            _decode_sql_tuple([owner_sql_hex], code="TASK8_V24_CENSUS_GUARD_ACL_INVALID")
            function_acl_hex = guard.get("acl_sql_utf8_hex_tuple")
            _decode_sql_tuple(function_acl_hex, code="TASK8_V24_CENSUS_GUARD_ACL_INVALID")
            guard_select = _guard_select_sql(guard)
            guard_bindings.append(
                {
                    "function_signature": guard.get("function_signature"),
                    "function_kind": guard.get("function_kind"),
                    "target_trigger_tuple": guard.get("target_trigger_tuple"),
                    "target_event_coordinate_tuple": guard.get("target_event_coordinate_tuple"),
                    "guard_function_acl_sha256": guard["expected_sha256"],
                    "owner_sql_utf8_hex": owner_sql_hex,
                    "function_acl_sql_utf8_hex_tuple": function_acl_hex,
                    "guard_select_sql_tuple": list(guard_select),
                    "guard_select_tuple_sha256": _canonical_sha256(
                        [
                            "task8-v24-guard-select-tuple-v1",
                            authority_id,
                            guard.get("function_signature"),
                            guard_select,
                        ]
                    ),
                }
            )
        downgrade_hex: object = []
        if authority_id == _AUTHORITY_IDS[1]:
            partial_contract = v24.get("task8_fdd_contract", {}).get("partial_exact_one_contract")
            if not isinstance(partial_contract, Mapping):
                _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
            downgrade_hex = partial_contract.get("downgrade_sql_utf8_hex_tuple")
            _decode_sql_tuple(
                downgrade_hex,
                code="TASK8_V24_CENSUS_AUTHORITY_SQL_INVALID",
            )
        sql_bindings.append(
            {
                "authority_id": authority_id,
                "authority_sha256": authority["expected_sha256"],
                "body_sql_utf8_hex_tuple": body_hex,
                "target_tuple": target_tuple,
                "body_tuple_sha256": _canonical_sha256(
                    [
                        "task8-v24-authority-body-tuple-v1",
                        authority_id,
                        body_hex,
                    ]
                ),
                "downgrade_sql_utf8_hex_tuple": downgrade_hex,
                "downgrade_tuple_sha256": _canonical_sha256(
                    [
                        "task8-v24-authority-downgrade-tuple-v1",
                        authority_id,
                        downgrade_hex,
                    ]
                ),
                "guard_function_acl_binding_tuple": guard_bindings,
            }
        )

    erasure_section = v24.get("erasure_execution_registry")
    if not isinstance(erasure_section, dict):
        _fail("TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure_rows = erasure_section.get("rows")
    if not isinstance(erasure_rows, list) or len(erasure_rows) != 1:
        _fail("TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure = erasure_rows[0]
    if (
        not isinstance(erasure, dict)
        or erasure.get("expected_sha256") != _erasure_execution_row_hash(erasure)
        or erasure_section.get("expected_sha256")
        != _canonical_sha256(["full-strict-v22-erasure-execution-registry-v1", erasure_rows])
    ):
        _fail("TASK8_V24_CENSUS_ERASURE_AUTHORITY_HASH_INVALID")
    body_hex = erasure.get("current_body_sql_utf8_hex_tuple")
    acl_hex = erasure.get("current_acl_sql_utf8_hex_tuple")
    body_sql = _decode_sql_tuple(
        body_hex,
        code="TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    acl_sql = _decode_sql_tuple(
        acl_hex,
        code="TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    if (
        len(body_sql) != 1
        or body_sql[0].count(_REFUSAL_CODE) != 1
        or body_sql[0].count("public.methodology_truth_dashboard_rows") != 1
        or tuple(
            statement
            for statement in acl_sql
            if "public.methodology_truth_dashboard_rows" in statement
        )
        != (_EXACT_DASHBOARD_ERASURE_ACL,)
    ):
        _fail("TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure_binding = {
        "execution_id": erasure.get("execution_id"),
        "authority_sha256": erasure["expected_sha256"],
        "current_body_sql_utf8_hex_tuple": body_hex,
        "body_tuple_sha256": _canonical_sha256(
            [
                "task8-v24-erasure-body-tuple-v1",
                erasure.get("execution_id"),
                body_hex,
            ]
        ),
        "current_acl_sql_utf8_hex_tuple": acl_hex,
        "acl_tuple_sha256": _canonical_sha256(
            [
                "task8-v24-erasure-acl-tuple-v1",
                erasure.get("execution_id"),
                acl_hex,
            ]
        ),
    }
    payload = {
        "schema_version": _AUTHORITY_EXECUTION_SCHEMA,
        "sql_authorities": sql_bindings,
        "erasure_authority": erasure_binding,
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(
            ["task8-v24-hash-bound-authority-execution-v1", payload]
        ),
    }


def execute_hash_bound_authority_sql(
    cursor: Any,
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute the exact decoded bundle in reviewed order and return its binding."""

    payload = {key: value for key, value in bundle.items() if key != "expected_sha256"}
    if (
        tuple(bundle)
        != (
            "schema_version",
            "sql_authorities",
            "erasure_authority",
            "expected_sha256",
        )
        or bundle.get("schema_version") != _AUTHORITY_EXECUTION_SCHEMA
        or bundle.get("expected_sha256")
        != _canonical_sha256(["task8-v24-hash-bound-authority-execution-v1", payload])
    ):
        _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
    statements: list[str] = []
    authority_binding: list[dict[str, Any]] = []
    authority_install_statements: dict[str, list[str]] = {}
    authority_downgrade_statements: dict[str, tuple[str, ...]] = {}
    sql_authorities = bundle.get("sql_authorities")
    erasure = bundle.get("erasure_authority")
    if not isinstance(sql_authorities, list) or not isinstance(erasure, dict):
        _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
    for row in sql_authorities:
        if not isinstance(row, dict):
            _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
        body = _decode_sql_tuple(
            row.get("body_sql_utf8_hex_tuple"),
            code="TASK8_V24_CENSUS_AUTHORITY_SQL_INVALID",
        )
        downgrade = (
            _decode_sql_tuple(
                row.get("downgrade_sql_utf8_hex_tuple"),
                code="TASK8_V24_CENSUS_AUTHORITY_SQL_INVALID",
            )
            if row.get("downgrade_sql_utf8_hex_tuple")
            else ()
        )
        if row.get("downgrade_tuple_sha256") != _canonical_sha256(
            [
                "task8-v24-authority-downgrade-tuple-v1",
                row.get("authority_id"),
                row.get("downgrade_sql_utf8_hex_tuple"),
            ]
        ):
            _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
        guard_bindings = row.get("guard_function_acl_binding_tuple")
        if not isinstance(guard_bindings, list) or not guard_bindings:
            _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
        guard_receipts: list[dict[str, Any]] = []
        row_statements = list(body)
        for guard in guard_bindings:
            if not isinstance(guard, dict):
                _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
            owner = _decode_sql_tuple(
                [guard.get("owner_sql_utf8_hex")],
                code="TASK8_V24_CENSUS_GUARD_ACL_INVALID",
            )
            acl = _decode_sql_tuple(
                guard.get("function_acl_sql_utf8_hex_tuple"),
                code="TASK8_V24_CENSUS_GUARD_ACL_INVALID",
            )
            guard_select = guard.get("guard_select_sql_tuple")
            if not isinstance(guard_select, list) or any(
                not isinstance(statement, str) for statement in guard_select
            ):
                _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
            if guard.get("guard_select_tuple_sha256") != _canonical_sha256(
                [
                    "task8-v24-guard-select-tuple-v1",
                    row.get("authority_id"),
                    guard.get("function_signature"),
                    tuple(guard_select),
                ]
            ):
                _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
            row_statements.extend((*owner, *acl, *guard_select))
            guard_receipts.append(
                {
                    "function_signature": guard["function_signature"],
                    "function_kind": guard["function_kind"],
                    "target_trigger_tuple": guard["target_trigger_tuple"],
                    "target_event_coordinate_tuple": guard["target_event_coordinate_tuple"],
                    "guard_select_sql_tuple": list(guard_select),
                    "guard_function_acl_sha256": guard["guard_function_acl_sha256"],
                    "guard_select_tuple_sha256": guard["guard_select_tuple_sha256"],
                }
            )
        for statement in row_statements:
            cursor.execute(statement)
            statements.append(statement)
        authority_install_statements[str(row["authority_id"])] = row_statements
        authority_downgrade_statements[str(row["authority_id"])] = downgrade
        authority_binding.append(
            {
                "authority_id": row["authority_id"],
                "authority_sha256": row["authority_sha256"],
                "body_tuple_sha256": row["body_tuple_sha256"],
                "target_tuple": row["target_tuple"],
                "downgrade_tuple_sha256": row["downgrade_tuple_sha256"],
                "downgrade_statement_count": len(downgrade),
                "guard_function_acl_binding_tuple": guard_receipts,
            }
        )
    erasure_body = _decode_sql_tuple(
        erasure.get("current_body_sql_utf8_hex_tuple"),
        code="TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    erasure_acl = _decode_sql_tuple(
        erasure.get("current_acl_sql_utf8_hex_tuple"),
        code="TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    for statement in (*erasure_body, *erasure_acl):
        cursor.execute(statement)
        statements.append(statement)
    authority_binding.append(
        {
            "authority_id": _AUTHORITY_IDS[2],
            "authority_sha256": erasure["authority_sha256"],
            "body_tuple_sha256": erasure["body_tuple_sha256"],
            "acl_tuple_sha256": erasure["acl_tuple_sha256"],
        }
    )
    partial_restore = authority_downgrade_statements.get(_AUTHORITY_IDS[1], ())
    partial_reinstall = authority_install_statements.get(_AUTHORITY_IDS[1], [])
    if not partial_restore or not partial_reinstall:
        _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
    partial_cycle_sha256 = _canonical_sha256(
        [
            "task8-v24-partial-restore-reinstall-v1",
            list(partial_restore),
            partial_reinstall,
        ]
    )
    return {
        "schema_version": "task8-v24-authority-execution-binding-receipt-v1",
        "bundle_sha256": bundle["expected_sha256"],
        "authority_binding_tuple": authority_binding,
        "partial_restore_reinstall_statement_tuple_sha256": partial_cycle_sha256,
        "executed_statement_tuple_sha256": _canonical_sha256(
            ["task8-v24-executed-authority-sql-v1", statements]
        ),
    }


def _artifact_row(
    artifact: Mapping[str, Any],
    registry: str,
    *,
    field: str,
    value: str,
) -> Mapping[str, Any]:
    section = artifact.get(registry)
    rows = section.get("rows") if isinstance(section, Mapping) else None
    matches = [row for row in rows or () if isinstance(row, Mapping) and row.get(field) == value]
    if len(matches) != 1:
        _fail("TASK8_V24_CENSUS_ARTIFACT_ROW_INVALID")
    return matches[0]


def _column_definition_tuple(row: Mapping[str, Any]) -> tuple[str, ...]:
    groups = (
        row.get("primary_key", ()),
        row.get("scope_columns", ()),
        row.get("immutable_fields", ()),
        row.get("column_tuple", ()),
    )
    result: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if (
                not isinstance(item, list)
                or len(item) != 3
                or not isinstance(item[0], int)
                or not isinstance(item[1], str)
                or not isinstance(item[2], str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", item[1]) is None
                or item[1] in seen
            ):
                _fail("TASK8_V24_CENSUS_FUTURE_DDL_INVALID")
            seen.add(item[1])
            result.append((len(result), item[1], item[2]))
    if not result:
        _fail("TASK8_V24_CENSUS_FUTURE_DDL_INVALID")
    return tuple(f"{name} {definition}" for _ordinal, name, definition in result)


def _table_constraint_tuple(row: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for item in row.get("check_tuple", ()):
        if not isinstance(item, list) or len(item) != 2:
            _fail("TASK8_V24_CENSUS_FUTURE_DDL_INVALID")
        result.append(f"CONSTRAINT {item[0]} CHECK ({item[1]})")
    unique_rows = row.get("unique_tuple", ())
    for item in unique_rows:
        if not isinstance(item, list) or len(item) != 3 or not item[1]:
            _fail("TASK8_V24_CENSUS_FUTURE_DDL_INVALID")
        columns = ",".join(item[1])
        authority_kind = "PRIMARY KEY" if str(item[0]).startswith("pk_") else "UNIQUE"
        result.append(f"CONSTRAINT {item[0]} {authority_kind} ({columns})")
    references = row.get("reference_fields", row.get("foreign_key_tuple", ()))
    for item in references:
        if not isinstance(item, list) or len(item) != 8:
            _fail("TASK8_V24_CENSUS_FUTURE_DDL_INVALID")
        name, columns, parent, _authority, parent_columns, on_update, on_delete, _index = item
        result.append(
            f"CONSTRAINT {name} FOREIGN KEY ({','.join(columns)}) "
            f"REFERENCES public.{parent} ({','.join(parent_columns)}) "
            f"ON UPDATE {on_update} ON DELETE {on_delete}"
        )
    return tuple(result)


def build_future_table_scaffold(v24: Mapping[str, Any], application_role: str) -> dict[str, Any]:
    """Derive the two Task-8 future tables from the reviewed registry bytes."""

    _validate_v24(v24)
    role = _quoted_identifier(application_role)
    native = _artifact_row(
        v24,
        "future_native_authority_registry",
        field="planned_table",
        value="methodology_truth_dashboard_rows",
    )
    child = _artifact_row(
        v24,
        "future_child_authority_registry",
        field="table_name",
        value="methodology_truth_dashboard_root_links",
    )
    statements: list[str] = []
    for row, field in ((native, "planned_table"), (child, "table_name")):
        relation = str(row[field])
        definitions = (*_column_definition_tuple(row), *_table_constraint_tuple(row))
        statements.append(f"CREATE TABLE public.{relation} ({','.join(definitions)})")
        index_rows = list(row.get("index_tuple", ()))
        for reference in row.get("reference_fields", row.get("foreign_key_tuple", ())):
            if not isinstance(reference, list) or len(reference) != 8:
                _fail("TASK8_V24_CENSUS_FUTURE_DDL_INVALID")
            index_rows.append([reference[7], reference[1], None, False])
        seen_indexes: set[str] = set()
        for index in index_rows:
            if not isinstance(index, list) or len(index) != 4:
                _fail("TASK8_V24_CENSUS_FUTURE_DDL_INVALID")
            name, columns, predicate, unique = index
            if name in seen_indexes:
                continue
            seen_indexes.add(name)
            unique_sql = "UNIQUE " if unique else ""
            predicate_sql = f" WHERE {predicate}" if predicate else ""
            statements.append(
                f"CREATE {unique_sql}INDEX {name} ON public.{relation} "
                f"({','.join(columns)}){predicate_sql}"
            )
        rls = row.get("rls_tuple")
        if not isinstance(rls, list) or len(rls) != 1:
            _fail("TASK8_V24_CENSUS_FUTURE_DDL_INVALID")
        for statement in rls[0]:
            if not isinstance(statement, str):
                _fail("TASK8_V24_CENSUS_FUTURE_DDL_INVALID")
            for part in statement.replace("<configured_app_role>", role).split(";"):
                if part.strip():
                    statements.append(part.strip())
    payload = {
        "schema_version": "task8-v24-future-table-scaffold-v1",
        "relation_tuple": [
            "methodology_truth_dashboard_rows",
            "methodology_truth_dashboard_root_links",
        ],
        "statement_tuple": statements,
        "installed_tenant_rls_table_count": 69,
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(["task8-v24-future-table-scaffold-v1", payload]),
    }


def build_application_behavior_grants(
    v24: Mapping[str, Any],
    application_role: str,
) -> dict[str, Any]:
    """Bind the reviewed 0038 app grants needed by the behavior fixture."""

    _validate_v24(v24)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", application_role) is None:
        _fail("TASK8_V24_CENSUS_ROLE_INVALID")
    relations = ("run_output_objects", "run_step_outputs")
    statements: list[str] = []
    rows = v24.get("current_ddl_registry", {}).get("rows", ())
    for relation in relations:
        matches = [
            row for row in rows if isinstance(row, Mapping) and row.get("table_name") == relation
        ]
        if len(matches) != 1:
            _fail("TASK8_V24_CENSUS_APPLICATION_GRANT_INVALID")
        rls_tuple = matches[0].get("rls_tuple")
        if not isinstance(rls_tuple, list):
            _fail("TASK8_V24_CENSUS_APPLICATION_GRANT_INVALID")
        expected = f"GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE {relation} TO <configured_app_role>"
        if rls_tuple.count(expected) != 1:
            _fail("TASK8_V24_CENSUS_APPLICATION_GRANT_INVALID")
        statements.append(expected.replace("<configured_app_role>", application_role))
    payload = {
        "schema_version": "task8-v24-application-behavior-grants-v1",
        "relation_tuple": list(relations),
        "statement_tuple": statements,
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(["task8-v24-application-behavior-grants-v1", payload]),
    }


def build_zero_mutation_projection_manifest(v24: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the rejection projection to CURRENT48 plus Task-8/control rows."""

    _validate_v24(v24)
    erasure = v24["erasure_execution_registry"]["rows"][0]
    current = list(erasure["current_relation_tuple"])
    payload = {
        "schema_version": "task8-v24-zero-mutation-projection-v1",
        "timestamp_policy": "compare-exact-redact-from-digest-v1",
        "current_relation_tuple": current,
        "indirect_relation_tuple": [
            "calc_sanads",
            "human_gate_actions",
            "run_steps",
        ],
        "task8_relation_tuple": [
            "methodology_truth_dashboard_rows",
            "methodology_truth_dashboard_root_links",
        ],
        "control_relation_tuple": sorted(
            set(current)
            | {
                "audit_events",
                "deals",
                "erasure_requests",
                "methodology_truth_dashboard_rows",
                "methodology_truth_dashboard_root_links",
            }
        ),
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(["task8-v24-zero-mutation-projection-v1", payload]),
    }


def build_advisory_lock_binding(v24: Mapping[str, Any]) -> dict[str, Any]:
    """Prove dashboard completion and erasure use one exact advisory key."""

    _validate_v24(v24)
    dashboard = _artifact_row(
        v24,
        "sql_authority_registry",
        field="authority_id",
        value=_AUTHORITY_IDS[0],
    )
    erasure = v24["erasure_execution_registry"]["rows"][0]
    dashboard_sql = " ".join(
        _decode_sql_tuple(
            dashboard["body_sql_utf8_hex_tuple"],
            code="TASK8_V24_CENSUS_AUTHORITY_SQL_INVALID",
        )
    )
    erasure_sql = " ".join(
        _decode_sql_tuple(
            erasure["current_body_sql_utf8_hex_tuple"],
            code="TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID",
        )
    )
    pattern = re.compile(r"pg_catalog\.hashtextextended\(\s*'([^']+)'\|\|[^,]+,(\d+)\s*\)")
    dashboard_matches = pattern.findall(dashboard_sql)
    erasure_matches = pattern.findall(erasure_sql)
    if len(dashboard_matches) != 1 or len(erasure_matches) != 1:
        _fail("TASK8_V24_CENSUS_ADVISORY_LOCK_INVALID")
    if dashboard_matches[0] != erasure_matches[0]:
        _fail("TASK8_V24_CENSUS_ADVISORY_LOCK_INVALID")
    prefix, seed = dashboard_matches[0]
    payload = {
        "schema_version": "task8-v24-shared-advisory-lock-binding-v1",
        "dashboard_authority_id": _AUTHORITY_IDS[0],
        "erasure_execution_id": erasure["execution_id"],
        "prefix": prefix,
        "seed": int(seed),
        "dashboard_occurrence_count": len(dashboard_matches),
        "erasure_occurrence_count": len(erasure_matches),
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(
            ["task8-v24-shared-advisory-lock-binding-v1", payload]
        ),
    }


def _case(
    case_id: str,
    authority_id: str,
    case_kind: str,
    semantic_case: str,
    execution_role: str,
    expected: str,
    *,
    expected_sqlstate: str | None = None,
    expected_reason_code: str | None = None,
    exercised_target_event_tuple: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    if expected_reason_code is None and authority_id == _AUTHORITY_IDS[0] and expected != "ACCEPT":
        expected_reason_code = _DASHBOARD_REJECTION_CODE
    if (
        expected_reason_code is None
        and authority_id == _AUTHORITY_IDS[1]
        and semantic_case in {"ZERO_DASHBOARD_NATIVE_ROOTS", "DIRECT_SQL_ROOT_MISMATCH"}
    ):
        expected_reason_code = _PARTIAL_EXACT_ONE_CODE
    return {
        "case_id": case_id,
        "authority_id": authority_id,
        "case_kind": case_kind,
        "semantic_case": semantic_case,
        "execution_role": execution_role,
        "expected": expected,
        "expected_sqlstate": expected_sqlstate,
        "expected_reason_code": expected_reason_code,
        "exercised_target_event_tuple": [
            list(coordinate) for coordinate in exercised_target_event_tuple
        ],
    }


def _global_dashboard_census_cases(dashboard_id: str) -> list[dict[str, Any]]:
    """Return the exact global-census rejection catalog without reading contract bytes."""

    return [
        _case(
            "dashboard-global-missing-row",
            dashboard_id,
            "ILLEGAL",
            "GLOBAL_DASHBOARD_ROW_MISSING",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "INSERT"),),
        ),
        _case(
            "dashboard-global-source-count-99",
            dashboard_id,
            "ILLEGAL",
            "GLOBAL_COVERAGE_SOURCE_COUNT_99",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "INSERT"),),
        ),
        _case(
            "dashboard-global-extra-noncoverage",
            dashboard_id,
            "ILLEGAL",
            "GLOBAL_EXTRA_NONCOVERAGE_ROW",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23503",
            expected_reason_code="",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "INSERT"),),
        ),
        _case(
            "dashboard-global-duplicate-coverage",
            dashboard_id,
            "ILLEGAL",
            "GLOBAL_DUPLICATE_COVERAGE_ROW",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23505",
            expected_reason_code="",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "INSERT"),),
        ),
    ]


@dataclass(frozen=True)
class _GlobalDashboardCensusPlan:
    semantic_case: str
    seed_coverage_row_count: int
    seed_financial_coverage_row_count: int
    omit_focus_output: bool
    extra_row_disposition: str | None


def _global_dashboard_census_plan(
    semantic_case: str,
    *,
    coverage_row_count: int,
    financial_coverage_row_count: int,
) -> _GlobalDashboardCensusPlan:
    """Derive the negative fixture shape independently of PostgreSQL and artifact loading."""

    if coverage_row_count != 100 or financial_coverage_row_count != 79:
        _fail("TASK8_V24_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    if semantic_case == "GLOBAL_DASHBOARD_ROW_MISSING":
        return _GlobalDashboardCensusPlan(semantic_case, 100, 79, True, None)
    if semantic_case == "GLOBAL_COVERAGE_SOURCE_COUNT_99":
        return _GlobalDashboardCensusPlan(semantic_case, 99, 78, False, None)
    if semantic_case == "GLOBAL_EXTRA_NONCOVERAGE_ROW":
        return _GlobalDashboardCensusPlan(semantic_case, 100, 79, False, "NONCOVERAGE")
    if semantic_case == "GLOBAL_DUPLICATE_COVERAGE_ROW":
        return _GlobalDashboardCensusPlan(semantic_case, 100, 79, False, "DUPLICATE")
    _fail("TASK8_V24_CENSUS_GLOBAL_CASE_INVALID")


def build_census_case_manifest(v24: Mapping[str, Any]) -> dict[str, Any]:
    _validate_v24(v24)
    dashboard_id, partial_id, erasure_id = _AUTHORITY_IDS
    changed_authority_targets = {
        authority_id: _artifact_row(
            v24,
            "sql_authority_registry",
            field="authority_id",
            value=authority_id,
        )["target_tuple"]
        for authority_id in (dashboard_id, partial_id)
    }
    partial_focus_targets = [
        target
        for target in changed_authority_targets[partial_id]
        if target[0] in {"run_output_objects", "methodology_truth_dashboard_rows"}
    ]
    if partial_focus_targets != [
        ["run_output_objects", "ctrg_task7_run_output_objects_exact_one"],
        [
            "methodology_truth_dashboard_rows",
            "ctrg_task8_partial_native_root_dashboard_rows",
        ],
    ]:
        _fail("TASK8_V24_CENSUS_PARTIAL_TARGET_INVALID")

    def target_event_tuple(authority_id: str) -> list[list[str]]:
        return sorted(
            [
                [target, event]
                for target, _trigger in changed_authority_targets[authority_id]
                for event in ("INSERT", "UPDATE", "DELETE")
            ]
        )

    def trigger_guard(authority_id: str) -> Mapping[str, Any]:
        rows = [
            row
            for row in v24["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
            if row.get("authority_id") == authority_id
            and row.get("function_kind") == "TRIGGER_GUARD"
        ]
        if len(rows) != 1:
            _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
        return rows[0]

    dashboard_guard = trigger_guard(dashboard_id)
    partial_guard = trigger_guard(partial_id)
    dashboard_contract = v24["task8_fdd_contract"]["dashboard_contract"]
    coverage_row_count = dashboard_contract.get("coverage_row_count")
    if coverage_row_count != 100:
        _fail("TASK8_V24_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    financial_coverage_row_count = dashboard_contract.get("financial_row_count")
    if financial_coverage_row_count != 79:
        _fail("TASK8_V24_CENSUS_FINANCIAL_COVERAGE_ROW_COUNT_INVALID")
    all_guard_rows = v24["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
    changed_guard_rows = [
        row for row in all_guard_rows if row.get("authority_id") in _AUTHORITY_IDS[:2]
    ]
    installed_guard_rows = [
        row
        for row in all_guard_rows
        if row.get("install_scope") == "CURRENT_0038"
        or row.get("authority_id") in _AUTHORITY_IDS[:2]
    ]
    guard_owner_parent_select_acl_tuple = sorted(
        [
            [relation, sorted(columns)]
            for relation, columns in {
                relation: {
                    column
                    for row in installed_guard_rows
                    for row_relation, row_columns, _source in row["direct_read_column_tuple"]
                    if row_relation == relation
                    for column in row_columns
                }
                for relation in {
                    relation
                    for row in installed_guard_rows
                    for relation, _columns, _source in row["direct_read_column_tuple"]
                }
            }.items()
        ]
    )
    trigger_function_binding_tuple = sorted(
        [
            [authority_id, target, trigger, guard["function_signature"]]
            for authority_id, guard in (
                (dashboard_id, dashboard_guard),
                (partial_id, partial_guard),
            )
            for target, trigger in guard["target_trigger_tuple"]
        ]
    )
    dashboard_legal = [
        _case(
            "dashboard-gap-all-not-found",
            dashboard_id,
            "LEGAL",
            "ALL_NOT_FOUND_GAP",
            "application",
            "ACCEPT",
            exercised_target_event_tuple=(
                ("methodology_truth_dashboard_rows", "INSERT"),
                ("methodology_truth_dashboard_root_links", "INSERT"),
            ),
        ),
        _case(
            "dashboard-mixed-answered",
            dashboard_id,
            "LEGAL",
            "MIXED_FOUND_NOT_FOUND_ANSWERED",
            "application",
            "ACCEPT",
        ),
        _case(
            "dashboard-mixed-red-flag",
            dashboard_id,
            "LEGAL",
            "MIXED_FOUND_NOT_FOUND_WITH_DEFECT_RED_FLAG",
            "application",
            "ACCEPT",
        ),
        _case(
            "dashboard-all-found-answered",
            dashboard_id,
            "LEGAL",
            "ALL_FOUND_ANSWERED",
            "application",
            "ACCEPT",
        ),
    ]
    dashboard_illegal = [
        _case(
            "dashboard-required-formula-missing",
            dashboard_id,
            "ILLEGAL",
            "REQUIRED_FORMULA_MISSING",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        _case(
            "dashboard-gap-with-found-result",
            dashboard_id,
            "ILLEGAL",
            "GAP_WITH_FOUND_RESULT",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        _case(
            "dashboard-answered-without-evidence",
            dashboard_id,
            "ILLEGAL",
            "ANSWERED_WITHOUT_EVIDENCE_SANAD",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        _case(
            "dashboard-red-flag-without-defect",
            dashboard_id,
            "ILLEGAL",
            "RED_FLAG_WITHOUT_ADVERSE_ROOT",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        *_global_dashboard_census_cases(dashboard_id),
    ]
    dashboard_direct = [
        _case(
            "dashboard-direct-insert-bypass",
            dashboard_id,
            "DIRECT_SQL",
            "DIRECT_INSERT_TRIGGER_BYPASS",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        _case(
            "dashboard-direct-update",
            dashboard_id,
            "DIRECT_SQL",
            "APPEND_ONLY_ROW_UPDATE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "UPDATE"),),
        ),
        _case(
            "dashboard-direct-delete",
            dashboard_id,
            "DIRECT_SQL",
            "APPEND_ONLY_ROW_DELETE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "DELETE"),),
        ),
        _case(
            "dashboard-root-link-direct-update",
            dashboard_id,
            "DIRECT_SQL",
            "APPEND_ONLY_ROOT_LINK_UPDATE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_root_links", "UPDATE"),),
        ),
        _case(
            "dashboard-root-link-direct-delete",
            dashboard_id,
            "DIRECT_SQL",
            "APPEND_ONLY_ROOT_LINK_DELETE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_root_links", "DELETE"),),
        ),
    ]
    partial_legal = [
        _case(
            "partial-root-exactly-one",
            partial_id,
            "LEGAL",
            "EXACTLY_ONE_DASHBOARD_NATIVE_ROOT",
            "application",
            "ACCEPT",
            exercised_target_event_tuple=(
                ("run_output_objects", "INSERT"),
                ("methodology_truth_dashboard_rows", "INSERT"),
            ),
        )
    ]
    partial_illegal = [
        _case(
            "partial-root-missing",
            partial_id,
            "ILLEGAL",
            "ZERO_DASHBOARD_NATIVE_ROOTS",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("run_output_objects", "INSERT"),),
        ),
        _case(
            "partial-root-duplicate",
            partial_id,
            "ILLEGAL",
            "MULTIPLE_DASHBOARD_NATIVE_ROOTS",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23505",
            exercised_target_event_tuple=(
                ("run_output_objects", "INSERT"),
                ("methodology_truth_dashboard_rows", "INSERT"),
            ),
        ),
    ]
    partial_direct = [
        _case(
            "partial-root-direct-sql",
            partial_id,
            "DIRECT_SQL",
            "DIRECT_SQL_ROOT_MISMATCH",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(
                ("run_output_objects", "INSERT"),
                ("methodology_truth_dashboard_rows", "INSERT"),
            ),
        )
    ]
    erasure_legal = [
        _case(
            "erasure-current48-without-dashboard",
            erasure_id,
            "LEGAL",
            "CURRENT48_NO_DASHBOARD_ROWS",
            "erasure_executor",
            "ACCEPT",
        )
    ]
    erasure_illegal = [
        _case(
            "erasure-dashboard-refusal",
            erasure_id,
            "ILLEGAL",
            "DASHBOARD_ROWS_REQUIRE_FUTURE38",
            "erasure_executor",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate=_REFUSAL_SQLSTATE,
            expected_reason_code=_REFUSAL_CODE,
        )
    ]
    erasure_direct = [
        _case(
            "erasure-application-execute-denied",
            erasure_id,
            "DIRECT_SQL",
            "APPLICATION_RUNNER_EXECUTE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="42501",
        ),
        _case(
            "erasure-executor-direct-refusal",
            erasure_id,
            "DIRECT_SQL",
            "EXECUTOR_CANNOT_BYPASS_DASHBOARD_REFUSAL",
            "erasure_executor",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate=_REFUSAL_SQLSTATE,
            expected_reason_code=_REFUSAL_CODE,
        ),
    ]
    return {
        "schema_version": _SCHEMA,
        "setup_role": "admin_setup_only",
        "behavior_role_contract": {
            "guard_default": "application",
            "erasure_function": "erasure_executor",
            "admin_behavior_case_count": 0,
            "catalog_observer": "application",
        },
        "dashboard_coverage_row_count": coverage_row_count,
        "dashboard_commercial_coverage_row_count": (
            coverage_row_count - financial_coverage_row_count
        ),
        "dashboard_financial_coverage_row_count": financial_coverage_row_count,
        "authorities": [
            {
                "authority_ordinal": 1,
                "authority_id": dashboard_id,
                "target_event_tuple": target_event_tuple(dashboard_id),
                "legal_case_tuple": dashboard_legal,
                "illegal_case_tuple": dashboard_illegal,
                "direct_sql_case_tuple": dashboard_direct,
            },
            {
                "authority_ordinal": 2,
                "authority_id": partial_id,
                # The successor authority replaces the function reached by all
                # 18 existing 0038 triggers and adds the dashboard trigger.  Its
                # executable surface is therefore the complete 19 x I/U/D set;
                # the two focus targets below are only the semantic delta cases.
                "target_event_tuple": target_event_tuple(partial_id),
                "legal_case_tuple": partial_legal,
                "illegal_case_tuple": partial_illegal,
                "direct_sql_case_tuple": partial_direct,
            },
            {
                "authority_ordinal": 3,
                "authority_id": erasure_id,
                "target_event_tuple": [
                    [
                        "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)",
                        "EXECUTE",
                    ]
                ],
                "legal_case_tuple": erasure_legal,
                "illegal_case_tuple": erasure_illegal,
                "direct_sql_case_tuple": erasure_direct,
            },
        ],
        "catalog_assertions": {
            "installed_tenant_rls_table_count": 69,
            "dashboard_enable_rls": True,
            "dashboard_force_rls": True,
            "guard_owner_superuser": False,
            "guard_owner_bypassrls": False,
            "guard_owner_role_membership_count": 0,
            "erasure_owner_superuser": False,
            "erasure_owner_bypassrls": False,
            "erasure_owner_role_membership_count": 0,
            "application_superuser": False,
            "application_bypassrls": False,
            "erasure_executor_superuser": False,
            "erasure_executor_bypassrls": False,
            "erasure_executor_role_membership_count": 0,
            "guard_owner_dashboard_select_columns": sorted(
                {
                    column
                    for row in v24["sql_authority_registry"]["guard_function_acl_registry"][
                        "function_rows"
                    ]
                    if row.get("authority_id") in _AUTHORITY_IDS[:2]
                    for relation, columns, _source in row["direct_read_column_tuple"]
                    if relation == "methodology_truth_dashboard_rows"
                    for column in columns
                }
            ),
            "guard_owner_dashboard_insert": False,
            "guard_owner_dashboard_update": False,
            "guard_owner_dashboard_delete": False,
            "guard_owner_dashboard_write_privilege_count": 0,
            "erasure_owner_dashboard_select_columns": ["tenant_id", "deal_id"],
            "erasure_owner_dashboard_write_privilege_count": 0,
            "erasure_executor_dashboard_table_privilege_count": 0,
            "application_runner_execute_count": 0,
            "erasure_executor_runner_execute_count": 1,
            "erasure_executor_schema_usage": True,
            "erasure_executor_schema_create": False,
            "dashboard_policy_count": 2,
            "dashboard_policy_using_with_check_equal_count": 2,
            "dashboard_guard_function_owner_count": 3,
            "dashboard_guard_security_definer_count": sum(
                row["security_mode"] == "DEFINER" for row in changed_guard_rows
            ),
            "application_guard_function_execute_count": 0,
            "guard_owner_parent_select_acl_tuple": guard_owner_parent_select_acl_tuple,
            "guard_owner_parent_write_privilege_count": 0,
            "dashboard_trigger_event_tuple": target_event_tuple(dashboard_id),
            "partial_trigger_event_tuple": target_event_tuple(partial_id),
            "dashboard_trigger_event_coordinate_tuple": dashboard_guard[
                "target_event_coordinate_tuple"
            ],
            "partial_trigger_event_coordinate_tuple": partial_guard[
                "target_event_coordinate_tuple"
            ],
            "trigger_function_binding_tuple": trigger_function_binding_tuple,
            "partial_target_application_dml_reachability_tuple": sorted(
                [
                    [
                        target,
                        trigger,
                        True,
                        True,
                        True,
                        partial_guard["function_signature"],
                    ]
                    for target, trigger in partial_guard["target_trigger_tuple"]
                ]
            ),
            "partial_target_application_dml_reachability_count": 19,
            "trigger_catalog_application_role_match": True,
            "erasure_runner_owner_valid": True,
            "erasure_runner_security_definer": True,
            "current_erasure_relation_count": 48,
            "future_erasure_relation_count": 38,
            "complete_erasure_relation_count": 86,
        },
        "race_cases": [
            {
                "case_id": "hold-erasure-lock-then-dashboard-write",
                "first_actor": "erasure_executor",
                "second_actor": "application",
                "expected": "SECOND_BLOCKS_THEN_PARENT_REVALIDATION_REJECTS",
            },
            {
                "case_id": "hold-dashboard-lock-then-erasure",
                "first_actor": "application",
                "second_actor": "erasure_executor",
                "expected": "SECOND_BLOCKS_THEN_DASHBOARD_REFUSAL",
            },
        ],
    }


def validate_census_case_manifest(
    manifest: Mapping[str, Any],
    v24: Mapping[str, Any],
) -> None:
    expected = build_census_case_manifest(v24)
    if dict(manifest) != expected:
        _fail("TASK8_V24_CENSUS_MANIFEST_INVALID")
    cases = [
        case
        for authority in manifest["authorities"]
        for family in ("legal_case_tuple", "illegal_case_tuple", "direct_sql_case_tuple")
        for case in authority[family]
    ]
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        _fail("TASK8_V24_CENSUS_CASE_ID_DUPLICATE")
    if any(case["execution_role"] == "admin" for case in cases):
        _fail("TASK8_V24_CENSUS_ADMIN_BEHAVIOR_FORBIDDEN")


def census_case_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the closed 25-case behavior matrix in reviewed order."""

    rows = _case_rows(manifest)
    if len(rows) != 25:
        _fail("TASK8_V24_CENSUS_MANIFEST_INVALID")
    return rows


def _decoded_sql_hash(value: object, *, domain: str) -> str:
    statements = _decode_sql_tuple(value, code="TASK8_V24_CENSUS_AUTHORITY_SQL_INVALID")
    return _canonical_sha256([domain, list(statements)])


def build_authority_restoration_plan(v24: Mapping[str, Any]) -> dict[str, Any]:
    """Bind exact partial and erasure U->D->U bytes and app observations."""

    bundle = build_hash_bound_authority_execution(v24)
    partial = next(
        row for row in bundle["sql_authorities"] if row["authority_id"] == _AUTHORITY_IDS[1]
    )
    erasure = bundle["erasure_authority"]
    erasure_contract = v24["task8_fdd_contract"]["erasure_refusal_contract"]
    partial_up = _canonical_sha256(
        [
            "task8-v24-partial-authority-up-catalog-v1",
            partial["authority_sha256"],
            partial["body_tuple_sha256"],
            partial["guard_function_acl_binding_tuple"],
        ]
    )
    partial_down = _decoded_sql_hash(
        partial["downgrade_sql_utf8_hex_tuple"],
        domain="task8-v24-partial-authority-down-catalog-v1",
    )
    erasure_up = _canonical_sha256(
        [
            "task8-v24-erasure-authority-up-catalog-v1",
            erasure["authority_sha256"],
            erasure["body_tuple_sha256"],
            erasure["acl_tuple_sha256"],
        ]
    )
    erasure_down = _decoded_sql_hash(
        erasure_contract["downgrade_sql_utf8_hex_tuple"],
        domain="task8-v24-erasure-authority-down-catalog-v1",
    )
    authorities = [
        {
            "authority_id": _AUTHORITY_IDS[1],
            "phase_tuple": ["UP", "DOWN", "UP"],
            "ddl_executor": "admin_setup_only",
            "observer_tuple": ["application", "application", "application"],
            "initial_catalog_sha256": partial_up,
            "downgrade_catalog_sha256": partial_down,
            "reinstalled_catalog_sha256": partial_up,
        },
        {
            "authority_id": _AUTHORITY_IDS[2],
            "phase_tuple": ["UP", "DOWN", "UP"],
            "ddl_executor": "admin_setup_only",
            "observer_tuple": ["application", "application", "application"],
            "initial_catalog_sha256": erasure_up,
            "downgrade_catalog_sha256": erasure_down,
            "reinstalled_catalog_sha256": erasure_up,
        },
    ]
    payload = {
        "schema_version": "task8-v24-authority-restoration-plan-v1",
        "ddl_executor": "admin_setup_only",
        "catalog_observer": "application",
        "authority_tuple": authorities,
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


@dataclass(frozen=True)
class _DisposableUrls:
    database_name: str
    admin: str
    application: str
    erasure: str
    application_role: str
    erasure_role: str


def _role_from_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if not parsed.username:
        _fail("TASK8_V24_CENSUS_DATABASE_URL_INVALID")
    return unquote(parsed.username)


def _replace_database(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        _fail("TASK8_V24_CENSUS_DATABASE_URL_INVALID")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{quote(database_name, safe='')}",
            parsed.query,
            parsed.fragment,
        )
    )


def _disposable_urls(
    admin_database_url: str,
    application_database_url: str,
    erasure_database_url: str,
) -> _DisposableUrls:
    name = f"idis_task8_census_{uuid.uuid4().hex[:16]}"
    return _DisposableUrls(
        database_name=name,
        admin=_replace_database(admin_database_url, name),
        application=_replace_database(application_database_url, name),
        erasure=_replace_database(erasure_database_url, name),
        application_role=_role_from_url(application_database_url),
        erasure_role=_role_from_url(erasure_database_url),
    )


def _connect(database_url: str) -> Any:
    try:
        import psycopg2
    except ImportError as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_PSYCOPG2_UNAVAILABLE") from exc
    return psycopg2.connect(database_url)


def _attest_connection_role_posture(cursor: Any, expected_role: str) -> str:
    """Prove one evidence connection is the expected unprivileged role."""

    cursor.execute(
        """
        SELECT current_user,session_user,role.rolsuper,role.rolbypassrls
          FROM pg_catalog.pg_roles AS role
         WHERE role.rolname=current_user
        """
    )
    row = cursor.fetchone()
    if row is None:
        _fail("TASK8_V24_CENSUS_ROLE_POSTURE_INVALID")
    current_role, session_role, is_superuser, bypasses_rls = row
    if (
        str(current_role) != expected_role
        or str(session_role) != expected_role
        or bool(is_superuser)
        or bool(bypasses_rls)
    ):
        _fail("TASK8_V24_CENSUS_ROLE_POSTURE_INVALID")
    return str(current_role)


def _execute_statements(connection: Any, statements: list[str]) -> None:
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def _quoted_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value):
        _fail("TASK8_V24_CENSUS_ROLE_INVALID")
    return f'"{value}"'


def _create_disposable_database(admin_database_url: str, database_name: str) -> None:
    connection = _connect(admin_database_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE {_quoted_identifier(database_name)} TEMPLATE template0"
            )
    finally:
        connection.close()


def _drop_disposable_database(admin_database_url: str, database_name: str) -> None:
    connection = _connect(admin_database_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pg_terminate_backend(pid)
                  FROM pg_stat_activity
                 WHERE datname = %s
                   AND pid <> pg_backend_pid()
                """,
                (database_name,),
            )
            cursor.execute(f"DROP DATABASE IF EXISTS {_quoted_identifier(database_name)}")
    finally:
        connection.close()


class _SqlAlchemyCursor:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, statement: str) -> None:
        self._connection.exec_driver_sql(statement.replace("%", "%%"))


def _run_migrations_through_0038(engine: Any) -> None:
    """Drive the project's real Alembic environment on the disposable engine."""

    try:
        from alembic import command
        from alembic.config import Config

        import idis.persistence.migrations as migrations_package
    except ImportError as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_MIGRATION_RUNNER_UNAVAILABLE") from exc
    package_file = getattr(migrations_package, "__file__", None)
    if not isinstance(package_file, str):
        _fail("TASK8_V24_CENSUS_MIGRATION_RUNNER_UNAVAILABLE")
    config = Config()
    config.set_main_option("script_location", str(Path(package_file).resolve().parent))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0038")


def _setup_disposable_schema(
    admin_database_url: str,
    *,
    application_role: str,
    erasure_role: str,
    v24: Mapping[str, Any],
    authority_execution: Mapping[str, Any],
) -> dict[str, Any]:
    """Install the real v21 source schema plus exact reviewed v24 authority bytes."""

    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_SQLALCHEMY_UNAVAILABLE") from exc

    future = build_future_table_scaffold(v24, application_role)
    application_grants = build_application_behavior_grants(v24, application_role)
    engine = create_engine(admin_database_url)
    try:
        # The exact v24 guards reference the real 0038 parent authorities.  An
        # empty/toy schema cannot prove their PostgreSQL or RLS semantics.
        _run_migrations_through_0038(engine)
        with engine.begin() as connection:
            for statement in application_grants["statement_tuple"]:
                connection.exec_driver_sql(statement)
            for statement in future["statement_tuple"]:
                connection.exec_driver_sql(statement.replace("%", "%%"))
            # 0038 installs the v21 runner under this frozen signature.  The
            # v24 body is CREATE FUNCTION by contract, so remove exactly that
            # predecessor before executing the reviewed replacement bytes.
            connection.exec_driver_sql(
                "DROP FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
            )
            binding = execute_hash_bound_authority_sql(
                _SqlAlchemyCursor(connection),
                authority_execution,
            )
    finally:
        engine.dispose()
    if binding["bundle_sha256"] != authority_execution["expected_sha256"]:
        _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
    return binding


def _set_tenant(cursor: Any, tenant_id: uuid.UUID) -> None:
    cursor.execute(
        "SELECT pg_catalog.set_config('idis.tenant_id', %s, true)",
        (str(tenant_id),),
    )


@dataclass(frozen=True)
class _CaseScope:
    tenant_id: uuid.UUID
    deal_id: uuid.UUID
    run_id: uuid.UUID
    lease_owner_id: uuid.UUID
    parent_step_id: uuid.UUID
    dashboard_step_id: uuid.UUID


@dataclass(frozen=True)
class _DashboardGraph:
    scope: _CaseScope
    coverage_record_id: str
    dashboard_row_id: uuid.UUID
    dashboard_output_object_id: uuid.UUID
    root_links: tuple[tuple[str, int, uuid.UUID, str, uuid.UUID], ...]
    root_projection_sha256: str
    row_payload_sha256: str
    native_identity_sha256: str
    native_output_sha256: str
    row_disposition: str
    dashboard_output_ordinal: int


def _insert_methodology_question_and_coverage(
    cursor: Any,
    *,
    scope: _CaseScope,
    question_seed: Mapping[str, Any],
    coverage_record_id: str,
    selection_id: uuid.UUID,
) -> None:
    question_payload = question_seed.get("question_payload")
    semantic_preimage = question_seed.get("semantic_hash_preimage")
    if (
        not isinstance(question_payload, Mapping)
        or not isinstance(semantic_preimage, Mapping)
        or question_seed.get("tenant_id") != str(scope.tenant_id)
        or question_seed.get("question_payload_hash") != _canonical_sha256(question_payload)
        or question_seed.get("semantic_row_hash") != _canonical_sha256(semantic_preimage)
    ):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    parameters = {
        "tenant_id": scope.tenant_id,
        "deal_id": scope.deal_id,
        "run_id": scope.run_id,
        "coverage_record_id": coverage_record_id,
        "selection_id": selection_id,
        "methodology_question_record_id": uuid.UUID(
            str(question_seed["methodology_question_record_id"])
        ),
        "methodology_version_record_id": uuid.UUID(
            str(question_seed["methodology_version_record_id"])
        ),
        "methodology_version_id": question_seed["methodology_version_id"],
        "methodology_question_id": question_seed["methodology_question_id"],
        "methodology_id": question_seed["methodology_id"],
        "methodology_type": question_seed["methodology_type"],
        "question_ordinal": question_seed["question_ordinal"],
        "section": question_seed["section"],
        "source_sheet_or_section": question_seed["source_sheet_or_section"],
        "source_row_number": question_seed["source_row_number"],
        "source_anchor_cell": question_seed["source_anchor_cell"],
        "source_sha256": question_seed["source_sha256"],
        "semantic_row_hash": question_seed["semantic_row_hash"],
        "question_payload_hash": question_seed["question_payload_hash"],
        "question_payload_json": _canonical_json(question_payload).decode("utf-8"),
    }
    cursor.execute(
        """
        INSERT INTO public.methodology_questions(
            tenant_id,methodology_question_record_id,
            methodology_version_record_id,methodology_version_id,
            methodology_question_id,methodology_id,methodology_type,
            question_ordinal,section,source_sheet_or_section,
            source_row_number,source_anchor_cell,source_sha256,
            semantic_row_hash,question_payload_hash,question_payload
        ) VALUES (
            %(tenant_id)s,%(methodology_question_record_id)s,
            %(methodology_version_record_id)s,%(methodology_version_id)s,
            %(methodology_question_id)s,%(methodology_id)s,%(methodology_type)s,
            %(question_ordinal)s,%(section)s,%(source_sheet_or_section)s,
            %(source_row_number)s,%(source_anchor_cell)s,%(source_sha256)s,
            %(semantic_row_hash)s,%(question_payload_hash)s,
            %(question_payload_json)s::jsonb
        );
        INSERT INTO public.methodology_coverage_records(
            coverage_record_id,tenant_id,deal_id,run_id,selection_id,
            methodology_version_record_id,methodology_version_id,
            methodology_question_record_id,methodology_question_id,
            methodology_type,status,reason_code,evidence_links,answer,
            conflict_ids,defect_ids
        ) VALUES (
            %(coverage_record_id)s,%(tenant_id)s,%(deal_id)s,%(run_id)s,
            %(selection_id)s,%(methodology_version_record_id)s,
            %(methodology_version_id)s,%(methodology_question_record_id)s,
            %(methodology_question_id)s,%(methodology_type)s,'not_started',NULL,
            '[]'::jsonb,NULL,ARRAY[]::text[],ARRAY[]::text[]
        )
        """,
        parameters,
    )


def _seed_scope(
    admin_url: str,
    tenant_id: uuid.UUID,
    deal_id: uuid.UUID,
    run_id: uuid.UUID,
) -> _CaseScope:
    lease_owner_id = _next_fixture_uuid()
    parent_step_id = _next_fixture_uuid()
    dashboard_step_id = _next_fixture_uuid()
    connection = _connect(admin_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SET LOCAL session_replication_role='replica';
                INSERT INTO public.tenants(tenant_id,name)
                VALUES (%s,'Task8 census tenant');
                INSERT INTO public.deals(deal_id,tenant_id,name,created_at,status)
                VALUES (%s,%s,'Task8 census deal',%s,'NEW');
                INSERT INTO public.runs(
                    run_id,tenant_id,deal_id,mode,status,lease_owner_id,
                    lease_fencing_token,lease_expires_at,last_heartbeat_at,
                    pipeline_contract_version,pipeline_contract_hash,
                    pipeline_resume_state
                ) VALUES (
                    %s,%s,%s,'FULL','RUNNING',%s,1,
                    transaction_timestamp()+interval '1 hour',
                    transaction_timestamp(),
                    'full-strict-v24',%s,'RESUMABLE'
                );
                INSERT INTO public.run_steps(
                    step_id,tenant_id,run_id,step_name,step_order,status,
                    pipeline_contract_version,pipeline_contract_hash,
                    semantic_rule_version,semantic_rule_hash
                ) VALUES
                    (%s,%s,%s,'TASK8_CENSUS_PARENT',100,'RUNNING',
                     'full-strict-v24',%s,'task8-v24-census-v1',%s),
                    (%s,%s,%s,'METHODOLOGY_TRUTH_DASHBOARD',101,'RUNNING',
                     'full-strict-v24',%s,'task8-v24-census-v1',%s)
                """,
                (
                    tenant_id,
                    deal_id,
                    tenant_id,
                    _FIXTURE_OCCURRED_AT,
                    run_id,
                    tenant_id,
                    deal_id,
                    lease_owner_id,
                    FULL_STRICT_V24_CONTRACT_HASH,
                    parent_step_id,
                    tenant_id,
                    run_id,
                    FULL_STRICT_V24_CONTRACT_HASH,
                    FULL_STRICT_V24_CONTRACT_HASH,
                    dashboard_step_id,
                    tenant_id,
                    run_id,
                    FULL_STRICT_V24_CONTRACT_HASH,
                    FULL_STRICT_V24_CONTRACT_HASH,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return _CaseScope(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        lease_owner_id=lease_owner_id,
        parent_step_id=parent_step_id,
        dashboard_step_id=dashboard_step_id,
    )


def _set_worker_lease(cursor: Any, scope: _CaseScope) -> None:
    _set_tenant(cursor, scope.tenant_id)
    for name, value in (
        ("idis.worker_lease_tenant_id", scope.tenant_id),
        ("idis.worker_lease_deal_id", scope.deal_id),
        ("idis.worker_lease_run_id", scope.run_id),
        ("idis.worker_lease_owner_id", scope.lease_owner_id),
        ("idis.worker_lease_fencing_token", 1),
    ):
        cursor.execute(
            "SELECT pg_catalog.set_config(%s,%s,true)",
            (name, str(value)),
        )


def _insert_seed_output(
    cursor: Any,
    *,
    scope: _CaseScope,
    output_object_id: uuid.UUID,
    output_kind: str,
    output_ordinal: int,
) -> None:
    # The real 0038 semantic-identity UNIQUE is live during the admin-only
    # fixture bootstrap, before the FK child that supplies the native identity
    # can exist.  These deterministic bootstrap values are scoped to the
    # object root and are replaced in the same seed transaction by the exact
    # installed authority before any application-role behavior is exercised.
    bootstrap_identity_sha256 = _canonical_sha256(
        ["task8-v24-admin-seed-bootstrap-identity-v1", str(output_object_id)]
    )
    bootstrap_output_sha256 = _canonical_sha256(
        ["task8-v24-admin-seed-bootstrap-output-v1", str(output_object_id)]
    )
    cursor.execute(
        """
        INSERT INTO public.run_output_objects(
            output_object_id,tenant_id,deal_id,run_id,output_class,output_kind,
            output_schema_version,native_identity_sha256,native_output_sha256,
            pipeline_contract_version,pipeline_contract_hash,write_origin,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,'NATIVE_ROW',%s,'task8-v24-census-parent-v1',
            %s,%s,'task8-v24-census-v1',%s,'BACKFILL',NULL,0
        );
        INSERT INTO public.run_step_outputs(
            step_output_id,tenant_id,deal_id,run_id,step_id,output_object_id,
            step_name,subrole,output_kind,output_ordinal,
            semantic_rule_version,semantic_rule_hash,summary_sha256,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,%s,%s,'TASK8_CENSUS_PARENT','__step__',%s,%s,
            'task8-v24-census-v1',%s,%s,%s,1
        )
        """,
        (
            output_object_id,
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            output_kind,
            bootstrap_identity_sha256,
            bootstrap_output_sha256,
            "a" * 64,
            _next_fixture_uuid(),
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            scope.parent_step_id,
            output_object_id,
            output_kind,
            output_ordinal,
            "a" * 64,
            "a" * 64,
            scope.lease_owner_id,
        ),
    )


def _refresh_seed_output_hashes(
    cursor: Any,
    *,
    scope: _CaseScope,
    roots: list[tuple[str, uuid.UUID, str]],
) -> None:
    """Finalize bootstrap hashes from the exact installed authority.

    Parent rows must exist before the Task-6 native rows because the real 0038
    schema retains their foreign keys.  The seed transaction therefore inserts
    deterministic bootstrap values with trigger execution disabled, creates the complete native
    graph, and then asks the *exact v24 helper installed from artifact bytes* for
    the values production validates.  No Python shadow recipe is used for the
    0038 source kinds.
    """

    for _root_kind, output_object_id, output_kind in roots:
        cursor.execute(
            """
            SELECT output_kind,expected_native_identity_sha256,
                   expected_native_output_sha256
              FROM public.idis_task7_current_native_hashes_v1(%s,%s,%s,%s)
            """,
            (
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                output_object_id,
            ),
        )
        expected = cursor.fetchall()
        if (
            len(expected) != 1
            or str(expected[0][0]) != output_kind
            or not all(
                isinstance(value, str) and _SHA256_RE.fullmatch(value) for value in expected[0][1:]
            )
        ):
            _fail("TASK8_V24_CENSUS_PARENT_NATIVE_HASH_INVALID")
        cursor.execute(
            """
            UPDATE public.run_output_objects
               SET native_identity_sha256=%s,
                   native_output_sha256=%s
             WHERE tenant_id=%s AND deal_id=%s AND run_id=%s
               AND output_object_id=%s AND output_kind=%s
            """,
            (
                expected[0][1],
                expected[0][2],
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                output_object_id,
                output_kind,
            ),
        )
        if cursor.rowcount != 1:
            _fail("TASK8_V24_CENSUS_PARENT_NATIVE_HASH_INVALID")


def _dashboard_case_shape(semantic_case: str) -> tuple[str, tuple[str, ...], bool, bool]:
    if semantic_case == "ALL_NOT_FOUND_GAP":
        return "GAP", ("EVIDENCE_NOT_FOUND",), False, False
    if semantic_case == "MIXED_FOUND_NOT_FOUND_ANSWERED":
        return "ANSWERED", ("EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"), False, False
    if semantic_case == "MIXED_FOUND_NOT_FOUND_WITH_DEFECT_RED_FLAG":
        return "RED_FLAG", ("EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"), False, True
    if semantic_case == "REQUIRED_FORMULA_MISSING":
        return "ANSWERED", ("EVIDENCE_FOUND",), True, False
    if semantic_case in {"GAP_WITH_FOUND_RESULT", "DIRECT_INSERT_TRIGGER_BYPASS"}:
        return "GAP", ("EVIDENCE_FOUND",), False, False
    if semantic_case == "ANSWERED_WITHOUT_EVIDENCE_SANAD":
        return "ANSWERED", ("EVIDENCE_FOUND",), False, False
    if semantic_case == "RED_FLAG_WITHOUT_ADVERSE_ROOT":
        return "RED_FLAG", ("EVIDENCE_FOUND",), False, False
    return "ANSWERED", ("EVIDENCE_FOUND",), False, False


def _seed_dashboard_graph(
    admin_url: str,
    *,
    scope: _CaseScope,
    semantic_case: str,
    question_seed: Mapping[str, Any],
    dashboard_output_ordinal: int = 0,
    connection: Any | None = None,
    parent_output_ordinals: Counter[str] | None = None,
) -> _DashboardGraph:
    methodology_type = question_seed.get("methodology_type")
    methodology_question_ordinal = question_seed.get("question_ordinal")
    if methodology_type not in {"commercial_dd", "financial_dd"}:
        _fail("TASK8_V24_CENSUS_METHODOLOGY_TYPE_INVALID")
    if not isinstance(methodology_question_ordinal, int) or methodology_question_ordinal <= 0:
        _fail("TASK8_V24_CENSUS_METHODOLOGY_ORDINAL_INVALID")
    disposition, outcomes, calculation_required, adverse = _dashboard_case_shape(semantic_case)
    question_seed = _question_seed_for_calculation_requirement(
        question_seed,
        required=calculation_required,
    )
    coverage_record_id = (
        "mc_"
        + _canonical_sha256(
            [
                "task8-v24-census-coverage-v1",
                str(scope.tenant_id),
                str(scope.deal_id),
                str(scope.run_id),
                methodology_type,
                methodology_question_ordinal,
                dashboard_output_ordinal,
            ]
        )[:24]
    )
    question_id = uuid.UUID(str(question_seed["methodology_question_record_id"]))
    version_id = uuid.UUID(str(question_seed["methodology_version_record_id"]))
    selection_id = uuid.UUID(
        _fixture_uuid("methodology-selection", str(scope.run_id), methodology_type)
    )
    methodology_version_id = str(question_seed["methodology_version_id"])
    methodology_question_id = str(question_seed["methodology_question_id"])
    question_payload_hash = str(question_seed["question_payload_hash"])
    roots: list[tuple[str, uuid.UUID, str]] = []
    output_ordinals = parent_output_ordinals if parent_output_ordinals is not None else Counter()
    owns_connection = connection is None
    if connection is None:
        connection = _connect(admin_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role='replica'")
            _insert_methodology_question_and_coverage(
                cursor,
                scope=scope,
                question_seed=question_seed,
                coverage_record_id=coverage_record_id,
                selection_id=selection_id,
            )
            for outcome_ordinal, outcome in enumerate(outcomes, start=1):
                task_id = _next_fixture_uuid()
                result_id = _next_fixture_uuid()
                attempt_id = _next_fixture_uuid()
                document_id = _next_fixture_uuid()
                span_id = _next_fixture_uuid()
                result_root = _next_fixture_uuid()
                _insert_seed_output(
                    cursor,
                    scope=scope,
                    output_object_id=result_root,
                    output_kind="METHODOLOGY_EXTRACTION_RESULT",
                    output_ordinal=output_ordinals["METHODOLOGY_EXTRACTION_RESULT"],
                )
                output_ordinals["METHODOLOGY_EXTRACTION_RESULT"] += 1
                roots.append(("RESULT", result_root, "METHODOLOGY_EXTRACTION_RESULT"))
                gap = {"code": "GOVERNED_EVIDENCE_GAP"} if outcome == "EVIDENCE_NOT_FOUND" else None
                cursor.execute(
                    """
                    INSERT INTO public.methodology_extraction_tasks(
                        task_record_id,tenant_id,deal_id,run_id,selection_id,
                        coverage_record_id,methodology_version_record_id,
                        methodology_version_id,methodology_question_record_id,
                        methodology_question_id,methodology_type,extraction_task_id,
                        document_id,document_classification_id,
                        document_classification_sha256,planner_contract_version,
                        task_contract_version,task_payload,task_input_sha256,
                        candidate_span_set_sha256,status
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,
                        'task8_census_document',%s,'task8-census-planner-v1',
                        'task8-census-task-v1',%s::jsonb,%s,%s,'READY'
                    );
                    INSERT INTO public.methodology_extraction_results(
                        result_id,tenant_id,deal_id,run_id,task_record_id,attempt_id,
                        task_input_sha256,outcome,result_payload,result_sha256,
                        searched_document_ids,searched_span_ids,searched_scope_sha256,
                        provider_provenance_sha256,gap_finding,run_output_object_id
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                        ARRAY[%s]::uuid[],ARRAY[%s]::uuid[],%s,%s,%s::jsonb,%s
                    )
                    """,
                    (
                        task_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        selection_id,
                        coverage_record_id,
                        version_id,
                        methodology_version_id,
                        question_id,
                        methodology_question_id,
                        methodology_type,
                        f"task8_census_task_{dashboard_output_ordinal}_{outcome_ordinal}",
                        document_id,
                        "a" * 64,
                        json.dumps(
                            {"coverage_record_id": coverage_record_id},
                            separators=(",", ":"),
                        ),
                        "a" * 64,
                        "a" * 64,
                        result_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        task_id,
                        attempt_id,
                        "a" * 64,
                        outcome,
                        json.dumps({"outcome": outcome}, separators=(",", ":")),
                        "a" * 64,
                        document_id,
                        span_id,
                        "a" * 64,
                        "a" * 64,
                        None if gap is None else json.dumps(gap, separators=(",", ":")),
                        result_root,
                    ),
                )
                if outcome != "EVIDENCE_FOUND":
                    continue
                factual_id = _next_fixture_uuid()
                factual_record_id = _next_fixture_uuid()
                claim_link_id = _next_fixture_uuid()
                evidence_id = _next_fixture_uuid()
                sanad_id = _next_fixture_uuid()
                evidence_link_id = _next_fixture_uuid()
                sanad_link_id = _next_fixture_uuid()
                cursor.execute(
                    """
                    INSERT INTO public.methodology_result_factual_outputs(
                        factual_output_record_id,tenant_id,deal_id,run_id,result_id,
                        task_record_id,output_ordinal,factual_output_id,
                        typed_output_payload,typed_output_sha256,
                        accepted_source_span_ids,accepted_locator_sha256s,
                        accepted_locator_set_sha256
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,1,%s,%s::jsonb,%s,
                        ARRAY[%s]::uuid[],ARRAY[%s]::text[],%s
                    )
                    """,
                    (
                        factual_record_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        result_id,
                        task_id,
                        factual_id,
                        json.dumps({"value": "task8-census"}, separators=(",", ":")),
                        "a" * 64,
                        span_id,
                        "b" * 64,
                        "a" * 64,
                    ),
                )
                if semantic_case == "ANSWERED_WITHOUT_EVIDENCE_SANAD":
                    continue
                evidence_root = _next_fixture_uuid()
                sanad_root = _next_fixture_uuid()
                for output_id, output_kind in (
                    (evidence_root, "METHODOLOGY_EVIDENCE_LINK"),
                    (sanad_root, "METHODOLOGY_SANAD_LINK"),
                ):
                    _insert_seed_output(
                        cursor,
                        scope=scope,
                        output_object_id=output_id,
                        output_kind=output_kind,
                        output_ordinal=output_ordinals[output_kind],
                    )
                    output_ordinals[output_kind] += 1
                cursor.execute(
                    """
                    INSERT INTO public.methodology_result_evidence_links(
                        evidence_link_id,tenant_id,deal_id,run_id,result_id,
                        factual_output_record_id,claim_link_id,evidence_id,document_id,
                        source_span_id,locator_sha256,run_output_object_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
                    INSERT INTO public.methodology_result_sanad_links(
                        sanad_link_id,tenant_id,deal_id,run_id,result_id,
                        factual_output_record_id,claim_link_id,sanad_id,
                        primary_evidence_id,run_output_object_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        evidence_link_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        result_id,
                        factual_record_id,
                        claim_link_id,
                        evidence_id,
                        document_id,
                        span_id,
                        "a" * 64,
                        evidence_root,
                        sanad_link_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        result_id,
                        factual_record_id,
                        claim_link_id,
                        sanad_id,
                        evidence_id,
                        sanad_root,
                    ),
                )
                roots.extend(
                    (
                        ("EVIDENCE", evidence_root, "METHODOLOGY_EVIDENCE_LINK"),
                        ("SANAD", sanad_root, "METHODOLOGY_SANAD_LINK"),
                    )
                )
                if adverse:
                    defect_root = _next_fixture_uuid()
                    defect_link_id = _next_fixture_uuid()
                    _insert_seed_output(
                        cursor,
                        scope=scope,
                        output_object_id=defect_root,
                        output_kind="METHODOLOGY_DEFECT_LINK",
                        output_ordinal=output_ordinals["METHODOLOGY_DEFECT_LINK"],
                    )
                    output_ordinals["METHODOLOGY_DEFECT_LINK"] += 1
                    cursor.execute(
                        """
                        INSERT INTO public.methodology_result_defect_links(
                            defect_link_id,tenant_id,deal_id,run_id,result_id,
                            factual_output_record_id,sanad_link_id,defect_id,
                            run_output_object_id
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            defect_link_id,
                            scope.tenant_id,
                            scope.deal_id,
                            scope.run_id,
                            result_id,
                            factual_record_id,
                            sanad_link_id,
                            _next_fixture_uuid(),
                            defect_root,
                        ),
                    )
                    roots.append(
                        (
                            "ADVERSE_CONTRADICTION",
                            defect_root,
                            "METHODOLOGY_DEFECT_LINK",
                        )
                    )
            _refresh_seed_output_hashes(cursor, scope=scope, roots=roots)
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()

    root_coordinates: list[dict[str, object]] = []
    for root_kind in sorted({row[0] for row in roots}):
        matching = sorted(
            (row for row in roots if row[0] == root_kind),
            key=lambda row: (row[1].int, row[2]),
        )
        for ordinal, (_kind, output_id, output_kind) in enumerate(matching):
            root_coordinates.append(
                {
                    "root_kind": root_kind,
                    "root_ordinal": ordinal,
                    "root_output_object_id": str(output_id),
                    "root_output_object_kind": output_kind,
                }
            )
    result_records = [
        ["result", _canonical_sha256(["task8-v24-census-root-v1", str(root[1])])]
        for root in roots
        if root[0] == "RESULT"
    ]
    identity = derive_v24_dashboard_identity(
        tenant_id=str(scope.tenant_id),
        deal_id=str(scope.deal_id),
        run_id=str(scope.run_id),
        pipeline_contract_version=PIPELINE_CONTRACT_VERSION,
        pipeline_contract_hash=FULL_STRICT_V24_CONTRACT_HASH,
        coverage_record_id=coverage_record_id,
        methodology_version_record_id=str(version_id),
        methodology_question_record_id=str(question_id),
        question_payload_hash=question_payload_hash,
        row_disposition=disposition,
        disposition_reason_code=("EVIDENCE_NOT_FOUND" if disposition == "GAP" else None),
        row_trace_prefix=[
            dashboard_output_ordinal + 1,
            1 if methodology_type == "commercial_dd" else 2,
            "Task8 census",
            methodology_question_ordinal,
            f"Task8 census!{methodology_question_ordinal}",
            str(version_id),
            methodology_version_id,
            str(question_id),
            methodology_question_id,
            question_payload_hash,
        ],
        result_records=result_records,
        answer_values=[],
        task_gaps=(
            [["gap", value] for _kind, value in result_records]
            if "EVIDENCE_NOT_FOUND" in outcomes
            else []
        ),
        calculations=[],
        roots=root_coordinates,
    )
    root_links = tuple(
        (
            str(kind),
            int(ordinal),
            uuid.UUID(str(output_id)),
            str(output_kind),
            uuid.UUID(str(link_id)),
        )
        for kind, ordinal, output_id, output_kind, link_id in identity["root_link_tuple"]
    )
    return _DashboardGraph(
        scope=scope,
        coverage_record_id=coverage_record_id,
        dashboard_row_id=uuid.UUID(str(identity["dashboard_row_id"])),
        dashboard_output_object_id=uuid.UUID(str(identity["output_object_id"])),
        root_links=root_links,
        root_projection_sha256=str(identity["root_projection_sha256"]),
        row_payload_sha256=str(identity["row_payload_sha256"]),
        native_identity_sha256=str(identity["native_identity_sha256"]),
        native_output_sha256=str(identity["native_output_sha256"]),
        row_disposition=disposition,
        dashboard_output_ordinal=dashboard_output_ordinal,
    )


def _seed_dashboard_batch(
    admin_url: str,
    *,
    scope: _CaseScope,
    dashboard_seed_plan: Mapping[str, Any],
    focus_semantic_case: str,
    coverage_row_count: int,
    financial_coverage_row_count: int,
) -> tuple[_DashboardGraph, ...]:
    """Seed one exact scoped coverage census in a single disposable transaction."""

    commercial_coverage_row_count = coverage_row_count - financial_coverage_row_count
    if (
        coverage_row_count <= 0
        or financial_coverage_row_count <= 0
        or commercial_coverage_row_count <= 0
    ):
        _fail("TASK8_V24_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    plan_rows = dashboard_seed_plan.get("row_tuple")
    plan_payload = {
        key: value for key, value in dashboard_seed_plan.items() if key != "expected_sha256"
    }
    if (
        dashboard_seed_plan.get("schema_version") != "task8-v24-dashboard-seed-plan-v1"
        or not isinstance(plan_rows, list)
        or len(plan_rows) != 100
        or dashboard_seed_plan.get("expected_sha256") != _canonical_sha256(plan_payload)
    ):
        _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
    question_seeds = []
    for plan_row in plan_rows:
        plan_question_seed = (
            plan_row.get("question_seed") if isinstance(plan_row, Mapping) else None
        )
        source = (
            plan_question_seed.get("source_row_preimage")
            if isinstance(plan_question_seed, Mapping)
            else None
        )
        if not isinstance(source, Mapping):
            _fail("TASK8_V24_CENSUS_QUESTION_SEED_INVALID")
        question_seeds.append(
            _derive_methodology_question_seed(
                source,
                tenant_id=str(scope.tenant_id),
                calculation_required=False,
            )
        )
    selected_question_seeds = question_seeds[:coverage_row_count]
    if (
        len(selected_question_seeds) != coverage_row_count
        or sum(row["methodology_type"] == "financial_dd" for row in selected_question_seeds)
        != financial_coverage_row_count
    ):
        _fail("TASK8_V24_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    connection = _connect(admin_url)
    parent_output_ordinals: Counter[str] = Counter()
    try:
        graphs = tuple(
            _seed_dashboard_graph(
                admin_url,
                scope=scope,
                semantic_case=(focus_semantic_case if ordinal == 0 else "ALL_NOT_FOUND_GAP"),
                question_seed=selected_question_seeds[ordinal],
                dashboard_output_ordinal=ordinal,
                connection=connection,
                parent_output_ordinals=parent_output_ordinals,
            )
            for ordinal in range(coverage_row_count)
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return graphs


def _insert_dashboard_output_and_row(
    cursor: Any,
    graph: _DashboardGraph,
    *,
    include_row: bool = True,
    corrupt_native_hashes: bool = False,
) -> None:
    scope = graph.scope
    row_payload_sha256 = graph.row_payload_sha256
    pipeline_contract_version = PIPELINE_CONTRACT_VERSION
    pipeline_contract_hash = FULL_STRICT_V24_CONTRACT_HASH
    native_identity_sha256 = graph.native_identity_sha256
    native_output_sha256 = graph.native_output_sha256
    if corrupt_native_hashes:
        native_output_sha256 = "f" * 64
    cursor.execute(
        """
        INSERT INTO public.run_output_objects(
            output_object_id,tenant_id,deal_id,run_id,output_class,output_kind,
            output_schema_version,native_identity_sha256,native_output_sha256,
            pipeline_contract_version,pipeline_contract_hash,write_origin,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,'NATIVE_ROW','METHODOLOGY_TRUTH_DASHBOARD_ROW',
            'task8-methodology-truth-dashboard-row-v1',%s,%s,
            %s,%s,'RUNTIME',%s,1
        );
        INSERT INTO public.run_step_outputs(
            step_output_id,tenant_id,deal_id,run_id,step_id,output_object_id,
            step_name,subrole,output_kind,output_ordinal,
            semantic_rule_version,semantic_rule_hash,summary_sha256,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,%s,%s,'METHODOLOGY_TRUTH_DASHBOARD','__step__',
            'METHODOLOGY_TRUTH_DASHBOARD_ROW',%s,'task8-v24-census-v1',%s,%s,%s,1
        )
        """,
        (
            graph.dashboard_output_object_id,
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            native_identity_sha256,
            native_output_sha256,
            pipeline_contract_version,
            pipeline_contract_hash,
            scope.lease_owner_id,
            _next_fixture_uuid(),
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            scope.dashboard_step_id,
            graph.dashboard_output_object_id,
            graph.dashboard_output_ordinal,
            row_payload_sha256,
            FULL_STRICT_V24_CONTRACT_HASH,
            scope.lease_owner_id,
        ),
    )
    if not include_row:
        return
    cursor.execute(
        """
        INSERT INTO public.methodology_truth_dashboard_rows(
            dashboard_row_id,tenant_id,deal_id,run_id,run_output_object_id,
            lease_owner_id,lease_fencing_token,coverage_record_id,
            root_projection_sha256,row_disposition,row_payload_sha256
        ) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
        """,
        (
            graph.dashboard_row_id,
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            graph.dashboard_output_object_id,
            scope.lease_owner_id,
            graph.coverage_record_id,
            graph.root_projection_sha256,
            graph.row_disposition,
            row_payload_sha256,
        ),
    )
    for kind, ordinal, output_id, output_kind, link_id in graph.root_links:
        cursor.execute(
            """
            INSERT INTO public.methodology_truth_dashboard_root_links(
                dashboard_root_link_id,tenant_id,deal_id,run_id,dashboard_row_id,
                dashboard_output_object_id,root_kind,root_ordinal,
                root_output_object_id,root_output_object_kind,
                lease_owner_id,lease_fencing_token
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            """,
            (
                link_id,
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                graph.dashboard_row_id,
                graph.dashboard_output_object_id,
                kind,
                ordinal,
                output_id,
                output_kind,
                scope.lease_owner_id,
            ),
        )


def _insert_dashboard_batch(
    cursor: Any,
    graphs: tuple[_DashboardGraph, ...],
    *,
    include_focus_row: bool = True,
    omit_focus_output: bool = False,
    corrupt_focus_native_hashes: bool = False,
) -> None:
    if not graphs or tuple(graph.dashboard_output_ordinal for graph in graphs) != tuple(
        range(len(graphs))
    ):
        _fail("TASK8_V24_CENSUS_DASHBOARD_BATCH_INVALID")
    for ordinal, graph in enumerate(graphs):
        if omit_focus_output and ordinal == 0:
            continue
        _insert_dashboard_output_and_row(
            cursor,
            graph,
            include_row=include_focus_row or ordinal != 0,
            corrupt_native_hashes=corrupt_focus_native_hashes and ordinal == 0,
        )


def _additional_dashboard_graph(
    graph: _DashboardGraph,
    *,
    coverage_record_id: str,
    dashboard_output_ordinal: int,
) -> _DashboardGraph:
    """Build a shape-valid extra row used only to reach an immediate FK/UNIQUE guard."""

    question_payload_hash = _canonical_sha256(
        ["task8-v24-extra-dashboard-question-v1", coverage_record_id]
    )
    identity = derive_v24_dashboard_identity(
        tenant_id=str(graph.scope.tenant_id),
        deal_id=str(graph.scope.deal_id),
        run_id=str(graph.scope.run_id),
        pipeline_contract_version=PIPELINE_CONTRACT_VERSION,
        pipeline_contract_hash=FULL_STRICT_V24_CONTRACT_HASH,
        coverage_record_id=coverage_record_id,
        methodology_version_record_id=_fixture_uuid("extra-dashboard-version", coverage_record_id),
        methodology_question_record_id=_fixture_uuid(
            "extra-dashboard-question", coverage_record_id
        ),
        question_payload_hash=question_payload_hash,
        row_disposition="GAP",
        disposition_reason_code="EVIDENCE_NOT_FOUND",
        row_trace_prefix=[
            dashboard_output_ordinal + 1,
            1,
            "Task8 census",
            dashboard_output_ordinal + 1,
            f"Task8 census!{dashboard_output_ordinal + 1}",
            _fixture_uuid("extra-dashboard-version", coverage_record_id),
            "task8-census-extra-v1",
            _fixture_uuid("extra-dashboard-question", coverage_record_id),
            f"task8_census_extra_{dashboard_output_ordinal + 1:04d}",
            question_payload_hash,
        ],
        result_records=[],
        answer_values=[],
        task_gaps=[],
        calculations=[],
        roots=[],
    )
    return replace(
        graph,
        coverage_record_id=coverage_record_id,
        dashboard_row_id=uuid.UUID(str(identity["dashboard_row_id"])),
        dashboard_output_object_id=uuid.UUID(str(identity["output_object_id"])),
        root_links=(),
        root_projection_sha256=str(identity["root_projection_sha256"]),
        row_payload_sha256=str(identity["row_payload_sha256"]),
        native_identity_sha256=str(identity["native_identity_sha256"]),
        native_output_sha256=str(identity["native_output_sha256"]),
        row_disposition="GAP",
        dashboard_output_ordinal=dashboard_output_ordinal,
    )


def _seed_erasure_request(admin_url: str, scope: _CaseScope) -> uuid.UUID:
    try:
        from idis.compliance.task7_erasure import (
            build_erasure_audit_event,
            derive_attempt_lineage,
            object_target_set_sha256,
        )
    except ImportError as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_ERASURE_FIXTURE_UNAVAILABLE") from exc
    request_id = _next_fixture_uuid()
    actor_id = "task8-census-admin"
    lineage = derive_attempt_lineage(
        tenant_id=str(scope.tenant_id),
        deal_id=str(scope.deal_id),
        request_id=str(request_id),
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
    )
    occurred_at = _FIXTURE_OCCURRED_AT
    event = build_erasure_audit_event(
        tenant_id=str(scope.tenant_id),
        deal_id=str(scope.deal_id),
        actor_id=actor_id,
        occurred_at=occurred_at,
        lineage=lineage,
        event_type="task7.erasure.attempted",
        object_target_set_sha256=object_target_set_sha256(
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            ordered_uris=(),
        ),
        counts=None,
    )
    connection = _connect(admin_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role='replica'")
            cursor.execute(
                """
                INSERT INTO public.erasure_requests(
                    tenant_id,request_id,deal_id,status,requested_by,requested_at,
                    reason_hash,reason_length
                ) VALUES (%s,%s,%s,'REQUESTED',%s,%s,%s,1);
                INSERT INTO public.audit_events(
                    event_id,tenant_id,occurred_at,event_type,request_id,
                    idempotency_key,event
                ) VALUES (%s,%s,%s,'task7.erasure.attempted',%s,%s,%s::jsonb)
                """,
                (
                    scope.tenant_id,
                    request_id,
                    scope.deal_id,
                    actor_id,
                    occurred_at,
                    "a" * 64,
                    uuid.UUID(event["event_id"]),
                    scope.tenant_id,
                    occurred_at,
                    str(request_id),
                    event["event_id"],
                    json.dumps(event, separators=(",", ":")),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return request_id


def _call_erasure(
    cursor: Any,
    tenant_id: uuid.UUID,
    deal_id: uuid.UUID,
    request_id: uuid.UUID,
) -> None:
    cursor.execute(
        """
        SELECT public.idis_task7_execute_approved_erasure(%s, %s, %s)
        """,
        # The frozen function's positional contract is request, tenant, deal;
        # this matches the production dedicated-connection adapter.
        (request_id, tenant_id, deal_id),
    )


def _insert_future_rls_probe(
    cursor: Any,
    *,
    graph: _DashboardGraph,
    relation: str,
) -> None:
    scope = graph.scope
    if relation == "methodology_truth_dashboard_rows":
        cursor.execute(
            """
            INSERT INTO public.methodology_truth_dashboard_rows(
                dashboard_row_id,tenant_id,deal_id,run_id,run_output_object_id,
                lease_owner_id,lease_fencing_token,coverage_record_id,
                root_projection_sha256,row_disposition,row_payload_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
            """,
            (
                _next_fixture_uuid(),
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                graph.dashboard_output_object_id,
                scope.lease_owner_id,
                graph.coverage_record_id,
                graph.root_projection_sha256,
                graph.row_disposition,
                "a" * 64,
            ),
        )
        return
    if relation == "methodology_truth_dashboard_root_links":
        kind, ordinal, output_id, output_kind, _link_id = graph.root_links[0]
        cursor.execute(
            """
            INSERT INTO public.methodology_truth_dashboard_root_links(
                dashboard_root_link_id,tenant_id,deal_id,run_id,dashboard_row_id,
                dashboard_output_object_id,root_kind,root_ordinal,
                root_output_object_id,root_output_object_kind,
                lease_owner_id,lease_fencing_token
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            """,
            (
                _next_fixture_uuid(),
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                graph.dashboard_row_id,
                graph.dashboard_output_object_id,
                kind,
                ordinal + 1000,
                output_id,
                output_kind,
                scope.lease_owner_id,
            ),
        )
        return
    _fail("TASK8_V24_CENSUS_RLS_RELATION_INVALID")


def _tenant_deal_projection(
    application_database_url: str,
    *,
    expected_application_role: str,
    tenant_id: uuid.UUID,
    deal_id: uuid.UUID,
    projection_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Hash the reviewed scope under the exact application/RLS identity."""

    relation_tuple = projection_manifest.get("control_relation_tuple")
    current_relation_tuple = projection_manifest.get("current_relation_tuple")
    if (
        not isinstance(relation_tuple, list)
        or not isinstance(current_relation_tuple, list)
        or not set(current_relation_tuple).issubset(relation_tuple)
        or projection_manifest.get("timestamp_policy") != "compare-exact-redact-from-digest-v1"
    ):
        _fail("TASK8_V24_CENSUS_PROJECTION_MANIFEST_INVALID")
    connection = _connect(application_database_url)
    try:
        with connection.cursor() as cursor:
            current_user = _attest_connection_role_posture(
                cursor,
                expected_application_role,
            )
            _set_tenant(cursor, tenant_id)
            full_rows: list[list[Any]] = []
            stable_rows: list[list[Any]] = []
            for table_name in relation_tuple:
                if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name) is None:
                    _fail("TASK8_V24_CENSUS_PROJECTION_RELATION_INVALID")
                cursor.execute(
                    """
                    SELECT array_agg(column_name::text ORDER BY ordinal_position)::text[],
                           COALESCE(
                               array_agg(column_name::text ORDER BY ordinal_position)
                                   FILTER (
                                       WHERE data_type IN (
                                           'timestamp with time zone',
                                           'timestamp without time zone'
                                       )
                                   ),
                               ARRAY[]::text[]
                           )
                      FROM information_schema.columns
                     WHERE table_schema='public' AND table_name=%s
                    """,
                    (table_name,),
                )
                raw_columns, raw_timestamp_columns = cursor.fetchone()
                columns = set(raw_columns or ())
                timestamp_columns = list(raw_timestamp_columns or ())
                if not columns:
                    _fail("TASK8_V24_CENSUS_PROJECTION_RELATION_MISSING")
                if {"tenant_id", "deal_id"}.issubset(columns):
                    scope_sql = "scoped.tenant_id=%s AND scoped.deal_id=%s"
                    from_sql = f"public.{table_name} AS scoped"
                elif table_name == "calc_sanads" and {"tenant_id", "calc_id"}.issubset(columns):
                    from_sql = (
                        "public.calc_sanads AS scoped JOIN "
                        "public.deterministic_calculations AS parent ON "
                        "parent.tenant_id=scoped.tenant_id AND parent.calc_id=scoped.calc_id"
                    )
                    scope_sql = "parent.tenant_id=%s AND parent.deal_id=%s"
                elif table_name == "human_gate_actions" and {
                    "tenant_id",
                    "gate_id",
                }.issubset(columns):
                    from_sql = (
                        "public.human_gate_actions AS scoped JOIN "
                        "public.human_gates AS parent ON "
                        "parent.tenant_id=scoped.tenant_id AND parent.gate_id=scoped.gate_id"
                    )
                    scope_sql = "parent.tenant_id=%s AND parent.deal_id=%s"
                elif table_name == "run_steps" and {"tenant_id", "run_id"}.issubset(columns):
                    from_sql = (
                        "public.run_steps AS scoped JOIN public.runs AS parent ON "
                        "parent.tenant_id=scoped.tenant_id AND parent.run_id=scoped.run_id"
                    )
                    scope_sql = "parent.tenant_id=%s AND parent.deal_id=%s"
                elif table_name == "audit_events" and {"tenant_id", "event"}.issubset(columns):
                    from_sql = "public.audit_events AS scoped"
                    scope_sql = "scoped.tenant_id=%s AND scoped.event->'resource'->>'deal_id'=%s"
                else:
                    _fail(f"TASK8_V24_CENSUS_PROJECTION_SCOPE_UNPROVEN:{table_name}")
                cursor.execute(
                    f"""
                    SELECT count(*),
                           COALESCE(
                             jsonb_agg(
                               to_jsonb(scoped)
                               ORDER BY to_jsonb(scoped)::text COLLATE "C"
                             )::text,
                             '[]'
                           ),
                           COALESCE(
                             jsonb_agg(
                               to_jsonb(scoped) - %s::text[]
                               ORDER BY (
                                   to_jsonb(scoped) - %s::text[]
                               )::text COLLATE "C"
                             )::text,
                             '[]'
                           )
                      FROM (
                        SELECT scoped.*
                          FROM {from_sql}
                         WHERE {scope_sql}
                      ) AS scoped
                    """,
                    (
                        timestamp_columns,
                        timestamp_columns,
                        tenant_id,
                        str(deal_id) if table_name == "audit_events" else deal_id,
                    ),
                )
                count, canonical_full_rows, canonical_stable_rows = cursor.fetchone()
                full_rows.append(
                    [
                        table_name,
                        int(count),
                        hashlib.sha256(str(canonical_full_rows).encode("utf-8")).hexdigest(),
                    ]
                )
                stable_rows.append(
                    [
                        table_name,
                        int(count),
                        timestamp_columns,
                        hashlib.sha256(str(canonical_stable_rows).encode("utf-8")).hexdigest(),
                    ]
                )
        connection.rollback()
    finally:
        connection.close()
    return {
        "observer_role": current_user,
        "relation_count": len(stable_rows),
        "row_count": sum(row[1] for row in stable_rows),
        "full_projection_sha256": _canonical_sha256(
            [
                "task8-v24-tenant-deal-full-projection-v1",
                projection_manifest["expected_sha256"],
                str(tenant_id),
                str(deal_id),
                full_rows,
            ]
        ),
        "projection_sha256": _canonical_sha256(
            [
                "task8-v24-tenant-deal-projection-v1",
                projection_manifest["expected_sha256"],
                str(tenant_id),
                str(deal_id),
                stable_rows,
            ]
        ),
    }


def _run_rls_behavior_matrix(
    urls: _DisposableUrls,
    projection_manifest: Mapping[str, Any],
    dashboard_seed_plan: Mapping[str, Any],
    dashboard_coverage_row_count: int,
    dashboard_financial_coverage_row_count: int,
) -> tuple[bool, list[dict[str, Any]]]:
    scope = _seed_scope(
        urls.admin,
        _next_fixture_uuid(),
        _next_fixture_uuid(),
        _next_fixture_uuid(),
    )
    graphs = _seed_dashboard_batch(
        urls.admin,
        scope=scope,
        dashboard_seed_plan=dashboard_seed_plan,
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=dashboard_coverage_row_count,
        financial_coverage_row_count=dashboard_financial_coverage_row_count,
    )
    graph = graphs[0]
    baseline = _connect(urls.application)
    try:
        with baseline.cursor() as cursor:
            _attest_connection_role_posture(cursor, urls.application_role)
            _set_worker_lease(cursor, scope)
            _insert_dashboard_batch(cursor, graphs)
        baseline.commit()
    finally:
        baseline.close()

    relations = (
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
    )
    results: list[dict[str, Any]] = []
    for mode, tenant_value in (
        ("WRONG_TENANT", str(_next_fixture_uuid())),
        ("UNSET_TENANT", ""),
    ):
        read_connection = _connect(urls.application)
        try:
            with read_connection.cursor() as cursor:
                current_user = _attest_connection_role_posture(
                    cursor,
                    urls.application_role,
                )
                cursor.execute(
                    "SELECT pg_catalog.set_config('idis.tenant_id',%s,true)",
                    (tenant_value,),
                )
                for relation in relations:
                    cursor.execute(f"SELECT count(*) FROM public.{relation}")
                    visible = int(cursor.fetchone()[0])
                    results.append(
                        {
                            "mode": mode,
                            "relation": relation,
                            "operation": "READ",
                            "observer_role": current_user,
                            "visible_row_count": visible,
                            "passed": visible == 0,
                        }
                    )
            read_connection.rollback()
        finally:
            read_connection.close()

        for relation in relations:
            pre = _tenant_deal_projection(
                urls.application,
                expected_application_role=urls.application_role,
                tenant_id=scope.tenant_id,
                deal_id=scope.deal_id,
                projection_manifest=projection_manifest,
            )
            write_connection = _connect(urls.application)
            sqlstate: str | None = None
            write_observer_role: str | None = None
            try:
                with write_connection.cursor() as cursor:
                    write_current_user = _attest_connection_role_posture(
                        cursor,
                        urls.application_role,
                    )
                    write_observer_role = write_current_user
                    cursor.execute(
                        "SELECT pg_catalog.set_config('idis.tenant_id',%s,true)",
                        (tenant_value,),
                    )
                    _insert_future_rls_probe(cursor, graph=graph, relation=relation)
                write_connection.commit()
            except Exception as exc:
                sqlstate = getattr(exc, "pgcode", None)
                write_connection.rollback()
            finally:
                write_connection.close()
            post = _tenant_deal_projection(
                urls.application,
                expected_application_role=urls.application_role,
                tenant_id=scope.tenant_id,
                deal_id=scope.deal_id,
                projection_manifest=projection_manifest,
            )
            results.append(
                {
                    "mode": mode,
                    "relation": relation,
                    "operation": "WRITE",
                    "observer_role": write_observer_role,
                    "sqlstate": sqlstate,
                    "zero_mutation": pre == post,
                    "passed": sqlstate == "42501" and pre == post,
                }
            )

    same_tenant = _connect(urls.application)
    try:
        with same_tenant.cursor() as cursor:
            same_current_user = _attest_connection_role_posture(
                cursor,
                urls.application_role,
            )
            _set_tenant(cursor, scope.tenant_id)
            for relation in relations:
                cursor.execute(f"SELECT count(*) FROM public.{relation}")
                visible = int(cursor.fetchone()[0])
                results.append(
                    {
                        "mode": "SAME_TENANT",
                        "relation": relation,
                        "operation": "READ",
                        "observer_role": same_current_user,
                        "visible_row_count": visible,
                        "passed": visible > 0,
                    }
                )
        same_tenant.rollback()
    finally:
        same_tenant.close()
    return all(row["passed"] for row in results), results


def _execute_behavior_case(
    case: Mapping[str, Any],
    *,
    urls: _DisposableUrls,
    projection_manifest: Mapping[str, Any],
    dashboard_seed_plan: Mapping[str, Any],
    dashboard_coverage_row_count: int,
    dashboard_financial_coverage_row_count: int,
) -> dict[str, Any]:
    tenant_id = _next_fixture_uuid()
    deal_id = _next_fixture_uuid()
    run_id = _next_fixture_uuid()
    scope = _seed_scope(urls.admin, tenant_id, deal_id, run_id)
    role = case["execution_role"]
    rejected = case["expected"] != "ACCEPT"
    semantic = str(case["semantic_case"])
    graph: _DashboardGraph | None = None
    graphs: tuple[_DashboardGraph, ...] = ()
    global_dashboard_plan: _GlobalDashboardCensusPlan | None = None
    request_id: uuid.UUID | None = None

    if case["authority_id"] in _AUTHORITY_IDS[:2]:
        if semantic.startswith("GLOBAL_"):
            global_dashboard_plan = _global_dashboard_census_plan(
                semantic,
                coverage_row_count=dashboard_coverage_row_count,
                financial_coverage_row_count=dashboard_financial_coverage_row_count,
            )
        graph_semantic = (
            "ALL_FOUND_ANSWERED"
            if case["authority_id"] == _AUTHORITY_IDS[1] or global_dashboard_plan is not None
            else semantic
        )
        seed_coverage_row_count = (
            global_dashboard_plan.seed_coverage_row_count
            if global_dashboard_plan is not None
            else dashboard_coverage_row_count
        )
        seed_financial_coverage_row_count = (
            global_dashboard_plan.seed_financial_coverage_row_count
            if global_dashboard_plan is not None
            else dashboard_financial_coverage_row_count
        )
        graphs = _seed_dashboard_batch(
            urls.admin,
            scope=scope,
            dashboard_seed_plan=dashboard_seed_plan,
            focus_semantic_case=graph_semantic,
            coverage_row_count=seed_coverage_row_count,
            financial_coverage_row_count=seed_financial_coverage_row_count,
        )
        graph = graphs[0]
    elif case["authority_id"] == _AUTHORITY_IDS[2]:
        if semantic in {
            "DASHBOARD_ROWS_REQUIRE_FUTURE38",
            "EXECUTOR_CANNOT_BYPASS_DASHBOARD_REFUSAL",
        }:
            graphs = _seed_dashboard_batch(
                urls.admin,
                scope=scope,
                dashboard_seed_plan=dashboard_seed_plan,
                focus_semantic_case="ALL_FOUND_ANSWERED",
                coverage_row_count=dashboard_coverage_row_count,
                financial_coverage_row_count=dashboard_financial_coverage_row_count,
            )
            graph = graphs[0]
            app_connection = _connect(urls.application)
            try:
                with app_connection.cursor() as app_cursor:
                    _attest_connection_role_posture(
                        app_cursor,
                        urls.application_role,
                    )
                    _set_worker_lease(app_cursor, scope)
                    _insert_dashboard_batch(app_cursor, graphs)
                app_connection.commit()
            finally:
                app_connection.close()
        request_id = _seed_erasure_request(urls.admin, scope)

    pre_projection = None
    if rejected:
        pre_projection = _tenant_deal_projection(
            urls.application,
            expected_application_role=urls.application_role,
            tenant_id=tenant_id,
            deal_id=deal_id,
            projection_manifest=projection_manifest,
        )
    database_url = urls.application if role == "application" else urls.erasure
    connection = _connect(database_url)
    observed_sqlstate: str | None = None
    observed_reason: str | None = None
    observed_current_user: str | None = None
    observed_session_user: str | None = None
    expected_database_role = urls.application_role if role == "application" else urls.erasure_role
    try:
        with connection.cursor() as cursor:
            observed_current_user = _attest_connection_role_posture(
                cursor,
                expected_database_role,
            )
            observed_session_user = observed_current_user
            if role == "application":
                _set_worker_lease(cursor, scope)
            if case["authority_id"] == _AUTHORITY_IDS[0]:
                if graph is None:
                    _fail("TASK8_V24_CENSUS_FIXTURE_INVALID")
                if global_dashboard_plan is not None and global_dashboard_plan.omit_focus_output:
                    _insert_dashboard_batch(cursor, graphs, omit_focus_output=True)
                elif global_dashboard_plan is not None and (
                    global_dashboard_plan.extra_row_disposition is None
                ):
                    _insert_dashboard_batch(cursor, graphs)
                elif global_dashboard_plan is not None:
                    _insert_dashboard_batch(cursor, graphs)
                    extra_coverage_record_id = (
                        graph.coverage_record_id
                        if global_dashboard_plan.extra_row_disposition == "DUPLICATE"
                        else f"mc_{_next_fixture_uuid().hex[:24]}"
                    )
                    extra_graph = _additional_dashboard_graph(
                        graph,
                        coverage_record_id=extra_coverage_record_id,
                        dashboard_output_ordinal=len(graphs),
                    )
                    _insert_dashboard_output_and_row(cursor, extra_graph)
                elif semantic in {
                    "APPEND_ONLY_ROW_UPDATE",
                    "APPEND_ONLY_ROW_DELETE",
                    "APPEND_ONLY_ROOT_LINK_UPDATE",
                    "APPEND_ONLY_ROOT_LINK_DELETE",
                }:
                    _insert_dashboard_batch(cursor, graphs)
                    connection.commit()
                    if rejected:
                        pre_projection = _tenant_deal_projection(
                            urls.application,
                            expected_application_role=urls.application_role,
                            tenant_id=tenant_id,
                            deal_id=deal_id,
                            projection_manifest=projection_manifest,
                        )
                    with connection.cursor() as cursor2:
                        _set_worker_lease(cursor2, scope)
                        if semantic == "APPEND_ONLY_ROW_UPDATE":
                            cursor2.execute(
                                """
                                UPDATE public.methodology_truth_dashboard_rows
                                   SET row_disposition = 'GAP'
                                 WHERE dashboard_row_id = %s
                                """,
                                (graph.dashboard_row_id,),
                            )
                            cursor2.execute(
                                "SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE"
                            )
                        elif semantic == "APPEND_ONLY_ROW_DELETE":
                            cursor2.execute(
                                """
                                DELETE FROM public.methodology_truth_dashboard_root_links
                                 WHERE dashboard_row_id = %s
                                """,
                                (graph.dashboard_row_id,),
                            )
                            cursor2.execute(
                                """
                                DELETE FROM public.methodology_truth_dashboard_rows
                                 WHERE dashboard_row_id = %s
                                """,
                                (graph.dashboard_row_id,),
                            )
                            cursor2.execute(
                                "SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE"
                            )
                        elif semantic == "APPEND_ONLY_ROOT_LINK_UPDATE":
                            cursor2.execute(
                                """
                                UPDATE public.methodology_truth_dashboard_root_links
                                   SET root_ordinal = root_ordinal + 1000
                                 WHERE dashboard_root_link_id = %s
                                """,
                                (graph.root_links[0][4],),
                            )
                            cursor2.execute(
                                "SET CONSTRAINTS ctrg_v3_dashboard_root_equation IMMEDIATE"
                            )
                        else:
                            cursor2.execute(
                                """
                                DELETE FROM public.methodology_truth_dashboard_root_links
                                 WHERE dashboard_root_link_id = %s
                                """,
                                (graph.root_links[0][4],),
                            )
                            cursor2.execute(
                                "SET CONSTRAINTS ctrg_v3_dashboard_root_equation IMMEDIATE"
                            )
                else:
                    _insert_dashboard_batch(cursor, graphs)
            elif case["authority_id"] == _AUTHORITY_IDS[1]:
                if graph is None:
                    _fail("TASK8_V24_CENSUS_FIXTURE_INVALID")
                include_row = semantic not in {
                    "ZERO_DASHBOARD_NATIVE_ROOTS",
                }
                _insert_dashboard_batch(
                    cursor,
                    graphs,
                    include_focus_row=include_row,
                    corrupt_focus_native_hashes=semantic == "DIRECT_SQL_ROOT_MISMATCH",
                )
                if semantic == "MULTIPLE_DASHBOARD_NATIVE_ROOTS":
                    cursor.execute(
                        """
                        INSERT INTO public.methodology_truth_dashboard_rows(
                            dashboard_row_id,tenant_id,deal_id,run_id,
                            run_output_object_id,lease_owner_id,
                            lease_fencing_token,coverage_record_id,
                            root_projection_sha256,row_disposition,
                            row_payload_sha256
                        ) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
                        """,
                        (
                            _next_fixture_uuid(),
                            scope.tenant_id,
                            scope.deal_id,
                            scope.run_id,
                            graph.dashboard_output_object_id,
                            scope.lease_owner_id,
                            graph.coverage_record_id,
                            graph.root_projection_sha256,
                            graph.row_disposition,
                            "a" * 64,
                        ),
                    )
            else:
                if request_id is None:
                    _fail("TASK8_V24_CENSUS_ERASURE_FIXTURE_INVALID")
                _call_erasure(cursor, tenant_id, deal_id, request_id)
        connection.commit()
    except Exception as exc:
        connection.rollback()
        observed_sqlstate = getattr(exc, "pgcode", None)
        observed_reason = str(exc)
        if case["expected"] == "ACCEPT":
            raise Task8V24CensusError(
                "TASK8_V24_CENSUS_LEGAL_CASE_REJECTED:"
                f"{case['case_id']}:{observed_sqlstate or 'UNKNOWN'}"
            ) from exc
    finally:
        connection.close()

    post_projection = (
        _tenant_deal_projection(
            urls.application,
            expected_application_role=urls.application_role,
            tenant_id=tenant_id,
            deal_id=deal_id,
            projection_manifest=projection_manifest,
        )
        if rejected
        else None
    )
    if case["expected"] == "ACCEPT":
        if observed_sqlstate is not None:
            _fail("TASK8_V24_CENSUS_UNEXPECTED_REJECTION")
        outcome = "ACCEPTED"
    else:
        expected_sqlstate = case.get("expected_sqlstate")
        if observed_sqlstate != expected_sqlstate:
            _fail("TASK8_V24_CENSUS_REJECTION_SQLSTATE_INVALID")
        reason_code = case.get("expected_reason_code")
        if reason_code and reason_code not in (observed_reason or ""):
            observed_code = (observed_reason or "UNKNOWN").splitlines()[0]
            _fail(f"TASK8_V24_CENSUS_REJECTION_REASON_INVALID:{case['case_id']}:{observed_code}")
        if pre_projection is None or post_projection is None:
            _fail("TASK8_V24_CENSUS_REJECTION_PROJECTION_MISSING")
        if pre_projection != post_projection:
            _fail("TASK8_V24_CENSUS_REJECTION_MUTATED")
        outcome = "REJECTED_ZERO_MUTATION"
    result: dict[str, Any] = {
        "case_id": case["case_id"],
        "case_kind": case["case_kind"],
        "execution_role": role,
        "outcome": outcome,
        "sqlstate": observed_sqlstate,
        "reason_code": case.get("expected_reason_code"),
        "expected_database_role": expected_database_role,
        "observed_current_user": observed_current_user,
        "observed_session_user": observed_session_user,
    }
    if rejected:
        result.update(
            {
                "pre_projection_sha256": pre_projection["projection_sha256"],
                "post_projection_sha256": post_projection["projection_sha256"],
                "projection_observer_role_tuple": [
                    pre_projection["observer_role"],
                    post_projection["observer_role"],
                ],
                "zero_mutation": pre_projection == post_projection,
            }
        )
    return result


def _catalog_projection(
    connection: Any,
    urls: _DisposableUrls,
    authority_execution_binding: Mapping[str, Any],
    expected_catalog: Mapping[str, Any],
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        _attest_connection_role_posture(cursor, urls.application_role)
        cursor.execute(
            """
            SELECT count(*)
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = 'public'
               AND relation.relkind = 'r'
               AND relation.relrowsecurity
               AND relation.relforcerowsecurity
            """
        )
        rls_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT relrowsecurity, relforcerowsecurity
              FROM pg_class
             WHERE oid = 'public.methodology_truth_dashboard_rows'::regclass
            """
        )
        enable_rls, force_rls = cursor.fetchone()
        cursor.execute(
            """
            SELECT rolname, rolsuper, rolbypassrls
              FROM pg_roles
             WHERE rolname IN (
                'idis_task7_guard_function_owner',
                'idis_erasure_function_owner',
                %s,
                %s
             )
            """,
            (urls.application_role, urls.erasure_role),
        )
        posture = {
            str(role): (bool(superuser), bool(bypassrls))
            for role, superuser, bypassrls in cursor.fetchall()
        }
        if set(posture) != {
            _GUARD_OWNER,
            _ERASURE_OWNER,
            urls.application_role,
            urls.erasure_role,
        }:
            _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
        cursor.execute(
            """
            SELECT role.rolname,
                   (SELECT count(*)
                      FROM pg_auth_members AS membership
                     WHERE membership.member=role.oid
                        OR membership.roleid=role.oid)
              FROM pg_roles AS role
             WHERE role.rolname IN (
                'idis_task7_guard_function_owner',
                'idis_erasure_function_owner',
                %s
             )
            """,
            (urls.erasure_role,),
        )
        memberships = {str(role): int(count) for role, count in cursor.fetchall()}
        cursor.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'methodology_truth_dashboard_rows'
               AND has_column_privilege(
                    'idis_task7_guard_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    column_name,
                    'SELECT'
               )
             ORDER BY column_name
            """
        )
        guard_owner_select = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT has_table_privilege(
                       'idis_task7_guard_function_owner',
                       'public.methodology_truth_dashboard_rows','INSERT'
                   ),
                   has_table_privilege(
                       'idis_task7_guard_function_owner',
                       'public.methodology_truth_dashboard_rows','UPDATE'
                   ),
                   has_table_privilege(
                       'idis_task7_guard_function_owner',
                       'public.methodology_truth_dashboard_rows','DELETE'
                   )
            """
        )
        guard_owner_insert, guard_owner_update, guard_owner_delete = map(
            bool,
            cursor.fetchone(),
        )
        guard_owner_write_count = sum((guard_owner_insert, guard_owner_update, guard_owner_delete))
        expected_parent_acl = expected_catalog.get("guard_owner_parent_select_acl_tuple")
        if not isinstance(expected_parent_acl, list):
            _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
        guard_owner_parent_select_acl: list[list[Any]] = []
        guard_owner_parent_write_count = 0
        for item in expected_parent_acl:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", item[0]) is None
                or not isinstance(item[1], list)
            ):
                _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
            relation = item[0]
            cursor.execute(
                """
                SELECT column_name::text
                  FROM information_schema.columns
                 WHERE table_schema='public' AND table_name=%s
                   AND has_column_privilege(
                       'idis_task7_guard_function_owner',
                       'public.' || %s,
                       column_name,
                       'SELECT'
                   )
                 ORDER BY column_name
                """,
                (relation, relation),
            )
            observed_columns = [str(row[0]) for row in cursor.fetchall()]
            guard_owner_parent_select_acl.append([relation, observed_columns])
            cursor.execute(
                """
                SELECT has_table_privilege(
                           'idis_task7_guard_function_owner',%s,'INSERT'
                       )::int
                     + has_table_privilege(
                           'idis_task7_guard_function_owner',%s,'UPDATE'
                       )::int
                     + has_table_privilege(
                           'idis_task7_guard_function_owner',%s,'DELETE'
                       )::int
                """,
                (f"public.{relation}",) * 3,
            )
            guard_owner_parent_write_count += int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'methodology_truth_dashboard_rows'
               AND has_column_privilege(
                    'idis_erasure_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    column_name,
                    'SELECT'
               )
             ORDER BY ordinal_position
            """
        )
        erasure_owner_select = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT
                has_table_privilege(
                    'idis_erasure_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    'INSERT'
                )::int
              + has_table_privilege(
                    'idis_erasure_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    'UPDATE'
                )::int
              + has_table_privilege(
                    'idis_erasure_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    'DELETE'
                )::int
            """
        )
        erasure_owner_write_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT
                has_table_privilege(
                    %s,
                    'public.methodology_truth_dashboard_rows',
                    'SELECT'
                )::int
              + has_table_privilege(
                    %s,
                    'public.methodology_truth_dashboard_rows',
                    'INSERT'
                )::int
              + has_table_privilege(
                    %s,
                    'public.methodology_truth_dashboard_rows',
                    'UPDATE'
                )::int
              + has_table_privilege(
                    %s,
                    'public.methodology_truth_dashboard_rows',
                    'DELETE'
                )::int
            """,
            (urls.erasure_role,) * 4,
        )
        executor_table_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT has_function_privilege(
                %s,
                'public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)',
                'EXECUTE'
            )::int
            """,
            (urls.application_role,),
        )
        app_execute_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT has_function_privilege(
                %s,
                'public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)',
                'EXECUTE'
            )::int,
            has_schema_privilege(%s, 'public', 'USAGE'),
            has_schema_privilege(%s, 'public', 'CREATE')
            """,
            (urls.erasure_role, urls.erasure_role, urls.erasure_role),
        )
        executor_execute_count, executor_schema_usage, executor_schema_create = cursor.fetchone()
        cursor.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE qual IS NOT NULL
                                     AND with_check IS NOT NULL
                                     AND qual=with_check)
              FROM pg_policies
             WHERE schemaname='public'
               AND tablename IN (
                    'methodology_truth_dashboard_rows',
                    'methodology_truth_dashboard_root_links'
               )
               AND permissive='PERMISSIVE'
               AND cmd='ALL'
               AND roles=ARRAY['public']::name[]
            """
        )
        dashboard_policy_count, policy_equal_count = map(int, cursor.fetchone())
        authority_rows = authority_execution_binding.get("authority_binding_tuple")
        if not isinstance(authority_rows, list):
            _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
        function_signatures = [
            guard["function_signature"]
            for row in authority_rows
            if isinstance(row, Mapping) and row.get("authority_id") in _AUTHORITY_IDS[:2]
            for guard in row.get("guard_function_acl_binding_tuple", ())
            if isinstance(guard, Mapping)
        ]
        if len(function_signatures) != len(set(function_signatures)) or not function_signatures:
            _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
        cursor.execute(
            """
            SELECT count(*) FILTER (WHERE pg_get_userbyid(proowner)=%s),
                   count(*) FILTER (WHERE prosecdef)
              FROM pg_proc
             WHERE oid=ANY(%s::regprocedure[])
            """,
            (_GUARD_OWNER, function_signatures),
        )
        guard_function_owner_count, guard_security_definer_count = map(int, cursor.fetchone())
        application_guard_execute_count = 0
        for signature in function_signatures:
            cursor.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')::int",
                (urls.application_role, signature),
            )
            application_guard_execute_count += int(cursor.fetchone()[0])
        trigger_guards: dict[str, Mapping[str, Any]] = {}
        for authority in authority_rows:
            if (
                not isinstance(authority, Mapping)
                or authority.get("authority_id") not in (_AUTHORITY_IDS[:2])
            ):
                continue
            guards = [
                guard
                for guard in authority.get("guard_function_acl_binding_tuple", ())
                if isinstance(guard, Mapping) and guard.get("function_kind") == "TRIGGER_GUARD"
            ]
            if len(guards) != 1:
                _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
            guard = guards[0]
            if guard.get("target_trigger_tuple") != authority.get("target_tuple"):
                _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
            trigger_guards[str(authority["authority_id"])] = guard
        if set(trigger_guards) != set(_AUTHORITY_IDS[:2]):
            _fail("TASK8_V24_CENSUS_CATALOG_INVALID")

        expected_pairs = {
            (str(target), str(trigger))
            for guard in trigger_guards.values()
            for target, trigger in guard["target_trigger_tuple"]
        }
        app_catalog_connection = _connect(urls.application)
        try:
            with app_catalog_connection.cursor() as catalog_cursor:
                catalog_current_user = _attest_connection_role_posture(
                    catalog_cursor,
                    urls.application_role,
                )
                catalog_session_user = catalog_current_user
                catalog_cursor.execute(
                    """
            SELECT relation.relname, trigger.tgname, trigger.tgtype,
                   trigger.tgenabled, trigger.tgconstraint<>0,
                   trigger.tgdeferrable, trigger.tginitdeferred,
                   function_namespace.nspname || '.' || function.proname || '(' ||
                   pg_catalog.replace(
                       pg_catalog.oidvectortypes(function.proargtypes), ', ', ','
                   ) || ')'
              FROM pg_trigger AS trigger
              JOIN pg_class AS relation ON relation.oid=trigger.tgrelid
              JOIN pg_namespace AS relation_namespace
                ON relation_namespace.oid=relation.relnamespace
              JOIN pg_proc AS function ON function.oid=trigger.tgfoid
              JOIN pg_namespace AS function_namespace
                ON function_namespace.oid=function.pronamespace
             WHERE NOT trigger.tgisinternal
               AND relation_namespace.nspname='public'
             ORDER BY relation.relname,trigger.tgname
                    """
                )
                raw_trigger_rows = catalog_cursor.fetchall()
                app_dml_reachability: dict[str, tuple[bool, bool, bool]] = {}
                for target in sorted({target for target, _trigger in expected_pairs}):
                    catalog_cursor.execute(
                        """
                        SELECT has_table_privilege(current_user,%s,'INSERT'),
                               has_table_privilege(current_user,%s,'UPDATE'),
                               has_table_privilege(current_user,%s,'DELETE')
                        """,
                        (f"public.{target}",) * 3,
                    )
                    app_dml_reachability[target] = tuple(map(bool, catalog_cursor.fetchone()))
            app_catalog_connection.rollback()
        finally:
            app_catalog_connection.close()
        trigger_rows = {
            (str(relation), str(trigger)): (
                int(mask),
                str(enabled),
                bool(is_constraint),
                bool(deferrable),
                bool(initially_deferred),
                str(function_signature),
            )
            for (
                relation,
                trigger,
                mask,
                enabled,
                is_constraint,
                deferrable,
                initially_deferred,
                function_signature,
            ) in raw_trigger_rows
            if (str(relation), str(trigger)) in expected_pairs
        }
        if set(trigger_rows) != expected_pairs:
            _fail("TASK8_V24_CENSUS_CATALOG_INVALID")

        observed_coordinates: dict[str, list[list[Any]]] = {}
        trigger_function_bindings: list[list[str]] = []
        partial_app_reachability: list[list[Any]] = []
        for authority_id, guard in trigger_guards.items():
            expected_function = str(guard["function_signature"])
            coordinates: list[list[Any]] = []
            for target, trigger in guard["target_trigger_tuple"]:
                mask, enabled, is_constraint, deferrable, initially_deferred, function = (
                    trigger_rows[(str(target), str(trigger))]
                )
                if function != expected_function:
                    _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
                trigger_function_bindings.append(
                    [authority_id, str(target), str(trigger), function]
                )
                if authority_id == _AUTHORITY_IDS[1]:
                    dml = app_dml_reachability[str(target)]
                    partial_app_reachability.append([str(target), str(trigger), *dml, function])
                actual_events = [
                    event
                    for bit, event in ((4, "INSERT"), (16, "UPDATE"), (8, "DELETE"))
                    if mask & bit
                ]
                if mask & (2 | 32 | 64) or not mask & 1:
                    _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
                coordinates.extend(
                    [
                        str(target),
                        str(trigger),
                        "AFTER",
                        event,
                        "ROW",
                        enabled,
                        is_constraint,
                        deferrable,
                        initially_deferred,
                    ]
                    for event in actual_events
                )
            expected_coordinates = guard.get("target_event_coordinate_tuple")
            if coordinates != expected_coordinates:
                _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
            observed_coordinates[authority_id] = coordinates
        dashboard_trigger_coordinates = observed_coordinates[_AUTHORITY_IDS[0]]
        partial_trigger_coordinates = observed_coordinates[_AUTHORITY_IDS[1]]
        dashboard_trigger_events = sorted(
            [[row[0], row[3]] for row in dashboard_trigger_coordinates]
        )
        partial_trigger_events = sorted([[row[0], row[3]] for row in partial_trigger_coordinates])
        trigger_function_bindings.sort()
        partial_app_reachability.sort()
        cursor.execute(
            """
            SELECT pg_get_userbyid(proowner)=%s, prosecdef
              FROM pg_proc
             WHERE oid='public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)'::regprocedure
            """,
            (_ERASURE_OWNER,),
        )
        erasure_runner_owner_valid, erasure_runner_security_definer = cursor.fetchone()
    return {
        "installed_tenant_rls_table_count": rls_count,
        "dashboard_enable_rls": bool(enable_rls),
        "dashboard_force_rls": bool(force_rls),
        "guard_owner_superuser": posture[_GUARD_OWNER][0],
        "guard_owner_bypassrls": posture[_GUARD_OWNER][1],
        "guard_owner_role_membership_count": memberships[_GUARD_OWNER],
        "erasure_owner_superuser": posture[_ERASURE_OWNER][0],
        "erasure_owner_bypassrls": posture[_ERASURE_OWNER][1],
        "erasure_owner_role_membership_count": memberships[_ERASURE_OWNER],
        "application_superuser": posture[urls.application_role][0],
        "application_bypassrls": posture[urls.application_role][1],
        "erasure_executor_superuser": posture[urls.erasure_role][0],
        "erasure_executor_bypassrls": posture[urls.erasure_role][1],
        "erasure_executor_role_membership_count": memberships[urls.erasure_role],
        "guard_owner_dashboard_select_columns": guard_owner_select,
        "guard_owner_dashboard_insert": guard_owner_insert,
        "guard_owner_dashboard_update": guard_owner_update,
        "guard_owner_dashboard_delete": guard_owner_delete,
        "guard_owner_dashboard_write_privilege_count": guard_owner_write_count,
        "erasure_owner_dashboard_select_columns": erasure_owner_select,
        "erasure_owner_dashboard_write_privilege_count": erasure_owner_write_count,
        "erasure_executor_dashboard_table_privilege_count": executor_table_count,
        "application_runner_execute_count": app_execute_count,
        "erasure_executor_runner_execute_count": int(executor_execute_count),
        "erasure_executor_schema_usage": bool(executor_schema_usage),
        "erasure_executor_schema_create": bool(executor_schema_create),
        "dashboard_policy_count": dashboard_policy_count,
        "dashboard_policy_using_with_check_equal_count": policy_equal_count,
        "dashboard_guard_function_owner_count": guard_function_owner_count,
        "dashboard_guard_security_definer_count": guard_security_definer_count,
        "application_guard_function_execute_count": application_guard_execute_count,
        "guard_owner_parent_select_acl_tuple": guard_owner_parent_select_acl,
        "guard_owner_parent_write_privilege_count": guard_owner_parent_write_count,
        "dashboard_trigger_event_tuple": dashboard_trigger_events,
        "partial_trigger_event_tuple": partial_trigger_events,
        "dashboard_trigger_event_coordinate_tuple": dashboard_trigger_coordinates,
        "partial_trigger_event_coordinate_tuple": partial_trigger_coordinates,
        "trigger_function_binding_tuple": trigger_function_bindings,
        "partial_target_application_dml_reachability_tuple": partial_app_reachability,
        "partial_target_application_dml_reachability_count": sum(
            all(row[2:5]) for row in partial_app_reachability
        ),
        "trigger_catalog_application_role_match": (
            catalog_current_user == urls.application_role
            and catalog_session_user == urls.application_role
        ),
        "erasure_runner_owner_valid": bool(erasure_runner_owner_valid),
        "erasure_runner_security_definer": bool(erasure_runner_security_definer),
    }


def _run_lock_races(
    urls: _DisposableUrls,
    lock_binding: Mapping[str, Any],
    dashboard_seed_plan: Mapping[str, Any],
    dashboard_coverage_row_count: int,
    dashboard_financial_coverage_row_count: int,
) -> tuple[bool, bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    prefix = lock_binding.get("prefix")
    seed = lock_binding.get("seed")
    if not isinstance(prefix, str) or not isinstance(seed, int):
        _fail("TASK8_V24_CENSUS_ADVISORY_LOCK_INVALID")

    forward_scope = _seed_scope(
        urls.admin,
        _next_fixture_uuid(),
        _next_fixture_uuid(),
        _next_fixture_uuid(),
    )
    forward_graphs = _seed_dashboard_batch(
        urls.admin,
        scope=forward_scope,
        dashboard_seed_plan=dashboard_seed_plan,
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=dashboard_coverage_row_count,
        financial_coverage_row_count=dashboard_financial_coverage_row_count,
    )
    forward_request_id = _seed_erasure_request(urls.admin, forward_scope)
    executor_connection = _connect(urls.erasure)
    application_connection = _connect(urls.application)
    forward_blocked = False
    forward_revalidated = False
    forward_revalidation_sqlstate: str | None = None
    forward_executor_role: str | None = None
    forward_application_role: str | None = None
    try:
        with executor_connection.cursor() as executor_cursor:
            forward_executor_role = _attest_connection_role_posture(
                executor_cursor,
                urls.erasure_role,
            )
            # This is the exact key expression decoded from both frozen bodies.
            executor_cursor.execute(
                """
                SELECT pg_catalog.pg_advisory_xact_lock(
                    pg_catalog.hashtextextended(%s || %s::text, %s)
                )
                """,
                (prefix, forward_scope.tenant_id, seed),
            )
        try:
            with application_connection.cursor() as app_cursor:
                forward_application_role = _attest_connection_role_posture(
                    app_cursor,
                    urls.application_role,
                )
                _set_worker_lease(app_cursor, forward_scope)
                app_cursor.execute("SET LOCAL lock_timeout = '250ms'")
                _insert_dashboard_batch(app_cursor, forward_graphs)
                app_cursor.execute("SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE")
        except Exception as exc:
            forward_blocked = getattr(exc, "pgcode", None) == "55P03"
            application_connection.rollback()

        with executor_connection.cursor() as executor_cursor:
            _call_erasure(
                executor_cursor,
                forward_scope.tenant_id,
                forward_scope.deal_id,
                forward_request_id,
            )
        executor_connection.commit()

        try:
            with application_connection.cursor() as app_cursor:
                _set_worker_lease(app_cursor, forward_scope)
                _insert_dashboard_batch(app_cursor, forward_graphs)
                app_cursor.execute("SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE")
            application_connection.commit()
        except Exception as exc:
            forward_revalidation_sqlstate = getattr(exc, "pgcode", None)
            forward_revalidated = forward_revalidation_sqlstate in {"23503", "40001"}
            application_connection.rollback()
        forward_passed = forward_blocked and forward_revalidated
    finally:
        executor_connection.close()
        application_connection.close()
    results.append(
        {
            "case_id": "hold-erasure-lock-then-dashboard-write",
            "blocked_sqlstate": "55P03" if forward_blocked else None,
            "terminal_revalidation_sqlstate": forward_revalidation_sqlstate,
            "observer_role_tuple": [
                forward_executor_role,
                forward_application_role,
            ],
            "passed": forward_passed,
        }
    )

    reverse_scope = _seed_scope(
        urls.admin,
        _next_fixture_uuid(),
        _next_fixture_uuid(),
        _next_fixture_uuid(),
    )
    reverse_graphs = _seed_dashboard_batch(
        urls.admin,
        scope=reverse_scope,
        dashboard_seed_plan=dashboard_seed_plan,
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=dashboard_coverage_row_count,
        financial_coverage_row_count=dashboard_financial_coverage_row_count,
    )
    reverse_request_id = _seed_erasure_request(urls.admin, reverse_scope)
    application_connection = _connect(urls.application)
    executor_connection = _connect(urls.erasure)
    reverse_blocked = False
    reverse_refused = False
    reverse_application_role: str | None = None
    reverse_executor_role: str | None = None
    try:
        with application_connection.cursor() as app_cursor:
            reverse_application_role = _attest_connection_role_posture(
                app_cursor,
                urls.application_role,
            )
            _set_worker_lease(app_cursor, reverse_scope)
            _insert_dashboard_batch(app_cursor, reverse_graphs)
            app_cursor.execute("SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE")
        try:
            with executor_connection.cursor() as executor_cursor:
                reverse_executor_role = _attest_connection_role_posture(
                    executor_cursor,
                    urls.erasure_role,
                )
                executor_cursor.execute("SET LOCAL lock_timeout = '250ms'")
                _call_erasure(
                    executor_cursor,
                    reverse_scope.tenant_id,
                    reverse_scope.deal_id,
                    reverse_request_id,
                )
        except Exception as exc:
            reverse_blocked = getattr(exc, "pgcode", None) == "55P03"
            executor_connection.rollback()
        application_connection.commit()
        try:
            with executor_connection.cursor() as executor_cursor:
                _call_erasure(
                    executor_cursor,
                    reverse_scope.tenant_id,
                    reverse_scope.deal_id,
                    reverse_request_id,
                )
        except Exception as exc:
            reverse_refused = getattr(
                exc, "pgcode", None
            ) == _REFUSAL_SQLSTATE and _REFUSAL_CODE in str(exc)
            executor_connection.rollback()
        reverse_passed = reverse_blocked and reverse_refused
    finally:
        application_connection.close()
        executor_connection.close()
    results.append(
        {
            "case_id": "hold-dashboard-lock-then-erasure",
            "blocked_sqlstate": "55P03" if reverse_blocked else None,
            "terminal_refusal_sqlstate": (_REFUSAL_SQLSTATE if reverse_refused else None),
            "terminal_refusal_reason_code": _REFUSAL_CODE if reverse_refused else None,
            "observer_role_tuple": [
                reverse_application_role,
                reverse_executor_role,
            ],
            "passed": reverse_passed,
        }
    )
    return forward_passed, reverse_passed, results


def _case_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        case
        for authority in manifest["authorities"]
        for family in ("legal_case_tuple", "illegal_case_tuple", "direct_sql_case_tuple")
        for case in authority[family]
    ]


def _authority_row(
    authority_execution_bundle: Mapping[str, Any],
    authority_id: str,
) -> Mapping[str, Any]:
    rows = authority_execution_bundle.get("sql_authorities")
    matches = [
        row
        for row in rows or ()
        if isinstance(row, Mapping) and row.get("authority_id") == authority_id
    ]
    if len(matches) != 1:
        _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
    return matches[0]


def _authority_install_statements(row: Mapping[str, Any]) -> tuple[str, ...]:
    statements = list(
        _decode_sql_tuple(
            row.get("body_sql_utf8_hex_tuple"),
            code="TASK8_V24_CENSUS_AUTHORITY_SQL_INVALID",
        )
    )
    guards = row.get("guard_function_acl_binding_tuple")
    if not isinstance(guards, list) or not guards:
        _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
    for guard in guards:
        if not isinstance(guard, Mapping):
            _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
        statements.extend(
            _decode_sql_tuple(
                [guard.get("owner_sql_utf8_hex")],
                code="TASK8_V24_CENSUS_GUARD_ACL_INVALID",
            )
        )
        statements.extend(
            _decode_sql_tuple(
                guard.get("function_acl_sql_utf8_hex_tuple"),
                code="TASK8_V24_CENSUS_GUARD_ACL_INVALID",
            )
        )
        select_sql = guard.get("guard_select_sql_tuple")
        if not isinstance(select_sql, list) or any(
            not isinstance(statement, str) for statement in select_sql
        ):
            _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
        statements.extend(select_sql)
    return tuple(statements)


def _erasure_install_statements(
    authority_execution_bundle: Mapping[str, Any],
) -> tuple[str, ...]:
    erasure = authority_execution_bundle.get("erasure_authority")
    if not isinstance(erasure, Mapping):
        _fail("TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID")
    return (
        *_decode_sql_tuple(
            erasure.get("current_body_sql_utf8_hex_tuple"),
            code="TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID",
        ),
        *_decode_sql_tuple(
            erasure.get("current_acl_sql_utf8_hex_tuple"),
            code="TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID",
        ),
    )


def _apply_admin_authority_ddl(admin_url: str, statements: tuple[str, ...]) -> None:
    """Apply reviewed authority bytes; this connection never observes evidence."""

    if not statements:
        _fail("TASK8_V24_CENSUS_AUTHORITY_SQL_INVALID")
    connection = _connect(admin_url)
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _function_catalog_projection(cursor: Any, signature: str) -> list[Any]:
    cursor.execute(
        """
        SELECT pg_catalog.pg_get_userbyid(proc.proowner),
               proc.prosecdef,
               COALESCE(
                   (SELECT pg_catalog.array_agg(acl_item::text ORDER BY acl_item::text)
                      FROM pg_catalog.unnest(proc.proacl) AS acl_item),
                   ARRAY[]::text[]
               ),
               pg_catalog.pg_get_functiondef(proc.oid)
          FROM pg_catalog.pg_proc AS proc
         WHERE proc.oid=%s::regprocedure
        """,
        (signature,),
    )
    row = cursor.fetchone()
    if row is None:
        _fail("TASK8_V24_CENSUS_AUTHORITY_CATALOG_INVALID")
    owner, security_definer, acl_tuple, definition = row
    return [
        signature,
        str(owner),
        bool(security_definer),
        list(acl_tuple or ()),
        hashlib.sha256(str(definition).encode("utf-8")).hexdigest(),
    ]


def _observe_authority_catalog(
    urls: _DisposableUrls,
    *,
    authority_id: str,
    authority_execution_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Observe one authority projection using only the application credential."""

    connection = _connect(urls.application)
    try:
        with connection.cursor() as cursor:
            current_user = _attest_connection_role_posture(
                cursor,
                urls.application_role,
            )

            if authority_id == _AUTHORITY_IDS[1]:
                row = _authority_row(authority_execution_bundle, authority_id)
                guards = row.get("guard_function_acl_binding_tuple")
                if not isinstance(guards, list):
                    _fail("TASK8_V24_CENSUS_GUARD_ACL_INVALID")
                signatures = sorted(
                    str(guard["function_signature"])
                    for guard in guards
                    if isinstance(guard, Mapping)
                )
                function_tuple = [
                    _function_catalog_projection(cursor, signature) for signature in signatures
                ]
                target_tuple = row.get("target_tuple")
                if not isinstance(target_tuple, list):
                    _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
                expected_targets = {(str(target), str(trigger)) for target, trigger in target_tuple}
                cursor.execute(
                    """
                    SELECT relation.relname, trigger.tgname, trigger.tgtype,
                           trigger.tgenabled, trigger.tgconstraint<>0,
                           trigger.tgdeferrable, trigger.tginitdeferred,
                           function_namespace.nspname || '.' || function.proname || '(' ||
                           pg_catalog.replace(
                               pg_catalog.oidvectortypes(function.proargtypes), ', ', ','
                           ) || ')'
                      FROM pg_catalog.pg_trigger AS trigger
                      JOIN pg_catalog.pg_class AS relation ON relation.oid=trigger.tgrelid
                      JOIN pg_catalog.pg_namespace AS relation_namespace
                        ON relation_namespace.oid=relation.relnamespace
                      JOIN pg_catalog.pg_proc AS function ON function.oid=trigger.tgfoid
                      JOIN pg_catalog.pg_namespace AS function_namespace
                        ON function_namespace.oid=function.pronamespace
                     WHERE NOT trigger.tgisinternal
                       AND relation_namespace.nspname='public'
                     ORDER BY relation.relname,trigger.tgname
                    """
                )
                trigger_tuple = [
                    [
                        str(relation),
                        str(trigger),
                        int(mask),
                        str(enabled),
                        bool(is_constraint),
                        bool(deferrable),
                        bool(initially_deferred),
                        str(function_signature),
                    ]
                    for (
                        relation,
                        trigger,
                        mask,
                        enabled,
                        is_constraint,
                        deferrable,
                        initially_deferred,
                        function_signature,
                    ) in cursor.fetchall()
                    if (str(relation), str(trigger)) in expected_targets
                ]
                application_dml_tuple: list[list[Any]] = []
                for target in sorted({target for target, _trigger in expected_targets}):
                    cursor.execute(
                        """
                        SELECT has_table_privilege(current_user,%s,'INSERT'),
                               has_table_privilege(current_user,%s,'UPDATE'),
                               has_table_privilege(current_user,%s,'DELETE')
                        """,
                        (f"public.{target}",) * 3,
                    )
                    application_dml_tuple.append([target, *map(bool, cursor.fetchone())])
                authority_projection: dict[str, Any] = {
                    "function_tuple": function_tuple,
                    "trigger_tuple": trigger_tuple,
                    "application_dml_tuple": application_dml_tuple,
                }
            elif authority_id == _AUTHORITY_IDS[2]:
                signature = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
                function_tuple = _function_catalog_projection(cursor, signature)
                cursor.execute(
                    """
                    SELECT has_function_privilege(%s,%s,'EXECUTE'),
                           has_function_privilege(%s,%s,'EXECUTE'),
                           has_schema_privilege(%s,'public','USAGE'),
                           has_schema_privilege(%s,'public','CREATE')
                    """,
                    (
                        urls.application_role,
                        signature,
                        urls.erasure_role,
                        signature,
                        urls.erasure_role,
                        urls.erasure_role,
                    ),
                )
                execute_acl = list(map(bool, cursor.fetchone()))
                column_acl_tuple: list[list[Any]] = []
                for relation, privilege, role in (
                    ("erasure_requests", "SELECT", _ERASURE_OWNER),
                    ("erasure_requests", "UPDATE", _ERASURE_OWNER),
                    ("methodology_truth_dashboard_rows", "SELECT", _ERASURE_OWNER),
                ):
                    cursor.execute(
                        """
                        SELECT column_name
                          FROM information_schema.columns
                         WHERE table_schema='public' AND table_name=%s
                           AND has_column_privilege(%s,'public.' || %s,column_name,%s)
                         ORDER BY ordinal_position
                        """,
                        (relation, role, relation, privilege),
                    )
                    column_acl_tuple.append(
                        [relation, privilege, role, [str(item[0]) for item in cursor.fetchall()]]
                    )
                authority_projection = {
                    "function_tuple": function_tuple,
                    "execute_acl_tuple": execute_acl,
                    "column_acl_tuple": column_acl_tuple,
                }
            else:
                _fail("TASK8_V24_CENSUS_AUTHORITY_BINDING_INVALID")
        connection.rollback()
    finally:
        connection.close()
    payload = {
        "schema_version": "task8-v24-app-observed-authority-catalog-v1",
        "authority_id": authority_id,
        "observer_role": current_user,
        "authority_projection": authority_projection,
    }
    return {
        **payload,
        "catalog_sha256": _canonical_sha256(
            ["task8-v24-app-observed-authority-catalog-v1", payload]
        ),
    }


def _execute_authority_restoration_cycles(
    urls: _DisposableUrls,
    *,
    artifact: Mapping[str, Any],
    authority_execution_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute exact U->D->U DDL while the app role observes every phase."""

    partial = _authority_row(authority_execution_bundle, _AUTHORITY_IDS[1])
    partial_down = _decode_sql_tuple(
        partial.get("downgrade_sql_utf8_hex_tuple"),
        code="TASK8_V24_CENSUS_AUTHORITY_SQL_INVALID",
    )
    partial_up = _authority_install_statements(partial)
    erasure_contract = artifact.get("task8_fdd_contract", {}).get("erasure_refusal_contract")
    if not isinstance(erasure_contract, Mapping):
        _fail("TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure_down = _decode_sql_tuple(
        erasure_contract.get("downgrade_sql_utf8_hex_tuple"),
        code="TASK8_V24_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    erasure_up = _erasure_install_statements(authority_execution_bundle)

    phase_receipts: dict[str, dict[str, Any]] = {}
    for label, authority_id, down_sql, up_sql in (
        ("partial", _AUTHORITY_IDS[1], partial_down, partial_up),
        ("erasure", _AUTHORITY_IDS[2], erasure_down, erasure_up),
    ):
        initial = _observe_authority_catalog(
            urls,
            authority_id=authority_id,
            authority_execution_bundle=authority_execution_bundle,
        )
        _apply_admin_authority_ddl(urls.admin, down_sql)
        downgrade = _observe_authority_catalog(
            urls,
            authority_id=authority_id,
            authority_execution_bundle=authority_execution_bundle,
        )
        _apply_admin_authority_ddl(urls.admin, up_sql)
        final = _observe_authority_catalog(
            urls,
            authority_id=authority_id,
            authority_execution_bundle=authority_execution_bundle,
        )
        initial_catalog_sha256 = initial["catalog_sha256"]
        downgrade_catalog_sha256 = downgrade["catalog_sha256"]
        final_catalog_sha256 = final["catalog_sha256"]
        passed = (
            initial_catalog_sha256 == final_catalog_sha256
            and initial_catalog_sha256 != downgrade_catalog_sha256
            and all(
                receipt["observer_role"] == urls.application_role
                for receipt in (initial, downgrade, final)
            )
        )
        if not passed:
            _fail("TASK8_V24_CENSUS_AUTHORITY_RESTORATION_INVALID")
        phase_receipts[label] = {
            "authority_id": authority_id,
            "passed": passed,
            "observer_role_tuple": [
                initial["observer_role"],
                downgrade["observer_role"],
                final["observer_role"],
            ],
            "initial_catalog_sha256": initial_catalog_sha256,
            "downgrade_catalog_sha256": downgrade_catalog_sha256,
            "final_catalog_sha256": final_catalog_sha256,
            "udu_sha256": _canonical_sha256(
                [
                    "task8-v24-app-observed-authority-udu-v1",
                    authority_id,
                    initial_catalog_sha256,
                    downgrade_catalog_sha256,
                    final_catalog_sha256,
                ]
            ),
        }
    return phase_receipts


def _run_database_execution(
    urls: _DisposableUrls,
    manifest: Mapping[str, Any],
    authority_execution_binding: Mapping[str, Any],
    authority_execution_bundle: Mapping[str, Any],
    artifact: Mapping[str, Any],
    dashboard_seed_plan: Mapping[str, Any],
    projection_manifest: Mapping[str, Any],
    lock_binding: Mapping[str, Any],
) -> dict[str, Any]:
    dashboard_coverage_row_count = manifest.get("dashboard_coverage_row_count")
    if dashboard_coverage_row_count != 100:
        _fail("TASK8_V24_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    dashboard_financial_coverage_row_count = manifest.get("dashboard_financial_coverage_row_count")
    if dashboard_financial_coverage_row_count != 79:
        _fail("TASK8_V24_CENSUS_FINANCIAL_COVERAGE_ROW_COUNT_INVALID")
    case_results = [
        _execute_behavior_case(
            case,
            urls=urls,
            projection_manifest=projection_manifest,
            dashboard_seed_plan=dashboard_seed_plan,
            dashboard_coverage_row_count=dashboard_coverage_row_count,
            dashboard_financial_coverage_row_count=(dashboard_financial_coverage_row_count),
        )
        for case in _case_rows(manifest)
    ]
    rls_behavior_passed, rls_behavior_results = _run_rls_behavior_matrix(
        urls,
        projection_manifest,
        dashboard_seed_plan,
        dashboard_coverage_row_count,
        dashboard_financial_coverage_row_count,
    )
    catalog_connection = _connect(urls.application)
    try:
        with catalog_connection.cursor() as role_cursor:
            catalog_observer_role = _attest_connection_role_posture(
                role_cursor,
                urls.application_role,
            )
        catalog = _catalog_projection(
            catalog_connection,
            urls,
            authority_execution_binding,
            manifest["catalog_assertions"],
        )
    finally:
        catalog_connection.close()
    forward_passed, reverse_passed, race_results = _run_lock_races(
        urls,
        lock_binding,
        dashboard_seed_plan,
        dashboard_coverage_row_count,
        dashboard_financial_coverage_row_count,
    )
    restoration = _execute_authority_restoration_cycles(
        urls,
        artifact=artifact,
        authority_execution_bundle=authority_execution_bundle,
    )
    counts = Counter(result["case_kind"] for result in case_results)
    unexpected = sum(
        result["outcome"]
        not in {
            "ACCEPTED",
            "REJECTED_ZERO_MUTATION",
        }
        for result in case_results
    )
    rejected_results = [
        result for result in case_results if result["case_kind"] in {"ILLEGAL", "DIRECT_SQL"}
    ]
    rejected_zero_mutation_count = sum(
        result.get("zero_mutation") is True for result in rejected_results
    )
    rejected_projection_hash = _canonical_sha256(
        [
            "task8-v24-rejected-case-projections-v1",
            [
                [
                    result["case_id"],
                    result.get("pre_projection_sha256"),
                    result.get("post_projection_sha256"),
                    result.get("zero_mutation"),
                ]
                for result in rejected_results
            ],
        ]
    )
    dashboard_refusal_results = [
        result
        for result in rejected_results
        if result["case_id"]
        in {
            "erasure-dashboard-refusal",
            "erasure-executor-direct-refusal",
        }
    ]
    dashboard_refusal_zero_mutation = bool(dashboard_refusal_results) and all(
        result.get("zero_mutation") is True
        and result.get("sqlstate") == _REFUSAL_SQLSTATE
        and result.get("reason_code") == _REFUSAL_CODE
        for result in dashboard_refusal_results
    )
    case_hash = _canonical_sha256(case_results)
    behavior_role_rows = [
        [
            result["case_id"],
            result["execution_role"],
            result["expected_database_role"],
            result["observed_current_user"],
            result["observed_session_user"],
        ]
        for result in case_results
    ]
    behavior_role_match_count = sum(
        expected_role == observed_current == observed_session
        for (
            _case_id,
            _execution_role,
            expected_role,
            observed_current,
            observed_session,
        ) in behavior_role_rows
    )
    catalog_hash = _canonical_sha256(catalog)
    race_hash = _canonical_sha256(race_results)
    rls_behavior_hash = _canonical_sha256(
        ["task8-v24-rls-behavior-matrix-v1", rls_behavior_results]
    )
    observation_role_rows: list[list[str]] = [
        ["catalog", urls.application_role, catalog_observer_role],
        *[
            [
                f"behavior-projection:{result['case_id']}:{ordinal}",
                urls.application_role,
                str(role),
            ]
            for result in rejected_results
            for ordinal, role in enumerate(
                result.get("projection_observer_role_tuple", ()), start=1
            )
        ],
        *[
            [
                f"rls:{row['mode']}:{row['relation']}:{row['operation']}",
                urls.application_role,
                str(row.get("observer_role")),
            ]
            for row in rls_behavior_results
        ],
        *[
            [
                f"authority:{label}:{ordinal}",
                urls.application_role,
                str(role),
            ]
            for label in ("partial", "erasure")
            for ordinal, role in enumerate(restoration[label]["observer_role_tuple"], start=1)
        ],
        *[
            [
                f"race:{result['case_id']}:{ordinal}",
                expected_role,
                str(role),
            ]
            for result in race_results
            for ordinal, (expected_role, role) in enumerate(
                zip(
                    (
                        (urls.erasure_role, urls.application_role)
                        if result["case_id"] == "hold-erasure-lock-then-dashboard-write"
                        else (urls.application_role, urls.erasure_role)
                    ),
                    result.get("observer_role_tuple", ()),
                    strict=True,
                ),
                start=1,
            )
        ],
    ]
    admin_role = _role_from_url(urls.admin)
    admin_observation_case_count = sum(
        role == admin_role for _coordinate, _expected, role in observation_role_rows
    )
    catalog_observer_application_role = all(
        observed == expected == urls.application_role
        for _coordinate, expected, observed in observation_role_rows
        if expected == urls.application_role
    )
    execution = {
        "admin_behavior_case_count": sum(
            result["execution_role"] == "admin" for result in case_results
        ),
        "admin_observation_case_count": admin_observation_case_count,
        "application_case_execution_count": sum(
            result["execution_role"] == "application" for result in case_results
        ),
        "erasure_executor_case_execution_count": sum(
            result["execution_role"] == "erasure_executor" for result in case_results
        ),
        "legal_execution_count": counts["LEGAL"],
        "illegal_rejection_count": counts["ILLEGAL"],
        "direct_sql_rejection_count": counts["DIRECT_SQL"],
        "rejected_case_projection_count": len(rejected_results),
        "rejected_case_zero_mutation_count": rejected_zero_mutation_count,
        "rejected_case_projection_sha256": rejected_projection_hash,
        "unexpected_outcome_count": unexpected,
        "behavior_role_match_count": behavior_role_match_count,
        "behavior_role_attestation_sha256": _canonical_sha256(
            ["task8-v24-behavior-role-attestation-v1", behavior_role_rows]
        ),
        "dashboard_coverage_row_count": dashboard_coverage_row_count,
        "dashboard_commercial_coverage_row_count": (
            dashboard_coverage_row_count - dashboard_financial_coverage_row_count
        ),
        "dashboard_financial_coverage_row_count": dashboard_financial_coverage_row_count,
        "dashboard_refusal_sqlstate": _REFUSAL_SQLSTATE,
        "dashboard_refusal_reason_code": _REFUSAL_CODE,
        "dashboard_refusal_zero_mutation": dashboard_refusal_zero_mutation,
        "forward_lock_race_passed": forward_passed,
        "reverse_lock_race_passed": reverse_passed,
        "rls_behavior_passed": rls_behavior_passed,
        "rls_behavior_sha256": rls_behavior_hash,
        "installed_tenant_rls_table_count": catalog["installed_tenant_rls_table_count"],
        "catalog_observer_application_role": catalog_observer_application_role,
        "partial_authority_target_count": 19,
        "partial_authority_event_count": 57,
        "guard_owner_dashboard_select_columns": catalog["guard_owner_dashboard_select_columns"],
        "guard_owner_dashboard_write_privilege_count": catalog[
            "guard_owner_dashboard_write_privilege_count"
        ],
        "erasure_owner_dashboard_select_columns": catalog["erasure_owner_dashboard_select_columns"],
        "erasure_executor_dashboard_table_privilege_count": catalog[
            "erasure_executor_dashboard_table_privilege_count"
        ],
        "application_runner_execute_count": catalog["application_runner_execute_count"],
        "catalog_assertion_receipt": catalog,
        "authority_execution_binding": dict(authority_execution_binding),
        "advisory_lock_binding": dict(lock_binding),
        "case_result_sha256": case_hash,
        "catalog_projection_sha256": catalog_hash,
        "race_result_sha256": race_hash,
        "partial_authority_udu_passed": restoration["partial"]["passed"],
        "erasure_authority_udu_passed": restoration["erasure"]["passed"],
        "partial_authority_udu_sha256": restoration["partial"]["udu_sha256"],
        "erasure_authority_udu_sha256": restoration["erasure"]["udu_sha256"],
        "partial_initial_catalog_sha256": restoration["partial"]["initial_catalog_sha256"],
        "partial_downgrade_catalog_sha256": restoration["partial"]["downgrade_catalog_sha256"],
        "partial_final_catalog_sha256": restoration["partial"]["final_catalog_sha256"],
        "erasure_initial_catalog_sha256": restoration["erasure"]["initial_catalog_sha256"],
        "erasure_downgrade_catalog_sha256": restoration["erasure"]["downgrade_catalog_sha256"],
        "erasure_final_catalog_sha256": restoration["erasure"]["final_catalog_sha256"],
        "complete_execution_sha256": complete_execution_sha256(
            case_hash,
            catalog_hash,
            race_hash,
            rls_behavior_hash,
            restoration["partial"]["udu_sha256"],
            restoration["erasure"]["udu_sha256"],
        ),
    }
    expected_catalog = manifest["catalog_assertions"]
    for field, observed in catalog.items():
        if field not in expected_catalog or observed != expected_catalog[field]:
            _fail("TASK8_V24_CENSUS_CATALOG_INVALID")
    if not forward_passed or not reverse_passed:
        _fail("TASK8_V24_CENSUS_LOCK_RACE_INVALID")
    if not rls_behavior_passed:
        _fail("TASK8_V24_CENSUS_RLS_BEHAVIOR_INVALID")
    if behavior_role_match_count != len(case_results):
        _fail("TASK8_V24_CENSUS_BEHAVIOR_ROLE_INVALID")
    if rejected_zero_mutation_count != len(rejected_results) or not dashboard_refusal_zero_mutation:
        _fail("TASK8_V24_CENSUS_REJECTION_MUTATED")
    return execution


def run_disposable_postgres_census(
    admin_database_url: str,
    application_database_url: str,
    erasure_database_url: str,
    artifact_raw: bytes,
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
) -> dict[str, Any]:
    _reset_fixture_uuid_sequence()
    try:
        artifact = json.loads(artifact_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Task8V24CensusError("TASK8_V24_CENSUS_ARTIFACT_INVALID") from exc
    if not isinstance(artifact, dict):
        _fail("TASK8_V24_CENSUS_ARTIFACT_INVALID")
    _validate_v24(artifact)
    if not isinstance(policy_artifact_raw, bytes) or not isinstance(workbook_raw, bytes):
        _fail("TASK8_V24_CENSUS_POLICY_INVALID")
    dashboard_seed_plan = build_v24_dashboard_seed_plan(
        artifact,
        policy_artifact_raw,
        workbook_raw=workbook_raw,
        tenant_id="11111111-1111-4111-8111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        run_id="33333333-3333-4333-8333-333333333333",
    )
    manifest = build_census_case_manifest(artifact)
    validate_census_case_manifest(manifest, artifact)
    urls = _disposable_urls(
        admin_database_url,
        application_database_url,
        erasure_database_url,
    )
    created = False
    dropped = False
    execution: dict[str, Any]
    try:
        _create_disposable_database(admin_database_url, urls.database_name)
        created = True
        authority_execution = build_hash_bound_authority_execution(artifact)
        authority_execution_binding = _setup_disposable_schema(
            urls.admin,
            application_role=urls.application_role,
            erasure_role=urls.erasure_role,
            v24=artifact,
            authority_execution=authority_execution,
        )
        execution = _run_database_execution(
            urls,
            manifest,
            authority_execution_binding=authority_execution_binding,
            authority_execution_bundle=authority_execution,
            artifact=artifact,
            dashboard_seed_plan=dashboard_seed_plan,
            projection_manifest=build_zero_mutation_projection_manifest(artifact),
            lock_binding=build_advisory_lock_binding(artifact),
        )
    finally:
        if created:
            _drop_disposable_database(admin_database_url, urls.database_name)
            dropped = True
    receipt = {
        "schema_version": _RECEIPT_SCHEMA,
        "result": "PASS",
        "pipeline_contract_version": "full-strict-v24",
        "artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "manifest_sha256": _canonical_sha256(manifest),
        "database_execution": execution,
        "disposable_database_dropped": dropped,
    }
    validate_census_receipt(receipt, artifact)
    return receipt


def validate_census_receipt(
    receipt: Mapping[str, Any],
    v24: Mapping[str, Any],
) -> None:
    _validate_v24(v24)
    if tuple(receipt) != (
        "schema_version",
        "result",
        "pipeline_contract_version",
        "artifact_sha256",
        "manifest_sha256",
        "database_execution",
        "disposable_database_dropped",
    ):
        _fail("TASK8_V24_CENSUS_RECEIPT_INVALID")
    if (
        receipt["schema_version"] != _RECEIPT_SCHEMA
        or receipt["result"] != "PASS"
        or receipt["pipeline_contract_version"] != "full-strict-v24"
        or receipt["disposable_database_dropped"] is not True
    ):
        _fail("TASK8_V24_CENSUS_RECEIPT_INVALID")
    manifest = build_census_case_manifest(v24)
    if receipt["manifest_sha256"] != _canonical_sha256(manifest):
        _fail("TASK8_V24_CENSUS_RECEIPT_INVALID")
    canonical_artifact_raw = (
        json.dumps(
            dict(v24),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    if receipt["artifact_sha256"] != hashlib.sha256(canonical_artifact_raw).hexdigest():
        _fail("TASK8_V24_CENSUS_RECEIPT_INVALID")
    execution = receipt.get("database_execution")
    if not isinstance(execution, dict):
        _fail("TASK8_V24_CENSUS_RECEIPT_INVALID")
    cases = _case_rows(manifest)
    counts = Counter(case["case_kind"] for case in cases)
    rejected_count = counts["ILLEGAL"] + counts["DIRECT_SQL"]
    expected_catalog = {
        key: value
        for key, value in manifest["catalog_assertions"].items()
        if key
        not in {
            "current_erasure_relation_count",
            "future_erasure_relation_count",
            "complete_erasure_relation_count",
        }
    }

    class _ReceiptCursor:
        def execute(self, _statement: str) -> None:
            return None

    expected_authority_binding = execute_hash_bound_authority_sql(
        _ReceiptCursor(),
        build_hash_bound_authority_execution(v24),
    )
    if (
        execution.get("admin_behavior_case_count") != 0
        or execution.get("admin_observation_case_count") != 0
        or execution.get("application_case_execution_count") != 22
        or execution.get("erasure_executor_case_execution_count") != 3
        or execution.get("legal_execution_count") != counts["LEGAL"]
        or execution.get("illegal_rejection_count") != counts["ILLEGAL"]
        or execution.get("direct_sql_rejection_count") != counts["DIRECT_SQL"]
        or execution.get("rejected_case_projection_count") != rejected_count
        or execution.get("rejected_case_zero_mutation_count") != rejected_count
        or execution.get("unexpected_outcome_count") != 0
        or execution.get("behavior_role_match_count") != len(cases)
        or execution.get("dashboard_coverage_row_count") != 100
        or execution.get("dashboard_commercial_coverage_row_count") != 21
        or execution.get("dashboard_financial_coverage_row_count") != 79
        or execution.get("dashboard_refusal_sqlstate") != _REFUSAL_SQLSTATE
        or execution.get("dashboard_refusal_reason_code") != _REFUSAL_CODE
        or execution.get("dashboard_refusal_zero_mutation") is not True
        or execution.get("forward_lock_race_passed") is not True
        or execution.get("reverse_lock_race_passed") is not True
        or execution.get("rls_behavior_passed") is not True
        or execution.get("installed_tenant_rls_table_count") != 69
        or execution.get("catalog_observer_application_role") is not True
        or execution.get("partial_authority_target_count") != 19
        or execution.get("partial_authority_event_count") != 57
        or execution.get("partial_authority_udu_passed") is not True
        or execution.get("erasure_authority_udu_passed") is not True
        or execution.get("partial_initial_catalog_sha256")
        != execution.get("partial_final_catalog_sha256")
        or execution.get("partial_initial_catalog_sha256")
        == execution.get("partial_downgrade_catalog_sha256")
        or execution.get("erasure_initial_catalog_sha256")
        != execution.get("erasure_final_catalog_sha256")
        or execution.get("erasure_initial_catalog_sha256")
        == execution.get("erasure_downgrade_catalog_sha256")
        or execution.get("guard_owner_dashboard_select_columns")
        != manifest["catalog_assertions"]["guard_owner_dashboard_select_columns"]
        or execution.get("guard_owner_dashboard_write_privilege_count") != 0
        or execution.get("erasure_executor_dashboard_table_privilege_count") != 0
        or execution.get("application_runner_execute_count") != 0
        or execution.get("catalog_assertion_receipt") != expected_catalog
        or execution.get("authority_execution_binding") != expected_authority_binding
        or execution.get("advisory_lock_binding") != build_advisory_lock_binding(v24)
    ):
        _fail("TASK8_V24_CENSUS_RECEIPT_INVALID")
    for field in (
        "rejected_case_projection_sha256",
        "behavior_role_attestation_sha256",
        "rls_behavior_sha256",
        "case_result_sha256",
        "catalog_projection_sha256",
        "race_result_sha256",
        "partial_authority_udu_sha256",
        "erasure_authority_udu_sha256",
        "partial_initial_catalog_sha256",
        "partial_downgrade_catalog_sha256",
        "partial_final_catalog_sha256",
        "erasure_initial_catalog_sha256",
        "erasure_downgrade_catalog_sha256",
        "erasure_final_catalog_sha256",
        "complete_execution_sha256",
    ):
        if not isinstance(execution.get(field), str) or not _SHA256_RE.fullmatch(execution[field]):
            _fail("TASK8_V24_CENSUS_RECEIPT_INVALID")
    expected_complete_execution_sha256 = complete_execution_sha256(
        execution["case_result_sha256"],
        execution["catalog_projection_sha256"],
        execution["race_result_sha256"],
        execution["rls_behavior_sha256"],
        execution["partial_authority_udu_sha256"],
        execution["erasure_authority_udu_sha256"],
    )
    if execution["complete_execution_sha256"] != expected_complete_execution_sha256:
        _fail("TASK8_V24_CENSUS_RECEIPT_INVALID")
