"""Typed, fail-closed Task 2 dependency admission.

Probe adapters are deliberately injected.  This module interprets typed observations
and never opens a socket, database connection, or object-store handle on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeVar

from idis.runtime.strict_auth import validate_strict_auth_binding
from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV,
    NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION,
    NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION,
    NO_SUBSTANTIVE_CONTENT_VARIANTS,
)
from idis.services.ingestion.password_manifest import (
    DOCUMENT_PASSWORD_MANIFEST_PATH_ENV,
    DocumentPasswordManifestStatus,
)

_BUDGET_COMPONENTS = frozenset(
    {
        "anthropic",
        "openai_embeddings",
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
    }
)

_COMPONENT_ORDER = (
    "postgres",
    "erasure_executor",
    "audit",
    "idempotency",
    "redis",
    "object_store",
    "ingestion",
    "auth",
    "worker",
    "budgets",
)
_TASK4_INGESTION_COMPONENT_ORDER = (
    "postgres",
    "audit",
    "idempotency",
    "redis",
    "object_store",
    "ingestion",
    "auth",
)

FULL_DEPENDENCY_ADMISSION_SCOPE = "full-v1"
TASK4_INGESTION_ADMISSION_SCOPE = "task4-ingestion-v1"

_APPROVED_INGEST_MAX_BYTES = 536_870_912
_APPROVED_OCR_MAX_PAGES = 72
_APPROVED_OCR_TIMEOUT_SECONDS = 120
_APPROVED_OCR_DPI = 200

TASK7_V21_CURRENT_ERASURE_RELATIONS: Final = (
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
TASK7_V21_FUTURE_ERASURE_RELATIONS: Final = (
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
TASK7_V21_COMPLETE_ERASURE_RELATIONS: Final = (
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
TASK7_V21_CURRENT_GUARD_ROUTINE_POSTURES: Final[tuple[tuple[str, str, tuple[str, str]], ...]] = (
    (
        "public.idis_task5_coverage_scope_guard()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task6_immutable_guard()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_canonical_jsonb_text_v1(jsonb)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_append_only_lease_v1()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_input_binding_admission_v1()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_output_bound_v1()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_output_exact_one_v1()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_receipt_equality_v1()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_run_steps_contract_immutable_v1()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_runs_contract_immutable_v1()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_source_bound_v1()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_current_source_exact_one_v1()",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_expected_edge_admission_is_valid_v1(jsonb,uuid,uuid,uuid)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_hash_jsonb_v1(jsonb)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_manifest_reference_digest_v1("
        "uuid,uuid,uuid,text,text,uuid,text,integer,uuid,uuid,text,text,text)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_sha1_v1(bytea)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_source_canonical_jsonb_text_v1(jsonb)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_source_hash_jsonb_v1(jsonb)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_source_projection_v1(uuid)",
        "DEFINER",
        ("pg_catalog", "pg_temp"),
    ),
    (
        "public.idis_task7_uuidv5_v1(uuid,text)",
        "INVOKER",
        ("pg_catalog", "pg_temp"),
    ),
)
TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES: Final = tuple(
    posture[0] for posture in TASK7_V21_CURRENT_GUARD_ROUTINE_POSTURES
)
TASK7_V21_FUTURE_GUARD_ROUTINE_SIGNATURES: Final = (
    "public.idis_task7_analysis_muhasabah_exact_one_v1()",
    "public.idis_task7_approved_erasure_append_only_v1()",
    "public.idis_task7_dashboard_root_equation_v1()",
    "public.idis_task7_debate_muhasabah_exact_one_v1()",
    "public.idis_task7_extract_rag_context_coordinate_v1()",
    "public.idis_task7_generic_claim_projection_v1()",
    "public.idis_task7_generic_evidence_projection_v1()",
    "public.idis_task7_grading_dispatch_and_consistency_v1()",
    "public.idis_task7_identity_identifier_projection_v1()",
    "public.idis_task7_layer2_muhasabah_exact_one_v1()",
    "public.idis_task7_native_root_exact_one_v1()",
    "public.idis_task7_rag_query_parent_coordinate_v1()",
    "public.idis_task7_scoring_binary64_negative_zero_v1()",
    "public.idis_task7_scoring_dimension_projection_v1()",
)
_TASK7_V21_GUARD_OWNER_ROUTINE_COUNT: Final = len(TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES)
_TASK7_V21_GUARD_OWNER_COLUMN_ACL_RELATION_COUNT: Final = 36
_TASK7_V21_GUARD_OWNER_COLUMN_ACL_COUNT: Final = 424
_TASK7_V21_GUARD_OWNER_COLUMN_ACL_SHA256: Final = (
    "0e15aa84b19ba4f63f95001049702b54279f9d1ffebf984394979c602b271da6"
)
_TASK7_V21_CURRENT_ERASURE_STATE: Final = "CURRENT48_ACTIVE_FUTURE38_FROZEN"
_TASK7_V21_ERASURE_OWNER_UPDATE_COLUMNS: Final = (
    "request_id",
    "status",
    "executed_by",
    "executed_at",
    "counts",
)


def task7_v21_erasure_runner_split_is_valid() -> bool:
    """Fail closed if the frozen CURRENT48/FUTURE38/RUNNER86 split drifts."""

    current = TASK7_V21_CURRENT_ERASURE_RELATIONS
    future = TASK7_V21_FUTURE_ERASURE_RELATIONS
    complete = TASK7_V21_COMPLETE_ERASURE_RELATIONS
    current_set = frozenset(current)
    future_set = frozenset(future)
    complete_set = frozenset(complete)
    return (
        len(current) == len(current_set) == 48
        and len(future) == len(future_set) == 38
        and len(complete) == len(complete_set) == 86
        and current_set.isdisjoint(future_set)
        and current_set | future_set == complete_set
        and tuple(relation for relation in complete if relation in current_set) == current
    )


# Import-compatible immutable aliases preserve frozen-v20 and frozen-v18 evidence
# tests. Active readiness and probe composition below consume only the v21 names.
TASK7_V20_CURRENT_ERASURE_RELATIONS: Final = TASK7_V21_CURRENT_ERASURE_RELATIONS
TASK7_V20_FUTURE_ERASURE_RELATIONS: Final = TASK7_V21_FUTURE_ERASURE_RELATIONS
TASK7_V20_COMPLETE_ERASURE_RELATIONS: Final = TASK7_V21_COMPLETE_ERASURE_RELATIONS
TASK7_V20_CURRENT_GUARD_ROUTINE_POSTURES: Final = TASK7_V21_CURRENT_GUARD_ROUTINE_POSTURES
TASK7_V20_CURRENT_GUARD_ROUTINE_SIGNATURES: Final = TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES
TASK7_V20_FUTURE_GUARD_ROUTINE_SIGNATURES: Final = TASK7_V21_FUTURE_GUARD_ROUTINE_SIGNATURES


def task7_v20_erasure_runner_split_is_valid() -> bool:
    """Validate immutable frozen-v20 aliases without selecting them."""

    return task7_v21_erasure_runner_split_is_valid()


TASK7_V18_CURRENT_ERASURE_RELATIONS: Final = TASK7_V20_CURRENT_ERASURE_RELATIONS
TASK7_V18_FUTURE_ERASURE_RELATIONS: Final = TASK7_V20_FUTURE_ERASURE_RELATIONS
TASK7_V18_COMPLETE_ERASURE_RELATIONS: Final = TASK7_V20_COMPLETE_ERASURE_RELATIONS
TASK7_V18_CURRENT_GUARD_ROUTINE_POSTURES: Final = TASK7_V20_CURRENT_GUARD_ROUTINE_POSTURES
TASK7_V18_CURRENT_GUARD_ROUTINE_SIGNATURES: Final = TASK7_V20_CURRENT_GUARD_ROUTINE_SIGNATURES
TASK7_V18_FUTURE_GUARD_ROUTINE_SIGNATURES: Final = TASK7_V20_FUTURE_GUARD_ROUTINE_SIGNATURES


def task7_v18_erasure_runner_split_is_valid() -> bool:
    """Validate the immutable frozen-v18 aliases without selecting them."""

    current = TASK7_V18_CURRENT_ERASURE_RELATIONS
    future = TASK7_V18_FUTURE_ERASURE_RELATIONS
    complete = TASK7_V18_COMPLETE_ERASURE_RELATIONS
    current_set = frozenset(current)
    future_set = frozenset(future)
    complete_set = frozenset(complete)
    return (
        len(current) == len(current_set) == 48
        and len(future) == len(future_set) == 38
        and len(complete) == len(complete_set) == 86
        and current_set.isdisjoint(future_set)
        and current_set | future_set == complete_set
        and tuple(relation for relation in complete if relation in current_set) == current
    )


@dataclass(frozen=True, slots=True)
class PostgresProbeFacts:
    connected: bool
    schema_at_head: bool
    app_role_superuser: bool
    app_role_bypass_rls: bool
    forced_rls: bool
    tenant_context_ok: bool
    cross_tenant_denied: bool


@dataclass(frozen=True, slots=True)
class ErasureDatabaseTargetFacts:
    """Secret-free identity of one connected PostgreSQL target."""

    database_name: str
    server_address: str
    server_port: int


@dataclass(frozen=True, slots=True)
class ErasureRoleFacts:
    """Catalog posture for one fixed erasure authority role."""

    role_name: str
    can_login: bool
    superuser: bool
    create_database: bool
    create_role: bool
    inherit: bool
    bypass_rls: bool
    replication: bool = False


@dataclass(frozen=True, slots=True)
class ErasureFunctionOwnerUpdateAclFacts:
    """Exact deployed UPDATE authority of the dedicated erasure function owner."""

    relation_tuple: tuple[str, ...]
    column_update_acl_count: int
    ordered_column_tuple: tuple[str, ...]
    grantable_acl_count: int
    table_update_privilege_count: int


@dataclass(frozen=True, slots=True)
class Task7GuardOwnerProbeFacts:
    """Catalog proof for the frozen v21 least-privilege guard owner."""

    role: ErasureRoleFacts | None
    membership_count: int
    owned_relation_count: int
    owned_routine_count: int
    schema_usage: bool
    schema_create: bool
    schema_direct_usage_acl_count: int
    schema_unexpected_acl_count: int
    table_select_privilege_count: int
    table_insert_privilege_count: int
    table_update_privilege_count: int
    table_delete_privilege_count: int
    table_truncate_privilege_count: int
    table_references_privilege_count: int
    table_trigger_privilege_count: int
    column_select_acl_relation_count: int
    column_select_acl_count: int
    column_select_acl_sha256: str
    force_rls_relation_count: int
    current_routine_signatures: tuple[str, ...]
    current_routine_posture_mismatch_count: int
    application_direct_execute_count: int
    public_direct_execute_count: int
    unexpected_execute_grant_count: int
    external_helper_count: int
    external_helper_signature: str
    external_helper_owner: str
    external_helper_security_definer: bool
    external_helper_search_path: tuple[str, ...]
    external_helper_guard_owner_execute: bool
    external_helper_application_execute: bool
    external_helper_public_execute: bool
    external_helper_unexpected_execute_grant_count: int
    future_routine_signatures_present: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Task7ErasureRunnerProbeFacts:
    """Non-mutating catalog projection of the active Task-7 erasure runner."""

    activation_state: str
    delete_relation_tuple: tuple[str, ...]
    result_relation_tuple: tuple[str, ...]
    future_relation_tuple_present: tuple[str, ...]


_DEFAULT_TASK7_GUARD_OWNER_FACTS: Final = Task7GuardOwnerProbeFacts(
    role=ErasureRoleFacts(
        role_name="idis_task7_guard_function_owner",
        can_login=False,
        superuser=False,
        create_database=False,
        create_role=False,
        inherit=False,
        bypass_rls=False,
        replication=False,
    ),
    membership_count=0,
    owned_relation_count=0,
    owned_routine_count=_TASK7_V21_GUARD_OWNER_ROUTINE_COUNT,
    schema_usage=True,
    schema_create=False,
    schema_direct_usage_acl_count=1,
    schema_unexpected_acl_count=0,
    table_select_privilege_count=0,
    table_insert_privilege_count=0,
    table_update_privilege_count=0,
    table_delete_privilege_count=0,
    table_truncate_privilege_count=0,
    table_references_privilege_count=0,
    table_trigger_privilege_count=0,
    column_select_acl_relation_count=(_TASK7_V21_GUARD_OWNER_COLUMN_ACL_RELATION_COUNT),
    column_select_acl_count=_TASK7_V21_GUARD_OWNER_COLUMN_ACL_COUNT,
    column_select_acl_sha256=_TASK7_V21_GUARD_OWNER_COLUMN_ACL_SHA256,
    force_rls_relation_count=_TASK7_V21_GUARD_OWNER_COLUMN_ACL_RELATION_COUNT,
    current_routine_signatures=TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES,
    current_routine_posture_mismatch_count=0,
    application_direct_execute_count=0,
    public_direct_execute_count=0,
    unexpected_execute_grant_count=0,
    external_helper_count=1,
    external_helper_signature="public.idis_task7_approved_erasure_context_v1(uuid,uuid)",
    external_helper_owner="idis_erasure_function_owner",
    external_helper_security_definer=True,
    external_helper_search_path=("pg_catalog", "pg_temp"),
    external_helper_guard_owner_execute=True,
    external_helper_application_execute=False,
    external_helper_public_execute=False,
    external_helper_unexpected_execute_grant_count=0,
    future_routine_signatures_present=(),
)
_DEFAULT_TASK7_ERASURE_RUNNER_FACTS: Final = Task7ErasureRunnerProbeFacts(
    activation_state=_TASK7_V21_CURRENT_ERASURE_STATE,
    delete_relation_tuple=TASK7_V21_CURRENT_ERASURE_RELATIONS,
    result_relation_tuple=TASK7_V21_CURRENT_ERASURE_RELATIONS,
    future_relation_tuple_present=(),
)


@dataclass(frozen=True, slots=True)
class ErasureExecutorProbeFacts:
    """Catalog and connection observations for the dedicated erasure authority."""

    connected: bool
    authenticated_role: str
    effective_role: str
    executor_target: ErasureDatabaseTargetFacts | None
    app_target: ErasureDatabaseTargetFacts | None
    app_role: str
    app_effective_role: str
    executor_role: ErasureRoleFacts | None
    owner_role: ErasureRoleFacts | None
    membership_count: int
    executor_table_privilege_count: int
    executor_column_privilege_count: int
    executor_schema_usage: bool
    executor_schema_create: bool
    executor_schema_direct_acl_count: int
    executor_other_direct_execute_count: int
    function_count: int
    function_schema: str
    function_name: str
    function_identity_arguments: tuple[str, ...]
    function_result_type: str
    function_owner: str
    function_security_definer: bool
    function_search_path: tuple[str, ...]
    executor_has_execute: bool
    public_has_execute: bool
    app_role_has_execute: bool
    unexpected_execute_grant_count: int
    function_owner_update_acl: ErasureFunctionOwnerUpdateAclFacts
    # Defaults preserve compatibility for older injected test probes. The
    # production collector always replaces both values with catalog observations.
    guard_owner: Task7GuardOwnerProbeFacts = _DEFAULT_TASK7_GUARD_OWNER_FACTS
    runner_contract: Task7ErasureRunnerProbeFacts = _DEFAULT_TASK7_ERASURE_RUNNER_FACTS


@dataclass(frozen=True, slots=True)
class AuditProbeFacts:
    backend: str
    emit_ok: bool
    read_ok: bool
    restart_read_ok: bool


@dataclass(frozen=True, slots=True)
class IdempotencyProbeFacts:
    backend: str
    put_ok: bool
    replay_ok: bool
    conflict_ok: bool
    restart_replay_ok: bool
    tenant_isolation_ok: bool


@dataclass(frozen=True, slots=True)
class RedisProbeFacts:
    backend: str
    ping_ok: bool
    atomic_bucket_ok: bool
    cross_process_ok: bool


@dataclass(frozen=True, slots=True)
class ObjectStoreProbeFacts:
    backend: str
    write_ok: bool
    read_ok: bool
    hash_ok: bool
    delete_ok: bool
    restart_read_ok: bool


@dataclass(frozen=True, slots=True)
class IngestionProbeFacts:
    """Offline observations for the bounded spool-to-object-store path."""

    backend: str
    put_stream_supported: bool
    bounded_read_ok: bool
    stream_write_ok: bool
    stream_read_ok: bool
    stream_hash_ok: bool
    stream_cleanup_ok: bool
    staging_write_ok: bool
    staging_cleanup_ok: bool
    staging_free_bytes: int
    object_store_free_bytes: int
    shared_filesystem: bool
    password_manifest_status: str
    no_substantive_content_attestation_status: str = "not_configured"
    no_substantive_content_attestation_entry_count: int = 0
    no_substantive_content_attestation_schema_version: str | None = None
    no_substantive_content_attestation_disposition: str | None = None
    no_substantive_content_attestation_variant: str | None = None
    no_substantive_content_attestation_payload_sha256: str | None = None
    no_substantive_content_attestation_envelope_sha256: str | None = None
    no_substantive_content_attestation_public_key_sha256: str | None = None
    no_substantive_content_attestation_manifest_sha256: str | None = None
    no_substantive_content_attestation_classifier_policy_sha256: str | None = None
    no_substantive_content_attestation_exemplar_envelope_sha256: str | None = None
    no_substantive_content_attestation_exemplar_canonical_bitmap_sha256: str | None = None
    no_substantive_content_attestation_prescan_receipt_sha256: str | None = None
    no_substantive_content_attestation_candidate_set_sha256: str | None = None
    no_substantive_content_attestation_sample_set_sha256: str | None = None
    no_substantive_content_attestation_batch_envelope_sha256: str | None = None
    no_substantive_content_attestation_manual_count: int = 0
    no_substantive_content_attestation_watermark_candidate_count: int = 0
    no_substantive_content_attestation_runtime_unseen_count: int = 0


@dataclass(frozen=True, slots=True)
class WorkerProbeFacts:
    backend: str
    queued_row_ok: bool
    claim_ok: bool
    fencing_ok: bool
    claim_audit_ok: bool
    heartbeat_ok: bool
    reclaim_ok: bool
    fresh_claim_ok: bool
    stale_write_rejected: bool
    boundary_rollback_ok: bool
    no_residue: bool


@dataclass(frozen=True, slots=True)
class BudgetProbeFacts:
    backend: str
    policy_loaded: bool
    policy_currency: str
    configured_provider_ids: frozenset[str]
    token_caps_present: bool
    call_reservation_ok: bool
    usd_reservation_ok: bool
    consume_ok: bool
    refund_ok: bool
    restart_read_ok: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "configured_provider_ids", frozenset(self.configured_provider_ids))


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    component: str
    may_proceed: bool
    code: str | None


@dataclass(frozen=True, slots=True)
class DependencyAdmissionReport:
    checks: tuple[ReadinessCheck, ...]
    may_proceed: bool
    blocker_codes: tuple[str, ...]
    admission_scope: str = FULL_DEPENDENCY_ADMISSION_SCOPE
    excluded_components: tuple[str, ...] = ()


_FactsT = TypeVar("_FactsT")


def _allowed(component: str) -> ReadinessCheck:
    return ReadinessCheck(component=component, may_proceed=True, code=None)


def _blocked(component: str, code: str) -> ReadinessCheck:
    return ReadinessCheck(component=component, may_proceed=False, code=code)


def _observe(probe: Callable[[], _FactsT]) -> _FactsT | None:
    try:
        return probe()
    except Exception:
        # Exception text may contain a DSN, key, or object path.  Admission uses only
        # the stable component code selected by the caller.
        return None


def _positive_int(value: str | None) -> int | None:
    try:
        parsed = int(value or "")
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def check_postgres_readiness(
    *,
    env: Mapping[str, str],
    probe: Callable[[], PostgresProbeFacts],
) -> ReadinessCheck:
    database_url = env.get("IDIS_DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgresql+")):
        return _blocked("postgres", "DATABASE_UNAVAILABLE")
    facts = _observe(probe)
    if not isinstance(facts, PostgresProbeFacts) or not facts.connected:
        return _blocked("postgres", "DATABASE_UNAVAILABLE")
    if not facts.schema_at_head:
        return _blocked("postgres", "DATABASE_SCHEMA_NOT_READY")
    if (
        facts.app_role_superuser
        or facts.app_role_bypass_rls
        or not facts.forced_rls
        or not facts.tenant_context_ok
        or not facts.cross_tenant_denied
    ):
        return _blocked("postgres", "RLS_HEALTH_BLOCKED")
    return _allowed("postgres")


def check_erasure_executor_readiness(
    *,
    env: Mapping[str, str],
    expected_role: str,
    probe: Callable[[], ErasureExecutorProbeFacts],
) -> ReadinessCheck:
    """Prove the reviewed dedicated erasure credential and callable boundary.

    Worker and governance processes are deliberately outside this authority
    boundary.  Their admission proves absence without touching the credential.
    """

    configured_url = str(env.get("IDIS_ERASURE_DATABASE_URL", "")).strip()
    if expected_role not in {"migrate", "api", "gate"}:
        if configured_url:
            return _blocked("erasure_executor", "ERASURE_EXECUTOR_CONFIG_BLOCKED")
        return _allowed("erasure_executor")
    if not configured_url.startswith(("postgresql://", "postgresql+")):
        return _blocked("erasure_executor", "ERASURE_EXECUTOR_CONFIG_BLOCKED")

    facts = _observe(probe)
    if not isinstance(facts, ErasureExecutorProbeFacts) or not facts.connected:
        return _blocked("erasure_executor", "ERASURE_EXECUTOR_POSTURE_BLOCKED")
    if facts.executor_target is None or facts.app_target is None:
        return _blocked("erasure_executor", "ERASURE_EXECUTOR_POSTURE_BLOCKED")
    if facts.executor_target != facts.app_target:
        return _blocked("erasure_executor", "ERASURE_EXECUTOR_DATABASE_MISMATCH")

    executor_role = facts.executor_role
    owner_role = facts.owner_role
    exact_executor_posture = executor_role == ErasureRoleFacts(
        role_name="idis_erasure_executor",
        can_login=True,
        superuser=False,
        create_database=False,
        create_role=False,
        inherit=False,
        bypass_rls=False,
        replication=False,
    )
    exact_owner_posture = owner_role == ErasureRoleFacts(
        role_name="idis_erasure_function_owner",
        can_login=False,
        superuser=False,
        create_database=False,
        create_role=False,
        inherit=False,
        bypass_rls=False,
        replication=False,
    )
    guard_owner = facts.guard_owner
    exact_guard_owner_posture = guard_owner.role == ErasureRoleFacts(
        role_name="idis_task7_guard_function_owner",
        can_login=False,
        superuser=False,
        create_database=False,
        create_role=False,
        inherit=False,
        bypass_rls=False,
        replication=False,
    )
    function_owner_update_acl = facts.function_owner_update_acl
    count_values = (
        facts.membership_count,
        facts.executor_table_privilege_count,
        facts.executor_column_privilege_count,
        facts.executor_schema_direct_acl_count,
        facts.executor_other_direct_execute_count,
        facts.function_count,
        facts.unexpected_execute_grant_count,
        guard_owner.membership_count,
        guard_owner.owned_relation_count,
        guard_owner.owned_routine_count,
        guard_owner.schema_direct_usage_acl_count,
        guard_owner.schema_unexpected_acl_count,
        guard_owner.table_select_privilege_count,
        guard_owner.table_insert_privilege_count,
        guard_owner.table_update_privilege_count,
        guard_owner.table_delete_privilege_count,
        guard_owner.table_truncate_privilege_count,
        guard_owner.table_references_privilege_count,
        guard_owner.table_trigger_privilege_count,
        guard_owner.column_select_acl_relation_count,
        guard_owner.column_select_acl_count,
        guard_owner.force_rls_relation_count,
        guard_owner.current_routine_posture_mismatch_count,
        guard_owner.application_direct_execute_count,
        guard_owner.public_direct_execute_count,
        guard_owner.unexpected_execute_grant_count,
        guard_owner.external_helper_count,
        guard_owner.external_helper_unexpected_execute_grant_count,
        function_owner_update_acl.column_update_acl_count,
        function_owner_update_acl.grantable_acl_count,
        function_owner_update_acl.table_update_privilege_count,
    )
    exact_function_shape = (
        type(facts.function_count) is int
        and facts.function_count == 1
        and facts.function_schema == "public"
        and facts.function_name == "idis_task7_execute_approved_erasure"
        and facts.function_identity_arguments == ("uuid", "uuid", "uuid")
        and facts.function_result_type == "jsonb"
        and facts.function_owner == "idis_erasure_function_owner"
        and facts.function_security_definer is True
        and facts.function_search_path == ("pg_catalog", "pg_temp")
    )
    exact_guard_owner_shape = (
        exact_guard_owner_posture
        and guard_owner.membership_count == 0
        and guard_owner.owned_relation_count == 0
        and guard_owner.owned_routine_count == _TASK7_V21_GUARD_OWNER_ROUTINE_COUNT
        and guard_owner.schema_usage is True
        and guard_owner.schema_create is False
        and guard_owner.schema_direct_usage_acl_count == 1
        and guard_owner.schema_unexpected_acl_count == 0
        and guard_owner.table_select_privilege_count == 0
        and guard_owner.table_insert_privilege_count == 0
        and guard_owner.table_update_privilege_count == 0
        and guard_owner.table_delete_privilege_count == 0
        and guard_owner.table_truncate_privilege_count == 0
        and guard_owner.table_references_privilege_count == 0
        and guard_owner.table_trigger_privilege_count == 0
        and guard_owner.column_select_acl_relation_count
        == _TASK7_V21_GUARD_OWNER_COLUMN_ACL_RELATION_COUNT
        and guard_owner.column_select_acl_count == _TASK7_V21_GUARD_OWNER_COLUMN_ACL_COUNT
        and guard_owner.column_select_acl_sha256 == _TASK7_V21_GUARD_OWNER_COLUMN_ACL_SHA256
        and guard_owner.force_rls_relation_count == _TASK7_V21_GUARD_OWNER_COLUMN_ACL_RELATION_COUNT
        and guard_owner.current_routine_signatures == TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES
        and guard_owner.current_routine_posture_mismatch_count == 0
        and guard_owner.external_helper_count == 1
        and guard_owner.external_helper_signature
        == "public.idis_task7_approved_erasure_context_v1(uuid,uuid)"
        and guard_owner.external_helper_owner == "idis_erasure_function_owner"
        and guard_owner.external_helper_security_definer is True
        and guard_owner.external_helper_search_path == ("pg_catalog", "pg_temp")
        and not guard_owner.future_routine_signatures_present
    )
    exact_function_owner_update_acl = (
        function_owner_update_acl.relation_tuple == ("erasure_requests",)
        and function_owner_update_acl.column_update_acl_count
        == len(_TASK7_V21_ERASURE_OWNER_UPDATE_COLUMNS)
        and function_owner_update_acl.ordered_column_tuple
        == _TASK7_V21_ERASURE_OWNER_UPDATE_COLUMNS
        and function_owner_update_acl.grantable_acl_count == 0
        and function_owner_update_acl.table_update_privilege_count == 0
    )
    runner_contract = facts.runner_contract
    exact_runner_shape = (
        task7_v21_erasure_runner_split_is_valid()
        and runner_contract.activation_state == _TASK7_V21_CURRENT_ERASURE_STATE
        and runner_contract.delete_relation_tuple == TASK7_V21_CURRENT_ERASURE_RELATIONS
        and runner_contract.result_relation_tuple == TASK7_V21_CURRENT_ERASURE_RELATIONS
        and not runner_contract.future_relation_tuple_present
    )
    if (
        not exact_executor_posture
        or not exact_owner_posture
        or not exact_function_owner_update_acl
        or not exact_guard_owner_shape
        or not exact_runner_shape
        or any(type(value) is not int for value in count_values)
        or facts.membership_count != 0
        or facts.executor_table_privilege_count != 0
        or facts.executor_column_privilege_count != 0
        or facts.executor_schema_usage is not True
        or facts.executor_schema_create is not False
        or facts.executor_schema_direct_acl_count != 1
        or facts.executor_other_direct_execute_count != 0
        or facts.app_effective_role != facts.app_role
        or not exact_function_shape
    ):
        return _blocked("erasure_executor", "ERASURE_EXECUTOR_POSTURE_BLOCKED")

    if (
        facts.authenticated_role != "idis_erasure_executor"
        or facts.effective_role != "idis_erasure_executor"
        or facts.app_role in {"", "idis_erasure_executor", "idis_erasure_function_owner"}
        or facts.executor_has_execute is not True
        or facts.public_has_execute is not False
        or facts.app_role_has_execute is not False
        or facts.unexpected_execute_grant_count != 0
        or guard_owner.application_direct_execute_count != 0
        or guard_owner.public_direct_execute_count != 0
        or guard_owner.unexpected_execute_grant_count != 0
        or guard_owner.external_helper_guard_owner_execute is not True
        or guard_owner.external_helper_application_execute is not False
        or guard_owner.external_helper_public_execute is not False
        or guard_owner.external_helper_unexpected_execute_grant_count != 0
    ):
        return _blocked("erasure_executor", "ERASURE_EXECUTOR_UNAUTHORIZED")
    return _allowed("erasure_executor")


def check_audit_readiness(*, probe: Callable[[], AuditProbeFacts]) -> ReadinessCheck:
    facts = _observe(probe)
    if not isinstance(facts, AuditProbeFacts) or facts.backend != "postgres":
        return _blocked("audit", "AUDIT_FAILURE")
    if not (facts.emit_ok and facts.read_ok and facts.restart_read_ok):
        return _blocked("audit", "AUDIT_EMIT_FAILED")
    return _allowed("audit")


def check_idempotency_readiness(
    *,
    env: Mapping[str, str],
    probe: Callable[[], IdempotencyProbeFacts],
) -> ReadinessCheck:
    if "IDIS_IDEMPOTENCY_DB_PATH" in env:
        return _blocked("idempotency", "IDEMPOTENCY_NON_DURABLE_BLOCKED")
    database_url = env.get("IDIS_DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgresql+")):
        return _blocked("idempotency", "IDEMPOTENCY_NON_DURABLE_BLOCKED")
    facts = _observe(probe)
    if not isinstance(facts, IdempotencyProbeFacts) or facts.backend != "postgres":
        return _blocked("idempotency", "IDEMPOTENCY_NON_DURABLE_BLOCKED")
    if not (
        facts.put_ok
        and facts.replay_ok
        and facts.conflict_ok
        and facts.restart_replay_ok
        and facts.tenant_isolation_ok
    ):
        return _blocked("idempotency", "IDEMPOTENCY_STORE_FAILED")
    return _allowed("idempotency")


def check_redis_readiness(
    *,
    env: Mapping[str, str],
    probe: Callable[[], RedisProbeFacts],
) -> ReadinessCheck:
    redis_url = env.get("IDIS_REDIS_URL", "")
    rate_values = (
        _positive_int(env.get("IDIS_RATE_LIMIT_USER_RPM")),
        _positive_int(env.get("IDIS_RATE_LIMIT_INTEGRATION_RPM")),
        _positive_int(env.get("IDIS_RATE_LIMIT_BURST_MULTIPLIER")),
    )
    if not redis_url.startswith(("redis://", "rediss://")) or None in rate_values:
        return _blocked("redis", "RATE_LIMIT_STORE_UNAVAILABLE")
    facts = _observe(probe)
    if not isinstance(facts, RedisProbeFacts):
        return _blocked("redis", "RATE_LIMIT_STORE_UNAVAILABLE")
    if facts.backend != "redis" or not (
        facts.ping_ok and facts.atomic_bucket_ok and facts.cross_process_ok
    ):
        return _blocked("redis", "RATE_LIMIT_STORE_UNAVAILABLE")
    return _allowed("redis")


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def check_object_store_readiness(
    *,
    env: Mapping[str, str],
    probe: Callable[[], ObjectStoreProbeFacts],
    worktree_root: Path,
    temp_root: Path,
) -> ReadinessCheck:
    raw_root = env.get("IDIS_OBJECT_STORE_BASE_DIR", "")
    configured = Path(raw_root).expanduser()
    if env.get("IDIS_OBJECT_STORE_BACKEND") != "filesystem" or not configured.is_absolute():
        return _blocked("object_store", "OBJECT_STORE_HEALTH_BLOCKED")
    try:
        configured = configured.resolve(strict=False)
        worktree = worktree_root.resolve(strict=False)
        temporary = temp_root.resolve(strict=False)
    except OSError:
        return _blocked("object_store", "OBJECT_STORE_HEALTH_BLOCKED")
    if _within(configured, worktree) or _within(configured, temporary):
        return _blocked("object_store", "OBJECT_STORE_HEALTH_BLOCKED")

    facts = _observe(probe)
    if not isinstance(facts, ObjectStoreProbeFacts):
        return _blocked("object_store", "OBJECT_STORE_HEALTH_BLOCKED")
    if facts.backend != "filesystem" or not (
        facts.write_ok
        and facts.read_ok
        and facts.hash_ok
        and facts.delete_ok
        and facts.restart_read_ok
    ):
        return _blocked("object_store", "OBJECT_STORE_HEALTH_BLOCKED")
    return _allowed("object_store")


def _non_negative_byte_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def check_ingestion_readiness(
    *,
    env: Mapping[str, str],
    probe: Callable[[], IngestionProbeFacts],
) -> ReadinessCheck:
    """Require one bounded large upload to fit the real spool/store path.

    A shared filesystem needs simultaneous room for the request spool and the
    object store's atomic publication copy.  Separate filesystems each need one
    complete configured upload.  Probe exceptions and private paths collapse to
    the single reviewed readiness code.
    """

    if (
        env.get("IDIS_INGEST_MAX_BYTES") != str(_APPROVED_INGEST_MAX_BYTES)
        or env.get("IDIS_OCR_MAX_PAGES") != str(_APPROVED_OCR_MAX_PAGES)
        or env.get("IDIS_OCR_TIMEOUT_SECONDS") != str(_APPROVED_OCR_TIMEOUT_SECONDS)
        or env.get("IDIS_OCR_DPI") != str(_APPROVED_OCR_DPI)
        or str(env.get(DOCUMENT_PASSWORD_MANIFEST_PATH_ENV, "")).strip()
    ):
        return _blocked("ingestion", "MEDIA_LARGE_UPLOAD_UNSUPPORTED")

    facts = _observe(probe)
    attestation_configured = bool(
        str(env.get(NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV, "")).strip()
    )
    if not isinstance(facts, IngestionProbeFacts):
        if attestation_configured:
            return _blocked(
                "ingestion",
                "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID",
            )
        return _blocked("ingestion", "MEDIA_LARGE_UPLOAD_UNSUPPORTED")
    if attestation_configured:
        v1_attestation_ready = (
            facts.no_substantive_content_attestation_status == "verified"
            and facts.no_substantive_content_attestation_entry_count == 1
            and facts.no_substantive_content_attestation_schema_version
            == NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION
            and facts.no_substantive_content_attestation_disposition
            == DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
            and facts.no_substantive_content_attestation_variant in NO_SUBSTANTIVE_CONTENT_VARIANTS
            and _is_sha256(facts.no_substantive_content_attestation_payload_sha256)
            and _is_sha256(facts.no_substantive_content_attestation_envelope_sha256)
            and _is_sha256(facts.no_substantive_content_attestation_public_key_sha256)
            and facts.no_substantive_content_attestation_manifest_sha256 is None
            and facts.no_substantive_content_attestation_classifier_policy_sha256 is None
            and facts.no_substantive_content_attestation_exemplar_envelope_sha256 is None
            and facts.no_substantive_content_attestation_exemplar_canonical_bitmap_sha256 is None
            and facts.no_substantive_content_attestation_prescan_receipt_sha256 is None
            and facts.no_substantive_content_attestation_candidate_set_sha256 is None
            and facts.no_substantive_content_attestation_sample_set_sha256 is None
            and facts.no_substantive_content_attestation_batch_envelope_sha256 is None
            and facts.no_substantive_content_attestation_manual_count == 0
            and facts.no_substantive_content_attestation_watermark_candidate_count == 0
            and facts.no_substantive_content_attestation_runtime_unseen_count == 0
        )
        v2_hash_bindings = (
            facts.no_substantive_content_attestation_manifest_sha256,
            facts.no_substantive_content_attestation_classifier_policy_sha256,
            facts.no_substantive_content_attestation_exemplar_envelope_sha256,
            facts.no_substantive_content_attestation_exemplar_canonical_bitmap_sha256,
            facts.no_substantive_content_attestation_prescan_receipt_sha256,
            facts.no_substantive_content_attestation_candidate_set_sha256,
            facts.no_substantive_content_attestation_sample_set_sha256,
            facts.no_substantive_content_attestation_batch_envelope_sha256,
        )
        v2_attestation_ready = (
            facts.no_substantive_content_attestation_status == "verified"
            and facts.no_substantive_content_attestation_entry_count == 2
            and facts.no_substantive_content_attestation_schema_version
            == NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION
            and facts.no_substantive_content_attestation_disposition
            == DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
            and facts.no_substantive_content_attestation_variant == "watermark_only"
            and _is_sha256(facts.no_substantive_content_attestation_payload_sha256)
            and _is_sha256(facts.no_substantive_content_attestation_envelope_sha256)
            and _is_sha256(facts.no_substantive_content_attestation_public_key_sha256)
            and all(_is_sha256(value) for value in v2_hash_bindings)
            and facts.no_substantive_content_attestation_manual_count == 1
            and facts.no_substantive_content_attestation_watermark_candidate_count == 2
            and facts.no_substantive_content_attestation_runtime_unseen_count == 0
        )
        attestation_ready = v1_attestation_ready or v2_attestation_ready
        if (
            facts.no_substantive_content_attestation_schema_version
            == NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION
            and not v2_attestation_ready
        ):
            return _blocked("ingestion", "WATERMARK_BATCH_ATTESTATION_MISMATCH")
    else:
        attestation_ready = (
            facts.no_substantive_content_attestation_status == "not_configured"
            and facts.no_substantive_content_attestation_entry_count == 0
            and facts.no_substantive_content_attestation_schema_version is None
            and facts.no_substantive_content_attestation_disposition is None
            and facts.no_substantive_content_attestation_variant is None
            and facts.no_substantive_content_attestation_payload_sha256 is None
            and facts.no_substantive_content_attestation_envelope_sha256 is None
            and facts.no_substantive_content_attestation_public_key_sha256 is None
            and facts.no_substantive_content_attestation_manifest_sha256 is None
            and facts.no_substantive_content_attestation_classifier_policy_sha256 is None
            and facts.no_substantive_content_attestation_exemplar_envelope_sha256 is None
            and facts.no_substantive_content_attestation_exemplar_canonical_bitmap_sha256 is None
            and facts.no_substantive_content_attestation_prescan_receipt_sha256 is None
            and facts.no_substantive_content_attestation_candidate_set_sha256 is None
            and facts.no_substantive_content_attestation_sample_set_sha256 is None
            and facts.no_substantive_content_attestation_batch_envelope_sha256 is None
            and facts.no_substantive_content_attestation_manual_count == 0
            and facts.no_substantive_content_attestation_watermark_candidate_count == 0
            and facts.no_substantive_content_attestation_runtime_unseen_count == 0
        )
    if not attestation_ready:
        return _blocked(
            "ingestion",
            "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID",
        )
    if facts.backend != "filesystem" or not (
        facts.put_stream_supported
        and facts.bounded_read_ok
        and facts.stream_write_ok
        and facts.stream_read_ok
        and facts.stream_hash_ok
        and facts.stream_cleanup_ok
        and facts.staging_write_ok
        and facts.staging_cleanup_ok
        and facts.password_manifest_status == DocumentPasswordManifestStatus.NONE_CONFIGURED.value
    ):
        return _blocked("ingestion", "MEDIA_LARGE_UPLOAD_UNSUPPORTED")

    staging_free = _non_negative_byte_count(facts.staging_free_bytes)
    object_store_free = _non_negative_byte_count(facts.object_store_free_bytes)
    if staging_free is None or object_store_free is None:
        return _blocked("ingestion", "MEDIA_LARGE_UPLOAD_UNSUPPORTED")
    if type(facts.shared_filesystem) is not bool:
        return _blocked("ingestion", "MEDIA_LARGE_UPLOAD_UNSUPPORTED")

    if facts.shared_filesystem:
        required = 2 * _APPROVED_INGEST_MAX_BYTES
        enough_headroom = min(staging_free, object_store_free) >= required
    else:
        enough_headroom = (
            staging_free >= _APPROVED_INGEST_MAX_BYTES
            and object_store_free >= _APPROVED_INGEST_MAX_BYTES
        )
    if not enough_headroom:
        return _blocked("ingestion", "MEDIA_LARGE_UPLOAD_UNSUPPORTED")
    return _allowed("ingestion")


def check_auth_readiness(*, env: Mapping[str, str]) -> ReadinessCheck:
    report = validate_strict_auth_binding(env)
    if report.may_proceed:
        return _allowed("auth")
    code = report.reason_codes[0] if report.reason_codes else "AUTH_CONFIG_BLOCKED"
    return _blocked("auth", code)


def check_worker_readiness(
    *,
    env: Mapping[str, str],
    expected_role: str,
    probe: Callable[[], WorkerProbeFacts],
) -> ReadinessCheck:
    tenant = env.get("IDIS_ACCEPTANCE_TENANT_ID", "")
    tenant_scope = [part.strip() for part in env.get("IDIS_WORKER_TENANT_IDS", "").split(",")]
    if not tenant or tenant_scope != [tenant]:
        return _blocked("worker", "WORKER_TENANT_SCOPE_MISSING")
    configured_role = env.get("IDIS_PROCESS_ROLE")
    role_matches = (
        configured_role == expected_role
        if expected_role in {"api", "worker"}
        else expected_role == "gate" and not configured_role
    )
    if not role_matches:
        return _blocked("worker", "STRICT_PROCESS_ROLE_INVALID")

    lease_seconds = _positive_int(env.get("IDIS_WORKER_LEASE_SECONDS"))
    heartbeat_seconds = _positive_int(env.get("IDIS_WORKER_HEARTBEAT_SECONDS"))
    if lease_seconds is None or heartbeat_seconds is None or heartbeat_seconds >= lease_seconds:
        return _blocked("worker", "WORKER_LEASE_CONFIG_INVALID")

    facts = _observe(probe)
    if not isinstance(facts, WorkerProbeFacts):
        return _blocked("worker", "WORKER_CLAIM_NOT_PROVEN")
    if facts.backend != "postgres" or not (
        facts.queued_row_ok
        and facts.claim_ok
        and facts.fencing_ok
        and facts.claim_audit_ok
        and facts.heartbeat_ok
        and facts.reclaim_ok
        and facts.fresh_claim_ok
        and facts.stale_write_rejected
        and facts.boundary_rollback_ok
        and facts.no_residue
    ):
        return _blocked("worker", "WORKER_CLAIM_NOT_PROVEN")
    return _allowed("worker")


def check_budget_readiness(
    *,
    env: Mapping[str, str],
    probe: Callable[[], BudgetProbeFacts],
) -> ReadinessCheck:
    policy_path = env.get("IDIS_PROVIDER_BUDGET_POLICY_PATH", "")
    max_calls = _positive_int(env.get("IDIS_PROVIDER_BUDGET_MAX_CALLS"))
    if (
        not policy_path
        or not Path(policy_path).is_file()
        or max_calls is None
        or env.get("IDIS_PROVIDER_BUDGET_CURRENCY") != "USD"
    ):
        return _blocked("budgets", "PROVIDER_BUDGET_CONFIG_BLOCKED")

    facts = _observe(probe)
    if not isinstance(facts, BudgetProbeFacts):
        return _blocked("budgets", "PROVIDER_BUDGET_CONFIG_BLOCKED")
    if facts.configured_provider_ids != _BUDGET_COMPONENTS or not facts.token_caps_present:
        return _blocked("budgets", "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED")
    if (
        facts.backend != "postgres"
        or not facts.policy_loaded
        or facts.policy_currency != "USD"
        or not facts.call_reservation_ok
        or not facts.usd_reservation_ok
        or not facts.consume_ok
        or not facts.refund_ok
        or not facts.restart_read_ok
    ):
        return _blocked("budgets", "PROVIDER_BUDGET_CONFIG_BLOCKED")
    return _allowed("budgets")


def build_dependency_admission_report(
    checks: Sequence[ReadinessCheck],
    *,
    admission_scope: str = FULL_DEPENDENCY_ADMISSION_SCOPE,
) -> DependencyAdmissionReport:
    frozen_checks = tuple(checks)
    blockers: list[str] = []
    for check in frozen_checks:
        if not check.may_proceed and check.code is not None and check.code not in blockers:
            blockers.append(check.code)
    expected_order: tuple[str, ...]
    excluded_components: tuple[str, ...]
    if admission_scope == FULL_DEPENDENCY_ADMISSION_SCOPE:
        expected_order = _COMPONENT_ORDER
        excluded_components = ()
    elif admission_scope == TASK4_INGESTION_ADMISSION_SCOPE:
        expected_order = _TASK4_INGESTION_COMPONENT_ORDER
        excluded_components = tuple(
            component
            for component in _COMPONENT_ORDER
            if component not in _TASK4_INGESTION_COMPONENT_ORDER
        )
    else:
        expected_order = ()
        excluded_components = _COMPONENT_ORDER
    if tuple(check.component for check in frozen_checks) != expected_order:
        blockers.append("STRICT_FULL_LIVE_BLOCKED")
    return DependencyAdmissionReport(
        checks=frozen_checks,
        may_proceed=not blockers,
        blocker_codes=tuple(blockers),
        admission_scope=admission_scope,
        excluded_components=excluded_components,
    )


__all__ = [
    "AuditProbeFacts",
    "BudgetProbeFacts",
    "DependencyAdmissionReport",
    "ErasureDatabaseTargetFacts",
    "ErasureExecutorProbeFacts",
    "ErasureFunctionOwnerUpdateAclFacts",
    "ErasureRoleFacts",
    "FULL_DEPENDENCY_ADMISSION_SCOPE",
    "IdempotencyProbeFacts",
    "IngestionProbeFacts",
    "ObjectStoreProbeFacts",
    "PostgresProbeFacts",
    "ReadinessCheck",
    "TASK4_INGESTION_ADMISSION_SCOPE",
    "TASK7_V18_COMPLETE_ERASURE_RELATIONS",
    "TASK7_V18_CURRENT_ERASURE_RELATIONS",
    "TASK7_V18_CURRENT_GUARD_ROUTINE_POSTURES",
    "TASK7_V18_CURRENT_GUARD_ROUTINE_SIGNATURES",
    "TASK7_V18_FUTURE_ERASURE_RELATIONS",
    "TASK7_V18_FUTURE_GUARD_ROUTINE_SIGNATURES",
    "TASK7_V20_COMPLETE_ERASURE_RELATIONS",
    "TASK7_V20_CURRENT_ERASURE_RELATIONS",
    "TASK7_V20_CURRENT_GUARD_ROUTINE_POSTURES",
    "TASK7_V20_CURRENT_GUARD_ROUTINE_SIGNATURES",
    "TASK7_V20_FUTURE_ERASURE_RELATIONS",
    "TASK7_V20_FUTURE_GUARD_ROUTINE_SIGNATURES",
    "TASK7_V21_COMPLETE_ERASURE_RELATIONS",
    "TASK7_V21_CURRENT_ERASURE_RELATIONS",
    "TASK7_V21_CURRENT_GUARD_ROUTINE_POSTURES",
    "TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES",
    "TASK7_V21_FUTURE_ERASURE_RELATIONS",
    "TASK7_V21_FUTURE_GUARD_ROUTINE_SIGNATURES",
    "Task7ErasureRunnerProbeFacts",
    "Task7GuardOwnerProbeFacts",
    "RedisProbeFacts",
    "WorkerProbeFacts",
    "build_dependency_admission_report",
    "check_audit_readiness",
    "check_auth_readiness",
    "check_budget_readiness",
    "check_erasure_executor_readiness",
    "check_idempotency_readiness",
    "check_ingestion_readiness",
    "check_object_store_readiness",
    "check_postgres_readiness",
    "check_redis_readiness",
    "check_worker_readiness",
    "task7_v18_erasure_runner_split_is_valid",
    "task7_v20_erasure_runner_split_is_valid",
    "task7_v21_erasure_runner_split_is_valid",
]
