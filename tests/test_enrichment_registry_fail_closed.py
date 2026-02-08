"""Tests for enrichment provider registry fail-closed behavior.

Verifies:
- Unknown provider raises ProviderNotRegisteredError (not empty success)
- Duplicate registration raises DuplicateProviderError
- Registered providers are retrievable
- list_providers returns all registered connectors
"""

from __future__ import annotations

import pytest

from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    RightsClass,
)
from idis.services.enrichment.registry import (
    DuplicateProviderError,
    EnrichmentProviderRegistry,
    ProviderNotRegisteredError,
)


class _StubConnector:
    """Stub connector for testing registry operations."""

    def __init__(
        self,
        provider_id: str = "stub_provider",
        rights_class: RightsClass = RightsClass.GREEN,
    ) -> None:
        self._provider_id = provider_id
        self._rights_class = rights_class

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def rights_class(self) -> RightsClass:
        return self._rights_class

    @property
    def cache_policy(self) -> CachePolicyConfig:
        return CachePolicyConfig(ttl_seconds=3600)

    def fetch(self, request: EnrichmentRequest, ctx: EnrichmentContext) -> EnrichmentResult:
        return EnrichmentResult(status=EnrichmentStatus.HIT)


class TestRegistryFailClosed:
    """Registry must fail closed on unknown providers."""

    def test_unknown_provider_raises_error(self) -> None:
        """Looking up an unregistered provider must raise, not return empty."""
        registry = EnrichmentProviderRegistry()
        with pytest.raises(ProviderNotRegisteredError) as exc_info:
            registry.get("nonexistent_provider")
        assert "nonexistent_provider" in str(exc_info.value)

    def test_empty_registry_raises_on_any_lookup(self) -> None:
        """Empty registry must reject all lookups."""
        registry = EnrichmentProviderRegistry()
        with pytest.raises(ProviderNotRegisteredError):
            registry.get("sec_edgar")

    def test_duplicate_registration_raises_error(self) -> None:
        """Registering the same provider_id twice must raise."""
        registry = EnrichmentProviderRegistry()
        connector = _StubConnector(provider_id="dup_test")
        registry.register(connector)
        with pytest.raises(DuplicateProviderError) as exc_info:
            registry.register(_StubConnector(provider_id="dup_test"))
        assert "dup_test" in str(exc_info.value)


class TestRegistryHappyPath:
    """Registry normal operations."""

    def test_register_and_retrieve(self) -> None:
        """Registered connector is retrievable by provider_id."""
        registry = EnrichmentProviderRegistry()
        connector = _StubConnector(provider_id="test_conn")
        registry.register(connector, requires_byol=False)

        descriptor = registry.get("test_conn")
        assert descriptor.provider_id == "test_conn"
        assert descriptor.rights_class == RightsClass.GREEN
        assert descriptor.requires_byol is False
        assert descriptor.connector is connector

    def test_list_providers_returns_all(self) -> None:
        """list_providers returns descriptors for all registered connectors."""
        registry = EnrichmentProviderRegistry()
        registry.register(_StubConnector(provider_id="a"))
        registry.register(_StubConnector(provider_id="b"))

        providers = registry.list_providers()
        ids = {p.provider_id for p in providers}
        assert ids == {"a", "b"}

    def test_provider_ids_returns_frozenset(self) -> None:
        """provider_ids returns a frozenset of all registered IDs."""
        registry = EnrichmentProviderRegistry()
        registry.register(_StubConnector(provider_id="x"))
        assert registry.provider_ids == frozenset({"x"})

    def test_byol_flag_preserved(self) -> None:
        """requires_byol flag is preserved in the descriptor."""
        registry = EnrichmentProviderRegistry()
        registry.register(_StubConnector(provider_id="byol_conn"), requires_byol=True)
        descriptor = registry.get("byol_conn")
        assert descriptor.requires_byol is True
