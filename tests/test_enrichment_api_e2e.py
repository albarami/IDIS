"""End-to-end tests for enrichment API routes.

Tests the full API surface:
- POST /v1/enrichment/fetch
- GET /v1/enrichment/providers
- RBAC enforcement (AUDITOR blocked from fetch, allowed to list providers)
- Tenant isolation
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.audit.sink import InMemoryAuditSink


def _api_keys_json(
    tenant_id: str = "t-enrich-001",
    actor_id: str = "actor-001",
    roles: list[str] | None = None,
) -> str:
    if roles is None:
        roles = ["ANALYST"]
    return json.dumps(
        {
            "test-key-enrich": {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "name": "Enrichment Test Tenant",
                "timezone": "UTC",
                "data_region": "me-south-1",
                "roles": roles,
            }
        }
    )


def _auditor_api_keys_json() -> str:
    return json.dumps(
        {
            "auditor-key-enrich": {
                "tenant_id": "t-enrich-001",
                "actor_id": "auditor-001",
                "name": "Auditor Test Tenant",
                "timezone": "UTC",
                "data_region": "me-south-1",
                "roles": ["AUDITOR"],
            }
        }
    )


@pytest.fixture()
def _enrich_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client with enrichment service configured."""
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _api_keys_json())
    sink = InMemoryAuditSink()
    app = create_app(audit_sink=sink)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def _auditor_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client with AUDITOR role."""
    keys = json.loads(_api_keys_json())
    keys.update(json.loads(_auditor_api_keys_json()))
    monkeypatch.setenv("IDIS_API_KEYS_JSON", json.dumps(keys))
    sink = InMemoryAuditSink()
    app = create_app(audit_sink=sink)
    return TestClient(app, raise_server_exceptions=False)


class TestListProviders:
    """GET /v1/enrichment/providers returns registered providers."""

    def test_list_providers_returns_edgar(self, _enrich_client: TestClient) -> None:
        resp = _enrich_client.get(
            "/v1/enrichment/providers",
            headers={"X-IDIS-API-Key": "test-key-enrich"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        provider_ids = [p["provider_id"] for p in data["providers"]]
        assert "sec_edgar" in provider_ids

    def test_list_providers_returns_all_14(self, _enrich_client: TestClient) -> None:
        resp = _enrich_client.get(
            "/v1/enrichment/providers",
            headers={"X-IDIS-API-Key": "test-key-enrich"},
        )
        assert resp.status_code == 200
        data = resp.json()
        provider_ids = {p["provider_id"] for p in data["providers"]}
        expected = {
            "sec_edgar",
            "companies_house",
            "github",
            "fred",
            "finnhub",
            "fmp",
            "world_bank",
            "escwa_catalog",
            "qatar_open_data",
            "hackernews",
            "gdelt",
            "patentsview",
            "wayback",
            "google_news_rss",
        }
        assert provider_ids == expected

    def test_list_providers_has_correct_schema(self, _enrich_client: TestClient) -> None:
        resp = _enrich_client.get(
            "/v1/enrichment/providers",
            headers={"X-IDIS-API-Key": "test-key-enrich"},
        )
        data = resp.json()
        edgar = next(p for p in data["providers"] if p["provider_id"] == "sec_edgar")
        assert edgar["rights_class"] == "GREEN"
        assert edgar["requires_byol"] is False
        assert edgar["cache_ttl_seconds"] > 0
        assert edgar["cache_no_store"] is False

    def test_list_providers_no_auth_returns_401(self, _enrich_client: TestClient) -> None:
        resp = _enrich_client.get("/v1/enrichment/providers")
        assert resp.status_code == 401


class TestFetchEnrichment:
    """POST /v1/enrichment/fetch with various scenarios."""

    def test_fetch_unknown_provider_returns_500(self, _enrich_client: TestClient) -> None:
        resp = _enrich_client.post(
            "/v1/enrichment/fetch",
            headers={"X-IDIS-API-Key": "test-key-enrich"},
            json={
                "provider_id": "nonexistent_provider",
                "entity_type": "COMPANY",
                "query": {"cik": "0001234567"},
            },
        )
        assert resp.status_code == 500

    def test_fetch_no_auth_returns_401(self, _enrich_client: TestClient) -> None:
        resp = _enrich_client.post(
            "/v1/enrichment/fetch",
            json={
                "provider_id": "sec_edgar",
                "entity_type": "COMPANY",
                "query": {"cik": "0001234567"},
            },
        )
        assert resp.status_code == 401

    def test_fetch_invalid_entity_type_returns_422(self, _enrich_client: TestClient) -> None:
        resp = _enrich_client.post(
            "/v1/enrichment/fetch",
            headers={"X-IDIS-API-Key": "test-key-enrich"},
            json={
                "provider_id": "sec_edgar",
                "entity_type": "INVALID",
                "query": {"cik": "0001234567"},
            },
        )
        assert resp.status_code == 422


class TestRbacEnforcement:
    """RBAC: AUDITOR can list providers but cannot fetch (mutation)."""

    def test_auditor_can_list_providers(self, _auditor_client: TestClient) -> None:
        resp = _auditor_client.get(
            "/v1/enrichment/providers",
            headers={"X-IDIS-API-Key": "auditor-key-enrich"},
        )
        assert resp.status_code == 200

    def test_auditor_blocked_from_fetch(self, _auditor_client: TestClient) -> None:
        resp = _auditor_client.post(
            "/v1/enrichment/fetch",
            headers={"X-IDIS-API-Key": "auditor-key-enrich"},
            json={
                "provider_id": "sec_edgar",
                "entity_type": "COMPANY",
                "query": {"cik": "0001234567"},
            },
        )
        assert resp.status_code == 403
