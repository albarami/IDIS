"""GL-05 slice 2: replay HTTP 429 only, and never replay an unknown outcome."""

from __future__ import annotations

import inspect
from collections.abc import Callable

import anthropic
import httpx
import httpx2
import pytest

from idis.services.extraction.extractors import anthropic_client

_http = (
    httpx2
    if "httpx2" in str(inspect.signature(anthropic.Anthropic).parameters["http_client"].annotation)
    else httpx
)

_MARKER = "gl05-body-marker"
_OFFLINE_KEY = "cir01-offline-test-key"
_REJECTED_STATUSES = (400, 401, 402, 403, 404, 409, 413, 422)
_UNKNOWN_STATUSES = (408, 500, 502, 503, 504, 529)

_Handle = Callable[[httpx.Request | httpx2.Request], httpx.Response | httpx2.Response]


@pytest.fixture
def anthropic_offline_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install the offline key used to construct a client without a provider call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    return _OFFLINE_KEY


def test_production_client_disables_sdk_retries(anthropic_offline_key: str) -> None:
    """A production-constructed client must not keep the SDK's hidden retries."""
    del anthropic_offline_key
    client = anthropic_client.AnthropicLLMClient(model="cir01-test-model")
    try:
        assert client._client.max_retries == 0
    finally:
        client._client.close()


@pytest.mark.parametrize("status", _REJECTED_STATUSES)
def test_confirmed_nonacceptance_is_one_request(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    calls = 0

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        return _status(status, request_id=f"req_gl05_rejected_{status}")

    with _offline_client(monkeypatch, handle) as client, pytest.raises(RuntimeError) as caught:
        client.call("Synthetic request only")

    exc = caught.value
    assert calls == 1
    assert getattr(exc, "code", None) == "LLM_PROVIDER_REQUEST_REJECTED"
    assert str(exc) == f"LLM_PROVIDER_REQUEST_REJECTED {status}"
    assert getattr(exc, "request_id", None) == f"req_gl05_rejected_{status}"
    assert _MARKER not in str(exc)


@pytest.mark.parametrize("status", _UNKNOWN_STATUSES)
def test_unknown_http_status_is_one_request(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    calls = 0
    backoffs: list[float] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        return _status(status, request_id=f"req_gl05_unknown_{status}")

    monkeypatch.setattr(anthropic_client.time, "sleep", backoffs.append)
    with _offline_client(monkeypatch, handle) as client, pytest.raises(RuntimeError) as caught:
        client.call("Synthetic request only")

    exc = caught.value
    assert calls == 1
    assert backoffs == []
    assert getattr(exc, "code", None) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert str(exc) == f"LLM_PROVIDER_OUTCOME_UNKNOWN {status}"
    assert getattr(exc, "request_id", None) == f"req_gl05_unknown_{status}"
    assert _MARKER not in str(exc)


def test_exhausted_rate_limit_sleeps_between_attempts_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    backoffs: list[float] = []
    request_ids = ("req_gl05_429_1", "req_gl05_429_2", "req_gl05_429_3")

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        return _status(429, request_id=request_ids[calls - 1])

    monkeypatch.setattr(anthropic_client.time, "sleep", backoffs.append)
    with _offline_client(monkeypatch, handle) as client, pytest.raises(RuntimeError) as caught:
        client.call("Synthetic request only")

    exc = caught.value
    assert calls == anthropic_client.MAX_RETRIES + 1 == 3
    assert backoffs == [1.0, 2.0]
    assert getattr(exc, "code", None) == "LLM_PROVIDER_RATE_LIMITED"
    assert str(exc) == "LLM_PROVIDER_RATE_LIMITED 429"
    assert getattr(exc, "request_id", None) == "req_gl05_429_3"
    assert _MARKER not in str(exc)


def test_rate_limit_then_success_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    backoffs: list[float] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        if calls == 1:
            return _status(429, request_id="req_gl05_429")
        return _message(request_id="req_gl05_ok")

    monkeypatch.setattr(anthropic_client.time, "sleep", backoffs.append)
    with _offline_client(monkeypatch, handle) as client:
        assert hasattr(client, "call_typed")
        assert client.call("Synthetic request only") == "visible answer"

    assert calls == 2
    assert backoffs == [1.0]


def test_should_retry_header_on_503_is_still_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    backoffs: list[float] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        return _status(
            503,
            request_id="req_gl05_503",
            extra_headers={"x-should-retry": "true"},
        )

    monkeypatch.setattr(anthropic_client.time, "sleep", backoffs.append)
    with _offline_client(monkeypatch, handle) as client, pytest.raises(RuntimeError) as caught:
        client.call("Synthetic request only")

    exc = caught.value
    assert calls == 1
    assert backoffs == []
    assert getattr(exc, "code", None) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert str(exc) == "LLM_PROVIDER_OUTCOME_UNKNOWN 503"
    assert getattr(exc, "request_id", None) == "req_gl05_503"
    assert _MARKER not in str(exc)


@pytest.mark.parametrize(
    "fault",
    [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError],
)
def test_transport_fault_is_unknown_after_one_request(
    monkeypatch: pytest.MonkeyPatch,
    fault: type[Exception],
) -> None:
    calls = 0

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        raise fault(_MARKER)

    with _offline_client(monkeypatch, handle) as client, pytest.raises(RuntimeError) as caught:
        client.call("Synthetic request only")

    exc = caught.value
    assert calls == 1
    assert getattr(exc, "code", None) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert str(exc) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert getattr(exc, "request_id", None) is None
    assert _MARKER not in str(exc)


def test_classifier_replays_only_429_and_unknown_without_a_response() -> None:
    import importlib.util

    assert importlib.util.find_spec("idis.providers.anthropic_failure") is not None
    from idis.providers.anthropic_failure import HttpAdmission, classify_http_status

    assert classify_http_status(None) is HttpAdmission.UNKNOWN
    assert classify_http_status(429) is HttpAdmission.REPLAYABLE
    for status in _REJECTED_STATUSES:
        assert classify_http_status(status) is HttpAdmission.CONFIRMED_NOT_ACCEPTED
    for status in _UNKNOWN_STATUSES:
        assert classify_http_status(status) is HttpAdmission.UNKNOWN
    assert classify_http_status(418) is HttpAdmission.UNKNOWN


@pytest.mark.parametrize(
    ("kind", "status", "attempts", "expected_backoffs", "code", "message"),
    [
        (
            "status",
            400,
            1,
            [],
            "LLM_PROVIDER_REQUEST_REJECTED",
            "LLM_PROVIDER_REQUEST_REJECTED 400",
        ),
        ("status", 503, 1, [], "LLM_PROVIDER_OUTCOME_UNKNOWN", "LLM_PROVIDER_OUTCOME_UNKNOWN 503"),
        ("connect", None, 1, [], "LLM_PROVIDER_OUTCOME_UNKNOWN", "LLM_PROVIDER_OUTCOME_UNKNOWN"),
        ("rate", 429, 3, [1.0, 2.0], "LLM_PROVIDER_RATE_LIMITED", "LLM_PROVIDER_RATE_LIMITED 429"),
    ],
)
def test_typed_provider_error_is_detached_from_the_sdk_exception(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    status: int | None,
    attempts: int,
    expected_backoffs: list[float],
    code: str,
    message: str,
) -> None:
    """Typed failures must not retain the SDK exception or its body."""
    calls = 0
    backoffs: list[float] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        if kind == "connect":
            raise httpx.ConnectError(_MARKER)
        assert status is not None
        return _status(status, request_id=f"req_gl05_detach_{status}_{calls}")

    monkeypatch.setattr(anthropic_client.time, "sleep", backoffs.append)
    with _offline_client(monkeypatch, handle) as client, pytest.raises(RuntimeError) as caught:
        client.call("Synthetic request only")

    exc = caught.value
    assert calls == attempts
    assert backoffs == expected_backoffs
    assert getattr(exc, "code", None) == code
    assert str(exc) == message
    if kind == "connect":
        assert getattr(exc, "request_id", None) is None
    elif status == 429:
        assert getattr(exc, "request_id", None) == "req_gl05_detach_429_3"
    else:
        assert getattr(exc, "request_id", None) == f"req_gl05_detach_{status}_1"
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert _MARKER not in repr(exc)
    assert _MARKER not in repr(exc.__dict__)


def test_refusal_category_outside_the_closed_set_is_dropped() -> None:
    import importlib.util

    assert importlib.util.find_spec("idis.providers.anthropic_failure") is not None
    from idis.providers.anthropic_failure import LlmResponseRefused

    exc = LlmResponseRefused(
        request_id="req_gl05_closed",
        category="free-text-category",
        recommended_model=None,
    )
    assert exc.code == "LLM_RESPONSE_REFUSED"
    assert exc.category is None
    assert exc.recommended_model is None
    assert exc.request_id == "req_gl05_closed"
    assert str(exc) == "LLM_RESPONSE_REFUSED"
    assert "free-text-category" not in str(exc)


def _offline_client(monkeypatch: pytest.MonkeyPatch, handle: _Handle) -> _ClosingClient:
    """Return a production client whose SDK transport is hermetic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    client = anthropic_client.AnthropicLLMClient(model="cir01-test-model", max_tokens=173)
    client._client.close()
    sdk_client = anthropic.Anthropic(
        api_key=_OFFLINE_KEY,
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
    )
    client._client = sdk_client
    return _ClosingClient(client, sdk_client)


class _ClosingClient:
    """Close the swapped SDK client when the test leaves the block."""

    def __init__(
        self, client: anthropic_client.AnthropicLLMClient, sdk: anthropic.Anthropic
    ) -> None:
        self._client = client
        self._sdk = sdk

    def __enter__(self) -> anthropic_client.AnthropicLLMClient:
        return self._client

    def __exit__(self, *exc: object) -> None:
        self._sdk.close()


def _status(
    status: int,
    *,
    request_id: str,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response | httpx2.Response:
    headers = {"request-id": request_id}
    if extra_headers:
        headers.update(extra_headers)
    return _http.Response(
        status,
        json={"type": "error", "error": {"type": "api_error", "message": _MARKER}},
        headers=headers,
    )


def _message(*, request_id: str) -> httpx.Response | httpx2.Response:
    return _http.Response(
        200,
        json={
            "id": "msg_gl05_retry",
            "type": "message",
            "role": "assistant",
            "model": "cir01-observed-model",
            "content": [{"type": "text", "text": "visible answer"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
        headers={"request-id": request_id},
    )
