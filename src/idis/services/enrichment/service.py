"""Enrichment service orchestrator.

The single entry point for all enrichment operations, used by the API route.
Implements the strict orchestration flow per spec §8:

1. Rights check (rights_gate) → if blocked, return BLOCKED_RIGHTS + audit
2. Cache lookup (cache_policy) → if hit, return cached result + audit cache_hit
3. If connector needs BYOL creds: load via credential repo → missing = BLOCKED_MISSING_BYOL + audit
4. Call provider fetch(request, ctx) with strict timeout/retry
5. Normalize to EnrichmentResult.normalized schema deterministically
6. Persist cache entry (in-memory; Postgres when configured)
7. Emit audit: enrichment.started, enrichment.completed, enrichment.failed
   (fail-closed if audit emission fails)

Spec: IDIS_Enrichment_Connector_Framework_v0_1.md §8
Roadmap: Task 7.8 (Phase 7.C)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from idis.audit.sink import AuditSink, AuditSinkError
from idis.persistence.repositories.enrichment_credentials import (
    CredentialNotFoundError,
    InMemoryCredentialRepository,
)
from idis.services.enrichment.cache_policy import (
    EnrichmentCacheStore,
    store_cache_entry,
    try_cache_lookup,
)
from idis.services.enrichment.models import (
    EnrichmentContext,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
)
from idis.services.enrichment.registry import (
    EnrichmentProviderRegistry,
    ProviderNotRegisteredError,
)
from idis.services.enrichment.rights_gate import (
    EnvironmentMode,
    check_rights,
)

logger = logging.getLogger(__name__)


class EnrichmentServiceError(Exception):
    """Raised when the enrichment service encounters a fatal error."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class EnrichmentService:
    """Orchestrates enrichment requests through the full pipeline.

    This is the only entry point used by the API route. All enrichment
    requests flow through this service.
    """

    def __init__(
        self,
        *,
        registry: EnrichmentProviderRegistry,
        audit_sink: AuditSink,
        credential_repo: InMemoryCredentialRepository,
        cache_store: EnrichmentCacheStore,
        environment: EnvironmentMode = EnvironmentMode.DEV,
    ) -> None:
        """Initialize the enrichment service.

        Args:
            registry: Provider registry with registered connectors.
            audit_sink: Audit sink for event emission (required, fail-closed).
            credential_repo: BYOL credential repository.
            cache_store: Cache store for enrichment results.
            environment: Deployment environment mode.
        """
        self._registry = registry
        self._audit_sink = audit_sink
        self._credential_repo = credential_repo
        self._cache_store = cache_store
        self._environment = environment

    def enrich(
        self,
        *,
        provider_id: str,
        request: EnrichmentRequest,
        request_id: str | None = None,
    ) -> EnrichmentResult:
        """Execute the full enrichment orchestration flow.

        Args:
            provider_id: ID of the provider to use.
            request: Enrichment request details.
            request_id: Optional correlation ID (generated if not provided).

        Returns:
            EnrichmentResult with status, normalized data, and provenance.

        Raises:
            EnrichmentServiceError: On fatal errors (audit failure, config errors).
        """
        if request_id is None:
            request_id = str(uuid.uuid4())

        # Step 0: Resolve provider (fail-closed on unknown)
        try:
            descriptor = self._registry.get(provider_id)
        except ProviderNotRegisteredError as exc:
            self._emit_audit_event(
                event_type="enrichment.failed",
                tenant_id=request.tenant_id,
                provider_id=provider_id,
                request_id=request_id,
                severity="MEDIUM",
                details={"error": str(exc)},
            )
            raise EnrichmentServiceError(str(exc)) from exc

        # Step 1: Rights check
        has_byol = self._credential_repo.exists(
            tenant_id=request.tenant_id,
            connector_id=provider_id,
        )

        rights_decision = check_rights(
            rights_class=descriptor.rights_class,
            provider_id=provider_id,
            tenant_id=request.tenant_id,
            environment=self._environment,
            has_byol_credentials=has_byol,
            audit_sink=self._audit_sink,
            request_id=request_id,
        )

        if not rights_decision.allowed:
            return EnrichmentResult(
                status=EnrichmentStatus.BLOCKED_RIGHTS,
                normalized={"reason": rights_decision.reason},
            )

        # Step 2: Cache lookup
        cached = try_cache_lookup(
            cache_store=self._cache_store,
            request=request,
            provider_id=provider_id,
            policy=descriptor.cache_policy,
        )

        if cached is not None:
            self._emit_audit_event(
                event_type="enrichment.cache_hit",
                tenant_id=request.tenant_id,
                provider_id=provider_id,
                request_id=request_id,
                severity="LOW",
                details={"status": cached.status.value},
            )
            return cached

        # Step 3: BYOL credential loading (if required)
        ctx = EnrichmentContext(
            timeout_seconds=30.0,
            max_retries=1,
            request_id=request_id,
        )

        if descriptor.requires_byol:
            try:
                creds = self._credential_repo.load(
                    tenant_id=request.tenant_id,
                    connector_id=provider_id,
                )
                ctx = EnrichmentContext(
                    timeout_seconds=ctx.timeout_seconds,
                    max_retries=ctx.max_retries,
                    request_id=request_id,
                    byol_credentials=creds,
                )
            except CredentialNotFoundError:
                self._emit_audit_event(
                    event_type="enrichment.blocked",
                    tenant_id=request.tenant_id,
                    provider_id=provider_id,
                    request_id=request_id,
                    severity="MEDIUM",
                    details={"reason": "BYOL credentials not configured"},
                )
                return EnrichmentResult(
                    status=EnrichmentStatus.BLOCKED_MISSING_BYOL,
                    normalized={"reason": "BYOL credentials not configured for this provider"},
                )

        # Step 4: Emit enrichment.started
        self._emit_audit_event(
            event_type="enrichment.started",
            tenant_id=request.tenant_id,
            provider_id=provider_id,
            request_id=request_id,
            severity="LOW",
            details={
                "entity_type": request.entity_type.value,
                "purpose": request.purpose.value,
            },
        )

        # Step 5: Call provider fetch
        try:
            result = descriptor.connector.fetch(request, ctx)
        except Exception as exc:
            self._emit_audit_event(
                event_type="enrichment.failed",
                tenant_id=request.tenant_id,
                provider_id=provider_id,
                request_id=request_id,
                severity="MEDIUM",
                details={"error": str(exc)},
            )
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": f"Provider fetch failed: {exc}"},
            )

        # Step 6: Persist cache entry
        store_cache_entry(
            cache_store=self._cache_store,
            request=request,
            provider_id=provider_id,
            result=result,
            policy=descriptor.cache_policy,
        )

        # Step 7: Emit enrichment.completed
        self._emit_audit_event(
            event_type="enrichment.completed",
            tenant_id=request.tenant_id,
            provider_id=provider_id,
            request_id=request_id,
            severity="LOW",
            details={
                "status": result.status.value,
                "has_provenance": result.provenance is not None,
            },
        )

        return result

    def list_providers(self) -> list[dict[str, Any]]:
        """List all registered providers with their metadata.

        Returns:
            List of provider descriptor dicts.
        """
        descriptors = self._registry.list_providers()
        return [
            {
                "provider_id": d.provider_id,
                "rights_class": d.rights_class.value,
                "requires_byol": d.requires_byol,
                "cache_ttl_seconds": d.cache_policy.ttl_seconds,
                "cache_no_store": d.cache_policy.no_store,
            }
            for d in descriptors
        ]

    def _emit_audit_event(
        self,
        *,
        event_type: str,
        tenant_id: str,
        provider_id: str,
        request_id: str,
        severity: str,
        details: dict[str, Any],
    ) -> None:
        """Emit an audit event. Fail-closed: raises on emission failure.

        Args:
            event_type: Audit event type (e.g., enrichment.started).
            tenant_id: Tenant ID for scoping.
            provider_id: Provider identifier.
            request_id: Correlation ID.
            severity: Event severity (LOW/MEDIUM/HIGH/CRITICAL).
            details: Additional event payload.

        Raises:
            EnrichmentServiceError: If audit emission fails.
        """
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "occurred_at": datetime.now(UTC).isoformat(),
            "tenant_id": tenant_id,
            "severity": severity,
            "resource": {
                "resource_type": "enrichment",
                "provider_id": provider_id,
            },
            "request": {
                "request_id": request_id,
            },
            "payload": details,
        }

        try:
            self._audit_sink.emit(event)
        except (AuditSinkError, Exception) as exc:
            raise EnrichmentServiceError(
                f"Fatal: audit emission failed for {event_type} "
                f"provider={provider_id} tenant={tenant_id}: {exc}"
            ) from exc


def create_default_enrichment_service(
    *,
    audit_sink: AuditSink,
    environment: EnvironmentMode = EnvironmentMode.DEV,
) -> EnrichmentService:
    """Create an EnrichmentService with all registered connectors.

    Args:
        audit_sink: Audit sink for event emission.
        environment: Deployment environment mode.

    Returns:
        Configured EnrichmentService instance.
    """
    registry = _build_default_registry()

    credential_repo = InMemoryCredentialRepository()
    cache_store = EnrichmentCacheStore()

    return EnrichmentService(
        registry=registry,
        audit_sink=audit_sink,
        credential_repo=credential_repo,
        cache_store=cache_store,
        environment=environment,
    )


def _build_default_registry() -> EnrichmentProviderRegistry:
    """Build the default provider registry with all connectors.

    Returns:
        Registry with all enrichment connectors registered.
    """
    from idis.services.enrichment.connectors.companies_house import CompaniesHouseConnector
    from idis.services.enrichment.connectors.edgar import EdgarConnector
    from idis.services.enrichment.connectors.escwa_catalog import EscwaCatalogConnector
    from idis.services.enrichment.connectors.finnhub import FinnhubConnector
    from idis.services.enrichment.connectors.fmp import FmpConnector
    from idis.services.enrichment.connectors.fred import FredConnector
    from idis.services.enrichment.connectors.gdelt import GdeltConnector
    from idis.services.enrichment.connectors.github import GitHubConnector
    from idis.services.enrichment.connectors.google_news_rss import GoogleNewsRssConnector
    from idis.services.enrichment.connectors.hackernews import HackerNewsConnector
    from idis.services.enrichment.connectors.patentsview import PatentsViewConnector
    from idis.services.enrichment.connectors.qatar_open_data import QatarOpenDataConnector
    from idis.services.enrichment.connectors.wayback import WaybackConnector
    from idis.services.enrichment.connectors.world_bank import WorldBankConnector

    registry = EnrichmentProviderRegistry()

    registry.register(EdgarConnector(), requires_byol=False)
    registry.register(CompaniesHouseConnector(), requires_byol=True)
    registry.register(GitHubConnector(), requires_byol=True)
    registry.register(FredConnector(), requires_byol=True)
    registry.register(FinnhubConnector(), requires_byol=True)
    registry.register(FmpConnector(), requires_byol=True)
    registry.register(WorldBankConnector(), requires_byol=False)
    registry.register(EscwaCatalogConnector(), requires_byol=False)
    registry.register(QatarOpenDataConnector(), requires_byol=False)
    registry.register(HackerNewsConnector(), requires_byol=False)
    registry.register(GdeltConnector(), requires_byol=False)
    registry.register(PatentsViewConnector(), requires_byol=False)
    registry.register(WaybackConnector(), requires_byol=False)
    registry.register(GoogleNewsRssConnector(), requires_byol=False)

    return registry
