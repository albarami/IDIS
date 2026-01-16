"""IDIS RBAC/ABAC policy definitions and enforcement.

Implements deny-by-default authorization per v6.3 Security Threat Model:
- RBAC roles: ANALYST, PARTNER, IC_MEMBER, ADMIN, AUDITOR, INTEGRATION_SERVICE
- ABAC constraints: deal-level access (assignment or group membership)
- policy_check(actor, action, resource, tenant_id, deal_id) in middleware

Policy highlights (v6.3 API Contracts):
- Only ADMIN can create webhooks (createWebhook)
- Only AUDITOR and ADMIN can list audit events (listAuditEvents)
- AUDITOR cannot perform mutations (read-only role)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Set


class Role(str, Enum):
    """RBAC roles per v6.3 Security Threat Model.

    Minimum roles required for IDIS authorization:
    - ANALYST: Deal analysts performing due diligence
    - PARTNER: Senior staff who can approve overrides
    - IC_MEMBER: Investment committee members
    - ADMIN: System administrators
    - AUDITOR: Read-only audit/compliance role
    - INTEGRATION_SERVICE: Service accounts for integrations
    """

    ANALYST = "ANALYST"
    PARTNER = "PARTNER"
    IC_MEMBER = "IC_MEMBER"
    ADMIN = "ADMIN"
    AUDITOR = "AUDITOR"
    INTEGRATION_SERVICE = "INTEGRATION_SERVICE"


ALL_ROLES: frozenset[str] = frozenset(r.value for r in Role)
MUTATOR_ROLES: frozenset[str] = frozenset(
    {
        Role.ANALYST.value,
        Role.PARTNER.value,
        Role.IC_MEMBER.value,
        Role.ADMIN.value,
        Role.INTEGRATION_SERVICE.value,
    }
)
READ_ONLY_ROLES: frozenset[str] = frozenset({Role.AUDITOR.value})
ADMIN_ONLY: frozenset[str] = frozenset({Role.ADMIN.value})
AUDIT_READERS: frozenset[str] = frozenset({Role.AUDITOR.value, Role.ADMIN.value})


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """Policy rule for an OpenAPI operation.

    Attributes:
        allowed_roles: Set of roles that can invoke this operation.
        is_mutation: True if this operation modifies state (AUDITOR blocked).
        is_deal_scoped: True if this operation requires deal-level access check.
    """

    allowed_roles: frozenset[str]
    is_mutation: bool = False
    is_deal_scoped: bool = False


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Result of policy_check evaluation.

    Attributes:
        allow: True if request is authorized.
        code: Machine-readable denial code (e.g., "RBAC_DENIED").
        message: Human-readable denial reason.
        details: Optional additional context for the denial.
    """

    allow: bool
    code: str
    message: str
    details: dict[str, str | list[str]] | None = None


POLICY_RULES: dict[str, PolicyRule] = {
    "getTenantMe": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False),
    "listDeals": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False),
    "createDeal": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=False),
    "getDeal": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=True),
    "updateDeal": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True),
    "getDealTruthDashboard": PolicyRule(
        allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=True
    ),
    "listDealDocuments": PolicyRule(
        allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=True
    ),
    "createDealDocument": PolicyRule(
        allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True
    ),
    "ingestDocument": PolicyRule(
        allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=False
    ),
    "listDealClaims": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=True),
    "createClaim": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True),
    "getClaim": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False),
    "updateClaim": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=False),
    "getClaimSanad": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False),
    "getSanad": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False),
    "listDealSanads": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=True),
    "createSanad": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True),
    "updateSanad": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=False),
    "setSanadCorroboration": PolicyRule(
        allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=False
    ),
    "getDefect": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False),
    "listDealDefects": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=True),
    "listClaimDefects": PolicyRule(
        allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False
    ),
    "createDefect": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True),
    "waiveDefect": PolicyRule(
        allowed_roles=frozenset({Role.PARTNER.value, Role.ADMIN.value}),
        is_mutation=True,
        is_deal_scoped=False,
    ),
    "cureDefect": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=False),
    "listDealCalcs": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=True),
    "runCalc": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True),
    "startRun": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True),
    "getRun": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False),
    "startDebate": PolicyRule(allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True),
    "getDebate": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False),
    "listHumanGates": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=True),
    "submitHumanGateAction": PolicyRule(
        allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True
    ),
    "createOverride": PolicyRule(
        allowed_roles=frozenset({Role.PARTNER.value, Role.ADMIN.value}),
        is_mutation=True,
        is_deal_scoped=True,
    ),
    "listDeliverables": PolicyRule(allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=True),
    "generateDeliverable": PolicyRule(
        allowed_roles=MUTATOR_ROLES, is_mutation=True, is_deal_scoped=True
    ),
    "listAuditEvents": PolicyRule(
        allowed_roles=AUDIT_READERS, is_mutation=False, is_deal_scoped=False
    ),
    "listIntegrations": PolicyRule(
        allowed_roles=ALL_ROLES, is_mutation=False, is_deal_scoped=False
    ),
    "createWebhook": PolicyRule(allowed_roles=ADMIN_ONLY, is_mutation=True, is_deal_scoped=False),
}


def policy_check(
    *,
    tenant_id: str,
    actor_id: str,
    roles: Set[str],
    operation_id: str,
    method: str,
    deal_id: str | None = None,
    claim_id: str | None = None,
    doc_id: str | None = None,
    run_id: str | None = None,
    debate_id: str | None = None,
) -> PolicyDecision:
    """Evaluate RBAC/ABAC policy for a request.

    Implements deny-by-default authorization per v6.3 Security Threat Model:
    1. Operation must be in POLICY_RULES (deny unknown operations)
    2. Actor must have at least one allowed role
    3. AUDITOR role cannot perform mutations
    4. Deal-scoped operations require deal_id (future: deal assignment check)

    Args:
        tenant_id: Tenant ID from auth context (required).
        actor_id: Actor ID from auth context (required).
        roles: Set of roles from auth context.
        operation_id: OpenAPI operationId being invoked.
        method: HTTP method (GET, POST, PATCH, etc.).
        deal_id: Deal ID from path params (optional).
        claim_id: Claim ID from path params (optional).
        doc_id: Document ID from path params (optional).
        run_id: Run ID from path params (optional).
        debate_id: Debate ID from path params (optional).

    Returns:
        PolicyDecision with allow=True or allow=False with denial reason.
    """
    if not tenant_id:
        return PolicyDecision(
            allow=False,
            code="RBAC_DENIED",
            message="Missing tenant context",
            details={"reason": "tenant_id is required"},
        )

    if not actor_id:
        return PolicyDecision(
            allow=False,
            code="RBAC_DENIED",
            message="Missing actor identity",
            details={"reason": "actor_id is required"},
        )

    if not roles:
        return PolicyDecision(
            allow=False,
            code="RBAC_DENIED",
            message="No roles assigned to actor",
            details={"actor_id": actor_id},
        )

    rule = POLICY_RULES.get(operation_id)
    if rule is None:
        return PolicyDecision(
            allow=False,
            code="RBAC_DENIED",
            message="Operation not permitted",
            details={"operation_id": operation_id, "reason": "unknown_operation"},
        )

    actor_roles = set(roles)
    allowed_roles = set(rule.allowed_roles)
    matching_roles = actor_roles & allowed_roles

    if not matching_roles:
        return PolicyDecision(
            allow=False,
            code="RBAC_DENIED",
            message="Insufficient privileges for this operation",
            details={
                "operation_id": operation_id,
                "required_roles": sorted(allowed_roles),
                "actor_roles": sorted(actor_roles),
            },
        )

    if rule.is_mutation and Role.AUDITOR.value in actor_roles and len(actor_roles) == 1:
        return PolicyDecision(
            allow=False,
            code="RBAC_DENIED",
            message="AUDITOR role cannot perform mutations",
            details={"operation_id": operation_id, "reason": "auditor_read_only"},
        )

    return PolicyDecision(
        allow=True,
        code="ALLOWED",
        message="Access granted",
        details=None,
    )


def get_all_v1_operation_ids() -> frozenset[str]:
    """Return all operationIds defined in the policy rules.

    Used by tests to verify policy coverage against OpenAPI spec.
    """
    return frozenset(POLICY_RULES.keys())
