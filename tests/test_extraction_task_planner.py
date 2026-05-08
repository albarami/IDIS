"""Tests for deterministic methodology-driven extraction task planning."""

from __future__ import annotations

from idis.methodology.models import (
    AssignedAgent,
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologySourceTrace,
    MethodologyType,
    MethodologyVersion,
    RedFlagRule,
    ReportMapping,
    RequiredCalculation,
    RequiredEvidence,
)
from idis.models.document_classification import (
    CddDocumentCategory,
    ClassificationEvidence,
    DocumentClassification,
    DocumentClassificationSource,
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
    ParserCapability,
)
from idis.models.extraction_task import (
    ExtractionTaskBlockerReason,
    ExtractionTaskStatus,
    SourceSpanReference,
)
from idis.services.extraction.task_planner import InMemoryExtractionTaskPlanner

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _question(
    *,
    question_id: str = "mq_financial_dd_revenue_quality_0001",
    methodology_type: MethodologyType = MethodologyType.FINANCIAL_DD,
    section: str = "P&L",
    target_categories: list[str] | None = None,
    required_evidence_types: list[str] | None = None,
    required_evidence_specs: list[tuple[str, int]] | None = None,
) -> MethodologyQuestion:
    methodology_id = (
        "financial_dd" if methodology_type == MethodologyType.FINANCIAL_DD else "commercial_dd"
    )
    version_id = f"{methodology_id}_v1"
    return MethodologyQuestion(
        methodology_id=methodology_id,
        methodology_version_id=version_id,
        methodology_question_id=question_id,
        methodology_type=methodology_type,
        section=section,
        sheet_or_source_section=section,
        source_row_number=2,
        term="Revenue",
        nature="Quality",
        line_item="Revenue",
        question_text="Explain revenue quality using support schedules.",
        required_evidence=_required_evidence(
            required_evidence_specs=required_evidence_specs,
            required_evidence_types=required_evidence_types,
        ),
        target_document_categories=target_categories or ["financial_schedule_model", "pl_support"],
        required_calculations=[RequiredCalculation(calc_type="revenue_growth")],
        assigned_agents=[AssignedAgent(role="financial_analyst", responsibility="Assess revenue")],
        red_flag_rules=[
            RedFlagRule(rule_id="rf_revenue_quality", description="Revenue drop", severity="medium")
        ],
        report_mapping=ReportMapping(
            report_section="Financial Due Diligence",
            report_subsection="Revenue",
        ),
        validation_requirements=["cite source spans"],
        source_trace=MethodologySourceTrace(
            source_type="synthetic",
            source_name="synthetic_methodology",
            source_hash="0" * 64,
            sheet_or_section=section,
            row_number=2,
        ),
    )


def _required_evidence(
    *,
    required_evidence_specs: list[tuple[str, int]] | None,
    required_evidence_types: list[str] | None,
) -> list[RequiredEvidence]:
    if required_evidence_specs is not None:
        return [
            RequiredEvidence(
                evidence_type=evidence_type,
                description=f"{evidence_type} evidence",
                min_count=min_count,
            )
            for evidence_type, min_count in required_evidence_specs
        ]
    return [
        RequiredEvidence(evidence_type=evidence_type, description=f"{evidence_type} evidence")
        for evidence_type in (required_evidence_types or ["schedule"])
    ]


def _registry(questions: list[MethodologyQuestion] | None = None) -> MethodologyRegistry:
    questions = questions or [_question()]
    methodology_id = questions[0].methodology_id
    methodology_type = questions[0].methodology_type
    return MethodologyRegistry(
        methodology_id=methodology_id,
        methodology_type=methodology_type,
        versions=[
            MethodologyVersion(
                methodology_id=methodology_id,
                methodology_version_id=questions[0].methodology_version_id,
                methodology_type=methodology_type,
                version_label="v1",
                source_hash="1" * 64,
                source_name="synthetic_methodology",
                questions=questions,
            )
        ],
    )


def _classification(
    *,
    document_id: str = "doc-financial-model",
    fdd_category: FddDocumentCategory = FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
    cdd_category: CddDocumentCategory = CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
    support_status: DocumentSupportStatus = DocumentSupportStatus.PARTIALLY_SUPPORTED,
    triage_status: DocumentTriageStatus = DocumentTriageStatus.PARTIAL,
    target_areas: list[str] | None = None,
    secondary_fdd_categories: list[FddDocumentCategory] | None = None,
    secondary_cdd_categories: list[CddDocumentCategory] | None = None,
) -> DocumentClassification:
    return DocumentClassification(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        document_id=document_id,
        classification_id=f"dc_{document_id}",
        fdd_category=fdd_category,
        cdd_category=cdd_category,
        secondary_fdd_categories=secondary_fdd_categories or [],
        secondary_cdd_categories=secondary_cdd_categories or [],
        confidence=0.9,
        evidence=[
            ClassificationEvidence(
                source=DocumentClassificationSource.RULE,
                reason_code="synthetic_rule",
                description="Synthetic classification",
            )
        ],
        parser_capability=ParserCapability(
            file_type="XLSX",
            parser_name="xlsx",
            support_status=support_status,
            triage_status=triage_status,
            reason_codes=[triage_status.value],
            requires_conversion=triage_status == DocumentTriageStatus.CONVERSION_REQUIRED,
            requires_ocr=triage_status == DocumentTriageStatus.OCR_REQUIRED,
        ),
        triage_status=triage_status,
        support_status=support_status,
        usable_for_methodology_extraction=support_status
        in {DocumentSupportStatus.SUPPORTED, DocumentSupportStatus.PARTIALLY_SUPPORTED},
        methodology_target_areas=target_areas or ["P&L", "Business Plan Assumptions"],
        reason_codes=["synthetic_rule"],
    )


def _span(
    document_id: str = "doc-financial-model",
    *,
    span_id: str = "span-001",
    evidence_tags: list[str] | None = None,
) -> SourceSpanReference:
    return SourceSpanReference(
        document_id=document_id,
        span_id=span_id,
        evidence_tags=evidence_tags or ["schedule"],
        locator={"sheet": "P&L", "cell": "A1"},
        text_excerpt="Revenue",
    )


def test_matching_methodology_question_classification_and_spans_creates_ready_task() -> None:
    planner = InMemoryExtractionTaskPlanner()

    result = planner.plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(),
        classifications=[_classification()],
        source_spans_by_document_id={"doc-financial-model": [_span()]},
    )

    assert result.summary.total_tasks == 1
    assert result.tasks[0].status == ExtractionTaskStatus.READY
    assert result.tasks[0].methodology_question_id == "mq_financial_dd_revenue_quality_0001"
    assert result.tasks[0].document_id == "doc-financial-model"
    assert result.tasks[0].source_span_ids == ["span-001"]


def test_no_matching_classification_creates_blocked_task() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(),
        classifications=[],
        source_spans_by_document_id={},
    )

    task = result.tasks[0]
    assert task.status == ExtractionTaskStatus.BLOCKED
    assert task.blocker_reason == ExtractionTaskBlockerReason.NO_MATCHING_CLASSIFIED_DOCUMENT


def test_mismatched_category_creates_blocker_reason() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(),
        classifications=[
            _classification(
                fdd_category=FddDocumentCategory.HR_TEAM,
                cdd_category=CddDocumentCategory.MANAGEMENT_TEAM,
                target_areas=["Management / Team"],
            )
        ],
        source_spans_by_document_id={"doc-financial-model": [_span()]},
    )

    assert (
        result.tasks[0].blocker_reason == ExtractionTaskBlockerReason.NO_MATCHING_DOCUMENT_CATEGORY
    )


def test_section_only_match_does_not_create_ready_task() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(),
        classifications=[
            _classification(
                fdd_category=FddDocumentCategory.MARKET_RESEARCH,
                cdd_category=CddDocumentCategory.MARKET_RESEARCH,
                target_areas=["P&L"],
            )
        ],
        source_spans_by_document_id={"doc-financial-model": [_span()]},
    )

    task = result.tasks[0]
    assert task.status == ExtractionTaskStatus.BLOCKED
    assert task.blocker_reason == ExtractionTaskBlockerReason.NO_MATCHING_DOCUMENT_CATEGORY
    assert task.document_id is None
    assert task.classification_id is None
    assert task.source_span_ids == []


def test_no_matching_category_does_not_attach_unrelated_document() -> None:
    first = (
        InMemoryExtractionTaskPlanner()
        .plan_tasks(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_registry=_registry(),
            classifications=[
                _classification(
                    document_id="doc-market",
                    fdd_category=FddDocumentCategory.MARKET_RESEARCH,
                    cdd_category=CddDocumentCategory.MARKET_RESEARCH,
                    target_areas=["Market"],
                )
            ],
            source_spans_by_document_id={"doc-market": [_span("doc-market")]},
        )
        .tasks[0]
    )
    second = (
        InMemoryExtractionTaskPlanner()
        .plan_tasks(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_registry=_registry(),
            classifications=[
                _classification(
                    document_id="doc-market",
                    fdd_category=FddDocumentCategory.MARKET_RESEARCH,
                    cdd_category=CddDocumentCategory.MARKET_RESEARCH,
                    target_areas=["Market"],
                )
            ],
            source_spans_by_document_id={"doc-market": [_span("doc-market")]},
        )
        .tasks[0]
    )

    assert first.blocker_reason == ExtractionTaskBlockerReason.NO_MATCHING_DOCUMENT_CATEGORY
    assert first.document_id is None
    assert first.classification_id is None
    assert first.target_fdd_category is None
    assert first.target_cdd_category is None
    assert first.source_span_ids == []
    assert first.extraction_task_id == second.extraction_task_id


def test_no_spans_creates_no_source_spans_blocker() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(),
        classifications=[_classification()],
        source_spans_by_document_id={"doc-financial-model": []},
    )

    assert result.tasks[0].status == ExtractionTaskStatus.BLOCKED
    assert result.tasks[0].blocker_reason == ExtractionTaskBlockerReason.NO_SOURCE_SPANS


def test_parser_triage_statuses_create_blocked_tasks() -> None:
    cases = [
        (
            DocumentSupportStatus.UNSUPPORTED,
            DocumentTriageStatus.UNSUPPORTED_SOURCE,
            ExtractionTaskBlockerReason.UNSUPPORTED_SOURCE,
        ),
        (
            DocumentSupportStatus.CONVERSION_REQUIRED,
            DocumentTriageStatus.CONVERSION_REQUIRED,
            ExtractionTaskBlockerReason.CONVERSION_REQUIRED,
        ),
        (
            DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY,
            DocumentTriageStatus.OCR_REQUIRED,
            ExtractionTaskBlockerReason.OCR_REQUIRED,
        ),
        (
            DocumentSupportStatus.ENCRYPTED,
            DocumentTriageStatus.BLOCKED,
            ExtractionTaskBlockerReason.ENCRYPTED_SOURCE,
        ),
        (
            DocumentSupportStatus.CORRUPTED,
            DocumentTriageStatus.BLOCKED,
            ExtractionTaskBlockerReason.CORRUPTED_SOURCE,
        ),
        (
            DocumentSupportStatus.TOO_LARGE,
            DocumentTriageStatus.TOO_LARGE,
            ExtractionTaskBlockerReason.TOO_LARGE,
        ),
    ]

    for support_status, triage_status, blocker_reason in cases:
        result = InMemoryExtractionTaskPlanner().plan_tasks(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_registry=_registry(),
            classifications=[
                _classification(support_status=support_status, triage_status=triage_status)
            ],
            source_spans_by_document_id={"doc-financial-model": [_span()]},
        )

        assert result.tasks[0].status == ExtractionTaskStatus.BLOCKED
        assert result.tasks[0].blocker_reason == blocker_reason


def test_unknown_parser_status_creates_blocked_task() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(),
        classifications=[
            _classification(
                support_status=DocumentSupportStatus.UNKNOWN,
                triage_status=DocumentTriageStatus.UNKNOWN,
            )
        ],
        source_spans_by_document_id={"doc-financial-model": [_span()]},
    )

    assert result.tasks[0].status == ExtractionTaskStatus.BLOCKED
    assert result.tasks[0].blocker_reason == ExtractionTaskBlockerReason.UNKNOWN_PARSER_STATUS


def test_financial_statement_alias_maps_to_fdd_financial_categories() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry([_question(target_categories=["financial_statement"])]),
        classifications=[_classification(fdd_category=FddDocumentCategory.CASH_FLOW_SUPPORT)],
        source_spans_by_document_id={"doc-financial-model": [_span()]},
    )

    assert result.tasks[0].status == ExtractionTaskStatus.READY


def test_workbook_support_alias_maps_to_fdd_support_categories() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry([_question(target_categories=["workbook_support"])]),
        classifications=[_classification(fdd_category=FddDocumentCategory.PL_SUPPORT)],
        source_spans_by_document_id={"doc-financial-model": [_span()]},
    )

    assert result.tasks[0].status == ExtractionTaskStatus.READY


def test_commercial_category_aliases_map_to_cdd_categories() -> None:
    cases = [
        ("business_plan", CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS),
        ("finance_model", CddDocumentCategory.PRICING_UNIT_ECONOMICS),
        ("market_report", CddDocumentCategory.MARKET_RESEARCH),
        ("customer_contract", CddDocumentCategory.COMMERCIAL_CONTRACTS),
        ("customer_contract", CddDocumentCategory.CUSTOMER_EVIDENCE),
    ]

    for target_category, cdd_category in cases:
        result = InMemoryExtractionTaskPlanner().plan_tasks(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_registry=_registry(
                [
                    _question(
                        question_id=f"mq_commercial_dd_{target_category}_{cdd_category.value}",
                        methodology_type=MethodologyType.COMMERCIAL_DD,
                        section="Customers",
                        target_categories=[target_category],
                    )
                ]
            ),
            classifications=[
                _classification(
                    cdd_category=cdd_category,
                    fdd_category=FddDocumentCategory.UNKNOWN,
                    target_areas=["Customers"],
                )
            ],
            source_spans_by_document_id={"doc-financial-model": [_span()]},
        )

        assert result.tasks[0].status == ExtractionTaskStatus.READY


def test_finance_model_alias_maps_to_fdd_financial_schedule_model() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry([_question(target_categories=["finance_model"])]),
        classifications=[
            _classification(fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL)
        ],
        source_spans_by_document_id={"doc-financial-model": [_span()]},
    )

    assert result.tasks[0].status == ExtractionTaskStatus.READY


def test_required_evidence_missing_creates_evidence_missing_task() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(
            [_question(required_evidence_types=["schedule", "contract"])]
        ),
        classifications=[_classification()],
        source_spans_by_document_id={"doc-financial-model": [_span(evidence_tags=["schedule"])]},
    )

    task = result.tasks[0]
    assert task.status == ExtractionTaskStatus.EVIDENCE_MISSING
    assert task.blocker_reason == ExtractionTaskBlockerReason.REQUIRED_EVIDENCE_MISSING
    assert "required_evidence_missing" in task.reason_codes


def test_multiple_required_evidence_types_are_satisfied_deterministically() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(
            [_question(required_evidence_types=["schedule", "contract"])]
        ),
        classifications=[_classification()],
        source_spans_by_document_id={
            "doc-financial-model": [
                _span(evidence_tags=["schedule"], span_id="span-002"),
                _span(evidence_tags=["contract"], span_id="span-001"),
            ]
        },
    )

    task = result.tasks[0]
    assert task.status == ExtractionTaskStatus.READY
    assert task.source_span_ids == ["span-001", "span-002"]


def test_required_evidence_min_count_one_with_one_tag_is_ready() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry([_question(required_evidence_specs=[("schedule", 1)])]),
        classifications=[_classification()],
        source_spans_by_document_id={"doc-financial-model": [_span(evidence_tags=["schedule"])]},
    )

    assert result.tasks[0].status == ExtractionTaskStatus.READY


def test_required_evidence_min_count_two_with_one_tag_is_evidence_missing() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry([_question(required_evidence_specs=[("schedule", 2)])]),
        classifications=[_classification()],
        source_spans_by_document_id={"doc-financial-model": [_span(evidence_tags=["schedule"])]},
    )

    task = result.tasks[0]
    assert task.status == ExtractionTaskStatus.EVIDENCE_MISSING
    assert task.blocker_reason == ExtractionTaskBlockerReason.REQUIRED_EVIDENCE_MISSING
    assert "required_evidence_missing" in task.reason_codes


def test_required_evidence_min_count_two_with_two_tags_is_ready() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry([_question(required_evidence_specs=[("schedule", 2)])]),
        classifications=[_classification()],
        source_spans_by_document_id={
            "doc-financial-model": [
                _span(evidence_tags=["schedule"], span_id="span-001"),
                _span(evidence_tags=["schedule"], span_id="span-002"),
            ]
        },
    )

    assert result.tasks[0].status == ExtractionTaskStatus.READY


def test_multiple_required_evidence_types_each_enforce_min_count() -> None:
    result = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(
            [_question(required_evidence_specs=[("schedule", 2), ("contract", 1)])]
        ),
        classifications=[_classification()],
        source_spans_by_document_id={
            "doc-financial-model": [
                _span(evidence_tags=["schedule"], span_id="span-001"),
                _span(evidence_tags=["contract"], span_id="span-002"),
            ]
        },
    )

    assert result.tasks[0].status == ExtractionTaskStatus.EVIDENCE_MISSING
    assert result.tasks[0].blocker_reason == ExtractionTaskBlockerReason.REQUIRED_EVIDENCE_MISSING

    satisfied = InMemoryExtractionTaskPlanner().plan_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_registry=_registry(
            [_question(required_evidence_specs=[("schedule", 2), ("contract", 1)])]
        ),
        classifications=[_classification()],
        source_spans_by_document_id={
            "doc-financial-model": [
                _span(evidence_tags=["schedule"], span_id="span-001"),
                _span(evidence_tags=["schedule", "contract"], span_id="span-002"),
            ]
        },
    )

    assert satisfied.tasks[0].status == ExtractionTaskStatus.READY


def test_expected_answer_schema_is_populated_from_methodology() -> None:
    task = (
        InMemoryExtractionTaskPlanner()
        .plan_tasks(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_registry=_registry(),
            classifications=[_classification()],
            source_spans_by_document_id={"doc-financial-model": [_span()]},
        )
        .tasks[0]
    )

    assert task.expected_answer_schema.answer_type == "narrative"
    assert (
        task.expected_answer_schema.question_text
        == "Explain revenue quality using support schedules."
    )
    assert task.expected_answer_schema.required_evidence[0].evidence_type == "schedule"
    assert task.expected_answer_schema.required_calculations[0].calc_type == "revenue_growth"
    assert task.expected_answer_schema.report_section == "Financial Due Diligence"
    assert task.expected_answer_schema.report_subsection == "Revenue"


def test_planning_is_deterministic_and_summary_counts_are_stable() -> None:
    planner = InMemoryExtractionTaskPlanner()
    kwargs = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "methodology_registry": _registry(),
        "classifications": [_classification()],
        "source_spans_by_document_id": {"doc-financial-model": [_span()]},
    }

    first = planner.plan_tasks(**kwargs)
    second = planner.plan_tasks(**kwargs)

    assert [task.to_deterministic_json() for task in first.tasks] == [
        task.to_deterministic_json() for task in second.tasks
    ]
    assert first.summary.by_status == second.summary.by_status == {"ready": 1}
