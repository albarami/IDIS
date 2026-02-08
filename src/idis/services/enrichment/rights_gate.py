"""Enrichment rights-class gating.

Enforces GREEN/YELLOW/RED licensing rules per compliance model.
Fail-closed: RED providers are blocked in production mode.
Emits audit events on deny.

Spec: IDIS_Enrichment_Connector_Framework_v0_1.md §2
Binding: IDIS_Data_Residency_and_Compliance_Model_v6_3.md §7
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from idis.audit.sink import AuditSink

from idis.services.enrichment.models import RightsClass

logger = logging.getLogger(__name__)


class EnvironmentMode(StrEnum):
    """Deployment environment mode for rights enforcement."""

    DEV = "DEV"
    PROD = "PROD"


class RightsGateError(Exception):
    """Raised when rights gating check fails fatally (e.g., audit emission failure)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RightsDecision:
    """Result of a rights-class gate check.

    Attributes:
        allowed: True if the request is permitted.
        reason: Human-readable explanation.
        rights_class: The rights class that was evaluated.
    """

    allowed: bool
    reason: str
    rights_class: RightsClass


def check_rights(
    *,
    rights_class: RightsClass,
    provider_id: str,
    tenant_id: str,
    environment: EnvironmentMode,
    has_byol_credentials: bool,
    audit_sink: AuditSink,
    request_id: str = "",
) -> RightsDecision:
    """Evaluate rights-class gating for an enrichment request.

    Rules per Data Architecture v3.1 deployment guardrails:
    - GREEN: always allowed in all environments.
    - YELLOW: allowed in DEV; allowed in PROD only with explicit approval
      (currently allowed with logged warning).
    - RED: allowed in DEV only; blocked in PROD unless BYOL credentials present.

    Emits audit event on deny. Fails closed if audit emission fails.

    Args:
        rights_class: Provider's rights classification.
        provider_id: Provider identifier for audit.
        tenant_id: Tenant ID for audit scoping.
        environment: Current deployment environment mode.
        has_byol_credentials: Whether tenant has supplied BYOL credentials.
        audit_sink: Audit sink for event emission (required).
        request_id: Request correlation ID.

    Returns:
        RightsDecision indicating whether the request is allowed.

    Raises:
        RightsGateError: If audit emission fails on a deny decision.
    """
    if rights_class == RightsClass.GREEN:
        return RightsDecision(
            allowed=True,
            reason="GREEN provider: production-ready, no restrictions",
            rights_class=rights_class,
        )

    if rights_class == RightsClass.YELLOW:
        if environment == EnvironmentMode.PROD:
            logger.warning(
                "YELLOW provider %s accessed in PROD by tenant %s",
                provider_id,
                tenant_id,
            )
        return RightsDecision(
            allowed=True,
            reason="YELLOW provider: allowed with attribution requirements",
            rights_class=rights_class,
        )

    # RED: blocked in PROD unless BYOL credentials present
    if environment == EnvironmentMode.PROD and not has_byol_credentials:
        _emit_deny_audit(
            audit_sink=audit_sink,
            provider_id=provider_id,
            tenant_id=tenant_id,
            rights_class=rights_class,
            reason="RED provider blocked in PROD without BYOL credentials",
            request_id=request_id,
        )
        return RightsDecision(
            allowed=False,
            reason="RED provider blocked in PROD without BYOL credentials",
            rights_class=rights_class,
        )

    if environment == EnvironmentMode.DEV:
        logger.warning(
            "RED provider %s accessed in DEV by tenant %s (personal-use tier only)",
            provider_id,
            tenant_id,
        )
        return RightsDecision(
            allowed=True,
            reason="RED provider: allowed in DEV for development only",
            rights_class=rights_class,
        )

    # PROD with BYOL credentials
    return RightsDecision(
        allowed=True,
        reason="RED provider: allowed in PROD with BYOL credentials",
        rights_class=rights_class,
    )


def _emit_deny_audit(
    *,
    audit_sink: AuditSink,
    provider_id: str,
    tenant_id: str,
    rights_class: RightsClass,
    reason: str,
    request_id: str,
) -> None:
    """Emit an audit event for a rights-gate denial. Fail-closed.

    Args:
        audit_sink: Audit sink instance.
        provider_id: Provider that was denied.
        tenant_id: Tenant making the request.
        rights_class: Rights class of the provider.
        reason: Denial reason.
        request_id: Request correlation ID.

    Raises:
        RightsGateError: If audit emission fails.
    """
    event: dict[str, Any] = {
        "event_type": "enrichment.rights_denied",
        "severity": "HIGH",
        "tenant_id": tenant_id,
        "payload": {
            "provider_id": provider_id,
            "rights_class": rights_class.value,
            "reason": reason,
            "request_id": request_id,
        },
    }
    try:
        audit_sink.emit(event)
    except Exception as exc:
        raise RightsGateError(
            f"Fatal: audit emission failed during rights denial for "
            f"provider={provider_id} tenant={tenant_id}: {exc}"
        ) from exc
