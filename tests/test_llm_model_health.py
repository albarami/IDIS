"""Slice82 Task 2 — standalone live (Anthropic) model-health check tests.

TDD RED-first. Mirrors embedding_health/ocr_health/media_health. The DEFAULT path is
**no-network**: it validates backend/credential/model config only and never instantiates
a provider client; ``runtime_call_proven`` is False. An OPT-IN runtime probe (explicit
``run_probe=True`` + injectable ``client_factory``) is the only path that touches a client
— tests always inject a fake (no real Anthropic call). The result is safe: no API key,
prompt text, response text, raw exception message, or provider payload — only fixed safe
identifiers, safe model names, provider name, and a sanitized request id.
"""

from __future__ import annotations

from typing import Any

import pytest

from idis.services.llm_model_health import (
    LlmModelHealthCheck,
    LlmModelHealthStatus,
    LlmModelRole,
    check_llm_model_health,
)

_API_KEY = "configured-not-a-real-key"


def _extract_env(**overrides: str) -> dict[str, str]:
    base = {
        "IDIS_EXTRACT_BACKEND": "anthropic",
        "ANTHROPIC_API_KEY": _API_KEY,
        "IDIS_ANTHROPIC_MODEL_EXTRACT": "claude-sonnet-4-20250514",
    }
    base.update(overrides)
    return base


def _debate_env(**overrides: str) -> dict[str, str]:
    base = {
        "IDIS_DEBATE_BACKEND": "anthropic",
        "ANTHROPIC_API_KEY": _API_KEY,
        "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT": "claude-sonnet-4-20250514",
        "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER": "claude-opus-4-20250514",
    }
    base.update(overrides)
    return base


class _FakeResponse:
    def __init__(self, response_id: str) -> None:
        self.id = response_id
        self.model = "claude-sonnet-4-20250514"


class _FakeMessages:
    def __init__(self, response_id: str) -> None:
        self._response_id = response_id

    def create(self, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._response_id)


class _FakeClient:
    def __init__(self, response_id: str = "msg_01HEALTHCHECKxyz") -> None:
        self.messages = _FakeMessages(response_id)


def _fake_factory(_api_key: str) -> _FakeClient:
    return _FakeClient()


def _raising_factory(_api_key: str) -> _FakeClient:
    raise AssertionError("client_factory must NOT be called on the no-network default path")


# --- 1. disabled when backend not anthropic ---


def test_disabled_when_backend_not_configured() -> None:
    for env in ({}, {"IDIS_EXTRACT_BACKEND": "deterministic"}, {"IDIS_EXTRACT_BACKEND": ""}):
        result = check_llm_model_health(env=env, role=LlmModelRole.EXTRACTION)
        assert result.status is LlmModelHealthStatus.DISABLED
        assert result.configured is False
        assert result.runtime_call_proven is False
        assert result.error is None


# --- 2. configured healthy in default no-network mode ---


def test_configured_healthy_default_is_no_network() -> None:
    result = check_llm_model_health(env=_extract_env(), role=LlmModelRole.EXTRACTION)
    assert result.status is LlmModelHealthStatus.HEALTHY
    assert result.configured is True
    assert result.runtime_call_proven is False
    assert result.backend == "anthropic"
    assert result.provider == "anthropic"
    assert result.models == {"extract_model": "claude-sonnet-4-20250514"}
    assert result.error is None
    assert result.provider_request_id is None


# --- 3. missing API key ---


def test_missing_api_key_is_missing_credentials_with_safe_id() -> None:
    env = _extract_env()
    env.pop("ANTHROPIC_API_KEY")
    result = check_llm_model_health(env=env, role=LlmModelRole.EXTRACTION)
    assert result.status is LlmModelHealthStatus.MISSING_CREDENTIALS
    assert result.configured is False
    assert "anthropic_api_key" in result.missing_dependencies


# --- 4. missing model env(s) with only fixed safe identifiers ---


def test_missing_model_uses_fixed_safe_identifier() -> None:
    env = _extract_env()
    env.pop("IDIS_ANTHROPIC_MODEL_EXTRACT")
    result = check_llm_model_health(env=env, role=LlmModelRole.EXTRACTION)
    assert result.status is LlmModelHealthStatus.MISSING_CREDENTIALS
    assert "extract_model" in result.missing_dependencies
    # only safe fixed identifiers, never the raw env var name
    assert "IDIS_ANTHROPIC_MODEL_EXTRACT" not in result.missing_dependencies


# --- 5. unsupported backend is failed without echoing the raw value ---


def test_unsupported_backend_is_failed_without_echoing_value() -> None:
    result = check_llm_model_health(
        env=_extract_env(IDIS_EXTRACT_BACKEND="openai-secret-value"),
        role=LlmModelRole.EXTRACTION,
    )
    assert result.status is LlmModelHealthStatus.FAILED
    assert result.error is not None
    assert "openai-secret-value" not in result.error


# --- 6. opt-in runtime probe success -> runtime_call_proven + safe metadata ---


def test_runtime_probe_success_proves_runtime_call_and_captures_safe_metadata() -> None:
    result = check_llm_model_health(
        env=_extract_env(),
        role=LlmModelRole.EXTRACTION,
        run_probe=True,
        client_factory=_fake_factory,
    )
    assert result.status is LlmModelHealthStatus.HEALTHY
    assert result.runtime_call_proven is True
    assert result.provider_request_id == "msg_01HEALTHCHECKxyz"
    assert result.models == {"extract_model": "claude-sonnet-4-20250514"}


# --- 7. opt-in runtime probe failure -> failed, not proven, no leak ---


def test_runtime_probe_failure_is_failed_without_leak() -> None:
    confidential = (
        "ANTHROPIC FATAL sk-LEAK123 C:\\secret\\key /var/secret PROMPT-BODY RESPONSE-BODY"
    )

    class _FailingMessages:
        def create(self, **kwargs: Any) -> _FakeResponse:
            raise RuntimeError(confidential)

    class _FailingClient:
        def __init__(self) -> None:
            self.messages = _FailingMessages()

    def _failing_factory(_api_key: str) -> _FailingClient:
        return _FailingClient()

    result = check_llm_model_health(
        env=_extract_env(),
        role=LlmModelRole.EXTRACTION,
        run_probe=True,
        client_factory=_failing_factory,
    )
    assert result.status is LlmModelHealthStatus.FAILED
    assert result.runtime_call_proven is False
    assert result.error is not None
    for marker in (
        "ANTHROPIC FATAL",
        "sk-LEAK123",
        "C:\\secret",
        "/var/secret",
        "PROMPT-BODY",
        "RESPONSE-BODY",
    ):
        assert marker not in result.error


# --- 8. sanitization / truncation ---


def test_failed_error_is_sanitized_and_truncated() -> None:
    nasty = (
        "boom C:\\secret\\models\\key.txt and /usr/share/secret/key token sk-ABC123def456 "
        "prompt=THE-PROMPT-TEXT " + ("x" * 400)
    )
    result = LlmModelHealthCheck.failed(role=LlmModelRole.EXTRACTION, error=nasty)
    assert result.status is LlmModelHealthStatus.FAILED
    assert result.error is not None
    assert "C:\\secret" not in result.error
    assert "/usr/share/secret" not in result.error
    assert "sk-ABC123def456" not in result.error
    assert len(result.error) <= 240


# --- 9. result schema is strict / only safe fields ---


def test_result_exposes_only_safe_fields() -> None:
    env = {
        "IDIS_EXTRACT_BACKEND": "anthropic",
        "ANTHROPIC_API_KEY": "sk-ant-CONFIDENTIAL-should-not-appear",
        "IDIS_ANTHROPIC_MODEL_EXTRACT": "claude-sonnet-4-20250514",
    }
    result = check_llm_model_health(env=env, role=LlmModelRole.EXTRACTION)
    assert set(result.model_dump().keys()) == {
        "status",
        "role",
        "configured",
        "backend",
        "provider",
        "models",
        "missing_dependencies",
        "runtime_call_proven",
        "provider_request_id",
        "error",
    }
    blob = result.model_dump_json()
    assert "CONFIDENTIAL" not in blob
    assert "sk-ant-" not in blob


# --- 10. sorted / deduped missing dependencies ---


def test_missing_dependencies_are_sorted_and_deduped() -> None:
    result = check_llm_model_health(
        env={"IDIS_DEBATE_BACKEND": "anthropic"},
        role=LlmModelRole.DEBATE,
    )
    assert result.status is LlmModelHealthStatus.MISSING_CREDENTIALS
    assert result.missing_dependencies == sorted(set(result.missing_dependencies))
    assert result.missing_dependencies == [
        "anthropic_api_key",
        "debate_arbiter_model",
        "debate_model",
    ]


# --- 11. multiple roles checked independently ---


@pytest.mark.parametrize(
    ("role", "env", "expected_models"),
    [
        (LlmModelRole.EXTRACTION, "extract", {"extract_model": "claude-sonnet-4-20250514"}),
        (
            LlmModelRole.DEBATE,
            "debate",
            {
                "debate_model": "claude-sonnet-4-20250514",
                "debate_arbiter_model": "claude-opus-4-20250514",
            },
        ),
        (LlmModelRole.ANALYSIS, "debate", {"analysis_model": "claude-sonnet-4-20250514"}),
        (LlmModelRole.SCORING, "debate", {"scoring_model": "claude-sonnet-4-20250514"}),
    ],
)
def test_roles_checked_independently(
    role: LlmModelRole, env: str, expected_models: dict[str, str]
) -> None:
    values = _extract_env() if env == "extract" else _debate_env()
    result = check_llm_model_health(env=values, role=role)
    assert result.status is LlmModelHealthStatus.HEALTHY
    assert result.role == role.value
    assert result.models == expected_models


# --- 12. no network by default (client_factory not called unless run_probe) ---


def test_no_network_by_default_client_factory_unused() -> None:
    # A factory that raises if called proves the default path never constructs a client.
    result = check_llm_model_health(
        env=_extract_env(),
        role=LlmModelRole.EXTRACTION,
        client_factory=_raising_factory,
    )
    assert result.status is LlmModelHealthStatus.HEALTHY
    assert result.runtime_call_proven is False
