"""RED contract for the exemplar-anchored watermark-page classifier.

The production module is deliberately a pure image-classification seam:

``default_watermark_page_policy()``
    Return the immutable, hashed ``exemplar-watermark-page-v1`` policy.
``WatermarkPageEligibility``
    Bind the completed native/OCR/rescue terminal state.
``WatermarkPageExemplarReference``
    Bind the signed exemplar and its canonical bitmap to the policy.
``classify_watermark_page(...)``
    Return a provenance-only terminal record or fail closed with a stable code.
``foreground_dice_meets_threshold(...)``
    Apply the policy threshold with integer arithmetic, never rounded floats.

Every valid synthetic exemplar is nevertheless produced through the production
governance path: source page -> PDF -> real 200-DPI Poppler render -> governed
canonical constructor -> manifest -> Ed25519 test signature -> production
verification.  No API, database, object store, source room, OCR engine, or
network service is touched.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import replace
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest
from pdf2image import convert_from_bytes  # type: ignore[import-untyped]
from PIL import Image, ImageDraw

from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime
from tests._linux_poppler_gate import requires_linux_governed_poppler
from tests.slice100_task4.test_watermark_classifier_domains_red import _pdf_from_page
from tests.slice100_task4.test_watermark_composition_root_red import _verified_runtime_config

_MODULE_NAME = "idis.parsers.watermark_page_classifier"
_POLICY_ID = "exemplar-watermark-page-v1"
_CLASSIFICATION_SCHEMA = "watermark-page-classification-v1"
_CONTENT_SHA256 = "c" * 64
_PAGE_NUMBER = 1

_EXPECTED_POLICY = {
    "policy_id": _POLICY_ID,
    "render_dpi": 200,
    "grayscale_mode": "L",
    "near_white_pixel_minimum": 245,
    "near_white_fraction_minimum": "0.970000",
    "near_white_fraction_maximum": "0.985000",
    "autocontrast_cutoff": 0,
    "foreground_pixel_maximum_exclusive": 224,
    "foreground_ink_fraction_minimum": "0.015000",
    "foreground_ink_fraction_maximum": "0.030000",
    "canonical_width": 128,
    "canonical_height": 128,
    "resampling": "LANCZOS",
    "similarity_metric": "foreground_dice",
    "similarity_minimum_numerator": 4,
    "similarity_minimum_denominator": 5,
    "persisted_decimal_places": 6,
    "persisted_rounding": "ROUND_HALF_EVEN",
    "allowed_rotations_degrees": [0],
    "deskew_enabled": False,
    "adaptive_threshold_enabled": False,
    "morphology_enabled": False,
    "ocr_token_matching_enabled": False,
    "transform_chain": [
        "convert-grayscale-L",
        "measure-near-white-ge-245",
        "autocontrast-cutoff-0",
        "measure-foreground-lt-224",
        "resize-grayscale-128x128-LANCZOS",
        "binary-foreground-lt-224",
        "foreground-dice",
    ],
}

# The production hash must bind the exact policy object already sealed by the
# reviewer-approved all-267 prescan, while ``to_dict`` remains the typed API view.
_EXPECTED_POLICY_HASH_PAYLOAD = {
    "schema_version": "exemplar-watermark-page-policy-v1",
    "classifier_policy_id": _POLICY_ID,
    "render": {"dpi": 200, "mode": "L", "page_order": "ascending"},
    "eligibility": {
        "native_text_span_count": 0,
        "primary_ocr_token_count": 0,
        "rescue_attempted_candidate_count": 24,
        "rescue_completed_candidate_count": 24,
        "rescue_outcome": "no_consensus",
        "rescue_policy_id": "pdf-ocr-rescue-v1",
    },
    "faint_raster": {
        "near_white_pixel_minimum": 245,
        "near_white_fraction_inclusive": ["0.970000", "0.985000"],
        "autocontrast_cutoff": 0,
        "binary_rule": "pixel < 224 -> 0, else 255",
        "foreground_ink_ratio_inclusive": ["0.015000", "0.030000"],
    },
    "canonical_bitmap": {
        "source": "autocontrasted_grayscale_full_page",
        "width": 128,
        "height": 128,
        "resize": "LANCZOS",
        "post_resize_binary_rule": "pixel < 224 -> 0, else 255",
        "crop": False,
        "reposition": False,
    },
    "similarity": {
        "metric": "foreground_dice",
        "threshold_inclusive": "0.800000",
        "decision_arithmetic": "exact_integer_cross_product",
        "persisted_rounding": "decimal_round_half_even_6",
    },
    "forbidden_transforms": [
        "adaptive_threshold",
        "crop",
        "deskew",
        "inversion",
        "morphology",
        "ocr_token_matching",
        "per_document_tuning",
        "rotation_45_degrees",
    ],
}


def _api() -> ModuleType:
    """Import the planned seam or produce one stable, intentional RED."""

    try:
        module = importlib.import_module(_MODULE_NAME)
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"TASK4_WATERMARK_PAGE_API_MISSING: {_MODULE_NAME}: {exc}",
            pytrace=False,
        )

    required = (
        "WatermarkPageClassificationError",
        "WatermarkPageEligibility",
        "WatermarkPageExemplarReference",
        "classify_watermark_page",
        "default_watermark_page_policy",
        "foreground_dice_meets_threshold",
    )
    missing = tuple(name for name in required if not hasattr(module, name))
    if missing:
        pytest.fail(
            f"TASK4_WATERMARK_PAGE_API_MISSING: {_MODULE_NAME}.{','.join(missing)}",
            pytrace=False,
        )
    return module


def _error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    return str(getattr(code, "value", code))


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _page_with_foreground_count(foreground_pixels: int) -> Image.Image:
    """Return a 1,000,000-pixel binary page with an exact ink fraction."""

    assert 0 < foreground_pixels < 1_000_000
    image = Image.new("L", (1000, 1000), color=255)
    draw = ImageDraw.Draw(image)
    complete_rows, remainder = divmod(foreground_pixels, 1000)
    if complete_rows:
        draw.rectangle((0, 0, 999, complete_rows - 1), fill=0)
    if remainder:
        draw.rectangle((0, complete_rows, remainder - 1, complete_rows), fill=0)
    return image


def _synthetic_stamp(*, horizontal_shift: int = 0, vertical: bool = False) -> Image.Image:
    """Create a fictional 2%-ink structural mark on a near-white page."""

    image = Image.new("L", (1000, 1000), color=255)
    draw = ImageDraw.Draw(image)
    if vertical:
        draw.rectangle((450, 400, 549, 599), fill=0)
    else:
        draw.rectangle((400 + horizontal_shift, 450, 599 + horizontal_shift, 549), fill=0)
    return image


def _eligibility(module: ModuleType, **overrides: object) -> Any:
    values: dict[str, object] = {
        "native_text_character_count": 0,
        "primary_ocr_token_count": 0,
        "rescue_candidate_count": 24,
        "rescue_completed_candidate_count": 24,
        "rescue_outcome": "no_consensus",
    }
    values.update(overrides)
    return module.WatermarkPageEligibility(**values)


def _reference(module: ModuleType, policy: Any, exemplar: Image.Image, **overrides: object) -> Any:
    assert policy is module.default_watermark_page_policy()
    runtime = _verified_runtime_config(_pdf_from_page(exemplar))
    return replace(runtime.exemplar_reference, **overrides)


def _classify(
    module: ModuleType,
    *,
    page: Image.Image,
    exemplar: Image.Image,
    eligibility: Any | None = None,
    reference: Any | None = None,
    render_dpi: object = 200,
    page_number: object = _PAGE_NUMBER,
) -> Any:
    policy = module.default_watermark_page_policy()
    exemplar_pdf = _pdf_from_page(exemplar)
    runtime = _verified_runtime_config(exemplar_pdf)
    binding = resolve_pinned_poppler_runtime()
    rendered = convert_from_bytes(
        exemplar_pdf,
        dpi=policy.render_dpi,
        first_page=1,
        last_page=1,
        fmt="png",
        thread_count=1,
        poppler_path=str(binding.binary_dir),
    )
    assert len(rendered) == 1
    with rendered[0] as rendered_exemplar:
        return module.classify_watermark_page(
            page_image=page,
            render_dpi=render_dpi,
            page_number=page_number,
            content_sha256=_CONTENT_SHA256,
            exemplar_image=rendered_exemplar,
            exemplar_reference=reference or runtime.exemplar_reference,
            eligibility=eligibility or _eligibility(module),
            policy=policy,
        )


def _assert_code(module: ModuleType, expected: str, action: Any) -> None:
    error_type = module.WatermarkPageClassificationError
    assert isinstance(error_type, type) and issubclass(error_type, Exception)
    with pytest.raises(error_type) as caught:
        action()
    assert _error_code(caught.value) == expected


def test_default_policy_pins_every_transform_and_excludes_forbidden_search() -> None:
    module = _api()
    policy = module.default_watermark_page_policy()

    assert policy.to_dict() == _EXPECTED_POLICY
    assert policy.policy_sha256 == _canonical_json_sha256(_EXPECTED_POLICY_HASH_PAYLOAD)
    assert len(policy.policy_sha256) == 64

    # These public values prove the classifier cannot silently grow an adaptive
    # search space, rotate toward diagonal stamps, or match OCR tokens.
    assert policy.allowed_rotations_degrees == (0,)
    assert policy.deskew_enabled is False
    assert policy.adaptive_threshold_enabled is False
    assert policy.morphology_enabled is False
    assert policy.ocr_token_matching_enabled is False


def test_similarity_threshold_uses_exact_integer_dice_comparison() -> None:
    module = _api()
    policy = module.default_watermark_page_policy()
    admits = module.foreground_dice_meets_threshold

    # Dice = 2*intersection/(candidate+exemplar).  The implementation must
    # cross-multiply integers; persistence rounding must never admit 0.799999.
    assert admits(
        intersection_count=4,
        candidate_foreground_count=5,
        exemplar_foreground_count=5,
        policy=policy,
    )
    assert not admits(
        intersection_count=399_999,
        candidate_foreground_count=500_000,
        exemplar_foreground_count=500_000,
        policy=policy,
    )
    assert admits(
        intersection_count=400_000,
        candidate_foreground_count=500_000,
        exemplar_foreground_count=500_000,
        policy=policy,
    )


@requires_linux_governed_poppler
def test_signed_exemplar_classifies_itself_with_exact_similarity() -> None:
    module = _api()
    with _synthetic_stamp() as exemplar:
        result = _classify(module, page=exemplar, exemplar=exemplar)

    payload = result.to_dict()
    assert payload["schema_version"] == _CLASSIFICATION_SCHEMA
    assert payload["classification"] == "watermark_only"
    assert payload["similarity_metric"] == "foreground_dice"
    assert payload["similarity_score"] == "1.000000"
    assert payload["similarity_numerator"] == payload["similarity_denominator"]


@requires_linux_governed_poppler
def test_structurally_similar_fictional_stamp_clears_strict_threshold() -> None:
    module = _api()
    with _synthetic_stamp() as exemplar, _synthetic_stamp(horizontal_shift=8) as page:
        result = _classify(module, page=page, exemplar=exemplar)

    payload = result.to_dict()
    score = Decimal(payload["similarity_score"])
    assert Decimal("0.800000") <= score < Decimal("1.000000")
    assert payload["classification"] == "watermark_only"


@pytest.mark.parametrize(
    ("foreground_pixels", "near_white", "ink"),
    [
        pytest.param(30_001, "0.969999", "0.030001", id="both-upper-limits-exceeded"),
        pytest.param(14_999, "0.985001", "0.014999", id="both-lower-limits-missed"),
    ],
)
@requires_linux_governed_poppler
def test_faint_signature_fails_immediately_outside_exact_bounds(
    foreground_pixels: int,
    near_white: str,
    ink: str,
) -> None:
    module = _api()
    with _page_with_foreground_count(foreground_pixels) as page, _synthetic_stamp() as exemplar:
        _assert_code(
            module,
            "WATERMARK_PAGE_CLASSIFICATION_BELOW_THRESHOLD",
            lambda: _classify(module, page=page, exemplar=exemplar),
        )

    # The fixture itself pins the six-decimal boundary named in this RED case.
    assert Decimal(near_white) == Decimal(1) - Decimal(foreground_pixels) / Decimal(1_000_000)
    assert Decimal(ink) == Decimal(foreground_pixels) / Decimal(1_000_000)


@pytest.mark.parametrize(
    "foreground_pixels",
    [
        pytest.param(30_000, id="near-white-0.970000-ink-0.030000"),
        pytest.param(15_000, id="near-white-0.985000-ink-0.015000"),
    ],
)
@requires_linux_governed_poppler
def test_faint_signature_bounds_are_inclusive(foreground_pixels: int) -> None:
    module = _api()
    with _page_with_foreground_count(foreground_pixels) as page:
        result = _classify(module, page=page, exemplar=page)

    assert result.to_dict()["classification"] == "watermark_only"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"native_text_character_count": 1}, id="native-content-present"),
        pytest.param({"primary_ocr_token_count": 1}, id="primary-ocr-content-present"),
        pytest.param({"rescue_candidate_count": 23}, id="rescue-matrix-not-24"),
        pytest.param({"rescue_completed_candidate_count": 23}, id="rescue-matrix-incomplete"),
        pytest.param({"rescue_outcome": "rescue_text"}, id="rescue-content-present"),
        pytest.param({"rescue_outcome": "deferred"}, id="rescue-not-terminal"),
        pytest.param({"native_text_character_count": False}, id="boolean-native-count"),
    ],
)
@requires_linux_governed_poppler
def test_any_content_or_incomplete_rescue_bypasses_classifier(
    overrides: dict[str, object],
) -> None:
    module = _api()
    eligibility = _eligibility(module, **overrides)
    with _synthetic_stamp() as exemplar:
        _assert_code(
            module,
            "WATERMARK_PAGE_NOT_ELIGIBLE",
            lambda: _classify(
                module,
                page=exemplar,
                exemplar=exemplar,
                eligibility=eligibility,
            ),
        )


@requires_linux_governed_poppler
def test_unrelated_faint_structure_remains_hard_blocked() -> None:
    module = _api()
    with _synthetic_stamp() as exemplar, _synthetic_stamp(vertical=True) as unrelated:
        _assert_code(
            module,
            "WATERMARK_PAGE_CLASSIFICATION_BELOW_THRESHOLD",
            lambda: _classify(module, page=unrelated, exemplar=exemplar),
        )


@requires_linux_governed_poppler
def test_signed_exemplar_policy_binding_drift_fails_closed() -> None:
    module = _api()
    policy = module.default_watermark_page_policy()
    with _synthetic_stamp() as exemplar:
        reference = _reference(
            module,
            policy,
            exemplar,
            classifier_policy_sha256="f" * 64,
        )
        _assert_code(
            module,
            "WATERMARK_PAGE_POLICY_INVALID",
            lambda: _classify(
                module,
                page=exemplar,
                exemplar=exemplar,
                reference=reference,
            ),
        )


@requires_linux_governed_poppler
def test_signed_exemplar_canonical_bitmap_drift_fails_closed() -> None:
    module = _api()
    policy = module.default_watermark_page_policy()
    with _synthetic_stamp() as exemplar:
        reference = _reference(
            module,
            policy,
            exemplar,
            canonical_bitmap_sha256="f" * 64,
        )
        _assert_code(
            module,
            "WATERMARK_PAGE_EXEMPLAR_INVALID",
            lambda: _classify(
                module,
                page=exemplar,
                exemplar=exemplar,
                reference=reference,
            ),
        )


@pytest.mark.parametrize(
    "render_dpi",
    [pytest.param(199, id="wrong-value"), pytest.param(200.0, id="float-equal-value")],
)
@requires_linux_governed_poppler
def test_render_dpi_requires_the_exact_pinned_integer_type(render_dpi: object) -> None:
    module = _api()
    with _synthetic_stamp() as exemplar:
        _assert_code(
            module,
            "WATERMARK_PAGE_POLICY_INVALID",
            lambda: _classify(
                module,
                page=exemplar,
                exemplar=exemplar,
                render_dpi=render_dpi,
            ),
        )


@requires_linux_governed_poppler
def test_boolean_page_number_cannot_masquerade_as_page_one() -> None:
    module = _api()
    with _synthetic_stamp() as exemplar:
        _assert_code(
            module,
            "WATERMARK_PAGE_POLICY_INVALID",
            lambda: _classify(
                module,
                page=exemplar,
                exemplar=exemplar,
                page_number=True,
            ),
        )


@requires_linux_governed_poppler
def test_classification_is_byte_deterministic_and_does_not_mutate_inputs() -> None:
    module = _api()
    with _synthetic_stamp() as exemplar, _synthetic_stamp(horizontal_shift=8) as page:
        exemplar_before = exemplar.tobytes()
        page_before = page.tobytes()
        payloads = [
            json.dumps(
                _classify(module, page=page, exemplar=exemplar).to_dict(),
                sort_keys=True,
                separators=(",", ":"),
            )
            for _attempt in range(5)
        ]
        assert exemplar.tobytes() == exemplar_before
        assert page.tobytes() == page_before

    assert len(set(payloads)) == 1


@requires_linux_governed_poppler
def test_provenance_is_complete_but_never_creates_evidence_text_or_spans() -> None:
    module = _api()
    policy = module.default_watermark_page_policy()
    with _synthetic_stamp() as exemplar, _synthetic_stamp(horizontal_shift=8) as page:
        expected_reference = _reference(module, policy, exemplar)
        result = _classify(module, page=page, exemplar=exemplar)

    payload = result.to_dict()
    assert payload["schema_version"] == _CLASSIFICATION_SCHEMA
    assert payload["policy_id"] == _POLICY_ID
    assert payload["policy_sha256"] == policy.policy_sha256
    assert payload["content_sha256"] == _CONTENT_SHA256
    assert payload["page_number"] == _PAGE_NUMBER
    assert payload["exemplar_reference"] == expected_reference.to_dict()
    assert payload["transform_chain"] == _EXPECTED_POLICY["transform_chain"]
    assert set(payload["confidence_distribution"]) == {
        "near_white_fraction",
        "foreground_ink_fraction",
    }
    assert payload["similarity_metric"] == "foreground_dice"
    assert Decimal(payload["similarity_score"]) >= Decimal("0.800000")

    forbidden_keys = {
        "text",
        "tokens",
        "ocr_text",
        "normalized_text",
        "span",
        "spans",
        "span_drafts",
    }
    assert forbidden_keys.isdisjoint(payload)
    assert "text" not in json.dumps(payload, sort_keys=True).casefold()
    assert not hasattr(result, "spans")


def test_policy_object_is_immutable() -> None:
    module = _api()
    policy = module.default_watermark_page_policy()

    with pytest.raises((AttributeError, TypeError)):
        policy.render_dpi = 201
