"""GL-09 packet 1: connector cache flags agree with the Task 9 no-store policy."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
_DEFAULT_REGISTRY_PROVIDER_RULING = (
    "owner ruling needed before adding or removing a provider that the default "
    "enrichment service registers: that provider needs the Task 9 no-store ruling"
)
_ONE_CONNECTOR_CLASS_MESSAGE = "connector module defines more than one connector class"
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
    """Import every connector module and return classes that define the adapter.

    Sub-packages are included. A module that defines more than one connector
    class fails the scan and names that module.
    """
    package = importlib.import_module(_CONNECTOR_PACKAGE)
    classes: list[type[object]] = []
    counts: dict[str, int] = {}
    prefix = package.__name__ + "."
    search_path = list(package.__path__)
    for module_info in pkgutil.walk_packages(search_path, prefix):
        module = importlib.import_module(module_info.name)
        found = 0
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__:
                continue
            if not _declares_connector_contract(cls):
                continue
            classes.append(cls)
            found += 1
        if found:
            counts[module.__name__] = found
    offenders = sorted(name for name, count in counts.items() if count > 1)
    if offenders:
        raise AssertionError(f"{_ONE_CONNECTOR_CLASS_MESSAGE}: {', '.join(offenders)}")
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
    assert registered == _DEFAULT_REGISTRY_PROVIDER_IDS, _DEFAULT_REGISTRY_PROVIDER_RULING

    discovered = _registry_of_discovered_connectors()
    by_id = {str(row["provider_id"]): row for row in rows}
    for provider_id in sorted(registered):
        cache = discovered.get(provider_id).connector.cache_policy
        policy = PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY[provider_id]
        assert by_id[provider_id]["cache_no_store"] is False, provider_id
        assert cache.no_store is False, provider_id
        assert policy.no_store_policy_version == EXTERNAL_CONTEXT_STORE_POLICY_V1, provider_id


def test_connector_class_in_a_subpackage_is_discovered(tmp_path: Path) -> None:
    """A connector class in a sub-module of a temporary package is discovered."""
    planted_root = tmp_path / "extra_connectors"
    nested = planted_root / "hx1_nested"
    nested.mkdir(parents=True)
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "planted.py").write_text(
        _planted_connector_source("PlantedSubpackageConnector"),
        encoding="utf-8",
    )
    module_names = (
        f"{_CONNECTOR_PACKAGE}.hx1_nested",
        f"{_CONNECTOR_PACKAGE}.hx1_nested.planted",
    )
    with _appended_connector_path(planted_root, module_names):
        discovered = {cls.__name__ for cls in _discover_connector_classes()}
    assert "PlantedSubpackageConnector" in discovered


def test_module_with_two_connector_classes_names_the_module(tmp_path: Path) -> None:
    """Two connector classes in one module fail the scan and name that module."""
    planted_root = tmp_path / "extra_connectors"
    planted_root.mkdir()
    (planted_root / "dual.py").write_text(
        _planted_connector_source("FirstPlantedConnector", "SecondPlantedConnector"),
        encoding="utf-8",
    )
    module_name = f"{_CONNECTOR_PACKAGE}.dual"
    with (
        _appended_connector_path(planted_root, (module_name,)),
        pytest.raises(AssertionError, match="more than one connector class") as caught,
    ):
        _discover_connector_classes()
    assert module_name in str(caught.value)


def test_default_registry_provider_difference_names_the_task9_ruling() -> None:
    """Adding a provider to the default set names the Task 9 no-store ruling."""
    planted = set(_DEFAULT_REGISTRY_PROVIDER_IDS)
    planted.add("planted_provider")
    with pytest.raises(AssertionError, match="Task 9 no-store ruling") as caught:
        assert planted == _DEFAULT_REGISTRY_PROVIDER_IDS, _DEFAULT_REGISTRY_PROVIDER_RULING
    assert _DEFAULT_REGISTRY_PROVIDER_RULING in str(caught.value)


def _planted_connector_source(*class_names: str) -> str:
    """Return source for connector classes that declare the adapter contract."""
    blocks: list[str] = []
    for name in class_names:
        blocks.append(
            f"class {name}:\n"
            f"    provider_id = '{name}'\n"
            "    rights_class = 'green'\n"
            "    cache_policy = None\n"
            "\n"
            "    def fetch(self, request: object, ctx: object) -> None:\n"
            "        return None\n"
        )
    return "\n".join(blocks)


@contextmanager
def _appended_connector_path(extra: Path, module_names: tuple[str, ...]) -> Iterator[None]:
    """Search a copy of the connector path plus ``extra``, then restore it."""
    package = importlib.import_module(_CONNECTOR_PACKAGE)
    original = list(package.__path__)
    package.__path__[:] = [*original, str(extra)]
    try:
        yield
    finally:
        package.__path__[:] = original
        prefix = package.__name__ + "."
        removed_tops: set[str] = set()
        for name in module_names:
            sys.modules.pop(name, None)
            if not name.startswith(prefix):
                continue
            top = name[len(prefix) :].split(".", 1)[0]
            if top and top not in removed_tops:
                package.__dict__.pop(top, None)
                removed_tops.add(top)
