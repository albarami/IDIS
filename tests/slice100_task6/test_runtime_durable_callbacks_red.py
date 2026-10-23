"""RED contracts for the Task-6 durable runtime composition.

These tests use hermetic dependency ports, but exercise the production
coordinators and their ordering rules.  No provider credential, network call,
or Task-7 output surface is available to the fixtures.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from idis.models.run_step import StepName
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from tests.slice100_task6._support import (
    ATTEMPT_ID,
    DEAL_ID,
    DOCUMENT_ID,
    QUESTION_RECORD_ID,
    RUN_ID,
    SHA_B,
    SHA_C,
    SHA_D,
    SHA_E,
    SPAN_ID,
    TASK_ID,
    TASK_RECORD_ID,
    TENANT_ID,
    default_searched_scope_sha256,
    extractor_request_kwargs,
    require_symbol,
)


@dataclass
class _ExecutionInput:
    task_record_id: str
    extraction_task_id: str
    methodology_question_record_id: str
    request: Any


class _RecordingExecutionBoundary:
    def __init__(self, execution_input: _ExecutionInput) -> None:
        self.execution_input = execution_input
        self.events: list[str] = []
        self.in_transaction = False
        self.terminal_commit_visible = False
        self.started_attempts: list[Any] = []
        self.committed_responses: dict[str, Any] = {}
        self.task7_writes: list[Any] = []
        self.verified_budget_policy = SimpleNamespace(policy_hash="budget-policy-fixture-v1")

    def load_execution_inputs(self, **_: Any) -> list[_ExecutionInput]:
        self.events.append("load_inputs")
        return [self.execution_input]

    def load_started_attempts(self, **_: Any) -> list[Any]:
        self.events.append("load_started")
        return list(self.started_attempts)

    def load_committed_responses(self, **_: Any) -> dict[str, Any]:
        self.events.append("load_committed")
        return dict(self.committed_responses)

    def load_active_budget_policy(self, **_: Any) -> Any:
        self.events.append("load_policy")
        return self.verified_budget_policy

    def admit_call(self, *, verified_budget_policy: Any, **_: Any) -> Any:
        assert verified_budget_policy is self.verified_budget_policy
        self.events.append("admission_begin")
        self.in_transaction = True
        self.events.append("admission_commit")
        self.in_transaction = False
        return SimpleNamespace(
            committed=True,
            budget_policy_id="budget-policy-fixture-v1",
        )

    def terminalize_call(self, **_: Any) -> Any:
        self.events.append("terminal_begin")
        self.in_transaction = True
        self.events.append("terminal_commit")
        self.in_transaction = False
        self.terminal_commit_visible = True
        return SimpleNamespace(committed=True)

    def fail_received_response(self, *, dispatched: Any, **_: Any) -> None:
        assert dispatched is not None
        self.events.append("received_billable_failure_commit")

    def stage_exact_terminal_set(self, **_: Any) -> Any:
        self.events.append("stage_exact_set")

        def persist(**_values: Any) -> None:
            self.events.append("exact_set")

        return SimpleNamespace(persist=persist)

    def build_execution_result(self, *, responses_by_task_id: dict[str, Any], **_: Any) -> Any:
        self.events.append("build_result")
        return SimpleNamespace(
            to_run_step_summary=lambda: {
                "status": "COMPLETED",
                "task_ids": sorted(responses_by_task_id),
            }
        )

    def mark_started_attempts_outcome_unknown(self, **_: Any) -> None:
        self.events.append("outcome_unknown_commit")

    def mark_admitted_call_outcome_unknown(self, **_: Any) -> None:
        self.events.append("post_admission_outcome_unknown_commit")

    def fail_provider_request_not_accepted(self, *, nonacceptance: Any, **_: Any) -> None:
        self.nonacceptance = nonacceptance
        self.events.append("provider_request_refund_commit")


class _RecordingExtractor:
    external_calls_enabled = True

    def __init__(self, boundary: _RecordingExecutionBoundary) -> None:
        self.boundary = boundary
        self.seen_question_record_id: str | None = None

    def prepare(self, request: Any, *, budget_policy_id: str) -> Any:
        assert budget_policy_id == "budget-policy-fixture-v1"
        self.boundary.events.append("prepare")
        self.seen_question_record_id = request.methodology_question_record_id
        return SimpleNamespace(request=request)

    def dispatch(self, prepared: Any, *, admission: Any) -> Any:
        assert admission.committed is True
        assert self.boundary.in_transaction is False
        self.boundary.events.append("dispatch")
        return SimpleNamespace(prepared_call=prepared)

    def validate_dispatched(self, dispatched: Any, *, request: Any) -> Any:
        assert dispatched.prepared_call.request is request
        self.boundary.events.append("validate")
        return SimpleNamespace(request=request)

    def compute_prospective_receipt_sha256(
        self,
        dispatched: Any,
        *,
        validated: Any,
        budget_terminal_state: str,
        budget_policy_id: str,
    ) -> str:
        assert validated.request is dispatched.prepared_call.request
        assert budget_terminal_state == "CONSUMED"
        assert budget_policy_id == "budget-policy-fixture-v1"
        self.boundary.events.append("prospective_receipt")
        return "a" * 64

    def finalize(
        self,
        dispatched: Any,
        *,
        validated: Any,
        terminalization_proof: Any,
    ) -> Any:
        assert terminalization_proof.committed is True
        assert self.boundary.in_transaction is False
        assert self.boundary.terminal_commit_visible is True
        assert validated.request is dispatched.prepared_call.request
        self.boundary.events.append("finalize")
        return SimpleNamespace(outcome="EVIDENCE_FOUND")


def _execution_service(boundary: _RecordingExecutionBoundary) -> tuple[Any, _RecordingExtractor]:
    service_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_execution",
        "DurableRunMethodologyExtractionTaskExecutionService",
    )
    extractor = _RecordingExtractor(boundary)
    return service_cls(extractor=extractor, boundary=boundary), extractor


class _LiveResponseBoundary(_RecordingExecutionBoundary):
    def __init__(self, execution_input: _ExecutionInput) -> None:
        super().__init__(execution_input)
        self.failed_dispatch: Any | None = None
        self.failure_code: str | None = None

    def admit_call(
        self,
        *,
        prepared: Any,
        verified_budget_policy: Any,
        **_: Any,
    ) -> Any:
        admission_cls = require_symbol(
            "idis.models.methodology_extraction", "MethodologyCallAdmissionProof"
        )
        assert verified_budget_policy is self.verified_budget_policy
        self.events.extend(("admission_begin", "admission_commit"))
        return admission_cls(
            backend="postgresql",
            committed=True,
            attempt_id=ATTEMPT_ID,
            budget_reservation_id=prepared.budget_reservation_id,
            prepared_call_sha256=prepared.prepared_call_sha256,
            task_input_sha256=prepared.request.task_input_sha256,
            price_catalog_row_sha256=prepared.reservation.price_catalog_row_sha256,
            worker_fencing_token=7,
            budget_policy_id=self.verified_budget_policy.policy_hash,
        )

    def fail_received_response(
        self,
        *,
        dispatched: Any,
        safe_failure_code: str,
        **_: Any,
    ) -> None:
        self.failed_dispatch = dispatched
        self.failure_code = safe_failure_code
        self.events.append("received_billable_failure_commit")


class _HermeticResponseBoundary(_LiveResponseBoundary):
    def admit_call(self, **values: Any) -> Any:
        admission = super().admit_call(**values)
        return admission.__class__(**{**admission.model_dump(), "backend": "hermetic"})


def _production_prompt_artifact() -> Any:
    model_module = __import__(
        "idis.models.methodology_extraction",
        fromlist=["MethodologyPromptArtifact", "canonical_json_bytes"],
    )
    output_schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/extraction/methodology_question_extraction_output.json"
        ).read_text(encoding="utf-8")
    )
    input_schema = {"type": "object"}
    content = "Governed fictional methodology extraction prompt."
    return model_module.MethodologyPromptArtifact(
        prompt_id="METHODOLOGY_QUESTION_EXTRACTION_V1",
        version="1.0.0",
        status="PROD",
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        input_schema=input_schema,
        input_schema_sha256=hashlib.sha256(
            model_module.canonical_json_bytes(input_schema)
        ).hexdigest(),
        output_schema=output_schema,
        output_schema_sha256=hashlib.sha256(
            model_module.canonical_json_bytes(output_schema)
        ).hexdigest(),
    )


@pytest.mark.parametrize(
    ("stop_reason", "block_count", "schema_valid", "expected_code"),
    (
        ("max_tokens", 1, True, "METHODOLOGY_RESPONSE_TRUNCATED"),
        ("tool_use", 2, True, "METHODOLOGY_RESPONSE_AMBIGUOUS"),
        ("tool_use", 1, False, "METHODOLOGY_EMPTY_RESULT"),
    ),
)
def test_injected_sdk_invalid_response_maps_validation_code_without_live_claim(
    stop_reason: str,
    block_count: int,
    schema_valid: bool,
    expected_code: str,
) -> None:
    extractor_module = __import__(
        "idis.services.extraction.methodology_task_extractor",
        fromlist=["AnthropicSDKMethodologyTransport"],
    )
    model_module = __import__(
        "idis.models.methodology_extraction",
        fromlist=["MethodologyTaskExtractorRequest"],
    )
    request = model_module.MethodologyTaskExtractorRequest(**extractor_request_kwargs())
    valid_payload = {
        "outcome": "EVIDENCE_NOT_FOUND",
        "searched_document_ids": [DOCUMENT_ID],
        "searched_span_ids": [SPAN_ID],
        "searched_scope_sha256": default_searched_scope_sha256(),
        "factual_outputs": [],
        "gap_finding": {
            "code": "GOVERNED_EVIDENCE_GAP",
            "finding": "No responsive fictional evidence was admitted.",
        },
    }
    payload = valid_payload if schema_valid else {"outcome": "EVIDENCE_FOUND"}
    block = {"type": "tool_use", "name": "methodology_result", "input": payload}
    raw_response = {
        "id": "req_fictional_received_invalid_0001",
        "model": "claude-sonnet-4-20250514",
        "content": [dict(block) for _ in range(block_count)],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": stop_reason,
    }

    class FakeMessages:
        def create(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(model_dump=lambda **_dump_kwargs: raw_response)

    transport = extractor_module.AnthropicSDKMethodologyTransport(
        api_key="fictional-test-key",
        client_factory=lambda **_kwargs: SimpleNamespace(messages=FakeMessages()),
    )
    assert transport.backend == "hermetic"
    extractor = extractor_module.AnthropicMethodologyTaskExtractor(
        transport=transport,
        prompt_artifact=_production_prompt_artifact(),
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=extractor_module.ANTHROPIC_STANDARD_PRICE_CATALOG,
    )
    execution_input = _ExecutionInput(
        task_record_id=TASK_RECORD_ID,
        extraction_task_id=TASK_ID,
        methodology_question_record_id=QUESTION_RECORD_ID,
        request=request,
    )
    boundary = _HermeticResponseBoundary(execution_input)
    service_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_execution",
        "DurableRunMethodologyExtractionTaskExecutionService",
    )

    with pytest.raises(Exception) as raised:
        service_cls(extractor=extractor, boundary=boundary).run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            run_step_id="18181818-1818-4181-8181-181818181818",
            tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
            documents=[],
        )

    assert getattr(raised.value, "code", None) == expected_code
    assert boundary.failure_code == expected_code
    assert boundary.failed_dispatch is not None
    assert boundary.failed_dispatch.backend == "hermetic"
    assert boundary.failed_dispatch.provider_request_id == raw_response["id"]
    assert (
        boundary.failed_dispatch.provider_response_sha256
        == hashlib.sha256(model_module.exact_canonical_json_bytes(raw_response)).hexdigest()
    )
    assert "received_billable_failure_commit" in boundary.events
    assert "post_admission_outcome_unknown_commit" not in boundary.events
    assert "terminal_begin" not in boundary.events
    assert boundary.committed_responses == {}


def test_returned_backend_mismatch_is_received_provenance_failure() -> None:
    extractor_module = __import__(
        "idis.services.extraction.methodology_task_extractor",
        fromlist=["AnthropicMethodologyTaskExtractor"],
    )
    model_module = __import__(
        "idis.models.methodology_extraction",
        fromlist=["MethodologyTaskExtractorRequest"],
    )
    request = model_module.MethodologyTaskExtractorRequest(**extractor_request_kwargs())
    payload = {
        "outcome": "EVIDENCE_NOT_FOUND",
        "searched_document_ids": [DOCUMENT_ID],
        "searched_span_ids": [SPAN_ID],
        "searched_scope_sha256": default_searched_scope_sha256(),
        "factual_outputs": [],
        "gap_finding": {
            "code": "GOVERNED_EVIDENCE_GAP",
            "finding": "No responsive fictional evidence was admitted.",
        },
    }

    class BackendMismatchTransport:
        backend = "live"

        def create_message(self, **_kwargs: Any) -> Any:
            return extractor_module.AnthropicMethodologyTransportResponse(
                backend="hermetic",
                provider_request_id="req_fictional_backend_mismatch_0001",
                observed_model="claude-sonnet-4-20250514",
                content_blocks=(
                    {"type": "tool_use", "name": "methodology_result", "input": payload},
                ),
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                stop_reason="tool_use",
                provider_response_sha256="a" * 64,
            )

    extractor = extractor_module.AnthropicMethodologyTaskExtractor(
        transport=BackendMismatchTransport(),
        prompt_artifact=_production_prompt_artifact(),
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=extractor_module.ANTHROPIC_STANDARD_PRICE_CATALOG,
    )
    execution_input = _ExecutionInput(
        task_record_id=TASK_RECORD_ID,
        extraction_task_id=TASK_ID,
        methodology_question_record_id=QUESTION_RECORD_ID,
        request=request,
    )
    boundary = _LiveResponseBoundary(execution_input)
    service_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_execution",
        "DurableRunMethodologyExtractionTaskExecutionService",
    )

    with pytest.raises(Exception) as raised:
        service_cls(extractor=extractor, boundary=boundary).run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            run_step_id="18181818-1818-4181-8181-181818181818",
            tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
            documents=[],
        )

    assert getattr(raised.value, "code", None) == ("METHODOLOGY_PROVIDER_PROVENANCE_INVALID")
    assert boundary.failure_code == "METHODOLOGY_PROVIDER_PROVENANCE_INVALID"
    assert "received_billable_failure_commit" in boundary.events
    assert "post_admission_outcome_unknown_commit" not in boundary.events


def test_durable_execution_threads_exact_question_record_and_orders_micro_boundaries() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    request = request_cls(**extractor_request_kwargs())
    boundary = _RecordingExecutionBoundary(
        _ExecutionInput(
            task_record_id=TASK_RECORD_ID,
            extraction_task_id=TASK_ID,
            methodology_question_record_id=QUESTION_RECORD_ID,
            request=request,
        )
    )
    service, extractor = _execution_service(boundary)

    run_result, execution_result, staged_exact_set = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        run_step_id="step-methodology-execution",
        tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
        documents=[{"document_id": DOCUMENT_ID}],
    )

    assert extractor.seen_question_record_id == QUESTION_RECORD_ID
    assert boundary.events == [
        "load_inputs",
        "load_started",
        "load_committed",
        "load_policy",
        "prepare",
        "admission_begin",
        "admission_commit",
        "dispatch",
        "validate",
        "prospective_receipt",
        "terminal_begin",
        "terminal_commit",
        "finalize",
        "stage_exact_set",
        "build_result",
    ]
    assert run_result is execution_result
    assert execution_result.to_run_step_summary()["status"] == "COMPLETED"
    assert boundary.task7_writes == []
    assert "exact_set" not in boundary.events
    staged_exact_set.persist(
        db_conn=object(),
        lease_owner_id="worker-fixture",
        lease_fencing_token=7,
    )
    assert boundary.events[-1] == "exact_set"


def test_started_attempt_is_marked_outcome_unknown_without_redispatch() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    boundary = _RecordingExecutionBoundary(
        _ExecutionInput(
            task_record_id=TASK_RECORD_ID,
            extraction_task_id=TASK_ID,
            methodology_question_record_id=QUESTION_RECORD_ID,
            request=request_cls(**extractor_request_kwargs()),
        )
    )
    boundary.started_attempts = [SimpleNamespace(attempt_id="ambiguous-attempt")]
    service, _extractor = _execution_service(boundary)
    error_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_execution",
        "DurableMethodologyExtractionExecutionError",
    )

    with pytest.raises(error_cls) as raised:
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            run_step_id="step-methodology-execution",
            tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
            documents=[],
        )

    assert raised.value.code == "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN"
    assert boundary.events == ["load_inputs", "load_started", "outcome_unknown_commit"]
    assert "dispatch" not in boundary.events
    assert boundary.task7_writes == []


def test_committed_terminal_response_is_rehydrated_without_redispatch() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    response = SimpleNamespace(outcome="EVIDENCE_NOT_FOUND")
    boundary = _RecordingExecutionBoundary(
        _ExecutionInput(
            task_record_id=TASK_RECORD_ID,
            extraction_task_id=TASK_ID,
            methodology_question_record_id=QUESTION_RECORD_ID,
            request=request_cls(**extractor_request_kwargs()),
        )
    )
    boundary.committed_responses[TASK_ID] = response
    service, _extractor = _execution_service(boundary)

    _run_result, execution_result, _staged = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        run_step_id="step-methodology-execution",
        tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
        documents=[],
    )

    assert execution_result.to_run_step_summary()["status"] == "COMPLETED"
    assert "prepare" not in boundary.events
    assert "dispatch" not in boundary.events


def test_invalid_received_provider_response_is_consumed_and_terminally_fails() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    boundary = _RecordingExecutionBoundary(
        _ExecutionInput(
            task_record_id=TASK_RECORD_ID,
            extraction_task_id=TASK_ID,
            methodology_question_record_id=QUESTION_RECORD_ID,
            request=request_cls(**extractor_request_kwargs()),
        )
    )
    service, extractor = _execution_service(boundary)

    def reject_confirmed(*_args: Any, **_kwargs: Any) -> Any:
        error = RuntimeError("METHODOLOGY_EMPTY_RESULT")
        error.code = "METHODOLOGY_EMPTY_RESULT"  # type: ignore[attr-defined]
        raise error

    extractor.validate_dispatched = reject_confirmed  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="METHODOLOGY_EMPTY_RESULT"):
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            run_step_id="step-methodology-execution",
            tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
            documents=[],
        )

    assert boundary.events[-1] == "received_billable_failure_commit"
    assert "terminal_begin" not in boundary.events


def test_transport_exception_after_admission_is_immediately_outcome_unknown() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    boundary = _RecordingExecutionBoundary(
        _ExecutionInput(
            task_record_id=TASK_RECORD_ID,
            extraction_task_id=TASK_ID,
            methodology_question_record_id=QUESTION_RECORD_ID,
            request=request_cls(**extractor_request_kwargs()),
        )
    )
    service, extractor = _execution_service(boundary)
    error_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_execution",
        "DurableMethodologyExtractionExecutionError",
    )

    def ambiguous_dispatch(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("transport outcome is ambiguous")

    extractor.dispatch = ambiguous_dispatch  # type: ignore[method-assign]

    with pytest.raises(error_cls) as raised:
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            run_step_id="step-methodology-execution",
            tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
            documents=[],
        )

    assert raised.value.code == "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN"
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert boundary.events[-1] == "post_admission_outcome_unknown_commit"
    assert "received_billable_failure_commit" not in boundary.events


def test_typed_provider_nonacceptance_is_the_only_refund_authority() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    nonacceptance_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyProviderRequestNotAccepted",
    )
    boundary = _RecordingExecutionBoundary(
        _ExecutionInput(
            task_record_id=TASK_RECORD_ID,
            extraction_task_id=TASK_ID,
            methodology_question_record_id=QUESTION_RECORD_ID,
            request=request_cls(**extractor_request_kwargs()),
        )
    )
    service, extractor = _execution_service(boundary)
    error_cls = require_symbol(
        "idis.services.runs.methodology_extraction_task_execution",
        "DurableMethodologyExtractionExecutionError",
    )

    def confirmed_nonacceptance(*_args: Any, **_kwargs: Any) -> Any:
        raise nonacceptance_cls(provider_status_code=400)

    extractor.dispatch = confirmed_nonacceptance  # type: ignore[method-assign]

    with pytest.raises(error_cls) as raised:
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            run_step_id="step-methodology-execution",
            tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
            documents=[],
        )

    assert raised.value.code == "METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED"
    assert boundary.events[-1] == "provider_request_refund_commit"
    assert boundary.nonacceptance.proof_kind == "anthropic-http-4xx-v1"
    assert boundary.nonacceptance.provider_status_code == 400
    assert "post_admission_outcome_unknown_commit" not in boundary.events
    assert "received_billable_failure_commit" not in boundary.events


def test_fence_loss_at_admission_never_dispatches_or_writes_task7_state() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    boundary = _RecordingExecutionBoundary(
        _ExecutionInput(
            task_record_id=TASK_RECORD_ID,
            extraction_task_id=TASK_ID,
            methodology_question_record_id=QUESTION_RECORD_ID,
            request=request_cls(**extractor_request_kwargs()),
        )
    )

    def fenced_admission(**_: Any) -> Any:
        boundary.events.append("admission_rollback")
        error = RuntimeError("WORKER_LEASE_FENCED")
        error.code = "WORKER_LEASE_FENCED"  # type: ignore[attr-defined]
        raise error

    boundary.admit_call = fenced_admission  # type: ignore[method-assign]
    service, _extractor = _execution_service(boundary)

    with pytest.raises(RuntimeError, match="WORKER_LEASE_FENCED"):
        service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            run_step_id="step-methodology-execution",
            tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
            documents=[],
        )

    assert boundary.events[-1] == "admission_rollback"
    assert "dispatch" not in boundary.events
    assert boundary.task7_writes == []


class _CanonicalBoundary:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.task7_writes: list[Any] = []
        self.snapshot = {
            "factual_output_ids": ("factual-001",),
            "persisted_factual_output_ids": ("factual-001",),
            "claim_link_output_ids": ("factual-001",),
            "evidence_link_output_ids": ("factual-001",),
            "sanad_link_output_ids": ("factual-001",),
            "accepted_source_locator_pairs_by_output": {"factual-001": ((SPAN_ID, SHA_B),)},
            "evidence_link_source_locator_pairs_by_output": {"factual-001": ((SPAN_ID, SHA_B),)},
            "expected_defect_ids_by_output": {"factual-001": ()},
            "defect_link_pairs": (),
            "rejected_count": 0,
            "claims": ("durable-claim",),
            "evidence_items": ("durable-evidence",),
            "sanads": ("durable-sanad",),
            "sanad_links": ("durable-sanad-link",),
            "sanad_grades": ("durable-grade",),
            "sanad_defects": (),
        }

    def prepare_claim_materialization(self, **_: Any) -> tuple[Any, tuple[str, ...], Any]:
        self.events.append("prepare_claims")
        return (
            "claim-result",
            ("computed-claim",),
            _CanonicalWrite(self, "persist_claim_materialization"),
        )

    def prepare_evidence_materialization(self, **_: Any) -> tuple[Any, tuple[str, ...], Any]:
        self.events.append("prepare_evidence")
        return (
            "evidence-result",
            ("computed-evidence",),
            _CanonicalWrite(self, "persist_evidence_materialization"),
        )

    def prepare_sanad_materialization(
        self, **_: Any
    ) -> tuple[Any, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[Any, ...], Any]:
        self.events.append("prepare_sanads")
        return (
            "sanad-result",
            ("computed-sanad",),
            ("computed-link",),
            ("computed-grade",),
            (),
            _CanonicalWrite(self, "persist_sanad_materialization"),
        )

    def persist_claim_materialization(self, **_: Any) -> None:
        self.events.append("persist_claims")

    def persist_evidence_materialization(self, **_: Any) -> None:
        self.events.append("persist_evidence")

    def persist_sanad_materialization(self, **_: Any) -> None:
        self.events.append("persist_sanads")

    def load_canonical_materialization_snapshot(self, **_: Any) -> dict[str, Any]:
        self.events.append("load_snapshot")
        return dict(self.snapshot)


class _CanonicalWrite:
    def __init__(self, boundary: _CanonicalBoundary, method_name: str) -> None:
        self.boundary = boundary
        self.method_name = method_name

    def persist(self, **values: Any) -> None:
        getattr(self.boundary, self.method_name)(**values)


def test_canonical_steps_persist_exact_links_and_rehydrate_only_from_snapshot() -> None:
    service_cls = require_symbol(
        "idis.services.extraction.canonical_methodology_materialization",
        "DurableCanonicalMethodologyMaterializationService",
    )
    boundary = _CanonicalBoundary()
    service = service_cls(boundary=boundary)
    scope = {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID}

    # Immediate-persistence assertions were obsolete once the reviewer required
    # Steps 8-10 rows to share the ordinary fenced completion transaction.
    _claim_result, claims, claim_write = service.materialize_claims(
        run_step_id="step-claims", **scope
    )
    _evidence_result, evidence, evidence_write = service.materialize_evidence(
        run_step_id="step-evidence", **scope
    )
    _sanad_result, sanads, links, grades, defects, sanad_write = service.materialize_sanads(
        run_step_id="step-sanads", **scope
    )

    assert claims == ("computed-claim",)
    assert evidence == ("computed-evidence",)
    assert (sanads, links, grades, defects) == (
        ("computed-sanad",),
        ("computed-link",),
        ("computed-grade",),
        (),
    )
    assert boundary.events == ["prepare_claims", "prepare_evidence", "prepare_sanads"]
    for staged in (claim_write, evidence_write, sanad_write):
        staged.persist(
            db_conn=object(),
            lease_owner_id="worker-fixture",
            lease_fencing_token=7,
        )

    poisoned_summary = {
        "claims": ["summary-claim"],
        "evidence_items": ["summary-evidence"],
        "sanads": ["summary-sanad"],
    }
    assert service.rehydrate_claims(result_summary=poisoned_summary, **scope) == ("durable-claim",)
    assert service.rehydrate_evidence(result_summary=poisoned_summary, **scope) == (
        "durable-evidence",
    )
    assert service.rehydrate_sanads(result_summary=poisoned_summary, **scope) == (
        ("durable-sanad",),
        ("durable-sanad-link",),
        ("durable-grade",),
        (),
    )
    assert boundary.events[:6] == [
        "prepare_claims",
        "prepare_evidence",
        "prepare_sanads",
        "persist_claims",
        "persist_evidence",
        "persist_sanads",
    ]
    assert boundary.task7_writes == []


def test_strict_canonical_callbacks_stage_writes_for_their_completion_boundaries() -> None:
    staged_claims = _StagedWrite([])
    staged_evidence = _StagedWrite([])
    staged_sanads = _StagedWrite([])
    completed = SimpleNamespace(
        to_run_step_summary=lambda: {"status": "COMPLETED", "rejected_count": 0}
    )

    def claims_callback(**_: Any) -> Any:
        return completed, [SimpleNamespace(claim_id="canonical-claim")], staged_claims

    def evidence_callback(**_: Any) -> Any:
        return (
            completed,
            [SimpleNamespace(source_ref=SimpleNamespace(source_span_id=SPAN_ID))],
            staged_evidence,
        )

    def sanads_callback(**_: Any) -> Any:
        return completed, [], [], [], [], staged_sanads

    for callback in (claims_callback, evidence_callback, sanads_callback):
        callback.persistence_backend = "postgresql"  # type: ignore[attr-defined]
        callback.external_calls_enabled = False  # type: ignore[attr-defined]

    context = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        strict_resume_validation_required=True,
        active_run_step_id="17171717-1717-4171-8171-171717171717",
        methodology_extraction_execution_result=object(),  # type: ignore[arg-type]
        methodology_claim_materialization_fn=claims_callback,
        methodology_evidence_item_materialization_fn=evidence_callback,
        methodology_sanad_creation_linking_grading_fn=sanads_callback,
    )
    orchestrator = object.__new__(RunOrchestrator)

    orchestrator._execute_methodology_claim_materialization(context)
    orchestrator._execute_methodology_evidence_item_materialization(context)
    orchestrator._execute_methodology_sanad_creation_linking_grading(context)

    assert context.methodology_pending_native_output_writes == {
        StepName.METHODOLOGY_CLAIM_MATERIALIZATION: (staged_claims,),
        StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION: (staged_evidence,),
        StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING: (staged_sanads,),
    }


class _StagedWrite:
    def __init__(self, durable_rows: list[str]) -> None:
        self.durable_rows = durable_rows

    def persist(self, **_: Any) -> None:
        self.durable_rows.append("methodology-task-row")


def test_step6_write_waits_for_completion_boundary_and_rolls_back_with_step() -> None:
    durable_rows: list[str] = []
    staged_write = _StagedWrite(durable_rows)
    captured: dict[str, Any] = {}
    context = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        lease_owner_id="worker-fixture",
        lease_fencing_token=7,
        methodology_pending_native_output_writes={
            StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING: (staged_write,)
        },
        stage_native_output_writes_fn=lambda **_kwargs: (),
    )
    step = SimpleNamespace(
        step_id="step-planning",
        step_name=StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING,
    )

    def boundary_uow(**kwargs: Any) -> None:
        captured.update(kwargs)
        before = list(durable_rows)
        try:
            for write in kwargs["native_output_writes"]:
                write.persist(
                    db_conn=object(),
                    lease_owner_id="worker-fixture",
                    lease_fencing_token=7,
                )
            raise RuntimeError("simulated step-ledger failure")
        except Exception:
            durable_rows[:] = before
            raise

    context.step_boundary_uow_fn = boundary_uow
    orchestrator = object.__new__(RunOrchestrator)
    orchestrator._complete_step = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    assert durable_rows == []
    with pytest.raises(RuntimeError, match="simulated step-ledger failure"):
        orchestrator._complete_step_boundary(context, step, {"status": "COMPLETED"})

    assert captured["boundary"] == "COMPLETE"
    assert captured["native_output_writes"] == (staged_write,)
    assert durable_rows == []


def test_production_request_builder_binds_exact_frozen_question_and_live_span() -> None:
    builder = require_symbol("idis.pipeline.worker", "_build_methodology_extractor_request")
    exact_source_text = " \tFictional admitted source — café.\r\n\r\nSecond line Ω. \r\n"
    exact_source_sha256 = hashlib.sha256(exact_source_text.encode("utf-8")).hexdigest()
    exact_question_text = (
        " \tWhat is the fictional recurring revenue — café?\r\n\r\n"
        "Retain the frozen question Ω exactly. \r\n"
    )
    methodology_type = SimpleNamespace(value="financial_dd")
    task = SimpleNamespace(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        extraction_task_id=TASK_ID,
        methodology_type=methodology_type,
        methodology_version_id="financial_dd:frozen-v1",
        methodology_question_id="mq_financial_dd_fictional_0001",
        document_id=DOCUMENT_ID,
        source_spans=[SimpleNamespace(span_id=SPAN_ID, content_hash=exact_source_sha256)],
        expected_answer_schema=SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "answer_type": "numeric",
                "constraints": {"currency": "USD"},
            }
        ),
        required_evidence=[
            SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "evidence_type": "fictional_support",
                    "description": "Fictional evidence",
                    "min_count": 1,
                }
            )
        ],
        required_calculations=[
            SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "calc_type": "fictional_bridge",
                    "required": True,
                }
            )
        ],
        validation_requirements=("cite_admitted_span",),
    )
    version_record_id = "17171717-1717-4171-8171-171717171717"
    record = SimpleNamespace(
        task_record_id=TASK_RECORD_ID,
        methodology_question_record_id=QUESTION_RECORD_ID,
        methodology_version_record_id=version_record_id,
        task_input_sha256=SHA_C,
        task=task,
    )
    question = SimpleNamespace(
        methodology_question_id=task.methodology_question_id,
        methodology_version_id=task.methodology_version_id,
        methodology_type=methodology_type,
        question_text=exact_question_text,
        line_item="fictional_recurring_revenue",
        term="Fictional recurring revenue",
        required_evidence=[
            SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "evidence_type": "fictional_support",
                    "description": "Fictional evidence",
                    "min_count": 1,
                }
            )
        ],
        required_calculations=[
            SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "calc_type": "fictional_bridge",
                    "required": True,
                }
            )
        ],
        validation_requirements=("cite_admitted_span",),
        source_trace=SimpleNamespace(
            sheet_or_section="Fictional P&L",
            row_number=4,
            source_anchor_cell="A4",
            source_hash=SHA_D,
        ),
    )
    version = SimpleNamespace(
        methodology_version_record_id=version_record_id,
        methodology_version_id=task.methodology_version_id,
        version_hash=SHA_D,
        source_sha256=SHA_D,
    )
    live_span = {
        "document_id": DOCUMENT_ID,
        "span_id": SPAN_ID,
        "content_hash": exact_source_sha256,
        "locator": {"sheet": "Fictional P&L", "cell": "B4"},
        "text_excerpt": exact_source_text,
    }

    request = builder(
        task_record=record,
        question=question,
        version_record=version,
        methodology_question_sha256=SHA_E,
        live_spans=[live_span],
    )

    assert request.methodology_question_record_id == QUESTION_RECORD_ID
    assert request.methodology_version_sha256 == SHA_D
    assert request.source_trace.kind == "fdd_workbook_row"
    assert request.source_trace.row_number == 4
    assert request.candidate_spans[0].content_sha256 == exact_source_sha256
    assert request.candidate_spans[0].locator_sha256 != SHA_B
    assert request.candidate_spans[0].text == exact_source_text
    assert request.question_text == exact_question_text
    assert request.expected_answer_schema == {
        "answer_type": "numeric",
        "constraints": {"currency": "USD"},
    }

    extractor_module = __import__(
        "idis.services.extraction.methodology_task_extractor",
        fromlist=["AnthropicMethodologyTaskExtractor"],
    )
    extractor = extractor_module.AnthropicMethodologyTaskExtractor(
        transport=SimpleNamespace(backend="hermetic"),
        prompt_artifact=_production_prompt_artifact(),
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=extractor_module.ANTHROPIC_STANDARD_PRICE_CATALOG,
    )
    prepared = extractor.prepare(request, budget_policy_id="hermetic")
    wire = json.loads(prepared.billable_payload.decode("utf-8"))
    provider_document = json.loads(wire["messages"][0]["content"][0]["text"])
    assert provider_document["candidate_spans"][0]["text"] == exact_source_text
    assert provider_document["question_text"] == exact_question_text
    assert (
        prepared.reservation.billable_payload_sha256
        == hashlib.sha256(prepared.billable_payload).hexdigest()
    )
    changed_request_values = request.model_dump(mode="python")
    changed_request_values["question_text"] = exact_question_text[:-1]
    changed_request = type(request).model_validate(changed_request_values)
    changed_prepared = extractor.prepare(changed_request, budget_policy_id="hermetic")
    assert changed_prepared.reservation.billable_payload_sha256 != (
        prepared.reservation.billable_payload_sha256
    )
    assert changed_prepared.prepared_call_sha256 != prepared.prepared_call_sha256


def test_production_request_builder_uses_the_typed_cdd_section_discriminator() -> None:
    builder = require_symbol("idis.pipeline.worker", "_build_methodology_extractor_request")
    source_text = "Fictional admitted commercial text."
    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    methodology_type = SimpleNamespace(value="commercial_dd")
    task = SimpleNamespace(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        extraction_task_id=TASK_ID,
        methodology_type=methodology_type,
        methodology_version_id="commercial_dd:frozen-v1",
        methodology_question_id="mq_commercial_dd_fictional_0001",
        document_id=DOCUMENT_ID,
        source_spans=[SimpleNamespace(span_id=SPAN_ID, content_hash=source_sha256)],
        expected_answer_schema=SimpleNamespace(
            model_dump=lambda **_kwargs: {"answer_type": "narrative"}
        ),
        required_evidence=[
            SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "evidence_type": "fictional_support",
                    "description": "Fictional evidence",
                    "min_count": 1,
                }
            )
        ],
        required_calculations=[],
        validation_requirements=("cite_admitted_span",),
    )
    record = SimpleNamespace(
        task_record_id=TASK_RECORD_ID,
        methodology_question_record_id=QUESTION_RECORD_ID,
        methodology_version_record_id="17171717-1717-4171-8171-171717171717",
        task_input_sha256=SHA_C,
        task=task,
    )
    question = SimpleNamespace(
        methodology_question_id=task.methodology_question_id,
        methodology_version_id=task.methodology_version_id,
        methodology_type=methodology_type,
        question_text="What is the fictional market size?",
        line_item="fictional_market_size",
        term="Fictional market size",
        required_evidence=[
            SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "evidence_type": "fictional_support",
                    "description": "Fictional evidence",
                    "min_count": 1,
                }
            )
        ],
        required_calculations=[],
        validation_requirements=("cite_admitted_span",),
        source_trace=SimpleNamespace(
            sheet_or_section="Fictional market",
            row_number=None,
            source_anchor_cell=None,
            source_hash=SHA_D,
        ),
    )
    version = SimpleNamespace(
        methodology_version_record_id=record.methodology_version_record_id,
        methodology_version_id=task.methodology_version_id,
        version_hash=SHA_D,
        source_sha256=SHA_D,
    )

    request = builder(
        task_record=record,
        question=question,
        version_record=version,
        methodology_question_sha256=SHA_E,
        live_spans=[
            {
                "document_id": DOCUMENT_ID,
                "span_id": SPAN_ID,
                "content_hash": source_sha256,
                "locator": {"page": 1},
                "text_excerpt": source_text,
            }
        ],
    )

    assert request.source_trace.kind == "cdd_section"
    assert request.source_trace.source_section == "Fictional market"
