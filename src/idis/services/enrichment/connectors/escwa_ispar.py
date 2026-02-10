"""ESCWA ISPAR / Arab Development Portal enrichment connector (GREEN).

Provides dataset catalog search and country-level indicator data from the
UN ESCWA Arab Development Portal (data.arabdevelopmentportal.org).

The ADP aggregates 200k+ datasets from UN agencies, national statistical
offices, and other institutions covering 22 Arab states. Themes include
macroeconomy, trade, banking/finance, demography, health, education,
governance, energy, and SDG tracking.

Entity type: COMPANY. Query by jurisdiction (ISO3 country code).

Rights: GREEN (UN open data, free unrestricted access).
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

ESCWA_ISPAR_PROVIDER_ID = "escwa_ispar"

ADP_BASE_URL = "https://data.arabdevelopmentportal.org"
ADP_CATALOG_SEARCH_URL = ADP_BASE_URL + "/datacatalog/un-agencies"
ADP_COUNTRY_DATA_URL = ADP_BASE_URL + "/country/{iso3}"

ESCWA_ISPAR_CACHE_TTL_SECONDS = 604800  # 7 days — statistical data

ARAB_COUNTRY_ISO3 = frozenset(
    {
        "dza",
        "bhr",
        "com",
        "dji",
        "egy",
        "irq",
        "jor",
        "kwt",
        "lbn",
        "lby",
        "mrt",
        "mar",
        "omn",
        "pse",
        "qat",
        "sau",
        "som",
        "sdn",
        "syr",
        "tun",
        "are",
        "yem",
    }
)


class EscwaIsparFetchError(Exception):
    """Raised when the ADP request fails after retries."""


class EscwaIsparConnector:
    """ESCWA ISPAR / Arab Development Portal enrichment connector.

    Searches the ADP data catalog for country-level datasets and
    indicators relevant to the Arab region. The portal hosts ESCWA,
    World Bank, and other UN agency data with CSV/JSON download
    support.

    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the ESCWA ISPAR connector.

        Args:
            http_client: Optional injected HTTP client for testing.
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Return the provider identifier."""
        return ESCWA_ISPAR_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """Return GREEN — UN open data, free unrestricted access."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """Return cache policy: 7-day TTL for statistical data."""
        return CachePolicyConfig(
            ttl_seconds=ESCWA_ISPAR_CACHE_TTL_SECONDS,
            no_store=False,
        )

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch country-level catalog data from the Arab Development Portal.

        Args:
            request: Enrichment request with jurisdiction (ISO3) in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized catalog data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": ("ESCWA ISPAR connector only supports COMPANY entity type")},
            )

        jurisdiction = request.query.jurisdiction
        if not jurisdiction:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={
                    "error": ("Jurisdiction (ISO3 country code) is required for ESCWA ISPAR lookup")
                },
            )

        iso3 = jurisdiction.lower()

        try:
            response_data = self._make_request(iso3=iso3, ctx=ctx)
        except EscwaIsparFetchError as exc:
            logger.warning(
                "ESCWA ISPAR fetch failed for %s: %s",
                iso3,
                exc,
            )
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, iso3)
        raw_hash = self._compute_raw_hash(response_data)

        now = datetime.now(UTC)
        provenance = EnrichmentProvenance(
            provider_id=ESCWA_ISPAR_PROVIDER_ID,
            source_id=ESCWA_ISPAR_PROVIDER_ID,
            retrieved_at=now,
            rights_class=RightsClass.GREEN,
            raw_ref_hash=raw_hash,
            identifiers_used={"jurisdiction": iso3},
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
        iso3: str,
        ctx: EnrichmentContext,
    ) -> dict[str, Any] | None:
        """Execute HTTP request to the Arab Development Portal.

        Args:
            iso3: ISO3 country code (lowercase).
            ctx: Execution context with timeout settings.

        Returns:
            Parsed JSON response dict, or None if 404.

        Raises:
            EscwaIsparFetchError: On network or HTTP errors.
        """
        headers = {
            "Accept": "application/json",
        }

        params = {
            "sources": "escwa",
            "countries": iso3,
        }

        url = ADP_CATALOG_SEARCH_URL

        client = self._http_client
        should_close = False
        if client is None:
            client = httpx.Client(
                timeout=ctx.timeout_seconds,
                headers=headers,
            )
            should_close = True
        try:
            return self._execute_with_retries(
                client=client,
                url=url,
                headers=headers if self._http_client is not None else {},
                params=params,
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
        params: dict[str, str],
        ctx: EnrichmentContext,
    ) -> dict[str, Any] | None:
        """Execute request with retry logic.

        Args:
            client: HTTP client to use.
            url: Request URL.
            headers: Additional headers.
            params: Query parameters.
            ctx: Context with retry settings.

        Returns:
            Parsed response or None on 404.

        Raises:
            EscwaIsparFetchError: On persistent failure.
        """
        last_error: Exception | None = None
        attempts = 1 + ctx.max_retries

        for attempt in range(attempts):
            try:
                response = client.get(
                    url,
                    headers=headers,
                    params=params,
                )

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

        raise EscwaIsparFetchError(f"ADP request failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        iso3: str,
    ) -> dict[str, Any]:
        """Normalize ADP catalog response to stable schema.

        Args:
            data: Raw ADP response.
            iso3: ISO3 country code queried.

        Returns:
            Normalized dict with standardized fields.
        """
        datasets_raw = data.get("datasets", data.get("results", []))
        if not isinstance(datasets_raw, list):
            datasets_raw = []

        datasets = []
        for ds in datasets_raw:
            if isinstance(ds, dict):
                datasets.append(
                    {
                        "id": ds.get("id", ds.get("dataset_id", "")),
                        "title": ds.get("title", ""),
                        "description": ds.get(
                            "description",
                            ds.get("notes", ""),
                        ),
                        "source": ds.get(
                            "source",
                            ds.get("organization", ""),
                        ),
                        "theme": ds.get("theme", ds.get("category", "")),
                        "modified": ds.get(
                            "modified",
                            ds.get("metadata_modified", ""),
                        ),
                        "format": ds.get("format", ""),
                    }
                )

        total_count = data.get(
            "total_count",
            data.get("count", len(datasets)),
        )

        return {
            "jurisdiction": iso3,
            "portal": "arabdevelopmentportal.org",
            "source_filter": "escwa",
            "total_count": total_count,
            "datasets": datasets,
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
