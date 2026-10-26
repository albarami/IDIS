"""One-shot deterministic materializer for the reviewed full-strict-v3 preimage.

This program converts the permanently rejected v2 materialization into the corrected,
fully normalized v3 artifact.  It is intentionally a one-way tool: an existing output is
never replaced.  Once produced and admitted, the artifact bytes -- not this program or the
narrative design -- are the normative authority.

The only inputs are the explicitly named rejected artifact and reviewed authority helper.
No environment variable, database, clock, network, runtime IDIS module, or Pydantic model is
consulted.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

SECTION_ORDER = (
    "metadata",
    "canonicalization_contract",
    "tuple_schema_registry",
    "digest_recipe_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
    "sql_authority_registry",
    "erasure_execution_registry",
    "provider_registry",
    "role_registry",
    "deliverable_registry",
    "producer_selector_registry",
    "consumer_selector_registry",
    "applicability_registry",
    "edge_rule_registry",
    "usage_route_registry",
    "usage_extractor_registry",
    "erasure_baseline",
    "erasure_materialized",
    "activation_assertions",
)

V2_SECTION_ORDER = tuple(
    section
    for section in SECTION_ORDER
    if section not in {"sql_authority_registry", "erasure_execution_registry"}
)
REJECTED_V2_ARTIFACT_SHA512 = (
    "dd6258f54009b79ef1b014408acf140d9e566c5788fb3e2d29eee552bdadd178"
    "663e323e27830a18ea9f016766f04b2156cdde25385d9af30de11fb529e6df00"
)
ERASURE_AUTHORITY_BLOCKER_CODE = "TASK7_APPROVED_ERASURE_AUTHORITY_UNRESOLVED"

AUTHORITY_ROW_SCHEMA_HASH_DOMAIN = b"full-strict-v3-authority-row-schema-sha256-v1\x00"
SQL_AUTHORITY_ROW_HASH_DOMAIN = b"full-strict-v3-sql-authority-row-sha256-v1\x00"
SQL_AUTHORITY_REGISTRY_HASH_DOMAIN = "full-strict-v3-sql-authority-registry-v1"
ERASURE_EXECUTION_ROW_HASH_DOMAIN = b"full-strict-v3-erasure-execution-row-sha256-v1\x00"
ERASURE_EXECUTION_REGISTRY_HASH_DOMAIN = "full-strict-v3-erasure-execution-registry-v1"
ERASURE_ACTIVATION_CENSUS_HASH_DOMAIN = b"full-strict-v3-erasure-future-activation-census-v1\x00"
TENANT_PREDICATE = (
    "NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL "
    "AND tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid"
)

PROVIDERS = (
    "sec_edgar",
    "companies_house",
    "github",
    "fred",
    "finnhub",
    "fmp",
    "world_bank",
    "escwa_catalog",
    "escwa_ispar",
    "qatar_open_data",
    "hackernews",
    "gdelt",
    "patentsview",
    "wayback",
    "google_news_rss",
    "epo_open_patent",
    "semantic_scholar",
    "perplexity",
)
DEBATE_ROLES = (
    "advocate",
    "sanad_breaker",
    "contradiction_finder",
    "risk_officer",
    "arbiter",
)
ANALYSIS_ROLES = (
    "financial_agent",
    "market_agent",
    "technical_agent",
    "terms_agent",
    "team_agent",
    "risk_officer_agent",
    "historian_agent",
    "sector_specialist_agent",
)
LAYER2_ROLES = ("ic_advocate", "ic_challenger", "ic_arbiter")

AUTHORITY_IDS = (
    "dashboard-root-equation-v1",
    "graph-rag-namespace-v1",
    "rag-query-parent-coordinate-v1",
    "layer2-readiness-v1",
    "extract-rag-context-coordinate-v1",
    "debate-muhasabah-exact-one-v1",
    "analysis-muhasabah-exact-one-v1",
    "layer2-muhasabah-exact-one-v1",
    "grading-dispatch-and-consistency-v1",
    "scoring-binary64-finite-v1",
    "scoring-binary64-negative-zero-v1",
    "approved-erasure-append-only-v1",
    "native-root-exact-one-v1",
    "identity-identifier-projection-v1",
    "scoring-dimension-projection-v1",
    "generic-claim-projection-v1",
    "generic-evidence-projection-v1",
)

FUTURE_NATIVE_TABLES = (
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
)
FUTURE_REFERENCE_TABLES = (
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
)
FUTURE_CHILD_TABLES = (
    "company_identity_identifiers",
    "scoring_output_dimensions",
    "generic_extraction_claim_links",
    "generic_extraction_evidence_links",
    "methodology_truth_dashboard_root_links",
)
FUTURE_RELATION_TABLES = (
    *FUTURE_NATIVE_TABLES,
    *FUTURE_REFERENCE_TABLES,
    *FUTURE_CHILD_TABLES,
)

# Schema-census order is deliberately distinct from the child-first executable order.
FUTURE_ERASURE_RELATIONS = FUTURE_RELATION_TABLES
COMPLETE_ERASURE_RELATIONS = (
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
_FUTURE_ERASURE_RELATION_SET = frozenset(FUTURE_ERASURE_RELATIONS)
CURRENT_ERASURE_RELATIONS = tuple(
    relation
    for relation in COMPLETE_ERASURE_RELATIONS
    if relation not in _FUTURE_ERASURE_RELATION_SET
)
ERASURE_EXECUTION_READ_RELATIONS = (
    "erasure_requests",
    "deterministic_calculations",
    "runs",
    "human_gates",
    "legal_holds",
    "document_artifacts",
)
ERASURE_ADVISORY_LOCK_TUPLE = (
    "erasure-tenant-advisory-lock-v1",
    "PostgreSQL-16",
    "pg_catalog.hashtextextended",
    "idis-task7-erasure-tenant-lock-v1:",
    0,
    "pg_catalog.pg_advisory_xact_lock",
)
ERASURE_HOLD_LOCK_CONTRACT = (
    "legal-hold-lock-enforcement-v1",
    "public.idis_task7_legal_hold_lock_guard()",
    "trg_task7_legal_holds_tenant_lock",
    "public.legal_holds",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    "PostgreSQL-16-trigger-acquires-xact-lock-v1",
    "TASK7_ERASURE_HOLD_TENANT_CONTEXT_REJECTED",
    "TASK7_ERASURE_HOLD_TENANT_CHANGE_REJECTED",
)
ERASURE_GUARD_AUTHORITY_CONTRACT = (
    "approved-erasure-guard-context-v1",
    "public.idis_task7_approved_erasure_context_v1(uuid,uuid)",
    "SECURITY DEFINER",
    "idis_erasure_function_owner",
    ("search_path=pg_catalog, pg_temp",),
    "idis_erasure_executor",
    ("idis.tenant_id", "idis.erasure_context"),
    ("REQUESTED", "FAILED"),
    "transaction-local-only",
)
_ERASURE_STRICT_PROOF_TRIGGER = (
    "public.strict_preflight_proofs",
    "strict_preflight_proofs_immutability",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    False,
    False,
)
_ERASURE_SELECTION_TRIGGER = (
    "public.run_methodology_selections",
    "trg_run_methodology_selections_guard",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    False,
    False,
)
_ERASURE_COVERAGE_TRIGGER = (
    "public.methodology_coverage_records",
    "trg_methodology_coverage_scope",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    False,
    False,
)
_ERASURE_IMMUTABLE_TRIGGERS = tuple(
    (
        f"public.{table}",
        (
            "trg_methodology_result_factual_outputs_immutable"
            if table == "methodology_result_factual_outputs"
            else f"trg_{table}_immutable"
        ),
        "BEFORE",
        ("INSERT", "UPDATE", "DELETE"),
        "ROW",
        "O",
        False,
        False,
    )
    for table in (
        "methodology_extraction_tasks",
        "methodology_extraction_task_spans",
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
        "methodology_result_claim_links",
        "methodology_result_evidence_links",
        "methodology_result_sanad_links",
        "methodology_result_defect_links",
    )
)
_ERASURE_ATTEMPT_TRIGGER = (
    "public.methodology_extraction_attempts",
    "trg_methodology_extraction_attempts_lifecycle",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    False,
    False,
)
_ERASURE_OUTPUT_VALIDATION_TRIGGERS = (
    (
        "public.methodology_extraction_results",
        "trg_methodology_extraction_results_outputs",
        "AFTER",
        ("INSERT", "UPDATE"),
        "ROW",
        "O",
        True,
        True,
    ),
    (
        "public.methodology_result_factual_outputs",
        "trg_methodology_result_factual_outputs_count",
        "AFTER",
        ("INSERT", "UPDATE", "DELETE"),
        "ROW",
        "O",
        True,
        True,
    ),
)
ERASURE_LEGACY_GUARD_REPLACEMENTS = (
    (
        "0031",
        "public.idis_prevent_strict_preflight_proof_mutation()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (_ERASURE_STRICT_PROOF_TRIGGER,),
        "1a05ce8af35fe5102cba13e3198cdf92e82b966a15ea88552024fc7244c7e139",
    ),
    (
        "0036",
        "public.idis_task5_selection_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (_ERASURE_SELECTION_TRIGGER,),
        "e4504847befc282ffbfce6d6c123fef5eaf1aac12b2478d496247a8f2df42956",
    ),
    (
        "0036",
        "public.idis_task5_coverage_scope_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (_ERASURE_COVERAGE_TRIGGER,),
        "e135a1c656c7d5015f901276daa3b058e042b7d2573928b0090ddd7e93e5ba4f",
    ),
    (
        "0037",
        "public.idis_task6_erasure_requested(uuid,uuid)",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (),
        "66e7319519dbfd4c1b94fd56ed5990b9c9046c4e60f3858034f6e374e47e3de8",
    ),
    (
        "0037",
        "public.idis_task6_immutable_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        _ERASURE_IMMUTABLE_TRIGGERS,
        "6cd61a832cf2af65c911480ce4a6534350a9500b6d526f47354b3d7c9c1059cf",
    ),
    (
        "0037",
        "public.idis_task6_attempt_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (_ERASURE_ATTEMPT_TRIGGER,),
        "580389555daf84edd33739439dc46c1203781d681f5d1a30d86e6904f851b85e",
    ),
    (
        "0037",
        "public.idis_task6_validate_result_outputs()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        _ERASURE_OUTPUT_VALIDATION_TRIGGERS,
        "b55d3fb0981fc7a8e5a282cec1c672975e5ac5697c55aa7a8b129b97030ad50e",
    ),
)
ERASURE_INDIRECT_DELETE_PARENTS = {
    "calc_sanads": ("deterministic_calculations", "calc_id"),
    "run_steps": ("runs", "run_id"),
    "human_gate_actions": ("human_gates", "gate_id"),
}

NATIVE_ROOT_BRANCHES = (
    (
        "methodology_truth_dashboard_rows",
        "dashboard_row_id",
        "METHODOLOGY_TRUTH_DASHBOARD_ROW",
        "METHODOLOGY_TRUTH_DASHBOARD",
        "__step__",
        None,
    ),
    (
        "company_identity_outputs",
        "identity_output_id",
        "COMPANY_IDENTITY_OUTPUT",
        "METHODOLOGY_COMPANY_IDENTITY_PACKAGE",
        "__step__",
        None,
    ),
    (
        "enrichment_provider_outcomes",
        "provider_outcome_id",
        "ENRICHMENT_PROVIDER_OUTCOME",
        "ENRICHMENT",
        None,
        "provider_id",
    ),
    (
        "enrichment_normalized_facts",
        "fact_id",
        "ENRICHMENT_FACT",
        "ENRICHMENT",
        None,
        "provider_id",
    ),
    (
        "graph_projection_results",
        "graph_result_id",
        "GRAPH_PROJECTION_RESULT",
        "GRAPH_EVIDENCE",
        None,
        "namespace",
    ),
    (
        "rag_index_results",
        "rag_index_result_id",
        "RAG_INDEX_RESULT",
        "RAG_EVIDENCE",
        None,
        "namespace",
    ),
    (
        "rag_query_results",
        "rag_query_result_id",
        "RAG_QUERY_RESULT",
        "RAG_EVIDENCE",
        None,
        "namespace",
    ),
    (
        "external_intelligence_conflict_plans",
        "conflict_plan_id",
        "CONFLICT_PLAN",
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "__step__",
        None,
    ),
    (
        "external_intelligence_conflict_executions",
        "conflict_execution_id",
        "CONFLICT_EXECUTION",
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "__step__",
        None,
    ),
    (
        "external_intelligence_conflict_findings",
        "conflict_finding_id",
        "CONFLICT_FINDING",
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "__step__",
        None,
    ),
    (
        "validated_evidence_package_references",
        "vep_reference_id",
        "VEP_CONTENT_REFERENCE",
        "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE",
        "__step__",
        None,
    ),
    (
        "debate_role_outputs",
        "debate_role_output_id",
        "DEBATE_ROLE_OUTPUT",
        "DEBATE",
        None,
        "role",
    ),
    (
        "analysis_agent_outputs",
        "analysis_output_id",
        "ANALYSIS_AGENT_OUTPUT",
        "ANALYSIS",
        None,
        "role",
    ),
    (
        "scoring_outputs",
        "scoring_output_id",
        "SCORING_OUTPUT",
        "SCORING",
        "__step__",
        None,
    ),
    (
        "layer2_readiness_outputs",
        "readiness_output_id",
        "LAYER2_READINESS_OUTPUT",
        "METHODOLOGY_LAYER2_READINESS_PACKAGE",
        "__step__",
        None,
    ),
    (
        "layer2_role_outputs",
        "layer2_role_output_id",
        "LAYER2_ROLE_OUTPUT",
        "LAYER2_IC_CHALLENGE",
        None,
        "role",
    ),
    (
        "generic_extraction_outputs",
        "generic_output_id",
        "GENERIC_EXTRACTION_OUTPUT",
        "EXTRACT",
        "__step__",
        None,
    ),
    (
        "run_claim_grading_links",
        "grading_link_id",
        "RUN_CLAIM_GRADING_LINK",
        "GRADE",
        "__step__",
        None,
    ),
    (
        "generic_extraction_claim_links",
        "generic_claim_link_id",
        "METHODOLOGY_CLAIM_LINK",
        "EXTRACT",
        "__step__",
        None,
    ),
    (
        "generic_extraction_evidence_links",
        "generic_evidence_link_id",
        "METHODOLOGY_EVIDENCE_LINK",
        "EXTRACT",
        "__step__",
        None,
    ),
)

AUTHORITY_TARGET_TABLES: dict[str, tuple[str, ...]] = {
    "dashboard-root-equation-v1": (
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
    ),
    "graph-rag-namespace-v1": (
        "graph_projection_results",
        "rag_index_results",
        "rag_query_results",
    ),
    "rag-query-parent-coordinate-v1": ("rag_query_results",),
    "layer2-readiness-v1": ("layer2_readiness_outputs",),
    "extract-rag-context-coordinate-v1": ("generic_extraction_outputs",),
    "debate-muhasabah-exact-one-v1": ("debate_role_outputs", "muhasabah_records"),
    "analysis-muhasabah-exact-one-v1": ("analysis_agent_outputs", "muhasabah_records"),
    "layer2-muhasabah-exact-one-v1": ("layer2_role_outputs", "muhasabah_records"),
    "grading-dispatch-and-consistency-v1": (
        "run_claim_grading_links",
        "methodology_result_claim_links",
        "generic_extraction_claim_links",
    ),
    "scoring-binary64-finite-v1": ("scoring_output_dimensions",),
    "scoring-binary64-negative-zero-v1": ("scoring_output_dimensions",),
    "approved-erasure-append-only-v1": FUTURE_RELATION_TABLES,
    "native-root-exact-one-v1": (
        "run_output_objects",
        *FUTURE_NATIVE_TABLES,
        "generic_extraction_claim_links",
        "generic_extraction_evidence_links",
    ),
    "identity-identifier-projection-v1": (
        "company_identity_outputs",
        "company_identity_identifiers",
    ),
    "scoring-dimension-projection-v1": (
        "scoring_outputs",
        "scoring_output_dimensions",
    ),
    "generic-claim-projection-v1": (
        "generic_extraction_claim_links",
        "run_output_objects",
    ),
    "generic-evidence-projection-v1": (
        "generic_extraction_evidence_links",
        "run_output_objects",
    ),
}

AUTHORITY_PARENT_TABLES: dict[str, tuple[str, ...]] = {
    "dashboard-root-equation-v1": (
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
        "methodology_coverage_records",
        "methodology_questions",
        "methodology_extraction_tasks",
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
        "methodology_result_evidence_links",
        "methodology_result_sanad_links",
        "methodology_result_defect_links",
        "deterministic_calculations",
        "run_output_objects",
    ),
    "graph-rag-namespace-v1": (),
    "rag-query-parent-coordinate-v1": ("rag_index_results",),
    "layer2-readiness-v1": (),
    "extract-rag-context-coordinate-v1": ("run_step_outputs",),
    "debate-muhasabah-exact-one-v1": ("debate_role_outputs", "muhasabah_records"),
    "analysis-muhasabah-exact-one-v1": ("analysis_agent_outputs", "muhasabah_records"),
    "layer2-muhasabah-exact-one-v1": ("layer2_role_outputs", "muhasabah_records"),
    "grading-dispatch-and-consistency-v1": (
        "run_claim_grading_links",
        "methodology_result_claim_links",
        "generic_extraction_claim_links",
        "run_output_objects",
        "claims",
        "sanads",
        "methodology_result_sanad_links",
    ),
    "scoring-binary64-finite-v1": (),
    "scoring-binary64-negative-zero-v1": (),
    "approved-erasure-append-only-v1": ("erasure_requests",),
    "native-root-exact-one-v1": (
        "run_output_objects",
        *FUTURE_NATIVE_TABLES,
        "generic_extraction_claim_links",
        "generic_extraction_evidence_links",
        "run_step_outputs",
    ),
    "identity-identifier-projection-v1": (
        "company_identity_outputs",
        "company_identity_identifiers",
    ),
    "scoring-dimension-projection-v1": (
        "scoring_outputs",
        "scoring_output_dimensions",
    ),
    "generic-claim-projection-v1": (
        "generic_extraction_claim_links",
        "run_output_objects",
        "run_step_outputs",
        "generic_extraction_outputs",
        "claims",
    ),
    "generic-evidence-projection-v1": (
        "generic_extraction_evidence_links",
        "run_output_objects",
        "run_step_outputs",
        "generic_extraction_outputs",
        "generic_extraction_claim_links",
        "evidence_items",
        "document_spans",
    ),
}

GUARD_TEST_PREFIX = "tests/slice100_task7/test_sql_authority_guards_postgres_red.py::"
FLOAT_TEST_PREFIX = "tests/slice100_task7/test_sql_authority_float8_postgres_red.py::"

AUTHORITY_RED_CASES: dict[str, tuple[str, str, str]] = {
    "dashboard-root-equation-v1": (
        GUARD_TEST_PREFIX + "test_dashboard_root_equation_accepts_legal_row[gap-no-calculation]",
        GUARD_TEST_PREFIX
        + "test_dashboard_root_equation_rejects_illegal_row[answered-missing-result]",
        GUARD_TEST_PREFIX
        + "test_dashboard_root_equation_rejects_direct_sql_bypass[gap-wrong-outcome]",
    ),
    "graph-rag-namespace-v1": (
        GUARD_TEST_PREFIX + "test_graph_rag_namespace_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_graph_rag_namespace_rejects_illegal_row[external-null-provider-"
        "graph_projection_results-graph_result_id]",
        GUARD_TEST_PREFIX
        + "test_graph_rag_namespace_rejects_direct_sql_bypass[graph_projection_results-"
        "graph_result_id]",
    ),
    "rag-query-parent-coordinate-v1": (
        GUARD_TEST_PREFIX + "test_rag_query_parent_coordinate_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_rag_query_parent_coordinate_rejects_illegal_row[namespace]",
        GUARD_TEST_PREFIX + "test_rag_query_parent_coordinate_rejects_direct_sql_bypass[namespace]",
    ),
    "layer2-readiness-v1": (
        GUARD_TEST_PREFIX + "test_layer2_readiness_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_layer2_readiness_rejects_illegal_row[status]",
        GUARD_TEST_PREFIX + "test_layer2_readiness_rejects_direct_sql_bypass[status]",
    ),
    "extract-rag-context-coordinate-v1": (
        GUARD_TEST_PREFIX + "test_extract_rag_context_coordinate_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_extract_rag_context_coordinate_rejects_illegal_row[step]",
        GUARD_TEST_PREFIX + "test_extract_rag_context_coordinate_rejects_direct_sql_bypass[step]",
    ),
    "debate-muhasabah-exact-one-v1": (
        GUARD_TEST_PREFIX + "test_debate_muhasabah_exact_one_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_debate_muhasabah_exact_one_rejects_illegal_row[duplicate]",
        GUARD_TEST_PREFIX + "test_debate_muhasabah_exact_one_rejects_direct_sql_bypass[duplicate]",
    ),
    "analysis-muhasabah-exact-one-v1": (
        GUARD_TEST_PREFIX + "test_analysis_muhasabah_exact_one_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_analysis_muhasabah_exact_one_rejects_illegal_row[duplicate]",
        GUARD_TEST_PREFIX
        + "test_analysis_muhasabah_exact_one_rejects_direct_sql_bypass[duplicate]",
    ),
    "layer2-muhasabah-exact-one-v1": (
        GUARD_TEST_PREFIX + "test_layer2_muhasabah_exact_one_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_layer2_muhasabah_exact_one_rejects_illegal_row[duplicate]",
        GUARD_TEST_PREFIX + "test_layer2_muhasabah_exact_one_rejects_direct_sql_bypass[duplicate]",
    ),
    "grading-dispatch-and-consistency-v1": (
        GUARD_TEST_PREFIX + "test_grading_dispatch_and_consistency_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_grading_dispatch_and_consistency_rejects_illegal_row",
        GUARD_TEST_PREFIX + "test_grading_dispatch_and_consistency_rejects_direct_sql_bypass",
    ),
    "scoring-binary64-finite-v1": (
        FLOAT_TEST_PREFIX + "test_direct_sql_accepts_binary64_boundaries",
        FLOAT_TEST_PREFIX
        + "test_direct_sql_rejects_every_nonfinite_binary64_value['NaN'::double precision]",
        FLOAT_TEST_PREFIX
        + "test_direct_sql_rejects_finite_out_of_range_values[-0.000001::double precision-"
        "0.5::double precision]",
    ),
    "scoring-binary64-negative-zero-v1": (
        FLOAT_TEST_PREFIX + "test_direct_sql_normalizes_negative_zero_to_positive_zero_bits",
        FLOAT_TEST_PREFIX + "test_direct_sql_update_normalizes_negative_zero_to_positive_zero_bits",
        GUARD_TEST_PREFIX + "test_scoring_binary64_negative_zero_rejects_direct_sql_bypass",
    ),
    "approved-erasure-append-only-v1": (
        GUARD_TEST_PREFIX + "test_approved_erasure_append_only_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_approved_erasure_append_only_rejects_illegal_row",
        GUARD_TEST_PREFIX + "test_approved_erasure_append_only_rejects_direct_sql_bypass",
    ),
    "native-root-exact-one-v1": (
        GUARD_TEST_PREFIX
        + "test_native_root_exact_one_accepts_legal_row[methodology_truth_dashboard_rows]",
        GUARD_TEST_PREFIX + "test_native_root_exact_one_rejects_illegal_row[missing-child]",
        GUARD_TEST_PREFIX + "test_native_root_exact_one_rejects_direct_sql_bypass[missing-child]",
    ),
    "identity-identifier-projection-v1": (
        GUARD_TEST_PREFIX + "test_identity_identifier_projection_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_identity_identifier_projection_rejects_illegal_row",
        GUARD_TEST_PREFIX + "test_identity_identifier_projection_rejects_direct_sql_bypass",
    ),
    "scoring-dimension-projection-v1": (
        GUARD_TEST_PREFIX + "test_scoring_dimension_projection_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_scoring_dimension_projection_rejects_illegal_row",
        GUARD_TEST_PREFIX + "test_scoring_dimension_projection_rejects_direct_sql_bypass",
    ),
    "generic-claim-projection-v1": (
        GUARD_TEST_PREFIX + "test_generic_claim_projection_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_generic_claim_projection_rejects_illegal_row[producer-step]",
        GUARD_TEST_PREFIX
        + "test_generic_claim_projection_rejects_direct_sql_bypass[producer-step]",
    ),
    "generic-evidence-projection-v1": (
        GUARD_TEST_PREFIX + "test_generic_evidence_projection_accepts_legal_row",
        GUARD_TEST_PREFIX + "test_generic_evidence_projection_rejects_illegal_row[producer-step]",
        GUARD_TEST_PREFIX
        + "test_generic_evidence_projection_rejects_direct_sql_bypass[producer-step]",
    ),
}

FUTURE_NATIVE_V1_FIELDS = (
    "authority_ordinal",
    "schema_id",
    "schema_version",
    "planned_table",
    "primary_key",
    "scope_columns",
    "immutable_fields",
    "check_tuple",
    "reference_fields",
    "unique_tuple",
    "child_relation_tuple",
    "rls_tuple",
    "erasure_ordinal",
    "subrole_field",
    "namespace_field",
    "schema_sha256",
)
FUTURE_REFERENCE_V1_FIELDS = (
    "relation_ordinal",
    "table_name",
    "column_tuple",
    "consumer_parent_tuple",
    "reference_kind_tuple",
    "check_tuple",
    "foreign_key_tuple",
    "unique_tuple",
    "index_tuple",
    "rls_tuple",
    "erasure_ordinal",
    "uuid_namespace",
    "uuid_name_tuple_schema",
    "schema_sha256",
)
FUTURE_CHILD_V1_FIELDS = (
    "relation_ordinal",
    "table_name",
    "column_tuple",
    "check_tuple",
    "foreign_key_tuple",
    "unique_tuple",
    "index_tuple",
    "rls_tuple",
    "erasure_ordinal",
    "uuid_namespace",
    "uuid_name_tuple_schema",
    "projection_guard",
    "append_only_trigger",
    "schema_sha256",
)
SQL_AUTHORITY_FIELDS = (
    "authority_ordinal",
    "authority_id",
    "authority_version",
    "authority_kind",
    "target_tuple",
    "declared_parent_relation_tuple",
    "body_sql_utf8_hex_tuple",
    "legal_red_case_tuple",
    "illegal_red_case_tuple",
    "direct_sql_red_case_tuple",
    "expected_sha256",
)
ERASURE_EXECUTION_FIELDS = (
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

REFERENCE_AUTHORITIES = (
    (
        0,
        "company_identity_input_references",
        "identity_reference_id",
        "identity_output_id",
        "UUID NOT NULL",
        "company_identity_outputs",
        "uq_v2_company_identity_outputs_reference_parent",
        ("METHODOLOGY_EVIDENCE", "METHODOLOGY_CALCULATION", "UNIFIED_CALCULATION"),
        "v2_identity_input_ref",
        26,
    ),
    (
        1,
        "generic_extraction_input_references",
        "generic_reference_id",
        "generic_output_id",
        "UUID NOT NULL",
        "generic_extraction_outputs",
        "uq_v2_generic_extraction_outputs_reference_parent",
        ("SOURCE_HANDOFF", "PREFLIGHT", "RAG_CONTEXT"),
        "v2_generic_input_ref",
        27,
    ),
    (
        2,
        "graph_result_input_references",
        "graph_reference_id",
        "graph_result_id",
        "UUID NOT NULL",
        "graph_projection_results",
        "uq_v2_graph_projection_results_reference_parent",
        (
            "SOURCE_HANDOFF",
            "PREFLIGHT",
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "APPLICABLE_COVERAGE",
            "IDENTITY",
            "PROVIDER_OUTCOME",
            "PROVIDER_FACT",
        ),
        "v2_graph_input_ref",
        28,
    ),
    (
        3,
        "rag_index_input_references",
        "rag_index_reference_id",
        "rag_index_result_id",
        "UUID NOT NULL",
        "rag_index_results",
        "uq_v2_rag_index_results_reference_parent",
        (
            "SOURCE_HANDOFF",
            "PREFLIGHT",
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "APPLICABLE_COVERAGE",
            "IDENTITY",
            "PROVIDER_OUTCOME",
            "PROVIDER_FACT",
        ),
        "v2_rag_index_input_ref",
        29,
    ),
    (
        4,
        "rag_query_input_references",
        "rag_query_reference_id",
        "rag_query_result_id",
        "UUID NOT NULL",
        "rag_query_results",
        "uq_v2_rag_query_results_reference_parent",
        ("RAG_INDEX_RESULT", "GRAPH_INTERNAL", "GRAPH_EXTERNAL"),
        "v2_rag_query_input_ref",
        30,
    ),
    (
        5,
        "conflict_input_references",
        "conflict_reference_id",
        "conflict_plan_id",
        "UUID NOT NULL",
        "external_intelligence_conflict_plans",
        "uq_v2_conflict_plans_reference_parent",
        (
            "IDENTITY",
            "PROVIDER_OUTCOME",
            "PROVIDER_FACT",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "GRAPH_EXTERNAL",
            "RAG_EXTERNAL",
        ),
        "v2_conflict_input_ref",
        31,
    ),
    (
        6,
        "evidence_trust_court_input_references",
        "court_reference_id",
        "finding_id",
        "VARCHAR(255) NOT NULL",
        "evidence_trust_findings",
        "uq_v2_evidence_trust_findings_reference_parent",
        (
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "APPLICABLE_COVERAGE",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "CONFLICT_INTERNAL",
        ),
        "v2_court_input_ref",
        32,
    ),
    (
        7,
        "validated_evidence_package_input_references",
        "vep_input_reference_id",
        "package_id",
        "UUID NOT NULL",
        "validated_evidence_packages",
        "uq_v2_validated_evidence_packages_reference_parent",
        (
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "APPLICABLE_COVERAGE",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "CONFLICT_INTERNAL",
            "COURT",
        ),
        "v2_vep_input_ref",
        33,
    ),
    (
        8,
        "debate_role_input_references",
        "debate_reference_id",
        "debate_role_output_id",
        "UUID NOT NULL",
        "debate_role_outputs",
        "uq_v2_debate_role_outputs_reference_parent",
        (
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "APPLICABLE_COVERAGE",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "CONFLICT_INTERNAL",
            "COURT",
            "VEP",
        ),
        "v2_debate_input_ref",
        34,
    ),
    (
        9,
        "analysis_agent_input_references",
        "analysis_reference_id",
        "analysis_output_id",
        "UUID NOT NULL",
        "analysis_agent_outputs",
        "uq_v2_analysis_agent_outputs_reference_parent",
        (
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "APPLICABLE_COVERAGE",
            "IDENTITY",
            "PROVIDER_OUTCOME",
            "PROVIDER_MISS",
            "PROVIDER_FACT",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "GRAPH_EXTERNAL",
            "RAG_EXTERNAL",
            "CONFLICT_INTERNAL",
            "CONFLICT_EXTERNAL",
            "COURT",
            "VEP",
            "DEBATE",
        ),
        "v2_analysis_input_ref",
        35,
    ),
    (
        10,
        "scoring_input_references",
        "scoring_reference_id",
        "scoring_output_id",
        "UUID NOT NULL",
        "scoring_outputs",
        "uq_v2_scoring_outputs_reference_parent",
        (
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "APPLICABLE_COVERAGE",
            "PROVIDER_OUTCOME",
            "PROVIDER_FACT",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "GRAPH_EXTERNAL",
            "RAG_EXTERNAL",
            "CONFLICT_INTERNAL",
            "CONFLICT_EXTERNAL",
            "COURT",
            "VEP",
            "DEBATE",
            "ANALYSIS",
        ),
        "v2_scoring_input_ref",
        36,
    ),
    (
        11,
        "layer2_readiness_input_references",
        "readiness_reference_id",
        "readiness_output_id",
        "UUID NOT NULL",
        "layer2_readiness_outputs",
        "uq_v2_layer2_readiness_outputs_reference_parent",
        (
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "APPLICABLE_COVERAGE",
            "PROVIDER_OUTCOME",
            "PROVIDER_FACT",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "GRAPH_EXTERNAL",
            "RAG_EXTERNAL",
            "CONFLICT_INTERNAL",
            "CONFLICT_EXTERNAL",
            "COURT",
            "VEP",
            "DEBATE",
            "ANALYSIS",
            "SCORING",
        ),
        "v2_readiness_input_ref",
        37,
    ),
    (
        12,
        "layer2_role_input_references",
        "layer2_reference_id",
        "layer2_role_output_id",
        "UUID NOT NULL",
        "layer2_role_outputs",
        "uq_v2_layer2_role_outputs_reference_parent",
        (
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "APPLICABLE_COVERAGE",
            "PROVIDER_OUTCOME",
            "PROVIDER_FACT",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "GRAPH_EXTERNAL",
            "RAG_EXTERNAL",
            "CONFLICT_INTERNAL",
            "CONFLICT_EXTERNAL",
            "COURT",
            "VEP",
            "DEBATE",
            "ANALYSIS",
            "SCORING",
            "READINESS",
        ),
        "v2_layer2_input_ref",
        38,
    ),
    (
        13,
        "deliverable_artifact_input_references",
        "deliverable_reference_id",
        "deliverable_id",
        "UUID NOT NULL",
        "deliverables",
        "uq_v2_deliverables_reference_parent",
        (
            "PACKAGE_SOURCE",
            "SOURCE_HANDOFF",
            "COVERAGE",
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "IDENTITY",
            "PROVIDER_OUTCOME",
            "PROVIDER_MISS",
            "PROVIDER_FACT",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "GRAPH_EXTERNAL",
            "RAG_EXTERNAL",
            "CONFLICT_INTERNAL",
            "CONFLICT_EXTERNAL",
            "COURT",
            "VEP",
            "DEBATE",
            "ANALYSIS",
            "SCORING",
            "LAYER2",
        ),
        "v2_deliverable_input_ref",
        39,
    ),
    (
        14,
        "step_receipt_input_references",
        "receipt_reference_id",
        "step_output_id",
        "UUID NOT NULL",
        "run_step_outputs",
        "uq_task7_run_step_outputs_scoped_output",
        (
            "SOURCE_INVENTORY",
            "SOURCE_HANDOFF",
            "SEMANTIC_INPUT",
            "PREFLIGHT",
            "APPLICABLE_COVERAGE",
            "COVERAGE",
            "METHOD_INTERNAL",
            "GENERIC_INTERNAL",
            "RAG_CONTEXT",
            "IDENTITY",
            "PROVIDER_INPUT",
            "PROVIDER_OUTCOME",
            "PROVIDER_MISS",
            "PROVIDER_FACT",
            "GRAPH_INTERNAL",
            "RAG_INTERNAL",
            "GRAPH_EXTERNAL",
            "RAG_EXTERNAL",
            "CONFLICT_INTERNAL",
            "CONFLICT_EXTERNAL",
            "COURT",
            "VEP",
            "DEBATE",
            "ANALYSIS",
            "SCORING",
            "READINESS",
            "LAYER2",
            "RECONCILIATION_INPUT",
            "BUNDLE_MEMBERSHIP",
            "METHODOLOGY_EVIDENCE",
            "METHODOLOGY_CALCULATION",
            "UNIFIED_CALCULATION",
        ),
        "v2_receipt_input_ref",
        40,
    ),
)

UUID_NAMESPACE = "8caa2d0a-4588-40f1-96ba-60401462ae91"
DASHBOARD_ROOT_LINK_NAMESPACE = "58a9370f-69f9-5873-b6b7-508272aee80e"


class MaterializationError(RuntimeError):
    """Stable fail-closed error emitted by the one-shot materializer."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


class MaterializationBlockedError(MaterializationError):
    """The normative inputs are incomplete, so no artifact may be emitted."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _h(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _authority_hash(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "schema_sha256"}
    return hashlib.sha256(AUTHORITY_ROW_SCHEMA_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest()


def _sql_authority_hash(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    return hashlib.sha256(SQL_AUTHORITY_ROW_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest()


def _erasure_execution_hash(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    return hashlib.sha256(ERASURE_EXECUTION_ROW_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest()


def _registry_hash(domain: str, rows: object) -> str:
    return _h([domain, rows])


def _hex(value: object) -> str:
    return _canonical_bytes(value).hex()


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise MaterializationError(
                "TASK7_V2_INPUT_INVALID",
                f"duplicate JSON object key {key!r}",
            )
        value[key] = child
    return value


def _reject_noninteger_number(value: str) -> Any:
    raise MaterializationError(
        "TASK7_V2_INPUT_INVALID",
        f"non-integer JSON number {value!r} is forbidden",
    )


def _load_rejected_artifact(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    actual_sha512 = hashlib.sha512(raw).hexdigest()
    if actual_sha512 != REJECTED_V2_ARTIFACT_SHA512:
        raise MaterializationError(
            "TASK7_V2_INPUT_HASH_MISMATCH",
            f"expected SHA-512 {REJECTED_V2_ARTIFACT_SHA512}, observed {actual_sha512}",
        )
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
        raise MaterializationError(
            "TASK7_V2_INPUT_INVALID",
            "rejected artifact must be BOM-free LF-only UTF-8",
        )
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=_reject_noninteger_number,
            parse_constant=_reject_noninteger_number,
        )
    except UnicodeDecodeError as exc:
        raise MaterializationError(
            "TASK7_V2_INPUT_INVALID",
            "rejected artifact is not UTF-8",
        ) from exc
    if not isinstance(value, dict) or tuple(value) != V2_SECTION_ORDER:
        raise MaterializationError(
            "TASK7_V2_INPUT_INVALID",
            "rejected artifact section order is not the reviewed v2 order",
        )
    return value


def _load_module(path: Path) -> ModuleType:
    module_name = "task7_v3_reviewed_authority_materializer_input"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load reviewed authority helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _column_rows(definitions: tuple[tuple[str, str], ...]) -> list[list[Any]]:
    return [[ordinal, name, definition] for ordinal, (name, definition) in enumerate(definitions)]


def _fk(
    name: str,
    child: tuple[str, ...],
    parent_table: str,
    parent_constraint: str,
    parent: tuple[str, ...],
    support: str,
) -> list[Any]:
    return [
        name,
        list(child),
        parent_table,
        parent_constraint,
        list(parent),
        "NO ACTION",
        "NO ACTION",
        support,
    ]


def _unique(name: str, columns: tuple[str, ...], *, nulls: bool = False) -> list[Any]:
    return [name, list(columns), nulls]


def _index(
    name: str,
    columns: tuple[str, ...],
    *,
    predicate: str | None = None,
    unique: bool = False,
) -> list[Any]:
    return [name, list(columns), predicate, unique]


def _rls(table: str) -> list[list[str]]:
    return [
        [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY {table}_tenant_isolation ON {table} AS PERMISSIVE FOR ALL "
            f"TO PUBLIC USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})",
            f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC; GRANT "
            f"SELECT,INSERT,UPDATE,DELETE ON TABLE {table} TO <configured_app_role>",
        ]
    ]


def _authority_ids_for_table(table: str) -> list[str]:
    return [
        authority_id
        for authority_id in AUTHORITY_IDS
        if table in AUTHORITY_TARGET_TABLES[authority_id]
    ]


def _reference_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (
        ordinal,
        table,
        reference_id,
        consumer_id,
        consumer_definition,
        parent_table,
        parent_constraint,
        reference_kinds,
        prefix,
        phase,
    ) in REFERENCE_AUTHORITIES:
        parent_output_column = (
            "output_object_id"
            if table == "step_receipt_input_references"
            else "run_output_object_id"
        )
        consumer_child = (
            "tenant_id",
            "deal_id",
            "run_id",
            consumer_id,
            "consumer_output_object_id",
        )
        consumer_parent = (
            "tenant_id",
            "deal_id",
            "run_id",
            consumer_id,
            parent_output_column,
        )
        columns = _column_rows(
            (
                (reference_id, "UUID NOT NULL"),
                ("tenant_id", "UUID NOT NULL"),
                ("deal_id", "UUID NOT NULL"),
                ("run_id", "UUID NOT NULL"),
                (consumer_id, consumer_definition),
                ("consumer_output_object_id", "UUID NOT NULL"),
                ("producer_type", "TEXT NOT NULL"),
                ("producer_input_binding_id", "UUID NULL"),
                ("producer_output_object_id", "UUID NULL"),
                ("producer_identity_sha256", "TEXT NOT NULL"),
                ("producer_value_sha256", "TEXT NOT NULL"),
                ("reference_kind", "TEXT NOT NULL"),
                ("reference_ordinal", "INTEGER NOT NULL"),
                ("lease_owner_id", "UUID NOT NULL"),
                ("lease_fencing_token", "BIGINT NOT NULL"),
                ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()"),
            )
        )
        kinds_sql = ",".join(f"'{kind}'" for kind in reference_kinds)
        checks = [
            [
                f"ck_{prefix}_producer",
                "producer_type IN ('INPUT_BINDING','OUTPUT_OBJECT') AND "
                "((producer_type='INPUT_BINDING' AND producer_input_binding_id IS NOT NULL "
                "AND producer_output_object_id IS NULL) OR "
                "(producer_type='OUTPUT_OBJECT' AND producer_input_binding_id IS NULL "
                "AND producer_output_object_id IS NOT NULL))",
            ],
            [
                f"ck_{prefix}_hashes",
                "producer_identity_sha256~'^[0-9a-f]{64}$' AND "
                "producer_value_sha256~'^[0-9a-f]{64}$'",
            ],
            [f"ck_{prefix}_kind", f"reference_kind IN ({kinds_sql})"],
            [f"ck_{prefix}_ordinal", "reference_ordinal>=0"],
            [
                f"ck_{prefix}_lease",
                "lease_owner_id IS NOT NULL AND lease_fencing_token>0",
            ],
        ]
        consumer_index = f"ix_{prefix}_consumer"
        input_index = f"ix_{prefix}_input"
        output_index = f"ix_{prefix}_output"
        foreign_keys = [
            _fk(
                f"fk_{prefix}_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                f"uq_{prefix}_scope",
            ),
            _fk(
                f"fk_{prefix}_consumer",
                consumer_child,
                parent_table,
                parent_constraint,
                consumer_parent,
                consumer_index,
            ),
            _fk(
                f"fk_{prefix}_input",
                ("tenant_id", "deal_id", "run_id", "producer_input_binding_id"),
                "run_input_bindings",
                "uq_task7_run_input_bindings_scoped_id",
                ("tenant_id", "deal_id", "run_id", "input_binding_id"),
                input_index,
            ),
            _fk(
                f"fk_{prefix}_output",
                ("tenant_id", "deal_id", "run_id", "producer_output_object_id"),
                "run_output_objects",
                "uq_task7_run_output_objects_scope",
                ("tenant_id", "deal_id", "run_id", "output_object_id"),
                output_index,
            ),
        ]
        uniques = [
            _unique(f"pk_{prefix}", (reference_id,)),
            _unique(
                f"uq_{prefix}_scope",
                ("tenant_id", "deal_id", "run_id", reference_id),
            ),
            _unique(
                f"uq_{prefix}_coordinate",
                (
                    "tenant_id",
                    "run_id",
                    consumer_id,
                    "producer_type",
                    "producer_input_binding_id",
                    "producer_output_object_id",
                    "reference_kind",
                ),
                nulls=True,
            ),
            _unique(
                f"uq_{prefix}_ordinal",
                ("tenant_id", "run_id", consumer_id, "reference_ordinal"),
            ),
        ]
        indexes = [
            _index(consumer_index, consumer_child),
            _index(
                input_index,
                ("tenant_id", "deal_id", "run_id", "producer_input_binding_id"),
                predicate="producer_input_binding_id IS NOT NULL",
            ),
            _index(
                output_index,
                ("tenant_id", "deal_id", "run_id", "producer_output_object_id"),
                predicate="producer_output_object_id IS NOT NULL",
            ),
        ]
        row: dict[str, Any] = {
            "relation_ordinal": ordinal,
            "table_name": table,
            "column_tuple": columns,
            "consumer_parent_tuple": [
                list(consumer_child),
                parent_table,
                parent_constraint,
                list(consumer_parent),
            ],
            "reference_kind_tuple": list(reference_kinds),
            "check_tuple": checks,
            "foreign_key_tuple": foreign_keys,
            "unique_tuple": uniques,
            "index_tuple": indexes,
            "rls_tuple": _rls(table),
            "erasure_ordinal": phase,
            "uuid_namespace": UUID_NAMESPACE,
            "uuid_name_tuple_schema": [
                "future-input-reference-v2",
                "tenant_id",
                "deal_id",
                "run_id",
                "reference_table",
                "consumer_id",
                "consumer_output_object_id",
                "producer_type",
                "producer_id",
                "reference_kind",
                "reference_ordinal",
                "producer_identity_sha256",
                "producer_value_sha256",
            ],
            "sql_authority_id_tuple": _authority_ids_for_table(table),
        }
        row["schema_sha256"] = _authority_hash(row)
        rows.append(row)
    return rows


def _child_common_columns(
    primary_key: str,
    specific: tuple[tuple[str, str], ...],
) -> list[list[Any]]:
    return _column_rows(
        (
            (primary_key, "UUID NOT NULL"),
            ("tenant_id", "UUID NOT NULL"),
            ("deal_id", "UUID NOT NULL"),
            ("run_id", "UUID NOT NULL"),
            *specific,
            ("lease_owner_id", "UUID NOT NULL"),
            ("lease_fencing_token", "BIGINT NOT NULL"),
            ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()"),
        )
    )


def _child_row(
    *,
    ordinal: int,
    table: str,
    primary_key: str,
    parent_table: str,
    parent_id: str,
    parent_object_id: str,
    parent_constraint: str,
    prefix: str,
    phase: int,
    specific_columns: tuple[tuple[str, str], ...],
    checks: list[list[str]],
    foreign_keys: list[list[Any]],
    uniques: list[list[Any]],
    indexes: list[list[Any]],
    namespace: str,
    uuid_tuple: list[str],
    projection_guard: str,
) -> dict[str, Any]:
    run_index = f"ix_{prefix}_run"
    parent_index = f"ix_{prefix}_parent"
    parent_child = (
        "tenant_id",
        "deal_id",
        "run_id",
        parent_id,
        parent_object_id,
    )
    parent_columns = (
        "tenant_id",
        "deal_id",
        "run_id",
        parent_id,
        "run_output_object_id",
    )
    row: dict[str, Any] = {
        "relation_ordinal": ordinal,
        "table_name": table,
        "column_tuple": _child_common_columns(primary_key, specific_columns),
        "check_tuple": checks,
        "foreign_key_tuple": [
            _fk(
                f"fk_{prefix}_run",
                ("tenant_id", "deal_id", "run_id"),
                "runs",
                "uq_task7_runs_tenant_deal_run",
                ("tenant_id", "deal_id", "run_id"),
                run_index,
            ),
            _fk(
                f"fk_{prefix}_parent",
                parent_child,
                parent_table,
                parent_constraint,
                parent_columns,
                parent_index,
            ),
            *foreign_keys,
        ],
        "unique_tuple": [_unique(f"pk_{prefix}", (primary_key,)), *uniques],
        "index_tuple": [
            _index(run_index, ("tenant_id", "deal_id", "run_id")),
            _index(parent_index, parent_child),
            *indexes,
        ],
        "rls_tuple": _rls(table),
        "erasure_ordinal": phase,
        "uuid_namespace": namespace,
        "uuid_name_tuple_schema": uuid_tuple,
        "projection_guard": projection_guard,
        "append_only_trigger": f"trg_{prefix}_append_only",
        "sql_authority_id_tuple": _authority_ids_for_table(table),
    }
    row["schema_sha256"] = _authority_hash(row)
    return row


def _child_rows() -> list[dict[str, Any]]:
    identity = _child_row(
        ordinal=15,
        table="company_identity_identifiers",
        primary_key="identity_identifier_id",
        parent_table="company_identity_outputs",
        parent_id="identity_output_id",
        parent_object_id="identity_output_object_id",
        parent_constraint="uq_v2_company_identity_outputs_reference_parent",
        prefix="v2_identity_identifier",
        phase=59,
        specific_columns=(
            ("identity_output_id", "UUID NOT NULL"),
            ("identity_output_object_id", "UUID NOT NULL"),
            ("identifier_kind", "TEXT NOT NULL"),
            ("identifier_hash", "TEXT NOT NULL"),
            ("verification_provenance_sha256", "TEXT NOT NULL"),
            ("verification_status", "TEXT GENERATED ALWAYS AS ('VERIFIED') STORED"),
        ),
        checks=[
            [
                "ck_v2_identity_identifier_kind",
                "identifier_kind~'^[a-z][a-z0-9_]{0,63}$'",
            ],
            [
                "ck_v2_identity_identifier_hashes",
                "identifier_hash~'^[0-9a-f]{64}$' AND "
                "verification_provenance_sha256~'^[0-9a-f]{64}$'",
            ],
            [
                "ck_v2_identity_identifier_lease",
                "lease_owner_id IS NOT NULL AND lease_fencing_token>0",
            ],
        ],
        foreign_keys=[],
        uniques=[
            _unique(
                "uq_v2_identity_identifier_scope",
                ("tenant_id", "deal_id", "run_id", "identity_identifier_id"),
            ),
            _unique(
                "uq_v2_identity_identifier_coordinate",
                (
                    "tenant_id",
                    "run_id",
                    "identity_output_id",
                    "identifier_kind",
                    "identifier_hash",
                ),
            ),
        ],
        indexes=[],
        namespace="1a5be9e5-5cc5-5a16-8d3f-6f9fc4eeda53",
        uuid_tuple=[
            "company-identity-identifier-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "identity_output_id",
            "identifier_kind",
            "identifier_hash",
            "verification_provenance_sha256",
        ],
        projection_guard=(
            "CREATE CONSTRAINT TRIGGER ctrg_v2_identity_identifier_projection AFTER INSERT OR "
            "UPDATE OR DELETE ON public.company_identity_identifiers DEFERRABLE INITIALLY "
            "DEFERRED FOR EACH ROW EXECUTE FUNCTION "
            "public.idis_task7_identity_identifier_projection_v1()"
        ),
    )

    scoring_root_index = "ix_v2_scoring_dimension_root"
    scoring = _child_row(
        ordinal=16,
        table="scoring_output_dimensions",
        primary_key="scoring_dimension_id",
        parent_table="scoring_outputs",
        parent_id="scoring_output_id",
        parent_object_id="scoring_output_object_id",
        parent_constraint="uq_v2_scoring_outputs_reference_parent",
        prefix="v2_scoring_dimension",
        phase=60,
        specific_columns=(
            ("scoring_output_id", "UUID NOT NULL"),
            ("scoring_output_object_id", "UUID NOT NULL"),
            ("dimension_ordinal", "SMALLINT NOT NULL"),
            ("dimension_key", "TEXT NOT NULL"),
            ("score", "DOUBLE PRECISION NOT NULL"),
            ("confidence", "DOUBLE PRECISION NOT NULL"),
            ("rationale_sha256", "TEXT NOT NULL"),
            ("confidence_justification_sha256", "TEXT NOT NULL"),
            ("support_projection_sha256", "TEXT NOT NULL"),
            ("muhasabah_projection_sha256", "TEXT NOT NULL"),
        ),
        checks=[
            [
                "ck_v2_scoring_dimension_coordinate",
                "(dimension_ordinal=0 AND dimension_key='MARKET_ATTRACTIVENESS') OR "
                "(dimension_ordinal=1 AND dimension_key='TEAM_QUALITY') OR "
                "(dimension_ordinal=2 AND dimension_key='PRODUCT_DEFENSIBILITY') OR "
                "(dimension_ordinal=3 AND dimension_key='TRACTION_VELOCITY') OR "
                "(dimension_ordinal=4 AND dimension_key='FUND_THESIS_FIT') OR "
                "(dimension_ordinal=5 AND dimension_key='CAPITAL_EFFICIENCY') OR "
                "(dimension_ordinal=6 AND dimension_key='SCALABILITY') OR "
                "(dimension_ordinal=7 AND dimension_key='RISK_PROFILE')",
            ],
            [
                "ck_v2_scoring_dimension_binary64_finite",
                "score<>'NaN'::double precision AND score<>'Infinity'::double precision AND "
                "score<>'-Infinity'::double precision AND score>=0::double precision AND "
                "score<=1::double precision AND confidence<>'NaN'::double precision AND "
                "confidence<>'Infinity'::double precision AND "
                "confidence<>'-Infinity'::double precision AND "
                "confidence>=0::double precision AND confidence<=1::double precision",
            ],
            [
                "ck_v2_scoring_dimension_hashes",
                "rationale_sha256~'^[0-9a-f]{64}$' AND "
                "confidence_justification_sha256~'^[0-9a-f]{64}$' AND "
                "support_projection_sha256~'^[0-9a-f]{64}$' AND "
                "muhasabah_projection_sha256~'^[0-9a-f]{64}$'",
            ],
            [
                "ck_v2_scoring_dimension_lease",
                "lease_owner_id IS NOT NULL AND lease_fencing_token>0",
            ],
        ],
        foreign_keys=[],
        uniques=[
            _unique(
                "uq_v2_scoring_dimension_scope",
                ("tenant_id", "deal_id", "run_id", "scoring_dimension_id"),
            ),
            _unique(
                "uq_v2_scoring_dimension_key",
                ("tenant_id", "run_id", "scoring_output_id", "dimension_key"),
            ),
            _unique(
                "uq_v2_scoring_dimension_ordinal",
                ("tenant_id", "run_id", "scoring_output_id", "dimension_ordinal"),
            ),
        ],
        indexes=[
            _index(
                scoring_root_index,
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "scoring_output_id",
                    "scoring_output_object_id",
                ),
            )
        ],
        namespace="dc0541cd-f3de-5bf4-8b9a-09fd2d6647d6",
        uuid_tuple=[
            "scoring-output-dimension-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "scoring_output_id",
            "dimension_ordinal",
            "dimension_key",
            "binary64(score)",
            "binary64(confidence)",
            "rationale_sha256",
            "confidence_justification_sha256",
            "support_projection_sha256",
            "muhasabah_projection_sha256",
        ],
        projection_guard=(
            "CREATE CONSTRAINT TRIGGER ctrg_v2_scoring_dimension_projection AFTER INSERT OR "
            "UPDATE OR DELETE ON public.scoring_output_dimensions DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION public.idis_task7_scoring_dimension_projection_v1()"
        ),
    )

    generic_claim_root_index = "ix_v2_generic_claim_link_root"
    generic_claim_claim_index = "ix_v2_generic_claim_link_claim"
    generic_claim = _child_row(
        ordinal=17,
        table="generic_extraction_claim_links",
        primary_key="generic_claim_link_id",
        parent_table="generic_extraction_outputs",
        parent_id="generic_output_id",
        parent_object_id="generic_output_object_id",
        parent_constraint="uq_v2_generic_extraction_outputs_reference_parent",
        prefix="v2_generic_claim_link",
        phase=61,
        specific_columns=(
            ("generic_output_id", "UUID NOT NULL"),
            ("generic_output_object_id", "UUID NOT NULL"),
            ("run_output_object_id", "UUID NOT NULL"),
            ("run_output_object_class", "TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED"),
            (
                "run_output_object_kind",
                "TEXT GENERATED ALWAYS AS ('METHODOLOGY_CLAIM_LINK') STORED",
            ),
            ("claim_ordinal", "INTEGER NOT NULL"),
            ("claim_id", "UUID NOT NULL"),
            ("canonical_key_sha256", "TEXT NOT NULL"),
            ("reused_existing_claim", "BOOLEAN NOT NULL"),
        ),
        checks=[
            [
                "ck_v2_generic_claim_link_hash",
                "canonical_key_sha256~'^[0-9a-f]{64}$'",
            ],
            ["ck_v2_generic_claim_link_ordinal", "claim_ordinal>=0"],
            [
                "ck_v2_generic_claim_link_lease",
                "lease_owner_id IS NOT NULL AND lease_fencing_token>0",
            ],
        ],
        foreign_keys=[
            _fk(
                "fk_v2_generic_claim_link_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                generic_claim_root_index,
            ),
            _fk(
                "fk_v2_generic_claim_link_claim",
                ("tenant_id", "deal_id", "claim_id"),
                "claims",
                "ux_claims_task6_scope",
                ("tenant_id", "deal_id", "claim_id"),
                generic_claim_claim_index,
            ),
        ],
        uniques=[
            _unique("uq_v2_generic_claim_link_root", ("run_output_object_id",)),
            _unique(
                "uq_v2_generic_claim_link_scope",
                ("tenant_id", "deal_id", "run_id", "generic_claim_link_id"),
            ),
            _unique(
                "uq_v2_generic_claim_link_coordinate",
                ("tenant_id", "run_id", "generic_output_id", "claim_id"),
            ),
            _unique(
                "uq_v2_generic_claim_link_ordinal",
                ("tenant_id", "run_id", "generic_output_id", "claim_ordinal"),
            ),
            _unique(
                "uq_v2_generic_claim_evidence_parent",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "generic_output_id",
                    "generic_claim_link_id",
                    "claim_id",
                ),
            ),
        ],
        indexes=[
            _index(
                generic_claim_root_index,
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
            ),
            _index(
                generic_claim_claim_index,
                ("tenant_id", "deal_id", "claim_id"),
            ),
        ],
        namespace="a66eb885-f319-5498-87d4-a408d5364b59",
        uuid_tuple=[
            "generic-extraction-claim-link-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "generic_output_id",
            "claim_ordinal",
            "claim_id",
            "canonical_key_sha256",
            "reused_existing_claim",
        ],
        projection_guard=(
            "CREATE CONSTRAINT TRIGGER ctrg_v2_generic_claim_projection AFTER INSERT OR UPDATE "
            "OR DELETE ON public.generic_extraction_claim_links DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION public.idis_task7_generic_claim_projection_v1()"
        ),
    )

    evidence_root_index = "ix_v2_generic_evidence_link_root"
    evidence_claim_index = "ix_v2_generic_evidence_link_claim"
    evidence_item_index = "ix_v2_generic_evidence_link_evidence"
    evidence_span_index = "ix_v2_generic_evidence_link_span"
    generic_evidence = _child_row(
        ordinal=18,
        table="generic_extraction_evidence_links",
        primary_key="generic_evidence_link_id",
        parent_table="generic_extraction_outputs",
        parent_id="generic_output_id",
        parent_object_id="generic_output_object_id",
        parent_constraint="uq_v2_generic_extraction_outputs_reference_parent",
        prefix="v2_generic_evidence_link",
        phase=62,
        specific_columns=(
            ("generic_output_id", "UUID NOT NULL"),
            ("generic_output_object_id", "UUID NOT NULL"),
            ("run_output_object_id", "UUID NOT NULL"),
            ("run_output_object_class", "TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED"),
            (
                "run_output_object_kind",
                "TEXT GENERATED ALWAYS AS ('METHODOLOGY_EVIDENCE_LINK') STORED",
            ),
            ("generic_claim_link_id", "UUID NOT NULL"),
            ("claim_id", "UUID NOT NULL"),
            ("evidence_id", "UUID NOT NULL"),
            ("document_id", "UUID NOT NULL"),
            ("source_span_id", "UUID NOT NULL"),
            ("locator_sha256", "TEXT NOT NULL"),
            ("evidence_ordinal", "INTEGER NOT NULL"),
        ),
        checks=[
            ["ck_v2_generic_evidence_link_hash", "locator_sha256~'^[0-9a-f]{64}$'"],
            ["ck_v2_generic_evidence_link_ordinal", "evidence_ordinal>=0"],
            [
                "ck_v2_generic_evidence_link_lease",
                "lease_owner_id IS NOT NULL AND lease_fencing_token>0",
            ],
        ],
        foreign_keys=[
            _fk(
                "fk_v2_generic_evidence_link_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                evidence_root_index,
            ),
            _fk(
                "fk_v2_generic_evidence_link_claim",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "generic_output_id",
                    "generic_claim_link_id",
                    "claim_id",
                ),
                "generic_extraction_claim_links",
                "uq_v2_generic_claim_evidence_parent",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "generic_output_id",
                    "generic_claim_link_id",
                    "claim_id",
                ),
                evidence_claim_index,
            ),
            _fk(
                "fk_v2_generic_evidence_link_evidence",
                ("tenant_id", "deal_id", "evidence_id", "claim_id", "source_span_id"),
                "evidence_items",
                "uq_v2_evidence_item_generic_parent",
                ("tenant_id", "deal_id", "evidence_id", "claim_id", "source_span_id"),
                evidence_item_index,
            ),
            _fk(
                "fk_v2_generic_evidence_link_span",
                ("tenant_id", "deal_id", "document_id", "source_span_id"),
                "document_spans",
                "ux_document_spans_task6_scope",
                ("tenant_id", "deal_id", "document_id", "span_id"),
                evidence_span_index,
            ),
        ],
        uniques=[
            _unique("uq_v2_generic_evidence_link_root", ("run_output_object_id",)),
            _unique(
                "uq_v2_generic_evidence_link_scope",
                ("tenant_id", "deal_id", "run_id", "generic_evidence_link_id"),
            ),
            _unique(
                "uq_v2_generic_evidence_link_coordinate",
                ("tenant_id", "run_id", "generic_output_id", "evidence_id"),
            ),
            _unique(
                "uq_v2_generic_evidence_link_span_coordinate",
                ("tenant_id", "run_id", "generic_output_id", "source_span_id"),
            ),
            _unique(
                "uq_v2_generic_evidence_link_ordinal",
                ("tenant_id", "run_id", "generic_output_id", "evidence_ordinal"),
            ),
        ],
        indexes=[
            _index(
                evidence_root_index,
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "run_output_object_id",
                    "run_output_object_class",
                    "run_output_object_kind",
                ),
            ),
            _index(
                evidence_claim_index,
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "generic_output_id",
                    "generic_claim_link_id",
                    "claim_id",
                ),
            ),
            _index(
                evidence_item_index,
                ("tenant_id", "deal_id", "evidence_id", "claim_id", "source_span_id"),
            ),
            _index(
                evidence_span_index,
                ("tenant_id", "deal_id", "document_id", "source_span_id"),
            ),
        ],
        namespace="fbf8b180-1b76-56b8-bef4-e619548b13dc",
        uuid_tuple=[
            "generic-extraction-evidence-link-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "generic_output_id",
            "evidence_ordinal",
            "generic_claim_link_id",
            "claim_id",
            "evidence_id",
            "document_id",
            "source_span_id",
            "locator_sha256",
        ],
        projection_guard=(
            "CREATE CONSTRAINT TRIGGER ctrg_v2_generic_evidence_projection AFTER INSERT OR "
            "UPDATE OR DELETE ON public.generic_extraction_evidence_links DEFERRABLE INITIALLY "
            "DEFERRED FOR EACH ROW EXECUTE FUNCTION "
            "public.idis_task7_generic_evidence_projection_v1()"
        ),
    )

    dashboard_root_index = "ix_v3_dashboard_root_link_root"
    dashboard = _child_row(
        ordinal=19,
        table="methodology_truth_dashboard_root_links",
        primary_key="dashboard_root_link_id",
        parent_table="methodology_truth_dashboard_rows",
        parent_id="dashboard_row_id",
        parent_object_id="dashboard_output_object_id",
        parent_constraint="uq_v2_methodology_truth_dashboard_rows_reference_parent",
        prefix="v3_dashboard_root_link",
        phase=65,
        specific_columns=(
            ("dashboard_row_id", "UUID NOT NULL"),
            ("dashboard_output_object_id", "UUID NOT NULL"),
            (
                "root_kind",
                "TEXT NOT NULL CHECK (root_kind IN "
                "('RESULT','EVIDENCE','SANAD','CALCULATION','ADVERSE_CONTRADICTION'))",
            ),
            ("root_ordinal", "INTEGER NOT NULL"),
            ("root_output_object_id", "UUID NOT NULL"),
            ("root_output_object_class", "TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED"),
            ("root_output_object_kind", "TEXT NOT NULL"),
        ),
        checks=[
            [
                "ck_v3_dashboard_root_link_coordinate",
                "root_ordinal>=0 AND ((root_kind='RESULT' AND "
                "root_output_object_kind='METHODOLOGY_EXTRACTION_RESULT') OR "
                "(root_kind='EVIDENCE' AND "
                "root_output_object_kind='METHODOLOGY_EVIDENCE_LINK') OR "
                "(root_kind='SANAD' AND root_output_object_kind='METHODOLOGY_SANAD_LINK') OR "
                "(root_kind='CALCULATION' AND "
                "root_output_object_kind='METHODOLOGY_CALCULATION') OR "
                "(root_kind='ADVERSE_CONTRADICTION' AND "
                "root_output_object_kind='METHODOLOGY_DEFECT_LINK'))",
            ],
            [
                "ck_v3_dashboard_root_link_lease",
                "lease_owner_id IS NOT NULL AND lease_fencing_token>0",
            ],
        ],
        foreign_keys=[
            _fk(
                "fk_v3_dashboard_root_link_root",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "root_output_object_id",
                    "root_output_object_class",
                    "root_output_object_kind",
                ),
                "run_output_objects",
                "uq_task7_run_output_objects_class_kind",
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "output_object_id",
                    "output_class",
                    "output_kind",
                ),
                dashboard_root_index,
            )
        ],
        uniques=[
            _unique(
                "uq_v3_dashboard_root_link_scope",
                ("tenant_id", "deal_id", "run_id", "dashboard_root_link_id"),
            ),
            _unique(
                "uq_v3_dashboard_root_link_coordinate",
                (
                    "tenant_id",
                    "run_id",
                    "dashboard_row_id",
                    "root_kind",
                    "root_output_object_id",
                ),
            ),
            _unique(
                "uq_v3_dashboard_root_link_ordinal",
                ("tenant_id", "run_id", "dashboard_row_id", "root_kind", "root_ordinal"),
            ),
        ],
        indexes=[
            _index(
                dashboard_root_index,
                (
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "root_output_object_id",
                    "root_output_object_class",
                    "root_output_object_kind",
                ),
            )
        ],
        namespace=DASHBOARD_ROOT_LINK_NAMESPACE,
        uuid_tuple=[
            "methodology-truth-dashboard-root-link-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "dashboard_row_id",
            "root_kind",
            "root_ordinal",
            "root_output_object_id",
        ],
        projection_guard=(
            "CREATE CONSTRAINT TRIGGER ctrg_v3_dashboard_root_equation AFTER INSERT OR UPDATE "
            "OR DELETE ON public.methodology_truth_dashboard_root_links DEFERRABLE INITIALLY "
            "DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_dashboard_root_equation_v1()"
        ),
    )

    return [identity, scoring, generic_claim, generic_evidence, dashboard]


def _tuple_schema_rows(v2_rows: list[list[Any]]) -> list[list[Any]]:
    replaced = {
        "FutureNativeAuthoritySpecV1",
        "FutureNativeAuthoritySpecV2",
        "FutureReferenceAuthoritySpecV1",
        "FutureReferenceAuthoritySpecV2",
        "FutureChildAuthoritySpecV1",
        "FutureChildAuthoritySpecV2",
        "SqlAuthoritySpecV1",
        "ErasureExecutionSpecV1",
    }
    rows = [row for row in v2_rows if row[0] not in replaced]
    rows.extend(
        [
            ["FutureNativeAuthoritySpecV1", list(FUTURE_NATIVE_V1_FIELDS)],
            [
                "FutureNativeAuthoritySpecV2",
                [*FUTURE_NATIVE_V1_FIELDS[:-1], "sql_authority_id_tuple", "schema_sha256"],
            ],
            ["FutureReferenceAuthoritySpecV1", list(FUTURE_REFERENCE_V1_FIELDS)],
            [
                "FutureReferenceAuthoritySpecV2",
                [
                    *FUTURE_REFERENCE_V1_FIELDS[:-1],
                    "sql_authority_id_tuple",
                    "schema_sha256",
                ],
            ],
            ["FutureChildAuthoritySpecV1", list(FUTURE_CHILD_V1_FIELDS)],
            [
                "FutureChildAuthoritySpecV2",
                [*FUTURE_CHILD_V1_FIELDS[:-1], "sql_authority_id_tuple", "schema_sha256"],
            ],
            ["SqlAuthoritySpecV1", list(SQL_AUTHORITY_FIELDS)],
            ["ErasureExecutionSpecV1", list(ERASURE_EXECUTION_FIELDS)],
        ]
    )
    return rows


def _digest_recipe_section(v2_section: dict[str, Any]) -> dict[str, Any]:
    replacements = {
        "pipeline_contract_hash": (
            '["pipeline-contract-v3","full-strict-v3",reviewed_preimage_artifact_hash,'
            "ORDERED[component_hashes]]"
        ),
        "identity_identifier_projection_sha256": (
            'H(["company-identity-identifier-projection-v1",tenant_id,deal_id,run_id,'
            "identity_output_id,run_output_object_id,count,"
            "ORDERED[(identifier_kind,identifier_hash,identity_identifier_id,"
            "verification_provenance_sha256)]])"
        ),
        "scoring_dimension_projection_sha256": (
            'H(["scoring-dimension-projection-v1",tenant_id,deal_id,run_id,'
            "scoring_output_id,run_output_object_id,count,"
            "ORDERED[(dimension_ordinal,dimension_key,hex(float8send(score)),"
            "hex(float8send(confidence)),rationale_sha256,"
            "confidence_justification_sha256,support_projection_sha256,"
            "muhasabah_projection_sha256,scoring_dimension_id)]])"
        ),
        "methodology_truth_dashboard_root_projection_sha256": (
            'H(["methodology-truth-dashboard-root-projection-v1",tenant_id,deal_id,run_id,'
            "dashboard_row_id,run_output_object_id,coverage_record_id,row_disposition,count,"
            "ORDERED[(root_kind,root_ordinal,root_output_object_id,"
            "root_output_object_kind,dashboard_root_link_id)]])"
        ),
    }
    rows = [row for row in v2_section["rows"] if row[0] not in replacements]
    rows.extend([[name, value] for name, value in replacements.items()])
    return {"rows": rows, "reviewed_literal_utf8_hex": _hex(rows)}


def _uuid_section(v2_section: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in v2_section["rows"] if row[0] != "dashboard_root_link_id"]
    rows.append(
        [
            "dashboard_root_link_id",
            DASHBOARD_ROOT_LINK_NAMESPACE,
            [
                "methodology-truth-dashboard-root-link-v1",
                "tenant_id",
                "deal_id",
                "run_id",
                "dashboard_row_id",
                "root_kind",
                "root_ordinal",
                "root_output_object_id",
            ],
        ]
    )
    return {
        "rows": rows,
        "expected_sha256": _registry_hash("task7-uuid-namespace-registry-v1", rows),
    }


def _future_native_rows(authority: ModuleType) -> list[dict[str, Any]]:
    dashboard_shape = (
        "root_projection_sha256~'^[0-9a-f]{64}$' AND "
        "row_disposition IN ('ANSWERED','GAP','RED_FLAG') AND "
        "row_payload_sha256~'^[0-9a-f]{64}$'"
    )
    source_rows = authority.expected_future_native_rows(dashboard_shape=dashboard_shape)
    rows: list[dict[str, Any]] = []
    providers_sql = ",".join(f"'{provider}'" for provider in PROVIDERS)
    namespace_expression = (
        "(namespace='internal_evidence' AND provider_id IS NULL AND fact_domain IS NULL) OR "
        "(namespace='external_context' AND provider_id IS NOT NULL AND "
        f"fact_domain IS NOT NULL AND provider_id IN ({providers_sql}) AND "
        "fact_domain IN ('commercial_diligence','financial_diligence'))"
    )
    for source in source_rows:
        table = source["planned_table"]
        checks = [list(check) for check in source["check_tuple"]]
        if table in {"graph_projection_results", "rag_index_results", "rag_query_results"}:
            for check in checks:
                if check[0].endswith("_namespace"):
                    check[1] = namespace_expression
        if table == "layer2_readiness_outputs":
            checks.insert(
                0,
                [
                    "ck_v2_layer2_readiness_semantics",
                    "status='READY' AND blocker_count=0 AND deferred_count=0 AND "
                    "default_count=0 AND "
                    "required_projection_sha256=observed_projection_sha256",
                ],
            )
        row = {
            "authority_ordinal": source["authority_ordinal"],
            "schema_id": source["schema_id"],
            "schema_version": source["schema_version"],
            "planned_table": table,
            "primary_key": source["primary_key"],
            "scope_columns": source["scope_columns"],
            "immutable_fields": source["immutable_fields"],
            "check_tuple": checks,
            "reference_fields": source["reference_fields"],
            "unique_tuple": source["unique_tuple"],
            "child_relation_tuple": source["child_relation_tuple"],
            "rls_tuple": source["rls_tuple"],
            "erasure_ordinal": source["erasure_ordinal"],
            "subrole_field": source["subrole_field"],
            "namespace_field": source["namespace_field"],
            "sql_authority_id_tuple": _authority_ids_for_table(table),
        }
        row["schema_sha256"] = _authority_hash(row)
        rows.append(row)
    return rows


def _expand_edge_literals(artifact: dict[str, Any]) -> dict[str, Any]:
    producers = {
        row["rule_id"]: {
            "base_selector_id": row["base_selector_id"],
            "instances": row["instances"],
        }
        for row in artifact["producer_selector_registry"]["rows"]
    }
    consumers = {
        row["rule_id"]: row["consumer_coordinates"]
        for row in artifact["consumer_selector_registry"]["rows"]
    }
    rows: list[dict[str, Any]] = []
    for source in artifact["edge_rule_registry"]["rows"]:
        row = dict(source)
        rule_id = row["rule_id"]
        row["producer_literal_utf8_hex"] = _hex(
            ["producer-selector-literal-v3", rule_id, producers[rule_id]]
        )
        row["consumer_literal_utf8_hex"] = _hex(
            ["consumer-selector-literal-v3", rule_id, consumers[rule_id]]
        )
        rows.append(row)
    return {
        "rows": rows,
        "expected_manifest_sha256": (
            "07b942ee3093b082aa71dd606a5e06ce89e3097e68dcc2435d8a5e0ac6320c8d"
        ),
    }


ERASURE_PHASE_ORDER = (
    0,
    1,
    26,
    27,
    28,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    58,
    64,
    63,
    62,
    61,
    4,
    57,
    44,
    43,
    47,
    46,
    50,
    49,
    48,
    51,
    52,
    53,
    56,
    65,
    41,
    59,
    42,
    45,
    60,
    54,
    55,
    2,
    3,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    15,
)


def _erasure_materialized(v2_section: dict[str, Any]) -> dict[str, Any]:
    tables_by_phase = {row[1]: list(row[2]) for row in v2_section["rows"]}
    tables_by_phase[65] = ["methodology_truth_dashboard_root_links"]
    rows = [
        [execution_rank, phase, sorted(tables_by_phase[phase])]
        for execution_rank, phase in enumerate(ERASURE_PHASE_ORDER)
    ]
    return {
        "phase_count": 66,
        "physical_table_count": 72,
        "table_delta": [
            "company_identity_identifiers",
            "scoring_output_dimensions",
            "generic_extraction_claim_links",
            "generic_extraction_evidence_links",
            "methodology_truth_dashboard_root_links",
        ],
        "phase_delta": [
            [59, 64],
            [60, 4],
            [61, 61],
            [62, 62],
            [63, 63],
            [64, 64],
            [65, 41],
        ],
        "rows": rows,
        "expected_sha256": _h(["task7-erasure-schedule-v2", rows]),
    }


def _marker(authority_id: str) -> str:
    return "TASK7_SQL_AUTHORITY_REJECTED:" + authority_id.removesuffix("-v1").upper().replace(
        "-", "_"
    )


def _sql_row(
    *,
    authority_id: str,
    authority_kind: str,
    targets: list[list[str]],
    statements: list[str],
) -> dict[str, Any]:
    legal, illegal, direct = AUTHORITY_RED_CASES[authority_id]
    row: dict[str, Any] = {
        "authority_ordinal": AUTHORITY_IDS.index(authority_id),
        "authority_id": authority_id,
        "authority_version": "1",
        "authority_kind": authority_kind,
        "target_tuple": targets,
        "declared_parent_relation_tuple": list(AUTHORITY_PARENT_TABLES[authority_id]),
        "body_sql_utf8_hex_tuple": [statement.encode("utf-8").hex() for statement in statements],
        "legal_red_case_tuple": [legal],
        "illegal_red_case_tuple": [illegal],
        "direct_sql_red_case_tuple": [direct],
    }
    row["expected_sha256"] = _sql_authority_hash(row)
    return row


def _constraint_authority(
    authority_id: str,
    *,
    function_name: str,
    body: str,
    trigger_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    trigger_names = trigger_names or {}
    statements = [
        f"CREATE FUNCTION public.{function_name}() RETURNS trigger LANGUAGE plpgsql "
        f"SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $${body}$$"
    ]
    targets: list[list[str]] = []
    for table in AUTHORITY_TARGET_TABLES[authority_id]:
        trigger = trigger_names.get(
            table, f"ctrg_task7_{function_name.removeprefix('idis_task7_')}_{table}"
        )
        targets.append([table, trigger])
        statements.append(
            f"CREATE CONSTRAINT TRIGGER {trigger} AFTER INSERT OR UPDATE OR DELETE ON "
            f"public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
            f"public.{function_name}()"
        )
    statements.append(f"REVOKE ALL ON FUNCTION public.{function_name}() FROM PUBLIC")
    return _sql_row(
        authority_id=authority_id,
        authority_kind="CONSTRAINT_TRIGGER",
        targets=targets,
        statements=statements,
    )


def _before_row_authority(
    authority_id: str,
    *,
    function_name: str,
    trigger_name: str,
    body: str,
    events: str,
    kind: str,
) -> dict[str, Any]:
    table = AUTHORITY_TARGET_TABLES[authority_id][0]
    statements = [
        f"CREATE FUNCTION public.{function_name}() RETURNS trigger LANGUAGE plpgsql "
        f"SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $${body}$$",
        f"CREATE TRIGGER {trigger_name} BEFORE {events} ON public.{table} FOR EACH ROW "
        f"EXECUTE FUNCTION public.{function_name}()",
        f"REVOKE ALL ON FUNCTION public.{function_name}() FROM PUBLIC",
    ]
    return _sql_row(
        authority_id=authority_id,
        authority_kind=kind,
        targets=[[table, trigger_name]],
        statements=statements,
    )


def _check_authority(
    authority_id: str,
    targets_and_expressions: list[tuple[str, str, str]],
) -> dict[str, Any]:
    return _sql_row(
        authority_id=authority_id,
        authority_kind="CHECK",
        targets=[[table, constraint] for table, constraint, _ in targets_and_expressions],
        statements=[expression for _, _, expression in targets_and_expressions],
    )


def _namespace_check_expression() -> str:
    providers = ",".join(f"'{provider}'" for provider in PROVIDERS)
    return (
        "(namespace='internal_evidence' AND provider_id IS NULL AND fact_domain IS NULL) OR "
        "(namespace='external_context' AND provider_id IS NOT NULL AND fact_domain IS NOT NULL "
        f"AND provider_id IN ({providers}) AND "
        "fact_domain IN ('commercial_diligence','financial_diligence'))"
    )


def _namespace_authority() -> dict[str, Any]:
    expression = _namespace_check_expression()
    return _check_authority(
        "graph-rag-namespace-v1",
        [
            (
                "graph_projection_results",
                "ck_v2_graph_projection_results_namespace",
                expression,
            ),
            ("rag_index_results", "ck_v2_rag_index_results_namespace", expression),
            ("rag_query_results", "ck_v2_rag_query_results_namespace", expression),
        ],
    )


def _readiness_authority() -> dict[str, Any]:
    return _check_authority(
        "layer2-readiness-v1",
        [
            (
                "layer2_readiness_outputs",
                "ck_v2_layer2_readiness_semantics",
                "status='READY' AND blocker_count=0 AND deferred_count=0 AND "
                "default_count=0 AND required_projection_sha256=observed_projection_sha256",
            )
        ],
    )


def _rag_parent_authority() -> dict[str, Any]:
    authority_id = "rag-query-parent-coordinate-v1"
    marker = _marker(authority_id)
    body = (
        "BEGIN "
        "IF TG_OP<>'DELETE' AND NOT EXISTS (SELECT 1 FROM public.rag_index_results p "
        "WHERE p.tenant_id=NEW.tenant_id AND p.deal_id=NEW.deal_id AND p.run_id=NEW.run_id "
        "AND p.rag_index_result_id=NEW.rag_index_result_id AND "
        "NEW.namespace IS NOT DISTINCT FROM p.namespace AND "
        "NEW.provider_id IS NOT DISTINCT FROM p.provider_id AND "
        "NEW.fact_domain IS NOT DISTINCT FROM p.fact_domain) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "RETURN NULL; END"
    )
    return _constraint_authority(
        authority_id,
        function_name="idis_task7_rag_query_parent_coordinate_v1",
        body=body,
    )


def _extract_parent_authority() -> dict[str, Any]:
    authority_id = "extract-rag-context-coordinate-v1"
    marker = _marker(authority_id)
    body = (
        "BEGIN "
        "IF TG_OP<>'DELETE' AND NOT EXISTS (SELECT 1 FROM public.run_step_outputs s "
        "WHERE s.tenant_id=NEW.tenant_id AND s.deal_id=NEW.deal_id AND s.run_id=NEW.run_id "
        "AND s.step_output_id=NEW.rag_context_step_output_id AND s.step_name='EXTRACT' "
        "AND s.subrole='rag_context') THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "RETURN NULL; END"
    )
    return _constraint_authority(
        authority_id,
        function_name="idis_task7_extract_rag_context_coordinate_v1",
        body=body,
    )


def _muhasabah_authority(
    authority_id: str,
    *,
    table: str,
    primary_key: str,
    step: str,
    kind: str,
) -> dict[str, Any]:
    marker = _marker(authority_id)
    body = (
        "BEGIN "
        f"IF EXISTS (SELECT 1 FROM public.{table} o WHERE (SELECT count(*) FROM "
        "public.muhasabah_records m WHERE m.tenant_id=o.tenant_id AND "
        "m.deal_id=o.deal_id AND m.run_id=o.run_id AND "
        "m.record_id=o.muhasabah_record_id AND m.source_step=o.muhasabah_source_step AND "
        "m.agent_id=o.muhasabah_agent_id AND m.output_id=o.muhasabah_output_id AND "
        "m.run_output_object_kind=o.muhasabah_output_kind)<>1) OR EXISTS (SELECT 1 FROM "
        f"public.muhasabah_records m WHERE m.source_step='{step}' AND "
        f"m.run_output_object_kind='{kind}' AND NOT EXISTS (SELECT 1 FROM public.{table} o "
        "WHERE o.tenant_id=m.tenant_id AND o.deal_id=m.deal_id AND o.run_id=m.run_id AND "
        "o.muhasabah_record_id=m.record_id AND o.muhasabah_source_step=m.source_step AND "
        "o.muhasabah_agent_id=m.agent_id AND o.muhasabah_output_id=m.output_id AND "
        f"o.muhasabah_output_kind=m.run_output_object_kind AND o.{primary_key}::text=m.output_id)) "
        f"THEN RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "RETURN NULL; END"
    )
    return _constraint_authority(
        authority_id,
        function_name=f"idis_task7_{authority_id.removesuffix('-v1').replace('-', '_')}_v1",
        body=body,
    )


def _generic_claim_authority() -> dict[str, Any]:
    authority_id = "generic-claim-projection-v1"
    marker = _marker(authority_id)
    body = (
        "BEGIN "
        "IF EXISTS (SELECT 1 FROM public.generic_extraction_claim_links l WHERE NOT EXISTS "
        "(SELECT 1 FROM public.generic_extraction_outputs g WHERE g.tenant_id=l.tenant_id "
        "AND g.deal_id=l.deal_id AND g.run_id=l.run_id AND "
        "g.generic_output_id=l.generic_output_id AND "
        "g.run_output_object_id=l.generic_output_object_id) OR NOT EXISTS (SELECT 1 FROM "
        "public.run_output_objects r WHERE r.tenant_id=l.tenant_id AND r.deal_id=l.deal_id "
        "AND r.run_id=l.run_id AND r.output_object_id=l.run_output_object_id AND "
        "r.output_class=l.run_output_object_class AND r.output_kind=l.run_output_object_kind "
        "AND r.output_class='NATIVE_ROW' AND r.output_kind='METHODOLOGY_CLAIM_LINK') OR "
        "NOT EXISTS (SELECT 1 FROM public.run_step_outputs s WHERE s.tenant_id=l.tenant_id "
        "AND s.deal_id=l.deal_id AND s.run_id=l.run_id AND "
        "s.output_object_id=l.run_output_object_id AND s.step_name='EXTRACT' AND "
        "s.output_kind='METHODOLOGY_CLAIM_LINK') OR NOT EXISTS (SELECT 1 FROM public.claims c "
        "WHERE c.tenant_id=l.tenant_id AND c.deal_id=l.deal_id AND c.claim_id=l.claim_id)) "
        "OR EXISTS (SELECT 1 FROM public.run_output_objects r JOIN public.run_step_outputs s "
        "ON s.tenant_id=r.tenant_id AND s.deal_id=r.deal_id AND s.run_id=r.run_id AND "
        "s.output_object_id=r.output_object_id WHERE r.output_class='NATIVE_ROW' AND "
        "r.output_kind='METHODOLOGY_CLAIM_LINK' AND s.step_name='EXTRACT' AND "
        "(SELECT count(*) FROM public.generic_extraction_claim_links l WHERE "
        "l.tenant_id=r.tenant_id AND l.deal_id=r.deal_id AND l.run_id=r.run_id AND "
        "l.run_output_object_id=r.output_object_id)<>1) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; RETURN NULL; END"
    )
    return _constraint_authority(
        authority_id,
        function_name="idis_task7_generic_claim_projection_v1",
        body=body,
        trigger_names={
            "generic_extraction_claim_links": "ctrg_v2_generic_claim_projection",
            "run_output_objects": "ctrg_task7_generic_claim_root_projection",
        },
    )


def _generic_evidence_authority() -> dict[str, Any]:
    authority_id = "generic-evidence-projection-v1"
    marker = _marker(authority_id)
    body = (
        "BEGIN "
        "IF EXISTS (SELECT 1 FROM public.generic_extraction_evidence_links l WHERE NOT EXISTS "
        "(SELECT 1 FROM public.generic_extraction_outputs g WHERE g.tenant_id=l.tenant_id "
        "AND g.deal_id=l.deal_id AND g.run_id=l.run_id AND "
        "g.generic_output_id=l.generic_output_id AND "
        "g.run_output_object_id=l.generic_output_object_id) OR NOT EXISTS (SELECT 1 FROM "
        "public.run_output_objects r WHERE r.tenant_id=l.tenant_id AND r.deal_id=l.deal_id "
        "AND r.run_id=l.run_id AND r.output_object_id=l.run_output_object_id AND "
        "r.output_class=l.run_output_object_class AND r.output_kind=l.run_output_object_kind "
        "AND r.output_class='NATIVE_ROW' AND r.output_kind='METHODOLOGY_EVIDENCE_LINK') OR "
        "NOT EXISTS (SELECT 1 FROM public.run_step_outputs s WHERE s.tenant_id=l.tenant_id "
        "AND s.deal_id=l.deal_id AND s.run_id=l.run_id AND "
        "s.output_object_id=l.run_output_object_id AND s.step_name='EXTRACT' AND "
        "s.output_kind='METHODOLOGY_EVIDENCE_LINK') OR NOT EXISTS (SELECT 1 FROM "
        "public.generic_extraction_claim_links c WHERE c.tenant_id=l.tenant_id AND "
        "c.deal_id=l.deal_id AND c.run_id=l.run_id AND "
        "c.generic_output_id=l.generic_output_id AND "
        "c.generic_claim_link_id=l.generic_claim_link_id AND c.claim_id=l.claim_id) OR "
        "NOT EXISTS (SELECT 1 FROM public.evidence_items e WHERE e.tenant_id=l.tenant_id AND "
        "e.deal_id=l.deal_id AND e.evidence_id=l.evidence_id AND e.claim_id=l.claim_id AND "
        "e.document_id=l.document_id AND e.source_span_id=l.source_span_id) OR NOT EXISTS "
        "(SELECT 1 FROM public.document_spans d WHERE d.tenant_id=l.tenant_id AND "
        "d.deal_id=l.deal_id AND d.document_id=l.document_id AND "
        "d.span_id=l.source_span_id)) OR EXISTS (SELECT 1 FROM public.run_output_objects r JOIN "
        "public.run_step_outputs s ON s.tenant_id=r.tenant_id AND s.deal_id=r.deal_id AND "
        "s.run_id=r.run_id AND s.output_object_id=r.output_object_id WHERE "
        "r.output_class='NATIVE_ROW' AND r.output_kind='METHODOLOGY_EVIDENCE_LINK' AND "
        "s.step_name='EXTRACT' AND (SELECT count(*) FROM "
        "public.generic_extraction_evidence_links l WHERE l.tenant_id=r.tenant_id AND "
        "l.deal_id=r.deal_id AND l.run_id=r.run_id AND "
        "l.run_output_object_id=r.output_object_id)<>1) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; RETURN NULL; END"
    )
    return _constraint_authority(
        authority_id,
        function_name="idis_task7_generic_evidence_projection_v1",
        body=body,
        trigger_names={
            "generic_extraction_evidence_links": "ctrg_v2_generic_evidence_projection",
            "run_output_objects": "ctrg_task7_generic_evidence_root_projection",
        },
    )


def _grading_authority() -> dict[str, Any]:
    authority_id = "grading-dispatch-and-consistency-v1"
    marker = _marker(authority_id)
    body = (
        "BEGIN "
        "IF EXISTS (SELECT 1 FROM public.run_claim_grading_links g WHERE "
        "g.source_claim_output_class<>'NATIVE_ROW' OR "
        "g.source_claim_output_kind<>'METHODOLOGY_CLAIM_LINK' OR "
        "g.grade NOT IN ('A','B','C','D') OR NOT EXISTS (SELECT 1 FROM "
        "public.run_output_objects r WHERE r.tenant_id=g.tenant_id AND "
        "r.deal_id=g.deal_id AND r.run_id=g.run_id AND "
        "r.output_object_id=g.source_claim_output_object_id AND "
        "r.output_class=g.source_claim_output_class AND "
        "r.output_kind=g.source_claim_output_kind) OR NOT EXISTS (SELECT 1 FROM "
        "public.claims c WHERE c.tenant_id=g.tenant_id AND c.deal_id=g.deal_id AND "
        "c.claim_id=g.claim_id) OR NOT EXISTS (SELECT 1 FROM public.sanads s WHERE "
        "s.tenant_id=g.tenant_id AND s.deal_id=g.deal_id AND s.sanad_id=g.sanad_id "
        "AND s.claim_id=g.claim_id) OR ((g.source_claim_step_name="
        "'METHODOLOGY_CLAIM_MATERIALIZATION' AND (g.methodology_sanad_link_id IS NULL "
        "OR NOT EXISTS (SELECT 1 FROM public.methodology_result_claim_links m WHERE "
        "m.tenant_id=g.tenant_id AND m.deal_id=g.deal_id AND m.run_id=g.run_id AND "
        "m.run_output_object_id=g.source_claim_output_object_id AND "
        "m.run_output_object_class=g.source_claim_output_class AND "
        "m.run_output_object_kind=g.source_claim_output_kind AND m.claim_id=g.claim_id) "
        "OR NOT EXISTS (SELECT 1 FROM public.methodology_result_sanad_links sl WHERE "
        "sl.tenant_id=g.tenant_id AND sl.deal_id=g.deal_id AND sl.run_id=g.run_id AND "
        "sl.sanad_link_id=g.methodology_sanad_link_id AND sl.sanad_id=g.sanad_id))) OR "
        "(g.source_claim_step_name='EXTRACT' AND (g.methodology_sanad_link_id IS NOT NULL "
        "OR NOT EXISTS (SELECT 1 FROM public.generic_extraction_claim_links x WHERE "
        "x.tenant_id=g.tenant_id AND x.deal_id=g.deal_id AND x.run_id=g.run_id AND "
        "x.run_output_object_id=g.source_claim_output_object_id AND "
        "x.run_output_object_class=g.source_claim_output_class AND "
        "x.run_output_object_kind=g.source_claim_output_kind AND x.claim_id=g.claim_id))) OR "
        "g.source_claim_step_name NOT IN ('METHODOLOGY_CLAIM_MATERIALIZATION','EXTRACT')) "
        "OR EXISTS (SELECT 1 FROM public.methodology_result_claim_links m WHERE "
        "(SELECT count(*) FROM public.run_claim_grading_links g WHERE "
        "g.tenant_id=m.tenant_id AND g.deal_id=m.deal_id AND g.run_id=m.run_id AND "
        "g.source_claim_output_object_id=m.run_output_object_id AND "
        "g.source_claim_output_class=m.run_output_object_class AND "
        "g.source_claim_output_kind=m.run_output_object_kind AND "
        "g.source_claim_step_name='METHODOLOGY_CLAIM_MATERIALIZATION' AND "
        "g.claim_id=m.claim_id)<>1) OR EXISTS (SELECT 1 FROM "
        "public.generic_extraction_claim_links x WHERE (SELECT count(*) FROM "
        "public.run_claim_grading_links g WHERE g.tenant_id=x.tenant_id AND "
        "g.deal_id=x.deal_id AND g.run_id=x.run_id AND "
        "g.source_claim_output_object_id=x.run_output_object_id AND "
        "g.source_claim_output_class=x.run_output_object_class AND "
        "g.source_claim_output_kind=x.run_output_object_kind AND "
        "g.source_claim_step_name='EXTRACT' AND g.claim_id=x.claim_id)<>1) OR "
        "EXISTS (SELECT 1 FROM public.run_claim_grading_links g GROUP BY "
        "g.tenant_id,g.deal_id,g.run_id,g.claim_id HAVING "
        "count(DISTINCT ROW(g.sanad_id,g.grade,g.grading_sha256))<>1) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; RETURN NULL; END"
    )
    return _constraint_authority(
        authority_id,
        function_name="idis_task7_grading_dispatch_and_consistency_v1",
        body=body,
        trigger_names={
            "run_claim_grading_links": "ctrg_task7_grading_link_consistency",
            "methodology_result_claim_links": "ctrg_task7_method_claim_grading",
            "generic_extraction_claim_links": "ctrg_task7_generic_claim_grading",
        },
    )


def _finite_authority() -> dict[str, Any]:
    expression = (
        "score<>'NaN'::double precision AND score<>'Infinity'::double precision AND "
        "score<>'-Infinity'::double precision AND score>=0::double precision AND "
        "score<=1::double precision AND confidence<>'NaN'::double precision AND "
        "confidence<>'Infinity'::double precision AND "
        "confidence<>'-Infinity'::double precision AND "
        "confidence>=0::double precision AND confidence<=1::double precision"
    )
    return _check_authority(
        "scoring-binary64-finite-v1",
        [("scoring_output_dimensions", "ck_v2_scoring_dimension_binary64_finite", expression)],
    )


def _negative_zero_authority() -> dict[str, Any]:
    return _before_row_authority(
        "scoring-binary64-negative-zero-v1",
        function_name="idis_task7_scoring_binary64_negative_zero_v1",
        trigger_name="trg_task7_scoring_binary64_negative_zero",
        body=(
            "BEGIN IF NEW.score=0::double precision THEN NEW.score:=0::double precision; "
            "END IF; IF NEW.confidence=0::double precision THEN "
            "NEW.confidence:=0::double precision; END IF; RETURN NEW; END"
        ),
        events="INSERT OR UPDATE",
        kind="BEFORE_ROW_TRIGGER",
    )


def _identity_projection_authority() -> dict[str, Any]:
    authority_id = "identity-identifier-projection-v1"
    marker = _marker(authority_id)
    body = (
        "DECLARE scope_tenant UUID; scope_deal UUID; scope_run UUID; "
        "scope_identity_output UUID; parent_root UUID; persisted_hash TEXT; "
        "child_count BIGINT; item RECORD; preimage TEXT; actual_hash TEXT; "
        "first_item BOOLEAN:=true; BEGIN "
        "preimage:='[\"company-identity-identifier-projection-v1\",'; "
        "IF TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; scope_deal:=OLD.deal_id; "
        "scope_run:=OLD.run_id; scope_identity_output:=OLD.identity_output_id; ELSE "
        "scope_tenant:=NEW.tenant_id; scope_deal:=NEW.deal_id; scope_run:=NEW.run_id; "
        "scope_identity_output:=NEW.identity_output_id; END IF; "
        "SELECT o.tenant_id,o.deal_id,o.run_id,o.identity_output_id,"
        "o.run_output_object_id,o.identifier_projection_sha256 INTO "
        "scope_tenant,scope_deal,scope_run,scope_identity_output,parent_root,persisted_hash "
        "FROM public.company_identity_outputs o WHERE o.tenant_id=scope_tenant AND "
        "o.deal_id=scope_deal AND o.run_id=scope_run AND "
        "o.identity_output_id=scope_identity_output; IF NOT FOUND THEN RETURN NULL; END IF; "
        "SELECT count(*) INTO child_count FROM public.company_identity_identifiers i WHERE "
        "i.tenant_id=scope_tenant AND i.deal_id=scope_deal AND i.run_id=scope_run AND "
        "i.identity_output_id=scope_identity_output; "
        "preimage:=preimage||'\"'||scope_tenant::text||'\",\"'||scope_deal::text||"
        "'\",\"'||scope_run::text||'\",\"'||scope_identity_output::text||'\",\"'||"
        "parent_root::text||'\",'||child_count::text||',['; "
        "FOR item IN SELECT identifier_kind,identifier_hash,identity_identifier_id,"
        "verification_provenance_sha256 FROM public.company_identity_identifiers WHERE "
        "tenant_id=scope_tenant AND deal_id=scope_deal AND run_id=scope_run AND "
        "identity_output_id=scope_identity_output ORDER BY identifier_kind,identifier_hash,"
        "identity_identifier_id LOOP IF NOT first_item THEN preimage:=preimage||','; END IF; "
        "first_item:=false; preimage:=preimage||'[\"'||item.identifier_kind||'\",\"'||"
        "item.identifier_hash||'\",\"'||item.identity_identifier_id::text||'\",\"'||"
        "item.verification_provenance_sha256||'\"]'; END LOOP; "
        "preimage:=preimage||']]'; actual_hash:=encode(pg_catalog.sha256("
        "convert_to(preimage,'UTF8')),'hex'); IF actual_hash<>persisted_hash THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; RETURN NULL; END"
    )
    return _constraint_authority(
        authority_id,
        function_name="idis_task7_identity_identifier_projection_v1",
        body=body,
        trigger_names={
            "company_identity_outputs": "ctrg_task7_identity_output_projection",
            "company_identity_identifiers": "ctrg_v2_identity_identifier_projection",
        },
    )


def _scoring_projection_authority() -> dict[str, Any]:
    authority_id = "scoring-dimension-projection-v1"
    marker = _marker(authority_id)
    body = (
        "DECLARE scope_tenant UUID; scope_deal UUID; scope_run UUID; "
        "scope_scoring_output UUID; parent_root UUID; persisted_hash TEXT; "
        "child_count BIGINT; item RECORD; preimage TEXT; actual_hash TEXT; "
        "first_item BOOLEAN:=true; BEGIN "
        "preimage:='[\"scoring-dimension-projection-v1\",'; "
        "IF TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; scope_deal:=OLD.deal_id; "
        "scope_run:=OLD.run_id; scope_scoring_output:=OLD.scoring_output_id; ELSE "
        "scope_tenant:=NEW.tenant_id; scope_deal:=NEW.deal_id; scope_run:=NEW.run_id; "
        "scope_scoring_output:=NEW.scoring_output_id; END IF; "
        "SELECT o.tenant_id,o.deal_id,o.run_id,o.scoring_output_id,"
        "o.run_output_object_id,o.dimension_projection_sha256 INTO "
        "scope_tenant,scope_deal,scope_run,scope_scoring_output,parent_root,persisted_hash "
        "FROM public.scoring_outputs o WHERE o.tenant_id=scope_tenant AND "
        "o.deal_id=scope_deal AND o.run_id=scope_run AND "
        "o.scoring_output_id=scope_scoring_output; IF NOT FOUND THEN RETURN NULL; END IF; "
        "SELECT count(*) INTO child_count FROM public.scoring_output_dimensions d WHERE "
        "d.tenant_id=scope_tenant AND d.deal_id=scope_deal AND d.run_id=scope_run AND "
        "d.scoring_output_id=scope_scoring_output; "
        "preimage:=preimage||'\"'||scope_tenant::text||'\",\"'||scope_deal::text||"
        "'\",\"'||scope_run::text||'\",\"'||scope_scoring_output::text||'\",\"'||"
        "parent_root::text||'\",'||child_count::text||',['; "
        "FOR item IN SELECT dimension_ordinal,dimension_key,"
        "encode(float8send(score),'hex') AS score_hex,"
        "encode(float8send(confidence),'hex') AS confidence_hex,rationale_sha256,"
        "confidence_justification_sha256,support_projection_sha256,"
        "muhasabah_projection_sha256,scoring_dimension_id FROM "
        "public.scoring_output_dimensions WHERE tenant_id=scope_tenant AND "
        "deal_id=scope_deal AND run_id=scope_run AND scoring_output_id=scope_scoring_output "
        "ORDER BY dimension_ordinal LOOP IF NOT first_item THEN preimage:=preimage||','; "
        "END IF; first_item:=false; preimage:=preimage||'['||item.dimension_ordinal::text||"
        "',\"'||item.dimension_key||'\",\"'||item.score_hex||'\",\"'||"
        "item.confidence_hex||'\",\"'||item.rationale_sha256||'\",\"'||"
        "item.confidence_justification_sha256||'\",\"'||item.support_projection_sha256||"
        "'\",\"'||item.muhasabah_projection_sha256||'\",\"'||"
        "item.scoring_dimension_id::text||'\"]'; END LOOP; preimage:=preimage||']]'; "
        "actual_hash:=encode(pg_catalog.sha256(convert_to(preimage,'UTF8')),'hex'); "
        "IF actual_hash<>persisted_hash THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; RETURN NULL; END"
    )
    return _constraint_authority(
        authority_id,
        function_name="idis_task7_scoring_dimension_projection_v1",
        body=body,
        trigger_names={
            "scoring_outputs": "ctrg_task7_scoring_output_projection",
            "scoring_output_dimensions": "ctrg_v2_scoring_dimension_projection",
        },
    )


def _native_root_authority() -> dict[str, Any]:
    authority_id = "native-root-exact-one-v1"
    marker = _marker(authority_id)
    child_selects: list[str] = []
    coordinate_values: list[str] = []
    for table, _primary_key, kind, step, fixed_subrole, subrole_column in NATIVE_ROOT_BRANCHES:
        subrole = f"c.{subrole_column}" if subrole_column is not None else f"'{fixed_subrole}'"
        child_selects.append(
            f"SELECT '{table}'::text AS child_table,c.tenant_id,c.deal_id,c.run_id,"
            "c.run_output_object_id,c.run_output_object_class,c.run_output_object_kind,"
            f"'{kind}'::text AS expected_kind,'{step}'::text AS expected_step,"
            f"{subrole}::text AS expected_subrole FROM public.{table} c"
        )
        coordinate_values.append(f"('{kind}'::text,'{step}'::text)")
    children_sql = " UNION ALL ".join(child_selects)
    coordinates_sql = ",".join(coordinate_values)
    body = (
        "BEGIN IF EXISTS (WITH registered_children AS ("
        + children_sql
        + "), registered_coordinates(output_kind,step_name) AS (VALUES "
        + coordinates_sql
        + ") SELECT 1 WHERE EXISTS (SELECT 1 FROM registered_children c WHERE "
        "c.run_output_object_class<>'NATIVE_ROW' OR "
        "c.run_output_object_kind<>c.expected_kind OR NOT EXISTS (SELECT 1 FROM "
        "public.run_output_objects r WHERE r.tenant_id=c.tenant_id AND "
        "r.deal_id=c.deal_id AND r.run_id=c.run_id AND "
        "r.output_object_id=c.run_output_object_id AND "
        "r.output_class=c.run_output_object_class AND r.output_kind=c.run_output_object_kind "
        "AND r.output_class='NATIVE_ROW') OR NOT EXISTS (SELECT 1 FROM "
        "public.run_step_outputs s WHERE s.tenant_id=c.tenant_id AND s.deal_id=c.deal_id "
        "AND s.run_id=c.run_id AND s.output_object_id=c.run_output_object_id AND "
        "s.step_name=c.expected_step AND s.subrole=c.expected_subrole AND "
        "s.output_kind=c.expected_kind)) OR EXISTS (SELECT 1 FROM "
        "public.run_output_objects r WHERE r.output_class='NATIVE_ROW' AND EXISTS "
        "(SELECT 1 FROM public.run_step_outputs s JOIN registered_coordinates q ON "
        "q.output_kind=s.output_kind AND q.step_name=s.step_name WHERE "
        "s.tenant_id=r.tenant_id AND s.deal_id=r.deal_id AND s.run_id=r.run_id AND "
        "s.output_object_id=r.output_object_id AND s.output_kind=r.output_kind) AND "
        "(SELECT count(*) FROM registered_children c WHERE c.tenant_id=r.tenant_id AND "
        "c.deal_id=r.deal_id AND c.run_id=r.run_id AND "
        "c.run_output_object_id=r.output_object_id)<>1) OR EXISTS (SELECT 1 FROM "
        "public.run_output_objects r WHERE r.output_class='STEP_RECEIPT' AND "
        "((SELECT count(*) FROM registered_children c WHERE c.tenant_id=r.tenant_id AND "
        "c.deal_id=r.deal_id AND c.run_id=r.run_id AND "
        "c.run_output_object_id=r.output_object_id)<>0 OR (SELECT count(*) FROM "
        "public.run_step_outputs s WHERE s.tenant_id=r.tenant_id AND "
        "s.deal_id=r.deal_id AND s.run_id=r.run_id AND "
        "s.output_object_id=r.output_object_id AND s.output_kind=r.output_kind)<>1))) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; RETURN NULL; END"
    )
    trigger_names = {
        table: f"ctrg_t7_native_{ordinal:02d}_{table[:32]}"
        for ordinal, table in enumerate(AUTHORITY_TARGET_TABLES[authority_id])
    }
    return _constraint_authority(
        authority_id,
        function_name="idis_task7_native_root_exact_one_v1",
        body=body,
        trigger_names=trigger_names,
    )


def _dashboard_expected_roots_cte() -> str:
    scope = (
        "t.tenant_id=dashboard_row.tenant_id AND t.deal_id=dashboard_row.deal_id AND "
        "t.run_id=dashboard_row.run_id AND "
        "t.coverage_record_id=dashboard_row.coverage_record_id"
    )
    result_join = (
        "r.tenant_id=t.tenant_id AND r.deal_id=t.deal_id AND r.run_id=t.run_id AND "
        "r.task_record_id=t.task_record_id"
    )
    root_join = (
        "o.tenant_id=dashboard_row.tenant_id AND o.deal_id=dashboard_row.deal_id AND "
        "o.run_id=dashboard_row.run_id"
    )
    return (
        "expected_raw(root_kind,root_output_object_id,root_output_object_kind) AS ("
        "SELECT 'RESULT'::text,r.run_output_object_id,r.run_output_object_kind FROM "
        "public.methodology_extraction_tasks t JOIN public.methodology_extraction_results r ON "
        f"{result_join} JOIN public.run_output_objects o ON {root_join} AND "
        "o.output_object_id=r.run_output_object_id AND "
        "o.output_class=r.run_output_object_class AND o.output_kind=r.run_output_object_kind "
        f"WHERE {scope} AND r.run_output_object_class='NATIVE_ROW' AND "
        "r.run_output_object_kind='METHODOLOGY_EXTRACTION_RESULT' UNION ALL "
        "SELECT 'EVIDENCE'::text,e.run_output_object_id,e.run_output_object_kind FROM "
        "public.methodology_extraction_tasks t JOIN public.methodology_extraction_results r ON "
        f"{result_join} JOIN public.methodology_result_factual_outputs f ON "
        "f.tenant_id=r.tenant_id AND f.deal_id=r.deal_id AND f.run_id=r.run_id AND "
        "f.result_id=r.result_id AND f.task_record_id=t.task_record_id JOIN "
        "public.methodology_result_evidence_links e ON e.tenant_id=f.tenant_id AND "
        "e.deal_id=f.deal_id AND e.run_id=f.run_id AND e.result_id=f.result_id AND "
        "e.factual_output_record_id=f.factual_output_record_id JOIN public.run_output_objects o "
        f"ON {root_join} AND o.output_object_id=e.run_output_object_id AND "
        "o.output_class=e.run_output_object_class AND o.output_kind=e.run_output_object_kind "
        f"WHERE {scope} AND e.run_output_object_class='NATIVE_ROW' AND "
        "e.run_output_object_kind='METHODOLOGY_EVIDENCE_LINK' UNION ALL "
        "SELECT 'SANAD'::text,s.run_output_object_id,s.run_output_object_kind FROM "
        "public.methodology_extraction_tasks t JOIN public.methodology_extraction_results r ON "
        f"{result_join} JOIN public.methodology_result_factual_outputs f ON "
        "f.tenant_id=r.tenant_id AND f.deal_id=r.deal_id AND f.run_id=r.run_id AND "
        "f.result_id=r.result_id AND f.task_record_id=t.task_record_id JOIN "
        "public.methodology_result_sanad_links s ON s.tenant_id=f.tenant_id AND "
        "s.deal_id=f.deal_id AND s.run_id=f.run_id AND s.result_id=f.result_id AND "
        "s.factual_output_record_id=f.factual_output_record_id JOIN public.run_output_objects o "
        f"ON {root_join} AND o.output_object_id=s.run_output_object_id AND "
        "o.output_class=s.run_output_object_class AND o.output_kind=s.run_output_object_kind "
        f"WHERE {scope} AND s.run_output_object_class='NATIVE_ROW' AND "
        "s.run_output_object_kind='METHODOLOGY_SANAD_LINK' UNION ALL "
        "SELECT 'ADVERSE_CONTRADICTION'::text,d.run_output_object_id,"
        "d.run_output_object_kind FROM public.methodology_extraction_tasks t JOIN "
        f"public.methodology_extraction_results r ON {result_join} JOIN "
        "public.methodology_result_factual_outputs f ON f.tenant_id=r.tenant_id AND "
        "f.deal_id=r.deal_id AND f.run_id=r.run_id AND f.result_id=r.result_id AND "
        "f.task_record_id=t.task_record_id JOIN public.methodology_result_defect_links d ON "
        "d.tenant_id=f.tenant_id AND d.deal_id=f.deal_id AND d.run_id=f.run_id AND "
        "d.result_id=f.result_id AND d.factual_output_record_id=f.factual_output_record_id JOIN "
        f"public.run_output_objects o ON {root_join} AND "
        "o.output_object_id=d.run_output_object_id AND "
        "o.output_class=d.run_output_object_class AND o.output_kind=d.run_output_object_kind "
        f"WHERE {scope} AND d.run_output_object_class='NATIVE_ROW' AND "
        "d.run_output_object_kind='METHODOLOGY_DEFECT_LINK' UNION ALL "
        "SELECT 'CALCULATION'::text,c.run_output_object_id,c.run_output_object_kind FROM "
        "public.deterministic_calculations c JOIN public.run_output_objects o ON "
        f"{root_join} AND o.output_object_id=c.run_output_object_id AND "
        "o.output_class=c.run_output_object_class AND o.output_kind=c.run_output_object_kind JOIN "
        "jsonb_array_elements(dashboard_row.question_payload->'required_calculations') item ON "
        "(item->>'required')::boolean AND item->>'calc_type'=c.calc_type WHERE "
        "c.tenant_id=dashboard_row.tenant_id AND c.deal_id=dashboard_row.deal_id AND "
        "c.run_id=dashboard_row.run_id AND "
        "c.coverage_record_id=dashboard_row.coverage_record_id AND "
        "c.methodology_question_record_id=dashboard_row.methodology_question_record_id AND "
        "c.producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION' AND "
        "c.run_output_object_class='NATIVE_ROW' AND "
        "c.run_output_object_kind='METHODOLOGY_CALCULATION'), "
        "expected_roots AS (SELECT e.root_kind,(SELECT count(*) FROM expected_raw p WHERE "
        "p.root_kind=e.root_kind AND (p.root_output_object_id<e.root_output_object_id OR "
        "(p.root_output_object_id=e.root_output_object_id AND "
        "p.root_output_object_kind<e.root_output_object_kind)))::integer AS root_ordinal,"
        "e.root_output_object_id,e.root_output_object_kind FROM expected_raw e)"
    )


def _dashboard_authority() -> dict[str, Any]:
    authority_id = "dashboard-root-equation-v1"
    marker = _marker(authority_id)
    expected_roots = _dashboard_expected_roots_cte()
    body = (
        "DECLARE dashboard_row RECORD; root_link RECORD; link_count BIGINT; preimage TEXT; "
        "actual_hash TEXT; first_link BOOLEAN; calculation_required BOOLEAN; BEGIN "
        "IF EXISTS (SELECT 1 FROM public.methodology_truth_dashboard_rows d WHERE NOT EXISTS "
        "(SELECT 1 FROM public.methodology_coverage_records c JOIN public.methodology_questions q "
        "ON q.tenant_id=c.tenant_id AND "
        "q.methodology_version_record_id=c.methodology_version_record_id AND "
        "q.methodology_question_record_id=c.methodology_question_record_id WHERE "
        "c.tenant_id=d.tenant_id AND c.deal_id=d.deal_id AND c.run_id=d.run_id AND "
        "c.coverage_record_id=d.coverage_record_id)) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "FOR dashboard_row IN SELECT d.*,q.question_payload,"
        "q.methodology_question_record_id FROM public.methodology_truth_dashboard_rows d JOIN "
        "public.methodology_coverage_records c ON c.tenant_id=d.tenant_id AND "
        "c.deal_id=d.deal_id AND c.run_id=d.run_id AND "
        "c.coverage_record_id=d.coverage_record_id JOIN public.methodology_questions q ON "
        "q.tenant_id=c.tenant_id AND "
        "q.methodology_version_record_id=c.methodology_version_record_id AND "
        "q.methodology_question_record_id=c.methodology_question_record_id LOOP "
        "IF jsonb_typeof(dashboard_row.question_payload->'required_calculations') IS DISTINCT "
        "FROM 'array' OR NOT EXISTS (SELECT 1 FROM "
        "jsonb_array_elements(dashboard_row.question_payload->'required_calculations')) OR "
        "EXISTS (SELECT 1 FROM "
        "jsonb_array_elements(dashboard_row.question_payload->'required_calculations') item "
        "WHERE jsonb_typeof(item)<>'object' OR NOT item?'required' OR "
        "jsonb_typeof(item->'required')<>'boolean') THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "SELECT bool_or((item->>'required')::boolean) INTO calculation_required FROM "
        "jsonb_array_elements(dashboard_row.question_payload->'required_calculations') item "
        "WHERE jsonb_typeof(dashboard_row.question_payload->'required_calculations')='array' "
        "AND jsonb_typeof(item)='object' AND item?'required' AND "
        "jsonb_typeof(item->'required')='boolean'; "
        "IF (calculation_required AND NOT EXISTS (SELECT 1 FROM "
        "public.deterministic_calculations c JOIN "
        "jsonb_array_elements(dashboard_row.question_payload->'required_calculations') item ON "
        "(item->>'required')::boolean AND item->>'calc_type'=c.calc_type WHERE "
        "c.tenant_id=dashboard_row.tenant_id AND c.deal_id=dashboard_row.deal_id AND "
        "c.run_id=dashboard_row.run_id AND "
        "c.coverage_record_id=dashboard_row.coverage_record_id AND "
        "c.methodology_question_record_id=dashboard_row.methodology_question_record_id AND "
        "c.producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION')) OR "
        "(NOT calculation_required AND EXISTS (SELECT 1 FROM "
        "public.deterministic_calculations c WHERE c.tenant_id=dashboard_row.tenant_id AND "
        "c.deal_id=dashboard_row.deal_id AND c.run_id=dashboard_row.run_id AND "
        "c.coverage_record_id=dashboard_row.coverage_record_id AND "
        "c.methodology_question_record_id=dashboard_row.methodology_question_record_id AND "
        "c.producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION')) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "IF dashboard_row.root_projection_sha256!~'^[0-9a-f]{64}$' OR EXISTS (SELECT 1 FROM "
        "public.methodology_truth_dashboard_root_links l WHERE "
        "l.tenant_id=dashboard_row.tenant_id AND l.deal_id=dashboard_row.deal_id AND "
        "l.run_id=dashboard_row.run_id AND l.dashboard_row_id=dashboard_row.dashboard_row_id "
        "AND NOT ((l.root_kind='RESULT' AND "
        "l.root_output_object_kind='METHODOLOGY_EXTRACTION_RESULT') OR "
        "(l.root_kind='EVIDENCE' AND "
        "l.root_output_object_kind='METHODOLOGY_EVIDENCE_LINK') OR "
        "(l.root_kind='SANAD' AND l.root_output_object_kind='METHODOLOGY_SANAD_LINK') OR "
        "(l.root_kind='CALCULATION' AND "
        "l.root_output_object_kind='METHODOLOGY_CALCULATION') OR "
        "(l.root_kind='ADVERSE_CONTRADICTION' AND "
        "l.root_output_object_kind='METHODOLOGY_DEFECT_LINK'))) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "IF dashboard_row.row_disposition='GAP' AND (NOT EXISTS (SELECT 1 FROM "
        "public.methodology_extraction_tasks t JOIN public.methodology_extraction_results r ON "
        "r.tenant_id=t.tenant_id AND r.deal_id=t.deal_id AND r.run_id=t.run_id AND "
        "r.task_record_id=t.task_record_id WHERE t.tenant_id=dashboard_row.tenant_id AND "
        "t.deal_id=dashboard_row.deal_id AND t.run_id=dashboard_row.run_id AND "
        "t.coverage_record_id=dashboard_row.coverage_record_id AND "
        "r.outcome='EVIDENCE_NOT_FOUND' AND r.result_payload<>'{}'::jsonb AND "
        "r.gap_finding<>'{}'::jsonb AND cardinality(r.searched_document_ids)>0 AND "
        "cardinality(r.searched_span_ids)>0) OR EXISTS (SELECT 1 FROM "
        "public.methodology_extraction_tasks t JOIN public.methodology_extraction_results r ON "
        "r.tenant_id=t.tenant_id AND r.deal_id=t.deal_id AND r.run_id=t.run_id AND "
        "r.task_record_id=t.task_record_id WHERE t.tenant_id=dashboard_row.tenant_id AND "
        "t.deal_id=dashboard_row.deal_id AND t.run_id=dashboard_row.run_id AND "
        "t.coverage_record_id=dashboard_row.coverage_record_id AND "
        "(r.outcome<>'EVIDENCE_NOT_FOUND' OR r.result_payload='{}'::jsonb OR "
        "r.gap_finding IS NULL OR r.gap_finding='{}'::jsonb OR "
        "cardinality(r.searched_document_ids)<=0 OR cardinality(r.searched_span_ids)<=0 OR "
        "r.searched_scope_sha256!~'^[0-9a-f]{64}$' OR "
        "r.provider_provenance_sha256!~'^[0-9a-f]{64}$')) OR EXISTS (SELECT 1 FROM "
        "public.methodology_result_factual_outputs f JOIN public.methodology_extraction_tasks t "
        "ON t.tenant_id=f.tenant_id AND t.deal_id=f.deal_id AND t.run_id=f.run_id AND "
        "t.task_record_id=f.task_record_id WHERE t.tenant_id=dashboard_row.tenant_id AND "
        "t.deal_id=dashboard_row.deal_id AND t.run_id=dashboard_row.run_id AND "
        "t.coverage_record_id=dashboard_row.coverage_record_id) OR calculation_required) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "IF dashboard_row.row_disposition IN ('ANSWERED','RED_FLAG') AND (NOT EXISTS (SELECT 1 "
        "FROM public.methodology_extraction_tasks t JOIN public.methodology_extraction_results r "
        "ON r.tenant_id=t.tenant_id AND r.deal_id=t.deal_id AND r.run_id=t.run_id AND "
        "r.task_record_id=t.task_record_id WHERE t.tenant_id=dashboard_row.tenant_id AND "
        "t.deal_id=dashboard_row.deal_id AND t.run_id=dashboard_row.run_id AND "
        "t.coverage_record_id=dashboard_row.coverage_record_id AND r.outcome='EVIDENCE_FOUND') "
        "OR EXISTS (SELECT 1 FROM public.methodology_extraction_tasks t JOIN "
        "public.methodology_extraction_results r ON r.tenant_id=t.tenant_id AND "
        "r.deal_id=t.deal_id AND r.run_id=t.run_id AND r.task_record_id=t.task_record_id WHERE "
        "t.tenant_id=dashboard_row.tenant_id AND t.deal_id=dashboard_row.deal_id AND "
        "t.run_id=dashboard_row.run_id AND t.coverage_record_id=dashboard_row.coverage_record_id "
        "AND (r.outcome<>'EVIDENCE_FOUND' OR r.result_payload='{}'::jsonb OR NOT EXISTS "
        "(SELECT 1 FROM public.methodology_result_factual_outputs f WHERE "
        "f.tenant_id=r.tenant_id AND f.deal_id=r.deal_id AND f.run_id=r.run_id AND "
        "f.result_id=r.result_id) OR EXISTS (SELECT 1 FROM "
        "public.methodology_result_factual_outputs f WHERE f.tenant_id=r.tenant_id AND "
        "f.deal_id=r.deal_id AND f.run_id=r.run_id AND f.result_id=r.result_id AND "
        "(NOT EXISTS (SELECT 1 FROM public.methodology_result_evidence_links e WHERE "
        "e.tenant_id=f.tenant_id AND e.deal_id=f.deal_id AND e.run_id=f.run_id AND "
        "e.result_id=f.result_id AND e.factual_output_record_id=f.factual_output_record_id) OR "
        "NOT EXISTS (SELECT 1 FROM public.methodology_result_sanad_links s WHERE "
        "s.tenant_id=f.tenant_id AND s.deal_id=f.deal_id AND s.run_id=f.run_id AND "
        "s.result_id=f.result_id AND s.factual_output_record_id=f.factual_output_record_id)))))) "
        "THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "IF (dashboard_row.row_disposition='ANSWERED' AND EXISTS (SELECT 1 FROM "
        "public.methodology_result_defect_links d JOIN public.methodology_result_factual_outputs f "
        "ON f.tenant_id=d.tenant_id AND f.deal_id=d.deal_id AND f.run_id=d.run_id AND "
        "f.result_id=d.result_id AND f.factual_output_record_id=d.factual_output_record_id JOIN "
        "public.methodology_extraction_tasks t ON t.tenant_id=f.tenant_id AND "
        "t.deal_id=f.deal_id AND t.run_id=f.run_id AND t.task_record_id=f.task_record_id WHERE "
        "t.tenant_id=dashboard_row.tenant_id AND t.deal_id=dashboard_row.deal_id AND "
        "t.run_id=dashboard_row.run_id AND t.coverage_record_id=dashboard_row.coverage_record_id)) "
        "OR (dashboard_row.row_disposition='RED_FLAG' AND NOT EXISTS (SELECT 1 FROM "
        "public.methodology_result_defect_links d JOIN public.methodology_result_factual_outputs f "
        "ON f.tenant_id=d.tenant_id AND f.deal_id=d.deal_id AND f.run_id=d.run_id AND "
        "f.result_id=d.result_id AND f.factual_output_record_id=d.factual_output_record_id JOIN "
        "public.methodology_extraction_tasks t ON t.tenant_id=f.tenant_id AND "
        "t.deal_id=f.deal_id AND t.run_id=f.run_id AND t.task_record_id=f.task_record_id WHERE "
        "t.tenant_id=dashboard_row.tenant_id AND t.deal_id=dashboard_row.deal_id AND "
        "t.run_id=dashboard_row.run_id AND t.coverage_record_id=dashboard_row.coverage_record_id)) "
        "THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "IF EXISTS (WITH "
        + expected_roots
        + ", missing_roots AS (SELECT root_kind,root_ordinal,root_output_object_id,"
        "root_output_object_kind FROM "
        "expected_roots EXCEPT SELECT l.root_kind,l.root_ordinal,l.root_output_object_id,"
        "l.root_output_object_kind FROM public.methodology_truth_dashboard_root_links l WHERE "
        "l.tenant_id=dashboard_row.tenant_id AND l.deal_id=dashboard_row.deal_id AND "
        "l.run_id=dashboard_row.run_id AND l.dashboard_row_id=dashboard_row.dashboard_row_id), "
        "extra_roots AS (SELECT l.root_kind,l.root_ordinal,l.root_output_object_id,"
        "l.root_output_object_kind FROM public.methodology_truth_dashboard_root_links l WHERE "
        "l.tenant_id=dashboard_row.tenant_id AND l.deal_id=dashboard_row.deal_id AND "
        "l.run_id=dashboard_row.run_id AND l.dashboard_row_id=dashboard_row.dashboard_row_id "
        "EXCEPT SELECT root_kind,root_ordinal,root_output_object_id,root_output_object_kind FROM "
        "expected_roots) SELECT 1 FROM missing_roots UNION ALL SELECT 1 FROM extra_roots) THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "SELECT count(*) INTO link_count FROM public.methodology_truth_dashboard_root_links l "
        "WHERE l.tenant_id=dashboard_row.tenant_id AND l.deal_id=dashboard_row.deal_id AND "
        "l.run_id=dashboard_row.run_id AND l.dashboard_row_id=dashboard_row.dashboard_row_id; "
        'preimage:=\'["methodology-truth-dashboard-root-projection-v1","\'||'
        "dashboard_row.tenant_id::text||'\",\"'||dashboard_row.deal_id::text||'\",\"'||"
        "dashboard_row.run_id::text||'\",\"'||dashboard_row.dashboard_row_id::text||'\",\"'||"
        "dashboard_row.run_output_object_id::text||'\",\"'||"
        "dashboard_row.coverage_record_id||'\",\"'||dashboard_row.row_disposition||'\",'||"
        "link_count::text||',['; first_link:=true; FOR root_link IN SELECT root_kind,"
        "root_ordinal,root_output_object_id,root_output_object_kind,dashboard_root_link_id FROM "
        "public.methodology_truth_dashboard_root_links WHERE tenant_id=dashboard_row.tenant_id "
        "AND deal_id=dashboard_row.deal_id AND run_id=dashboard_row.run_id AND "
        "dashboard_row_id=dashboard_row.dashboard_row_id ORDER BY root_kind,root_ordinal,"
        "root_output_object_id,dashboard_root_link_id LOOP IF NOT first_link THEN "
        "preimage:=preimage||','; END IF; first_link:=false; preimage:=preimage||'[\"'||"
        "root_link.root_kind||'\",'||root_link.root_ordinal::text||',\"'||"
        "root_link.root_output_object_id::text||'\",\"'||root_link.root_output_object_kind||"
        "'\",\"'||root_link.dashboard_root_link_id::text||'\"]'; END LOOP; "
        "preimage:=preimage||']]'; actual_hash:=encode(pg_catalog.sha256("
        "convert_to(preimage,'UTF8')),'hex'); IF actual_hash<>"
        "dashboard_row.root_projection_sha256 THEN "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; END LOOP; "
        "RETURN NULL; END"
    )
    return _constraint_authority(
        authority_id,
        function_name="idis_task7_dashboard_root_equation_v1",
        body=body,
        trigger_names={
            "methodology_truth_dashboard_rows": "ctrg_v2_dashboard_root_equation",
            "methodology_truth_dashboard_root_links": "ctrg_v3_dashboard_root_equation",
        },
    )


def _non_erasure_sql_authority_rows() -> list[dict[str, Any]]:
    rows_by_id = {
        "dashboard-root-equation-v1": _dashboard_authority(),
        "graph-rag-namespace-v1": _namespace_authority(),
        "rag-query-parent-coordinate-v1": _rag_parent_authority(),
        "layer2-readiness-v1": _readiness_authority(),
        "extract-rag-context-coordinate-v1": _extract_parent_authority(),
        "debate-muhasabah-exact-one-v1": _muhasabah_authority(
            "debate-muhasabah-exact-one-v1",
            table="debate_role_outputs",
            primary_key="debate_role_output_id",
            step="DEBATE",
            kind="DEBATE_MUHASABAH",
        ),
        "analysis-muhasabah-exact-one-v1": _muhasabah_authority(
            "analysis-muhasabah-exact-one-v1",
            table="analysis_agent_outputs",
            primary_key="analysis_output_id",
            step="ANALYSIS",
            kind="ANALYSIS_MUHASABAH",
        ),
        "layer2-muhasabah-exact-one-v1": _muhasabah_authority(
            "layer2-muhasabah-exact-one-v1",
            table="layer2_role_outputs",
            primary_key="layer2_role_output_id",
            step="LAYER2_IC_CHALLENGE",
            kind="LAYER2_MUHASABAH",
        ),
        "grading-dispatch-and-consistency-v1": _grading_authority(),
        "scoring-binary64-finite-v1": _finite_authority(),
        "scoring-binary64-negative-zero-v1": _negative_zero_authority(),
        "native-root-exact-one-v1": _native_root_authority(),
        "identity-identifier-projection-v1": _identity_projection_authority(),
        "scoring-dimension-projection-v1": _scoring_projection_authority(),
        "generic-claim-projection-v1": _generic_claim_authority(),
        "generic-evidence-projection-v1": _generic_evidence_authority(),
    }
    expected_ids = tuple(
        authority_id
        for authority_id in AUTHORITY_IDS
        if authority_id != "approved-erasure-append-only-v1"
    )
    if tuple(rows_by_id) != expected_ids:
        raise MaterializationError(
            "TASK7_SQL_AUTHORITY_ORDER_INVALID",
            "non-erasure authority construction order differs from the reviewed registry",
        )
    rows = [rows_by_id[authority_id] for authority_id in expected_ids]
    for ordinal, row in enumerate(rows):
        expected_ordinal = AUTHORITY_IDS.index(row["authority_id"])
        if row["authority_ordinal"] != expected_ordinal or row["expected_sha256"] != (
            _sql_authority_hash(row)
        ):
            raise MaterializationError(
                "TASK7_SQL_AUTHORITY_ROW_INVALID",
                f"non-erasure authority row {ordinal} is not canonically hash-bound",
            )
    return rows


def _validate_erasure_execution_split() -> None:
    current = frozenset(CURRENT_ERASURE_RELATIONS)
    future = frozenset(FUTURE_ERASURE_RELATIONS)
    complete = frozenset(COMPLETE_ERASURE_RELATIONS)
    expected_current = tuple(
        relation for relation in COMPLETE_ERASURE_RELATIONS if relation not in future
    )
    if (
        len(CURRENT_ERASURE_RELATIONS) != 48
        or len(current) != 48
        or len(FUTURE_ERASURE_RELATIONS) != 38
        or len(future) != 38
        or len(COMPLETE_ERASURE_RELATIONS) != 86
        or len(complete) != 86
    ):
        raise MaterializationError(
            "TASK7_ERASURE_EXECUTION_SPLIT_INVALID",
            "erasure execution relation tuples must contain 48, 38, and 86 unique rows",
        )
    if (
        FUTURE_ERASURE_RELATIONS != FUTURE_RELATION_TABLES
        or current & future
        or current | future != complete
        or expected_current != CURRENT_ERASURE_RELATIONS
    ):
        raise MaterializationError(
            "TASK7_ERASURE_EXECUTION_SPLIT_INVALID",
            "CURRENT48/FUTURE38/RUNNER86 set and ordered-projection equations differ",
        )


def _erasure_delete_statement(table: str) -> str:
    indirect = ERASURE_INDIRECT_DELETE_PARENTS.get(table)
    if indirect is None:
        return f"DELETE FROM public.{table} WHERE tenant_id=$2 AND deal_id=$3;"
    parent, join_key = indirect
    return (
        f"DELETE FROM public.{table} WHERE {table}.tenant_id=$2 AND {table}.{join_key} IN "
        f"(SELECT {parent}.{join_key} FROM public.{parent} WHERE "
        f"{parent}.tenant_id=$2 AND {parent}.deal_id=$3);"
    )


def _erasure_delete_block(relations: tuple[str, ...]) -> str:
    fragments: list[str] = []
    for relation in relations:
        fragments.extend(
            (
                _erasure_delete_statement(relation),
                "GET DIAGNOSTICS deleted_count=ROW_COUNT;",
                "deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object("
                f"'{relation}',deleted_count);",
            )
        )
    return " ".join(fragments)


def _erasure_runner_statement(relations: tuple[str, ...]) -> str:
    signature = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
    body = (
        "DECLARE deletion_counts JSONB:='{}'::jsonb; deleted_count BIGINT; BEGIN "
        "IF NOT (session_user='idis_erasure_executor' AND "
        "current_user='idis_erasure_function_owner') THEN RAISE EXCEPTION "
        "'TASK7_ERASURE_EXECUTOR_UNAUTHORIZED' USING ERRCODE='42501'; END IF; "
        "PERFORM pg_catalog.set_config('idis.tenant_id',$2::text,true); "
        "PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended("
        "'idis-task7-erasure-tenant-lock-v1:'||$2::text,0)); "
        "PERFORM 1 FROM public.erasure_requests r WHERE r.request_id=$1 AND "
        "r.tenant_id=$2 AND r.deal_id=$3 AND r.status IN ('REQUESTED','FAILED') "
        "FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION "
        "'TASK7_ERASURE_REQUEST_NOT_EXECUTABLE' USING ERRCODE='42501'; END IF; "
        "IF EXISTS (SELECT 1 FROM public.document_artifacts a WHERE a.tenant_id=$2 AND "
        "a.deal_id=$3 AND a.uri IS NOT NULL AND (a.uri NOT LIKE 'idis://%' OR "
        "a.uri='idis://')) THEN RAISE EXCEPTION 'TASK7_ERASURE_ARTIFACT_LOCATOR_INVALID' "
        "USING ERRCODE='23514'; END IF; "
        "IF EXISTS (SELECT 1 FROM public.legal_holds h WHERE h.tenant_id=$2 AND "
        "h.lifted_at IS NULL AND ((h.target_type='DEAL' AND h.target_id=$3::text) OR "
        "(h.target_type='DOCUMENT' AND EXISTS (SELECT 1 FROM public.document_artifacts a "
        "WHERE a.tenant_id=$2 AND a.deal_id=$3 AND h.target_id=a.doc_id::text)) OR "
        "(h.target_type='ARTIFACT' AND EXISTS (SELECT 1 FROM public.document_artifacts a "
        "WHERE a.tenant_id=$2 AND a.deal_id=$3 AND a.uri='idis://'||h.target_id)))) "
        "THEN RAISE EXCEPTION 'TASK7_ERASURE_BLOCKED_BY_HOLD' USING ERRCODE='23514'; "
        "END IF; SET CONSTRAINTS ALL DEFERRED; "
        "PERFORM pg_catalog.set_config('idis.erasure_context','true',true); "
        f"{_erasure_delete_block(relations)} "
        "SET CONSTRAINTS ALL IMMEDIATE; RETURN deletion_counts; END"
    )
    return (
        f"CREATE FUNCTION {signature} RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER "
        f"SET search_path=pg_catalog,pg_temp AS $${body}$$"
    )


_ERASURE_READ_PROJECTION_COLUMNS = {
    "erasure_requests": ("request_id", "tenant_id", "deal_id", "status"),
    "deterministic_calculations": ("tenant_id", "deal_id", "calc_id"),
    "runs": ("tenant_id", "deal_id", "run_id"),
    "human_gates": ("tenant_id", "deal_id", "gate_id"),
    "legal_holds": ("tenant_id", "target_type", "target_id", "lifted_at"),
    "document_artifacts": ("tenant_id", "deal_id", "doc_id", "uri"),
}


def _erasure_acl_statements(relations: tuple[str, ...]) -> list[str]:
    signature = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
    owner = "idis_erasure_function_owner"
    executor = "idis_erasure_executor"
    statements = [
        f"ALTER FUNCTION {signature} OWNER TO {owner}",
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC",
        f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {owner}",
        f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {executor}",
    ]
    reset_relations = tuple(dict.fromkeys((*relations, "erasure_requests", "legal_holds")))
    for table in reset_relations:
        statements.extend(
            (
                f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM {owner}",
                f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM {executor}",
            )
        )
    statements.append(f"GRANT USAGE ON SCHEMA public TO {owner}")
    statements.extend(
        f"GRANT SELECT ({','.join(columns)}) ON TABLE public.{table} TO {owner}"
        for table, columns in _ERASURE_READ_PROJECTION_COLUMNS.items()
    )
    for table in relations:
        if table in _ERASURE_READ_PROJECTION_COLUMNS:
            continue
        indirect = ERASURE_INDIRECT_DELETE_PARENTS.get(table)
        columns = ("tenant_id", indirect[1]) if indirect is not None else ("tenant_id", "deal_id")
        statements.append(f"GRANT SELECT ({','.join(columns)}) ON TABLE public.{table} TO {owner}")
    statements.extend(f"GRANT DELETE ON TABLE public.{table} TO {owner}" for table in relations)
    statements.extend(
        (
            f"GRANT UPDATE (request_id) ON TABLE public.erasure_requests TO {owner}",
            f"GRANT EXECUTE ON FUNCTION {signature} TO idis_erasure_executor",
        )
    )
    return statements


def _canonical_sql(statement: str) -> str:
    return " ".join(statement.split())


def _sql_hex_tuple(statements: list[str] | tuple[str, ...]) -> list[str]:
    return [_canonical_sql(statement).encode("utf-8").hex() for statement in statements]


def _approved_erasure_return(
    tenant_expression: str,
    deal_expression: str,
    return_expression: str,
) -> str:
    return (
        "IF session_user='idis_erasure_executor' AND "
        "current_user='idis_erasure_function_owner' THEN "
        "IF public.idis_task7_approved_erasure_context_v1("
        f"{tenant_expression},{deal_expression}) THEN RETURN {return_expression}; END IF; "
        "END IF;"
    )


def _hold_lock_upgrade_statements() -> tuple[str, ...]:
    function = (
        "CREATE FUNCTION public.idis_task7_legal_hold_lock_guard() RETURNS trigger "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$"
        "DECLARE locked_tenant uuid; BEGIN "
        "IF TG_OP='UPDATE' AND NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN "
        "RAISE EXCEPTION 'TASK7_ERASURE_HOLD_TENANT_CHANGE_REJECTED' "
        "USING ERRCODE='23514'; END IF; "
        "IF TG_OP='DELETE' THEN locked_tenant:=OLD.tenant_id; "
        "ELSE locked_tenant:=NEW.tenant_id; END IF; "
        "IF pg_catalog.current_setting('idis.tenant_id',true) IS DISTINCT FROM "
        "locked_tenant::text THEN "
        "RAISE EXCEPTION 'TASK7_ERASURE_HOLD_TENANT_CONTEXT_REJECTED' "
        "USING ERRCODE='42501'; END IF; "
        "PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended("
        "'idis-task7-erasure-tenant-lock-v1:'||locked_tenant::text,0)); "
        "IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END$$"
    )
    return (
        function,
        "ALTER FUNCTION public.idis_task7_legal_hold_lock_guard() OWNER TO "
        "idis_erasure_function_owner",
        "REVOKE ALL ON FUNCTION public.idis_task7_legal_hold_lock_guard() FROM PUBLIC",
        "CREATE TRIGGER trg_task7_legal_holds_tenant_lock BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.legal_holds FOR EACH ROW EXECUTE FUNCTION "
        "public.idis_task7_legal_hold_lock_guard()",
    )


def _guard_authority_statement() -> str:
    return (
        "CREATE FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) "
        "RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path=pg_catalog,pg_temp AS $$SELECT "
        "session_user='idis_erasure_executor' AND "
        "current_user='idis_erasure_function_owner' AND "
        "pg_catalog.current_setting('idis.tenant_id',true)=$1::text AND "
        "pg_catalog.current_setting('idis.erasure_context',true)='true' AND "
        "EXISTS (SELECT 1 FROM public.erasure_requests e WHERE e.tenant_id=$1 AND "
        "e.deal_id=$2 AND e.status IN ('REQUESTED','FAILED'))$$"
    )


def _strict_proof_guard_replacement() -> str:
    approved_delete = _approved_erasure_return("OLD.tenant_id", "OLD.deal_id", "OLD")
    return (
        "CREATE OR REPLACE FUNCTION public.idis_prevent_strict_preflight_proof_mutation() "
        "RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN "
        "IF TG_OP='INSERT' THEN IF NEW.consumed_at IS NULL AND "
        "NEW.consumed_by_run_id IS NULL THEN PERFORM 1 FROM public.deals WHERE "
        "deals.tenant_id=NEW.tenant_id AND deals.deal_id=NEW.deal_id FOR KEY SHARE; "
        "IF FOUND THEN RETURN NEW; END IF; END IF; RAISE EXCEPTION "
        "'Proof insert requires unconsumed state and a live same-tenant deal'; "
        "ELSIF TG_OP='UPDATE' THEN IF NEW.proof_id IS NOT DISTINCT FROM OLD.proof_id "
        "AND NEW.tenant_id IS NOT DISTINCT FROM OLD.tenant_id AND NEW.deal_id IS NOT "
        "DISTINCT FROM OLD.deal_id AND NEW.corpus_hash IS NOT DISTINCT FROM OLD.corpus_hash "
        "AND NEW.strict_profile_generation IS NOT DISTINCT FROM OLD.strict_profile_generation "
        "AND NEW.strict_profile_hmac IS NOT DISTINCT FROM OLD.strict_profile_hmac AND "
        "NEW.pipeline_contract_version IS NOT DISTINCT FROM OLD.pipeline_contract_version "
        "AND NEW.pipeline_contract_hash IS NOT DISTINCT FROM OLD.pipeline_contract_hash AND "
        "NEW.expected_consumer_edge_rule_hash IS NOT DISTINCT FROM "
        "OLD.expected_consumer_edge_rule_hash AND NEW.static_decision_set_hash IS NOT DISTINCT "
        "FROM OLD.static_decision_set_hash AND NEW.methodology_version_hashes IS NOT DISTINCT "
        "FROM OLD.methodology_version_hashes AND NEW.promoted_pointer_hash IS NOT DISTINCT FROM "
        "OLD.promoted_pointer_hash AND NEW.provider_receipt_set_hash IS NOT DISTINCT FROM "
        "OLD.provider_receipt_set_hash AND NEW.dependency_receipt_set_hash IS NOT DISTINCT FROM "
        "OLD.dependency_receipt_set_hash AND NEW.binding_hash IS NOT DISTINCT FROM "
        "OLD.binding_hash AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at AND "
        "NEW.expires_at IS NOT DISTINCT FROM OLD.expires_at AND OLD.consumed_at IS NULL AND "
        "OLD.consumed_by_run_id IS NULL AND OLD.expires_at>pg_catalog.statement_timestamp() AND "
        "NEW.consumed_at IS NOT NULL AND NEW.consumed_at<=OLD.expires_at AND "
        "NEW.consumed_by_run_id IS NOT NULL AND EXISTS (SELECT 1 FROM public.runs WHERE "
        "runs.run_id=NEW.consumed_by_run_id AND runs.tenant_id=NEW.tenant_id AND "
        "runs.deal_id=NEW.deal_id AND runs.strict_preflight_proof_id=NEW.proof_id) THEN "
        "RETURN NEW; END IF; RAISE EXCEPTION "
        "'Strict proof immutable; only first reciprocal consumption is allowed'; "
        f"ELSIF TG_OP='DELETE' THEN {approved_delete} IF NOT EXISTS (SELECT 1 FROM "
        "public.deals WHERE deals.tenant_id=OLD.tenant_id AND deals.deal_id=OLD.deal_id) "
        "THEN RETURN OLD; END IF; RAISE EXCEPTION "
        "'Strict preflight proof cannot be deleted while its same-tenant deal exists'; "
        "END IF; RAISE EXCEPTION 'Unsupported strict preflight proof mutation'; END$$"
    )


def _selection_guard_replacement() -> str:
    approved_delete = _approved_erasure_return("OLD.tenant_id", "OLD.deal_id", "OLD")
    return (
        "CREATE OR REPLACE FUNCTION public.idis_task5_selection_guard() RETURNS trigger "
        "LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$DECLARE "
        "commercial_count integer; financial_count integer; BEGIN "
        f"IF TG_OP='DELETE' THEN {approved_delete} END IF; "
        "IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'RUN_METHODOLOGY_SELECTION_IMMUTABLE' "
        "USING ERRCODE='23514'; END IF; SELECT question_count INTO commercial_count FROM "
        "public.methodology_versions WHERE tenant_id=NEW.tenant_id AND "
        "methodology_version_record_id=NEW.commercial_dd_version_record_id AND "
        "methodology_version_id=NEW.commercial_methodology_version_id AND "
        "version_hash=NEW.commercial_version_hash AND methodology_type='commercial_dd' AND "
        "status='FROZEN'; SELECT question_count INTO financial_count FROM "
        "public.methodology_versions WHERE tenant_id=NEW.tenant_id AND "
        "methodology_version_record_id=NEW.financial_dd_version_record_id AND "
        "methodology_version_id=NEW.financial_methodology_version_id AND "
        "version_hash=NEW.financial_version_hash AND methodology_type='financial_dd' AND "
        "status='FROZEN'; IF commercial_count IS DISTINCT FROM 21 OR financial_count IS "
        "DISTINCT FROM 79 OR commercial_count+financial_count<>NEW.selected_question_count "
        "THEN RAISE EXCEPTION 'RUN_METHODOLOGY_SELECTION_INVALID' USING ERRCODE='23514'; "
        "END IF; RETURN NEW; END$$"
    )


def _coverage_guard_replacement() -> str:
    approved_delete = _approved_erasure_return("OLD.tenant_id", "OLD.deal_id", "OLD")
    return (
        "CREATE OR REPLACE FUNCTION public.idis_task5_coverage_scope_guard() RETURNS trigger "
        "LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN "
        f"IF TG_OP='DELETE' THEN {approved_delete} RAISE EXCEPTION "
        "'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING ERRCODE='23514'; END IF; "
        "IF TG_OP='UPDATE' AND (NEW.coverage_record_id IS DISTINCT FROM "
        "OLD.coverage_record_id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR "
        "NEW.deal_id IS DISTINCT FROM OLD.deal_id OR NEW.run_id IS DISTINCT FROM OLD.run_id "
        "OR NEW.selection_id IS DISTINCT FROM OLD.selection_id OR "
        "NEW.methodology_version_record_id IS DISTINCT FROM OLD.methodology_version_record_id "
        "OR NEW.methodology_version_id IS DISTINCT FROM OLD.methodology_version_id OR "
        "NEW.methodology_question_record_id IS DISTINCT FROM "
        "OLD.methodology_question_record_id OR NEW.methodology_question_id IS DISTINCT FROM "
        "OLD.methodology_question_id OR NEW.methodology_type IS DISTINCT FROM "
        "OLD.methodology_type OR NEW.created_at IS DISTINCT FROM OLD.created_at) THEN "
        "RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING ERRCODE='23514'; "
        "END IF; IF NOT EXISTS (SELECT 1 FROM public.run_methodology_selections s JOIN "
        "public.methodology_questions q ON q.tenant_id=NEW.tenant_id AND "
        "q.methodology_version_record_id=NEW.methodology_version_record_id AND "
        "q.methodology_version_id=NEW.methodology_version_id AND "
        "q.methodology_question_record_id=NEW.methodology_question_record_id AND "
        "q.methodology_question_id=NEW.methodology_question_id AND "
        "q.methodology_type=NEW.methodology_type WHERE s.tenant_id=NEW.tenant_id AND "
        "s.deal_id=NEW.deal_id AND s.run_id=NEW.run_id AND s.selection_id=NEW.selection_id "
        "AND ((q.methodology_type='commercial_dd' AND "
        "q.methodology_version_record_id=s.commercial_dd_version_record_id) OR "
        "(q.methodology_type='financial_dd' AND "
        "q.methodology_version_record_id=s.financial_dd_version_record_id))) THEN "
        "RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_INVALID' USING ERRCODE='23514'; END IF; "
        "NEW.updated_at:=pg_catalog.now(); RETURN NEW; END$$"
    )


def _erasure_requested_replacement() -> str:
    return (
        "CREATE OR REPLACE FUNCTION public.idis_task6_erasure_requested(checked_tenant uuid,"
        "checked_deal uuid) RETURNS boolean LANGUAGE plpgsql STABLE "
        "SET search_path=pg_catalog,pg_temp AS $$BEGIN IF NOT ("
        "session_user='idis_erasure_executor' AND "
        "current_user='idis_erasure_function_owner') THEN RETURN false; END IF; "
        "RETURN public.idis_task7_approved_erasure_context_v1(checked_tenant,checked_deal); "
        "END$$"
    )


def _immutable_guard_replacement() -> str:
    approved_delete = _approved_erasure_return("OLD.tenant_id", "OLD.deal_id", "OLD")
    return (
        "CREATE OR REPLACE FUNCTION public.idis_task6_immutable_guard() RETURNS trigger "
        "LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN "
        "IF TG_OP='INSERT' THEN IF NOT public.idis_task6_current_lease_is_valid("
        "NEW.tenant_id,NEW.deal_id,NEW.run_id) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' "
        "USING ERRCODE='40001'; END IF; RETURN NEW; END IF; "
        f"IF TG_OP='DELETE' THEN {approved_delete} END IF; RAISE EXCEPTION "
        "'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE' USING ERRCODE='23514'; END$$"
    )


def _attempt_guard_replacement() -> str:
    approved_delete = _approved_erasure_return("OLD.tenant_id", "OLD.deal_id", "OLD")
    return (
        "CREATE OR REPLACE FUNCTION public.idis_task6_attempt_guard() RETURNS trigger "
        "LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$DECLARE "
        "current_tenant_id uuid; current_deal_id uuid; current_run_id uuid; "
        "current_owner_id uuid; current_fencing_token bigint; BEGIN "
        f"IF TG_OP='DELETE' THEN {approved_delete} RAISE EXCEPTION "
        "'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE' USING ERRCODE='23514'; END IF; "
        "BEGIN current_tenant_id:=NULLIF(pg_catalog.current_setting("
        "'idis.worker_lease_tenant_id',true),'')::uuid; "
        "current_deal_id:=NULLIF(pg_catalog.current_setting("
        "'idis.worker_lease_deal_id',true),'')::uuid; "
        "current_run_id:=NULLIF(pg_catalog.current_setting("
        "'idis.worker_lease_run_id',true),'')::uuid; "
        "current_owner_id:=NULLIF(pg_catalog.current_setting("
        "'idis.worker_lease_owner_id',true),'')::uuid; "
        "current_fencing_token:=NULLIF(pg_catalog.current_setting("
        "'idis.worker_lease_fencing_token',true),'')::bigint; EXCEPTION WHEN OTHERS THEN "
        "RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END; "
        "IF NOT EXISTS (SELECT 1 FROM public.runs r WHERE r.tenant_id=NEW.tenant_id AND "
        "r.deal_id=NEW.deal_id AND r.run_id=NEW.run_id AND r.status='RUNNING' AND "
        "r.tenant_id=current_tenant_id AND r.deal_id=current_deal_id AND "
        "r.run_id=current_run_id AND r.lease_owner_id=current_owner_id AND "
        "r.lease_fencing_token=current_fencing_token AND "
        "r.lease_expires_at>pg_catalog.statement_timestamp()) THEN RAISE EXCEPTION "
        "'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; "
        "IF NOT EXISTS (SELECT 1 FROM public.run_steps s WHERE s.tenant_id=NEW.tenant_id "
        "AND s.run_id=NEW.run_id AND s.step_id=NEW.run_step_id AND "
        "s.step_name='METHODOLOGY_EXTRACTION_TASK_EXECUTION' AND s.step_order=6 AND "
        "s.status='RUNNING') THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' "
        "USING ERRCODE='40001'; END IF; IF TG_OP='INSERT' THEN "
        "IF NEW.status<>'STARTED' THEN RAISE EXCEPTION 'METHODOLOGY_ATTEMPT_MUST_START' "
        "USING ERRCODE='23514'; END IF; IF NEW.lease_owner_id<>current_owner_id OR "
        "NEW.lease_fencing_token<>current_fencing_token THEN RAISE EXCEPTION "
        "'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; RETURN NEW; END IF; "
        "IF OLD.status<>'STARTED' OR NEW.status NOT IN ('SUCCEEDED','FAILED','OUTCOME_UNKNOWN') "
        "OR (pg_catalog.to_jsonb(NEW)-ARRAY['status','observed_model','provider_request_id',"
        "'provider_response_sha256','dispatched_call_sha256','completed_at','latency_ms',"
        "'input_tokens','output_tokens','stop_reason','budget_terminal_state','receipt_sha256',"
        "'safe_failure_code']::text[]) IS DISTINCT FROM (pg_catalog.to_jsonb(OLD)-ARRAY["
        "'status','observed_model','provider_request_id','provider_response_sha256',"
        "'dispatched_call_sha256','completed_at','latency_ms','input_tokens','output_tokens',"
        "'stop_reason','budget_terminal_state','receipt_sha256','safe_failure_code']::text[]) "
        "THEN RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE' "
        "USING ERRCODE='23514'; END IF; IF NEW.status IN ('SUCCEEDED','FAILED') AND ("
        "current_owner_id<>OLD.lease_owner_id OR "
        "current_fencing_token<>OLD.lease_fencing_token) THEN RAISE EXCEPTION "
        "'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; "
        "IF NEW.status='OUTCOME_UNKNOWN' AND NOT ((current_owner_id=OLD.lease_owner_id AND "
        "current_fencing_token=OLD.lease_fencing_token) OR "
        "current_fencing_token>OLD.lease_fencing_token) THEN RAISE EXCEPTION "
        "'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; RETURN NEW; END$$"
    )


def _result_outputs_guard_replacement() -> str:
    approved_delete = _approved_erasure_return("OLD.tenant_id", "OLD.deal_id", "NULL")
    return (
        "CREATE OR REPLACE FUNCTION public.idis_task6_validate_result_outputs() RETURNS trigger "
        "LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$DECLARE "
        "checked_result uuid; checked_outcome text; output_count bigint; BEGIN "
        f"IF TG_OP='DELETE' THEN {approved_delete} END IF; "
        "checked_result:=COALESCE(NEW.result_id,OLD.result_id); SELECT outcome INTO "
        "checked_outcome FROM public.methodology_extraction_results WHERE "
        "result_id=checked_result; IF checked_outcome IS NULL THEN RETURN NULL; END IF; "
        "SELECT pg_catalog.count(*) INTO output_count FROM "
        "public.methodology_result_factual_outputs WHERE result_id=checked_result; "
        "IF (checked_outcome='EVIDENCE_FOUND' AND output_count=0) OR "
        "(checked_outcome='EVIDENCE_NOT_FOUND' AND output_count<>0) THEN RAISE EXCEPTION "
        "'METHODOLOGY_RESULT_FACTUAL_OUTPUT_MISMATCH' USING ERRCODE='23514'; END IF; "
        "RETURN NULL; END$$"
    )


def _legacy_guard_upgrade_statements() -> tuple[str, ...]:
    signature = "public.idis_task7_approved_erasure_context_v1(uuid,uuid)"
    return (
        _guard_authority_statement(),
        f"ALTER FUNCTION {signature} OWNER TO idis_erasure_function_owner",
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC",
        _strict_proof_guard_replacement(),
        _selection_guard_replacement(),
        _coverage_guard_replacement(),
        _erasure_requested_replacement(),
        _immutable_guard_replacement(),
        _attempt_guard_replacement(),
        _result_outputs_guard_replacement(),
    )


_LEGACY_GUARD_RESTORE_STATEMENTS = (
    # Revision 0031: source line comments are omitted before one-line canonicalization.
    (
        "CREATE OR REPLACE FUNCTION idis_prevent_strict_preflight_proof_mutation() RETURNS "
        "TRIGGER AS $$ BEGIN IF TG_OP = 'INSERT' THEN IF NEW.consumed_at IS NULL AND "
        "NEW.consumed_by_run_id IS NULL THEN PERFORM 1 FROM deals WHERE "
        "deals.tenant_id = NEW.tenant_id AND deals.deal_id = NEW.deal_id FOR KEY SHARE; IF FOUND "
        "THEN RETURN NEW; END IF; END IF; RAISE EXCEPTION 'Proof insert requires unconsumed state "
        "and a live same-tenant deal'; ELSIF TG_OP = 'UPDATE' THEN IF NEW.proof_id IS NOT DISTINCT "
        "FROM OLD.proof_id AND NEW.tenant_id IS NOT DISTINCT FROM OLD.tenant_id AND NEW.deal_id IS "
        "NOT DISTINCT FROM OLD.deal_id AND NEW.corpus_hash IS NOT DISTINCT FROM "
        "OLD.corpus_hash AND "
        "NEW.strict_profile_generation IS NOT DISTINCT FROM OLD.strict_profile_generation AND "
        "NEW.strict_profile_hmac IS NOT DISTINCT FROM OLD.strict_profile_hmac AND "
        "NEW.pipeline_contract_version IS NOT DISTINCT FROM OLD.pipeline_contract_version AND "
        "NEW.pipeline_contract_hash IS NOT DISTINCT FROM OLD.pipeline_contract_hash AND "
        "NEW.expected_consumer_edge_rule_hash IS NOT DISTINCT FROM "
        "OLD.expected_consumer_edge_rule_hash AND NEW.static_decision_set_hash IS NOT "
        "DISTINCT FROM "
        "OLD.static_decision_set_hash AND NEW.methodology_version_hashes IS NOT DISTINCT FROM "
        "OLD.methodology_version_hashes AND NEW.promoted_pointer_hash IS NOT DISTINCT FROM "
        "OLD.promoted_pointer_hash AND NEW.provider_receipt_set_hash IS NOT DISTINCT FROM "
        "OLD.provider_receipt_set_hash AND NEW.dependency_receipt_set_hash IS NOT DISTINCT FROM "
        "OLD.dependency_receipt_set_hash AND NEW.binding_hash IS NOT DISTINCT FROM "
        "OLD.binding_hash "
        "AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at AND NEW.expires_at IS NOT DISTINCT "
        "FROM OLD.expires_at AND OLD.consumed_at IS NULL AND OLD.consumed_by_run_id IS NULL AND "
        "OLD.expires_at > statement_timestamp() AND NEW.consumed_at IS NOT NULL AND "
        "NEW.consumed_at "
        "<= OLD.expires_at AND NEW.consumed_by_run_id IS NOT NULL AND EXISTS ( SELECT 1 FROM runs "
        "WHERE runs.run_id = NEW.consumed_by_run_id AND runs.tenant_id = NEW.tenant_id AND "
        "runs.deal_id = NEW.deal_id AND runs.strict_preflight_proof_id = NEW.proof_id ) THEN "
        "RETURN NEW; END IF; RAISE EXCEPTION 'Strict proof immutable; only first reciprocal "
        "consumption is "
        "allowed'; ELSIF TG_OP = 'DELETE' THEN IF NOT EXISTS ( SELECT 1 FROM deals WHERE "
        "deals.tenant_id = OLD.tenant_id AND deals.deal_id = OLD.deal_id ) THEN RETURN OLD; "
        "END IF; "
        "RAISE EXCEPTION 'Strict preflight proof cannot be deleted while its same-tenant deal "
        "exists'; END IF; RAISE EXCEPTION 'Unsupported strict preflight proof mutation'; END; $$ "
        "LANGUAGE plpgsql;"
    ),
    # Revision 0036: run methodology selection guard.
    (
        "CREATE OR REPLACE FUNCTION idis_task5_selection_guard() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ DECLARE commercial_count integer; financial_count integer; BEGIN IF "
        "TG_OP = 'DELETE' "
        "AND EXISTS ( SELECT 1 FROM erasure_requests e WHERE e.tenant_id = OLD.tenant_id AND "
        "e.deal_id = OLD.deal_id AND e.status = 'REQUESTED' ) THEN RETURN OLD; END IF; IF TG_OP <> "
        "'INSERT' THEN RAISE EXCEPTION 'RUN_METHODOLOGY_SELECTION_IMMUTABLE' USING "
        "ERRCODE = '23514'; "
        "END IF; SELECT question_count INTO commercial_count FROM methodology_versions WHERE "
        "tenant_id = NEW.tenant_id AND methodology_version_record_id = "
        "NEW.commercial_dd_version_record_id AND methodology_version_id = "
        "NEW.commercial_methodology_version_id AND version_hash = NEW.commercial_version_hash AND "
        "methodology_type = 'commercial_dd' AND status = 'FROZEN'; SELECT question_count INTO "
        "financial_count FROM methodology_versions WHERE tenant_id = NEW.tenant_id AND "
        "methodology_version_record_id = NEW.financial_dd_version_record_id AND "
        "methodology_version_id = NEW.financial_methodology_version_id AND version_hash = "
        "NEW.financial_version_hash AND methodology_type = 'financial_dd' AND status = "
        "'FROZEN'; IF "
        "commercial_count IS DISTINCT FROM 21 OR financial_count IS DISTINCT FROM 79 OR "
        "commercial_count + financial_count <> NEW.selected_question_count THEN RAISE EXCEPTION "
        "'RUN_METHODOLOGY_SELECTION_INVALID' USING ERRCODE = '23514'; END IF; RETURN NEW; END $$"
    ),
    # Revision 0036: methodology coverage guard.
    (
        "CREATE OR REPLACE FUNCTION idis_task5_coverage_scope_guard() RETURNS trigger LANGUAGE "
        "plpgsql AS $$ BEGIN IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; IF "
        "TG_OP = 'UPDATE' AND ( NEW.coverage_record_id IS DISTINCT FROM "
        "OLD.coverage_record_id OR NEW.tenant_id IS DISTINCT "
        "FROM OLD.tenant_id OR NEW.deal_id IS DISTINCT FROM OLD.deal_id OR NEW.run_id IS DISTINCT "
        "FROM OLD.run_id OR NEW.selection_id IS DISTINCT FROM OLD.selection_id OR "
        "NEW.methodology_version_record_id IS DISTINCT FROM OLD.methodology_version_record_id OR "
        "NEW.methodology_version_id IS DISTINCT FROM OLD.methodology_version_id OR "
        "NEW.methodology_question_record_id IS DISTINCT FROM OLD.methodology_question_record_id OR "
        "NEW.methodology_question_id IS DISTINCT FROM OLD.methodology_question_id OR "
        "NEW.methodology_type IS DISTINCT FROM OLD.methodology_type OR NEW.created_at IS DISTINCT "
        "FROM OLD.created_at ) THEN RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING "
        "ERRCODE = '23514'; END IF; IF NOT EXISTS ( SELECT 1 FROM "
        "run_methodology_selections s JOIN "
        "methodology_questions q ON q.tenant_id = NEW.tenant_id AND "
        "q.methodology_version_record_id = NEW.methodology_version_record_id AND "
        "q.methodology_version_id = NEW.methodology_version_id AND "
        "q.methodology_question_record_id "
        "= NEW.methodology_question_record_id AND q.methodology_question_id = "
        "NEW.methodology_question_id AND q.methodology_type = NEW.methodology_type WHERE "
        "s.tenant_id = NEW.tenant_id AND s.deal_id = NEW.deal_id AND s.run_id = NEW.run_id AND "
        "s.selection_id = "
        "NEW.selection_id AND ( (q.methodology_type = 'commercial_dd' AND "
        "q.methodology_version_record_id = s.commercial_dd_version_record_id) OR "
        "(q.methodology_type = 'financial_dd' AND q.methodology_version_record_id = "
        "s.financial_dd_version_record_id) ) ) THEN RAISE EXCEPTION "
        "'METHODOLOGY_COVERAGE_SCOPE_INVALID' USING ERRCODE = '23514'; END IF; NEW.updated_at := "
        "now(); RETURN NEW; END $$"
    ),
    # Revision 0037: legacy erasure-request predicate.
    (
        "CREATE OR REPLACE FUNCTION idis_task6_erasure_requested( checked_tenant UUID, "
        "checked_deal "
        "UUID ) RETURNS boolean LANGUAGE sql STABLE AS $$ SELECT EXISTS ( SELECT 1 FROM "
        "erasure_requests e WHERE e.tenant_id = checked_tenant AND e.deal_id = checked_deal AND "
        "e.status = 'REQUESTED' ) $$"
    ),
    # Revision 0037: immutable extraction records.
    (
        "CREATE OR REPLACE FUNCTION idis_task6_immutable_guard() RETURNS trigger LANGUAGE "
        "plpgsql AS $$ BEGIN IF TG_OP = 'INSERT' THEN IF NOT "
        "idis_task6_current_lease_is_valid( NEW.tenant_id, "
        "NEW.deal_id, NEW.run_id ) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = "
        "'40001'; END IF; RETURN NEW; END IF; IF TG_OP = 'DELETE' AND "
        "idis_task6_erasure_requested(OLD.tenant_id, OLD.deal_id) THEN RETURN OLD; END IF; RAISE "
        "EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE' USING ERRCODE = '23514'; END $$"
    ),
    # Revision 0037: extraction attempt lifecycle.
    (
        "CREATE OR REPLACE FUNCTION idis_task6_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ DECLARE current_tenant_id UUID; current_deal_id UUID; current_run_id UUID; "
        "current_owner_id UUID; current_fencing_token BIGINT; BEGIN IF TG_OP = 'DELETE' THEN IF "
        "idis_task6_erasure_requested(OLD.tenant_id, OLD.deal_id) THEN RETURN OLD; END IF; RAISE "
        "EXCEPTION 'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE' USING ERRCODE = '23514'; "
        "END IF; BEGIN "
        "current_tenant_id := NULLIF( current_setting('idis.worker_lease_tenant_id', true), '' "
        ")::uuid; current_deal_id := NULLIF( "
        "current_setting('idis.worker_lease_deal_id', true), '' "
        ")::uuid; current_run_id := NULLIF( current_setting('idis.worker_lease_run_id', true), '' "
        ")::uuid; current_owner_id := NULLIF( "
        "current_setting('idis.worker_lease_owner_id', true), '' "
        ")::uuid; current_fencing_token := NULLIF( "
        "current_setting('idis.worker_lease_fencing_token', true), '' )::bigint; EXCEPTION "
        "WHEN OTHERS "
        "THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001'; END; IF NOT EXISTS ( "
        "SELECT 1 FROM runs r WHERE r.tenant_id = NEW.tenant_id AND r.deal_id = NEW.deal_id AND "
        "r.run_id = NEW.run_id AND r.status = 'RUNNING' AND r.tenant_id = current_tenant_id AND "
        "r.deal_id = current_deal_id AND r.run_id = current_run_id AND r.lease_owner_id = "
        "current_owner_id AND r.lease_fencing_token = current_fencing_token AND "
        "r.lease_expires_at > statement_timestamp() ) THEN RAISE EXCEPTION "
        "'WORKER_LEASE_FENCED' USING ERRCODE = '40001'; "
        "END IF; IF NOT EXISTS ( SELECT 1 FROM run_steps s WHERE s.tenant_id = NEW.tenant_id AND "
        "s.run_id = NEW.run_id AND s.step_id = NEW.run_step_id AND s.step_name = "
        "'METHODOLOGY_EXTRACTION_TASK_EXECUTION' AND s.step_order = 6 AND "
        "s.status = 'RUNNING' ) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING "
        "ERRCODE = '40001'; END IF; IF TG_OP = 'INSERT' THEN IF NEW.status <> 'STARTED' THEN "
        "RAISE EXCEPTION 'METHODOLOGY_ATTEMPT_MUST_START' USING "
        "ERRCODE = '23514'; END IF; IF NEW.lease_owner_id <> current_owner_id OR "
        "NEW.lease_fencing_token <> current_fencing_token THEN RAISE EXCEPTION "
        "'WORKER_LEASE_FENCED' "
        "USING ERRCODE = '40001'; END IF; RETURN NEW; END IF; IF OLD.status <> 'STARTED' OR "
        "NEW.status NOT IN ('SUCCEEDED', 'FAILED', 'OUTCOME_UNKNOWN') OR (to_jsonb(NEW) - ARRAY[ "
        "'status', 'observed_model', 'provider_request_id', 'provider_response_sha256', "
        "'dispatched_call_sha256', 'completed_at', 'latency_ms', 'input_tokens', 'output_tokens', "
        "'stop_reason', 'budget_terminal_state', 'receipt_sha256', 'safe_failure_code' "
        "]::text[]) IS "
        "DISTINCT FROM (to_jsonb(OLD) - ARRAY[ 'status', 'observed_model', 'provider_request_id', "
        "'provider_response_sha256', 'dispatched_call_sha256', 'completed_at', 'latency_ms', "
        "'input_tokens', 'output_tokens', 'stop_reason', 'budget_terminal_state', "
        "'receipt_sha256', "
        "'safe_failure_code' ]::text[]) THEN RAISE EXCEPTION "
        "'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE' USING ERRCODE = '23514'; END IF; IF "
        "NEW.status IN "
        "('SUCCEEDED', 'FAILED') AND ( current_owner_id <> OLD.lease_owner_id OR "
        "current_fencing_token <> OLD.lease_fencing_token ) THEN RAISE EXCEPTION "
        "'WORKER_LEASE_FENCED' USING ERRCODE = '40001'; END IF; IF "
        "NEW.status = 'OUTCOME_UNKNOWN' AND "
        "NOT ( ( current_owner_id = OLD.lease_owner_id AND current_fencing_token = "
        "OLD.lease_fencing_token ) OR current_fencing_token > OLD.lease_fencing_token ) THEN RAISE "
        "EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001'; END IF; RETURN NEW; END $$"
    ),
    # Revision 0037: deferred result/output cardinality validator.
    (
        "CREATE OR REPLACE FUNCTION idis_task6_validate_result_outputs() RETURNS trigger LANGUAGE "
        "plpgsql AS $$ DECLARE checked_result UUID; checked_outcome TEXT; output_count BIGINT; "
        "BEGIN checked_result := COALESCE(NEW.result_id, OLD.result_id); SELECT outcome INTO "
        "checked_outcome FROM methodology_extraction_results WHERE result_id = checked_result; "
        "IF checked_outcome IS "
        "NULL THEN RETURN NULL; END IF; SELECT count(*) INTO output_count FROM "
        "methodology_result_factual_outputs WHERE result_id = checked_result; IF "
        "(checked_outcome = "
        "'EVIDENCE_FOUND' AND output_count = 0) OR (checked_outcome = 'EVIDENCE_NOT_FOUND' AND "
        "output_count <> 0) THEN RAISE EXCEPTION "
        "'METHODOLOGY_RESULT_FACTUAL_OUTPUT_MISMATCH' USING "
        "ERRCODE = '23514'; END IF; RETURN NULL; END $$"
    ),
)


def _legacy_guard_downgrade_statements() -> tuple[str, ...]:
    canonical = tuple(_canonical_sql(statement) for statement in _LEGACY_GUARD_RESTORE_STATEMENTS)
    expected_hashes = tuple(row[6] for row in ERASURE_LEGACY_GUARD_REPLACEMENTS)
    actual_hashes = tuple(
        hashlib.sha256(statement.encode("utf-8")).hexdigest() for statement in canonical
    )
    if actual_hashes != expected_hashes:
        raise MaterializationError(
            "TASK7_ERASURE_LEGACY_GUARD_RESTORATION_INVALID",
            "literal 0031/0036/0037 guard restoration bytes differ from their reviewed hashes",
        )
    return (
        *canonical,
        "DROP FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid)",
    )


def _future_activation_census(
    future_native_rows: list[dict[str, Any]],
    future_reference_rows: list[dict[str, Any]],
    future_child_rows: list[dict[str, Any]],
) -> list[list[str]]:
    schema_by_table: dict[str, str] = {}
    for rows, table_key in (
        (future_native_rows, "planned_table"),
        (future_reference_rows, "table_name"),
        (future_child_rows, "table_name"),
    ):
        for row in rows:
            table = row.get(table_key)
            schema_hash = row.get("schema_sha256")
            if (
                not isinstance(table, str)
                or not isinstance(schema_hash, str)
                or len(schema_hash) != 64
                or any(character not in "0123456789abcdef" for character in schema_hash)
                or table in schema_by_table
            ):
                raise MaterializationError(
                    "TASK7_ERASURE_ACTIVATION_CENSUS_INVALID",
                    "future relation schema census is incomplete or duplicated",
                )
            schema_by_table[table] = schema_hash
    if tuple(schema_by_table) != FUTURE_ERASURE_RELATIONS:
        raise MaterializationError(
            "TASK7_ERASURE_ACTIVATION_CENSUS_INVALID",
            "future relation census order differs from FUTURE38",
        )
    return [[table, schema_by_table[table]] for table in FUTURE_ERASURE_RELATIONS]


def _erasure_execution_row(
    future_native_rows: list[dict[str, Any]],
    future_reference_rows: list[dict[str, Any]],
    future_child_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    _validate_erasure_execution_split()
    census = _future_activation_census(
        future_native_rows,
        future_reference_rows,
        future_child_rows,
    )
    census_hash = hashlib.sha256(
        ERASURE_ACTIVATION_CENSUS_HASH_DOMAIN + _canonical_bytes(census)
    ).hexdigest()
    runner_signature = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
    current_body = (_canonical_sql(_erasure_runner_statement(CURRENT_ERASURE_RELATIONS)),)
    current_acl = tuple(
        _canonical_sql(statement)
        for statement in _erasure_acl_statements(CURRENT_ERASURE_RELATIONS)
    )
    future_body = (_canonical_sql(_erasure_runner_statement(COMPLETE_ERASURE_RELATIONS)),)
    future_acl = tuple(
        _canonical_sql(statement)
        for statement in _erasure_acl_statements(COMPLETE_ERASURE_RELATIONS)
    )
    hold_lock_upgrade = tuple(
        _canonical_sql(statement) for statement in _hold_lock_upgrade_statements()
    )
    hold_lock_downgrade = (
        "DROP TRIGGER trg_task7_legal_holds_tenant_lock ON public.legal_holds",
        "DROP FUNCTION public.idis_task7_legal_hold_lock_guard()",
    )
    legacy_guard_upgrade = tuple(
        _canonical_sql(statement) for statement in _legacy_guard_upgrade_statements()
    )
    legacy_guard_downgrade = _legacy_guard_downgrade_statements()
    runner_drop = f"DROP FUNCTION {runner_signature}"
    future_activation = (runner_drop, *future_body, *future_acl)
    future_restoration = (runner_drop, *current_body, *current_acl)
    row: dict[str, Any] = {
        "execution_ordinal": 0,
        "execution_id": "approved-erasure-execution-v1",
        "execution_version": "1",
        "runner_function_signature": runner_signature,
        "activation_state": "CURRENT48_ACTIVE_FUTURE38_FROZEN",
        "current_relation_tuple": list(CURRENT_ERASURE_RELATIONS),
        "future_relation_tuple": list(FUTURE_ERASURE_RELATIONS),
        "complete_relation_tuple": list(COMPLETE_ERASURE_RELATIONS),
        "declared_read_relation_tuple": list(ERASURE_EXECUTION_READ_RELATIONS),
        "advisory_lock_tuple": list(ERASURE_ADVISORY_LOCK_TUPLE),
        "current_body_sql_utf8_hex_tuple": _sql_hex_tuple(current_body),
        "current_acl_sql_utf8_hex_tuple": _sql_hex_tuple(current_acl),
        "future_body_sql_utf8_hex_tuple": _sql_hex_tuple(future_body),
        "future_acl_sql_utf8_hex_tuple": _sql_hex_tuple(future_acl),
        "future_activation_census_tuple": census,
        "future_activation_census_sha256": census_hash,
        "hold_lock_contract_tuple": list(ERASURE_HOLD_LOCK_CONTRACT),
        "guard_authority_contract_tuple": list(ERASURE_GUARD_AUTHORITY_CONTRACT),
        "legacy_guard_replacement_tuple": list(ERASURE_LEGACY_GUARD_REPLACEMENTS),
        "hold_lock_upgrade_sql_utf8_hex_tuple": _sql_hex_tuple(hold_lock_upgrade),
        "hold_lock_downgrade_sql_utf8_hex_tuple": _sql_hex_tuple(hold_lock_downgrade),
        "legacy_guard_upgrade_sql_utf8_hex_tuple": _sql_hex_tuple(legacy_guard_upgrade),
        "legacy_guard_downgrade_sql_utf8_hex_tuple": _sql_hex_tuple(legacy_guard_downgrade),
        "future_activation_sql_utf8_hex_tuple": _sql_hex_tuple(future_activation),
        "future_restoration_sql_utf8_hex_tuple": _sql_hex_tuple(future_restoration),
        "legal_red_case_tuple": [
            "tests/slice100_task7/test_erasure_execution_registry_red.py::"
            "test_current_body_is_current48_only_and_future_body_is_exact_runner86"
        ],
        "illegal_red_case_tuple": [
            "tests/slice100_task7/test_erasure_execution_registry_red.py::"
            "test_erasure_execution_authority_boundary_does_not_claim_unprovable_audit_binding"
        ],
        "direct_sql_red_case_tuple": [
            "tests/slice100_task7/test_erasure_execution_registry_red.py::"
            "test_current_and_future_acl_are_exact_and_future_acl_is_not_migration0038_runtime_sql"
        ],
    }
    row["expected_sha256"] = _erasure_execution_hash(row)
    if tuple(row) != ERASURE_EXECUTION_FIELDS:
        raise MaterializationError(
            "TASK7_ERASURE_EXECUTION_ROW_INVALID",
            "erasure execution row differs from ErasureExecutionSpecV1",
        )
    return row


def _approved_erasure_authority(
    erasure_materialized: dict[str, Any],
) -> dict[str, Any]:
    if erasure_materialized.get("physical_table_count") != 72:
        raise MaterializationBlockedError(
            ERASURE_AUTHORITY_BLOCKER_CODE,
            "the reviewed erasure census is unavailable to the future append-only authority",
        )
    authority_id = "approved-erasure-append-only-v1"
    marker = _marker(authority_id)
    function_name = "idis_task7_approved_erasure_append_only_v1"
    body = (
        "BEGIN IF TG_OP='INSERT' THEN RETURN NEW; END IF; "
        f"IF TG_OP='UPDATE' THEN RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END IF; "
        "IF TG_OP='DELETE' AND session_user='idis_erasure_executor' AND "
        "current_user='idis_erasure_function_owner' AND "
        "pg_catalog.current_setting('idis.erasure_context',true)='true' AND "
        "NULLIF(pg_catalog.current_setting('idis.tenant_id',true),'')::uuid=OLD.tenant_id "
        "AND EXISTS (SELECT 1 FROM public.erasure_requests r WHERE "
        "r.tenant_id=OLD.tenant_id AND r.deal_id=OLD.deal_id AND "
        "r.status IN ('REQUESTED','FAILED') FOR UPDATE) THEN RETURN OLD; END IF; "
        f"RAISE EXCEPTION '{marker}' USING ERRCODE='23514'; END"
    )
    statements = [
        f"CREATE FUNCTION public.{function_name}() RETURNS trigger LANGUAGE plpgsql "
        f"SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $${body}$$"
    ]
    targets: list[list[str]] = []
    for table in FUTURE_ERASURE_RELATIONS:
        trigger = f"trg_task7_{table}_approved_erasure_append_only"
        targets.append([table, trigger])
        statements.append(
            f"CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE OR DELETE ON public.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{function_name}()"
        )
    statements.extend(
        (
            f"ALTER FUNCTION public.{function_name}() OWNER TO idis_erasure_function_owner",
            f"REVOKE ALL ON FUNCTION public.{function_name}() FROM PUBLIC",
        )
    )
    return _sql_row(
        authority_id=authority_id,
        authority_kind="APPEND_ONLY_TRIGGER",
        targets=targets,
        statements=statements,
    )


def _sql_authority_rows(erasure_materialized: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _non_erasure_sql_authority_rows()
    erasure_row = _approved_erasure_authority(erasure_materialized)
    erasure_ordinal = AUTHORITY_IDS.index("approved-erasure-append-only-v1")
    ordered = [*rows[:erasure_ordinal], erasure_row, *rows[erasure_ordinal:]]
    if tuple(row.get("authority_id") for row in ordered) != AUTHORITY_IDS:
        raise MaterializationError(
            "TASK7_SQL_AUTHORITY_ORDER_INVALID",
            "complete SQL authority registry differs from the reviewed order",
        )
    for ordinal, row in enumerate(ordered):
        if (
            tuple(row) != SQL_AUTHORITY_FIELDS
            or row["authority_ordinal"] != ordinal
            or row["expected_sha256"] != _sql_authority_hash(row)
        ):
            raise MaterializationError(
                "TASK7_SQL_AUTHORITY_ROW_INVALID",
                f"complete SQL authority row {ordinal} is not canonically hash-bound",
            )
    return ordered


def _checked_rows(section: object, *, name: str, expected_count: int) -> list[Any]:
    if not isinstance(section, dict) or tuple(section) not in {
        ("rows", "expected_sha256"),
        ("rows", "expected_vector_sha256"),
        ("rows", "expected_manifest_sha256"),
        ("rows", "expected_sha256", "expected_combined_sha256"),
    }:
        raise MaterializationError(
            "TASK7_V2_INPUT_INVALID",
            f"{name} has an unexpected section shape",
        )
    rows = section.get("rows")
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise MaterializationError(
            "TASK7_V2_INPUT_INVALID",
            f"{name} row census differs from {expected_count}",
        )
    return rows


def _reviewed_design_bytes() -> bytes:
    literal = [
        "full-strict-v3-reviewed-design-literal-v1",
        ["pipeline_contract_version", "full-strict-v3"],
        ["semantic_step_count", 28],
        ["edge_rule_count", 47],
        ["usage_route_count", 1756],
        ["usage_extractor_count", 19],
        ["providers", list(PROVIDERS)],
        ["debate_roles", list(DEBATE_ROLES)],
        ["analysis_roles", list(ANALYSIS_ROLES)],
        ["layer2_roles", list(LAYER2_ROLES)],
        ["future_native_relations", list(FUTURE_NATIVE_TABLES)],
        ["future_reference_relations", list(FUTURE_REFERENCE_TABLES)],
        ["future_child_relations", list(FUTURE_CHILD_TABLES)],
        ["sql_authorities", list(AUTHORITY_IDS)],
        ["erasure_guard_authority", "future_append_only_only"],
        ["erasure_current_relations", list(CURRENT_ERASURE_RELATIONS)],
        ["erasure_future_relations", list(FUTURE_ERASURE_RELATIONS)],
        ["erasure_complete_relations", list(COMPLETE_ERASURE_RELATIONS)],
        ["erasure_read_relations", list(ERASURE_EXECUTION_READ_RELATIONS)],
        ["erasure_advisory_lock", list(ERASURE_ADVISORY_LOCK_TUPLE)],
    ]
    return _canonical_bytes(literal)


def _metadata(v2: dict[str, Any]) -> dict[str, Any]:
    source = v2.get("metadata")
    if not isinstance(source, dict):
        raise MaterializationError("TASK7_V2_INPUT_INVALID", "metadata is not an object")
    reviewed_design = _reviewed_design_bytes()
    return {
        "artifact_schema": "full-strict-v3-reviewed-preimage-v1",
        "append_frozen": True,
        "pipeline_contract_version": "full-strict-v3",
        "pydantic_version": "2.12.5",
        "hash_protocol": source.get("hash_protocol"),
        "reviewed_design_sha256": hashlib.sha256(reviewed_design).hexdigest(),
        "reviewed_design_utf8_hex": reviewed_design.hex(),
        "section_order": list(SECTION_ORDER),
    }


def _canonicalization_section(v2: dict[str, Any]) -> dict[str, Any]:
    source = v2.get("canonicalization_contract")
    if not isinstance(source, dict) or not isinstance(source.get("canonical_json"), dict):
        raise MaterializationError(
            "TASK7_V2_INPUT_INVALID",
            "canonicalization_contract is malformed",
        )
    canonical_json = source["canonical_json"]
    literal = [
        "full-strict-v3-canonicalization-contract-v1",
        canonical_json,
        ["double_precision", "finite_binary64_network_order_negative_zero_normalized"],
        ["timestamps", "utc_rfc3339_six_fractional_digits_Z"],
        ["arrays", ["ORDERED", "SET"]],
    ]
    return {
        "canonical_json": canonical_json,
        "reviewed_literal_utf8_hex": _hex(literal),
    }


def _registry(rows: list[Any], *, domain: str) -> dict[str, Any]:
    return {"rows": rows, "expected_sha256": _registry_hash(domain, rows)}


def _build_artifact(v2: dict[str, Any], authority: ModuleType) -> dict[str, Any]:
    semantic_rows = _checked_rows(
        v2.get("semantic_step_registry"),
        name="semantic_step_registry",
        expected_count=28,
    )
    provider_rows = _checked_rows(
        v2.get("provider_registry"), name="provider_registry", expected_count=18
    )
    role_rows = _checked_rows(v2.get("role_registry"), name="role_registry", expected_count=3)
    deliverable_rows = _checked_rows(
        v2.get("deliverable_registry"), name="deliverable_registry", expected_count=15
    )
    route_rows = _checked_rows(
        v2.get("usage_route_registry"), name="usage_route_registry", expected_count=1756
    )
    extractor_rows = _checked_rows(
        v2.get("usage_extractor_registry"),
        name="usage_extractor_registry",
        expected_count=19,
    )

    erasure_materialized = _erasure_materialized(v2["erasure_materialized"])
    current_rows = authority.expected_current_ddl_rows()
    if not isinstance(current_rows, list) or len(current_rows) != 34:
        raise MaterializationError(
            "TASK7_REVIEWED_AUTHORITY_INVALID",
            "current DDL authority must contain exactly 34 rows",
        )
    future_native_rows = _future_native_rows(authority)
    future_reference_rows = _reference_rows()
    future_child_rows = _child_rows()
    sql_rows = _sql_authority_rows(erasure_materialized)
    erasure_execution_row = _erasure_execution_row(
        future_native_rows,
        future_reference_rows,
        future_child_rows,
    )

    artifact: dict[str, Any] = {
        "metadata": _metadata(v2),
        "canonicalization_contract": _canonicalization_section(v2),
        "tuple_schema_registry": {
            "rows": _tuple_schema_rows(v2["tuple_schema_registry"]["rows"]),
        },
        "digest_recipe_registry": _digest_recipe_section(v2["digest_recipe_registry"]),
        "uuid_namespace_registry": _uuid_section(v2["uuid_namespace_registry"]),
        "schema_registry": v2["schema_registry"],
        "semantic_step_registry": {
            "rows": semantic_rows,
            "expected_vector_sha256": v2["semantic_step_registry"]["expected_vector_sha256"],
        },
        "current_ddl_registry": {
            "rows": current_rows,
            "expected_sha256": _h(
                [
                    "task7-ddl-registry-v2",
                    current_rows,
                    erasure_materialized["expected_sha256"],
                ]
            ),
        },
        "future_native_authority_registry": _registry(
            future_native_rows,
            domain="task7-future-native-authority-registry-v2",
        ),
        "future_reference_authority_registry": _registry(
            future_reference_rows,
            domain="task7-future-reference-authority-registry-v2",
        ),
        "future_child_authority_registry": _registry(
            future_child_rows,
            domain="task7-future-child-authority-registry-v2",
        ),
        "sql_authority_registry": _registry(
            sql_rows,
            domain=SQL_AUTHORITY_REGISTRY_HASH_DOMAIN,
        ),
        "erasure_execution_registry": _registry(
            [erasure_execution_row],
            domain=ERASURE_EXECUTION_REGISTRY_HASH_DOMAIN,
        ),
        "provider_registry": _registry(provider_rows, domain="task7-provider-registry-v1"),
        "role_registry": _registry(role_rows, domain="task7-role-registry-v1"),
        "deliverable_registry": _registry(
            deliverable_rows,
            domain="task7-deliverable-registry-v1",
        ),
        "producer_selector_registry": v2["producer_selector_registry"],
        "consumer_selector_registry": v2["consumer_selector_registry"],
        "applicability_registry": v2["applicability_registry"],
        "edge_rule_registry": v2["edge_rule_registry"],
        "usage_route_registry": {
            "rows": route_rows,
            "expected_sha256": _registry_hash("usage-path-route-v2", route_rows),
        },
        "usage_extractor_registry": {
            "rows": extractor_rows,
            "expected_sha256": v2["usage_extractor_registry"]["expected_sha256"],
        },
        "erasure_baseline": v2["erasure_baseline"],
        "erasure_materialized": erasure_materialized,
        "activation_assertions": {},
    }
    artifact["edge_rule_registry"] = _expand_edge_literals(artifact)
    artifact["activation_assertions"] = {
        "semantic_step_count": len(semantic_rows),
        "edge_rule_count": len(artifact["edge_rule_registry"]["rows"]),
        "usage_route_count": len(route_rows),
        "usage_extractor_count": len(extractor_rows),
        "current_ddl_table_count": len(current_rows),
        "future_native_table_count": len(future_native_rows),
        "future_reference_table_count": len(future_reference_rows),
        "future_child_table_count": len(future_child_rows),
        "baseline_erasure_phase_count": v2["erasure_baseline"]["phase_count"],
        "baseline_physical_table_count": v2["erasure_baseline"]["physical_table_count"],
        "materialized_erasure_phase_count": erasure_materialized["phase_count"],
        "materialized_physical_table_count": erasure_materialized["physical_table_count"],
        "erasure_current_relation_count": len(CURRENT_ERASURE_RELATIONS),
        "erasure_future_relation_count": len(FUTURE_ERASURE_RELATIONS),
        "erasure_complete_relation_count": len(COMPLETE_ERASURE_RELATIONS),
        "expected_erasure_execution_sha256": erasure_execution_row["expected_sha256"],
        "semantic_hashes_unchanged": True,
        "expected_usage_route_sha256": artifact["usage_route_registry"]["expected_sha256"],
        "expected_usage_extractor_sha256": artifact["usage_extractor_registry"]["expected_sha256"],
        "expected_baseline_erasure_sha256": v2["erasure_baseline"]["expected_sha256"],
        "expected_materialized_erasure_sha256": erasure_materialized["expected_sha256"],
    }
    if tuple(artifact) != SECTION_ORDER:
        raise MaterializationError(
            "TASK7_V3_ASSEMBLY_INVALID",
            "assembled artifact section order differs from the closed contract",
        )
    return artifact


def _render_artifact(artifact: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            artifact,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _write_exclusive(path: Path, content: bytes) -> None:
    if path.exists():
        raise MaterializationError(
            "TASK7_V3_OUTPUT_ALREADY_EXISTS",
            "append-frozen output already exists",
        )
    if not path.parent.is_dir():
        raise MaterializationError(
            "TASK7_V3_OUTPUT_PARENT_MISSING",
            "output parent directory does not exist",
        )
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            stream.write(content)
            stream.flush()
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise


def _parse_args(argv: list[str]) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Materialize the append-frozen full-strict-v3 reviewed preimage once."
    )
    parser.add_argument(
        "--rejected-artifact",
        type=Path,
        default=repo_root / "contracts" / "full_strict_v2_reviewed_preimage_v1.json",
    )
    parser.add_argument(
        "--reviewed-authority",
        type=Path,
        default=(repo_root / "tests" / "slice100_task7" / "_full_strict_v3_reviewed_authority.py"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "contracts" / "full_strict_v3_reviewed_preimage_v1.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.output.exists():
            raise MaterializationError(
                "TASK7_V3_OUTPUT_ALREADY_EXISTS",
                "append-frozen output already exists",
            )
        rejected = _load_rejected_artifact(args.rejected_artifact)
        authority = _load_module(args.reviewed_authority)
        artifact = _build_artifact(rejected, authority)
        rendered = _render_artifact(artifact)
        _write_exclusive(args.output, rendered)
    except MaterializationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V3_ARTIFACT_MATERIALIZED:{hashlib.sha256(rendered).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
