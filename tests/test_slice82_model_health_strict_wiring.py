"""Slice82 Task 3 — wire llm_model_health into strict readiness + provisioning truth.

TDD RED-first. Mirrors test_slice79_ocr_strict_wiring / test_slice80_media_strict_wiring.
The 4 Anthropic components (extraction/debate/analysis/scoring) become health-status driven
via an injectable per-role ``model_health_checker`` (``(env, role) -> LlmModelHealthCheck``),
fail closed when not HEALTHY, never echo the API key / model path / prompt / response /
exception message, and surface the four states (configured / health_checked /
runtime_call_proven / full_run_used=False). No real provider call; no FULL run.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from idis.services.llm_model_health import LlmModelHealthCheck, LlmModelRole
from idis.services.media_health import MediaHealthCheck
from idis.services.ocr_health import OcrHealthCheck
from idis.services.runs.strict_full_live import (
    StrictComponentStatus,
    build_strict_full_live_readiness_report,
    build_strict_provisioning_truth_report,
)

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
    "ANTHROPIC_API_KEY": "configured-not-a-real-key",
    "IDIS_ANTHROPIC_MODEL_EXTRACT": "claude-sonnet-4-20250514",
    "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT": "claude-sonnet-4-20250514",
    "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER": "claude-opus-4-20250514",
}


def _healthy(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
    return LlmModelHealthCheck.healthy(
        role, backend="anthropic", provider="anthropic", models={"model": "claude-x"}
    )


def _proven(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
    return LlmModelHealthCheck.healthy(
        role,
        backend="anthropic",
        provider="anthropic",
        models={"model": "claude-x"},
        runtime_call_proven=True,
        provider_request_id="msg_safe_request_id",
    )


def _readiness(checker: Any, *, env: dict[str, str] | None = None) -> Any:
    return build_strict_full_live_readiness_report(
        env=_CONFIGURED_ENV if env is None else env,
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
        model_health_checker=checker,
    )


def _inventory(report: Any, name: str) -> Any:
    return next(item for item in report.component_inventory if item.component_name == name)


# ----- readiness signatures -----


def test_readiness_builder_accepts_model_health_checker() -> None:
    assert (
        "model_health_checker"
        in inspect.signature(build_strict_full_live_readiness_report).parameters
    )


def test_provisioning_builder_accepts_model_health_checker() -> None:
    assert (
        "model_health_checker"
        in inspect.signature(build_strict_provisioning_truth_report).parameters
    )


# ----- healthy -> live -----


def test_configured_healthy_components_are_live() -> None:
    report = _readiness(_healthy)
    for name in _LLM_COMPONENTS:
        component = report.component(name)
        assert component.status is StrictComponentStatus.LIVE_WIRED_AND_USED
        assert component.may_proceed is True


# ----- fail-closed: missing / disabled / failed -----


def test_missing_credentials_is_fail_closed_with_safe_ids() -> None:
    def _missing(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
        return LlmModelHealthCheck.missing(
            role, missing_dependencies=["anthropic_api_key", "extract_model"]
        )

    component = _readiness(_missing).component("live_llm_model_clients")
    assert component.status is StrictComponentStatus.MISSING_CREDENTIALS
    assert component.may_proceed is False
    blob = component.model_dump_json()
    for token in ("C:\\", "/var/", "sk-"):
        assert token not in blob


def test_disabled_backend_is_fail_closed() -> None:
    def _disabled(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
        return LlmModelHealthCheck.disabled(role)

    component = _readiness(_disabled, env={}).component("agent_analysis")
    assert component.status is StrictComponentStatus.MISSING_CREDENTIALS
    assert component.may_proceed is False


def test_failed_health_is_fail_closed_without_leaking_error() -> None:
    def _failed(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
        return LlmModelHealthCheck.failed(
            role, error="boom sk-LEAK123 C:\\secret\\key /var/secret PROMPT-BODY RESPONSE-BODY"
        )

    component = _readiness(_failed).component("scoring")
    assert component.status is StrictComponentStatus.CONFIGURED_BUT_FAILED_HEALTH_CHECK
    assert component.may_proceed is False
    blob = component.model_dump_json()
    for marker in (
        "sk-LEAK123",
        "C:\\secret",
        "/var/secret",
        "PROMPT-BODY",
        "RESPONSE-BODY",
        "[redacted]",
    ):
        assert marker not in blob


# ----- inventory health is health-status driven (no longer "not_implemented") -----


def test_anthropic_inventory_health_is_health_status_driven() -> None:
    healthy = _readiness(_healthy)
    for name in _ANTHROPIC_INVENTORY:
        item = _inventory(healthy, name)
        assert item.health_check_status == "healthy"
        assert item.health_check_status != "not_implemented"


# ----- default path is no-network (no injected checker, no crash) -----


def test_default_path_is_no_network_and_fail_closed() -> None:
    # No injected checker + no anthropic config -> default check (no provider call) -> disabled.
    report = build_strict_full_live_readiness_report(
        env={}, load_byol_env_credentials=False, binary_resolver=lambda _name: None
    )
    for name in _LLM_COMPONENTS:
        component = report.component(name)
        assert component.status is StrictComponentStatus.MISSING_CREDENTIALS
        assert component.may_proceed is False


# ----- existing OCR/media wiring unchanged -----


def test_ocr_and_media_wiring_unchanged_alongside_model_wiring() -> None:
    report = build_strict_full_live_readiness_report(
        env=_CONFIGURED_ENV,
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
        ocr_health_checker=lambda _env: OcrHealthCheck.healthy(),
        media_health_checker=lambda _env: MediaHealthCheck.healthy(),
        model_health_checker=_healthy,
    )
    # No OCR/media evidence in this corpus -> those components remain may_proceed True.
    assert report.component("ocr").may_proceed is True
    assert report.component("mp4_stt").may_proceed is True


# ----- provisioning four-state -----


def test_provisioning_surfaces_four_states_default_runtime_not_proven() -> None:
    report = build_strict_provisioning_truth_report(env=_CONFIGURED_ENV)
    components = {c["component_name"]: c for c in report["components"]}
    for name in _ANTHROPIC_INVENTORY:
        component = components[name]
        assert "configured" in component
        assert "health_checked" in component
        assert component["runtime_call_proven"] is False
        assert component["full_run_used"] is False


def test_provisioning_runtime_call_proven_when_opt_in_probe_proves_it() -> None:
    report = build_strict_provisioning_truth_report(
        env=_CONFIGURED_ENV,
        allow_local_strict_health_probes=True,
        model_health_checker=_proven,
    )
    components = {c["component_name"]: c for c in report["components"]}
    for name in _ANTHROPIC_INVENTORY:
        assert components[name]["runtime_call_proven"] is True
        assert components[name]["full_run_used"] is False
    # safe: no request id / model leaks beyond safe fields
    blob = __import__("json").dumps(report)
    assert "sk-" not in blob


def test_provisioning_no_leak_on_failed_model_health() -> None:
    def _failed(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
        return LlmModelHealthCheck.failed(role, error="C:\\secret sk-LEAK123 PROMPT-BODY")

    report = build_strict_provisioning_truth_report(
        env=_CONFIGURED_ENV,
        allow_local_strict_health_probes=True,
        model_health_checker=_failed,
    )
    blob = __import__("json").dumps(report)
    for marker in ("sk-LEAK123", "C:\\secret", "PROMPT-BODY"):
        assert marker not in blob


# ----- four-state truth: documented decision (no production change) -----


def test_provisioning_four_state_truth_is_explicit_and_split_by_design() -> None:
    """Document the EXACT provisioning four-state truth for the Anthropic components.

    Decision (Slice82 Task 3 clarification): NO production change. The provisioning
    ``health_checked`` / ``health_check_status`` fields keep their established Slice72
    meaning -- "an opt-in local PROBE was attempted" -- which Slice72's
    test_strict_provisioning_truth_marks_static_runtime_checks_not_run deliberately
    guards ("must not report health_checked as if probes ran"). The no-network model
    health check is NOT a probe; it is surfaced as the readiness-inventory
    ``health_check_status`` ("healthy"). The four acceptance states are therefore all
    distinguishable, split across the two report layers by design:
      * configured          -> provisioning ``configured`` / inventory ``config_present``
      * health-checked       -> readiness inventory ``health_check_status`` (no-network)
      * runtime-call-proven  -> provisioning ``runtime_call_proven`` (opt-in only)
      * not-yet-FULL-used    -> provisioning ``full_run_used`` (always False pre-FULL)
    Forcing provisioning ``health_checked=True`` here would re-introduce the exact
    overclaiming Slice72 forbids, so it is intentionally NOT done.
    """
    # health-checked signal: lives at the readiness-inventory layer (pure no-network default).
    readiness = build_strict_full_live_readiness_report(
        env=_CONFIGURED_ENV,
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
    )
    for name in _ANTHROPIC_INVENTORY:
        assert _inventory(readiness, name).health_check_status == "healthy"

    # provisioning default: configured + statically inspected, NO probe, NO runtime proof.
    default = build_strict_provisioning_truth_report(env=_CONFIGURED_ENV)
    default_components = {c["component_name"]: c for c in default["components"]}
    for name in _ANTHROPIC_INVENTORY:
        component = default_components[name]
        assert component["configured"] is True
        assert component["health_checked"] is False
        assert component["health_check_status"] == "configured_not_checked"
        assert component["runtime_call_proven"] is False
        assert component["full_run_used"] is False

    # provisioning opt-in proven: runtime_call_proven flips True, but health_checked STAYS
    # False (no Slice73 local probe ran) and full_run_used STAYS False (no FULL run).
    proven = build_strict_provisioning_truth_report(
        env=_CONFIGURED_ENV,
        allow_local_strict_health_probes=True,
        model_health_checker=_proven,
    )
    proven_components = {c["component_name"]: c for c in proven["components"]}
    for name in _ANTHROPIC_INVENTORY:
        component = proven_components[name]
        assert component["runtime_call_proven"] is True
        assert component["health_checked"] is False
        assert component["health_check_status"] == "configured_not_checked"
        assert component["full_run_used"] is False
