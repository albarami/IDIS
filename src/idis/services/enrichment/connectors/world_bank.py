"""World Bank enrichment connector (GREEN).

Provides macroeconomic indicator data from the World Bank Open Data API.
Entity type: COMPANY. Query by jurisdiction (ISO country code).

Rights: GREEN (public API under Creative Commons, production-ready).
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

WORLD_BANK_PROVIDER_ID = "world_bank"
WORLD_BANK_BASE_URL = "https://api.worldbank.org/v2/country"
WORLD_BANK_INDICATOR = "NY.GDP.MKTP.CD"
WORLD_BANK_CACHE_TTL_SECONDS = 604800
WORLD_BANK_PER_PAGE = 10


class WorldBankConnector:
    """World Bank enrichment connector.

    Fetches macroeconomic indicator data (GDP) for a given country.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the World Bank connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return WORLD_BANK_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: Creative Commons licensed public data."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """7-day TTL for macroeconomic data."""
        return CachePolicyConfig(ttl_seconds=WORLD_BANK_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch macroeconomic data from World Bank.

        Args:
            request: Enrichment request with jurisdiction in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized indicator data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "World Bank connector only supports COMPANY entity type"},
            )

        jurisdiction = request.query.jurisdiction
        if not jurisdiction:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={
                    "error": "jurisdiction (ISO country code) is required for World Bank lookup"
                },
            )

        encoded_code = urllib.parse.quote(jurisdiction, safe="")
        url = (
            f"{WORLD_BANK_BASE_URL}/{encoded_code}/indicator/{WORLD_BANK_INDICATOR}"
            f"?format=json&per_page={WORLD_BANK_PER_PAGE}"
        )

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except WorldBankFetchError as exc:
            logger.warning("World Bank fetch failed for %s: %s", jurisdiction, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, jurisdiction)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            provider_id=WORLD_BANK_PROVIDER_ID,
            source_id=WORLD_BANK_PROVIDER_ID,
            retrieved_at=datetime.now(UTC),
            rights_class=RightsClass.GREEN,
            raw_ref_hash=raw_hash,
            identifiers_used={"jurisdiction": jurisdiction},
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
        """Execute HTTP request to World Bank API.

        The World Bank API returns a JSON array of [metadata, data].
        This method converts the response into a dict.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.

        Returns:
            Dict with 'metadata' and 'data' keys, or None if no data found.

        Raises:
            WorldBankFetchError: On network or HTTP errors.
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
            Parsed response dict or None on 404 / empty data.

        Raises:
            WorldBankFetchError: On persistent failure.
        """
        last_error: Exception | None = None
        attempts = 1 + ctx.max_retries

        for attempt in range(attempts):
            try:
                response = client.get(url, headers=headers)

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                raw: Any = response.json()

                if isinstance(raw, list) and len(raw) >= 2:
                    data_section = raw[1]
                    if data_section is None or (
                        isinstance(data_section, list) and len(data_section) == 0
                    ):
                        return None
                    return {"metadata": raw[0], "data": data_section}

                return None

            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue

        raise WorldBankFetchError(
            f"World Bank request failed after {attempts} attempts: {last_error}"
        )

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        jurisdiction: str,
    ) -> dict[str, Any]:
        """Normalize World Bank response to stable schema.

        Args:
            data: Dict with 'metadata' and 'data' keys.
            jurisdiction: ISO country code.

        Returns:
            Normalized dict with standardized fields.
        """
        records = data.get("data", [])
        data_points: list[dict[str, Any]] = []

        country_name = ""
        for record in records:
            if not country_name and isinstance(record, dict):
                country_info = record.get("country", {})
                if isinstance(country_info, dict):
                    country_name = country_info.get("value", "")

            data_points.append(
                {
                    "date": record.get("date", "") if isinstance(record, dict) else "",
                    "value": record.get("value") if isinstance(record, dict) else None,
                }
            )

        return {
            "country_code": jurisdiction,
            "country_name": country_name,
            "data_points": data_points,
            "indicator": WORLD_BANK_INDICATOR,
            "total_records": len(data_points),
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


class WorldBankFetchError(Exception):
    """Raised when a World Bank API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
