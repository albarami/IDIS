"""Tests for Slice 9 in-memory methodology deterministic calculation service."""

from __future__ import annotations

import json
from decimal import Decimal

from idis.methodology.models import MethodologyType, RequiredCalculation, RequiredEvidence
from idis.models.calc_materialization import (
    MethodologyCalculationReason,
    MethodologyCalculationStatus,
)
from idis.models.claim import Materiality
from idis.models.claim_materialization import (
    MaterializedClaimSourceRef,
    MaterializedClaimType,
    MaterializedClaimValueStruct,
    RunScopedMaterializedClaim,
)
from idis.models.deterministic_calculation import CalcType
from idis.models.extraction_task import (
    ExpectedAnswerSchema,
    ExtractionTask,
    ExtractionTaskStatus,
    SourceSpanReference,
)
from idis.models.sanad import CorroborationStatus, Sanad, SanadGrade
from idis.models.sanad_materialization import (
    RunScopedSanadGradeRecord,
    RunScopedSanadRecord,
    RunScopedSanadShell,
    deterministic_sanad_node_id,
    deterministic_sanad_timestamp,
)
from idis.models.transmission_node import ActorType, NodeType, TransmissionNode
from idis.models.value_structs import ValueStructType
from idis.services.runs.methodology_deterministic_calculation import (
    InMemoryRunMethodologyDeterministicCalculationService,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _claim(
    claim_id: str,
    label: str,
    value: str,
    *,
    source_answer_type: str = "number",
    materiality: Materiality = Materiality.HIGH,
    methodology_question_id: str = "mq_unit_economics",
    extraction_task_id: str = "et_unit_economics",
    coverage_record_id: str = "mcr_unit_economics",
) -> RunScopedMaterializedClaim:
    return RunScopedMaterializedClaim(
        claim_id=claim_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_text=f"{label}: {value} USD",
        claim_type=MaterializedClaimType.FINANCIAL_METRIC,
        value_struct=MaterializedClaimValueStruct(
            type=ValueStructType.MONETARY,
            value=Decimal(value),
            unit="USD",
            currency="USD",
            time_window="FY2024",
            source_answer_type=source_answer_type,
        ),
        materiality=materiality,
        source_refs=[
            MaterializedClaimSourceRef(
                document_id="doc-001",
                source_span_id=f"span-{label}",
                locator={"cell_id": "B2"},
            )
        ],
        methodology_id="m_cdd_fdd",
        methodology_version_id="mv_1",
        methodology_question_id=methodology_question_id,
        coverage_record_id=coverage_record_id,
        extraction_task_id=extraction_task_id,
        extraction_output_id=f"meo_{label}",
        status="accepted",
    )


def _grade(claim_id: str, grade: SanadGrade = SanadGrade.A) -> RunScopedSanadGradeRecord:
    return RunScopedSanadGradeRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=claim_id,
        sanad_id=f"sanad-{claim_id}",
        sanad_grade=grade,
        grade_reason_codes=[f"grade_{grade.value.lower()}"],
        defect_ids=[],
        fatal_defect_count=0,
        major_defect_count=0,
        minor_defect_count=0,
    )


def _sanad_record(
    claim_id: str,
    *,
    grade: SanadGrade = SanadGrade.A,
    extraction_confidence: float = 0.99,
    dhabt_score: float | None = 0.95,
) -> RunScopedSanadRecord:
    sanad_id = f"sanad-{claim_id}"
    node = TransmissionNode(
        node_id=deterministic_sanad_node_id(
            sanad_id=sanad_id,
            node_type=NodeType.INGEST.value,
            ordinal=0,
            input_refs=[{"claim_id": claim_id}],
            output_refs=[{"claim_id": claim_id}],
        ),
        node_type=NodeType.INGEST,
        actor_type=ActorType.SYSTEM,
        actor_id="slice_9_test",
        input_refs=[{"claim_id": claim_id}],
        output_refs=[{"claim_id": claim_id}],
        timestamp=deterministic_sanad_timestamp(0),
        confidence=0.9,
    )
    return RunScopedSanadRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=claim_id,
        sanad=Sanad(
            sanad_id=sanad_id,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            claim_id=claim_id,
            primary_evidence_id=f"evidence-{claim_id}",
            corroborating_evidence_ids=[],
            extraction_confidence=extraction_confidence,
            dhabt_score=dhabt_score,
            corroboration_status=CorroborationStatus.AHAD_1,
            sanad_grade=grade,
            transmission_chain=[node],
            defects=[],
        ),
        evidence_ids=[f"evidence-{claim_id}"],
        source_span_ids=[f"span-{claim_id}"],
        methodology_question_id="mq_unit_economics",
        coverage_record_id="mcr_unit_economics",
        extraction_task_id="et_unit_economics",
        extraction_output_id=f"meo_{claim_id}",
        status="created_linked_graded",
    )


def _sanad_shell(claim_id: str, grade: SanadGrade = SanadGrade.A) -> RunScopedSanadShell:
    return _sanad_record(claim_id, grade=grade).to_shell()


def _task(
    *,
    calc_type: str = "GROSS_MARGIN",
    required: bool = True,
    methodology_question_id: str = "mq_unit_economics",
    extraction_task_id: str = "et_unit_economics",
    coverage_record_id: str = "mcr_unit_economics",
) -> ExtractionTask:
    required_evidence = [
        RequiredEvidence(evidence_type="financial_support", description="supporting source")
    ]
    return ExtractionTask(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        extraction_task_id=extraction_task_id,
        status=ExtractionTaskStatus.READY,
        reason_codes=["ready"],
        methodology_id="m_cdd_fdd",
        methodology_version_id="mv_1",
        methodology_question_id=methodology_question_id,
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="P&L",
        coverage_record_id=coverage_record_id,
        document_id="doc-001",
        classification_id="classification-001",
        source_spans=[
            SourceSpanReference(
                document_id="doc-001",
                span_id="span-revenue",
                evidence_tags=["financial_support"],
                locator={"cell_id": "B2"},
            )
        ],
        required_evidence=required_evidence,
        expected_answer_schema=ExpectedAnswerSchema(
            answer_type="number",
            question_text="What is gross margin?",
            required_evidence=required_evidence,
            required_calculations=[RequiredCalculation(calc_type=calc_type, required=required)],
            validation_requirements=["requires_claim_or_evidence"],
            report_section="P&L",
            methodology_type=MethodologyType.FINANCIAL_DD,
            methodology_section="P&L",
        ),
        validation_requirements=["requires_claim_or_evidence"],
    )


def _service() -> InMemoryRunMethodologyDeterministicCalculationService:
    return InMemoryRunMethodologyDeterministicCalculationService()


def test_runs_required_calculation_from_expected_answer_schema_only() -> None:
    claims = [
        _claim("claim_mth_revenue", "revenue", "1000"),
        _claim("claim_mth_cogs", "cogs", "400"),
    ]
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        sanads=[
            _sanad_record("claim_mth_revenue"),
            _sanad_record("claim_mth_cogs", grade=SanadGrade.B),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs", SanadGrade.B)],
        extraction_tasks=[_task()],
    )

    summary_json = json.dumps(result.to_run_step_summary(), sort_keys=True)

    assert result.status == MethodologyCalculationStatus.COMPLETED
    assert len(calculations) == 1
    assert len(calc_sanads) == 1
    assert calculations[0].calculation.calc_type == CalcType.GROSS_MARGIN
    assert calculations[0].calculation.output.primary_value == Decimal("60.0000")
    assert calc_sanads[0].calc_sanad.calc_grade.value == "B"
    assert result.summary.created_calculation_count == 1
    assert "claim_text" not in summary_json
    assert "value_struct" not in summary_json
    assert "locator" not in summary_json
    assert "explanation" not in summary_json


def test_repeated_runs_are_deterministic_and_do_not_use_demo_fixture_scripts() -> None:
    claims = [
        _claim("claim_mth_revenue", "revenue", "1000"),
        _claim("claim_mth_cogs", "cogs", "400"),
    ]
    kwargs = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "materialized_claims": claims,
        "sanads": [_sanad_record("claim_mth_revenue"), _sanad_record("claim_mth_cogs")],
        "sanad_grades": [_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        "extraction_tasks": [_task()],
    }

    first = _service().run(**kwargs)
    second = _service().run(**kwargs)

    assert first[1][0].calculation.calc_id == second[1][0].calculation.calc_id
    assert first[2][0].calc_sanad.calc_sanad_id == second[2][0].calc_sanad.calc_sanad_id
    assert (
        first[1][0].calculation.reproducibility_hash
        == second[1][0].calculation.reproducibility_hash
    )
    assert "add_calcs_to_adversarial" not in json.dumps(first[0].to_run_step_summary())
    assert "generate_gdbs_full" not in json.dumps(first[0].to_run_step_summary())
    assert "llm_demo_one_deal" not in json.dumps(first[0].to_run_step_summary())


def test_optional_missing_inputs_are_blocked_without_fake_calc_ids() -> None:
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim("claim_mth_revenue", "revenue", "1000")],
        sanads=[_sanad_record("claim_mth_revenue")],
        sanad_grades=[_grade("claim_mth_revenue")],
        extraction_tasks=[_task(required=False)],
    )

    assert result.status == MethodologyCalculationStatus.COMPLETED
    assert calculations == []
    assert calc_sanads == []
    assert result.summary.blocked_count == 1
    assert result.rejections[0].reason == MethodologyCalculationReason.MISSING_REQUIRED_CLAIM
    assert result.rejections[0].required is False
    assert result.to_run_step_summary()["calc_ids"] == []


def test_required_unsupported_calc_type_fails_closed() -> None:
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[_sanad_record("claim_mth_revenue"), _sanad_record("claim_mth_cogs")],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(calc_type="IRR", required=True)],
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert result.rejections[0].reason == MethodologyCalculationReason.UNSUPPORTED_CALC_TYPE
    assert result.rejections[0].required is True


def test_multiple_tasks_scope_claim_inputs_to_their_own_task_metadata() -> None:
    claims = [
        _claim(
            "claim_mth_t1_revenue",
            "revenue",
            "1000",
            methodology_question_id="mq_margin_a",
            extraction_task_id="et_margin_a",
            coverage_record_id="mcr_margin_a",
        ),
        _claim(
            "claim_mth_t1_cogs",
            "cogs",
            "400",
            methodology_question_id="mq_margin_a",
            extraction_task_id="et_margin_a",
            coverage_record_id="mcr_margin_a",
        ),
        _claim(
            "claim_mth_t2_revenue",
            "revenue",
            "2000",
            methodology_question_id="mq_margin_b",
            extraction_task_id="et_margin_b",
            coverage_record_id="mcr_margin_b",
        ),
        _claim(
            "claim_mth_t2_cogs",
            "cogs",
            "1000",
            methodology_question_id="mq_margin_b",
            extraction_task_id="et_margin_b",
            coverage_record_id="mcr_margin_b",
        ),
    ]
    tasks = [
        _task(
            methodology_question_id="mq_margin_a",
            extraction_task_id="et_margin_a",
            coverage_record_id="mcr_margin_a",
        ),
        _task(
            methodology_question_id="mq_margin_b",
            extraction_task_id="et_margin_b",
            coverage_record_id="mcr_margin_b",
        ),
    ]
    grades = [_grade(claim.claim_id or "") for claim in claims]
    sanads = [_sanad_record(claim.claim_id or "") for claim in claims]

    result, calculations, _calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        sanads=sanads,
        sanad_grades=grades,
        extraction_tasks=tasks,
    )

    by_task = {
        record.extraction_task_id: record.calculation.output.primary_value
        for record in calculations
    }

    assert result.status == MethodologyCalculationStatus.COMPLETED
    assert by_task == {"et_margin_a": Decimal("60.0000"), "et_margin_b": Decimal("50.0000")}


def test_missing_sanad_grade_fails_closed_for_required_calculation() -> None:
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[_sanad_record("claim_mth_revenue"), _sanad_record("claim_mth_cogs")],
        sanad_grades=[_grade("claim_mth_revenue")],
        extraction_tasks=[_task(required=True)],
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert result.rejections[0].reason == MethodologyCalculationReason.MISSING_SANAD_GRADE


def test_required_failure_with_success_is_failed_not_partial() -> None:
    result, calculations, _calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[_sanad_record("claim_mth_revenue"), _sanad_record("claim_mth_cogs")],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(required=True), _task(calc_type="IRR", required=True)],
    )

    assert calculations
    assert result.status == MethodologyCalculationStatus.FAILED
    assert result.summary.by_reason["unsupported_calc_type"] == 1


def test_missing_sanad_metadata_blocks_without_fake_calc_ids() -> None:
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[
            _sanad_record("claim_mth_revenue", dhabt_score=None),
            _sanad_record("claim_mth_cogs"),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(required=True)],
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert result.rejections[0].reason == MethodologyCalculationReason.MISSING_SOURCE_METADATA
    assert result.to_run_step_summary()["calc_ids"] == []


def test_shell_only_sanads_block_without_fake_calc_ids() -> None:
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[_sanad_shell("claim_mth_revenue"), _sanad_shell("claim_mth_cogs")],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(required=True)],
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert result.rejections[0].reason == MethodologyCalculationReason.MISSING_SOURCE_METADATA
    assert result.to_run_step_summary()["calc_ids"] == []


def test_low_confidence_uses_extraction_gate_blocker_without_fake_calc_ids() -> None:
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[
            _sanad_record("claim_mth_revenue", extraction_confidence=0.94),
            _sanad_record("claim_mth_cogs"),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(required=True)],
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert result.rejections[0].reason == MethodologyCalculationReason.BELOW_CONFIDENCE_THRESHOLD
    assert result.to_run_step_summary()["calc_ids"] == []


def test_low_dhabt_uses_extraction_gate_blocker_without_fake_calc_ids() -> None:
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[
            _sanad_record("claim_mth_revenue", dhabt_score=0.89),
            _sanad_record("claim_mth_cogs"),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(required=True)],
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert result.rejections[0].reason == MethodologyCalculationReason.BELOW_DHABT_THRESHOLD
    assert result.to_run_step_summary()["calc_ids"] == []
