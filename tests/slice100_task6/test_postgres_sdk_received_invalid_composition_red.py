"""Hermetic SDK validation plus durable rejection of false-live composition."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from idis.audit.postgres_sink import PostgresAuditSink
from idis.models.methodology_extraction import (
    MethodologyCallAdmissionProof,
    MethodologyCandidateSpan,
    MethodologyPromptArtifact,
    MethodologyTaskExtractorRequest,
    canonical_json_bytes,
    compute_methodology_searched_scope_sha256,
    exact_canonical_json_bytes,
)
from idis.persistence.repositories.provider_budget_policies import (
    VerifiedActiveProviderBudgetPolicy,
)
from idis.services.extraction.methodology_task_extractor import (
    ANTHROPIC_STANDARD_PRICE_CATALOG,
    AnthropicMethodologyTaskExtractor,
    AnthropicSDKMethodologyTransport,
    MethodologyExtractionValidationError,
)
from idis.services.runs.methodology_extraction_task_execution import (
    DurableMethodologyExtractionExecutionError,
    DurableRunMethodologyExtractionTaskExecutionService,
)
from idis.services.runs.methodology_postgres_runtime import (
    MethodologyExecutionInput,
    PostgresMethodologyExecutionBoundary,
)
from tests.slice100_task2._support import budget_payload
from tests.slice100_task2.test_governed_budget_postgres import _insert_policy
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task6._support import CDD_SOURCE_SHA256, TASK6_SCHEMA_HEAD
from tests.slice100_task6.test_postgres_0037_red import (
    DEAL_ID,
    DOCUMENT_ID,
    HASH_A,
    HASH_B,
    LEASE_OWNER_ID,
    LOCATOR_HASH,
    POLICY_HASH,
    READY_TASK_ID,
    RUN_ID,
    SPAN_ID,
    STEP_ID,
    TENANT_A,
    _insert_ready_task_without_attempt,
    _seed_task6_dependencies,
)

_EXTRACTION_TASK_ID = "et_task6_budget_lineage_fixture"
_QUESTION_RECORD_ID = "17171717-1717-4171-8171-171717171717"
_MODEL_ID = "claude-sonnet-4-20250514"


class _PinnedPolicyPostgresBoundary(PostgresMethodologyExecutionBoundary):
    """Use production PostgreSQL mutations with a test-signed policy projection."""

    def __init__(
        self,
        *,
        connection_factory: Any,
        execution_input: MethodologyExecutionInput,
        policy: VerifiedActiveProviderBudgetPolicy,
    ) -> None:
        self._connection_factory = connection_factory
        self._tenant_id = TENANT_A
        self._lease_owner_id = LEASE_OWNER_ID
        self._lease_fencing_token = 7
        self._audit_sink = PostgresAuditSink()
        self._budget_runtime = SimpleNamespace(global_max_calls=50)
        self._execution_input = execution_input
        self._execution_inputs: tuple[MethodologyExecutionInput, ...] = ()
        self._pinned_policy = policy

    def _load_policy(self, _connection: Any) -> VerifiedActiveProviderBudgetPolicy:
        return self._pinned_policy

    def load_execution_inputs(self, **values: Any) -> list[MethodologyExecutionInput]:
        assert values["tenant_id"] == TENANT_A
        assert values["deal_id"] == DEAL_ID
        assert values["run_id"] == RUN_ID
        self._execution_inputs = (self._execution_input,)
        return [self._execution_input]


def _verified_policy() -> VerifiedActiveProviderBudgetPolicy:
    payload = budget_payload()
    component = dict(payload["components"]["anthropic"])
    return VerifiedActiveProviderBudgetPolicy(
        tenant_id=TENANT_A,
        policy_version=str(payload["policy_version"]),
        policy_hash=POLICY_HASH,
        signer_id="slice100-governance-key-v1",
        signature_verified=True,
        currency="USD",
        total_max_usd=Decimal("0.50000000"),
        components=MappingProxyType({"anthropic": MappingProxyType(component)}),
        imported_by_actor_id="slice100-governance-admin",
        envelope_hash="b" * 64,
        payload_hash=POLICY_HASH,
        public_key_hash="c" * 64,
        import_audit_event_id="26262626-2626-4262-8262-262626262626",
        imported_at=datetime.now(UTC),
    )


def _request() -> MethodologyTaskExtractorRequest:
    span = MethodologyCandidateSpan(
        document_id=DOCUMENT_ID,
        span_id=SPAN_ID,
        ordinal=1,
        content_sha256=hashlib.sha256(b"Fictional").hexdigest(),
        locator_sha256=LOCATOR_HASH,
        text="Fictional",
    )
    return MethodologyTaskExtractorRequest(
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        extraction_task_id=_EXTRACTION_TASK_ID,
        task_input_sha256=HASH_A,
        methodology_type="commercial_dd",
        methodology_version_id="commercial_dd:frozen-v1",
        methodology_version_sha256=HASH_A,
        methodology_question_id="mq_commercial_dd_fixture_0001",
        methodology_question_record_id=_QUESTION_RECORD_ID,
        methodology_question_sha256=HASH_B,
        source_trace={
            "kind": "cdd_section",
            "source_section": "Fictional",
            "source_sha256": CDD_SOURCE_SHA256,
        },
        question_text="What fictional evidence is present?",
        expected_answer_schema={"answer_type": "narrative"},
        required_evidence=({"evidence_type": "fictional_support", "min_count": 1},),
        candidate_spans=(span,),
        predicates=("fictional_evidence",),
        validation_requirements=("cite_admitted_span",),
        required_calculations=(),
    )


def _prompt() -> MethodologyPromptArtifact:
    output_schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/extraction/methodology_question_extraction_output.json"
        ).read_text(encoding="utf-8")
    )
    input_schema = {"type": "object"}
    content = "Governed fictional methodology extraction prompt."
    return MethodologyPromptArtifact(
        prompt_id="METHODOLOGY_QUESTION_EXTRACTION_V1",
        version="1.0.0",
        status="PROD",
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        input_schema=input_schema,
        input_schema_sha256=hashlib.sha256(canonical_json_bytes(input_schema)).hexdigest(),
        output_schema=output_schema,
        output_schema_sha256=hashlib.sha256(canonical_json_bytes(output_schema)).hexdigest(),
    )


@pytest.mark.parametrize(
    (
        "stop_reason",
        "block_count",
        "schema_valid",
        "response_overrides",
        "expected_code",
    ),
    (
        ("max_tokens", 1, True, {}, "METHODOLOGY_RESPONSE_TRUNCATED"),
        ("tool_use", 2, True, {}, "METHODOLOGY_RESPONSE_AMBIGUOUS"),
        ("tool_use", 1, False, {}, "METHODOLOGY_EMPTY_RESULT"),
        (
            "tool_use",
            1,
            True,
            {"id": ""},
            "METHODOLOGY_PROVIDER_PROVENANCE_INVALID",
        ),
        (
            "tool_use",
            1,
            True,
            {"model": ""},
            "METHODOLOGY_PROVIDER_PROVENANCE_INVALID",
        ),
        (
            "tool_use",
            1,
            True,
            {"model": "claude-opus-4-20250514"},
            "METHODOLOGY_PROVIDER_PROVENANCE_INVALID",
        ),
        (
            "",
            1,
            True,
            {},
            "METHODOLOGY_PROVIDER_PROVENANCE_INVALID",
        ),
        (
            "tool_use",
            1,
            True,
            {"usage": {"input_tokens": -1, "output_tokens": 5}},
            "METHODOLOGY_PROVIDER_USAGE_INVALID",
        ),
        (
            "tool_use",
            1,
            True,
            {"usage": {"input_tokens": "10", "output_tokens": 5}},
            "METHODOLOGY_PROVIDER_USAGE_INVALID",
        ),
        (
            "tool_use",
            1,
            True,
            {"usage": {"input_tokens": 10_000_000, "output_tokens": 5}},
            "METHODOLOGY_PROVIDER_USAGE_INVALID",
        ),
        (
            "tool_use",
            1,
            True,
            {"usage": {}},
            "METHODOLOGY_PROVIDER_USAGE_INVALID",
        ),
    ),
)
def test_injected_sdk_invalid_response_maps_exact_code_without_live_claim(
    stop_reason: str,
    block_count: int,
    schema_valid: bool,
    response_overrides: dict[str, Any],
    expected_code: str,
) -> None:
    request = _request()
    valid_payload = {
        "outcome": "EVIDENCE_NOT_FOUND",
        "searched_document_ids": [DOCUMENT_ID],
        "searched_span_ids": [SPAN_ID],
        "searched_scope_sha256": compute_methodology_searched_scope_sha256(request.candidate_spans),
        "factual_outputs": [],
        "gap_finding": {
            "code": "GOVERNED_EVIDENCE_GAP",
            "finding": "No responsive fictional evidence was admitted.",
        },
    }
    payload = valid_payload if schema_valid else {"outcome": "EVIDENCE_FOUND"}
    block = {"type": "tool_use", "name": "methodology_result", "input": payload}
    raw_response = {
        "id": "req_fictional_postgres_received_invalid_0001",
        "model": _MODEL_ID,
        "content": [dict(block) for _ in range(block_count)],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": stop_reason,
    }
    raw_response.update(response_overrides)

    class FakeMessages:
        def create(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(model_dump=lambda **_dump_kwargs: raw_response)

    transport = AnthropicSDKMethodologyTransport(
        api_key="fictional-test-key",
        client_factory=lambda **_kwargs: SimpleNamespace(messages=FakeMessages()),
    )
    assert transport.backend == "hermetic"
    extractor = AnthropicMethodologyTaskExtractor(
        transport=transport,
        prompt_artifact=_prompt(),
        configured_model=_MODEL_ID,
        max_tokens=512,
        temperature="0",
        price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
    )
    prepared = extractor.prepare(request, budget_policy_id=POLICY_HASH)
    admission = MethodologyCallAdmissionProof(
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
    dispatched = extractor.dispatch(prepared, admission=admission)

    assert dispatched.backend == "hermetic"
    assert (
        dispatched.provider_response_sha256
        == hashlib.sha256(exact_canonical_json_bytes(raw_response)).hexdigest()
    )
    with pytest.raises(MethodologyExtractionValidationError) as raised:
        extractor.validate_dispatched(dispatched, request=request)
    assert raised.value.code == expected_code


def test_postgres_admission_rejects_injected_sdk_before_client_call_and_retains_reservation() -> (
    None
):
    request = _request()
    sdk_calls: list[dict[str, Any]] = []

    class NeverCalledMessages:
        def create(self, **kwargs: Any) -> Any:
            sdk_calls.append(kwargs)
            raise AssertionError("backend mismatch must fail before an injected client is called")

    transport = AnthropicSDKMethodologyTransport(
        api_key="fictional-test-key",
        client_factory=lambda **_kwargs: SimpleNamespace(messages=NeverCalledMessages()),
    )
    assert transport.backend == "hermetic"
    extractor = AnthropicMethodologyTaskExtractor(
        transport=transport,
        prompt_artifact=_prompt(),
        configured_model=_MODEL_ID,
        max_tokens=512,
        temperature="0",
        price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
    )
    execution_input = MethodologyExecutionInput(
        task_record_id=READY_TASK_ID,
        extraction_task_id=_EXTRACTION_TASK_ID,
        methodology_question_record_id=_QUESTION_RECORD_ID,
        request=request,
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
        grant_application_access(database)
        _seed_task6_dependencies(database)
        _insert_policy(database.admin_engine, tenant_id=TENANT_A, policy_hash=POLICY_HASH)
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
        assert sdk_calls == []
        with database.admin_engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT a.status AS attempt_status,
                           a.budget_terminal_state,
                           a.observed_model, a.provider_request_id,
                           a.provider_response_sha256, a.dispatched_call_sha256,
                           a.input_tokens, a.output_tokens, a.stop_reason,
                           a.reserved_input_tokens, a.reserved_output_tokens,
                           a.reserved_usd,
                           a.safe_failure_code, s.status AS step_status,
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
                    SELECT used, reserved_calls, consumed_usd,
                           consumed_input_tokens, consumed_output_tokens
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
        assert row.attempt_status == "OUTCOME_UNKNOWN"
        assert row.budget_terminal_state == "RESERVED"
        assert row.observed_model is None
        assert row.provider_request_id is None
        assert row.provider_response_sha256 is None
        assert row.dispatched_call_sha256 is None
        assert row.input_tokens is None
        assert row.output_tokens is None
        assert row.stop_reason is None
        assert row.safe_failure_code == "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN"
        assert row.step_status == "FAILED"
        assert row.result_count == 0
        assert budget.used == 0
        assert budget.reserved_calls == 1
        assert budget.consumed_usd == 0
        assert budget.consumed_input_tokens == 0
        assert budget.consumed_output_tokens == 0
        assert {
            "integration.provider_budget.reserved",
            "deal.run.methodology_attempt.admitted",
            "deal.run.methodology_attempt.outcome_unknown",
        }.issubset(audit_types)
        assert "integration.provider_budget.consumed" not in audit_types
        assert "integration.provider_budget.refunded" not in audit_types
