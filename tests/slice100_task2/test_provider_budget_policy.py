"""RED contracts for the signed, exact, durable Slice100 provider-budget policy."""

from __future__ import annotations

import copy
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

import pytest

from idis.providers.budget import InMemoryProviderBudgetStore, PostgresProviderBudgetStore
from tests.slice100_task2._support import (
    EXPECTED_BUDGET_COMPONENT_IDS,
    budget_payload,
    error_code,
    require_attr,
    require_module,
)


def _api(name: str) -> Any:
    module = require_module("idis.providers.budget_policy")
    return require_attr(module, name)


def _validate(payload: dict[str, object]) -> object:
    return _api("validate_provider_budget_policy_payload")(MappingProxyType(payload))


def _assert_error_code(call: Callable[[], object], expected_code: str) -> BaseException:
    with pytest.raises(Exception) as exc_info:
        call()
    assert error_code(exc_info.value) == expected_code
    return exc_info.value


def _components(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    value = payload["components"]
    assert isinstance(value, dict)
    return value


def test_exact_budget_component_set_is_pinned_with_sec_edgar() -> None:
    declared = _api("EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS")
    assert frozenset(declared) == EXPECTED_BUDGET_COMPONENT_IDS
    assert len(declared) == len(EXPECTED_BUDGET_COMPONENT_IDS)
    assert "sec_edgar" in declared
    assert "edgar" not in declared


def test_complete_usd_decimal_policy_is_valid_and_input_is_not_mutated() -> None:
    payload = budget_payload()
    before = copy.deepcopy(payload)

    policy = _validate(payload)

    assert policy is not None
    assert payload == before


@pytest.mark.parametrize("missing_component", sorted(EXPECTED_BUDGET_COMPONENT_IDS))
def test_every_missing_component_fails_closed(missing_component: str) -> None:
    payload = budget_payload()
    del _components(payload)[missing_component]
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


def test_unplanned_component_fails_closed() -> None:
    payload = budget_payload()
    _components(payload)["unreviewed_provider"] = {"max_calls": 1, "max_usd": "0.00"}
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


@pytest.mark.parametrize("currency", ["", "usd", "EUR", "QAR"])
def test_currency_is_exactly_usd(currency: str) -> None:
    payload = budget_payload()
    payload["currency"] = currency
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


@pytest.mark.parametrize(
    "bad_money",
    [1, 1.0, 1.25, "", "-1.00", "1", "1.0", "01.00", "1.000", "NaN", "Infinity"],
)
def test_total_max_usd_is_a_nonnegative_two_decimal_string(bad_money: object) -> None:
    payload = budget_payload()
    payload["total_max_usd"] = bad_money
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


@pytest.mark.parametrize(
    "bad_money",
    [0, 1.0, 1.25, "", "-0.01", "1", "1.0", "01.00", "1.000", "NaN", "Infinity"],
)
def test_component_max_usd_is_a_nonnegative_two_decimal_string(bad_money: object) -> None:
    payload = budget_payload()
    _components(payload)["sec_edgar"]["max_usd"] = bad_money
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


@pytest.mark.parametrize("bad_calls", [None, True, 0, -1, 1.0, "1"])
def test_every_component_requires_a_positive_integer_call_cap(bad_calls: object) -> None:
    payload = budget_payload()
    _components(payload)["sec_edgar"]["max_calls"] = bad_calls
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


@pytest.mark.parametrize("field", ["max_input_tokens", "max_output_tokens"])
def test_anthropic_requires_both_relevant_token_caps(field: str) -> None:
    payload = budget_payload()
    del _components(payload)["anthropic"][field]
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


def test_openai_embeddings_requires_its_embedding_token_cap() -> None:
    payload = budget_payload()
    del _components(payload)["openai_embeddings"]["max_embedding_tokens"]
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


@pytest.mark.parametrize("bad_cap", [None, True, 0, -1, 1.5, "100"])
def test_relevant_token_caps_are_positive_integers(bad_cap: object) -> None:
    payload = budget_payload()
    _components(payload)["anthropic"]["max_input_tokens"] = bad_cap
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


def test_non_llm_provider_rejects_unreviewed_token_caps() -> None:
    payload = budget_payload()
    _components(payload)["sec_edgar"]["max_input_tokens"] = 10
    _assert_error_code(
        lambda: _validate(payload),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


def test_unknown_runtime_component_uses_provider_unconfigured_code() -> None:
    policy = _validate(budget_payload())
    require_component = _api("require_policy_component")
    _assert_error_code(
        lambda: require_component(policy=policy, component_id="unreviewed_provider"),
        "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED",
    )


@pytest.mark.parametrize("raw", [None, "", "0", "-1", "1.5", "not-an-integer"])
def test_global_provider_call_cap_is_required_and_strict(raw: str | None) -> None:
    validate_cap = _api("validate_global_provider_budget_max_calls")
    _assert_error_code(
        lambda: validate_cap(raw),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


def test_global_provider_call_cap_accepts_a_positive_integer() -> None:
    validate_cap = _api("validate_global_provider_budget_max_calls")
    assert validate_cap("50") == 50


def test_strict_budget_rejects_existing_in_memory_store() -> None:
    require_durable = _api("require_durable_budget_store")
    _assert_error_code(
        lambda: require_durable(InMemoryProviderBudgetStore()),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


def test_strict_budget_accepts_postgres_store_without_connecting() -> None:
    require_durable = _api("require_durable_budget_store")
    store = PostgresProviderBudgetStore(global_max_calls=50)
    assert require_durable(store) is store


class _IncompleteDurableClaim:
    is_durable = True


def test_durable_boolean_alone_cannot_clear_reserve_consume_refund_contract() -> None:
    require_durable = _api("require_durable_budget_store")
    _assert_error_code(
        lambda: require_durable(_IncompleteDurableClaim()),
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
    )


def test_postgres_store_exposes_atomic_reserve_consume_refund_surface() -> None:
    store = PostgresProviderBudgetStore()
    assert store.is_durable is True
    assert callable(store.reserve)
    assert callable(store.consume)
    assert callable(store.refund)
