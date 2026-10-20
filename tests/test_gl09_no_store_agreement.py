"""GL-09 packet 1: connector cache flags agree with the Task 9 no-store policy."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from datetime import UTC, datetime

from idis.audit.sink import InMemoryAuditSink
from idis.services.enrichment.cache_policy import (
    CacheEntry,
    EnrichmentCacheStore,
    store_cache_entry,
    try_cache_lookup,
)
from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentConnector,
    EnrichmentProvenance,
    EnrichmentPurpose,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry
from idis.services.enrichment.service import create_default_enrichment_service
from idis.services.enrichment.task9.provider_catalog import PROVIDER_CATALOG
from idis.services.enrichment.task9.rights_index_policy import (
    EXTERNAL_CONTEXT_NO_STORE_POLICY_V1,
    EXTERNAL_CONTEXT_STORE_POLICY_V1,
    PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY,
)

_CONNECTOR_PACKAGE = "idis.services.enrichment.connectors"
_CONTRACT_MEMBERS = ("provider_id", "rights_class", "cache_policy", "fetch")
_NO_STORE_PROVIDER_ID = "perplexity"
_STORE_TTL_SECONDS = 3600
_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_PERPLEXITY_REGISTRATION_RULING = (
    "owner ruling needed before registering perplexity on the default "
    "enrichment service: strict FULL turns a connector error into a failed step"
)
_DEFAULT_REGISTRY_PROVIDER_IDS = frozenset(
    {
        "companies_house",
        "escwa_catalog",
        "escwa_ispar",
        "finnhub",
        "fmp",
        "fred",
        "gdelt",
        "github",
        "google_news_rss",
        "hackernews",
        "patentsview",
        "qatar_open_data",
        "sec_edgar",
        "wayback",
        "world_bank",
    }
)


class _CallLogCacheStore(EnrichmentCacheStore):
    """In-memory cache that records read and write calls."""

    def __init__(self) -> None:
        super().__init__()
        self.call_log: list[str] = []

    def get(self, cache_key: str) -> CacheEntry | None:
        """Record a read, then perform the real lookup."""
        self.call_log.append("get")
        return super().get(cache_key)

    def put(self, entry: CacheEntry) -> None:
        """Record a write, then perform the real store."""
        self.call_log.append("put")
        super().put(entry)


def _declares_connector_contract(cls: type[object]) -> bool:
    """Return whether ``cls`` defines the enrichment connector adapter members."""
    for name in _CONTRACT_MEMBERS:
        if not any(name in base.__dict__ for base in cls.__mro__):
            return False
    fetch_attr = next(base.__dict__["fetch"] for base in cls.__mro__ if "fetch" in base.__dict__)
    return inspect.isfunction(fetch_attr)


def _discover_connector_classes() -> tuple[type[object], ...]:
    """Import every connector module and return classes that define the adapter."""
    package = importlib.import_module(_CONNECTOR_PACKAGE)
    classes: list[type[object]] = []
    prefix = package.__name__ + "."
    for module_info in pkgutil.iter_modules(package.__path__, prefix):
        module = importlib.import_module(module_info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__:
                continue
            if _declares_connector_contract(cls):
                classes.append(cls)
    return tuple(classes)


def _registry_of_discovered_connectors() -> EnrichmentProviderRegistry:
    """Register every discovered connector the same way the default registry does."""
    registry = EnrichmentProviderRegistry()
    for cls in _discover_connector_classes():
        connector = cls()
        if not isinstance(connector, EnrichmentConnector):
            raise AssertionError(f"{cls.__name__} does not satisfy EnrichmentConnector")
        registry.register(connector)
    return registry


def _request() -> EnrichmentRequest:
    """Return a cacheable company request with no provider credentials."""
    return EnrichmentRequest(
        tenant_id=_TENANT_ID,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name="Northwind Analytics"),
        purpose=EnrichmentPurpose.DUE_DILIGENCE,
    )


def _hit_result() -> EnrichmentResult:
    """Return one HIT result the cache writer is allowed to store."""
    return EnrichmentResult(
        status=EnrichmentStatus.HIT,
        normalized={"company_name": "Northwind Analytics"},
        provenance=EnrichmentProvenance(
            provider_id="sec_edgar",
            source_id="sec_edgar",
            retrieved_at=datetime.now(UTC),
            rights_class=RightsClass.GREEN,
            raw_ref_hash="abc123",
        ),
    )


def test_discovered_connectors_match_catalog_and_policy_rows() -> None:
    """Connector ids, catalog ids, and policy ids are the same set."""
    connector_ids = _registry_of_discovered_connectors().provider_ids
    catalog_ids = frozenset(PROVIDER_CATALOG)
    policy_ids = frozenset(PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY)
    catalog_only = catalog_ids - connector_ids
    connectors_without_catalog = connector_ids - catalog_ids
    connectors_without_policy = connector_ids - policy_ids

    assert connector_ids, "no connector classes discovered under the connectors package"
    assert policy_ids == catalog_ids
    assert connectors_without_catalog == frozenset(), "connector with no catalog row: " + ", ".join(
        sorted(connectors_without_catalog)
    )
    assert connectors_without_policy == frozenset(), "connector with no policy row: " + ", ".join(
        sorted(connectors_without_policy)
    )
    assert catalog_only == frozenset(), "catalog provider with no connector: " + ", ".join(
        sorted(catalog_only)
    )
    assert connector_ids == catalog_ids


def test_connector_no_store_agrees_with_rights_index_policy() -> None:
    """Each connector no_store flag matches its Task 9 no-store policy row."""
    registry = _registry_of_discovered_connectors()
    no_store_ids: set[str] = set()
    problems: list[str] = []

    for descriptor in sorted(registry.list_providers(), key=lambda item: item.provider_id):
        policy = PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY.get(descriptor.provider_id)
        if policy is None:
            problems.append(f"{descriptor.provider_id}: no policy row")
            continue
        cache = descriptor.connector.cache_policy
        expected_no_store = policy.no_store_policy_version == EXTERNAL_CONTEXT_NO_STORE_POLICY_V1
        if cache.no_store is not expected_no_store:
            problems.append(
                f"{descriptor.provider_id}: connector no_store={cache.no_store} "
                f"policy={policy.no_store_policy_version}"
            )
        if cache.no_store:
            no_store_ids.add(descriptor.provider_id)
            if policy.external_context_index_allowed:
                problems.append(
                    f"{descriptor.provider_id}: no_store is true and "
                    "external_context_index_allowed is true"
                )

    assert problems == []
    assert no_store_ids == frozenset({_NO_STORE_PROVIDER_ID})


def test_no_store_policy_performs_no_cache_read_or_write() -> None:
    """A no-store policy does not call the cache read or write methods."""
    store = _CallLogCacheStore()
    request = _request()
    policy = CachePolicyConfig(ttl_seconds=_STORE_TTL_SECONDS, no_store=True)

    looked_up = try_cache_lookup(
        cache_store=store,
        request=request,
        provider_id="sec_edgar",
        policy=policy,
    )
    store_cache_entry(
        cache_store=store,
        request=request,
        provider_id="sec_edgar",
        result=_hit_result(),
        policy=policy,
    )

    assert store.call_log == []
    assert looked_up is None


def test_store_policy_reads_and_writes_cache() -> None:
    """A store policy calls the cache write and then the cache read."""
    store = _CallLogCacheStore()
    request = _request()
    policy = CachePolicyConfig(ttl_seconds=_STORE_TTL_SECONDS, no_store=False)
    stored = _hit_result()

    store_cache_entry(
        cache_store=store,
        request=request,
        provider_id="sec_edgar",
        result=stored,
        policy=policy,
    )
    looked_up = try_cache_lookup(
        cache_store=store,
        request=request,
        provider_id="sec_edgar",
        policy=policy,
    )

    assert store.call_log == ["put", "get"]
    assert looked_up is not None
    assert looked_up.status == EnrichmentStatus.HIT


def test_default_enrichment_service_registers_store_policy_providers_only() -> None:
    """The default service registers store-policy providers and not perplexity."""
    service = create_default_enrichment_service(
        audit_sink=InMemoryAuditSink(),
        strict_full_live=False,
    )
    rows = service.list_providers()
    registered = {str(row["provider_id"]) for row in rows}

    assert _NO_STORE_PROVIDER_ID not in registered, _PERPLEXITY_REGISTRATION_RULING
    assert registered == _DEFAULT_REGISTRY_PROVIDER_IDS

    discovered = _registry_of_discovered_connectors()
    by_id = {str(row["provider_id"]): row for row in rows}
    for provider_id in sorted(registered):
        cache = discovered.get(provider_id).connector.cache_policy
        policy = PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY[provider_id]
        assert by_id[provider_id]["cache_no_store"] is False, provider_id
        assert cache.no_store is False, provider_id
        assert policy.no_store_policy_version == EXTERNAL_CONTEXT_STORE_POLICY_V1, provider_id
