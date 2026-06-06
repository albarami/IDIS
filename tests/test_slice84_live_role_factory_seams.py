"""Slice84 Task 2 — injectable factory seams for analysis / scoring / debate role runners.

TDD RED-first. Mirrors Slice83's `ExtractorClientFactory` seam. Each of the three builders
gains an optional `*_factory` (default None → behavior unchanged); when supplied the factory
receives a safe selection (backend/model(s)/max_tokens — never the API key) and returns the
client / RoleRunners. The three FULL steps accept + forward the factory; build_run_context
carries them into the FULL partials. No strict enforcement and no provenance here (Tasks 3/4),
no real provider call, no network.
"""

from __future__ import annotations

import dataclasses
import inspect
import os
from typing import Any
from unittest.mock import patch

import pytest

from idis.api.routes.runs import (
    AnalysisClientSelection,
    DebateRoleRunnerSelection,
    ScoringClientSelection,
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
from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient
from idis.services.extraction.extractors.llm_client import (
    DeterministicAnalysisLLMClient,
    DeterministicScoringLLMClient,
)

_FAKE_KEY = "sk-ant-test-fake-key-for-unit-test"


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


class _FakeClient:
    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "{}"


class _StopForTest(Exception):
    pass


# --- signatures accept the factory params (default None) ---


def test_builders_accept_factory_params() -> None:
    analysis = inspect.signature(_build_analysis_llm_client).parameters
    scoring = inspect.signature(_build_scoring_llm_client).parameters
    debate = inspect.signature(_build_debate_role_runners).parameters
    assert analysis["analysis_client_factory"].default is None
    assert scoring["scoring_client_factory"].default is None
    assert debate["debate_role_runners_factory"].default is None
    assert "context" in debate  # existing param preserved


# --- default behavior unchanged (no factory supplied) ---


def test_analysis_default_unchanged() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        assert isinstance(_build_analysis_llm_client(), DeterministicAnalysisLLMClient)
    with patch.dict(
        os.environ,
        {"IDIS_DEBATE_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY},
        clear=False,
    ):
        assert isinstance(_build_analysis_llm_client(), AnthropicLLMClient)


def test_scoring_default_unchanged() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        assert isinstance(_build_scoring_llm_client(), DeterministicScoringLLMClient)
    with patch.dict(
        os.environ,
        {"IDIS_DEBATE_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY},
        clear=False,
    ):
        assert isinstance(_build_scoring_llm_client(), AnthropicLLMClient)


def test_debate_default_unchanged() -> None:
    with patch.dict(os.environ, _env_without("IDIS_DEBATE_BACKEND"), clear=True):
        assert isinstance(_build_debate_role_runners().advocate, AdvocateRole)
    with patch.dict(
        os.environ,
        {"IDIS_DEBATE_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY},
        clear=False,
    ):
        assert isinstance(_build_debate_role_runners().advocate, LLMRoleRunner)


def test_default_anthropic_missing_key_still_fails_closed() -> None:
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_DEBATE_BACKEND"] = "anthropic"
    with patch.dict(os.environ, env, clear=True):
        for builder in (
            _build_analysis_llm_client,
            _build_scoring_llm_client,
            _build_debate_role_runners,
        ):
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                builder()


# --- injected factories are used (no real Anthropic construction, no network) ---


def test_analysis_factory_used_without_real_construction() -> None:
    fake = _FakeClient()
    env = _env_without("ANTHROPIC_API_KEY")  # real client would raise; factory bypasses it
    env["IDIS_DEBATE_BACKEND"] = "anthropic"
    with patch.dict(os.environ, env, clear=True):
        client = _build_analysis_llm_client(analysis_client_factory=lambda _s: fake)
    assert client is fake
    assert not isinstance(client, AnthropicLLMClient)


def test_scoring_factory_used_without_real_construction() -> None:
    fake = _FakeClient()
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_DEBATE_BACKEND"] = "anthropic"
    with patch.dict(os.environ, env, clear=True):
        client = _build_scoring_llm_client(scoring_client_factory=lambda _s: fake)
    assert client is fake
    assert not isinstance(client, AnthropicLLMClient)


def test_debate_factory_used_without_real_construction() -> None:
    fake_runners = RoleRunners()
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_DEBATE_BACKEND"] = "anthropic"
    with patch.dict(os.environ, env, clear=True):
        runners = _build_debate_role_runners(debate_role_runners_factory=lambda _s: fake_runners)
    assert runners is fake_runners


# --- factory selection contexts are safe (no API key) ---


def test_selection_contexts_carry_no_api_key() -> None:
    captured: dict[str, Any] = {}

    env = {"IDIS_DEBATE_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY}
    with patch.dict(os.environ, env, clear=False):
        _build_analysis_llm_client(analysis_client_factory=lambda s: captured.setdefault("a", s))
        _build_scoring_llm_client(scoring_client_factory=lambda s: captured.setdefault("s", s))
        _build_debate_role_runners(
            debate_role_runners_factory=lambda s: captured.setdefault("d", s)
        )

    assert {f.name for f in dataclasses.fields(captured["a"])} == {"backend", "model", "max_tokens"}
    assert {f.name for f in dataclasses.fields(captured["s"])} == {"backend", "model", "max_tokens"}
    assert {f.name for f in dataclasses.fields(captured["d"])} == {
        "backend",
        "default_model",
        "arbiter_model",
        "max_tokens",
    }
    assert isinstance(captured["a"], AnalysisClientSelection)
    assert isinstance(captured["s"], ScoringClientSelection)
    assert isinstance(captured["d"], DebateRoleRunnerSelection)
    assert captured["a"].model == "claude-sonnet-4-20250514"
    assert captured["d"].arbiter_model == "claude-opus-4-20250514"
    for sel in (captured["a"], captured["s"], captured["d"]):
        assert _FAKE_KEY not in repr(sel)


# --- FULL steps accept + forward the factory ---


def test_run_full_analysis_forwards_factory() -> None:
    def sentinel(_s: Any) -> _FakeClient:
        return _FakeClient()

    captured: dict[str, Any] = {}

    def fake_build(*, analysis_client_factory: Any = None, **_kw: Any) -> Any:
        captured["f"] = analysis_client_factory
        raise _StopForTest

    with (
        patch("idis.api.routes.runs._build_analysis_llm_client", fake_build),
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
            analysis_client_factory=sentinel,
        )
    assert captured["f"] is sentinel


def test_run_full_debate_forwards_factory() -> None:
    def sentinel(_s: Any) -> RoleRunners:
        return RoleRunners()

    captured: dict[str, Any] = {}

    def fake_build(
        context: Any = None, *, debate_role_runners_factory: Any = None, **_kw: Any
    ) -> Any:
        captured["f"] = debate_role_runners_factory
        raise _StopForTest

    with (
        patch("idis.api.routes.runs._build_debate_role_runners", fake_build),
        pytest.raises(_StopForTest),
    ):
        _run_full_debate(
            run_id="r",
            tenant_id="t",
            deal_id="d",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
            debate_role_runners_factory=sentinel,
        )
    assert captured["f"] is sentinel


def test_run_full_scoring_forwards_factory() -> None:
    def sentinel(_s: Any) -> _FakeClient:
        return _FakeClient()

    captured: dict[str, Any] = {}

    def fake_build(*, scoring_client_factory: Any = None, **_kw: Any) -> Any:
        captured["f"] = scoring_client_factory
        raise _StopForTest

    with (
        patch("idis.api.routes.runs._build_scoring_llm_client", fake_build),
        pytest.raises(_StopForTest),
    ):
        _run_full_scoring(
            run_id="r",
            tenant_id="t",
            deal_id="d",
            analysis_bundle=object(),
            analysis_context=object(),
            scoring_client_factory=sentinel,
        )
    assert captured["f"] is sentinel


# --- build_run_context carries factories into the FULL partials ---


def test_build_run_context_carries_factories() -> None:
    from idis.audit.sink import InMemoryAuditSink
    from idis.services.runs.steps import build_run_context

    def a(_s: Any) -> None:
        return None

    def s(_s: Any) -> None:
        return None

    def d(_s: Any) -> None:
        return None

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
            analysis_client_factory=a,
            scoring_client_factory=s,
            debate_role_runners_factory=d,
        )
    assert ctx.analysis_fn.keywords.get("analysis_client_factory") is a
    assert ctx.scoring_fn.keywords.get("scoring_client_factory") is s
    assert ctx.debate_fn.keywords.get("debate_role_runners_factory") is d
