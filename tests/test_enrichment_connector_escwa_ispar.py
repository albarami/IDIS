"""Tests for ESCWA ISPAR / Arab Development Portal enrichment connector.

Uses httpx.MockTransport for deterministic testing with no live network calls.
The ADP (data.arabdevelopmentportal.org) provides API query support for
200k+ datasets covering 22 Arab states.
"""

from __future__ import annotations

from typing import Any

import httpx

from idis.services.enrichment.connectors.escwa_ispar import (
    ESCWA_ISPAR_PROVIDER_ID,
    EscwaIsparConnector,
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
    "total_count": 2,
    "datasets": [
        {
            "id": "ds-gdp-qat-2024",
            "title": "Qatar GDP Indicators",
            "description": "Gross domestic product data for Qatar",
            "source": "ESCWA",
            "theme": "Macroeconomy",
            "modified": "2024-06-15",
            "format": "CSV",
        },
        {
            "id": "ds-trade-qat-2024",
            "title": "Qatar Trade Statistics",
            "description": "Import/export data for Qatar",
            "source": "ESCWA",
            "theme": "Trade",
            "modified": "2024-05-01",
            "format": "CSV",
        },
    ],
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
    jurisdiction: str = "qat",
    entity_type: EntityType = EntityType.COMPANY,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-ispar-001",
        entity_type=entity_type,
        query=EnrichmentQuery(jurisdiction=jurisdiction),
    )


def _ctx() -> EnrichmentContext:
    return EnrichmentContext(timeout_seconds=5.0, max_retries=0, request_id="req-ispar-test")


class TestEscwaIsparProperties:
    def test_provider_id(self) -> None:
        assert EscwaIsparConnector().provider_id == ESCWA_ISPAR_PROVIDER_ID

    def test_rights_class_is_green(self) -> None:
        assert EscwaIsparConnector().rights_class == RightsClass.GREEN

    def test_cache_policy(self) -> None:
        policy = EscwaIsparConnector().cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0

    def test_cache_ttl_is_7_days(self) -> None:
        assert EscwaIsparConnector().cache_policy.ttl_seconds == 604800


class TestEscwaIsparFetchSuccess:
    def test_successful_fetch_returns_hit(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["jurisdiction"] == "qat"
        assert result.normalized["total_count"] == 2
        assert len(result.normalized["datasets"]) == 2

    def test_portal_field_present(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.normalized["portal"] == "arabdevelopmentportal.org"
        assert result.normalized["source_filter"] == "escwa"

    def test_dataset_fields_normalized(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        ds = result.normalized["datasets"][0]
        assert ds["id"] == "ds-gdp-qat-2024"
        assert ds["title"] == "Qatar GDP Indicators"
        assert ds["theme"] == "Macroeconomy"
        assert ds["format"] == "CSV"

    def test_provenance_populated(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.provenance is not None
        assert result.provenance.source_id == ESCWA_ISPAR_PROVIDER_ID
        assert result.provenance.rights_class == RightsClass.GREEN
        assert result.provenance.raw_ref_hash
        assert result.provenance.identifiers_used["jurisdiction"] == "qat"

    def test_normalized_schema_deterministic(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx())
        r2 = connector.fetch(_make_request(), _ctx())
        assert r1.normalized == r2.normalized

    def test_raw_hash_deterministic(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx())
        r2 = connector.fetch(_make_request(), _ctx())
        assert r1.provenance is not None
        assert r2.provenance is not None
        assert r1.provenance.raw_ref_hash == r2.provenance.raw_ref_hash

    def test_jurisdiction_lowercased(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client())
        result = connector.fetch(_make_request(jurisdiction="QAT"), _ctx())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["jurisdiction"] == "qat"


class TestEscwaIsparFetchFailures:
    def test_404_returns_miss(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client(status_code=404))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client(status_code=500, response_json={}))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client(raise_error=True))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client())
        result = connector.fetch(_make_request(entity_type=EntityType.PERSON), _ctx())
        assert result.status == EnrichmentStatus.ERROR

    def test_missing_jurisdiction_returns_error(self) -> None:
        connector = EscwaIsparConnector(http_client=_make_client())
        request = EnrichmentRequest(
            tenant_id="t-001",
            entity_type=EntityType.COMPANY,
            query=EnrichmentQuery(),
        )
        assert connector.fetch(request, _ctx()).status == EnrichmentStatus.ERROR

    def test_empty_datasets_returns_hit_with_zero(self) -> None:
        connector = EscwaIsparConnector(
            http_client=_make_client(response_json={"total_count": 0, "datasets": []})
        )
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["total_count"] == 0
        assert result.normalized["datasets"] == []


class TestEscwaIsparQueryParams:
    """Verify the connector sends correct query parameters."""

    def test_request_includes_sources_param(self) -> None:
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json=SAMPLE_RESPONSE)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        connector = EscwaIsparConnector(http_client=client)
        connector.fetch(_make_request(jurisdiction="egy"), _ctx())

        assert len(captured_requests) == 1
        url = str(captured_requests[0].url)
        assert "sources=escwa" in url
        assert "countries=egy" in url
