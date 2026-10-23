"""Gate contracts separating OCR admission from classifier-first execution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from idis.evaluation import real_example_gate as gate
from idis.evaluation.real_example_source_package import TwoCompanySourceContract
from idis.evaluation.real_example_terminal_reconciliation import PdfOcrRequirementsCensus
from idis.parsers import ocr
from idis.services.ingestion.no_substantive_content_attestation import (
    VerifiedDocumentNoSubstantiveContentAttestation,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    VerifiedWatermarkBatchAttestation,
)


def _strict_source_render_plan(page_number: int) -> dict[str, object]:
    """Return a pre-render v2 source plan; actual dimensions belong to receipts."""

    return {
        "page": page_number,
        "schema_version": ocr.OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        "ladder_version": ocr.OCR_DPI_LADDER_VERSION,
        "configured_dpi": 200,
        "chosen_dpi": 200,
        "projected_width": 1_700,
        "projected_height": 2_200,
        "projected_pixel_count": 3_740_000,
        "expected_render_width": 1_700,
        "expected_render_height": 2_200,
        "expected_render_pixel_count": 3_740_000,
        "poppler_version": ocr.PINNED_POPPLER_VERSION,
        "poppler_runtime_identity_sha256": "8" * 64,
        "expected_dimension_policy_id": ocr.POPPLER_EXPECTED_DIMENSION_POLICY_ID,
        "expected_dimension_policy_sha256": ocr.POPPLER_EXPECTED_DIMENSION_POLICY_SHA256,
        "comparison_mode": ocr.POPPLER_EXACT_COMPARISON_MODE,
        "tolerance_pixels": 0,
        "tolerance_used": False,
        "max_pixel_count": ocr.MAX_OCR_IMAGE_PIXELS,
    }


def _verified_manifest(
    candidates: tuple[dict[str, object], ...],
) -> VerifiedDocumentNoSubstantiveContentAttestationManifest:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    exemplar = VerifiedDocumentNoSubstantiveContentAttestation(
        schema_version="document-no-substantive-content-attestation-v1",
        signer_id="test-signer",
        tenant_id="00000000-0000-4000-8000-000000000001",
        source_tree_fingerprint="a" * 64,
        safe_alias=str(candidates[0]["safe_alias"]),
        content_sha256=str(candidates[0]["content_sha256"]),
        disposition="DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        variant="watermark_only",
        attestation_text_sha256="b" * 64,
        reviewer_id="test-reviewer",
        approved_at_text="2026-07-17T00:00:00Z",
        expires_at_text="2026-10-15T00:00:00Z",
        approved_at=now,
        expires_at=now + timedelta(days=90),
        payload_sha256="c" * 64,
        envelope_sha256="d" * 64,
        public_key_sha256="e" * 64,
    )
    batch = VerifiedWatermarkBatchAttestation(
        schema_version="watermark-batch-attestation-v1",
        signer_id="test-signer",
        payload={
            "classifier_policy_id": "exemplar-watermark-page-v1",
            "classifier_policy_sha256": "f" * 64,
        },
        candidates=candidates,
        candidate_set_sha256="1" * 64,
        prescan_receipt_sha256="2" * 64,
        sample_set_sha256="3" * 64,
        payload_sha256="4" * 64,
        envelope_sha256="5" * 64,
        public_key_sha256="e" * 64,
        approved_at=now,
        expires_at=now + timedelta(days=90),
    )
    return VerifiedDocumentNoSubstantiveContentAttestationManifest(
        schema_version="document-no-substantive-content-attestation-manifest-v2",
        exemplar=exemplar,
        batch=batch,
        exemplar_envelope_sha256="d" * 64,
        exemplar_canonical_bitmap_sha256="6" * 64,
        manifest_sha256="7" * 64,
        company_file_counts={"company-001": 58, "company-002": 209},
        exemplar_canonical_bitmap=b"\x00" * 2048,
    )


def _admission_and_contract() -> tuple[PdfOcrRequirementsCensus, TwoCompanySourceContract]:
    hashes = tuple(f"{index:064x}" for index in range(42))
    required_pages_by_hash: dict[str, tuple[int, ...]] = {}
    for index, content_sha256 in enumerate(hashes):
        if index < 2:
            required_pages_by_hash[content_sha256] = (1,)
        elif index == 2:
            required_pages_by_hash[content_sha256] = tuple(range(2, 9))
        elif index == 41:
            required_pages_by_hash[content_sha256] = tuple(range(1, 10))
        else:
            required_pages_by_hash[content_sha256] = tuple(range(1, 8))
    page_count_by_hash = {
        content_sha256: 9 if index in {2, 41} else len(required_pages_by_hash[content_sha256])
        for index, content_sha256 in enumerate(hashes)
    }
    render_admissions_by_hash = {
        content_sha256: tuple(
            _strict_source_render_plan(page_number) for page_number in required_pages
        )
        for content_sha256, required_pages in required_pages_by_hash.items()
    }
    sources = tuple(
        SimpleNamespace(
            generated_upload_name=f"company-002-document-{index + 53:05d}.pdf",
            content_sha256=content_sha256,
            extension="pdf",
        )
        for index, content_sha256 in enumerate(hashes[:3])
    )
    contract = cast(
        TwoCompanySourceContract,
        SimpleNamespace(
            companies=(SimpleNamespace(files=sources),),
            total_file_count=len(sources),
        ),
    )
    admission = cast(
        PdfOcrRequirementsCensus,
        SimpleNamespace(
            required_content_sha256s=frozenset(hashes),
            required_pages_by_content_sha256=required_pages_by_hash,
            page_count_by_content_sha256=page_count_by_hash,
            render_admissions_by_content_sha256=render_admissions_by_hash,
            pdf_file_count=223,
            total_page_count=2595,
            required_page_count=sum(len(value) for value in required_pages_by_hash.values()),
            all_native_file_count=181,
            all_ocr_file_count=35,
            mixed_file_count=7,
            max_required_page_index=72,
            max_required_pages_per_file=72,
        ),
    )
    return admission, contract


def test_signed_candidates_derive_classifier_first_execution_census() -> None:
    admission, contract = _admission_and_contract()
    candidates = tuple(
        {
            "safe_alias": source.generated_upload_name,
            "content_sha256": source.content_sha256,
            "page_number": 1,
        }
        for source in contract.companies[0].files[:2]
    )

    census = gate._derive_pdf_ocr_execution_census(
        contract=contract,
        admission=admission,
        attestation=_verified_manifest(candidates),
    )

    assert census.admission_file_count == 42
    assert census.admission_page_count == 284
    assert census.classifier_only_file_count == len(candidates) == 2
    assert census.classifier_only_page_count == len(candidates) == 2
    assert census.classifier_first_file_count == len(candidates) == 2
    assert census.classifier_first_page_count == len(candidates) == 2
    assert census.classifier_terminal_file_count == len(candidates) == 2
    assert census.execution_file_count == census.admission_file_count - len(candidates) == 40
    assert census.execution_page_count == census.admission_page_count - len(candidates) == 282
    assert census.total_ocr_execution_file_count == (
        census.execution_file_count + gate.TASK4_EXPECTED_FORMAT_COUNTS["png"]
    )
    assert census.execution_content_sha256s == (
        admission.required_content_sha256s
        - {str(candidate["content_sha256"]) for candidate in candidates}
    )
    assert census.required_pages_by_content_sha256 == (admission.required_pages_by_content_sha256)
    assert census.render_admissions_by_content_sha256 == (
        admission.render_admissions_by_content_sha256
    )


def test_candidate_page_in_a_multi_ocr_page_pdf_reduces_pages_but_not_files() -> None:
    admission, contract = _admission_and_contract()
    source = contract.companies[0].files[2]
    candidates = (
        {
            "safe_alias": source.generated_upload_name,
            "content_sha256": source.content_sha256,
            "page_number": 4,
        },
    )

    census = gate._derive_pdf_ocr_execution_census(
        contract=contract,
        admission=admission,
        attestation=_verified_manifest(candidates),
    )

    assert census.classifier_first_page_count == 1
    assert census.classifier_first_file_count == 1
    assert census.classifier_only_file_count == 0
    assert census.classifier_only_page_count == 0
    assert census.execution_file_count == census.admission_file_count == 42
    assert census.execution_page_count == census.admission_page_count - 1 == 283
    assert source.content_sha256 in census.execution_content_sha256s


def test_all_required_candidates_in_a_multi_page_pdf_remove_that_file() -> None:
    admission, contract = _admission_and_contract()
    source = contract.companies[0].files[2]
    required_pages = admission.required_pages_by_content_sha256[source.content_sha256]
    candidates = tuple(
        {
            "safe_alias": source.generated_upload_name,
            "content_sha256": source.content_sha256,
            "page_number": page_number,
        }
        for page_number in required_pages
    )

    census = gate._derive_pdf_ocr_execution_census(
        contract=contract,
        admission=admission,
        attestation=_verified_manifest(candidates),
    )

    assert census.classifier_first_page_count == 7
    assert census.classifier_first_file_count == 1
    assert census.classifier_only_file_count == 1
    assert census.classifier_only_page_count == 7
    assert census.classifier_terminal_file_count == 0
    assert census.execution_file_count == census.admission_file_count - 1 == 41
    assert census.execution_page_count == census.admission_page_count - 7 == 277
    assert source.content_sha256 not in census.execution_content_sha256s


def test_candidate_page_must_be_in_the_source_bound_required_page_set() -> None:
    admission, contract = _admission_and_contract()
    source = contract.companies[0].files[2]
    candidates = (
        {
            "safe_alias": source.generated_upload_name,
            "content_sha256": source.content_sha256,
            "page_number": 1,
        },
    )

    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._derive_pdf_ocr_execution_census(
            contract=contract,
            admission=admission,
            attestation=_verified_manifest(candidates),
        )

    assert exc_info.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_signed_candidate_must_bind_an_admitted_source_pdf() -> None:
    admission, contract = _admission_and_contract()
    candidates = (
        {
            "safe_alias": contract.companies[0].files[0].generated_upload_name,
            "content_sha256": "f" * 64,
            "page_number": 1,
        },
    )

    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._derive_pdf_ocr_execution_census(
            contract=contract,
            admission=admission,
            attestation=_verified_manifest(candidates),
        )

    assert exc_info.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def _v4_counts() -> dict[str, int]:
    return {
        "parsed_file_count": 265,
        "parsed_span_bearing_file_count": 265,
        "confirmed_no_substantive_content_file_count": 2,
        "durable_span_file_count": 265,
        "durable_object_file_count": 267,
        "ocr_file_count": 42,
        "pdf_ocr_file_count": 40,
        "stt_file_count": 8,
        "media_dual_receipt_file_count": 8,
        "stt_positive_file_count": 6,
        "frame_ocr_positive_file_count": 2,
        "large_streamed_file_count": 3,
        "pdf_ocr_admission_file_count": 42,
        "pdf_ocr_admission_page_count": 284,
        "pdf_ocr_execution_page_count": 282,
        "pdf_classifier_first_file_count": 2,
        "pdf_classifier_only_file_count": 2,
        "pdf_classifier_only_page_count": 2,
        "pdf_classifier_first_page_count": 2,
        "pdf_classifier_terminal_file_count": 2,
        "manual_v1_confirmed_document_count": 0,
        "watermark_classifier_document_count": 2,
        "watermark_classified_page_count": 2,
        "watermark_batch_attested_page_count": 2,
        "watermark_runtime_unseen_page_count": 0,
    }


def test_v4_safe_counts_preserve_admission_and_execution_as_distinct_censuses() -> None:
    counts = _v4_counts()

    assert gate._safe_parser_counts(counts) == counts
    assert (
        gate._observed_pdf_ocr_execution_page_count(
            (
                {
                    "extension": "pdf",
                    "ocr_performed": True,
                    "ocr_evidence": {"ocr_page_count": 280},
                },
                {"extension": "pdf", "ocr_performed": True, "ocr_evidence": {"ocr_page_count": 2}},
                {"extension": "pdf", "ocr_performed": False},
            )
        )
        == counts["pdf_ocr_execution_page_count"]
    )


def test_v4_safe_counts_allow_ocr_exhaustion_with_native_pages_remaining() -> None:
    counts = _v4_counts()
    counts.update(
        {
            "parsed_file_count": 267,
            "parsed_span_bearing_file_count": 267,
            "confirmed_no_substantive_content_file_count": 0,
            "durable_span_file_count": 267,
            "ocr_file_count": 43,
            "pdf_ocr_file_count": 41,
            "pdf_ocr_execution_page_count": 277,
            "pdf_classifier_first_file_count": 1,
            "pdf_classifier_only_file_count": 1,
            "pdf_classifier_only_page_count": 7,
            "pdf_classifier_first_page_count": 7,
            "pdf_classifier_terminal_file_count": 0,
            "watermark_classifier_document_count": 1,
            "watermark_classified_page_count": 7,
            "watermark_batch_attested_page_count": 7,
        }
    )

    assert gate._safe_parser_counts(counts) == counts


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pdf_ocr_file_count", 42),
        ("ocr_file_count", 44),
        ("pdf_ocr_execution_page_count", 284),
        ("pdf_classifier_only_page_count", 1),
        ("pdf_classifier_first_page_count", 1),
        ("pdf_classifier_first_file_count", 1),
        ("pdf_classifier_terminal_file_count", 1),
    ],
)
def test_v4_safe_counts_reject_a_collapsed_or_unbalanced_census(
    field: str,
    value: int,
) -> None:
    counts = _v4_counts()
    counts[field] = value

    with pytest.raises(gate.DurableIngestionGateError) as exc_info:
        gate._safe_parser_counts(counts)

    assert exc_info.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"
