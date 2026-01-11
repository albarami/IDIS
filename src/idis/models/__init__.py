"""IDIS Domain Models — Pydantic models for persistence entities.

Phase 3.1: Ingestion Gate storage primitives.
Phase 4.1: Deterministic calculation models.
Phase 5.1: Debate orchestration models.
Phase 5.2: Canonical MuhasabahRecord with nested structures.
Phase POST-5.2: ValueStruct type hierarchy for typed values.
Phase 6.1: Evidence-linked deliverables models.
"""

from idis.models.calc_sanad import CalcSanad, GradeExplanationEntry, SanadGrade
from idis.models.claim import (
    CalcLoopGuard,
    CalcLoopGuardError,
    Claim,
    ClaimAction,
    ClaimClass,
    ClaimType,
    ClaimVerdict,
    Corroboration,
    CorroborationStatus,
    Materiality,
)
from idis.models.debate import (
    AgentOutput,
    ArbiterDecision,
    DebateConfig,
    DebateMessage,
    DebateRole,
    DebateState,
    MuhasabahRecord,
    PositionSnapshot,
    StopReason,
)
from idis.models.deliverables import (
    AuditAppendix,
    AuditAppendixEntry,
    DeliverableExportFormat,
    DeliverableExportRequest,
    DeliverableExportResult,
    DeliverableFact,
    DeliverableSection,
    DissentSection,
    ICMemo,
    RefType,
    ScreeningSnapshot,
)
from idis.models.deterministic_calculation import (
    CalcInputs,
    CalcOutput,
    CalcType,
    DeterministicCalculation,
)
from idis.models.document import Document, DocumentType, ParseStatus
from idis.models.document_artifact import DocType, DocumentArtifact
from idis.models.document_span import DocumentSpan, SpanLocator, SpanType
from idis.models.muhasabah_record import (
    FalsifiabilityTest,
    ImpactLevel,
    MuhasabahRecordCanonical,
    Uncertainty,
    muhasabah_to_validator_dict,
)
from idis.models.value_structs import (
    CountValue,
    Currency,
    DateValue,
    MonetaryValue,
    PercentageValue,
    RangeValue,
    SemanticTag,
    TextValue,
    TimeWindow,
    ValueStruct,
    ValueStructType,
    parse_value_struct,
    value_struct_to_dict,
)

__all__ = [
    "AgentOutput",
    "ArbiterDecision",
    "AuditAppendix",
    "AuditAppendixEntry",
    "CalcInputs",
    "CalcLoopGuard",
    "CalcLoopGuardError",
    "CalcOutput",
    "CalcSanad",
    "CalcType",
    "Claim",
    "ClaimAction",
    "ClaimClass",
    "ClaimType",
    "ClaimVerdict",
    "Corroboration",
    "CorroborationStatus",
    "CountValue",
    "Currency",
    "DateValue",
    "DebateConfig",
    "DebateMessage",
    "DebateRole",
    "DebateState",
    "DeliverableExportFormat",
    "DeliverableExportRequest",
    "DeliverableExportResult",
    "DeliverableFact",
    "DeliverableSection",
    "DeterministicCalculation",
    "DissentSection",
    "DocType",
    "Document",
    "DocumentArtifact",
    "DocumentSpan",
    "DocumentType",
    "FalsifiabilityTest",
    "GradeExplanationEntry",
    "ICMemo",
    "ImpactLevel",
    "Materiality",
    "MonetaryValue",
    "MuhasabahRecord",
    "MuhasabahRecordCanonical",
    "ParseStatus",
    "PercentageValue",
    "PositionSnapshot",
    "RangeValue",
    "RefType",
    "SanadGrade",
    "ScreeningSnapshot",
    "SemanticTag",
    "SpanLocator",
    "SpanType",
    "StopReason",
    "TextValue",
    "TimeWindow",
    "Uncertainty",
    "ValueStruct",
    "ValueStructType",
    "muhasabah_to_validator_dict",
    "parse_value_struct",
    "value_struct_to_dict",
]
