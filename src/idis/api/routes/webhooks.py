"""Webhooks routes for IDIS API.

Provides POST /v1/webhooks per OpenAPI spec (operationId=createWebhook).

Per v6.3 requirements:
- ADMIN-only via RBAC (policy configured in policy.py)
- Request body: {url, events[], secret?, active?}
- Response: Webhook object (MUST NOT return secret)
- Emits webhook.created audit event
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from idis.api.auth import RequireTenantContext
from idis.services.webhooks.service import (
    CreateWebhookInput,
    WebhookService,
    get_webhook_service,
)

router = APIRouter(prefix="/v1", tags=["Webhooks"])


class CreateWebhookRequest(BaseModel):
    """Request body for POST /v1/webhooks per OpenAPI CreateWebhookRequest schema."""

    url: Annotated[str, Field(description="Destination URL for webhook delivery")]
    events: Annotated[list[str], Field(description="List of event types to subscribe to")]
    secret: Annotated[
        str | None,
        Field(default=None, description="Optional shared secret for HMAC signing"),
    ] = None
    active: Annotated[bool, Field(default=True, description="Whether the webhook is active")] = True


class WebhookResponse(BaseModel):
    """Response model for webhook per OpenAPI Webhook schema.

    Note: secret is NEVER returned in responses.
    """

    webhook_id: str
    url: str
    events: list[str]
    active: bool
    created_at: str
    updated_at: str | None = None


@router.post("/webhooks", response_model=WebhookResponse, status_code=201)
def create_webhook(
    request_body: CreateWebhookRequest,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> WebhookResponse:
    """Create a new webhook subscription.

    Requires ADMIN role (enforced by RBAC middleware via policy.py).

    Args:
        request_body: Webhook creation request with url and events.
        tenant_ctx: Injected tenant context from auth dependency.
        request: FastAPI request for accessing db_conn.

    Returns:
        Created Webhook object (without secret).
    """
    service: WebhookService = get_webhook_service()

    db_conn = getattr(request.state, "db_conn", None)

    input_data = CreateWebhookInput(
        url=request_body.url,
        events=request_body.events,
        secret=request_body.secret,
        active=request_body.active,
    )

    webhook = service.create_webhook(
        tenant_id=tenant_ctx.tenant_id,
        input_data=input_data,
        conn=db_conn,
    )

    return WebhookResponse(
        webhook_id=webhook.webhook_id,
        url=webhook.url,
        events=webhook.events,
        active=webhook.active,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
    )
