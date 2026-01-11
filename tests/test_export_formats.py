"""Tests for Export Formats — v6.3 Phase 6.1

Tests:
- PDF export returns bytes beginning with %PDF
- DOCX export returns bytes beginning with PK (zip header)
- Both exports include the audit appendix section
"""

from __future__ import annotations

import pytest

from idis.deliverables.export import (
    DeliverableExporter,
    DeliverableExportError,
    export_to_docx,
    export_to_pdf,
)
from idis.deliverables.memo import ICMemoBuilder
from idis.deliverables.screening import ScreeningSnapshotBuilder
from idis.models.deliverables import DeliverableExportFormat


class TestPDFExport:
    """Tests for PDF export."""

    def test_pdf_export_returns_valid_pdf_header(self) -> None:
        """Test that PDF export returns bytes beginning with %PDF."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-pdf-001",
            tenant_id="tenant-001",
            deal_id="deal-pdf-001",
            deal_name="PDF Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Company founded in 2020.",
            claim_refs=["claim-001"],
        )
        builder.add_metric_fact(
            text="ARR of $5M.",
            claim_refs=["claim-002"],
        )
        builder.add_red_flag_fact(
            text="High burn rate.",
            claim_refs=["claim-003"],
        )
        builder.add_missing_info(text="Need cap table.")

        snapshot = builder.build()

        result = export_to_pdf(
            deliverable=snapshot,
            export_timestamp="2026-01-11T12:00:00Z",
            include_audit_appendix=True,
        )

        assert result.format == DeliverableExportFormat.PDF
        assert result.content_bytes.startswith(b"%PDF")
        assert result.content_length == len(result.content_bytes)
        assert result.includes_audit_appendix is True

    def test_pdf_export_ic_memo(self) -> None:
        """Test PDF export for IC Memo."""
        builder = ICMemoBuilder(
            deliverable_id="memo-pdf-001",
            tenant_id="tenant-001",
            deal_id="deal-memo-pdf",
            deal_name="Memo PDF Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_executive_summary_fact(text="Strong company.", claim_refs=["claim-001"])
        builder.add_company_overview_fact(text="Founded 2018.", claim_refs=["claim-002"])
        builder.add_market_analysis_fact(text="Large TAM.", claim_refs=["claim-003"])
        builder.add_financials_fact(text="Profitable.", claim_refs=["claim-004"])
        builder.add_team_assessment_fact(text="Experienced team.", claim_refs=["claim-005"])
        builder.add_risks_fact(text="Market risk.", claim_refs=["claim-006"])
        builder.add_recommendation_fact(text="Recommend invest.", claim_refs=["claim-007"])
        builder.add_truth_dashboard_fact(text="High verification.", claim_refs=["claim-008"])

        memo = builder.build()

        result = export_to_pdf(
            deliverable=memo,
            export_timestamp="2026-01-11T12:00:00Z",
        )

        assert result.content_bytes.startswith(b"%PDF")


class TestDOCXExport:
    """Tests for DOCX export."""

    def test_docx_export_returns_valid_zip_header(self) -> None:
        """Test that DOCX export returns bytes beginning with PK (zip header)."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-docx-001",
            tenant_id="tenant-001",
            deal_id="deal-docx-001",
            deal_name="DOCX Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Company founded in 2019.",
            claim_refs=["claim-001"],
        )
        builder.add_metric_fact(
            text="ARR of $3M.",
            claim_refs=["claim-002"],
        )
        builder.add_red_flag_fact(
            text="Customer concentration.",
            claim_refs=["claim-003"],
        )
        builder.add_missing_info(text="Need financials.")

        snapshot = builder.build()

        result = export_to_docx(
            deliverable=snapshot,
            export_timestamp="2026-01-11T12:00:00Z",
            include_audit_appendix=True,
        )

        assert result.format == DeliverableExportFormat.DOCX
        assert result.content_bytes.startswith(b"PK")
        assert result.content_length == len(result.content_bytes)
        assert result.includes_audit_appendix is True

    def test_docx_export_ic_memo(self) -> None:
        """Test DOCX export for IC Memo."""
        builder = ICMemoBuilder(
            deliverable_id="memo-docx-001",
            tenant_id="tenant-001",
            deal_id="deal-memo-docx",
            deal_name="Memo DOCX Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_executive_summary_fact(text="Good company.", claim_refs=["claim-001"])
        builder.add_company_overview_fact(text="Founded 2017.", claim_refs=["claim-002"])
        builder.add_market_analysis_fact(text="Growing market.", claim_refs=["claim-003"])
        builder.add_financials_fact(text="Strong unit economics.", claim_refs=["claim-004"])
        builder.add_team_assessment_fact(text="Great team.", claim_refs=["claim-005"])
        builder.add_risks_fact(text="Competition.", claim_refs=["claim-006"])
        builder.add_recommendation_fact(text="Recommend proceed.", claim_refs=["claim-007"])
        builder.add_truth_dashboard_fact(text="Verified claims.", claim_refs=["claim-008"])

        memo = builder.build()

        result = export_to_docx(
            deliverable=memo,
            export_timestamp="2026-01-11T12:00:00Z",
        )

        assert result.content_bytes.startswith(b"PK")


class TestAuditAppendixInExport:
    """Tests for audit appendix inclusion in exports."""

    def test_pdf_includes_audit_appendix_text(self) -> None:
        """Test that PDF export includes audit appendix section."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-audit-pdf",
            tenant_id="tenant-001",
            deal_id="deal-audit-pdf",
            deal_name="Audit PDF Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Fact with claim ref.",
            claim_refs=["claim-audit-001"],
        )
        builder.add_metric_fact(
            text="Metric with calc ref.",
            claim_refs=["claim-audit-002"],
            calc_refs=["calc-audit-001"],
        )
        builder.add_red_flag_fact(
            text="Red flag.",
            claim_refs=["claim-audit-003"],
        )
        builder.add_missing_info(text="Missing info.")

        snapshot = builder.build()

        exporter = DeliverableExporter(validate_before_export=True)
        result = exporter.export_to_pdf(
            deliverable=snapshot,
            export_timestamp="2026-01-11T12:00:00Z",
            include_audit_appendix=True,
        )

        assert result.includes_audit_appendix is True

        text_content = exporter._render_text(snapshot)
        assert "Audit Appendix" in text_content
        assert "claim-audit-001" in text_content
        assert "claim-audit-002" in text_content
        assert "calc-audit-001" in text_content

    def test_docx_includes_audit_appendix_text(self) -> None:
        """Test that DOCX export includes audit appendix section."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-audit-docx",
            tenant_id="tenant-001",
            deal_id="deal-audit-docx",
            deal_name="Audit DOCX Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Summary fact.",
            claim_refs=["claim-docx-001"],
        )
        builder.add_metric_fact(
            text="Metric fact.",
            claim_refs=["claim-docx-002"],
        )
        builder.add_red_flag_fact(
            text="Red flag.",
            claim_refs=["claim-docx-003"],
        )
        builder.add_missing_info(text="Missing.")

        snapshot = builder.build()

        exporter = DeliverableExporter(validate_before_export=True)
        result = exporter.export_to_docx(
            deliverable=snapshot,
            export_timestamp="2026-01-11T12:00:00Z",
            include_audit_appendix=True,
        )

        assert result.includes_audit_appendix is True

        text_content = exporter._render_text(snapshot)
        assert "Audit Appendix" in text_content
        assert "claim-docx-001" in text_content


class TestExportValidation:
    """Tests for validation before export."""

    def test_export_fails_on_validation_error(self) -> None:
        """Test that export fails if validation fails."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-fail-export",
            tenant_id="tenant-001",
            deal_id="deal-fail-export",
            deal_name="Fail Export Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Revenue is $10M.",
            claim_refs=[],
        )
        builder.add_metric_fact(
            text="Metric.",
            claim_refs=["claim-001"],
        )
        builder.add_red_flag_fact(
            text="Flag.",
            claim_refs=["claim-002"],
        )
        builder.add_missing_info(text="Missing.")

        snapshot = builder.build()

        with pytest.raises(DeliverableExportError) as exc_info:
            export_to_pdf(
                deliverable=snapshot,
                export_timestamp="2026-01-11T12:00:00Z",
            )

        assert exc_info.value.code == "VALIDATION_FAILED"

    def test_export_with_validation_disabled(self) -> None:
        """Test that export works when validation is disabled."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-no-validate",
            tenant_id="tenant-001",
            deal_id="deal-no-validate",
            deal_name="No Validate Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Revenue is $10M.",
            claim_refs=[],
        )
        builder.add_metric_fact(
            text="Metric.",
            claim_refs=["claim-001"],
        )
        builder.add_red_flag_fact(
            text="Flag.",
            claim_refs=["claim-002"],
        )
        builder.add_missing_info(text="Missing.")

        snapshot = builder.build()

        result = export_to_pdf(
            deliverable=snapshot,
            export_timestamp="2026-01-11T12:00:00Z",
            validate=False,
        )

        assert result.content_bytes.startswith(b"%PDF")


class TestExporterDeterminism:
    """Tests for deterministic export behavior."""

    def test_same_input_produces_same_output(self) -> None:
        """Test that same input produces same output (no randomness)."""

        def create_snapshot() -> ScreeningSnapshotBuilder:
            builder = ScreeningSnapshotBuilder(
                deliverable_id="snap-deterministic",
                tenant_id="tenant-001",
                deal_id="deal-deterministic",
                deal_name="Deterministic Corp",
                generated_at="2026-01-11T12:00:00Z",
            )
            builder.add_summary_fact(text="Fact 1.", claim_refs=["claim-001"])
            builder.add_metric_fact(text="Fact 2.", claim_refs=["claim-002"])
            builder.add_red_flag_fact(text="Fact 3.", claim_refs=["claim-003"])
            builder.add_missing_info(text="Missing.")
            return builder

        snapshot1 = create_snapshot().build()
        snapshot2 = create_snapshot().build()

        exporter = DeliverableExporter(validate_before_export=True)

        text1 = exporter._render_text(snapshot1)
        text2 = exporter._render_text(snapshot2)

        assert text1 == text2
