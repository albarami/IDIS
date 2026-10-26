"""Typed, code-owned CENSUS contracts for the frozen full-strict-v21 pipeline."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, NoReturn, TypeGuard, TypeVar
from uuid import UUID

PIPELINE_CONTRACT_UNAVAILABLE: Final = "PIPELINE_CONTRACT_UNAVAILABLE"
PIPELINE_CONTRACT_HASH_MISMATCH: Final = "PIPELINE_CONTRACT_HASH_MISMATCH"
STEP_SEMANTIC_VALIDATION_FAILED: Final = "STEP_SEMANTIC_VALIDATION_FAILED"

_HASH_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_PROVIDERS: Final = (
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
_DEBATE_ROLES: Final = (
    "advocate",
    "sanad_breaker",
    "contradiction_finder",
    "risk_officer",
    "arbiter",
)
_ANALYSIS_ROLES: Final = (
    "financial_agent",
    "market_agent",
    "technical_agent",
    "terms_agent",
    "team_agent",
    "risk_officer_agent",
    "historian_agent",
    "sector_specialist_agent",
)
_LAYER2_ROLES: Final = ("ic_advocate", "ic_challenger", "ic_arbiter")
_DELIVERABLE_ARTIFACT_SUBROLES: Final = (
    "screening_snapshot_pdf",
    "screening_snapshot_docx",
    "ic_memo_pdf",
    "ic_memo_docx",
    "truth_dashboard",
    "qa_brief",
    "executive_summary",
    "commercial_diligence",
    "financial_diligence",
    "risk_register",
    "layer2_ic_challenge",
    "evidence_index",
    "run_summary",
    "provenance_appendix",
    "product_bundle_manifest",
)
_DELIVERABLE_RECEIPT_SUBROLES: Final = (
    "reconciliation",
    *_DELIVERABLE_ARTIFACT_SUBROLES,
)


class CensusValidationError(ValueError):
    """Stable fail-closed error raised for an invalid typed CENSUS."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CensusSchemaSpec:
    """One normalized CENSUS schema admitted by the v5 artifact."""

    schema_id: str
    schema_version: str
    authority_locator: str
    field_types: tuple[tuple[str, str], ...]
    required_fields: tuple[str, ...]
    collection_paths: tuple[tuple[str, str], ...]
    schema_sha256: str


@dataclass(frozen=True, slots=True)
class CensusEnvelopeV1:
    """Validated CENSUS payload bound to its reviewed schema identity."""

    schema_id: str
    schema_version: str
    schema_sha256: str
    payload: Mapping[str, Any]
    canonical_sha256: str


_SCHEMA_ROWS: Final = (
    (
        "inventory-census-v1",
        "task7-census:STATUS_ONLY_INVENTORY_V1",
        "ec465a874ae41db2f4b5031f5c4655852d11ac18b38443914020f556a49d4e4e",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("package_root", "NativeRootRefV1"),
            ("package_file_roots", "RootSet"),
            ("manifest_file_count", "Count"),
            ("ledger_file_count", "Count"),
            ("total_bytes", "Count"),
            ("evidence_bearing_count", "Count"),
            ("confirmed_exclusion_count", "Count"),
            ("missing_count", "Count"),
            ("extra_count", "Count"),
            ("deferred_count", "Count"),
            ("unresolved_count", "Count"),
        ),
        (("/input_refs", "RefSet"), ("/package_file_roots", "RootSet")),
    ),
    (
        "handoff-census-v1",
        "task7-census:STATUS_ONLY_HANDOFF_V1",
        "4942b6b04eabdd18df144b9431da109640fa00dd3c5942d6a47e9a8b23064958",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("artifact_roots", "RootSet"),
            ("document_roots", "RootSet"),
            ("attempt_roots", "RootSet"),
            ("span_roots", "RootSet"),
            (
                "terminal_class_counts",
                "Histogram<EVIDENCE_BEARING|CONFIRMED_EXCLUSION>",
            ),
            ("object_proof_count", "Count"),
            ("object_hash_mismatch_count", "Count"),
            ("member_count_mismatch_count", "Count"),
            ("unresolved_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/artifact_roots", "RootSet"),
            ("/document_roots", "RootSet"),
            ("/attempt_roots", "RootSet"),
            ("/span_roots", "RootSet"),
            (
                "/terminal_class_counts",
                "Histogram<EVIDENCE_BEARING|CONFIRMED_EXCLUSION>",
            ),
        ),
    ),
    (
        "ingest-check-census-v1",
        "task7-census:STATUS_ONLY_INGEST_CHECK_V1",
        "28516a09dbf094ce504e81f8497ebc9e814c36c5c1b48344bb6e2b49a9eeca5e",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("manifest_member_hashes", "HashSet"),
            ("inventory_binding_hashes", "HashSet"),
            ("handoff_binding_hashes", "HashSet"),
            ("symmetric_difference_sha256", "Hash"),
            ("selected_manifest_count", "Count"),
            ("inventory_count", "Count"),
            ("handoff_count", "Count"),
        ),
        (("/input_refs", "RefSet"),),
    ),
    (
        "preflight-census-v1",
        "task7-census:STATUS_ONLY_PREFLIGHT_V1",
        "0d2ccd55382f1caed55b320498a137c52cc1081bcc2fb49c12c3ff1730aca9c8",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("terminal_classification_hashes", "HashSet"),
            ("format_histogram", "Histogram<registered_format>"),
            ("status_histogram", "Histogram<registered_status>"),
            ("manifest_count", "Count"),
            ("usable_count", "Count"),
            ("governed_exclusion_count", "Count"),
            ("unresolved_count", "Count"),
            ("password_count", "Count"),
            ("corrupt_count", "Count"),
            ("unsupported_count", "Count"),
            ("oversize_count", "Count"),
            ("ocr_pending_count", "Count"),
            ("media_pending_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/format_histogram", "Histogram<registered_format>"),
            ("/status_histogram", "Histogram<registered_status>"),
        ),
    ),
    (
        "coverage-census-v1",
        "task7-census:METHODOLOGY_COVERAGE_RECEIPT_V1",
        "dc20c2b5897902c259429b26b6a1ce9564eeecdc0f8140353d373ee9788b1f2f",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("selection_id", "Id"),
            ("selection_hash", "Hash"),
            ("cdd_version_hash", "Hash"),
            ("fdd_version_hash", "Hash"),
            ("coverage_roots", "RootSet"),
            ("commercial_count", "Count"),
            ("financial_count", "Count"),
            ("total_count", "Count"),
            ("applicability", "Histogram<APPLICABLE|NOT_APPLICABLE>"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/coverage_roots", "RootSet"),
            ("/applicability", "Histogram<APPLICABLE|NOT_APPLICABLE>"),
        ),
    ),
    (
        "task-plan-census-v1",
        "task7-census:METHODOLOGY_TASK_PLAN_RECEIPT_V1",
        "aafe880fb3628c855d303f213aee0fdde36c3b2e6910d23b165c4447f37f1f12",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("applicable_coverage_roots", "RootSet"),
            ("task_roots", "RootSet"),
            (
                "task_span_refs",
                "Ordered<(task_id,span_ordinal,span_id,content_sha256,locator_sha256),"
                "(task_id,span_ordinal,span_id)>",
            ),
            ("applicable_count", "Count"),
            ("planned_count", "Count"),
            ("missing_count", "Count"),
            ("duplicate_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/applicable_coverage_roots", "RootSet"),
            ("/task_roots", "RootSet"),
            (
                "/task_span_refs",
                "Ordered<(task_id,span_ordinal,span_id,content_sha256,locator_sha256),"
                "(task_id,span_ordinal,span_id)>",
            ),
        ),
    ),
    (
        "methodology-execution-census-v1",
        "task7-census:METHODOLOGY_EXECUTION_RECEIPT_V1",
        "9c52ece5cc54f31ca2fe2d64983f7e69b09f081ffd506236d5f4bbb1165f5409",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("result_roots", "RootSet"),
            ("factual_roots", "RootSet"),
            ("attempt_receipt_hashes", "HashSet"),
            ("provider_provenance_hashes", "HashSet"),
            ("budget_hashes", "HashSet"),
            ("applicable_task_count", "Count"),
            ("terminal_result_count", "Count"),
            ("evidence_not_found_count", "Count"),
            ("failed_count", "Count"),
            ("partial_count", "Count"),
            ("deferred_count", "Count"),
            ("unknown_count", "Count"),
            ("missing_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/result_roots", "RootSet"),
            ("/factual_roots", "RootSet"),
        ),
    ),
    (
        "claim-materialization-census-v1",
        "task7-census:METHODOLOGY_CLAIM_RECEIPT_V1",
        "11b4396b5ba19a2a8fbfd246a612cf71e3a16d7e450fcd3467c5cd9d8ba9eecb",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("claim_link_roots", "RootSet"),
            ("accepted_result_factual_keys", "SET[(result_id,factual_id)]"),
            ("canonical_claim_ids", "IdSet"),
            ("reused_claim_ids", "IdSet"),
            ("accepted_count", "Count"),
            ("rejected_count", "Count"),
            ("missing_count", "Count"),
            ("duplicate_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/claim_link_roots", "RootSet"),
            ("/accepted_result_factual_keys", "SET[(result_id,factual_id)]"),
        ),
    ),
    (
        "evidence-materialization-census-v1",
        "task7-census:METHODOLOGY_EVIDENCE_RECEIPT_V1",
        "fb34ab7fff34a6011e272f8403f8bd7dc1fc94fe504fe5d7e14d8f0622518dd5",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("evidence_link_roots", "RootSet"),
            ("factual_source_keys", "SET[(factual_id,span_id,locator_sha256)]"),
            ("evidence_ids", "IdSet"),
            ("expected_source_count", "Count"),
            ("resolved_source_count", "Count"),
            ("unresolved_reference_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/evidence_link_roots", "RootSet"),
            ("/factual_source_keys", "SET[(factual_id,span_id,locator_sha256)]"),
        ),
    ),
    (
        "sanad-materialization-census-v1",
        "task7-census:METHODOLOGY_SANAD_RECEIPT_V1",
        "f2cd5f2412fd92c2e0a2b7364c2093ce540ff39d2a76eefa495edd81b76d09ff",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("sanad_link_roots", "RootSet"),
            ("defect_link_roots", "RootSet"),
            ("applicable_claim_ids", "IdSet"),
            ("graded_claim_ids", "IdSet"),
            ("grade_histogram", "Histogram<A|B|C|D>"),
            ("failed_count", "Count"),
            ("partial_count", "Count"),
            ("unlinked_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/sanad_link_roots", "RootSet"),
            ("/defect_link_roots", "RootSet"),
            ("/grade_histogram", "Histogram<A|B|C|D>"),
        ),
    ),
    (
        "calculation-census-v1",
        "task7-census:METHODOLOGY_CALC_RECEIPT_V1",
        "1540d0d99e61796403575fdded0cafa8d9292e4a77409c9a25c01d1750f11f60",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("calculation_roots", "RootSet"),
            ("calc_sanad_hashes", "HashSet"),
            ("required_formula_keys", "SET[(calc_type,formula_hash)]"),
            ("materialized_formula_keys", "SET[(calc_type,formula_hash)]"),
            ("blocked_count", "Count"),
            ("missing_count", "Count"),
            ("reproducibility_mismatch_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/calculation_roots", "RootSet"),
            ("/required_formula_keys", "SET[(calc_type,formula_hash)]"),
            ("/materialized_formula_keys", "SET[(calc_type,formula_hash)]"),
        ),
    ),
    (
        "dashboard-census-v1",
        "task7-census:METHODOLOGY_DASHBOARD_RECEIPT_V1",
        "f15a5e534ad0cd7fca415118981fd03c94026dea1dc27c95e4783493df8e4a33",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("dashboard_roots", "RootSet"),
            (
                "row_linkage",
                "Ordered<(row_id,coverage_root,result_root,evidence_root,sanad_root,calc_root),"
                "row_id>",
            ),
            ("expected_row_count", "Count"),
            ("materialized_row_count", "Count"),
            ("missing_count", "Count"),
            ("partial_count", "Count"),
            ("rejected_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/dashboard_roots", "RootSet"),
            (
                "/row_linkage",
                "Ordered<(row_id,coverage_root,result_root,evidence_root,sanad_root,calc_root),"
                "row_id>",
            ),
        ),
    ),
    (
        "extract-rag-context-census-v1",
        "task7-census:EXTRACT_RAG_CONTEXT_RECEIPT_V1",
        "be74606fa3de72bf056515c04139bda643c38e9d2b1a699cd5d59886763b69b2",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("preindex_roots", "RootSet"),
            ("query_roots", "RootSet"),
            ("result_roots", "RootSet"),
            ("source_refs", "RefSet"),
            ("retrieved_ref_ids", "IdSet"),
            ("failed_count", "Count"),
            ("missing_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/preindex_roots", "RootSet"),
            ("/query_roots", "RootSet"),
            ("/result_roots", "RootSet"),
            ("/source_refs", "RefSet"),
        ),
    ),
    (
        "extract-census-v1",
        "task7-census:EXTRACT_RECEIPT_V1",
        "2c35a89fc5dbce5b7c096ab27b09333270311c87bbbc00929fb3191823bf8984",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("generic_output_roots", "RootSet"),
            ("methodology_claim_ids", "IdSet"),
            ("generic_new_claim_ids", "IdSet"),
            ("reused_claim_ids", "IdSet"),
            ("provider_hash", "Hash"),
            ("prompt_hash", "Hash"),
            ("request_hash", "Hash"),
            ("scope_hash", "Hash"),
            ("governed_result_count", "Count"),
            ("failed_count", "Count"),
            ("partial_count", "Count"),
        ),
        (("/input_refs", "RefSet"), ("/generic_output_roots", "RootSet")),
    ),
    (
        "grade-census-v1",
        "task7-census:GRADE_RECEIPT_V1",
        "d88f68cfd336ea1212ee3096c28526e1eeae9b7057fd4d7253a6facb9140ca50",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("grading_link_roots", "RootSet"),
            ("canonical_claim_union", "IdSet"),
            ("graded_claim_ids", "IdSet"),
            ("failed_count", "Count"),
            ("missing_count", "Count"),
        ),
        (("/input_refs", "RefSet"), ("/grading_link_roots", "RootSet")),
    ),
    (
        "identity-census-v1",
        "task7-census:COMPANY_IDENTITY_RECEIPT_V1",
        "9225a46cffcc683f92065b36c704dcb1797b531f4684a8b2adfbda47af2c84e9",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("identity_roots", "RootSet"),
            ("evidence_input_refs", "RefSet"),
            ("calculation_input_refs", "RefSet"),
            ("identity_version_hash", "Hash"),
            ("verified_identifier_keys", "SET[(identifier_kind,identifier_hash)]"),
            (
                "provider_applicability",
                "KeyMap<provider_id,APPLICABLE|NOT_APPLICABLE>",
            ),
            ("unresolved_mandatory_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/identity_roots", "RootSet"),
            ("/evidence_input_refs", "RefSet"),
            ("/calculation_input_refs", "RefSet"),
            ("/verified_identifier_keys", "SET[(identifier_kind,identifier_hash)]"),
            (
                "/provider_applicability",
                "KeyMap<provider_id,APPLICABLE|NOT_APPLICABLE>",
            ),
        ),
    ),
    (
        "provider-outcome-census-v1",
        "task7-census:ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1",
        "4247efef5befa3d030e688f9f2b1cd3dfdb822fdac7f3988dace705425763b5b",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("provider_id", "provider_id"),
            ("outcome", "HIT|MISS|NOT_APPLICABLE"),
            ("identity_hash", "Hash"),
            ("applicability_decision_id", "Id"),
            ("applicability_evidence_sha256", "Hash"),
            ("rights_approval_id", "IdOrNull"),
            ("rights_version_hash", "HashOrNull"),
            ("rights_class", "GREEN|YELLOW|RED|null"),
            ("no_store_policy_version", "TextOrNull"),
            ("attribution_policy_version", "TextOrNull"),
            ("external_context_index_allowed", "Boolean"),
            (
                "outcome_hash_recipe_version",
                "Literal[enrichment-provider-outcome-sha256-v1]",
            ),
            ("outcome_sha256", "Hash"),
            (
                "native_output_recipe_version",
                "Literal[provider-outcome-native-output-v1]",
            ),
            ("native_output_sha256", "Hash"),
            ("budget_hash", "Hash"),
            ("probe_hash", "Hash"),
            ("request_hash", "Hash"),
            ("provenance_hash", "Hash"),
            ("fact_roots", "RootSet"),
            ("error_count", "Count"),
            ("blocked_count", "Count"),
            ("deferred_count", "Count"),
        ),
        (("/input_refs", "RefSet"), ("/fact_roots", "RootSet")),
    ),
    (
        "enrichment-census-v1",
        "task7-census:ENRICHMENT_RECEIPT_V1",
        "d9866faa3cb767ff304efe6935d02cfb135ddcf2a739348af821d49e8097b8ba",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("provider_receipts", "KeyMap<provider_id,SubroleReceiptRefV1>"),
            ("outcome_histogram", "Histogram<HIT|MISS|NOT_APPLICABLE>"),
            ("error_count", "Count"),
            ("blocked_count", "Count"),
            ("deferred_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/provider_receipts", "KeyMap<provider_id,SubroleReceiptRefV1>"),
            ("/outcome_histogram", "Histogram<HIT|MISS|NOT_APPLICABLE>"),
        ),
    ),
    (
        "graph-namespace-census-v1",
        "task7-census:GRAPH_NAMESPACE_RECEIPT_V1",
        "ee08df8acd45c58ffa8fc696ab8e0974683bab685383d42b6691cacd9157babd",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("namespace", "internal_evidence|external_context"),
            ("projection_roots", "RootSet"),
            ("query_roots", "RootSet"),
            ("result_roots", "RootSet"),
            ("producer_refs", "RefSet"),
            ("node_count", "Count"),
            ("edge_count", "Count"),
            ("query_count", "Count"),
            ("result_ref_count", "Count"),
            ("failed_count", "Count"),
            ("missing_count", "Count"),
            ("cross_namespace_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/projection_roots", "RootSet"),
            ("/query_roots", "RootSet"),
            ("/result_roots", "RootSet"),
            ("/producer_refs", "RefSet"),
        ),
    ),
    (
        "graph-census-v1",
        "task7-census:GRAPH_RECEIPT_V1",
        "6a6355aa4ca2c4428bf27ff06406df6c1dcd5c5cd6a04d70f4ae369148b08818",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            (
                "namespace_receipts",
                "KeyMap<internal_evidence|external_context,SubroleReceiptRefV1>",
            ),
            ("projection_roots", "RootSet"),
            ("query_roots", "RootSet"),
            ("result_roots", "RootSet"),
            ("failed_count", "Count"),
            ("missing_count", "Count"),
            ("cross_namespace_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            (
                "/namespace_receipts",
                "KeyMap<internal_evidence|external_context,SubroleReceiptRefV1>",
            ),
            ("/projection_roots", "RootSet"),
            ("/query_roots", "RootSet"),
            ("/result_roots", "RootSet"),
        ),
    ),
    (
        "rag-namespace-census-v1",
        "task7-census:RAG_NAMESPACE_RECEIPT_V1",
        "332c703377ab9505f7bf91f8869a897b62b532d0b24696cf079fa6d4d4a26834",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("namespace", "internal_evidence|external_context"),
            ("embedding_roots", "RootSet"),
            ("index_roots", "RootSet"),
            ("query_roots", "RootSet"),
            ("result_roots", "RootSet"),
            ("producer_refs", "RefSet"),
            ("index_source_count", "Count"),
            ("retrieved_ref_count", "Count"),
            ("failed_count", "Count"),
            ("missing_count", "Count"),
            ("cross_namespace_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/embedding_roots", "RootSet"),
            ("/index_roots", "RootSet"),
            ("/query_roots", "RootSet"),
            ("/result_roots", "RootSet"),
            ("/producer_refs", "RefSet"),
        ),
    ),
    (
        "rag-census-v1",
        "task7-census:RAG_RECEIPT_V1",
        "93965c93fa2b6b5bb917e5ce5a988b04f0d8b98c7c0d731bbc1680b3c95e8ff7",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            (
                "namespace_receipts",
                "KeyMap<internal_evidence|external_context,SubroleReceiptRefV1>",
            ),
            ("embedding_roots", "RootSet"),
            ("index_roots", "RootSet"),
            ("query_roots", "RootSet"),
            ("result_roots", "RootSet"),
            ("failed_count", "Count"),
            ("missing_count", "Count"),
            ("cross_namespace_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            (
                "/namespace_receipts",
                "KeyMap<internal_evidence|external_context,SubroleReceiptRefV1>",
            ),
            ("/embedding_roots", "RootSet"),
            ("/index_roots", "RootSet"),
            ("/query_roots", "RootSet"),
            ("/result_roots", "RootSet"),
        ),
    ),
    (
        "conflict-census-v1",
        "task7-census:CONFLICT_CHECK_RECEIPT_V1",
        "5d71363fa8bf5cbbe81af69cf71382f142a823917a36e74bc7ca6a272845ff27",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("plan_roots", "RootSet"),
            ("execution_roots", "RootSet"),
            ("finding_roots", "RootSet"),
            ("applicable_check_keys", "SET[(check_kind,subject_key)]"),
            ("executed_check_keys", "SET[(check_kind,subject_key)]"),
            (
                "evidence_class_histogram",
                "Histogram<INTERNAL_EVIDENCE|EXTERNAL_CONTEXT>",
            ),
            ("planned_count", "Count"),
            ("blocked_count", "Count"),
            ("deferred_count", "Count"),
            ("unavailable_count", "Count"),
            ("no_op_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/plan_roots", "RootSet"),
            ("/execution_roots", "RootSet"),
            ("/finding_roots", "RootSet"),
            ("/applicable_check_keys", "SET[(check_kind,subject_key)]"),
            ("/executed_check_keys", "SET[(check_kind,subject_key)]"),
            (
                "/evidence_class_histogram",
                "Histogram<INTERNAL_EVIDENCE|EXTERNAL_CONTEXT>",
            ),
        ),
    ),
    (
        "court-census-v1",
        "task7-census:EVIDENCE_TRUST_COURT_RECEIPT_V1",
        "a93bba63c770ae2e1632f6eaed7a6199ecfd6a50afa90f91874e004eac9bc945",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("finding_roots", "RootSet"),
            ("muhasabah_roots", "RootSet"),
            ("internal_input_refs", "RefSet"),
            ("assessed_input_refs", "RefSet"),
            ("terminal_record_count", "Count"),
            ("partial_count", "Count"),
            ("rejected_count", "Count"),
            ("external_context_reference_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/finding_roots", "RootSet"),
            ("/muhasabah_roots", "RootSet"),
            ("/internal_input_refs", "RefSet"),
            ("/assessed_input_refs", "RefSet"),
        ),
    ),
    (
        "vep-census-v1",
        "task7-census:VEP_RECEIPT_V1",
        "4e4db535d772b3d9adfcad0b7371be096b37c26ac31330ad4cbc54d909ea5360",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("vep_roots", "RootSet"),
            ("content_reference_roots", "RootSet"),
            ("claim_ids", "IdSet"),
            ("evidence_ids", "IdSet"),
            ("sanad_ids", "IdSet"),
            ("calc_ids", "IdSet"),
            ("court_finding_ids", "TextIdSet"),
            ("content_count", "Count"),
            ("unresolved_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/vep_roots", "RootSet"),
            ("/content_reference_roots", "RootSet"),
        ),
    ),
    (
        "debate-role-census-v1",
        "task7-census:DEBATE_ROLE_RECEIPT_V1",
        "46db2572f1ed87f17d30bc4321bf049e5e5e443583693fd669db74bff1bc7491",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("role", "debate_role"),
            ("role_output_roots", "RootSet"),
            ("request_hash", "Hash"),
            ("prompt_hash", "Hash"),
            ("retrieval_refs", "RefSet"),
            ("muhasabah_roots", "RootSet"),
            ("round_numbers", "SET[Count]"),
            ("failed_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/role_output_roots", "RootSet"),
            ("/retrieval_refs", "RefSet"),
            ("/muhasabah_roots", "RootSet"),
            ("/round_numbers", "SET[Count]"),
        ),
    ),
    (
        "debate-census-v1",
        "task7-census:DEBATE_RECEIPT_V1",
        "42f0525a94cab85858bc69c2616959205fff7276640f5050b7cf4a546b300c36",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("debate_session_roots", "RootSet"),
            ("role_receipts", "KeyMap<debate_role,SubroleReceiptRefV1>"),
            ("round_digest", "Hash"),
            ("dissent_digest", "Hash"),
            ("missing_role_count", "Count"),
            ("failed_role_count", "Count"),
            ("external_context_reference_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/debate_session_roots", "RootSet"),
            ("/role_receipts", "KeyMap<debate_role,SubroleReceiptRefV1>"),
        ),
    ),
    (
        "analysis-role-census-v1",
        "task7-census:ANALYSIS_AGENT_RECEIPT_V1",
        "a7e7ca3b457104e94e2a5666de5b8999130d0e9f772941b9cc3f35118ea9275c",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("role", "analysis_role"),
            ("agent_output_roots", "RootSet"),
            ("muhasabah_roots", "RootSet"),
            ("request_hash", "Hash"),
            ("prompt_hash", "Hash"),
            ("provider_refs", "RefSet"),
            ("failed_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/agent_output_roots", "RootSet"),
            ("/muhasabah_roots", "RootSet"),
            ("/input_refs", "RefSet"),
            ("/provider_refs", "RefSet"),
        ),
    ),
    (
        "analysis-census-v1",
        "task7-census:ANALYSIS_RECEIPT_V1",
        "6517bc7f144073cee6fc09519c8cec29e597ed1e4973be624b002483b175d06d",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("agent_receipts", "KeyMap<analysis_role,SubroleReceiptRefV1>"),
            ("report_bundle_roots", "RootSet"),
            ("muhasabah_roots", "RootSet"),
            ("missing_role_count", "Count"),
            ("failed_role_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/agent_receipts", "KeyMap<analysis_role,SubroleReceiptRefV1>"),
            ("/report_bundle_roots", "RootSet"),
            ("/muhasabah_roots", "RootSet"),
        ),
    ),
    (
        "scoring-census-v1",
        "task7-census:SCORING_RECEIPT_V1",
        "4edf20a3678fc3140597db18264eaf143e7f5d3b4832960ce9a0e7929e73246f",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("scorecard_roots", "RootSet"),
            ("request_hash", "Hash"),
            ("dimension_keys", "SET[str]"),
            ("validated_dimension_keys", "SET[str]"),
            ("validation_succeeded", "bool"),
            ("missing_dimension_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/scorecard_roots", "RootSet"),
            ("/input_refs", "RefSet"),
            ("/dimension_keys", "SET[str]"),
            ("/validated_dimension_keys", "SET[str]"),
        ),
    ),
    (
        "readiness-census-v1",
        "task7-census:LAYER2_READINESS_RECEIPT_V1",
        "1be3c2eede21d2fcae74a65bcd13187ead220c7ccb8edf75290a0504215a605d",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("readiness_roots", "RootSet"),
            ("status", "READY"),
            ("required_upstream", "KeyMap<(step,subrole),ProducerRefV1>"),
            ("observed_upstream", "KeyMap<(step,subrole),ProducerRefV1>"),
            ("blocker_count", "Count"),
            ("deferred_count", "Count"),
            ("default_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/readiness_roots", "RootSet"),
            ("/required_upstream", "KeyMap<(step,subrole),ProducerRefV1>"),
            ("/observed_upstream", "KeyMap<(step,subrole),ProducerRefV1>"),
        ),
    ),
    (
        "layer2-role-census-v1",
        "task7-census:LAYER2_ROLE_RECEIPT_V1",
        "6cc4786ec9f7cec4446d4313e81faa49208600e69b3f37271e3cf8683361778f",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("role", "layer2_role"),
            ("role_output_roots", "RootSet"),
            ("request_hash", "Hash"),
            ("prompt_hash", "Hash"),
            ("muhasabah_roots", "RootSet"),
            ("unsupported_reference_count", "Count"),
            ("missing_reference_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/role_output_roots", "RootSet"),
            ("/input_refs", "RefSet"),
            ("/muhasabah_roots", "RootSet"),
        ),
    ),
    (
        "layer2-census-v1",
        "task7-census:LAYER2_CHALLENGE_RECEIPT_V1",
        "12b2594c1a4f28711ee55567beb4375462aa5b4a5b33cffeab4948cadbbe73c3",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("challenge_roots", "RootSet"),
            ("finding_roots", "RootSet"),
            ("role_receipts", "KeyMap<layer2_role,SubroleReceiptRefV1>"),
            ("dissent_digest", "Hash"),
            ("unsupported_reference_count", "Count"),
            ("missing_reference_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/challenge_roots", "RootSet"),
            ("/finding_roots", "RootSet"),
            ("/role_receipts", "KeyMap<layer2_role,SubroleReceiptRefV1>"),
        ),
    ),
    (
        "deliverable-reconciliation-census-v1",
        "task7-census:DELIVERABLE_RECONCILIATION_RECEIPT_V1",
        "3eb8c04e1405bfa36dedb5cf8b89d52a079981bee6d0fd377158f5e90b698fe1",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("inventory_output_ref", "ProducerRefV1"),
            ("package_binding_refs", "RefSet"),
            ("artifact_catalog_hash", "Hash"),
            ("artifact_catalog_count", "Count"),
            ("artifact_package_refs", "KeyMap<deliverable_artifact_subrole,RefSet>"),
            ("product_provenance_package_refs", "RefSet"),
            ("manifest_projection_sha256", "Hash"),
            ("missing_package_ref_count", "Count"),
            ("hash_mismatch_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/package_binding_refs", "RefSet"),
            ("/artifact_package_refs", "KeyMap<deliverable_artifact_subrole,RefSet>"),
            ("/product_provenance_package_refs", "RefSet"),
        ),
    ),
    (
        "deliverable-artifact-census-v1",
        "task7-census:DELIVERABLE_ARTIFACT_RECEIPT_V1",
        "3011eac71f9906304db52d85172a1271b0d201067f6cc293222b6d37c5e7da3a",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            ("catalog_ordinal", "Count"),
            ("deliverable_type", "str"),
            ("format", "PDF|DOCX|JSON"),
            ("deliverable_roots", "RootSet"),
            ("content_type", "str"),
            ("size_bytes", "Count"),
            ("content_sha256", "Hash"),
            ("uri_sha256", "Hash"),
            ("object_version_hash", "Hash"),
            ("download_proof_hash", "Hash"),
            ("manifest_reference_roots", "RootSet"),
            ("nff_succeeded", "bool"),
        ),
        (
            ("/input_refs", "RefSet"),
            ("/deliverable_roots", "RootSet"),
            ("/input_refs", "RefSet"),
            ("/manifest_reference_roots", "RootSet"),
        ),
    ),
    (
        "product-bundle-census-v1",
        "task7-census:PRODUCT_BUNDLE_RECEIPT_V1",
        "425f001b7faca7dff912415bed8a8be0f61a953fd8edb6a6f1fc469780c66f74",
        (
            ("schema_version", "Literal[1]"),
            ("input_refs", "RefSet"),
            (
                "receipt_subroles",
                "KeyMap<deliverable_receipt_subrole,SubroleReceiptRefV1>",
            ),
            (
                "artifact_entries",
                "Ordered<(catalog_ordinal,deliverable_type,format,content_sha256,size_bytes,"
                "uri_sha256,object_version_hash),catalog_ordinal>",
            ),
            ("manifest_reference_roots", "RootSet"),
            ("manifest_projection_sha256", "Hash"),
            ("manifest_member_ordinals", "SET[Count]"),
            ("manifest_ordinal", "Count"),
            ("durable_export", "bool"),
            ("nff_succeeded", "bool"),
            ("missing_count", "Count"),
            ("extra_count", "Count"),
            ("failed_count", "Count"),
        ),
        (
            ("/input_refs", "RefSet"),
            (
                "/receipt_subroles",
                "KeyMap<deliverable_receipt_subrole,SubroleReceiptRefV1>",
            ),
            (
                "/artifact_entries",
                "Ordered<(catalog_ordinal,deliverable_type,format,content_sha256,size_bytes,"
                "uri_sha256,object_version_hash),catalog_ordinal>",
            ),
            ("/manifest_reference_roots", "RootSet"),
            ("/manifest_member_ordinals", "SET[Count]"),
        ),
    ),
)

CENSUS_SCHEMA_REGISTRY: Final[Mapping[str, CensusSchemaSpec]] = MappingProxyType(
    {
        schema_id: CensusSchemaSpec(
            schema_id=schema_id,
            schema_version="1",
            authority_locator=authority_locator,
            field_types=field_types,
            required_fields=tuple(field_name for field_name, _ in field_types),
            collection_paths=collection_paths,
            schema_sha256=schema_sha256,
        )
        for (
            schema_id,
            authority_locator,
            schema_sha256,
            field_types,
            collection_paths,
        ) in _SCHEMA_ROWS
    }
)


_T = TypeVar("_T")


def _fail(
    message: str,
    *,
    code: str = STEP_SEMANTIC_VALIDATION_FAILED,
) -> NoReturn:
    raise CensusValidationError(code, message)


def _is_sequence(value: object) -> TypeGuard[Sequence[Any]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _canonical_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            unicodedata.normalize("NFC", str(key)): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if _is_sequence(value):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _canonical_json_value(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("CENSUS value is not canonical-JSON encodable")
        raise AssertionError("unreachable") from exc


def _validated_hash(value: object, path: str) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        _fail(f"{path} must be a lowercase SHA-256")
    return value


def _validated_id(value: object, path: str) -> str:
    if not isinstance(value, str):
        _fail(f"{path} must be lowercase UUID text")
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        _fail(f"{path} must be lowercase UUID text")
        raise AssertionError("unreachable") from exc
    if str(parsed) != value:
        _fail(f"{path} must be canonical lowercase UUID text")
    return value


def _validated_text(value: object, path: str, *, text_id: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{path} must be nonblank text without surrounding whitespace")
    if unicodedata.normalize("NFC", value) != value or any(
        unicodedata.category(character).startswith("C") for character in value
    ):
        _fail(f"{path} must be NFC text without control characters")
    if text_id and len(value) > 255:
        _fail(f"{path} exceeds the TextId limit")
    return value


def _validated_count(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"{path} must be a non-negative integer")
    return value


def _validated_record(
    value: object,
    path: str,
    *,
    fields: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{path} must be an object")
    expected = tuple(field_name for field_name, _ in fields)
    if set(value) != set(expected):
        _fail(f"{path} has missing or unknown fields")
    return {
        field_name: _validate_type(value[field_name], type_expression, f"{path}/{field_name}")
        for field_name, type_expression in fields
    }


def _validated_producer_ref(value: object, path: str) -> dict[str, Any]:
    record = _validated_record(
        value,
        path,
        fields=(
            ("producer_type", "INPUT_BINDING|OUTPUT_OBJECT"),
            ("producer_id", "Id"),
            ("producer_identity_sha256", "Hash"),
            ("producer_value_sha256", "Hash"),
        ),
    )
    return record


def _validated_native_root(value: object, path: str) -> dict[str, Any]:
    return _validated_record(
        value,
        path,
        fields=(
            ("output_kind", "str"),
            ("native_identity_sha256", "Hash"),
            ("native_output_sha256", "Hash"),
            ("output_object_id", "Id"),
        ),
    )


def _validated_subrole_receipt(value: object, path: str) -> dict[str, Any]:
    return _validated_record(
        value,
        path,
        fields=(
            ("subrole", "str"),
            ("native_identity_sha256", "Hash"),
            ("native_output_sha256", "Hash"),
        ),
    )


def _sorted_unique(values: Sequence[_T], path: str) -> tuple[_T, ...]:
    normalized = tuple(values)
    keys = tuple(_canonical_json_bytes(item) for item in normalized)
    if len(set(keys)) != len(keys):
        _fail(f"{path} contains a duplicate set member")
    return tuple(
        item for _, item in sorted(zip(keys, normalized, strict=True), key=lambda pair: pair[0])
    )


def _validated_set(
    value: object,
    path: str,
    *,
    item_validator: Any,
) -> tuple[object, ...]:
    if not _is_sequence(value):
        _fail(f"{path} must be an array")
    return _sorted_unique(
        tuple(item_validator(item, f"{path}/{index}") for index, item in enumerate(value)),
        path,
    )


def _key_domain(type_expression: str) -> tuple[str, ...] | None:
    if type_expression.startswith("KeyMap<provider_id,"):
        return _PROVIDERS
    if type_expression.startswith("KeyMap<debate_role,"):
        return _DEBATE_ROLES
    if type_expression.startswith("KeyMap<analysis_role,"):
        return _ANALYSIS_ROLES
    if type_expression.startswith("KeyMap<layer2_role,"):
        return _LAYER2_ROLES
    if type_expression.startswith("KeyMap<internal_evidence|external_context,"):
        return ("internal_evidence", "external_context")
    if type_expression.startswith("KeyMap<deliverable_artifact_subrole,"):
        return _DELIVERABLE_ARTIFACT_SUBROLES
    if type_expression.startswith("KeyMap<deliverable_receipt_subrole,"):
        return _DELIVERABLE_RECEIPT_SUBROLES
    return None


def _validate_key_map(value: object, type_expression: str, path: str) -> tuple[object, ...]:
    if not _is_sequence(value):
        _fail(f"{path} must be a key-map array")
    value_type = type_expression.rsplit(",", 1)[1].removesuffix(">")
    normalized: list[tuple[object, object]] = []
    for index, item in enumerate(value):
        if not _is_sequence(item) or len(item) != 2:
            _fail(f"{path}/{index} must be a key/value pair")
        key = item[0]
        if isinstance(key, str):
            key = _validated_text(key, f"{path}/{index}/0")
        elif _is_sequence(key):
            key = tuple(
                _validated_text(member, f"{path}/{index}/0/{member_index}")
                for member_index, member in enumerate(key)
            )
        else:
            _fail(f"{path}/{index}/0 must be a text key or tuple")
        item_value: object
        if value_type == "SubroleReceiptRefV1":
            item_value = _validated_subrole_receipt(item[1], f"{path}/{index}/1")
        elif value_type == "ProducerRefV1":
            item_value = _validated_producer_ref(item[1], f"{path}/{index}/1")
        elif value_type == "RefSet":
            item_value = _validate_type(item[1], "RefSet", f"{path}/{index}/1")
        elif value_type == "APPLICABLE|NOT_APPLICABLE":
            item_value = _validate_type(
                item[1],
                "APPLICABLE|NOT_APPLICABLE",
                f"{path}/{index}/1",
            )
        else:
            _fail(f"{path} uses an unsupported reviewed key-map value type")
        normalized.append((key, item_value))
    sorted_pairs = _sorted_unique(tuple(normalized), path)
    domain = _key_domain(type_expression)
    if domain is not None and {pair[0] for pair in sorted_pairs} != set(domain):
        _fail(f"{path} does not contain the exact reviewed key domain")
    return sorted_pairs


def _validate_histogram(value: object, type_expression: str, path: str) -> tuple[object, ...]:
    if not _is_sequence(value):
        _fail(f"{path} must be a histogram array")
    pairs: list[tuple[str, int]] = []
    for index, item in enumerate(value):
        if not _is_sequence(item) or len(item) != 2:
            _fail(f"{path}/{index} must be a histogram pair")
        pairs.append(
            (
                _validated_text(item[0], f"{path}/{index}/0"),
                _validated_count(item[1], f"{path}/{index}/1"),
            )
        )
    sorted_pairs = _sorted_unique(tuple(pairs), path)
    domain = type_expression.removeprefix("Histogram<").removesuffix(">")
    if "|" in domain and {pair[0] for pair in sorted_pairs} != set(domain.split("|")):
        _fail(f"{path} does not contain the exact reviewed histogram domain")
    if not sorted_pairs:
        _fail(f"{path} must contain at least one histogram key")
    return sorted_pairs


def _validate_tuple_set(value: object, type_expression: str, path: str) -> tuple[object, ...]:
    if not _is_sequence(value):
        _fail(f"{path} must be a set array")
    field_list = type_expression.removeprefix("SET[(").removesuffix(")]").split(",")
    normalized: list[tuple[object, ...]] = []
    for index, item in enumerate(value):
        if not _is_sequence(item) or len(item) != len(field_list):
            _fail(f"{path}/{index} has the wrong tuple arity")
        members: list[object] = []
        for member_index, (field_name, member) in enumerate(zip(field_list, item, strict=True)):
            member_path = f"{path}/{index}/{member_index}"
            if field_name.endswith("_id"):
                members.append(_validated_id(member, member_path))
            elif field_name.endswith("_hash") or field_name.endswith("_sha256"):
                members.append(_validated_hash(member, member_path))
            else:
                members.append(_validated_text(member, member_path))
        normalized.append(tuple(members))
    return _sorted_unique(tuple(normalized), path)


def _validate_ordered(value: object, type_expression: str, path: str) -> tuple[object, ...]:
    if not _is_sequence(value):
        _fail(f"{path} must be an ordered array")
    normalized: list[tuple[object, ...]] = []
    for index, item in enumerate(value):
        if not _is_sequence(item):
            _fail(f"{path}/{index} must be an ordered tuple")
        normalized.append(tuple(_canonical_json_value(member) for member in item))
    keys: tuple[Any, ...]
    if type_expression.endswith((",catalog_ordinal>", ",row_id>")):
        keys = tuple(item[0] for item in normalized)
    else:
        keys = tuple(item[:3] for item in normalized)
    if tuple(keys) != tuple(sorted(keys)):
        _fail(f"{path} is not in the reviewed total order")
    return tuple(normalized)


def _validate_type(value: object, type_expression: str, path: str) -> object:
    if type_expression == "Count":
        return _validated_count(value, path)
    if type_expression == "Hash":
        return _validated_hash(value, path)
    if type_expression == "HashOrNull":
        return None if value is None else _validated_hash(value, path)
    if type_expression == "Id":
        return _validated_id(value, path)
    if type_expression == "IdOrNull":
        return None if value is None else _validated_id(value, path)
    if type_expression == "str":
        return _validated_text(value, path)
    if type_expression == "TextOrNull":
        return None if value is None else _validated_text(value, path)
    if type_expression in {"bool", "Boolean"}:
        if not isinstance(value, bool):
            _fail(f"{path} must be boolean")
        return value
    if type_expression == "NativeRootRefV1":
        return _validated_native_root(value, path)
    if type_expression == "ProducerRefV1":
        return _validated_producer_ref(value, path)
    if type_expression == "RootSet":
        return _validated_set(value, path, item_validator=_validated_native_root)
    if type_expression == "RefSet":
        return _validated_set(value, path, item_validator=_validated_producer_ref)
    if type_expression == "HashSet":
        return _validated_set(value, path, item_validator=_validated_hash)
    if type_expression == "IdSet":
        return _validated_set(value, path, item_validator=_validated_id)
    if type_expression == "TextIdSet":
        return _validated_set(
            value,
            path,
            item_validator=lambda item, item_path: _validated_text(
                item,
                item_path,
                text_id=True,
            ),
        )
    if type_expression == "SET[Count]":
        return _validated_set(value, path, item_validator=_validated_count)
    if type_expression == "SET[str]":
        return _validated_set(value, path, item_validator=_validated_text)
    if type_expression.startswith("SET[("):
        return _validate_tuple_set(value, type_expression, path)
    if type_expression.startswith("Histogram<"):
        return _validate_histogram(value, type_expression, path)
    if type_expression.startswith("KeyMap<"):
        return _validate_key_map(value, type_expression, path)
    if type_expression.startswith("Ordered<"):
        return _validate_ordered(value, type_expression, path)
    if type_expression == "provider_id":
        if value not in _PROVIDERS:
            _fail(f"{path} is not a reviewed provider")
        return value
    if type_expression == "debate_role":
        if value not in _DEBATE_ROLES:
            _fail(f"{path} is not a reviewed debate role")
        return value
    if type_expression == "analysis_role":
        if value not in _ANALYSIS_ROLES:
            _fail(f"{path} is not a reviewed analysis role")
        return value
    if type_expression == "layer2_role":
        if value not in _LAYER2_ROLES:
            _fail(f"{path} is not a reviewed Layer-2 role")
        return value
    if type_expression == "READY":
        if value != "READY":
            _fail(f"{path} must be READY")
        return value
    if type_expression.startswith("Literal["):
        expected = type_expression.removeprefix("Literal[").removesuffix("]")
        if value != expected:
            _fail(f"{path} does not match its reviewed literal")
        return value
    if "|" in type_expression:
        allowed: set[object] = set(type_expression.split("|"))
        if "null" in allowed:
            allowed.remove("null")
            allowed.add(None)
        if value not in allowed:
            _fail(f"{path} is outside its reviewed enum")
        return value
    _fail(f"{path} uses an unsupported reviewed type expression")
    raise AssertionError("unreachable")


def build_census_envelope(
    *,
    schema_id: str,
    payload: Mapping[str, Any],
    expected_schema_sha256: str | None = None,
) -> CensusEnvelopeV1:
    """Validate, canonicalize, and hash a CENSUS against the frozen registry."""
    schema = CENSUS_SCHEMA_REGISTRY.get(schema_id)
    if schema is None:
        _fail(
            "CENSUS schema is absent from the frozen registry",
            code=PIPELINE_CONTRACT_UNAVAILABLE,
        )
    if expected_schema_sha256 is not None and expected_schema_sha256 != schema.schema_sha256:
        _fail(
            "CENSUS schema hash differs from the frozen registry",
            code=PIPELINE_CONTRACT_HASH_MISMATCH,
        )
    expected_fields = set(schema.required_fields)
    if set(payload) != expected_fields:
        _fail("CENSUS payload has missing or unknown fields")
    canonical_payload = {
        field_name: _validate_type(payload[field_name], type_expression, f"/{field_name}")
        for field_name, type_expression in schema.field_types
    }
    envelope_value = [
        "census-envelope-v1",
        schema.schema_id,
        schema.schema_version,
        schema.schema_sha256,
        canonical_payload,
    ]
    canonical_sha256 = hashlib.sha256(_canonical_json_bytes(envelope_value)).hexdigest()
    return CensusEnvelopeV1(
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        schema_sha256=schema.schema_sha256,
        payload=MappingProxyType(canonical_payload),
        canonical_sha256=canonical_sha256,
    )
