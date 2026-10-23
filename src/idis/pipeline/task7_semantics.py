"""Typed, dispatch-only semantic validation for all 28 full-strict-v21 steps."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, NoReturn
from uuid import UUID

from idis.pipeline.contract_registry import PIPELINE_CONTRACTS
from idis.pipeline.task7_census import (
    CENSUS_SCHEMA_REGISTRY,
    CensusEnvelopeV1,
    build_census_envelope,
)
from idis.pipeline.task7_native_registry import (
    STEP_OUTPUT_MISSING,
    require_native_authority,
)
from idis.pipeline.task7_usage import ProducerRefV1

PIPELINE_CONTRACT_UNAVAILABLE: Final = "PIPELINE_CONTRACT_UNAVAILABLE"
PIPELINE_CONTRACT_HASH_MISMATCH: Final = "PIPELINE_CONTRACT_HASH_MISMATCH"
STEP_SEMANTIC_VALIDATION_FAILED: Final = "STEP_SEMANTIC_VALIDATION_FAILED"

_HASH_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_NON_SUCCESS_STATUSES: Final = frozenset(
    {
        "FAILED",
        "PARTIAL",
        "DEFERRED",
        "BLOCKED",
        "SKIPPED",
        "NOT_ATTEMPTED",
    }
)
_FAILURE_COUNTERS: Final = frozenset(
    {"rejected_count", "failed_count", "deferred_count", "blocked_count"}
)


class StepSemanticValidationError(RuntimeError):
    """Stable fail-closed semantic validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StepSemanticSpec:
    """One reviewed semantic row from the full-strict-v21 contract."""

    step_order: int
    step_name: str
    semantic_rule_version: str
    semantic_rule_hash: str
    aggregate_receipt_kind: str
    census_schema_id: str
    mandatory_subroles: tuple[str, ...]
    native_output_kinds: tuple[str, ...]
    terminal_status: str


@dataclass(frozen=True, slots=True)
class NativeOutputEvidence:
    """Persistence evidence for one reviewed native output."""

    output_kind: str
    output_object_id: UUID
    native_identity_sha256: str
    native_output_sha256: str
    persisted: bool


@dataclass(frozen=True, slots=True)
class SubroleReceiptEvidence:
    """Persistence evidence for one mandatory subrole receipt."""

    subrole: str
    native_identity_sha256: str
    native_output_sha256: str
    persisted: bool


@dataclass(frozen=True, slots=True)
class Step5ApplicabilityEvidenceV1:
    """Frozen, extra-forbid Step 5 applicability evidence."""

    coverage_record_id: str
    subject_key: str
    methodology_type: str
    selection_hash: str
    question_semantic_hash: str
    outcome: str
    basis_code: str


@dataclass(frozen=True, slots=True)
class Step16ProviderApplicabilityEvidenceV1:
    """Frozen, extra-forbid Step 16 provider applicability evidence."""

    provider_id: str
    identity_output_ref: ProducerRefV1
    listing_state: str
    verified_identifier_kind: str | None
    verified_identifier_hash: str | None
    applicability_rule_id: str
    applicability_rule_version: str
    applicability_rule_hash: str
    outcome: str
    basis_code: str


Task7ApplicabilityEvidence = Step5ApplicabilityEvidenceV1 | Step16ProviderApplicabilityEvidenceV1


@dataclass(frozen=True, slots=True)
class ApplicabilityDecisionEvidence:
    """Internal typed carrier validated before the completion transaction."""

    upstream_producer: ProducerRefV1
    contract_rule_id: str
    contract_rule_version: str
    contract_rule_hash: str
    evidence: Task7ApplicabilityEvidence


@dataclass(frozen=True, slots=True)
class SemanticValidationInput:
    """Complete evidence passed to a step-specific semantic validator."""

    step_name: str
    semantic_rule_version: str
    semantic_rule_hash: str
    terminal_status: str
    result_payload: Mapping[str, Any]
    census: CensusEnvelopeV1
    native_outputs: tuple[NativeOutputEvidence, ...]
    subrole_receipts: tuple[SubroleReceiptEvidence, ...]
    producer_persistence_confirmed: bool
    applicability_decisions: tuple[ApplicabilityDecisionEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticValidationResult:
    """Validated semantic evidence safe for the completion transaction."""

    step_name: str
    semantic_rule_version: str
    semantic_rule_hash: str
    aggregate_receipt_kind: str
    terminal_status: str
    census: CensusEnvelopeV1
    native_outputs: tuple[NativeOutputEvidence, ...]
    subrole_receipts: tuple[SubroleReceiptEvidence, ...]
    applicability_decisions: tuple[ApplicabilityDecisionEvidence, ...]


_SEMANTIC_ROWS: Final[
    tuple[
        tuple[
            str,
            str,
            tuple[str, ...],
            tuple[str, ...],
        ],
        ...,
    ]
] = (
    ("STATUS_ONLY_INVENTORY_V1", "inventory-census-v1", (), ()),
    ("STATUS_ONLY_HANDOFF_V1", "handoff-census-v1", (), ()),
    ("STATUS_ONLY_INGEST_CHECK_V1", "ingest-check-census-v1", (), ()),
    ("STATUS_ONLY_PREFLIGHT_V1", "preflight-census-v1", (), ()),
    (
        "METHODOLOGY_COVERAGE_RECEIPT_V1",
        "coverage-census-v1",
        (),
        ("METHODOLOGY_COVERAGE_IDENTITY",),
    ),
    (
        "METHODOLOGY_TASK_PLAN_RECEIPT_V1",
        "task-plan-census-v1",
        (),
        ("METHODOLOGY_EXTRACTION_TASK",),
    ),
    (
        "METHODOLOGY_EXECUTION_RECEIPT_V1",
        "methodology-execution-census-v1",
        (),
        ("METHODOLOGY_EXTRACTION_RESULT", "METHODOLOGY_FACTUAL_OUTPUT"),
    ),
    (
        "METHODOLOGY_CLAIM_RECEIPT_V1",
        "claim-materialization-census-v1",
        (),
        ("METHODOLOGY_CLAIM_LINK",),
    ),
    (
        "METHODOLOGY_EVIDENCE_RECEIPT_V1",
        "evidence-materialization-census-v1",
        (),
        ("METHODOLOGY_EVIDENCE_LINK",),
    ),
    (
        "METHODOLOGY_SANAD_RECEIPT_V1",
        "sanad-materialization-census-v1",
        (),
        ("METHODOLOGY_SANAD_LINK", "METHODOLOGY_DEFECT_LINK"),
    ),
    (
        "METHODOLOGY_CALC_RECEIPT_V1",
        "calculation-census-v1",
        (),
        ("METHODOLOGY_CALCULATION",),
    ),
    (
        "METHODOLOGY_DASHBOARD_RECEIPT_V1",
        "dashboard-census-v1",
        (),
        ("METHODOLOGY_TRUTH_DASHBOARD_ROW",),
    ),
    (
        "EXTRACT_RECEIPT_V1",
        "extract-census-v1",
        ("rag_context",),
        (
            "GENERIC_EXTRACTION_OUTPUT",
            "METHODOLOGY_CLAIM_LINK",
            "METHODOLOGY_EVIDENCE_LINK",
        ),
    ),
    (
        "GRADE_RECEIPT_V1",
        "grade-census-v1",
        (),
        ("RUN_CLAIM_GRADING_LINK",),
    ),
    ("CALC_RECEIPT_V1", "calculation-census-v1", (), ("CALCULATION",)),
    (
        "COMPANY_IDENTITY_RECEIPT_V1",
        "identity-census-v1",
        (),
        ("COMPANY_IDENTITY_OUTPUT",),
    ),
    (
        "ENRICHMENT_RECEIPT_V1",
        "enrichment-census-v1",
        (
            "sec_edgar",
            "companies_house",
            "github",
            "fred",
            "finnhub",
            "fmp",
            "world_bank",
            "escwa_catalog",
            "escwa_ispar",
            "qatar_open_data",
            "hackernews",
            "gdelt",
            "patentsview",
            "wayback",
            "google_news_rss",
            "epo_open_patent",
            "semantic_scholar",
            "perplexity",
        ),
        ("ENRICHMENT_PROVIDER_OUTCOME", "ENRICHMENT_FACT"),
    ),
    (
        "GRAPH_RECEIPT_V1",
        "graph-census-v1",
        ("internal_evidence", "external_context"),
        ("GRAPH_PROJECTION_RESULT",),
    ),
    (
        "RAG_RECEIPT_V1",
        "rag-census-v1",
        ("internal_evidence", "external_context"),
        ("RAG_INDEX_RESULT", "RAG_QUERY_RESULT"),
    ),
    (
        "CONFLICT_CHECK_RECEIPT_V1",
        "conflict-census-v1",
        (),
        ("CONFLICT_PLAN", "CONFLICT_EXECUTION", "CONFLICT_FINDING"),
    ),
    (
        "EVIDENCE_TRUST_COURT_RECEIPT_V1",
        "court-census-v1",
        (),
        ("EVIDENCE_TRUST_FINDING", "COURT_MUHASABAH"),
    ),
    (
        "VEP_RECEIPT_V1",
        "vep-census-v1",
        (),
        ("VALIDATED_EVIDENCE_PACKAGE", "VEP_CONTENT_REFERENCE"),
    ),
    (
        "DEBATE_RECEIPT_V1",
        "debate-census-v1",
        (
            "advocate",
            "sanad_breaker",
            "contradiction_finder",
            "risk_officer",
            "arbiter",
        ),
        ("DEBATE_SESSION", "DEBATE_ROLE_OUTPUT", "DEBATE_MUHASABAH"),
    ),
    (
        "ANALYSIS_RECEIPT_V1",
        "analysis-census-v1",
        (
            "financial_agent",
            "market_agent",
            "technical_agent",
            "terms_agent",
            "team_agent",
            "risk_officer_agent",
            "historian_agent",
            "sector_specialist_agent",
        ),
        ("ANALYSIS_AGENT_OUTPUT", "ANALYSIS_MUHASABAH"),
    ),
    ("SCORING_RECEIPT_V1", "scoring-census-v1", (), ("SCORING_OUTPUT",)),
    (
        "LAYER2_READINESS_RECEIPT_V1",
        "readiness-census-v1",
        (),
        ("LAYER2_READINESS_OUTPUT",),
    ),
    (
        "LAYER2_RECEIPT_V1",
        "layer2-census-v1",
        ("ic_advocate", "ic_challenger", "ic_arbiter"),
        (
            "LAYER2_CHALLENGE",
            "LAYER2_FINDING",
            "LAYER2_ROLE_OUTPUT",
            "LAYER2_MUHASABAH",
        ),
    ),
    (
        "PRODUCT_BUNDLE_RECEIPT_V1",
        "product-bundle-census-v1",
        (
            "reconciliation",
            "screening_snapshot_pdf",
            "screening_snapshot_docx",
            "ic_memo_pdf",
            "ic_memo_docx",
            "truth_dashboard",
            "qa_brief",
            "executive_summary",
            "commercial_diligence",
            "financial_diligence",
            "risk_register",
            "layer2_ic_challenge",
            "evidence_index",
            "run_summary",
            "provenance_appendix",
            "product_bundle_manifest",
        ),
        ("DELIVERABLE_ARTIFACT", "DELIVERABLE_MANIFEST_REFERENCE"),
    ),
)


def _build_semantic_registry() -> dict[str, StepSemanticSpec]:
    contract = PIPELINE_CONTRACTS["full-strict-v21"]
    if len(contract.steps) != len(_SEMANTIC_ROWS):
        raise RuntimeError("full-strict-v21 semantic registry cardinality drift")
    registry: dict[str, StepSemanticSpec] = {}
    for pipeline_step, semantic_row in zip(
        contract.steps,
        _SEMANTIC_ROWS,
        strict=True,
    ):
        aggregate, census, subroles, natives = semantic_row
        if census not in CENSUS_SCHEMA_REGISTRY:
            raise RuntimeError(f"missing reviewed CENSUS schema: {census}")
        registry[pipeline_step.step_name] = StepSemanticSpec(
            step_order=pipeline_step.step_order,
            step_name=pipeline_step.step_name,
            semantic_rule_version=pipeline_step.semantic_rule_version,
            semantic_rule_hash=pipeline_step.semantic_rule_hash,
            aggregate_receipt_kind=aggregate,
            census_schema_id=census,
            mandatory_subroles=subroles,
            native_output_kinds=natives,
            terminal_status="COMPLETED",
        )
    return registry


STEP_SEMANTIC_REGISTRY: Final = MappingProxyType(_build_semantic_registry())


def _fail(code: str, message: str) -> NoReturn:
    raise StepSemanticValidationError(code, message)


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and _HASH_PATTERN.fullmatch(value) is not None


def _walk_for_non_success(value: object, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = key.casefold()
            item_path = f"{path}/{key}"
            if (
                (normalized_key == "status" or normalized_key.endswith("_status"))
                and isinstance(item, str)
                and (item.strip().upper().replace("-", "_") in _NON_SUCCESS_STATUSES)
            ):
                _fail(
                    STEP_SEMANTIC_VALIDATION_FAILED,
                    f"internal non-success status at {item_path}",
                )
            if normalized_key == "all_failed" and item is True:
                _fail(
                    STEP_SEMANTIC_VALIDATION_FAILED,
                    f"all_failed is true at {item_path}",
                )
            if normalized_key in _FAILURE_COUNTERS:
                if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                    _fail(
                        STEP_SEMANTIC_VALIDATION_FAILED,
                        f"failure counter is not a non-negative integer at {item_path}",
                    )
                if item != 0:
                    _fail(
                        STEP_SEMANTIC_VALIDATION_FAILED,
                        f"failure counter is nonzero at {item_path}",
                    )
            _walk_for_non_success(item, item_path)
        return
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for index, item in enumerate(value):
            _walk_for_non_success(item, f"{path}/{index}")


def _validate_native_outputs(
    spec: StepSemanticSpec,
    values: tuple[NativeOutputEvidence, ...],
) -> tuple[NativeOutputEvidence, ...]:
    if tuple(value.output_kind for value in values) != spec.native_output_kinds:
        _fail(
            STEP_OUTPUT_MISSING,
            f"{spec.step_name} lacks its exact reviewed native-output set",
        )
    for value in values:
        require_native_authority(spec.step_name, value.output_kind)
        if not value.persisted:
            _fail(
                STEP_OUTPUT_MISSING,
                f"{spec.step_name}/{value.output_kind} is not durably persisted",
            )
        if not isinstance(value.output_object_id, UUID):
            _fail(
                STEP_OUTPUT_MISSING,
                f"{spec.step_name}/{value.output_kind} lacks a UUID output root",
            )
        if not _valid_hash(value.native_identity_sha256) or not _valid_hash(
            value.native_output_sha256
        ):
            _fail(
                STEP_OUTPUT_MISSING,
                f"{spec.step_name}/{value.output_kind} lacks reviewed native hashes",
            )
    return values


def _validate_subrole_receipts(
    spec: StepSemanticSpec,
    values: tuple[SubroleReceiptEvidence, ...],
) -> tuple[SubroleReceiptEvidence, ...]:
    if tuple(value.subrole for value in values) != spec.mandatory_subroles:
        _fail(
            STEP_OUTPUT_MISSING,
            f"{spec.step_name} lacks its exact reviewed subrole receipt set",
        )
    for value in values:
        if not value.persisted:
            _fail(
                STEP_OUTPUT_MISSING,
                f"{spec.step_name}/{value.subrole} receipt is not durably persisted",
            )
        if not _valid_hash(value.native_identity_sha256) or not _valid_hash(
            value.native_output_sha256
        ):
            _fail(
                STEP_OUTPUT_MISSING,
                f"{spec.step_name}/{value.subrole} receipt lacks reviewed hashes",
            )
    return values


def _validate_producer_ref(value: ProducerRefV1) -> None:
    if (
        not isinstance(value, ProducerRefV1)
        or value.producer_type != "OUTPUT_OBJECT"
        or not isinstance(value.producer_id, UUID)
        or not _valid_hash(value.producer_identity_sha256)
        or not _valid_hash(value.producer_value_sha256)
    ):
        _fail(
            STEP_SEMANTIC_VALIDATION_FAILED,
            "applicability evidence lacks an immutable output-object producer",
        )


def _validate_step5_applicability(
    value: Step5ApplicabilityEvidenceV1,
) -> None:
    if (
        not value.coverage_record_id
        or not value.subject_key
        or value.methodology_type not in {"commercial", "financial"}
        or not _valid_hash(value.selection_hash)
        or not _valid_hash(value.question_semantic_hash)
        or value.outcome not in {"APPLICABLE", "NOT_APPLICABLE"}
        or _SAFE_CODE_PATTERN.fullmatch(value.basis_code) is None
    ):
        _fail(
            STEP_SEMANTIC_VALIDATION_FAILED,
            "Step 5 applicability evidence is malformed",
        )


def _validate_step16_applicability(
    value: Step16ProviderApplicabilityEvidenceV1,
) -> None:
    _validate_producer_ref(value.identity_output_ref)
    provider_ids = frozenset(STEP_SEMANTIC_REGISTRY["ENRICHMENT"].mandatory_subroles)
    identifier_pair_is_valid = (
        value.verified_identifier_kind is None and value.verified_identifier_hash is None
    ) or (
        isinstance(value.verified_identifier_kind, str)
        and bool(value.verified_identifier_kind)
        and _valid_hash(value.verified_identifier_hash)
    )
    if (
        value.provider_id not in provider_ids
        or value.listing_state not in {"LISTED", "PRIVATE", "UNLISTED", "UNKNOWN"}
        or not identifier_pair_is_valid
        or not value.applicability_rule_id
        or not value.applicability_rule_version
        or not _valid_hash(value.applicability_rule_hash)
        or value.outcome not in {"APPLICABLE", "NOT_APPLICABLE"}
        or _SAFE_CODE_PATTERN.fullmatch(value.basis_code) is None
        or (
            value.outcome == "NOT_APPLICABLE"
            and (
                value.listing_state == "UNKNOWN"
                or value.verified_identifier_kind is None
                or value.verified_identifier_hash is None
            )
        )
    ):
        _fail(
            STEP_SEMANTIC_VALIDATION_FAILED,
            "Step 16 applicability evidence is malformed or unverified",
        )


def _validate_applicability_decisions(
    spec: StepSemanticSpec,
    values: tuple[ApplicabilityDecisionEvidence, ...],
) -> tuple[ApplicabilityDecisionEvidence, ...]:
    from idis.pipeline.task7_edges import EDGE_RULE_REGISTRY

    expected_evidence_type: type[Task7ApplicabilityEvidence] | None
    expected_predicate_id: str | None
    if spec.step_name == "METHODOLOGY_COVERAGE_INIT":
        expected_evidence_type = Step5ApplicabilityEvidenceV1
        expected_predicate_id = "STEP5_APPLICABLE"
    elif spec.step_name == "METHODOLOGY_COMPANY_IDENTITY_PACKAGE":
        expected_evidence_type = Step16ProviderApplicabilityEvidenceV1
        expected_predicate_id = "STEP16_PROVIDER_APPLICABLE"
    else:
        expected_evidence_type = None
        expected_predicate_id = None

    if expected_evidence_type is None:
        if values:
            _fail(
                STEP_SEMANTIC_VALIDATION_FAILED,
                f"{spec.step_name} may not emit applicability decisions",
            )
        return ()

    seen: set[tuple[str, str]] = set()
    subjects_by_rule: dict[str, set[str]] = {}
    for value in values:
        if (
            not isinstance(value, ApplicabilityDecisionEvidence)
            or type(value.evidence) is not expected_evidence_type
        ):
            _fail(
                STEP_SEMANTIC_VALIDATION_FAILED,
                f"{spec.step_name} carries the wrong applicability evidence type",
            )
        _validate_producer_ref(value.upstream_producer)
        rule = EDGE_RULE_REGISTRY.get(value.contract_rule_id)
        if (
            rule is None
            or rule.predicate_id != expected_predicate_id
            or value.contract_rule_version != rule.rule_version
            or value.contract_rule_hash != rule.rule_sha256
        ):
            _fail(
                PIPELINE_CONTRACT_HASH_MISMATCH,
                f"{spec.step_name} applicability rule differs from the contract",
            )
        evidence = value.evidence
        if isinstance(evidence, Step5ApplicabilityEvidenceV1):
            _validate_step5_applicability(evidence)
            subject_key = evidence.subject_key
        else:
            _validate_step16_applicability(evidence)
            if evidence.identity_output_ref != value.upstream_producer:
                _fail(
                    STEP_SEMANTIC_VALIDATION_FAILED,
                    "Step 16 identity evidence differs from its upstream producer",
                )
            subject_key = evidence.provider_id
        unique_key = (value.contract_rule_id, subject_key)
        if unique_key in seen:
            _fail(
                STEP_SEMANTIC_VALIDATION_FAILED,
                "applicability evidence contains a duplicate governed subject",
            )
        seen.add(unique_key)
        subjects_by_rule.setdefault(value.contract_rule_id, set()).add(subject_key)

    if spec.step_name == "METHODOLOGY_COVERAGE_INIT":
        required_rules = {
            "E009_COVERAGE_TO_PLANNING",
            "E012_TASK_TO_EXECUTION",
            "E046_APPLICABLE_COVERAGE_DOWNSTREAM",
        }
        subject_sets = tuple(subjects_by_rule.get(rule_id, set()) for rule_id in required_rules)
        if (
            set(subjects_by_rule) != required_rules
            or not subject_sets
            or not subject_sets[0]
            or any(subjects != subject_sets[0] for subjects in subject_sets[1:])
        ):
            _fail(
                STEP_SEMANTIC_VALIDATION_FAILED,
                "Step 5 lacks the exact three-rule applicability subject matrix",
            )
    else:
        expected_providers = set(STEP_SEMANTIC_REGISTRY["ENRICHMENT"].mandatory_subroles)
        if (
            set(subjects_by_rule) != {"E025_IDENTITY_TO_PROVIDERS"}
            or subjects_by_rule["E025_IDENTITY_TO_PROVIDERS"] != expected_providers
        ):
            _fail(
                STEP_SEMANTIC_VALIDATION_FAILED,
                "Step 16 lacks the exact 18-provider applicability matrix",
            )
    return values


def _validate_for_spec(
    spec: StepSemanticSpec,
    request: SemanticValidationInput,
) -> SemanticValidationResult:
    if (
        request.semantic_rule_version != spec.semantic_rule_version
        or request.semantic_rule_hash != spec.semantic_rule_hash
    ):
        _fail(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            f"{spec.step_name} semantic rule differs from full-strict-v21",
        )
    if request.terminal_status != spec.terminal_status:
        _fail(
            STEP_SEMANTIC_VALIDATION_FAILED,
            f"{spec.step_name} is not terminally COMPLETED",
        )
    if not request.producer_persistence_confirmed:
        _fail(
            STEP_OUTPUT_MISSING,
            f"{spec.step_name} producer persistence is unconfirmed",
        )
    if request.census.schema_id != spec.census_schema_id:
        _fail(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            f"{spec.step_name} carries the wrong CENSUS schema",
        )
    schema = CENSUS_SCHEMA_REGISTRY[spec.census_schema_id]
    if (
        request.census.schema_version != schema.schema_version
        or request.census.schema_sha256 != schema.schema_sha256
    ):
        _fail(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            f"{spec.step_name} CENSUS schema identity differs from full-strict-v21",
        )
    census = build_census_envelope(
        schema_id=spec.census_schema_id,
        payload=request.census.payload,
        expected_schema_sha256=request.census.schema_sha256,
    )
    if census.canonical_sha256 != request.census.canonical_sha256:
        _fail(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            f"{spec.step_name} CENSUS payload digest is not canonical",
        )
    _walk_for_non_success(request.result_payload)
    if "durable_export" in census.payload and census.payload["durable_export"] is not True:
        _fail(
            STEP_SEMANTIC_VALIDATION_FAILED,
            f"{spec.step_name} did not durably export its product bundle",
        )
    native_outputs = _validate_native_outputs(spec, request.native_outputs)
    subrole_receipts = _validate_subrole_receipts(spec, request.subrole_receipts)
    applicability_decisions = _validate_applicability_decisions(
        spec,
        request.applicability_decisions,
    )
    return SemanticValidationResult(
        step_name=spec.step_name,
        semantic_rule_version=spec.semantic_rule_version,
        semantic_rule_hash=spec.semantic_rule_hash,
        aggregate_receipt_kind=spec.aggregate_receipt_kind,
        terminal_status=spec.terminal_status,
        census=census,
        native_outputs=native_outputs,
        subrole_receipts=subrole_receipts,
        applicability_decisions=applicability_decisions,
    )


_StepValidator = Callable[[SemanticValidationInput], SemanticValidationResult]


def _build_dispatch() -> dict[str, _StepValidator]:
    dispatch: dict[str, _StepValidator] = {}
    for step_name, spec in STEP_SEMANTIC_REGISTRY.items():

        def validate_step(
            request: SemanticValidationInput,
            *,
            _spec: StepSemanticSpec = spec,
        ) -> SemanticValidationResult:
            return _validate_for_spec(_spec, request)

        dispatch[step_name] = validate_step
    return dispatch


class StepSemanticValidator:
    """Dispatch every step through its exact reviewed semantic validator."""

    def __init__(self) -> None:
        self._dispatch: Mapping[str, _StepValidator] = MappingProxyType(_build_dispatch())

    @property
    def dispatch_keys(self) -> tuple[str, ...]:
        """Return the exact reviewed dispatch order."""
        return tuple(self._dispatch)

    def validate(
        self,
        request: SemanticValidationInput,
    ) -> SemanticValidationResult:
        """Validate one semantic completion request, failing closed on drift."""
        validator = self._dispatch.get(request.step_name)
        if validator is None:
            _fail(
                PIPELINE_CONTRACT_UNAVAILABLE,
                f"step {request.step_name!r} is absent from full-strict-v21",
            )
        return validator(request)
