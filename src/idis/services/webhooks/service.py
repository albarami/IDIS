"""Webhook service for IDIS.

Provides webhook subscription management per IDIS v6.3:
- API Contracts §6 (Webhooks)
- OpenAPI: POST /v1/webhooks (operationId=createWebhook)
- Traceability Matrix WH-001

Responsibilities:
- Create webhook subscriptions (tenant-scoped)
- NEVER return or log secrets
- Emit audit events for webhook.created
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Connection


@dataclass(frozen=True)
class WebhookSubscription:
    """Webhook subscription data.

    Matches OpenAPI Webhook schema (without secret).

    Attributes:
        webhook_id: UUID of the webhook.
        tenant_id: UUID of the owning tenant.
        url: Destination URL for webhook delivery.
        events: List of event types to subscribe to.
        active: Whether the webhook is active.
        created_at: Creation timestamp (ISO format).
        updated_at: Last update timestamp (ISO format).
    """

    webhook_id: str
    tenant_id: str
    url: str
    events: list[str]
    active: bool
    created_at: str
    updated_at: str


@dataclass
class CreateWebhookInput:
    """Input for creating a webhook subscription.

    Attributes:
        url: Destination URL for webhook delivery.
        events: List of event types to subscribe to.
        secret: Optional shared secret for HMAC signing.
        active: Whether the webhook is active (default True).
    """

    url: str
    events: list[str]
    secret: str | None = None
    active: bool = True


class WebhookService:
    """Service for managing webhook subscriptions.

    Thread-safe, stateless service. All state is persisted to database.
    """

    def create_webhook(
        self,
        tenant_id: str,
        input_data: CreateWebhookInput,
        conn: Connection | None = None,
    ) -> WebhookSubscription:
        """Create a new webhook subscription.

        Args:
            tenant_id: UUID of the tenant creating the webhook.
            input_data: Webhook creation input.
            conn: Optional database connection. If provided, persists to Postgres.

        Returns:
            Created WebhookSubscription (without secret).
        """
        webhook_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        if conn is not None:
            self._persist_webhook(
                conn=conn,
                webhook_id=webhook_id,
                tenant_id=tenant_id,
                url=input_data.url,
                events=input_data.events,
                secret=input_data.secret,
                active=input_data.active,
                created_at=now,
                updated_at=now,
            )

        return WebhookSubscription(
            webhook_id=webhook_id,
            tenant_id=tenant_id,
            url=input_data.url,
            events=list(input_data.events),
            active=input_data.active,
            created_at=now,
            updated_at=now,
        )

    def _persist_webhook(
        self,
        conn: Connection,
        webhook_id: str,
        tenant_id: str,
        url: str,
        events: list[str],
        secret: str | None,
        active: bool,
        created_at: str,
        updated_at: str,
    ) -> None:
        """Persist webhook to database.

        Args:
            conn: Database connection with tenant context set.
            webhook_id: UUID of the webhook.
            tenant_id: UUID of the tenant.
            url: Destination URL.
            events: List of event types.
            secret: Optional shared secret (stored encrypted in production).
            active: Whether active.
            created_at: Creation timestamp.
            updated_at: Update timestamp.
        """
        from sqlalchemy import text

        conn.execute(
            text(
                """
                INSERT INTO webhooks (
                    webhook_id, tenant_id, url, events, secret, active, created_at, updated_at
                ) VALUES (
                    :webhook_id, :tenant_id, :url, :events, :secret, :active,
                    :created_at::timestamptz, :updated_at::timestamptz
                )
                """
            ),
            {
                "webhook_id": webhook_id,
                "tenant_id": tenant_id,
                "url": url,
                "events": events,
                "secret": secret,
                "active": active,
                "created_at": created_at,
                "updated_at": updated_at,
            },
        )

    def get_webhook(
        self,
        webhook_id: str,
        conn: Connection,
    ) -> WebhookSubscription | None:
        """Get a webhook by ID (tenant-scoped via RLS).

        Args:
            webhook_id: UUID of the webhook.
            conn: Database connection with tenant context set.

        Returns:
            WebhookSubscription if found, None otherwise.
            Never returns the secret.
        """
        from sqlalchemy import text

        result = conn.execute(
            text(
                """
                SELECT webhook_id, tenant_id, url, events, active, created_at, updated_at
                FROM webhooks
                WHERE webhook_id = :webhook_id
                """
            ),
            {"webhook_id": webhook_id},
        ).fetchone()

        if result is None:
            return None

        return WebhookSubscription(
            webhook_id=str(result[0]),
            tenant_id=str(result[1]),
            url=result[2],
            events=list(result[3]),
            active=result[4],
            created_at=result[5].isoformat().replace("+00:00", "Z"),
            updated_at=result[6].isoformat().replace("+00:00", "Z"),
        )

    def list_webhooks(
        self,
        conn: Connection,
        active_only: bool = False,
    ) -> list[WebhookSubscription]:
        """List webhooks for current tenant (via RLS).

        Args:
            conn: Database connection with tenant context set.
            active_only: If True, only return active webhooks.

        Returns:
            List of WebhookSubscription (never includes secrets).
        """
        from sqlalchemy import text

        query = """
            SELECT webhook_id, tenant_id, url, events, active, created_at, updated_at
            FROM webhooks
        """
        if active_only:
            query += " WHERE active = true"
        query += " ORDER BY created_at DESC"

        results = conn.execute(text(query)).fetchall()

        return [
            WebhookSubscription(
                webhook_id=str(row[0]),
                tenant_id=str(row[1]),
                url=row[2],
                events=list(row[3]),
                active=row[4],
                created_at=row[5].isoformat().replace("+00:00", "Z"),
                updated_at=row[6].isoformat().replace("+00:00", "Z"),
            )
            for row in results
        ]


_webhook_service: WebhookService | None = None


def get_webhook_service() -> WebhookService:
    """Get the singleton WebhookService instance."""
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = WebhookService()
    return _webhook_service
