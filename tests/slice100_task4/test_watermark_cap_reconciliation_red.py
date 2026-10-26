"""RED contracts for v4 watermark reconciliation and the integer sanity cap."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from idis.evaluation import real_example_terminal_reconciliation as reconciliation
from idis.evaluation.real_example_source_package import CompanySourceContract
from tests.slice100_task4.test_media_dual_track_terminal import _dual_track_rows
from tests.slice100_task4.test_no_substantive_content_terminal_reconciliation import (
    EMPTY_SEQUENCE_SHA256,
    _rows_with_one_confirmed_no_substantive_content,
)
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
from tests.slice100_task4.test_watermark_terminal_receipt_red import (
    TENANT_ID,
    _classification,
    _digest,
    _receipt_v2,
)

TERMINAL_SCHEMA_V4 = "slice100-terminal-ingestion-reconciliation-v4"
CAP_EXCEEDED = "WATERMARK_PAGE_SANITY_CAP_EXCEEDED"
_AUTO_MANIFEST_SUMMARY = object()


def _manifest_runtime_summary_for_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, object] | None:
    """Build the exact fictional runtime view that admitted the stamped rows."""

    receipts = [
        receipt
        for row in rows
        if isinstance(
            receipt := row.get("document_no_substantive_content_receipt"),
            Mapping,
        )
        and receipt.get("schema_version") == "document-no-substantive-content-receipt-v2"
    ]
    if not receipts:
        return None
    first = receipts[0]
    first_classifications = first["watermark_page_classifications"]
    assert isinstance(first_classifications, list) and first_classifications
    first_classification = first_classifications[0]
    assert isinstance(first_classification, Mapping)
    exemplar = first_classification["exemplar_reference"]
    assert isinstance(exemplar, Mapping)

    candidates: list[dict[str, object]] = []
    for receipt in receipts:
        classifications = receipt["watermark_page_classifications"]
        assert isinstance(classifications, list)
        for classification in classifications:
            assert isinstance(classification, Mapping)
            if classification.get("batch_membership") != "ATTESTED":
                continue
            pre_ocr = classification.get("classification_phase") == "pre_ocr"
            candidates.append(
                {
                    "safe_alias": receipt["safe_alias"],
                    "content_sha256": receipt["content_sha256"],
                    "page_number": classification["page_number"],
                    "canonical_render_sha256": classification[
                        "batch_candidate_canonical_render_sha256"
                        if pre_ocr
                        else "canonical_render_sha256"
                    ],
                    "similarity_score": classification[
                        "batch_candidate_similarity_score" if pre_ocr else "similarity_score"
                    ],
                }
            )
    exemplar_id = str(exemplar["exemplar_id"])
    return {
        "schema_version": "document-no-substantive-content-attestation-manifest-v2",
        "status": "verified",
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": first["source_tree_fingerprint"],
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "classifier_policy_id": first["classifier_policy_id"],
        "classifier_policy_sha256": first["classifier_policy_sha256"],
        "exemplar_safe_alias": exemplar_id.rsplit(":page-", 1)[0],
        "exemplar_page_number": exemplar["page_number"],
        "exemplar_content_sha256": exemplar["content_sha256"],
        "exemplar_envelope_sha256": first["exemplar_envelope_sha256"],
        "exemplar_canonical_bitmap_sha256": first["exemplar_canonical_bitmap_sha256"],
        "manifest_sha256": first["manifest_sha256"],
        "batch_envelope_sha256": first["batch_envelope_sha256"],
        "candidate_set_sha256": first["candidate_set_sha256"],
        "public_key_sha256": first["public_key_sha256"],
        "candidate_count": len(candidates),
        "candidates": candidates,
        "company_file_counts": EXPECTED_COMPANY_COUNTS,
        "signature_verified": True,
    }


def _pdf_rows(
    rows: Sequence[dict[str, Any]],
    *,
    company_alias: str,
    count: int,
) -> list[dict[str, Any]]:
    selected = [
        row for row in rows if row["company_alias"] == company_alias and row["extension"] == "pdf"
    ][:count]
    assert len(selected) == count
    return selected


def _assign_exact_pdf_ocr_set(
    rows: Sequence[dict[str, Any]],
    required_rows: Sequence[dict[str, Any]],
) -> frozenset[str]:
    required_hashes = {str(row["content_sha256"]) for row in required_rows}
    all_pdf_rows = [row for row in rows if row["extension"] == "pdf"]
    remaining = [
        str(row["content_sha256"])
        for row in all_pdf_rows
        if str(row["content_sha256"]) not in required_hashes
    ]
    selected_hashes = frozenset(required_hashes | set(remaining[: 42 - len(required_hashes)]))
    assert len(selected_hashes) == 42

    for row in all_pdf_rows:
        enabled = str(row["content_sha256"]) in selected_hashes
        row["ocr_performed"] = enabled
        if enabled and "ocr_evidence" not in row:
            row["ocr_evidence"] = {
                "schema_version": "ocr-terminal-evidence-v2",
                "ocr_page_count": 1,
                "ocr_span_count": 1,
                "confidence_count": 1,
                "mean_confidence": 0.8,
                "minimum_confidence": 0.8,
                "maximum_confidence": 0.8,
                "locator_contract_sha256": hashlib.sha256(
                    f"fictional-ocr:{row['generated_upload_name']}".encode()
                ).hexdigest(),
                "ocr_rescue_page_count": 0,
                "ocr_rescue_span_count": 0,
                "ocr_rescue_receipt_sha256": EMPTY_SEQUENCE_SHA256,
            }
        if not enabled:
            row.pop("ocr_evidence", None)
    return selected_hashes


def _stamp_classifier_exclusion(
    row: dict[str, Any],
    *,
    membership: str,
    page_count: int = 1,
) -> dict[str, Any]:
    classifications = [
        _classification(
            batch_membership=membership,
            page_number=page_number,
            safe_alias=str(row["generated_upload_name"]),
            content_sha256=str(row["content_sha256"]),
        )
        for page_number in range(1, page_count + 1)
    ]
    receipt = _receipt_v2(
        batch_membership=membership,
        classifications=classifications,
    )
    receipt.update(
        {
            "safe_alias": row["generated_upload_name"],
            "content_sha256": row["content_sha256"],
            "page_count": page_count,
            "classified_page_count": page_count,
            "substantive_span_count": 0,
            "whole_document_terminal": True,
            "watermark_page_classifications_sha256": _digest(classifications),
        }
    )
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
            "ocr_performed": True,
            "durable_spans": [],
            "document_no_substantive_content_receipt": receipt,
            "ocr_evidence": {
                "schema_version": "ocr-terminal-evidence-v2",
                "ocr_page_count": page_count,
                "ocr_span_count": 0,
                "confidence_count": 0,
                "mean_confidence": None,
                "minimum_confidence": None,
                "maximum_confidence": None,
                "locator_contract_sha256": EMPTY_SEQUENCE_SHA256,
                "ocr_rescue_page_count": 0,
                "ocr_rescue_span_count": 0,
                "ocr_rescue_receipt_sha256": receipt["watermark_page_classifications_sha256"],
            },
        }
    )
    return receipt


def _reconcile_v4(
    companies: Sequence[CompanySourceContract],
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_pdf_ocr_hashes: frozenset[str] | None = None,
    verified_manifest_runtime_summary: Mapping[str, object] | None | object = (
        _AUTO_MANIFEST_SUMMARY
    ),
) -> dict[str, Any]:
    expected_hashes = expected_pdf_ocr_hashes or frozenset(
        str(row["content_sha256"])
        for row in rows
        if row["extension"] == "pdf" and row["ocr_performed"] is True
    )
    manifest_summary = (
        _manifest_runtime_summary_for_rows(rows)
        if verified_manifest_runtime_summary is _AUTO_MANIFEST_SUMMARY
        else verified_manifest_runtime_summary
    )
    assert manifest_summary is None or isinstance(manifest_summary, Mapping)
    result = reconciliation.reconcile_terminal_ingestion(
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
        verified_watermark_manifest_runtime_summary=manifest_summary,
    )
    assert isinstance(result, dict)
    return result


def _assert_code(
    companies: Sequence[CompanySourceContract],
    rows: Sequence[Mapping[str, Any]],
    *,
    code: str,
    expected_pdf_ocr_hashes: frozenset[str] | None = None,
) -> None:
    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_v4(
            companies,
            rows,
            expected_pdf_ocr_hashes=expected_pdf_ocr_hashes,
        )
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("company_alias", "classified_pages", "allowed"),
    [
        ("company-001", 8, True),
        ("company-001", 9, False),
        ("company-002", 31, True),
        ("company-002", 32, False),
    ],
    ids=("8-of-58-pass", "9-of-58-block", "31-of-209-pass", "32-of-209-block"),
)
def test_integer_cap_boundaries_are_derived_per_company(
    company_alias: str,
    classified_pages: int,
    allowed: bool,
) -> None:
    companies, rows = _dual_track_rows(lambda _index: (True, False))
    selected = _pdf_rows(rows, company_alias=company_alias, count=classified_pages)
    expected_hashes = _assign_exact_pdf_ocr_set(rows, selected)
    for row in selected:
        _stamp_classifier_exclusion(row, membership="ATTESTED")

    if not allowed:
        _assert_code(
            companies,
            rows,
            code=CAP_EXCEEDED,
            expected_pdf_ocr_hashes=expected_hashes,
        )
        return

    summary = _reconcile_v4(
        companies,
        rows,
        expected_pdf_ocr_hashes=expected_hashes,
    )
    assert summary["schema_version"] == TERMINAL_SCHEMA_V4
    assert summary["watermark_classified_page_count"] == classified_pages
    assert summary["watermark_classified_pages_by_company"] == {
        "company-001": classified_pages if company_alias == "company-001" else 0,
        "company-002": classified_pages if company_alias == "company-002" else 0,
    }


def test_v4_derives_manual_batch_unseen_and_per_company_counts() -> None:
    companies, rows, manual_row = _rows_with_one_confirmed_no_substantive_content()
    company_one = next(
        row
        for row in rows
        if row is not manual_row
        and row["company_alias"] == "company-001"
        and row["extension"] == "pdf"
    )
    company_two = next(
        row for row in rows if row["company_alias"] == "company-002" and row["extension"] == "pdf"
    )
    expected_hashes = _assign_exact_pdf_ocr_set(rows, [manual_row, company_one, company_two])
    _stamp_classifier_exclusion(company_one, membership="ATTESTED")
    _stamp_classifier_exclusion(company_two, membership="RUNTIME_UNSEEN")

    summary = _reconcile_v4(
        companies,
        rows,
        expected_pdf_ocr_hashes=expected_hashes,
    )

    assert summary["schema_version"] == TERMINAL_SCHEMA_V4
    assert summary["manual_v1_confirmed_document_count"] == 1
    assert summary["watermark_classifier_document_count"] == 2
    assert summary["watermark_classified_page_count"] == 2
    assert summary["watermark_batch_attested_page_count"] == 1
    assert summary["watermark_runtime_unseen_page_count"] == 1
    assert summary["watermark_classified_pages_by_company"] == {
        "company-001": 1,
        "company-002": 1,
    }
    assert summary["runtime_review_required"] is True
    assert len(summary["watermark_classification_receipts_sha256"]) == 64


def test_cap_counts_pages_not_documents() -> None:
    companies, rows = _dual_track_rows(lambda _index: (True, False))
    selected = _pdf_rows(rows, company_alias="company-001", count=1)
    expected_hashes = _assign_exact_pdf_ocr_set(rows, selected)
    _stamp_classifier_exclusion(selected[0], membership="ATTESTED", page_count=2)

    summary = _reconcile_v4(
        companies,
        rows,
        expected_pdf_ocr_hashes=expected_hashes,
    )

    assert summary["watermark_classifier_document_count"] == 1
    assert summary["watermark_classified_page_count"] == 2
    assert summary["watermark_classified_pages_by_company"]["company-001"] == 2


def test_manual_v1_remains_compatible_but_does_not_consume_classifier_cap() -> None:
    companies, rows, _manual_row = _rows_with_one_confirmed_no_substantive_content()

    summary = _reconcile_v4(companies, rows)

    assert summary["schema_version"] == TERMINAL_SCHEMA_V4
    assert summary["manual_v1_confirmed_document_count"] == 1
    assert summary["watermark_classifier_document_count"] == 0
    assert summary["watermark_classified_page_count"] == 0
    assert summary["watermark_classified_pages_by_company"] == {
        "company-001": 0,
        "company-002": 0,
    }
    assert summary["watermark_manifest_bound_receipt_count"] == 0
    assert summary["watermark_manifest_runtime_summary_sha256"] == hashlib.sha256(b"{}").hexdigest()


def _one_attested_manifest_fixture() -> tuple[
    Sequence[CompanySourceContract],
    list[dict[str, Any]],
    frozenset[str],
    dict[str, object],
]:
    companies, rows = _dual_track_rows(lambda _index: (True, False))
    selected = _pdf_rows(rows, company_alias="company-002", count=1)
    expected_hashes = _assign_exact_pdf_ocr_set(rows, selected)
    _stamp_classifier_exclusion(selected[0], membership="ATTESTED")
    summary = _manifest_runtime_summary_for_rows(rows)
    assert summary is not None
    return companies, rows, expected_hashes, summary


@pytest.mark.parametrize(
    "governance_field",
    [
        "manifest_sha256",
        "batch_envelope_sha256",
        "candidate_set_sha256",
        "exemplar_envelope_sha256",
        "exemplar_canonical_bitmap_sha256",
        "classifier_policy_sha256",
    ],
)
def test_final_reconciliation_rejects_stale_manifest_governance_hashes(
    governance_field: str,
) -> None:
    companies, rows, expected_hashes, summary = _one_attested_manifest_fixture()
    stale = copy.deepcopy(summary)
    stale[governance_field] = "9" * 64

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_v4(
            companies,
            rows,
            expected_pdf_ocr_hashes=expected_hashes,
            verified_manifest_runtime_summary=stale,
        )

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_final_reconciliation_rejects_same_count_candidate_identity_replacement() -> None:
    companies, rows, expected_hashes, summary = _one_attested_manifest_fixture()
    replaced = copy.deepcopy(summary)
    candidates = replaced["candidates"]
    assert isinstance(candidates, list) and len(candidates) == 1
    candidates[0]["safe_alias"] = "company-002-document-00209.pdf"

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_v4(
            companies,
            rows,
            expected_pdf_ocr_hashes=expected_hashes,
            verified_manifest_runtime_summary=replaced,
        )

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_final_reconciliation_rejects_same_identity_candidate_observation_replacement() -> None:
    companies, rows, expected_hashes, summary = _one_attested_manifest_fixture()
    replaced = copy.deepcopy(summary)
    candidates = replaced["candidates"]
    assert isinstance(candidates, list) and len(candidates) == 1
    candidates[0]["canonical_render_sha256"] = "8" * 64

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_v4(
            companies,
            rows,
            expected_pdf_ocr_hashes=expected_hashes,
            verified_manifest_runtime_summary=replaced,
        )

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_final_reconciliation_rejects_v2_without_current_verified_manifest() -> None:
    companies, rows, expected_hashes, _summary = _one_attested_manifest_fixture()

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_v4(
            companies,
            rows,
            expected_pdf_ocr_hashes=expected_hashes,
            verified_manifest_runtime_summary=None,
        )

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_duplicate_page_claim_fails_instead_of_inflating_reconciliation() -> None:
    companies, rows = _dual_track_rows(lambda _index: (True, False))
    selected = _pdf_rows(rows, company_alias="company-001", count=1)
    expected_hashes = _assign_exact_pdf_ocr_set(rows, selected)
    receipt = _stamp_classifier_exclusion(
        selected[0],
        membership="ATTESTED",
        page_count=2,
    )
    receipt["watermark_page_classifications"][1] = copy.deepcopy(
        receipt["watermark_page_classifications"][0]
    )
    receipt["watermark_page_classifications_sha256"] = _digest(
        receipt["watermark_page_classifications"]
    )

    _assert_code(
        companies,
        rows,
        code="DATA_ROOM_RECONCILIATION_INCOMPLETE",
        expected_pdf_ocr_hashes=expected_hashes,
    )
