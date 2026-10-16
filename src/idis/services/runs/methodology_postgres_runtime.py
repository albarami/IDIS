"""PostgreSQL composition for strict Task-6 methodology execution.

This module owns the approved Step-7 microtransactions.  It never holds a
database transaction across provider dispatch and never exposes a response
until the terminal result, budget consumption, and audit event have committed.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from idis.audit.canonical import build_canonical_audit_event
from idis.audit.sink import AuditSinkError
from idis.models.methodology_extraction import (
    DurableMethodologyExtractionExecutionResult,
    MethodologyCallAdmissionProof,
    MethodologyCallTerminalizationProof,
    MethodologyProviderReceipt,
    MethodologyTaskExtractorResponse,
    ValidatedDispatchedMethodologyResult,
    compute_methodology_provider_receipt_sha256,
)
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.persistence.repositories.methodologies import (
    get_methodologies_repository,
    methodology_question_payload_hashes,
)
from idis.persistence.repositories.methodology_extraction import (
    PostgresMethodologyExtractionRepository,
)
from idis.persistence.repositories.provider_budget_policies import (
    PostgresProviderBudgetPoliciesRepository,
    VerifiedActiveProviderBudgetPolicy,
)
from idis.providers.budget import (
    PostgresProviderBudgetStore,
    provider_budget_exceeded_error_code,
)
from idis.runtime.governance_envelopes import BUDGET_SCHEMA_VERSION, verify_envelope
from idis.runtime.secure_files import secure_read_integrity_file
from idis.services.extraction.methodology_task_extractor import (
    MethodologyProviderRequestNotAccepted,
    validate_step7_exact_terminal_set,
)
from idis.validators.audit_event_validator import validate_audit_event

_METHODOLOGY_MAX_TOKENS = 4096
_GOVERNANCE_IMPORT_ACTOR = "slice100-governance-admin"
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,127}\Z")
_POSTGRES_BIGINT_MAX = (1 << 63) - 1


def _returned_text_or_none(value: Any) -> str | None:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        return None
    return value


def _returned_usage_or_none(value: Any) -> int | None:
    if type(value) is not int or value < 0 or value > _POSTGRES_BIGINT_MAX:
        return None
    return value


class MethodologyPostgresRuntimeError(RuntimeError):
    """Secret-safe strict composition failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MethodologyExecutionInput:
    """One exact durable task plus its source-text-bearing provider request."""

    task_record_id: str
    extraction_task_id: str
    methodology_question_record_id: str
    request: Any


@dataclass(frozen=True, slots=True)
class _StagedExactTerminalSetWrite:
    """Step-7 exact-set proof executed by the ordinary COMPLETE transaction."""

    tenant_id: str
    deal_id: str
    run_id: str
    run_step_id: str
    expected_task_ids: tuple[str, ...]
    lease_owner_id: str
    lease_fencing_token: int

    def persist(
        self,
        *,
        db_conn: Any,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        if lease_owner_id != self.lease_owner_id or lease_fencing_token != self.lease_fencing_token:
            raise MethodologyPostgresRuntimeError("WORKER_LEASE_FENCED")
        repository = PostgresMethodologyExtractionRepository(db_conn, self.tenant_id)
        repository.complete_execution_step_exact_set(
            deal_id=self.deal_id,
            run_id=self.run_id,
            run_step_id=self.run_step_id,
            expected_task_ids=self.expected_task_ids,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )


@dataclass(frozen=True, slots=True)
class _VerifiedBudgetRuntime:
    envelope: Any
    trusted_signer_id: str
    trusted_public_key_sha256: str
    global_max_calls: int


class PostgresMethodologyExecutionBoundary:
    """Short-transaction persistence port used by the phased coordinator."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        tenant_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        audit_sink: Any,
        strict_profile: Any,
    ) -> None:
        self._connection_factory = connection_factory
        self._tenant_id = tenant_id
        self._lease_owner_id = lease_owner_id
        self._lease_fencing_token = lease_fencing_token
        self._audit_sink = audit_sink
        self._budget_runtime = _load_verified_budget_runtime(strict_profile, tenant_id)
        self._execution_inputs: tuple[MethodologyExecutionInput, ...] = ()

    def load_execution_inputs(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        tasks: list[Any],
        documents: list[dict[str, Any]],
        **_: Any,
    ) -> list[MethodologyExecutionInput]:
        _require_scope(tenant_id, self._tenant_id)
        expected_task_ids = _identifier_set(
            getattr(task, "extraction_task_id", None) for task in tasks
        )
        if not expected_task_ids:
            raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            extraction_repository = PostgresMethodologyExtractionRepository(connection, tenant_id)
            methodology_repository = get_methodologies_repository(connection, tenant_id)
            document_repository = PostgresDocumentsRepository(connection, tenant_id)
            selection = methodology_repository.load_run_selection(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
            )
            if selection is None:
                raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
            version_records: dict[str, Any] = {}
            for record_id in (
                selection.commercial_dd_version_record_id,
                selection.financial_dd_version_record_id,
            ):
                version_record = methodology_repository.get_frozen_version(
                    methodology_version_record_id=record_id
                )
                if version_record is None:
                    raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
                version_records[record_id] = version_record
            questions: dict[tuple[str, str], tuple[Any, str]] = {}
            for record_id, version in version_records.items():
                hashes = methodology_question_payload_hashes(version.registry)
                for question, question_hash in zip(
                    version.registry.current_version.questions,
                    hashes,
                    strict=True,
                ):
                    questions[(record_id, question.methodology_question_id)] = (
                        question,
                        question_hash,
                    )
            records = extraction_repository.load_planned_task_records(
                deal_id=deal_id,
                run_id=run_id,
            )
            if _identifier_set(
                record.task.extraction_task_id for record in records
            ) != expected_task_ids or any(
                record.selection_id != selection.selection_id for record in records
            ):
                raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
            built: list[MethodologyExecutionInput] = []
            for record in records:
                key = (
                    record.methodology_version_record_id,
                    record.task.methodology_question_id,
                )
                question_values = questions.get(key)
                selected_version = version_records.get(record.methodology_version_record_id)
                if question_values is None or selected_version is None:
                    raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
                question, question_hash = question_values
                admitted_span_ids = {span.span_id for span in record.task.source_spans}
                live_spans = [
                    span
                    for span in document_repository.list_spans_by_document(
                        deal_id=deal_id,
                        document_id=str(record.task.document_id),
                    )
                    if span["span_id"] in admitted_span_ids
                ]
                from idis.pipeline.worker import _build_methodology_extractor_request

                request = _build_methodology_extractor_request(
                    task_record=record,
                    question=question,
                    version_record=selected_version,
                    methodology_question_sha256=question_hash,
                    live_spans=live_spans,
                )
                built.append(
                    MethodologyExecutionInput(
                        task_record_id=record.task_record_id,
                        extraction_task_id=str(record.task.extraction_task_id),
                        methodology_question_record_id=(record.methodology_question_record_id),
                        request=request,
                    )
                )
        self._execution_inputs = tuple(sorted(built, key=lambda item: item.extraction_task_id))
        return list(self._execution_inputs)

    def load_started_attempts(
        self, *, tenant_id: str, deal_id: str, run_id: str, **_: Any
    ) -> list[dict[str, Any]]:
        _require_scope(tenant_id, self._tenant_id)
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            return PostgresMethodologyExtractionRepository(
                connection, tenant_id
            ).load_started_attempts(deal_id=deal_id, run_id=run_id)

    def mark_started_attempts_outcome_unknown(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        attempts: list[Mapping[str, Any]],
        **_: Any,
    ) -> None:
        _require_scope(tenant_id, self._tenant_id)
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            repository = PostgresMethodologyExtractionRepository(connection, tenant_id)
            for attempt in attempts:
                repository.mark_attempt_outcome_unknown(
                    deal_id=deal_id,
                    run_id=run_id,
                    attempt_id=str(attempt["attempt_id"]),
                    run_step_id=run_step_id,
                    lease_owner_id=self._lease_owner_id,
                    lease_fencing_token=self._lease_fencing_token,
                )
                _emit_methodology_audit_in_tx(
                    connection=connection,
                    audit_sink=self._audit_sink,
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    event_type="deal.run.methodology_attempt.outcome_unknown",
                    summary="Methodology attempt outcome became ambiguous",
                    status_code=409,
                    safe={
                        "attempt_id": str(attempt["attempt_id"]),
                        "run_step_id": run_step_id,
                        "code": "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN",
                    },
                )

    def mark_admitted_call_outcome_unknown(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        admission: MethodologyCallAdmissionProof,
        **_: Any,
    ) -> None:
        """Persist an ambiguous post-admission transport outcome immediately."""
        _require_scope(tenant_id, self._tenant_id)
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            PostgresMethodologyExtractionRepository(
                connection, tenant_id
            ).mark_attempt_outcome_unknown(
                deal_id=deal_id,
                run_id=run_id,
                attempt_id=admission.attempt_id,
                run_step_id=run_step_id,
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
            )
            _emit_methodology_audit_in_tx(
                connection=connection,
                audit_sink=self._audit_sink,
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                event_type="deal.run.methodology_attempt.outcome_unknown",
                summary="Methodology provider call outcome became ambiguous",
                status_code=409,
                safe={
                    "attempt_id": admission.attempt_id,
                    "run_step_id": run_step_id,
                    "code": "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN",
                    "budget_terminal_state": "RESERVED",
                },
            )

    def fail_provider_request_not_accepted(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        prepared: Any,
        admission: MethodologyCallAdmissionProof,
        nonacceptance: MethodologyProviderRequestNotAccepted,
        **_: Any,
    ) -> None:
        """Atomically refund an explicitly rejected request and fail Step 7."""
        _require_scope(tenant_id, self._tenant_id)
        if (
            admission.prepared_call_sha256 != prepared.prepared_call_sha256
            or admission.budget_reservation_id != prepared.budget_reservation_id
            or admission.budget_policy_id != prepared.budget_policy_id
            or nonacceptance.code != "METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED"
        ):
            raise MethodologyPostgresRuntimeError("METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN")
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            vector = self._budget_vector(
                policy_hash=admission.budget_policy_id,
                reservation_id=prepared.budget_reservation_id,
                reservation=prepared.reservation,
            )
            self._budget_store().refund_in_tx(connection, **vector)
            PostgresMethodologyExtractionRepository(connection, tenant_id).finalize_attempt_failure(
                deal_id=deal_id,
                run_id=run_id,
                attempt_id=admission.attempt_id,
                run_step_id=run_step_id,
                budget_reservation_id=prepared.budget_reservation_id,
                budget_policy_id=admission.budget_policy_id,
                safe_failure_code="METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED",
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
            )
            _emit_methodology_audit_in_tx(
                connection=connection,
                audit_sink=self._audit_sink,
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                event_type=("deal.run.methodology_attempt.provider_request_not_accepted"),
                summary="Methodology provider explicitly rejected the request",
                status_code=nonacceptance.provider_status_code,
                safe={
                    "attempt_id": admission.attempt_id,
                    "run_step_id": run_step_id,
                    "code": "METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED",
                    "budget_terminal_state": "REFUNDED",
                    "nonacceptance_proof_kind": nonacceptance.proof_kind,
                    "provider_status_code": nonacceptance.provider_status_code,
                },
            )

    def load_active_budget_policy(
        self, *, tenant_id: str, **_: Any
    ) -> VerifiedActiveProviderBudgetPolicy:
        _require_scope(tenant_id, self._tenant_id)
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            return self._load_policy(connection)

    def load_committed_responses(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        **_: Any,
    ) -> dict[str, MethodologyTaskExtractorResponse]:
        _require_scope(tenant_id, self._tenant_id)
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            repository = PostgresMethodologyExtractionRepository(connection, tenant_id)
            non_success = repository.load_non_success_attempts(
                deal_id=deal_id,
                run_id=run_id,
            )
            if non_success:
                state = str(non_success[0].get("status", ""))
                code = (
                    "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN"
                    if state == "OUTCOME_UNKNOWN"
                    else str(non_success[0].get("safe_failure_code") or "")
                )
                if _SAFE_CODE.fullmatch(code) is None:
                    code = "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"
                raise MethodologyPostgresRuntimeError(code)
            rows = repository.load_terminal_execution_records(
                deal_id=deal_id,
                run_id=run_id,
            )
        return _responses_from_terminal_rows(rows)

    def admit_call(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        execution_input: MethodologyExecutionInput,
        prepared: Any,
        verified_budget_policy: VerifiedActiveProviderBudgetPolicy,
        **_: Any,
    ) -> MethodologyCallAdmissionProof:
        _require_scope(tenant_id, self._tenant_id)
        reservation = prepared.reservation
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            policy = self._load_policy(connection)
            if (
                verified_budget_policy != policy
                or str(prepared.budget_policy_id) != policy.policy_hash
            ):
                raise MethodologyPostgresRuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
            vector = self._budget_vector(
                policy_hash=policy.policy_hash,
                reservation_id=prepared.budget_reservation_id,
                reservation=reservation,
            )
            if not self._budget_store().reserve_in_tx(connection, **vector):
                raise MethodologyPostgresRuntimeError(provider_budget_exceeded_error_code())
            repository = PostgresMethodologyExtractionRepository(connection, tenant_id)
            attempt = repository.admit_attempt(
                deal_id=deal_id,
                run_id=run_id,
                run_step_id=run_step_id,
                task_record_id=execution_input.task_record_id,
                task_input_sha256=prepared.request.task_input_sha256,
                prepared_call_sha256=prepared.prepared_call_sha256,
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
                provider="anthropic",
                backend="live",
                transport_kind="LIVE",
                configured_model=prepared.model,
                prompt_id=prepared.prompt_artifact.prompt_id,
                prompt_version=prepared.prompt_artifact.version,
                prompt_content_sha256=prepared.prompt_artifact.content_sha256,
                prompt_input_schema_sha256=(prepared.prompt_artifact.input_schema_sha256),
                prompt_output_schema_sha256=(prepared.prompt_artifact.output_schema_sha256),
                request_payload_sha256=reservation.billable_payload_sha256,
                billable_payload_sha256=reservation.billable_payload_sha256,
                billable_payload_utf8_bytes=reservation.billable_payload_utf8_bytes,
                configured_max_tokens=prepared.max_tokens,
                configured_temperature=prepared.temperature,
                configured_json_mode=True,
                budget_policy_id=policy.policy_hash,
                verified_budget_policy=verified_budget_policy,
                budget_component="anthropic",
                budget_reservation_id=prepared.budget_reservation_id,
                price_catalog_version=reservation.price_catalog_version,
                price_catalog_row_sha256=reservation.price_catalog_row_sha256,
                input_usd_per_million_tokens=(reservation.input_usd_per_million_tokens),
                output_usd_per_million_tokens=(reservation.output_usd_per_million_tokens),
                reserved_calls=1,
                reserved_input_tokens=reservation.reserved_input_tokens,
                reserved_output_tokens=reservation.reserved_output_tokens,
                reserved_usd=reservation.worst_case_usd,
            )
            _emit_methodology_audit_in_tx(
                connection=connection,
                audit_sink=self._audit_sink,
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                event_type="deal.run.methodology_attempt.admitted",
                summary="Methodology provider call admitted",
                status_code=201,
                safe={
                    "attempt_id": str(attempt["attempt_id"]),
                    "task_record_id": execution_input.task_record_id,
                    "extraction_task_id": execution_input.extraction_task_id,
                    "run_step_id": run_step_id,
                    "budget_policy_id": policy.policy_hash,
                    "budget_reservation_id": prepared.budget_reservation_id,
                    "prepared_call_sha256": prepared.prepared_call_sha256,
                },
            )
            proof_values = {
                "backend": "postgresql",
                "committed": True,
                "attempt_id": str(attempt["attempt_id"]),
                "budget_reservation_id": prepared.budget_reservation_id,
                "prepared_call_sha256": prepared.prepared_call_sha256,
                "task_input_sha256": prepared.request.task_input_sha256,
                "price_catalog_row_sha256": str(attempt["price_catalog_row_sha256"]),
                "worker_fencing_token": self._lease_fencing_token,
                "budget_policy_id": policy.policy_hash,
            }
        return MethodologyCallAdmissionProof.model_validate(proof_values)

    def fail_received_response(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        dispatched: Any,
        safe_failure_code: str,
        **_: Any,
    ) -> None:
        _require_scope(tenant_id, self._tenant_id)
        code = (
            safe_failure_code
            if _SAFE_CODE.fullmatch(safe_failure_code)
            else "METHODOLOGY_PROVIDER_RESULT_INVALID"
        )
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            vector = self._budget_vector(
                policy_hash=dispatched.admission.budget_policy_id,
                reservation_id=dispatched.prepared_call.budget_reservation_id,
                reservation=dispatched.prepared_call.reservation,
            )
            self._budget_store().consume_in_tx(connection, **vector)
            PostgresMethodologyExtractionRepository(
                connection, tenant_id
            ).finalize_received_response_failure(
                deal_id=deal_id,
                run_id=run_id,
                run_step_id=run_step_id,
                attempt_id=dispatched.admission.attempt_id,
                budget_reservation_id=(dispatched.prepared_call.budget_reservation_id),
                budget_policy_id=dispatched.admission.budget_policy_id,
                observed_model=_returned_text_or_none(dispatched.observed_model),
                provider_request_id=_returned_text_or_none(dispatched.provider_request_id),
                provider_response_sha256=dispatched.provider_response_sha256,
                dispatched_call_sha256=dispatched.dispatched_call_sha256,
                latency_ms=dispatched.latency_ms,
                input_tokens=_returned_usage_or_none(dispatched.input_tokens),
                output_tokens=_returned_usage_or_none(dispatched.output_tokens),
                stop_reason=_returned_text_or_none(dispatched.stop_reason),
                safe_failure_code=code,
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
            )
            _emit_methodology_audit_in_tx(
                connection=connection,
                audit_sink=self._audit_sink,
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                event_type="deal.run.methodology_attempt.failed",
                summary="Methodology provider response failed validation",
                status_code=422,
                safe={
                    "attempt_id": dispatched.admission.attempt_id,
                    "run_step_id": run_step_id,
                    "code": code,
                    "budget_terminal_state": "CONSUMED",
                    "provider_response_sha256": dispatched.provider_response_sha256,
                    "dispatched_call_sha256": dispatched.dispatched_call_sha256,
                },
            )

    def terminalize_call(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        dispatched: Any,
        validated_result: ValidatedDispatchedMethodologyResult,
        prospective_receipt_sha256: str,
        **_: Any,
    ) -> MethodologyCallTerminalizationProof:
        _require_scope(tenant_id, self._tenant_id)
        with self._connection_factory() as connection:
            set_tenant_local(connection, tenant_id)
            vector = self._budget_vector(
                policy_hash=dispatched.admission.budget_policy_id,
                reservation_id=dispatched.prepared_call.budget_reservation_id,
                reservation=dispatched.prepared_call.reservation,
            )
            self._budget_store().consume_in_tx(connection, **vector)
            durable = PostgresMethodologyExtractionRepository(
                connection, tenant_id
            ).finalize_attempt_success(
                deal_id=deal_id,
                run_id=run_id,
                run_step_id=run_step_id,
                attempt_id=dispatched.admission.attempt_id,
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
                observed_model=dispatched.observed_model,
                provider_request_id=dispatched.provider_request_id,
                provider_response_sha256=dispatched.provider_response_sha256,
                dispatched_call_sha256=dispatched.dispatched_call_sha256,
                validated_result_sha256=(validated_result.validated_result_sha256),
                prospective_receipt_sha256=prospective_receipt_sha256,
                budget_policy_id=dispatched.admission.budget_policy_id,
                latency_ms=dispatched.latency_ms,
                input_tokens=dispatched.input_tokens,
                output_tokens=dispatched.output_tokens,
                stop_reason=dispatched.stop_reason,
                result=validated_result,
            )
            _emit_methodology_audit_in_tx(
                connection=connection,
                audit_sink=self._audit_sink,
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                event_type="deal.run.methodology_attempt.terminalized",
                summary="Methodology provider result committed",
                status_code=200,
                safe={
                    "attempt_id": durable.attempt_id,
                    "result_id": durable.result_id,
                    "run_step_id": run_step_id,
                    "result_sha256": durable.result_sha256,
                    "receipt_sha256": durable.receipt_sha256,
                    "budget_terminal_state": durable.budget_terminal_state,
                },
            )
            proof_values = {
                "backend": "postgresql",
                "committed": True,
                "attempt_id": durable.attempt_id,
                "budget_reservation_id": durable.budget_reservation_id,
                "prepared_call_sha256": durable.prepared_call_sha256,
                "dispatched_call_sha256": durable.dispatched_call_sha256,
                "validated_result_sha256": durable.validated_result_sha256,
                "prospective_receipt_sha256": (durable.prospective_receipt_sha256),
                "task_input_sha256": durable.task_input_sha256,
                "price_catalog_row_sha256": durable.price_catalog_row_sha256,
                "worker_fencing_token": durable.worker_fencing_token,
                "budget_terminal_state": durable.budget_terminal_state,
                "budget_policy_id": durable.budget_policy_id,
                "consumed_calls": durable.consumed_calls,
                "consumed_input_tokens": durable.consumed_input_tokens,
                "consumed_output_tokens": durable.consumed_output_tokens,
                "consumed_usd": durable.consumed_usd,
                "terminalization_proof_sha256": durable.proof_sha256,
            }
        return MethodologyCallTerminalizationProof.model_validate(proof_values)

    def stage_exact_terminal_set(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        expected_task_ids: tuple[str, ...],
        responses_by_task_id: Mapping[str, MethodologyTaskExtractorResponse],
        **_: Any,
    ) -> _StagedExactTerminalSetWrite:
        _require_scope(tenant_id, self._tenant_id)
        validate_step7_exact_terminal_set(
            selected_question_ids=tuple(
                sorted({item.methodology_question_record_id for item in self._execution_inputs})
            ),
            planned_task_question_ids={
                item.extraction_task_id: item.methodology_question_record_id
                for item in self._execution_inputs
            },
            attempt_task_ids=tuple(responses_by_task_id),
            result_task_ids=tuple(responses_by_task_id),
        )
        return _StagedExactTerminalSetWrite(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            run_step_id=run_step_id,
            expected_task_ids=expected_task_ids,
            lease_owner_id=self._lease_owner_id,
            lease_fencing_token=self._lease_fencing_token,
        )

    def build_execution_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        responses_by_task_id: Mapping[str, MethodologyTaskExtractorResponse],
        **_: Any,
    ) -> DurableMethodologyExtractionExecutionResult:
        planned = {
            item.extraction_task_id: item.methodology_question_record_id
            for item in self._execution_inputs
        }
        return DurableMethodologyExtractionExecutionResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            selected_question_ids=tuple(sorted(set(planned.values()))),
            planned_task_question_ids=planned,
            attempt_task_ids=tuple(sorted(responses_by_task_id)),
            result_task_ids=tuple(sorted(responses_by_task_id)),
            terminal_results_by_task_id=dict(responses_by_task_id),
        )

    def _load_policy(self, connection: Any) -> VerifiedActiveProviderBudgetPolicy:
        runtime = self._budget_runtime
        return PostgresProviderBudgetPoliciesRepository(
            connection,
            self._tenant_id,
            tenant_context_is_set=True,
        ).load_active_verified_policy(
            verified_envelope=runtime.envelope,
            trusted_signer_id=runtime.trusted_signer_id,
            trusted_public_key_sha256=runtime.trusted_public_key_sha256,
            expected_imported_by_actor_id=_GOVERNANCE_IMPORT_ACTOR,
        )

    def _budget_store(self) -> PostgresProviderBudgetStore:
        return PostgresProviderBudgetStore(global_max_calls=self._budget_runtime.global_max_calls)

    def _budget_vector(
        self,
        *,
        policy_hash: str,
        reservation_id: str,
        reservation: Any,
    ) -> dict[str, object]:
        return {
            "tenant_id": self._tenant_id,
            "policy_hash": policy_hash,
            "component_id": "anthropic",
            "calls": 1,
            "usd": Decimal(reservation.worst_case_usd),
            "input_tokens": int(reservation.reserved_input_tokens),
            "output_tokens": int(reservation.reserved_output_tokens),
            "embedding_tokens": 0,
            "global_max_calls": self._budget_runtime.global_max_calls,
            "reservation_id": reservation_id,
        }


def execute_postgres_methodology_tasks(
    *,
    connection_factory: Callable[[], Any],
    configured_tenant_id: str,
    lease_owner_id: str,
    lease_fencing_token: int,
    audit_sink: Any,
    strict_profile: Any,
    **kwargs: Any,
) -> tuple[Any, Any, Any]:
    """Compose the PROD-only real extractor and execute through durable boundaries."""
    from idis.services.extraction.methodology_task_extractor import (
        ANTHROPIC_STANDARD_PRICE_CATALOG,
        AnthropicMethodologyTaskExtractor,
        AnthropicSDKMethodologyTransport,
        resolve_strict_methodology_prompt,
    )
    from idis.services.runs.methodology_extraction_task_execution import (
        DurableRunMethodologyExtractionTaskExecutionService,
    )

    if strict_profile is None:
        raise MethodologyPostgresRuntimeError("STRICT_PROFILE_LOAD_FAILED")
    values = strict_profile.values
    if str(values.get("IDIS_EXTRACT_BACKEND", "")) != "anthropic":
        raise MethodologyPostgresRuntimeError("EXTRACTOR_UNAVAILABLE")
    prompt = resolve_strict_methodology_prompt(
        prompts_root=Path(__file__).resolve().parents[4] / "prompts"
    )
    extractor = AnthropicMethodologyTaskExtractor(
        transport=AnthropicSDKMethodologyTransport(
            api_key=str(values.get("ANTHROPIC_API_KEY", ""))
        ),
        prompt_artifact=prompt,
        configured_model=str(values.get("IDIS_ANTHROPIC_MODEL_EXTRACT", "")),
        max_tokens=_METHODOLOGY_MAX_TOKENS,
        temperature="0",
        price_catalog=ANTHROPIC_STANDARD_PRICE_CATALOG,
    )
    if extractor.external_calls_enabled is not True:
        raise MethodologyPostgresRuntimeError("EXTRACTOR_UNAVAILABLE")
    boundary = PostgresMethodologyExecutionBoundary(
        connection_factory=connection_factory,
        tenant_id=configured_tenant_id,
        lease_owner_id=lease_owner_id,
        lease_fencing_token=lease_fencing_token,
        audit_sink=audit_sink,
        strict_profile=strict_profile,
    )
    return DurableRunMethodologyExtractionTaskExecutionService(
        extractor=extractor,
        boundary=boundary,
    ).run(**kwargs)


def rehydrate_postgres_methodology_execution(
    *,
    connection_factory: Callable[[], Any],
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> tuple[Any, Any]:
    """Rebuild typed Step-7 state only from committed PostgreSQL rows."""
    with connection_factory() as connection:
        set_tenant_local(connection, tenant_id)
        repository = PostgresMethodologyExtractionRepository(connection, tenant_id)
        tasks = repository.load_planned_task_records(deal_id=deal_id, run_id=run_id)
        non_success = repository.load_non_success_attempts(deal_id=deal_id, run_id=run_id)
        terminal_rows = repository.load_terminal_execution_records(
            deal_id=deal_id,
            run_id=run_id,
        )
        repository.require_coverage_projection(
            deal_id=deal_id,
            run_id=run_id,
            stage="step7",
        )
    if non_success:
        raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
    responses = _responses_from_terminal_rows(terminal_rows)
    planned = {
        str(record.task.extraction_task_id): record.methodology_question_record_id
        for record in tasks
    }
    result = DurableMethodologyExtractionExecutionResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        selected_question_ids=tuple(sorted(set(planned.values()))),
        planned_task_question_ids=planned,
        attempt_task_ids=tuple(sorted(responses)),
        result_task_ids=tuple(sorted(responses)),
        terminal_results_by_task_id=responses,
    )
    return result, result


def _responses_from_terminal_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, MethodologyTaskExtractorResponse]:
    responses: dict[str, MethodologyTaskExtractorResponse] = {}
    for row in rows:
        payload = row.get("result_payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        validated = ValidatedDispatchedMethodologyResult.model_validate(payload)
        if validated.validated_result_sha256 != str(row["result_sha256"]):
            raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
        receipt_values = {
            "provider": row["provider"],
            "backend": row["backend"],
            "configured_model": row["configured_model"],
            "observed_model": row["observed_model"],
            "prompt_id": row["prompt_id"],
            "prompt_version": row["prompt_version"],
            "prompt_content_sha256": row["prompt_content_sha256"],
            "prompt_input_schema_sha256": row["prompt_input_schema_sha256"],
            "prompt_output_schema_sha256": row["prompt_output_schema_sha256"],
            "request_payload_sha256": row["billable_payload_sha256"],
            "provider_request_id": row["provider_request_id"],
            "provider_response_sha256": row["provider_response_sha256"],
            "real_call": row["real_call"],
            "configured_max_tokens": row["configured_max_tokens"],
            "configured_temperature": "0",
            "configured_json_mode": row["configured_json_mode"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "stop_reason": row["stop_reason"],
            "budget_policy_id": row["budget_policy_id"],
            "budget_component": row["budget_component"],
            "budget_reservation_id": row["budget_reservation_id"],
            "billable_payload_sha256": row["billable_payload_sha256"],
            "billable_payload_utf8_bytes": row["billable_payload_utf8_bytes"],
            "price_catalog_version": row["price_catalog_version"],
            "price_catalog_row_sha256": row["price_catalog_row_sha256"],
            "input_usd_per_million_tokens": _fixed_decimal_8(row["input_usd_per_million_tokens"]),
            "output_usd_per_million_tokens": _fixed_decimal_8(row["output_usd_per_million_tokens"]),
            "reserved_calls": row["reserved_calls"],
            "reserved_input_tokens": row["reserved_input_tokens"],
            "reserved_output_tokens": row["reserved_output_tokens"],
            "reserved_embedding_tokens": row["reserved_embedding_tokens"],
            "reserved_usd": _fixed_decimal_8(row["reserved_usd"]),
            "budget_terminal_state": row["budget_terminal_state"],
        }
        expected_receipt_sha256 = compute_methodology_provider_receipt_sha256(
            receipt_fields=receipt_values,
            validated_result_sha256=validated.validated_result_sha256,
        )
        if expected_receipt_sha256 != str(row["receipt_sha256"]):
            raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
        receipt = MethodologyProviderReceipt.model_validate(
            {**receipt_values, "receipt_sha256": expected_receipt_sha256}
        )
        task_id = str(row["extraction_task_id"])
        if task_id in responses:
            raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
        responses[task_id] = MethodologyTaskExtractorResponse(
            outcome=validated.outcome,
            searched_document_ids=validated.searched_document_ids,
            searched_span_ids=validated.searched_span_ids,
            searched_scope_sha256=validated.searched_scope_sha256,
            factual_outputs=validated.factual_outputs,
            gap_finding=validated.gap_finding,
            provider_receipt=receipt,
        )
    return responses


def _load_verified_budget_runtime(
    strict_profile: Any,
    tenant_id: str,
) -> _VerifiedBudgetRuntime:
    if strict_profile is None:
        raise MethodologyPostgresRuntimeError("STRICT_PROFILE_LOAD_FAILED")
    values = strict_profile.values
    try:
        public_key = secure_read_integrity_file(
            str(values.get("IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH", "")),
            max_bytes=65_536,
            require_readonly=True,
            require_secure_parent=True,
            require_owner_only=True,
        )
        public_key_hash = hashlib.sha256(public_key).hexdigest()
        if not secrets.compare_digest(
            public_key_hash,
            str(strict_profile.governance_public_key_sha256),
        ):
            raise ValueError
        envelope_bytes = secure_read_integrity_file(
            str(values.get("IDIS_PROVIDER_BUDGET_POLICY_PATH", "")),
            max_bytes=1_048_576,
            require_readonly=True,
            require_secure_parent=True,
            require_owner_only=True,
        )
        envelope = json.loads(envelope_bytes.decode("utf-8"))
        verified = verify_envelope(
            envelope=envelope,
            trusted_signer_id=str(values.get("IDIS_GOVERNANCE_TRUSTED_SIGNER_ID", "")),
            trusted_public_key_pem=public_key,
        )
        global_max_calls = int(values.get("IDIS_PROVIDER_BUDGET_MAX_CALLS", "0"))
        if (
            verified.schema_version != BUDGET_SCHEMA_VERSION
            or verified.payload.get("tenant_id") != tenant_id
            or global_max_calls <= 0
        ):
            raise ValueError
    except Exception as exc:
        raise MethodologyPostgresRuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED") from exc
    return _VerifiedBudgetRuntime(
        envelope=verified,
        trusted_signer_id=str(values["IDIS_GOVERNANCE_TRUSTED_SIGNER_ID"]),
        trusted_public_key_sha256=public_key_hash,
        global_max_calls=global_max_calls,
    )


def _emit_methodology_audit_in_tx(
    *,
    connection: Any,
    audit_sink: Any,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    event_type: str,
    summary: str,
    status_code: int,
    safe: Mapping[str, Any],
) -> None:
    event = build_canonical_audit_event(
        tenant_id=tenant_id,
        event_type=event_type,
        severity="HIGH" if status_code >= 400 else "MEDIUM",
        summary=summary,
        actor_id="idis-pipeline-worker",
        request_id=run_id,
        method="POST",
        path=f"/v1/runs/{run_id}/methodology-extraction",
        status_code=status_code,
        resource_type="run",
        resource_id=run_id,
        deal_id=deal_id,
        safe=dict(safe),
    )
    validation = validate_audit_event(event)
    if not validation.passed:
        raise AuditSinkError("METHODOLOGY_AUDIT_INVALID")
    emit_in_tx = getattr(audit_sink, "emit_in_tx", None)
    if not callable(emit_in_tx):
        raise AuditSinkError("METHODOLOGY_AUDIT_SINK_UNAVAILABLE")
    emit_in_tx(connection, event)


def _identifier_set(values: Any) -> set[str]:
    identifiers = [str(value or "").strip() for value in values]
    if any(not value for value in identifiers) or len(identifiers) != len(set(identifiers)):
        raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
    return set(identifiers)


def _require_scope(actual: str, configured: str) -> None:
    if str(actual) != str(configured):
        raise MethodologyPostgresRuntimeError("WORKER_LEASE_FENCED")


def _fixed_decimal_8(value: object) -> str:
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise MethodologyPostgresRuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
    return f"{number.quantize(Decimal('0.00000001')):.8f}"


__all__ = [
    "MethodologyExecutionInput",
    "MethodologyPostgresRuntimeError",
    "PostgresMethodologyExecutionBoundary",
    "execute_postgres_methodology_tasks",
    "rehydrate_postgres_methodology_execution",
]
