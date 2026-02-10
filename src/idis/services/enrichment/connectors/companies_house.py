"""Companies House enrichment connector (GREEN, BYOL required).

Provides UK company registration data from the Companies House API.
Entity type: COMPANY. Query by company_name.

Rights: GREEN (UK government open data, production-ready).
Requires BYOL API key for authentication (Basic auth).

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

COMPANIES_HOUSE_PROVIDER_ID = "companies_house"
COMPANIES_HOUSE_BASE_URL = "https://api.company-information.service.gov.uk"
COMPANIES_HOUSE_CACHE_TTL_SECONDS = 86400
COMPANIES_HOUSE_ITEMS_PER_PAGE = 10
COMPANIES_HOUSE_CREDENTIAL_KEY = "api_key"


class CompaniesHouseConnector:
    """Companies House enrichment connector.

    Searches the UK Companies House register for company information.
    Requires a BYOL API key passed via ctx.byol_credentials.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the Companies House connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return COMPANIES_HOUSE_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: UK government open data."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """24-hour TTL for company registration data."""
        return CachePolicyConfig(ttl_seconds=COMPANIES_HOUSE_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch company data from Companies House.

        Args:
            request: Enrichment request with company_name in query.
            ctx: Execution context with timeouts and BYOL credentials.

        Returns:
            EnrichmentResult with normalized company data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "Companies House connector only supports COMPANY entity type"},
            )

        company_name = request.query.company_name
        if not company_name:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "company_name is required for Companies House lookup"},
            )

        api_key = (ctx.byol_credentials or {}).get(COMPANIES_HOUSE_CREDENTIAL_KEY)
        if not api_key:
            return EnrichmentResult(
                status=EnrichmentStatus.BLOCKED_MISSING_BYOL,
                normalized={"error": "API key not found in BYOL credentials"},
            )

        encoded_name = urllib.parse.quote_plus(company_name)
        url = (
            f"{COMPANIES_HOUSE_BASE_URL}/search/companies"
            f"?q={encoded_name}&items_per_page={COMPANIES_HOUSE_ITEMS_PER_PAGE}"
        )

        try:
            response_data = self._make_request(url=url, ctx=ctx, api_key=api_key)
        except CompaniesHouseFetchError as exc:
            logger.warning("Companies House fetch failed for %s: %s", company_name, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, company_name)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            provider_id=COMPANIES_HOUSE_PROVIDER_ID,
            source_id=COMPANIES_HOUSE_PROVIDER_ID,
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
        api_key: str,
    ) -> dict[str, Any] | None:
        """Execute HTTP request to Companies House API with Basic auth.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.
            api_key: BYOL API key for Basic authentication.

        Returns:
            Parsed JSON response dict, or None if 404.

        Raises:
            CompaniesHouseFetchError: On network or HTTP errors.
        """
        headers = {
            "Accept": "application/json",
        }
        auth = httpx.BasicAuth(username=api_key, password="")

        client = self._http_client
        should_close = False
        if client is None:
            client = httpx.Client(
                timeout=ctx.timeout_seconds,
                headers=headers,
                auth=auth,
            )
            should_close = True

        extra_headers = headers if self._http_client is not None else {}
        request_auth = auth if self._http_client is not None else None
        try:
            return self._execute_with_retries(
                client=client,
                url=url,
                headers=extra_headers,
                ctx=ctx,
                auth=request_auth,
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
        auth: httpx.BasicAuth | None = None,
    ) -> dict[str, Any] | None:
        """Execute request with retry logic.

        Args:
            client: HTTP client to use.
            url: Request URL.
            headers: Additional headers.
            ctx: Context with retry settings.
            auth: Optional Basic auth for injected clients.

        Returns:
            Parsed response or None on 404.

        Raises:
            CompaniesHouseFetchError: On persistent failure.
        """
        last_error: Exception | None = None
        attempts = 1 + ctx.max_retries

        kwargs: dict[str, Any] = {"headers": headers}
        if auth is not None:
            kwargs["auth"] = auth

        for attempt in range(attempts):
            try:
                response = client.get(url, **kwargs)

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

        raise CompaniesHouseFetchError(
            f"Companies House request failed after {attempts} attempts: {last_error}"
        )

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        company_name: str,
    ) -> dict[str, Any]:
        """Normalize Companies House response to stable schema.

        Args:
            data: Raw Companies House JSON response.
            company_name: Original query term.

        Returns:
            Normalized dict with standardized fields.
        """
        raw_items = data.get("items", [])
        companies: list[dict[str, Any]] = []

        for item in raw_items[:COMPANIES_HOUSE_ITEMS_PER_PAGE]:
            address = item.get("address", {}) or {}
            companies.append(
                {
                    "address_snippet": item.get("address_snippet", ""),
                    "company_number": item.get("company_number", ""),
                    "company_status": item.get("company_status", ""),
                    "company_type": item.get("company_type", ""),
                    "date_of_creation": item.get("date_of_creation", ""),
                    "locality": address.get("locality", ""),
                    "postal_code": address.get("postal_code", ""),
                    "title": item.get("title", ""),
                }
            )

        return {
            "companies": companies,
            "query": company_name,
            "total_results": data.get("total_results", 0),
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


class CompaniesHouseFetchError(Exception):
    """Raised when a Companies House API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
