"""Slice84 Task 4 — safe, additive provenance + debate observability in the FULL-step summaries.

TDD RED-first. Mirrors Slice83's extraction-provenance style. Each FULL step records an additive
``*_provenance`` block (provider/backend + safe model name(s) + prompt id/version + the strict
flag + a SANITIZED provider request id only when safely exposed); debate additionally records a
``debate_observability`` block (round/stop/agent counts, dissent_preserved, validated-challenge
count, a FIXED-SAFE arbiter rationale summary — never the raw rationale — and safe source
reference ids). Nothing surfaces the API key, prompt body, response text, raw provider payload,
exception message, or a filesystem path. Existing summary fields are unchanged (additive only;
``result_summary`` is an open dict). No real provider call; no engine coupling for live provenance
(builders are the unit of truth). Layer 2 IC challenge untouched.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from idis.api.routes.runs import (
    AnalysisClientSelection,
    DebateRoleRunnerSelection,
    ScoringClientSelection,
    _build_analysis_provenance,
    _build_debate_observability,
    _build_debate_provenance,
    _build_scoring_provenance,
    _run_full_analysis,
    _run_full_debate,
    _run_full_scoring,
)
from idis.models.debate import ArbiterDecision, DebateState, StopReason

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


class _FakeClientWithReqId:
    provider_request_id = "req-trace-7 sk-ant-LEAK /var/secret/x"


class _FakeClientNoReqId:
    pass


def _runner(client: Any) -> SimpleNamespace:
    return SimpleNamespace(llm_client=client)


# --- analysis provenance (builder unit of truth) ---


def test_analysis_provenance_deterministic_is_safe() -> None:
    sel = AnalysisClientSelection(backend="deterministic", model=None, max_tokens=8192)
    prov = _build_analysis_provenance(
        selection=sel, strict_live_debate_backend_required=False, client=_FakeClientNoReqId()
    )
    assert prov["provider"] == "deterministic"
    assert prov["backend"] == "deterministic"
    assert prov["model"] is None
    assert "prompt_id" in prov and "prompt_version" in prov
    assert prov["strict_live_debate_backend_required"] is False
    assert prov["provider_request_id"] is None  # no fake live metadata


def test_analysis_provenance_live_fake_records_safe_request_id() -> None:
    sel = AnalysisClientSelection(
        backend="anthropic", model="claude-sonnet-4-20250514", max_tokens=8192
    )
    prov = _build_analysis_provenance(
        selection=sel, strict_live_debate_backend_required=True, client=_FakeClientWithReqId()
    )
    assert prov["provider"] == "anthropic"
    assert prov["model"] == "claude-sonnet-4-20250514"  # safe model name from selection
    assert prov["strict_live_debate_backend_required"] is True
    rid = prov["provider_request_id"]
    assert isinstance(rid, str) and rid  # sanitized, present
    blob = repr(prov)
    for marker in _LEAK:
        assert marker not in blob


# --- scoring provenance (real on-disk prompt: scoring_agent/1.0.0) ---


def test_scoring_provenance_carries_prompt_id_version() -> None:
    sel = ScoringClientSelection(backend="deterministic", model=None, max_tokens=16384)
    prov = _build_scoring_provenance(
        selection=sel, strict_live_debate_backend_required=False, client=_FakeClientNoReqId()
    )
    assert prov["provider"] == "deterministic"
    assert prov["prompt_id"] == "scoring_agent"
    assert prov["prompt_version"] == "1.0.0"
    assert prov["provider_request_id"] is None


def test_scoring_provenance_live_fake_records_safe_request_id() -> None:
    sel = ScoringClientSelection(
        backend="anthropic", model="claude-sonnet-4-20250514", max_tokens=16384
    )
    prov = _build_scoring_provenance(
        selection=sel, strict_live_debate_backend_required=True, client=_FakeClientWithReqId()
    )
    assert prov["provider"] == "anthropic"
    assert prov["model"] == "claude-sonnet-4-20250514"
    rid = prov["provider_request_id"]
    assert isinstance(rid, str) and rid
    for marker in _LEAK:
        assert marker not in rid


# --- debate provenance (5 registry prompt ids + default/arbiter models + 2 request ids) ---


def test_debate_provenance_deterministic_is_safe() -> None:
    sel = DebateRoleRunnerSelection(
        backend="deterministic", default_model=None, arbiter_model=None, max_tokens=8192
    )
    runners = SimpleNamespace(
        advocate=_runner(_FakeClientNoReqId()), arbiter=_runner(_FakeClientNoReqId())
    )
    prov = _build_debate_provenance(
        selection=sel, strict_live_debate_backend_required=False, role_runners=runners
    )
    assert prov["provider"] == "deterministic"
    assert prov["default_model"] is None
    assert prov["arbiter_model"] is None
    assert len(prov["prompt_ids"]) == 5
    assert "DEBATE_ADVOCATE_V1" in prov["prompt_ids"]
    assert "DEBATE_ARBITER_V1" in prov["prompt_ids"]
    assert prov["prompt_version"] == "1.0.0"  # from prompts/registry.yaml
    assert prov["strict_live_debate_backend_required"] is False
    assert prov["default_provider_request_id"] is None
    assert prov["arbiter_provider_request_id"] is None


def test_debate_provenance_live_fakes_record_safe_request_ids() -> None:
    sel = DebateRoleRunnerSelection(
        backend="anthropic",
        default_model="claude-sonnet-4-20250514",
        arbiter_model="claude-opus-4-20250514",
        max_tokens=8192,
    )
    runners = SimpleNamespace(
        advocate=_runner(_FakeClientWithReqId()), arbiter=_runner(_FakeClientWithReqId())
    )
    prov = _build_debate_provenance(
        selection=sel, strict_live_debate_backend_required=True, role_runners=runners
    )
    assert prov["provider"] == "anthropic"
    assert prov["default_model"] == "claude-sonnet-4-20250514"
    assert prov["arbiter_model"] == "claude-opus-4-20250514"
    for key in ("default_provider_request_id", "arbiter_provider_request_id"):
        rid = prov[key]
        assert isinstance(rid, str) and rid
    blob = repr(prov)
    for marker in _LEAK:
        assert marker not in blob


# --- debate observability (safe rationale summary, counts, source refs) ---


def test_debate_observability_summarizes_safely() -> None:
    state = DebateState(
        tenant_id="t",
        deal_id="d",
        claim_registry_ref="claims://run-x",
        sanad_graph_ref="sanad://run-x",
        round_number=4,
        arbiter_decisions=[
            ArbiterDecision(
                decision_id="dec-1",
                round_number=3,
                challenges_validated=["c1", "c2"],
                dissent_preserved=False,
                rationale="SECRET-RATIONALE sk-LEAK123 private model reasoning",
            ),
            ArbiterDecision(
                decision_id="dec-2",
                round_number=4,
                challenges_validated=["c3"],
                dissent_preserved=True,
                rationale="more SECRET-RATIONALE text",
            ),
        ],
        agent_outputs=[],
        stop_reason=StopReason.MAX_ROUNDS,
    )
    obs = _build_debate_observability(state)
    assert obs["round_number"] == 4
    assert obs["stop_reason"] == "MAX_ROUNDS"
    assert obs["agent_output_count"] == 0
    assert obs["arbiter_decision_count"] == 2
    assert obs["dissent_preserved"] is True
    assert obs["challenges_validated_count"] == 3
    # FIXED safe summary — never the raw rationale text.
    assert obs["arbiter_rationale_summary"] == "arbiter_decision_recorded"
    assert obs["source_reference_ids"] == ["claims://run-x", "sanad://run-x"]
    blob = repr(obs)
    for marker in ("SECRET-RATIONALE", "sk-LEAK123", "private model reasoning"):
        assert marker not in blob


def test_debate_observability_no_decisions_is_safe() -> None:
    state = DebateState(
        tenant_id="t",
        deal_id="d",
        claim_registry_ref="claims://r",
        sanad_graph_ref="sanad://r",
        round_number=1,
        stop_reason=None,
    )
    obs = _build_debate_observability(state)
    assert obs["arbiter_decision_count"] == 0
    assert obs["dissent_preserved"] is False
    assert obs["challenges_validated_count"] == 0
    assert obs["arbiter_rationale_summary"] == "no_arbiter_decision"
    assert obs["stop_reason"] is None


# --- step integration: provenance/observability present + existing fields unchanged ---


def test_analysis_step_summary_includes_provenance() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        summary = _run_full_analysis(
            run_id="run-1",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            enrichment_refs={},
            db_conn=None,
        )
    assert "agent_count" in summary  # existing field unchanged
    assert "report_ids" in summary
    prov = summary["analysis_provenance"]
    assert prov["provider"] == "deterministic"
    assert prov["provider_request_id"] is None


def test_scoring_step_summary_includes_provenance() -> None:
    class _Band:
        value = "MEDIUM"

    class _Routing:
        value = "HOLD"

    class _Scorecard:
        composite_score = 68.5
        score_band = _Band()
        routing = _Routing()

    with (
        patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True),
        patch("idis.analysis.scoring.engine.ScoringEngine.score", return_value=_Scorecard()),
    ):
        summary = _run_full_scoring(
            run_id="run-1",
            tenant_id="t",
            deal_id="d",
            analysis_bundle=object(),
            analysis_context=object(),
        )
    assert summary["composite_score"] == 68.5  # existing fields unchanged
    assert summary["band"] == "MEDIUM"
    prov = summary["scoring_provenance"]
    assert prov["prompt_id"] == "scoring_agent"
    assert prov["provider"] == "deterministic"


def test_debate_step_summary_includes_provenance_and_observability() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        summary = _run_full_debate(
            run_id="run-1",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )
    for key in (
        "debate_id",
        "stop_reason",
        "round_number",
        "muhasabah_passed",
        "agent_output_count",
    ):
        assert key in summary  # existing 5 fields unchanged
    prov = summary["debate_provenance"]
    assert prov["provider"] == "deterministic"
    assert len(prov["prompt_ids"]) == 5
    obs = summary["debate_observability"]
    assert "dissent_preserved" in obs
    assert "challenges_validated_count" in obs
    assert obs["arbiter_rationale_summary"] in ("arbiter_decision_recorded", "no_arbiter_decision")
    assert obs["source_reference_ids"] == ["claims://run-1", "sanad://run-1"]


# --- failure paths stay safe: Task 3 strict codes remain intact + importable ---


def test_task3_strict_codes_intact() -> None:
    from idis.api.routes.runs import (
        STRICT_LIVE_ANALYSIS_REQUIRED,
        STRICT_LIVE_DEBATE_REQUIRED,
        STRICT_LIVE_SCORING_REQUIRED,
        StrictLiveRoleError,
    )

    assert STRICT_LIVE_ANALYSIS_REQUIRED == "STRICT_LIVE_ANALYSIS_REQUIRED"
    assert STRICT_LIVE_DEBATE_REQUIRED == "STRICT_LIVE_DEBATE_REQUIRED"
    assert STRICT_LIVE_SCORING_REQUIRED == "STRICT_LIVE_SCORING_REQUIRED"
    assert issubclass(StrictLiveRoleError, Exception)
