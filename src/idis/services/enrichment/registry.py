"""Enrichment provider registry.

Maintains a catalog of registered connectors with their rights class,
cache policy, and credential requirements. Fail-closed on unknown providers.

Spec: IDIS_Enrichment_Connector_Framework_v0_1.md §8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from idis.services.enrichment.models import (
        CachePolicyConfig,
        EnrichmentConnector,
        RightsClass,
    )

logger = logging.getLogger(__name__)


class ProviderNotRegisteredError(Exception):
    """Raised when a requested provider is not in the registry."""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__(f"Provider not registered: {provider_id}")


class DuplicateProviderError(Exception):
    """Raised when attempting to register a provider that already exists."""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__(f"Provider already registered: {provider_id}")


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Describes a registered enrichment provider.

    Attributes:
        provider_id: Unique string identifier for the provider.
        rights_class: GREEN/YELLOW/RED classification.
        cache_policy: Caching configuration for this provider.
        requires_byol: True if the provider needs tenant-supplied credentials.
        connector: The connector instance implementing the adapter contract.
    """

    provider_id: str
    rights_class: RightsClass
    cache_policy: CachePolicyConfig
    requires_byol: bool
    connector: EnrichmentConnector


@dataclass
class EnrichmentProviderRegistry:
    """Registry of enrichment connectors.

    Provides lookup by provider_id. Fail-closed: unknown providers raise
    ProviderNotRegisteredError rather than returning empty/None results.
    """

    _providers: dict[str, ProviderDescriptor] = field(default_factory=dict)

    def register(
        self,
        connector: EnrichmentConnector,
        *,
        requires_byol: bool = False,
    ) -> None:
        """Register a connector in the registry.

        Args:
            connector: Connector implementing the adapter contract.
            requires_byol: Whether this connector needs tenant-supplied credentials.

        Raises:
            DuplicateProviderError: If a provider with the same ID is already registered.
        """
        pid = connector.provider_id
        if pid in self._providers:
            raise DuplicateProviderError(pid)

        descriptor = ProviderDescriptor(
            provider_id=pid,
            rights_class=connector.rights_class,
            cache_policy=connector.cache_policy,
            requires_byol=requires_byol,
            connector=connector,
        )
        self._providers[pid] = descriptor
        logger.info(
            "Registered enrichment provider: %s (rights=%s, byol=%s)",
            pid,
            connector.rights_class,
            requires_byol,
        )

    def get(self, provider_id: str) -> ProviderDescriptor:
        """Look up a provider by ID. Fail-closed on unknown providers.

        Args:
            provider_id: Unique provider identifier.

        Returns:
            ProviderDescriptor for the requested provider.

        Raises:
            ProviderNotRegisteredError: If provider_id is not registered.
        """
        descriptor = self._providers.get(provider_id)
        if descriptor is None:
            raise ProviderNotRegisteredError(provider_id)
        return descriptor

    def list_providers(self) -> list[ProviderDescriptor]:
        """Return all registered provider descriptors."""
        return list(self._providers.values())

    @property
    def provider_ids(self) -> frozenset[str]:
        """Return the set of registered provider IDs."""
        return frozenset(self._providers.keys())
