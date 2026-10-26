"""RED-first v17 receipt-authority composition contracts."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V16_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V17_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_RECEIPT_HELPER = _REPO_ROOT / "scripts" / "task7_v17_receipt_guard_sql.py"
_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v17_preimage.py"
_RECEIPT_BODY_BYTES = 18_212
_RECEIPT_BODY_SHA256 = "eb1c87584e024e2a609bc518b134c067d76565c2ec714071737efe9c2a44721a"
_RECEIPT_ROW_SHA256 = "75479db1fe362d8c62f0aafbff1cfeba459a082304c03834dbbe74372a50a7d7"
_EXACT_ACL_COLUMN_DELTA = {
    "source_lineage_objects": {
        "object_identity_sha256",
        "source_disposition",
        "source_version",
        "write_origin",
    },
    "data_room_packages": {"metadata"},
    "data_room_package_files": {
        "doc_id",
        "document_id",
        "error_codes",
        "extension",
        "file_entry_id",
        "file_status",
        "package_id",
        "parse_status",
        "path_hash",
        "reason_codes",
        "sequence",
        "sha256",
        "storage_uri",
        "support_status",
        "triage_status",
    },
    "document_artifacts": {
        "doc_id",
        "doc_type",
        "ingested_at",
        "metadata",
        "sha256",
        "source_system",
        "title",
        "uri",
        "version_id",
    },
    "documents": {"doc_id", "doc_type", "document_id", "metadata", "parse_status"},
    "document_parse_attempts": {
        "attempt_ordinal",
        "document_id",
        "finished_at",
        "input_sha256",
        "parse_attempt_id",
        "parser_name",
        "parser_version",
        "receipt_sha256",
        "started_at",
        "status",
        "write_origin",
    },
    "document_spans": {
        "content_hash",
        "document_id",
        "locator",
        "parse_attempt_id",
        "span_id",
        "span_type",
    },
    "runs": {"pipeline_resume_state"},
    "run_step_outputs": {
        "output_kind",
        "output_ordinal",
        "semantic_rule_hash",
        "semantic_rule_version",
        "step_name",
    },
    "run_consumer_receipts": {"consumer_output_object_id"},
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _receipt_body(artifact: dict[str, Any]) -> str:
    row = artifact["sql_authority_registry"]["rows"][19]
    assert row["authority_id"] == "current-receipt-equality-v1"
    return bytes.fromhex(row["body_sql_utf8_hex_tuple"][2]).decode("utf-8")


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def test_v17_receipt_authority_embeds_every_reviewed_aggregate_and_subrole() -> None:
    """The artifact, not a unit-only helper, must bind the full receipt registry."""

    artifact = _load_json(_V17_ARTIFACT)
    helper = _load_module(_RECEIPT_HELPER, "_task7_v17_receipt_helper_red")
    rows = helper.expected_receipt_rows(artifact["semantic_step_registry"]["rows"])

    assert len(rows) == 83
    assert Counter(row[0] for row in rows) == {
        "DATA_ROOM_INVENTORY_PACKAGE": 1,
        "DATA_ROOM_INGESTION_HANDOFF": 1,
        "INGEST_CHECK": 1,
        "DOCUMENT_PREFLIGHT": 1,
        "METHODOLOGY_COVERAGE_INIT": 1,
        "METHODOLOGY_EXTRACTION_TASK_PLANNING": 1,
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION": 1,
        "METHODOLOGY_CLAIM_MATERIALIZATION": 1,
        "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION": 1,
        "METHODOLOGY_SANAD_CREATION_LINKING_GRADING": 1,
        "METHODOLOGY_DETERMINISTIC_CALCULATION": 1,
        "METHODOLOGY_TRUTH_DASHBOARD": 1,
        "EXTRACT": 2,
        "GRADE": 1,
        "CALC": 1,
        "METHODOLOGY_COMPANY_IDENTITY_PACKAGE": 1,
        "ENRICHMENT": 19,
        "GRAPH_EVIDENCE": 3,
        "RAG_EVIDENCE": 3,
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN": 1,
        "METHODOLOGY_EVIDENCE_TRUST_COURT": 1,
        "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE": 1,
        "DEBATE": 6,
        "ANALYSIS": 9,
        "SCORING": 1,
        "METHODOLOGY_LAYER2_READINESS_PACKAGE": 1,
        "LAYER2_IC_CHALLENGE": 4,
        "DELIVERABLES": 17,
    }

    body = _receipt_body(artifact)
    for row in rows:
        rendered = (
            "("
            + ",".join(
                (
                    _sql_literal(row[0]),
                    _sql_literal(row[1]),
                    _sql_literal(row[2]),
                    str(row[3]),
                    _sql_literal(row[4]),
                    _sql_literal(row[5]),
                )
            )
            + ")"
        )
        assert rendered in body, row
        if row[1] != "__step__":
            route_kinds = {
                route[6]
                for route in artifact["usage_route_registry"]["rows"]
                if route[3] == row[0] and route[4] == row[1]
            }
            assert route_kinds == {row[2]}, row
    assert "FULL JOIN actual" in body
    assert "required.step_id IS NULL OR actual.step_id IS NULL" in body


def test_v17_receipt_authority_rejects_native_consumer_roots_and_is_materialized() -> None:
    """The composed candidate must equal the helper-derived row-19 authority."""

    v16 = _load_json(_V16_ARTIFACT)
    v17 = _load_json(_V17_ARTIFACT)
    helper = _load_module(_RECEIPT_HELPER, "_task7_v17_receipt_helper_materialized_red")
    materializer = _load_module(_MATERIALIZER, "_task7_v17_receipt_materializer_red")

    expected = helper.build_receipt_equality_statements(
        v16["sql_authority_registry"]["rows"][19],
        v16["semantic_step_registry"]["rows"],
    )
    actual = tuple(
        bytes.fromhex(value).decode("utf-8")
        for value in v17["sql_authority_registry"]["rows"][19]["body_sql_utf8_hex_tuple"]
    )

    assert actual == expected
    assert "consumer_root.output_class<>'STEP_RECEIPT'" in actual[2]
    assert (
        "JOIN public.runs actual_run ON "
        "actual_run.tenant_id=so.tenant_id "
        "AND actual_run.deal_id=so.deal_id "
        "AND actual_run.run_id=so.run_id "
        "AND actual_run.pipeline_contract_version='full-strict-v17' "
        "AND actual_run.pipeline_resume_state='RESUMABLE'"
    ) in actual[2]
    assert (
        "AND r.pipeline_contract_version='full-strict-v17' "
        "AND r.pipeline_resume_state='RESUMABLE' "
        "AND s.status='COMPLETED'"
    ) in actual[2]
    assert actual[2].count("pipeline_resume_state='RESUMABLE'") == 3
    assert v17["sql_authority_registry"]["rows"][19]["declared_parent_relation_tuple"] == [
        "runs",
        "run_steps",
        "run_step_outputs",
        "run_output_objects",
        "run_expected_consumer_edges",
        "run_consumer_receipts",
        "deliverable_manifest_references",
        "deliverables",
    ]
    rebuilt = materializer.build_v17(v16)
    assert (
        rebuilt["sql_authority_registry"]["rows"][19] == (v17["sql_authority_registry"]["rows"][19])
    )


def test_v17_receipt_row_and_acl_delta_are_exact_bytes_not_wholesale_ordinal() -> None:
    v16 = _load_json(_V16_ARTIFACT)
    v17 = _load_json(_V17_ARTIFACT)
    materializer = _load_module(_MATERIALIZER, "_task7_v17_receipt_delta_red")
    row = v17["sql_authority_registry"]["rows"][19]
    body = bytes.fromhex(row["body_sql_utf8_hex_tuple"][2])

    assert len(body) == _RECEIPT_BODY_BYTES
    import hashlib

    assert hashlib.sha256(body).hexdigest() == _RECEIPT_BODY_SHA256
    assert row["expected_sha256"] == _RECEIPT_ROW_SHA256

    def columns(artifact: dict[str, Any]) -> dict[str, set[str]]:
        values = artifact["sql_authority_registry"]["guard_function_acl_registry"][
            "guard_owner_role"
        ]["select_relation_column_tuple"]
        return {relation: set(relation_columns) for relation, relation_columns in values}

    before = columns(v16)
    after = columns(v17)
    assert {
        relation: after[relation] - before[relation]
        for relation in after
        if after[relation] != before[relation]
    } == _EXACT_ACL_COLUMN_DELTA
    assert materializer._validate_receipt_authority_delta(v16, v17) is None

    tampered = copy.deepcopy(v17)
    encoded = tampered["sql_authority_registry"]["rows"][19]["body_sql_utf8_hex_tuple"][2]
    statement = bytes.fromhex(encoded).decode("utf-8")
    tampered["sql_authority_registry"]["rows"][19]["body_sql_utf8_hex_tuple"][2] = (
        statement.replace("DEBATE_ROLE_RECEIPT_V1", "DEBATE_ROLE_RECEIPT_V2", 1)
        .encode("utf-8")
        .hex()
    )
    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_V17_RECEIPT_AUTHORITY_DELTA_INVALID",
    ):
        materializer._validate_receipt_authority_delta(v16, tampered)


def test_v17_receipt_derivation_rejects_a_missing_subrole_field_fail_closed() -> None:
    artifact = _load_json(_V16_ARTIFACT)
    helper = _load_module(_RECEIPT_HELPER, "_task7_v17_receipt_helper_shape_red")
    rows = copy.deepcopy(artifact["semantic_step_registry"]["rows"])
    rows[0][4] = [field for field in rows[0][4] if field[0] != "subroles"]

    with pytest.raises(
        helper.ReceiptGuardSqlError,
        match="TASK7_V17_SEMANTIC_ROW_INVALID",
    ):
        helper.expected_receipt_rows(rows)
