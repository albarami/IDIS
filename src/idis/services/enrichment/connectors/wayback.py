"""Wayback Machine CDX enrichment connector (YELLOW).

Provides historical web snapshot data from the Internet Archive CDX API.
Entity type: COMPANY. Query by company_name (interpreted as domain).

Rights: YELLOW (attribution required; constrained redistribution).
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

WAYBACK_PROVIDER_ID = "wayback"
WAYBACK_BASE_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_CACHE_TTL_SECONDS = 86400
WAYBACK_SNAPSHOT_LIMIT = 10


class WaybackConnector:
    """Wayback Machine CDX enrichment connector.

    Fetches historical web snapshots for a given domain from the Internet Archive.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the Wayback connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return WAYBACK_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """YELLOW: attribution required for redistribution."""
        return RightsClass.YELLOW

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """24-hour TTL for historical snapshot data."""
        return CachePolicyConfig(ttl_seconds=WAYBACK_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch historical snapshots from Wayback Machine.

        Args:
            request: Enrichment request with company_name (domain) in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized snapshot data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "Wayback connector only supports COMPANY entity type"},
            )

        domain = request.query.company_name
        if not domain:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "company_name (domain) is required for Wayback lookup"},
            )

        encoded_domain = urllib.parse.quote(domain, safe="")
        url = f"{WAYBACK_BASE_URL}?url={encoded_domain}&output=json&limit={WAYBACK_SNAPSHOT_LIMIT}"

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except WaybackFetchError as exc:
            logger.warning("Wayback fetch failed for %s: %s", domain, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, domain)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            provider_id=WAYBACK_PROVIDER_ID,
            source_id=WAYBACK_PROVIDER_ID,
            retrieved_at=datetime.now(UTC),
            rights_class=RightsClass.YELLOW,
            raw_ref_hash=raw_hash,
            identifiers_used={"domain": domain},
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
        """Execute HTTP request to Wayback CDX API.

        The CDX API with output=json returns a JSON array-of-arrays where
        the first element is the header row. This method converts it to a dict.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.

        Returns:
            Dict with 'headers' and 'rows' keys, or None if no data found.

        Raises:
            WaybackFetchError: On network or HTTP errors.
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
            WaybackFetchError: On persistent failure.
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
                    return {"headers": raw[0], "rows": raw[1:]}

                return None

            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue

        raise WaybackFetchError(f"Wayback request failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        domain: str,
    ) -> dict[str, Any]:
        """Normalize Wayback CDX response to stable schema.

        Args:
            data: Dict with 'headers' and 'rows' keys.
            domain: Original domain query.

        Returns:
            Normalized dict with standardized fields.
        """
        header_row = data.get("headers", [])
        rows = data.get("rows", [])

        col_index: dict[str, int] = {}
        for idx, col_name in enumerate(header_row):
            col_index[col_name] = idx

        snapshots: list[dict[str, str]] = []
        for row in rows[:WAYBACK_SNAPSHOT_LIMIT]:
            if not isinstance(row, list):
                continue
            snapshot: dict[str, str] = {}
            for col_name in sorted(col_index.keys()):
                ci = col_index[col_name]
                snapshot[col_name] = row[ci] if ci < len(row) else ""
            snapshots.append(snapshot)

        return {
            "domain": domain,
            "snapshots": snapshots,
            "total_snapshots": len(snapshots),
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


class WaybackFetchError(Exception):
    """Raised when a Wayback Machine CDX API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
