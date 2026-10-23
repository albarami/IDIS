"""Unit contracts for sparse, per-page PDF OCR render admission."""

from __future__ import annotations

import dataclasses
import hashlib
import time
from pathlib import Path
from typing import Any

import pytest

from idis.parsers import ocr
from idis.parsers.poppler_runtime import PinnedPopplerRuntime, resolve_pinned_poppler_runtime


class _Output:
    DICT = "dict"


class _FakeImage:
    def __init__(self, *, page_number: int, width: int, height: int) -> None:
        self.page_number = page_number
        self.width = width
        self.height = height
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _legacy_admission(
    page_number: int,
    *,
    chosen_dpi: int,
    width: int,
    height: int,
) -> ocr.OcrPageRenderAdmission:
    return ocr.OcrPageRenderAdmission(
        page_number=page_number,
        schema_version=ocr.LEGACY_OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        ladder_version=ocr.OCR_DPI_LADDER_VERSION,
        configured_dpi=200,
        chosen_dpi=chosen_dpi,
        projected_width=width,
        projected_height=height,
        projected_pixel_count=width * height,
        max_pixel_count=ocr.MAX_OCR_IMAGE_PIXELS,
    )


def _test_poppler_runtime(tmp_path: Path) -> PinnedPopplerRuntime:
    runtime_root = tmp_path / "fictional-poppler"
    runtime_root.mkdir()
    files = {
        "pdftoppm.exe": runtime_root / "pdftoppm.exe",
        "pdfinfo.exe": runtime_root / "pdfinfo.exe",
        "poppler.dll": runtime_root / "poppler.dll",
    }
    for name, path in files.items():
        path.write_bytes(f"fictional-{name}-25.12.0".encode())
    return resolve_pinned_poppler_runtime(
        binary_resolver=lambda name: str(files[name]) if name in files else None,
        version_probe=lambda _path: ocr.PINNED_POPPLER_VERSION,
        platform_name="Windows",
    )


def _strict_admission(
    page_number: int,
    *,
    chosen_dpi: int,
    projected_width: int,
    projected_height: int,
    runtime: PinnedPopplerRuntime,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> ocr.OcrPageRenderAdmission:
    expected_width = projected_width if expected_width is None else expected_width
    expected_height = projected_height if expected_height is None else expected_height
    return ocr.OcrPageRenderAdmission(
        page_number=page_number,
        schema_version=ocr.OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        ladder_version=ocr.OCR_DPI_LADDER_VERSION,
        configured_dpi=200,
        chosen_dpi=chosen_dpi,
        projected_width=projected_width,
        projected_height=projected_height,
        projected_pixel_count=projected_width * projected_height,
        max_pixel_count=ocr.MAX_OCR_IMAGE_PIXELS,
        expected_render_width=expected_width,
        expected_render_height=expected_height,
        expected_render_pixel_count=expected_width * expected_height,
        poppler_version=runtime.version,
        poppler_runtime_identity_sha256=runtime.runtime_identity_sha256,
        expected_dimension_policy_id=ocr.POPPLER_EXPECTED_DIMENSION_POLICY_ID,
        expected_dimension_policy_sha256=ocr.POPPLER_EXPECTED_DIMENSION_POLICY_SHA256,
        comparison_mode=ocr.POPPLER_EXACT_COMPARISON_MODE,
        tolerance_pixels=0,
        tolerance_used=False,
    )


def _tesseract_payload(page_number: int) -> dict[str, object]:
    return {
        "text": [f"fictional-page-{page_number}", "evidence"],
        "conf": ["92", "94"],
        "block_num": [1, 1],
        "par_num": [1, 1],
        "line_num": [1, 1],
        "word_num": [1, 2],
    }


def _adapter_success_worker(
    source_path: str | Path,
    render_admission_payloads: tuple[dict[str, object], ...],
    language: str,
    retain_partial_results: bool,
    deadline: float,
    queue: Any,
) -> None:
    assert Path(source_path).name == "fictional-sparse.pdf"
    assert language == "eng"
    assert retain_partial_results is False
    assert deadline > time.monotonic()
    plan = ocr._ocr_page_render_admission_from_worker_payload(render_admission_payloads[0])
    admission = plan.with_observed_render(
        width=int(plan.expected_render_width or 0),
        height=int(plan.expected_render_height or 0),
    )
    text = "fictional sparse page evidence"
    receipt = ocr._accepted_page_execution_receipt(
        page_number=3,
        outcome="primary_text",
        text=text,
        primary_token_count=4,
        candidate_receipt_sha256=None,
    )
    queue.put(
        {
            "status": "success",
            "pages": [
                {
                    "page_number": 3,
                    "text": text,
                    "confidences": ["97"],
                    "execution_receipt": receipt.to_payload(),
                    "render_admission": admission._to_worker_payload(),
                }
            ],
        }
    )


def test_legacy_render_admission_is_immutable_and_retains_exact_six_key_public_value() -> None:
    admission = _legacy_admission(7, chosen_dpi=120, width=2_000, height=3_000)

    expected = {
        "schema_version": "ocr-render-admission-v1",
        "ladder_version": "ocr-dpi-ladder-v1",
        "configured_dpi": 200,
        "chosen_dpi": 120,
        "projected_pixel_count": 6_000_000,
        "max_pixel_count": 20_000_000,
    }
    assert admission.to_span_dict() == expected
    assert admission.to_metadata_dict() == {"page": 7, **expected}
    assert set(admission.to_span_dict()) == set(expected)
    with pytest.raises(dataclasses.FrozenInstanceError):
        admission.chosen_dpi = 96  # type: ignore[misc]


def test_strict_render_receipt_persists_distinct_projected_expected_and_actual_dimensions(
    tmp_path: Path,
) -> None:
    runtime = _test_poppler_runtime(tmp_path)
    plan = _strict_admission(
        7,
        chosen_dpi=120,
        projected_width=2_001,
        projected_height=3_000,
        expected_width=2_000,
        expected_height=3_000,
        runtime=runtime,
    )

    with pytest.raises(ocr.OcrError, match="lacks observed dimensions"):
        plan.to_span_dict()

    receipt = plan.with_observed_render(width=2_000, height=3_000)
    public = receipt.to_span_dict()
    assert public["schema_version"] == "ocr-render-admission-v2"
    assert public["projected_width"] == 2_001
    assert public["projected_pixel_count"] == 6_003_000
    assert public["expected_render_width"] == 2_000
    assert public["expected_render_pixel_count"] == 6_000_000
    assert public["actual_render_width"] == 2_000
    assert public["actual_render_pixel_count"] == 6_000_000
    assert public["poppler_version"] == "25.12.0"
    assert public["poppler_runtime_identity_sha256"] == runtime.runtime_identity_sha256
    assert public["comparison_mode"] == "exact-poppler-25.12.0"
    assert public["width_delta_pixels"] == 0
    assert public["height_delta_pixels"] == 0
    assert public["tolerance_pixels"] == 0
    assert public["tolerance_used"] is False
    assert receipt.to_metadata_dict() == {"page": 7, **public}
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.actual_render_width = 2_001  # type: ignore[misc]


@pytest.mark.parametrize(
    "updates",
    [
        {"chosen_dpi": 110},
        {"projected_pixel_count": 5_999_999},
        {"projected_width": 5_000, "projected_height": 5_000, "projected_pixel_count": 25_000_000},
        {"max_pixel_count": 20_000_001},
        {"schema_version": "stale"},
    ],
)
def test_render_admission_rejects_noncanonical_or_over_cap_values(
    updates: dict[str, object],
) -> None:
    runtime_identity = "8" * 64
    values: dict[str, object] = {
        "page_number": 7,
        "schema_version": ocr.OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        "ladder_version": ocr.OCR_DPI_LADDER_VERSION,
        "configured_dpi": 200,
        "chosen_dpi": 120,
        "projected_width": 2_000,
        "projected_height": 3_000,
        "projected_pixel_count": 6_000_000,
        "expected_render_width": 2_000,
        "expected_render_height": 3_000,
        "expected_render_pixel_count": 6_000_000,
        "poppler_version": ocr.PINNED_POPPLER_VERSION,
        "poppler_runtime_identity_sha256": runtime_identity,
        "expected_dimension_policy_id": ocr.POPPLER_EXPECTED_DIMENSION_POLICY_ID,
        "expected_dimension_policy_sha256": ocr.POPPLER_EXPECTED_DIMENSION_POLICY_SHA256,
        "comparison_mode": ocr.POPPLER_EXACT_COMPARISON_MODE,
        "tolerance_pixels": 0,
        "tolerance_used": False,
        "max_pixel_count": ocr.MAX_OCR_IMAGE_PIXELS,
    }
    values.update(updates)

    with pytest.raises(ocr.OcrError, match="render admission"):
        ocr.OcrPageRenderAdmission(**values)  # type: ignore[arg-type]


def test_sparse_render_worker_uses_each_chosen_dpi_and_binds_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _test_poppler_runtime(tmp_path)
    monkeypatch.setattr(ocr, "verify_pinned_poppler_runtime", lambda _runtime: None)
    admissions = (
        _strict_admission(
            2,
            chosen_dpi=150,
            projected_width=401,
            projected_height=500,
            expected_width=400,
            expected_height=500,
            runtime=runtime,
        ),
        _strict_admission(
            5,
            chosen_dpi=96,
            projected_width=321,
            projected_height=400,
            expected_width=320,
            expected_height=400,
            runtime=runtime,
        ),
    )
    images: list[_FakeImage] = []
    render_calls: list[tuple[int, int, int]] = []

    def convert_from_path(_path: str, **kwargs: object) -> list[_FakeImage]:
        page_number = int(kwargs["first_page"])
        assert kwargs["last_page"] == page_number
        admission = next(item for item in admissions if item.page_number == page_number)
        assert kwargs["dpi"] == admission.chosen_dpi
        render_calls.append((page_number, page_number, admission.chosen_dpi))
        image = _FakeImage(
            page_number=page_number,
            width=int(admission.expected_render_width or 0),
            height=int(admission.expected_render_height or 0),
        )
        images.append(image)
        return [image]

    def image_to_data(image: _FakeImage, **_kwargs: object) -> dict[str, object]:
        return _tesseract_payload(image.page_number)

    pages = ocr._ocr_pdf_file_render_admission_batches(
        source_path="fictional.pdf",
        render_admissions=admissions,
        language="eng",
        deadline=time.monotonic() + 3,
        convert_from_path=convert_from_path,
        image_to_data=image_to_data,
        output=_Output,
        compute_page_content_sha256s=False,
        poppler_runtime=runtime,
    )

    assert render_calls == [(2, 2, 150), (5, 5, 96)]
    assert all(image.closed for image in images)
    assert [page["page_number"] for page in pages] == [2, 5]
    for page, admission in zip(pages, admissions, strict=True):
        receipt_before = ocr._execution_receipt_for_worker_page(
            {key: value for key, value in page.items() if key != "render_admission"}
        )
        receipt_after = ocr._execution_receipt_for_worker_page(page)
        assert ocr.ocr_page_execution_receipt_sha256(
            receipt_after
        ) == ocr.ocr_page_execution_receipt_sha256(receipt_before)
        observed = ocr._ocr_page_render_admission_from_worker_payload(page["render_admission"])
        assert observed.same_render_plan(admission)
        assert observed.has_observed_render is True
        assert observed.actual_render_width == admission.expected_render_width
        assert observed.actual_render_height == admission.expected_render_height


def test_render_dimension_mismatch_fails_before_tesseract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _test_poppler_runtime(tmp_path)
    monkeypatch.setattr(ocr, "verify_pinned_poppler_runtime", lambda _runtime: None)
    admission = _strict_admission(
        2,
        chosen_dpi=150,
        projected_width=400,
        projected_height=500,
        runtime=runtime,
    )
    image = _FakeImage(page_number=2, width=401, height=500)

    def forbidden_image_to_data(_image: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("Tesseract must not execute after a render mismatch")

    with pytest.raises(ocr.OcrError, match="violated its page admission"):
        ocr._ocr_pdf_file_render_admission_batches(
            source_path="fictional.pdf",
            render_admissions=(admission,),
            language="eng",
            deadline=time.monotonic() + 3,
            convert_from_path=lambda *_args, **_kwargs: [image],
            image_to_data=forbidden_image_to_data,
            output=_Output,
            compute_page_content_sha256s=False,
            poppler_runtime=runtime,
        )
    assert image.closed is True


def test_adapter_forwards_absolute_deadline_and_returns_typed_admission(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "fictional-sparse.pdf"
    source_path.write_bytes(b"fictional-pdf")
    runtime = _test_poppler_runtime(tmp_path)
    admission = _strict_admission(
        3,
        chosen_dpi=120,
        projected_width=2_001,
        projected_height=3_000,
        expected_width=2_000,
        expected_height=3_000,
        runtime=runtime,
    )
    adapter = ocr.TesseractOcrAdapter(
        render_admission_file_worker_target=_adapter_success_worker,
        poppler_runtime=runtime,
    )

    pages = adapter.extract_pdf_file_with_render_admissions(
        source_path,
        render_admissions=(admission,),
        deadline=time.monotonic() + 3,
        retain_partial_results=False,
    )

    assert len(pages) == 1
    assert pages[0].page_number == 3
    assert pages[0].render_admission is not None
    assert pages[0].render_admission.same_render_plan(admission)
    assert pages[0].render_admission.has_observed_render is True
    assert pages[0].render_admission.actual_render_width == 2_000


def test_sparse_partial_failure_retains_observed_admission_on_accepted_page(
    tmp_path: Path,
) -> None:
    runtime = _test_poppler_runtime(tmp_path)
    no_consensus_admission = _strict_admission(
        2,
        chosen_dpi=150,
        projected_width=400,
        projected_height=500,
        runtime=runtime,
    )
    accepted_admission = _strict_admission(
        5,
        chosen_dpi=96,
        projected_width=320,
        projected_height=400,
        runtime=runtime,
    )
    observed_no_consensus = no_consensus_admission.with_observed_render(
        width=400,
        height=500,
    )
    observed_accepted = accepted_admission.with_observed_render(width=320, height=400)
    accepted_text = "fictional accepted page evidence"
    pages = [
        {
            "page_number": 2,
            "status": "no_consensus",
            "execution_receipt": ocr._no_consensus_page_execution_receipt(
                page_number=2,
                candidate_receipt_sha256=hashlib.sha256(b"candidate").hexdigest(),
            ).to_payload(),
            "page_content_sha256": hashlib.sha256(b"page-two").hexdigest(),
            "render_admission": observed_no_consensus._to_worker_payload(),
        },
        {
            "page_number": 5,
            "text": accepted_text,
            "confidences": ["95"],
            "execution_receipt": ocr._accepted_page_execution_receipt(
                page_number=5,
                outcome="primary_text",
                text=accepted_text,
                primary_token_count=4,
                candidate_receipt_sha256=None,
            ).to_payload(),
            "render_admission": observed_accepted._to_worker_payload(),
        },
    ]
    payload = ocr._ocr_pdf_worker_payload(
        pages,
        retain_accepted_pages=True,
        allow_sparse_pages=True,
    )

    with pytest.raises(ocr.OcrRescueNoConsensusError) as caught:
        ocr._pages_from_worker_payload(payload)

    assert caught.value.file_proof is not None
    assert [receipt.page_number for receipt in caught.value.file_proof.page_receipts] == [2, 5]
    assert len(caught.value.accepted_pages) == 1
    assert caught.value.accepted_pages[0].render_admission == observed_accepted
