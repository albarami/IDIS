"""USPTO PatentsView enrichment connector (GREEN).

Provides patent data from the PatentsView API maintained by the USPTO.
Entity type: COMPANY. Query by company_name (assignee organization search).

Rights: GREEN (US government public API, production-ready).
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

PATENTSVIEW_PROVIDER_ID = "patentsview"
PATENTSVIEW_BASE_URL = "https://api.patentsview.org/patents/query"
PATENTSVIEW_CACHE_TTL_SECONDS = 604800
PATENTSVIEW_PER_PAGE = 10


class PatentsViewConnector:
    """USPTO PatentsView enrichment connector.

    Searches patents by assignee organization name.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the PatentsView connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return PATENTSVIEW_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: US government public data."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """7-day TTL for patent data."""
        return CachePolicyConfig(ttl_seconds=PATENTSVIEW_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch patent data from PatentsView.

        Args:
            request: Enrichment request with company_name in query.
            ctx: Execution context with timeouts.

        Returns:
            EnrichmentResult with normalized patent data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "PatentsView connector only supports COMPANY entity type"},
            )

        company_name = request.query.company_name
        if not company_name:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "company_name is required for PatentsView lookup"},
            )

        query_json = json.dumps(
            {"_contains": {"assignee_organization": company_name}},
            sort_keys=True,
            separators=(",", ":"),
        )
        fields_json = '["patent_number","patent_title","patent_date","assignee_organization"]'
        options_json = json.dumps({"per_page": PATENTSVIEW_PER_PAGE})

        encoded_q = urllib.parse.quote(query_json, safe="")
        encoded_f = urllib.parse.quote(fields_json, safe="")
        encoded_o = urllib.parse.quote(options_json, safe="")
        url = f"{PATENTSVIEW_BASE_URL}?q={encoded_q}&f={encoded_f}&o={encoded_o}"

        try:
            response_data = self._make_request(url=url, ctx=ctx)
        except PatentsViewFetchError as exc:
            logger.warning("PatentsView fetch failed for %s: %s", company_name, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, company_name)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            source_id=PATENTSVIEW_PROVIDER_ID,
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
        """Execute HTTP request to PatentsView API.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.

        Returns:
            Parsed JSON response dict, or None if 404.

        Raises:
            PatentsViewFetchError: On network or HTTP errors.
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
            PatentsViewFetchError: On persistent failure.
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

                if data.get("patents") is None:
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

        raise PatentsViewFetchError(
            f"PatentsView request failed after {attempts} attempts: {last_error}"
        )

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        company_name: str,
    ) -> dict[str, Any]:
        """Normalize PatentsView response to stable schema.

        Args:
            data: Raw PatentsView JSON response.
            company_name: Original query term.

        Returns:
            Normalized dict with standardized fields.
        """
        raw_patents = data.get("patents", [])
        patents: list[dict[str, Any]] = []

        for patent in raw_patents[:PATENTSVIEW_PER_PAGE]:
            assignees_raw = patent.get("assignees", [])
            assignees = [
                a.get("assignee_organization", "") for a in assignees_raw if isinstance(a, dict)
            ]
            patents.append(
                {
                    "assignees": sorted(assignees),
                    "patent_date": patent.get("patent_date", ""),
                    "patent_number": patent.get("patent_number", ""),
                    "patent_title": patent.get("patent_title", ""),
                }
            )

        return {
            "patents": patents,
            "query": company_name,
            "total_patent_count": data.get("total_patent_count", 0),
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


class PatentsViewFetchError(Exception):
    """Raised when a PatentsView API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
