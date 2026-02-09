"""Tests for HackerNews enrichment connector.

Uses httpx.MockTransport for deterministic testing with no live network calls.
"""

from __future__ import annotations

from typing import Any

import httpx

from idis.services.enrichment.connectors.hackernews import (
    HACKERNEWS_PROVIDER_ID,
    HackerNewsConnector,
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
    "hits": [
        {
            "title": "Acme Corp raises $50M",
            "url": "https://example.com/acme",
            "points": 150,
            "num_comments": 42,
            "author": "pg",
            "created_at": "2024-01-15T10:00:00Z",
            "objectID": "12345",
        },
    ],
    "nbHits": 1,
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
    company_name: str = "Acme Corp",
    entity_type: EntityType = EntityType.COMPANY,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-hn-001",
        entity_type=entity_type,
        query=EnrichmentQuery(company_name=company_name),
    )


def _ctx() -> EnrichmentContext:
    return EnrichmentContext(timeout_seconds=5.0, max_retries=0, request_id="req-hn-test")


class TestHackerNewsProperties:
    def test_provider_id(self) -> None:
        assert HackerNewsConnector().provider_id == HACKERNEWS_PROVIDER_ID

    def test_rights_class_is_green(self) -> None:
        assert HackerNewsConnector().rights_class == RightsClass.GREEN

    def test_cache_policy(self) -> None:
        policy = HackerNewsConnector().cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0
        assert policy.no_store is False


class TestHackerNewsFetchSuccess:
    def test_successful_fetch_returns_hit(self) -> None:
        connector = HackerNewsConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["total_hits"] == 1
        assert len(result.normalized["stories"]) == 1
        assert result.normalized["stories"][0]["title"] == "Acme Corp raises $50M"

    def test_provenance_populated(self) -> None:
        connector = HackerNewsConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.provenance is not None
        assert result.provenance.source_id == HACKERNEWS_PROVIDER_ID
        assert result.provenance.rights_class == RightsClass.GREEN
        assert result.provenance.raw_ref_hash
        assert result.provenance.identifiers_used["company_name"] == "Acme Corp"

    def test_normalized_schema_deterministic(self) -> None:
        connector = HackerNewsConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx())
        r2 = connector.fetch(_make_request(), _ctx())
        assert r1.normalized == r2.normalized


class TestHackerNewsFetchFailures:
    def test_404_returns_miss(self) -> None:
        connector = HackerNewsConnector(http_client=_make_client(status_code=404))
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        connector = HackerNewsConnector(http_client=_make_client(status_code=500, response_json={}))
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        connector = HackerNewsConnector(http_client=_make_client(raise_error=True))
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        connector = HackerNewsConnector(http_client=_make_client())
        result = connector.fetch(_make_request(entity_type=EntityType.PERSON), _ctx())
        assert result.status == EnrichmentStatus.ERROR
        assert "COMPANY" in result.normalized.get("error", "")

    def test_missing_company_name_returns_error(self) -> None:
        connector = HackerNewsConnector(http_client=_make_client())
        request = EnrichmentRequest(
            tenant_id="t-001", entity_type=EntityType.COMPANY, query=EnrichmentQuery()
        )
        result = connector.fetch(request, _ctx())
        assert result.status == EnrichmentStatus.ERROR
        assert "company_name" in result.normalized.get("error", "")
