"""Deterministic cache policy engine for enrichment results.

Cache keys are computed as SHA256 hashes of canonical JSON containing:
(tenant_id, provider_id, entity_type, canonical_query_json, requested_fields,
purpose, connector_version).

Stable ordering + serialization ensures deterministic keys.

Spec: IDIS_Enrichment_Connector_Framework_v0_1.md §4
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
)

logger = logging.getLogger(__name__)

CONNECTOR_VERSION_KEY = "connector_version"
DEFAULT_CONNECTOR_VERSION = "1.0.0"


class CacheEntry(BaseModel):
    """A cached enrichment result.

    Attributes:
        cache_key: Deterministic hash key.
        result: The cached EnrichmentResult.
        created_at: When the entry was stored.
        expires_at: When the entry expires.
    """

    cache_key: str
    result: EnrichmentResult
    created_at: datetime
    expires_at: datetime


class EnrichmentCacheStore:
    """In-memory cache store for enrichment results.

    Provides tenant-scoped, deterministic caching with TTL expiry.
    Thread-safety note: single-process only; for multi-process deployments
    use Postgres-backed cache (future extension).
    """

    def __init__(self) -> None:
        """Initialize empty cache store."""
        self._store: dict[str, CacheEntry] = {}

    def get(self, cache_key: str) -> CacheEntry | None:
        """Look up a cache entry by key.

        Returns None if not found or expired. Expired entries are evicted.

        Args:
            cache_key: Deterministic cache key.

        Returns:
            CacheEntry if found and not expired, None otherwise.
        """
        entry = self._store.get(cache_key)
        if entry is None:
            return None

        now = datetime.now(UTC)
        if now >= entry.expires_at:
            del self._store[cache_key]
            return None

        return entry

    def put(self, entry: CacheEntry) -> None:
        """Store a cache entry.

        Args:
            entry: CacheEntry to store.
        """
        self._store[entry.cache_key] = entry

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()

    @property
    def size(self) -> int:
        """Return the number of entries in the cache."""
        return len(self._store)


def compute_cache_key(
    *,
    request: EnrichmentRequest,
    provider_id: str,
    connector_version: str = DEFAULT_CONNECTOR_VERSION,
) -> str:
    """Compute a deterministic cache key for an enrichment request.

    The key is a SHA256 hex digest of canonical JSON with stable field ordering.

    Args:
        request: The enrichment request.
        provider_id: Provider identifier.
        connector_version: Version string of the connector.

    Returns:
        SHA256 hex digest string.
    """
    query_dict = request.query.model_dump(exclude_none=True)

    canonical: dict[str, Any] = {
        "connector_version": connector_version,
        "entity_type": request.entity_type.value,
        "provider_id": provider_id,
        "purpose": request.purpose.value,
        "query": query_dict,
        "requested_fields": sorted(request.requested_fields) if request.requested_fields else [],
        "tenant_id": request.tenant_id,
    }

    canonical_json = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def try_cache_lookup(
    *,
    cache_store: EnrichmentCacheStore,
    request: EnrichmentRequest,
    provider_id: str,
    policy: CachePolicyConfig,
    connector_version: str = DEFAULT_CONNECTOR_VERSION,
) -> EnrichmentResult | None:
    """Attempt to find a cached result for the given request.

    Returns None if cache policy is no-store, entry not found, or entry expired.

    Args:
        cache_store: The cache store to search.
        request: Enrichment request.
        provider_id: Provider identifier.
        policy: Cache policy configuration.
        connector_version: Connector version for cache key computation.

    Returns:
        Cached EnrichmentResult if found, None otherwise.
    """
    if policy.no_store:
        return None

    if policy.ttl_seconds == 0:
        return None

    key = compute_cache_key(
        request=request,
        provider_id=provider_id,
        connector_version=connector_version,
    )
    entry = cache_store.get(key)
    if entry is not None:
        return entry.result

    return None


def store_cache_entry(
    *,
    cache_store: EnrichmentCacheStore,
    request: EnrichmentRequest,
    provider_id: str,
    result: EnrichmentResult,
    policy: CachePolicyConfig,
    connector_version: str = DEFAULT_CONNECTOR_VERSION,
) -> None:
    """Store an enrichment result in the cache if policy allows.

    Only HIT results are cached. No-store policies and zero-TTL policies
    skip caching.

    Args:
        cache_store: The cache store.
        request: Enrichment request.
        provider_id: Provider identifier.
        result: The enrichment result to cache.
        policy: Cache policy configuration.
        connector_version: Connector version for cache key computation.
    """
    if policy.no_store:
        return

    if policy.ttl_seconds == 0:
        return

    if result.status != EnrichmentStatus.HIT:
        return

    key = compute_cache_key(
        request=request,
        provider_id=provider_id,
        connector_version=connector_version,
    )

    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=policy.ttl_seconds)

    entry = CacheEntry(
        cache_key=key,
        result=result,
        created_at=now,
        expires_at=expires_at,
    )
    cache_store.put(entry)
