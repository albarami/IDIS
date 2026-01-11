"""Deliverables Export — v6.3 Phase 6.1

PDF/DOCX exporters with audit appendix rendering.

Trust invariants:
- Validation MUST pass before export (fail-closed)
- Audit appendix included by default
- Stable ordering for deterministic output
- No randomness in export paths
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from idis.models.deliverables import (
    AuditAppendix,
    DeliverableExportFormat,
    DeliverableExportResult,
    DeliverableFact,
    DeliverableSection,
    DissentSection,
    ICMemo,
    ScreeningSnapshot,
)
from idis.validators.deliverable import (
    DeliverableValidationError,
    validate_deliverable_no_free_facts,
)

if TYPE_CHECKING:
    pass


class DeliverableExportError(Exception):
    """Error during deliverable export."""

    def __init__(self, message: str, code: str = "EXPORT_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


Deliverable = ScreeningSnapshot | ICMemo


class DeliverableExporter:
    """Exports deliverables to PDF and DOCX formats.

    Trust invariants enforced:
    - No-Free-Facts validation runs before export (fail-closed)
    - Audit appendix is rendered by default
    - Export is deterministic (no randomness)
    """

    def __init__(self, validate_before_export: bool = True) -> None:
        """Initialize the exporter.

        Args:
            validate_before_export: If True, run No-Free-Facts validation
                before export. Raises DeliverableExportError on violation.
                Defaults to True (fail-closed behavior).
        """
        self._validate_before_export = validate_before_export

    def _validate(self, deliverable: Deliverable) -> None:
        """Validate deliverable before export (fail-closed)."""
        if not self._validate_before_export:
            return

        try:
            validate_deliverable_no_free_facts(deliverable)
        except DeliverableValidationError as e:
            raise DeliverableExportError(
                message=f"Deliverable validation failed: {e.message}",
                code="VALIDATION_FAILED",
            ) from e

    def _render_fact_text(self, fact: DeliverableFact) -> str:
        """Render a fact as text with ref annotations."""
        refs = []
        if fact.claim_refs:
            refs.extend([f"[claim:{r}]" for r in fact.claim_refs])
        if fact.calc_refs:
            refs.extend([f"[calc:{r}]" for r in fact.calc_refs])

        ref_str = " ".join(refs)
        if fact.sanad_grade:
            ref_str = f"[Grade:{fact.sanad_grade}] {ref_str}"

        return f"{fact.text} {ref_str}".strip()

    def _render_section_text(self, section: DeliverableSection) -> str:
        """Render a section as plain text."""
        lines = [f"## {section.title}", ""]

        for fact in section.facts:
            lines.append(f"- {self._render_fact_text(fact)}")

        if section.narrative:
            lines.append("")
            lines.append(section.narrative)

        return "\n".join(lines)

    def _render_dissent_text(self, dissent: DissentSection) -> str:
        """Render dissent section as plain text."""
        refs = []
        if dissent.claim_refs:
            refs.extend([f"[claim:{r}]" for r in dissent.claim_refs])
        if dissent.calc_refs:
            refs.extend([f"[calc:{r}]" for r in dissent.calc_refs])

        lines = [
            "## Dissent",
            "",
            f"**Agent Role:** {dissent.agent_role}",
            f"**Position:** {dissent.position}",
            f"**Confidence:** {dissent.confidence:.0%}",
            "",
            f"**Rationale:** {dissent.rationale}",
            "",
            f"**Evidence:** {' '.join(refs)}",
        ]
        return "\n".join(lines)

    def _render_audit_appendix_text(self, appendix: AuditAppendix) -> str:
        """Render audit appendix as plain text."""
        lines = [
            "---",
            "# Audit Appendix",
            "",
            f"**Generated:** {appendix.generated_at}",
            f"**Deal ID:** {appendix.deal_id}",
            f"**Tenant ID:** {appendix.tenant_id}",
            "",
            "## Evidence References",
            "",
        ]

        for entry in appendix.entries:
            ref_line = f"- [{entry.ref_type.value}] {entry.ref_id}"
            if entry.sanad_grade:
                ref_line += f" (Grade: {entry.sanad_grade})"
            if entry.reproducibility_hash:
                ref_line += f" (Hash: {entry.reproducibility_hash[:16]}...)"
            lines.append(ref_line)

        return "\n".join(lines)

    def _render_screening_snapshot_text(self, snapshot: ScreeningSnapshot) -> str:
        """Render screening snapshot as plain text."""
        lines = [
            f"# Screening Snapshot: {snapshot.deal_name}",
            "",
            f"**Deal ID:** {snapshot.deal_id}",
            f"**Generated:** {snapshot.generated_at}",
            "",
        ]

        lines.append(self._render_section_text(snapshot.summary_section))
        lines.append("")
        lines.append(self._render_section_text(snapshot.key_metrics_section))
        lines.append("")
        lines.append(self._render_section_text(snapshot.red_flags_section))
        lines.append("")
        lines.append(self._render_section_text(snapshot.missing_info_section))

        for section in snapshot.additional_sections:
            lines.append("")
            lines.append(self._render_section_text(section))

        lines.append("")
        lines.append(self._render_audit_appendix_text(snapshot.audit_appendix))

        return "\n".join(lines)

    def _render_ic_memo_text(self, memo: ICMemo) -> str:
        """Render IC Memo as plain text."""
        lines = [
            f"# IC Memo: {memo.deal_name}",
            "",
            f"**Deal ID:** {memo.deal_id}",
            f"**Generated:** {memo.generated_at}",
            "",
        ]

        sections = [
            memo.executive_summary,
            memo.company_overview,
            memo.market_analysis,
            memo.financials,
            memo.team_assessment,
            memo.risks_and_mitigations,
            memo.recommendation,
            memo.truth_dashboard_summary,
        ]

        if memo.scenario_analysis:
            sections.append(memo.scenario_analysis)

        for section in sections:
            lines.append(self._render_section_text(section))
            lines.append("")

        if memo.sanad_grade_distribution:
            lines.append("## Sanad Grade Distribution")
            lines.append("")
            for grade, count in sorted(memo.sanad_grade_distribution.items()):
                lines.append(f"- **Grade {grade}:** {count}")
            lines.append("")

        if memo.dissent_section:
            lines.append(self._render_dissent_text(memo.dissent_section))
            lines.append("")

        for section in memo.additional_sections:
            lines.append(self._render_section_text(section))
            lines.append("")

        lines.append(self._render_audit_appendix_text(memo.audit_appendix))

        return "\n".join(lines)

    def _render_text(self, deliverable: Deliverable) -> str:
        """Render deliverable as plain text."""
        if isinstance(deliverable, ScreeningSnapshot):
            return self._render_screening_snapshot_text(deliverable)
        elif isinstance(deliverable, ICMemo):
            return self._render_ic_memo_text(deliverable)
        else:
            raise DeliverableExportError(
                message=f"Unsupported deliverable type: {type(deliverable)}",
                code="UNSUPPORTED_TYPE",
            )

    def export_to_pdf(
        self,
        deliverable: Deliverable,
        export_timestamp: str,
        include_audit_appendix: bool = True,
    ) -> DeliverableExportResult:
        """Export deliverable to PDF format.

        Args:
            deliverable: The deliverable to export
            export_timestamp: ISO timestamp for the export (passed in)
            include_audit_appendix: Whether to include audit appendix

        Returns:
            DeliverableExportResult with PDF bytes

        Raises:
            DeliverableExportError: If validation fails or export fails
        """
        self._validate(deliverable)

        text_content = self._render_text(deliverable)

        pdf_bytes = self._generate_pdf_bytes(text_content)

        return DeliverableExportResult(
            deliverable_id=deliverable.deliverable_id,
            format=DeliverableExportFormat.PDF,
            content_bytes=pdf_bytes,
            content_length=len(pdf_bytes),
            includes_audit_appendix=include_audit_appendix,
            export_timestamp=export_timestamp,
        )

    def export_to_docx(
        self,
        deliverable: Deliverable,
        export_timestamp: str,
        include_audit_appendix: bool = True,
    ) -> DeliverableExportResult:
        """Export deliverable to DOCX format.

        Args:
            deliverable: The deliverable to export
            export_timestamp: ISO timestamp for the export (passed in)
            include_audit_appendix: Whether to include audit appendix

        Returns:
            DeliverableExportResult with DOCX bytes

        Raises:
            DeliverableExportError: If validation fails or export fails
        """
        self._validate(deliverable)

        text_content = self._render_text(deliverable)

        docx_bytes = self._generate_docx_bytes(text_content)

        return DeliverableExportResult(
            deliverable_id=deliverable.deliverable_id,
            format=DeliverableExportFormat.DOCX,
            content_bytes=docx_bytes,
            content_length=len(docx_bytes),
            includes_audit_appendix=include_audit_appendix,
            export_timestamp=export_timestamp,
        )

    def _generate_pdf_bytes(self, text_content: str) -> bytes:
        """Generate PDF bytes from text content.

        This is a minimal PDF generator that produces a valid PDF
        with the text content. In production, use a proper PDF library.
        """
        pdf_content = self._create_minimal_pdf(text_content)
        return pdf_content

    def _generate_docx_bytes(self, text_content: str) -> bytes:
        """Generate DOCX bytes from text content.

        This is a minimal DOCX generator that produces a valid DOCX
        with the text content. In production, use python-docx.
        """
        docx_content = self._create_minimal_docx(text_content)
        return docx_content

    def _create_minimal_pdf(self, text: str) -> bytes:
        """Create a minimal valid PDF with text content.

        PDF structure:
        - Header: %PDF-1.4
        - Objects: Catalog, Pages, Page, Content stream, Font
        - Cross-reference table
        - Trailer
        """
        text_escaped = (
            text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\n", ") Tj T* (")
        )

        objects: list[bytes] = []
        offsets: list[int] = []

        header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"

        current_offset = len(header)
        offsets.append(current_offset)
        obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        objects.append(obj1)
        current_offset += len(obj1)

        offsets.append(current_offset)
        obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        objects.append(obj2)
        current_offset += len(obj2)

        offsets.append(current_offset)
        obj3 = (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        )
        objects.append(obj3)
        current_offset += len(obj3)

        content_stream = f"BT /F1 10 Tf 50 750 Td 12 TL ({text_escaped}) Tj ET".encode(
            "latin-1", errors="replace"
        )
        stream_len = len(content_stream)

        offsets.append(current_offset)
        obj4 = (
            f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode()
            + content_stream
            + b"\nendstream\nendobj\n"
        )
        objects.append(obj4)
        current_offset += len(obj4)

        offsets.append(current_offset)
        obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        objects.append(obj5)
        current_offset += len(obj5)

        xref_offset = current_offset
        xref_lines = [b"xref\n", f"0 {len(objects) + 1}\n".encode()]
        xref_lines.append(b"0000000000 65535 f \n")
        for offset in offsets:
            xref_lines.append(f"{offset:010d} 00000 n \n".encode())

        xref = b"".join(xref_lines)

        trailer = (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()

        pdf_bytes = header + b"".join(objects) + xref + trailer
        return pdf_bytes

    def _create_minimal_docx(self, text: str) -> bytes:
        """Create a minimal valid DOCX with text content.

        DOCX is a ZIP file with XML content. Minimal structure:
        - [Content_Types].xml
        - _rels/.rels
        - word/document.xml
        """
        import zipfile

        text_escaped = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "</w:t></w:r></w:p><w:p><w:r><w:t>")
        )

        ct_rels = "application/vnd.openxmlformats-package.relationships+xml"
        ct_main = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        content_types = f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="{ct_rels}"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/word/document.xml" ContentType="{ct_main}"/>
</Types>""".encode()

        rel_type = (
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        )
        rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="{rel_type}" Target="word/document.xml"/>
</Relationships>""".encode()

        document = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body>
        <w:p>
            <w:r>
                <w:t>{text_escaped}</w:t>
            </w:r>
        </w:p>
    </w:body>
</w:document>""".encode()

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", rels)
            zf.writestr("word/document.xml", document)

        return buffer.getvalue()


def export_to_pdf(
    deliverable: Deliverable,
    export_timestamp: str,
    include_audit_appendix: bool = True,
    validate: bool = True,
) -> DeliverableExportResult:
    """Convenience function to export deliverable to PDF.

    Args:
        deliverable: The deliverable to export
        export_timestamp: ISO timestamp for the export
        include_audit_appendix: Whether to include audit appendix
        validate: Whether to validate before export

    Returns:
        DeliverableExportResult with PDF bytes
    """
    exporter = DeliverableExporter(validate_before_export=validate)
    return exporter.export_to_pdf(
        deliverable=deliverable,
        export_timestamp=export_timestamp,
        include_audit_appendix=include_audit_appendix,
    )


def export_to_docx(
    deliverable: Deliverable,
    export_timestamp: str,
    include_audit_appendix: bool = True,
    validate: bool = True,
) -> DeliverableExportResult:
    """Convenience function to export deliverable to DOCX.

    Args:
        deliverable: The deliverable to export
        export_timestamp: ISO timestamp for the export
        include_audit_appendix: Whether to include audit appendix
        validate: Whether to validate before export

    Returns:
        DeliverableExportResult with DOCX bytes
    """
    exporter = DeliverableExporter(validate_before_export=validate)
    return exporter.export_to_docx(
        deliverable=deliverable,
        export_timestamp=export_timestamp,
        include_audit_appendix=include_audit_appendix,
    )
