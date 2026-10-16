"""Disposable-Postgres proof for confirmed provider nonacceptance refunds."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from sqlalchemy import text

from idis.models.methodology_extraction import MethodologyCallAdmissionProof
from idis.services.extraction.methodology_task_extractor import (
    ANTHROPIC_STANDARD_PRICE_CATALOG,
    AnthropicMethodologyTaskExtractor,
    AnthropicSDKMethodologyTransport,
    MethodologyProviderRequestNotAccepted,
)
from idis.services.runs.methodology_extraction_task_execution import (
    DurableMethodologyExtractionExecutionError,
    DurableRunMethodologyExtractionTaskExecutionService,
)
from idis.services.runs.methodology_postgres_runtime import MethodologyExecutionInput
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

_PRIVATE_ERROR_SENTINEL = "private-provider-error-must-not-persist"


def _sdk_extractor_for_error(error_factory: Any) -> tuple[Any, Any]:
    class RaisingMessages:
        def create(self, **_kwargs: Any) -> Any:
            raise error_factory()

    transport = AnthropicSDKMethodologyTransport(
        api_key="fictional-test-key",
        client_factory=lambda **_kwargs: SimpleNamespace(messages=RaisingMessages()),
    )
    extractor = AnthropicMethodologyTaskExtractor(
        transport=transport,
        prompt_artifact=_prompt(),
        configured_model=_MODEL_ID,
        max_tokens=512,
        temperature="0",
        price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
    )
    return extractor, transport


def _anthropic_status_error(status_code: int) -> Exception:
    http_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    http_response = httpx.Response(
        status_code,
        request=http_request,
        headers={"request-id": "req_fictional_failure_0001"},
        json={
            "type": "error",
            "error": {
                "type": "invalid_request_error" if status_code == 400 else "api_error",
                "message": _PRIVATE_ERROR_SENTINEL,
            },
        },
    )
    error_type = anthropic.BadRequestError if status_code == 400 else anthropic.APIStatusError
    return error_type(
        _PRIVATE_ERROR_SENTINEL,
        response=http_response,
        body=http_response.json(),
    )


def _hermetic_admission(extractor: Any, request: Any) -> tuple[Any, Any]:
    prepared = extractor.prepare(request, budget_policy_id=POLICY_HASH)
    return prepared, MethodologyCallAdmissionProof(
        backend="hermetic",
        committed=True,
        attempt_id="18181818-1818-4181-8181-181818181818",
        budget_reservation_id=prepared.budget_reservation_id,
        prepared_call_sha256=prepared.prepared_call_sha256,
        task_input_sha256=request.task_input_sha256,
        price_catalog_row_sha256=prepared.reservation.price_catalog_row_sha256,
        worker_fencing_token=7,
        budget_policy_id=POLICY_HASH,
    )


class _TypedDispatchFailureExtractor:
    external_calls_enabled = False

    def __init__(self, base_extractor: Any, error_factory: Any) -> None:
        self._base_extractor = base_extractor
        self._error_factory = error_factory

    def prepare(self, request: Any, *, budget_policy_id: str) -> Any:
        return self._base_extractor.prepare(request, budget_policy_id=budget_policy_id)

    def dispatch(self, prepared: Any, *, admission: Any) -> Any:
        assert admission.committed is True
        raise self._error_factory()


def test_injected_sdk_confirmed_nonacceptance_is_typed_without_live_claim() -> None:
    request = _request()
    extractor, transport = _sdk_extractor_for_error(lambda: _anthropic_status_error(400))
    prepared, admission = _hermetic_admission(extractor, request)

    assert transport.backend == "hermetic"
    with pytest.raises(MethodologyProviderRequestNotAccepted) as raised:
        extractor.dispatch(prepared, admission=admission)
    assert raised.value.provider_status_code == 400


def test_typed_confirmed_nonacceptance_atomically_refunds_and_fails() -> None:
    request = _request()
    base_extractor, transport = _sdk_extractor_for_error(
        lambda: AssertionError("durable typed-port test must not invoke an SDK client")
    )
    assert transport.backend == "hermetic"
    extractor = _TypedDispatchFailureExtractor(
        base_extractor,
        lambda: MethodologyProviderRequestNotAccepted(provider_status_code=400),
    )
    execution_input = MethodologyExecutionInput(
        task_record_id=READY_TASK_ID,
        extraction_task_id=_EXTRACTION_TASK_ID,
        methodology_question_record_id=(request.methodology_question_record_id),
        request=request,
    )
    policy = _verified_policy()

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
        boundary = _PinnedPolicyPostgresBoundary(
            connection_factory=database.app_engine.begin,
            execution_input=execution_input,
            policy=policy,
        )

        with pytest.raises(DurableMethodologyExtractionExecutionError) as raised:
            DurableRunMethodologyExtractionTaskExecutionService(
                extractor=extractor,
                boundary=boundary,
            ).run(
                tenant_id=TENANT_A,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=STEP_ID,
                tasks=[SimpleNamespace(extraction_task_id=_EXTRACTION_TASK_ID)],
                documents=[],
            )

        assert raised.value.code == "METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED"
        with database.admin_engine.connect() as connection:
            attempt = connection.execute(
                text(
                    """
                    SELECT a.status, a.budget_terminal_state, a.safe_failure_code,
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
            budget = connection.execute(
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
            audit_types = set(
                connection.execute(
                    text(
                        "SELECT event_type FROM audit_events "
                        "WHERE tenant_id=CAST(:tenant_id AS uuid)"
                    ),
                    {"tenant_id": TENANT_A},
                ).scalars()
            )
            leaked_error_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND event::text LIKE :needle"
                ),
                {"tenant_id": TENANT_A, "needle": f"%{_PRIVATE_ERROR_SENTINEL}%"},
            ).scalar_one()

        assert attempt.status == "FAILED"
        assert attempt.budget_terminal_state == "REFUNDED"
        assert attempt.safe_failure_code == "METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED"
        assert attempt.step_status == "FAILED"
        assert attempt.observed_model is None
        assert attempt.provider_request_id is None
        assert attempt.provider_response_sha256 is None
        assert attempt.input_tokens is None
        assert attempt.output_tokens is None
        assert attempt.stop_reason is None
        assert budget.used == 0
        assert budget.reserved_calls == 0
        assert budget.reserved_usd == 0
        assert budget.reserved_input_tokens == 0
        assert budget.reserved_output_tokens == 0
        assert budget.consumed_usd == 0
        assert budget.consumed_input_tokens == 0
        assert budget.consumed_output_tokens == 0
        assert task6_output_count == 0
        assert {
            "integration.provider_budget.reserved",
            "integration.provider_budget.refunded",
            "deal.run.methodology_attempt.admitted",
            "deal.run.methodology_attempt.provider_request_not_accepted",
        }.issubset(audit_types)
        assert "integration.provider_budget.consumed" not in audit_types
        assert leaked_error_count == 0


@pytest.mark.parametrize("provider_status_code", (408, 500))
def test_injected_sdk_ambiguous_status_propagates_without_live_claim(
    provider_status_code: int,
) -> None:
    request = _request()
    extractor, transport = _sdk_extractor_for_error(
        lambda: _anthropic_status_error(provider_status_code)
    )
    prepared, admission = _hermetic_admission(extractor, request)

    assert transport.backend == "hermetic"
    with pytest.raises(anthropic.APIStatusError) as raised:
        extractor.dispatch(prepared, admission=admission)
    assert raised.value.status_code == provider_status_code


@pytest.mark.parametrize("provider_status_code", (408, 500))
def test_typed_ambiguous_failure_retains_reservation_and_marks_unknown(
    provider_status_code: int,
) -> None:
    request = _request()
    base_extractor, transport = _sdk_extractor_for_error(
        lambda: AssertionError("durable typed-port test must not invoke an SDK client")
    )
    assert transport.backend == "hermetic"
    extractor = _TypedDispatchFailureExtractor(
        base_extractor,
        lambda: RuntimeError(f"ambiguous provider status {provider_status_code}"),
    )
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
        boundary = _PinnedPolicyPostgresBoundary(
            connection_factory=database.app_engine.begin,
            execution_input=execution_input,
            policy=_verified_policy(),
        )

        with pytest.raises(DurableMethodologyExtractionExecutionError) as raised:
            DurableRunMethodologyExtractionTaskExecutionService(
                extractor=extractor,
                boundary=boundary,
            ).run(
                tenant_id=TENANT_A,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                run_step_id=STEP_ID,
                tasks=[SimpleNamespace(extraction_task_id=_EXTRACTION_TASK_ID)],
                documents=[],
            )

        assert raised.value.code == "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN"
        with database.admin_engine.connect() as connection:
            attempt = connection.execute(
                text(
                    """
                    SELECT a.status, a.budget_terminal_state, a.safe_failure_code,
                           s.status AS step_status,
                           (SELECT COUNT(*) FROM methodology_extraction_results r
                            WHERE r.tenant_id=a.tenant_id
                              AND r.attempt_id=a.attempt_id) AS result_count
                    FROM methodology_extraction_attempts a
                    JOIN run_steps s
                      ON s.tenant_id=a.tenant_id AND s.step_id=a.run_step_id
                    WHERE a.tenant_id=CAST(:tenant_id AS uuid)
                      AND a.task_record_id=CAST(:task_record_id AS uuid)
                    """
                ),
                {"tenant_id": TENANT_A, "task_record_id": READY_TASK_ID},
            ).one()
            budget = connection.execute(
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
            audit_types = set(
                connection.execute(
                    text(
                        "SELECT event_type FROM audit_events "
                        "WHERE tenant_id=CAST(:tenant_id AS uuid)"
                    ),
                    {"tenant_id": TENANT_A},
                ).scalars()
            )
            leaked_error_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND event::text LIKE :needle"
                ),
                {"tenant_id": TENANT_A, "needle": f"%{_PRIVATE_ERROR_SENTINEL}%"},
            ).scalar_one()

        assert attempt.status == "OUTCOME_UNKNOWN"
        assert attempt.budget_terminal_state == "RESERVED"
        assert attempt.safe_failure_code == "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN"
        assert attempt.step_status == "FAILED"
        assert attempt.result_count == 0
        assert budget.used == 0
        assert budget.reserved_calls == 1
        assert budget.reserved_usd > 0
        assert budget.reserved_input_tokens > 0
        assert budget.reserved_output_tokens == 512
        assert budget.consumed_usd == 0
        assert budget.consumed_input_tokens == 0
        assert budget.consumed_output_tokens == 0
        assert {
            "integration.provider_budget.reserved",
            "deal.run.methodology_attempt.admitted",
            "deal.run.methodology_attempt.outcome_unknown",
        }.issubset(audit_types)
        assert "integration.provider_budget.refunded" not in audit_types
        assert "integration.provider_budget.consumed" not in audit_types
        assert leaked_error_count == 0
