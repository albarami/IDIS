"""Slice82 Task 4 — provider-metadata capture + thin prompt-registry model linkage.

TDD RED-first. Two concerns:

1. The opt-in runtime probe captures only SAFE provider metadata (request id, provider name,
   model name(s), role, runtime_call_proven=True) and never prompt/response text, API key,
   path, raw payload, or exception message. The default no-network path never constructs a
   client. Strict readiness/provisioning consume only safe status/runtime flags — never raw
   provider metadata and never the ``.error`` body. (Most of this is Task 2/3 behavior; these
   are guard tests pinning it.)
2. A thin, additive registry-linkage diagnostic surfaces the OpenAI-vs-Anthropic model-name
   mismatch (registry model classes default to OpenAI names while the live runtime backend is
   Anthropic) as a SAFE, label-only diagnostic, WITHOUT mutating the registry YAML. (New code.)
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from idis.services.llm_model_health import (
    LlmModelHealthCheck,
    LlmModelHealthStatus,
    LlmModelRole,
    PromptRegistryModelLinkage,
    check_llm_model_health,
    summarize_prompt_registry_model_linkage,
)
from idis.services.runs.strict_full_live import (
    StrictComponentStatus,
    build_strict_full_live_readiness_report,
    build_strict_provisioning_truth_report,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REGISTRY_YAML = _REPO_ROOT / "prompts" / "registry.yaml"

_LLM_COMPONENTS = ("live_llm_model_clients", "agent_analysis", "debate_layer_1", "scoring")
_ANTHROPIC_INVENTORY = (
    "Anthropic extraction",
    "Anthropic debate",
    "Anthropic analysis",
    "Anthropic scoring",
)
_CONFIGURED_ENV: dict[str, str] = {
    "IDIS_EXTRACT_BACKEND": "anthropic",
    "IDIS_DEBATE_BACKEND": "anthropic",
    "ANTHROPIC_API_KEY": "sk-ant-SECRET-must-never-surface",
    "IDIS_ANTHROPIC_MODEL_EXTRACT": "claude-sonnet-4-20250514",
    "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT": "claude-sonnet-4-20250514",
    "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER": "claude-opus-4-20250514",
}

_REQUEST_ID_SENTINEL = "msg_SECRET_reqid_should_not_surface"


def _extract_env(**overrides: str) -> dict[str, str]:
    base = {
        "IDIS_EXTRACT_BACKEND": "anthropic",
        "ANTHROPIC_API_KEY": "sk-ant-CONFIDENTIAL-not-real",
        "IDIS_ANTHROPIC_MODEL_EXTRACT": "claude-sonnet-4-20250514",
    }
    base.update(overrides)
    return base


# --- fakes for the opt-in runtime probe (no real provider call) ---


class _FakeResponse:
    def __init__(self, response_id: str) -> None:
        self.id = response_id
        self.model = "claude-sonnet-4-20250514"
        self.content = "RESPONSE-BODY-should-not-be-captured"


class _FakeMessages:
    def __init__(self, response_id: str) -> None:
        self._response_id = response_id

    def create(self, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._response_id)


class _FakeClient:
    def __init__(self, response_id: str) -> None:
        self.messages = _FakeMessages(response_id)


def _factory(response_id: str):
    def _make(_api_key: str) -> _FakeClient:
        return _FakeClient(response_id)

    return _make


# ===== 1. opt-in probe success captures only safe metadata =====


def test_opt_in_probe_success_captures_safe_provider_metadata() -> None:
    result = check_llm_model_health(
        env=_extract_env(),
        role=LlmModelRole.EXTRACTION,
        run_probe=True,
        client_factory=_factory("msg_01SAFErequestid"),
    )
    assert result.status is LlmModelHealthStatus.HEALTHY
    assert result.runtime_call_proven is True
    assert result.provider_request_id == "msg_01SAFErequestid"
    assert result.provider == "anthropic"
    assert result.role == LlmModelRole.EXTRACTION.value
    assert result.models == {"extract_model": "claude-sonnet-4-20250514"}
    assert result.error is None
    # No API key, response body, or raw payload anywhere in the serialized result.
    blob = result.model_dump_json()
    for marker in ("sk-ant-", "CONFIDENTIAL", "RESPONSE-BODY"):
        assert marker not in blob


def test_opt_in_probe_request_id_is_sanitized() -> None:
    # A hostile request id carrying a secret/path is sanitized before capture.
    result = check_llm_model_health(
        env=_extract_env(),
        role=LlmModelRole.EXTRACTION,
        run_probe=True,
        client_factory=_factory("sk-LEAK999 C:\\secret\\path"),
    )
    assert result.status is LlmModelHealthStatus.HEALTHY
    assert result.provider_request_id is not None
    assert "sk-LEAK999" not in result.provider_request_id
    assert "C:\\secret" not in result.provider_request_id


# ===== 2. opt-in probe failure stays safe =====


def test_opt_in_probe_failure_is_safe() -> None:
    confidential = "FATAL sk-LEAK123 C:\\secret /var/secret PROMPT-BODY RESPONSE-BODY"

    class _FailingMessages:
        def create(self, **_kwargs: Any) -> _FakeResponse:
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
    assert result.error == "RuntimeError"
    blob = result.model_dump_json()
    for marker in ("sk-LEAK123", "C:\\secret", "/var/secret", "PROMPT-BODY", "RESPONSE-BODY"):
        assert marker not in blob


# ===== 3. default no-network path never constructs a client =====


def test_default_no_network_path_never_constructs_client() -> None:
    def _raising_factory(_api_key: str) -> Any:
        raise AssertionError("client must not be constructed on the no-network default path")

    result = check_llm_model_health(
        env=_extract_env(),
        role=LlmModelRole.EXTRACTION,
        client_factory=_raising_factory,
    )
    assert result.status is LlmModelHealthStatus.HEALTHY
    assert result.runtime_call_proven is False
    assert result.provider_request_id is None


# ===== 4. strict readiness/provisioning consume metadata safely (no leak) =====


def _proven_with_reqid(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
    return LlmModelHealthCheck.healthy(
        role,
        backend="anthropic",
        provider="anthropic",
        models={"model": "claude-x"},
        runtime_call_proven=True,
        provider_request_id=_REQUEST_ID_SENTINEL,
    )


def test_readiness_does_not_leak_provider_metadata() -> None:
    report = build_strict_full_live_readiness_report(
        env=_CONFIGURED_ENV,
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
        model_health_checker=_proven_with_reqid,
    )
    for name in _LLM_COMPONENTS:
        component = report.component(name)
        assert component.status is StrictComponentStatus.LIVE_WIRED_AND_USED
        # readiness exposes safe fields only -- no provider_request_id field at all.
        assert "provider_request_id" not in component.model_dump()
    assert _REQUEST_ID_SENTINEL not in report.model_dump_json()


def test_provisioning_does_not_leak_provider_metadata() -> None:
    report = build_strict_provisioning_truth_report(
        env=_CONFIGURED_ENV,
        allow_local_strict_health_probes=True,
        model_health_checker=_proven_with_reqid,
    )
    components = {c["component_name"]: c for c in report["components"]}
    for name in _ANTHROPIC_INVENTORY:
        component = components[name]
        assert component["runtime_call_proven"] is True  # safe flag is surfaced
        assert "provider_request_id" not in component  # raw metadata is not
        assert "error" not in component
    assert _REQUEST_ID_SENTINEL not in json.dumps(report)


# ===== 5. thin registry linkage surfaces the OpenAI/Anthropic mismatch (safe) =====


def test_registry_linkage_surfaces_openai_anthropic_mismatch() -> None:
    linkage = summarize_prompt_registry_model_linkage()
    assert isinstance(linkage, PromptRegistryModelLinkage)
    assert linkage.provider_mismatch is True
    assert linkage.runtime_provider_family == "anthropic"
    assert linkage.registry_provider_families == ["openai"]
    assert linkage.registry_model_names == ["gpt-3.5-turbo", "gpt-4o", "gpt-4o-mini"]
    # prompt -> model_class links are surfaced (safe labels already in the registry).
    assert linkage.prompt_count > 0
    assert linkage.prompt_model_classes["EXTRACT_CLAIMS_V1"] == "fast"
    assert linkage.prompt_model_classes["DEBATE_ARBITER_V1"] == "reasoning"
    assert linkage.prompt_model_classes["SANAD_GRADER_V1"] == "verifier"


def test_registry_linkage_is_safe_label_only() -> None:
    linkage = summarize_prompt_registry_model_linkage()
    blob = linkage.model_dump_json()
    # Only safe model-name strings / classes / ids -- no Anthropic names, no secrets, no paths.
    assert "claude" not in blob
    assert "sk-" not in blob
    for name in linkage.registry_model_names:
        assert name.strip() == name
        assert "/" not in name and "\\" not in name and " " not in name
    for model_class in linkage.prompt_model_classes.values():
        assert model_class in {"fast", "reasoning", "verifier"}


def test_registry_linkage_no_mismatch_when_registry_matches_runtime(tmp_path: Path) -> None:
    crafted = tmp_path / "registry.yaml"
    crafted.write_text(
        "prompts:\n"
        "  SOME_PROMPT_V1:\n"
        "    model_requirements:\n"
        "      model_class: fast\n"
        "model_classes:\n"
        "  fast:\n"
        "    default_model: claude-sonnet-4-20250514\n"
        "    fallback_model: claude-opus-4-20250514\n",
        encoding="utf-8",
    )
    linkage = summarize_prompt_registry_model_linkage(registry_path=crafted)
    assert linkage.registry_provider_families == ["anthropic"]
    assert linkage.runtime_provider_family == "anthropic"
    assert linkage.provider_mismatch is False


def test_registry_linkage_does_not_mutate_yaml() -> None:
    before = _REGISTRY_YAML.read_bytes()
    summarize_prompt_registry_model_linkage()
    after = _REGISTRY_YAML.read_bytes()
    assert before == after


def test_registry_linkage_result_exposes_only_safe_fields() -> None:
    linkage = summarize_prompt_registry_model_linkage()
    assert set(linkage.model_dump().keys()) == {
        "prompt_count",
        "prompt_model_classes",
        "registry_model_names",
        "registry_provider_families",
        "runtime_provider_family",
        "provider_mismatch",
        "summary",
    }
