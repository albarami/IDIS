"""Disposable-PostgreSQL RED contracts for run-global defect ownership."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from idis.models.methodology_extraction import (
    MethodologyFactualOutput,
    MethodologyResultDefectLink,
    canonical_json_bytes,
    generate_methodology_result_link_id,
)
from idis.persistence.repositories.methodology_extraction import (
    METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED,
    MethodologyExtractionRepositoryError,
    PostgresMethodologyExtractionRepository,
)
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
    set_tenant_context,
)
from tests.slice100_task6.test_postgres_0037_red import (
    CANONICAL_FACTUAL_OUTPUT,
    CLAIM_CANONICAL_KEY_SHA256,
    CLAIM_ID,
    COVERAGE_ID,
    DEAL_ID,
    DEFECT_ID,
    DOCUMENT_ID,
    EVIDENCE_ID,
    FACTUAL_RECORD_ID,
    HASH_A,
    HASH_B,
    LEASE_OWNER_ID,
    LOCATOR_HASH,
    RESULT_ID,
    RUN_ID,
    SANAD_ID,
    SECOND_LOCATOR_HASH,
    SECOND_SPAN_ID,
    SELECTION_ID,
    SOURCE_CONTENT_SHA256,
    SPAN_ID,
    STEP_ID,
    TASK_ID,
    TENANT_A,
    _insert_second_admitted_span,
    _insert_task6_graph,
    _seed_task6_dependencies,
    _set_active_task6_write_context,
)
from tests.slice100_task6.test_postgres_canonical_lineage_guards_red import (
    VALID_EVIDENCE_LINK_ID,
    VALID_SANAD_LINK_ID,
    _insert_claim_link,
    _insert_evidence_link,
    _insert_sanad_link,
)

SECOND_FACTUAL_RECORD_ID = "61616161-6161-4161-8161-616161616161"
SECOND_EVIDENCE_ID = "62626262-6262-4262-8262-626262626262"
SECOND_SANAD_ID = "63636363-6363-4363-8363-636363636363"
SECOND_CLAIM_LINK_ID = "64646464-6464-4464-8464-646464646464"
SECOND_EVIDENCE_LINK_ID = "65656565-6565-4565-8565-656565656565"
SECOND_SANAD_LINK_ID = "67676767-6767-4767-8767-676767676767"
FIRST_DEFECT_LINK_ID = "68686868-6868-4868-8868-686868686868"
SECOND_DEFECT_LINK_ID = "69696969-6969-4969-8969-696969696969"

SECOND_RUN_ID = "71717171-7171-4171-8171-717171717171"
SECOND_RUN_STEP_ID = "72727272-7272-4272-8272-727272727272"
SECOND_RUN_SELECTION_ID = "73737373-7373-4373-8373-737373737373"
SECOND_RUN_COVERAGE_ID = "mc_" + "7" * 24
SECOND_RUN_TASK_ID = "74747474-7474-4474-8474-747474747474"
SECOND_RUN_TASK_SPAN_ID = "75757575-7575-4575-8575-757575757575"
SECOND_RUN_ATTEMPT_ID = "76767676-7676-4676-8676-767676767676"
SECOND_RUN_RESERVATION_ID = "77767676-7676-4676-8676-767676767677"
SECOND_RUN_RESULT_ID = "78787878-7878-4878-8878-787878787878"
SECOND_RUN_FACTUAL_RECORD_ID = "79797979-7979-4979-8979-797979797979"
SECOND_RUN_CLAIM_LINK_ID = "81818181-8181-4181-8181-818181818181"
SECOND_RUN_EVIDENCE_LINK_ID = "82828282-8282-4282-8282-828282828282"
SECOND_RUN_SANAD_LINK_ID = "83838383-8383-4383-8383-838383838383"
SECOND_RUN_DEFECT_LINK_ID = "84848484-8484-4484-8484-848484848484"


def _second_factual_output() -> MethodologyFactualOutput:
    payload = CANONICAL_FACTUAL_OUTPUT.model_dump(mode="json")
    payload.pop("factual_output_id")
    payload["accepted_source_span_ids"] = [SECOND_SPAN_ID]
    payload["accepted_locator_sha256s"] = [SECOND_LOCATOR_HASH]
    return MethodologyFactualOutput(**payload)


SECOND_FACTUAL_OUTPUT = _second_factual_output()
SECOND_FACTUAL_ID = str(SECOND_FACTUAL_OUTPUT.factual_output_id)
SECOND_FACTUAL_PAYLOAD = SECOND_FACTUAL_OUTPUT.model_dump(mode="json")
SECOND_FACTUAL_PAYLOAD_SHA256 = hashlib.sha256(
    canonical_json_bytes(SECOND_FACTUAL_PAYLOAD)
).hexdigest()
SECOND_LOCATOR_SET_SHA256 = hashlib.sha256(
    canonical_json_bytes(
        sorted(
            zip(
                SECOND_FACTUAL_OUTPUT.accepted_source_span_ids,
                SECOND_FACTUAL_OUTPUT.accepted_locator_sha256s,
                strict=True,
            )
        )
    )
).hexdigest()


def _prepare_base_graph(database: DisposablePostgres, *, include_links: bool) -> None:
    migrate(database.admin_engine, "0037")
    grant_application_access(database)
    _seed_task6_dependencies(database)
    _insert_task6_graph(database, include_links=include_links)


def _insert_base_links_without_defect(database: DisposablePostgres) -> None:
    _insert_claim_link(database)
    _insert_evidence_link(
        database,
        evidence_link_id=VALID_EVIDENCE_LINK_ID,
        evidence_id=EVIDENCE_ID,
        source_span_id=SPAN_ID,
    )
    _insert_sanad_link(
        database,
        sanad_link_id=VALID_SANAD_LINK_ID,
        sanad_id=SANAD_ID,
        primary_evidence_id=EVIDENCE_ID,
    )


def _insert_second_factual_lineage(database: DisposablePostgres) -> None:
    """Insert a second valid output with distinct evidence/Sanad lineage."""
    _insert_second_admitted_span(database)
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
                INSERT INTO evidence_items (
                    evidence_id, tenant_id, deal_id, claim_id, source_span_id,
                    source_grade, source_system, upstream_origin_id,
                    retrieval_timestamp, verification_status, rationale, updated_at
                ) VALUES (
                    CAST(:evidence_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:claim_id AS uuid),
                    CAST(:span_id AS uuid), 'B', 'fixture', 'fixture-origin-second',
                    now(), 'UNVERIFIED', '{}'::jsonb, now()
                );
                INSERT INTO sanads (
                    sanad_id, tenant_id, claim_id, deal_id, primary_evidence_id,
                    created_at
                ) VALUES (
                    CAST(:sanad_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:claim_id AS uuid), CAST(:deal_id AS uuid),
                    CAST(:evidence_id AS uuid), now()
                );
                INSERT INTO methodology_result_factual_outputs (
                    factual_output_record_id, tenant_id, deal_id, run_id,
                    result_id, task_record_id, output_ordinal, factual_output_id,
                    typed_output_payload, typed_output_sha256,
                    accepted_source_span_ids, accepted_locator_sha256s,
                    accepted_locator_set_sha256
                ) VALUES (
                    CAST(:factual_record_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:task_id AS uuid), 2,
                    CAST(:factual_output_id AS uuid), CAST(:factual_payload AS jsonb),
                    :factual_payload_sha256, ARRAY[CAST(:span_id AS uuid)],
                    ARRAY[:locator_sha256], :locator_set_sha256
                );
                INSERT INTO methodology_result_claim_links (
                    claim_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, selection_id, coverage_record_id,
                    methodology_version_record_id,
                    methodology_question_record_id, claim_id,
                    canonical_key_sha256, reused_existing_claim
                ) VALUES (
                    CAST(:claim_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_record_id AS uuid),
                    CAST(:selection_id AS uuid), :coverage_record_id,
                    CAST(:version_record_id AS uuid),
                    CAST(:question_record_id AS uuid), CAST(:claim_id AS uuid),
                    :canonical_key_sha256, true
                );
                INSERT INTO methodology_result_evidence_links (
                    evidence_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, claim_link_id, evidence_id,
                    document_id, source_span_id, locator_sha256
                ) VALUES (
                    CAST(:evidence_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_record_id AS uuid),
                    CAST(:claim_link_id AS uuid), CAST(:evidence_id AS uuid),
                    CAST(:document_id AS uuid), CAST(:span_id AS uuid),
                    :locator_sha256
                );
                INSERT INTO methodology_result_sanad_links (
                    sanad_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, claim_link_id, sanad_id,
                    primary_evidence_id
                ) VALUES (
                    CAST(:sanad_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_record_id AS uuid),
                    CAST(:claim_link_id AS uuid), CAST(:sanad_id AS uuid),
                    CAST(:evidence_id AS uuid)
                )
                """
            ),
            {
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "result_id": RESULT_ID,
                "task_id": TASK_ID,
                "factual_record_id": SECOND_FACTUAL_RECORD_ID,
                "factual_output_id": SECOND_FACTUAL_ID,
                "factual_payload": json.dumps(SECOND_FACTUAL_PAYLOAD),
                "factual_payload_sha256": SECOND_FACTUAL_PAYLOAD_SHA256,
                "span_id": SECOND_SPAN_ID,
                "locator_sha256": SECOND_LOCATOR_HASH,
                "locator_set_sha256": SECOND_LOCATOR_SET_SHA256,
                "selection_id": SELECTION_ID,
                "coverage_record_id": COVERAGE_ID,
                "version_record_id": "15151515-1515-4151-8151-151515151515",
                "question_record_id": "17171717-1717-4171-8171-171717171717",
                "claim_id": CLAIM_ID,
                "canonical_key_sha256": CLAIM_CANONICAL_KEY_SHA256,
                "claim_link_id": SECOND_CLAIM_LINK_ID,
                "evidence_id": SECOND_EVIDENCE_ID,
                "evidence_link_id": SECOND_EVIDENCE_LINK_ID,
                "sanad_id": SECOND_SANAD_ID,
                "sanad_link_id": SECOND_SANAD_LINK_ID,
                "document_id": DOCUMENT_ID,
            },
        )


def _defect_link_payload(
    *,
    defect_link_id: str,
    factual_output_record_id: str,
    sanad_link_id: str,
) -> dict[str, str]:
    return {
        "defect_link_id": defect_link_id,
        "tenant_id": TENANT_A,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "result_id": RESULT_ID,
        "factual_output_record_id": factual_output_record_id,
        "sanad_link_id": sanad_link_id,
        "defect_id": DEFECT_ID,
    }


def _repository_defect_link(
    *,
    factual_output_record_id: str,
    sanad_link_id: str,
) -> MethodologyResultDefectLink:
    logical_record = {
        "tenant_id": TENANT_A,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "result_id": RESULT_ID,
        "factual_output_record_id": factual_output_record_id,
        "sanad_link_id": sanad_link_id,
        "defect_id": DEFECT_ID,
    }
    return MethodologyResultDefectLink(
        defect_link_id=generate_methodology_result_link_id(
            table="methodology_result_defect_links",
            logical_record=logical_record,
        ),
        **logical_record,
    )


def _insert_defect_link(
    database: DisposablePostgres,
    *,
    defect_link_id: str,
    factual_output_record_id: str,
    sanad_link_id: str,
) -> None:
    payload = _defect_link_payload(
        defect_link_id=defect_link_id,
        factual_output_record_id=factual_output_record_id,
        sanad_link_id=sanad_link_id,
    )
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
                INSERT INTO methodology_result_defect_links (
                    defect_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, sanad_link_id, defect_id
                ) VALUES (
                    CAST(:defect_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid),
                    CAST(:factual_output_record_id AS uuid),
                    CAST(:sanad_link_id AS uuid), CAST(:defect_id AS uuid)
                )
                """
            ),
            payload,
        )


def _defect_link_count(database: DisposablePostgres, run_id: str = RUN_ID) -> int:
    with database.admin_engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    """
                    SELECT count(*) FROM methodology_result_defect_links
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "run_id": run_id},
            ).scalar_one()
        )


def _insert_second_run_with_same_canonical_defect(database: DisposablePostgres) -> None:
    with database.admin_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE run_steps
                   SET status = 'COMPLETED', finished_at = now()
                 WHERE tenant_id = CAST(:tenant_id AS uuid)
                   AND run_id = CAST(:source_run_id AS uuid)
                   AND step_id = CAST(:source_step_id AS uuid);
                UPDATE runs
                   SET status = 'SUCCEEDED', finished_at = now()
                 WHERE tenant_id = CAST(:tenant_id AS uuid)
                   AND deal_id = CAST(:deal_id AS uuid)
                   AND run_id = CAST(:source_run_id AS uuid)
                """
            ),
            {
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "source_run_id": RUN_ID,
                "source_step_id": STEP_ID,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO runs (
                    run_id, tenant_id, deal_id, mode, status,
                    lease_owner_id, lease_fencing_token,
                    lease_expires_at, last_heartbeat_at
                ) VALUES (
                    CAST(:run_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), 'FULL', 'RUNNING',
                    CAST(:lease_owner_id AS uuid), 7,
                    now() + interval '1 hour', now()
                );
                INSERT INTO run_steps (
                    step_id, tenant_id, run_id, step_name, step_order, status
                ) SELECT CAST(:step_id AS uuid), tenant_id, CAST(:run_id AS uuid),
                         step_name, step_order, 'RUNNING'
                  FROM run_steps
                 WHERE tenant_id = CAST(:tenant_id AS uuid)
                   AND run_id = CAST(:source_run_id AS uuid)
                   AND step_id = CAST(:source_step_id AS uuid);
                INSERT INTO run_methodology_selections (
                    selection_id, tenant_id, deal_id, run_id,
                    commercial_dd_version_record_id,
                    commercial_methodology_version_id, commercial_version_hash,
                    financial_dd_version_record_id,
                    financial_methodology_version_id, financial_version_hash,
                    selection_hash, selected_question_count,
                    created_by_actor_id, selection_audit_event_id
                ) SELECT CAST(:selection_id AS uuid), tenant_id, deal_id,
                         CAST(:run_id AS uuid), commercial_dd_version_record_id,
                         commercial_methodology_version_id, commercial_version_hash,
                         financial_dd_version_record_id,
                         financial_methodology_version_id, financial_version_hash,
                         selection_hash, selected_question_count,
                         created_by_actor_id, gen_random_uuid()
                    FROM run_methodology_selections
                   WHERE tenant_id = CAST(:tenant_id AS uuid)
                     AND deal_id = CAST(:deal_id AS uuid)
                     AND run_id = CAST(:source_run_id AS uuid)
                     AND selection_id = CAST(:source_selection_id AS uuid);
                INSERT INTO methodology_coverage_records (
                    coverage_record_id, tenant_id, deal_id, run_id, selection_id,
                    methodology_version_record_id, methodology_version_id,
                    methodology_question_record_id, methodology_question_id,
                    methodology_type, status
                ) SELECT :coverage_record_id, tenant_id, deal_id,
                         CAST(:run_id AS uuid), CAST(:selection_id AS uuid),
                         methodology_version_record_id, methodology_version_id,
                         methodology_question_record_id, methodology_question_id,
                         methodology_type, status
                    FROM methodology_coverage_records
                   WHERE tenant_id = CAST(:tenant_id AS uuid)
                     AND deal_id = CAST(:deal_id AS uuid)
                     AND run_id = CAST(:source_run_id AS uuid)
                     AND coverage_record_id = :source_coverage_record_id
                """
            ),
            {
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": SECOND_RUN_ID,
                "lease_owner_id": LEASE_OWNER_ID,
                "step_id": SECOND_RUN_STEP_ID,
                "source_run_id": RUN_ID,
                "source_step_id": STEP_ID,
                "selection_id": SECOND_RUN_SELECTION_ID,
                "source_selection_id": SELECTION_ID,
                "coverage_record_id": SECOND_RUN_COVERAGE_ID,
                "source_coverage_record_id": COVERAGE_ID,
            },
        )

    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
        repository._require_fence(
            deal_id=DEAL_ID,
            run_id=SECOND_RUN_ID,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=7,
        )
        params = {
            "tenant_id": TENANT_A,
            "deal_id": DEAL_ID,
            "run_id": SECOND_RUN_ID,
            "selection_id": SECOND_RUN_SELECTION_ID,
            "coverage_record_id": SECOND_RUN_COVERAGE_ID,
            "step_id": SECOND_RUN_STEP_ID,
            "task_id": SECOND_RUN_TASK_ID,
            "task_span_id": SECOND_RUN_TASK_SPAN_ID,
            "attempt_id": SECOND_RUN_ATTEMPT_ID,
            "reservation_id": SECOND_RUN_RESERVATION_ID,
            "result_id": SECOND_RUN_RESULT_ID,
            "factual_record_id": SECOND_RUN_FACTUAL_RECORD_ID,
            "claim_link_id": SECOND_RUN_CLAIM_LINK_ID,
            "evidence_link_id": SECOND_RUN_EVIDENCE_LINK_ID,
            "sanad_link_id": SECOND_RUN_SANAD_LINK_ID,
            "defect_link_id": SECOND_RUN_DEFECT_LINK_ID,
            "lease_owner_id": LEASE_OWNER_ID,
            "document_id": DOCUMENT_ID,
            "span_id": SPAN_ID,
            "source_content_sha256": SOURCE_CONTENT_SHA256,
            "locator_sha256": LOCATOR_HASH,
            "claim_id": CLAIM_ID,
            "canonical_key_sha256": CLAIM_CANONICAL_KEY_SHA256,
            "evidence_id": EVIDENCE_ID,
            "sanad_id": SANAD_ID,
            "defect_id": DEFECT_ID,
            "hash_a": HASH_A,
            "hash_b": HASH_B,
        }
        connection.execute(
            text(
                """
                INSERT INTO methodology_extraction_tasks (
                    task_record_id, tenant_id, deal_id, run_id, selection_id,
                    coverage_record_id, methodology_version_record_id,
                    methodology_version_id, methodology_question_record_id,
                    methodology_question_id, methodology_type, extraction_task_id,
                    document_id, document_classification_id,
                    document_classification_sha256, planner_contract_version,
                    task_contract_version, task_payload, task_input_sha256,
                    candidate_span_set_sha256, status
                ) SELECT CAST(:task_id AS uuid), tenant_id, deal_id,
                         CAST(:run_id AS uuid), CAST(:selection_id AS uuid),
                         :coverage_record_id, methodology_version_record_id,
                         methodology_version_id, methodology_question_record_id,
                         methodology_question_id, methodology_type,
                         extraction_task_id || '-second-run', document_id,
                         document_classification_id,
                         document_classification_sha256, planner_contract_version,
                         task_contract_version, task_payload, task_input_sha256,
                         candidate_span_set_sha256, status
                    FROM methodology_extraction_tasks
                   WHERE tenant_id = CAST(:tenant_id AS uuid)
                     AND deal_id = CAST(:deal_id AS uuid)
                     AND run_id = CAST(:source_run_id AS uuid)
                     AND task_record_id = CAST(:source_task_id AS uuid);
                INSERT INTO methodology_extraction_task_spans (
                    task_span_record_id, tenant_id, deal_id, run_id,
                    task_record_id, document_id, span_id, span_ordinal,
                    content_sha256, locator_sha256
                ) VALUES (
                    CAST(:task_span_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:task_id AS uuid), CAST(:document_id AS uuid),
                    CAST(:span_id AS uuid), 1, :source_content_sha256,
                    :locator_sha256
                );
                INSERT INTO methodology_extraction_attempts (
                    attempt_id, tenant_id, deal_id, run_id, run_step_id,
                    task_record_id, task_input_sha256, lease_owner_id,
                    lease_fencing_token, status, provider, backend, transport_kind,
                    configured_model, prompt_id, prompt_version,
                    prompt_content_sha256, prompt_input_schema_sha256,
                    prompt_output_schema_sha256, request_payload_sha256,
                    prepared_call_sha256, billable_payload_sha256,
                    billable_payload_utf8_bytes, real_call, configured_max_tokens,
                    configured_temperature, configured_json_mode, budget_policy_id,
                    budget_component, budget_reservation_id, price_catalog_version,
                    price_catalog_row_sha256, input_usd_per_million_tokens,
                    output_usd_per_million_tokens, reserved_calls,
                    reserved_input_tokens, reserved_output_tokens,
                    reserved_embedding_tokens, reserved_usd, budget_terminal_state
                ) VALUES (
                    CAST(:attempt_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:step_id AS uuid), CAST(:task_id AS uuid), :hash_a,
                    CAST(:lease_owner_id AS uuid), 7, 'STARTED', 'anthropic',
                    'hermetic', 'HERMETIC_TEST', 'fixture-model',
                    'METHODOLOGY_QUESTION_EXTRACTION_V1', '1.0.0',
                    :hash_a, :hash_a, :hash_a, :hash_a, :hash_b, :hash_a, 1,
                    false, 1, 0, true, 'fixture-policy', 'anthropic',
                    CAST(:reservation_id AS uuid), 'fixture-price-v1', :hash_a,
                    0, 0, 0, 0, 0, 0, 0, 'NOT_BILLABLE'
                )
                """
            ),
            {**params, "source_run_id": RUN_ID, "source_task_id": TASK_ID},
        )
        connection.execute(
            text(
                """
                UPDATE methodology_extraction_attempts
                   SET status='SUCCEEDED', observed_model='fixture-model',
                       provider_request_id='hermetic-fixture-second-run',
                       provider_response_sha256=:hash_a, completed_at=now(),
                       dispatched_call_sha256=:hash_b, latency_ms=1,
                       input_tokens=0, output_tokens=0, stop_reason='end_turn',
                       receipt_sha256=:hash_b
                 WHERE attempt_id=CAST(:attempt_id AS uuid);
                INSERT INTO methodology_extraction_results (
                    result_id, tenant_id, deal_id, run_id, task_record_id,
                    attempt_id, task_input_sha256, outcome, result_payload,
                    result_sha256, searched_document_ids, searched_span_ids,
                    searched_scope_sha256, provider_provenance_sha256
                ) VALUES (
                    CAST(:result_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:task_id AS uuid), CAST(:attempt_id AS uuid), :hash_a,
                    'EVIDENCE_FOUND', '{"outcome":"EVIDENCE_FOUND"}'::jsonb,
                    :hash_a, ARRAY[CAST(:document_id AS uuid)],
                    ARRAY[CAST(:span_id AS uuid)], :hash_a, :hash_b
                );
                INSERT INTO methodology_result_factual_outputs (
                    factual_output_record_id, tenant_id, deal_id, run_id,
                    result_id, task_record_id, output_ordinal, factual_output_id,
                    typed_output_payload, typed_output_sha256,
                    accepted_source_span_ids, accepted_locator_sha256s,
                    accepted_locator_set_sha256
                ) SELECT CAST(:factual_record_id AS uuid), tenant_id, deal_id,
                         CAST(:run_id AS uuid), CAST(:result_id AS uuid),
                         CAST(:task_id AS uuid), output_ordinal, factual_output_id,
                         typed_output_payload, typed_output_sha256,
                         accepted_source_span_ids, accepted_locator_sha256s,
                         accepted_locator_set_sha256
                    FROM methodology_result_factual_outputs
                   WHERE tenant_id = CAST(:tenant_id AS uuid)
                     AND deal_id = CAST(:deal_id AS uuid)
                     AND run_id = CAST(:source_run_id AS uuid)
                     AND factual_output_record_id = CAST(:source_factual_id AS uuid)
                """
            ),
            {
                **params,
                "source_run_id": RUN_ID,
                "source_factual_id": FACTUAL_RECORD_ID,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO methodology_result_claim_links (
                    claim_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, selection_id, coverage_record_id,
                    methodology_version_record_id,
                    methodology_question_record_id, claim_id,
                    canonical_key_sha256, reused_existing_claim
                ) VALUES (
                    CAST(:claim_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_record_id AS uuid),
                    CAST(:selection_id AS uuid), :coverage_record_id,
                    CAST('15151515-1515-4151-8151-151515151515' AS uuid),
                    CAST('17171717-1717-4171-8171-171717171717' AS uuid),
                    CAST(:claim_id AS uuid), :canonical_key_sha256, true
                );
                INSERT INTO methodology_result_evidence_links (
                    evidence_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, claim_link_id, evidence_id,
                    document_id, source_span_id, locator_sha256
                ) VALUES (
                    CAST(:evidence_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_record_id AS uuid),
                    CAST(:claim_link_id AS uuid), CAST(:evidence_id AS uuid),
                    CAST(:document_id AS uuid), CAST(:span_id AS uuid),
                    :locator_sha256
                );
                INSERT INTO methodology_result_sanad_links (
                    sanad_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, claim_link_id, sanad_id,
                    primary_evidence_id
                ) VALUES (
                    CAST(:sanad_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_record_id AS uuid),
                    CAST(:claim_link_id AS uuid), CAST(:sanad_id AS uuid),
                    CAST(:evidence_id AS uuid)
                );
                INSERT INTO methodology_result_defect_links (
                    defect_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, sanad_link_id, defect_id
                ) VALUES (
                    CAST(:defect_link_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:result_id AS uuid), CAST(:factual_record_id AS uuid),
                    CAST(:sanad_link_id AS uuid), CAST(:defect_id AS uuid)
                )
                """
            ),
            params,
        )


def test_database_rejects_same_defect_owned_by_two_outputs_in_one_run() -> None:
    with disposable_postgres() as database:
        _prepare_base_graph(database, include_links=True)
        _insert_second_factual_lineage(database)
        assert _defect_link_count(database) == 1

        with pytest.raises(DBAPIError) as exc_info:
            _insert_defect_link(
                database,
                defect_link_id=SECOND_DEFECT_LINK_ID,
                factual_output_record_id=SECOND_FACTUAL_RECORD_ID,
                sanad_link_id=SECOND_SANAD_LINK_ID,
            )

        assert getattr(exc_info.value.orig, "pgcode", None) == "23505"
        assert (
            getattr(getattr(exc_info.value.orig, "diag", None), "constraint_name", None)
            == "uq_methodology_result_defect_links_defect"
        )
        assert _defect_link_count(database) == 1


def test_same_canonical_defect_once_in_each_fully_scoped_run_is_valid() -> None:
    with disposable_postgres() as database:
        _prepare_base_graph(database, include_links=True)
        _insert_second_run_with_same_canonical_defect(database)

        assert _defect_link_count(database, RUN_ID) == 1
        assert _defect_link_count(database, SECOND_RUN_ID) == 1
        with database.app_engine.begin() as connection:
            set_tenant_context(connection, TENANT_A)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            first = repository.load_canonical_materialization_snapshot(
                deal_id=DEAL_ID, run_id=RUN_ID
            )
            second = repository.load_canonical_materialization_snapshot(
                deal_id=DEAL_ID, run_id=SECOND_RUN_ID
            )
        assert [str(row["defect_id"]) for row in first.defects] == [DEFECT_ID]
        assert [str(row["defect_id"]) for row in second.defects] == [DEFECT_ID]


def test_repository_rejects_batch_defect_reuse_before_any_mutation() -> None:
    with disposable_postgres() as database:
        _prepare_base_graph(database, include_links=False)
        _insert_base_links_without_defect(database)
        _insert_second_factual_lineage(database)

        with database.app_engine.begin() as connection:
            set_tenant_context(connection, TENANT_A)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
                repository.persist_defect_links(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    lease_owner_id=LEASE_OWNER_ID,
                    lease_fencing_token=7,
                    records=(
                        _repository_defect_link(
                            factual_output_record_id=FACTUAL_RECORD_ID,
                            sanad_link_id=VALID_SANAD_LINK_ID,
                        ),
                        _repository_defect_link(
                            factual_output_record_id=SECOND_FACTUAL_RECORD_ID,
                            sanad_link_id=SECOND_SANAD_LINK_ID,
                        ),
                    ),
                )
            assert exc_info.value.code == METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED
            count = connection.execute(
                text(
                    "SELECT count(*) FROM methodology_result_defect_links "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND run_id = CAST(:run_id AS uuid)"
                ),
                {"tenant_id": TENANT_A, "run_id": RUN_ID},
            ).scalar_one()
            assert int(count) == 0


def test_repository_rejects_preexisting_run_defect_before_mutation() -> None:
    with disposable_postgres() as database:
        _prepare_base_graph(database, include_links=False)
        _insert_base_links_without_defect(database)
        _insert_second_factual_lineage(database)
        _insert_defect_link(
            database,
            defect_link_id=FIRST_DEFECT_LINK_ID,
            factual_output_record_id=FACTUAL_RECORD_ID,
            sanad_link_id=VALID_SANAD_LINK_ID,
        )

        with database.app_engine.begin() as connection:
            set_tenant_context(connection, TENANT_A)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
                repository.persist_defect_links(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    lease_owner_id=LEASE_OWNER_ID,
                    lease_fencing_token=7,
                    records=(
                        _repository_defect_link(
                            factual_output_record_id=SECOND_FACTUAL_RECORD_ID,
                            sanad_link_id=SECOND_SANAD_LINK_ID,
                        ),
                    ),
                )
            assert exc_info.value.code == METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED
            count = connection.execute(
                text(
                    "SELECT count(*) FROM methodology_result_defect_links "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND run_id = CAST(:run_id AS uuid)"
                ),
                {"tenant_id": TENANT_A, "run_id": RUN_ID},
            ).scalar_one()
            assert int(count) == 1


def test_restart_rejects_duplicate_defect_ownership_after_constraint_bypass() -> None:
    with disposable_postgres() as database:
        _prepare_base_graph(database, include_links=True)
        _insert_second_factual_lineage(database)
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE methodology_result_defect_links "
                    "DROP CONSTRAINT uq_methodology_result_defect_links_defect"
                )
            )
        _insert_defect_link(
            database,
            defect_link_id=SECOND_DEFECT_LINK_ID,
            factual_output_record_id=SECOND_FACTUAL_RECORD_ID,
            sanad_link_id=SECOND_SANAD_LINK_ID,
        )
        assert _defect_link_count(database) == 2

        rejected_loaders: list[str] = []
        for loader_name in ("result_links", "snapshot"):
            with database.app_engine.begin() as connection:
                set_tenant_context(connection, TENANT_A)
                repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
                try:
                    if loader_name == "result_links":
                        repository.load_canonical_result_links(deal_id=DEAL_ID, run_id=RUN_ID)
                    else:
                        repository.load_canonical_materialization_snapshot(
                            deal_id=DEAL_ID, run_id=RUN_ID
                        )
                except MethodologyExtractionRepositoryError:
                    rejected_loaders.append(loader_name)

        assert rejected_loaders == ["result_links", "snapshot"]
        assert _defect_link_count(database) == 2
