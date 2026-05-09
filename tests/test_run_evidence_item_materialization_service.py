"""Tests for Slice 7 run-scoped EvidenceItem materialization service."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from idis.models.claim import Materiality
from idis.models.claim_materialization import (
    MaterializedClaimSourceRef,
    MaterializedClaimType,
    MaterializedClaimValueStruct,
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.evidence_item import SourceGrade, VerificationStatus
from idis.models.evidence_item_materialization import (
    EvidenceItemMaterializationReason,
    EvidenceItemMaterializationStatus,
)
from idis.models.value_structs import ValueStructType
from idis.persistence.repositories.claims import InMemoryEvidenceRepository
from idis.services.runs.methodology_evidence_item_materialization import (
    InMemoryRunMethodologyEvidenceItemMaterializationService,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _source_ref(
    *,
    document_id: str = "doc-financial-model",
    source_span_id: str = "span-001",
) -> MaterializedClaimSourceRef:
    return MaterializedClaimSourceRef(
        document_id=document_id,
        source_span_id=source_span_id,
        locator={"sheet": "P&L", "cell": "B12"},
    )


def _claim(
    *,
    claim_id: str | None = "claim_mth_revenue",
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
    source_refs: list[MaterializedClaimSourceRef] | None = None,
) -> RunScopedMaterializedClaim:
    return RunScopedMaterializedClaim(
        claim_id=claim_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        claim_text="revenue: 10000000 USD",
        claim_type=MaterializedClaimType.FINANCIAL_METRIC,
        value_struct=MaterializedClaimValueStruct(
            type=ValueStructType.MONETARY,
            value=Decimal("10000000"),
            unit="USD",
            currency="USD",
            time_window="FY2024",
            source_answer_type="numeric",
        ),
        materiality=Materiality.MEDIUM,
        source_refs=source_refs or [_source_ref()],
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_revenue",
        coverage_record_id="mcr_revenue",
        extraction_task_id="et_revenue",
        extraction_output_id="meo_revenue",
        status="materialized_unverified",
    )


def _shell(
    *,
    claim_id: str = "claim_mth_shell",
    source_refs: list[MaterializedClaimSourceRef] | None = None,
) -> RunScopedMaterializedClaimShell:
    return RunScopedMaterializedClaimShell(
        claim_id=claim_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        source_refs=source_refs or [_source_ref(source_span_id="span-shell")],
        methodology_question_id="mq_revenue",
        coverage_record_id="mcr_revenue",
        extraction_task_id="et_revenue",
        extraction_output_id="meo_revenue",
        status="materialized_unverified",
    )


def _run(
    materialized_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
) -> tuple[Any, Any]:
    return InMemoryRunMethodologyEvidenceItemMaterializationService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=materialized_claims,
    )


def test_materialized_claim_source_ref_becomes_conservative_evidence_item() -> None:
    run_result, records = _run([_claim()])

    assert run_result.status == EvidenceItemMaterializationStatus.COMPLETED
    assert len(records) == 1
    record = records[0]
    assert record.claim_id == "claim_mth_revenue"
    assert record.evidence_item.source_grade == SourceGrade.D
    assert record.evidence_item.verification_status == VerificationStatus.UNVERIFIED
    assert record.evidence_item.created_at is None
    assert record.evidence_item.updated_at is None
    assert record.evidence_item.source_span_id is None
    assert record.source_ref.source_span_id == "span-001"
    assert run_result.summary.created_evidence_count == 1


def test_uuid_source_span_id_is_copied_to_evidence_item() -> None:
    source_ref = _source_ref(source_span_id="44444444-4444-4444-4444-444444444444")

    _run_result, records = _run([_claim(source_refs=[source_ref])])

    assert records[0].evidence_item.source_span_id == "44444444-4444-4444-4444-444444444444"
    assert records[0].source_ref.source_span_id == "44444444-4444-4444-4444-444444444444"


def test_duplicate_claim_source_ref_does_not_create_duplicate_evidence() -> None:
    duplicate = _source_ref(source_span_id="span-001")

    run_result, records = _run([_claim(source_refs=[duplicate, duplicate])])

    assert run_result.status == EvidenceItemMaterializationStatus.PARTIAL
    assert len(records) == 1
    assert run_result.summary.created_evidence_count == 1
    assert run_result.summary.rejected_source_ref_count == 1
    assert run_result.rejected_source_refs[0].reason == (
        EvidenceItemMaterializationReason.DUPLICATE_CLAIM_SOURCE_REF
    )


def test_materialized_claim_shell_input_creates_evidence_item() -> None:
    run_result, records = _run([_shell()])

    assert run_result.status == EvidenceItemMaterializationStatus.COMPLETED
    assert len(records) == 1
    assert records[0].claim_id == "claim_mth_shell"
    assert records[0].source_ref.source_span_id == "span-shell"


def test_explicit_empty_materialized_claim_list_is_completed_noop() -> None:
    run_result, records = _run([])

    assert run_result.status == EvidenceItemMaterializationStatus.COMPLETED
    assert records == []
    assert run_result.summary.total_claims == 0
    assert run_result.summary.created_evidence_count == 0


def test_missing_claim_id_rejects_without_evidence() -> None:
    malformed = _claim().model_copy(update={"claim_id": None})

    run_result, records = _run([malformed])

    assert run_result.status == EvidenceItemMaterializationStatus.FAILED
    assert records == []
    assert run_result.rejected_source_refs[0].reason == (
        EvidenceItemMaterializationReason.MISSING_CLAIM_ID
    )


def test_tenant_deal_run_mismatch_rejects_without_evidence() -> None:
    mismatched = _claim(run_id="44444444-4444-4444-4444-444444444444")

    run_result, records = _run([mismatched])

    assert run_result.status == EvidenceItemMaterializationStatus.FAILED
    assert records == []
    assert run_result.rejected_source_refs[0].reason == (
        EvidenceItemMaterializationReason.TENANT_OR_RUN_MISMATCH
    )


def test_safe_run_step_summary_excludes_forbidden_payloads() -> None:
    run_result, _records = _run([_claim()])
    summary = run_result.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)

    assert "evidence_ids" in summary
    assert "claim_mth_revenue" in serialized
    assert "doc-financial-model" in serialized
    assert "span-001" in serialized
    assert "locator" not in serialized
    assert "claim_text" not in serialized
    assert "revenue: 10000000 USD" not in serialized
    assert "value_struct" not in serialized
    assert "answer" not in serialized
    assert "raw_text" not in serialized
    assert "document_name" not in serialized
    assert "C:/secret" not in serialized
    assert "sanad" not in serialized.lower()
    assert "truth_dashboard" not in serialized
    assert "calc_ids" not in serialized
    assert "deliverables" not in serialized


def test_service_does_not_call_existing_repositories_or_downstream_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("Slice 7 service must not call InMemoryEvidenceRepository.create")

    monkeypatch.setattr(InMemoryEvidenceRepository, "create", forbidden_create)

    run_result, records = _run([_claim()])

    assert run_result.status == EvidenceItemMaterializationStatus.COMPLETED
    assert len(records) == 1
