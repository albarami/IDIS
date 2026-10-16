"""Hermetically validate and derive the full-strict-v11 contract candidate.

The artifact is the normative contract byte source.  The pinned target-schema
catalog below is validation authority only: a disposable PostgreSQL proof
compares it byte-for-byte with migration 0037 plus the artifact scaffold.
Neither environment state nor runtime product code participates in derivation.

This module intentionally does not publish a fixture.  V11 remains unfrozen
until the separately reviewed executable PostgreSQL census is complete.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Never, cast


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"TASK7_V11_HELPER_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SCRIPT_DIR = Path(__file__).resolve().parent
_V10 = _load_script(
    _SCRIPT_DIR / "generate_task7_v10_contract_fixture.py",
    "_task7_v10_fixture_helpers_for_v11",
)
_MATERIALIZER = _load_script(
    _SCRIPT_DIR / "materialize_full_strict_v11_preimage.py",
    "_task7_v11_materializer_helpers",
)

ContractArtifactError = _V10.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V10._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V10._COMPONENT_ORDER)
_EXPECTED_INVARIANTS = cast(dict[str, int], _V10._EXPECTED_INVARIANTS)
_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_ERASURE_ROW_HASH_DOMAIN = b"full-strict-v4-erasure-execution-row-sha256-v1\x00"
_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"
_ERASURE_REGISTRY_HASH_DOMAIN = "full-strict-v4-erasure-execution-registry-v1"
_CURRENT_AUTHORITY_START = 17

# Generated once from a disposable PostgreSQL 0037 database after installing
# the reviewed artifact scaffold.  The operator-backed test proves exact
# equality; generation itself never consults a database or source module.
_TARGET_SCHEMA_AUTHORITY_JSON = r"""
[
["data_room_package_files",["file_entry_id","tenant_id","package_id","deal_id","sequence","path_hash","extension","sha256","file_status","support_status","triage_status","parse_status","reason_codes","error_codes","doc_id","document_id","storage_uri","created_at","source_binding_state","source_lineage_object_id","source_lineage_object_kind","source_corpus_receipt_id"]],
["data_room_packages",["package_id","tenant_id","deal_id","status","created_by_actor_id","created_by_actor_type","manifest_uri","metadata","created_at","updated_at","source_binding_state","source_lineage_object_id","source_lineage_object_kind","source_corpus_receipt_id"]],
["debate_sessions",["debate_id","tenant_id","deal_id","run_id","protocol_version","max_rounds","rounds","status","idempotency_key","created_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["deliverable_manifest_references",["manifest_reference_id","tenant_id","deal_id","run_id","manifest_deliverable_id","manifest_output_object_id","manifest_catalog_ordinal","run_output_object_id","run_output_object_class","run_output_object_kind","member_kind","catalog_ordinal","member_step_output_id","member_output_object_id","member_identity_sha256","member_value_sha256","member_content_sha256","reference_sha256","lease_owner_id","lease_fencing_token","created_at"]],
["deliverables",["deliverable_id","tenant_id","deal_id","run_id","deliverable_type","format","status","uri","idempotency_key","created_at","run_output_object_id","run_output_object_class","run_output_object_kind","content_type","size_bytes","content_sha256","uri_sha256","object_version_hash","download_proof_hash","catalog_ordinal"]],
["deterministic_calculations",["calc_id","tenant_id","deal_id","calc_type","inputs","formula_hash","code_version","output","reproducibility_hash","created_at","updated_at","run_id","producer_step_name","run_output_object_id","run_output_object_class","run_output_object_kind","coverage_record_id","methodology_question_record_id"]],
["document_artifacts",["doc_id","tenant_id","deal_id","doc_type","title","source_system","version_id","ingested_at","sha256","uri","metadata","created_at","updated_at","source_binding_state","source_lineage_object_id","source_lineage_object_kind","source_corpus_receipt_id"]],
["document_parse_attempts",["parse_attempt_id","tenant_id","deal_id","document_id","attempt_ordinal","parser_name","parser_version","input_sha256","receipt_sha256","status","started_at","finished_at","source_binding_state","source_lineage_object_id","source_lineage_object_kind","source_corpus_receipt_id","write_origin","created_at"]],
["document_spans",["span_id","tenant_id","document_id","span_type","locator","text_excerpt","created_at","updated_at","deal_id","content_hash","parse_attempt_id","source_binding_state","source_lineage_object_id","source_lineage_object_kind","source_corpus_receipt_id"]],
["documents",["document_id","tenant_id","deal_id","doc_id","doc_type","parse_status","metadata","created_at","updated_at","source_binding_state","source_lineage_object_id","source_lineage_object_kind","source_corpus_receipt_id"]],
["evidence_trust_findings",["finding_id","tenant_id","deal_id","run_id","court_id","finding_type","claim_id","evidence_ids","sanad_id","calc_ids","defect_ids","reason_codes","created_at","updated_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["layer2_ic_challenges",["challenge_id","tenant_id","deal_id","run_id","source_debate_id","status","safe_summary","created_at","updated_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["layer2_ic_findings",["finding_id","tenant_id","deal_id","run_id","challenge_id","finding_type","severity","category","supported_claim_ids","supported_calc_ids","graph_ref_ids","rag_ref_ids","enrichment_ref_ids","created_at","updated_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["methodology_coverage_records",["coverage_record_id","tenant_id","deal_id","run_id","selection_id","methodology_version_record_id","methodology_version_id","methodology_question_record_id","methodology_question_id","methodology_type","status","reason_code","evidence_links","answer","conflict_ids","defect_ids","created_at","updated_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["methodology_extraction_results",["result_id","tenant_id","deal_id","run_id","task_record_id","attempt_id","task_input_sha256","outcome","result_payload","result_sha256","searched_document_ids","searched_span_ids","searched_scope_sha256","provider_provenance_sha256","gap_finding","created_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["methodology_extraction_task_spans",["task_span_record_id","tenant_id","deal_id","run_id","task_record_id","document_id","span_id","span_ordinal","content_sha256","locator_sha256","created_at"]],
["methodology_extraction_tasks",["task_record_id","tenant_id","deal_id","run_id","selection_id","coverage_record_id","methodology_version_record_id","methodology_version_id","methodology_question_record_id","methodology_question_id","methodology_type","extraction_task_id","document_id","document_classification_id","document_classification_sha256","planner_contract_version","task_contract_version","task_payload","task_input_sha256","candidate_span_set_sha256","status","created_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["methodology_result_claim_links",["claim_link_id","tenant_id","deal_id","run_id","result_id","factual_output_record_id","selection_id","coverage_record_id","methodology_version_record_id","methodology_question_record_id","claim_id","canonical_key_sha256","reused_existing_claim","created_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["methodology_result_defect_links",["defect_link_id","tenant_id","deal_id","run_id","result_id","factual_output_record_id","sanad_link_id","defect_id","created_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["methodology_result_evidence_links",["evidence_link_id","tenant_id","deal_id","run_id","result_id","factual_output_record_id","claim_link_id","evidence_id","document_id","source_span_id","locator_sha256","created_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["methodology_result_factual_outputs",["factual_output_record_id","tenant_id","deal_id","run_id","result_id","task_record_id","output_ordinal","factual_output_id","typed_output_payload","typed_output_sha256","accepted_source_span_ids","accepted_locator_sha256s","accepted_locator_set_sha256","created_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["methodology_result_sanad_links",["sanad_link_id","tenant_id","deal_id","run_id","result_id","factual_output_record_id","claim_link_id","sanad_id","primary_evidence_id","created_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["muhasabah_records",["record_id","tenant_id","deal_id","run_id","source_step","agent_id","output_id","confidence","is_subjective","supported_claim_ids","supported_calc_ids","uncertainties","record_timestamp","created_at","updated_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["run_applicability_decisions",["applicability_decision_id","tenant_id","deal_id","run_id","decision_phase","decision_kind","subject_kind","subject_key","outcome","upstream_input_binding_id","upstream_output_object_id","deciding_step_output_id","contract_rule_id","contract_rule_version","contract_rule_hash","evidence_sha256","decision_sha256","lease_owner_id","lease_fencing_token","created_at"]],
["run_consumer_receipts",["receipt_id","tenant_id","deal_id","run_id","expected_edge_id","consumer_step_output_id","consumer_output_object_id","consumer_step_name","consumer_subrole","usage_evidence_sha256","consumed_at","lease_owner_id","lease_fencing_token","created_at"]],
["run_expected_consumer_edges",["expected_edge_id","tenant_id","deal_id","run_id","producer_input_binding_id","producer_output_object_id","consumer_step_name","consumer_subrole","usage_kind","applicability_decision_id","contract_rule_id","contract_rule_version","contract_rule_hash","edge_tuple_sha256","materialization_origin","lease_owner_id","lease_fencing_token","created_at"]],
["run_input_bindings",["binding_id","tenant_id","deal_id","run_id","source_lineage_object_id","source_object_kind","source_disposition","binding_purpose","corpus_receipt_id","selected_source_version","selected_identity_sha256","selected_content_sha256","corpus_receipt_sha256","pipeline_contract_version","pipeline_contract_hash","binding_sha256","created_at"]],
["run_output_objects",["output_object_id","tenant_id","deal_id","run_id","output_class","output_kind","output_schema_version","native_identity_sha256","native_output_sha256","pipeline_contract_version","pipeline_contract_hash","write_origin","lease_owner_id","lease_fencing_token","created_at"]],
["run_step_outputs",["step_output_id","tenant_id","deal_id","run_id","step_id","output_object_id","step_name","subrole","output_kind","output_ordinal","semantic_rule_version","semantic_rule_hash","summary_sha256","lease_owner_id","lease_fencing_token","created_at"]],
["run_steps",["step_id","tenant_id","run_id","step_name","step_order","status","started_at","finished_at","retry_count","result_summary","error_code","error_message","pipeline_contract_version","pipeline_contract_hash","semantic_rule_version","semantic_rule_hash"]],
["runs",["run_id","tenant_id","deal_id","mode","status","started_at","finished_at","idempotency_key","created_at","source","cancel_requested_at","created_by_actor_id","created_by_actor_type","strict_preflight_proof_id","lease_owner_id","lease_fencing_token","lease_expires_at","last_heartbeat_at","pipeline_contract_version","pipeline_contract_hash","pipeline_resume_state","pipeline_resume_error_code"]],
["source_lineage_objects",["source_lineage_object_id","tenant_id","deal_id","corpus_receipt_id","object_kind","source_version","source_disposition","object_identity_sha256","content_sha256","corpus_receipt_sha256","write_origin","created_at"]],
["validated_evidence_packages",["package_id","tenant_id","deal_id","run_id","court_id","dashboard_id","status","safe_summary","created_at","updated_at","run_output_object_id","run_output_object_class","run_output_object_kind"]],
["vector_embeddings",["embedding_id","tenant_id","deal_id","run_id","source_type","source_id","embedding_model","embedding_dimensions","content_hash","embedding","created_at","updated_at","producer_step_name","embedding_namespace","run_output_object_id","run_output_object_class","run_output_object_kind"]]
]
"""
_TARGET_SCHEMA_AUTHORITY = tuple(
    (str(table), tuple(str(column) for column in columns))
    for table, columns in json.loads(_TARGET_SCHEMA_AUTHORITY_JSON)
)

_FIELD_REFERENCE_RE = re.compile(r"\b(NEW|OLD)\.([a-z_][a-z0-9_]*)\b")
_FUNCTION_BODY_RE = re.compile(
    r"\bCREATE(?:\s+OR\s+REPLACE)?\s+FUNCTION\b.*?\bAS\s+\$\$(.*?)\$\$",
    re.IGNORECASE | re.DOTALL,
)
_RELATION_REFERENCE_RE = re.compile(
    r"(?<!DISTINCT )\b(?:"
    r"FROM|JOIN|UPDATE|INSERT\s+INTO|DELETE\s+FROM|REFERENCES|"
    r"MERGE\s+INTO|LOCK\s+TABLE|TRUNCATE(?:\s+TABLE)?|TABLE"
    r")\s+([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_RELATION_FORM_RE = re.compile(
    r"\b(?:EXECUTE|COPY|CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE|"
    r"ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+(?:MATERIALIZED\s+)?VIEW|"
    r"REFRESH\s+MATERIALIZED\s+VIEW)\b",
    re.IGNORECASE,
)
_CTE_NAME_RE = re.compile(
    r"(?:\bWITH\b|,)\s*([a-z_][a-z0-9_]*)\s+AS\s*\(",
    re.IGNORECASE,
)
_TRIGGER_EVENT_RE = re.compile(
    r"\bCREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+"
    r"(?P<name>[a-z_][a-z0-9_]*)\s+"
    r"(?:BEFORE|AFTER)\s+(?P<events>.+?)\s+ON\s+"
    r"public\.(?P<table>[a-z_][a-z0-9_]*)\b",
    re.IGNORECASE,
)


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V10._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _error(code: str, detail: str) -> Never:
    raise ContractArtifactError(code, detail)


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        _error(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _decode_artifact(raw: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], _V10._decode_artifact(raw))


def _render_artifact(artifact: dict[str, Any]) -> bytes:
    return (json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _decode_sql(row: dict[str, Any]) -> tuple[str, ...]:
    try:
        return tuple(
            bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]
        )
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_V11_CURRENT_GUARD_INVALID",
            "current-guard SQL is not canonical UTF-8 hex",
        ) from exc


def target_schema_authority() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return the immutable 0037-plus-scaffold target schema catalog."""

    return _TARGET_SCHEMA_AUTHORITY


def target_trigger_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str, str], ...]:
    """Return all 73 literal authority/table/trigger coordinates."""

    return tuple(
        (str(row["authority_id"]), str(table), str(trigger))
        for row in artifact["sql_authority_registry"]["rows"][_CURRENT_AUTHORITY_START:]
        for table, trigger in row["target_tuple"]
    )


def target_event_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str, str, str], ...]:
    """Return all 192 authority/table/trigger/event coordinates."""

    coordinates: list[tuple[str, str, str, str]] = []
    for row in artifact["sql_authority_registry"]["rows"][_CURRENT_AUTHORITY_START:]:
        declared = _declared_trigger_events(artifact, row)
        for table, trigger in row["target_tuple"]:
            events = declared.get((table, trigger))
            if not events:
                _error(
                    "TASK7_V11_CURRENT_GUARD_TARGET_EVENT_MISSING",
                    f"{row['authority_id']}:{table}:{trigger}",
                )
            coordinates.extend(
                (str(row["authority_id"]), str(table), str(trigger), event)
                for event in sorted(events)
            )
    return tuple(coordinates)


def invariant_vector(artifact: dict[str, Any]) -> dict[str, int]:
    return cast(dict[str, int], _V10.invariant_vector(artifact))


def _validate_hash_bound_rows(artifact: dict[str, Any]) -> None:
    sql_section = artifact["sql_authority_registry"]
    for ordinal, row in enumerate(sql_section["rows"]):
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        expected = hashlib.sha256(_ROW_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest()
        _expect(expected, row.get("expected_sha256"), f"v11 SQL authority {ordinal}")
    _expect(
        _h([_SQL_REGISTRY_HASH_DOMAIN, sql_section["rows"]]),
        sql_section.get("expected_sha256"),
        "v11 SQL authority registry",
    )

    erasure_section = artifact["erasure_execution_registry"]
    for ordinal, row in enumerate(erasure_section["rows"]):
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        expected = hashlib.sha256(_ERASURE_ROW_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest()
        _expect(expected, row.get("expected_sha256"), f"v11 erasure row {ordinal}")
    _expect(
        _h([_ERASURE_REGISTRY_HASH_DOMAIN, erasure_section["rows"]]),
        erasure_section.get("expected_sha256"),
        "v11 erasure registry",
    )


def _legacy_trigger_events(
    artifact: dict[str, Any],
) -> dict[tuple[str, str], frozenset[str]]:
    result: dict[tuple[str, str], frozenset[str]] = {}
    for replacement in artifact["erasure_execution_registry"]["rows"][0][
        "legacy_guard_replacement_tuple"
    ]:
        for trigger in replacement[5]:
            relation, name, _timing, events = trigger[:4]
            table = str(relation).removeprefix("public.")
            result[(table, str(name))] = frozenset(map(str, events))
    return result


def _declared_trigger_events(
    artifact: dict[str, Any],
    row: dict[str, Any],
) -> dict[tuple[str, str], frozenset[str]]:
    declared = _legacy_trigger_events(artifact)
    for statement in _decode_sql(row):
        match = _TRIGGER_EVENT_RE.search(statement)
        if match is None:
            continue
        events = frozenset(
            event
            for event in ("INSERT", "UPDATE", "DELETE")
            if re.search(rf"\b{event}\b", match.group("events"), re.IGNORECASE)
        )
        declared[(match.group("table"), match.group("name"))] = events
    return declared


def _validate_current_guard_target_field_closure(
    artifact: dict[str, Any],
) -> None:
    """Prove every NEW/OLD field exists on every bound target schema."""

    schemas = dict(_TARGET_SCHEMA_AUTHORITY)
    rows = artifact["sql_authority_registry"]["rows"][_CURRENT_AUTHORITY_START:]
    for row in rows:
        references = {
            record: {
                field
                for found_record, field in _FIELD_REFERENCE_RE.findall("\n".join(_decode_sql(row)))
                if found_record == record
            }
            for record in ("NEW", "OLD")
        }
        events_by_target = _declared_trigger_events(artifact, row)
        for table, trigger_name in row["target_tuple"]:
            columns = frozenset(schemas.get(table, ()))
            if not columns:
                _error(
                    "TASK7_V11_CURRENT_GUARD_TARGET_FIELD_MISMATCH",
                    f"{row['authority_id']}:{table}:schema authority absent",
                )
            events = events_by_target.get((table, trigger_name))
            if not events:
                _error(
                    "TASK7_V11_CURRENT_GUARD_TARGET_EVENT_MISSING",
                    f"{row['authority_id']}:{table}:{trigger_name}",
                )
            # Trigger-event reachability is a separate closed inventory.  A
            # shared record descriptor must still be valid for every target,
            # even when a branch is not expected to execute for one event.
            missing = sorted((references["NEW"] | references["OLD"]) - columns)
            if missing:
                _error(
                    "TASK7_V11_CURRENT_GUARD_TARGET_FIELD_MISMATCH",
                    f"{row['authority_id']}:{table}:{trigger_name}:{missing!r}",
                )


def _validate_target_authority_closure(artifact: dict[str, Any]) -> None:
    targets = target_trigger_authority(artifact)
    events = target_event_authority(artifact)
    target_tables = {table for _authority, table, _trigger in targets}
    schema_tables = {table for table, _columns in _TARGET_SCHEMA_AUTHORITY}
    if (
        len(targets) != 73
        or len(targets) != len(set(targets))
        or len(events) != 192
        or len(events) != len(set(events))
        or schema_tables != target_tables
    ):
        _error(
            "TASK7_V11_CURRENT_GUARD_TARGET_AUTHORITY_INCOMPLETE",
            f"targets={len(targets)} events={len(events)} "
            f"schema_tables={len(schema_tables)} target_tables={len(target_tables)}",
        )


def _validate_locked_search_path_call_closure(
    artifact: dict[str, Any],
) -> None:
    """Reject unqualified relation access from every current guard/helper."""

    rows = artifact["sql_authority_registry"]["rows"][_CURRENT_AUTHORITY_START:]
    statements = [statement for row in rows for statement in _decode_sql(row)]
    execution = artifact["erasure_execution_registry"]["rows"][0]
    statements.extend(
        bytes.fromhex(value).decode("utf-8")
        for value in execution["legacy_guard_upgrade_sql_utf8_hex_tuple"]
    )
    for statement in statements:
        bodies = _FUNCTION_BODY_RE.findall(statement)
        for body in bodies:
            compact = " ".join(body.split())
            unsupported = _UNSUPPORTED_RELATION_FORM_RE.search(compact)
            if unsupported is not None:
                _error(
                    "TASK7_V11_LOCKED_SEARCH_PATH_RELATION_FORM_UNSUPPORTED",
                    unsupported.group(0),
                )
            local_relations = {name.lower() for name in _CTE_NAME_RE.findall(compact)}
            for match in _RELATION_REFERENCE_RE.finditer(compact):
                relation = match.group(1)
                if (
                    relation.upper() in {"ROWS", "PUBLIC", "LATERAL"}
                    or relation.lower() in local_relations
                ):
                    continue
                if "." not in relation:
                    _error(
                        "TASK7_V11_LOCKED_SEARCH_PATH_RELATION_UNQUALIFIED",
                        relation,
                    )


def _validate_v11_authorities(artifact: dict[str, Any]) -> None:
    by_id = {row["authority_id"]: row for row in artifact["sql_authority_registry"]["rows"]}
    receipt = _decode_sql(by_id["current-receipt-equality-v1"])[2]
    if (
        receipt.count("full-strict-v11") != 1
        or "full-strict-v10" in receipt
        or "FROM public.runs r" not in receipt
        or "NEW.deal_id" in receipt
        or "OLD.deal_id" in receipt
    ):
        _error(
            "TASK7_V11_RECEIPT_AUTHORITY_INVALID",
            "receipt scope/version correction differs",
        )
    task5 = _decode_sql(by_id["current-task5-coverage-replacement-v1"])[0]
    if task5.count("full-strict-v11") != 1 or "full-strict-v10" in task5:
        _error(
            "TASK7_V11_TASK5_AUTHORITY_INVALID",
            "Task-5 version correction differs",
        )
    execution = artifact["erasure_execution_registry"]["rows"][0]
    lease_rows = [
        item
        for item in execution["legacy_guard_replacement_tuple"]
        if item[1] == "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)"
    ]
    if len(lease_rows) != 1:
        _error(
            "TASK7_V11_TASK6_LEASE_AUTHORITY_INVALID",
            "Task-6 lease replacement cardinality differs",
        )


def _validate_shape(
    raw: bytes,
    artifact: dict[str, Any],
    *,
    require_frozen: bool = False,
) -> None:
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "artifact must be LF-only with one terminal newline",
        )
    if tuple(artifact) != _SECTION_ORDER:
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "section order differs")
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v11-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v11"
        or not isinstance(metadata.get("append_frozen"), bool)
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v11 metadata differs")
    if require_frozen and metadata["append_frozen"] is not True:
        _error(
            "TASK7_V11_APPEND_FREEZE_REQUIRED",
            "fixture admission requires frozen v11 bytes",
        )

    try:
        reviewed = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
        reviewed_value = json.loads(reviewed)
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v11 reviewed design encoding differs",
        ) from exc
    _expect(
        _sha256_bytes(reviewed),
        metadata.get("reviewed_design_sha256"),
        "v11 reviewed design",
    )
    if not isinstance(reviewed_value, list) or reviewed_value[:2] != [
        "full-strict-v11-reviewed-design-literal-v1",
        ["pipeline_contract_version", "full-strict-v11"],
    ]:
        _error(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v11 reviewed design version differs",
        )
    recipes = artifact["digest_recipe_registry"]["rows"]
    expected_recipe = [
        "pipeline_contract_hash",
        '["pipeline-contract-v11","full-strict-v11",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if recipes[5] != expected_recipe or bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v11 digest recipe differs")

    _validate_hash_bound_rows(artifact)
    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    try:
        v10 = _MATERIALIZER.project_v11_to_v10(candidate)
        _MATERIALIZER._validate_exact_diff(v10, candidate)
    except _MATERIALIZER.MaterializationError as exc:
        raise ContractArtifactError(
            "TASK7_V11_EXACT_DIFF_INVALID",
            "candidate is not the exact reviewed v10 successor",
        ) from exc
    _V10._validate_shape(_render_artifact(v10), v10)
    _validate_v11_authorities(artifact)
    _validate_target_authority_closure(artifact)
    _validate_current_guard_target_field_closure(artifact)
    _validate_locked_search_path_call_closure(artifact)
    if invariant_vector(artifact) != _EXPECTED_INVARIANTS:
        _error(
            "TASK7_V11_INVARIANT_DRIFT",
            f"derived {invariant_vector(artifact)!r}",
        )


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Return exact append-frozen bytes without mutating the candidate."""

    candidate = _decode_artifact(candidate_raw)
    _validate_shape(candidate_raw, candidate)
    if candidate["metadata"]["append_frozen"] is not False:
        _error(
            "TASK7_V11_UNFROZEN_CANDIDATE_REQUIRED",
            "prospective freeze requires append_frozen=false",
        )
    frozen = copy.deepcopy(candidate)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render_artifact(frozen)
    _validate_shape(frozen_raw, frozen, require_frozen=True)
    return frozen_raw


def _section_hashes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": _sha256_bytes(_canonical_bytes(artifact[name])),
        }
        for ordinal, name in enumerate(_SECTION_ORDER)
    ]


def derive_contract_bytes(
    raw: bytes,
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Derive every v11 component solely from the supplied artifact bytes."""

    artifact = _decode_artifact(raw)
    artifact_sha256 = _sha256_bytes(raw)
    sections = _section_hashes(artifact)
    reviewed_hash = _h(
        [
            "full-strict-v11-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    components = cast(
        dict[str, str],
        _V10._V9._V7._V6._V5._derive_components(artifact, reviewed_hash),
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v11",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v11-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v11",
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V11_CONTRACT_HASH": pipeline_hash,
    }
