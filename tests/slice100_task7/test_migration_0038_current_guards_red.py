"""RED evidence for the full-strict-v21 current-table Task-7 guards."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import (
    disposable_postgres,
    downgrade,
    grant_application_access,
    migrate,
    set_tenant_context,
)
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals
from tests.slice100_task7.test_migration_0038_postgres_red import (
    _seed_legacy_run,
    _seed_reconstructible_source,
)
from tests.slice100_task7.test_migration_0038_rls_postgres_red import (
    OWNER_TENANT_ID,
    _task7_rls_database,
)

from idis.pipeline.task7_uuid import derive_task7_uuid

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_CONTRACT_FIXTURE = Path(__file__).with_name("fixtures") / "full_strict_v21_contract_hash_v1.json"

_DEFERRED_TARGETS = (
    ("source_lineage_objects", "ctrg_task7_source_lineage_objects_exact_one"),
    ("data_room_packages", "ctrg_task7_data_room_packages_source_exact_one"),
    (
        "data_room_package_files",
        "ctrg_task7_data_room_package_files_source_exact_one",
    ),
    ("document_artifacts", "ctrg_task7_document_artifacts_source_exact_one"),
    ("documents", "ctrg_task7_documents_source_exact_one"),
    (
        "document_parse_attempts",
        "ctrg_task7_document_parse_attempts_source_exact_one",
    ),
    ("document_spans", "ctrg_task7_document_spans_source_exact_one"),
    ("run_output_objects", "ctrg_task7_run_output_objects_exact_one"),
    (
        "methodology_coverage_records",
        "ctrg_task7_methodology_coverage_records_output_exact_one",
    ),
    (
        "methodology_extraction_tasks",
        "ctrg_task7_methodology_extraction_tasks_output_exact_one",
    ),
    (
        "methodology_extraction_results",
        "ctrg_task7_methodology_extraction_results_output_exact_one",
    ),
    (
        "methodology_result_factual_outputs",
        "ctrg_task7_methodology_result_factual_outputs_output_exact_one",
    ),
    (
        "methodology_result_claim_links",
        "ctrg_task7_methodology_result_claim_links_output_exact_one",
    ),
    (
        "methodology_result_evidence_links",
        "ctrg_task7_methodology_result_evidence_links_output_exact_one",
    ),
    (
        "methodology_result_sanad_links",
        "ctrg_task7_methodology_result_sanad_links_output_exact_one",
    ),
    (
        "methodology_result_defect_links",
        "ctrg_task7_methodology_result_defect_links_output_exact_one",
    ),
    (
        "deterministic_calculations",
        "ctrg_task7_deterministic_calculations_output_exact_one",
    ),
    ("vector_embeddings", "ctrg_task7_vector_embeddings_output_exact_one"),
    (
        "evidence_trust_findings",
        "ctrg_task7_evidence_trust_findings_output_exact_one",
    ),
    ("muhasabah_records", "ctrg_task7_muhasabah_records_output_exact_one"),
    (
        "validated_evidence_packages",
        "ctrg_task7_validated_evidence_packages_output_exact_one",
    ),
    ("debate_sessions", "ctrg_task7_debate_sessions_output_exact_one"),
    (
        "layer2_ic_challenges",
        "ctrg_task7_layer2_ic_challenges_output_exact_one",
    ),
    ("layer2_ic_findings", "ctrg_task7_layer2_ic_findings_output_exact_one"),
    ("deliverables", "ctrg_task7_deliverables_output_exact_one"),
    ("run_steps", "ctrg_task7_run_steps_receipt_complete"),
    ("run_step_outputs", "ctrg_task7_run_step_outputs_receipt_complete"),
    ("run_output_objects", "ctrg_task7_run_output_objects_receipt_complete"),
    (
        "run_expected_consumer_edges",
        "ctrg_task7_expected_edges_decision_complete",
    ),
    (
        "run_consumer_receipts",
        "ctrg_task7_consumer_receipts_edge_complete",
    ),
    (
        "deliverable_manifest_references",
        "ctrg_task7_deliverable_manifest_references_exact_one",
    ),
    (
        "deliverable_manifest_references",
        "ctrg_task7_deliverable_manifest_references_exact_projection",
    ),
)
_APPEND_ONLY_TARGETS = (
    ("run_input_bindings", "trg_task7_run_input_bindings_append_only"),
    ("run_output_objects", "trg_task7_run_output_objects_append_only"),
    ("run_step_outputs", "trg_task7_run_step_outputs_append_only"),
    (
        "run_applicability_decisions",
        "trg_task7_run_applicability_decisions_append_only",
    ),
    (
        "run_expected_consumer_edges",
        "trg_task7_run_expected_consumer_edges_append_only",
    ),
    ("run_consumer_receipts", "trg_task7_run_consumer_receipts_append_only"),
    (
        "deliverable_manifest_references",
        "trg_task7_deliverable_manifest_references_append_only",
    ),
)
_BOUND_CONTRACT_TARGETS = (
    ("data_room_packages", "trg_task7_data_room_packages_source_bound"),
    (
        "data_room_package_files",
        "trg_task7_data_room_package_files_source_bound",
    ),
    ("document_artifacts", "trg_task7_document_artifacts_source_bound"),
    ("documents", "trg_task7_documents_source_bound"),
    (
        "document_parse_attempts",
        "trg_task7_document_parse_attempts_source_bound",
    ),
    ("document_spans", "trg_task7_document_spans_source_bound"),
    (
        "methodology_coverage_records",
        "trg_task7_methodology_coverage_records_output_bound",
    ),
    (
        "methodology_extraction_tasks",
        "trg_task7_methodology_extraction_tasks_output_bound",
    ),
    (
        "methodology_extraction_results",
        "trg_task7_methodology_extraction_results_output_bound",
    ),
    (
        "methodology_result_factual_outputs",
        "trg_task7_methodology_result_factual_outputs_output_bound",
    ),
    (
        "methodology_result_claim_links",
        "trg_task7_methodology_result_claim_links_output_bound",
    ),
    (
        "methodology_result_evidence_links",
        "trg_task7_methodology_result_evidence_links_output_bound",
    ),
    (
        "methodology_result_sanad_links",
        "trg_task7_methodology_result_sanad_links_output_bound",
    ),
    (
        "methodology_result_defect_links",
        "trg_task7_methodology_result_defect_links_output_bound",
    ),
    (
        "deterministic_calculations",
        "trg_task7_deterministic_calculations_output_bound",
    ),
    ("vector_embeddings", "trg_task7_vector_embeddings_output_bound"),
    (
        "evidence_trust_findings",
        "trg_task7_evidence_trust_findings_output_bound",
    ),
    ("muhasabah_records", "trg_task7_muhasabah_records_output_bound"),
    (
        "validated_evidence_packages",
        "trg_task7_validated_evidence_packages_output_bound",
    ),
    ("debate_sessions", "trg_task7_debate_sessions_output_bound"),
    ("layer2_ic_challenges", "trg_task7_layer2_ic_challenges_output_bound"),
    ("layer2_ic_findings", "trg_task7_layer2_ic_findings_output_bound"),
    ("deliverables", "trg_task7_deliverables_output_bound"),
    ("runs", "trg_task7_runs_contract_immutable"),
    ("run_steps", "trg_task7_run_steps_contract_immutable"),
)
_RETAINED_TASK5_TASK6_TARGETS = (
    ("methodology_coverage_records", "trg_methodology_coverage_scope"),
    (
        "methodology_extraction_tasks",
        "trg_methodology_extraction_tasks_immutable",
    ),
    (
        "methodology_extraction_task_spans",
        "trg_methodology_extraction_task_spans_immutable",
    ),
    (
        "methodology_extraction_results",
        "trg_methodology_extraction_results_immutable",
    ),
    (
        "methodology_result_factual_outputs",
        "trg_methodology_result_factual_outputs_immutable",
    ),
    (
        "methodology_result_claim_links",
        "trg_methodology_result_claim_links_immutable",
    ),
    (
        "methodology_result_evidence_links",
        "trg_methodology_result_evidence_links_immutable",
    ),
    (
        "methodology_result_sanad_links",
        "trg_methodology_result_sanad_links_immutable",
    ),
    (
        "methodology_result_defect_links",
        "trg_methodology_result_defect_links_immutable",
    ),
)
_NEW_CURRENT_TARGETS = (
    *_DEFERRED_TARGETS,
    *_APPEND_ONLY_TARGETS,
    *_BOUND_CONTRACT_TARGETS,
)
_ALL_CURRENT_TARGETS = (*_NEW_CURRENT_TARGETS, *_RETAINED_TASK5_TASK6_TARGETS)
_REVIEWED_CURRENT_TRIGGER_NAMES = frozenset(name for _table, name in _ALL_CURRENT_TARGETS)
_AUXILIARY_0038_TRIGGER_TARGETS = (("legal_holds", "trg_task7_legal_holds_tenant_lock"),)
_STRICT_V9 = "full-strict-v9"
_STRICT_V21 = "full-strict-v21"

_PROVIDER_SUBROLES = (
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
_DEBATE_SUBROLES = (
    "advocate",
    "sanad_breaker",
    "contradiction_finder",
    "risk_officer",
    "arbiter",
)
_ANALYSIS_SUBROLES = (
    "financial_agent",
    "market_agent",
    "technical_agent",
    "terms_agent",
    "team_agent",
    "risk_officer_agent",
    "historian_agent",
    "sector_specialist_agent",
)
_LAYER2_SUBROLES = ("ic_advocate", "ic_challenger", "ic_arbiter")
_DELIVERABLE_SUBROLES = (
    "reconciliation",
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

_REGISTRY_SUBROLE_STEPS = (
    pytest.param(
        "EXTRACT",
        12,
        "ba078b98792baa4f5656ebdd21659a02f659a8e666a6f53b2e094c6c666c065f",
        "EXTRACT_RECEIPT_V1",
        (("rag_context", "EXTRACT_RAG_CONTEXT_RECEIPT_V1"),),
        id="extract",
    ),
    pytest.param(
        "ENRICHMENT",
        16,
        "0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6",
        "ENRICHMENT_RECEIPT_V1",
        tuple(
            (subrole, "ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1") for subrole in _PROVIDER_SUBROLES
        ),
        id="enrichment",
    ),
    pytest.param(
        "GRAPH_EVIDENCE",
        17,
        "11d0a5a8557433a21994ac042a5d248ec98b1310c4374df66d3ebb1705262298",
        "GRAPH_RECEIPT_V1",
        (
            ("internal_evidence", "GRAPH_NAMESPACE_RECEIPT_V1"),
            ("external_context", "GRAPH_NAMESPACE_RECEIPT_V1"),
        ),
        id="graph",
    ),
    pytest.param(
        "RAG_EVIDENCE",
        18,
        "de1aa5c89992b1eec574448a6f857d419782126544b556ae177f8fa8356508f9",
        "RAG_RECEIPT_V1",
        (
            ("internal_evidence", "RAG_NAMESPACE_RECEIPT_V1"),
            ("external_context", "RAG_NAMESPACE_RECEIPT_V1"),
        ),
        id="rag",
    ),
    pytest.param(
        "DEBATE",
        22,
        "ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83",
        "DEBATE_RECEIPT_V1",
        tuple((subrole, "DEBATE_ROLE_RECEIPT_V1") for subrole in _DEBATE_SUBROLES),
        id="debate",
    ),
    pytest.param(
        "ANALYSIS",
        23,
        "aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa",
        "ANALYSIS_RECEIPT_V1",
        tuple((subrole, "ANALYSIS_AGENT_RECEIPT_V1") for subrole in _ANALYSIS_SUBROLES),
        id="analysis",
    ),
    pytest.param(
        "LAYER2_IC_CHALLENGE",
        26,
        "3f144df6c9855f7733689553d24af2c15065d7004e4b1adccd0ca9f9500b366e",
        "LAYER2_RECEIPT_V1",
        tuple((subrole, "LAYER2_ROLE_RECEIPT_V1") for subrole in _LAYER2_SUBROLES),
        id="layer2",
    ),
    pytest.param(
        "DELIVERABLES",
        27,
        "672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d",
        "PRODUCT_BUNDLE_RECEIPT_V1",
        tuple(
            (
                subrole,
                (
                    "DELIVERABLE_RECONCILIATION_RECEIPT_V1"
                    if subrole == "reconciliation"
                    else "DELIVERABLE_ARTIFACT_RECEIPT_V1"
                ),
            )
            for subrole in _DELIVERABLE_SUBROLES
        ),
        id="deliverables",
    ),
)


def _artifact() -> dict[str, Any]:
    value = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _decoded_sql() -> str:
    rows = _artifact()["sql_authority_registry"]["rows"]
    return "\n".join(
        bytes.fromhex(encoded).decode("utf-8")
        for row in rows
        for encoded in row["body_sql_utf8_hex_tuple"]
    )


def _provision(database: Any) -> None:
    with database.admin_engine.begin() as connection:
        provision_exact_erasure_principals(connection)
        connection.exec_driver_sql(
            """
            DO $task7$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname='idis_task7_guard_function_owner'
              ) THEN
                CREATE ROLE idis_task7_guard_function_owner
                  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                  NOINHERIT NOBYPASSRLS;
              END IF;
            END
            $task7$;
            """
        )


def _sha256_json(value: object) -> str:
    raw = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _manifest_reference_digest(
    graph: dict[str, str],
    *,
    manifest_deliverable_id: str,
    member_kind: str,
    catalog_ordinal: int | None,
    member_step_output_id: str,
    member_output_object_id: str,
    member_identity_sha256: str,
    member_value_sha256: str,
    member_content_sha256: str | None,
) -> str:
    return _sha256_json(
        [
            "deliverable-manifest-reference-digest-v1",
            graph["tenant_id"],
            graph["deal_id"],
            graph["run_id"],
            graph["pipeline_contract_version"],
            graph["pipeline_contract_hash"],
            manifest_deliverable_id,
            member_kind,
            catalog_ordinal,
            member_step_output_id,
            member_output_object_id,
            member_identity_sha256,
            member_value_sha256,
            member_content_sha256,
        ]
    )


def _contract_hash() -> str:
    fixture = json.loads(_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    value = fixture["FULL_STRICT_V21_CONTRACT_HASH"]
    assert isinstance(value, str) and len(value) == 64
    return value


def _seed_strict_run_for_source(
    connection: Any,
    *,
    tenant_id: str,
    deal_id: str,
    source: dict[str, str],
    proof_corpus_hash: str | None = None,
    pipeline_contract_version: str = "full-strict-v21",
    pipeline_contract_hash: str | None = None,
) -> dict[str, str]:
    run_id = str(uuid.uuid4())
    proof_id = str(uuid.uuid4())
    contract_hash = pipeline_contract_hash or _contract_hash()
    repeated_hash = hashlib.sha256(b"task7-v8-binding-fixture").hexdigest()
    set_tenant_context(connection, tenant_id)
    connection.execute(
        text(
            """
            INSERT INTO strict_preflight_proofs (
                proof_id,tenant_id,deal_id,corpus_hash,
                strict_profile_generation,strict_profile_hmac,
                pipeline_contract_version,pipeline_contract_hash,
                expected_consumer_edge_rule_hash,static_decision_set_hash,
                methodology_version_hashes,promoted_pointer_hash,
                provider_receipt_set_hash,dependency_receipt_set_hash,
                binding_hash,expires_at
            ) VALUES (
                CAST(:proof_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),:proof_corpus_hash,'bootstrap-v1',
                :repeated_hash,:pipeline_contract_version,:contract_hash,
                :repeated_hash,:repeated_hash,
                CAST(:methodology_version_hashes AS jsonb),:repeated_hash,
                :repeated_hash,:repeated_hash,:repeated_hash,
                statement_timestamp()+interval '1 hour'
            );
            INSERT INTO runs (
                run_id,tenant_id,deal_id,mode,status,idempotency_key,
                strict_preflight_proof_id,pipeline_contract_version,
                pipeline_contract_hash,pipeline_resume_state,
                pipeline_resume_error_code
            ) VALUES (
                CAST(:run_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),'FULL','QUEUED',:idempotency_key,
                CAST(:proof_id AS uuid),:pipeline_contract_version,:contract_hash,
                'RESUMABLE',NULL
            );
            UPDATE strict_preflight_proofs
            SET consumed_at=statement_timestamp(),
                consumed_by_run_id=CAST(:run_id AS uuid)
            WHERE proof_id=CAST(:proof_id AS uuid);
            """
        ),
        {
            "proof_id": proof_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "proof_corpus_hash": proof_corpus_hash or source["corpus_receipt_sha256"],
            "repeated_hash": repeated_hash,
            "pipeline_contract_version": pipeline_contract_version,
            "contract_hash": contract_hash,
            "methodology_version_hashes": json.dumps([repeated_hash]),
            "run_id": run_id,
            "idempotency_key": f"task7-v8-binding-{run_id}",
        },
    )
    return {
        **source,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "proof_id": proof_id,
        "pipeline_contract_version": pipeline_contract_version,
        "pipeline_contract_hash": contract_hash,
    }


def _package_source(connection: Any, package_id: str) -> dict[str, str]:
    row = connection.execute(
        text(
            """
            SELECT
                source_lineage_object_id::text,
                corpus_receipt_id::text,
                object_kind,
                source_disposition,
                source_version,
                object_identity_sha256,
                content_sha256,
                corpus_receipt_sha256
            FROM source_lineage_objects
            WHERE corpus_receipt_id=CAST(:package_id AS uuid)
              AND object_kind='PACKAGE'
            """
        ),
        {"package_id": package_id},
    ).one()
    return {
        "source_lineage_object_id": str(row[0]),
        "corpus_receipt_id": str(row[1]),
        "source_object_kind": str(row[2]),
        "source_disposition": str(row[3]),
        "selected_source_version": str(row[4]),
        "selected_identity_sha256": str(row[5]),
        "selected_content_sha256": str(row[6]),
        "corpus_receipt_sha256": str(row[7]),
        "binding_purpose": "INVENTORY",
    }


def _insert_bound_package_and_source_root(
    connection: Any,
    *,
    write_origin: str,
) -> dict[str, str]:
    tenant_id, deal_id, package_id, source_lineage_object_id = (str(uuid.uuid4()) for _ in range(4))
    identity_sha256 = hashlib.sha256(f"source-identity:{package_id}".encode()).hexdigest()
    content_sha256 = hashlib.sha256(f"source-content:{package_id}".encode()).hexdigest()
    corpus_sha256 = hashlib.sha256(f"source-corpus:{package_id}".encode()).hexdigest()
    set_tenant_context(connection, tenant_id)
    connection.execute(
        text(
            """
            INSERT INTO tenants (tenant_id,name)
            VALUES (CAST(:tenant_id AS uuid),'Task 7 source guard tenant');
            INSERT INTO deals (deal_id,tenant_id,name,created_at)
            VALUES (
                CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),
                'Fictional source guard deal',statement_timestamp()
            );
            INSERT INTO data_room_packages (
                package_id,tenant_id,deal_id,status,metadata,
                source_binding_state,source_lineage_object_id
            ) VALUES (
                CAST(:package_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),'SEALED','{}'::jsonb,'BOUND',
                CAST(:source_lineage_object_id AS uuid)
            );
            INSERT INTO source_lineage_objects (
                source_lineage_object_id,tenant_id,deal_id,corpus_receipt_id,
                object_kind,source_version,source_disposition,
                object_identity_sha256,content_sha256,corpus_receipt_sha256,
                write_origin
            ) VALUES (
                CAST(:source_lineage_object_id AS uuid),
                CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                CAST(:package_id AS uuid),'PACKAGE','noncanonical-package-v1',
                'CORPUS',:identity_sha256,:content_sha256,:corpus_sha256,
                :write_origin
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "package_id": package_id,
            "source_lineage_object_id": source_lineage_object_id,
            "identity_sha256": identity_sha256,
            "content_sha256": content_sha256,
            "corpus_sha256": corpus_sha256,
            "write_origin": write_origin,
        },
    )
    return {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "package_id": package_id,
        "source_lineage_object_id": source_lineage_object_id,
    }


def _binding_values(graph: dict[str, str]) -> dict[str, str]:
    preimage = [
        "run-input-binding-digest-v2",
        graph["tenant_id"],
        graph["deal_id"],
        graph["run_id"],
        graph["pipeline_contract_version"],
        graph["pipeline_contract_hash"],
        graph["corpus_receipt_id"],
        graph["source_lineage_object_id"],
        graph["source_object_kind"],
        graph["source_disposition"],
        graph["binding_purpose"],
        graph["selected_source_version"],
        graph["selected_identity_sha256"],
        graph["selected_content_sha256"],
        graph["corpus_receipt_sha256"],
    ]
    return {
        **graph,
        "binding_id": str(uuid.uuid4()),
        "binding_sha256": _sha256_json(preimage),
    }


def _insert_binding(connection: Any, values: dict[str, str]) -> None:
    set_tenant_context(connection, values["tenant_id"])
    connection.execute(
        text(
            """
            INSERT INTO run_input_bindings (
                binding_id,tenant_id,deal_id,run_id,
                source_lineage_object_id,source_object_kind,
                source_disposition,binding_purpose,corpus_receipt_id,
                selected_source_version,selected_identity_sha256,
                selected_content_sha256,corpus_receipt_sha256,
                pipeline_contract_version,pipeline_contract_hash,binding_sha256
            ) VALUES (
                CAST(:binding_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:run_id AS uuid),
                CAST(:source_lineage_object_id AS uuid),:source_object_kind,
                :source_disposition,:binding_purpose,
                CAST(:corpus_receipt_id AS uuid),:selected_source_version,
                :selected_identity_sha256,:selected_content_sha256,
                :corpus_receipt_sha256,:pipeline_contract_version,
                :pipeline_contract_hash,:binding_sha256
            )
            """
        ),
        values,
    )


def _seed_v8_run_step(
    connection: Any,
    *,
    step_name: str,
    step_order: int,
    semantic_rule_hash: str,
    pipeline_contract_version: str = "full-strict-v21",
    pipeline_resume_state: str = "RESUMABLE",
) -> dict[str, str]:
    tenant_id, deal_id, run_id, step_id, lease_owner_id = (str(uuid.uuid4()) for _ in range(5))
    contract_hash = "a" * 64
    set_tenant_context(connection, tenant_id)
    connection.execute(
        text(
            """
            INSERT INTO tenants (tenant_id,name)
            VALUES (CAST(:tenant_id AS uuid),'Task 7 guard tenant');
            INSERT INTO deals (deal_id,tenant_id,name,created_at)
            VALUES (
                CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),
                'Fictional guard deal',statement_timestamp()
            );
            INSERT INTO runs (
                run_id,tenant_id,deal_id,mode,status,idempotency_key,
                lease_owner_id,lease_fencing_token,lease_expires_at,
                last_heartbeat_at,pipeline_contract_version,
                pipeline_contract_hash,pipeline_resume_state,
                pipeline_resume_error_code
            ) VALUES (
                CAST(:run_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),'FULL','RUNNING',:idempotency_key,
                CAST(:lease_owner_id AS uuid),1,
                statement_timestamp()+interval '1 hour',statement_timestamp(),
                :pipeline_contract_version,:contract_hash,
                :pipeline_resume_state,:pipeline_resume_error_code
            );
            INSERT INTO run_steps (
                step_id,tenant_id,run_id,step_name,step_order,status,
                pipeline_contract_version,pipeline_contract_hash,
                semantic_rule_version,semantic_rule_hash
            ) VALUES (
                CAST(:step_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:run_id AS uuid),:step_name,:step_order,'PENDING',
                :pipeline_contract_version,:contract_hash,'2',:semantic_rule_hash
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "step_id": step_id,
            "lease_owner_id": lease_owner_id,
            "idempotency_key": f"task7-guard-{run_id}",
            "contract_hash": contract_hash,
            "pipeline_contract_version": pipeline_contract_version,
            "pipeline_resume_state": pipeline_resume_state,
            "pipeline_resume_error_code": (
                "LEGACY_RUN_RESUME_UNSAFE" if pipeline_resume_state == "NON_RESUMABLE" else None
            ),
            "step_name": step_name,
            "step_order": step_order,
            "semantic_rule_hash": semantic_rule_hash,
        },
    )
    connection.execute(
        text(
            """
            SELECT
                set_config('idis.worker_lease_tenant_id', :tenant_id, true),
                set_config('idis.worker_lease_deal_id', :deal_id, true),
                set_config('idis.worker_lease_run_id', :run_id, true),
                set_config('idis.worker_lease_owner_id', :lease_owner_id, true),
                set_config('idis.worker_lease_fencing_token', '1', true)
            """
        ),
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "lease_owner_id": lease_owner_id,
        },
    )
    return {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "step_id": step_id,
        "lease_owner_id": lease_owner_id,
        "pipeline_contract_hash": contract_hash,
        "pipeline_contract_version": pipeline_contract_version,
        "step_name": step_name,
        "semantic_rule_hash": semantic_rule_hash,
    }


def _insert_step_output(
    connection: Any,
    graph: dict[str, str],
    *,
    subrole: str,
    output_kind: str,
    output_ordinal: int,
    semantic_rule_version: str = "2",
    semantic_rule_hash: str | None = None,
    output_class: str = "STEP_RECEIPT",
    root_write_origin: str = "RUNTIME",
) -> dict[str, str]:
    output_object_id = str(uuid.uuid4())
    step_output_id = str(uuid.uuid4())
    identity_sha256 = hashlib.sha256(f"identity:{output_object_id}".encode()).hexdigest()
    value_sha256 = hashlib.sha256(f"value:{output_object_id}".encode()).hexdigest()
    set_tenant_context(connection, graph["tenant_id"])
    connection.execute(
        text(
            """
            INSERT INTO run_output_objects (
                output_object_id,tenant_id,deal_id,run_id,output_class,
                output_kind,output_schema_version,native_identity_sha256,
                native_output_sha256,pipeline_contract_version,
                pipeline_contract_hash,write_origin,lease_owner_id,
                lease_fencing_token
            ) VALUES (
                CAST(:output_object_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:run_id AS uuid),:output_class,
                :output_kind,'task7-guard-output-v1',:identity_sha256,
                :value_sha256,:pipeline_contract_version,:pipeline_contract_hash,
                :root_write_origin,CAST(:root_lease_owner_id AS uuid),
                :root_lease_fencing_token
            );
            INSERT INTO run_step_outputs (
                step_output_id,tenant_id,deal_id,run_id,step_id,
                output_object_id,step_name,subrole,output_kind,
                output_ordinal,semantic_rule_version,semantic_rule_hash,
                summary_sha256,lease_owner_id,lease_fencing_token
            ) VALUES (
                CAST(:step_output_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:run_id AS uuid),
                CAST(:step_id AS uuid),CAST(:output_object_id AS uuid),
                :step_name,:subrole,:output_kind,:output_ordinal,
                :semantic_rule_version,
                :semantic_rule_hash,:value_sha256,
                CAST(:lease_owner_id AS uuid),1
            )
            """
        ),
        {
            **graph,
            "output_object_id": output_object_id,
            "step_output_id": step_output_id,
            "subrole": subrole,
            "output_kind": output_kind,
            "output_ordinal": output_ordinal,
            "semantic_rule_version": semantic_rule_version,
            "semantic_rule_hash": semantic_rule_hash or graph["semantic_rule_hash"],
            "output_class": output_class,
            "identity_sha256": identity_sha256,
            "value_sha256": value_sha256,
            "pipeline_contract_version": graph.get(
                "pipeline_contract_version",
                "full-strict-v21",
            ),
            "root_write_origin": root_write_origin,
            "root_lease_owner_id": (
                graph["lease_owner_id"] if root_write_origin == "RUNTIME" else None
            ),
            "root_lease_fencing_token": 1 if root_write_origin == "RUNTIME" else 0,
        },
    )
    return {
        "output_object_id": output_object_id,
        "step_output_id": step_output_id,
        "native_identity_sha256": identity_sha256,
        "native_output_sha256": value_sha256,
    }


def _insert_exact_receipt_set(
    connection: Any,
    graph: dict[str, str],
    *,
    aggregate_kind: str,
    required_subroles: tuple[tuple[str, str], ...],
) -> None:
    _insert_step_output(
        connection,
        graph,
        subrole="__step__",
        output_kind=aggregate_kind,
        output_ordinal=0,
    )
    for subrole, output_kind in required_subroles:
        _insert_step_output(
            connection,
            graph,
            subrole=subrole,
            output_kind=output_kind,
            output_ordinal=0,
        )


def _assert_application_role(connection: Any, expected_role: str) -> None:
    assert connection.execute(text("SELECT current_user")).scalar_one() == expected_role


def _insert_deliverable(
    connection: Any,
    graph: dict[str, str],
    *,
    output_object_id: str,
    catalog_ordinal: int,
) -> dict[str, str]:
    deliverable_id = str(uuid.uuid4())
    content_sha256 = hashlib.sha256(f"deliverable:{deliverable_id}".encode()).hexdigest()
    uri_sha256 = hashlib.sha256(f"uri:{deliverable_id}".encode()).hexdigest()
    object_version_hash = hashlib.sha256(f"version:{deliverable_id}".encode()).hexdigest()
    download_proof_hash = hashlib.sha256(f"download:{deliverable_id}".encode()).hexdigest()
    set_tenant_context(connection, graph["tenant_id"])
    connection.execute(
        text(
            """
            INSERT INTO deliverables (
                deliverable_id,tenant_id,deal_id,run_id,deliverable_type,
                format,status,uri,run_output_object_id,content_type,
                size_bytes,content_sha256,uri_sha256,object_version_hash,
                download_proof_hash,catalog_ordinal
            ) VALUES (
                CAST(:deliverable_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:run_id AS uuid),
                :deliverable_type,'JSON','COMPLETED',:uri,
                CAST(:output_object_id AS uuid),'application/json',1,
                :content_sha256,:uri_sha256,:object_version_hash,
                :download_proof_hash,:catalog_ordinal
            )
            """
        ),
        {
            **graph,
            "deliverable_id": deliverable_id,
            "deliverable_type": f"task7_manifest_member_{catalog_ordinal}",
            "uri": f"object://task7/manifest-member-{catalog_ordinal}.json",
            "output_object_id": output_object_id,
            "content_sha256": content_sha256,
            "uri_sha256": uri_sha256,
            "object_version_hash": object_version_hash,
            "download_proof_hash": download_proof_hash,
            "catalog_ordinal": catalog_ordinal,
        },
    )
    return {
        "deliverable_id": deliverable_id,
        "content_sha256": content_sha256,
    }


def _insert_canonical_deliverable_step_output(
    connection: Any,
    graph: dict[str, str],
) -> dict[str, str]:
    """Persist one constructor-valid native deliverable and its output roots."""

    deliverable_id = str(uuid.uuid4())
    deliverable = {
        "deliverable_id": deliverable_id,
        "deliverable_type": "task7-native-consumer",
        "format": "JSON",
        "status": "COMPLETED",
        "uri": "object://task7/native-consumer.json",
        "content_type": "application/json",
        "size_bytes": 1,
        "content_sha256": hashlib.sha256(b"task7-native-consumer-content").hexdigest(),
        "uri_sha256": hashlib.sha256(b"task7-native-consumer-uri").hexdigest(),
        "object_version_hash": hashlib.sha256(b"task7-native-consumer-version").hexdigest(),
        "download_proof_hash": hashlib.sha256(b"task7-native-consumer-download").hexdigest(),
        "catalog_ordinal": 0,
    }
    identity_sha256, value_sha256 = connection.execute(
        text(
            """
            SELECT
                public.idis_task7_hash_jsonb_v1(
                    pg_catalog.jsonb_build_array(
                        'native-identity-v1',CAST(:tenant_id AS text),
                        CAST(:deal_id AS text),CAST(:run_id AS text),
                        'DELIVERABLE_ARTIFACT',
                        pg_catalog.jsonb_build_array(
                            CAST(:deliverable_id AS text)
                        )
                    )
                ),
                public.idis_task7_hash_jsonb_v1(
                    pg_catalog.jsonb_build_array(
                        'native-output-v1',CAST(:tenant_id AS text),
                        CAST(:deal_id AS text),CAST(:run_id AS text),
                        :pipeline_contract_version,:pipeline_contract_hash,
                        'DELIVERABLE_ARTIFACT',
                        pg_catalog.jsonb_build_array(
                            CAST(:deliverable_id AS text)
                        ),
                        pg_catalog.jsonb_build_array(
                            :deliverable_type,:format,:status,:uri,
                            :content_type,:size_bytes,:content_sha256,
                            :uri_sha256,:object_version_hash,
                            :download_proof_hash,:catalog_ordinal
                        )
                    )
                )
            """
        ),
        {**graph, **deliverable},
    ).one()
    output_object_id = str(
        derive_task7_uuid(
            id_kind="output_object_id",
            values={
                "tenant_id": graph["tenant_id"],
                "deal_id": graph["deal_id"],
                "run_id": graph["run_id"],
                "pipeline_contract_version": graph["pipeline_contract_version"],
                "pipeline_contract_hash": graph["pipeline_contract_hash"],
                "output_class": "NATIVE_ROW",
                "output_kind": "DELIVERABLE_ARTIFACT",
                "native_identity_sha256": str(identity_sha256),
                "native_output_sha256": str(value_sha256),
            },
        ).value
    )
    step_output_id = str(
        derive_task7_uuid(
            id_kind="step_output_id",
            values={
                "tenant_id": graph["tenant_id"],
                "deal_id": graph["deal_id"],
                "run_id": graph["run_id"],
                "pipeline_contract_hash": graph["pipeline_contract_hash"],
                "step_id": graph["step_id"],
                "step_name": graph["step_name"],
                "subrole": "__step__",
                "output_kind": "DELIVERABLE_ARTIFACT",
                "output_ordinal": 0,
                "output_object_id": output_object_id,
            },
        ).value
    )
    summary_sha256 = _sha256_json(
        [
            "step-output-summary-v1",
            graph["step_name"],
            "__step__",
            "DELIVERABLE_ARTIFACT",
            hashlib.sha256(b"task7-native-consumer-census").hexdigest(),
            str(identity_sha256),
            str(value_sha256),
        ]
    )
    set_tenant_context(connection, graph["tenant_id"])
    connection.execute(
        text(
            """
            INSERT INTO run_output_objects (
                output_object_id,tenant_id,deal_id,run_id,output_class,
                output_kind,output_schema_version,native_identity_sha256,
                native_output_sha256,pipeline_contract_version,
                pipeline_contract_hash,write_origin,lease_owner_id,
                lease_fencing_token
            ) VALUES (
                CAST(:output_object_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:run_id AS uuid),'NATIVE_ROW',
                'DELIVERABLE_ARTIFACT','2',:native_identity_sha256,
                :native_output_sha256,:pipeline_contract_version,
                :pipeline_contract_hash,'RUNTIME',
                CAST(:lease_owner_id AS uuid),1
            );
            INSERT INTO run_step_outputs (
                step_output_id,tenant_id,deal_id,run_id,step_id,
                output_object_id,step_name,subrole,output_kind,
                output_ordinal,semantic_rule_version,semantic_rule_hash,
                summary_sha256,lease_owner_id,lease_fencing_token
            ) VALUES (
                CAST(:step_output_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:run_id AS uuid),
                CAST(:step_id AS uuid),CAST(:output_object_id AS uuid),
                :step_name,'__step__','DELIVERABLE_ARTIFACT',0,'2',
                :semantic_rule_hash,:summary_sha256,
                CAST(:lease_owner_id AS uuid),1
            );
            INSERT INTO deliverables (
                deliverable_id,tenant_id,deal_id,run_id,deliverable_type,
                format,status,uri,run_output_object_id,content_type,
                size_bytes,content_sha256,uri_sha256,object_version_hash,
                download_proof_hash,catalog_ordinal
            ) VALUES (
                CAST(:deliverable_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:run_id AS uuid),
                :deliverable_type,:format,:status,:uri,
                CAST(:output_object_id AS uuid),:content_type,:size_bytes,
                :content_sha256,:uri_sha256,:object_version_hash,
                :download_proof_hash,:catalog_ordinal
            )
            """
        ),
        {
            **graph,
            **deliverable,
            "output_object_id": output_object_id,
            "step_output_id": step_output_id,
            "native_identity_sha256": str(identity_sha256),
            "native_output_sha256": str(value_sha256),
            "summary_sha256": summary_sha256,
        },
    )
    return {
        "deliverable_id": deliverable_id,
        "output_object_id": output_object_id,
        "step_output_id": step_output_id,
        "native_identity_sha256": str(identity_sha256),
        "native_output_sha256": str(value_sha256),
    }


def _insert_manifest_reference(
    connection: Any,
    graph: dict[str, str],
    *,
    manifest_deliverable_id: str,
    manifest_output_object_id: str,
    reference_output_object_id: str,
    member_kind: str,
    catalog_ordinal: int | None,
    member_step_output_id: str,
    member_output_object_id: str,
    member_identity_sha256: str,
    member_value_sha256: str,
    member_content_sha256: str | None,
    reference_sha256: str,
) -> None:
    set_tenant_context(connection, graph["tenant_id"])
    connection.execute(
        text(
            """
            INSERT INTO deliverable_manifest_references (
                manifest_reference_id,tenant_id,deal_id,run_id,
                manifest_deliverable_id,manifest_output_object_id,
                run_output_object_id,member_kind,catalog_ordinal,
                member_step_output_id,member_output_object_id,
                member_identity_sha256,member_value_sha256,
                member_content_sha256,reference_sha256,lease_owner_id,
                lease_fencing_token
            ) VALUES (
                CAST(:manifest_reference_id AS uuid),
                CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                CAST(:run_id AS uuid),CAST(:manifest_deliverable_id AS uuid),
                CAST(:manifest_output_object_id AS uuid),
                CAST(:reference_output_object_id AS uuid),:member_kind,
                :catalog_ordinal,CAST(:member_step_output_id AS uuid),
                CAST(:member_output_object_id AS uuid),
                :member_identity_sha256,:member_value_sha256,
                :member_content_sha256,:reference_sha256,
                CAST(:lease_owner_id AS uuid),1
            )
            """
        ),
        {
            **graph,
            "manifest_reference_id": str(uuid.uuid4()),
            "manifest_deliverable_id": manifest_deliverable_id,
            "manifest_output_object_id": manifest_output_object_id,
            "reference_output_object_id": reference_output_object_id,
            "member_kind": member_kind,
            "catalog_ordinal": catalog_ordinal,
            "member_step_output_id": member_step_output_id,
            "member_output_object_id": member_output_object_id,
            "member_identity_sha256": member_identity_sha256,
            "member_value_sha256": member_value_sha256,
            "member_content_sha256": member_content_sha256,
            "reference_sha256": reference_sha256,
        },
    )


def _guard_catalog(
    connection: Any,
    targets: tuple[tuple[str, str], ...],
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT
                    c.relname,
                    t.tgname,
                    t.tgenabled,
                    pg_catalog.pg_get_triggerdef(t.oid, true),
                    p.proname,
                    pg_catalog.pg_get_functiondef(p.oid),
                    owner.rolname,
                    COALESCE(p.proconfig, ARRAY[]::text[])
                FROM pg_catalog.pg_trigger t
                JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner
                WHERE n.nspname='public'
                  AND NOT t.tgisinternal
                  AND t.tgname=ANY(CAST(:names AS text[]))
                ORDER BY c.relname,t.tgname
                """
            ),
            {"names": [name for _table, name in targets]},
        ).fetchall()
    )


def _all_user_trigger_catalog(connection: Any) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT
                    c.relname,
                    t.tgname,
                    t.tgenabled,
                    pg_catalog.pg_get_triggerdef(t.oid, true),
                    p.proname,
                    pg_catalog.pg_get_functiondef(p.oid),
                    owner.rolname,
                    COALESCE(p.proconfig, ARRAY[]::text[])
                FROM pg_catalog.pg_trigger t
                JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner
                WHERE n.nspname='public' AND NOT t.tgisinternal
                ORDER BY c.relname,t.tgname
                """
            )
        ).fetchall()
    )


def test_v21_artifact_hash_binds_every_reviewed_current_guard() -> None:
    """Section 15/16 guard names must be normative bytes, not narrative only."""

    assert len(_DEFERRED_TARGETS) == 32
    assert len(_APPEND_ONLY_TARGETS) == 7
    assert len(_BOUND_CONTRACT_TARGETS) == 25
    assert len(_NEW_CURRENT_TARGETS) == 64
    assert len(_RETAINED_TASK5_TASK6_TARGETS) == 9
    assert len(_ALL_CURRENT_TARGETS) == len(_REVIEWED_CURRENT_TRIGGER_NAMES) == 73
    sql = _decoded_sql()
    # Retained Task-5/6 trigger objects are target bytes; only their functions are replaced.
    artifact_targets = {
        tuple(target)
        for row in _artifact()["sql_authority_registry"]["rows"]
        for target in row["target_tuple"]
    }
    assert set(_ALL_CURRENT_TARGETS) <= artifact_targets
    missing = sorted(name for _table, name in _NEW_CURRENT_TARGETS if name not in sql)
    assert missing == []


def test_v21_artifact_hash_binds_run_input_binding_admission_semantics() -> None:
    """The section-6.1 package/proof/run equality guard must be hash-bound."""

    sql = _decoded_sql()
    required_literals = (
        "run_input_bindings",
        "corpus_receipt_sha256",
        "strict_preflight_proofs",
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "SEALED",
    )
    missing = [literal for literal in required_literals if literal not in sql]
    assert missing == []


def test_0038_installs_the_reviewed_current_guard_trigger_census() -> None:
    """A migrated database must expose every named current-table guard."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            installed = {
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    text(
                        """
                        SELECT c.relname,t.tgname
                        FROM pg_catalog.pg_trigger t
                        JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                        WHERE n.nspname='public' AND NOT t.tgisinternal
                          AND t.tgname=ANY(CAST(:names AS text[]))
                        """
                    ),
                    {"names": [name for _table, name in _ALL_CURRENT_TARGETS]},
                )
            }
    assert installed == set(_ALL_CURRENT_TARGETS)


def test_0038_guard_catalog_upgrade_downgrade_upgrade_is_exact() -> None:
    """0038 adds 64 guards plus the hold lock and restores the prior catalog."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            all_before = _all_user_trigger_catalog(connection)
            retained_before = _guard_catalog(
                connection,
                _RETAINED_TASK5_TASK6_TARGETS,
            )
        assert len(retained_before) == 9

        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            all_first = _all_user_trigger_catalog(connection)
            first = _guard_catalog(connection, _ALL_CURRENT_TARGETS)
        assert len(first) == 73
        before_pairs = {(str(row[0]), str(row[1])) for row in all_before}
        first_pairs = {(str(row[0]), str(row[1])) for row in all_first}
        assert first_pairs - before_pairs == {
            *_NEW_CURRENT_TARGETS,
            *_AUXILIARY_0038_TRIGGER_TARGETS,
        }
        assert before_pairs - first_pairs == set()

        downgrade(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            assert _all_user_trigger_catalog(connection) == all_before
            assert _guard_catalog(connection, _RETAINED_TASK5_TASK6_TARGETS) == retained_before
            assert _guard_catalog(connection, _NEW_CURRENT_TARGETS) == ()

        migrate(database.admin_engine, "0038")
        with database.admin_engine.connect() as connection:
            assert _all_user_trigger_catalog(connection) == all_first
            assert _guard_catalog(connection, _ALL_CURRENT_TARGETS) == first


def test_0038_rejects_direct_run_contract_mutation() -> None:
    """Direct SQL cannot rewrite a persisted run's strict contract identity."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            _tenant_id, _deal_id, run_id = _seed_legacy_run(connection)
        migrate(database.admin_engine, "0038")

        with (
            pytest.raises(DBAPIError),
            database.admin_engine.begin() as connection,
        ):
            connection.execute(
                text(
                    """
                    UPDATE runs
                    SET pipeline_contract_hash=:replacement_hash
                    WHERE run_id=CAST(:run_id AS uuid)
                    """
                ),
                {"replacement_hash": "f" * 64, "run_id": run_id},
            )


def _assert_binding_rejection(error: DBAPIError, *, code: str) -> None:
    assert code in str(error.orig)


def test_0038_run_input_binding_accepts_only_reviewed_preimage_and_is_append_only() -> None:
    """Legal admission succeeds; contract/digest drift and later mutation fail."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            tenant_id, deal_id, package_id = _seed_reconstructible_source(connection)
        migrate(database.admin_engine, "0038")
        with database.admin_engine.begin() as connection:
            source = _package_source(connection, package_id)
            graph = _seed_strict_run_for_source(
                connection,
                tenant_id=tenant_id,
                deal_id=deal_id,
                source=source,
            )
        values = _binding_values(
            {
                **graph,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
            }
        )

        wrong_contract = {
            **values,
            "binding_id": str(uuid.uuid4()),
            "pipeline_contract_hash": "f" * 64,
        }
        wrong_contract["binding_sha256"] = _sha256_json(
            [
                "run-input-binding-digest-v2",
                wrong_contract["tenant_id"],
                wrong_contract["deal_id"],
                wrong_contract["run_id"],
                wrong_contract["pipeline_contract_version"],
                wrong_contract["pipeline_contract_hash"],
                wrong_contract["corpus_receipt_id"],
                wrong_contract["source_lineage_object_id"],
                wrong_contract["source_object_kind"],
                wrong_contract["source_disposition"],
                wrong_contract["binding_purpose"],
                wrong_contract["selected_source_version"],
                wrong_contract["selected_identity_sha256"],
                wrong_contract["selected_content_sha256"],
                wrong_contract["corpus_receipt_sha256"],
            ]
        )
        with (
            pytest.raises(DBAPIError) as wrong_contract_error,
            database.admin_engine.begin() as connection,
        ):
            _insert_binding(connection, wrong_contract)
        _assert_binding_rejection(
            wrong_contract_error.value,
            code="STRICT_PREFLIGHT_PROOF_MISMATCH",
        )

        with (
            pytest.raises(DBAPIError) as wrong_digest_error,
            database.admin_engine.begin() as connection,
        ):
            _insert_binding(
                connection,
                {
                    **values,
                    "binding_id": str(uuid.uuid4()),
                    "binding_sha256": "0" * 64,
                },
            )
        _assert_binding_rejection(
            wrong_digest_error.value,
            code="PIPELINE_CONTRACT_HASH_MISMATCH",
        )

        with database.admin_engine.begin() as connection:
            _insert_binding(connection, values)
        with database.admin_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT binding_sha256 FROM run_input_bindings "
                        "WHERE binding_id=CAST(:binding_id AS uuid)"
                    ),
                    {"binding_id": values["binding_id"]},
                ).scalar_one()
                == values["binding_sha256"]
            )

        for statement in (
            "UPDATE run_input_bindings SET binding_sha256=:replacement "
            "WHERE binding_id=CAST(:binding_id AS uuid)",
            "DELETE FROM run_input_bindings WHERE binding_id=CAST(:binding_id AS uuid)",
        ):
            with (
                pytest.raises(DBAPIError) as mutation_error,
                database.admin_engine.begin() as connection,
            ):
                connection.execute(
                    text(statement),
                    {
                        "binding_id": values["binding_id"],
                        "replacement": "e" * 64,
                    },
                )
            assert "STEP_REHYDRATION_FAILED" in str(mutation_error.value.orig)


def test_0038_run_input_binding_requires_proof_source_corpus_equality() -> None:
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            tenant_id, deal_id, package_id = _seed_reconstructible_source(connection)
        migrate(database.admin_engine, "0038")
        with database.admin_engine.begin() as connection:
            source = _package_source(connection, package_id)
            graph = _seed_strict_run_for_source(
                connection,
                tenant_id=tenant_id,
                deal_id=deal_id,
                source=source,
                proof_corpus_hash="d" * 64,
            )
        values = _binding_values(
            {
                **graph,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
            }
        )
        with (
            pytest.raises(DBAPIError) as error,
            database.admin_engine.begin() as connection,
        ):
            _insert_binding(connection, values)
        _assert_binding_rejection(
            error.value,
            code="STRICT_PREFLIGHT_PROOF_MISMATCH",
        )


def test_0038_run_input_binding_requires_a_sealed_package() -> None:
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        tenant_id, deal_id, package_id, source_id = (str(uuid.uuid4()) for _ in range(4))
        source = {
            "source_lineage_object_id": source_id,
            "corpus_receipt_id": package_id,
            "source_object_kind": "PACKAGE",
            "source_disposition": "CORPUS",
            "binding_purpose": "INVENTORY",
            "selected_source_version": "open-source-v1",
            "selected_identity_sha256": "1" * 64,
            "selected_content_sha256": "2" * 64,
            "corpus_receipt_sha256": "3" * 64,
        }
        with (
            pytest.raises(DBAPIError) as error,
            database.admin_engine.begin() as connection,
        ):
            connection.execute(
                text(
                    """
                        INSERT INTO tenants (tenant_id,name)
                        VALUES (CAST(:tenant_id AS uuid),'Task 7 open package tenant');
                        INSERT INTO deals (deal_id,tenant_id,name,created_at)
                        VALUES (
                            CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),
                            'Fictional open package deal',statement_timestamp()
                        );
                        INSERT INTO data_room_packages (
                            package_id,tenant_id,deal_id,status,source_binding_state
                        ) VALUES (
                            CAST(:package_id AS uuid),CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid),'OPEN','UNBOUND'
                        );
                        INSERT INTO source_lineage_objects (
                            source_lineage_object_id,tenant_id,deal_id,
                            corpus_receipt_id,object_kind,source_version,
                            source_disposition,object_identity_sha256,
                            content_sha256,corpus_receipt_sha256,write_origin
                        ) VALUES (
                            CAST(:source_lineage_object_id AS uuid),
                            CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                            CAST(:corpus_receipt_id AS uuid),:source_object_kind,
                            :selected_source_version,:source_disposition,
                            :selected_identity_sha256,:selected_content_sha256,
                            :corpus_receipt_sha256,'RUNTIME'
                        )
                        """
                ),
                {
                    **source,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "package_id": package_id,
                },
            )
            graph = _seed_strict_run_for_source(
                connection,
                tenant_id=tenant_id,
                deal_id=deal_id,
                source=source,
            )
            _insert_binding(
                connection,
                _binding_values(
                    {
                        **graph,
                        "tenant_id": tenant_id,
                        "deal_id": deal_id,
                    }
                ),
            )
        _assert_binding_rejection(
            error.value,
            code="STRICT_PREFLIGHT_PROOF_MISMATCH",
        )


def test_0038_completed_step_without_receipt_fails_at_commit() -> None:
    """Deferred equality rejects a completed strict step with no durable receipt."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            tenant_id, deal_id, package_id = _seed_reconstructible_source(connection)
        migrate(database.admin_engine, "0038")
        step_id = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            source = _package_source(connection, package_id)
            graph = _seed_strict_run_for_source(
                connection,
                tenant_id=tenant_id,
                deal_id=deal_id,
                source=source,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO run_steps (
                        step_id,tenant_id,run_id,step_name,step_order,status,
                        pipeline_contract_version,pipeline_contract_hash,
                        semantic_rule_version,semantic_rule_hash
                    ) VALUES (
                        CAST(:step_id AS uuid),CAST(:tenant_id AS uuid),
                        CAST(:run_id AS uuid),'DATA_ROOM_INVENTORY_PACKAGE',0,
                        'PENDING','full-strict-v21',:contract_hash,
                        'task7-v8-receipt-test-v1',:semantic_hash
                    )
                    """
                ),
                {
                    "step_id": step_id,
                    "tenant_id": tenant_id,
                    "run_id": graph["run_id"],
                    "contract_hash": graph["pipeline_contract_hash"],
                    "semantic_hash": hashlib.sha256(b"task7-v8-receipt-semantic").hexdigest(),
                },
            )

        with (
            pytest.raises(DBAPIError) as error,
            database.admin_engine.begin() as connection,
        ):
            set_tenant_context(connection, tenant_id)
            connection.execute(
                text(
                    "UPDATE run_steps SET status='COMPLETED' WHERE step_id=CAST(:step_id AS uuid)"
                ),
                {"step_id": step_id},
            )
        assert "CONSUMER_RECEIPT_INCOMPLETE" in str(error.value.orig)
        with database.admin_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT status FROM run_steps WHERE step_id=CAST(:step_id AS uuid)"),
                    {"step_id": step_id},
                ).scalar_one()
                == "PENDING"
            )


def test_0038_source_root_rederives_the_exact_native_package_chain() -> None:
    """A one-to-one root count cannot admit a noncanonical source projection."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        with (
            pytest.raises(DBAPIError) as error,
            database.admin_engine.begin() as connection,
        ):
            _insert_bound_package_and_source_root(
                connection,
                write_origin="RUNTIME",
            )
        assert "TASK7_SOURCE_TERMINAL_STATE_INVALID" in str(error.value.orig)


def test_0038_rejects_post_migration_source_backfill_as_an_authority_bypass() -> None:
    """BACKFILL provenance is migration-only and never a runtime write escape."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        with (
            pytest.raises(DBAPIError) as error,
            database.admin_engine.begin() as connection,
        ):
            _insert_bound_package_and_source_root(
                connection,
                write_origin="BACKFILL",
            )
        assert "WORKER_LEASE_FENCED" in str(error.value.orig)


def test_0038_sealed_package_rejects_a_new_unbound_owned_child() -> None:
    """A SEALED/BOUND corpus cannot acquire an UNBOUND package-file member."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            tenant_id, deal_id, package_id = _seed_reconstructible_source(connection)
        migrate(database.admin_engine, "0038")
        new_file_id = str(uuid.uuid4())
        with (
            pytest.raises(DBAPIError) as error,
            database.admin_engine.begin() as connection,
        ):
            set_tenant_context(connection, tenant_id)
            connection.execute(
                text(
                    """
                    INSERT INTO data_room_package_files (
                        file_entry_id,tenant_id,package_id,deal_id,sequence,
                        path_hash,extension,sha256,file_status,support_status,
                        triage_status,parse_status,reason_codes,error_codes,
                        doc_id,document_id,storage_uri,source_binding_state,
                        source_lineage_object_id
                    ) VALUES (
                        CAST(:file_entry_id AS uuid),CAST(:tenant_id AS uuid),
                        CAST(:package_id AS uuid),CAST(:deal_id AS uuid),1,
                        :path_hash,'png',:content_sha256,'supported','supported',
                        'ready','PARSED','["image_only_page"]'::jsonb,
                        '[]'::jsonb,NULL,NULL,:storage_uri,'UNBOUND',NULL
                    )
                    """
                ),
                {
                    "file_entry_id": new_file_id,
                    "tenant_id": tenant_id,
                    "package_id": package_id,
                    "deal_id": deal_id,
                    "path_hash": hashlib.sha256(b"task7-unbound-sealed-child-path").hexdigest(),
                    "content_sha256": hashlib.sha256(b"task7-unbound-sealed-child").hexdigest(),
                    "storage_uri": "object://task7/unbound-sealed-child.png",
                },
            )
        assert "SOURCE_TREE_MUTATED" in str(error.value.orig)
        with database.admin_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM data_room_package_files "
                        "WHERE file_entry_id=CAST(:file_entry_id AS uuid)"
                    ),
                    {"file_entry_id": new_file_id},
                ).scalar_one()
                == 0
            )


@pytest.mark.parametrize(
    "defect",
    (
        "missing_subrole",
        "wrong_kind",
        "wrong_ordinal",
        "wrong_semantic_version",
        "wrong_semantic_hash",
    ),
)
def test_0038_canonical_step_rejects_incomplete_or_drifted_subrole_receipts(
    defect: str,
) -> None:
    """Mandatory role receipts are a registry equality, not aggregate decoration."""

    semantic_hash = "ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83"
    roles = (
        "advocate",
        "sanad_breaker",
        "contradiction_finder",
        "risk_officer",
        "arbiter",
    )
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with (
            pytest.raises(DBAPIError) as error,
            database.app_engine.begin() as connection,
        ):
            _assert_application_role(connection, database.role_name)
            graph = _seed_v8_run_step(
                connection,
                step_name="DEBATE",
                step_order=22,
                semantic_rule_hash=semantic_hash,
                pipeline_contract_version=_STRICT_V21,
            )
            _insert_step_output(
                connection,
                graph,
                subrole="__step__",
                output_kind="DEBATE_RECEIPT_V1",
                output_ordinal=0,
            )
            included_roles = roles[:-1] if defect == "missing_subrole" else roles
            for role in included_roles:
                output_kind = (
                    "WRONG_DEBATE_RECEIPT_V1"
                    if defect == "wrong_kind" and role == "advocate"
                    else "DEBATE_ROLE_RECEIPT_V1"
                )
                output_ordinal = 99 if defect == "wrong_ordinal" and role == "advocate" else 0
                semantic_rule_version = (
                    "wrong" if defect == "wrong_semantic_version" and role == "advocate" else "2"
                )
                row_semantic_hash = (
                    "f" * 64
                    if defect == "wrong_semantic_hash" and role == "advocate"
                    else semantic_hash
                )
                _insert_step_output(
                    connection,
                    graph,
                    subrole=role,
                    output_kind=output_kind,
                    output_ordinal=output_ordinal,
                    semantic_rule_version=semantic_rule_version,
                    semantic_rule_hash=row_semantic_hash,
                )
            connection.execute(
                text(
                    "UPDATE run_steps SET status='COMPLETED' WHERE step_id=CAST(:step_id AS uuid)"
                ),
                {"step_id": graph["step_id"]},
            )
        assert "CONSUMER_RECEIPT_INCOMPLETE" in str(error.value.orig)


@pytest.mark.parametrize(
    (
        "step_name",
        "step_order",
        "semantic_hash",
        "aggregate_kind",
        "required_subroles",
    ),
    _REGISTRY_SUBROLE_STEPS,
)
def test_0038_completed_step_requires_the_exact_registry_subrole_set(
    step_name: str,
    step_order: int,
    semantic_hash: str,
    aggregate_kind: str,
    required_subroles: tuple[tuple[str, str], ...],
) -> None:
    """A complete-looking aggregate cannot mask one omitted registry receipt."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with (
            pytest.raises(DBAPIError) as error,
            database.app_engine.begin() as connection,
        ):
            _assert_application_role(connection, database.role_name)
            graph = _seed_v8_run_step(
                connection,
                step_name=step_name,
                step_order=step_order,
                semantic_rule_hash=semantic_hash,
                pipeline_contract_version=_STRICT_V21,
            )
            _insert_step_output(
                connection,
                graph,
                subrole="__step__",
                output_kind=aggregate_kind,
                output_ordinal=0,
            )
            for subrole, output_kind in required_subroles[:-1]:
                _insert_step_output(
                    connection,
                    graph,
                    subrole=subrole,
                    output_kind=output_kind,
                    output_ordinal=0,
                )
            connection.execute(
                text(
                    "UPDATE run_steps SET status='COMPLETED' WHERE step_id=CAST(:step_id AS uuid)"
                ),
                {"step_id": graph["step_id"]},
            )
        assert "CONSUMER_RECEIPT_INCOMPLETE" in str(error.value.orig)


def test_0038_completed_step_accepts_the_exact_registry_receipt_set() -> None:
    """The full aggregate-plus-role set is legal through the application role."""

    semantic_hash = "ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83"
    required_subroles = tuple((subrole, "DEBATE_ROLE_RECEIPT_V1") for subrole in _DEBATE_SUBROLES)
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with database.app_engine.begin() as connection:
            _assert_application_role(connection, database.role_name)
            graph = _seed_v8_run_step(
                connection,
                step_name="DEBATE",
                step_order=22,
                semantic_rule_hash=semantic_hash,
                pipeline_contract_version=_STRICT_V21,
            )
            _insert_exact_receipt_set(
                connection,
                graph,
                aggregate_kind="DEBATE_RECEIPT_V1",
                required_subroles=required_subroles,
            )
            connection.execute(
                text(
                    "UPDATE run_steps SET status='COMPLETED' WHERE step_id=CAST(:step_id AS uuid)"
                ),
                {"step_id": graph["step_id"]},
            )

        with database.app_engine.connect() as connection:
            set_tenant_context(connection, graph["tenant_id"])
            persisted = connection.execute(
                text(
                    "SELECT s.status,count(so.step_output_id) "
                    "FROM run_steps s JOIN run_step_outputs so "
                    "ON so.tenant_id=s.tenant_id AND so.run_id=s.run_id "
                    "AND so.step_id=s.step_id "
                    "WHERE s.step_id=CAST(:step_id AS uuid) GROUP BY s.status"
                ),
                {"step_id": graph["step_id"]},
            ).one()
        assert tuple(persisted) == ("COMPLETED", 6)


def test_0038_completed_step_rejects_an_extra_receipt_coordinate() -> None:
    """A complete required set cannot hide one unregistered success receipt."""

    semantic_hash = "ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83"
    required_subroles = tuple((subrole, "DEBATE_ROLE_RECEIPT_V1") for subrole in _DEBATE_SUBROLES)
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with (
            pytest.raises(DBAPIError) as error,
            database.app_engine.begin() as connection,
        ):
            _assert_application_role(connection, database.role_name)
            graph = _seed_v8_run_step(
                connection,
                step_name="DEBATE",
                step_order=22,
                semantic_rule_hash=semantic_hash,
                pipeline_contract_version=_STRICT_V21,
            )
            _insert_exact_receipt_set(
                connection,
                graph,
                aggregate_kind="DEBATE_RECEIPT_V1",
                required_subroles=required_subroles,
            )
            _insert_step_output(
                connection,
                graph,
                subrole="unregistered_role",
                output_kind="DEBATE_ROLE_RECEIPT_V1",
                output_ordinal=0,
            )
            connection.execute(
                text(
                    "UPDATE run_steps SET status='COMPLETED' WHERE step_id=CAST(:step_id AS uuid)"
                ),
                {"step_id": graph["step_id"]},
            )
        assert "CONSUMER_RECEIPT_INCOMPLETE" in str(error.value.orig)


def test_0038_pending_step_rejects_a_success_receipt() -> None:
    """A nonterminal strict/resumable step cannot own a success root."""

    semantic_hash = "6634007f7e2d2ac83631d9bb386450de73bd30ca1e8ee5529de10cba9994df03"
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with (
            pytest.raises(DBAPIError) as error,
            database.app_engine.begin() as connection,
        ):
            _assert_application_role(connection, database.role_name)
            graph = _seed_v8_run_step(
                connection,
                step_name="INGEST_CHECK",
                step_order=2,
                semantic_rule_hash=semantic_hash,
                pipeline_contract_version=_STRICT_V21,
            )
            _insert_step_output(
                connection,
                graph,
                subrole="__step__",
                output_kind="STATUS_ONLY_INGEST_CHECK_V1",
                output_ordinal=0,
            )
        assert "CONSUMER_RECEIPT_INCOMPLETE" in str(error.value.orig)


@pytest.mark.parametrize(
    ("pipeline_contract_version", "pipeline_resume_state"),
    (
        pytest.param("full-v1", "RESUMABLE", id="legacy-contract"),
        pytest.param("full-strict-v18", "RESUMABLE", id="superseded-v18"),
        pytest.param(_STRICT_V21, "NON_RESUMABLE", id="v21-non-resumable"),
    ),
)
def test_0038_receipt_equality_excludes_legacy_and_non_resumable_runs(
    pipeline_contract_version: str,
    pipeline_resume_state: str,
) -> None:
    """The new receipt predicate never fabricates requirements for excluded runs."""

    semantic_hash = "6634007f7e2d2ac83631d9bb386450de73bd30ca1e8ee5529de10cba9994df03"
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with database.app_engine.begin() as connection:
            _assert_application_role(connection, database.role_name)
            graph = _seed_v8_run_step(
                connection,
                step_name="INGEST_CHECK",
                step_order=2,
                semantic_rule_hash=semantic_hash,
                pipeline_contract_version=pipeline_contract_version,
                pipeline_resume_state=pipeline_resume_state,
            )
            _insert_step_output(
                connection,
                graph,
                subrole="outside_v21_resumable",
                output_kind="OUTSIDE_V21_RESUMABLE_RECEIPT_V1",
                output_ordinal=91,
            )

        with database.app_engine.connect() as connection:
            set_tenant_context(connection, graph["tenant_id"])
            persisted = connection.execute(
                text(
                    "SELECT s.status,count(so.step_output_id) "
                    "FROM run_steps s JOIN run_step_outputs so "
                    "ON so.tenant_id=s.tenant_id AND so.run_id=s.run_id "
                    "AND so.step_id=s.step_id "
                    "WHERE s.step_id=CAST(:step_id AS uuid) GROUP BY s.status"
                ),
                {"step_id": graph["step_id"]},
            ).one()
        assert tuple(persisted) == ("PENDING", 1)


def test_0038_consumer_receipt_rejects_a_native_row_instead_of_step_receipt() -> None:
    """An edge receipt must target the exact STEP_RECEIPT, not any step output."""

    semantic_hash = "672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d"
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        expected_edge_id = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            graph = _seed_v8_run_step(
                connection,
                step_name="DELIVERABLES",
                step_order=27,
                semantic_rule_hash=semantic_hash,
                pipeline_contract_version=_STRICT_V21,
            )
            native = _insert_canonical_deliverable_step_output(connection, graph)
            connection.execute(
                text(
                    """
                    INSERT INTO run_expected_consumer_edges (
                        expected_edge_id,tenant_id,deal_id,run_id,
                        producer_output_object_id,consumer_step_name,
                        consumer_subrole,usage_kind,contract_rule_id,
                        contract_rule_version,contract_rule_hash,
                        edge_tuple_sha256,materialization_origin,
                        lease_owner_id,lease_fencing_token
                    ) VALUES (
                        CAST(:expected_edge_id AS uuid),
                        CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                        CAST(:run_id AS uuid),CAST(:output_object_id AS uuid),
                        'DELIVERABLES','__step__','TASK7_TARGET_CLASS_PROOF',
                        'E_TEST_TARGET_CLASS','1',:contract_rule_hash,
                        :edge_tuple_sha256,'WORKER',
                        CAST(:lease_owner_id AS uuid),1
                    )
                    """
                ),
                {
                    **graph,
                    **native,
                    "expected_edge_id": expected_edge_id,
                    "contract_rule_hash": hashlib.sha256(b"task7-target-class-rule").hexdigest(),
                    "edge_tuple_sha256": hashlib.sha256(b"task7-target-class-edge").hexdigest(),
                },
            )

        with (
            pytest.raises(DBAPIError) as error,
            database.app_engine.begin() as connection,
        ):
            _assert_application_role(connection, database.role_name)
            set_tenant_context(connection, graph["tenant_id"])
            connection.execute(
                text(
                    """
                    SELECT
                        set_config('idis.worker_lease_tenant_id', :tenant_id, true),
                        set_config('idis.worker_lease_deal_id', :deal_id, true),
                        set_config('idis.worker_lease_run_id', :run_id, true),
                        set_config('idis.worker_lease_owner_id', :lease_owner_id, true),
                        set_config('idis.worker_lease_fencing_token', '1', true)
                    """
                ),
                graph,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO run_consumer_receipts (
                        receipt_id,tenant_id,deal_id,run_id,expected_edge_id,
                        consumer_step_output_id,consumer_output_object_id,
                        consumer_step_name,consumer_subrole,
                        usage_evidence_sha256,consumed_at,lease_owner_id,
                        lease_fencing_token
                    ) VALUES (
                        CAST(:receipt_id AS uuid),CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid),CAST(:run_id AS uuid),
                        CAST(:expected_edge_id AS uuid),
                        CAST(:step_output_id AS uuid),
                        CAST(:output_object_id AS uuid),
                        'DELIVERABLES','__step__',
                        :usage_evidence_sha256,statement_timestamp(),
                        CAST(:lease_owner_id AS uuid),1
                    )
                    """
                ),
                {
                    **graph,
                    **native,
                    "expected_edge_id": expected_edge_id,
                    "receipt_id": str(uuid.uuid4()),
                    "usage_evidence_sha256": hashlib.sha256(
                        b"task7-target-class-usage"
                    ).hexdigest(),
                },
            )
        assert "CONSUMER_RECEIPT_INCOMPLETE" in str(error.value.orig)


@pytest.mark.parametrize("defect", ("member_projection", "reference_digest"))
def test_0038_manifest_count_fifteen_still_rejects_hash_projection_drift(
    defect: str,
) -> None:
    """The 14+1 count cannot mask a projection or reference-digest defect."""

    semantic_hash = "672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d"
    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        with (
            pytest.raises(DBAPIError) as error,
            database.admin_engine.begin() as connection,
        ):
            graph = _seed_v8_run_step(
                connection,
                step_name="DELIVERABLES",
                step_order=27,
                semantic_rule_hash=semantic_hash,
                pipeline_contract_version=_STRICT_V9,
            )
            manifest_root = _insert_step_output(
                connection,
                graph,
                subrole="product_bundle_manifest",
                output_kind="DELIVERABLE_ARTIFACT",
                output_ordinal=14,
                output_class="NATIVE_ROW",
            )
            manifest = _insert_deliverable(
                connection,
                graph,
                output_object_id=manifest_root["output_object_id"],
                catalog_ordinal=14,
            )

            for catalog_ordinal in range(14):
                member_root = _insert_step_output(
                    connection,
                    graph,
                    subrole=f"artifact_{catalog_ordinal}",
                    output_kind="DELIVERABLE_ARTIFACT",
                    output_ordinal=catalog_ordinal,
                    output_class="NATIVE_ROW",
                )
                member = _insert_deliverable(
                    connection,
                    graph,
                    output_object_id=member_root["output_object_id"],
                    catalog_ordinal=catalog_ordinal,
                )
                reference_root = _insert_step_output(
                    connection,
                    graph,
                    subrole=f"manifest_reference_{catalog_ordinal}",
                    output_kind="DELIVERABLE_MANIFEST_REFERENCE",
                    output_ordinal=100 + catalog_ordinal,
                    output_class="NATIVE_ROW",
                )
                identity_sha256 = member_root["native_identity_sha256"]
                if defect == "member_projection" and catalog_ordinal == 0:
                    identity_sha256 = "f" * 64
                reference_sha256 = _manifest_reference_digest(
                    graph,
                    manifest_deliverable_id=manifest["deliverable_id"],
                    member_kind="ARTIFACT",
                    catalog_ordinal=catalog_ordinal,
                    member_step_output_id=member_root["step_output_id"],
                    member_output_object_id=member_root["output_object_id"],
                    member_identity_sha256=identity_sha256,
                    member_value_sha256=member_root["native_output_sha256"],
                    member_content_sha256=member["content_sha256"],
                )
                if defect == "reference_digest" and catalog_ordinal == 0:
                    reference_sha256 = "f" * 64
                _insert_manifest_reference(
                    connection,
                    graph,
                    manifest_deliverable_id=manifest["deliverable_id"],
                    manifest_output_object_id=manifest_root["output_object_id"],
                    reference_output_object_id=reference_root["output_object_id"],
                    member_kind="ARTIFACT",
                    catalog_ordinal=catalog_ordinal,
                    member_step_output_id=member_root["step_output_id"],
                    member_output_object_id=member_root["output_object_id"],
                    member_identity_sha256=identity_sha256,
                    member_value_sha256=member_root["native_output_sha256"],
                    member_content_sha256=member["content_sha256"],
                    reference_sha256=reference_sha256,
                )

            reconciliation_root = _insert_step_output(
                connection,
                graph,
                subrole="reconciliation",
                output_kind="RECONCILIATION_RECEIPT_V1",
                output_ordinal=15,
            )
            reconciliation_reference_root = _insert_step_output(
                connection,
                graph,
                subrole="manifest_reference_reconciliation",
                output_kind="DELIVERABLE_MANIFEST_REFERENCE",
                output_ordinal=200,
                output_class="NATIVE_ROW",
            )
            _insert_manifest_reference(
                connection,
                graph,
                manifest_deliverable_id=manifest["deliverable_id"],
                manifest_output_object_id=manifest_root["output_object_id"],
                reference_output_object_id=reconciliation_reference_root["output_object_id"],
                member_kind="RECONCILIATION",
                catalog_ordinal=None,
                member_step_output_id=reconciliation_root["step_output_id"],
                member_output_object_id=reconciliation_root["output_object_id"],
                member_identity_sha256=reconciliation_root["native_identity_sha256"],
                member_value_sha256=reconciliation_root["native_output_sha256"],
                member_content_sha256=None,
                reference_sha256=_manifest_reference_digest(
                    graph,
                    manifest_deliverable_id=manifest["deliverable_id"],
                    member_kind="RECONCILIATION",
                    catalog_ordinal=None,
                    member_step_output_id=reconciliation_root["step_output_id"],
                    member_output_object_id=reconciliation_root["output_object_id"],
                    member_identity_sha256=reconciliation_root["native_identity_sha256"],
                    member_value_sha256=reconciliation_root["native_output_sha256"],
                    member_content_sha256=None,
                ),
            )
            assert (
                connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM deliverable_manifest_references
                        WHERE manifest_deliverable_id=
                              CAST(:manifest_deliverable_id AS uuid)
                        """
                    ),
                    {"manifest_deliverable_id": manifest["deliverable_id"]},
                ).scalar_one()
                == 15
            )
        assert "CONSUMER_RECEIPT_INCOMPLETE" in str(error.value.orig)


def test_0038_rejects_post_migration_output_backfill_as_a_lease_bypass() -> None:
    """A strict runtime output cannot claim migration-only BACKFILL provenance."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0038")
        with (
            pytest.raises(DBAPIError) as error,
            database.admin_engine.begin() as connection,
        ):
            graph = _seed_v8_run_step(
                connection,
                step_name="DELIVERABLES",
                step_order=27,
                semantic_rule_hash=(
                    "672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d"
                ),
                pipeline_contract_version=_STRICT_V9,
            )
            native = _insert_step_output(
                connection,
                graph,
                subrole="screening_snapshot_pdf",
                output_kind="DELIVERABLE_ARTIFACT",
                output_ordinal=0,
                output_class="NATIVE_ROW",
                root_write_origin="BACKFILL",
            )
            _insert_deliverable(
                connection,
                graph,
                output_object_id=native["output_object_id"],
                catalog_ordinal=0,
            )
        assert "WORKER_LEASE_FENCED" in str(error.value.orig)


def test_0038_rejects_noncanonical_lease_free_admission_edge() -> None:
    """ADMISSION is limited to the exact E001/E002 queued-run transaction."""

    with disposable_postgres() as database:
        _provision(database)
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            tenant_id, deal_id, package_id = _seed_reconstructible_source(connection)
        migrate(database.admin_engine, "0038")
        with (
            pytest.raises(DBAPIError) as error,
            database.admin_engine.begin() as connection,
        ):
            source = _package_source(connection, package_id)
            graph = _seed_strict_run_for_source(
                connection,
                tenant_id=tenant_id,
                deal_id=deal_id,
                source=source,
                pipeline_contract_version=_STRICT_V9,
                pipeline_contract_hash="9" * 64,
            )
            binding = _binding_values(graph)
            _insert_binding(connection, binding)
            step_id = str(uuid.uuid4())
            connection.execute(
                text(
                    """
                    INSERT INTO run_steps (
                        step_id,tenant_id,run_id,step_name,step_order,status,
                        pipeline_contract_version,pipeline_contract_hash,
                        semantic_rule_version,semantic_rule_hash
                    ) VALUES (
                        CAST(:step_id AS uuid),CAST(:tenant_id AS uuid),
                        CAST(:run_id AS uuid),'DATA_ROOM_INVENTORY_PACKAGE',0,
                        'PENDING',:pipeline_contract_version,
                        :pipeline_contract_hash,'2',:semantic_rule_hash
                    );
                    INSERT INTO run_expected_consumer_edges (
                        expected_edge_id,tenant_id,deal_id,run_id,
                        producer_input_binding_id,consumer_step_name,
                        consumer_subrole,usage_kind,contract_rule_id,
                        contract_rule_version,contract_rule_hash,
                        edge_tuple_sha256,materialization_origin,
                        lease_owner_id,lease_fencing_token
                    ) VALUES (
                        CAST(:expected_edge_id AS uuid),
                        CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                        CAST(:run_id AS uuid),CAST(:binding_id AS uuid),
                        'DATA_ROOM_INVENTORY_PACKAGE','__step__',
                        'SOURCE_INVENTORY','E999_NOT_ADMISSION','1',
                        :contract_rule_hash,:edge_tuple_sha256,'ADMISSION',
                        NULL,0
                    )
                    """
                ),
                {
                    **graph,
                    **binding,
                    "step_id": step_id,
                    "semantic_rule_hash": (
                        "7fb7c7d9f4af3bc9c0f224aa9a868dc20c3f77cc22c35f0d2f5f4b4bf3e9ef06"
                    ),
                    "expected_edge_id": str(uuid.uuid4()),
                    "contract_rule_hash": hashlib.sha256(
                        b"task7-noncanonical-admission-rule"
                    ).hexdigest(),
                    "edge_tuple_sha256": hashlib.sha256(
                        b"task7-noncanonical-admission-edge"
                    ).hexdigest(),
                },
            )
        assert "WORKER_LEASE_FENCED" in str(error.value.orig)


def test_0038_runtime_output_rejects_stale_lease_through_direct_sql() -> None:
    """A stale worker token is fenced before its output row can be persisted."""

    with _task7_rls_database() as fixture:
        lease_owner_id = str(uuid.uuid4())
        with fixture.database.admin_engine.begin() as connection:
            run = connection.execute(
                text(
                    """
                    SELECT run_id::text,deal_id::text,pipeline_contract_version,
                           pipeline_contract_hash
                    FROM runs
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                    ORDER BY run_id
                    LIMIT 1
                    """
                ),
                {"tenant_id": OWNER_TENANT_ID},
            ).one()
            connection.execute(
                text(
                    """
                    UPDATE runs
                    SET status='RUNNING',
                        lease_owner_id=CAST(:lease_owner_id AS uuid),
                        lease_fencing_token=2,
                        lease_expires_at=statement_timestamp()+interval '1 hour',
                        last_heartbeat_at=statement_timestamp()
                    WHERE run_id=CAST(:run_id AS uuid)
                    """
                ),
                {"lease_owner_id": lease_owner_id, "run_id": str(run[0])},
            )

        values = {
            "output_object_id": str(uuid.uuid4()),
            "tenant_id": OWNER_TENANT_ID,
            "deal_id": str(run[1]),
            "run_id": str(run[0]),
            "output_kind": "TASK7_STALE_LEASE_PROOF",
            "native_identity_sha256": "4" * 64,
            "native_output_sha256": "5" * 64,
            "pipeline_contract_version": str(run[2]),
            "pipeline_contract_hash": str(run[3]),
            "lease_owner_id": lease_owner_id,
        }
        statement = text(
            """
            INSERT INTO run_output_objects (
                output_object_id,tenant_id,deal_id,run_id,output_class,
                output_kind,output_schema_version,native_identity_sha256,
                native_output_sha256,pipeline_contract_version,
                pipeline_contract_hash,write_origin,lease_owner_id,
                lease_fencing_token
            ) VALUES (
                CAST(:output_object_id AS uuid),CAST(:tenant_id AS uuid),
                CAST(:deal_id AS uuid),CAST(:run_id AS uuid),'STEP_RECEIPT',
                :output_kind,'task7-stale-lease-v1',:native_identity_sha256,
                :native_output_sha256,:pipeline_contract_version,
                :pipeline_contract_hash,'RUNTIME',
                CAST(:lease_owner_id AS uuid),1
            )
            """
        )
        with (
            pytest.raises(DBAPIError) as error,
            fixture.database.admin_engine.begin() as connection,
        ):
            connection.execute(statement, values)
        assert "WORKER_LEASE_FENCED" in str(error.value.orig)

        with fixture.database.admin_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM run_output_objects "
                        "WHERE output_object_id=CAST(:output_object_id AS uuid)"
                    ),
                    {"output_object_id": values["output_object_id"]},
                ).scalar_one()
                == 0
            )
