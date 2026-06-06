"""Slice84 acceptance — master-plan acceptance proof for strict live analysis/debate/scoring.

Acceptance criteria (docs/plans/2026-06-05-slice84-...md §26-27):
  - No deterministic LLM role/scoring path is used in strict mode.
  - Outputs include safe model/prompt provenance and source references.

This suite composes the behaviors delivered by Tasks 2-4 into an end-to-end acceptance proof,
mirroring Slice83's seam-level acceptance: each FULL role is exercised with the strict flag and
injected fakes — NO real Anthropic/network call, no real-data FULL run. It proves (A) strict
rejects every deterministic role/scoring path with role-specific safe codes; (B) strict accepts
injected live fakes without real construction; (C) missing/failed provider fails closed safely
with no leak; (D) non-strict/SNAPSHOT behavior is unchanged (no env inference); (E) the strict
flag is threaded through the shared build_run_context funnel (API + worker); (F) the step
summaries carry safe additive provenance; (G) the debate summary carries safe observability; and
(H) all additions are additive in the open result_summary (no schema change). Layer 2 untouched.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from idis.api.routes.runs import (
    STRICT_LIVE_ANALYSIS_PROVIDER_FAILED,
    STRICT_LIVE_ANALYSIS_REQUIRED,
    STRICT_LIVE_DEBATE_PROVIDER_FAILED,
    STRICT_LIVE_DEBATE_REQUIRED,
    STRICT_LIVE_SCORING_PROVIDER_FAILED,
    STRICT_LIVE_SCORING_REQUIRED,
    AnalysisClientSelection,
    DebateRoleRunnerSelection,
    ScoringClientSelection,
    StrictLiveRoleError,
    _build_analysis_llm_client,
    _build_analysis_provenance,
    _build_debate_provenance,
    _build_debate_role_runners,
    _build_scoring_llm_client,
    _build_scoring_provenance,
    _run_full_analysis,
    _run_full_debate,
    _run_full_scoring,
)
from idis.debate.orchestrator import RoleRunners
from idis.debate.roles.advocate import AdvocateRole
from idis.models.run_step import RunStep, StepName
from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient
from idis.services.extraction.extractors.llm_client import (
    DeterministicAnalysisLLMClient,
    DeterministicScoringLLMClient,
)

_LEAK = (
    "sk-ant-LEAK",
    "sk-LEAK123",
    "/var/secret",
    "C:\\secret",
    "PROMPT-BODY",
    "RESPONSE-BODY",
    "SECRET-RATIONALE",
)


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


class _FakeClient:
    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "{}"


class _FakeClientWithReqId:
    provider_request_id = "req-trace-7 sk-ant-LEAK /var/secret/x"


def _runner(client: Any) -> SimpleNamespace:
    return SimpleNamespace(llm_client=client)


# === A. No deterministic LLM role/scoring path is used in strict mode ===


def test_acceptance_strict_rejects_every_deterministic_role_path() -> None:
    # Non-strict the same builders return the deterministic variants; strict must reject them.
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        assert isinstance(_build_analysis_llm_client(), DeterministicAnalysisLLMClient)
        assert isinstance(_build_scoring_llm_client(), DeterministicScoringLLMClient)
        assert isinstance(_build_debate_role_runners().advocate, AdvocateRole)

        for builder, code in (
            (_build_analysis_llm_client, STRICT_LIVE_ANALYSIS_REQUIRED),
            (_build_debate_role_runners, STRICT_LIVE_DEBATE_REQUIRED),
            (_build_scoring_llm_client, STRICT_LIVE_SCORING_REQUIRED),
        ):
            with pytest.raises(StrictLiveRoleError) as exc_info:
                builder(strict_live_debate_backend_required=True)
            assert exc_info.value.code == code


def test_acceptance_strict_rejects_explicit_deterministic_backend() -> None:
    with patch.dict(os.environ, {"IDIS_DEBATE_BACKEND": "deterministic"}, clear=False):
        for builder, code in (
            (_build_analysis_llm_client, STRICT_LIVE_ANALYSIS_REQUIRED),
            (_build_debate_role_runners, STRICT_LIVE_DEBATE_REQUIRED),
            (_build_scoring_llm_client, STRICT_LIVE_SCORING_REQUIRED),
        ):
            with pytest.raises(StrictLiveRoleError) as exc_info:
                builder(strict_live_debate_backend_required=True)
            assert exc_info.value.code == code


# === B. Strict mode with injected live fakes is allowed (no real provider call) ===


def test_acceptance_strict_allows_injected_live_fakes() -> None:
    fake_client = _FakeClient()
    fake_runners = RoleRunners()
    # No ANTHROPIC_API_KEY: a real client would raise; injected fakes prove the strict live path
    # is taken without any real provider construction or network.
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_DEBATE_BACKEND"] = "anthropic"
    with patch.dict(os.environ, env, clear=True):
        analysis = _build_analysis_llm_client(
            analysis_client_factory=lambda _s: fake_client,
            strict_live_debate_backend_required=True,
        )
        scoring = _build_scoring_llm_client(
            scoring_client_factory=lambda _s: fake_client,
            strict_live_debate_backend_required=True,
        )
        debate = _build_debate_role_runners(
            debate_role_runners_factory=lambda _s: fake_runners,
            strict_live_debate_backend_required=True,
        )
    assert analysis is fake_client
    assert scoring is fake_client
    assert debate is fake_runners
    # deterministic paths not used, no real Anthropic client constructed
    assert not isinstance(analysis, (AnthropicLLMClient, DeterministicAnalysisLLMClient))
    assert not isinstance(scoring, (AnthropicLLMClient, DeterministicScoringLLMClient))


# === C. Missing/failed provider blocks safely (no leak) ===


def test_acceptance_strict_provider_failure_is_safe() -> None:
    confidential = "boom sk-ant-LEAK PROMPT-BODY RESPONSE-BODY /var/secret"

    def failing(_s: Any) -> Any:
        raise RuntimeError(confidential)

    env = {"IDIS_DEBATE_BACKEND": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-not-real"}
    cases = (
        (
            "analysis",
            _build_analysis_llm_client,
            "analysis_client_factory",
            STRICT_LIVE_ANALYSIS_PROVIDER_FAILED,
        ),
        (
            "scoring",
            _build_scoring_llm_client,
            "scoring_client_factory",
            STRICT_LIVE_SCORING_PROVIDER_FAILED,
        ),
        (
            "debate",
            _build_debate_role_runners,
            "debate_role_runners_factory",
            STRICT_LIVE_DEBATE_PROVIDER_FAILED,
        ),
    )
    with patch.dict(os.environ, env, clear=False):
        for _role, builder, factory_kw, code in cases:
            with pytest.raises(StrictLiveRoleError) as exc_info:
                builder(strict_live_debate_backend_required=True, **{factory_kw: failing})
            assert exc_info.value.code == code
            blob = f"{exc_info.value.code}|{exc_info.value.message}|{exc_info.value!r}"
            for marker in _LEAK:
                assert marker not in blob


def test_acceptance_strict_missing_key_is_safe() -> None:
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_DEBATE_BACKEND"] = "anthropic"
    with patch.dict(os.environ, env, clear=True):
        for builder, code in (
            (_build_analysis_llm_client, STRICT_LIVE_ANALYSIS_PROVIDER_FAILED),
            (_build_scoring_llm_client, STRICT_LIVE_SCORING_PROVIDER_FAILED),
            (_build_debate_role_runners, STRICT_LIVE_DEBATE_PROVIDER_FAILED),
        ):
            with pytest.raises(StrictLiveRoleError) as exc_info:
                builder(strict_live_debate_backend_required=True)
            assert exc_info.value.code == code
            assert "ANTHROPIC_API_KEY" not in str(exc_info.value)


# === D. Non-strict / SNAPSHOT behavior remains unchanged (no env inference) ===


def test_acceptance_non_strict_deterministic_unchanged() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        assert isinstance(_build_analysis_llm_client(), DeterministicAnalysisLLMClient)
        assert isinstance(_build_scoring_llm_client(), DeterministicScoringLLMClient)
        assert isinstance(_build_debate_role_runners().advocate, AdvocateRole)


def test_acceptance_no_global_env_strict_inference() -> None:
    # A strict env profile alone (no explicit flag) must not force live: SNAPSHOT/non-strict
    # stays deterministic; strictness is only ever the explicit flag.
    env = _env_without("IDIS_DEBATE_BACKEND")
    env["IDIS_REQUIRE_FULL_LIVE"] = "1"
    with patch.dict(os.environ, env, clear=True):
        assert isinstance(_build_analysis_llm_client(), DeterministicAnalysisLLMClient)
        assert isinstance(_build_scoring_llm_client(), DeterministicScoringLLMClient)
        assert isinstance(_build_debate_role_runners().advocate, AdvocateRole)


# === E. API/worker strict flag threading (shared build_run_context funnel) ===


def test_acceptance_build_run_context_threads_strict_flag() -> None:
    from idis.audit.sink import InMemoryAuditSink
    from idis.services.runs.steps import build_run_context

    with patch(
        "idis.storage.defaults.build_configured_product_export_object_store", return_value=None
    ):
        ctx = build_run_context(
            db_conn=None,
            tenant_id="t",
            run_id="r",
            deal_id="d",
            mode="FULL",
            documents=[],
            audit_sink=InMemoryAuditSink(),
            strict_live_debate_backend_required=True,
        )
    # Both the API start_run and the worker context factory funnel through build_run_context and
    # pass the same computed strict boolean; the three FULL partials must carry it.
    assert ctx.analysis_fn.keywords.get("strict_live_debate_backend_required") is True
    assert ctx.debate_fn.keywords.get("strict_live_debate_backend_required") is True
    assert ctx.scoring_fn.keywords.get("strict_live_debate_backend_required") is True


# === F. Outputs include safe model/prompt provenance ===


def test_acceptance_step_summaries_carry_safe_provenance() -> None:
    class _Band:
        value = "MEDIUM"

    class _Routing:
        value = "HOLD"

    class _Scorecard:
        composite_score = 68.5
        score_band = _Band()
        routing = _Routing()

    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        analysis = _run_full_analysis(
            run_id="run-1",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            enrichment_refs={},
            db_conn=None,
        )
        debate = _run_full_debate(
            run_id="run-1",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )
        with patch("idis.analysis.scoring.engine.ScoringEngine.score", return_value=_Scorecard()):
            scoring = _run_full_scoring(
                run_id="run-1",
                tenant_id="t",
                deal_id="d",
                analysis_bundle=object(),
                analysis_context=object(),
            )

    a_prov = analysis["analysis_provenance"]
    assert a_prov["provider"] == "deterministic"
    assert a_prov["backend"] == "deterministic"
    assert "model" in a_prov and "prompt_id" in a_prov and "prompt_version" in a_prov
    assert a_prov["strict_live_debate_backend_required"] is False
    assert a_prov["provider_request_id"] is None

    d_prov = debate["debate_provenance"]
    assert d_prov["provider"] == "deterministic"
    assert "default_model" in d_prov and "arbiter_model" in d_prov
    assert len(d_prov["prompt_ids"]) == 5
    assert d_prov["prompt_version"] == "1.0.0"

    s_prov = scoring["scoring_provenance"]
    assert s_prov["provider"] == "deterministic"
    assert s_prov["prompt_id"] == "scoring_agent"
    assert s_prov["prompt_version"] == "1.0.0"

    for prov in (a_prov, d_prov, s_prov):
        blob = repr(prov)
        for marker in _LEAK:
            assert marker not in blob


def test_acceptance_live_fake_provenance_records_safe_model_and_request_id() -> None:
    # Strict live + injected fakes record live provenance: model from safe selection/config,
    # request id sanitized only when the fake exposes one — no real call, no leak.
    a = _build_analysis_provenance(
        selection=AnalysisClientSelection(
            backend="anthropic", model="claude-sonnet-4-20250514", max_tokens=8192
        ),
        strict_live_debate_backend_required=True,
        client=_FakeClientWithReqId(),
    )
    s = _build_scoring_provenance(
        selection=ScoringClientSelection(
            backend="anthropic", model="claude-sonnet-4-20250514", max_tokens=16384
        ),
        strict_live_debate_backend_required=True,
        client=_FakeClientWithReqId(),
    )
    d = _build_debate_provenance(
        selection=DebateRoleRunnerSelection(
            backend="anthropic",
            default_model="claude-sonnet-4-20250514",
            arbiter_model="claude-opus-4-20250514",
            max_tokens=8192,
        ),
        strict_live_debate_backend_required=True,
        role_runners=SimpleNamespace(
            advocate=_runner(_FakeClientWithReqId()), arbiter=_runner(_FakeClientWithReqId())
        ),
    )
    assert a["provider"] == s["provider"] == d["provider"] == "anthropic"
    assert a["model"] == s["model"] == "claude-sonnet-4-20250514"
    assert d["default_model"] == "claude-sonnet-4-20250514"
    assert d["arbiter_model"] == "claude-opus-4-20250514"
    assert isinstance(a["provider_request_id"], str) and a["provider_request_id"]
    assert isinstance(s["provider_request_id"], str) and s["provider_request_id"]
    assert isinstance(d["default_provider_request_id"], str) and d["default_provider_request_id"]
    assert isinstance(d["arbiter_provider_request_id"], str) and d["arbiter_provider_request_id"]
    for prov in (a, s, d):
        blob = repr(prov)
        for marker in _LEAK:
            assert marker not in blob


# === G. Outputs include safe debate observability ===


def test_acceptance_debate_summary_carries_safe_observability() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        summary = _run_full_debate(
            run_id="run-9",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )
    obs = summary["debate_observability"]
    assert isinstance(obs["round_number"], int)
    assert "stop_reason" in obs
    assert isinstance(obs["agent_output_count"], int)
    assert isinstance(obs["dissent_preserved"], bool)
    assert isinstance(obs["challenges_validated_count"], int)
    assert obs["arbiter_rationale_summary"] in ("arbiter_decision_recorded", "no_arbiter_decision")
    assert obs["source_reference_ids"] == ["claims://run-9", "sanad://run-9"]
    blob = repr(obs)
    for marker in _LEAK:
        assert marker not in blob


# === H. No schema change: additive in open result_summary; existing fields remain ===


def test_acceptance_provenance_is_additive_in_open_result_summary() -> None:
    # The open result_summary accepts the additive provenance/observability with no schema change.
    step = RunStep(
        step_id="s-1",
        run_id="run-1",
        tenant_id="t",
        step_name=next(iter(StepName)),
        step_order=0,
        result_summary={
            "debate_id": "run-1",
            "round_number": 1,
            "debate_provenance": {"provider": "anthropic"},
            "debate_observability": {"dissent_preserved": False},
        },
    )
    dumped = step.model_dump(mode="json")["result_summary"]
    assert dumped["round_number"] == 1  # existing-style field preserved
    assert dumped["debate_provenance"]["provider"] == "anthropic"
    assert dumped["debate_observability"]["dissent_preserved"] is False


def test_acceptance_existing_summary_fields_remain_present() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        analysis = _run_full_analysis(
            run_id="run-1",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            enrichment_refs={},
            db_conn=None,
        )
        debate = _run_full_debate(
            run_id="run-1",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )
    assert {"agent_count", "report_ids", "bundle_id"} <= set(analysis)
    assert {
        "debate_id",
        "stop_reason",
        "round_number",
        "muhasabah_passed",
        "agent_output_count",
    } <= set(debate)
