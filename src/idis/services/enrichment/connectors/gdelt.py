"""GDELT enrichment connector (GREEN).

Provides global event and news article data from the GDELT Project API v2.
Entity type: COMPANY. Query by company_name.

Rights: GREEN (public API, open data, production-ready).
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

GDELT_PROVIDER_ID = "gdelt"
GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_CACHE_TTL_SECONDS = 3600
GDELT_MAX_RECORDS = 10


class GdeltConnector:
    """GDELT enrichment connector.

    Searches the GDELT Global Knowledge Graph for company-related news articles.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the GDELT connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return GDELT_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: open public data."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """1-hour TTL for GDELT event data."""
        return CachePolicyConfig(ttl_seconds=GDELT_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch company news articles from GDELT.

        Args:
            request: Enrichment request with company_name in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized article data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "GDELT connector only supports COMPANY entity type"},
            )

        company_name = request.query.company_name
        if not company_name:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "company_name is required for GDELT lookup"},
            )

        encoded_name = urllib.parse.quote_plus(company_name)
        url = (
            f"{GDELT_BASE_URL}?query={encoded_name}"
            f"&mode=artlist&format=json&maxrecords={GDELT_MAX_RECORDS}"
        )

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except GdeltFetchError as exc:
            logger.warning("GDELT fetch failed for %s: %s", company_name, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, company_name)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            provider_id=GDELT_PROVIDER_ID,
            source_id=GDELT_PROVIDER_ID,
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
        """Execute HTTP request to GDELT API.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.

        Returns:
            Parsed JSON response dict, or None if 404.

        Raises:
            GdeltFetchError: On network or HTTP errors.
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
            GdeltFetchError: On persistent failure.
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

        raise GdeltFetchError(f"GDELT request failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        company_name: str,
    ) -> dict[str, Any]:
        """Normalize GDELT response to stable schema.

        Args:
            data: Raw GDELT JSON response.
            company_name: Original query term.

        Returns:
            Normalized dict with standardized fields.
        """
        raw_articles = data.get("articles", [])
        articles: list[dict[str, Any]] = []

        for article in raw_articles[:GDELT_MAX_RECORDS]:
            articles.append(
                {
                    "domain": article.get("domain", ""),
                    "language": article.get("language", ""),
                    "seendate": article.get("seendate", ""),
                    "socialimage": article.get("socialimage", ""),
                    "source_country": article.get("sourcecountry", ""),
                    "title": article.get("title", ""),
                    "url": article.get("url", ""),
                }
            )

        return {
            "articles": articles,
            "query": company_name,
            "total_articles": len(articles),
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


class GdeltFetchError(Exception):
    """Raised when a GDELT API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
