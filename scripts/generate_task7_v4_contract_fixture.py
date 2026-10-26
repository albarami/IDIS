"""Derive the frozen full-strict-v4 contract fixture from its sole byte authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

_SECTION_ORDER = (
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
    "rights_index_policy_registry",
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

_ACTIVATION_ASSERTION_FIELDS = (
    "semantic_step_count",
    "edge_rule_count",
    "usage_route_count",
    "usage_extractor_count",
    "current_ddl_table_count",
    "future_native_table_count",
    "future_reference_table_count",
    "future_child_table_count",
    "baseline_erasure_phase_count",
    "baseline_physical_table_count",
    "materialized_erasure_phase_count",
    "materialized_physical_table_count",
    "erasure_current_relation_count",
    "erasure_future_relation_count",
    "erasure_complete_relation_count",
    "expected_erasure_execution_sha256",
    "semantic_hashes_unchanged",
    "expected_usage_route_sha256",
    "expected_usage_extractor_sha256",
    "expected_baseline_erasure_sha256",
    "expected_materialized_erasure_sha256",
)

_COMPONENT_ORDER = (
    "schema_registry_hash",
    "semantic_step_vector_hash",
    "task7_ddl_registry_hash",
    "future_native_authority_registry_hash",
    "future_reference_authority_registry_hash",
    "future_child_authority_registry_hash",
    "sql_authority_registry_hash",
    "erasure_execution_registry_hash",
    "provider_registry_hash",
    "rights_index_policy_registry_hash",
    "role_registry_hash",
    "deliverable_registry_hash",
    "selector_registry_hash",
    "applicability_registry_hash",
    "usage_extractor_registry_hash",
    "edge_manifest_hash",
    "uuid_namespace_registry_hash",
    "task7_erasure_schedule_hash",
)

_AUTHORITY_ROW_SCHEMA_HASH_DOMAIN = b"full-strict-v4-authority-row-schema-sha256-v1\x00"
_SQL_AUTHORITY_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_ERASURE_EXECUTION_ROW_HASH_DOMAIN = b"full-strict-v4-erasure-execution-row-sha256-v1\x00"
_ERASURE_ACTIVATION_CENSUS_HASH_DOMAIN = b"full-strict-v4-erasure-future-activation-census-v1\x00"
_PROVIDER_IDS = (
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
_RIGHTS_INDEX_POLICY_ROWS = (
    (
        0,
        "sec_edgar",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        1,
        "companies_house",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        2,
        "github",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        3,
        "fred",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        4,
        "finnhub",
        "RED",
        "COMMERCIAL_PROD",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        5,
        "fmp",
        "RED",
        "COMMERCIAL_PROD",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        6,
        "world_bank",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        7,
        "escwa_catalog",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        8,
        "escwa_ispar",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        9,
        "qatar_open_data",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        10,
        "hackernews",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        11,
        "gdelt",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        12,
        "patentsview",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        13,
        "wayback",
        "YELLOW",
        "ATTRIBUTION_PROD",
        "external-context-store-v1",
        "attribution-bound-v1",
        True,
    ),
    (
        14,
        "google_news_rss",
        "YELLOW",
        "ATTRIBUTION_PROD",
        "external-context-store-v1",
        "attribution-bound-v1",
        True,
    ),
    (
        15,
        "epo_open_patent",
        "GREEN",
        "NONE",
        "external-context-store-v1",
        "attribution-not-required-v1",
        True,
    ),
    (
        16,
        "semantic_scholar",
        "YELLOW",
        "ATTRIBUTION_PROD",
        "external-context-store-v1",
        "attribution-bound-v1",
        True,
    ),
    (
        17,
        "perplexity",
        "RED",
        "COMMERCIAL_PROD",
        "external-context-no-store-v1",
        "attribution-bound-v1",
        False,
    ),
)
_LOWERCASE_HEX_PATTERN = re.compile(r"^(?:[0-9a-f]{2})+$")
_SYMBOLIC_MACRO_PATTERNS = (
    re.compile(r"\b(?:PROVIDERS18|DEBATE5|ANALYSIS8|LAYER2_3)\b"),
    re.compile(
        r"\b(?:D_SOURCE|D_METHOD|D_IDENTITY|D_DECISION|D_LAYER2|"
        r"DOMAIN_DELIVERABLE_ROUTE|PROVIDER_ANALYSIS_ROUTE)(?:\[[^\]]+\])?\b"
    ),
    re.compile(r"\bfor\s+[A-Za-z_][A-Za-z0-9_]*\s+in\s+"),
    re.compile(r"\bexpanded\s+[A-Za-z_][A-Za-z0-9_]*\s+over\b"),
    re.compile(r"\bprovider-specific literal\b", flags=re.IGNORECASE),
    re.compile(r"\{[A-Z][A-Z0-9_]*(?:\[[^{}]+\])?\}"),
)

_SQL_AUTHORITY_FIELDS = (
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
_ERASURE_EXECUTION_FIELDS = (
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
_REQUIRED_SQL_AUTHORITY_IDS = (
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
_REQUIRED_SQL_AUTHORITY_KINDS = {
    "dashboard-root-equation-v1": "CONSTRAINT_TRIGGER",
    "graph-rag-namespace-v1": "CHECK",
    "rag-query-parent-coordinate-v1": "CONSTRAINT_TRIGGER",
    "layer2-readiness-v1": "CHECK",
    "extract-rag-context-coordinate-v1": "CONSTRAINT_TRIGGER",
    "debate-muhasabah-exact-one-v1": "CONSTRAINT_TRIGGER",
    "analysis-muhasabah-exact-one-v1": "CONSTRAINT_TRIGGER",
    "layer2-muhasabah-exact-one-v1": "CONSTRAINT_TRIGGER",
    "grading-dispatch-and-consistency-v1": "CONSTRAINT_TRIGGER",
    "scoring-binary64-finite-v1": "CHECK",
    "scoring-binary64-negative-zero-v1": "BEFORE_ROW_TRIGGER",
    "approved-erasure-append-only-v1": "APPEND_ONLY_TRIGGER",
    "native-root-exact-one-v1": "CONSTRAINT_TRIGGER",
    "identity-identifier-projection-v1": "CONSTRAINT_TRIGGER",
    "scoring-dimension-projection-v1": "CONSTRAINT_TRIGGER",
    "generic-claim-projection-v1": "CONSTRAINT_TRIGGER",
    "generic-evidence-projection-v1": "CONSTRAINT_TRIGGER",
}
_REQUIRED_SQL_AUTHORITY_EVENTS: dict[str, tuple[str, ...] | None] = {
    "dashboard-root-equation-v1": ("INSERT", "UPDATE", "DELETE"),
    "graph-rag-namespace-v1": (),
    "rag-query-parent-coordinate-v1": ("INSERT", "UPDATE", "DELETE"),
    "layer2-readiness-v1": (),
    "extract-rag-context-coordinate-v1": ("INSERT", "UPDATE", "DELETE"),
    "debate-muhasabah-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
    "analysis-muhasabah-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
    "layer2-muhasabah-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
    "grading-dispatch-and-consistency-v1": ("INSERT", "UPDATE", "DELETE"),
    "scoring-binary64-finite-v1": (),
    "scoring-binary64-negative-zero-v1": ("INSERT", "UPDATE"),
    "approved-erasure-append-only-v1": ("INSERT", "UPDATE", "DELETE"),
    "native-root-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
    "identity-identifier-projection-v1": ("INSERT", "UPDATE", "DELETE"),
    "scoring-dimension-projection-v1": ("INSERT", "UPDATE", "DELETE"),
    "generic-claim-projection-v1": ("INSERT", "UPDATE", "DELETE"),
    "generic-evidence-projection-v1": ("INSERT", "UPDATE", "DELETE"),
}
_SQL_GUARD_NODE_PREFIX = "tests/slice100_task7/test_sql_authority_guards_postgres_red.py::"
_SQL_FLOAT_NODE_PREFIX = "tests/slice100_task7/test_sql_authority_float8_postgres_red.py::"


def _sql_guard_nodes(legal: str, illegal: str, direct: str) -> tuple[str, str, str]:
    return tuple(_SQL_GUARD_NODE_PREFIX + name for name in (legal, illegal, direct))


_REQUIRED_SQL_RED_CASES = {
    "dashboard-root-equation-v1": _sql_guard_nodes(
        "test_dashboard_root_equation_accepts_legal_row[gap-no-calculation]",
        "test_dashboard_root_equation_rejects_illegal_row[answered-missing-result]",
        "test_dashboard_root_equation_rejects_direct_sql_bypass[gap-wrong-outcome]",
    ),
    "graph-rag-namespace-v1": _sql_guard_nodes(
        "test_graph_rag_namespace_accepts_legal_row",
        "test_graph_rag_namespace_rejects_illegal_row[external-null-provider-graph_projection_results-graph_result_id]",
        "test_graph_rag_namespace_rejects_direct_sql_bypass[graph_projection_results-graph_result_id]",
    ),
    "rag-query-parent-coordinate-v1": _sql_guard_nodes(
        "test_rag_query_parent_coordinate_accepts_legal_row",
        "test_rag_query_parent_coordinate_rejects_illegal_row[namespace]",
        "test_rag_query_parent_coordinate_rejects_direct_sql_bypass[namespace]",
    ),
    "layer2-readiness-v1": _sql_guard_nodes(
        "test_layer2_readiness_accepts_legal_row",
        "test_layer2_readiness_rejects_illegal_row[status]",
        "test_layer2_readiness_rejects_direct_sql_bypass[status]",
    ),
    "extract-rag-context-coordinate-v1": _sql_guard_nodes(
        "test_extract_rag_context_coordinate_accepts_legal_row",
        "test_extract_rag_context_coordinate_rejects_illegal_row[step]",
        "test_extract_rag_context_coordinate_rejects_direct_sql_bypass[step]",
    ),
    "debate-muhasabah-exact-one-v1": _sql_guard_nodes(
        "test_debate_muhasabah_exact_one_accepts_legal_row",
        "test_debate_muhasabah_exact_one_rejects_illegal_row[duplicate]",
        "test_debate_muhasabah_exact_one_rejects_direct_sql_bypass[duplicate]",
    ),
    "analysis-muhasabah-exact-one-v1": _sql_guard_nodes(
        "test_analysis_muhasabah_exact_one_accepts_legal_row",
        "test_analysis_muhasabah_exact_one_rejects_illegal_row[duplicate]",
        "test_analysis_muhasabah_exact_one_rejects_direct_sql_bypass[duplicate]",
    ),
    "layer2-muhasabah-exact-one-v1": _sql_guard_nodes(
        "test_layer2_muhasabah_exact_one_accepts_legal_row",
        "test_layer2_muhasabah_exact_one_rejects_illegal_row[duplicate]",
        "test_layer2_muhasabah_exact_one_rejects_direct_sql_bypass[duplicate]",
    ),
    "grading-dispatch-and-consistency-v1": _sql_guard_nodes(
        "test_grading_dispatch_and_consistency_accepts_legal_row",
        "test_grading_dispatch_and_consistency_rejects_illegal_row",
        "test_grading_dispatch_and_consistency_rejects_direct_sql_bypass",
    ),
    "scoring-binary64-finite-v1": (
        _SQL_FLOAT_NODE_PREFIX + "test_direct_sql_accepts_binary64_boundaries",
        _SQL_FLOAT_NODE_PREFIX
        + "test_direct_sql_rejects_every_nonfinite_binary64_value['NaN'::double precision]",
        _SQL_FLOAT_NODE_PREFIX
        + (
            "test_direct_sql_rejects_finite_out_of_range_values["
            "-0.000001::double precision-0.5::double precision]"
        ),
    ),
    "scoring-binary64-negative-zero-v1": (
        _SQL_FLOAT_NODE_PREFIX + "test_direct_sql_normalizes_negative_zero_to_positive_zero_bits",
        _SQL_FLOAT_NODE_PREFIX
        + "test_direct_sql_update_normalizes_negative_zero_to_positive_zero_bits",
        _SQL_GUARD_NODE_PREFIX + "test_scoring_binary64_negative_zero_rejects_direct_sql_bypass",
    ),
    "approved-erasure-append-only-v1": _sql_guard_nodes(
        "test_approved_erasure_append_only_accepts_legal_row",
        "test_approved_erasure_append_only_rejects_illegal_row",
        "test_approved_erasure_append_only_rejects_direct_sql_bypass",
    ),
    "native-root-exact-one-v1": _sql_guard_nodes(
        "test_native_root_exact_one_accepts_legal_row[methodology_truth_dashboard_rows]",
        "test_native_root_exact_one_rejects_illegal_row[missing-child]",
        "test_native_root_exact_one_rejects_direct_sql_bypass[missing-child]",
    ),
    "identity-identifier-projection-v1": _sql_guard_nodes(
        "test_identity_identifier_projection_accepts_legal_row",
        "test_identity_identifier_projection_rejects_illegal_row",
        "test_identity_identifier_projection_rejects_direct_sql_bypass",
    ),
    "scoring-dimension-projection-v1": _sql_guard_nodes(
        "test_scoring_dimension_projection_accepts_legal_row",
        "test_scoring_dimension_projection_rejects_illegal_row",
        "test_scoring_dimension_projection_rejects_direct_sql_bypass",
    ),
    "generic-claim-projection-v1": _sql_guard_nodes(
        "test_generic_claim_projection_accepts_legal_row",
        "test_generic_claim_projection_rejects_illegal_row[producer-step]",
        "test_generic_claim_projection_rejects_direct_sql_bypass[producer-step]",
    ),
    "generic-evidence-projection-v1": _sql_guard_nodes(
        "test_generic_evidence_projection_accepts_legal_row",
        "test_generic_evidence_projection_rejects_illegal_row[producer-step]",
        "test_generic_evidence_projection_rejects_direct_sql_bypass[producer-step]",
    ),
}
_FUTURE_NATIVE_TABLES = (
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
_FUTURE_REFERENCE_TABLES = (
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
_FUTURE_CHILD_TABLES = (
    "company_identity_identifiers",
    "scoring_output_dimensions",
    "generic_extraction_claim_links",
    "generic_extraction_evidence_links",
    "methodology_truth_dashboard_root_links",
)
_FUTURE_RELATION_TABLES = (
    *_FUTURE_NATIVE_TABLES,
    *_FUTURE_REFERENCE_TABLES,
    *_FUTURE_CHILD_TABLES,
)
_REQUIRED_TARGET_TABLES: dict[str, tuple[str, ...]] = {
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
    "approved-erasure-append-only-v1": _FUTURE_RELATION_TABLES,
    "native-root-exact-one-v1": (
        "run_output_objects",
        *_FUTURE_NATIVE_TABLES,
        "generic_extraction_claim_links",
        "generic_extraction_evidence_links",
    ),
    "identity-identifier-projection-v1": (
        "company_identity_outputs",
        "company_identity_identifiers",
    ),
    "scoring-dimension-projection-v1": ("scoring_outputs", "scoring_output_dimensions"),
    "generic-claim-projection-v1": (
        "generic_extraction_claim_links",
        "run_output_objects",
    ),
    "generic-evidence-projection-v1": (
        "generic_extraction_evidence_links",
        "run_output_objects",
    ),
}
_REQUIRED_PARENT_RELATIONS: dict[str, tuple[str, ...]] = {
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
        *_FUTURE_NATIVE_TABLES,
        "generic_extraction_claim_links",
        "generic_extraction_evidence_links",
        "run_step_outputs",
    ),
    "identity-identifier-projection-v1": (
        "company_identity_outputs",
        "company_identity_identifiers",
    ),
    "scoring-dimension-projection-v1": ("scoring_outputs", "scoring_output_dimensions"),
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
_APPROVED_RELATION_FUNCTIONS = {
    "jsonb_array_elements",
    "jsonb_array_elements_text",
    "unnest",
}
_APPROVED_SQL_FUNCTIONS = {
    "abs",
    "array",
    "array_agg",
    "bool_and",
    "bool_or",
    "cardinality",
    "check",
    "coalesce",
    "convert_to",
    "count",
    "decode",
    "encode",
    "exists",
    "float8send",
    "if",
    "in",
    "jsonb_agg",
    "jsonb_array_elements",
    "jsonb_array_elements_text",
    "jsonb_build_array",
    "jsonb_build_object",
    "jsonb_typeof",
    "max",
    "min",
    "nullif",
    "sum",
    "unnest",
    "values",
    "when",
}
_APPROVED_PG_CATALOG_ONLY_FUNCTIONS = {
    "hashtextextended",
    "pg_advisory_xact_lock",
    "sha256",
}
_SESSION_STATE_FUNCTIONS = {
    "current_setting",
    "set_config",
    "pg_backend_pid",
    "txid_current",
}
_APPROVED_ERASURE_GUARD_AUTHORITY = "approved-erasure-append-only-v1"
_APPROVED_ERASURE_EXECUTION_AUTHORITY = "approved-erasure-execution-v1"
_APPROVED_ERASURE_SESSION_AUTHORITIES = frozenset(
    {
        _APPROVED_ERASURE_GUARD_AUTHORITY,
        _APPROVED_ERASURE_EXECUTION_AUTHORITY,
    }
)
_APPROVED_ERASURE_SETTING_KEYS = frozenset({"idis.erasure_context", "idis.tenant_id"})
_APPROVED_ERASURE_PRINCIPAL_PSEUDO_VALUES = frozenset({"current_user", "session_user"})
_ERASURE_RUNNER_IDENTITY = (
    "public",
    "idis_task7_execute_approved_erasure",
    ("uuid", "uuid", "uuid"),
)
_ERASURE_FUNCTION_OWNER = "idis_erasure_function_owner"
_ERASURE_EXECUTOR = "idis_erasure_executor"
_ERASURE_RUNNER_POSITIONAL_ARGUMENTS = frozenset({1, 2, 3})
_APPROVED_ERASURE_RUNNER_SEQUENCE = (
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
_CURRENT_ERASURE_RELATIONS = (
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
_FUTURE_ERASURE_RELATIONS = _FUTURE_RELATION_TABLES
_ERASURE_EXECUTION_READ_RELATIONS = (
    "erasure_requests",
    "deterministic_calculations",
    "runs",
    "human_gates",
    "legal_holds",
    "document_artifacts",
)
_ERASURE_ADVISORY_LOCK_TUPLE = (
    "erasure-tenant-advisory-lock-v1",
    "PostgreSQL-16",
    "pg_catalog.hashtextextended",
    "idis-task7-erasure-tenant-lock-v1:",
    0,
    "pg_catalog.pg_advisory_xact_lock",
)
_ERASURE_RUNNER_SIGNATURE = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_ERASURE_EXECUTION_ACTIVATION_STATE = "CURRENT48_ACTIVE_FUTURE38_FROZEN"
_ERASURE_HOLD_LOCK_GUARD_SIGNATURE = "public.idis_task7_legal_hold_lock_guard()"
_ERASURE_HOLD_LOCK_TRIGGER = "trg_task7_legal_holds_tenant_lock"
_ERASURE_HOLD_LOCK_CONTRACT = (
    "legal-hold-lock-enforcement-v1",
    _ERASURE_HOLD_LOCK_GUARD_SIGNATURE,
    _ERASURE_HOLD_LOCK_TRIGGER,
    "public.legal_holds",
    "BEFORE",
    ("INSERT", "UPDATE", "DELETE"),
    "ROW",
    "O",
    "PostgreSQL-16-trigger-acquires-xact-lock-v1",
    "TASK7_ERASURE_HOLD_TENANT_CONTEXT_REJECTED",
    "TASK7_ERASURE_HOLD_TENANT_CHANGE_REJECTED",
)
_ERASURE_GUARD_AUTHORITY_SIGNATURE = "public.idis_task7_approved_erasure_context_v1(uuid,uuid)"
_ERASURE_GUARD_AUTHORITY_CONTRACT = (
    "approved-erasure-guard-context-v1",
    _ERASURE_GUARD_AUTHORITY_SIGNATURE,
    "SECURITY DEFINER",
    _ERASURE_FUNCTION_OWNER,
    ("search_path=pg_catalog, pg_temp",),
    _ERASURE_EXECUTOR,
    ("idis.tenant_id", "idis.erasure_context"),
    ("REQUESTED", "FAILED"),
    "transaction-local-only",
)
_ERASURE_HOLD_LOCK_UPGRADE_BYTE_FIXTURES = (
    (820, "bc51bfcbd8fdb6b31e018f3fc6391a47b0b8f7742891a1d7fd5951b3726c470c"),
    (93, "41c104a6221c79dd5ac67777617371e067fe118d90c4a2a4758830a7c8986cf8"),
    (76, "f139808f3cdd7d41a46c3343282a5a49deb634f1bf27223b2d3ef7e97e854bc3"),
    (176, "c98b7e89e0b1514a3da9501dc6285b3cf05a82a7d929938dcd076e63cf557899"),
)
_ERASURE_LEGACY_GUARD_UPGRADE_BYTE_FIXTURES = (
    (518, "46051b154a0f24f91a22c9fc4c45c042fb5c2a79b2c79ba28a7066750d12992f"),
    (108, "679521b944673375e72ab6e9bfdea08a54c9c93491203e191a02a13e2f75218e"),
    (91, "e31f322b91b5bee7803bf55af38c11d185f1b6d8e2eedbc98b3b25c99452e02f"),
    (2789, "47b3a8e62bb770bd42eaf7ca9bfc7cf1e3585c9626df36840a7e80059105b613"),
    (1476, "564ea8d682639b79cf12ef4f4e96b634deffc9facf3f4a354354079c5975ee47"),
    (2173, "f90336d2e180c47e845a54e4c7d3eb92a6b6f2969b249c08c936c122122f55e9"),
    (396, "ec30043d5b97beb6a49c37e6a60e95b1bc5396242670cd664b993757112f33ab"),
    (663, "fd172b4749005e824edacf6a9491f1264eb05d29a934d39306eab9d94ede63ac"),
    (3491, "549e3d652ae2fb48004eb09b03ce296376ba1acbbe30316314330e4997eec003"),
    (1032, "3c0037ce6f6a7ca81fc0398551c0904d757dac8a142f70ff34e48621576bb1e5"),
)
_ERASURE_LEGACY_GUARD_RESTORATION_BYTE_LENGTHS = (
    2548,
    1406,
    1885,
    284,
    500,
    3382,
    751,
)
_ERASURE_LEGACY_GUARD_REPLACEMENTS = (
    (
        "0031",
        "public.idis_prevent_strict_preflight_proof_mutation()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (
            (
                "public.strict_preflight_proofs",
                "strict_preflight_proofs_immutability",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
        ),
        "1a05ce8af35fe5102cba13e3198cdf92e82b966a15ea88552024fc7244c7e139",
    ),
    (
        "0036",
        "public.idis_task5_selection_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (
            (
                "public.run_methodology_selections",
                "trg_run_methodology_selections_guard",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
        ),
        "e4504847befc282ffbfce6d6c123fef5eaf1aac12b2478d496247a8f2df42956",
    ),
    (
        "0036",
        "public.idis_task5_coverage_scope_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (
            (
                "public.methodology_coverage_records",
                "trg_methodology_coverage_scope",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
        ),
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
        (
            (
                "public.methodology_extraction_tasks",
                "trg_methodology_extraction_tasks_immutable",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
            (
                "public.methodology_extraction_task_spans",
                "trg_methodology_extraction_task_spans_immutable",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
            (
                "public.methodology_extraction_results",
                "trg_methodology_extraction_results_immutable",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
            (
                "public.methodology_result_factual_outputs",
                "trg_methodology_result_factual_outputs_immutable",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
            (
                "public.methodology_result_claim_links",
                "trg_methodology_result_claim_links_immutable",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
            (
                "public.methodology_result_evidence_links",
                "trg_methodology_result_evidence_links_immutable",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
            (
                "public.methodology_result_sanad_links",
                "trg_methodology_result_sanad_links_immutable",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
            (
                "public.methodology_result_defect_links",
                "trg_methodology_result_defect_links_immutable",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
        ),
        "6cd61a832cf2af65c911480ce4a6534350a9500b6d526f47354b3d7c9c1059cf",
    ),
    (
        "0037",
        "public.idis_task6_attempt_guard()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (
            (
                "public.methodology_extraction_attempts",
                "trg_methodology_extraction_attempts_lifecycle",
                "BEFORE",
                ("INSERT", "UPDATE", "DELETE"),
                "ROW",
                "O",
                False,
                False,
            ),
        ),
        "580389555daf84edd33739439dc46c1203781d681f5d1a30d86e6904f851b85e",
    ),
    (
        "0037",
        "public.idis_task6_validate_result_outputs()",
        "PRESERVE_PRE_0038_OWNER",
        ("search_path=pg_catalog, pg_temp",),
        (),
        (
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
        ),
        "b55d3fb0981fc7a8e5a282cec1c672975e5ac5697c55aa7a8b129b97030ad50e",
    ),
)
_ERASURE_EXECUTION_REQUIRED_RED_CASES = (
    (
        "tests/slice100_task7/test_erasure_execution_registry_red.py::"
        "test_current_body_is_current48_only_and_future_body_is_exact_runner86"
    ),
    (
        "tests/slice100_task7/test_erasure_execution_registry_red.py::"
        "test_erasure_execution_authority_boundary_does_not_claim_unprovable_audit_binding"
    ),
    (
        "tests/slice100_task7/test_erasure_execution_registry_red.py::"
        "test_current_and_future_acl_are_exact_and_future_acl_is_not_migration0038_runtime_sql"
    ),
)
_APPROVED_ERASURE_LEGACY_DELTA = (
    "break_glass_grants",
    "claims",
    "deal_assignments",
    "deals",
    "defects",
    "evidence_items",
    "human_gate_actions",
    "human_gates",
    "methodology_extraction_attempts",
    "methodology_extraction_task_spans",
    "overrides",
    "run_methodology_selections",
    "sanads",
    "strict_preflight_proofs",
)
_ERASURE_INDIRECT_DELETE_PARENT = {
    "calc_sanads": ("deterministic_calculations", "calc_id"),
    "run_steps": ("runs", "run_id"),
    "human_gate_actions": ("human_gates", "gate_id"),
}
_FORBIDDEN_SESSION_PSEUDO_VALUES = frozenset(
    {
        "current_catalog",
        "current_date",
        "current_role",
        "current_schema",
        "current_time",
        "current_timestamp",
        "localtime",
        "localtimestamp",
        "system_user",
        "user",
    }
)
_APPROVED_FUNCTION_GRANTEES_BY_AUTHORITY: dict[str, frozenset[str]] = {
    _APPROVED_ERASURE_EXECUTION_AUTHORITY: frozenset({_ERASURE_EXECUTOR}),
}
_UNSUPPORTED_SQL_KEYWORDS = {
    "ALTER",
    "ANALYZE",
    "CALL",
    "CHECKPOINT",
    "CLUSTER",
    "COMMENT",
    "COPY",
    "CREATE",
    "DISCARD",
    "DO",
    "DROP",
    "IMPORT",
    "LISTEN",
    "LOAD",
    "LOCK",
    "MERGE",
    "NOTIFY",
    "REASSIGN",
    "REFRESH",
    "REINDEX",
    "SECURITY",
    "TABLE",
    "TRUNCATE",
    "UNLISTEN",
    "VACUUM",
}
_DIRECT_SQL_STATEMENT_LEADERS = {"DELETE", "INSERT", "SELECT", "UPDATE", "WITH"}
_SQL_IDENTIFIER_TOKEN = r'"?[a-z_][a-z0-9_]*"?'
_SQL_RELATION_TOKEN = rf"{_SQL_IDENTIFIER_TOKEN}(?:\s*\.\s*{_SQL_IDENTIFIER_TOKEN})*"
_SQL_READ_RELATION_RE = re.compile(
    rf'(?<!")\b(?P<clause>FROM|JOIN|USING)\b(?!")\s+(?:LATERAL\s+)?(?:ONLY\s+)?'
    rf"(?P<relation>{_SQL_RELATION_TOKEN})",
    flags=re.IGNORECASE,
)
_SQL_WRITE_RELATION_RE = re.compile(
    r'(?<!")\b(?:DELETE\s+FROM|INSERT\s+INTO|(?<!ON\s)UPDATE)\b(?!")\s+'
    rf"(?:ONLY\s+)?(?P<relation>{_SQL_RELATION_TOKEN})",
    flags=re.IGNORECASE,
)
_SQL_CTE_RE = re.compile(
    rf"(?:\bWITH\b|,)\s+(?P<name>{_SQL_IDENTIFIER_TOKEN})"
    rf"(?:\s*\(\s*{_SQL_IDENTIFIER_TOKEN}(?:\s*,\s*{_SQL_IDENTIFIER_TOKEN})*\s*\))?"
    r"\s+AS\s+"
    r"(?:NOT\s+MATERIALIZED\s+|MATERIALIZED\s+)?\(",
    flags=re.IGNORECASE,
)
_SQL_NON_FUNCTION_PARENTHESIS_KEYWORDS = frozenset(
    {
        "and",
        "as",
        "check",
        "if",
        "in",
        "not",
        "on",
        "or",
        "row",
        "values",
        "when",
        "where",
    }
)
_SQL_CALL_IDENTIFIER_TOKEN = r'(?:U&)?(?:"[a-z_][a-z0-9_]*"|[a-z_][a-z0-9_]*)'
_SQL_QUALIFIED_FUNCTION_CALL_RE = re.compile(
    rf'(?<![a-z0-9_$"])(?P<schema>{_SQL_CALL_IDENTIFIER_TOKEN})\s*\.\s*'
    rf"(?P<function>{_SQL_CALL_IDENTIFIER_TOKEN})\s*\(",
    flags=re.IGNORECASE,
)
_SQL_UNQUALIFIED_FUNCTION_CALL_RE = re.compile(
    rf"(?<![a-z0-9_$.\"])(?P<function>{_SQL_CALL_IDENTIFIER_TOKEN})\s*\(",
    flags=re.IGNORECASE,
)
_CREATE_FUNCTION_PREFIX_RE = re.compile(
    r"^CREATE\s+FUNCTION\s+"
    r'(?P<schema>"?[a-z_][a-z0-9_]*"?)\.(?P<name>"?[a-z_][a-z0-9_]*"?)\s*'
    r"\((?P<arguments>[^)]*)\)\s+RETURNS\s+trigger\s+LANGUAGE\s+plpgsql\s+"
    r"SECURITY\s+DEFINER\s+SET\s+search_path\s*=\s*pg_catalog\s*,\s*pg_temp\s+AS\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_CREATE_ERASURE_RUNNER_PREFIX_RE = re.compile(
    r"^CREATE\s+FUNCTION\s+"
    r'(?P<schema>"?[a-z_][a-z0-9_]*"?)\.(?P<name>"?[a-z_][a-z0-9_]*"?)\s*'
    r"\(\s*uuid\s*,\s*uuid\s*,\s*uuid\s*\)\s+RETURNS\s+jsonb\s+"
    r"LANGUAGE\s+plpgsql\s+SECURITY\s+DEFINER\s+"
    r"SET\s+search_path\s*=\s*pg_catalog\s*,\s*pg_temp\s+AS\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_CREATE_TRIGGER_RE = re.compile(
    r"^CREATE\s+(?P<constraint>CONSTRAINT\s+)?TRIGGER\s+"
    r'(?P<trigger>"?[a-z_][a-z0-9_]*"?)\s+'
    r"(?P<timing>BEFORE|AFTER)\s+"
    r"(?P<events>(?:INSERT|UPDATE|DELETE)(?:\s+OR\s+(?:INSERT|UPDATE|DELETE))*)\s+"
    r'ON\s+(?P<table>"?public"?\."?[a-z_][a-z0-9_]*"?)\s+'
    r"(?P<deferrable>DEFERRABLE\s+INITIALLY\s+DEFERRED\s+)?"
    r"FOR\s+EACH\s+ROW\s+EXECUTE\s+FUNCTION\s+"
    r'(?P<function>"?public"?\."?[a-z_][a-z0-9_]*"?)\s*'
    r"\((?P<arguments>[^)]*)\)\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_FUNCTION_ACL_RE = re.compile(
    r"^(?P<verb>REVOKE\s+ALL|GRANT\s+EXECUTE)\s+ON\s+FUNCTION\s+"
    r'(?P<function>(?:"?[a-z_][a-z0-9_]*"?\.)?"?[a-z_][a-z0-9_]*"?)\s*'
    r"\((?P<arguments>[^)]*)\)\s+(?P<direction>FROM|TO)\s+"
    r'(?P<principal>"?[a-z_][a-z0-9_-]*"?)\s*$',
    flags=re.IGNORECASE | re.DOTALL,
)
_FUNCTION_OWNER_RE = re.compile(
    r"^ALTER\s+FUNCTION\s+"
    r'(?P<function>(?:"?[a-z_][a-z0-9_]*"?\.)?"?[a-z_][a-z0-9_]*"?)\s*'
    r"\((?P<arguments>[^)]*)\)\s+OWNER\s+TO\s+"
    r'(?P<principal>"?[a-z_][a-z0-9_-]*"?)\s*$',
    flags=re.IGNORECASE | re.DOTALL,
)


class ContractArtifactError(ValueError):
    """A stable fail-closed materialization error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def _sql_error(code: str, detail: str) -> None:
    raise ContractArtifactError(code, detail)


def _unquote_sql_identifier(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if re.fullmatch(r"[a-z_][a-z0-9_-]*", value, flags=re.IGNORECASE) is None:
        _sql_error("TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID", value)
    return value.lower()


def _normalize_scanned_sql_identifier(value: str) -> str:
    """Normalize a callable identifier after the lexer has exposed its spelling."""

    if value[:2].lower() == "u&":
        value = value[2:]
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if re.fullmatch(r"[a-z_][a-z0-9_]*", value, flags=re.IGNORECASE) is None:
        _sql_error("TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID", value)
    return value.lower()


def _qualified_public_name(value: str, *, label: str) -> tuple[str, str]:
    parts = value.split(".")
    if len(parts) != 2:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            f"{label} must be explicitly public-qualified: {value!r}",
        )
    schema, name = (_unquote_sql_identifier(part) for part in parts)
    if schema != "public":
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            f"{label} schema must be public: {schema!r}",
        )
    return schema, name


def _mask_sql_code(
    sql: str,
    *,
    reject_semicolons: bool,
    allow_dollar_blocks: bool = True,
) -> tuple[str, tuple[tuple[str, str, str], ...]]:
    """Mask literals while retaining executable tokens and extracted dollar bodies."""

    masked = list(sql)
    dollar_blocks: list[tuple[str, str, str]] = []
    index = 0
    while index < len(sql):
        character = sql[index]
        prefixed_string = (
            character in "eEbBxXnN" and index + 1 < len(sql) and sql[index + 1] == "'"
        ) or (
            sql[index : index + 2].lower() == "u&"
            and index + 2 < len(sql)
            and sql[index + 2] == "'"
        )
        if prefixed_string:
            _sql_error(
                "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                "prefixed SQL string literals are outside the normalized authority grammar",
            )
        if character == "'":
            start = index
            index += 1
            while index < len(sql):
                if sql[index] == "\\":
                    _sql_error(
                        "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                        "backslash string escapes are outside the normalized authority grammar",
                    )
                if sql[index] == "'":
                    if index + 1 < len(sql) and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                    "unterminated SQL string literal",
                )
            for offset in range(start, index):
                masked[offset] = " "
            continue
        if character == '"':
            start = index
            index += 1
            while index < len(sql):
                if sql[index] == '"':
                    if index + 1 < len(sql) and sql[index + 1] == '"':
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                    "unterminated quoted identifier",
                )
            identifier = sql[start + 1 : index - 1]
            if re.fullmatch(r"[a-z_][a-z0-9_]*", identifier) is None:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                    f"quoted identifier is not a simple canonical identifier: {identifier!r}",
                )
            continue
        if sql.startswith("--", index) or sql.startswith("/*", index):
            _sql_error(
                "TASK7_SQL_AUTHORITY_COMMENTS_FORBIDDEN",
                "SQL comments are not canonical authority bytes",
            )
        if character == "$" and allow_dollar_blocks:
            delimiter_match = re.match(r"\$(?:[a-z_][a-z0-9_]*)?\$", sql[index:], re.I)
            if delimiter_match is not None:
                delimiter = delimiter_match.group(0)
                body_start = index + len(delimiter)
                body_end = sql.find(delimiter, body_start)
                if body_end < 0:
                    _sql_error(
                        "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                        "unterminated dollar-quoted function body",
                    )
                body = sql[body_start:body_end]
                body_masked, nested = _mask_sql_code(
                    body,
                    reject_semicolons=False,
                    allow_dollar_blocks=False,
                )
                if nested:
                    _sql_error(
                        "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                        "nested dollar quoting is forbidden",
                    )
                block_end = body_end + len(delimiter)
                for offset in range(index, block_end):
                    masked[offset] = " "
                dollar_blocks.append((delimiter, body, body_masked))
                index = block_end
                continue
        if (
            character == "$"
            and not allow_dollar_blocks
            and re.match(r"\$(?:[a-z_][a-z0-9_]*)?\$", sql[index:], re.I) is not None
        ):
            _sql_error(
                "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                "nested dollar quoting is forbidden",
            )
        index += 1

    masked_text = "".join(masked)
    if reject_semicolons and ";" in masked_text:
        _sql_error(
            "TASK7_SQL_AUTHORITY_MULTIPLE_STATEMENTS",
            "each body_sql_utf8_hex_tuple item must be exactly one top-level statement",
        )
    return masked_text, tuple(dollar_blocks)


def _reject_unsupported_sql_forms(masked_sql: str) -> None:
    tokens = {token.upper() for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked_sql)}
    unsupported = sorted(tokens & _UNSUPPORTED_SQL_KEYWORDS)
    if unsupported:
        _sql_error(
            "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
            f"unsupported SQL form(s): {unsupported!r}",
        )


def _first_sql_keyword(masked_sql: str) -> str:
    match = re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked_sql)
    return match.group(0).upper() if match is not None else ""


def _is_non_relation_clause(masked_sql: str, match: re.Match[str]) -> bool:
    """Distinguish comparison/RAISE grammar from durable relation clauses."""

    clause = match.group("clause").upper()
    prefix = masked_sql[: match.start()]
    if clause == "FROM" and re.search(
        r"\bIS\s+(?:NOT\s+)?DISTINCT\s*$", prefix, flags=re.IGNORECASE
    ):
        return True
    return (
        clause == "USING"
        and re.search(
            r"\bRAISE\s+(?:EXCEPTION|WARNING|NOTICE|INFO|LOG|DEBUG)\b[^;]*$",
            prefix,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _validate_plpgsql_fragment(fragment: str) -> None:
    fragment = fragment.strip()
    if not fragment:
        return
    _reject_unsupported_sql_forms(fragment)
    leader = _first_sql_keyword(fragment)
    if leader == "BEGIN":
        _validate_plpgsql_fragment(re.sub(r"^\s*BEGIN\b", "", fragment, count=1, flags=re.I))
        return
    if leader in {"IF", "ELSIF"}:
        then = re.search(r"\bTHEN\b", fragment, flags=re.IGNORECASE)
        if then is None:
            _sql_error(
                "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
                f"PL/pgSQL {leader} has no explicit THEN",
            )
        _validate_plpgsql_fragment(fragment[then.end() :])
        return
    if leader in {"ELSE", "EXCEPTION"}:
        tail = re.sub(rf"^\s*{leader}\b", "", fragment, count=1, flags=re.I)
        if leader == "EXCEPTION":
            when = re.search(r"\bTHEN\b", tail, flags=re.IGNORECASE)
            if when is None:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
                    "PL/pgSQL EXCEPTION has no explicit THEN",
                )
            tail = tail[when.end() :]
        _validate_plpgsql_fragment(tail)
        return
    if leader == "FOR":
        query = re.match(
            rf"^\s*FOR\s+{_SQL_IDENTIFIER_TOKEN}\s+IN\s+(?:SELECT|WITH)\b",
            fragment,
            flags=re.IGNORECASE,
        )
        loop = (
            re.search(r"\bLOOP\b", fragment[query.end() :], flags=re.IGNORECASE) if query else None
        )
        if query is None or loop is None:
            _sql_error(
                "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
                "PL/pgSQL FOR admits only a literal SELECT/WITH query followed by LOOP",
            )
        tail_start = query.end() + loop.end()
        _validate_plpgsql_fragment(fragment[tail_start:])
        return
    if leader == "END":
        if re.fullmatch(r"\s*END(?:\s+(?:IF|LOOP))?\s*", fragment, flags=re.I) is None:
            _sql_error(
                "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
                f"unsupported END fragment: {fragment!r}",
            )
        return
    if leader == "RETURN":
        query = re.match(r"^\s*RETURN\s+QUERY\s+", fragment, flags=re.I)
        if query is not None:
            nested_leader = _first_sql_keyword(fragment[query.end() :])
            if nested_leader not in {"SELECT", "WITH"}:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
                    "RETURN QUERY must contain SELECT or WITH",
                )
        return
    if leader == "GET" and re.fullmatch(
        rf"\s*GET\s+DIAGNOSTICS\s+{_SQL_IDENTIFIER_TOKEN}\s*=\s*ROW_COUNT\s*",
        fragment,
        flags=re.IGNORECASE,
    ):
        return
    if leader == "SET" and re.fullmatch(
        r"\s*SET\s+CONSTRAINTS\s+ALL\s+(?:DEFERRED|IMMEDIATE)\s*",
        fragment,
        flags=re.IGNORECASE,
    ):
        return
    if leader in {"DELETE", "INSERT", "NULL", "PERFORM", "RAISE", "SELECT", "UPDATE", "WITH"}:
        return
    if re.match(
        rf"^\s*(?:NEW\s*\.\s*)?{_SQL_IDENTIFIER_TOKEN}\s*:=",
        fragment,
        flags=re.I,
    ):
        return
    _sql_error(
        "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
        f"unknown PL/pgSQL statement-leading form {leader!r}",
    )


def _validate_plpgsql_body_grammar(masked_body: str) -> None:
    fragments = masked_body.split(";")
    if not fragments or _first_sql_keyword(fragments[0]) not in {"BEGIN", "DECLARE"}:
        _sql_error(
            "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
            "helper body must start with BEGIN or DECLARE",
        )
    in_declarations = _first_sql_keyword(fragments[0]) == "DECLARE"
    for fragment in fragments:
        if in_declarations:
            begin = re.search(r"\bBEGIN\b", fragment, flags=re.IGNORECASE)
            if begin is None:
                declaration = re.sub(r"^\s*DECLARE\b", "", fragment, count=1, flags=re.I)
                if (
                    declaration.strip()
                    and re.match(
                        r'^\s*"?[a-z_][a-z0-9_]*"?\s+[a-z_][a-z0-9_]*(?:\[\])?',
                        declaration,
                        flags=re.I,
                    )
                    is None
                ):
                    _sql_error(
                        "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
                        f"unsupported declaration: {declaration!r}",
                    )
                continue
            in_declarations = False
            fragment = fragment[begin.start() :]
        _validate_plpgsql_fragment(fragment)
    if in_declarations:
        _sql_error(
            "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
            "DECLARE block has no BEGIN",
        )


def _sql_identifier_parts(value: str) -> tuple[str, ...]:
    return tuple(_unquote_sql_identifier(part.strip()) for part in re.split(r"\s*\.\s*", value))


def _validated_relation_name(
    relation_token: str,
    *,
    cte_names: set[str],
    followed_by_call: bool,
) -> str | None:
    parts = _sql_identifier_parts(relation_token)
    if followed_by_call:
        if len(parts) != 1 or parts[0] not in _APPROVED_RELATION_FUNCTIONS:
            _sql_error(
                "TASK7_SQL_AUTHORITY_UNDECLARED_RELATION_FUNCTION",
                f"{relation_token!r} is not an admitted immutable set-returning builtin",
            )
        return None
    if len(parts) == 1 and parts[0] in cte_names:
        return None
    if len(parts) != 2 or parts[0] != "public":
        _sql_error(
            "TASK7_SQL_AUTHORITY_RELATION_QUALIFICATION_INVALID",
            f"durable relation must be exactly public-qualified: {relation_token!r}",
        )
    return parts[1]


def _same_level_comma_relations(masked_sql: str) -> tuple[tuple[str, str], ...]:
    """Return comma-joined FROM/USING sources omitted by keyword-based matching."""

    found: list[tuple[str, str]] = []
    clause_re = re.compile(r'(?<!")\b(?P<clause>FROM|USING)\b(?!")', flags=re.IGNORECASE)
    relation_re = re.compile(
        rf"\s*(?:LATERAL\s+)?(?:ONLY\s+)?(?P<relation>{_SQL_RELATION_TOKEN})",
        flags=re.IGNORECASE,
    )
    stop_re = re.compile(
        r"\b(?:WHERE|GROUP|ORDER|LIMIT|RETURNING|UNION|EXCEPT|INTERSECT)\b",
        flags=re.IGNORECASE,
    )
    for clause in clause_re.finditer(masked_sql):
        if _is_non_relation_clause(masked_sql, clause):
            continue
        base_depth = masked_sql[: clause.end()].count("(") - masked_sql[: clause.end()].count(")")
        depth = base_depth
        index = clause.end()
        in_identifier_quote = False
        while index < len(masked_sql):
            character = masked_sql[index]
            if character == '"':
                in_identifier_quote = not in_identifier_quote
                index += 1
                continue
            if in_identifier_quote:
                index += 1
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth < base_depth:
                    break
            elif character == ";" and depth == base_depth:
                break
            elif depth == base_depth:
                stop = stop_re.match(masked_sql, index)
                if stop is not None:
                    break
                if character == ",":
                    relation = relation_re.match(masked_sql, index + 1)
                    if relation is None:
                        _sql_error(
                            "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                            "comma-joined relation source is not canonical",
                        )
                    token = relation.group("relation")
                    found.append((token, masked_sql[relation.end() :].lstrip()))
                    index = relation.end()
                    continue
            index += 1
    return tuple(found)


def _scan_code_relation_accesses(
    masked_sql: str, *, plpgsql_body: bool = False, expression: bool = False
) -> tuple[set[str], set[str]]:
    _reject_unsupported_sql_forms(masked_sql)
    if plpgsql_body:
        _validate_plpgsql_body_grammar(masked_sql)
    elif not expression and _first_sql_keyword(masked_sql) not in _DIRECT_SQL_STATEMENT_LEADERS:
        _sql_error(
            "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
            f"unknown top-level statement-leading form {_first_sql_keyword(masked_sql)!r}",
        )
    cte_names = {
        _unquote_sql_identifier(match.group("name")) for match in _SQL_CTE_RE.finditer(masked_sql)
    }
    masked_reads = list(masked_sql)
    writes: set[str] = set()
    for match in _SQL_WRITE_RELATION_RE.finditer(masked_sql):
        relation_name = _validated_relation_name(
            match.group("relation"), cte_names=cte_names, followed_by_call=False
        )
        if relation_name is None:
            _sql_error(
                "TASK7_SQL_AUTHORITY_RELATION_QUALIFICATION_INVALID",
                "a writable target cannot be a CTE",
            )
        writes.add(relation_name)
        for offset in range(match.start(), match.end()):
            masked_reads[offset] = " "
    read_source = "".join(masked_reads)
    reads: set[str] = set()
    for match in _SQL_READ_RELATION_RE.finditer(read_source):
        if _is_non_relation_clause(read_source, match):
            continue
        relation_token = match.group("relation")
        following = read_source[match.end() :].lstrip()
        relation_name = _validated_relation_name(
            relation_token,
            cte_names=cte_names,
            followed_by_call=following.startswith("("),
        )
        if relation_name is not None:
            reads.add(relation_name)
    for relation_token, following in _same_level_comma_relations(read_source):
        relation_name = _validated_relation_name(
            relation_token,
            cte_names=cte_names,
            followed_by_call=following.startswith("("),
        )
        if relation_name is not None:
            reads.add(relation_name)
    return reads, writes


def _scan_code_write_relation_sequence(masked_sql: str) -> tuple[str, ...]:
    """Return durable write targets in literal occurrence order, preserving duplicates."""

    _reject_unsupported_sql_forms(masked_sql)
    cte_names = {
        _unquote_sql_identifier(match.group("name")) for match in _SQL_CTE_RE.finditer(masked_sql)
    }
    result: list[str] = []
    for match in _SQL_WRITE_RELATION_RE.finditer(masked_sql):
        relation_name = _validated_relation_name(
            match.group("relation"), cte_names=cte_names, followed_by_call=False
        )
        if relation_name is None:
            _sql_error(
                "TASK7_SQL_AUTHORITY_RELATION_QUALIFICATION_INVALID",
                "a writable target cannot be a CTE",
            )
        result.append(relation_name)
    return tuple(result)


_ERASURE_DELETE_FRAGMENT_RE = re.compile(
    r"\bDELETE\s+FROM\s+public\.(?P<table>[a-z_][a-z0-9_]*)\b[^;]*;",
    flags=re.IGNORECASE | re.DOTALL,
)


def _validate_erasure_runner_delete_scope(masked_body: str) -> None:
    """Admit only the reviewed tenant/deal predicate shape for every runner DELETE."""

    for match in _ERASURE_DELETE_FRAGMENT_RE.finditer(masked_body):
        table = match.group("table").lower()
        escaped_table = re.escape(table)
        indirect = _ERASURE_INDIRECT_DELETE_PARENT.get(table)
        if indirect is None:
            expected = (
                rf"DELETE\s+FROM\s+public\.{escaped_table}\s+"
                r"WHERE\s+tenant_id\s*=\s*\$2\s+AND\s+deal_id\s*=\s*\$3\s*;"
            )
        else:
            parent, join_key = indirect
            escaped_parent = re.escape(parent)
            escaped_join_key = re.escape(join_key)
            expected = (
                rf"DELETE\s+FROM\s+public\.{escaped_table}\s+"
                rf"WHERE\s+{escaped_table}\.tenant_id\s*=\s*\$2\s+"
                rf"AND\s+{escaped_table}\.{escaped_join_key}\s+IN\s*\(\s*"
                rf"SELECT\s+{escaped_parent}\.{escaped_join_key}\s+"
                rf"FROM\s+public\.{escaped_parent}\s+"
                rf"WHERE\s+{escaped_parent}\.tenant_id\s*=\s*\$2\s+"
                rf"AND\s+{escaped_parent}\.deal_id\s*=\s*\$3\s*\)\s*;"
            )
        if re.fullmatch(expected, match.group(0), flags=re.IGNORECASE | re.DOTALL) is None:
            _sql_error(
                "TASK7_SQL_AUTHORITY_ERASURE_SCOPE_INVALID",
                f"erasure DELETE for {table!r} must use the exact tenant/deal predicate",
            )


def _erasure_runner_write_sequence(statements: tuple[str, ...]) -> tuple[str, ...]:
    matches: list[tuple[str, ...]] = []
    for statement in statements:
        parsed = _parse_create_function(statement)
        if parsed is None or parsed[0] != _ERASURE_RUNNER_IDENTITY:
            continue
        body_masked = _mask_sql_code(parsed[1], reject_semicolons=False, allow_dollar_blocks=False)[
            0
        ]
        _validate_erasure_runner_delete_scope(body_masked)
        matches.append(_scan_code_write_relation_sequence(body_masked))
    if len(matches) != 1:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "the reviewed authority must contain exactly one fixed erasure runner",
        )
    return matches[0]


def _looks_like_erasure_runner_declaration(masked_statement: str) -> bool:
    return (
        re.search(r"\bidis_task7_execute_approved_erasure\b", masked_statement, re.I) is not None
        or re.search(r"\bRETURNS\s+jsonb\b", masked_statement, re.I) is not None
    )


def _parse_create_function(
    statement: str,
) -> tuple[tuple[str, str, tuple[str, ...]], str] | None:
    masked, dollar_blocks = _mask_sql_code(statement, reject_semicolons=True)
    if re.match(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b", masked, re.I) is None:
        return None
    erasure_runner_declaration = _looks_like_erasure_runner_declaration(masked)
    binding_error = (
        "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID"
        if erasure_runner_declaration
        else "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID"
    )
    if re.match(r"^\s*CREATE\s+OR\s+REPLACE\s+FUNCTION\b", masked, re.I) is not None:
        _sql_error(
            binding_error,
            "CREATE OR REPLACE could retain unreviewed helper ownership or ACL state",
        )
    if len(dollar_blocks) != 1:
        _sql_error(
            binding_error,
            "a reviewed function requires exactly one dollar-quoted body",
        )
    delimiter, body, _ = dollar_blocks[0]
    start = statement.find(delimiter)
    end = statement.rfind(delimiter)
    if start < 0 or end <= start or statement[end + len(delimiter) :].strip():
        _sql_error(
            binding_error,
            "function body must be the final CREATE FUNCTION clause",
        )
    prefix = statement[:start]
    if erasure_runner_declaration:
        match = _CREATE_ERASURE_RUNNER_PREFIX_RE.fullmatch(prefix)
        if match is None:
            _sql_error(
                "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
                "erasure runner must use its exact reviewed name, uuid signature, jsonb return, "
                "LANGUAGE plpgsql, SECURITY DEFINER, and search_path",
            )
        schema, name = _qualified_public_name(
            f"{match.group('schema')}.{match.group('name')}", label="erasure runner"
        )
        identity = (schema, name, ("uuid", "uuid", "uuid"))
        if identity != _ERASURE_RUNNER_IDENTITY:
            _sql_error(
                "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
                f"unreviewed erasure runner identity {identity!r}",
            )
        return identity, body

    match = _CREATE_FUNCTION_PREFIX_RE.fullmatch(prefix)
    if match is None:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "helper must use public schema, RETURNS trigger, LANGUAGE plpgsql, SECURITY "
            "DEFINER, and exact search_path=pg_catalog,pg_temp",
        )
    schema, name = _qualified_public_name(
        f"{match.group('schema')}.{match.group('name')}", label="created helper"
    )
    arguments = match.group("arguments").strip()
    if arguments:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "trigger helper signatures must be exactly empty",
        )
    return (schema, name, ()), body


def _parse_trigger_binding(
    statement: str,
) -> (
    tuple[
        tuple[str, str],
        tuple[str, str, tuple[str, ...]],
        tuple[bool, str, tuple[str, ...], bool],
    ]
    | None
):
    masked, dollar_blocks = _mask_sql_code(statement, reject_semicolons=True)
    if re.match(r"^\s*CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\b", masked, re.I) is None:
        return None
    if dollar_blocks:
        _sql_error(
            "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
            "CREATE TRIGGER cannot contain a dollar-quoted body",
        )
    match = _CREATE_TRIGGER_RE.fullmatch(statement)
    if match is None:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "CREATE TRIGGER must bind an exact public helper signature",
        )
    _, table = _qualified_public_name(match.group("table"), label="trigger relation")
    function_schema, function_name = _qualified_public_name(
        match.group("function"), label="trigger helper"
    )
    if match.group("arguments").strip():
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "trigger EXECUTE FUNCTION signature must be exactly empty",
        )
    trigger = _unquote_sql_identifier(match.group("trigger"))
    events = tuple(
        part.upper() for part in re.split(r"\s+OR\s+", match.group("events"), flags=re.I)
    )
    event_order = ("INSERT", "UPDATE", "DELETE")
    if len(events) != len(set(events)) or events != tuple(
        event for event in event_order if event in events
    ):
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            f"trigger events are not a unique canonical subset: {events!r}",
        )
    shape = (
        match.group("constraint") is not None,
        match.group("timing").upper(),
        events,
        match.group("deferrable") is not None,
    )
    constraint, timing, _, deferrable = shape
    if constraint and (timing != "AFTER" or not deferrable):
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "constraint triggers must be AFTER and DEFERRABLE INITIALLY DEFERRED",
        )
    if not constraint and (timing != "BEFORE" or deferrable):
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "ordinary guard triggers must be non-deferrable BEFORE ROW triggers",
        )
    return (table, trigger), (function_schema, function_name, ()), shape


def _validate_trigger_kind_shape(
    authority_kind: str,
    shape: tuple[bool, str, tuple[str, ...], bool],
) -> None:
    constraint, timing, events, deferrable = shape
    if authority_kind == "CONSTRAINT_TRIGGER":
        valid = constraint and timing == "AFTER" and deferrable and bool(events)
    elif authority_kind == "BEFORE_ROW_TRIGGER":
        valid = (
            not constraint
            and timing == "BEFORE"
            and events == ("INSERT", "UPDATE")
            and not deferrable
        )
    elif authority_kind == "APPEND_ONLY_TRIGGER":
        valid = (
            not constraint
            and timing == "BEFORE"
            and events == ("INSERT", "UPDATE", "DELETE")
            and not deferrable
        )
    else:
        valid = False
    if not valid:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            f"trigger shape {shape!r} does not match {authority_kind}",
        )


def _parse_function_acl(
    statement: str,
) -> tuple[str, tuple[str, str, tuple[str, ...]], str] | None:
    masked, blocks = _mask_sql_code(statement, reject_semicolons=True)
    if re.match(r"^\s*(?:REVOKE|GRANT)\b", masked, re.I) is None:
        return None
    if blocks:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "function ACL statement cannot contain a dollar body",
        )
    match = _FUNCTION_ACL_RE.fullmatch(statement)
    if match is None:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "function ACL must bind an exact public schema and signature",
        )
    schema, name = _qualified_public_name(match.group("function"), label="function ACL")
    arguments = match.group("arguments").strip()
    identity = (schema, name, ())
    if name == _ERASURE_RUNNER_IDENTITY[1]:
        normalized_arguments = tuple(part.strip().lower() for part in arguments.split(","))
        identity = (schema, name, normalized_arguments)
        if identity != _ERASURE_RUNNER_IDENTITY:
            _sql_error(
                "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
                f"unreviewed erasure runner ACL signature {identity!r}",
            )
    elif arguments:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "trigger helper ACL signature must be exactly empty",
        )
    verb = " ".join(match.group("verb").upper().split())
    direction = match.group("direction").upper()
    principal = _unquote_sql_identifier(match.group("principal"))
    if verb == "REVOKE ALL" and (direction != "FROM" or principal != "public"):
        _sql_error(
            (
                "TASK7_SQL_AUTHORITY_ERASURE_ACL_INVALID"
                if identity == _ERASURE_RUNNER_IDENTITY
                else "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID"
            ),
            "REVOKE must remove all helper privileges from PUBLIC",
        )
    if verb == "GRANT EXECUTE" and direction != "TO":
        _sql_error(
            (
                "TASK7_SQL_AUTHORITY_ERASURE_ACL_INVALID"
                if identity == _ERASURE_RUNNER_IDENTITY
                else "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID"
            ),
            "GRANT EXECUTE must use TO",
        )
    return verb, identity, principal


def _parse_function_owner(
    statement: str,
) -> tuple[tuple[str, str, tuple[str, ...]], str] | None:
    masked, blocks = _mask_sql_code(statement, reject_semicolons=True)
    if re.match(r"^\s*ALTER\s+FUNCTION\b", masked, re.I) is None:
        return None
    if blocks:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "function ownership statement cannot contain a dollar body",
        )
    match = _FUNCTION_OWNER_RE.fullmatch(statement)
    if match is None:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "function ownership must bind an exact public function signature",
        )
    schema, name = _qualified_public_name(match.group("function"), label="function owner")
    argument_text = match.group("arguments").strip()
    arguments = (
        tuple(part.strip().lower() for part in argument_text.split(",")) if argument_text else ()
    )
    identity = (schema, name, arguments)
    if name == _ERASURE_RUNNER_IDENTITY[1] and identity != _ERASURE_RUNNER_IDENTITY:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            f"unreviewed erasure runner ownership identity {identity!r}",
        )
    if name != _ERASURE_RUNNER_IDENTITY[1] and arguments:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "trigger helper ownership signature must be exactly empty",
        )
    return identity, _unquote_sql_identifier(match.group("principal"))


def _split_sql_call_arguments(sql: str, opening_parenthesis: int) -> tuple[str, ...]:
    arguments: list[str] = []
    start = opening_parenthesis + 1
    index = start
    depth = 1
    quote: str | None = None
    while index < len(sql):
        character = sql[index]
        if quote is not None:
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            index += 1
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                arguments.append(sql[start:index].strip())
                return tuple(arguments)
        elif character == "," and depth == 1:
            arguments.append(sql[start:index].strip())
            start = index + 1
        index += 1
    _sql_error(
        "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        "unterminated session-state function call",
    )
    raise AssertionError("unreachable")


def _validate_erasure_execution_control_contract(
    sql: str,
    masked_sql: str,
    *,
    authority_id: str | None,
) -> None:
    """Admit only the reviewed tenant lock and paired deferred-constraint fence."""

    lock_like = re.findall(
        r"\bpg_catalog\.(?:pg_(?:try_)?advisory[a-z0-9_]*|hashtext[a-z0-9_]*)\s*\(",
        masked_sql,
        flags=re.IGNORECASE,
    )
    fence_matches = tuple(re.finditer(r"\bSET\s+CONSTRAINTS\b", masked_sql, flags=re.IGNORECASE))
    if not lock_like and not fence_matches:
        return
    if authority_id != _APPROVED_ERASURE_EXECUTION_AUTHORITY:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_LOCK_INVALID",
            "tenant advisory locking and constraint fencing are execution-authority only",
        )

    compact = "".join(sql.split())
    canonical_lock = (
        "pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended("
        "'idis-task7-erasure-tenant-lock-v1:'||$2::text,0))"
    )
    deferred = re.findall(
        r"\bSET\s+CONSTRAINTS\s+ALL\s+DEFERRED\b",
        masked_sql,
        flags=re.IGNORECASE,
    )
    immediate = re.findall(
        r"\bSET\s+CONSTRAINTS\s+ALL\s+IMMEDIATE\b",
        masked_sql,
        flags=re.IGNORECASE,
    )
    if (
        compact.count(canonical_lock) != 1
        or len(lock_like) != 2
        or len(deferred) != 1
        or len(immediate) != 1
        or len(fence_matches) != 2
    ):
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_LOCK_INVALID",
            "execution control must contain one exact tenant lock and one deferred/immediate fence",
        )
    deferred_match = re.search(
        r"\bSET\s+CONSTRAINTS\s+ALL\s+DEFERRED\b", masked_sql, flags=re.IGNORECASE
    )
    immediate_match = re.search(
        r"\bSET\s+CONSTRAINTS\s+ALL\s+IMMEDIATE\b", masked_sql, flags=re.IGNORECASE
    )
    if (
        deferred_match is None
        or immediate_match is None
        or deferred_match.start() >= immediate_match.start()
    ):
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_FENCE_INVALID",
            "the deferred constraint fence must precede the immediate fence",
        )


def _validate_session_state_contract(
    sql: str,
    masked_sql: str,
    *,
    authority_id: str | None,
) -> None:
    tokens = {token.lower() for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked_sql)}
    forbidden_pseudo_values = tokens & _FORBIDDEN_SESSION_PSEUDO_VALUES
    if forbidden_pseudo_values:
        _sql_error(
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
            "time-dependent or unapproved session pseudo-values: "
            f"{sorted(forbidden_pseudo_values)!r}",
        )
    principal_pseudo_values = tokens & _APPROVED_ERASURE_PRINCIPAL_PSEUDO_VALUES
    if principal_pseudo_values and authority_id not in _APPROVED_ERASURE_SESSION_AUTHORITIES:
        _sql_error(
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
            f"principal pseudo-values are erasure-only: {sorted(principal_pseudo_values)!r}",
        )
    if principal_pseudo_values:
        reviewed_pair = re.compile(
            r"(?:\bcurrent_user\s*=\s*'idis_erasure_function_owner'\s+AND\s+"
            r"session_user\s*=\s*'idis_erasure_executor'|"
            r"\bsession_user\s*=\s*'idis_erasure_executor'\s+AND\s+"
            r"current_user\s*=\s*'idis_erasure_function_owner')(?![A-Za-z0-9_])",
            flags=re.IGNORECASE,
        )
        without_reviewed_pair = list(masked_sql)
        for match in reviewed_pair.finditer(sql):
            masked_fragment = masked_sql[match.start() : match.end()]
            if {
                token.lower()
                for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked_fragment)
            } >= _APPROVED_ERASURE_PRINCIPAL_PSEUDO_VALUES:
                for offset in range(match.start(), match.end()):
                    without_reviewed_pair[offset] = " "
        remaining = {
            token.lower()
            for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", "".join(without_reviewed_pair))
        } & _APPROVED_ERASURE_PRINCIPAL_PSEUDO_VALUES
        if remaining:
            _sql_error(
                "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
                "principal pseudo-values must occur only as the exact reviewed owner/executor pair",
            )

    for match in _SQL_QUALIFIED_FUNCTION_CALL_RE.finditer(masked_sql):
        schema = _normalize_scanned_sql_identifier(match.group("schema"))
        function_name = _normalize_scanned_sql_identifier(match.group("function"))
        if schema != "pg_catalog" or function_name not in _SESSION_STATE_FUNCTIONS:
            continue
        allowed_authority = (
            authority_id in _APPROVED_ERASURE_SESSION_AUTHORITIES
            if function_name == "current_setting"
            else authority_id == _APPROVED_ERASURE_EXECUTION_AUTHORITY
        )
        if not allowed_authority:
            _sql_error(
                "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
                f"{schema}.{function_name} is erasure-only",
            )
        if function_name not in {"current_setting", "set_config"}:
            _sql_error(
                "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
                f"{schema}.{function_name} is outside the reviewed erasure context contract",
            )
        arguments = _split_sql_call_arguments(sql, match.end() - 1)
        expected_arity = 2 if function_name == "current_setting" else 3
        if len(arguments) != expected_arity:
            _sql_error(
                "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
                f"{function_name} requires exactly {expected_arity} reviewed arguments",
            )
        setting = arguments[0]
        if setting not in {f"'{key}'" for key in _APPROVED_ERASURE_SETTING_KEYS}:
            _sql_error(
                "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
                f"unapproved erasure setting key: {setting!r}",
            )
        local_argument = arguments[-1]
        if local_argument.lower() != "true":
            _sql_error(
                "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
                f"{function_name} requires a literal true transaction-local flag",
            )
        if function_name == "current_setting":
            continue
        if setting == "'idis.tenant_id'":
            if re.fullmatch(r"\s*\$2\s*::\s*text\s*", arguments[1], re.IGNORECASE) is None:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
                    "tenant context must be exactly the runner's second UUID argument as text",
                )
        elif arguments[1] != "'true'":
            _sql_error(
                "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
                "erasure context must be the exact literal true marker",
            )


def _validate_code_function_calls(
    sql: str,
    masked_sql: str,
    *,
    authority_id: str | None,
    allowed_positional_arguments: frozenset[int] = frozenset(),
) -> None:
    normalized_identifier_sql = masked_sql
    if "$" in normalized_identifier_sql:
        allowed = {str(value) for value in allowed_positional_arguments}

        def replace_positional(match: re.Match[str]) -> str:
            if match.group(1) not in allowed:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                    "only the erasure runner's exact $1/$2/$3 positional arguments are admitted",
                )
            return " " * len(match.group(0))

        normalized_identifier_sql = re.sub(
            r"\$([A-Za-z0-9_]+)", replace_positional, normalized_identifier_sql
        )
    if "$" in normalized_identifier_sql or any(
        ord(character) > 127 for character in normalized_identifier_sql
    ):
        _sql_error(
            "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
            "executable identifiers must use the normalized ASCII identifier alphabet",
        )
    if re.search(r"\bEXECUTE\b", masked_sql, flags=re.IGNORECASE):
        _sql_error("TASK7_SQL_AUTHORITY_DYNAMIC_SQL_FORBIDDEN", "dynamic EXECUTE")
    qualified_matches = tuple(_SQL_QUALIFIED_FUNCTION_CALL_RE.finditer(masked_sql))
    qualified_calls = {
        (
            _normalize_scanned_sql_identifier(match.group("schema")),
            _normalize_scanned_sql_identifier(match.group("function")),
        )
        for match in qualified_matches
    }
    qualified_function_starts = {match.start("function") for match in qualified_matches}
    unsupported_schemas = {
        schema for schema, _ in qualified_calls if schema not in {"pg_catalog", "public"}
    }
    if unsupported_schemas:
        _sql_error(
            "TASK7_SQL_AUTHORITY_UNAPPROVED_FUNCTION_SCHEMA",
            repr(sorted(unsupported_schemas)),
        )
    public_calls = {name for schema, name in qualified_calls if schema == "public"}
    if public_calls:
        _sql_error("TASK7_SQL_AUTHORITY_UNBOUND_PUBLIC_FUNCTION", repr(sorted(public_calls)))
    pg_catalog_calls = {name for schema, name in qualified_calls if schema == "pg_catalog"}
    for match in qualified_matches:
        if (
            _normalize_scanned_sql_identifier(match.group("schema")) == "pg_catalog"
            and _normalize_scanned_sql_identifier(match.group("function")) == "sha256"
        ):
            arguments = _split_sql_call_arguments(sql, match.end() - 1)
            if len(arguments) != 1 or not arguments[0]:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_UNAPPROVED_PG_CATALOG_FUNCTION",
                    "pg_catalog.sha256 requires its exact single bytea argument shape",
                )
    cte_declaration_starts = {match.start("name") for match in _SQL_CTE_RE.finditer(masked_sql)}
    unqualified_calls: set[str] = set()
    for match in _SQL_UNQUALIFIED_FUNCTION_CALL_RE.finditer(masked_sql):
        if (
            match.start("function") in qualified_function_starts
            or match.start("function") in cte_declaration_starts
        ):
            continue
        function_token = match.group("function")
        function_name = _normalize_scanned_sql_identifier(function_token)
        if (
            re.fullmatch(r"[a-z_][a-z0-9_]*", function_token, flags=re.IGNORECASE)
            and function_name in _SQL_NON_FUNCTION_PARENTHESIS_KEYWORDS
        ):
            continue
        unqualified_calls.add(function_name)
    unqualified_session_calls = unqualified_calls & _SESSION_STATE_FUNCTIONS
    if unqualified_session_calls:
        _sql_error(
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
            "session-state functions must be explicitly pg_catalog-qualified: "
            f"{sorted(unqualified_session_calls)!r}",
        )
    _validate_session_state_contract(sql, masked_sql, authority_id=authority_id)
    _validate_erasure_execution_control_contract(
        sql,
        masked_sql,
        authority_id=authority_id,
    )
    unsupported_pg_catalog = (
        pg_catalog_calls
        - _APPROVED_SQL_FUNCTIONS
        - _APPROVED_PG_CATALOG_ONLY_FUNCTIONS
        - _SESSION_STATE_FUNCTIONS
    )
    if unsupported_pg_catalog:
        _sql_error(
            "TASK7_SQL_AUTHORITY_UNAPPROVED_PG_CATALOG_FUNCTION",
            repr(sorted(unsupported_pg_catalog)),
        )
    unsupported_unqualified = unqualified_calls - _APPROVED_SQL_FUNCTIONS - _SESSION_STATE_FUNCTIONS
    if unsupported_unqualified:
        _sql_error(
            "TASK7_SQL_AUTHORITY_UNBOUND_UNQUALIFIED_FUNCTION",
            repr(sorted(unsupported_unqualified)),
        )


def validate_sql_function_security(
    statements: tuple[str, ...], *, authority_id: str | None = None
) -> dict[str, set[tuple[str, str, tuple[str, ...]]]]:
    """Validate exact helper creation, trigger binding, ACL, calls, and session state."""

    created: set[tuple[str, str, tuple[str, ...]]] = set()
    triggered: set[tuple[str, str, tuple[str, ...]]] = set()
    revoked: set[tuple[str, str, tuple[str, ...]]] = set()
    granted: set[tuple[str, str, tuple[str, ...]]] = set()
    owners: dict[tuple[str, str, tuple[str, ...]], str] = {}
    seen_acl_entries: set[tuple[str, tuple[str, str, tuple[str, ...]], str]] = set()
    for statement in statements:
        if statement != statement.strip() or not statement or "\r" in statement:
            _sql_error(
                "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                "SQL must be non-empty, trimmed, and LF-only",
            )
        masked, blocks = _mask_sql_code(statement, reject_semicolons=True)
        parsed_function = _parse_create_function(statement)
        if parsed_function is not None:
            identity, _ = parsed_function
            if identity in created:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
                    f"duplicate helper definition {identity!r}",
                )
            created.add(identity)
            body_masked = _mask_sql_code(
                parsed_function[1], reject_semicolons=False, allow_dollar_blocks=False
            )[0]
            _validate_code_function_calls(
                parsed_function[1],
                body_masked,
                authority_id=authority_id,
                allowed_positional_arguments=(
                    _ERASURE_RUNNER_POSITIONAL_ARGUMENTS
                    if identity == _ERASURE_RUNNER_IDENTITY
                    else frozenset()
                ),
            )
            continue
        parsed_trigger = _parse_trigger_binding(statement)
        if parsed_trigger is not None:
            triggered.add(parsed_trigger[1])
            continue
        parsed_owner = _parse_function_owner(statement)
        if parsed_owner is not None:
            identity, principal = parsed_owner
            if identity in owners:
                _sql_error(
                    (
                        "TASK7_SQL_AUTHORITY_ERASURE_ACL_INVALID"
                        if identity == _ERASURE_RUNNER_IDENTITY
                        else "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID"
                    ),
                    f"duplicate function ownership statement {identity!r}",
                )
            owners[identity] = principal
            continue
        parsed_acl = _parse_function_acl(statement)
        if parsed_acl is not None:
            verb, identity, principal = parsed_acl
            if parsed_acl in seen_acl_entries:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
                    f"duplicate function ACL statement {parsed_acl!r}",
                )
            seen_acl_entries.add(parsed_acl)
            if verb == "GRANT EXECUTE":
                if identity != _ERASURE_RUNNER_IDENTITY:
                    _sql_error(
                        "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
                        "ordinary trigger helpers cannot receive an execution grant",
                    )
                allowed_grantees = _APPROVED_FUNCTION_GRANTEES_BY_AUTHORITY.get(
                    authority_id or "", frozenset()
                )
                if principal not in allowed_grantees:
                    _sql_error(
                        "TASK7_SQL_AUTHORITY_ERASURE_ACL_INVALID",
                        f"unreviewed erasure runner execution grantee {principal!r}",
                    )
            (revoked if verb == "REVOKE ALL" else granted).add(identity)
            continue
        if blocks:
            _sql_error(
                "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                "dollar quoting is allowed only in CREATE FUNCTION",
            )
        _validate_code_function_calls(statement, masked, authority_id=authority_id)

    runner_related = created | triggered | revoked | granted | set(owners)
    malformed_runner_identities = {
        identity
        for identity in runner_related
        if identity[1] == _ERASURE_RUNNER_IDENTITY[1] and identity != _ERASURE_RUNNER_IDENTITY
    }
    if malformed_runner_identities or _ERASURE_RUNNER_IDENTITY in triggered:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "the exact erasure runner cannot have another overload or a trigger binding",
        )
    runner_present = _ERASURE_RUNNER_IDENTITY in runner_related
    if runner_present and authority_id != _APPROVED_ERASURE_EXECUTION_AUTHORITY:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "the dedicated erasure runner is admitted only by its reviewed authority",
        )

    if triggered - created or revoked - created or granted - created or set(owners) - created:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "helper bindings or ACLs do not resolve to the exact created schema/signature",
        )
    helper_created = created - {_ERASURE_RUNNER_IDENTITY}
    if runner_present and helper_created:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "the execution registry cannot install trigger helpers",
        )
    if triggered != helper_created:
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "every trigger helper must be bound once by its exact trigger signature",
        )
    if runner_present:
        exact_acl = (
            _ERASURE_RUNNER_IDENTITY in created
            and revoked == created
            and granted == {_ERASURE_RUNNER_IDENTITY}
            and owners == {_ERASURE_RUNNER_IDENTITY: _ERASURE_FUNCTION_OWNER}
        )
        if not exact_acl:
            _sql_error(
                "TASK7_SQL_AUTHORITY_ERASURE_ACL_INVALID",
                "erasure runner requires its exact owner, PUBLIC revocation, and "
                "executor-only grant",
            )
    elif authority_id == _APPROVED_ERASURE_EXECUTION_AUTHORITY and created:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "the erasure function contract requires the exact dedicated runner",
        )
    elif (
        revoked != created
        or granted
        or (
            owners
            and not (
                authority_id == _APPROVED_ERASURE_GUARD_AUTHORITY
                and owners == dict.fromkeys(helper_created, _ERASURE_FUNCTION_OWNER)
            )
        )
    ):
        _sql_error(
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
            "every trigger helper must be revoked from PUBLIC and remain ungranted",
        )
    return {
        "created": created,
        "triggered": triggered,
        "revoked": revoked,
        "granted": granted,
    }


def scan_sql_relation_accesses(
    statements: tuple[str, ...], *, expressions: bool = False
) -> tuple[set[str], set[str]]:
    """Scan literal SQL without allowing comments, multi-statements, or unknown SQL forms."""

    reads: set[str] = set()
    writes: set[str] = set()
    for statement in statements:
        if statement != statement.strip() or not statement or "\r" in statement:
            _sql_error(
                "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                "SQL must be non-empty, trimmed, and LF-only",
            )
        masked, blocks = _mask_sql_code(statement, reject_semicolons=True)
        parsed_function = _parse_create_function(statement)
        if parsed_function is not None:
            body_masked = _mask_sql_code(
                parsed_function[1], reject_semicolons=False, allow_dollar_blocks=False
            )[0]
            statement_reads, statement_writes = _scan_code_relation_accesses(
                body_masked, plpgsql_body=True
            )
        elif (
            _parse_trigger_binding(statement) is not None
            or _parse_function_acl(statement) is not None
            or _parse_function_owner(statement) is not None
        ):
            statement_reads, statement_writes = set(), set()
        else:
            if blocks:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                    "dollar quoting is allowed only in CREATE FUNCTION",
                )
            statement_reads, statement_writes = _scan_code_relation_accesses(
                masked, expression=expressions
            )
        reads.update(statement_reads)
        writes.update(statement_writes)
    return reads, writes


def validate_sql_authority_statement_tuple(
    authority_id: str,
    authority_kind: str,
    statements: tuple[str, ...],
) -> dict[str, Any]:
    """Enforce the closed statement grammar for one normalized SQL authority row."""

    if not statements:
        _sql_error("TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID", "empty SQL tuple")
    allowed_kinds = {
        "CHECK",
        "CONSTRAINT_TRIGGER",
        "BEFORE_ROW_TRIGGER",
        "APPEND_ONLY_TRIGGER",
    }
    if authority_kind not in allowed_kinds:
        _sql_error("TASK7_SQL_AUTHORITY_SPEC_INVALID", f"unknown kind {authority_kind!r}")
    for statement in statements:
        masked, blocks = _mask_sql_code(statement, reject_semicolons=True)
        first = re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked)
        keyword = first.group(0).upper() if first is not None else ""
        if authority_kind == "CHECK":
            _reject_unsupported_sql_forms(masked)
            if blocks or keyword in {
                "ALTER",
                "CREATE",
                "DELETE",
                "DROP",
                "GRANT",
                "INSERT",
                "REVOKE",
                "SELECT",
                "UPDATE",
            }:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
                    "CHECK authorities contain expressions only",
                )
            _validate_code_function_calls(statement, masked, authority_id=authority_id)
            continue
        if (
            _parse_create_function(statement) is None
            and _parse_trigger_binding(statement) is None
            and _parse_function_acl(statement) is None
            and _parse_function_owner(statement) is None
        ):
            _sql_error(
                "TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT",
                f"{authority_kind} admits only reviewed function/trigger/ACL/owner statements",
            )
        parsed_trigger = _parse_trigger_binding(statement)
        if parsed_trigger is not None:
            _validate_trigger_kind_shape(authority_kind, parsed_trigger[2])
    function_sets = validate_sql_function_security(statements, authority_id=authority_id)
    reads, writes = scan_sql_relation_accesses(statements, expressions=authority_kind == "CHECK")
    parsed_triggers = tuple(
        parsed
        for statement in statements
        if (parsed := _parse_trigger_binding(statement)) is not None
    )
    triggers = {parsed[0] for parsed in parsed_triggers}
    trigger_events = {parsed[0]: parsed[2][2] for parsed in parsed_triggers}
    return {
        "reads": reads,
        "writes": writes,
        "triggers": triggers,
        "trigger_events": trigger_events,
        **function_sets,
    }


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID", f"duplicate JSON key {key!r}"
            )
        result[key] = value
    return result


def _reject_float(value: str) -> Any:
    raise ContractArtifactError(
        "TASK7_CONTRACT_ARTIFACT_INVALID", f"JSON float is forbidden: {value}"
    )


def _decode(raw: bytes) -> dict[str, Any]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ContractArtifactError("TASK7_CONTRACT_ARTIFACT_INVALID", "UTF-8 BOM")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "artifact is not UTF-8"
        ) from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=_reject_float,
        )
    except json.JSONDecodeError as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "artifact is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "artifact root must be an object"
        )
    return value


def _validate_scalar_tree(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        raise ContractArtifactError("TASK7_CONTRACT_ARTIFACT_INVALID", f"float at {path}")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID", f"non-NFC string at {path}"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_scalar_tree(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractArtifactError(
                    "TASK7_CONTRACT_ARTIFACT_INVALID", f"non-string key at {path}"
                )
            _validate_scalar_tree(key, f"{path}.<key>")
            _validate_scalar_tree(item, f"{path}.{key}")
        return
    raise ContractArtifactError(
        "TASK7_CONTRACT_ARTIFACT_INVALID",
        f"unsupported value type at {path}: {type(value).__name__}",
    )


def _canonical_bytes(value: Any) -> bytes:
    _validate_scalar_tree(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _authority_schema_sha256(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "schema_sha256"}
    return _sha256_bytes(_AUTHORITY_ROW_SCHEMA_HASH_DOMAIN + _canonical_bytes(payload))


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        raise ContractArtifactError(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _iter_named_values(
    value: Any,
    path: tuple[str | int, ...] = (),
) -> list[tuple[tuple[str | int, ...], str, Any]]:
    found: list[tuple[tuple[str | int, ...], str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            found.append((child_path, key, child))
            found.extend(_iter_named_values(child, child_path))
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            found.extend(_iter_named_values(child, (*path, ordinal)))
    return found


def _decode_reviewed_hex(value: Any, path: str) -> bytes:
    if not isinstance(value, str) or _LOWERCASE_HEX_PATTERN.fullmatch(value) is None:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            f"{path} is not canonical lowercase UTF-8 hex",
        )
    try:
        decoded = bytes.fromhex(value)
        text = decoded.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            f"{path} is not UTF-8 hex",
        ) from exc
    if decoded.hex() != value:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            f"{path} is not canonical lowercase hex",
        )
    for pattern in _SYMBOLIC_MACRO_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID",
                f"{path} contains symbolic macro {match.group(0)!r}",
            )
    return decoded


def _validate_reviewed_row_bytes(artifact: dict[str, Any]) -> None:
    named_paths: set[tuple[str | int, ...]] = set()
    for path, key, value in _iter_named_values(artifact):
        if not key.endswith("_utf8_hex"):
            continue
        named_paths.add(path)
        _decode_reviewed_hex(value, ".".join(str(part) for part in path))

    expected_named_paths: set[tuple[str | int, ...]] = {
        ("metadata", "reviewed_design_utf8_hex"),
        ("canonicalization_contract", "reviewed_literal_utf8_hex"),
        ("digest_recipe_registry", "reviewed_literal_utf8_hex"),
        *{
            ("current_ddl_registry", "rows", ordinal, "reviewed_authority_utf8_hex")
            for ordinal in range(34)
        },
        *{
            ("edge_rule_registry", "rows", ordinal, field)
            for ordinal in range(48)
            for field in ("producer_literal_utf8_hex", "consumer_literal_utf8_hex")
        },
    }
    if named_paths != expected_named_paths:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "named reviewed-byte census mismatch",
        )

    semantic_rows = artifact["semantic_step_registry"]["rows"]
    semantic_paths: set[tuple[str | int, ...]] = set()
    for ordinal, row in enumerate(semantic_rows):
        path = ("semantic_step_registry", "rows", ordinal, 2)
        semantic_paths.add(path)
        if not isinstance(row, list) or len(row) != 5:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID",
                f"semantic step {ordinal} does not have five positional fields",
            )
        _decode_reviewed_hex(row[2], ".".join(str(part) for part in path))
    expected_semantic_paths = {
        ("semantic_step_registry", "rows", ordinal, 2) for ordinal in range(28)
    }
    if semantic_paths != expected_semantic_paths or len(named_paths | semantic_paths) != 161:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "semantic reviewed-byte census mismatch",
        )

    for ordinal, row in enumerate(artifact["current_ddl_registry"]["rows"]):
        encoded = _decode_reviewed_hex(
            row["reviewed_authority_utf8_hex"],
            f"current_ddl_registry.rows.{ordinal}.reviewed_authority_utf8_hex",
        )
        payload = {key: value for key, value in row.items() if key != "reviewed_authority_utf8_hex"}
        if encoded != _canonical_bytes(payload):
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID",
                f"current DDL row {ordinal} authority bytes mismatch",
            )

    for section in (
        "future_native_authority_registry",
        "future_reference_authority_registry",
        "future_child_authority_registry",
    ):
        for ordinal, row in enumerate(artifact[section]["rows"]):
            expected = _authority_schema_sha256(row)
            if row.get("schema_sha256") != expected:
                raise ContractArtifactError(
                    "TASK7_CONTRACT_ARTIFACT_INVALID",
                    f"{section} row {ordinal} schema_sha256 mismatch",
                )


def _section_hashes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": _sha256_bytes(_canonical_bytes(artifact[name])),
        }
        for ordinal, name in enumerate(_SECTION_ORDER)
    ]


def _schema_components(artifact: dict[str, Any]) -> tuple[list[list[Any]], str]:
    derived_rows: list[list[Any]] = []
    for row in artifact["schema_registry"]["rows"]:
        schema_hash = _h(
            [
                "typed-schema-definition-v1",
                row["schema_id"],
                row["schema_version"],
                row["authority_locator"],
                row["normalized_schema_snapshot"],
                row["collection_paths"],
            ]
        )
        _expect(schema_hash, row["expected_schema_sha256"], row["schema_id"])
        derived_rows.append([row["schema_id"], row["schema_version"], schema_hash])
    registry_hash = _h(["task7-schema-registry-v1", derived_rows])
    _expect(
        registry_hash,
        artifact["schema_registry"]["expected_sha256"],
        "schema_registry_hash",
    )
    return derived_rows, registry_hash


def _semantic_components(artifact: dict[str, Any]) -> tuple[list[list[Any]], str]:
    derived_rows: list[list[Any]] = []
    for expected_order, row in enumerate(artifact["semantic_step_registry"]["rows"]):
        if row[0] != expected_order:
            raise ContractArtifactError("TASK7_CONTRACT_ARTIFACT_INVALID", "semantic ordinal gap")
        try:
            preimage = bytes.fromhex(row[2])
        except ValueError as exc:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID", "semantic preimage is not hex"
            ) from exc
        semantic_hash = _sha256_bytes(preimage)
        _expect(semantic_hash, row[3], f"semantic step {expected_order}")
        derived_rows.append([row[0], row[1], semantic_hash, row[4]])
    vector_hash = _h(["task7-semantic-step-vector-v2", derived_rows])
    _expect(
        vector_hash,
        artifact["semantic_step_registry"]["expected_vector_sha256"],
        "semantic_step_vector_hash",
    )
    return derived_rows, vector_hash


def _erasure_hash(section: dict[str, Any], label: str) -> str:
    rows = section["rows"]
    result = _h(["task7-erasure-schedule-v2", rows])
    _expect(result, section["expected_sha256"], label)
    return result


def _selector_components(
    artifact: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], str, str, str]:
    producer_rows: list[list[Any]] = []
    producer_rule_hashes: dict[str, str] = {}
    for row in artifact["producer_selector_registry"]["rows"]:
        instances: list[list[Any]] = []
        for instance in row["instances"]:
            instance_hash = _h(["producer-selector-v2", instance["selector_ast"]])
            _expect(instance_hash, instance["expected_sha256"], "producer selector")
            instances.append([instance["ordinal"], instance["parameter_tuple"], instance_hash])
        selector_hash = _h(["producer-selector-rule-v2", row["rule_id"], instances])
        _expect(selector_hash, row["expected_sha256"], row["rule_id"])
        producer_rule_hashes[row["rule_id"]] = selector_hash
        producer_rows.append([row["rule_id"], row["version"], selector_hash])
    producer_hash = _h(["task7-producer-selector-registry-v1", producer_rows])
    _expect(
        producer_hash,
        artifact["producer_selector_registry"]["expected_sha256"],
        "producer_selector_registry_hash",
    )

    consumer_rows: list[list[Any]] = []
    consumer_rule_hashes: dict[str, str] = {}
    for row in artifact["consumer_selector_registry"]["rows"]:
        selector_hash = _h(
            [
                "consumer-selector-v2",
                row["rule_ordinal"],
                row["rule_id"],
                row["consumer_coordinates"],
            ]
        )
        _expect(selector_hash, row["expected_sha256"], row["rule_id"])
        consumer_rule_hashes[row["rule_id"]] = selector_hash
        consumer_rows.append([row["rule_id"], row["version"], selector_hash])
    consumer_hash = _h(["task7-consumer-selector-registry-v1", consumer_rows])
    _expect(
        consumer_hash,
        artifact["consumer_selector_registry"]["expected_sha256"],
        "consumer_selector_registry_hash",
    )
    selector_hash = _h(["task7-selector-registry-v1", producer_hash, consumer_hash])
    _expect(
        selector_hash,
        artifact["producer_selector_registry"]["expected_combined_sha256"],
        "selector_registry_hash",
    )
    return (
        producer_rule_hashes,
        consumer_rule_hashes,
        producer_hash,
        consumer_hash,
        selector_hash,
    )


def _applicability_components(artifact: dict[str, Any]) -> tuple[dict[str, str], str]:
    hashes: dict[str, str] = {}
    rows: list[list[Any]] = []
    for row in artifact["applicability_registry"]["rows"]:
        predicate_hash = _h(row["hash_preimage"])
        _expect(predicate_hash, row["expected_sha256"], row["predicate_id"])
        hashes[row["predicate_id"]] = predicate_hash
        rows.append(
            [
                row["predicate_id"],
                row["version"],
                predicate_hash,
                row["evidence_schema_id"],
                row["evidence_schema_version"],
                row["evidence_schema_sha256"],
            ]
        )
    result = _h(["task7-applicability-registry-v1", rows])
    _expect(
        result,
        artifact["applicability_registry"]["expected_sha256"],
        "applicability_registry_hash",
    )
    return hashes, result


def _edge_components(
    artifact: dict[str, Any],
    producer_hashes: dict[str, str],
    consumer_hashes: dict[str, str],
    predicate_hashes: dict[str, str],
) -> str:
    rows: list[list[Any]] = []
    for row in artifact["edge_rule_registry"]["rows"]:
        rule_hash = _h(
            [
                "expected-edge-rule-digest-v1",
                row["rule_ordinal"],
                row["rule_id"],
                row["rule_version"],
                row["rule_id"],
                "2",
                producer_hashes[row["rule_id"]],
                row["rule_id"],
                "2",
                consumer_hashes[row["rule_id"]],
                row["usage_kind"],
                row["predicate_kind"],
                row["predicate_id"],
                row["predicate_version"],
                predicate_hashes[row["predicate_id"]],
            ]
        )
        _expect(rule_hash, row["expected_rule_sha256"], row["rule_id"])
        rows.append([row["rule_ordinal"], row["rule_id"], "1", rule_hash])
    result = _h(["expected-edge-manifest-v1", "full-strict-v4", rows])
    _expect(
        result,
        artifact["edge_rule_registry"]["expected_manifest_sha256"],
        "edge_manifest_hash",
    )
    return result


def _usage_components(artifact: dict[str, Any]) -> tuple[str, str]:
    routes = artifact["usage_route_registry"]["rows"]
    route_hash = _h(["usage-path-route-v2", routes])
    _expect(
        route_hash,
        artifact["usage_route_registry"]["expected_sha256"],
        "usage_path_route_hash",
    )

    registry_rows: list[list[Any]] = []
    for row in artifact["usage_extractor_registry"]["rows"]:
        extractor_hash = _h(
            [
                "usage-extractor-definition-v2",
                row["usage_kind"],
                row["extractor_id"],
                row["extractor_version"],
                row["program"],
                row["accepted_consumers"],
            ]
        )
        _expect(extractor_hash, row["expected_sha256"], row["usage_kind"])
        accepted_hash = _h(
            [
                "accepted-consumer-set-v2",
                row["usage_kind"],
                row["accepted_consumers"],
            ]
        )
        _expect(
            accepted_hash,
            row["expected_accepted_consumer_sha256"],
            f"accepted consumers {row['usage_kind']}",
        )
        registry_rows.append(
            [
                row["usage_kind"],
                row["extractor_id"],
                row["extractor_version"],
                extractor_hash,
                row["accepted_consumers"],
            ]
        )
    extractor_hash = _h(["task7-usage-extractor-registry-v2", registry_rows, route_hash])
    _expect(
        extractor_hash,
        artifact["usage_extractor_registry"]["expected_sha256"],
        "usage_extractor_registry_hash",
    )
    return route_hash, extractor_hash


def _generic_registry_hash(
    artifact: dict[str, Any], section: str, tag: str, expected_key: str = "expected_sha256"
) -> str:
    value = _h([tag, artifact[section]["rows"]])
    _expect(value, artifact[section][expected_key], f"{section}_hash")
    return value


def _rights_index_policy_registry_hash(artifact: dict[str, Any]) -> str:
    """Validate and hash the closed provider-rights indexing policy registry."""

    provider_section = artifact.get("provider_registry")
    rights_section = artifact.get("rights_index_policy_registry")
    if not isinstance(provider_section, dict) or tuple(provider_section) != (
        "rows",
        "expected_sha256",
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "provider registry shape mismatch",
        )
    if not isinstance(rights_section, dict) or tuple(rights_section) != (
        "rows",
        "expected_sha256",
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "rights-index policy registry shape mismatch",
        )

    provider_rows = provider_section["rows"]
    if (
        not isinstance(provider_rows, list)
        or len(provider_rows) != len(_PROVIDER_IDS)
        or any(not isinstance(row, list) or len(row) != 3 for row in provider_rows)
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "provider registry does not contain 18 canonical rows",
        )
    provider_ids = tuple(row[0] for row in provider_rows)
    if provider_ids != _PROVIDER_IDS:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "provider registry order differs from the reviewed 18-provider order",
        )

    rows = rights_section["rows"]
    expected_rows = [list(row) for row in _RIGHTS_INDEX_POLICY_ROWS]
    if rows != expected_rows:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "rights-index policy rows differ from the reviewed 18-provider policy",
        )
    if tuple(row[1] for row in rows) != provider_ids:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "rights-index policy provider order differs from provider_registry",
        )

    value = _h(["external-context-rights-index-policy-v1", rows])
    _expect(
        value,
        rights_section["expected_sha256"],
        "rights_index_policy_registry_hash",
    )
    return value


def _reviewed_erasure_write_sequence(artifact: dict[str, Any]) -> tuple[str, ...]:
    section = artifact.get("erasure_materialized")
    if not isinstance(section, dict):
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "erasure_materialized is required to bind the runner write closure",
        )
    rows = section.get("rows")
    phase_count = section.get("phase_count")
    physical_table_count = section.get("physical_table_count")
    if (
        not isinstance(rows, list)
        or not isinstance(phase_count, int)
        or not isinstance(physical_table_count, int)
        or len(rows) != phase_count
    ):
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "erasure schedule census is malformed",
        )
    tables: list[str] = []
    phases: set[int] = set()
    for execution_ordinal, row in enumerate(rows):
        if (
            not isinstance(row, list)
            or len(row) != 3
            or row[0] != execution_ordinal
            or not isinstance(row[1], int)
            or row[1] in phases
            or not isinstance(row[2], list)
            or not row[2]
            or any(not isinstance(table, str) or not table for table in row[2])
        ):
            _sql_error(
                "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
                f"erasure schedule row {execution_ordinal} is malformed",
            )
        phases.add(row[1])
        tables.extend(row[2])
    if len(tables) != physical_table_count or len(tables) != len(set(tables)):
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "erasure schedule physical-table census is not unique and complete",
        )
    return tuple(tables)


def _validate_erasure_runner_superset(reviewed_writes: tuple[str, ...]) -> None:
    approved = _APPROVED_ERASURE_RUNNER_SEQUENCE
    approved_set = frozenset(approved)
    reviewed_set = frozenset(reviewed_writes)
    if len(approved) != 86 or len(approved_set) != 86:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "the approved erasure runner relation tuple must contain 86 unique relations",
        )
    if not reviewed_set < approved_set:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "the reviewed 72-relation census must be a strict subset of the runner",
        )
    legacy_delta = tuple(sorted(approved_set - reviewed_set))
    if legacy_delta != _APPROVED_ERASURE_LEGACY_DELTA:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            "the runner/reviewed delta must equal the exact 14-relation legacy tuple",
        )


_ERASURE_READ_PROJECTION_COLUMNS = {
    "erasure_requests": ("request_id", "tenant_id", "deal_id", "status"),
    "deterministic_calculations": ("tenant_id", "deal_id", "calc_id"),
    "runs": ("tenant_id", "deal_id", "run_id"),
    "human_gates": ("tenant_id", "deal_id", "gate_id"),
    "legal_holds": ("tenant_id", "target_type", "target_id", "lifted_at"),
    "document_artifacts": ("tenant_id", "deal_id", "doc_id", "uri"),
}


def _expected_erasure_delete_statement(table: str) -> str:
    indirect = _ERASURE_INDIRECT_DELETE_PARENT.get(table)
    if indirect is None:
        return f"DELETE FROM public.{table} WHERE tenant_id=$2 AND deal_id=$3;"
    parent, join_key = indirect
    return (
        f"DELETE FROM public.{table} WHERE {table}.tenant_id=$2 AND {table}.{join_key} IN "
        f"(SELECT {parent}.{join_key} FROM public.{parent} WHERE "
        f"{parent}.tenant_id=$2 AND {parent}.deal_id=$3);"
    )


def _expected_erasure_runner_statement(relations: tuple[str, ...]) -> str:
    deletion_fragments: list[str] = []
    for relation in relations:
        deletion_fragments.extend(
            (
                _expected_erasure_delete_statement(relation),
                "GET DIAGNOSTICS deleted_count=ROW_COUNT;",
                "deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object("
                f"'{relation}',deleted_count);",
            )
        )
    deletion_block = " ".join(deletion_fragments)
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
        "a.deal_id=$3 AND (a.uri IS NULL OR a.uri NOT LIKE 'idis://%' OR "
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
        f"{deletion_block} "
        "SET CONSTRAINTS ALL IMMEDIATE; RETURN deletion_counts; END"
    )
    return (
        f"CREATE FUNCTION {_ERASURE_RUNNER_SIGNATURE} RETURNS jsonb LANGUAGE plpgsql "
        f"SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $${body}$$"
    )


def _expected_erasure_acl_statements(relations: tuple[str, ...]) -> tuple[str, ...]:
    statements = [
        f"ALTER FUNCTION {_ERASURE_RUNNER_SIGNATURE} OWNER TO {_ERASURE_FUNCTION_OWNER}",
        f"REVOKE ALL ON FUNCTION {_ERASURE_RUNNER_SIGNATURE} FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_function_owner",
        "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_executor",
    ]
    reset_relations = tuple(dict.fromkeys((*relations, "erasure_requests", "legal_holds")))
    for table in reset_relations:
        statements.extend(
            (
                f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM idis_erasure_function_owner",
                f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM idis_erasure_executor",
            )
        )
    statements.append("GRANT USAGE ON SCHEMA public TO idis_erasure_function_owner")
    statements.extend(
        f"GRANT SELECT ({','.join(columns)}) ON TABLE public.{table} TO idis_erasure_function_owner"
        for table, columns in _ERASURE_READ_PROJECTION_COLUMNS.items()
    )
    for table in relations:
        if table in _ERASURE_READ_PROJECTION_COLUMNS:
            continue
        indirect = _ERASURE_INDIRECT_DELETE_PARENT.get(table)
        columns = (
            ("tenant_id", indirect[1])
            if indirect is not None
            else (
                "tenant_id",
                "deal_id",
            )
        )
        statements.append(
            f"GRANT SELECT ({','.join(columns)}) ON TABLE public.{table} "
            "TO idis_erasure_function_owner"
        )
    statements.extend(
        f"GRANT DELETE ON TABLE public.{table} TO idis_erasure_function_owner"
        for table in relations
    )
    statements.extend(
        (
            "GRANT UPDATE (request_id) ON TABLE public.erasure_requests "
            "TO idis_erasure_function_owner",
            f"GRANT EXECUTE ON FUNCTION {_ERASURE_RUNNER_SIGNATURE} TO idis_erasure_executor",
        )
    )
    return tuple(statements)


def _decoded_execution_sql_tuple(row: dict[str, Any], field: str) -> tuple[str, ...]:
    encoded_statements = row.get(field)
    if not isinstance(encoded_statements, list) or not encoded_statements:
        _sql_error(
            "TASK7_ERASURE_EXECUTION_SPEC_INVALID",
            f"{field} must contain literal reviewed SQL bytes",
        )
    result: list[str] = []
    for ordinal, encoded in enumerate(encoded_statements):
        decoded = _decode_reviewed_hex(encoded, f"erasure_execution_registry.{field}[{ordinal}]")
        statement = decoded.decode("utf-8")
        if statement.encode("utf-8").hex() != encoded or statement != statement.strip():
            _sql_error(
                "TASK7_ERASURE_EXECUTION_SPEC_INVALID",
                f"{field}[{ordinal}] is not canonical literal SQL",
            )
        result.append(statement)
    return tuple(result)


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _decoded_auxiliary_sql_tuple(row: dict[str, Any], field: str) -> tuple[str, ...]:
    statements = _decoded_execution_sql_tuple(row, field)
    for ordinal, statement in enumerate(statements):
        if (
            "\n" in statement
            or "\r" in statement
            or "\x00" in statement
            or "--" in statement
            or "/*" in statement
            or "*/" in statement
            or statement != " ".join(statement.split())
        ):
            _sql_error(
                "TASK7_ERASURE_AUXILIARY_SQL_INVALID",
                f"{field}[{ordinal}] is not canonical one-line single-space SQL",
            )
    return statements


def _sql_byte_fixtures(statements: tuple[str, ...]) -> tuple[tuple[int, str], ...]:
    return tuple(
        (len(statement.encode("utf-8")), _sha256_bytes(statement.encode("utf-8")))
        for statement in statements
    )


def _reject_erasure_auxiliary_dynamic_or_catalog_sql(
    statements: tuple[str, ...],
    *,
    allow_trigger_execute: bool = False,
) -> None:
    sql = " ".join(statements)
    compact = "".join(sql.lower().split())
    forbidden_literals = (
        "to_regclass",
        "information_schema",
        "pg_catalog.pg_class",
        "pg_catalog.pg_locks",
        "pg_catalog.pg_advisory_unlock",
        "pg_catalog.pg_advisory_lock(",
        "pg_advisory_unlock",
        "pg_advisory_lock(",
        "pg_advisory_lock_shared",
        "pg_advisory_xact_lock_shared",
        "pg_try_advisory",
        "locktable",
        "session_replication_role",
        "disabletrigger",
        "bypassrls",
    )
    if any(literal in compact for literal in forbidden_literals):
        _sql_error(
            "TASK7_ERASURE_AUXILIARY_SQL_INVALID",
            "auxiliary erasure SQL contains dynamic/catalog/session-lock authority",
        )
    if re.search(r"\b(?:FROM|JOIN)\s+pg_catalog\.", sql, flags=re.IGNORECASE):
        _sql_error(
            "TASK7_ERASURE_AUXILIARY_SQL_INVALID",
            "auxiliary erasure SQL cannot branch on mutable system-catalog rows",
        )
    executable = re.sub(
        r"\bGRANT\s+EXECUTE\s+ON\s+FUNCTION\b",
        "",
        sql,
        flags=re.IGNORECASE,
    )
    executable = re.sub(r"\bEXECUTE\s+FUNCTION\b", "", executable, flags=re.IGNORECASE)
    if re.search(r"\bEXECUTE\b", executable, flags=re.IGNORECASE):
        _sql_error(
            "TASK7_ERASURE_AUXILIARY_SQL_INVALID",
            "dynamic EXECUTE is forbidden",
        )
    if not allow_trigger_execute and re.search(r"\bEXECUTE\s+FUNCTION\b", sql, flags=re.IGNORECASE):
        _sql_error(
            "TASK7_ERASURE_AUXILIARY_SQL_INVALID",
            "trigger binding is outside this auxiliary SQL field",
        )


def _future_activation_census(artifact: dict[str, Any]) -> list[list[str]]:
    schema_by_relation: dict[str, str] = {}
    for section_name, table_key in (
        ("future_native_authority_registry", "planned_table"),
        ("future_reference_authority_registry", "table_name"),
        ("future_child_authority_registry", "table_name"),
    ):
        section = artifact.get(section_name)
        rows = section.get("rows") if isinstance(section, dict) else None
        if not isinstance(rows, list):
            _sql_error(
                "TASK7_ERASURE_ACTIVATION_CENSUS_INVALID",
                f"{section_name} is unavailable",
            )
        for row in rows:
            table = row.get(table_key) if isinstance(row, dict) else None
            schema_hash = row.get("schema_sha256") if isinstance(row, dict) else None
            if (
                not isinstance(table, str)
                or not isinstance(schema_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", schema_hash) is None
                or table in schema_by_relation
            ):
                _sql_error(
                    "TASK7_ERASURE_ACTIVATION_CENSUS_INVALID",
                    f"{section_name} contains an invalid schema authority row",
                )
            schema_by_relation[table] = schema_hash
    if tuple(schema_by_relation) != _FUTURE_ERASURE_RELATIONS:
        _sql_error(
            "TASK7_ERASURE_ACTIVATION_CENSUS_INVALID",
            "future schema authority order differs from FUTURE38",
        )
    return [[table, schema_by_relation[table]] for table in _FUTURE_ERASURE_RELATIONS]


def _validate_erasure_execution_phase(
    row: dict[str, Any],
    *,
    body_field: str,
    acl_field: str,
    relations: tuple[str, ...],
) -> None:
    body = _decoded_execution_sql_tuple(row, body_field)
    acl = _decoded_execution_sql_tuple(row, acl_field)
    expected_body = (_expected_erasure_runner_statement(relations),)
    expected_acl = _expected_erasure_acl_statements(relations)
    if body != expected_body:
        _sql_error(
            "TASK7_ERASURE_EXECUTION_BODY_INVALID",
            f"{body_field} differs from the exact reviewed runner bytes",
        )
    if acl != expected_acl:
        _sql_error(
            "TASK7_ERASURE_EXECUTION_ACL_INVALID",
            f"{acl_field} differs from the exact reset/least-privilege ACL bytes",
        )

    function_contract = (body[0], acl[0], acl[1], acl[-1])
    validate_sql_function_security(
        function_contract,
        authority_id=_APPROVED_ERASURE_EXECUTION_AUTHORITY,
    )
    reads, writes = scan_sql_relation_accesses(body)
    if reads != set(_ERASURE_EXECUTION_READ_RELATIONS):
        _sql_error(
            "TASK7_ERASURE_EXECUTION_READ_CLOSURE_INVALID",
            f"{body_field} reads {sorted(reads)!r}",
        )
    if writes != set(relations) or _erasure_runner_write_sequence(body) != relations:
        _sql_error(
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
            f"{body_field} does not write its exact child-first relation tuple",
        )


def _validate_hold_lock_auxiliary_sql(row: dict[str, Any]) -> None:
    if _freeze_json_value(row.get("hold_lock_contract_tuple")) != (_ERASURE_HOLD_LOCK_CONTRACT):
        _sql_error(
            "TASK7_ERASURE_HOLD_LOCK_CONTRACT_INVALID",
            "legal-hold lock contract differs from the reviewed PG16 trigger tuple",
        )
    upgrade = _decoded_auxiliary_sql_tuple(row, "hold_lock_upgrade_sql_utf8_hex_tuple")
    downgrade = _decoded_auxiliary_sql_tuple(row, "hold_lock_downgrade_sql_utf8_hex_tuple")
    expected_downgrade = (
        "DROP TRIGGER trg_task7_legal_holds_tenant_lock ON public.legal_holds",
        "DROP FUNCTION public.idis_task7_legal_hold_lock_guard()",
    )
    if downgrade != expected_downgrade:
        _sql_error(
            "TASK7_ERASURE_HOLD_LOCK_DOWNGRADE_INVALID",
            "hold-lock downgrade must remove only the exact trigger and helper",
        )
    if _sql_byte_fixtures(upgrade) != _ERASURE_HOLD_LOCK_UPGRADE_BYTE_FIXTURES:
        _sql_error(
            "TASK7_ERASURE_HOLD_LOCK_UPGRADE_INVALID",
            "hold-lock upgrade differs from the independently pinned byte fixtures",
        )
    _reject_erasure_auxiliary_dynamic_or_catalog_sql(
        upgrade,
        allow_trigger_execute=True,
    )
    _reject_erasure_auxiliary_dynamic_or_catalog_sql(downgrade)

    create_prefix = (
        "CREATE FUNCTION public.idis_task7_legal_hold_lock_guard() RETURNS trigger "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$"
    )
    function_rows = [statement for statement in upgrade if statement.startswith(create_prefix)]
    trigger = (
        "CREATE TRIGGER trg_task7_legal_holds_tenant_lock BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.legal_holds FOR EACH ROW EXECUTE FUNCTION "
        "public.idis_task7_legal_hold_lock_guard()"
    )
    required_nonbody = {
        "ALTER FUNCTION public.idis_task7_legal_hold_lock_guard() OWNER TO "
        "idis_erasure_function_owner",
        "REVOKE ALL ON FUNCTION public.idis_task7_legal_hold_lock_guard() FROM PUBLIC",
        trigger,
    }
    if (
        len(function_rows) != 1
        or len(upgrade) != 4
        or set(upgrade) != {*function_rows, *required_nonbody}
    ):
        _sql_error(
            "TASK7_ERASURE_HOLD_LOCK_UPGRADE_INVALID",
            "hold-lock upgrade object identity/owner/ACL/trigger tuple is not exact",
        )
    function_sql = function_rows[0]
    compact = "".join(function_sql.lower().split())
    exact_lock = (
        "pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended("
        "'idis-task7-erasure-tenant-lock-v1:'||locked_tenant::text,0))"
    )
    required_fragments = (
        "new.tenant_idisdistinctfromold.tenant_id",
        "task7_erasure_hold_tenant_change_rejected",
        "pg_catalog.current_setting('idis.tenant_id',true)",
        "task7_erasure_hold_tenant_context_rejected",
        "if tg_op='delete'",
        "old.tenant_id",
        "new.tenant_id",
    )
    if (
        compact.count(exact_lock) != 1
        or compact.count("pg_catalog.pg_advisory_xact_lock(") != 1
        or compact.count("pg_catalog.hashtextextended(") != 1
        or any(fragment.replace(" ", "") not in compact for fragment in required_fragments)
        or compact.index(exact_lock) > compact.rindex("return")
        or re.search(
            r"\b(?:FROM|JOIN|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+public\.",
            function_sql,
            re.IGNORECASE,
        )
        is not None
    ):
        _sql_error(
            "TASK7_ERASURE_HOLD_LOCK_UPGRADE_INVALID",
            "hold-lock helper does not enforce the exact tenant/xact-lock contract",
        )


def _normalized_auxiliary_signature(signature: str) -> tuple[str, tuple[str, ...]]:
    match = re.fullmatch(
        r"(?:public\.)?(?P<name>[a-z_][a-z0-9_]*)\((?P<arguments>[^()]*)\)",
        signature,
        flags=re.IGNORECASE,
    )
    if match is None:
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_INVALID",
            f"invalid reviewed function signature {signature!r}",
        )
    arguments = tuple(
        argument.strip().lower()
        for argument in match.group("arguments").split(",")
        if argument.strip()
    )
    return match.group("name").lower(), arguments


def _created_auxiliary_function_signature(
    statement: str,
) -> tuple[str, tuple[str, ...]] | None:
    match = re.match(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(?:public\.)?"
        r"(?P<name>[a-z_][a-z0-9_]*)\((?P<arguments>[^()]*)\)",
        statement,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    argument_types: list[str] = []
    for argument in match.group("arguments").split(","):
        tokens = argument.strip().lower().split()
        if not tokens:
            continue
        if tokens[-1] != "uuid":
            _sql_error(
                "TASK7_ERASURE_LEGACY_GUARD_INVALID",
                "legacy guard signatures may contain only the reviewed UUID arguments",
            )
        argument_types.append(tokens[-1])
    return match.group("name").lower(), tuple(argument_types)


def _replacement_statement_for_signature(statements: tuple[str, ...], signature: str) -> str:
    expected = _normalized_auxiliary_signature(signature)
    matches = [
        statement
        for statement in statements
        if _created_auxiliary_function_signature(statement) == expected
    ]
    if len(matches) != 1:
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_INVALID",
            f"legacy guard statement cardinality differs for {signature}",
        )
    return matches[0]


def _validate_legacy_guard_auxiliary_sql(row: dict[str, Any]) -> None:
    if _freeze_json_value(row.get("guard_authority_contract_tuple")) != (
        _ERASURE_GUARD_AUTHORITY_CONTRACT
    ):
        _sql_error(
            "TASK7_ERASURE_GUARD_AUTHORITY_INVALID",
            "guard authority tuple differs from the reviewed dedicated context helper",
        )
    if _freeze_json_value(row.get("legacy_guard_replacement_tuple")) != (
        _ERASURE_LEGACY_GUARD_REPLACEMENTS
    ):
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_INVENTORY_INVALID",
            "legacy guard replacement/catalog/digest inventory differs",
        )

    upgrade = _decoded_auxiliary_sql_tuple(row, "legacy_guard_upgrade_sql_utf8_hex_tuple")
    downgrade = _decoded_auxiliary_sql_tuple(row, "legacy_guard_downgrade_sql_utf8_hex_tuple")
    _reject_erasure_auxiliary_dynamic_or_catalog_sql(upgrade)
    _reject_erasure_auxiliary_dynamic_or_catalog_sql(downgrade)

    authority_prefix = "CREATE FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) "
    authority_rows = [statement for statement in upgrade if statement.startswith(authority_prefix)]
    expected_authority_acl = {
        "ALTER FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) OWNER TO "
        "idis_erasure_function_owner",
        "REVOKE ALL ON FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) "
        "FROM PUBLIC",
    }
    replacement_rows = tuple(
        _replacement_statement_for_signature(upgrade, replacement[1])
        for replacement in _ERASURE_LEGACY_GUARD_REPLACEMENTS
    )
    expected_upgrade_prefix = (
        authority_rows[0] if authority_rows else "",
        "ALTER FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) OWNER TO "
        "idis_erasure_function_owner",
        "REVOKE ALL ON FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) "
        "FROM PUBLIC",
    )
    if (
        len(authority_rows) != 1
        or len(set(replacement_rows)) != len(_ERASURE_LEGACY_GUARD_REPLACEMENTS)
        or len(upgrade) != 10
        or set(upgrade) != {*authority_rows, *expected_authority_acl, *replacement_rows}
        or upgrade != (*expected_upgrade_prefix, *replacement_rows)
    ):
        _sql_error(
            "TASK7_ERASURE_GUARD_AUTHORITY_INVALID",
            "guard authority/replacement statement identity and ACL tuple differs",
        )
    if _sql_byte_fixtures(upgrade) != _ERASURE_LEGACY_GUARD_UPGRADE_BYTE_FIXTURES:
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_INVALID",
            "legacy guard upgrade differs from the independently pinned byte fixtures",
        )
    authority_compact = "".join(authority_rows[0].lower().split())
    authority_required = (
        "securitydefiner",
        "setsearch_path=pg_catalog,pg_temp",
        "session_user='idis_erasure_executor'",
        "current_user='idis_erasure_function_owner'",
        "pg_catalog.current_setting('idis.tenant_id',true)",
        "pg_catalog.current_setting('idis.erasure_context',true)='true'",
        "statusin('requested','failed')",
        "frompublic.erasure_requests",
    )
    if any(fragment not in authority_compact for fragment in authority_required):
        _sql_error(
            "TASK7_ERASURE_GUARD_AUTHORITY_INVALID",
            "guard authority omits an authenticated transaction-local precondition",
        )
    for statement in replacement_rows:
        compact = "".join(statement.lower().split())
        if (
            "setsearch_path=pg_catalog,pg_temp" not in compact
            or "public.idis_task7_approved_erasure_context_v1(" not in compact
            or "fromerasure_requests" in compact
            or "fromruns" in compact
            or "frommethodology_" in compact
            or "set_config('idis.erasure_context'" in compact
        ):
            _sql_error(
                "TASK7_ERASURE_LEGACY_GUARD_INVALID",
                "replacement guard bypasses the dedicated context authority or read closure",
            )
    deferred = _replacement_statement_for_signature(
        upgrade,
        "public.idis_task6_validate_result_outputs()",
    )
    deferred_compact = "".join(deferred.lower().split())
    early_return = deferred_compact.find("public.idis_task7_approved_erasure_context_v1(")
    result_read = deferred_compact.find("frompublic.methodology_extraction_results")
    factual_read = deferred_compact.find("frompublic.methodology_result_factual_outputs")
    if result_read < 0 or factual_read < 0:
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_INVALID",
            "deferred validation guard omits a reviewed parent relation",
        )
    first_validation_read = min(result_read, factual_read)
    if (
        early_return < 0
        or early_return >= first_validation_read
        or "returnnull" not in deferred_compact[early_return:first_validation_read]
    ):
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_INVALID",
            "deferred validation guard does not return before deleted-parent reads",
        )

    if len(downgrade) != len(_ERASURE_LEGACY_GUARD_REPLACEMENTS) + 1:
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_RESTORATION_INVALID",
            "legacy guard restoration statement census differs",
        )
    restoration_rows = tuple(
        _replacement_statement_for_signature(downgrade, replacement[1])
        for replacement in _ERASURE_LEGACY_GUARD_REPLACEMENTS
    )
    if downgrade[:-1] != restoration_rows:
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_RESTORATION_INVALID",
            "legacy restoration function order differs from the reviewed inventory",
        )
    for replacement_ordinal, (replacement, statement) in enumerate(
        zip(
            _ERASURE_LEGACY_GUARD_REPLACEMENTS,
            restoration_rows,
            strict=True,
        )
    ):
        if (
            len(statement.encode("utf-8"))
            != (_ERASURE_LEGACY_GUARD_RESTORATION_BYTE_LENGTHS[replacement_ordinal])
        ):
            _sql_error(
                "TASK7_ERASURE_LEGACY_GUARD_RESTORATION_INVALID",
                f"legacy byte length differs for {replacement[1]}",
            )
        if _sha256_bytes(statement.encode("utf-8")) != replacement[6]:
            _sql_error(
                "TASK7_ERASURE_LEGACY_GUARD_RESTORATION_INVALID",
                f"legacy bytes differ for {replacement[1]}",
            )
        compact = "".join(statement.lower().split())
        if (
            "idis_task7_approved_erasure_context_v1" in compact
            or "setsearch_path=" in compact
            or "securitydefiner" in compact
        ):
            _sql_error(
                "TASK7_ERASURE_LEGACY_GUARD_RESTORATION_INVALID",
                f"legacy bytes retain Task-7 authority for {replacement[1]}",
            )
    if downgrade[-1] != ("DROP FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid)"):
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_RESTORATION_INVALID",
            "legacy restoration must finish by dropping the exact Task-7 helper",
        )
    restoration_sql = " ".join(downgrade).upper()
    if any(
        token in restoration_sql
        for token in (
            "ALTER FUNCTION",
            "ALTER TRIGGER",
            "DROP TRIGGER",
            "CREATE TRIGGER",
            "DISABLE TRIGGER",
            "IF EXISTS",
            "CASCADE",
        )
    ):
        _sql_error(
            "TASK7_ERASURE_LEGACY_GUARD_RESTORATION_INVALID",
            "legacy restoration changes catalog shape beyond literal function bodies",
        )


def _validate_future_activation_auxiliary_sql(row: dict[str, Any]) -> None:
    activation = _decoded_auxiliary_sql_tuple(row, "future_activation_sql_utf8_hex_tuple")
    restoration = _decoded_auxiliary_sql_tuple(row, "future_restoration_sql_utf8_hex_tuple")
    future_body = _decoded_execution_sql_tuple(row, "future_body_sql_utf8_hex_tuple")
    future_acl = _decoded_execution_sql_tuple(row, "future_acl_sql_utf8_hex_tuple")
    current_body = _decoded_execution_sql_tuple(row, "current_body_sql_utf8_hex_tuple")
    current_acl = _decoded_execution_sql_tuple(row, "current_acl_sql_utf8_hex_tuple")
    drop = f"DROP FUNCTION {_ERASURE_RUNNER_SIGNATURE}"
    if activation != (drop, *future_body, *future_acl):
        _sql_error(
            "TASK7_ERASURE_FUTURE_ACTIVATION_INVALID",
            "future activation is not the exact drop/RUNNER86/ACL choreography",
        )
    if restoration != (drop, *current_body, *current_acl):
        _sql_error(
            "TASK7_ERASURE_FUTURE_RESTORATION_INVALID",
            "future restoration is not the exact drop/CURRENT48/ACL choreography",
        )
    for statements in (activation, restoration):
        _reject_erasure_auxiliary_dynamic_or_catalog_sql(statements)
        if (
            statements.count(drop) != 1
            or statements[1].upper().startswith("CREATE OR REPLACE FUNCTION")
            or any(
                statement.upper().startswith("DROP FUNCTION IF EXISTS") for statement in statements
            )
        ):
            _sql_error(
                "TASK7_ERASURE_FUTURE_ACTIVATION_INVALID",
                "future runner choreography contains an unreviewed replacement path",
            )


def validate_erasure_execution_registry(artifact: dict[str, Any]) -> str:
    """Validate the hash-bound active/frozen erasure execution lifecycle."""

    tuple_rows = artifact.get("tuple_schema_registry", {}).get("rows", [])
    try:
        tuple_specs = dict(tuple_rows)
    except (TypeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_ERASURE_EXECUTION_SPEC_INVALID",
            "tuple schema registry is malformed",
        ) from exc
    if tuple(tuple_specs.get("ErasureExecutionSpecV1", ())) != _ERASURE_EXECUTION_FIELDS:
        _sql_error(
            "TASK7_ERASURE_EXECUTION_SPEC_INVALID",
            "ErasureExecutionSpecV1 does not equal the closed field tuple",
        )

    section = artifact.get("erasure_execution_registry")
    if not isinstance(section, dict) or tuple(section) != ("rows", "expected_sha256"):
        _sql_error(
            "TASK7_ERASURE_EXECUTION_SPEC_INVALID",
            "erasure_execution_registry must contain only rows and expected_sha256",
        )
    rows = section["rows"]
    if not isinstance(rows, list) or len(rows) != 1:
        _sql_error(
            "TASK7_ERASURE_EXECUTION_SPEC_INVALID",
            "erasure execution registry must contain exactly one lifecycle row",
        )
    row = rows[0]
    if not isinstance(row, dict) or tuple(row) != _ERASURE_EXECUTION_FIELDS:
        _sql_error(
            "TASK7_ERASURE_EXECUTION_SPEC_INVALID",
            "erasure execution row does not match ErasureExecutionSpecV1",
        )

    expected_scalars = {
        "execution_ordinal": 0,
        "execution_id": _APPROVED_ERASURE_EXECUTION_AUTHORITY,
        "execution_version": "1",
        "runner_function_signature": _ERASURE_RUNNER_SIGNATURE,
        "activation_state": _ERASURE_EXECUTION_ACTIVATION_STATE,
    }
    if any(row[key] != value for key, value in expected_scalars.items()):
        _sql_error(
            "TASK7_ERASURE_EXECUTION_SPEC_INVALID",
            "erasure execution identity/version/state differs from the reviewed lifecycle",
        )

    relation_contract = (
        ("current_relation_tuple", _CURRENT_ERASURE_RELATIONS),
        ("future_relation_tuple", _FUTURE_ERASURE_RELATIONS),
        ("complete_relation_tuple", _APPROVED_ERASURE_RUNNER_SEQUENCE),
        ("declared_read_relation_tuple", _ERASURE_EXECUTION_READ_RELATIONS),
    )
    if any(tuple(row.get(field, ())) != expected for field, expected in relation_contract):
        _sql_error(
            "TASK7_ERASURE_EXECUTION_SPLIT_INVALID",
            "erasure execution relation/read tuples differ from the reviewed literals",
        )
    current = frozenset(_CURRENT_ERASURE_RELATIONS)
    future = frozenset(_FUTURE_ERASURE_RELATIONS)
    complete = frozenset(_APPROVED_ERASURE_RUNNER_SEQUENCE)
    if (
        len(current) != 48
        or len(future) != 38
        or len(complete) != 86
        or current & future
        or current | future != complete
        or tuple(relation for relation in _APPROVED_ERASURE_RUNNER_SEQUENCE if relation in current)
        != _CURRENT_ERASURE_RELATIONS
    ):
        _sql_error(
            "TASK7_ERASURE_EXECUTION_SPLIT_INVALID",
            "CURRENT48/FUTURE38/RUNNER86 equations are not exact",
        )
    if tuple(row.get("advisory_lock_tuple", ())) != _ERASURE_ADVISORY_LOCK_TUPLE:
        _sql_error(
            "TASK7_ERASURE_EXECUTION_LOCK_INVALID",
            "advisory lock tuple differs from the PostgreSQL-16 reviewed contract",
        )

    _validate_erasure_execution_phase(
        row,
        body_field="current_body_sql_utf8_hex_tuple",
        acl_field="current_acl_sql_utf8_hex_tuple",
        relations=_CURRENT_ERASURE_RELATIONS,
    )
    _validate_erasure_execution_phase(
        row,
        body_field="future_body_sql_utf8_hex_tuple",
        acl_field="future_acl_sql_utf8_hex_tuple",
        relations=_APPROVED_ERASURE_RUNNER_SEQUENCE,
    )
    _validate_hold_lock_auxiliary_sql(row)
    _validate_legacy_guard_auxiliary_sql(row)
    _validate_future_activation_auxiliary_sql(row)

    expected_census = _future_activation_census(artifact)
    if row.get("future_activation_census_tuple") != expected_census:
        _sql_error(
            "TASK7_ERASURE_ACTIVATION_CENSUS_INVALID",
            "future activation census differs from the 38 schema authority hashes",
        )
    census_hash = _sha256_bytes(
        _ERASURE_ACTIVATION_CENSUS_HASH_DOMAIN + _canonical_bytes(expected_census)
    )
    if row.get("future_activation_census_sha256") != census_hash:
        _sql_error(
            "TASK7_ERASURE_ACTIVATION_CENSUS_INVALID",
            "future activation census hash differs from its literal bytes",
        )

    red_categories = (
        row.get("legal_red_case_tuple"),
        row.get("illegal_red_case_tuple"),
        row.get("direct_sql_red_case_tuple"),
    )
    if red_categories != tuple([node_id] for node_id in _ERASURE_EXECUTION_REQUIRED_RED_CASES):
        _sql_error(
            "TASK7_ERASURE_EXECUTION_RED_CASE_INVALID",
            "execution RED node IDs differ from the reviewed exact triplet",
        )

    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row_hash = _sha256_bytes(_ERASURE_EXECUTION_ROW_HASH_DOMAIN + _canonical_bytes(payload))
    _expect(row_hash, row.get("expected_sha256"), "erasure execution row 0")
    registry_hash = _h(["full-strict-v4-erasure-execution-registry-v1", rows])
    _expect(registry_hash, section.get("expected_sha256"), "erasure_execution_registry_hash")
    return registry_hash


def _erasure_execution_registry_hash(artifact: dict[str, Any]) -> str:
    """Derive this component directly; strict admission is performed separately."""

    return _h(
        [
            "full-strict-v4-erasure-execution-registry-v1",
            artifact["erasure_execution_registry"]["rows"],
        ]
    )


def validate_sql_authority_registry(artifact: dict[str, Any]) -> str:
    """Independently validate the complete closed SQL-authority registry."""

    tuple_rows = artifact.get("tuple_schema_registry", {}).get("rows", [])
    try:
        tuple_specs = dict(tuple_rows)
    except (TypeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_SQL_AUTHORITY_SPEC_INVALID", "tuple schema registry is malformed"
        ) from exc
    if tuple(tuple_specs.get("SqlAuthoritySpecV1", ())) != _SQL_AUTHORITY_FIELDS:
        _sql_error(
            "TASK7_SQL_AUTHORITY_SPEC_INVALID",
            "SqlAuthoritySpecV1 does not equal the closed field tuple",
        )

    section = artifact.get("sql_authority_registry")
    if not isinstance(section, dict) or tuple(section) != ("rows", "expected_sha256"):
        _sql_error(
            "TASK7_SQL_AUTHORITY_SPEC_INVALID",
            "sql_authority_registry must contain only rows and expected_sha256",
        )
    rows = section["rows"]
    if not isinstance(rows, list) or len(rows) != len(_REQUIRED_SQL_AUTHORITY_IDS):
        _sql_error("TASK7_SQL_AUTHORITY_SPEC_INVALID", "SQL authority row census mismatch")
    if tuple(row.get("authority_id") for row in rows if isinstance(row, dict)) != (
        _REQUIRED_SQL_AUTHORITY_IDS
    ):
        _sql_error("TASK7_SQL_AUTHORITY_SPEC_INVALID", "SQL authority ID/order mismatch")

    future_rows: list[tuple[str, dict[str, Any]]] = []
    for section_name, table_key in (
        ("future_native_authority_registry", "planned_table"),
        ("future_reference_authority_registry", "table_name"),
        ("future_child_authority_registry", "table_name"),
    ):
        relation_section = artifact.get(section_name)
        if not isinstance(relation_section, dict) or not isinstance(
            relation_section.get("rows"), list
        ):
            _sql_error("TASK7_SQL_AUTHORITY_SPEC_INVALID", f"{section_name} is malformed")
        for relation_row in relation_section["rows"]:
            if not isinstance(relation_row, dict) or not isinstance(
                relation_row.get(table_key), str
            ):
                _sql_error(
                    "TASK7_SQL_AUTHORITY_SPEC_INVALID",
                    f"{section_name} contains a malformed relation row",
                )
            future_rows.append((relation_row[table_key], relation_row))
    future_by_table = dict(future_rows)
    if tuple(future_by_table) != _FUTURE_RELATION_TABLES:
        _sql_error("TASK7_SQL_AUTHORITY_SPEC_INVALID", "future relation census/order mismatch")

    reviewed_erasure_writes = _reviewed_erasure_write_sequence(artifact)
    _validate_erasure_runner_superset(reviewed_erasure_writes)
    all_target_objects: set[tuple[str, str]] = set()
    row_by_id: dict[str, dict[str, Any]] = {}
    for ordinal, row in enumerate(rows):
        path = f"sql_authority_registry.rows[{ordinal}]"
        if not isinstance(row, dict) or tuple(row) != _SQL_AUTHORITY_FIELDS:
            _sql_error(
                "TASK7_SQL_AUTHORITY_SPEC_INVALID",
                f"{path} does not exactly match SqlAuthoritySpecV1",
            )
        authority_id = row["authority_id"]
        if row["authority_ordinal"] != ordinal or row["authority_version"] != "1":
            _sql_error("TASK7_SQL_AUTHORITY_SPEC_INVALID", f"{path} ordinal/version mismatch")
        expected_kind = _REQUIRED_SQL_AUTHORITY_KINDS[authority_id]
        if row["authority_kind"] != expected_kind:
            _sql_error(
                "TASK7_SQL_AUTHORITY_SPEC_INVALID",
                f"{path} kind {row['authority_kind']!r} != {expected_kind!r}",
            )
        expected_events = _REQUIRED_SQL_AUTHORITY_EVENTS[authority_id]
        if expected_events is None:
            _sql_error(
                "TASK7_SQL_AUTHORITY_EVENT_SET_UNRESOLVED",
                f"{authority_id} has no reviewer-approved exact trigger event tuple",
            )

        targets = row["target_tuple"]
        if (
            not isinstance(targets, list)
            or not targets
            or any(
                not isinstance(target, list)
                or len(target) != 2
                or any(not isinstance(part, str) or not part for part in target)
                for target in targets
            )
            or len(targets) != len({tuple(target) for target in targets})
        ):
            _sql_error("TASK7_SQL_AUTHORITY_SPEC_INVALID", f"{path} target tuple malformed")
        target_tables = tuple(dict.fromkeys(target[0] for target in targets))
        if target_tables != _REQUIRED_TARGET_TABLES[authority_id]:
            _sql_error(
                "TASK7_SQL_AUTHORITY_TARGET_CLOSURE_MISMATCH",
                f"{authority_id}: {target_tables!r}",
            )
        target_objects = {tuple(target) for target in targets}
        collision = all_target_objects & target_objects
        if collision:
            _sql_error(
                "TASK7_SQL_AUTHORITY_TARGET_CLOSURE_MISMATCH",
                f"target object collision: {sorted(collision)!r}",
            )
        all_target_objects.update(target_objects)

        parents = row["declared_parent_relation_tuple"]
        if (
            not isinstance(parents, list)
            or any(not isinstance(parent, str) or not parent for parent in parents)
            or len(parents) != len(set(parents))
            or tuple(parents) != _REQUIRED_PARENT_RELATIONS[authority_id]
        ):
            _sql_error(
                "TASK7_SQL_AUTHORITY_PARENT_CLOSURE_MISMATCH",
                f"{authority_id}: {parents!r}",
            )

        encoded_statements = row["body_sql_utf8_hex_tuple"]
        if not isinstance(encoded_statements, list) or not encoded_statements:
            _sql_error(
                "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                f"{authority_id} has no literal SQL",
            )
        statements: list[str] = []
        for statement_ordinal, encoded in enumerate(encoded_statements):
            decoded = _decode_reviewed_hex(
                encoded, f"{path}.body_sql_utf8_hex_tuple[{statement_ordinal}]"
            )
            statement = decoded.decode("utf-8")
            if statement.encode("utf-8").hex() != encoded:
                _sql_error(
                    "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
                    f"{authority_id} SQL hex is not canonical",
                )
            statements.append(statement)

        red_categories = (
            row["legal_red_case_tuple"],
            row["illegal_red_case_tuple"],
            row["direct_sql_red_case_tuple"],
        )
        if any(
            not isinstance(category, list)
            or not category
            or any(
                not isinstance(node_id, str)
                or re.fullmatch(
                    r"tests/slice100_task7/test_[a-z0-9_]+\.py::test_[^\r\n]+",
                    node_id,
                )
                is None
                for node_id in category
            )
            or len(category) != len(set(category))
            for category in red_categories
        ):
            _sql_error(
                "TASK7_SQL_AUTHORITY_RED_CASE_INVALID",
                f"{authority_id} RED node IDs are not exact collected-node syntax",
            )
        if any(
            set(left) & set(right)
            for index, left in enumerate(red_categories)
            for right in red_categories[index + 1 :]
        ):
            _sql_error(
                "TASK7_SQL_AUTHORITY_RED_CASE_INVALID",
                f"{authority_id} legal/illegal/direct categories overlap",
            )
        expected_red_cases = _REQUIRED_SQL_RED_CASES[authority_id]
        if tuple(tuple(category) for category in red_categories) != tuple(
            (node_id,) for node_id in expected_red_cases
        ):
            _sql_error(
                "TASK7_SQL_AUTHORITY_RED_CASE_INVALID",
                f"{authority_id} RED nodes differ from the reviewed exact triplet",
            )

        analysis = validate_sql_authority_statement_tuple(
            authority_id, row["authority_kind"], tuple(statements)
        )
        if any(events != expected_events for events in analysis["trigger_events"].values()):
            _sql_error(
                "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
                f"{authority_id} trigger events differ from {expected_events!r}",
            )
        if analysis["reads"] != set(parents):
            _sql_error(
                "TASK7_SQL_AUTHORITY_PARENT_CLOSURE_MISMATCH",
                f"{authority_id} reads {sorted(analysis['reads'])!r}",
            )
        allowed_writes = set(target_tables)
        if not analysis["writes"] <= allowed_writes:
            _sql_error(
                "TASK7_SQL_AUTHORITY_TARGET_CLOSURE_MISMATCH",
                f"{authority_id} writes {sorted(analysis['writes'])!r}",
            )
        if row["authority_kind"] == "CHECK":
            if len(statements) != len(targets):
                _sql_error(
                    "TASK7_SQL_AUTHORITY_TARGET_CLOSURE_MISMATCH",
                    f"{authority_id} CHECK statement/target cardinality mismatch",
                )
            for target, expression in zip(targets, statements, strict=True):
                relation_row = future_by_table.get(target[0])
                checks = relation_row.get("check_tuple") if relation_row is not None else None
                if (
                    not isinstance(checks, list)
                    or any(not isinstance(check, list) or len(check) != 2 for check in checks)
                    or dict(checks).get(target[1]) != expression
                ):
                    _sql_error(
                        "TASK7_SQL_AUTHORITY_TARGET_CLOSURE_MISMATCH",
                        f"{authority_id} CHECK {target!r} differs from relation authority",
                    )
        elif analysis["triggers"] != target_objects:
            _sql_error(
                "TASK7_SQL_AUTHORITY_TARGET_CLOSURE_MISMATCH",
                f"{authority_id} installed triggers differ from target_tuple",
            )

        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        row_hash = _sha256_bytes(_SQL_AUTHORITY_ROW_HASH_DOMAIN + _canonical_bytes(payload))
        _expect(row_hash, row["expected_sha256"], f"SQL authority row {ordinal}")
        row_by_id[authority_id] = row

    for table, relation_row in future_rows:
        expected_authorities = [
            authority_id
            for authority_id in _REQUIRED_SQL_AUTHORITY_IDS
            if table in _REQUIRED_TARGET_TABLES[authority_id]
        ]
        if relation_row.get("sql_authority_id_tuple") != expected_authorities:
            _sql_error(
                "TASK7_SQL_AUTHORITY_TARGET_CLOSURE_MISMATCH",
                f"{table} authority ownership differs from target closure",
            )

    registry_hash = _h(["full-strict-v4-sql-authority-registry-v1", rows])
    _expect(registry_hash, section["expected_sha256"], "sql_authority_registry_hash")
    return registry_hash


def _sql_authority_registry_hash(artifact: dict[str, Any]) -> str:
    section = artifact["sql_authority_registry"]
    rows = section["rows"]
    for ordinal, row in enumerate(rows):
        if row.get("authority_ordinal") != ordinal:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID", "SQL authority ordinal gap"
            )
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        row_hash = _sha256_bytes(_SQL_AUTHORITY_ROW_HASH_DOMAIN + _canonical_bytes(payload))
        _expect(row_hash, row["expected_sha256"], f"SQL authority row {ordinal}")
    result = _h(["full-strict-v4-sql-authority-registry-v1", rows])
    _expect(result, section["expected_sha256"], "sql_authority_registry_hash")
    return result


def _derive_components(
    artifact: dict[str, Any], reviewed_preimage_artifact_hash: str
) -> dict[str, str]:
    _, schema_hash = _schema_components(artifact)
    _, semantic_hash = _semantic_components(artifact)
    baseline_erasure_hash = _erasure_hash(artifact["erasure_baseline"], "baseline")
    final_erasure_hash = _erasure_hash(artifact["erasure_materialized"], "materialized")
    ddl_hash = _h(
        [
            "task7-ddl-registry-v2",
            artifact["current_ddl_registry"]["rows"],
            final_erasure_hash,
        ]
    )
    _expect(
        ddl_hash,
        artifact["current_ddl_registry"]["expected_sha256"],
        "task7_ddl_registry_hash",
    )
    future_native_hash = _generic_registry_hash(
        artifact,
        "future_native_authority_registry",
        "task7-future-native-authority-registry-v2",
    )
    future_reference_hash = _generic_registry_hash(
        artifact,
        "future_reference_authority_registry",
        "task7-future-reference-authority-registry-v2",
    )
    future_child_hash = _generic_registry_hash(
        artifact,
        "future_child_authority_registry",
        "task7-future-child-authority-registry-v2",
    )
    sql_authority_hash = _sql_authority_registry_hash(artifact)
    erasure_execution_hash = _erasure_execution_registry_hash(artifact)
    provider_hash = _generic_registry_hash(
        artifact, "provider_registry", "task7-provider-registry-v1"
    )
    rights_index_policy_hash = _rights_index_policy_registry_hash(artifact)
    role_hash = _generic_registry_hash(artifact, "role_registry", "task7-role-registry-v1")
    deliverable_hash = _generic_registry_hash(
        artifact, "deliverable_registry", "task7-deliverable-registry-v1"
    )
    uuid_hash = _generic_registry_hash(
        artifact, "uuid_namespace_registry", "task7-uuid-namespace-registry-v1"
    )
    (
        producer_hashes,
        consumer_hashes,
        producer_registry_hash,
        consumer_registry_hash,
        selector_hash,
    ) = _selector_components(artifact)
    predicate_hashes, applicability_hash = _applicability_components(artifact)
    edge_hash = _edge_components(artifact, producer_hashes, consumer_hashes, predicate_hashes)
    route_hash, extractor_hash = _usage_components(artifact)
    return {
        "reviewed_preimage_artifact_hash": reviewed_preimage_artifact_hash,
        "schema_registry_hash": schema_hash,
        "semantic_step_vector_hash": semantic_hash,
        "baseline_erasure_schedule_hash": baseline_erasure_hash,
        "task7_erasure_schedule_hash": final_erasure_hash,
        "task7_ddl_registry_hash": ddl_hash,
        "future_native_authority_registry_hash": future_native_hash,
        "future_reference_authority_registry_hash": future_reference_hash,
        "future_child_authority_registry_hash": future_child_hash,
        "sql_authority_registry_hash": sql_authority_hash,
        "erasure_execution_registry_hash": erasure_execution_hash,
        "provider_registry_hash": provider_hash,
        "rights_index_policy_registry_hash": rights_index_policy_hash,
        "role_registry_hash": role_hash,
        "deliverable_registry_hash": deliverable_hash,
        "producer_selector_registry_hash": producer_registry_hash,
        "consumer_selector_registry_hash": consumer_registry_hash,
        "selector_registry_hash": selector_hash,
        "applicability_registry_hash": applicability_hash,
        "usage_path_route_hash": route_hash,
        "usage_extractor_registry_hash": extractor_hash,
        "edge_manifest_hash": edge_hash,
        "uuid_namespace_registry_hash": uuid_hash,
    }


def _validate_artifact_shape(raw: bytes, artifact: dict[str, Any]) -> None:
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "artifact must be LF-only with one newline"
        )
    if tuple(artifact) != _SECTION_ORDER:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "top-level section order mismatch"
        )
    metadata = artifact["metadata"]
    if tuple(metadata["section_order"]) != _SECTION_ORDER:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "metadata section order mismatch"
        )
    if metadata["artifact_schema"] != "full-strict-v4-reviewed-preimage-v1":
        raise ContractArtifactError("TASK7_CONTRACT_ARTIFACT_INVALID", "artifact schema mismatch")
    if metadata.get("pipeline_contract_version") != "full-strict-v4":
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "pipeline contract version must be exactly full-strict-v4",
        )
    if metadata["append_frozen"] is not True:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "artifact is not append-frozen"
        )
    if metadata["pydantic_version"] != "2.12.5":
        raise ContractArtifactError("TASK7_CONTRACT_ARTIFACT_INVALID", "Pydantic version mismatch")
    assertions = artifact["activation_assertions"]
    if tuple(assertions) != _ACTIVATION_ASSERTION_FIELDS:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "activation assertion field contract mismatch",
        )
    count_contract = (
        ("semantic_step_registry", "semantic_step_count", 28),
        ("edge_rule_registry", "edge_rule_count", 48),
        ("usage_route_registry", "usage_route_count", 1756),
        ("usage_extractor_registry", "usage_extractor_count", 19),
    )
    for section, assertion_key, expected in count_contract:
        if len(artifact[section]["rows"]) != expected or assertions[assertion_key] != expected:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID", f"{section} count mismatch"
            )
    route_ordinals = [row[0] for row in artifact["usage_route_registry"]["rows"]]
    if route_ordinals != list(range(1756)):
        raise ContractArtifactError("TASK7_CONTRACT_ARTIFACT_INVALID", "usage route ordinal gap")
    if len(artifact["current_ddl_registry"]["rows"]) != 34:
        raise ContractArtifactError("TASK7_CONTRACT_ARTIFACT_INVALID", "current DDL count mismatch")
    if len(artifact["future_native_authority_registry"]["rows"]) != 18:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "future native count mismatch"
        )
    if len(artifact["future_reference_authority_registry"]["rows"]) != 15:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "future reference count mismatch"
        )
    if len(artifact["future_child_authority_registry"]["rows"]) != 5:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "future child count mismatch"
        )
    if len(artifact["sql_authority_registry"]["rows"]) != 17:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "SQL authority count mismatch"
        )
    if len(artifact["erasure_execution_registry"]["rows"]) != 1:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID", "erasure execution count mismatch"
        )
    _rights_index_policy_registry_hash(artifact)
    erasure_execution = artifact["erasure_execution_registry"]["rows"][0]
    erasure_assertions = {
        "erasure_current_relation_count": len(erasure_execution["current_relation_tuple"]),
        "erasure_future_relation_count": len(erasure_execution["future_relation_tuple"]),
        "erasure_complete_relation_count": len(erasure_execution["complete_relation_tuple"]),
        "expected_erasure_execution_sha256": erasure_execution["expected_sha256"],
    }
    if any(assertions[key] != value for key, value in erasure_assertions.items()):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "erasure execution activation assertions mismatch",
        )
    baseline = artifact["erasure_baseline"]
    materialized = artifact["erasure_materialized"]
    if (
        baseline["phase_count"] != 59
        or baseline["physical_table_count"] != 67
        or len(baseline["rows"]) != 59
        or materialized["phase_count"] != 66
        or materialized["physical_table_count"] != 72
        or len(materialized["rows"]) != 66
    ):
        raise ContractArtifactError("TASK7_CONTRACT_ARTIFACT_INVALID", "erasure census mismatch")
    validate_sql_authority_registry(artifact)
    validate_erasure_execution_registry(artifact)
    _validate_reviewed_row_bytes(artifact)
    _validate_scalar_tree(artifact)


def derive_contract_bytes(raw: bytes, *, validate: bool) -> dict[str, Any]:
    """Derive every component; validation never supplies hashing inputs."""

    artifact = _decode(raw)
    artifact_sha256 = _sha256_bytes(raw)
    available_section_hashes = [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": _sha256_bytes(_canonical_bytes(value)),
        }
        for ordinal, (name, value) in enumerate(artifact.items())
    ]
    reviewed_preimage_artifact_hash = _h(
        [
            "full-strict-v4-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in available_section_hashes],
        ]
    )
    if validate:
        _validate_artifact_shape(raw, artifact)
        available_section_hashes = _section_hashes(artifact)
        components = _derive_components(artifact, reviewed_preimage_artifact_hash)
        pipeline_values = [components[name] for name in _COMPONENT_ORDER]
        pipeline_hash = _h(
            [
                "pipeline-contract-v4",
                artifact["metadata"]["pipeline_contract_version"],
                reviewed_preimage_artifact_hash,
                pipeline_values,
            ]
        )
    else:
        try:
            components = _derive_components(artifact, reviewed_preimage_artifact_hash)
            pipeline_values = [components[name] for name in _COMPONENT_ORDER]
            version = artifact.get("metadata", {}).get("pipeline_contract_version", "invalid")
            pipeline_hash = _h(
                [
                    "pipeline-contract-v4",
                    version,
                    reviewed_preimage_artifact_hash,
                    pipeline_values,
                ]
            )
        except (ContractArtifactError, KeyError, TypeError, ValueError, IndexError) as exc:
            components = {
                "reviewed_preimage_artifact_hash": reviewed_preimage_artifact_hash,
                "invalid_derivation_sha256": _sha256_bytes(f"{type(exc).__name__}:{exc}".encode()),
            }
            pipeline_hash = _h(
                [
                    "pipeline-contract-v4-invalid-artifact",
                    reviewed_preimage_artifact_hash,
                    components["invalid_derivation_sha256"],
                ]
            )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v4-contract-hash-fixture-v1",
        "artifact_sha256": artifact_sha256,
        "section_hashes": available_section_hashes,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V4_CONTRACT_HASH": pipeline_hash,
    }


def _render_fixture(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_fixture_exclusive(output: Path, rendered: str) -> None:
    """Create immutable fixture evidence without ever replacing existing bytes."""

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
    except FileExistsError as exc:
        raise ContractArtifactError(
            "TASK7_V4_FIXTURE_OUTPUT_ALREADY_EXISTS",
            str(output),
        ) from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = derive_contract_bytes(args.artifact.read_bytes(), validate=True)
        rendered = _render_fixture(result)
        if args.stdout:
            sys.stdout.buffer.write(rendered.encode("utf-8"))
        else:
            if args.output is None:
                raise ContractArtifactError(
                    "TASK7_CONTRACT_ARTIFACT_INVALID",
                    "fixture output path is absent",
                )
            _write_fixture_exclusive(args.output, rendered)
        return 0
    except ContractArtifactError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
