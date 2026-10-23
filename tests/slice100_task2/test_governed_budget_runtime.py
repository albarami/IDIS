"""Slice100 Task 2: production budget accounting is signed-policy bound."""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest

from idis.providers.budget import (
    PostgresProviderBudgetStore,
    ProviderBudget,
    ProviderBudgetConfig,
    build_default_provider_budget_store,
)
from idis.providers.budget_policy import (
    PROVIDER_BUDGET_CONFIG_BLOCKED,
    require_durable_budget_store,
    validate_provider_budget_policy_payload,
)
from tests.slice100_task2._support import ACCEPTANCE_TENANT_ID, budget_payload, error_code


def _governed_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": ACCEPTANCE_TENANT_ID,
        "policy_hash": "a" * 64,
        "component_id": "anthropic",
        "reservation_id": "40000000-0000-4000-8000-000000000010",
        "calls": 1,
        "usd": Decimal("0.01"),
        "input_tokens": 1,
        "output_tokens": 1,
        "embedding_tokens": 0,
        "global_max_calls": 50,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("calls", True),
        ("calls", 0),
        ("usd", Decimal("-0.01")),
        ("input_tokens", -1),
        ("global_max_calls", 0),
        ("policy_hash", "not-a-hash"),
        ("reservation_id", "sk-live-secret-shaped-value"),
        ("reservation_id", "40000000-0000-4000-8000-0000000000ab".upper()),
        ("tenant_id", "10000000-0000-4000-8000-0000000000ab".upper()),
    ],
)
def test_production_store_rejects_invalid_governed_amounts_before_connecting(
    field: str,
    value: object,
) -> None:
    connected = False

    def forbidden_connection() -> Any:
        nonlocal connected
        connected = True
        raise AssertionError("invalid accounting must fail before database access")

    store = PostgresProviderBudgetStore(
        connection_factory=forbidden_connection,
        tenant_setter=lambda *_args: None,
        global_max_calls=50,
    )

    with pytest.raises(Exception) as exc_info:
        store.reserve(**_governed_values(**{field: value}))

    assert error_code(exc_info.value) == PROVIDER_BUDGET_CONFIG_BLOCKED
    assert connected is False


def test_production_store_requires_a_reservation_identity_before_connecting() -> None:
    connected = False

    def forbidden_connection() -> Any:
        nonlocal connected
        connected = True
        raise AssertionError("missing reservation identity must fail before database access")

    values = _governed_values()
    values.pop("reservation_id")
    store = PostgresProviderBudgetStore(
        connection_factory=forbidden_connection,
        tenant_setter=lambda *_args: None,
        global_max_calls=50,
    )

    with pytest.raises(Exception) as exc_info:
        store.reserve(**values)

    assert error_code(exc_info.value) == PROVIDER_BUDGET_CONFIG_BLOCKED
    assert connected is False


def test_new_reservation_cap_mismatch_is_rejected_after_exact_ledger_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_checked = False
    store = PostgresProviderBudgetStore(global_max_calls=1)
    monkeypatch.setattr(store, "_lock_policy", lambda *_args: None)

    def absent_reservation(*_args: object) -> None:
        nonlocal ledger_checked
        ledger_checked = True
        return None

    monkeypatch.setattr(store, "_reservation_state", absent_reservation)
    monkeypatch.setattr(
        store,
        "_active_component_caps",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("cap mismatch must fail before active-policy lookup")
        ),
    )

    with pytest.raises(Exception) as exc_info:
        store.reserve_in_tx(object(), **_governed_values(global_max_calls=100))

    assert error_code(exc_info.value) == PROVIDER_BUDGET_CONFIG_BLOCKED
    assert ledger_checked is True


@pytest.mark.parametrize(
    ("operation", "ledger_state"),
    [("reserve", "RESERVED"), ("consume", "CONSUMED"), ("refund", "REFUNDED")],
)
def test_exact_governed_retries_use_the_ledger_before_active_policy_lookup(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    ledger_state: str,
) -> None:
    store = PostgresProviderBudgetStore(global_max_calls=50)
    monkeypatch.setattr(store, "_lock_policy", lambda *_args: None)
    monkeypatch.setattr(store, "_reservation_state", lambda *_args: ledger_state)

    def forbidden_active_policy_lookup(*_args: object) -> dict[str, object]:
        raise AssertionError("an exact ledger retry must survive signed-policy replacement")

    monkeypatch.setattr(store, "_active_component_caps", forbidden_active_policy_lookup)

    assert getattr(store, f"{operation}_in_tx")(object(), **_governed_values()) is True


@pytest.mark.parametrize(
    ("operation", "ledger_state"),
    [("reserve", "RESERVED"), ("consume", "CONSUMED"), ("refund", "REFUNDED")],
)
def test_exact_historical_cap_retries_are_ledger_authorized(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    ledger_state: str,
) -> None:
    store = PostgresProviderBudgetStore(global_max_calls=60)
    monkeypatch.setattr(store, "_lock_policy", lambda *_args: None)
    monkeypatch.setattr(store, "_reservation_state", lambda *_args: ledger_state)
    monkeypatch.setattr(
        store,
        "_active_component_caps",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("an exact historical retry must not require current-policy admission")
        ),
    )

    assert (
        getattr(store, f"{operation}_in_tx")(object(), **_governed_values(global_max_calls=50))
        is True
    )


@pytest.mark.parametrize(
    ("operation", "private_branch", "arguments"),
    [
        (
            "reserve",
            "_reserve_legacy",
            {"key": f"{ACCEPTANCE_TENANT_ID}:anthropic", "amount": 1, "cap": 100},
        ),
        (
            "consume",
            "_consume_legacy",
            {"key": f"{ACCEPTANCE_TENANT_ID}:anthropic", "amount": 1, "cap": 100},
        ),
        (
            "refund",
            "_refund_legacy",
            {"key": f"{ACCEPTANCE_TENANT_ID}:anthropic", "amount": 1},
        ),
    ],
)
def test_governed_store_cannot_reach_any_legacy_accounting_branch(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    private_branch: str,
    arguments: dict[str, object],
) -> None:
    reached: list[dict[str, object]] = []
    store = PostgresProviderBudgetStore(global_max_calls=1)

    def record_legacy_branch(**values: object) -> tuple[bool, int]:
        reached.append(values)
        return (True, 1)

    monkeypatch.setattr(store, private_branch, record_legacy_branch)

    with pytest.raises(Exception) as exc_info:
        getattr(store, operation)(**arguments)

    assert error_code(exc_info.value) == PROVIDER_BUDGET_CONFIG_BLOCKED
    assert reached == []


@pytest.mark.parametrize(
    ("private_branch", "arguments"),
    [
        (
            "_reserve_legacy",
            {"key": f"{ACCEPTANCE_TENANT_ID}:anthropic", "amount": 1, "cap": 100},
        ),
        (
            "_consume_legacy",
            {"key": f"{ACCEPTANCE_TENANT_ID}:anthropic", "amount": 1, "cap": 100},
        ),
        (
            "_refund_legacy",
            {"key": f"{ACCEPTANCE_TENANT_ID}:anthropic", "amount": 1},
        ),
    ],
)
def test_governed_store_rejects_even_direct_private_legacy_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    private_branch: str,
    arguments: dict[str, object],
) -> None:
    connected = False

    def forbidden_connection() -> Any:
        nonlocal connected
        connected = True
        raise AssertionError("governed stores must reject before legacy database access")

    monkeypatch.setattr("idis.persistence.db.begin_app_conn", forbidden_connection)
    store = PostgresProviderBudgetStore(global_max_calls=1)

    with pytest.raises(Exception) as exc_info:
        getattr(store, private_branch)(**arguments)

    assert error_code(exc_info.value) == PROVIDER_BUDGET_CONFIG_BLOCKED
    assert connected is False


def test_unbound_compatibility_store_can_still_use_the_non_strict_legacy_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PostgresProviderBudgetStore()
    reached: list[dict[str, object]] = []

    def record_legacy_consume(**values: object) -> tuple[bool, int]:
        reached.append(values)
        return (True, 1)

    monkeypatch.setattr(store, "_consume_legacy", record_legacy_consume)

    assert store.consume(key=f"{ACCEPTANCE_TENANT_ID}:anthropic", amount=1, cap=100) == (True, 1)
    assert reached == [{"key": f"{ACCEPTANCE_TENANT_ID}:anthropic", "amount": 1, "cap": 100}]


def test_default_postgres_store_binds_the_planned_environment_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    monkeypatch.setenv("IDIS_PROVIDER_BUDGET_MAX_CALLS", "50")
    monkeypatch.setattr("idis.persistence.db.is_postgres_configured", lambda: True)

    store = build_default_provider_budget_store()

    assert isinstance(store, PostgresProviderBudgetStore)
    assert store.trusted_global_max_calls == 50
    assert store.governed_only is True


def test_non_strict_default_postgres_store_preserves_explicit_legacy_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    monkeypatch.setenv("IDIS_PROVIDER_BUDGET_MAX_CALLS", "50")
    monkeypatch.setattr("idis.persistence.db.is_postgres_configured", lambda: True)

    store = build_default_provider_budget_store()

    assert isinstance(store, PostgresProviderBudgetStore)
    assert store.trusted_global_max_calls is None
    assert store.governed_only is False


def test_strict_legacy_call_only_charge_is_blocked_instead_of_bypassing_signed_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _LegacyRecorder:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def consume(self, **values: object) -> tuple[bool, int]:
            self.calls.append(values)
            return (True, 1)

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    store = _LegacyRecorder()
    budget = ProviderBudget(config=ProviderBudgetConfig(max_calls=50), store=store)

    with pytest.raises(Exception) as exc_info:
        budget.charge(tenant_id=ACCEPTANCE_TENANT_ID, provider="anthropic")

    assert error_code(exc_info.value) == PROVIDER_BUDGET_CONFIG_BLOCKED
    assert store.calls == []


def test_validated_policy_component_mapping_is_deeply_immutable() -> None:
    policy = validate_provider_budget_policy_payload(MappingProxyType(budget_payload()))

    with pytest.raises(TypeError):
        policy.components["anthropic"] = policy.components["anthropic"]  # type: ignore[index]


def test_durable_contract_requires_explicit_signed_policy_accounting_version() -> None:
    store = PostgresProviderBudgetStore(global_max_calls=50)

    assert require_durable_budget_store(store) is store
    assert store.signed_policy_accounting_version == "provider-budget-policy-v1"
    assert store.governed_only is True


class _LegacyCapableDurableClaim:
    is_durable = True
    signed_policy_accounting_version = "provider-budget-policy-v1"
    trusted_global_max_calls = 50
    governed_only = False

    def reserve(self, **_values: object) -> bool:
        return True

    def consume(self, **_values: object) -> bool:
        return True

    def refund(self, **_values: object) -> bool:
        return True


def test_durable_contract_rejects_a_legacy_capable_store_claim() -> None:
    with pytest.raises(Exception) as exc_info:
        require_durable_budget_store(_LegacyCapableDurableClaim())

    assert error_code(exc_info.value) == PROVIDER_BUDGET_CONFIG_BLOCKED


def test_durable_contract_rejects_an_unbound_postgres_budget_store() -> None:
    store = PostgresProviderBudgetStore()

    with pytest.raises(Exception) as exc_info:
        require_durable_budget_store(store)

    assert error_code(exc_info.value) == PROVIDER_BUDGET_CONFIG_BLOCKED
