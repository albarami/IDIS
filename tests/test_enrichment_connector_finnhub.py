"""Tests for Finnhub enrichment connector (RED, BYOL required).

Uses httpx.MockTransport for deterministic testing with no live network calls.
Verifies BYOL credential handling and RED rights class.
"""

from __future__ import annotations

from typing import Any

import httpx

from idis.services.enrichment.connectors.finnhub import (
    FINNHUB_PROVIDER_ID,
    FinnhubConnector,
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
    "country": "US",
    "currency": "USD",
    "exchange": "NASDAQ",
    "finnhubIndustry": "Technology",
    "ipo": "1980-12-12",
    "logo": "https://finnhub.io/api/logo?symbol=AAPL",
    "marketCapitalization": 3000000,
    "name": "Apple Inc",
    "shareOutstanding": 15000,
    "ticker": "AAPL",
    "weburl": "https://www.apple.com/",
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
    ticker: str = "AAPL",
    entity_type: EntityType = EntityType.COMPANY,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-fh-001",
        entity_type=entity_type,
        query=EnrichmentQuery(ticker=ticker),
    )


def _ctx_with_key() -> EnrichmentContext:
    return EnrichmentContext(
        timeout_seconds=5.0,
        max_retries=0,
        request_id="req-fh-test",
        byol_credentials={"api_key": "test-finnhub-key-12345"},
    )


def _ctx_no_key() -> EnrichmentContext:
    return EnrichmentContext(timeout_seconds=5.0, max_retries=0, request_id="req-fh-test")


class TestFinnhubProperties:
    def test_provider_id(self) -> None:
        assert FinnhubConnector().provider_id == FINNHUB_PROVIDER_ID

    def test_rights_class_is_red(self) -> None:
        assert FinnhubConnector().rights_class == RightsClass.RED

    def test_cache_policy(self) -> None:
        policy = FinnhubConnector().cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0


class TestFinnhubFetchSuccess:
    def test_successful_fetch_returns_hit(self) -> None:
        connector = FinnhubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx_with_key())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["name"] == "Apple Inc"
        assert result.normalized["ticker"] == "AAPL"
        assert result.normalized["exchange"] == "NASDAQ"

    def test_provenance_populated(self) -> None:
        connector = FinnhubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx_with_key())
        assert result.provenance is not None
        assert result.provenance.source_id == FINNHUB_PROVIDER_ID
        assert result.provenance.rights_class == RightsClass.RED
        assert result.provenance.identifiers_used["ticker"] == "AAPL"

    def test_normalized_schema_deterministic(self) -> None:
        connector = FinnhubConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx_with_key())
        r2 = connector.fetch(_make_request(), _ctx_with_key())
        assert r1.normalized == r2.normalized


class TestFinnhubFetchFailures:
    def test_404_returns_miss(self) -> None:
        connector = FinnhubConnector(http_client=_make_client(status_code=404))
        assert connector.fetch(_make_request(), _ctx_with_key()).status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        connector = FinnhubConnector(http_client=_make_client(status_code=500, response_json={}))
        assert connector.fetch(_make_request(), _ctx_with_key()).status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        connector = FinnhubConnector(http_client=_make_client(raise_error=True))
        assert connector.fetch(_make_request(), _ctx_with_key()).status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        connector = FinnhubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(entity_type=EntityType.PERSON), _ctx_with_key())
        assert result.status == EnrichmentStatus.ERROR

    def test_missing_ticker_returns_error(self) -> None:
        connector = FinnhubConnector(http_client=_make_client())
        request = EnrichmentRequest(
            tenant_id="t-001", entity_type=EntityType.COMPANY, query=EnrichmentQuery()
        )
        assert connector.fetch(request, _ctx_with_key()).status == EnrichmentStatus.ERROR

    def test_empty_profile_returns_miss(self) -> None:
        connector = FinnhubConnector(http_client=_make_client(response_json={}))
        assert connector.fetch(_make_request(), _ctx_with_key()).status == EnrichmentStatus.MISS


class TestFinnhubByol:
    def test_missing_api_key_returns_error(self) -> None:
        connector = FinnhubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx_no_key())
        assert result.status == EnrichmentStatus.ERROR
        assert "API key" in result.normalized.get("error", "")

    def test_empty_credentials_returns_error(self) -> None:
        ctx = EnrichmentContext(
            timeout_seconds=5.0,
            max_retries=0,
            request_id="req-fh-test",
            byol_credentials={},
        )
        connector = FinnhubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), ctx)
        assert result.status == EnrichmentStatus.ERROR


class TestFinnhubRedRights:
    """Verify RED rights class is correctly declared for service-level gating."""

    def test_connector_declares_red(self) -> None:
        assert FinnhubConnector().rights_class == RightsClass.RED

    def test_rights_class_value(self) -> None:
        assert FinnhubConnector().rights_class.value == "RED"
