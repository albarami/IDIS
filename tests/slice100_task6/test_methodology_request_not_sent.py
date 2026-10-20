"""Offline proof that pre-send methodology failures refund instead of stranding."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from idis.models.methodology_extraction import (
    AnthropicMethodologyTool,
    AnthropicMethodologyToolChoice,
    AnthropicMethodologyWireRequest,
    MethodologyTaskExtractorRequest,
)
from idis.services.extraction.methodology_task_extractor import (
    AnthropicSDKMethodologyTransport,
    MethodologyExtractorConfigurationError,
    MethodologyProviderRequestNotAccepted,
    MethodologyRequestNotSent,
)
from idis.services.runs.methodology_extraction_task_execution import (
    METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN,
    METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED,
    METHODOLOGY_REQUEST_NOT_SENT,
    DurableMethodologyExtractionExecutionError,
    DurableRunMethodologyExtractionTaskExecutionService,
)
from tests.slice100_task6._support import (
    DEAL_ID,
    QUESTION_RECORD_ID,
    RUN_ID,
    TASK_ID,
    TASK_RECORD_ID,
    TENANT_ID,
    extractor_request_kwargs,
)

_MARKER = "local-failure-marker-must-not-surface"
_GUARD_PREFIX = "Streaming is required for operations that may take longer than 10 minutes"
_STEP_ID = "step-methodology-execution"
_FICTIONAL_API_KEY = "fictional-test-key"


def _wire_kwargs(*, max_tokens: int = 512) -> dict[str, Any]:
    wire_request = AnthropicMethodologyWireRequest(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        temperature=0.0,
        system="governed fictional system",
        messages=({"role": "user", "content": [{"type": "text", "text": "fictional"}]},),
        tools=(
            AnthropicMethodologyTool(
                name="methodology_result",
                description="Return one governed methodology extraction result.",
                input_schema={"type": "object"},
                strict=True,
            ),
        ),
        tool_choice=AnthropicMethodologyToolChoice(
            type="tool",
            name="methodology_result",
            disable_parallel_tool_use=True,
        ),
    )
    return wire_request.as_sdk_kwargs()


class _SdkRecorder:
    """Records factory and messages.create calls without performing I/O."""

    def __init__(self, create_error: BaseException | None = None) -> None:
        self.factory_calls = 0
        self.create_calls: list[dict[str, Any]] = []
        self._create_error = create_error

    def client_factory(self, *, api_key: str, max_retries: int) -> Any:
        self.factory_calls += 1
        assert api_key == _FICTIONAL_API_KEY
        assert max_retries == 0
        recorder = self

        class _Messages:
            def create(self, **kwargs: Any) -> Any:
                recorder.create_calls.append(kwargs)
                if recorder._create_error is not None:
                    raise recorder._create_error
                raise AssertionError("sdk create must not run")

        return SimpleNamespace(messages=_Messages())


def _transport(recorder: _SdkRecorder) -> AnthropicSDKMethodologyTransport:
    return AnthropicSDKMethodologyTransport(
        api_key=_FICTIONAL_API_KEY,
        client_factory=recorder.client_factory,
    )


def _assert_detached(exc: MethodologyRequestNotSent, *, code: str, reason: str) -> None:
    assert isinstance(exc, MethodologyExtractorConfigurationError)
    assert type(exc) is MethodologyRequestNotSent
    assert exc.code == code
    assert exc.reason == reason
    assert exc.proof_kind == "PRE_SEND_LOCAL_FAILURE"
    assert str(exc) == code
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert _MARKER not in repr(exc)
    assert _GUARD_PREFIX not in repr(exc)


def _guard_call(kind: str) -> tuple[AnthropicSDKMethodologyTransport, _SdkRecorder, dict[str, Any]]:
    recorder = _SdkRecorder()
    kwargs = _wire_kwargs()
    kwargs["system"] = _MARKER
    kwargs["retries"] = 0
    kwargs["batch_mode"] = False
    if kind == "client":

        def client_factory(*, api_key: str, max_retries: int) -> Any:
            recorder.factory_calls += 1
            assert api_key == _FICTIONAL_API_KEY
            assert max_retries == 0
            raise RuntimeError(_MARKER)

        transport = AnthropicSDKMethodologyTransport(
            api_key=_FICTIONAL_API_KEY,
            client_factory=client_factory,
        )
        return transport, recorder, kwargs
    if kind == "retries":
        kwargs["retries"] = 2
    elif kind == "batch":
        kwargs["batch_mode"] = True
    elif kind == "cache":
        kwargs["cache_control"] = {"type": "ephemeral", "note": _MARKER}
    elif kind == "wire":
        kwargs["temperature"] = _MARKER
    return _transport(recorder), recorder, kwargs


@pytest.mark.parametrize(
    ("kind", "code", "reason", "factory_calls"),
    [
        ("retries", "METHODOLOGY_HIDDEN_RETRY_FORBIDDEN", "HIDDEN_RETRY_FORBIDDEN", 0),
        ("batch", "METHODOLOGY_UNPRICED_REQUEST_MODE", "UNPRICED_BATCH_OR_CACHE", 0),
        ("cache", "METHODOLOGY_UNPRICED_REQUEST_MODE", "UNPRICED_BATCH_OR_CACHE", 0),
        ("wire", "METHODOLOGY_UNPRICED_REQUEST_MODE", "UNPRICED_WIRE_REQUEST", 0),
        ("client", "METHODOLOGY_CLIENT_UNAVAILABLE", "CLIENT_UNAVAILABLE", 1),
    ],
)
def test_pre_send_guards_raise_request_not_sent_without_sdk_or_http(
    kind: str,
    code: str,
    reason: str,
    factory_calls: int,
) -> None:
    transport, recorder, kwargs = _guard_call(kind)

    with pytest.raises(MethodologyRequestNotSent) as raised:
        transport.create_message(**kwargs)

    _assert_detached(raised.value, code=code, reason=reason)
    assert recorder.factory_calls == factory_calls
    assert recorder.create_calls == []


def test_sdk_local_streaming_guard_is_request_not_sent_with_zero_http() -> None:
    recorded: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(500, json={"type": "error", "error": {"message": _MARKER}})

    kwargs = _wire_kwargs(max_tokens=32768)
    kwargs["system"] = _MARKER
    kwargs["retries"] = 0
    kwargs["batch_mode"] = False
    with httpx.Client(transport=httpx.MockTransport(record)) as http_client:
        sdk_client = anthropic.Anthropic(
            api_key=_FICTIONAL_API_KEY,
            max_retries=0,
            http_client=http_client,
        )
        transport = AnthropicSDKMethodologyTransport(
            api_key=_FICTIONAL_API_KEY,
            client_factory=lambda **_kwargs: sdk_client,
        )
        with pytest.raises(MethodologyRequestNotSent) as raised:
            transport.create_message(**kwargs)

    _assert_detached(raised.value, code="METHODOLOGY_SDK_LOCAL_GUARD", reason="SDK_LOCAL_GUARD")
    assert recorded == []


def _http_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(status_code: int) -> anthropic.APIStatusError:
    response = httpx.Response(
        status_code,
        request=_http_request(),
        json={"type": "error", "error": {"type": "api_error", "message": _MARKER}},
    )
    return anthropic.APIStatusError(_MARKER, response=response, body=response.json())


@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (ValueError("ordinary local failure " + _MARKER), ValueError),
        (TypeError(_MARKER), TypeError),
        (
            anthropic.APIConnectionError(message=_MARKER, request=_http_request()),
            anthropic.APIConnectionError,
        ),
        (anthropic.APITimeoutError(_http_request()), anthropic.APITimeoutError),
        (_status_error(500), anthropic.APIStatusError),
        (_status_error(529), anthropic.APIStatusError),
        (
            ValueError("released after send " + _GUARD_PREFIX + " " + _MARKER),
            ValueError,
        ),
    ],
)
def test_other_create_failures_propagate_without_request_not_sent(
    error: BaseException,
    expected_type: type[BaseException],
) -> None:
    recorder = _SdkRecorder(create_error=error)
    transport = _transport(recorder)

    with pytest.raises(expected_type) as raised:
        transport.create_message(**_wire_kwargs(), retries=0, batch_mode=False)

    assert raised.value is error
    assert not isinstance(raised.value, MethodologyRequestNotSent)
    assert len(recorder.create_calls) == 1


def test_guard_prefix_after_recorded_call_is_classified_by_type_and_prefix_only() -> None:
    """A matching ValueError is classified even when messages.create already ran.

    Production has no transport recorder. An offline stand-in cannot prove the
    installed SDK never emits this prefix after a send, so the rule stays
    type-and-prefix only.
    """

    error = ValueError(_GUARD_PREFIX + ". " + _MARKER)
    recorder = _SdkRecorder(create_error=error)
    transport = _transport(recorder)

    with pytest.raises(MethodologyRequestNotSent) as raised:
        transport.create_message(**_wire_kwargs(), retries=0, batch_mode=False)

    _assert_detached(raised.value, code="METHODOLOGY_SDK_LOCAL_GUARD", reason="SDK_LOCAL_GUARD")
    assert len(recorder.create_calls) == 1


def test_confirmed_nonacceptance_stays_typed_and_chained() -> None:
    error = _status_error(400)
    recorder = _SdkRecorder(create_error=error)
    transport = _transport(recorder)

    with pytest.raises(MethodologyProviderRequestNotAccepted) as raised:
        transport.create_message(**_wire_kwargs(), retries=0, batch_mode=False)

    assert raised.value.provider_status_code == 400
    assert raised.value.__cause__ is error
    assert not isinstance(raised.value, MethodologyRequestNotSent)
    assert len(recorder.create_calls) == 1


class _CoordinatorExtractor:
    def __init__(self, error_factory: Any) -> None:
        self.dispatches = 0
        self._error_factory = error_factory
        self.prepared = SimpleNamespace(request=None)

    def prepare(self, request: Any, *, budget_policy_id: str) -> Any:
        assert budget_policy_id == "budget-policy-fixture-v1"
        self.prepared = SimpleNamespace(request=request)
        return self.prepared

    def dispatch(self, prepared: Any, *, admission: Any) -> Any:
        assert prepared is self.prepared
        assert admission.committed is True
        self.dispatches += 1
        raise self._error_factory()


class _CoordinatorBoundary:
    def __init__(self, execution_input: Any) -> None:
        self.execution_input = execution_input
        self.events: list[str] = []
        self.unknown_kwargs: list[dict[str, Any]] = []
        self.nonacceptance_kwargs: list[dict[str, Any]] = []
        self.admission = SimpleNamespace(committed=True)

    def load_execution_inputs(self, **_: Any) -> list[Any]:
        return [self.execution_input]

    def load_started_attempts(self, **_: Any) -> list[Any]:
        return []

    def load_committed_responses(self, **_: Any) -> dict[str, Any]:
        return {}

    def load_active_budget_policy(self, **_: Any) -> Any:
        return SimpleNamespace(policy_hash="budget-policy-fixture-v1")

    def admit_call(self, **_: Any) -> Any:
        self.events.append("admit")
        return self.admission

    def mark_admitted_call_outcome_unknown(self, **kwargs: Any) -> None:
        self.events.append("unknown")
        self.unknown_kwargs.append(kwargs)

    def fail_provider_request_not_accepted(self, **kwargs: Any) -> None:
        self.events.append("nonacceptance")
        self.nonacceptance_kwargs.append(kwargs)


class _RefundBoundary(_CoordinatorBoundary):
    def __init__(self, execution_input: Any) -> None:
        super().__init__(execution_input)
        self.not_sent_kwargs: list[dict[str, Any]] = []

    def fail_request_not_sent(self, **kwargs: Any) -> None:
        self.events.append("not_sent")
        self.not_sent_kwargs.append(kwargs)


def _execution_input() -> Any:
    request = MethodologyTaskExtractorRequest(**extractor_request_kwargs())
    return SimpleNamespace(
        task_record_id=TASK_RECORD_ID,
        extraction_task_id=TASK_ID,
        methodology_question_record_id=QUESTION_RECORD_ID,
        request=request,
    )


def _run(boundary: _CoordinatorBoundary, extractor: _CoordinatorExtractor) -> None:
    DurableRunMethodologyExtractionTaskExecutionService(
        extractor=extractor,
        boundary=boundary,
    ).run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        run_step_id=_STEP_ID,
        tasks=[SimpleNamespace(extraction_task_id=TASK_ID)],
        documents=[],
    )


def test_request_not_sent_calls_fail_request_not_sent_once_and_does_not_replay() -> None:
    execution_input = _execution_input()
    boundary = _RefundBoundary(execution_input)
    not_sent = MethodologyRequestNotSent(
        code="METHODOLOGY_SDK_LOCAL_GUARD",
        reason="SDK_LOCAL_GUARD",
    )
    extractor = _CoordinatorExtractor(lambda: not_sent)

    with pytest.raises(DurableMethodologyExtractionExecutionError) as raised:
        _run(boundary, extractor)

    assert raised.value.code == METHODOLOGY_REQUEST_NOT_SENT
    assert raised.value.__cause__ is not_sent
    assert extractor.dispatches == 1
    assert boundary.events == ["admit", "not_sent"]
    assert boundary.unknown_kwargs == []
    assert boundary.not_sent_kwargs == [
        {
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "run_step_id": _STEP_ID,
            "execution_input": execution_input,
            "prepared": extractor.prepared,
            "admission": boundary.admission,
            "request_not_sent": not_sent,
        }
    ]


def test_missing_fail_request_not_sent_keeps_unknown_outcome() -> None:
    execution_input = _execution_input()
    boundary = _CoordinatorBoundary(execution_input)
    not_sent = MethodologyRequestNotSent(
        code="METHODOLOGY_CLIENT_UNAVAILABLE",
        reason="CLIENT_UNAVAILABLE",
    )
    extractor = _CoordinatorExtractor(lambda: not_sent)

    with pytest.raises(DurableMethodologyExtractionExecutionError) as raised:
        _run(boundary, extractor)

    assert raised.value.code == METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN
    assert raised.value.__cause__ is not_sent
    assert extractor.dispatches == 1
    assert boundary.events == ["admit", "unknown"]
    assert not hasattr(boundary, "fail_request_not_sent")
    assert boundary.unknown_kwargs[0]["execution_input"] is execution_input
    assert boundary.unknown_kwargs[0]["prepared"] is extractor.prepared
    assert boundary.unknown_kwargs[0]["admission"] is boundary.admission


def test_provider_nonacceptance_and_other_exceptions_keep_their_routes() -> None:
    execution_input = _execution_input()
    nonacceptance = MethodologyProviderRequestNotAccepted(provider_status_code=400)
    nonacceptance_boundary = _RefundBoundary(execution_input)
    nonacceptance_extractor = _CoordinatorExtractor(lambda: nonacceptance)

    with pytest.raises(DurableMethodologyExtractionExecutionError) as accepted:
        _run(nonacceptance_boundary, nonacceptance_extractor)

    assert accepted.value.code == METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED
    assert nonacceptance_extractor.dispatches == 1
    assert nonacceptance_boundary.events == ["admit", "nonacceptance"]
    assert nonacceptance_boundary.nonacceptance_kwargs[0]["nonacceptance"] is nonacceptance

    other_boundary = _RefundBoundary(execution_input)
    other = RuntimeError("transport outcome is ambiguous")
    other_extractor = _CoordinatorExtractor(lambda: other)

    with pytest.raises(DurableMethodologyExtractionExecutionError) as unknown:
        _run(other_boundary, other_extractor)

    assert unknown.value.code == METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN
    assert unknown.value.__cause__ is other
    assert other_extractor.dispatches == 1
    assert other_boundary.events == ["admit", "unknown"]


def test_fail_request_not_sent_exception_propagates_without_wrapping() -> None:
    execution_input = _execution_input()
    boundary = _RefundBoundary(execution_input)
    boundary_error = RuntimeError("BOUNDARY_WRITE_FAILED")

    def fail_request_not_sent(**_kwargs: Any) -> None:
        raise boundary_error

    boundary.fail_request_not_sent = fail_request_not_sent  # type: ignore[method-assign]
    extractor = _CoordinatorExtractor(
        lambda: MethodologyRequestNotSent(
            code="METHODOLOGY_HIDDEN_RETRY_FORBIDDEN",
            reason="HIDDEN_RETRY_FORBIDDEN",
        )
    )

    with pytest.raises(RuntimeError) as raised:
        _run(boundary, extractor)

    assert raised.value is boundary_error
    assert extractor.dispatches == 1
