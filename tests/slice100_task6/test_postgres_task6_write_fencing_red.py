"""Real-PostgreSQL RED contracts for every Task-6 write boundary's lease."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from idis.methodology.category_mapping import load_commercial_dd_question_category_map
from idis.methodology.models import MethodologyType
from idis.models.document_classification import CddDocumentCategory, FddDocumentCategory
from idis.models.extraction_task import (
    ExtractionTask,
    ExtractionTaskStatus,
    SourceSpanReference,
    StrictExtractionTaskAdmission,
)
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.methodologies import PostgresMethodologiesRepository
from idis.persistence.repositories.methodology_extraction import (
    WORKER_LEASE_FENCED,
    MethodologyExtractionRepositoryError,
    PostgresMethodologyExtractionRepository,
)
from idis.services.extraction.task_planner import build_expected_answer_schema
from idis.services.runs.methodology_extraction_task_planning import (
    StagedMethodologyTaskPlanWrite,
)
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task6._support import (
    SOURCE_BINDING_COLUMN_SQL,
    SOURCE_BINDING_VALUE_SQL,
    TASK6_SCHEMA_HEAD,
    bind_frozen_run_source,
    insert_bound_extra_span,
    insert_list_suffix,
    schema_head_installed,
    set_seed_tenant,
    unbound_source_binding_params,
)
from tests.slice100_task6.test_coverage_projections_postgres_red import (
    DEAL_ID,
    DOCUMENT_ID,
    HASH_A,
    HASH_B,
    LEASE_OWNER_ID,
    RUN_ID,
    RUN_STEP_ID,
    SPAN_ID,
    TENANT_ID,
    _seed_selected_coverage,
)
from tests.slice100_task6.test_selected_pair_planning_red import (
    CDD_DOCUMENT_ID,
    CDD_SOURCE_SHA256,
    CDD_SOURCE_TEXT,
    CDD_SPAN_ID,
    FDD_DOCUMENT_ID,
    FDD_SOURCE_SHA256,
    FDD_SOURCE_TEXT,
    FDD_SPAN_ID,
)

FOREIGN_TENANT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-bbbbbbbbbbbb"
SUCCESSOR_OWNER_ID = "bbbbbbbb-bbbb-4bbb-8bbb-cccccccccccc"
TASK_RECORD_ID = "cccccccc-cccc-4ccc-8ccc-dddddddddddd"
TASK_SPAN_RECORD_ID = "dddddddd-dddd-4ddd-8ddd-eeeeeeeeeeee"
EXTRA_SPAN_ID = "eeeeeeee-eeee-4eee-8eee-ffffffffffff"
EXTRA_TASK_SPAN_RECORD_ID = "ffffffff-ffff-4fff-8fff-aaaaaaaaaaaa"
CROSS_DOCUMENT_TASK_SPAN_RECORD_ID = "acacacac-acac-4cac-8cac-acacacacacac"
ATTEMPT_A_ID = "01010101-0101-4101-8101-010101010101"
ATTEMPT_B_ID = "02020202-0202-4202-8202-020202020202"
RESERVATION_A_ID = "03030303-0303-4303-8303-030303030303"
RESERVATION_B_ID = "04040404-0404-4404-8404-040404040404"
MISMATCHED_RESULT_ID = "05050505-0505-4505-8505-050505050505"
ORDERING_SPAN_TEXT = "Second exact methodology span"
ORDERING_SPAN_SHA256 = hashlib.sha256(ORDERING_SPAN_TEXT.encode("utf-8")).hexdigest()


def _prepare_planning_database(
    database: DisposablePostgres,
    *,
    schema_revision: str = TASK6_SCHEMA_HEAD,
) -> tuple[str, tuple[object, ...]]:
    migrate(database.admin_engine, schema_revision)
    grant_application_access(database)
    selection_id = _seed_selected_coverage(database)
    at_head = schema_revision == TASK6_SCHEMA_HEAD
    binding_columns = insert_list_suffix(SOURCE_BINDING_COLUMN_SQL, required=at_head)
    binding_values = insert_list_suffix(SOURCE_BINDING_VALUE_SQL, required=at_head)
    binding_params = unbound_source_binding_params() if at_head else {}
    with database.admin_engine.begin() as connection:
        set_seed_tenant(connection, TENANT_ID)
        connection.execute(
            text(
                f"""
                INSERT INTO document_artifacts (
                    doc_id, tenant_id, deal_id, doc_type, title,
                    source_system, version_id, sha256{binding_columns}
                ) VALUES
                (gen_random_uuid(), CAST(:tenant_id AS uuid), CAST(:deal_id AS uuid),
                 'DATA_ROOM_FILE', 'Fictional CDD', 'fixture', 'v1', :hash_a{binding_values}),
                (gen_random_uuid(), CAST(:tenant_id AS uuid), CAST(:deal_id AS uuid),
                 'DATA_ROOM_FILE', 'Fictional FDD', 'fixture', 'v1', :hash_b{binding_values})
                RETURNING doc_id
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "hash_a": HASH_A,
                "hash_b": HASH_B,
                **binding_params,
            },
        ).fetchall()
        artifact_rows = connection.execute(
            text(
                """
                SELECT doc_id FROM document_artifacts
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND title IN ('Fictional CDD', 'Fictional FDD')
                ORDER BY title
                """
            ),
            {"tenant_id": TENANT_ID, "deal_id": DEAL_ID},
        ).fetchall()
        assert len(artifact_rows) == 2
        cdd_artifact_id = str(artifact_rows[0].doc_id)
        fdd_artifact_id = str(artifact_rows[1].doc_id)
        connection.execute(
            text(
                f"""
                INSERT INTO documents (
                    document_id, tenant_id, deal_id, doc_id, doc_type, parse_status{binding_columns}
                ) VALUES
                (CAST(:cdd_document AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:cdd_artifact AS uuid),
                 'PDF', 'PARSED'{binding_values}),
                (CAST(:fdd_document AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:fdd_artifact AS uuid),
                 'XLSX', 'PARSED'{binding_values});
                INSERT INTO document_spans (
                    span_id, tenant_id, deal_id, document_id, span_type,
                    locator, text_excerpt, content_hash{binding_columns}
                ) VALUES
                (CAST(:cdd_span AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:cdd_document AS uuid), 'PAGE_TEXT',
                 jsonb_build_object('page', 1, 'line', 1),
                 :cdd_text, :cdd_source_sha256{binding_values}),
                (CAST(:fdd_span AS uuid), CAST(:tenant_id AS uuid),
                 CAST(:deal_id AS uuid), CAST(:fdd_document AS uuid), 'CELL',
                 jsonb_build_object('sheet', 'P&L', 'cell', 'A1', 'row', 1, 'col', 1),
                 :fdd_text, :fdd_source_sha256{binding_values})
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "cdd_artifact": cdd_artifact_id,
                "fdd_artifact": fdd_artifact_id,
                "cdd_document": CDD_DOCUMENT_ID,
                "fdd_document": FDD_DOCUMENT_ID,
                "cdd_span": CDD_SPAN_ID,
                "fdd_span": FDD_SPAN_ID,
                "cdd_text": CDD_SOURCE_TEXT,
                "fdd_text": FDD_SOURCE_TEXT,
                "cdd_source_sha256": CDD_SOURCE_SHA256,
                "fdd_source_sha256": FDD_SOURCE_SHA256,
                **binding_params,
            },
        )

    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        methodology_repository = PostgresMethodologiesRepository(connection, TENANT_ID)
        selection = methodology_repository.load_run_selection(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
        )
        assert selection is not None
        registries = methodology_repository.load_selected_registries(selection=selection)
        coverage = methodology_repository.list_coverage_records(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
        )
        questions = {
            question.methodology_question_id: question
            for registry in registries
            for question in registry.current_version.questions
        }
        category_map = load_commercial_dd_question_category_map()
        planned: list[ExtractionTask] = []
        # This lane proves fenced repository/graph behavior.  The genuine-PG
        # composition suite is the authority for governed-map planner output.
        for record in coverage:
            question = questions[record.methodology_question_id]
            is_cdd = record.methodology_type is MethodologyType.COMMERCIAL_DD
            document_id = CDD_DOCUMENT_ID if is_cdd else FDD_DOCUMENT_ID
            source_span = SourceSpanReference(
                document_id=document_id,
                span_id=CDD_SPAN_ID if is_cdd else FDD_SPAN_ID,
                evidence_tags=[],
                locator=(
                    {"page": 1, "line": 1}
                    if is_cdd
                    else {"sheet": "P&L", "cell": "A1", "row": 1, "col": 1}
                ),
                span_type="PAGE_TEXT" if is_cdd else "CELL",
                content_hash=CDD_SOURCE_SHA256 if is_cdd else FDD_SOURCE_SHA256,
                text_excerpt=CDD_SOURCE_TEXT if is_cdd else FDD_SOURCE_TEXT,
            )
            primary_fdd = (
                FddDocumentCategory.UNKNOWN
                if is_cdd
                else FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL
            )
            primary_cdd = (
                CddDocumentCategory.MARKET_RESEARCH if is_cdd else CddDocumentCategory.UNKNOWN
            )
            matched_category = primary_cdd.value if is_cdd else primary_fdd.value
            planned.append(
                ExtractionTask(
                    tenant_id=TENANT_ID,
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    status=ExtractionTaskStatus.READY,
                    reason_codes=["ready"],
                    methodology_id=question.methodology_id,
                    methodology_version_id=question.methodology_version_id,
                    methodology_question_id=question.methodology_question_id,
                    methodology_type=question.methodology_type,
                    methodology_section=question.section,
                    coverage_record_id=record.coverage_record_id,
                    document_id=document_id,
                    classification_id=(
                        "dc_repository_fencing_cdd" if is_cdd else "dc_repository_fencing_fdd"
                    ),
                    source_spans=[source_span],
                    target_fdd_category=primary_fdd,
                    target_cdd_category=primary_cdd,
                    strict_admission=StrictExtractionTaskAdmission(
                        contract_version="strict-category-span-admission-v2",
                        matched_category=matched_category,
                        classified_primary_fdd_category=primary_fdd,
                        classified_primary_cdd_category=primary_cdd,
                        classified_secondary_fdd_categories=[],
                        classified_secondary_cdd_categories=[],
                        cdd_category_mapping_version=category_map.mapping_version,
                        cdd_category_mapping_sha256=category_map.canonical_payload_sha256,
                        classifier_contract_version=category_map.classifier_version,
                        classifier_reachable_categories_sha256=(
                            category_map.classifier_reachable_categories_sha256
                        ),
                    ),
                    required_evidence=list(question.required_evidence),
                    expected_answer_schema=build_expected_answer_schema(question),
                    validation_requirements=list(question.validation_requirements),
                )
            )
    assert len(planned) == 100
    return selection_id, tuple(
        sorted(
            planned,
            key=lambda item: (
                item.methodology_question_id,
                item.document_id or "",
                item.extraction_task_id or "",
            ),
        )
    )


def _task6_write_counts(database: DisposablePostgres) -> tuple[int, ...]:
    with database.admin_engine.connect() as connection:
        return tuple(
            int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in (
                "methodology_extraction_tasks",
                "methodology_extraction_task_spans",
                "methodology_extraction_attempts",
                "methodology_extraction_results",
                "methodology_result_factual_outputs",
                "methodology_result_claim_links",
                "methodology_result_evidence_links",
                "methodology_result_sanad_links",
                "methodology_result_defect_links",
            )
        )


def _bind_planned_documents(connection: Any) -> None:
    """Freeze CDD and FDD once the last span exists. Skip below schema head."""

    if not schema_head_installed(connection):
        return
    bind_frozen_run_source(
        connection,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_ids=(CDD_DOCUMENT_ID, FDD_DOCUMENT_ID),
    )


def _persist_planned_graph(
    database: DisposablePostgres,
    selection_id: str,
    tasks: tuple[object, ...],
) -> None:
    with database.app_engine.begin() as connection:
        _bind_planned_documents(connection)
        _staged_write(selection_id, tasks).persist(
            db_conn=connection,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )


def _planned_graph_snapshot(database: DisposablePostgres) -> tuple[tuple[object, ...], ...]:
    with database.admin_engine.connect() as connection:
        task_rows = connection.execute(
            text(
                """
                SELECT task_record_id, selection_id, coverage_record_id,
                       methodology_version_record_id, methodology_version_id,
                       methodology_question_record_id, methodology_question_id,
                       methodology_type, extraction_task_id, document_id,
                       document_classification_id, document_classification_sha256,
                       planner_contract_version, task_contract_version,
                       task_payload::text, task_input_sha256,
                       candidate_span_set_sha256, status, created_at
                FROM methodology_extraction_tasks
                ORDER BY task_record_id
                """
            )
        ).fetchall()
        span_rows = connection.execute(
            text(
                """
                SELECT task_span_record_id, task_record_id, document_id, span_id,
                       span_ordinal, content_sha256, locator_sha256, created_at
                FROM methodology_extraction_task_spans
                ORDER BY task_record_id, span_ordinal, span_id
                """
            )
        ).fetchall()
    return tuple(tuple(row) for row in (*task_rows, *span_rows))


def _planning_rehydrate_callback(database: DisposablePostgres) -> object:
    # This graph-corruption matrix isolates the repository loader.  The genuine
    # selected-pair suite owns production worker callback restart recomputation.
    def rehydrate(*, tenant_id: str, deal_id: str, run_id: str) -> object:
        assert (tenant_id, deal_id, run_id) == (TENANT_ID, DEAL_ID, RUN_ID)
        with database.app_engine.begin() as connection:
            records = PostgresMethodologyExtractionRepository(
                connection,
                TENANT_ID,
            ).load_planned_task_records(deal_id=deal_id, run_id=run_id)
            return [record.task for record in records]

    return rehydrate


def _same_deal_cross_document_span(database: DisposablePostgres) -> dict[str, object]:
    """Return a real same-deal span whose document differs from the selected task."""
    with database.admin_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                    SELECT t.task_record_id,
                           t.document_id AS task_document_id,
                           s.document_id AS substituted_document_id,
                           s.span_id AS substituted_span_id,
                           s.content_hash AS substituted_content_sha256,
                           s.locator AS substituted_locator
                    FROM methodology_extraction_tasks AS t
                    JOIN document_spans AS s
                      ON s.tenant_id = t.tenant_id
                     AND s.deal_id = t.deal_id
                     AND s.document_id <> t.document_id
                    WHERE t.tenant_id = CAST(:tenant_id AS uuid)
                      AND t.deal_id = CAST(:deal_id AS uuid)
                      AND t.run_id = CAST(:run_id AS uuid)
                    ORDER BY t.task_record_id, s.document_id, s.span_id
                    LIMIT 1
                    """
                ),
                {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID},
            )
            .mappings()
            .one()
        )
    result = dict(row)
    assert result["task_document_id"] != result["substituted_document_id"]
    result["substituted_locator_sha256"] = hashlib.sha256(
        json.dumps(
            result["substituted_locator"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return result


def _seed_two_succeeded_attempts(
    database: DisposablePostgres,
) -> tuple[dict[str, object], dict[str, object]]:
    """Persist two valid terminal attempts over two distinct planned task inputs."""
    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        PostgresMethodologyExtractionRepository(connection, TENANT_ID)._require_fence(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )
        task_rows = [
            dict(row)
            for row in (
                connection.execute(
                    text(
                        """
                        SELECT t.task_record_id, t.task_input_sha256,
                               t.document_id, s.span_id
                        FROM methodology_extraction_tasks AS t
                        JOIN methodology_extraction_task_spans AS s
                          ON s.tenant_id = t.tenant_id
                         AND s.deal_id = t.deal_id
                         AND s.run_id = t.run_id
                         AND s.task_record_id = t.task_record_id
                         AND s.document_id = t.document_id
                         AND s.span_ordinal = 1
                        WHERE t.tenant_id = CAST(:tenant_id AS uuid)
                          AND t.deal_id = CAST(:deal_id AS uuid)
                          AND t.run_id = CAST(:run_id AS uuid)
                        ORDER BY t.task_record_id
                        LIMIT 2
                        """
                    ),
                    {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID},
                )
                .mappings()
                .all()
            )
        ]
        assert len(task_rows) == 2
        assert task_rows[0]["task_record_id"] != task_rows[1]["task_record_id"]
        assert task_rows[0]["task_input_sha256"] != task_rows[1]["task_input_sha256"]
        attempt_rows = (
            {
                **task_rows[0],
                "attempt_id": ATTEMPT_A_ID,
                "reservation_id": RESERVATION_A_ID,
            },
            {
                **task_rows[1],
                "attempt_id": ATTEMPT_B_ID,
                "reservation_id": RESERVATION_B_ID,
            },
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
                    price_catalog_row_sha256, input_usd_per_million_tokens,
                    output_usd_per_million_tokens, reserved_calls,
                    reserved_input_tokens, reserved_output_tokens,
                    reserved_embedding_tokens, reserved_usd, budget_terminal_state
                ) VALUES (
                    CAST(:attempt_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:run_step_id AS uuid), CAST(:task_record_id AS uuid),
                    :task_input_sha256, CAST(:lease_owner_id AS uuid), 1,
                    'STARTED', 'anthropic', 'hermetic-test', 'HERMETIC_TEST',
                    'fixture-model', 'METHODOLOGY_QUESTION_EXTRACTION_V1', '1.0.0',
                    :hash_a, :hash_a, :hash_a, :hash_a, :hash_b, :hash_a, 1,
                    false, 512, 0, true, 'fixture-policy', 'anthropic',
                    CAST(:reservation_id AS uuid), 'fixture-price-v1', :hash_a,
                    0, 0, 0, 0, 0, 0, 0, 'NOT_BILLABLE'
                )
                """
            ),
            [
                {
                    **row,
                    "tenant_id": TENANT_ID,
                    "deal_id": DEAL_ID,
                    "run_id": RUN_ID,
                    "run_step_id": RUN_STEP_ID,
                    "lease_owner_id": LEASE_OWNER_ID,
                    "hash_a": HASH_A,
                    "hash_b": HASH_B,
                }
                for row in attempt_rows
            ],
        )
        connection.execute(
            text(
                """
                UPDATE methodology_extraction_attempts
                SET status = 'SUCCEEDED', observed_model = configured_model,
                    provider_request_id = 'fixture-request',
                    provider_response_sha256 = :hash_a,
                    dispatched_call_sha256 = :hash_b,
                    completed_at = statement_timestamp(), latency_ms = 1,
                    input_tokens = 0, output_tokens = 0,
                    stop_reason = 'end_turn', receipt_sha256 = :hash_a
                WHERE attempt_id IN (
                    CAST(:attempt_a AS uuid), CAST(:attempt_b AS uuid)
                )
                """
            ),
            {
                "attempt_a": ATTEMPT_A_ID,
                "attempt_b": ATTEMPT_B_ID,
                "hash_a": HASH_A,
                "hash_b": HASH_B,
            },
        )
    return dict(attempt_rows[0]), dict(attempt_rows[1])


def _inject_planned_graph_corruption(
    database: DisposablePostgres,
    corruption: str,
) -> None:
    with database.admin_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        first = (
            connection.execute(
                text(
                    """
                SELECT task_record_id, document_id, task_payload
                FROM methodology_extraction_tasks
                ORDER BY task_record_id
                LIMIT 1
                """
                )
            )
            .mappings()
            .one()
        )
        task_record_id = str(first["task_record_id"])
        if corruption == "missing_span":
            connection.execute(
                text(
                    "DELETE FROM methodology_extraction_task_spans "
                    "WHERE task_record_id=CAST(:task_record_id AS uuid)"
                ),
                {"task_record_id": task_record_id},
            )
        elif corruption == "missing_task":
            connection.execute(
                text(
                    "DELETE FROM methodology_extraction_task_spans "
                    "WHERE task_record_id=CAST(:task_record_id AS uuid); "
                    "DELETE FROM methodology_extraction_tasks "
                    "WHERE task_record_id=CAST(:task_record_id AS uuid)"
                ),
                {"task_record_id": task_record_id},
            )
        elif corruption == "extra_span":
            connection.execute(text("SET LOCAL session_replication_role = origin"))
            insert_bound_extra_span(
                connection,
                document_id=str(first["document_id"]),
                span_id=EXTRA_SPAN_ID,
            )
            connection.execute(
                text(
                    """
                    SELECT set_config(
                               'idis.worker_lease_tenant_id', run.tenant_id::text, true
                           ),
                           set_config('idis.worker_lease_deal_id', run.deal_id::text, true),
                           set_config('idis.worker_lease_run_id', run.run_id::text, true),
                           set_config(
                               'idis.worker_lease_owner_id', run.lease_owner_id::text, true
                           ),
                           set_config(
                               'idis.worker_lease_fencing_token',
                               run.lease_fencing_token::text,
                               true
                           )
                    FROM methodology_extraction_tasks AS task
                    JOIN runs AS run
                      ON run.tenant_id = task.tenant_id
                     AND run.run_id = task.run_id
                    WHERE task.task_record_id = CAST(:task_record_id AS uuid)
                    """
                ),
                {"task_record_id": task_record_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO methodology_extraction_task_spans (
                        task_span_record_id, tenant_id, deal_id, run_id,
                        task_record_id, document_id, span_id, span_ordinal,
                        content_sha256, locator_sha256
                    )
                    SELECT CAST(:task_span_record_id AS uuid), task.tenant_id,
                           task.deal_id, task.run_id, task.task_record_id,
                           span.document_id, span.span_id, 2, span.content_hash,
                           :locator_sha256
                    FROM methodology_extraction_tasks AS task
                    JOIN document_spans AS span
                      ON span.tenant_id = task.tenant_id
                     AND span.deal_id = task.deal_id
                     AND span.document_id = task.document_id
                     AND span.span_id = CAST(:span_id AS uuid)
                    WHERE task.task_record_id = CAST(:task_record_id AS uuid)
                    """
                ),
                {
                    "task_span_record_id": EXTRA_TASK_SPAN_RECORD_ID,
                    "task_record_id": task_record_id,
                    "span_id": EXTRA_SPAN_ID,
                    "locator_sha256": "7" * 64,
                },
            )
        elif corruption == "ordinal_drift":
            connection.execute(
                text(
                    "UPDATE methodology_extraction_task_spans SET span_ordinal=2 "
                    "WHERE task_record_id=CAST(:task_record_id AS uuid)"
                ),
                {"task_record_id": task_record_id},
            )
        elif corruption == "content_hash_drift":
            connection.execute(
                text(
                    "UPDATE methodology_extraction_task_spans SET content_sha256=:hash "
                    "WHERE task_record_id=CAST(:task_record_id AS uuid)"
                ),
                {"task_record_id": task_record_id, "hash": "9" * 64},
            )
        elif corruption == "locator_hash_drift":
            connection.execute(
                text(
                    "UPDATE methodology_extraction_task_spans SET locator_sha256=:hash "
                    "WHERE task_record_id=CAST(:task_record_id AS uuid)"
                ),
                {"task_record_id": task_record_id, "hash": "8" * 64},
            )
        elif corruption == "cross_document_substitution":
            connection.execute(
                text(
                    """
                    UPDATE methodology_extraction_task_spans
                    SET document_id=CAST(:document_id AS uuid),
                        span_id=CAST(:span_id AS uuid)
                    WHERE task_record_id=CAST(:task_record_id AS uuid)
                    """
                ),
                {
                    "task_record_id": task_record_id,
                    "document_id": "abababab-abab-4bab-8bab-abababababab",
                    "span_id": "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd",
                },
            )
        elif corruption == "task_uuid_drift":
            payload = dict(first["task_payload"])
            payload["reason_codes"] = ["CORRUPTED_BUT_SCHEMA_VALID"]
            canonical = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            task_input_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            connection.execute(
                text(
                    """
                    UPDATE methodology_extraction_tasks
                    SET task_payload=CAST(:payload AS jsonb),
                        task_input_sha256=:task_input_sha256
                    WHERE task_record_id=CAST(:task_record_id AS uuid)
                    """
                ),
                {
                    "task_record_id": task_record_id,
                    "payload": canonical,
                    "task_input_sha256": task_input_sha256,
                },
            )
        else:
            raise AssertionError(f"unknown corruption: {corruption}")


def _staged_write(selection_id: str, tasks: tuple[object, ...]) -> StagedMethodologyTaskPlanWrite:
    return StagedMethodologyTaskPlanWrite(
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        selection_id=selection_id,
        tasks=tasks,  # type: ignore[arg-type]
        repository_factory=lambda db_conn: PostgresMethodologyExtractionRepository(
            db_conn, TENANT_ID
        ),
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=1,
    )


def _first_coverage_lineage(database: DisposablePostgres) -> dict[str, str]:
    with database.admin_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                SELECT coverage_record_id, methodology_version_record_id,
                       methodology_version_id, methodology_question_record_id,
                       methodology_question_id, methodology_type
                FROM methodology_coverage_records
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                ORDER BY methodology_question_record_id
                LIMIT 1
                """
                ),
                {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID},
            )
            .mappings()
            .one()
        )
    return {key: str(value) for key, value in row.items()}


def _raw_task_params(database: DisposablePostgres, selection_id: str) -> dict[str, str]:
    return {
        **_first_coverage_lineage(database),
        "task_record_id": TASK_RECORD_ID,
        "task_span_record_id": TASK_SPAN_RECORD_ID,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "selection_id": selection_id,
        "document_id": DOCUMENT_ID,
        "span_id": SPAN_ID,
        "hash_a": HASH_A,
        "hash_b": HASH_B,
    }


_RAW_TASK_INSERT = """
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
        CAST(:task_record_id AS uuid), CAST(:tenant_id AS uuid),
        CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
        CAST(:selection_id AS uuid), :coverage_record_id,
        CAST(:methodology_version_record_id AS uuid), :methodology_version_id,
        CAST(:methodology_question_record_id AS uuid), :methodology_question_id,
        :methodology_type, 'et_unfenced_fixture', CAST(:document_id AS uuid),
        'dc_fixture', :hash_a, 'selected-pair-planner-v1',
        'methodology-extraction-task-v1', '{"task":"fixture"}'::jsonb,
        :hash_a, :hash_b, 'READY'
    )
"""

_RAW_SPAN_INSERT = """
    INSERT INTO methodology_extraction_task_spans (
        task_span_record_id, tenant_id, deal_id, run_id, task_record_id,
        document_id, span_id, span_ordinal, content_sha256, locator_sha256
    ) VALUES (
        CAST(:task_span_record_id AS uuid), CAST(:tenant_id AS uuid),
        CAST(:deal_id AS uuid), CAST(:run_id AS uuid), CAST(:task_record_id AS uuid),
        CAST(:document_id AS uuid), CAST(:span_id AS uuid), 1, :hash_a, :hash_b
    )
"""


@pytest.mark.parametrize("row_kind", ("task", "span"))
def test_task_or_span_insert_without_validated_lease_context_fails(row_kind: str) -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        selection_id = _seed_selected_coverage(database)
        params = _raw_task_params(database, selection_id)
        if row_kind == "span":
            with database.admin_engine.begin() as connection:
                connection.execute(text("SET LOCAL session_replication_role = replica"))
                connection.execute(text(_RAW_TASK_INSERT), params)

        before = _task6_write_counts(database)
        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            with pytest.raises(DBAPIError) as exc_info:
                connection.execute(
                    text(_RAW_TASK_INSERT if row_kind == "task" else _RAW_SPAN_INSERT),
                    params,
                )
            assert "WORKER_LEASE_FENCED" in str(exc_info.value)
        assert _task6_write_counts(database) == before


@pytest.mark.parametrize("fence_state", ("missing", "successor", "expired", "cross_tenant"))
def test_planning_repository_rejects_invalid_fence_with_zero_mutation(fence_state: str) -> None:
    with disposable_postgres() as database:
        selection_id, tasks = _prepare_planning_database(database)
        if fence_state == "successor":
            with database.admin_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE runs
                        SET lease_owner_id = CAST(:owner_id AS uuid),
                            lease_fencing_token = 3,
                            lease_expires_at = statement_timestamp() + interval '1 hour',
                            last_heartbeat_at = statement_timestamp()
                        WHERE run_id = CAST(:run_id AS uuid)
                        """
                    ),
                    {"owner_id": SUCCESSOR_OWNER_ID, "run_id": RUN_ID},
                )
        elif fence_state == "expired":
            with database.admin_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE runs SET lease_expires_at = "
                        "statement_timestamp() - interval '1 second' "
                        "WHERE run_id = CAST(:run_id AS uuid)"
                    ),
                    {"run_id": RUN_ID},
                )

        repository_tenant = FOREIGN_TENANT_ID if fence_state == "cross_tenant" else TENANT_ID
        owner_id = "" if fence_state == "missing" else LEASE_OWNER_ID
        token = 0 if fence_state == "missing" else 1
        before = _task6_write_counts(database)
        with database.app_engine.begin() as connection:
            repository = PostgresMethodologyExtractionRepository(connection, repository_tenant)
            with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
                repository.persist_planned_tasks(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    selection_id=selection_id,
                    tasks=tasks,  # type: ignore[arg-type]
                    lease_owner_id=owner_id,
                    lease_fencing_token=token,
                )
            assert exc_info.value.code == WORKER_LEASE_FENCED
        assert _task6_write_counts(database) == before


def test_valid_step6_staged_boundary_persists_complete_task_and_span_set() -> None:
    with disposable_postgres() as database:
        selection_id, tasks = _prepare_planning_database(database)
        assert _task6_write_counts(database) == (0,) * 9

        with database.app_engine.begin() as connection:
            _bind_planned_documents(connection)
            _staged_write(selection_id, tasks).persist(
                db_conn=connection,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )

        counts = _task6_write_counts(database)
        assert counts[0] == 100
        assert counts[1] == 100
        assert counts[2:] == (0,) * 7


def test_exact_plan_replay_is_a_true_noop() -> None:
    with disposable_postgres() as database:
        selection_id, tasks = _prepare_planning_database(database)
        _persist_planned_graph(database, selection_id, tasks)
        before = _planned_graph_snapshot(database)

        _persist_planned_graph(database, selection_id, tasks)

        assert _planned_graph_snapshot(database) == before
        rehydrated = _planning_rehydrate_callback(database)(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
        )
        assert [task.model_dump(mode="json") for task in rehydrated] == [
            task.model_dump(mode="json") for task in tasks
        ]


def test_task_span_document_identity_has_exact_fk_and_referencing_index() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")

        with database.admin_engine.connect() as connection:
            task_unique = connection.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid, true)
                    FROM pg_constraint
                    WHERE conrelid = 'methodology_extraction_tasks'::regclass
                      AND conname = 'uq_methodology_extraction_tasks_scope_document'
                    """
                )
            ).scalar_one_or_none()
            task_span_fk = connection.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid, true)
                    FROM pg_constraint
                    WHERE conrelid = 'methodology_extraction_task_spans'::regclass
                      AND conname =
                          'fk_methodology_extraction_task_spans_task_document'
                    """
                )
            ).scalar_one_or_none()
            task_span_index = connection.execute(
                text(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'methodology_extraction_task_spans'
                      AND indexname =
                          'ix_methodology_extraction_task_spans_task_document'
                    """
                )
            ).scalar_one_or_none()

        assert task_unique is not None
        assert " ".join(task_unique.split()) == (
            "UNIQUE (tenant_id, deal_id, run_id, task_record_id, document_id)"
        )
        assert task_span_fk is not None
        assert " ".join(task_span_fk.split()) == (
            "FOREIGN KEY (tenant_id, deal_id, run_id, task_record_id, document_id) "
            "REFERENCES methodology_extraction_tasks(tenant_id, deal_id, run_id, "
            "task_record_id, document_id)"
        )
        assert task_span_index is not None
        assert " ".join(task_span_index.split()).endswith(
            "USING btree (tenant_id, deal_id, run_id, task_record_id, document_id)"
        )


def test_result_attempt_task_input_identity_has_exact_constraints_and_indexes() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")

        constraint_names = (
            "uq_methodology_extraction_tasks_scope_input",
            "fk_methodology_extraction_attempts_task_input",
            "uq_methodology_extraction_attempts_scope_task_input",
            "fk_methodology_extraction_results_task_input",
            "fk_methodology_extraction_results_attempt_task_input",
            "uq_methodology_extraction_results_attempt",
        )
        with database.admin_engine.connect() as connection:
            constraints = {
                str(row.conname): " ".join(str(row.definition).split())
                for row in connection.execute(
                    text(
                        """
                        SELECT conname, pg_get_constraintdef(oid, true) AS definition
                        FROM pg_constraint
                        WHERE conname IN (
                            'uq_methodology_extraction_tasks_scope_input',
                            'fk_methodology_extraction_attempts_task_input',
                            'uq_methodology_extraction_attempts_scope_task_input',
                            'fk_methodology_extraction_results_task_input',
                            'fk_methodology_extraction_results_attempt_task_input',
                            'uq_methodology_extraction_results_attempt'
                        )
                        ORDER BY conname
                        """
                    )
                )
            }
            indexes = {
                str(row.indexname): " ".join(str(row.indexdef).split())
                for row in connection.execute(
                    text(
                        """
                        SELECT indexname, indexdef
                        FROM pg_indexes
                        WHERE schemaname = current_schema()
                          AND indexname IN (
                              'ix_methodology_extraction_attempts_task_input',
                              'ix_methodology_extraction_results_task_input'
                          )
                        ORDER BY indexname
                        """
                    )
                )
            }

        assert set(constraints) == set(constraint_names)
        assert constraints["uq_methodology_extraction_tasks_scope_input"] == (
            "UNIQUE (tenant_id, deal_id, run_id, task_record_id, task_input_sha256)"
        )
        assert constraints["fk_methodology_extraction_attempts_task_input"] == (
            "FOREIGN KEY (tenant_id, deal_id, run_id, task_record_id, "
            "task_input_sha256) REFERENCES methodology_extraction_tasks(tenant_id, "
            "deal_id, run_id, task_record_id, task_input_sha256)"
        )
        assert constraints["uq_methodology_extraction_attempts_scope_task_input"] == (
            "UNIQUE (tenant_id, deal_id, run_id, attempt_id, task_record_id, task_input_sha256)"
        )
        assert constraints["fk_methodology_extraction_results_task_input"] == (
            "FOREIGN KEY (tenant_id, deal_id, run_id, task_record_id, "
            "task_input_sha256) REFERENCES methodology_extraction_tasks(tenant_id, "
            "deal_id, run_id, task_record_id, task_input_sha256)"
        )
        assert constraints["fk_methodology_extraction_results_attempt_task_input"] == (
            "FOREIGN KEY (tenant_id, deal_id, run_id, attempt_id, task_record_id, "
            "task_input_sha256) REFERENCES methodology_extraction_attempts(tenant_id, "
            "deal_id, run_id, attempt_id, task_record_id, task_input_sha256)"
        )
        assert constraints["uq_methodology_extraction_results_attempt"] == (
            "UNIQUE (tenant_id, deal_id, run_id, attempt_id)"
        )
        assert set(indexes) == {
            "ix_methodology_extraction_attempts_task_input",
            "ix_methodology_extraction_results_task_input",
        }
        assert indexes["ix_methodology_extraction_attempts_task_input"].endswith(
            "USING btree (tenant_id, deal_id, run_id, task_record_id, task_input_sha256)"
        )
        assert indexes["ix_methodology_extraction_results_task_input"].endswith(
            "USING btree (tenant_id, deal_id, run_id, task_record_id, task_input_sha256)"
        )


def test_result_insert_rejects_attempt_for_another_valid_task_input() -> None:
    with disposable_postgres() as database:
        selection_id, tasks = _prepare_planning_database(database)
        _persist_planned_graph(database, selection_id, tasks)
        attempt_a, attempt_b = _seed_two_succeeded_attempts(database)
        before = _task6_write_counts(database)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            PostgresMethodologyExtractionRepository(connection, TENANT_ID)._require_fence(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )
            with pytest.raises(DBAPIError) as exc_info:
                connection.execute(
                    text(
                        """
                        INSERT INTO methodology_extraction_results (
                            result_id, tenant_id, deal_id, run_id, task_record_id,
                            attempt_id, task_input_sha256, outcome, result_payload,
                            result_sha256, searched_document_ids, searched_span_ids,
                            searched_scope_sha256, provider_provenance_sha256,
                            gap_finding
                        ) VALUES (
                            CAST(:result_id AS uuid), CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                            CAST(:task_record_id AS uuid), CAST(:attempt_id AS uuid),
                            :task_input_sha256, 'EVIDENCE_NOT_FOUND',
                            jsonb_build_object('outcome', 'EVIDENCE_NOT_FOUND'),
                            :result_sha256, ARRAY[CAST(:document_id AS uuid)],
                            ARRAY[CAST(:span_id AS uuid)], :searched_scope_sha256,
                            :provider_provenance_sha256,
                            '{"reason_code":"EVIDENCE_NOT_FOUND"}'::jsonb
                        )
                        """
                    ),
                    {
                        "result_id": MISMATCHED_RESULT_ID,
                        "tenant_id": TENANT_ID,
                        "deal_id": DEAL_ID,
                        "run_id": RUN_ID,
                        "task_record_id": attempt_a["task_record_id"],
                        "attempt_id": attempt_b["attempt_id"],
                        "task_input_sha256": attempt_a["task_input_sha256"],
                        "result_sha256": HASH_A,
                        "document_id": attempt_a["document_id"],
                        "span_id": attempt_a["span_id"],
                        "searched_scope_sha256": HASH_A,
                        "provider_provenance_sha256": HASH_B,
                    },
                )
            assert getattr(exc_info.value.orig, "pgcode", None) == "23503"

        assert _task6_write_counts(database) == before


def test_task_span_insert_rejects_a_same_deal_span_from_another_document() -> None:
    with disposable_postgres() as database:
        selection_id, tasks = _prepare_planning_database(database)
        _persist_planned_graph(database, selection_id, tasks)
        substituted = _same_deal_cross_document_span(database)
        before = _task6_write_counts(database)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            PostgresMethodologyExtractionRepository(connection, TENANT_ID)._require_fence(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=1,
            )
            with pytest.raises(DBAPIError) as exc_info:
                connection.execute(
                    text(
                        """
                        INSERT INTO methodology_extraction_task_spans (
                            task_span_record_id, tenant_id, deal_id, run_id,
                            task_record_id, document_id, span_id, span_ordinal,
                            content_sha256, locator_sha256
                        ) VALUES (
                            CAST(:task_span_record_id AS uuid), CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                            CAST(:task_record_id AS uuid), CAST(:document_id AS uuid),
                            CAST(:span_id AS uuid), 2, :content_sha256, :locator_sha256
                        )
                        """
                    ),
                    {
                        "task_span_record_id": CROSS_DOCUMENT_TASK_SPAN_RECORD_ID,
                        "tenant_id": TENANT_ID,
                        "deal_id": DEAL_ID,
                        "run_id": RUN_ID,
                        "task_record_id": substituted["task_record_id"],
                        "document_id": substituted["substituted_document_id"],
                        "span_id": substituted["substituted_span_id"],
                        "content_sha256": substituted["substituted_content_sha256"],
                        "locator_sha256": substituted["substituted_locator_sha256"],
                    },
                )
            assert getattr(exc_info.value.orig, "pgcode", None) == "23503"

        assert _task6_write_counts(database) == before


def test_restart_loader_rejects_a_bypassed_same_deal_cross_document_substitution() -> None:
    with disposable_postgres() as database:
        selection_id, tasks = _prepare_planning_database(database)
        _persist_planned_graph(database, selection_id, tasks)
        substituted = _same_deal_cross_document_span(database)

        with database.admin_engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            changed = connection.execute(
                text(
                    """
                    UPDATE methodology_extraction_task_spans
                    SET document_id = CAST(:document_id AS uuid),
                        span_id = CAST(:span_id AS uuid),
                        content_sha256 = :content_sha256,
                        locator_sha256 = :locator_sha256
                    WHERE task_record_id = CAST(:task_record_id AS uuid)
                    """
                ),
                {
                    "task_record_id": substituted["task_record_id"],
                    "document_id": substituted["substituted_document_id"],
                    "span_id": substituted["substituted_span_id"],
                    "content_sha256": substituted["substituted_content_sha256"],
                    "locator_sha256": substituted["substituted_locator_sha256"],
                },
            )
            assert changed.rowcount == 1

        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            _planning_rehydrate_callback(database)(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
            )

        assert exc_info.value.code == "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"


def test_planned_task_payload_and_span_ordinals_share_one_canonical_order() -> None:
    with disposable_postgres() as database:
        selection_id, raw_tasks = _prepare_planning_database(database)
        first = ExtractionTask.model_validate(raw_tasks[0])
        original = first.source_spans[0]
        if original.span_type == "CELL":
            extra_locator: dict[str, object] = {"sheet": "P&L", "cell": "B2", "row": 2, "col": 2}
            extra_span_type = "CELL"
        else:
            extra_locator = {"page": 2, "line": 1}
            extra_span_type = "PAGE_TEXT"
        second = SourceSpanReference(
            document_id=str(first.document_id),
            span_id=EXTRA_SPAN_ID,
            locator=extra_locator,
            span_type=extra_span_type,
            content_hash=ORDERING_SPAN_SHA256,
            text_excerpt=ORDERING_SPAN_TEXT,
        )
        payload = first.model_dump(mode="json")
        payload["extraction_task_id"] = None
        payload["source_spans"] = [
            span.model_dump(mode="json")
            for span in sorted(
                (original, second),
                key=lambda span: span.span_id,
                reverse=True,
            )
        ]
        first_with_two_spans = ExtractionTask.model_validate(payload)
        tasks = (first_with_two_spans, *raw_tasks[1:])
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO document_spans (
                        span_id, tenant_id, deal_id, document_id, span_type,
                        locator, text_excerpt, content_hash,
                        source_binding_state, source_lineage_object_id,
                        source_corpus_receipt_id
                    ) VALUES (
                        CAST(:span_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid), CAST(:document_id AS uuid),
                        :span_type, CAST(:locator AS jsonb), :text_excerpt,
                        :content_hash, :source_binding_state, NULL, NULL
                    )
                    """
                ),
                {
                    "span_id": EXTRA_SPAN_ID,
                    "tenant_id": TENANT_ID,
                    "deal_id": DEAL_ID,
                    "document_id": first.document_id,
                    "span_type": second.span_type,
                    "locator": json.dumps(second.locator),
                    "text_excerpt": ORDERING_SPAN_TEXT,
                    "content_hash": ORDERING_SPAN_SHA256,
                    **unbound_source_binding_params(),
                },
            )

        _persist_planned_graph(database, selection_id, tasks)

        with database.admin_engine.connect() as connection:
            stored = (
                connection.execute(
                    text(
                        """
                        SELECT t.task_payload,
                               array_agg(s.span_id ORDER BY s.span_ordinal) AS span_ids
                        FROM methodology_extraction_tasks t
                        JOIN methodology_extraction_task_spans s
                          ON s.tenant_id=t.tenant_id
                         AND s.deal_id=t.deal_id
                         AND s.run_id=t.run_id
                         AND s.task_record_id=t.task_record_id
                        WHERE t.extraction_task_id=:extraction_task_id
                        GROUP BY t.task_record_id
                        """
                    ),
                    {"extraction_task_id": first_with_two_spans.extraction_task_id},
                )
                .mappings()
                .one()
            )
        payload_span_ids = [span["span_id"] for span in stored["task_payload"]["source_spans"]]
        expected = sorted(payload_span_ids)
        assert payload_span_ids == expected
        assert [str(span_id) for span_id in stored["span_ids"]] == expected


def test_partial_plan_replay_is_rejected_not_repaired() -> None:
    with disposable_postgres() as database:
        selection_id, tasks = _prepare_planning_database(database)
        _persist_planned_graph(database, selection_id, tasks)
        _inject_planned_graph_corruption(database, "missing_span")
        partial = _planned_graph_snapshot(database)
        assert _task6_write_counts(database)[:2] == (100, 99)

        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            _persist_planned_graph(database, selection_id, tasks)

        assert exc_info.value.code == "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"
        assert _planned_graph_snapshot(database) == partial
        assert _task6_write_counts(database)[:2] == (100, 99)


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_task",
        "missing_span",
        "extra_span",
        "ordinal_drift",
        "content_hash_drift",
        "locator_hash_drift",
        "cross_document_substitution",
        "task_uuid_drift",
    ),
)
def test_repository_restart_loader_rejects_graph_corruption(corruption: str) -> None:
    with disposable_postgres() as database:
        selection_id, tasks = _prepare_planning_database(database)
        _persist_planned_graph(database, selection_id, tasks)
        _inject_planned_graph_corruption(database, corruption)

        with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
            _planning_rehydrate_callback(database)(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
            )

        assert exc_info.value.code == "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"


def test_every_non_attempt_task6_insert_table_uses_the_shared_lease_guard() -> None:
    migration = __import__(
        "idis.persistence.migrations.versions.0037_durable_methodology_extraction",
        fromlist=["upgrade"],
    )
    source = __import__("inspect").getsource(migration._create_guards)

    assert "idis_task6_current_lease_is_valid" in source
    assert "idis_task6_immutable_guard" in source
    for table in (
        "methodology_extraction_tasks",
        "methodology_extraction_task_spans",
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
        "methodology_result_claim_links",
        "methodology_result_evidence_links",
        "methodology_result_sanad_links",
        "methodology_result_defect_links",
    ):
        assert table in source
