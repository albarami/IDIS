"""Tests for SEC EDGAR enrichment connector.

Uses httpx.MockTransport for deterministic testing with no live network calls.

Verifies:
- Successful company filing lookup by CIK
- 404 returns MISS status
- Network errors return ERROR status
- Non-COMPANY entity type returns ERROR
- Missing CIK returns ERROR
- Response normalization produces stable schema
- Provenance metadata populated correctly
- Rate limiting (429) retries and eventually errors
"""

from __future__ import annotations

from typing import Any

import httpx

from idis.services.enrichment.connectors.edgar import (
    EDGAR_PROVIDER_ID,
    EdgarConnector,
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

SAMPLE_CIK = "0000320193"  # Apple Inc.

SAMPLE_EDGAR_RESPONSE: dict[str, Any] = {
    "cik": "320193",
    "entityType": "operating",
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "name": "Apple Inc.",
    "tickers": ["AAPL"],
    "exchanges": ["Nasdaq"],
    "stateOfIncorporation": "CA",
    "fiscalYearEnd": "0930",
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-24-000001"],
            "filingDate": ["2024-01-15", "2024-01-10", "2023-12-20"],
            "form": ["10-K", "10-Q", "8-K"],
            "primaryDocument": ["aapl-20240115.htm"],
        },
        "files": [],
    },
}


def _make_transport(
    status_code: int = 200,
    response_json: dict[str, Any] | None = None,
    raise_error: bool = False,
) -> httpx.MockTransport:
    """Create a mock transport for testing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if raise_error:
            raise httpx.ConnectError("Connection refused")
        body = response_json if response_json is not None else SAMPLE_EDGAR_RESPONSE
        return httpx.Response(
            status_code=status_code,
            json=body,
            headers={"content-type": "application/json"},
        )

    return httpx.MockTransport(handler)


def _make_client(
    status_code: int = 200,
    response_json: dict[str, Any] | None = None,
    raise_error: bool = False,
) -> httpx.Client:
    transport = _make_transport(status_code, response_json, raise_error)
    return httpx.Client(transport=transport)


def _make_request(
    cik: str = SAMPLE_CIK,
    entity_type: EntityType = EntityType.COMPANY,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-edgar-001",
        entity_type=entity_type,
        query=EnrichmentQuery(cik=cik),
    )


def _make_context() -> EnrichmentContext:
    return EnrichmentContext(
        timeout_seconds=5.0,
        max_retries=0,
        request_id="req-edgar-test",
    )


class TestEdgarConnectorProperties:
    """Verify connector metadata properties."""

    def test_provider_id(self) -> None:
        connector = EdgarConnector()
        assert connector.provider_id == EDGAR_PROVIDER_ID

    def test_rights_class_is_green(self) -> None:
        connector = EdgarConnector()
        assert connector.rights_class == RightsClass.GREEN

    def test_cache_policy_has_ttl(self) -> None:
        connector = EdgarConnector()
        policy = connector.cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0
        assert policy.no_store is False


class TestEdgarFetchSuccess:
    """Successful EDGAR fetch with normalized response."""

    def test_successful_fetch_returns_hit(self) -> None:
        client = _make_client()
        connector = EdgarConnector(http_client=client)
        result = connector.fetch(_make_request(), _make_context())

        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["registrant_name"] == "Apple Inc."
        assert result.normalized["cik"] == SAMPLE_CIK
        assert result.normalized["has_filings"] is True
        assert result.normalized["total_recent_filings"] == 3
        assert result.normalized["latest_filing_date"] == "2024-01-15"

    def test_provenance_populated(self) -> None:
        client = _make_client()
        connector = EdgarConnector(http_client=client)
        result = connector.fetch(_make_request(), _make_context())

        assert result.provenance is not None
        assert result.provenance.provider_id == EDGAR_PROVIDER_ID
        assert result.provenance.source_id == EDGAR_PROVIDER_ID
        assert result.provenance.rights_class == RightsClass.GREEN
        assert result.provenance.raw_ref_hash
        assert result.provenance.identifiers_used["cik"] == SAMPLE_CIK

    def test_normalized_schema_fields(self) -> None:
        client = _make_client()
        connector = EdgarConnector(http_client=client)
        result = connector.fetch(_make_request(), _make_context())

        expected_fields = {
            "cik",
            "registrant_name",
            "entity_type_sec",
            "sic",
            "sic_description",
            "state_of_incorporation",
            "fiscal_year_end",
            "has_filings",
            "total_recent_filings",
            "latest_filing_date",
            "form_type_counts",
            "tickers",
            "exchanges",
        }
        assert set(result.normalized.keys()) == expected_fields

    def test_form_type_counts(self) -> None:
        client = _make_client()
        connector = EdgarConnector(http_client=client)
        result = connector.fetch(_make_request(), _make_context())

        counts = result.normalized["form_type_counts"]
        assert counts["10-K"] == 1
        assert counts["10-Q"] == 1
        assert counts["8-K"] == 1


class TestEdgarFetchFailures:
    """Error cases: 404, network errors, invalid inputs."""

    def test_404_returns_miss(self) -> None:
        client = _make_client(status_code=404)
        connector = EdgarConnector(http_client=client)
        result = connector.fetch(_make_request(), _make_context())

        assert result.status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        client = _make_client(status_code=500, response_json={"error": "internal"})
        connector = EdgarConnector(http_client=client)
        result = connector.fetch(_make_request(), _make_context())

        assert result.status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        client = _make_client(raise_error=True)
        connector = EdgarConnector(http_client=client)
        result = connector.fetch(_make_request(), _make_context())

        assert result.status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        client = _make_client()
        connector = EdgarConnector(http_client=client)
        request = _make_request(entity_type=EntityType.PERSON)
        result = connector.fetch(request, _make_context())

        assert result.status == EnrichmentStatus.ERROR
        assert "COMPANY" in result.normalized.get("error", "")

    def test_missing_cik_returns_error(self) -> None:
        client = _make_client()
        connector = EdgarConnector(http_client=client)
        request = EnrichmentRequest(
            tenant_id="tenant-001",
            entity_type=EntityType.COMPANY,
            query=EnrichmentQuery(company_name="Apple"),
        )
        result = connector.fetch(request, _make_context())

        assert result.status == EnrichmentStatus.ERROR
        assert "CIK" in result.normalized.get("error", "")


class TestEdgarCikNormalization:
    """CIK is zero-padded to 10 digits for the SEC API."""

    def test_short_cik_is_padded(self) -> None:
        client = _make_client()
        connector = EdgarConnector(http_client=client)
        request = _make_request(cik="320193")
        result = connector.fetch(request, _make_context())

        assert result.status == EnrichmentStatus.HIT
        assert result.provenance is not None
        assert result.provenance.identifiers_used["cik"] == "0000320193"


class TestEdgarRateLimitRetry:
    """429 rate limiting with retry logic."""

    def test_persistent_429_returns_error(self) -> None:
        """Persistent 429 after max retries returns ERROR."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status_code=429)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        connector = EdgarConnector(http_client=client)

        ctx = EnrichmentContext(
            timeout_seconds=5.0,
            max_retries=2,
            request_id="req-429-test",
        )
        result = connector.fetch(_make_request(), ctx)

        assert result.status == EnrichmentStatus.ERROR
        assert call_count == 3  # 1 initial + 2 retries


class TestEdgarRawHashDeterminism:
    """Raw response hash must be deterministic."""

    def test_same_response_produces_same_hash(self) -> None:
        client = _make_client()
        connector = EdgarConnector(http_client=client)

        result1 = connector.fetch(_make_request(), _make_context())
        result2 = connector.fetch(_make_request(), _make_context())

        assert result1.provenance is not None
        assert result2.provenance is not None
        assert result1.provenance.raw_ref_hash == result2.provenance.raw_ref_hash
