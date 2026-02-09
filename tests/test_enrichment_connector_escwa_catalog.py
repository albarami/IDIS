"""Tests for ESCWA Data Catalog enrichment connector.

Uses httpx.MockTransport for deterministic testing with no live network calls.
"""

from __future__ import annotations

from typing import Any

import httpx

from idis.services.enrichment.connectors.escwa_catalog import (
    ESCWA_CATALOG_PROVIDER_ID,
    EscwaCatalogConnector,
)
from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)

SAMPLE_RESPONSE: dict[str, Any] = {
    "success": True,
    "result": {
        "count": 1,
        "results": [
            {
                "name": "arab-gdp-dataset",
                "title": "Arab Region GDP Data",
                "notes": "GDP data for Arab states",
                "author": "UN ESCWA",
                "metadata_created": "2024-01-01T00:00:00Z",
                "metadata_modified": "2024-01-15T00:00:00Z",
                "organization": {"title": "ESCWA Statistics"},
            }
        ],
    },
}


def _make_client(
    status_code: int = 200,
    response_json: dict[str, Any] | None = None,
    raise_error: bool = False,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_error:
            raise httpx.ConnectError("Connection refused")
        body = response_json if response_json is not None else SAMPLE_RESPONSE
        return httpx.Response(status_code=status_code, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _make_request(
    company_name: str = "GDP",
    entity_type: EntityType = EntityType.COMPANY,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-escwa-001",
        entity_type=entity_type,
        query=EnrichmentQuery(company_name=company_name),
    )


def _ctx() -> EnrichmentContext:
    return EnrichmentContext(timeout_seconds=5.0, max_retries=0, request_id="req-escwa-test")


class TestEscwaCatalogProperties:
    def test_provider_id(self) -> None:
        assert EscwaCatalogConnector().provider_id == ESCWA_CATALOG_PROVIDER_ID

    def test_rights_class_is_green(self) -> None:
        assert EscwaCatalogConnector().rights_class == RightsClass.GREEN

    def test_cache_policy(self) -> None:
        policy = EscwaCatalogConnector().cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0


class TestEscwaCatalogFetchSuccess:
    def test_successful_fetch_returns_hit(self) -> None:
        connector = EscwaCatalogConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["count"] == 1
        assert result.normalized["datasets"][0]["title"] == "Arab Region GDP Data"

    def test_provenance_populated(self) -> None:
        connector = EscwaCatalogConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.provenance is not None
        assert result.provenance.source_id == ESCWA_CATALOG_PROVIDER_ID
        assert result.provenance.identifiers_used["company_name"] == "GDP"

    def test_normalized_schema_deterministic(self) -> None:
        connector = EscwaCatalogConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx())
        r2 = connector.fetch(_make_request(), _ctx())
        assert r1.normalized == r2.normalized


class TestEscwaCatalogFetchFailures:
    def test_404_returns_miss(self) -> None:
        connector = EscwaCatalogConnector(http_client=_make_client(status_code=404))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        connector = EscwaCatalogConnector(
            http_client=_make_client(status_code=500, response_json={})
        )
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        connector = EscwaCatalogConnector(http_client=_make_client(raise_error=True))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        connector = EscwaCatalogConnector(http_client=_make_client())
        result = connector.fetch(_make_request(entity_type=EntityType.PERSON), _ctx())
        assert result.status == EnrichmentStatus.ERROR

    def test_missing_company_name_returns_error(self) -> None:
        connector = EscwaCatalogConnector(http_client=_make_client())
        request = EnrichmentRequest(
            tenant_id="t-001", entity_type=EntityType.COMPANY, query=EnrichmentQuery()
        )
        assert connector.fetch(request, _ctx()).status == EnrichmentStatus.ERROR

    def test_ckan_success_false_returns_miss(self) -> None:
        connector = EscwaCatalogConnector(
            http_client=_make_client(response_json={"success": False})
        )
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.MISS
