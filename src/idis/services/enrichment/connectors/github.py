"""GitHub enrichment connector (GREEN, BYOL required).

Provides organization and repository data from the GitHub REST API.
Entity type: COMPANY. Query by company_name (GitHub org/user login).

Rights: GREEN (public API data, production-ready).
Requires BYOL personal access token for authentication.

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

GITHUB_PROVIDER_ID = "github"
GITHUB_BASE_URL = "https://api.github.com"
GITHUB_CACHE_TTL_SECONDS = 3600
GITHUB_USER_AGENT = "IDIS/1.0"
GITHUB_CREDENTIAL_KEY = "token"


class GitHubConnector:
    """GitHub enrichment connector.

    Fetches organization profile data from the GitHub REST API.
    Requires a BYOL personal access token via ctx.byol_credentials.
    Implements the adapter contract per spec §3.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        """Initialize the GitHub connector.

        Args:
            http_client: Optional httpx.Client for dependency injection (testing).
        """
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return GITHUB_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        """GREEN: public API data."""
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        """1-hour TTL for GitHub org data."""
        return CachePolicyConfig(ttl_seconds=GITHUB_CACHE_TTL_SECONDS, no_store=False)

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch organization data from GitHub.

        Args:
            request: Enrichment request with company_name (org login) in query.
            ctx: Execution context with timeouts and BYOL credentials.

        Returns:
            EnrichmentResult with normalized org data.
        """
        if request.entity_type != EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "GitHub connector only supports COMPANY entity type"},
            )

        company_name = request.query.company_name
        if not company_name:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "company_name (org login) is required for GitHub lookup"},
            )

        token = (ctx.byol_credentials or {}).get(GITHUB_CREDENTIAL_KEY)
        if not token:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "Token not found in BYOL credentials"},
            )

        encoded_name = urllib.parse.quote(company_name, safe="")
        url = f"{GITHUB_BASE_URL}/orgs/{encoded_name}"

        try:
            response_data = self._make_request(url=url, ctx=ctx, token=token)
        except GitHubFetchError as exc:
            logger.warning("GitHub fetch failed for %s: %s", company_name, exc)
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": str(exc)},
            )

        if response_data is None:
            return EnrichmentResult(status=EnrichmentStatus.MISS)

        normalized = self._normalize_response(response_data, company_name)
        raw_hash = self._compute_raw_hash(response_data)

        provenance = EnrichmentProvenance(
            source_id=GITHUB_PROVIDER_ID,
            retrieved_at=datetime.now(UTC),
            rights_class=RightsClass.GREEN,
            raw_ref_hash=raw_hash,
            identifiers_used={"login": response_data.get("login", company_name)},
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
        token: str,
    ) -> dict[str, Any] | None:
        """Execute HTTP request to GitHub API with Bearer auth.

        Args:
            url: Full URL to fetch.
            ctx: Execution context with timeout settings.
            token: BYOL personal access token.

        Returns:
            Parsed JSON response dict, or None if 404.

        Raises:
            GitHubFetchError: On network or HTTP errors.
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": GITHUB_USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
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
            GitHubFetchError: On persistent failure.
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

        raise GitHubFetchError(f"GitHub request failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _normalize_response(
        data: dict[str, Any],
        company_name: str,
    ) -> dict[str, Any]:
        """Normalize GitHub org response to stable schema.

        Args:
            data: Raw GitHub org JSON response.
            company_name: Original query term.

        Returns:
            Normalized dict with standardized fields.
        """
        return {
            "avatar_url": data.get("avatar_url", ""),
            "blog": data.get("blog", ""),
            "company": data.get("company", ""),
            "created_at": data.get("created_at", ""),
            "description": data.get("description", ""),
            "html_url": data.get("html_url", ""),
            "location": data.get("location", ""),
            "login": data.get("login", company_name),
            "name": data.get("name", ""),
            "public_repos": data.get("public_repos", 0),
            "type": data.get("type", ""),
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


class GitHubFetchError(Exception):
    """Raised when a GitHub API request fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
