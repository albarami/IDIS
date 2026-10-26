"""RED contract for one signed, non-consumable no-substantive-content disposition."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest

from idis.evaluation import real_example_terminal_reconciliation as reconciliation
from tests.slice100_task4.test_durable_spans_api import _ocr_collector_fixture
from tests.slice100_task4.test_media_dual_track_terminal import _dual_track_rows
from tests.slice100_task4.test_terminal_reconciliation import _digest, _reconcile

EMPTY_SEQUENCE_SHA256 = hashlib.sha256(b"[]").hexdigest()
SOURCE_TREE_FINGERPRINT = _digest("fictional-source-tree")


def _upgrade_standard_ocr_evidence_to_v2(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        evidence = row.get("ocr_evidence")
        if not isinstance(evidence, dict):
            continue
        evidence["schema_version"] = "ocr-terminal-evidence-v2"
        evidence["ocr_rescue_page_count"] = 0
        evidence["ocr_rescue_span_count"] = 0
        evidence["ocr_rescue_receipt_sha256"] = EMPTY_SEQUENCE_SHA256


def _confirmed_no_substantive_content_receipt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "document-no-substantive-content-receipt-v1",
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "safe_alias": row["generated_upload_name"],
        "content_sha256": row["content_sha256"],
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "attestation_text_sha256": _digest("fictional-reviewer-attestation"),
        "reviewer_id": "reviewer-fictional-001",
        "approved_at": "2026-07-17T00:00:00Z",
        "expires_at": "2026-07-18T00:00:00Z",
        "signer_id": "fictional-governance-signer",
        "payload_sha256": _digest("fictional-attestation-payload"),
        "envelope_sha256": _digest("fictional-attestation-envelope"),
        "public_key_sha256": _digest("fictional-public-key"),
        "signature_verified": True,
        "parser_error_code": "ocr_rescue_no_consensus",
        "parser_runtime": "tesseract",
        "parser_runtime_status": "completed_no_extractable_text",
        "page_count": 1,
        "ocr_required_page_count": 1,
        "primary_completed_page_count": 1,
        "rescue_completed_page_count": 1,
        "accepted_text_page_count": 0,
        "span_count": 0,
        "ocr_result_sha256": _digest("fictional-complete-zero-text-ocr-result"),
    }


def _rows_with_one_confirmed_no_substantive_content() -> tuple[
    Any, list[dict[str, Any]], dict[str, Any]
]:
    companies, rows = _dual_track_rows(lambda _index: (True, False))
    _upgrade_standard_ocr_evidence_to_v2(rows)
    row = next(
        candidate
        for candidate in rows
        if candidate["extension"] == "pdf" and candidate["ocr_performed"] is True
    )
    receipt = _confirmed_no_substantive_content_receipt(row)
    row.update(
        {
            "terminal_status": "CONFIRMED_EXCLUSION",
            "file_status": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
            "support_status": "scanned_or_image_only",
            "triage_status": "excluded",
            "parse_status": "FAILED",
            "reason_codes": ["DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"],
            "warning_codes": [],
            "error_codes": ["ocr_rescue_no_consensus"],
            "durable_spans": [],
            "document_no_substantive_content_receipt": receipt,
            "ocr_evidence": {
                "schema_version": "ocr-terminal-evidence-v2",
                "ocr_page_count": 1,
                "ocr_span_count": 0,
                "confidence_count": 0,
                "mean_confidence": None,
                "minimum_confidence": None,
                "maximum_confidence": None,
                "locator_contract_sha256": EMPTY_SEQUENCE_SHA256,
                "ocr_rescue_page_count": 0,
                "ocr_rescue_span_count": 0,
                "ocr_rescue_receipt_sha256": receipt["ocr_result_sha256"],
            },
        }
    )
    return companies, rows, row


def test_one_exact_confirmed_no_substantive_content_row_reconciles_without_becoming_parsed() -> (
    None
):
    companies, rows, row = _rows_with_one_confirmed_no_substantive_content()

    summary = _reconcile(companies, rows)

    assert row["parse_status"] == "FAILED"
    assert row["durable_spans"] == []
    # Plan §1.11 makes reconciliation v4 current while retaining manual-v1 input.
    assert summary["schema_version"] == "slice100-terminal-ingestion-reconciliation-v4"
    assert summary["total_file_count"] == 267
    assert summary["parsed_file_count"] == 266
    assert summary["parsed_span_bearing_file_count"] == 266
    assert summary["confirmed_no_substantive_content_file_count"] == 1
    assert summary["confirmed_no_substantive_content_variant_counts"] == {
        "blank": 0,
        "illegible": 0,
        "watermark_only": 1,
    }
    assert summary["durable_span_file_count"] == 266
    assert summary["durable_object_file_count"] == 267
    receipt = row["document_no_substantive_content_receipt"]
    expected_digest = hashlib.sha256(
        json.dumps([receipt], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert summary["confirmed_no_substantive_content_receipts_sha256"] == expected_digest


def test_authenticated_collector_preserves_failed_zero_span_confirmed_disposition() -> None:
    companies, summary, api = _ocr_collector_fixture(extension="pdf")
    source = companies[0].files[0]
    document_url = next(
        url for url in api.responses if "/v1/deals/" in url and "/documents/" in url
    )
    span_url = next(url for url in api.responses if url.endswith("/spans"))
    document = api.responses[document_url]
    receipt = _confirmed_no_substantive_content_receipt(
        {
            "generated_upload_name": source.generated_upload_name,
            "content_sha256": source.content_sha256,
        }
    )
    document["parse_status"] = "FAILED"
    document["metadata"].update(
        {
            "parse_error_codes": ["ocr_rescue_no_consensus"],
            "parser_runtime": "tesseract",
            "parser_runtime_status": "completed_no_extractable_text",
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
    row = rows[0]
    assert row["terminal_status"] == "CONFIRMED_EXCLUSION"
    assert row["file_status"] == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
    assert row["parse_status"] == "FAILED"
    assert row["durable_spans"] == []
    assert row["document_no_substantive_content_receipt"] == receipt
    assert row["ocr_evidence"]["ocr_span_count"] == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", "document-illegibility-receipt-v0"),
        ("disposition", "PARSED"),
        ("safe_alias", "company-001-document-99999.pdf"),
        ("content_sha256", "0" * 64),
        ("source_tree_fingerprint", "not-a-digest"),
        ("attestation_text_sha256", "not-a-digest"),
        ("reviewer_id", ""),
        ("approved_at", "not-a-time"),
        ("expires_at", "2020-01-01T00:00:00Z"),
        ("signer_id", ""),
        ("payload_sha256", "not-a-digest"),
        ("envelope_sha256", "not-a-digest"),
        ("public_key_sha256", "not-a-digest"),
        ("signature_verified", False),
        ("parser_error_code", "ocr_timeout"),
        ("parser_runtime", "synthetic"),
        ("parser_runtime_status", "failed"),
        ("page_count", 0),
        ("ocr_required_page_count", 0),
        ("primary_completed_page_count", 0),
        ("rescue_completed_page_count", 0),
        ("accepted_text_page_count", 1),
        ("span_count", 1),
        ("ocr_result_sha256", "not-a-digest"),
    ],
)
def test_confirmed_no_substantive_content_receipt_field_drift_blocks(
    field: str,
    replacement: object,
) -> None:
    companies, rows, row = _rows_with_one_confirmed_no_substantive_content()
    row["document_no_substantive_content_receipt"][field] = replacement

    with pytest.raises(Exception) as caught:
        _reconcile(companies, rows)

    assert getattr(caught.value, "code", None) == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_unsigned_failed_zero_span_row_remains_blocked() -> None:
    companies, rows, row = _rows_with_one_confirmed_no_substantive_content()
    row.pop("document_no_substantive_content_receipt")

    with pytest.raises(Exception) as caught:
        _reconcile(companies, rows)

    assert getattr(caught.value, "code", None) == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_second_confirmed_no_substantive_content_row_blocks_one_document_manifest_contract() -> (
    None
):
    companies, rows, first = _rows_with_one_confirmed_no_substantive_content()
    second = next(
        candidate
        for candidate in rows
        if candidate is not first
        and candidate["extension"] == "pdf"
        and candidate["ocr_performed"] is True
    )
    source_binding = copy.deepcopy(second)
    receipt = _confirmed_no_substantive_content_receipt(second)
    second.update(copy.deepcopy(first))
    second.update(
        {
            "company_alias": source_binding["company_alias"],
            "deal_id": source_binding["deal_id"],
            "source_sequence": source_binding["source_sequence"],
            "generated_upload_name": source_binding["generated_upload_name"],
            "document_id": source_binding["document_id"],
            "doc_id": source_binding["doc_id"],
            "content_sha256": source_binding["content_sha256"],
            "size_bytes": source_binding["size_bytes"],
            "extension": source_binding["extension"],
            "parsed_doc_type": source_binding["parsed_doc_type"],
            "durable_object_integrity": source_binding["durable_object_integrity"],
            "pdf_encryption_disposition": source_binding["pdf_encryption_disposition"],
            "document_no_substantive_content_receipt": receipt,
        }
    )

    with pytest.raises(Exception) as caught:
        _reconcile(companies, rows)

    assert getattr(caught.value, "code", None) == "DATA_ROOM_RECONCILIATION_INCOMPLETE"
