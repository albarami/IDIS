"""Exact source-page and immutable-geometry proof at terminal reconciliation."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from idis.evaluation import real_example_terminal_reconciliation as reconciliation
from idis.evaluation.real_example_source_package import CompanySourceContract
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


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _render_admission(
    *,
    page: int,
    chosen_dpi: int,
    projected_pixel_count: int,
) -> dict[str, object]:
    projected_width = projected_pixel_count
    projected_height = 1
    return {
        "page": page,
        "schema_version": "ocr-render-admission-v2",
        "ladder_version": "ocr-dpi-ladder-v1",
        "configured_dpi": 200,
        "chosen_dpi": chosen_dpi,
        "projected_width": projected_width,
        "projected_height": projected_height,
        "projected_pixel_count": projected_pixel_count,
        "expected_render_width": projected_width,
        "expected_render_height": projected_height,
        "expected_render_pixel_count": projected_pixel_count,
        "poppler_version": "25.12.0",
        "poppler_runtime_identity_sha256": (
            "8ebfb07d9a8e5e01c12cc2994837a122cc6c94a930c657e10ffc655db8f0cc72"
        ),
        "expected_dimension_policy_id": "poppler-pdftoppm-25.12.0-dimensions-v1",
        "expected_dimension_policy_sha256": (
            "483ee7542fbcb6a69dc3949072ef2897471130e409cc41318395e72fec3e0253"
        ),
        "comparison_mode": "exact-poppler-25.12.0",
        "tolerance_pixels": 0,
        "tolerance_used": False,
        "max_pixel_count": 20_000_000,
    }


def _observed_admission(plan: Mapping[str, object]) -> dict[str, object]:
    return {
        **plan,
        "actual_render_width": plan["expected_render_width"],
        "actual_render_height": plan["expected_render_height"],
        "actual_render_pixel_count": plan["expected_render_pixel_count"],
        "width_delta_pixels": 0,
        "height_delta_pixels": 0,
    }


def _v4_evidence(
    *,
    prior: Mapping[str, Any],
    admission: Mapping[str, object],
) -> dict[str, object]:
    page = int(admission["page"])
    admissions = [_observed_admission(admission)]
    return {
        "schema_version": "ocr-terminal-evidence-v4",
        "ocr_page_count": 1,
        "ocr_page_numbers": [page],
        "preclassified_page_numbers": [],
        "ocr_required_page_count": 1,
        "ocr_span_count": 1,
        "confidence_count": 1,
        "mean_confidence": 0.8,
        "minimum_confidence": 0.8,
        "maximum_confidence": 0.8,
        "locator_contract_sha256": prior["locator_contract_sha256"],
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": _canonical_sha256([]),
        "ocr_render_admissions": admissions,
        "ocr_render_admissions_sha256": _canonical_sha256(admissions),
    }


def _exact_contract_fixture() -> tuple[
    Sequence[CompanySourceContract],
    list[dict[str, Any]],
    dict[str, tuple[int, ...]],
    dict[str, tuple[dict[str, object], ...]],
    list[dict[str, Any]],
]:
    companies, rows = _dual_track_rows(lambda _index: (True, False))
    pdf_rows = [row for row in rows if row["extension"] == "pdf" and row["ocr_performed"] is True]
    required_pages: dict[str, tuple[int, ...]] = {}
    expected_admissions: dict[str, tuple[dict[str, object], ...]] = {}
    for index, row in enumerate(pdf_rows):
        page = index % 3 + 1
        chosen_dpi = (200, 150, 120)[index % 3]
        projected_pixel_count = 3_740_000 + index
        admission = _render_admission(
            page=page,
            chosen_dpi=chosen_dpi,
            projected_pixel_count=projected_pixel_count,
        )
        content_sha256 = str(row["content_sha256"])
        required_pages[content_sha256] = (page,)
        expected_admissions[content_sha256] = (admission,)
        row["ocr_evidence"] = _v4_evidence(
            prior=row["ocr_evidence"],
            admission=admission,
        )
    return companies, rows, required_pages, expected_admissions, pdf_rows


def _reconcile_exact(
    companies: Sequence[CompanySourceContract],
    rows: Sequence[Mapping[str, Any]],
    *,
    required_pages: Mapping[str, Sequence[int]],
    expected_admissions: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, Any]:
    expected_hashes = frozenset(required_pages)
    return reconciliation.reconcile_terminal_ingestion(
        source_companies=companies,
        terminal_rows=rows,
        expected_company_counts=EXPECTED_COMPANY_COUNTS,
        expected_format_counts=EXPECTED_FORMAT_COUNTS,
        expected_total_files=EXPECTED_TOTAL_FILES,
        expected_ocr_files=len(expected_hashes) + EXPECTED_FORMAT_COUNTS["png"],
        expected_stt_files=EXPECTED_STT_FILES,
        expected_large_files=EXPECTED_LARGE_FILES,
        large_file_threshold_bytes=FIFTY_MIB,
        expected_pdf_encryption_dispositions=EXPECTED_PDF_ENCRYPTION_DISPOSITIONS,
        expected_media_runtime_provenance=EXPECTED_MEDIA_RUNTIME_PROVENANCE,
        expected_pdf_ocr_content_sha256s=expected_hashes,
        expected_pdf_ocr_required_pages_by_content_sha256=required_pages,
        expected_pdf_ocr_render_admissions_by_content_sha256=expected_admissions,
    )


def test_exact_source_bound_page_and_geometry_contract_reconciles() -> None:
    companies, rows, required_pages, expected_admissions, _pdf_rows = _exact_contract_fixture()

    result = _reconcile_exact(
        companies,
        rows,
        required_pages=required_pages,
        expected_admissions=expected_admissions,
    )

    assert result["status"] == "ready"


def test_count_preserving_cross_file_page_swap_fails_source_binding() -> None:
    companies, rows, required_pages, expected_admissions, pdf_rows = _exact_contract_fixture()
    first_evidence = copy.deepcopy(pdf_rows[0]["ocr_evidence"])
    second_evidence = copy.deepcopy(pdf_rows[1]["ocr_evidence"])
    pdf_rows[0]["ocr_evidence"] = second_evidence
    pdf_rows[1]["ocr_evidence"] = first_evidence

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_exact(
            companies,
            rows,
            required_pages=required_pages,
            expected_admissions=expected_admissions,
        )

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_allowed_but_not_highest_fitting_dpi_fails_source_geometry_binding() -> None:
    companies, rows, required_pages, expected_admissions, pdf_rows = _exact_contract_fixture()
    evidence = pdf_rows[0]["ocr_evidence"]
    evidence["ocr_render_admissions"][0]["chosen_dpi"] = 150
    evidence["ocr_render_admissions_sha256"] = _canonical_sha256(evidence["ocr_render_admissions"])

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_exact(
            companies,
            rows,
            required_pages=required_pages,
            expected_admissions=expected_admissions,
        )

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_fictional_projected_pixel_count_fails_source_geometry_binding() -> None:
    companies, rows, required_pages, expected_admissions, pdf_rows = _exact_contract_fixture()
    evidence = pdf_rows[0]["ocr_evidence"]
    evidence["ocr_render_admissions"][0]["projected_pixel_count"] += 1
    evidence["ocr_render_admissions_sha256"] = _canonical_sha256(evidence["ocr_render_admissions"])

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_exact(
            companies,
            rows,
            required_pages=required_pages,
            expected_admissions=expected_admissions,
        )

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actual_render_width", 3_740_001),
        ("actual_render_pixel_count", 20_000_001),
        ("width_delta_pixels", 1),
        ("tolerance_pixels", 1),
        ("tolerance_used", True),
        ("poppler_runtime_identity_sha256", "0" * 64),
        ("expected_dimension_policy_sha256", "not-a-sha256"),
    ],
)
def test_fresh_v4_receipt_fails_closed_on_observation_or_authority_drift(
    field: str,
    value: object,
) -> None:
    companies, rows, required_pages, expected_admissions, pdf_rows = _exact_contract_fixture()
    evidence = pdf_rows[0]["ocr_evidence"]
    evidence["ocr_render_admissions"][0][field] = value
    evidence["ocr_render_admissions_sha256"] = _canonical_sha256(evidence["ocr_render_admissions"])

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_exact(
            companies,
            rows,
            required_pages=required_pages,
            expected_admissions=expected_admissions,
        )

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_fresh_exact_reconciliation_rejects_prior_v3_terminal_evidence() -> None:
    companies, rows, required_pages, expected_admissions, pdf_rows = _exact_contract_fixture()
    pdf_rows[0]["ocr_evidence"]["schema_version"] = "ocr-terminal-evidence-v3"

    with pytest.raises(reconciliation.TerminalIngestionReconciliationError) as caught:
        _reconcile_exact(
            companies,
            rows,
            required_pages=required_pages,
            expected_admissions=expected_admissions,
        )

    assert caught.value.code == reconciliation.DATA_ROOM_RECONCILIATION_INCOMPLETE


def test_sealed_v3_v1_page_resolution_evidence_remains_honestly_validatable() -> None:
    admission = {
        "page": 1,
        "schema_version": "ocr-render-admission-v1",
        "ladder_version": "ocr-dpi-ladder-v1",
        "configured_dpi": 200,
        "chosen_dpi": 200,
        "projected_pixel_count": 3_740_000,
        "max_pixel_count": 20_000_000,
    }
    evidence = {
        "schema_version": "ocr-terminal-evidence-v3",
        "ocr_page_count": 1,
        "ocr_page_numbers": [1],
        "preclassified_page_numbers": [],
        "ocr_required_page_count": 1,
        "ocr_span_count": 1,
        "confidence_count": 1,
        "mean_confidence": 0.8,
        "minimum_confidence": 0.8,
        "maximum_confidence": 0.8,
        "locator_contract_sha256": _canonical_sha256([]),
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": _canonical_sha256([]),
        "ocr_render_admissions": [admission],
        "ocr_render_admissions_sha256": _canonical_sha256([admission]),
    }

    reconciliation._validate_safe_ocr_evidence(
        evidence,
        extension="pdf",
        durable_span_count=1,
    )
