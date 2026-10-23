from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from _historical_v6_fixture import historical_v6_fixture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V4_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v4_reviewed_preimage_v1.json"
_V5_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v6_reviewed_preimage_v1.json"
_V7_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v7_reviewed_preimage_v1.json"
_V16_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V21_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_ARTIFACT = _V5_ARTIFACT
_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v6_contract_fixture.py"
_V5_GENERATOR_HELPER = _REPO_ROOT / "scripts" / "generate_task7_v5_contract_fixture.py"
_V4_GENERATOR_HELPER = _REPO_ROOT / "scripts" / "generate_task7_v4_contract_fixture.py"
_DRIFT_CHECKER = _REPO_ROOT / "scripts" / "check_task7_pydantic_schema_drift.py"
_REVIEWED_AUTHORITY = (
    _REPO_ROOT / "tests" / "slice100_task7" / "_full_strict_v4_reviewed_authority.py"
)
_BLOCKLISTED_V6_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v6_contract_hash_v1.json"
)

_SECTION_ORDER = (
    "metadata",
    "canonicalization_contract",
    "tuple_schema_registry",
    "digest_recipe_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
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

_ROUTE_HASH = "96cbe37825fca73179b8077d7a81fb235aaf057aff405342bac18b7cf365c616"
_EXTRACTOR_HASH = "e6542bb692bb4819712bc95bfdd23c2f66f7fd21fa15e0b7881057ff81eeeccc"
_BASE_ERASURE_HASH = "b8d41d1c967252f69ea162ca2d5e2ad8fdd19a240bdc659f6dadaff86fd30033"
_FINAL_ERASURE_HASH = "87cf4253d09f722ede64de549d50ba16555cd72239970ea4479c4a85d9275ca2"
_SEMANTIC_VECTOR_HASH = "ee473f158a9e48dff845c7e498e8bd3eadde20ea68eeb698e7c3482c158182f6"
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LOWERCASE_HEX_PATTERN = re.compile(r"^(?:[0-9a-f]{2})+$")
_AUTHORITY_ROW_SCHEMA_HASH_DOMAIN = b"full-strict-v4-authority-row-schema-sha256-v1\x00"
_SQL_AUTHORITY_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_SQL_AUTHORITY_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"
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
_PLACEHOLDER_LITERAL = re.compile(
    r"^(?:x|xx|to" r"do|tbd|place" r"holder|n/?a|none|null|same|unchanged)$",
    flags=re.IGNORECASE,
)

_EDGE_RULE_TUPLE_VECTOR_HASH = "4e66b43644af5a59a3336d7b5b276e501e0488643d6b42e0372f199b9dbea4d0"
_V4_EDGE_MANIFEST_HASH = "bc50ef48b5caad56e963ebf07b29824a770665dfdb043d826cf71936cc4b9014"
_REVIEWED_CURRENT_DDL_LITERAL_HASH = (
    "6073dd69e42018d571cd3205f915ca9a8754fc4b953d92fe636a180b21e1707c"
)
_DASHBOARD_ROOT_LINK_NAMESPACE = "58a9370f-69f9-5873-b6b7-508272aee80e"

_FROZEN_COMPONENT_HASHES = {
    "semantic_step_vector_hash": _SEMANTIC_VECTOR_HASH,
    "usage_path_route_hash": _ROUTE_HASH,
    "usage_extractor_registry_hash": _EXTRACTOR_HASH,
    "baseline_erasure_schedule_hash": _BASE_ERASURE_HASH,
    "task7_erasure_schedule_hash": _FINAL_ERASURE_HASH,
}

_CURRENT_DDL_CENSUS = (
    ("deliverable_manifest_references", 0),
    ("run_consumer_receipts", 0),
    ("run_expected_consumer_edges", 1),
    ("run_applicability_decisions", 2),
    ("run_step_outputs", 3),
    ("methodology_result_claim_links", 4),
    ("methodology_result_factual_outputs", 5),
    ("methodology_extraction_results", 6),
    ("methodology_extraction_tasks", 7),
    ("methodology_coverage_records", 8),
    ("calc_sanads", 9),
    ("deterministic_calculations", 10),
    ("layer2_ic_findings", 11),
    ("layer2_ic_challenges", 12),
    ("debate_sessions", 13),
    ("evidence_trust_findings", 13),
    ("validated_evidence_packages", 13),
    ("muhasabah_records", 14),
    ("runs", 15),
    ("deliverables", 16),
    ("vector_embeddings", 16),
    ("run_output_objects", 17),
    ("run_input_bindings", 18),
    ("document_spans", 19),
    ("data_room_package_files", 20),
    ("document_parse_attempts", 20),
    ("documents", 21),
    ("document_artifacts", 22),
    ("data_room_packages", 23),
    ("source_lineage_objects", 24),
    ("run_steps", 25),
    ("methodology_result_evidence_links", 63),
    ("methodology_result_sanad_links", 63),
    ("methodology_result_defect_links", 64),
)

# Section 4.5 is the independent reviewed authority. These physical names intentionally retain
# their reviewed historical SQL prefixes even though the active contract is full-strict-v6.
_FUTURE_NATIVE_AUTHORITIES = (
    (0, "truth-dashboard-native-v1", "methodology_truth_dashboard_rows", 41),
    (1, "company-identity-native-v1", "company_identity_outputs", 42),
    (2, "provider-outcome-native-v1", "enrichment_provider_outcomes", 43),
    (3, "provider-fact-native-v1", "enrichment_normalized_facts", 44),
    (4, "graph-native-v1", "graph_projection_results", 45),
    (5, "rag-index-native-v1", "rag_index_results", 46),
    (6, "rag-query-native-v1", "rag_query_results", 47),
    (7, "conflict-plan-native-v1", "external_intelligence_conflict_plans", 48),
    (8, "conflict-execution-native-v1", "external_intelligence_conflict_executions", 49),
    (9, "conflict-finding-native-v1", "external_intelligence_conflict_findings", 50),
    (10, "vep-content-reference-native-v1", "validated_evidence_package_references", 51),
    (11, "debate-role-native-v1", "debate_role_outputs", 52),
    (12, "analysis-role-native-v1", "analysis_agent_outputs", 53),
    (13, "scorecard-native-v1", "scoring_outputs", 54),
    (14, "layer2-readiness-native-v1", "layer2_readiness_outputs", 55),
    (15, "layer2-role-native-v1", "layer2_role_outputs", 56),
    (16, "generic-extraction-native-v1", "generic_extraction_outputs", 57),
    (17, "grading-link-native-v1", "run_claim_grading_links", 58),
)

_NATIVE_PRIMARY_KEYS = {
    "methodology_truth_dashboard_rows": "dashboard_row_id",
    "company_identity_outputs": "identity_output_id",
    "enrichment_provider_outcomes": "provider_outcome_id",
    "enrichment_normalized_facts": "fact_id",
    "graph_projection_results": "graph_result_id",
    "rag_index_results": "rag_index_result_id",
    "rag_query_results": "rag_query_result_id",
    "external_intelligence_conflict_plans": "conflict_plan_id",
    "external_intelligence_conflict_executions": "conflict_execution_id",
    "external_intelligence_conflict_findings": "conflict_finding_id",
    "validated_evidence_package_references": "vep_reference_id",
    "debate_role_outputs": "debate_role_output_id",
    "analysis_agent_outputs": "analysis_output_id",
    "scoring_outputs": "scoring_output_id",
    "layer2_readiness_outputs": "readiness_output_id",
    "layer2_role_outputs": "layer2_role_output_id",
    "generic_extraction_outputs": "generic_output_id",
    "run_claim_grading_links": "grading_link_id",
}

_NATIVE_OBJECT_PREFIXES = {
    "methodology_truth_dashboard_rows": "v2_methodology_truth_dashboard_rows",
    "company_identity_outputs": "v2_company_identity_outputs",
    "enrichment_provider_outcomes": "v2_enrichment_provider_outcomes",
    "enrichment_normalized_facts": "v2_enrichment_normalized_facts",
    "graph_projection_results": "v2_graph_projection_results",
    "rag_index_results": "v2_rag_index_results",
    "rag_query_results": "v2_rag_query_results",
    "external_intelligence_conflict_plans": "v2_conflict_plans",
    "external_intelligence_conflict_executions": "v2_conflict_executions",
    "external_intelligence_conflict_findings": "v2_conflict_findings",
    "validated_evidence_package_references": "v2_vep_content_references",
    "debate_role_outputs": "v2_debate_role_outputs",
    "analysis_agent_outputs": "v2_analysis_agent_outputs",
    "scoring_outputs": "v2_scoring_outputs",
    "layer2_readiness_outputs": "v2_layer2_readiness_outputs",
    "layer2_role_outputs": "v2_layer2_role_outputs",
    "generic_extraction_outputs": "v2_generic_extraction_outputs",
    "run_claim_grading_links": "v2_run_claim_grading_links",
}

_NATIVE_OUTPUT_KINDS = {
    "methodology_truth_dashboard_rows": "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    "company_identity_outputs": "COMPANY_IDENTITY_OUTPUT",
    "enrichment_provider_outcomes": "ENRICHMENT_PROVIDER_OUTCOME",
    "enrichment_normalized_facts": "ENRICHMENT_FACT",
    "graph_projection_results": "GRAPH_PROJECTION_RESULT",
    "rag_index_results": "RAG_INDEX_RESULT",
    "rag_query_results": "RAG_QUERY_RESULT",
    "external_intelligence_conflict_plans": "CONFLICT_PLAN",
    "external_intelligence_conflict_executions": "CONFLICT_EXECUTION",
    "external_intelligence_conflict_findings": "CONFLICT_FINDING",
    "validated_evidence_package_references": "VEP_CONTENT_REFERENCE",
    "debate_role_outputs": "DEBATE_ROLE_OUTPUT",
    "analysis_agent_outputs": "ANALYSIS_AGENT_OUTPUT",
    "scoring_outputs": "SCORING_OUTPUT",
    "layer2_readiness_outputs": "LAYER2_READINESS_OUTPUT",
    "layer2_role_outputs": "LAYER2_ROLE_OUTPUT",
    "generic_extraction_outputs": "GENERIC_EXTRACTION_OUTPUT",
    "run_claim_grading_links": "RUN_CLAIM_GRADING_LINK",
}

_CURRENT_DDL_FIELDS = (
    "table_name",
    "column_tuple",
    "check_tuple",
    "foreign_key_tuple",
    "unique_tuple",
    "index_tuple",
    "rls_tuple",
    "erasure_ordinal",
    "reviewed_authority_utf8_hex",
)
_TASK7_NEW_TABLES = frozenset(
    {
        "deliverable_manifest_references",
        "document_parse_attempts",
        "run_applicability_decisions",
        "run_consumer_receipts",
        "run_expected_consumer_edges",
        "run_input_bindings",
        "run_output_objects",
        "run_step_outputs",
        "source_lineage_objects",
    }
)
_CURRENT_DDL_EXISTING_RLS_POLICIES = {
    "calc_sanads": "calc_sanads_tenant_isolation",
    "data_room_package_files": "tenant_isolation_data_room_package_files",
    "data_room_packages": "tenant_isolation_data_room_packages",
    "debate_sessions": "tenant_isolation_debate_sessions",
    "deliverables": "tenant_isolation_deliverables",
    "deterministic_calculations": "deterministic_calculations_tenant_isolation",
    "document_artifacts": "document_artifacts_tenant_isolation",
    "document_spans": "document_spans_tenant_isolation",
    "documents": "documents_tenant_isolation",
    "evidence_trust_findings": "tenant_isolation_evidence_trust_findings",
    "layer2_ic_challenges": "tenant_isolation_layer2_ic_challenges",
    "layer2_ic_findings": "tenant_isolation_layer2_ic_findings",
    "methodology_coverage_records": "methodology_coverage_records_tenant_isolation",
    "methodology_extraction_results": "methodology_extraction_results_tenant_isolation",
    "methodology_extraction_tasks": "methodology_extraction_tasks_tenant_isolation",
    "methodology_result_claim_links": "methodology_result_claim_links_tenant_isolation",
    "methodology_result_defect_links": "methodology_result_defect_links_tenant_isolation",
    "methodology_result_evidence_links": "methodology_result_evidence_links_tenant_isolation",
    "methodology_result_factual_outputs": ("methodology_result_factual_outputs_tenant_isolation"),
    "methodology_result_sanad_links": "methodology_result_sanad_links_tenant_isolation",
    "muhasabah_records": "tenant_isolation_muhasabah_records",
    "run_steps": "tenant_isolation_run_steps",
    "runs": "tenant_isolation_runs",
    "validated_evidence_packages": "tenant_isolation_validated_evidence_packages",
    "vector_embeddings": "vector_embeddings_tenant_isolation",
}
assert len(_CURRENT_DDL_EXISTING_RLS_POLICIES) == 25
_FUTURE_NATIVE_V1_FIELDS = (
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
_FUTURE_NATIVE_V2_FIELDS = (
    *_FUTURE_NATIVE_V1_FIELDS[:-1],
    "sql_authority_id_tuple",
    "schema_sha256",
)
_FUTURE_REFERENCE_V1_FIELDS = (
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
_FUTURE_REFERENCE_V2_FIELDS = (
    *_FUTURE_REFERENCE_V1_FIELDS[:-1],
    "sql_authority_id_tuple",
    "schema_sha256",
)
_FUTURE_CHILD_V1_FIELDS = (
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
_FUTURE_CHILD_V2_FIELDS = (
    *_FUTURE_CHILD_V1_FIELDS[:-1],
    "sql_authority_id_tuple",
    "schema_sha256",
)

_REGISTRY_SECTION_KEYS = {
    "tuple_schema_registry": ("rows",),
    "digest_recipe_registry": ("rows", "reviewed_literal_utf8_hex"),
    "uuid_namespace_registry": ("rows", "expected_sha256"),
    "schema_registry": ("rows", "expected_sha256"),
    "semantic_step_registry": ("rows", "expected_vector_sha256"),
    "current_ddl_registry": ("rows", "expected_sha256"),
    "baseline_parent_authority_registry": (
        "schema_version",
        "row_schema",
        "rows",
        "expected_sha256",
    ),
    "future_native_authority_registry": ("rows", "expected_sha256"),
    "future_reference_authority_registry": ("rows", "expected_sha256"),
    "future_child_authority_registry": ("rows", "expected_sha256"),
    "sql_authority_registry": ("rows", "expected_sha256"),
    "erasure_execution_registry": ("rows", "expected_sha256"),
    "provider_registry": ("rows", "expected_sha256"),
    "rights_index_policy_registry": ("rows", "expected_sha256"),
    "role_registry": ("rows", "expected_sha256"),
    "deliverable_registry": ("rows", "expected_sha256"),
    "producer_selector_registry": (
        "rows",
        "expected_sha256",
        "expected_combined_sha256",
    ),
    "consumer_selector_registry": ("rows", "expected_sha256"),
    "applicability_registry": ("rows", "expected_sha256"),
    "edge_rule_registry": ("rows", "expected_manifest_sha256"),
    "usage_route_registry": ("rows", "expected_sha256"),
    "usage_extractor_registry": ("rows", "expected_sha256"),
    "erasure_baseline": (
        "phase_count",
        "physical_table_count",
        "rows",
        "expected_sha256",
    ),
    "erasure_materialized": (
        "phase_count",
        "physical_table_count",
        "table_delta",
        "phase_delta",
        "rows",
        "expected_sha256",
    ),
}

_REGISTRY_DICT_ROW_KEYS = {
    "schema_registry": (
        "schema_id",
        "schema_version",
        "authority_locator",
        "normalized_schema_snapshot",
        "collection_paths",
        "origin",
        "expected_schema_sha256",
    ),
    "current_ddl_registry": _CURRENT_DDL_FIELDS,
    "future_native_authority_registry": _FUTURE_NATIVE_V2_FIELDS,
    "future_reference_authority_registry": _FUTURE_REFERENCE_V2_FIELDS,
    "future_child_authority_registry": _FUTURE_CHILD_V2_FIELDS,
    "erasure_execution_registry": _ERASURE_EXECUTION_FIELDS,
    "producer_selector_registry": (
        "rule_ordinal",
        "rule_id",
        "version",
        "base_selector_id",
        "instances",
        "expected_sha256",
    ),
    "consumer_selector_registry": (
        "rule_ordinal",
        "rule_id",
        "version",
        "consumer_coordinates",
        "expected_sha256",
    ),
    "applicability_registry": (
        "predicate_kind",
        "predicate_id",
        "version",
        "hash_preimage",
        "evidence_schema_id",
        "evidence_schema_version",
        "evidence_schema_sha256",
        "expected_sha256",
    ),
    "edge_rule_registry": (
        "rule_ordinal",
        "rule_id",
        "rule_version",
        "usage_kind",
        "predicate_kind",
        "predicate_id",
        "predicate_version",
        "expected_rule_sha256",
        "producer_literal_utf8_hex",
        "consumer_literal_utf8_hex",
    ),
    "usage_extractor_registry": (
        "usage_kind",
        "extractor_id",
        "extractor_version",
        "program",
        "accepted_consumers",
        "expected_accepted_consumer_sha256",
        "expected_sha256",
    ),
}

_REGISTRY_POSITIONAL_ROW_LENGTHS = {
    "tuple_schema_registry": {2},
    "digest_recipe_registry": {2},
    "uuid_namespace_registry": {3},
    "semantic_step_registry": {5},
    "baseline_parent_authority_registry": {9},
    "provider_registry": {3},
    "rights_index_policy_registry": {7},
    "role_registry": {3, 5, 8},
    "deliverable_registry": {6},
    "usage_route_registry": {12},
    "erasure_baseline": {3},
    "erasure_materialized": {3},
}

_REFERENCE_AUTHORITIES = (
    (
        0,
        "company_identity_input_references",
        "identity_reference_id",
        "identity_output_id",
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

_CHILD_AUTHORITIES = (
    (
        15,
        "company_identity_identifiers",
        "company_identity_outputs",
        "identity_output_id",
        "v2_identity_identifier",
        59,
    ),
    (
        16,
        "scoring_output_dimensions",
        "scoring_outputs",
        "scoring_output_id",
        "v2_scoring_dimension",
        60,
    ),
    (
        17,
        "generic_extraction_claim_links",
        "generic_extraction_outputs",
        "generic_output_id",
        "v2_generic_claim_link",
        61,
    ),
    (
        18,
        "generic_extraction_evidence_links",
        "generic_extraction_outputs",
        "generic_output_id",
        "v2_generic_evidence_link",
        62,
    ),
    (
        19,
        "methodology_truth_dashboard_root_links",
        "methodology_truth_dashboard_rows",
        "dashboard_row_id",
        "v3_dashboard_root_link",
        65,
    ),
)

_ERASURE_PHASE_ORDER = (
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


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required Task-7 materialization module is missing: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError(f"duplicate artifact key: {key}")
        result[key] = value
    return result


def _artifact_bytes() -> bytes:
    assert _ARTIFACT.is_file(), "reviewed preimage must exist before migration 0038"
    return _ARTIFACT.read_bytes()


def _artifact_object() -> dict[str, Any]:
    value = json.loads(
        _artifact_bytes().decode("utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
        parse_float=lambda value: (_ for _ in ()).throw(
            AssertionError(f"JSON floats are forbidden: {value}")
        ),
    )
    assert isinstance(value, dict)
    return value


def _canonical_json_bytes(value: object) -> bytes:
    """Render mutated artifact bytes without disturbing reviewed object-field order."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _canonical_row_bytes(value: object) -> bytes:
    """Canonical row payload; unlike a JSON file, a row has no trailing newline."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _authority_schema_sha256(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "schema_sha256"}
    return hashlib.sha256(
        _AUTHORITY_ROW_SCHEMA_HASH_DOMAIN + _canonical_row_bytes(payload)
    ).hexdigest()


def _validate_sql_authority_id_tuple(value: object, *, path: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(authority_id, str) or not authority_id.strip() for authority_id in value
        )
    ):
        return [f"{path} must be a non-empty literal authority-ID tuple"]
    if len(value) != len(set(value)):
        return [f"{path} repeats an authority ID"]
    return []


def _is_placeholder_literal(value: object) -> bool:
    if not isinstance(value, str):
        return False
    literal = value.strip()
    return bool(
        not literal
        or _PLACEHOLDER_LITERAL.fullmatch(literal)
        or re.search(
            r"\b(?:preserve|place"
            r"holder|to be defined|not applicable|same as|"
            r"complete revision|migration adds no)\b",
            literal,
            flags=re.IGNORECASE,
        )
    )


def _iter_strings(
    value: object,
    *,
    path: tuple[str | int, ...] = (),
) -> list[tuple[tuple[str | int, ...], str]]:
    found: list[tuple[tuple[str | int, ...], str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.append(((*path, f"<key:{key}>"), key))
            found.extend(_iter_strings(child, path=(*path, key)))
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            found.extend(_iter_strings(child, path=(*path, ordinal)))
    elif isinstance(value, str):
        found.append((path, value))
    return found


def _column_name_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        item[1]
        for item in value
        if isinstance(item, list) and len(item) == 3 and isinstance(item[1], str) and item[1]
    }


def _validate_constraint_name(
    value: object,
    *,
    path: str,
    prefixes: tuple[str, ...],
) -> list[str]:
    if not isinstance(value, str) or _is_placeholder_literal(value):
        return [f"{path} must be a literal named authority"]
    if not value.startswith(prefixes) or not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        return [f"{path} has a non-canonical authority name: {value!r}"]
    return []


def _mutate_section_header_byte(raw: bytes, section_name: str) -> bytes:
    marker = f'\n  "{section_name}": '.encode()
    assert raw.count(marker) == 1
    return raw.replace(marker, marker + b" ", 1)


def _iter_named_values(
    value: object,
    *,
    path: tuple[str | int, ...] = (),
) -> list[tuple[tuple[str | int, ...], str, object]]:
    found: list[tuple[tuple[str | int, ...], str, object]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            found.append((child_path, key, child))
            found.extend(_iter_named_values(child, path=child_path))
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            found.extend(_iter_named_values(child, path=(*path, ordinal)))
    return found


def _path_text(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)


def _require_nonempty_string_tuple(
    row: dict[str, Any],
    field: str,
    *,
    row_name: str,
) -> list[str]:
    value = row.get(field)
    if not isinstance(value, list) or not value:
        return [f"{row_name}.{field} must be a non-empty ordered tuple"]
    invalid = [item for item in value if not isinstance(item, str) or _is_placeholder_literal(item)]
    if invalid:
        return [f"{row_name}.{field} contains non-literal/empty entries: {invalid!r}"]
    return []


_CURRENT_COLUMN_LITERAL = re.compile(
    r"^[a-z][a-z0-9_]* (?:UUID|TEXT|VARCHAR\([1-9][0-9]*\)|BIGINT|INTEGER|"
    r"SMALLINT|DOUBLE PRECISION|BOOLEAN|TIMESTAMPTZ|JSONB|BYTEA|REAL|NUMERIC|DATE)"
    r"(?:\b|(?<=\)))\s+.+$"
)
_CURRENT_CHECK_LITERAL = re.compile(
    r"^(?:ck_[a-z0-9_]+|[a-z][a-z0-9_]*_check)\((?P<expression>.+)\)$"
)
_CURRENT_UNIQUE_LITERAL = re.compile(
    r"^(?:(?:pk|uq)_[a-z0-9_]+|[a-z][a-z0-9_]*(?:_pkey|_key))"
    r"\((?P<columns>[a-z][a-z0-9_]*(?:,[a-z][a-z0-9_]*)*)\)"
    r"(?: NULLS NOT DISTINCT)?"
    r"|^ux_[a-z0-9_]+\((?P<unique_columns>[a-z][a-z0-9_]*"
    r"(?:,[a-z][a-z0-9_]*)*)\)(?: WHERE (?P<unique_predicate>.+))?$"
)
_CURRENT_INDEX_LITERAL = re.compile(
    r"^(?:idx|ix|ux)_[a-z0-9_]+\((?P<columns>[a-z][a-z0-9_]*(?: (?:ASC|DESC))?"
    r"(?:,[a-z][a-z0-9_]*(?: (?:ASC|DESC))?)*)\)"
    r"(?: WHERE (?P<predicate>.+))?$"
)
_CURRENT_FK_LITERAL = re.compile(
    r"^(?:fk_[a-z0-9_]+|[a-z][a-z0-9_]*_fkey)"
    r"\((?P<child>[a-z][a-z0-9_]*(?:,[a-z][a-z0-9_]*)*)\) -> "
    r"(?P<parent_table>[a-z][a-z0-9_]*)(?:\."
    r"(?P<parent_constraint>(?:pk|uq|ux)_[a-z0-9_]+))?"
    r"\((?P<parent>[a-z][a-z0-9_]*(?:,[a-z][a-z0-9_]*)*)\) "
    r"ON UPDATE NO ACTION ON DELETE NO ACTION USING "
    r"(?P<support>(?:idx|ix|ux|uq|pk)_[a-z0-9_]+)"
    r"(?P<deferred> DEFERRABLE INITIALLY DEFERRED)?$"
)


def _validate_current_literal_grammar(row: dict[str, Any], *, path: str) -> list[str]:
    errors: list[str] = []
    for field in (
        "column_tuple",
        "check_tuple",
        "foreign_key_tuple",
        "unique_tuple",
        "index_tuple",
    ):
        errors.extend(_require_nonempty_string_tuple(row, field, row_name=path))

    for ordinal, literal in enumerate(row.get("column_tuple", ())):
        if isinstance(literal, str) and _CURRENT_COLUMN_LITERAL.fullmatch(literal) is None:
            errors.append(f"{path}.column_tuple[{ordinal}] is not canonical column DDL")
    for ordinal, literal in enumerate(row.get("check_tuple", ())):
        if not isinstance(literal, str):
            continue
        match = _CURRENT_CHECK_LITERAL.fullmatch(literal)
        if (
            match is None
            or re.search(
                r"(?:[=<>~]|\bIN\b|\bIS\b|\bAND\b|\bOR\b)",
                match.group("expression") if match else "",
            )
            is None
        ):
            errors.append(f"{path}.check_tuple[{ordinal}] is not canonical named check DDL")
    for ordinal, literal in enumerate(row.get("foreign_key_tuple", ())):
        if not isinstance(literal, str):
            continue
        match = _CURRENT_FK_LITERAL.fullmatch(literal)
        if match is None:
            errors.append(f"{path}.foreign_key_tuple[{ordinal}] is not canonical scoped FK DDL")
            continue
        if len(match.group("child").split(",")) != len(match.group("parent").split(",")):
            errors.append(f"{path}.foreign_key_tuple[{ordinal}] has an FK arity mismatch")
    for ordinal, literal in enumerate(row.get("unique_tuple", ())):
        if isinstance(literal, str) and _CURRENT_UNIQUE_LITERAL.fullmatch(literal) is None:
            errors.append(f"{path}.unique_tuple[{ordinal}] is not canonical unique DDL")
    for ordinal, literal in enumerate(row.get("index_tuple", ())):
        if isinstance(literal, str) and _CURRENT_INDEX_LITERAL.fullmatch(literal) is None:
            errors.append(f"{path}.index_tuple[{ordinal}] is not canonical index DDL")
    return errors


def _validate_ordered_column_definitions(
    value: object,
    *,
    path: str,
) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{path} must be a non-empty ordered tuple"]
    errors: list[str] = []
    ordinals: list[int] = []
    for item_ordinal, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 3:
            errors.append(f"{path}[{item_ordinal}] must be [ordinal,column,definition_sql]")
            continue
        ordinal, column, definition_sql = item
        if not isinstance(ordinal, int) or isinstance(ordinal, bool):
            errors.append(f"{path}[{item_ordinal}].ordinal must be an integer")
        else:
            ordinals.append(ordinal)
        for label, literal in (("column", column), ("definition_sql", definition_sql)):
            if not isinstance(literal, str) or _is_placeholder_literal(literal):
                errors.append(f"{path}[{item_ordinal}].{label} must be literal text")
        if isinstance(column, str) and not re.fullmatch(r"[a-z][a-z0-9_]*", column):
            errors.append(f"{path}[{item_ordinal}].column is not lower-snake-case")
        if isinstance(definition_sql, str) and not re.search(
            r"\b(?:UUID|TEXT|VARCHAR|BIGINT|INTEGER|SMALLINT|DOUBLE\s+PRECISION|"
            r"BOOLEAN|TIMESTAMPTZ|JSONB|BYTEA|REAL|NUMERIC|DATE)\b",
            definition_sql,
        ):
            errors.append(f"{path}[{item_ordinal}].definition_sql has no literal PostgreSQL type")
    if ordinals and ordinals != list(range(len(value))):
        errors.append(f"{path} ordinals must be exact 0..N-1 order: {ordinals!r}")
    return errors


def _validate_named_checks(value: object, *, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{path} must be a non-empty ordered tuple"]
    errors: list[str] = []
    for ordinal, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 2:
            errors.append(f"{path}[{ordinal}] must be [constraint_name,expression_sql]")
            continue
        name, expression_sql = item
        errors.extend(
            _validate_constraint_name(
                name,
                path=f"{path}[{ordinal}].constraint_name",
                prefixes=("ck_",),
            )
        )
        if not isinstance(expression_sql, str) or _is_placeholder_literal(expression_sql):
            errors.append(f"{path}[{ordinal}].expression_sql must be literal SQL")
        elif not re.search(r"(?:[=<>~]|\bIN\b|\bIS\b|\bAND\b|\bOR\b)", expression_sql):
            errors.append(f"{path}[{ordinal}].expression_sql is not a complete predicate")
    return errors


def _validate_unique_constraints(value: object, *, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{path} must be a non-empty ordered tuple"]
    errors: list[str] = []
    for ordinal, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 3:
            errors.append(
                f"{path}[{ordinal}] must be [constraint_name,column_tuple,nulls_not_distinct]"
            )
            continue
        name, columns, nulls_not_distinct = item
        errors.extend(
            _validate_constraint_name(
                name,
                path=f"{path}[{ordinal}].constraint_name",
                prefixes=("pk_", "uq_", "ux_"),
            )
        )
        if (
            not isinstance(columns, list)
            or not columns
            or not all(isinstance(column, str) and column.strip() for column in columns)
        ):
            errors.append(f"{path}[{ordinal}] has no complete column tuple")
        if not isinstance(nulls_not_distinct, bool):
            errors.append(f"{path}[{ordinal}] nulls_not_distinct must be boolean")
    return errors


def _validate_foreign_keys(value: object, *, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{path} must be a non-empty ordered tuple"]
    errors: list[str] = []
    for ordinal, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 8:
            errors.append(
                f"{path}[{ordinal}] must be "
                "[constraint_name,child_column_tuple,parent_table,"
                "parent_constraint_name,parent_column_tuple,on_update,on_delete,"
                "supporting_index_name]"
            )
            continue
        (
            constraint_name,
            child_columns,
            parent_table,
            parent_constraint_name,
            parent_columns,
            on_update,
            on_delete,
            supporting_index_name,
        ) = item
        for label, literal in (
            ("constraint_name", constraint_name),
            ("parent_table", parent_table),
            ("parent_constraint_name", parent_constraint_name),
            ("supporting_index_name", supporting_index_name),
        ):
            if not isinstance(literal, str) or _is_placeholder_literal(literal):
                errors.append(f"{path}[{ordinal}].{label} must be literal text")
        errors.extend(
            _validate_constraint_name(
                constraint_name,
                path=f"{path}[{ordinal}].constraint_name",
                prefixes=("fk_",),
            )
        )
        errors.extend(
            _validate_constraint_name(
                parent_constraint_name,
                path=f"{path}[{ordinal}].parent_constraint_name",
                prefixes=("pk_", "uq_", "ux_"),
            )
        )
        errors.extend(
            _validate_constraint_name(
                supporting_index_name,
                path=f"{path}[{ordinal}].supporting_index_name",
                prefixes=("ix_", "ux_", "uq_", "pk_"),
            )
        )
        if (
            not isinstance(child_columns, list)
            or not child_columns
            or not all(isinstance(column, str) and column.strip() for column in child_columns)
        ):
            errors.append(f"{path}[{ordinal}] has no complete child-column tuple")
        if (
            not isinstance(parent_columns, list)
            or not parent_columns
            or not all(isinstance(column, str) and column.strip() for column in parent_columns)
        ):
            errors.append(f"{path}[{ordinal}] has no complete parent-column tuple")
        if (
            isinstance(child_columns, list)
            and isinstance(parent_columns, list)
            and len(child_columns) != len(parent_columns)
        ):
            errors.append(
                f"{path}[{ordinal}] child/parent column arity differs: "
                f"{len(child_columns)} != {len(parent_columns)}"
            )
        if on_update != "NO ACTION" or on_delete != "NO ACTION":
            errors.append(f"{path}[{ordinal}] must pin NO ACTION update/delete")
    return errors


def _validate_rls_rows(value: object, *, path: str, table: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{path} must be a non-empty ordered tuple"]
    errors: list[str] = []
    if len(value) != 1:
        errors.append(f"{path} must contain exactly one canonical RLS authority row")
    for ordinal, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 4:
            errors.append(f"{path}[{ordinal}] must be [enable,force,policy_sql,grant_sql]")
            continue
        if not all(isinstance(part, str) and not _is_placeholder_literal(part) for part in item):
            errors.append(f"{path}[{ordinal}] contains an empty/non-literal RLS field")
            continue
        tenant_predicate = (
            "NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL "
            "AND tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid"
        )
        expected = [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY {table}_tenant_isolation ON {table} AS PERMISSIVE FOR ALL "
            f"TO PUBLIC USING ({tenant_predicate}) WITH CHECK ({tenant_predicate})",
            f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC; GRANT "
            f"SELECT,INSERT,UPDATE,DELETE ON TABLE {table} TO <configured_app_role>",
        ]
        if item != expected:
            errors.append(f"{path}[{ordinal}] differs from the canonical null-guarded RLS row")
    return errors


def _validate_current_task7_rls(value: object, *, path: str, table: str) -> list[str]:
    """Validate only the nine RLS rows migration 0038 is authorized to create."""
    tenant_predicate = (
        "NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL "
        "AND tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid"
    )
    expected = [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_tenant_isolation ON {table} AS PERMISSIVE FOR ALL "
        f"TO PUBLIC USING ({tenant_predicate}) WITH CHECK ({tenant_predicate})",
        f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC",
        f"GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE {table} TO <configured_app_role>",
    ]
    if value != expected:
        return [f"{path} differs from the reviewed migration-0038 RLS authority"]
    return []


def _validate_current_existing_rls(value: object, *, path: str, table: str) -> list[str]:
    """Existing relations retain their 0034/0036/0037 policy authority verbatim."""

    policy_name = _CURRENT_DDL_EXISTING_RLS_POLICIES[table]
    tenant_predicate = (
        "NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL "
        "AND tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid"
    )
    expected = [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {policy_name} ON {table} AS PERMISSIVE FOR ALL TO PUBLIC "
        f"USING ({tenant_predicate}) WITH CHECK ({tenant_predicate})",
    ]
    if value != expected:
        return [f"{path} must retain policy {policy_name!r} without a Task-7 privilege rewrite"]
    return []


def _validate_current_table_locality(row: dict[str, Any], *, path: str) -> list[str]:
    table = row["table_name"]
    errors: list[str] = []
    forbidden_prose = re.compile(
        r"\b(?:revision|migration|section|preserve|activation|reviewed|authority)\b|"
        r"full-strict-v[0-9]+",
        flags=re.IGNORECASE,
    )
    local_fields = (
        "column_tuple",
        "check_tuple",
        "unique_tuple",
        "index_tuple",
    )
    for field in local_fields:
        for value_path, literal in _iter_strings(row[field], path=(field,)):
            if forbidden_prose.search(literal):
                errors.append(
                    f"{path}.{_path_text(value_path)} contains prose/version text, not DDL"
                )
            for other_table, _ in _CURRENT_DDL_CENSUS:
                if other_table == table:
                    continue
                if re.search(
                    rf"(?<![a-z0-9_]){re.escape(other_table)}(?![a-z0-9_])",
                    literal,
                ):
                    errors.append(
                        f"{path}.{_path_text(value_path)} leaks unrelated table {other_table!r}"
                    )
    return errors


def _validate_parent_anchor(value: object, *, path: str) -> list[str]:
    if not isinstance(value, list) or len(value) != 4:
        return [
            f"{path} must be "
            "[child_column_tuple,parent_table,parent_constraint_name,parent_column_tuple]"
        ]
    child_columns, parent_table, parent_constraint_name, parent_columns = value
    errors: list[str] = []
    if (
        not isinstance(child_columns, list)
        or not child_columns
        or not all(isinstance(column, str) and column.strip() for column in child_columns)
    ):
        errors.append(f"{path} has no complete child-column tuple")
    if (
        not isinstance(parent_columns, list)
        or not parent_columns
        or not all(isinstance(column, str) and column.strip() for column in parent_columns)
    ):
        errors.append(f"{path} has no complete parent-column tuple")
    for label, literal in (
        ("parent_table", parent_table),
        ("parent_constraint_name", parent_constraint_name),
    ):
        if not isinstance(literal, str) or _is_placeholder_literal(literal):
            errors.append(f"{path}.{label} must be literal text")
    errors.extend(
        _validate_constraint_name(
            parent_constraint_name,
            path=f"{path}.parent_constraint_name",
            prefixes=("pk_", "uq_", "ux_"),
        )
    )
    if (
        isinstance(child_columns, list)
        and isinstance(parent_columns, list)
        and len(child_columns) != len(parent_columns)
    ):
        errors.append(f"{path} child/parent column arity differs")
    return errors


def _validate_indexes(value: object, *, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{path} must be a non-empty ordered tuple"]
    errors: list[str] = []
    for ordinal, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 4:
            errors.append(
                f"{path}[{ordinal}] must be [index_name,column_tuple,predicate_sql,unique]"
            )
            continue
        index_name, columns, predicate_sql, unique = item
        errors.extend(
            _validate_constraint_name(
                index_name,
                path=f"{path}[{ordinal}].index_name",
                prefixes=("ix_", "ux_"),
            )
        )
        if (
            not isinstance(columns, list)
            or not columns
            or not all(isinstance(column, str) and column.strip() for column in columns)
        ):
            errors.append(f"{path}[{ordinal}] has no complete column tuple")
        if predicate_sql is not None and (
            not isinstance(predicate_sql, str) or not predicate_sql.strip()
        ):
            errors.append(f"{path}[{ordinal}].predicate_sql must be text or null")
        if not isinstance(unique, bool):
            errors.append(f"{path}[{ordinal}].unique must be boolean")
    return errors


def test_declared_environment_and_normative_file_contract_are_pinned() -> None:
    project = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    dependencies = project["project"]["dependencies"]
    assert "pydantic==2.12.5" in dependencies
    assert not any(
        dependency.startswith("pydantic") and dependency != "pydantic==2.12.5"
        for dependency in dependencies
    )

    attributes = (_REPO_ROOT / ".gitattributes").read_text("utf-8").splitlines()
    assert "contracts/full_strict_v6_reviewed_preimage_v1.json text eol=lf" in attributes
    assert "contracts/full_strict_v7_reviewed_preimage_v1.json text eol=lf" in attributes
    assert _V5_ARTIFACT.is_file()
    assert _V7_ARTIFACT.is_file()
    assert _V16_ARTIFACT.is_file()
    assert _V21_ARTIFACT.is_file()
    assert _GENERATOR.is_file()
    assert _DRIFT_CHECKER.is_file()
    assert not _BLOCKLISTED_V6_FIXTURE.exists()
    lock = json.loads((_REPO_ROOT / "contracts" / "contract_lock.json").read_text("utf-8"))
    historical_artifact = "contracts/full_strict_v6_reviewed_preimage_v1.json"
    active_artifact = "contracts/full_strict_v21_reviewed_preimage_v1.json"
    assert historical_artifact not in lock["files"]
    assert "contracts/full_strict_v7_reviewed_preimage_v1.json" not in lock["files"]
    assert lock["files"][active_artifact] == hashlib.sha256(_V21_ARTIFACT.read_bytes()).hexdigest()


def test_artifact_is_utf8_lf_only_and_has_the_closed_section_order() -> None:
    raw = _artifact_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    raw.decode("utf-8")

    artifact = _artifact_object()
    assert tuple(artifact) == _SECTION_ORDER
    assert tuple(artifact["metadata"]["section_order"]) == _SECTION_ORDER
    assert artifact["metadata"]["artifact_schema"] == "full-strict-v6-reviewed-preimage-v1"
    assert artifact["metadata"]["append_frozen"] is True
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v6"
    assert artifact["metadata"]["pydantic_version"] == "2.12.5"


def test_artifact_contains_every_literal_registry_row() -> None:
    artifact = _artifact_object()
    assertions = artifact["activation_assertions"]

    assert tuple(assertions) == (
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

    assert len(artifact["semantic_step_registry"]["rows"]) == 28
    assert len(artifact["edge_rule_registry"]["rows"]) == 48
    assert len(artifact["usage_route_registry"]["rows"]) == 1_756
    assert [row[0] for row in artifact["usage_route_registry"]["rows"]] == list(range(1_756))
    assert len(artifact["usage_extractor_registry"]["rows"]) == 19
    assert len(artifact["current_ddl_registry"]["rows"]) == 34
    assert len(artifact["baseline_parent_authority_registry"]["rows"]) == 4
    assert len(artifact["future_native_authority_registry"]["rows"]) == 18
    assert len(artifact["future_reference_authority_registry"]["rows"]) == 15
    assert len(artifact["future_child_authority_registry"]["rows"]) == 5
    assert len(artifact["erasure_execution_registry"]["rows"]) == 1
    assert len(artifact["provider_registry"]["rows"]) == 18
    assert len(artifact["rights_index_policy_registry"]["rows"]) == 18
    assert [len(group) for group in artifact["role_registry"]["rows"]] == [5, 8, 3]
    assert len(artifact["deliverable_registry"]["rows"]) == 15
    assert artifact["erasure_materialized"]["physical_table_count"] == 72
    assert artifact["erasure_materialized"]["phase_count"] == 66
    assert assertions["semantic_step_count"] == 28
    assert assertions["edge_rule_count"] == 48
    assert assertions["usage_route_count"] == 1_756
    assert assertions["usage_extractor_count"] == 19
    assert assertions["erasure_current_relation_count"] == 48
    assert assertions["erasure_future_relation_count"] == 38
    assert assertions["erasure_complete_relation_count"] == 86

    rendered = json.dumps(artifact, ensure_ascii=False, separators=(",", ":"))
    for forbidden in (
        '"expand_at_runtime"',
        '"discover_from_catalog"',
        '"selector_sql"',
    ):
        if forbidden in rendered:
            pytest.fail(f"hash-time discovery remains in reviewed artifact: {forbidden}")


def test_final_lineage_and_projection_recipes_are_literal_and_hash_bound() -> None:
    artifact = _artifact_object()
    digest_recipes = dict(artifact["digest_recipe_registry"]["rows"])
    assert digest_recipes["identity_identifier_projection_sha256"] == (
        'H(["company-identity-identifier-projection-v1",tenant_id,deal_id,run_id,'
        "identity_output_id,run_output_object_id,count,"
        "ORDERED[(identifier_kind,identifier_hash,identity_identifier_id,"
        "verification_provenance_sha256)]])"
    )
    assert digest_recipes["scoring_dimension_projection_sha256"] == (
        'H(["scoring-dimension-projection-v1",tenant_id,deal_id,run_id,'
        "scoring_output_id,run_output_object_id,count,"
        "ORDERED[(dimension_ordinal,dimension_key,hex(float8send(score)),"
        "hex(float8send(confidence)),rationale_sha256,"
        "confidence_justification_sha256,support_projection_sha256,"
        "muhasabah_projection_sha256,scoring_dimension_id)]])"
    )
    assert digest_recipes["methodology_truth_dashboard_root_projection_sha256"] == (
        'H(["methodology-truth-dashboard-root-projection-v1",tenant_id,deal_id,run_id,'
        "dashboard_row_id,run_output_object_id,coverage_record_id,row_disposition,count,"
        "ORDERED[(root_kind,root_ordinal,root_output_object_id,"
        "root_output_object_kind,dashboard_root_link_id)]])"
    )

    namespace_rows = {row[0]: row[1:] for row in artifact["uuid_namespace_registry"]["rows"]}
    assert namespace_rows["dashboard_root_link_id"] == [
        _DASHBOARD_ROOT_LINK_NAMESPACE,
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


def test_registry_section_and_row_key_schemas_are_closed() -> None:
    artifact = _artifact_object()
    errors: list[str] = []

    for section, expected_keys in _REGISTRY_SECTION_KEYS.items():
        if tuple(artifact[section]) != expected_keys:
            errors.append(f"{section} keys differ from {expected_keys!r}")

    for section, expected_keys in _REGISTRY_DICT_ROW_KEYS.items():
        for ordinal, row in enumerate(artifact[section]["rows"]):
            if not isinstance(row, dict) or tuple(row) != expected_keys:
                errors.append(f"{section}.rows[{ordinal}] has undeclared or missing keys")

    for section, allowed_lengths in _REGISTRY_POSITIONAL_ROW_LENGTHS.items():
        for ordinal, row in enumerate(artifact[section]["rows"]):
            if not isinstance(row, list) or len(row) not in allowed_lengths:
                errors.append(f"{section}.rows[{ordinal}] does not match positional row census")

    for row_ordinal, row in enumerate(artifact["producer_selector_registry"]["rows"]):
        for instance_ordinal, instance in enumerate(row["instances"]):
            if tuple(instance) != (
                "ordinal",
                "parameter_tuple",
                "selector_ast",
                "expected_sha256",
            ):
                errors.append(
                    "producer_selector_registry.rows"
                    f"[{row_ordinal}].instances[{instance_ordinal}] has undeclared keys"
                )

    assert not errors, "open registry key schema:\n" + "\n".join(errors)


def test_all_strings_are_macro_free_and_utf8_hex_is_canonical_lowercase() -> None:
    artifact = _artifact_object()
    decoded_paths: set[tuple[str | int, ...]] = set()
    defects: list[str] = []

    for path, literal in _iter_strings(artifact):
        for pattern in _SYMBOLIC_MACRO_PATTERNS:
            match = pattern.search(literal)
            if match is not None:
                defects.append(
                    f"{_path_text(path)}: symbolic macro/comprehension remains in artifact "
                    f"string: {match.group(0)!r}"
                )

    for path, key, value in _iter_named_values(artifact):
        if not key.endswith("_utf8_hex"):
            continue
        path_text = _path_text(path)
        decoded_paths.add(path)
        if not isinstance(value, str):
            defects.append(f"{path_text}: encoded byte field is not a string")
            continue
        if not _LOWERCASE_HEX_PATTERN.fullmatch(value):
            defects.append(f"{path_text}: hex must be non-empty canonical lowercase byte pairs")
            continue
        try:
            decoded_bytes = bytes.fromhex(value)
            decoded = decoded_bytes.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            defects.append(f"{path_text}: cannot decode as UTF-8 hex ({exc})")
            continue
        if decoded_bytes.hex() != value:
            defects.append(f"{path_text}: hex is not a canonical lowercase round trip")
        for pattern in _SYMBOLIC_MACRO_PATTERNS:
            match = pattern.search(decoded)
            if match is not None:
                defects.append(
                    f"{path_text}: symbolic macro/comprehension remains: {match.group(0)!r}"
                )

    semantic_paths: set[tuple[str | int, ...]] = set()
    semantic_rows = artifact["semantic_step_registry"]["rows"]
    for ordinal, row in enumerate(semantic_rows):
        path = ("semantic_step_registry", "rows", ordinal, 2)
        semantic_paths.add(path)
        path_text = _path_text(path)
        if not isinstance(row, list) or len(row) != 5:
            defects.append(f"{path_text}: semantic row does not have five positional fields")
            continue
        encoded = row[2]
        if not isinstance(encoded, str) or not _LOWERCASE_HEX_PATTERN.fullmatch(encoded):
            defects.append(f"{path_text}: semantic preimage is not canonical lowercase hex")
            continue
        try:
            decoded_bytes = bytes.fromhex(encoded)
            decoded = decoded_bytes.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            defects.append(f"{path_text}: cannot decode as UTF-8 hex ({exc})")
            continue
        if decoded_bytes.hex() != encoded:
            defects.append(f"{path_text}: semantic hex is not a canonical lowercase round trip")
        for pattern in _SYMBOLIC_MACRO_PATTERNS:
            match = pattern.search(decoded)
            if match is not None:
                defects.append(
                    f"{path_text}: symbolic macro/comprehension remains: {match.group(0)!r}"
                )

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
    expected_semantic_paths = {
        ("semantic_step_registry", "rows", ordinal, 2) for ordinal in range(28)
    }
    assert decoded_paths == expected_named_paths, (
        "named reviewed-byte census differs: "
        f"missing={sorted(expected_named_paths - decoded_paths, key=str)!r}; "
        f"extra={sorted(decoded_paths - expected_named_paths, key=str)!r}"
    )
    assert semantic_paths == expected_semantic_paths
    assert len(decoded_paths | semantic_paths) == 161
    assert not defects, "non-literal or non-canonical reviewed bytes:\n" + "\n".join(defects)


def _validate_current_ddl_row(row: object, *, row_ordinal: int) -> list[str]:
    errors: list[str] = []
    path = f"current_ddl_registry.rows[{row_ordinal}]"
    if not isinstance(row, dict):
        return [f"{path} must be an object"]
    if tuple(row) != _CURRENT_DDL_FIELDS:
        errors.append(f"{path} fields differ from the reviewed current-DDL row shape")
        return errors

    table = row["table_name"]
    if not isinstance(table, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", table):
        errors.append(f"{path}.table_name must be a literal lower-snake-case name")
        table = f"invalid_table_{row_ordinal}"
    path = f"current_ddl_registry[{table}]"
    errors.extend(_validate_current_literal_grammar(row, path=path))
    if table in _TASK7_NEW_TABLES:
        errors.extend(
            _validate_current_task7_rls(
                row["rls_tuple"],
                path=f"{path}.rls_tuple",
                table=table,
            )
        )
    else:
        errors.extend(
            _validate_current_existing_rls(
                row["rls_tuple"],
                path=f"{path}.rls_tuple",
                table=table,
            )
        )
    errors.extend(_validate_current_table_locality(row, path=path))
    if not isinstance(row["erasure_ordinal"], int) or isinstance(row["erasure_ordinal"], bool):
        errors.append(f"{path}.erasure_ordinal must be an integer")

    encoded = row["reviewed_authority_utf8_hex"]
    if not isinstance(encoded, str) or not _LOWERCASE_HEX_PATTERN.fullmatch(encoded):
        errors.append(f"{path}.reviewed_authority_utf8_hex is not canonical lowercase hex")
    else:
        payload = {key: value for key, value in row.items() if key != "reviewed_authority_utf8_hex"}
        if bytes.fromhex(encoded) != _canonical_row_bytes(payload):
            errors.append(
                f"{path}.reviewed_authority_utf8_hex does not exactly encode the complete row"
            )
    return errors


def test_every_current_ddl_row_is_a_complete_literal_shape() -> None:
    artifact = _artifact_object()
    errors: list[str] = []

    rows = artifact["current_ddl_registry"]["rows"]
    census = tuple((row.get("table_name"), row.get("erasure_ordinal")) for row in rows)
    if census != _CURRENT_DDL_CENSUS:
        errors.append("current DDL table/erasure order differs from the reviewed census")
    errors.extend(
        error
        for row_ordinal, row in enumerate(rows)
        for error in _validate_current_ddl_row(row, row_ordinal=row_ordinal)
    )
    assert not errors, "incomplete current DDL rows:\n" + "\n".join(errors)


def test_historical_v4_current_ddl_rows_equal_the_independent_reviewed_literals() -> None:
    artifact = json.loads(_V4_ARTIFACT.read_text("utf-8"))
    authority = _load_module(_REVIEWED_AUTHORITY, "task7_v4_reviewed_authority_current")
    expected_rows = authority.expected_current_ddl_rows()

    assert (
        # The reviewed current-DDL literal vector is byte-identical to its v3 authority.
        authority.registry_sha256("task7-current-ddl-registry-v3", expected_rows)
        == _REVIEWED_CURRENT_DDL_LITERAL_HASH
    )
    assert artifact["current_ddl_registry"]["rows"] == expected_rows


def test_calculation_lineage_is_bound_to_the_frozen_coverage_question() -> None:
    artifact = _artifact_object()
    rows = {row["table_name"]: row for row in artifact["current_ddl_registry"]["rows"]}
    calculations = rows["deterministic_calculations"]
    coverage = rows["methodology_coverage_records"]

    assert "coverage_record_id TEXT NULL" in calculations["column_tuple"]
    assert "methodology_question_record_id UUID NULL" in calculations["column_tuple"]
    assert any(
        constraint.startswith("ck_task7_deterministic_calculations_output_shape(")
        and "METHODOLOGY_DETERMINISTIC_CALCULATION" in constraint
        and "coverage_record_id IS NOT NULL" in constraint
        and "methodology_question_record_id IS NOT NULL" in constraint
        and "producer_step_name='CALC'" in constraint
        and "coverage_record_id IS NULL" in constraint
        and "methodology_question_record_id IS NULL" in constraint
        for constraint in calculations["check_tuple"]
    )
    assert any(
        constraint.startswith("fk_task7_deterministic_calculations_coverage_question(")
        and "methodology_coverage_records.uq_task7_methodology_coverage_calculation_parent"
        in constraint
        for constraint in calculations["foreign_key_tuple"]
    )
    assert (
        "uq_task7_methodology_coverage_calculation_parent(tenant_id,deal_id,run_id,"
        "coverage_record_id,methodology_question_record_id)" in coverage["unique_tuple"]
    )
    assert (
        "ix_task7_deterministic_calculations_coverage_question(tenant_id,deal_id,run_id,"
        "coverage_record_id,methodology_question_record_id)" in calculations["index_tuple"]
    )


def test_all_future_native_rows_conform_to_future_native_authority_spec_v2() -> None:
    artifact = _artifact_object()
    tuple_specs = dict(artifact["tuple_schema_registry"]["rows"])
    # Historical V1 stays evidence; active V2 prevents valid v5 bytes failing an obsolete shape.
    assert tuple(tuple_specs["FutureNativeAuthoritySpecV1"]) == _FUTURE_NATIVE_V1_FIELDS
    assert tuple(tuple_specs["FutureNativeAuthoritySpecV2"]) == _FUTURE_NATIVE_V2_FIELDS

    rows = artifact["future_native_authority_registry"]["rows"]
    errors: list[str] = []
    if not isinstance(rows, list):
        pytest.fail("future_native_authority_registry.rows must be an ordered tuple")
    if len(rows) != 18:
        errors.append(f"future native census has {len(rows)} rows instead of 18")
    for expected_ordinal, row in enumerate(rows):
        path = f"future_native_authority_registry.rows[{expected_ordinal}]"
        if not isinstance(row, dict):
            errors.append(f"{path} must be an object")
            continue
        if expected_ordinal >= len(_FUTURE_NATIVE_AUTHORITIES):
            errors.append(f"{path} is outside the reviewed 18-row census")
            continue
        actual_census_row = (
            row.get("authority_ordinal"),
            row.get("schema_id"),
            row.get("planned_table"),
            row.get("erasure_ordinal"),
        )
        if actual_census_row != _FUTURE_NATIVE_AUTHORITIES[expected_ordinal]:
            errors.append(f"{path} differs from the reviewed authority/erasure census")
        if tuple(row) != _FUTURE_NATIVE_V2_FIELDS:
            errors.append(f"{path} does not match FutureNativeAuthoritySpecV2")
            continue
        if row["authority_ordinal"] != expected_ordinal:
            errors.append(f"{path}.authority_ordinal must equal {expected_ordinal}")
        for field in ("schema_id", "planned_table"):
            if not isinstance(row[field], str) or not row[field].strip():
                errors.append(f"{path}.{field} must be literal text")
        if row["schema_version"] != "1":
            errors.append(f"{path}.schema_version must be the literal '1'")
        table = row["planned_table"]
        expected_pk = _NATIVE_PRIMARY_KEYS.get(table)
        if row["primary_key"] != [[0, expected_pk, "UUID NOT NULL"]]:
            errors.append(f"{path}.primary_key differs from the reviewed single-column PK")
        expected_scope = [
            [0, "tenant_id", "UUID NOT NULL"],
            [1, "deal_id", "UUID NOT NULL"],
            [2, "run_id", "UUID NOT NULL"],
            [3, "run_output_object_id", "UUID NOT NULL"],
            [
                4,
                "run_output_object_class",
                "TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            ],
            [
                5,
                "run_output_object_kind",
                f"TEXT GENERATED ALWAYS AS ('{_NATIVE_OUTPUT_KINDS.get(table)}') STORED",
            ],
            [6, "lease_owner_id", "UUID NOT NULL"],
            [7, "lease_fencing_token", "BIGINT NOT NULL"],
            [8, "created_at", "TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()"],
        ]
        if row["scope_columns"] != expected_scope:
            errors.append(f"{path}.scope_columns differs from FUTURE_NATIVE_COMMON_V2")
        for field in ("primary_key", "scope_columns", "immutable_fields"):
            errors.extend(_validate_ordered_column_definitions(row[field], path=f"{path}.{field}"))
        errors.extend(_validate_named_checks(row["check_tuple"], path=f"{path}.check_tuple"))
        errors.extend(
            _validate_foreign_keys(row["reference_fields"], path=f"{path}.reference_fields")
        )
        errors.extend(
            _validate_unique_constraints(row["unique_tuple"], path=f"{path}.unique_tuple")
        )
        child_relations = row["child_relation_tuple"]
        if not isinstance(child_relations, list):
            errors.append(f"{path}.child_relation_tuple must be an ordered tuple")
        else:
            for child_ordinal, child in enumerate(child_relations):
                if (
                    not isinstance(child, list)
                    or len(child) != 2
                    or not all(isinstance(part, str) and part.strip() for part in child)
                ):
                    errors.append(
                        f"{path}.child_relation_tuple[{child_ordinal}] must be "
                        "[table_name,schema_id]"
                    )
        errors.extend(_validate_rls_rows(row["rls_tuple"], path=f"{path}.rls_tuple", table=table))
        if not isinstance(row["erasure_ordinal"], int) or isinstance(row["erasure_ordinal"], bool):
            errors.append(f"{path}.erasure_ordinal must be an integer")
        for field in ("subrole_field", "namespace_field"):
            if row[field] is not None and (
                not isinstance(row[field], str) or not row[field].strip()
            ):
                errors.append(f"{path}.{field} must be literal text or null")
        errors.extend(
            _validate_sql_authority_id_tuple(
                row["sql_authority_id_tuple"], path=f"{path}.sql_authority_id_tuple"
            )
        )
        if not isinstance(row["schema_sha256"], str) or not _HASH_PATTERN.fullmatch(
            row["schema_sha256"]
        ):
            errors.append(f"{path}.schema_sha256 must be a lowercase SHA-256")
        elif row["schema_sha256"] != _authority_schema_sha256(row):
            errors.append(f"{path}.schema_sha256 does not rederive from the complete row")

        all_columns = (
            _column_name_set(row["primary_key"])
            | _column_name_set(row["scope_columns"])
            | _column_name_set(row["immutable_fields"])
        )
        expected_column_count = (
            len(row["primary_key"]) + len(row["scope_columns"]) + len(row["immutable_fields"])
        )
        if len(all_columns) != expected_column_count:
            errors.append(f"{path} repeats a column across PK/scope/immutable tuples")
        prefix = _NATIVE_OBJECT_PREFIXES.get(table)
        expected_common_fks = {f"fk_{prefix}_run", f"fk_{prefix}_root"}
        expected_common_uniques = {
            f"uq_{prefix}_root",
            f"uq_{prefix}_scope",
            f"uq_{prefix}_reference_parent",
        }
        fk_names: set[str] = set()
        support_names: set[str] = set()
        for fk_ordinal, fk in enumerate(row["reference_fields"]):
            if not isinstance(fk, list) or len(fk) != 8:
                continue
            fk_name, child_columns, _, _, _, _, _, supporting_index = fk
            if isinstance(fk_name, str):
                fk_names.add(fk_name)
                if not fk_name.startswith("fk_v2_"):
                    errors.append(f"{path}.reference_fields[{fk_ordinal}] lost v2 SQL intent")
            if isinstance(supporting_index, str):
                support_names.add(supporting_index)
                if not supporting_index.startswith(("ix_v2_", "uq_v2_")):
                    errors.append(f"{path}.reference_fields[{fk_ordinal}] index lost v2 SQL intent")
            if isinstance(child_columns, list) and not set(child_columns) <= all_columns:
                errors.append(f"{path}.reference_fields[{fk_ordinal}] uses unknown child columns")
        if not expected_common_fks <= fk_names:
            errors.append(f"{path} omits its exact common run/root FKs")
        if len(fk_names) != len(row["reference_fields"]):
            errors.append(f"{path} has duplicate FK authority names")
        if len(support_names) != len(row["reference_fields"]):
            errors.append(f"{path} reuses a supporting-index authority")

        unique_names = {
            item[0]
            for item in row["unique_tuple"]
            if isinstance(item, list) and item and isinstance(item[0], str)
        }
        if not expected_common_uniques <= unique_names:
            errors.append(f"{path} omits its root/scope/reference-parent unique authorities")
        if any(not name.startswith(("pk_v2_", "uq_v2_", "ux_v2_")) for name in unique_names):
            errors.append(f"{path}.unique_tuple lost v2 SQL name-prefix intent")
        allowed_v4_checks = {
            "enrichment_provider_outcomes": {
                "ck_v4_enrichment_provider_outcomes_rights_index",
            },
        }
        for item in row["check_tuple"]:
            if not isinstance(item, list) or not item or not isinstance(item[0], str):
                continue
            if not item[0].startswith("ck_v2_") and item[0] not in allowed_v4_checks.get(
                table, set()
            ):
                errors.append(f"{path}.check_tuple has an unreviewed SQL name prefix")

    for missing_ordinal in range(len(rows), len(_FUTURE_NATIVE_AUTHORITIES)):
        errors.append(
            "future_native_authority_registry.rows"
            f"[{missing_ordinal}] is missing from the reviewed census"
        )
    assert not errors, "invalid FutureNativeAuthoritySpecV2 rows:\n" + "\n".join(errors)


def test_dashboard_native_row_uses_lossless_root_links_not_representative_roots() -> None:
    artifact = _artifact_object()
    dashboard = next(
        row
        for row in artifact["future_native_authority_registry"]["rows"]
        if row["planned_table"] == "methodology_truth_dashboard_rows"
    )
    immutable_names = [column[1] for column in dashboard["immutable_fields"]]
    assert "coverage_record_id" in immutable_names
    assert "root_projection_sha256" in immutable_names
    assert "row_disposition" in immutable_names
    assert "row_payload_sha256" in immutable_names
    assert not {
        "result_output_object_id",
        "evidence_output_object_id",
        "sanad_output_object_id",
        "calculation_output_object_id",
    } & set(immutable_names)
    assert [
        "methodology_truth_dashboard_root_links",
        "methodology-truth-dashboard-root-links-v1",
    ] in dashboard["child_relation_tuple"]
    reference_names = {row[0] for row in dashboard["reference_fields"]}
    assert "fk_v2_dashboard_coverage" in reference_names
    assert (
        not {
            "fk_v2_dashboard_result_root",
            "fk_v2_dashboard_evidence_root",
            "fk_v2_dashboard_sanad_root",
            "fk_v2_dashboard_calculation_root",
        }
        & reference_names
    )


def test_future_reference_rows_bind_full_constraints_and_parent_columns() -> None:
    artifact = _artifact_object()
    tuple_specs = dict(artifact["tuple_schema_registry"]["rows"])
    errors: list[str] = []
    if tuple(tuple_specs.get("FutureReferenceAuthoritySpecV1", ())) != _FUTURE_REFERENCE_V1_FIELDS:
        errors.append("tuple_schema_registry omits FutureReferenceAuthoritySpecV1")
    if tuple(tuple_specs.get("FutureReferenceAuthoritySpecV2", ())) != _FUTURE_REFERENCE_V2_FIELDS:
        errors.append("tuple_schema_registry omits FutureReferenceAuthoritySpecV2")
    rows = artifact["future_reference_authority_registry"]["rows"]
    assert len(rows) == 15
    assert tuple(
        (row.get("relation_ordinal"), row.get("table_name"), row.get("erasure_ordinal"))
        for row in rows
    ) == tuple((row[0], row[1], row[8]) for row in _REFERENCE_AUTHORITIES)
    for expected, row in zip(_REFERENCE_AUTHORITIES, rows, strict=True):
        (
            expected_ordinal,
            expected_table,
            reference_id,
            consumer_id,
            parent_table,
            parent_constraint,
            reference_kinds,
            prefix,
            _,
        ) = expected
        path = f"future_reference_authority_registry.rows[{expected_ordinal}]"
        if not isinstance(row, dict):
            errors.append(f"{path} must be an object")
            continue
        if tuple(row) != _FUTURE_REFERENCE_V2_FIELDS:
            errors.append(f"{path} fields are not the complete normalized shape")
            continue
        if row["relation_ordinal"] != expected_ordinal:
            errors.append(f"{path}.relation_ordinal must equal {expected_ordinal}")
        for field in ("table_name", "uuid_namespace"):
            if not isinstance(row[field], str) or not row[field].strip():
                errors.append(f"{path}.{field} must be literal text")
        errors.extend(
            _validate_ordered_column_definitions(row["column_tuple"], path=f"{path}.column_tuple")
        )
        errors.extend(
            _validate_parent_anchor(
                row["consumer_parent_tuple"], path=f"{path}.consumer_parent_tuple"
            )
        )
        expected_parent_columns = [
            "tenant_id",
            "deal_id",
            "run_id",
            consumer_id,
            "output_object_id"
            if expected_table == "step_receipt_input_references"
            else "run_output_object_id",
        ]
        expected_parent_anchor = [
            ["tenant_id", "deal_id", "run_id", consumer_id, "consumer_output_object_id"],
            parent_table,
            parent_constraint,
            expected_parent_columns,
        ]
        if row["consumer_parent_tuple"] != expected_parent_anchor:
            errors.append(f"{path}.consumer_parent_tuple differs from the reviewed anchor")
        if (
            not isinstance(row["reference_kind_tuple"], list)
            or not row["reference_kind_tuple"]
            or not all(
                isinstance(kind, str) and kind.strip() for kind in row["reference_kind_tuple"]
            )
        ):
            errors.append(f"{path}.reference_kind_tuple must contain literal values")
        elif tuple(row["reference_kind_tuple"]) != reference_kinds:
            errors.append(f"{path}.reference_kind_tuple differs from the reviewed literal set")
        errors.extend(_validate_named_checks(row["check_tuple"], path=f"{path}.check_tuple"))
        errors.extend(
            _validate_foreign_keys(row["foreign_key_tuple"], path=f"{path}.foreign_key_tuple")
        )
        errors.extend(
            _validate_unique_constraints(row["unique_tuple"], path=f"{path}.unique_tuple")
        )
        errors.extend(_validate_indexes(row["index_tuple"], path=f"{path}.index_tuple"))
        errors.extend(
            _validate_rls_rows(row["rls_tuple"], path=f"{path}.rls_tuple", table=expected_table)
        )
        if not isinstance(row["erasure_ordinal"], int) or isinstance(row["erasure_ordinal"], bool):
            errors.append(f"{path}.erasure_ordinal must be an integer")
        if (
            not isinstance(row["uuid_name_tuple_schema"], list)
            or not row["uuid_name_tuple_schema"]
            or not all(
                isinstance(part, str) and part.strip() for part in row["uuid_name_tuple_schema"]
            )
        ):
            errors.append(f"{path}.uuid_name_tuple_schema must be a literal tuple")
        errors.extend(
            _validate_sql_authority_id_tuple(
                row["sql_authority_id_tuple"], path=f"{path}.sql_authority_id_tuple"
            )
        )
        if not isinstance(row["schema_sha256"], str) or not _HASH_PATTERN.fullmatch(
            row["schema_sha256"]
        ):
            errors.append(f"{path}.schema_sha256 must be a lowercase SHA-256")
        elif row["schema_sha256"] != _authority_schema_sha256(row):
            errors.append(f"{path}.schema_sha256 does not rederive from the complete row")

        expected_column_names = [
            reference_id,
            "tenant_id",
            "deal_id",
            "run_id",
            consumer_id,
            "consumer_output_object_id",
            "producer_type",
            "producer_input_binding_id",
            "producer_output_object_id",
            "producer_identity_sha256",
            "producer_value_sha256",
            "reference_kind",
            "reference_ordinal",
            "lease_owner_id",
            "lease_fencing_token",
            "created_at",
        ]
        actual_columns = [
            item[1] for item in row["column_tuple"] if isinstance(item, list) and len(item) == 3
        ]
        if actual_columns != expected_column_names:
            errors.append(f"{path}.column_tuple differs from the reviewed common order")
        local_columns = set(actual_columns)
        unique_by_name = {
            item[0]: item[1]
            for item in row["unique_tuple"]
            if isinstance(item, list) and len(item) == 3
        }
        index_by_name = {
            item[0]: item[1]
            for item in row["index_tuple"]
            if isinstance(item, list) and len(item) == 4
        }
        expected_fks = {
            f"fk_{prefix}_run",
            f"fk_{prefix}_consumer",
            f"fk_{prefix}_input",
            f"fk_{prefix}_output",
        }
        expected_uniques = {
            f"pk_{prefix}",
            f"uq_{prefix}_scope",
            f"uq_{prefix}_coordinate",
            f"uq_{prefix}_ordinal",
        }
        expected_indexes = {
            f"ix_{prefix}_consumer",
            f"ix_{prefix}_input",
            f"ix_{prefix}_output",
        }
        fk_names: set[str] = set()
        consumer_fks: list[list[Any]] = []
        for fk_ordinal, fk in enumerate(row["foreign_key_tuple"]):
            if not isinstance(fk, list) or len(fk) != 8:
                continue
            fk_name, child_columns, _, _, _, _, _, support_name = fk
            if isinstance(fk_name, str):
                fk_names.add(fk_name)
                if fk_name == f"fk_{prefix}_consumer":
                    consumer_fks.append(fk)
            if isinstance(child_columns, list) and not set(child_columns) <= local_columns:
                errors.append(f"{path}.foreign_key_tuple[{fk_ordinal}] uses unknown columns")
            support_columns = index_by_name.get(support_name) or unique_by_name.get(support_name)
            if support_columns is None:
                errors.append(f"{path}.foreign_key_tuple[{fk_ordinal}] lacks its supporting index")
            elif support_columns[: len(child_columns)] != child_columns:
                errors.append(
                    f"{path}.foreign_key_tuple[{fk_ordinal}] supporting index order differs"
                )
        if fk_names != expected_fks:
            errors.append(f"{path}.foreign_key_tuple differs from the four reviewed FKs")
        if set(unique_by_name) != expected_uniques:
            errors.append(f"{path}.unique_tuple differs from the reviewed PK/unique set")
        if set(index_by_name) != expected_indexes:
            errors.append(f"{path}.index_tuple differs from the three reviewed indexes")
        expected_unique_rows = {
            f"pk_{prefix}": [reference_id],
            f"uq_{prefix}_scope": [
                "tenant_id",
                "deal_id",
                "run_id",
                reference_id,
            ],
            f"uq_{prefix}_coordinate": [
                "tenant_id",
                "run_id",
                consumer_id,
                "producer_type",
                "producer_input_binding_id",
                "producer_output_object_id",
                "reference_kind",
            ],
            f"uq_{prefix}_ordinal": [
                "tenant_id",
                "run_id",
                consumer_id,
                "reference_ordinal",
            ],
        }
        if unique_by_name != expected_unique_rows:
            errors.append(f"{path}.unique_tuple column authority differs")
        expected_index_rows = {
            f"ix_{prefix}_consumer": expected_parent_anchor[0],
            f"ix_{prefix}_input": [
                "tenant_id",
                "deal_id",
                "run_id",
                "producer_input_binding_id",
            ],
            f"ix_{prefix}_output": [
                "tenant_id",
                "deal_id",
                "run_id",
                "producer_output_object_id",
            ],
        }
        if index_by_name != expected_index_rows:
            errors.append(f"{path}.index_tuple column authority differs")
        if len(consumer_fks) != 1 or consumer_fks[0][1:5] != [
            expected_parent_anchor[0],
            expected_parent_anchor[1],
            expected_parent_anchor[2],
            expected_parent_anchor[3],
        ]:
            errors.append(f"{path} consumer parent anchor and consumer FK disagree")

    assert not errors, "invalid future reference authority rows:\n" + "\n".join(errors)


def test_future_child_rows_bind_full_constraints_and_parent_columns() -> None:
    artifact = _artifact_object()
    tuple_specs = dict(artifact["tuple_schema_registry"]["rows"])
    errors: list[str] = []
    if tuple(tuple_specs.get("FutureChildAuthoritySpecV1", ())) != _FUTURE_CHILD_V1_FIELDS:
        errors.append("tuple_schema_registry omits FutureChildAuthoritySpecV1")
    if tuple(tuple_specs.get("FutureChildAuthoritySpecV2", ())) != _FUTURE_CHILD_V2_FIELDS:
        errors.append("tuple_schema_registry omits FutureChildAuthoritySpecV2")
    rows = artifact["future_child_authority_registry"]["rows"]
    assert len(rows) == 5
    assert tuple(
        (row.get("relation_ordinal"), row.get("table_name"), row.get("erasure_ordinal"))
        for row in rows
    ) == tuple((row[0], row[1], row[5]) for row in _CHILD_AUTHORITIES)
    required_columns = {
        "company_identity_identifiers": {
            "identity_identifier_id",
            "identity_output_id",
            "identity_output_object_id",
            "identifier_kind",
            "identifier_hash",
            "verification_provenance_sha256",
            "verification_status",
        },
        "scoring_output_dimensions": {
            "scoring_dimension_id",
            "scoring_output_id",
            "scoring_output_object_id",
            "dimension_ordinal",
            "dimension_key",
            "score",
            "confidence",
            "rationale_sha256",
            "confidence_justification_sha256",
            "support_projection_sha256",
            "muhasabah_projection_sha256",
        },
        "generic_extraction_claim_links": {
            "generic_claim_link_id",
            "generic_output_id",
            "generic_output_object_id",
            "run_output_object_id",
            "run_output_object_class",
            "run_output_object_kind",
            "claim_ordinal",
            "claim_id",
            "canonical_key_sha256",
            "reused_existing_claim",
        },
        "generic_extraction_evidence_links": {
            "generic_evidence_link_id",
            "generic_output_id",
            "generic_output_object_id",
            "run_output_object_id",
            "run_output_object_class",
            "run_output_object_kind",
            "generic_claim_link_id",
            "claim_id",
            "evidence_id",
            "document_id",
            "source_span_id",
            "locator_sha256",
            "evidence_ordinal",
        },
        "methodology_truth_dashboard_root_links": {
            "dashboard_root_link_id",
            "dashboard_row_id",
            "dashboard_output_object_id",
            "root_kind",
            "root_ordinal",
            "root_output_object_id",
            "root_output_object_class",
            "root_output_object_kind",
        },
    }
    for offset, (expected, row) in enumerate(zip(_CHILD_AUTHORITIES, rows, strict=True)):
        _, expected_table, parent_table, parent_id, prefix, _ = expected
        path = f"future_child_authority_registry.rows[{offset}]"
        if not isinstance(row, dict):
            errors.append(f"{path} must be an object")
            continue
        if tuple(row) != _FUTURE_CHILD_V2_FIELDS:
            errors.append(f"{path} fields are not the complete normalized shape")
            continue
        if row["relation_ordinal"] != 15 + offset:
            errors.append(f"{path}.relation_ordinal must equal {15 + offset}")
        for field in (
            "table_name",
            "uuid_namespace",
            "projection_guard",
            "append_only_trigger",
        ):
            if not isinstance(row[field], str) or not row[field].strip():
                errors.append(f"{path}.{field} must be literal text")
        errors.extend(
            _validate_ordered_column_definitions(row["column_tuple"], path=f"{path}.column_tuple")
        )
        errors.extend(_validate_named_checks(row["check_tuple"], path=f"{path}.check_tuple"))
        errors.extend(
            _validate_foreign_keys(row["foreign_key_tuple"], path=f"{path}.foreign_key_tuple")
        )
        errors.extend(
            _validate_unique_constraints(row["unique_tuple"], path=f"{path}.unique_tuple")
        )
        errors.extend(_validate_indexes(row["index_tuple"], path=f"{path}.index_tuple"))
        errors.extend(
            _validate_rls_rows(row["rls_tuple"], path=f"{path}.rls_tuple", table=expected_table)
        )
        if not isinstance(row["erasure_ordinal"], int) or isinstance(row["erasure_ordinal"], bool):
            errors.append(f"{path}.erasure_ordinal must be an integer")
        if (
            not isinstance(row["uuid_name_tuple_schema"], list)
            or not row["uuid_name_tuple_schema"]
            or not all(
                isinstance(part, str) and part.strip() for part in row["uuid_name_tuple_schema"]
            )
        ):
            errors.append(f"{path}.uuid_name_tuple_schema must be a literal tuple")
        errors.extend(
            _validate_sql_authority_id_tuple(
                row["sql_authority_id_tuple"], path=f"{path}.sql_authority_id_tuple"
            )
        )
        if not isinstance(row["schema_sha256"], str) or not _HASH_PATTERN.fullmatch(
            row["schema_sha256"]
        ):
            errors.append(f"{path}.schema_sha256 must be a lowercase SHA-256")
        elif row["schema_sha256"] != _authority_schema_sha256(row):
            errors.append(f"{path}.schema_sha256 does not rederive from the complete row")

        local_columns = _column_name_set(row["column_tuple"])
        common_columns = {
            "tenant_id",
            "deal_id",
            "run_id",
            "lease_owner_id",
            "lease_fencing_token",
            "created_at",
        }
        if not (common_columns | required_columns[expected_table]) <= local_columns:
            errors.append(f"{path}.column_tuple omits reviewed child columns")
        unique_by_name = {
            item[0]: item[1]
            for item in row["unique_tuple"]
            if isinstance(item, list) and len(item) == 3
        }
        index_by_name = {
            item[0]: item[1]
            for item in row["index_tuple"]
            if isinstance(item, list) and len(item) == 4
        }
        fk_names: set[str] = set()
        parent_fk: list[list[Any]] = []
        for fk_ordinal, fk in enumerate(row["foreign_key_tuple"]):
            if not isinstance(fk, list) or len(fk) != 8:
                continue
            fk_name, child_columns, _, _, _, _, _, support_name = fk
            if isinstance(fk_name, str):
                fk_names.add(fk_name)
                if fk_name == f"fk_{prefix}_parent":
                    parent_fk.append(fk)
                expected_fk_prefix = "fk_v3_" if row["relation_ordinal"] == 19 else "fk_v2_"
                if not fk_name.startswith(expected_fk_prefix):
                    errors.append(
                        f"{path}.foreign_key_tuple[{fk_ordinal}] lost its reviewed SQL intent"
                    )
            if isinstance(child_columns, list) and not set(child_columns) <= local_columns:
                errors.append(f"{path}.foreign_key_tuple[{fk_ordinal}] uses unknown columns")
            support_columns = index_by_name.get(support_name) or unique_by_name.get(support_name)
            if support_columns is None:
                errors.append(f"{path}.foreign_key_tuple[{fk_ordinal}] lacks an index")
            elif support_columns[: len(child_columns)] != child_columns:
                errors.append(f"{path}.foreign_key_tuple[{fk_ordinal}] index order differs")
        required_fks = {f"fk_{prefix}_run", f"fk_{prefix}_parent"}
        if expected_table == "generic_extraction_claim_links":
            required_fks |= {f"fk_{prefix}_root", f"fk_{prefix}_claim"}
        if expected_table == "generic_extraction_evidence_links":
            required_fks |= {
                f"fk_{prefix}_root",
                f"fk_{prefix}_claim",
                f"fk_{prefix}_evidence",
                f"fk_{prefix}_span",
            }
        if expected_table == "methodology_truth_dashboard_root_links":
            required_fks |= {f"fk_{prefix}_root"}
        if not required_fks <= fk_names:
            errors.append(f"{path}.foreign_key_tuple omits reviewed child relations")
        expected_parent_child = (
            [
                "tenant_id",
                "deal_id",
                "run_id",
                "dashboard_row_id",
                "dashboard_output_object_id",
            ]
            if expected_table == "methodology_truth_dashboard_root_links"
            else [
                "tenant_id",
                "deal_id",
                "run_id",
                parent_id,
                parent_id.removesuffix("_id") + "_object_id",
            ]
        )
        expected_parent_columns = [
            "tenant_id",
            "deal_id",
            "run_id",
            parent_id,
            "run_output_object_id",
        ]
        parent_constraint = f"uq_{_NATIVE_OBJECT_PREFIXES[parent_table]}_reference_parent"
        if len(parent_fk) != 1 or parent_fk[0][1:5] != [
            expected_parent_child,
            parent_table,
            parent_constraint,
            expected_parent_columns,
        ]:
            errors.append(f"{path} parent FK differs from the reviewed parent anchor")
        if row["append_only_trigger"] != f"trg_{prefix}_append_only":
            errors.append(f"{path}.append_only_trigger lost its reviewed name")
        if expected_table == "methodology_truth_dashboard_root_links":
            if row["uuid_namespace"] != _DASHBOARD_ROOT_LINK_NAMESPACE:
                errors.append(f"{path}.uuid_namespace differs from the reviewed namespace")
            if row["uuid_name_tuple_schema"] != [
                "methodology-truth-dashboard-root-link-v1",
                "tenant_id",
                "deal_id",
                "run_id",
                "dashboard_row_id",
                "root_kind",
                "root_ordinal",
                "root_output_object_id",
            ]:
                errors.append(f"{path}.uuid_name_tuple_schema differs from the reviewed tuple")
            check_sql = " ".join(
                item[1]
                for item in row["check_tuple"]
                if isinstance(item, list) and len(item) == 2 and isinstance(item[1], str)
            )
            for required_literal in (
                "RESULT",
                "EVIDENCE",
                "SANAD",
                "CALCULATION",
                "ADVERSE_CONTRADICTION",
                "METHODOLOGY_EXTRACTION_RESULT",
                "METHODOLOGY_EVIDENCE_LINK",
                "METHODOLOGY_SANAD_LINK",
                "METHODOLOGY_CALCULATION",
                "METHODOLOGY_DEFECT_LINK",
            ):
                if required_literal not in check_sql:
                    errors.append(f"{path}.check_tuple omits {required_literal}")
            if row["sql_authority_id_tuple"] != [
                "dashboard-root-equation-v1",
                "approved-erasure-append-only-v1",
            ]:
                errors.append(f"{path}.sql_authority_id_tuple differs from reviewed ownership")
        if not isinstance(row["projection_guard"], str) or not all(
            token in row["projection_guard"].upper()
            for token in ("CREATE CONSTRAINT TRIGGER", "DEFERRABLE", "INITIALLY DEFERRED")
        ):
            errors.append(f"{path}.projection_guard is not complete deferred trigger DDL")

    assert not errors, "invalid future child authority rows:\n" + "\n".join(errors)


def test_authority_relations_and_72_table_erasure_census_are_correlated() -> None:
    artifact = _artifact_object()
    current_rows = artifact["current_ddl_registry"]["rows"]
    native_rows = artifact["future_native_authority_registry"]["rows"]
    reference_rows = artifact["future_reference_authority_registry"]["rows"]
    child_rows = artifact["future_child_authority_registry"]["rows"]

    expected_phase_tables: dict[int, list[str]] = {}
    for table, phase in _CURRENT_DDL_CENSUS:
        expected_phase_tables.setdefault(phase, []).append(table)
    for _, _, table, phase in _FUTURE_NATIVE_AUTHORITIES:
        expected_phase_tables.setdefault(phase, []).append(table)
    for ordinal, table, *_, phase in _REFERENCE_AUTHORITIES:
        assert ordinal == phase - 26
        expected_phase_tables.setdefault(phase, []).append(table)
    for ordinal, table, *_, phase in _CHILD_AUTHORITIES:
        if table == "methodology_truth_dashboard_root_links":
            assert (ordinal, phase) == (19, 65)
        else:
            assert ordinal == phase - 44
        expected_phase_tables.setdefault(phase, []).append(table)
    for tables in expected_phase_tables.values():
        tables.sort()

    actual_authority_map = {row["table_name"]: row["erasure_ordinal"] for row in current_rows}
    actual_authority_map.update(
        {row["planned_table"]: row["erasure_ordinal"] for row in native_rows}
    )
    actual_authority_map.update(
        {row["table_name"]: row["erasure_ordinal"] for row in reference_rows}
    )
    actual_authority_map.update({row["table_name"]: row["erasure_ordinal"] for row in child_rows})
    expected_authority_map = {
        table: phase for phase, tables in expected_phase_tables.items() for table in tables
    }
    assert actual_authority_map == expected_authority_map
    assert len(actual_authority_map) == 72

    final_rows = artifact["erasure_materialized"]["rows"]
    assert tuple(row[0] for row in final_rows) == tuple(range(66))
    assert tuple(row[1] for row in final_rows) == _ERASURE_PHASE_ORDER
    assert {row[1]: row[2] for row in final_rows} == expected_phase_tables

    native_by_table = {row["planned_table"]: row for row in native_rows}
    expected_children: dict[str, set[str]] = {}
    for expected in _REFERENCE_AUTHORITIES:
        parent = expected[4]
        if parent in native_by_table:
            expected_children.setdefault(parent, set()).add(expected[1])
    for expected in _CHILD_AUTHORITIES:
        expected_children.setdefault(expected[2], set()).add(expected[1])
    defects: list[str] = []
    for parent, child_tables in expected_children.items():
        materialized = {
            row[0]
            for row in native_by_table[parent]["child_relation_tuple"]
            if isinstance(row, list) and len(row) == 2
        }
        if not child_tables <= materialized:
            defects.append(
                f"{parent} omits child authorities {sorted(child_tables - materialized)!r}"
            )
    assert not defects, "native/child authority correlation defects:\n" + "\n".join(defects)


def test_semantic_preimages_and_reviewed_vectors_rederive_from_artifact() -> None:
    artifact = _artifact_object()
    semantic_rows = artifact["semantic_step_registry"]["rows"]
    for expected_order, row in enumerate(semantic_rows):
        assert row[0] == expected_order
        preimage = bytes.fromhex(row[2])
        assert hashlib.sha256(preimage).hexdigest() == row[3]

    generator = _load_module(_GENERATOR, "task7_contract_generator_vectors")
    result = generator.derive_contract_bytes(_artifact_bytes(), validate=True)
    components = result["component_hashes"]
    assert {
        component: components[component] for component in _FROZEN_COMPONENT_HASHES
    } == _FROZEN_COMPONENT_HASHES


def test_edge_rule_tuple_vector_and_manifest_use_the_v4_domain() -> None:
    artifact = _artifact_object()
    rows = artifact["edge_rule_registry"]["rows"]
    assert len(rows) == 48
    assert [row["rule_ordinal"] for row in rows] == list(range(48))
    assert {row["rule_version"] for row in rows} == {"1"}

    projection = [
        [
            row["rule_ordinal"],
            row["rule_id"],
            row["rule_version"],
            row["expected_rule_sha256"],
        ]
        for row in rows
    ]
    tuple_vector = hashlib.sha256(
        _canonical_row_bytes(["task7-edge-rule-tuple-vector-v1", projection])
    ).hexdigest()
    assert tuple_vector == _EDGE_RULE_TUPLE_VECTOR_HASH

    v4_domain = hashlib.sha256(
        _canonical_row_bytes(["expected-edge-manifest-v1", "full-strict-v4", projection])
    ).hexdigest()
    assert v4_domain == _V4_EDGE_MANIFEST_HASH
    assert artifact["edge_rule_registry"]["expected_manifest_sha256"] == v4_domain


def test_erasure_census_delta_is_exact_and_child_first() -> None:
    artifact = _artifact_object()
    baseline = artifact["erasure_baseline"]
    final = artifact["erasure_materialized"]

    assert baseline["phase_count"] == 59
    assert baseline["physical_table_count"] == 67
    assert len(baseline["rows"]) == 59
    assert final["phase_count"] == 66
    assert final["physical_table_count"] == 72
    assert len(final["rows"]) == 66
    assert final["expected_sha256"] == _FINAL_ERASURE_HASH
    assert final["table_delta"] == [
        "company_identity_identifiers",
        "scoring_output_dimensions",
        "generic_extraction_claim_links",
        "generic_extraction_evidence_links",
        "methodology_truth_dashboard_root_links",
    ]

    phase_rank = {row[1]: row[0] for row in final["rows"]}
    assert phase_rank[58] < phase_rank[64] < phase_rank[63] < phase_rank[4]
    assert phase_rank[58] < phase_rank[62] < phase_rank[61] < phase_rank[57]
    assert phase_rank[65] < phase_rank[41]
    assert phase_rank[59] < phase_rank[42]
    assert phase_rank[60] < phase_rank[54]

    final_tables = [table for row in final["rows"] for table in row[2]]
    assert len(final_tables) == len(set(final_tables)) == 72


def test_generator_is_stdlib_only_and_has_no_environment_or_runtime_imports() -> None:
    source = _GENERATOR.read_text("utf-8")
    tree = ast.parse(source, filename=str(_GENERATOR))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is not None
            imported.add(node.module.split(".")[0])
    assert imported <= {
        "__future__",  # language-level annotations import is hermetic stdlib syntax support.
        "argparse",
        "copy",
        "dataclasses",
        "hashlib",
        "importlib",
        "json",
        "pathlib",
        "re",
        "sys",
        "types",
        "typing",
        "unicodedata",
    }
    assert "import idis" not in source
    assert "import pydantic" not in source
    assert "os.environ" not in source
    assert "getenv(" not in source


def test_corrupting_any_artifact_section_changes_the_final_hash() -> None:
    generator = _load_module(_GENERATOR, "task7_contract_generator_corruption")
    raw = _artifact_bytes()
    baseline = generator.derive_contract_bytes(raw, validate=True)
    assert "FULL_STRICT_V6_CONTRACT_HASH" in baseline, (
        "the hermetic generator must emit the admitted v5 contract key"
    )
    baseline_hash = baseline["FULL_STRICT_V6_CONTRACT_HASH"]

    for section_name in _SECTION_ORDER:
        corrupted = _mutate_section_header_byte(raw, section_name)
        derived = generator.derive_contract_bytes(corrupted, validate=False)
        assert derived["FULL_STRICT_V6_CONTRACT_HASH"] != baseline_hash, section_name


def test_corrupting_sql_authority_body_changes_component_and_final_hash() -> None:
    """SQL bodies are contract bytes, not validation-only descriptive metadata."""

    artifact = _artifact_object()
    generator = _load_module(_GENERATOR, "task7_contract_generator_sql_body_corruption")
    baseline = generator.derive_contract_bytes(_artifact_bytes(), validate=True)
    corrupted = json.loads(json.dumps(artifact))
    section = corrupted.get("sql_authority_registry")
    assert isinstance(section, dict), "v5 omits sql_authority_registry"
    rows = section.get("rows")
    assert isinstance(rows, list) and rows
    bodies = rows[0].get("body_sql_utf8_hex_tuple")
    assert isinstance(bodies, list) and bodies
    encoded = bodies[0]
    assert isinstance(encoded, str) and len(encoded) >= 2
    bodies[0] = encoded[:-2] + ("00" if encoded[-2:] != "00" else "01")
    row_payload = {key: value for key, value in rows[0].items() if key != "expected_sha256"}
    rows[0]["expected_sha256"] = hashlib.sha256(
        # Registry rows use canonical JSON without the file-level trailing LF.
        _SQL_AUTHORITY_ROW_HASH_DOMAIN + _canonical_row_bytes(row_payload)
    ).hexdigest()
    section["expected_sha256"] = hashlib.sha256(
        _canonical_row_bytes([_SQL_AUTHORITY_REGISTRY_HASH_DOMAIN, rows])
    ).hexdigest()

    derived = generator.derive_contract_bytes(_canonical_json_bytes(corrupted), validate=False)
    assert (
        derived["component_hashes"]["sql_authority_registry_hash"]
        != baseline["component_hashes"]["sql_authority_registry_hash"]
    )
    assert derived["FULL_STRICT_V6_CONTRACT_HASH"] != baseline["FULL_STRICT_V6_CONTRACT_HASH"]


def test_corrupting_erasure_execution_body_changes_component_and_final_hash() -> None:
    """Active and frozen runner bytes participate in the admitted contract hash."""

    artifact = _artifact_object()
    generator = _load_module(_GENERATOR, "task7_contract_generator_erasure_body_corruption")
    baseline = generator.derive_contract_bytes(_artifact_bytes(), validate=True)
    corrupted = json.loads(json.dumps(artifact))
    section = corrupted.get("erasure_execution_registry")
    assert isinstance(section, dict)
    rows = section.get("rows")
    assert isinstance(rows, list) and len(rows) == 1
    bodies = rows[0].get("future_body_sql_utf8_hex_tuple")
    assert isinstance(bodies, list) and bodies
    encoded = bodies[0]
    assert isinstance(encoded, str) and len(encoded) >= 2
    bodies[0] = encoded[:-2] + ("00" if encoded[-2:] != "00" else "01")

    derived = generator.derive_contract_bytes(_canonical_json_bytes(corrupted), validate=False)
    assert (
        derived["component_hashes"]["erasure_execution_registry_hash"]
        != baseline["component_hashes"]["erasure_execution_registry_hash"]
    )
    assert derived["FULL_STRICT_V6_CONTRACT_HASH"] != baseline["FULL_STRICT_V6_CONTRACT_HASH"]


def test_fixture_is_exact_fresh_hermetic_derivation() -> None:
    generator = _load_module(_GENERATOR, "task7_contract_generator_fixture")
    expected = generator.derive_contract_bytes(_artifact_bytes(), validate=True)
    actual = historical_v6_fixture(_REPO_ROOT)
    assert actual == expected
    assert actual["fixture_schema"] == "full-strict-v6-contract-hash-fixture-v1"
    assert "FULL_STRICT_V2_CONTRACT_HASH" not in actual
    assert "FULL_STRICT_V4_CONTRACT_HASH" not in actual
    assert actual["artifact_sha256"] == hashlib.sha256(_artifact_bytes()).hexdigest()
    assert len(actual["section_hashes"]) == len(_SECTION_ORDER)
    assert (
        actual["FULL_STRICT_V6_CONTRACT_HASH"]
        == actual["component_hashes"]["pipeline_contract_hash"]
    )


def test_generator_runs_isolated_from_cwd_and_environment(tmp_path: Path) -> None:
    expected = historical_v6_fixture(_REPO_ROOT)
    isolated = tmp_path / "isolated"
    empty_cwd = tmp_path / "empty-cwd"
    isolated.mkdir()
    empty_cwd.mkdir()
    isolated_generator = isolated / _GENERATOR.name
    isolated_v5_helper = isolated / _V5_GENERATOR_HELPER.name
    isolated_v4_helper = isolated / _V4_GENERATOR_HELPER.name
    isolated_artifact = isolated / _ARTIFACT.name
    shutil.copyfile(_GENERATOR, isolated_generator)
    shutil.copyfile(_V5_GENERATOR_HELPER, isolated_v5_helper)
    shutil.copyfile(_V4_GENERATOR_HELPER, isolated_v4_helper)
    shutil.copyfile(_ARTIFACT, isolated_artifact)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONHASHSEED": "987654",
        "TASK7_SHOULD_NOT_BE_READ": "forbidden",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(isolated_generator),
            "--artifact",
            str(isolated_artifact),
            "--stdout",
        ],
        cwd=empty_cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == expected


def test_pydantic_snapshots_regenerate_or_fail_with_distinct_drift_code(
    tmp_path: Path,
) -> None:
    checker = _load_module(_DRIFT_CHECKER, "task7_pydantic_drift_checker")
    result = checker.check_pydantic_schema_snapshots(_ARTIFACT)
    assert result["ok"] is True
    assert result["pydantic_version"] == "2.12.5"
    assert result["checked_schema_count"] > 0

    artifact = _artifact_object()
    pydantic_rows = [
        row for row in artifact["schema_registry"]["rows"] if row["origin"]["kind"] == "pydantic"
    ]
    assert pydantic_rows
    corrupted = json.loads(json.dumps(artifact))
    target = next(
        row for row in corrupted["schema_registry"]["rows"] if row["origin"]["kind"] == "pydantic"
    )
    target["normalized_schema_snapshot"]["additionalProperties"] = True
    corrupt_path = tmp_path / "full_strict_v6_reviewed_preimage_v1.corrupt.json"
    try:
        corrupt_path.write_bytes(_canonical_json_bytes(corrupted))
        with pytest.raises(checker.PydanticSchemaDriftError) as exc_info:
            checker.check_pydantic_schema_snapshots(corrupt_path)
        assert exc_info.value.code == "TASK7_PYDANTIC_SCHEMA_DRIFT"
    finally:
        corrupt_path.unlink(missing_ok=True)
