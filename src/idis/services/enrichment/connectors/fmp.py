"""Financial Modeling Prep (FMP) enrichment connector (RED, BYOL required).

Provides company financial profile data from the FMP API.
Entity type: COMPANY. Query by ticker.

Rights: RED (commercial API, DEV only; MUST be blocked in PROD by rights gate).
Requires BYOL API key for authentication.

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

FMP_PROVIDER_ID = "fmp"
FMP_BASE_URL = "https://financialmodelingprep.com/api/v3/profile"
FMP_CACHE_TTL_SECONDS = 300
FMP_CREDENTIAL_KEY = "api_key"


class FmpConnector:
    """Financial Modeling Prep enrichment connector.

    Fetches company financial profile from the FMP API.
    RED rights class: blocked in PROD without BYOL credentials.
    Requires a BYOL API key via ctx.byol_credentials.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the FMP connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return FMP_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """RED: commercial API, DEV only without BYOL."""
        return RightsClass.RED

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """5-minute TTL for financial profile data."""
        return CachePolicyConfig(ttl_seconds=FMP_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch company financial profile from FMP.

        Args:
            request: Enrichment request with ticker in query.
            ctx: Execution context with timeouts and BYOL credentials.

        Returns:
            EnrichmentResult with normalized profile data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "FMP connector only supports COMPANY entity type"},
            )

        ticker = request.query.ticker
        if not ticker:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "ticker is required for FMP lookup"},
            )

        api_key = (ctx.byol_credentials or {}).get(FMP_CREDENTIAL_KEY)
        if not api_key:
            return EnrichmentResult(
                status=EnrichmentStatus.BLOCKED_MISSING_BYOL,
                normalized={"error": "API key not found in BYOL credentials"},
            )

        encoded_ticker = urllib.parse.quote(ticker, safe="")
        url = f"{FMP_BASE_URL}/{encoded_ticker}?apikey={api_key}"

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except FmpFetchError as exc:
            logger.warning("FMP fetch failed for %s: %s", ticker, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, ticker)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            provider_id=FMP_PROVIDER_ID,
            source_id=FMP_PROVIDER_ID,
            retrieved_at=datetime.now(UTC),
            rights_class=RightsClass.RED,
            raw_ref_hash=raw_hash,
            identifiers_used={"ticker": ticker},
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
        """Execute HTTP request to FMP API.

        The FMP profile endpoint returns a JSON array. This method
        extracts the first element.

        Args:
            url: Full URL to fetch (includes API key as query param).
            ctx: Execution context with timeout settings.

        Returns:
            Parsed JSON response dict (first profile), or None if 404 / empty.

        Raises:
            FmpFetchError: On network or HTTP errors.
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
            First profile dict or None on 404 / empty.

        Raises:
            FmpFetchError: On persistent failure.
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

                if isinstance(raw, list) and len(raw) > 0:
                    first = raw[0]
                    if isinstance(first, dict) and first.get("companyName"):
                        return first
                    return None

                return None

            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue

        raise FmpFetchError(f"FMP request failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        ticker: str,
    ) -> dict[str, Any]:
        """Normalize FMP response to stable schema.

        Args:
            data: Raw FMP profile JSON (first element of array).
            ticker: Stock ticker symbol.

        Returns:
            Normalized dict with standardized fields.
        """
        return {
            "ceo": data.get("ceo", ""),
            "city": data.get("city", ""),
            "company_name": data.get("companyName", ""),
            "country": data.get("country", ""),
            "currency": data.get("currency", ""),
            "exchange": data.get("exchange", ""),
            "full_time_employees": data.get("fullTimeEmployees", ""),
            "industry": data.get("industry", ""),
            "market_cap": data.get("mktCap", 0),
            "price": data.get("price", 0),
            "sector": data.get("sector", ""),
            "ticker": data.get("symbol", ticker),
            "website": data.get("website", ""),
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


class FmpFetchError(Exception):
    """Raised when an FMP API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
