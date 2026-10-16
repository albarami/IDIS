"""Operator-backed disposable-PostgreSQL RED proof for migration 0037."""

from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from idis.models.methodology_extraction import (
    CanonicalMethodologyClaimIdentity,
    MethodologyFactualOutput,
    canonical_json_bytes,
)
from idis.models.run_step import STEP_ORDER, StepName
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    downgrade,
    grant_application_access,
    migrate,
    set_tenant_context,
    tenant_catalog_snapshot,
    tenant_table_inventory,
)
from tests.slice100_task6.test_migration_0037_red import (
    EXPECTED_TENANT_TABLES,
    TASK6_TABLES,
)

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
DEAL_ID = "33333333-3333-4333-8333-333333333333"
RUN_ID = "44444444-4444-4444-8444-444444444444"
SELECTION_ID = "55555555-5555-4555-8555-555555555555"
COVERAGE_ID = "mc_" + "a" * 24
LEASE_OWNER_ID = "66666666-6666-4666-8666-666666666666"
DOCUMENT_ID = "77777777-7777-4777-8777-777777777777"
SPAN_ID = "88888888-8888-4888-8888-888888888888"
SECOND_SPAN_ID = "89898989-8989-4989-8989-898989898989"
STEP_ID = "99999999-9999-4999-8999-999999999999"
TASK_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ATTEMPT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
READY_TASK_ID = "bcbcbcbc-bcbc-4bcb-8bcb-bcbcbcbcbcbc"
READY_RESERVATION_ID = "bdbdbdbd-bdbd-4bdb-8bdb-bdbdbdbdbdbd"
RESULT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
FACTUAL_RECORD_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
EVIDENCE_ID = "12121212-1212-4121-8121-121212121212"
SANAD_ID = "13131313-1313-4131-8131-131313131313"
DEFECT_ID = "14141414-1414-4141-8141-141414141414"
HASH_A = "a" * 64
HASH_B = "b" * 64
POLICY_HASH = "f" * 64
SOURCE_TEXT = "Fictional"
SOURCE_CONTENT_SHA256 = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
SECOND_SOURCE_TEXT = "Second fictional span"
SECOND_SOURCE_CONTENT_SHA256 = hashlib.sha256(SECOND_SOURCE_TEXT.encode("utf-8")).hexdigest()
OTHER_SOURCE_TEXT = "Other"
OTHER_SOURCE_CONTENT_SHA256 = hashlib.sha256(OTHER_SOURCE_TEXT.encode("utf-8")).hexdigest()
LOCATOR_HASH = hashlib.sha256(b'{"page":1}').hexdigest()
SECOND_LOCATOR_HASH = hashlib.sha256(b'{"page":2}').hexdigest()
SECOND_COVERAGE_ID = "mc_" + "b" * 24
SECOND_QUESTION_RECORD_ID = "28282828-2828-4282-8282-282828282828"

CANONICAL_FACTUAL_OUTPUT = MethodologyFactualOutput.model_validate(
    {
        "subject": "Fictional",
        "methodology_question_record_id": "17171717-1717-4171-8171-171717171717",
        "predicate": "Fictional",
        "claim_class": "FACT",
        "value": {"type": "text", "value": "fictional"},
        "period": {"kind": "none"},
        "unit": "text",
        "claim_text": "Fictional claim",
        "accepted_source_span_ids": [SPAN_ID],
        "accepted_locator_sha256s": [LOCATOR_HASH],
    }
)
CANONICAL_CLAIM_IDENTITY = CanonicalMethodologyClaimIdentity.from_factual_output(
    tenant_id=TENANT_A,
    deal_id=DEAL_ID,
    factual_output=CANONICAL_FACTUAL_OUTPUT,
)
FACTUAL_ID = str(CANONICAL_FACTUAL_OUTPUT.factual_output_id)
CLAIM_ID = CANONICAL_CLAIM_IDENTITY.claim_id
CLAIM_VALUE_SHA256 = CANONICAL_CLAIM_IDENTITY.value_sha256
CLAIM_CANONICAL_KEY_SHA256 = CANONICAL_CLAIM_IDENTITY.canonical_key_sha256
CANONICAL_FACTUAL_PAYLOAD = CANONICAL_FACTUAL_OUTPUT.model_dump(mode="json")
CANONICAL_FACTUAL_PAYLOAD_SHA256 = hashlib.sha256(
    canonical_json_bytes(CANONICAL_FACTUAL_PAYLOAD)
).hexdigest()
CANONICAL_LOCATOR_SET_SHA256 = hashlib.sha256(
    canonical_json_bytes(
        sorted(
            zip(
                CANONICAL_FACTUAL_OUTPUT.accepted_source_span_ids,
                CANONICAL_FACTUAL_OUTPUT.accepted_locator_sha256s,
                strict=True,
            )
        )
    )
).hexdigest()


def _set_active_task6_write_context(connection: Connection) -> None:
    """Establish the real repository-proved lease context for direct SQL fixtures."""
    from idis.persistence.repositories.methodology_extraction import (
        PostgresMethodologyExtractionRepository,
    )

    PostgresMethodologyExtractionRepository(connection, TENANT_A)._require_fence(
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=7,
    )


def _assert_task6_policies(database: DisposablePostgres) -> None:
    with database.admin_engine.connect() as connection:
        rows = {
            str(row[0]): tuple(row)
            for row in tenant_catalog_snapshot(connection)
            if str(row[0]) in TASK6_TABLES
        }
    assert set(rows) == set(TASK6_TABLES)
    for table, row in rows.items():
        _name, enabled, forced, policy, permissive, command, roles, using, check = row
        assert enabled is True, table
        assert forced is True, table
        assert policy == f"{table}_tenant_isolation", table
        assert permissive is True, table
        assert command == "*", table
        assert roles == "{0}", table
        assert using == check, table
        normalized = " ".join(str(using).lower().split())
        assert "nullif(" in normalized, table
        assert "is not null" in normalized, table
        assert "tenant_id =" in normalized, table


def _seed_task6_dependencies(database: DisposablePostgres) -> None:
    """Seed only the parent graph; Task-6 rows are inserted through the app role."""
    params = {
        "tenant_a": TENANT_A,
        "tenant_b": TENANT_B,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "selection_id": SELECTION_ID,
        "coverage_id": COVERAGE_ID,
        "lease_owner_id": LEASE_OWNER_ID,
        "document_id": DOCUMENT_ID,
        "span_id": SPAN_ID,
        "step_id": STEP_ID,
        "step_name": StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION.value,
        "step_order": STEP_ORDER[StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION],
        "hash_a": HASH_A,
        "hash_b": HASH_B,
        "source_text": SOURCE_TEXT,
        "source_content_sha256": SOURCE_CONTENT_SHA256,
        "cdd_version": "15151515-1515-4151-8151-151515151515",
        "fdd_version": "16161616-1616-4161-8161-161616161616",
        "question_record": "17171717-1717-4171-8171-171717171717",
        "artifact_id": "18181818-1818-4181-8181-181818181818",
    }
    with database.admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (tenant_id, name) VALUES "
                "(CAST(:tenant_a AS uuid), 'Task6 A'), "
                "(CAST(:tenant_b AS uuid), 'Task6 B')"
            ),
            params,
        )
        connection.execute(
            text(
                """
                INSERT INTO deals (deal_id, tenant_id, name, created_at)
                VALUES (CAST(:deal_id AS uuid), CAST(:tenant_a AS uuid),
                        'Fictional Task6 Deal', now())
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                INSERT INTO document_artifacts (
                    doc_id, tenant_id, deal_id, doc_type, title,
                    source_system, version_id, sha256
                ) VALUES (
                    CAST(:artifact_id AS uuid), CAST(:tenant_a AS uuid),
                    CAST(:deal_id AS uuid), 'DATA_ROOM_FILE', 'Fictional',
                    'fixture', 'v1', :hash_a
                );
                INSERT INTO documents (
                    document_id, tenant_id, deal_id, doc_id, doc_type, parse_status
                ) VALUES (
                    CAST(:document_id AS uuid), CAST(:tenant_a AS uuid),
                    CAST(:deal_id AS uuid), CAST(:artifact_id AS uuid),
                    'PDF', 'PARSED'
                );
                INSERT INTO document_spans (
                    span_id, tenant_id, deal_id, document_id, span_type,
                    locator, text_excerpt, content_hash
                ) VALUES (
                    CAST(:span_id AS uuid), CAST(:tenant_a AS uuid),
                    CAST(:deal_id AS uuid), CAST(:document_id AS uuid),
                    'PAGE_TEXT', jsonb_build_object('page', 1),
                    :source_text, :source_content_sha256
                )
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                INSERT INTO runs (
                    run_id, tenant_id, deal_id, mode, status,
                    lease_owner_id, lease_fencing_token,
                    lease_expires_at, last_heartbeat_at
                ) VALUES (
                    CAST(:run_id AS uuid), CAST(:tenant_a AS uuid),
                    CAST(:deal_id AS uuid), 'FULL', 'RUNNING',
                    CAST(:lease_owner_id AS uuid), 7, now() + interval '1 hour', now()
                );
                INSERT INTO run_steps (
                    step_id, tenant_id, run_id, step_name, step_order, status
                ) VALUES (
                    CAST(:step_id AS uuid), CAST(:tenant_a AS uuid),
                    CAST(:run_id AS uuid), :step_name, :step_order, 'RUNNING'
                )
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                INSERT INTO methodology_versions (
                    tenant_id, methodology_version_record_id,
                    methodology_version_id, methodology_id, methodology_type,
                    version_label, source_type, source_alias, source_sha256,
                    source_byte_count, source_contract_hash, source_trace_digest,
                    registry_hash, version_hash, question_count,
                    source_section_count, header_row, status,
                    imported_by_actor_id, import_audit_event_id,
                    frozen_by_actor_id, freeze_audit_event_id, frozen_at
                ) VALUES
                (CAST(:tenant_a AS uuid), CAST(:cdd_version AS uuid),
                 'commercial_dd:frozen-v1', 'commercial_dd', 'commercial_dd',
                 'v1', 'checked_in_template', 'cdd-safe', :hash_a, 1,
                 :hash_a, :hash_a, :hash_a, :hash_a, 21, 1, NULL, 'FROZEN',
                 'fixture', gen_random_uuid(), 'fixture', gen_random_uuid(), now()),
                (CAST(:tenant_a AS uuid), CAST(:fdd_version AS uuid),
                 'financial_dd:frozen-v1', 'financial_dd', 'financial_dd',
                 'v1', 'operator_workbook', 'fdd-safe', :hash_b, 1,
                 :hash_b, :hash_b, :hash_b, :hash_b, 79, 1, 2, 'FROZEN',
                 'fixture', gen_random_uuid(), 'fixture', gen_random_uuid(), now())
                """
            ),
            params,
        )
        connection.execute(
            text(
                "ALTER TABLE methodology_questions "
                "DISABLE TRIGGER trg_methodology_questions_immutable"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO methodology_questions (
                    tenant_id, methodology_question_record_id,
                    methodology_version_record_id, methodology_version_id,
                    methodology_question_id, methodology_id, methodology_type,
                    question_ordinal, section, source_sheet_or_section,
                    source_row_number, source_anchor_cell,
                    source_sha256, semantic_row_hash, question_payload_hash,
                    question_payload
                ) VALUES (
                    CAST(:tenant_a AS uuid), CAST(:question_record AS uuid),
                    CAST(:cdd_version AS uuid), 'commercial_dd:frozen-v1',
                    'mq_commercial_dd_fixture_0001', 'commercial_dd',
                    'commercial_dd', 1, 'Fictional', 'Fictional', NULL, NULL, :hash_a,
                    :hash_a, :hash_b, '{"question":"fictional"}'::jsonb
                )
                """
            ),
            params,
        )
        connection.execute(
            text(
                "ALTER TABLE methodology_questions "
                "ENABLE TRIGGER trg_methodology_questions_immutable"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO run_methodology_selections (
                    selection_id, tenant_id, deal_id, run_id,
                    commercial_dd_version_record_id,
                    commercial_methodology_version_id, commercial_version_hash,
                    financial_dd_version_record_id,
                    financial_methodology_version_id, financial_version_hash,
                    selection_hash, selected_question_count,
                    created_by_actor_id, selection_audit_event_id
                ) VALUES (
                    CAST(:selection_id AS uuid), CAST(:tenant_a AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:cdd_version AS uuid), 'commercial_dd:frozen-v1', :hash_a,
                    CAST(:fdd_version AS uuid), 'financial_dd:frozen-v1', :hash_b,
                    :hash_a, 100, 'fixture', gen_random_uuid()
                );
                INSERT INTO methodology_coverage_records (
                    coverage_record_id, tenant_id, deal_id, run_id, selection_id,
                    methodology_version_record_id, methodology_version_id,
                    methodology_question_record_id, methodology_question_id,
                    methodology_type, status
                ) VALUES (
                    :coverage_id, CAST(:tenant_a AS uuid), CAST(:deal_id AS uuid),
                    CAST(:run_id AS uuid), CAST(:selection_id AS uuid),
                    CAST(:cdd_version AS uuid), 'commercial_dd:frozen-v1',
                    CAST(:question_record AS uuid), 'mq_commercial_dd_fixture_0001',
                    'commercial_dd', 'not_started'
                )
                """
            ),
            params,
        )


def _insert_task6_graph(
    database: DisposablePostgres,
    *,
    started_attempt_only: bool = False,
    live_attempt: bool = False,
    include_links: bool = True,
    run_step_id: str = STEP_ID,
) -> None:
    params = {
        "tenant": TENANT_A,
        "deal": DEAL_ID,
        "run": RUN_ID,
        "selection": SELECTION_ID,
        "coverage": COVERAGE_ID,
        "document": DOCUMENT_ID,
        "span": SPAN_ID,
        "step": run_step_id,
        "task": TASK_ID,
        "attempt": ATTEMPT_ID,
        "result": RESULT_ID,
        "factual": FACTUAL_RECORD_ID,
        "factual_id": FACTUAL_ID,
        "factual_payload": json.dumps(CANONICAL_FACTUAL_PAYLOAD),
        "factual_payload_sha256": CANONICAL_FACTUAL_PAYLOAD_SHA256,
        "locator_set_sha256": CANONICAL_LOCATOR_SET_SHA256,
        "claim": CLAIM_ID,
        "claim_class": CANONICAL_FACTUAL_OUTPUT.claim_class,
        "claim_text": CANONICAL_FACTUAL_OUTPUT.claim_text,
        "predicate": CANONICAL_FACTUAL_OUTPUT.predicate,
        "claim_value": json.dumps(CANONICAL_FACTUAL_OUTPUT.value.model_dump(mode="json")),
        "normalization_rule_version": CANONICAL_CLAIM_IDENTITY.normalization_rule_version,
        "normalized_predicate": CANONICAL_CLAIM_IDENTITY.normalized_predicate,
        "normalized_subject": CANONICAL_CLAIM_IDENTITY.normalized_subject,
        "normalized_period": CANONICAL_CLAIM_IDENTITY.normalized_period,
        "normalized_unit": CANONICAL_CLAIM_IDENTITY.normalized_unit,
        "value_sha256": CLAIM_VALUE_SHA256,
        "canonical_key_sha256": CLAIM_CANONICAL_KEY_SHA256,
        "evidence": EVIDENCE_ID,
        "sanad": SANAD_ID,
        "defect": DEFECT_ID,
        "lease_owner": LEASE_OWNER_ID,
        "hash_a": HASH_A,
        "hash_b": HASH_B,
        "source_content_sha256": SOURCE_CONTENT_SHA256,
        "locator_hash": LOCATOR_HASH,
        "version_record": "15151515-1515-4151-8151-151515151515",
        "question_record": "17171717-1717-4171-8171-171717171717",
        "task_span": "19191919-1919-4191-8191-191919191919",
        "reservation": "20202020-2020-4202-8202-202020202020",
        "claim_link": "21212121-2121-4212-8212-212121212121",
        "evidence_link": "23232323-2323-4232-8232-232323232323",
        "sanad_link": "24242424-2424-4242-8242-242424242424",
        "defect_link": "25252525-2525-4252-8252-252525252525",
        "backend": "live" if live_attempt else "hermetic",
        "transport_kind": "LIVE" if live_attempt else "HERMETIC_TEST",
        "configured_model": ("claude-sonnet-4-20250514" if live_attempt else "fixture-model"),
        "real_call": live_attempt,
        "configured_max_tokens": 512 if live_attempt else 1,
        "budget_policy_id": POLICY_HASH if live_attempt else "fixture-policy",
        "price_catalog_version": (
            "anthropic-standard-price-catalog-2026-07-20-v1" if live_attempt else "fixture-price-v1"
        ),
        "price_catalog_row_sha256": (
            "d2dcb26249b7be143bce3ba02d85dd501a95782cf7e006ade95790fe1240f321"
            if live_attempt
            else HASH_A
        ),
        "input_price": "3.00000000" if live_attempt else "0.00000000",
        "output_price": "15.00000000" if live_attempt else "0.00000000",
        "reserved_calls": 1 if live_attempt else 0,
        "reserved_input_tokens": 100 if live_attempt else 0,
        "reserved_output_tokens": 512 if live_attempt else 0,
        "reserved_usd": "0.00798000" if live_attempt else "0.00000000",
        "budget_terminal_state": "RESERVED" if live_attempt else "NOT_BILLABLE",
    }
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
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
                ) VALUES (
                    CAST(:task AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:run AS uuid), CAST(:selection AS uuid), :coverage,
                    CAST(:version_record AS uuid), 'commercial_dd:frozen-v1',
                    CAST(:question_record AS uuid), 'mq_commercial_dd_fixture_0001',
                    'commercial_dd', 'et_task6_fixture', CAST(:document AS uuid),
                    'dc_fixture', :hash_a, 'selected-pair-planner-v1',
                    'methodology-extraction-task-v1', '{"task":"fixture"}'::jsonb,
                    :hash_a, :hash_b, 'READY'
                );
                INSERT INTO methodology_extraction_task_spans (
                    task_span_record_id, tenant_id, deal_id, run_id,
                    task_record_id, document_id, span_id, span_ordinal,
                    content_sha256, locator_sha256
                ) VALUES (
                    CAST(:task_span AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), CAST(:run AS uuid), CAST(:task AS uuid),
                    CAST(:document AS uuid), CAST(:span AS uuid), 1,
                    :source_content_sha256, :locator_hash
                );
                INSERT INTO methodology_extraction_attempts (
                    attempt_id, tenant_id, deal_id, run_id, run_step_id,
                    task_record_id, task_input_sha256, lease_owner_id,
                    lease_fencing_token, status, provider, backend, transport_kind,
                    configured_model, prompt_id, prompt_version,
                    prompt_content_sha256, prompt_input_schema_sha256,
                    prompt_output_schema_sha256, request_payload_sha256,
                    prepared_call_sha256,
                    billable_payload_sha256, billable_payload_utf8_bytes,
                    real_call, configured_max_tokens, configured_temperature,
                    configured_json_mode, budget_policy_id, budget_component,
                    budget_reservation_id, price_catalog_version,
                    price_catalog_row_sha256,
                    input_usd_per_million_tokens, output_usd_per_million_tokens,
                    reserved_calls, reserved_input_tokens, reserved_output_tokens,
                    reserved_embedding_tokens, reserved_usd, budget_terminal_state
                ) VALUES (
                    CAST(:attempt AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:run AS uuid), CAST(:step AS uuid), CAST(:task AS uuid),
                    :hash_a, CAST(:lease_owner AS uuid), 7, 'STARTED', 'anthropic',
                    :backend, :transport_kind, :configured_model,
                    'METHODOLOGY_QUESTION_EXTRACTION_V1',
                    '1.0.0', :hash_a, :hash_a, :hash_a, :hash_a, :hash_b, :hash_a, 1,
                    :real_call, :configured_max_tokens, 0, true,
                    :budget_policy_id, 'anthropic', CAST(:reservation AS uuid),
                    :price_catalog_version, :price_catalog_row_sha256,
                    :input_price, :output_price,
                    :reserved_calls, :reserved_input_tokens, :reserved_output_tokens,
                    0, :reserved_usd, :budget_terminal_state
                )
                """
            ),
            params,
        )
        if started_attempt_only:
            return
        connection.execute(
            text(
                """
                UPDATE methodology_extraction_attempts
                SET status='SUCCEEDED', observed_model='fixture-model',
                    provider_request_id='hermetic-fixture',
                    provider_response_sha256=:hash_a, completed_at=now(),
                    dispatched_call_sha256=:hash_b,
                    latency_ms=1, input_tokens=0, output_tokens=0,
                    stop_reason='end_turn', receipt_sha256=:hash_b
                WHERE attempt_id=CAST(:attempt AS uuid);
                INSERT INTO methodology_extraction_results (
                    result_id, tenant_id, deal_id, run_id, task_record_id,
                    attempt_id, task_input_sha256, outcome, result_payload,
                    result_sha256, searched_document_ids, searched_span_ids,
                    searched_scope_sha256, provider_provenance_sha256
                ) VALUES (
                    CAST(:result AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:run AS uuid), CAST(:task AS uuid), CAST(:attempt AS uuid),
                    :hash_a, 'EVIDENCE_FOUND', '{"outcome":"EVIDENCE_FOUND"}'::jsonb,
                    :hash_a, ARRAY[CAST(:document AS uuid)], ARRAY[CAST(:span AS uuid)],
                    :hash_a, :hash_b
                );
                INSERT INTO methodology_result_factual_outputs (
                    factual_output_record_id, tenant_id, deal_id, run_id,
                    result_id, task_record_id, output_ordinal, factual_output_id,
                    typed_output_payload, typed_output_sha256,
                    accepted_source_span_ids, accepted_locator_sha256s,
                    accepted_locator_set_sha256
                ) VALUES (
                    CAST(:factual AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:run AS uuid), CAST(:result AS uuid), CAST(:task AS uuid), 1,
                    CAST(:factual_id AS uuid), CAST(:factual_payload AS jsonb),
                    :factual_payload_sha256, ARRAY[CAST(:span AS uuid)],
                    ARRAY[:locator_hash], :locator_set_sha256
                )
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                INSERT INTO claims (
                    claim_id, tenant_id, deal_id, claim_class, claim_text,
                    predicate, value, created_at, normalization_rule_version,
                    normalized_predicate, normalized_subject, normalized_period,
                    normalized_unit, value_sha256
                ) VALUES (
                    CAST(:claim AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    :claim_class, :claim_text, :predicate, CAST(:claim_value AS jsonb),
                    now(), :normalization_rule_version, :normalized_predicate,
                    :normalized_subject, :normalized_period, :normalized_unit,
                    :value_sha256
                );
                INSERT INTO evidence_items (
                    evidence_id, tenant_id, deal_id, claim_id, source_span_id,
                    source_grade, source_system, upstream_origin_id,
                    retrieval_timestamp, verification_status, rationale, updated_at
                ) VALUES (
                    CAST(:evidence AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:claim AS uuid), CAST(:span AS uuid), 'B', 'fixture',
                    'fixture-origin', now(), 'UNVERIFIED', '{}'::jsonb, now()
                );
                INSERT INTO sanads (
                    sanad_id, tenant_id, claim_id, deal_id, primary_evidence_id,
                    created_at
                ) VALUES (
                    CAST(:sanad AS uuid), CAST(:tenant AS uuid), CAST(:claim AS uuid),
                    CAST(:deal AS uuid), CAST(:evidence AS uuid), now()
                );
                INSERT INTO defects (
                    defect_id, tenant_id, deal_id, claim_id, defect_type,
                    severity, status, created_at
                ) VALUES (
                    CAST(:defect AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:claim AS uuid), 'FIXTURE', 'MINOR', 'OPEN', now()
                )
                """
            ),
            params,
        )
        if not include_links:
            return
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
                    CAST(:claim_link AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), CAST(:run AS uuid), CAST(:result AS uuid),
                    CAST(:factual AS uuid), CAST(:selection AS uuid), :coverage,
                    CAST(:version_record AS uuid), CAST(:question_record AS uuid),
                    CAST(:claim AS uuid), :canonical_key_sha256, false
                );
                INSERT INTO methodology_result_evidence_links (
                    evidence_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, claim_link_id, evidence_id,
                    document_id, source_span_id, locator_sha256
                ) VALUES (
                    CAST(:evidence_link AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), CAST(:run AS uuid), CAST(:result AS uuid),
                    CAST(:factual AS uuid), CAST(:claim_link AS uuid),
                    CAST(:evidence AS uuid), CAST(:document AS uuid),
                    CAST(:span AS uuid), :locator_hash
                );
                INSERT INTO methodology_result_sanad_links (
                    sanad_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, claim_link_id, sanad_id,
                    primary_evidence_id
                ) VALUES (
                    CAST(:sanad_link AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), CAST(:run AS uuid), CAST(:result AS uuid),
                    CAST(:factual AS uuid), CAST(:claim_link AS uuid),
                    CAST(:sanad AS uuid), CAST(:evidence AS uuid)
                );
                INSERT INTO methodology_result_defect_links (
                    defect_link_id, tenant_id, deal_id, run_id, result_id,
                    factual_output_record_id, sanad_link_id, defect_id
                ) VALUES (
                    CAST(:defect_link AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), CAST(:run AS uuid), CAST(:result AS uuid),
                    CAST(:factual AS uuid), CAST(:sanad_link AS uuid),
                    CAST(:defect AS uuid)
                )
                """
            ),
            params,
        )


def _insert_second_coverage_record(database: DisposablePostgres) -> None:
    with database.admin_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE methodology_questions "
                "DISABLE TRIGGER trg_methodology_questions_immutable"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO methodology_questions (
                    tenant_id, methodology_question_record_id,
                    methodology_version_record_id, methodology_version_id,
                    methodology_question_id, methodology_id, methodology_type,
                    question_ordinal, section, source_sheet_or_section,
                    source_row_number, source_anchor_cell,
                    source_sha256, semantic_row_hash, question_payload_hash,
                    question_payload
                ) VALUES (
                    CAST(:tenant_id AS uuid), CAST(:question_record_id AS uuid),
                    CAST(:version_record_id AS uuid), 'financial_dd:frozen-v1',
                    'mq_financial_dd_fixture_0001', 'financial_dd',
                    'financial_dd', 1, 'Fictional', 'Assets', 4, 'E4', :hash_b,
                    :hash_b, :hash_a, '{"question":"fictional fdd"}'::jsonb
                )
                """
            ),
            {
                "tenant_id": TENANT_A,
                "question_record_id": SECOND_QUESTION_RECORD_ID,
                "version_record_id": "16161616-1616-4161-8161-161616161616",
                "hash_a": HASH_A,
                "hash_b": HASH_B,
            },
        )
        connection.execute(
            text(
                "ALTER TABLE methodology_questions "
                "ENABLE TRIGGER trg_methodology_questions_immutable"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO methodology_coverage_records (
                    coverage_record_id, tenant_id, deal_id, run_id, selection_id,
                    methodology_version_record_id, methodology_version_id,
                    methodology_question_record_id, methodology_question_id,
                    methodology_type, status
                ) VALUES (
                    :coverage_record_id, CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:selection_id AS uuid), CAST(:version_record_id AS uuid),
                    'financial_dd:frozen-v1', CAST(:question_record_id AS uuid),
                    'mq_financial_dd_fixture_0001', 'financial_dd', 'not_started'
                )
                """
            ),
            {
                "coverage_record_id": SECOND_COVERAGE_ID,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "selection_id": SELECTION_ID,
                "version_record_id": "16161616-1616-4161-8161-161616161616",
                "question_record_id": SECOND_QUESTION_RECORD_ID,
            },
        )


def _insert_second_admitted_span(
    database: DisposablePostgres,
    *,
    locator_page: int = 2,
    locator_sha256: str = SECOND_LOCATOR_HASH,
) -> None:
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
        connection.execute(
            text(
                """
                INSERT INTO document_spans (
                    span_id, tenant_id, deal_id, document_id, span_type,
                    locator, text_excerpt, content_hash
                ) VALUES (
                    CAST(:span_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:document_id AS uuid),
                    'PAGE_TEXT', jsonb_build_object('page', :locator_page),
                    :source_text, :content_sha256
                );
                INSERT INTO methodology_extraction_task_spans (
                    task_span_record_id, tenant_id, deal_id, run_id,
                    task_record_id, document_id, span_id, span_ordinal,
                    content_sha256, locator_sha256
                ) VALUES (
                    gen_random_uuid(), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:task_record_id AS uuid), CAST(:document_id AS uuid),
                    CAST(:span_id AS uuid), 2, :content_sha256,
                    :locator_sha256
                )
                """
            ),
            {
                "span_id": SECOND_SPAN_ID,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "document_id": DOCUMENT_ID,
                "run_id": RUN_ID,
                "task_record_id": TASK_ID,
                "source_text": SECOND_SOURCE_TEXT,
                "content_sha256": SECOND_SOURCE_CONTENT_SHA256,
                "locator_page": locator_page,
                "locator_sha256": locator_sha256,
            },
        )


def _insert_ready_task_without_attempt(database: DisposablePostgres) -> None:
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, TENANT_A)
        _set_active_task6_write_context(connection)
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
                ) VALUES (
                    CAST(:task_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:selection_id AS uuid), :coverage_record_id,
                    CAST(:version_record_id AS uuid), 'commercial_dd:frozen-v1',
                    CAST(:question_record_id AS uuid),
                    'mq_commercial_dd_fixture_0001', 'commercial_dd',
                    'et_task6_budget_lineage_fixture', CAST(:document_id AS uuid),
                    'dc_fixture', :hash_a, 'selected-pair-planner-v1',
                    'methodology-extraction-task-v1', '{"task":"budget-lineage"}'::jsonb,
                    :hash_a, :hash_b, 'READY'
                );
                INSERT INTO methodology_extraction_task_spans (
                    task_span_record_id, tenant_id, deal_id, run_id,
                    task_record_id, document_id, span_id, span_ordinal,
                    content_sha256, locator_sha256
                ) VALUES (
                    gen_random_uuid(), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:task_id AS uuid), CAST(:document_id AS uuid),
                    CAST(:span_id AS uuid), 1,
                    :source_content_sha256, :locator_sha256
                )
                """
            ),
            {
                "task_id": READY_TASK_ID,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "selection_id": SELECTION_ID,
                "coverage_record_id": COVERAGE_ID,
                "version_record_id": "15151515-1515-4151-8151-151515151515",
                "question_record_id": "17171717-1717-4171-8171-171717171717",
                "document_id": DOCUMENT_ID,
                "span_id": SPAN_ID,
                "locator_sha256": LOCATOR_HASH,
                "source_content_sha256": SOURCE_CONTENT_SHA256,
                "hash_a": HASH_A,
                "hash_b": HASH_B,
            },
        )


def test_0037_upgrade_downgrade_upgrade_is_exact_on_empty_postgres() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0036")
        with database.admin_engine.connect() as connection:
            before = tenant_catalog_snapshot(connection)

        migrate(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0037"
            assert tenant_table_inventory(connection) == EXPECTED_TENANT_TABLES
        _assert_task6_policies(database)

        downgrade(database.admin_engine, "0036")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0036"
            assert tenant_catalog_snapshot(connection) == before
            remaining = set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public'"
                    )
                ).scalars()
            )
            assert set(TASK6_TABLES).isdisjoint(remaining)

        migrate(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0037"
            assert tenant_table_inventory(connection) == EXPECTED_TENANT_TABLES
        _assert_task6_policies(database)


def test_0037_app_role_remains_nonsuperuser_and_nobypassrls() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            row = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :role"),
                {"role": database.role_name},
            ).one()
        assert row.rolsuper is False
        assert row.rolbypassrls is False


_PRIMARY_KEYS = {
    "methodology_extraction_tasks": "task_record_id",
    "methodology_extraction_task_spans": "task_span_record_id",
    "methodology_extraction_attempts": "attempt_id",
    "methodology_extraction_results": "result_id",
    "methodology_result_factual_outputs": "factual_output_record_id",
    "methodology_result_claim_links": "claim_link_id",
    "methodology_result_evidence_links": "evidence_link_id",
    "methodology_result_sanad_links": "sanad_link_id",
    "methodology_result_defect_links": "defect_link_id",
}


def _table_payloads(database: DisposablePostgres) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    with database.admin_engine.connect() as connection:
        for table in TASK6_TABLES:
            payload = connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalar_one()
            assert isinstance(payload, dict)
            result[table] = payload
    return result


def _clone_insert_sql(table: str) -> str:
    return (
        f"INSERT INTO {table} SELECT * FROM jsonb_populate_record("
        f"NULL::{table}, CAST(:payload AS jsonb))"
    )


def test_0037_all_nine_tables_enforce_same_tenant_and_deny_cross_or_unset_scope() -> None:
    """Exercise actual RLS reads/writes through the unprivileged application role."""
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_task6_graph(database)
        from idis.persistence.repositories.methodology_extraction import (
            PostgresMethodologyExtractionRepository,
        )

        with database.app_engine.begin() as connection:
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            terminal = repository.load_terminal_execution_records(deal_id=DEAL_ID, run_id=RUN_ID)
            factual = repository.load_factual_output_records(deal_id=DEAL_ID, run_id=RUN_ID)
            canonical = repository.load_canonical_materialization_snapshot(
                deal_id=DEAL_ID, run_id=RUN_ID
            )
        assert len(terminal) == len(factual) == 1
        assert terminal[0]["methodology_question_record_id"] == (
            "17171717-1717-4171-8171-171717171717"
        )
        assert terminal[0]["prepared_call_sha256"] == HASH_B
        assert factual[0]["factual_output_record_id"] == FACTUAL_RECORD_ID
        assert tuple(
            len(rows)
            for rows in (
                canonical.claims,
                canonical.evidence_items,
                canonical.sanads,
                canonical.defects,
            )
        ) == (1, 1, 1, 1)
        payloads = _table_payloads(database)

        with database.app_engine.connect() as connection:
            set_tenant_context(connection, TENANT_A)
            for table in TASK6_TABLES:
                assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 1

        for tenant_id in (TENANT_B, None):
            with database.app_engine.connect() as connection:
                if tenant_id is not None:
                    set_tenant_context(connection, tenant_id)
                for table in TASK6_TABLES:
                    assert (
                        connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
                    )
                    primary_key = _PRIMARY_KEYS[table]
                    original_id = payloads[table][primary_key]
                    update = connection.execute(
                        text(
                            f"UPDATE {table} SET {primary_key} = {primary_key} "
                            f"WHERE {primary_key} = CAST(:row_id AS uuid)"
                        ),
                        {"row_id": original_id},
                    )
                    assert update.rowcount == 0
                    deleted = connection.execute(
                        text(f"DELETE FROM {table} WHERE {primary_key} = CAST(:row_id AS uuid)"),
                        {"row_id": original_id},
                    )
                    assert deleted.rowcount == 0

            for table in TASK6_TABLES:
                payload = dict(payloads[table])
                payload[_PRIMARY_KEYS[table]] = str(uuid.uuid4())
                payload["tenant_id"] = TENANT_A if tenant_id == TENANT_B else TENANT_B
                with database.app_engine.begin() as connection:
                    if tenant_id is not None:
                        set_tenant_context(connection, tenant_id)
                    with pytest.raises(DBAPIError):
                        connection.execute(
                            text(_clone_insert_sql(table)),
                            {"payload": json.dumps(payload, sort_keys=True, default=str)},
                        )

        with database.admin_engine.connect() as connection:
            for table in TASK6_TABLES:
                assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 1


def test_0037_scoped_foreign_key_rejects_same_tenant_cross_deal_span() -> None:
    """RLS admission cannot weaken the composite deal/document/span scope."""
    other_deal = "26262626-2626-4262-8262-262626262626"
    other_artifact = "27272727-2727-4272-8272-272727272727"
    other_document = "28282828-2828-4282-8282-282828282828"
    other_span = "29292929-2929-4292-8292-292929292929"
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_task6_graph(database)
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (CAST(:deal AS uuid), CAST(:tenant AS uuid), 'Other', now());
                    INSERT INTO document_artifacts (
                        doc_id, tenant_id, deal_id, doc_type, title,
                        source_system, version_id, sha256
                    ) VALUES (
                        CAST(:artifact AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                        'DATA_ROOM_FILE', 'Other', 'fixture', 'v1', :hash
                    );
                    INSERT INTO documents (
                        document_id, tenant_id, deal_id, doc_id, doc_type, parse_status
                    ) VALUES (
                        CAST(:document AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                        CAST(:artifact AS uuid), 'PDF', 'PARSED'
                    );
                    INSERT INTO document_spans (
                        span_id, tenant_id, deal_id, document_id, span_type,
                        locator, text_excerpt, content_hash
                    ) VALUES (
                        CAST(:span AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                        CAST(:document AS uuid), 'PAGE_TEXT', jsonb_build_object('page', 1),
                        :source_text, :source_content_sha256
                    )
                    """
                ),
                {
                    "tenant": TENANT_A,
                    "deal": other_deal,
                    "artifact": other_artifact,
                    "document": other_document,
                    "span": other_span,
                    "hash": HASH_A,
                    "source_text": OTHER_SOURCE_TEXT,
                    "source_content_sha256": OTHER_SOURCE_CONTENT_SHA256,
                },
            )
        with database.app_engine.begin() as connection:
            set_tenant_context(connection, TENANT_A)
            _set_active_task6_write_context(connection)
            with pytest.raises(DBAPIError) as exc_info:
                connection.execute(
                    text(
                        """
                        INSERT INTO methodology_extraction_task_spans (
                            task_span_record_id, tenant_id, deal_id, run_id,
                            task_record_id, document_id, span_id, span_ordinal,
                            content_sha256, locator_sha256
                        ) VALUES (
                            CAST(:record AS uuid), CAST(:tenant AS uuid),
                            CAST(:deal AS uuid), CAST(:run AS uuid),
                            CAST(:task AS uuid), CAST(:document AS uuid),
                            CAST(:span AS uuid), 2,
                            :source_content_sha256, :hash_b
                        )
                        """
                    ),
                    {
                        "record": str(uuid.uuid4()),
                        "tenant": TENANT_A,
                        "deal": DEAL_ID,
                        "run": RUN_ID,
                        "task": TASK_ID,
                        "document": other_document,
                        "span": other_span,
                        "source_content_sha256": OTHER_SOURCE_CONTENT_SHA256,
                        "hash_b": HASH_B,
                    },
                )
        assert getattr(exc_info.value.orig, "pgcode", None) == "23503"


def test_task6_fence_expires_against_wall_clock_inside_a_long_transaction() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        WORKER_LEASE_FENCED,
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_task6_graph(database, started_attempt_only=True)

        with database.app_engine.connect() as connection:
            transaction = connection.begin()
            try:
                set_tenant_context(connection, TENANT_A)
                transaction_started_at = connection.execute(text("SELECT now()")).scalar_one()
                with database.admin_engine.begin() as admin:
                    admin.execute(
                        text(
                            """
                            UPDATE runs
                            SET lease_expires_at =
                                CAST(:transaction_started_at AS timestamptz)
                                + interval '200 milliseconds'
                            WHERE tenant_id = CAST(:tenant_id AS uuid)
                              AND run_id = CAST(:run_id AS uuid)
                            """
                        ),
                        {
                            "transaction_started_at": transaction_started_at,
                            "tenant_id": TENANT_A,
                            "run_id": RUN_ID,
                        },
                    )
                time.sleep(0.35)

                repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
                with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
                    repository._require_fence(
                        deal_id=DEAL_ID,
                        run_id=RUN_ID,
                        lease_owner_id=LEASE_OWNER_ID,
                        lease_fencing_token=7,
                    )
                assert exc_info.value.code == WORKER_LEASE_FENCED

                with pytest.raises(DBAPIError) as trigger_exc:
                    connection.execute(
                        text(
                            """
                            UPDATE methodology_extraction_attempts
                            SET status = 'OUTCOME_UNKNOWN', completed_at = now(),
                                safe_failure_code =
                                    'METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN'
                            WHERE tenant_id = CAST(:tenant_id AS uuid)
                              AND attempt_id = CAST(:attempt_id AS uuid)
                            """
                        ),
                        {"tenant_id": TENANT_A, "attempt_id": ATTEMPT_ID},
                    )
                assert "WORKER_LEASE_FENCED" in str(trigger_exc.value)
            finally:
                if transaction.is_active:
                    transaction.rollback()


def test_claim_link_rejects_coherent_lineage_tuple_from_another_coverage_row() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_second_coverage_record(database)
        _insert_task6_graph(database, include_links=False)

        with database.app_engine.begin() as connection:
            set_tenant_context(connection, TENANT_A)
            _set_active_task6_write_context(connection)
            with pytest.raises(DBAPIError) as exc_info:
                connection.execute(
                    text(
                        """
                        INSERT INTO methodology_result_claim_links (
                            claim_link_id, tenant_id, deal_id, run_id, result_id,
                            factual_output_record_id, selection_id,
                            coverage_record_id, methodology_version_record_id,
                            methodology_question_record_id, claim_id,
                            canonical_key_sha256, reused_existing_claim
                        ) VALUES (
                            gen_random_uuid(), CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                            CAST(:result_id AS uuid),
                            CAST(:factual_output_record_id AS uuid),
                            CAST(:selection_id AS uuid), :coverage_record_id,
                            CAST(:version_record_id AS uuid),
                            CAST(:question_record_id AS uuid),
                            CAST(:claim_id AS uuid),
                            :canonical_key_sha256, false
                        )
                        """
                    ),
                    {
                        "tenant_id": TENANT_A,
                        "deal_id": DEAL_ID,
                        "run_id": RUN_ID,
                        "result_id": RESULT_ID,
                        "factual_output_record_id": FACTUAL_RECORD_ID,
                        "selection_id": SELECTION_ID,
                        "coverage_record_id": SECOND_COVERAGE_ID,
                        "version_record_id": "16161616-1616-4161-8161-161616161616",
                        "question_record_id": SECOND_QUESTION_RECORD_ID,
                        "claim_id": CLAIM_ID,
                        "canonical_key_sha256": CLAIM_CANONICAL_KEY_SHA256,
                    },
                )
        assert getattr(exc_info.value.orig, "pgcode", None) == "23514"
        assert "METHODOLOGY_CANONICAL_LINK_LINEAGE_REJECTED" in str(exc_info.value)


def test_claim_link_composite_fk_rejects_mismatched_bound_lineage() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_second_coverage_record(database)
        _insert_task6_graph(database, include_links=False)

        with database.admin_engine.connect() as connection:
            columns = {
                str(row[0])
                for row in connection.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'methodology_result_claim_links'
                        """
                    )
                )
            }
        assert {
            "selection_id",
            "methodology_version_record_id",
            "methodology_question_record_id",
        } <= columns

        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE methodology_result_claim_links DISABLE TRIGGER "
                    "trg_methodology_result_claim_links_lineage"
                )
            )
        try:
            with database.app_engine.begin() as connection:
                set_tenant_context(connection, TENANT_A)
                _set_active_task6_write_context(connection)
                with pytest.raises(DBAPIError) as exc_info:
                    connection.execute(
                        text(
                            """
                        INSERT INTO methodology_result_claim_links (
                            claim_link_id, tenant_id, deal_id, run_id, result_id,
                            factual_output_record_id, selection_id,
                            coverage_record_id, methodology_version_record_id,
                            methodology_question_record_id, claim_id,
                            canonical_key_sha256, reused_existing_claim
                        ) VALUES (
                            gen_random_uuid(), CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                            CAST(:result_id AS uuid),
                            CAST(:factual_output_record_id AS uuid),
                            CAST(:selection_id AS uuid), :coverage_record_id,
                            CAST(:cdd_version_record_id AS uuid),
                            CAST(:cdd_question_record_id AS uuid),
                            CAST(:claim_id AS uuid), :canonical_key_sha256, false
                        )
                            """
                        ),
                        {
                            "tenant_id": TENANT_A,
                            "deal_id": DEAL_ID,
                            "run_id": RUN_ID,
                            "result_id": RESULT_ID,
                            "factual_output_record_id": FACTUAL_RECORD_ID,
                            "selection_id": SELECTION_ID,
                            "coverage_record_id": SECOND_COVERAGE_ID,
                            "cdd_version_record_id": ("15151515-1515-4151-8151-151515151515"),
                            "cdd_question_record_id": ("17171717-1717-4171-8171-171717171717"),
                            "claim_id": CLAIM_ID,
                            "canonical_key_sha256": CLAIM_CANONICAL_KEY_SHA256,
                        },
                    )
        finally:
            with database.admin_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE methodology_result_claim_links ENABLE TRIGGER "
                        "trg_methodology_result_claim_links_lineage"
                    )
                )
        assert getattr(exc_info.value.orig, "pgcode", None) == "23503"


def test_live_attempt_admission_binds_verified_policy_and_exact_catalog_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime
    from decimal import Decimal
    from types import MappingProxyType

    from idis.models.methodology_extraction import (
        MethodologyCandidateSpan,
        MethodologyFactualOutput,
        ValidatedDispatchedMethodologyResult,
        compute_methodology_provider_receipt_sha256,
        compute_methodology_searched_scope_sha256,
    )
    from idis.models.methodology_pricing import resolve_anthropic_standard_price
    from idis.persistence.repositories import methodology_extraction as repository_module
    from idis.persistence.repositories.methodology_extraction import (
        METHODOLOGY_BUDGET_LINEAGE_REJECTED,
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )
    from idis.persistence.repositories.provider_budget_policies import (
        VerifiedActiveProviderBudgetPolicy,
    )

    component = MappingProxyType(
        {
            "max_calls": 10,
            "max_usd": "1.00000000",
            "max_input_tokens": 10_000,
            "max_output_tokens": 10_000,
            "max_embedding_tokens": 0,
        }
    )
    verified_policy = VerifiedActiveProviderBudgetPolicy(
        tenant_id=TENANT_A,
        policy_version="task6-budget-policy-v1",
        policy_hash=POLICY_HASH,
        signer_id="task6-fixture-signer",
        signature_verified=True,
        currency="USD",
        total_max_usd=Decimal("2.00000000"),
        components=MappingProxyType({"anthropic": component}),
        imported_by_actor_id="task6-fixture-importer",
        envelope_hash=HASH_A,
        payload_hash=POLICY_HASH,
        public_key_hash=HASH_B,
        import_audit_event_id="26262626-2626-4262-8262-262626262626",
        imported_at=datetime.now(UTC),
    )
    catalog_row = resolve_anthropic_standard_price(model_id="claude-sonnet-4-20250514")

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_ready_task_without_attempt(database)

        def admit(
            *,
            policy: VerifiedActiveProviderBudgetPolicy,
            input_price: str = "3.00000000",
            price_catalog_row_sha256: str = catalog_row.row_sha256,
        ) -> dict[str, object]:
            with database.app_engine.begin() as connection:
                repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
                return repository.admit_attempt(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    run_step_id=STEP_ID,
                    task_record_id=READY_TASK_ID,
                    task_input_sha256=HASH_A,
                    prepared_call_sha256=HASH_B,
                    lease_owner_id=LEASE_OWNER_ID,
                    lease_fencing_token=7,
                    provider="anthropic",
                    configured_model="claude-sonnet-4-20250514",
                    prompt_id="METHODOLOGY_QUESTION_EXTRACTION_V1",
                    prompt_content_sha256=HASH_A,
                    prompt_input_schema_sha256=HASH_A,
                    prompt_output_schema_sha256=HASH_A,
                    request_payload_sha256=HASH_A,
                    billable_payload_sha256=HASH_A,
                    billable_payload_utf8_bytes=100,
                    configured_max_tokens=512,
                    configured_temperature="0",
                    configured_json_mode=True,
                    budget_policy_id=POLICY_HASH,
                    verified_budget_policy=policy,
                    budget_component="anthropic",
                    budget_reservation_id=READY_RESERVATION_ID,
                    price_catalog_version="anthropic-standard-price-catalog-2026-07-20-v1",
                    price_catalog_row_sha256=price_catalog_row_sha256,
                    input_usd_per_million_tokens=input_price,
                    output_usd_per_million_tokens="15.00000000",
                    reserved_calls=1,
                    reserved_input_tokens=100,
                    reserved_output_tokens=512,
                    reserved_usd="0.00798000",
                )

        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            admit(policy=replace(verified_policy, policy_hash=HASH_B))
        assert exc_info.value.code == METHODOLOGY_BUDGET_LINEAGE_REJECTED

        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            admit(policy=verified_policy, input_price="4.00000000")
        assert exc_info.value.code == METHODOLOGY_BUDGET_LINEAGE_REJECTED

        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            admit(policy=verified_policy, price_catalog_row_sha256=HASH_A)
        assert exc_info.value.code == METHODOLOGY_BUDGET_LINEAGE_REJECTED

        admitted = admit(policy=verified_policy)
        assert admitted["budget_policy_id"] == POLICY_HASH
        assert admitted["price_catalog_version"] == (
            "anthropic-standard-price-catalog-2026-07-20-v1"
        )
        assert admitted["price_catalog_row_sha256"] == catalog_row.row_sha256
        assert str(admitted["input_usd_per_million_tokens"]) == "3.00000000"
        assert str(admitted["reserved_usd"]) == "0.00798000"

        factual_output = MethodologyFactualOutput.model_validate(
            {
                "subject": "Fictional Company",
                "methodology_question_record_id": "17171717-1717-4171-8171-171717171717",
                "predicate": "fictional_revenue",
                "claim_class": "FINANCIAL_METRIC",
                "value": {"type": "monetary", "amount": "12.00", "currency": "USD"},
                "period": {"kind": "as_of", "date": "2026-06-30"},
                "unit": "USD",
                "claim_text": "Fictional revenue was USD 12.",
                "accepted_source_span_ids": [SPAN_ID],
                "accepted_locator_sha256s": [LOCATOR_HASH],
            }
        )
        validated = ValidatedDispatchedMethodologyResult.from_terminal_payload(
            dispatched_call_sha256=HASH_B,
            task_input_sha256=HASH_A,
            outcome="EVIDENCE_FOUND",
            searched_document_ids=(DOCUMENT_ID,),
            searched_span_ids=(SPAN_ID,),
            searched_scope_sha256=compute_methodology_searched_scope_sha256(
                (
                    MethodologyCandidateSpan(
                        document_id=DOCUMENT_ID,
                        span_id=SPAN_ID,
                        ordinal=1,
                        content_sha256=SOURCE_CONTENT_SHA256,
                        locator_sha256=LOCATOR_HASH,
                        text=SOURCE_TEXT,
                    ),
                )
            ),
            factual_outputs=(factual_output,),
            gap_finding=None,
        )

        def terminalize(
            *,
            input_tokens: int,
            output_tokens: int,
            observed_model: str = "claude-sonnet-4-20250514",
        ) -> object:
            receipt_fields: dict[str, object] = {
                "provider": "anthropic",
                "backend": "live",
                "configured_model": "claude-sonnet-4-20250514",
                "observed_model": observed_model,
                "prompt_id": "METHODOLOGY_QUESTION_EXTRACTION_V1",
                "prompt_version": "1.0.0",
                "prompt_content_sha256": HASH_A,
                "prompt_input_schema_sha256": HASH_A,
                "prompt_output_schema_sha256": HASH_A,
                "request_payload_sha256": HASH_A,
                "provider_request_id": "live-fixture-request",
                "provider_response_sha256": HASH_A,
                "real_call": True,
                "configured_max_tokens": 512,
                "configured_temperature": "0",
                "configured_json_mode": True,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "stop_reason": "end_turn",
                "budget_policy_id": POLICY_HASH,
                "budget_component": "anthropic",
                "budget_reservation_id": READY_RESERVATION_ID,
                "billable_payload_sha256": HASH_A,
                "billable_payload_utf8_bytes": 100,
                "price_catalog_version": "anthropic-standard-price-catalog-2026-07-20-v1",
                "price_catalog_row_sha256": catalog_row.row_sha256,
                "input_usd_per_million_tokens": "3.00000000",
                "output_usd_per_million_tokens": "15.00000000",
                "reserved_calls": 1,
                "reserved_input_tokens": 100,
                "reserved_output_tokens": 512,
                "reserved_embedding_tokens": 0,
                "reserved_usd": "0.00798000",
                "budget_terminal_state": "CONSUMED",
            }
            prospective_receipt_sha256 = compute_methodology_provider_receipt_sha256(
                receipt_fields=receipt_fields,
                validated_result_sha256=validated.validated_result_sha256,
            )
            with database.app_engine.begin() as connection:
                repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
                return repository.finalize_attempt_success(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    run_step_id=STEP_ID,
                    attempt_id=str(admitted["attempt_id"]),
                    lease_owner_id=LEASE_OWNER_ID,
                    lease_fencing_token=7,
                    observed_model=observed_model,
                    provider_request_id="live-fixture-request",
                    provider_response_sha256=HASH_A,
                    dispatched_call_sha256=HASH_B,
                    validated_result_sha256=validated.validated_result_sha256,
                    prospective_receipt_sha256=prospective_receipt_sha256,
                    budget_policy_id=POLICY_HASH,
                    latency_ms=1,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    stop_reason="end_turn",
                    result=validated,
                )

        drifted_row = replace(catalog_row, row_sha256="9" * 64)
        monkeypatch.setattr(
            repository_module,
            "resolve_anthropic_standard_price",
            lambda **_kwargs: drifted_row,
        )
        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            terminalize(input_tokens=80, output_tokens=10)
        assert exc_info.value.code == METHODOLOGY_BUDGET_LINEAGE_REJECTED
        monkeypatch.setattr(
            repository_module,
            "resolve_anthropic_standard_price",
            resolve_anthropic_standard_price,
        )

        with pytest.raises(MethodologyExtractionRepositoryError):
            terminalize(
                input_tokens=80,
                output_tokens=10,
                observed_model="claude-opus-4-20250514",
            )

        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            terminalize(input_tokens=101, output_tokens=10)
        assert exc_info.value.code == METHODOLOGY_BUDGET_LINEAGE_REJECTED

        terminal_proof = terminalize(input_tokens=80, output_tokens=10)
        assert terminal_proof.budget_policy_id == POLICY_HASH
        assert terminal_proof.price_catalog_row_sha256 == catalog_row.row_sha256
        assert terminal_proof.consumed_calls == 1
        assert terminal_proof.consumed_input_tokens == 80
        assert terminal_proof.consumed_output_tokens == 10
        assert terminal_proof.consumed_usd == "0.00039000"


def test_received_invalid_terminalization_rejects_catalog_row_hash_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime
    from decimal import Decimal
    from types import MappingProxyType

    from idis.models.methodology_pricing import resolve_anthropic_standard_price
    from idis.persistence.repositories import methodology_extraction as repository_module
    from idis.persistence.repositories.methodology_extraction import (
        METHODOLOGY_BUDGET_LINEAGE_REJECTED,
        METHODOLOGY_PROVIDER_PROVENANCE_INVALID,
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )
    from idis.persistence.repositories.provider_budget_policies import (
        VerifiedActiveProviderBudgetPolicy,
    )

    component = MappingProxyType(
        {
            "max_calls": 10,
            "max_usd": "1.00000000",
            "max_input_tokens": 10_000,
            "max_output_tokens": 10_000,
            "max_embedding_tokens": 0,
        }
    )
    verified_policy = VerifiedActiveProviderBudgetPolicy(
        tenant_id=TENANT_A,
        policy_version="task6-budget-policy-v1",
        policy_hash=POLICY_HASH,
        signer_id="task6-fixture-signer",
        signature_verified=True,
        currency="USD",
        total_max_usd=Decimal("2.00000000"),
        components=MappingProxyType({"anthropic": component}),
        imported_by_actor_id="task6-fixture-importer",
        envelope_hash=HASH_A,
        payload_hash=POLICY_HASH,
        public_key_hash=HASH_B,
        import_audit_event_id="26262626-2626-4262-8262-262626262626",
        imported_at=datetime.now(UTC),
    )
    catalog_row = resolve_anthropic_standard_price(model_id="claude-sonnet-4-20250514")

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_ready_task_without_attempt(database)
        with database.app_engine.begin() as connection:
            admitted = PostgresMethodologyExtractionRepository(connection, TENANT_A).admit_attempt(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=STEP_ID,
                task_record_id=READY_TASK_ID,
                task_input_sha256=HASH_A,
                prepared_call_sha256=HASH_B,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
                provider="anthropic",
                configured_model="claude-sonnet-4-20250514",
                prompt_id="METHODOLOGY_QUESTION_EXTRACTION_V1",
                prompt_content_sha256=HASH_A,
                prompt_input_schema_sha256=HASH_A,
                prompt_output_schema_sha256=HASH_A,
                request_payload_sha256=HASH_A,
                billable_payload_sha256=HASH_A,
                billable_payload_utf8_bytes=100,
                configured_max_tokens=512,
                configured_temperature="0",
                configured_json_mode=True,
                budget_policy_id=POLICY_HASH,
                verified_budget_policy=verified_policy,
                budget_component="anthropic",
                budget_reservation_id=READY_RESERVATION_ID,
                price_catalog_version=catalog_row.catalog_version,
                price_catalog_row_sha256=catalog_row.row_sha256,
                input_usd_per_million_tokens=catalog_row.input_usd_per_million_tokens,
                output_usd_per_million_tokens=catalog_row.output_usd_per_million_tokens,
                reserved_calls=1,
                reserved_input_tokens=100,
                reserved_output_tokens=512,
                reserved_usd="0.00798000",
            )

        drifted_row = replace(catalog_row, row_sha256="9" * 64)
        monkeypatch.setattr(
            repository_module,
            "resolve_anthropic_standard_price",
            lambda **_kwargs: drifted_row,
        )
        with (
            pytest.raises(MethodologyExtractionRepositoryError) as exc_info,
            database.app_engine.begin() as connection,
        ):
            PostgresMethodologyExtractionRepository(
                connection, TENANT_A
            ).finalize_received_response_failure(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                attempt_id=str(admitted["attempt_id"]),
                run_step_id=STEP_ID,
                budget_reservation_id=READY_RESERVATION_ID,
                budget_policy_id=POLICY_HASH,
                safe_failure_code=METHODOLOGY_PROVIDER_PROVENANCE_INVALID,
                observed_model=None,
                provider_request_id="live-fixture-request",
                provider_response_sha256=HASH_A,
                dispatched_call_sha256=HASH_B,
                latency_ms=1,
                input_tokens=10,
                output_tokens=5,
                stop_reason="tool_use",
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
            )
        assert exc_info.value.code == METHODOLOGY_BUDGET_LINEAGE_REJECTED

        with database.admin_engine.connect() as connection:
            attempt_state = connection.execute(
                text(
                    "SELECT status FROM methodology_extraction_attempts "
                    "WHERE attempt_id=CAST(:attempt_id AS uuid)"
                ),
                {"attempt_id": admitted["attempt_id"]},
            ).scalar_one()
            result_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM methodology_extraction_results "
                    "WHERE attempt_id=CAST(:attempt_id AS uuid)"
                ),
                {"attempt_id": admitted["attempt_id"]},
            ).scalar_one()
        assert attempt_state == "STARTED"
        assert result_count == 0


@pytest.mark.parametrize(
    ("second_locator_hash", "second_locator_page"),
    ((SECOND_LOCATOR_HASH, 2), (LOCATOR_HASH, 1)),
    ids=("distinct-locators", "shared-locator-pairs"),
)
def test_repository_terminal_transaction_binds_shared_receipt_and_typed_result(
    second_locator_hash: str,
    second_locator_page: int,
) -> None:
    """Exercise the real repository terminal write and restart read on PostgreSQL."""
    from idis.models.methodology_extraction import (
        MethodologyCandidateSpan,
        MethodologyFactualOutput,
        ValidatedDispatchedMethodologyResult,
        compute_methodology_provider_receipt_sha256,
        compute_methodology_searched_scope_sha256,
    )
    from idis.persistence.repositories.methodology_extraction import (
        METHODOLOGY_BUDGET_LINEAGE_REJECTED,
        METHODOLOGY_SEARCH_SCOPE_MISMATCH,
        METHODOLOGY_SOURCE_LOCATOR_REJECTED,
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_task6_graph(database, started_attempt_only=True)
        _insert_second_admitted_span(
            database,
            locator_page=second_locator_page,
            locator_sha256=second_locator_hash,
        )
        candidate_spans = (
            MethodologyCandidateSpan(
                document_id=DOCUMENT_ID,
                span_id=SPAN_ID,
                ordinal=1,
                content_sha256=SOURCE_CONTENT_SHA256,
                locator_sha256=LOCATOR_HASH,
                text=SOURCE_TEXT,
            ),
            MethodologyCandidateSpan(
                document_id=DOCUMENT_ID,
                span_id=SECOND_SPAN_ID,
                ordinal=2,
                content_sha256=SECOND_SOURCE_CONTENT_SHA256,
                locator_sha256=second_locator_hash,
                text=SECOND_SOURCE_TEXT,
            ),
        )
        searched_scope_sha256 = compute_methodology_searched_scope_sha256(candidate_spans)

        def factual_output_with_locators(
            locator_sha256s: tuple[str, str],
        ) -> MethodologyFactualOutput:
            return MethodologyFactualOutput.model_validate(
                {
                    "subject": "Fictional Company",
                    "methodology_question_record_id": "17171717-1717-4171-8171-171717171717",
                    "predicate": "fictional_revenue",
                    "claim_class": "FINANCIAL_METRIC",
                    "value": {"type": "monetary", "amount": "12.00", "currency": "USD"},
                    "period": {"kind": "as_of", "date": "2026-06-30"},
                    "unit": "USD",
                    "claim_text": "Fictional revenue was USD 12.",
                    "accepted_source_span_ids": [SPAN_ID, SECOND_SPAN_ID],
                    "accepted_locator_sha256s": locator_sha256s,
                }
            )

        def validated_result_with_output(
            output: MethodologyFactualOutput,
            *,
            searched_span_ids: tuple[str, ...] = (SPAN_ID, SECOND_SPAN_ID),
            scope_sha256: str = searched_scope_sha256,
        ) -> ValidatedDispatchedMethodologyResult:
            return ValidatedDispatchedMethodologyResult.from_terminal_payload(
                dispatched_call_sha256=HASH_B,
                task_input_sha256=HASH_A,
                outcome="EVIDENCE_FOUND",
                searched_document_ids=(DOCUMENT_ID,),
                searched_span_ids=searched_span_ids,
                searched_scope_sha256=scope_sha256,
                factual_outputs=(output,),
                gap_finding=None,
            )

        swapped_factual_output = factual_output_with_locators((second_locator_hash, LOCATOR_HASH))
        swapped_validated = validated_result_with_output(swapped_factual_output)
        factual_output = factual_output_with_locators((LOCATOR_HASH, second_locator_hash))
        validated = validated_result_with_output(factual_output)
        receipt_fields: dict[str, object] = {
            "provider": "anthropic",
            "backend": "hermetic",
            "configured_model": "fixture-model",
            "observed_model": "fixture-model",
            "prompt_id": "METHODOLOGY_QUESTION_EXTRACTION_V1",
            "prompt_version": "1.0.0",
            "prompt_content_sha256": HASH_A,
            "prompt_input_schema_sha256": HASH_A,
            "prompt_output_schema_sha256": HASH_A,
            "request_payload_sha256": HASH_A,
            "provider_request_id": "hermetic-fixture",
            "provider_response_sha256": HASH_A,
            "real_call": False,
            "configured_max_tokens": 1,
            "configured_temperature": "0",
            "configured_json_mode": True,
            "input_tokens": 0,
            "output_tokens": 0,
            "stop_reason": "end_turn",
            "budget_policy_id": "fixture-policy",
            "budget_component": "anthropic",
            "budget_reservation_id": "20202020-2020-4202-8202-202020202020",
            "billable_payload_sha256": HASH_A,
            "billable_payload_utf8_bytes": 1,
            "price_catalog_version": "fixture-price-v1",
            "price_catalog_row_sha256": HASH_A,
            "input_usd_per_million_tokens": "0.00000000",
            "output_usd_per_million_tokens": "0.00000000",
            "reserved_calls": 0,
            "reserved_input_tokens": 0,
            "reserved_output_tokens": 0,
            "reserved_embedding_tokens": 0,
            "reserved_usd": "0.00000000",
            "budget_terminal_state": "NOT_BILLABLE",
        }

        def finalize(
            terminal_result: ValidatedDispatchedMethodologyResult,
            *,
            budget_policy_id: str = "fixture-policy",
        ) -> object:
            receipt_sha256 = compute_methodology_provider_receipt_sha256(
                receipt_fields=receipt_fields,
                validated_result_sha256=terminal_result.validated_result_sha256,
            )
            with database.app_engine.begin() as connection:
                repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
                return repository.finalize_attempt_success(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    run_step_id=STEP_ID,
                    attempt_id=ATTEMPT_ID,
                    lease_owner_id=LEASE_OWNER_ID,
                    lease_fencing_token=7,
                    observed_model="fixture-model",
                    provider_request_id="hermetic-fixture",
                    provider_response_sha256=HASH_A,
                    dispatched_call_sha256=HASH_B,
                    validated_result_sha256=terminal_result.validated_result_sha256,
                    prospective_receipt_sha256=receipt_sha256,
                    latency_ms=1,
                    input_tokens=0,
                    output_tokens=0,
                    stop_reason="end_turn",
                    result=terminal_result,
                    budget_policy_id=budget_policy_id,
                )

        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            finalize(validated, budget_policy_id="different-policy")
        assert exc_info.value.code == METHODOLOGY_BUDGET_LINEAGE_REJECTED

        partial_scope_result = validated_result_with_output(
            factual_output,
            searched_span_ids=(SPAN_ID,),
            scope_sha256=compute_methodology_searched_scope_sha256(candidate_spans[:1]),
        )
        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            finalize(partial_scope_result)
        assert exc_info.value.code == METHODOLOGY_SEARCH_SCOPE_MISMATCH

        provider_chosen_scope_hash_result = validated_result_with_output(
            factual_output,
            scope_sha256=HASH_A,
        )
        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            finalize(provider_chosen_scope_hash_result)
        assert exc_info.value.code == METHODOLOGY_SEARCH_SCOPE_MISMATCH

        if second_locator_hash != LOCATOR_HASH:
            with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
                finalize(swapped_validated)
            assert exc_info.value.code == METHODOLOGY_SOURCE_LOCATOR_REJECTED
        prospective_receipt_sha256 = compute_methodology_provider_receipt_sha256(
            receipt_fields=receipt_fields,
            validated_result_sha256=validated.validated_result_sha256,
        )
        proof = finalize(validated)
        assert proof.dispatched_call_sha256 == HASH_B
        assert proof.provider_response_sha256 == HASH_A
        assert proof.validated_result_sha256 == validated.validated_result_sha256
        assert proof.prospective_receipt_sha256 == prospective_receipt_sha256
        assert proof.receipt_sha256 == prospective_receipt_sha256
        assert proof.budget_terminal_state == "NOT_BILLABLE"
        with database.app_engine.begin() as connection:
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            terminal = repository.load_terminal_execution_records(deal_id=DEAL_ID, run_id=RUN_ID)
            factual = repository.load_factual_output_records(deal_id=DEAL_ID, run_id=RUN_ID)
        assert len(terminal) == len(factual) == 1
        assert terminal[0]["receipt_sha256"] == prospective_receipt_sha256
        assert terminal[0]["result_sha256"] == validated.validated_result_sha256
        assert factual[0]["factual_output_id"] == factual_output.factual_output_id
        assert tuple(str(item) for item in factual[0]["accepted_source_span_ids"]) == (
            SPAN_ID,
            SECOND_SPAN_ID,
        )
        assert tuple(str(item) for item in factual[0]["accepted_locator_sha256s"]) == (
            LOCATOR_HASH,
            second_locator_hash,
        )


def test_failure_terminalization_rejects_budget_policy_swap() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        METHODOLOGY_BUDGET_LINEAGE_REJECTED,
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_task6_graph(database, started_attempt_only=True)

        with (
            pytest.raises(MethodologyExtractionRepositoryError) as exc_info,
            database.app_engine.begin() as connection,
        ):
            PostgresMethodologyExtractionRepository(connection, TENANT_A).finalize_attempt_failure(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                attempt_id=ATTEMPT_ID,
                run_step_id=STEP_ID,
                budget_reservation_id="20202020-2020-4202-8202-202020202020",
                budget_policy_id="different-policy",
                safe_failure_code="METHODOLOGY_PROVIDER_RESULT_INVALID",
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
            )
        assert exc_info.value.code == METHODOLOGY_BUDGET_LINEAGE_REJECTED

        with database.app_engine.begin() as connection:
            PostgresMethodologyExtractionRepository(connection, TENANT_A).finalize_attempt_failure(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                attempt_id=ATTEMPT_ID,
                run_step_id=STEP_ID,
                budget_reservation_id="20202020-2020-4202-8202-202020202020",
                budget_policy_id="fixture-policy",
                safe_failure_code="METHODOLOGY_PROVIDER_RESULT_INVALID",
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
            )
            states = connection.execute(
                text(
                    """
                    SELECT a.status AS attempt_status, s.status AS step_status
                    FROM methodology_extraction_attempts AS a
                    JOIN run_steps AS s
                      ON s.tenant_id = a.tenant_id AND s.step_id = a.run_step_id
                    WHERE a.tenant_id = CAST(:tenant_id AS uuid)
                      AND a.attempt_id = CAST(:attempt_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "attempt_id": ATTEMPT_ID},
            ).one()
        assert states.attempt_status == "FAILED"
        assert states.step_status == "FAILED"


def test_received_invalid_response_is_failed_consumed_with_durable_provenance() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        METHODOLOGY_BUDGET_LINEAGE_REJECTED,
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_task6_graph(database, started_attempt_only=True, live_attempt=True)

        values = {
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "attempt_id": ATTEMPT_ID,
            "run_step_id": STEP_ID,
            "budget_reservation_id": "20202020-2020-4202-8202-202020202020",
            "budget_policy_id": POLICY_HASH,
            "safe_failure_code": "METHODOLOGY_PROVIDER_RESULT_INVALID",
            "observed_model": "claude-sonnet-4-20250514",
            "provider_request_id": "live-invalid-fixture-request",
            "provider_response_sha256": HASH_A,
            "dispatched_call_sha256": HASH_B,
            "latency_ms": 17,
            "input_tokens": 80,
            "output_tokens": 10,
            "stop_reason": "end_turn",
            "lease_owner_id": LEASE_OWNER_ID,
            "lease_fencing_token": 7,
        }
        with (
            pytest.raises(MethodologyExtractionRepositoryError),
            database.app_engine.begin() as connection,
        ):
            PostgresMethodologyExtractionRepository(
                connection, TENANT_A
            ).finalize_received_response_failure(
                **{**values, "observed_model": "claude-opus-4-20250514"}
            )

        with (
            pytest.raises(MethodologyExtractionRepositoryError) as exc_info,
            database.app_engine.begin() as connection,
        ):
            PostgresMethodologyExtractionRepository(
                connection, TENANT_A
            ).finalize_received_response_failure(
                **{**values, "budget_reservation_id": READY_RESERVATION_ID}
            )
        assert exc_info.value.code == METHODOLOGY_BUDGET_LINEAGE_REJECTED

        with database.app_engine.begin() as connection:
            PostgresMethodologyExtractionRepository(
                connection, TENANT_A
            ).finalize_received_response_failure(**values)
            row = connection.execute(
                text(
                    """
                    SELECT a.status AS attempt_status,
                           a.budget_terminal_state,
                           a.observed_model, a.provider_request_id,
                           a.provider_response_sha256, a.dispatched_call_sha256,
                           a.latency_ms, a.input_tokens, a.output_tokens,
                           a.stop_reason, a.safe_failure_code,
                           s.status AS step_status,
                           (SELECT COUNT(*)
                            FROM methodology_extraction_results r
                            WHERE r.tenant_id = a.tenant_id
                              AND r.attempt_id = a.attempt_id) AS result_count
                    FROM methodology_extraction_attempts AS a
                    JOIN run_steps AS s
                      ON s.tenant_id = a.tenant_id AND s.step_id = a.run_step_id
                    WHERE a.tenant_id = CAST(:tenant_id AS uuid)
                      AND a.attempt_id = CAST(:attempt_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "attempt_id": ATTEMPT_ID},
            ).one()
        assert row.attempt_status == "FAILED"
        assert row.budget_terminal_state == "CONSUMED"
        assert row.observed_model == "claude-sonnet-4-20250514"
        assert row.provider_request_id == "live-invalid-fixture-request"
        assert row.provider_response_sha256 == HASH_A
        assert row.dispatched_call_sha256 == HASH_B
        assert row.latency_ms == 17
        assert row.input_tokens == 80
        assert row.output_tokens == 10
        assert row.stop_reason == "end_turn"
        assert row.safe_failure_code == "METHODOLOGY_PROVIDER_RESULT_INVALID"
        assert row.step_status == "FAILED"
        assert row.result_count == 0


def test_canonical_claim_convergence_rejects_arbitrary_uuid_winner() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_task6_graph(database)

        with (
            pytest.raises(MethodologyExtractionRepositoryError),
            database.app_engine.begin() as connection,
        ):
            PostgresMethodologyExtractionRepository(connection, TENANT_A).upsert_canonical_claim(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
                claim={
                    "claim_id": "27272727-2727-5272-8272-272727272727",
                    "claim_class": "FACT",
                    "claim_text": "Fictional claim",
                    "predicate": "fictional",
                    "value": CANONICAL_FACTUAL_OUTPUT.value.model_dump(mode="json"),
                    "normalization_rule_version": (
                        CANONICAL_CLAIM_IDENTITY.normalization_rule_version
                    ),
                    "normalized_predicate": CANONICAL_CLAIM_IDENTITY.normalized_predicate,
                    "normalized_subject": CANONICAL_CLAIM_IDENTITY.normalized_subject,
                    "normalized_period": CANONICAL_CLAIM_IDENTITY.normalized_period,
                    "normalized_unit": CANONICAL_CLAIM_IDENTITY.normalized_unit,
                    "value_sha256": CANONICAL_CLAIM_IDENTITY.value_sha256,
                    "canonical_key_sha256": CANONICAL_CLAIM_IDENTITY.canonical_key_sha256,
                },
            )
