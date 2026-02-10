"""Tests for FRED enrichment connector.

Uses httpx.MockTransport for deterministic testing with no live network calls.
Verifies BYOL credential handling.
"""

from __future__ import annotations

from typing import Any

import httpx

from idis.services.enrichment.connectors.fred import (
    FRED_PROVIDER_ID,
    FredConnector,
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
    "observations": [
        {"date": "2024-01-01", "value": "5.25"},
        {"date": "2023-12-01", "value": "5.33"},
    ],
    "count": 2,
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
    ticker: str = "FEDFUNDS",
    entity_type: EntityType = EntityType.COMPANY,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-fred-001",
        entity_type=entity_type,
        query=EnrichmentQuery(ticker=ticker),
    )


def _ctx_with_key() -> EnrichmentContext:
    return EnrichmentContext(
        timeout_seconds=5.0,
        max_retries=0,
        request_id="req-fred-test",
        byol_credentials={"api_key": "test-fred-key-12345"},
    )


def _ctx_no_key() -> EnrichmentContext:
    return EnrichmentContext(timeout_seconds=5.0, max_retries=0, request_id="req-fred-test")


class TestFredProperties:
    def test_provider_id(self) -> None:
        assert FredConnector().provider_id == FRED_PROVIDER_ID

    def test_rights_class_is_green(self) -> None:
        assert FredConnector().rights_class == RightsClass.GREEN

    def test_cache_policy(self) -> None:
        policy = FredConnector().cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0


class TestFredFetchSuccess:
    def test_successful_fetch_returns_hit(self) -> None:
        connector = FredConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx_with_key())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["series_id"] == "FEDFUNDS"
        assert result.normalized["count"] == 2
        assert len(result.normalized["observations"]) == 2

    def test_provenance_populated(self) -> None:
        connector = FredConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx_with_key())
        assert result.provenance is not None
        assert result.provenance.provider_id == FRED_PROVIDER_ID
        assert result.provenance.source_id == FRED_PROVIDER_ID
        assert result.provenance.identifiers_used["series_id"] == "FEDFUNDS"

    def test_normalized_schema_deterministic(self) -> None:
        connector = FredConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx_with_key())
        r2 = connector.fetch(_make_request(), _ctx_with_key())
        assert r1.normalized == r2.normalized


class TestFredFetchFailures:
    def test_404_returns_miss(self) -> None:
        connector = FredConnector(http_client=_make_client(status_code=404))
        assert connector.fetch(_make_request(), _ctx_with_key()).status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        connector = FredConnector(http_client=_make_client(status_code=500, response_json={}))
        assert connector.fetch(_make_request(), _ctx_with_key()).status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        connector = FredConnector(http_client=_make_client(raise_error=True))
        assert connector.fetch(_make_request(), _ctx_with_key()).status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        connector = FredConnector(http_client=_make_client())
        result = connector.fetch(_make_request(entity_type=EntityType.PERSON), _ctx_with_key())
        assert result.status == EnrichmentStatus.ERROR

    def test_missing_ticker_returns_error(self) -> None:
        connector = FredConnector(http_client=_make_client())
        request = EnrichmentRequest(
            tenant_id="t-001", entity_type=EntityType.COMPANY, query=EnrichmentQuery()
        )
        assert connector.fetch(request, _ctx_with_key()).status == EnrichmentStatus.ERROR

    def test_empty_observations_returns_miss(self) -> None:
        connector = FredConnector(http_client=_make_client(response_json={"observations": []}))
        assert connector.fetch(_make_request(), _ctx_with_key()).status == EnrichmentStatus.MISS


class TestFredByol:
    def test_missing_api_key_returns_blocked(self) -> None:
        connector = FredConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx_no_key())
        assert result.status == EnrichmentStatus.BLOCKED_MISSING_BYOL
        assert "API key" in result.normalized.get("error", "")

    def test_empty_credentials_returns_blocked(self) -> None:
        ctx = EnrichmentContext(
            timeout_seconds=5.0,
            max_retries=0,
            request_id="req-fred-test",
            byol_credentials={},
        )
        connector = FredConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), ctx)
        assert result.status == EnrichmentStatus.BLOCKED_MISSING_BYOL
