"""IDIS Domain Models — Pydantic models for persistence entities.

Phase 3.1: Ingestion Gate storage primitives.
Phase 4.1: Deterministic calculation models.
Phase 5.1: Debate orchestration models.
"""

from idis.models.calc_sanad import CalcSanad, GradeExplanationEntry, SanadGrade
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
from idis.models.deterministic_calculation import (
    CalcInputs,
    CalcOutput,
    CalcType,
    DeterministicCalculation,
)
from idis.models.document import Document, DocumentType, ParseStatus
from idis.models.document_artifact import DocType, DocumentArtifact
from idis.models.document_span import DocumentSpan, SpanLocator, SpanType

__all__ = [
    "AgentOutput",
    "ArbiterDecision",
    "CalcInputs",
    "CalcOutput",
    "CalcSanad",
    "CalcType",
    "DebateConfig",
    "DebateMessage",
    "DebateRole",
    "DebateState",
    "DeterministicCalculation",
    "DocType",
    "Document",
    "DocumentArtifact",
    "DocumentSpan",
    "DocumentType",
    "GradeExplanationEntry",
    "MuhasabahRecord",
    "ParseStatus",
    "PositionSnapshot",
    "SanadGrade",
    "SpanLocator",
    "SpanType",
    "StopReason",
]
