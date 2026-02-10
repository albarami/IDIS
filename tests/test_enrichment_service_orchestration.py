"""Tests for enrichment service orchestration.

Verifies the full orchestration flow per spec §8:
- Rights check → cache → BYOL creds → provider fetch → normalize → persist → audit
- Fail-closed on unknown provider
- Fail-closed on audit emission failure
- BLOCKED_MISSING_BYOL when BYOL creds not configured
- Cache hit returns cached result with audit event
- Successful fetch emits started + completed audit events
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.persistence.repositories.enrichment_credentials import (
    InMemoryCredentialRepository,
)
from idis.services.enrichment.cache_policy import EnrichmentCacheStore
from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentProvenance,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry
from idis.services.enrichment.rights_gate import EnvironmentMode
from idis.services.enrichment.service import (
    EnrichmentService,
    EnrichmentServiceError,
)

TENANT_ID = "tenant-svc-001"


class _FakeConnector:
    """Fake connector for testing service orchestration."""

    def __init__(
        self,
        provider_id: str = "fake_green",
        rights_class: RightsClass = RightsClass.GREEN,
        requires_byol: bool = False,
        result: EnrichmentResult | None = None,
        should_raise: bool = False,
    ) -> None:
        self._provider_id = provider_id
        self._rights_class = rights_class
        self._requires_byol = requires_byol
        self._result = result
        self._should_raise = should_raise
        self.fetch_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def rights_class(self) -> RightsClass:
        return self._rights_class

    @property
    def cache_policy(self) -> CachePolicyConfig:
        return CachePolicyConfig(ttl_seconds=3600, no_store=False)

    def fetch(self, request: EnrichmentRequest, ctx: EnrichmentContext) -> EnrichmentResult:
        self.fetch_count += 1
        if self._should_raise:
            raise RuntimeError("Simulated provider failure")
        if self._result is not None:
            return self._result
        from datetime import UTC, datetime

        return EnrichmentResult(
            status=EnrichmentStatus.HIT,
            normalized={"test": "data"},
            provenance=EnrichmentProvenance(
                provider_id=self._provider_id,
                source_id=self._provider_id,
                retrieved_at=datetime.now(UTC),
                rights_class=self._rights_class,
                raw_ref_hash="fakehash123",
            ),
        )


def _make_request(tenant_id: str = TENANT_ID) -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id=tenant_id,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(cik="0001234567"),
    )


def _build_service(
    connectors: list[tuple[Any, bool]] | None = None,
    environment: EnvironmentMode = EnvironmentMode.DEV,
    audit_sink: InMemoryAuditSink | None = None,
    credential_repo: InMemoryCredentialRepository | None = None,
    cache_store: EnrichmentCacheStore | None = None,
) -> tuple[EnrichmentService, InMemoryAuditSink]:
    sink = audit_sink or InMemoryAuditSink()
    registry = EnrichmentProviderRegistry()
    cred_repo = credential_repo or InMemoryCredentialRepository()
    cache = cache_store or EnrichmentCacheStore()

    if connectors:
        for connector, requires_byol in connectors:
            registry.register(connector, requires_byol=requires_byol)
    else:
        registry.register(_FakeConnector(), requires_byol=False)

    svc = EnrichmentService(
        registry=registry,
        audit_sink=sink,
        credential_repo=cred_repo,
        cache_store=cache,
        environment=environment,
    )
    return svc, sink


class TestUnknownProviderFailClosed:
    """Unknown provider must raise EnrichmentServiceError."""

    def test_unknown_provider_raises(self) -> None:
        svc, sink = _build_service()
        with pytest.raises(EnrichmentServiceError) as exc_info:
            svc.enrich(provider_id="nonexistent", request=_make_request())
        assert "not registered" in str(exc_info.value)

    def test_unknown_provider_emits_audit(self) -> None:
        svc, sink = _build_service()
        with pytest.raises(EnrichmentServiceError):
            svc.enrich(provider_id="nonexistent", request=_make_request())
        failed_events = [e for e in sink.events if e["event_type"] == "enrichment.failed"]
        assert len(failed_events) == 1


class TestRightsGateIntegration:
    """Rights gate blocks RED providers in PROD without BYOL."""

    def test_red_blocked_in_prod(self) -> None:
        connector = _FakeConnector(
            provider_id="red_prov",
            rights_class=RightsClass.RED,
        )
        svc, sink = _build_service(
            connectors=[(connector, False)],
            environment=EnvironmentMode.PROD,
        )
        result = svc.enrich(provider_id="red_prov", request=_make_request())
        assert result.status == EnrichmentStatus.BLOCKED_RIGHTS

    def test_green_allowed_in_prod(self) -> None:
        svc, sink = _build_service(environment=EnvironmentMode.PROD)
        result = svc.enrich(provider_id="fake_green", request=_make_request())
        assert result.status == EnrichmentStatus.HIT


class TestByolCredentialIntegration:
    """BYOL credential flow: missing creds → BLOCKED_MISSING_BYOL."""

    def test_missing_byol_creds_blocked(self) -> None:
        connector = _FakeConnector(
            provider_id="byol_prov",
            rights_class=RightsClass.GREEN,
            requires_byol=True,
        )
        svc, sink = _build_service(connectors=[(connector, True)])
        result = svc.enrich(provider_id="byol_prov", request=_make_request())
        assert result.status == EnrichmentStatus.BLOCKED_MISSING_BYOL

    def test_byol_creds_present_allows_fetch(self) -> None:
        connector = _FakeConnector(
            provider_id="byol_prov",
            rights_class=RightsClass.GREEN,
            requires_byol=True,
        )
        cred_repo = InMemoryCredentialRepository()
        cred_repo.store(
            tenant_id=TENANT_ID,
            connector_id="byol_prov",
            credentials={"api_key": "test-key-123"},
        )
        svc, sink = _build_service(
            connectors=[(connector, True)],
            credential_repo=cred_repo,
        )
        result = svc.enrich(provider_id="byol_prov", request=_make_request())
        assert result.status == EnrichmentStatus.HIT


class TestCacheIntegration:
    """Cache hit returns cached result without calling provider."""

    def test_second_call_uses_cache(self) -> None:
        connector = _FakeConnector(provider_id="cached_prov")
        svc, sink = _build_service(connectors=[(connector, False)])
        req = _make_request()

        result1 = svc.enrich(provider_id="cached_prov", request=req)
        assert result1.status == EnrichmentStatus.HIT
        assert connector.fetch_count == 1

        result2 = svc.enrich(provider_id="cached_prov", request=req)
        assert result2.status == EnrichmentStatus.HIT
        assert connector.fetch_count == 1  # Not called again

    def test_cache_hit_emits_audit(self) -> None:
        connector = _FakeConnector(provider_id="cached_prov")
        svc, sink = _build_service(connectors=[(connector, False)])
        req = _make_request()

        svc.enrich(provider_id="cached_prov", request=req)
        svc.enrich(provider_id="cached_prov", request=req)

        cache_hit_events = [e for e in sink.events if e["event_type"] == "enrichment.cache_hit"]
        assert len(cache_hit_events) == 1


class TestAuditFlow:
    """Audit events emitted for started, completed, and failed."""

    def test_successful_fetch_emits_started_and_completed(self) -> None:
        svc, sink = _build_service()
        svc.enrich(provider_id="fake_green", request=_make_request())

        event_types = [e["event_type"] for e in sink.events]
        assert "enrichment.started" in event_types
        assert "enrichment.completed" in event_types

    def test_provider_failure_emits_failed(self) -> None:
        connector = _FakeConnector(
            provider_id="failing_prov",
            should_raise=True,
        )
        svc, sink = _build_service(connectors=[(connector, False)])

        result = svc.enrich(provider_id="failing_prov", request=_make_request())
        assert result.status == EnrichmentStatus.ERROR

        event_types = [e["event_type"] for e in sink.events]
        assert "enrichment.failed" in event_types


class TestAuditFailureFatal:
    """Audit emission failure must be fatal."""

    def test_audit_failure_on_started_raises(self) -> None:
        broken_sink = MagicMock()
        call_count = 0

        def side_effect(event: dict[str, Any]) -> None:
            nonlocal call_count
            call_count += 1
            # Fail on the "enrichment.started" event (after rights check passes)
            if call_count >= 1:
                raise AuditSinkError("disk full")

        broken_sink.emit.side_effect = side_effect

        registry = EnrichmentProviderRegistry()
        registry.register(_FakeConnector(), requires_byol=False)

        svc = EnrichmentService(
            registry=registry,
            audit_sink=broken_sink,
            credential_repo=InMemoryCredentialRepository(),
            cache_store=EnrichmentCacheStore(),
            environment=EnvironmentMode.DEV,
        )

        with pytest.raises(EnrichmentServiceError) as exc_info:
            svc.enrich(provider_id="fake_green", request=_make_request())
        assert "audit emission failed" in str(exc_info.value)


class TestListProviders:
    """list_providers returns metadata for all registered connectors."""

    def test_list_providers(self) -> None:
        svc, _ = _build_service()
        providers = svc.list_providers()
        assert len(providers) == 1
        assert providers[0]["provider_id"] == "fake_green"
        assert providers[0]["rights_class"] == "GREEN"
