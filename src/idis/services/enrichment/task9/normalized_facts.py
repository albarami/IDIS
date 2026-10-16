"""Pure, deterministic persistence intents for Task 9 provider outcomes and facts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid5

from idis.models.sanad import SanadGrade
from idis.services.enrichment.models import RightsClass
from idis.services.enrichment.task9.perplexity_evidence import (
    PerplexityCitationEvidence,
    PerplexityEvidenceError,
    SupportedSynthesisUnit,
    canonicalize_citation_url,
)
from idis.services.enrichment.task9.provider_admission import (
    ApplicabilityDecision,
    ApplicabilityStatus,
    CacheDisposition,
    ListingState,
    ProviderCacheHitIntent,
    ProviderCallIntent,
    ProviderNotApplicableIntent,
    admission_proof_matches,
    canonical_applicability_decision_content_sha256,
    request_binding_matches,
)
from idis.services.enrichment.task9.provider_catalog import (
    PROVIDER_CATALOG,
    PROVIDER_CATALOG_VERSION,
    FactDomain,
    ProviderCatalogRoutingError,
    deliverable_consumers_for,
)
from idis.services.sanad.source_tiers import SourceTier, TierUsage

_OUTCOME_NAMESPACE = UUID("88689da7-3e18-58a0-87f0-2269142ee0ce")
_FACT_NAMESPACE = UUID("ec1987ec-d2df-5ae2-8446-74fb8e629043")
_RAG_NAMESPACE = UUID("9bab96aa-e820-5537-8860-453477b358d4")
_EDGE_NAMESPACE = UUID("099f7fb2-7afb-5d4d-aa5e-cb564bf0ea31")
_SYNTHESIS_NAMESPACE = UUID("912dbb45-11b4-50ee-aefc-bcc618c7a801")
_BUDGET_DISPOSITION_NAMESPACE = UUID("18053165-343f-5b75-b113-5c01d55c6e77")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_OPAQUE_SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")


class ProviderExecutionStatus(StrEnum):
    HIT = "HIT"
    MISS = "MISS"
    ERROR = "ERROR"
    PARTIAL = "PARTIAL"


class ProviderOutcomeKind(StrEnum):
    HIT = "HIT"
    MISS = "MISS"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class BudgetReservationDisposition(StrEnum):
    REFUNDED = "REFUNDED"


class BudgetReservationState(StrEnum):
    RELEASED = "RELEASED"


class NormalizedFactsError(RuntimeError):
    """Stable, payload-free rejection of an invalid persistence intent."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}:{reason}")


@dataclass(frozen=True, slots=True)
class ProviderFactInput:
    subject: str
    predicate: str
    value: object
    fact_domain: FactDomain | str
    safe_source_ref: str
    citation_evidence: object | None = None


@dataclass(frozen=True, slots=True)
class ProviderExecutionOperationEvidence:
    """Ephemeral byte evidence used to derive request/response digests before persistence."""

    operation_id: str
    safe_request_content: bytes
    request_content_sha256: str
    response_body: bytes
    response_sha256: str


@dataclass(frozen=True, slots=True)
class ProviderExecutionResult:
    provider_id: str
    admission_proof_sha256: str
    request_binding_sha256: str
    identifiers_used: tuple[tuple[str, str], ...]
    identifiers_used_sha256: str
    request_content_sha256: str
    connector_identity: str
    connector_version_hash: str
    request_builder_identity: str
    status: ProviderExecutionStatus
    provider_request_id: str
    retrieved_at: datetime
    response_sha256: str
    request_builder_version_hash: str
    request_succeeded: bool
    schema_valid: bool
    facts: tuple[ProviderFactInput, ...]
    operation_evidence: tuple[ProviderExecutionOperationEvidence, ...]
    supported_synthesis: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderFactProvenanceIntent:
    provider_id: str
    provider_request_id: str
    retrieved_at: datetime
    identifiers_used: tuple[tuple[str, str], ...]
    identifiers_used_sha256: str
    response_sha256: str
    safe_source_ref: str
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
    cache_disposition: CacheDisposition
    request_builder_version_hash: str
    request_binding_sha256: str
    request_content_sha256: str
    connector_identity: str
    connector_version_hash: str
    request_builder_identity: str
    admission_proof_sha256: str
    identity_id: str
    identity_version: str
    identity_rule_version: str
    identity_listing_state: ListingState
    identity_evidence_ids: tuple[str, ...]
    identity_evidence_set_sha256: str
    identity_provenance_ids: tuple[str, ...]
    identity_provenance_set_sha256: str
    identity_hash: str
    applicability_decision_hash: str
    applicability_decision_id: str
    applicability_decision_content_sha256: str
    applicability_rule_version: str
    applicability_rule_sha256: str
    applicability_reason_code: str
    catalog_version: str
    cached_outcome_id: str | None = None
    cached_outcome_sha256: str | None = None
    cached_fact_id: str | None = None
    cached_fact_content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedFactPersistenceIntent:
    fact_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    outcome_id: str
    subject: str
    predicate: str
    canonical_value_json: str
    value_sha256: str
    fact_content_sha256: str
    fact_domain: FactDomain
    provenance: ProviderFactProvenanceIntent
    citation_id: str | None
    source_tier: SourceTier | None
    sanad_grade: SanadGrade | None
    tier_usage: TierUsage | None
    primary_eligible: bool | None


@dataclass(frozen=True, slots=True)
class ExternalContextRagIntent:
    rag_intent_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    fact_id: str
    namespace: str
    content_sha256: str
    safe_source_ref: str


@dataclass(frozen=True, slots=True)
class ExpectedConsumerEdgeIntent:
    edge_intent_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    fact_id: str
    fact_domain: FactDomain
    consumer_id: str


@dataclass(frozen=True, slots=True)
class SynthesisPersistenceIntent:
    synthesis_intent_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    outcome_id: str
    text: str
    text_sha256: str
    citation_ids: tuple[str, ...]
    citation_fact_ids: tuple[str, ...]
    source_tier: SourceTier
    sanad_grade: SanadGrade
    tier_usage: TierUsage
    primary_eligible: bool


@dataclass(frozen=True, slots=True)
class ProviderOutcomePersistenceIntent:
    outcome_id: str
    outcome_sha256: str
    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    outcome_kind: ProviderOutcomeKind
    identity_id: str
    identity_version: str
    identity_rule_version: str
    identity_listing_state: ListingState
    identity_evidence_ids: tuple[str, ...]
    identity_evidence_set_sha256: str
    identity_provenance_ids: tuple[str, ...]
    identity_provenance_set_sha256: str
    identity_hash: str
    listing_state: ListingState | None
    applicability_decision_id: str
    applicability_decision_hash: str
    applicability_decision_content_sha256: str
    applicability_rule_version: str
    applicability_rule_sha256: str
    applicability_reason_code: str
    identifiers_used: tuple[tuple[str, str], ...] | None
    identifiers_used_sha256: str | None
    provider_request_id: str | None
    retrieved_at: datetime | None
    response_sha256: str | None
    rights_approval_id: str | None
    rights_version_hash: str | None
    rights_class: RightsClass | None
    no_store_policy_version: str | None
    attribution_policy_version: str | None
    external_context_index_allowed: bool
    credential_version_id: str | None
    credential_bundle_hash: str | None
    budget_reservation_id: str | None
    budget_policy_hash: str | None
    cache_decision_id: str | None
    cache_policy_hash: str | None
    cache_disposition: CacheDisposition | None
    request_builder_version_hash: str | None
    request_binding_sha256: str | None
    request_content_sha256: str | None
    connector_identity: str | None
    connector_version_hash: str | None
    request_builder_identity: str | None
    admission_proof_sha256: str
    catalog_version: str
    cached_outcome_id: str | None
    cached_outcome_sha256: str | None
    budget_reservation_disposition_id: str | None
    fact_ids: tuple[str, ...]
    rag_intent_ids: tuple[str, ...]
    consumer_edge_ids: tuple[str, ...]
    synthesis_intent_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CachedProviderOutcomePayload:
    """Typed cached terminal payload consumed by current-run materialization."""

    outcome: ProviderOutcomePersistenceIntent
    facts: tuple[NormalizedFactPersistenceIntent, ...]
    synthesis_intents: tuple[SynthesisPersistenceIntent, ...]


@dataclass(frozen=True, slots=True)
class BudgetReservationDispositionIntent:
    disposition_intent_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    provider_id: str
    reservation_id: str
    budget_policy_hash: str
    cache_decision_id: str
    cached_outcome_id: str
    disposition: BudgetReservationDisposition
    reservation_state: BudgetReservationState


@dataclass(frozen=True, slots=True)
class ProviderIntentBundle:
    outcome: ProviderOutcomePersistenceIntent
    facts: tuple[NormalizedFactPersistenceIntent, ...]
    rag_intents: tuple[ExternalContextRagIntent, ...]
    consumer_edges: tuple[ExpectedConsumerEdgeIntent, ...]
    synthesis_intents: tuple[SynthesisPersistenceIntent, ...]
    budget_reservation_disposition: BudgetReservationDispositionIntent | None = None


def materialize_provider_intents(
    admission: ProviderCallIntent | ProviderNotApplicableIntent | ProviderCacheHitIntent,
    execution: ProviderExecutionResult | CachedProviderOutcomePayload | None,
) -> ProviderIntentBundle:
    """Validate one terminal provider result and create immutable write intents only."""

    if isinstance(admission, ProviderNotApplicableIntent):
        return _materialize_not_applicable(admission, execution)
    if isinstance(admission, ProviderCacheHitIntent):
        return _materialize_cache_hit(admission, execution)
    if not isinstance(admission, ProviderCallIntent) or not isinstance(
        execution, ProviderExecutionResult
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVIDER_FAILED", "execution_missing")
    _validate_call_intent(admission)
    _validate_execution(admission, execution)
    if execution.status not in {ProviderExecutionStatus.HIT, ProviderExecutionStatus.MISS}:
        raise NormalizedFactsError("ENRICHMENT_PROVIDER_FAILED", "execution_not_terminal_success")
    if not execution.request_succeeded or not execution.schema_valid:
        raise NormalizedFactsError("ENRICHMENT_PROVIDER_FAILED", "request_or_schema_failed")
    if execution.status is ProviderExecutionStatus.HIT and not execution.facts:
        raise NormalizedFactsError("ENRICHMENT_PROVIDER_FAILED", "hit_has_no_facts")
    if execution.status is ProviderExecutionStatus.MISS and (
        execution.facts or execution.supported_synthesis
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVIDER_FAILED", "miss_has_outputs")

    outcome_kind = ProviderOutcomeKind(execution.status.value)
    identifiers_sha256 = admission.identifiers_used_sha256
    outcome_identity = {
        "tenant_id": admission.tenant_id,
        "deal_id": admission.deal_id,
        "run_id": admission.run_id,
        "provider_id": admission.provider_id,
        "outcome_kind": outcome_kind.value,
        "provider_request_id": execution.provider_request_id,
        "identity_id": admission.identity_id,
        "identity_version": admission.identity_version,
        "identity_rule_version": admission.identity_rule_version,
        "identity_listing_state": admission.identity_listing_state.value,
        "identity_evidence_ids": sorted(admission.identity_evidence_ids),
        "identity_evidence_set_sha256": admission.identity_evidence_set_sha256,
        "identity_provenance_ids": sorted(admission.identity_provenance_ids),
        "identity_provenance_set_sha256": admission.identity_provenance_set_sha256,
        "identity_hash": admission.identity_hash,
        "listing_state": None,
        "applicability_decision_id": admission.applicability_decision_id,
        "applicability_decision_hash": admission.applicability_decision_hash,
        "applicability_decision_content_sha256": (admission.applicability_decision_content_sha256),
        "applicability_rule_version": admission.applicability_rule_version,
        "applicability_rule_sha256": admission.applicability_rule_sha256,
        "applicability_reason_code": admission.applicability_reason_code,
        "identifiers_used": sorted(admission.required_identifiers),
        "identifiers_used_sha256": identifiers_sha256,
        "response_sha256": execution.response_sha256,
        "retrieved_at": _canonical_timestamp(execution.retrieved_at),
        "request_builder_version_hash": execution.request_builder_version_hash,
        "request_binding_sha256": execution.request_binding_sha256,
        "request_content_sha256": execution.request_content_sha256,
        "connector_identity": execution.connector_identity,
        "connector_version_hash": execution.connector_version_hash,
        "request_builder_identity": execution.request_builder_identity,
        "rights_approval_id": admission.rights_approval_id,
        "rights_version_hash": admission.rights_version_hash,
        "rights_class": admission.rights_class.value,
        "no_store_policy_version": admission.no_store_policy_version,
        "attribution_policy_version": admission.attribution_policy_version,
        "credential_version_id": admission.credential_version_id,
        "credential_bundle_hash": admission.credential_bundle_hash,
        "budget_reservation_id": admission.budget_reservation_id,
        "budget_policy_hash": admission.budget_policy_hash,
        "cache_decision_id": admission.cache_decision_id,
        "cache_policy_hash": admission.cache_policy_hash,
        "cache_disposition": admission.cache_disposition.value,
        "catalog_version": admission.catalog_version,
        "external_context_index_allowed": admission.external_context_index_allowed,
        "admission_proof_sha256": admission.admission_proof_sha256,
        "cached_outcome_id": None,
        "cached_outcome_sha256": None,
    }
    outcome_id = str(uuid5(_OUTCOME_NAMESPACE, _canonical_json(outcome_identity)))
    facts = _materialize_facts(
        admission,
        execution,
        outcome_id=outcome_id,
        identifiers_sha256=identifiers_sha256,
    )
    if outcome_kind is ProviderOutcomeKind.HIT and not facts:
        raise NormalizedFactsError("ENRICHMENT_PROVIDER_FAILED", "hit_has_no_valid_facts")

    rag_intents = _materialize_rag_intents(admission, facts)
    consumer_edges = _materialize_consumer_edges(admission, facts)
    synthesis_intents = _materialize_synthesis(
        admission,
        execution,
        outcome_id=outcome_id,
        facts=facts,
    )
    fact_ids = tuple(fact.fact_id for fact in facts)
    rag_intent_ids = tuple(intent.rag_intent_id for intent in rag_intents)
    consumer_edge_ids = tuple(intent.edge_intent_id for intent in consumer_edges)
    synthesis_intent_ids = tuple(intent.synthesis_intent_id for intent in synthesis_intents)
    outcome_sha256 = _sha256_json(
        {
            **outcome_identity,
            "fact_ids": fact_ids,
            "rag_intent_ids": rag_intent_ids,
            "consumer_edge_ids": consumer_edge_ids,
            "synthesis_intent_ids": synthesis_intent_ids,
            "budget_reservation_disposition_id": None,
        }
    )
    outcome = ProviderOutcomePersistenceIntent(
        outcome_id=outcome_id,
        outcome_sha256=outcome_sha256,
        tenant_id=admission.tenant_id,
        deal_id=admission.deal_id,
        run_id=admission.run_id,
        provider_id=admission.provider_id,
        outcome_kind=outcome_kind,
        identity_id=admission.identity_id,
        identity_version=admission.identity_version,
        identity_rule_version=admission.identity_rule_version,
        identity_listing_state=admission.identity_listing_state,
        identity_evidence_ids=admission.identity_evidence_ids,
        identity_evidence_set_sha256=admission.identity_evidence_set_sha256,
        identity_provenance_ids=admission.identity_provenance_ids,
        identity_provenance_set_sha256=admission.identity_provenance_set_sha256,
        identity_hash=admission.identity_hash,
        listing_state=None,
        applicability_decision_id=admission.applicability_decision_id,
        applicability_decision_hash=admission.applicability_decision_hash,
        applicability_decision_content_sha256=(admission.applicability_decision_content_sha256),
        applicability_rule_version=admission.applicability_rule_version,
        applicability_rule_sha256=admission.applicability_rule_sha256,
        applicability_reason_code=admission.applicability_reason_code,
        identifiers_used=admission.required_identifiers,
        identifiers_used_sha256=identifiers_sha256,
        provider_request_id=execution.provider_request_id,
        retrieved_at=execution.retrieved_at.astimezone(UTC),
        response_sha256=execution.response_sha256,
        rights_approval_id=admission.rights_approval_id,
        rights_version_hash=admission.rights_version_hash,
        rights_class=admission.rights_class,
        no_store_policy_version=admission.no_store_policy_version,
        attribution_policy_version=admission.attribution_policy_version,
        external_context_index_allowed=admission.external_context_index_allowed,
        credential_version_id=admission.credential_version_id,
        credential_bundle_hash=admission.credential_bundle_hash,
        budget_reservation_id=admission.budget_reservation_id,
        budget_policy_hash=admission.budget_policy_hash,
        cache_decision_id=admission.cache_decision_id,
        cache_policy_hash=admission.cache_policy_hash,
        cache_disposition=admission.cache_disposition,
        request_builder_version_hash=execution.request_builder_version_hash,
        request_binding_sha256=execution.request_binding_sha256,
        request_content_sha256=execution.request_content_sha256,
        connector_identity=execution.connector_identity,
        connector_version_hash=execution.connector_version_hash,
        request_builder_identity=execution.request_builder_identity,
        admission_proof_sha256=admission.admission_proof_sha256,
        catalog_version=admission.catalog_version,
        cached_outcome_id=None,
        cached_outcome_sha256=None,
        budget_reservation_disposition_id=None,
        fact_ids=fact_ids,
        rag_intent_ids=rag_intent_ids,
        consumer_edge_ids=consumer_edge_ids,
        synthesis_intent_ids=synthesis_intent_ids,
    )
    return ProviderIntentBundle(
        outcome=outcome,
        facts=facts,
        rag_intents=rag_intents,
        consumer_edges=consumer_edges,
        synthesis_intents=synthesis_intents,
    )


def _materialize_cache_hit(
    admission: ProviderCacheHitIntent,
    payload: ProviderExecutionResult | CachedProviderOutcomePayload | None,
) -> ProviderIntentBundle:
    _validate_cache_hit_intent(admission)
    if not isinstance(payload, CachedProviderOutcomePayload):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cache_payload_missing")
    _validate_cached_payload(admission, payload)
    source = payload.outcome
    if source.retrieved_at is None:
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_outcome_invalid")
    disposition_id = str(
        uuid5(
            _BUDGET_DISPOSITION_NAMESPACE,
            _canonical_json(
                {
                    "tenant_id": admission.tenant_id,
                    "deal_id": admission.deal_id,
                    "run_id": admission.run_id,
                    "provider_id": admission.provider_id,
                    "reservation_id": admission.budget_reservation_id,
                    "budget_policy_hash": admission.budget_policy_hash,
                    "cache_decision_id": admission.cache_decision_id,
                    "cached_outcome_id": admission.cached_outcome_id,
                    "disposition": BudgetReservationDisposition.REFUNDED.value,
                    "reservation_state": BudgetReservationState.RELEASED.value,
                }
            ),
        )
    )
    disposition = BudgetReservationDispositionIntent(
        disposition_intent_id=disposition_id,
        tenant_id=admission.tenant_id,
        deal_id=admission.deal_id,
        run_id=admission.run_id,
        provider_id=admission.provider_id,
        reservation_id=admission.budget_reservation_id,
        budget_policy_hash=admission.budget_policy_hash,
        cache_decision_id=admission.cache_decision_id,
        cached_outcome_id=admission.cached_outcome_id,
        disposition=BudgetReservationDisposition.REFUNDED,
        reservation_state=BudgetReservationState.RELEASED,
    )
    outcome_identity = {
        "tenant_id": admission.tenant_id,
        "deal_id": admission.deal_id,
        "run_id": admission.run_id,
        "provider_id": admission.provider_id,
        "outcome_kind": ProviderOutcomeKind.HIT.value,
        "provider_request_id": source.provider_request_id,
        "identity_id": admission.identity_id,
        "identity_version": admission.identity_version,
        "identity_rule_version": admission.identity_rule_version,
        "identity_listing_state": admission.identity_listing_state.value,
        "identity_evidence_ids": sorted(admission.identity_evidence_ids),
        "identity_evidence_set_sha256": admission.identity_evidence_set_sha256,
        "identity_provenance_ids": sorted(admission.identity_provenance_ids),
        "identity_provenance_set_sha256": admission.identity_provenance_set_sha256,
        "identity_hash": admission.identity_hash,
        "listing_state": None,
        "applicability_decision_id": admission.applicability_decision_id,
        "applicability_decision_hash": admission.applicability_decision_hash,
        "applicability_decision_content_sha256": (admission.applicability_decision_content_sha256),
        "applicability_rule_version": admission.applicability_rule_version,
        "applicability_rule_sha256": admission.applicability_rule_sha256,
        "applicability_reason_code": admission.applicability_reason_code,
        "identifiers_used": sorted(admission.required_identifiers),
        "identifiers_used_sha256": admission.identifiers_used_sha256,
        "response_sha256": source.response_sha256,
        "retrieved_at": _canonical_timestamp(source.retrieved_at),
        "request_builder_version_hash": source.request_builder_version_hash,
        "request_binding_sha256": admission.request_binding_sha256,
        "request_content_sha256": source.request_content_sha256,
        "connector_identity": source.connector_identity,
        "connector_version_hash": source.connector_version_hash,
        "request_builder_identity": source.request_builder_identity,
        "rights_approval_id": admission.rights_approval_id,
        "rights_version_hash": admission.rights_version_hash,
        "rights_class": admission.rights_class.value,
        "no_store_policy_version": admission.no_store_policy_version,
        "attribution_policy_version": admission.attribution_policy_version,
        "credential_version_id": admission.credential_version_id,
        "credential_bundle_hash": admission.credential_bundle_hash,
        "budget_reservation_id": admission.budget_reservation_id,
        "budget_policy_hash": admission.budget_policy_hash,
        "cache_decision_id": admission.cache_decision_id,
        "cache_policy_hash": admission.cache_policy_hash,
        "cache_disposition": CacheDisposition.HIT.value,
        "catalog_version": admission.catalog_version,
        "external_context_index_allowed": admission.external_context_index_allowed,
        "admission_proof_sha256": admission.admission_proof_sha256,
        "cached_outcome_id": admission.cached_outcome_id,
        "cached_outcome_sha256": admission.cached_outcome_sha256,
    }
    outcome_id = str(uuid5(_OUTCOME_NAMESPACE, _canonical_json(outcome_identity)))
    facts = _materialize_cached_facts(admission, payload, outcome_id=outcome_id)
    rag_intents = _materialize_rag_intents(admission, facts)
    consumer_edges = _materialize_consumer_edges(admission, facts)
    synthesis_intents = _materialize_cached_synthesis(
        admission,
        payload,
        outcome_id=outcome_id,
        facts=facts,
    )
    fact_ids = tuple(fact.fact_id for fact in facts)
    rag_ids = tuple(item.rag_intent_id for item in rag_intents)
    edge_ids = tuple(item.edge_intent_id for item in consumer_edges)
    synthesis_ids = tuple(item.synthesis_intent_id for item in synthesis_intents)
    outcome_sha256 = _sha256_json(
        {
            **outcome_identity,
            "fact_ids": fact_ids,
            "rag_intent_ids": rag_ids,
            "consumer_edge_ids": edge_ids,
            "synthesis_intent_ids": synthesis_ids,
            "budget_reservation_disposition_id": disposition_id,
        }
    )
    outcome = ProviderOutcomePersistenceIntent(
        outcome_id=outcome_id,
        outcome_sha256=outcome_sha256,
        tenant_id=admission.tenant_id,
        deal_id=admission.deal_id,
        run_id=admission.run_id,
        provider_id=admission.provider_id,
        outcome_kind=ProviderOutcomeKind.HIT,
        identity_id=admission.identity_id,
        identity_version=admission.identity_version,
        identity_rule_version=admission.identity_rule_version,
        identity_listing_state=admission.identity_listing_state,
        identity_evidence_ids=admission.identity_evidence_ids,
        identity_evidence_set_sha256=admission.identity_evidence_set_sha256,
        identity_provenance_ids=admission.identity_provenance_ids,
        identity_provenance_set_sha256=admission.identity_provenance_set_sha256,
        identity_hash=admission.identity_hash,
        listing_state=None,
        applicability_decision_id=admission.applicability_decision_id,
        applicability_decision_hash=admission.applicability_decision_hash,
        applicability_decision_content_sha256=(admission.applicability_decision_content_sha256),
        applicability_rule_version=admission.applicability_rule_version,
        applicability_rule_sha256=admission.applicability_rule_sha256,
        applicability_reason_code=admission.applicability_reason_code,
        identifiers_used=admission.required_identifiers,
        identifiers_used_sha256=admission.identifiers_used_sha256,
        provider_request_id=source.provider_request_id,
        retrieved_at=source.retrieved_at,
        response_sha256=source.response_sha256,
        rights_approval_id=admission.rights_approval_id,
        rights_version_hash=admission.rights_version_hash,
        rights_class=admission.rights_class,
        no_store_policy_version=admission.no_store_policy_version,
        attribution_policy_version=admission.attribution_policy_version,
        external_context_index_allowed=admission.external_context_index_allowed,
        credential_version_id=admission.credential_version_id,
        credential_bundle_hash=admission.credential_bundle_hash,
        budget_reservation_id=admission.budget_reservation_id,
        budget_policy_hash=admission.budget_policy_hash,
        cache_decision_id=admission.cache_decision_id,
        cache_policy_hash=admission.cache_policy_hash,
        cache_disposition=CacheDisposition.HIT,
        request_builder_version_hash=source.request_builder_version_hash,
        request_binding_sha256=admission.request_binding_sha256,
        request_content_sha256=source.request_content_sha256,
        connector_identity=source.connector_identity,
        connector_version_hash=source.connector_version_hash,
        request_builder_identity=source.request_builder_identity,
        admission_proof_sha256=admission.admission_proof_sha256,
        catalog_version=admission.catalog_version,
        cached_outcome_id=admission.cached_outcome_id,
        cached_outcome_sha256=admission.cached_outcome_sha256,
        budget_reservation_disposition_id=disposition_id,
        fact_ids=fact_ids,
        rag_intent_ids=rag_ids,
        consumer_edge_ids=edge_ids,
        synthesis_intent_ids=synthesis_ids,
    )
    return ProviderIntentBundle(
        outcome=outcome,
        facts=facts,
        rag_intents=rag_intents,
        consumer_edges=consumer_edges,
        synthesis_intents=synthesis_intents,
        budget_reservation_disposition=disposition,
    )


def _validate_cache_hit_intent(admission: ProviderCacheHitIntent) -> None:
    if (
        not isinstance(admission, ProviderCacheHitIntent)
        or admission.provider_id not in PROVIDER_CATALOG
        or admission.catalog_version != PROVIDER_CATALOG_VERSION
        or not admission.no_provider_call
        or not admission_proof_matches(admission)
        or not request_binding_matches(admission)
        or admission.rights_class is not PROVIDER_CATALOG[admission.provider_id].rights_class
        or admission.identifiers_used_sha256 != _sha256_json(sorted(admission.required_identifiers))
        or not _is_sha256(admission.cached_outcome_sha256)
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cache_admission_invalid")
    try:
        UUID(admission.cached_outcome_id)
        UUID(admission.budget_reservation_id)
    except (TypeError, ValueError, AttributeError):
        raise NormalizedFactsError(
            "ENRICHMENT_PROVENANCE_MISSING", "cache_admission_invalid"
        ) from None


def _validate_cached_payload(
    admission: ProviderCacheHitIntent,
    payload: CachedProviderOutcomePayload,
) -> None:
    source = payload.outcome
    if (
        type(source) is not ProviderOutcomePersistenceIntent
        or type(payload.synthesis_intents) is not tuple
        or type(source.synthesis_intent_ids) is not tuple
        or any(
            type(synthesis) is not SynthesisPersistenceIntent
            for synthesis in payload.synthesis_intents
        )
        or any(
            type(synthesis.synthesis_intent_id) is not str
            for synthesis in payload.synthesis_intents
        )
        or any(type(synthesis_id) is not str for synthesis_id in source.synthesis_intent_ids)
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_outcome_invalid")
    try:
        source_identity = _persisted_outcome_identity(source)
        synthesis_ids = tuple(
            synthesis.synthesis_intent_id for synthesis in payload.synthesis_intents
        )
    except (AttributeError, TypeError, ValueError):
        raise NormalizedFactsError(
            "ENRICHMENT_PROVENANCE_MISSING", "cached_outcome_invalid"
        ) from None
    if (
        not isinstance(payload.facts, tuple)
        or not payload.facts
        or synthesis_ids != source.synthesis_intent_ids
        or synthesis_ids != tuple(sorted(set(synthesis_ids)))
        or (source.provider_id != "perplexity" and bool(synthesis_ids))
        or source.outcome_kind is not ProviderOutcomeKind.HIT
        or source.outcome_id != admission.cached_outcome_id
        or source.outcome_sha256 != admission.cached_outcome_sha256
        or source.tenant_id != admission.tenant_id
        or source.deal_id != admission.deal_id
        or source.provider_id != admission.provider_id
        or source.identity_id != admission.identity_id
        or source.identity_version != admission.identity_version
        or source.identity_rule_version != admission.identity_rule_version
        or source.identity_listing_state is not admission.identity_listing_state
        or source.identity_evidence_ids != admission.identity_evidence_ids
        or source.identity_evidence_set_sha256 != admission.identity_evidence_set_sha256
        or source.identity_hash != admission.identity_hash
        or source.identity_provenance_ids != admission.identity_provenance_ids
        or source.identity_provenance_set_sha256 != admission.identity_provenance_set_sha256
        or source.applicability_decision_id != admission.applicability_decision_id
        or source.applicability_decision_content_sha256
        != admission.applicability_decision_content_sha256
        or source.identifiers_used != admission.required_identifiers
        or source.identifiers_used_sha256 != admission.identifiers_used_sha256
        or source.request_binding_sha256 != admission.request_binding_sha256
        or source.rights_approval_id != admission.rights_approval_id
        or source.rights_version_hash != admission.rights_version_hash
        or source.rights_class is not admission.rights_class
        or source.no_store_policy_version != admission.no_store_policy_version
        or source.attribution_policy_version != admission.attribution_policy_version
        or source.external_context_index_allowed is not admission.external_context_index_allowed
        or bool(source.rag_intent_ids) is not source.external_context_index_allowed
        or source.credential_version_id != admission.credential_version_id
        or source.credential_bundle_hash != admission.credential_bundle_hash
        or source.catalog_version != admission.catalog_version
        or tuple(fact.fact_id for fact in payload.facts) != source.fact_ids
        or source.outcome_id != str(uuid5(_OUTCOME_NAMESPACE, _canonical_json(source_identity)))
        or source.outcome_sha256
        != _sha256_json(
            {
                **source_identity,
                "fact_ids": source.fact_ids,
                "rag_intent_ids": source.rag_intent_ids,
                "consumer_edge_ids": source.consumer_edge_ids,
                "synthesis_intent_ids": source.synthesis_intent_ids,
                "budget_reservation_disposition_id": (source.budget_reservation_disposition_id),
            }
        )
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_outcome_invalid")
    for fact in payload.facts:
        _validate_cached_fact(source, fact)
    for synthesis in payload.synthesis_intents:
        _validate_cached_synthesis(source, synthesis, payload.facts)


def _validate_cached_fact(
    outcome: ProviderOutcomePersistenceIntent,
    fact: NormalizedFactPersistenceIntent,
) -> None:
    if not isinstance(fact, NormalizedFactPersistenceIntent):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_fact_invalid")
    try:
        canonical_value = _canonical_fact_value(json.loads(fact.canonical_value_json))
        _validate_persisted_fact_evidence(fact)
        content_sha256 = _fact_content_sha256(
            subject=fact.subject,
            predicate=fact.predicate,
            canonical_value_json=fact.canonical_value_json,
            safe_source_ref=fact.provenance.safe_source_ref,
            citation_id=fact.citation_id,
            source_tier=fact.source_tier,
            sanad_grade=fact.sanad_grade,
            tier_usage=fact.tier_usage,
            primary_eligible=fact.primary_eligible,
        )
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_fact_invalid") from None
    fact_identity = {
        "outcome_id": outcome.outcome_id,
        "tenant_id": outcome.tenant_id,
        "deal_id": outcome.deal_id,
        "run_id": outcome.run_id,
        "provider_id": outcome.provider_id,
        "subject": fact.subject,
        "predicate": fact.predicate,
        "value_sha256": fact.value_sha256,
        "fact_domain": fact.fact_domain.value,
        "safe_source_ref": fact.provenance.safe_source_ref,
        "citation_id": fact.citation_id,
        "fact_content_sha256": content_sha256,
    }
    provenance = fact.provenance
    if (
        canonical_value != fact.canonical_value_json
        or _sha256_text(fact.canonical_value_json) != fact.value_sha256
        or content_sha256 != fact.fact_content_sha256
        or fact.fact_id != str(uuid5(_FACT_NAMESPACE, _canonical_json(fact_identity)))
        or fact.outcome_id != outcome.outcome_id
        or fact.tenant_id != outcome.tenant_id
        or fact.deal_id != outcome.deal_id
        or fact.run_id != outcome.run_id
        or fact.provider_id != outcome.provider_id
        or provenance.provider_id != outcome.provider_id
        or provenance.provider_request_id != outcome.provider_request_id
        or provenance.retrieved_at != outcome.retrieved_at
        or provenance.response_sha256 != outcome.response_sha256
        or provenance.identifiers_used != outcome.identifiers_used
        or provenance.identifiers_used_sha256 != outcome.identifiers_used_sha256
        or provenance.request_binding_sha256 != outcome.request_binding_sha256
        or provenance.request_content_sha256 != outcome.request_content_sha256
        or provenance.connector_identity != outcome.connector_identity
        or provenance.connector_version_hash != outcome.connector_version_hash
        or provenance.request_builder_identity != outcome.request_builder_identity
        or provenance.request_builder_version_hash != outcome.request_builder_version_hash
        or provenance.identity_id != outcome.identity_id
        or provenance.identity_version != outcome.identity_version
        or provenance.identity_rule_version != outcome.identity_rule_version
        or provenance.identity_listing_state is not outcome.identity_listing_state
        or provenance.identity_evidence_ids != outcome.identity_evidence_ids
        or provenance.identity_evidence_set_sha256 != outcome.identity_evidence_set_sha256
        or provenance.identity_provenance_ids != outcome.identity_provenance_ids
        or provenance.identity_provenance_set_sha256 != outcome.identity_provenance_set_sha256
        or provenance.identity_hash != outcome.identity_hash
        or provenance.applicability_decision_hash != outcome.applicability_decision_hash
        or provenance.applicability_decision_id != outcome.applicability_decision_id
        or provenance.applicability_decision_content_sha256
        != outcome.applicability_decision_content_sha256
        or provenance.applicability_rule_version != outcome.applicability_rule_version
        or provenance.applicability_rule_sha256 != outcome.applicability_rule_sha256
        or provenance.applicability_reason_code != outcome.applicability_reason_code
        or provenance.rights_approval_id != outcome.rights_approval_id
        or provenance.rights_version_hash != outcome.rights_version_hash
        or provenance.rights_class is not outcome.rights_class
        or provenance.no_store_policy_version != outcome.no_store_policy_version
        or provenance.attribution_policy_version != outcome.attribution_policy_version
        or provenance.credential_version_id != outcome.credential_version_id
        or provenance.credential_bundle_hash != outcome.credential_bundle_hash
        or provenance.budget_reservation_id != outcome.budget_reservation_id
        or provenance.budget_policy_hash != outcome.budget_policy_hash
        or provenance.cache_decision_id != outcome.cache_decision_id
        or provenance.cache_policy_hash != outcome.cache_policy_hash
        or provenance.cache_disposition is not outcome.cache_disposition
        or provenance.admission_proof_sha256 != outcome.admission_proof_sha256
        or provenance.catalog_version != outcome.catalog_version
        or not _cached_fact_ancestry_matches(outcome, fact)
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_fact_invalid")


def _validate_cached_synthesis(
    outcome: ProviderOutcomePersistenceIntent,
    synthesis: SynthesisPersistenceIntent,
    facts: tuple[NormalizedFactPersistenceIntent, ...],
) -> None:
    if type(synthesis) is not SynthesisPersistenceIntent:
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_synthesis_invalid")
    if (
        any(
            type(value) is not str
            for value in (
                synthesis.synthesis_intent_id,
                synthesis.tenant_id,
                synthesis.deal_id,
                synthesis.run_id,
                synthesis.provider_id,
                synthesis.outcome_id,
                synthesis.text,
                synthesis.text_sha256,
            )
        )
        or type(synthesis.citation_ids) is not tuple
        or type(synthesis.citation_fact_ids) is not tuple
        or any(type(citation_id) is not str for citation_id in synthesis.citation_ids)
        or any(type(fact_id) is not str for fact_id in synthesis.citation_fact_ids)
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_synthesis_invalid")
    if (
        not synthesis.text
        or synthesis.text != synthesis.text.strip()
        or not _is_sha256(synthesis.text_sha256)
        or not synthesis.citation_ids
        or synthesis.citation_ids != tuple(sorted(set(synthesis.citation_ids)))
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_synthesis_invalid")
    citation_fact_ids = {
        fact.citation_id: fact.fact_id for fact in facts if fact.citation_id is not None
    }
    try:
        linked_fact_ids = tuple(
            citation_fact_ids[citation_id] for citation_id in synthesis.citation_ids
        )
    except (KeyError, TypeError):
        raise NormalizedFactsError(
            "ENRICHMENT_PROVENANCE_MISSING", "cached_synthesis_invalid"
        ) from None
    expected_id = _synthesis_intent_id(
        outcome_id=outcome.outcome_id,
        text_sha256=synthesis.text_sha256,
        citation_ids=synthesis.citation_ids,
        citation_fact_ids=linked_fact_ids,
    )
    if (
        outcome.provider_id != "perplexity"
        or synthesis.tenant_id != outcome.tenant_id
        or synthesis.deal_id != outcome.deal_id
        or synthesis.run_id != outcome.run_id
        or synthesis.provider_id != outcome.provider_id
        or synthesis.outcome_id != outcome.outcome_id
        or _sha256_text(synthesis.text) != synthesis.text_sha256
        or synthesis.citation_fact_ids != linked_fact_ids
        or synthesis.source_tier is not SourceTier.MAQBUL
        or synthesis.sanad_grade is not SanadGrade.C
        or synthesis.tier_usage is not TierUsage.SUPPORT_ONLY
        or synthesis.primary_eligible is not False
        or synthesis.synthesis_intent_id != expected_id
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_synthesis_invalid")


def _cached_fact_ancestry_matches(
    outcome: ProviderOutcomePersistenceIntent,
    fact: NormalizedFactPersistenceIntent,
) -> bool:
    provenance = fact.provenance
    if (
        provenance.cached_outcome_id != outcome.cached_outcome_id
        or provenance.cached_outcome_sha256 != outcome.cached_outcome_sha256
    ):
        return False
    if outcome.cached_outcome_id is None and outcome.cached_outcome_sha256 is None:
        return provenance.cached_fact_id is None and provenance.cached_fact_content_sha256 is None
    if (
        outcome.cached_outcome_id is None
        or not _is_sha256(outcome.cached_outcome_sha256)
        or provenance.cached_fact_id is None
        or not _is_sha256(provenance.cached_fact_content_sha256)
        or provenance.cached_fact_content_sha256 != fact.fact_content_sha256
        or provenance.cached_fact_id == fact.fact_id
    ):
        return False
    try:
        cached_outcome_uuid = UUID(outcome.cached_outcome_id)
        cached_fact_uuid = UUID(provenance.cached_fact_id)
    except (TypeError, ValueError, AttributeError):
        return False
    return cached_outcome_uuid.version == 5 and cached_fact_uuid.version == 5


def _materialize_cached_facts(
    admission: ProviderCacheHitIntent,
    payload: CachedProviderOutcomePayload,
    *,
    outcome_id: str,
) -> tuple[NormalizedFactPersistenceIntent, ...]:
    materialized: list[NormalizedFactPersistenceIntent] = []
    for source in payload.facts:
        fact_identity = {
            "outcome_id": outcome_id,
            "tenant_id": admission.tenant_id,
            "deal_id": admission.deal_id,
            "run_id": admission.run_id,
            "provider_id": admission.provider_id,
            "subject": source.subject,
            "predicate": source.predicate,
            "value_sha256": source.value_sha256,
            "fact_domain": source.fact_domain.value,
            "safe_source_ref": source.provenance.safe_source_ref,
            "citation_id": source.citation_id,
            "fact_content_sha256": source.fact_content_sha256,
        }
        fact_id = str(uuid5(_FACT_NAMESPACE, _canonical_json(fact_identity)))
        provenance = replace(
            source.provenance,
            provider_id=admission.provider_id,
            identifiers_used=admission.required_identifiers,
            identifiers_used_sha256=admission.identifiers_used_sha256,
            rights_approval_id=admission.rights_approval_id,
            rights_version_hash=admission.rights_version_hash,
            rights_class=admission.rights_class,
            no_store_policy_version=admission.no_store_policy_version,
            attribution_policy_version=admission.attribution_policy_version,
            credential_version_id=admission.credential_version_id,
            credential_bundle_hash=admission.credential_bundle_hash,
            budget_reservation_id=admission.budget_reservation_id,
            budget_policy_hash=admission.budget_policy_hash,
            cache_decision_id=admission.cache_decision_id,
            cache_policy_hash=admission.cache_policy_hash,
            cache_disposition=CacheDisposition.HIT,
            request_binding_sha256=admission.request_binding_sha256,
            admission_proof_sha256=admission.admission_proof_sha256,
            identity_id=admission.identity_id,
            identity_version=admission.identity_version,
            identity_rule_version=admission.identity_rule_version,
            identity_listing_state=admission.identity_listing_state,
            identity_evidence_ids=admission.identity_evidence_ids,
            identity_evidence_set_sha256=admission.identity_evidence_set_sha256,
            identity_provenance_ids=admission.identity_provenance_ids,
            identity_provenance_set_sha256=admission.identity_provenance_set_sha256,
            identity_hash=admission.identity_hash,
            applicability_decision_hash=admission.applicability_decision_hash,
            applicability_decision_id=admission.applicability_decision_id,
            applicability_decision_content_sha256=(admission.applicability_decision_content_sha256),
            applicability_rule_version=admission.applicability_rule_version,
            applicability_rule_sha256=admission.applicability_rule_sha256,
            applicability_reason_code=admission.applicability_reason_code,
            catalog_version=admission.catalog_version,
            cached_outcome_id=admission.cached_outcome_id,
            cached_outcome_sha256=admission.cached_outcome_sha256,
            cached_fact_id=source.fact_id,
            cached_fact_content_sha256=source.fact_content_sha256,
        )
        materialized.append(
            replace(
                source,
                fact_id=fact_id,
                tenant_id=admission.tenant_id,
                deal_id=admission.deal_id,
                run_id=admission.run_id,
                provider_id=admission.provider_id,
                outcome_id=outcome_id,
                provenance=provenance,
            )
        )
    return tuple(sorted(materialized, key=lambda fact: fact.fact_id))


def _materialize_cached_synthesis(
    admission: ProviderCacheHitIntent,
    payload: CachedProviderOutcomePayload,
    *,
    outcome_id: str,
    facts: tuple[NormalizedFactPersistenceIntent, ...],
) -> tuple[SynthesisPersistenceIntent, ...]:
    if not payload.synthesis_intents:
        return ()
    citation_fact_ids = {
        fact.citation_id: fact.fact_id for fact in facts if fact.citation_id is not None
    }
    materialized: list[SynthesisPersistenceIntent] = []
    for source in payload.synthesis_intents:
        try:
            linked_fact_ids = tuple(
                citation_fact_ids[citation_id] for citation_id in source.citation_ids
            )
        except (KeyError, TypeError, AttributeError):
            raise NormalizedFactsError(
                "ENRICHMENT_PROVENANCE_MISSING", "cached_synthesis_invalid"
            ) from None
        synthesis_id = _synthesis_intent_id(
            outcome_id=outcome_id,
            text_sha256=source.text_sha256,
            citation_ids=source.citation_ids,
            citation_fact_ids=linked_fact_ids,
        )
        materialized.append(
            replace(
                source,
                synthesis_intent_id=synthesis_id,
                tenant_id=admission.tenant_id,
                deal_id=admission.deal_id,
                run_id=admission.run_id,
                provider_id=admission.provider_id,
                outcome_id=outcome_id,
                citation_fact_ids=linked_fact_ids,
            )
        )
    return tuple(sorted(materialized, key=lambda item: item.synthesis_intent_id))


def _persisted_outcome_identity(outcome: ProviderOutcomePersistenceIntent) -> dict[str, Any]:
    return {
        "tenant_id": outcome.tenant_id,
        "deal_id": outcome.deal_id,
        "run_id": outcome.run_id,
        "provider_id": outcome.provider_id,
        "outcome_kind": outcome.outcome_kind.value,
        "provider_request_id": outcome.provider_request_id,
        "identity_id": outcome.identity_id,
        "identity_version": outcome.identity_version,
        "identity_rule_version": outcome.identity_rule_version,
        "identity_listing_state": outcome.identity_listing_state.value,
        "identity_evidence_ids": sorted(outcome.identity_evidence_ids),
        "identity_evidence_set_sha256": outcome.identity_evidence_set_sha256,
        "identity_provenance_ids": sorted(outcome.identity_provenance_ids),
        "identity_provenance_set_sha256": outcome.identity_provenance_set_sha256,
        "identity_hash": outcome.identity_hash,
        "listing_state": outcome.listing_state.value if outcome.listing_state else None,
        "applicability_decision_id": outcome.applicability_decision_id,
        "applicability_decision_hash": outcome.applicability_decision_hash,
        "applicability_decision_content_sha256": (outcome.applicability_decision_content_sha256),
        "applicability_rule_version": outcome.applicability_rule_version,
        "applicability_rule_sha256": outcome.applicability_rule_sha256,
        "applicability_reason_code": outcome.applicability_reason_code,
        "identifiers_used": sorted(outcome.identifiers_used) if outcome.identifiers_used else None,
        "identifiers_used_sha256": outcome.identifiers_used_sha256,
        "response_sha256": outcome.response_sha256,
        "retrieved_at": (
            _canonical_timestamp(outcome.retrieved_at) if outcome.retrieved_at else None
        ),
        "request_builder_version_hash": outcome.request_builder_version_hash,
        "request_binding_sha256": outcome.request_binding_sha256,
        "request_content_sha256": outcome.request_content_sha256,
        "connector_identity": outcome.connector_identity,
        "connector_version_hash": outcome.connector_version_hash,
        "request_builder_identity": outcome.request_builder_identity,
        "rights_approval_id": outcome.rights_approval_id,
        "rights_version_hash": outcome.rights_version_hash,
        "rights_class": outcome.rights_class.value if outcome.rights_class else None,
        "no_store_policy_version": outcome.no_store_policy_version,
        "attribution_policy_version": outcome.attribution_policy_version,
        "credential_version_id": outcome.credential_version_id,
        "credential_bundle_hash": outcome.credential_bundle_hash,
        "budget_reservation_id": outcome.budget_reservation_id,
        "budget_policy_hash": outcome.budget_policy_hash,
        "cache_decision_id": outcome.cache_decision_id,
        "cache_policy_hash": outcome.cache_policy_hash,
        "cache_disposition": (
            outcome.cache_disposition.value if outcome.cache_disposition else None
        ),
        "catalog_version": outcome.catalog_version,
        "external_context_index_allowed": outcome.external_context_index_allowed,
        "admission_proof_sha256": outcome.admission_proof_sha256,
        "cached_outcome_id": outcome.cached_outcome_id,
        "cached_outcome_sha256": outcome.cached_outcome_sha256,
    }


def _materialize_not_applicable(
    admission: ProviderNotApplicableIntent,
    execution: ProviderExecutionResult | CachedProviderOutcomePayload | None,
) -> ProviderIntentBundle:
    decision = admission.decision
    if not isinstance(decision, ApplicabilityDecision):
        raise NormalizedFactsError("ENRICHMENT_PROVIDER_FAILED", "not_applicable_invalid")
    try:
        decision_content_sha256 = canonical_applicability_decision_content_sha256(decision)
    except (TypeError, ValueError, AttributeError):
        raise NormalizedFactsError("ENRICHMENT_PROVIDER_FAILED", "not_applicable_invalid") from None
    if (
        not isinstance(admission, ProviderNotApplicableIntent)
        or execution is not None
        or not admission.zero_call
        or admission.listing_state not in {ListingState.PRIVATE, ListingState.UNLISTED}
        or admission.provider_id not in {"finnhub", "fmp"}
        or admission.catalog_version != PROVIDER_CATALOG_VERSION
        or not admission_proof_matches(admission)
        or decision.status is not ApplicabilityStatus.NOT_APPLICABLE
        or decision.provider_id != admission.provider_id
        or decision.identity_id != admission.identity_id
        or decision.identity_version != admission.identity_version
        or decision.identity_hash != admission.identity_hash
        or decision.reason_code != "VERIFIED_NOT_LISTED"
        or admission.applicability_decision_id != decision.decision_id
        or admission.applicability_decision_content_sha256 != decision.decision_content_sha256
        or decision.decision_hash != decision.decision_content_sha256
        or decision.decision_content_sha256 != decision_content_sha256
        or any(
            not isinstance(value, str) or not value.strip()
            for value in (
                admission.tenant_id,
                admission.deal_id,
                admission.run_id,
                admission.identity_id,
                admission.identity_version,
                admission.identity_rule_version,
                decision.rule_version,
                decision.reason_code,
            )
        )
        or any(
            not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None
            for value in (
                admission.identity_evidence_set_sha256,
                admission.identity_provenance_set_sha256,
                admission.identity_hash,
                admission.applicability_decision_content_sha256,
                decision.rule_sha256,
                decision.decision_hash,
            )
        )
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVIDER_FAILED", "not_applicable_invalid")
    identity = {
        "tenant_id": admission.tenant_id,
        "deal_id": admission.deal_id,
        "run_id": admission.run_id,
        "provider_id": admission.provider_id,
        "outcome_kind": ProviderOutcomeKind.NOT_APPLICABLE.value,
        "identity_id": admission.identity_id,
        "identity_version": admission.identity_version,
        "identity_rule_version": admission.identity_rule_version,
        "identity_listing_state": admission.listing_state.value,
        "identity_evidence_ids": sorted(admission.identity_evidence_ids),
        "identity_evidence_set_sha256": admission.identity_evidence_set_sha256,
        "identity_provenance_ids": sorted(admission.identity_provenance_ids),
        "identity_provenance_set_sha256": admission.identity_provenance_set_sha256,
        "identity_hash": admission.identity_hash,
        "listing_state": admission.listing_state.value,
        "applicability_decision_id": decision.decision_id,
        "applicability_decision_hash": decision.decision_hash,
        "applicability_decision_content_sha256": decision.decision_content_sha256,
        "applicability_rule_version": decision.rule_version,
        "applicability_rule_sha256": decision.rule_sha256,
        "applicability_reason_code": decision.reason_code,
        "identifiers_used": None,
        "identifiers_used_sha256": None,
        "provider_request_id": None,
        "retrieved_at": None,
        "response_sha256": None,
        "request_builder_version_hash": None,
        "request_binding_sha256": None,
        "request_content_sha256": None,
        "connector_identity": None,
        "connector_version_hash": None,
        "request_builder_identity": None,
        "rights_approval_id": None,
        "rights_version_hash": None,
        "rights_class": None,
        "no_store_policy_version": None,
        "attribution_policy_version": None,
        "credential_version_id": None,
        "credential_bundle_hash": None,
        "budget_reservation_id": None,
        "budget_policy_hash": None,
        "cache_decision_id": None,
        "cache_policy_hash": None,
        "cache_disposition": None,
        "catalog_version": admission.catalog_version,
        "admission_proof_sha256": admission.admission_proof_sha256,
        "cached_outcome_id": None,
        "cached_outcome_sha256": None,
        "external_context_index_allowed": False,
    }
    outcome_id = str(uuid5(_OUTCOME_NAMESPACE, _canonical_json(identity)))
    outcome = ProviderOutcomePersistenceIntent(
        outcome_id=outcome_id,
        outcome_sha256=_sha256_json(
            {
                **identity,
                "fact_ids": (),
                "rag_intent_ids": (),
                "consumer_edge_ids": (),
                "synthesis_intent_ids": (),
                "budget_reservation_disposition_id": None,
            }
        ),
        tenant_id=admission.tenant_id,
        deal_id=admission.deal_id,
        run_id=admission.run_id,
        provider_id=admission.provider_id,
        outcome_kind=ProviderOutcomeKind.NOT_APPLICABLE,
        identity_id=admission.identity_id,
        identity_version=admission.identity_version,
        identity_rule_version=admission.identity_rule_version,
        identity_listing_state=admission.listing_state,
        identity_evidence_ids=admission.identity_evidence_ids,
        identity_evidence_set_sha256=admission.identity_evidence_set_sha256,
        identity_provenance_ids=admission.identity_provenance_ids,
        identity_provenance_set_sha256=admission.identity_provenance_set_sha256,
        identity_hash=admission.identity_hash,
        listing_state=admission.listing_state,
        applicability_decision_id=decision.decision_id,
        applicability_decision_hash=decision.decision_hash,
        applicability_decision_content_sha256=decision.decision_content_sha256,
        applicability_rule_version=decision.rule_version,
        applicability_rule_sha256=decision.rule_sha256,
        applicability_reason_code=decision.reason_code,
        identifiers_used=None,
        identifiers_used_sha256=None,
        provider_request_id=None,
        retrieved_at=None,
        response_sha256=None,
        rights_approval_id=None,
        rights_version_hash=None,
        rights_class=None,
        no_store_policy_version=None,
        attribution_policy_version=None,
        external_context_index_allowed=False,
        credential_version_id=None,
        credential_bundle_hash=None,
        budget_reservation_id=None,
        budget_policy_hash=None,
        cache_decision_id=None,
        cache_policy_hash=None,
        cache_disposition=None,
        request_builder_version_hash=None,
        request_binding_sha256=None,
        request_content_sha256=None,
        connector_identity=None,
        connector_version_hash=None,
        request_builder_identity=None,
        admission_proof_sha256=admission.admission_proof_sha256,
        catalog_version=admission.catalog_version,
        cached_outcome_id=None,
        cached_outcome_sha256=None,
        budget_reservation_disposition_id=None,
        fact_ids=(),
        rag_intent_ids=(),
        consumer_edge_ids=(),
        synthesis_intent_ids=(),
    )
    return ProviderIntentBundle(outcome, (), (), (), ())


def _validate_call_intent(call: ProviderCallIntent) -> None:
    if not isinstance(call, ProviderCallIntent):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "call_lineage_invalid")
    text_values = (
        call.tenant_id,
        call.deal_id,
        call.run_id,
        call.identity_id,
        call.identity_version,
        call.identity_rule_version,
        call.rights_approval_id,
        call.no_store_policy_version,
        call.attribution_policy_version,
        call.budget_reservation_id,
        call.cache_decision_id,
        call.applicability_rule_version,
        call.applicability_reason_code,
        call.applicability_decision_id,
    )
    hash_values = (
        call.identity_hash,
        call.identity_evidence_set_sha256,
        call.identity_provenance_set_sha256,
        call.applicability_decision_hash,
        call.applicability_decision_content_sha256,
        call.applicability_rule_sha256,
        call.rights_version_hash,
        call.budget_policy_hash,
        call.cache_policy_hash,
        call.identifiers_used_sha256,
        call.request_binding_sha256,
        call.admission_proof_sha256,
    )
    if (
        not isinstance(call.provider_id, str)
        or call.provider_id not in PROVIDER_CATALOG
        or any(not isinstance(value, str) or not value.strip() for value in text_values)
        or any(
            not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None
            for value in hash_values
        )
        or not isinstance(call.required_identifiers, tuple)
        or not call.required_identifiers
        or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or any(not isinstance(value, str) or not value.strip() for value in item)
            for item in call.required_identifiers
        )
        or len(dict(call.required_identifiers)) != len(call.required_identifiers)
        or not isinstance(call.cache_disposition, CacheDisposition)
        or call.cache_disposition not in {CacheDisposition.MISS, CacheDisposition.BYPASS}
        or not isinstance(call.external_context_index_allowed, bool)
        or call.catalog_version != PROVIDER_CATALOG_VERSION
        or call.rights_class is not PROVIDER_CATALOG[call.provider_id].rights_class
        or call.identifiers_used_sha256 != _sha256_json(sorted(call.required_identifiers))
        or not request_binding_matches(call)
        or not admission_proof_matches(call)
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "call_lineage_invalid")
    if (call.credential_version_id is None) != (call.credential_bundle_hash is None):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "credential_lineage_invalid")
    if call.credential_version_id is not None and (
        not isinstance(call.credential_version_id, str)
        or not call.credential_version_id.strip()
        or not isinstance(call.credential_bundle_hash, str)
        or _SHA256_RE.fullmatch(call.credential_bundle_hash) is None
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "credential_lineage_invalid")
    expects_credentials = bool(PROVIDER_CATALOG[call.provider_id].credential_fields)
    if expects_credentials != (call.credential_version_id is not None):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "credential_lineage_invalid")
    try:
        UUID(call.budget_reservation_id)
    except (TypeError, ValueError, AttributeError):
        raise NormalizedFactsError(
            "ENRICHMENT_PROVENANCE_MISSING", "budget_lineage_invalid"
        ) from None


def _validate_execution(call: ProviderCallIntent, execution: ProviderExecutionResult) -> None:
    if (
        not isinstance(execution, ProviderExecutionResult)
        or not isinstance(execution.status, ProviderExecutionStatus)
        or not isinstance(execution.request_succeeded, bool)
        or not isinstance(execution.schema_valid, bool)
        or not isinstance(execution.facts, tuple)
        or any(not isinstance(fact, ProviderFactInput) for fact in execution.facts)
        or not isinstance(execution.operation_evidence, tuple)
        or not execution.operation_evidence
        or any(
            not isinstance(item, ProviderExecutionOperationEvidence)
            for item in execution.operation_evidence
        )
        or not isinstance(execution.supported_synthesis, tuple)
        or not isinstance(execution.provider_request_id, str)
        or not execution.provider_request_id.strip()
        or not isinstance(execution.retrieved_at, datetime)
        or execution.retrieved_at.tzinfo is None
        or execution.retrieved_at.utcoffset() is None
        or not isinstance(execution.response_sha256, str)
        or _SHA256_RE.fullmatch(execution.response_sha256) is None
        or not isinstance(execution.request_builder_version_hash, str)
        or _SHA256_RE.fullmatch(execution.request_builder_version_hash) is None
        or not isinstance(execution.provider_id, str)
        or execution.provider_id != call.provider_id
        or not isinstance(execution.admission_proof_sha256, str)
        or execution.admission_proof_sha256 != call.admission_proof_sha256
        or not isinstance(execution.request_binding_sha256, str)
        or execution.request_binding_sha256 != call.request_binding_sha256
        or not isinstance(execution.identifiers_used, tuple)
        or execution.identifiers_used != call.required_identifiers
        or not isinstance(execution.identifiers_used_sha256, str)
        or execution.identifiers_used_sha256 != call.identifiers_used_sha256
        or execution.identifiers_used_sha256 != _sha256_json(sorted(execution.identifiers_used))
        or not isinstance(execution.request_content_sha256, str)
        or _SHA256_RE.fullmatch(execution.request_content_sha256) is None
        or not isinstance(execution.connector_identity, str)
        or execution.connector_identity != call.connector_identity
        or not isinstance(execution.connector_version_hash, str)
        or execution.connector_version_hash != call.connector_version_hash
        or not isinstance(execution.request_builder_identity, str)
        or execution.request_builder_identity != call.request_builder_identity
        or not isinstance(execution.request_builder_version_hash, str)
        or execution.request_builder_version_hash != call.request_builder_version_hash
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "request_lineage_invalid")
    operation_ids: set[str] = set()
    for operation in execution.operation_evidence:
        if (
            not isinstance(operation.operation_id, str)
            or not operation.operation_id.strip()
            or operation.operation_id in operation_ids
            or not isinstance(operation.safe_request_content, bytes)
            or not operation.safe_request_content
            or not isinstance(operation.response_body, bytes)
            or not operation.response_body
            or not isinstance(operation.request_content_sha256, str)
            or operation.request_content_sha256
            != hashlib.sha256(operation.safe_request_content).hexdigest()
            or not isinstance(operation.response_sha256, str)
            or operation.response_sha256 != hashlib.sha256(operation.response_body).hexdigest()
        ):
            raise NormalizedFactsError(
                "ENRICHMENT_PROVENANCE_MISSING", "operation_evidence_invalid"
            )
        operation_ids.add(operation.operation_id)
    if execution.request_content_sha256 != _combined_operation_sha256(
        tuple(item.request_content_sha256 for item in execution.operation_evidence)
    ) or execution.response_sha256 != _combined_operation_sha256(
        tuple(item.response_sha256 for item in execution.operation_evidence)
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "operation_evidence_invalid")


def _materialize_facts(
    call: ProviderCallIntent,
    execution: ProviderExecutionResult,
    *,
    outcome_id: str,
    identifiers_sha256: str,
) -> tuple[NormalizedFactPersistenceIntent, ...]:
    intents: dict[str, NormalizedFactPersistenceIntent] = {}
    for fact_input in execution.facts:
        subject = _required_text(fact_input.subject, "fact_subject_invalid")
        predicate = _required_text(fact_input.predicate, "fact_predicate_invalid")
        safe_source_ref = _validate_safe_source_ref(fact_input.safe_source_ref)
        canonical_value_json = _canonical_fact_value(fact_input.value)
        value_sha256 = _sha256_text(canonical_value_json)
        try:
            domain = FactDomain(fact_input.fact_domain)
            deliverable_consumers_for(call.provider_id, domain)
        except (TypeError, ValueError, ProviderCatalogRoutingError):
            raise NormalizedFactsError(
                "ENRICHMENT_CONSUMPTION_MISSING", "fact_domain_not_routable"
            ) from None

        citation = _validate_fact_citation(call, execution, fact_input, safe_source_ref)
        citation_id = citation.citation_id if citation else None
        source_tier = citation.source_tier if citation else None
        sanad_grade = citation.sanad_grade if citation else None
        tier_usage = citation.tier_usage if citation else None
        primary_eligible = citation.primary_eligible if citation else None
        fact_content_sha256 = _fact_content_sha256(
            subject=subject,
            predicate=predicate,
            canonical_value_json=canonical_value_json,
            safe_source_ref=safe_source_ref,
            citation_id=citation_id,
            source_tier=source_tier,
            sanad_grade=sanad_grade,
            tier_usage=tier_usage,
            primary_eligible=primary_eligible,
        )
        fact_identity = {
            "outcome_id": outcome_id,
            "tenant_id": call.tenant_id,
            "deal_id": call.deal_id,
            "run_id": call.run_id,
            "provider_id": call.provider_id,
            "subject": subject,
            "predicate": predicate,
            "value_sha256": value_sha256,
            "fact_domain": domain.value,
            "safe_source_ref": safe_source_ref,
            "citation_id": citation_id,
            "fact_content_sha256": fact_content_sha256,
        }
        fact_id = str(uuid5(_FACT_NAMESPACE, _canonical_json(fact_identity)))
        provenance = ProviderFactProvenanceIntent(
            provider_id=call.provider_id,
            provider_request_id=execution.provider_request_id,
            retrieved_at=execution.retrieved_at.astimezone(UTC),
            identifiers_used=execution.identifiers_used,
            identifiers_used_sha256=identifiers_sha256,
            response_sha256=execution.response_sha256,
            safe_source_ref=safe_source_ref,
            rights_approval_id=call.rights_approval_id,
            rights_version_hash=call.rights_version_hash,
            rights_class=call.rights_class,
            no_store_policy_version=call.no_store_policy_version,
            attribution_policy_version=call.attribution_policy_version,
            credential_version_id=call.credential_version_id,
            credential_bundle_hash=call.credential_bundle_hash,
            budget_reservation_id=call.budget_reservation_id,
            budget_policy_hash=call.budget_policy_hash,
            cache_decision_id=call.cache_decision_id,
            cache_policy_hash=call.cache_policy_hash,
            cache_disposition=call.cache_disposition,
            request_builder_version_hash=execution.request_builder_version_hash,
            request_binding_sha256=execution.request_binding_sha256,
            request_content_sha256=execution.request_content_sha256,
            connector_identity=execution.connector_identity,
            connector_version_hash=execution.connector_version_hash,
            request_builder_identity=execution.request_builder_identity,
            admission_proof_sha256=call.admission_proof_sha256,
            identity_id=call.identity_id,
            identity_version=call.identity_version,
            identity_rule_version=call.identity_rule_version,
            identity_listing_state=call.identity_listing_state,
            identity_evidence_ids=call.identity_evidence_ids,
            identity_evidence_set_sha256=call.identity_evidence_set_sha256,
            identity_provenance_ids=call.identity_provenance_ids,
            identity_provenance_set_sha256=call.identity_provenance_set_sha256,
            identity_hash=call.identity_hash,
            applicability_decision_hash=call.applicability_decision_hash,
            applicability_decision_id=call.applicability_decision_id,
            applicability_decision_content_sha256=(call.applicability_decision_content_sha256),
            applicability_rule_version=call.applicability_rule_version,
            applicability_rule_sha256=call.applicability_rule_sha256,
            applicability_reason_code=call.applicability_reason_code,
            catalog_version=call.catalog_version,
        )
        intent = NormalizedFactPersistenceIntent(
            fact_id=fact_id,
            tenant_id=call.tenant_id,
            deal_id=call.deal_id,
            run_id=call.run_id,
            provider_id=call.provider_id,
            outcome_id=outcome_id,
            subject=subject,
            predicate=predicate,
            canonical_value_json=canonical_value_json,
            value_sha256=value_sha256,
            fact_content_sha256=fact_content_sha256,
            fact_domain=domain,
            provenance=provenance,
            citation_id=citation_id,
            source_tier=source_tier,
            sanad_grade=sanad_grade,
            tier_usage=tier_usage,
            primary_eligible=primary_eligible,
        )
        previous = intents.setdefault(fact_id, intent)
        if previous != intent:
            raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "fact_id_collision")
    return tuple(intents[fact_id] for fact_id in sorted(intents))


def _validate_fact_citation(
    call: ProviderCallIntent,
    execution: ProviderExecutionResult,
    fact_input: ProviderFactInput,
    safe_source_ref: str,
) -> PerplexityCitationEvidence | None:
    if call.provider_id != "perplexity":
        if fact_input.citation_evidence is not None:
            raise NormalizedFactsError(
                "ENRICHMENT_PROVENANCE_MISSING", "unexpected_citation_evidence"
            )
        return None
    citation = fact_input.citation_evidence
    if not isinstance(citation, PerplexityCitationEvidence):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "perplexity_citation_missing")
    try:
        canonical_url = canonicalize_citation_url(citation.canonical_url)
    except PerplexityEvidenceError:
        raise NormalizedFactsError(
            "ENRICHMENT_PROVENANCE_MISSING", "perplexity_citation_invalid"
        ) from None
    url_hash = _sha256_text(canonical_url)
    provenance = citation.provenance
    if (
        canonical_url != citation.canonical_url
        or citation.canonical_url_sha256 != url_hash
        or citation.citation_id != f"perplexity-citation-{url_hash}"
        or citation.source_tier is not SourceTier.MAQBUL
        or citation.sanad_grade is not SanadGrade.C
        or citation.tier_usage is not TierUsage.SUPPORT_ONLY
        or citation.primary_eligible is not False
        or provenance.provider_id != "perplexity"
        or provenance.provider_request_id != execution.provider_request_id
        or provenance.response_sha256 != execution.response_sha256
        or provenance.retrieved_at != execution.retrieved_at
        or safe_source_ref != canonical_url
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "perplexity_citation_invalid")
    return citation


def _materialize_rag_intents(
    call: ProviderCallIntent | ProviderCacheHitIntent,
    facts: tuple[NormalizedFactPersistenceIntent, ...],
) -> tuple[ExternalContextRagIntent, ...]:
    if not call.external_context_index_allowed:
        return ()
    return tuple(
        ExternalContextRagIntent(
            rag_intent_id=str(uuid5(_RAG_NAMESPACE, f"{fact.fact_id}:external_context")),
            tenant_id=call.tenant_id,
            deal_id=call.deal_id,
            run_id=call.run_id,
            provider_id=call.provider_id,
            fact_id=fact.fact_id,
            namespace="external_context",
            content_sha256=fact.fact_content_sha256,
            safe_source_ref=fact.provenance.safe_source_ref,
        )
        for fact in facts
    )


def _materialize_consumer_edges(
    call: ProviderCallIntent | ProviderCacheHitIntent,
    facts: tuple[NormalizedFactPersistenceIntent, ...],
) -> tuple[ExpectedConsumerEdgeIntent, ...]:
    spec = PROVIDER_CATALOG[call.provider_id]
    edges: list[ExpectedConsumerEdgeIntent] = []
    for fact in facts:
        consumers = set(spec.analysis_consumers)
        consumers.update(spec.common_consumers)
        consumers.update(deliverable_consumers_for(call.provider_id, fact.fact_domain))
        if not call.external_context_index_allowed:
            consumers.discard("RAG_EVIDENCE:external_context")
            consumers.discard("GRAPH_EVIDENCE:external_context")
        if any(
            consumer.startswith("DEBATE:") or "COURT" in consumer or "VEP" in consumer
            for consumer in consumers
        ):
            raise NormalizedFactsError(
                "ENRICHMENT_CONSUMPTION_MISSING", "layer1_consumer_forbidden"
            )
        edges.extend(
            ExpectedConsumerEdgeIntent(
                edge_intent_id=str(uuid5(_EDGE_NAMESPACE, f"{fact.fact_id}:{consumer}")),
                tenant_id=call.tenant_id,
                deal_id=call.deal_id,
                run_id=call.run_id,
                provider_id=call.provider_id,
                fact_id=fact.fact_id,
                fact_domain=fact.fact_domain,
                consumer_id=consumer,
            )
            for consumer in sorted(consumers)
        )
    return tuple(edges)


def _synthesis_intent_id(
    *,
    outcome_id: str,
    text_sha256: str,
    citation_ids: tuple[str, ...],
    citation_fact_ids: tuple[str, ...],
) -> str:
    return str(
        uuid5(
            _SYNTHESIS_NAMESPACE,
            _canonical_json(
                {
                    "outcome_id": outcome_id,
                    "text_sha256": text_sha256,
                    "citation_ids": citation_ids,
                    "citation_fact_ids": citation_fact_ids,
                }
            ),
        )
    )


def _materialize_synthesis(
    call: ProviderCallIntent,
    execution: ProviderExecutionResult,
    *,
    outcome_id: str,
    facts: tuple[NormalizedFactPersistenceIntent, ...],
) -> tuple[SynthesisPersistenceIntent, ...]:
    if not execution.supported_synthesis:
        return ()
    if call.provider_id != "perplexity":
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "unexpected_provider_synthesis")
    citation_fact_ids = {
        fact.citation_id: fact.fact_id for fact in facts if fact.citation_id is not None
    }
    intents: dict[str, SynthesisPersistenceIntent] = {}
    for raw_unit in execution.supported_synthesis:
        if not isinstance(raw_unit, SupportedSynthesisUnit):
            raise NormalizedFactsError(
                "ENRICHMENT_PROVENANCE_MISSING", "perplexity_synthesis_invalid"
            )
        if (
            not isinstance(raw_unit.text, str)
            or not raw_unit.text
            or raw_unit.text != raw_unit.text.strip()
            or not isinstance(raw_unit.citation_ids, tuple)
            or not raw_unit.citation_ids
            or any(
                not isinstance(citation_id, str) or not citation_id
                for citation_id in raw_unit.citation_ids
            )
            or raw_unit.citation_ids != tuple(sorted(set(raw_unit.citation_ids)))
            or raw_unit.source_tier is not SourceTier.MAQBUL
            or raw_unit.sanad_grade is not SanadGrade.C
            or raw_unit.tier_usage is not TierUsage.SUPPORT_ONLY
            or raw_unit.primary_eligible is not False
            or _sha256_text(raw_unit.text) != raw_unit.text_sha256
            or any(citation_id not in citation_fact_ids for citation_id in raw_unit.citation_ids)
        ):
            raise NormalizedFactsError(
                "ENRICHMENT_PROVENANCE_MISSING", "perplexity_synthesis_invalid"
            )
        linked_fact_ids = tuple(citation_fact_ids[item] for item in raw_unit.citation_ids)
        synthesis_id = _synthesis_intent_id(
            outcome_id=outcome_id,
            text_sha256=raw_unit.text_sha256,
            citation_ids=raw_unit.citation_ids,
            citation_fact_ids=linked_fact_ids,
        )
        intents[synthesis_id] = SynthesisPersistenceIntent(
            synthesis_intent_id=synthesis_id,
            tenant_id=call.tenant_id,
            deal_id=call.deal_id,
            run_id=call.run_id,
            provider_id=call.provider_id,
            outcome_id=outcome_id,
            text=raw_unit.text,
            text_sha256=raw_unit.text_sha256,
            citation_ids=raw_unit.citation_ids,
            citation_fact_ids=linked_fact_ids,
            source_tier=raw_unit.source_tier,
            sanad_grade=raw_unit.sanad_grade,
            tier_usage=raw_unit.tier_usage,
            primary_eligible=False,
        )
    return tuple(intents[item] for item in sorted(intents))


def _validate_safe_source_ref(value: object) -> str:
    source_ref = _required_text(value, "fact_source_reference_invalid")
    if len(source_ref) > 2_048 or any(ord(character) < 32 for character in source_ref):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "fact_source_reference_invalid")
    if "://" not in source_ref:
        if _SAFE_OPAQUE_SOURCE_RE.fullmatch(source_ref) is None:
            raise NormalizedFactsError(
                "ENRICHMENT_PROVENANCE_MISSING", "fact_source_reference_invalid"
            )
        return source_ref
    try:
        canonical_source_ref = canonicalize_citation_url(source_ref)
    except PerplexityEvidenceError:
        raise NormalizedFactsError(
            "ENRICHMENT_PROVENANCE_MISSING", "fact_source_reference_invalid"
        ) from None
    if canonical_source_ref != source_ref:
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "fact_source_reference_invalid")
    return canonical_source_ref


def _fact_content_sha256(
    *,
    subject: str,
    predicate: str,
    canonical_value_json: str,
    safe_source_ref: str,
    citation_id: str | None,
    source_tier: SourceTier | None,
    sanad_grade: SanadGrade | None,
    tier_usage: TierUsage | None,
    primary_eligible: bool | None,
) -> str:
    return _sha256_json(
        {
            "schema": "external-context-fact-v2",
            "subject": subject,
            "predicate": predicate,
            "canonical_value_json": canonical_value_json,
            "safe_source_ref": safe_source_ref,
            "citation_id": citation_id,
            "source_tier": source_tier.value if source_tier is not None else None,
            "sanad_grade": sanad_grade.value if sanad_grade is not None else None,
            "tier_usage": tier_usage.value if tier_usage is not None else None,
            "primary_eligible": primary_eligible,
        }
    )


def _validate_persisted_fact_evidence(fact: NormalizedFactPersistenceIntent) -> None:
    _validate_safe_source_ref(fact.provenance.safe_source_ref)
    if fact.provider_id != "perplexity":
        if any(
            value is not None
            for value in (
                fact.citation_id,
                fact.source_tier,
                fact.sanad_grade,
                fact.tier_usage,
                fact.primary_eligible,
            )
        ):
            raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_fact_invalid")
        return
    try:
        canonical_url = canonicalize_citation_url(fact.provenance.safe_source_ref)
    except PerplexityEvidenceError:
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_fact_invalid") from None
    citation_id = f"perplexity-citation-{_sha256_text(canonical_url)}"
    if (
        canonical_url != fact.provenance.safe_source_ref
        or fact.citation_id != citation_id
        or fact.source_tier is not SourceTier.MAQBUL
        or fact.sanad_grade is not SanadGrade.C
        or fact.tier_usage is not TierUsage.SUPPORT_ONLY
        or fact.primary_eligible is not False
    ):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", "cached_fact_invalid")


def _required_text(value: object, reason: str) -> str:
    if not isinstance(value, str):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", reason)
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or any(character in normalized for character in "\x00\r\n"):
        raise NormalizedFactsError("ENRICHMENT_PROVENANCE_MISSING", reason)
    return normalized


def _canonical_fact_value(value: object) -> str:
    try:
        return json.dumps(
            _canonical_fact_data(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise NormalizedFactsError(
            "ENRICHMENT_PROVENANCE_MISSING", "fact_value_not_canonical_json"
        ) from None


def _canonical_fact_data(value: object) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("fact float must be finite")
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return [_canonical_fact_data(item) for item in value]
    if isinstance(value, dict):
        canonical: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("fact object keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in canonical:
                raise ValueError("fact object key normalization collision")
            canonical[normalized_key] = _canonical_fact_data(item)
        return canonical
    raise TypeError("fact value is not canonical JSON")


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _combined_operation_sha256(hashes: tuple[str, ...]) -> str:
    if len(hashes) == 1:
        return hashes[0]
    return hashlib.sha256("|".join(hashes).encode("ascii")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None
