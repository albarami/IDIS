"""Deterministic, exemplar-anchored classification of watermark-only pages.

This module is intentionally pure.  It performs no rendering, OCR, I/O, or
network access.  The original v1 entry point consumes an already-rendered page
and completed OCR execution facts.  The distinct v2 pre-OCR entry point consumes
an exact, bounded 512 x 512 Poppler candidate, reduces it to the governed
128 x 128 binary comparison domain, and consumes only the native-text fact; it
cannot claim that primary or rescue OCR ran.
"""

from __future__ import annotations

import hashlib
import json
import string
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from PIL import Image, ImageChops, ImageOps

WATERMARK_PAGE_POLICY_ID = "exemplar-watermark-page-v1"
WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION = "watermark-page-classification-v1"
WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION = "watermark-page-classification-v2"
WATERMARK_RUNTIME_CANDIDATE_RENDER_POLICY_ID = "watermark-runtime-candidate-render-v1"

_FRACTION_SCALE = 1_000_000
_PERSISTENCE_QUANTUM = Decimal("0.000001")
_HEX_DIGITS = frozenset(string.hexdigits.lower())
_TRANSFORM_CHAIN = (
    "convert-grayscale-L",
    "measure-near-white-ge-245",
    "autocontrast-cutoff-0",
    "measure-foreground-lt-224",
    "resize-grayscale-128x128-LANCZOS",
    "binary-foreground-lt-224",
    "foreground-dice",
)
_RUNTIME_TRANSFORM_CHAIN = ("poppler-200dpi", *_TRANSFORM_CHAIN)
WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN = (
    "poppler-size-512x512",
    "poppler-format-png",
    "poppler-grayscale-true",
    "require-exact-512x512",
    "require-grayscale-pixels",
    *_TRANSFORM_CHAIN,
)
_RUNTIME_TRANSFORM_BOUNDS = {
    "near_white_fraction_min": "0.970000",
    "near_white_fraction_max": "0.985000",
    "foreground_ink_fraction_min": "0.015000",
    "foreground_ink_fraction_max": "0.030000",
    "similarity_min": "0.800000",
}
WATERMARK_PAGE_CLASSIFICATION_V2_FIELDS = frozenset(
    {
        "schema_version",
        "classification",
        "classification_phase",
        "full_resolution_ocr_attempted",
        "content_sha256",
        "page_number",
        "signed_exemplar_policy_id",
        "signed_exemplar_policy_sha256",
        "runtime_candidate_policy_id",
        "runtime_candidate_policy_sha256",
        "exemplar_reference",
        "transform_chain",
        "actual_width_pixels",
        "actual_height_pixels",
        "actual_mode",
        "confidence_distribution",
        "transform_bounds",
        "similarity_metric",
        "similarity_score",
        "similarity_numerator",
        "similarity_denominator",
        "canonical_render_sha256",
    }
)


class WatermarkPageClassificationError(Exception):
    """Fail-closed classifier error carrying a stable, secret-safe code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class WatermarkPagePolicy:
    """Pinned transform and comparison policy for watermark-page v1."""

    policy_id: str = WATERMARK_PAGE_POLICY_ID
    render_dpi: int = 200
    grayscale_mode: str = "L"
    near_white_pixel_minimum: int = 245
    near_white_fraction_minimum: Decimal = Decimal("0.970000")
    near_white_fraction_maximum: Decimal = Decimal("0.985000")
    autocontrast_cutoff: int = 0
    foreground_pixel_maximum_exclusive: int = 224
    foreground_ink_fraction_minimum: Decimal = Decimal("0.015000")
    foreground_ink_fraction_maximum: Decimal = Decimal("0.030000")
    canonical_width: int = 128
    canonical_height: int = 128
    resampling: str = "LANCZOS"
    similarity_metric: str = "foreground_dice"
    similarity_minimum_numerator: int = 4
    similarity_minimum_denominator: int = 5
    persisted_decimal_places: int = 6
    persisted_rounding: str = "ROUND_HALF_EVEN"
    allowed_rotations_degrees: tuple[int, ...] = (0,)
    deskew_enabled: bool = False
    adaptive_threshold_enabled: bool = False
    morphology_enabled: bool = False
    ocr_token_matching_enabled: bool = False
    transform_chain: tuple[str, ...] = _TRANSFORM_CHAIN

    def to_dict(self) -> dict[str, object]:
        """Return the canonical policy payload used for its governance hash."""

        return {
            "policy_id": self.policy_id,
            "render_dpi": self.render_dpi,
            "grayscale_mode": self.grayscale_mode,
            "near_white_pixel_minimum": self.near_white_pixel_minimum,
            "near_white_fraction_minimum": format(self.near_white_fraction_minimum, ".6f"),
            "near_white_fraction_maximum": format(self.near_white_fraction_maximum, ".6f"),
            "autocontrast_cutoff": self.autocontrast_cutoff,
            "foreground_pixel_maximum_exclusive": (self.foreground_pixel_maximum_exclusive),
            "foreground_ink_fraction_minimum": format(self.foreground_ink_fraction_minimum, ".6f"),
            "foreground_ink_fraction_maximum": format(self.foreground_ink_fraction_maximum, ".6f"),
            "canonical_width": self.canonical_width,
            "canonical_height": self.canonical_height,
            "resampling": self.resampling,
            "similarity_metric": self.similarity_metric,
            "similarity_minimum_numerator": self.similarity_minimum_numerator,
            "similarity_minimum_denominator": self.similarity_minimum_denominator,
            "persisted_decimal_places": self.persisted_decimal_places,
            "persisted_rounding": self.persisted_rounding,
            "allowed_rotations_degrees": list(self.allowed_rotations_degrees),
            "deskew_enabled": self.deskew_enabled,
            "adaptive_threshold_enabled": self.adaptive_threshold_enabled,
            "morphology_enabled": self.morphology_enabled,
            "ocr_token_matching_enabled": self.ocr_token_matching_enabled,
            "transform_chain": list(self.transform_chain),
        }

    def governance_payload(self) -> dict[str, object]:
        """Return the exact policy object sealed by the reviewed prescan."""

        return {
            "schema_version": "exemplar-watermark-page-policy-v1",
            "classifier_policy_id": self.policy_id,
            "render": {
                "dpi": self.render_dpi,
                "mode": self.grayscale_mode,
                "page_order": "ascending",
            },
            "eligibility": {
                "native_text_span_count": 0,
                "primary_ocr_token_count": 0,
                "rescue_attempted_candidate_count": 24,
                "rescue_completed_candidate_count": 24,
                "rescue_outcome": "no_consensus",
                "rescue_policy_id": "pdf-ocr-rescue-v1",
            },
            "faint_raster": {
                "near_white_pixel_minimum": self.near_white_pixel_minimum,
                "near_white_fraction_inclusive": [
                    format(self.near_white_fraction_minimum, ".6f"),
                    format(self.near_white_fraction_maximum, ".6f"),
                ],
                "autocontrast_cutoff": self.autocontrast_cutoff,
                "binary_rule": "pixel < 224 -> 0, else 255",
                "foreground_ink_ratio_inclusive": [
                    format(self.foreground_ink_fraction_minimum, ".6f"),
                    format(self.foreground_ink_fraction_maximum, ".6f"),
                ],
            },
            "canonical_bitmap": {
                "source": "autocontrasted_grayscale_full_page",
                "width": self.canonical_width,
                "height": self.canonical_height,
                "resize": self.resampling,
                "post_resize_binary_rule": "pixel < 224 -> 0, else 255",
                "crop": False,
                "reposition": False,
            },
            "similarity": {
                "metric": self.similarity_metric,
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

    @property
    def policy_sha256(self) -> str:
        """Return the SHA-256 of the exact canonical policy payload."""

        return _canonical_json_sha256(self.governance_payload())


@dataclass(frozen=True, slots=True)
class WatermarkRuntimeCandidateRenderPolicy:
    """Code-pinned render contract for the bounded pre-OCR candidate.

    Poppler execution remains outside this pure module.  This policy records the
    exact arguments the caller must use and the image invariants revalidated at
    the classification boundary.
    """

    policy_id: str = WATERMARK_RUNTIME_CANDIDATE_RENDER_POLICY_ID
    renderer: str = "poppler"
    size_width_pixels: int = 512
    size_height_pixels: int = 512
    output_format: str = "png"
    grayscale: bool = True
    exact_dimensions_required: bool = True
    grayscale_pixels_required: bool = True
    transform_chain: tuple[str, ...] = WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN

    def to_dict(self) -> dict[str, object]:
        """Return the canonical API view of the bounded render contract."""

        return {
            "policy_id": self.policy_id,
            "renderer": self.renderer,
            "size_pixels": [self.size_width_pixels, self.size_height_pixels],
            "output_format": self.output_format,
            "grayscale": self.grayscale,
            "exact_dimensions_required": self.exact_dimensions_required,
            "grayscale_pixels_required": self.grayscale_pixels_required,
            "transform_chain": list(self.transform_chain),
        }

    def governance_payload(self) -> dict[str, object]:
        """Return the exact code-owned object bound by ``policy_sha256``."""

        return {
            "schema_version": "watermark-runtime-candidate-render-policy-v1",
            "runtime_candidate_policy_id": self.policy_id,
            "poppler": {
                "size_pixels": [self.size_width_pixels, self.size_height_pixels],
                "format": self.output_format,
                "grayscale": self.grayscale,
            },
            "candidate_admission": {
                "exact_width_pixels": self.size_width_pixels,
                "exact_height_pixels": self.size_height_pixels,
                "grayscale_pixels_required": self.grayscale_pixels_required,
            },
            "transform_chain": list(self.transform_chain),
        }

    @property
    def policy_sha256(self) -> str:
        """Return the deterministic hash of the canonical runtime policy."""

        return _canonical_json_sha256(self.governance_payload())


@dataclass(frozen=True, slots=True)
class WatermarkPageEligibility:
    """Content-free terminal facts required before classification may run."""

    native_text_character_count: int
    primary_ocr_token_count: int
    rescue_candidate_count: int
    rescue_completed_candidate_count: int
    rescue_outcome: str


@dataclass(frozen=True, slots=True)
class WatermarkPageExemplarReference:
    """Signed-exemplar bindings needed to reject policy or bitmap drift."""

    exemplar_id: str
    content_sha256: str
    page_number: int
    attestation_envelope_sha256: str
    canonical_bitmap_sha256: str
    classifier_policy_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the exact persistence-safe exemplar reference."""

        return {
            "exemplar_id": self.exemplar_id,
            "content_sha256": self.content_sha256,
            "page_number": self.page_number,
            "attestation_envelope_sha256": self.attestation_envelope_sha256,
            "canonical_bitmap_sha256": self.canonical_bitmap_sha256,
            "classifier_policy_sha256": self.classifier_policy_sha256,
        }


@dataclass(frozen=True, slots=True)
class _PreparedFullResolutionRaster:
    """Governed measurements derived only from a rendered source-page raster."""

    near_white_count: int
    foreground_ink_count: int
    source_pixel_count: int
    canonical_bitmap: bytes = field(repr=False)
    canonical_foreground_count: int


@dataclass(frozen=True, slots=True)
class _ValidatedCanonicalExemplar:
    """Signed 128 x 128 binary exemplar material, never a source-page raster."""

    reference: WatermarkPageExemplarReference
    canonical_bitmap: bytes = field(repr=False)
    canonical_foreground_count: int
    packed_bitmap_sha256: str


@dataclass(frozen=True, slots=True)
class WatermarkPageRuntimeConfig:
    """Verified, immutable classifier material passed through parser composition.

    The packed bitmap is deliberately excluded from ``repr`` so governance pixels
    cannot be emitted by an exception or diagnostic representation.  Construction
    validates the exact pinned policy and the signed exemplar binding once, before
    any document is parsed.
    """

    policy: WatermarkPagePolicy
    exemplar_reference: WatermarkPageExemplarReference
    exemplar_canonical_bitmap: bytes = field(repr=False)
    _validated_exemplar: _ValidatedCanonicalExemplar = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy, WatermarkPagePolicy)
            or self.policy.to_dict() != _DEFAULT_POLICY.to_dict()
            or self.policy.policy_sha256 != _DEFAULT_POLICY.policy_sha256
        ):
            raise WatermarkPageClassificationError(
                "WATERMARK_PAGE_EXEMPLAR_INVALID",
                "runtime classifier configuration is invalid",
            )
        validated = _validate_signed_canonical_exemplar(
            reference=self.exemplar_reference,
            packed_bitmap=self.exemplar_canonical_bitmap,
            policy=self.policy,
        )
        object.__setattr__(self, "_validated_exemplar", validated)


@dataclass(frozen=True, slots=True)
class WatermarkPageClassification:
    """Text-free provenance for one admitted watermark-only page."""

    schema_version: str
    classification: str
    policy_id: str
    policy_sha256: str
    content_sha256: str
    page_number: int
    exemplar_reference: WatermarkPageExemplarReference
    transform_chain: tuple[str, ...]
    near_white_fraction: str
    foreground_ink_fraction: str
    similarity_metric: str
    similarity_score: str
    similarity_numerator: int
    similarity_denominator: int

    def to_dict(self) -> dict[str, object]:
        """Return the exact text-free durable representation."""

        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            "content_sha256": self.content_sha256,
            "page_number": self.page_number,
            "exemplar_reference": self.exemplar_reference.to_dict(),
            "transform_chain": list(self.transform_chain),
            "confidence_distribution": {
                "near_white_fraction": self.near_white_fraction,
                "foreground_ink_fraction": self.foreground_ink_fraction,
            },
            "similarity_metric": self.similarity_metric,
            "similarity_score": self.similarity_score,
            "similarity_numerator": self.similarity_numerator,
            "similarity_denominator": self.similarity_denominator,
        }


@dataclass(frozen=True, slots=True)
class WatermarkPreOcrClassification:
    """Typed v2 provenance for one bounded classifier-first observation."""

    schema_version: str
    classification: str
    classification_phase: str
    full_resolution_ocr_attempted: bool
    content_sha256: str
    page_number: int
    signed_exemplar_policy_id: str
    signed_exemplar_policy_sha256: str
    runtime_candidate_policy_id: str
    runtime_candidate_policy_sha256: str
    exemplar_reference: WatermarkPageExemplarReference
    transform_chain: tuple[str, ...]
    actual_width_pixels: int
    actual_height_pixels: int
    actual_mode: str
    near_white_fraction: str
    foreground_ink_fraction: str
    similarity_metric: str
    similarity_score: str
    similarity_numerator: int
    similarity_denominator: int
    canonical_render_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the exact OCR-claim-free durable v2 representation."""

        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "classification_phase": self.classification_phase,
            "full_resolution_ocr_attempted": self.full_resolution_ocr_attempted,
            "content_sha256": self.content_sha256,
            "page_number": self.page_number,
            "signed_exemplar_policy_id": self.signed_exemplar_policy_id,
            "signed_exemplar_policy_sha256": self.signed_exemplar_policy_sha256,
            "runtime_candidate_policy_id": self.runtime_candidate_policy_id,
            "runtime_candidate_policy_sha256": self.runtime_candidate_policy_sha256,
            "exemplar_reference": self.exemplar_reference.to_dict(),
            "transform_chain": list(self.transform_chain),
            "actual_width_pixels": self.actual_width_pixels,
            "actual_height_pixels": self.actual_height_pixels,
            "actual_mode": self.actual_mode,
            "confidence_distribution": {
                "near_white_fraction": self.near_white_fraction,
                "foreground_ink_fraction": self.foreground_ink_fraction,
            },
            "transform_bounds": dict(_RUNTIME_TRANSFORM_BOUNDS),
            "similarity_metric": self.similarity_metric,
            "similarity_score": self.similarity_score,
            "similarity_numerator": self.similarity_numerator,
            "similarity_denominator": self.similarity_denominator,
            "canonical_render_sha256": self.canonical_render_sha256,
        }
        if set(payload) != WATERMARK_PAGE_CLASSIFICATION_V2_FIELDS:  # pragma: no cover
            raise AssertionError("watermark-page-classification-v2 field set drifted")
        return payload


_DEFAULT_POLICY = WatermarkPagePolicy()
_DEFAULT_RUNTIME_CANDIDATE_RENDER_POLICY = WatermarkRuntimeCandidateRenderPolicy()


def default_watermark_page_policy() -> WatermarkPagePolicy:
    """Return the immutable production policy."""

    return _DEFAULT_POLICY


def default_watermark_runtime_candidate_render_policy() -> WatermarkRuntimeCandidateRenderPolicy:
    """Return the immutable, code-pinned 512 x 512 Poppler contract."""

    return _DEFAULT_RUNTIME_CANDIDATE_RENDER_POLICY


def build_watermark_page_canonical_exemplar_bitmap(
    *,
    page_image: Image.Image,
    render_dpi: int,
    policy: WatermarkPagePolicy,
) -> bytes:
    """Construct signed-manifest bitmap material from a real rendered page.

    Governance tooling and tests share this exact transform boundary so a valid
    exemplar cannot be manufactured from hand-assembled canonical pixels.
    """

    _validate_policy(policy, render_dpi=render_dpi)
    raster = _prepare_page(page_image, policy)
    if not _has_faint_signature(raster, policy):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_EXEMPLAR_INVALID",
            "full-resolution exemplar does not satisfy the pinned faint-raster bounds",
        )
    return _pack_canonical_bitmap(raster.canonical_bitmap, policy)


def foreground_dice_meets_threshold(
    *,
    intersection_count: int,
    candidate_foreground_count: int,
    exemplar_foreground_count: int,
    policy: WatermarkPagePolicy,
) -> bool:
    """Compare foreground Dice to the policy threshold using integers only."""

    if (
        type(intersection_count) is not int
        or type(candidate_foreground_count) is not int
        or type(exemplar_foreground_count) is not int
        or intersection_count < 0
        or candidate_foreground_count < 0
        or exemplar_foreground_count < 0
        or intersection_count > candidate_foreground_count
        or intersection_count > exemplar_foreground_count
    ):
        raise ValueError("foreground counts are inconsistent")
    denominator = candidate_foreground_count + exemplar_foreground_count
    if denominator == 0:
        return False
    dice_numerator = 2 * intersection_count
    return (
        dice_numerator * policy.similarity_minimum_denominator
        >= denominator * policy.similarity_minimum_numerator
    )


def classify_watermark_page(
    *,
    page_image: Image.Image,
    render_dpi: int,
    page_number: int,
    content_sha256: str,
    exemplar_image: Image.Image,
    exemplar_reference: WatermarkPageExemplarReference,
    eligibility: WatermarkPageEligibility,
    policy: WatermarkPagePolicy,
) -> WatermarkPageClassification:
    """Classify a source-page raster against a full-resolution signed exemplar.

    This entry point is for exemplar acquisition/prescan, where both images are
    genuine page renders.  Runtime parser composition uses the already signed
    canonical exemplar through :func:`classify_runtime_watermark_page` instead.
    """

    _validate_policy(policy, render_dpi=render_dpi)
    _validate_eligibility(eligibility)
    _validate_page_identity(page_number=page_number, content_sha256=content_sha256)

    exemplar_raster = _prepare_page(exemplar_image, policy)
    exemplar = _validate_full_resolution_exemplar(
        reference=exemplar_reference,
        raster=exemplar_raster,
        policy=policy,
    )
    candidate = _prepare_page(page_image, policy)
    if not _has_faint_signature(candidate, policy):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_CLASSIFICATION_BELOW_THRESHOLD",
            "candidate does not satisfy the pinned faint-raster bounds",
        )
    if not _has_faint_signature(exemplar_raster, policy):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_EXEMPLAR_INVALID",
            "signed full-resolution exemplar does not satisfy the pinned faint-raster bounds",
        )
    return _classify_prepared_candidate(
        candidate=candidate,
        exemplar=exemplar,
        content_sha256=content_sha256,
        page_number=page_number,
        policy=policy,
    )


def classify_runtime_watermark_page(
    *,
    page_image: Image.Image,
    render_dpi: int,
    page_number: int,
    content_sha256: str,
    eligibility: WatermarkPageEligibility,
    ocr_terminal_proof_sha256: str,
    runtime_config: WatermarkPageRuntimeConfig,
) -> dict[str, object]:
    """Return the exact parser-to-ingestion watermark provenance payload.

    This boundary consumes only a previously verified typed configuration.  It
    performs no environment, manifest, filesystem, or global-state lookup.
    """

    if not isinstance(runtime_config, WatermarkPageRuntimeConfig) or not _is_sha256(
        ocr_terminal_proof_sha256
    ):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_POLICY_INVALID", "runtime page proof is invalid"
        )
    _validate_policy(runtime_config.policy, render_dpi=render_dpi)
    _validate_eligibility(eligibility)
    _validate_page_identity(page_number=page_number, content_sha256=content_sha256)
    prepared = _prepare_page(page_image, runtime_config.policy)
    classification = _classify_prepared_candidate(
        candidate=prepared,
        exemplar=runtime_config._validated_exemplar,
        content_sha256=content_sha256,
        page_number=page_number,
        policy=runtime_config.policy,
    )
    return {
        "schema_version": classification.schema_version,
        "classification": classification.classification,
        "content_sha256": classification.content_sha256,
        "page_number": classification.page_number,
        "policy_id": classification.policy_id,
        "policy_sha256": classification.policy_sha256,
        "exemplar_reference": classification.exemplar_reference.to_dict(),
        "transform_chain": list(_RUNTIME_TRANSFORM_CHAIN),
        "confidence_distribution": {
            "near_white_fraction": classification.near_white_fraction,
            "foreground_ink_fraction": classification.foreground_ink_fraction,
        },
        "transform_bounds": dict(_RUNTIME_TRANSFORM_BOUNDS),
        "similarity_metric": classification.similarity_metric,
        "similarity_score": classification.similarity_score,
        "similarity_numerator": classification.similarity_numerator,
        "similarity_denominator": classification.similarity_denominator,
        "canonical_render_sha256": _canonical_bitmap_sha256(
            prepared.canonical_bitmap,
            runtime_config.policy,
        ),
        "ocr_terminal_proof_sha256": ocr_terminal_proof_sha256,
    }


def classify_pre_ocr_runtime_watermark_candidate(
    *,
    candidate_image: Image.Image,
    page_number: int,
    content_sha256: str,
    native_text_character_count: int,
    runtime_config: WatermarkPageRuntimeConfig,
) -> WatermarkPreOcrClassification:
    """Classify one exact bounded Poppler candidate before any OCR attempt.

    The caller cannot supply primary/rescue execution facts at this boundary.
    Consequently the v2 result contains no OCR terminal-proof hash, candidate
    counts, or rescue-outcome claims.  Only zero native text is required.
    """

    if not isinstance(runtime_config, WatermarkPageRuntimeConfig):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_POLICY_INVALID", "runtime classifier configuration is invalid"
        )
    if type(native_text_character_count) is not int or native_text_character_count != 0:
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_NOT_ELIGIBLE",
            "native text does not admit pre-OCR watermark classification",
        )
    _validate_page_identity(page_number=page_number, content_sha256=content_sha256)
    candidate_policy = _DEFAULT_RUNTIME_CANDIDATE_RENDER_POLICY
    _validate_runtime_candidate_image(candidate_image, candidate_policy)
    prepared = _prepare_page(candidate_image, runtime_config.policy)
    classification = _classify_prepared_candidate(
        candidate=prepared,
        exemplar=runtime_config._validated_exemplar,
        content_sha256=content_sha256,
        page_number=page_number,
        policy=runtime_config.policy,
    )
    return WatermarkPreOcrClassification(
        schema_version=WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION,
        classification=classification.classification,
        classification_phase="pre_ocr",
        full_resolution_ocr_attempted=False,
        content_sha256=classification.content_sha256,
        page_number=classification.page_number,
        signed_exemplar_policy_id=runtime_config.policy.policy_id,
        signed_exemplar_policy_sha256=runtime_config.policy.policy_sha256,
        runtime_candidate_policy_id=candidate_policy.policy_id,
        runtime_candidate_policy_sha256=candidate_policy.policy_sha256,
        exemplar_reference=classification.exemplar_reference,
        transform_chain=candidate_policy.transform_chain,
        actual_width_pixels=candidate_image.width,
        actual_height_pixels=candidate_image.height,
        actual_mode=candidate_image.mode,
        near_white_fraction=classification.near_white_fraction,
        foreground_ink_fraction=classification.foreground_ink_fraction,
        similarity_metric=classification.similarity_metric,
        similarity_score=classification.similarity_score,
        similarity_numerator=classification.similarity_numerator,
        similarity_denominator=classification.similarity_denominator,
        canonical_render_sha256=_canonical_bitmap_sha256(
            prepared.canonical_bitmap,
            runtime_config.policy,
        ),
    )


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_runtime_candidate_image(
    image: Image.Image,
    policy: WatermarkRuntimeCandidateRenderPolicy,
) -> None:
    if (
        not isinstance(policy, WatermarkRuntimeCandidateRenderPolicy)
        or policy.to_dict() != _DEFAULT_RUNTIME_CANDIDATE_RENDER_POLICY.to_dict()
        or policy.policy_sha256 != _DEFAULT_RUNTIME_CANDIDATE_RENDER_POLICY.policy_sha256
        or not isinstance(image, Image.Image)
        or image.width != policy.size_width_pixels
        or image.height != policy.size_height_pixels
        or str(image.format or "").lower() != policy.output_format
        or image.mode not in {"L", "RGB"}
    ):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_POLICY_INVALID",
            "runtime candidate does not satisfy the pinned render contract",
        )
    if image.mode == "L":
        return
    with image.convert("RGB") as rgb:
        red, green, blue = rgb.split()
        try:
            with (
                ImageChops.difference(red, green) as red_green,
                ImageChops.difference(red, blue) as red_blue,
            ):
                is_grayscale = red_green.getbbox() is None and red_blue.getbbox() is None
        finally:
            red.close()
            green.close()
            blue.close()
    if not is_grayscale:
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_POLICY_INVALID",
            "runtime candidate does not satisfy the pinned render contract",
        )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and set(value) <= _HEX_DIGITS
    )


def _validate_policy(policy: WatermarkPagePolicy, *, render_dpi: int) -> None:
    if (
        not isinstance(policy, WatermarkPagePolicy)
        or policy.to_dict() != _DEFAULT_POLICY.to_dict()
        or policy.policy_sha256 != _DEFAULT_POLICY.policy_sha256
        or type(render_dpi) is not int
        or render_dpi != policy.render_dpi
    ):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_POLICY_INVALID", "classifier policy or render DPI drifted"
        )


def _validate_eligibility(eligibility: WatermarkPageEligibility) -> None:
    if (
        not isinstance(eligibility, WatermarkPageEligibility)
        or type(eligibility.native_text_character_count) is not int
        or type(eligibility.primary_ocr_token_count) is not int
        or type(eligibility.rescue_candidate_count) is not int
        or type(eligibility.rescue_completed_candidate_count) is not int
        or eligibility.native_text_character_count != 0
        or eligibility.primary_ocr_token_count != 0
        or eligibility.rescue_candidate_count != 24
        or eligibility.rescue_completed_candidate_count != 24
        or eligibility.rescue_outcome != "no_consensus"
    ):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_NOT_ELIGIBLE",
            "native, primary OCR, and complete rescue facts do not admit classification",
        )


def _validate_page_identity(*, page_number: int, content_sha256: str) -> None:
    if type(page_number) is not int or page_number < 1 or not _is_sha256(content_sha256):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_POLICY_INVALID", "page identity is invalid"
        )


def _validate_exemplar_reference(
    reference: WatermarkPageExemplarReference,
    policy: WatermarkPagePolicy,
) -> None:
    if (
        not isinstance(reference, WatermarkPageExemplarReference)
        or not reference.exemplar_id
        or not _is_sha256(reference.content_sha256)
        or type(reference.page_number) is not int
        or reference.page_number < 1
        or not _is_sha256(reference.attestation_envelope_sha256)
        or not _is_sha256(reference.canonical_bitmap_sha256)
        or not _is_sha256(reference.classifier_policy_sha256)
    ):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_EXEMPLAR_INVALID", "signed exemplar reference is invalid"
        )
    if reference.classifier_policy_sha256 != policy.policy_sha256:
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_POLICY_INVALID",
            "signed exemplar is bound to a different classifier policy",
        )


def _validate_canonical_pixels(
    bitmap: bytes,
    policy: WatermarkPagePolicy,
) -> int:
    expected_pixel_count = policy.canonical_width * policy.canonical_height
    if (
        not isinstance(bitmap, bytes)
        or len(bitmap) != expected_pixel_count
        or any(pixel not in {0, 255} for pixel in bitmap)
    ):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_EXEMPLAR_INVALID", "canonical bitmap is invalid"
        )
    foreground_count = bitmap.count(0)
    if foreground_count == 0 or foreground_count == expected_pixel_count:
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_EXEMPLAR_INVALID",
            "canonical bitmap must contain foreground and background",
        )
    return foreground_count


def _validate_signed_canonical_exemplar(
    *,
    reference: WatermarkPageExemplarReference,
    packed_bitmap: bytes,
    policy: WatermarkPagePolicy,
) -> _ValidatedCanonicalExemplar:
    """Validate only signed canonical-domain invariants.

    Full-resolution near-white and ink fractions are intentionally absent: the
    packed manifest artifact is already the output of that source-raster stage.
    """

    _validate_exemplar_reference(reference, policy)
    expected_pixel_count = policy.canonical_width * policy.canonical_height
    if (
        expected_pixel_count <= 0
        or expected_pixel_count % 8 != 0
        or not isinstance(packed_bitmap, bytes)
        or len(packed_bitmap) != expected_pixel_count // 8
    ):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_EXEMPLAR_INVALID", "packed exemplar bitmap is invalid"
        )
    packed_sha256 = hashlib.sha256(packed_bitmap).hexdigest()
    if packed_sha256 != reference.canonical_bitmap_sha256:
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_EXEMPLAR_INVALID",
            "signed exemplar canonical bitmap does not match",
        )
    canonical_bitmap = _unpack_canonical_pixels(packed_bitmap, policy)
    foreground_count = _validate_canonical_pixels(canonical_bitmap, policy)
    return _ValidatedCanonicalExemplar(
        reference=reference,
        canonical_bitmap=canonical_bitmap,
        canonical_foreground_count=foreground_count,
        packed_bitmap_sha256=packed_sha256,
    )


def _validate_full_resolution_exemplar(
    *,
    reference: WatermarkPageExemplarReference,
    raster: _PreparedFullResolutionRaster,
    policy: WatermarkPagePolicy,
) -> _ValidatedCanonicalExemplar:
    """Construct canonical exemplar material from a genuine rendered page."""

    _validate_exemplar_reference(reference, policy)
    packed_sha256 = _canonical_bitmap_sha256(raster.canonical_bitmap, policy)
    if reference.canonical_bitmap_sha256 != packed_sha256:
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_EXEMPLAR_INVALID",
            "signed exemplar canonical bitmap does not match",
        )
    foreground_count = _validate_canonical_pixels(raster.canonical_bitmap, policy)
    return _ValidatedCanonicalExemplar(
        reference=reference,
        canonical_bitmap=raster.canonical_bitmap,
        canonical_foreground_count=foreground_count,
        packed_bitmap_sha256=packed_sha256,
    )


def _classify_prepared_candidate(
    *,
    candidate: _PreparedFullResolutionRaster,
    exemplar: _ValidatedCanonicalExemplar,
    content_sha256: str,
    page_number: int,
    policy: WatermarkPagePolicy,
) -> WatermarkPageClassification:
    if not _has_faint_signature(candidate, policy):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_CLASSIFICATION_BELOW_THRESHOLD",
            "candidate does not satisfy the pinned faint-raster bounds",
        )

    intersection_count = sum(
        1
        for candidate_pixel, exemplar_pixel in zip(
            candidate.canonical_bitmap,
            exemplar.canonical_bitmap,
            strict=True,
        )
        if candidate_pixel == 0 and exemplar_pixel == 0
    )
    similarity_numerator = 2 * intersection_count
    similarity_denominator = (
        candidate.canonical_foreground_count + exemplar.canonical_foreground_count
    )
    if not foreground_dice_meets_threshold(
        intersection_count=intersection_count,
        candidate_foreground_count=candidate.canonical_foreground_count,
        exemplar_foreground_count=exemplar.canonical_foreground_count,
        policy=policy,
    ):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_CLASSIFICATION_BELOW_THRESHOLD",
            "candidate is below the pinned structural-similarity threshold",
        )

    return WatermarkPageClassification(
        schema_version=WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION,
        classification="watermark_only",
        policy_id=policy.policy_id,
        policy_sha256=policy.policy_sha256,
        content_sha256=content_sha256,
        page_number=page_number,
        exemplar_reference=exemplar.reference,
        transform_chain=policy.transform_chain,
        near_white_fraction=_persist_fraction(
            candidate.near_white_count, candidate.source_pixel_count
        ),
        foreground_ink_fraction=_persist_fraction(
            candidate.foreground_ink_count, candidate.source_pixel_count
        ),
        similarity_metric=policy.similarity_metric,
        similarity_score=_persist_fraction(similarity_numerator, similarity_denominator),
        similarity_numerator=similarity_numerator,
        similarity_denominator=similarity_denominator,
    )


def _prepare_page(
    image: Image.Image,
    policy: WatermarkPagePolicy,
) -> _PreparedFullResolutionRaster:
    """Prepare a rendered source-page raster; canonical exemplars never enter here."""

    if not isinstance(image, Image.Image) or image.width < 1 or image.height < 1:
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_POLICY_INVALID", "page image is invalid"
        )
    with (
        image.convert(policy.grayscale_mode) as grayscale,
        ImageOps.autocontrast(grayscale, cutoff=policy.autocontrast_cutoff) as contrasted,
        contrasted.resize(
            (policy.canonical_width, policy.canonical_height),
            Image.Resampling.LANCZOS,
        ) as resized,
        resized.point(
            lambda pixel: 0 if pixel < policy.foreground_pixel_maximum_exclusive else 255
        ) as binary,
    ):
        grayscale_histogram = grayscale.histogram()
        contrasted_histogram = contrasted.histogram()
        source_pixel_count = grayscale.width * grayscale.height
        near_white_count = sum(grayscale_histogram[policy.near_white_pixel_minimum :])
        foreground_ink_count = sum(
            contrasted_histogram[: policy.foreground_pixel_maximum_exclusive]
        )
        canonical_bitmap = binary.tobytes()

    return _PreparedFullResolutionRaster(
        near_white_count=near_white_count,
        foreground_ink_count=foreground_ink_count,
        source_pixel_count=source_pixel_count,
        canonical_bitmap=canonical_bitmap,
        canonical_foreground_count=canonical_bitmap.count(0),
    )


def _has_faint_signature(
    page: _PreparedFullResolutionRaster,
    policy: WatermarkPagePolicy,
) -> bool:
    return _fraction_inclusive(
        page.near_white_count,
        page.source_pixel_count,
        minimum=policy.near_white_fraction_minimum,
        maximum=policy.near_white_fraction_maximum,
    ) and _fraction_inclusive(
        page.foreground_ink_count,
        page.source_pixel_count,
        minimum=policy.foreground_ink_fraction_minimum,
        maximum=policy.foreground_ink_fraction_maximum,
    )


def _fraction_inclusive(
    numerator: int,
    denominator: int,
    *,
    minimum: Decimal,
    maximum: Decimal,
) -> bool:
    minimum_scaled = int(minimum * _FRACTION_SCALE)
    maximum_scaled = int(maximum * _FRACTION_SCALE)
    scaled_numerator = numerator * _FRACTION_SCALE
    return denominator * minimum_scaled <= scaled_numerator <= denominator * maximum_scaled


def _persist_fraction(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_POLICY_INVALID", "fraction denominator is invalid"
        )
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        _PERSISTENCE_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )
    return format(value, ".6f")


def _pack_canonical_bitmap(bitmap: bytes, policy: WatermarkPagePolicy) -> bytes:
    _validate_canonical_pixels(bitmap, policy)
    packed = bytearray()
    for offset in range(0, len(bitmap), 8):
        value = 0
        for pixel in bitmap[offset : offset + 8]:
            value = (value << 1) | (1 if pixel == 255 else 0)
        packed.append(value)
    return bytes(packed)


def _canonical_bitmap_sha256(bitmap: bytes, policy: WatermarkPagePolicy) -> str:
    return hashlib.sha256(_pack_canonical_bitmap(bitmap, policy)).hexdigest()


def _unpack_canonical_pixels(
    packed: bytes,
    policy: WatermarkPagePolicy,
) -> bytes:
    expected_pixel_count = policy.canonical_width * policy.canonical_height
    if (
        not isinstance(packed, bytes)
        or expected_pixel_count <= 0
        or expected_pixel_count % 8 != 0
        or len(packed) != expected_pixel_count // 8
    ):
        raise WatermarkPageClassificationError(
            "WATERMARK_PAGE_EXEMPLAR_INVALID", "packed exemplar bitmap is invalid"
        )
    pixels = bytearray()
    for value in packed:
        pixels.extend(255 if value & (1 << shift) else 0 for shift in range(7, -1, -1))
    return bytes(pixels)


__all__ = [
    "WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION",
    "WATERMARK_PAGE_CLASSIFICATION_V2_FIELDS",
    "WATERMARK_PAGE_POLICY_ID",
    "WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION",
    "WATERMARK_RUNTIME_CANDIDATE_RENDER_POLICY_ID",
    "WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN",
    "WatermarkPageClassification",
    "WatermarkPageClassificationError",
    "WatermarkPageEligibility",
    "WatermarkPageExemplarReference",
    "WatermarkPagePolicy",
    "WatermarkPageRuntimeConfig",
    "WatermarkPreOcrClassification",
    "WatermarkRuntimeCandidateRenderPolicy",
    "build_watermark_page_canonical_exemplar_bitmap",
    "classify_pre_ocr_runtime_watermark_candidate",
    "classify_watermark_page",
    "classify_runtime_watermark_page",
    "default_watermark_page_policy",
    "default_watermark_runtime_candidate_render_policy",
    "foreground_dice_meets_threshold",
]
