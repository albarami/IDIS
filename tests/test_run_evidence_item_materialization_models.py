"""Tests for Slice 7 EvidenceItem source-provenance materialization models."""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from idis.models.claim_materialization import MaterializedClaimSourceRef
from idis.models.evidence_item import EvidenceItem, SourceGrade, VerificationStatus
from idis.models.evidence_item_materialization import (
    EvidenceItemMaterializationReason,
    EvidenceItemMaterializationStatus,
    MethodologyEvidenceItemMapping,
    MethodologyEvidenceItemMaterializationRunResult,
    MethodologyEvidenceItemMaterializationSummary,
    MethodologyEvidenceItemRejection,
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceItemShell,
    RunScopedEvidenceProvenanceRef,
    evidence_item_source_span_id,
    generate_methodology_evidence_item_id,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _source_ref(
    *,
    document_id: str = "doc-financial-model",
    source_span_id: str = "44444444-4444-4444-4444-444444444444",
) -> RunScopedEvidenceProvenanceRef:
    return RunScopedEvidenceProvenanceRef(
        document_id=document_id,
        source_span_id=source_span_id,
        locator={"sheet": "P&L", "cell": "B12"},
    )


def _evidence_id(source_ref: MaterializedClaimSourceRef | None = None) -> str:
    return generate_methodology_evidence_item_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id="claim_mth_revenue",
        extraction_output_id="meo_revenue",
        extraction_task_id="et_revenue",
        methodology_question_id="mq_revenue",
        coverage_record_id="mcr_revenue",
        source_ref=source_ref or _source_ref(),
    )


def _evidence_item(source_ref: RunScopedEvidenceProvenanceRef | None = None) -> EvidenceItem:
    ref = source_ref or _source_ref()
    return EvidenceItem(
        evidence_id=_evidence_id(ref),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        source_span_id=evidence_item_source_span_id(ref.source_span_id),
        source_system="methodology_claim_materialization",
        upstream_origin_id=ref.source_span_id,
        verification_status=VerificationStatus.UNVERIFIED,
        source_grade=SourceGrade.D,
        rationale={
            "claim_id": "claim_mth_revenue",
            "run_id": RUN_ID,
            "methodology_question_id": "mq_revenue",
            "coverage_record_id": "mcr_revenue",
            "extraction_task_id": "et_revenue",
            "extraction_output_id": "meo_revenue",
            "document_id": ref.document_id,
            "source_span_id": ref.source_span_id,
            "source": "slice_7_methodology_source_provenance",
        },
    )


def _record(
    source_ref: RunScopedEvidenceProvenanceRef | None = None,
) -> RunScopedEvidenceItemRecord:
    ref = source_ref or _source_ref()
    return RunScopedEvidenceItemRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id="claim_mth_revenue",
        evidence_item=_evidence_item(ref),
        source_ref=ref,
        methodology_question_id="mq_revenue",
        coverage_record_id="mcr_revenue",
        extraction_task_id="et_revenue",
        extraction_output_id="meo_revenue",
        status="materialized_unverified",
    )


def test_slice7_inventory_reuses_existing_evidence_item_concepts() -> None:
    evidence = _evidence_item()

    assert isinstance(evidence, EvidenceItem)
    assert evidence.verification_status == VerificationStatus.UNVERIFIED
    assert evidence.source_grade == SourceGrade.D
    assert EvidenceItemMaterializationReason.MISSING_CLAIM_ID.value == "missing_claim_id"


def test_deterministic_evidence_id_is_uuid_v5_stable_and_changes_with_source_ref() -> None:
    source_ref = _source_ref()
    same = _evidence_id(source_ref)
    changed = _evidence_id(_source_ref(source_span_id="55555555-5555-5555-5555-555555555555"))

    parsed = UUID(same)

    assert parsed.version == 5
    assert same == _evidence_id(source_ref)
    assert changed != same


def test_evidence_item_uses_conservative_ungraded_defaults() -> None:
    evidence = _evidence_item()

    assert evidence.source_grade == SourceGrade.D
    assert evidence.verification_status == VerificationStatus.UNVERIFIED
    assert evidence.created_at is None
    assert evidence.updated_at is None


def test_evidence_item_source_span_id_keeps_only_uuid_values() -> None:
    uuid_source_ref = _source_ref(source_span_id="44444444-4444-4444-4444-444444444444")
    safe_non_uuid_source_ref = _source_ref(source_span_id="span-001")

    assert evidence_item_source_span_id(uuid_source_ref.source_span_id) == (
        "44444444-4444-4444-4444-444444444444"
    )
    assert evidence_item_source_span_id(safe_non_uuid_source_ref.source_span_id) is None

    evidence = _evidence_item(safe_non_uuid_source_ref)
    assert evidence.source_span_id is None
    assert evidence.rationale is not None
    assert evidence.rationale["source_span_id"] == "span-001"


def test_provenance_ref_reuses_materialized_claim_source_ref_safety() -> None:
    safe = RunScopedEvidenceProvenanceRef(
        document_id="doc-financial-model",
        source_span_id="span-001",
        locator={"sheet": "P&L"},
    )

    assert isinstance(safe, MaterializedClaimSourceRef)

    with pytest.raises(ValidationError):
        RunScopedEvidenceProvenanceRef(
            document_id="C:/secret/file.pdf",
            source_span_id="span-001",
            locator={"sheet": "P&L"},
        )
    with pytest.raises(ValidationError):
        RunScopedEvidenceProvenanceRef(
            document_id="doc-financial-model",
            source_span_id="span-001",
            locator={"raw_text": "Revenue was $10M"},
        )


def test_record_shell_and_mapping_keep_safe_source_ref_but_no_locator() -> None:
    record = _record(_source_ref(source_span_id="span-001"))
    shell = record.to_shell()
    mapping = MethodologyEvidenceItemMapping.from_record(record)

    assert isinstance(shell, RunScopedEvidenceItemShell)
    assert shell.source_span_id == "span-001"
    assert mapping.source_span_id == "span-001"
    assert "locator" not in mapping.model_dump(mode="json")


def test_run_step_summary_excludes_raw_fields_and_keeps_safe_ids() -> None:
    record = _record(_source_ref(source_span_id="span-001"))
    mapping = MethodologyEvidenceItemMapping.from_record(record)
    rejection = MethodologyEvidenceItemRejection(
        claim_id="claim_mth_bad",
        reason=EvidenceItemMaterializationReason.MISSING_SOURCE_REFS,
        reason_codes=[EvidenceItemMaterializationReason.MISSING_SOURCE_REFS.value],
        message="missing_source_refs",
    )
    run_result = MethodologyEvidenceItemMaterializationRunResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=EvidenceItemMaterializationStatus.PARTIAL,
        evidence_item_mappings=[mapping],
        rejected_source_refs=[rejection],
        summary=MethodologyEvidenceItemMaterializationSummary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            total_claims=2,
            total_source_refs=1,
            created_evidence_count=1,
            rejected_source_ref_count=1,
            by_status={"completed": 1, "rejected": 1},
            by_reason={EvidenceItemMaterializationReason.MISSING_SOURCE_REFS.value: 1},
        ),
    )

    summary = run_result.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["evidence_ids"] == [record.evidence_item.evidence_id]
    assert summary["claim_ids"] == ["claim_mth_revenue"]
    assert "doc-financial-model" in serialized
    assert "span-001" in serialized
    assert "locator" not in serialized
    assert "claim_text" not in serialized
    assert "value_struct" not in serialized
    assert "raw_text" not in serialized
    assert "document_name" not in serialized
    assert "C:/secret" not in serialized
