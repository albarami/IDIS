"""Terminal reconciliation contracts for classifier-first PDF render admission."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from idis.evaluation import real_example_terminal_reconciliation as reconciliation
from idis.evaluation.real_example_source_package import CompanySourceContract
from idis.parsers.watermark_page_classifier import (
    WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION,
    WATERMARK_RUNTIME_CANDIDATE_RENDER_POLICY_ID,
    WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN,
    default_watermark_runtime_candidate_render_policy,
)
from tests.slice100_task4.test_durable_spans_api import _ocr_collector_fixture
from tests.slice100_task4.test_media_dual_track_terminal import _dual_track_rows
from tests.slice100_task4.test_terminal_reconciliation import (
    EXPECTED_COMPANY_COUNTS,
    EXPECTED_FORMAT_COUNTS,
    EXPECTED_LARGE_FILES,
    EXPECTED_MEDIA_RUNTIME_PROVENANCE,
    EXPECTED_PDF_ENCRYPTION_DISPOSITIONS,
    EXPECTED_STT_FILES,
    EXPECTED_TOTAL_FILES,
    FIFTY_MIB,
)
from tests.slice100_task4.test_watermark_cap_reconciliation_red import (
    _manifest_runtime_summary_for_rows,
)
from tests.slice100_task4.test_watermark_terminal_receipt_red import (
    _classification,
    _receipt_v2,
)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _render_admission(page: int = 2) -> dict[str, object]:
    return {
        "page": page,
        "schema_version": "ocr-render-admission-v2",
        "ladder_version": "ocr-dpi-ladder-v1",
        "configured_dpi": 200,
        "chosen_dpi": 120,
        "projected_width": 2_500,
        "projected_height": 2_400,
        "projected_pixel_count": 6_000_000,
        "expected_render_width": 2_500,
        "expected_render_height": 2_400,
        "expected_render_pixel_count": 6_000_000,
        "actual_render_width": 2_500,
        "actual_render_height": 2_400,
        "actual_render_pixel_count": 6_000_000,
        "poppler_version": "25.12.0",
        "poppler_runtime_identity_sha256": (
            "8ebfb07d9a8e5e01c12cc2994837a122cc6c94a930c657e10ffc655db8f0cc72"
        ),
        "expected_dimension_policy_id": "poppler-pdftoppm-25.12.0-dimensions-v1",
        "expected_dimension_policy_sha256": (
            "483ee7542fbcb6a69dc3949072ef2897471130e409cc41318395e72fec3e0253"
        ),
        "comparison_mode": "exact-poppler-25.12.0",
        "width_delta_pixels": 0,
        "height_delta_pixels": 0,
        "tolerance_pixels": 0,
        "tolerance_used": False,
        "max_pixel_count": 20_000_000,
    }


def _preocr_classification(
    *,
    content_sha256: str,
    page_number: int = 1,
) -> dict[str, object]:
    legacy = _classification(
        batch_membership="ATTESTED",
        page_number=page_number,
        content_sha256=content_sha256,
    )
    return {
        "schema_version": WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION,
        "classification": "watermark_only",
        "classification_phase": "pre_ocr",
        "full_resolution_ocr_attempted": False,
        "content_sha256": content_sha256,
        "page_number": page_number,
        "signed_exemplar_policy_id": legacy["policy_id"],
        "signed_exemplar_policy_sha256": legacy["policy_sha256"],
        "runtime_candidate_policy_id": WATERMARK_RUNTIME_CANDIDATE_RENDER_POLICY_ID,
        "runtime_candidate_policy_sha256": (
            default_watermark_runtime_candidate_render_policy().policy_sha256
        ),
        "exemplar_reference": legacy["exemplar_reference"],
        "transform_chain": list(WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN),
        "actual_width_pixels": 512,
        "actual_height_pixels": 512,
        "actual_mode": "L",
        "confidence_distribution": legacy["confidence_distribution"],
        "transform_bounds": legacy["transform_bounds"],
        "similarity_metric": legacy["similarity_metric"],
        "similarity_score": legacy["similarity_score"],
        "similarity_numerator": legacy["similarity_numerator"],
        "similarity_denominator": legacy["similarity_denominator"],
        "canonical_render_sha256": legacy["canonical_render_sha256"],
        "batch_membership": "ATTESTED",
        "confirmation_source": "reviewer_batch_identity",
        "post_run_review_required": False,
        "review_status": "BATCH_ATTESTED",
        "batch_candidate_canonical_render_sha256": legacy["canonical_render_sha256"],
        "batch_candidate_similarity_score": legacy["similarity_score"],
    }


def _receipt_for_row(
    row: Mapping[str, Any],
    *,
    whole_document_terminal: bool,
) -> dict[str, Any]:
    classification = _preocr_classification(content_sha256=str(row["content_sha256"]))
    receipt = _receipt_v2(
        whole_document_terminal=whole_document_terminal,
        classifications=[classification],
    )
    receipt.update(
        {
            "safe_alias": row["generated_upload_name"],
            "content_sha256": row["content_sha256"],
            "page_count": 1 if whole_document_terminal else 2,
            "classified_page_count": 1,
            "substantive_span_count": 0 if whole_document_terminal else 1,
            "whole_document_terminal": whole_document_terminal,
            "watermark_page_classifications_sha256": _canonical_sha256([classification]),
        }
    )
    return receipt


def _v4_evidence(
    *,
    whole_document_terminal: bool,
) -> dict[str, object]:
    admissions = [] if whole_document_terminal else [_render_admission()]
    return {
        "schema_version": "ocr-terminal-evidence-v4",
        "ocr_page_count": 0 if whole_document_terminal else 1,
        "ocr_page_numbers": [] if whole_document_terminal else [2],
        "preclassified_page_numbers": [1],
        "ocr_required_page_count": 1 if whole_document_terminal else 2,
        "ocr_span_count": 0 if whole_document_terminal else 1,
        "confidence_count": 0 if whole_document_terminal else 1,
        "mean_confidence": None if whole_document_terminal else 0.8,
        "minimum_confidence": None if whole_document_terminal else 0.8,
        "maximum_confidence": None if whole_document_terminal else 0.8,
        "locator_contract_sha256": _canonical_sha256([]),
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": _canonical_sha256([]),
        "ocr_render_admissions": admissions,
        "ocr_render_admissions_sha256": _canonical_sha256(admissions),
    }


def _fresh_rows(
    *,
    whole_document_terminal: bool,
) -> tuple[
    Sequence[CompanySourceContract],
    list[dict[str, Any]],
    dict[str, Any],
    frozenset[str],
]:
    companies, rows = _dual_track_rows(lambda _index: (True, False))
    row = next(
        candidate
        for candidate in rows
        if candidate["extension"] == "pdf" and candidate["ocr_performed"] is True
    )
    receipt = _receipt_for_row(
        row,
        whole_document_terminal=whole_document_terminal,
    )
    row["document_no_substantive_content_receipt"] = receipt
    row["ocr_evidence"] = _v4_evidence(whole_document_terminal=whole_document_terminal)
    if whole_document_terminal:
        row.update(
            {
                "terminal_status": "CONFIRMED_EXCLUSION",
                "file_status": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
                "support_status": "scanned_or_image_only",
                "triage_status": "excluded",
                "parse_status": "FAILED",
                "reason_codes": ["DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"],
                "warning_codes": [],
                "error_codes": ["ocr_no_text_extracted"],
                "ocr_performed": False,
                "durable_spans": [],
            }
        )
    expected_hashes = frozenset(
        str(candidate["content_sha256"])
        for candidate in rows
        if candidate["extension"] == "pdf" and candidate["ocr_performed"] is True
    )
    assert len(expected_hashes) == (41 if whole_document_terminal else 42)
    return companies, rows, row, expected_hashes


def _reconcile(
    companies: Sequence[CompanySourceContract],
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_hashes: frozenset[str],
) -> dict[str, Any]:
    return reconciliation.reconcile_terminal_ingestion(
        source_companies=companies,
        terminal_rows=rows,
        expected_company_counts=EXPECTED_COMPANY_COUNTS,
        expected_format_counts=EXPECTED_FORMAT_COUNTS,
        expected_total_files=EXPECTED_TOTAL_FILES,
        expected_ocr_files=len(expected_hashes) + 2,
        expected_stt_files=EXPECTED_STT_FILES,
        expected_large_files=EXPECTED_LARGE_FILES,
        large_file_threshold_bytes=FIFTY_MIB,
        expected_pdf_encryption_dispositions=EXPECTED_PDF_ENCRYPTION_DISPOSITIONS,
        expected_media_runtime_provenance=EXPECTED_MEDIA_RUNTIME_PROVENANCE,
        expected_pdf_ocr_content_sha256s=expected_hashes,
        verified_watermark_manifest_runtime_summary=(_manifest_runtime_summary_for_rows(rows)),
    )


def test_v2_mixed_parsed_parent_reconciles_disjoint_ocr_and_preclassified_pages() -> None:
    companies, rows, row, expected_hashes = _fresh_rows(whole_document_terminal=False)

    summary = _reconcile(companies, rows, expected_hashes=expected_hashes)

    assert row["parse_status"] == "PARSED"
    assert row["error_codes"] == []
    assert summary["parsed_file_count"] == 267
    assert summary["confirmed_no_substantive_content_file_count"] == 0
    assert summary["watermark_classifier_document_count"] == 1
    assert summary["watermark_classified_page_count"] == 1
    assert summary["pdf_ocr_file_count"] == 42


def test_v2_whole_terminal_uses_real_preocr_error_code_without_claiming_ocr() -> None:
    companies, rows, row, expected_hashes = _fresh_rows(whole_document_terminal=True)

    summary = _reconcile(companies, rows, expected_hashes=expected_hashes)

    assert row["error_codes"] == ["ocr_no_text_extracted"]
    assert row["ocr_performed"] is False
    assert summary["parsed_file_count"] == 266
    assert summary["confirmed_no_substantive_content_file_count"] == 1
    assert str(row["content_sha256"]) not in expected_hashes
    assert summary["ocr_file_count"] == 43
    assert summary["pdf_ocr_file_count"] == 41


def test_preclassified_only_pdf_cannot_be_counted_as_an_ocr_execution() -> None:
    companies, rows, row, expected_hashes = _fresh_rows(whole_document_terminal=True)
    drifted_hashes = expected_hashes | {str(row["content_sha256"])}

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile(companies, rows, expected_hashes=drifted_hashes)

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence.update(ocr_render_admissions=[]),
        lambda evidence: evidence.update(ocr_render_admissions_sha256="0" * 64),
        lambda evidence: evidence.update(ocr_page_numbers=[1]),
        lambda evidence: evidence.update(preclassified_page_numbers=[2]),
        lambda evidence: evidence.update(ocr_required_page_count=3),
    ],
)
def test_fresh_v4_missing_drifted_or_overlapping_page_admission_blocks(
    mutation: Any,
) -> None:
    companies, rows, row, expected_hashes = _fresh_rows(whole_document_terminal=False)
    mutation(row["ocr_evidence"])

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile(companies, rows, expected_hashes=expected_hashes)

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_span_and_metadata_admissions_are_bound_before_terminal_projection() -> None:
    document_id = "10000000-0000-4000-8000-000000000001"
    text = "fictional admitted OCR evidence"
    span_page = {
        "total": 1,
        "items": [
            {
                "span_id": "20000000-0000-4000-8000-000000000001",
                "document_id": document_id,
                "span_type": "PAGE_TEXT",
                "text_excerpt": text,
                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                "locator": {
                    "page": 2,
                    "line": 1,
                    "source": "ocr",
                    "ocr_render_admission": {
                        key: value for key, value in _render_admission().items() if key != "page"
                    },
                },
            }
        ],
    }
    _spans, locator_proof = reconciliation._safe_durable_span_receipts(
        span_page,
        document_id=document_id,
        is_media=False,
        extension="pdf",
        ocr_performed=True,
    )
    assert locator_proof is not None
    admission = _render_admission()
    metadata = {
        "page_count": 2,
        "ocr_page_count": 1,
        "ocr_required_page_count": 2,
        "ocr_confidence_by_page": [0.9],
        "ocr_mean_confidence": 0.9,
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": _canonical_sha256([]),
        "ocr_render_admissions": [admission],
        "ocr_render_admissions_sha256": _canonical_sha256([admission]),
    }
    receipt = {
        "schema_version": "document-no-substantive-content-receipt-v2",
        "watermark_page_classifications": [
            {
                "schema_version": WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION,
                "page_number": 1,
            }
        ],
    }

    evidence = reconciliation._safe_ocr_evidence(
        metadata,
        extension="pdf",
        locator_proof=locator_proof,
        watermark_receipt=receipt,
    )

    assert evidence["schema_version"] == "ocr-terminal-evidence-v4"
    assert evidence["ocr_page_numbers"] == [2]
    assert evidence["preclassified_page_numbers"] == [1]
    assert evidence["ocr_render_admissions"] == [admission]


def test_fresh_page_rejects_one_ocr_span_missing_its_admission() -> None:
    document_id = "10000000-0000-4000-8000-000000000001"
    admission = _render_admission()
    items: list[dict[str, object]] = []
    for line in (1, 2):
        text = f"fictional OCR line {line}"
        locator: dict[str, object] = {"page": 2, "line": line, "source": "ocr"}
        if line == 1:
            locator["ocr_render_admission"] = {
                key: value for key, value in admission.items() if key != "page"
            }
        items.append(
            {
                "span_id": f"20000000-0000-4000-8000-00000000000{line}",
                "document_id": document_id,
                "span_type": "PAGE_TEXT",
                "text_excerpt": text,
                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                "locator": locator,
            }
        )
    _spans, locator_proof = reconciliation._safe_durable_span_receipts(
        {"total": 2, "items": items},
        document_id=document_id,
        is_media=False,
        extension="pdf",
        ocr_performed=True,
    )
    receipt = {
        "schema_version": "document-no-substantive-content-receipt-v2",
        "watermark_page_classifications": [
            {
                "schema_version": WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION,
                "page_number": 1,
            }
        ],
    }
    metadata = {
        "page_count": 2,
        "ocr_page_count": 1,
        "ocr_required_page_count": 2,
        "ocr_confidence_by_page": [0.9],
        "ocr_mean_confidence": 0.9,
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": _canonical_sha256([]),
        "ocr_render_admissions": [admission],
        "ocr_render_admissions_sha256": _canonical_sha256([admission]),
    }

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError):
        reconciliation._safe_ocr_evidence(
            metadata,
            extension="pdf",
            locator_proof=locator_proof,
            watermark_receipt=receipt,
        )


def test_collector_preserves_mixed_parent_and_does_not_fabricate_old_error_code() -> None:
    companies, summary, api = _ocr_collector_fixture(extension="pdf")
    source = companies[0].files[0]
    document_url = next(
        url for url in api.responses if "/v1/deals/" in url and "/documents/" in url
    )
    span_url = next(url for url in api.responses if url.endswith("/spans"))
    document = api.responses[document_url]
    receipt = _receipt_for_row(
        {
            "generated_upload_name": source.generated_upload_name,
            "content_sha256": source.content_sha256,
        },
        whole_document_terminal=False,
    )
    receipt["page_count"] = 3
    receipt["substantive_span_count"] = 2
    admission = _render_admission(page=3)
    document["metadata"].update(
        {
            "page_count": 3,
            "ocr_page_count": 1,
            "ocr_required_page_count": 2,
            "ocr_confidence_by_page": [0.7],
            "ocr_mean_confidence": 0.7,
            "ocr_render_admissions": [admission],
            "ocr_render_admissions_sha256": _canonical_sha256([admission]),
            "document_no_substantive_content_receipt": receipt,
        }
    )
    api.responses[span_url]["items"] = api.responses[span_url]["items"][1:]
    api.responses[span_url]["items"][1]["locator"]["ocr_render_admission"] = {
        key: value for key, value in admission.items() if key != "page"
    }
    api.responses[span_url]["total"] = 2

    rows = reconciliation.collect_durable_terminal_rows(
        api_client=api,
        api_key="fictional-acceptance-key",
        source_companies=companies,
        preparation_summary=summary,
        large_file_threshold_bytes=50 * 1024 * 1024,
    )

    assert len(rows) == 1
    assert rows[0]["terminal_status"] == "SUCCEEDED"
    assert rows[0]["parse_status"] == "PARSED"
    assert rows[0]["error_codes"] == []
    assert rows[0]["ocr_evidence"]["schema_version"] == "ocr-terminal-evidence-v4"
    assert rows[0]["ocr_evidence"]["ocr_page_numbers"] == [3]
    assert rows[0]["ocr_evidence"]["preclassified_page_numbers"] == [1]


def test_collector_uses_preocr_terminal_code_for_whole_classifier_receipt() -> None:
    companies, summary, api = _ocr_collector_fixture(extension="pdf")
    source = companies[0].files[0]
    document_url = next(
        url for url in api.responses if "/v1/deals/" in url and "/documents/" in url
    )
    span_url = next(url for url in api.responses if url.endswith("/spans"))
    document = api.responses[document_url]
    receipt = _receipt_for_row(
        {
            "generated_upload_name": source.generated_upload_name,
            "content_sha256": source.content_sha256,
        },
        whole_document_terminal=True,
    )
    document["parse_status"] = "FAILED"
    document["metadata"].update(
        {
            "page_count": 1,
            "ocr_performed": False,
            "ocr_page_count": 0,
            "ocr_required_page_count": 1,
            "ocr_render_admissions": [],
            "ocr_render_admissions_sha256": _canonical_sha256([]),
            "parse_error_codes": ["ocr_no_text_extracted"],
            "document_no_substantive_content_receipt": receipt,
        }
    )
    api.responses[span_url] = {"items": [], "total": 0}

    rows = reconciliation.collect_durable_terminal_rows(
        api_client=api,
        api_key="fictional-acceptance-key",
        source_companies=companies,
        preparation_summary=summary,
        large_file_threshold_bytes=50 * 1024 * 1024,
    )

    assert len(rows) == 1
    assert rows[0]["terminal_status"] == "CONFIRMED_EXCLUSION"
    assert rows[0]["error_codes"] == ["ocr_no_text_extracted"]
    assert rows[0]["ocr_performed"] is False
    assert rows[0]["ocr_evidence"]["ocr_page_numbers"] == []
    assert rows[0]["ocr_evidence"]["preclassified_page_numbers"] == [1]


def test_explicit_legacy_v2_fixture_needs_no_retroactive_admission() -> None:
    evidence = {
        "schema_version": "ocr-terminal-evidence-v2",
        "ocr_page_count": 1,
        "ocr_span_count": 1,
        "confidence_count": 1,
        "mean_confidence": 0.8,
        "minimum_confidence": 0.8,
        "maximum_confidence": 0.8,
        "locator_contract_sha256": _canonical_sha256([]),
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": _canonical_sha256([]),
    }

    reconciliation._validate_safe_ocr_evidence(
        evidence,
        extension="pdf",
        durable_span_count=1,
    )
