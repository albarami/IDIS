"""Tests for run-scoped methodology coverage initialization."""

from __future__ import annotations

from idis.methodology.models import (
    AssignedAgent,
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologySourceTrace,
    MethodologyType,
    MethodologyVersion,
    ReportMapping,
    RequiredEvidence,
)
from idis.models.methodology_coverage import (
    MethodologyCoverageInitializationStatus,
    MethodologyCoverageStatus,
)
from idis.services.runs.methodology_coverage_init import (
    InMemoryRunMethodologyCoverageInitService,
    load_default_methodology_registry,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _question(
    question_id: str,
    section: str,
    methodology_type: MethodologyType = MethodologyType.COMMERCIAL_DD,
    question_text: str | None = None,
) -> MethodologyQuestion:
    methodology_id = methodology_type.value
    methodology_version_id = f"{methodology_type.value}:v1"
    return MethodologyQuestion(
        methodology_id=methodology_id,
        methodology_version_id=methodology_version_id,
        methodology_question_id=question_id,
        methodology_type=methodology_type,
        section=section,
        sheet_or_source_section=section,
        question_text=question_text or f"What evidence supports {section}?",
        required_evidence=[
            RequiredEvidence(
                evidence_type="management_deck",
                description="Management-provided support",
                min_count=1,
            )
        ],
        target_document_categories=["management_presentation"],
        assigned_agents=[AssignedAgent(role="market_agent", responsibility="Assess evidence")],
        report_mapping=ReportMapping(report_section=section),
        validation_requirements=["requires_claim_or_evidence"],
        source_trace=MethodologySourceTrace(
            source_type="template",
            source_name="synthetic.json",
            source_hash="c" * 64,
            sheet_or_section=section,
        ),
    )


def _registry(
    methodology_type: MethodologyType = MethodologyType.COMMERCIAL_DD,
) -> MethodologyRegistry:
    methodology_id = methodology_type.value
    version = MethodologyVersion(
        methodology_id=methodology_id,
        methodology_version_id=f"{methodology_type.value}:v1",
        methodology_type=methodology_type,
        version_label="v1",
        source_hash="c" * 64,
        source_name="synthetic.json",
        questions=[
            _question(f"mq_{methodology_type.value}_market_0001", "Market", methodology_type),
            _question(f"mq_{methodology_type.value}_customers_0001", "Customers", methodology_type),
        ],
    )
    return MethodologyRegistry(
        methodology_id=methodology_id,
        methodology_type=methodology_type,
        versions=[version],
    )


def _registry_with_arbitrary_registry_text() -> MethodologyRegistry:
    section_label = "Arbitrary Section Label / real_example / financial Due Diligence.xlsx"
    question_text = "Sensitive question text that must not be persisted"
    version = MethodologyVersion(
        methodology_id="commercial_dd",
        methodology_version_id="commercial_dd:v1",
        methodology_type=MethodologyType.COMMERCIAL_DD,
        version_label="v1",
        source_hash="c" * 64,
        source_name="synthetic.json",
        questions=[
            _question(
                "mq_commercial_dd_arbitrary_0001",
                section_label,
                MethodologyType.COMMERCIAL_DD,
                question_text=question_text,
            )
        ],
    )
    return MethodologyRegistry(
        methodology_id="commercial_dd",
        methodology_type=MethodologyType.COMMERCIAL_DD,
        versions=[version],
    )


def test_default_safe_registry_loads_deterministically() -> None:
    """Default run registry selection uses the checked-in CDD JSON template."""
    registry = load_default_methodology_registry()

    assert registry.methodology_id == "commercial_dd"
    assert registry.methodology_type == MethodologyType.COMMERCIAL_DD
    assert registry.current_version.methodology_version_id == "commercial_dd:v1"
    assert registry.registry_hash == load_default_methodology_registry().registry_hash


def test_initialization_result_has_one_record_per_question() -> None:
    """Coverage init creates NOT_STARTED records for each registry question."""
    service = InMemoryRunMethodologyCoverageInitService(
        registry_loader_fn=lambda: _registry(),
    )

    result, records = service.run(tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID)

    assert result.status == MethodologyCoverageInitializationStatus.COMPLETED
    assert len(records) == 2
    assert result.coverage_record_ids == [record.coverage_record_id for record in records]
    assert result.methodology_question_ids == [
        "mq_commercial_dd_customers_0001",
        "mq_commercial_dd_market_0001",
    ]
    assert {record.status for record in records} == {MethodologyCoverageStatus.NOT_STARTED}


def test_initialization_result_summary_is_run_step_safe() -> None:
    """Run-step summary omits question text and document/span raw text fields."""
    service = InMemoryRunMethodologyCoverageInitService(
        registry_loader_fn=lambda: _registry(),
    )

    result, _records = service.run(tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID)
    summary = result.to_run_step_summary()

    assert "question_text" not in str(summary)
    assert "text_excerpt" not in str(summary)
    assert "raw document body" not in str(summary)
    assert summary["methodology_id"] == "commercial_dd"
    assert summary["methodology_version_id"] == "commercial_dd:v1"
    assert summary["summary"]["by_status"] == {"not_started": 2}
    assert "by_section" not in summary["summary"]
    assert "coverage_records" in summary


def test_injected_fdd_registry_works_without_excel_access() -> None:
    """Tests can inject synthetic FDD metadata without reading real Excel sources."""
    service = InMemoryRunMethodologyCoverageInitService(
        registry_loader_fn=lambda: _registry(MethodologyType.FINANCIAL_DD),
    )

    result, records = service.run(tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID)

    assert result.methodology_type == MethodologyType.FINANCIAL_DD
    assert result.methodology_id == "financial_dd"
    assert len(records) == 2
    assert result.summary.by_methodology_type == {"financial_dd": 2}


def test_run_step_summary_excludes_arbitrary_registry_text_and_raw_text_markers() -> None:
    """Persisted summaries exclude section labels and other arbitrary text."""
    arbitrary_section = "Arbitrary Section Label / real_example / financial Due Diligence.xlsx"
    arbitrary_question = "Sensitive question text that must not be persisted"
    raw_document_text = "raw document body that should never appear"
    text_excerpt = "text_excerpt"
    file_path = "C:/customer/data-room/source.pdf"
    service = InMemoryRunMethodologyCoverageInitService(
        registry_loader_fn=_registry_with_arbitrary_registry_text,
    )

    result, _records = service.run(tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID)
    summary = result.to_run_step_summary()
    persisted = str(summary)

    assert result.summary.by_section == {arbitrary_section: 1}
    assert "by_section" not in persisted
    assert arbitrary_section not in persisted
    assert arbitrary_question not in persisted
    assert raw_document_text not in persisted
    assert text_excerpt not in persisted
    assert file_path not in persisted
    assert summary["summary"] == {
        "total_questions": 1,
        "by_status": {"not_started": 1},
        "by_methodology_type": {"commercial_dd": 1},
    }
    assert summary["coverage_records"] == [
        {
            "coverage_record_id": result.coverage_record_ids[0],
            "methodology_question_id": "mq_commercial_dd_arbitrary_0001",
            "methodology_id": "commercial_dd",
            "methodology_version_id": "commercial_dd:v1",
            "methodology_type": "commercial_dd",
            "status": "not_started",
        }
    ]
