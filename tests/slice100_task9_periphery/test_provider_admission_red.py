"""RED-first tests for ordered, zero-I/O Task 9 provider admission."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import idis.services.enrichment.task9.provider_admission as provider_admission
from idis.services.enrichment.models import RightsClass
from idis.services.enrichment.task9.provider_admission import (
    AdmissionCollaborators,
    ApplicabilityDecision,
    ApplicabilityStatus,
    BudgetReservationSnapshot,
    CacheDecisionSnapshot,
    CacheDisposition,
    CredentialVersionSnapshot,
    IdentifierSelectorRule,
    IdentityVerificationBasis,
    ListingState,
    ProviderAdmissionError,
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
    identifier_selector_rule_for,
)
from idis.services.enrichment.task9.provider_catalog import (
    PROVIDER_CATALOG,
    PROVIDER_CATALOG_VERSION,
    RightsRequirement,
)
from idis.services.enrichment.task9.rights_index_policy import (
    PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY,
)

_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
_HASH = "a" * 64


def _unsafe_replace(instance: Any, mutation: dict[str, object]) -> Any:
    """Build deliberately ill-typed frozen snapshots for boundary fuzzing."""

    return replace(instance, **mutation)


def _identity(
    *,
    listing_state: ListingState = ListingState.LISTED,
    basis: IdentityVerificationBasis = IdentityVerificationBasis.VERIFIED_EVIDENCE,
    identifiers: tuple[tuple[str, str], ...] = (("listed_ticker", "AAPL"),),
) -> VerifiedIdentitySnapshot:
    identity = VerifiedIdentitySnapshot(
        tenant_id="tenant-001",
        deal_id="deal-001",
        identity_id="identity-001",
        identity_version="identity-v3",
        identity_rule_version="company-identity-v3",
        listing_state=listing_state,
        verification_basis=basis,
        identifiers=identifiers,
        evidence_ids=("evidence-001",),
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


def _applicability(
    identity: VerifiedIdentitySnapshot,
    *,
    provider_id: str = "finnhub",
    status: ApplicabilityStatus = ApplicabilityStatus.APPLICABLE,
) -> ApplicabilityDecision:
    decision = ApplicabilityDecision(
        decision_id="00000000-0000-4000-8000-000000000011",
        provider_id=provider_id,
        status=status,
        identity_id=identity.identity_id,
        identity_version=identity.identity_version,
        identity_hash=identity.identity_hash,
        rule_version="listing-applicability-v1",
        rule_sha256="7" * 64,
        decision_hash="",
        decision_content_sha256="",
        reason_code=(
            "VERIFIED_LISTED" if status is ApplicabilityStatus.APPLICABLE else "VERIFIED_NOT_LISTED"
        ),
    )
    content_sha256 = canonical_applicability_decision_content_sha256(decision)
    return replace(
        decision,
        decision_hash=content_sha256,
        decision_content_sha256=content_sha256,
    )


def _rights(provider_id: str = "finnhub") -> RightsSnapshot:
    # Invalid applicability fixtures must still reach the production validator rather than
    # failing while this test helper constructs an otherwise-unused collaborator snapshot.
    policy = PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY.get(
        provider_id,
        PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY["finnhub"],
    )
    return RightsSnapshot(
        tenant_id="tenant-001",
        rights_approval_id="rights-001",
        provider_id=provider_id,
        environment="PROD",
        rights_class=RightsClass.RED,
        rights_requirement=RightsRequirement.COMMERCIAL_PROD,
        status=RightsStatus.ACTIVE,
        rights_version_hash="c" * 64,
        credential_version_id=(
            "credential-001" if provider_id in {"finnhub", "fmp", "perplexity"} else None
        ),
        expires_at=_NOW + timedelta(days=30),
        no_store_policy_version=policy.no_store_policy_version,
        attribution_policy_version=policy.attribution_policy_version,
    )


def _credential(
    provider_id: str = "finnhub",
    keys: tuple[str, ...] = ("api_key",),
) -> CredentialVersionSnapshot:
    return CredentialVersionSnapshot(
        tenant_id="tenant-001",
        credential_version_id="credential-001",
        provider_id=provider_id,
        credential_keys=keys,
        encrypted=True,
        active=True,
        ciphertext_sha256="d" * 64,
        bundle_version_hash="e" * 64,
    )


def _budget(provider_id: str = "finnhub") -> BudgetReservationSnapshot:
    return BudgetReservationSnapshot(
        tenant_id="tenant-001",
        deal_id="deal-001",
        run_id="run-001",
        reservation_id="00000000-0000-4000-8000-000000000001",
        provider_id=provider_id,
        policy_hash="f" * 64,
        signed_policy_id="policy-001",
        active=True,
        signature_verified=True,
        expires_at=_NOW + timedelta(minutes=5),
    )


def _cache(
    rights: RightsSnapshot,
    *,
    identity: VerifiedIdentitySnapshot | None = None,
    required_identifiers: tuple[tuple[str, str], ...] = (("listed_ticker", "AAPL"),),
    credential_version_id: str | None = "credential-001",
) -> CacheDecisionSnapshot:
    identity = identity or _identity()
    return CacheDecisionSnapshot(
        tenant_id="tenant-001",
        deal_id="deal-001",
        run_id="run-001",
        provider_id=rights.provider_id,
        identity_id=identity.identity_id,
        identity_version=identity.identity_version,
        identity_hash=identity.identity_hash,
        required_identifiers=required_identifiers,
        identifiers_used_sha256=canonical_identifiers_sha256(required_identifiers),
        credential_version_id=credential_version_id,
        catalog_version=PROVIDER_CATALOG_VERSION,
        decision_id="cache-decision-001",
        disposition=CacheDisposition.MISS,
        rights_approval_id=rights.rights_approval_id,
        rights_version_hash=rights.rights_version_hash,
        rights_compatible=True,
        policy_hash="1" * 64,
    )


class _CollaboratorStack:
    def __init__(
        self,
        *,
        identity: VerifiedIdentitySnapshot,
        applicability: ApplicabilityDecision,
        rights: RightsSnapshot | None = None,
        credential: CredentialVersionSnapshot | None = None,
        budget: BudgetReservationSnapshot | None = None,
        cache: CacheDecisionSnapshot | None = None,
    ) -> None:
        self.identity = identity
        self.applicability = applicability
        self.rights = rights or _rights(applicability.provider_id)
        self.credential = credential or _credential(applicability.provider_id)
        self.budget = budget or _budget(applicability.provider_id)
        self.cache = cache
        self.calls: list[str] = []

    def resolve_identity(self, request: ProviderAdmissionRequest) -> VerifiedIdentitySnapshot:
        self.calls.append("identity")
        return self.identity

    def decide_applicability(
        self, identity: VerifiedIdentitySnapshot, provider_id: str
    ) -> ApplicabilityDecision:
        self.calls.append("applicability")
        return self.applicability

    def resolve_rights(self, provider_id: str, tenant_id: str) -> RightsSnapshot:
        self.calls.append("rights")
        return self.rights

    def resolve_credentials(
        self, provider_id: str, tenant_id: str
    ) -> CredentialVersionSnapshot | None:
        self.calls.append("credential")
        return self.credential

    def reserve_budget(
        self, provider_id: str, tenant_id: str, deal_id: str, run_id: str
    ) -> BudgetReservationSnapshot:
        self.calls.append("budget")
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
        self.calls.append("cache")
        return self.cache or _cache(
            self.rights,
            identity=identity,
            required_identifiers=required_identifiers,
            credential_version_id=credential_version_id,
        )

    def collaborators(self) -> AdmissionCollaborators:
        return AdmissionCollaborators(
            identity_resolver=self,
            applicability_resolver=self,
            rights_resolver=self,
            credential_resolver=self,
            budget_reserver=self,
            cache_resolver=self,
        )


def _request(provider_id: str = "finnhub") -> ProviderAdmissionRequest:
    return ProviderAdmissionRequest(
        tenant_id="tenant-001",
        deal_id="deal-001",
        run_id="run-001",
        provider_id=provider_id,
        expected_identity_version="identity-v3",
        evaluated_at=_NOW,
    )


def test_applicable_provider_is_admitted_in_exact_fail_closed_order() -> None:
    identity = _identity()
    stack = _CollaboratorStack(identity=identity, applicability=_applicability(identity))

    result = admit_provider(_request(), stack.collaborators())

    assert isinstance(result, ProviderCallIntent)
    assert stack.calls == ["identity", "applicability", "rights", "credential", "budget", "cache"]
    assert result.required_identifiers == (("listed_ticker", "AAPL"),)
    assert result.identity_version == "identity-v3"
    assert result.identity_rule_version == "company-identity-v3"
    assert result.identity_evidence_ids == identity.evidence_ids
    assert result.identity_provenance_ids == identity.provenance_ids
    assert result.identity_evidence_set_sha256 == identity.evidence_set_sha256
    assert result.identity_provenance_set_sha256 == identity.provenance_set_sha256
    assert result.applicability_rule_sha256 == "7" * 64
    assert result.external_context_index_allowed is True
    assert result.no_store_policy_version == stack.rights.no_store_policy_version
    assert result.attribution_policy_version == stack.rights.attribution_policy_version
    assert result.credential_version_id == "credential-001"
    assert result.rights_class is RightsClass.RED
    assert result.identifiers_used_sha256 == canonical_identifiers_sha256(
        result.required_identifiers
    )
    assert len(result.request_binding_sha256) == 64
    assert len(result.admission_proof_sha256) == 64
    assert (
        result.connector_identity == PROVIDER_CATALOG["finnhub"].runtime_lineage.connector_identity
    )
    assert (
        result.request_builder_version_hash
        == PROVIDER_CATALOG["finnhub"].runtime_lineage.request_builder_version_hash
    )
    assert "ciphertext" not in repr(result).lower()
    assert "api_key" not in repr(result)


@pytest.mark.parametrize("listing_state", [ListingState.PRIVATE, ListingState.UNLISTED])
@pytest.mark.parametrize("provider_id", ["finnhub", "fmp"])
def test_verified_private_or_unlisted_financial_provider_stops_before_rights(
    listing_state: ListingState,
    provider_id: str,
) -> None:
    identity = _identity(listing_state=listing_state, identifiers=())
    decision = _applicability(
        identity,
        provider_id=provider_id,
        status=ApplicabilityStatus.NOT_APPLICABLE,
    )
    stack = _CollaboratorStack(identity=identity, applicability=decision)

    result = admit_provider(_request(provider_id), stack.collaborators())

    assert isinstance(result, ProviderNotApplicableIntent)
    assert result.decision == decision
    assert result.identity_evidence_ids == identity.evidence_ids
    assert result.identity_provenance_ids == identity.provenance_ids
    assert result.identity_evidence_set_sha256 == identity.evidence_set_sha256
    assert result.identity_provenance_set_sha256 == identity.provenance_set_sha256
    assert result.zero_call is True
    assert result.listing_state is listing_state
    assert result.applicability_decision_id == decision.decision_id
    assert result.applicability_decision_content_sha256 == decision.decision_content_sha256
    assert result.catalog_version == PROVIDER_CATALOG_VERSION
    assert len(result.admission_proof_sha256) == 64
    assert stack.calls == ["identity", "applicability"]
    assert not hasattr(result, "rights_approval_id")
    assert not hasattr(result, "credential_version_id")
    assert not hasattr(result, "reservation_id")
    assert not hasattr(result, "cache_decision_id")
    assert not hasattr(result, "fact_intents")
    assert not hasattr(result, "rag_intents")
    assert not hasattr(result, "consumer_intents")


@pytest.mark.parametrize(
    ("identity", "decision_status", "expected_code"),
    [
        (
            _identity(listing_state=ListingState.UNKNOWN, identifiers=()),
            ApplicabilityStatus.NOT_APPLICABLE,
            "COMPANY_IDENTITY_UNVERIFIED",
        ),
        (
            _identity(
                listing_state=ListingState.PRIVATE,
                basis=IdentityVerificationBasis.OPERATOR_SEED,
                identifiers=(),
            ),
            ApplicabilityStatus.NOT_APPLICABLE,
            "COMPANY_IDENTITY_UNVERIFIED",
        ),
        (
            _identity(
                listing_state=ListingState.PRIVATE,
                basis=IdentityVerificationBasis.DEAL_UUID,
                identifiers=(),
            ),
            ApplicabilityStatus.NOT_APPLICABLE,
            "COMPANY_IDENTITY_UNVERIFIED",
        ),
        (
            _identity(listing_state=ListingState.LISTED, identifiers=()),
            ApplicabilityStatus.APPLICABLE,
            "ENRICHMENT_PROVIDER_INPUT_MISSING",
        ),
        (
            _identity(listing_state=ListingState.LISTED),
            ApplicabilityStatus.NOT_APPLICABLE,
            "ENRICHMENT_PROVIDER_INPUT_MISSING",
        ),
    ],
)
def test_false_not_applicable_paths_fail_before_rights(
    identity: VerifiedIdentitySnapshot,
    decision_status: ApplicabilityStatus,
    expected_code: str,
) -> None:
    stack = _CollaboratorStack(
        identity=identity,
        applicability=_applicability(identity, status=decision_status),
    )
    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())
    assert exc_info.value.code == expected_code
    assert "rights" not in stack.calls


def test_wrong_identity_version_fails_before_applicability() -> None:
    identity = replace(_identity(), identity_version="identity-v2")
    stack = _CollaboratorStack(identity=identity, applicability=_applicability(identity))
    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())
    assert exc_info.value.code == "COMPANY_IDENTITY_UNVERIFIED"
    assert stack.calls == ["identity"]


@pytest.mark.parametrize(
    "identity",
    [
        replace(_identity(), identity_rule_version=""),
        replace(_identity(), evidence_set_sha256="bad"),
        replace(_identity(), provenance_set_sha256="bad"),
        replace(_identity(), evidence_ids=("",)),
        replace(_identity(), provenance_ids=("",)),
    ],
)
def test_identity_evidence_and_rule_lineage_is_mandatory_before_applicability(
    identity: VerifiedIdentitySnapshot,
) -> None:
    stack = _CollaboratorStack(identity=identity, applicability=_applicability(identity))
    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())
    assert exc_info.value.code == "COMPANY_IDENTITY_UNVERIFIED"
    assert stack.calls == ["identity"]


@pytest.mark.parametrize(
    "rights",
    [
        replace(_rights(), environment="DEV"),
        replace(_rights(), status=RightsStatus.REVOKED),
        replace(_rights(), status=RightsStatus.UNAPPROVED),
        replace(_rights(), expires_at=_NOW),
        replace(_rights(), rights_class=RightsClass.YELLOW),
        replace(_rights(), rights_requirement=RightsRequirement.ATTRIBUTION_PROD),
    ],
)
def test_commercial_rights_must_be_active_exact_prod_entitlement(rights: RightsSnapshot) -> None:
    identity = _identity()
    stack = _CollaboratorStack(
        identity=identity,
        applicability=_applicability(identity),
        rights=rights,
    )
    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())
    assert exc_info.value.code == "ENRICHMENT_LICENSE_BLOCKED"
    assert stack.calls == ["identity", "applicability", "rights"]


@pytest.mark.parametrize(
    "credential",
    [
        replace(_credential(), encrypted=False),
        replace(_credential(), active=False),
        replace(_credential(), ciphertext_sha256="bad"),
        replace(_credential(), credential_keys=()),
    ],
)
def test_missing_unencrypted_or_mismatched_credentials_fail_before_budget(
    credential: CredentialVersionSnapshot,
) -> None:
    identity = _identity()
    stack = _CollaboratorStack(
        identity=identity,
        applicability=_applicability(identity),
        credential=credential,
    )
    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())
    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert "budget" not in stack.calls


def test_epo_key_and_secret_must_share_one_complete_credential_version() -> None:
    identity = _identity(identifiers=(("assignee", "Public Example Corp"),))
    decision = _applicability(identity, provider_id="epo_open_patent")
    rights = replace(
        _rights("epo_open_patent"),
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
    )
    partial = _credential("epo_open_patent", keys=("consumer_key",))
    stack = _CollaboratorStack(
        identity=identity,
        applicability=decision,
        rights=rights,
        credential=partial,
        budget=_budget("epo_open_patent"),
    )
    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request("epo_open_patent"), stack.collaborators())
    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert stack.calls == ["identity", "applicability", "rights", "credential"]


@pytest.mark.parametrize(
    ("budget", "expected_code"),
    [
        (replace(_budget(), active=False), "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        (replace(_budget(), signature_verified=False), "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        (replace(_budget(), expires_at=_NOW), "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        (replace(_budget(), provider_id="other"), "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED"),
    ],
)
def test_invalid_budget_reservation_fails_before_cache(
    budget: BudgetReservationSnapshot,
    expected_code: str,
) -> None:
    identity = _identity()
    stack = _CollaboratorStack(
        identity=identity,
        applicability=_applicability(identity),
        budget=budget,
    )
    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())
    assert exc_info.value.code == expected_code
    assert "cache" not in stack.calls


def test_cache_must_bind_the_selected_rights_version() -> None:
    identity = _identity()
    rights = _rights()
    stack = _CollaboratorStack(
        identity=identity,
        applicability=_applicability(identity),
        rights=rights,
        cache=replace(_cache(rights, identity=identity), rights_approval_id="different-rights"),
    )
    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())
    assert exc_info.value.code == "ENRICHMENT_RIGHTS_BLOCKED"
    assert stack.calls[-1] == "cache"


def test_rights_compatible_cache_hit_never_mints_a_provider_call_intent() -> None:
    identity = _identity()
    rights = _rights()
    cache_hit = replace(
        _cache(rights, identity=identity),
        disposition=CacheDisposition.HIT,
        cached_outcome_id="00000000-0000-4000-8000-000000000099",
        cached_outcome_sha256="9" * 64,
    )
    stack = _CollaboratorStack(
        identity=identity,
        applicability=_applicability(identity),
        rights=rights,
        cache=cache_hit,
    )

    result = admit_provider(_request(), stack.collaborators())

    assert isinstance(result, ProviderCacheHitIntent)
    assert not isinstance(result, ProviderCallIntent)
    assert result.no_provider_call is True
    assert result.cached_outcome_id == cache_hit.cached_outcome_id
    assert result.cached_outcome_sha256 == cache_hit.cached_outcome_sha256
    assert stack.calls == ["identity", "applicability", "rights", "credential", "budget", "cache"]


@pytest.mark.parametrize(
    "cache",
    [
        replace(_cache(_rights()), disposition=CacheDisposition.HIT),
        replace(_cache(_rights()), disposition="HIT"),  # type: ignore[arg-type]
        replace(_cache(_rights()), disposition="invented"),  # type: ignore[arg-type]
    ],
)
def test_cache_hit_or_untyped_disposition_without_bound_outcome_fails_closed(
    cache: CacheDecisionSnapshot,
) -> None:
    identity = _identity()
    stack = _CollaboratorStack(
        identity=identity,
        applicability=_applicability(identity),
        cache=cache,
    )
    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())
    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"


def test_untyped_applicability_status_cannot_fall_through_as_applicable() -> None:
    identity = _identity()
    decision = replace(
        _applicability(identity),
        status="APPLICABLE",  # type: ignore[arg-type]
    )
    stack = _CollaboratorStack(identity=identity, applicability=decision)

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())

    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert stack.calls == ["identity", "applicability"]


def test_admission_snapshots_expose_every_required_scope_binding() -> None:
    assert {item.name for item in fields(RightsSnapshot)} >= {
        "tenant_id",
        "credential_version_id",
        "no_store_policy_version",
        "attribution_policy_version",
    }
    assert {item.name for item in fields(ProviderCallIntent)} >= {
        "no_store_policy_version",
        "attribution_policy_version",
    }
    assert "rag_index_allowed" not in {item.name for item in fields(RightsSnapshot)}
    assert {item.name for item in fields(CredentialVersionSnapshot)} >= {"tenant_id"}
    assert {item.name for item in fields(BudgetReservationSnapshot)} >= {
        "tenant_id",
        "run_id",
    }
    assert {item.name for item in fields(CacheDecisionSnapshot)} >= {
        "tenant_id",
        "provider_id",
        "identity_id",
        "identity_version",
        "identity_hash",
        "required_identifiers",
        "identifiers_used_sha256",
        "credential_version_id",
        "catalog_version",
    }


def test_not_applicable_intent_carries_positive_state_decision_and_admission_proof() -> None:
    assert {item.name for item in fields(ProviderNotApplicableIntent)} >= {
        "listing_state",
        "applicability_decision_id",
        "applicability_decision_content_sha256",
        "catalog_version",
        "admission_proof_sha256",
    }


def test_catalog_selector_rules_distinguish_compounds_from_alternatives() -> None:
    fred = identifier_selector_rule_for("fred")
    patents = identifier_selector_rule_for("patentsview")
    semantic = identifier_selector_rule_for("semantic_scholar")

    assert isinstance(fred, IdentifierSelectorRule)
    assert fred.alternatives == (("macro_series", "jurisdiction"),)
    assert patents.alternatives == (("assignee",), ("legal_name",))
    assert semantic.alternatives == (
        ("semantic_scholar_paper_id",),
        ("semantic_scholar_author_id",),
        ("semantic_scholar_topic_query", "semantic_scholar_field_of_study"),
        ("ror_id", "semantic_scholar_author_id"),
    )


@pytest.mark.parametrize(
    "identifiers",
    [
        (("semantic_scholar_topic_query", "public topic"),),
        (
            ("semantic_scholar_paper_id", "CorpusId:1"),
            ("semantic_scholar_author_id", "author-1"),
        ),
    ],
)
def test_semantic_partial_compound_or_mixed_alternatives_fail_before_rights(
    identifiers: tuple[tuple[str, str], ...],
) -> None:
    identity = _identity(identifiers=identifiers)
    decision = _applicability(identity, provider_id="semantic_scholar")
    stack = _CollaboratorStack(identity=identity, applicability=decision)

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request("semantic_scholar"), stack.collaborators())

    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert stack.calls == ["identity", "applicability"]


def test_alternative_selector_accepts_one_complete_cataloged_alternative() -> None:
    identity = _identity(identifiers=(("legal_name", "Public Example Corp"),))
    decision = _applicability(identity, provider_id="patentsview")
    rights = replace(
        _rights("patentsview"),
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
    )
    stack = _CollaboratorStack(
        identity=identity,
        applicability=decision,
        rights=rights,
        credential=_credential("patentsview"),
        budget=_budget("patentsview"),
    )

    result = admit_provider(_request("patentsview"), stack.collaborators())

    assert isinstance(result, ProviderCallIntent)
    assert result.required_identifiers == (("legal_name", "Public Example Corp"),)


@pytest.mark.parametrize(
    ("component", "mutation", "expected_code"),
    [
        ("rights", {"tenant_id": "other-tenant"}, "ENRICHMENT_LICENSE_BLOCKED"),
        ("credential", {"tenant_id": "other-tenant"}, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("budget", {"tenant_id": "other-tenant"}, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("budget", {"run_id": "other-run"}, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
    ],
)
def test_snapshot_scope_mismatch_is_rejected(
    component: str,
    mutation: dict[str, object],
    expected_code: str,
) -> None:
    identity = _identity()
    kwargs: dict[str, object] = {
        "identity": identity,
        "applicability": _applicability(identity),
    }
    if component == "rights":
        kwargs["rights"] = _unsafe_replace(_rights(), mutation)
    elif component == "credential":
        kwargs["credential"] = _unsafe_replace(_credential(), mutation)
    else:
        kwargs["budget"] = _unsafe_replace(_budget(), mutation)
    stack = _CollaboratorStack(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())

    assert exc_info.value.code == expected_code
    assert "NoneType" not in str(exc_info.value)


def test_red_rights_must_link_the_exact_resolved_credential_version() -> None:
    identity = _identity()
    stack = _CollaboratorStack(
        identity=identity,
        applicability=_applicability(identity),
        rights=replace(_rights(), credential_version_id="credential-other"),
        credential=_credential(),
    )

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())

    assert exc_info.value.code == "ENRICHMENT_LICENSE_BLOCKED"
    assert stack.calls == ["identity", "applicability", "rights", "credential"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"tenant_id": "other-tenant"},
        {"provider_id": "fmp"},
        {"identity_id": "other-identity"},
        {"identity_version": "identity-v2"},
        {"identity_hash": "b" * 64},
        {"required_identifiers": (("listed_ticker", "MSFT"),)},
        {"identifiers_used_sha256": "2" * 64},
        {"credential_version_id": "credential-other"},
        {"catalog_version": "catalog-other"},
        {"rights_version_hash": "2" * 64},
    ],
)
def test_cache_decision_must_bind_the_complete_admission_context(
    mutation: dict[str, object],
) -> None:
    identity = _identity()
    rights = _rights()
    stack = _CollaboratorStack(
        identity=identity,
        applicability=_applicability(identity),
        rights=rights,
        cache=_unsafe_replace(_cache(rights, identity=identity), mutation),
    )

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())

    assert exc_info.value.code in {
        "ENRICHMENT_PROVENANCE_MISSING",
        "ENRICHMENT_RIGHTS_BLOCKED",
    }


def test_fred_requires_the_complete_macro_series_and_jurisdiction_selector() -> None:
    identity = _identity(identifiers=(("macro_series", "GDP"),))
    decision = _applicability(identity, provider_id="fred")
    rights = replace(
        _rights("fred"),
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
    )
    stack = _CollaboratorStack(
        identity=identity,
        applicability=decision,
        rights=rights,
        credential=_credential("fred"),
        budget=_budget("fred"),
    )

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request("fred"), stack.collaborators())

    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert stack.calls == ["identity", "applicability"]


def test_noncommercial_rights_row_cannot_carry_a_credential_version_link() -> None:
    identity = _identity(identifiers=(("macro_series", "GDP"), ("jurisdiction", "QAT")))
    decision = _applicability(identity, provider_id="fred")
    rights = replace(
        _rights("fred"),
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        credential_version_id="credential-001",
    )
    stack = _CollaboratorStack(
        identity=identity,
        applicability=decision,
        rights=rights,
        credential=_credential("fred"),
        budget=_budget("fred"),
    )

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request("fred"), stack.collaborators())

    assert exc_info.value.code == "ENRICHMENT_RIGHTS_BLOCKED"
    assert stack.calls == ["identity", "applicability", "rights"]


@pytest.mark.parametrize(
    ("component", "field_name", "bad_value", "expected_code"),
    [
        ("identity", "identity_id", None, "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "identifiers", None, "COMPANY_IDENTITY_UNVERIFIED"),
        ("applicability", "rule_version", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("rights", "rights_approval_id", None, "ENRICHMENT_LICENSE_BLOCKED"),
        ("rights", "expires_at", None, "ENRICHMENT_LICENSE_BLOCKED"),
        ("credential", "credential_version_id", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("credential", "credential_keys", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("budget", "reservation_id", None, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("budget", "expires_at", None, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("cache", "policy_hash", None, "ENRICHMENT_PROVENANCE_MISSING"),
    ],
)
def test_malformed_collaborator_fields_never_leak_python_exceptions(
    component: str,
    field_name: str,
    bad_value: object,
    expected_code: str,
) -> None:
    identity = _identity()
    applicability = _applicability(identity)
    kwargs: dict[str, object] = {"identity": identity, "applicability": applicability}
    if component == "identity":
        kwargs["identity"] = _unsafe_replace(identity, {field_name: bad_value})
    elif component == "applicability":
        kwargs["applicability"] = _unsafe_replace(applicability, {field_name: bad_value})
    elif component == "rights":
        kwargs["rights"] = _unsafe_replace(_rights(), {field_name: bad_value})
    elif component == "credential":
        kwargs["credential"] = _unsafe_replace(_credential(), {field_name: bad_value})
    elif component == "budget":
        kwargs["budget"] = _unsafe_replace(_budget(), {field_name: bad_value})
    else:
        kwargs["cache"] = _unsafe_replace(_cache(_rights()), {field_name: bad_value})
    stack = _CollaboratorStack(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    ("component", "field_name", "bad_value", "expected_code"),
    [
        ("identity", "tenant_id", None, "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "deal_id", 7, "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "identity_version", None, "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "identity_rule_version", 7, "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "listing_state", "LISTED", "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "verification_basis", object(), "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "evidence_ids", None, "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "evidence_set_sha256", 7, "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "provenance_ids", None, "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "provenance_set_sha256", 7, "COMPANY_IDENTITY_UNVERIFIED"),
        ("identity", "identity_hash", None, "COMPANY_IDENTITY_UNVERIFIED"),
        ("applicability", "decision_id", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("applicability", "provider_id", 7, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("applicability", "status", "APPLICABLE", "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("applicability", "identity_id", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("applicability", "identity_version", 7, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("applicability", "identity_hash", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("applicability", "rule_sha256", 7, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("applicability", "decision_hash", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        (
            "applicability",
            "decision_content_sha256",
            7,
            "ENRICHMENT_PROVIDER_INPUT_MISSING",
        ),
        ("applicability", "reason_code", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("rights", "tenant_id", None, "ENRICHMENT_LICENSE_BLOCKED"),
        ("rights", "provider_id", 7, "ENRICHMENT_LICENSE_BLOCKED"),
        ("rights", "environment", None, "ENRICHMENT_LICENSE_BLOCKED"),
        ("rights", "rights_class", "RED", "ENRICHMENT_LICENSE_BLOCKED"),
        (
            "rights",
            "rights_requirement",
            "COMMERCIAL_PROD",
            "ENRICHMENT_LICENSE_BLOCKED",
        ),
        ("rights", "status", "ACTIVE", "ENRICHMENT_LICENSE_BLOCKED"),
        ("rights", "rights_version_hash", None, "ENRICHMENT_LICENSE_BLOCKED"),
        ("rights", "credential_version_id", None, "ENRICHMENT_LICENSE_BLOCKED"),
        ("rights", "no_store_policy_version", 1, "ENRICHMENT_LICENSE_BLOCKED"),
        ("rights", "attribution_policy_version", 1, "ENRICHMENT_LICENSE_BLOCKED"),
        ("credential", "tenant_id", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("credential", "provider_id", 7, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("credential", "encrypted", 1, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("credential", "active", "true", "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("credential", "ciphertext_sha256", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("credential", "bundle_version_hash", 7, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("budget", "tenant_id", None, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("budget", "deal_id", None, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("budget", "run_id", 7, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("budget", "provider_id", None, "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED"),
        ("budget", "policy_hash", 7, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("budget", "signed_policy_id", None, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("budget", "active", 1, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("budget", "signature_verified", "true", "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("cache", "tenant_id", None, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "deal_id", None, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "run_id", None, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "provider_id", 7, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "identity_id", None, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "identity_version", 7, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "identity_hash", None, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "required_identifiers", None, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "identifiers_used_sha256", 7, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "credential_version_id", None, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "catalog_version", 7, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "decision_id", None, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "disposition", "MISS", "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "rights_version_hash", None, "ENRICHMENT_RIGHTS_BLOCKED"),
        ("cache", "rights_compatible", 1, "ENRICHMENT_PROVENANCE_MISSING"),
        ("cache", "cached_outcome_id", object(), "ENRICHMENT_PROVENANCE_MISSING"),
    ],
)
def test_each_snapshot_field_rejects_none_or_wrong_type_with_stable_code(
    component: str,
    field_name: str,
    bad_value: object,
    expected_code: str,
) -> None:
    identity = _identity()
    applicability = _applicability(identity)
    rights = _rights()
    kwargs: dict[str, object] = {"identity": identity, "applicability": applicability}
    if component == "identity":
        kwargs["identity"] = _unsafe_replace(identity, {field_name: bad_value})
    elif component == "applicability":
        kwargs["applicability"] = _unsafe_replace(applicability, {field_name: bad_value})
    elif component == "rights":
        kwargs["rights"] = _unsafe_replace(rights, {field_name: bad_value})
    elif component == "credential":
        kwargs["credential"] = _unsafe_replace(_credential(), {field_name: bad_value})
    elif component == "budget":
        kwargs["budget"] = _unsafe_replace(_budget(), {field_name: bad_value})
    else:
        kwargs["cache"] = _unsafe_replace(
            _cache(rights, identity=identity), {field_name: bad_value}
        )
    stack = _CollaboratorStack(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())

    assert exc_info.value.code == expected_code
    assert "NoneType" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("component", "bad_value", "expected_code"),
    [
        ("identity", None, "COMPANY_IDENTITY_UNVERIFIED"),
        ("applicability", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("rights", None, "ENRICHMENT_LICENSE_BLOCKED"),
        ("credential", None, "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("budget", None, "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("cache", object(), "ENRICHMENT_PROVENANCE_MISSING"),
    ],
)
def test_whole_malformed_collaborator_snapshots_fail_stably(
    component: str,
    bad_value: object,
    expected_code: str,
) -> None:
    identity = _identity()
    stack = _CollaboratorStack(identity=identity, applicability=_applicability(identity))
    setattr(stack, component, bad_value)

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())

    assert exc_info.value.code == expected_code
    assert "NoneType" not in str(exc_info.value)


def test_identity_lineage_hashes_are_recomputed_and_duplicate_ids_are_rejected() -> None:
    valid_identity = _identity()
    identity = replace(valid_identity, identity_hash=_HASH)
    decision = _applicability(identity)

    with pytest.raises(ProviderAdmissionError) as arbitrary_hash_error:
        admit_provider(
            _request(),
            _CollaboratorStack(identity=identity, applicability=decision).collaborators(),
        )

    assert arbitrary_hash_error.value.code == "COMPANY_IDENTITY_UNVERIFIED"

    duplicate_ids = replace(identity, evidence_ids=("evidence-001", "evidence-001"))
    duplicate_decision = _applicability(duplicate_ids)
    with pytest.raises(ProviderAdmissionError) as duplicate_error:
        admit_provider(
            _request(),
            _CollaboratorStack(
                identity=duplicate_ids,
                applicability=duplicate_decision,
            ).collaborators(),
        )

    assert duplicate_error.value.code == "COMPANY_IDENTITY_UNVERIFIED"
    assert hasattr(provider_admission, "canonical_identity_evidence_set_sha256")
    assert hasattr(provider_admission, "canonical_identity_provenance_set_sha256")
    assert hasattr(provider_admission, "canonical_verified_identity_hash")


def test_private_not_applicable_rejects_a_contradictory_listed_ticker() -> None:
    identity = _identity(
        listing_state=ListingState.PRIVATE,
        identifiers=(("listed_ticker", "AAPL"),),
    )
    decision = _applicability(
        identity,
        status=ApplicabilityStatus.NOT_APPLICABLE,
    )

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(
            _request(),
            _CollaboratorStack(identity=identity, applicability=decision).collaborators(),
        )

    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert exc_info.value.reason == "not_applicable_unproven"


@pytest.mark.parametrize(
    ("stage", "expected_code"),
    [
        ("identity", "COMPANY_IDENTITY_UNVERIFIED"),
        ("applicability", "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("rights", "ENRICHMENT_LICENSE_BLOCKED"),
        ("credential", "ENRICHMENT_PROVIDER_INPUT_MISSING"),
        ("budget", "PROVIDER_BUDGET_CONFIG_BLOCKED"),
        ("cache", "ENRICHMENT_PROVENANCE_MISSING"),
    ],
)
def test_collaborator_exceptions_map_to_constant_stage_errors(
    stage: str,
    expected_code: str,
) -> None:
    identity = _identity()
    stack = _CollaboratorStack(identity=identity, applicability=_applicability(identity))

    def fail(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("sensitive collaborator detail")

    method_by_stage = {
        "identity": "resolve_identity",
        "applicability": "decide_applicability",
        "rights": "resolve_rights",
        "credential": "resolve_credentials",
        "budget": "reserve_budget",
        "cache": "decide_cache",
    }
    setattr(stack, method_by_stage[stage], fail)

    with pytest.raises(ProviderAdmissionError) as exc_info:
        admit_provider(_request(), stack.collaborators())

    assert exc_info.value.code == expected_code
    assert "sensitive" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_budget_and_cache_snapshots_bind_deal_and_run_scope() -> None:
    assert {item.name for item in fields(BudgetReservationSnapshot)} >= {
        "tenant_id",
        "deal_id",
        "run_id",
    }
    assert {item.name for item in fields(CacheDecisionSnapshot)} >= {
        "tenant_id",
        "deal_id",
        "run_id",
    }
