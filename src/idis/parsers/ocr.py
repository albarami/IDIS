"""Config-gated OCR adapter boundary for parser tests and future provisioning."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import multiprocessing as mp
import os
import platform
import re
import statistics
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from dataclasses import asdict, dataclass, fields, replace
from decimal import ROUND_HALF_EVEN, Decimal
from functools import partial
from itertools import combinations
from pathlib import Path
from queue import Empty
from typing import Any, Protocol

from idis.parsers.poppler_linux_runtime import (
    PinnedLinuxPopplerRuntime,
    install_poppler_child_environment,
    mark_governed_worker_child,
)
from idis.parsers.poppler_runtime import (
    GovernedPopplerRuntime,
    PinnedPopplerRuntime,
    PopplerRuntimeBindingError,
    resolve_pinned_poppler_runtime,
    verify_governed_poppler_runtime,
    verify_pinned_poppler_runtime,
)

MAX_OCR_IMAGE_PIXELS = 20_000_000
OCR_DPI_LADDER = (200, 150, 120, 96, 72)
OCR_DPI_LADDER_VERSION = "ocr-dpi-ladder-v1"
OCR_RENDER_ADMISSION_SCHEMA_VERSION = "ocr-render-admission-v2"
LEGACY_OCR_RENDER_ADMISSION_SCHEMA_VERSION = "ocr-render-admission-v1"
PINNED_POPPLER_VERSION = "25.12.0"
POPPLER_EXPECTED_DIMENSION_POLICY_ID = "poppler-pdftoppm-25.12.0-dimensions-v1"
POPPLER_EXACT_COMPARISON_MODE = "exact-poppler-25.12.0"
_OCR_CLEANUP_RESERVE_MAX_SECONDS = 0.25
_OCR_PROCESS_REAP_SECONDS = 0.20
_LOG = logging.getLogger(__name__)
_PDF_OCR_RENDER_BATCH_SIZE = 8
_PDF_OCR_PAGE_WORKERS = 4
OCR_RESCUE_POLICY_VERSION = "pdf-ocr-rescue-v1"
OCR_RESCUE_ROTATIONS = (0, 90, 180, 270)
OCR_RESCUE_TRANSFORMS = ("autocontrast-c0", "binary-192", "binary-224")
OCR_RESCUE_PSMS = (6, 11)
OCR_RESCUE_MATRIX = tuple(
    (rotation, transform, psm)
    for rotation in OCR_RESCUE_ROTATIONS
    for transform in OCR_RESCUE_TRANSFORMS
    for psm in OCR_RESCUE_PSMS
)
_OCR_RESCUE_MAX_NORMALIZED_CODEPOINTS = 65_536
_OCR_RESCUE_MAX_TOKENS = 4_096
_OCR_PAGE_EXECUTION_MAX_CODEPOINTS = 100_000_000
_OCR_PAGE_EXECUTION_MAX_TOKENS = 1_000_000
_OCR_RESCUE_MIN_TOKENS = 4
_OCR_RESCUE_MIN_ALPHANUMERIC_CODEPOINTS = 16
_OCR_RESCUE_MIN_MEAN_CONFIDENCE = 0.60
_OCR_RESCUE_MIN_MEDIAN_CONFIDENCE = 0.55
_OCR_RESCUE_HIGH_CONFIDENCE_THRESHOLD = 0.50
_OCR_RESCUE_MIN_HIGH_CONFIDENCE_FRACTION = 0.75
_OCR_RESCUE_MIN_AGREEMENT = 0.80
_OCR_RESCUE_AGREEMENT_QUANTUM = Decimal("0.000001")
_OCR_RESCUE_HISTOGRAM_KEYS = (
    "below_0_50",
    "from_0_50_to_below_0_60",
    "from_0_60_to_below_0_80",
    "at_or_above_0_80",
)
OCR_PAGE_EXECUTION_RECEIPT_SCHEMA_VERSION = "ocr-page-execution-receipt-v1"
OCR_NO_CONSENSUS_FILE_PROOF_SCHEMA_VERSION = "ocr-no-consensus-file-proof-v1"
_EMPTY_NORMALIZED_OCR_TEXT_SHA256 = hashlib.sha256(b"").hexdigest()


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


OCR_RESCUE_POLICY_SHA256 = _canonical_json_sha256(
    {
        "agreement": {
            "comparison": "unrounded",
            "minimum": _OCR_RESCUE_MIN_AGREEMENT,
            "persisted_decimal_places": 6,
            "score": "min-unigram-bigram-multiset-dice",
        },
        "candidate_bounds": {
            "max_normalized_codepoints": _OCR_RESCUE_MAX_NORMALIZED_CODEPOINTS,
            "max_tokens": _OCR_RESCUE_MAX_TOKENS,
            "min_alphanumeric_codepoints": _OCR_RESCUE_MIN_ALPHANUMERIC_CODEPOINTS,
            "min_high_confidence_fraction": _OCR_RESCUE_MIN_HIGH_CONFIDENCE_FRACTION,
            "min_mean_confidence": _OCR_RESCUE_MIN_MEAN_CONFIDENCE,
            "min_median_confidence": _OCR_RESCUE_MIN_MEDIAN_CONFIDENCE,
            "min_tokens": _OCR_RESCUE_MIN_TOKENS,
        },
        "candidate_order": [
            {"psm": psm, "rotation_degrees": rotation, "transform_id": transform}
            for rotation, transform, psm in OCR_RESCUE_MATRIX
        ],
        "normalization": "nfkc-casefold-unicode-letter-number-runs-v1",
        "policy_version": OCR_RESCUE_POLICY_VERSION,
        "tesseract_oem": 1,
    }
)

POPPLER_EXPECTED_DIMENSION_POLICY_SHA256 = _canonical_json_sha256(
    {
        "backend": "splash",
        "numeric_parser": "poppler-lexer-decimal-digit-accumulation-binary64-v1",
        "page_box": "MediaBox",
        "poppler_version": PINNED_POPPLER_VERSION,
        "renderer": "pdftoppm",
        "rotation": "swap-axes-for-90-or-270",
        "rounding": "ceil",
        "scale_order": "extent-times-dpi-then-divide-72-binary64",
        "schema_version": "poppler-expected-dimensions-policy-v1",
        "user_unit_handling": "ignored-by-pinned-renderer",
    }
)
if POPPLER_EXPECTED_DIMENSION_POLICY_SHA256 != (
    "483ee7542fbcb6a69dc3949072ef2897471130e409cc41318395e72fec3e0253"
):  # pragma: no cover - import-time guard against policy drift
    raise RuntimeError("Poppler expected-dimension policy hash drifted")


@dataclass(frozen=True, slots=True)
class OcrPageExecutionReceipt:
    """Secret-safe proof of one complete page OCR execution."""

    schema_version: str
    page_number: int
    outcome: str
    primary_completed: bool
    primary_token_count: int
    rescue_attempted_candidate_count: int
    rescue_completed_candidate_count: int
    accepted_text: bool
    accepted_text_token_count: int
    accepted_text_span_count: int
    accepted_text_codepoint_count: int
    normalized_page_text_sha256: str
    candidate_receipt_sha256: str | None

    def to_payload(self) -> dict[str, object]:
        """Return the exact process-safe representation without OCR text."""

        return {
            "schema_version": self.schema_version,
            "page_number": self.page_number,
            "outcome": self.outcome,
            "primary_completed": self.primary_completed,
            "primary_token_count": self.primary_token_count,
            "rescue_attempted_candidate_count": self.rescue_attempted_candidate_count,
            "rescue_completed_candidate_count": self.rescue_completed_candidate_count,
            "accepted_text": self.accepted_text,
            "accepted_text_token_count": self.accepted_text_token_count,
            "accepted_text_span_count": self.accepted_text_span_count,
            "accepted_text_codepoint_count": self.accepted_text_codepoint_count,
            "normalized_page_text_sha256": self.normalized_page_text_sha256,
            "candidate_receipt_sha256": self.candidate_receipt_sha256,
        }


@dataclass(frozen=True, slots=True)
class OcrNoConsensusFileProof:
    """Validated, text-free execution proof for a file with a failed rescue page."""

    schema_version: str
    ocr_rescue_policy_version: str
    ocr_rescue_policy_sha256: str
    page_receipts: tuple[OcrPageExecutionReceipt, ...]
    ocr_result_sha256: str

    def to_payload(self) -> dict[str, object]:
        """Return the exact process-safe proof representation."""

        return {
            "schema_version": self.schema_version,
            "ocr_rescue_policy_version": self.ocr_rescue_policy_version,
            "ocr_rescue_policy_sha256": self.ocr_rescue_policy_sha256,
            "page_receipts": [receipt.to_payload() for receipt in self.page_receipts],
            "ocr_result_sha256": self.ocr_result_sha256,
        }


@dataclass(frozen=True, slots=True)
class OcrPageRenderAdmission:
    """Immutable OCR render plan, upgraded with observed dimensions after rendering."""

    page_number: int
    schema_version: str
    ladder_version: str
    configured_dpi: int
    chosen_dpi: int
    projected_width: int
    projected_height: int
    projected_pixel_count: int
    max_pixel_count: int
    expected_render_width: int | None = None
    expected_render_height: int | None = None
    expected_render_pixel_count: int | None = None
    poppler_version: str | None = None
    poppler_runtime_identity_sha256: str | None = None
    expected_dimension_policy_id: str | None = None
    expected_dimension_policy_sha256: str | None = None
    comparison_mode: str | None = None
    actual_render_width: int | None = None
    actual_render_height: int | None = None
    actual_render_pixel_count: int | None = None
    width_delta_pixels: int | None = None
    height_delta_pixels: int | None = None
    tolerance_pixels: int | None = None
    tolerance_used: bool | None = None

    def __post_init__(self) -> None:
        common_integer_values = (
            self.page_number,
            self.configured_dpi,
            self.chosen_dpi,
            self.projected_width,
            self.projected_height,
            self.projected_pixel_count,
            self.max_pixel_count,
        )
        if (
            any(type(value) is not int for value in common_integer_values)
            or self.page_number < 1
            or self.ladder_version != OCR_DPI_LADDER_VERSION
            or self.configured_dpi != OCR_DPI_LADDER[0]
            or self.chosen_dpi not in OCR_DPI_LADDER
            or self.chosen_dpi > self.configured_dpi
            or self.projected_width < 1
            or self.projected_height < 1
            or self.projected_pixel_count != self.projected_width * self.projected_height
            or self.max_pixel_count != MAX_OCR_IMAGE_PIXELS
            or self.projected_pixel_count > self.max_pixel_count
        ):
            raise OcrError("OCR render admission is malformed")

        if self.schema_version == LEGACY_OCR_RENDER_ADMISSION_SCHEMA_VERSION:
            if any(
                value is not None
                for value in (
                    self.expected_render_width,
                    self.expected_render_height,
                    self.expected_render_pixel_count,
                    self.poppler_version,
                    self.poppler_runtime_identity_sha256,
                    self.expected_dimension_policy_id,
                    self.expected_dimension_policy_sha256,
                    self.comparison_mode,
                    self.actual_render_width,
                    self.actual_render_height,
                    self.actual_render_pixel_count,
                    self.width_delta_pixels,
                    self.height_delta_pixels,
                    self.tolerance_pixels,
                    self.tolerance_used,
                )
            ):
                raise OcrError("OCR render admission is malformed")
            return

        expected_integer_values = (
            self.expected_render_width,
            self.expected_render_height,
            self.expected_render_pixel_count,
            self.tolerance_pixels,
        )
        if (
            self.schema_version != OCR_RENDER_ADMISSION_SCHEMA_VERSION
            or any(type(value) is not int for value in expected_integer_values)
            or int(self.expected_render_width or 0) < 1
            or int(self.expected_render_height or 0) < 1
            or self.expected_render_pixel_count
            != int(self.expected_render_width or 0) * int(self.expected_render_height or 0)
            or self.poppler_version != PINNED_POPPLER_VERSION
            or not _is_lower_sha256(self.poppler_runtime_identity_sha256)
            or self.expected_dimension_policy_id != POPPLER_EXPECTED_DIMENSION_POLICY_ID
            or self.expected_dimension_policy_sha256 != POPPLER_EXPECTED_DIMENSION_POLICY_SHA256
            or self.comparison_mode != POPPLER_EXACT_COMPARISON_MODE
            or self.tolerance_pixels != 0
            or self.tolerance_used is not False
        ):
            raise OcrError("OCR render admission is malformed")

        actual_values = (
            self.actual_render_width,
            self.actual_render_height,
            self.actual_render_pixel_count,
            self.width_delta_pixels,
            self.height_delta_pixels,
        )
        if all(value is None for value in actual_values):
            return
        if (
            any(type(value) is not int for value in actual_values)
            or int(self.actual_render_width or 0) < 1
            or int(self.actual_render_height or 0) < 1
            or self.actual_render_pixel_count
            != int(self.actual_render_width or 0) * int(self.actual_render_height or 0)
            or self.actual_render_pixel_count > self.max_pixel_count
            or self.width_delta_pixels
            != int(self.actual_render_width or 0) - int(self.expected_render_width or 0)
            or self.height_delta_pixels
            != int(self.actual_render_height or 0) - int(self.expected_render_height or 0)
            or self.width_delta_pixels != 0
            or self.height_delta_pixels != 0
        ):
            raise OcrError("OCR render admission is malformed")

    @property
    def has_observed_render(self) -> bool:
        """Return whether this value contains renderer-observed dimensions."""

        return self.actual_render_width is not None

    def with_observed_render(self, *, width: int, height: int) -> OcrPageRenderAdmission:
        """Return the exact-mode receipt for one verified Poppler raster."""

        if self.schema_version != OCR_RENDER_ADMISSION_SCHEMA_VERSION:
            if width != self.projected_width or height != self.projected_height:
                raise OcrError("OCR renderer violated its page admission")
            return self
        if (
            type(width) is not int
            or type(height) is not int
            or width != self.expected_render_width
            or height != self.expected_render_height
            or width * height > self.max_pixel_count
        ):
            raise OcrError("OCR renderer violated its page admission")
        return replace(
            self,
            actual_render_width=width,
            actual_render_height=height,
            actual_render_pixel_count=width * height,
            width_delta_pixels=width - int(self.expected_render_width),
            height_delta_pixels=height - int(self.expected_render_height),
        )

    def same_render_plan(self, other: object) -> bool:
        """Compare immutable pre-render fields while excluding observed dimensions."""

        if not isinstance(other, OcrPageRenderAdmission):
            return False
        observed_fields = {
            "actual_render_width",
            "actual_render_height",
            "actual_render_pixel_count",
            "width_delta_pixels",
            "height_delta_pixels",
        }
        return all(
            getattr(self, field_name) == getattr(other, field_name)
            for field_name in (field.name for field in fields(self))
            if field_name not in observed_fields
        )

    def to_span_dict(self) -> dict[str, object]:
        """Return the durable value nested below ``ocr_render_admission``."""

        legacy = {
            "schema_version": self.schema_version,
            "ladder_version": self.ladder_version,
            "configured_dpi": self.configured_dpi,
            "chosen_dpi": self.chosen_dpi,
            "projected_pixel_count": self.projected_pixel_count,
            "max_pixel_count": self.max_pixel_count,
        }
        if self.schema_version == LEGACY_OCR_RENDER_ADMISSION_SCHEMA_VERSION:
            return legacy
        if not self.has_observed_render:
            raise OcrError("OCR render admission lacks observed dimensions")
        return {
            "schema_version": self.schema_version,
            "ladder_version": self.ladder_version,
            "configured_dpi": self.configured_dpi,
            "chosen_dpi": self.chosen_dpi,
            "projected_width": self.projected_width,
            "projected_height": self.projected_height,
            "projected_pixel_count": self.projected_pixel_count,
            "expected_render_width": self.expected_render_width,
            "expected_render_height": self.expected_render_height,
            "expected_render_pixel_count": self.expected_render_pixel_count,
            "actual_render_width": self.actual_render_width,
            "actual_render_height": self.actual_render_height,
            "actual_render_pixel_count": self.actual_render_pixel_count,
            "poppler_version": self.poppler_version,
            "poppler_runtime_identity_sha256": self.poppler_runtime_identity_sha256,
            "expected_dimension_policy_id": self.expected_dimension_policy_id,
            "expected_dimension_policy_sha256": self.expected_dimension_policy_sha256,
            "comparison_mode": self.comparison_mode,
            "width_delta_pixels": self.width_delta_pixels,
            "height_delta_pixels": self.height_delta_pixels,
            "tolerance_pixels": self.tolerance_pixels,
            "tolerance_used": self.tolerance_used,
            "max_pixel_count": self.max_pixel_count,
        }

    def to_metadata_dict(self) -> dict[str, object]:
        """Bind the stable six-key span representation to its PDF page."""

        return {"page": self.page_number, **self.to_span_dict()}

    def _to_worker_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "page_number": self.page_number,
            "schema_version": self.schema_version,
            "ladder_version": self.ladder_version,
            "configured_dpi": self.configured_dpi,
            "chosen_dpi": self.chosen_dpi,
            "projected_width": self.projected_width,
            "projected_height": self.projected_height,
            "projected_pixel_count": self.projected_pixel_count,
            "max_pixel_count": self.max_pixel_count,
        }
        if self.schema_version == OCR_RENDER_ADMISSION_SCHEMA_VERSION:
            payload.update(
                {
                    "expected_render_width": self.expected_render_width,
                    "expected_render_height": self.expected_render_height,
                    "expected_render_pixel_count": self.expected_render_pixel_count,
                    "poppler_version": self.poppler_version,
                    "poppler_runtime_identity_sha256": (self.poppler_runtime_identity_sha256),
                    "expected_dimension_policy_id": self.expected_dimension_policy_id,
                    "expected_dimension_policy_sha256": (self.expected_dimension_policy_sha256),
                    "comparison_mode": self.comparison_mode,
                    "actual_render_width": self.actual_render_width,
                    "actual_render_height": self.actual_render_height,
                    "actual_render_pixel_count": self.actual_render_pixel_count,
                    "width_delta_pixels": self.width_delta_pixels,
                    "height_delta_pixels": self.height_delta_pixels,
                    "tolerance_pixels": self.tolerance_pixels,
                    "tolerance_used": self.tolerance_used,
                }
            )
        return payload


class OcrError(Exception):
    """Base class for safe OCR adapter failures."""


class OcrTimeoutError(OcrError):
    """Raised by OCR adapters when OCR exceeds a configured runtime budget."""


class OcrUnavailableError(OcrError):
    """Raised when OCR dependencies or binaries are not available."""


class OcrRescueNoConsensusError(OcrError):
    """Raised when the complete OCR rescue matrix has no unique admitted clique."""

    def __init__(
        self,
        message: str,
        *,
        page_proof: OcrPageExecutionReceipt | None = None,
        file_proof: OcrNoConsensusFileProof | None = None,
        accepted_pages: tuple[OcrPageText, ...] = (),
        page_content_sha256s: tuple[tuple[int, str], ...] = (),
        render_admissions: tuple[OcrPageRenderAdmission, ...] = (),
    ) -> None:
        super().__init__(message)
        self.page_proof = page_proof
        self.file_proof = file_proof
        self.accepted_pages = accepted_pages
        self.page_content_sha256s = page_content_sha256s
        self.render_admissions = render_admissions


@dataclass(frozen=True, slots=True)
class OcrConfidenceHistogram:
    """Fixed, disjoint normalized-confidence histogram for one OCR candidate."""

    below_0_50: int
    from_0_50_to_below_0_60: int
    from_0_60_to_below_0_80: int
    at_or_above_0_80: int


@dataclass(frozen=True, slots=True)
class OcrRescueProvenance:
    """Immutable page-level provenance repeated by every rescued line span."""

    policy_version: str
    policy_sha256: str
    winner_rotation_degrees: int
    winner_transform_id: str
    winner_psm: int
    winner_word_count: int
    winner_mean_confidence: float
    winner_median_confidence: float
    winner_minimum_confidence: float
    winner_maximum_confidence: float
    winner_confidence_histogram_counts: OcrConfidenceHistogram
    winner_confidence_values_sha256: str
    winner_fraction_at_or_above_0_50: float
    attempted_candidate_count: int
    supporting_candidate_count: int
    minimum_clique_agreement: float
    mean_clique_agreement: float
    candidate_receipt_sha256: str
    normalized_page_text_sha256: str


@dataclass(frozen=True, slots=True)
class OcrRescueCandidate:
    """One completed, bounded candidate in the fixed OCR rescue matrix."""

    matrix_ordinal: int
    candidate_id: str
    rotation_degrees: int
    transform_id: str
    psm: int
    text: str
    normalized_tokens: tuple[str, ...]
    word_confidences: tuple[float, ...]
    quality_admitted: bool
    state: str
    rejection_reasons: tuple[str, ...]
    word_count: int
    mean_confidence: float
    median_confidence: float
    minimum_confidence: float
    maximum_confidence: float
    confidence_histogram_counts: OcrConfidenceHistogram
    confidence_values_sha256: str
    fraction_at_or_above_0_50: float
    normalized_text_sha256: str


@dataclass(frozen=True, slots=True)
class OcrRescueConsensus:
    """The sole qualifying consensus clique and its real representative."""

    winner: Any
    supporting_candidates: tuple[Any, ...]
    minimum_clique_agreement: float
    mean_clique_agreement: float


@dataclass(frozen=True, slots=True)
class OcrPageText:
    """OCR text extracted for one PDF page.

    ``confidence`` is the page/image mean OCR confidence normalized to 0-1, or None
    when no valid confidence is available (backward-compatible default).
    """

    page_number: int
    text: str
    confidence: float | None = None
    rescue_provenance: OcrRescueProvenance | None = None
    render_admission: OcrPageRenderAdmission | None = None


_TRUSTED_IMAGE_ONLY_PAGE_ADMISSION_FIELDS = frozenset(
    {
        "safe_alias",
        "content_sha256",
        "page_number",
        "page_content_sha256",
        "ocr_terminal_proof_sha256",
        "watermark_classifier_receipt_sha256",
        "reviewer_disposition",
    }
)
_TRUSTED_IMAGE_ONLY_SAFE_ALIAS_RE = re.compile(
    r"company-(?:001|002)-document-[0-9]{5}\.(?:docx|html|mp4|pdf|png|pptx|txt|xlsx)"
)


def _is_lower_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def canonical_image_only_page_content_sha256(image: Any) -> str:
    """Hash the pinned 128x128 row-major one-bit page bitmap (white is one)."""

    from PIL import Image, ImageOps

    if not isinstance(image, Image.Image) or image.width < 1 or image.height < 1:
        raise OcrError("OCR page image is malformed")
    with (
        image.convert("L") as grayscale,
        ImageOps.autocontrast(grayscale, cutoff=0) as contrasted,
        contrasted.resize((128, 128), Image.Resampling.LANCZOS) as resized,
        resized.point(lambda pixel: 0 if pixel < 224 else 255) as binary,
    ):
        bitmap = binary.tobytes()
    if len(bitmap) != 128 * 128 or any(pixel not in {0, 255} for pixel in bitmap):
        raise OcrError("OCR canonical page bitmap is malformed")
    packed = bytearray()
    for offset in range(0, len(bitmap), 8):
        value = 0
        for pixel in bitmap[offset : offset + 8]:
            value = (value << 1) | (1 if pixel == 255 else 0)
        packed.append(value)
    if len(packed) != 2048:
        raise OcrError("OCR canonical page bitmap is malformed")
    return hashlib.sha256(bytes(packed)).hexdigest()


@dataclass(frozen=True, slots=True)
class TrustedImageOnlyPageAdmission:
    """One page admission originating in the verified reviewer-signed prescan."""

    safe_alias: str
    content_sha256: str
    page_number: int
    page_content_sha256: str
    ocr_terminal_proof_sha256: str
    watermark_classifier_receipt_sha256: str
    reviewer_disposition: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.safe_alias, str)
            or _TRUSTED_IMAGE_ONLY_SAFE_ALIAS_RE.fullmatch(self.safe_alias) is None
            or not _is_lower_sha256(self.content_sha256)
            or isinstance(self.page_number, bool)
            or not isinstance(self.page_number, int)
            or self.page_number < 1
            or not _is_lower_sha256(self.page_content_sha256)
            or not _is_lower_sha256(self.ocr_terminal_proof_sha256)
            or not _is_lower_sha256(self.watermark_classifier_receipt_sha256)
            or self.reviewer_disposition != "SUBSTANTIVE_IMAGE"
        ):
            raise OcrError("Reviewed image-only page admission is malformed")

    def to_payload(self) -> dict[str, object]:
        """Return the exact signed safe-record shape without private source data."""

        return {
            "safe_alias": self.safe_alias,
            "content_sha256": self.content_sha256,
            "page_number": self.page_number,
            "page_content_sha256": self.page_content_sha256,
            "ocr_terminal_proof_sha256": self.ocr_terminal_proof_sha256,
            "watermark_classifier_receipt_sha256": (self.watermark_classifier_receipt_sha256),
            "reviewer_disposition": "SUBSTANTIVE_IMAGE",
        }


@dataclass(frozen=True, slots=True)
class TrustedImageOnlyPageAdmissionSet:
    """Hash-bound page records retained only after manifest verification."""

    admissions: tuple[TrustedImageOnlyPageAdmission, ...]
    reviewed_set_sha256: str

    def __post_init__(self) -> None:
        payloads = [admission.to_payload() for admission in self.admissions]
        identities = [
            (admission.safe_alias, admission.page_number) for admission in self.admissions
        ]
        if (
            not self.admissions
            or any(
                not isinstance(admission, TrustedImageOnlyPageAdmission)
                for admission in self.admissions
            )
            or len(set(identities)) != len(identities)
            or identities != sorted(identities)
            or not _is_lower_sha256(self.reviewed_set_sha256)
            or _canonical_json_sha256(payloads) != self.reviewed_set_sha256
        ):
            raise OcrError("Reviewed image-only page admission set is malformed")


def build_trusted_image_only_page_admission_set(
    *,
    records: object,
    expected_set_sha256: str,
) -> TrustedImageOnlyPageAdmissionSet:
    """Bind exact verified-manifest rows to their signed ordered-set digest."""

    if (
        not isinstance(records, Sequence)
        or isinstance(records, (str, bytes, bytearray))
        or not records
    ):
        raise OcrError("Reviewed image-only page admission set is malformed")
    admissions: list[TrustedImageOnlyPageAdmission] = []
    for record in records:
        if (
            not isinstance(record, Mapping)
            or set(record) != _TRUSTED_IMAGE_ONLY_PAGE_ADMISSION_FIELDS
        ):
            raise OcrError("Reviewed image-only page admission is malformed")
        admissions.append(
            TrustedImageOnlyPageAdmission(
                safe_alias=record["safe_alias"],
                content_sha256=record["content_sha256"],
                page_number=record["page_number"],
                page_content_sha256=record["page_content_sha256"],
                ocr_terminal_proof_sha256=record["ocr_terminal_proof_sha256"],
                watermark_classifier_receipt_sha256=record["watermark_classifier_receipt_sha256"],
                reviewer_disposition=record["reviewer_disposition"],
            )
        )
    return TrustedImageOnlyPageAdmissionSet(
        admissions=tuple(admissions),
        reviewed_set_sha256=expected_set_sha256,
    )


class OcrAdapter(Protocol):
    """Adapter interface for opt-in OCR implementations."""

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Return OCR text by page for PDF bytes."""

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Return OCR text for image bytes."""


@dataclass(frozen=True, slots=True)
class OcrConfig:
    """Config-gated OCR execution settings."""

    enabled: bool = False
    adapter: OcrAdapter | None = None
    max_pages: int = 10
    timeout_seconds: float = 30.0
    image_only_page_admissions: TrustedImageOnlyPageAdmissionSet | None = None


def normalize_ocr_confidence(values: Any) -> float | None:
    """Return the mean of valid Tesseract confidences (0-100) scaled to 0-1.

    Invalid entries (``-1``, non-numeric, empty, out-of-range) are ignored. Returns
    None when no valid confidence value exists.
    """
    if not isinstance(values, (list, tuple)):
        return None
    valid: list[float] = []
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric < 0.0 or numeric > 100.0:
            continue
        valid.append(numeric / 100.0)
    if not valid:
        return None
    return round(sum(valid) / len(valid), 4)


def overall_mean_confidence(pages: list[OcrPageText]) -> float | None:
    """Return the mean of page/image confidences (0-1), ignoring None, else None."""
    values = [page.confidence for page in pages if page.confidence is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _normalize_ocr_rescue_text(text: str) -> tuple[str, tuple[str, ...]]:
    normalized_characters = [
        character if unicodedata.category(character).startswith(("L", "N")) else " "
        for character in unicodedata.normalize("NFKC", text).casefold()
    ]
    normalized = " ".join("".join(normalized_characters).split())
    tokens = tuple(normalized.split()) if normalized else ()
    return normalized, tokens


def _ocr_confidence_histogram(values: Sequence[float]) -> OcrConfidenceHistogram:
    return OcrConfidenceHistogram(
        below_0_50=sum(value < 0.50 for value in values),
        from_0_50_to_below_0_60=sum(0.50 <= value < 0.60 for value in values),
        from_0_60_to_below_0_80=sum(0.60 <= value < 0.80 for value in values),
        at_or_above_0_80=sum(value >= 0.80 for value in values),
    )


def _make_ocr_rescue_candidate(
    *,
    matrix_ordinal: int,
    rotation_degrees: int,
    transform_id: str,
    psm: int,
    text: str,
    word_confidences: Sequence[float],
) -> OcrRescueCandidate:
    """Validate and quality-grade one completed candidate without discarding it."""
    if (
        isinstance(matrix_ordinal, bool)
        or not isinstance(matrix_ordinal, int)
        or matrix_ordinal < 0
        or matrix_ordinal >= len(OCR_RESCUE_MATRIX)
        or rotation_degrees not in OCR_RESCUE_ROTATIONS
        or transform_id not in OCR_RESCUE_TRANSFORMS
        or psm not in OCR_RESCUE_PSMS
        or not isinstance(text, str)
    ):
        raise OcrError("OCR rescue candidate identity is malformed")
    normalized, tokens = _normalize_ocr_rescue_text(text)
    if len(normalized) > _OCR_RESCUE_MAX_NORMALIZED_CODEPOINTS:
        raise OcrError("OCR rescue candidate text exceeded its bound")
    if len(tokens) > _OCR_RESCUE_MAX_TOKENS:
        raise OcrError("OCR rescue candidate token count exceeded its bound")

    raw_words = tuple(word for word in text.split() if word)
    try:
        confidences = tuple(float(value) for value in word_confidences)
    except (TypeError, ValueError) as exc:
        raise OcrError("OCR rescue candidate confidence is malformed") from exc
    if len(confidences) != len(raw_words) or any(
        not math.isfinite(value) or value < 0.0 or value > 1.0 for value in confidences
    ):
        raise OcrError("OCR rescue candidate confidence is malformed")

    word_count = len(confidences)
    if confidences:
        mean_confidence = math.fsum(confidences) / word_count
        median_confidence = float(statistics.median(confidences))
        minimum_confidence = min(confidences)
        maximum_confidence = max(confidences)
        high_confidence_fraction = (
            sum(value >= _OCR_RESCUE_HIGH_CONFIDENCE_THRESHOLD for value in confidences)
            / word_count
        )
    else:
        mean_confidence = 0.0
        median_confidence = 0.0
        minimum_confidence = 0.0
        maximum_confidence = 0.0
        high_confidence_fraction = 0.0

    rejection_reasons: list[str] = []
    if len(tokens) < _OCR_RESCUE_MIN_TOKENS:
        rejection_reasons.append("token_count")
    if sum(len(token) for token in tokens) < _OCR_RESCUE_MIN_ALPHANUMERIC_CODEPOINTS:
        rejection_reasons.append("alphanumeric_codepoint_count")
    if mean_confidence < _OCR_RESCUE_MIN_MEAN_CONFIDENCE:
        rejection_reasons.append("mean_confidence")
    if median_confidence < _OCR_RESCUE_MIN_MEDIAN_CONFIDENCE:
        rejection_reasons.append("median_confidence")
    if high_confidence_fraction < _OCR_RESCUE_MIN_HIGH_CONFIDENCE_FRACTION:
        rejection_reasons.append("high_confidence_fraction")
    quality_admitted = not rejection_reasons

    return OcrRescueCandidate(
        matrix_ordinal=matrix_ordinal,
        candidate_id=f"ocr-rescue-candidate-{matrix_ordinal:02d}",
        rotation_degrees=rotation_degrees,
        transform_id=transform_id,
        psm=psm,
        text=text,
        normalized_tokens=tokens,
        word_confidences=confidences,
        quality_admitted=quality_admitted,
        state="completed" if quality_admitted else "rejected",
        rejection_reasons=tuple(rejection_reasons),
        word_count=word_count,
        mean_confidence=mean_confidence,
        median_confidence=median_confidence,
        minimum_confidence=minimum_confidence,
        maximum_confidence=maximum_confidence,
        confidence_histogram_counts=_ocr_confidence_histogram(confidences),
        confidence_values_sha256=_canonical_json_sha256(list(confidences)),
        fraction_at_or_above_0_50=high_confidence_fraction,
        normalized_text_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def _ocr_rescue_multiset_dice(
    left_tokens: tuple[str, ...],
    right_tokens: tuple[str, ...],
    *,
    width: int,
) -> float:
    left = Counter(
        tuple(left_tokens[index : index + width]) for index in range(len(left_tokens) - width + 1)
    )
    right = Counter(
        tuple(right_tokens[index : index + width]) for index in range(len(right_tokens) - width + 1)
    )
    if not left or not right:
        return 0.0
    overlap = sum(min(count, right[gram]) for gram, count in left.items())
    return 2.0 * overlap / (sum(left.values()) + sum(right.values()))


def _ocr_rescue_agreement(left_text: str, right_text: str) -> float:
    """Return the lesser of unigram and adjacent-bigram multiset Dice."""
    _left_normalized, left_tokens = _normalize_ocr_rescue_text(left_text)
    _right_normalized, right_tokens = _normalize_ocr_rescue_text(right_text)
    return min(
        _ocr_rescue_multiset_dice(left_tokens, right_tokens, width=1),
        _ocr_rescue_multiset_dice(left_tokens, right_tokens, width=2),
    )


def _is_ocr_rescue_agreement_edge(value: float) -> bool:
    return math.isfinite(value) and value >= _OCR_RESCUE_MIN_AGREEMENT


def _round_ocr_rescue_agreement(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(
        _OCR_RESCUE_AGREEMENT_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )


def _candidate_metric(candidate: Any, name: str) -> float:
    try:
        value = float(getattr(candidate, name))
    except (AttributeError, TypeError, ValueError) as exc:
        raise OcrError("OCR rescue candidate metric is malformed") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise OcrError("OCR rescue candidate metric is malformed")
    return value


def _select_ocr_rescue_consensus(
    candidates: Sequence[Any],
    *,
    pairwise_agreements: Mapping[tuple[int, int], float],
) -> OcrRescueConsensus:
    """Select exactly one maximal qualifying clique, or fail closed."""
    admitted = [candidate for candidate in candidates if candidate.quality_admitted is True]
    ordinals = [candidate.matrix_ordinal for candidate in admitted]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in ordinals):
        raise OcrError("OCR rescue candidate ordinal is malformed")
    if len(set(ordinals)) != len(ordinals):
        raise OcrError("OCR rescue candidate ordinal is duplicated")

    agreement_by_pair: dict[tuple[int, int], float] = {}
    for left, right in combinations(admitted, 2):
        key = tuple(sorted((left.matrix_ordinal, right.matrix_ordinal)))
        raw_value = pairwise_agreements.get(key)
        if raw_value is None:
            raise OcrError("OCR rescue agreement matrix is incomplete")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise OcrError("OCR rescue agreement matrix is incomplete") from exc
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise OcrError("OCR rescue agreement matrix is malformed")
        agreement_by_pair[key] = value

    qualifying: list[frozenset[int]] = []
    candidates_by_ordinal = {candidate.matrix_ordinal: candidate for candidate in admitted}
    for rotation in OCR_RESCUE_ROTATIONS:
        rotation_candidates = [
            candidate for candidate in admitted if candidate.rotation_degrees == rotation
        ]
        for size in range(3, len(rotation_candidates) + 1):
            for members in combinations(rotation_candidates, size):
                if (
                    len({member.transform_id for member in members}) < 2
                    or {member.psm for member in members} != set(OCR_RESCUE_PSMS)
                    or any(
                        not _is_ocr_rescue_agreement_edge(
                            agreement_by_pair[
                                tuple(sorted((left.matrix_ordinal, right.matrix_ordinal)))
                            ]
                        )
                        for left, right in combinations(members, 2)
                    )
                ):
                    continue
                qualifying.append(frozenset(member.matrix_ordinal for member in members))
    maximal = [clique for clique in qualifying if not any(clique < other for other in qualifying)]
    if len(maximal) != 1:
        raise OcrRescueNoConsensusError("OCR rescue produced no unique consensus")

    supporting = tuple(candidates_by_ordinal[ordinal] for ordinal in sorted(maximal[0]))
    clique_agreements = [
        agreement_by_pair[tuple(sorted((left.matrix_ordinal, right.matrix_ordinal)))]
        for left, right in combinations(supporting, 2)
    ]
    minimum_clique_agreement = min(clique_agreements)
    mean_clique_agreement = math.fsum(clique_agreements) / len(clique_agreements)

    def representative_key(candidate: Any) -> tuple[float, float, float, float, int]:
        values = [
            agreement_by_pair[tuple(sorted((candidate.matrix_ordinal, other.matrix_ordinal)))]
            for other in supporting
            if other.matrix_ordinal != candidate.matrix_ordinal
        ]
        return (
            min(values),
            math.fsum(values) / len(values),
            _candidate_metric(candidate, "mean_confidence"),
            _candidate_metric(candidate, "fraction_at_or_above_0_50"),
            -candidate.matrix_ordinal,
        )

    return OcrRescueConsensus(
        winner=max(supporting, key=representative_key),
        supporting_candidates=supporting,
        minimum_clique_agreement=minimum_clique_agreement,
        mean_clique_agreement=mean_clique_agreement,
    )


PdfWorkerTarget = Callable[[bytes, int, int, str, float, Any], None]
PdfFileWorkerTarget = Callable[[str | Path, int, int, str, float, Any], None]
PdfRenderAdmissionFileWorkerTarget = Callable[
    [str | Path, tuple[dict[str, object], ...], str, bool, float, Any],
    None,
]
ImageWorkerTarget = Callable[[bytes, int, str, float, Any], None]


def _bound_poppler_worker(
    target: Callable[..., None],
    binding: GovernedPopplerRuntime | None,
) -> Callable[..., None]:
    """Bind the admitted runtime without changing injected test-target arity."""

    if binding is None:
        return target
    return partial(target, poppler_runtime=binding)


def _verified_poppler_path_kwargs(
    binding: GovernedPopplerRuntime | None,
) -> dict[str, str]:
    """Revalidate the bound executable/library pair before a worker renders."""

    if binding is None and platform.system() != "Linux":
        return {}
    try:
        if binding is None:
            binding = resolve_pinned_poppler_runtime()
            verify_governed_poppler_runtime(binding)
            install_poppler_child_environment(binding, apply_to_process=True)
        elif type(binding) is PinnedLinuxPopplerRuntime:
            verify_governed_poppler_runtime(binding)
        elif type(binding) is PinnedPopplerRuntime:
            verify_pinned_poppler_runtime(binding)
        else:
            verify_governed_poppler_runtime(binding)
    except PopplerRuntimeBindingError as exc:
        raise OcrUnavailableError("OCR Poppler runtime binding failed verification") from exc
    return {"poppler_path": str(binding.binary_dir)}


class TesseractOcrAdapter:
    """Process-isolated OCR adapter backed by pdf2image and pytesseract."""

    def __init__(
        self,
        *,
        dpi: int = 200,
        language: str = "eng",
        worker_target: PdfWorkerTarget | None = None,
        file_worker_target: PdfFileWorkerTarget | None = None,
        render_admission_file_worker_target: PdfRenderAdmissionFileWorkerTarget | None = None,
        image_worker_target: ImageWorkerTarget | None = None,
        poppler_runtime: GovernedPopplerRuntime | None = None,
    ) -> None:
        if (
            poppler_runtime is not None
            and type(poppler_runtime) is not PinnedPopplerRuntime
            and (type(poppler_runtime) is not PinnedLinuxPopplerRuntime)
        ):
            raise OcrError("OCR Poppler runtime binding is malformed")
        self._dpi = dpi
        self._language = language
        self._poppler_runtime = poppler_runtime
        self._worker_target = worker_target or _bound_poppler_worker(
            _tesseract_ocr_worker,
            poppler_runtime,
        )
        self._file_worker_target = file_worker_target or _bound_poppler_worker(
            _tesseract_pdf_file_ocr_worker,
            poppler_runtime,
        )
        self._partial_file_worker_target = file_worker_target or _bound_poppler_worker(
            _tesseract_pdf_file_ocr_worker_with_partial_results,
            poppler_runtime,
        )
        self._render_admission_file_worker_target = (
            render_admission_file_worker_target
            or _bound_poppler_worker(
                _tesseract_pdf_file_ocr_worker_with_render_admissions,
                poppler_runtime,
            )
        )
        self._image_worker_target = image_worker_target or _tesseract_image_ocr_worker

    @property
    def dpi(self) -> int:
        """Return the safe configured render density used for OCR admission."""
        return self._dpi

    @property
    def language(self) -> str:
        """Return the safe configured Tesseract language identifier."""
        return self._language

    @property
    def poppler_runtime(self) -> GovernedPopplerRuntime | None:
        """Return the explicitly admitted Poppler authority, if configured."""

        return self._poppler_runtime

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Return OCR text from the configured page window."""
        if max_pages < 1:
            raise OcrError("OCR max_pages must be positive")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise OcrTimeoutError("OCR timeout must be positive")

        payload = _run_ocr_worker(
            target=self._worker_target,
            args=(data, max_pages, self._dpi, self._language, timeout_seconds),
            timeout_seconds=timeout_seconds,
        )
        return _pages_from_worker_payload(payload)

    def extract_pdf_file(
        self,
        source_path: str | Path,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """OCR one caller-owned PDF path without creating another PDF copy."""
        if max_pages < 1:
            raise OcrError("OCR max_pages must be positive")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise OcrTimeoutError("OCR timeout must be positive")
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise OcrError("OCR source file is unavailable")

        payload = _run_ocr_worker(
            target=self._file_worker_target,
            args=(path, max_pages, self._dpi, self._language),
            timeout_seconds=timeout_seconds,
            worker_uses_execution_deadline=True,
        )
        return _pages_from_worker_payload(payload)

    def extract_pdf_file_with_partial_results(
        self,
        source_path: str | Path,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Retain proof-bound successful page text when a reviewed page has no consensus."""

        if max_pages < 1:
            raise OcrError("OCR max_pages must be positive")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise OcrTimeoutError("OCR timeout must be positive")
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise OcrError("OCR source file is unavailable")

        payload = _run_ocr_worker(
            target=self._partial_file_worker_target,
            args=(path, max_pages, self._dpi, self._language),
            timeout_seconds=timeout_seconds,
            worker_uses_execution_deadline=True,
        )
        return _pages_from_worker_payload(payload)

    def extract_pdf_file_with_render_admissions(
        self,
        source_path: str | Path,
        *,
        render_admissions: tuple[OcrPageRenderAdmission, ...],
        deadline: float,
        retain_partial_results: bool,
    ) -> list[OcrPageText]:
        """OCR exactly the admitted sparse PDF pages within the caller's deadline."""

        if (
            not isinstance(render_admissions, tuple)
            or not render_admissions
            or any(
                not isinstance(admission, OcrPageRenderAdmission) for admission in render_admissions
            )
        ):
            raise OcrError("OCR render admissions are malformed")
        if any(
            admission.schema_version == OCR_RENDER_ADMISSION_SCHEMA_VERSION
            and (
                self._poppler_runtime is None
                or admission.poppler_version != self._poppler_runtime.version
                or admission.poppler_runtime_identity_sha256
                != self._poppler_runtime.runtime_identity_sha256
            )
            for admission in render_admissions
        ):
            raise OcrError("OCR Poppler runtime binding drifted from admission")
        page_numbers = tuple(admission.page_number for admission in render_admissions)
        if (
            page_numbers != tuple(sorted(page_numbers))
            or len(set(page_numbers)) != len(page_numbers)
            or any(admission.configured_dpi != self._dpi for admission in render_admissions)
        ):
            raise OcrError("OCR render admissions are malformed")
        if type(retain_partial_results) is not bool:
            raise OcrError("OCR partial-result policy is malformed")
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise OcrTimeoutError("OCR timed out")
        absolute_deadline = float(deadline)
        if not math.isfinite(absolute_deadline) or absolute_deadline <= time.monotonic():
            raise OcrTimeoutError("OCR timed out")
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise OcrError("OCR source file is unavailable")

        payload = _run_ocr_worker_until(
            target=self._render_admission_file_worker_target,
            args=(
                path,
                tuple(admission._to_worker_payload() for admission in render_admissions),
                self._language,
                retain_partial_results,
            ),
            deadline=absolute_deadline,
            worker_uses_execution_deadline=True,
        )
        pages = _pages_from_worker_payload(payload)
        if tuple(page.page_number for page in pages) != page_numbers or any(
            page.render_admission is None
            or not page.render_admission.same_render_plan(admission)
            or (
                admission.schema_version == OCR_RENDER_ADMISSION_SCHEMA_VERSION
                and not page.render_admission.has_observed_render
            )
            for page, admission in zip(pages, render_admissions, strict=True)
        ):
            raise OcrError("OCR worker returned incomplete render admissions")
        return pages

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Return OCR text from one image in a process-isolated worker."""
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise OcrTimeoutError("OCR timeout must be positive")

        payload = _run_ocr_worker(
            target=self._image_worker_target,
            args=(data, self._dpi, self._language, timeout_seconds),
            timeout_seconds=timeout_seconds,
        )
        return _pages_from_worker_payload(payload)


def _run_ocr_worker(
    *,
    target: Callable[..., None],
    args: tuple[object, ...],
    timeout_seconds: float,
    worker_uses_execution_deadline: bool = False,
) -> dict[str, object]:
    """Drain one bounded result while the worker is alive, then release all handles."""
    deadline = time.monotonic() + timeout_seconds
    return _run_ocr_worker_until(
        target=target,
        args=args,
        deadline=deadline,
        worker_uses_execution_deadline=worker_uses_execution_deadline,
    )


def _run_ocr_worker_until(
    *,
    target: Callable[..., None],
    args: tuple[object, ...],
    deadline: float,
    worker_uses_execution_deadline: bool = False,
) -> dict[str, object]:
    """Run one killable parser worker within a caller-owned absolute deadline."""

    if not math.isfinite(deadline) or deadline <= time.monotonic():
        raise OcrTimeoutError("OCR timed out")
    execution_deadline = _ocr_execution_deadline(deadline)
    queue: mp.Queue[dict[str, object]] = mp.Queue(maxsize=1)
    worker_args = (
        (*args, execution_deadline, queue) if worker_uses_execution_deadline else (*args, queue)
    )
    process = mp.Process(target=target, args=worker_args)
    started = False
    try:
        process.start()
        started = True
        payload = _receive_ocr_payload(
            queue=queue,
            process=process,
            deadline=execution_deadline,
        )
        remaining = execution_deadline - time.monotonic()
        if remaining > 0:
            process.join(remaining)
        if process.is_alive():
            raise OcrTimeoutError("OCR timed out")
        if time.monotonic() >= deadline:
            raise OcrTimeoutError("OCR timed out")
        return payload
    finally:
        try:
            _close_ocr_process(process=process, started=started, deadline=deadline)
        finally:
            _close_ocr_queue(queue)


def _ocr_execution_deadline(deadline: float) -> float:
    """Reserve part of the configured wall-clock budget for bounded cleanup."""
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise OcrTimeoutError("OCR timed out")
    cleanup_reserve = min(_OCR_CLEANUP_RESERVE_MAX_SECONDS, remaining / 4.0)
    return deadline - cleanup_reserve


def _receive_ocr_payload(
    *,
    queue: mp.Queue[dict[str, object]],
    process: mp.Process,
    deadline: float,
) -> dict[str, object]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OcrTimeoutError("OCR timed out")
        try:
            return queue.get(timeout=min(0.05, remaining))
        except Empty:
            if process.is_alive():
                continue
            process.join(timeout=max(0.0, deadline - time.monotonic()))
            try:
                return queue.get_nowait()
            except (EOFError, Empty, OSError, ValueError) as exc:
                raise OcrError("OCR worker produced no result") from exc
        except (EOFError, OSError, ValueError) as exc:
            raise OcrError("OCR worker produced no result") from exc


def _close_ocr_process(*, process: mp.Process, started: bool, deadline: float) -> None:
    """Terminate a worker and close it once the post-kill reap observes exit."""
    if started and process.is_alive():
        _terminate_process_tree(
            process,
            timeout_seconds=_remaining_cleanup_seconds(deadline) / 2.0,
        )
    if started:
        process.join(timeout=_remaining_cleanup_seconds(deadline) / 2.0)
        if process.is_alive():
            with contextlib.suppress(OSError, ValueError):
                process.kill()
            process.join(
                timeout=max(
                    _remaining_cleanup_seconds(deadline),
                    _OCR_PROCESS_REAP_SECONDS,
                )
            )
    if not started or not process.is_alive():
        with contextlib.suppress(OSError, ValueError):
            process.close()
    else:
        _LOG.error("OCR_WORKER_HANDLE_UNREAPED", extra={"pid": process.pid})


def _close_ocr_queue(queue: mp.Queue[dict[str, object]]) -> None:
    # The parent never puts into this queue. Avoid joining a feeder that may be
    # blocked on a dead child/pipe after the common OCR deadline expires.
    with contextlib.suppress(OSError, ValueError):
        queue.cancel_join_thread()
    with contextlib.suppress(OSError, ValueError):
        queue.close()


def _remaining_cleanup_seconds(deadline: float) -> float:
    """Return remaining cleanup budget without raising after deadline expiry."""
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        return 0.0
    return remaining


def _worker_provenance_float(
    value: object,
    *,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OcrError("OCR rescue provenance is malformed")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise OcrError("OCR rescue provenance is malformed")
    return result


def _worker_provenance_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise OcrError("OCR rescue provenance is malformed")
    return value


def _worker_provenance_sha256(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise OcrError("OCR rescue provenance is malformed")
    try:
        int(value, 16)
    except ValueError as exc:
        raise OcrError("OCR rescue provenance is malformed") from exc
    return value


def _rescue_provenance_from_worker_payload(value: object) -> OcrRescueProvenance:
    expected_keys = {
        "attempted_candidate_count",
        "candidate_receipt_sha256",
        "mean_clique_agreement",
        "minimum_clique_agreement",
        "normalized_page_text_sha256",
        "policy_sha256",
        "policy_version",
        "supporting_candidate_count",
        "winner_confidence_histogram_counts",
        "winner_confidence_values_sha256",
        "winner_fraction_at_or_above_0_50",
        "winner_maximum_confidence",
        "winner_mean_confidence",
        "winner_median_confidence",
        "winner_minimum_confidence",
        "winner_psm",
        "winner_rotation_degrees",
        "winner_transform_id",
        "winner_word_count",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise OcrError("OCR rescue provenance is malformed")
    if (
        value.get("policy_version") != OCR_RESCUE_POLICY_VERSION
        or value.get("policy_sha256") != OCR_RESCUE_POLICY_SHA256
        or value.get("winner_rotation_degrees") not in OCR_RESCUE_ROTATIONS
        or value.get("winner_transform_id") not in OCR_RESCUE_TRANSFORMS
        or value.get("winner_psm") not in OCR_RESCUE_PSMS
        or value.get("attempted_candidate_count") != len(OCR_RESCUE_MATRIX)
    ):
        raise OcrError("OCR rescue provenance is malformed")
    raw_histogram = value.get("winner_confidence_histogram_counts")
    if not isinstance(raw_histogram, Mapping) or set(raw_histogram) != set(
        _OCR_RESCUE_HISTOGRAM_KEYS
    ):
        raise OcrError("OCR rescue provenance is malformed")
    histogram_values = {
        key: _worker_provenance_int(raw_histogram.get(key), minimum=0, maximum=4_096)
        for key in _OCR_RESCUE_HISTOGRAM_KEYS
    }
    histogram = OcrConfidenceHistogram(**histogram_values)
    word_count = _worker_provenance_int(
        value.get("winner_word_count"),
        minimum=1,
        maximum=4_096,
    )
    if sum(histogram_values.values()) != word_count:
        raise OcrError("OCR rescue provenance is malformed")
    mean_confidence = _worker_provenance_float(value.get("winner_mean_confidence"))
    median_confidence = _worker_provenance_float(value.get("winner_median_confidence"))
    minimum_confidence = _worker_provenance_float(value.get("winner_minimum_confidence"))
    maximum_confidence = _worker_provenance_float(value.get("winner_maximum_confidence"))
    if not (
        minimum_confidence <= mean_confidence <= maximum_confidence
        and minimum_confidence <= median_confidence <= maximum_confidence
    ):
        raise OcrError("OCR rescue provenance is malformed")
    return OcrRescueProvenance(
        policy_version=OCR_RESCUE_POLICY_VERSION,
        policy_sha256=OCR_RESCUE_POLICY_SHA256,
        winner_rotation_degrees=int(value["winner_rotation_degrees"]),
        winner_transform_id=str(value["winner_transform_id"]),
        winner_psm=int(value["winner_psm"]),
        winner_word_count=word_count,
        winner_mean_confidence=mean_confidence,
        winner_median_confidence=median_confidence,
        winner_minimum_confidence=minimum_confidence,
        winner_maximum_confidence=maximum_confidence,
        winner_confidence_histogram_counts=histogram,
        winner_confidence_values_sha256=_worker_provenance_sha256(
            value.get("winner_confidence_values_sha256")
        ),
        winner_fraction_at_or_above_0_50=_worker_provenance_float(
            value.get("winner_fraction_at_or_above_0_50"),
            minimum=_OCR_RESCUE_MIN_HIGH_CONFIDENCE_FRACTION,
        ),
        attempted_candidate_count=len(OCR_RESCUE_MATRIX),
        supporting_candidate_count=_worker_provenance_int(
            value.get("supporting_candidate_count"),
            minimum=3,
            maximum=len(OCR_RESCUE_MATRIX),
        ),
        minimum_clique_agreement=_worker_provenance_float(
            value.get("minimum_clique_agreement"),
            minimum=_OCR_RESCUE_MIN_AGREEMENT,
        ),
        mean_clique_agreement=_worker_provenance_float(
            value.get("mean_clique_agreement"),
            minimum=_OCR_RESCUE_MIN_AGREEMENT,
        ),
        candidate_receipt_sha256=_worker_provenance_sha256(value.get("candidate_receipt_sha256")),
        normalized_page_text_sha256=_worker_provenance_sha256(
            value.get("normalized_page_text_sha256")
        ),
    )


_OCR_PAGE_EXECUTION_RECEIPT_KEYS = {
    "accepted_text",
    "accepted_text_codepoint_count",
    "accepted_text_span_count",
    "accepted_text_token_count",
    "candidate_receipt_sha256",
    "normalized_page_text_sha256",
    "outcome",
    "page_number",
    "primary_completed",
    "primary_token_count",
    "rescue_attempted_candidate_count",
    "rescue_completed_candidate_count",
    "schema_version",
}


def _worker_receipt_int(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise OcrError("OCR worker returned malformed execution proof")
    return value


def _worker_receipt_sha256(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise OcrError("OCR worker returned malformed execution proof")
    try:
        int(value, 16)
    except ValueError as exc:
        raise OcrError("OCR worker returned malformed execution proof") from exc
    return value


def _ocr_page_execution_receipt_from_payload(value: object) -> OcrPageExecutionReceipt:
    if not isinstance(value, Mapping) or set(value) != _OCR_PAGE_EXECUTION_RECEIPT_KEYS:
        raise OcrError("OCR worker returned malformed execution proof")
    if value.get("schema_version") != OCR_PAGE_EXECUTION_RECEIPT_SCHEMA_VERSION:
        raise OcrError("OCR worker returned malformed execution proof")
    outcome = value.get("outcome")
    if outcome not in {"primary_text", "rescue_text", "no_consensus"}:
        raise OcrError("OCR worker returned malformed execution proof")
    if value.get("primary_completed") is not True or not isinstance(
        value.get("accepted_text"), bool
    ):
        raise OcrError("OCR worker returned malformed execution proof")

    page_number = _worker_receipt_int(value.get("page_number"), minimum=1, maximum=1_000_000)
    primary_token_count = _worker_receipt_int(
        value.get("primary_token_count"),
        minimum=0,
        maximum=_OCR_PAGE_EXECUTION_MAX_TOKENS,
    )
    attempted = _worker_receipt_int(
        value.get("rescue_attempted_candidate_count"),
        minimum=0,
        maximum=len(OCR_RESCUE_MATRIX),
    )
    completed = _worker_receipt_int(
        value.get("rescue_completed_candidate_count"),
        minimum=0,
        maximum=len(OCR_RESCUE_MATRIX),
    )
    accepted_token_count = _worker_receipt_int(
        value.get("accepted_text_token_count"),
        minimum=0,
        maximum=_OCR_PAGE_EXECUTION_MAX_TOKENS,
    )
    accepted_span_count = _worker_receipt_int(
        value.get("accepted_text_span_count"),
        minimum=0,
        maximum=_OCR_PAGE_EXECUTION_MAX_TOKENS,
    )
    accepted_codepoint_count = _worker_receipt_int(
        value.get("accepted_text_codepoint_count"),
        minimum=0,
        maximum=_OCR_PAGE_EXECUTION_MAX_CODEPOINTS,
    )
    normalized_text_sha256 = _worker_receipt_sha256(value.get("normalized_page_text_sha256"))
    raw_candidate_sha256 = value.get("candidate_receipt_sha256")
    candidate_sha256 = (
        None if raw_candidate_sha256 is None else _worker_receipt_sha256(raw_candidate_sha256)
    )
    accepted_text = value["accepted_text"]

    if outcome == "primary_text":
        valid = (
            accepted_text is True
            and primary_token_count > 0
            and attempted == completed == 0
            and accepted_token_count == primary_token_count
            and accepted_span_count > 0
            and accepted_codepoint_count > 0
            and candidate_sha256 is None
        )
    elif outcome == "rescue_text":
        valid = (
            accepted_text is True
            and primary_token_count == 0
            and attempted == completed == len(OCR_RESCUE_MATRIX)
            and accepted_token_count > 0
            and accepted_span_count > 0
            and accepted_codepoint_count > 0
            and candidate_sha256 is not None
        )
    else:
        valid = (
            accepted_text is False
            and primary_token_count == 0
            and attempted == completed == len(OCR_RESCUE_MATRIX)
            and accepted_token_count == accepted_span_count == accepted_codepoint_count == 0
            and normalized_text_sha256 == _EMPTY_NORMALIZED_OCR_TEXT_SHA256
            and candidate_sha256 is not None
        )
    if not valid:
        raise OcrError("OCR worker returned malformed execution proof")

    return OcrPageExecutionReceipt(
        schema_version=OCR_PAGE_EXECUTION_RECEIPT_SCHEMA_VERSION,
        page_number=page_number,
        outcome=str(outcome),
        primary_completed=True,
        primary_token_count=primary_token_count,
        rescue_attempted_candidate_count=attempted,
        rescue_completed_candidate_count=completed,
        accepted_text=accepted_text,
        accepted_text_token_count=accepted_token_count,
        accepted_text_span_count=accepted_span_count,
        accepted_text_codepoint_count=accepted_codepoint_count,
        normalized_page_text_sha256=normalized_text_sha256,
        candidate_receipt_sha256=candidate_sha256,
    )


def _ocr_no_consensus_file_digest_payload(
    page_receipts: Sequence[OcrPageExecutionReceipt],
) -> dict[str, object]:
    return {
        "schema_version": OCR_NO_CONSENSUS_FILE_PROOF_SCHEMA_VERSION,
        "ocr_rescue_policy_version": OCR_RESCUE_POLICY_VERSION,
        "ocr_rescue_policy_sha256": OCR_RESCUE_POLICY_SHA256,
        "page_receipts": [receipt.to_payload() for receipt in page_receipts],
    }


def _ocr_no_consensus_file_proof(
    page_receipts: Sequence[OcrPageExecutionReceipt],
) -> OcrNoConsensusFileProof:
    receipts = tuple(
        _ocr_page_execution_receipt_from_payload(receipt.to_payload()) for receipt in page_receipts
    )
    page_numbers = tuple(receipt.page_number for receipt in receipts)
    if (
        not receipts
        or page_numbers != tuple(sorted(set(page_numbers)))
        or not any(receipt.outcome == "no_consensus" for receipt in receipts)
    ):
        raise OcrError("OCR worker returned malformed execution proof")
    return OcrNoConsensusFileProof(
        schema_version=OCR_NO_CONSENSUS_FILE_PROOF_SCHEMA_VERSION,
        ocr_rescue_policy_version=OCR_RESCUE_POLICY_VERSION,
        ocr_rescue_policy_sha256=OCR_RESCUE_POLICY_SHA256,
        page_receipts=receipts,
        ocr_result_sha256=_canonical_json_sha256(_ocr_no_consensus_file_digest_payload(receipts)),
    )


def _ocr_no_consensus_file_proof_from_payload(value: object) -> OcrNoConsensusFileProof:
    expected_keys = {
        "schema_version",
        "ocr_rescue_policy_version",
        "ocr_rescue_policy_sha256",
        "page_receipts",
        "ocr_result_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise OcrError("OCR worker returned malformed execution proof")
    if (
        value.get("schema_version") != OCR_NO_CONSENSUS_FILE_PROOF_SCHEMA_VERSION
        or value.get("ocr_rescue_policy_version") != OCR_RESCUE_POLICY_VERSION
        or value.get("ocr_rescue_policy_sha256") != OCR_RESCUE_POLICY_SHA256
        or not isinstance(value.get("page_receipts"), list)
    ):
        raise OcrError("OCR worker returned malformed execution proof")
    receipts = tuple(
        _ocr_page_execution_receipt_from_payload(receipt) for receipt in value["page_receipts"]
    )
    proof = _ocr_no_consensus_file_proof(receipts)
    supplied_digest = _worker_receipt_sha256(value.get("ocr_result_sha256"))
    if supplied_digest != proof.ocr_result_sha256:
        raise OcrError("OCR worker returned malformed execution proof")
    return proof


def validate_ocr_no_consensus_file_proof(value: object) -> OcrNoConsensusFileProof:
    """Revalidate an adapter-supplied proof at the parser trust boundary."""

    if not isinstance(value, OcrNoConsensusFileProof):
        raise OcrError("OCR worker returned malformed execution proof")
    return _ocr_no_consensus_file_proof_from_payload(value.to_payload())


def ocr_page_execution_receipt_sha256(value: object) -> str:
    """Return the canonical digest of one revalidated, text-free page proof."""

    if not isinstance(value, OcrPageExecutionReceipt):
        raise OcrError("OCR page execution receipt is malformed")
    receipt = _ocr_page_execution_receipt_from_payload(value.to_payload())
    return _canonical_json_sha256(receipt.to_payload())


def validate_ocr_page_execution_receipt_payload(value: object) -> OcrPageExecutionReceipt:
    """Revalidate one serialized page execution receipt at a durable boundary."""

    return _ocr_page_execution_receipt_from_payload(value)


def validate_ocr_no_consensus_file_proof_payload(value: object) -> OcrNoConsensusFileProof:
    """Revalidate a serialized proof at a downstream persistence boundary."""

    return _ocr_no_consensus_file_proof_from_payload(value)


_LEGACY_OCR_RENDER_ADMISSION_WORKER_FIELDS = frozenset(
    {
        "page_number",
        "schema_version",
        "ladder_version",
        "configured_dpi",
        "chosen_dpi",
        "projected_width",
        "projected_height",
        "projected_pixel_count",
        "max_pixel_count",
    }
)
_OCR_RENDER_ADMISSION_WORKER_FIELDS = _LEGACY_OCR_RENDER_ADMISSION_WORKER_FIELDS | {
    "expected_render_width",
    "expected_render_height",
    "expected_render_pixel_count",
    "poppler_version",
    "poppler_runtime_identity_sha256",
    "expected_dimension_policy_id",
    "expected_dimension_policy_sha256",
    "comparison_mode",
    "actual_render_width",
    "actual_render_height",
    "actual_render_pixel_count",
    "width_delta_pixels",
    "height_delta_pixels",
    "tolerance_pixels",
    "tolerance_used",
}


def _ocr_page_render_admission_from_worker_payload(
    value: object,
) -> OcrPageRenderAdmission:
    if not isinstance(value, Mapping):
        raise OcrError("OCR worker returned malformed render admission")
    payload_fields = frozenset(value)
    if payload_fields not in {
        _LEGACY_OCR_RENDER_ADMISSION_WORKER_FIELDS,
        _OCR_RENDER_ADMISSION_WORKER_FIELDS,
    }:
        raise OcrError("OCR worker returned malformed render admission")
    try:
        common = {
            "page_number": value["page_number"],
            "schema_version": value["schema_version"],
            "ladder_version": value["ladder_version"],
            "configured_dpi": value["configured_dpi"],
            "chosen_dpi": value["chosen_dpi"],
            "projected_width": value["projected_width"],
            "projected_height": value["projected_height"],
            "projected_pixel_count": value["projected_pixel_count"],
            "max_pixel_count": value["max_pixel_count"],
        }
        if payload_fields == _LEGACY_OCR_RENDER_ADMISSION_WORKER_FIELDS:
            return OcrPageRenderAdmission(**common)
        return OcrPageRenderAdmission(
            **common,
            expected_render_width=value["expected_render_width"],
            expected_render_height=value["expected_render_height"],
            expected_render_pixel_count=value["expected_render_pixel_count"],
            poppler_version=value["poppler_version"],
            poppler_runtime_identity_sha256=value["poppler_runtime_identity_sha256"],
            expected_dimension_policy_id=value["expected_dimension_policy_id"],
            expected_dimension_policy_sha256=value["expected_dimension_policy_sha256"],
            comparison_mode=value["comparison_mode"],
            actual_render_width=value["actual_render_width"],
            actual_render_height=value["actual_render_height"],
            actual_render_pixel_count=value["actual_render_pixel_count"],
            width_delta_pixels=value["width_delta_pixels"],
            height_delta_pixels=value["height_delta_pixels"],
            tolerance_pixels=value["tolerance_pixels"],
            tolerance_used=value["tolerance_used"],
        )
    except (KeyError, TypeError) as exc:
        raise OcrError("OCR worker returned malformed render admission") from exc


def validate_observed_ocr_render_admission_payload(
    value: object,
    *,
    page_number: int,
) -> OcrPageRenderAdmission:
    """Validate the exact v2 span-shaped receipt for one observed PDF render."""

    if (
        type(page_number) is not int
        or page_number < 1
        or not isinstance(value, Mapping)
        or "page_number" in value
    ):
        raise OcrError("OCR render admission is malformed")
    admission = _ocr_page_render_admission_from_worker_payload(
        {"page_number": page_number, **dict(value)}
    )
    if (
        admission.schema_version != OCR_RENDER_ADMISSION_SCHEMA_VERSION
        or not admission.has_observed_render
        or admission.to_span_dict() != dict(value)
    ):
        raise OcrError("OCR render admission is malformed")
    return admission


def _render_admission_from_worker_page(
    page: Mapping[str, object],
) -> OcrPageRenderAdmission | None:
    if "render_admission" not in page:
        return None
    admission = _ocr_page_render_admission_from_worker_payload(page["render_admission"])
    if admission.page_number != _worker_page_number(dict(page)):
        raise OcrError("OCR worker returned malformed render admission")
    return admission


def _no_consensus_render_admissions_from_worker_payload(
    value: object,
    *,
    proof: OcrNoConsensusFileProof,
) -> tuple[OcrPageRenderAdmission, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise OcrError("OCR worker returned malformed render admissions")
    admissions = tuple(_ocr_page_render_admission_from_worker_payload(row) for row in value)
    if tuple(admission.page_number for admission in admissions) != tuple(
        receipt.page_number for receipt in proof.page_receipts
    ) or any(
        admission.schema_version == OCR_RENDER_ADMISSION_SCHEMA_VERSION
        and not admission.has_observed_render
        for admission in admissions
    ):
        raise OcrError("OCR worker returned malformed render admissions")
    return admissions


def _pages_from_worker_payload(payload: dict[str, object]) -> list[OcrPageText]:
    status = payload.get("status")
    if status == "success":
        pages = payload.get("pages")
        if not isinstance(pages, list):
            raise OcrError("OCR worker returned malformed pages")
        results: list[OcrPageText] = []
        for page in pages:
            if not isinstance(page, dict):
                raise OcrError("OCR worker returned malformed page")
            results.append(
                OcrPageText(
                    page_number=int(page["page_number"]),
                    text=str(page["text"]),
                    confidence=normalize_ocr_confidence(page.get("confidences")),
                    rescue_provenance=(
                        _rescue_provenance_from_worker_payload(page["rescue_provenance"])
                        if "rescue_provenance" in page
                        else None
                    ),
                    render_admission=_render_admission_from_worker_page(page),
                )
            )
        return results
    if status == "timeout":
        raise OcrTimeoutError("OCR timed out")
    if status == "unavailable":
        raise OcrUnavailableError("OCR dependencies are unavailable")
    if status == "no_consensus":
        if set(payload) not in (
            {"status", "proof"},
            {"status", "proof", "render_admissions"},
        ):
            raise OcrError("OCR worker returned malformed execution proof")
        proof = _ocr_no_consensus_file_proof_from_payload(payload.get("proof"))
        render_admissions = _no_consensus_render_admissions_from_worker_payload(
            payload.get("render_admissions"),
            proof=proof,
        )
        raise OcrRescueNoConsensusError(
            "OCR rescue produced no unique consensus",
            file_proof=proof,
            render_admissions=render_admissions,
        )
    if status == "partial_no_consensus":
        base_fields = {
            "status",
            "proof",
            "accepted_pages",
            "no_consensus_page_content_sha256s",
        }
        if set(payload) not in (base_fields, base_fields | {"render_admissions"}):
            raise OcrError("OCR worker returned malformed execution proof")
        proof = _ocr_no_consensus_file_proof_from_payload(payload.get("proof"))
        accepted_pages = _accepted_pages_from_partial_worker_payload(
            payload.get("accepted_pages"),
            proof=proof,
        )
        page_content_sha256s = _no_consensus_page_content_sha256s_from_worker_payload(
            payload.get("no_consensus_page_content_sha256s"),
            proof=proof,
        )
        render_admissions = _no_consensus_render_admissions_from_worker_payload(
            payload.get("render_admissions"),
            proof=proof,
        )
        raise OcrRescueNoConsensusError(
            "OCR rescue produced no unique consensus",
            file_proof=proof,
            accepted_pages=accepted_pages,
            page_content_sha256s=page_content_sha256s,
            render_admissions=render_admissions,
        )
    raise OcrError("OCR failed")


def _accepted_page_from_worker_payload(page: object) -> tuple[OcrPageText, OcrPageExecutionReceipt]:
    if not isinstance(page, dict):
        raise OcrError("OCR worker returned malformed execution proof")
    receipt = _execution_receipt_for_worker_page(page)
    if not receipt.accepted_text or receipt.outcome == "no_consensus":
        raise OcrError("OCR worker returned malformed execution proof")
    return (
        OcrPageText(
            page_number=receipt.page_number,
            text=str(page["text"]),
            confidence=normalize_ocr_confidence(page.get("confidences")),
            rescue_provenance=(
                _rescue_provenance_from_worker_payload(page["rescue_provenance"])
                if "rescue_provenance" in page
                else None
            ),
            render_admission=_render_admission_from_worker_page(page),
        ),
        receipt,
    )


def _accepted_pages_from_partial_worker_payload(
    value: object,
    *,
    proof: OcrNoConsensusFileProof,
) -> tuple[OcrPageText, ...]:
    if not isinstance(value, list):
        raise OcrError("OCR worker returned malformed execution proof")
    parsed = tuple(_accepted_page_from_worker_payload(page) for page in value)
    pages = tuple(page for page, _receipt in parsed)
    receipts = tuple(receipt for _page, receipt in parsed)
    expected_receipts = tuple(receipt for receipt in proof.page_receipts if receipt.accepted_text)
    if receipts != expected_receipts or tuple(page.page_number for page in pages) != tuple(
        receipt.page_number for receipt in expected_receipts
    ):
        raise OcrError("OCR worker returned malformed execution proof")
    return pages


def validate_partial_ocr_pages(
    *,
    pages: object,
    proof: OcrNoConsensusFileProof,
) -> tuple[OcrPageText, ...]:
    """Cross-bind adapter-retained OCR text to the validated file receipt hashes."""

    if not isinstance(pages, tuple) or any(not isinstance(page, OcrPageText) for page in pages):
        raise OcrError("OCR partial page results are malformed")
    expected_receipts = tuple(receipt for receipt in proof.page_receipts if receipt.accepted_text)
    if tuple(page.page_number for page in pages) != tuple(
        receipt.page_number for receipt in expected_receipts
    ):
        raise OcrError("OCR partial page results are incomplete")
    for page, receipt in zip(pages, expected_receipts, strict=True):
        normalized, tokens = _normalize_ocr_rescue_text(page.text)
        if (
            receipt.accepted_text_token_count != len(tokens)
            or receipt.accepted_text_span_count
            != sum(bool(line.strip()) for line in page.text.split("\n"))
            or receipt.accepted_text_codepoint_count != len(page.text)
            or receipt.normalized_page_text_sha256
            != hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            or (receipt.outcome == "rescue_text") != (page.rescue_provenance is not None)
        ):
            raise OcrError("OCR partial page results do not match execution proof")
    return pages


def _no_consensus_page_content_sha256s_from_worker_payload(
    value: object,
    *,
    proof: OcrNoConsensusFileProof,
) -> tuple[tuple[int, str], ...]:
    if not isinstance(value, list):
        raise OcrError("OCR worker returned malformed page-content proof")
    expected_page_numbers = tuple(
        receipt.page_number for receipt in proof.page_receipts if receipt.outcome == "no_consensus"
    )
    parsed: list[tuple[int, str]] = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {
            "page_number",
            "page_content_sha256",
        }:
            raise OcrError("OCR worker returned malformed page-content proof")
        page_number = row.get("page_number")
        page_content_sha256 = row.get("page_content_sha256")
        if (
            isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or not _is_lower_sha256(page_content_sha256)
        ):
            raise OcrError("OCR worker returned malformed page-content proof")
        parsed.append((page_number, str(page_content_sha256)))
    if tuple(page_number for page_number, _digest in parsed) != expected_page_numbers:
        raise OcrError("OCR worker returned incomplete page-content proof")
    return tuple(parsed)


def _execution_receipt_for_worker_page(page: dict[str, object]) -> OcrPageExecutionReceipt:
    page_number = _worker_page_number(page)
    receipt = _ocr_page_execution_receipt_from_payload(page.get("execution_receipt"))
    if receipt.page_number != page_number:
        raise OcrError("OCR worker returned malformed execution proof")
    _render_admission_from_worker_page(page)

    if receipt.outcome == "no_consensus":
        page_keys = set(page)
        page_keys.discard("render_admission")
        if (
            page_keys
            not in (
                {"page_number", "status", "execution_receipt"},
                {
                    "page_number",
                    "status",
                    "execution_receipt",
                    "page_content_sha256",
                },
            )
            or page.get("status") != "no_consensus"
        ):
            raise OcrError("OCR worker returned malformed execution proof")
        return receipt

    text = page.get("text")
    if page.get("status") is not None or not isinstance(text, str):
        raise OcrError("OCR worker returned malformed execution proof")
    normalized, tokens = _normalize_ocr_rescue_text(text)
    if (
        receipt.accepted_text_token_count != len(tokens)
        or receipt.accepted_text_span_count != sum(bool(line.strip()) for line in text.split("\n"))
        or receipt.accepted_text_codepoint_count != len(text)
        or receipt.normalized_page_text_sha256
        != hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        or (receipt.outcome == "rescue_text") != ("rescue_provenance" in page)
    ):
        raise OcrError("OCR worker returned malformed execution proof")
    return receipt


def _ocr_pdf_worker_payload(
    pages: list[dict[str, object]],
    *,
    retain_accepted_pages: bool = False,
    allow_sparse_pages: bool = False,
) -> dict[str, object]:
    """Reduce a file with any no-consensus page to exact text-free proof."""

    ordered_pages = sorted(pages, key=_worker_page_number)
    receipts = tuple(_execution_receipt_for_worker_page(page) for page in ordered_pages)
    receipt_page_numbers = tuple(receipt.page_number for receipt in receipts)
    expected_page_numbers = (
        tuple(sorted(set(receipt_page_numbers)))
        if allow_sparse_pages
        else tuple(range(1, len(receipts) + 1))
    )
    if not receipts or receipt_page_numbers != expected_page_numbers:
        raise OcrError("OCR worker returned malformed execution proof")
    if not any(receipt.outcome == "no_consensus" for receipt in receipts):
        return {"status": "success", "pages": ordered_pages}
    proof = _ocr_no_consensus_file_proof(receipts)
    render_admission_payloads = _no_consensus_render_admission_payloads(ordered_pages)
    if retain_accepted_pages:
        accepted_pages = [
            page
            for page, receipt in zip(ordered_pages, receipts, strict=True)
            if receipt.accepted_text
        ]
        no_consensus_page_content_sha256s: list[dict[str, object]] = []
        for page, receipt in zip(ordered_pages, receipts, strict=True):
            if receipt.outcome != "no_consensus":
                continue
            page_content_sha256 = page.get("page_content_sha256")
            if not _is_lower_sha256(page_content_sha256):
                raise OcrError("OCR worker returned malformed page-content proof")
            no_consensus_page_content_sha256s.append(
                {
                    "page_number": receipt.page_number,
                    "page_content_sha256": page_content_sha256,
                }
            )
        payload: dict[str, object] = {
            "status": "partial_no_consensus",
            "proof": proof.to_payload(),
            "accepted_pages": accepted_pages,
            "no_consensus_page_content_sha256s": no_consensus_page_content_sha256s,
        }
        if render_admission_payloads:
            payload["render_admissions"] = render_admission_payloads
        return payload
    payload = {"status": "no_consensus", "proof": proof.to_payload()}
    if render_admission_payloads:
        payload["render_admissions"] = render_admission_payloads
    return payload


def _no_consensus_render_admission_payloads(
    ordered_pages: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Preserve every worker-observed render receipt, or none for a legacy worker."""

    raw_values = [page.get("render_admission") for page in ordered_pages]
    if all(value is None for value in raw_values):
        return []
    if any(value is None for value in raw_values):
        raise OcrError("OCR worker returned incomplete render admissions")
    admissions = tuple(
        _ocr_page_render_admission_from_worker_payload(value) for value in raw_values
    )
    if tuple(admission.page_number for admission in admissions) != tuple(
        _worker_page_number(dict(page)) for page in ordered_pages
    ) or any(
        admission.schema_version == OCR_RENDER_ADMISSION_SCHEMA_VERSION
        and not admission.has_observed_render
        for admission in admissions
    ):
        raise OcrError("OCR worker returned incomplete render admissions")
    return [admission._to_worker_payload() for admission in admissions]


def _tesseract_pdf_file_ocr_worker(
    source_path: str | Path,
    max_pages: int,
    dpi: int,
    language: str,
    deadline: float,
    queue: Any,
    *,
    retain_accepted_pages: bool = False,
    poppler_runtime: GovernedPopplerRuntime | None = None,
) -> None:
    """Render and OCR a caller-owned PDF path under one absolute deadline."""
    mark_governed_worker_child()
    payload: dict[str, object] = {"status": "failed"}
    try:
        with _suppress_output(), _omp_thread_limit_one():
            try:
                from pdf2image import convert_from_path
                from pdf2image.exceptions import (
                    PDFInfoNotInstalledError,
                    PDFPopplerTimeoutError,
                )
                from pytesseract import (
                    Output,
                    TesseractError,
                    TesseractNotFoundError,
                    image_to_data,
                )
            except ImportError:
                payload = {"status": "unavailable"}
            else:
                try:
                    pages = _ocr_pdf_file_batches(
                        source_path=source_path,
                        max_pages=max_pages,
                        dpi=dpi,
                        language=language,
                        deadline=deadline,
                        convert_from_path=convert_from_path,
                        image_to_data=image_to_data,
                        output=Output,
                        compute_page_content_sha256s=retain_accepted_pages,
                        poppler_runtime=poppler_runtime,
                    )
                    payload = _ocr_pdf_worker_payload(
                        pages,
                        retain_accepted_pages=retain_accepted_pages,
                    )
                except PDFInfoNotInstalledError:
                    payload = {"status": "unavailable"}
                except PDFPopplerTimeoutError:
                    payload = {"status": "timeout"}
                except TesseractNotFoundError:
                    payload = {"status": "unavailable"}
                except OcrRescueNoConsensusError:
                    # A proofless escape is malformed; per-page no-consensus is
                    # normally reduced by _ocr_pdf_worker_payload above.
                    payload = {"status": "failed"}
                except TesseractError as exc:
                    payload = {
                        "status": "unavailable" if _is_tesseract_unavailable(exc) else "failed"
                    }
                except OcrTimeoutError:
                    payload = {"status": "timeout"}
                except RuntimeError as exc:
                    payload = {"status": "timeout" if _is_timeout_error(exc) else "failed"}
                except Exception:
                    payload = {"status": "failed"}
    except Exception:
        payload = {"status": "failed"}
    _put_payload(queue, payload)


def _tesseract_pdf_file_ocr_worker_with_partial_results(
    source_path: str | Path,
    max_pages: int,
    dpi: int,
    language: str,
    deadline: float,
    queue: Any,
    *,
    poppler_runtime: GovernedPopplerRuntime | None = None,
) -> None:
    """Production file worker variant used only with verified reviewed admissions."""

    _tesseract_pdf_file_ocr_worker(
        source_path,
        max_pages,
        dpi,
        language,
        deadline,
        queue,
        retain_accepted_pages=True,
        poppler_runtime=poppler_runtime,
    )


def _render_admissions_from_worker_payload(
    value: object,
) -> tuple[OcrPageRenderAdmission, ...]:
    if not isinstance(value, tuple) or not value:
        raise OcrError("OCR render admissions are malformed")
    admissions = tuple(_ocr_page_render_admission_from_worker_payload(row) for row in value)
    page_numbers = tuple(admission.page_number for admission in admissions)
    if page_numbers != tuple(sorted(set(page_numbers))):
        raise OcrError("OCR render admissions are malformed")
    return admissions


def _render_admission_batches(
    admissions: Sequence[OcrPageRenderAdmission],
) -> tuple[tuple[OcrPageRenderAdmission, ...], ...]:
    batches: list[tuple[OcrPageRenderAdmission, ...]] = []
    current: list[OcrPageRenderAdmission] = []
    for admission in admissions:
        if current and (
            len(current) >= _PDF_OCR_RENDER_BATCH_SIZE
            or admission.chosen_dpi != current[-1].chosen_dpi
            or admission.page_number != current[-1].page_number + 1
        ):
            batches.append(tuple(current))
            current = []
        current.append(admission)
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _validate_rendered_image_admission(
    *,
    image: object,
    admission: OcrPageRenderAdmission,
) -> OcrPageRenderAdmission:
    width = getattr(image, "width", None)
    height = getattr(image, "height", None)
    if type(width) is not int or type(height) is not int:
        raise OcrError("OCR renderer violated its page admission")
    return admission.with_observed_render(width=width, height=height)


def _ocr_pdf_file_render_admission_batches(
    *,
    source_path: str | Path,
    render_admissions: Sequence[OcrPageRenderAdmission],
    language: str,
    deadline: float,
    convert_from_path: Any,
    image_to_data: Any,
    output: Any,
    compute_page_content_sha256s: bool,
    poppler_runtime: GovernedPopplerRuntime | None = None,
) -> list[dict[str, object]]:
    """Render only admitted pages, validating each image before Tesseract executes."""

    _remaining_ocr_deadline_seconds(deadline)
    if any(
        admission.schema_version == OCR_RENDER_ADMISSION_SCHEMA_VERSION
        and (
            poppler_runtime is None
            or admission.poppler_version != poppler_runtime.version
            or admission.poppler_runtime_identity_sha256 != poppler_runtime.runtime_identity_sha256
        )
        for admission in render_admissions
    ):
        raise OcrError("OCR Poppler runtime binding drifted from admission")
    poppler_kwargs = _verified_poppler_path_kwargs(poppler_runtime)
    if poppler_runtime is not None:
        install_poppler_child_environment(poppler_runtime, apply_to_process=True)
    executor = ThreadPoolExecutor(max_workers=_PDF_OCR_PAGE_WORKERS)
    executor_closed = False
    pages: list[dict[str, object]] = []
    try:
        for admission_batch in _render_admission_batches(render_admissions):
            first_admission = admission_batch[0]
            last_admission = admission_batch[-1]
            rendered = convert_from_path(
                str(source_path),
                dpi=first_admission.chosen_dpi,
                first_page=first_admission.page_number,
                last_page=last_admission.page_number,
                fmt="png",
                grayscale=True,
                thread_count=1,
                timeout=_remaining_ocr_deadline_seconds(deadline),
                **poppler_kwargs,
            )
            if not isinstance(rendered, list):
                raise OcrError("OCR renderer returned malformed pages")
            images = list(rendered)
            try:
                if len(images) != len(admission_batch):
                    raise OcrError("OCR renderer returned incomplete admitted pages")
                observed_admissions = tuple(
                    _validate_rendered_image_admission(image=image, admission=admission)
                    for image, admission in zip(images, admission_batch, strict=True)
                )
                try:
                    batch_pages = _ocr_pdf_image_batch(
                        images=images,
                        first_page=first_admission.page_number,
                        language=language,
                        deadline=deadline,
                        image_to_data=image_to_data,
                        output=output,
                        executor=executor,
                        compute_page_content_sha256s=compute_page_content_sha256s,
                    )
                except BaseException:
                    executor.shutdown(wait=True, cancel_futures=True)
                    executor_closed = True
                    raise
                admission_by_page = {
                    admission.page_number: admission for admission in observed_admissions
                }
                for page in batch_pages:
                    page_number = _worker_page_number(page)
                    selected_admission = admission_by_page.get(page_number)
                    if selected_admission is None:
                        raise OcrError("OCR worker returned an unadmitted page")
                    page["render_admission"] = selected_admission._to_worker_payload()
                pages.extend(batch_pages)
            finally:
                if not _close_pdf_images(images):
                    raise OcrError("OCR image cleanup failed")
    finally:
        if not executor_closed:
            executor.shutdown(wait=True, cancel_futures=True)

    _remaining_ocr_deadline_seconds(deadline)
    pages.sort(key=_worker_page_number)
    if tuple(_worker_page_number(page) for page in pages) != tuple(
        admission.page_number for admission in render_admissions
    ):
        raise OcrError("OCR worker returned incomplete admitted pages")
    return pages


def _tesseract_pdf_file_ocr_worker_with_render_admissions(
    source_path: str | Path,
    render_admission_payloads: tuple[dict[str, object], ...],
    language: str,
    retain_partial_results: bool,
    deadline: float,
    queue: Any,
    *,
    poppler_runtime: GovernedPopplerRuntime | None = None,
) -> None:
    """Production worker for exact sparse, per-page render admissions."""

    mark_governed_worker_child()
    payload: dict[str, object] = {"status": "failed"}
    try:
        render_admissions = _render_admissions_from_worker_payload(render_admission_payloads)
        if type(retain_partial_results) is not bool:
            raise OcrError("OCR partial-result policy is malformed")
        with _suppress_output(), _omp_thread_limit_one():
            try:
                from pdf2image import convert_from_path
                from pdf2image.exceptions import (
                    PDFInfoNotInstalledError,
                    PDFPopplerTimeoutError,
                )
                from pytesseract import (
                    Output,
                    TesseractError,
                    TesseractNotFoundError,
                    image_to_data,
                )
            except ImportError:
                payload = {"status": "unavailable"}
            else:
                try:
                    pages = _ocr_pdf_file_render_admission_batches(
                        source_path=source_path,
                        render_admissions=render_admissions,
                        language=language,
                        deadline=deadline,
                        convert_from_path=convert_from_path,
                        image_to_data=image_to_data,
                        output=Output,
                        compute_page_content_sha256s=retain_partial_results,
                        poppler_runtime=poppler_runtime,
                    )
                    payload = _ocr_pdf_worker_payload(
                        pages,
                        retain_accepted_pages=retain_partial_results,
                        allow_sparse_pages=True,
                    )
                except PDFInfoNotInstalledError:
                    payload = {"status": "unavailable"}
                except PDFPopplerTimeoutError:
                    payload = {"status": "timeout"}
                except TesseractNotFoundError:
                    payload = {"status": "unavailable"}
                except OcrRescueNoConsensusError:
                    payload = {"status": "failed"}
                except TesseractError as exc:
                    payload = {
                        "status": ("unavailable" if _is_tesseract_unavailable(exc) else "failed")
                    }
                except OcrTimeoutError:
                    payload = {"status": "timeout"}
                except RuntimeError as exc:
                    payload = {"status": "timeout" if _is_timeout_error(exc) else "failed"}
                except Exception:
                    payload = {"status": "failed"}
    except Exception:
        payload = {"status": "failed"}
    _put_payload(queue, payload)


def _ocr_pdf_file_batches(
    *,
    source_path: str | Path,
    max_pages: int,
    dpi: int,
    language: str,
    deadline: float,
    convert_from_path: Any,
    image_to_data: Any,
    output: Any,
    compute_page_content_sha256s: bool = False,
    poppler_runtime: GovernedPopplerRuntime | None = None,
) -> list[dict[str, object]]:
    """OCR ascending render batches while retaining at most eight page images."""
    _remaining_ocr_deadline_seconds(deadline)
    poppler_kwargs = _verified_poppler_path_kwargs(poppler_runtime)
    if poppler_runtime is not None:
        install_poppler_child_environment(poppler_runtime, apply_to_process=True)
    executor = ThreadPoolExecutor(max_workers=_PDF_OCR_PAGE_WORKERS)
    executor_closed = False
    pages: list[dict[str, object]] = []
    first_page = 1
    try:
        while first_page <= max_pages:
            last_page = min(max_pages, first_page + _PDF_OCR_RENDER_BATCH_SIZE - 1)
            rendered = convert_from_path(
                str(source_path),
                dpi=dpi,
                first_page=first_page,
                last_page=last_page,
                fmt="png",
                grayscale=True,
                thread_count=1,
                timeout=_remaining_ocr_deadline_seconds(deadline),
                **poppler_kwargs,
            )
            if not isinstance(rendered, list):
                raise OcrError("OCR renderer returned malformed pages")
            images = list(rendered)
            requested_count = last_page - first_page + 1
            try:
                if len(images) > requested_count or len(images) > _PDF_OCR_RENDER_BATCH_SIZE:
                    raise OcrError("OCR renderer exceeded its page batch")
                if not images:
                    if not pages:
                        raise OcrError("OCR renderer returned no pages")
                    break
                try:
                    batch_pages = _ocr_pdf_image_batch(
                        images=images,
                        first_page=first_page,
                        language=language,
                        deadline=deadline,
                        image_to_data=image_to_data,
                        output=output,
                        executor=executor,
                        compute_page_content_sha256s=compute_page_content_sha256s,
                    )
                except BaseException:
                    executor.shutdown(wait=True, cancel_futures=True)
                    executor_closed = True
                    raise
                pages.extend(batch_pages)
            finally:
                if not _close_pdf_images(images):
                    raise OcrError("OCR image cleanup failed")

            if len(images) < requested_count:
                break
            first_page = last_page + 1
    finally:
        if not executor_closed:
            executor.shutdown(wait=True, cancel_futures=True)

    _remaining_ocr_deadline_seconds(deadline)
    pages.sort(key=_worker_page_number)
    return pages


def _ocr_pdf_image_batch(
    *,
    images: list[Any],
    first_page: int,
    language: str,
    deadline: float,
    image_to_data: Any,
    output: Any,
    executor: ThreadPoolExecutor,
    compute_page_content_sha256s: bool = False,
) -> list[dict[str, object]]:
    futures: dict[Future[dict[str, object]], int] = {}
    page_content_sha256s: dict[int, str] = {}
    for offset, image in enumerate(images):
        page_number = first_page + offset
        if compute_page_content_sha256s:
            page_content_sha256s[page_number] = canonical_image_only_page_content_sha256(image)
        future = executor.submit(
            _ocr_pdf_page_from_data,
            image=image,
            page_number=page_number,
            language=language,
            deadline=deadline,
            image_to_data=image_to_data,
            output=output,
        )
        futures[future] = page_number

    results: list[dict[str, object]] = []
    try:
        for future in as_completed(
            futures,
            timeout=_remaining_ocr_deadline_seconds(deadline),
        ):
            try:
                results.append(future.result())
            except OcrRescueNoConsensusError as exc:
                page_number = futures[future]
                proof = exc.page_proof
                if proof is None or proof.page_number != page_number:
                    raise OcrError("OCR worker returned malformed execution proof") from exc
                results.append(
                    {
                        "page_number": page_number,
                        "status": "no_consensus",
                        "execution_receipt": proof.to_payload(),
                    }
                )
    except FutureTimeoutError as exc:
        raise OcrTimeoutError("OCR timed out") from exc
    if len(results) != len(images):
        raise OcrTimeoutError("OCR timed out")
    for result in results:
        if compute_page_content_sha256s and result.get("status") == "no_consensus":
            page_number = _worker_page_number(result)
            result["page_content_sha256"] = page_content_sha256s[page_number]
    results.sort(key=_worker_page_number)
    return results


def _normalized_word_confidences(values: Sequence[str]) -> tuple[float, ...]:
    normalized: list[float] = []
    for value in values:
        try:
            numeric = float(value) / 100.0
        except (TypeError, ValueError) as exc:
            raise OcrError("OCR rescue candidate confidence is malformed") from exc
        if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise OcrError("OCR rescue candidate confidence is malformed")
        normalized.append(numeric)
    return tuple(normalized)


def _ocr_rescue_transformed_image(
    image: Any,
    *,
    rotation_degrees: int,
    transform_id: str,
) -> Any:
    from PIL import Image, ImageOps

    grayscale = image.convert("L")
    try:
        if rotation_degrees == 0:
            rotated = grayscale.copy()
        else:
            transpose = {
                90: Image.Transpose.ROTATE_90,
                180: Image.Transpose.ROTATE_180,
                270: Image.Transpose.ROTATE_270,
            }.get(rotation_degrees)
            if transpose is None:
                raise OcrError("OCR rescue rotation is malformed")
            rotated = grayscale.transpose(transpose)
    finally:
        if grayscale is not image:
            grayscale.close()

    try:
        if transform_id == "autocontrast-c0":
            return ImageOps.autocontrast(rotated, cutoff=0)
        threshold = {
            "binary-192": 192,
            "binary-224": 224,
        }.get(transform_id)
        if threshold is None:
            raise OcrError("OCR rescue transform is malformed")
        return rotated.point(lambda pixel: 0 if pixel < threshold else 255)
    finally:
        rotated.close()


def _ocr_rescue_candidate_receipt_row(candidate: OcrRescueCandidate) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "confidence_histogram_counts": asdict(candidate.confidence_histogram_counts),
        "confidence_values_sha256": candidate.confidence_values_sha256,
        "fraction_at_or_above_0_50": candidate.fraction_at_or_above_0_50,
        "matrix_ordinal": candidate.matrix_ordinal,
        "maximum_confidence": candidate.maximum_confidence,
        "mean_confidence": candidate.mean_confidence,
        "median_confidence": candidate.median_confidence,
        "minimum_confidence": candidate.minimum_confidence,
        "normalized_text_sha256": candidate.normalized_text_sha256,
        "psm": candidate.psm,
        "rejection_reasons": list(candidate.rejection_reasons),
        "rotation_degrees": candidate.rotation_degrees,
        "state": candidate.state,
        "transform_id": candidate.transform_id,
        "word_count": candidate.word_count,
    }


def _ocr_rescue_pairwise_agreements(
    candidates: Sequence[OcrRescueCandidate],
) -> dict[tuple[int, int], float]:
    values: dict[tuple[int, int], float] = {}
    for left, right in combinations(candidates, 2):
        values[(left.matrix_ordinal, right.matrix_ordinal)] = _ocr_rescue_agreement(
            left.text,
            right.text,
        )
    return values


def _ocr_rescue_candidate_receipt_sha256(
    candidates: Sequence[OcrRescueCandidate],
    pairwise_agreements: Mapping[tuple[int, int], float],
) -> str:
    agreement_rows = [
        {
            "agreement": float(_round_ocr_rescue_agreement(value)),
            "left_matrix_ordinal": key[0],
            "right_matrix_ordinal": key[1],
        }
        for key, value in sorted(pairwise_agreements.items())
    ]
    return _canonical_json_sha256(
        {
            "candidates": [
                _ocr_rescue_candidate_receipt_row(candidate) for candidate in candidates
            ],
            "pairwise_agreements": agreement_rows,
            "policy_sha256": OCR_RESCUE_POLICY_SHA256,
            "policy_version": OCR_RESCUE_POLICY_VERSION,
        }
    )


def _ocr_rescue_provenance(
    *,
    consensus: OcrRescueConsensus,
    candidate_receipt_sha256: str,
) -> OcrRescueProvenance:
    winner = consensus.winner
    return OcrRescueProvenance(
        policy_version=OCR_RESCUE_POLICY_VERSION,
        policy_sha256=OCR_RESCUE_POLICY_SHA256,
        winner_rotation_degrees=winner.rotation_degrees,
        winner_transform_id=winner.transform_id,
        winner_psm=winner.psm,
        winner_word_count=winner.word_count,
        winner_mean_confidence=round(winner.mean_confidence, 4),
        winner_median_confidence=round(winner.median_confidence, 4),
        winner_minimum_confidence=round(winner.minimum_confidence, 4),
        winner_maximum_confidence=round(winner.maximum_confidence, 4),
        winner_confidence_histogram_counts=winner.confidence_histogram_counts,
        winner_confidence_values_sha256=winner.confidence_values_sha256,
        winner_fraction_at_or_above_0_50=round(winner.fraction_at_or_above_0_50, 4),
        attempted_candidate_count=len(OCR_RESCUE_MATRIX),
        supporting_candidate_count=len(consensus.supporting_candidates),
        minimum_clique_agreement=float(
            _round_ocr_rescue_agreement(consensus.minimum_clique_agreement)
        ),
        mean_clique_agreement=float(_round_ocr_rescue_agreement(consensus.mean_clique_agreement)),
        candidate_receipt_sha256=candidate_receipt_sha256,
        normalized_page_text_sha256=winner.normalized_text_sha256,
    )


def _accepted_page_execution_receipt(
    *,
    page_number: int,
    outcome: str,
    text: str,
    primary_token_count: int,
    candidate_receipt_sha256: str | None,
) -> OcrPageExecutionReceipt:
    normalized, tokens = _normalize_ocr_rescue_text(text)
    receipt = OcrPageExecutionReceipt(
        schema_version=OCR_PAGE_EXECUTION_RECEIPT_SCHEMA_VERSION,
        page_number=page_number,
        outcome=outcome,
        primary_completed=True,
        primary_token_count=primary_token_count,
        rescue_attempted_candidate_count=(
            len(OCR_RESCUE_MATRIX) if outcome == "rescue_text" else 0
        ),
        rescue_completed_candidate_count=(
            len(OCR_RESCUE_MATRIX) if outcome == "rescue_text" else 0
        ),
        accepted_text=True,
        accepted_text_token_count=len(tokens),
        accepted_text_span_count=sum(bool(line.strip()) for line in text.split("\n")),
        accepted_text_codepoint_count=len(text),
        normalized_page_text_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        candidate_receipt_sha256=candidate_receipt_sha256,
    )
    return _ocr_page_execution_receipt_from_payload(receipt.to_payload())


def _no_consensus_page_execution_receipt(
    *,
    page_number: int,
    candidate_receipt_sha256: str,
) -> OcrPageExecutionReceipt:
    receipt = OcrPageExecutionReceipt(
        schema_version=OCR_PAGE_EXECUTION_RECEIPT_SCHEMA_VERSION,
        page_number=page_number,
        outcome="no_consensus",
        primary_completed=True,
        primary_token_count=0,
        rescue_attempted_candidate_count=len(OCR_RESCUE_MATRIX),
        rescue_completed_candidate_count=len(OCR_RESCUE_MATRIX),
        accepted_text=False,
        accepted_text_token_count=0,
        accepted_text_span_count=0,
        accepted_text_codepoint_count=0,
        normalized_page_text_sha256=_EMPTY_NORMALIZED_OCR_TEXT_SHA256,
        candidate_receipt_sha256=candidate_receipt_sha256,
    )
    return _ocr_page_execution_receipt_from_payload(receipt.to_payload())


def _ocr_pdf_page_rescue_from_data(
    *,
    image: Any,
    page_number: int,
    language: str,
    deadline: float,
    image_to_data: Any,
    output: Any,
) -> dict[str, object]:
    candidates: list[OcrRescueCandidate] = []
    for matrix_ordinal, (rotation_degrees, transform_id, psm) in enumerate(OCR_RESCUE_MATRIX):
        _remaining_ocr_deadline_seconds(deadline)
        with _ocr_rescue_transformed_image(
            image,
            rotation_degrees=rotation_degrees,
            transform_id=transform_id,
        ) as candidate_image:
            data = image_to_data(
                candidate_image,
                lang=language,
                config=f"--oem 1 --psm {psm}",
                timeout=_remaining_ocr_deadline_seconds(deadline),
                output_type=output.DICT,
            )
        _remaining_ocr_deadline_seconds(deadline)
        text, raw_confidences = _text_and_confidences_from_tesseract_data(data)
        candidates.append(
            _make_ocr_rescue_candidate(
                matrix_ordinal=matrix_ordinal,
                rotation_degrees=rotation_degrees,
                transform_id=transform_id,
                psm=psm,
                text=text,
                word_confidences=_normalized_word_confidences(raw_confidences),
            )
        )

    pairwise_agreements = _ocr_rescue_pairwise_agreements(candidates)
    receipt_sha256 = _ocr_rescue_candidate_receipt_sha256(
        candidates,
        pairwise_agreements,
    )
    try:
        consensus = _select_ocr_rescue_consensus(
            candidates,
            pairwise_agreements=pairwise_agreements,
        )
    except OcrRescueNoConsensusError as exc:
        raise OcrRescueNoConsensusError(
            "OCR rescue produced no unique consensus",
            page_proof=_no_consensus_page_execution_receipt(
                page_number=page_number,
                candidate_receipt_sha256=receipt_sha256,
            ),
        ) from exc
    provenance = _ocr_rescue_provenance(
        consensus=consensus,
        candidate_receipt_sha256=receipt_sha256,
    )
    winner = consensus.winner
    return {
        "page_number": page_number,
        "text": winner.text,
        "confidences": [str(value * 100.0) for value in winner.word_confidences],
        "rescue_provenance": asdict(provenance),
        "execution_receipt": _accepted_page_execution_receipt(
            page_number=page_number,
            outcome="rescue_text",
            text=winner.text,
            primary_token_count=0,
            candidate_receipt_sha256=receipt_sha256,
        ).to_payload(),
    }


def _ocr_pdf_page_from_data(
    *,
    image: Any,
    page_number: int,
    language: str,
    deadline: float,
    image_to_data: Any,
    output: Any,
) -> dict[str, object]:
    data = image_to_data(
        image,
        lang=language,
        timeout=_remaining_ocr_deadline_seconds(deadline),
        output_type=output.DICT,
    )
    _remaining_ocr_deadline_seconds(deadline)
    text, confidences = _text_and_confidences_from_tesseract_data(data)
    _normalized, normalized_tokens = _normalize_ocr_rescue_text(text)
    if not normalized_tokens:
        return _ocr_pdf_page_rescue_from_data(
            image=image,
            page_number=page_number,
            language=language,
            deadline=deadline,
            image_to_data=image_to_data,
            output=output,
        )
    return {
        "page_number": page_number,
        "text": text,
        "confidences": confidences,
        "execution_receipt": _accepted_page_execution_receipt(
            page_number=page_number,
            outcome="primary_text",
            text=text,
            primary_token_count=len(normalized_tokens),
            candidate_receipt_sha256=None,
        ).to_payload(),
    }


def _text_and_confidences_from_tesseract_data(data: object) -> tuple[str, list[str]]:
    if not isinstance(data, dict):
        raise OcrError("OCR page data is malformed")
    required_fields = ("text", "conf", "block_num", "par_num", "line_num", "word_num")
    columns: dict[str, list[Any] | tuple[Any, ...]] = {}
    for field in required_fields:
        values = data.get(field)
        if not isinstance(values, (list, tuple)):
            raise OcrError("OCR page data is malformed")
        columns[field] = values
    row_count = len(columns["text"])
    if any(len(columns[field]) != row_count for field in required_fields):
        raise OcrError("OCR page data is malformed")

    line_words: dict[tuple[int, int, int], list[tuple[int, str]]] = {}
    occupied_word_coordinates: set[tuple[int, int, int, int]] = set()
    confidences: list[str] = []
    for index in range(row_count):
        raw_text = columns["text"][index]
        if not isinstance(raw_text, str):
            raise OcrError("OCR page data is malformed")
        try:
            confidence = float(columns["conf"][index])
            coordinate = (
                int(columns["block_num"][index]),
                int(columns["par_num"][index]),
                int(columns["line_num"][index]),
                int(columns["word_num"][index]),
            )
        except (TypeError, ValueError) as exc:
            raise OcrError("OCR page data is malformed") from exc
        if not math.isfinite(confidence) or confidence < -1.0 or confidence > 100.0:
            raise OcrError("OCR page data is malformed")
        if any(value < 0 for value in coordinate):
            raise OcrError("OCR page data is malformed")
        word = " ".join(raw_text.split())
        if word:
            if any(value <= 0 for value in coordinate):
                raise OcrError("OCR word coordinate is malformed")
            if coordinate in occupied_word_coordinates:
                raise OcrError("OCR word coordinate is duplicated")
            occupied_word_coordinates.add(coordinate)
            line_words.setdefault(coordinate[:3], []).append((coordinate[3], word))
            confidences.append(str(columns["conf"][index]))

    lines = [
        " ".join(word for _word_number, word in sorted(line_words[key]))
        for key in sorted(line_words)
    ]
    return "\n".join(lines), confidences


def _worker_page_number(page: dict[str, object]) -> int:
    value = page.get("page_number")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OcrError("OCR worker returned an invalid page number")
    return value


def _remaining_ocr_deadline_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise OcrTimeoutError("OCR timed out")
    return remaining


def _close_pdf_images(images: list[Any]) -> bool:
    closed = True
    for image in images:
        try:
            image.close()
        except Exception:
            closed = False
    return closed


@contextlib.contextmanager
def _omp_thread_limit_one() -> Iterator[None]:
    previous = os.environ.get("OMP_THREAD_LIMIT")
    os.environ["OMP_THREAD_LIMIT"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("OMP_THREAD_LIMIT", None)
        else:
            os.environ["OMP_THREAD_LIMIT"] = previous


def _tesseract_ocr_worker(
    data: bytes,
    max_pages: int,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Any,
    *,
    poppler_runtime: GovernedPopplerRuntime | None = None,
) -> None:
    mark_governed_worker_child()
    deadline = time.monotonic() + timeout_seconds
    try:
        poppler_kwargs = _verified_poppler_path_kwargs(poppler_runtime)
        if poppler_runtime is not None:
            install_poppler_child_environment(poppler_runtime, apply_to_process=True)
        with _suppress_output():
            try:
                from pdf2image import convert_from_bytes
                from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPopplerTimeoutError
                from pytesseract import (
                    Output,
                    TesseractError,
                    TesseractNotFoundError,
                    image_to_data,
                    image_to_string,
                )
            except ImportError:
                _put_payload(queue, {"status": "unavailable"})
                return

            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _put_payload(queue, {"status": "timeout"})
                    return
                render_pdf: Any = convert_from_bytes
                images = render_pdf(
                    data,
                    dpi=dpi,
                    first_page=1,
                    last_page=max_pages,
                    fmt="png",
                    grayscale=True,
                    thread_count=1,
                    timeout=max(1, int(remaining)),
                    **poppler_kwargs,
                )
            except PDFInfoNotInstalledError:
                _put_payload(queue, {"status": "unavailable"})
                return
            except PDFPopplerTimeoutError:
                _put_payload(queue, {"status": "timeout"})
                return

            pages: list[dict[str, object]] = []
            for page_number, image in enumerate(images, start=1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _put_payload(queue, {"status": "timeout"})
                    return
                try:
                    text = image_to_string(image, lang=language, timeout=remaining)
                except TesseractNotFoundError:
                    _put_payload(queue, {"status": "unavailable"})
                    return
                except TesseractError as exc:
                    if _is_tesseract_unavailable(exc):
                        _put_payload(queue, {"status": "unavailable"})
                    else:
                        _put_payload(queue, {"status": "failed"})
                    return
                except RuntimeError as exc:
                    if _is_timeout_error(exc):
                        _put_payload(queue, {"status": "timeout"})
                    else:
                        _put_payload(queue, {"status": "failed"})
                    return
                confidences = _word_confidences(
                    image_to_data, Output, image, language, deadline - time.monotonic()
                )
                pages.append({"page_number": page_number, "text": text, "confidences": confidences})
            _put_payload(queue, {"status": "success", "pages": pages})
    except Exception:
        _put_payload(queue, {"status": "failed"})


def _tesseract_image_ocr_worker(
    data: bytes,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del dpi
    try:
        with _suppress_output():
            try:
                from io import BytesIO

                from PIL import Image
                from pytesseract import (
                    Output,
                    TesseractError,
                    TesseractNotFoundError,
                    image_to_data,
                    image_to_string,
                )
            except ImportError:
                _put_payload(queue, {"status": "unavailable"})
                return

            try:
                with Image.open(BytesIO(data)) as image:
                    if not _image_within_resource_bounds(
                        width=image.size[0],
                        height=image.size[1],
                        frame_count=int(getattr(image, "n_frames", 1)),
                    ):
                        _put_payload(queue, {"status": "failed"})
                        return
                    rgb_image = image.convert("RGB")
                    text = image_to_string(
                        rgb_image,
                        lang=language,
                        timeout=timeout_seconds,
                    )
                    confidences = _word_confidences(
                        image_to_data, Output, rgb_image, language, timeout_seconds
                    )
            except TesseractNotFoundError:
                _put_payload(queue, {"status": "unavailable"})
                return
            except TesseractError as exc:
                if _is_tesseract_unavailable(exc):
                    _put_payload(queue, {"status": "unavailable"})
                else:
                    _put_payload(queue, {"status": "failed"})
                return
            except RuntimeError as exc:
                if _is_timeout_error(exc):
                    _put_payload(queue, {"status": "timeout"})
                else:
                    _put_payload(queue, {"status": "failed"})
                return

            _put_payload(
                queue,
                {
                    "status": "success",
                    "pages": [{"page_number": 1, "text": text, "confidences": confidences}],
                },
            )
    except Exception:
        _put_payload(queue, {"status": "failed"})


def _put_payload(queue: Any, payload: dict[str, object]) -> None:
    queue.put(payload)


def _word_confidences(
    image_to_data: Any,
    output: Any,
    image: Any,
    language: str,
    timeout: float,
) -> list[str]:
    """Best-effort per-word OCR confidences; never raises, returns [] on any failure.

    Confidence is purely additive diagnostics — text extraction via image_to_string is
    unchanged. Failures here (timeout, missing data) degrade to no confidence, not an
    OCR failure.
    """
    if timeout <= 0:
        return []
    try:
        data = image_to_data(
            image,
            lang=language,
            timeout=max(1, int(timeout)),
            output_type=output.DICT,
        )
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    values = data.get("conf", [])
    if not isinstance(values, (list, tuple)):
        return []
    return [str(value) for value in values]


def _image_within_resource_bounds(*, width: int, height: int, frame_count: int) -> bool:
    if width < 1 or height < 1 or frame_count != 1:
        return False
    return width * height <= MAX_OCR_IMAGE_PIXELS


def _terminate_process_tree(process: mp.Process, *, timeout_seconds: float) -> None:
    pid = process.pid
    if pid is None:
        process.terminate()
        return
    try:
        import psutil

        root = psutil.Process(pid)
        children = root.children(recursive=True)
        for child in children:
            child.terminate()
        _gone, alive = psutil.wait_procs(
            children,
            timeout=max(0.0, timeout_seconds),
        )
        for child in alive:
            child.kill()
    except Exception:
        process.terminate()
        return
    process.terminate()


def _is_timeout_error(exc: RuntimeError) -> bool:
    return "timeout" in str(exc).lower()


def _is_tesseract_unavailable(exc: Exception) -> bool:
    lowered = str(exc).lower()
    unavailable_tokens = (
        "error opening data file",
        "failed loading language",
        "could not initialize tesseract",
    )
    return any(token in lowered for token in unavailable_tokens)


def _suppress_output() -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def suppress() -> Iterator[None]:
        with (
            open(os.devnull, "w", encoding="utf-8") as devnull,
            contextlib.redirect_stdout(devnull),
            contextlib.redirect_stderr(devnull),
        ):
            yield

    return suppress()
