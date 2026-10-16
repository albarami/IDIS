"""RED admission contract for Task-7 erasure executability.

The reviewed evidence/receipt census has 72 relations, while the executable erasure
runner owns a fixed 86-relation deletion graph.  Migration 0038 can execute only the
48 relations that exist at that revision.  The remaining 38 relations are frozen as
an inactive replacement body and may activate only in a later table-creating
migration after an exact, hash-checked census.

This module deliberately specifies that split independently of the materializer.
It must remain green for the append-frozen v4 artifact.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MATERIALIZER_PATH = _REPO_ROOT / "scripts" / "materialize_full_strict_v4_preimage.py"
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_task7_v4_contract_fixture.py"
_V4_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v4_reviewed_preimage_v1.json"
_VOID_V2_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v2_reviewed_preimage_v1.json"
_REVIEWED_AUTHORITY = (
    _REPO_ROOT / "tests" / "slice100_task7" / "_full_strict_v4_reviewed_authority.py"
)

_CURRENT48 = (
    "deliverable_manifest_references",
    "run_consumer_receipts",
    "run_expected_consumer_edges",
    "methodology_result_defect_links",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "methodology_result_claim_links",
    "run_applicability_decisions",
    "run_step_outputs",
    "methodology_result_factual_outputs",
    "methodology_extraction_results",
    "calc_sanads",
    "deterministic_calculations",
    "layer2_ic_findings",
    "layer2_ic_challenges",
    "debate_sessions",
    "evidence_trust_findings",
    "validated_evidence_packages",
    "muhasabah_records",
    "deliverables",
    "vector_embeddings",
    "run_input_bindings",
    "data_room_package_files",
    "break_glass_grants",
    "deal_assignments",
    "defects",
    "human_gate_actions",
    "human_gates",
    "methodology_extraction_attempts",
    "run_steps",
    "methodology_extraction_task_spans",
    "methodology_extraction_tasks",
    "methodology_coverage_records",
    "run_output_objects",
    "document_spans",
    "document_parse_attempts",
    "documents",
    "document_artifacts",
    "data_room_packages",
    "source_lineage_objects",
    "overrides",
    "run_methodology_selections",
    "runs",
    "sanads",
    "evidence_items",
    "claims",
    "deals",
    "strict_preflight_proofs",
)

_FUTURE38 = (
    "methodology_truth_dashboard_rows",
    "company_identity_outputs",
    "enrichment_provider_outcomes",
    "enrichment_normalized_facts",
    "graph_projection_results",
    "rag_index_results",
    "rag_query_results",
    "external_intelligence_conflict_plans",
    "external_intelligence_conflict_executions",
    "external_intelligence_conflict_findings",
    "validated_evidence_package_references",
    "debate_role_outputs",
    "analysis_agent_outputs",
    "scoring_outputs",
    "layer2_readiness_outputs",
    "layer2_role_outputs",
    "generic_extraction_outputs",
    "run_claim_grading_links",
    "company_identity_input_references",
    "generic_extraction_input_references",
    "graph_result_input_references",
    "rag_index_input_references",
    "rag_query_input_references",
    "conflict_input_references",
    "evidence_trust_court_input_references",
    "validated_evidence_package_input_references",
    "debate_role_input_references",
    "analysis_agent_input_references",
    "scoring_input_references",
    "layer2_readiness_input_references",
    "layer2_role_input_references",
    "deliverable_artifact_input_references",
    "step_receipt_input_references",
    "company_identity_identifiers",
    "scoring_output_dimensions",
    "generic_extraction_claim_links",
    "generic_extraction_evidence_links",
    "methodology_truth_dashboard_root_links",
)

_RUNNER86 = (
    "deliverable_manifest_references",
    "run_consumer_receipts",
    "run_expected_consumer_edges",
    "company_identity_input_references",
    "generic_extraction_input_references",
    "graph_result_input_references",
    "rag_index_input_references",
    "rag_query_input_references",
    "conflict_input_references",
    "evidence_trust_court_input_references",
    "validated_evidence_package_input_references",
    "debate_role_input_references",
    "analysis_agent_input_references",
    "scoring_input_references",
    "layer2_readiness_input_references",
    "layer2_role_input_references",
    "deliverable_artifact_input_references",
    "step_receipt_input_references",
    "run_claim_grading_links",
    "methodology_result_defect_links",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "generic_extraction_evidence_links",
    "generic_extraction_claim_links",
    "methodology_result_claim_links",
    "generic_extraction_outputs",
    "enrichment_normalized_facts",
    "enrichment_provider_outcomes",
    "rag_query_results",
    "rag_index_results",
    "external_intelligence_conflict_findings",
    "external_intelligence_conflict_executions",
    "external_intelligence_conflict_plans",
    "validated_evidence_package_references",
    "debate_role_outputs",
    "analysis_agent_outputs",
    "layer2_role_outputs",
    "methodology_truth_dashboard_root_links",
    "methodology_truth_dashboard_rows",
    "company_identity_identifiers",
    "company_identity_outputs",
    "graph_projection_results",
    "scoring_output_dimensions",
    "scoring_outputs",
    "layer2_readiness_outputs",
    "run_applicability_decisions",
    "run_step_outputs",
    "methodology_result_factual_outputs",
    "methodology_extraction_results",
    "calc_sanads",
    "deterministic_calculations",
    "layer2_ic_findings",
    "layer2_ic_challenges",
    "debate_sessions",
    "evidence_trust_findings",
    "validated_evidence_packages",
    "muhasabah_records",
    "deliverables",
    "vector_embeddings",
    "run_input_bindings",
    "data_room_package_files",
    "break_glass_grants",
    "deal_assignments",
    "defects",
    "human_gate_actions",
    "human_gates",
    "methodology_extraction_attempts",
    "run_steps",
    "methodology_extraction_task_spans",
    "methodology_extraction_tasks",
    "methodology_coverage_records",
    "run_output_objects",
    "document_spans",
    "document_parse_attempts",
    "documents",
    "document_artifacts",
    "data_room_packages",
    "source_lineage_objects",
    "overrides",
    "run_methodology_selections",
    "runs",
    "sanads",
    "evidence_items",
    "claims",
    "deals",
    "strict_preflight_proofs",
)

_READ_CLOSURE = (
    "erasure_requests",
    "deterministic_calculations",
    "runs",
    "human_gates",
    "legal_holds",
    "document_artifacts",
)

_LOCK_TUPLE = (
    "erasure-tenant-advisory-lock-v1",
    "PostgreSQL-16",
    "pg_catalog.hashtextextended",
    "idis-task7-erasure-tenant-lock-v1:",
    0,
    "pg_catalog.pg_advisory_xact_lock",
)

_EXECUTION_FIELDS = (
    "execution_ordinal",
    "execution_id",
    "execution_version",
    "runner_function_signature",
    "activation_state",
    "current_relation_tuple",
    "future_relation_tuple",
    "complete_relation_tuple",
    "declared_read_relation_tuple",
    "advisory_lock_tuple",
    "current_body_sql_utf8_hex_tuple",
    "current_acl_sql_utf8_hex_tuple",
    "future_body_sql_utf8_hex_tuple",
    "future_acl_sql_utf8_hex_tuple",
    "future_activation_census_tuple",
    "future_activation_census_sha256",
    "hold_lock_contract_tuple",
    "guard_authority_contract_tuple",
    "legacy_guard_replacement_tuple",
    "hold_lock_upgrade_sql_utf8_hex_tuple",
    "hold_lock_downgrade_sql_utf8_hex_tuple",
    "legacy_guard_upgrade_sql_utf8_hex_tuple",
    "legacy_guard_downgrade_sql_utf8_hex_tuple",
    "future_activation_sql_utf8_hex_tuple",
    "future_restoration_sql_utf8_hex_tuple",
    "legal_red_case_tuple",
    "illegal_red_case_tuple",
    "direct_sql_red_case_tuple",
    "expected_sha256",
)

_DELETE_RE = re.compile(
    r"\bDELETE\s+FROM\s+public\.(?P<table>[a-z][a-z0-9_]*)\b",
    re.IGNORECASE,
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ROW_HASH_DOMAIN = b"full-strict-v4-erasure-execution-row-sha256-v1\x00"


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _artifact() -> dict[str, Any]:
    assert _V4_ARTIFACT.is_file(), (
        "TASK7_ERASURE_EXECUTION_V4_ARTIFACT_ABSENT: the split registry must be "
        "admitted before migration 0038"
    )
    value = json.loads(_V4_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _in_memory_materialized_artifact() -> dict[str, Any]:
    module = _load_module(_MATERIALIZER_PATH, "task7_erasure_in_memory_materializer")
    rejected = module._load_rejected_artifact(_VOID_V2_ARTIFACT)
    authority = module._load_module(_REVIEWED_AUTHORITY)
    value = module._build_artifact(rejected, authority)
    assert isinstance(value, dict)
    return value


def _execution_row() -> dict[str, Any]:
    section = _artifact().get("erasure_execution_registry")
    assert isinstance(section, dict)
    assert tuple(section) == ("rows", "expected_sha256")
    rows = section.get("rows")
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


def _decode_tuple(row: dict[str, Any], field: str) -> tuple[str, ...]:
    encoded = row.get(field)
    assert isinstance(encoded, list) and encoded
    decoded = tuple(bytes.fromhex(item).decode("utf-8") for item in encoded)
    assert all(item == item.strip() and "\r" not in item for item in decoded)
    return decoded


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _row_hash(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    return hashlib.sha256(_ROW_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest()


def _delete_order(statements: tuple[str, ...]) -> tuple[str, ...]:
    matches: list[str] = []
    for statement in statements:
        matches.extend(match.group("table").lower() for match in _DELETE_RE.finditer(statement))
    return tuple(matches)


def test_materializer_declares_the_exact_current48_future38_runner86_split() -> None:
    """The materializer cannot infer this lifecycle split from tables at runtime."""

    module = _load_module(_MATERIALIZER_PATH, "task7_erasure_split_materializer")
    assert getattr(module, "CURRENT_ERASURE_RELATIONS", None) == _CURRENT48
    assert getattr(module, "FUTURE_ERASURE_RELATIONS", None) == _FUTURE38
    assert getattr(module, "COMPLETE_ERASURE_RELATIONS", None) == _RUNNER86
    assert getattr(module, "ERASURE_EXECUTION_FIELDS", None) == _EXECUTION_FIELDS

    current = frozenset(_CURRENT48)
    future = frozenset(_FUTURE38)
    complete = frozenset(_RUNNER86)
    assert len(current) == 48 and len(future) == 38 and len(complete) == 86
    assert current.isdisjoint(future)
    assert current | future == complete
    assert tuple(relation for relation in _RUNNER86 if relation in current) == _CURRENT48
    assert tuple(module.FUTURE_RELATION_TABLES) == _FUTURE38


def test_hermetic_generator_owns_an_independent_closed_split_validator() -> None:
    """Hash derivation must reject split drift without importing the materializer."""

    module = _load_module(_GENERATOR_PATH, "task7_erasure_split_generator")
    assert getattr(module, "_ERASURE_EXECUTION_FIELDS", None) == _EXECUTION_FIELDS
    assert getattr(module, "_CURRENT_ERASURE_RELATIONS", None) == _CURRENT48
    assert getattr(module, "_FUTURE_ERASURE_RELATIONS", None) == _FUTURE38
    assert getattr(module, "_APPROVED_ERASURE_RUNNER_SEQUENCE", None) == _RUNNER86
    assert callable(getattr(module, "validate_erasure_execution_registry", None))


def test_in_memory_materializer_emits_exact_results_and_state_reset_acl() -> None:
    """The one-shot write is gated on complete literal results and privilege reset."""

    artifact = _in_memory_materialized_artifact()
    section = artifact["erasure_execution_registry"]
    row = section["rows"][0]
    assert tuple(row) == _EXECUTION_FIELDS

    for body_field, relation_tuple in (
        ("current_body_sql_utf8_hex_tuple", _CURRENT48),
        ("future_body_sql_utf8_hex_tuple", _RUNNER86),
    ):
        body = "\n".join(_decode_tuple(row, body_field))
        result_keys = tuple(
            re.findall(
                r"pg_catalog\.jsonb_build_object\('([a-z][a-z0-9_]*)',deleted_count\)",
                body,
            )
        )
        assert result_keys == relation_tuple

    for acl_field, relation_tuple in (
        ("current_acl_sql_utf8_hex_tuple", _CURRENT48),
        ("future_acl_sql_utf8_hex_tuple", _RUNNER86),
    ):
        acl = "\n".join(_decode_tuple(row, acl_field))
        assert "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_function_owner" in acl
        assert "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_executor" in acl
        reset_relations = frozenset(relation_tuple) | {"erasure_requests", "legal_holds"}
        for relation in reset_relations:
            assert (
                f"REVOKE ALL PRIVILEGES ON TABLE public.{relation} FROM idis_erasure_function_owner"
            ) in acl
            assert (
                f"REVOKE ALL PRIVILEGES ON TABLE public.{relation} FROM idis_erasure_executor"
            ) in acl
        assert "ALL TABLES IN SCHEMA" not in acl.upper()


def test_erasure_execution_registry_is_closed_literal_and_hash_bound() -> None:
    artifact = _artifact()
    tuple_specs = dict(artifact["tuple_schema_registry"]["rows"])
    assert tuple(tuple_specs.get("ErasureExecutionSpecV1", ())) == _EXECUTION_FIELDS

    row = _execution_row()
    assert tuple(row) == _EXECUTION_FIELDS
    assert row["execution_ordinal"] == 0
    assert row["execution_id"] == "approved-erasure-execution-v1"
    assert row["execution_version"] == "1"
    assert row["runner_function_signature"] == (
        "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
    )
    assert row["activation_state"] == "CURRENT48_ACTIVE_FUTURE38_FROZEN"
    assert tuple(row["current_relation_tuple"]) == _CURRENT48
    assert tuple(row["future_relation_tuple"]) == _FUTURE38
    assert tuple(row["complete_relation_tuple"]) == _RUNNER86
    assert tuple(row["declared_read_relation_tuple"]) == _READ_CLOSURE
    assert tuple(row["advisory_lock_tuple"]) == _LOCK_TUPLE
    assert _HASH_RE.fullmatch(row["expected_sha256"])
    assert row["expected_sha256"] == _row_hash(row)


def test_current_body_is_current48_only_and_future_body_is_exact_runner86() -> None:
    row = _execution_row()
    current_body = _decode_tuple(row, "current_body_sql_utf8_hex_tuple")
    future_body = _decode_tuple(row, "future_body_sql_utf8_hex_tuple")

    assert _delete_order(current_body) == _CURRENT48
    assert _delete_order(future_body) == _RUNNER86
    assert not (frozenset(_FUTURE38) & frozenset(_delete_order(current_body)))
    for statements in (current_body, future_body):
        sql = "\n".join(statements)
        assert "CREATE FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)" in sql
        assert "SECURITY DEFINER" in sql
        assert "SET search_path=pg_catalog,pg_temp" in sql
        assert "CREATE OR REPLACE" not in sql.upper()
        assert "EXECUTE " not in sql.upper()
        assert "session_user='idis_erasure_executor'" in sql
        assert "current_user='idis_erasure_function_owner'" in sql
        assert "status IN ('REQUESTED','FAILED')" in sql
        assert "FOR UPDATE" in sql
        assert "SET CONSTRAINTS ALL DEFERRED" in sql
        assert "SET CONSTRAINTS ALL IMMEDIATE" in sql
        first_delete = sql.index("DELETE FROM public.")
        last_delete = sql.rindex("DELETE FROM public.")
        assert sql.index("SET CONSTRAINTS ALL DEFERRED") < first_delete
        assert sql.index("SET CONSTRAINTS ALL IMMEDIATE") > last_delete
        assert "pg_catalog.set_config('idis.tenant_id',$2::text,true)" in sql
        assert "pg_catalog.set_config('idis.erasure_context','true',true)" in sql
        assert (
            "pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended("
            "'idis-task7-erasure-tenant-lock-v1:'||$2::text,0))"
        ) in "".join(sql.split())
        assert "public.legal_holds" in sql
        assert "public.document_artifacts" in sql
        assert "public.audit_events" not in sql


def test_current_and_future_runners_fail_closed_on_null_or_malformed_artifact_uri() -> None:
    """A nullable legacy URI must never disappear from the hold-target projection."""

    row = _execution_row()
    for field in (
        "current_body_sql_utf8_hex_tuple",
        "future_body_sql_utf8_hex_tuple",
        "future_activation_sql_utf8_hex_tuple",
        "future_restoration_sql_utf8_hex_tuple",
    ):
        sql = " ".join(_decode_tuple(row, field))
        assert "a.uri IS NULL OR a.uri NOT LIKE 'idis://%' OR a.uri='idis://'" in sql, field
        assert "a.uri IS NOT NULL AND (a.uri NOT LIKE 'idis://%'" not in sql, field


def test_current_and_future_acl_are_exact_and_future_acl_is_not_migration0038_runtime_sql() -> None:
    row = _execution_row()
    current_acl = "\n".join(_decode_tuple(row, "current_acl_sql_utf8_hex_tuple"))
    future_acl = "\n".join(_decode_tuple(row, "future_acl_sql_utf8_hex_tuple"))

    for sql, delete_relations in ((current_acl, _CURRENT48), (future_acl, _RUNNER86)):
        assert "GRANT USAGE ON SCHEMA public TO idis_erasure_function_owner" in sql
        assert "GRANT UPDATE (request_id) ON TABLE public.erasure_requests" in sql
        assert "GRANT EXECUTE ON FUNCTION " in sql
        assert "TO idis_erasure_executor" in sql
        assert "FROM PUBLIC" in sql
        assert "OWNER TO idis_erasure_function_owner" in sql
        assert "GRANT INSERT" not in sql.upper()
        assert "GRANT ALL" not in sql.upper()
        assert "CREATE ROLE" not in sql.upper()
        assert "ALTER ROLE" not in sql.upper()
        assert "BYPASSRLS" not in sql.upper()
        for relation in delete_relations:
            assert f"public.{relation}" in sql

    assert frozenset(_FUTURE38).isdisjoint(
        relation for relation in _FUTURE38 if f"public.{relation}" in current_acl
    )


def test_future_activation_census_is_exact_and_cannot_be_runtime_conditional() -> None:
    row = _execution_row()
    census = row["future_activation_census_tuple"]
    assert isinstance(census, list) and len(census) == 38
    assert tuple(item[0] for item in census) == _FUTURE38
    assert all(
        isinstance(item, list)
        and len(item) == 2
        and isinstance(item[0], str)
        and isinstance(item[1], str)
        and _HASH_RE.fullmatch(item[1])
        for item in census
    )
    census_hash = hashlib.sha256(
        b"full-strict-v4-erasure-future-activation-census-v1\x00" + _canonical_bytes(census)
    ).hexdigest()
    assert row["future_activation_census_sha256"] == census_hash

    all_sql = "\n".join(
        statement
        for field in (
            "current_body_sql_utf8_hex_tuple",
            "current_acl_sql_utf8_hex_tuple",
            "future_body_sql_utf8_hex_tuple",
            "future_acl_sql_utf8_hex_tuple",
        )
        for statement in _decode_tuple(row, field)
    )
    compact = " ".join(all_sql.lower().split())
    assert "to_regclass" not in compact
    assert "information_schema" not in compact
    assert "pg_class" not in compact
    # GRANT EXECUTE is part of the exact ACL; only runtime/dynamic EXECUTE is forbidden.
    assert re.search(r"(?<!grant )\bexecute\b", compact) is None
    # Data-row EXISTS is required for hold checks; only catalog/table-existence branching is banned.
    assert "create table if exists" not in compact
    assert "drop table if exists" not in compact


def test_erasure_execution_authority_boundary_does_not_claim_unprovable_audit_binding() -> None:
    """Current audit rows cannot distinguish retries of one erasure request."""

    row = _execution_row()
    assert "audit_events" not in row["declared_read_relation_tuple"]
    body = "\n".join(_decode_tuple(row, "current_body_sql_utf8_hex_tuple"))
    assert "audit_events" not in body
    assert "erasure_requests" in body
    assert "legal_holds" in body
