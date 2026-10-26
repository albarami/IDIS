"""GL-05 slice 4: candidate rows stream; legacy and unknown rows do not.

The SDK parser and encoder run over a hermetic transport. No test sends a
provider request.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import anthropic
import httpx
import httpx2
import pytest

from idis.providers.anthropic_result import LlmResult, parse_message
from idis.services.extraction.extractors import anthropic_client

_http = (
    httpx2
    if "httpx2" in str(inspect.signature(anthropic.Anthropic).parameters["http_client"].annotation)
    else httpx
)

_OFFLINE_KEY = "cir01-offline-test-key"
_PROMPT = "Synthetic request only"
_MARKER = "gl05-stream-marker"
_TEXT = "visible answer"
_OBSERVED = "cir01-observed-model"
_REQUEST_ID = "req_gl05_stream"
_SIGNATURE = "sig_gl05"
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_LEGACY_IDS = ("claude-sonnet-4-20250514", "claude-opus-4-20250514")
_CANDIDATE_IDS = ("claude-sonnet-5-5", "claude-opus-5-5")
_CEILING = 32768

_START_USAGE: dict[str, Any] = {
    "input_tokens": 3,
    "output_tokens": 1,
    "cache_creation_input_tokens": 2,
    "cache_read_input_tokens": 4,
    "cache_creation": {"ephemeral_5m_input_tokens": 2, "ephemeral_1h_input_tokens": 0},
    "service_tier": "standard",
    "inference_geo": "us",
}
_DELTA_USAGE: dict[str, Any] = {
    "output_tokens": 40,
    "input_tokens": 11,
    "cache_creation_input_tokens": 8,
    "output_tokens_details": {"thinking_tokens": 9},
}
_FINAL_USAGE: dict[str, Any] = {
    "input_tokens": 11,
    "output_tokens": 40,
    "cache_creation_input_tokens": 8,
    "cache_read_input_tokens": 4,
    "cache_creation": {"ephemeral_5m_input_tokens": 2, "ephemeral_1h_input_tokens": 0},
    "service_tier": "standard",
    "inference_geo": "us",
    "output_tokens_details": {"thinking_tokens": 9},
}

_Handle = Callable[[httpx.Request | httpx2.Request], httpx.Response | httpx2.Response]
_Event = tuple[str, dict[str, Any]]


def test_transport_field_is_create_for_legacy_and_stream_for_candidates() -> None:
    """The resolution result exposes transport for every stored row and unknown ids."""
    caps = importlib.import_module("idis.providers.anthropic_capabilities")
    by_id = {row.model_id: row.transport for row in caps.MODEL_CAPABILITIES}
    assert by_id == {
        "claude-sonnet-4-20250514": "create",
        "claude-opus-4-20250514": "create",
        "claude-sonnet-5-5": "stream",
        "claude-opus-5-5": "stream",
    }
    assert caps.resolve_model_capability("cir01-test-model").transport == "create"
    assert caps.resolve_model_capability("claude-sonnet-5-5").transport == "stream"
    assert caps.resolve_model_capability("claude-opus-5-5").transport == "stream"


def test_streamed_candidate_completion_matches_the_nonstreamed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thinking leads, text is kept, and the typed result matches a create parse."""
    result, requests = _call(
        monkeypatch,
        model="claude-sonnet-5-5",
        effort="medium",
        handle=_events_handle(_completion_events()),
    )
    created = _parse_created_message(_final_message())
    assert result == created
    assert result.text == _TEXT
    assert result.stop_reason == "end_turn"
    assert result.thinking_block_count == 1
    assert result.redacted_thinking_block_count == 0
    assert result.request_id == _REQUEST_ID
    assert _MARKER not in result.text
    assert _MARKER not in repr(result)
    assert len(requests) == 1


def test_message_delta_usage_is_merged_over_message_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """message_delta totals overwrite, and omitted message_start fields survive."""
    result, _requests = _call(
        monkeypatch,
        model="claude-opus-5-5",
        effort="high",
        handle=_events_handle(_completion_events()),
    )
    usage = result.usage
    assert usage.input_tokens == 11
    assert usage.output_tokens == 40
    assert usage.output_tokens != _START_USAGE["output_tokens"]
    assert usage.cache_creation_input_tokens == 8
    assert usage.cache_read_input_tokens == 4
    assert usage.cache_creation_5m_input_tokens == 2
    assert usage.cache_creation_1h_input_tokens == 0
    assert usage.thinking_tokens == 9
    assert usage.service_tier == "standard"
    assert usage.inference_geo == "us"
    assert result == _parse_created_message(_final_message())


@pytest.mark.parametrize("model", _CANDIDATE_IDS)
@pytest.mark.parametrize("effort", _EFFORTS)
def test_candidate_stream_body_adds_only_stream_true(
    monkeypatch: pytest.MonkeyPatch, model: str, effort: str
) -> None:
    """Both candidate ids stream the same body the non-stream encoder would send."""
    _result, requests = _call(
        monkeypatch,
        model=model,
        effort=effort,
        handle=_events_handle(_completion_events()),
    )
    payload = json.loads(requests[0].content)
    assert payload == {
        "model": model,
        "max_tokens": 173,
        "messages": [{"role": "user", "content": _PROMPT}],
        "output_config": {"effort": effort},
        "stream": True,
    }


@pytest.mark.parametrize("model", (*_LEGACY_IDS, "cir01-test-model"))
def test_legacy_and_unknown_ids_do_not_stream(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    """Legacy and unknown ids stay on messages.create, with no stream flag."""
    _result, requests = _call(
        monkeypatch,
        model=model,
        effort=None,
        handle=_json_handle(_final_message()),
    )
    payload = json.loads(requests[0].content)
    assert "stream" not in payload
    assert payload["temperature"] == 0
    assert payload["model"] == model


def test_streamed_refusal_discards_partial_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal stop_reason keeps the category and drops partial text."""
    events = _completion_events(
        text=_MARKER,
        stop_reason="refusal",
        stop_details={
            "type": "refusal",
            "category": "cyber",
            "explanation": _MARKER,
            "recommended_model": "claude-opus-5-5",
        },
    )
    with pytest.raises(RuntimeError) as caught:
        _call(
            monkeypatch,
            model="claude-sonnet-5-5",
            effort="medium",
            handle=_events_handle(events),
        )
    exc = caught.value
    assert getattr(exc, "code", None) == "LLM_RESPONSE_REFUSED"
    assert str(exc) == "LLM_RESPONSE_REFUSED"
    assert getattr(exc, "category", None) == "cyber"
    assert getattr(exc, "recommended_model", None) == "claude-opus-5-5"
    assert getattr(exc, "request_id", None) == _REQUEST_ID
    _assert_detached(exc)


def test_streamed_max_tokens_is_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_tokens in message_delta is truncation and the partial text is dropped."""
    events = _completion_events(text=_MARKER, stop_reason="max_tokens")
    with pytest.raises(RuntimeError) as caught:
        _call(
            monkeypatch,
            model="claude-sonnet-5-5",
            effort="low",
            handle=_events_handle(events),
        )
    exc = caught.value
    assert getattr(exc, "code", None) == "LLM_RESPONSE_TRUNCATED"
    assert str(exc) == "LLM_RESPONSE_TRUNCATED"
    assert getattr(exc, "request_id", None) == _REQUEST_ID
    _assert_detached(exc)


def test_mid_stream_disconnect_is_unknown_after_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport fault after the first events is not replayed."""
    raw = _sse(_completion_events())
    parts = raw.split(b"\n\n")
    prefix = b"\n\n".join(parts[:3]) + b"\n\n"
    calls = 0
    seen: list[httpx.Request | httpx2.Request] = []

    class _Reset(_http.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield prefix
            raise _http.RemoteProtocolError(_MARKER)

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        seen.append(request)
        nonlocal calls
        calls += 1
        return _http.Response(
            200,
            stream=_Reset(),
            headers={"content-type": "text/event-stream", "request-id": _REQUEST_ID},
        )

    with pytest.raises(RuntimeError) as caught:
        _call(monkeypatch, model="claude-sonnet-5-5", effort="medium", handle=handle)
    assert calls == 1
    assert json.loads(seen[0].content)["stream"] is True
    exc = caught.value
    assert getattr(exc, "code", None) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert str(exc) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    _assert_detached(exc)


def test_sse_error_event_is_unknown_and_not_replayable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An SSE error after HTTP 200 is an unknown outcome, including status 200."""
    events = _completion_events(text=_MARKER)[:7]
    events.append(
        (
            "error",
            {"type": "error", "error": {"type": "api_error", "message": _MARKER}},
        )
    )
    calls = 0

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        return _sse_response(events)

    with pytest.raises(RuntimeError) as caught:
        _call(monkeypatch, model="claude-opus-5-5", effort="high", handle=handle)
    assert calls == 1
    exc = caught.value
    assert getattr(exc, "code", None) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert str(exc) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert getattr(exc, "code", None) != "LLM_PROVIDER_REQUEST_REJECTED"
    assert getattr(exc, "code", None) != "LLM_PROVIDER_RATE_LIMITED"
    _assert_detached(exc)


def test_initial_rate_limit_then_stream_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An initial HTTP 429 is replayed once; the following stream is the success."""
    calls = 0
    backoffs: list[float] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        if calls == 1:
            return _http.Response(
                429,
                json={"type": "error", "error": {"type": "rate_limit_error", "message": _MARKER}},
                headers={"request-id": "req_gl05_429"},
            )
        return _sse_response(_completion_events())

    monkeypatch.setattr(anthropic_client.time, "sleep", backoffs.append)
    result, _requests = _call(
        monkeypatch, model="claude-sonnet-5-5", effort="medium", handle=handle
    )
    assert calls == 2
    assert backoffs == [1.0]
    assert result.text == _TEXT
    assert _MARKER not in result.text


def test_clean_stream_close_error_returns_the_typed_result(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A close error after a clean stream does not replace the typed result."""
    caplog.set_level(logging.WARNING, logger=anthropic_client.logger.name)
    close_error = _StreamCloseError(_MARKER)
    result, requests = _call(
        monkeypatch,
        model="claude-sonnet-5-5",
        effort="medium",
        handle=_events_handle(_completion_events()),
        close_error=close_error,
    )
    assert result == _parse_created_message(_final_message())
    assert result.text == _TEXT
    assert len(requests) == 1
    assert json.loads(requests[0].content)["stream"] is True
    _assert_close_class_logged(caplog, close_error)


def test_mid_stream_close_error_stays_unknown_and_detached(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A close error after a mid-stream fault does not replace the typed failure."""
    raw = _sse(_completion_events())
    parts = raw.split(b"\n\n")
    prefix = b"\n\n".join(parts[:3]) + b"\n\n"
    calls = 0

    class _Reset(_http.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield prefix
            raise _http.RemoteProtocolError(_MARKER)

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        nonlocal calls
        calls += 1
        return _http.Response(
            200,
            stream=_Reset(),
            headers={"content-type": "text/event-stream", "request-id": _REQUEST_ID},
        )

    caplog.set_level(logging.WARNING, logger=anthropic_client.logger.name)
    close_error = _StreamCloseError(_MARKER)
    with pytest.raises(RuntimeError) as caught:
        _call(
            monkeypatch,
            model="claude-sonnet-5-5",
            effort="medium",
            handle=handle,
            close_error=close_error,
        )
    assert calls == 1
    exc = caught.value
    assert getattr(exc, "code", None) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert str(exc) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    _assert_detached(exc)
    _assert_close_class_logged(caplog, close_error)


def test_stream_close_keyboard_interrupt_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """KeyboardInterrupt from stream close propagates."""
    with pytest.raises(KeyboardInterrupt):
        _call(
            monkeypatch,
            model="claude-sonnet-5-5",
            effort="medium",
            handle=_events_handle(_completion_events()),
            close_error=KeyboardInterrupt(),
        )


def test_initial_503_on_stream_entry_is_unknown_after_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An initial HTTP 503 on stream entry is unknown, with the status, after one request."""
    calls = 0
    backoffs: list[float] = []
    seen: list[httpx.Request | httpx2.Request] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        seen.append(request)
        nonlocal calls
        calls += 1
        return _http.Response(
            503,
            json={"type": "error", "error": {"type": "api_error", "message": _MARKER}},
            headers={"request-id": "req_gl05_503"},
        )

    monkeypatch.setattr(anthropic_client.time, "sleep", backoffs.append)
    with pytest.raises(RuntimeError) as caught:
        _call(monkeypatch, model="claude-sonnet-5-5", effort="high", handle=handle)
    assert calls == 1
    assert backoffs == []
    assert json.loads(seen[0].content)["stream"] is True
    exc = caught.value
    assert getattr(exc, "code", None) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert getattr(exc, "status_code", None) == 503
    assert str(exc) == "LLM_PROVIDER_OUTCOME_UNKNOWN 503"
    assert getattr(exc, "request_id", None) == "req_gl05_503"
    _assert_detached(exc)


def test_connect_error_before_stream_response_is_unknown_after_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connect error before any stream response is unknown after exactly one request."""
    calls = 0
    seen: list[httpx.Request | httpx2.Request] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        seen.append(request)
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(_MARKER)

    with pytest.raises(RuntimeError) as caught:
        _call(monkeypatch, model="claude-opus-5-5", effort="medium", handle=handle)
    assert calls == 1
    assert json.loads(seen[0].content)["stream"] is True
    exc = caught.value
    assert getattr(exc, "code", None) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert str(exc) == "LLM_PROVIDER_OUTCOME_UNKNOWN"
    assert getattr(exc, "status_code", None) is None
    _assert_detached(exc)


def test_initial_400_is_rejected_after_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """An initial HTTP 400 is confirmed non-acceptance and is not replayed."""
    calls = 0
    backoffs: list[float] = []
    seen: list[httpx.Request | httpx2.Request] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        seen.append(request)
        nonlocal calls
        calls += 1
        return _http.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": _MARKER}},
            headers={"request-id": "req_gl05_400"},
        )

    monkeypatch.setattr(anthropic_client.time, "sleep", backoffs.append)
    with pytest.raises(RuntimeError) as caught:
        _call(monkeypatch, model="claude-sonnet-5-5", effort="high", handle=handle)
    assert calls == 1
    assert backoffs == []
    assert json.loads(seen[0].content)["stream"] is True
    exc = caught.value
    assert getattr(exc, "code", None) == "LLM_PROVIDER_REQUEST_REJECTED"
    assert str(exc) == "LLM_PROVIDER_REQUEST_REJECTED 400"
    assert getattr(exc, "request_id", None) == "req_gl05_400"
    _assert_detached(exc)


def test_ceiling_32768_streams_and_default_timeout_create_refuses_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """32,768 is sent as a stream. A default-timeout create raises before any request."""
    sent: list[bytes] = []

    def empty(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        sent.append(request.content)
        return _http.Response(200, content=b"")

    guard = anthropic.Anthropic(
        api_key=_OFFLINE_KEY,
        http_client=_http.Client(transport=_http.MockTransport(empty)),
        max_retries=0,
    )
    try:
        with pytest.raises(ValueError, match="Streaming is required") as caught:
            guard.messages.create(
                model="claude-sonnet-5-5",
                max_tokens=_CEILING,
                messages=[{"role": "user", "content": _PROMPT}],
            )
    finally:
        guard.close()
    assert sent == []
    assert "Streaming is required" in str(caught.value)

    result, requests = _call(
        monkeypatch,
        model="claude-sonnet-5-5",
        effort="medium",
        max_tokens=_CEILING,
        handle=_events_handle(_completion_events()),
    )
    payload = json.loads(requests[0].content)
    assert payload["max_tokens"] == _CEILING
    assert payload["stream"] is True
    assert result.text == _TEXT


def _assert_detached(exc: BaseException) -> None:
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert _MARKER not in str(exc)
    assert _MARKER not in repr(exc)
    assert _MARKER not in repr(exc.__dict__)


class _StreamCloseError(RuntimeError):
    """Close failure whose text must not enter the log or the typed result."""


class _RaisingStreamExit:
    """Real SDK stream manager whose close raises after the inner close."""

    def __init__(self, inner: Any, error: BaseException) -> None:
        self._inner = inner
        self._error = error

    def __enter__(self) -> Any:
        return self._inner.__enter__()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._inner.__exit__(exc_type, exc, tb)
        raise self._error


def _arm_stream_close(sdk: anthropic.Anthropic, error: BaseException) -> None:
    original = sdk.messages.stream

    def stream(*args: Any, **kwargs: Any) -> _RaisingStreamExit:
        return _RaisingStreamExit(original(*args, **kwargs), error)

    sdk.messages.stream = stream


def _assert_close_class_logged(
    caplog: pytest.LogCaptureFixture,
    error: BaseException,
) -> None:
    name = type(error).__name__
    matched = [
        record
        for record in caplog.records
        if record.name == anthropic_client.logger.name
        and record.levelno == logging.WARNING
        and record.message == f"Anthropic stream close failed: {name}"
    ]
    assert len(matched) == 1
    assert matched[0].args == (name,)
    assert matched[0].exc_info is None
    for record in caplog.records:
        assert _MARKER not in record.message
        assert all(_MARKER not in str(arg) for arg in record.args)


def _call(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: str,
    effort: str | None,
    handle: _Handle,
    max_tokens: int = 173,
    close_error: BaseException | None = None,
) -> tuple[LlmResult, list[httpx.Request | httpx2.Request]]:
    requests: list[httpx.Request | httpx2.Request] = []

    def recording(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        requests.append(request)
        return handle(request)

    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
    if effort is not None:
        kwargs["effort"] = effort
    client = anthropic_client.AnthropicLLMClient(**kwargs)
    client._client.close()
    sdk = anthropic.Anthropic(
        api_key=_OFFLINE_KEY,
        http_client=_http.Client(transport=_http.MockTransport(recording)),
        max_retries=0,
        timeout=anthropic_client.REQUEST_TIMEOUT_SECONDS,
    )
    client._client = sdk
    if close_error is not None:
        _arm_stream_close(sdk, close_error)
    try:
        result = client.call_typed(_PROMPT)
    finally:
        sdk.close()
    return result, requests


def _parse_created_message(message: dict[str, Any]) -> LlmResult:
    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        return _http.Response(200, json=message, headers={"request-id": _REQUEST_ID})

    with anthropic.Anthropic(
        api_key=_OFFLINE_KEY,
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
        timeout=anthropic_client.REQUEST_TIMEOUT_SECONDS,
    ) as sdk:
        created = sdk.messages.create(
            model="cir01-test-model",
            max_tokens=16,
            messages=[{"role": "user", "content": _PROMPT}],
        )
    return parse_message(created)


def _events_handle(events: list[_Event]) -> _Handle:
    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        return _sse_response(events)

    return handle


def _json_handle(message: dict[str, Any]) -> _Handle:
    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        return _http.Response(200, json=message, headers={"request-id": _REQUEST_ID})

    return handle


def _sse_response(events: list[_Event]) -> httpx.Response | httpx2.Response:
    return _http.Response(
        200,
        content=_sse(events),
        headers={"content-type": "text/event-stream", "request-id": _REQUEST_ID},
    )


def _sse(events: list[_Event]) -> bytes:
    parts = [
        f"event: {name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        for name, payload in events
    ]
    return "".join(parts).encode()


def _completion_events(
    *,
    text: str = _TEXT,
    stop_reason: str = "end_turn",
    stop_details: dict[str, Any] | None = None,
) -> list[_Event]:
    delta: dict[str, Any] = {"stop_reason": stop_reason, "stop_sequence": None}
    if stop_details is not None:
        delta["stop_details"] = stop_details
    return [
        ("message_start", _message_start()),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": _MARKER},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": _SIGNATURE},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        (
            "message_delta",
            {"type": "message_delta", "delta": delta, "usage": _DELTA_USAGE},
        ),
        ("message_stop", {"type": "message_stop"}),
    ]


def _message_start() -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_gl05_stream",
            "type": "message",
            "role": "assistant",
            "model": _OBSERVED,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": _START_USAGE,
        },
    }


def _final_message() -> dict[str, Any]:
    return {
        "id": "msg_gl05_stream",
        "type": "message",
        "role": "assistant",
        "model": _OBSERVED,
        "content": [
            {"type": "thinking", "thinking": _MARKER, "signature": _SIGNATURE},
            {"type": "text", "text": _TEXT},
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": _FINAL_USAGE,
    }
