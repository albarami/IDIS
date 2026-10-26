"""Tests for Slice 9 in-memory methodology deterministic calculation service."""

from __future__ import annotations

import json
from decimal import Decimal

from idis.calc.engine import CalcEngine, CalcEngineResult, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.methodology.models import MethodologyType, RequiredCalculation, RequiredEvidence
from idis.models.calc_materialization import (
    MethodologyCalculationReason,
    MethodologyCalculationRunResult,
    MethodologyCalculationStatus,
    RunScopedCalcSanadRecord,
    RunScopedDeterministicCalculationRecord,
)
from idis.models.claim import Materiality
from idis.models.claim_materialization import (
    MaterializedClaimSourceRef,
    MaterializedClaimType,
    MaterializedClaimValueStruct,
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.deterministic_calculation import CalcType
from idis.models.extraction_task import (
    ExpectedAnswerSchema,
    ExtractionTask,
    ExtractionTaskStatus,
    FddPolicyV2ExtractionTask,
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
    unit: str = "USD",
    currency: str = "USD",
    time_window: str = "FY2024",
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
            unit=unit,
            currency=currency,
            time_window=time_window,
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


class _CountingCalcEngine(CalcEngine):
    """Genuine CalcEngine that additionally records run invocations.

    Bounded test spy at the existing ``service._engine`` seam; delegates every
    call so behavior is identical while refusing paths can assert zero calls.
    """

    def __init__(self) -> None:
        super().__init__(registry=register_core_formulas(FormulaRegistry()))
        self.run_calls = 0

    def run(
        self,
        tenant_id: str,
        deal_id: str,
        calc_type: CalcType,
        input_values: dict[str, Decimal],
        input_grades: list[InputGradeInfo],
        metadata: dict[str, str] | None = None,
    ) -> CalcEngineResult:
        self.run_calls += 1
        return super().run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=calc_type,
            input_values=input_values,
            input_grades=input_grades,
            metadata=metadata,
        )


def _service_and_spy() -> tuple[
    InMemoryRunMethodologyDeterministicCalculationService,
    _CountingCalcEngine,
]:
    """Service with a counting engine installed at its existing engine seam."""
    service = _service()
    engine = _CountingCalcEngine()
    service._engine = engine
    return service, engine


def _assert_selected_pair_coordinate_refusal(
    *,
    result: MethodologyCalculationRunResult,
    calculations: list[RunScopedDeterministicCalculationRecord],
    calc_sanads: list[RunScopedCalcSanadRecord],
    engine: _CountingCalcEngine,
    coordinate: str,
) -> None:
    """Shared oracle: one truthful pre-computation refusal, no engine or records."""
    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert result.calculation_mappings == []
    assert engine.run_calls == 0
    assert result.summary.created_calculation_count == 0
    assert result.summary.blocked_count == 1
    assert result.summary.by_reason == {MethodologyCalculationReason.CALCULATION_FAILED.value: 1}
    assert len(result.rejections) == 1
    rejection = result.rejections[0]
    assert rejection.reason == MethodologyCalculationReason.CALCULATION_FAILED
    assert rejection.reason_codes == [MethodologyCalculationReason.CALCULATION_FAILED.value]
    assert rejection.claim_ids == ["claim_mth_cogs", "claim_mth_revenue"]
    assert rejection.required is True
    assert "pre-computation" in rejection.message
    assert coordinate in rejection.message
    assert "engine" not in rejection.message
    assert result.to_run_step_summary()["calc_ids"] == []


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


_COMPETING_REVENUE_IDS = ("claim_mth_revenue_a", "claim_mth_revenue_b")


def _three_input_support() -> tuple[
    list[RunScopedSanadRecord | RunScopedSanadShell],
    list[RunScopedSanadGradeRecord],
]:
    """Sanad and grade support for the standard revenue/cogs fixtures."""
    claim_ids = (*_COMPETING_REVENUE_IDS, "claim_mth_cogs")
    return (
        [_sanad_record(claim_id) for claim_id in claim_ids],
        [_grade(claim_id) for claim_id in claim_ids],
    )


def test_competing_required_input_operands_refuse_for_both_competitor_orders() -> None:
    """Two distinct claim identities feeding one required input key refuse."""
    cogs = _claim("claim_mth_cogs", "cogs", "400")
    sanads, grades = _three_input_support()
    for first, second in (_COMPETING_REVENUE_IDS, _COMPETING_REVENUE_IDS[::-1]):
        claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [
            _claim(first, "revenue", "1000"),
            _claim(second, "revenue", "1500"),
            cogs,
        ]
        result, calculations, calc_sanads = _service().run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            materialized_claims=claims,
            sanads=sanads,
            sanad_grades=grades,
            extraction_tasks=[_task(required=True)],
        )

        assert result.status == MethodologyCalculationStatus.FAILED
        assert calculations == []
        assert calc_sanads == []
        assert len(result.rejections) == 1
        rejection = result.rejections[0]
        assert rejection.reason == MethodologyCalculationReason.CALCULATION_FAILED
        assert rejection.claim_ids == sorted(_COMPETING_REVENUE_IDS)
        assert rejection.required is True
        assert "operand" in rejection.message
        assert result.to_run_step_summary()["calc_ids"] == []


def test_distinct_claim_identities_compete_even_at_equal_amounts() -> None:
    """Equal amounts do not collapse two distinct eligible claim identities."""
    sanads, grades = _three_input_support()
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [
        _claim("claim_mth_revenue_a", "revenue", "1000"),
        _claim("claim_mth_revenue_b", "revenue", "1000"),
        _claim("claim_mth_cogs", "cogs", "400"),
    ]
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        sanads=sanads,
        sanad_grades=grades,
        extraction_tasks=[_task(required=True)],
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert result.rejections[0].reason == MethodologyCalculationReason.CALCULATION_FAILED
    assert result.rejections[0].claim_ids == sorted(_COMPETING_REVENUE_IDS)


def test_identical_repeat_of_same_claim_is_one_observation() -> None:
    """The same immutable claim record appearing twice is not competition."""
    revenue = _claim("claim_mth_revenue", "revenue", "1000")
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [
        revenue,
        revenue,
        _claim("claim_mth_cogs", "cogs", "400"),
    ]
    sanads: list[RunScopedSanadRecord | RunScopedSanadShell] = [
        _sanad_record("claim_mth_revenue"),
        _sanad_record("claim_mth_cogs"),
    ]
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        sanads=sanads,
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(required=True)],
    )

    assert result.status == MethodologyCalculationStatus.COMPLETED
    assert len(calculations) == 1
    assert len(calc_sanads) == 1
    assert calculations[0].calculation.output.primary_value == Decimal("60.0000")


def test_same_claim_id_with_conflicting_record_content_refuses() -> None:
    """One claim id carrying conflicting record content refuses."""
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [
        _claim("claim_mth_revenue", "revenue", "1000"),
        _claim("claim_mth_revenue", "revenue", "9999"),
        _claim("claim_mth_cogs", "cogs", "400"),
    ]
    sanads: list[RunScopedSanadRecord | RunScopedSanadShell] = [
        _sanad_record("claim_mth_revenue"),
        _sanad_record("claim_mth_cogs"),
    ]
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        sanads=sanads,
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(required=True)],
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert len(result.rejections) == 1
    rejection = result.rejections[0]
    assert rejection.reason == MethodologyCalculationReason.CALCULATION_FAILED
    assert rejection.claim_ids == ["claim_mth_revenue"]
    assert "operand" in rejection.message

    # The same conflicting identity is refused when its snapshots project to
    # two different required keys — per-key singletons must not escape.
    for first, second in (("revenue", "cogs"), ("cogs", "revenue")):
        claims = [
            _claim("claim_mth_shared", first, "1000"),
            _claim("claim_mth_shared", second, "400"),
        ]
        result, calculations, calc_sanads = _service().run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            materialized_claims=claims,
            sanads=[_sanad_record("claim_mth_shared")],
            sanad_grades=[_grade("claim_mth_shared")],
            extraction_tasks=[_task(required=True)],
        )

        assert result.status == MethodologyCalculationStatus.FAILED
        assert calculations == []
        assert calc_sanads == []
        assert len(result.rejections) == 1
        rejection = result.rejections[0]
        assert rejection.reason == MethodologyCalculationReason.CALCULATION_FAILED
        assert rejection.claim_ids == ["claim_mth_shared"]
        assert "operand" in rejection.message


def test_unrequested_key_duplicates_and_wrong_task_competitors_do_not_block() -> None:
    """Duplicates on unrequested keys and wrong-task claims stay out of scope."""
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [
        _claim("claim_mth_revenue", "revenue", "1000"),
        _claim("claim_mth_cogs", "cogs", "400"),
        _claim("claim_mth_cash_a", "cash_balance", "500"),
        _claim("claim_mth_cash_b", "cash_balance", "700"),
        # Same id with conflicting content that only spans an unrequested key
        # stays outside this calculation's required-key union.
        _claim("claim_mth_revenue", "cash_balance", "7777"),
        _claim(
            "claim_mth_oth_revenue",
            "revenue",
            "9999",
            methodology_question_id="mq_other",
            extraction_task_id="et_other",
            coverage_record_id="mcr_other",
        ),
        # Same id reappearing with conflicting content under another task is
        # filtered out before the identity check.
        _claim(
            "claim_mth_revenue",
            "cogs",
            "8888",
            methodology_question_id="mq_other",
            extraction_task_id="et_other",
            coverage_record_id="mcr_other",
        ),
    ]
    sanads: list[RunScopedSanadRecord | RunScopedSanadShell] = [
        _sanad_record("claim_mth_revenue"),
        _sanad_record("claim_mth_cogs"),
    ]
    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        sanads=sanads,
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(required=True)],
    )

    assert result.status == MethodologyCalculationStatus.COMPLETED
    assert result.rejections == []
    assert len(calculations) == 1
    assert calculations[0].calculation.output.primary_value == Decimal("60.0000")


def test_ambiguity_in_one_task_does_not_block_another_task() -> None:
    """An ambiguous required input in task A refuses A while task B completes."""
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [
        _claim(
            "claim_mth_t1_rev_a",
            "revenue",
            "1000",
            methodology_question_id="mq_margin_a",
            extraction_task_id="et_margin_a",
            coverage_record_id="mcr_margin_a",
        ),
        _claim(
            "claim_mth_t1_rev_b",
            "revenue",
            "2000",
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
    tasks: list[ExtractionTask | FddPolicyV2ExtractionTask] = [
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
    sanads: list[RunScopedSanadRecord | RunScopedSanadShell] = [
        _sanad_record(claim.claim_id or "") for claim in claims
    ]

    result, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        sanads=sanads,
        sanad_grades=[_grade(claim.claim_id or "") for claim in claims],
        extraction_tasks=tasks,
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    by_task = {
        record.extraction_task_id: record.calculation.output.primary_value
        for record in calculations
    }
    assert by_task == {"et_margin_b": Decimal("50.0000")}
    assert len(calc_sanads) == 1
    assert len(result.rejections) == 1
    rejection = result.rejections[0]
    assert rejection.reason == MethodologyCalculationReason.CALCULATION_FAILED
    assert rejection.claim_ids == ["claim_mth_t1_rev_a", "claim_mth_t1_rev_b"]
    assert rejection.extraction_task_id == "et_margin_a"


def test_removing_competitor_restores_unambiguous_calculation() -> None:
    """The coherent public GROSS_MARGIN fixture is unchanged once the duplicate leaves."""
    sanads, grades = _three_input_support()
    revenue = _claim("claim_mth_revenue_a", "revenue", "1000")
    cogs = _claim("claim_mth_cogs", "cogs", "400")
    competing: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [
        revenue,
        _claim("claim_mth_revenue_b", "revenue", "1500"),
        cogs,
    ]

    refused, refused_records, refused_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=competing,
        sanads=sanads,
        sanad_grades=grades,
        extraction_tasks=[_task(required=True)],
    )
    assert refused.status == MethodologyCalculationStatus.FAILED
    assert refused_records == []
    assert refused_sanads == []

    unique: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [revenue, cogs]
    restored_sanads: list[RunScopedSanadRecord | RunScopedSanadShell] = [
        _sanad_record("claim_mth_revenue_a"),
        _sanad_record("claim_mth_cogs"),
    ]
    restored, calculations, calc_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=unique,
        sanads=restored_sanads,
        sanad_grades=[_grade("claim_mth_revenue_a"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task(required=True)],
    )
    assert restored.status == MethodologyCalculationStatus.COMPLETED
    assert restored.rejections == []
    assert len(calculations) == 1
    assert len(calc_sanads) == 1
    assert calculations[0].calculation.output.primary_value == Decimal("60.0000")
    assert calc_sanads[0].calc_sanad.calc_id == calculations[0].calculation.calc_id


def test_selected_pair_currency_mismatch_refuses_pre_computation() -> None:
    """Different present currencies on the selected pair refuse before the engine."""
    revenue = _claim("claim_mth_revenue", "revenue", "1000")
    cogs_eur = _claim("claim_mth_cogs", "cogs", "400", currency="EUR")
    for claims in ([revenue, cogs_eur], [cogs_eur, revenue]):
        service, engine = _service_and_spy()
        result, calculations, calc_sanads = service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            materialized_claims=list(claims),
            sanads=[
                _sanad_record("claim_mth_revenue"),
                _sanad_record("claim_mth_cogs"),
            ],
            sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
            extraction_tasks=[_task()],
        )

        _assert_selected_pair_coordinate_refusal(
            result=result,
            calculations=calculations,
            calc_sanads=calc_sanads,
            engine=engine,
            coordinate="currency",
        )


def test_selected_pair_time_window_mismatch_refuses_pre_computation() -> None:
    """Different present periods on the selected pair refuse before the engine."""
    revenue = _claim("claim_mth_revenue", "revenue", "1000")
    cogs_fy23 = _claim("claim_mth_cogs", "cogs", "400", time_window="FY2023")
    for claims in ([revenue, cogs_fy23], [cogs_fy23, revenue]):
        service, engine = _service_and_spy()
        result, calculations, calc_sanads = service.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            materialized_claims=list(claims),
            sanads=[
                _sanad_record("claim_mth_revenue"),
                _sanad_record("claim_mth_cogs"),
            ],
            sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
            extraction_tasks=[_task()],
        )

        _assert_selected_pair_coordinate_refusal(
            result=result,
            calculations=calculations,
            calc_sanads=calc_sanads,
            engine=engine,
            coordinate="time_window",
        )


def test_matching_present_coordinates_preserve_existing_positive() -> None:
    """Equal present currency and period stay coherent through the shared seam."""
    service, engine = _service_and_spy()
    result, calculations, calc_sanads = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[
            _sanad_record("claim_mth_revenue"),
            _sanad_record("claim_mth_cogs"),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task()],
    )

    assert result.status == MethodologyCalculationStatus.COMPLETED
    assert result.rejections == []
    assert engine.run_calls == 1
    assert len(calculations) == 1
    assert len(calc_sanads) == 1
    assert len(result.calculation_mappings) == 1
    calculation = calculations[0].calculation
    assert calculation.output.primary_value == Decimal("60.0000")
    assert calculation.output.unit == "percent"
    assert calculation.output.currency == "USD"
    assert calc_sanads[0].calc_sanad.calc_id == calculation.calc_id


def test_restoring_changed_coordinate_restores_genuine_result() -> None:
    """Restoring only the changed coordinate returns the genuine positive identity."""
    refused_service, refused_engine = _service_and_spy()
    refused, refused_calculations, refused_calc_sanads = refused_service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400", currency="EUR"),
        ],
        sanads=[
            _sanad_record("claim_mth_revenue"),
            _sanad_record("claim_mth_cogs"),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task()],
    )
    assert refused.status == MethodologyCalculationStatus.FAILED
    assert refused_engine.run_calls == 0
    assert refused_calculations == []
    assert refused_calc_sanads == []

    service, engine = _service_and_spy()
    restored, calculations, calc_sanads = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[
            _sanad_record("claim_mth_revenue"),
            _sanad_record("claim_mth_cogs"),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task()],
    )
    assert restored.status == MethodologyCalculationStatus.COMPLETED
    assert restored.rejections == []
    assert engine.run_calls == 1
    assert len(calculations) == 1
    assert len(calc_sanads) == 1
    restored_calculation = calculations[0].calculation
    assert restored_calculation.output.primary_value == Decimal("60.0000")
    assert restored_calculation.output.unit == "percent"
    assert restored_calculation.output.currency == "USD"
    assert calc_sanads[0].calc_sanad.calc_id == restored_calculation.calc_id

    _positive, positive_calculations, _positive_sanads = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        sanads=[
            _sanad_record("claim_mth_revenue"),
            _sanad_record("claim_mth_cogs"),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task()],
    )
    assert (
        restored_calculation.reproducibility_hash
        == positive_calculations[0].calculation.reproducibility_hash
    )
    assert restored_calculation.calc_id == positive_calculations[0].calculation.calc_id


def test_coordinate_invalid_pair_refuses_before_engine_thresholds() -> None:
    """A coordinate defect refuses pre-engine even when input also sits below gate."""
    service, engine = _service_and_spy()
    result, calculations, calc_sanads = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400", currency="EUR"),
        ],
        sanads=[
            _sanad_record("claim_mth_revenue", extraction_confidence=0.94),
            _sanad_record("claim_mth_cogs"),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task()],
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert calculations == []
    assert calc_sanads == []
    assert engine.run_calls == 0
    assert len(result.rejections) == 1
    rejection = result.rejections[0]
    assert rejection.reason == MethodologyCalculationReason.CALCULATION_FAILED
    assert "pre-computation" in rejection.message
    assert "currency" in rejection.message


def test_unrequested_and_wrong_task_coordinate_mismatches_do_not_block() -> None:
    """Coordinate disagreement outside the selected pair stays out of scope."""
    service, engine = _service_and_spy()
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [
        _claim("claim_mth_revenue", "revenue", "1000"),
        _claim("claim_mth_cogs", "cogs", "400"),
        _claim(
            "claim_mth_cash",
            "cash_balance",
            "500",
            currency="EUR",
            time_window="FY2023",
        ),
        _claim(
            "claim_mth_oth_revenue",
            "revenue",
            "900",
            currency="EUR",
            methodology_question_id="mq_other",
            extraction_task_id="et_other",
            coverage_record_id="mcr_other",
        ),
        _claim(
            "claim_mth_oth_cogs",
            "cogs",
            "300",
            methodology_question_id="mq_other",
            extraction_task_id="et_other",
            coverage_record_id="mcr_other",
        ),
    ]
    result, calculations, calc_sanads = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        sanads=[
            _sanad_record("claim_mth_revenue"),
            _sanad_record("claim_mth_cogs"),
        ],
        sanad_grades=[_grade("claim_mth_revenue"), _grade("claim_mth_cogs")],
        extraction_tasks=[_task()],
    )

    assert result.status == MethodologyCalculationStatus.COMPLETED
    assert result.rejections == []
    assert engine.run_calls == 1
    assert len(calculations) == 1
    assert len(calc_sanads) == 1
    assert calculations[0].calculation.output.primary_value == Decimal("60.0000")


def test_coordinate_mismatch_in_one_task_does_not_block_coherent_task() -> None:
    """Task A's incoherent selected pair refuses while task B's pair completes."""
    service, engine = _service_and_spy()
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = [
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
            currency="EUR",
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
    tasks: list[ExtractionTask | FddPolicyV2ExtractionTask] = [
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

    result, calculations, calc_sanads = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        sanads=[_sanad_record(claim.claim_id or "") for claim in claims],
        sanad_grades=[_grade(claim.claim_id or "") for claim in claims],
        extraction_tasks=tasks,
    )

    assert result.status == MethodologyCalculationStatus.FAILED
    assert engine.run_calls == 1
    assert len(calculations) == 1
    assert len(calc_sanads) == 1
    by_task = {
        record.extraction_task_id: record.calculation.output.primary_value
        for record in calculations
    }
    assert by_task == {"et_margin_b": Decimal("50.0000")}
    assert len(result.rejections) == 1
    rejection = result.rejections[0]
    assert rejection.reason == MethodologyCalculationReason.CALCULATION_FAILED
    assert rejection.extraction_task_id == "et_margin_a"
    assert rejection.claim_ids == ["claim_mth_t1_cogs", "claim_mth_t1_revenue"]
    assert "pre-computation" in rejection.message
    assert "currency" in rejection.message
