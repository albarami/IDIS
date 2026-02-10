"""Tests for Google News RSS enrichment connector.

Uses httpx.MockTransport for deterministic testing with no live network calls.
Verifies XML/RSS parsing, provenance, and deterministic normalization.
"""

from __future__ import annotations

import httpx

from idis.services.enrichment.connectors.google_news_rss import (
    GOOGLE_NEWS_RSS_PROVIDER_ID,
    GoogleNewsRssConnector,
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

SAMPLE_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Acme Corp - Google News</title>
    <item>
      <title>Acme Corp launches new product</title>
      <link>https://example.com/acme-launch</link>
      <pubDate>Mon, 15 Jan 2024 12:00:00 GMT</pubDate>
      <source url="https://example.com">Example News</source>
    </item>
    <item>
      <title>Acme Corp reports Q4 earnings</title>
      <link>https://example.com/acme-q4</link>
      <pubDate>Sun, 14 Jan 2024 10:00:00 GMT</pubDate>
      <source url="https://biz.com">Biz Journal</source>
    </item>
  </channel>
</rss>
"""


def _make_client(
    status_code: int = 200,
    response_text: str | None = None,
    raise_error: bool = False,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_error:
            raise httpx.ConnectError("Connection refused")
        text = response_text if response_text is not None else SAMPLE_RSS_XML
        return httpx.Response(
            status_code=status_code,
            text=text,
            headers={"content-type": "application/rss+xml"},
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def _make_request(
    company_name: str = "Acme Corp",
    entity_type: EntityType = EntityType.COMPANY,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-gnr-001",
        entity_type=entity_type,
        query=EnrichmentQuery(company_name=company_name),
    )


def _ctx() -> EnrichmentContext:
    return EnrichmentContext(timeout_seconds=5.0, max_retries=0, request_id="req-gnr-test")


class TestGoogleNewsRssProperties:
    def test_provider_id(self) -> None:
        assert GoogleNewsRssConnector().provider_id == GOOGLE_NEWS_RSS_PROVIDER_ID

    def test_rights_class_is_yellow(self) -> None:
        assert GoogleNewsRssConnector().rights_class == RightsClass.YELLOW

    def test_cache_policy(self) -> None:
        policy = GoogleNewsRssConnector().cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0


class TestGoogleNewsRssFetchSuccess:
    def test_successful_fetch_returns_hit(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["total_items"] == 2
        assert result.normalized["query"] == "Acme Corp"

    def test_items_parsed_from_xml(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        items = result.normalized["items"]
        assert items[0]["title"] == "Acme Corp launches new product"
        assert items[0]["link"] == "https://example.com/acme-launch"
        assert items[0]["source"] == "Example News"
        assert "2024" in items[0]["published"]

    def test_provenance_populated(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx())
        assert result.provenance is not None
        assert result.provenance.provider_id == GOOGLE_NEWS_RSS_PROVIDER_ID
        assert result.provenance.source_id == GOOGLE_NEWS_RSS_PROVIDER_ID
        assert result.provenance.rights_class == RightsClass.YELLOW
        assert result.provenance.raw_ref_hash
        assert result.provenance.identifiers_used["company_name"] == "Acme Corp"

    def test_normalized_schema_deterministic(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx())
        r2 = connector.fetch(_make_request(), _ctx())
        assert r1.normalized == r2.normalized

    def test_raw_hash_deterministic(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx())
        r2 = connector.fetch(_make_request(), _ctx())
        assert r1.provenance is not None
        assert r2.provenance is not None
        assert r1.provenance.raw_ref_hash == r2.provenance.raw_ref_hash


class TestGoogleNewsRssFetchFailures:
    def test_404_returns_miss(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client(status_code=404))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        connector = GoogleNewsRssConnector(
            http_client=_make_client(status_code=500, response_text="error")
        )
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client(raise_error=True))
        assert connector.fetch(_make_request(), _ctx()).status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client())
        result = connector.fetch(_make_request(entity_type=EntityType.PERSON), _ctx())
        assert result.status == EnrichmentStatus.ERROR

    def test_missing_company_name_returns_error(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client())
        request = EnrichmentRequest(
            tenant_id="t-001", entity_type=EntityType.COMPANY, query=EnrichmentQuery()
        )
        assert connector.fetch(request, _ctx()).status == EnrichmentStatus.ERROR

    def test_invalid_xml_returns_error(self) -> None:
        connector = GoogleNewsRssConnector(http_client=_make_client(response_text="<not valid xml"))
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.ERROR

    def test_empty_channel_returns_hit_with_zero_items(self) -> None:
        xml = '<?xml version="1.0"?><rss><channel></channel></rss>'
        connector = GoogleNewsRssConnector(http_client=_make_client(response_text=xml))
        result = connector.fetch(_make_request(), _ctx())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["total_items"] == 0
