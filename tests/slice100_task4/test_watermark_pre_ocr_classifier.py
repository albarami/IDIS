"""Direct contracts for the bounded classifier-first watermark seam."""

from __future__ import annotations

import hashlib
import json

import pytest
from pdf2image import convert_from_bytes

from idis.parsers import watermark_page_classifier as classifier
from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime
from tests._linux_poppler_gate import requires_linux_governed_poppler
from tests.slice100_task4.test_watermark_composition_root_red import (
    _fixture_pdf,
    _verified_runtime_config,
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


def _bounded_real_candidate(pdf_bytes: bytes):
    binding = resolve_pinned_poppler_runtime()
    pages = convert_from_bytes(
        pdf_bytes,
        first_page=1,
        last_page=1,
        fmt="png",
        grayscale=True,
        size=(512, 512),
        thread_count=1,
        poppler_path=str(binding.binary_dir),
    )
    assert len(pages) == 1
    return pages[0]


def test_runtime_candidate_render_policy_is_separate_exact_and_hash_bound() -> None:
    policy = classifier.default_watermark_runtime_candidate_render_policy()
    expected_transform_chain = (
        "poppler-size-512x512",
        "poppler-format-png",
        "poppler-grayscale-true",
        "require-exact-512x512",
        "require-grayscale-pixels",
        "convert-grayscale-L",
        "measure-near-white-ge-245",
        "autocontrast-cutoff-0",
        "measure-foreground-lt-224",
        "resize-grayscale-128x128-LANCZOS",
        "binary-foreground-lt-224",
        "foreground-dice",
    )
    expected_api = {
        "policy_id": "watermark-runtime-candidate-render-v1",
        "renderer": "poppler",
        "size_pixels": [512, 512],
        "output_format": "png",
        "grayscale": True,
        "exact_dimensions_required": True,
        "grayscale_pixels_required": True,
        "transform_chain": list(expected_transform_chain),
    }
    expected_governance = {
        "schema_version": "watermark-runtime-candidate-render-policy-v1",
        "runtime_candidate_policy_id": "watermark-runtime-candidate-render-v1",
        "poppler": {
            "size_pixels": [512, 512],
            "format": "png",
            "grayscale": True,
        },
        "candidate_admission": {
            "exact_width_pixels": 512,
            "exact_height_pixels": 512,
            "grayscale_pixels_required": True,
        },
        "transform_chain": list(expected_transform_chain),
    }

    assert policy.to_dict() == expected_api
    assert policy.governance_payload() == expected_governance
    assert policy.policy_sha256 == _canonical_sha256(expected_governance)
    assert policy.transform_chain == classifier.WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN
    assert policy.policy_id != classifier.default_watermark_page_policy().policy_id
    assert policy.policy_sha256 != classifier.default_watermark_page_policy().policy_sha256


@requires_linux_governed_poppler
def test_real_512x512_candidate_emits_typed_pre_ocr_v2_without_terminal_claims() -> None:
    pdf_bytes = _fixture_pdf()
    runtime = _verified_runtime_config(pdf_bytes)
    content_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    candidate_policy = classifier.default_watermark_runtime_candidate_render_policy()

    with _bounded_real_candidate(pdf_bytes) as candidate:
        assert candidate.size == (512, 512)
        assert candidate.format == "PNG"
        result = classifier.classify_pre_ocr_runtime_watermark_candidate(
            candidate_image=candidate,
            page_number=1,
            content_sha256=content_sha256,
            native_text_character_count=0,
            runtime_config=runtime,
        )
        payload = result.to_dict()

    assert isinstance(result, classifier.WatermarkPreOcrClassification)
    assert set(payload) == classifier.WATERMARK_PAGE_CLASSIFICATION_V2_FIELDS
    assert payload["schema_version"] == "watermark-page-classification-v2"
    assert payload["classification"] == "watermark_only"
    assert payload["classification_phase"] == "pre_ocr"
    assert payload["full_resolution_ocr_attempted"] is False
    assert payload["content_sha256"] == content_sha256
    assert payload["page_number"] == 1
    assert payload["signed_exemplar_policy_id"] == runtime.policy.policy_id
    assert payload["signed_exemplar_policy_sha256"] == runtime.policy.policy_sha256
    assert payload["runtime_candidate_policy_id"] == candidate_policy.policy_id
    assert payload["runtime_candidate_policy_sha256"] == candidate_policy.policy_sha256
    assert payload["exemplar_reference"] == runtime.exemplar_reference.to_dict()
    assert payload["transform_chain"] == list(
        classifier.WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN
    )
    assert payload["actual_width_pixels"] == 512
    assert payload["actual_height_pixels"] == 512
    assert payload["actual_mode"] in {"L", "RGB"}
    assert payload["transform_bounds"] == {
        "near_white_fraction_min": "0.970000",
        "near_white_fraction_max": "0.985000",
        "foreground_ink_fraction_min": "0.015000",
        "foreground_ink_fraction_max": "0.030000",
        "similarity_min": "0.800000",
    }
    assert payload["similarity_metric"] == "foreground_dice"
    assert len(str(payload["canonical_render_sha256"])) == 64
    forbidden_claims = {
        "ocr_terminal_proof_sha256",
        "primary_ocr_token_count",
        "rescue_candidate_count",
        "rescue_completed_candidate_count",
        "rescue_outcome",
        "rescue_policy_id",
    }
    assert forbidden_claims.isdisjoint(payload)


@pytest.mark.parametrize(
    "native_text_character_count",
    [pytest.param(1, id="native-text-present"), pytest.param(False, id="boolean-zero")],
)
@requires_linux_governed_poppler
def test_pre_ocr_classifier_requires_an_exact_integer_zero_native_text_count(
    native_text_character_count: int,
) -> None:
    pdf_bytes = _fixture_pdf()
    runtime = _verified_runtime_config(pdf_bytes)
    with (
        _bounded_real_candidate(pdf_bytes) as candidate,
        pytest.raises(classifier.WatermarkPageClassificationError) as caught,
    ):
        classifier.classify_pre_ocr_runtime_watermark_candidate(
            candidate_image=candidate,
            page_number=1,
            content_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            native_text_character_count=native_text_character_count,
            runtime_config=runtime,
        )

    assert caught.value.code == "WATERMARK_PAGE_NOT_ELIGIBLE"


@requires_linux_governed_poppler
def test_pre_ocr_classifier_rejects_non_grayscale_or_wrong_sized_candidates() -> None:
    pdf_bytes = _fixture_pdf()
    runtime = _verified_runtime_config(pdf_bytes)
    content_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    with _bounded_real_candidate(pdf_bytes) as candidate:
        wrong_size = candidate.crop((0, 0, 511, 512))
        try:
            with pytest.raises(classifier.WatermarkPageClassificationError) as size_error:
                classifier.classify_pre_ocr_runtime_watermark_candidate(
                    candidate_image=wrong_size,
                    page_number=1,
                    content_sha256=content_sha256,
                    native_text_character_count=0,
                    runtime_config=runtime,
                )
        finally:
            wrong_size.close()

        candidate.putpixel((0, 0), (255, 0, 0))
        with pytest.raises(classifier.WatermarkPageClassificationError) as color_error:
            classifier.classify_pre_ocr_runtime_watermark_candidate(
                candidate_image=candidate,
                page_number=1,
                content_sha256=content_sha256,
                native_text_character_count=0,
                runtime_config=runtime,
            )

    assert size_error.value.code == "WATERMARK_PAGE_POLICY_INVALID"
    assert color_error.value.code == "WATERMARK_PAGE_POLICY_INVALID"
