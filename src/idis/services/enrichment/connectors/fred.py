"""FRED (Federal Reserve Economic Data) enrichment connector (GREEN, BYOL required).

Provides economic time-series data from the FRED API.
Entity type: COMPANY. Query by ticker (interpreted as FRED series ID).

Rights: GREEN (US government public data, production-ready).
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

FRED_PROVIDER_ID = "fred"
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_CACHE_TTL_SECONDS = 86400
FRED_OBSERVATION_LIMIT = 20
FRED_CREDENTIAL_KEY = "api_key"


class FredConnector:
    """FRED enrichment connector.

    Fetches economic time-series observations from the FRED API.
    Uses the ticker query field as the FRED series ID.
    Requires a BYOL API key via ctx.byol_credentials.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the FRED connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return FRED_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: US government public data."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """24-hour TTL for economic data."""
        return CachePolicyConfig(ttl_seconds=FRED_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch economic data from FRED.

        Args:
            request: Enrichment request with ticker (series ID) in query.
            ctx: Execution context with timeouts and BYOL credentials.

        Returns:
            EnrichmentResult with normalized observation data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "FRED connector only supports COMPANY entity type"},
            )

        series_id = request.query.ticker
        if not series_id:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "ticker (FRED series ID) is required for FRED lookup"},
            )

        api_key = (ctx.byol_credentials or {}).get(FRED_CREDENTIAL_KEY)
        if not api_key:
            return EnrichmentResult(
                status=EnrichmentStatus.BLOCKED_MISSING_BYOL,
                normalized={"error": "API key not found in BYOL credentials"},
            )

        encoded_series = urllib.parse.quote(series_id, safe="")
        url = (
            f"{FRED_BASE_URL}?series_id={encoded_series}"
            f"&api_key={api_key}&file_type=json"
            f"&sort_order=desc&limit={FRED_OBSERVATION_LIMIT}"
        )

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except FredFetchError as exc:
            logger.warning("FRED fetch failed for %s: %s", series_id, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, series_id)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            provider_id=FRED_PROVIDER_ID,
            source_id=FRED_PROVIDER_ID,
            retrieved_at=datetime.now(UTC),
            rights_class=RightsClass.GREEN,
            raw_ref_hash=raw_hash,
            identifiers_used={"series_id": series_id},
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
        """Execute HTTP request to FRED API.

        Args:
            url: Full URL to fetch (includes API key as query param).
            ctx: Execution context with timeout settings.

        Returns:
            Parsed JSON response dict, or None if 404.

        Raises:
            FredFetchError: On network or HTTP errors.
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
            Parsed response or None on 404 / empty observations.

        Raises:
            FredFetchError: On persistent failure.
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

                if not data.get("observations"):
                    return None

                return data

            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue

        raise FredFetchError(f"FRED request failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        series_id: str,
    ) -> dict[str, Any]:
        """Normalize FRED response to stable schema.

        Args:
            data: Raw FRED JSON response.
            series_id: FRED series identifier.

        Returns:
            Normalized dict with standardized fields.
        """
        raw_obs = data.get("observations", [])
        observations: list[dict[str, Any]] = []

        for obs in raw_obs[:FRED_OBSERVATION_LIMIT]:
            observations.append(
                {
                    "date": obs.get("date", ""),
                    "value": obs.get("value", ""),
                }
            )

        return {
            "count": data.get("count", 0),
            "observations": observations,
            "series_id": series_id,
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


class FredFetchError(Exception):
    """Raised when a FRED API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
