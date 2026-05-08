"""Tests for Phase 2.6 methodology claim materialization."""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any

from idis.audit.sink import InMemoryAuditSink
from idis.models.claim_materialization import (
    ClaimMaterializationReason,
    ClaimMaterializationResult,
    ClaimMaterializationStatus,
)
from idis.models.extraction_execution import (
    MethodologyClaimDraft,
    MethodologyExtractionExecutionResult,
    MethodologyExtractionExecutionStatus,
    MethodologyExtractionExecutionSummary,
)
from idis.services.claims.service import ClaimService, CreateClaimInput
from idis.services.extraction.claim_materializer import MethodologyClaimMaterializationService

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "99999999-9999-9999-9999-999999999999"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


class FailingClaimService(ClaimService):
    """ClaimService variant that raises for selected claim text."""

    def create(self, input_data: CreateClaimInput) -> dict[str, Any]:
        if "fail this draft" in input_data.claim_text:
            raise RuntimeError("synthetic claim creation failure")
        return super().create(input_data)


def _draft(
    *,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
    document_id: str = "doc-financial-model",
    span_id: str = "span-001",
    claim_text: str = "Revenue was $10M in FY2024.",
    predicate: str = "revenue",
    value: dict[str, Any] | None = None,
    confidence: Decimal | None = Decimal("0.97"),
    dhabt: Decimal | None = Decimal("0.95"),
    source_span_metadata: list[dict[str, Any]] | None = None,
) -> MethodologyClaimDraft:
    if source_span_metadata is None:
        source_span_metadata = [
            {
                "span_id": span_id,
                "document_id": document_id,
                "locator": {"sheet": "P&L", "cell": "A1"},
                "evidence_tags": ["schedule"],
            }
        ]
    return MethodologyClaimDraft(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        extraction_task_id="et_revenue_quality",
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        document_id=document_id,
        source_span_ids=[span_id],
        claim_text=claim_text,
        claim_class="FINANCIAL",
        predicate=predicate,
        value=value if value is not None else {"value": 10_000_000, "unit": "USD"},
        extraction_confidence=confidence,  # type: ignore[arg-type]
        dhabt_score=dhabt,  # type: ignore[arg-type]
        future_claim_input_preview={
            "deal_id": "tampered-preview-deal",
            "claim_class": "TAMPERED",
            "claim_text": "Tampered preview claim text.",
            "predicate": "tampered",
            "value": {"value": 0},
            "primary_span_id": "tampered-span",
            "corroboration": {
                "methodology_claim_draft_id": "mcd_tampered",
                "extraction_task_id": "et_tampered",
                "methodology_id": "tampered_methodology",
                "methodology_version_id": "tampered_version",
                "methodology_question_id": "tampered_question",
                "document_id": "tampered-document",
                "source_span_ids": ["tampered-span"],
                "source_span_metadata": source_span_metadata,
                "extraction_confidence": "0.01",
                "dhabt_score": "0.01",
            },
        },
    )


def _service(claim_service: ClaimService | None = None) -> MethodologyClaimMaterializationService:
    return MethodologyClaimMaterializationService(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_service=claim_service
        or ClaimService(tenant_id=TENANT_ID, db_conn=None, audit_sink=InMemoryAuditSink()),
    )


def _materialize(
    drafts: list[MethodologyClaimDraft],
    claim_service: ClaimService | None = None,
) -> ClaimMaterializationResult:
    return _service(claim_service).materialize(drafts=drafts)


def test_accepted_draft_creates_claim_through_claim_service() -> None:
    draft = _draft()
    result = _materialize([draft])

    assert result.status == ClaimMaterializationStatus.COMPLETED
    assert result.summary.created_claim_count == 1
    mapping = result.draft_claim_mappings[0]
    assert mapping.methodology_claim_draft_id == draft.methodology_claim_draft_id


def test_execution_result_input_materializes_accepted_claim_drafts() -> None:
    draft = _draft()
    execution_result = MethodologyExtractionExecutionResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=MethodologyExtractionExecutionStatus.COMPLETED,
        task_results=[],
        accepted_claim_drafts=[draft],
        summary=MethodologyExtractionExecutionSummary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            total_tasks=1,
            executed_tasks=1,
            skipped_tasks=0,
            failed_tasks=0,
            accepted_draft_count=1,
            rejected_draft_count=0,
            by_status={"completed": 1},
            by_reason={},
        ),
    )

    result = _service().materialize(execution_result=execution_result)

    assert result.status == ClaimMaterializationStatus.COMPLETED
    assert result.summary.created_claim_count == 1


def test_execution_result_envelope_scope_mismatch_is_rejected() -> None:
    draft = _draft()
    execution_result = MethodologyExtractionExecutionResult(
        tenant_id=OTHER_TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=MethodologyExtractionExecutionStatus.COMPLETED,
        task_results=[],
        accepted_claim_drafts=[draft],
        summary=MethodologyExtractionExecutionSummary(
            tenant_id=OTHER_TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            total_tasks=1,
            executed_tasks=1,
            skipped_tasks=0,
            failed_tasks=0,
            accepted_draft_count=1,
            rejected_draft_count=0,
            by_status={"completed": 1},
            by_reason={},
        ),
    )

    result = _service().materialize(execution_result=execution_result)

    assert result.status == ClaimMaterializationStatus.FAILED
    assert result.rejected_drafts[0].reason == ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH


def test_created_claim_preserves_methodology_and_source_provenance() -> None:
    claim_service = ClaimService(tenant_id=TENANT_ID, db_conn=None, audit_sink=InMemoryAuditSink())
    draft = _draft()
    result = _materialize([draft], claim_service)
    claim = claim_service.get(result.draft_claim_mappings[0].claim_id)

    assert claim["deal_id"] == DEAL_ID
    assert claim["claim_class"] == "FINANCIAL"
    assert claim["claim_text"] == "Revenue was $10M in FY2024."
    assert claim["predicate"] == "revenue"
    assert claim["value"] == {"value": 10_000_000, "unit": "USD"}
    assert claim["primary_span_id"] == "span-001"
    assert claim["ic_bound"] is False
    assert claim["sanad_id"] is None
    assert claim["claim_grade"] == "D"
    assert claim["claim_verdict"] == "UNVERIFIED"
    assert claim["claim_action"] == "VERIFY"

    corroboration = claim["corroboration"]
    assert corroboration["methodology_claim_draft_id"] == draft.methodology_claim_draft_id
    assert corroboration["extraction_task_id"] == "et_revenue_quality"
    assert corroboration["methodology_id"] == "financial_dd"
    assert corroboration["methodology_version_id"] == "financial_dd_v1"
    assert corroboration["methodology_question_id"] == "mq_financial_dd_revenue_quality_0001"
    assert corroboration["document_id"] == "doc-financial-model"
    assert corroboration["source_span_ids"] == ["span-001"]
    assert corroboration["source_span_metadata"] == [
        {
            "span_id": "span-001",
            "document_id": "doc-financial-model",
            "locator": {"sheet": "P&L", "cell": "A1"},
            "evidence_tags": ["schedule"],
        }
    ]
    assert corroboration["extraction_confidence"] == "0.97"
    assert corroboration["dhabt_score"] == "0.95"
    assert corroboration["sanad_status"] == "deferred"
    assert corroboration["coverage_status"] == "deferred"


def test_corroboration_is_rebuilt_from_typed_fields_not_preview() -> None:
    claim_service = ClaimService(tenant_id=TENANT_ID, db_conn=None, audit_sink=InMemoryAuditSink())
    draft = _draft()
    draft.future_claim_input_preview["raw_text"] = "must not persist"

    result = _materialize([draft], claim_service)
    claim = claim_service.get(result.draft_claim_mappings[0].claim_id)

    assert claim["deal_id"] == DEAL_ID
    assert claim["claim_class"] == "FINANCIAL"
    assert claim["claim_text"] == "Revenue was $10M in FY2024."
    assert claim["corroboration"]["methodology_id"] == "financial_dd"
    assert "prior_preview" not in claim["corroboration"]
    assert "raw_text" not in claim["corroboration"]


def test_stale_or_invalid_draft_id_is_rejected() -> None:
    payload = _draft().model_dump(mode="python")
    payload["methodology_claim_draft_id"] = "mcd_stale"
    stale = MethodologyClaimDraft.model_validate(payload)

    result = _materialize([stale])

    assert result.summary.rejected_draft_count == 1
    assert result.rejected_drafts[0].reason == ClaimMaterializationReason.STALE_OR_INVALID_DRAFT_ID


def test_tenant_deal_run_and_claim_service_context_mismatches_are_rejected() -> None:
    mismatched_draft = _draft(tenant_id=OTHER_TENANT_ID)
    result = _materialize([mismatched_draft])
    assert result.rejected_drafts[0].reason == ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH

    wrong_service = ClaimService(
        tenant_id=OTHER_TENANT_ID,
        db_conn=None,
        audit_sink=InMemoryAuditSink(),
    )
    result = _materialize([_draft()], wrong_service)
    assert result.rejected_drafts[0].reason == ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH


def test_claim_service_tenant_mismatch_with_zero_drafts_fails_closed() -> None:
    wrong_service = ClaimService(
        tenant_id=OTHER_TENANT_ID,
        db_conn=None,
        audit_sink=InMemoryAuditSink(),
    )

    result = _materialize([], wrong_service)

    assert result.status == ClaimMaterializationStatus.FAILED
    assert result.summary.total_drafts == 0
    assert result.summary.created_claim_count == 0
    assert result.summary.rejected_draft_count == 1
    assert result.rejected_drafts[0].reason == ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH


def test_zero_drafts_and_zero_rejections_is_deterministic_noop_completed() -> None:
    result = _materialize([])

    assert result.status == ClaimMaterializationStatus.COMPLETED
    assert result.summary.total_drafts == 0
    assert result.summary.created_claim_count == 0
    assert result.summary.rejected_draft_count == 0
    assert result.summary.by_status == {}
    assert result.summary.by_reason == {}
    assert result.to_deterministic_json() == result.to_deterministic_json()


def test_some_mappings_and_some_rejections_returns_partial() -> None:
    draft = _draft()
    duplicate = _draft()

    result = _materialize([draft, duplicate])

    assert result.status == ClaimMaterializationStatus.PARTIAL
    assert result.summary.created_claim_count == 1
    assert result.summary.rejected_draft_count == 1
    assert result.rejected_drafts[0].reason == ClaimMaterializationReason.DUPLICATE_DRAFT_ID


def test_no_mappings_and_any_rejection_returns_failed() -> None:
    result = _materialize([_draft(confidence=Decimal("0.80"))])

    assert result.status == ClaimMaterializationStatus.FAILED
    assert result.summary.created_claim_count == 0
    assert result.summary.rejected_draft_count == 1
    assert result.rejected_drafts[0].reason == (
        ClaimMaterializationReason.BELOW_CONFIDENCE_THRESHOLD
    )


def test_source_span_metadata_must_match_span_ids_and_document_id() -> None:
    multi_span_payload = _draft().model_dump(mode="python")
    multi_span_payload["methodology_claim_draft_id"] = None
    multi_span_payload["source_span_ids"] = ["span-001", "span-002"]
    incomplete_multi_span = MethodologyClaimDraft.model_validate(multi_span_payload)
    cases = [
        _draft(source_span_metadata=[]),
        incomplete_multi_span,
        _draft(
            source_span_metadata=[
                {"span_id": "span-999", "document_id": "doc-financial-model"}
            ]
        ),
        _draft(source_span_metadata=[{"span_id": "span-001", "document_id": "doc-other"}]),
        _draft(
            source_span_metadata=[
                {
                    "span_id": "span-001",
                    "document_id": "doc-financial-model",
                    "text_excerpt": "Revenue was $10M in FY2024.",
                }
            ]
        ),
    ]

    for draft in cases:
        result = _materialize([draft])
        assert result.rejected_drafts[0].reason == (
            ClaimMaterializationReason.SOURCE_SPAN_METADATA_MISMATCH
        )


def test_gate_and_malformed_draft_failures_are_rejected() -> None:
    malformed = _draft().model_copy(update={"value": {}})
    missing_confidence = _draft().model_copy(update={"extraction_confidence": None})
    missing_source_span = _draft().model_copy(update={"source_span_ids": []})
    missing_linkage = _draft().model_copy(update={"methodology_question_id": ""})
    missing_draft_id = _draft().model_copy(update={"methodology_claim_draft_id": None})
    cases = [
        (_draft(confidence=Decimal("0.80")), ClaimMaterializationReason.BELOW_CONFIDENCE_THRESHOLD),
        (_draft(dhabt=Decimal("0.70")), ClaimMaterializationReason.BELOW_DHABT_THRESHOLD),
        (missing_confidence, ClaimMaterializationReason.MISSING_GATE_METADATA),
        (malformed, ClaimMaterializationReason.MALFORMED_CLAIM_DRAFT),
        (missing_source_span, ClaimMaterializationReason.MISSING_SOURCE_SPAN),
        (missing_linkage, ClaimMaterializationReason.MISSING_METHODOLOGY_LINKAGE),
        (missing_draft_id, ClaimMaterializationReason.STALE_OR_INVALID_DRAFT_ID),
    ]

    for draft, reason in cases:
        result = _materialize([draft])
        assert result.rejected_drafts[0].reason == reason


def test_duplicate_draft_ids_are_rejected() -> None:
    draft = _draft()
    result = _materialize([draft, draft])

    assert result.summary.created_claim_count == 1
    assert result.summary.rejected_draft_count == 1
    assert result.rejected_drafts[0].reason == ClaimMaterializationReason.DUPLICATE_DRAFT_ID


def test_claim_service_create_failure_is_per_draft_and_continues() -> None:
    claim_service = FailingClaimService(
        tenant_id=TENANT_ID,
        db_conn=None,
        audit_sink=InMemoryAuditSink(),
    )
    failing = _draft(claim_text="fail this draft")
    succeeding = _draft(span_id="span-002", claim_text="Revenue was $11M in FY2025.")

    result = _materialize([failing, succeeding], claim_service)

    assert result.status == ClaimMaterializationStatus.PARTIAL
    assert result.summary.created_claim_count == 1
    assert result.summary.rejected_draft_count == 1
    assert result.rejected_drafts[0].reason == (
        ClaimMaterializationReason.CLAIM_SERVICE_CREATE_FAILED
    )
    assert result.draft_claim_mappings[0].methodology_claim_draft_id == (
        succeeding.methodology_claim_draft_id
    )


def test_materializer_does_not_import_disallowed_integrations() -> None:
    import idis.services.extraction.claim_materializer as claim_materializer

    source = inspect.getsource(claim_materializer)
    forbidden = [
        "ClaimsRepository",
        "SanadService",
        "InMemoryMethodologyCoverageService",
        "sqlalchemy",
        "FastAPI",
        "APIRouter",
        "neo4j",
        "redis",
        "pgvector",
        "LLMClaimExtractor",
        "Anthropic",
        "OpenAI",
        "requests",
        "httpx",
    ]

    for token in forbidden:
        assert token not in source
