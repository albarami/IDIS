"""RED contracts separating full-resolution rasters from signed canonical masks.

Every valid exemplar in this module follows the project-authored offline path:
PDF creation, real 200-DPI Poppler rendering, governed downscale/binarization,
manifest construction, Ed25519 test-signing, verification, and typed runtime
configuration.  No hand-assembled 128x128 exemplar is used.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from pdf2image import convert_from_bytes
from PIL import Image, ImageDraw, ImageOps
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from idis.parsers import watermark_page_classifier as classifier
from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime
from idis.parsers.watermark_page_classifier import WatermarkPageEligibility
from tests._linux_poppler_gate import requires_linux_governed_poppler
from tests.slice100_task4.test_watermark_composition_root_red import (
    _fixture_pdf,
    _signed_manifest,
    _verified_runtime_config,
)


def _domain_separating_page() -> Image.Image:
    """Return valid 2.3%-ink source whose canonical mask has 6.25% ink."""

    page = Image.new("L", (1000, 1000), color=255)
    draw = ImageDraw.Draw(page)
    for x in range(8, 248, 16):
        draw.line((x, 0, x, 999), fill=0, width=1)
    return page


def _pdf_from_page(page: Image.Image) -> bytes:
    png = io.BytesIO()
    page.save(png, format="PNG")
    png.seek(0)
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(360, 360), invariant=1)
    document.drawImage(
        ImageReader(png),
        0,
        0,
        width=360,
        height=360,
        preserveAspectRatio=False,
        mask=None,
    )
    document.showPage()
    document.save()
    return output.getvalue()


def _domain_separating_pdf() -> bytes:
    with _domain_separating_page() as page:
        return _pdf_from_page(page)


def _render_page(pdf_bytes: bytes) -> Image.Image:
    binding = resolve_pinned_poppler_runtime()
    pages = convert_from_bytes(
        pdf_bytes,
        dpi=200,
        first_page=1,
        last_page=1,
        fmt="png",
        thread_count=1,
        poppler_path=str(binding.binary_dir),
    )
    assert len(pages) == 1
    return pages[0]


def _ink_fractions(page: Image.Image) -> tuple[Decimal, Decimal]:
    """Independent oracle for full-raster and canonical-mask ink fractions."""

    with (
        page.convert("L") as grayscale,
        ImageOps.autocontrast(grayscale, cutoff=0) as contrasted,
        contrasted.resize((128, 128), Image.Resampling.LANCZOS) as resized,
        resized.point(lambda pixel: 0 if pixel < 224 else 255) as canonical,
    ):
        full_ink = sum(contrasted.histogram()[:224])
        full_pixels = contrasted.width * contrasted.height
        canonical_ink = canonical.tobytes().count(0)
    return (
        Decimal(full_ink) / Decimal(full_pixels),
        Decimal(canonical_ink) / Decimal(128 * 128),
    )


def _eligibility() -> WatermarkPageEligibility:
    return WatermarkPageEligibility(
        native_text_character_count=0,
        primary_ocr_token_count=0,
        rescue_candidate_count=24,
        rescue_completed_candidate_count=24,
        rescue_outcome="no_consensus",
    )


@requires_linux_governed_poppler
def test_signed_canonical_mask_is_not_revalidated_as_a_full_resolution_faint_raster() -> None:
    """Canonical geometry may differ from source faintness without changing domains."""

    pdf_bytes = _domain_separating_pdf()
    with _render_page(pdf_bytes) as rendered_page:
        full_ink, canonical_ink = _ink_fractions(rendered_page)
    assert full_ink == Decimal("0.023")
    assert Decimal("0.015") <= full_ink <= Decimal("0.03")
    assert canonical_ink == Decimal("0.0625")
    assert canonical_ink > Decimal("0.03")

    # The manifest helper performs the real render/downscale/binarize/sign path.
    # Runtime construction must validate the signed canonical domain as canonical,
    # never route it through full-resolution near-white/ink eligibility again.
    runtime = _verified_runtime_config(pdf_bytes)
    with _render_page(pdf_bytes) as rendered_page:
        provenance = classifier.classify_runtime_watermark_page(
            page_image=rendered_page,
            render_dpi=200,
            page_number=1,
            content_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            eligibility=_eligibility(),
            ocr_terminal_proof_sha256=hashlib.sha256(
                b"fictional-domain-separated-ocr-proof"
            ).hexdigest(),
            runtime_config=runtime,
        )

    assert provenance["classification"] == "watermark_only"
    assert provenance["similarity_score"] == "1.000000"


@requires_linux_governed_poppler
def test_runtime_never_sends_signed_128x128_material_to_full_raster_preparation(
    monkeypatch: Any,
) -> None:
    """Spy on the domain boundary because dimensions are erased after preparation."""

    pdf_bytes = _fixture_pdf()
    # Complete render + manifest + test-signing before installing the runtime spy.
    # This keeps fixture production out of the observed classifier call set.
    _signed_manifest(pdf_bytes)
    real_prepare = classifier._prepare_page  # noqa: SLF001
    observed_dimensions: list[tuple[int, int]] = []

    def observe_full_raster(image: Image.Image, policy: Any) -> Any:
        observed_dimensions.append((image.width, image.height))
        return real_prepare(image, policy)

    monkeypatch.setattr(classifier, "_prepare_page", observe_full_raster)
    runtime = _verified_runtime_config(pdf_bytes)
    with _render_page(pdf_bytes) as rendered_page:
        provenance = classifier.classify_runtime_watermark_page(
            page_image=rendered_page,
            render_dpi=200,
            page_number=1,
            content_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            eligibility=_eligibility(),
            ocr_terminal_proof_sha256=hashlib.sha256(
                b"fictional-domain-boundary-ocr-proof"
            ).hexdigest(),
            runtime_config=runtime,
        )

    assert provenance["classification"] == "watermark_only"
    assert observed_dimensions
    assert (1000, 1000) in observed_dimensions
    assert (128, 128) not in observed_dimensions, (
        "signed canonical exemplar material crossed into the full-resolution "
        f"faint-raster preparation domain: {observed_dimensions!r}"
    )


@pytest.mark.parametrize(
    "mutated_bitmap",
    [
        pytest.param(bytes(2048), id="all-foreground"),
        pytest.param(bytes([255]) * 2048, id="all-background"),
        pytest.param(bytes(2047), id="one-byte-short"),
        pytest.param(bytes(2049), id="one-byte-long"),
    ],
)
@requires_linux_governed_poppler
def test_canonical_exemplar_domain_rejects_degenerate_or_wrong_geometry_mutations(
    mutated_bitmap: bytes,
) -> None:
    """Negative cases may mutate a valid production-constructed signed base."""

    valid = _verified_runtime_config(_fixture_pdf())
    mutated_reference = replace(
        valid.exemplar_reference,
        canonical_bitmap_sha256=hashlib.sha256(mutated_bitmap).hexdigest(),
    )

    with pytest.raises(classifier.WatermarkPageClassificationError) as caught:
        classifier.WatermarkPageRuntimeConfig(
            policy=valid.policy,
            exemplar_reference=mutated_reference,
            exemplar_canonical_bitmap=mutated_bitmap,
        )

    assert caught.value.code == "WATERMARK_PAGE_EXEMPLAR_INVALID"


@requires_linux_governed_poppler
def test_canonical_exemplar_reference_requires_an_integer_page_number() -> None:
    valid = _verified_runtime_config(_fixture_pdf())
    boolean_page_reference = replace(valid.exemplar_reference, page_number=True)

    with pytest.raises(classifier.WatermarkPageClassificationError) as caught:
        classifier.WatermarkPageRuntimeConfig(
            policy=valid.policy,
            exemplar_reference=boolean_page_reference,
            exemplar_canonical_bitmap=valid.exemplar_canonical_bitmap,
        )

    assert caught.value.code == "WATERMARK_PAGE_EXEMPLAR_INVALID"
