"""RED: durable watermark provenance must name the algorithm it executes."""

from __future__ import annotations

import hashlib

from pdf2image import convert_from_bytes

from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime
from idis.parsers.watermark_page_classifier import (
    WatermarkPageEligibility,
    classify_runtime_watermark_page,
    default_watermark_page_policy,
)
from tests._linux_poppler_gate import requires_linux_governed_poppler
from tests.slice100_task4.test_watermark_composition_root_red import (
    _fixture_pdf,
    _verified_runtime_config,
)


@requires_linux_governed_poppler
def test_durable_labels_match_spatial_foreground_dice_pipeline() -> None:
    policy = default_watermark_page_policy()
    pdf_bytes = _fixture_pdf()
    runtime = _verified_runtime_config(pdf_bytes)
    binding = resolve_pinned_poppler_runtime()
    pages = convert_from_bytes(
        pdf_bytes,
        dpi=policy.render_dpi,
        first_page=1,
        last_page=1,
        fmt="png",
        thread_count=1,
        poppler_path=str(binding.binary_dir),
    )
    assert len(pages) == 1
    with pages[0] as rendered_page:
        provenance = classify_runtime_watermark_page(
            page_image=rendered_page,
            render_dpi=policy.render_dpi,
            page_number=1,
            content_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            eligibility=WatermarkPageEligibility(
                native_text_character_count=0,
                primary_ocr_token_count=0,
                rescue_candidate_count=24,
                rescue_completed_candidate_count=24,
                rescue_outcome="no_consensus",
            ),
            ocr_terminal_proof_sha256="d" * 64,
            runtime_config=runtime,
        )

    # Poppler renders the page before this classifier boundary; every remaining
    # label is the governed policy operation, including spatial foreground Dice.
    expected = {
        "transform_chain": ["poppler-200dpi", *policy.transform_chain],
        "similarity_metric": policy.similarity_metric,
    }
    observed = {
        "transform_chain": provenance["transform_chain"],
        "similarity_metric": provenance["similarity_metric"],
    }
    assert observed == expected
