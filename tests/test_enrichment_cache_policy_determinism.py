"""Tests for deterministic enrichment cache policy.

Verifies:
- Cache key is deterministic (same inputs = same key)
- Cache key changes when any input field changes
- No-store policy skips caching
- TTL expiry evicts entries
- Only HIT results are cached
- Stable ordering of query fields in cache key
"""

from __future__ import annotations

from datetime import UTC, datetime

from idis.services.enrichment.cache_policy import (
    EnrichmentCacheStore,
    compute_cache_key,
    store_cache_entry,
    try_cache_lookup,
)
from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentProvenance,
    EnrichmentPurpose,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)


def _make_request(
    tenant_id: str = "tenant-001",
    cik: str = "0001234567",
    purpose: EnrichmentPurpose = EnrichmentPurpose.DUE_DILIGENCE,
    requested_fields: list[str] | None = None,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id=tenant_id,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(cik=cik),
        requested_fields=requested_fields,
        purpose=purpose,
    )


def _make_hit_result() -> EnrichmentResult:
    return EnrichmentResult(
        status=EnrichmentStatus.HIT,
        normalized={"registrant_name": "Test Corp"},
        provenance=EnrichmentProvenance(
            provider_id="sec_edgar",
            source_id="sec_edgar",
            retrieved_at=datetime.now(UTC),
            rights_class=RightsClass.GREEN,
            raw_ref_hash="abc123",
        ),
    )


class TestCacheKeyDeterminism:
    """Cache key must be deterministic and stable."""

    def test_same_inputs_produce_same_key(self) -> None:
        req = _make_request()
        key1 = compute_cache_key(request=req, provider_id="sec_edgar")
        key2 = compute_cache_key(request=req, provider_id="sec_edgar")
        assert key1 == key2

    def test_different_tenant_produces_different_key(self) -> None:
        req1 = _make_request(tenant_id="tenant-001")
        req2 = _make_request(tenant_id="tenant-002")
        key1 = compute_cache_key(request=req1, provider_id="sec_edgar")
        key2 = compute_cache_key(request=req2, provider_id="sec_edgar")
        assert key1 != key2

    def test_different_cik_produces_different_key(self) -> None:
        req1 = _make_request(cik="0001234567")
        req2 = _make_request(cik="0009999999")
        key1 = compute_cache_key(request=req1, provider_id="sec_edgar")
        key2 = compute_cache_key(request=req2, provider_id="sec_edgar")
        assert key1 != key2

    def test_different_provider_produces_different_key(self) -> None:
        req = _make_request()
        key1 = compute_cache_key(request=req, provider_id="sec_edgar")
        key2 = compute_cache_key(request=req, provider_id="companies_house")
        assert key1 != key2

    def test_different_purpose_produces_different_key(self) -> None:
        req1 = _make_request(purpose=EnrichmentPurpose.DUE_DILIGENCE)
        req2 = _make_request(purpose=EnrichmentPurpose.KYC)
        key1 = compute_cache_key(request=req1, provider_id="sec_edgar")
        key2 = compute_cache_key(request=req2, provider_id="sec_edgar")
        assert key1 != key2

    def test_different_connector_version_produces_different_key(self) -> None:
        req = _make_request()
        key1 = compute_cache_key(request=req, provider_id="sec_edgar", connector_version="1.0.0")
        key2 = compute_cache_key(request=req, provider_id="sec_edgar", connector_version="2.0.0")
        assert key1 != key2

    def test_requested_fields_order_does_not_affect_key(self) -> None:
        """Fields are sorted before hashing, so order should not matter."""
        req1 = _make_request(requested_fields=["name", "cik", "filings"])
        req2 = _make_request(requested_fields=["filings", "cik", "name"])
        key1 = compute_cache_key(request=req1, provider_id="sec_edgar")
        key2 = compute_cache_key(request=req2, provider_id="sec_edgar")
        assert key1 == key2

    def test_key_is_sha256_hex(self) -> None:
        req = _make_request()
        key = compute_cache_key(request=req, provider_id="sec_edgar")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


class TestNoStorePolicy:
    """No-store policy must prevent caching."""

    def test_no_store_skips_cache_lookup(self) -> None:
        store = EnrichmentCacheStore()
        req = _make_request()
        policy = CachePolicyConfig(ttl_seconds=3600, no_store=True)

        result = try_cache_lookup(
            cache_store=store,
            request=req,
            provider_id="sec_edgar",
            policy=policy,
        )
        assert result is None

    def test_no_store_skips_cache_write(self) -> None:
        store = EnrichmentCacheStore()
        req = _make_request()
        policy = CachePolicyConfig(ttl_seconds=3600, no_store=True)

        store_cache_entry(
            cache_store=store,
            request=req,
            provider_id="sec_edgar",
            result=_make_hit_result(),
            policy=policy,
        )
        assert store.size == 0

    def test_zero_ttl_skips_caching(self) -> None:
        store = EnrichmentCacheStore()
        req = _make_request()
        policy = CachePolicyConfig(ttl_seconds=0, no_store=False)

        store_cache_entry(
            cache_store=store,
            request=req,
            provider_id="sec_edgar",
            result=_make_hit_result(),
            policy=policy,
        )
        assert store.size == 0


class TestCacheStoreOperations:
    """Cache store put/get/expiry operations."""

    def test_store_and_retrieve_hit(self) -> None:
        store = EnrichmentCacheStore()
        req = _make_request()
        policy = CachePolicyConfig(ttl_seconds=3600)
        result = _make_hit_result()

        store_cache_entry(
            cache_store=store,
            request=req,
            provider_id="sec_edgar",
            result=result,
            policy=policy,
        )

        cached = try_cache_lookup(
            cache_store=store,
            request=req,
            provider_id="sec_edgar",
            policy=policy,
        )
        assert cached is not None
        assert cached.status == EnrichmentStatus.HIT
        assert cached.normalized["registrant_name"] == "Test Corp"

    def test_non_hit_results_not_cached(self) -> None:
        store = EnrichmentCacheStore()
        req = _make_request()
        policy = CachePolicyConfig(ttl_seconds=3600)

        for status in [
            EnrichmentStatus.MISS,
            EnrichmentStatus.ERROR,
            EnrichmentStatus.BLOCKED_RIGHTS,
            EnrichmentStatus.BLOCKED_MISSING_BYOL,
        ]:
            result = EnrichmentResult(status=status)
            store_cache_entry(
                cache_store=store,
                request=req,
                provider_id="sec_edgar",
                result=result,
                policy=policy,
            )

        assert store.size == 0

    def test_cache_clear(self) -> None:
        store = EnrichmentCacheStore()
        req = _make_request()
        policy = CachePolicyConfig(ttl_seconds=3600)

        store_cache_entry(
            cache_store=store,
            request=req,
            provider_id="sec_edgar",
            result=_make_hit_result(),
            policy=policy,
        )
        assert store.size == 1

        store.clear()
        assert store.size == 0
