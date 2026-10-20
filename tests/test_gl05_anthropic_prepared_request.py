"""GL-05 P5: prepare_request matches the SDK wire body and does no I/O.

The encoder runs over a hermetic transport. No test sends a provider request.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import anthropic
import httpx
import httpx2
import pytest

from idis.models.methodology_extraction import exact_canonical_json_bytes
from idis.providers.anthropic_capabilities import LlmRequestShapeRejected
from idis.services.extraction.extractors import anthropic_client
from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

_http = (
    httpx2
    if "httpx2" in str(inspect.signature(anthropic.Anthropic).parameters["http_client"].annotation)
    else httpx
)

_OFFLINE_KEY = "cir01-offline-test-key"
_PROMPT = "Synthetic request only"
_NON_ASCII_PROMPT = "café 字"
_JSON_SYSTEM = (
    "You MUST respond with valid JSON only. No markdown, no explanation, "
    "no code fences. Output raw JSON."
)

_Request = httpx.Request | httpx2.Request
_Response = httpx.Response | httpx2.Response


class _RejectingSdk:
    """Any attribute access fails. prepare_request must not touch the SDK."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(name)


def test_prepare_request_does_not_use_the_sdk_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building the request reads no SDK client and opens no transport."""
    client = _client(monkeypatch, model="claude-sonnet-4-20250514", effort=None)
    client._client = _RejectingSdk()
    prepared = client.prepare_request(_PROMPT)
    assert prepared.model == "claude-sonnet-4-20250514"
    assert prepared.max_tokens == 173
    assert prepared.transport == "create"
    assert prepared.payload_bytes == len(exact_canonical_json_bytes(dict(prepared.params)))


def test_prepare_request_rejects_a_legacy_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """A legacy row still rejects effort from prepare_request, before any send."""
    client = _client(monkeypatch, model="claude-sonnet-4-20250514", effort=None)
    client._effort = "high"
    with pytest.raises(LlmRequestShapeRejected) as caught:
        client.prepare_request(_PROMPT)
    assert caught.value.code == "LLM_REQUEST_SHAPE_REJECTED"
    assert caught.value.reason == "effort_not_applicable"
    assert str(caught.value) == "LLM_REQUEST_SHAPE_REJECTED"


@pytest.mark.parametrize(
    ("model", "effort", "transport"),
    [
        ("claude-sonnet-4-20250514", None, "create"),
        ("claude-opus-4-20250514", None, "create"),
        ("cir01-test-model", None, "create"),
        ("claude-sonnet-5-5", "high", "stream"),
        ("claude-opus-5-5", "medium", "stream"),
    ],
)
@pytest.mark.parametrize("json_mode", [False, True])
def test_prepared_params_equal_the_sdk_wire_body(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    effort: str | None,
    transport: str,
    json_mode: bool,
) -> None:
    """Legacy, unknown, and both candidate ids match the encoder, including stream."""
    client = _client(monkeypatch, model=model, effort=effort)
    prepared = client.prepare_request(_PROMPT, json_mode=json_mode)
    payload = _encode(prepared.params, transport)
    expected = dict(prepared.params)
    assert prepared.transport == transport
    assert prepared.model == model
    assert prepared.max_tokens == 173
    assert prepared.payload_bytes == len(exact_canonical_json_bytes(expected))
    assert "stream" not in expected
    if transport == "stream":
        expected["stream"] = True
        assert payload["output_config"] == {"effort": effort}
        assert "temperature" not in payload
    else:
        assert "stream" not in payload
        assert payload["temperature"] == 0
    if json_mode:
        assert payload["system"] == _JSON_SYSTEM
    assert payload == expected
    assert payload["model"] == model
    assert payload["max_tokens"] == 173


def test_non_ascii_payload_bytes_follow_canonical_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payload size is the canonical UTF-8 length, with non-ASCII left unescaped."""
    client = _client(monkeypatch, model="cir01-test-model", effort=None)
    prepared = client.prepare_request(_NON_ASCII_PROMPT)
    canonical = exact_canonical_json_bytes(dict(prepared.params))
    assert prepared.payload_bytes == len(canonical)
    assert "é".encode() in canonical
    assert "字".encode() in canonical
    assert b"\\u00e9" not in canonical
    payload = _encode(prepared.params, "create")
    assert payload == dict(prepared.params)


def test_call_typed_sends_the_prepared_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """call_typed puts the prepare_request params on the wire, plus stream for candidates."""
    client = _client(monkeypatch, model="claude-sonnet-5-5", effort="high")
    prepared = client.prepare_request(_PROMPT, json_mode=True)
    payload = _send_with_client(client, _PROMPT, json_mode=True, transport="stream")
    expected = dict(prepared.params)
    expected["stream"] = True
    assert payload == expected


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: str,
    effort: str | None,
) -> AnthropicLLMClient:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    kwargs: dict[str, Any] = {"model": model, "max_tokens": 173}
    if effort is not None:
        kwargs["effort"] = effort
    return anthropic_client.AnthropicLLMClient(**kwargs)


def _encode(params: Any, transport: str) -> dict[str, Any]:
    requests: list[_Request] = []

    def handle(request: _Request) -> _Response:
        requests.append(request)
        if transport == "stream":
            return _sse_response()
        return _json_response()

    with anthropic.Anthropic(
        api_key=_OFFLINE_KEY,
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
        timeout=anthropic_client.REQUEST_TIMEOUT_SECONDS,
    ) as sdk:
        if transport == "stream":
            with sdk.messages.stream(**params) as stream:
                stream.get_final_message()
        else:
            sdk.messages.create(**params)
    assert len(requests) == 1
    return json.loads(requests[0].content)


def _send_with_client(
    client: AnthropicLLMClient,
    prompt: str,
    *,
    json_mode: bool,
    transport: str,
) -> dict[str, Any]:
    requests: list[_Request] = []

    def handle(request: _Request) -> _Response:
        requests.append(request)
        if transport == "stream":
            return _sse_response()
        return _json_response()

    client._client.close()
    sdk = anthropic.Anthropic(
        api_key=_OFFLINE_KEY,
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
        timeout=anthropic_client.REQUEST_TIMEOUT_SECONDS,
    )
    client._client = sdk
    try:
        client.call_typed(prompt, json_mode=json_mode)
    finally:
        sdk.close()
    assert len(requests) == 1
    return json.loads(requests[0].content)


def _json_response() -> _Response:
    return _http.Response(
        200,
        json={
            "id": "msg_gl05_prepared",
            "type": "message",
            "role": "assistant",
            "model": "cir01-observed-model",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
        headers={"request-id": "req_gl05_prepared"},
    )


def _sse_response() -> _Response:
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_gl05_prepared",
                    "type": "message",
                    "role": "assistant",
                    "model": "cir01-observed-model",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "ok"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    body = "".join(
        f"event: {name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        for name, payload in events
    )
    return _http.Response(
        200,
        content=body.encode(),
        headers={"content-type": "text/event-stream", "request-id": "req_gl05_prepared"},
    )
