"""RED admission proof for the full-strict-v5 FK-authority correction.

This suite is deliberately artifact-only.  It verifies the reviewed relational
authority bytes without importing runtime code or connecting to PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V4_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v4_reviewed_preimage_v1.json"
_V5_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v5_reviewed_preimage_v1.json"

_V4_SPAN_AUTHORITY = (
    "ux_document_spans_task6_scope(tenant_id,deal_id,span_id) WHERE deal_id IS NOT NULL"
)
_V5_SPAN_AUTHORITY = "ux_document_spans_task6_scope(tenant_id,deal_id,document_id,span_id)"
_BASELINE_AUTHORITY_SECTION = "baseline_parent_authority_registry"
_BASELINE_AUTHORITY_SCHEMA_VERSION = "baseline-parent-authority-registry-v1"
_BASELINE_AUTHORITY_ROW_SCHEMA = [
    "authority_ordinal",
    "table_name",
    "authority_name",
    "authority_kind",
    "column_tuple",
    "predicate",
    "installation_revision",
    "upgrade_ddl",
    "downgrade_disposition",
]
_BASELINE_AUTHORITY_ROWS = [
    [
        0,
        "claims",
        "ux_claims_task6_scope",
        "UNIQUE_INDEX",
        ["tenant_id", "deal_id", "claim_id"],
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_claims_task6_scope ON claims (tenant_id, deal_id, claim_id)",
        "PRESERVE_PRE_0038",
    ],
    [
        1,
        "deals",
        "uq_task7_deals_tenant_deal",
        "UNIQUE_CONSTRAINT",
        ["tenant_id", "deal_id"],
        None,
        "0038",
        "ALTER TABLE deals ADD CONSTRAINT uq_task7_deals_tenant_deal UNIQUE (tenant_id,deal_id)",
        "DROP_WITH_0038",
    ],
    [
        2,
        "evidence_items",
        "uq_v2_evidence_item_generic_parent",
        "UNIQUE_CONSTRAINT",
        ["tenant_id", "deal_id", "evidence_id", "claim_id", "source_span_id"],
        None,
        "0038",
        "ALTER TABLE evidence_items ADD CONSTRAINT uq_v2_evidence_item_generic_parent "
        "UNIQUE (tenant_id,deal_id,evidence_id,claim_id,source_span_id)",
        "DROP_WITH_0038",
    ],
    [
        3,
        "sanads",
        "ux_sanads_task6_scope",
        "UNIQUE_INDEX",
        ["tenant_id", "deal_id", "sanad_id"],
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_sanads_task6_scope ON sanads (tenant_id, deal_id, sanad_id)",
        "PRESERVE_PRE_0038",
    ],
]

_CURRENT_UNIQUE_ADDITIONS = {
    "debate_sessions": ("uq_v2_debate_session_scope(tenant_id,deal_id,run_id,debate_id)",),
    "evidence_trust_findings": (
        "uq_v2_evidence_trust_findings_reference_parent"
        "(tenant_id,deal_id,run_id,finding_id,run_output_object_id)",
    ),
    "layer2_ic_challenges": (
        "uq_v2_layer2_challenge_scope(tenant_id,deal_id,run_id,challenge_id)",
    ),
    "methodology_coverage_records": (
        "uq_v2_coverage_dashboard_parent(tenant_id,deal_id,run_id,coverage_record_id)",
    ),
    "methodology_result_sanad_links": (
        "uq_v2_methodology_sanad_grading_parent(tenant_id,deal_id,run_id,sanad_link_id,sanad_id)",
    ),
    "validated_evidence_packages": (
        "uq_v2_validated_evidence_package_scope(tenant_id,deal_id,run_id,package_id)",
        "uq_v2_validated_evidence_packages_reference_parent"
        "(tenant_id,deal_id,run_id,package_id,run_output_object_id)",
    ),
}
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
_FUTURE_NATIVE_ALIAS_RETARGETS = {
    ("external_intelligence_conflict_executions", "fk_v2_conflict_execution_plan"): (
        "uq_v2_conflict_plan_scope",
        "uq_v2_conflict_plans_scope",
    ),
    ("rag_query_results", "fk_v2_rag_query_index"): (
        "uq_v2_rag_index_result_scope",
        "uq_v2_rag_index_results_scope",
    ),
}
_FUTURE_SCHEMA_HASH_CHANGED_TABLES = frozenset(
    (
        *_FUTURE_REFERENCE_TABLES,
        *(table for table, _foreign_key_name in _FUTURE_NATIVE_ALIAS_RETARGETS),
    )
)
_V4_MISSING_UNIQUE_AUTHORITIES = frozenset(
    {
        ("debate_sessions", "uq_v2_debate_session_scope"),
        (
            "evidence_items",
            "uq_v2_evidence_item_generic_parent",
        ),
        (
            "evidence_trust_findings",
            "uq_v2_evidence_trust_findings_reference_parent",
        ),
        ("layer2_ic_challenges", "uq_v2_layer2_challenge_scope"),
        (
            "methodology_coverage_records",
            "uq_v2_coverage_dashboard_parent",
        ),
        (
            "methodology_result_sanad_links",
            "uq_v2_methodology_sanad_grading_parent",
        ),
        (
            "validated_evidence_packages",
            "uq_v2_validated_evidence_package_scope",
        ),
        (
            "validated_evidence_packages",
            "uq_v2_validated_evidence_packages_reference_parent",
        ),
    }
)

_CURRENT_UNIQUE_RE = re.compile(r"^(?P<name>[a-z0-9_]+)\((?P<columns>[^)]*)\)(?P<suffix>.*)$")
_CURRENT_FK_RE = re.compile(
    r"^(?P<name>[a-z0-9_]+)\((?P<child>[^)]*)\) -> "
    r"(?P<parent_table>[a-z0-9_]+)\.(?P<parent_key>[a-z0-9_]+)"
    r"\((?P<parent>[^)]*)\) "
)


@dataclass(frozen=True)
class _Authority:
    table: str
    name: str
    columns: tuple[str, ...]
    partial: bool
    source: str

    @property
    def key(self) -> tuple[str, str]:
        return self.table, self.name


@dataclass(frozen=True)
class _ForeignKey:
    table: str
    name: str
    child_columns: tuple[str, ...]
    parent_table: str
    parent_key: str
    parent_columns: tuple[str, ...]
    source: str

    @property
    def authority_key(self) -> tuple[str, str]:
        return self.parent_table, self.parent_key


# A finite, reviewed reverse marker.  Any newly introduced unique authority
# must either be the exact target of a declared FK or be deliberately added to
# this list; deriving the list from the artifact would mask dead authorities.
_STANDALONE_PARENT_AUTHORITY_MARKERS_V1 = frozenset(
    line
    for line in """
analysis_agent_input_references.pk_v2_analysis_input_ref
analysis_agent_input_references.uq_v2_analysis_input_ref_coordinate
analysis_agent_input_references.uq_v2_analysis_input_ref_ordinal
analysis_agent_input_references.uq_v2_analysis_input_ref_scope
analysis_agent_outputs.pk_v2_analysis_agent_outputs
analysis_agent_outputs.uq_v2_analysis_agent_outputs_root
analysis_agent_outputs.uq_v2_analysis_agent_outputs_scope
analysis_agent_outputs.uq_v2_analysis_output_muhasabah
analysis_agent_outputs.uq_v2_analysis_output_role
calc_sanads.uq_task7_calc_sanads_scope
company_identity_identifiers.pk_v2_identity_identifier
company_identity_identifiers.uq_v2_identity_identifier_coordinate
company_identity_identifiers.uq_v2_identity_identifier_scope
company_identity_input_references.pk_v2_identity_input_ref
company_identity_input_references.uq_v2_identity_input_ref_coordinate
company_identity_input_references.uq_v2_identity_input_ref_ordinal
company_identity_input_references.uq_v2_identity_input_ref_scope
company_identity_outputs.pk_v2_company_identity_outputs
company_identity_outputs.uq_v2_company_identity_outputs_root
company_identity_outputs.uq_v2_company_identity_outputs_scope
conflict_input_references.pk_v2_conflict_input_ref
conflict_input_references.uq_v2_conflict_input_ref_coordinate
conflict_input_references.uq_v2_conflict_input_ref_ordinal
conflict_input_references.uq_v2_conflict_input_ref_scope
data_room_package_files.uq_task7_data_room_package_files_source_root
data_room_package_files.ux_task7_package_files_doc_id_bound
data_room_package_files.ux_task7_package_files_document_id_bound
data_room_packages.uq_task7_data_room_packages_source_root
debate_role_input_references.pk_v2_debate_input_ref
debate_role_input_references.uq_v2_debate_input_ref_coordinate
debate_role_input_references.uq_v2_debate_input_ref_ordinal
debate_role_input_references.uq_v2_debate_input_ref_scope
debate_role_outputs.pk_v2_debate_role_outputs
debate_role_outputs.uq_v2_debate_output_role
debate_role_outputs.uq_v2_debate_role_outputs_root
debate_role_outputs.uq_v2_debate_role_outputs_scope
debate_sessions.uq_task7_debate_sessions_output_root
deliverable_artifact_input_references.pk_v2_deliverable_input_ref
deliverable_artifact_input_references.uq_v2_deliverable_input_ref_coordinate
deliverable_artifact_input_references.uq_v2_deliverable_input_ref_ordinal
deliverable_artifact_input_references.uq_v2_deliverable_input_ref_scope
deliverable_manifest_references.pk_task7_deliverable_manifest_references
deliverable_manifest_references.uq_task7_deliverable_manifest_references_coordinate
deliverable_manifest_references.uq_task7_deliverable_manifest_references_member
deliverable_manifest_references.uq_task7_deliverable_manifest_references_native_root
deliverable_manifest_references.uq_task7_deliverable_manifest_references_reference_sha
deliverable_manifest_references.uq_task7_deliverable_manifest_references_scoped_id
deliverables.uq_task7_deliverables_output_root
deterministic_calculations.uq_task7_deterministic_calculations_output_root
document_artifacts.uq_task7_document_artifacts_source_root
document_parse_attempts.pk_task7_document_parse_attempts
document_parse_attempts.uq_task7_document_parse_attempts_ordinal
document_parse_attempts.uq_task7_document_parse_attempts_source_root
document_spans.uq_task7_document_spans_source_root
documents.uq_task7_documents_source_root
enrichment_normalized_facts.pk_v2_enrichment_normalized_facts
enrichment_normalized_facts.uq_v2_enrichment_normalized_facts_reference_parent
enrichment_normalized_facts.uq_v2_enrichment_normalized_facts_root
enrichment_normalized_facts.uq_v2_enrichment_normalized_facts_scope
enrichment_normalized_facts.uq_v2_fact_coordinate
enrichment_provider_outcomes.pk_v2_enrichment_provider_outcomes
enrichment_provider_outcomes.uq_v2_enrichment_provider_outcomes_reference_parent
enrichment_provider_outcomes.uq_v2_enrichment_provider_outcomes_root
enrichment_provider_outcomes.uq_v2_enrichment_provider_outcomes_scope
enrichment_provider_outcomes.uq_v2_provider_outcome_provider
evidence_trust_court_input_references.pk_v2_court_input_ref
evidence_trust_court_input_references.uq_v2_court_input_ref_coordinate
evidence_trust_court_input_references.uq_v2_court_input_ref_ordinal
evidence_trust_court_input_references.uq_v2_court_input_ref_scope
evidence_trust_findings.uq_task7_evidence_trust_findings_output_root
external_intelligence_conflict_executions.pk_v2_conflict_executions
external_intelligence_conflict_executions.uq_v2_conflict_execution_plan
external_intelligence_conflict_executions.uq_v2_conflict_executions_reference_parent
external_intelligence_conflict_executions.uq_v2_conflict_executions_root
external_intelligence_conflict_executions.uq_v2_conflict_executions_scope
external_intelligence_conflict_findings.pk_v2_conflict_findings
external_intelligence_conflict_findings.uq_v2_conflict_findings_reference_parent
external_intelligence_conflict_findings.uq_v2_conflict_findings_root
external_intelligence_conflict_findings.uq_v2_conflict_findings_scope
external_intelligence_conflict_plans.pk_v2_conflict_plans
external_intelligence_conflict_plans.uq_v2_conflict_plan_subject
external_intelligence_conflict_plans.uq_v2_conflict_plans_root
generic_extraction_claim_links.pk_v2_generic_claim_link
generic_extraction_claim_links.uq_v2_generic_claim_link_coordinate
generic_extraction_claim_links.uq_v2_generic_claim_link_ordinal
generic_extraction_claim_links.uq_v2_generic_claim_link_root
generic_extraction_claim_links.uq_v2_generic_claim_link_scope
generic_extraction_evidence_links.pk_v2_generic_evidence_link
generic_extraction_evidence_links.uq_v2_generic_evidence_link_coordinate
generic_extraction_evidence_links.uq_v2_generic_evidence_link_ordinal
generic_extraction_evidence_links.uq_v2_generic_evidence_link_root
generic_extraction_evidence_links.uq_v2_generic_evidence_link_scope
generic_extraction_evidence_links.uq_v2_generic_evidence_link_span_coordinate
generic_extraction_input_references.pk_v2_generic_input_ref
generic_extraction_input_references.uq_v2_generic_input_ref_coordinate
generic_extraction_input_references.uq_v2_generic_input_ref_ordinal
generic_extraction_input_references.uq_v2_generic_input_ref_scope
generic_extraction_outputs.pk_v2_generic_extraction_outputs
generic_extraction_outputs.uq_v2_generic_extraction_outputs_root
generic_extraction_outputs.uq_v2_generic_extraction_outputs_scope
graph_projection_results.pk_v2_graph_projection_results
graph_projection_results.uq_v2_graph_projection_results_root
graph_projection_results.uq_v2_graph_projection_results_scope
graph_result_input_references.pk_v2_graph_input_ref
graph_result_input_references.uq_v2_graph_input_ref_coordinate
graph_result_input_references.uq_v2_graph_input_ref_ordinal
graph_result_input_references.uq_v2_graph_input_ref_scope
layer2_ic_challenges.uq_task7_layer2_ic_challenges_output_root
layer2_ic_findings.uq_task7_layer2_ic_findings_output_root
layer2_readiness_input_references.pk_v2_readiness_input_ref
layer2_readiness_input_references.uq_v2_readiness_input_ref_coordinate
layer2_readiness_input_references.uq_v2_readiness_input_ref_ordinal
layer2_readiness_input_references.uq_v2_readiness_input_ref_scope
layer2_readiness_outputs.pk_v2_layer2_readiness_outputs
layer2_readiness_outputs.uq_v2_layer2_readiness_outputs_root
layer2_readiness_outputs.uq_v2_layer2_readiness_outputs_scope
layer2_role_input_references.pk_v2_layer2_input_ref
layer2_role_input_references.uq_v2_layer2_input_ref_coordinate
layer2_role_input_references.uq_v2_layer2_input_ref_ordinal
layer2_role_input_references.uq_v2_layer2_input_ref_scope
layer2_role_outputs.pk_v2_layer2_role_outputs
layer2_role_outputs.uq_v2_layer2_output_muhasabah
layer2_role_outputs.uq_v2_layer2_output_role
layer2_role_outputs.uq_v2_layer2_role_outputs_root
layer2_role_outputs.uq_v2_layer2_role_outputs_scope
methodology_coverage_records.uq_task7_methodology_coverage_records_output_root
methodology_extraction_results.uq_task7_methodology_extraction_results_output_root
methodology_extraction_tasks.uq_task7_methodology_extraction_tasks_output_root
methodology_result_claim_links.uq_task7_methodology_result_claim_links_output_root
methodology_result_defect_links.uq_task7_methodology_result_defect_links_output_root
methodology_result_evidence_links.uq_task7_methodology_result_evidence_links_output_root
methodology_result_factual_outputs.uq_task7_methodology_result_factual_outputs_output_root
methodology_result_sanad_links.uq_task7_methodology_result_sanad_links_output_root
methodology_truth_dashboard_root_links.pk_v3_dashboard_root_link
methodology_truth_dashboard_root_links.uq_v3_dashboard_root_link_coordinate
methodology_truth_dashboard_root_links.uq_v3_dashboard_root_link_ordinal
methodology_truth_dashboard_root_links.uq_v3_dashboard_root_link_scope
methodology_truth_dashboard_rows.pk_v2_methodology_truth_dashboard_rows
methodology_truth_dashboard_rows.uq_v2_methodology_truth_dashboard_rows_root
methodology_truth_dashboard_rows.uq_v2_methodology_truth_dashboard_rows_scope
muhasabah_records.uq_task7_muhasabah_records_output_root
rag_index_input_references.pk_v2_rag_index_input_ref
rag_index_input_references.uq_v2_rag_index_input_ref_coordinate
rag_index_input_references.uq_v2_rag_index_input_ref_ordinal
rag_index_input_references.uq_v2_rag_index_input_ref_scope
rag_index_results.pk_v2_rag_index_results
rag_index_results.uq_v2_rag_index_results_root
rag_query_input_references.pk_v2_rag_query_input_ref
rag_query_input_references.uq_v2_rag_query_input_ref_coordinate
rag_query_input_references.uq_v2_rag_query_input_ref_ordinal
rag_query_input_references.uq_v2_rag_query_input_ref_scope
rag_query_results.pk_v2_rag_query_results
rag_query_results.uq_v2_rag_query_results_root
rag_query_results.uq_v2_rag_query_results_scope
run_applicability_decisions.pk_task7_run_applicability_decisions
run_applicability_decisions.uq_task7_run_applicability_decisions_decision_sha
run_applicability_decisions.uq_task7_run_applicability_decisions_subject
run_claim_grading_links.pk_v2_run_claim_grading_links
run_claim_grading_links.uq_v2_run_claim_grading_links_reference_parent
run_claim_grading_links.uq_v2_run_claim_grading_links_root
run_claim_grading_links.uq_v2_run_claim_grading_links_scope
run_claim_grading_links.uq_v2_run_claim_grading_links_source_root
run_consumer_receipts.pk_task7_run_consumer_receipts
run_consumer_receipts.uq_task7_run_consumer_receipts_edge_output
run_consumer_receipts.uq_task7_run_consumer_receipts_one_per_edge
run_consumer_receipts.uq_task7_run_consumer_receipts_scoped_id
run_expected_consumer_edges.pk_task7_run_expected_consumer_edges
run_expected_consumer_edges.uq_task7_run_expected_consumer_edges_natural
run_expected_consumer_edges.uq_task7_run_expected_consumer_edges_scoped_id
run_expected_consumer_edges.uq_task7_run_expected_consumer_edges_tuple_sha
run_input_bindings.pk_task7_run_input_bindings
run_input_bindings.uq_task7_run_input_bindings_binding_sha
run_input_bindings.uq_task7_run_input_bindings_run_source
run_output_objects.pk_task7_run_output_objects
run_output_objects.uq_task7_run_output_objects_semantic_identity
run_output_objects.uq_task7_run_output_objects_tenant_run_id
run_step_outputs.pk_task7_run_step_outputs
run_step_outputs.uq_task7_run_step_outputs_coordinate
run_step_outputs.uq_task7_run_step_outputs_output_object
run_steps.uq_task7_run_steps_order
scoring_input_references.pk_v2_scoring_input_ref
scoring_input_references.uq_v2_scoring_input_ref_coordinate
scoring_input_references.uq_v2_scoring_input_ref_ordinal
scoring_input_references.uq_v2_scoring_input_ref_scope
scoring_output_dimensions.pk_v2_scoring_dimension
scoring_output_dimensions.uq_v2_scoring_dimension_key
scoring_output_dimensions.uq_v2_scoring_dimension_ordinal
scoring_output_dimensions.uq_v2_scoring_dimension_scope
scoring_outputs.pk_v2_scoring_outputs
scoring_outputs.uq_v2_scoring_outputs_root
scoring_outputs.uq_v2_scoring_outputs_scope
source_lineage_objects.pk_task7_source_lineage_objects
source_lineage_objects.uq_task7_source_lineage_objects_identity_version
source_lineage_objects.uq_task7_source_lineage_objects_scope_kind
step_receipt_input_references.pk_v2_receipt_input_ref
step_receipt_input_references.uq_v2_receipt_input_ref_coordinate
step_receipt_input_references.uq_v2_receipt_input_ref_ordinal
step_receipt_input_references.uq_v2_receipt_input_ref_scope
validated_evidence_package_input_references.pk_v2_vep_input_ref
validated_evidence_package_input_references.uq_v2_vep_input_ref_coordinate
validated_evidence_package_input_references.uq_v2_vep_input_ref_ordinal
validated_evidence_package_input_references.uq_v2_vep_input_ref_scope
validated_evidence_package_references.pk_v2_vep_content_references
validated_evidence_package_references.uq_v2_vep_content_coordinate
validated_evidence_package_references.uq_v2_vep_content_references_reference_parent
validated_evidence_package_references.uq_v2_vep_content_references_root
validated_evidence_package_references.uq_v2_vep_content_references_scope
validated_evidence_packages.uq_task7_validated_evidence_packages_output_root
vector_embeddings.uq_task7_vector_embeddings_output_root
vector_embeddings.ux_vector_embeddings_reusable
vector_embeddings.ux_vector_embeddings_strict_run
""".splitlines()
    if line
)

_BYTE_STABLE_SECTIONS = (
    "schema_registry",
    "semantic_step_registry",
    "future_child_authority_registry",
    "sql_authority_registry",
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
)


def _load(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"TASK7_CONTRACT_ARTIFACT_MISSING:{path.name}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _h(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _columns(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _register(
    authorities: dict[tuple[str, str], _Authority],
    authority: _Authority,
) -> None:
    previous = authorities.get(authority.key)
    assert previous is None or (
        previous.columns == authority.columns and previous.partial == authority.partial
    ), f"TASK7_PARENT_AUTHORITY_AMBIGUOUS:{authority.table}.{authority.name}:{previous}:{authority}"
    authorities[authority.key] = authority


def _authorities(artifact: dict[str, Any]) -> dict[tuple[str, str], _Authority]:
    result: dict[tuple[str, str], _Authority] = {}

    baseline = artifact.get(_BASELINE_AUTHORITY_SECTION)
    if baseline is not None:
        fields = tuple(str(field) for field in baseline["row_schema"])
        assert fields == tuple(_BASELINE_AUTHORITY_ROW_SCHEMA)
        for row in baseline["rows"]:
            values = dict(zip(fields, row, strict=True))
            assert values["authority_kind"] in {"UNIQUE_CONSTRAINT", "UNIQUE_INDEX"}
            _register(
                result,
                _Authority(
                    table=str(values["table_name"]),
                    name=str(values["authority_name"]),
                    columns=tuple(str(column) for column in values["column_tuple"]),
                    partial=values["predicate"] is not None,
                    source=_BASELINE_AUTHORITY_SECTION,
                ),
            )

    for row in artifact["current_ddl_registry"]["rows"]:
        table = str(row["table_name"])
        for literal in row["unique_tuple"]:
            match = _CURRENT_UNIQUE_RE.fullmatch(literal)
            assert match is not None, f"TASK7_UNIQUE_AUTHORITY_UNPARSEABLE:{literal}"
            _register(
                result,
                _Authority(
                    table=table,
                    name=match.group("name"),
                    columns=_columns(match.group("columns")),
                    partial=match.group("suffix").strip().startswith("WHERE "),
                    source="current_ddl_registry",
                ),
            )

    for registry, table_field in (
        ("future_native_authority_registry", "planned_table"),
        ("future_reference_authority_registry", "table_name"),
        ("future_child_authority_registry", "table_name"),
    ):
        for row in artifact[registry]["rows"]:
            table = str(row[table_field])
            for name, columns, _nulls_not_distinct in row["unique_tuple"]:
                _register(
                    result,
                    _Authority(
                        table=table,
                        name=str(name),
                        columns=tuple(str(column) for column in columns),
                        partial=False,
                        source=registry,
                    ),
                )

    return result


def _structured_foreign_key(
    *,
    table: str,
    value: list[Any],
    source: str,
) -> _ForeignKey:
    return _ForeignKey(
        table=table,
        name=str(value[0]),
        child_columns=tuple(str(column) for column in value[1]),
        parent_table=str(value[2]),
        parent_key=str(value[3]),
        parent_columns=tuple(str(column) for column in value[4]),
        source=source,
    )


def _foreign_keys(artifact: dict[str, Any]) -> tuple[_ForeignKey, ...]:
    result: list[_ForeignKey] = []
    for row in artifact["current_ddl_registry"]["rows"]:
        table = str(row["table_name"])
        for literal in row["foreign_key_tuple"]:
            match = _CURRENT_FK_RE.match(literal)
            assert match is not None, f"TASK7_FOREIGN_KEY_UNPARSEABLE:{literal}"
            result.append(
                _ForeignKey(
                    table=table,
                    name=match.group("name"),
                    child_columns=_columns(match.group("child")),
                    parent_table=match.group("parent_table"),
                    parent_key=match.group("parent_key"),
                    parent_columns=_columns(match.group("parent")),
                    source="current_ddl_registry",
                )
            )

    for registry, table_field, fk_field in (
        ("future_native_authority_registry", "planned_table", "reference_fields"),
        ("future_reference_authority_registry", "table_name", "foreign_key_tuple"),
        ("future_child_authority_registry", "table_name", "foreign_key_tuple"),
    ):
        for row in artifact[registry]["rows"]:
            for value in row[fk_field]:
                result.append(
                    _structured_foreign_key(
                        table=str(row[table_field]),
                        value=value,
                        source=registry,
                    )
                )
    return tuple(result)


def _fk_issues(artifact: dict[str, Any]) -> tuple[str, ...]:
    authorities = _authorities(artifact)
    issues: list[str] = []
    for foreign_key in _foreign_keys(artifact):
        coordinate = f"{foreign_key.table}.{foreign_key.name}"
        if len(foreign_key.child_columns) != len(foreign_key.parent_columns):
            issues.append(
                f"{coordinate}:arity:{foreign_key.child_columns!r}:{foreign_key.parent_columns!r}"
            )
            continue
        authority = authorities.get(foreign_key.authority_key)
        if authority is None:
            issues.append(
                f"{coordinate}:missing:{foreign_key.parent_table}."
                f"{foreign_key.parent_key}{foreign_key.parent_columns!r}"
            )
            continue
        if authority.partial:
            issues.append(f"{coordinate}:partial-parent:{authority.table}.{authority.name}")
        if authority.columns != foreign_key.parent_columns:
            issues.append(
                f"{coordinate}:parent-columns:{foreign_key.parent_columns!r}!={authority.columns!r}"
            )
    return tuple(sorted(issues))


def _walk_differences(
    left: Any,
    right: Any,
    path: tuple[object, ...] = (),
) -> list[tuple[object, ...]]:
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        if tuple(left) != tuple(right):
            return [path]
        return [
            difference
            for key in left
            for difference in _walk_differences(left[key], right[key], (*path, key))
        ]
    if isinstance(left, list):
        if len(left) != len(right):
            return [path]
        return [
            difference
            for index, (left_item, right_item) in enumerate(zip(left, right, strict=True))
            for difference in _walk_differences(
                left_item,
                right_item,
                (*path, index),
            )
        ]
    return [] if left == right else [path]


def _row_index(
    artifact: dict[str, Any],
    *,
    registry: str,
    field: str,
    value: str,
) -> int:
    matches = [index for index, row in enumerate(artifact[registry]["rows"]) if row[field] == value]
    assert len(matches) == 1
    return matches[0]


def _current_row_index(artifact: dict[str, Any], table: str) -> int:
    return _row_index(
        artifact,
        registry="current_ddl_registry",
        field="table_name",
        value=table,
    )


def _document_span_row_index(artifact: dict[str, Any]) -> int:
    return _current_row_index(artifact, "document_spans")


def _foreign_key_index(row: dict[str, Any], field: str, foreign_key_name: str) -> int:
    matches = [
        index for index, foreign_key in enumerate(row[field]) if foreign_key[0] == foreign_key_name
    ]
    assert len(matches) == 1
    return matches[0]


def _common_sections(
    artifact: dict[str, Any],
    *,
    excluding: set[str],
) -> dict[str, Any]:
    return {key: value for key, value in artifact.items() if key not in excluding}


def test_v4_reproduces_the_document_span_fk_parent_authority_mismatch() -> None:
    artifact = _load(_V4_ARTIFACT)
    row = artifact["current_ddl_registry"]["rows"][_document_span_row_index(artifact)]
    assert row["unique_tuple"][0] == _V4_SPAN_AUTHORITY

    issues = _fk_issues(artifact)
    assert (
        "generic_extraction_evidence_links.fk_v2_generic_evidence_link_span:"
        "partial-parent:document_spans.ux_document_spans_task6_scope"
    ) in issues
    assert (
        "generic_extraction_evidence_links.fk_v2_generic_evidence_link_span:"
        "parent-columns:('tenant_id', 'deal_id', 'document_id', 'span_id')!="
        "('tenant_id', 'deal_id', 'span_id')"
    ) in issues


def test_v5_structural_diff_is_exactly_the_reviewed_class_closure_delta() -> None:
    v4 = _load(_V4_ARTIFACT)
    v5 = _load(_V5_ARTIFACT)

    expected_section_order = list(v4)
    expected_section_order.insert(
        expected_section_order.index("current_ddl_registry") + 1,
        _BASELINE_AUTHORITY_SECTION,
    )
    assert tuple(v5) == tuple(expected_section_order)
    assert v5["metadata"]["section_order"] == expected_section_order

    pipeline_row_index = next(
        index
        for index, row in enumerate(v4["digest_recipe_registry"]["rows"])
        if row[0] == "pipeline_contract_hash"
    )
    allowed_paths = {
        ("metadata", "artifact_schema"),
        ("metadata", "pipeline_contract_version"),
        ("metadata", "reviewed_design_sha256"),
        ("metadata", "reviewed_design_utf8_hex"),
        ("metadata", "section_order"),
        ("digest_recipe_registry", "rows"),
        ("digest_recipe_registry", "reviewed_literal_utf8_hex"),
        ("current_ddl_registry", "expected_sha256"),
        ("future_native_authority_registry", "expected_sha256"),
        ("future_reference_authority_registry", "expected_sha256"),
    }

    span_row_index = _document_span_row_index(v4)
    assert _document_span_row_index(v5) == span_row_index
    allowed_paths.update(
        {
            ("current_ddl_registry", "rows", span_row_index, "unique_tuple", 0),
            (
                "current_ddl_registry",
                "rows",
                span_row_index,
                "reviewed_authority_utf8_hex",
            ),
        }
    )

    for table in _CURRENT_UNIQUE_ADDITIONS:
        row_index = _current_row_index(v4, table)
        assert _current_row_index(v5, table) == row_index
        allowed_paths.update(
            {
                ("current_ddl_registry", "rows", row_index, "unique_tuple"),
                (
                    "current_ddl_registry",
                    "rows",
                    row_index,
                    "reviewed_authority_utf8_hex",
                ),
            }
        )

    for table in _FUTURE_REFERENCE_TABLES:
        row_index = _row_index(
            v4,
            registry="future_reference_authority_registry",
            field="table_name",
            value=table,
        )
        assert (
            _row_index(
                v5,
                registry="future_reference_authority_registry",
                field="table_name",
                value=table,
            )
            == row_index
        )
        v4_row = v4["future_reference_authority_registry"]["rows"][row_index]
        foreign_key_index = next(
            index
            for index, foreign_key in enumerate(v4_row["foreign_key_tuple"])
            if foreign_key[2:4] == ["run_input_bindings", "uq_task7_run_input_bindings_scoped_id"]
        )
        allowed_paths.update(
            {
                (
                    "future_reference_authority_registry",
                    "rows",
                    row_index,
                    "foreign_key_tuple",
                    foreign_key_index,
                    4,
                    3,
                ),
                (
                    "future_reference_authority_registry",
                    "rows",
                    row_index,
                    "schema_sha256",
                ),
            }
        )

    for (table, foreign_key_name), _aliases in _FUTURE_NATIVE_ALIAS_RETARGETS.items():
        row_index = _row_index(
            v4,
            registry="future_native_authority_registry",
            field="planned_table",
            value=table,
        )
        assert (
            _row_index(
                v5,
                registry="future_native_authority_registry",
                field="planned_table",
                value=table,
            )
            == row_index
        )
        v4_row = v4["future_native_authority_registry"]["rows"][row_index]
        foreign_key_index = _foreign_key_index(
            v4_row,
            "reference_fields",
            foreign_key_name,
        )
        allowed_paths.update(
            {
                (
                    "future_native_authority_registry",
                    "rows",
                    row_index,
                    "reference_fields",
                    foreign_key_index,
                    3,
                ),
                (
                    "future_native_authority_registry",
                    "rows",
                    row_index,
                    "schema_sha256",
                ),
            }
        )

    v4_erasure = v4["erasure_execution_registry"]["rows"][0]
    for census_index, (table, _schema_sha256) in enumerate(
        v4_erasure["future_activation_census_tuple"]
    ):
        if table in _FUTURE_SCHEMA_HASH_CHANGED_TABLES:
            allowed_paths.add(
                (
                    "erasure_execution_registry",
                    "rows",
                    0,
                    "future_activation_census_tuple",
                    census_index,
                    1,
                )
            )
    allowed_paths.update(
        {
            (
                "erasure_execution_registry",
                "rows",
                0,
                "future_activation_census_sha256",
            ),
            ("erasure_execution_registry", "rows", 0, "expected_sha256"),
            ("erasure_execution_registry", "expected_sha256"),
            ("activation_assertions", "expected_erasure_execution_sha256"),
        }
    )

    v4_common = _common_sections(v4, excluding={_BASELINE_AUTHORITY_SECTION})
    v5_common = _common_sections(v5, excluding={_BASELINE_AUTHORITY_SECTION})
    differences = set(_walk_differences(v4_common, v5_common))
    assert differences == allowed_paths, (
        "TASK7_V5_STRUCTURAL_DELTA_DRIFT:"
        f"unexpected={sorted(differences - allowed_paths, key=str)!r}:"
        f"missing={sorted(allowed_paths - differences, key=str)!r}"
    )

    assert v5["metadata"]["artifact_schema"] == "full-strict-v5-reviewed-preimage-v1"
    assert v5["metadata"]["pipeline_contract_version"] == "full-strict-v5"
    v4_recipes = v4["digest_recipe_registry"]["rows"]
    v5_recipes = v5["digest_recipe_registry"]["rows"]
    assert v5_recipes[:pipeline_row_index] == v4_recipes[:pipeline_row_index]
    assert v5_recipes[pipeline_row_index][0] == "pipeline_contract_hash"
    assert v5_recipes[pipeline_row_index][1] == (
        '["pipeline-contract-v5","full-strict-v5",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    assert v5_recipes[pipeline_row_index + 1 : -1] == v4_recipes[pipeline_row_index + 1 :]
    assert v5_recipes[-1] == [
        "baseline_parent_authority_registry_hash",
        "lowercase_hex(SHA256(CJ(ORDERED[baseline_parent_authority_rows])))",
    ]
    span_row = v5["current_ddl_registry"]["rows"][span_row_index]
    assert span_row["unique_tuple"][0] == _V5_SPAN_AUTHORITY


def test_v5_baseline_parent_authority_catalog_is_exact_and_hash_bound() -> None:
    artifact = _load(_V5_ARTIFACT)
    section = artifact[_BASELINE_AUTHORITY_SECTION]

    assert tuple(section) == (
        "schema_version",
        "row_schema",
        "rows",
        "expected_sha256",
    )
    assert section["schema_version"] == _BASELINE_AUTHORITY_SCHEMA_VERSION
    assert section["row_schema"] == _BASELINE_AUTHORITY_ROW_SCHEMA
    assert section["rows"] == _BASELINE_AUTHORITY_ROWS
    assert section["expected_sha256"] == _h(_BASELINE_AUTHORITY_ROWS)


def test_v5_applies_all_and_only_the_reviewed_fk_authority_corrections() -> None:
    v4 = _load(_V4_ARTIFACT)
    v5 = _load(_V5_ARTIFACT)

    for table, additions in _CURRENT_UNIQUE_ADDITIONS.items():
        v4_row = v4["current_ddl_registry"]["rows"][_current_row_index(v4, table)]
        v5_row = v5["current_ddl_registry"]["rows"][_current_row_index(v5, table)]
        assert v5_row["unique_tuple"] == [*v4_row["unique_tuple"], *additions]

    corrected_reference_count = 0
    for table in _FUTURE_REFERENCE_TABLES:
        v4_row = v4["future_reference_authority_registry"]["rows"][
            _row_index(
                v4,
                registry="future_reference_authority_registry",
                field="table_name",
                value=table,
            )
        ]
        v5_row = v5["future_reference_authority_registry"]["rows"][
            _row_index(
                v5,
                registry="future_reference_authority_registry",
                field="table_name",
                value=table,
            )
        ]
        foreign_key_index = next(
            index
            for index, foreign_key in enumerate(v4_row["foreign_key_tuple"])
            if foreign_key[2:4] == ["run_input_bindings", "uq_task7_run_input_bindings_scoped_id"]
        )
        v4_foreign_key = v4_row["foreign_key_tuple"][foreign_key_index]
        v5_foreign_key = v5_row["foreign_key_tuple"][foreign_key_index]
        assert v4_foreign_key[4] == [
            "tenant_id",
            "deal_id",
            "run_id",
            "input_binding_id",
        ]
        assert v5_foreign_key[4] == [
            "tenant_id",
            "deal_id",
            "run_id",
            "binding_id",
        ]
        corrected_reference_count += 1
    assert corrected_reference_count == 15

    for (table, foreign_key_name), (v4_alias, v5_alias) in _FUTURE_NATIVE_ALIAS_RETARGETS.items():
        v4_row = v4["future_native_authority_registry"]["rows"][
            _row_index(
                v4,
                registry="future_native_authority_registry",
                field="planned_table",
                value=table,
            )
        ]
        v5_row = v5["future_native_authority_registry"]["rows"][
            _row_index(
                v5,
                registry="future_native_authority_registry",
                field="planned_table",
                value=table,
            )
        ]
        v4_foreign_key = v4_row["reference_fields"][
            _foreign_key_index(v4_row, "reference_fields", foreign_key_name)
        ]
        v5_foreign_key = v5_row["reference_fields"][
            _foreign_key_index(v5_row, "reference_fields", foreign_key_name)
        ]
        assert v4_foreign_key[3] == v4_alias
        assert v5_foreign_key[3] == v5_alias

    v4_authorities = set(_authorities(v4))
    v5_authorities = set(_authorities(v5))
    assert _V4_MISSING_UNIQUE_AUTHORITIES.isdisjoint(v4_authorities)
    assert v5_authorities >= _V4_MISSING_UNIQUE_AUTHORITIES
    assert len(_V4_MISSING_UNIQUE_AUTHORITIES) == 8


def test_v5_every_fk_resolves_an_exact_nonpartial_parent_authority() -> None:
    artifact = _load(_V5_ARTIFACT)
    assert len(_foreign_keys(artifact)) == 195
    assert len(_authorities(artifact)) == 267
    issues = _fk_issues(artifact)
    assert not issues, "TASK7_FK_PARENT_AUTHORITY_INVALID:\n" + "\n".join(issues)


def test_v5_every_unique_authority_is_used_or_in_the_finite_standalone_set() -> None:
    artifact = _load(_V5_ARTIFACT)
    authority_keys = set(_authorities(artifact))
    depended_on = {foreign_key.authority_key for foreign_key in _foreign_keys(artifact)}
    actual_standalone = {f"{table}.{name}" for table, name in sorted(authority_keys - depended_on)}

    assert actual_standalone == _STANDALONE_PARENT_AUTHORITY_MARKERS_V1, (
        "TASK7_STANDALONE_PARENT_AUTHORITY_DRIFT:"
        f"missing_markers={sorted(actual_standalone - _STANDALONE_PARENT_AUTHORITY_MARKERS_V1)!r}:"
        f"stale_markers={sorted(_STANDALONE_PARENT_AUTHORITY_MARKERS_V1 - actual_standalone)!r}"
    )


def test_v5_preserves_semantic_route_rights_and_erasure_bytes() -> None:
    v4 = _load(_V4_ARTIFACT)
    v5 = _load(_V5_ARTIFACT)
    drift = [section for section in _BYTE_STABLE_SECTIONS if v5[section] != v4[section]]
    assert not drift, f"TASK7_V5_UNAUTHORIZED_SECTION_DRIFT:{drift!r}"

    v4_erasure = v4["erasure_execution_registry"]
    v5_erasure = v5["erasure_execution_registry"]
    v4_row = v4_erasure["rows"][0]
    v5_row = v5_erasure["rows"][0]
    allowed_row_drift = {
        "future_activation_census_tuple",
        "future_activation_census_sha256",
        "expected_sha256",
    }
    assert {key for key in v4_row if v4_row[key] != v5_row[key]} == allowed_row_drift

    v4_census = v4_row["future_activation_census_tuple"]
    v5_census = v5_row["future_activation_census_tuple"]
    assert [row[0] for row in v5_census] == [row[0] for row in v4_census]
    assert {
        old[0] for old, new in zip(v4_census, v5_census, strict=True) if old != new
    } == _FUTURE_SCHEMA_HASH_CHANGED_TABLES

    v5_future_hashes = {
        str(row[table_field]): str(row["schema_sha256"])
        for registry, table_field in (
            ("future_native_authority_registry", "planned_table"),
            ("future_reference_authority_registry", "table_name"),
            ("future_child_authority_registry", "table_name"),
        )
        for row in v5[registry]["rows"]
    }
    assert v5_census == [[table, v5_future_hashes[table]] for table, _old_hash in v4_census]
    assert v5_erasure["expected_sha256"] == _h(
        ["full-strict-v4-erasure-execution-registry-v1", v5_erasure["rows"]]
    )

    v4_assertions = dict(v4["activation_assertions"])
    v5_assertions = dict(v5["activation_assertions"])
    assert v5_assertions.pop("expected_erasure_execution_sha256") == v5_row["expected_sha256"]
    v4_assertions.pop("expected_erasure_execution_sha256")
    assert v5_assertions == v4_assertions


def test_v5_preserves_reviewed_cardinalities() -> None:
    v5 = _load(_V5_ARTIFACT)
    assert len(v5) == 27
    assert len(v5["semantic_step_registry"]["rows"]) == 28
    assert len(v5["edge_rule_registry"]["rows"]) == 48
    assert len(v5["usage_route_registry"]["rows"]) == 1_756
    assert len(v5["usage_extractor_registry"]["rows"]) == 19
    assert len(v5["rights_index_policy_registry"]["rows"]) == 18
    assert len(v5["current_ddl_registry"]["rows"]) == 34
    assert len(v5[_BASELINE_AUTHORITY_SECTION]["rows"]) == 4
    assert len(v5["future_native_authority_registry"]["rows"]) == 18
    assert len(v5["future_reference_authority_registry"]["rows"]) == 15
    assert len(v5["future_child_authority_registry"]["rows"]) == 5
    assert len(v5["erasure_baseline"]["rows"]) == 59
    assert len(v5["erasure_materialized"]["rows"]) == 66
