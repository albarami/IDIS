"""Slice84 Task 1 — characterization pinning the CURRENT analysis/debate/scoring truth.

RED-as-discovery: documents existing behavior so later tasks change it deliberately. No real
Anthropic/network call (constructing live clients does not call the API; only `.call`/
`messages.create` would), no real FULL run against live providers, no production change. Pins
(per the Slice84 plan, decisions D-A..D-J locked):
  1. Selection truth for the 3 builders (analysis/scoring clients + debate RoleRunners).
  2. Builders never infer strictness from env (Slice84 Task 3): with no explicit strict flag,
     a strict env profile + unset backend still returns deterministic. Explicit-flag enforcement
     lives in test_slice84_strict_live_roles_enforcement.py.
  3. Injectable factory seams now exist (Slice84 Task 2); defaults None (behavior unchanged).
  4. The 3 FULL-step summaries now carry the Slice84 additive provenance/observability blocks
     (Task 4); existing summary fields are unchanged. Full content/safety lives in
     test_slice84_live_role_provenance_observability.py.
  5. RunStep.result_summary is an open dict[str, Any] (D-I: additive-safe, no schema change).
  6. Layer 2 IC challenge already has a SEPARATE strict pattern (out of Slice84 scope).
  7. Slice83/Slice82 reuse symbols are importable.
"""

from __future__ import annotations

import inspect
import os
from unittest.mock import patch

import pytest

from idis.api.routes.runs import (
    _build_analysis_llm_client,
    _build_debate_role_runners,
    _build_scoring_llm_client,
    _run_full_analysis,
    _run_full_debate,
    _run_full_scoring,
)
from idis.debate.orchestrator import RoleRunners
from idis.debate.roles.advocate import AdvocateRole
from idis.debate.roles.llm_role_runner import LLMRoleRunner
from idis.models.run_step import RunStep, StepName
from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient
from idis.services.extraction.extractors.llm_client import (
    DeterministicAnalysisLLMClient,
    DeterministicScoringLLMClient,
)
from idis.services.runs.layer2_ic_challenge import Layer2ICChallengeBlockedError

# Same fake unit-test key shape used by the existing test_llm_backend_selection.py (no real key).
_FAKE_KEY = "sk-ant-test-fake-key-for-unit-test"


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


# --- 1. selection truth for the 3 builders ---


def test_analysis_builder_selection() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        assert isinstance(_build_analysis_llm_client(), DeterministicAnalysisLLMClient)
    with patch.dict(
        os.environ,
        {"IDIS_DEBATE_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY},
        clear=False,
    ):
        assert isinstance(_build_analysis_llm_client(), AnthropicLLMClient)
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_DEBATE_BACKEND"] = "anthropic"
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="ANTHROPIC_API_KEY"),
    ):
        _build_analysis_llm_client()


def test_scoring_builder_selection() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        assert isinstance(_build_scoring_llm_client(), DeterministicScoringLLMClient)
    with patch.dict(
        os.environ,
        {"IDIS_DEBATE_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY},
        clear=False,
    ):
        assert isinstance(_build_scoring_llm_client(), AnthropicLLMClient)
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_DEBATE_BACKEND"] = "anthropic"
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="ANTHROPIC_API_KEY"),
    ):
        _build_scoring_llm_client()


def test_debate_runners_selection() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        runners = _build_debate_role_runners()
        assert isinstance(runners, RoleRunners)
        assert isinstance(runners.advocate, AdvocateRole)
    with patch.dict(
        os.environ,
        {"IDIS_DEBATE_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY},
        clear=False,
    ):
        runners = _build_debate_role_runners()
        assert isinstance(runners.advocate, LLMRoleRunner)
        assert isinstance(runners.arbiter, LLMRoleRunner)
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_DEBATE_BACKEND"] = "anthropic"
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="ANTHROPIC_API_KEY"),
    ):
        _build_debate_role_runners()


# --- 2. builders never infer strictness from env (Task 3: enforcement needs the explicit flag) ---


def test_builders_never_infer_strictness_from_env() -> None:
    # Deliberate invariant (Slice84 Task 3): a strict env profile (IDIS_REQUIRE_FULL_LIVE=1) with
    # IDIS_DEBATE_BACKEND unset and NO explicit strict flag -> all 3 builders still return
    # deterministic variants and raise nothing. Fail-closed happens only when the explicit
    # strict_live_debate_backend_required flag is passed (see the enforcement suite), never by
    # env inference inside the builders.
    env = _env_without("IDIS_DEBATE_BACKEND")
    env["IDIS_REQUIRE_FULL_LIVE"] = "1"
    with patch.dict(os.environ, env, clear=True):
        assert isinstance(_build_analysis_llm_client(), DeterministicAnalysisLLMClient)
        assert isinstance(_build_scoring_llm_client(), DeterministicScoringLLMClient)
        assert isinstance(_build_debate_role_runners().advocate, AdvocateRole)


# --- 3. injectable factory seams now exist (Slice84 Task 2) ---


def test_builders_have_factory_params() -> None:
    analysis = inspect.signature(_build_analysis_llm_client).parameters
    scoring = inspect.signature(_build_scoring_llm_client).parameters
    debate = inspect.signature(_build_debate_role_runners).parameters
    # Task 2 added the injectable seams; defaults are None so behavior is unchanged without them.
    assert analysis["analysis_client_factory"].default is None
    assert scoring["scoring_client_factory"].default is None
    assert debate["debate_role_runners_factory"].default is None
    assert "context" in debate  # existing param preserved


# --- 4. FULL-step summaries now carry the Slice84 additive provenance/observability (Task 4) ---


def test_analysis_summary_carries_additive_provenance() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        summary = _run_full_analysis(
            run_id="run-1",
            tenant_id="tenant-1",
            deal_id="deal-1",
            created_claim_ids=[],
            calc_ids=[],
            enrichment_refs={},
            db_conn=None,
        )
    # Existing fields unchanged; provenance is additive (nested, not top-level scalars).
    assert "agent_count" in summary
    assert "report_ids" in summary
    assert summary["analysis_provenance"]["provider"] == "deterministic"


def test_debate_summary_carries_additive_provenance_and_observability() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        summary = _run_full_debate(
            run_id="run-1",
            tenant_id="tenant-1",
            deal_id="deal-1",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )
    # The original 5 keys remain present and unchanged; two additive blocks join them.
    for key in (
        "debate_id",
        "stop_reason",
        "round_number",
        "muhasabah_passed",
        "agent_output_count",
    ):
        assert key in summary
    assert summary["debate_provenance"]["provider"] == "deterministic"
    assert "dissent_preserved" in summary["debate_observability"]


def test_scoring_summary_carries_additive_provenance() -> None:
    # The deterministic scoring engine's muhasabah gate requires claim-backed dimensions, so a
    # zero-claim bundle cannot score. This pins the scoring summary SHAPE via a fake scorecard;
    # the _run_full_scoring return-dict construction is the real code under characterization.
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
            tenant_id="tenant-1",
            deal_id="deal-1",
            analysis_bundle=object(),
            analysis_context=object(),
        )
    # Existing fields unchanged; provenance is additive.
    assert summary["composite_score"] == 68.5
    assert summary["band"] == "MEDIUM"
    assert summary["routing"] == "HOLD"
    assert summary["scoring_provenance"]["prompt_id"] == "scoring_agent"


# --- 5. result_summary is an open, additive-safe dict (D-I) ---


def test_run_step_result_summary_is_open_additive_dict() -> None:
    step = RunStep(
        step_id="step-1",
        run_id="run-1",
        tenant_id="tenant-1",
        step_name=next(iter(StepName)),
        step_order=0,
        result_summary={"analysis_provenance": {"provider": "anthropic"}},
    )
    assert step.result_summary["analysis_provenance"]["provider"] == "anthropic"
    dumped = step.model_dump(mode="json")
    assert dumped["result_summary"]["analysis_provenance"]["provider"] == "anthropic"


# --- 6. Layer 2 IC challenge already has a separate strict pattern (out of scope) ---


def test_layer2_ic_challenge_has_separate_strict_pattern() -> None:
    from idis.api.routes.runs import _run_full_layer2_ic_challenge

    # Layer 2 blocks via its own RuntimeError-based code, distinct from the Slice84 codes.
    err = Layer2ICChallengeBlockedError("LAYER2_MISSING_LIVE_MODEL_CONFIG")
    assert str(err) == "LAYER2_MISSING_LIVE_MODEL_CONFIG"
    assert "STRICT_LIVE" not in str(err)
    assert callable(_run_full_layer2_ic_challenge)


# --- 7. Slice83/Slice82 reuse symbols are importable ---


def test_slice83_82_reuse_symbols_are_importable() -> None:
    from idis.api.routes.runs import (
        StrictLiveExtractionError,
        _build_extraction_provenance,
        _extraction_prompt_version,
        _safe_client_request_id,
    )
    from idis.services.llm_model_health import LlmModelRole, _sanitize_request_id
    from idis.services.prompts.registry import PromptRegistry

    assert LlmModelRole.ANALYSIS.value == "analysis"
    assert LlmModelRole.DEBATE.value == "debate"
    assert LlmModelRole.SCORING.value == "scoring"
    assert callable(_sanitize_request_id)
    assert callable(_safe_client_request_id)
    assert callable(_extraction_prompt_version)
    assert callable(_build_extraction_provenance)
    assert issubclass(StrictLiveExtractionError, Exception)
    assert hasattr(PromptRegistry, "get_version")
