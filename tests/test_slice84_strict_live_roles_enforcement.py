"""Slice84 Task 3 — execution-time strict enforcement for analysis / debate L1 / scoring.

TDD RED-first. A single shared flag ``strict_live_debate_backend_required`` (threaded from the
strict FULL execution path) forbids the deterministic analysis client / debate RoleRunners /
scoring client: a non-anthropic backend fails closed with the role-specific
``STRICT_LIVE_{ANALYSIS,DEBATE,SCORING}_REQUIRED``; a provider construction/factory failure
fails closed with ``..._PROVIDER_FAILED``. All codes carry only safe, fixed strings — never the
API key, prompt, response, provider payload, raw exception message, or path. Non-strict and
SNAPSHOT paths are unchanged; strictness is never inferred from env inside the builders.
Layer 2 IC challenge is untouched.
"""

from __future__ import annotations

import inspect
import os
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
    StrictLiveRoleError,
    _build_analysis_llm_client,
    _build_debate_role_runners,
    _build_scoring_llm_client,
    _run_full_analysis,
    _run_full_debate,
    _run_full_scoring,
)
from idis.debate.orchestrator import RoleRunners
from idis.debate.roles.advocate import AdvocateRole
from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient
from idis.services.extraction.extractors.llm_client import (
    DeterministicAnalysisLLMClient,
    DeterministicScoringLLMClient,
)

_LEAK_MARKERS = (
    "sk-LEAK123",
    "sk-ant-",
    "C:\\secret",
    "/var/secret",
    "PROMPT-BODY",
    "RESPONSE-BODY",
    "boom",
)


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


class _FakeClient:
    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "{}"


def _surfaced(err: StrictLiveRoleError) -> str:
    return f"{err.code}|{err.message}|{err!s}|{err!r}"


# --- strict + non-anthropic backend fails closed (no deterministic) ---


def test_strict_analysis_unset_backend_blocked() -> None:
    with (
        patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True),
        pytest.raises(StrictLiveRoleError) as exc_info,
    ):
        _build_analysis_llm_client(strict_live_debate_backend_required=True)
    assert exc_info.value.code == STRICT_LIVE_ANALYSIS_REQUIRED


def test_strict_debate_unset_backend_blocked() -> None:
    with (
        patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True),
        pytest.raises(StrictLiveRoleError) as exc_info,
    ):
        _build_debate_role_runners(strict_live_debate_backend_required=True)
    assert exc_info.value.code == STRICT_LIVE_DEBATE_REQUIRED


def test_strict_scoring_explicit_deterministic_blocked() -> None:
    with (
        patch.dict(os.environ, {"IDIS_DEBATE_BACKEND": "deterministic"}, clear=False),
        pytest.raises(StrictLiveRoleError) as exc_info,
    ):
        _build_scoring_llm_client(strict_live_debate_backend_required=True)
    assert exc_info.value.code == STRICT_LIVE_SCORING_REQUIRED


def test_strict_required_block_messages_are_safe() -> None:
    env = _env_without("IDIS_DEBATE_BACKEND")
    env["ANTHROPIC_API_KEY"] = "sk-LEAK123-secret"
    with patch.dict(os.environ, env, clear=True):
        for builder in (
            _build_analysis_llm_client,
            _build_scoring_llm_client,
            _build_debate_role_runners,
        ):
            with pytest.raises(StrictLiveRoleError) as exc_info:
                builder(strict_live_debate_backend_required=True)
            blob = _surfaced(exc_info.value)
            for marker in (*_LEAK_MARKERS, "sk-LEAK123-secret"):
                assert marker not in blob


# --- strict + anthropic + injected fakes is allowed (no real call) ---


def test_strict_anthropic_injected_fakes_allowed_for_all_three() -> None:
    fake_client = _FakeClient()
    fake_runners = RoleRunners()
    # No ANTHROPIC_API_KEY: the real clients would raise; the injected fakes prove the strict
    # live path is taken without any real provider construction.
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
    assert not isinstance(analysis, (AnthropicLLMClient, DeterministicAnalysisLLMClient))
    assert not isinstance(scoring, (AnthropicLLMClient, DeterministicScoringLLMClient))


# --- strict + provider construction/factory failure fails safely ---


def test_strict_provider_failure_is_safe_for_all_three() -> None:
    confidential = "boom sk-LEAK123 PROMPT-BODY RESPONSE-BODY"

    def failing(_s: Any) -> Any:
        raise RuntimeError(confidential)

    env = {"IDIS_DEBATE_BACKEND": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-fake-not-real"}
    cases = (
        (
            lambda: _build_analysis_llm_client(
                analysis_client_factory=failing, strict_live_debate_backend_required=True
            ),
            STRICT_LIVE_ANALYSIS_PROVIDER_FAILED,
        ),
        (
            lambda: _build_scoring_llm_client(
                scoring_client_factory=failing, strict_live_debate_backend_required=True
            ),
            STRICT_LIVE_SCORING_PROVIDER_FAILED,
        ),
        (
            lambda: _build_debate_role_runners(
                debate_role_runners_factory=failing, strict_live_debate_backend_required=True
            ),
            STRICT_LIVE_DEBATE_PROVIDER_FAILED,
        ),
    )
    with patch.dict(os.environ, env, clear=False):
        for call, expected_code in cases:
            with pytest.raises(StrictLiveRoleError) as exc_info:
                call()
            assert exc_info.value.code == expected_code
            blob = _surfaced(exc_info.value)
            for marker in _LEAK_MARKERS:
                assert marker not in blob


def test_strict_anthropic_missing_key_no_factory_is_provider_failed() -> None:
    # The real Anthropic clients raise ValueError (mentions ANTHROPIC_API_KEY); the wrapper must
    # surface only the safe provider-failed code and never that raw message.
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
            assert "ANTHROPIC_API_KEY" not in _surfaced(exc_info.value)


# --- non-strict / SNAPSHOT unchanged; no env inference ---


def test_non_strict_unset_backend_still_deterministic() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        assert isinstance(
            _build_analysis_llm_client(strict_live_debate_backend_required=False),
            DeterministicAnalysisLLMClient,
        )
        assert isinstance(
            _build_scoring_llm_client(strict_live_debate_backend_required=False),
            DeterministicScoringLLMClient,
        )
        assert isinstance(
            _build_debate_role_runners(strict_live_debate_backend_required=False).advocate,
            AdvocateRole,
        )


def test_no_global_env_strict_inference_in_builders() -> None:
    # IDIS_REQUIRE_FULL_LIVE=1 with no explicit flag -> deterministic (SNAPSHOT/non-strict path).
    env = _env_without("IDIS_DEBATE_BACKEND")
    env["IDIS_REQUIRE_FULL_LIVE"] = "1"
    with patch.dict(os.environ, env, clear=True):
        assert isinstance(_build_analysis_llm_client(), DeterministicAnalysisLLMClient)
        assert isinstance(_build_scoring_llm_client(), DeterministicScoringLLMClient)
        assert isinstance(_build_debate_role_runners().advocate, AdvocateRole)


# --- threading: signatures, FULL-step forwarding, build_run_context ---


def test_seam_signatures_accept_strict_flag() -> None:
    for fn in (
        _build_analysis_llm_client,
        _build_scoring_llm_client,
        _build_debate_role_runners,
        _run_full_analysis,
        _run_full_debate,
        _run_full_scoring,
    ):
        params = inspect.signature(fn).parameters
        assert "strict_live_debate_backend_required" in params
        assert params["strict_live_debate_backend_required"].default is False


def test_build_run_context_threads_strict_flag() -> None:
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
    assert ctx.analysis_fn.keywords.get("strict_live_debate_backend_required") is True
    assert ctx.debate_fn.keywords.get("strict_live_debate_backend_required") is True
    assert ctx.scoring_fn.keywords.get("strict_live_debate_backend_required") is True


class _StopForTest(Exception):
    pass


def test_full_steps_forward_strict_flag_to_builders() -> None:
    # Each FULL step must forward its strict flag to the builder (not merely accept it).
    captured: dict[str, Any] = {}

    def fake_analysis(*, strict_live_debate_backend_required: bool = False, **_kw: Any) -> Any:
        captured["analysis"] = strict_live_debate_backend_required
        raise _StopForTest

    def fake_debate(
        context: Any = None, *, strict_live_debate_backend_required: bool = False, **_kw: Any
    ) -> Any:
        captured["debate"] = strict_live_debate_backend_required
        raise _StopForTest

    def fake_scoring(*, strict_live_debate_backend_required: bool = False, **_kw: Any) -> Any:
        captured["scoring"] = strict_live_debate_backend_required
        raise _StopForTest

    with (
        patch("idis.api.routes.runs._build_analysis_llm_client", fake_analysis),
        pytest.raises(_StopForTest),
    ):
        _run_full_analysis(
            run_id="r",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            enrichment_refs={},
            db_conn=None,
            strict_live_debate_backend_required=True,
        )
    with (
        patch("idis.api.routes.runs._build_debate_role_runners", fake_debate),
        pytest.raises(_StopForTest),
    ):
        _run_full_debate(
            run_id="r",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
            strict_live_debate_backend_required=True,
        )
    with (
        patch("idis.api.routes.runs._build_scoring_llm_client", fake_scoring),
        pytest.raises(_StopForTest),
    ):
        _run_full_scoring(
            run_id="r",
            tenant_id="t",
            deal_id="d",
            analysis_bundle=object(),
            analysis_context=object(),
            strict_live_debate_backend_required=True,
        )
    assert captured == {"analysis": True, "debate": True, "scoring": True}
