"""Slice96 Task 4 — provider budget hard cap (DEC-C): the accounting seam + wrapper (unit).

RED-first, hermetic. Pins the minimal per-tenant/provider hard cap: the injectable
``ProviderBudgetStore`` seam (Postgres-backed when DB-configured; in-memory only as the fallback
these hermetic tests use) tracks live-provider-call counts per ``{tenant}:{provider}``;
``ProviderBudget.charge`` raises a safe ``PROVIDER_BUDGET_EXCEEDED``
denial BEFORE spend; ``BudgetedLLMClient`` runs the gate before delegating ``.call``. Disabled
unless a cap env is set (default = passthrough, no behavior change). No network, no real provider.
The real production wiring per live LLM seam is proven separately in
``test_slice96_provider_budget_wiring.py``. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import pytest

from idis.providers.budget import (
    PROVIDER_BUDGET_EXCEEDED,
    BudgetedLLMClient,
    InMemoryProviderBudgetStore,
    PostgresProviderBudgetStore,
    ProviderBudget,
    ProviderBudgetConfig,
    ProviderBudgetExceededError,
    ProviderBudgetStore,
    build_default_provider_budget_store,
    default_provider_budget,
    load_provider_budget_config,
    reset_default_provider_budget,
    wrap_with_provider_budget,
)


class _RecordingLLMClient:
    """In-process LLMClient stand-in: records prompts, returns a fixed sentinel (no network)."""

    def __init__(self, response: str = "OK") -> None:
        self.calls: list[str] = []
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        self.calls.append(prompt)
        return self._response


# --- the accounting seam ---


def test_store_protocol_and_in_memory_impl_exist() -> None:
    assert isinstance(InMemoryProviderBudgetStore(), ProviderBudgetStore)  # runtime-checkable


def test_in_memory_store_consume_is_atomic_under_a_hard_cap() -> None:
    store = InMemoryProviderBudgetStore()
    assert store.consume(key="t:anthropic", amount=1, cap=2) == (True, 1)
    assert store.consume(key="t:anthropic", amount=1, cap=2) == (True, 2)
    assert store.consume(key="t:anthropic", amount=1, cap=2) == (False, 2)  # denied, unchanged


def test_config_is_disabled_by_default_and_parses_a_positive_cap() -> None:
    assert load_provider_budget_config(env={}).max_calls is None
    assert load_provider_budget_config(env={"IDIS_PROVIDER_BUDGET_MAX_CALLS": "5"}).max_calls == 5
    for bad in ("0", "-3", "x", "", "  "):
        cfg = load_provider_budget_config(env={"IDIS_PROVIDER_BUDGET_MAX_CALLS": bad})
        assert cfg.max_calls is None  # 0 / negative / garbage -> disabled (fail-safe)


# --- enforcement ---


def test_budget_disabled_never_charges() -> None:
    budget = ProviderBudget(config=load_provider_budget_config(env={}))
    assert budget.enabled is False
    for _ in range(100):
        budget.charge(tenant_id="t", provider="anthropic")  # never raises when disabled


def test_budget_enforces_hard_cap_per_tenant_and_provider() -> None:
    budget = ProviderBudget(
        config=ProviderBudgetConfig(max_calls=2), store=InMemoryProviderBudgetStore()
    )
    budget.charge(tenant_id="t1", provider="anthropic")
    budget.charge(tenant_id="t1", provider="anthropic")
    with pytest.raises(ProviderBudgetExceededError) as excinfo:
        budget.charge(tenant_id="t1", provider="anthropic")  # 3rd exceeds cap=2
    err = excinfo.value
    assert err.code == PROVIDER_BUDGET_EXCEEDED
    assert (err.limit, err.used, err.provider, err.tenant_id) == (2, 2, "anthropic", "t1")
    # per-(tenant, provider) isolation: independent buckets still have headroom.
    budget.charge(tenant_id="t2", provider="anthropic")  # different tenant
    budget.charge(tenant_id="t1", provider="openai")  # different provider


def test_budgeted_client_denies_before_calling_inner_provider() -> None:
    inner = _RecordingLLMClient()
    budget = ProviderBudget(config=ProviderBudgetConfig(max_calls=1))
    client = BudgetedLLMClient(inner, tenant_id="t", provider="anthropic", budget=budget)
    assert client.call("first") == "OK"  # allowed -> delegates to inner
    assert inner.calls == ["first"]
    with pytest.raises(ProviderBudgetExceededError):
        client.call("second")  # exhausted -> gate raises BEFORE inner is invoked
    assert inner.calls == ["first"]  # provider NOT called on denial


def test_denial_is_safe_shape_no_secrets_or_prompt_or_cost() -> None:
    err = ProviderBudgetExceededError(provider="anthropic", tenant_id="t", limit=3, used=3)
    text = str(err)
    assert err.code == PROVIDER_BUDGET_EXCEEDED
    assert PROVIDER_BUDGET_EXCEEDED in text
    for forbidden in ("prompt", "api_key", "sk-", "$", "token", "cost", "bearer"):
        assert forbidden not in text.lower()  # static message; no leakage


def test_wrap_is_passthrough_when_disabled_and_wraps_when_enabled() -> None:
    inner = _RecordingLLMClient()
    disabled = ProviderBudget(config=ProviderBudgetConfig(max_calls=None))
    assert wrap_with_provider_budget(inner, tenant_id="t", budget=disabled) is inner  # no wrap
    enabled = ProviderBudget(config=ProviderBudgetConfig(max_calls=5))
    wrapped = wrap_with_provider_budget(inner, tenant_id="t", budget=enabled)
    assert isinstance(wrapped, BudgetedLLMClient)


def test_process_default_budget_is_disabled_without_the_cap_env() -> None:
    reset_default_provider_budget()  # rebuild from current env (cap unset in this process)
    try:
        assert default_provider_budget().enabled is False
    finally:
        reset_default_provider_budget()


def test_postgres_store_implements_the_protocol() -> None:
    # Constructible without a live DB (it connects lazily per consume) and satisfies the seam.
    assert isinstance(PostgresProviderBudgetStore(), ProviderBudgetStore)


def test_default_store_selection_prefers_postgres_when_db_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DEC-C durable default: a DB-configured deployment uses Postgres (cross-replica, restart-safe),
    # never per-process memory; only the no-DB dev/test fallback is in-memory.
    import idis.persistence.db as db_module

    monkeypatch.setattr(db_module, "is_postgres_configured", lambda: True)
    assert isinstance(build_default_provider_budget_store(), PostgresProviderBudgetStore)
    monkeypatch.setattr(db_module, "is_postgres_configured", lambda: False)
    assert isinstance(build_default_provider_budget_store(), InMemoryProviderBudgetStore)


def test_migration_0024_forces_rls_with_explicit_with_check() -> None:
    # Match the project RLS convention: FORCE (owner not exempt) + an explicit WITH CHECK using the
    # same NULLIF-guarded predicate as USING, so writes are policy-checked, not just reads.
    from pathlib import Path

    src = Path("src/idis/persistence/migrations/versions/0024_provider_budget_usage.py").read_text(
        encoding="utf-8"
    )
    assert "FORCE ROW LEVEL SECURITY" in src
    assert "WITH CHECK (" in src
    predicate = "NULLIF(current_setting('idis.tenant_id', true), '')::uuid"
    assert src.count(predicate) >= 2  # same predicate on both USING and WITH CHECK
