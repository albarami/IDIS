"""Tests for Slice 8 in-memory Sanad creation/linking/grading service."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

from idis.models.claim import Materiality
from idis.models.claim_materialization import (
    MaterializedClaimSourceRef,
    MaterializedClaimType,
    MaterializedClaimValueStruct,
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.defect import Defect, DefectSeverity
from idis.models.evidence_item import EvidenceItem, SourceGrade, VerificationStatus
from idis.models.evidence_item_materialization import (
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceItemShell,
    RunScopedEvidenceProvenanceRef,
)
from idis.models.sanad import CorroborationStatus
from idis.models.sanad_materialization import (
    MethodologySanadMaterializationStatus,
    MethodologySanadReason,
)
from idis.models.value_structs import ValueStructType
from idis.services.runs.methodology_sanad_creation_linking_grading import (
    InMemoryRunMethodologySanadCreationLinkingGradingService,
)
from idis.services.sanad.grader import DefectSummary

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _source_ref(source_span_id: str = "span-001") -> MaterializedClaimSourceRef:
    return MaterializedClaimSourceRef(
        document_id="doc-001",
        source_span_id=source_span_id,
        locator={"sheet_id": "sheet-001", "cell_id": "B2"},
    )


def _claim(
    *,
    claim_id: str = "claim_mth_revenue",
    tenant_id: str = TENANT_ID,
    run_id: str = RUN_ID,
    extraction_output_id: str = "meo_revenue",
) -> RunScopedMaterializedClaim:
    return RunScopedMaterializedClaim(
        claim_id=claim_id,
        tenant_id=tenant_id,
        deal_id=DEAL_ID,
        run_id=run_id,
        claim_text="revenue: 10000000 USD",
        claim_type=MaterializedClaimType.FINANCIAL_METRIC,
        value_struct=MaterializedClaimValueStruct(
            type=ValueStructType.MONETARY,
            value=Decimal("10000000"),
            unit="USD",
            currency="USD",
            time_window="FY2024",
            source_answer_type="number",
        ),
        materiality=Materiality.HIGH,
        source_refs=[_source_ref()],
        methodology_id="m_cdd_fdd",
        methodology_version_id="mv_1",
        methodology_question_id="mq_revenue",
        coverage_record_id="mcr_revenue",
        extraction_task_id="et_revenue",
        extraction_output_id=extraction_output_id,
        status="accepted",
    )


def _claim_shell() -> RunScopedMaterializedClaimShell:
    claim = _claim()
    return RunScopedMaterializedClaimShell(
        claim_id=claim.claim_id or "claim_mth_revenue",
        tenant_id=claim.tenant_id,
        deal_id=claim.deal_id,
        run_id=claim.run_id,
        source_refs=claim.source_refs,
        methodology_question_id=claim.methodology_question_id,
        coverage_record_id=claim.coverage_record_id,
        extraction_task_id=claim.extraction_task_id,
        extraction_output_id=claim.extraction_output_id,
        status=claim.status,
    )


def _evidence(
    *,
    evidence_id: str = "evidence-001",
    claim_id: str = "claim_mth_revenue",
    tenant_id: str = TENANT_ID,
    run_id: str = RUN_ID,
    document_id: str = "doc-001",
    source_span_id: str = "span-001",
) -> RunScopedEvidenceItemRecord:
    source_ref = RunScopedEvidenceProvenanceRef(
        document_id=document_id,
        source_span_id=source_span_id,
        locator={"sheet_id": "sheet-001", "cell_id": "B2"},
    )
    return RunScopedEvidenceItemRecord(
        tenant_id=tenant_id,
        deal_id=DEAL_ID,
        run_id=run_id,
        claim_id=claim_id,
        evidence_item=EvidenceItem(
            evidence_id=evidence_id,
            tenant_id=tenant_id,
            deal_id=DEAL_ID,
            source_span_id=None,
            source_system="methodology_source_ref",
            upstream_origin_id=document_id,
            verification_status=VerificationStatus.UNVERIFIED,
            source_grade=SourceGrade.D,
        ),
        source_ref=source_ref,
        methodology_question_id="mq_revenue",
        coverage_record_id="mcr_revenue",
        extraction_task_id="et_revenue",
        extraction_output_id="meo_revenue",
        status="created",
    )


def _service() -> InMemoryRunMethodologySanadCreationLinkingGradingService:
    return InMemoryRunMethodologySanadCreationLinkingGradingService()


def test_creates_links_and_grades_one_sanad_from_claim_and_evidence() -> None:
    result, sanads, links, grades, defects = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim()],
        evidence_items=[_evidence()],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(
                document_id="doc-001",
                source_span_id="span-001",
                locator=None,
            )
        ],
    )

    summary_json = json.dumps(result.to_run_step_summary(), sort_keys=True)

    assert result.status == MethodologySanadMaterializationStatus.COMPLETED
    assert len(sanads) == 1
    assert len(links) == 1
    assert len(grades) == 1
    assert all(isinstance(defect.defect, Defect) for defect in defects)
    assert result.summary.by_status == {"created_linked_graded": 1}
    assert result.summary.by_grade == {grades[0].sanad_grade.value: 1}
    assert result.summary.by_status != result.summary.by_grade
    assert sanads[0].sanad.claim_id == "claim_mth_revenue"
    assert sanads[0].sanad.primary_evidence_id == "evidence-001"
    assert links[0].claim_link_status == "linked_run_scoped"
    assert grades[0].sanad_grade.value in {"A", "B", "C", "D"}
    assert "claim_text" not in summary_json
    assert "value_struct" not in summary_json
    assert "sheet-001" not in summary_json


def test_deterministic_chain_ids_timestamps_and_id_only_refs_are_stable() -> None:
    first = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim()],
        evidence_items=[_evidence()],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(document_id="doc-001", source_span_id="span-001")
        ],
    )
    second = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim()],
        evidence_items=[_evidence()],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(document_id="doc-001", source_span_id="span-001")
        ],
    )

    first_chain = first[1][0].sanad.transmission_chain
    second_chain = second[1][0].sanad.transmission_chain

    assert [node.node_id for node in first_chain] == [node.node_id for node in second_chain]
    assert [node.timestamp for node in first_chain] == [node.timestamp for node in second_chain]
    for node in first_chain:
        refs = node.input_refs + node.output_refs
        assert refs
        assert all(
            set(ref) <= {"claim_id", "evidence_id", "source_span_id", "sanad_id"} for ref in refs
        )
        assert "doc-001" not in json.dumps(refs)


def test_multiple_evidence_items_are_corroborating_and_duplicate_inputs_are_deduped() -> None:
    result, sanads, links, grades, defects = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim(), _claim()],
        evidence_items=[_evidence(), _evidence(evidence_id="evidence-002")],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(document_id="doc-001", source_span_id="span-001")
        ],
    )

    assert result.status == MethodologySanadMaterializationStatus.COMPLETED
    assert len(sanads) == 1
    assert sanads[0].sanad.primary_evidence_id == "evidence-001"
    assert sanads[0].sanad.corroborating_evidence_ids == ["evidence-002"]
    assert len(links) == 1
    assert len(grades) == 1
    assert all(isinstance(defect.defect, Defect) for defect in defects)


def test_evidence_source_span_missing_from_source_provenance_fails_closed() -> None:
    result, sanads, links, grades, defects = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim()],
        evidence_items=[_evidence(source_span_id="span-absent-from-provenance")],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(document_id="doc-001", source_span_id="span-001")
        ],
    )

    assert result.status == MethodologySanadMaterializationStatus.FAILED
    assert result.rejections[0].reason == MethodologySanadReason.MISSING_SOURCE_PROVENANCE
    assert result.summary.by_status == {"rejected": 1}
    assert sanads == []
    assert links == []
    assert grades == []
    assert defects == []


def test_three_independent_evidence_sources_are_mutawatir() -> None:
    result, sanads, *_ = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim()],
        evidence_items=[
            _evidence(evidence_id="evidence-001", document_id="doc-001", source_span_id="span-001"),
            _evidence(evidence_id="evidence-002", document_id="doc-002", source_span_id="span-002"),
            _evidence(evidence_id="evidence-003", document_id="doc-003", source_span_id="span-003"),
        ],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(document_id="doc-001", source_span_id="span-001"),
            RunScopedEvidenceProvenanceRef(document_id="doc-002", source_span_id="span-002"),
            RunScopedEvidenceProvenanceRef(document_id="doc-003", source_span_id="span-003"),
        ],
    )

    assert result.status == MethodologySanadMaterializationStatus.COMPLETED
    assert sanads[0].sanad.corroboration_status == CorroborationStatus.MUTAWATIR


def test_safe_claim_and_evidence_shells_work_without_full_payloads() -> None:
    evidence = _evidence().to_shell()
    shell = RunScopedEvidenceItemShell(
        tenant_id=evidence.tenant_id,
        deal_id=evidence.deal_id,
        run_id=evidence.run_id,
        claim_id=evidence.claim_id,
        evidence_id=evidence.evidence_id,
        document_id=evidence.document_id,
        source_span_id=evidence.source_span_id,
        methodology_question_id=evidence.methodology_question_id,
        coverage_record_id=evidence.coverage_record_id,
        extraction_task_id=evidence.extraction_task_id,
        extraction_output_id=evidence.extraction_output_id,
        status=evidence.status,
    )

    result, sanads, *_ = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim_shell()],
        evidence_items=[shell],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(document_id="doc-001", source_span_id="span-001")
        ],
    )

    assert result.status == MethodologySanadMaterializationStatus.COMPLETED
    assert sanads[0].claim_id == "claim_mth_revenue"


def test_missing_evidence_and_scope_mismatch_fail_closed() -> None:
    missing_result, sanads, *_ = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim()],
        evidence_items=[],
        source_provenance=[],
    )
    mismatch_result, *_ = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim(tenant_id="other-tenant")],
        evidence_items=[_evidence()],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(document_id="doc-001", source_span_id="span-001")
        ],
    )

    assert sanads == []
    assert missing_result.status == MethodologySanadMaterializationStatus.FAILED
    assert missing_result.rejections[0].reason == MethodologySanadReason.MISSING_EVIDENCE_ITEMS
    assert mismatch_result.status == MethodologySanadMaterializationStatus.FAILED
    assert mismatch_result.rejections[0].reason == MethodologySanadReason.TENANT_OR_RUN_MISMATCH


def test_unmapped_grader_defect_code_fails_closed_without_defect_record(monkeypatch) -> None:
    def fake_grade_sanad_v2(**kwargs):
        return SimpleNamespace(
            grade="D",
            explanation=SimpleNamespace(
                to_dict=lambda: {"summary": "verbose unsupported defect explanation"}
            ),
            all_defects=[
                DefectSummary(
                    code="UNMAPPED_GRADER_DEFECT",
                    severity=DefectSeverity.FATAL.value,
                    description="unsupported grader defect",
                )
            ],
        )

    monkeypatch.setattr(
        "idis.services.runs.methodology_sanad_creation_linking_grading.grade_sanad_v2",
        fake_grade_sanad_v2,
    )

    result, sanads, links, grades, defects = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim()],
        evidence_items=[_evidence()],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(document_id="doc-001", source_span_id="span-001")
        ],
    )

    summary_json = json.dumps(result.to_run_step_summary(), sort_keys=True)

    assert result.status == MethodologySanadMaterializationStatus.FAILED
    assert result.rejections[0].reason == MethodologySanadReason.DEFECT_MATERIALIZATION_FAILED
    assert sanads == []
    assert links == []
    assert grades == []
    assert defects == []
    assert "unsupported grader defect" not in summary_json
    assert "verbose unsupported defect explanation" not in summary_json


def test_explicit_empty_inputs_return_diagnostic_noop() -> None:
    result, sanads, links, grades, defects = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[],
        evidence_items=[],
        source_provenance=[],
    )

    assert result.status == MethodologySanadMaterializationStatus.COMPLETED
    assert sanads == []
    assert links == []
    assert grades == []
    assert defects == []
    assert result.summary.created_sanad_count == 0
