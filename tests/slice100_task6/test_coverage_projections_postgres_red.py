"""Real-PostgreSQL RED contracts for Task-6 coverage completion boundaries."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text

from idis.methodology.models import MethodologyType
from idis.models.methodology_extraction import (
    CanonicalMethodologyClaimIdentity,
    MethodologyFactualOutput,
    canonical_json_bytes,
)
from idis.models.run_step import STEP_ORDER, StepName
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.methodologies import (
    PostgresMethodologiesRepository,
    methodology_source_trace_digest,
)
from idis.persistence.repositories.methodology_extraction import (
    METHODOLOGY_TASK_COVERAGE_INCOMPLETE,
    WORKER_LEASE_FENCED,
    MethodologyExtractionRepositoryError,
    PostgresMethodologyExtractionRepository,
)
from idis.persistence.repositories.runs import PostgresRunsRepository
from idis.services.runs.methodology_coverage_init import (
    DurableRunMethodologyCoverageInitService,
)
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task5.test_durable_selection_coverage_red import _registry

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
LEASE_OWNER_ID = "55555555-5555-4555-8555-555555555555"
FOREIGN_TENANT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-bbbbbbbbbbbb"
RUN_STEP_ID = "66666666-6666-4666-8666-666666666666"
SANAD_STEP_ID = "67676767-6767-4676-8676-676767676767"
DOCUMENT_ID = "77777777-7777-4777-8777-777777777777"
ARTIFACT_ID = "88888888-8888-4888-8888-888888888888"
SPAN_ID = "99999999-9999-4999-8999-999999999999"
HASH_A = "a" * 64
HASH_B = "b" * 64
SOURCE_TEXT = "Fictional source text"
SOURCE_CONTENT_SHA256 = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
SECOND_SOURCE_TEXT = "Second fictional source text"
SECOND_SOURCE_CONTENT_SHA256 = hashlib.sha256(SECOND_SOURCE_TEXT.encode("utf-8")).hexdigest()
EXTRA_SOURCE_TEXT = "Extra fictional source"
EXTRA_SOURCE_CONTENT_SHA256 = hashlib.sha256(EXTRA_SOURCE_TEXT.encode("utf-8")).hexdigest()
_RECORD_NAMESPACE = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@dataclass(frozen=True)
class _SeededRun:
    task_ids: tuple[str, ...]
    selection_id: str
    first: dict[str, Any]


class _BoundTransaction:
    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield


def _seed_selected_coverage(database: DisposablePostgres) -> str:
    cdd = _registry(MethodologyType.COMMERCIAL_DD, 21)
    fdd = _registry(MethodologyType.FINANCIAL_DD, 79)
    with database.admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (tenant_id,name) VALUES "
                "(CAST(:tenant AS uuid),'Coverage projection tenant')"
            ),
            {"tenant": TENANT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO deals (deal_id,tenant_id,name,created_at) VALUES "
                "(CAST(:deal AS uuid),CAST(:tenant AS uuid),'Fictional deal',now())"
            ),
            {"tenant": TENANT_ID, "deal": DEAL_ID},
        )
        connection.execute(
            text(
                "INSERT INTO runs (run_id,tenant_id,deal_id,mode,status,created_at) "
                "VALUES (CAST(:run AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),"
                "'FULL','QUEUED',now())"
            ),
            {"tenant": TENANT_ID, "deal": DEAL_ID, "run": RUN_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO document_artifacts (
                    doc_id, tenant_id, deal_id, doc_type, title,
                    source_system, version_id, sha256
                ) VALUES (
                    CAST(:artifact AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), 'DATA_ROOM_FILE', 'Fictional fixture',
                    'fixture', 'v1', :hash
                );
                INSERT INTO documents (
                    document_id, tenant_id, deal_id, doc_id, doc_type, parse_status
                ) VALUES (
                    CAST(:document AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), CAST(:artifact AS uuid), 'PDF', 'PARSED'
                );
                INSERT INTO document_spans (
                    span_id, tenant_id, deal_id, document_id, span_type,
                    locator, text_excerpt, content_hash
                ) VALUES (
                    CAST(:span AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:document AS uuid), 'PAGE_TEXT',
                    jsonb_build_object('page', 1),
                    :source_text, :source_content_sha256
                )
                """
            ),
            {
                "tenant": TENANT_ID,
                "deal": DEAL_ID,
                "artifact": ARTIFACT_ID,
                "document": DOCUMENT_ID,
                "span": SPAN_ID,
                "hash": HASH_A,
                "source_text": SOURCE_TEXT,
                "source_content_sha256": SOURCE_CONTENT_SHA256,
            },
        )

    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        repository = PostgresMethodologiesRepository(connection, TENANT_ID)
        frozen = []
        for registry in (cdd, fdd):
            imported = repository.import_registry(
                registry=registry,
                source_type=registry.current_version.questions[0].source_trace.source_type,
                source_alias="configured-source",
                source_byte_count=1,
                source_contract_hash=HASH_A,
                source_trace_digest=methodology_source_trace_digest(registry),
                imported_by_actor_id="coverage-projection-test",
                import_audit_event_id=str(uuid.uuid4()),
            )
            frozen.append(
                repository.freeze_version(
                    methodology_version_record_id=imported.methodology_version_record_id,
                    expected_source_sha256=registry.current_version.source_hash,
                    frozen_by_actor_id="coverage-projection-test",
                    freeze_audit_event_id=str(uuid.uuid4()),
                    expected_registry=registry,
                )
            )
        selection = repository.create_run_selection(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            commercial_dd_version_record_id=frozen[0].methodology_version_record_id,
            financial_dd_version_record_id=frozen[1].methodology_version_record_id,
            created_by_actor_id="coverage-projection-test",
            selection_audit_event_id=str(uuid.uuid4()),
        )
        runs = PostgresRunsRepository(connection, TENANT_ID)
        claimed = runs.claim_queued_runs(limit=1, lease_owner_id=LEASE_OWNER_ID, lease_seconds=3600)
        assert len(claimed) == 1
        coverage_service = DurableRunMethodologyCoverageInitService(
            methodology_repository=repository,
            runs_repository=runs,
            unit_of_work=_BoundTransaction(),
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )
        _result, records = coverage_service.initialize_for_run(
            tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID
        )
        assert len(records) == 100
        connection.execute(
            text(
                """
                INSERT INTO run_steps (
                    step_id, tenant_id, run_id, step_name, step_order, status
                ) VALUES (
                    CAST(:step AS uuid), CAST(:tenant AS uuid), CAST(:run AS uuid),
                    :step_name, :step_order, 'RUNNING'
                )
                """
            ),
            {
                "step": RUN_STEP_ID,
                "tenant": TENANT_ID,
                "run": RUN_ID,
                "step_name": StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION.value,
                "step_order": STEP_ORDER[StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION],
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO run_steps (
                    step_id, tenant_id, run_id, step_name, step_order, status
                ) VALUES (
                    CAST(:step AS uuid), CAST(:tenant AS uuid), CAST(:run AS uuid),
                    :step_name, :step_order, 'RUNNING'
                )
                """
            ),
            {
                "step": SANAD_STEP_ID,
                "tenant": TENANT_ID,
                "run": RUN_ID,
                "step_name": StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING.value,
                "step_order": STEP_ORDER[StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING],
            },
        )
    return selection.selection_id


def _record_id(label: str, ordinal: int) -> str:
    return str(uuid.uuid5(_RECORD_NAMESPACE, f"{label}:{ordinal}"))


def _seed_terminal_tasks(
    database: DisposablePostgres,
    selection_id: str,
    *,
    found_count: int = 0,
    mixed_first_question: bool = False,
) -> _SeededRun:
    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        PostgresMethodologyExtractionRepository(connection, TENANT_ID)._require_fence(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )
        coverage_rows = (
            connection.execute(
                text(
                    """
                SELECT coverage_record_id, methodology_version_record_id,
                       methodology_version_id, methodology_question_record_id,
                       methodology_question_id, methodology_type
                FROM methodology_coverage_records
                WHERE tenant_id=CAST(:tenant AS uuid) AND run_id=CAST(:run AS uuid)
                ORDER BY coverage_record_id
                """
                ),
                {"tenant": TENANT_ID, "run": RUN_ID},
            )
            .mappings()
            .all()
        )
        assert len(coverage_rows) == 100
        task_params: list[dict[str, Any]] = []
        for ordinal, coverage in enumerate(coverage_rows, start=1):
            task_record_id = _record_id("task", ordinal)
            found = ordinal <= found_count
            task_params.append(
                {
                    "task": task_record_id,
                    "task_span": _record_id("task-span", ordinal),
                    "attempt": _record_id("attempt", ordinal),
                    "reservation": _record_id("reservation", ordinal),
                    "result": _record_id("result", ordinal),
                    "factual": _record_id("factual", ordinal),
                    "factual_id": _record_id("factual-id", ordinal),
                    "claim": _record_id("claim", ordinal),
                    "evidence": _record_id("evidence", ordinal),
                    "sanad": _record_id("sanad", ordinal),
                    "claim_link": _record_id("claim-link", ordinal),
                    "evidence_link": _record_id("evidence-link", ordinal),
                    "sanad_link": _record_id("sanad-link", ordinal),
                    "tenant": TENANT_ID,
                    "deal": DEAL_ID,
                    "run": RUN_ID,
                    "selection": selection_id,
                    "coverage": str(coverage["coverage_record_id"]),
                    "version_record": str(coverage["methodology_version_record_id"]),
                    "version_id": str(coverage["methodology_version_id"]),
                    "question_record": str(coverage["methodology_question_record_id"]),
                    "question_id": str(coverage["methodology_question_id"]),
                    "methodology_type": str(coverage["methodology_type"]),
                    "extraction_task_id": f"et_coverage_projection_{ordinal:03d}",
                    "document": DOCUMENT_ID,
                    "span": SPAN_ID,
                    "step": RUN_STEP_ID,
                    "lease_owner": LEASE_OWNER_ID,
                    "hash_a": HASH_A,
                    "hash_b": HASH_B,
                    "source_content_sha256": SOURCE_CONTENT_SHA256,
                    "outcome": "EVIDENCE_FOUND" if found else "EVIDENCE_NOT_FOUND",
                    "gap_finding": (None if found else '{"reason_code":"EVIDENCE_NOT_FOUND"}'),
                }
            )
        if mixed_first_question:
            mixed_artifact = _record_id("mixed-artifact", 1)
            mixed_document = _record_id("mixed-document", 1)
            mixed_span = _record_id("mixed-span", 1)
            connection.execute(
                text(
                    """
                    INSERT INTO document_artifacts (
                        doc_id, tenant_id, deal_id, doc_type, title,
                        source_system, version_id, sha256
                    ) VALUES (
                        CAST(:artifact AS uuid), CAST(:tenant AS uuid),
                        CAST(:deal AS uuid), 'DATA_ROOM_FILE',
                        'Second fictional fixture', 'fixture', 'v1', :hash
                    );
                    INSERT INTO documents (
                        document_id, tenant_id, deal_id, doc_id, doc_type, parse_status
                    ) VALUES (
                        CAST(:document AS uuid), CAST(:tenant AS uuid),
                        CAST(:deal AS uuid), CAST(:artifact AS uuid), 'PDF', 'PARSED'
                    );
                    INSERT INTO document_spans (
                        span_id, tenant_id, deal_id, document_id, span_type,
                        locator, text_excerpt, content_hash
                    ) VALUES (
                        CAST(:span AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                        CAST(:document AS uuid), 'PAGE_TEXT',
                        jsonb_build_object('page', 1),
                        :source_text, :source_content_sha256
                    )
                    """
                ),
                {
                    "artifact": mixed_artifact,
                    "tenant": TENANT_ID,
                    "deal": DEAL_ID,
                    "document": mixed_document,
                    "span": mixed_span,
                    "hash": HASH_B,
                    "source_text": SECOND_SOURCE_TEXT,
                    "source_content_sha256": SECOND_SOURCE_CONTENT_SHA256,
                },
            )
            task_params.append(
                {
                    **task_params[0],
                    "task": _record_id("mixed-task", 1),
                    "task_span": _record_id("mixed-task-span", 1),
                    "attempt": _record_id("mixed-attempt", 1),
                    "reservation": _record_id("mixed-reservation", 1),
                    "result": _record_id("mixed-result", 1),
                    "factual": _record_id("mixed-factual", 1),
                    "factual_id": _record_id("mixed-factual-id", 1),
                    "claim": _record_id("mixed-claim", 1),
                    "evidence": _record_id("mixed-evidence", 1),
                    "sanad": _record_id("mixed-sanad", 1),
                    "claim_link": _record_id("mixed-claim-link", 1),
                    "evidence_link": _record_id("mixed-evidence-link", 1),
                    "sanad_link": _record_id("mixed-sanad-link", 1),
                    "extraction_task_id": "et_coverage_projection_mixed_001",
                    "document": mixed_document,
                    "span": mixed_span,
                    "source_content_sha256": SECOND_SOURCE_CONTENT_SHA256,
                    "outcome": "EVIDENCE_FOUND",
                    "gap_finding": None,
                }
            )
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
                    CAST(:version_record AS uuid), :version_id,
                    CAST(:question_record AS uuid), :question_id, :methodology_type,
                    :extraction_task_id, CAST(:document AS uuid), 'dc_fixture',
                    :hash_a, 'selected-pair-planner-v1',
                    'methodology-extraction-task-v1', '{"task":"fixture"}'::jsonb,
                    :hash_a, :hash_b, 'READY'
                )
                """
            ),
            task_params,
        )
        connection.execute(
            text(
                """
                INSERT INTO methodology_extraction_task_spans (
                    task_span_record_id, tenant_id, deal_id, run_id,
                    task_record_id, document_id, span_id, span_ordinal,
                    content_sha256, locator_sha256
                ) VALUES (
                    CAST(:task_span AS uuid), CAST(:tenant AS uuid),
                    CAST(:deal AS uuid), CAST(:run AS uuid), CAST(:task AS uuid),
                    CAST(:document AS uuid), CAST(:span AS uuid), 1,
                    :source_content_sha256, :hash_b
                )
                """
            ),
            task_params,
        )
        connection.execute(
            text(
                """
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
                    price_catalog_row_sha256,
                    input_usd_per_million_tokens, output_usd_per_million_tokens,
                    reserved_calls, reserved_input_tokens, reserved_output_tokens,
                    reserved_embedding_tokens, reserved_usd, budget_terminal_state
                ) VALUES (
                    CAST(:attempt AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:run AS uuid), CAST(:step AS uuid), CAST(:task AS uuid),
                    :hash_a, CAST(:lease_owner AS uuid), 1, 'STARTED', 'anthropic',
                    'live', 'LIVE', 'fixture-model',
                    'METHODOLOGY_QUESTION_EXTRACTION_V1', '1.0.0',
                    :hash_a, :hash_a, :hash_a, :hash_a, :hash_b, :hash_a, 1,
                    true, 512, 0, true, :hash_a, 'anthropic',
                    CAST(:reservation AS uuid), 'fixture-price-v1', :hash_a, 1, 1,
                    1, 100, 512, 0, 1, 'RESERVED'
                )
                """
            ),
            task_params,
        )
        connection.execute(
            text(
                """
                UPDATE methodology_extraction_attempts
                SET status='SUCCEEDED', observed_model='fixture-model',
                    provider_request_id='fixture-request',
                    provider_response_sha256=:hash_a,
                    dispatched_call_sha256=:hash_b, completed_at=statement_timestamp(),
                    latency_ms=1, input_tokens=1, output_tokens=1,
                    stop_reason='end_turn', budget_terminal_state='CONSUMED',
                    receipt_sha256=:hash_a
                WHERE attempt_id=CAST(:attempt AS uuid)
                """
            ),
            task_params,
        )
        connection.execute(
            text(
                """
                INSERT INTO methodology_extraction_results (
                    result_id, tenant_id, deal_id, run_id, task_record_id,
                    attempt_id, task_input_sha256, outcome, result_payload,
                    result_sha256, searched_document_ids, searched_span_ids,
                    searched_scope_sha256, provider_provenance_sha256, gap_finding
                ) VALUES (
                    CAST(:result AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                    CAST(:run AS uuid), CAST(:task AS uuid), CAST(:attempt AS uuid),
                    :hash_a, :outcome,
                    jsonb_build_object('outcome', :outcome), :hash_a,
                    ARRAY[CAST(:document AS uuid)], ARRAY[CAST(:span AS uuid)],
                    :hash_a, :hash_b, CAST(:gap_finding AS jsonb)
                )
                """
            ),
            task_params,
        )
        found_params = [row for row in task_params if row["outcome"] == "EVIDENCE_FOUND"]
        if found_params:
            connection.execute(
                text(
                    """
                    INSERT INTO methodology_result_factual_outputs (
                        factual_output_record_id, tenant_id, deal_id, run_id,
                        result_id, task_record_id, output_ordinal,
                        factual_output_id, typed_output_payload,
                        typed_output_sha256, accepted_source_span_ids,
                        accepted_locator_sha256s, accepted_locator_set_sha256
                    ) VALUES (
                        CAST(:factual AS uuid), CAST(:tenant AS uuid),
                        CAST(:deal AS uuid), CAST(:run AS uuid),
                        CAST(:result AS uuid), CAST(:task AS uuid), 1,
                        CAST(:factual_id AS uuid), '{"value":"fictional"}'::jsonb,
                        :hash_a, ARRAY[CAST(:span AS uuid)], ARRAY[:hash_b], :hash_b
                    )
                    """
                ),
                found_params,
            )
    return _SeededRun(
        task_ids=tuple(str(row["extraction_task_id"]) for row in task_params),
        selection_id=selection_id,
        first=dict(task_params[0]),
    )


def _seed_first_canonical_snapshot(database: DisposablePostgres, seeded: _SeededRun) -> None:
    output = MethodologyFactualOutput.model_validate(
        {
            "subject": "Fictional",
            "methodology_question_record_id": seeded.first["question_record"],
            "predicate": "Fictional",
            "claim_class": "FACT",
            "value": {"type": "text", "value": "fictional"},
            "period": {"kind": "none"},
            "unit": "text",
            "claim_text": "Verified fictional fact.",
            "accepted_source_span_ids": [seeded.first["span"]],
            "accepted_locator_sha256s": [seeded.first["hash_b"]],
        }
    )
    identity = CanonicalMethodologyClaimIdentity.from_factual_output(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        factual_output=output,
    )
    factual_payload = output.model_dump(mode="json")
    params = {
        **seeded.first,
        "factual_id": output.factual_output_id,
        "factual_payload": json.dumps(factual_payload),
        "factual_payload_sha256": hashlib.sha256(canonical_json_bytes(factual_payload)).hexdigest(),
        "locator_set_sha256": hashlib.sha256(
            canonical_json_bytes(
                sorted(
                    zip(
                        output.accepted_source_span_ids,
                        output.accepted_locator_sha256s,
                        strict=True,
                    )
                )
            )
        ).hexdigest(),
        "claim": identity.claim_id,
        "claim_value": json.dumps(output.value.model_dump(mode="json")),
        "normalization_rule_version": identity.normalization_rule_version,
        "normalized_predicate": identity.normalized_predicate,
        "normalized_subject": identity.normalized_subject,
        "normalized_period": identity.normalized_period,
        "normalized_unit": identity.normalized_unit,
        "value_sha256": identity.value_sha256,
        "canonical_key_sha256": identity.canonical_key_sha256,
    }
    seeded.first.update(params)
    with database.admin_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                """
                UPDATE methodology_result_factual_outputs
                SET factual_output_id = CAST(:factual_id AS uuid),
                    typed_output_payload = CAST(:factual_payload AS jsonb),
                    typed_output_sha256 = :factual_payload_sha256,
                    accepted_source_span_ids = ARRAY[CAST(:span AS uuid)],
                    accepted_locator_sha256s = ARRAY[:hash_b],
                    accepted_locator_set_sha256 = :locator_set_sha256
                WHERE tenant_id = CAST(:tenant AS uuid)
                  AND deal_id = CAST(:deal AS uuid)
                  AND run_id = CAST(:run AS uuid)
                  AND factual_output_record_id = CAST(:factual AS uuid)
                """
            ),
            params,
        )
    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        PostgresMethodologyExtractionRepository(connection, TENANT_ID)._require_fence(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
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
                    'FACT', 'Verified fictional fact.', 'fictional',
                    CAST(:claim_value AS jsonb), now(),
                    :normalization_rule_version, :normalized_predicate,
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
                    'fixture-origin', now(), 'VERIFIED', '{}'::jsonb, now()
                );
                INSERT INTO sanads (
                    sanad_id, tenant_id, claim_id, deal_id, primary_evidence_id,
                    computed, created_at
                ) VALUES (
                    CAST(:sanad AS uuid), CAST(:tenant AS uuid), CAST(:claim AS uuid),
                    CAST(:deal AS uuid), CAST(:evidence AS uuid),
                    '{"defect_ids":[]}'::jsonb, now()
                )
                """
            ),
            params,
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
                    CAST(:span AS uuid), :hash_b
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
                )
                """
            ),
            params,
        )


def _cross_wire_first_two_result_attempts(database: DisposablePostgres) -> None:
    """Bypass triggers to model durable corruption across two otherwise valid pairs."""
    with database.admin_engine.begin() as connection:
        rows = (
            connection.execute(
                text(
                    """
                    SELECT result_id, attempt_id, task_record_id, task_input_sha256
                    FROM methodology_extraction_results
                    WHERE tenant_id = CAST(:tenant AS uuid)
                      AND deal_id = CAST(:deal AS uuid)
                      AND run_id = CAST(:run AS uuid)
                    ORDER BY task_record_id
                    LIMIT 2
                    """
                ),
                {"tenant": TENANT_ID, "deal": DEAL_ID, "run": RUN_ID},
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2
        assert rows[0]["attempt_id"] != rows[1]["attempt_id"]
        assert rows[0]["task_record_id"] != rows[1]["task_record_id"]
        temporary_attempt_id = _record_id("cross-wired-temporary-attempt", 1)
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                """
                UPDATE methodology_extraction_results
                SET attempt_id = CAST(:temporary_attempt_id AS uuid)
                WHERE result_id = CAST(:result_id AS uuid)
                """
            ),
            {
                "temporary_attempt_id": temporary_attempt_id,
                "result_id": str(rows[0]["result_id"]),
            },
        )
        connection.execute(
            text(
                """
                UPDATE methodology_extraction_results
                SET attempt_id = CAST(:attempt_id AS uuid)
                WHERE result_id = CAST(:result_id AS uuid)
                """
            ),
            {
                "attempt_id": str(rows[0]["attempt_id"]),
                "result_id": str(rows[1]["result_id"]),
            },
        )
        connection.execute(
            text(
                """
                UPDATE methodology_extraction_results
                SET attempt_id = CAST(:attempt_id AS uuid)
                WHERE result_id = CAST(:result_id AS uuid)
                """
            ),
            {
                "attempt_id": str(rows[1]["attempt_id"]),
                "result_id": str(rows[0]["result_id"]),
            },
        )


def _insert_extra_unattempted_task(connection: Any, seeded: _SeededRun) -> None:
    PostgresMethodologyExtractionRepository(connection, TENANT_ID)._require_fence(
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=1,
    )
    params = {
        **seeded.first,
        "artifact": _record_id("extra-artifact", 1),
        "document": _record_id("extra-document", 1),
        "span": _record_id("extra-span", 1),
        "task": _record_id("extra-task", 1),
        "task_span": _record_id("extra-task-span", 1),
        "extraction_task_id": "et_coverage_projection_extra",
        "source_text": EXTRA_SOURCE_TEXT,
        "source_content_sha256": EXTRA_SOURCE_CONTENT_SHA256,
    }
    connection.execute(
        text(
            """
            INSERT INTO document_artifacts (
                doc_id, tenant_id, deal_id, doc_type, title,
                source_system, version_id, sha256
            ) VALUES (
                CAST(:artifact AS uuid), CAST(:tenant AS uuid),
                CAST(:deal AS uuid), 'DATA_ROOM_FILE', 'Extra fictional fixture',
                'fixture', 'v1', :hash_a
            );
            INSERT INTO documents (
                document_id, tenant_id, deal_id, doc_id, doc_type, parse_status
            ) VALUES (
                CAST(:document AS uuid), CAST(:tenant AS uuid),
                CAST(:deal AS uuid), CAST(:artifact AS uuid), 'PDF', 'PARSED'
            );
            INSERT INTO document_spans (
                span_id, tenant_id, deal_id, document_id, span_type,
                locator, text_excerpt, content_hash
            ) VALUES (
                CAST(:span AS uuid), CAST(:tenant AS uuid), CAST(:deal AS uuid),
                CAST(:document AS uuid), 'PAGE_TEXT',
                jsonb_build_object('page', 1),
                :source_text, :source_content_sha256
            );
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
                CAST(:version_record AS uuid), :version_id,
                CAST(:question_record AS uuid), :question_id, :methodology_type,
                :extraction_task_id, CAST(:document AS uuid), 'dc_extra_fixture',
                :hash_a, 'selected-pair-planner-v1',
                'methodology-extraction-task-v1', '{"task":"extra"}'::jsonb,
                :hash_b, :hash_b, 'READY'
            );
            INSERT INTO methodology_extraction_task_spans (
                task_span_record_id, tenant_id, deal_id, run_id,
                task_record_id, document_id, span_id, span_ordinal,
                content_sha256, locator_sha256
            ) VALUES (
                CAST(:task_span AS uuid), CAST(:tenant AS uuid),
                CAST(:deal AS uuid), CAST(:run AS uuid), CAST(:task AS uuid),
                CAST(:document AS uuid), CAST(:span AS uuid), 1,
                :source_content_sha256, :hash_b
            )
            """
        ),
        params,
    )


def test_step7_exact_set_atomically_projects_all_100_coverage_rows() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        selection_id = _seed_selected_coverage(database)
        seeded = _seed_terminal_tasks(database, selection_id)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            repository.complete_execution_step_exact_set(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=RUN_STEP_ID,
                expected_task_ids=seeded.task_ids,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT coverage_record_id, status, reason_code,
                           evidence_links, answer, conflict_ids, defect_ids,
                           updated_at
                    FROM methodology_coverage_records
                    WHERE tenant_id=CAST(:tenant AS uuid) AND run_id=CAST(:run AS uuid)
                    ORDER BY coverage_record_id
                    """
                    ),
                    {"tenant": TENANT_ID, "run": RUN_ID},
                )
                .mappings()
                .all()
            )

        assert len(rows) == 100
        assert Counter(str(row["status"]) for row in rows) == {
            "evidence_missing": 100,
        }
        assert Counter(str(row["reason_code"]) for row in rows) == {
            "METHODOLOGY_EVIDENCE_NOT_FOUND_ALL_TASKS_V1": 100,
        }
        assert all(row["evidence_links"] == [] and row["answer"] is None for row in rows)
        assert all(row["conflict_ids"] == [] and row["defect_ids"] == [] for row in rows)

        first_updated_at = {str(row["coverage_record_id"]): row["updated_at"] for row in rows}
        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            repository.complete_execution_step_exact_set(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=RUN_STEP_ID,
                expected_task_ids=seeded.task_ids,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )
            repository.require_coverage_projection(deal_id=DEAL_ID, run_id=RUN_ID, stage="step7")
            replay_updated_at = dict(
                connection.execute(
                    text(
                        """
                        SELECT coverage_record_id, updated_at
                        FROM methodology_coverage_records
                        WHERE tenant_id=CAST(:tenant AS uuid)
                          AND run_id=CAST(:run AS uuid)
                        ORDER BY coverage_record_id
                        """
                    ),
                    {"tenant": TENANT_ID, "run": RUN_ID},
                ).all()
            )
        assert replay_updated_at == first_updated_at

        with database.app_engine.connect() as connection:
            transaction = connection.begin()
            set_tenant_local(connection, TENANT_ID)
            _insert_extra_unattempted_task(connection, seeded)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            with pytest.raises(MethodologyExtractionRepositoryError) as extra_task:
                repository.require_coverage_projection(
                    deal_id=DEAL_ID, run_id=RUN_ID, stage="step7"
                )
            transaction.rollback()
        assert extra_task.value.code == METHODOLOGY_TASK_COVERAGE_INCOMPLETE

        tampered_id = str(rows[0]["coverage_record_id"])
        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            connection.execute(
                text(
                    """
                    UPDATE methodology_coverage_records
                    SET reason_code='METHODOLOGY_EVIDENCE_FOUND_ALL_TASKS_V1'
                    WHERE tenant_id=CAST(:tenant AS uuid)
                      AND coverage_record_id=:coverage
                    """
                ),
                {"tenant": TENANT_ID, "coverage": tampered_id},
            )
        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            with pytest.raises(MethodologyExtractionRepositoryError) as raised:
                repository.require_coverage_projection(
                    deal_id=DEAL_ID, run_id=RUN_ID, stage="step7"
                )
        assert raised.value.code == METHODOLOGY_TASK_COVERAGE_INCOMPLETE


def test_step7_exact_set_rejects_trigger_bypassed_cross_wired_results() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        selection_id = _seed_selected_coverage(database)
        seeded = _seed_terminal_tasks(database, selection_id)
        _cross_wire_first_two_result_attempts(database)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
                repository.complete_execution_step_exact_set(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    run_step_id=RUN_STEP_ID,
                    expected_task_ids=seeded.task_ids,
                    lease_owner_id=LEASE_OWNER_ID,
                    lease_fencing_token=1,
                )

        assert exc_info.value.code == METHODOLOGY_TASK_COVERAGE_INCOMPLETE


def test_restart_join_excludes_trigger_bypassed_cross_wired_results() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        selection_id = _seed_selected_coverage(database)
        _seed_terminal_tasks(database, selection_id)
        _cross_wire_first_two_result_attempts(database)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            execution_rows = repository.load_terminal_execution_records(
                deal_id=DEAL_ID, run_id=RUN_ID
            )
            result_rows = repository.load_terminal_results(deal_id=DEAL_ID, run_id=RUN_ID)

        assert len(execution_rows) == 98
        assert len(result_rows) == 98


def test_step7_real_postgres_projects_mixed_multi_task_question() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        selection_id = _seed_selected_coverage(database)
        seeded = _seed_terminal_tasks(
            database,
            selection_id,
            mixed_first_question=True,
        )
        assert len(seeded.task_ids) == 101

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            repository.complete_execution_step_exact_set(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=RUN_STEP_ID,
                expected_task_ids=seeded.task_ids,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            mixed = (
                connection.execute(
                    text(
                        """
                        SELECT status, reason_code, evidence_links, answer,
                               conflict_ids, defect_ids
                        FROM methodology_coverage_records
                        WHERE tenant_id=CAST(:tenant AS uuid)
                          AND run_id=CAST(:run AS uuid)
                          AND coverage_record_id=:coverage
                        """
                    ),
                    {
                        "tenant": TENANT_ID,
                        "run": RUN_ID,
                        "coverage": seeded.first["coverage"],
                    },
                )
                .mappings()
                .one()
            )
            statuses = Counter(
                str(row.status)
                for row in connection.execute(
                    text(
                        """
                        SELECT status
                        FROM methodology_coverage_records
                        WHERE tenant_id=CAST(:tenant AS uuid)
                          AND run_id=CAST(:run AS uuid)
                        """
                    ),
                    {"tenant": TENANT_ID, "run": RUN_ID},
                )
            )

        assert statuses == {"evidence_missing": 99, "extracted": 1}
        assert mixed["status"] == "extracted"
        assert mixed["reason_code"] == "METHODOLOGY_EVIDENCE_FOUND_WITH_TASK_GAPS_V1"
        assert mixed["evidence_links"] == []
        assert mixed["answer"] is None
        assert mixed["conflict_ids"] == []
        assert mixed["defect_ids"] == []


def test_step10_projects_exact_canonical_lineage_and_keeps_gaps_missing() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        selection_id = _seed_selected_coverage(database)
        seeded = _seed_terminal_tasks(database, selection_id, found_count=1)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            repository.complete_execution_step_exact_set(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=RUN_STEP_ID,
                expected_task_ids=seeded.task_ids,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )
        _seed_first_canonical_snapshot(database, seeded)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            repository.finalize_canonical_coverage_exact_set(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=SANAD_STEP_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            found = (
                connection.execute(
                    text(
                        """
                    SELECT status, reason_code, evidence_links, answer,
                           conflict_ids, defect_ids, updated_at
                    FROM methodology_coverage_records
                    WHERE tenant_id=CAST(:tenant AS uuid)
                      AND coverage_record_id=:coverage
                    """
                    ),
                    {"tenant": TENANT_ID, "coverage": seeded.first["coverage"]},
                )
                .mappings()
                .one()
            )
            statuses = Counter(
                connection.execute(
                    text(
                        """
                        SELECT status FROM methodology_coverage_records
                        WHERE tenant_id=CAST(:tenant AS uuid)
                          AND run_id=CAST(:run AS uuid)
                        """
                    ),
                    {"tenant": TENANT_ID, "run": RUN_ID},
                ).scalars()
            )

        assert statuses == {"answered": 1, "evidence_missing": 99}
        assert found["status"] == "answered"
        assert found["reason_code"] == "METHODOLOGY_EVIDENCE_FOUND_ALL_TASKS_V1"
        assert found["evidence_links"] == [
            {
                "claim_id": seeded.first["claim"],
                "evidence_id": seeded.first["evidence"],
                "calc_id": None,
            }
        ]
        assert found["answer"] == {
            "answer_text": "Verified fictional fact.",
            "claim_ids": [seeded.first["claim"]],
            "evidence_ids": [seeded.first["evidence"]],
            "calc_ids": [],
            "requires_calculation": False,
        }
        assert found["conflict_ids"] == [] and found["defect_ids"] == []

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            PostgresMethodologyExtractionRepository(
                connection, TENANT_ID
            ).finalize_canonical_coverage_exact_set(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=SANAD_STEP_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )
            replay_updated_at = connection.execute(
                text(
                    """
                    SELECT updated_at FROM methodology_coverage_records
                    WHERE tenant_id=CAST(:tenant AS uuid)
                      AND coverage_record_id=:coverage
                    """
                ),
                {"tenant": TENANT_ID, "coverage": seeded.first["coverage"]},
            ).scalar_one()
        assert replay_updated_at == found["updated_at"]

        from idis.services.runs import methodology_postgres_materialization_runtime as runtime

        acquisitions = 0

        @contextmanager
        def counted_connection_factory() -> Iterator[Any]:
            nonlocal acquisitions
            acquisitions += 1
            with database.app_engine.begin() as connection:
                yield connection

        boundary = runtime.PostgresCanonicalMethodologyMaterializationBoundary(
            connection_factory=counted_connection_factory,
            tenant_id=TENANT_ID,
            lease_owner_id="rehydration-only",
            lease_fencing_token=1,
            audit_sink=None,
        )
        snapshot = boundary.load_validated_final_snapshot(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
        )
        assert acquisitions == 1
        assert len(snapshot.factual_outputs) == 1
        assert len(snapshot.claims) == 1
        assert len(snapshot.evidence_items) == 1
        assert len(snapshot.sanads) == 1


def test_step7_projection_rolls_back_and_a_stale_fence_mutates_nothing() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        selection_id = _seed_selected_coverage(database)
        seeded = _seed_terminal_tasks(database, selection_id)

        with database.app_engine.connect() as connection:
            transaction = connection.begin()
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            repository.complete_execution_step_exact_set(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=RUN_STEP_ID,
                expected_task_ids=seeded.task_ids,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )
            transaction.rollback()

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*) FROM methodology_coverage_records
                    WHERE tenant_id=CAST(:tenant AS uuid)
                      AND run_id=CAST(:run AS uuid) AND status='not_started'
                    """
                    ),
                    {"tenant": TENANT_ID, "run": RUN_ID},
                ).scalar_one()
                == 100
            )
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            with pytest.raises(MethodologyExtractionRepositoryError) as raised:
                repository.complete_execution_step_exact_set(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    run_step_id=RUN_STEP_ID,
                    expected_task_ids=seeded.task_ids,
                    lease_owner_id=LEASE_OWNER_ID,
                    lease_fencing_token=0,
                )
        assert raised.value.code == WORKER_LEASE_FENCED

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, FOREIGN_TENANT_ID)
            foreign = PostgresMethodologyExtractionRepository(connection, FOREIGN_TENANT_ID)
            with pytest.raises(MethodologyExtractionRepositoryError) as cross_tenant:
                foreign.require_coverage_projection(deal_id=DEAL_ID, run_id=RUN_ID, stage="step7")
        assert cross_tenant.value.code == METHODOLOGY_TASK_COVERAGE_INCOMPLETE

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*) FROM methodology_coverage_records
                    WHERE tenant_id=CAST(:tenant AS uuid)
                      AND run_id=CAST(:run AS uuid) AND status='not_started'
                    """
                    ),
                    {"tenant": TENANT_ID, "run": RUN_ID},
                ).scalar_one()
                == 100
            )


def test_step10_staged_sanad_boundary_commits_coverage_in_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.services.runs import methodology_postgres_materialization_runtime as runtime

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        selection_id = _seed_selected_coverage(database)
        seeded = _seed_terminal_tasks(database, selection_id, found_count=1)
        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            repository.complete_execution_step_exact_set(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=RUN_STEP_ID,
                expected_task_ids=seeded.task_ids,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )
        _seed_first_canonical_snapshot(database, seeded)
        monkeypatch.setattr(runtime, "_emit_audit", lambda *_args, **_kwargs: None)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            factual = repository.load_factual_output_records(deal_id=DEAL_ID, run_id=RUN_ID)
            signature = tuple(
                sorted(
                    (
                        str(row["factual_output_record_id"]),
                        str(row["typed_output_sha256"]),
                    )
                    for row in factual
                )
            )
            staged = runtime._StagedSanadWrite(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=SANAD_STEP_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
                audit_sink=object(),
                records=(),
                grades=(),
                defects=(),
                factual_signature=signature,
            )
            staged.persist(
                db_conn=connection,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )
            status = connection.execute(
                text(
                    """
                    SELECT status FROM methodology_coverage_records
                    WHERE tenant_id=CAST(:tenant AS uuid)
                      AND coverage_record_id=:coverage
                    """
                ),
                {"tenant": TENANT_ID, "coverage": seeded.first["coverage"]},
            ).scalar_one()

        assert status == "answered"


def test_step10_audit_failure_rolls_back_the_final_coverage_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.services.runs import methodology_postgres_materialization_runtime as runtime

    class _InjectedAuditFailure(RuntimeError):
        pass

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        selection_id = _seed_selected_coverage(database)
        seeded = _seed_terminal_tasks(database, selection_id, found_count=1)
        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            repository.complete_execution_step_exact_set(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=RUN_STEP_ID,
                expected_task_ids=seeded.task_ids,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )
        _seed_first_canonical_snapshot(database, seeded)

        def fail_audit(*_args: Any, **_kwargs: Any) -> None:
            raise _InjectedAuditFailure

        monkeypatch.setattr(runtime, "_emit_audit", fail_audit)
        with (
            pytest.raises(_InjectedAuditFailure),
            database.app_engine.begin() as connection,
        ):
            set_tenant_local(connection, TENANT_ID)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
            factual = repository.load_factual_output_records(deal_id=DEAL_ID, run_id=RUN_ID)
            staged = runtime._StagedSanadWrite(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=SANAD_STEP_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
                audit_sink=object(),
                records=(),
                grades=(),
                defects=(),
                factual_signature=tuple(
                    sorted(
                        (
                            str(row["factual_output_record_id"]),
                            str(row["typed_output_sha256"]),
                        )
                        for row in factual
                    )
                ),
            )
            staged.persist(
                db_conn=connection,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            status = connection.execute(
                text(
                    """
                    SELECT status FROM methodology_coverage_records
                    WHERE tenant_id=CAST(:tenant AS uuid)
                      AND coverage_record_id=:coverage
                    """
                ),
                {"tenant": TENANT_ID, "coverage": seeded.first["coverage"]},
            ).scalar_one()
        assert status == "extracted"
