"""Task 4 RED contract for exact, fail-closed terminal ingestion reconciliation.

The fixtures are entirely fictional and contain safe aliases/hashes only.  This
module does not inspect or name the private source tree.  Password/encryption
semantics are intentionally outside this module pending the approved contract
clarification for empty-password-openable PDFs.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from types import ModuleType
from typing import Any

import pytest

from idis.evaluation.real_example_source_package import (
    CompanySourceContract,
    SourceFileContract,
)

EXPECTED_COMPANY_COUNTS = {"company-001": 58, "company-002": 209}
EXPECTED_FORMAT_COUNTS = {
    "pdf": 223,
    "xlsx": 27,
    "mp4": 8,
    "docx": 3,
    "png": 2,
    "pptx": 2,
    "html": 1,
    "txt": 1,
}
EXPECTED_COMPANY_FORMATS = {
    "company-001": {
        "pdf": 41,
        "xlsx": 5,
        "mp4": 8,
        "docx": 3,
        "html": 1,
    },
    "company-002": {
        "pdf": 182,
        "xlsx": 22,
        "png": 2,
        "pptx": 2,
        "txt": 1,
    },
}
EXPECTED_TOTAL_FILES = 267
EXPECTED_OCR_FILES = 44
EXPECTED_PDF_OCR_FILES = 42
EXPECTED_STT_FILES = 8
EXPECTED_LARGE_FILES = 3
EXPECTED_PDF_ENCRYPTION_DISPOSITIONS = {
    "not_encryption_marked": 81,
    "empty_credential_openable": 142,
    "configured_credential_openable": 0,
    "credential_required": 0,
    "credential_invalid": 0,
}
EXPECTED_MEDIA_RUNTIME_PROVENANCE = {
    "schema_version": "faster-whisper-runtime-provenance-v2",
    "adapter": "faster-whisper",
    "adapter_contract_version": "idis-faster-whisper-adapter-v1",
    "runtime_package_version": "fictional-version-1",
    "model_source": "local",
    "model_files_sha256": hashlib.sha256(b"fictional-model").hexdigest(),
    "cpu_threads": 4,
    "num_workers": 1,
    "config_sha256": hashlib.sha256(b"fictional-config").hexdigest(),
}
FIFTY_MIB = 50 * 1024 * 1024
COMPLETION_REASON_BY_EXTENSION = {
    "pdf": "pdf_parse_completed",
    "xlsx": "xlsx_parse_completed",
    "docx": "docx_parse_completed",
    "pptx": "pptx_parse_completed",
    "html": "text_parse_completed",
    "txt": "text_parse_completed",
    "png": "image_ocr_completed",
    "mp4": "media_transcription_completed",
}
PARSED_DOC_TYPE_BY_EXTENSION = {
    "pdf": "PDF",
    "xlsx": "XLSX",
    "docx": "DOCX",
    "pptx": "PPTX",
    "html": "HTML",
    "txt": "TEXT",
    "png": "IMAGE",
    "mp4": "VIDEO",
}


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _uuid(company_number: int, sequence: int, namespace: int) -> str:
    value = namespace * 100_000 + company_number * 10_000 + sequence + 1
    return str(uuid.UUID(int=value))


def _source_companies() -> tuple[CompanySourceContract, CompanySourceContract]:
    companies: list[CompanySourceContract] = []
    for company_number, (company_alias, format_counts) in enumerate(
        EXPECTED_COMPANY_FORMATS.items(),
        start=1,
    ):
        extensions = [extension for extension, count in format_counts.items() for _ in range(count)]
        large_media_remaining = EXPECTED_LARGE_FILES if company_number == 1 else 0
        files: list[SourceFileContract] = []
        for sequence, extension in enumerate(extensions):
            is_large = extension == "mp4" and large_media_remaining > 0
            if is_large:
                large_media_remaining -= 1
            size_bytes = FIFTY_MIB + sequence + 1 if is_large else 1_000 + sequence
            files.append(
                SourceFileContract(
                    sequence=sequence,
                    generated_upload_name=(
                        f"{company_alias}-document-{sequence + 1:05d}.{extension}"
                    ),
                    source_locator_sha256=_digest(f"fictional-locator:{company_alias}:{sequence}"),
                    content_sha256=_digest(f"fictional-content:{company_alias}:{sequence}"),
                    size_bytes=size_bytes,
                    extension=extension,
                )
            )
        suffix = company_alias.removeprefix("company-")
        companies.append(
            CompanySourceContract(
                company_alias=company_alias,
                deal_alias=f"deal-{suffix}",
                document_set_alias=f"documents-{suffix}",
                run_alias=f"run-{suffix}",
                ledger_alias=f"ledger-{suffix}",
                package_alias=f"package-{suffix}",
                source_company_locator_sha256=_digest(f"fictional-company-locator:{company_alias}"),
                file_count=len(files),
                total_bytes=sum(row.size_bytes for row in files),
                source_contract_sha256=_digest(f"fictional-contract:{company_alias}"),
                files=tuple(files),
            )
        )
    return companies[0], companies[1]


def _expected_pdf_ocr_hashes(
    companies: Sequence[CompanySourceContract],
) -> frozenset[str]:
    pdf_hashes = [
        source.content_sha256
        for company in companies
        for source in company.files
        if source.extension == "pdf"
    ]
    return frozenset(pdf_hashes[:EXPECTED_PDF_OCR_FILES])


def _terminal_rows(
    companies: Sequence[CompanySourceContract],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pdf_index = 0
    expected_pdf_ocr_hashes = _expected_pdf_ocr_hashes(companies)
    for company_number, company in enumerate(companies, start=1):
        deal_id = _uuid(company_number, 0, 1)
        for source in company.files:
            document_id = _uuid(company_number, source.sequence, 2)
            artifact_id = _uuid(company_number, source.sequence, 3)
            is_ocr = source.extension == "png" or (
                source.extension == "pdf" and source.content_sha256 in expected_pdf_ocr_hashes
            )
            is_stt = source.extension == "mp4"
            is_large = source.size_bytes > FIFTY_MIB
            span_type = "TIMECODE" if is_stt else "PAGE_TEXT"
            locator: dict[str, Any] = (
                {"start_ms": 0, "end_ms": 1_000}
                if is_stt
                else {
                    "locator_sha256": _digest(
                        f"fictional-safe-locator:{company.company_alias}:{source.sequence}"
                    )
                }
            )
            rows.append(
                {
                    "company_alias": company.company_alias,
                    "deal_id": deal_id,
                    "source_sequence": source.sequence,
                    "generated_upload_name": source.generated_upload_name,
                    "document_id": document_id,
                    "doc_id": artifact_id,
                    "content_sha256": source.content_sha256,
                    "size_bytes": source.size_bytes,
                    "extension": source.extension,
                    "parsed_doc_type": PARSED_DOC_TYPE_BY_EXTENSION[source.extension],
                    "terminal_status": "SUCCEEDED",
                    "file_status": "SUPPORTED",
                    "support_status": "supported",
                    "triage_status": "ready",
                    "parse_status": "PARSED",
                    "reason_codes": [COMPLETION_REASON_BY_EXTENSION[source.extension]],
                    "warning_codes": [],
                    "error_codes": [],
                    "ocr_performed": is_ocr,
                    **(
                        {
                            "ocr_evidence": {
                                "schema_version": "ocr-terminal-evidence-v2",
                                "ocr_page_count": 1,
                                "ocr_span_count": 1,
                                "confidence_count": 1,
                                "mean_confidence": 0.8,
                                "minimum_confidence": 0.8,
                                "maximum_confidence": 0.8,
                                "locator_contract_sha256": _digest(
                                    "fictional-ocr-locator-contract:"
                                    f"{company.company_alias}:{source.sequence}"
                                ),
                                "ocr_rescue_page_count": 0,
                                "ocr_rescue_span_count": 0,
                                "ocr_rescue_receipt_sha256": hashlib.sha256(b"[]").hexdigest(),
                            }
                        }
                        if is_ocr
                        else {}
                    ),
                    "media_transcription_performed": is_stt,
                    **(
                        {"media_runtime_provenance": EXPECTED_MEDIA_RUNTIME_PROVENANCE.copy()}
                        if is_stt
                        else {}
                    ),
                    "bounded_streaming_verified": is_large,
                    "durable_object_integrity": {
                        "schema_version": "durable-object-integrity-v1",
                        "content_sha256": source.content_sha256,
                        "size_bytes": source.size_bytes,
                        "uri_sha256": _digest(
                            f"fictional-uri:{company.company_alias}:{source.sequence}"
                        ),
                        "version_id": _uuid(company_number, source.sequence, 5),
                        "backend": "compliant:filesystem",
                        "streaming_verified": True,
                    },
                    **(
                        {
                            "pdf_encryption_disposition": (
                                "not_encryption_marked"
                                if pdf_index < 81
                                else "empty_credential_openable"
                            )
                        }
                        if source.extension == "pdf"
                        else {}
                    ),
                    "durable_spans": [
                        {
                            "span_id": _uuid(company_number, source.sequence, 4),
                            "document_id": document_id,
                            "span_type": span_type,
                            "locator": locator,
                            "content_hash": _digest(
                                f"fictional-span:{company.company_alias}:{source.sequence}"
                            ),
                        }
                    ],
                }
            )
            pdf_index += int(source.extension == "pdf")
    return rows


def _target_module() -> ModuleType:
    try:
        return importlib.import_module("idis.evaluation.real_example_terminal_reconciliation")
    except ModuleNotFoundError:
        pytest.fail(
            "Task 4 terminal-ingestion reconciler is not implemented",
            pytrace=False,
        )


def _reconcile(
    companies: Sequence[CompanySourceContract],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    module = _target_module()
    return module.reconcile_terminal_ingestion(
        source_companies=companies,
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
        expected_pdf_ocr_content_sha256s=_expected_pdf_ocr_hashes(companies),
    )


def _assert_blocked(
    companies: Sequence[CompanySourceContract],
    rows: Sequence[Mapping[str, Any]],
    *,
    code: str = "DATA_ROOM_RECONCILIATION_INCOMPLETE",
) -> None:
    module = _target_module()
    with pytest.raises(module.TerminalIngestionReconciliationError) as caught:
        _reconcile(companies, rows)
    assert caught.value.code == code


def test_exact_267_terminal_rows_reconcile_by_company_format_and_runtime_proof() -> None:
    # The baseline success row now carries both required media receipts; the
    # helper intentionally chooses a split that is not a real-source oracle.
    from tests.slice100_task4.test_media_dual_track_terminal import _dual_track_rows

    companies, rows = _dual_track_rows(lambda _index: (True, False))

    summary = _reconcile(companies, rows)

    # Plan §1.11 makes reconciliation v4 current after classifier admission.
    assert summary == {
        "schema_version": "slice100-terminal-ingestion-reconciliation-v4",
        "status": "ready",
        "total_file_count": 267,
        "counts_by_company": {"company-001": 58, "company-002": 209},
        "counts_by_extension": EXPECTED_FORMAT_COUNTS,
        "parsed_file_count": 267,
        "parsed_span_bearing_file_count": 267,
        "confirmed_no_substantive_content_file_count": 0,
        "confirmed_no_substantive_content_variant_counts": {
            "blank": 0,
            "illegible": 0,
            "watermark_only": 0,
        },
        "confirmed_no_substantive_content_receipts_sha256": hashlib.sha256(b"[]").hexdigest(),
        "manual_v1_confirmed_document_count": 0,
        "watermark_classifier_document_count": 0,
        "watermark_manifest_bound_receipt_count": 0,
        "watermark_manifest_runtime_summary_sha256": hashlib.sha256(b"{}").hexdigest(),
        "watermark_classified_page_count": 0,
        "watermark_batch_attested_page_count": 0,
        "watermark_runtime_unseen_page_count": 0,
        "watermark_classified_pages_by_company": {"company-001": 0, "company-002": 0},
        "runtime_review_required": False,
        "watermark_classification_receipts_sha256": hashlib.sha256(b"[]").hexdigest(),
        "durable_span_file_count": 267,
        "durable_object_file_count": 267,
        "object_store_backend": "compliant:filesystem",
        "ocr_file_count": 44,
        "pdf_ocr_file_count": 42,
        "pdf_ocr_contract_sha256": _digest(
            json.dumps(
                sorted(_expected_pdf_ocr_hashes(companies)),
                separators=(",", ":"),
            )
        ),
        "stt_file_count": 8,
        "media_dual_receipt_file_count": 8,
        "stt_positive_file_count": 8,
        "frame_ocr_positive_file_count": 0,
        "large_streamed_file_count": 3,
        "pdf_encryption_dispositions": EXPECTED_PDF_ENCRYPTION_DISPOSITIONS,
        "media_runtime_provenance_sha256": _digest(
            json.dumps(
                EXPECTED_MEDIA_RUNTIME_PROVENANCE,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    }


def test_fixture_itself_pins_the_approved_safe_aggregate_census() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)

    assert [company.file_count for company in companies] == [58, 209]
    assert Counter(row["extension"] for row in rows) == Counter(EXPECTED_FORMAT_COUNTS)
    assert sum(bool(row["ocr_performed"]) for row in rows) == 44
    assert sum(row["extension"] == "pdf" and bool(row["ocr_performed"]) for row in rows) == 42
    assert sum(bool(row["media_transcription_performed"]) for row in rows) == 8
    assert sum(bool(row["bounded_streaming_verified"]) for row in rows) == 3


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("document_id", None),
        ("doc_id", None),
        ("content_sha256", None),
    ],
)
def test_duplicate_document_artifact_or_content_hash_blocks(
    field: str,
    replacement: None,
) -> None:
    del replacement
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[1][field] = rows[0][field]

    _assert_blocked(companies, rows)


def test_missing_terminal_row_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)

    _assert_blocked(companies, rows[:-1])


def test_extra_terminal_row_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows.append(copy.deepcopy(rows[-1]))
    rows[-1]["document_id"] = str(uuid.uuid4())
    rows[-1]["doc_id"] = str(uuid.uuid4())

    _assert_blocked(companies, rows)


def test_cross_deal_membership_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    second_company_row = next(row for row in rows if row["company_alias"] == "company-002")
    second_company_row["deal_id"] = rows[0]["deal_id"]

    _assert_blocked(companies, rows)


def test_source_hash_drift_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[0]["content_sha256"] = _digest("fictional-drift")

    _assert_blocked(companies, rows)


def test_format_census_drift_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[0]["extension"] = "txt"

    _assert_blocked(companies, rows)


@pytest.mark.parametrize("replacement", [None, "HTML"])
def test_canonical_parsed_document_type_drift_blocks(replacement: str | None) -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    if replacement is None:
        rows[0].pop("parsed_doc_type")
    else:
        rows[0]["parsed_doc_type"] = replacement

    _assert_blocked(companies, rows)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("terminal_status", "FAILED"),
        ("terminal_status", "PARTIAL"),
        ("terminal_status", "DEFERRED"),
        ("file_status", "FAILED"),
        ("file_status", "PARTIAL"),
        ("file_status", "DEFERRED"),
        ("support_status", "partially_supported"),
        ("triage_status", "partial"),
        ("parse_status", "FAILED"),
    ],
)
def test_any_non_success_internal_state_blocks(field: str, value: str) -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[0][field] = value

    _assert_blocked(companies, rows)


@pytest.mark.parametrize("warning_codes", [None, ["fictional_parser_warning"]])
def test_missing_or_nonempty_parser_warning_proof_blocks(warning_codes: object) -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    if warning_codes is None:
        rows[0].pop("warning_codes")
    else:
        rows[0]["warning_codes"] = warning_codes

    _assert_blocked(companies, rows)


def test_empty_durable_spans_block_even_when_parse_status_says_parsed() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[0]["durable_spans"] = []

    _assert_blocked(companies, rows)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("content_sha256", _digest("fictional-drifted-object")),
        ("size_bytes", 0),
        ("uri_sha256", "not-a-digest"),
        ("version_id", "not-a-version"),
        ("backend", "inmemory"),
        ("streaming_verified", False),
    ],
)
def test_missing_or_drifted_durable_object_integrity_proof_blocks(
    field: str,
    replacement: object,
) -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[0]["durable_object_integrity"][field] = replacement

    _assert_blocked(companies, rows)


def test_missing_durable_object_integrity_proof_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[0].pop("durable_object_integrity")

    _assert_blocked(companies, rows)


@pytest.mark.parametrize("field", ["version_id", "uri_sha256"])
def test_durable_object_identity_proofs_must_be_unique(field: str) -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[1]["durable_object_integrity"][field] = rows[0]["durable_object_integrity"][field]

    _assert_blocked(companies, rows)


def test_durable_object_rows_must_share_one_backend() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[0]["durable_object_integrity"]["backend"] = "compliant:s3"

    _assert_blocked(companies, rows)


def test_span_bound_to_another_document_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[0]["durable_spans"][0]["document_id"] = rows[1]["document_id"]

    _assert_blocked(companies, rows)


def test_png_without_real_ocr_proof_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    png = next(row for row in rows if row["extension"] == "png")
    png["ocr_performed"] = False

    _assert_blocked(companies, rows)


def test_source_bound_pdf_ocr_requirement_cannot_be_skipped_or_swapped() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    required = next(
        row for row in rows if row["extension"] == "pdf" and row["ocr_performed"] is True
    )
    not_required = next(
        row for row in rows if row["extension"] == "pdf" and row["ocr_performed"] is False
    )
    required["ocr_performed"] = False
    not_required["ocr_performed"] = True

    _assert_blocked(companies, rows)


def test_ocr_row_without_collector_confidence_and_locator_proof_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    ocr_row = next(row for row in rows if row["ocr_performed"] is True)
    ocr_row.pop("ocr_evidence")

    _assert_blocked(companies, rows)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mean_confidence", float("nan")),
        ("minimum_confidence", -0.01),
        ("maximum_confidence", 1.01),
        ("confidence_count", 0),
        ("ocr_page_count", 0),
        ("ocr_span_count", 0),
        ("locator_contract_sha256", "not-a-digest"),
    ],
)
def test_invalid_ocr_confidence_or_locator_evidence_blocks(field: str, value: object) -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    ocr_row = next(row for row in rows if row["ocr_performed"] is True)
    ocr_row["ocr_evidence"][field] = value

    _assert_blocked(companies, rows)


def test_pdf_ocr_confidence_page_cardinality_must_match_ocr_spans() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    pdf = next(row for row in rows if row["extension"] == "pdf" and row["ocr_performed"] is True)
    pdf["ocr_evidence"]["confidence_count"] = 2

    _assert_blocked(companies, rows)


def test_ocr_safe_receipt_rejects_unreduced_or_junk_locator() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    ocr_row = next(row for row in rows if row["ocr_performed"] is True)
    ocr_row["durable_spans"][0]["locator"] = {"page": 1, "line": 1, "junk": True}

    _assert_blocked(companies, rows)


def test_mp4_without_real_stt_or_timecode_proof_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    mp4 = next(row for row in rows if row["extension"] == "mp4")
    mp4["media_transcription_performed"] = False
    mp4["durable_spans"][0]["span_type"] = "PAGE_TEXT"

    _assert_blocked(companies, rows)


def test_overlapping_media_timecodes_block_terminal_reconciliation() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    mp4 = next(row for row in rows if row["extension"] == "mp4")
    prior = mp4["durable_spans"][0]
    mp4["durable_spans"].append(
        {
            "span_id": str(uuid.uuid4()),
            "document_id": mp4["document_id"],
            "span_type": "TIMECODE",
            "locator": {"start_ms": 500, "end_ms": 1_500},
            "content_hash": _digest("fictional-overlapping-media-span"),
        }
    )
    assert prior["locator"] == {"start_ms": 0, "end_ms": 1_000}

    _assert_blocked(companies, rows)


def test_large_file_without_bounded_streaming_proof_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    large = next(row for row in rows if row["size_bytes"] > FIFTY_MIB)
    large["bounded_streaming_verified"] = False

    _assert_blocked(companies, rows)


def test_old_file_too_large_row_is_a_hard_admission_blocker() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    large = next(row for row in rows if row["size_bytes"] > FIFTY_MIB)
    large["terminal_status"] = "FAILED"
    large["parse_status"] = "FAILED"
    large["reason_codes"] = ["file_too_large"]
    large["error_codes"] = ["max_size_exceeded"]

    _assert_blocked(
        companies,
        rows,
        code="INGEST_ADMISSION_MAX_SIZE_EXCEEDED",
    )


def test_duplicate_source_sequence_within_company_blocks() -> None:
    companies = _source_companies()
    rows = _terminal_rows(companies)
    rows[1]["source_sequence"] = rows[0]["source_sequence"]

    _assert_blocked(companies, rows)
