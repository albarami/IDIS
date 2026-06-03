"""Slice82 Task 1 — characterization pinning the CURRENT Anthropic model-health truth.

Characterization updated to the post-Task-3 truth (the Task 1 RED snapshot documented the
pre-wiring behavior; Slice82 Task 3 deliberately changed items 2-4). No real Anthropic/network
call; no FULL run. Pins (per the Slice82 plan):
  1. The 4 LLM strict components are config-driven via a no-network health check: configured
     env -> LIVE_WIRED_AND_USED, missing env -> MISSING_CREDENTIALS.
  2. Anthropic inventory health is health-status driven: "healthy" when configured,
     "missing_config" when not.
  3. Both strict builders now expose a model_health_checker injectable (Slice82 Task 3).
  4. idis.services.llm_model_health exists (Task 2) and is wired into strict readiness +
     provisioning (Task 3).
  5. Provisioning Anthropic components expose configured / health_checked /
     runtime_call_proven=False / full_run_used=False (default path: no opt-in probe).
  6. STRICT_MODEL_ENV_VARS matches current code.
  7. Prompt registry linkage is class/fallback based and surfaces the OpenAI/Anthropic model-name
     mismatch (gpt-* in the registry vs claude-* runtime defaults) WITHOUT fixing it.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from idis.services.runs.strict_full_live import (
    STRICT_MODEL_ENV_VARS,
    StrictComponentStatus,
    build_strict_full_live_readiness_report,
    build_strict_provisioning_truth_report,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Fully-configured live-model env (any non-empty API key value; never a real secret).
_CONFIGURED_ENV: dict[str, str] = {
    "IDIS_EXTRACT_BACKEND": "anthropic",
    "IDIS_DEBATE_BACKEND": "anthropic",
    "ANTHROPIC_API_KEY": "configured-not-a-real-key",
    "IDIS_ANTHROPIC_MODEL_EXTRACT": "claude-sonnet-4-20250514",
    "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT": "claude-sonnet-4-20250514",
    "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER": "claude-opus-4-20250514",
}

_LLM_COMPONENTS = ("live_llm_model_clients", "agent_analysis", "debate_layer_1", "scoring")
_ANTHROPIC_INVENTORY = (
    "Anthropic extraction",
    "Anthropic debate",
    "Anthropic analysis",
    "Anthropic scoring",
)


def _readiness(env: dict[str, str]):
    return build_strict_full_live_readiness_report(
        env=env,
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
    )


def _inventory(report, name: str):
    return next(item for item in report.component_inventory if item.component_name == name)


# --- 1. LLM components are config-presence only ---


def test_llm_components_live_when_model_env_configured() -> None:
    report = _readiness(_CONFIGURED_ENV)
    for component_name in _LLM_COMPONENTS:
        component = report.component(component_name)
        assert component.status is StrictComponentStatus.LIVE_WIRED_AND_USED
        assert component.may_proceed is True


def test_llm_components_missing_credentials_when_env_absent() -> None:
    report = _readiness({})
    for component_name in _LLM_COMPONENTS:
        component = report.component(component_name)
        assert component.status is StrictComponentStatus.MISSING_CREDENTIALS
        assert component.may_proceed is False


# --- 2. Anthropic inventory health is health-status driven (Slice82 Task 3) ---


def test_anthropic_inventory_health_is_health_status_driven_when_configured() -> None:
    configured = _readiness(_CONFIGURED_ENV)
    for name in _ANTHROPIC_INVENTORY:
        item = _inventory(configured, name)
        assert item.config_present is True
        # Task 3 replaced the fixed "not_implemented" value with a no-network health status.
        assert item.health_check_status == "healthy"

    unconfigured = _readiness({})
    for name in _ANTHROPIC_INVENTORY:
        item = _inventory(unconfigured, name)
        assert item.config_present is False
        assert item.health_check_status == "missing_config"


# --- 3. model_health_checker injectable now exists on both strict builders (Slice82 Task 3) ---


def test_strict_builders_have_model_health_checker_param() -> None:
    readiness_params = inspect.signature(build_strict_full_live_readiness_report).parameters
    provisioning_params = inspect.signature(build_strict_provisioning_truth_report).parameters
    for params in (readiness_params, provisioning_params):
        # OCR/media health checkers exist (Slices 79/80) ...
        assert "ocr_health_checker" in params
        assert "media_health_checker" in params
        # ... and Task 3 added the model health checker injectable alongside them.
        assert "model_health_checker" in params


# --- 4. llm_model_health module exists (Task 2) and is wired into strict mode (Task 3) ---


def test_llm_model_health_module_exists_and_is_wired() -> None:
    # Task 2 added the standalone module; Task 3 wired it (see
    # test_strict_builders_have_model_health_checker_param for the injectable).
    assert importlib.util.find_spec("idis.services.llm_model_health") is not None
    from idis.services.llm_model_health import (
        LlmModelHealthCheck,
        LlmModelHealthStatus,
        check_llm_model_health,
    )

    assert callable(check_llm_model_health)
    assert hasattr(LlmModelHealthStatus, "DISABLED")
    assert {"status", "configured", "runtime_call_proven"} <= set(LlmModelHealthCheck.model_fields)


# --- 5. Provisioning Anthropic components expose the four-state fields ---


def test_provisioning_anthropic_components_expose_four_state_fields() -> None:
    report = build_strict_provisioning_truth_report(env=_CONFIGURED_ENV)
    components = {c["component_name"]: c for c in report["components"]}
    for name in _ANTHROPIC_INVENTORY:
        assert name in components
        component = components[name]
        assert "configured" in component
        assert "health_checked" in component
        assert component["runtime_call_proven"] is False
        assert component["full_run_used"] is False


# --- 6. STRICT_MODEL_ENV_VARS matches current code ---


def test_strict_model_env_vars_match_current_code() -> None:
    assert STRICT_MODEL_ENV_VARS == (
        "IDIS_EXTRACT_BACKEND",
        "IDIS_DEBATE_BACKEND",
        "ANTHROPIC_API_KEY",
        "IDIS_ANTHROPIC_MODEL_EXTRACT",
        "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT",
        "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER",
    )


# --- 7. Prompt registry surfaces the OpenAI/Anthropic mismatch (unfixed) ---


def test_prompt_registry_surfaces_openai_anthropic_mismatch() -> None:
    registry = (_REPO_ROOT / "prompts" / "registry.yaml").read_text(encoding="utf-8")
    env_example = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    # The prompt registry's model classes/fallbacks use OpenAI model names ...
    assert "gpt-4o" in registry
    assert "claude" not in registry
    # ... while the runtime model config defaults to Anthropic (claude-*). Mismatch is
    # currently surfaced (present in different sources), not reconciled.
    assert "claude-sonnet-4" in env_example
