"""Slice93 Task 5 — Layer-2 live-provider provenance + strict runtime-proof surface (DEC-F).

A safe Layer-2 provenance artifact (model/prompt ids + a runtime-executed signal) is
surfaced in the challenge result, and the strict ``debate_layer_2_ic_challenge`` readiness
component clears **only** when the debate model health is runtime-call-proven — a strictly
stronger bar than a labels-only config check, so a fake key never clears the gate.

Safe metadata only: provider/model/prompt ids, sanitized provider request ids, counts and
booleans — never prompt text, model output, or raw rationale. Injected fakes only — no real
Anthropic, no database.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from idis.persistence.repositories.layer2_challenge import (
    clear_in_memory_layer2_challenge_store,
)
from idis.services.llm_model_health import LlmModelHealthCheck, LlmModelRole
from tests.test_slice65_layer2_ic_challenge import _layer2_response

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "33333333-3333-3333-3333-333333333333"
RUN_ID = "22222222-2222-2222-2222-222222222222"
CLAIM_ID = "claim_mth_0123456789abcdef01234567"
CALC_ID = "calc-1"

_SECRET_SYSTEM_PROMPT = "SECRET_SYSTEM_PROMPT_BODY do not leak"

# A configured debate env (backend + api key + default/arbiter models) — passes the Layer-2
# env precondition so the health status decides whether the component clears.
_CONFIGURED_ENV: dict[str, str] = {
    "IDIS_DEBATE_BACKEND": "anthropic",
    "ANTHROPIC_API_KEY": "configured-not-a-real-key",
    "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT": "claude-sonnet-fake",
    "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER": "claude-opus-fake",
}


class _FakeClient:
    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, json_mode: bool = False) -> str:
        return self._response


def _proven(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
    return LlmModelHealthCheck.healthy(
        role,
        backend="anthropic",
        provider="anthropic",
        models={"model": "claude-x"},
        runtime_call_proven=True,
        provider_request_id="msg_safe_request_id",
    )


def _healthy(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
    return LlmModelHealthCheck.healthy(
        role, backend="anthropic", provider="anthropic", models={"model": "claude-x"}
    )


def _failed(_env: Mapping[str, str], role: LlmModelRole) -> LlmModelHealthCheck:
    return LlmModelHealthCheck.failed(role, error="boom sk-LEAK C:\\secret PROMPT RESPONSE")


def _report(checker: Any, env: dict[str, str]) -> Any:
    from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report

    return build_strict_full_live_readiness_report(
        env=env,
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
        model_health_checker=checker,
    )


# --- Provenance builder: safe fields + genuine executed signal ---


def test_build_layer2_provenance_records_safe_executed_signal() -> None:
    from idis.api.routes.runs import _build_layer2_provenance
    from idis.services.runs.layer2_ic_challenge import Layer2ICLLMRunner

    response = _layer2_response(supported_claim_ids=["claim-a"], supported_calc_ids=["calc-a"])
    challenger = Layer2ICLLMRunner(
        role="ic_challenger", llm_client=_FakeClient(response), system_prompt=_SECRET_SYSTEM_PROMPT
    )
    arbiter = Layer2ICLLMRunner(
        role="ic_arbiter", llm_client=_FakeClient(response), system_prompt=_SECRET_SYSTEM_PROMPT
    )
    # Simulate the two live calls happening.
    challenger.run({"claim_ids": ["claim-a"]})
    arbiter.run({"claim_ids": ["claim-a"]})

    provenance = _build_layer2_provenance(
        strict_full_live=True,
        backend="anthropic",
        challenger_model="claude-sonnet-fake",
        arbiter_model="claude-opus-fake",
        challenger_runner=challenger,
        arbiter_runner=arbiter,
    )

    assert provenance["provider"] == "anthropic"
    assert provenance["backend"] == "anthropic"
    assert provenance["challenger_model"] == "claude-sonnet-fake"
    assert provenance["arbiter_model"] == "claude-opus-fake"
    assert provenance["prompt_ids"] == ["layer2_ic_challenger", "layer2_ic_arbiter"]
    assert provenance["prompt_version"] == "1.0.0"
    assert provenance["strict_full_live"] is True
    # The runtime-executed signal proves both live runners ran.
    assert provenance["challenger_executed"] is True
    assert provenance["arbiter_executed"] is True
    assert provenance["live_calls_executed"] is True

    # Safe shape only: no prompt body, model output, or raw rationale.
    encoded = json.dumps(provenance)
    assert _SECRET_SYSTEM_PROMPT not in encoded
    assert "unresolved_risk" not in encoded  # no model output content
    assert "muhasabah" not in encoded


def test_build_layer2_provenance_not_executed_when_runners_missing() -> None:
    from idis.api.routes.runs import _build_layer2_provenance

    provenance = _build_layer2_provenance(
        strict_full_live=False,
        backend="deterministic",
        challenger_model=None,
        arbiter_model=None,
        challenger_runner=None,
        arbiter_runner=None,
    )
    assert provenance["provider"] == "deterministic"
    assert provenance["strict_full_live"] is False
    assert provenance["challenger_executed"] is False
    assert provenance["arbiter_executed"] is False
    assert provenance["live_calls_executed"] is False
    assert provenance["challenger_provider_request_id"] is None
    assert provenance["arbiter_provider_request_id"] is None


# --- Provenance surfaced in the challenge result (non-strict default path) ---


def test_layer2_provenance_surfaced_in_result_non_strict() -> None:
    from idis.api.routes.runs import _run_full_layer2_ic_challenge

    clear_in_memory_layer2_challenge_store()
    result = _run_full_layer2_ic_challenge(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        debate_summary={"debate_id": RUN_ID, "muhasabah_passed": True, "stop_reason": "consensus"},
        created_claim_ids=[CLAIM_ID],
        calc_ids=[CALC_ID],
    )
    provenance = result["layer2_provenance"]
    # Non-strict default path: no live runners were constructed.
    assert provenance["strict_full_live"] is False
    assert provenance["live_calls_executed"] is False
    assert provenance["challenger_executed"] is False
    assert provenance["arbiter_executed"] is False
    assert provenance["prompt_ids"] == ["layer2_ic_challenger", "layer2_ic_arbiter"]
    # Safe: the block carries only ids/counts/booleans.
    encoded = json.dumps(result)
    for forbidden in ("SECRET", "prompt_body", "raw_rationale", "transcript"):
        assert forbidden not in encoded


# --- Strict readiness: clears ONLY on runtime-call-proven, blocks otherwise ---


def test_strict_layer2_clears_only_when_runtime_call_proven() -> None:
    from idis.services.runs.strict_full_live import StrictComponentStatus

    proven = _report(_proven, _CONFIGURED_ENV).component("debate_layer_2_ic_challenge")
    assert proven.status is StrictComponentStatus.LIVE_WIRED_AND_USED
    assert proven.may_proceed is True

    # Configured + health-checked but NOT runtime-proven -> still blocked (labels never clear).
    healthy = _report(_healthy, _CONFIGURED_ENV).component("debate_layer_2_ic_challenge")
    assert healthy.status is StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED
    assert healthy.may_proceed is False

    failed = _report(_failed, _CONFIGURED_ENV).component("debate_layer_2_ic_challenge")
    assert failed.status is StrictComponentStatus.CONFIGURED_BUT_FAILED_HEALTH_CHECK
    assert failed.may_proceed is False
    # No leak of the failed-health error body.
    for marker in ("sk-LEAK", "C:\\secret", "PROMPT", "RESPONSE"):
        assert marker not in failed.model_dump_json()

    missing = _report(_proven, {}).component("debate_layer_2_ic_challenge")
    assert missing.status is StrictComponentStatus.MISSING_CREDENTIALS
    assert missing.may_proceed is False


def test_strict_layer2_blocked_when_only_challenger_model_is_runtime_proven() -> None:
    from idis.services.runs.strict_full_live import StrictComponentStatus

    def _only_challenger_proven(_env: Mapping[str, str], role: LlmModelRole) -> Any:
        # Only the default (challenger) debate model is runtime-proven; every other role
        # — including the arbiter model — is healthy but NOT runtime-call-proven.
        if role is LlmModelRole.DEBATE:
            return _proven(_env, role)
        return _healthy(_env, role)

    component = _report(_only_challenger_proven, _CONFIGURED_ENV).component(
        "debate_layer_2_ic_challenge"
    )
    # Only ONE of the two debate models/roles is proven -> Layer-2 must NOT clear.
    assert component.status is StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED
    assert component.may_proceed is False


def test_strict_layer2_blocked_when_only_arbiter_model_is_runtime_proven() -> None:
    from idis.services.runs.strict_full_live import StrictComponentStatus

    def _only_arbiter_proven(_env: Mapping[str, str], role: LlmModelRole) -> Any:
        # Only the arbiter debate model is runtime-proven; the challenger (default) is not.
        if role is LlmModelRole.DEBATE_ARBITER:
            return _proven(_env, role)
        return _healthy(_env, role)

    component = _report(_only_arbiter_proven, _CONFIGURED_ENV).component(
        "debate_layer_2_ic_challenge"
    )
    # The symmetric case must also stay blocked — both models must be proven.
    assert component.status is StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED
    assert component.may_proceed is False
