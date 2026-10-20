"""Real-PostgreSQL RED contracts for the exact canonical human Step-7 boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, CursorResult
from sqlalchemy.exc import DBAPIError

from idis.models.methodology_extraction import (
    MethodologyCandidateSpan,
    MethodologyFactualOutput,
    ValidatedDispatchedMethodologyResult,
    compute_methodology_provider_receipt_sha256,
    compute_methodology_searched_scope_sha256,
)
from idis.models.run_step import STEP_ORDER, StepName
from idis.persistence.repositories.methodology_extraction import (
    WORKER_LEASE_FENCED,
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
from tests.slice100_task6 import test_coverage_projections_postgres_red as coverage
from tests.slice100_task6 import test_postgres_0037_red as pg
from tests.slice100_task6._out_of_band import corrupt_durable_rows_out_of_band
from tests.slice100_task6._support import (
    STEP_CONTRACT_COLUMN_SQL,
    STEP_CONTRACT_VALUE_SQL,
    TASK6_SCHEMA_HEAD,
    legacy_step_contract_params,
    set_seed_tenant,
)

UNRELATED_STEP_ID = "31313131-3131-4313-8313-313131313131"
STEP_CONDITIONS = (
    "canonical_completed",
    "unrelated_running_step_id",
    "wrong_name_order",
)
BOUNDARIES = (
    "admit_attempt",
    "finalize_success",
    "finalize_ordinary_failure",
    "finalize_received_response_invalid",
    "mark_outcome_unknown",
    "complete_exact_set",
)


def _canonical_step_values() -> tuple[str, int]:
    name = StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION
    return name.value, STEP_ORDER[name]


def _corrupt_canonical_step(
    connection: Connection,
    statement: str,
    params: Mapping[str, Any],
) -> CursorResult[Any]:
    """Rewrite the canonical step with both ``run_steps`` guards disabled.

    The helper disables ``ctrg_task7_run_steps_receipt_complete`` and
    ``trg_task7_run_steps_contract_immutable`` before the statement and
    enables them again in reverse. The receipt trigger is deferrable, so
    leaving it enabled would queue an event and make the re-enable fail.
    """

    return corrupt_durable_rows_out_of_band(
        connection,
        statement,
        params,
        table="run_steps",
    )


def _apply_step_condition(
    database: DisposablePostgres,
    condition: str,
    *,
    tenant_id: str,
    run_id: str,
    canonical_step_id: str,
) -> str:
    with database.admin_engine.begin() as connection:
        set_seed_tenant(connection, tenant_id)
        if condition == "canonical_completed":
            changed = connection.execute(
                text(
                    """
                    UPDATE run_steps
                    SET status = 'COMPLETED', finished_at = statement_timestamp()
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                      AND step_id = CAST(:step_id AS uuid)
                    """
                ),
                {"tenant_id": tenant_id, "run_id": run_id, "step_id": canonical_step_id},
            )
            assert changed.rowcount == 1
            return canonical_step_id
        if condition == "unrelated_running_step_id":
            connection.execute(
                text(
                    f"""
                    INSERT INTO run_steps (
                        step_id, tenant_id, run_id, step_name, step_order, status,
                        {STEP_CONTRACT_COLUMN_SQL}
                    ) VALUES (
                        CAST(:step_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:run_id AS uuid), :step_name, :step_order, 'RUNNING',
                        {STEP_CONTRACT_VALUE_SQL}
                    ) ON CONFLICT (step_id) DO NOTHING
                    """
                ),
                {
                    "step_id": UNRELATED_STEP_ID,
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "step_name": StepName.METHODOLOGY_CLAIM_MATERIALIZATION.value,
                    "step_order": STEP_ORDER[StepName.METHODOLOGY_CLAIM_MATERIALIZATION],
                    **legacy_step_contract_params(),
                },
            )
            return UNRELATED_STEP_ID
        if condition == "wrong_name_order":
            changed = _corrupt_canonical_step(
                connection,
                """
                UPDATE run_steps
                SET step_name = :step_name, step_order = :step_order
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND step_id = CAST(:step_id AS uuid)
                """,
                {
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "step_id": canonical_step_id,
                    "step_name": StepName.METHODOLOGY_CLAIM_MATERIALIZATION.value,
                    "step_order": STEP_ORDER[StepName.METHODOLOGY_CLAIM_MATERIALIZATION],
                },
            )
            assert changed.rowcount == 1
            return canonical_step_id
    raise AssertionError(f"unknown Step-7 test condition: {condition}")


def _restore_canonical_step(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    run_id: str,
    canonical_step_id: str,
) -> None:
    step_name, step_order = _canonical_step_values()
    with database.admin_engine.begin() as connection:
        set_seed_tenant(connection, tenant_id)
        changed = _corrupt_canonical_step(
            connection,
            """
            UPDATE run_steps
            SET step_name = :step_name, step_order = :step_order,
                status = 'RUNNING', finished_at = NULL,
                error_code = NULL, error_message = NULL
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND run_id = CAST(:run_id AS uuid)
              AND step_id = CAST(:step_id AS uuid)
            """,
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "step_id": canonical_step_id,
                "step_name": step_name,
                "step_order": step_order,
            },
        )
        assert changed.rowcount == 1


def _safe_state(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    run_id: str,
) -> dict[str, tuple[str, ...]]:
    statements = {
        "attempts": """
            SELECT row_to_json(a)::text
            FROM methodology_extraction_attempts AS a
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND run_id = CAST(:run_id AS uuid)
            ORDER BY attempt_id
        """,
        "results": """
            SELECT row_to_json(r)::text
            FROM methodology_extraction_results AS r
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND run_id = CAST(:run_id AS uuid)
            ORDER BY result_id
        """,
        "budget": """
            SELECT row_to_json(b)::text
            FROM provider_budget_usage AS b
            WHERE tenant_id = CAST(:tenant_id AS uuid)
            ORDER BY provider
        """,
        "steps": """
            SELECT row_to_json(s)::text
            FROM run_steps AS s
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND run_id = CAST(:run_id AS uuid)
            ORDER BY step_order, step_id
        """,
        "coverage": """
            SELECT row_to_json(c)::text
            FROM methodology_coverage_records AS c
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND run_id = CAST(:run_id AS uuid)
            ORDER BY coverage_record_id
        """,
    }
    with database.admin_engine.connect() as connection:
        return {
            name: tuple(
                str(row[0])
                for row in connection.execute(
                    text(statement), {"tenant_id": tenant_id, "run_id": run_id}
                )
            )
            for name, statement in statements.items()
        }


def _hermetic_success_values() -> tuple[ValidatedDispatchedMethodologyResult, str]:
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
            "accepted_source_span_ids": [pg.SPAN_ID],
            "accepted_locator_sha256s": [pg.LOCATOR_HASH],
        }
    )
    searched_scope_sha256 = compute_methodology_searched_scope_sha256(
        (
            MethodologyCandidateSpan(
                document_id=pg.DOCUMENT_ID,
                span_id=pg.SPAN_ID,
                ordinal=1,
                content_sha256=pg.SOURCE_CONTENT_SHA256,
                locator_sha256=pg.LOCATOR_HASH,
                text=pg.SOURCE_TEXT,
            ),
        )
    )
    result = ValidatedDispatchedMethodologyResult.from_terminal_payload(
        dispatched_call_sha256=pg.HASH_B,
        task_input_sha256=pg.HASH_A,
        outcome="EVIDENCE_FOUND",
        searched_document_ids=(pg.DOCUMENT_ID,),
        searched_span_ids=(pg.SPAN_ID,),
        searched_scope_sha256=searched_scope_sha256,
        factual_outputs=(factual_output,),
        gap_finding=None,
    )
    receipt_fields: dict[str, object] = {
        "provider": "anthropic",
        "backend": "hermetic",
        "configured_model": "fixture-model",
        "observed_model": "fixture-model",
        "prompt_id": "METHODOLOGY_QUESTION_EXTRACTION_V1",
        "prompt_version": "1.0.0",
        "prompt_content_sha256": pg.HASH_A,
        "prompt_input_schema_sha256": pg.HASH_A,
        "prompt_output_schema_sha256": pg.HASH_A,
        "request_payload_sha256": pg.HASH_A,
        "provider_request_id": "hermetic-fixture",
        "provider_response_sha256": pg.HASH_A,
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
        "billable_payload_sha256": pg.HASH_A,
        "billable_payload_utf8_bytes": 1,
        "price_catalog_version": "fixture-price-v1",
        "price_catalog_row_sha256": pg.HASH_A,
        "input_usd_per_million_tokens": "0.00000000",
        "output_usd_per_million_tokens": "0.00000000",
        "reserved_calls": 0,
        "reserved_input_tokens": 0,
        "reserved_output_tokens": 0,
        "reserved_embedding_tokens": 0,
        "reserved_usd": "0.00000000",
        "budget_terminal_state": "NOT_BILLABLE",
    }
    return result, compute_methodology_provider_receipt_sha256(
        receipt_fields=receipt_fields,
        validated_result_sha256=result.validated_result_sha256,
    )


def _invoke_repository_boundary(
    repository: PostgresMethodologyExtractionRepository,
    boundary: str,
    *,
    run_step_id: str,
) -> None:
    common = {
        "deal_id": pg.DEAL_ID,
        "run_id": pg.RUN_ID,
        "run_step_id": run_step_id,
        "lease_owner_id": pg.LEASE_OWNER_ID,
        "lease_fencing_token": 7,
    }
    if boundary == "admit_attempt":
        repository.admit_attempt(
            **common,
            task_record_id=pg.READY_TASK_ID,
            task_input_sha256=pg.HASH_A,
            prepared_call_sha256=pg.HASH_B,
            provider="anthropic",
            configured_model="fixture-model",
            prompt_id="METHODOLOGY_QUESTION_EXTRACTION_V1",
            prompt_content_sha256=pg.HASH_A,
            prompt_input_schema_sha256=pg.HASH_A,
            prompt_output_schema_sha256=pg.HASH_A,
            request_payload_sha256=pg.HASH_A,
            billable_payload_sha256=pg.HASH_A,
            billable_payload_utf8_bytes=1,
            configured_max_tokens=1,
            configured_temperature="0",
            configured_json_mode=True,
            budget_policy_id="fixture-policy",
            budget_reservation_id=pg.READY_RESERVATION_ID,
            backend="hermetic",
            transport_kind="HERMETIC_TEST",
            price_catalog_version="fixture-price-v1",
            price_catalog_row_sha256=pg.HASH_A,
            reserved_calls=0,
            reserved_input_tokens=0,
            reserved_output_tokens=0,
            reserved_usd="0",
        )
        return
    if boundary == "finalize_success":
        result, receipt_sha256 = _hermetic_success_values()
        repository.finalize_attempt_success(
            **common,
            attempt_id=pg.ATTEMPT_ID,
            observed_model="fixture-model",
            provider_request_id="hermetic-fixture",
            provider_response_sha256=pg.HASH_A,
            dispatched_call_sha256=pg.HASH_B,
            validated_result_sha256=result.validated_result_sha256,
            prospective_receipt_sha256=receipt_sha256,
            budget_policy_id="fixture-policy",
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            stop_reason="end_turn",
            result=result,
        )
        return
    if boundary == "finalize_ordinary_failure":
        repository.finalize_attempt_failure(
            **common,
            attempt_id=pg.ATTEMPT_ID,
            budget_reservation_id="20202020-2020-4202-8202-202020202020",
            budget_policy_id="fixture-policy",
            safe_failure_code="METHODOLOGY_PROVIDER_RESULT_INVALID",
        )
        return
    if boundary == "finalize_received_response_invalid":
        repository.finalize_received_response_failure(
            **common,
            attempt_id=pg.ATTEMPT_ID,
            budget_reservation_id="20202020-2020-4202-8202-202020202020",
            budget_policy_id=pg.POLICY_HASH,
            safe_failure_code="METHODOLOGY_PROVIDER_RESULT_INVALID",
            observed_model="claude-sonnet-4-20250514",
            provider_request_id="live-invalid-fixture-request",
            provider_response_sha256=pg.HASH_A,
            dispatched_call_sha256=pg.HASH_B,
            latency_ms=17,
            input_tokens=80,
            output_tokens=10,
            stop_reason="end_turn",
        )
        return
    if boundary == "mark_outcome_unknown":
        repository.mark_attempt_outcome_unknown(
            **common,
            attempt_id=pg.ATTEMPT_ID,
        )
        return
    raise AssertionError(f"unknown Task-6 repository boundary: {boundary}")


def _prepare_repository_boundary(
    database: DisposablePostgres,
    boundary: str,
) -> None:
    pg._seed_task6_dependencies(database)
    if boundary == "admit_attempt":
        pg._insert_ready_task_without_attempt(database)
        return
    pg._insert_task6_graph(
        database,
        started_attempt_only=True,
        live_attempt=boundary == "finalize_received_response_invalid",
    )


@pytest.mark.parametrize("condition", STEP_CONDITIONS)
@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_every_attempt_result_boundary_requires_the_exact_running_canonical_step(
    boundary: str,
    condition: str,
) -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        if boundary == "complete_exact_set":
            selection_id = coverage._seed_selected_coverage(database)
            seeded = coverage._seed_terminal_tasks(database, selection_id)
            run_step_id = _apply_step_condition(
                database,
                condition,
                tenant_id=coverage.TENANT_ID,
                run_id=coverage.RUN_ID,
                canonical_step_id=coverage.RUN_STEP_ID,
            )
            before = _safe_state(database, tenant_id=coverage.TENANT_ID, run_id=coverage.RUN_ID)
            with (
                pytest.raises(MethodologyExtractionRepositoryError) as exc_info,
                database.app_engine.begin() as connection,
            ):
                set_tenant_context(connection, coverage.TENANT_ID)
                PostgresMethodologyExtractionRepository(
                    connection, coverage.TENANT_ID
                ).complete_execution_step_exact_set(
                    deal_id=coverage.DEAL_ID,
                    run_id=coverage.RUN_ID,
                    run_step_id=run_step_id,
                    expected_task_ids=seeded.task_ids,
                    lease_owner_id=coverage.LEASE_OWNER_ID,
                    lease_fencing_token=1,
                )
            assert exc_info.value.code == WORKER_LEASE_FENCED
            assert (
                _safe_state(database, tenant_id=coverage.TENANT_ID, run_id=coverage.RUN_ID)
                == before
            )
            return

        _prepare_repository_boundary(database, boundary)
        run_step_id = _apply_step_condition(
            database,
            condition,
            tenant_id=pg.TENANT_A,
            run_id=pg.RUN_ID,
            canonical_step_id=pg.STEP_ID,
        )
        before = _safe_state(database, tenant_id=pg.TENANT_A, run_id=pg.RUN_ID)
        with (
            pytest.raises(MethodologyExtractionRepositoryError) as exc_info,
            database.app_engine.begin() as connection,
        ):
            set_tenant_context(connection, pg.TENANT_A)
            _invoke_repository_boundary(
                PostgresMethodologyExtractionRepository(connection, pg.TENANT_A),
                boundary,
                run_step_id=run_step_id,
            )
        assert exc_info.value.code == WORKER_LEASE_FENCED
        assert _safe_state(database, tenant_id=pg.TENANT_A, run_id=pg.RUN_ID) == before


@pytest.mark.parametrize("condition", STEP_CONDITIONS)
def test_attempt_guard_rejects_direct_insert_for_noncanonical_step(condition: str) -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        pg._seed_task6_dependencies(database)
        run_step_id = _apply_step_condition(
            database,
            condition,
            tenant_id=pg.TENANT_A,
            run_id=pg.RUN_ID,
            canonical_step_id=pg.STEP_ID,
        )
        before = _safe_state(database, tenant_id=pg.TENANT_A, run_id=pg.RUN_ID)

        with pytest.raises(DBAPIError) as exc_info:
            pg._insert_task6_graph(
                database,
                started_attempt_only=True,
                run_step_id=run_step_id,
            )

        assert getattr(exc_info.value.orig, "pgcode", None) == "40001"
        assert WORKER_LEASE_FENCED in str(exc_info.value.orig)
        assert _safe_state(database, tenant_id=pg.TENANT_A, run_id=pg.RUN_ID) == before


@pytest.mark.parametrize("condition", ("canonical_completed", "wrong_name_order"))
def test_attempt_guard_rejects_direct_terminal_update_for_noncanonical_step(
    condition: str,
) -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        pg._seed_task6_dependencies(database)
        pg._insert_task6_graph(database, started_attempt_only=True)
        _apply_step_condition(
            database,
            condition,
            tenant_id=pg.TENANT_A,
            run_id=pg.RUN_ID,
            canonical_step_id=pg.STEP_ID,
        )
        before = _safe_state(database, tenant_id=pg.TENANT_A, run_id=pg.RUN_ID)

        with (
            pytest.raises(DBAPIError) as exc_info,
            database.app_engine.begin() as connection,
        ):
            set_tenant_context(connection, pg.TENANT_A)
            pg._set_active_task6_write_context(connection)
            connection.execute(
                text(
                    """
                    UPDATE methodology_extraction_attempts
                    SET status = 'OUTCOME_UNKNOWN',
                        completed_at = statement_timestamp(),
                        safe_failure_code = 'METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN'
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND attempt_id = CAST(:attempt_id AS uuid)
                    """
                ),
                {"tenant_id": pg.TENANT_A, "attempt_id": pg.ATTEMPT_ID},
            )

        assert getattr(exc_info.value.orig, "pgcode", None) == "40001"
        assert WORKER_LEASE_FENCED in str(exc_info.value.orig)
        assert _safe_state(database, tenant_id=pg.TENANT_A, run_id=pg.RUN_ID) == before


def test_fixture_and_repository_share_the_code_owned_step7_identity() -> None:
    assert _canonical_step_values() == (
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION",
        6,
    )
    assert isinstance(STEP_ORDER, Mapping)
