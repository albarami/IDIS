"""Google News RSS enrichment connector (YELLOW).

Provides recent news articles via Google News RSS feed (XML parsing).
Entity type: COMPANY. Query by company_name.

Rights: YELLOW (RSS feed with attribution requirements).
Spec: IDIS_Data_Architecture_v3_1.md §1 (Entity & Company Intelligence)
"""

from __future__ import annotations

import hashlib
import logging
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

import httpx

from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentProvenance,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS_PROVIDER_ID = "google_news_rss"
GOOGLE_NEWS_RSS_BASE_URL = "https://news.google.com/rss/search"
GOOGLE_NEWS_RSS_CACHE_TTL_SECONDS = 1800
GOOGLE_NEWS_RSS_MAX_ITEMS = 10


class GoogleNewsRssConnector:
    """Google News RSS enrichment connector.

    Fetches recent news articles via Google News RSS feed and parses XML.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the Google News RSS connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return GOOGLE_NEWS_RSS_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """YELLOW: RSS feed with attribution requirements."""
        return RightsClass.YELLOW

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """30-minute TTL for news data."""
        return CachePolicyConfig(ttl_seconds=GOOGLE_NEWS_RSS_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch news articles from Google News RSS.

        Args:
            request: Enrichment request with company_name in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized news data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "Google News RSS connector only supports COMPANY entity type"},
            )

        company_name = request.query.company_name
        if not company_name:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "company_name is required for Google News RSS lookup"},
            )

        encoded_name = urllib.parse.quote_plus(company_name)
        url = f"{GOOGLE_NEWS_RSS_BASE_URL}?q={encoded_name}&hl=en-US&gl=US&ceid=US:en"

        try:
            raw_xml = self._make_request(url=url, ctx=ctx)
        except GoogleNewsRssFetchError as exc:
            logger.warning("Google News RSS fetch failed for %s: %s", company_name, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if raw_xml is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        try:
            items = self._parse_rss(raw_xml)
        except GoogleNewsRssParseError as exc:
            logger.warning("Google News RSS parse failed for %s: %s", company_name, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        normalized = self._normalize_response(items, company_name)
        raw_hash = self._compute_raw_hash(raw_xml)

        provenance = EnrichmentProvenance(
            source_id=GOOGLE_NEWS_RSS_PROVIDER_ID,
            retrieved_at=datetime.now(UTC),
            rights_class=RightsClass.YELLOW,
            raw_ref_hash=raw_hash,
            identifiers_used={"company_name": company_name},
        )

        return EnrichmentResult(
            status=EnrichmentStatus.HIT,
            normalized=normalized,
            provenance=provenance,
            raw=None,
        )

    def _make_request(
        self,
        *,
        url: str,
        ctx: EnrichmentContext,
    ) -> str | None:
        """Execute HTTP request to Google News RSS feed.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.

        Returns:
            Raw XML text, or None if 404.

        Raises:
            GoogleNewsRssFetchError: On network or HTTP errors.
        """
        headers = {"Accept": "application/rss+xml, application/xml, text/xml"}

        client = self._http_client
        should_close = False
        if client is None:
            client = httpx.Client(timeout=ctx.timeout_seconds, headers=headers)
            should_close = True
        try:
            return self._execute_with_retries(
                client=client,
                url=url,
                headers=headers if self._http_client is not None else {},
                ctx=ctx,
            )
        finally:
            if should_close:
                client.close()

    def _execute_with_retries(
        self,
        *,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        ctx: EnrichmentContext,
    ) -> str | None:
        """Execute request with retry logic, returning raw text.

        Args:
            client: HTTP client to use.
            url: Request URL.
            headers: Additional headers.
            ctx: Context with retry settings.

        Returns:
            Raw response text or None on 404.

        Raises:
            GoogleNewsRssFetchError: On persistent failure.
        """
        last_error: Exception | None = None
        attempts = 1 + ctx.max_retries

        for attempt in range(attempts):
            try:
                response = client.get(url, headers=headers)

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                return response.text

            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue

        raise GoogleNewsRssFetchError(
            f"Google News RSS request failed after {attempts} attempts: {last_error}"
        )

    @staticmethod
    def _parse_rss(raw_xml: str) -> list[dict[str, str]]:
        """Parse RSS XML into a list of item dicts.

        Args:
            raw_xml: Raw RSS XML text.

        Returns:
            List of dicts with title, link, pubDate, source fields.

        Raises:
            GoogleNewsRssParseError: If XML parsing fails.
        """
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError as exc:
            raise GoogleNewsRssParseError(f"Failed to parse RSS XML: {exc}") from exc

        channel = root.find("channel")
        if channel is None:
            return []

        items: list[dict[str, str]] = []
        for item_elem in channel.findall("item")[:GOOGLE_NEWS_RSS_MAX_ITEMS]:
            title_elem = item_elem.find("title")
            link_elem = item_elem.find("link")
            pub_date_elem = item_elem.find("pubDate")
            source_elem = item_elem.find("source")

            items.append(
                {
                    "link": link_elem.text if link_elem is not None and link_elem.text else "",
                    "published": pub_date_elem.text
                    if pub_date_elem is not None and pub_date_elem.text
                    else "",
                    "source": source_elem.text
                    if source_elem is not None and source_elem.text
                    else "",
                    "title": title_elem.text if title_elem is not None and title_elem.text else "",
                }
            )

        return items

    @staticmethod
    def _normalize_response(
        items: list[dict[str, str]],
        company_name: str,
    ) -> dict[str, Any]:
        """Normalize parsed RSS items to stable schema.

        Args:
            items: Parsed RSS item dicts.
            company_name: Original query term.

        Returns:
            Normalized dict with standardized fields.
        """
        return {
            "items": items,
            "query": company_name,
            "total_items": len(items),
        }

    @staticmethod
    def _compute_raw_hash(raw_xml: str) -> str:
        """Compute SHA256 hash of raw XML response for provenance.

        Args:
            raw_xml: Raw XML text.

        Returns:
            SHA256 hex digest.
        """
        return hashlib.sha256(raw_xml.encode("utf-8")).hexdigest()


class GoogleNewsRssFetchError(Exception):
    """Raised when a Google News RSS request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class GoogleNewsRssParseError(Exception):
    """Raised when RSS XML parsing fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
