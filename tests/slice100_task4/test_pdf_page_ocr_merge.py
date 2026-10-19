"""Task 4 RED contract for complete per-page PDF OCR merging."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from idis.evaluation.real_example_source_package import build_two_company_source_contract
from idis.parsers.base import ParseErrorCode, ParseLimits, ParseResult
from idis.parsers.ocr import OcrConfig, OcrPageText
from idis.parsers.pdf import parse_pdf
from tests._canonical_poppler_pin import requires_canonical_poppler_pin


def _source_render_plan(
    *,
    page: int,
    chosen_dpi: int,
    width: int,
    height: int,
) -> dict[str, object]:
    return {
        "page": page,
        "schema_version": "ocr-render-admission-v2",
        "ladder_version": "ocr-dpi-ladder-v1",
        "configured_dpi": 200,
        "chosen_dpi": chosen_dpi,
        "projected_width": width,
        "projected_height": height,
        "projected_pixel_count": width * height,
        "expected_render_width": width,
        "expected_render_height": height,
        "expected_render_pixel_count": width * height,
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


class _PageOcrAdapter:
    def __init__(self, pages: list[OcrPageText]) -> None:
        self.pages = pages

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        del data, max_pages, timeout_seconds
        return list(self.pages)

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        del data, timeout_seconds
        return []


class _FileBackedPageOcrAdapter(_PageOcrAdapter):
    def __init__(self, pages: list[OcrPageText]) -> None:
        super().__init__(pages)
        self.paths: list[Path] = []
        self.byte_calls = 0

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        del data, max_pages, timeout_seconds
        self.byte_calls += 1
        raise AssertionError("strict staged PDF OCR must not create a private byte-copy temp file")

    def extract_pdf_file(
        self,
        source_path: Path,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        assert max_pages == 10
        assert timeout_seconds == 120
        self.paths.append(source_path)
        return list(self.pages)


def _mixed_native_and_image_pdf() -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=letter)
    document.drawString(72, 720, "FICTIONAL NATIVE PAGE ONE")
    document.showPage()
    image_bytes = io.BytesIO()
    Image.new("RGB", (16, 16), color=(20, 40, 60)).save(image_bytes, format="PNG")
    image_bytes.seek(0)
    document.drawImage(ImageReader(image_bytes), 72, 650, width=64, height=64)
    document.save()
    return output.getvalue()


def test_mixed_pdf_merges_native_and_only_required_ocr_pages_in_page_order() -> None:
    adapter = _PageOcrAdapter(
        [
            OcrPageText(page_number=1, text="MUST NOT DUPLICATE NATIVE PAGE", confidence=0.8),
            OcrPageText(page_number=2, text="FICTIONAL OCR PAGE TWO", confidence=0.9),
        ]
    )

    result = parse_pdf(
        _mixed_native_and_image_pdf(),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=10),
    )

    assert result.success is True
    assert result.metadata["native_text_page_count"] == 1
    assert result.metadata["ocr_required_page_count"] == 1
    assert result.metadata["ocr_page_count"] == 1
    assert result.metadata["ocr_performed"] is True
    assert [span.locator["page"] for span in result.spans] == [1, 2]
    assert result.spans[0].locator.get("source") is None
    assert result.spans[1].locator["source"] == "ocr"
    combined = " ".join(span.text_excerpt for span in result.spans)
    assert "FICTIONAL NATIVE PAGE ONE" in combined
    assert "FICTIONAL OCR PAGE TWO" in combined
    assert "MUST NOT DUPLICATE" not in combined


def test_strict_pdf_ocr_uses_the_verified_staged_path_without_a_second_pdf_copy(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "fictional-staged.pdf"
    data = _mixed_native_and_image_pdf()
    source_path.write_bytes(data)
    adapter = _FileBackedPageOcrAdapter(
        [
            OcrPageText(page_number=1, text="unused native page", confidence=0.8),
            OcrPageText(page_number=2, text="FICTIONAL OCR PAGE TWO", confidence=0.9),
        ]
    )

    result = parse_pdf(
        data,
        ocr_config=OcrConfig(
            enabled=True,
            adapter=adapter,
            max_pages=10,
            timeout_seconds=120,
        ),
        source_path=source_path,
    )

    assert result.success is True
    assert adapter.paths == [source_path]
    assert adapter.byte_calls == 0


def test_ingestion_service_forwards_the_verified_stage_to_the_parser_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.services.ingestion import service as service_module

    source_path = tmp_path / "fictional-stage.pdf"
    source_path.write_bytes(b"%PDF-fictional")
    observed: dict[str, object] = {}

    def parse_bytes(**values: object) -> ParseResult:
        observed.update(values)
        return ParseResult(doc_type="PDF", success=True)

    monkeypatch.setattr(service_module, "parse_bytes", parse_bytes)
    service = SimpleNamespace(
        _parse_limits=ParseLimits(),
        _ocr_config=None,
        _media_config=None,
        # This private-method seam must mirror constructor-owned parser configuration.
        _watermark_page_config=None,
        _password_manifest=SimpleNamespace(resolve=lambda _sha256: None),
    )

    result = service_module.IngestionService._parse_document(
        service,
        b"%PDF-fictional",
        "fictional-stage.pdf",
        "application/octet-stream",
        sha256="a" * 64,
        source_path=source_path,
    )

    assert result.success is True
    assert observed["source_path"] == source_path


def test_missing_ocr_result_for_one_required_image_page_fails_closed() -> None:
    adapter = _PageOcrAdapter(
        [OcrPageText(page_number=1, text="irrelevant native-page OCR", confidence=0.8)]
    )

    result = parse_pdf(
        _mixed_native_and_image_pdf(),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=10),
    )

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_NO_TEXT_EXTRACTED]


def test_mixed_pdf_without_ocr_runtime_fails_instead_of_returning_partial_success() -> None:
    result = parse_pdf(_mixed_native_and_image_pdf())

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_UNAVAILABLE]


def test_ocr_adapter_result_with_duplicate_page_is_rejected() -> None:
    adapter = _PageOcrAdapter(
        [
            OcrPageText(page_number=2, text="first", confidence=0.8),
            OcrPageText(page_number=2, text="duplicate", confidence=0.9),
        ]
    )

    result = parse_pdf(
        _mixed_native_and_image_pdf(),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=10),
    )

    assert result.success is False
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_FAILED]


@requires_canonical_poppler_pin
def test_read_only_pdf_ocr_census_binds_required_files_by_content_hash(
    tmp_path: Path,
) -> None:
    from idis.evaluation.real_example_terminal_reconciliation import (
        inspect_pdf_ocr_requirements,
    )

    root = tmp_path / "fictional-data-room"
    first = root / "fictional-company-a"
    second = root / "fictional-company-b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    mixed_path = first / "fictional-mixed.pdf"
    native_path = second / "fictional-native.pdf"
    mixed_path.write_bytes(_mixed_native_and_image_pdf())
    native_output = io.BytesIO()
    native_document = canvas.Canvas(native_output, pagesize=letter)
    native_document.drawString(72, 720, "FICTIONAL ALL NATIVE PDF")
    native_document.save()
    native_path.write_bytes(native_output.getvalue())
    company_bytes = (mixed_path.stat().st_size, native_path.stat().st_size)
    contract = build_two_company_source_contract(
        root=root,
        expected_file_counts=(1, 1),
        expected_total_files=2,
        expected_company_bytes=company_bytes,
        expected_total_bytes=sum(company_bytes),
    )

    census = inspect_pdf_ocr_requirements(
        contract,
        expected_pdf_files=2,
        expected_total_pages=3,
        expected_required_files=1,
        expected_required_pages=1,
        expected_all_native_files=1,
        expected_all_ocr_files=0,
        expected_mixed_files=1,
        expected_max_required_page_index=2,
        expected_max_required_pages_per_file=1,
    )

    mixed_hash = contract.companies[0].files[0].content_sha256
    native_hash = contract.companies[1].files[0].content_sha256
    assert census.required_content_sha256s == frozenset({mixed_hash})
    assert census.required_pages_by_content_sha256 == {mixed_hash: (2,)}
    assert census.page_count_by_content_sha256 == {mixed_hash: 2, native_hash: 1}
    assert census.render_admissions_by_content_sha256 == {
        mixed_hash: (_source_render_plan(page=2, chosen_dpi=200, width=1_700, height=2_200),)
    }
    assert census.safe_summary()["required_file_count"] == 1
    assert census.safe_summary()["required_page_count"] == 1
    assert census.safe_summary()["max_required_page_index"] == 2
    assert census.safe_summary()["max_required_pages_per_file"] == 1
    encoded = json.dumps(census.safe_summary(), sort_keys=True)
    assert str(root) not in encoded
    assert "fictional-company" not in encoded
    assert "fictional-mixed" not in encoded


@requires_canonical_poppler_pin
def test_read_only_census_derives_highest_fitting_rung_from_source_geometry(
    tmp_path: Path,
) -> None:
    from idis.evaluation.real_example_terminal_reconciliation import (
        inspect_pdf_ocr_requirements,
    )

    root = tmp_path / "fictional-poster-room"
    first = root / "fictional-company-a"
    second = root / "fictional-company-b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    poster_path = first / "fictional-blank-poster.pdf"
    poster_output = io.BytesIO()
    poster = canvas.Canvas(poster_output, pagesize=(1_800, 1_800))
    poster.showPage()
    poster.save()
    poster_path.write_bytes(poster_output.getvalue())

    native_path = second / "fictional-native.pdf"
    native_output = io.BytesIO()
    native = canvas.Canvas(native_output, pagesize=letter)
    native.drawString(72, 720, "FICTIONAL NATIVE PAGE")
    native.save()
    native_path.write_bytes(native_output.getvalue())

    company_bytes = (poster_path.stat().st_size, native_path.stat().st_size)
    contract = build_two_company_source_contract(
        root=root,
        expected_file_counts=(1, 1),
        expected_total_files=2,
        expected_company_bytes=company_bytes,
        expected_total_bytes=sum(company_bytes),
    )
    census = inspect_pdf_ocr_requirements(
        contract,
        expected_pdf_files=2,
        expected_total_pages=2,
        expected_required_files=1,
        expected_required_pages=1,
        expected_all_native_files=1,
        expected_all_ocr_files=1,
        expected_mixed_files=0,
        expected_max_required_page_index=1,
        expected_max_required_pages_per_file=1,
    )

    poster_hash = contract.companies[0].files[0].content_sha256
    assert census.render_admissions_by_content_sha256[poster_hash] == (
        _source_render_plan(page=1, chosen_dpi=150, width=3_750, height=3_750),
    )
