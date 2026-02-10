"""SEC EDGAR enrichment connector (GREEN).

Provides company filing information from the SEC EDGAR public API.
Entity type: COMPANY. Query by CIK and/or company name.

Rights: GREEN (US government public API, production-ready).
Must declare User-Agent header per SEC EDGAR access policy.

Spec: IDIS_Data_Architecture_v3_1.md §1 (Entity & Company Intelligence)
"""

from __future__ import annotations

import hashlib
import json
import logging
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

SEC_EDGAR_BASE_URL = "https://data.sec.gov"
SEC_EDGAR_SUBMISSIONS_URL = f"{SEC_EDGAR_BASE_URL}/submissions/CIK{{cik}}.json"
SEC_EDGAR_COMPANY_TICKERS_URL = f"{SEC_EDGAR_BASE_URL}/submissions/company_tickers.json"
SEC_EDGAR_USER_AGENT = "IDIS/1.0 (institutional-dd@idis.app)"

EDGAR_PROVIDER_ID = "sec_edgar"
EDGAR_CONNECTOR_VERSION = "1.0.0"

# SEC EDGAR filings data is updated daily; 24h TTL per caching policy
EDGAR_CACHE_TTL_SECONDS = 86400


class EdgarConnector:
    """SEC EDGAR enrichment connector.

    Fetches company filing existence, latest filing date, and registrant name
    from the SEC EDGAR XBRL submissions API.

    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the EDGAR connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
                If None, a new client is created per fetch call with proper headers.
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return EDGAR_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: US government public API."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """24-hour TTL for EDGAR filings data."""
        return CachePolicyConfig(ttl_seconds=EDGAR_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch company filing data from SEC EDGAR.

        Args:
            request: Enrichment request with CIK in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized filing data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "EDGAR connector only supports COMPANY entity type"},
            )

        cik = request.query.cik
        if not cik:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "CIK is required for EDGAR lookup"},
            )

        # Normalize CIK to 10-digit zero-padded format
        cik_padded = cik.zfill(10)
        url = SEC_EDGAR_SUBMISSIONS_URL.format(cik=cik_padded)

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except EdgarFetchError as exc:
            logger.warning(
                "EDGAR fetch failed for CIK %s: %s",
                cik,
                exc,
            )
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, cik_padded)
        raw_hash = self._compute_raw_hash(response_data)

        now = datetime.now(UTC)
        provenance = EnrichmentProvenance(
            provider_id=EDGAR_PROVIDER_ID,
            source_id=EDGAR_PROVIDER_ID,
            retrieved_at=now,
            rights_class=RightsClass.GREEN,
            raw_ref_hash=raw_hash,
            identifiers_used={"cik": cik_padded},
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
        """Execute HTTP request to SEC EDGAR.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.

        Returns:
            Parsed JSON response dict, or None if 404.

        Raises:
            EdgarFetchError: On network or HTTP errors.
        """
        headers = {
            "User-Agent": SEC_EDGAR_USER_AGENT,
            "Accept": "application/json",
        }

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
            EdgarFetchError: On persistent failure.
        """
        last_error: Exception | None = None
        attempts = 1 + ctx.max_retries

        for attempt in range(attempts):
            try:
                response = client.get(url, headers=headers)

                if response.status_code == 404:
                    return None

                if response.status_code == 429:
                    if attempt < attempts - 1:
                        continue
                    raise EdgarFetchError(f"Rate limited by SEC EDGAR after {attempts} attempts")

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

        raise EdgarFetchError(f"EDGAR request failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        cik: str,
    ) -> dict[str, Any]:
        """Normalize EDGAR submissions response to stable schema.

        Args:
            data: Raw EDGAR submissions JSON.
            cik: Zero-padded CIK.

        Returns:
            Normalized dict with standardized fields.
        """
        recent_filings = data.get("filings", {}).get("recent", {})
        filing_dates = recent_filings.get("filingDate", [])
        form_types = recent_filings.get("form", [])

        latest_filing_date: str | None = None
        if filing_dates:
            latest_filing_date = filing_dates[0]

        total_filings = len(filing_dates)

        # Count by form type
        form_type_counts: dict[str, int] = {}
        for ft in form_types:
            form_type_counts[ft] = form_type_counts.get(ft, 0) + 1

        return {
            "cik": cik,
            "registrant_name": data.get("name", ""),
            "entity_type_sec": data.get("entityType", ""),
            "sic": data.get("sic", ""),
            "sic_description": data.get("sicDescription", ""),
            "state_of_incorporation": data.get("stateOfIncorporation", ""),
            "fiscal_year_end": data.get("fiscalYearEnd", ""),
            "has_filings": total_filings > 0,
            "total_recent_filings": total_filings,
            "latest_filing_date": latest_filing_date,
            "form_type_counts": form_type_counts,
            "tickers": data.get("tickers", []),
            "exchanges": data.get("exchanges", []),
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


class EdgarFetchError(Exception):
    """Raised when an EDGAR API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
