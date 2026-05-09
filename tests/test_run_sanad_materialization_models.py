"""Tests for Slice 8 run-scoped Sanad materialization models."""

from __future__ import annotations

import json
from datetime import UTC
from uuid import UUID

from idis.models.defect import CureProtocol, Defect, DefectSeverity, DefectStatus, DefectType
from idis.models.sanad import CorroborationStatus, Sanad, SanadGrade
from idis.models.sanad_materialization import (
    MethodologySanadMapping,
    MethodologySanadMaterializationRunResult,
    MethodologySanadMaterializationStatus,
    MethodologySanadMaterializationSummary,
    MethodologySanadReason,
    RunScopedSanadDefectRecord,
    RunScopedSanadGradeRecord,
    RunScopedSanadLinkRecord,
    RunScopedSanadRecord,
    deterministic_sanad_id,
    deterministic_sanad_node_id,
    deterministic_sanad_timestamp,
)
from idis.models.transmission_node import ActorType, NodeType, TransmissionNode

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _sanad_id(evidence_ids: list[str] | None = None) -> str:
    return deterministic_sanad_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id="claim_mth_revenue",
        evidence_ids=evidence_ids or ["evidence-001"],
        source_span_ids=["span-001"],
        extraction_output_id="meo_revenue",
        extraction_task_id="et_revenue",
        methodology_question_id="mq_revenue",
        coverage_record_id="mcr_revenue",
    )


def _node(sanad_id: str) -> TransmissionNode:
    return TransmissionNode(
        node_id=deterministic_sanad_node_id(
            sanad_id=sanad_id,
            node_type=NodeType.INGEST.value,
            ordinal=0,
            input_refs=[{"evidence_id": "evidence-001"}],
            output_refs=[{"evidence_id": "evidence-001"}],
        ),
        node_type=NodeType.INGEST,
        actor_type=ActorType.SYSTEM,
        actor_id="slice_8_sanad_adapter",
        input_refs=[{"evidence_id": "evidence-001"}],
        output_refs=[{"evidence_id": "evidence-001"}],
        timestamp=deterministic_sanad_timestamp(0),
        confidence=0.9,
    )


def _defect(sanad_id: str) -> Defect:
    return Defect(
        defect_id=str(
            UUID(
                "aaaaaaaa-aaaa-5aaa-aaaa-aaaaaaaaaaaa",
                version=5,
            )
        ),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        defect_type=DefectType.BROKEN_CHAIN,
        severity=DefectSeverity.MAJOR,
        detected_by="slice_8_sanad_adapter",
        description="deterministic BROKEN_CHAIN defect",
        evidence_refs=[{"evidence_id": "evidence-001"}],
        cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
        status=DefectStatus.OPEN,
        affected_claim_ids=["claim_mth_revenue"],
        timestamp=deterministic_sanad_timestamp(2),
    )


def _sanad() -> Sanad:
    sanad_id = _sanad_id()
    return Sanad(
        sanad_id=sanad_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claim_id="claim_mth_revenue",
        primary_evidence_id="evidence-001",
        corroborating_evidence_ids=[],
        extraction_confidence=0.9,
        dhabt_score=0.9,
        corroboration_status=CorroborationStatus.AHAD_1,
        sanad_grade=SanadGrade.C,
        grade_explanation=[{"summary": "verbose internal explanation only"}],
        transmission_chain=[_node(sanad_id)],
        defects=[_defect(sanad_id)],
    )


def _record() -> RunScopedSanadRecord:
    sanad = _sanad()
    return RunScopedSanadRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=sanad.claim_id,
        sanad=sanad,
        evidence_ids=["evidence-001"],
        source_span_ids=["span-001"],
        methodology_question_id="mq_revenue",
        coverage_record_id="mcr_revenue",
        extraction_task_id="et_revenue",
        extraction_output_id="meo_revenue",
        status="created_linked_graded",
    )


def test_slice8_inventory_reuses_existing_sanad_models() -> None:
    record = _record()

    assert isinstance(record.sanad, Sanad)
    assert isinstance(record.sanad.transmission_chain[0], TransmissionNode)
    assert isinstance(record.sanad.defects[0], Defect)
    assert MethodologySanadReason.MISSING_CLAIM_EVIDENCE.value == "missing_claim_evidence"


def test_deterministic_sanad_ids_and_node_timestamps_are_stable() -> None:
    same = _sanad_id()
    changed = _sanad_id(["evidence-002"])
    node_id = deterministic_sanad_node_id(
        sanad_id=same,
        node_type=NodeType.EXTRACT.value,
        ordinal=1,
        input_refs=[{"evidence_id": "evidence-001"}],
        output_refs=[{"claim_id": "claim_mth_revenue"}],
    )

    assert UUID(same).version == 5
    assert UUID(node_id).version == 5
    assert same == _sanad_id()
    assert changed != same
    assert deterministic_sanad_timestamp(1).tzinfo == UTC
    assert deterministic_sanad_timestamp(1).isoformat() == "1970-01-01T00:00:01+00:00"


def test_shells_exclude_chain_refs_defect_descriptions_and_grade_explanations() -> None:
    record = _record()
    defect_shell = RunScopedSanadDefectRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=record.claim_id,
        sanad_id=record.sanad.sanad_id,
        defect=record.sanad.defects[0],
    ).to_shell()
    shell = record.to_shell(defect_ids=[defect_shell.defect_id])

    shell_dump = shell.model_dump(mode="json")
    defect_dump = defect_shell.model_dump(mode="json")

    assert "transmission_chain" not in shell_dump
    assert "grade_explanation" not in shell_dump
    assert "description" not in defect_dump
    assert shell.defect_ids == [defect_shell.defect_id]


def test_run_step_summary_contains_safe_ids_counts_and_no_forbidden_payloads() -> None:
    record = _record()
    mapping = MethodologySanadMapping.from_record(record, defect_ids=["defect-001"])
    grade = RunScopedSanadGradeRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=record.claim_id,
        sanad_id=record.sanad.sanad_id,
        sanad_grade=SanadGrade.C,
        grade_reason_codes=["base_grade_c"],
        defect_ids=["defect-001"],
        fatal_defect_count=0,
        major_defect_count=1,
        minor_defect_count=0,
    )
    link = RunScopedSanadLinkRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=record.claim_id,
        sanad_id=record.sanad.sanad_id,
        evidence_ids=["evidence-001"],
        source_span_ids=["span-001"],
        claim_link_status="linked_run_scoped",
    )
    run_result = MethodologySanadMaterializationRunResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=MethodologySanadMaterializationStatus.COMPLETED,
        sanad_mappings=[mapping],
        claim_sanad_links=[link],
        grade_records=[grade],
        defect_shells=[],
        rejections=[],
        summary=MethodologySanadMaterializationSummary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            total_claims=1,
            total_evidence_items=1,
            created_sanad_count=1,
            linked_claim_count=1,
            graded_sanad_count=1,
            defect_count=1,
            rejected_count=0,
            by_status={"created": 1},
            by_reason={},
            by_grade={"C": 1},
            by_defect_severity={"MAJOR": 1},
        ),
    )

    summary = run_result.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)

    assert record.sanad.sanad_id in serialized
    assert "claim_mth_revenue" in serialized
    assert "evidence-001" in serialized
    assert "span-001" in serialized
    assert "verbose internal explanation only" not in serialized
    assert "deterministic BROKEN_CHAIN defect" not in serialized
    assert "transmission_chain_node_count" in serialized
    assert "input_refs" not in serialized
    assert "output_refs" not in serialized
    assert "claim_text" not in serialized
    assert "value_struct" not in serialized
    assert "locator" not in serialized
    assert "document_name" not in serialized
    assert "C:/secret" not in serialized
