"""Task 4 RED contract for durable, aggregate-safe PDF encryption dispositions."""

from __future__ import annotations

import io
import json
from typing import Any
from uuid import uuid4

from pypdf import PdfReader, PdfWriter

from idis.parsers.ocr import OcrConfig, OcrPageText
from idis.parsers.pdf import parse_pdf
from idis.services.ingestion.service import IngestionService
from tests.slice100_task4.test_media_dual_track_terminal import _dual_track_rows
from tests.slice100_task4.test_terminal_reconciliation import (
    EXPECTED_COMPANY_COUNTS,
    EXPECTED_FORMAT_COUNTS,
    EXPECTED_LARGE_FILES,
    EXPECTED_MEDIA_RUNTIME_PROVENANCE,
    EXPECTED_OCR_FILES,
    EXPECTED_STT_FILES,
    EXPECTED_TOTAL_FILES,
    FIFTY_MIB,
    _expected_pdf_ocr_hashes,
    _source_companies,
)
from tests.test_pdf_parser import (
    create_encrypted_test_pdf,
    create_image_only_test_pdf,
    create_test_pdf,
)

EXPECTED_PDF_ENCRYPTION_DISPOSITIONS = {
    "not_encryption_marked": 81,
    "empty_credential_openable": 142,
    "configured_credential_openable": 0,
    "credential_required": 0,
    "credential_invalid": 0,
}


class _OcrAdapter:
    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        del data, max_pages, timeout_seconds
        return [OcrPageText(page_number=1, text="FICTIONAL OCR EVIDENCE", confidence=0.9)]

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        del data, timeout_seconds
        return []


def _encrypt(data: bytes, password: str) -> bytes:
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    owner_credential = "-".join(("fictional", "owner", "credential"))
    writer.encrypt(user_password=password, owner_password=owner_credential)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _metadata(result: Any) -> dict[str, Any]:
    service = IngestionService(compliant_store=object())  # type: ignore[arg-type]
    return service._parse_metadata_for_persistence(  # noqa: SLF001 -- durable contract.
        result,
        document_id=uuid4(),
        artifact_id=uuid4(),
    )


def test_plain_empty_and_configured_password_pdfs_persist_safe_dispositions() -> None:
    configured_secret = "fictional-configured-password"
    results = [
        parse_pdf(create_test_pdf(["FICTIONAL PLAIN EVIDENCE"])),
        parse_pdf(
            create_encrypted_test_pdf(
                ["FICTIONAL EMPTY-CREDENTIAL EVIDENCE"],
                user_password="",
            )
        ),
        parse_pdf(
            create_encrypted_test_pdf(
                ["FICTIONAL CONFIGURED-CREDENTIAL EVIDENCE"],
                user_password=configured_secret,
            ),
            password=configured_secret,
        ),
    ]

    persisted = [_metadata(result)["pdf_encryption_disposition"] for result in results]

    assert persisted == [
        "not_encryption_marked",
        "empty_credential_openable",
        "configured_credential_openable",
    ]
    assert configured_secret not in json.dumps([_metadata(result) for result in results])


def test_empty_credential_encryption_disposition_survives_pdf_ocr_fallback() -> None:
    encrypted_image_pdf = _encrypt(create_image_only_test_pdf(), "")

    result = parse_pdf(
        encrypted_image_pdf,
        ocr_config=OcrConfig(enabled=True, adapter=_OcrAdapter()),
    )

    assert result.success is True
    assert result.metadata["ocr_performed"] is True
    assert _metadata(result)["pdf_encryption_disposition"] == ("empty_credential_openable")


def _rows_with_audited_dispositions() -> list[dict[str, Any]]:
    _companies, rows = _dual_track_rows(lambda _index: (True, False))
    pdf_index = 0
    for row in rows:
        if row["extension"] != "pdf":
            continue
        row["pdf_encryption_disposition"] = (
            "not_encryption_marked" if pdf_index < 81 else "empty_credential_openable"
        )
        pdf_index += 1
    assert pdf_index == 223
    return rows


def _reconcile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from idis.evaluation.real_example_terminal_reconciliation import (
        reconcile_terminal_ingestion,
    )

    return reconcile_terminal_ingestion(
        source_companies=_source_companies(),
        terminal_rows=rows,
        expected_company_counts=EXPECTED_COMPANY_COUNTS,
        expected_format_counts=EXPECTED_FORMAT_COUNTS,
        expected_total_files=EXPECTED_TOTAL_FILES,
        expected_ocr_files=EXPECTED_OCR_FILES,
        expected_stt_files=EXPECTED_STT_FILES,
        expected_large_files=EXPECTED_LARGE_FILES,
        large_file_threshold_bytes=FIFTY_MIB,
        expected_pdf_encryption_dispositions=EXPECTED_PDF_ENCRYPTION_DISPOSITIONS,
        expected_media_runtime_provenance=EXPECTED_MEDIA_RUNTIME_PROVENANCE,
        expected_pdf_ocr_content_sha256s=_expected_pdf_ocr_hashes(_source_companies()),
    )


def test_exact_audited_pdf_dispositions_reconcile_to_aggregate_only() -> None:
    summary = _reconcile(_rows_with_audited_dispositions())

    assert summary["pdf_encryption_dispositions"] == EXPECTED_PDF_ENCRYPTION_DISPOSITIONS
    encoded = json.dumps(summary, sort_keys=True)
    assert "password" not in encoded
    assert "filename" not in encoded
    assert "path" not in encoded


def test_missing_pdf_disposition_fails_closed() -> None:
    import pytest

    from idis.evaluation.real_example_terminal_reconciliation import (
        TerminalIngestionReconciliationError,
    )

    rows = _rows_with_audited_dispositions()
    del next(row for row in rows if row["extension"] == "pdf")["pdf_encryption_disposition"]

    with pytest.raises(TerminalIngestionReconciliationError):
        _reconcile(rows)


def test_configured_credential_pdf_is_unexpected_for_data_room_contract() -> None:
    import pytest

    from idis.evaluation.real_example_terminal_reconciliation import (
        TerminalIngestionReconciliationError,
    )

    rows = _rows_with_audited_dispositions()
    next(row for row in rows if row["extension"] == "pdf")["pdf_encryption_disposition"] = (
        "configured_credential_openable"
    )

    with pytest.raises(TerminalIngestionReconciliationError):
        _reconcile(rows)
