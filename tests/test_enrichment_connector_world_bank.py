"""Tests for World Bank enrichment connector.

Uses httpx.MockTransport for deterministic testing with no live network calls.
"""

from __future__ import annotations

from typing import Any

import httpx

from idis.services.enrichment.connectors.world_bank import (
    WORLD_BANK_PROVIDER_ID,
    WorldBankConnector,
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

SAMPLE_RESPONSE: list[Any] = [
    {"page": 1, "pages": 1, "per_page": 10, "total": 2},
    [
        {
            "country": {"id": "QA", "value": "Qatar"},
            "date": "2023",
            "value": 235000000000,
            "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP"},
        },
        {
            "country": {"id": "QA", "value": "Qatar"},
            "date": "2022",
            "value": 220000000000,
            "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP"},
        },
    ],
]


def _make_client(
    status_code: int = 200,
    response_json: Any = None,
    raise_error: bool = False,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_error:
            raise httpx.ConnectError("Connection refused")
        body = response_json if response_json is not None else SAMPLE_RESPONSE
        return httpx.Response(status_code=status_code, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _make_request(
    jurisdiction: str = "QA",
    entity_type: EntityType = EntityType.COMPANY,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-wb-001",
        entity_type=entity_type,
        query=EnrichmentQuery(jurisdiction=jurisdiction),
    )


def _ctx() -> EnrichmentContext:
    return EnrichmentContext(timeout_seconds=5.0, max_retries=0, request_id="req-wb-test")


class TestWorldBankProperties:
    def test_provider_id(self) -> None:
        assert WorldBankConnector().provider_id == WORLD_BANK_PROVIDER_ID

    def test_rights_class_is_green(self) -> None:
        assert WorldBankConnector().rights_class == RightsClass.GREEN

    def test_cache_policy(self) -> None:
        policy = WorldBankConnector().cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0


class TestWorldBankFetchSuccess:
    def test_successful_fetch_returns_hit(self) -> None:
        connector = WorldBankConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["country_code"] == "QA"
        assert result.normalized["country_name"] == "Qatar"
        assert result.normalized["total_records"] == 2

    def test_provenance_populated(self) -> None:
        connector = WorldBankConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.provenance is not None
        assert result.provenance.provider_id == WORLD_BANK_PROVIDER_ID
        assert result.provenance.source_id == WORLD_BANK_PROVIDER_ID
        assert result.provenance.identifiers_used["jurisdiction"] == "QA"

    def test_normalized_schema_deterministic(self) -> None:
        connector = WorldBankConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx())
        r2 = connector.fetch(_make_request(), _ctx())
        assert r1.normalized == r2.normalized


class TestWorldBankFetchFailures:
    def test_404_returns_miss(self) -> None:
        connector = WorldBankConnector(http_client=_make_client(status_code=404))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        connector = WorldBankConnector(http_client=_make_client(status_code=500, response_json={}))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        connector = WorldBankConnector(http_client=_make_client(raise_error=True))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        connector = WorldBankConnector(http_client=_make_client())
        result = connector.fetch(_make_request(entity_type=EntityType.PERSON), _ctx())
        assert result.status == EnrichmentStatus.ERROR

    def test_missing_jurisdiction_returns_error(self) -> None:
        connector = WorldBankConnector(http_client=_make_client())
        request = EnrichmentRequest(
            tenant_id="t-001", entity_type=EntityType.COMPANY, query=EnrichmentQuery()
        )
        assert connector.fetch(request, _ctx()).status == EnrichmentStatus.ERROR

    def test_empty_data_section_returns_miss(self) -> None:
        empty_resp: list[Any] = [{"page": 1}, None]
        connector = WorldBankConnector(http_client=_make_client(response_json=empty_resp))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.MISS
