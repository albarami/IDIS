"""Typed strict provider-budget policy admission for Slice100."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from uuid import UUID

PROVIDER_BUDGET_CONFIG_BLOCKED = "PROVIDER_BUDGET_CONFIG_BLOCKED"
PROVIDER_BUDGET_PROVIDER_UNCONFIGURED = "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED"

EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS = frozenset(
    {
        "anthropic",
        "openai_embeddings",
        "sec_edgar",
        "companies_house",
        "github",
        "fred",
        "finnhub",
        "fmp",
        "world_bank",
        "escwa_catalog",
        "escwa_ispar",
        "qatar_open_data",
        "hackernews",
        "gdelt",
        "patentsview",
        "wayback",
        "google_news_rss",
    }
)

_POLICY_FIELDS = frozenset(
    {"tenant_id", "policy_version", "currency", "total_max_usd", "components"}
)
_BASE_COMPONENT_FIELDS = frozenset({"max_calls", "max_usd"})
_ANTHROPIC_FIELDS = _BASE_COMPONENT_FIELDS | {"max_input_tokens", "max_output_tokens"}
_EMBEDDING_FIELDS = _BASE_COMPONENT_FIELDS | {"max_embedding_tokens"}
_MONEY_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")


class ProviderBudgetPolicyError(ValueError):
    """Safe typed strict-budget policy failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProviderBudgetComponentPolicy:
    """Validated immutable caps for one provider component."""

    component_id: str
    max_calls: int
    max_usd: Decimal
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_embedding_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderBudgetPolicy:
    """Validated immutable signed-policy payload."""

    tenant_id: str
    policy_version: str
    currency: str
    total_max_usd: Decimal
    components: Mapping[str, ProviderBudgetComponentPolicy]


@runtime_checkable
class DurableProviderBudgetStore(Protocol):
    """Minimum durable reserve/consume/refund surface required in strict mode."""

    is_durable: bool
    signed_policy_accounting_version: str
    trusted_global_max_calls: int | None
    governed_only: bool

    def reserve(self, **values: object) -> object:
        """Atomically reserve bounded calls, currency, and tokens."""
        ...

    def consume(self, **values: object) -> object:
        """Atomically convert a reservation to consumed usage."""
        ...

    def refund(self, **values: object) -> object:
        """Atomically release an unused reservation."""
        ...


def _config_error() -> ProviderBudgetPolicyError:
    return ProviderBudgetPolicyError(PROVIDER_BUDGET_CONFIG_BLOCKED)


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


def _money(value: object) -> Decimal:
    if not isinstance(value, str) or _MONEY_RE.fullmatch(value) is None:
        raise _config_error()
    result = Decimal(value)
    if result < 0:
        raise _config_error()
    return result


def _positive_int(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise _config_error()
    return value


def _component_fields(component_id: str) -> frozenset[str]:
    if component_id == "anthropic":
        return _ANTHROPIC_FIELDS
    if component_id == "openai_embeddings":
        return _EMBEDDING_FIELDS
    return _BASE_COMPONENT_FIELDS


def _parse_component(component_id: str, raw: object) -> ProviderBudgetComponentPolicy:
    if not isinstance(raw, Mapping) or frozenset(raw) != _component_fields(component_id):
        raise _config_error()
    values: dict[str, int | None] = {
        "max_input_tokens": None,
        "max_output_tokens": None,
        "max_embedding_tokens": None,
    }
    for field in values:
        if field in raw:
            values[field] = _positive_int(raw[field])
    return ProviderBudgetComponentPolicy(
        component_id=component_id,
        max_calls=_positive_int(raw.get("max_calls")),
        max_usd=_money(raw.get("max_usd")),
        max_input_tokens=values["max_input_tokens"],
        max_output_tokens=values["max_output_tokens"],
        max_embedding_tokens=values["max_embedding_tokens"],
    )


def validate_provider_budget_policy_payload(
    payload: Mapping[str, object],
) -> ProviderBudgetPolicy:
    """Validate the exact 17-component USD budget-policy payload."""

    if not isinstance(payload, Mapping) or frozenset(payload) != _POLICY_FIELDS:
        raise _config_error()
    tenant_id = payload.get("tenant_id")
    policy_version = payload.get("policy_version")
    if not _canonical_uuid(tenant_id) or not isinstance(policy_version, str) or not policy_version:
        raise _config_error()
    if payload.get("currency") != "USD":
        raise _config_error()
    raw_components = payload.get("components")
    if (
        not isinstance(raw_components, Mapping)
        or frozenset(raw_components) != EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS
    ):
        raise _config_error()
    parsed_components = {
        component_id: _parse_component(component_id, raw_components[component_id])
        for component_id in sorted(EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS)
    }
    assert isinstance(tenant_id, str)
    assert isinstance(policy_version, str)
    return ProviderBudgetPolicy(
        tenant_id=tenant_id,
        policy_version=policy_version,
        currency="USD",
        total_max_usd=_money(payload.get("total_max_usd")),
        components=MappingProxyType(parsed_components),
    )


def require_policy_component(
    *, policy: ProviderBudgetPolicy, component_id: str
) -> ProviderBudgetComponentPolicy:
    """Resolve one configured component or fail closed with the planned code."""

    component = policy.components.get(component_id)
    if component is None:
        raise ProviderBudgetPolicyError(PROVIDER_BUDGET_PROVIDER_UNCONFIGURED)
    return component


def validate_global_provider_budget_max_calls(raw: str | None) -> int:
    """Parse the required positive strict global call cap."""

    if raw is None:
        raise _config_error()
    normalized = raw.strip()
    if not normalized or not normalized.isascii() or not normalized.isdecimal():
        raise _config_error()
    value = int(normalized)
    if value <= 0:
        raise _config_error()
    return value


def require_durable_budget_store(store: object) -> DurableProviderBudgetStore:
    """Reject every in-memory or incomplete budget store in strict mode."""

    if (
        not isinstance(store, DurableProviderBudgetStore)
        or store.is_durable is not True
        or store.signed_policy_accounting_version != "provider-budget-policy-v1"
        or type(store.trusted_global_max_calls) is not int
        or store.trusted_global_max_calls <= 0
        or store.governed_only is not True
    ):
        raise _config_error()
    return store


__all__ = [
    "DurableProviderBudgetStore",
    "EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS",
    "PROVIDER_BUDGET_CONFIG_BLOCKED",
    "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED",
    "ProviderBudgetComponentPolicy",
    "ProviderBudgetPolicy",
    "ProviderBudgetPolicyError",
    "require_durable_budget_store",
    "require_policy_component",
    "validate_global_provider_budget_max_calls",
    "validate_provider_budget_policy_payload",
]
