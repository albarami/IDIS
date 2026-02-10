"""HackerNews enrichment connector (GREEN).

Provides company mentions and stories from the HackerNews Algolia search API.
Entity type: COMPANY. Query by company_name.

Rights: GREEN (public API, no authentication required).
Spec: IDIS_Data_Architecture_v3_1.md §1 (Entity & Company Intelligence)
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.parse
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

HACKERNEWS_PROVIDER_ID = "hackernews"
HACKERNEWS_BASE_URL = "https://hn.algolia.com/api/v1/search"
HACKERNEWS_CACHE_TTL_SECONDS = 1800
HACKERNEWS_MAX_HITS = 10


class HackerNewsConnector:
    """HackerNews enrichment connector.

    Searches HackerNews stories via the Algolia search API for company mentions.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the HackerNews connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return HACKERNEWS_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: public API, no restrictions."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """30-minute TTL for news data."""
        return CachePolicyConfig(ttl_seconds=HACKERNEWS_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch company mentions from HackerNews.

        Args:
            request: Enrichment request with company_name in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized story data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "HackerNews connector only supports COMPANY entity type"},
            )

        company_name = request.query.company_name
        if not company_name:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "company_name is required for HackerNews lookup"},
            )

        encoded_name = urllib.parse.quote_plus(company_name)
        url = (
            f"{HACKERNEWS_BASE_URL}?query={encoded_name}"
            f"&tags=story&hitsPerPage={HACKERNEWS_MAX_HITS}"
        )

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except HackerNewsFetchError as exc:
            logger.warning("HackerNews fetch failed for %s: %s", company_name, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, company_name)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            provider_id=HACKERNEWS_PROVIDER_ID,
            source_id=HACKERNEWS_PROVIDER_ID,
            retrieved_at=datetime.now(UTC),
            rights_class=RightsClass.GREEN,
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
    ) -> dict[str, Any] | None:
        """Execute HTTP request to HackerNews Algolia API.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.

        Returns:
            Parsed JSON response dict, or None if 404.

        Raises:
            HackerNewsFetchError: On network or HTTP errors.
        """
        headers = {"Accept": "application/json"}

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
    ) -> dict[str, Any] | None:
        """Execute request with retry logic.

        Args:
            client: HTTP client to use.
            url: Request URL.
            headers: Additional headers.
            ctx: Context with retry settings.

        Returns:
            Parsed response or None on 404.

        Raises:
            HackerNewsFetchError: On persistent failure.
        """
        last_error: Exception | None = None
        attempts = 1 + ctx.max_retries

        for attempt in range(attempts):
            try:
                response = client.get(url, headers=headers)

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                data: dict[str, Any] = response.json()
                return data

            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue

        raise HackerNewsFetchError(
            f"HackerNews request failed after {attempts} attempts: {last_error}"
        )

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        company_name: str,
    ) -> dict[str, Any]:
        """Normalize HackerNews response to stable schema.

        Args:
            data: Raw HackerNews Algolia JSON.
            company_name: Original query term.

        Returns:
            Normalized dict with standardized fields.
        """
        hits = data.get("hits", [])
        stories = []
        for hit in hits[:HACKERNEWS_MAX_HITS]:
            stories.append(
                {
                    "author": hit.get("author", ""),
                    "created_at": hit.get("created_at", ""),
                    "num_comments": hit.get("num_comments", 0),
                    "objectID": hit.get("objectID", ""),
                    "points": hit.get("points", 0),
                    "title": hit.get("title", ""),
                    "url": hit.get("url", ""),
                }
            )

        return {
            "query": company_name,
            "stories": stories,
            "total_hits": data.get("nbHits", 0),
        }

    @staticmethod
    def _compute_raw_hash(data: dict[str, Any]) -> str:
        """Compute SHA256 hash of raw response for provenance.

        Args:
            data: Raw response dict.

        Returns:
            SHA256 hex digest.
        """
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class HackerNewsFetchError(Exception):
    """Raised when a HackerNews API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
