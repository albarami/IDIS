"""Tests for PatentsView enrichment connector.

Uses httpx.MockTransport for deterministic testing with no live network calls.
"""

from __future__ import annotations

from typing import Any

import httpx

from idis.services.enrichment.connectors.patentsview import (
    PATENTSVIEW_PROVIDER_ID,
    PatentsViewConnector,
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
    "patents": [
        {
            "patent_number": "US12345678",
            "patent_title": "Quantum Widget System",
            "patent_date": "2024-01-15",
            "assignees": [{"assignee_organization": "Acme Corp"}],
        },
    ],
    "total_patent_count": 1,
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
        tenant_id="tenant-pv-001",
        entity_type=entity_type,
        query=EnrichmentQuery(company_name=company_name),
    )


def _ctx() -> EnrichmentContext:
    return EnrichmentContext(timeout_seconds=5.0, max_retries=0, request_id="req-pv-test")


class TestPatentsViewProperties:
    def test_provider_id(self) -> None:
        assert PatentsViewConnector().provider_id == PATENTSVIEW_PROVIDER_ID

    def test_rights_class_is_green(self) -> None:
        assert PatentsViewConnector().rights_class == RightsClass.GREEN

    def test_cache_policy(self) -> None:
        policy = PatentsViewConnector().cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0


class TestPatentsViewFetchSuccess:
    def test_successful_fetch_returns_hit(self) -> None:
        connector = PatentsViewConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["total_patent_count"] == 1
        assert result.normalized["patents"][0]["patent_number"] == "US12345678"
        assert result.normalized["patents"][0]["assignees"] == ["Acme Corp"]

    def test_provenance_populated(self) -> None:
        connector = PatentsViewConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.provenance is not None
        assert result.provenance.source_id == PATENTSVIEW_PROVIDER_ID
        assert result.provenance.identifiers_used["company_name"] == "Acme Corp"

    def test_normalized_schema_deterministic(self) -> None:
        connector = PatentsViewConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx())
        r2 = connector.fetch(_make_request(), _ctx())
        assert r1.normalized == r2.normalized


class TestPatentsViewFetchFailures:
    def test_404_returns_miss(self) -> None:
        connector = PatentsViewConnector(http_client=_make_client(status_code=404))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        connector = PatentsViewConnector(
            http_client=_make_client(status_code=500, response_json={})
        )
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        connector = PatentsViewConnector(http_client=_make_client(raise_error=True))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        connector = PatentsViewConnector(http_client=_make_client())
        result = connector.fetch(_make_request(entity_type=EntityType.PERSON), _ctx())
        assert result.status == EnrichmentStatus.ERROR

    def test_missing_company_name_returns_error(self) -> None:
        connector = PatentsViewConnector(http_client=_make_client())
        request = EnrichmentRequest(
            tenant_id="t-001", entity_type=EntityType.COMPANY, query=EnrichmentQuery()
        )
        assert connector.fetch(request, _ctx()).status == EnrichmentStatus.ERROR

    def test_null_patents_returns_miss(self) -> None:
        connector = PatentsViewConnector(http_client=_make_client(response_json={"patents": None}))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.MISS
