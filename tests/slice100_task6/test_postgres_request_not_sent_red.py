"""Disposable-Postgres proof that a pre-send methodology failure refunds."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from idis.services.extraction.methodology_task_extractor import (
    ANTHROPIC_STANDARD_PRICE_CATALOG,
    AnthropicMethodologyTaskExtractor,
    AnthropicSDKMethodologyTransport,
    MethodologyRequestNotSent,
)
from idis.services.runs.methodology_extraction_task_execution import (
    DurableMethodologyExtractionExecutionError,
    DurableRunMethodologyExtractionTaskExecutionService,
)
from idis.services.runs.methodology_postgres_runtime import (
    MethodologyExecutionInput,
    MethodologyPostgresRuntimeError,
)
from tests.slice100_task2.test_governed_budget_postgres import _insert_policy
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task6.test_postgres_0037_red import (
    DEAL_ID,
    POLICY_HASH,
    READY_TASK_ID,
    RUN_ID,
    STEP_ID,
    TENANT_A,
    _insert_ready_task_without_attempt,
    _seed_task6_dependencies,
)
from tests.slice100_task6.test_postgres_sdk_received_invalid_composition_red import (
    _EXTRACTION_TASK_ID,
    _MODEL_ID,
    _PinnedPolicyPostgresBoundary,
    _prompt,
    _request,
    _verified_policy,
)

_PRIVATE_ERROR_SENTINEL = "private-local-failure-must-not-persist"
_NOT_SENT_EVENT = "deal.run.methodology_attempt.request_not_sent"
_SAFE_AUDIT_FIELDS = {
    "attempt_id",
    "run_step_id",
    "code",
    "budget_terminal_state",
    "request_not_sent_reason",
    "request_not_sent_proof_kind",
}


class _RequestNotSentExtractor:
    def __init__(self, base_extractor: Any) -> None:
        self._base_extractor = base_extractor
        self.dispatches = 0

    def prepare(self, request: Any, *, budget_policy_id: str) -> Any:
        return self._base_extractor.prepare(request, budget_policy_id=budget_policy_id)

    def dispatch(self, prepared: Any, *, admission: Any) -> Any:
        assert admission.committed is True
        assert prepared is not None
        self.dispatches += 1
        try:
            raise RuntimeError(_PRIVATE_ERROR_SENTINEL)
        except RuntimeError:
            error = MethodologyRequestNotSent(
                code="METHODOLOGY_SDK_LOCAL_GUARD",
                reason="SDK_LOCAL_GUARD",
            )
        raise error from None


def _base_extractor() -> AnthropicMethodologyTaskExtractor:
    def forbid_create(**_kwargs: Any) -> Any:
        raise AssertionError("postgres proof must not call the sdk")

    transport = AnthropicSDKMethodologyTransport(
        api_key="fictional-test-key",
        client_factory=lambda **_kwargs: SimpleNamespace(
            messages=SimpleNamespace(create=forbid_create)
        ),
    )
    return AnthropicMethodologyTaskExtractor(
        transport=transport,
        prompt_artifact=_prompt(),
        configured_model=_MODEL_ID,
        max_tokens=512,
        temperature="0",
        price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
    )


def _budget_totals(connection: Any) -> tuple[Any, ...] | None:
    row = connection.execute(
        text(
            """
            SELECT used, reserved_calls, reserved_usd,
                   reserved_input_tokens, reserved_output_tokens,
                   consumed_usd, consumed_input_tokens, consumed_output_tokens
            FROM provider_budget_usage
            WHERE tenant_id=CAST(:tenant_id AS uuid)
              AND provider='anthropic'
            """
        ),
        {"tenant_id": TENANT_A},
    ).one_or_none()
    if row is None:
        return None
    return tuple(row)


def test_request_not_sent_refunds_and_second_run_does_not_dispatch() -> None:
    request = _request()
    extractor = _RequestNotSentExtractor(_base_extractor())
    execution_input = MethodologyExecutionInput(
        task_record_id=READY_TASK_ID,
        extraction_task_id=_EXTRACTION_TASK_ID,
        methodology_question_record_id=request.methodology_question_record_id,
        request=request,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_policy(
            database.admin_engine,
            tenant_id=TENANT_A,
            policy_hash=POLICY_HASH,
        )
        _insert_ready_task_without_attempt(database)
        with database.admin_engine.connect() as connection:
            before_budget = _budget_totals(connection)
        boundary = _PinnedPolicyPostgresBoundary(
            connection_factory=database.app_engine.begin,
            execution_input=execution_input,
            policy=_verified_policy(),
        )
        service = DurableRunMethodologyExtractionTaskExecutionService(
            extractor=extractor,
            boundary=boundary,
        )

        with pytest.raises(DurableMethodologyExtractionExecutionError) as raised:
            service.run(
                tenant_id=TENANT_A,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=STEP_ID,
                tasks=[SimpleNamespace(extraction_task_id=_EXTRACTION_TASK_ID)],
                documents=[],
            )

        assert raised.value.code == "METHODOLOGY_REQUEST_NOT_SENT"
        assert extractor.dispatches == 1
        with database.admin_engine.connect() as connection:
            attempt = connection.execute(
                text(
                    """
                    SELECT a.status, a.budget_terminal_state, a.safe_failure_code,
                           a.attempt_id::text AS attempt_id,
                           a.observed_model, a.provider_request_id,
                           a.provider_response_sha256, a.input_tokens,
                           a.output_tokens, a.stop_reason,
                           s.status AS step_status
                    FROM methodology_extraction_attempts a
                    JOIN run_steps s
                      ON s.tenant_id=a.tenant_id AND s.step_id=a.run_step_id
                    WHERE a.tenant_id=CAST(:tenant_id AS uuid)
                      AND a.task_record_id=CAST(:task_record_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "task_record_id": READY_TASK_ID},
            ).one()
            after_budget = connection.execute(
                text(
                    """
                    SELECT used, reserved_calls, reserved_usd,
                           reserved_input_tokens, reserved_output_tokens,
                           consumed_usd, consumed_input_tokens,
                           consumed_output_tokens
                    FROM provider_budget_usage
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND provider='anthropic'
                    """
                ),
                {"tenant_id": TENANT_A},
            ).one()
            task6_output_count = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM methodology_extraction_results
                       WHERE tenant_id=CAST(:tenant_id AS uuid)
                         AND run_id=CAST(:run_id AS uuid))
                    + (SELECT COUNT(*) FROM methodology_result_factual_outputs
                       WHERE tenant_id=CAST(:tenant_id AS uuid)
                         AND run_id=CAST(:run_id AS uuid))
                    + (SELECT COUNT(*) FROM methodology_result_claim_links
                       WHERE tenant_id=CAST(:tenant_id AS uuid)
                         AND run_id=CAST(:run_id AS uuid))
                    + (SELECT COUNT(*) FROM methodology_result_evidence_links
                       WHERE tenant_id=CAST(:tenant_id AS uuid)
                         AND run_id=CAST(:run_id AS uuid))
                    + (SELECT COUNT(*) FROM methodology_result_sanad_links
                       WHERE tenant_id=CAST(:tenant_id AS uuid)
                         AND run_id=CAST(:run_id AS uuid))
                    + (SELECT COUNT(*) FROM methodology_result_defect_links
                       WHERE tenant_id=CAST(:tenant_id AS uuid)
                         AND run_id=CAST(:run_id AS uuid))
                    """
                ),
                {"tenant_id": TENANT_A, "run_id": RUN_ID},
            ).scalar_one()
            audit_event = connection.execute(
                text(
                    """
                    SELECT event FROM audit_events
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND event_type=:event_type
                    """
                ),
                {"tenant_id": TENANT_A, "event_type": _NOT_SENT_EVENT},
            ).scalar_one()
            leaked_error_count = connection.execute(
                text(
                    """
                    SELECT COUNT(*) FROM audit_events
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND event::text LIKE :needle
                    """
                ),
                {"tenant_id": TENANT_A, "needle": f"%{_PRIVATE_ERROR_SENTINEL}%"},
            ).scalar_one()
            audit_types = set(
                connection.execute(
                    text(
                        "SELECT event_type FROM audit_events "
                        "WHERE tenant_id=CAST(:tenant_id AS uuid)"
                    ),
                    {"tenant_id": TENANT_A},
                ).scalars()
            )
            attempt_count = connection.execute(
                text(
                    """
                    SELECT COUNT(*) FROM methodology_extraction_attempts
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND run_id=CAST(:run_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "run_id": RUN_ID},
            ).scalar_one()

        assert attempt.status == "FAILED"
        assert attempt.budget_terminal_state == "REFUNDED"
        assert attempt.safe_failure_code == "METHODOLOGY_REQUEST_NOT_SENT"
        assert attempt.step_status == "FAILED"
        assert attempt.observed_model is None
        assert attempt.provider_request_id is None
        assert attempt.provider_response_sha256 is None
        assert attempt.input_tokens is None
        assert attempt.output_tokens is None
        assert attempt.stop_reason is None
        assert attempt_count == 1
        assert task6_output_count == 0
        assert after_budget.used == 0
        assert after_budget.reserved_calls == 0
        assert after_budget.reserved_usd == 0
        assert after_budget.reserved_input_tokens == 0
        assert after_budget.reserved_output_tokens == 0
        assert after_budget.consumed_usd == 0
        assert after_budget.consumed_input_tokens == 0
        assert after_budget.consumed_output_tokens == 0
        if before_budget is None:
            assert tuple(after_budget) == (0, 0, 0, 0, 0, 0, 0, 0)
        else:
            assert tuple(after_budget) == before_budget
        event = audit_event if isinstance(audit_event, dict) else json.loads(audit_event)
        assert set(event["payload"]["safe"]) == _SAFE_AUDIT_FIELDS
        assert event["payload"]["safe"]["attempt_id"] == attempt.attempt_id
        assert event["payload"]["safe"]["run_step_id"] == STEP_ID
        assert event["payload"]["safe"]["code"] == "METHODOLOGY_REQUEST_NOT_SENT"
        assert event["payload"]["safe"]["budget_terminal_state"] == "REFUNDED"
        assert event["payload"]["safe"]["request_not_sent_reason"] == "SDK_LOCAL_GUARD"
        assert event["payload"]["safe"]["request_not_sent_proof_kind"] == ("PRE_SEND_LOCAL_FAILURE")
        assert event["request"]["status_code"] == 409
        assert _PRIVATE_ERROR_SENTINEL not in json.dumps(event)
        assert "Governed fictional methodology extraction prompt." not in json.dumps(event)
        assert leaked_error_count == 0
        assert {
            "integration.provider_budget.reserved",
            "integration.provider_budget.refunded",
            "deal.run.methodology_attempt.admitted",
            _NOT_SENT_EVENT,
        }.issubset(audit_types)
        assert "integration.provider_budget.consumed" not in audit_types

        with pytest.raises(MethodologyPostgresRuntimeError) as second:
            service.run(
                tenant_id=TENANT_A,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=STEP_ID,
                tasks=[SimpleNamespace(extraction_task_id=_EXTRACTION_TASK_ID)],
                documents=[],
            )

        assert second.value.code == "METHODOLOGY_REQUEST_NOT_SENT"
        assert extractor.dispatches == 1
        with database.admin_engine.connect() as connection:
            attempt_count_after = connection.execute(
                text(
                    """
                    SELECT COUNT(*) FROM methodology_extraction_attempts
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND run_id=CAST(:run_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "run_id": RUN_ID},
            ).scalar_one()
            not_sent_events = connection.execute(
                text(
                    """
                    SELECT COUNT(*) FROM audit_events
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND event_type=:event_type
                    """
                ),
                {"tenant_id": TENANT_A, "event_type": _NOT_SENT_EVENT},
            ).scalar_one()
        assert attempt_count_after == 1
        assert not_sent_events == 1
