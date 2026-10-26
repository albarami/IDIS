"""RED-first tests for immutable normalized-fact and routing intents."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

import idis.services.enrichment.task9.normalized_facts as normalized_facts
import idis.services.enrichment.task9.provider_admission as provider_admission
from idis.models.sanad import SanadGrade
from idis.services.enrichment.models import RightsClass
from idis.services.enrichment.task9.normalized_facts import (
    NormalizedFactsError,
    ProviderExecutionResult,
    ProviderExecutionStatus,
    ProviderFactInput,
    ProviderOutcomeKind,
    materialize_provider_intents,
)
from idis.services.enrichment.task9.perplexity_evidence import (
    PerplexityCitationEvidence,
    PerplexityRetrievalProvenance,
    SupportedSynthesisUnit,
    SynthesisUnitInput,
    build_perplexity_evidence,
)
from idis.services.enrichment.task9.provider_admission import (
    AdmissionCollaborators,
    ApplicabilityDecision,
    ApplicabilityStatus,
    BudgetReservationSnapshot,
    CacheDecisionSnapshot,
    CacheDisposition,
    CredentialVersionSnapshot,
    IdentityVerificationBasis,
    ListingState,
    ProviderAdmissionRequest,
    ProviderCacheHitIntent,
    ProviderCallIntent,
    ProviderNotApplicableIntent,
    RightsSnapshot,
    RightsStatus,
    VerifiedIdentitySnapshot,
    admit_provider,
    canonical_applicability_decision_content_sha256,
    canonical_identifiers_sha256,
)
from idis.services.enrichment.task9.provider_catalog import (
    COMMON_EXTERNAL_CONTEXT_CONSUMERS,
    PROVIDER_CATALOG,
    FactDomain,
    RightsRequirement,
    deliverable_consumers_for,
)
from idis.services.enrichment.task9.rights_index_policy import (
    ATTRIBUTION_NOT_REQUIRED_POLICY_V1,
    EXTERNAL_CONTEXT_NO_STORE_POLICY_V1,
    EXTERNAL_CONTEXT_STORE_POLICY_V1,
    PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY,
)
from idis.services.sanad.source_tiers import SourceTier, TierUsage

_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
_REQUEST_CONTENT = b"GET https://public.example/provider"
_RESPONSE_BODY = b'{"fixture":"public"}'
_REQUEST_SHA256 = hashlib.sha256(_REQUEST_CONTENT).hexdigest()
_HASH = hashlib.sha256(_RESPONSE_BODY).hexdigest()
_PRIVATE_CACHE_MARKER = "PRIVATE-CACHE-MARKER"


class _HostileString(str):
    def strip(self, chars: str | None = None) -> str:
        raise RuntimeError(_PRIVATE_CACHE_MARKER)


class _HostileTuple(tuple[object, ...]):
    def __iter__(self):
        raise RuntimeError(_PRIVATE_CACHE_MARKER)


def _unsafe_replace(instance: Any, mutation: dict[str, object]) -> Any:
    """Build deliberately ill-typed frozen inputs for boundary fuzzing."""

    return replace(instance, **mutation)


def _identity_for(
    provider_id: str,
    *,
    identity_version: str = "identity-v3",
    identity_rule_version: str = "company-identity-v3",
    evidence_ids: tuple[str, ...] = ("evidence-001",),
    listing_state: ListingState = ListingState.LISTED,
) -> VerifiedIdentitySnapshot:
    identifiers_by_provider = {
        "github": (("github_organization", "github"), ("github_login", "github")),
        "perplexity": (
            ("verified_company", "Public Example Corp"),
            ("market_selector", "software"),
        ),
        "sec_edgar": (("cik", "0000320193"), ("us_reporting_identity", "verified")),
        "finnhub": (("listed_ticker", "AAPL"),),
    }
    identity = VerifiedIdentitySnapshot(
        tenant_id="tenant-001",
        deal_id="deal-001",
        identity_id="identity-001",
        identity_version=identity_version,
        identity_rule_version=identity_rule_version,
        listing_state=listing_state,
        verification_basis=IdentityVerificationBasis.VERIFIED_EVIDENCE,
        identifiers=(
            ()
            if provider_id in {"finnhub", "fmp"}
            and listing_state in {ListingState.PRIVATE, ListingState.UNLISTED}
            else identifiers_by_provider[provider_id]
        ),
        evidence_ids=evidence_ids,
        evidence_set_sha256="",
        provenance_ids=("provenance-001",),
        provenance_set_sha256="",
        identity_hash="",
    )
    identity = replace(
        identity,
        evidence_set_sha256=provider_admission.canonical_identity_evidence_set_sha256(
            identity.evidence_ids
        ),
        provenance_set_sha256=(
            provider_admission.canonical_identity_provenance_set_sha256(identity.provenance_ids)
        ),
    )
    return replace(
        identity,
        identity_hash=provider_admission.canonical_verified_identity_hash(identity),
    )


def _decision_for(
    identity: VerifiedIdentitySnapshot,
    provider_id: str,
    *,
    status: ApplicabilityStatus = ApplicabilityStatus.APPLICABLE,
    decision_id: str = "00000000-0000-4000-8000-000000000011",
    rule_sha256: str = "7" * 64,
) -> ApplicabilityDecision:
    decision = ApplicabilityDecision(
        decision_id=decision_id,
        provider_id=provider_id,
        status=status,
        identity_id=identity.identity_id,
        identity_version=identity.identity_version,
        identity_hash=identity.identity_hash,
        rule_version="listing-applicability-v1",
        rule_sha256=rule_sha256,
        decision_hash="",
        decision_content_sha256="",
        reason_code=(
            "VERIFIED_APPLICABLE"
            if status is ApplicabilityStatus.APPLICABLE
            else "VERIFIED_NOT_LISTED"
        ),
    )
    content_sha256 = canonical_applicability_decision_content_sha256(decision)
    return replace(
        decision,
        decision_hash=content_sha256,
        decision_content_sha256=content_sha256,
    )


class _AdmissionStack:
    def __init__(
        self,
        identity: VerifiedIdentitySnapshot,
        decision: ApplicabilityDecision,
        *,
        external_context_index_allowed: bool | None,
        run_id: str,
        rights_approval_id: str = "rights-001",
        rights_version_hash: str = "3" * 64,
        budget_policy_hash: str = "5" * 64,
        cache_policy_hash: str = "6" * 64,
        cache_disposition: CacheDisposition = CacheDisposition.MISS,
        cached_outcome_id: str | None = None,
        cached_outcome_sha256: str | None = None,
    ) -> None:
        self.identity = identity
        self.decision = decision
        self.run_id = run_id
        spec = PROVIDER_CATALOG[decision.provider_id]
        policy = PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY[decision.provider_id]
        index_allowed = (
            policy.external_context_index_allowed
            if external_context_index_allowed is None
            else external_context_index_allowed
        )
        credential_version_id = "credential-001" if spec.credential_fields else None
        self.rights = RightsSnapshot(
            tenant_id="tenant-001",
            rights_approval_id=rights_approval_id,
            provider_id=decision.provider_id,
            environment="PROD",
            rights_class=spec.rights_class,
            rights_requirement=spec.rights_requirement,
            status=RightsStatus.ACTIVE,
            rights_version_hash=rights_version_hash,
            credential_version_id=(
                credential_version_id
                if spec.rights_requirement is RightsRequirement.COMMERCIAL_PROD
                else None
            ),
            expires_at=_NOW + timedelta(days=30),
            no_store_policy_version=(
                EXTERNAL_CONTEXT_STORE_POLICY_V1
                if index_allowed
                else EXTERNAL_CONTEXT_NO_STORE_POLICY_V1
            ),
            attribution_policy_version=policy.attribution_policy_version,
        )
        self.credential = (
            CredentialVersionSnapshot(
                tenant_id="tenant-001",
                credential_version_id="credential-001",
                provider_id=decision.provider_id,
                credential_keys=tuple(item.credential_key for item in spec.credential_fields),
                encrypted=True,
                active=True,
                ciphertext_sha256="d" * 64,
                bundle_version_hash="4" * 64,
            )
            if spec.credential_fields
            else None
        )
        self.budget = BudgetReservationSnapshot(
            tenant_id="tenant-001",
            deal_id="deal-001",
            run_id=run_id,
            reservation_id="00000000-0000-4000-8000-000000000001",
            provider_id=decision.provider_id,
            policy_hash=budget_policy_hash,
            signed_policy_id="policy-001",
            active=True,
            signature_verified=True,
            expires_at=_NOW + timedelta(minutes=5),
        )
        self.cache_disposition = cache_disposition
        self.cache_policy_hash = cache_policy_hash
        self.cached_outcome_id = cached_outcome_id
        self.cached_outcome_sha256 = cached_outcome_sha256

    def resolve_identity(self, request: ProviderAdmissionRequest) -> VerifiedIdentitySnapshot:
        return self.identity

    def decide_applicability(
        self, identity: VerifiedIdentitySnapshot, provider_id: str
    ) -> ApplicabilityDecision:
        return self.decision

    def resolve_rights(self, provider_id: str, tenant_id: str) -> RightsSnapshot:
        return self.rights

    def resolve_credentials(
        self, provider_id: str, tenant_id: str
    ) -> CredentialVersionSnapshot | None:
        return self.credential

    def reserve_budget(
        self, provider_id: str, tenant_id: str, deal_id: str, run_id: str
    ) -> BudgetReservationSnapshot:
        return self.budget

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
    ) -> CacheDecisionSnapshot:
        return CacheDecisionSnapshot(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            provider_id=provider_id,
            identity_id=identity.identity_id,
            identity_version=identity.identity_version,
            identity_hash=identity.identity_hash,
            required_identifiers=required_identifiers,
            identifiers_used_sha256=canonical_identifiers_sha256(required_identifiers),
            credential_version_id=credential_version_id,
            catalog_version=catalog_version,
            decision_id="cache-001",
            disposition=self.cache_disposition,
            rights_approval_id=rights.rights_approval_id,
            rights_version_hash=rights.rights_version_hash,
            rights_compatible=True,
            policy_hash=self.cache_policy_hash,
            cached_outcome_id=self.cached_outcome_id,
            cached_outcome_sha256=self.cached_outcome_sha256,
        )

    def collaborators(self) -> AdmissionCollaborators:
        return AdmissionCollaborators(self, self, self, self, self, self)


def _admission(
    provider_id: str = "github",
    *,
    run_id: str = "run-001",
    identity_version: str = "identity-v3",
    identity_rule_version: str = "company-identity-v3",
    evidence_ids: tuple[str, ...] = ("evidence-001",),
    listing_state: ListingState = ListingState.LISTED,
    applicability_status: ApplicabilityStatus = ApplicabilityStatus.APPLICABLE,
    external_context_index_allowed: bool | None = None,
    rights_approval_id: str = "rights-001",
    rights_version_hash: str = "3" * 64,
    budget_policy_hash: str = "5" * 64,
    cache_policy_hash: str = "6" * 64,
    cache_disposition: CacheDisposition = CacheDisposition.MISS,
    cached_outcome_id: str | None = None,
    cached_outcome_sha256: str | None = None,
) -> ProviderCallIntent | ProviderNotApplicableIntent | ProviderCacheHitIntent:
    identity = _identity_for(
        provider_id,
        identity_version=identity_version,
        identity_rule_version=identity_rule_version,
        evidence_ids=evidence_ids,
        listing_state=listing_state,
    )
    decision = _decision_for(identity, provider_id, status=applicability_status)
    stack = _AdmissionStack(
        identity,
        decision,
        external_context_index_allowed=external_context_index_allowed,
        run_id=run_id,
        rights_approval_id=rights_approval_id,
        rights_version_hash=rights_version_hash,
        budget_policy_hash=budget_policy_hash,
        cache_policy_hash=cache_policy_hash,
        cache_disposition=cache_disposition,
        cached_outcome_id=cached_outcome_id,
        cached_outcome_sha256=cached_outcome_sha256,
    )
    return admit_provider(
        ProviderAdmissionRequest(
            tenant_id="tenant-001",
            deal_id="deal-001",
            run_id=run_id,
            provider_id=provider_id,
            expected_identity_version=identity_version,
            evaluated_at=_NOW,
        ),
        stack.collaborators(),
    )


def _call(
    provider_id: str = "github",
    *,
    run_id: str = "run-001",
    identity_version: str = "identity-v3",
    identity_rule_version: str = "company-identity-v3",
    evidence_ids: tuple[str, ...] = ("evidence-001",),
    external_context_index_allowed: bool | None = None,
    rights_approval_id: str = "rights-001",
    rights_version_hash: str = "3" * 64,
    budget_policy_hash: str = "5" * 64,
    cache_policy_hash: str = "6" * 64,
) -> ProviderCallIntent:
    result = _admission(
        provider_id,
        run_id=run_id,
        identity_version=identity_version,
        identity_rule_version=identity_rule_version,
        evidence_ids=evidence_ids,
        external_context_index_allowed=external_context_index_allowed,
        rights_approval_id=rights_approval_id,
        rights_version_hash=rights_version_hash,
        budget_policy_hash=budget_policy_hash,
        cache_policy_hash=cache_policy_hash,
    )
    assert isinstance(result, ProviderCallIntent)
    return result


def _fact(
    *,
    subject: str = "Public Example Corp",
    predicate: str = "public_metric",
    value: object = 42,
    fact_domain: FactDomain | str = FactDomain.COMMERCIAL_DILIGENCE,
    safe_source_ref: str = "public-source-001",
    citation_evidence: object | None = None,
) -> ProviderFactInput:
    return ProviderFactInput(
        subject=subject,
        predicate=predicate,
        value=value,
        fact_domain=fact_domain,
        safe_source_ref=safe_source_ref,
        citation_evidence=citation_evidence,
    )


def _execution(
    *,
    call: ProviderCallIntent | None = None,
    status: ProviderExecutionStatus = ProviderExecutionStatus.HIT,
    facts: tuple[ProviderFactInput, ...] = (_fact(),),
    provider_request_id: str = "request-public-001",
    response_sha256: str = _HASH,
    request_succeeded: bool = True,
    schema_valid: bool = True,
    retrieved_at: datetime = _NOW,
    supported_synthesis: tuple[object, ...] = (),
) -> ProviderExecutionResult:
    call = call or _call()
    return ProviderExecutionResult(
        provider_id=call.provider_id,
        admission_proof_sha256=call.admission_proof_sha256,
        request_binding_sha256=call.request_binding_sha256,
        identifiers_used=call.required_identifiers,
        identifiers_used_sha256=call.identifiers_used_sha256,
        request_content_sha256=_REQUEST_SHA256,
        connector_identity=call.connector_identity,
        connector_version_hash=call.connector_version_hash,
        request_builder_identity=call.request_builder_identity,
        status=status,
        provider_request_id=provider_request_id,
        retrieved_at=retrieved_at,
        response_sha256=response_sha256,
        request_builder_version_hash=call.request_builder_version_hash,
        request_succeeded=request_succeeded,
        schema_valid=schema_valid,
        facts=facts,
        operation_evidence=(
            normalized_facts.ProviderExecutionOperationEvidence(
                operation_id="provider-operation-001",
                safe_request_content=_REQUEST_CONTENT,
                request_content_sha256=_REQUEST_SHA256,
                response_body=_RESPONSE_BODY,
                response_sha256=response_sha256,
            ),
        ),
        supported_synthesis=supported_synthesis,
    )


def test_hit_materializes_deterministic_scoped_facts_rag_and_catalog_edges() -> None:
    call = _call()
    first_fact = _fact()
    second_fact = _fact(predicate="employee_count", value={"as_of": "2026", "value": 100})
    first = materialize_provider_intents(
        call,
        _execution(facts=(second_fact, first_fact)),
    )
    reordered = materialize_provider_intents(
        call,
        _execution(facts=(first_fact, second_fact)),
    )

    assert first == reordered
    assert first.outcome.outcome_kind is ProviderOutcomeKind.HIT
    assert len(first.facts) == 2
    assert len(first.rag_intents) == 2
    UUID(first.outcome.outcome_id)
    for fact in first.facts:
        UUID(fact.fact_id)
        assert fact.tenant_id == "tenant-001"
        assert fact.deal_id == "deal-001"
        assert fact.run_id == "run-001"
        assert fact.provider_id == "github"
        assert fact.outcome_id == first.outcome.outcome_id
        assert len(fact.value_sha256) == 64
        assert fact.provenance.provider_request_id == "request-public-001"
        assert fact.provenance.identity_version == "identity-v3"
        assert fact.provenance.identity_rule_version == "company-identity-v3"
        assert fact.provenance.identity_evidence_set_sha256 == call.identity_evidence_set_sha256
        assert fact.provenance.identity_provenance_set_sha256 == call.identity_provenance_set_sha256
        assert fact.provenance.applicability_rule_sha256 == "7" * 64
        assert fact.provenance.identifiers_used_sha256
        assert fact.provenance.identifiers_used == call.required_identifiers
        assert fact.provenance.provider_id == "github"
        assert fact.provenance.rights_class is RightsClass.GREEN
        assert fact.provenance.request_binding_sha256 == call.request_binding_sha256
        assert fact.provenance.request_content_sha256 == _REQUEST_SHA256
        assert fact.provenance.connector_identity == call.connector_identity
        assert fact.provenance.connector_version_hash == call.connector_version_hash
        assert fact.provenance.request_builder_identity == call.request_builder_identity
    assert first.outcome.identifiers_used == call.required_identifiers
    assert first.outcome.request_binding_sha256 == call.request_binding_sha256
    assert first.outcome.request_content_sha256 == _REQUEST_SHA256
    assert first.outcome.connector_identity == call.connector_identity
    assert first.outcome.connector_version_hash == call.connector_version_hash
    assert first.outcome.request_builder_identity == call.request_builder_identity

    expected_consumers = set(PROVIDER_CATALOG["github"].analysis_consumers)
    expected_consumers.update(COMMON_EXTERNAL_CONTEXT_CONSUMERS)
    expected_consumers.update(deliverable_consumers_for("github", FactDomain.COMMERCIAL_DILIGENCE))
    for fact in first.facts:
        assert {
            edge.consumer_id for edge in first.consumer_edges if edge.fact_id == fact.fact_id
        } == expected_consumers


def test_scope_request_and_identity_lineage_change_deterministic_ids() -> None:
    baseline_call = _call()
    changed_run_call = _call(run_id="run-002")
    changed_identity_call = _call(identity_version="identity-v4")
    baseline = materialize_provider_intents(baseline_call, _execution(call=baseline_call))
    changed_run = materialize_provider_intents(changed_run_call, _execution(call=changed_run_call))
    changed_identity = materialize_provider_intents(
        changed_identity_call, _execution(call=changed_identity_call)
    )
    changed_request = materialize_provider_intents(
        baseline_call,
        _execution(call=baseline_call, provider_request_id="request-public-002"),
    )
    assert (
        len(
            {
                baseline.outcome.outcome_id,
                changed_run.outcome.outcome_id,
                changed_identity.outcome.outcome_id,
                changed_request.outcome.outcome_id,
            }
        )
        == 4
    )


def test_every_authorization_lineage_change_changes_the_outcome_identity() -> None:
    baseline_call = _call()
    variants = (
        _call(rights_approval_id="rights-002"),
        _call(identity_rule_version="company-identity-v4"),
        _call(evidence_ids=("evidence-002",)),
        _call(rights_version_hash="8" * 64),
        _call(budget_policy_hash="b" * 64),
        _call(cache_policy_hash="c" * 64),
    )
    outcome_ids = {
        materialize_provider_intents(call, _execution(call=call)).outcome.outcome_id
        for call in (baseline_call, *variants)
    }
    assert len(outcome_ids) == 1 + len(variants)


@pytest.mark.parametrize(
    "mutation",
    [
        {"tenant_id": "other-tenant"},
        {"provider_id": "perplexity"},
        {"identity_version": "identity-v4"},
        {"applicability_decision_content_sha256": "f" * 64},
        {"required_identifiers": (("github_organization", "other"),)},
        {"rights_approval_id": "rights-other"},
        {"catalog_version": "catalog-other"},
        {"request_binding_sha256": "e" * 64},
    ],
)
def test_call_intent_tampering_breaks_the_content_bound_admission_proof(
    mutation: dict[str, object],
) -> None:
    genuine = _call()
    forged = copy.copy(genuine)
    for field_name, value in mutation.items():
        object.__setattr__(forged, field_name, value)

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(forged, _execution(call=genuine))

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


def test_real_miss_requires_successful_schema_valid_request_and_has_no_fact_intents() -> None:
    result = materialize_provider_intents(
        _call(),
        _execution(status=ProviderExecutionStatus.MISS, facts=()),
    )
    assert result.outcome.outcome_kind is ProviderOutcomeKind.MISS
    assert result.outcome.fact_ids == ()
    assert result.facts == ()
    assert result.rag_intents == ()
    assert result.consumer_edges == ()
    assert result.outcome.retrieved_at == _NOW
    assert result.outcome.response_sha256 == _HASH
    assert result.outcome.identifiers_used_sha256
    assert result.outcome.rights_version_hash == "3" * 64
    assert result.outcome.credential_bundle_hash == "4" * 64
    assert result.outcome.budget_reservation_id == "00000000-0000-4000-8000-000000000001"
    assert result.outcome.budget_policy_hash == "5" * 64
    assert result.outcome.cache_decision_id == "cache-001"
    assert result.outcome.cache_policy_hash == "6" * 64


def test_verified_not_applicable_materializes_zero_call_outcome_only() -> None:
    admission = _admission(
        "finnhub",
        listing_state=ListingState.PRIVATE,
        applicability_status=ApplicabilityStatus.NOT_APPLICABLE,
    )
    assert isinstance(admission, ProviderNotApplicableIntent)
    result = materialize_provider_intents(admission, None)
    assert result.outcome.outcome_kind is ProviderOutcomeKind.NOT_APPLICABLE
    assert result.outcome.provider_request_id is None
    assert result.outcome.rights_approval_id is None
    assert result.outcome.applicability_rule_version == "listing-applicability-v1"
    assert result.outcome.applicability_rule_sha256 == "7" * 64
    assert result.outcome.identity_rule_version == "company-identity-v3"
    assert result.outcome.identity_evidence_set_sha256 == admission.identity_evidence_set_sha256
    assert result.outcome.identity_provenance_set_sha256 == admission.identity_provenance_set_sha256
    assert result.outcome.applicability_reason_code == "VERIFIED_NOT_LISTED"
    assert result.outcome.applicability_decision_id == admission.applicability_decision_id
    assert (
        result.outcome.applicability_decision_content_sha256
        == admission.applicability_decision_content_sha256
    )
    assert result.outcome.catalog_version == admission.catalog_version
    assert result.outcome.retrieved_at is None
    assert result.outcome.response_sha256 is None
    assert result.facts == result.rag_intents == result.consumer_edges == ()


@pytest.mark.parametrize(
    "mutation",
    [
        {"listing_state": ListingState.LISTED},
        {"applicability_decision_id": "00000000-0000-4000-8000-000000000099"},
        {"applicability_decision_content_sha256": "f" * 64},
        {"catalog_version": "catalog-other"},
        {"admission_proof_sha256": "e" * 64},
    ],
)
def test_not_applicable_persistence_rejects_forged_intent_content(
    mutation: dict[str, object],
) -> None:
    genuine = _admission(
        "finnhub",
        listing_state=ListingState.UNLISTED,
        applicability_status=ApplicabilityStatus.NOT_APPLICABLE,
    )
    assert isinstance(genuine, ProviderNotApplicableIntent)
    forged = copy.copy(genuine)
    for field_name, value in mutation.items():
        object.__setattr__(forged, field_name, value)

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(forged, None)

    assert exc_info.value.code == "ENRICHMENT_PROVIDER_FAILED"


@pytest.mark.parametrize(
    "execution",
    [
        _execution(facts=()),
        _execution(status=ProviderExecutionStatus.MISS, facts=(_fact(),)),
        _execution(status=ProviderExecutionStatus.ERROR, facts=()),
        _execution(status=ProviderExecutionStatus.PARTIAL, facts=(_fact(),)),
        _execution(request_succeeded=False),
        _execution(schema_valid=False),
    ],
)
def test_error_partial_empty_hit_and_false_miss_shapes_cannot_look_terminal(
    execution: ProviderExecutionResult,
) -> None:
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(_call(), execution)
    assert exc_info.value.code == "ENRICHMENT_PROVIDER_FAILED"


@pytest.mark.parametrize(
    "execution",
    [
        _execution(provider_request_id=""),
        _execution(response_sha256="bad"),
        _execution(retrieved_at=datetime(2026, 7, 21, 12, 0)),
        replace(_execution(), request_builder_version_hash="bad"),
    ],
)
def test_missing_or_invalid_request_provenance_fails_closed(
    execution: ProviderExecutionResult,
) -> None:
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(_call(), execution)
    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    "mutation",
    [
        {"provider_id": "perplexity"},
        {"admission_proof_sha256": "f" * 64},
        {"request_binding_sha256": "e" * 64},
        {"identifiers_used": (("github_organization", "other"),)},
        {"identifiers_used_sha256": "d" * 64},
        {"request_content_sha256": "bad"},
        {"connector_identity": "perplexity"},
        {"connector_version_hash": "bad"},
        {"request_builder_identity": ""},
        {"request_builder_version_hash": "bad"},
    ],
)
def test_execution_must_match_exact_admission_provider_selector_and_request_provenance(
    mutation: dict[str, object],
) -> None:
    call = _call()
    execution = _unsafe_replace(_execution(call=call), mutation)

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(call, execution)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    "mutation",
    [
        {"connector_version_hash": "e" * 64},
        {"request_builder_identity": "alternate-builder-v1"},
        {"request_builder_version_hash": "d" * 64},
    ],
)
def test_execution_lineage_must_equal_the_catalog_bound_admission(
    mutation: dict[str, object],
) -> None:
    call = _call()
    execution = _unsafe_replace(_execution(call=call), mutation)

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(call, execution)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


def test_execution_request_and_response_hashes_are_derived_from_operation_bytes() -> None:
    assert hasattr(normalized_facts, "ProviderExecutionOperationEvidence")
    call = _call()
    execution = _execution(call=call)
    assert execution.operation_evidence
    operation = execution.operation_evidence[0]

    forged_request = replace(
        execution,
        operation_evidence=(replace(operation, safe_request_content=b"changed"),),
    )
    with pytest.raises(NormalizedFactsError) as request_error:
        materialize_provider_intents(call, forged_request)
    assert request_error.value.code == "ENRICHMENT_PROVENANCE_MISSING"

    forged_response = replace(
        execution,
        operation_evidence=(replace(operation, response_body=b"changed"),),
    )
    with pytest.raises(NormalizedFactsError) as response_error:
        materialize_provider_intents(call, forged_response)
    assert response_error.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    "execution",
    [
        replace(_execution(), status="HIT"),  # type: ignore[arg-type]
        replace(_execution(), request_succeeded="true"),  # type: ignore[arg-type]
        replace(_execution(), schema_valid=1),  # type: ignore[arg-type]
        replace(_execution(), facts=(object(),)),  # type: ignore[arg-type]
    ],
)
def test_untyped_execution_shapes_fail_with_the_stable_code(
    execution: ProviderExecutionResult,
) -> None:
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(_call(), execution)
    assert exc_info.value.code in {"ENRICHMENT_PROVIDER_FAILED", "ENRICHMENT_PROVENANCE_MISSING"}


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("provider_id", None),
        ("admission_proof_sha256", None),
        ("request_binding_sha256", None),
        ("identifiers_used", None),
        ("identifiers_used_sha256", None),
        ("request_content_sha256", None),
        ("connector_identity", None),
        ("connector_version_hash", None),
        ("request_builder_identity", None),
        ("request_builder_version_hash", None),
        ("status", None),
        ("provider_request_id", None),
        ("retrieved_at", None),
        ("response_sha256", None),
        ("request_succeeded", None),
        ("schema_valid", None),
        ("facts", None),
        ("supported_synthesis", None),
    ],
)
def test_malformed_execution_fields_fail_with_stable_secret_safe_code(
    field_name: str,
    bad_value: object,
) -> None:
    call = _call()
    malformed = _unsafe_replace(_execution(call=call), {field_name: bad_value})

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(call, malformed)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"
    assert "NoneType" not in str(exc_info.value)


def test_credentialless_provider_call_preserves_valid_none_pair() -> None:
    call = _call("sec_edgar")
    result = materialize_provider_intents(call, _execution(call=call))
    assert result.outcome.outcome_kind is ProviderOutcomeKind.HIT
    assert result.outcome.credential_version_id is None


@pytest.mark.parametrize(
    ("credential_version_id", "credential_bundle_hash"),
    [("credential-001", None), (None, "4" * 64)],
)
def test_partial_credential_lineage_fails_closed(
    credential_version_id: str | None,
    credential_bundle_hash: str | None,
) -> None:
    call = replace(
        _call(),
        credential_version_id=credential_version_id,
        credential_bundle_hash=credential_bundle_hash,
    )
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(call, _execution())
    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    "fact",
    [
        _fact(subject=""),
        _fact(predicate=""),
        _fact(safe_source_ref=""),
        _fact(value=float("nan")),
    ],
)
def test_incomplete_or_noncanonical_fact_is_rejected(fact: ProviderFactInput) -> None:
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(_call(), _execution(facts=(fact,)))
    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    "safe_source_ref",
    [
        "https://viewer:masked@public.example/fact",
        "https://public.example/fact?api_key=masked",
        "https://public.example/fact?key=masked",
        "https://public.example/fact?AWSAccessKeyId=AKIAEXAMPLE",
        "https://public.example/fact?X-Amz-Signature=masked",
        "https://127.0.0.1/private",
        "https://PUBLIC.EXAMPLE:443/a/../fact?b=2&a=1",
        "public-source?access_token=masked",
        "C:/private/source",
    ],
)
def test_source_reference_rejects_credential_bearing_or_signed_values(
    safe_source_ref: str,
) -> None:
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(
            _call(),
            _execution(facts=(_fact(safe_source_ref=safe_source_ref),)),
        )

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    "domain",
    ["unknown_domain", FactDomain.FINANCIAL_DILIGENCE],
)
def test_unknown_or_provider_disallowed_fact_domain_fails_consumption(
    domain: FactDomain | str,
) -> None:
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(_call(), _execution(facts=(_fact(fact_domain=domain),)))
    assert exc_info.value.code == "ENRICHMENT_CONSUMPTION_MISSING"


def test_no_layer1_consumer_can_be_constructed() -> None:
    result = materialize_provider_intents(_call(), _execution())
    consumers = {edge.consumer_id for edge in result.consumer_edges}
    assert not any(value.startswith("DEBATE:") for value in consumers)
    assert not any("COURT" in value or "VEP" in value for value in consumers)


def test_external_context_rag_intent_and_edge_are_omitted_when_rights_disallow_indexing() -> None:
    call = _call(external_context_index_allowed=False)
    result = materialize_provider_intents(call, _execution(call=call))
    assert result.rag_intents == ()
    assert "RAG_EVIDENCE:external_context" not in {
        edge.consumer_id for edge in result.consumer_edges
    }
    assert "GRAPH_EVIDENCE:external_context" not in {
        edge.consumer_id for edge in result.consumer_edges
    }
    expected_non_index_consumers = (
        set(PROVIDER_CATALOG[call.provider_id].analysis_consumers)
        | (
            set(PROVIDER_CATALOG[call.provider_id].common_consumers)
            - {"RAG_EVIDENCE:external_context", "GRAPH_EVIDENCE:external_context"}
        )
        | set(
            deliverable_consumers_for(
                call.provider_id,
                FactDomain.COMMERCIAL_DILIGENCE,
            )
        )
    )
    assert {edge.consumer_id for edge in result.consumer_edges} == expected_non_index_consumers
    assert result.outcome.external_context_index_allowed is False


def test_external_context_rights_flag_is_immutable_and_participates_in_outcome_hash() -> None:
    allowed = _call(
        external_context_index_allowed=True,
        rights_approval_id="rights-store",
        rights_version_hash="1" * 64,
    )
    denied = _call(
        external_context_index_allowed=False,
        rights_approval_id="rights-no-store",
        rights_version_hash="2" * 64,
    )

    first = materialize_provider_intents(allowed, _execution(call=allowed))
    first_snapshot = (first.outcome.outcome_id, first.outcome.outcome_sha256)
    second = materialize_provider_intents(denied, _execution(call=denied))

    assert first.outcome.external_context_index_allowed is True
    assert second.outcome.external_context_index_allowed is False
    assert first.outcome.no_store_policy_version == EXTERNAL_CONTEXT_STORE_POLICY_V1
    assert second.outcome.no_store_policy_version == EXTERNAL_CONTEXT_NO_STORE_POLICY_V1
    assert first.outcome.attribution_policy_version == second.outcome.attribution_policy_version
    assert first_snapshot == (first.outcome.outcome_id, first.outcome.outcome_sha256)
    assert second.outcome.outcome_id != first.outcome.outcome_id
    assert second.outcome.outcome_sha256 != first.outcome.outcome_sha256
    assert first.rag_intents
    assert second.rag_intents == ()


def test_not_applicable_outcome_binds_closed_false_without_a_rights_row() -> None:
    admission = _admission(
        "finnhub",
        listing_state=ListingState.PRIVATE,
        applicability_status=ApplicabilityStatus.NOT_APPLICABLE,
    )
    assert isinstance(admission, ProviderNotApplicableIntent)

    result = materialize_provider_intents(admission, None)

    assert result.outcome.rights_approval_id is None
    assert result.outcome.rights_version_hash is None
    assert result.outcome.external_context_index_allowed is False


def test_unicode_equivalent_facts_have_one_canonical_identity() -> None:
    decomposed = _fact(
        subject="Cafe\u0301",
        predicate="re\u0301sume\u0301",
        value={"label": "Cafe\u0301"},
    )
    composed = _fact(
        subject="Caf\u00e9",
        predicate="r\u00e9sum\u00e9",
        value={"label": "Caf\u00e9"},
    )
    first = materialize_provider_intents(_call(), _execution(facts=(decomposed,)))
    second = materialize_provider_intents(_call(), _execution(facts=(composed,)))
    assert first == second


def test_rag_content_hash_and_consumer_edges_bind_complete_fact_scope() -> None:
    result = materialize_provider_intents(
        _call(),
        _execution(
            facts=(
                _fact(predicate="metric_one", value=42),
                _fact(predicate="metric_two", value=42),
            )
        ),
    )
    assert len({intent.content_sha256 for intent in result.rag_intents}) == 2
    for edge in result.consumer_edges:
        assert edge.deal_id == "deal-001"
        assert edge.fact_domain is FactDomain.COMMERCIAL_DILIGENCE


def _perplexity_evidence() -> tuple[PerplexityCitationEvidence, SupportedSynthesisUnit]:
    bundle = build_perplexity_evidence(
        citation_urls=("https://public.example/source",),
        provenance=PerplexityRetrievalProvenance(
            provider_request_id="request-public-001",
            retrieved_at=_NOW,
            response_sha256=_HASH,
        ),
    )
    citation = bundle.citations[0]
    synthesis = build_perplexity_evidence(
        citation_urls=(citation.canonical_url,),
        provenance=citation.provenance,
        synthesis_units=(
            SynthesisUnitInput(
                text="A citation-supported public synthesis.",
                citation_ids=(citation.citation_id,),
            ),
        ),
    ).synthesis_units[0]
    return citation, synthesis


def test_perplexity_fact_revalidates_citation_and_keeps_synthesis_non_primary() -> None:
    citation, synthesis = _perplexity_evidence()
    fact = _fact(
        subject="https://public.example/source",
        predicate="cited_source",
        value={"citation_id": citation.citation_id},
        safe_source_ref=citation.canonical_url,
        citation_evidence=citation,
    )
    call = _call("perplexity")
    result = materialize_provider_intents(
        call,
        _execution(call=call, facts=(fact,), supported_synthesis=(synthesis,)),
    )
    persisted = result.facts[0]
    assert persisted.citation_id == citation.citation_id
    assert persisted.source_tier is SourceTier.MAQBUL
    assert persisted.sanad_grade is SanadGrade.C
    assert persisted.tier_usage is TierUsage.SUPPORT_ONLY
    assert persisted.primary_eligible is False
    assert len(result.synthesis_intents) == 1
    assert result.synthesis_intents[0].primary_eligible is False
    assert result.synthesis_intents[0].citation_ids == (citation.citation_id,)


@pytest.mark.parametrize(
    "mutation",
    [
        {"citation_id": "forged-id"},
        {"canonical_url_sha256": "f" * 64},
        {"source_tier": SourceTier.THIQAH},
        {"sanad_grade": SanadGrade.A},
        {"tier_usage": TierUsage.PRIMARY},
        {"primary_eligible": True},
    ],
)
def test_forged_perplexity_citation_evidence_is_rejected(mutation: dict[str, object]) -> None:
    citation, _ = _perplexity_evidence()
    # Model an untrusted/deserialized instance that bypassed constructor invariants.
    forged = copy.copy(citation)
    for field_name, value in mutation.items():
        object.__setattr__(forged, field_name, value)
    fact = _fact(
        safe_source_ref=forged.canonical_url,
        citation_evidence=forged,
    )
    call = _call("perplexity")
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(call, _execution(call=call, facts=(fact,)))
    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


def test_perplexity_synthesis_cannot_satisfy_hit_without_citation_fact() -> None:
    _, synthesis = _perplexity_evidence()
    call = _call("perplexity")
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(
            call,
            _execution(call=call, facts=(), supported_synthesis=(synthesis,)),
        )
    assert exc_info.value.code == "ENRICHMENT_PROVIDER_FAILED"


def test_execution_and_persistence_schemas_bind_exact_request_provenance() -> None:
    assert {item.name for item in fields(ProviderExecutionResult)} >= {
        "provider_id",
        "admission_proof_sha256",
        "request_binding_sha256",
        "identifiers_used",
        "identifiers_used_sha256",
        "request_content_sha256",
        "connector_identity",
        "connector_version_hash",
        "request_builder_identity",
        "request_builder_version_hash",
        "operation_evidence",
    }
    assert {item.name for item in fields(normalized_facts.ProviderFactProvenanceIntent)} >= {
        "provider_id",
        "rights_class",
        "identity_listing_state",
        "identity_evidence_ids",
        "identity_provenance_ids",
        "identifiers_used",
        "request_binding_sha256",
        "request_content_sha256",
        "connector_identity",
        "connector_version_hash",
        "request_builder_identity",
    }
    assert {item.name for item in fields(normalized_facts.ProviderOutcomePersistenceIntent)} >= {
        "rights_class",
        "identity_listing_state",
        "identity_evidence_ids",
        "identity_provenance_ids",
        "identifiers_used",
        "request_binding_sha256",
        "request_content_sha256",
        "connector_identity",
        "connector_version_hash",
        "request_builder_identity",
    }


def test_cache_hit_has_typed_payload_and_refunded_released_terminal_intent() -> None:
    assert hasattr(normalized_facts, "CachedProviderOutcomePayload")
    assert hasattr(normalized_facts, "BudgetReservationDispositionIntent")
    assert {item.name for item in fields(normalized_facts.CachedProviderOutcomePayload)} >= {
        "outcome",
        "facts",
        "synthesis_intents",
    }
    assert {item.name for item in fields(normalized_facts.ProviderIntentBundle)} >= {
        "budget_reservation_disposition",
    }


def _source_and_cache_hit() -> tuple[
    ProviderCallIntent,
    normalized_facts.ProviderIntentBundle,
    ProviderCacheHitIntent,
    normalized_facts.CachedProviderOutcomePayload,
]:
    source_call = _call(run_id="run-source")
    source = materialize_provider_intents(
        source_call,
        _execution(call=source_call),
    )
    cache_admission = _admission(
        run_id="run-current",
        cache_disposition=CacheDisposition.HIT,
        cached_outcome_id=source.outcome.outcome_id,
        cached_outcome_sha256=source.outcome.outcome_sha256,
    )
    assert isinstance(cache_admission, ProviderCacheHitIntent)
    payload = normalized_facts.CachedProviderOutcomePayload(
        outcome=source.outcome,
        facts=source.facts,
        synthesis_intents=source.synthesis_intents,
    )
    return source_call, source, cache_admission, payload


def _perplexity_source_and_cache_hit() -> tuple[
    normalized_facts.ProviderIntentBundle,
    ProviderCacheHitIntent,
    normalized_facts.CachedProviderOutcomePayload,
]:
    citation, synthesis = _perplexity_evidence()
    source_call = _call("perplexity", run_id="run-source")
    fact = _fact(
        subject=citation.canonical_url,
        predicate="cited_source",
        value={"citation_id": citation.citation_id},
        safe_source_ref=citation.canonical_url,
        citation_evidence=citation,
    )
    source = materialize_provider_intents(
        source_call,
        _execution(
            call=source_call,
            facts=(fact,),
            supported_synthesis=(synthesis,),
        ),
    )
    cache_admission = _admission(
        "perplexity",
        run_id="run-current",
        cache_disposition=CacheDisposition.HIT,
        cached_outcome_id=source.outcome.outcome_id,
        cached_outcome_sha256=source.outcome.outcome_sha256,
    )
    assert isinstance(cache_admission, ProviderCacheHitIntent)
    return (
        source,
        cache_admission,
        normalized_facts.CachedProviderOutcomePayload(
            outcome=source.outcome,
            facts=source.facts,
            synthesis_intents=source.synthesis_intents,
        ),
    )


def test_perplexity_cache_hit_rematerializes_citation_bound_supported_synthesis() -> None:
    source, admission, payload = _perplexity_source_and_cache_hit()

    current = materialize_provider_intents(admission, payload)

    assert len(source.synthesis_intents) == len(current.synthesis_intents) == 1
    source_synthesis = source.synthesis_intents[0]
    current_synthesis = current.synthesis_intents[0]
    assert current_synthesis.synthesis_intent_id != source_synthesis.synthesis_intent_id
    assert current_synthesis.run_id == "run-current"
    assert current_synthesis.outcome_id == current.outcome.outcome_id
    assert current_synthesis.text == source_synthesis.text
    assert current_synthesis.text_sha256 == source_synthesis.text_sha256
    assert current_synthesis.citation_ids == source_synthesis.citation_ids
    assert current_synthesis.citation_fact_ids == (current.facts[0].fact_id,)
    assert current_synthesis.citation_fact_ids != source_synthesis.citation_fact_ids
    assert current_synthesis.source_tier is SourceTier.MAQBUL
    assert current_synthesis.sanad_grade is SanadGrade.C
    assert current_synthesis.tier_usage is TierUsage.SUPPORT_ONLY
    assert current_synthesis.primary_eligible is False
    assert current.outcome.synthesis_intent_ids == (current_synthesis.synthesis_intent_id,)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "not_tuple",
        "intent_id",
        "tenant_scope",
        "outcome_id",
        "text",
        "text_hash",
        "citation_ids",
        "citation_fact_ids",
        "source_tier",
        "sanad_grade",
        "tier_usage",
        "primary_eligible",
    ],
)
def test_cache_hit_rejects_missing_or_tampered_perplexity_synthesis(
    mutation: str,
) -> None:
    _, admission, payload = _perplexity_source_and_cache_hit()
    source = payload.synthesis_intents[0]
    if mutation == "missing":
        forged = replace(payload, synthesis_intents=())
    elif mutation == "duplicate":
        forged = replace(payload, synthesis_intents=(source, source))
    elif mutation == "not_tuple":
        forged = replace(payload, synthesis_intents=[source])  # type: ignore[arg-type]
    else:
        replacements: dict[str, object] = {
            "intent_id": {"synthesis_intent_id": "00000000-0000-5000-8000-000000000099"},
            "tenant_scope": {"tenant_id": "tenant-forged"},
            "outcome_id": {"outcome_id": "00000000-0000-5000-8000-000000000099"},
            "text": {"text": "Forged cached synthesis."},
            "text_hash": {"text_sha256": "f" * 64},
            "citation_ids": {"citation_ids": (f"perplexity-citation-{'f' * 64}",)},
            "citation_fact_ids": {"citation_fact_ids": ("00000000-0000-5000-8000-000000000098",)},
            "source_tier": {"source_tier": SourceTier.THIQAH},
            "sanad_grade": {"sanad_grade": SanadGrade.A},
            "tier_usage": {"tier_usage": TierUsage.PRIMARY},
            "primary_eligible": {"primary_eligible": True},
        }[mutation]
        forged = replace(payload, synthesis_intents=(replace(source, **replacements),))

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(admission, forged)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("text", _HostileString(_PRIVATE_CACHE_MARKER)),
        ("citation_ids", _HostileTuple(("perplexity-citation-hostile",))),
    ],
)
def test_cache_hit_rejects_type_confused_cached_synthesis_without_payload_leak(
    field_name: str,
    bad_value: object,
) -> None:
    _, admission, payload = _perplexity_source_and_cache_hit()
    forged_synthesis = replace(
        payload.synthesis_intents[0],
        **{field_name: bad_value},
    )
    forged = replace(payload, synthesis_intents=(forged_synthesis,))

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(admission, forged)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"
    assert exc_info.value.reason == "cached_synthesis_invalid"
    assert _PRIVATE_CACHE_MARKER not in str(exc_info.value)


def test_cache_hit_rejects_type_confused_synthesis_tuple_without_payload_leak() -> None:
    _, admission, payload = _perplexity_source_and_cache_hit()
    forged = replace(
        payload,
        synthesis_intents=_HostileTuple(payload.synthesis_intents),
    )

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(admission, forged)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"
    assert exc_info.value.reason == "cached_outcome_invalid"
    assert _PRIVATE_CACHE_MARKER not in str(exc_info.value)


def test_non_perplexity_cache_payload_rejects_unexpected_synthesis() -> None:
    _, _, admission, payload = _source_and_cache_hit()
    perplexity_source, _, _ = _perplexity_source_and_cache_hit()
    forged = replace(
        payload,
        synthesis_intents=perplexity_source.synthesis_intents,
    )

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(admission, forged)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("source_tier", SourceTier.THIQAH),
        ("sanad_grade", SanadGrade.A),
        ("tier_usage", TierUsage.PRIMARY),
        ("primary_eligible", True),
    ],
)
def test_cache_hit_rejects_perplexity_evidence_grade_elevation(
    field_name: str,
    bad_value: object,
) -> None:
    _, admission, payload = _perplexity_source_and_cache_hit()
    forged_fact = replace(payload.facts[0], **{field_name: bad_value})
    forged = replace(payload, facts=(forged_fact,))

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(admission, forged)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


def test_cache_hit_rejects_fact_retrieval_time_drift() -> None:
    _, admission, payload = _perplexity_source_and_cache_hit()
    forged_provenance = replace(
        payload.facts[0].provenance,
        retrieved_at=_NOW + timedelta(seconds=1),
    )
    forged = replace(
        payload,
        facts=(replace(payload.facts[0], provenance=forged_provenance),),
    )

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(admission, forged)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("identity_id", "forged-identity"),
        ("identity_version", "forged-identity-v999"),
        ("identity_rule_version", "forged-identity-rule-v999"),
        ("applicability_decision_hash", "0" * 64),
        (
            "applicability_decision_id",
            "00000000-0000-4000-8000-000000000099",
        ),
        ("applicability_decision_content_sha256", "0" * 64),
        ("applicability_rule_version", "forged-applicability-rule-v999"),
        ("applicability_rule_sha256", "0" * 64),
        ("applicability_reason_code", "FORGED_APPLICABILITY"),
        (
            "budget_reservation_id",
            "00000000-0000-4000-8000-000000000099",
        ),
        ("budget_policy_hash", "0" * 64),
        ("cache_decision_id", "forged-cache-decision"),
        ("cache_policy_hash", "0" * 64),
        ("cache_disposition", CacheDisposition.HIT),
        ("cached_outcome_id", "00000000-0000-4000-8000-000000000099"),
        ("cached_outcome_sha256", "0" * 64),
        ("cached_fact_id", "00000000-0000-4000-8000-000000000098"),
        ("cached_fact_content_sha256", "0" * 64),
        ("admission_proof_sha256", "0" * 64),
        ("catalog_version", "forged-catalog-v999"),
    ],
)
def test_cache_hit_rejects_every_omitted_source_provenance_family(
    field_name: str,
    bad_value: object,
) -> None:
    _, _, admission, payload = _source_and_cache_hit()
    forged_provenance = replace(
        payload.facts[0].provenance,
        **{field_name: bad_value},
    )
    forged = replace(
        payload,
        facts=(replace(payload.facts[0], provenance=forged_provenance),),
    )

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(admission, forged)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


def _recached_source_and_cache_hit() -> tuple[
    normalized_facts.ProviderIntentBundle,
    ProviderCacheHitIntent,
    normalized_facts.CachedProviderOutcomePayload,
]:
    _, _, first_admission, first_payload = _source_and_cache_hit()
    first_cached = materialize_provider_intents(first_admission, first_payload)
    second_admission = _admission(
        run_id="run-recached",
        cache_disposition=CacheDisposition.HIT,
        cached_outcome_id=first_cached.outcome.outcome_id,
        cached_outcome_sha256=first_cached.outcome.outcome_sha256,
    )
    assert isinstance(second_admission, ProviderCacheHitIntent)
    return (
        first_cached,
        second_admission,
        normalized_facts.CachedProviderOutcomePayload(
            outcome=first_cached.outcome,
            facts=first_cached.facts,
            synthesis_intents=first_cached.synthesis_intents,
        ),
    )


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("cached_outcome_id", "00000000-0000-4000-8000-000000000099"),
        ("cached_outcome_sha256", "0" * 64),
        ("cached_fact_id", None),
        ("cached_fact_id", "not-a-uuid"),
        ("cached_fact_content_sha256", None),
        ("cached_fact_content_sha256", "0" * 64),
    ],
)
def test_recached_fact_requires_complete_immediate_ancestry(
    field_name: str,
    bad_value: object,
) -> None:
    _, admission, payload = _recached_source_and_cache_hit()
    forged_provenance = replace(
        payload.facts[0].provenance,
        **{field_name: bad_value},
    )
    forged = replace(
        payload,
        facts=(replace(payload.facts[0], provenance=forged_provenance),),
    )

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(admission, forged)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


def test_recached_fact_preserves_complete_immediate_ancestry() -> None:
    source, admission, payload = _recached_source_and_cache_hit()

    current = materialize_provider_intents(admission, payload)

    assert current.outcome.cached_outcome_id == source.outcome.outcome_id
    assert current.outcome.cached_outcome_sha256 == source.outcome.outcome_sha256
    assert current.facts[0].provenance.cached_outcome_id == source.outcome.outcome_id
    assert current.facts[0].provenance.cached_outcome_sha256 == source.outcome.outcome_sha256
    assert current.facts[0].provenance.cached_fact_id == source.facts[0].fact_id
    assert (
        current.facts[0].provenance.cached_fact_content_sha256
        == source.facts[0].fact_content_sha256
    )


def test_cache_hit_revalidates_payload_and_emits_fresh_current_run_intents() -> None:
    _, source, cache_admission, payload = _source_and_cache_hit()

    current = materialize_provider_intents(cache_admission, payload)

    assert current.outcome.outcome_kind is ProviderOutcomeKind.HIT
    assert current.outcome.run_id == "run-current"
    assert current.outcome.outcome_id != source.outcome.outcome_id
    assert current.outcome.cached_outcome_id == source.outcome.outcome_id
    assert current.outcome.cached_outcome_sha256 == source.outcome.outcome_sha256
    assert current.outcome.cache_disposition is CacheDisposition.HIT
    assert len(current.facts) == len(source.facts) == 1
    assert current.facts[0].run_id == "run-current"
    assert current.facts[0].fact_id != source.facts[0].fact_id
    assert current.facts[0].canonical_value_json == source.facts[0].canonical_value_json
    assert {item.run_id for item in current.rag_intents} == {"run-current"}
    assert {item.run_id for item in current.consumer_edges} == {"run-current"}
    disposition = current.budget_reservation_disposition
    assert disposition is not None
    assert disposition.disposition is normalized_facts.BudgetReservationDisposition.REFUNDED
    assert disposition.reservation_state is normalized_facts.BudgetReservationState.RELEASED
    assert disposition.reservation_id == cache_admission.budget_reservation_id
    assert disposition.run_id == "run-current"


def test_cache_hit_rejects_bare_cached_id_and_hash_without_typed_payload() -> None:
    _, _, cache_admission, _ = _source_and_cache_hit()

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(cache_admission, None)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


@pytest.mark.parametrize(
    "mutation",
    [
        "outcome_hash",
        "outcome_id",
        "no_store_policy_version",
        "attribution_policy_version",
        "fact",
        "facts_missing",
    ],
)
def test_cache_hit_rejects_tampered_cached_outcome_fact_or_provenance_payload(
    mutation: str,
) -> None:
    _, _, cache_admission, raw_payload = _source_and_cache_hit()
    payload = raw_payload
    assert isinstance(payload, normalized_facts.CachedProviderOutcomePayload)
    if mutation == "outcome_hash":
        payload = replace(
            payload,
            outcome=replace(payload.outcome, outcome_sha256="f" * 64),
        )
    elif mutation == "outcome_id":
        payload = replace(
            payload,
            outcome=replace(
                payload.outcome,
                outcome_id="00000000-0000-4000-8000-000000000099",
            ),
        )
    elif mutation == "no_store_policy_version":
        payload = replace(
            payload,
            outcome=replace(
                payload.outcome,
                no_store_policy_version=EXTERNAL_CONTEXT_NO_STORE_POLICY_V1,
            ),
        )
    elif mutation == "attribution_policy_version":
        payload = replace(
            payload,
            outcome=replace(
                payload.outcome,
                attribution_policy_version="forged-attribution-policy-v999",
            ),
        )
    elif mutation == "fact":
        forged_provenance = replace(
            payload.facts[0].provenance,
            response_sha256="e" * 64,
        )
        payload = replace(
            payload,
            facts=(replace(payload.facts[0], provenance=forged_provenance),),
        )
    else:
        payload = replace(payload, facts=())

    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(cache_admission, payload)

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


def test_rights_class_is_persisted_in_outcome_and_every_fact_provenance() -> None:
    result = materialize_provider_intents(_call(), _execution())

    assert result.outcome.rights_class is RightsClass.GREEN
    assert result.outcome.no_store_policy_version == EXTERNAL_CONTEXT_STORE_POLICY_V1
    assert result.outcome.attribution_policy_version == ATTRIBUTION_NOT_REQUIRED_POLICY_V1
    assert {fact.provenance.rights_class for fact in result.facts} == {RightsClass.GREEN}
    assert {fact.provenance.no_store_policy_version for fact in result.facts} == {
        EXTERNAL_CONTEXT_STORE_POLICY_V1
    }
    assert {fact.provenance.attribution_policy_version for fact in result.facts} == {
        ATTRIBUTION_NOT_REQUIRED_POLICY_V1
    }


@pytest.mark.parametrize("mutation", ["whitespace", "duplicate_citations", "unsorted_citations"])
def test_perplexity_synthesis_is_revalidated_at_persistence_boundary(mutation: str) -> None:
    citation, synthesis = _perplexity_evidence()
    second_bundle = build_perplexity_evidence(
        citation_urls=("https://public.example/second",),
        provenance=citation.provenance,
    )
    second = second_bundle.citations[0]
    facts = tuple(
        _fact(
            subject=item.canonical_url,
            predicate="cited_source",
            value={"citation_id": item.citation_id},
            safe_source_ref=item.canonical_url,
            citation_evidence=item,
        )
        for item in (citation, second)
    )
    forged = copy.copy(synthesis)
    if mutation == "whitespace":
        text = f" {synthesis.text} "
        object.__setattr__(forged, "text", text)
        object.__setattr__(forged, "text_sha256", normalized_facts._sha256_text(text))
    elif mutation == "duplicate_citations":
        object.__setattr__(
            forged,
            "citation_ids",
            (citation.citation_id, citation.citation_id),
        )
    else:
        ordered = tuple(sorted((citation.citation_id, second.citation_id), reverse=True))
        object.__setattr__(forged, "citation_ids", ordered)

    call = _call("perplexity")
    with pytest.raises(NormalizedFactsError) as exc_info:
        materialize_provider_intents(
            call,
            _execution(call=call, facts=facts, supported_synthesis=(forged,)),
        )

    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"
