"""Offline proof of fail_request_not_sent with recording collaborators and no database."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_socket import disable_socket

from idis.services.extraction.methodology_task_extractor import (
    METHODOLOGY_REQUEST_NOT_SENT_PROOFS,
    MethodologyProviderRequestNotAccepted,
    MethodologyRequestNotSent,
)
from idis.services.runs import methodology_postgres_runtime as runtime
from idis.services.runs.methodology_postgres_runtime import (
    MethodologyPostgresRuntimeError,
    PostgresMethodologyExecutionBoundary,
)

disable_socket()

_TENANT_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_TENANT_ID = "12121212-1212-4212-8212-121212121212"
_DEAL_ID = "22222222-2222-4222-8222-222222222222"
_RUN_ID = "33333333-3333-4333-8333-333333333333"
_RUN_STEP_ID = "step-methodology-execution"
_ATTEMPT_ID = "77777777-7777-4777-8777-777777777777"
_PREPARED_CALL_SHA256 = "a" * 64
_RESERVATION_ID = "88888888-8888-4888-8888-888888888888"
_OTHER_RESERVATION_ID = "99999999-9999-4999-8999-999999999999"
_POLICY_ID = "budget-policy-fictional-p8b"
_OTHER_POLICY_ID = "budget-policy-fictional-other"
_LEASE_OWNER_ID = "55555555-5555-4555-8555-555555555555"
_LEASE_FENCING_TOKEN = 7
_GLOBAL_MAX_CALLS = 11
_WORST_CASE_USD = "2.50000000"
_RESERVED_INPUT_TOKENS = 128
_RESERVED_OUTPUT_TOKENS = 256
_COLLABORATOR_ORDER = ("set_tenant_local", "refund", "finalize", "audit")
_REQUEST_NOT_SENT_EVENT = "deal.run.methodology_attempt.request_not_sent"
_NONACCEPTANCE_EVENT = "deal.run.methodology_attempt.provider_request_not_accepted"
_REQUEST_NOT_SENT_CODE = "METHODOLOGY_REQUEST_NOT_SENT"
_NONACCEPTANCE_CODE = "METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED"
_OUTCOME_UNKNOWN = "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN"
_SCOPE_FENCED = "WORKER_LEASE_FENCED"
_INVALID_PROOF_PAIRS = (
    ("METHODOLOGY_SDK_LOCAL_GUARD", "HIDDEN_RETRY_FORBIDDEN"),
    ("METHODOLOGY_HIDDEN_RETRY_FORBIDDEN", "UNPRICED_BATCH_OR_CACHE"),
    ("METHODOLOGY_UNPRICED_REQUEST_MODE", "CLIENT_UNAVAILABLE"),
    ("METHODOLOGY_NOT_A_CLOSED_CODE", "NOT_A_CLOSED_REASON"),
)


class _ProbeError(RuntimeError):
    pass


class _RecordingConnection:
    def __init__(self) -> None:
        self.entered = 0
        self.exit_exceptions: list[BaseException | None] = []

    def __enter__(self) -> _RecordingConnection:
        self.entered += 1
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        self.exit_exceptions.append(exc)
        return False


class _RecordingConnectionFactory:
    def __init__(self) -> None:
        self.connections: list[_RecordingConnection] = []

    def __call__(self) -> _RecordingConnection:
        connection = _RecordingConnection()
        self.connections.append(connection)
        return connection


class _RecordingBudgetStore:
    def __init__(self, harness: _Harness) -> None:
        self._harness = harness

    def __call__(self) -> _RecordingBudgetStore:
        return self

    def refund_in_tx(self, connection: Any, **vector: Any) -> None:
        self._harness.order.append("refund")
        self._harness.refunds.append((connection, dict(vector)))
        if self._harness.refund_error is not None:
            raise self._harness.refund_error


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.order: list[str] = []
        self.tenant_calls: list[tuple[Any, str]] = []
        self.refunds: list[tuple[Any, dict[str, Any]]] = []
        self.finalizes: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []
        self.repository_builds: list[tuple[Any, str]] = []
        self.refund_error: BaseException | None = None
        self.finalize_error: BaseException | None = None
        self.audit_error: BaseException | None = None
        self.factory = _RecordingConnectionFactory()
        self.audit_sink = object()
        self.store = _RecordingBudgetStore(self)
        self._install(monkeypatch)
        boundary = object.__new__(PostgresMethodologyExecutionBoundary)
        boundary._connection_factory = self.factory
        boundary._tenant_id = _TENANT_ID
        boundary._lease_owner_id = _LEASE_OWNER_ID
        boundary._lease_fencing_token = _LEASE_FENCING_TOKEN
        boundary._audit_sink = self.audit_sink
        boundary._budget_runtime = SimpleNamespace(global_max_calls=_GLOBAL_MAX_CALLS)
        boundary._budget_store = self.store
        self.boundary = boundary

    def _install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        harness = self

        def record_tenant(connection: Any, tenant_id: str) -> None:
            harness.order.append("set_tenant_local")
            harness.tenant_calls.append((connection, tenant_id))

        class _RecordingRepository:
            def __init__(self, connection: Any, tenant_id: str) -> None:
                harness.repository_builds.append((connection, tenant_id))

            def finalize_attempt_failure(self, **kwargs: Any) -> None:
                harness.order.append("finalize")
                harness.finalizes.append(dict(kwargs))
                if harness.finalize_error is not None:
                    raise harness.finalize_error

        def record_audit(**kwargs: Any) -> None:
            recorded = dict(kwargs)
            safe = recorded.get("safe")
            if isinstance(safe, dict):
                recorded["safe"] = dict(safe)
            harness.order.append("audit")
            harness.audits.append(recorded)
            if harness.audit_error is not None:
                raise harness.audit_error

        monkeypatch.setattr(runtime, "set_tenant_local", record_tenant)
        monkeypatch.setattr(
            runtime, "PostgresMethodologyExtractionRepository", _RecordingRepository
        )
        monkeypatch.setattr(runtime, "_emit_methodology_audit_in_tx", record_audit)

    def call_not_sent(
        self,
        *,
        tenant_id: str = _TENANT_ID,
        prepared: SimpleNamespace | None = None,
        admission: SimpleNamespace | None = None,
        request_not_sent: Any = None,
    ) -> None:
        self.boundary.fail_request_not_sent(
            tenant_id=tenant_id,
            deal_id=_DEAL_ID,
            run_id=_RUN_ID,
            run_step_id=_RUN_STEP_ID,
            prepared=_prepared() if prepared is None else prepared,
            admission=_admission() if admission is None else admission,
            request_not_sent=(_closed_request() if request_not_sent is None else request_not_sent),
        )

    def call_not_accepted(
        self,
        *,
        prepared: SimpleNamespace,
        admission: SimpleNamespace,
        nonacceptance: MethodologyProviderRequestNotAccepted,
    ) -> None:
        self.boundary.fail_provider_request_not_accepted(
            tenant_id=_TENANT_ID,
            deal_id=_DEAL_ID,
            run_id=_RUN_ID,
            run_step_id=_RUN_STEP_ID,
            prepared=prepared,
            admission=admission,
            nonacceptance=nonacceptance,
        )


def _prepared() -> SimpleNamespace:
    return SimpleNamespace(
        prepared_call_sha256=_PREPARED_CALL_SHA256,
        budget_reservation_id=_RESERVATION_ID,
        budget_policy_id=_POLICY_ID,
        reservation=SimpleNamespace(
            worst_case_usd=_WORST_CASE_USD,
            reserved_input_tokens=_RESERVED_INPUT_TOKENS,
            reserved_output_tokens=_RESERVED_OUTPUT_TOKENS,
        ),
    )


def _admission(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "prepared_call_sha256": _PREPARED_CALL_SHA256,
        "budget_reservation_id": _RESERVATION_ID,
        "budget_policy_id": _POLICY_ID,
        "attempt_id": _ATTEMPT_ID,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _closed_request(
    code: str = "METHODOLOGY_SDK_LOCAL_GUARD",
    reason: str = "SDK_LOCAL_GUARD",
) -> MethodologyRequestNotSent:
    return MethodologyRequestNotSent(code=code, reason=reason)


def _expected_vector(prepared: SimpleNamespace, admission: SimpleNamespace) -> dict[str, object]:
    reservation = prepared.reservation
    return {
        "tenant_id": _TENANT_ID,
        "policy_hash": admission.budget_policy_id,
        "component_id": "anthropic",
        "calls": 1,
        "usd": Decimal(reservation.worst_case_usd),
        "input_tokens": int(reservation.reserved_input_tokens),
        "output_tokens": int(reservation.reserved_output_tokens),
        "embedding_tokens": 0,
        "global_max_calls": _GLOBAL_MAX_CALLS,
        "reservation_id": prepared.budget_reservation_id,
    }


def _expected_finalize(
    prepared: SimpleNamespace,
    admission: SimpleNamespace,
    *,
    safe_failure_code: str,
) -> dict[str, object]:
    return {
        "deal_id": _DEAL_ID,
        "run_id": _RUN_ID,
        "attempt_id": admission.attempt_id,
        "run_step_id": _RUN_STEP_ID,
        "budget_reservation_id": prepared.budget_reservation_id,
        "budget_policy_id": admission.budget_policy_id,
        "safe_failure_code": safe_failure_code,
        "lease_owner_id": _LEASE_OWNER_ID,
        "lease_fencing_token": _LEASE_FENCING_TOKEN,
    }


def _expected_request_safe(
    admission: SimpleNamespace,
    request_not_sent: Any,
) -> dict[str, object]:
    return {
        "attempt_id": admission.attempt_id,
        "run_step_id": _RUN_STEP_ID,
        "code": _REQUEST_NOT_SENT_CODE,
        "budget_terminal_state": "REFUNDED",
        "request_not_sent_reason": request_not_sent.reason,
        "request_not_sent_proof_kind": request_not_sent.proof_kind,
    }


def _opened(harness: _Harness) -> _RecordingConnection:
    assert len(harness.factory.connections) == 1
    connection = harness.factory.connections[0]
    assert connection.entered == 1
    return connection


def _assert_no_write(harness: _Harness) -> None:
    assert harness.factory.connections == []
    assert harness.order == harness.tenant_calls == []
    assert harness.refunds == harness.finalizes == harness.audits == []
    assert harness.repository_builds == []


def _assert_code(error: MethodologyPostgresRuntimeError, code: str) -> None:
    assert type(error) is MethodologyPostgresRuntimeError
    assert error.code == code
    assert str(error) == code


def _assert_request_not_sent_committed(
    harness: _Harness,
    prepared: SimpleNamespace,
    admission: SimpleNamespace,
    request_not_sent: Any,
) -> None:
    connection = _opened(harness)
    assert harness.order == list(_COLLABORATOR_ORDER)
    assert harness.tenant_calls == [(connection, _TENANT_ID)]
    assert len(harness.refunds) == 1
    assert harness.refunds[0] == (connection, _expected_vector(prepared, admission))
    assert harness.finalizes == [
        _expected_finalize(prepared, admission, safe_failure_code=_REQUEST_NOT_SENT_CODE)
    ]
    assert harness.repository_builds == [(connection, _TENANT_ID)]
    safe = _expected_request_safe(admission, request_not_sent)
    assert harness.audits == [
        {
            "connection": connection,
            "audit_sink": harness.audit_sink,
            "tenant_id": _TENANT_ID,
            "deal_id": _DEAL_ID,
            "run_id": _RUN_ID,
            "event_type": _REQUEST_NOT_SENT_EVENT,
            "summary": "Methodology request was not sent",
            "status_code": 409,
            "safe": safe,
        }
    ]
    assert connection.exit_exceptions == [None]


@pytest.mark.parametrize(
    "field",
    ["prepared_call_sha256", "budget_reservation_id", "budget_policy_id"],
)
def test_admission_binding_mismatch_refuses_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    """A prepared-call binding mismatch raises before a connection is opened."""
    mismatched = {
        "prepared_call_sha256": "b" * 64,
        "budget_reservation_id": _OTHER_RESERVATION_ID,
        "budget_policy_id": _OTHER_POLICY_ID,
    }
    harness = _Harness(monkeypatch)
    with pytest.raises(MethodologyPostgresRuntimeError) as raised:
        harness.call_not_sent(admission=_admission(**{field: mismatched[field]}))
    _assert_code(raised.value, _OUTCOME_UNKNOWN)
    _assert_no_write(harness)


def test_proof_kind_other_than_pre_send_refuses_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proof kind outside PRE_SEND_LOCAL_FAILURE raises before any write."""
    request_not_sent = SimpleNamespace(
        proof_kind="POST_SEND_AMBIGUOUS",
        code="METHODOLOGY_SDK_LOCAL_GUARD",
        reason="SDK_LOCAL_GUARD",
    )
    assert (request_not_sent.code, request_not_sent.reason) in METHODOLOGY_REQUEST_NOT_SENT_PROOFS
    harness = _Harness(monkeypatch)
    with pytest.raises(MethodologyPostgresRuntimeError) as raised:
        harness.call_not_sent(request_not_sent=request_not_sent)
    _assert_code(raised.value, _OUTCOME_UNKNOWN)
    _assert_no_write(harness)


@pytest.mark.parametrize(("code", "reason"), _INVALID_PROOF_PAIRS)
def test_proof_pair_outside_closed_set_refuses_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    reason: str,
) -> None:
    """A code and reason outside the closed set raises before any write."""
    assert (code, reason) not in METHODOLOGY_REQUEST_NOT_SENT_PROOFS
    request_not_sent = SimpleNamespace(
        proof_kind="PRE_SEND_LOCAL_FAILURE",
        code=code,
        reason=reason,
    )
    harness = _Harness(monkeypatch)
    with pytest.raises(MethodologyPostgresRuntimeError) as raised:
        harness.call_not_sent(request_not_sent=request_not_sent)
    _assert_code(raised.value, _OUTCOME_UNKNOWN)
    _assert_no_write(harness)


@pytest.mark.parametrize(("code", "reason"), sorted(METHODOLOGY_REQUEST_NOT_SENT_PROOFS))
def test_every_closed_proof_pair_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    reason: str,
) -> None:
    """Each closed pair refunds, finalizes, audits, and exits with no exception."""
    prepared = _prepared()
    admission = _admission()
    request_not_sent = _closed_request(code, reason)
    harness = _Harness(monkeypatch)
    harness.call_not_sent(
        prepared=prepared,
        admission=admission,
        request_not_sent=request_not_sent,
    )
    _assert_request_not_sent_committed(harness, prepared, admission, request_not_sent)


def test_tenant_that_fails_require_scope_refuses_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tenant outside the boundary scope raises before any write."""
    harness = _Harness(monkeypatch)
    with pytest.raises(MethodologyPostgresRuntimeError) as raised:
        harness.call_not_sent(tenant_id=_OTHER_TENANT_ID)
    _assert_code(raised.value, _SCOPE_FENCED)
    _assert_no_write(harness)


@pytest.mark.parametrize(
    ("slot", "order", "finalize_called", "audit_called"),
    [
        ("refund_error", ("set_tenant_local", "refund"), False, False),
        ("finalize_error", ("set_tenant_local", "refund", "finalize"), True, False),
        ("audit_error", _COLLABORATOR_ORDER, True, True),
    ],
)
def test_collaborator_failure_propagates_and_exits_with_it(
    monkeypatch: pytest.MonkeyPatch,
    slot: str,
    order: tuple[str, ...],
    finalize_called: bool,
    audit_called: bool,
) -> None:
    """A collaborator failure skips later writes and exits with that exception."""
    boom = _ProbeError(slot)
    harness = _Harness(monkeypatch)
    setattr(harness, slot, boom)
    with pytest.raises(_ProbeError) as raised:
        harness.call_not_sent()
    assert raised.value is boom
    assert _opened(harness).exit_exceptions == [boom]
    assert harness.order == list(order)
    assert bool(harness.finalizes) is finalize_called
    assert bool(harness.audits) is audit_called


def test_fail_request_not_sent_mirrors_nonacceptance_collaborators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sequence, vector, and lease match; code, event, status, safe, and summary differ."""
    prepared = _prepared()
    admission = _admission()
    request_not_sent = _closed_request()
    nonacceptance = MethodologyProviderRequestNotAccepted(provider_status_code=422)
    request_harness = _Harness(monkeypatch)
    request_harness.call_not_sent(
        prepared=prepared,
        admission=admission,
        request_not_sent=request_not_sent,
    )
    sibling_harness = _Harness(monkeypatch)
    sibling_harness.call_not_accepted(
        prepared=prepared,
        admission=admission,
        nonacceptance=nonacceptance,
    )

    assert request_harness.order == sibling_harness.order == list(_COLLABORATOR_ORDER)
    assert request_harness.refunds[0][1] == sibling_harness.refunds[0][1]
    assert request_harness.refunds[0][1] == _expected_vector(prepared, admission)
    request_finalize = dict(request_harness.finalizes[0])
    sibling_finalize = dict(sibling_harness.finalizes[0])
    assert request_finalize.pop("safe_failure_code") == _REQUEST_NOT_SENT_CODE
    assert sibling_finalize.pop("safe_failure_code") == _NONACCEPTANCE_CODE
    assert request_finalize == sibling_finalize
    assert request_finalize["lease_owner_id"] == _LEASE_OWNER_ID
    assert request_finalize["lease_fencing_token"] == _LEASE_FENCING_TOKEN

    request_audit = dict(request_harness.audits[0])
    sibling_audit = dict(sibling_harness.audits[0])
    request_connection = _opened(request_harness)
    sibling_connection = _opened(sibling_harness)
    assert request_audit.pop("connection") is request_connection
    assert sibling_audit.pop("connection") is sibling_connection
    assert request_audit.pop("audit_sink") is request_harness.audit_sink
    assert sibling_audit.pop("audit_sink") is sibling_harness.audit_sink
    assert request_audit.pop("event_type") == _REQUEST_NOT_SENT_EVENT
    assert sibling_audit.pop("event_type") == _NONACCEPTANCE_EVENT
    assert request_audit.pop("status_code") == 409
    assert sibling_audit.pop("status_code") == nonacceptance.provider_status_code
    assert request_audit.pop("safe") == _expected_request_safe(admission, request_not_sent)
    assert sibling_audit.pop("safe") == {
        "attempt_id": admission.attempt_id,
        "run_step_id": _RUN_STEP_ID,
        "code": _NONACCEPTANCE_CODE,
        "budget_terminal_state": "REFUNDED",
        "nonacceptance_proof_kind": nonacceptance.proof_kind,
        "provider_status_code": nonacceptance.provider_status_code,
    }
    assert request_audit.pop("summary") == "Methodology request was not sent"
    assert sibling_audit.pop("summary") == "Methodology provider explicitly rejected the request"
    assert request_audit == sibling_audit
    assert request_connection.exit_exceptions == [None]
    assert sibling_connection.exit_exceptions == [None]
