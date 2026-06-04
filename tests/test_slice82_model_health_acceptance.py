"""Slice82 acceptance proof — live (Anthropic) model health and policy.

Master-plan acceptance (verbatim):

    "Strict readiness distinguishes configured, health-checked, runtime-call-proven,
     and not-yet-FULL-used."

This module proves that statement end-to-end across the combined strict-readiness +
provisioning-truth system, plus the safe provider-metadata capture and the prompt/model
registry-linkage diagnostic. It exercises ONLY readiness / provisioning / health / registry
helpers — never a real provider call and never a FULL run. All probes use injected fakes.
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
_LEAK_MARKERS = (
    "sk-ant-SECRET",
    "sk-LEAK",
    "C:\\secret",
    "/var/secret",
    "PROMPT-BODY",
    "RESPONSE-BODY",
    _REQUEST_ID_SENTINEL,
)


def _healthy(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
    return LlmModelHealthCheck.healthy(
        role, backend="anthropic", provider="anthropic", models={"model": "claude-x"}
    )


def _failed_leaky(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
    return LlmModelHealthCheck.failed(
        role, error="sk-LEAK C:\\secret\\k /var/secret PROMPT-BODY RESPONSE-BODY"
    )


def _proven_with_reqid(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
    return LlmModelHealthCheck.healthy(
        role,
        backend="anthropic",
        provider="anthropic",
        models={"model": "claude-x"},
        runtime_call_proven=True,
        provider_request_id=_REQUEST_ID_SENTINEL,
    )


def _readiness(checker: Any = None, *, env: dict[str, str] | None = None) -> Any:
    return build_strict_full_live_readiness_report(
        env=_CONFIGURED_ENV if env is None else env,
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
        model_health_checker=checker,
    )


def _inventory(report: Any, name: str) -> Any:
    return next(item for item in report.component_inventory if item.component_name == name)


# ===== Acceptance 1: readiness distinguishes configured vs missing =====


def test_acceptance_missing_env_blocks_anthropic_components_safely() -> None:
    report = _readiness(env={})
    for name in _LLM_COMPONENTS:
        component = report.component(name)
        assert component.status is StrictComponentStatus.MISSING_CREDENTIALS
        assert component.may_proceed is False
    # safe: missing-credentials report carries no secret values / paths.
    blob = report.model_dump_json()
    for marker in _LEAK_MARKERS:
        assert marker not in blob


def test_acceptance_configured_env_is_health_checked_live() -> None:
    report = _readiness(env=_CONFIGURED_ENV)
    for name in _LLM_COMPONENTS:
        component = report.component(name)
        assert component.status is StrictComponentStatus.LIVE_WIRED_AND_USED
        assert component.may_proceed is True
    # inventory health-check status reflects the no-network model health.
    for name in _ANTHROPIC_INVENTORY:
        assert _inventory(report, name).health_check_status == "healthy"


# ===== Acceptance 2: readiness distinguishes healthy vs failed health (no leak) =====


def test_acceptance_healthy_checker_marks_inventory_healthy() -> None:
    report = _readiness(_healthy)
    for name in _ANTHROPIC_INVENTORY:
        assert _inventory(report, name).health_check_status == "healthy"


def test_acceptance_failed_checker_is_fail_closed_without_leak() -> None:
    report = _readiness(_failed_leaky)
    for name in _LLM_COMPONENTS:
        component = report.component(name)
        assert component.status is StrictComponentStatus.CONFIGURED_BUT_FAILED_HEALTH_CHECK
        assert component.may_proceed is False
    for name in _ANTHROPIC_INVENTORY:
        assert _inventory(report, name).health_check_status == "configured_failed"
    # no .error / key / prompt / response / path leaks anywhere in the report.
    blob = report.model_dump_json()
    for marker in (*_LEAK_MARKERS, "[redacted]"):
        assert marker not in blob


# ===== Acceptance 3: runtime-call-proven is a distinct state (no real call) =====


def test_acceptance_default_no_network_is_not_runtime_proven() -> None:
    # The no-network default check never constructs a client even if one is available.
    def _raising_factory(_api_key: str) -> Any:
        raise AssertionError("no client may be constructed on the no-network path")

    result = check_llm_model_health(
        env=_CONFIGURED_ENV, role=LlmModelRole.EXTRACTION, client_factory=_raising_factory
    )
    assert result.status is LlmModelHealthStatus.HEALTHY
    assert result.runtime_call_proven is False
    # provisioning default path keeps runtime_call_proven False for every Anthropic component.
    report = build_strict_provisioning_truth_report(env=_CONFIGURED_ENV)
    components = {c["component_name"]: c for c in report["components"]}
    for name in _ANTHROPIC_INVENTORY:
        assert components[name]["runtime_call_proven"] is False


def test_acceptance_opt_in_fake_probe_is_runtime_proven_without_real_call() -> None:
    class _FakeResponse:
        id = "msg_safe_request_id"

    class _FakeMessages:
        def create(self, **_kwargs: Any) -> _FakeResponse:
            return _FakeResponse()

    class _FakeClient:
        def __init__(self) -> None:
            self.messages = _FakeMessages()

    result = check_llm_model_health(
        env=_CONFIGURED_ENV,
        role=LlmModelRole.EXTRACTION,
        run_probe=True,
        client_factory=lambda _api_key: _FakeClient(),
    )
    assert result.runtime_call_proven is True
    assert result.provider_request_id == "msg_safe_request_id"
    # provisioning opt-in proven path surfaces runtime_call_proven True.
    report = build_strict_provisioning_truth_report(
        env=_CONFIGURED_ENV,
        allow_local_strict_health_probes=True,
        model_health_checker=_proven_with_reqid,
    )
    components = {c["component_name"]: c for c in report["components"]}
    for name in _ANTHROPIC_INVENTORY:
        assert components[name]["runtime_call_proven"] is True


# ===== Acceptance 4: not-yet-FULL-used is explicit =====


def test_acceptance_full_run_used_is_always_false() -> None:
    for report in (
        build_strict_provisioning_truth_report(env=_CONFIGURED_ENV),
        build_strict_provisioning_truth_report(
            env=_CONFIGURED_ENV,
            allow_local_strict_health_probes=True,
            model_health_checker=_proven_with_reqid,
        ),
    ):
        components = {c["component_name"]: c for c in report["components"]}
        for name in _ANTHROPIC_INVENTORY:
            assert components[name]["full_run_used"] is False


# ===== Acceptance 5: four-state truth across readiness + provisioning =====


def test_acceptance_four_state_truth_is_accepted_across_the_system() -> None:
    readiness = _readiness(_proven_with_reqid)
    provisioning = build_strict_provisioning_truth_report(
        env=_CONFIGURED_ENV,
        allow_local_strict_health_probes=True,
        model_health_checker=_proven_with_reqid,
    )
    components = {c["component_name"]: c for c in provisioning["components"]}
    for inventory_name, component_name in zip(_ANTHROPIC_INVENTORY, _LLM_COMPONENTS, strict=True):
        # configured: present in both layers.
        provisioning_component = components[inventory_name]
        assert provisioning_component["configured"] is True
        # health-checked: readiness inventory carries the no-network health-check status.
        assert _inventory(readiness, inventory_name).health_check_status == "healthy"
        # runtime-call-proven: provisioning carries it (opt-in).
        assert provisioning_component["runtime_call_proven"] is True
        # not-yet-FULL-used: explicit.
        assert provisioning_component["full_run_used"] is False
        # provisioning health_checked stays False = intentional Slice72 anti-overclaiming.
        assert provisioning_component["health_checked"] is False
        # readiness component itself is live.
        readiness_component = readiness.component(component_name)
        assert readiness_component.status is StrictComponentStatus.LIVE_WIRED_AND_USED


# ===== Acceptance 6: provider metadata / request-id safety =====


def test_acceptance_request_id_captured_in_health_but_not_leaked_by_reports() -> None:
    captured = _proven_with_reqid(_CONFIGURED_ENV, LlmModelRole.EXTRACTION)
    assert captured.provider_request_id == _REQUEST_ID_SENTINEL  # captured in the health result

    readiness = _readiness(_proven_with_reqid)
    provisioning = build_strict_provisioning_truth_report(
        env=_CONFIGURED_ENV,
        allow_local_strict_health_probes=True,
        model_health_checker=_proven_with_reqid,
    )
    # ...but neither consuming report leaks it (no approved request-id field on the reports).
    assert _REQUEST_ID_SENTINEL not in readiness.model_dump_json()
    assert _REQUEST_ID_SENTINEL not in json.dumps(provisioning)
    for marker in _LEAK_MARKERS:
        assert marker not in readiness.model_dump_json()
        assert marker not in json.dumps(provisioning)


# ===== Acceptance 7: prompt/model registry linkage (safe, no mutation) =====


def test_acceptance_registry_linkage_surfaces_mismatch_safely() -> None:
    before = _REGISTRY_YAML.read_bytes()
    linkage = summarize_prompt_registry_model_linkage()
    after = _REGISTRY_YAML.read_bytes()

    assert isinstance(linkage, PromptRegistryModelLinkage)
    assert linkage.provider_mismatch is True
    assert linkage.registry_provider_families == ["openai"]
    assert linkage.runtime_provider_family == "anthropic"
    assert before == after  # no registry mutation
    # safe, label-only: no Anthropic names, no secrets, no env values, no paths/prompt bodies.
    blob = linkage.model_dump_json()
    assert "claude" not in blob
    for marker in _LEAK_MARKERS:
        assert marker not in blob


# ===== Acceptance 8: no FULL run boundary =====


def test_acceptance_no_full_run_is_triggered_or_cleared() -> None:
    # Even fully model-configured (but infra absent), readiness never clears FULL.
    readiness = _readiness(_healthy)
    assert readiness.may_proceed is False

    provisioning = build_strict_provisioning_truth_report(
        env=_CONFIGURED_ENV,
        allow_local_strict_health_probes=True,
        model_health_checker=_proven_with_reqid,
    )
    assert provisioning["strict_global_may_proceed"] is False
    assert provisioning["readiness_may_proceed"] is False
    assert provisioning["real_example_not_run"] is True
    assert provisioning["live_provider_calls_made"] is False
