"""Authored-record models for the obligation register."""

from __future__ import annotations

from pydantic import Field

from idis.methodology.obligations.models_records import ObligationModel
from idis.methodology.obligations.vocab import (
    AmbiguityDecisionOwner,
    AmbiguityStatus,
    AmbiguityTopic,
    ApplicabilityDecision,
    BaselineKind,
    CalculationKind,
    CapabilityStatus,
    ChildStatus,
    ConsumerKind,
    CoordinateRequirement,
    CrossRowKind,
    Derivation,
    DispositionKind,
    EvidenceType,
    FundFeedbackStatus,
    Independence,
    OmissionVerdict,
    OutputDestinationStatus,
    ProducerKind,
    RoleAliasOwner,
    RoleAliasStatus,
    SpecialistAgentType,
    ViewClosure,
    ViewStatus,
)


class CoordinateSlot(ObligationModel):
    """One coordinate requirement and an optional note."""

    requirement: CoordinateRequirement
    note: str | None = None


class Coordinates(ObligationModel):
    """The seven coordinate keys on a child or a profile default."""

    entity: CoordinateSlot
    consolidation_scope: CoordinateSlot
    period: CoordinateSlot
    actual_forecast_status: CoordinateSlot
    currency: CoordinateSlot
    unit: CoordinateSlot
    adjustment: CoordinateSlot


class ProducerDetail(ObligationModel):
    """Who produces the answer to a child obligation."""

    kind: ProducerKind
    detail: str


class ConsumerDetail(ObligationModel):
    """Who consumes the answer to a child obligation."""

    kind: ConsumerKind
    detail: str


class ResponsibleSpecialist(ObligationModel):
    """Registered agent types, plus the commercial template role when one applies."""

    registered_types: tuple[SpecialistAgentType, ...]
    template_role: str | None = None


class AnalystReview(ObligationModel):
    """Whether an analyst must review the child before it is treated as answered."""

    required: bool
    reason: str | None = None


class OutputDestination(ObligationModel):
    """Where a finished answer is reported."""

    template: str
    report_section: str
    report_subsection: str | None = None
    deliverable_ref: str | None = None
    status: OutputDestinationStatus
    deviation_reason: str | None = None


class Capability(ObligationModel):
    """Current ability of the system to satisfy the child."""

    status: CapabilityStatus
    dependencies: tuple[str, ...] = ()
    note: str | None = None


class FundFeedback(ObligationModel):
    """Fund review state. Every child starts as proposed."""

    status: FundFeedbackStatus
    note: str | None = None


class SourceQuote(ObligationModel):
    """An exact substring of a parent field. A null parent id means the child's own parent."""

    field: str
    occurrence: int = 0
    parent_id: str | None = None
    text: str


class RequiredFact(ObligationModel):
    """One fact the child must establish."""

    description: str
    fact_id: str
    needs: tuple[str, ...]


class CalculationRule(ObligationModel):
    """One calculation the child carries."""

    kind: CalculationKind
    formula_ordinal: int | None = None
    binding_ordinal: int | None = None
    rule_code: str | None = None
    note: str | None = None


class EvidenceRequirement(ObligationModel):
    """One acceptable evidence item."""

    description: str
    document_categories: tuple[str, ...] = ()
    evidence_type: EvidenceType
    independence: Independence
    min_count: int = Field(ge=1)


class Applicability(ObligationModel):
    """Whether the child applies, and the condition when it does not always apply."""

    condition: str | None = None
    decision: ApplicabilityDecision


class ParentProfile(ObligationModel):
    """Defaults shared by the children of one parent."""

    parent_id: str
    default_coordinates: Coordinates | None = None
    default_producer: ProducerDetail | None = None
    default_consumer: ConsumerDetail | None = None
    default_specialists: ResponsibleSpecialist | None = None
    default_output_destination: OutputDestination | None = None
    default_analyst_review: AnalystReview | None = None
    default_capability: Capability | None = None
    default_fund_feedback_status: FundFeedbackStatus | None = None
    parent_summary_note: str | None = None


class ChildObligation(ObligationModel):
    """One thing a finished analysis must show for a parent."""

    acceptable_evidence: tuple[EvidenceRequirement, ...]
    acceptance_case: str
    acceptance_evidence_refs: tuple[str, ...] = ()
    ambiguity_ids: tuple[str, ...] = ()
    analyst_review: AnalystReview | None = None
    applicability: Applicability
    calculation_rules: tuple[CalculationRule, ...] = ()
    capability: Capability | None = None
    child_id: str
    completeness_conditions: tuple[str, ...]
    consumer: ConsumerDetail | None = None
    coordinates: Coordinates | None = None
    cross_row_instruction_ids: tuple[str, ...] = ()
    derivation: Derivation
    derivation_rationale: str | None = None
    fund_feedback: FundFeedback | None = None
    joint_answer_reason: str | None = None
    judgment_sought: str | None = None
    obligation_statement: str
    output_destination: OutputDestination | None = None
    parent_id: str
    plan_minimum_codes: tuple[str, ...] = ()
    producer: ProducerDetail | None = None
    required_facts: tuple[RequiredFact, ...] = ()
    responsible_specialist: ResponsibleSpecialist | None = None
    retired_reason: str | None = None
    shared_instruction_ids: tuple[str, ...] = ()
    source_quotes: tuple[SourceQuote, ...] = ()
    status: ChildStatus


class Disposition(ObligationModel):
    """An explicit accounting decision for one uncovered run or one marker."""

    kind: DispositionKind
    parent_id: str
    field: str
    start: int
    end: int
    text_sha256: str
    disposition: str
    child_ids: tuple[str, ...] = ()
    note: str | None = None


class Ambiguity(ObligationModel):
    """One workbook ambiguity or apparent error. The source text is not amended."""

    ambiguity_id: str
    topic_code: AmbiguityTopic
    affected_parent_ids: tuple[str, ...]
    description: str
    decision_owner: AmbiguityDecisionOwner
    blocks_definitive_answer: bool
    status: AmbiguityStatus


class MissingObligation(ObligationModel):
    """A gap noted by the omission audit, as wording or as existing child ids."""

    wording: str | None = None
    child_ids: tuple[str, ...] = ()


class OmissionAuditRecord(ObligationModel):
    """Independent pass over one parent."""

    parent_id: str
    verdict: OmissionVerdict
    missing_obligations: tuple[MissingObligation, ...] = ()
    notes: str | None = None


class RoleAlias(ObligationModel):
    """Proposed map from a commercial template role to registered agent types."""

    template_role: str
    proposed_registered_types: tuple[SpecialistAgentType, ...]
    rationale: str
    decision_owner: RoleAliasOwner
    status: RoleAliasStatus


class BaselineExpectation(ObligationModel):
    """A coordinator fact the register must satisfy. The note carries no wording."""

    expectation_id: str
    kind: BaselineKind
    source_coordinate: str
    target_coordinates: tuple[str, ...] = ()
    all_rows_of_sheet: str | None = None
    note: str


class ViewRecord(ObligationModel):
    """A named subset of parents. Parent ids may be public coordinates."""

    view_id: str
    parent_ids: tuple[str, ...]
    closure: ViewClosure
    status: ViewStatus
    agreed_by: str | None = None


class LedgerRecord(ObligationModel):
    """One child id issued by a seal. Lines are appended and never rewritten."""

    child_id: str
    parent_id: str
    public_coordinate: str
    first_seal: int


class SealCounts(ObligationModel):
    """Integer counts stored on a seal."""

    parents: int
    children: int
    active_children: int
    retired_children: int
    new_ids: int
    ambiguities: int
    cross_row_instructions: int
    dispositions: int


class SealRecord(ObligationModel):
    """One seal of the derived and authored tree.

    ``scope`` is the string ``full`` or a sorted tuple of shard codes.
    """

    seq: int
    tree_hash: str
    created_utc: str
    scope: tuple[str, ...] | str
    counts: SealCounts
    snapshot: str
    workbook_sha256: str
    register_complete: bool


class CrossRowInstruction(ObligationModel):
    """An authored instruction whose source text applies to other parents."""

    instruction_id: str
    kind: CrossRowKind
    source_parent_id: str
    source_field: str
    source_quote: SourceQuote
    target_parent_ids: tuple[str, ...]
    note: str


class ResolvedChild(ObligationModel):
    """A child after profile defaults fill null fields. Validators use this view."""

    child_id: str
    parent_id: str
    status: ChildStatus
    retired_reason: str | None
    derivation: Derivation
    derivation_rationale: str | None
    source_quotes: tuple[SourceQuote, ...]
    joint_answer_reason: str | None
    shared_instruction_ids: tuple[str, ...]
    cross_row_instruction_ids: tuple[str, ...]
    plan_minimum_codes: tuple[str, ...]
    obligation_statement: str
    required_facts: tuple[RequiredFact, ...]
    calculation_rules: tuple[CalculationRule, ...]
    judgment_sought: str | None
    acceptable_evidence: tuple[EvidenceRequirement, ...]
    completeness_conditions: tuple[str, ...]
    applicability: Applicability
    coordinates: Coordinates | None
    producer: ProducerDetail | None
    consumer: ConsumerDetail | None
    responsible_specialist: ResponsibleSpecialist | None
    analyst_review: AnalystReview | None
    output_destination: OutputDestination | None
    capability: Capability | None
    acceptance_case: str
    acceptance_evidence_refs: tuple[str, ...]
    ambiguity_ids: tuple[str, ...]
    fund_feedback: FundFeedback | None
