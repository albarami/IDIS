"""Qatar Open Data enrichment connector (GREEN).

Provides dataset search results from Qatar's national open data portal.
Entity type: COMPANY. Query by company_name.

Rights: GREEN (government open data, production-ready).
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

QATAR_OPEN_DATA_PROVIDER_ID = "qatar_open_data"
QATAR_OPEN_DATA_BASE_URL = "https://www.data.gov.qa/api/explore/v2.1/catalog/datasets"
QATAR_OPEN_DATA_CACHE_TTL_SECONDS = 86400
QATAR_OPEN_DATA_LIMIT = 10


class QatarOpenDataConnector:
    """Qatar Open Data enrichment connector.

    Searches the Qatar national open data portal for relevant datasets.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the Qatar Open Data connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return QATAR_OPEN_DATA_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: government open data portal."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """24-hour TTL for open data catalog."""
        return CachePolicyConfig(ttl_seconds=QATAR_OPEN_DATA_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch dataset listings from Qatar Open Data.

        Args:
            request: Enrichment request with company_name in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized dataset data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "Qatar Open Data connector only supports COMPANY entity type"},
            )

        company_name = request.query.company_name
        if not company_name:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "company_name is required for Qatar Open Data lookup"},
            )

        encoded_name = urllib.parse.quote(company_name, safe="")
        url = (
            f"{QATAR_OPEN_DATA_BASE_URL}"
            f"?where=search(default,%22{encoded_name}%22)"
            f"&limit={QATAR_OPEN_DATA_LIMIT}"
        )

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except QatarOpenDataFetchError as exc:
            logger.warning("Qatar Open Data fetch failed for %s: %s", company_name, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, company_name)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            source_id=QATAR_OPEN_DATA_PROVIDER_ID,
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
        """Execute HTTP request to Qatar Open Data API.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.

        Returns:
            Parsed JSON response dict, or None if 404.

        Raises:
            QatarOpenDataFetchError: On network or HTTP errors.
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
            QatarOpenDataFetchError: On persistent failure.
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

        raise QatarOpenDataFetchError(
            f"Qatar Open Data request failed after {attempts} attempts: {last_error}"
        )

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        company_name: str,
    ) -> dict[str, Any]:
        """Normalize Qatar Open Data response to stable schema.

        Args:
            data: Raw Qatar Open Data JSON response.
            company_name: Original query term.

        Returns:
            Normalized dict with standardized fields.
        """
        raw_results = data.get("results", [])
        datasets: list[dict[str, Any]] = []

        for result in raw_results[:QATAR_OPEN_DATA_LIMIT]:
            dataset_info = result.get("dataset", result)
            if not isinstance(dataset_info, dict):
                continue
            datasets.append(
                {
                    "dataset_id": dataset_info.get("dataset_id", ""),
                    "description": dataset_info.get("description", ""),
                    "modified": dataset_info.get("modified", ""),
                    "publisher": dataset_info.get("publisher", ""),
                    "theme": dataset_info.get("theme", []),
                    "title": dataset_info.get("title", ""),
                }
            )

        return {
            "datasets": datasets,
            "query": company_name,
            "total_count": data.get("total_count", 0),
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


class QatarOpenDataFetchError(Exception):
    """Raised when a Qatar Open Data API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
