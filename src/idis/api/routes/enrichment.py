"""Enrichment API routes for IDIS.

Provides POST /v1/enrichment/fetch and GET /v1/enrichment/providers
per the enrichment connector framework spec.

All requests flow through EnrichmentService orchestrator.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from idis.api.auth import RequireTenantContext
from idis.services.enrichment.models import (
    EnrichmentPurpose,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentStatus,
    EntityType,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Enrichment"])


class EnrichmentFetchRequest(BaseModel):
    """Request body for POST /v1/enrichment/fetch."""

    provider_id: str
    entity_type: EntityType
    query: EnrichmentQuery
    requested_fields: list[str] | None = None
    purpose: EnrichmentPurpose = EnrichmentPurpose.DUE_DILIGENCE


class EnrichmentProvenanceResponse(BaseModel):
    """Provenance metadata in enrichment response."""

    source_id: str
    retrieved_at: str
    rights_class: str
    raw_ref_hash: str
    identifiers_used: dict[str, str] = Field(default_factory=dict)


class EnrichmentFetchResponse(BaseModel):
    """Response body for POST /v1/enrichment/fetch."""

    status: EnrichmentStatus
    normalized: dict[str, Any] = Field(default_factory=dict)
    provenance: EnrichmentProvenanceResponse | None = None
    expires_at: str | None = None


class ProviderInfo(BaseModel):
    """Provider information for GET /v1/enrichment/providers."""

    provider_id: str
    rights_class: str
    requires_byol: bool
    cache_ttl_seconds: int
    cache_no_store: bool


class ProviderListResponse(BaseModel):
    """Response body for GET /v1/enrichment/providers."""

    providers: list[ProviderInfo]


def _get_enrichment_service(request: Request) -> Any:
    """Get the enrichment service from app state.

    Creates a default instance if not configured (fail-closed: requires audit sink).

    Args:
        request: FastAPI request.

    Returns:
        EnrichmentService instance.

    Raises:
        HTTPException: If enrichment service cannot be created.
    """
    from fastapi import HTTPException

    from idis.audit.sink import get_audit_sink
    from idis.services.enrichment.service import create_default_enrichment_service

    svc = getattr(request.app.state, "enrichment_service", None)
    if svc is not None:
        return svc

    audit_sink = getattr(request.app.state, "audit_sink", None)
    if audit_sink is None:
        audit_sink = get_audit_sink()

    try:
        svc = create_default_enrichment_service(audit_sink=audit_sink)
        request.app.state.enrichment_service = svc
        return svc
    except Exception as exc:
        logger.error("Failed to create enrichment service: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Enrichment service unavailable",
        ) from exc


@router.post(
    "/enrichment/fetch",
    response_model=EnrichmentFetchResponse,
    status_code=200,
)
def fetch_enrichment(
    request_body: EnrichmentFetchRequest,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> EnrichmentFetchResponse:
    """Fetch enrichment data from a registered provider.

    Orchestration flow: rights check → cache → BYOL creds → provider fetch → audit.

    Args:
        request_body: Enrichment fetch request with provider, entity, and query.
        request: FastAPI request for service access.
        tenant_ctx: Injected tenant context from auth.

    Returns:
        Enrichment result with status, normalized data, and provenance.
    """
    from idis.services.enrichment.service import EnrichmentServiceError

    svc = _get_enrichment_service(request)

    enrichment_request = EnrichmentRequest(
        tenant_id=tenant_ctx.tenant_id,
        entity_type=request_body.entity_type,
        query=request_body.query,
        requested_fields=request_body.requested_fields,
        purpose=request_body.purpose,
    )

    request_id = getattr(request.state, "request_id", "")

    try:
        result = svc.enrich(
            provider_id=request_body.provider_id,
            request=enrichment_request,
            request_id=request_id,
        )
    except EnrichmentServiceError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail=str(exc)) from exc

    provenance_resp = None
    if result.provenance is not None:
        provenance_resp = EnrichmentProvenanceResponse(
            source_id=result.provenance.source_id,
            retrieved_at=result.provenance.retrieved_at.isoformat(),
            rights_class=result.provenance.rights_class.value,
            raw_ref_hash=result.provenance.raw_ref_hash,
            identifiers_used=result.provenance.identifiers_used,
        )

    return EnrichmentFetchResponse(
        status=result.status,
        normalized=result.normalized,
        provenance=provenance_resp,
        expires_at=result.expires_at.isoformat() if result.expires_at else None,
    )


@router.get(
    "/enrichment/providers",
    response_model=ProviderListResponse,
)
def list_enrichment_providers(
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> ProviderListResponse:
    """List all registered enrichment providers.

    Args:
        request: FastAPI request for service access.
        tenant_ctx: Injected tenant context from auth.

    Returns:
        List of registered provider descriptors.
    """
    svc = _get_enrichment_service(request)
    providers = svc.list_providers()

    return ProviderListResponse(
        providers=[
            ProviderInfo(
                provider_id=p["provider_id"],
                rights_class=p["rights_class"],
                requires_byol=p["requires_byol"],
                cache_ttl_seconds=p["cache_ttl_seconds"],
                cache_no_store=p["cache_no_store"],
            )
            for p in providers
        ]
    )
