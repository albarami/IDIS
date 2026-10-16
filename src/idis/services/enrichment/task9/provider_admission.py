"""Pure, ordered admission contracts for applicable Task 9 provider calls."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from idis.services.enrichment.models import RightsClass
from idis.services.enrichment.task9.provider_catalog import (
    PROVIDER_CATALOG,
    PROVIDER_CATALOG_VERSION,
    ProviderSpec,
    RightsRequirement,
)
from idis.services.enrichment.task9.rights_index_policy import (
    PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_COMPOUND_SELECTOR_PROVIDERS = frozenset(
    {"fred", "world_bank", "qatar_open_data", "perplexity"}
)


class ListingState(StrEnum):
    """Governed company listing state."""

    LISTED = "LISTED"
    PRIVATE = "PRIVATE"
    UNLISTED = "UNLISTED"
    UNKNOWN = "UNKNOWN"


class IdentityVerificationBasis(StrEnum):
    """Source of an identity snapshot's verification claim."""

    VERIFIED_EVIDENCE = "VERIFIED_EVIDENCE"
    OPERATOR_SEED = "OPERATOR_SEED"
    DEAL_UUID = "DEAL_UUID"


class ApplicabilityStatus(StrEnum):
    """Immutable provider applicability decision."""

    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RightsStatus(StrEnum):
    """Lifecycle state of a governed rights/entitlement version."""

    ACTIVE = "ACTIVE"
    UNAPPROVED = "UNAPPROVED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class CacheDisposition(StrEnum):
    """Rights-compatible cache decision made before a provider call."""

    MISS = "MISS"
    BYPASS = "BYPASS"
    HIT = "HIT"


class ProviderAdmissionError(RuntimeError):
    """Stable, secret-free provider admission failure."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}:{reason}")


@dataclass(frozen=True, slots=True)
class IdentifierSelectorRule:
    """Catalog-derived DNF selector rule; each alternative is an all-of group."""

    provider_id: str
    alternatives: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class ProviderAdmissionRequest:
    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    expected_identity_version: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedIdentitySnapshot:
    tenant_id: str
    deal_id: str
    identity_id: str
    identity_version: str
    identity_rule_version: str
    listing_state: ListingState
    verification_basis: IdentityVerificationBasis
    identifiers: tuple[tuple[str, str], ...]
    evidence_ids: tuple[str, ...]
    evidence_set_sha256: str
    provenance_ids: tuple[str, ...]
    provenance_set_sha256: str
    identity_hash: str


@dataclass(frozen=True, slots=True)
class ApplicabilityDecision:
    decision_id: str
    provider_id: str
    status: ApplicabilityStatus
    identity_id: str
    identity_version: str
    identity_hash: str
    rule_version: str
    rule_sha256: str
    decision_hash: str
    decision_content_sha256: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class RightsSnapshot:
    tenant_id: str
    rights_approval_id: str
    provider_id: str
    environment: str
    rights_class: RightsClass
    rights_requirement: RightsRequirement
    status: RightsStatus
    rights_version_hash: str
    credential_version_id: str | None
    expires_at: datetime
    no_store_policy_version: str
    attribution_policy_version: str


def derive_external_context_index_allowed(rights: RightsSnapshot) -> bool:
    """Derive Graph/RAG eligibility from one exact governed rights row."""

    if not isinstance(rights, RightsSnapshot):
        raise ProviderAdmissionError(
            "ENRICHMENT_RIGHTS_BLOCKED",
            "rights_index_policy_invalid",
        )
    spec = PROVIDER_CATALOG.get(rights.provider_id)
    policy = PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY.get(rights.provider_id)
    code = _rights_error_code(spec) if spec is not None else "ENRICHMENT_RIGHTS_BLOCKED"
    if (
        spec is None
        or policy is None
        or rights.environment != "PROD"
        or rights.status is not RightsStatus.ACTIVE
        or rights.rights_class is not spec.rights_class
        or rights.rights_requirement is not spec.rights_requirement
        or rights.rights_class is not policy.rights_class
        or rights.rights_requirement is not policy.rights_requirement
        or rights.no_store_policy_version
        not in {
            policy.no_store_policy_version,
            "external-context-store-v1",
            "external-context-no-store-v1",
        }
        or rights.attribution_policy_version != policy.attribution_policy_version
        or not _is_sha256(rights.rights_version_hash)
    ):
        raise ProviderAdmissionError(code, "rights_index_policy_invalid")
    return (
        spec.rag_external_context and rights.no_store_policy_version == "external-context-store-v1"
    )


@dataclass(frozen=True, slots=True)
class CredentialVersionSnapshot:
    """Secret-free metadata for one encrypted, atomic credential version."""

    tenant_id: str
    credential_version_id: str
    provider_id: str
    credential_keys: tuple[str, ...]
    encrypted: bool
    active: bool
    ciphertext_sha256: str
    bundle_version_hash: str


@dataclass(frozen=True, slots=True)
class BudgetReservationSnapshot:
    tenant_id: str
    deal_id: str
    run_id: str
    reservation_id: str
    provider_id: str
    policy_hash: str
    signed_policy_id: str
    active: bool
    signature_verified: bool
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CacheDecisionSnapshot:
    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    identity_id: str
    identity_version: str
    identity_hash: str
    required_identifiers: tuple[tuple[str, str], ...]
    identifiers_used_sha256: str
    credential_version_id: str | None
    catalog_version: str
    decision_id: str
    disposition: CacheDisposition
    rights_approval_id: str
    rights_version_hash: str
    rights_compatible: bool
    policy_hash: str
    cached_outcome_id: str | None = None
    cached_outcome_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCallIntent:
    """Secret-free authorization to construct one provider request."""

    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    identity_id: str
    identity_version: str
    identity_rule_version: str
    identity_listing_state: ListingState
    identity_evidence_ids: tuple[str, ...]
    identity_evidence_set_sha256: str
    identity_provenance_ids: tuple[str, ...]
    identity_provenance_set_sha256: str
    identity_hash: str
    applicability_decision_id: str
    applicability_decision_hash: str
    applicability_decision_content_sha256: str
    applicability_rule_version: str
    applicability_rule_sha256: str
    applicability_reason_code: str
    required_identifiers: tuple[tuple[str, str], ...]
    identifiers_used_sha256: str
    request_binding_sha256: str
    connector_identity: str
    connector_version_hash: str
    request_builder_identity: str
    request_builder_version_hash: str
    rights_approval_id: str
    rights_version_hash: str
    rights_class: RightsClass
    no_store_policy_version: str
    attribution_policy_version: str
    credential_version_id: str | None
    credential_bundle_hash: str | None
    budget_reservation_id: str
    budget_policy_hash: str
    cache_decision_id: str
    cache_policy_hash: str
    external_context_index_allowed: bool
    admission_proof_sha256: str
    cache_disposition: CacheDisposition = CacheDisposition.MISS
    catalog_version: str = field(default=PROVIDER_CATALOG_VERSION, init=False)


@dataclass(frozen=True, slots=True)
class ProviderNotApplicableIntent:
    """Identity/rule-bound zero-call N/A admission result."""

    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    identity_id: str
    identity_version: str
    identity_rule_version: str
    identity_evidence_ids: tuple[str, ...]
    identity_evidence_set_sha256: str
    identity_provenance_ids: tuple[str, ...]
    identity_provenance_set_sha256: str
    identity_hash: str
    listing_state: ListingState
    applicability_decision_id: str
    applicability_decision_content_sha256: str
    decision: ApplicabilityDecision
    admission_proof_sha256: str
    zero_call: bool = field(default=True, init=False)
    catalog_version: str = field(default=PROVIDER_CATALOG_VERSION, init=False)


@dataclass(frozen=True, slots=True)
class ProviderCacheHitIntent:
    """Rights-bound reuse of one durable outcome without a provider request."""

    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    identity_id: str
    identity_version: str
    identity_rule_version: str
    identity_listing_state: ListingState
    identity_evidence_ids: tuple[str, ...]
    identity_evidence_set_sha256: str
    identity_provenance_ids: tuple[str, ...]
    identity_provenance_set_sha256: str
    identity_hash: str
    applicability_decision_id: str
    applicability_decision_hash: str
    applicability_decision_content_sha256: str
    applicability_rule_version: str
    applicability_rule_sha256: str
    applicability_reason_code: str
    required_identifiers: tuple[tuple[str, str], ...]
    identifiers_used_sha256: str
    request_binding_sha256: str
    connector_identity: str
    connector_version_hash: str
    request_builder_identity: str
    request_builder_version_hash: str
    rights_approval_id: str
    rights_version_hash: str
    rights_class: RightsClass
    no_store_policy_version: str
    attribution_policy_version: str
    credential_version_id: str | None
    credential_bundle_hash: str | None
    budget_reservation_id: str
    budget_policy_hash: str
    cache_decision_id: str
    cache_policy_hash: str
    cached_outcome_id: str
    cached_outcome_sha256: str
    external_context_index_allowed: bool
    admission_proof_sha256: str
    no_provider_call: bool = field(default=True, init=False)
    catalog_version: str = field(default=PROVIDER_CATALOG_VERSION, init=False)


class IdentityResolver(Protocol):
    def resolve_identity(self, request: ProviderAdmissionRequest) -> VerifiedIdentitySnapshot: ...


class ApplicabilityResolver(Protocol):
    def decide_applicability(
        self, identity: VerifiedIdentitySnapshot, provider_id: str
    ) -> ApplicabilityDecision: ...


class RightsResolver(Protocol):
    def resolve_rights(self, provider_id: str, tenant_id: str) -> RightsSnapshot: ...


class CredentialResolver(Protocol):
    def resolve_credentials(
        self, provider_id: str, tenant_id: str
    ) -> CredentialVersionSnapshot | None: ...


class BudgetReserver(Protocol):
    def reserve_budget(
        self, provider_id: str, tenant_id: str, deal_id: str, run_id: str
    ) -> BudgetReservationSnapshot: ...


class CacheResolver(Protocol):
    def decide_cache(
        self,
        provider_id: str,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        rights: RightsSnapshot,
        identity: VerifiedIdentitySnapshot,
        required_identifiers: tuple[tuple[str, str], ...],
        credential_version_id: str | None,
        catalog_version: str,
    ) -> CacheDecisionSnapshot: ...


@dataclass(frozen=True, slots=True)
class AdmissionCollaborators:
    identity_resolver: IdentityResolver
    applicability_resolver: ApplicabilityResolver
    rights_resolver: RightsResolver
    credential_resolver: CredentialResolver
    budget_reserver: BudgetReserver
    cache_resolver: CacheResolver


def canonical_identifiers_sha256(identifiers: tuple[tuple[str, str], ...]) -> str:
    """Return the canonical digest of one exact identifier selector set."""

    if not _valid_identifier_pairs(identifiers):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "identifier_invalid")
    canonical = sorted((key, value) for key, value in identifiers)
    return _sha256_json(canonical)


def canonical_identity_evidence_set_sha256(evidence_ids: tuple[str, ...]) -> str:
    """Hash one unique identity-evidence ID set with an explicit domain label."""

    return _canonical_identity_reference_set_sha256("identity-evidence-set-v1", evidence_ids)


def canonical_identity_provenance_set_sha256(provenance_ids: tuple[str, ...]) -> str:
    """Hash one unique identity-provenance ID set with an explicit domain label."""

    return _canonical_identity_reference_set_sha256("identity-provenance-set-v1", provenance_ids)


def canonical_verified_identity_hash(identity: VerifiedIdentitySnapshot) -> str:
    """Recompute the complete immutable company-identity content hash."""

    if (
        not isinstance(identity, VerifiedIdentitySnapshot)
        or not isinstance(identity.listing_state, ListingState)
        or not isinstance(identity.verification_basis, IdentityVerificationBasis)
        or not _valid_identifier_pairs(identity.identifiers, allow_empty=True)
        or any(
            not _is_nonempty_text(value)
            for value in (
                identity.tenant_id,
                identity.deal_id,
                identity.identity_id,
                identity.identity_version,
                identity.identity_rule_version,
            )
        )
    ):
        raise ProviderAdmissionError("COMPANY_IDENTITY_UNVERIFIED", "identity_invalid")
    evidence_sha256 = canonical_identity_evidence_set_sha256(identity.evidence_ids)
    provenance_sha256 = canonical_identity_provenance_set_sha256(identity.provenance_ids)
    return _sha256_json(
        {
            "schema": "verified-company-identity-v1",
            "tenant_id": identity.tenant_id,
            "deal_id": identity.deal_id,
            "identity_id": identity.identity_id,
            "identity_version": identity.identity_version,
            "identity_rule_version": identity.identity_rule_version,
            "listing_state": identity.listing_state.value,
            "verification_basis": identity.verification_basis.value,
            "identifiers": sorted(identity.identifiers),
            "evidence_ids": sorted(identity.evidence_ids),
            "evidence_set_sha256": evidence_sha256,
            "provenance_ids": sorted(identity.provenance_ids),
            "provenance_set_sha256": provenance_sha256,
        }
    )


def canonical_applicability_decision_content_sha256(
    decision: ApplicabilityDecision,
) -> str:
    """Hash the immutable applicability decision content, excluding its IDs/hashes."""

    status = decision.status.value if isinstance(decision.status, ApplicabilityStatus) else None
    return _sha256_json(
        {
            "provider_id": decision.provider_id,
            "status": status,
            "identity_id": decision.identity_id,
            "identity_version": decision.identity_version,
            "identity_hash": decision.identity_hash,
            "rule_version": decision.rule_version,
            "rule_sha256": decision.rule_sha256,
            "reason_code": decision.reason_code,
        }
    )


def identifier_selector_rule_for(provider_id: str) -> IdentifierSelectorRule:
    """Return the typed selector alternatives owned by the provider catalog."""

    spec = PROVIDER_CATALOG.get(provider_id)
    if spec is None:
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "provider_unknown")
    catalog_alternatives = getattr(spec, "identity_selector_alternatives", None)
    if catalog_alternatives is not None:
        try:
            alternatives = tuple(
                tuple(alternative.required_fields) for alternative in catalog_alternatives
            )
        except (AttributeError, TypeError):
            raise ProviderAdmissionError(
                "ENRICHMENT_PROVIDER_INPUT_MISSING", "identifier_rule_invalid"
            ) from None
    elif spec.provider_id in _LEGACY_COMPOUND_SELECTOR_PROVIDERS:
        alternatives = (tuple(spec.required_identity_fields),)
    else:
        alternatives = tuple((field_name,) for field_name in spec.required_identity_fields)
    flattened = tuple(field_name for group in alternatives for field_name in group)
    if (
        not alternatives
        or any(
            not isinstance(group, tuple)
            or not group
            or len(set(group)) != len(group)
            or any(not _is_nonempty_text(field_name) for field_name in group)
            for group in alternatives
        )
        or set(flattened) != set(spec.required_identity_fields)
    ):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "identifier_rule_invalid")
    return IdentifierSelectorRule(provider_id=provider_id, alternatives=alternatives)


def request_binding_sha256_for(
    *,
    provider_id: str,
    required_identifiers: tuple[tuple[str, str], ...],
    identity_id: str,
    identity_version: str,
    identity_rule_version: str,
    identity_listing_state: ListingState,
    identity_evidence_ids: tuple[str, ...],
    identity_evidence_set_sha256: str,
    identity_provenance_ids: tuple[str, ...],
    identity_provenance_set_sha256: str,
    identity_hash: str,
    applicability_decision_id: str,
    applicability_decision_content_sha256: str,
    applicability_rule_version: str,
    applicability_rule_sha256: str,
    applicability_reason_code: str,
    connector_identity: str,
    connector_version_hash: str,
    request_builder_identity: str,
    request_builder_version_hash: str,
    catalog_version: str,
) -> str:
    """Bind a future provider request to admitted selector and decision lineage."""

    return _sha256_json(
        {
            "schema": "provider-request-binding-v1",
            "provider_id": provider_id,
            "required_identifiers": sorted(required_identifiers),
            "identity_id": identity_id,
            "identity_version": identity_version,
            "identity_rule_version": identity_rule_version,
            "identity_listing_state": identity_listing_state.value,
            "identity_evidence_ids": sorted(identity_evidence_ids),
            "identity_evidence_set_sha256": identity_evidence_set_sha256,
            "identity_provenance_ids": sorted(identity_provenance_ids),
            "identity_provenance_set_sha256": identity_provenance_set_sha256,
            "identity_hash": identity_hash,
            "applicability_decision_id": applicability_decision_id,
            "applicability_decision_content_sha256": (applicability_decision_content_sha256),
            "applicability_rule_version": applicability_rule_version,
            "applicability_rule_sha256": applicability_rule_sha256,
            "applicability_reason_code": applicability_reason_code,
            "connector_identity": connector_identity,
            "connector_version_hash": connector_version_hash,
            "request_builder_identity": request_builder_identity,
            "request_builder_version_hash": request_builder_version_hash,
            "catalog_version": catalog_version,
        }
    )


def admission_proof_matches(
    intent: ProviderCallIntent | ProviderNotApplicableIntent | ProviderCacheHitIntent,
) -> bool:
    """Return whether an admission intent still matches its content-bound proof."""

    try:
        expected = _admission_proof_sha256(intent)
    except (AttributeError, TypeError, ValueError):
        return False
    return _is_sha256(intent.admission_proof_sha256) and intent.admission_proof_sha256 == expected


def request_binding_matches(intent: ProviderCallIntent | ProviderCacheHitIntent) -> bool:
    """Return whether the request binding still matches the admission lineage."""

    try:
        expected = request_binding_sha256_for(
            provider_id=intent.provider_id,
            required_identifiers=intent.required_identifiers,
            identity_id=intent.identity_id,
            identity_version=intent.identity_version,
            identity_rule_version=intent.identity_rule_version,
            identity_listing_state=intent.identity_listing_state,
            identity_evidence_ids=intent.identity_evidence_ids,
            identity_evidence_set_sha256=intent.identity_evidence_set_sha256,
            identity_provenance_ids=intent.identity_provenance_ids,
            identity_provenance_set_sha256=intent.identity_provenance_set_sha256,
            identity_hash=intent.identity_hash,
            applicability_decision_id=intent.applicability_decision_id,
            applicability_decision_content_sha256=(intent.applicability_decision_content_sha256),
            applicability_rule_version=intent.applicability_rule_version,
            applicability_rule_sha256=intent.applicability_rule_sha256,
            applicability_reason_code=intent.applicability_reason_code,
            connector_identity=intent.connector_identity,
            connector_version_hash=intent.connector_version_hash,
            request_builder_identity=intent.request_builder_identity,
            request_builder_version_hash=intent.request_builder_version_hash,
            catalog_version=intent.catalog_version,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return _is_sha256(intent.request_binding_sha256) and intent.request_binding_sha256 == expected


def admit_provider(
    request: ProviderAdmissionRequest,
    collaborators: AdmissionCollaborators,
) -> ProviderCallIntent | ProviderNotApplicableIntent | ProviderCacheHitIntent:
    """Evaluate the exact identity-to-cache admission sequence and emit an intent."""

    _validate_request(request)
    spec = PROVIDER_CATALOG.get(request.provider_id)
    if spec is None:
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "provider_unknown")

    try:
        identity = collaborators.identity_resolver.resolve_identity(request)
    except Exception:
        raise ProviderAdmissionError(
            "COMPANY_IDENTITY_UNVERIFIED", "identity_resolution_failed"
        ) from None
    _validate_identity(request, identity)

    try:
        decision = collaborators.applicability_resolver.decide_applicability(
            identity, request.provider_id
        )
    except Exception:
        raise ProviderAdmissionError(
            "ENRICHMENT_PROVIDER_INPUT_MISSING", "applicability_resolution_failed"
        ) from None
    _validate_applicability(identity, spec, decision)
    if decision.status is ApplicabilityStatus.NOT_APPLICABLE:
        _validate_not_applicable(identity, spec, decision)
        intent = ProviderNotApplicableIntent(
            tenant_id=request.tenant_id,
            deal_id=request.deal_id,
            run_id=request.run_id,
            provider_id=request.provider_id,
            identity_id=identity.identity_id,
            identity_version=identity.identity_version,
            identity_rule_version=identity.identity_rule_version,
            identity_evidence_ids=identity.evidence_ids,
            identity_evidence_set_sha256=identity.evidence_set_sha256,
            identity_provenance_ids=identity.provenance_ids,
            identity_provenance_set_sha256=identity.provenance_set_sha256,
            identity_hash=identity.identity_hash,
            listing_state=identity.listing_state,
            applicability_decision_id=decision.decision_id,
            applicability_decision_content_sha256=decision.decision_content_sha256,
            decision=decision,
            admission_proof_sha256="",
        )
        return replace(intent, admission_proof_sha256=_admission_proof_sha256(intent))

    identifiers = _required_identifiers(identity, spec)
    identifiers_sha256 = canonical_identifiers_sha256(identifiers)
    try:
        rights = collaborators.rights_resolver.resolve_rights(
            request.provider_id, request.tenant_id
        )
    except Exception:
        raise ProviderAdmissionError(_rights_error_code(spec), "rights_resolution_failed") from None
    _validate_rights(request, spec, rights)

    credential: CredentialVersionSnapshot | None = None
    if spec.credential_fields:
        try:
            credential = collaborators.credential_resolver.resolve_credentials(
                request.provider_id, request.tenant_id
            )
        except Exception:
            raise ProviderAdmissionError(
                "ENRICHMENT_PROVIDER_INPUT_MISSING", "credential_resolution_failed"
            ) from None
        _validate_credentials(request, spec, credential)
    _validate_rights_credential_binding(spec, rights, credential)

    try:
        budget = collaborators.budget_reserver.reserve_budget(
            request.provider_id, request.tenant_id, request.deal_id, request.run_id
        )
    except Exception:
        raise ProviderAdmissionError(
            "PROVIDER_BUDGET_CONFIG_BLOCKED", "budget_reservation_failed"
        ) from None
    _validate_budget(request, budget)
    credential_version_id = credential.credential_version_id if credential else None
    try:
        cache = collaborators.cache_resolver.decide_cache(
            request.provider_id,
            request.tenant_id,
            request.deal_id,
            request.run_id,
            rights,
            identity,
            identifiers,
            credential_version_id,
            PROVIDER_CATALOG_VERSION,
        )
    except Exception:
        raise ProviderAdmissionError(
            "ENRICHMENT_PROVENANCE_MISSING", "cache_resolution_failed"
        ) from None
    _validate_cache(
        request,
        rights,
        identity,
        identifiers,
        identifiers_sha256,
        credential_version_id,
        cache,
    )
    binding_sha256 = request_binding_sha256_for(
        provider_id=request.provider_id,
        required_identifiers=identifiers,
        identity_id=identity.identity_id,
        identity_version=identity.identity_version,
        identity_rule_version=identity.identity_rule_version,
        identity_listing_state=identity.listing_state,
        identity_evidence_ids=identity.evidence_ids,
        identity_evidence_set_sha256=identity.evidence_set_sha256,
        identity_provenance_ids=identity.provenance_ids,
        identity_provenance_set_sha256=identity.provenance_set_sha256,
        identity_hash=identity.identity_hash,
        applicability_decision_id=decision.decision_id,
        applicability_decision_content_sha256=decision.decision_content_sha256,
        applicability_rule_version=decision.rule_version,
        applicability_rule_sha256=decision.rule_sha256,
        applicability_reason_code=decision.reason_code,
        connector_identity=spec.runtime_lineage.connector_identity,
        connector_version_hash=spec.runtime_lineage.connector_version_hash,
        request_builder_identity=spec.runtime_lineage.request_builder_identity,
        request_builder_version_hash=spec.runtime_lineage.request_builder_version_hash,
        catalog_version=PROVIDER_CATALOG_VERSION,
    )
    common: dict[str, Any] = {
        "tenant_id": request.tenant_id,
        "deal_id": request.deal_id,
        "run_id": request.run_id,
        "provider_id": request.provider_id,
        "identity_id": identity.identity_id,
        "identity_version": identity.identity_version,
        "identity_rule_version": identity.identity_rule_version,
        "identity_listing_state": identity.listing_state,
        "identity_evidence_ids": identity.evidence_ids,
        "identity_evidence_set_sha256": identity.evidence_set_sha256,
        "identity_provenance_ids": identity.provenance_ids,
        "identity_provenance_set_sha256": identity.provenance_set_sha256,
        "identity_hash": identity.identity_hash,
        "applicability_decision_id": decision.decision_id,
        "applicability_decision_hash": decision.decision_hash,
        "applicability_decision_content_sha256": decision.decision_content_sha256,
        "applicability_rule_version": decision.rule_version,
        "applicability_rule_sha256": decision.rule_sha256,
        "applicability_reason_code": decision.reason_code,
        "required_identifiers": identifiers,
        "identifiers_used_sha256": identifiers_sha256,
        "request_binding_sha256": binding_sha256,
        "connector_identity": spec.runtime_lineage.connector_identity,
        "connector_version_hash": spec.runtime_lineage.connector_version_hash,
        "request_builder_identity": spec.runtime_lineage.request_builder_identity,
        "request_builder_version_hash": spec.runtime_lineage.request_builder_version_hash,
        "rights_approval_id": rights.rights_approval_id,
        "rights_version_hash": rights.rights_version_hash,
        "rights_class": rights.rights_class,
        "no_store_policy_version": rights.no_store_policy_version,
        "attribution_policy_version": rights.attribution_policy_version,
        "credential_version_id": credential_version_id,
        "credential_bundle_hash": credential.bundle_version_hash if credential else None,
        "budget_reservation_id": budget.reservation_id,
        "budget_policy_hash": budget.policy_hash,
        "cache_decision_id": cache.decision_id,
        "cache_policy_hash": cache.policy_hash,
        "external_context_index_allowed": derive_external_context_index_allowed(rights),
        "admission_proof_sha256": "",
    }

    if cache.disposition is CacheDisposition.HIT:
        if cache.cached_outcome_id is None or cache.cached_outcome_sha256 is None:
            raise ProviderAdmissionError(
                "ENRICHMENT_PROVENANCE_MISSING", "cache_hit_reuse_lineage_missing"
            )
        hit = ProviderCacheHitIntent(
            **common,
            cached_outcome_id=cache.cached_outcome_id,
            cached_outcome_sha256=cache.cached_outcome_sha256,
        )
        return replace(hit, admission_proof_sha256=_admission_proof_sha256(hit))

    call = ProviderCallIntent(
        **common,
        cache_disposition=cache.disposition,
    )
    return replace(call, admission_proof_sha256=_admission_proof_sha256(call))


def _validate_request(request: ProviderAdmissionRequest) -> None:
    if not isinstance(request, ProviderAdmissionRequest) or any(
        not _is_nonempty_text(value)
        for value in (
            request.tenant_id,
            request.deal_id,
            request.run_id,
            request.provider_id,
            request.expected_identity_version,
        )
    ):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "scope_invalid")
    if not _is_aware_datetime(request.evaluated_at):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "time_invalid")


def _validate_identity(
    request: ProviderAdmissionRequest, identity: VerifiedIdentitySnapshot
) -> None:
    try:
        expected_evidence_sha256 = canonical_identity_evidence_set_sha256(identity.evidence_ids)
        expected_provenance_sha256 = canonical_identity_provenance_set_sha256(
            identity.provenance_ids
        )
        expected_identity_hash = canonical_verified_identity_hash(identity)
    except (AttributeError, TypeError, ValueError, ProviderAdmissionError):
        raise ProviderAdmissionError("COMPANY_IDENTITY_UNVERIFIED", "identity_invalid") from None
    if (
        not isinstance(identity, VerifiedIdentitySnapshot)
        or identity.tenant_id != request.tenant_id
        or identity.deal_id != request.deal_id
        or identity.identity_version != request.expected_identity_version
        or not _is_nonempty_text(identity.identity_id)
        or not _is_nonempty_text(identity.identity_version)
        or not _is_nonempty_text(identity.identity_rule_version)
        or not isinstance(identity.listing_state, ListingState)
        or identity.verification_basis is not IdentityVerificationBasis.VERIFIED_EVIDENCE
        or identity.listing_state is ListingState.UNKNOWN
        or not _valid_text_tuple(identity.evidence_ids)
        or not _is_sha256(identity.evidence_set_sha256)
        or not _valid_text_tuple(identity.provenance_ids)
        or not _is_sha256(identity.provenance_set_sha256)
        or not _is_sha256(identity.identity_hash)
        or not _valid_identifier_pairs(identity.identifiers, allow_empty=True)
        or identity.evidence_set_sha256 != expected_evidence_sha256
        or identity.provenance_set_sha256 != expected_provenance_sha256
        or identity.identity_hash != expected_identity_hash
    ):
        raise ProviderAdmissionError("COMPANY_IDENTITY_UNVERIFIED", "identity_invalid")


def _validate_applicability(
    identity: VerifiedIdentitySnapshot,
    spec: ProviderSpec,
    decision: ApplicabilityDecision,
) -> None:
    if (
        not isinstance(decision, ApplicabilityDecision)
        or not isinstance(decision.status, ApplicabilityStatus)
        or decision.provider_id != spec.provider_id
        or decision.identity_id != identity.identity_id
        or decision.identity_version != identity.identity_version
        or decision.identity_hash != identity.identity_hash
        or not _is_uuid(decision.decision_id)
        or not _is_nonempty_text(decision.rule_version)
        or not _is_sha256(decision.rule_sha256)
        or not _is_sha256(decision.decision_hash)
        or not _is_sha256(decision.decision_content_sha256)
        or not _is_nonempty_text(decision.reason_code)
    ):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "applicability_invalid")
    expected_content = canonical_applicability_decision_content_sha256(decision)
    if (
        decision.decision_content_sha256 != expected_content
        or decision.decision_hash != expected_content
    ):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "applicability_invalid")


def _validate_not_applicable(
    identity: VerifiedIdentitySnapshot,
    spec: ProviderSpec,
    decision: ApplicabilityDecision,
) -> None:
    if (
        spec.provider_id not in {"finnhub", "fmp"}
        or identity.listing_state not in {ListingState.PRIVATE, ListingState.UNLISTED}
        or any(key == "listed_ticker" for key, _ in identity.identifiers)
        or decision.reason_code != "VERIFIED_NOT_LISTED"
    ):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "not_applicable_unproven")


def _required_identifiers(
    identity: VerifiedIdentitySnapshot, spec: ProviderSpec
) -> tuple[tuple[str, str], ...]:
    if not _valid_identifier_pairs(identity.identifiers, allow_empty=True):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "identifier_invalid")
    identifier_map = dict(identity.identifiers)
    if len(identifier_map) != len(identity.identifiers):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "identifier_duplicate")
    rule = identifier_selector_rule_for(spec.provider_id)
    known_fields = {field_name for group in rule.alternatives for field_name in group}
    if any(field_name not in known_fields for field_name in identifier_map):
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", "identifier_mixed")
    complete = [
        group
        for group in rule.alternatives
        if all(field_name in identifier_map for field_name in group)
    ]
    maximal = [
        group for group in complete if not any(set(group) < set(other) for other in complete)
    ]
    if len(maximal) != 1 or set(identifier_map) != set(maximal[0]):
        reason = "identifier_missing" if not maximal else "identifier_mixed"
        raise ProviderAdmissionError("ENRICHMENT_PROVIDER_INPUT_MISSING", reason)
    selected = maximal[0]
    return tuple((field_name, identifier_map[field_name]) for field_name in selected)


def _validate_rights(
    request: ProviderAdmissionRequest,
    spec: ProviderSpec,
    rights: RightsSnapshot,
) -> None:
    code = _rights_error_code(spec)
    if not isinstance(rights, RightsSnapshot):
        raise ProviderAdmissionError(code, "rights_not_active_exact_prod")
    credential_link_valid = rights.credential_version_id is None or _is_nonempty_text(
        rights.credential_version_id
    )
    if (
        rights.tenant_id != request.tenant_id
        or rights.provider_id != spec.provider_id
        or rights.environment != "PROD"
        or rights.rights_class is not spec.rights_class
        or rights.rights_requirement is not spec.rights_requirement
        or rights.status is not RightsStatus.ACTIVE
        or not _is_nonempty_text(rights.no_store_policy_version)
        or not _is_nonempty_text(rights.attribution_policy_version)
        or not _is_nonempty_text(rights.rights_approval_id)
        or not _is_sha256(rights.rights_version_hash)
        or not credential_link_valid
        or (
            spec.rights_requirement is not RightsRequirement.COMMERCIAL_PROD
            and rights.credential_version_id is not None
        )
        or not _is_aware_datetime(rights.expires_at)
        or rights.expires_at <= request.evaluated_at
    ):
        raise ProviderAdmissionError(code, "rights_not_active_exact_prod")


def _validate_credentials(
    request: ProviderAdmissionRequest,
    spec: ProviderSpec,
    credential: CredentialVersionSnapshot | None,
) -> None:
    expected_keys = tuple(field.credential_key for field in spec.credential_fields)
    if (
        not isinstance(credential, CredentialVersionSnapshot)
        or credential.tenant_id != request.tenant_id
        or credential.provider_id != spec.provider_id
        or not isinstance(credential.credential_keys, tuple)
        or credential.credential_keys != expected_keys
        or len(set(credential.credential_keys)) != len(credential.credential_keys)
        or any(not _is_nonempty_text(item) for item in credential.credential_keys)
        or credential.encrypted is not True
        or credential.active is not True
        or not _is_nonempty_text(credential.credential_version_id)
        or not _is_sha256(credential.ciphertext_sha256)
        or not _is_sha256(credential.bundle_version_hash)
    ):
        raise ProviderAdmissionError(
            "ENRICHMENT_PROVIDER_INPUT_MISSING", "credential_version_invalid"
        )


def _validate_rights_credential_binding(
    spec: ProviderSpec,
    rights: RightsSnapshot,
    credential: CredentialVersionSnapshot | None,
) -> None:
    if spec.rights_requirement is RightsRequirement.COMMERCIAL_PROD and (
        credential is None or rights.credential_version_id != credential.credential_version_id
    ):
        raise ProviderAdmissionError(_rights_error_code(spec), "rights_credential_version_mismatch")


def _validate_budget(
    request: ProviderAdmissionRequest,
    budget: BudgetReservationSnapshot,
) -> None:
    if not isinstance(budget, BudgetReservationSnapshot):
        raise ProviderAdmissionError("PROVIDER_BUDGET_CONFIG_BLOCKED", "budget_reservation_invalid")
    if budget.provider_id != request.provider_id:
        raise ProviderAdmissionError(
            "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED", "budget_provider_mismatch"
        )
    if (
        budget.tenant_id != request.tenant_id
        or budget.deal_id != request.deal_id
        or budget.run_id != request.run_id
        or not _is_uuid(budget.reservation_id)
        or budget.active is not True
        or budget.signature_verified is not True
        or not _is_nonempty_text(budget.signed_policy_id)
        or not _is_sha256(budget.policy_hash)
        or not _is_aware_datetime(budget.expires_at)
        or budget.expires_at <= request.evaluated_at
    ):
        raise ProviderAdmissionError("PROVIDER_BUDGET_CONFIG_BLOCKED", "budget_reservation_invalid")


def _validate_cache(
    request: ProviderAdmissionRequest,
    rights: RightsSnapshot,
    identity: VerifiedIdentitySnapshot,
    required_identifiers: tuple[tuple[str, str], ...],
    identifiers_sha256: str,
    credential_version_id: str | None,
    cache: CacheDecisionSnapshot,
) -> None:
    if not isinstance(cache, CacheDecisionSnapshot):
        raise ProviderAdmissionError("ENRICHMENT_PROVENANCE_MISSING", "cache_lineage_invalid")
    if (
        cache.rights_approval_id != rights.rights_approval_id
        or cache.rights_version_hash != rights.rights_version_hash
    ):
        raise ProviderAdmissionError("ENRICHMENT_RIGHTS_BLOCKED", "cache_rights_mismatch")
    if (
        cache.tenant_id != request.tenant_id
        or cache.deal_id != request.deal_id
        or cache.run_id != request.run_id
        or cache.provider_id != request.provider_id
        or cache.identity_id != identity.identity_id
        or cache.identity_version != identity.identity_version
        or cache.identity_hash != identity.identity_hash
        or cache.required_identifiers != required_identifiers
        or cache.identifiers_used_sha256 != identifiers_sha256
        or cache.credential_version_id != credential_version_id
        or cache.catalog_version != PROVIDER_CATALOG_VERSION
        or not isinstance(cache.disposition, CacheDisposition)
        or not _is_nonempty_text(cache.decision_id)
        or cache.rights_compatible is not True
        or not _is_sha256(cache.policy_hash)
    ):
        raise ProviderAdmissionError("ENRICHMENT_PROVENANCE_MISSING", "cache_lineage_invalid")
    has_cached_id = cache.cached_outcome_id is not None
    has_cached_hash = cache.cached_outcome_sha256 is not None
    if has_cached_id != has_cached_hash:
        raise ProviderAdmissionError("ENRICHMENT_PROVENANCE_MISSING", "cache_lineage_invalid")
    if cache.disposition is CacheDisposition.HIT:
        if not _is_uuid(cache.cached_outcome_id) or not _is_sha256(cache.cached_outcome_sha256):
            raise ProviderAdmissionError(
                "ENRICHMENT_PROVENANCE_MISSING", "cache_hit_reuse_lineage_missing"
            )
    elif has_cached_id or has_cached_hash:
        raise ProviderAdmissionError("ENRICHMENT_PROVENANCE_MISSING", "cache_lineage_invalid")


def _admission_proof_sha256(
    intent: ProviderCallIntent | ProviderNotApplicableIntent | ProviderCacheHitIntent,
) -> str:
    if isinstance(intent, ProviderNotApplicableIntent):
        payload: dict[str, Any] = {
            "schema": "provider-not-applicable-admission-v1",
            "tenant_id": intent.tenant_id,
            "deal_id": intent.deal_id,
            "run_id": intent.run_id,
            "provider_id": intent.provider_id,
            "identity_id": intent.identity_id,
            "identity_version": intent.identity_version,
            "identity_rule_version": intent.identity_rule_version,
            "identity_evidence_ids": sorted(intent.identity_evidence_ids),
            "identity_evidence_set_sha256": intent.identity_evidence_set_sha256,
            "identity_provenance_ids": sorted(intent.identity_provenance_ids),
            "identity_provenance_set_sha256": intent.identity_provenance_set_sha256,
            "identity_hash": intent.identity_hash,
            "listing_state": intent.listing_state.value,
            "applicability_decision_id": intent.applicability_decision_id,
            "applicability_decision_content_sha256": (intent.applicability_decision_content_sha256),
            "decision": _decision_payload(intent.decision),
            "zero_call": intent.zero_call,
            "catalog_version": intent.catalog_version,
        }
        return _sha256_json(payload)
    common = {
        "tenant_id": intent.tenant_id,
        "deal_id": intent.deal_id,
        "run_id": intent.run_id,
        "provider_id": intent.provider_id,
        "identity_id": intent.identity_id,
        "identity_version": intent.identity_version,
        "identity_rule_version": intent.identity_rule_version,
        "identity_listing_state": intent.identity_listing_state.value,
        "identity_evidence_ids": sorted(intent.identity_evidence_ids),
        "identity_evidence_set_sha256": intent.identity_evidence_set_sha256,
        "identity_provenance_ids": sorted(intent.identity_provenance_ids),
        "identity_provenance_set_sha256": intent.identity_provenance_set_sha256,
        "identity_hash": intent.identity_hash,
        "applicability_decision_id": intent.applicability_decision_id,
        "applicability_decision_hash": intent.applicability_decision_hash,
        "applicability_decision_content_sha256": (intent.applicability_decision_content_sha256),
        "applicability_rule_version": intent.applicability_rule_version,
        "applicability_rule_sha256": intent.applicability_rule_sha256,
        "applicability_reason_code": intent.applicability_reason_code,
        "required_identifiers": sorted(intent.required_identifiers),
        "identifiers_used_sha256": intent.identifiers_used_sha256,
        "request_binding_sha256": intent.request_binding_sha256,
        "connector_identity": intent.connector_identity,
        "connector_version_hash": intent.connector_version_hash,
        "request_builder_identity": intent.request_builder_identity,
        "request_builder_version_hash": intent.request_builder_version_hash,
        "rights_approval_id": intent.rights_approval_id,
        "rights_version_hash": intent.rights_version_hash,
        "rights_class": intent.rights_class.value,
        "no_store_policy_version": intent.no_store_policy_version,
        "attribution_policy_version": intent.attribution_policy_version,
        "credential_version_id": intent.credential_version_id,
        "credential_bundle_hash": intent.credential_bundle_hash,
        "budget_reservation_id": intent.budget_reservation_id,
        "budget_policy_hash": intent.budget_policy_hash,
        "cache_decision_id": intent.cache_decision_id,
        "cache_policy_hash": intent.cache_policy_hash,
        "external_context_index_allowed": intent.external_context_index_allowed,
        "catalog_version": intent.catalog_version,
    }
    if isinstance(intent, ProviderCacheHitIntent):
        common.update(
            {
                "schema": "provider-cache-hit-admission-v1",
                "cached_outcome_id": intent.cached_outcome_id,
                "cached_outcome_sha256": intent.cached_outcome_sha256,
                "no_provider_call": intent.no_provider_call,
            }
        )
    else:
        common.update(
            {
                "schema": "provider-call-admission-v1",
                "cache_disposition": intent.cache_disposition.value,
            }
        )
    return _sha256_json(common)


def _decision_payload(decision: ApplicabilityDecision) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "provider_id": decision.provider_id,
        "status": decision.status.value,
        "identity_id": decision.identity_id,
        "identity_version": decision.identity_version,
        "identity_hash": decision.identity_hash,
        "rule_version": decision.rule_version,
        "rule_sha256": decision.rule_sha256,
        "decision_hash": decision.decision_hash,
        "decision_content_sha256": decision.decision_content_sha256,
        "reason_code": decision.reason_code,
    }


def _rights_error_code(spec: ProviderSpec) -> str:
    return (
        "ENRICHMENT_LICENSE_BLOCKED"
        if spec.rights_requirement is RightsRequirement.COMMERCIAL_PROD
        else "ENRICHMENT_RIGHTS_BLOCKED"
    )


def _valid_identifier_pairs(value: object, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, tuple)
        and (allow_empty or bool(value))
        and all(
            isinstance(item, tuple)
            and len(item) == 2
            and _is_nonempty_text(item[0])
            and _is_nonempty_text(item[1])
            for item in value
        )
    )


def _valid_text_tuple(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and bool(value)
        and len(set(value)) == len(value)
        and all(_is_nonempty_text(item) for item in value)
    )


def _canonical_identity_reference_set_sha256(schema: str, values: tuple[str, ...]) -> str:
    if not _valid_text_tuple(values):
        raise ProviderAdmissionError("COMPANY_IDENTITY_UNVERIFIED", "identity_invalid")
    return _sha256_json({"schema": schema, "ids": sorted(values)})


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
