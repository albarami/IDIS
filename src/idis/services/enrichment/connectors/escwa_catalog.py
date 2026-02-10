"""ESCWA Data Catalog enrichment connector (GREEN).

Provides dataset search results from the UN ESCWA open data catalog (CKAN-based).
Entity type: COMPANY. Query by company_name.

Rights: GREEN (UN open data, production-ready).
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

ESCWA_CATALOG_PROVIDER_ID = "escwa_catalog"
ESCWA_CATALOG_BASE_URL = "https://data.unescwa.org/api/3/action/package_search"
ESCWA_CATALOG_CACHE_TTL_SECONDS = 604800
ESCWA_CATALOG_ROWS = 10


class EscwaCatalogConnector:
    """ESCWA Data Catalog enrichment connector.

    Searches the UN ESCWA CKAN-based open data catalog.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the ESCWA Catalog connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return ESCWA_CATALOG_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: UN open data."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """7-day TTL for catalog data."""
        return CachePolicyConfig(ttl_seconds=ESCWA_CATALOG_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch dataset listings from ESCWA Data Catalog.

        Args:
            request: Enrichment request with company_name in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized dataset data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "ESCWA Catalog connector only supports COMPANY entity type"},
            )

        company_name = request.query.company_name
        if not company_name:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "company_name is required for ESCWA Catalog lookup"},
            )

        encoded_name = urllib.parse.quote_plus(company_name)
        url = f"{ESCWA_CATALOG_BASE_URL}?q={encoded_name}&rows={ESCWA_CATALOG_ROWS}"

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except EscwaCatalogFetchError as exc:
            logger.warning("ESCWA Catalog fetch failed for %s: %s", company_name, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, company_name)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            provider_id=ESCWA_CATALOG_PROVIDER_ID,
            source_id=ESCWA_CATALOG_PROVIDER_ID,
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
        """Execute HTTP request to ESCWA CKAN API.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.

        Returns:
            Parsed JSON response dict, or None if 404 or not success.

        Raises:
            EscwaCatalogFetchError: On network or HTTP errors.
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
            Parsed response or None on 404 / CKAN error.

        Raises:
            EscwaCatalogFetchError: On persistent failure.
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

                if not data.get("success", False):
                    return None

                result: dict[str, Any] = data.get("result", {})
                return result

            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue

        raise EscwaCatalogFetchError(
            f"ESCWA Catalog request failed after {attempts} attempts: {last_error}"
        )

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        company_name: str,
    ) -> dict[str, Any]:
        """Normalize ESCWA CKAN response to stable schema.

        Args:
            data: CKAN result dict (the 'result' field from the response).
            company_name: Original query term.

        Returns:
            Normalized dict with standardized fields.
        """
        raw_results = data.get("results", [])
        datasets: list[dict[str, Any]] = []

        for result in raw_results[:ESCWA_CATALOG_ROWS]:
            datasets.append(
                {
                    "author": result.get("author", ""),
                    "metadata_created": result.get("metadata_created", ""),
                    "metadata_modified": result.get("metadata_modified", ""),
                    "name": result.get("name", ""),
                    "notes": result.get("notes", ""),
                    "organization": result.get("organization", {}).get("title", "")
                    if isinstance(result.get("organization"), dict)
                    else "",
                    "title": result.get("title", ""),
                }
            )

        return {
            "count": data.get("count", 0),
            "datasets": datasets,
            "query": company_name,
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


class EscwaCatalogFetchError(Exception):
    """Raised when an ESCWA Data Catalog API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
