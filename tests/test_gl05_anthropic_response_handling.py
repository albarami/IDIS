"""GL-05 slice 2: only a clean single text completion is a success."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import SimpleNamespace

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
_OBSERVED_MODEL = "cir01-observed-model"
_REQUEST_ID = "req_gl05_message"

_Handle = Callable[[httpx.Request | httpx2.Request], httpx.Response | httpx2.Response]
_JsonObject = dict[str, object]


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("refusal-empty", "LLM_RESPONSE_REFUSED"),
        ("refusal-partial", "LLM_RESPONSE_REFUSED"),
        ("max-tokens", "LLM_RESPONSE_TRUNCATED"),
        ("context-exceeded", "LLM_RESPONSE_CONTEXT_EXCEEDED"),
        ("pause-turn", "LLM_RESPONSE_INCOMPLETE"),
        ("tool-stop", "LLM_RESPONSE_INCOMPLETE"),
        ("empty-end-turn", "LLM_RESPONSE_EMPTY"),
        ("thinking-only", "LLM_RESPONSE_EMPTY"),
        ("two-text", "LLM_RESPONSE_AMBIGUOUS"),
        ("tool-block", "LLM_RESPONSE_UNEXPECTED_BLOCK"),
        ("server-tool-block", "LLM_RESPONSE_UNEXPECTED_BLOCK"),
        ("missing-stop", "LLM_RESPONSE_INCOMPLETE"),
    ],
)
def test_non_success_responses_raise_typed_codes(
    monkeypatch: pytest.MonkeyPatch, kind: str, code: str
) -> None:
    content, stop_reason, stop_details = _case(kind)
    with (
        _offline_client(
            monkeypatch,
            _single(content, stop_reason=stop_reason, stop_details=stop_details),
        ) as client,
        pytest.raises(RuntimeError) as caught,
    ):
        client.call("Synthetic request only")

    exc = caught.value
    assert getattr(exc, "code", None) == code
    assert getattr(exc, "code", type(exc).__name__.upper()) == code
    assert str(exc) == code
    assert getattr(exc, "request_id", None) == _REQUEST_ID
    assert _MARKER not in str(exc)


def test_refusal_records_category_and_extra_recommended_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = [{"type": "text", "text": _MARKER}]
    stop_details: _JsonObject = {
        "type": "refusal",
        "category": "cyber",
        "explanation": _MARKER,
        "recommended_model": "claude-opus-5-5",
    }
    with (
        _offline_client(
            monkeypatch, _single(content, stop_reason="refusal", stop_details=stop_details)
        ) as client,
        pytest.raises(RuntimeError) as caught,
    ):
        client.call("Synthetic request only")

    exc = caught.value
    assert getattr(exc, "code", type(exc).__name__.upper()) == "LLM_RESPONSE_REFUSED"
    assert str(exc) == "LLM_RESPONSE_REFUSED"
    assert getattr(exc, "category", None) == "cyber"
    assert getattr(exc, "recommended_model", None) == "claude-opus-5-5"
    assert getattr(exc, "request_id", None) == _REQUEST_ID
    assert _MARKER not in str(exc)


def test_thinking_block_before_text_returns_only_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = [
        {"type": "thinking", "thinking": _MARKER, "signature": "sig_gl05"},
        {"type": "text", "text": "visible answer"},
    ]
    with _offline_client(monkeypatch, _single(content, stop_reason="end_turn")) as client:
        assert client.call("Synthetic request only") == "visible answer"
        assert hasattr(client, "call_typed")
        result = client.call_typed("Synthetic request only")

    assert result.text == "visible answer"
    assert result.stop_reason == "end_turn"
    assert result.request_id == _REQUEST_ID
    assert result.model == _OBSERVED_MODEL
    assert result.thinking_block_count == 1
    assert result.redacted_thinking_block_count == 0
    assert _MARKER not in result.text
    assert _MARKER not in repr(result)
    assert "ThinkingBlock" not in result.text


def test_redacted_thinking_is_counted_and_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    content = [
        {"type": "redacted_thinking", "data": _MARKER},
        {"type": "text", "text": "visible answer"},
    ]
    with _offline_client(monkeypatch, _single(content, stop_reason="end_turn")) as client:
        assert client.call("Synthetic request only") == "visible answer"
        result = client.call_typed("Synthetic request only")

    assert result.thinking_block_count == 0
    assert result.redacted_thinking_block_count == 1
    assert _MARKER not in result.text
    assert _MARKER not in repr(result)


def test_stop_sequence_is_a_clean_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    content = [{"type": "text", "text": "visible answer"}]
    with _offline_client(monkeypatch, _single(content, stop_reason="stop_sequence")) as client:
        assert hasattr(client, "call_typed")
        result = client.call_typed("Synthetic request only")
        assert client.call("Synthetic request only") == "visible answer"

    assert isinstance(result.text, str)
    assert result.text == "visible answer"
    assert result.stop_reason == "stop_sequence"
    assert result.model == _OBSERVED_MODEL
    assert result.request_id == _REQUEST_ID


def test_call_typed_returns_usage_and_call_returns_text_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = {
        "input_tokens": 11,
        "output_tokens": 22,
        "cache_creation_input_tokens": 33,
        "cache_read_input_tokens": 44,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 55,
            "ephemeral_1h_input_tokens": 66,
        },
        "output_tokens_details": {"thinking_tokens": 77},
        "service_tier": "standard",
        "inference_geo": "us",
    }
    content = [{"type": "text", "text": "visible answer"}]
    with _offline_client(
        monkeypatch, _single(content, stop_reason="end_turn", usage=usage)
    ) as client:
        assert hasattr(client, "call_typed")
        from idis.providers.anthropic_result import LlmUsage

        result = client.call_typed("Synthetic request only")
        assert client.call("Synthetic request only") == "visible answer"
        assert not hasattr(client, "provider_request_id")

    assert result.usage == LlmUsage(
        input_tokens=11,
        output_tokens=22,
        cache_creation_input_tokens=33,
        cache_read_input_tokens=44,
        cache_creation_5m_input_tokens=55,
        cache_creation_1h_input_tokens=66,
        thinking_tokens=77,
        service_tier="standard",
        inference_geo="us",
    )
    assert result.stop_reason == "end_turn"
    assert result.request_id == _REQUEST_ID
    assert result.model == _OBSERVED_MODEL
    assert result.thinking_block_count == 0
    assert result.redacted_thinking_block_count == 0


def test_parse_message_drops_unknown_refusal_category() -> None:
    import importlib.util

    assert importlib.util.find_spec("idis.providers.anthropic_result") is not None
    from idis.providers.anthropic_result import parse_message

    message = SimpleNamespace(
        content=[],
        stop_reason="refusal",
        stop_details=SimpleNamespace(
            category="free-text-category",
            explanation=_MARKER,
            recommended_model="claude-opus-5-5",
            model_extra={"recommended_model": "claude-opus-5-5"},
        ),
        _request_id="req_gl05_closed",
        model=_OBSERVED_MODEL,
        usage=None,
    )
    with pytest.raises(RuntimeError) as caught:
        parse_message(message)

    exc = caught.value
    assert getattr(exc, "code", None) == "LLM_RESPONSE_REFUSED"
    assert getattr(exc, "category", None) is None
    assert getattr(exc, "recommended_model", None) == "claude-opus-5-5"
    assert getattr(exc, "request_id", None) == "req_gl05_closed"
    assert str(exc) == "LLM_RESPONSE_REFUSED"
    assert "free-text-category" not in str(exc)
    assert _MARKER not in str(exc)


def _case(
    kind: str,
) -> tuple[list[_JsonObject], str | None, _JsonObject | None]:
    text = {"type": "text", "text": _MARKER}
    thinking = {"type": "thinking", "thinking": _MARKER, "signature": "sig_gl05"}
    tool = {"type": "tool_use", "id": "toolu_gl05", "name": "not_sent", "input": {}}
    server = {
        "type": "server_tool_use",
        "id": "srvtoolu_gl05",
        "name": "web_search",
        "input": {"query": _MARKER},
    }
    refusal = {"type": "refusal", "category": "bio", "explanation": _MARKER}
    cases: dict[str, tuple[list[_JsonObject], str | None, _JsonObject | None]] = {
        "refusal-empty": ([], "refusal", refusal),
        "refusal-partial": ([text], "refusal", refusal),
        "max-tokens": ([text], "max_tokens", None),
        "context-exceeded": ([], "model_context_window_exceeded", None),
        "pause-turn": ([text], "pause_turn", None),
        "tool-stop": ([tool], "tool_use", None),
        "empty-end-turn": ([], "end_turn", None),
        "thinking-only": ([thinking], "end_turn", None),
        "two-text": ([text, {"type": "text", "text": "second block"}], "end_turn", None),
        "tool-block": ([tool], "end_turn", None),
        "server-tool-block": ([server], "end_turn", None),
        "missing-stop": ([text], None, None),
    }
    return cases[kind]


def _single(
    content: list[_JsonObject],
    *,
    stop_reason: str | None,
    stop_details: _JsonObject | None = None,
    usage: _JsonObject | None = None,
) -> _Handle:
    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        del request
        body: _JsonObject = {
            "id": "msg_gl05_response",
            "type": "message",
            "role": "assistant",
            "model": _OBSERVED_MODEL,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage or {"input_tokens": 3, "output_tokens": 4},
        }
        if stop_details is not None:
            body["stop_details"] = stop_details
        return _http.Response(200, json=body, headers={"request-id": _REQUEST_ID})

    return handle


def _offline_client(monkeypatch: pytest.MonkeyPatch, handle: _Handle) -> _ClosingClient:
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
