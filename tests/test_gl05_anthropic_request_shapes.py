"""GL-05 slice 3: request shape selected by exact model id.

Legacy ids and unknown ids keep today's sampling request. The two candidate ids
use the candidate shape. This module does not activate either candidate.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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

_OFFLINE_KEY = "cir01-offline-test-key"
_PROMPT = "Synthetic request only"
_JSON_SYSTEM = (
    "You MUST respond with valid JSON only. No markdown, no explanation, "
    "no code fences. Output raw JSON."
)
_MARKER = "gl05-shape-marker"
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_BETWEEN_TOOLS_EFFORTS = ("low", "medium", "high")
_LEGACY_IDS = ("claude-sonnet-4-20250514", "claude-opus-4-20250514")
_CANDIDATE_IDS = ("claude-sonnet-5-5", "claude-opus-5-5")
_SRC = Path(__file__).resolve().parents[1] / "src"
_CAPABILITIES = _SRC / "idis" / "providers" / "anthropic_capabilities.py"
_CLIENT = _SRC / "idis" / "services" / "extraction" / "extractors" / "anthropic_client.py"


def _capabilities() -> Any:
    spec = importlib.util.find_spec("idis.providers.anthropic_capabilities")
    assert spec is not None
    return importlib.import_module("idis.providers.anthropic_capabilities")


def _assert_rejected(exc: BaseException, reason: str, *hidden: str) -> None:
    assert type(exc) is not RuntimeError
    assert isinstance(exc, RuntimeError)
    assert getattr(exc, "code", None) == "LLM_REQUEST_SHAPE_REJECTED"
    assert getattr(exc, "reason", None) == reason
    assert str(exc) == "LLM_REQUEST_SHAPE_REJECTED"
    for item in hidden:
        assert item not in str(exc)
        assert item not in repr(exc)
        assert item not in str(getattr(exc, "reason", ""))


def _user(content: str = _PROMPT) -> dict[str, str]:
    return {"role": "user", "content": content}


def _legacy_body(model: str, *, json_mode: bool) -> dict[str, object]:
    return {
        "model": model,
        "max_tokens": 173,
        "temperature": 0,
        "messages": [_user()],
        "system": _JSON_SYSTEM if json_mode else "",
    }


def _candidate_body(model: str, effort: str, *, json_mode: bool) -> dict[str, object]:
    body: dict[str, object] = {
        "model": model,
        "max_tokens": 173,
        "messages": [_user()],
        "output_config": {"effort": effort},
    }
    if json_mode:
        body["system"] = _JSON_SYSTEM
    return body


def _ok() -> httpx.Response | httpx2.Response:
    return _http.Response(
        200,
        json={
            "id": "msg_gl05_shape",
            "type": "message",
            "role": "assistant",
            "model": "cir01-observed-model",
            "content": [{"type": "text", "text": '{"ok":true}'}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
    )


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: str | None,
    json_mode: bool,
    effort: str | None = None,
    max_tokens: int | None = 173,
    prompt: str = _PROMPT,
) -> dict[str, Any]:
    requests: list[httpx.Request | httpx2.Request] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        requests.append(request)
        return _ok()

    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    kwargs: dict[str, Any] = {}
    if model is not None:
        kwargs["model"] = model
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if effort is not None:
        kwargs["effort"] = effort
    client = anthropic_client.AnthropicLLMClient(**kwargs)
    client._client.close()
    try:
        with anthropic.Anthropic(
            api_key=_OFFLINE_KEY,
            http_client=_http.Client(transport=_http.MockTransport(handle)),
            max_retries=0,
        ) as sdk_client:
            client._client = sdk_client
            assert client.call(prompt, json_mode=json_mode) == '{"ok":true}'
    finally:
        client._client.close()
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize("model", (*_LEGACY_IDS, "cir01-test-model"))
@pytest.mark.parametrize("json_mode", [False, True])
def test_legacy_and_unknown_ids_keep_the_cir01_sampling_body(
    monkeypatch: pytest.MonkeyPatch, model: str, json_mode: bool
) -> None:
    payload = _capture(monkeypatch, model=model, json_mode=json_mode)
    assert payload == _legacy_body(model, json_mode=json_mode)


def test_default_model_chain_keeps_the_legacy_sampling_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IDIS_ANTHROPIC_MODEL_EXTRACT", raising=False)
    payload = _capture(monkeypatch, model=None, json_mode=False, max_tokens=None)
    assert payload["model"] == "claude-sonnet-4-20250514"
    assert payload["temperature"] == 0
    assert payload["system"] == ""
    assert "output_config" not in payload
    assert "thinking" not in payload


@pytest.mark.parametrize("model", _CANDIDATE_IDS)
@pytest.mark.parametrize("effort", _EFFORTS)
def test_candidate_body_sends_explicit_effort_and_omits_sampling(
    monkeypatch: pytest.MonkeyPatch, model: str, effort: str
) -> None:
    payload = _capture(monkeypatch, model=model, json_mode=False, effort=effort)
    assert payload == _candidate_body(model, effort, json_mode=False)
    assert "temperature" not in payload
    assert "thinking" not in payload
    assert "system" not in payload
    assert "top_p" not in payload
    assert "top_k" not in payload
    assert "tool_choice" not in payload


@pytest.mark.parametrize(
    ("model", "effort"),
    [("claude-sonnet-5-5", "medium"), ("claude-opus-5-5", "high")],
)
def test_candidate_json_mode_keeps_the_system_sentence(
    monkeypatch: pytest.MonkeyPatch, model: str, effort: str
) -> None:
    payload = _capture(monkeypatch, model=model, json_mode=True, effort=effort)
    assert payload == _candidate_body(model, effort, json_mode=True)
    assert "temperature" not in payload
    assert "thinking" not in payload


@pytest.mark.parametrize("model", _CANDIDATE_IDS)
def test_candidate_without_effort_fails_before_the_sdk_client(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    built: list[int] = []
    original = anthropic_client.anthropic.Anthropic

    def spy(*args: object, **kwargs: object) -> anthropic.Anthropic:
        built.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(anthropic_client.anthropic, "Anthropic", spy)
    client: anthropic_client.AnthropicLLMClient | None = None
    try:
        with pytest.raises(RuntimeError) as caught:
            client = anthropic_client.AnthropicLLMClient(model=model)
        assert built == []
        _assert_rejected(caught.value, "effort_required")
    finally:
        if client is not None:
            client._client.close()


def test_env_candidate_without_effort_fails_before_the_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    monkeypatch.setenv("IDIS_ANTHROPIC_MODEL_EXTRACT", "claude-sonnet-5-5")
    built: list[int] = []
    original = anthropic_client.anthropic.Anthropic

    def spy(*args: object, **kwargs: object) -> anthropic.Anthropic:
        built.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(anthropic_client.anthropic, "Anthropic", spy)
    client: anthropic_client.AnthropicLLMClient | None = None
    try:
        with pytest.raises(RuntimeError) as caught:
            client = anthropic_client.AnthropicLLMClient()
        assert built == []
        _assert_rejected(caught.value, "effort_required")
    finally:
        if client is not None:
            client._client.close()


def test_candidate_with_effort_still_requires_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        anthropic_client.AnthropicLLMClient(model="claude-sonnet-5-5", effort="medium")


@pytest.mark.parametrize("model", (*_LEGACY_IDS, "not-a-known-model"))
@pytest.mark.parametrize("effort", ("high", "low"))
def test_effort_on_legacy_or_unknown_id_is_rejected_at_construction(
    monkeypatch: pytest.MonkeyPatch, model: str, effort: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    built: list[int] = []
    original = anthropic_client.anthropic.Anthropic

    def spy(*args: object, **kwargs: object) -> anthropic.Anthropic:
        built.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(anthropic_client.anthropic, "Anthropic", spy)
    client: anthropic_client.AnthropicLLMClient | None = None
    try:
        with pytest.raises(RuntimeError) as caught:
            client = anthropic_client.AnthropicLLMClient(model=model, effort=effort)
        assert built == []
        _assert_rejected(caught.value, "effort_not_applicable", effort)
    finally:
        if client is not None:
            client._client.close()


@pytest.mark.parametrize("effort", ("", "HIGH", "minimal", "default"))
def test_effort_outside_the_candidate_set_is_rejected_at_construction(
    monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    with pytest.raises(RuntimeError) as caught:
        anthropic_client.AnthropicLLMClient(model="claude-opus-5-5", effort=effort)
    hidden = () if effort == "" else (effort,)
    _assert_rejected(caught.value, "effort_unsupported", *hidden)


def test_table_rows_are_unique_and_candidates_are_not_qualified() -> None:
    caps = _capabilities()
    rows = caps.MODEL_CAPABILITIES
    ids = [row.model_id for row in rows]
    assert ids == [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
    ]
    assert len(ids) == len(set(ids))
    assert {row.shape_id for row in rows} == {"legacy-sampling-v1", "candidate-5-5-v1"}
    assert set(caps.ThinkingPolicy) == {caps.ThinkingPolicy.OMIT, caps.ThinkingPolicy.ADAPTIVE}
    by_id = {row.model_id: row for row in rows}
    for model_id in _LEGACY_IDS:
        row = by_id[model_id]
        assert row.shape_id == "legacy-sampling-v1"
        assert row.supports_sampling_controls is True
        assert row.supported_efforts == frozenset()
        assert row.default_effort_known is None
        assert row.thinking_policy == "OMIT"
        assert row.between_tools_allowed is False
        assert row.forced_tool_choice_allowed is False
        assert row.qualification_status == "ADMITTED_V1"
    sonnet = by_id["claude-sonnet-5-5"]
    opus = by_id["claude-opus-5-5"]
    for row in (sonnet, opus):
        assert row.shape_id == "candidate-5-5-v1"
        assert row.supports_sampling_controls is False
        assert row.supported_efforts == frozenset(_EFFORTS)
        assert row.thinking_policy == "OMIT"
        assert row.forced_tool_choice_allowed is False
        assert row.qualification_status == "CANDIDATE_NOT_QUALIFIED"
    assert sonnet.default_effort_known == "high"
    assert sonnet.between_tools_allowed is True
    assert opus.default_effort_known == "medium"
    assert opus.between_tools_allowed is False
    assert all(row.thinking_policy == "OMIT" for row in rows)


def test_unknown_id_resolves_to_the_legacy_shape_without_admission() -> None:
    caps = _capabilities()
    row = caps.resolve_model_capability("cir01-test-model")
    assert row.model_id == "cir01-test-model"
    assert row.shape_id == "legacy-sampling-v1"
    assert row.supports_sampling_controls is True
    assert row.supported_efforts == frozenset()
    assert row.default_effort_known is None
    assert row.thinking_policy == "OMIT"
    assert row.between_tools_allowed is False
    assert row.forced_tool_choice_allowed is False
    assert row.qualification_status == "UNKNOWN_ID_LEGACY_BOUND"
    assert "cir01-test-model" not in {item.model_id for item in caps.MODEL_CAPABILITIES}


def test_known_candidate_default_effort_is_not_applied_when_omitted() -> None:
    caps = _capabilities()
    messages = [_user()]
    for model_id, known in (("claude-sonnet-5-5", "high"), ("claude-opus-5-5", "medium")):
        assert caps.resolve_model_capability(model_id).default_effort_known == known
        with pytest.raises(RuntimeError) as caught:
            caps.build_request(
                model=model_id,
                max_tokens=173,
                system="",
                messages=messages,
                effort=None,
            )
        _assert_rejected(caught.value, "effort_required", known)


def test_candidate_ids_are_named_only_by_the_capability_table_and_client() -> None:
    needles = _CANDIDATE_IDS
    allowed = {_CAPABILITIES.resolve(), _CLIENT.resolve()}
    hits: list[str] = []
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles) and path.resolve() not in allowed:
            hits.append(path.relative_to(_SRC).as_posix())
    assert hits == []
    assert _CAPABILITIES.is_file()
    table = _CAPABILITIES.read_text(encoding="utf-8")
    assert "claude-sonnet-5-5" in table
    assert "claude-opus-5-5" in table


@pytest.mark.parametrize(
    ("thinking", "reason"),
    [
        ("disabled", "thinking_disabled"),
        ({"type": "disabled"}, "thinking_disabled"),
        ("enabled", "thinking_enabled"),
        ({"type": "enabled", "budget_tokens": 2048}, "thinking_enabled"),
        ("between_tools", "thinking_between_tools_not_allowed"),
        ({"type": "between_tools"}, "thinking_between_tools_not_allowed"),
        ("adaptive", "thinking_unsupported"),
        ({"type": "adaptive"}, "thinking_unsupported"),
    ],
)
def test_legacy_shape_rejects_thinking_overrides(thinking: object, reason: str) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-4-20250514",
            max_tokens=173,
            system="",
            messages=[_user()],
            thinking=thinking,
        )
    hidden = ("2048",) if reason == "thinking_enabled" else ()
    _assert_rejected(caught.value, reason, *hidden)


@pytest.mark.parametrize("model", _CANDIDATE_IDS)
@pytest.mark.parametrize(
    ("thinking", "reason", "hidden"),
    [
        ("disabled", "thinking_disabled", ()),
        ({"type": "disabled"}, "thinking_disabled", ()),
        ("enabled", "thinking_enabled", ()),
        ({"type": "enabled", "budget_tokens": 2048}, "thinking_enabled", ("2048",)),
    ],
)
def test_disabled_and_enabled_thinking_are_always_rejected(
    model: str, thinking: object, reason: str, hidden: tuple[str, ...]
) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model=model,
            max_tokens=173,
            system="",
            messages=[_user()],
            effort="low",
            thinking=thinking,
        )
    _assert_rejected(caught.value, reason, *hidden)


@pytest.mark.parametrize("effort", ("xhigh", "max"))
def test_between_tools_is_rejected_for_sonnet_above_high(effort: str) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-5-5",
            max_tokens=173,
            system="",
            messages=[_user()],
            effort=effort,
            thinking="between_tools",
        )
    _assert_rejected(caught.value, "thinking_between_tools_not_allowed", effort)


@pytest.mark.parametrize("effort", _EFFORTS)
def test_between_tools_is_rejected_for_opus(effort: str) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-opus-5-5",
            max_tokens=173,
            system="",
            messages=[_user()],
            effort=effort,
            thinking={"type": "between_tools"},
        )
    _assert_rejected(caught.value, "thinking_between_tools_not_allowed")


@pytest.mark.parametrize("effort", _BETWEEN_TOOLS_EFFORTS)
def test_between_tools_on_sonnet_sends_only_its_type(effort: str) -> None:
    caps = _capabilities()
    built = caps.build_request(
        model="claude-sonnet-5-5",
        max_tokens=173,
        system="",
        messages=[_user()],
        effort=effort,
        thinking="between_tools",
    )
    assert built["thinking"] == {"type": "between_tools"}
    assert built["output_config"] == {"effort": effort}
    assert "temperature" not in built
    assert "system" not in built


def test_between_tools_with_an_extra_field_is_rejected() -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-5-5",
            max_tokens=173,
            system="",
            messages=[_user()],
            effort="low",
            thinking={"type": "between_tools", "display": "summarized"},
        )
    _assert_rejected(caught.value, "thinking_unsupported", "summarized")


@pytest.mark.parametrize("model", _CANDIDATE_IDS)
def test_adaptive_thinking_sends_only_its_type(model: str) -> None:
    caps = _capabilities()
    built = caps.build_request(
        model=model,
        max_tokens=173,
        system="kept",
        messages=[_user()],
        effort="high",
        thinking={"type": "adaptive"},
    )
    assert built["thinking"] == {"type": "adaptive"}
    assert built["system"] == "kept"
    assert "temperature" not in built


def test_explicit_omit_matches_the_default_candidate_shape() -> None:
    caps = _capabilities()
    messages = [_user()]
    omitted = caps.build_request(
        model="claude-sonnet-5-5",
        max_tokens=173,
        system="",
        messages=messages,
        effort="medium",
        thinking="omit",
    )
    default = caps.build_request(
        model="claude-sonnet-5-5",
        max_tokens=173,
        system="",
        messages=messages,
        effort="medium",
    )
    assert omitted == default
    assert "thinking" not in omitted


def test_trailing_assistant_message_is_rejected() -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-5-5",
            max_tokens=173,
            system="",
            messages=[_user(), {"role": "assistant", "content": _MARKER}],
            effort="medium",
        )
    _assert_rejected(caught.value, "assistant_prefill", _MARKER)


def test_earlier_assistant_turn_is_not_prefill() -> None:
    caps = _capabilities()
    messages = [
        _user("one"),
        {"role": "assistant", "content": "earlier turn"},
        _user("three"),
    ]
    built = caps.build_request(
        model="claude-opus-5-5",
        max_tokens=173,
        system="",
        messages=messages,
        effort="low",
    )
    assert built["messages"] == messages
    assert "thinking" not in built


@pytest.mark.parametrize(
    "tool_choice",
    [
        {"type": "tool", "name": _MARKER},
        {"type": "any"},
        {"type": "auto"},
        {"type": "none"},
    ],
)
def test_tool_choice_argument_is_rejected(tool_choice: Mapping[str, str]) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-4-20250514",
            max_tokens=173,
            system="",
            messages=[_user()],
            tool_choice=tool_choice,
        )
    _assert_rejected(caught.value, "tool_choice_forbidden", _MARKER)


def test_rejection_does_not_echo_prompt_text() -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="cir01-test-model",
            max_tokens=173,
            system=_MARKER,
            messages=[_user(_MARKER)],
            effort="high",
        )
    _assert_rejected(caught.value, "effort_not_applicable", _MARKER)


@pytest.mark.parametrize(
    ("model", "effort", "thinking"),
    [
        ("claude-sonnet-5-5", "low", "between_tools"),
        ("claude-sonnet-5-5", "medium", "adaptive"),
        ("claude-opus-5-5", "high", {"type": "adaptive"}),
    ],
)
def test_sdk_encoder_accepts_the_candidate_thinking_cast(
    model: str, effort: str, thinking: object
) -> None:
    caps = _capabilities()
    built = caps.build_request(
        model=model,
        max_tokens=173,
        system="",
        messages=[_user()],
        effort=effort,
        thinking=thinking,
    )
    params = anthropic_client.to_message_create_params(built)
    requests: list[httpx.Request | httpx2.Request] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        requests.append(request)
        return _ok()

    with anthropic.Anthropic(
        api_key=_OFFLINE_KEY,
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
    ) as sdk_client:
        message = sdk_client.messages.create(**params)

    assert message.content[0].text == '{"ok":true}'
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["model"] == model
    assert payload["output_config"] == {"effort": effort}
    assert "temperature" not in payload
    assert "system" not in payload
    if thinking == "between_tools":
        assert payload["thinking"] == {"type": "between_tools"}
    else:
        assert payload["thinking"] == {"type": "adaptive"}
    assert set(payload["thinking"]) == {"type"}
