"""Independent disposable-PostgreSQL census for Task-8 v27 authorities."""

# ruff: noqa: E501 -- frozen single-line JSON authority literals are hash-bound.

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import Any, NoReturn, Protocol, cast
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_SCHEMA = "task8-v27-postgres-authority-census-v1"
_RECEIPT_SCHEMA = "task8-v27-postgres-authority-census-receipt-v1"
_PUBLIC_ERROR_CODES = frozenset(
    {
        "TASK8_V27_CENSUS_ADMIN_BEHAVIOR_FORBIDDEN",
        "TASK8_V27_CENSUS_ADVISORY_LOCK_INVALID",
        "TASK8_V27_CENSUS_APPLICATION_CATALOG_INVALID",
        "TASK8_V27_CENSUS_APPLICATION_GRANT_INVALID",
        "TASK8_V27_CENSUS_ARTIFACT_INVALID",
        "TASK8_V27_CENSUS_ARTIFACT_ROW_INVALID",
        "TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID",
        "TASK8_V27_CENSUS_AUTHORITY_CATALOG_INVALID",
        "TASK8_V27_CENSUS_AUTHORITY_HASH_INVALID",
        "TASK8_V27_CENSUS_AUTHORITY_RESTORATION_INVALID",
        "TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID",
        "TASK8_V27_CENSUS_BEHAVIOR_ROLE_INVALID",
        "TASK8_V27_CENSUS_CASE_ID_DUPLICATE",
        "TASK8_V27_CENSUS_CASE_MANIFEST_INVALID",
        "TASK8_V27_CENSUS_CATALOG_INVALID",
        "TASK8_V27_CENSUS_CDD_TEMPLATE_INVALID",
        "TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID",
        "TASK8_V27_CENSUS_DASHBOARD_BATCH_INVALID",
        "TASK8_V27_CENSUS_DASHBOARD_IDENTITY_INVALID",
        "TASK8_V27_CENSUS_DATABASE_URL_INVALID",
        "TASK8_V27_CENSUS_ERASURE_AUTHORITY_HASH_INVALID",
        "TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID",
        "TASK8_V27_CENSUS_ERASURE_FIXTURE_INVALID",
        "TASK8_V27_CENSUS_ERASURE_FIXTURE_UNAVAILABLE",
        "TASK8_V27_CENSUS_EXECUTION_DIGEST_INVALID",
        "TASK8_V27_CENSUS_FINANCIAL_COVERAGE_ROW_COUNT_INVALID",
        "TASK8_V27_CENSUS_FIXTURE_DML_CTE_UNSUPPORTED",
        "TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED",
        "TASK8_V27_CENSUS_FIXTURE_DML_LEDGER_INVALID",
        "TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID",
        "TASK8_V27_CENSUS_FIXTURE_INVALID",
        "TASK8_V27_CENSUS_FUTURE_DDL_INVALID",
        "TASK8_V27_CENSUS_GLOBAL_CASE_INVALID",
        "TASK8_V27_CENSUS_GOVERNED_SEED_INVALID",
        "TASK8_V27_CENSUS_GUARD_ACL_INVALID",
        "TASK8_V27_CENSUS_IDENTITY_INPUT_INVALID",
        "TASK8_V27_CENSUS_INPUT_IDENTITY_INVALID",
        "TASK8_V27_CENSUS_LOCK_RACE_INVALID",
        "TASK8_V27_CENSUS_MANIFEST_INVALID",
        "TASK8_V27_CENSUS_MIGRATION_RUNNER_UNAVAILABLE",
        "TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID",
        "TASK8_V27_CENSUS_NATIVE_HELPER_MISMATCH",
        "TASK8_V27_CENSUS_NATIVE_ROOT_INVALID",
        "TASK8_V27_CENSUS_PARTIAL_TARGET_INVALID",
        "TASK8_V27_CENSUS_POLICY_INVALID",
        "TASK8_V27_CENSUS_PROJECTION_MANIFEST_INVALID",
        "TASK8_V27_CENSUS_PROJECTION_RELATION_INVALID",
        "TASK8_V27_CENSUS_PROJECTION_RELATION_MISSING",
        "TASK8_V27_CENSUS_PSYCOPG2_UNAVAILABLE",
        "TASK8_V27_CENSUS_QUESTION_SEED_INVALID",
        "TASK8_V27_CENSUS_RECEIPT_INVALID",
        "TASK8_V27_CENSUS_REJECTION_MUTATED",
        "TASK8_V27_CENSUS_REJECTION_PROJECTION_MISSING",
        "TASK8_V27_CENSUS_REJECTION_SQLSTATE_INVALID",
        "TASK8_V27_CENSUS_REQUIREMENT_AUTHORITY_CATALOG_INVALID",
        "TASK8_V27_CENSUS_REQUIREMENT_FIXTURE_SCOPE_INVALID",
        "TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID",
        "TASK8_V27_CENSUS_RLS_BEHAVIOR_INVALID",
        "TASK8_V27_CENSUS_RLS_RELATION_INVALID",
        "TASK8_V27_CENSUS_ROLE_INVALID",
        "TASK8_V27_CENSUS_ROLE_POSTURE_INVALID",
        "TASK8_V27_CENSUS_RUNTIME_GRAPH_INVALID",
        "TASK8_V27_CENSUS_SCOPE_INVALID",
        "TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID",
        "TASK8_V27_CENSUS_SOURCE_CONTRACT_HASH_DRIFT",
        "TASK8_V27_CENSUS_SOURCE_GRAPH_INVALID",
        "TASK8_V27_CENSUS_SQLALCHEMY_UNAVAILABLE",
        "TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID",
        "TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID",
        "TASK8_V27_CENSUS_UNCLASSIFIED_DML_FORBIDDEN",
        "TASK8_V27_CENSUS_UNEXPECTED",
        "TASK8_V27_CENSUS_UNEXPECTED_REJECTION",
        "TASK8_V27_DASHBOARD_REQUIREMENT_BINDING_INVALID",
    }
)
_AUTHORITY_IDS = (
    "dashboard-root-equation-v3",
    "dashboard-partial-native-root-exact-one-v1",
    "task8-current48-dashboard-erasure-refusal-v1",
)
_REFUSAL_CODE = "TASK8_DASHBOARD_ERASURE_FUTURE38_REQUIRED"
_REFUSAL_SQLSTATE = "23514"
_DASHBOARD_REJECTION_CODE = "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"
_DASHBOARD_REJECTION_SQLSTATE = "23514"
_REQUIREMENT_SCHEMA_PREEMPTION_SQLSTATE = "23514"
_REQUIREMENT_SCHEMA_PREEMPTION_CODE = "METHODOLOGY_COVERAGE_SCOPE_INVALID"
_DASHBOARD_AUTHORITY_ROW_SHA256 = "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5"
_DASHBOARD_AUTHORITY_INSTALL_SHA256 = (
    "9dc3d588a8014796b2da01ac4942dd0b2037db2f0fff6a809197ed786cdf95d1"
)
_DASHBOARD_AUTHORITY_CREATE_SHA256 = (
    "6433d7c462b90804977dd5999d79a816a7386750e00748f0121dbfa75d088619"
)
_DASHBOARD_AUTHORITY_SEMANTIC_BODY_SHA256 = (
    "509789807246a51eb4f449e67548ec229dd928d3fac554925d6fa13a8ef713b7"
)
_PARTIAL_EXACT_ONE_CODE = "STEP_OUTPUT_MISSING"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CDD_TEMPLATE_SHA256 = "57cfec98436514cae717e359fa872504a7b9e1115c7bc12a4561e0989c4281fc"
_CDD_TEMPLATE_BYTE_COUNT = 32529
_FIXTURE_DML_RE = re.compile(
    r"(?:^|;)\s*(?:INSERT\s+INTO|UPDATE\s+|DELETE\s+FROM|MERGE\s+INTO|TRUNCATE\s+)",
    flags=re.IGNORECASE,
)
_AUTHORITY_EXECUTION_SCHEMA = "task8-v27-hash-bound-authority-execution-v1"
_ACL_FUNCTION_HASH_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_REGISTRY_HASH_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"
_ERASURE_OWNER = "idis_erasure_function_owner"
_GUARD_OWNER = "idis_task7_guard_function_owner"
_EXACT_DASHBOARD_ERASURE_ACL = (
    "GRANT SELECT (tenant_id,deal_id) ON TABLE "
    "public.methodology_truth_dashboard_rows TO idis_erasure_function_owner"
)
PIPELINE_CONTRACT_VERSION = "full-strict-v27"
FULL_STRICT_V27_CONTRACT_HASH = "5221e622a40c44b4f2fe38e53e0208a7e1dba5358278d75624a0cc1c40ef0985"
_ARTIFACT_SHA256 = "4169371675ce32c2b4b459528dcdf21914887941d952648597bce59e49a8ac06"
_FIXTURE_SHA256 = "78a3aedb04260cc56e98a2a9193ce489ca73bd4b5b95fc1486f676bfbd07b0cf"
_GRAPH_SHA256 = "a951b96575f5c2927c59185f20378fde6ce019c207b77510a1033f4947649e3c"
_CONSERVATION_SHA256 = "32a06ca41841c196ae76ad3d33b3d15da4dec8a247343b9a629390da8128475d"
_POLICY_BINDING_SHA256 = "9e41ea12908a5c37542f6156d4bbf22dc19d59ac172d096a84fc55c4db83d2ff"
_APPLICATION_RELATION_TUPLE = tuple(
    json.loads(
        r"""["audit_events","break_glass_grants","byok_policies","calc_sanads","claims","data_room_package_files","data_room_packages","deal_assignments","deals","debate_sessions","defects","deliverable_manifest_references","deliverables","deterministic_calculations","document_artifacts","document_parse_attempts","document_spans","documents","enrichment_credentials","enrichment_rights_approvals","erasure_requests","evidence_items","evidence_trust_findings","group_memberships","groups","human_gate_actions","human_gates","idempotency_records","layer2_ic_challenges","layer2_ic_findings","legal_holds","methodology_coverage_records","methodology_extraction_attempts","methodology_extraction_results","methodology_extraction_task_spans","methodology_extraction_tasks","methodology_questions","methodology_result_claim_links","methodology_result_defect_links","methodology_result_evidence_links","methodology_result_factual_outputs","methodology_result_sanad_links","methodology_truth_dashboard_root_links","methodology_truth_dashboard_rows","methodology_versions","muhasabah_records","overrides","provider_budget_policies","provider_budget_usage","run_applicability_decisions","run_consumer_receipts","run_expected_consumer_edges","run_input_bindings","run_methodology_selections","run_output_objects","run_step_outputs","run_steps","runs","sanads","source_lineage_objects","strict_preflight_proofs","tenants","validated_evidence_packages","vector_embeddings","webhook_delivery_attempts","webhooks"]"""
    )
)
_EXCLUDED_APPLICATION_RELATION_TUPLE = ("alembic_version",)
_PHYSICAL_PUBLIC_RELATION_TUPLE = tuple(
    sorted((*_APPLICATION_RELATION_TUPLE, *_EXCLUDED_APPLICATION_RELATION_TUPLE))
)
_APPLICATION_RELATION_TUPLE_SHA256 = (
    "e06330cdf5eabf5e1f95da4e66537de60476460ff9af6c0ebd7fd920a5ae6c39"
)
_PHYSICAL_PUBLIC_RELATION_TUPLE_SHA256 = (
    "85f9d8162c0241f88cf560e83689ce1ea7baff6c0de9a6cdddda512ac4df27f6"
)
_APPLICATION_RELATION_HASH_DOMAIN = (
    "task8-v27-post-0038-plus-scaffold-public-ordinary-table-inventory-v1"
)
_PHYSICAL_PUBLIC_RELATION_HASH_DOMAIN = (
    "task8-v27-post-0038-plus-scaffold-public-ordinary-table-physical-catalog-v1"
)
_SQL_AUTHORITY_ROW_SHA256 = {
    "dashboard-root-equation-v3": (
        "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5"
    ),
    "dashboard-partial-native-root-exact-one-v1": (
        "076eb51508606bacc9ff1e457ff9e1264ce25ab338b595ba4ee038dab9a1a9f4"
    ),
}
_GUARD_FUNCTION_ROW_SHA256 = {
    "public.idis_task8_dashboard_root_equation_v3()": (
        "914d7c1c91269dcbd284b4d2590d501d567e04d9ca0e3cac28519430d98be005"
    ),
    "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)": (
        "c249c283ff9f07d7e6d8a9191a3b656b440573580e4119ff3de7036ca1211f53"
    ),
    "public.idis_task7_current_output_exact_one_v1()": (
        "9baff99a336cd2f852b8bbeaad04bba34fc3a4e45bbed80b5f2da290cb0587d7"
    ),
}
_ERASURE_EXECUTION_ROW_SHA256 = "6aa073ca47937660f0e2efa077f71c82889b779d0ac8d215d085d45a7bed59f9"
_ERASURE_EXECUTION_REGISTRY_SHA256 = (
    "dd02924e999cf1503edaf1924ad7a006fdcd72a2eba8b1dec32142853a79dacc"
)
_TASK5_SELECTION_GUARD_UPGRADE_SHA256 = (
    "564ea8d682639b79cf12ef4f4e96b634deffc9facf3f4a354354079c5975ee47"
)
_TASK5_SELECTION_GUARD_BODY_SHA256 = (
    "56083eb83dadb9d1ea7855af2f0f964ae01222f623e70bf548498d598ac1a763"
)
_PARENT_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_POLICY_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
_REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
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
_METHODOLOGY_RESULT_NAMESPACE = uuid.UUID("671535ce-d8bb-586d-a7c2-4eac6691c52d")
_METHODOLOGY_FACTUAL_RECORD_NAMESPACE = uuid.UUID("6c0ed11d-42cf-5746-aada-f54543224172")
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
_SEED_WRITE_RELATIONS = tuple(
    json.loads(
        r"""["audit_events","calc_sanads","claims","data_room_package_files","data_room_packages","deals","defects","deterministic_calculations","document_artifacts","document_parse_attempts","document_spans","documents","erasure_requests","evidence_items","methodology_coverage_records","methodology_extraction_attempts","methodology_extraction_results","methodology_extraction_task_spans","methodology_extraction_tasks","methodology_questions","methodology_result_claim_links","methodology_result_defect_links","methodology_result_evidence_links","methodology_result_factual_outputs","methodology_result_sanad_links","methodology_truth_dashboard_root_links","methodology_truth_dashboard_rows","methodology_versions","run_methodology_selections","run_output_objects","run_step_outputs","run_steps","runs","sanads","source_lineage_objects","tenants"]"""
    )
)
_SEED_FK_CLOSURE_RELATIONS = tuple(
    json.loads(
        r"""["audit_events","calc_sanads","claims","data_room_package_files","data_room_packages","deals","defects","deterministic_calculations","document_artifacts","document_parse_attempts","document_spans","documents","erasure_requests","evidence_items","methodology_coverage_records","methodology_extraction_attempts","methodology_extraction_results","methodology_extraction_task_spans","methodology_extraction_tasks","methodology_questions","methodology_result_claim_links","methodology_result_defect_links","methodology_result_evidence_links","methodology_result_factual_outputs","methodology_result_sanad_links","methodology_truth_dashboard_root_links","methodology_truth_dashboard_rows","methodology_versions","run_methodology_selections","run_output_objects","run_step_outputs","run_steps","runs","sanads","source_lineage_objects","strict_preflight_proofs","tenants"]"""
    )
)
_EXPECTED_SEED_FK_ROWS = tuple(
    (
        row[0],
        row[1],
        row[2],
        tuple(row[3]),
        row[4],
        row[5],
        tuple(row[6]),
        row[7],
        row[8],
        row[9],
        row[10],
        row[11],
        row[12],
    )
    for row in json.loads(
        r"""[["public","calc_sanads","calc_sanads_calc_id_fkey",["calc_id"],"public","deterministic_calculations",["calc_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","calc_sanads","calc_sanads_tenant_id_fkey",["tenant_id"],"public","tenants",["tenant_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","calc_sanads","fk_task7_calc_sanads_calculation",["tenant_id","deal_id","run_id","calc_id"],"public","deterministic_calculations",["tenant_id","deal_id","run_id","calc_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","claims","claims_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","data_room_package_files","fk_task7_data_room_package_files_source_root",["tenant_id","deal_id","source_corpus_receipt_id","source_lineage_object_id","source_lineage_object_kind"],"public","source_lineage_objects",["tenant_id","deal_id","corpus_receipt_id","source_lineage_object_id","object_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","data_room_package_files","fk_task7_package_files_document",["tenant_id","deal_id","doc_id","document_id"],"public","documents",["tenant_id","deal_id","doc_id","document_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","data_room_package_files","fk_task7_package_files_package",["tenant_id","deal_id","package_id"],"public","data_room_packages",["tenant_id","deal_id","package_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","data_room_packages","data_room_packages_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","data_room_packages","fk_task7_data_room_packages_source_root",["tenant_id","deal_id","source_corpus_receipt_id","source_lineage_object_id","source_lineage_object_kind"],"public","source_lineage_objects",["tenant_id","deal_id","corpus_receipt_id","source_lineage_object_id","object_kind"],true,true,true,"NO ACTION","NO ACTION","SIMPLE"],["public","defects","defects_claim_id_fkey",["claim_id"],"public","claims",["claim_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","defects","defects_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","defects","fk_defects_task6_claim_scope",["tenant_id","deal_id","claim_id"],"public","claims",["tenant_id","deal_id","claim_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","deterministic_calculations","deterministic_calculations_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","deterministic_calculations","deterministic_calculations_tenant_id_fkey",["tenant_id"],"public","tenants",["tenant_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","deterministic_calculations","fk_task7_deterministic_calculations_coverage_question",["tenant_id","deal_id","run_id","coverage_record_id","methodology_question_record_id"],"public","methodology_coverage_records",["tenant_id","deal_id","run_id","coverage_record_id","methodology_question_record_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","deterministic_calculations","fk_task7_deterministic_calculations_output_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","deterministic_calculations","fk_task7_deterministic_calculations_run",["tenant_id","deal_id","run_id"],"public","runs",["tenant_id","deal_id","run_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","document_artifacts","document_artifacts_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","document_artifacts","fk_task7_document_artifacts_source_root",["tenant_id","deal_id","source_corpus_receipt_id","source_lineage_object_id","source_lineage_object_kind"],"public","source_lineage_objects",["tenant_id","deal_id","corpus_receipt_id","source_lineage_object_id","object_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","document_parse_attempts","fk_task7_document_parse_attempts_source_root",["tenant_id","deal_id","source_corpus_receipt_id","source_lineage_object_id","source_lineage_object_kind"],"public","source_lineage_objects",["tenant_id","deal_id","corpus_receipt_id","source_lineage_object_id","object_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","document_parse_attempts","fk_task7_parse_attempts_document",["tenant_id","deal_id","document_id"],"public","documents",["tenant_id","deal_id","document_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","document_spans","document_spans_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","document_spans","fk_task7_document_spans_source_root",["tenant_id","deal_id","source_corpus_receipt_id","source_lineage_object_id","source_lineage_object_kind"],"public","source_lineage_objects",["tenant_id","deal_id","corpus_receipt_id","source_lineage_object_id","object_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","document_spans","fk_task7_spans_deal",["tenant_id","deal_id"],"public","deals",["tenant_id","deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","document_spans","fk_task7_spans_document",["tenant_id","deal_id","document_id"],"public","documents",["tenant_id","deal_id","document_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","document_spans","fk_task7_spans_parse_attempt",["tenant_id","deal_id","document_id","parse_attempt_id"],"public","document_parse_attempts",["tenant_id","deal_id","document_id","parse_attempt_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","documents","documents_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","documents","documents_doc_id_fkey",["doc_id"],"public","document_artifacts",["doc_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","documents","fk_task7_documents_artifact",["tenant_id","deal_id","doc_id"],"public","document_artifacts",["tenant_id","deal_id","doc_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","documents","fk_task7_documents_source_root",["tenant_id","deal_id","source_corpus_receipt_id","source_lineage_object_id","source_lineage_object_kind"],"public","source_lineage_objects",["tenant_id","deal_id","corpus_receipt_id","source_lineage_object_id","object_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","evidence_items","evidence_items_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","evidence_items","fk_evidence_items_task6_claim_scope",["tenant_id","deal_id","claim_id"],"public","claims",["tenant_id","deal_id","claim_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_coverage_records","fk_methodology_coverage_question",["tenant_id","methodology_version_record_id","methodology_version_id","methodology_question_record_id","methodology_question_id","methodology_type"],"public","methodology_questions",["tenant_id","methodology_version_record_id","methodology_version_id","methodology_question_record_id","methodology_question_id","methodology_type"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_coverage_records","fk_methodology_coverage_selection_scope",["tenant_id","deal_id","run_id","selection_id"],"public","run_methodology_selections",["tenant_id","deal_id","run_id","selection_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_coverage_records","fk_task7_methodology_coverage_records_output_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_attempts","fk_methodology_extraction_attempts_step",["tenant_id","run_id","run_step_id"],"public","run_steps",["tenant_id","run_id","step_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_attempts","fk_methodology_extraction_attempts_task_input",["tenant_id","deal_id","run_id","task_record_id","task_input_sha256"],"public","methodology_extraction_tasks",["tenant_id","deal_id","run_id","task_record_id","task_input_sha256"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_results","fk_methodology_extraction_results_attempt_task_input",["tenant_id","deal_id","run_id","attempt_id","task_record_id","task_input_sha256"],"public","methodology_extraction_attempts",["tenant_id","deal_id","run_id","attempt_id","task_record_id","task_input_sha256"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_results","fk_methodology_extraction_results_task_input",["tenant_id","deal_id","run_id","task_record_id","task_input_sha256"],"public","methodology_extraction_tasks",["tenant_id","deal_id","run_id","task_record_id","task_input_sha256"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_results","fk_task7_methodology_extraction_results_output_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_task_spans","fk_methodology_extraction_task_spans_span",["tenant_id","deal_id","document_id","span_id"],"public","document_spans",["tenant_id","deal_id","document_id","span_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_task_spans","fk_methodology_extraction_task_spans_task_document",["tenant_id","deal_id","run_id","task_record_id","document_id"],"public","methodology_extraction_tasks",["tenant_id","deal_id","run_id","task_record_id","document_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_tasks","fk_methodology_extraction_tasks_coverage",["tenant_id","deal_id","run_id","selection_id","coverage_record_id","methodology_version_record_id","methodology_question_record_id"],"public","methodology_coverage_records",["tenant_id","deal_id","run_id","selection_id","coverage_record_id","methodology_version_record_id","methodology_question_record_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_tasks","fk_methodology_extraction_tasks_document",["tenant_id","deal_id","document_id"],"public","documents",["tenant_id","deal_id","document_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_tasks","fk_methodology_extraction_tasks_question",["tenant_id","methodology_version_record_id","methodology_version_id","methodology_question_record_id","methodology_question_id","methodology_type"],"public","methodology_questions",["tenant_id","methodology_version_record_id","methodology_version_id","methodology_question_record_id","methodology_question_id","methodology_type"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_tasks","fk_methodology_extraction_tasks_selection",["tenant_id","deal_id","run_id","selection_id"],"public","run_methodology_selections",["tenant_id","deal_id","run_id","selection_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_extraction_tasks","fk_task7_methodology_extraction_tasks_output_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_questions","fk_methodology_questions_version",["tenant_id","methodology_version_record_id","methodology_version_id","methodology_id","methodology_type","source_sha256"],"public","methodology_versions",["tenant_id","methodology_version_record_id","methodology_version_id","methodology_id","methodology_type","source_sha256"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_claim_links","fk_methodology_result_claim_links_claim",["tenant_id","deal_id","claim_id"],"public","claims",["tenant_id","deal_id","claim_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_claim_links","fk_methodology_result_claim_links_coverage",["tenant_id","deal_id","run_id","selection_id","coverage_record_id","methodology_version_record_id","methodology_question_record_id"],"public","methodology_coverage_records",["tenant_id","deal_id","run_id","selection_id","coverage_record_id","methodology_version_record_id","methodology_question_record_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_claim_links","fk_methodology_result_claim_links_output",["tenant_id","deal_id","run_id","result_id","factual_output_record_id"],"public","methodology_result_factual_outputs",["tenant_id","deal_id","run_id","result_id","factual_output_record_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_claim_links","fk_task7_methodology_result_claim_links_output_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_defect_links","fk_methodology_result_defect_links_defect",["tenant_id","deal_id","defect_id"],"public","defects",["tenant_id","deal_id","defect_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_defect_links","fk_methodology_result_defect_links_output",["tenant_id","deal_id","run_id","result_id","factual_output_record_id"],"public","methodology_result_factual_outputs",["tenant_id","deal_id","run_id","result_id","factual_output_record_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_defect_links","fk_methodology_result_defect_links_sanad_link",["tenant_id","deal_id","run_id","result_id","factual_output_record_id","sanad_link_id"],"public","methodology_result_sanad_links",["tenant_id","deal_id","run_id","result_id","factual_output_record_id","sanad_link_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_defect_links","fk_task7_methodology_result_defect_links_output_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_evidence_links","fk_methodology_result_evidence_links_claim_link",["tenant_id","deal_id","run_id","result_id","factual_output_record_id","claim_link_id"],"public","methodology_result_claim_links",["tenant_id","deal_id","run_id","result_id","factual_output_record_id","claim_link_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_evidence_links","fk_methodology_result_evidence_links_evidence",["tenant_id","deal_id","evidence_id"],"public","evidence_items",["tenant_id","deal_id","evidence_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_evidence_links","fk_methodology_result_evidence_links_output",["tenant_id","deal_id","run_id","result_id","factual_output_record_id"],"public","methodology_result_factual_outputs",["tenant_id","deal_id","run_id","result_id","factual_output_record_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_evidence_links","fk_methodology_result_evidence_links_span",["tenant_id","deal_id","document_id","source_span_id"],"public","document_spans",["tenant_id","deal_id","document_id","span_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_evidence_links","fk_task7_methodology_result_evidence_links_output_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_factual_outputs","fk_methodology_result_factual_outputs_result",["tenant_id","deal_id","run_id","result_id","task_record_id"],"public","methodology_extraction_results",["tenant_id","deal_id","run_id","result_id","task_record_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_factual_outputs","fk_task7_methodology_result_factual_outputs_output_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_sanad_links","fk_methodology_result_sanad_links_claim_link",["tenant_id","deal_id","run_id","result_id","factual_output_record_id","claim_link_id"],"public","methodology_result_claim_links",["tenant_id","deal_id","run_id","result_id","factual_output_record_id","claim_link_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_sanad_links","fk_methodology_result_sanad_links_evidence",["tenant_id","deal_id","primary_evidence_id"],"public","evidence_items",["tenant_id","deal_id","evidence_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_sanad_links","fk_methodology_result_sanad_links_output",["tenant_id","deal_id","run_id","result_id","factual_output_record_id"],"public","methodology_result_factual_outputs",["tenant_id","deal_id","run_id","result_id","factual_output_record_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_sanad_links","fk_methodology_result_sanad_links_sanad",["tenant_id","deal_id","sanad_id"],"public","sanads",["tenant_id","deal_id","sanad_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_result_sanad_links","fk_task7_methodology_result_sanad_links_output_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_truth_dashboard_root_links","fk_v3_dashboard_root_link_parent",["tenant_id","deal_id","run_id","dashboard_row_id","dashboard_output_object_id"],"public","methodology_truth_dashboard_rows",["tenant_id","deal_id","run_id","dashboard_row_id","run_output_object_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_truth_dashboard_root_links","fk_v3_dashboard_root_link_root",["tenant_id","deal_id","run_id","root_output_object_id","root_output_object_class","root_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_truth_dashboard_root_links","fk_v3_dashboard_root_link_run",["tenant_id","deal_id","run_id"],"public","runs",["tenant_id","deal_id","run_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_truth_dashboard_rows","fk_v2_dashboard_coverage",["tenant_id","deal_id","run_id","coverage_record_id"],"public","methodology_coverage_records",["tenant_id","deal_id","run_id","coverage_record_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_truth_dashboard_rows","fk_v2_methodology_truth_dashboard_rows_root",["tenant_id","deal_id","run_id","run_output_object_id","run_output_object_class","run_output_object_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_class","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_truth_dashboard_rows","fk_v2_methodology_truth_dashboard_rows_run",["tenant_id","deal_id","run_id"],"public","runs",["tenant_id","deal_id","run_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","methodology_versions","methodology_versions_tenant_id_fkey",["tenant_id"],"public","tenants",["tenant_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","run_methodology_selections","fk_run_methodology_selections_cdd_version",["tenant_id","commercial_dd_version_record_id","commercial_methodology_version_id","commercial_version_hash"],"public","methodology_versions",["tenant_id","methodology_version_record_id","methodology_version_id","version_hash"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","run_methodology_selections","fk_run_methodology_selections_fdd_version",["tenant_id","financial_dd_version_record_id","financial_methodology_version_id","financial_version_hash"],"public","methodology_versions",["tenant_id","methodology_version_record_id","methodology_version_id","version_hash"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","run_methodology_selections","fk_run_methodology_selections_run_scope",["tenant_id","deal_id","run_id"],"public","runs",["tenant_id","deal_id","run_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","run_output_objects","fk_task7_run_output_objects_run",["tenant_id","deal_id","run_id"],"public","runs",["tenant_id","deal_id","run_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","run_step_outputs","fk_task7_run_step_outputs_output",["tenant_id","deal_id","run_id","output_object_id","output_kind"],"public","run_output_objects",["tenant_id","deal_id","run_id","output_object_id","output_kind"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","run_step_outputs","fk_task7_run_step_outputs_step",["tenant_id","run_id","step_id","step_name"],"public","run_steps",["tenant_id","run_id","step_id","step_name"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","run_steps","fk_task7_run_steps_run",["tenant_id","run_id"],"public","runs",["tenant_id","run_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","run_steps","run_steps_run_id_fkey",["run_id"],"public","runs",["run_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","runs","fk_runs_strict_preflight_proof_tenant",["tenant_id","strict_preflight_proof_id"],"public","strict_preflight_proofs",["tenant_id","proof_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","runs","fk_task7_runs_deal",["tenant_id","deal_id"],"public","deals",["tenant_id","deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","runs","runs_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","sanads","fk_sanads_task6_claim_scope",["tenant_id","deal_id","claim_id"],"public","claims",["tenant_id","deal_id","claim_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","sanads","fk_sanads_task6_primary_evidence_scope",["tenant_id","deal_id","primary_evidence_id"],"public","evidence_items",["tenant_id","deal_id","evidence_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","sanads","sanads_claim_id_fkey",["claim_id"],"public","claims",["claim_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","sanads","sanads_deal_id_fkey",["deal_id"],"public","deals",["deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"],["public","source_lineage_objects","fk_task7_source_lineage_objects_corpus",["tenant_id","deal_id","corpus_receipt_id"],"public","data_room_packages",["tenant_id","deal_id","package_id"],true,true,true,"NO ACTION","NO ACTION","SIMPLE"],["public","source_lineage_objects","fk_task7_source_lineage_objects_deal",["tenant_id","deal_id"],"public","deals",["tenant_id","deal_id"],true,false,false,"NO ACTION","NO ACTION","SIMPLE"]]"""
    )
)
_MIGRATION_LF_SHA256_ROWS = tuple(
    tuple(row)
    for row in json.loads(
        r"""[["src/idis/persistence/migrations/versions/0001_postgres_foundation.py","3af10629f84c0f7fbcea0ccd5914d4e6876eb16fceb91f5e6bf70ae3445ea795"],["src/idis/persistence/migrations/versions/0002_rls_nullif_empty_tenant.py","8d1677c1862a8d5a58b2e98d4157f10d3ed979a6c6263d5c44326707bc979478"],["src/idis/persistence/migrations/versions/0003_webhooks_foundation.py","13c9a09774d43a9b2e6542aa818a50c186d71b6b92cf2ed3375c1b1aadff362d"],["src/idis/persistence/migrations/versions/0004_ingestion_gate_storage.py","3a95f2e217dfe3f82648981bdf3fc51b6f6b5cb23c6d242cd5cbb0b7cf43b937"],["src/idis/persistence/migrations/versions/0005_deterministic_calculations_and_calc_sanads.py","11e917aa2f56c071a715f655bce026ed7cc325ad16c4be369c30b000d703234a"],["src/idis/persistence/migrations/versions/0006_deals_extended_schema.py","71e74fbaf8b3dd4fea2e6cb3f03483534adab043d30dba44b23e468ab5238a86"],["src/idis/persistence/migrations/versions/0007_claims_sanad_defects_tables.py","2cf192298a732b55313ecf7d7b3734dbaa962df57aaec4b61acd342a1d7cf4f3"],["src/idis/persistence/migrations/versions/0008_add_primary_span_id_to_claims.py","791330fd21a1cffc474400bbc120e00dca461aecbe74d67239b87c8b7282b665"],["src/idis/persistence/migrations/versions/0009_runs_debate_deliverables_human_gates_overrides.py","a8ad09f8bfc6f2c5ef95e3cc2a3e1692234e4104d5ec8e971b7377903dad013f"],["src/idis/persistence/migrations/versions/0010_run_steps_evidence_items.py","e41e8b7635b7e04c6b5e2621162ecd24b4f390115507ba3b5e58379159486250"],["src/idis/persistence/migrations/versions/0011_enrichment_credentials.py","f27a1a3500112dc8171ff3468b5e97cf76b4362f8905b2a9dc154f183827a7e6"],["src/idis/persistence/migrations/versions/0012_document_spans_deal_content_hash.py","c02fc2d7056f8fc595376bd5926c10f8df91a646cf62d0fe90220b342c0705f6"],["src/idis/persistence/migrations/versions/0013_runs_source_contract.py","8b1fef918a1e4f9ffa4351c0d12288c98eadb545685a258cd66f220f47c50a22"],["src/idis/persistence/migrations/versions/0014_run_step_name_width.py","ec1581f2000a5a868df7c58fecac577cac6e78e332fe41e1de4ea7a670c12780"],["src/idis/persistence/migrations/versions/0015_defects_workflow_columns.py","61060e7806c02d759e5b37e1a2f345080dbbf91e2e6b889afe514a8288a4de20"],["src/idis/persistence/migrations/versions/0016_allow_image_document_type.py","3985f690e3ec03171d46a5050cb75b426b3397ee33e126b7c03b9f13d29c9f28"],["src/idis/persistence/migrations/versions/0017_vector_embeddings.py","fd4b824b380cef63c84aa7a6abebde5ff5a51e2a6a75b398dff24e557878824d"],["src/idis/persistence/migrations/versions/0018_runs_cancel_state.py","e01db8deb5b24e6ad27335aaa0c51fc22a5e32a2f4ddfb4650dfdadcdb2a3f33"],["src/idis/persistence/migrations/versions/0019_runs_created_by_actor.py","8f83d3fe7623bfa80256846378ff7e81b15e64b8a0a11e39d21701ea01b7e5a5"],["src/idis/persistence/migrations/versions/0020_data_room_packages_and_files.py","b408c04ad346cf6d64351ae6039f35981023f65b60c430aee3b125e410934063"],["src/idis/persistence/migrations/versions/0021_layer1_evidence_durability.py","84c081969906772f4b55b307775f98abd4f0146f32e78fac6dd60772756b98de"],["src/idis/persistence/migrations/versions/0022_layer2_ic_challenge_durability.py","babd88f271e5a6f7206ab02ba46ccc1db4d93f8f82c7de5f94ea2e7de6bdc705"],["src/idis/persistence/migrations/versions/0023_runs_one_active_run_per_deal.py","322626bfa6d265c8bc53dc1b196d2434b782972f8899dafb875000a67a3644b8"],["src/idis/persistence/migrations/versions/0024_provider_budget_usage.py","b5cef6a9b8b09585ddf7c61a1047653380ed3672dc5125968fcd6a4f1612b402"],["src/idis/persistence/migrations/versions/0025_webhook_outbox_enqueue_idempotency.py","7d2366408707f15d08d30579e3343b074f4309bec1ad41342d0c576874f712e5"],["src/idis/persistence/migrations/versions/0026_abac_deal_assignments.py","7bb44c97b7b2b0322008fe7c2b727b5fa11a87eb4cab27512919222ca0bf7731"],["src/idis/persistence/migrations/versions/0027_tenants_data_region.py","a0f8c577dc04b88785266f5a92d02848cc3c16c6299bcf5f92f2d0620ec7af30"],["src/idis/persistence/migrations/versions/0028_break_glass_grants.py","9552b0077a86cb007ee081d916ce68026cbd76ea8360dbcc924eca60ab8a1cc1"],["src/idis/persistence/migrations/versions/0029_byok_and_legal_holds.py","e847664e1ecbab2ca8ef2d5f4f980df63f486ff59297e5c486db2e4ee765d3f7"],["src/idis/persistence/migrations/versions/0030_erasure_requests.py","02ff13548c97eb2ac5fd64e1fd5dae6fb7747bc65f2dfd158ae839f57f1f2327"],["src/idis/persistence/migrations/versions/0031_strict_preflight_proofs.py","cf3925a24956e3d2178aff665aabec03e6d23516f644d645d8f407fd5e79cce0"],["src/idis/persistence/migrations/versions/0032_worker_run_leases.py","fc4fe6c8dc1e53c6cf3c1c333e897b194eb1b84c4d6b7fc530c3ed44af14260f"],["src/idis/persistence/migrations/versions/0033_durable_dependency_governance.py","4e16da49cf2eca50d28268756b545a7dddd06a561a84db975c05ffc4c752621d"],["src/idis/persistence/migrations/versions/0034_normalize_tenant_rls.py","823d99ead19bd7b929d16f04a02f1a37f8e62e3f8833555f727827c89e4011bc"],["src/idis/persistence/migrations/versions/0035_allow_native_text_document_types.py","4a38125a4d8a2a671dec0d04b7089e590adca9cb70719ad9a3ec7225ebd33c02"],["src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py","b3f69b79b6ffb1a827900670d50f4d2dc3813ef0e40a81604f70b6c756ba8fb5"],["src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py","41faa2cfc3f37ae008aea39d6ad7cd1ce46013266cd747990b84e6b403271794"],["src/idis/persistence/migrations/versions/0038_full_strict_v16_receipts_and_edges.py","37ba0627fc9bccc13dc332e1540a74f09d9fb0ef557bc1336e88e6765c3ca4ce"]]"""
    )
)
_SEED_WRITE_RELATIONS_SHA256 = "0ef6453642b8f475ba4b724da27af34a1cf68387e959e00fb01625fa8c58b79b"
_SEED_FK_CLOSURE_RELATIONS_SHA256 = (
    "dfbf294032fb6ffa4ec99d51996c4863a34dd9976c15c2a19be1f417974c475c"
)
_EXPECTED_SEED_FK_ROWS_SHA256 = "655a8654badf793301af4cc08683d22daa5517b5718e540d7d6ad11db494c7c3"
_MIGRATION_LF_SHA256_ROWS_SHA256 = (
    "67d6048ed049b328e0fe08e53f3faed8056ef91f0644af33eb493a32c6ae9813"
)
_MIGRATION_EXECUTION_SOURCE_ROWS = (
    ("env.py", "754fcfba47d490a7539c452f414fcb617606acd43b060ea11f1ec050dac52985"),
    ("script.py.mako", "b6938938463b075669b553f454fa82c860f3afc540c53a6d4c17a227b012ca0a"),
    *tuple((f"versions/{Path(path).name}", sha256) for path, sha256 in _MIGRATION_LF_SHA256_ROWS),
)
_MIGRATION_EXECUTION_INTERNAL_MODULE_ROWS = (
    (
        "idis.persistence.db",
        "src/idis/persistence/db.py",
        "9e359cb8a69b370ae31c6ef313ac80ffd6f288adb83e17fdd2a3fc71ae9227eb",
    ),
    (
        "idis.models.run_step",
        "src/idis/models/run_step.py",
        "19f6eb21d96f121c6ab9bd8c68413dabec31005bdeef0240c24772a15fcc7715",
    ),
)
_MIGRATION_EXECUTION_INTERNAL_IMPORT_EDGES = (
    ("env.py", "idis.persistence.db"),
    ("versions/0037_durable_methodology_extraction.py", "idis.models.run_step"),
)
_MIGRATION_EXECUTION_SOURCE_MANIFEST_SHA256 = (
    "1b8aa20b7684bb259545b282c9f70a12107bfa15981f54116246a6102c4fb243"
)
_TASK5_QUESTION_IDENTITY_FIELDS = (
    "tenant_id",
    "methodology_question_record_id",
    "methodology_version_record_id",
    "methodology_version_id",
    "methodology_question_id",
    "methodology_id",
    "methodology_type",
    "question_ordinal",
    "section",
    "source_type",
    "source_name",
    "source_sheet_or_section",
    "source_row_number",
    "source_anchor_cell",
    "source_sha256",
    "semantic_row_hash",
    "question_payload_hash",
    "full_question_payload_sha256",
    "required_calculations_json",
)
_TASK5_REGISTRY_IDENTITY_FIELDS = (
    "schema_version",
    "tenant_id",
    "methodology_version_record_id",
    "methodology_version_id",
    "methodology_id",
    "methodology_type",
    "version_label",
    "source_type",
    "source_alias",
    "source_sha256",
    "source_byte_count",
    "source_contract_hash",
    "source_trace_sha256",
    "registry_sha256",
    "version_sha256",
    "question_count",
    "source_section_count",
    "header_row",
    "status",
    "timestamp_redaction_field_tuple",
    "deterministic_registry_bytes_sha256",
    "methodology_question_payload_hashes",
    "question_identity_field_tuple",
    "question_identity_tuple",
    "question_identity_tuple_sha256",
    "required_calculation_tuple",
    "required_calculation_tuple_sha256",
)
_TASK5_SELECTION_IDENTITY_FIELDS = (
    "selection_id",
    "tenant_id",
    "deal_id",
    "run_id",
    "commercial_dd_version_record_id",
    "financial_dd_version_record_id",
    "selected_version_ids",
    "selected_source_hashes",
    "selected_registry_hashes",
    "selected_version_hashes",
    "selection_hash",
    "selected_question_count",
    "state",
    "immutable",
)


class Task8V27CensusError(RuntimeError):
    """Raised with a stable safe code when the census cannot be trusted."""


def _fail(code: str) -> NoReturn:
    raise Task8V27CensusError(code)


@contextmanager
def _suppress_bytecode_writes() -> Iterator[None]:
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = previous


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


def _fk_row_json(row: Sequence[object]) -> list[object]:
    if len(row) != 13:
        _fail("TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID")
    return [
        row[0],
        row[1],
        row[2],
        list(row[3]),
        row[4],
        row[5],
        list(row[6]),
        *row[7:],
    ]


def validate_census_input_bytes(
    *,
    artifact_raw: bytes,
    fixture_raw: bytes,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recheck the exact independently admitted byte identities at DB entry."""

    inputs = (
        (artifact_raw, _ARTIFACT_SHA256),
        (fixture_raw, _FIXTURE_SHA256),
        (parent_artifact_raw, _PARENT_ARTIFACT_SHA256),
        (policy_artifact_raw, _POLICY_ARTIFACT_SHA256),
        (reviewer_map_raw, _REVIEWER_MAP_SHA256),
    )
    if any(
        not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != expected
        for raw, expected in inputs
    ):
        _fail("TASK8_V27_CENSUS_INPUT_IDENTITY_INVALID")
    try:
        artifact = json.loads(artifact_raw.decode("utf-8"))
        fixture = json.loads(fixture_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_INPUT_IDENTITY_INVALID") from exc
    if not isinstance(artifact, dict) or not isinstance(fixture, dict):
        _fail("TASK8_V27_CENSUS_INPUT_IDENTITY_INVALID")
    _validate_v27(artifact)
    try:
        valid = (
            fixture["artifact_schema"] == "full-strict-v27-contract-hash-fixture-v1"
            and fixture["pipeline_contract_version"] == PIPELINE_CONTRACT_VERSION
            and fixture["activation_status"] == "INACTIVE"
            and fixture["artifact_sha256"] == _ARTIFACT_SHA256
            and fixture["FULL_STRICT_V27_CONTRACT_HASH"] == FULL_STRICT_V27_CONTRACT_HASH
            and fixture["identity_hash_dependency_graph"]["graph_sha256"] == _GRAPH_SHA256
            and fixture["approved_semantics_conservation"]["sha256"] == _CONSERVATION_SHA256
            and fixture["policy_binding"]["binding_sha256"] == _POLICY_BINDING_SHA256
        )
    except (KeyError, TypeError):
        valid = False
    if not valid:
        _fail("TASK8_V27_CENSUS_INPUT_IDENTITY_INVALID")
    return artifact, fixture


COMPLETE_EXECUTION_SHA256_FIELDS = (
    "case_result_sha256",
    "catalog_projection_sha256",
    "race_result_sha256",
    "rls_behavior_sha256",
    "partial_authority_udu_sha256",
    "erasure_authority_udu_sha256",
    "dashboard_seed_plan_sha256",
    "dashboard_seed_db_projection_sha256",
    "task5_roundtrip_sha256",
    "seed_fk_closure_sha256",
    "seed_native_helper_equality_sha256",
    "source_freeze_sha256",
    "main_dashboard_census_sha256",
    "requirement_matrix_sha256",
    "requirement_predicate_sha256",
)


def complete_execution_sha256(execution: Mapping[str, object]) -> str:
    """Derive the one shared complete-execution digest preimage."""

    if tuple(execution) != COMPLETE_EXECUTION_SHA256_FIELDS:
        _fail("TASK8_V27_CENSUS_EXECUTION_DIGEST_INVALID")
    try:
        pairs = [[field, execution[field]] for field in COMPLETE_EXECUTION_SHA256_FIELDS]
    except KeyError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_EXECUTION_DIGEST_INVALID") from exc
    if any(
        not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None for _field, value in pairs
    ):
        _fail("TASK8_V27_CENSUS_EXECUTION_DIGEST_INVALID")
    return _canonical_sha256(["task8-v27-census-complete-execution-v2", pairs])


def _complete_execution_evidence(execution: Mapping[str, object]) -> dict[str, object]:
    """Project the exact reviewed evidence keyset from the wider safe receipt."""

    try:
        return {field: execution[field] for field in COMPLETE_EXECUTION_SHA256_FIELDS}
    except KeyError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_EXECUTION_DIGEST_INVALID") from exc


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
        raise Task8V27CensusError("TASK8_V27_CENSUS_SCOPE_INVALID") from exc
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
        "schema_version": "task8-v27-census-native-output-manifest-v1",
        "output_kind_tuple": output_kinds,
        "producer_step_tuple": [
            [output_kind, _NATIVE_OUTPUT_STEP_BY_KIND[output_kind]] for output_kind in output_kinds
        ],
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def _dashboard_authority_binding(v27: Mapping[str, Any]) -> dict[str, object]:
    """Bind the reviewed installation tuple and its executable semantic body."""

    try:
        authority = next(
            row
            for row in v27["sql_authority_registry"]["rows"]
            if row.get("authority_id") == "dashboard-root-equation-v3"
        )
        statements = tuple(
            bytes.fromhex(value).decode("utf-8") for value in authority["body_sql_utf8_hex_tuple"]
        )
    except (KeyError, StopIteration, TypeError, UnicodeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_DASHBOARD_REQUIREMENT_BINDING_INVALID") from exc
    install_sql = "\n".join(statements)
    create_statements = tuple(
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v3()")
    )
    if len(create_statements) != 1:
        _fail("TASK8_V27_DASHBOARD_REQUIREMENT_BINDING_INVALID")
    create_statement = create_statements[0]
    marker = " AS $$"
    marker_index = create_statement.find(marker)
    body_end = create_statement.rfind("$$")
    if marker_index < 0 or body_end <= marker_index + len(marker):
        _fail("TASK8_V27_DASHBOARD_REQUIREMENT_BINDING_INVALID")
    semantic_body = create_statement[marker_index + len(marker) : body_end]
    binding = {
        "authority_row_sha256": authority.get("expected_sha256"),
        "install_tuple_sha256": hashlib.sha256(install_sql.encode("utf-8")).hexdigest(),
        "install_tuple_utf8_bytes": len(install_sql.encode("utf-8")),
        "create_function_sha256": hashlib.sha256(create_statement.encode("utf-8")).hexdigest(),
        "create_function_utf8_bytes": len(create_statement.encode("utf-8")),
        "semantic_body_sha256": hashlib.sha256(semantic_body.encode("utf-8")).hexdigest(),
        "semantic_body_utf8_bytes": len(semantic_body.encode("utf-8")),
    }
    if (
        binding["authority_row_sha256"] != _DASHBOARD_AUTHORITY_ROW_SHA256
        or binding["install_tuple_sha256"] != _DASHBOARD_AUTHORITY_INSTALL_SHA256
        or binding["install_tuple_utf8_bytes"] != 26016
        or binding["create_function_sha256"] != _DASHBOARD_AUTHORITY_CREATE_SHA256
        or binding["create_function_utf8_bytes"] != 25367
        or binding["semantic_body_sha256"] != _DASHBOARD_AUTHORITY_SEMANTIC_BODY_SHA256
        or binding["semantic_body_utf8_bytes"] != 25212
    ):
        _fail("TASK8_V27_DASHBOARD_REQUIREMENT_BINDING_INVALID")
    return binding


def _dashboard_authority_sql(v27: Mapping[str, Any]) -> str:
    """Return the exact reviewed five-statement installation tuple."""

    _dashboard_authority_binding(v27)
    authority = next(
        row
        for row in v27["sql_authority_registry"]["rows"]
        if row.get("authority_id") == "dashboard-root-equation-v3"
    )
    return "\n".join(
        bytes.fromhex(value).decode("utf-8") for value in authority["body_sql_utf8_hex_tuple"]
    )


def validate_v27_dashboard_requirement_binding(
    v27: Mapping[str, Any],
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
        raise Task8V27CensusError("TASK8_V27_DASHBOARD_REQUIREMENT_BINDING_INVALID") from exc
    expected_keys = {"formula_ordinal", "formula_type", "required", "scope"}
    dashboard_sql = _dashboard_authority_sql(v27)
    if (
        len(questions) != 79
        or len(requirements) != 10
        or any(set(item) != expected_keys for item in requirements)
        or any(item["required"] is not True for item in requirements)
        or dashboard_sql.count("item->>'calc_type'") != 3
        or "item->>'formula_type'" not in dashboard_sql
        or "item->>'formula_ordinal'" not in dashboard_sql
    ):
        _fail("TASK8_V27_DASHBOARD_REQUIREMENT_BINDING_INVALID")


def derive_v27_dashboard_identity(**inputs: Any) -> dict[str, object]:
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
        _fail("TASK8_V27_CENSUS_IDENTITY_INPUT_INVALID")
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
        ["task8-v27-postgres-census-fixture-v1", label, *coordinates],
    )


def _fixture_hash(label: str, *coordinates: object) -> str:
    """Derive a deterministic non-placeholder hash for negative-only fixtures."""

    return _canonical_sha256(["task8-v27-postgres-census-fixture-value-v1", label, *coordinates])


_fixture_uuid_ordinal = 0
_fixture_dml_role_evidence: list[dict[str, object]] = []
_APPLICATION_FIXTURE_DML = "APPLICATION_FIXTURE"
_APPLICATION_BEHAVIOR_DML = "APPLICATION_BEHAVIOR"
_ERASURE_BEHAVIOR_DML = "ERASURE_BEHAVIOR"
_CENSUS_DML_OPERATION_SEQUENCE_SHA256 = (
    "f36eecb83fdfdb61a5fe91c9b4ece935550fd5aa795400dc66450531e0118e42"
)
_FROZEN_REQUIREMENT_CASE_ID_TUPLE = (
    "LEGACY_FOUR_FIELD",
    "POLICY_V2_TWO_FIELD",
    "UNKNOWN_FORMULA_TYPE",
    "ORDINAL_DRIFT",
    "SCOPE_DRIFT",
    "UNDECLARED_FDD_CALCULATION_ROOT",
    "MIXED_SHAPES",
    "POLICY_V2_EXTRA_FIELD",
    "POLICY_V2_MISSING_FIELD",
    "LEGACY_EXTRA_FIELD",
    "LEGACY_MISSING_FIELD",
    "CROSS_VERSION_JOIN",
    "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE",
)
_FROZEN_BEHAVIOR_DML_CASE_TUPLE = (
    ("dashboard-gap-all-not-found", "APPLICATION", "COMMIT", True, False, False, False),
    ("dashboard-mixed-answered", "APPLICATION", "COMMIT", True, False, False, False),
    ("dashboard-mixed-red-flag", "APPLICATION", "COMMIT", True, False, False, False),
    ("dashboard-all-found-answered", "APPLICATION", "COMMIT", True, False, False, False),
    (
        "dashboard-required-formula-missing",
        "APPLICATION",
        "ROLLBACK",
        True,
        False,
        False,
        False,
    ),
    (
        "dashboard-gap-with-found-result",
        "APPLICATION",
        "ROLLBACK",
        True,
        False,
        False,
        False,
    ),
    (
        "dashboard-answered-without-evidence",
        "APPLICATION",
        "ROLLBACK",
        True,
        False,
        False,
        False,
    ),
    (
        "dashboard-red-flag-without-defect",
        "APPLICATION",
        "ROLLBACK",
        True,
        False,
        False,
        False,
    ),
    ("dashboard-global-missing-row", "APPLICATION", "ROLLBACK", True, False, False, False),
    (
        "dashboard-global-source-count-99",
        "APPLICATION",
        "ROLLBACK",
        True,
        False,
        False,
        False,
    ),
    (
        "dashboard-global-extra-noncoverage",
        "APPLICATION",
        "ROLLBACK",
        True,
        False,
        False,
        False,
    ),
    (
        "dashboard-global-duplicate-coverage",
        "APPLICATION",
        "ROLLBACK",
        True,
        False,
        False,
        False,
    ),
    ("dashboard-direct-insert-bypass", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-direct-update", "APPLICATION", "ROLLBACK", True, False, False, True),
    ("dashboard-direct-delete", "APPLICATION", "ROLLBACK", True, False, False, True),
    (
        "dashboard-root-link-direct-update",
        "APPLICATION",
        "ROLLBACK",
        True,
        False,
        False,
        True,
    ),
    (
        "dashboard-root-link-direct-delete",
        "APPLICATION",
        "ROLLBACK",
        True,
        False,
        False,
        True,
    ),
    ("partial-root-exactly-one", "APPLICATION", "COMMIT", True, False, False, False),
    ("partial-root-missing", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("partial-root-duplicate", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("partial-root-direct-sql", "APPLICATION", "ROLLBACK", True, False, False, False),
    (
        "erasure-current48-without-dashboard",
        "ERASURE",
        "COMMIT",
        False,
        False,
        True,
        False,
    ),
    (
        "erasure-dashboard-refusal",
        "ERASURE",
        "ROLLBACK",
        True,
        True,
        True,
        False,
    ),
    (
        "erasure-application-execute-denied",
        "APPLICATION",
        "ROLLBACK",
        False,
        False,
        True,
        False,
    ),
    (
        "erasure-executor-direct-refusal",
        "ERASURE",
        "ROLLBACK",
        True,
        True,
        True,
        False,
    ),
)


def _reset_fixture_uuid_sequence() -> None:
    global _fixture_uuid_ordinal
    _fixture_uuid_ordinal = 0
    _fixture_dml_role_evidence.clear()


def _record_fixture_dml_role(
    *,
    case_coordinate: str,
    dml_classification: str,
    operation: str,
    posture: Mapping[str, object],
    expected_application_role: str,
    tenant_id: object,
    session_tenant_id: object,
    transaction_outcome: str = "COMMIT",
    dml_statement_tuple: Sequence[str] = (),
) -> None:
    """Record one closed, tenant-scoped fixture-DML transaction."""

    if (
        re.fullmatch(r"[A-Z][A-Z0-9_:-]{0,127}", operation) is None
        or dml_classification
        not in {
            _APPLICATION_FIXTURE_DML,
            _APPLICATION_BEHAVIOR_DML,
            _ERASURE_BEHAVIOR_DML,
        }
        or transaction_outcome not in {"COMMIT", "ROLLBACK"}
    ):
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID")
    _case_scope_identity(case_coordinate)
    posture_sha256 = _connection_posture_sha256(posture)
    current_user = posture.get("current_user")
    session_user = posture.get("session_user")
    replication_role = posture.get("session_replication_role")
    try:
        normalized_tenant_id = str(uuid.UUID(str(tenant_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID") from exc
    try:
        normalized_session_tenant_id = (
            "" if session_tenant_id == "" else str(uuid.UUID(str(session_tenant_id)))
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID") from exc
    normalized_statements = tuple(
        _normalized_fixture_dml_statement(statement) for statement in dml_statement_tuple
    )
    if (
        not normalized_statements
        or current_user != expected_application_role
        or session_user != expected_application_role
        or replication_role != "origin"
        or any(statement is None for statement in normalized_statements)
    ):
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID")
    exact_statements = tuple(str(statement) for statement in normalized_statements)
    statement_hashes = tuple(
        hashlib.sha256(statement.encode("utf-8")).hexdigest() for statement in exact_statements
    )
    _fixture_dml_role_evidence.append(
        {
            "ledger_ordinal": len(_fixture_dml_role_evidence) + 1,
            "case_coordinate": case_coordinate,
            "dml_classification": dml_classification,
            "operation": operation,
            "expected_role": expected_application_role,
            "current_user": current_user,
            "session_user": session_user,
            "tenant_id": normalized_tenant_id,
            "session_tenant_id": normalized_session_tenant_id,
            "session_replication_role": replication_role,
            "transaction_outcome": transaction_outcome,
            "dml_statement_sha256_tuple": list(statement_hashes),
            "dml_statement_count": len(exact_statements),
            "dml_statement_tuple_sha256": _canonical_sha256(
                [
                    "task8-v27-fixture-dml-statement-sha256-tuple-v1",
                    statement_hashes,
                ]
            ),
            "posture_sha256": posture_sha256,
        }
    )


def _fixture_dml_role_rows() -> tuple[dict[str, object], ...]:
    """Return immutable copies of the safe in-process fixture-DML ledger."""

    return tuple(dict(row) for row in _fixture_dml_role_evidence)


def _case_scope_identity(case_coordinate: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Derive one frozen case scope directly from its literal coordinate."""

    if (
        re.fullmatch(
            r"(?:MAIN|RLS|RACE_FORWARD|RACE_REVERSE|REQUIREMENT_[0-9]{2}|BEHAVIOR_[0-9]{2})",
            case_coordinate,
        )
        is None
    ):
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_LEDGER_INVALID")
    return tuple(
        uuid.UUID(_fixture_uuid("case-scope", case_coordinate, label))
        for label in ("tenant", "deal", "run")
    )  # type: ignore[return-value]


def _expected_census_dml_operation_sequence() -> tuple[tuple[str, ...], ...]:
    """Return the independently frozen case/operation/class/role/outcome plan."""

    rows: list[tuple[str, ...]] = []

    def add(
        coordinate: str,
        classification: str,
        operation: str,
        role_class: str,
        outcome: str,
    ) -> None:
        rows.append((coordinate, classification, operation, role_class, outcome))

    def add_scope(coordinate: str) -> None:
        add(coordinate, _APPLICATION_FIXTURE_DML, "SCOPE_SEED:0001", "APPLICATION", "COMMIT")

    def add_graph(coordinate: str) -> None:
        add(coordinate, _APPLICATION_FIXTURE_DML, "SOURCE_GRAPH:0001", "APPLICATION", "COMMIT")
        add(coordinate, _APPLICATION_FIXTURE_DML, "SOURCE_GRAPH:0002", "APPLICATION", "COMMIT")
        add(coordinate, _APPLICATION_FIXTURE_DML, "RUNTIME_GRAPH:0001", "APPLICATION", "COMMIT")

    add_scope("MAIN")
    add_graph("MAIN")
    add("MAIN", _APPLICATION_BEHAVIOR_DML, "MAIN_DASHBOARD:0001", "APPLICATION", "COMMIT")
    for ordinal, case_id in enumerate(_FROZEN_REQUIREMENT_CASE_ID_TUPLE, start=1):
        coordinate = f"REQUIREMENT_{ordinal:02d}"
        add_scope(coordinate)
        add_graph(coordinate)
        if case_id == "UNDECLARED_FDD_CALCULATION_ROOT":
            add(
                coordinate,
                _APPLICATION_FIXTURE_DML,
                "UNDECLARED_CALCULATION_ROOT:0001",
                "APPLICATION",
                "COMMIT",
            )
        add(
            coordinate,
            _APPLICATION_BEHAVIOR_DML,
            f"REQUIREMENT_CASE_{ordinal:02d}:0001",
            "APPLICATION",
            "ROLLBACK",
        )
    for ordinal, (
        _case_id,
        role_class,
        outcome,
        has_graph,
        has_dashboard_preseed,
        has_erasure_seed,
        has_append_baseline,
    ) in enumerate(_FROZEN_BEHAVIOR_DML_CASE_TUPLE, start=1):
        coordinate = f"BEHAVIOR_{ordinal:02d}"
        add_scope(coordinate)
        if has_graph:
            add_graph(coordinate)
        if has_dashboard_preseed:
            add(
                coordinate,
                _APPLICATION_FIXTURE_DML,
                "BEHAVIOR_DASHBOARD_SEED:0001",
                "APPLICATION",
                "COMMIT",
            )
        if has_erasure_seed:
            add(
                coordinate,
                _APPLICATION_FIXTURE_DML,
                "ERASURE_REQUEST_SEED:0001",
                "APPLICATION",
                "COMMIT",
            )
        classification = (
            _APPLICATION_BEHAVIOR_DML if role_class == "APPLICATION" else _ERASURE_BEHAVIOR_DML
        )
        operation_prefix = f"BEHAVIOR_CASE_{ordinal:02d}"
        if has_append_baseline:
            add(coordinate, classification, f"{operation_prefix}:0001", role_class, "COMMIT")
            add(coordinate, classification, f"{operation_prefix}:0002", role_class, outcome)
        else:
            add(coordinate, classification, f"{operation_prefix}:0001", role_class, outcome)
    add_scope("RLS")
    add_graph("RLS")
    add("RLS", _APPLICATION_FIXTURE_DML, "RLS_BASELINE:0001", "APPLICATION", "COMMIT")
    for ordinal in range(1, 5):
        add(
            "RLS",
            _APPLICATION_BEHAVIOR_DML,
            f"RLS_PROBE_{ordinal:02d}:0001",
            "APPLICATION",
            "ROLLBACK",
        )
    add_scope("RACE_FORWARD")
    add_graph("RACE_FORWARD")
    add(
        "RACE_FORWARD",
        _APPLICATION_FIXTURE_DML,
        "ERASURE_REQUEST_SEED:0001",
        "APPLICATION",
        "COMMIT",
    )
    add(
        "RACE_FORWARD",
        _APPLICATION_BEHAVIOR_DML,
        "RACE_FORWARD_APPLICATION:0001",
        "APPLICATION",
        "ROLLBACK",
    )
    add(
        "RACE_FORWARD",
        _ERASURE_BEHAVIOR_DML,
        "RACE_FORWARD_ERASURE:0001",
        "ERASURE",
        "COMMIT",
    )
    add(
        "RACE_FORWARD",
        _APPLICATION_BEHAVIOR_DML,
        "RACE_FORWARD_APPLICATION:0002",
        "APPLICATION",
        "ROLLBACK",
    )
    add_scope("RACE_REVERSE")
    add_graph("RACE_REVERSE")
    add(
        "RACE_REVERSE",
        _APPLICATION_FIXTURE_DML,
        "ERASURE_REQUEST_SEED:0001",
        "APPLICATION",
        "COMMIT",
    )
    add(
        "RACE_REVERSE",
        _ERASURE_BEHAVIOR_DML,
        "RACE_REVERSE_ERASURE:0001",
        "ERASURE",
        "ROLLBACK",
    )
    add(
        "RACE_REVERSE",
        _APPLICATION_BEHAVIOR_DML,
        "RACE_REVERSE_APPLICATION:0001",
        "APPLICATION",
        "COMMIT",
    )
    add(
        "RACE_REVERSE",
        _ERASURE_BEHAVIOR_DML,
        "RACE_REVERSE_ERASURE:0002",
        "ERASURE",
        "ROLLBACK",
    )
    return tuple(rows)


def _validate_census_dml_closed_ledger(
    rows: Sequence[Mapping[str, object]],
    *,
    application_role: str,
    erasure_role: str,
) -> dict[str, object]:
    """Require the exact literal DML topology with no inferred operations."""

    def is_canonical_uuid(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return str(uuid.UUID(value)) == value
        except (AttributeError, TypeError, ValueError):
            return False

    ledger_keys = (
        "ledger_ordinal",
        "case_coordinate",
        "dml_classification",
        "operation",
        "expected_role",
        "current_user",
        "session_user",
        "tenant_id",
        "session_tenant_id",
        "session_replication_role",
        "transaction_outcome",
        "dml_statement_sha256_tuple",
        "dml_statement_count",
        "dml_statement_tuple_sha256",
        "posture_sha256",
    )

    def row_is_valid(ordinal: int, row: Mapping[str, object]) -> bool:
        statement_hashes_raw = row.get("dml_statement_sha256_tuple")
        statement_count_raw = row.get("dml_statement_count")
        if not isinstance(statement_hashes_raw, list) or type(statement_count_raw) is not int:
            return False
        statement_hashes = cast(list[object], statement_hashes_raw)
        statement_count = statement_count_raw
        return not (
            tuple(row) != ledger_keys
            or row.get("ledger_ordinal") != ordinal
            or row.get("current_user") != row.get("expected_role")
            or row.get("session_user") != row.get("expected_role")
            or row.get("session_replication_role") != "origin"
            or statement_count <= 0
            or statement_count != len(statement_hashes)
            or any(
                not isinstance(statement_sha256, str)
                or _SHA256_RE.fullmatch(statement_sha256) is None
                for statement_sha256 in statement_hashes
            )
            or row.get("dml_statement_tuple_sha256")
            != _canonical_sha256(
                [
                    "task8-v27-fixture-dml-statement-sha256-tuple-v1",
                    statement_hashes,
                ]
            )
            or not is_canonical_uuid(row.get("tenant_id"))
            or not isinstance(row.get("dml_statement_tuple_sha256"), str)
            or _SHA256_RE.fullmatch(str(row.get("dml_statement_tuple_sha256"))) is None
            or not isinstance(row.get("posture_sha256"), str)
            or _SHA256_RE.fullmatch(str(row.get("posture_sha256"))) is None
        )

    expected_sequence = _expected_census_dml_operation_sequence()
    actual_sequence = tuple(
        (
            str(row.get("case_coordinate")),
            str(row.get("dml_classification")),
            str(row.get("operation")),
            (
                "APPLICATION"
                if row.get("expected_role") == application_role
                else "ERASURE"
                if row.get("expected_role") == erasure_role
                else "UNKNOWN"
            ),
            str(row.get("transaction_outcome")),
        )
        for row in rows
    )
    if (
        not rows
        or actual_sequence != expected_sequence
        or any(not row_is_valid(ordinal, row) for ordinal, row in enumerate(rows, start=1))
    ):
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_LEDGER_INVALID")
    if any(
        row["tenant_id"] != str(_case_scope_identity(str(row["case_coordinate"]))[0])
        for row in rows
    ):
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_LEDGER_INVALID")
    if any(
        row["posture_sha256"]
        != _connection_posture_sha256(
            {
                "current_user": row["expected_role"],
                "session_user": row["expected_role"],
                "rolsuper": False,
                "rolbypassrls": False,
                "session_replication_role": "origin",
            }
        )
        for row in rows
    ):
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_LEDGER_INVALID")
    wrong_rls_tenant = _fixture_uuid("rls-probe-tenant", "WRONG_TENANT")
    if any(
        row["session_tenant_id"]
        != (
            wrong_rls_tenant
            if row["operation"] in {"RLS_PROBE_01:0001", "RLS_PROBE_02:0001"}
            else ""
            if row["operation"] in {"RLS_PROBE_03:0001", "RLS_PROBE_04:0001"}
            else row["tenant_id"]
        )
        for row in rows
    ):
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_LEDGER_INVALID")
    expected_sequence_sha256 = _canonical_sha256(
        ["task8-v27-census-dml-operation-sequence-v1", expected_sequence]
    )
    if expected_sequence_sha256 != _CENSUS_DML_OPERATION_SEQUENCE_SHA256:
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_LEDGER_INVALID")
    ledger_sha256 = _canonical_sha256(["task8-v27-census-dml-ledger-v1", rows])
    classification_counts = Counter(str(row["dml_classification"]) for row in rows)
    admin_dml_case_count = sum(
        row["expected_role"] not in {application_role, erasure_role} for row in rows
    )
    return {
        "fixture_dml_transaction_count": len(rows),
        "application_fixture_dml_count": classification_counts[_APPLICATION_FIXTURE_DML],
        "application_behavior_dml_count": classification_counts[_APPLICATION_BEHAVIOR_DML],
        "erasure_behavior_dml_count": classification_counts[_ERASURE_BEHAVIOR_DML],
        "admin_dml_case_count": admin_dml_case_count,
        "fixture_dml_operation_ledger_sha256": ledger_sha256,
        "fixture_dml_operation_sequence_sha256": expected_sequence_sha256,
    }


def _postgresql_unquoted_identifier_start(character: str) -> bool:
    return character == "_" or character.isalpha() or ord(character) >= 128


def _postgresql_unquoted_identifier_continuation(character: str) -> bool:
    return (
        _postgresql_unquoted_identifier_start(character) or character.isdigit() or character == "$"
    )


def _postgresql_dollar_quote_delimiter(sql: str, index: int) -> str | None:
    if index > 0 and _postgresql_unquoted_identifier_continuation(sql[index - 1]):
        return None
    tag_end = sql.find("$", index + 1)
    if tag_end < 0:
        return None
    tag = sql[index + 1 : tag_end]
    if tag and (
        not _postgresql_unquoted_identifier_start(tag[0])
        or not all(_postgresql_unquoted_identifier_continuation(character) for character in tag[1:])
    ):
        return None
    return sql[index : tag_end + 1]


def _top_level_sql_statement_count(sql: str) -> int:
    """Count top-level statements without treating quoted semicolons as separators."""

    count = 0
    segment_has_content = False
    index = 0
    while index < len(sql):
        value = sql[index]
        if value == "'":
            segment_has_content = True
            escape_string = (
                index > 0
                and sql[index - 1] in {"e", "E"}
                and (index == 1 or not (sql[index - 2].isalnum() or sql[index - 2] in {"_", "$"}))
            )
            index += 1
            while index < len(sql):
                if escape_string and sql[index] == "\\":
                    index += 2
                    continue
                if sql[index] != "'":
                    index += 1
                    continue
                if index + 1 < len(sql) and sql[index + 1] == "'":
                    index += 2
                    continue
                index += 1
                break
            else:
                _fail("TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED")
            continue
        if value == '"':
            segment_has_content = True
            index += 1
            while index < len(sql):
                if sql[index] != '"':
                    index += 1
                    continue
                if index + 1 < len(sql) and sql[index + 1] == '"':
                    index += 2
                    continue
                index += 1
                break
            else:
                _fail("TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED")
            continue
        if value == "$":
            delimiter = _postgresql_dollar_quote_delimiter(sql, index)
            if delimiter is not None:
                segment_has_content = True
                closing = sql.find(delimiter, index + len(delimiter))
                if closing < 0:
                    _fail("TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED")
                index = closing + len(delimiter)
                continue
        if value == ";":
            if not segment_has_content:
                _fail("TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED")
            count += 1
            segment_has_content = False
            index += 1
            continue
        if not value.isspace():
            segment_has_content = True
        index += 1
    return count + int(segment_has_content)


def _normalized_fixture_dml_statement(statement: object) -> str | None:
    """Return normalized SQL only when one execute contains fixture DML."""

    raw_statement = str(statement)
    normalized = " ".join(raw_statement.split())
    upper = normalized.upper()
    if "/*" in normalized or "*/" in normalized or "--" in normalized:
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED")
    if _top_level_sql_statement_count(raw_statement) > 1:
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED")
    contains_dml = (
        re.search(
            r"\b(?:INSERT\s+INTO|UPDATE\s+|DELETE\s+FROM|MERGE\s+INTO|TRUNCATE\s+)",
            normalized,
            flags=re.IGNORECASE,
        )
        is not None
    )
    mutation_wrapper = (
        re.match(
            r"^(?:CALL\b|DO\b|COPY\b|REFRESH\s+MATERIALIZED\s+VIEW\b|CREATE\s+TABLE\b.+\bAS\b)",
            upper,
        )
        is not None
    )
    if mutation_wrapper:
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED")
    if upper.startswith("WITH ") and contains_dml:
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_CTE_UNSUPPORTED")
    if _FIXTURE_DML_RE.search(normalized) is not None:
        return normalized
    if contains_dml:
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED")
    if re.fullmatch(
        r"SELECT public\.idis_task7_execute_approved_erasure\s*\(.+\)\s*;?",
        normalized,
        flags=re.IGNORECASE,
    ):
        return normalized
    return None


_ENGINE_DML_CONFIG_KEY = "task8_v27_census_dml_config"


def _observe_fixture_dml_engine_statement(connection: Any, statement: object) -> None:
    config = connection.info.get(_ENGINE_DML_CONFIG_KEY)
    if not isinstance(config, dict):
        _fail("TASK8_V27_CENSUS_UNCLASSIFIED_DML_FORBIDDEN")
    normalized = _normalized_fixture_dml_statement(statement)
    if normalized is None:
        return
    transaction = connection.info.setdefault(
        str(config["info_key"]),
        {"statements": [], "posture": None},
    )
    if transaction["posture"] is None:
        raw_cursor = connection.connection.cursor()
        try:
            posture = _attest_connection_role_posture(
                raw_cursor,
                str(config["expected_role"]),
            )
            raw_cursor.execute("SELECT current_setting('idis.tenant_id',true)")
            tenant_row = raw_cursor.fetchone()
        finally:
            raw_cursor.close()
        if tenant_row is None or str(tenant_row[0]) != config["expected_tenant"]:
            _fail("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID")
        transaction["posture"] = posture
    transaction["statements"].append(normalized)


class _InstrumentedDbapiCursor:
    """Route direct DBAPI executes into the owning SQLAlchemy transaction ledger."""

    def __init__(self, connection: Any, cursor: Any) -> None:
        self._connection = connection
        self._cursor = cursor

    def execute(self, statement: object, parameters: object = None) -> Any:
        _observe_fixture_dml_engine_statement(self._connection, statement)
        if parameters is None:
            return self._cursor.execute(statement)
        return self._cursor.execute(statement, parameters)

    def executemany(self, statement: object, parameters: object) -> Any:
        _observe_fixture_dml_engine_statement(self._connection, statement)
        return self._cursor.executemany(statement, parameters)

    def __enter__(self) -> _InstrumentedDbapiCursor:
        self._cursor.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> object:
        return self._cursor.__exit__(*exc_info)

    def __iter__(self) -> Any:
        return iter(self._cursor)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


def _instrumented_dbapi_cursor(connection: Any) -> _InstrumentedDbapiCursor:
    if not isinstance(connection.info.get(_ENGINE_DML_CONFIG_KEY), dict):
        _fail("TASK8_V27_CENSUS_UNCLASSIFIED_DML_FORBIDDEN")
    return _InstrumentedDbapiCursor(connection, connection.connection.cursor())


def _instrument_fixture_dml_engine(
    engine: Any,
    *,
    expected_application_role: str,
    tenant_id: object,
    case_coordinate: str,
    operation_prefix: str,
) -> None:
    """Attest every SQLAlchemy and direct-DBAPI fixture-DML transaction."""

    try:
        from sqlalchemy import event
    except ImportError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_SQLALCHEMY_UNAVAILABLE") from exc
    try:
        expected_tenant = str(uuid.UUID(str(tenant_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID") from exc
    if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", operation_prefix) is None:
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID")
    _case_scope_identity(case_coordinate)
    config: dict[str, Any] = {
        "info_key": f"task8_v27_fixture_dml_{case_coordinate.lower()}_{operation_prefix.lower()}",
        "expected_role": expected_application_role,
        "expected_tenant": expected_tenant,
        "case_coordinate": case_coordinate,
        "operation_prefix": operation_prefix,
        "transaction_ordinal": 0,
    }

    def engine_connect(connection: Any) -> None:
        connection.info[_ENGINE_DML_CONFIG_KEY] = config

    def before_cursor_execute(
        connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        _observe_fixture_dml_engine_statement(connection, statement)

    def finish_transaction(connection: Any, outcome: str) -> None:
        transaction = connection.info.pop(str(config["info_key"]), None)
        if transaction is None or not transaction["statements"]:
            return
        config["transaction_ordinal"] = int(config["transaction_ordinal"]) + 1
        _record_fixture_dml_role(
            case_coordinate=str(config["case_coordinate"]),
            dml_classification=_APPLICATION_FIXTURE_DML,
            operation=(f"{config['operation_prefix']}:{int(config['transaction_ordinal']):04d}"),
            posture=transaction["posture"],
            expected_application_role=str(config["expected_role"]),
            tenant_id=str(config["expected_tenant"]),
            session_tenant_id=str(config["expected_tenant"]),
            transaction_outcome=outcome,
            dml_statement_tuple=tuple(transaction["statements"]),
        )

    def commit(connection: Any) -> None:
        finish_transaction(connection, "COMMIT")

    def rollback(connection: Any) -> None:
        finish_transaction(connection, "ROLLBACK")

    event.listen(engine, "engine_connect", engine_connect)
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "commit", commit)
    event.listen(engine, "rollback", rollback)


def _next_fixture_uuid() -> uuid.UUID:
    """Return the next deterministic disposable-fixture UUID."""

    global _fixture_uuid_ordinal
    _fixture_uuid_ordinal += 1
    return uuid.UUID(_fixture_uuid("ordered-fixture-id", _fixture_uuid_ordinal))


def _validated_cdd_template_bytes(raw: object) -> bytes:
    if (
        not isinstance(raw, bytes)
        or len(raw) != _CDD_TEMPLATE_BYTE_COUNT
        or hashlib.sha256(raw).hexdigest() != _CDD_TEMPLATE_SHA256
    ):
        _fail("TASK8_V27_CENSUS_CDD_TEMPLATE_INVALID")
    return raw


def _capture_cdd_template_bytes(path: Path | None = None) -> bytes:
    """Read the checked-in CDD through one regular, unlinked file identity."""

    candidate = path or (
        Path(__file__).resolve().parents[1]
        / "src"
        / "idis"
        / "methodology"
        / "templates"
        / "commercial_dd_v1.json"
    )
    try:
        lexical = candidate.absolute()
        before = lexical.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or getattr(before, "st_file_attributes", 0) & 0x400
        ):
            _fail("TASK8_V27_CENSUS_CDD_TEMPLATE_INVALID")
        with lexical.open("rb") as handle:
            opened_before = os.fstat(handle.fileno())
            raw = handle.read()
            opened_after = os.fstat(handle.fileno())
        after = lexical.lstat()
    except Task8V27CensusError:
        raise
    except OSError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_CDD_TEMPLATE_INVALID") from exc
    identities = tuple(
        (value.st_dev, value.st_ino, value.st_mode, value.st_size)
        for value in (before, opened_before, opened_after, after)
    )
    if len(set(identities)) != 1:
        _fail("TASK8_V27_CENSUS_CDD_TEMPLATE_INVALID")
    return _validated_cdd_template_bytes(raw)


def build_v27_dashboard_seed_plan(
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    *,
    cdd_raw: bytes,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Build the safe plan from the two production methodology constructors."""

    from idis.api.routes.methodology_versions import _source_contract_hash
    from idis.methodology.importers.cdd_json import import_cdd_template
    from idis.methodology.importers.fdd_formula_policy_v2 import (
        import_fdd_formula_policy_v2_registry,
    )
    from idis.methodology.models import MethodologyType
    from idis.models.methodology_coverage import MethodologyCoverageRecord

    _validate_v27(artifact)
    if not isinstance(policy_artifact_raw, bytes) or not isinstance(workbook_raw, bytes):
        _fail("TASK8_V27_CENSUS_POLICY_INVALID")
    try:
        canonical_scope = tuple(str(uuid.UUID(value)) for value in (tenant_id, deal_id, run_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_SCOPE_INVALID") from exc
    cdd_raw = _validated_cdd_template_bytes(cdd_raw)
    try:
        commercial = import_cdd_template(cdd_raw)
        financial = import_fdd_formula_policy_v2_registry(
            workbook_raw,
            policy_artifact=policy_artifact_raw,
        )
    except Exception as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_POLICY_INVALID") from exc
    validate_v27_dashboard_requirement_binding(artifact, financial)

    def registry_identity(registry: Any) -> dict[str, object]:
        is_financial = registry.methodology_type is MethodologyType.FINANCIAL_DD
        return _methodology_registry_roundtrip_identity(
            registry,
            tenant_id=canonical_scope[0],
            source_type="operator_workbook" if is_financial else "checked_in_template",
            source_byte_count=len(workbook_raw) if is_financial else len(cdd_raw),
            source_contract_hash=_source_contract_hash(registry.methodology_type),
        )

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
        _fail("TASK8_V27_CENSUS_QUESTION_SEED_INVALID")
    payload = {
        "schema_version": "task8-v27-dashboard-seed-plan-v2",
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
        plan.get("schema_version") != "task8-v27-dashboard-seed-plan-v2"
        or plan.get("expected_sha256") != _canonical_sha256(payload)
        or not isinstance(rows, list)
        or len(rows) != 100
    ):
        _fail("TASK8_V27_CENSUS_GOVERNED_SEED_INVALID")
    try:
        tenant_id, deal_id, run_id = (
            str(uuid.UUID(str(plan[key]))) for key in ("tenant_id", "deal_id", "run_id")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_GOVERNED_SEED_INVALID") from exc
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
            raise Task8V27CensusError("TASK8_V27_CENSUS_GOVERNED_SEED_INVALID") from exc
        if (
            row.get("methodology_version_record_id") != expected_version_record_id
            or row.get("methodology_question_record_id") != expected_question_record_id
            or row.get("coverage_record_id") != expected_coverage_record_id
            or row.get("dashboard_row_id") != expected_dashboard_row_id
        ):
            _fail("TASK8_V27_CENSUS_GOVERNED_SEED_INVALID")
        for key, value in row.items():
            if (
                key.endswith("_sha256")
                and isinstance(value, str)
                and (_SHA256_RE.fullmatch(value) is None or len(set(value)) == 1)
            ):
                _fail(f"TASK8_V27_CENSUS_GOVERNED_SEED_INVALID:{key}")


def _partial_dashboard_seed_plan(
    full_plan: Mapping[str, Any],
    *,
    coverage_row_count: int,
    financial_coverage_row_count: int,
) -> dict[str, Any]:
    """Derive the one reviewed 99/78 negative fixture from the exact 100-row plan."""

    rows = full_plan.get("row_tuple")
    if (
        coverage_row_count != 99
        or financial_coverage_row_count != 78
        or not isinstance(rows, list)
        or len(rows) != 100
    ):
        _fail("TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    removable = next(
        (
            row
            for row in reversed(rows)
            if row.get("methodology_type") == "financial_dd"
            and row.get("required_calculation_count") == 0
        ),
        None,
    )
    if removable is None:
        _fail("TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    partial_rows = [dict(row) for row in rows if row is not removable]
    for ordinal, row in enumerate(partial_rows, start=1):
        row["coverage_ordinal"] = ordinal
    payload = {
        key: value
        for key, value in full_plan.items()
        if key not in {"expected_sha256", "row_tuple"}
    }
    payload.update(
        {
            "schema_version": "task8-v27-dashboard-seed-plan-partial-99-v1",
            "parent_plan_sha256": full_plan["expected_sha256"],
            "omitted_coverage_record_id": removable["coverage_record_id"],
            "coverage_row_delta": -1,
            "financial_row_delta": -1,
            "coverage_row_count": 99,
            "commercial_row_count": 21,
            "financial_row_count": 78,
            "row_tuple": partial_rows,
        }
    )
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def build_seed_fk_closure_manifest(v27: Mapping[str, Any]) -> dict[str, object]:
    """Bind the exact source-derived write, parent, and installed-FK authority."""

    _validate_v27(v27)
    fk_rows = [_fk_row_json(row) for row in _EXPECTED_SEED_FK_ROWS]
    fk_sort_keys = [(row[0], row[1], row[2], row[4], row[5]) for row in fk_rows]
    deferred_rows = {(row[1], row[2]) for row in fk_rows if row[8] is True or row[9] is True}
    fk_parent_relations = {str(row[5]) for row in fk_rows}
    if (
        len(_SEED_WRITE_RELATIONS) != 36
        or len(_SEED_FK_CLOSURE_RELATIONS) != 37
        or len(_EXPECTED_SEED_FK_ROWS) != 92
        or tuple(sorted(set(_SEED_WRITE_RELATIONS))) != _SEED_WRITE_RELATIONS
        or tuple(sorted(set(_SEED_FK_CLOSURE_RELATIONS))) != _SEED_FK_CLOSURE_RELATIONS
        or set(_SEED_FK_CLOSURE_RELATIONS) != set(_SEED_WRITE_RELATIONS) | fk_parent_relations
        or fk_sort_keys != sorted(set(fk_sort_keys))
        or any(
            len(row) != 13
            or row[0] != "public"
            or row[4] != "public"
            or row[1] not in _SEED_WRITE_RELATIONS
            or row[5] not in _SEED_FK_CLOSURE_RELATIONS
            or not isinstance(row[3], list)
            or not isinstance(row[6], list)
            or not row[3]
            or len(row[3]) != len(row[6])
            or type(row[7]) is not bool
            or type(row[8]) is not bool
            or type(row[9]) is not bool
            or row[7] is not True
            or row[10:] != ["NO ACTION", "NO ACTION", "SIMPLE"]
            for row in fk_rows
        )
        or deferred_rows
        != {
            ("data_room_packages", "fk_task7_data_room_packages_source_root"),
            (
                "source_lineage_objects",
                "fk_task7_source_lineage_objects_corpus",
            ),
        }
        or any(
            (row[8], row[9])
            != ((True, True) if (row[1], row[2]) in deferred_rows else (False, False))
            for row in fk_rows
        )
    ):
        _fail("TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID")

    observed_migration_rows = [list(row) for row in _MIGRATION_LF_SHA256_ROWS]
    try:
        dashboard_schema_rows = [
            row
            for row in v27["future_native_authority_registry"]["rows"]
            if row.get("planned_table") == "methodology_truth_dashboard_rows"
        ]
        dashboard_link_schema_rows = [
            row
            for row in v27["future_child_authority_registry"]["rows"]
            if row.get("table_name") == "methodology_truth_dashboard_root_links"
        ]
    except (KeyError, TypeError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID") from exc
    if (
        len(dashboard_schema_rows) != 1
        or dashboard_schema_rows[0].get("schema_sha256")
        != "b69c13dfc8a41eee6b403116b1bc971b917905705e4c5a4a457b8b4bf1f7f221"
        or len(dashboard_link_schema_rows) != 1
        or dashboard_link_schema_rows[0].get("schema_sha256")
        != "f33c3a3172bd66c9dedd354c2aedc6b735bd0b4edcbcc468d7211f41179929f8"
    ):
        _fail("TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID")

    payload = {
        "schema_version": "task8-v27-seed-fk-closure-manifest-v2",
        "seed_write_relation_count": 36,
        "seed_write_relation_tuple": list(_SEED_WRITE_RELATIONS),
        "seed_write_relation_sha256": _SEED_WRITE_RELATIONS_SHA256,
        "seed_fk_relation_closure_count": 37,
        "seed_fk_relation_closure_tuple": list(_SEED_FK_CLOSURE_RELATIONS),
        "seed_fk_relation_closure_sha256": _SEED_FK_CLOSURE_RELATIONS_SHA256,
        "migration_lf_sha256_tuple": observed_migration_rows,
        "migration_lf_sha256_tuple_sha256": _MIGRATION_LF_SHA256_ROWS_SHA256,
        "expected_foreign_key_inventory_count": 92,
        "expected_foreign_key_inventory_tuple": [
            _fk_row_json(row) for row in _EXPECTED_SEED_FK_ROWS
        ],
        "expected_foreign_key_inventory_sha256": _EXPECTED_SEED_FK_ROWS_SHA256,
        "installed_v27_artifact_sha256": _ARTIFACT_SHA256,
        "dashboard_schema_sha256": dashboard_schema_rows[0]["schema_sha256"],
        "dashboard_root_link_schema_sha256": dashboard_link_schema_rows[0]["schema_sha256"],
        "legacy_fk_drop_tuple": [
            ["data_room_package_files", "data_room_package_files_package_id_fkey"],
            ["document_spans", "document_spans_document_id_fkey"],
        ],
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
    closure_manifest: Mapping[str, object],
) -> dict[str, object]:
    """Force all keys and compare app-observed catalog/FK closure byte-for-byte."""

    manifest_payload = {
        key: value for key, value in closure_manifest.items() if key != "expected_sha256"
    }
    if (
        closure_manifest.get("schema_version") != "task8-v27-seed-fk-closure-manifest-v2"
        or closure_manifest.get("seed_write_relation_tuple") != list(_SEED_WRITE_RELATIONS)
        or closure_manifest.get("seed_fk_relation_closure_tuple")
        != list(_SEED_FK_CLOSURE_RELATIONS)
        or closure_manifest.get("expected_foreign_key_inventory_tuple")
        != [_fk_row_json(row) for row in _EXPECTED_SEED_FK_ROWS]
        or closure_manifest.get("migration_lf_sha256_tuple")
        != [list(row) for row in _MIGRATION_LF_SHA256_ROWS]
        or closure_manifest.get("expected_sha256") != _canonical_sha256(manifest_payload)
    ):
        _fail("TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID")

    identifier_re = re.compile(r"^[a-z_][a-z0-9_]*$")
    connection = _connect(application_url)
    try:
        with connection.cursor() as cursor:
            observer_posture = _attest_connection_role_posture(
                cursor,
                expected_application_role,
            )
            _set_tenant(cursor, tenant_id)
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                """
                SELECT c.relname
                  FROM pg_catalog.pg_class AS c
                  JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace
                 WHERE n.nspname='public'
                   AND c.relkind IN ('r','p')
                   AND c.relname=ANY(%s::text[])
                 ORDER BY c.relname
                """,
                (list(_SEED_FK_CLOSURE_RELATIONS),),
            )
            relation_inventory = tuple(str(row[0]) for row in cursor.fetchall())
            cursor.execute(
                """
                SELECT child_namespace.nspname,
                       child.relname,
                       c.conname,
                       ARRAY(
                         SELECT child_attribute.attname
                           FROM unnest(c.conkey) WITH ORDINALITY AS key(attnum,ordinal)
                           JOIN pg_catalog.pg_attribute AS child_attribute
                             ON child_attribute.attrelid=c.conrelid
                            AND child_attribute.attnum=key.attnum
                          ORDER BY key.ordinal
                       ),
                       parent_namespace.nspname,
                       parent.relname,
                       ARRAY(
                         SELECT parent_attribute.attname
                           FROM unnest(c.confkey) WITH ORDINALITY AS key(attnum,ordinal)
                           JOIN pg_catalog.pg_attribute AS parent_attribute
                             ON parent_attribute.attrelid=c.confrelid
                            AND parent_attribute.attnum=key.attnum
                          ORDER BY key.ordinal
                       ),
                       c.convalidated,
                       c.condeferrable,
                       c.condeferred,
                       CASE c.confupdtype
                         WHEN 'a' THEN 'NO ACTION'
                         WHEN 'r' THEN 'RESTRICT'
                         WHEN 'c' THEN 'CASCADE'
                         WHEN 'n' THEN 'SET NULL'
                         WHEN 'd' THEN 'SET DEFAULT'
                       END,
                       CASE c.confdeltype
                         WHEN 'a' THEN 'NO ACTION'
                         WHEN 'r' THEN 'RESTRICT'
                         WHEN 'c' THEN 'CASCADE'
                         WHEN 'n' THEN 'SET NULL'
                         WHEN 'd' THEN 'SET DEFAULT'
                       END,
                       CASE c.confmatchtype
                         WHEN 's' THEN 'SIMPLE'
                         WHEN 'f' THEN 'FULL'
                         WHEN 'p' THEN 'PARTIAL'
                       END
                  FROM pg_catalog.pg_constraint AS c
                  JOIN pg_catalog.pg_class AS child ON child.oid=c.conrelid
                  JOIN pg_catalog.pg_class AS parent ON parent.oid=c.confrelid
                  JOIN pg_catalog.pg_namespace AS child_namespace
                    ON child_namespace.oid=child.relnamespace
                  JOIN pg_catalog.pg_namespace AS parent_namespace
                    ON parent_namespace.oid=parent.relnamespace
                 WHERE c.contype='f'
                   AND child_namespace.nspname='public'
                   AND child.relname=ANY(%s::text[])
                 ORDER BY child_namespace.nspname,
                          child.relname,
                          c.conname,
                          parent_namespace.nspname,
                          parent.relname
                """,
                (list(_SEED_WRITE_RELATIONS),),
            )
            raw_foreign_key_rows = cursor.fetchall()
            if any(
                len(row) != 13
                or type(row[7]) is not bool
                or type(row[8]) is not bool
                or type(row[9]) is not bool
                or not isinstance(row[3], (list, tuple))
                or not isinstance(row[6], (list, tuple))
                or any(value is None for value in row)
                for row in raw_foreign_key_rows
            ):
                _fail("TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID")
            foreign_key_inventory = tuple(
                [
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    [str(value) for value in row[3]],
                    str(row[4]),
                    str(row[5]),
                    [str(value) for value in row[6]],
                    row[7],
                    row[8],
                    row[9],
                    str(row[10]),
                    str(row[11]),
                    str(row[12]),
                ]
                for row in raw_foreign_key_rows
            )
            orphan_row_count = 0
            for fk_row in foreign_key_inventory:
                (
                    child_schema,
                    child_relation,
                    _constraint_name,
                    child_columns,
                    parent_schema,
                    parent_relation,
                    parent_columns,
                    _validated,
                    _deferrable,
                    _deferred,
                    _update_action,
                    _delete_action,
                    match_type,
                ) = fk_row
                identifiers = (
                    child_schema,
                    child_relation,
                    parent_schema,
                    parent_relation,
                    *child_columns,
                    *parent_columns,
                )
                if (
                    match_type != "SIMPLE"
                    or len(child_columns) != len(parent_columns)
                    or any(identifier_re.fullmatch(value) is None for value in identifiers)
                ):
                    _fail("TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID")
                nonnull = " AND ".join(f'child."{column}" IS NOT NULL' for column in child_columns)
                equality = " AND ".join(
                    f'parent."{parent_column}" IS NOT DISTINCT FROM child."{child_column}"'
                    for child_column, parent_column in zip(
                        child_columns, parent_columns, strict=True
                    )
                )
                cursor.execute(
                    f"""
                    SELECT count(*)
                      FROM "{child_schema}"."{child_relation}" AS child
                     WHERE {nonnull}
                       AND NOT EXISTS (
                         SELECT 1
                           FROM "{parent_schema}"."{parent_relation}" AS parent
                          WHERE {equality}
                       )
                    """
                )
                orphan_row_count += int(cursor.fetchone()[0])
        connection.rollback()
    finally:
        connection.close()

    expected_foreign_keys = tuple(_fk_row_json(row) for row in _EXPECTED_SEED_FK_ROWS)
    relation_inventory_sha256 = _SEED_FK_CLOSURE_RELATIONS_SHA256
    foreign_key_inventory_sha256 = _EXPECTED_SEED_FK_ROWS_SHA256
    unvalidated_fk_count = sum(row[7] is not True for row in foreign_key_inventory)
    if (
        relation_inventory != _SEED_FK_CLOSURE_RELATIONS
        or foreign_key_inventory != expected_foreign_keys
        or relation_inventory_sha256 != _SEED_FK_CLOSURE_RELATIONS_SHA256
        or foreign_key_inventory_sha256 != _EXPECTED_SEED_FK_ROWS_SHA256
        or any(row[5] not in _SEED_FK_CLOSURE_RELATIONS for row in foreign_key_inventory)
        or unvalidated_fk_count != 0
        or orphan_row_count != 0
        or observer_posture["session_replication_role"] != "origin"
    ):
        _fail("TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID")
    payload = {
        "schema_version": "task8-v27-seed-fk-closure-observation-v2",
        "observer_role": _posture_role(observer_posture),
        "observer_posture_sha256": _connection_posture_sha256(observer_posture),
        "session_replication_role": observer_posture["session_replication_role"],
        "tenant_id": str(tenant_id),
        "deal_id": str(deal_id),
        "closure_manifest_sha256": closure_manifest["expected_sha256"],
        "seed_write_relation_count": len(_SEED_WRITE_RELATIONS),
        "seed_write_relation_sha256": _SEED_WRITE_RELATIONS_SHA256,
        "relation_inventory_count": len(relation_inventory),
        "relation_inventory_sha256": relation_inventory_sha256,
        "foreign_key_inventory_count": len(foreign_key_inventory),
        "foreign_key_inventory_sha256": foreign_key_inventory_sha256,
        "migration_lf_sha256_tuple_sha256": _MIGRATION_LF_SHA256_ROWS_SHA256,
        "unvalidated_fk_count": unvalidated_fk_count,
        "orphan_row_count": orphan_row_count,
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def _production_methodology_registries(
    *,
    cdd_raw: bytes,
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
) -> tuple[Any, Any]:
    """Construct the exact checked-in CDD and governed policy-v2 FDD registries."""

    from idis.methodology.importers.cdd_json import import_cdd_template
    from idis.methodology.importers.fdd_formula_policy_v2 import (
        import_fdd_formula_policy_v2_registry,
    )

    cdd_raw = _validated_cdd_template_bytes(cdd_raw)
    if not isinstance(policy_artifact_raw, bytes) or not isinstance(workbook_raw, bytes):
        _fail("TASK8_V27_CENSUS_POLICY_INVALID")
    try:
        commercial = import_cdd_template(cdd_raw)
        financial = import_fdd_formula_policy_v2_registry(
            workbook_raw,
            policy_artifact=policy_artifact_raw,
        )
    except Exception as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_POLICY_INVALID") from exc
    if (
        len(commercial.current_version.questions) != 21
        or len(financial.current_version.questions) != 79
        or sum(
            len(question.required_calculations) for question in financial.current_version.questions
        )
        != 10
    ):
        _fail("TASK8_V27_CENSUS_POLICY_INVALID")
    return commercial, financial


def _methodology_question_identity_tuple(
    registry: Any,
    *,
    tenant_id: str,
    methodology_version_record_id: str,
) -> list[list[object]]:
    """Render every persisted question field that participates in Task-5 truth."""

    from idis.methodology.ids import normalize_text

    rows: list[list[object]] = []
    try:
        for ordinal, question in enumerate(registry.current_version.questions, start=1):
            record_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "idis:methodology-question:"
                    f"{tenant_id}:{methodology_version_record_id}:"
                    f"{question.methodology_question_id}",
                )
            )
            payload = question.model_dump(mode="json")
            semantic = {
                "methodology_type": question.methodology_type.value,
                "section": normalize_text(question.section),
                "sheet_or_source_section": normalize_text(question.sheet_or_source_section or ""),
                "term": normalize_text(question.term or ""),
                "nature": normalize_text(question.nature or ""),
                "line_item": normalize_text(question.line_item or ""),
                "question_text": normalize_text(question.question_text),
            }
            requirements = [
                requirement.model_dump(mode="json")
                for requirement in question.required_calculations
            ]
            rows.append(
                [
                    tenant_id,
                    record_id,
                    methodology_version_record_id,
                    question.methodology_version_id,
                    question.methodology_question_id,
                    question.methodology_id,
                    question.methodology_type.value,
                    ordinal,
                    question.section,
                    question.source_trace.source_type,
                    question.source_trace.source_name,
                    question.source_trace.sheet_or_section,
                    question.source_trace.row_number,
                    question.source_trace.source_anchor_cell,
                    question.source_trace.source_hash,
                    _canonical_sha256(semantic),
                    _canonical_sha256(payload),
                    _canonical_sha256(payload),
                    requirements,
                ]
            )
    except (AttributeError, TypeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID") from exc
    return rows


def _methodology_registry_roundtrip_identity(
    registry: Any,
    *,
    tenant_id: str,
    source_type: str,
    source_byte_count: int,
    source_contract_hash: str,
    status: str = "FROZEN",
) -> dict[str, object]:
    """Render complete timestamp-redacted version and question authority bytes."""

    from idis.persistence.repositories.methodologies import (
        methodology_question_payload_hashes,
        methodology_source_trace_digest,
        methodology_version_hash,
    )

    try:
        version = registry.current_version
        record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "idis:methodology-version:"
                f"{tenant_id}:{version.methodology_version_id}:{version.source_hash}",
            )
        )
        question_tuple = _methodology_question_identity_tuple(
            registry,
            tenant_id=tenant_id,
            methodology_version_record_id=record_id,
        )
        source_sections = {question.source_trace.sheet_or_section for question in version.questions}
        question_payload_hashes = list(methodology_question_payload_hashes(registry))
        required_calculation_tuple = [[row[1], row[18]] for row in question_tuple]
        identity = {
            "schema_version": "task8-v27-task5-version-roundtrip-identity-v2",
            "tenant_id": str(uuid.UUID(tenant_id)),
            "methodology_version_record_id": record_id,
            "methodology_version_id": version.methodology_version_id,
            "methodology_id": version.methodology_id,
            "methodology_type": version.methodology_type.value,
            "version_label": version.version_label,
            "source_type": source_type,
            "source_alias": version.source_name,
            "source_sha256": version.source_hash,
            "source_byte_count": source_byte_count,
            "source_contract_hash": source_contract_hash,
            "source_trace_sha256": methodology_source_trace_digest(registry),
            "registry_sha256": registry.registry_hash,
            "version_sha256": methodology_version_hash(registry),
            "question_count": len(question_tuple),
            "source_section_count": len(source_sections),
            "header_row": 2 if version.methodology_type.value == "financial_dd" else None,
            "status": status,
            "timestamp_redaction_field_tuple": ["imported_at", "frozen_at"],
            "deterministic_registry_bytes_sha256": hashlib.sha256(
                registry.to_deterministic_json().encode("utf-8")
            ).hexdigest(),
            "methodology_question_payload_hashes": question_payload_hashes,
            "question_identity_field_tuple": list(_TASK5_QUESTION_IDENTITY_FIELDS),
            "question_identity_tuple": question_tuple,
            "question_identity_tuple_sha256": _canonical_sha256(question_tuple),
            "required_calculation_tuple": required_calculation_tuple,
            "required_calculation_tuple_sha256": _canonical_sha256(required_calculation_tuple),
        }
        if question_payload_hashes != [str(row[16]) for row in question_tuple]:
            _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    except (AttributeError, TypeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID") from exc
    return identity


def _methodology_version_record_roundtrip_identity(record: Any) -> dict[str, object]:
    identity = _methodology_registry_roundtrip_identity(
        record.registry,
        tenant_id=str(record.tenant_id),
        source_type=str(record.source_type),
        source_byte_count=int(record.source_byte_count),
        source_contract_hash=str(record.source_contract_hash),
        status=str(record.status),
    )
    expected = {
        "methodology_version_record_id": str(record.methodology_version_record_id),
        "methodology_version_id": str(record.methodology_version_id),
        "methodology_id": str(record.methodology_id),
        "methodology_type": record.methodology_type.value,
        "version_label": str(record.version_label),
        "source_alias": str(record.source_alias),
        "source_sha256": str(record.source_sha256),
        "source_trace_sha256": str(record.source_trace_digest),
        "registry_sha256": str(record.registry_hash),
        "version_sha256": str(record.version_hash),
        "question_count": int(record.question_count),
        "source_section_count": int(record.source_section_count),
        "header_row": record.header_row,
        "status": "FROZEN",
    }
    if any(identity[key] != value for key, value in expected.items()):
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    return identity


def _selection_roundtrip_identity(selection: Any) -> dict[str, object]:
    """Render the exact persisted Task-5 pair selection without prose."""

    try:
        return {
            "selection_id": str(selection.selection_id),
            "tenant_id": str(selection.tenant_id),
            "deal_id": str(selection.deal_id),
            "run_id": str(selection.run_id),
            "commercial_dd_version_record_id": str(selection.commercial_dd_version_record_id),
            "financial_dd_version_record_id": str(selection.financial_dd_version_record_id),
            "selected_version_ids": list(selection.selected_version_ids),
            "selected_source_hashes": list(selection.selected_source_hashes),
            "selected_registry_hashes": list(selection.selected_registry_hashes),
            "selected_version_hashes": list(selection.selected_version_hashes),
            "selection_hash": str(selection.selection_hash),
            "selected_question_count": int(selection.selected_question_count),
            "state": str(selection.state),
            "immutable": bool(selection.immutable),
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID") from exc


def _validate_task5_roundtrip_identity_pair(
    registries: Sequence[Mapping[str, object]],
    selection: Mapping[str, object],
) -> None:
    """Validate the closed Task-5 family and selection cross-binding."""

    if (
        len(registries) != 2
        or [row.get("methodology_type") for row in registries] != ["commercial_dd", "financial_dd"]
        or set(selection) != set(_TASK5_SELECTION_IDENTITY_FIELDS)
    ):
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    expected_family = (
        ("commercial_dd", 21, None, "checked_in_template"),
        ("financial_dd", 79, 2, "operator_workbook"),
    )
    for identity, (methodology_type, count, header_row, source_type) in zip(
        registries, expected_family, strict=True
    ):
        rows = identity.get("question_identity_tuple")
        payload_hashes = identity.get("methodology_question_payload_hashes")
        required_tuple = identity.get("required_calculation_tuple")
        if (
            set(identity) != set(_TASK5_REGISTRY_IDENTITY_FIELDS)
            or identity.get("schema_version") != "task8-v27-task5-version-roundtrip-identity-v2"
            or identity.get("methodology_id") != methodology_type
            or identity.get("methodology_type") != methodology_type
            or identity.get("question_count") != count
            or identity.get("header_row") != header_row
            or identity.get("source_type") != source_type
            or identity.get("status") != "FROZEN"
            or identity.get("timestamp_redaction_field_tuple") != ["imported_at", "frozen_at"]
            or identity.get("question_identity_field_tuple")
            != list(_TASK5_QUESTION_IDENTITY_FIELDS)
            or not isinstance(rows, list)
            or len(rows) != count
            or not isinstance(payload_hashes, list)
            or not isinstance(required_tuple, list)
            or type(identity.get("source_byte_count")) is not int
            or int(identity["source_byte_count"]) <= 0
        ):
            _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
        hash_fields = (
            "source_sha256",
            "source_contract_hash",
            "source_trace_sha256",
            "registry_sha256",
            "version_sha256",
            "deterministic_registry_bytes_sha256",
            "question_identity_tuple_sha256",
            "required_calculation_tuple_sha256",
        )
        if any(
            not isinstance(identity.get(field), str)
            or _SHA256_RE.fullmatch(str(identity[field])) is None
            for field in hash_fields
        ):
            _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
        for ordinal, row in enumerate(rows, start=1):
            if (
                not isinstance(row, list)
                or len(row) != len(_TASK5_QUESTION_IDENTITY_FIELDS)
                or row[0] != identity["tenant_id"]
                or row[2] != identity["methodology_version_record_id"]
                or row[3] != identity["methodology_version_id"]
                or row[5] != identity["methodology_id"]
                or row[6] != methodology_type
                or row[7] != ordinal
                or row[9] != source_type
                or row[10] != identity["source_alias"]
                or row[14] != identity["source_sha256"]
                or row[16] != row[17]
                or any(
                    not isinstance(row[index], str) or _SHA256_RE.fullmatch(row[index]) is None
                    for index in (15, 16, 17)
                )
                or not isinstance(row[18], list)
            ):
                _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
        if (
            len({row[1] for row in rows}) != count
            or [row[16] for row in rows] != payload_hashes
            or [[row[1], row[18]] for row in rows] != required_tuple
            or _canonical_sha256(rows) != identity["question_identity_tuple_sha256"]
            or _canonical_sha256(required_tuple) != identity["required_calculation_tuple_sha256"]
            or len({row[11] for row in rows}) != identity["source_section_count"]
        ):
            _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
        nonempty_requirements = [row[18] for row in rows if row[18]]
        if methodology_type == "commercial_dd":
            if nonempty_requirements != [[{"calc_type": "LTV_CAC_RATIO", "required": False}]]:
                _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
        elif len(nonempty_requirements) != 10 or any(
            len(requirements) != 1
            or set(requirements[0]) != {"formula_ordinal", "formula_type", "required", "scope"}
            or requirements[0]["required"] is not True
            for requirements in nonempty_requirements
        ):
            _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")

    commercial, financial = registries
    if (
        selection.get("tenant_id") != commercial["tenant_id"]
        or selection.get("tenant_id") != financial["tenant_id"]
        or selection.get("commercial_dd_version_record_id")
        != commercial["methodology_version_record_id"]
        or selection.get("financial_dd_version_record_id")
        != financial["methodology_version_record_id"]
        or selection.get("selected_version_ids")
        != [commercial["methodology_version_id"], financial["methodology_version_id"]]
        or selection.get("selected_source_hashes")
        != [commercial["source_sha256"], financial["source_sha256"]]
        or selection.get("selected_registry_hashes")
        != [commercial["registry_sha256"], financial["registry_sha256"]]
        or selection.get("selected_version_hashes")
        != [commercial["version_sha256"], financial["version_sha256"]]
        or selection.get("selected_question_count") != 100
        or selection.get("state") != "FROZEN"
        or selection.get("immutable") is not True
    ):
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    expected_selection_hash = _canonical_sha256(
        {
            "tenant_id": selection["tenant_id"],
            "deal_id": selection["deal_id"],
            "run_id": selection["run_id"],
            "commercial_dd_version_record_id": commercial["methodology_version_record_id"],
            "commercial_methodology_version_id": commercial["methodology_version_id"],
            "commercial_source_sha256": commercial["source_sha256"],
            "commercial_registry_hash": commercial["registry_sha256"],
            "commercial_version_hash": commercial["version_sha256"],
            "financial_dd_version_record_id": financial["methodology_version_record_id"],
            "financial_methodology_version_id": financial["methodology_version_id"],
            "financial_source_sha256": financial["source_sha256"],
            "financial_registry_hash": financial["registry_sha256"],
            "financial_version_hash": financial["version_sha256"],
            "selected_question_count": 100,
        }
    )
    expected_selection_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "idis:run-methodology-selection:"
            f"{selection['tenant_id']}:{selection['run_id']}:{expected_selection_hash}",
        )
    )
    if (
        selection.get("selection_hash") != expected_selection_hash
        or selection.get("selection_id") != expected_selection_id
    ):
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")


def _task5_selection_guard_authority(artifact: Mapping[str, Any]) -> dict[str, object]:
    """Derive the exact installed selection guard from frozen v27 authority bytes."""

    _validate_v27(artifact)
    try:
        erasure = artifact["erasure_execution_registry"]["rows"]
        if not isinstance(erasure, list) or len(erasure) != 1:
            _fail("TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID")
        row = erasure[0]
        upgrade_statements = [
            bytes.fromhex(value).decode("utf-8")
            for value in row["legacy_guard_upgrade_sql_utf8_hex_tuple"]
        ]
        statement_matches = [
            statement
            for statement in upgrade_statements
            if "FUNCTION public.idis_task5_selection_guard()" in statement
        ]
        replacement_matches = [
            replacement
            for replacement in row["legacy_guard_replacement_tuple"]
            if replacement[1] == "public.idis_task5_selection_guard()"
        ]
        if len(statement_matches) != 1 or len(replacement_matches) != 1:
            _fail("TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID")
        statement = statement_matches[0]
        replacement = replacement_matches[0]
        body = statement.split(" AS $$", 1)[1].rsplit("$$", 1)[0]
        trigger = replacement[5]
    except Task8V27CensusError:
        raise
    except (IndexError, KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID") from exc
    if (
        hashlib.sha256(statement.encode("utf-8")).hexdigest()
        != _TASK5_SELECTION_GUARD_UPGRADE_SHA256
        or hashlib.sha256(body.encode("utf-8")).hexdigest() != _TASK5_SELECTION_GUARD_BODY_SHA256
        or replacement[0] != "0036"
        or replacement[2] != "PRESERVE_PRE_0038_OWNER"
        or replacement[3] != ["search_path=pg_catalog, pg_temp"]
        or trigger
        != [
            [
                "public.run_methodology_selections",
                "trg_run_methodology_selections_guard",
                "BEFORE",
                ["INSERT", "UPDATE", "DELETE"],
                "ROW",
                "O",
                False,
                False,
            ]
        ]
    ):
        _fail("TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID")
    payload = {
        "schema_version": "task8-v27-task5-selection-guard-authority-v1",
        "function_signature": "public.idis_task5_selection_guard()",
        "upgrade_statement_sha256": _TASK5_SELECTION_GUARD_UPGRADE_SHA256,
        "semantic_body_sha256": _TASK5_SELECTION_GUARD_BODY_SHA256,
        "semantic_body": body,
        "proconfig_tuple": replacement[3],
        "trigger_tuple": trigger[0],
    }
    return payload


def _observe_task5_selection_immutability(
    connection: Any,
    *,
    artifact: Mapping[str, Any],
    scope: _CaseScope,
    selection_id: str,
    expected_application_role: str,
    expected_guard_owner_role: str,
) -> dict[str, object]:
    """Catalog-bind and behaviorally prove the installed Task-5 immutable selection."""

    authority = _task5_selection_guard_authority(artifact)
    cursor = _instrumented_dbapi_cursor(connection)
    try:
        posture = _attest_connection_role_posture(cursor, expected_application_role)
        _set_tenant(cursor, scope.tenant_id)
        cursor.execute(
            """
            SELECT p.prosrc,p.prosecdef,p.proconfig,owner.rolname,p.proacl IS NULL,
                   pg_catalog.has_function_privilege(%s,p.oid,'EXECUTE'),
                   EXISTS (
                     SELECT 1 FROM pg_catalog.aclexplode(p.proacl) AS acl
                      WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
                   ),
                   function_namespace.nspname,p.proname,
                   relation_namespace.nspname,relation.relname,
                   trigger.tgname,trigger.tgenabled,trigger.tgisinternal,trigger.tgtype,
                   trigger.tgdeferrable,trigger.tginitdeferred
              FROM pg_catalog.pg_proc AS p
              JOIN pg_catalog.pg_namespace AS function_namespace
                ON function_namespace.oid=p.pronamespace
              JOIN pg_catalog.pg_roles AS owner ON owner.oid=p.proowner
              JOIN pg_catalog.pg_trigger AS trigger ON trigger.tgfoid=p.oid
              JOIN pg_catalog.pg_class AS relation ON relation.oid=trigger.tgrelid
              JOIN pg_catalog.pg_namespace AS relation_namespace
                ON relation_namespace.oid=relation.relnamespace
             WHERE function_namespace.nspname='public'
               AND p.proname='idis_task5_selection_guard'
               AND pg_catalog.pg_get_function_identity_arguments(p.oid)=''
             ORDER BY relation_namespace.nspname,relation.relname,trigger.tgname
            """,
            (expected_application_role,),
        )
        catalog_rows = cursor.fetchall()
        if len(catalog_rows) != 1:
            _fail("TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID")
        catalog = catalog_rows[0]
        observed_catalog = [
            hashlib.sha256(str(catalog[0]).encode("utf-8")).hexdigest(),
            bool(catalog[1]),
            list(catalog[2] or ()),
            str(catalog[3]),
            bool(catalog[4]),
            bool(catalog[5]),
            bool(catalog[6]),
            str(catalog[7]),
            str(catalog[8]),
            str(catalog[9]),
            str(catalog[10]),
            str(catalog[11]),
            str(catalog[12]),
            bool(catalog[13]),
            int(catalog[14]),
            bool(catalog[15]),
            bool(catalog[16]),
        ]
        expected_catalog = [
            authority["semantic_body_sha256"],
            False,
            authority["proconfig_tuple"],
            expected_guard_owner_role,
            True,
            True,
            False,
            "public",
            "idis_task5_selection_guard",
            "public",
            "run_methodology_selections",
            "trg_run_methodology_selections_guard",
            "O",
            False,
            31,
            False,
            False,
        ]
        if observed_catalog != expected_catalog:
            _fail("TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID")

        def selection_projection() -> list[list[object]]:
            cursor.execute(
                """
                SELECT selection_id::text,tenant_id::text,deal_id::text,run_id::text,
                       selection_hash,selected_question_count
                  FROM public.run_methodology_selections
                 WHERE tenant_id=%s AND deal_id=%s AND run_id=%s
                 ORDER BY selection_id
                """,
                (scope.tenant_id, scope.deal_id, scope.run_id),
            )
            return [list(row) for row in cursor.fetchall()]

        before = selection_projection()
        if len(before) != 1 or before[0][0] != selection_id:
            _fail("TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID")
        rejection_rows: list[list[str]] = []
        for operation, statement in (
            (
                "UPDATE",
                "UPDATE public.run_methodology_selections "
                "SET selection_hash=selection_hash WHERE selection_id=%s",
            ),
            (
                "DELETE",
                "DELETE FROM public.run_methodology_selections WHERE selection_id=%s",
            ),
        ):
            cursor.execute("SAVEPOINT task8_v27_task5_immutability")
            try:
                cursor.execute(statement, (uuid.UUID(selection_id),))
            except Exception as exc:
                sqlstate = str(getattr(exc, "pgcode", ""))
                reason = str(getattr(getattr(exc, "diag", None), "message_primary", ""))
                cursor.execute("ROLLBACK TO SAVEPOINT task8_v27_task5_immutability")
                cursor.execute("RELEASE SAVEPOINT task8_v27_task5_immutability")
                rejection_rows.append([operation, sqlstate, reason])
            else:
                cursor.execute("ROLLBACK TO SAVEPOINT task8_v27_task5_immutability")
                cursor.execute("RELEASE SAVEPOINT task8_v27_task5_immutability")
                _fail("TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID")
        after = selection_projection()
    finally:
        cursor.close()
    if (
        rejection_rows
        != [
            ["UPDATE", "23514", "RUN_METHODOLOGY_SELECTION_IMMUTABLE"],
            ["DELETE", "23514", "RUN_METHODOLOGY_SELECTION_IMMUTABLE"],
        ]
        or before != after
    ):
        _fail("TASK8_V27_CENSUS_TASK5_SELECTION_GUARD_INVALID")
    payload = {
        "schema_version": "task8-v27-task5-selection-immutability-proof-v1",
        "observer_role": _posture_role(posture),
        "observer_posture_sha256": _connection_posture_sha256(posture),
        "authority_statement_sha256": authority["upgrade_statement_sha256"],
        "authority_body_sha256": authority["semantic_body_sha256"],
        "catalog_sha256": _canonical_sha256(observed_catalog),
        "rejection_tuple": rejection_rows,
        "zero_mutation": True,
        "immutable": True,
        "selection_projection_sha256": _canonical_sha256(before),
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def build_task5_roundtrip_binding(
    *,
    planned_registry_identity_tuple: Sequence[Mapping[str, object]],
    reloaded_registry_identity_tuple: Sequence[Mapping[str, object]],
    planned_selection_identity: Mapping[str, object],
    reloaded_selection_identity: Mapping[str, object],
    app_observed_question_projection_sha256: str,
    app_observed_version_projection_sha256: str,
    app_observed_selection_projection_sha256: str,
    selection_immutability_sha256: str,
) -> dict[str, object]:
    """Bind importâ†’freezeâ†’reloadâ†’selection to byte-equal Task-5 identities."""

    planned_registries = [dict(row) for row in planned_registry_identity_tuple]
    reloaded_registries = [dict(row) for row in reloaded_registry_identity_tuple]
    planned_selection = dict(planned_selection_identity)
    reloaded_selection = dict(reloaded_selection_identity)
    if planned_registries != reloaded_registries or planned_selection != reloaded_selection:
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    _validate_task5_roundtrip_identity_pair(planned_registries, planned_selection)
    try:
        question_projection = [
            list(row)
            for identity in planned_registries
            for row in identity["question_identity_tuple"]
        ]
        version_projection = [
            [
                identity["methodology_version_record_id"],
                identity["methodology_version_id"],
                identity["methodology_id"],
                identity["methodology_type"],
                identity["version_label"],
                identity["source_type"],
                identity["source_alias"],
                identity["source_sha256"],
                identity["source_byte_count"],
                identity["source_contract_hash"],
                identity["source_trace_sha256"],
                identity["registry_sha256"],
                identity["version_sha256"],
                identity["question_count"],
                identity["source_section_count"],
                identity["header_row"],
                identity["status"],
            ]
            for identity in planned_registries
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID") from exc
    expected_question_projection_sha256 = _canonical_sha256(
        ["task8-v27-task5-question-db-projection-v2", question_projection]
    )
    expected_version_projection_sha256 = _canonical_sha256(
        ["task8-v27-task5-version-db-projection-v1", version_projection]
    )
    expected_selection_projection_sha256 = _canonical_sha256(
        ["task8-v27-task5-selection-db-projection-v1", planned_selection]
    )
    if (
        app_observed_question_projection_sha256 != expected_question_projection_sha256
        or app_observed_version_projection_sha256 != expected_version_projection_sha256
        or app_observed_selection_projection_sha256 != expected_selection_projection_sha256
        or not isinstance(selection_immutability_sha256, str)
        or _SHA256_RE.fullmatch(selection_immutability_sha256) is None
    ):
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    payload = {
        "schema_version": "task8-v27-task5-roundtrip-binding-v2",
        "registry_roundtrip_equal": True,
        "selection_roundtrip_equal": True,
        "planned_registry_identity_sha256": _canonical_sha256(planned_registries),
        "reloaded_registry_identity_sha256": _canonical_sha256(reloaded_registries),
        "planned_selection_identity_sha256": _canonical_sha256(planned_selection),
        "reloaded_selection_identity_sha256": _canonical_sha256(reloaded_selection),
        "question_projection_sha256": app_observed_question_projection_sha256,
        "version_projection_sha256": app_observed_version_projection_sha256,
        "selection_projection_sha256": app_observed_selection_projection_sha256,
        "selection_immutability_sha256": selection_immutability_sha256,
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def _persist_task5_seed_plan(
    connection: Any,
    *,
    artifact: Mapping[str, Any],
    expected_application_role: str,
    expected_guard_owner_role: str,
    scope: _CaseScope,
    dashboard_seed_plan: Mapping[str, Any],
    commercial_registry: Any,
    financial_registry: Any,
    cdd_byte_count: int,
    workbook_byte_count: int,
    steps: Mapping[int, Mapping[str, object]],
) -> tuple[Any, tuple[Any, ...], tuple[_NativeRootPlan, ...], str, str, str]:
    """Persist import→freeze→reload→selection and root-bound coverage."""

    from idis.api.routes.methodology_versions import _source_contract_hash
    from idis.methodology.models import MethodologyType
    from idis.models.methodology_coverage import MethodologyCoverageRecord
    from idis.persistence.repositories.methodologies import (
        PostgresMethodologiesRepository,
        methodology_source_trace_digest,
    )

    plan_rows = dashboard_seed_plan.get("row_tuple")
    expected_coverage_count = len(plan_rows) if isinstance(plan_rows, list) else 0
    if (
        dashboard_seed_plan.get("schema_version")
        not in {
            "task8-v27-dashboard-seed-plan-v2",
            "task8-v27-dashboard-seed-plan-partial-99-v1",
            "task8-v27-dashboard-requirement-seed-plan-v1",
        }
        or not isinstance(plan_rows, list)
        or expected_coverage_count not in {99, 100}
        or cdd_byte_count != _CDD_TEMPLATE_BYTE_COUNT
        or type(workbook_byte_count) is not int
        or workbook_byte_count <= 0
        or dashboard_seed_plan.get("expected_sha256")
        != _canonical_sha256(
            {key: value for key, value in dashboard_seed_plan.items() if key != "expected_sha256"}
        )
    ):
        _fail("TASK8_V27_CENSUS_QUESTION_SEED_INVALID")

    repository = PostgresMethodologiesRepository(connection, str(scope.tenant_id))
    cdd_contract_hash = _source_contract_hash(MethodologyType.COMMERCIAL_DD)
    fdd_contract_hash = _source_contract_hash(MethodologyType.FINANCIAL_DD)
    if (
        cdd_contract_hash != "e42ae90f9f8b34657ba022c056cb3a0d4ef20006b89cc71cd1dfbe82b56b9b9a"
        or fdd_contract_hash != "ed191040179c85ee2298b9d53df024600e069a8a66ba758b7ec50ddb76d1612e"
    ):
        _fail("TASK8_V27_CENSUS_SOURCE_CONTRACT_HASH_DRIFT")
    cdd_import = repository.import_registry(
        registry=commercial_registry,
        source_type="checked_in_template",
        source_alias=str(commercial_registry.current_version.source_name),
        source_byte_count=cdd_byte_count,
        source_contract_hash=cdd_contract_hash,
        source_trace_digest=methodology_source_trace_digest(commercial_registry),
        imported_by_actor_id="task8-v27-census-operator",
        import_audit_event_id=_fixture_uuid("cdd-import-audit", scope.tenant_id),
    )
    fdd_import = repository.import_registry(
        registry=financial_registry,
        source_type="operator_workbook",
        source_alias=str(financial_registry.current_version.source_name),
        source_byte_count=workbook_byte_count,
        source_contract_hash=fdd_contract_hash,
        source_trace_digest=methodology_source_trace_digest(financial_registry),
        imported_by_actor_id="task8-v27-census-operator",
        import_audit_event_id=_fixture_uuid("fdd-import-audit", scope.tenant_id),
    )
    cdd_frozen = repository.freeze_version(
        methodology_version_record_id=cdd_import.methodology_version_record_id,
        expected_source_sha256=commercial_registry.current_version.source_hash,
        expected_registry=commercial_registry,
        frozen_by_actor_id="task8-v27-census-reviewer",
        freeze_audit_event_id=_fixture_uuid("cdd-freeze-audit", scope.tenant_id),
    )
    fdd_frozen = repository.freeze_version(
        methodology_version_record_id=fdd_import.methodology_version_record_id,
        expected_source_sha256=financial_registry.current_version.source_hash,
        expected_registry=financial_registry,
        frozen_by_actor_id="task8-v27-census-reviewer",
        freeze_audit_event_id=_fixture_uuid("fdd-freeze-audit", scope.tenant_id),
    )
    cdd_frozen = repository.get_frozen_version(
        methodology_version_record_id=cdd_frozen.methodology_version_record_id
    )
    fdd_frozen = repository.get_frozen_version(
        methodology_version_record_id=fdd_frozen.methodology_version_record_id
    )
    selection = repository.create_run_selection(
        deal_id=str(scope.deal_id),
        run_id=str(scope.run_id),
        commercial_dd_version_record_id=cdd_frozen.methodology_version_record_id,
        financial_dd_version_record_id=fdd_frozen.methodology_version_record_id,
        created_by_actor_id="task8-v27-census-operator",
        selection_audit_event_id=_fixture_uuid("selection-audit", scope.run_id),
    )
    reloaded_selection = repository.load_run_selection(
        tenant_id=str(scope.tenant_id),
        deal_id=str(scope.deal_id),
        run_id=str(scope.run_id),
    )
    if reloaded_selection is None:
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    reloaded_registries = repository.load_selected_registries(
        selection=reloaded_selection,
    )
    if tuple(reloaded_registries) != (commercial_registry, financial_registry):
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")

    plan_by_coverage = {str(row["coverage_record_id"]): row for row in plan_rows}
    records: list[MethodologyCoverageRecord] = []
    question_record_by_key: dict[tuple[str, str], tuple[str, str]] = {}
    for frozen in (cdd_frozen, fdd_frozen):
        for question in frozen.registry.current_version.questions:
            question_record_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "idis:methodology-question:"
                    f"{scope.tenant_id}:{frozen.methodology_version_record_id}:"
                    f"{question.methodology_question_id}",
                )
            )
            question_record_by_key[
                (question.methodology_version_id, question.methodology_question_id)
            ] = (frozen.methodology_version_record_id, question_record_id)
            candidate = MethodologyCoverageRecord(
                tenant_id=str(scope.tenant_id),
                deal_id=str(scope.deal_id),
                run_id=str(scope.run_id),
                methodology_id=question.methodology_id,
                methodology_version_id=question.methodology_version_id,
                methodology_question_id=question.methodology_question_id,
                methodology_type=MethodologyType(question.methodology_type),
                section=question.section,
            )
            if candidate.coverage_record_id in plan_by_coverage:
                records.append(candidate)
    if len(records) != expected_coverage_count or len(plan_by_coverage) != expected_coverage_count:
        _fail("TASK8_V27_CENSUS_QUESTION_SEED_INVALID")

    root_plans: list[_NativeRootPlan] = []
    for ordinal, record in enumerate(sorted(records, key=lambda item: item.coverage_record_id)):
        row = plan_by_coverage.get(record.coverage_record_id)
        parent = question_record_by_key.get(
            (record.methodology_version_id, record.methodology_question_id)
        )
        if (
            row is None
            or parent is None
            or row.get("methodology_version_record_id") != parent[0]
            or row.get("methodology_question_record_id") != parent[1]
        ):
            _fail("TASK8_V27_CENSUS_QUESTION_SEED_INVALID")
        payload = [
            selection.selection_id,
            parent[0],
            record.methodology_version_id,
            parent[1],
            record.methodology_question_id,
            record.methodology_type.value,
        ]
        root_plans.append(
            _derive_seed_native_root(
                scope=scope,
                step=steps[4],
                native_id=record.coverage_record_id,
                output_kind="METHODOLOGY_COVERAGE_IDENTITY",
                output_schema_version="methodology-coverage-identity-v1",
                output_ordinal=ordinal,
                native_key=[record.coverage_record_id],
                native_payload=payload,
            )
        )
    raw_cursor = _instrumented_dbapi_cursor(connection)
    try:
        _set_worker_lease(raw_cursor, scope)
        for plan in root_plans:
            _persist_seed_native_root(raw_cursor, scope=scope, plan=plan)
        output_ids = {plan.native_id: plan.output_object_id for plan in root_plans}
        if expected_coverage_count == 100:
            persisted = repository.insert_coverage_records(
                records=records,
                run_output_object_ids=output_ids,
            )
        else:
            for record in records:
                parent = question_record_by_key[
                    (record.methodology_version_id, record.methodology_question_id)
                ]
                repository._insert_coverage_record(
                    record=record,
                    selection_id=selection.selection_id,
                    version_record_id=parent[0],
                    question_record_id=parent[1],
                    run_output_object_id=output_ids[record.coverage_record_id],
                )
            persisted = repository.list_coverage_records(
                deal_id=str(scope.deal_id),
                run_id=str(scope.run_id),
            )
        native_helper_sha256 = _verify_seed_native_roots(
            raw_cursor,
            scope=scope,
            plans=tuple(root_plans),
        )
        raw_cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw_cursor.close()

    expected_plan_ids = tuple(sorted(plan_by_coverage))
    if tuple(record.coverage_record_id for record in persisted) != expected_plan_ids:
        _fail("TASK8_V27_CENSUS_QUESTION_SEED_INVALID")
    rows = connection.exec_driver_sql(
        """
        SELECT coverage_record_id,selection_id::text,
               methodology_version_record_id::text,methodology_version_id,
               methodology_question_record_id::text,methodology_question_id,
               methodology_type,run_output_object_id::text
          FROM public.methodology_coverage_records
         WHERE tenant_id=%s AND deal_id=%s AND run_id=%s
         ORDER BY coverage_record_id
        """,
        (str(scope.tenant_id), str(scope.deal_id), str(scope.run_id)),
    ).fetchall()
    projection = [list(row) for row in rows]
    if len(projection) != expected_coverage_count:
        _fail("TASK8_V27_CENSUS_QUESTION_SEED_INVALID")
    question_rows = connection.exec_driver_sql(
        """
        SELECT tenant_id::text,methodology_question_record_id::text,
               methodology_version_record_id::text,methodology_version_id,
               methodology_question_id,methodology_id,methodology_type,
               question_ordinal,section,
               source_sheet_or_section,source_row_number,source_anchor_cell,
               source_sha256,semantic_row_hash,question_payload_hash,
               question_payload
          FROM public.methodology_questions
         WHERE tenant_id=%s
           AND methodology_version_record_id IN (%s::uuid,%s::uuid)
         ORDER BY methodology_type,question_ordinal,
                  methodology_question_record_id
        """,
        (
            str(scope.tenant_id),
            cdd_frozen.methodology_version_record_id,
            fdd_frozen.methodology_version_record_id,
        ),
    ).fetchall()
    safe_question_projection: list[list[object]] = []
    for row in question_rows:
        payload = row[15]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict) or _canonical_sha256(payload) != str(row[14]):
            _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
        requirements = payload.get("required_calculations")
        source_trace = payload.get("source_trace")
        if (
            not isinstance(requirements, list)
            or not isinstance(source_trace, dict)
            or source_trace.get("sheet_or_section") != row[9]
            or source_trace.get("row_number") != row[10]
            or source_trace.get("source_anchor_cell") != row[11]
            or source_trace.get("source_hash") != row[12]
        ):
            _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
        safe_question_projection.append(
            [
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                int(row[7]),
                str(row[8]),
                str(source_trace.get("source_type")),
                str(source_trace.get("source_name")),
                str(row[9]),
                None if row[10] is None else int(row[10]),
                None if row[11] is None else str(row[11]),
                str(row[12]),
                str(row[13]),
                str(row[14]),
                _canonical_sha256(payload),
                requirements,
            ]
        )
    if len(safe_question_projection) != 100:
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    planned_registry_identities = (
        dashboard_seed_plan["commercial_registry_identity"],
        dashboard_seed_plan["financial_registry_identity"],
    )
    expected_question_projection = [
        row
        for identity in planned_registry_identities
        for row in identity["question_identity_tuple"]
    ]
    if safe_question_projection != expected_question_projection:
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    question_projection_sha256 = _canonical_sha256(
        ["task8-v27-task5-question-db-projection-v2", safe_question_projection]
    )
    reloaded_registry_identities = (
        _methodology_version_record_roundtrip_identity(cdd_frozen),
        _methodology_version_record_roundtrip_identity(fdd_frozen),
    )
    version_rows = connection.exec_driver_sql(
        """
        SELECT methodology_version_record_id::text,methodology_version_id,
               methodology_id,methodology_type,version_label,source_type,
               source_alias,source_sha256,source_byte_count,source_contract_hash,
               source_trace_digest,registry_hash,version_hash,question_count,
               source_section_count,header_row,status
          FROM public.methodology_versions
         WHERE tenant_id=%s
           AND methodology_version_record_id IN (%s::uuid,%s::uuid)
         ORDER BY methodology_type,methodology_version_record_id
        """,
        (
            str(scope.tenant_id),
            cdd_frozen.methodology_version_record_id,
            fdd_frozen.methodology_version_record_id,
        ),
    ).fetchall()
    version_projection = [list(row) for row in version_rows]
    expected_version_projection = [
        [
            identity["methodology_version_record_id"],
            identity["methodology_version_id"],
            identity["methodology_id"],
            identity["methodology_type"],
            identity["version_label"],
            identity["source_type"],
            identity["source_alias"],
            identity["source_sha256"],
            identity["source_byte_count"],
            identity["source_contract_hash"],
            identity["source_trace_sha256"],
            identity["registry_sha256"],
            identity["version_sha256"],
            identity["question_count"],
            identity["source_section_count"],
            identity["header_row"],
            identity["status"],
        ]
        for identity in planned_registry_identities
    ]
    if version_projection != expected_version_projection:
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    version_projection_sha256 = _canonical_sha256(
        ["task8-v27-task5-version-db-projection-v1", version_projection]
    )
    selection_rows = connection.exec_driver_sql(
        """
        SELECT s.selection_id::text,s.tenant_id::text,s.deal_id::text,s.run_id::text,
               s.commercial_dd_version_record_id::text,
               s.financial_dd_version_record_id::text,
               s.commercial_methodology_version_id,
               s.financial_methodology_version_id,
               cv.source_sha256,fv.source_sha256,
               cv.registry_hash,fv.registry_hash,
               s.commercial_version_hash,s.financial_version_hash,
               s.selection_hash,s.selected_question_count,
               cv.status,fv.status
          FROM public.run_methodology_selections AS s
          JOIN public.methodology_versions AS cv
            ON cv.tenant_id=s.tenant_id
           AND cv.methodology_version_record_id=s.commercial_dd_version_record_id
          JOIN public.methodology_versions AS fv
            ON fv.tenant_id=s.tenant_id
           AND fv.methodology_version_record_id=s.financial_dd_version_record_id
         WHERE s.tenant_id=%s AND s.deal_id=%s AND s.run_id=%s
        """,
        (str(scope.tenant_id), str(scope.deal_id), str(scope.run_id)),
    ).fetchall()
    if (
        len(selection_rows) != 1
        or str(selection_rows[0][16]) != "FROZEN"
        or str(selection_rows[0][17]) != "FROZEN"
    ):
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    selection_row = selection_rows[0]
    selection_immutability = _observe_task5_selection_immutability(
        connection,
        artifact=artifact,
        scope=scope,
        selection_id=str(selection_row[0]),
        expected_application_role=expected_application_role,
        expected_guard_owner_role=expected_guard_owner_role,
    )
    observed_selection_identity = {
        "selection_id": str(selection_row[0]),
        "tenant_id": str(selection_row[1]),
        "deal_id": str(selection_row[2]),
        "run_id": str(selection_row[3]),
        "commercial_dd_version_record_id": str(selection_row[4]),
        "financial_dd_version_record_id": str(selection_row[5]),
        "selection_hash": str(selection_row[14]),
        "selected_question_count": int(selection_row[15]),
        "selected_version_ids": [str(selection_row[6]), str(selection_row[7])],
        "selected_source_hashes": [str(selection_row[8]), str(selection_row[9])],
        "selected_registry_hashes": [str(selection_row[10]), str(selection_row[11])],
        "selected_version_hashes": [str(selection_row[12]), str(selection_row[13])],
        "state": "FROZEN",
        "immutable": selection_immutability["immutable"] is True,
    }
    expected_selection_identity = _selection_roundtrip_identity(reloaded_selection)
    if observed_selection_identity != expected_selection_identity:
        _fail("TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID")
    selection_projection_sha256 = _canonical_sha256(
        [
            "task8-v27-task5-selection-db-projection-v1",
            observed_selection_identity,
        ]
    )
    roundtrip = build_task5_roundtrip_binding(
        planned_registry_identity_tuple=planned_registry_identities,
        reloaded_registry_identity_tuple=reloaded_registry_identities,
        planned_selection_identity=_selection_roundtrip_identity(selection),
        reloaded_selection_identity=expected_selection_identity,
        app_observed_question_projection_sha256=question_projection_sha256,
        app_observed_version_projection_sha256=version_projection_sha256,
        app_observed_selection_projection_sha256=selection_projection_sha256,
        selection_immutability_sha256=str(selection_immutability["expected_sha256"]),
    )
    projection_sha256 = _canonical_sha256(
        [
            "task8-v27-dashboard-seed-db-projection-v2",
            projection,
            roundtrip["expected_sha256"],
        ]
    )
    return (
        selection,
        tuple(persisted),
        tuple(root_plans),
        projection_sha256,
        native_helper_sha256,
        str(roundtrip["expected_sha256"]),
    )


def _persist_adversarial_requirement_task5_seed(
    connection: Any,
    *,
    scope: _CaseScope,
    seed: _RequirementSeedSpec,
    cdd_byte_count: int,
    workbook_byte_count: int,
    steps: Mapping[int, Mapping[str, object]],
) -> tuple[Any, tuple[Any, ...], tuple[_NativeRootPlan, ...], str, str, str]:
    """Persist one narrowly labeled malformed-requirement fixture with guards on."""

    from idis.api.routes.methodology_versions import _source_contract_hash
    from idis.methodology.ids import normalize_text
    from idis.methodology.models import MethodologyType
    from idis.models.methodology_coverage import MethodologyCoverageRecord
    from idis.persistence.repositories.methodologies import (
        PostgresMethodologiesRepository,
        methodology_source_trace_digest,
    )

    plan_rows = seed.dashboard_seed_plan.get("row_tuple")
    raw_financial = dict(seed.financial_raw_registry)
    if (
        seed.dashboard_seed_plan.get("schema_version")
        != "task8-v27-dashboard-requirement-seed-plan-v1"
        or not isinstance(plan_rows, list)
        or len(plan_rows) != 100
        or cdd_byte_count != _CDD_TEMPLATE_BYTE_COUNT
        or seed.dashboard_seed_plan.get("expected_sha256")
        != _canonical_sha256(
            {
                key: value
                for key, value in seed.dashboard_seed_plan.items()
                if key != "expected_sha256"
            }
        )
    ):
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    repository = PostgresMethodologiesRepository(connection, str(scope.tenant_id))
    cdd_contract_hash = _source_contract_hash(MethodologyType.COMMERCIAL_DD)
    fdd_contract_hash = _source_contract_hash(MethodologyType.FINANCIAL_DD)
    cdd_import = repository.import_registry(
        registry=seed.commercial_registry,
        source_type="checked_in_template",
        source_alias=str(seed.commercial_registry.current_version.source_name),
        source_byte_count=cdd_byte_count,
        source_contract_hash=cdd_contract_hash,
        source_trace_digest=methodology_source_trace_digest(seed.commercial_registry),
        imported_by_actor_id="task8-v27-census-operator",
        import_audit_event_id=_fixture_uuid("requirement-cdd-import", scope.tenant_id),
    )
    cdd_frozen = repository.freeze_version(
        methodology_version_record_id=cdd_import.methodology_version_record_id,
        expected_source_sha256=seed.commercial_registry.current_version.source_hash,
        expected_registry=seed.commercial_registry,
        frozen_by_actor_id="task8-v27-census-reviewer",
        freeze_audit_event_id=_fixture_uuid("requirement-cdd-freeze", scope.tenant_id),
    )

    try:
        fdd_version = raw_financial["versions"][0]
        fdd_questions = fdd_version["questions"]
        fdd_identity = _raw_registry_identity(raw_financial)
        source_hash = str(fdd_version["source_hash"])
        fdd_record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "idis:methodology-version:"
                f"{scope.tenant_id}:{fdd_version['methodology_version_id']}:{source_hash}",
            )
        )
        source_sections = {
            str(question["source_trace"]["sheet_or_section"]) for question in fdd_questions
        }
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID") from exc
    connection.exec_driver_sql(
        """
        INSERT INTO public.methodology_versions(
            tenant_id,methodology_version_record_id,methodology_version_id,
            methodology_id,methodology_type,version_label,source_type,source_alias,
            source_sha256,source_byte_count,source_contract_hash,source_trace_digest,
            registry_hash,version_hash,question_count,source_section_count,header_row,
            status,imported_by_actor_id,import_audit_event_id,imported_at
        ) VALUES (
            %s,%s,%s,'financial_dd','financial_dd',%s,'operator_workbook',%s,
            %s,%s,%s,%s,%s,%s,79,%s,2,'IMPORTED',%s,%s,%s
        )
        """,
        (
            str(scope.tenant_id),
            fdd_record_id,
            str(fdd_version["methodology_version_id"]),
            str(fdd_version["version_label"]),
            str(fdd_version["source_name"]),
            source_hash,
            workbook_byte_count,
            fdd_contract_hash,
            fdd_identity["source_trace_sha256"],
            fdd_identity["registry_sha256"],
            fdd_identity["version_sha256"],
            len(source_sections),
            "task8-v27-census-operator",
            _fixture_uuid("requirement-fdd-import", scope.tenant_id),
            _FIXTURE_OCCURRED_AT,
        ),
    )
    question_record_by_key: dict[tuple[str, str], tuple[str, str]] = {}
    for ordinal, question in enumerate(fdd_questions, start=1):
        question_id = str(question["methodology_question_id"])
        question_record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"idis:methodology-question:{scope.tenant_id}:{fdd_record_id}:{question_id}",
            )
        )
        trace = question["source_trace"]
        semantic_row_hash = _canonical_sha256(
            {
                "methodology_type": "financial_dd",
                "section": normalize_text(str(question["section"])),
                "sheet_or_source_section": normalize_text(
                    str(question.get("sheet_or_source_section") or "")
                ),
                "term": normalize_text(str(question.get("term") or "")),
                "nature": normalize_text(str(question.get("nature") or "")),
                "line_item": normalize_text(str(question.get("line_item") or "")),
                "question_text": normalize_text(str(question["question_text"])),
            }
        )
        payload_sha256 = _canonical_sha256(question)
        connection.exec_driver_sql(
            """
            INSERT INTO public.methodology_questions(
                tenant_id,methodology_question_record_id,
                methodology_version_record_id,methodology_version_id,
                methodology_question_id,methodology_id,methodology_type,
                question_ordinal,section,source_sheet_or_section,source_row_number,
                source_anchor_cell,source_sha256,semantic_row_hash,
                question_payload_hash,question_payload,created_at
            ) VALUES (
                %s,%s,%s,%s,%s,'financial_dd','financial_dd',%s,%s,%s,%s,%s,
                %s,%s,%s,%s::jsonb,%s
            )
            """,
            (
                str(scope.tenant_id),
                question_record_id,
                fdd_record_id,
                str(fdd_version["methodology_version_id"]),
                question_id,
                ordinal,
                str(question["section"]),
                str(trace["sheet_or_section"]),
                int(trace["row_number"]),
                str(trace["source_anchor_cell"]),
                source_hash,
                semantic_row_hash,
                payload_sha256,
                json.dumps(question, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                _FIXTURE_OCCURRED_AT,
            ),
        )
        question_record_by_key[(str(fdd_version["methodology_version_id"]), question_id)] = (
            fdd_record_id,
            question_record_id,
        )
    connection.exec_driver_sql(
        """
        UPDATE public.methodology_versions
           SET status='FROZEN',frozen_by_actor_id=%s,
               freeze_audit_event_id=%s,frozen_at=%s
         WHERE tenant_id=%s AND methodology_version_record_id=%s
        """,
        (
            "task8-v27-census-reviewer",
            _fixture_uuid("requirement-fdd-freeze", scope.tenant_id),
            _FIXTURE_OCCURRED_AT,
            str(scope.tenant_id),
            fdd_record_id,
        ),
    )

    selection_hash = _canonical_sha256(
        {
            "tenant_id": str(scope.tenant_id),
            "deal_id": str(scope.deal_id),
            "run_id": str(scope.run_id),
            "commercial_dd_version_record_id": cdd_frozen.methodology_version_record_id,
            "commercial_methodology_version_id": cdd_frozen.methodology_version_id,
            "commercial_source_sha256": cdd_frozen.source_sha256,
            "commercial_registry_hash": cdd_frozen.registry_hash,
            "commercial_version_hash": cdd_frozen.version_hash,
            "financial_dd_version_record_id": fdd_record_id,
            "financial_methodology_version_id": fdd_version["methodology_version_id"],
            "financial_source_sha256": source_hash,
            "financial_registry_hash": fdd_identity["registry_sha256"],
            "financial_version_hash": fdd_identity["version_sha256"],
            "selected_question_count": 100,
        }
    )
    selection_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"idis:run-methodology-selection:{scope.tenant_id}:{scope.run_id}:{selection_hash}",
        )
    )
    connection.exec_driver_sql(
        """
        INSERT INTO public.run_methodology_selections(
            selection_id,tenant_id,deal_id,run_id,
            commercial_dd_version_record_id,commercial_methodology_version_id,
            commercial_version_hash,financial_dd_version_record_id,
            financial_methodology_version_id,financial_version_hash,selection_hash,
            selected_question_count,created_by_actor_id,selection_audit_event_id,created_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,100,%s,%s,%s)
        """,
        (
            selection_id,
            str(scope.tenant_id),
            str(scope.deal_id),
            str(scope.run_id),
            cdd_frozen.methodology_version_record_id,
            cdd_frozen.methodology_version_id,
            cdd_frozen.version_hash,
            fdd_record_id,
            str(fdd_version["methodology_version_id"]),
            str(fdd_identity["version_sha256"]),
            selection_hash,
            "task8-v27-census-operator",
            _fixture_uuid("requirement-selection", scope.run_id),
            _FIXTURE_OCCURRED_AT,
        ),
    )

    for question in cdd_frozen.registry.current_version.questions:
        question_record_by_key[
            (question.methodology_version_id, question.methodology_question_id)
        ] = (
            cdd_frozen.methodology_version_record_id,
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "idis:methodology-question:"
                    f"{scope.tenant_id}:{cdd_frozen.methodology_version_record_id}:"
                    f"{question.methodology_question_id}",
                )
            ),
        )
    records = [
        MethodologyCoverageRecord(
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            run_id=str(scope.run_id),
            methodology_id=question.methodology_id,
            methodology_version_id=question.methodology_version_id,
            methodology_question_id=question.methodology_question_id,
            methodology_type=question.methodology_type,
            section=question.section,
        )
        for registry in (seed.commercial_registry, seed.financial_registry)
        for question in registry.current_version.questions
    ]
    plan_by_coverage = {
        str(row["coverage_record_id"]): row for row in seed.dashboard_seed_plan["row_tuple"]
    }
    if len(records) != 100 or set(plan_by_coverage) != {
        record.coverage_record_id for record in records
    }:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    root_plans = tuple(
        _derive_seed_native_root(
            scope=scope,
            step=steps[4],
            native_id=record.coverage_record_id,
            output_kind="METHODOLOGY_COVERAGE_IDENTITY",
            output_schema_version="methodology-coverage-identity-v1",
            output_ordinal=ordinal,
            native_key=[record.coverage_record_id],
            native_payload=[
                selection_id,
                question_record_by_key[
                    (record.methodology_version_id, record.methodology_question_id)
                ][0],
                record.methodology_version_id,
                question_record_by_key[
                    (record.methodology_version_id, record.methodology_question_id)
                ][1],
                record.methodology_question_id,
                record.methodology_type.value,
            ],
        )
        for ordinal, record in enumerate(sorted(records, key=lambda item: item.coverage_record_id))
    )
    raw_cursor = _instrumented_dbapi_cursor(connection)
    try:
        _attest_connection_role_posture(raw_cursor, _role_from_url(str(connection.engine.url)))
        _set_worker_lease(raw_cursor, scope)
        for root in root_plans:
            _persist_seed_native_root(raw_cursor, scope=scope, plan=root)
        roots = {root.native_id: root.output_object_id for root in root_plans}
        vector = seed.vector
        for record in records:
            parent = question_record_by_key[
                (record.methodology_version_id, record.methodology_question_id)
            ]
            insert_record = record
            if (
                vector["case_id"] == "CROSS_VERSION_JOIN"
                and record.methodology_question_id == seed.focus_question_id
            ):
                insert_record = MethodologyCoverageRecord.model_validate(
                    {
                        **record.model_dump(mode="json"),
                        "methodology_version_id": vector["joined_methodology_version_id"],
                    }
                )
            repository._insert_coverage_record(
                record=insert_record,
                selection_id=selection_id,
                version_record_id=parent[0],
                question_record_id=parent[1],
                run_output_object_id=roots[record.coverage_record_id],
            )
        native_helper_sha256 = _verify_seed_native_roots(
            raw_cursor,
            scope=scope,
            plans=root_plans,
        )
        raw_cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw_cursor.close()
    persisted = repository.list_coverage_records(
        deal_id=str(scope.deal_id),
        run_id=str(scope.run_id),
    )
    if len(persisted) != 100:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    projection_sha256 = _canonical_sha256(
        [
            "task8-v27-adversarial-requirement-task5-projection-v1",
            seed.dashboard_seed_plan["expected_sha256"],
            fdd_identity,
            selection_hash,
            sorted(record.coverage_record_id for record in persisted),
        ]
    )
    return (
        _RequirementSelection(selection_id=selection_id),
        tuple(persisted),
        root_plans,
        projection_sha256,
        native_helper_sha256,
        _canonical_sha256(
            [
                "task8-v27-adversarial-task5-roundtrip-v1",
                seed.dashboard_seed_plan["expected_sha256"],
                fdd_identity,
                selection_hash,
            ]
        ),
    )


def _validate_v27(artifact: Mapping[str, Any]) -> None:
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict):
        _fail("TASK8_V27_CENSUS_ARTIFACT_INVALID")
    if (
        metadata.get("artifact_schema") != "full-strict-v27-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v27"
        or metadata.get("append_frozen") is not True
        or metadata.get("activation_status") != "INACTIVE"
    ):
        _fail("TASK8_V27_CENSUS_ARTIFACT_INVALID")
    section = artifact.get("task8_fdd_contract")
    if not isinstance(section, dict):
        _fail("TASK8_V27_CENSUS_ARTIFACT_INVALID")
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
        _fail("TASK8_V27_CENSUS_ARTIFACT_INVALID")
    authority_registry = artifact.get("sql_authority_registry")
    if not isinstance(authority_registry, dict):
        _fail("TASK8_V27_CENSUS_ARTIFACT_INVALID")
    authority_ids = {
        row.get("authority_id")
        for row in authority_registry.get("rows", [])
        if isinstance(row, dict)
    }
    if not set(_AUTHORITY_IDS[:2]).issubset(authority_ids):
        _fail("TASK8_V27_CENSUS_ARTIFACT_INVALID")


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
        raise Task8V27CensusError(code) from exc
    return tuple(decoded)


def _guard_acl_function_row_hash(row: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    return hashlib.sha256(_ACL_FUNCTION_HASH_DOMAIN + _canonical_json(payload)).hexdigest()


def _guard_select_sql(function_row: Mapping[str, Any]) -> tuple[str, ...]:
    owner = function_row.get("owner_role")
    if owner != _GUARD_OWNER:
        _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
    direct = function_row.get("direct_read_column_tuple")
    if not isinstance(direct, list) or not direct:
        _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
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
            _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
        relation = item[0]
        columns = tuple(item[1])
        key = (relation, columns)
        if key in seen:
            _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
        seen.add(key)
        statements.append(
            f"GRANT SELECT ({','.join(columns)}) ON TABLE public.{relation} TO {_GUARD_OWNER}"
        )
    return tuple(statements)


def build_hash_bound_authority_execution(v27: Mapping[str, Any]) -> dict[str, Any]:
    """Decode and bind the only SQL bytes the Task-8 census may install."""

    _validate_v27(v27)
    sql_registry = v27["sql_authority_registry"]
    rows = sql_registry.get("rows")
    guard_registry = sql_registry.get("guard_function_acl_registry")
    if not isinstance(rows, list) or not isinstance(guard_registry, dict):
        _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
    guard_rows = guard_registry.get("function_rows")
    if not isinstance(guard_rows, list):
        _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")

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
            _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
        authority = authority_matches[0]
        guard_matches.sort(key=lambda row: row.get("function_ordinal", -1))
        if authority.get("expected_sha256") != _SQL_AUTHORITY_ROW_SHA256[authority_id] or any(
            guard.get("expected_sha256")
            != _GUARD_FUNCTION_ROW_SHA256.get(str(guard.get("function_signature")))
            or guard.get("expected_sha256") != _guard_acl_function_row_hash(guard)
            for guard in guard_matches
        ):
            _fail("TASK8_V27_CENSUS_AUTHORITY_HASH_INVALID")
        body_hex = authority.get("body_sql_utf8_hex_tuple")
        _decode_sql_tuple(body_hex, code="TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID")
        target_tuple = authority.get("target_tuple")
        if not isinstance(target_tuple, list) or not target_tuple:
            _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
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
                _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
            if function_kind == "HELPER":
                if target_triggers or event_coordinates:
                    _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
            elif target_triggers != target_tuple or len(event_coordinates) != 3 * len(
                target_triggers
            ):
                _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
            owner_sql_hex = guard.get("owner_sql_utf8_hex")
            if not isinstance(owner_sql_hex, str):
                _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
            _decode_sql_tuple([owner_sql_hex], code="TASK8_V27_CENSUS_GUARD_ACL_INVALID")
            function_acl_hex = guard.get("acl_sql_utf8_hex_tuple")
            _decode_sql_tuple(function_acl_hex, code="TASK8_V27_CENSUS_GUARD_ACL_INVALID")
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
                            "task8-v27-guard-select-tuple-v1",
                            authority_id,
                            guard.get("function_signature"),
                            guard_select,
                        ]
                    ),
                }
            )
        downgrade_hex: object = []
        if authority_id == _AUTHORITY_IDS[1]:
            partial_contract = v27.get("task8_fdd_contract", {}).get("partial_exact_one_contract")
            if not isinstance(partial_contract, Mapping):
                _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
            downgrade_hex = partial_contract.get("downgrade_sql_utf8_hex_tuple")
            _decode_sql_tuple(
                downgrade_hex,
                code="TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID",
            )
        sql_bindings.append(
            {
                "authority_id": authority_id,
                "authority_sha256": authority["expected_sha256"],
                "body_sql_utf8_hex_tuple": body_hex,
                "target_tuple": target_tuple,
                "body_tuple_sha256": _canonical_sha256(
                    [
                        "task8-v27-authority-body-tuple-v1",
                        authority_id,
                        body_hex,
                    ]
                ),
                "downgrade_sql_utf8_hex_tuple": downgrade_hex,
                "downgrade_tuple_sha256": _canonical_sha256(
                    [
                        "task8-v27-authority-downgrade-tuple-v1",
                        authority_id,
                        downgrade_hex,
                    ]
                ),
                "guard_function_acl_binding_tuple": guard_bindings,
            }
        )

    erasure_section = v27.get("erasure_execution_registry")
    if not isinstance(erasure_section, dict):
        _fail("TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure_rows = erasure_section.get("rows")
    if not isinstance(erasure_rows, list) or len(erasure_rows) != 1:
        _fail("TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure = erasure_rows[0]
    if (
        not isinstance(erasure, dict)
        or erasure.get("expected_sha256") != _ERASURE_EXECUTION_ROW_SHA256
        or erasure_section.get("expected_sha256") != _ERASURE_EXECUTION_REGISTRY_SHA256
    ):
        _fail("TASK8_V27_CENSUS_ERASURE_AUTHORITY_HASH_INVALID")
    body_hex = erasure.get("current_body_sql_utf8_hex_tuple")
    acl_hex = erasure.get("current_acl_sql_utf8_hex_tuple")
    body_sql = _decode_sql_tuple(
        body_hex,
        code="TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    acl_sql = _decode_sql_tuple(
        acl_hex,
        code="TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID",
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
        _fail("TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure_binding = {
        "execution_id": erasure.get("execution_id"),
        "authority_sha256": erasure["expected_sha256"],
        "current_body_sql_utf8_hex_tuple": body_hex,
        "body_tuple_sha256": _canonical_sha256(
            [
                "task8-v27-erasure-body-tuple-v1",
                erasure.get("execution_id"),
                body_hex,
            ]
        ),
        "current_acl_sql_utf8_hex_tuple": acl_hex,
        "acl_tuple_sha256": _canonical_sha256(
            [
                "task8-v27-erasure-acl-tuple-v1",
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
            ["task8-v27-hash-bound-authority-execution-v1", payload]
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
        != _canonical_sha256(["task8-v27-hash-bound-authority-execution-v1", payload])
    ):
        _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
    statements: list[str] = []
    authority_binding: list[dict[str, Any]] = []
    authority_install_statements: dict[str, list[str]] = {}
    authority_downgrade_statements: dict[str, tuple[str, ...]] = {}
    sql_authorities = bundle.get("sql_authorities")
    erasure = bundle.get("erasure_authority")
    if not isinstance(sql_authorities, list) or not isinstance(erasure, dict):
        _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
    for row in sql_authorities:
        if not isinstance(row, dict):
            _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
        body = _decode_sql_tuple(
            row.get("body_sql_utf8_hex_tuple"),
            code="TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID",
        )
        downgrade = (
            _decode_sql_tuple(
                row.get("downgrade_sql_utf8_hex_tuple"),
                code="TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID",
            )
            if row.get("downgrade_sql_utf8_hex_tuple")
            else ()
        )
        if row.get("downgrade_tuple_sha256") != _canonical_sha256(
            [
                "task8-v27-authority-downgrade-tuple-v1",
                row.get("authority_id"),
                row.get("downgrade_sql_utf8_hex_tuple"),
            ]
        ):
            _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
        guard_bindings = row.get("guard_function_acl_binding_tuple")
        if not isinstance(guard_bindings, list) or not guard_bindings:
            _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
        guard_receipts: list[dict[str, Any]] = []
        row_statements = list(body)
        for guard in guard_bindings:
            if not isinstance(guard, dict):
                _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
            owner = _decode_sql_tuple(
                [guard.get("owner_sql_utf8_hex")],
                code="TASK8_V27_CENSUS_GUARD_ACL_INVALID",
            )
            acl = _decode_sql_tuple(
                guard.get("function_acl_sql_utf8_hex_tuple"),
                code="TASK8_V27_CENSUS_GUARD_ACL_INVALID",
            )
            guard_select = guard.get("guard_select_sql_tuple")
            if not isinstance(guard_select, list) or any(
                not isinstance(statement, str) for statement in guard_select
            ):
                _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
            if guard.get("guard_select_tuple_sha256") != _canonical_sha256(
                [
                    "task8-v27-guard-select-tuple-v1",
                    row.get("authority_id"),
                    guard.get("function_signature"),
                    tuple(guard_select),
                ]
            ):
                _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
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
        code="TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    erasure_acl = _decode_sql_tuple(
        erasure.get("current_acl_sql_utf8_hex_tuple"),
        code="TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID",
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
        _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
    partial_cycle_sha256 = _canonical_sha256(
        [
            "task8-v27-partial-restore-reinstall-v1",
            list(partial_restore),
            partial_reinstall,
        ]
    )
    return {
        "schema_version": "task8-v27-authority-execution-binding-receipt-v1",
        "bundle_sha256": bundle["expected_sha256"],
        "authority_binding_tuple": authority_binding,
        "partial_restore_reinstall_statement_tuple_sha256": partial_cycle_sha256,
        "executed_statement_tuple_sha256": _canonical_sha256(
            ["task8-v27-executed-authority-sql-v1", statements]
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
        _fail("TASK8_V27_CENSUS_ARTIFACT_ROW_INVALID")
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
                _fail("TASK8_V27_CENSUS_FUTURE_DDL_INVALID")
            seen.add(item[1])
            result.append((len(result), item[1], item[2]))
    if not result:
        _fail("TASK8_V27_CENSUS_FUTURE_DDL_INVALID")
    return tuple(f"{name} {definition}" for _ordinal, name, definition in result)


def _table_constraint_tuple(row: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for item in row.get("check_tuple", ()):
        if not isinstance(item, list) or len(item) != 2:
            _fail("TASK8_V27_CENSUS_FUTURE_DDL_INVALID")
        result.append(f"CONSTRAINT {item[0]} CHECK ({item[1]})")
    unique_rows = row.get("unique_tuple", ())
    for item in unique_rows:
        if not isinstance(item, list) or len(item) != 3 or not item[1]:
            _fail("TASK8_V27_CENSUS_FUTURE_DDL_INVALID")
        columns = ",".join(item[1])
        authority_kind = "PRIMARY KEY" if str(item[0]).startswith("pk_") else "UNIQUE"
        result.append(f"CONSTRAINT {item[0]} {authority_kind} ({columns})")
    references = row.get("reference_fields", row.get("foreign_key_tuple", ()))
    for item in references:
        if not isinstance(item, list) or len(item) != 8:
            _fail("TASK8_V27_CENSUS_FUTURE_DDL_INVALID")
        name, columns, parent, _authority, parent_columns, on_update, on_delete, _index = item
        result.append(
            f"CONSTRAINT {name} FOREIGN KEY ({','.join(columns)}) "
            f"REFERENCES public.{parent} ({','.join(parent_columns)}) "
            f"ON UPDATE {on_update} ON DELETE {on_delete}"
        )
    return tuple(result)


def build_future_table_scaffold(v27: Mapping[str, Any], application_role: str) -> dict[str, Any]:
    """Derive the two Task-8 future tables from the reviewed registry bytes."""

    _validate_v27(v27)
    role = _quoted_identifier(application_role)
    native = _artifact_row(
        v27,
        "future_native_authority_registry",
        field="planned_table",
        value="methodology_truth_dashboard_rows",
    )
    child = _artifact_row(
        v27,
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
                _fail("TASK8_V27_CENSUS_FUTURE_DDL_INVALID")
            index_rows.append([reference[7], reference[1], None, False])
        seen_indexes: set[str] = set()
        for index in index_rows:
            if not isinstance(index, list) or len(index) != 4:
                _fail("TASK8_V27_CENSUS_FUTURE_DDL_INVALID")
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
            _fail("TASK8_V27_CENSUS_FUTURE_DDL_INVALID")
        for statement in rls[0]:
            if not isinstance(statement, str):
                _fail("TASK8_V27_CENSUS_FUTURE_DDL_INVALID")
            for part in statement.replace("<configured_app_role>", role).split(";"):
                if part.strip():
                    statements.append(part.strip())
    payload = {
        "schema_version": "task8-v27-future-table-scaffold-v1",
        "relation_tuple": [
            "methodology_truth_dashboard_rows",
            "methodology_truth_dashboard_root_links",
        ],
        "statement_tuple": statements,
        "installed_tenant_rls_table_count": 66,
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(["task8-v27-future-table-scaffold-v1", payload]),
    }


def build_application_behavior_grants(
    v27: Mapping[str, Any],
    application_role: str,
) -> dict[str, Any]:
    """Bind the reviewed 0038 app grants needed by the behavior fixture."""

    _validate_v27(v27)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", application_role) is None:
        _fail("TASK8_V27_CENSUS_ROLE_INVALID")
    relations = ("run_output_objects", "run_step_outputs")
    statements: list[str] = []
    rows = v27.get("current_ddl_registry", {}).get("rows", ())
    for relation in relations:
        matches = [
            row for row in rows if isinstance(row, Mapping) and row.get("table_name") == relation
        ]
        if len(matches) != 1:
            _fail("TASK8_V27_CENSUS_APPLICATION_GRANT_INVALID")
        rls_tuple = matches[0].get("rls_tuple")
        if not isinstance(rls_tuple, list):
            _fail("TASK8_V27_CENSUS_APPLICATION_GRANT_INVALID")
        expected = f"GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE {relation} TO <configured_app_role>"
        if rls_tuple.count(expected) != 1:
            _fail("TASK8_V27_CENSUS_APPLICATION_GRANT_INVALID")
        statements.append(expected.replace("<configured_app_role>", application_role))
    payload = {
        "schema_version": "task8-v27-application-behavior-grants-v1",
        "relation_tuple": list(relations),
        "statement_tuple": statements,
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(["task8-v27-application-behavior-grants-v1", payload]),
    }


def build_zero_mutation_projection_manifest(v27: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the rejection projection to CURRENT48 plus Task-8/control rows."""

    _validate_v27(v27)
    erasure = v27["erasure_execution_registry"]["rows"][0]
    current = list(erasure["current_relation_tuple"])
    payload = {
        "schema_version": "task8-v27-zero-mutation-projection-v1",
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
        "expected_sha256": _canonical_sha256(["task8-v27-zero-mutation-projection-v1", payload]),
    }


def build_advisory_lock_binding(v27: Mapping[str, Any]) -> dict[str, Any]:
    """Prove dashboard completion and erasure use one exact advisory key."""

    _validate_v27(v27)
    dashboard = _artifact_row(
        v27,
        "sql_authority_registry",
        field="authority_id",
        value=_AUTHORITY_IDS[0],
    )
    erasure = v27["erasure_execution_registry"]["rows"][0]
    dashboard_sql = " ".join(
        _decode_sql_tuple(
            dashboard["body_sql_utf8_hex_tuple"],
            code="TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID",
        )
    )
    erasure_sql = " ".join(
        _decode_sql_tuple(
            erasure["current_body_sql_utf8_hex_tuple"],
            code="TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID",
        )
    )
    pattern = re.compile(r"pg_catalog\.hashtextextended\(\s*'([^']+)'\|\|[^,]+,(\d+)\s*\)")
    dashboard_matches = pattern.findall(dashboard_sql)
    erasure_matches = pattern.findall(erasure_sql)
    if len(dashboard_matches) != 1 or len(erasure_matches) != 1:
        _fail("TASK8_V27_CENSUS_ADVISORY_LOCK_INVALID")
    if dashboard_matches[0] != erasure_matches[0]:
        _fail("TASK8_V27_CENSUS_ADVISORY_LOCK_INVALID")
    prefix, seed = dashboard_matches[0]
    payload = {
        "schema_version": "task8-v27-shared-advisory-lock-binding-v1",
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
            ["task8-v27-shared-advisory-lock-binding-v1", payload]
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

    if (coverage_row_count, financial_coverage_row_count) not in {(100, 79), (99, 78)}:
        _fail("TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    if semantic_case == "GLOBAL_DASHBOARD_ROW_MISSING":
        return _GlobalDashboardCensusPlan(semantic_case, 100, 79, True, None)
    if semantic_case == "GLOBAL_COVERAGE_SOURCE_COUNT_99":
        return _GlobalDashboardCensusPlan(semantic_case, 99, 78, False, None)
    if semantic_case == "GLOBAL_EXTRA_NONCOVERAGE_ROW":
        return _GlobalDashboardCensusPlan(semantic_case, 100, 79, False, "NONCOVERAGE")
    if semantic_case == "GLOBAL_DUPLICATE_COVERAGE_ROW":
        return _GlobalDashboardCensusPlan(semantic_case, 100, 79, False, "DUPLICATE")
    _fail("TASK8_V27_CENSUS_GLOBAL_CASE_INVALID")


def _requirement_matrix() -> dict[str, object]:
    """Return the frozen v3 legal/negative requirement discriminator matrix."""

    legal = (
        "LEGACY_EMPTY",
        "LEGACY_TWO_FIELD",
        "POLICY_V2_EMPTY",
        "POLICY_V2_TEN_LITERAL_TUPLES",
    )
    negative = (
        "LEGACY_FOUR_FIELD",
        "POLICY_V2_TWO_FIELD",
        "UNKNOWN_FORMULA_TYPE",
        "ORDINAL_DRIFT",
        "SCOPE_DRIFT",
        "UNDECLARED_FDD_CALCULATION_ROOT",
        "MIXED_SHAPES",
        "POLICY_V2_EXTRA_FIELD",
        "POLICY_V2_MISSING_FIELD",
        "LEGACY_EXTRA_FIELD",
        "LEGACY_MISSING_FIELD",
        "CROSS_VERSION_JOIN",
        "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE",
    )
    payload = {
        "schema_version": "task8-v27-dashboard-requirement-matrix-v1",
        "execution_role": "application",
        "legal_case_id_tuple": list(legal),
        "negative_case_id_tuple": list(negative),
        "legal_case_count": len(legal),
        "negative_case_count": len(negative),
        "negative_zero_mutation_count": len(negative),
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def _requirement_case_vectors(v27: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    """Build the closed v3 vectors from the frozen contract literal."""

    _validate_v27(v27)
    contract = v27["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"]
    policy_version = contract["policy_v2_methodology_version_id"]
    approved = contract["policy_v2_requirement_literal_tuple"]
    if not isinstance(policy_version, str) or not isinstance(approved, list) or len(approved) != 10:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    policy_requirements = [
        {
            "formula_ordinal": int(row[0]),
            "formula_type": str(row[1]),
            "required": True,
            "scope": str(row[2]),
        }
        for row in approved
    ]
    legacy_version = "financial_dd:task8-v27-legacy-requirement-census-v1"
    legacy_requirement = {"calc_type": "LTV_CAC_RATIO", "required": False}
    first_policy = dict(policy_requirements[0])

    def case(
        case_id: str,
        *,
        version: str,
        requirements: list[dict[str, object]],
        calculations: list[str],
        accepted: bool,
        joined_version: str | None = None,
    ) -> dict[str, object]:
        return {
            "case_id": case_id,
            "methodology_version_id": version,
            "joined_methodology_version_id": joined_version or version,
            "required_calculations": requirements,
            "calculation_type_tuple": calculations,
            "accepted": accepted,
        }

    return (
        case(
            "LEGACY_EMPTY",
            version=legacy_version,
            requirements=[],
            calculations=[],
            accepted=True,
        ),
        case(
            "LEGACY_TWO_FIELD",
            version=legacy_version,
            requirements=[legacy_requirement],
            calculations=[],
            accepted=True,
        ),
        case(
            "POLICY_V2_EMPTY",
            version=policy_version,
            requirements=[],
            calculations=[],
            accepted=True,
        ),
        case(
            "POLICY_V2_TEN_LITERAL_TUPLES",
            version=policy_version,
            requirements=policy_requirements,
            calculations=[str(row[1]) for row in approved],
            accepted=True,
        ),
        case(
            "LEGACY_FOUR_FIELD",
            version=legacy_version,
            requirements=[first_policy],
            calculations=[str(first_policy["formula_type"])],
            accepted=False,
        ),
        case(
            "POLICY_V2_TWO_FIELD",
            version=policy_version,
            requirements=[legacy_requirement],
            calculations=["BURN_RATE"],
            accepted=False,
        ),
        case(
            "UNKNOWN_FORMULA_TYPE",
            version=policy_version,
            requirements=[{**first_policy, "formula_type": "UNKNOWN"}],
            calculations=["UNKNOWN"],
            accepted=False,
        ),
        case(
            "ORDINAL_DRIFT",
            version=policy_version,
            requirements=[{**first_policy, "formula_ordinal": 2}],
            calculations=[str(first_policy["formula_type"])],
            accepted=False,
        ),
        case(
            "SCOPE_DRIFT",
            version=policy_version,
            requirements=[{**first_policy, "scope": "sheet_control"}],
            calculations=[str(first_policy["formula_type"])],
            accepted=False,
        ),
        case(
            "UNDECLARED_FDD_CALCULATION_ROOT",
            version=policy_version,
            requirements=[],
            calculations=[str(first_policy["formula_type"])],
            accepted=False,
        ),
        case(
            "MIXED_SHAPES",
            version=policy_version,
            requirements=[first_policy, legacy_requirement],
            calculations=[str(first_policy["formula_type"])],
            accepted=False,
        ),
        case(
            "POLICY_V2_EXTRA_FIELD",
            version=policy_version,
            requirements=[{**first_policy, "calc_type": first_policy["formula_type"]}],
            calculations=[str(first_policy["formula_type"])],
            accepted=False,
        ),
        case(
            "POLICY_V2_MISSING_FIELD",
            version=policy_version,
            requirements=[{key: value for key, value in first_policy.items() if key != "scope"}],
            calculations=[str(first_policy["formula_type"])],
            accepted=False,
        ),
        case(
            "LEGACY_EXTRA_FIELD",
            version=legacy_version,
            requirements=[{**legacy_requirement, "scope": "line_item"}],
            calculations=["BURN_RATE"],
            accepted=False,
        ),
        case(
            "LEGACY_MISSING_FIELD",
            version=legacy_version,
            requirements=[{"calc_type": "BURN_RATE"}],
            calculations=["BURN_RATE"],
            accepted=False,
        ),
        case(
            "CROSS_VERSION_JOIN",
            version=policy_version,
            joined_version=legacy_version,
            requirements=[first_policy],
            calculations=[str(first_policy["formula_type"])],
            accepted=False,
        ),
        case(
            "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE",
            version=policy_version,
            requirements=[first_policy, dict(first_policy)],
            calculations=[str(first_policy["formula_type"])],
            accepted=False,
        ),
    )


def validate_requirement_matrix_result_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    expected_application_role: str,
) -> dict[str, object]:
    """Validate the closed 4-legal/12-guard/1-FK requirement evidence split."""

    results = [dict(row) for row in rows]
    legal = [row for row in results if row.get("expected_accepted") is True]
    negative = [row for row in results if row.get("expected_accepted") is False]
    equation = [row for row in negative if row.get("rejection_path") == "EQUATION_V3"]
    preempted = [row for row in negative if row.get("rejection_path") == "SCHEMA_PREEMPTION"]
    common_negative_valid = all(
        row.get("observed_accepted") is False
        and row.get("observer_role") == expected_application_role
        and row.get("session_replication_role") == "origin"
        and row.get("zero_mutation") is True
        and row.get("dashboard_row_delta") == 0
        and row.get("dashboard_root_delta") == 0
        and isinstance(row.get("pre_projection_sha256"), str)
        and row.get("pre_projection_sha256") == row.get("post_projection_sha256")
        and row.get("stable_pre_projection_sha256") == row.get("stable_post_projection_sha256")
        and all(
            isinstance(row.get(field), str) and _SHA256_RE.fullmatch(str(row[field])) is not None
            for field in (
                "observer_posture_sha256",
                "pre_observer_posture_sha256",
                "post_observer_posture_sha256",
            )
        )
        for row in negative
    )
    if (
        len(results) != 17
        or len(legal) != 4
        or len(negative) != 13
        or len(equation) != 12
        or len(preempted) != 1
        or not all(
            row.get("observed_accepted") is True
            and row.get("observer_role") == expected_application_role
            and row.get("session_replication_role") == "origin"
            and row.get("sqlstate") is None
            and row.get("reason_code") is None
            and row.get("rejection_path") is None
            and row.get("guard_reached") is True
            and row.get("dashboard_row_delta") == 0
            and row.get("dashboard_root_delta") == 0
            and isinstance(row.get("production_graph_sha256"), str)
            and _SHA256_RE.fullmatch(str(row["production_graph_sha256"])) is not None
            and isinstance(row.get("legal_projection_sha256"), str)
            and _SHA256_RE.fullmatch(str(row["legal_projection_sha256"])) is not None
            for row in legal
        )
        or not common_negative_valid
        or not all(
            row.get("sqlstate") == _DASHBOARD_REJECTION_SQLSTATE
            and row.get("reason_code") == _DASHBOARD_REJECTION_CODE
            and row.get("guard_reached") is True
            for row in equation
        )
        or preempted[0].get("case_id") != "CROSS_VERSION_JOIN"
        or preempted[0].get("sqlstate") != _REQUIREMENT_SCHEMA_PREEMPTION_SQLSTATE
        or preempted[0].get("reason_code") != _REQUIREMENT_SCHEMA_PREEMPTION_CODE
        or preempted[0].get("guard_reached") is not False
    ):
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    payload = {
        "legal_case_count": len(legal),
        "negative_case_count": len(negative),
        "equation_v3_rejection_count": len(equation),
        "schema_preemption_count": len(preempted),
        "negative_zero_mutation_count": sum(row.get("zero_mutation") is True for row in negative),
        "negative_zero_dashboard_derivation_count": sum(
            row.get("dashboard_row_delta") == 0 and row.get("dashboard_root_delta") == 0
            for row in negative
        ),
        "case_result_sha256": _canonical_sha256(results),
    }
    return payload


@dataclass(frozen=True)
class _RequirementSeedSpec:
    vector: Mapping[str, object]
    dashboard_seed_plan: Mapping[str, Any]
    commercial_registry: Any
    financial_registry: Any
    financial_raw_registry: Mapping[str, Any]
    focus_question_id: str
    cdd_byte_count: int


@dataclass(frozen=True)
class _RequirementSelection:
    selection_id: str


def _raw_registry_identity(registry: Mapping[str, Any]) -> dict[str, object]:
    try:
        version = registry["versions"][0]
        questions = version["questions"]
        question_hashes = [_canonical_sha256(question) for question in questions]
        trace_rows = [
            {
                "methodology_question_id": question["methodology_question_id"],
                "source_trace": question["source_trace"],
            }
            for question in questions
        ]
        requirement_rows = [
            [question["methodology_question_id"], question["required_calculations"]]
            for question in questions
        ]
        return {
            "methodology_type": registry["methodology_type"],
            "methodology_version_id": version["methodology_version_id"],
            "source_sha256": version["source_hash"],
            "registry_sha256": _canonical_sha256(registry),
            "version_sha256": _canonical_sha256(version),
            "source_trace_sha256": _canonical_sha256(trace_rows),
            "question_count": len(questions),
            "ordered_question_hashes_sha256": _canonical_sha256(question_hashes),
            "required_calculation_tuple_sha256": _canonical_sha256(requirement_rows),
        }
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID") from exc


def _build_requirement_seed_spec(
    *,
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    scope: _CaseScope,
    vector: Mapping[str, object],
) -> _RequirementSeedSpec:
    """Build one app-role adversarial Task-5 fixture from the frozen registries."""

    if vector.get("accepted") is not False or vector.get("case_id") in {
        "CROSS_VERSION_JOIN",
        "UNDECLARED_FDD_CALCULATION_ROOT",
    }:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_FIXTURE_SCOPE_INVALID")

    from idis.methodology.models import MethodologyRegistry
    from idis.models.methodology_coverage import MethodologyCoverageRecord

    cdd_raw = _capture_cdd_template_bytes()
    commercial, financial = _production_methodology_registries(
        cdd_raw=cdd_raw,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
    )
    base_plan = build_v27_dashboard_seed_plan(
        artifact,
        policy_artifact_raw,
        workbook_raw,
        cdd_raw=cdd_raw,
        tenant_id=str(scope.tenant_id),
        deal_id=str(scope.deal_id),
        run_id=str(scope.run_id),
    )
    raw_financial = deepcopy(financial.model_dump(mode="json"))
    version = raw_financial["versions"][0]
    version_id = str(vector["methodology_version_id"])
    version["methodology_version_id"] = version_id
    version["version_label"] = "task8-v27-requirement-census-v1"
    questions = version["questions"]
    if not isinstance(questions, list) or len(questions) != 79:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    focus_question = questions[0]
    focus_question_id = str(focus_question["methodology_question_id"])
    for question in questions:
        question["methodology_version_id"] = version_id
        question["required_calculations"] = []
    focus_question["required_calculations"] = deepcopy(vector["required_calculations"])

    sanitized_financial = deepcopy(raw_financial)
    try:
        financial_registry = MethodologyRegistry.model_validate(sanitized_financial)
    except (TypeError, ValueError):
        sanitized_financial["versions"][0]["questions"][0]["required_calculations"] = []
        financial_registry = MethodologyRegistry.model_validate(sanitized_financial)

    base_rows = [dict(row) for row in base_plan["row_tuple"]]
    commercial_rows = [row for row in base_rows if row["methodology_type"] == "commercial_dd"]
    source_hash = str(version["source_hash"])
    version_record_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"idis:methodology-version:{scope.tenant_id}:{version_id}:{source_hash}",
        )
    )
    raw_by_id = {str(question["methodology_question_id"]): question for question in questions}
    sanitized_by_id = {
        question.methodology_question_id: question
        for question in financial_registry.current_version.questions
    }
    financial_rows: list[dict[str, object]] = []
    for question_ordinal, question_id in enumerate(raw_by_id, start=1):
        raw_question = raw_by_id[question_id]
        question = sanitized_by_id[question_id]
        question_record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"idis:methodology-question:{scope.tenant_id}:{version_record_id}:{question_id}",
            )
        )
        coverage = MethodologyCoverageRecord(
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            run_id=str(scope.run_id),
            methodology_id=question.methodology_id,
            methodology_version_id=version_id,
            methodology_question_id=question_id,
            methodology_type=question.methodology_type,
            section=question.section,
        )
        question_payload_sha256 = _canonical_sha256(raw_question)
        dashboard_row_id = _uuid5_from_preimage(
            _DASHBOARD_ROW_NAMESPACE,
            [
                "methodology-truth-dashboard-row-v2",
                str(scope.tenant_id),
                str(scope.deal_id),
                str(scope.run_id),
                CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
                CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
                coverage.coverage_record_id,
                version_record_id,
                question_record_id,
                question_payload_sha256,
            ],
        )
        requirements = deepcopy(raw_question["required_calculations"])
        calculation_types = (
            list(vector["calculation_type_tuple"]) if question_id == focus_question_id else []
        )
        financial_rows.append(
            {
                "coverage_ordinal": 0,
                "methodology_type": "financial_dd",
                "question_ordinal": question_ordinal,
                "methodology_version_id": version_id,
                "methodology_version_record_id": version_record_id,
                "methodology_question_id": question_id,
                "methodology_question_record_id": question_record_id,
                "coverage_record_id": coverage.coverage_record_id,
                "question_payload_sha256": question_payload_sha256,
                "source_trace_sha256": _canonical_sha256(raw_question["source_trace"]),
                "required_calculation_tuple": requirements,
                "required_calculation_count": len(requirements),
                "calculation_type_tuple": calculation_types,
                "base_row_disposition": (
                    "ANSWERED" if requirements or calculation_types else "GAP"
                ),
                "dashboard_row_id": dashboard_row_id,
            }
        )
    rows = [*commercial_rows, *financial_rows]
    for ordinal, row in enumerate(rows, start=1):
        row["coverage_ordinal"] = ordinal
        row.setdefault("calculation_type_tuple", [])
    required_count = sum(int(row["required_calculation_count"]) for row in rows)
    payload = {
        key: value
        for key, value in base_plan.items()
        if key
        not in {
            "expected_sha256",
            "financial_registry_identity",
            "row_tuple",
            "required_calculation_count",
        }
    }
    payload.update(
        {
            "schema_version": "task8-v27-dashboard-requirement-seed-plan-v1",
            "requirement_case_id": vector["case_id"],
            "financial_registry_identity": _raw_registry_identity(raw_financial),
            "required_calculation_count": required_count,
            "row_tuple": rows,
        }
    )
    plan = {**payload, "expected_sha256": _canonical_sha256(payload)}
    return _RequirementSeedSpec(
        vector=dict(vector),
        dashboard_seed_plan=plan,
        commercial_registry=commercial,
        financial_registry=financial_registry,
        financial_raw_registry=raw_financial,
        focus_question_id=focus_question_id,
        cdd_byte_count=len(cdd_raw),
    )


def build_census_case_manifest(v27: Mapping[str, Any]) -> dict[str, Any]:
    _validate_v27(v27)
    dashboard_id, partial_id, erasure_id = _AUTHORITY_IDS
    changed_authority_targets = {
        authority_id: _artifact_row(
            v27,
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
        _fail("TASK8_V27_CENSUS_PARTIAL_TARGET_INVALID")

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
            for row in v27["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
            if row.get("authority_id") == authority_id
            and row.get("function_kind") == "TRIGGER_GUARD"
        ]
        if len(rows) != 1:
            _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
        return rows[0]

    dashboard_guard = trigger_guard(dashboard_id)
    partial_guard = trigger_guard(partial_id)
    dashboard_contract = v27["task8_fdd_contract"]["dashboard_contract"]
    coverage_row_count = dashboard_contract.get("coverage_row_count")
    if coverage_row_count != 100:
        _fail("TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    financial_coverage_row_count = dashboard_contract.get("financial_row_count")
    if financial_coverage_row_count != 79:
        _fail("TASK8_V27_CENSUS_FINANCIAL_COVERAGE_ROW_COUNT_INVALID")
    all_guard_rows = v27["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
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
        "dashboard_expected_row_count": coverage_row_count,
        "requirement_matrix": _requirement_matrix(),
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
            "installed_tenant_rls_table_count": 66,
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
                    for row in v27["sql_authority_registry"]["guard_function_acl_registry"][
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
    v27: Mapping[str, Any],
) -> None:
    expected = build_census_case_manifest(v27)
    if dict(manifest) != expected:
        _fail("TASK8_V27_CENSUS_MANIFEST_INVALID")
    cases = [
        case
        for authority in manifest["authorities"]
        for family in ("legal_case_tuple", "illegal_case_tuple", "direct_sql_case_tuple")
        for case in authority[family]
    ]
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        _fail("TASK8_V27_CENSUS_CASE_ID_DUPLICATE")
    if any(case["execution_role"] == "admin" for case in cases):
        _fail("TASK8_V27_CENSUS_ADMIN_BEHAVIOR_FORBIDDEN")


def census_case_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the closed 25-case behavior matrix in reviewed order."""

    rows = _case_rows(manifest)
    if len(rows) != 25:
        _fail("TASK8_V27_CENSUS_MANIFEST_INVALID")
    return rows


def _decoded_sql_hash(value: object, *, domain: str) -> str:
    statements = _decode_sql_tuple(value, code="TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID")
    return _canonical_sha256([domain, list(statements)])


def build_authority_restoration_plan(v27: Mapping[str, Any]) -> dict[str, Any]:
    """Bind exact partial and erasure U->D->U bytes and app observations."""

    bundle = build_hash_bound_authority_execution(v27)
    partial = next(
        row for row in bundle["sql_authorities"] if row["authority_id"] == _AUTHORITY_IDS[1]
    )
    erasure = bundle["erasure_authority"]
    erasure_contract = v27["task8_fdd_contract"]["erasure_refusal_contract"]
    partial_up = _canonical_sha256(
        [
            "task8-v27-partial-authority-up-catalog-v1",
            partial["authority_sha256"],
            partial["body_tuple_sha256"],
            partial["guard_function_acl_binding_tuple"],
        ]
    )
    partial_down = _decoded_sql_hash(
        partial["downgrade_sql_utf8_hex_tuple"],
        domain="task8-v27-partial-authority-down-catalog-v1",
    )
    erasure_up = _canonical_sha256(
        [
            "task8-v27-erasure-authority-up-catalog-v1",
            erasure["authority_sha256"],
            erasure["body_tuple_sha256"],
            erasure["acl_tuple_sha256"],
        ]
    )
    erasure_down = _decoded_sql_hash(
        erasure_contract["downgrade_sql_utf8_hex_tuple"],
        domain="task8-v27-erasure-authority-down-catalog-v1",
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
        "schema_version": "task8-v27-authority-restoration-plan-v1",
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
        _fail("TASK8_V27_CENSUS_DATABASE_URL_INVALID")
    return unquote(parsed.username)


def _replace_database(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        _fail("TASK8_V27_CENSUS_DATABASE_URL_INVALID")
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


def _connect_raw(database_url: str) -> Any:
    try:
        import psycopg2
    except ImportError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_PSYCOPG2_UNAVAILABLE") from exc
    return psycopg2.connect(database_url)


class _UnclassifiedDmlRejectingConnection:
    """Permit reads/DDL but reject every unclassified raw DML call site."""

    def __init__(self, connection: Any) -> None:
        object.__setattr__(self, "_connection", connection)

    def observe_statement(self, statement: object) -> None:
        if _normalized_fixture_dml_statement(statement) is not None:
            _fail("TASK8_V27_CENSUS_UNCLASSIFIED_DML_FORBIDDEN")

    def cursor(self, *args: object, **kwargs: object) -> _FixtureDmlCursor:
        return _FixtureDmlCursor(self, self._connection.cursor(*args, **kwargs))

    def __enter__(self) -> _UnclassifiedDmlRejectingConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> object:
        return self._connection.__exit__(*exc_info)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_connection":
            object.__setattr__(self, name, value)
        else:
            setattr(self._connection, name, value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _connect(database_url: str) -> Any:
    return _UnclassifiedDmlRejectingConnection(_connect_raw(database_url))


class _DmlStatementObserver(Protocol):
    def observe_statement(self, statement: object) -> None: ...


class _FixtureDmlCursor:
    """Cursor proxy that observes the SQL actually submitted to PostgreSQL."""

    def __init__(self, owner: _DmlStatementObserver, cursor: Any) -> None:
        self._owner = owner
        self._cursor = cursor

    def execute(self, statement: object, parameters: object = None) -> Any:
        self._owner.observe_statement(statement)
        if parameters is None:
            return self._cursor.execute(statement)
        return self._cursor.execute(statement, parameters)

    def executemany(self, statement: object, parameters: object) -> Any:
        self._owner.observe_statement(statement)
        return self._cursor.executemany(statement, parameters)

    def __enter__(self) -> _FixtureDmlCursor:
        self._cursor.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> object:
        return self._cursor.__exit__(*exc_info)

    def __iter__(self) -> Any:
        return iter(self._cursor)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _FixtureDmlConnection:
    """Connection proxy that closes every observed fixture-DML transaction."""

    def __init__(
        self,
        connection: Any,
        *,
        expected_application_role: str,
        tenant_id: object,
        session_tenant_id: object | None,
        case_coordinate: str,
        dml_classification: str,
        operation_prefix: str,
    ) -> None:
        try:
            expected_tenant = str(uuid.UUID(str(tenant_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise Task8V27CensusError("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID") from exc
        try:
            expected_session_tenant = (
                expected_tenant
                if session_tenant_id is None
                else ""
                if session_tenant_id == ""
                else str(uuid.UUID(str(session_tenant_id)))
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise Task8V27CensusError("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID") from exc
        if (
            re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", operation_prefix) is None
            or not expected_application_role
            or dml_classification
            not in {
                _APPLICATION_FIXTURE_DML,
                _APPLICATION_BEHAVIOR_DML,
                _ERASURE_BEHAVIOR_DML,
            }
        ):
            _fail("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID")
        _case_scope_identity(case_coordinate)
        self._connection = connection
        self._expected_application_role = expected_application_role
        self._expected_tenant = expected_tenant
        self._expected_session_tenant = expected_session_tenant
        self._case_coordinate = case_coordinate
        self._dml_classification = dml_classification
        self._operation_prefix = operation_prefix
        self._transaction_ordinal = 0
        self._statements: list[str] = []
        self._posture: Mapping[str, object] | None = None

    def cursor(self, *args: object, **kwargs: object) -> _FixtureDmlCursor:
        return _FixtureDmlCursor(self, self._connection.cursor(*args, **kwargs))

    def observe_statement(self, statement: object) -> None:
        normalized = _normalized_fixture_dml_statement(statement)
        if normalized is None:
            return
        if self._posture is None:
            cursor = self._connection.cursor()
            try:
                posture = _attest_connection_role_posture(
                    cursor,
                    self._expected_application_role,
                )
                cursor.execute("SELECT current_setting('idis.tenant_id',true)")
                tenant_row = cursor.fetchone()
            finally:
                cursor.close()
            if tenant_row is None or str(tenant_row[0] or "") != self._expected_session_tenant:
                _fail("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID")
            self._posture = posture
        self._statements.append(normalized)

    def _finish_transaction(self, outcome: str) -> None:
        if not self._statements:
            return
        if self._posture is None:
            _fail("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID")
        self._transaction_ordinal += 1
        _record_fixture_dml_role(
            case_coordinate=self._case_coordinate,
            dml_classification=self._dml_classification,
            operation=f"{self._operation_prefix}:{self._transaction_ordinal:04d}",
            posture=self._posture,
            expected_application_role=self._expected_application_role,
            tenant_id=self._expected_tenant,
            session_tenant_id=self._expected_session_tenant,
            transaction_outcome=outcome,
            dml_statement_tuple=tuple(self._statements),
        )
        self._statements = []
        self._posture = None

    def commit(self) -> None:
        try:
            self._connection.commit()
        except Exception:
            try:
                self._connection.rollback()
            finally:
                self._finish_transaction("ROLLBACK")
            raise
        self._finish_transaction("COMMIT")

    def rollback(self) -> None:
        try:
            self._connection.rollback()
        finally:
            self._finish_transaction("ROLLBACK")

    def close(self) -> None:
        try:
            if self._statements:
                try:
                    self._connection.rollback()
                finally:
                    self._finish_transaction("ROLLBACK")
        finally:
            self._connection.close()

    def __enter__(self) -> _FixtureDmlConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> object:
        try:
            outcome = "COMMIT" if exc_info[0] is None else "ROLLBACK"
            return self._connection.__exit__(*exc_info)
        finally:
            self._finish_transaction(outcome)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _connect_fixture_dml(
    database_url: str,
    *,
    expected_application_role: str,
    tenant_id: object,
    case_coordinate: str,
    operation_prefix: str,
) -> _FixtureDmlConnection:
    """Open one identity-bound application fixture-DML connection."""

    try:
        normalized_tenant_id = str(uuid.UUID(str(tenant_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID") from exc
    if (
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", operation_prefix) is None
        or not expected_application_role
    ):
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID")
    connection = _connect_raw(database_url)
    try:
        return _FixtureDmlConnection(
            connection,
            expected_application_role=expected_application_role,
            tenant_id=normalized_tenant_id,
            session_tenant_id=None,
            case_coordinate=case_coordinate,
            dml_classification=_APPLICATION_FIXTURE_DML,
            operation_prefix=operation_prefix,
        )
    except Exception:
        connection.close()
        raise


def _connect_behavior_dml(
    database_url: str,
    *,
    expected_role: str,
    tenant_id: object,
    session_tenant_id: object | None = None,
    case_coordinate: str,
    operation_prefix: str,
    dml_classification: str,
) -> _FixtureDmlConnection:
    """Open one classified application/erasure behavior-DML connection."""

    if dml_classification not in {
        _APPLICATION_BEHAVIOR_DML,
        _ERASURE_BEHAVIOR_DML,
    }:
        _fail("TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID")
    connection = _connect_raw(database_url)
    try:
        return _FixtureDmlConnection(
            connection,
            expected_application_role=expected_role,
            tenant_id=tenant_id,
            session_tenant_id=session_tenant_id,
            case_coordinate=case_coordinate,
            dml_classification=dml_classification,
            operation_prefix=operation_prefix,
        )
    except Exception:
        connection.close()
        raise


def _attest_connection_role_posture(
    cursor: Any,
    expected_role: str,
) -> dict[str, object]:
    """Prove one evidence connection is the expected unprivileged role."""

    cursor.execute(
        """
        SELECT current_user,session_user,role.rolsuper,role.rolbypassrls,
               current_setting('session_replication_role')
          FROM pg_catalog.pg_roles AS role
         WHERE role.rolname=current_user
        """
    )
    row = cursor.fetchone()
    if row is None:
        _fail("TASK8_V27_CENSUS_ROLE_POSTURE_INVALID")
    current_role, session_role, is_superuser, bypasses_rls, replication_role = row
    if (
        str(current_role) != expected_role
        or str(session_role) != expected_role
        or bool(is_superuser)
        or bool(bypasses_rls)
        or str(replication_role) != "origin"
    ):
        _fail("TASK8_V27_CENSUS_ROLE_POSTURE_INVALID")
    return {
        "current_user": str(current_role),
        "session_user": str(session_role),
        "rolsuper": bool(is_superuser),
        "rolbypassrls": bool(bypasses_rls),
        "session_replication_role": str(replication_role),
    }


def _connection_posture_sha256(posture: Mapping[str, object]) -> str:
    expected_keys = (
        "current_user",
        "session_user",
        "rolsuper",
        "rolbypassrls",
        "session_replication_role",
    )
    if tuple(posture) != expected_keys or posture.get("session_replication_role") != "origin":
        _fail("TASK8_V27_CENSUS_ROLE_POSTURE_INVALID")
    return _canonical_sha256(
        ["task8-v27-connection-role-posture-v1", [[key, posture[key]] for key in expected_keys]]
    )


def _posture_role(posture: Mapping[str, object]) -> str:
    _connection_posture_sha256(posture)
    role = posture.get("current_user")
    if not isinstance(role, str):
        _fail("TASK8_V27_CENSUS_ROLE_POSTURE_INVALID")
    return role


def _execute_statements(connection: Any, statements: list[str]) -> None:
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def _quoted_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value):
        _fail("TASK8_V27_CENSUS_ROLE_INVALID")
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


@dataclass(frozen=True)
class _MigrationSourceSnapshot:
    file_rows: tuple[tuple[str, bytes, str], ...]
    internal_module_rows: tuple[tuple[str, str, bytes, str], ...]
    expected_sha256: str


def _read_lf_pinned_regular_file(path: Path, expected_sha256: str) -> bytes:
    try:
        lexical = path.absolute()
        before = lexical.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or getattr(before, "st_file_attributes", 0) & 0x400
        ):
            _fail("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID")
        with lexical.open("rb") as handle:
            opened_before = os.fstat(handle.fileno())
            raw = handle.read()
            opened_after = os.fstat(handle.fileno())
        after = lexical.lstat()
    except Task8V27CensusError:
        raise
    except OSError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID") from exc
    identities = tuple(
        (value.st_dev, value.st_ino, value.st_mode, value.st_size)
        for value in (before, opened_before, opened_after, after)
    )
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if len(set(identities)) != 1 or hashlib.sha256(normalized).hexdigest() != expected_sha256:
        _fail("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID")
    return normalized


def _capture_migration_source_snapshot(
    migration_root: Path | None = None,
    *,
    internal_source_root: Path | None = None,
) -> _MigrationSourceSnapshot:
    """Admit every Alembic execution source once before disposable DB creation."""

    repository_root = internal_source_root or Path(__file__).resolve().parents[1]
    root = migration_root or (
        Path(__file__).resolve().parents[1] / "src" / "idis" / "persistence" / "migrations"
    )
    rows = tuple(
        (
            relative_path,
            _read_lf_pinned_regular_file(root / relative_path, expected_sha256),
            expected_sha256,
        )
        for relative_path, expected_sha256 in _MIGRATION_EXECUTION_SOURCE_ROWS
    )
    internal_module_rows = tuple(
        (
            module_name,
            relative_path,
            _read_lf_pinned_regular_file(repository_root / relative_path, expected_sha256),
            expected_sha256,
        )
        for module_name, relative_path, expected_sha256 in _MIGRATION_EXECUTION_INTERNAL_MODULE_ROWS
    )
    import_edges = _migration_internal_import_edges(rows)
    manifest_sha256 = _canonical_sha256(
        [
            "task8-v27-migration-execution-source-manifest-v2",
            [(relative_path, sha256) for relative_path, _raw, sha256 in rows],
            [
                (module_name, relative_path, sha256)
                for module_name, relative_path, _raw, sha256 in internal_module_rows
            ],
            import_edges,
        ]
    )
    if (
        import_edges != _MIGRATION_EXECUTION_INTERNAL_IMPORT_EDGES
        or manifest_sha256 != _MIGRATION_EXECUTION_SOURCE_MANIFEST_SHA256
    ):
        _fail("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID")
    return _MigrationSourceSnapshot(
        file_rows=rows,
        internal_module_rows=internal_module_rows,
        expected_sha256=manifest_sha256,
    )


def _migration_internal_import_edges(
    file_rows: Sequence[tuple[str, bytes, str]],
) -> tuple[tuple[str, str], ...]:
    """Return every static internal import in the captured Alembic sources."""

    edges: set[tuple[str, str]] = set()
    try:
        for relative_path, raw, _sha256 in file_rows:
            if not relative_path.endswith(".py"):
                continue
            tree = ast.parse(raw.decode("utf-8"), filename=relative_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module is not None:
                    if node.module == "idis" or node.module.startswith("idis."):
                        edges.add((relative_path, node.module))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "idis" or alias.name.startswith("idis."):
                            edges.add((relative_path, alias.name))
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID") from exc
    return tuple(sorted(edges))


def _validate_migration_source_snapshot(snapshot: object) -> _MigrationSourceSnapshot:
    if not isinstance(snapshot, _MigrationSourceSnapshot):
        _fail("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID")
    observed_rows = tuple(
        (relative_path, sha256) for relative_path, _raw, sha256 in snapshot.file_rows
    )
    observed_internal_rows = tuple(
        (module_name, relative_path, sha256)
        for module_name, relative_path, _raw, sha256 in snapshot.internal_module_rows
    )
    import_edges = _migration_internal_import_edges(snapshot.file_rows)
    if (
        observed_rows != _MIGRATION_EXECUTION_SOURCE_ROWS
        or observed_internal_rows != _MIGRATION_EXECUTION_INTERNAL_MODULE_ROWS
        or import_edges != _MIGRATION_EXECUTION_INTERNAL_IMPORT_EDGES
        or snapshot.expected_sha256 != _MIGRATION_EXECUTION_SOURCE_MANIFEST_SHA256
        or any(
            hashlib.sha256(raw).hexdigest() != sha256 for _path, raw, sha256 in snapshot.file_rows
        )
        or any(
            hashlib.sha256(raw).hexdigest() != sha256
            for _module_name, _path, raw, sha256 in snapshot.internal_module_rows
        )
        or snapshot.expected_sha256
        != _canonical_sha256(
            [
                "task8-v27-migration-execution-source-manifest-v2",
                observed_rows,
                observed_internal_rows,
                import_edges,
            ]
        )
    ):
        _fail("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID")
    return snapshot


class _RejectUncapturedIdisImports:
    """Fail closed if a migration attempts any uncaptured internal import."""

    def find_spec(
        self,
        fullname: str,
        _path: object = None,
        _target: object = None,
    ) -> None:
        if fullname == "idis" or fullname.startswith("idis."):
            _fail("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID")
        return None


@contextmanager
def _captured_migration_internal_module_scope(
    snapshot: _MigrationSourceSnapshot,
) -> Iterator[None]:
    """Install only captured internal modules while Alembic executes."""

    snapshot = _validate_migration_source_snapshot(snapshot)
    prior_modules = {
        name: module
        for name, module in tuple(sys.modules.items())
        if name == "idis" or name.startswith("idis.")
    }
    guard = _RejectUncapturedIdisImports()
    try:
        for name in prior_modules:
            sys.modules.pop(name, None)
        package_names = {
            ".".join(module_name.split(".")[:ordinal])
            for module_name, _path, _raw, _sha256 in snapshot.internal_module_rows
            for ordinal in range(1, len(module_name.split(".")))
        }
        for package_name in sorted(package_names, key=lambda value: (value.count("."), value)):
            package = ModuleType(package_name)
            package.__package__ = package_name
            package.__path__ = []
            sys.modules[package_name] = package
            parent_name, _, child_name = package_name.rpartition(".")
            if parent_name:
                setattr(sys.modules[parent_name], child_name, package)
        sys.meta_path.insert(0, cast(Any, guard))
        for module_name, relative_path, raw, _sha256 in snapshot.internal_module_rows:
            module = ModuleType(module_name)
            module.__file__ = relative_path
            module.__package__ = module_name.rpartition(".")[0]
            sys.modules[module_name] = module
            parent_name, _, child_name = module_name.rpartition(".")
            setattr(sys.modules[parent_name], child_name, module)
            exec(compile(raw, relative_path, "exec"), module.__dict__)  # noqa: S102
        yield
    finally:
        while guard in sys.meta_path:
            sys.meta_path.remove(cast(Any, guard))
        for name in tuple(sys.modules):
            if name == "idis" or name.startswith("idis."):
                sys.modules.pop(name, None)
        sys.modules.update(prior_modules)


@contextmanager
def _captured_migration_execution_scope(
    snapshot: _MigrationSourceSnapshot,
    snapshot_root: Path,
) -> Iterator[None]:
    """Make every Alembic Python load execute the admitted in-memory bytes."""

    try:
        from alembic.util import pyfiles as alembic_pyfiles
    except ImportError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_MIGRATION_RUNNER_UNAVAILABLE") from exc
    snapshot = _validate_migration_source_snapshot(snapshot)
    lexical_root = snapshot_root.absolute()
    captured_by_path = {
        relative_path: raw
        for relative_path, raw, _sha256 in snapshot.file_rows
        if relative_path.endswith(".py")
    }
    original_loader = alembic_pyfiles.load_module_py

    def load_captured_module(module_id: str, path: object) -> ModuleType:
        try:
            if not isinstance(path, (str, os.PathLike)):
                _fail("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID")
            candidate = Path(path).absolute()
            relative_path = candidate.relative_to(lexical_root).as_posix()
        except (OSError, TypeError, ValueError) as exc:
            raise Task8V27CensusError("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID") from exc
        raw = captured_by_path.get(relative_path)
        if raw is None:
            _fail("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID")
        module = ModuleType(module_id)
        module.__file__ = str(candidate)
        module.__package__ = ""
        exec(compile(raw, str(candidate), "exec"), module.__dict__)  # noqa: S102
        return module

    alembic_pyfiles.load_module_py = load_captured_module
    try:
        with _captured_migration_internal_module_scope(snapshot):
            yield
    finally:
        alembic_pyfiles.load_module_py = original_loader


def _materialize_migration_source_snapshot(
    snapshot: _MigrationSourceSnapshot,
    destination: Path,
) -> None:
    """Materialize only the already-admitted LF-normalized Alembic bytes."""

    snapshot = _validate_migration_source_snapshot(snapshot)
    try:
        destination.mkdir()
        for relative_path, raw, expected_sha256 in snapshot.file_rows:
            target = destination / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha256:
                _fail("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID")
    except Task8V27CensusError:
        raise
    except OSError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID") from exc


def _run_migrations_through_0038(
    engine: Any,
    migration_snapshot: _MigrationSourceSnapshot,
) -> None:
    """Drive Alembic only from the pre-admitted private source snapshot."""

    try:
        from alembic import command
        from alembic.config import Config
    except ImportError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_MIGRATION_RUNNER_UNAVAILABLE") from exc
    snapshot = _validate_migration_source_snapshot(migration_snapshot)
    with TemporaryDirectory(prefix="idis-task8-v27-migrations-") as temporary_directory:
        snapshot_root = Path(temporary_directory) / "migrations"
        _materialize_migration_source_snapshot(snapshot, snapshot_root)
        config = Config()
        config.set_main_option("script_location", str(snapshot_root))
        with (
            _captured_migration_execution_scope(snapshot, snapshot_root),
            engine.begin() as connection,
        ):
            config.attributes["connection"] = connection
            command.upgrade(config, "0038")


def _pregrant_application_catalog_payload() -> dict[str, object]:
    """Return the exact reviewed application/physical catalog boundary."""

    if (
        len(_APPLICATION_RELATION_TUPLE) != 66
        or len(_PHYSICAL_PUBLIC_RELATION_TUPLE) != 67
        or tuple(sorted(set(_APPLICATION_RELATION_TUPLE))) != _APPLICATION_RELATION_TUPLE
        or tuple(sorted(set(_PHYSICAL_PUBLIC_RELATION_TUPLE))) != _PHYSICAL_PUBLIC_RELATION_TUPLE
        or set(_PHYSICAL_PUBLIC_RELATION_TUPLE) - set(_APPLICATION_RELATION_TUPLE)
        != set(_EXCLUDED_APPLICATION_RELATION_TUPLE)
        or _canonical_sha256([_APPLICATION_RELATION_HASH_DOMAIN, _APPLICATION_RELATION_TUPLE])
        != _APPLICATION_RELATION_TUPLE_SHA256
        or _canonical_sha256(
            [_PHYSICAL_PUBLIC_RELATION_HASH_DOMAIN, _PHYSICAL_PUBLIC_RELATION_TUPLE]
        )
        != _PHYSICAL_PUBLIC_RELATION_TUPLE_SHA256
    ):
        _fail("TASK8_V27_CENSUS_APPLICATION_CATALOG_INVALID")
    return {
        "schema_version": "task8-v27-pregrant-application-catalog-v1",
        "physical_public_relation_count": 67,
        "physical_public_relation_tuple": list(_PHYSICAL_PUBLIC_RELATION_TUPLE),
        "physical_public_relation_tuple_sha256": (_PHYSICAL_PUBLIC_RELATION_TUPLE_SHA256),
        "application_relation_count": 66,
        "application_relation_tuple": list(_APPLICATION_RELATION_TUPLE),
        "application_relation_tuple_sha256": _APPLICATION_RELATION_TUPLE_SHA256,
        "excluded_application_relation_tuple": list(_EXCLUDED_APPLICATION_RELATION_TUPLE),
        "excluded_application_direct_privilege_count": 0,
        "excluded_application_effective_privilege_count": 0,
    }


def _observe_pregrant_application_catalog(
    connection: Any,
    *,
    application_role: str,
    text: Any,
) -> dict[str, object]:
    """Observe the exact 67/66 boundary before any reviewed grant executes."""

    observed_physical = tuple(
        str(value)
        for value in connection.execute(
            text(
                """
                SELECT c.relname
                  FROM pg_catalog.pg_class AS c
                  JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace
                 WHERE n.nspname='public' AND c.relkind IN ('r','p')
                 ORDER BY c.relname
                """
            )
        ).scalars()
    )
    privilege_row = connection.execute(
        text(
            """
            SELECT has_table_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','SELECT'
                   )::int
                 + has_table_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','INSERT'
                   )::int
                 + has_table_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','UPDATE'
                   )::int
                 + has_table_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','DELETE'
                   )::int
                 + has_table_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','TRUNCATE'
                   )::int
                 + has_table_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','REFERENCES'
                   )::int
                 + has_table_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','TRIGGER'
                   )::int
                 + has_any_column_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','SELECT'
                   )::int
                 + has_any_column_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','INSERT'
                   )::int
                 + has_any_column_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','UPDATE'
                   )::int
                 + has_any_column_privilege(
                       CAST(:application_role AS name),
                       'public.alembic_version','REFERENCES'
                   )::int AS effective_privilege_count
            """
        ),
        {"application_role": application_role},
    ).one()
    effective_privilege_count = int(privilege_row[0])
    direct_privilege_row = connection.execute(
        text(
            """
            SELECT
              (SELECT count(*)
                 FROM pg_catalog.pg_class AS c
                 JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                     COALESCE(c.relacl,ARRAY[]::aclitem[])
                 ) AS acl
                 JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                WHERE n.nspname='public' AND c.relname='alembic_version'
                  AND grantee.rolname=:application_role)
              +
              (SELECT count(*)
                 FROM pg_catalog.pg_class AS c
                 JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace
                 JOIN pg_catalog.pg_attribute AS a ON a.attrelid=c.oid
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                     COALESCE(a.attacl,ARRAY[]::aclitem[])
                 ) AS acl
                 JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                WHERE n.nspname='public' AND c.relname='alembic_version'
                  AND a.attnum > 0 AND NOT a.attisdropped
                  AND grantee.rolname=:application_role)
              AS direct_privilege_count
            """
        ),
        {"application_role": application_role},
    ).one()
    direct_privilege_count = int(direct_privilege_row[0])
    payload = _pregrant_application_catalog_payload()
    if (
        observed_physical != _PHYSICAL_PUBLIC_RELATION_TUPLE
        or direct_privilege_count != 0
        or effective_privilege_count != 0
    ):
        _fail("TASK8_V27_CENSUS_APPLICATION_CATALOG_INVALID")
    receipt = {
        **payload,
        "observed_before_any_grant": True,
        "application_role": application_role,
    }
    return {
        **receipt,
        "expected_sha256": _canonical_sha256(
            ["task8-v27-pregrant-application-catalog-v1", receipt]
        ),
    }


def _grant_and_verify_application_schema_privileges(
    connection: Any,
    *,
    engine: Any,
    application_role: str,
    pregrant_catalog: Mapping[str, object],
) -> dict[str, object]:
    """Replay and verify the production post-migration app CRUD boundary."""

    try:
        from sqlalchemy import text

        from idis.runtime.strict_launcher import _migration_privilege_catalog_is_exact
    except ImportError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_APPLICATION_GRANT_INVALID") from exc
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", application_role) is None:
        _fail("TASK8_V27_CENSUS_APPLICATION_GRANT_INVALID")
    pregrant_payload = {
        key: value for key, value in pregrant_catalog.items() if key != "expected_sha256"
    }
    if (
        pregrant_catalog.get("physical_public_relation_tuple")
        != list(_PHYSICAL_PUBLIC_RELATION_TUPLE)
        or pregrant_catalog.get("application_relation_tuple") != list(_APPLICATION_RELATION_TUPLE)
        or pregrant_catalog.get("excluded_application_direct_privilege_count") != 0
        or pregrant_catalog.get("excluded_application_effective_privilege_count") != 0
        or pregrant_catalog.get("observed_before_any_grant") is not True
        or pregrant_catalog.get("application_role") != application_role
        or pregrant_catalog.get("expected_sha256")
        != _canonical_sha256(["task8-v27-pregrant-application-catalog-v1", pregrant_payload])
    ):
        _fail("TASK8_V27_CENSUS_APPLICATION_GRANT_INVALID")
    table_names = _APPLICATION_RELATION_TUPLE
    preparer = engine.dialect.identifier_preparer
    quoted_role = preparer.quote(application_role)
    connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")
    connection.exec_driver_sql(
        "REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
        f"ON TABLE public.alembic_version FROM {quoted_role}"
    )
    connection.exec_driver_sql(
        "REVOKE SELECT (version_num), INSERT (version_num), "
        "UPDATE (version_num), REFERENCES (version_num) "
        f"ON TABLE public.alembic_version FROM {quoted_role}"
    )
    for table_name in table_names:
        quoted_table = preparer.quote(table_name)
        connection.exec_driver_sql(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{quoted_table} TO {quoted_role}"
        )
    target_database = str(engine.url.database or "")
    if not target_database or not _migration_privilege_catalog_is_exact(
        connection,
        table_names=table_names,
        app_role=application_role,
        target_database=target_database,
        text=text,
    ):
        _fail("TASK8_V27_CENSUS_APPLICATION_GRANT_INVALID")
    payload = {
        "schema_version": "task8-v27-application-privilege-boundary-v1",
        "pregrant_catalog_sha256": pregrant_catalog["expected_sha256"],
        "granted_application_relation_count": 66,
        "granted_application_relation_tuple_sha256": (_APPLICATION_RELATION_TUPLE_SHA256),
        "excluded_application_relation_tuple": list(_EXCLUDED_APPLICATION_RELATION_TUPLE),
        "excluded_application_direct_privilege_count": 0,
        "excluded_application_effective_privilege_count": 0,
        "verified_by_production_catalog_predicate": True,
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(
            ["task8-v27-application-privilege-boundary-v1", payload]
        ),
    }


def _pregrant_execution_binding(
    authority_execution_binding: Mapping[str, object],
) -> dict[str, object]:
    """Validate and flatten the exact observed 67/66/Alembic privilege boundary."""

    pregrant = authority_execution_binding.get("pregrant_application_catalog")
    privilege = authority_execution_binding.get("application_privilege_boundary")
    if not isinstance(pregrant, Mapping) or not isinstance(privilege, Mapping):
        _fail("TASK8_V27_CENSUS_APPLICATION_CATALOG_INVALID")
    expected_pregrant_payload = _pregrant_application_catalog_payload()
    pregrant_payload = {key: value for key, value in pregrant.items() if key != "expected_sha256"}
    privilege_payload = {key: value for key, value in privilege.items() if key != "expected_sha256"}
    application_role = pregrant.get("application_role")
    if (
        tuple(pregrant)
        != (
            *expected_pregrant_payload,
            "observed_before_any_grant",
            "application_role",
            "expected_sha256",
        )
        or not isinstance(application_role, str)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", application_role) is None
        or any(pregrant.get(key) != value for key, value in expected_pregrant_payload.items())
        or pregrant.get("observed_before_any_grant") is not True
        or pregrant.get("expected_sha256")
        != _canonical_sha256(["task8-v27-pregrant-application-catalog-v1", pregrant_payload])
        or tuple(privilege)
        != (
            "schema_version",
            "pregrant_catalog_sha256",
            "granted_application_relation_count",
            "granted_application_relation_tuple_sha256",
            "excluded_application_relation_tuple",
            "excluded_application_direct_privilege_count",
            "excluded_application_effective_privilege_count",
            "verified_by_production_catalog_predicate",
            "expected_sha256",
        )
        or privilege.get("schema_version") != "task8-v27-application-privilege-boundary-v1"
        or privilege.get("pregrant_catalog_sha256") != pregrant.get("expected_sha256")
        or privilege.get("granted_application_relation_count") != 66
        or privilege.get("granted_application_relation_tuple_sha256")
        != _APPLICATION_RELATION_TUPLE_SHA256
        or privilege.get("excluded_application_relation_tuple")
        != list(_EXCLUDED_APPLICATION_RELATION_TUPLE)
        or privilege.get("excluded_application_direct_privilege_count") != 0
        or privilege.get("excluded_application_effective_privilege_count") != 0
        or privilege.get("verified_by_production_catalog_predicate") is not True
        or privilege.get("expected_sha256")
        != _canonical_sha256(["task8-v27-application-privilege-boundary-v1", privilege_payload])
    ):
        _fail("TASK8_V27_CENSUS_APPLICATION_CATALOG_INVALID")
    return {
        "pregrant_physical_public_relation_count": 67,
        "pregrant_application_relation_count": 66,
        "pregrant_excluded_relation_count": 1,
        "pregrant_excluded_application_direct_privilege_count": 0,
        "pregrant_excluded_application_effective_privilege_count": 0,
        "pregrant_application_catalog_sha256": pregrant["expected_sha256"],
        "application_privilege_boundary_sha256": privilege["expected_sha256"],
    }


def _setup_disposable_schema(
    admin_database_url: str,
    *,
    migration_snapshot: _MigrationSourceSnapshot,
    application_role: str,
    erasure_role: str,
    v27: Mapping[str, Any],
    authority_execution: Mapping[str, Any],
) -> dict[str, Any]:
    """Install the real v21 source schema plus exact reviewed v27 authority bytes."""

    try:
        from sqlalchemy import create_engine, text
    except ImportError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_SQLALCHEMY_UNAVAILABLE") from exc

    future = build_future_table_scaffold(v27, application_role)
    application_grants = build_application_behavior_grants(v27, application_role)
    engine = create_engine(admin_database_url)
    try:
        # The exact v27 guards reference the real 0038 parent authorities.  An
        # empty/toy schema cannot prove their PostgreSQL or RLS semantics.
        _run_migrations_through_0038(engine, migration_snapshot)
        with engine.begin() as connection:
            future_application_grants = tuple(
                statement
                for statement in future["statement_tuple"]
                if statement.lstrip().upper().startswith("GRANT ")
            )
            declared_application_grants = (
                *application_grants["statement_tuple"],
                *future_application_grants,
            )
            if not declared_application_grants or any(
                "alembic_version" in statement for statement in declared_application_grants
            ):
                _fail("TASK8_V27_CENSUS_APPLICATION_GRANT_INVALID")
            for statement in future["statement_tuple"]:
                if not statement.lstrip().upper().startswith("GRANT "):
                    connection.exec_driver_sql(statement.replace("%", "%%"))
            # 0038 installs the v21 runner under this frozen signature.  The
            # v27 body is CREATE FUNCTION by contract, so remove exactly that
            # predecessor before executing the reviewed replacement bytes.
            connection.exec_driver_sql(
                "DROP FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
            )
            pregrant_catalog = _observe_pregrant_application_catalog(
                connection,
                application_role=application_role,
                text=text,
            )
            binding = execute_hash_bound_authority_sql(
                _SqlAlchemyCursor(connection),
                authority_execution,
            )
            application_privilege_boundary = _grant_and_verify_application_schema_privileges(
                connection,
                engine=engine,
                application_role=application_role,
                pregrant_catalog=pregrant_catalog,
            )
    finally:
        engine.dispose()
    if binding["bundle_sha256"] != authority_execution["expected_sha256"]:
        _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
    return {
        **binding,
        "pregrant_application_catalog": pregrant_catalog,
        "application_privilege_boundary": application_privilege_boundary,
    }


def _set_tenant(cursor: Any, tenant_id: uuid.UUID) -> None:
    cursor.execute(
        "SELECT pg_catalog.set_config('idis.tenant_id', %s, true)",
        (str(tenant_id),),
    )


@dataclass(frozen=True)
class _CaseScope:
    case_coordinate: str
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


def _seed_scope(
    application_url: str,
    expected_application_role: str,
    case_coordinate: str,
) -> _CaseScope:
    tenant_id, deal_id, run_id = _case_scope_identity(case_coordinate)
    lease_owner_id = uuid.UUID(
        _fixture_uuid("lease-owner", str(tenant_id), str(deal_id), str(run_id))
    )
    steps = build_current_run_step_plan(
        tenant_id=str(tenant_id),
        deal_id=str(deal_id),
        run_id=str(run_id),
    )
    step_by_order = {int(row["step_order"]): row for row in steps}
    scope = _CaseScope(
        case_coordinate=case_coordinate,
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        lease_owner_id=lease_owner_id,
        parent_step_id=uuid.UUID(str(step_by_order[6]["step_id"])),
        dashboard_step_id=uuid.UUID(str(step_by_order[11]["step_id"])),
    )
    connection = _connect_fixture_dml(
        application_url,
        expected_application_role=expected_application_role,
        tenant_id=tenant_id,
        case_coordinate=case_coordinate,
        operation_prefix="SCOPE_SEED",
    )
    try:
        with connection.cursor() as cursor:
            _set_tenant(cursor, tenant_id)
            cursor.execute(
                """
                INSERT INTO public.tenants(tenant_id,name)
                VALUES (%s,'Task8 census tenant')
                """,
                (tenant_id,),
            )
            cursor.execute(
                """
                INSERT INTO public.deals(deal_id,tenant_id,name,created_at,status)
                VALUES (%s,%s,'Task8 census deal',%s,'NEW')
                """,
                (deal_id, tenant_id, _FIXTURE_OCCURRED_AT),
            )
            cursor.execute(
                """
                INSERT INTO public.runs(
                    run_id,tenant_id,deal_id,mode,status,lease_owner_id,
                    lease_fencing_token,lease_expires_at,last_heartbeat_at,
                    pipeline_contract_version,pipeline_contract_hash,
                    pipeline_resume_state
                ) VALUES (
                    %s,%s,%s,'FULL','RUNNING',%s,1,
                    transaction_timestamp()+interval '1 hour',
                    transaction_timestamp(),
                    %s,%s,'RESUMABLE'
                )
                """,
                (
                    run_id,
                    tenant_id,
                    deal_id,
                    lease_owner_id,
                    CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
                    CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
                ),
            )
            _set_worker_lease(cursor, scope)
            for step in steps:
                cursor.execute(
                    """
                    INSERT INTO public.run_steps(
                        step_id,tenant_id,run_id,step_name,step_order,status,
                        pipeline_contract_version,pipeline_contract_hash,
                        semantic_rule_version,semantic_rule_hash
                    ) VALUES (
                        %s,%s,%s,%s,%s,'RUNNING',%s,%s,%s,%s
                    )
                    """,
                    (
                        uuid.UUID(str(step["step_id"])),
                        tenant_id,
                        run_id,
                        step["step_name"],
                        step["step_order"],
                        step["pipeline_contract_version"],
                        step["pipeline_contract_hash"],
                        step["semantic_contract_version"],
                        step["semantic_contract_hash"],
                    ),
                )
        connection.commit()
    finally:
        connection.close()
    return scope


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


@dataclass(frozen=True)
class _NativeRootPlan:
    native_id: str
    output_kind: str
    output_object_id: str
    step_output_id: str
    output_schema_version: str
    output_ordinal: int
    native_identity_sha256: str
    native_output_sha256: str
    summary_sha256: str
    step: Mapping[str, object]


def _derive_seed_native_root(
    *,
    scope: _CaseScope,
    step: Mapping[str, object],
    native_id: str,
    output_kind: str,
    output_schema_version: str,
    output_ordinal: int,
    native_key: object,
    native_payload: object,
) -> _NativeRootPlan:
    """Independently derive final native hashes, root UUID, and step-output UUID."""

    if (
        not native_id
        or output_kind not in _NATIVE_OUTPUT_STEP_BY_KIND
        or step.get("step_order") != _NATIVE_OUTPUT_STEP_BY_KIND[output_kind]
        or type(output_ordinal) is not int
        or output_ordinal < 0
    ):
        _fail("TASK8_V27_CENSUS_NATIVE_ROOT_INVALID")
    native_identity_sha256 = _canonical_sha256(
        [
            "native-identity-v1",
            str(scope.tenant_id),
            str(scope.deal_id),
            str(scope.run_id),
            output_kind,
            native_key,
        ]
    )
    native_output_sha256 = _canonical_sha256(
        [
            "native-output-v1",
            str(scope.tenant_id),
            str(scope.deal_id),
            str(scope.run_id),
            CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
            CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
            output_kind,
            native_key,
            native_payload,
        ]
    )
    output_object_id = _uuid5_from_preimage(
        _OUTPUT_OBJECT_NAMESPACE,
        [
            "run-output-object-v1",
            str(scope.tenant_id),
            str(scope.deal_id),
            str(scope.run_id),
            CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
            CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
            "NATIVE_ROW",
            output_kind,
            native_identity_sha256,
            native_output_sha256,
        ],
    )
    step_output_id = _uuid5_from_preimage(
        _STEP_OUTPUT_NAMESPACE,
        [
            "run-step-output-v1",
            str(scope.tenant_id),
            str(scope.deal_id),
            str(scope.run_id),
            CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
            str(step["step_id"]),
            str(step["step_name"]),
            "__step__",
            output_kind,
            output_ordinal,
            output_object_id,
        ],
    )
    canonical_value_sha256 = _canonical_sha256(native_payload)
    summary_sha256 = _canonical_sha256(
        [
            "step-output-summary-v1",
            str(step["step_name"]),
            "__step__",
            output_kind,
            canonical_value_sha256,
            native_identity_sha256,
            native_output_sha256,
        ]
    )
    return _NativeRootPlan(
        native_id=native_id,
        output_kind=output_kind,
        output_object_id=output_object_id,
        step_output_id=step_output_id,
        output_schema_version=output_schema_version,
        output_ordinal=output_ordinal,
        native_identity_sha256=native_identity_sha256,
        native_output_sha256=native_output_sha256,
        summary_sha256=summary_sha256,
        step=step,
    )


def _persist_seed_native_root(
    cursor: Any,
    *,
    scope: _CaseScope,
    plan: _NativeRootPlan,
    finalize: bool = False,
) -> str:
    """Insert one final RUNTIME root; its native row is inserted by the caller."""

    cursor.execute("SET CONSTRAINTS ALL DEFERRED")
    cursor.execute(
        """
        INSERT INTO public.run_output_objects(
            output_object_id,tenant_id,deal_id,run_id,output_class,output_kind,
            output_schema_version,native_identity_sha256,native_output_sha256,
            pipeline_contract_version,pipeline_contract_hash,write_origin,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,'NATIVE_ROW',%s,%s,%s,%s,%s,%s,
            'RUNTIME',%s,1
        );
        INSERT INTO public.run_step_outputs(
            step_output_id,tenant_id,deal_id,run_id,step_id,output_object_id,
            step_name,subrole,output_kind,output_ordinal,
            semantic_rule_version,semantic_rule_hash,summary_sha256,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,%s,%s,%s,'__step__',%s,%s,%s,%s,%s,%s,1
        )
        """,
        (
            uuid.UUID(plan.output_object_id),
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            plan.output_kind,
            plan.output_schema_version,
            plan.native_identity_sha256,
            plan.native_output_sha256,
            CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
            CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
            scope.lease_owner_id,
            uuid.UUID(plan.step_output_id),
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            uuid.UUID(str(plan.step["step_id"])),
            uuid.UUID(plan.output_object_id),
            plan.step["step_name"],
            plan.output_kind,
            plan.output_ordinal,
            plan.step["semantic_contract_version"],
            plan.step["semantic_contract_hash"],
            plan.summary_sha256,
            scope.lease_owner_id,
        ),
    )
    if finalize:
        _verify_seed_native_roots(cursor, scope=scope, plans=(plan,))
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return plan.output_object_id


def _verify_seed_native_roots(
    cursor: Any,
    *,
    scope: _CaseScope,
    plans: tuple[_NativeRootPlan, ...],
) -> str:
    """Compare every independent hash to 0038's SQL helper before constraints fire."""

    observed: list[list[str]] = []
    for plan in plans:
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
                uuid.UUID(plan.output_object_id),
            ),
        )
        rows = cursor.fetchall()
        expected = [
            plan.output_kind,
            plan.native_identity_sha256,
            plan.native_output_sha256,
        ]
        if len(rows) != 1 or [str(value) for value in rows[0]] != expected:
            _fail("TASK8_V27_CENSUS_NATIVE_HELPER_MISMATCH")
        observed.append([plan.native_id, *expected, plan.output_object_id])
    return _canonical_sha256(["task8-v27-seed-native-helper-equality-v1", sorted(observed)])


@dataclass(frozen=True)
class _SeedSource:
    document_id: str
    span_id: str
    text: str
    content_sha256: str
    locator: Mapping[str, object]
    locator_sha256: str


def _stage_and_freeze_source_graph(
    *,
    application_url: str,
    expected_application_role: str,
    scope: _CaseScope,
    document_count: int,
) -> tuple[tuple[_SeedSource, ...], str]:
    """Stage deterministic OPEN inputs and freeze them through the app boundary."""

    if type(document_count) is not int or document_count <= 0:
        _fail("TASK8_V27_CENSUS_SOURCE_GRAPH_INVALID")
    try:
        from sqlalchemy import create_engine, text

        from idis.models.source_package_manifest import canonical_source_manifest_sha256
        from idis.persistence.db import set_tenant_local
        from idis.persistence.repositories.task7_source_freeze import freeze_source_package
    except ImportError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_SOURCE_GRAPH_INVALID") from exc

    package_id = _fixture_uuid("source-package", scope.tenant_id, scope.deal_id)
    files: list[dict[str, object]] = []
    seed_rows: list[dict[str, object]] = []
    sources: list[_SeedSource] = []
    terminal_projection = {
        "parser_support_status": "supported",
        "parser_triage_status": "ready",
        "applicable_schema_refs": [],
    }
    terminal_receipt_sha256 = _canonical_sha256(
        ["terminal-document-metadata-v2", "DOCX", "PARSED", terminal_projection]
    )
    for ordinal in range(document_count):
        label = [str(scope.tenant_id), str(scope.deal_id), str(scope.run_id), ordinal]
        file_entry_id = _fixture_uuid("source-file", *label)
        doc_id = _fixture_uuid("source-artifact", *label)
        document_id = _fixture_uuid("source-document", *label)
        attempt_id = _fixture_uuid("source-attempt", *label)
        span_id = _fixture_uuid("source-span", *label)
        version_id = _fixture_uuid("source-version", *label)
        text_value = f"Fictional governed Task-8 census evidence row {ordinal + 1}."
        content_sha256 = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
        locator = {"paragraph": ordinal + 1}
        locator_sha256 = _canonical_sha256(locator)
        object_uri = f"object://fictional-task8/{doc_id}"
        files.append(
            {
                "sequence": ordinal,
                "generated_upload_name": f"task8-census-document-{ordinal + 1:05d}.docx",
                "source_locator_sha256": locator_sha256,
                "content_sha256": content_sha256,
                "size_bytes": len(text_value.encode("utf-8")),
                "extension": "docx",
                "document_id": document_id,
                "doc_id": doc_id,
            }
        )
        artifact_metadata = {
            "durable_object_integrity": {
                "schema_version": "durable-object-integrity-v1",
                "doc_id": doc_id,
                "content_sha256": content_sha256,
                "size_bytes": len(text_value.encode("utf-8")),
                "uri_sha256": hashlib.sha256(object_uri.encode("utf-8")).hexdigest(),
                "version_id": version_id,
                "backend": "compliant:postgres-census-object-store",
                "streaming_verified": True,
            }
        }
        document_metadata = {
            "parser_runtime": "built_in",
            "parser_runtime_status": "completed",
            "parser_support_status": "supported",
            "parser_triage_status": "ready",
            "parser_reason_codes": ["docx_parse_completed"],
            "parse_warning_codes": [],
            "parse_error_codes": [],
            "applicable_schema_refs": [],
            "span_count": 1,
        }
        seed_rows.append(
            {
                "file_entry_id": file_entry_id,
                "doc_id": doc_id,
                "document_id": document_id,
                "attempt_id": attempt_id,
                "span_id": span_id,
                "version_id": version_id,
                "sequence": ordinal,
                "content_sha256": content_sha256,
                "locator_sha256": locator_sha256,
                "locator": locator,
                "text": text_value,
                "object_uri": object_uri,
                "artifact_metadata": artifact_metadata,
                "document_metadata": document_metadata,
            }
        )
        sources.append(
            _SeedSource(
                document_id=document_id,
                span_id=span_id,
                text=text_value,
                content_sha256=content_sha256,
                locator=locator,
                locator_sha256=locator_sha256,
            )
        )
    manifest: dict[str, object] = {
        "schema_version": "source-package-manifest-v1",
        "company_alias": "task8-census-company",
        "deal_alias": "task8-census-deal",
        "document_set_alias": "task8-census-documents",
        "run_alias": "task8-census-run",
        "ledger_alias": "task8-census-ledger",
        "package_alias": "task8-census-package",
        "source_tree_fingerprint": _fixture_hash("source-tree", scope.deal_id),
        "file_count": document_count,
        "total_bytes": sum(int(row["size_bytes"]) for row in files),
        "files": files,
    }
    manifest["manifest_sha256"] = canonical_source_manifest_sha256(manifest)

    application_engine = create_engine(application_url)
    _instrument_fixture_dml_engine(
        application_engine,
        expected_application_role=expected_application_role,
        tenant_id=scope.tenant_id,
        case_coordinate=scope.case_coordinate,
        operation_prefix="SOURCE_GRAPH",
    )
    try:
        with application_engine.begin() as connection:
            with _instrumented_dbapi_cursor(connection) as posture_cursor:
                _attest_connection_role_posture(
                    posture_cursor,
                    expected_application_role,
                )
            set_tenant_local(connection, str(scope.tenant_id))
            connection.execute(
                text(
                    """
                    INSERT INTO data_room_packages(
                        package_id,tenant_id,deal_id,status,metadata,created_at,updated_at,
                        source_binding_state,source_lineage_object_id
                    ) VALUES (
                        CAST(:package_id AS uuid),CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid),'OPEN',CAST(:metadata AS jsonb),
                        CAST(:occurred_at AS timestamptz),CAST(:occurred_at AS timestamptz),
                        'UNBOUND',NULL
                    )
                    """
                ),
                {
                    "package_id": package_id,
                    "tenant_id": str(scope.tenant_id),
                    "deal_id": str(scope.deal_id),
                    "metadata": json.dumps({"source_manifest": manifest}),
                    "occurred_at": _FIXTURE_OCCURRED_AT,
                },
            )
            for row in seed_rows:
                connection.execute(
                    text(
                        """
                        INSERT INTO document_artifacts(
                            doc_id,tenant_id,deal_id,doc_type,title,source_system,version_id,
                            ingested_at,sha256,uri,metadata,source_binding_state,
                            source_lineage_object_id,source_corpus_receipt_id
                        ) VALUES (
                            CAST(:doc_id AS uuid),CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid),'DATA_ROOM_FILE',:title,
                            'task8-postgres-census',CAST(:version_id AS uuid),
                            CAST(:occurred_at AS timestamptz),:content_sha256,:object_uri,
                            CAST(:artifact_metadata AS jsonb),'UNBOUND',NULL,NULL
                        );
                        INSERT INTO documents(
                            document_id,tenant_id,deal_id,doc_id,doc_type,parse_status,
                            metadata,created_at,updated_at,source_binding_state,
                            source_lineage_object_id,source_corpus_receipt_id
                        ) VALUES (
                            CAST(:document_id AS uuid),CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid),CAST(:doc_id AS uuid),'DOCX','PARSED',
                            CAST(:document_metadata AS jsonb),CAST(:occurred_at AS timestamptz),
                            CAST(:occurred_at AS timestamptz),'UNBOUND',NULL,NULL
                        );
                        INSERT INTO document_parse_attempts(
                            parse_attempt_id,tenant_id,deal_id,document_id,attempt_ordinal,
                            parser_name,parser_version,input_sha256,receipt_sha256,status,
                            started_at,finished_at,source_binding_state,source_lineage_object_id,
                            source_corpus_receipt_id,write_origin
                        ) VALUES (
                            CAST(:attempt_id AS uuid),CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid),CAST(:document_id AS uuid),1,
                            'built_in','runtime-parser-v1',:content_sha256,
                            :terminal_receipt_sha256,'PARSED',CAST(:occurred_at AS timestamptz),
                            CAST(:occurred_at AS timestamptz),'UNBOUND',NULL,NULL,'RUNTIME'
                        );
                        INSERT INTO document_spans(
                            span_id,tenant_id,deal_id,document_id,span_type,locator,text_excerpt,
                            content_hash,parse_attempt_id,source_binding_state,
                            source_lineage_object_id,source_corpus_receipt_id
                        ) VALUES (
                            CAST(:span_id AS uuid),CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid),CAST(:document_id AS uuid),'PARAGRAPH',
                            CAST(:locator AS jsonb),:span_text,:content_sha256,NULL,
                            'UNBOUND',NULL,NULL
                        );
                        INSERT INTO data_room_package_files(
                            file_entry_id,tenant_id,package_id,deal_id,sequence,path_hash,
                            extension,sha256,file_status,support_status,triage_status,
                            parse_status,reason_codes,error_codes,doc_id,document_id,
                            storage_uri,source_binding_state,source_lineage_object_id
                        ) VALUES (
                            CAST(:file_entry_id AS uuid),CAST(:tenant_id AS uuid),
                            CAST(:package_id AS uuid),CAST(:deal_id AS uuid),:sequence,
                            :locator_sha256,'docx',:content_sha256,'supported','supported',
                            'ready','PARSED','[\"docx_parse_completed\"]'::jsonb,
                            '[]'::jsonb,NULL,NULL,NULL,'UNBOUND',NULL
                        )
                        """
                    ),
                    {
                        **row,
                        "tenant_id": str(scope.tenant_id),
                        "deal_id": str(scope.deal_id),
                        "package_id": package_id,
                        "title": f"Fictional Task-8 source {int(row['sequence']) + 1}",
                        "artifact_metadata": json.dumps(row["artifact_metadata"]),
                        "document_metadata": json.dumps(row["document_metadata"]),
                        "locator": json.dumps(row["locator"]),
                        "span_text": row["text"],
                        "terminal_receipt_sha256": terminal_receipt_sha256,
                        "occurred_at": _FIXTURE_OCCURRED_AT,
                    },
                )
        frozen = freeze_source_package(
            engine=application_engine,
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            package_id=package_id,
        )
        if int(frozen.root_count) != 1 + 5 * document_count:
            _fail("TASK8_V27_CENSUS_SOURCE_GRAPH_INVALID")
    except Exception as exc:
        if isinstance(exc, Task8V27CensusError):
            raise
        raise Task8V27CensusError("TASK8_V27_CENSUS_SOURCE_GRAPH_INVALID") from exc
    finally:
        application_engine.dispose()

    observation = _connect(application_url)
    try:
        with observation.cursor() as cursor:
            observer_posture = _attest_connection_role_posture(cursor, expected_application_role)
            observer = _posture_role(observer_posture)
            _set_tenant(cursor, scope.tenant_id)
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                """
                SELECT p.status,p.source_binding_state,
                       (SELECT count(*) FROM public.source_lineage_objects r
                         WHERE r.tenant_id=p.tenant_id AND r.deal_id=p.deal_id
                           AND r.corpus_receipt_id=p.package_id),
                       (SELECT count(*) FROM public.data_room_package_files f
                         WHERE f.package_id=p.package_id AND f.source_binding_state='BOUND'),
                       (SELECT count(*) FROM public.documents d
                         WHERE d.source_corpus_receipt_id=p.package_id
                           AND d.source_binding_state='BOUND'),
                       (SELECT count(*) FROM public.document_spans s
                         WHERE s.source_corpus_receipt_id=p.package_id
                           AND s.source_binding_state='BOUND'),
                       (SELECT count(*) FROM public.source_lineage_objects r
                         WHERE r.tenant_id=p.tenant_id AND r.deal_id=p.deal_id
                           AND r.corpus_receipt_id=p.package_id
                           AND public.idis_task7_source_projection_v1(
                               r.source_lineage_object_id
                           ) IS NOT NULL)
                  FROM public.data_room_packages p
                 WHERE p.package_id=%s
                """,
                (uuid.UUID(package_id),),
            )
            row = cursor.fetchone()
        observation.rollback()
    finally:
        observation.close()
    expected = (
        "SEALED",
        "BOUND",
        1 + 5 * document_count,
        document_count,
        document_count,
        document_count,
        1 + 5 * document_count,
    )
    if row is None or tuple(row) != expected or observer != expected_application_role:
        _fail("TASK8_V27_CENSUS_SOURCE_GRAPH_INVALID")
    return tuple(sources), _canonical_sha256(
        ["task8-v27-source-freeze-observation-v1", observer, package_id, list(expected)]
    )


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


@dataclass(frozen=True)
class _BoundRuntimeGraph:
    graphs: tuple[_DashboardGraph, ...]
    dashboard_seed_plan_sha256: str
    dashboard_seed_db_projection_sha256: str
    seed_fk_closure_sha256: str
    seed_native_helper_equality_sha256: str
    source_freeze_sha256: str
    task5_roundtrip_sha256: str


def _task_native_payload(spec: Any) -> list[object]:
    task = spec.record.task
    return [
        spec.record.selection_id,
        spec.record.coverage_record_id,
        spec.record.methodology_version_record_id,
        task.methodology_version_id,
        spec.record.methodology_question_record_id,
        task.methodology_question_id,
        task.methodology_type.value,
        task.extraction_task_id,
        task.document_id,
        task.classification_id,
        spec.classification_sha256,
        "selected-pair-planner-v2",
        "methodology-extraction-task-v2",
        spec.record.task_input_sha256,
        spec.record.candidate_span_set_sha256,
        [
            [
                span.span_ordinal,
                span.task_span_record_id,
                span.document_id,
                span.span_id,
                span.content_sha256,
                span.locator_sha256,
            ]
            for span in spec.spans
        ],
    ]


def _result_native_payload(
    *,
    task_record_id: str,
    attempt_id: str,
    task_input_sha256: str,
    result: Any,
    provider_provenance_sha256: str,
) -> list[object]:
    return [
        task_record_id,
        attempt_id,
        task_input_sha256,
        result.outcome,
        result.validated_result_sha256,
        sorted(result.searched_document_ids),
        sorted(result.searched_span_ids),
        result.searched_scope_sha256,
        provider_provenance_sha256,
        None if result.gap_finding is None else result.gap_finding.model_dump(mode="json"),
    ]


def _factual_native_payload(
    *,
    result_id: str,
    task_record_id: str,
    factual_output_record_id: str,
    output: Any,
) -> list[object]:
    payload = output.model_dump(mode="json")
    typed_sha256 = _canonical_sha256(payload)
    pairs = sorted(
        [
            [span_id, locator_sha256]
            for span_id, locator_sha256 in zip(
                output.accepted_source_span_ids,
                output.accepted_locator_sha256s,
                strict=True,
            )
        ]
    )
    return [
        result_id,
        task_record_id,
        1,
        output.factual_output_id,
        typed_sha256,
        pairs,
        _canonical_sha256(pairs),
    ]


def _link_native_payload(output_kind: str, record: Any, **derived: object) -> list[object]:
    if output_kind == "METHODOLOGY_CLAIM_LINK":
        return [
            record.result_id,
            record.factual_output_record_id,
            derived["selection_id"],
            record.coverage_record_id,
            derived["methodology_version_record_id"],
            derived["methodology_question_record_id"],
            record.claim_id,
            record.canonical_key_sha256,
            record.reused_existing_claim,
        ]
    if output_kind == "METHODOLOGY_EVIDENCE_LINK":
        return [
            record.result_id,
            record.factual_output_record_id,
            record.claim_link_id,
            record.evidence_id,
            record.document_id,
            record.source_span_id,
            derived["locator_sha256"],
        ]
    if output_kind == "METHODOLOGY_SANAD_LINK":
        return [
            record.result_id,
            record.factual_output_record_id,
            record.claim_link_id,
            record.sanad_id,
            record.primary_evidence_id,
        ]
    if output_kind == "METHODOLOGY_DEFECT_LINK":
        return [
            record.result_id,
            record.factual_output_record_id,
            record.sanad_link_id,
            record.defect_id,
        ]
    _fail("TASK8_V27_CENSUS_NATIVE_ROOT_INVALID")


def _persist_runtime_graph_core(
    *,
    urls: _DisposableUrls,
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    scope: _CaseScope,
    focus_semantic_case: str,
    coverage_row_count: int = 100,
    financial_coverage_row_count: int = 79,
    allow_partial: bool = False,
    requirement_seed: _RequirementSeedSpec | None = None,
) -> _BoundRuntimeGraph:
    """Build one trigger-enabled, FK-closed runtime graph through production seams."""

    coverage_shape = (coverage_row_count, financial_coverage_row_count)
    if coverage_shape not in {(100, 79), (99, 78)}:
        _fail("TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    if coverage_shape == (99, 78) and not allow_partial:
        _fail("TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    if requirement_seed is not None and coverage_shape != (100, 79):
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    try:
        from sqlalchemy import create_engine

        from idis.audit.postgres_sink import PostgresAuditSink
        from idis.models.extraction_task import (
            ExtractionTask,
            ExtractionTaskStatus,
            SourceSpanReference,
        )
        from idis.models.methodology_extraction import (
            MethodologyCandidateSpan,
            MethodologyFactualOutput,
            MethodologyGapFinding,
            MethodologyResultClaimLink,
            MethodologyResultDefectLink,
            MethodologyResultEvidenceLink,
            MethodologyResultSanadLink,
            ValidatedDispatchedMethodologyResult,
            compute_methodology_provider_receipt_sha256,
            compute_methodology_searched_scope_sha256,
            generate_methodology_result_link_id,
        )
        from idis.persistence.db import set_tenant_local
        from idis.persistence.repositories.methodology_extraction import (
            PostgresMethodologyExtractionRepository,
        )
        from idis.services.extraction.task_planner import build_expected_answer_schema
        from idis.services.runs.methodology_postgres_materialization_runtime import (
            PostgresCanonicalMethodologyMaterializationBoundary,
            _canonical_claim_payload,
            _canonical_sanad_payload,
        )
    except ImportError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_RUNTIME_GRAPH_INVALID") from exc

    if requirement_seed is None:
        cdd_raw = _capture_cdd_template_bytes()
        cdd_byte_count = len(cdd_raw)
        scope_plan = build_v27_dashboard_seed_plan(
            artifact,
            policy_artifact_raw,
            workbook_raw,
            cdd_raw=cdd_raw,
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            run_id=str(scope.run_id),
        )
        if coverage_row_count == 99:
            scope_plan = _partial_dashboard_seed_plan(
                scope_plan,
                coverage_row_count=coverage_row_count,
                financial_coverage_row_count=financial_coverage_row_count,
            )
        commercial, financial = _production_methodology_registries(
            cdd_raw=cdd_raw,
            policy_artifact_raw=policy_artifact_raw,
            workbook_raw=workbook_raw,
        )
    else:
        scope_plan = requirement_seed.dashboard_seed_plan
        commercial = requirement_seed.commercial_registry
        financial = requirement_seed.financial_registry
        cdd_byte_count = requirement_seed.cdd_byte_count
    sources, source_freeze_sha256 = _stage_and_freeze_source_graph(
        application_url=urls.application,
        expected_application_role=urls.application_role,
        scope=scope,
        document_count=coverage_row_count,
    )
    steps = {
        int(row["step_order"]): row
        for row in build_current_run_step_plan(
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            run_id=str(scope.run_id),
        )
    }
    question_by_key = {
        (question.methodology_version_id, question.methodology_question_id): question
        for registry in (commercial, financial)
        for question in registry.current_version.questions
    }
    engine = create_engine(urls.application)
    _instrument_fixture_dml_engine(
        engine,
        expected_application_role=urls.application_role,
        tenant_id=scope.tenant_id,
        case_coordinate=scope.case_coordinate,
        operation_prefix="RUNTIME_GRAPH",
    )
    native_receipts: list[str] = []
    terminal_rows: list[dict[str, object]] = []
    try:
        with engine.begin() as connection:
            set_tenant_local(connection, str(scope.tenant_id))
            raw_cursor = _instrumented_dbapi_cursor(connection)
            try:
                _attest_connection_role_posture(raw_cursor, urls.application_role)
                _set_worker_lease(raw_cursor, scope)
            finally:
                raw_cursor.close()
            task5_persist = _persist_task5_seed_plan
            if (
                requirement_seed is not None
                and requirement_seed.vector.get("accepted") is False
                and requirement_seed.vector.get("case_id") != "UNDECLARED_FDD_CALCULATION_ROOT"
            ):
                task5_persist = _persist_adversarial_requirement_task5_seed
            if task5_persist is _persist_adversarial_requirement_task5_seed:
                (
                    selection,
                    coverage,
                    coverage_roots,
                    db_projection_sha256,
                    coverage_helper,
                    task5_roundtrip_sha256,
                ) = task5_persist(
                    connection,
                    scope=scope,
                    seed=requirement_seed,
                    cdd_byte_count=cdd_byte_count,
                    workbook_byte_count=len(workbook_raw),
                    steps=steps,
                )
            else:
                (
                    selection,
                    coverage,
                    coverage_roots,
                    db_projection_sha256,
                    coverage_helper,
                    task5_roundtrip_sha256,
                ) = task5_persist(
                    connection,
                    artifact=artifact,
                    expected_application_role=urls.application_role,
                    expected_guard_owner_role=_role_from_url(urls.admin),
                    scope=scope,
                    dashboard_seed_plan=scope_plan,
                    commercial_registry=commercial,
                    financial_registry=financial,
                    cdd_byte_count=cdd_byte_count,
                    workbook_byte_count=len(workbook_raw),
                    steps=steps,
                )
            native_receipts.append(coverage_helper)
            ordered_coverage = sorted(coverage, key=lambda item: item.coverage_record_id)
            tasks: list[ExtractionTask] = []
            for coverage_record, source in zip(ordered_coverage, sources, strict=True):
                question = question_by_key[
                    (
                        coverage_record.methodology_version_id,
                        coverage_record.methodology_question_id,
                    )
                ]
                tasks.append(
                    ExtractionTask(
                        tenant_id=str(scope.tenant_id),
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        status=ExtractionTaskStatus.READY,
                        reason_codes=["SELECTED_APPLICABLE"],
                        methodology_id=question.methodology_id,
                        methodology_version_id=question.methodology_version_id,
                        methodology_question_id=question.methodology_question_id,
                        methodology_type=question.methodology_type,
                        methodology_section=question.section,
                        coverage_record_id=coverage_record.coverage_record_id,
                        document_id=source.document_id,
                        classification_id=(
                            "dc_" + _fixture_hash("classification", source.document_id)[:24]
                        ),
                        source_spans=[
                            SourceSpanReference(
                                document_id=source.document_id,
                                span_id=source.span_id,
                                locator=dict(source.locator),
                                span_type="PARAGRAPH",
                                content_hash=source.content_sha256,
                                text_excerpt=source.text,
                            )
                        ],
                        required_evidence=list(question.required_evidence),
                        expected_answer_schema=build_expected_answer_schema(question),
                        validation_requirements=list(question.validation_requirements),
                    )
                )
            repository = PostgresMethodologyExtractionRepository(
                connection,
                str(scope.tenant_id),
            )
            selected_id = str(selection.selection_id)
            if coverage_row_count == 100:
                selected_id, coverage_by_id = repository._load_exact_selected_coverage(
                    deal_id=str(scope.deal_id),
                    run_id=str(scope.run_id),
                    selection_id=selected_id,
                )
            else:
                partial_rows = connection.exec_driver_sql(
                    """
                    SELECT coverage_record_id,methodology_version_record_id,
                           methodology_version_id,methodology_question_record_id,
                           methodology_question_id,methodology_type,status
                      FROM public.methodology_coverage_records
                     WHERE tenant_id=%s AND deal_id=%s AND run_id=%s
                       AND selection_id=%s::uuid
                     ORDER BY methodology_question_record_id,coverage_record_id
                    """,
                    (
                        str(scope.tenant_id),
                        str(scope.deal_id),
                        str(scope.run_id),
                        selected_id,
                    ),
                ).fetchall()
                coverage_by_id = {str(row.coverage_record_id): row for row in partial_rows}
                if len(coverage_by_id) != 99:
                    _fail("TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID")
            task_specs = repository._build_planned_task_specs(
                deal_id=str(scope.deal_id),
                run_id=str(scope.run_id),
                selection_id=selected_id,
                tasks=tasks,
                coverage=coverage_by_id,
            )
            task_roots = tuple(
                _derive_seed_native_root(
                    scope=scope,
                    step=steps[5],
                    native_id=spec.record.task_record_id,
                    output_kind="METHODOLOGY_EXTRACTION_TASK",
                    output_schema_version="methodology-extraction-task-v2",
                    output_ordinal=ordinal,
                    native_key=[spec.record.task_record_id],
                    native_payload=_task_native_payload(spec),
                )
                for ordinal, spec in enumerate(task_specs)
            )
            raw_cursor = _instrumented_dbapi_cursor(connection)
            try:
                _set_worker_lease(raw_cursor, scope)
                for plan in task_roots:
                    _persist_seed_native_root(raw_cursor, scope=scope, plan=plan)
                task_root_map = {plan.native_id: plan.output_object_id for plan in task_roots}
                if coverage_row_count == 100:
                    repository.persist_planned_tasks(
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        selection_id=selected_id,
                        tasks=tasks,
                        lease_owner_id=str(scope.lease_owner_id),
                        lease_fencing_token=1,
                        run_output_object_ids=task_root_map,
                    )
                else:
                    repository._require_fence(
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        lease_owner_id=str(scope.lease_owner_id),
                        lease_fencing_token=1,
                    )
                    for spec in task_specs:
                        repository._insert_planned_task_spec(
                            deal_id=str(scope.deal_id),
                            run_id=str(scope.run_id),
                            selection_id=selected_id,
                            spec=spec,
                            run_output_object_id=task_root_map[spec.record.task_record_id],
                        )
                    repository._validate_exact_planned_graph(
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        selection_id=selected_id,
                        coverage=coverage_by_id,
                        expected=task_specs,
                        expected_root_bindings=task_root_map,
                    )
                native_receipts.append(
                    _verify_seed_native_roots(
                        raw_cursor,
                        scope=scope,
                        plans=task_roots,
                    )
                )
                raw_cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            finally:
                raw_cursor.close()

            source_by_document = {source.document_id: source for source in sources}
            plan_by_coverage = {
                str(row["coverage_record_id"]): row for row in scope_plan["row_tuple"]
            }
            for ordinal, spec in enumerate(task_specs):
                source = source_by_document[str(spec.record.task.document_id)]
                plan_row = plan_by_coverage[spec.record.coverage_record_id]
                required = bool(
                    plan_row["required_calculation_tuple"]
                    or plan_row.get("calculation_type_tuple", [])
                )
                candidate = MethodologyCandidateSpan(
                    document_id=source.document_id,
                    span_id=source.span_id,
                    ordinal=1,
                    content_sha256=source.content_sha256,
                    locator_sha256=source.locator_sha256,
                    text=source.text,
                )
                dispatched_sha256 = _fixture_hash(
                    "dispatched-call",
                    scope.run_id,
                    spec.record.task_record_id,
                )
                if required:
                    factual = MethodologyFactualOutput.model_validate(
                        {
                            "subject": "Fictional governed census subject",
                            "methodology_question_record_id": (
                                spec.record.methodology_question_record_id
                            ),
                            "predicate": "governed_census_value",
                            "claim_class": "FACT",
                            "value": {
                                "type": "text",
                                "value": f"Fictional governed value {ordinal + 1}",
                            },
                            "period": {"kind": "none"},
                            "unit": "text",
                            "claim_text": (f"Fictional governed claim for row {ordinal + 1}."),
                            "accepted_source_span_ids": [source.span_id],
                            "accepted_locator_sha256s": [source.locator_sha256],
                        }
                    )
                    outcome = "EVIDENCE_FOUND"
                    factual_outputs = (factual,)
                    gap_finding = None
                else:
                    outcome = "EVIDENCE_NOT_FOUND"
                    factual_outputs = ()
                    gap_finding = MethodologyGapFinding(
                        code="EVIDENCE_NOT_FOUND",
                        finding=(
                            "The admitted fictional source scope contained no "
                            "evidence responsive to this governed question."
                        ),
                    )
                result = ValidatedDispatchedMethodologyResult.from_terminal_payload(
                    dispatched_call_sha256=dispatched_sha256,
                    task_input_sha256=spec.record.task_input_sha256,
                    outcome=outcome,
                    searched_document_ids=(source.document_id,),
                    searched_span_ids=(source.span_id,),
                    searched_scope_sha256=compute_methodology_searched_scope_sha256((candidate,)),
                    factual_outputs=factual_outputs,
                    gap_finding=gap_finding,
                )
                prompt_hash = _fixture_hash(
                    "prompt",
                    spec.record.methodology_question_record_id,
                )
                billable_hash = _fixture_hash(
                    "billable-payload",
                    spec.record.task_record_id,
                )
                response_hash = _fixture_hash(
                    "provider-response",
                    spec.record.task_record_id,
                )
                reservation_id = _fixture_uuid(
                    "budget-reservation",
                    scope.run_id,
                    spec.record.task_record_id,
                )
                admitted = repository.admit_attempt(
                    deal_id=str(scope.deal_id),
                    run_id=str(scope.run_id),
                    run_step_id=str(steps[6]["step_id"]),
                    task_record_id=spec.record.task_record_id,
                    task_input_sha256=spec.record.task_input_sha256,
                    prepared_call_sha256=_fixture_hash(
                        "prepared-call",
                        spec.record.task_record_id,
                    ),
                    lease_owner_id=str(scope.lease_owner_id),
                    lease_fencing_token=1,
                    provider="anthropic",
                    configured_model="task8-v27-hermetic-model",
                    prompt_id="METHODOLOGY_QUESTION_EXTRACTION_V1",
                    prompt_content_sha256=prompt_hash,
                    budget_reservation_id=reservation_id,
                    backend="hermetic",
                    transport_kind="HERMETIC_TEST",
                    prompt_version="1.0.0",
                    prompt_input_schema_sha256=_fixture_hash(
                        "prompt-input-schema",
                        spec.record.methodology_question_record_id,
                    ),
                    prompt_output_schema_sha256=_fixture_hash(
                        "prompt-output-schema",
                        spec.record.methodology_question_record_id,
                    ),
                    request_payload_sha256=billable_hash,
                    billable_payload_sha256=billable_hash,
                    billable_payload_utf8_bytes=1,
                    configured_max_tokens=1,
                    configured_temperature="0",
                    configured_json_mode=True,
                    budget_policy_id="task8-v27-census-not-billable",
                    budget_component="anthropic",
                    price_catalog_version="task8-v27-hermetic-price-v1",
                    price_catalog_row_sha256=_fixture_hash(
                        "price-catalog",
                        spec.record.task_record_id,
                    ),
                    input_usd_per_million_tokens="0",
                    output_usd_per_million_tokens="0",
                    reserved_calls=0,
                    reserved_input_tokens=0,
                    reserved_output_tokens=0,
                    reserved_usd="0",
                )
                attempt_id = str(admitted["attempt_id"])
                result_id = str(
                    uuid.uuid5(
                        _METHODOLOGY_RESULT_NAMESPACE,
                        f"{attempt_id}:{result.validated_result_sha256}",
                    )
                )
                receipt_fields = {
                    "provider": "anthropic",
                    "backend": "hermetic",
                    "configured_model": "task8-v27-hermetic-model",
                    "observed_model": "task8-v27-hermetic-model",
                    "prompt_id": "METHODOLOGY_QUESTION_EXTRACTION_V1",
                    "prompt_version": "1.0.0",
                    "prompt_content_sha256": prompt_hash,
                    "prompt_input_schema_sha256": admitted["prompt_input_schema_sha256"],
                    "prompt_output_schema_sha256": admitted["prompt_output_schema_sha256"],
                    "request_payload_sha256": billable_hash,
                    "provider_request_id": ("task8-v27-hermetic-" + spec.record.task_record_id),
                    "provider_response_sha256": response_hash,
                    "real_call": False,
                    "configured_max_tokens": 1,
                    "configured_temperature": "0",
                    "configured_json_mode": True,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "stop_reason": "end_turn",
                    "budget_policy_id": "task8-v27-census-not-billable",
                    "budget_component": "anthropic",
                    "budget_reservation_id": reservation_id,
                    "billable_payload_sha256": billable_hash,
                    "billable_payload_utf8_bytes": 1,
                    "price_catalog_version": "task8-v27-hermetic-price-v1",
                    "price_catalog_row_sha256": admitted["price_catalog_row_sha256"],
                    "input_usd_per_million_tokens": "0.00000000",
                    "output_usd_per_million_tokens": "0.00000000",
                    "reserved_calls": 0,
                    "reserved_input_tokens": 0,
                    "reserved_output_tokens": 0,
                    "reserved_embedding_tokens": 0,
                    "reserved_usd": "0.00000000",
                    "budget_terminal_state": "NOT_BILLABLE",
                }
                provider_receipt_sha256 = compute_methodology_provider_receipt_sha256(
                    receipt_fields=receipt_fields,
                    validated_result_sha256=result.validated_result_sha256,
                )
                result_plan = _derive_seed_native_root(
                    scope=scope,
                    step=steps[6],
                    native_id=result_id,
                    output_kind="METHODOLOGY_EXTRACTION_RESULT",
                    output_schema_version="methodology-extraction-result-v2",
                    output_ordinal=ordinal,
                    native_key=[result_id],
                    native_payload=_result_native_payload(
                        task_record_id=spec.record.task_record_id,
                        attempt_id=attempt_id,
                        task_input_sha256=spec.record.task_input_sha256,
                        result=result,
                        provider_provenance_sha256=provider_receipt_sha256,
                    ),
                )
                result_plans = [result_plan]
                factual_record_id: str | None = None
                if factual_outputs:
                    factual_payload = factual_outputs[0].model_dump(mode="json")
                    factual_record_id = str(
                        uuid.uuid5(
                            _METHODOLOGY_FACTUAL_RECORD_NAMESPACE,
                            f"{result_id}:{factual_outputs[0].factual_output_id}:"
                            f"{_canonical_sha256(factual_payload)}",
                        )
                    )
                    result_plans.append(
                        _derive_seed_native_root(
                            scope=scope,
                            step=steps[6],
                            native_id=factual_record_id,
                            output_kind="METHODOLOGY_FACTUAL_OUTPUT",
                            output_schema_version="methodology-factual-output-v2",
                            output_ordinal=ordinal,
                            native_key=[factual_record_id],
                            native_payload=_factual_native_payload(
                                result_id=result_id,
                                task_record_id=spec.record.task_record_id,
                                factual_output_record_id=factual_record_id,
                                output=factual_outputs[0],
                            ),
                        )
                    )
                raw_cursor = _instrumented_dbapi_cursor(connection)
                try:
                    _set_worker_lease(raw_cursor, scope)
                    for root_plan in result_plans:
                        _persist_seed_native_root(
                            raw_cursor,
                            scope=scope,
                            plan=root_plan,
                        )
                finally:
                    raw_cursor.close()
                repository.finalize_attempt_success(
                    deal_id=str(scope.deal_id),
                    run_id=str(scope.run_id),
                    run_step_id=str(steps[6]["step_id"]),
                    attempt_id=attempt_id,
                    lease_owner_id=str(scope.lease_owner_id),
                    lease_fencing_token=1,
                    observed_model="task8-v27-hermetic-model",
                    provider_request_id=receipt_fields["provider_request_id"],
                    provider_response_sha256=response_hash,
                    dispatched_call_sha256=dispatched_sha256,
                    validated_result_sha256=result.validated_result_sha256,
                    prospective_receipt_sha256=provider_receipt_sha256,
                    budget_policy_id="task8-v27-census-not-billable",
                    latency_ms=0,
                    input_tokens=0,
                    output_tokens=0,
                    stop_reason="end_turn",
                    result=result,
                )
                connection.exec_driver_sql(
                    """
                    UPDATE public.methodology_extraction_results
                       SET run_output_object_id=%s
                     WHERE tenant_id=%s AND deal_id=%s AND run_id=%s
                       AND result_id=%s
                    """,
                    (
                        result_plan.output_object_id,
                        str(scope.tenant_id),
                        str(scope.deal_id),
                        str(scope.run_id),
                        result_id,
                    ),
                )
                if factual_record_id is not None:
                    connection.exec_driver_sql(
                        """
                        UPDATE public.methodology_result_factual_outputs
                           SET run_output_object_id=%s
                         WHERE tenant_id=%s AND deal_id=%s AND run_id=%s
                           AND factual_output_record_id=%s
                        """,
                        (
                            result_plans[1].output_object_id,
                            str(scope.tenant_id),
                            str(scope.deal_id),
                            str(scope.run_id),
                            factual_record_id,
                        ),
                    )
                raw_cursor = _instrumented_dbapi_cursor(connection)
                try:
                    _set_worker_lease(raw_cursor, scope)
                    native_receipts.append(
                        _verify_seed_native_roots(
                            raw_cursor,
                            scope=scope,
                            plans=tuple(result_plans),
                        )
                    )
                    raw_cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                finally:
                    raw_cursor.close()
                terminal_rows.append(
                    {
                        "coverage_record_id": spec.record.coverage_record_id,
                        "task_record_id": spec.record.task_record_id,
                        "result_id": result_id,
                        "outcome": outcome,
                        "result_sha256": result.validated_result_sha256,
                        "result_root_id": result_plan.output_object_id,
                        "question_record_id": spec.record.methodology_question_record_id,
                        "source": source,
                    }
                )

        boundary = PostgresCanonicalMethodologyMaterializationBoundary(
            connection_factory=engine.begin,
            tenant_id=str(scope.tenant_id),
            lease_owner_id=str(scope.lease_owner_id),
            lease_fencing_token=1,
            audit_sink=PostgresAuditSink(),
        )
        boundary_scope = {
            "tenant_id": str(scope.tenant_id),
            "deal_id": str(scope.deal_id),
            "run_id": str(scope.run_id),
        }
        _claim_result, _claims, staged_claims = boundary.prepare_claim_materialization(
            **boundary_scope,
            run_step_id=str(steps[7]["step_id"]),
        )
        with engine.begin() as connection:
            set_tenant_local(connection, str(scope.tenant_id))
            repository = PostgresMethodologyExtractionRepository(
                connection,
                str(scope.tenant_id),
            )
            raw_cursor = _instrumented_dbapi_cursor(connection)
            try:
                _set_worker_lease(raw_cursor, scope)
                claim_records: list[MethodologyResultClaimLink] = []
                claim_plans: list[_NativeRootPlan] = []
                for ordinal, spec in enumerate(staged_claims.specs):
                    winner, reused = repository.upsert_canonical_claim(
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        lease_owner_id=str(scope.lease_owner_id),
                        lease_fencing_token=1,
                        claim=_canonical_claim_payload(spec),
                    )
                    if str(winner["claim_id"]) != spec.identity.claim_id:
                        _fail("TASK8_V27_CENSUS_RUNTIME_GRAPH_INVALID")
                    logical = {
                        "tenant_id": str(scope.tenant_id),
                        "deal_id": str(scope.deal_id),
                        "run_id": str(scope.run_id),
                        "result_id": str(spec.row["result_id"]),
                        "factual_output_record_id": str(spec.row["factual_output_record_id"]),
                        "coverage_record_id": str(spec.row["coverage_record_id"]),
                        "factual_output_id": str(spec.output.factual_output_id),
                        "claim_id": spec.identity.claim_id,
                        "canonical_key_sha256": spec.identity.canonical_key_sha256,
                        "reused_existing_claim": bool(reused),
                    }
                    derived = {
                        "selection_id": str(spec.row["selection_id"]),
                        "methodology_version_record_id": str(
                            spec.row["methodology_version_record_id"]
                        ),
                        "methodology_question_record_id": str(
                            spec.row["methodology_question_record_id"]
                        ),
                    }
                    record = MethodologyResultClaimLink.model_validate(
                        {
                            "claim_link_id": generate_methodology_result_link_id(
                                table="methodology_result_claim_links",
                                logical_record=logical,
                                derived_fields=derived,
                            ),
                            **logical,
                        }
                    )
                    claim_records.append(record)
                    claim_plans.append(
                        _derive_seed_native_root(
                            scope=scope,
                            step=steps[7],
                            native_id=record.claim_link_id,
                            output_kind="METHODOLOGY_CLAIM_LINK",
                            output_schema_version="methodology-claim-link-v2",
                            output_ordinal=ordinal,
                            native_key=[record.claim_link_id],
                            native_payload=_link_native_payload(
                                "METHODOLOGY_CLAIM_LINK",
                                record,
                                **derived,
                            ),
                        )
                    )
                for plan in claim_plans:
                    _persist_seed_native_root(raw_cursor, scope=scope, plan=plan)
                repository.persist_claim_links(
                    deal_id=str(scope.deal_id),
                    run_id=str(scope.run_id),
                    lease_owner_id=str(scope.lease_owner_id),
                    lease_fencing_token=1,
                    records=claim_records,
                    run_output_object_ids={
                        plan.native_id: plan.output_object_id for plan in claim_plans
                    },
                )
                native_receipts.append(
                    _verify_seed_native_roots(
                        raw_cursor,
                        scope=scope,
                        plans=tuple(claim_plans),
                    )
                )
                raw_cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            finally:
                raw_cursor.close()

        _evidence_result, _evidence, staged_evidence = boundary.prepare_evidence_materialization(
            **boundary_scope,
            run_step_id=str(steps[8]["step_id"]),
        )
        with engine.begin() as connection:
            set_tenant_local(connection, str(scope.tenant_id))
            repository = PostgresMethodologyExtractionRepository(
                connection,
                str(scope.tenant_id),
            )
            raw_cursor = _instrumented_dbapi_cursor(connection)
            try:
                _set_worker_lease(raw_cursor, scope)
                evidence_records: list[MethodologyResultEvidenceLink] = []
                evidence_plans: list[_NativeRootPlan] = []
                task_by_link: dict[str, str] = {}
                for ordinal, spec in enumerate(staged_evidence.specs):
                    item = spec.record.evidence_item
                    inserted = repository.insert_canonical_evidence_item(
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        task_record_id=str(spec.factual_row["task_record_id"]),
                        lease_owner_id=str(scope.lease_owner_id),
                        lease_fencing_token=1,
                        evidence_item=item,
                        claim_id=spec.record.claim_id,
                    )
                    if str(inserted["evidence_id"]) != item.evidence_id:
                        _fail("TASK8_V27_CENSUS_RUNTIME_GRAPH_INVALID")
                    logical = {
                        "tenant_id": str(scope.tenant_id),
                        "deal_id": str(scope.deal_id),
                        "run_id": str(scope.run_id),
                        "result_id": str(spec.factual_row["result_id"]),
                        "factual_output_record_id": str(
                            spec.factual_row["factual_output_record_id"]
                        ),
                        "claim_link_id": str(spec.claim_row["claim_link_id"]),
                        "factual_output_id": spec.record.extraction_output_id,
                        "evidence_id": item.evidence_id,
                        "document_id": spec.record.source_ref.document_id,
                        "source_span_id": spec.record.source_ref.source_span_id,
                    }
                    record = MethodologyResultEvidenceLink.model_validate(
                        {
                            "evidence_link_id": generate_methodology_result_link_id(
                                table="methodology_result_evidence_links",
                                logical_record=logical,
                                derived_fields={"locator_sha256": spec.locator_sha256},
                            ),
                            **logical,
                        }
                    )
                    evidence_records.append(record)
                    task_by_link[record.evidence_link_id] = str(spec.factual_row["task_record_id"])
                    evidence_plans.append(
                        _derive_seed_native_root(
                            scope=scope,
                            step=steps[8],
                            native_id=record.evidence_link_id,
                            output_kind="METHODOLOGY_EVIDENCE_LINK",
                            output_schema_version="methodology-evidence-link-v2",
                            output_ordinal=ordinal,
                            native_key=[record.evidence_link_id],
                            native_payload=_link_native_payload(
                                "METHODOLOGY_EVIDENCE_LINK",
                                record,
                                locator_sha256=spec.locator_sha256,
                            ),
                        )
                    )
                for plan in evidence_plans:
                    _persist_seed_native_root(raw_cursor, scope=scope, plan=plan)
                by_task: dict[str, list[MethodologyResultEvidenceLink]] = {}
                for record in evidence_records:
                    by_task.setdefault(task_by_link[record.evidence_link_id], []).append(record)
                for task_record_id, records in sorted(by_task.items()):
                    repository.persist_evidence_links(
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        task_record_id=task_record_id,
                        lease_owner_id=str(scope.lease_owner_id),
                        lease_fencing_token=1,
                        records=records,
                        run_output_object_ids={
                            record.evidence_link_id: next(
                                plan.output_object_id
                                for plan in evidence_plans
                                if plan.native_id == record.evidence_link_id
                            )
                            for record in records
                        },
                    )
                native_receipts.append(
                    _verify_seed_native_roots(
                        raw_cursor,
                        scope=scope,
                        plans=tuple(evidence_plans),
                    )
                )
                raw_cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            finally:
                raw_cursor.close()

        *_sanad_outputs, staged_sanads = boundary.prepare_sanad_materialization(
            **boundary_scope,
            run_step_id=str(steps[9]["step_id"]),
        )
        with engine.begin() as connection:
            set_tenant_local(connection, str(scope.tenant_id))
            repository = PostgresMethodologyExtractionRepository(
                connection,
                str(scope.tenant_id),
            )
            raw_cursor = _instrumented_dbapi_cursor(connection)
            try:
                _set_worker_lease(raw_cursor, scope)
                snapshot = repository.load_canonical_materialization_snapshot(
                    deal_id=str(scope.deal_id),
                    run_id=str(scope.run_id),
                )
                claim_by_output = {str(row["factual_output_id"]): row for row in snapshot.claims}
                grade_by_sanad = {grade.sanad_id: grade for grade in staged_sanads.grades}
                sanad_records: list[MethodologyResultSanadLink] = []
                sanad_plans: list[_NativeRootPlan] = []
                source_record_by_sanad: dict[str, Any] = {}
                for ordinal, record_source in enumerate(staged_sanads.records):
                    claim_row = claim_by_output[record_source.extraction_output_id]
                    grade = grade_by_sanad[record_source.sanad.sanad_id]
                    repository.insert_canonical_sanad(
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        lease_owner_id=str(scope.lease_owner_id),
                        lease_fencing_token=1,
                        sanad=_canonical_sanad_payload(record_source, grade),
                    )
                    source_record_by_sanad[record_source.sanad.sanad_id] = record_source
                    logical = {
                        "tenant_id": str(scope.tenant_id),
                        "deal_id": str(scope.deal_id),
                        "run_id": str(scope.run_id),
                        "result_id": str(claim_row["result_id"]),
                        "factual_output_record_id": str(claim_row["factual_output_record_id"]),
                        "claim_link_id": str(claim_row["claim_link_id"]),
                        "factual_output_id": record_source.extraction_output_id,
                        "sanad_id": record_source.sanad.sanad_id,
                        "primary_evidence_id": record_source.sanad.primary_evidence_id,
                        "grade": grade.sanad_grade.value,
                        "defect_ids": tuple(sorted(grade.defect_ids)),
                    }
                    record = MethodologyResultSanadLink.model_validate(
                        {
                            "sanad_link_id": generate_methodology_result_link_id(
                                table="methodology_result_sanad_links",
                                logical_record=logical,
                            ),
                            **logical,
                        }
                    )
                    sanad_records.append(record)
                    sanad_plans.append(
                        _derive_seed_native_root(
                            scope=scope,
                            step=steps[9],
                            native_id=record.sanad_link_id,
                            output_kind="METHODOLOGY_SANAD_LINK",
                            output_schema_version="methodology-sanad-link-v2",
                            output_ordinal=ordinal,
                            native_key=[record.sanad_link_id],
                            native_payload=_link_native_payload(
                                "METHODOLOGY_SANAD_LINK",
                                record,
                            ),
                        )
                    )
                for plan in sanad_plans:
                    _persist_seed_native_root(raw_cursor, scope=scope, plan=plan)
                repository.persist_sanad_links(
                    deal_id=str(scope.deal_id),
                    run_id=str(scope.run_id),
                    lease_owner_id=str(scope.lease_owner_id),
                    lease_fencing_token=1,
                    records=sanad_records,
                    run_output_object_ids={
                        plan.native_id: plan.output_object_id for plan in sanad_plans
                    },
                )
                after_sanads = repository.load_canonical_materialization_snapshot(
                    deal_id=str(scope.deal_id),
                    run_id=str(scope.run_id),
                )
                sanad_link_by_sanad = {str(row["sanad_id"]): row for row in after_sanads.sanads}
                defect_records: list[MethodologyResultDefectLink] = []
                defect_plans: list[_NativeRootPlan] = []
                for ordinal, defect_source in enumerate(staged_sanads.defects):
                    defect = defect_source.defect
                    sanad_row = sanad_link_by_sanad[str(defect_source.sanad_id)]
                    source_record = source_record_by_sanad[str(defect_source.sanad_id)]
                    repository.insert_canonical_defect(
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        lease_owner_id=str(scope.lease_owner_id),
                        lease_fencing_token=1,
                        defect={
                            **defect.model_dump(mode="json"),
                            "claim_id": source_record.claim_id,
                        },
                    )
                    logical = {
                        "tenant_id": str(scope.tenant_id),
                        "deal_id": str(scope.deal_id),
                        "run_id": str(scope.run_id),
                        "result_id": str(sanad_row["result_id"]),
                        "factual_output_record_id": str(sanad_row["factual_output_record_id"]),
                        "sanad_link_id": str(sanad_row["sanad_link_id"]),
                        "defect_id": defect.defect_id,
                    }
                    record = MethodologyResultDefectLink.model_validate(
                        {
                            "defect_link_id": generate_methodology_result_link_id(
                                table="methodology_result_defect_links",
                                logical_record=logical,
                            ),
                            **logical,
                        }
                    )
                    defect_records.append(record)
                    defect_plans.append(
                        _derive_seed_native_root(
                            scope=scope,
                            step=steps[9],
                            native_id=record.defect_link_id,
                            output_kind="METHODOLOGY_DEFECT_LINK",
                            output_schema_version="methodology-defect-link-v2",
                            output_ordinal=ordinal,
                            native_key=[record.defect_link_id],
                            native_payload=_link_native_payload(
                                "METHODOLOGY_DEFECT_LINK",
                                record,
                            ),
                        )
                    )
                for plan in defect_plans:
                    _persist_seed_native_root(raw_cursor, scope=scope, plan=plan)
                if defect_records:
                    repository.persist_defect_links(
                        deal_id=str(scope.deal_id),
                        run_id=str(scope.run_id),
                        lease_owner_id=str(scope.lease_owner_id),
                        lease_fencing_token=1,
                        records=defect_records,
                        run_output_object_ids={
                            plan.native_id: plan.output_object_id for plan in defect_plans
                        },
                    )
                native_receipts.append(
                    _verify_seed_native_roots(
                        raw_cursor,
                        scope=scope,
                        plans=(*sanad_plans, *defect_plans),
                    )
                )
                raw_cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            finally:
                raw_cursor.close()

        with engine.begin() as connection:
            set_tenant_local(connection, str(scope.tenant_id))
            raw_cursor = _instrumented_dbapi_cursor(connection)
            try:
                _attest_connection_role_posture(raw_cursor, urls.application_role)
                _set_worker_lease(raw_cursor, scope)
                calculation_plans: list[_NativeRootPlan] = []
                calculation_specs: list[tuple[Mapping[str, Any], dict[str, object]]] = []
                for row in scope_plan["row_tuple"]:
                    requirements = list(row["required_calculation_tuple"])
                    requested_types = list(row.get("calculation_type_tuple", []))
                    if not requested_types:
                        requested_types = [
                            str(requirement.get("formula_type") or requirement.get("calc_type"))
                            for requirement in requirements
                            if requirement.get("required") is True
                        ]
                    for type_ordinal, calc_type in enumerate(requested_types, start=1):
                        matching = next(
                            (
                                requirement
                                for requirement in requirements
                                if str(
                                    requirement.get("formula_type") or requirement.get("calc_type")
                                )
                                == str(calc_type)
                            ),
                            None,
                        )
                        formula_ordinal = (
                            matching.get("formula_ordinal")
                            if isinstance(matching, Mapping)
                            else type_ordinal
                        )
                        if type(formula_ordinal) is not int or int(formula_ordinal) <= 0:
                            _fail("TASK8_V27_CENSUS_RUNTIME_GRAPH_INVALID")
                        calculation_specs.append(
                            (
                                row,
                                {
                                    "formula_ordinal": int(formula_ordinal),
                                    "formula_type": str(calc_type),
                                    "declared_requirement": matching,
                                },
                            )
                        )
                for ordinal, (row, requirement) in enumerate(calculation_specs):
                    calc_id = _fixture_uuid(
                        "calculation",
                        scope.run_id,
                        row["coverage_record_id"],
                        requirement["formula_type"],
                        requirement["formula_ordinal"],
                    )
                    calc_sanad_id = _fixture_uuid("calculation-sanad", calc_id)
                    raw_cursor.execute(
                        """
                        SELECT l.claim_id::text
                          FROM public.methodology_result_claim_links l
                         WHERE l.tenant_id=%s AND l.deal_id=%s AND l.run_id=%s
                           AND l.coverage_record_id=%s
                        """,
                        (
                            scope.tenant_id,
                            scope.deal_id,
                            scope.run_id,
                            row["coverage_record_id"],
                        ),
                    )
                    claim_row = raw_cursor.fetchone()
                    if claim_row is None:
                        _fail("TASK8_V27_CENSUS_RUNTIME_GRAPH_INVALID")
                    claim_id = str(claim_row[0])
                    inputs = {
                        "schema_version": "task8-v27-calculation-input-v1",
                        "claim_ids": [claim_id],
                        "formula_ordinal": requirement["formula_ordinal"],
                    }
                    output = {
                        "schema_version": "task8-v27-calculation-output-v1",
                        "value": str(requirement["formula_ordinal"]),
                    }
                    formula_hash = _canonical_sha256(["task8-v27-formula-v1", requirement])
                    reproducibility_hash = _canonical_sha256(
                        [
                            "task8-v27-calculation-reproducibility-v1",
                            calc_id,
                            inputs,
                            output,
                            formula_hash,
                        ]
                    )
                    explanation = [
                        {
                            "kind": "hermetic_census",
                            "formula_ordinal": requirement["formula_ordinal"],
                        }
                    ]
                    native_payload = [
                        requirement["formula_type"],
                        inputs,
                        output,
                        formula_hash,
                        "task8-v27-census-calculator-v1",
                        reproducibility_hash,
                        [
                            calc_sanad_id,
                            [claim_id],
                            "A",
                            "A",
                            explanation,
                        ],
                    ]
                    plan = _derive_seed_native_root(
                        scope=scope,
                        step=steps[10],
                        native_id=calc_id,
                        output_kind="METHODOLOGY_CALCULATION",
                        output_schema_version="task8-fdd-deterministic-calculation-v1",
                        output_ordinal=ordinal,
                        native_key=[calc_id],
                        native_payload=native_payload,
                    )
                    _persist_seed_native_root(raw_cursor, scope=scope, plan=plan)
                    raw_cursor.execute(
                        """
                        INSERT INTO public.deterministic_calculations(
                            calc_id,tenant_id,deal_id,run_id,producer_step_name,
                            run_output_object_id,run_output_object_kind,
                            coverage_record_id,methodology_question_record_id,
                            calc_type,inputs,formula_hash,code_version,output,
                            reproducibility_hash,created_at,updated_at
                        ) VALUES (
                            %s,%s,%s,%s,'METHODOLOGY_DETERMINISTIC_CALCULATION',
                            %s,'METHODOLOGY_CALCULATION',%s,%s,%s,%s::jsonb,%s,%s,
                            %s::jsonb,%s,%s,%s
                        );
                        INSERT INTO public.calc_sanads(
                            calc_sanad_id,tenant_id,deal_id,run_id,calc_id,
                            input_claim_ids,input_min_sanad_grade,calc_grade,
                            explanation,created_at,updated_at
                        ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,'A','A',%s::jsonb,%s,%s)
                        """,
                        (
                            uuid.UUID(calc_id),
                            scope.tenant_id,
                            scope.deal_id,
                            scope.run_id,
                            uuid.UUID(plan.output_object_id),
                            row["coverage_record_id"],
                            uuid.UUID(str(row["methodology_question_record_id"])),
                            requirement["formula_type"],
                            json.dumps(inputs, separators=(",", ":"), sort_keys=True),
                            formula_hash,
                            "task8-v27-census-calculator-v1",
                            json.dumps(output, separators=(",", ":"), sort_keys=True),
                            reproducibility_hash,
                            _FIXTURE_OCCURRED_AT,
                            _FIXTURE_OCCURRED_AT,
                            uuid.UUID(calc_sanad_id),
                            scope.tenant_id,
                            scope.deal_id,
                            scope.run_id,
                            uuid.UUID(calc_id),
                            json.dumps([claim_id], separators=(",", ":")),
                            json.dumps(explanation, separators=(",", ":"), sort_keys=True),
                            _FIXTURE_OCCURRED_AT,
                            _FIXTURE_OCCURRED_AT,
                        ),
                    )
                    calculation_plans.append(plan)
                native_receipts.append(
                    _verify_seed_native_roots(
                        raw_cursor,
                        scope=scope,
                        plans=tuple(calculation_plans),
                    )
                )
                raw_cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            finally:
                raw_cursor.close()

        graphs = _load_bound_dashboard_graphs(
            application_url=urls.application,
            expected_application_role=urls.application_role,
            scope=scope,
            seed_plan=scope_plan,
            focus_semantic_case=focus_semantic_case,
            expected_coverage_row_count=coverage_row_count,
        )
        closure = _observe_seed_fk_closure(
            urls.application,
            expected_application_role=urls.application_role,
            tenant_id=scope.tenant_id,
            deal_id=scope.deal_id,
            closure_manifest=build_seed_fk_closure_manifest(artifact),
        )
    finally:
        engine.dispose()
    return _BoundRuntimeGraph(
        graphs=graphs,
        dashboard_seed_plan_sha256=str(scope_plan["expected_sha256"]),
        dashboard_seed_db_projection_sha256=db_projection_sha256,
        seed_fk_closure_sha256=str(closure["expected_sha256"]),
        seed_native_helper_equality_sha256=_canonical_sha256(
            ["task8-v27-seed-native-helper-equality-set-v1", native_receipts]
        ),
        source_freeze_sha256=source_freeze_sha256,
        task5_roundtrip_sha256=task5_roundtrip_sha256,
    )


def _persist_bound_runtime_graph(
    *,
    urls: _DisposableUrls,
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    scope: _CaseScope,
    focus_semantic_case: str,
    coverage_row_count: int = 100,
    financial_coverage_row_count: int = 79,
) -> _BoundRuntimeGraph:
    """Persist only the normative exact 21+79 production runtime graph."""

    if coverage_row_count != 100 or financial_coverage_row_count != 79:
        _fail("TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    return _persist_runtime_graph_core(
        urls=urls,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
        scope=scope,
        focus_semantic_case=focus_semantic_case,
        coverage_row_count=coverage_row_count,
        financial_coverage_row_count=financial_coverage_row_count,
    )


def _load_bound_dashboard_graphs(
    *,
    application_url: str,
    expected_application_role: str,
    scope: _CaseScope,
    seed_plan: Mapping[str, Any],
    focus_semantic_case: str,
    expected_coverage_row_count: int,
) -> tuple[_DashboardGraph, ...]:
    """Project the committed production graph into exact dashboard identities."""

    connection = _connect(application_url)
    try:
        with connection.cursor() as cursor:
            _attest_connection_role_posture(cursor, expected_application_role)
            _set_tenant(cursor, scope.tenant_id)
            cursor.execute(
                """
                SELECT c.coverage_record_id,c.methodology_version_record_id::text,
                       c.methodology_version_id,c.methodology_question_record_id::text,
                       c.methodology_question_id,q.question_payload_hash,
                       r.result_id::text,r.result_sha256,r.outcome,
                       r.run_output_object_id::text,
                       COALESCE(array_agg(DISTINCT e.run_output_object_id::text)
                           FILTER (WHERE e.run_output_object_id IS NOT NULL),ARRAY[]::text[]),
                       COALESCE(array_agg(DISTINCT s.run_output_object_id::text)
                           FILTER (WHERE s.run_output_object_id IS NOT NULL),ARRAY[]::text[]),
                       COALESCE(array_agg(DISTINCT d.run_output_object_id::text)
                           FILTER (WHERE d.run_output_object_id IS NOT NULL),ARRAY[]::text[]),
                       COALESCE(array_agg(DISTINCT calc.run_output_object_id::text)
                           FILTER (WHERE calc.run_output_object_id IS NOT NULL),ARRAY[]::text[])
                  FROM public.methodology_coverage_records c
                  JOIN public.methodology_questions q
                    ON q.tenant_id=c.tenant_id
                   AND q.methodology_version_record_id=c.methodology_version_record_id
                   AND q.methodology_question_record_id=c.methodology_question_record_id
                  JOIN public.methodology_extraction_tasks t
                    ON t.tenant_id=c.tenant_id AND t.deal_id=c.deal_id
                   AND t.run_id=c.run_id AND t.coverage_record_id=c.coverage_record_id
                  JOIN public.methodology_extraction_results r
                    ON r.tenant_id=t.tenant_id AND r.deal_id=t.deal_id
                   AND r.run_id=t.run_id AND r.task_record_id=t.task_record_id
                  LEFT JOIN public.methodology_result_factual_outputs f
                    ON f.tenant_id=r.tenant_id AND f.deal_id=r.deal_id
                   AND f.run_id=r.run_id AND f.result_id=r.result_id
                  LEFT JOIN public.methodology_result_evidence_links e
                    ON e.tenant_id=f.tenant_id AND e.deal_id=f.deal_id
                   AND e.run_id=f.run_id AND e.result_id=f.result_id
                   AND e.factual_output_record_id=f.factual_output_record_id
                  LEFT JOIN public.methodology_result_sanad_links s
                    ON s.tenant_id=f.tenant_id AND s.deal_id=f.deal_id
                   AND s.run_id=f.run_id AND s.result_id=f.result_id
                   AND s.factual_output_record_id=f.factual_output_record_id
                  LEFT JOIN public.methodology_result_defect_links d
                    ON d.tenant_id=f.tenant_id AND d.deal_id=f.deal_id
                   AND d.run_id=f.run_id AND d.result_id=f.result_id
                   AND d.factual_output_record_id=f.factual_output_record_id
                  LEFT JOIN public.deterministic_calculations calc
                    ON calc.tenant_id=c.tenant_id AND calc.deal_id=c.deal_id
                   AND calc.run_id=c.run_id
                   AND calc.coverage_record_id=c.coverage_record_id
                   AND calc.methodology_question_record_id=
                       c.methodology_question_record_id
                 WHERE c.tenant_id=%s AND c.deal_id=%s AND c.run_id=%s
                 GROUP BY c.coverage_record_id,c.methodology_version_record_id,
                          c.methodology_version_id,c.methodology_question_record_id,
                          c.methodology_question_id,q.question_payload_hash,
                          r.result_id,r.result_sha256,r.outcome,r.run_output_object_id
                 ORDER BY c.coverage_record_id
                """,
                (scope.tenant_id, scope.deal_id, scope.run_id),
            )
            rows = cursor.fetchall()
        connection.rollback()
    finally:
        connection.close()
    if len(rows) != expected_coverage_row_count:
        _fail("TASK8_V27_CENSUS_RUNTIME_GRAPH_INVALID")
    plan_by_coverage = {str(row["coverage_record_id"]): row for row in seed_plan["row_tuple"]}
    graphs: list[_DashboardGraph] = []
    for ordinal, row in enumerate(rows):
        (
            coverage_id,
            version_record_id,
            methodology_version_id,
            question_record_id,
            methodology_question_id,
            question_payload_hash,
            result_id,
            result_sha256,
            outcome,
            result_root,
            evidence_roots,
            sanad_roots,
            defect_roots,
            calculation_roots,
        ) = row
        plan_row = plan_by_coverage[str(coverage_id)]
        disposition = (
            "GAP"
            if str(outcome) == "EVIDENCE_NOT_FOUND"
            else ("RED_FLAG" if defect_roots else "ANSWERED")
        )
        if ordinal == 0 and focus_semantic_case != "PRODUCTION_POLICY_V2_BASELINE":
            requested, _outcomes, _required, _adverse = _dashboard_case_shape(focus_semantic_case)
            disposition = requested
        raw_roots = [
            ("RESULT", str(result_root), "METHODOLOGY_EXTRACTION_RESULT"),
            *[("EVIDENCE", str(value), "METHODOLOGY_EVIDENCE_LINK") for value in evidence_roots],
            *[("SANAD", str(value), "METHODOLOGY_SANAD_LINK") for value in sanad_roots],
            *[
                ("ADVERSE_CONTRADICTION", str(value), "METHODOLOGY_DEFECT_LINK")
                for value in defect_roots
            ],
            *[
                ("CALCULATION", str(value), "METHODOLOGY_CALCULATION")
                for value in calculation_roots
            ],
        ]
        root_coordinates: list[dict[str, object]] = []
        for kind in sorted({value[0] for value in raw_roots}):
            matches = sorted(
                (value for value in raw_roots if value[0] == kind),
                key=lambda value: (value[1], value[2]),
            )
            for root_ordinal, (_kind, output_id, output_kind) in enumerate(matches):
                root_coordinates.append(
                    {
                        "root_kind": kind,
                        "root_ordinal": root_ordinal,
                        "root_output_object_id": output_id,
                        "root_output_object_kind": output_kind,
                    }
                )
        identity = derive_v27_dashboard_identity(
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            run_id=str(scope.run_id),
            pipeline_contract_version=CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
            pipeline_contract_hash=CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
            coverage_record_id=str(coverage_id),
            methodology_version_record_id=str(version_record_id),
            methodology_question_record_id=str(question_record_id),
            question_payload_hash=str(question_payload_hash),
            row_disposition=disposition,
            disposition_reason_code=("EVIDENCE_NOT_FOUND" if disposition == "GAP" else None),
            row_trace_prefix=[
                int(plan_row["coverage_ordinal"]),
                1 if plan_row["methodology_type"] == "commercial_dd" else 2,
                "Task8 governed census",
                int(plan_row["question_ordinal"]),
                f"Task8 governed census!{int(plan_row['question_ordinal'])}",
                str(version_record_id),
                str(methodology_version_id),
                str(question_record_id),
                str(methodology_question_id),
                str(question_payload_hash),
            ],
            result_records=[[str(result_id), str(result_sha256)]],
            answer_values=[],
            task_gaps=(
                [["EVIDENCE_NOT_FOUND", str(result_sha256)]] if disposition == "GAP" else []
            ),
            calculations=[["CALCULATION", str(value)] for value in calculation_roots],
            roots=root_coordinates,
        )
        graphs.append(
            _DashboardGraph(
                scope=scope,
                coverage_record_id=str(coverage_id),
                dashboard_row_id=uuid.UUID(str(identity["dashboard_row_id"])),
                dashboard_output_object_id=uuid.UUID(str(identity["output_object_id"])),
                root_links=tuple(
                    (
                        str(kind),
                        int(root_ordinal),
                        uuid.UUID(str(output_id)),
                        str(output_kind),
                        uuid.UUID(str(link_id)),
                    )
                    for kind, root_ordinal, output_id, output_kind, link_id in identity[
                        "root_link_tuple"
                    ]
                ),
                root_projection_sha256=str(identity["root_projection_sha256"]),
                row_payload_sha256=str(identity["row_payload_sha256"]),
                native_identity_sha256=str(identity["native_identity_sha256"]),
                native_output_sha256=str(identity["native_output_sha256"]),
                row_disposition=disposition,
                dashboard_output_ordinal=ordinal,
            )
        )
    return tuple(graphs)


def _seed_dashboard_batch(
    urls: _DisposableUrls,
    *,
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    scope: _CaseScope,
    focus_semantic_case: str,
    coverage_row_count: int,
    financial_coverage_row_count: int,
) -> _BoundRuntimeGraph:
    if (coverage_row_count, financial_coverage_row_count) == (99, 78):
        return _persist_partial_dashboard_runtime_graph(
            urls=urls,
            artifact=artifact,
            policy_artifact_raw=policy_artifact_raw,
            workbook_raw=workbook_raw,
            scope=scope,
            focus_semantic_case=focus_semantic_case,
        )
    return _persist_bound_runtime_graph(
        urls=urls,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
        scope=scope,
        focus_semantic_case=focus_semantic_case,
        coverage_row_count=coverage_row_count,
        financial_coverage_row_count=financial_coverage_row_count,
    )


def _persist_partial_dashboard_runtime_graph(
    *,
    urls: _DisposableUrls,
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    scope: _CaseScope,
    focus_semantic_case: str,
) -> _BoundRuntimeGraph:
    """Build the exact FK-valid 99/78 graph used by the global-count RED."""

    return _persist_runtime_graph_core(
        urls=urls,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
        scope=scope,
        focus_semantic_case=focus_semantic_case,
        coverage_row_count=99,
        financial_coverage_row_count=78,
        allow_partial=True,
    )


def _insert_dashboard_output_and_row(
    cursor: Any,
    graph: _DashboardGraph,
    *,
    include_row: bool = True,
    corrupt_native_hashes: bool = False,
) -> None:
    scope = graph.scope
    row_payload_sha256 = graph.row_payload_sha256
    dashboard_step = next(
        row
        for row in build_current_run_step_plan(
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            run_id=str(scope.run_id),
        )
        if row["step_order"] == 11
    )
    native_payload = [
        graph.coverage_record_id,
        graph.root_projection_sha256,
        graph.row_disposition,
        graph.row_payload_sha256,
    ]
    root_plan = _derive_seed_native_root(
        scope=scope,
        step=dashboard_step,
        native_id=str(graph.dashboard_row_id),
        output_kind="METHODOLOGY_TRUTH_DASHBOARD_ROW",
        output_schema_version="task8-methodology-truth-dashboard-row-v1",
        output_ordinal=graph.dashboard_output_ordinal,
        native_key=[str(graph.dashboard_row_id)],
        native_payload=native_payload,
    )
    if (
        root_plan.output_object_id != str(graph.dashboard_output_object_id)
        or root_plan.native_identity_sha256 != graph.native_identity_sha256
        or root_plan.native_output_sha256 != graph.native_output_sha256
    ):
        _fail("TASK8_V27_CENSUS_DASHBOARD_IDENTITY_INVALID")
    if corrupt_native_hashes:
        root_plan = replace(
            root_plan,
            native_output_sha256=_fixture_hash(
                "illegal-corrupt-dashboard-native-output",
                graph.dashboard_row_id,
            ),
        )
    _persist_seed_native_root(cursor, scope=scope, plan=root_plan)
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
        _fail("TASK8_V27_CENSUS_DASHBOARD_BATCH_INVALID")
    for ordinal, graph in enumerate(graphs):
        if omit_focus_output and ordinal == 0:
            continue
        _insert_dashboard_output_and_row(
            cursor,
            graph,
            include_row=include_focus_row or ordinal != 0,
            corrupt_native_hashes=corrupt_focus_native_hashes and ordinal == 0,
        )


def _run_main_dashboard_census(
    urls: _DisposableUrls,
    *,
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
) -> tuple[_CaseScope, _BoundRuntimeGraph, dict[str, object]]:
    """Persist and app-observe the exact production-backed 100-row dashboard."""

    scope = _seed_scope(
        urls.application,
        urls.application_role,
        "MAIN",
    )
    bound = _seed_dashboard_batch(
        urls,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
        scope=scope,
        focus_semantic_case="PRODUCTION_POLICY_V2_BASELINE",
        coverage_row_count=100,
        financial_coverage_row_count=79,
    )
    connection = _connect_behavior_dml(
        urls.application,
        expected_role=urls.application_role,
        tenant_id=scope.tenant_id,
        case_coordinate=scope.case_coordinate,
        operation_prefix="MAIN_DASHBOARD",
        dml_classification=_APPLICATION_BEHAVIOR_DML,
    )
    helper_rows: list[list[str]] = []
    try:
        with connection.cursor() as cursor:
            observer_posture = _attest_connection_role_posture(
                cursor,
                urls.application_role,
            )
            observer_role = _posture_role(observer_posture)
            _set_worker_lease(cursor, scope)
            _insert_dashboard_batch(cursor, bound.graphs)
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                """
                SELECT
                  (SELECT count(*) FROM public.methodology_coverage_records c
                    WHERE c.tenant_id=%s AND c.deal_id=%s AND c.run_id=%s),
                  (SELECT count(*) FROM public.methodology_truth_dashboard_rows d
                    WHERE d.tenant_id=%s AND d.deal_id=%s AND d.run_id=%s),
                  (SELECT count(*) FROM public.methodology_truth_dashboard_root_links l
                    WHERE l.tenant_id=%s AND l.deal_id=%s AND l.run_id=%s)
                """,
                (
                    scope.tenant_id,
                    scope.deal_id,
                    scope.run_id,
                    scope.tenant_id,
                    scope.deal_id,
                    scope.run_id,
                    scope.tenant_id,
                    scope.deal_id,
                    scope.run_id,
                ),
            )
            coverage_count, dashboard_count, root_link_count = (
                int(value) for value in cursor.fetchone()
            )
            for graph in bound.graphs:
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
                        graph.dashboard_output_object_id,
                    ),
                )
                row = cursor.fetchone()
                expected = [
                    "METHODOLOGY_TRUTH_DASHBOARD_ROW",
                    graph.native_identity_sha256,
                    graph.native_output_sha256,
                ]
                if row is None or [str(value) for value in row] != expected:
                    _fail("TASK8_V27_CENSUS_NATIVE_HELPER_MISMATCH")
                helper_rows.append([str(graph.dashboard_row_id), *expected])
        connection.commit()
    finally:
        connection.close()
    if (
        observer_role != urls.application_role
        or coverage_count != 100
        or dashboard_count != 100
        or root_link_count < 100
    ):
        _fail("TASK8_V27_CENSUS_DASHBOARD_BATCH_INVALID")
    closure_manifest = build_seed_fk_closure_manifest(artifact)
    closure = _observe_seed_fk_closure(
        urls.application,
        expected_application_role=urls.application_role,
        tenant_id=scope.tenant_id,
        deal_id=scope.deal_id,
        closure_manifest=closure_manifest,
    )
    dashboard_helper_sha256 = _canonical_sha256(
        ["task8-v27-dashboard-native-helper-equality-v1", helper_rows]
    )
    bound = replace(
        bound,
        seed_fk_closure_sha256=str(closure["expected_sha256"]),
        seed_native_helper_equality_sha256=_canonical_sha256(
            [
                "task8-v27-complete-seed-native-helper-equality-v1",
                bound.seed_native_helper_equality_sha256,
                dashboard_helper_sha256,
            ]
        ),
    )
    receipt = {
        "schema_version": "task8-v27-main-dashboard-census-v1",
        "observer_role": observer_role,
        "coverage_row_count": coverage_count,
        "dashboard_row_count": dashboard_count,
        "root_link_count": root_link_count,
        "dashboard_native_helper_sha256": dashboard_helper_sha256,
    }
    return scope, bound, {**receipt, "expected_sha256": _canonical_sha256(receipt)}


def _additional_dashboard_graph(
    graph: _DashboardGraph,
    *,
    coverage_record_id: str,
    dashboard_output_ordinal: int,
) -> _DashboardGraph:
    """Build a shape-valid extra row used only to reach an immediate FK/UNIQUE guard."""

    question_payload_hash = _canonical_sha256(
        ["task8-v27-extra-dashboard-question-v1", coverage_record_id]
    )
    identity = derive_v27_dashboard_identity(
        tenant_id=str(graph.scope.tenant_id),
        deal_id=str(graph.scope.deal_id),
        run_id=str(graph.scope.run_id),
        pipeline_contract_version=CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION,
        pipeline_contract_hash=CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH,
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


def _seed_erasure_request(
    application_url: str,
    expected_application_role: str,
    scope: _CaseScope,
) -> uuid.UUID:
    try:
        from idis.compliance.task7_erasure import (
            build_erasure_audit_event,
            derive_attempt_lineage,
            object_target_set_sha256,
        )
    except ImportError as exc:
        raise Task8V27CensusError("TASK8_V27_CENSUS_ERASURE_FIXTURE_UNAVAILABLE") from exc
    request_id = _next_fixture_uuid()
    actor_id = "task8-census-application"
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
    connection = _connect_fixture_dml(
        application_url,
        expected_application_role=expected_application_role,
        tenant_id=scope.tenant_id,
        case_coordinate=scope.case_coordinate,
        operation_prefix="ERASURE_REQUEST_SEED",
    )
    try:
        with connection.cursor() as cursor:
            _set_tenant(cursor, scope.tenant_id)
            cursor.execute(
                """
                INSERT INTO public.erasure_requests(
                    tenant_id,request_id,deal_id,status,requested_by,requested_at,
                    reason_hash,reason_length
                ) VALUES (%s,%s,%s,'REQUESTED',%s,%s,%s,1)
                """,
                (
                    scope.tenant_id,
                    request_id,
                    scope.deal_id,
                    actor_id,
                    occurred_at,
                    _fixture_hash("erasure-request-reason", request_id),
                ),
            )
            cursor.execute(
                """
                INSERT INTO public.audit_events(
                    event_id,tenant_id,occurred_at,event_type,request_id,
                    idempotency_key,event
                ) VALUES (%s,%s,%s,'task7.erasure.attempted',%s,%s,%s::jsonb)
                """,
                (
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
                _fixture_hash(
                    "illegal-future-rls-dashboard-payload",
                    graph.dashboard_row_id,
                ),
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
    _fail("TASK8_V27_CENSUS_RLS_RELATION_INVALID")


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
        _fail("TASK8_V27_CENSUS_PROJECTION_MANIFEST_INVALID")
    connection = _connect(application_database_url)
    try:
        with connection.cursor() as cursor:
            observer_posture = _attest_connection_role_posture(
                cursor,
                expected_application_role,
            )
            current_user = _posture_role(observer_posture)
            _set_tenant(cursor, tenant_id)
            full_rows: list[list[Any]] = []
            stable_rows: list[list[Any]] = []
            for table_name in relation_tuple:
                if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name) is None:
                    _fail("TASK8_V27_CENSUS_PROJECTION_RELATION_INVALID")
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
                    _fail("TASK8_V27_CENSUS_PROJECTION_RELATION_MISSING")
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
                    _fail(f"TASK8_V27_CENSUS_PROJECTION_SCOPE_UNPROVEN:{table_name}")
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
                "task8-v27-tenant-deal-full-projection-v1",
                projection_manifest["expected_sha256"],
                str(tenant_id),
                str(deal_id),
                full_rows,
            ]
        ),
        "projection_sha256": _canonical_sha256(
            [
                "task8-v27-tenant-deal-projection-v1",
                projection_manifest["expected_sha256"],
                str(tenant_id),
                str(deal_id),
                stable_rows,
            ]
        ),
    }


def _dashboard_scope_counts(cursor: Any, scope: _CaseScope) -> tuple[int, int]:
    """Observe dashboard native rows and roots for one exact run scope."""

    cursor.execute(
        """
        SELECT
          (SELECT count(*) FROM public.methodology_truth_dashboard_rows d
            WHERE d.tenant_id=%s AND d.deal_id=%s AND d.run_id=%s),
          (SELECT count(*) FROM public.run_output_objects o
            WHERE o.tenant_id=%s AND o.deal_id=%s AND o.run_id=%s
              AND o.output_kind='METHODOLOGY_TRUTH_DASHBOARD_ROW')
        """,
        (
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    return int(row[0]), int(row[1])


def _database_error_identity(exc: BaseException) -> tuple[str | None, str | None]:
    sqlstate = getattr(exc, "pgcode", None)
    diagnostic = getattr(exc, "diag", None)
    reason = getattr(diagnostic, "message_primary", None)
    return (
        None if sqlstate is None else str(sqlstate),
        None if reason is None else str(reason),
    )


def _insert_cross_version_coverage_probe(
    cursor: Any,
    *,
    scope: _CaseScope,
    vector: Mapping[str, object],
) -> None:
    """Attempt one governed cross-version coverage row without bypassing guards."""

    from idis.models.methodology_coverage import MethodologyCoverageRecord

    cursor.execute(
        """
        SELECT c.selection_id::text,c.methodology_version_record_id::text,
               c.methodology_question_record_id::text,c.methodology_question_id,
               c.methodology_type,q.methodology_id,q.section
          FROM public.methodology_coverage_records AS c
          JOIN public.methodology_questions AS q
            ON q.tenant_id=c.tenant_id
           AND q.methodology_question_record_id=c.methodology_question_record_id
         WHERE c.tenant_id=%s AND c.deal_id=%s AND c.run_id=%s
           AND c.methodology_type='financial_dd'
         ORDER BY c.coverage_record_id
         LIMIT 1
        """,
        (scope.tenant_id, scope.deal_id, scope.run_id),
    )
    row = cursor.fetchone()
    if row is None:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    (
        selection_id,
        version_record_id,
        question_record_id,
        question_id,
        methodology_type,
        methodology_id,
        section,
    ) = row
    mismatched = MethodologyCoverageRecord.model_validate(
        {
            "tenant_id": str(scope.tenant_id),
            "deal_id": str(scope.deal_id),
            "run_id": str(scope.run_id),
            "methodology_id": str(methodology_id),
            "methodology_version_id": str(vector["joined_methodology_version_id"]),
            "methodology_question_id": str(question_id),
            "methodology_type": str(methodology_type),
            "section": str(section),
        }
    )
    step = next(
        item
        for item in build_current_run_step_plan(
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            run_id=str(scope.run_id),
        )
        if item["step_order"] == 4
    )
    root = _derive_seed_native_root(
        scope=scope,
        step=step,
        native_id=mismatched.coverage_record_id,
        output_kind="METHODOLOGY_COVERAGE_IDENTITY",
        output_schema_version="methodology-coverage-identity-v1",
        output_ordinal=100,
        native_key=[mismatched.coverage_record_id],
        native_payload=[
            str(selection_id),
            str(version_record_id),
            mismatched.methodology_version_id,
            str(question_record_id),
            mismatched.methodology_question_id,
            mismatched.methodology_type.value,
        ],
    )
    _persist_seed_native_root(cursor, scope=scope, plan=root)
    cursor.execute(
        """
        INSERT INTO public.methodology_coverage_records(
            coverage_record_id,tenant_id,deal_id,run_id,selection_id,
            methodology_version_record_id,methodology_version_id,
            methodology_question_record_id,methodology_question_id,
            methodology_type,status,evidence_links,conflict_ids,defect_ids,
            run_output_object_id
        ) VALUES (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'not_started','[]'::jsonb,
            ARRAY[]::text[],ARRAY[]::text[],%s
        )
        """,
        (
            mismatched.coverage_record_id,
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            uuid.UUID(str(selection_id)),
            uuid.UUID(str(version_record_id)),
            mismatched.methodology_version_id,
            uuid.UUID(str(question_record_id)),
            mismatched.methodology_question_id,
            mismatched.methodology_type.value,
            uuid.UUID(root.output_object_id),
        ),
    )


def _observe_legal_requirement_cases(
    application_url: str,
    *,
    expected_application_role: str,
    scope: _CaseScope,
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    main_dashboard_census_sha256: str,
) -> list[dict[str, object]]:
    """Derive all four legal vectors from the one committed production graph."""

    contract = artifact["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"]
    policy_version_id = str(contract["policy_v2_methodology_version_id"])
    approved_tuple = [
        [int(row[0]), str(row[1]), str(row[2])]
        for row in contract["policy_v2_requirement_literal_tuple"]
    ]
    cdd_raw = _capture_cdd_template_bytes()
    seed_plan = build_v27_dashboard_seed_plan(
        artifact,
        policy_artifact_raw,
        workbook_raw,
        cdd_raw=cdd_raw,
        tenant_id=str(scope.tenant_id),
        deal_id=str(scope.deal_id),
        run_id=str(scope.run_id),
    )
    expected_by_coverage = {str(row["coverage_record_id"]): row for row in seed_plan["row_tuple"]}
    connection = _connect(application_url)
    try:
        with connection.cursor() as cursor:
            posture = _attest_connection_role_posture(cursor, expected_application_role)
            _set_tenant(cursor, scope.tenant_id)
            cursor.execute(
                """
                SELECT c.methodology_type,c.methodology_version_id,
                       c.methodology_version_record_id::text,
                       c.coverage_record_id,q.methodology_question_id,
                       c.methodology_question_record_id::text,
                       q.question_payload_hash,q.question_payload,
                       COALESCE(
                           array_agg(DISTINCT calc.calc_type ORDER BY calc.calc_type)
                               FILTER (WHERE calc.calc_id IS NOT NULL),
                           ARRAY[]::text[]
                       ),
                       d.dashboard_row_id::text,d.run_output_object_id::text
                  FROM public.methodology_coverage_records AS c
                  JOIN public.methodology_questions AS q
                    ON q.tenant_id=c.tenant_id
                   AND q.methodology_question_record_id=c.methodology_question_record_id
                  JOIN public.methodology_truth_dashboard_rows AS d
                    ON d.tenant_id=c.tenant_id AND d.deal_id=c.deal_id
                   AND d.run_id=c.run_id
                   AND d.coverage_record_id=c.coverage_record_id
                  LEFT JOIN public.deterministic_calculations AS calc
                    ON calc.tenant_id=c.tenant_id AND calc.deal_id=c.deal_id
                   AND calc.run_id=c.run_id
                   AND calc.coverage_record_id=c.coverage_record_id
                 WHERE c.tenant_id=%s AND c.deal_id=%s AND c.run_id=%s
                 GROUP BY c.methodology_type,c.methodology_version_id,
                          c.methodology_version_record_id,c.coverage_record_id,
                          q.methodology_question_id,c.methodology_question_record_id,
                          q.question_payload_hash,q.question_payload,
                          d.dashboard_row_id,d.run_output_object_id
                 ORDER BY c.methodology_type,c.coverage_record_id
                """,
                (scope.tenant_id, scope.deal_id, scope.run_id),
            )
            raw_rows = cursor.fetchall()
        connection.rollback()
    finally:
        connection.close()
    projected: list[dict[str, object]] = []
    for row in raw_rows:
        payload = row[7]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
        requirements = payload.get("required_calculations")
        if not isinstance(requirements, list):
            _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
        projected.append(
            {
                "methodology_type": str(row[0]),
                "methodology_version_id": str(row[1]),
                "methodology_version_record_id": str(row[2]),
                "coverage_record_id": str(row[3]),
                "methodology_question_id": str(row[4]),
                "methodology_question_record_id": str(row[5]),
                "question_payload_sha256": str(row[6]),
                "requirements": requirements,
                "calculation_type_tuple": [str(value) for value in row[8]],
                "dashboard_row_id": str(row[9]),
                "dashboard_output_object_id": str(row[10]),
            }
        )
    for observed in projected:
        planned = expected_by_coverage.get(str(observed["coverage_record_id"]))
        if (
            planned is None
            or observed["methodology_type"] != planned["methodology_type"]
            or observed["methodology_version_id"] != planned["methodology_version_id"]
            or observed["methodology_version_record_id"] != planned["methodology_version_record_id"]
            or observed["methodology_question_id"] != planned["methodology_question_id"]
            or observed["methodology_question_record_id"]
            != planned["methodology_question_record_id"]
            or observed["question_payload_sha256"] != planned["question_payload_sha256"]
            or observed["requirements"] != planned["required_calculation_tuple"]
            or observed["dashboard_row_id"] != planned["dashboard_row_id"]
        ):
            _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    commercial = [row for row in projected if row["methodology_type"] == "commercial_dd"]
    financial = [row for row in projected if row["methodology_type"] == "financial_dd"]
    commercial_empty = [row for row in commercial if row["requirements"] == []]
    commercial_two_field = [
        row
        for row in commercial
        if row["requirements"] == [{"calc_type": "LTV_CAC_RATIO", "required": False}]
        and row["calculation_type_tuple"] == []
    ]
    financial_empty = [row for row in financial if row["requirements"] == []]
    financial_required = [row for row in financial if row["requirements"] != []]
    observed_policy_tuple = sorted(
        [
            [
                int(requirement["formula_ordinal"]),
                str(requirement["formula_type"]),
                str(requirement["scope"]),
            ]
            for row in financial_required
            for requirement in row["requirements"]
            if requirement.get("required") is True
        ]
    )
    if (
        len(projected) != 100
        or len(commercial) != 21
        or len(commercial_empty) != 20
        or len(commercial_two_field) != 1
        or len(financial) != 79
        or any(row["methodology_version_id"] != policy_version_id for row in financial)
        or len(financial_empty) != 69
        or len(financial_required) != 10
        or observed_policy_tuple != sorted(approved_tuple)
        or any(
            row["calculation_type_tuple"] != [str(row["requirements"][0]["formula_type"])]
            for row in financial_required
        )
        or _SHA256_RE.fullmatch(main_dashboard_census_sha256) is None
    ):
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    cases = (
        ("LEGACY_EMPTY", commercial_empty),
        ("LEGACY_TWO_FIELD", commercial_two_field),
        ("POLICY_V2_EMPTY", financial_empty),
        ("POLICY_V2_TEN_LITERAL_TUPLES", financial_required),
    )
    results: list[dict[str, object]] = []
    for case_id, case_rows in cases:
        results.append(
            {
                "case_id": case_id,
                "expected_accepted": True,
                "observed_accepted": True,
                "observer_role": _posture_role(posture),
                "observer_posture_sha256": _connection_posture_sha256(posture),
                "session_replication_role": posture["session_replication_role"],
                "sqlstate": None,
                "reason_code": None,
                "rejection_path": None,
                "guard_reached": True,
                "zero_mutation": True,
                "dashboard_row_delta": 0,
                "dashboard_root_delta": 0,
                "pre_projection_sha256": None,
                "post_projection_sha256": None,
                "stable_pre_projection_sha256": None,
                "stable_post_projection_sha256": None,
                "production_graph_sha256": main_dashboard_census_sha256,
                "legal_projection_sha256": _canonical_sha256(
                    ["task8-v27-legal-requirement-observation-v1", case_id, case_rows]
                ),
            }
        )
    return results


def _attach_undeclared_calculation_root(
    *,
    urls: _DisposableUrls,
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    scope: _CaseScope,
    bound: _BoundRuntimeGraph,
    vector: Mapping[str, object],
) -> _BoundRuntimeGraph:
    """Attach one valid calc root to a real policy-empty question for rejection."""

    calculation_types = vector.get("calculation_type_tuple")
    if (
        vector.get("case_id") != "UNDECLARED_FDD_CALCULATION_ROOT"
        or not isinstance(calculation_types, list)
        or len(calculation_types) != 1
    ):
        _fail("TASK8_V27_CENSUS_REQUIREMENT_FIXTURE_SCOPE_INVALID")
    connection = _connect_fixture_dml(
        urls.application,
        expected_application_role=urls.application_role,
        tenant_id=scope.tenant_id,
        case_coordinate=scope.case_coordinate,
        operation_prefix="UNDECLARED_CALCULATION_ROOT",
    )
    plan: _NativeRootPlan | None = None
    try:
        with connection.cursor() as cursor:
            posture = _attest_connection_role_posture(cursor, urls.application_role)
            _set_worker_lease(cursor, scope)
            cursor.execute(
                """
                SELECT c.coverage_record_id,c.methodology_question_record_id::text
                  FROM public.methodology_coverage_records AS c
                  JOIN public.methodology_questions AS q
                    ON q.tenant_id=c.tenant_id
                   AND q.methodology_question_record_id=c.methodology_question_record_id
                 WHERE c.tenant_id=%s AND c.deal_id=%s AND c.run_id=%s
                   AND c.methodology_type='financial_dd'
                   AND q.question_payload->'required_calculations'='[]'::jsonb
                 ORDER BY c.coverage_record_id
                 LIMIT 1
                """,
                (scope.tenant_id, scope.deal_id, scope.run_id),
            )
            row = cursor.fetchone()
            if row is None:
                _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
            coverage_record_id = str(row[0])
            question_record_id = str(row[1])
            calc_type = str(calculation_types[0])
            calc_id = _fixture_uuid(
                "undeclared-calculation", scope.run_id, coverage_record_id, calc_type
            )
            calc_sanad_id = _fixture_uuid("undeclared-calculation-sanad", calc_id)
            inputs = {
                "schema_version": "task8-v27-calculation-input-v1",
                "claim_ids": [],
                "formula_ordinal": 1,
            }
            output = {
                "schema_version": "task8-v27-calculation-output-v1",
                "value": "1",
            }
            formula_authority = {
                "formula_ordinal": 1,
                "formula_type": calc_type,
                "declared_requirement": None,
            }
            formula_hash = _canonical_sha256(["task8-v27-formula-v1", formula_authority])
            reproducibility_hash = _canonical_sha256(
                [
                    "task8-v27-calculation-reproducibility-v1",
                    calc_id,
                    inputs,
                    output,
                    formula_hash,
                ]
            )
            explanation = [{"kind": "hermetic_census", "formula_ordinal": 1}]
            step = next(
                item
                for item in build_current_run_step_plan(
                    tenant_id=str(scope.tenant_id),
                    deal_id=str(scope.deal_id),
                    run_id=str(scope.run_id),
                )
                if item["step_order"] == 10
            )
            plan = _derive_seed_native_root(
                scope=scope,
                step=step,
                native_id=calc_id,
                output_kind="METHODOLOGY_CALCULATION",
                output_schema_version="task8-fdd-deterministic-calculation-v1",
                output_ordinal=10,
                native_key=[calc_id],
                native_payload=[
                    calc_type,
                    inputs,
                    output,
                    formula_hash,
                    "task8-v27-census-calculator-v1",
                    reproducibility_hash,
                    [calc_sanad_id, [], "A", "A", explanation],
                ],
            )
            _persist_seed_native_root(cursor, scope=scope, plan=plan)
            cursor.execute(
                """
                INSERT INTO public.deterministic_calculations(
                    calc_id,tenant_id,deal_id,run_id,producer_step_name,
                    run_output_object_id,run_output_object_kind,
                    coverage_record_id,methodology_question_record_id,
                    calc_type,inputs,formula_hash,code_version,output,
                    reproducibility_hash,created_at,updated_at
                ) VALUES (
                    %s,%s,%s,%s,'METHODOLOGY_DETERMINISTIC_CALCULATION',
                    %s,'METHODOLOGY_CALCULATION',%s,%s,%s,%s::jsonb,%s,%s,
                    %s::jsonb,%s,%s,%s
                );
                INSERT INTO public.calc_sanads(
                    calc_sanad_id,tenant_id,deal_id,run_id,calc_id,
                    input_claim_ids,input_min_sanad_grade,calc_grade,
                    explanation,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,'A','A',%s::jsonb,%s,%s)
                """,
                (
                    uuid.UUID(calc_id),
                    scope.tenant_id,
                    scope.deal_id,
                    scope.run_id,
                    uuid.UUID(plan.output_object_id),
                    coverage_record_id,
                    uuid.UUID(question_record_id),
                    calc_type,
                    json.dumps(inputs, separators=(",", ":"), sort_keys=True),
                    formula_hash,
                    "task8-v27-census-calculator-v1",
                    json.dumps(output, separators=(",", ":"), sort_keys=True),
                    reproducibility_hash,
                    _FIXTURE_OCCURRED_AT,
                    _FIXTURE_OCCURRED_AT,
                    uuid.UUID(calc_sanad_id),
                    scope.tenant_id,
                    scope.deal_id,
                    scope.run_id,
                    uuid.UUID(calc_id),
                    json.dumps(explanation, separators=(",", ":"), sort_keys=True),
                    _FIXTURE_OCCURRED_AT,
                    _FIXTURE_OCCURRED_AT,
                ),
            )
            helper_sha256 = _verify_seed_native_roots(cursor, scope=scope, plans=(plan,))
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        connection.commit()
    finally:
        connection.close()
    if plan is None or _posture_role(posture) != urls.application_role:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    cdd_raw = _capture_cdd_template_bytes()
    seed_plan = build_v27_dashboard_seed_plan(
        artifact,
        policy_artifact_raw,
        workbook_raw,
        cdd_raw=cdd_raw,
        tenant_id=str(scope.tenant_id),
        deal_id=str(scope.deal_id),
        run_id=str(scope.run_id),
    )
    graphs = _load_bound_dashboard_graphs(
        application_url=urls.application,
        expected_application_role=urls.application_role,
        scope=scope,
        seed_plan=seed_plan,
        focus_semantic_case="PRODUCTION_POLICY_V2_BASELINE",
        expected_coverage_row_count=100,
    )
    closure = _observe_seed_fk_closure(
        urls.application,
        expected_application_role=urls.application_role,
        tenant_id=scope.tenant_id,
        deal_id=scope.deal_id,
        closure_manifest=build_seed_fk_closure_manifest(artifact),
    )
    return replace(
        bound,
        graphs=graphs,
        seed_fk_closure_sha256=str(closure["expected_sha256"]),
        seed_native_helper_equality_sha256=_canonical_sha256(
            [
                "task8-v27-seed-native-helper-equality-set-with-undeclared-v1",
                bound.seed_native_helper_equality_sha256,
                helper_sha256,
            ]
        ),
    )


def _execute_requirement_case(
    vector: Mapping[str, object],
    *,
    case_ordinal: int,
    urls: _DisposableUrls,
    projection_manifest: Mapping[str, Any],
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
) -> dict[str, object]:
    """Exercise one requirement vector through installed app-role authorities."""

    case_id = str(vector["case_id"])
    if (
        case_ordinal < 1
        or case_ordinal > len(_FROZEN_REQUIREMENT_CASE_ID_TUPLE)
        or _FROZEN_REQUIREMENT_CASE_ID_TUPLE[case_ordinal - 1] != case_id
    ):
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    case_coordinate = f"REQUIREMENT_{case_ordinal:02d}"
    scope = _seed_scope(urls.application, urls.application_role, case_coordinate)
    expected_accepted = vector.get("accepted") is True
    if expected_accepted:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_FIXTURE_SCOPE_INVALID")
    if case_id in {"CROSS_VERSION_JOIN", "UNDECLARED_FDD_CALCULATION_ROOT"}:
        bound = _persist_bound_runtime_graph(
            urls=urls,
            artifact=artifact,
            policy_artifact_raw=policy_artifact_raw,
            workbook_raw=workbook_raw,
            scope=scope,
            focus_semantic_case="PRODUCTION_POLICY_V2_BASELINE",
        )
        if case_id == "UNDECLARED_FDD_CALCULATION_ROOT":
            bound = _attach_undeclared_calculation_root(
                urls=urls,
                artifact=artifact,
                policy_artifact_raw=policy_artifact_raw,
                workbook_raw=workbook_raw,
                scope=scope,
                bound=bound,
                vector=vector,
            )
    else:
        requirement_seed = _build_requirement_seed_spec(
            artifact=artifact,
            policy_artifact_raw=policy_artifact_raw,
            workbook_raw=workbook_raw,
            scope=scope,
            vector=vector,
        )
        bound = _persist_runtime_graph_core(
            urls=urls,
            artifact=artifact,
            policy_artifact_raw=policy_artifact_raw,
            workbook_raw=workbook_raw,
            scope=scope,
            focus_semantic_case="PRODUCTION_POLICY_V2_BASELINE",
            requirement_seed=requirement_seed,
        )
    pre_projection = _tenant_deal_projection(
        urls.application,
        expected_application_role=urls.application_role,
        tenant_id=scope.tenant_id,
        deal_id=scope.deal_id,
        projection_manifest=projection_manifest,
    )
    pre_connection = _connect(urls.application)
    try:
        with pre_connection.cursor() as cursor:
            pre_posture = _attest_connection_role_posture(cursor, urls.application_role)
            _set_tenant(cursor, scope.tenant_id)
            pre_dashboard_count, pre_root_count = _dashboard_scope_counts(cursor, scope)
        pre_connection.rollback()
    finally:
        pre_connection.close()

    connection = _connect_behavior_dml(
        urls.application,
        expected_role=urls.application_role,
        tenant_id=scope.tenant_id,
        case_coordinate=case_coordinate,
        operation_prefix=f"REQUIREMENT_CASE_{case_ordinal:02d}",
        dml_classification=_APPLICATION_BEHAVIOR_DML,
    )
    observed_accepted = False
    sqlstate: str | None = None
    reason_code: str | None = None
    guard_reached = False
    write_posture: Mapping[str, object] | None = None
    try:
        with connection.cursor() as cursor:
            write_posture = _attest_connection_role_posture(cursor, urls.application_role)
            _set_worker_lease(cursor, scope)
            if case_id == "CROSS_VERSION_JOIN":
                _insert_cross_version_coverage_probe(cursor, scope=scope, vector=vector)
            else:
                _insert_dashboard_batch(cursor, bound.graphs)
                guard_reached = True
                cursor.execute("SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE")
                cursor.execute("SET CONSTRAINTS ctrg_v3_dashboard_root_equation IMMEDIATE")
        connection.commit()
        observed_accepted = True
    except Exception as exc:
        sqlstate, reason_code = _database_error_identity(exc)
        connection.rollback()
    finally:
        connection.close()
    if write_posture is None:
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")

    post_projection = _tenant_deal_projection(
        urls.application,
        expected_application_role=urls.application_role,
        tenant_id=scope.tenant_id,
        deal_id=scope.deal_id,
        projection_manifest=projection_manifest,
    )
    post_connection = _connect(urls.application)
    try:
        with post_connection.cursor() as cursor:
            post_posture = _attest_connection_role_posture(cursor, urls.application_role)
            _set_tenant(cursor, scope.tenant_id)
            post_dashboard_count, post_root_count = _dashboard_scope_counts(cursor, scope)
        post_connection.rollback()
    finally:
        post_connection.close()
    rejection_path = None
    if not expected_accepted:
        rejection_path = "SCHEMA_PREEMPTION" if case_id == "CROSS_VERSION_JOIN" else "EQUATION_V3"
    zero_mutation = (
        pre_projection["full_projection_sha256"] == post_projection["full_projection_sha256"]
        and pre_projection["projection_sha256"] == post_projection["projection_sha256"]
    )
    return {
        "case_id": case_id,
        "expected_accepted": expected_accepted,
        "observed_accepted": observed_accepted,
        "observer_role": _posture_role(write_posture),
        "observer_posture_sha256": _connection_posture_sha256(write_posture),
        "pre_observer_posture_sha256": _connection_posture_sha256(pre_posture),
        "post_observer_posture_sha256": _connection_posture_sha256(post_posture),
        "session_replication_role": write_posture["session_replication_role"],
        "sqlstate": sqlstate,
        "reason_code": reason_code,
        "rejection_path": rejection_path,
        "guard_reached": guard_reached,
        "zero_mutation": zero_mutation,
        "dashboard_row_delta": post_dashboard_count - pre_dashboard_count,
        "dashboard_root_delta": post_root_count - pre_root_count,
        "pre_projection_sha256": pre_projection["full_projection_sha256"],
        "post_projection_sha256": post_projection["full_projection_sha256"],
        "stable_pre_projection_sha256": pre_projection["projection_sha256"],
        "stable_post_projection_sha256": post_projection["projection_sha256"],
        "seed_plan_sha256": bound.dashboard_seed_plan_sha256,
        "seed_db_projection_sha256": bound.dashboard_seed_db_projection_sha256,
        "seed_fk_closure_sha256": bound.seed_fk_closure_sha256,
        "seed_native_helper_sha256": bound.seed_native_helper_equality_sha256,
        "source_freeze_sha256": bound.source_freeze_sha256,
        "task5_roundtrip_sha256": bound.task5_roundtrip_sha256,
    }


def _observe_installed_dashboard_authority(
    application_url: str,
    *,
    expected_application_role: str,
    artifact: Mapping[str, Any],
) -> dict[str, object]:
    """Bind pg_proc and trigger topology to the reviewed v27 body."""

    binding = _dashboard_authority_binding(artifact)
    connection = _connect(application_url)
    try:
        with connection.cursor() as cursor:
            posture = _attest_connection_role_posture(cursor, expected_application_role)
            cursor.execute(
                """
                SELECT p.prosrc
                  FROM pg_catalog.pg_proc AS p
                  JOIN pg_catalog.pg_namespace AS n ON n.oid=p.pronamespace
                 WHERE n.nspname='public'
                   AND p.proname='idis_task8_dashboard_root_equation_v3'
                   AND pg_catalog.pg_get_function_identity_arguments(p.oid)=''
                """
            )
            function_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT t.tgname,c.relname,t.tgtype,t.tgdeferrable,
                       t.tginitdeferred,t.tgenabled,
                       p.proname || '(' ||
                       pg_catalog.pg_get_function_identity_arguments(p.oid) || ')'
                  FROM pg_catalog.pg_trigger AS t
                  JOIN pg_catalog.pg_class AS c ON c.oid=t.tgrelid
                  JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace
                  JOIN pg_catalog.pg_proc AS p ON p.oid=t.tgfoid
                 WHERE n.nspname='public'
                   AND t.tgname IN (
                       'ctrg_v2_dashboard_root_equation',
                       'ctrg_v3_dashboard_root_equation'
                   )
                 ORDER BY t.tgname,c.relname
                """
            )
            trigger_rows = [
                [
                    str(row[0]),
                    str(row[1]),
                    int(row[2]),
                    bool(row[3]),
                    bool(row[4]),
                    str(row[5]),
                    str(row[6]),
                ]
                for row in cursor.fetchall()
            ]
        connection.rollback()
    finally:
        connection.close()
    expected_triggers = [
        [
            "ctrg_v2_dashboard_root_equation",
            "methodology_truth_dashboard_rows",
            29,
            True,
            True,
            "O",
            "idis_task8_dashboard_root_equation_v3()",
        ],
        [
            "ctrg_v3_dashboard_root_equation",
            "methodology_truth_dashboard_root_links",
            29,
            True,
            True,
            "O",
            "idis_task8_dashboard_root_equation_v3()",
        ],
    ]
    if (
        len(function_rows) != 1
        or hashlib.sha256(str(function_rows[0][0]).encode("utf-8")).hexdigest()
        != binding["semantic_body_sha256"]
        or trigger_rows != expected_triggers
    ):
        _fail("TASK8_V27_CENSUS_REQUIREMENT_AUTHORITY_CATALOG_INVALID")
    payload = {
        "schema_version": "task8-v27-dashboard-authority-catalog-v1",
        "observer_role": _posture_role(posture),
        "observer_posture_sha256": _connection_posture_sha256(posture),
        "session_replication_role": posture["session_replication_role"],
        "authority_row_sha256": binding["authority_row_sha256"],
        "installation_tuple_sha256": binding["install_tuple_sha256"],
        "semantic_body_sha256": binding["semantic_body_sha256"],
        "installed_prosrc_sha256": hashlib.sha256(
            str(function_rows[0][0]).encode("utf-8")
        ).hexdigest(),
        "trigger_topology_sha256": _canonical_sha256(expected_triggers),
    }
    return {**payload, "expected_sha256": _canonical_sha256(payload)}


def _run_dashboard_requirement_matrix(
    application_url: str,
    *,
    urls: _DisposableUrls,
    expected_application_role: str,
    projection_manifest: Mapping[str, Any],
    tenant_id: uuid.UUID,
    deal_id: uuid.UUID,
    main_scope: _CaseScope,
    main_dashboard_census_sha256: str,
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
) -> dict[str, object]:
    """Execute all 17 vectors through the installed v3 constraint authority."""

    if (
        application_url != urls.application
        or tenant_id != main_scope.tenant_id
        or deal_id != main_scope.deal_id
    ):
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    matrix = _requirement_matrix()
    authority = _observe_installed_dashboard_authority(
        application_url,
        expected_application_role=expected_application_role,
        artifact=artifact,
    )
    vectors = _requirement_case_vectors(artifact)
    results = _observe_legal_requirement_cases(
        application_url,
        expected_application_role=expected_application_role,
        scope=main_scope,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
        main_dashboard_census_sha256=main_dashboard_census_sha256,
    )
    negative_vectors = tuple(vector for vector in vectors if vector["accepted"] is False)
    results.extend(
        _execute_requirement_case(
            vector,
            case_ordinal=ordinal,
            urls=urls,
            projection_manifest=projection_manifest,
            artifact=artifact,
            policy_artifact_raw=policy_artifact_raw,
            workbook_raw=workbook_raw,
        )
        for ordinal, vector in enumerate(negative_vectors, start=1)
    )
    validated = validate_requirement_matrix_result_rows(
        results,
        expected_application_role=expected_application_role,
    )
    if (
        {row["case_id"] for row in results if row["expected_accepted"] is True}
        != set(matrix["legal_case_id_tuple"])
        or {row["case_id"] for row in results if row["expected_accepted"] is False}
        != set(matrix["negative_case_id_tuple"])
        or any(
            row["guard_reached"] is not True
            for row in results
            if row["rejection_path"] == "EQUATION_V3"
        )
        or next(row for row in results if row["case_id"] == "CROSS_VERSION_JOIN")["guard_reached"]
        is not False
    ):
        _fail("TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID")
    receipt = {
        "schema_version": "task8-v27-dashboard-requirement-postgres-census-v2",
        "execution_role": "application",
        "authority_body_sha256": authority["semantic_body_sha256"],
        "authority_installation_sha256": authority["installation_tuple_sha256"],
        "authority_catalog_sha256": authority["expected_sha256"],
        "predicate_sha256": authority["semantic_body_sha256"],
        "legal_case_count": validated["legal_case_count"],
        "negative_case_count": validated["negative_case_count"],
        "equation_v3_rejection_count": validated["equation_v3_rejection_count"],
        "schema_preemption_count": validated["schema_preemption_count"],
        "negative_zero_mutation_count": validated["negative_zero_mutation_count"],
        "negative_zero_dashboard_derivation_count": validated[
            "negative_zero_dashboard_derivation_count"
        ],
        "case_result_sha256": validated["case_result_sha256"],
    }
    return {**receipt, "expected_sha256": _canonical_sha256(receipt)}


def _run_rls_behavior_matrix(
    urls: _DisposableUrls,
    projection_manifest: Mapping[str, Any],
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    dashboard_coverage_row_count: int,
    dashboard_financial_coverage_row_count: int,
) -> tuple[bool, list[dict[str, Any]]]:
    scope = _seed_scope(
        urls.application,
        urls.application_role,
        "RLS",
    )
    bound_graph = _seed_dashboard_batch(
        urls,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
        scope=scope,
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=dashboard_coverage_row_count,
        financial_coverage_row_count=dashboard_financial_coverage_row_count,
    )
    graphs = bound_graph.graphs
    graph = graphs[0]
    baseline = _connect_fixture_dml(
        urls.application,
        expected_application_role=urls.application_role,
        tenant_id=scope.tenant_id,
        case_coordinate=scope.case_coordinate,
        operation_prefix="RLS_BASELINE",
    )
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
    write_probe_ordinal = 0
    for mode, tenant_value in (
        ("WRONG_TENANT", _fixture_uuid("rls-probe-tenant", "WRONG_TENANT")),
        ("UNSET_TENANT", ""),
    ):
        read_connection = _connect(urls.application)
        try:
            with read_connection.cursor() as cursor:
                read_posture = _attest_connection_role_posture(
                    cursor,
                    urls.application_role,
                )
                current_user = _posture_role(read_posture)
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
            write_probe_ordinal += 1
            pre = _tenant_deal_projection(
                urls.application,
                expected_application_role=urls.application_role,
                tenant_id=scope.tenant_id,
                deal_id=scope.deal_id,
                projection_manifest=projection_manifest,
            )
            write_connection = _connect_behavior_dml(
                urls.application,
                expected_role=urls.application_role,
                tenant_id=scope.tenant_id,
                session_tenant_id=tenant_value,
                case_coordinate=scope.case_coordinate,
                operation_prefix=f"RLS_PROBE_{write_probe_ordinal:02d}",
                dml_classification=_APPLICATION_BEHAVIOR_DML,
            )
            sqlstate: str | None = None
            write_observer_role: str | None = None
            try:
                with write_connection.cursor() as cursor:
                    write_posture = _attest_connection_role_posture(
                        cursor,
                        urls.application_role,
                    )
                    write_current_user = _posture_role(write_posture)
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
            same_posture = _attest_connection_role_posture(
                cursor,
                urls.application_role,
            )
            same_current_user = _posture_role(same_posture)
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
    case_ordinal: int,
    urls: _DisposableUrls,
    projection_manifest: Mapping[str, Any],
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    dashboard_coverage_row_count: int,
    dashboard_financial_coverage_row_count: int,
) -> dict[str, Any]:
    if (
        case_ordinal < 1
        or case_ordinal > len(_FROZEN_BEHAVIOR_DML_CASE_TUPLE)
        or _FROZEN_BEHAVIOR_DML_CASE_TUPLE[case_ordinal - 1][0] != case.get("case_id")
    ):
        _fail("TASK8_V27_CENSUS_CASE_MANIFEST_INVALID")
    case_coordinate = f"BEHAVIOR_{case_ordinal:02d}"
    scope = _seed_scope(urls.application, urls.application_role, case_coordinate)
    tenant_id = scope.tenant_id
    deal_id = scope.deal_id
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
        bound_graph = _seed_dashboard_batch(
            urls,
            artifact=artifact,
            policy_artifact_raw=policy_artifact_raw,
            workbook_raw=workbook_raw,
            scope=scope,
            focus_semantic_case=graph_semantic,
            coverage_row_count=seed_coverage_row_count,
            financial_coverage_row_count=seed_financial_coverage_row_count,
        )
        graphs = bound_graph.graphs
        graph = graphs[0]
    elif case["authority_id"] == _AUTHORITY_IDS[2]:
        if semantic in {
            "DASHBOARD_ROWS_REQUIRE_FUTURE38",
            "EXECUTOR_CANNOT_BYPASS_DASHBOARD_REFUSAL",
        }:
            bound_graph = _seed_dashboard_batch(
                urls,
                artifact=artifact,
                policy_artifact_raw=policy_artifact_raw,
                workbook_raw=workbook_raw,
                scope=scope,
                focus_semantic_case="ALL_FOUND_ANSWERED",
                coverage_row_count=dashboard_coverage_row_count,
                financial_coverage_row_count=dashboard_financial_coverage_row_count,
            )
            graphs = bound_graph.graphs
            graph = graphs[0]
            app_connection = _connect_fixture_dml(
                urls.application,
                expected_application_role=urls.application_role,
                tenant_id=scope.tenant_id,
                case_coordinate=case_coordinate,
                operation_prefix="BEHAVIOR_DASHBOARD_SEED",
            )
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
        request_id = _seed_erasure_request(
            urls.application,
            urls.application_role,
            scope,
        )

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
    expected_database_role = urls.application_role if role == "application" else urls.erasure_role
    dml_classification = (
        _APPLICATION_BEHAVIOR_DML if role == "application" else _ERASURE_BEHAVIOR_DML
    )
    connection = _connect_behavior_dml(
        database_url,
        expected_role=expected_database_role,
        tenant_id=scope.tenant_id,
        case_coordinate=case_coordinate,
        operation_prefix=f"BEHAVIOR_CASE_{case_ordinal:02d}",
        dml_classification=dml_classification,
    )
    observed_sqlstate: str | None = None
    observed_reason: str | None = None
    observed_current_user: str | None = None
    observed_session_user: str | None = None
    guard_reached = False
    try:
        with connection.cursor() as cursor:
            observed_posture = _attest_connection_role_posture(
                cursor,
                expected_database_role,
            )
            observed_current_user = _posture_role(observed_posture)
            observed_session_user = observed_current_user
            if role == "application":
                _set_worker_lease(cursor, scope)
            else:
                _set_tenant(cursor, scope.tenant_id)
            if case["authority_id"] == _AUTHORITY_IDS[0]:
                if graph is None:
                    _fail("TASK8_V27_CENSUS_FIXTURE_INVALID")
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
                guard_reached = True
                cursor.execute("SET CONSTRAINTS ctrg_v3_dashboard_root_equation IMMEDIATE")
            elif case["authority_id"] == _AUTHORITY_IDS[1]:
                if graph is None:
                    _fail("TASK8_V27_CENSUS_FIXTURE_INVALID")
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
                            _fixture_hash(
                                "illegal-partial-native-root-hash",
                                graph.dashboard_output_object_id,
                            ),
                        ),
                    )
            else:
                if request_id is None:
                    _fail("TASK8_V27_CENSUS_ERASURE_FIXTURE_INVALID")
                _call_erasure(cursor, tenant_id, deal_id, request_id)
        connection.commit()
    except Exception as exc:
        connection.rollback()
        observed_sqlstate = getattr(exc, "pgcode", None)
        observed_reason = str(exc)
        if case["expected"] == "ACCEPT":
            raise Task8V27CensusError(
                "TASK8_V27_CENSUS_LEGAL_CASE_REJECTED:"
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
            _fail("TASK8_V27_CENSUS_UNEXPECTED_REJECTION")
        outcome = "ACCEPTED"
    else:
        expected_sqlstate = case.get("expected_sqlstate")
        if observed_sqlstate != expected_sqlstate:
            _fail("TASK8_V27_CENSUS_REJECTION_SQLSTATE_INVALID")
        reason_code = case.get("expected_reason_code")
        if reason_code and reason_code not in (observed_reason or ""):
            observed_code = (observed_reason or "UNKNOWN").splitlines()[0]
            _fail(f"TASK8_V27_CENSUS_REJECTION_REASON_INVALID:{case['case_id']}:{observed_code}")
        if pre_projection is None or post_projection is None:
            _fail("TASK8_V27_CENSUS_REJECTION_PROJECTION_MISSING")
        if pre_projection != post_projection:
            _fail("TASK8_V27_CENSUS_REJECTION_MUTATED")
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
        "guard_reached": guard_reached,
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
            _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
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
            _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
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
                _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
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
            _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
        function_signatures = [
            guard["function_signature"]
            for row in authority_rows
            if isinstance(row, Mapping) and row.get("authority_id") in _AUTHORITY_IDS[:2]
            for guard in row.get("guard_function_acl_binding_tuple", ())
            if isinstance(guard, Mapping)
        ]
        if len(function_signatures) != len(set(function_signatures)) or not function_signatures:
            _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
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
                _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
            guard = guards[0]
            if guard.get("target_trigger_tuple") != authority.get("target_tuple"):
                _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
            trigger_guards[str(authority["authority_id"])] = guard
        if set(trigger_guards) != set(_AUTHORITY_IDS[:2]):
            _fail("TASK8_V27_CENSUS_CATALOG_INVALID")

        expected_pairs = {
            (str(target), str(trigger))
            for guard in trigger_guards.values()
            for target, trigger in guard["target_trigger_tuple"]
        }
        app_catalog_connection = _connect(urls.application)
        try:
            with app_catalog_connection.cursor() as catalog_cursor:
                catalog_posture = _attest_connection_role_posture(
                    catalog_cursor,
                    urls.application_role,
                )
                catalog_current_user = _posture_role(catalog_posture)
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
            _fail("TASK8_V27_CENSUS_CATALOG_INVALID")

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
                    _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
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
                    _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
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
                _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
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
    artifact: Mapping[str, Any],
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    dashboard_coverage_row_count: int,
    dashboard_financial_coverage_row_count: int,
) -> tuple[bool, bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    prefix = lock_binding.get("prefix")
    seed = lock_binding.get("seed")
    if not isinstance(prefix, str) or not isinstance(seed, int):
        _fail("TASK8_V27_CENSUS_ADVISORY_LOCK_INVALID")

    forward_scope = _seed_scope(
        urls.application,
        urls.application_role,
        "RACE_FORWARD",
    )
    forward_bound_graph = _seed_dashboard_batch(
        urls,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
        scope=forward_scope,
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=dashboard_coverage_row_count,
        financial_coverage_row_count=dashboard_financial_coverage_row_count,
    )
    forward_graphs = forward_bound_graph.graphs
    forward_request_id = _seed_erasure_request(
        urls.application,
        urls.application_role,
        forward_scope,
    )
    executor_connection = _connect_behavior_dml(
        urls.erasure,
        expected_role=urls.erasure_role,
        tenant_id=forward_scope.tenant_id,
        case_coordinate=forward_scope.case_coordinate,
        operation_prefix="RACE_FORWARD_ERASURE",
        dml_classification=_ERASURE_BEHAVIOR_DML,
    )
    application_connection = _connect_behavior_dml(
        urls.application,
        expected_role=urls.application_role,
        tenant_id=forward_scope.tenant_id,
        case_coordinate=forward_scope.case_coordinate,
        operation_prefix="RACE_FORWARD_APPLICATION",
        dml_classification=_APPLICATION_BEHAVIOR_DML,
    )
    forward_blocked = False
    forward_revalidated = False
    forward_revalidation_sqlstate: str | None = None
    forward_executor_role: str | None = None
    forward_application_role: str | None = None
    try:
        with executor_connection.cursor() as executor_cursor:
            forward_executor_posture = _attest_connection_role_posture(
                executor_cursor,
                urls.erasure_role,
            )
            forward_executor_role = _posture_role(forward_executor_posture)
            _set_tenant(executor_cursor, forward_scope.tenant_id)
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
                forward_application_posture = _attest_connection_role_posture(
                    app_cursor,
                    urls.application_role,
                )
                forward_application_role = _posture_role(forward_application_posture)
                _set_worker_lease(app_cursor, forward_scope)
                app_cursor.execute("SET LOCAL lock_timeout = '250ms'")
                _insert_dashboard_batch(app_cursor, forward_graphs)
                app_cursor.execute("SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE")
        except Exception as exc:
            forward_blocked = getattr(exc, "pgcode", None) == "55P03"
            application_connection.rollback()

        with executor_connection.cursor() as executor_cursor:
            _set_tenant(executor_cursor, forward_scope.tenant_id)
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
        urls.application,
        urls.application_role,
        "RACE_REVERSE",
    )
    reverse_bound_graph = _seed_dashboard_batch(
        urls,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
        scope=reverse_scope,
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=dashboard_coverage_row_count,
        financial_coverage_row_count=dashboard_financial_coverage_row_count,
    )
    reverse_graphs = reverse_bound_graph.graphs
    reverse_request_id = _seed_erasure_request(
        urls.application,
        urls.application_role,
        reverse_scope,
    )
    application_connection = _connect_behavior_dml(
        urls.application,
        expected_role=urls.application_role,
        tenant_id=reverse_scope.tenant_id,
        case_coordinate=reverse_scope.case_coordinate,
        operation_prefix="RACE_REVERSE_APPLICATION",
        dml_classification=_APPLICATION_BEHAVIOR_DML,
    )
    executor_connection = _connect_behavior_dml(
        urls.erasure,
        expected_role=urls.erasure_role,
        tenant_id=reverse_scope.tenant_id,
        case_coordinate=reverse_scope.case_coordinate,
        operation_prefix="RACE_REVERSE_ERASURE",
        dml_classification=_ERASURE_BEHAVIOR_DML,
    )
    reverse_blocked = False
    reverse_refused = False
    reverse_application_role: str | None = None
    reverse_executor_role: str | None = None
    try:
        with application_connection.cursor() as app_cursor:
            reverse_application_posture = _attest_connection_role_posture(
                app_cursor,
                urls.application_role,
            )
            reverse_application_role = _posture_role(reverse_application_posture)
            _set_worker_lease(app_cursor, reverse_scope)
            _insert_dashboard_batch(app_cursor, reverse_graphs)
            app_cursor.execute("SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE")
        try:
            with executor_connection.cursor() as executor_cursor:
                reverse_executor_posture = _attest_connection_role_posture(
                    executor_cursor,
                    urls.erasure_role,
                )
                reverse_executor_role = _posture_role(reverse_executor_posture)
                _set_tenant(executor_cursor, reverse_scope.tenant_id)
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
                _set_tenant(executor_cursor, reverse_scope.tenant_id)
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
        _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
    return matches[0]


def _authority_install_statements(row: Mapping[str, Any]) -> tuple[str, ...]:
    statements = list(
        _decode_sql_tuple(
            row.get("body_sql_utf8_hex_tuple"),
            code="TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID",
        )
    )
    guards = row.get("guard_function_acl_binding_tuple")
    if not isinstance(guards, list) or not guards:
        _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
    for guard in guards:
        if not isinstance(guard, Mapping):
            _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
        statements.extend(
            _decode_sql_tuple(
                [guard.get("owner_sql_utf8_hex")],
                code="TASK8_V27_CENSUS_GUARD_ACL_INVALID",
            )
        )
        statements.extend(
            _decode_sql_tuple(
                guard.get("function_acl_sql_utf8_hex_tuple"),
                code="TASK8_V27_CENSUS_GUARD_ACL_INVALID",
            )
        )
        select_sql = guard.get("guard_select_sql_tuple")
        if not isinstance(select_sql, list) or any(
            not isinstance(statement, str) for statement in select_sql
        ):
            _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
        statements.extend(select_sql)
    return tuple(statements)


def _erasure_install_statements(
    authority_execution_bundle: Mapping[str, Any],
) -> tuple[str, ...]:
    erasure = authority_execution_bundle.get("erasure_authority")
    if not isinstance(erasure, Mapping):
        _fail("TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID")
    return (
        *_decode_sql_tuple(
            erasure.get("current_body_sql_utf8_hex_tuple"),
            code="TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID",
        ),
        *_decode_sql_tuple(
            erasure.get("current_acl_sql_utf8_hex_tuple"),
            code="TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID",
        ),
    )


def _apply_admin_authority_ddl(admin_url: str, statements: tuple[str, ...]) -> None:
    """Apply reviewed authority bytes; this connection never observes evidence."""

    allowed_prefixes = (
        "CREATE FUNCTION ",
        "CREATE CONSTRAINT TRIGGER ",
        "DROP FUNCTION ",
        "DROP TRIGGER ",
        "ALTER FUNCTION ",
        "GRANT ",
        "REVOKE ",
    )
    if not statements or any(
        not statement.lstrip().upper().startswith(allowed_prefixes) for statement in statements
    ):
        _fail("TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID")
    connection = _connect_raw(admin_url)
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
        _fail("TASK8_V27_CENSUS_AUTHORITY_CATALOG_INVALID")
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
            observer_posture = _attest_connection_role_posture(
                cursor,
                urls.application_role,
            )
            current_user = _posture_role(observer_posture)

            if authority_id == _AUTHORITY_IDS[1]:
                row = _authority_row(authority_execution_bundle, authority_id)
                guards = row.get("guard_function_acl_binding_tuple")
                if not isinstance(guards, list):
                    _fail("TASK8_V27_CENSUS_GUARD_ACL_INVALID")
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
                    _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
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
                _fail("TASK8_V27_CENSUS_AUTHORITY_BINDING_INVALID")
        connection.rollback()
    finally:
        connection.close()
    payload = {
        "schema_version": "task8-v27-app-observed-authority-catalog-v1",
        "authority_id": authority_id,
        "observer_role": current_user,
        "authority_projection": authority_projection,
    }
    return {
        **payload,
        "catalog_sha256": _canonical_sha256(
            ["task8-v27-app-observed-authority-catalog-v1", payload]
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
        code="TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID",
    )
    partial_up = _authority_install_statements(partial)
    erasure_contract = artifact.get("task8_fdd_contract", {}).get("erasure_refusal_contract")
    if not isinstance(erasure_contract, Mapping):
        _fail("TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure_down = _decode_sql_tuple(
        erasure_contract.get("downgrade_sql_utf8_hex_tuple"),
        code="TASK8_V27_CENSUS_ERASURE_AUTHORITY_INVALID",
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
            _fail("TASK8_V27_CENSUS_AUTHORITY_RESTORATION_INVALID")
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
                    "task8-v27-app-observed-authority-udu-v1",
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
    policy_artifact_raw: bytes,
    workbook_raw: bytes,
    projection_manifest: Mapping[str, Any],
    lock_binding: Mapping[str, Any],
) -> dict[str, Any]:
    dashboard_coverage_row_count = manifest.get("dashboard_coverage_row_count")
    if dashboard_coverage_row_count != 100:
        _fail("TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    dashboard_financial_coverage_row_count = manifest.get("dashboard_financial_coverage_row_count")
    if dashboard_financial_coverage_row_count != 79:
        _fail("TASK8_V27_CENSUS_FINANCIAL_COVERAGE_ROW_COUNT_INVALID")
    main_scope, main_bound, main_dashboard_receipt = _run_main_dashboard_census(
        urls,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
    )
    main_dashboard_census_sha256 = main_dashboard_receipt.get("expected_sha256")
    if not isinstance(main_dashboard_census_sha256, str):
        _fail("TASK8_V27_CENSUS_DASHBOARD_BATCH_INVALID")
    requirement_receipt = _run_dashboard_requirement_matrix(
        urls.application,
        urls=urls,
        expected_application_role=urls.application_role,
        projection_manifest=projection_manifest,
        tenant_id=main_scope.tenant_id,
        deal_id=main_scope.deal_id,
        main_scope=main_scope,
        main_dashboard_census_sha256=main_dashboard_census_sha256,
        artifact=artifact,
        policy_artifact_raw=policy_artifact_raw,
        workbook_raw=workbook_raw,
    )
    case_results = [
        _execute_behavior_case(
            case,
            case_ordinal=ordinal,
            urls=urls,
            projection_manifest=projection_manifest,
            artifact=artifact,
            policy_artifact_raw=policy_artifact_raw,
            workbook_raw=workbook_raw,
            dashboard_coverage_row_count=dashboard_coverage_row_count,
            dashboard_financial_coverage_row_count=(dashboard_financial_coverage_row_count),
        )
        for ordinal, case in enumerate(_case_rows(manifest), start=1)
    ]
    rls_behavior_passed, rls_behavior_results = _run_rls_behavior_matrix(
        urls,
        projection_manifest,
        artifact,
        policy_artifact_raw,
        workbook_raw,
        dashboard_coverage_row_count,
        dashboard_financial_coverage_row_count,
    )
    catalog_connection = _connect(urls.application)
    try:
        with catalog_connection.cursor() as role_cursor:
            catalog_posture = _attest_connection_role_posture(
                role_cursor,
                urls.application_role,
            )
            catalog_observer_role = _posture_role(catalog_posture)
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
        artifact,
        policy_artifact_raw,
        workbook_raw,
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
            "task8-v27-rejected-case-projections-v1",
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
    seed_dml_rows = [dict(row) for row in _fixture_dml_role_rows()]
    dml_ledger_binding = _validate_census_dml_closed_ledger(
        seed_dml_rows,
        application_role=urls.application_role,
        erasure_role=urls.erasure_role,
    )
    pregrant_execution_binding = _pregrant_execution_binding(authority_execution_binding)
    catalog_hash = _canonical_sha256(catalog)
    race_hash = _canonical_sha256(race_results)
    rls_behavior_hash = _canonical_sha256(
        ["task8-v27-rls-behavior-matrix-v1", rls_behavior_results]
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
        **dml_ledger_binding,
        **pregrant_execution_binding,
        "application_seed_dml_count": dml_ledger_binding["application_fixture_dml_count"],
        "fixture_dml_operation_ledger": seed_dml_rows,
        "seed_dml_role_attestation_sha256": dml_ledger_binding[
            "fixture_dml_operation_ledger_sha256"
        ],
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
            [
                "task8-v27-behavior-role-attestation-v2",
                behavior_role_rows,
                observation_role_rows,
                seed_dml_rows,
            ]
        ),
        "dashboard_coverage_row_count": dashboard_coverage_row_count,
        "dashboard_commercial_coverage_row_count": (
            dashboard_coverage_row_count - dashboard_financial_coverage_row_count
        ),
        "dashboard_financial_coverage_row_count": dashboard_financial_coverage_row_count,
        "dashboard_seed_plan_sha256": main_bound.dashboard_seed_plan_sha256,
        "dashboard_seed_db_projection_sha256": (main_bound.dashboard_seed_db_projection_sha256),
        "seed_fk_closure_sha256": main_bound.seed_fk_closure_sha256,
        "seed_native_helper_equality_sha256": (main_bound.seed_native_helper_equality_sha256),
        "source_freeze_sha256": main_bound.source_freeze_sha256,
        "task5_roundtrip_sha256": main_bound.task5_roundtrip_sha256,
        "main_dashboard_census_sha256": main_dashboard_receipt["expected_sha256"],
        "requirement_matrix_sha256": requirement_receipt["expected_sha256"],
        "requirement_predicate_sha256": requirement_receipt["predicate_sha256"],
        "requirement_legal_case_count": requirement_receipt["legal_case_count"],
        "requirement_negative_case_count": requirement_receipt["negative_case_count"],
        "requirement_negative_zero_mutation_count": requirement_receipt[
            "negative_zero_mutation_count"
        ],
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
    }
    execution["complete_execution_sha256"] = complete_execution_sha256(
        _complete_execution_evidence(execution)
    )
    expected_catalog = manifest["catalog_assertions"]
    for field, observed in catalog.items():
        if field not in expected_catalog or observed != expected_catalog[field]:
            _fail("TASK8_V27_CENSUS_CATALOG_INVALID")
    if not forward_passed or not reverse_passed:
        _fail("TASK8_V27_CENSUS_LOCK_RACE_INVALID")
    if not rls_behavior_passed:
        _fail("TASK8_V27_CENSUS_RLS_BEHAVIOR_INVALID")
    if behavior_role_match_count != len(case_results):
        _fail("TASK8_V27_CENSUS_BEHAVIOR_ROLE_INVALID")
    if rejected_zero_mutation_count != len(rejected_results) or not dashboard_refusal_zero_mutation:
        _fail("TASK8_V27_CENSUS_REJECTION_MUTATED")
    return execution


def _run_disposable_postgres_census_impl(
    admin_database_url: str,
    application_database_url: str,
    erasure_database_url: str,
    artifact_raw: bytes,
    fixture_raw: bytes,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
    workbook_raw: bytes,
) -> dict[str, Any]:
    _reset_fixture_uuid_sequence()
    artifact, _fixture = validate_census_input_bytes(
        artifact_raw=artifact_raw,
        fixture_raw=fixture_raw,
        parent_artifact_raw=parent_artifact_raw,
        policy_artifact_raw=policy_artifact_raw,
        reviewer_map_raw=reviewer_map_raw,
    )
    if not isinstance(policy_artifact_raw, bytes) or not isinstance(workbook_raw, bytes):
        _fail("TASK8_V27_CENSUS_POLICY_INVALID")
    manifest = build_census_case_manifest(artifact)
    validate_census_case_manifest(manifest, artifact)
    migration_snapshot = _capture_migration_source_snapshot()
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
            migration_snapshot=migration_snapshot,
            application_role=urls.application_role,
            erasure_role=urls.erasure_role,
            v27=artifact,
            authority_execution=authority_execution,
        )
        execution = _run_database_execution(
            urls,
            manifest,
            authority_execution_binding=authority_execution_binding,
            authority_execution_bundle=authority_execution,
            artifact=artifact,
            policy_artifact_raw=policy_artifact_raw,
            workbook_raw=workbook_raw,
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
        "pipeline_contract_version": "full-strict-v27",
        "artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "manifest_sha256": _canonical_sha256(manifest),
        "database_execution": execution,
        "disposable_database_dropped": dropped,
    }
    validate_census_receipt(receipt, artifact)
    return receipt


def run_disposable_postgres_census(
    admin_database_url: str,
    application_database_url: str,
    erasure_database_url: str,
    artifact_raw: bytes,
    fixture_raw: bytes,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
    workbook_raw: bytes,
) -> dict[str, Any]:
    """Run the census behind a code-only, no-bytecode public boundary."""

    failure_code: str | None = None
    try:
        with _suppress_bytecode_writes():
            return _run_disposable_postgres_census_impl(
                admin_database_url,
                application_database_url,
                erasure_database_url,
                artifact_raw,
                fixture_raw,
                parent_artifact_raw,
                policy_artifact_raw,
                reviewer_map_raw,
                workbook_raw,
            )
    except Task8V27CensusError as exc:
        candidate_code = str(exc)
        failure_code = (
            candidate_code
            if candidate_code in _PUBLIC_ERROR_CODES
            else "TASK8_V27_CENSUS_UNEXPECTED"
        )
    except Exception:
        failure_code = "TASK8_V27_CENSUS_UNEXPECTED"
    raise Task8V27CensusError(failure_code) from None


def validate_census_receipt(
    receipt: Mapping[str, Any],
    v27: Mapping[str, Any],
) -> None:
    _validate_v27(v27)
    if tuple(receipt) != (
        "schema_version",
        "result",
        "pipeline_contract_version",
        "artifact_sha256",
        "manifest_sha256",
        "database_execution",
        "disposable_database_dropped",
    ):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    if (
        receipt["schema_version"] != _RECEIPT_SCHEMA
        or receipt["result"] != "PASS"
        or receipt["pipeline_contract_version"] != "full-strict-v27"
        or receipt["disposable_database_dropped"] is not True
    ):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    manifest = build_census_case_manifest(v27)
    if receipt["manifest_sha256"] != _canonical_sha256(manifest):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    canonical_artifact_raw = (
        json.dumps(
            dict(v27),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    if receipt["artifact_sha256"] != hashlib.sha256(canonical_artifact_raw).hexdigest():
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    execution = receipt.get("database_execution")
    if not isinstance(execution, dict):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
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
        build_hash_bound_authority_execution(v27),
    )
    observed_authority_binding = execution.get("authority_execution_binding")
    if not isinstance(observed_authority_binding, Mapping):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    if any(
        observed_authority_binding.get(key) != value
        for key, value in expected_authority_binding.items()
    ):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    try:
        derived_pregrant_execution_binding = _pregrant_execution_binding(observed_authority_binding)
    except Task8V27CensusError:
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    pregrant_catalog = observed_authority_binding.get("pregrant_application_catalog")
    privilege_boundary = observed_authority_binding.get("application_privilege_boundary")
    if not isinstance(pregrant_catalog, Mapping) or not isinstance(privilege_boundary, Mapping):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    pregrant_payload = {
        key: value for key, value in pregrant_catalog.items() if key != "expected_sha256"
    }
    privilege_payload = {
        key: value for key, value in privilege_boundary.items() if key != "expected_sha256"
    }
    application_role = pregrant_catalog.get("application_role")
    expected_pregrant = _pregrant_application_catalog_payload()
    if (
        not isinstance(application_role, str)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", application_role) is None
        or any(pregrant_catalog.get(key) != value for key, value in expected_pregrant.items())
        or pregrant_catalog.get("observed_before_any_grant") is not True
        or pregrant_catalog.get("expected_sha256")
        != _canonical_sha256(["task8-v27-pregrant-application-catalog-v1", pregrant_payload])
        or privilege_boundary.get("pregrant_catalog_sha256")
        != pregrant_catalog.get("expected_sha256")
        or privilege_boundary.get("granted_application_relation_count") != 66
        or privilege_boundary.get("granted_application_relation_tuple_sha256")
        != _APPLICATION_RELATION_TUPLE_SHA256
        or privilege_boundary.get("excluded_application_relation_tuple")
        != list(_EXCLUDED_APPLICATION_RELATION_TUPLE)
        or privilege_boundary.get("excluded_application_direct_privilege_count") != 0
        or privilege_boundary.get("excluded_application_effective_privilege_count") != 0
        or privilege_boundary.get("verified_by_production_catalog_predicate") is not True
        or privilege_boundary.get("expected_sha256")
        != _canonical_sha256(["task8-v27-application-privilege-boundary-v1", privilege_payload])
    ):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    fixture_dml_ledger = execution.get("fixture_dml_operation_ledger")
    if not isinstance(fixture_dml_ledger, list) or not all(
        isinstance(row, dict) for row in fixture_dml_ledger
    ):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    erasure_roles = {
        str(row.get("expected_role"))
        for row in fixture_dml_ledger
        if row.get("dml_classification") == _ERASURE_BEHAVIOR_DML
    }
    if (
        len(erasure_roles) != 1
        or any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role) is None for role in erasure_roles)
        or application_role in erasure_roles
    ):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    derived_dml_binding = _validate_census_dml_closed_ledger(
        fixture_dml_ledger,
        application_role=application_role,
        erasure_role=next(iter(erasure_roles)),
    )
    if (
        execution.get("admin_behavior_case_count") != 0
        or execution.get("admin_observation_case_count") != 0
        or any(
            execution.get(key) != value for key, value in derived_pregrant_execution_binding.items()
        )
        or any(execution.get(key) != value for key, value in derived_dml_binding.items())
        or execution.get("application_seed_dml_count")
        != derived_dml_binding["application_fixture_dml_count"]
        or execution.get("seed_dml_role_attestation_sha256")
        != derived_dml_binding["fixture_dml_operation_ledger_sha256"]
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
        or execution.get("requirement_legal_case_count") != 4
        or execution.get("requirement_negative_case_count") != 13
        or execution.get("requirement_negative_zero_mutation_count") != 13
        or execution.get("dashboard_refusal_sqlstate") != _REFUSAL_SQLSTATE
        or execution.get("dashboard_refusal_reason_code") != _REFUSAL_CODE
        or execution.get("dashboard_refusal_zero_mutation") is not True
        or execution.get("forward_lock_race_passed") is not True
        or execution.get("reverse_lock_race_passed") is not True
        or execution.get("rls_behavior_passed") is not True
        or execution.get("installed_tenant_rls_table_count") != 66
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
        or execution.get("advisory_lock_binding") != build_advisory_lock_binding(v27)
    ):
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    for field in (
        "rejected_case_projection_sha256",
        "behavior_role_attestation_sha256",
        "seed_dml_role_attestation_sha256",
        "fixture_dml_operation_ledger_sha256",
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
        "dashboard_seed_plan_sha256",
        "dashboard_seed_db_projection_sha256",
        "seed_fk_closure_sha256",
        "seed_native_helper_equality_sha256",
        "source_freeze_sha256",
        "task5_roundtrip_sha256",
        "main_dashboard_census_sha256",
        "requirement_matrix_sha256",
        "requirement_predicate_sha256",
        "complete_execution_sha256",
    ):
        if not isinstance(execution.get(field), str) or not _SHA256_RE.fullmatch(execution[field]):
            _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
    expected_complete_execution_sha256 = complete_execution_sha256(
        _complete_execution_evidence(execution)
    )
    if execution["complete_execution_sha256"] != expected_complete_execution_sha256:
        _fail("TASK8_V27_CENSUS_RECEIPT_INVALID")
