"""Slice96 Task 4 — provider budget hard cap (DEC-C): REAL-path wiring proof per live LLM seam.

Not a fake seam. Each test drives the REAL production construction path in
``idis.api.routes.runs`` (the actual builders a live run uses) with the process-default budget
exhausted, and proves the gate raises ``PROVIDER_BUDGET_EXCEEDED`` BEFORE the wrapped Anthropic
client is invoked. No real paid provider call is made: ``AnthropicLLMClient.call`` is tripwired to
fail if ever reached, so a passing test proves the denial happens strictly before any provider
request. Covers every selected live LLM seam: extraction, analysis, scoring, debate (Layer-1
roles), and Layer-2 IC challenge, plus a completeness invariant that no bare live construction
bypasses the budget helper. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from idis.providers.budget import (
    ProviderBudgetExceededError,
    default_provider_budget,
    reset_default_provider_budget,
)
from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

_TENANT = "11111111-2222-3333-4444-555555555555"


class _ProviderInvoked(Exception):
    """Raised by the tripwire if a live provider call is reached despite an exhausted budget."""


@pytest.fixture
def exhausted_budget(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    # Real cap of 1, tripwire the live provider call, then exhaust (tenant, anthropic) on the SAME
    # process-default budget the production wiring uses. ANTHROPIC_API_KEY is a dummy: the client
    # is constructed (no network) but its .call is never permitted to run.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key-never-called")
    monkeypatch.setenv("IDIS_PROVIDER_BUDGET_MAX_CALLS", "1")
    reset_default_provider_budget()

    def _tripwire(self: object, prompt: str, *, json_mode: bool = False) -> str:
        raise _ProviderInvoked("live provider call reached despite an exhausted budget")

    monkeypatch.setattr(AnthropicLLMClient, "call", _tripwire)
    default_provider_budget().charge(tenant_id=_TENANT, provider="anthropic")  # consume the 1 unit
    yield _TENANT
    reset_default_provider_budget()


def test_extraction_seam_is_capped_on_the_real_path(
    exhausted_budget: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IDIS_EXTRACT_BACKEND", "anthropic")
    from idis.api.routes.runs import _build_extraction_llm_client

    client = _build_extraction_llm_client(tenant_id=exhausted_budget)
    with pytest.raises(ProviderBudgetExceededError):
        client.call("extract this")  # denied before the tripwired provider call


def test_analysis_seam_is_capped_on_the_real_path(
    exhausted_budget: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IDIS_DEBATE_BACKEND", "anthropic")
    from idis.api.routes.runs import _build_analysis_llm_client

    client = _build_analysis_llm_client(tenant_id=exhausted_budget)
    with pytest.raises(ProviderBudgetExceededError):
        client.call("analyze this")


def test_scoring_seam_is_capped_on_the_real_path(
    exhausted_budget: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IDIS_DEBATE_BACKEND", "anthropic")
    from idis.api.routes.runs import _build_scoring_llm_client

    client = _build_scoring_llm_client(tenant_id=exhausted_budget)
    with pytest.raises(ProviderBudgetExceededError):
        client.call("score this")


def test_debate_layer1_role_seam_is_capped_on_the_real_path(
    exhausted_budget: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IDIS_DEBATE_BACKEND", "anthropic")
    from idis.api.routes.runs import _build_debate_role_runners

    runners = _build_debate_role_runners(context=None, tenant_id=exhausted_budget)
    with pytest.raises(ProviderBudgetExceededError):
        runners.advocate._llm_client.call("debate this")  # the role's wrapped client denies


def test_layer2_ic_challenge_seam_is_capped_on_the_real_path(exhausted_budget: str) -> None:
    from idis.api.routes.runs import _new_budgeted_llm_client
    from idis.services.runs.layer2_ic_challenge import build_live_layer2_ic_runners

    challenger = _new_budgeted_llm_client(
        model="claude-sonnet-4-20250514", max_tokens=8192, tenant_id=exhausted_budget
    )
    arbiter = _new_budgeted_llm_client(
        model="claude-opus-4-20250514", max_tokens=8192, tenant_id=exhausted_budget
    )
    challenger_runner, _ = build_live_layer2_ic_runners(
        challenger_client=challenger, arbiter_client=arbiter
    )
    with pytest.raises(ProviderBudgetExceededError):
        challenger_runner._llm_client.call("challenge this")  # runner's wrapped client denies


def test_no_live_anthropic_construction_bypasses_the_budget_helper() -> None:
    # Completeness invariant: every live AnthropicLLMClient is built via _new_budgeted_llm_client
    # (which budget-wraps it), so no live LLM seam can bypass the cap.
    import inspect

    from idis.api.routes import runs as runs_module

    module_src = inspect.getsource(runs_module)
    assert module_src.count("AnthropicLLMClient(") == 1  # exactly one construction site
    helper_src = inspect.getsource(runs_module._new_budgeted_llm_client)
    assert "AnthropicLLMClient(" in helper_src  # ...and it lives inside the budget helper
