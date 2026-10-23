"""Gate bindings for the v5 PDF page-disposition census."""

from __future__ import annotations

import copy
import hashlib

import pytest

from idis.evaluation import real_example_gate as gate
from idis.evaluation import real_example_terminal_reconciliation as reconciliation
from tests.slice100_task4.test_durable_spans_api import _collector_fixture
from tests.slice100_task4.test_oversized_terminal_reconciliation import (
    _canonical_sha256,
    _ocr_collector_fixture,
    _receipt_for_row,
    _render_admission,
)
from tests.slice100_task4.test_strict_durable_api_gate import _terminal_row
from tests.slice100_task4.test_watermark_privacy_readiness_contract_red import (
    _safe_v4_proof,
)


def _safe_v5_proof() -> dict[str, object]:
    proof = _safe_v4_proof()
    proof.update(
        {
            # This fixture intentionally remains the backward-compatible v5 census proof.
            "schema_version": "slice100-task4-durable-ingestion-proof-v5",
            "pdf_page_disposition_census_schema_version": ("pdf-page-disposition-census-v1"),
            "pdf_page_disposition_class_counts": {
                "native_text": 2_311,
                "ocr_text": 282,
                "image_only_page": 0,
                "watermark_only": 2,
            },
            "pdf_page_disposition_censuses_sha256": "a" * 64,
        }
    )
    return proof


def _safe_v6_proof() -> dict[str, object]:
    proof = _safe_v5_proof()
    snapshot = gate._canonical_terminal_snapshot_digests(
        [_terminal_row(private_token="fictional-private-token")]
    )
    proof.update(
        {
            "schema_version": gate.TASK4_DURABLE_PROOF_SCHEMA_VERSION,
            "terminal_rows_sha256": snapshot["terminal_rows_sha256"],
            "terminal_snapshot_digests": snapshot,
        }
    )
    return proof


def test_safe_v6_proof_binds_exact_component_digest_mapping() -> None:
    proof = _safe_v6_proof()

    gate._validate_safe_durable_ingestion_receipt(proof)

    missing = copy.deepcopy(proof)
    missing["terminal_snapshot_digests"]["component_sha256"].pop("spans")
    extra = copy.deepcopy(proof)
    extra["terminal_snapshot_digests"]["component_sha256"]["unexpected"] = "a" * 64
    malformed = copy.deepcopy(proof)
    malformed["terminal_snapshot_digests"]["component_sha256"]["documents"] = "not-a-sha"
    unbound = copy.deepcopy(proof)
    unbound["terminal_rows_sha256"] = "f" * 64

    for invalid in (missing, extra, malformed, unbound):
        with pytest.raises(gate.DurableIngestionGateError) as caught:
            gate._validate_safe_durable_ingestion_receipt(invalid)
        assert caught.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_safe_v5_proof_requires_complete_page_census_summary() -> None:
    proof = _safe_v5_proof()

    gate._validate_safe_durable_ingestion_receipt(proof)

    for key in (
        "pdf_page_disposition_census_schema_version",
        "pdf_page_disposition_class_counts",
        "pdf_page_disposition_censuses_sha256",
    ):
        mutated = copy.deepcopy(proof)
        mutated.pop(key)
        with pytest.raises(gate.DurableIngestionGateError):
            gate._validate_safe_durable_ingestion_receipt(mutated)


def test_v5_observed_ocr_execution_count_includes_image_only_pages() -> None:
    rows = [
        {
            "extension": "pdf",
            "ocr_performed": True,
            "ocr_evidence": {
                "schema_version": "ocr-terminal-evidence-v5",
                "pdf_page_census": {
                    "schema_version": "pdf-page-disposition-census-v1",
                    "class_counts": {
                        "native_text": 6,
                        "ocr_text": 3,
                        "image_only_page": 2,
                        "watermark_only": 1,
                    },
                },
            },
        }
    ]

    assert gate._observed_pdf_ocr_execution_page_count(rows) == 5


def test_restart_equivalence_binds_page_census_digest() -> None:
    baseline = _safe_v5_proof()
    baseline.update(
        {
            "phase": "prepared",
            "restart_survival_verified": False,
            "previous_api_boot_sha256": None,
            "api_boot_sha256": "1" * 64,
        }
    )
    current = copy.deepcopy(baseline)
    current["api_boot_sha256"] = "2" * 64

    gate._verify_restart_equivalence(baseline=baseline, current=current, upload_post_count=0)

    current["pdf_page_disposition_censuses_sha256"] = "3" * 64
    with pytest.raises(gate.DurableIngestionGateError):
        gate._verify_restart_equivalence(baseline=baseline, current=current, upload_post_count=0)


def test_native_only_pdf_gets_a_complete_v5_census_when_source_bound() -> None:
    companies, summary, api = _collector_fixture()
    pdf_company = next(company for company in companies if company.files[0].extension == "pdf")
    source = pdf_company.files[0]
    document_url = next(
        url
        for url in api.responses
        if url.startswith("/v1/deals/")
        and "/documents/" in url
        and api.responses[url]["sha256"] == source.content_sha256
    )
    document_id = api.responses[document_url]["document_id"]
    span_url = next(
        url
        for url in api.responses
        if url.endswith("/spans") and api.responses[url]["items"][0]["document_id"] == document_id
    )
    api.responses[document_url]["metadata"]["page_count"] = 1
    api.responses[document_url]["metadata"]["ocr_required_page_count"] = 0
    api.responses[span_url]["items"][0]["locator"] = {"page": 1, "line": 1}

    rows = reconciliation.collect_durable_terminal_rows(
        api_client=api,
        api_key="fictional-page-census-key",
        source_companies=companies,
        preparation_summary=summary,
        large_file_threshold_bytes=50 * 1024 * 1024,
        expected_pdf_page_counts_by_content_sha256={source.content_sha256: 1},
    )

    pdf_row = next(row for row in rows if row["extension"] == "pdf")
    census = pdf_row["ocr_evidence"]["pdf_page_census"]
    assert census["native_text_page_numbers"] == [1]
    assert census["ocr_text_page_numbers"] == []
    assert census["image_only_page_numbers"] == []
    assert census["watermark_only_page_numbers"] == []


def test_whole_watermark_terminal_gets_v5_census_without_ocr_execution() -> None:
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
            "ocr_mean_confidence": None,
            "ocr_confidence_by_page": [],
            "ocr_render_admissions": [],
            "ocr_render_admissions_sha256": hashlib.sha256(b"[]").hexdigest(),
            "parse_error_codes": ["ocr_no_text_extracted"],
            "document_no_substantive_content_receipt": receipt,
        }
    )
    api.responses[span_url] = {"items": [], "total": 0}

    rows = reconciliation.collect_durable_terminal_rows(
        api_client=api,
        api_key="fictional-page-census-key",
        source_companies=companies,
        preparation_summary=summary,
        large_file_threshold_bytes=50 * 1024 * 1024,
        expected_pdf_page_counts_by_content_sha256={source.content_sha256: 1},
    )

    evidence = rows[0]["ocr_evidence"]
    assert evidence["schema_version"] == "ocr-terminal-evidence-v5"
    assert evidence["ocr_page_count"] == 0
    assert evidence["ocr_required_page_count"] == 1
    assert evidence["pdf_page_census"]["watermark_only_page_numbers"] == [1]


def test_v5_rejects_an_ocr_text_span_without_per_span_render_admission() -> None:
    admission = _render_admission(page=1)
    empty_digest = hashlib.sha256(b"[]").hexdigest()
    metadata = {
        "page_count": 1,
        "ocr_performed": True,
        "ocr_page_count": 1,
        "ocr_required_page_count": 1,
        "ocr_confidence_by_page": [0.7],
        "ocr_mean_confidence": 0.7,
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": empty_digest,
        "ocr_render_admissions": [admission],
        "ocr_render_admissions_sha256": _canonical_sha256([admission]),
    }
    locator_proof = {
        "durable_span_count": 2,
        "native_page_numbers": [],
        "ocr_page_numbers": [1],
        "page_evidence_bindings": {"1": "1" * 64},
        "ocr_page_count": 1,
        "ocr_span_count": 2,
        "locator_contract_sha256": "2" * 64,
        "ocr_render_admissions": [admission],
        "ocr_render_admission_span_count": 1,
        "ocr_render_admissions_sha256": _canonical_sha256([admission]),
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": empty_digest,
    }

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError):
        reconciliation._safe_pdf_ocr_evidence_v5(
            metadata,
            content_sha256="3" * 64,
            parse_status="PARSED",
            locator_proof=locator_proof,
            watermark_receipt=None,
            expected_page_count=1,
            whole_document_terminal=False,
        )
