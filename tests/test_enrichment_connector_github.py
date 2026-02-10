"""Tests for GitHub enrichment connector.

Uses httpx.MockTransport for deterministic testing with no live network calls.
Verifies BYOL credential handling.
"""

from __future__ import annotations

from typing import Any

import httpx

from idis.services.enrichment.connectors.github import (
    GITHUB_PROVIDER_ID,
    GitHubConnector,
)
from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)

SAMPLE_RESPONSE: dict[str, Any] = {
    "login": "acme-corp",
    "name": "Acme Corporation",
    "description": "Building the future",
    "company": "Acme Corp",
    "blog": "https://acme.com",
    "location": "San Francisco",
    "html_url": "https://github.com/acme-corp",
    "public_repos": 42,
    "created_at": "2020-01-01T00:00:00Z",
    "type": "Organization",
    "avatar_url": "https://avatars.githubusercontent.com/u/12345",
}


def _make_client(
    status_code: int = 200,
    response_json: dict[str, Any] | None = None,
    raise_error: bool = False,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_error:
            raise httpx.ConnectError("Connection refused")
        body = response_json if response_json is not None else SAMPLE_RESPONSE
        return httpx.Response(status_code=status_code, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _make_request(
    company_name: str = "acme-corp",
    entity_type: EntityType = EntityType.COMPANY,
) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-gh-001",
        entity_type=entity_type,
        query=EnrichmentQuery(company_name=company_name),
    )


def _ctx_with_token() -> EnrichmentContext:
    return EnrichmentContext(
        timeout_seconds=5.0,
        max_retries=0,
        request_id="req-gh-test",
        byol_credentials={"token": "ghp_test_token_12345"},
    )


def _ctx_no_token() -> EnrichmentContext:
    return EnrichmentContext(timeout_seconds=5.0, max_retries=0, request_id="req-gh-test")


class TestGitHubProperties:
    def test_provider_id(self) -> None:
        assert GitHubConnector().provider_id == GITHUB_PROVIDER_ID

    def test_rights_class_is_green(self) -> None:
        assert GitHubConnector().rights_class == RightsClass.GREEN

    def test_cache_policy(self) -> None:
        policy = GitHubConnector().cache_policy
        assert isinstance(policy, CachePolicyConfig)
        assert policy.ttl_seconds > 0


class TestGitHubFetchSuccess:
    def test_successful_fetch_returns_hit(self) -> None:
        connector = GitHubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx_with_token())
        assert result.status == EnrichmentStatus.HIT
        assert result.normalized["login"] == "acme-corp"
        assert result.normalized["name"] == "Acme Corporation"
        assert result.normalized["public_repos"] == 42

    def test_provenance_populated(self) -> None:
        connector = GitHubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx_with_token())
        assert result.provenance is not None
        assert result.provenance.source_id == GITHUB_PROVIDER_ID
        assert result.provenance.identifiers_used["login"] == "acme-corp"

    def test_normalized_schema_deterministic(self) -> None:
        connector = GitHubConnector(http_client=_make_client())
        r1 = connector.fetch(_make_request(), _ctx_with_token())
        r2 = connector.fetch(_make_request(), _ctx_with_token())
        assert r1.normalized == r2.normalized


class TestGitHubFetchFailures:
    def test_404_returns_miss(self) -> None:
        connector = GitHubConnector(http_client=_make_client(status_code=404))
        assert connector.fetch(_make_request(), _ctx_with_token()).status == EnrichmentStatus.MISS

    def test_500_returns_error(self) -> None:
        connector = GitHubConnector(http_client=_make_client(status_code=500, response_json={}))
        assert connector.fetch(_make_request(), _ctx_with_token()).status == EnrichmentStatus.ERROR

    def test_network_error_returns_error(self) -> None:
        connector = GitHubConnector(http_client=_make_client(raise_error=True))
        assert connector.fetch(_make_request(), _ctx_with_token()).status == EnrichmentStatus.ERROR

    def test_non_company_entity_returns_error(self) -> None:
        connector = GitHubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(entity_type=EntityType.PERSON), _ctx_with_token())
        assert result.status == EnrichmentStatus.ERROR

    def test_missing_company_name_returns_error(self) -> None:
        connector = GitHubConnector(http_client=_make_client())
        request = EnrichmentRequest(
            tenant_id="t-001", entity_type=EntityType.COMPANY, query=EnrichmentQuery()
        )
        assert connector.fetch(request, _ctx_with_token()).status == EnrichmentStatus.ERROR


class TestGitHubByol:
    def test_missing_token_returns_blocked(self) -> None:
        connector = GitHubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), _ctx_no_token())
        assert result.status == EnrichmentStatus.BLOCKED_MISSING_BYOL
        assert "Token" in result.normalized.get("error", "")

    def test_empty_credentials_returns_blocked(self) -> None:
        ctx = EnrichmentContext(
            timeout_seconds=5.0,
            max_retries=0,
            request_id="req-gh-test",
            byol_credentials={},
        )
        connector = GitHubConnector(http_client=_make_client())
        result = connector.fetch(_make_request(), ctx)
        assert result.status == EnrichmentStatus.BLOCKED_MISSING_BYOL
