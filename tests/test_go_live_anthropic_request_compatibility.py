"""CIR-01: exercise the installed SDK request contract without provider access."""

from __future__ import annotations

import inspect
import json

import anthropic
import httpx
import httpx2
import pytest

from idis.models.methodology_extraction import (
    AnthropicMethodologyTool,
    AnthropicMethodologyToolChoice,
    AnthropicMethodologyWireRequest,
)
from idis.services.extraction.extractors import anthropic_client
from idis.services.extraction.methodology_task_extractor import AnthropicSDKMethodologyTransport

# Qualify either SDK transport generation without replacing its real request signature.
_http = (
    httpx2
    if "httpx2" in str(inspect.signature(anthropic.Anthropic).parameters["http_client"].annotation)
    else httpx
)


@pytest.mark.parametrize("json_mode", [False, True])
def test_real_sdk_serializes_legacy_request_without_changing_sampling(
    monkeypatch: pytest.MonkeyPatch, json_mode: bool
) -> None:
    """Use the real SDK signature, encoder and parser over a hermetic transport."""
    requests: list[httpx.Request | httpx2.Request] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        requests.append(request)
        return _message_response()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "cir01-offline-test-key")
    client = anthropic_client.AnthropicLLMClient(model="cir01-test-model", max_tokens=173)
    client._client.close()
    with anthropic.Anthropic(
        api_key="cir01-offline-test-key",
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
    ) as sdk_client:
        client._client = sdk_client
        assert client.call("Synthetic request only", json_mode=json_mode) == '{"ok":true}'

    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload == {
        "model": "cir01-test-model",
        "max_tokens": 173,
        "temperature": 0,
        "messages": [{"role": "user", "content": "Synthetic request only"}],
        "system": (
            "You MUST respond with valid JSON only. No markdown, no explanation, "
            "no code fences. Output raw JSON."
            if json_mode
            else ""
        ),
    }


@pytest.mark.parametrize(
    ("status", "code", "attempts", "expected_backoffs"),
    [
        (429, "LLM_PROVIDER_RATE_LIMITED", 3, [1.0, 2.0]),
        (503, "LLM_PROVIDER_OUTCOME_UNKNOWN", 1, []),
    ],
)
def test_real_sdk_transient_response_preserves_client_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    code: str,
    attempts: int,
    expected_backoffs: list[float],
) -> None:
    calls = 0
    backoffs: list[float] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        nonlocal calls
        calls += 1
        return _http.Response(
            status,
            json={"type": "error", "error": {"message": "gl05-body-marker"}},
            headers={"request-id": "req_cir01_status"},
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "cir01-offline-test-key")
    monkeypatch.setattr(anthropic_client.time, "sleep", backoffs.append)
    client = anthropic_client.AnthropicLLMClient(model="cir01-test-model")
    client._client.close()
    with anthropic.Anthropic(
        api_key="cir01-offline-test-key",
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
    ) as sdk_client:
        client._client = sdk_client
        with pytest.raises(RuntimeError) as caught:
            client.call("Synthetic request only")

    exc = caught.value
    assert calls == attempts
    assert backoffs == expected_backoffs
    assert getattr(exc, "code", None) == code
    assert str(exc) == f"{code} {status}"
    assert getattr(exc, "request_id", None) == "req_cir01_status"
    assert "gl05-body-marker" not in str(exc)


def test_real_sdk_non_retryable_response_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        nonlocal calls
        calls += 1
        return _http.Response(
            400,
            json={"type": "error", "error": {"message": "gl05-body-marker"}},
            headers={"request-id": "req_cir01_rejected"},
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "cir01-offline-test-key")
    client = anthropic_client.AnthropicLLMClient(model="cir01-test-model")
    client._client.close()
    with anthropic.Anthropic(
        api_key="cir01-offline-test-key",
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
    ) as sdk_client:
        client._client = sdk_client
        with pytest.raises(RuntimeError, match="LLM_PROVIDER_REQUEST_REJECTED 400") as caught:
            client.call("Synthetic request only")
    exc = caught.value
    assert calls == 1
    assert getattr(exc, "code", None) == "LLM_PROVIDER_REQUEST_REJECTED"
    assert getattr(exc, "request_id", None) == "req_cir01_rejected"
    assert "gl05-body-marker" not in str(exc)


def test_real_sdk_accepts_exact_frozen_methodology_wire_request() -> None:
    """The pinned SDK must support the unchanged reserved and dispatched payload."""
    wire_request = AnthropicMethodologyWireRequest(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature=0.0,
        system="governed synthetic system prompt\nwith exact layout\n",
        messages=({"role": "user", "content": [{"type": "text", "text": "facts\n"}]},),
        tools=(
            AnthropicMethodologyTool(
                name="methodology_result",
                description="Return governed synthetic methodology evidence.",
                input_schema={"type": "object", "required": ["outcome"]},
                strict=True,
            ),
        ),
        tool_choice=AnthropicMethodologyToolChoice(
            type="tool", name="methodology_result", disable_parallel_tool_use=True
        ),
    )
    requests: list[httpx.Request | httpx2.Request] = []
    dispatched_payload = {
        "id": "msg_cir01_methodology",
        "type": "message",
        "role": "assistant",
        "model": wire_request.model,
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_cir01_methodology",
                "name": "methodology_result",
                "input": {"outcome": "EVIDENCE_NOT_FOUND"},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        requests.append(request)
        return _http.Response(200, json=dispatched_payload)

    with anthropic.Anthropic(
        api_key="cir01-offline-test-key",
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
    ) as sdk_client:

        def client_factory(*, api_key: str, max_retries: int) -> anthropic.Anthropic:
            assert api_key == "cir01-offline-test-key"
            assert max_retries == sdk_client.max_retries == 0
            return sdk_client

        transport = AnthropicSDKMethodologyTransport(
            api_key="cir01-offline-test-key", client_factory=client_factory
        )
        response = transport.create_message(
            **wire_request.as_sdk_kwargs(), retries=0, batch_mode=False
        )

    assert len(requests) == 1
    assert json.loads(requests[0].content) == json.loads(wire_request.exact_payload())
    assert response.backend == "hermetic"
    assert response.observed_model == wire_request.model
    assert response.provider_request_id == "msg_cir01_methodology"
    assert response.input_tokens == 3
    assert response.output_tokens == 4
    assert response.stop_reason == "tool_use"
    assert response.content_blocks[0]["input"] == {"outcome": "EVIDENCE_NOT_FOUND"}


def _message_response() -> httpx.Response | httpx2.Response:
    return _http.Response(
        200,
        json={
            "id": "msg_cir01_offline",
            "type": "message",
            "role": "assistant",
            "model": "cir01-test-model",
            "content": [{"type": "text", "text": '{"ok":true}'}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
    )
