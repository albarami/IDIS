"""Fail-closed terminal ingestion reconciliation for Slice 100.

This boundary consumes only safe source aliases/digests and durable ingestion
receipts assembled by an authenticated caller.  It never accepts a package or
an upload receipt as proof of parsing: every source row must have one distinct
durable artifact/document binding and at least one matching durable span.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import uuid
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Never

from pypdf import PdfReader

from idis.evaluation.real_example_source_package import (
    CompanySourceContract,
    SourceFileContract,
    TwoCompanySourceContract,
)
from idis.parsers.image_only_page import (
    IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION,
    project_safe_image_only_page_metadata,
    project_safe_image_only_page_receipts,
)
from idis.parsers.media import (
    FASTER_WHISPER_ADAPTER_CONTRACT_VERSION,
    FASTER_WHISPER_CPU_THREADS,
    FASTER_WHISPER_NUM_WORKERS,
    FASTER_WHISPER_RUNTIME_PROVENANCE_SCHEMA_VERSION,
)
from idis.parsers.ocr import (
    LEGACY_OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    MAX_OCR_IMAGE_PIXELS,
    OCR_DPI_LADDER,
    OCR_DPI_LADDER_VERSION,
    OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    PINNED_POPPLER_VERSION,
    POPPLER_EXACT_COMPARISON_MODE,
    POPPLER_EXPECTED_DIMENSION_POLICY_ID,
    POPPLER_EXPECTED_DIMENSION_POLICY_SHA256,
    OcrError,
    OcrPageRenderAdmission,
    ocr_page_execution_receipt_sha256,
    validate_ocr_page_execution_receipt_payload,
)
from idis.parsers.pdf import build_pdf_ocr_render_admissions
from idis.parsers.watermark_page_classifier import (
    WATERMARK_PAGE_CLASSIFICATION_V2_FIELDS,
    WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION,
    WATERMARK_RUNTIME_CANDIDATE_RENDER_POLICY_ID,
    WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN,
    default_watermark_runtime_candidate_render_policy,
)
from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    NO_SUBSTANTIVE_CONTENT_RECEIPT_FIELDS,
    NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION,
    NO_SUBSTANTIVE_CONTENT_VARIANTS,
    DocumentNoSubstantiveContentAttestationError,
    validate_document_no_substantive_content_receipt_v2,
)
from idis.services.ingestion.service import STREAMING_CONTRACT_VERSION

TERMINAL_INGESTION_RECONCILIATION_SCHEMA_VERSION = "slice100-terminal-ingestion-reconciliation-v5"
TERMINAL_INGESTION_PRIOR_RECONCILIATION_SCHEMA_VERSION = (
    "slice100-terminal-ingestion-reconciliation-v4"
)
OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION = "ocr-terminal-evidence-v5"
OCR_TERMINAL_PRIOR_EVIDENCE_SCHEMA_VERSION = "ocr-terminal-evidence-v4"
OCR_TERMINAL_PREVIOUS_EVIDENCE_SCHEMA_VERSION = "ocr-terminal-evidence-v3"
OCR_TERMINAL_LEGACY_EVIDENCE_SCHEMA_VERSION = "ocr-terminal-evidence-v2"
PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION = "pdf-page-disposition-census-v1"
DURABLE_OBJECT_INTEGRITY_SCHEMA_VERSION = "durable-object-integrity-v1"
DATA_ROOM_RECONCILIATION_INCOMPLETE = "DATA_ROOM_RECONCILIATION_INCOMPLETE"
INGEST_ADMISSION_MAX_SIZE_EXCEEDED = "INGEST_ADMISSION_MAX_SIZE_EXCEEDED"
MEDIA_NO_EXTRACTABLE_EVIDENCE = "MEDIA_NO_EXTRACTABLE_EVIDENCE"
WATERMARK_PAGE_SANITY_CAP_EXCEEDED = "WATERMARK_PAGE_SANITY_CAP_EXCEEDED"
NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION = "document-no-substantive-content-receipt-v2"
WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION = "watermark-page-classification-v1"
WATERMARK_MANIFEST_SCHEMA_VERSION = "document-no-substantive-content-attestation-manifest-v2"
WATERMARK_CLASSIFIER_POLICY_ID = "exemplar-watermark-page-v1"
WATERMARK_CLASSIFIER_SANITY_CAP_PERCENT = 15

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,12}$")
_NON_MEDIA_SPAN_TYPES = frozenset({"PAGE_TEXT", "PARAGRAPH", "CELL"})
_MAX_SIZE_REASON_CODES = frozenset({"file_too_large", "max_size_exceeded"})
PDF_ENCRYPTION_DISPOSITIONS = (
    "not_encryption_marked",
    "empty_credential_openable",
    "configured_credential_openable",
    "credential_required",
    "credential_invalid",
)
_PDF_ENCRYPTION_DISPOSITION_SET = frozenset(PDF_ENCRYPTION_DISPOSITIONS)
_COMPLETION_REASONS_BY_EXTENSION = {
    "pdf": frozenset({"pdf_parse_completed", "pdf_ocr_completed"}),
    "xlsx": frozenset({"xlsx_parse_completed"}),
    "docx": frozenset({"docx_parse_completed"}),
    "pptx": frozenset({"pptx_parse_completed"}),
    "html": frozenset({"text_parse_completed"}),
    "txt": frozenset({"text_parse_completed"}),
    "png": frozenset({"image_ocr_completed"}),
    "mp4": frozenset({"media_evidence_completed"}),
}
_PARSED_DOCUMENT_TYPE_BY_EXTENSION = {
    "pdf": "PDF",
    "xlsx": "XLSX",
    "docx": "DOCX",
    "pptx": "PPTX",
    "html": "HTML",
    "txt": "TEXT",
    "png": "IMAGE",
    "mp4": "VIDEO",
}
MEDIA_RUNTIME_PROVENANCE_KEY = "media_runtime_provenance"
MEDIA_STT_RECEIPT_KEY = "media_stt_receipt"
MEDIA_FRAME_OCR_RECEIPT_KEY = "media_frame_ocr_receipt"
MEDIA_STT_RECEIPT_SCHEMA_VERSION = "media-stt-receipt-v1"
MEDIA_FRAME_OCR_RECEIPT_SCHEMA_VERSION = "media-frame-ocr-receipt-v1"
MEDIA_FRAME_SAMPLING_POLICY = "periodic-plus-scenes-v1"
MEDIA_FRAME_SCENE_THRESHOLD = 0.20
MEDIA_FRAME_MAX_SAMPLE_COUNT = 600
MEDIA_FRAME_MAX_WIDTH = 1_920
MEDIA_FRAME_MAX_HEIGHT = 1_080
PDF_OCR_REQUIREMENTS_SCHEMA_VERSION = "slice100-pdf-ocr-requirements-v1"
DEFAULT_PDF_FILE_COUNT = 223
DEFAULT_PDF_PAGE_COUNT = 2_595
DEFAULT_PDF_OCR_REQUIRED_FILE_COUNT = 42
DEFAULT_PDF_OCR_REQUIRED_PAGE_COUNT = 284
DEFAULT_PDF_ALL_NATIVE_FILE_COUNT = 181
DEFAULT_PDF_ALL_OCR_FILE_COUNT = 35
DEFAULT_PDF_MIXED_FILE_COUNT = 7
DEFAULT_PDF_MAX_REQUIRED_PAGE_INDEX = 72
DEFAULT_PDF_MAX_REQUIRED_PAGES_PER_FILE = 72
_SOURCE_READ_CHUNK_BYTES = 1024 * 1024
_MEDIA_RUNTIME_PROVENANCE_KEYS = frozenset(
    {
        "schema_version",
        "adapter",
        "adapter_contract_version",
        "runtime_package_version",
        "model_source",
        "model_files_sha256",
        "cpu_threads",
        "num_workers",
        "config_sha256",
    }
)
_SAFE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}$")
_SAFE_BACKEND_RE = re.compile(r"^[a-z0-9][a-z0-9._:+\-]{0,127}$")
_NON_PERSISTENT_BACKEND_MARKERS = (
    "fake",
    "inmemory",
    "mo" + "ck",
    "stub",
    "synthetic",
    "tempfile",
    "temporary",
)
_OCR_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "ocr_page_count",
        "ocr_span_count",
        "confidence_count",
        "mean_confidence",
        "minimum_confidence",
        "maximum_confidence",
        "locator_contract_sha256",
        "ocr_rescue_page_count",
        "ocr_rescue_span_count",
        "ocr_rescue_receipt_sha256",
    }
)
_OCR_EVIDENCE_PAGE_RESOLUTION_KEYS = frozenset(
    {
        *_OCR_EVIDENCE_KEYS,
        "ocr_page_numbers",
        "preclassified_page_numbers",
        "ocr_required_page_count",
        "ocr_render_admissions",
        "ocr_render_admissions_sha256",
    }
)
_OCR_EVIDENCE_PAGE_CENSUS_KEYS = frozenset(
    {
        *_OCR_EVIDENCE_PAGE_RESOLUTION_KEYS,
        "pdf_page_census",
    }
)
_PDF_PAGE_DISPOSITION_CLASSES = (
    "native_text",
    "ocr_text",
    "image_only_page",
    "watermark_only",
)
_PDF_PAGE_CENSUS_KEYS = frozenset(
    {
        "schema_version",
        "page_count",
        "page_dispositions",
        "native_text_page_numbers",
        "ocr_text_page_numbers",
        "image_only_page_numbers",
        "watermark_only_page_numbers",
        "class_counts",
        "census_sha256",
    }
)
_OCR_RENDER_ADMISSION_V1_KEYS = frozenset(
    {
        "schema_version",
        "ladder_version",
        "configured_dpi",
        "chosen_dpi",
        "projected_pixel_count",
        "max_pixel_count",
    }
)
_OCR_RENDER_PLAN_V2_KEYS = frozenset(
    {
        "schema_version",
        "ladder_version",
        "configured_dpi",
        "chosen_dpi",
        "projected_width",
        "projected_height",
        "projected_pixel_count",
        "expected_render_width",
        "expected_render_height",
        "expected_render_pixel_count",
        "poppler_version",
        "poppler_runtime_identity_sha256",
        "expected_dimension_policy_id",
        "expected_dimension_policy_sha256",
        "comparison_mode",
        "tolerance_pixels",
        "tolerance_used",
        "max_pixel_count",
    }
)
_OCR_RENDER_ADMISSION_V2_KEYS = frozenset(
    {
        *_OCR_RENDER_PLAN_V2_KEYS,
        "actual_render_width",
        "actual_render_height",
        "actual_render_pixel_count",
        "width_delta_pixels",
        "height_delta_pixels",
    }
)
_OCR_RENDER_ADMISSION_KEYS = frozenset({"page", *_OCR_RENDER_ADMISSION_V1_KEYS})
_OCR_RENDER_ADMISSION_V2_PAGE_KEYS = frozenset({"page", *_OCR_RENDER_ADMISSION_V2_KEYS})
_OCR_RENDER_PLAN_V2_PAGE_KEYS = frozenset({"page", *_OCR_RENDER_PLAN_V2_KEYS})
_PINNED_POPPLER_RUNTIME_IDENTITY_SHA256 = (
    "8ebfb07d9a8e5e01c12cc2994837a122cc6c94a930c657e10ffc655db8f0cc72"
)
_DOCUMENT_NO_SUBSTANTIVE_CONTENT_RECEIPT_KEYS = NO_SUBSTANTIVE_CONTENT_RECEIPT_FIELDS
_DOCUMENT_NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_KEYS = frozenset(
    {
        "schema_version",
        "disposition",
        "variant",
        "safe_alias",
        "content_sha256",
        "source_tree_fingerprint",
        "manifest_schema_version",
        "manifest_sha256",
        "batch_envelope_sha256",
        "candidate_set_sha256",
        "classifier_policy_id",
        "classifier_policy_sha256",
        "exemplar_envelope_sha256",
        "exemplar_canonical_bitmap_sha256",
        "public_key_sha256",
        "signature_verified",
        "page_count",
        "classified_page_count",
        "substantive_span_count",
        "whole_document_terminal",
        "watermark_page_classifications",
        "watermark_page_classifications_sha256",
    }
)
_WATERMARK_PAGE_CLASSIFICATION_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "content_sha256",
        "page_number",
        "policy_id",
        "policy_sha256",
        "exemplar_reference",
        "transform_chain",
        "transform_bounds",
        "confidence_distribution",
        "similarity_metric",
        "similarity_score",
        "similarity_numerator",
        "similarity_denominator",
        "canonical_render_sha256",
        "ocr_terminal_proof_sha256",
        "batch_membership",
        "confirmation_source",
        "post_run_review_required",
        "review_status",
    }
)
_WATERMARK_PAGE_CLASSIFICATION_V2_KEYS = frozenset(
    {
        *WATERMARK_PAGE_CLASSIFICATION_V2_FIELDS,
        "batch_membership",
        "confirmation_source",
        "post_run_review_required",
        "review_status",
        "batch_candidate_canonical_render_sha256",
        "batch_candidate_similarity_score",
    }
)
_WATERMARK_EXEMPLAR_REFERENCE_KEYS = frozenset(
    {
        "exemplar_id",
        "content_sha256",
        "page_number",
        "attestation_envelope_sha256",
        "canonical_bitmap_sha256",
        "classifier_policy_sha256",
    }
)
_WATERMARK_TRANSFORM_BOUNDS = {
    "near_white_fraction_min": "0.970000",
    "near_white_fraction_max": "0.985000",
    "foreground_ink_fraction_min": "0.015000",
    "foreground_ink_fraction_max": "0.030000",
    "similarity_min": "0.800000",
}
_WATERMARK_TRANSFORM_CHAIN = [
    "poppler-200dpi",
    "convert-grayscale-L",
    "measure-near-white-ge-245",
    "autocontrast-cutoff-0",
    "measure-foreground-lt-224",
    "resize-grayscale-128x128-LANCZOS",
    "binary-foreground-lt-224",
    "foreground-dice",
]
_WATERMARK_CONFIDENCE_DISTRIBUTION_KEYS = frozenset(
    {"near_white_fraction", "foreground_ink_fraction"}
)
_FIXED_SIX_DECIMAL_RE = re.compile(r"^(?:0|1)\.[0-9]{6}$")
_MEDIA_STT_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "segment_count",
        "evidence_span_count",
        "evidence_sha256",
        "runtime_provenance_sha256",
    }
)
_MEDIA_FRAME_OCR_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "sampling_policy",
        "scene_threshold",
        "max_sample_count",
        "max_frame_width",
        "max_frame_height",
        "duration_ms",
        "scheduled_sample_count",
        "completed_sample_count",
        "nonempty_sample_count",
        "evidence_span_count",
        "schedule_sha256",
        "evidence_sha256",
        "confidence_count",
        "mean_confidence",
        "minimum_confidence",
        "maximum_confidence",
    }
)


class TerminalIngestionReconciliationError(Exception):
    """Safe terminal-reconciliation failure with a stable public code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True, repr=False)
class PdfOcrRequirementsCensus:
    """Private-name-free binding of PDF OCR requirements to content hashes."""

    required_content_sha256s: frozenset[str]
    pdf_file_count: int
    total_page_count: int
    required_page_count: int
    all_native_file_count: int
    all_ocr_file_count: int
    mixed_file_count: int
    max_required_page_index: int
    max_required_pages_per_file: int
    required_pages_by_content_sha256: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    page_count_by_content_sha256: Mapping[str, int] = field(default_factory=dict)
    render_admissions_by_content_sha256: Mapping[str, tuple[Mapping[str, object], ...]] = field(
        default_factory=dict
    )

    def safe_summary(self) -> dict[str, Any]:
        return {
            "schema_version": PDF_OCR_REQUIREMENTS_SCHEMA_VERSION,
            "pdf_file_count": self.pdf_file_count,
            "total_page_count": self.total_page_count,
            "required_file_count": len(self.required_content_sha256s),
            "required_page_count": self.required_page_count,
            "all_native_file_count": self.all_native_file_count,
            "all_ocr_file_count": self.all_ocr_file_count,
            "mixed_file_count": self.mixed_file_count,
            "max_required_page_index": self.max_required_page_index,
            "max_required_pages_per_file": self.max_required_pages_per_file,
            "required_contract_sha256": _canonical_sequence_sha256(
                sorted(self.required_content_sha256s)
            ),
        }


def inspect_pdf_ocr_requirements(
    contract: TwoCompanySourceContract,
    *,
    expected_pdf_files: int = DEFAULT_PDF_FILE_COUNT,
    expected_total_pages: int = DEFAULT_PDF_PAGE_COUNT,
    expected_required_files: int = DEFAULT_PDF_OCR_REQUIRED_FILE_COUNT,
    expected_required_pages: int = DEFAULT_PDF_OCR_REQUIRED_PAGE_COUNT,
    expected_all_native_files: int = DEFAULT_PDF_ALL_NATIVE_FILE_COUNT,
    expected_all_ocr_files: int = DEFAULT_PDF_ALL_OCR_FILE_COUNT,
    expected_mixed_files: int = DEFAULT_PDF_MIXED_FILE_COUNT,
    expected_max_required_page_index: int = DEFAULT_PDF_MAX_REQUIRED_PAGE_INDEX,
    expected_max_required_pages_per_file: int = DEFAULT_PDF_MAX_REQUIRED_PAGES_PER_FILE,
) -> PdfOcrRequirementsCensus:
    """Audit the approved PDFs read-only and bind every OCR need to its digest.

    Source paths and extracted text remain private.  The returned contract contains
    only aggregate counts and already-approved content hashes, so it is safe to use
    as terminal reconciliation input.
    """
    expected_values = (
        expected_pdf_files,
        expected_total_pages,
        expected_required_files,
        expected_required_pages,
        expected_all_native_files,
        expected_all_ocr_files,
        expected_mixed_files,
        expected_max_required_page_index,
        expected_max_required_pages_per_file,
    )
    if (
        not isinstance(contract, TwoCompanySourceContract)
        or any(not _is_nonnegative_int(value) for value in expected_values)
        or expected_pdf_files <= 0
        or expected_total_pages <= 0
        or expected_required_files != expected_all_ocr_files + expected_mixed_files
        or expected_pdf_files
        != expected_all_native_files + expected_all_ocr_files + expected_mixed_files
        or expected_max_required_page_index <= 0
        or expected_max_required_pages_per_file <= 0
        or expected_max_required_page_index > expected_total_pages
        or expected_max_required_pages_per_file > expected_required_pages
    ):
        _fail("The expected PDF OCR census contract is invalid")

    source_by_key, _, source_formats = _source_index(contract.companies)
    if (
        source_formats.get("pdf", 0) != expected_pdf_files
        or contract.total_file_count != len(source_by_key)
        or contract.total_bytes != sum(source.size_bytes for source in source_by_key.values())
    ):
        _fail("The approved source inventory does not match its PDF OCR census")

    required_hashes: set[str] = set()
    required_pages_by_hash: dict[str, tuple[int, ...]] = {}
    page_count_by_hash: dict[str, int] = {}
    render_admissions_by_hash: dict[str, tuple[Mapping[str, object], ...]] = {}
    total_pages = 0
    required_pages = 0
    all_native_files = 0
    all_ocr_files = 0
    mixed_files = 0
    max_required_page_index = 0
    max_required_pages_per_file = 0
    for (company_alias, sequence), source in source_by_key.items():
        if source.extension != "pdf":
            continue
        data = _read_verified_source_bytes(
            contract,
            company_alias=company_alias,
            sequence=sequence,
            source=source,
        )
        page_count, file_required_page_numbers = _inspect_pdf_page_requirements(data)
        if source.content_sha256 in page_count_by_hash:
            _fail("A PDF content digest is duplicated in the approved source inventory")
        page_count_by_hash[source.content_sha256] = page_count
        file_required_pages = len(file_required_page_numbers)
        file_max_required_page_index = max(file_required_page_numbers, default=0)
        total_pages += page_count
        required_pages += file_required_pages
        max_required_page_index = max(
            max_required_page_index,
            file_max_required_page_index,
        )
        max_required_pages_per_file = max(
            max_required_pages_per_file,
            file_required_pages,
        )
        if file_required_pages == 0:
            all_native_files += 1
        else:
            if file_required_pages == page_count:
                all_ocr_files += 1
            else:
                mixed_files += 1
            required_hashes.add(source.content_sha256)
            required_pages_by_hash[source.content_sha256] = file_required_page_numbers
            try:
                render_admissions = build_pdf_ocr_render_admissions(
                    data=data,
                    page_numbers=file_required_page_numbers,
                )
            except OcrError:
                _fail("A source PDF page cannot satisfy the governed OCR render ladder")
            render_admissions_by_hash[source.content_sha256] = tuple(
                MappingProxyType(_source_render_plan(admission)) for admission in render_admissions
            )

    census = PdfOcrRequirementsCensus(
        required_content_sha256s=frozenset(required_hashes),
        pdf_file_count=expected_pdf_files,
        total_page_count=total_pages,
        required_page_count=required_pages,
        all_native_file_count=all_native_files,
        all_ocr_file_count=all_ocr_files,
        mixed_file_count=mixed_files,
        max_required_page_index=max_required_page_index,
        max_required_pages_per_file=max_required_pages_per_file,
        required_pages_by_content_sha256=MappingProxyType(dict(required_pages_by_hash)),
        page_count_by_content_sha256=MappingProxyType(dict(page_count_by_hash)),
        render_admissions_by_content_sha256=MappingProxyType(dict(render_admissions_by_hash)),
    )
    if (
        total_pages != expected_total_pages
        or len(required_hashes) != expected_required_files
        or required_pages != expected_required_pages
        or all_native_files != expected_all_native_files
        or all_ocr_files != expected_all_ocr_files
        or mixed_files != expected_mixed_files
        or max_required_page_index != expected_max_required_page_index
        or max_required_pages_per_file != expected_max_required_pages_per_file
    ):
        _fail("The read-only PDF OCR census has drifted from its approved contract")
    return census


def _read_verified_source_bytes(
    contract: TwoCompanySourceContract,
    *,
    company_alias: str,
    sequence: int,
    source: SourceFileContract,
) -> bytes:
    try:
        root = contract.root.resolve(strict=True)
        path = contract.source_path(company_alias, sequence)
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not resolved.is_relative_to(root) or not resolved.is_file():
            _fail("A PDF source entry escaped its approved read-only boundary")
        before = resolved.stat()
        digest = hashlib.sha256()
        payload = bytearray()
        with resolved.open("rb") as stream:
            while chunk := stream.read(_SOURCE_READ_CHUNK_BYTES):
                digest.update(chunk)
                payload.extend(chunk)
        after = resolved.stat()
    except TerminalIngestionReconciliationError:
        raise
    except (OSError, RuntimeError, ValueError):
        _fail("A PDF source entry became unavailable during read-only inspection")
    if (
        before.st_size != source.size_bytes
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or digest.hexdigest() != source.content_sha256
    ):
        _fail("A PDF source entry changed during read-only inspection")
    return bytes(payload)


def _inspect_pdf_page_requirements(data: bytes) -> tuple[int, tuple[int, ...]]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not bool(reader.decrypt("")):
            _fail("A PDF unexpectedly requires a configured credential")
        page_count = len(reader.pages)
        if page_count <= 0:
            _fail("A PDF contains no inspectable pages")
        required_page_numbers: list[int] = []
        for page_index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                required_page_numbers.append(page_index)
    except TerminalIngestionReconciliationError:
        raise
    except Exception:
        _fail("A PDF could not be inspected for OCR requirements")
    return page_count, tuple(required_page_numbers)


def _inspect_pdf_bytes(data: bytes) -> tuple[int, int, int]:
    """Preserve the aggregate-only private helper contract for existing callers."""
    page_count, required_page_numbers = _inspect_pdf_page_requirements(data)
    return (
        page_count,
        len(required_page_numbers),
        max(required_page_numbers, default=0),
    )


def collect_durable_terminal_rows(
    *,
    api_client: Any,
    api_key: str,
    source_companies: Sequence[CompanySourceContract],
    preparation_summary: Mapping[str, Any],
    large_file_threshold_bytes: int,
    expected_pdf_page_counts_by_content_sha256: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Collect safe terminal receipts solely through authenticated durable APIs.

    Private excerpts and non-media locators are deliberately reduced to hashes.
    Package, document, and span reads are re-bound to the approved source contract;
    a preparation ledger or upload response is never treated as terminal proof.
    """
    if not api_key or not _is_positive_int(large_file_threshold_bytes):
        _fail("The durable terminal evidence collector is misconfigured")
    if (
        not isinstance(preparation_summary, Mapping)
        or preparation_summary.get("schema_version") != "slice100-source-preparation-summary-v1"
        or preparation_summary.get("total_file_count")
        != sum(company.file_count for company in source_companies)
    ):
        _fail("The source preparation summary is invalid")
    source_pdf_hashes = {
        source.content_sha256
        for company in source_companies
        for source in company.files
        if source.extension == "pdf"
    }
    normalized_pdf_page_counts: dict[str, int] | None = None
    if expected_pdf_page_counts_by_content_sha256 is not None:
        if (
            not isinstance(expected_pdf_page_counts_by_content_sha256, Mapping)
            or set(expected_pdf_page_counts_by_content_sha256) != source_pdf_hashes
            or any(
                not isinstance(content_sha256, str)
                or _SHA256_RE.fullmatch(content_sha256) is None
                or type(page_count) is not int
                or page_count < 1
                for content_sha256, page_count in (
                    expected_pdf_page_counts_by_content_sha256.items()
                )
            )
        ):
            _fail("The source-bound PDF page-count census is incomplete")
        normalized_pdf_page_counts = dict(expected_pdf_page_counts_by_content_sha256)
    raw_states = preparation_summary.get("companies")
    if not isinstance(raw_states, list) or len(raw_states) != len(source_companies):
        _fail("The source preparation company census is invalid")
    states_by_alias: dict[str, Mapping[str, Any]] = {}
    for raw_state in raw_states:
        if not isinstance(raw_state, Mapping):
            _fail("A source preparation company row is malformed")
        alias = _required_string(raw_state, "company_alias")
        if alias in states_by_alias:
            _fail("A source preparation company row is duplicated")
        states_by_alias[alias] = raw_state

    headers = {"X-IDIS-API-Key": api_key}
    terminal_rows: list[dict[str, Any]] = []
    seen_deals: set[str] = set()
    seen_packages: set[str] = set()
    for company in source_companies:
        state = states_by_alias.get(company.company_alias)
        if state is None or any(
            state.get(key) != value for key, value in company.safe_header().items()
        ):
            _fail("A source preparation company binding has drifted")
        deal_id = _required_uuid(state, "deal_id")
        package_id = _required_uuid(state, "package_id")
        _admit_unique(seen_deals, deal_id, "deal")
        _admit_unique(seen_packages, package_id, "package")
        package = _authenticated_json_get(
            api_client,
            f"/v1/deals/{deal_id}/data-room-packages/{package_id}",
            headers=headers,
        )
        if (
            _required_uuid(package, "deal_id") != deal_id
            or _required_uuid(package, "package_id") != package_id
        ):
            _fail("A durable package crossed its preparation boundary")
        raw_files = package.get("files")
        if not isinstance(raw_files, list) or len(raw_files) != len(company.files):
            _fail("A durable package file census is incomplete")
        files_by_sequence: dict[int, Mapping[str, Any]] = {}
        for raw_file in raw_files:
            if not isinstance(raw_file, Mapping):
                _fail("A durable package file row is malformed")
            sequence = _required_nonnegative_int(raw_file, "sequence")
            if sequence in files_by_sequence:
                _fail("A durable package source sequence is duplicated")
            files_by_sequence[sequence] = raw_file

        for source in company.files:
            package_file = files_by_sequence.get(source.sequence)
            if package_file is None or (
                package_file.get("sha256") != source.content_sha256
                or package_file.get("extension") != source.extension
            ):
                _fail("A durable package file has drifted from its source binding")
            document_id = _required_uuid(package_file, "document_id")
            artifact_id = _required_uuid(package_file, "doc_id")
            document = _authenticated_json_get(
                api_client,
                f"/v1/deals/{deal_id}/documents/{document_id}",
                headers=headers,
            )
            if (
                _required_uuid(document, "deal_id") != deal_id
                or _required_uuid(document, "document_id") != document_id
                or _required_uuid(document, "doc_id") != artifact_id
                or _required_sha256(document, "sha256") != source.content_sha256
            ):
                _fail("A durable document has drifted from its package binding")
            parsed_doc_type = _required_string(document, "parsed_doc_type")
            if parsed_doc_type != _PARSED_DOCUMENT_TYPE_BY_EXTENSION.get(source.extension):
                _fail("A durable parsed-document type drifted from its source format")
            metadata = document.get("metadata")
            source_metadata = document.get("source_metadata")
            if not isinstance(metadata, Mapping) or not isinstance(source_metadata, Mapping):
                _fail("A durable document omitted parser or source metadata")
            if (
                source_metadata.get("upload_filename") != source.generated_upload_name
                or source_metadata.get("size_bytes") != source.size_bytes
                or source_metadata.get("upload_intake") != "single_document_upload"
                or source_metadata.get("streaming_contract_version") != STREAMING_CONTRACT_VERSION
                or source_metadata.get("streaming_size_verified") is not True
                or source_metadata.get("streaming_sha256_verified") is not True
            ):
                _fail("A durable document source binding has drifted")

            raw_uri = _required_string(document, "uri")
            object_integrity = _safe_durable_object_integrity_receipt(
                _authenticated_json_get(
                    api_client,
                    f"/v1/documents/{artifact_id}/content-integrity",
                    headers=headers,
                ),
                artifact_id=artifact_id,
                content_sha256=source.content_sha256,
                size_bytes=source.size_bytes,
                raw_uri=raw_uri,
            )

            parse_status = _required_string(document, "parse_status")
            support_status = _required_string(metadata, "parser_support_status")
            triage_status = _required_string(metadata, "parser_triage_status")
            warning_codes = _required_string_list(metadata, "parse_warning_codes")
            error_codes = _required_string_list(metadata, "parse_error_codes")
            parser_reason_codes = _required_string_list(metadata, "parser_reason_codes")
            raw_no_substantive_content_receipt = metadata.get(
                "document_no_substantive_content_receipt"
            )
            has_no_substantive_content_receipt = raw_no_substantive_content_receipt is not None
            whole_document_terminal = _receipt_claims_whole_document_terminal(
                raw_no_substantive_content_receipt
            )
            if has_no_substantive_content_receipt:
                receipt_error_codes = _receipt_terminal_error_codes(
                    raw_no_substantive_content_receipt
                )
                receipt_row = (
                    {
                        "terminal_status": "CONFIRMED_EXCLUSION",
                        "file_status": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
                        "support_status": "scanned_or_image_only",
                        "triage_status": "excluded",
                        "parse_status": parse_status,
                        "reason_codes": [DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED],
                        "warning_codes": [],
                        "error_codes": receipt_error_codes,
                    }
                    if whole_document_terminal
                    else {
                        "terminal_status": "SUCCEEDED",
                        "file_status": "SUPPORTED",
                        "support_status": support_status,
                        "triage_status": triage_status,
                        "parse_status": parse_status,
                        "reason_codes": parser_reason_codes,
                        "warning_codes": warning_codes,
                        "error_codes": error_codes,
                    }
                )
                confirmed_no_substantive_content_receipt = _confirmed_exclusion_receipt(
                    {
                        **receipt_row,
                        "document_no_substantive_content_receipt": (
                            raw_no_substantive_content_receipt
                        ),
                    },
                    generated_upload_name=source.generated_upload_name,
                    content_sha256=source.content_sha256,
                    extension=source.extension,
                )
                if confirmed_no_substantive_content_receipt is None:
                    _fail("A confirmed-no-substantive-content API row omitted its receipt")
            else:
                confirmed_no_substantive_content_receipt = None

            ocr_performed = metadata.get("ocr_performed") is True
            require_pdf_page_census = source.extension == "pdf" and (
                normalized_pdf_page_counts is not None
                or _has_current_image_only_page_receipt(metadata)
            )
            span_page = _authenticated_json_get(
                api_client,
                f"/v1/documents/{artifact_id}/spans",
                headers=headers,
            )
            durable_spans, ocr_locator_proof = _safe_durable_span_receipts(
                span_page,
                document_id=document_id,
                is_media=source.extension == "mp4",
                extension=source.extension,
                ocr_performed=ocr_performed,
                allow_empty_confirmed=whole_document_terminal,
                require_pdf_page_census=require_pdf_page_census,
            )
            if source.extension == "pdf" and require_pdf_page_census:
                expected_page_count = (
                    normalized_pdf_page_counts.get(source.content_sha256)
                    if normalized_pdf_page_counts is not None
                    else None
                )
                ocr_evidence = _safe_pdf_ocr_evidence_v5(
                    metadata,
                    content_sha256=source.content_sha256,
                    parse_status=parse_status,
                    locator_proof=ocr_locator_proof,
                    watermark_receipt=confirmed_no_substantive_content_receipt,
                    expected_page_count=expected_page_count,
                    whole_document_terminal=whole_document_terminal,
                )
            elif (
                ocr_performed
                and confirmed_no_substantive_content_receipt is not None
                and whole_document_terminal
                and not _preclassified_page_numbers(confirmed_no_substantive_content_receipt)
            ):
                ocr_evidence = _safe_confirmed_ocr_evidence_from_receipt(
                    confirmed_no_substantive_content_receipt
                )
            elif ocr_performed or _preclassified_page_numbers(
                confirmed_no_substantive_content_receipt
            ):
                ocr_evidence = _safe_ocr_evidence(
                    metadata,
                    extension=source.extension,
                    locator_proof=ocr_locator_proof,
                    watermark_receipt=confirmed_no_substantive_content_receipt,
                )
            else:
                ocr_evidence = None
            media_performed = metadata.get("media_transcription_performed") is True
            media_frame_ocr_performed = metadata.get("media_frame_ocr_performed") is True
            media_runtime_provenance: dict[str, object] | None = None
            media_receipts: tuple[dict[str, Any], dict[str, Any]] | None = None
            if source.extension == "mp4":
                media_runtime_provenance = _safe_media_runtime_provenance(
                    metadata.get(MEDIA_RUNTIME_PROVENANCE_KEY)
                )
                if not media_performed or not media_frame_ocr_performed:
                    _fail("A media document omitted one required execution proof")
                media_receipts = _validate_media_dual_track_receipts(
                    stt_receipt=metadata.get(MEDIA_STT_RECEIPT_KEY),
                    frame_receipt=metadata.get(MEDIA_FRAME_OCR_RECEIPT_KEY),
                    spans=durable_spans,
                    runtime_provenance=media_runtime_provenance,
                )[:2]
            is_success = (
                parse_status == "PARSED"
                and support_status == "supported"
                and triage_status == "ready"
                and not warning_codes
                and not error_codes
                and _has_exact_completion_reason(source.extension, parser_reason_codes)
                and bool(durable_spans)
            )
            if confirmed_no_substantive_content_receipt is not None and whole_document_terminal:
                terminal_status = "CONFIRMED_EXCLUSION"
                file_status = DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
                support_status = "scanned_or_image_only"
                triage_status = "excluded"
                parser_reason_codes = [DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED]
                warning_codes = []
                error_codes = _receipt_terminal_error_codes(
                    confirmed_no_substantive_content_receipt
                )
            else:
                terminal_status = (
                    "SUCCEEDED"
                    if is_success
                    else "FAILED"
                    if parse_status != "PARSED"
                    else "PARTIAL"
                )
                file_status = "SUPPORTED" if is_success else support_status.upper()
            terminal_rows.append(
                {
                    "company_alias": company.company_alias,
                    "deal_id": deal_id,
                    "source_sequence": source.sequence,
                    "generated_upload_name": source.generated_upload_name,
                    "document_id": document_id,
                    "doc_id": artifact_id,
                    "content_sha256": source.content_sha256,
                    "size_bytes": source.size_bytes,
                    "extension": source.extension,
                    "parsed_doc_type": parsed_doc_type,
                    "terminal_status": terminal_status,
                    "file_status": file_status,
                    "support_status": support_status,
                    "triage_status": triage_status,
                    "parse_status": parse_status,
                    "reason_codes": parser_reason_codes,
                    "warning_codes": warning_codes,
                    "error_codes": error_codes,
                    **(
                        {
                            "document_no_substantive_content_receipt": (
                                confirmed_no_substantive_content_receipt
                            )
                        }
                        if confirmed_no_substantive_content_receipt is not None
                        else {}
                    ),
                    "ocr_performed": ocr_performed,
                    **({"ocr_evidence": ocr_evidence} if ocr_evidence is not None else {}),
                    "media_transcription_performed": (media_performed),
                    "media_frame_ocr_performed": media_frame_ocr_performed,
                    "bounded_streaming_verified": (source.size_bytes > large_file_threshold_bytes),
                    "durable_object_integrity": object_integrity,
                    **(
                        {"pdf_encryption_disposition": metadata.get("pdf_encryption_disposition")}
                        if source.extension == "pdf"
                        else {}
                    ),
                    **(
                        {
                            MEDIA_RUNTIME_PROVENANCE_KEY: media_runtime_provenance,
                            MEDIA_STT_RECEIPT_KEY: media_receipts[0],
                            MEDIA_FRAME_OCR_RECEIPT_KEY: media_receipts[1],
                        }
                        if source.extension == "mp4" and media_receipts is not None
                        else {}
                    ),
                    "durable_spans": durable_spans,
                }
            )
    return terminal_rows


def _safe_durable_object_integrity_receipt(
    proof: Mapping[str, Any],
    *,
    artifact_id: str,
    content_sha256: str,
    size_bytes: int,
    raw_uri: str,
) -> dict[str, Any]:
    """Reduce an authenticated bounded object verification to safe evidence."""
    expected_keys = {
        "schema_version",
        "doc_id",
        "content_sha256",
        "size_bytes",
        "uri_sha256",
        "version_id",
        "backend",
        "streaming_verified",
    }
    if set(proof) != expected_keys:
        _fail("A durable object integrity proof is malformed")
    expected_uri_sha256 = hashlib.sha256(raw_uri.encode("utf-8")).hexdigest()
    if (
        proof.get("schema_version") != DURABLE_OBJECT_INTEGRITY_SCHEMA_VERSION
        or _required_uuid(proof, "doc_id") != artifact_id
        or _required_sha256(proof, "content_sha256") != content_sha256
        or _required_nonnegative_int(proof, "size_bytes") != size_bytes
        or _required_sha256(proof, "uri_sha256") != expected_uri_sha256
        or proof.get("streaming_verified") is not True
    ):
        _fail("A durable object integrity proof drifted from its source binding")
    version_id = _required_uuid(proof, "version_id")
    backend = _required_persistent_backend(proof, "backend")
    return {
        "schema_version": DURABLE_OBJECT_INTEGRITY_SCHEMA_VERSION,
        "content_sha256": content_sha256,
        "size_bytes": size_bytes,
        "uri_sha256": expected_uri_sha256,
        "version_id": version_id,
        "backend": backend,
        "streaming_verified": True,
    }


def _has_current_image_only_page_receipt(metadata: Mapping[str, Any]) -> bool:
    receipts = metadata.get("image_only_page_receipts")
    return (
        isinstance(receipts, list)
        and bool(receipts)
        and all(
            isinstance(receipt, Mapping)
            and receipt.get("schema_version") == "image-only-page-receipt-v2"
            for receipt in receipts
        )
    )


def _authenticated_json_get(
    api_client: Any,
    url: str,
    *,
    headers: Mapping[str, str],
) -> Mapping[str, Any]:
    try:
        response = api_client.get(url, headers=dict(headers))
        if response.status_code != 200:
            _fail("An authenticated durable evidence read failed")
        payload = response.json()
    except TerminalIngestionReconciliationError:
        raise
    except Exception:
        _fail("An authenticated durable evidence read failed")
    if not isinstance(payload, Mapping):
        _fail("An authenticated durable evidence response is malformed")
    return payload


def _safe_durable_span_receipts(
    span_page: Mapping[str, Any],
    *,
    document_id: str,
    is_media: bool,
    extension: str,
    ocr_performed: bool,
    allow_empty_confirmed: bool = False,
    require_pdf_page_census: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    raw_items = span_page.get("items")
    total = span_page.get("total")
    if not isinstance(raw_items, list) or type(total) is not int or total != len(raw_items):
        _fail("A durable document has no complete span page")
    if not raw_items:
        if allow_empty_confirmed and total == 0:
            if extension == "pdf" and require_pdf_page_census:
                return [], _empty_pdf_locator_proof()
            return [], None
        _fail("A durable document has no complete span page")

    def total_span_order(raw_span: object) -> tuple[int, int, str]:
        if not isinstance(raw_span, Mapping):
            _fail("A durable span response is malformed")
        span_id = _required_uuid(raw_span, "span_id")
        locator = raw_span.get("locator")
        start_ms = (
            _strict_int(locator.get("start_ms"))
            if raw_span.get("span_type") == "TIMECODE" and isinstance(locator, Mapping)
            else None
        )
        return (0, start_ms, span_id) if start_ms is not None else (1, 0, span_id)

    ordered_items = sorted(raw_items, key=total_span_order)
    receipts: list[dict[str, Any]] = []
    ocr_locator_rows: list[dict[str, Any]] = []
    ocr_page_numbers: set[int] = set()
    native_page_numbers: set[int] = set()
    page_evidence_rows: dict[int, list[dict[str, str]]] = {}
    ocr_render_admissions_by_page: dict[int, dict[str, object]] = {}
    ocr_render_admission_span_count = 0
    ocr_rescue_receipts_by_page: dict[int, dict[str, Any]] = {}
    ocr_rescue_span_count = 0
    seen_page_lines: set[tuple[int, int]] = set()
    for raw_span in ordered_items:
        if not isinstance(raw_span, Mapping):
            _fail("A durable span response is malformed")
        if _required_uuid(raw_span, "document_id") != document_id:
            _fail("A durable span crossed its document boundary")
        text_excerpt = raw_span.get("text_excerpt")
        if not isinstance(text_excerpt, str) or not text_excerpt.strip():
            _fail("A durable span omitted its evidence text")
        content_hash = _required_sha256(raw_span, "content_hash")
        if hashlib.sha256(text_excerpt.encode("utf-8")).hexdigest() != content_hash:
            _fail("A durable span content hash does not match its evidence text")
        span_type = _required_string(raw_span, "span_type")
        locator = raw_span.get("locator")
        if not isinstance(locator, Mapping) or not locator:
            _fail("A durable span omitted its locator")
        if is_media:
            start_ms = _strict_int(locator.get("start_ms"))
            end_ms = _strict_int(locator.get("end_ms"))
            source = locator.get("source")
            if (
                start_ms is None
                or end_ms is None
                or source
                not in {
                    "media_transcript",
                    "video_frame_ocr",
                }
            ):
                _fail("A durable media span omitted its timecode")
            if source == "media_transcript":
                if set(locator) != {"source", "start_ms", "end_ms"}:
                    _fail("A durable transcript locator has an invalid shape")
                safe_locator = {
                    "source": source,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                }
            else:
                if set(locator) != {
                    "source",
                    "sample_index",
                    "start_ms",
                    "end_ms",
                    "confidence",
                }:
                    _fail("A durable frame-OCR locator has an invalid shape")
                sample_index = _strict_int(locator.get("sample_index"))
                confidence = _optional_confidence(locator.get("confidence"))
                if sample_index is None or sample_index < 0 or confidence is None:
                    _fail("A durable frame-OCR locator is invalid")
                safe_locator = {
                    "source": source,
                    "sample_index": sample_index,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "confidence": confidence,
                }
        else:
            locator_row: dict[str, Any] | None = None
            is_ocr_locator = False
            if extension == "pdf" and require_pdf_page_census or ocr_performed:
                locator_row, is_ocr_locator = _validated_raw_ocr_locator(
                    locator,
                    extension=extension,
                    span_type=span_type,
                )
                page_line = (locator_row["page"], locator_row["line"])
                if page_line in seen_page_lines:
                    _fail("An OCR document contains a duplicated page-line locator")
                seen_page_lines.add(page_line)
                if is_ocr_locator:
                    ocr_locator_rows.append(locator_row)
                    ocr_page_numbers.add(locator_row["page"])
                    render_admission = locator_row.get("ocr_render_admission")
                    if isinstance(render_admission, Mapping):
                        ocr_render_admission_span_count += 1
                        page_bound_admission = {
                            "page": locator_row["page"],
                            **dict(render_admission),
                        }
                        existing_admission = ocr_render_admissions_by_page.get(locator_row["page"])
                        if (
                            existing_admission is not None
                            and existing_admission != page_bound_admission
                        ):
                            _fail("A PDF OCR page contains inconsistent render admissions")
                        ocr_render_admissions_by_page[locator_row["page"]] = page_bound_admission
                    if locator_row["source"] == "ocr_rescue":
                        ocr_rescue_span_count += 1
                        rescue_receipt = {
                            key: value
                            for key, value in locator_row.items()
                            if key not in {"line", "source", "ocr_render_admission"}
                        }
                        existing_receipt = ocr_rescue_receipts_by_page.get(locator_row["page"])
                        if existing_receipt is not None and existing_receipt != rescue_receipt:
                            _fail("An OCR rescue page contains inconsistent provenance")
                        ocr_rescue_receipts_by_page[locator_row["page"]] = rescue_receipt
                else:
                    native_page_numbers.add(locator_row["page"])
            try:
                locator_json = json.dumps(
                    dict(locator),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
            except (TypeError, ValueError):
                _fail("A durable span locator is malformed")
            safe_locator = {
                "locator_sha256": hashlib.sha256(locator_json.encode("utf-8")).hexdigest()
            }
            if locator_row is not None:
                page_evidence_rows.setdefault(locator_row["page"], []).append(
                    {
                        "span_type": span_type,
                        "content_hash": content_hash,
                        "locator_sha256": safe_locator["locator_sha256"],
                    }
                )
        receipts.append(
            {
                "span_id": _required_uuid(raw_span, "span_id"),
                "document_id": document_id,
                "span_type": span_type,
                "locator": safe_locator,
                "content_hash": content_hash,
            }
        )
    if extension == "pdf" and require_pdf_page_census and native_page_numbers & ocr_page_numbers:
        _fail("A PDF page claims both native and OCR text")
    if not ocr_performed and not (extension == "pdf" and require_pdf_page_census):
        return receipts, None
    if extension == "png":
        if len(ocr_locator_rows) != len(receipts) or ocr_page_numbers != {1}:
            _fail("An image OCR locator census is incomplete")
    elif extension == "pdf":
        pass
    else:
        _fail("An OCR locator proof is bound to an unexpected source format")
    rescue_receipts = [
        ocr_rescue_receipts_by_page[page] for page in sorted(ocr_rescue_receipts_by_page)
    ]
    render_admissions = [
        ocr_render_admissions_by_page[page] for page in sorted(ocr_render_admissions_by_page)
    ]
    proof = {
        "durable_span_count": len(receipts),
        "ocr_page_count": len(ocr_page_numbers),
        "ocr_page_numbers": sorted(ocr_page_numbers),
        "native_page_numbers": sorted(native_page_numbers),
        "page_evidence_bindings": {
            str(page): _canonical_sequence_of_mappings_sha256(page_evidence_rows[page])
            for page in sorted(page_evidence_rows)
        },
        "ocr_span_count": len(ocr_locator_rows),
        "locator_contract_sha256": _canonical_sequence_of_mappings_sha256(ocr_locator_rows),
        "ocr_render_admissions": render_admissions,
        "ocr_render_admission_span_count": ocr_render_admission_span_count,
        "ocr_render_admissions_sha256": _canonical_sequence_of_mappings_sha256(render_admissions),
        "ocr_rescue_page_count": len(rescue_receipts),
        "ocr_rescue_span_count": ocr_rescue_span_count,
        "ocr_rescue_receipt_sha256": _canonical_sequence_of_mappings_sha256(rescue_receipts),
    }
    return receipts, proof


def _empty_pdf_locator_proof() -> dict[str, Any]:
    empty_digest = _canonical_sequence_of_mappings_sha256([])
    return {
        "durable_span_count": 0,
        "ocr_page_count": 0,
        "ocr_page_numbers": [],
        "native_page_numbers": [],
        "page_evidence_bindings": {},
        "ocr_span_count": 0,
        "locator_contract_sha256": empty_digest,
        "ocr_render_admissions": [],
        "ocr_render_admission_span_count": 0,
        "ocr_render_admissions_sha256": empty_digest,
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": empty_digest,
    }


def _validated_raw_ocr_locator(
    locator: Mapping[str, Any],
    *,
    extension: str,
    span_type: str,
) -> tuple[dict[str, Any], bool]:
    """Validate private raw OCR locators before reducing them to a digest."""
    if span_type != "PAGE_TEXT":
        _fail("An OCR document contains a non-page-text durable span")
    page = _strict_int(locator.get("page"))
    line = _strict_int(locator.get("line"))
    if page is None or line is None or page <= 0 or line <= 0:
        _fail("An OCR document contains an invalid page-line locator")
    if extension == "png":
        if set(locator) != {"page", "line", "source"} or locator.get("source") != "ocr_image":
            _fail("An image OCR locator has an invalid shape")
        return {"page": page, "line": line, "source": "ocr_image"}, True
    if extension == "pdf":
        keys = set(locator)
        if keys == {"page", "line"}:
            return {"page": page, "line": line, "source": "native"}, False
        if keys == {"page", "line", "source"} and locator.get("source") == "ocr":
            return {"page": page, "line": line, "source": "ocr"}, True
        render_admission = _validated_ocr_render_admission(locator.get("ocr_render_admission"))
        if (
            keys == {"page", "line", "source", "ocr_render_admission"}
            and locator.get("source") == "ocr"
            and render_admission is not None
        ):
            return {
                "page": page,
                "line": line,
                "source": "ocr",
                "ocr_render_admission": render_admission,
            }, True
        if locator.get("source") == "ocr_rescue":
            from idis.parsers.ocr import OcrError, _rescue_provenance_from_worker_payload

            raw_provenance = {
                key: value
                for key, value in locator.items()
                if key not in {"page", "line", "source", "ocr_render_admission"}
            }
            try:
                provenance = _rescue_provenance_from_worker_payload(raw_provenance)
            except OcrError:
                _fail("A PDF OCR rescue locator has invalid provenance")
            result: dict[str, Any] = {
                "page": page,
                "line": line,
                "source": "ocr_rescue",
                **asdict(provenance),
            }
            if render_admission is not None:
                result["ocr_render_admission"] = render_admission
            return result, True
        _fail("A PDF OCR locator has an invalid shape")
    _fail("An OCR locator is bound to an unexpected source format")


def _validated_ocr_render_admission(
    value: object,
    *,
    required_schema_version: str | None = None,
) -> dict[str, object] | None:
    """Validate a full post-render receipt, retaining v1 for sealed evidence only."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        _fail("A PDF OCR render admission has an invalid shape")
    schema_version = value.get("schema_version")
    if required_schema_version is not None and schema_version != required_schema_version:
        _fail("A PDF OCR render admission has an invalid version")
    if schema_version == LEGACY_OCR_RENDER_ADMISSION_SCHEMA_VERSION:
        if set(value) != _OCR_RENDER_ADMISSION_V1_KEYS:
            _fail("A PDF OCR render admission has an invalid shape")
        if (
            value.get("ladder_version") != OCR_DPI_LADDER_VERSION
            or value.get("configured_dpi") != OCR_DPI_LADDER[0]
            or value.get("chosen_dpi") not in OCR_DPI_LADDER
            or _strict_int(value.get("projected_pixel_count")) is None
            or _strict_int(value.get("max_pixel_count")) != MAX_OCR_IMAGE_PIXELS
            or int(value["projected_pixel_count"]) < 1
            or int(value["projected_pixel_count"]) > MAX_OCR_IMAGE_PIXELS
        ):
            _fail("A PDF OCR render admission drifted from its governed ladder")
        return dict(value)
    if schema_version != OCR_RENDER_ADMISSION_SCHEMA_VERSION:
        _fail("A PDF OCR render admission has an invalid version")
    if set(value) != _OCR_RENDER_ADMISSION_V2_KEYS:
        _fail("A PDF OCR render admission has an invalid shape")
    _validate_v2_render_contract(value, observed=True)
    return dict(value)


def _validated_ocr_render_plan(value: object) -> dict[str, object]:
    """Validate a source-derived pre-render plan without fabricating observations."""

    if not isinstance(value, Mapping) or set(value) != _OCR_RENDER_PLAN_V2_KEYS:
        _fail("A PDF OCR source render plan has an invalid shape")
    _validate_v2_render_contract(value, observed=False)
    return dict(value)


def _validate_v2_render_contract(value: Mapping[str, Any], *, observed: bool) -> None:
    integer_keys: tuple[str, ...] = (
        "configured_dpi",
        "chosen_dpi",
        "projected_width",
        "projected_height",
        "projected_pixel_count",
        "expected_render_width",
        "expected_render_height",
        "expected_render_pixel_count",
        "tolerance_pixels",
        "max_pixel_count",
    )
    if observed:
        integer_keys = (
            *integer_keys,
            "actual_render_width",
            "actual_render_height",
            "actual_render_pixel_count",
            "width_delta_pixels",
            "height_delta_pixels",
        )
    if any(type(value.get(key)) is not int for key in integer_keys):
        _fail("A PDF OCR render admission contains an invalid dimension")

    projected_width = int(value["projected_width"])
    projected_height = int(value["projected_height"])
    projected_pixels = int(value["projected_pixel_count"])
    expected_width = int(value["expected_render_width"])
    expected_height = int(value["expected_render_height"])
    expected_pixels = int(value["expected_render_pixel_count"])
    max_pixels = int(value["max_pixel_count"])
    if (
        value.get("schema_version") != OCR_RENDER_ADMISSION_SCHEMA_VERSION
        or value.get("ladder_version") != OCR_DPI_LADDER_VERSION
        or value.get("configured_dpi") != OCR_DPI_LADDER[0]
        or value.get("chosen_dpi") not in OCR_DPI_LADDER
        or int(value["chosen_dpi"]) > int(value["configured_dpi"])
        or min(projected_width, projected_height, expected_width, expected_height) < 1
        or projected_pixels != projected_width * projected_height
        or expected_pixels != expected_width * expected_height
        or max_pixels != MAX_OCR_IMAGE_PIXELS
        or projected_pixels > max_pixels
        or expected_pixels > max_pixels
        or value.get("poppler_version") != PINNED_POPPLER_VERSION
        or value.get("poppler_runtime_identity_sha256") != _PINNED_POPPLER_RUNTIME_IDENTITY_SHA256
        or value.get("expected_dimension_policy_id") != POPPLER_EXPECTED_DIMENSION_POLICY_ID
        or value.get("expected_dimension_policy_sha256") != POPPLER_EXPECTED_DIMENSION_POLICY_SHA256
        or value.get("comparison_mode") != POPPLER_EXACT_COMPARISON_MODE
        or value.get("tolerance_pixels") != 0
        or value.get("tolerance_used") is not False
    ):
        _fail("A PDF OCR render admission drifted from its governed ladder")
    _required_sha256(value, "poppler_runtime_identity_sha256")
    _required_sha256(value, "expected_dimension_policy_sha256")

    if not observed:
        return
    actual_width = int(value["actual_render_width"])
    actual_height = int(value["actual_render_height"])
    actual_pixels = int(value["actual_render_pixel_count"])
    if (
        min(actual_width, actual_height) < 1
        or actual_pixels != actual_width * actual_height
        or actual_pixels > max_pixels
        or actual_width != expected_width
        or actual_height != expected_height
        or value.get("width_delta_pixels") != actual_width - expected_width
        or value.get("height_delta_pixels") != actual_height - expected_height
        or value.get("width_delta_pixels") != 0
        or value.get("height_delta_pixels") != 0
    ):
        _fail("A PDF OCR rendered dimension receipt violated its exact admission")


def _source_render_plan(admission: OcrPageRenderAdmission) -> dict[str, object]:
    """Project an immutable v2 plan while keeping unavailable observations absent."""

    if (
        not isinstance(admission, OcrPageRenderAdmission)
        or admission.schema_version != OCR_RENDER_ADMISSION_SCHEMA_VERSION
        or admission.has_observed_render
    ):
        _fail("A source PDF OCR render plan is not a pre-render v2 admission")
    plan = {
        "schema_version": admission.schema_version,
        "ladder_version": admission.ladder_version,
        "configured_dpi": admission.configured_dpi,
        "chosen_dpi": admission.chosen_dpi,
        "projected_width": admission.projected_width,
        "projected_height": admission.projected_height,
        "projected_pixel_count": admission.projected_pixel_count,
        "expected_render_width": admission.expected_render_width,
        "expected_render_height": admission.expected_render_height,
        "expected_render_pixel_count": admission.expected_render_pixel_count,
        "poppler_version": admission.poppler_version,
        "poppler_runtime_identity_sha256": admission.poppler_runtime_identity_sha256,
        "expected_dimension_policy_id": admission.expected_dimension_policy_id,
        "expected_dimension_policy_sha256": admission.expected_dimension_policy_sha256,
        "comparison_mode": admission.comparison_mode,
        "tolerance_pixels": admission.tolerance_pixels,
        "tolerance_used": admission.tolerance_used,
        "max_pixel_count": admission.max_pixel_count,
    }
    return {"page": admission.page_number, **_validated_ocr_render_plan(plan)}


def _safe_ocr_evidence(
    metadata: Mapping[str, Any],
    *,
    extension: str,
    locator_proof: Mapping[str, Any] | None,
    watermark_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    is_fresh_pdf_evidence = extension == "pdf" and (
        "ocr_render_admissions" in metadata or bool(_preclassified_page_numbers(watermark_receipt))
    )
    if is_fresh_pdf_evidence:
        return _safe_pdf_ocr_evidence_v4(
            metadata,
            locator_proof=locator_proof,
            watermark_receipt=watermark_receipt,
        )
    if locator_proof is None:
        _fail("An OCR document omitted its locator proof")
    mean_confidence = _required_confidence(metadata.get("ocr_mean_confidence"))
    locator_page_count = _required_positive_int(locator_proof, "ocr_page_count")
    locator_span_count = _required_positive_int(locator_proof, "ocr_span_count")
    locator_contract_sha256 = _required_sha256(locator_proof, "locator_contract_sha256")
    rescue_page_count = _required_nonnegative_int(locator_proof, "ocr_rescue_page_count")
    rescue_span_count = _required_nonnegative_int(locator_proof, "ocr_rescue_span_count")
    rescue_receipt_sha256 = _required_sha256(
        locator_proof,
        "ocr_rescue_receipt_sha256",
    )
    if extension == "png":
        if metadata.get("ocr_image_count") != 1 or locator_page_count != 1:
            _fail("An image OCR confidence census is incomplete")
        confidences = [mean_confidence]
    elif extension == "pdf":
        page_count = _required_positive_int(metadata, "ocr_page_count")
        required_page_count = _required_positive_int(metadata, "ocr_required_page_count")
        raw_confidences = metadata.get("ocr_confidence_by_page")
        if not isinstance(raw_confidences, list):
            _fail("A PDF OCR confidence census is incomplete")
        confidences = [_required_confidence(value) for value in raw_confidences]
        if (
            page_count != required_page_count
            or page_count != locator_page_count
            or len(confidences) != page_count
            or metadata.get("ocr_rescue_page_count") != rescue_page_count
            or metadata.get("ocr_rescue_span_count") != rescue_span_count
            or metadata.get("ocr_rescue_receipt_sha256") != rescue_receipt_sha256
            or not math.isclose(
                mean_confidence,
                round(math.fsum(confidences) / len(confidences), 4),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            _fail("A PDF OCR confidence census does not cover its OCR spans")
    else:
        _fail("An OCR confidence proof is bound to an unexpected source format")
    return {
        "schema_version": OCR_TERMINAL_LEGACY_EVIDENCE_SCHEMA_VERSION,
        "ocr_page_count": locator_page_count,
        "ocr_span_count": locator_span_count,
        "confidence_count": len(confidences),
        "mean_confidence": mean_confidence,
        "minimum_confidence": min(confidences),
        "maximum_confidence": max(confidences),
        "locator_contract_sha256": locator_contract_sha256,
        "ocr_rescue_page_count": rescue_page_count,
        "ocr_rescue_span_count": rescue_span_count,
        "ocr_rescue_receipt_sha256": rescue_receipt_sha256,
    }


def _safe_pdf_ocr_evidence_v5(
    metadata: Mapping[str, Any],
    *,
    content_sha256: str,
    parse_status: str,
    locator_proof: Mapping[str, Any] | None,
    watermark_receipt: Mapping[str, Any] | None,
    expected_page_count: int | None,
    whole_document_terminal: bool,
) -> dict[str, Any]:
    """Build one complete PDF page census from durable, reduced evidence only."""

    if locator_proof is None:
        _fail("A PDF omitted its durable page-locator proof")
    page_count = _required_positive_int(metadata, "page_count")
    if expected_page_count is not None and page_count != expected_page_count:
        _fail("A durable PDF page count drifted from its source binding")
    if watermark_receipt is not None and watermark_receipt.get("page_count") != page_count:
        _fail("A watermark receipt drifted from its durable PDF page count")

    native_pages = _required_ordered_page_numbers(locator_proof, "native_page_numbers")
    ocr_pages = _required_ordered_page_numbers(locator_proof, "ocr_page_numbers")
    page_evidence_bindings = locator_proof.get("page_evidence_bindings")
    if not isinstance(page_evidence_bindings, Mapping):
        _fail("A PDF omitted its durable page evidence bindings")
    text_page_bindings: dict[int, str] = {}
    for page in (*native_pages, *ocr_pages):
        binding = page_evidence_bindings.get(str(page))
        if not isinstance(binding, str) or _SHA256_RE.fullmatch(binding) is None:
            _fail("A PDF page omitted its durable text binding")
        text_page_bindings[page] = binding
    if set(page_evidence_bindings) != {str(page) for page in text_page_bindings}:
        _fail("A PDF durable page binding is not text-bearing")

    image_receipts = _validated_image_only_page_receipts(
        metadata,
        content_sha256=content_sha256,
    )
    image_pages = tuple(
        _required_positive_int(receipt, "page_number") for receipt in image_receipts
    )
    watermark_pages = _preclassified_page_numbers(watermark_receipt)

    if any(
        page > page_count for page in (*native_pages, *ocr_pages, *image_pages, *watermark_pages)
    ):
        _fail("A PDF page disposition exceeds its document page count")
    required_page_count = _required_nonnegative_int(metadata, "ocr_required_page_count")
    required_pages = set(ocr_pages) | set(image_pages) | set(watermark_pages)
    if required_page_count != len(required_pages):
        _fail("A PDF OCR/preclassification census does not cover every required page")

    raw_metadata_admissions = metadata.get("ocr_render_admissions", [])
    metadata_admissions = _validated_page_bound_render_admissions(
        raw_metadata_admissions,
        required_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    )
    metadata_admissions_sha256 = metadata.get(
        "ocr_render_admissions_sha256",
        _canonical_sequence_of_mappings_sha256([]),
    )
    if not isinstance(metadata_admissions_sha256, str) or (
        _SHA256_RE.fullmatch(metadata_admissions_sha256) is None
    ):
        _fail("A PDF OCR render-admission digest is malformed")
    execution_pages = tuple(sorted((*ocr_pages, *image_pages)))
    if (
        tuple(_required_positive_int(admission, "page") for admission in metadata_admissions)
        != execution_pages
        or metadata_admissions_sha256 != _canonical_sequence_of_mappings_sha256(metadata_admissions)
        or (metadata.get("ocr_performed") is True) != bool(execution_pages)
    ):
        _fail("A PDF OCR render-admission receipt has an incomplete page census")
    admissions_by_page = {
        _required_positive_int(admission, "page"): admission for admission in metadata_admissions
    }

    span_admissions = _validated_page_bound_render_admissions(
        locator_proof.get("ocr_render_admissions"),
        required_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    )
    span_admission_count = _required_nonnegative_int(
        locator_proof,
        "ocr_render_admission_span_count",
    )
    if span_admission_count != _required_nonnegative_int(
        locator_proof, "ocr_span_count"
    ) or span_admissions != [admissions_by_page[page] for page in ocr_pages]:
        _fail("A PDF OCR render admission drifted from its durable text locators")
    if locator_proof.get("ocr_render_admissions_sha256") != (
        _canonical_sequence_of_mappings_sha256(span_admissions)
    ):
        _fail("A PDF OCR text-locator admission digest drifted")
    for receipt in image_receipts:
        page = _required_positive_int(receipt, "page_number")
        receipt_admission = receipt.get("ocr_render_admission")
        if not isinstance(receipt_admission, Mapping) or {
            "page": page,
            **dict(receipt_admission),
        } != admissions_by_page.get(page):
            _fail("An image-only page render receipt drifted from PDF metadata")

    locator_span_count = _required_nonnegative_int(locator_proof, "ocr_span_count")
    if locator_span_count and not ocr_pages or not locator_span_count and ocr_pages:
        _fail("A PDF OCR text-locator census is incomplete")
    locator_contract_sha256 = _required_sha256(locator_proof, "locator_contract_sha256")
    rescue_page_count = _required_nonnegative_int(locator_proof, "ocr_rescue_page_count")
    rescue_span_count = _required_nonnegative_int(locator_proof, "ocr_rescue_span_count")
    rescue_receipt_sha256 = _required_sha256(locator_proof, "ocr_rescue_receipt_sha256")
    raw_confidences = metadata.get("ocr_confidence_by_page", [])
    if not isinstance(raw_confidences, list):
        _fail("A PDF OCR confidence census is incomplete")
    confidences = [_required_confidence(value) for value in raw_confidences]
    mean_confidence: float | None = None
    if ocr_pages:
        mean_confidence = _required_confidence(metadata.get("ocr_mean_confidence"))
        if len(confidences) != len(ocr_pages) or not math.isclose(
            mean_confidence,
            round(math.fsum(confidences) / len(confidences), 4),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            _fail("A PDF OCR confidence census does not cover its text pages")
    elif confidences or metadata.get("ocr_mean_confidence") is not None:
        _fail("A zero-text OCR execution carries fabricated confidence")
    if (
        metadata.get("ocr_page_count", 0) != len(execution_pages)
        or metadata.get("ocr_rescue_page_count", 0) != rescue_page_count
        or metadata.get("ocr_rescue_span_count", 0) != rescue_span_count
        or metadata.get("ocr_rescue_receipt_sha256", _canonical_sequence_of_mappings_sha256([]))
        != rescue_receipt_sha256
    ):
        _fail("A PDF OCR execution aggregate drifted from durable evidence")

    watermark_classifications = _watermark_classifications_by_page(watermark_receipt)
    image_bindings = {
        _required_positive_int(receipt, "page_number"): _canonical_sha256(receipt)
        for receipt in image_receipts
    }
    watermark_bindings = {
        page: _canonical_sha256(classification)
        for page, classification in watermark_classifications.items()
    }
    census = _build_pdf_page_census(
        page_count=page_count,
        native_text_pages=native_pages,
        ocr_text_pages=ocr_pages,
        image_only_pages=image_pages,
        watermark_only_pages=watermark_pages,
        evidence_bindings={
            **text_page_bindings,
            **image_bindings,
            **watermark_bindings,
        },
    )
    text_bearing = bool(native_pages or ocr_pages)
    if parse_status == "PARSED" and not text_bearing:
        _fail("A parsed PDF has no text-bearing page")
    if not whole_document_terminal and parse_status != "PARSED":
        _fail("A non-terminal PDF did not reach a parsed state")
    if whole_document_terminal and (
        text_bearing or set(watermark_pages) != set(range(1, page_count + 1))
    ):
        _fail("A whole-document watermark terminal row has an invalid page census")

    # Validate the document-level image-only decision after the page census and
    # the text-bearing invariant.  Receipt provenance is already fully checked
    # above; this ordering keeps a complete, coherent all-image page set from
    # being misreported as a malformed receipt/evaluation pair.
    image_evaluation = _validated_image_only_page_evaluation(
        metadata,
        content_sha256=content_sha256,
        page_count=page_count,
        parse_status=parse_status,
    )
    if image_evaluation is not None:
        durable_span_count = _required_nonnegative_int(locator_proof, "durable_span_count")
        if image_evaluation.get("substantive_span_count") != durable_span_count:
            _fail("An image-only evaluation drifted from durable substantive spans")

    return {
        "schema_version": OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION,
        "ocr_page_count": len(ocr_pages),
        "ocr_page_numbers": list(ocr_pages),
        "preclassified_page_numbers": list(watermark_pages),
        "ocr_required_page_count": required_page_count,
        "ocr_span_count": locator_span_count,
        "confidence_count": len(confidences),
        "mean_confidence": mean_confidence,
        "minimum_confidence": min(confidences) if confidences else None,
        "maximum_confidence": max(confidences) if confidences else None,
        "locator_contract_sha256": locator_contract_sha256,
        "ocr_rescue_page_count": rescue_page_count,
        "ocr_rescue_span_count": rescue_span_count,
        "ocr_rescue_receipt_sha256": rescue_receipt_sha256,
        "ocr_render_admissions": metadata_admissions,
        "ocr_render_admissions_sha256": metadata_admissions_sha256,
        "pdf_page_census": census,
    }


def _required_ordered_page_numbers(
    value: Mapping[str, Any],
    key: str,
) -> tuple[int, ...]:
    raw_pages = value.get(key)
    if not isinstance(raw_pages, list):
        _fail("A PDF durable page set is missing")
    pages = tuple(_required_page_number(page) for page in raw_pages)
    if pages != tuple(sorted(set(pages))):
        _fail("A PDF durable page set is duplicated or unordered")
    return pages


def _validated_image_only_page_receipts(
    metadata: Mapping[str, Any],
    *,
    content_sha256: str,
) -> list[dict[str, object]]:
    raw_receipts = metadata.get("image_only_page_receipts")
    raw_evaluation = metadata.get("image_only_page_evaluation")
    if raw_receipts is None and raw_evaluation is None:
        return []
    projected = project_safe_image_only_page_receipts(raw_receipts)
    if projected is None:
        _fail("An image-only PDF page omitted its reviewed terminal receipt")
    validated: list[dict[str, object]] = []
    for receipt in projected:
        page = _required_positive_int(receipt, "page_number")
        if (
            receipt.get("schema_version") != IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION
            or IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION != "image-only-page-receipt-v2"
            or receipt.get("content_sha256") != content_sha256
        ):
            _fail("An image-only page receipt has an invalid current version")
        admission = _validated_ocr_render_admission(
            receipt.get("ocr_render_admission"),
            required_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        )
        if admission is None:
            _fail("An image-only page receipt omitted its render provenance")
        try:
            execution = validate_ocr_page_execution_receipt_payload(
                receipt.get("ocr_page_execution_receipt")
            )
            execution_payload = execution.to_payload()
            execution_sha256 = ocr_page_execution_receipt_sha256(execution)
        except OcrError:
            _fail("An image-only page receipt omitted its no-consensus provenance")
        if (
            execution.page_number != page
            or execution.outcome != "no_consensus"
            or execution.primary_completed is not True
            or execution.primary_token_count != 0
            or execution.rescue_attempted_candidate_count
            != execution.rescue_completed_candidate_count
            or execution.accepted_text is not False
            or execution.accepted_text_token_count != 0
            or execution.accepted_text_span_count != 0
            or execution.accepted_text_codepoint_count != 0
            or receipt.get("ocr_terminal_proof_sha256") != execution_sha256
        ):
            _fail("An image-only page receipt does not prove a completed no-consensus result")
        validated.append(
            {
                **dict(receipt),
                "ocr_render_admission": admission,
                "ocr_page_execution_receipt": execution_payload,
            }
        )
    return validated


def _validated_image_only_page_evaluation(
    metadata: Mapping[str, Any],
    *,
    content_sha256: str,
    page_count: int,
    parse_status: str,
) -> dict[str, object] | None:
    raw_receipts = metadata.get("image_only_page_receipts")
    raw_evaluation = metadata.get("image_only_page_evaluation")
    if raw_receipts is None and raw_evaluation is None:
        return None
    projected_metadata = project_safe_image_only_page_metadata(
        receipts=raw_receipts,
        evaluation=raw_evaluation,
        parse_status=parse_status,
    )
    if projected_metadata is None:
        _fail("An image-only PDF page omitted its reviewed terminal receipt")
    evaluation = projected_metadata.get("image_only_page_evaluation")
    if not isinstance(evaluation, Mapping):
        _fail("An image-only PDF page omitted its durable evaluation")
    if (
        evaluation.get("content_sha256") != content_sha256
        or evaluation.get("page_count") != page_count
    ):
        _fail("An image-only page receipt drifted from its parent PDF")
    return dict(evaluation)


def _watermark_classifications_by_page(
    receipt: Mapping[str, Any] | None,
) -> dict[int, Mapping[str, Any]]:
    pages = _preclassified_page_numbers(receipt)
    if not pages:
        return {}
    assert receipt is not None
    raw_classifications = receipt.get("watermark_page_classifications")
    if not isinstance(raw_classifications, list) or len(raw_classifications) != len(pages):
        _fail("A watermark page census omitted its signed classifications")
    return {
        page: classification
        for page, classification in zip(pages, raw_classifications, strict=True)
        if isinstance(classification, Mapping)
    }


def _build_pdf_page_census(
    *,
    page_count: int,
    native_text_pages: Sequence[int],
    ocr_text_pages: Sequence[int],
    image_only_pages: Sequence[int],
    watermark_only_pages: Sequence[int],
    evidence_bindings: Mapping[int, str],
) -> dict[str, Any]:
    classes = {
        "native_text": tuple(native_text_pages),
        "ocr_text": tuple(ocr_text_pages),
        "image_only_page": tuple(image_only_pages),
        "watermark_only": tuple(watermark_only_pages),
    }
    memberships: dict[int, list[str]] = {page: [] for page in range(1, page_count + 1)}
    for disposition, pages in classes.items():
        if pages != tuple(sorted(set(pages))):
            _fail("A PDF page disposition is duplicated or unordered")
        for page in pages:
            if page not in memberships:
                _fail("A PDF page disposition is outside its document")
            memberships[page].append(disposition)
    if any(len(dispositions) != 1 for dispositions in memberships.values()):
        _fail("A PDF page disposition census is incomplete or overlapping")
    if set(evidence_bindings) != set(memberships) or any(
        not isinstance(binding, str) or _SHA256_RE.fullmatch(binding) is None
        for binding in evidence_bindings.values()
    ):
        _fail("A PDF page disposition omitted its durable evidence binding")
    unsigned: dict[str, Any] = {
        "schema_version": PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION,
        "page_count": page_count,
        "page_dispositions": [
            {
                "page_number": page,
                "disposition": memberships[page][0],
                "evidence_binding_sha256": evidence_bindings[page],
            }
            for page in range(1, page_count + 1)
        ],
        "native_text_page_numbers": list(classes["native_text"]),
        "ocr_text_page_numbers": list(classes["ocr_text"]),
        "image_only_page_numbers": list(classes["image_only_page"]),
        "watermark_only_page_numbers": list(classes["watermark_only"]),
        "class_counts": {
            disposition: len(classes[disposition]) for disposition in _PDF_PAGE_DISPOSITION_CLASSES
        },
    }
    return {**unsigned, "census_sha256": _canonical_sha256(unsigned)}


def _validated_pdf_page_census(
    value: object,
    *,
    expected_page_count: int | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _PDF_PAGE_CENSUS_KEYS:
        _fail("A fresh PDF omitted its complete page-disposition census")
    page_count = _required_positive_int(value, "page_count")
    if expected_page_count is not None and page_count != expected_page_count:
        _fail("A PDF page-disposition census drifted from its source page count")
    if value.get("schema_version") != PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION:
        _fail("A PDF page-disposition census has an invalid version")
    raw_dispositions = value.get("page_dispositions")
    if not isinstance(raw_dispositions, list) or len(raw_dispositions) != page_count:
        _fail("A PDF page-disposition sequence is incomplete")
    evidence_bindings: dict[int, str] = {}
    for expected_page, raw in enumerate(raw_dispositions, start=1):
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"page_number", "disposition", "evidence_binding_sha256"}
            or raw.get("page_number") != expected_page
            or raw.get("disposition") not in _PDF_PAGE_DISPOSITION_CLASSES
        ):
            _fail("A PDF page-disposition row is malformed or unordered")
        binding = raw.get("evidence_binding_sha256")
        if not isinstance(binding, str) or _SHA256_RE.fullmatch(binding) is None:
            _fail("A PDF page-disposition row omitted its evidence binding")
        evidence_bindings[expected_page] = binding

    def page_array(key: str) -> tuple[int, ...]:
        raw_pages = value.get(key)
        if not isinstance(raw_pages, list):
            _fail("A PDF page-disposition class omitted its page set")
        pages = tuple(_required_page_number(page) for page in raw_pages)
        if pages != tuple(sorted(set(pages))):
            _fail("A PDF page-disposition class is duplicated or unordered")
        return pages

    native_pages = page_array("native_text_page_numbers")
    ocr_pages = page_array("ocr_text_page_numbers")
    image_pages = page_array("image_only_page_numbers")
    watermark_pages = page_array("watermark_only_page_numbers")
    rebuilt = _build_pdf_page_census(
        page_count=page_count,
        native_text_pages=native_pages,
        ocr_text_pages=ocr_pages,
        image_only_pages=image_pages,
        watermark_only_pages=watermark_pages,
        evidence_bindings=evidence_bindings,
    )
    if dict(value) != rebuilt:
        _fail("A PDF page-disposition census digest or class index drifted")
    return rebuilt


def _preclassified_page_numbers(
    receipt: Mapping[str, Any] | None,
) -> tuple[int, ...]:
    if (
        receipt is None
        or receipt.get("schema_version") != NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
    ):
        return ()
    raw_classifications = receipt.get("watermark_page_classifications")
    if not isinstance(raw_classifications, list) or not raw_classifications:
        return ()
    schemas = {
        classification.get("schema_version")
        for classification in raw_classifications
        if isinstance(classification, Mapping)
    }
    if schemas != {WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION}:
        return ()
    pages = tuple(
        _required_positive_int(classification, "page_number")
        for classification in raw_classifications
        if isinstance(classification, Mapping)
    )
    if len(pages) != len(raw_classifications) or pages != tuple(sorted(set(pages))):
        _fail("A preclassified PDF page census is duplicated or unordered")
    return pages


def _validated_page_bound_render_admissions(
    value: Any,
    *,
    required_schema_version: str | None = None,
    source_plans: bool = False,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        _fail("A PDF OCR render-admission census is missing")
    admissions: list[dict[str, object]] = []
    pages: list[int] = []
    for raw_admission in value:
        expected_keys = (
            _OCR_RENDER_PLAN_V2_PAGE_KEYS
            if source_plans
            else (
                _OCR_RENDER_ADMISSION_V2_PAGE_KEYS
                if required_schema_version == OCR_RENDER_ADMISSION_SCHEMA_VERSION
                else None
            )
        )
        if (
            not isinstance(raw_admission, Mapping)
            or (expected_keys is not None and set(raw_admission) != expected_keys)
            or (
                expected_keys is None
                and set(raw_admission)
                not in {_OCR_RENDER_ADMISSION_KEYS, _OCR_RENDER_ADMISSION_V2_PAGE_KEYS}
            )
        ):
            _fail("A PDF OCR render admission has an invalid page-bound shape")
        page = _required_positive_int(raw_admission, "page")
        unbound = {key: item for key, item in raw_admission.items() if key != "page"}
        if source_plans:
            validated_admission = _validated_ocr_render_plan(unbound)
        else:
            maybe_admission = _validated_ocr_render_admission(
                unbound,
                required_schema_version=required_schema_version,
            )
            if maybe_admission is None:
                _fail("A PDF OCR render admission is missing")
            validated_admission = maybe_admission
        pages.append(page)
        admissions.append({"page": page, **validated_admission})
    if pages != sorted(set(pages)):
        _fail("A PDF OCR render-admission page census is duplicated or unordered")
    return admissions


def _safe_pdf_ocr_evidence_v4(
    metadata: Mapping[str, Any],
    *,
    locator_proof: Mapping[str, Any] | None,
    watermark_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    required_page_count = _required_positive_int(metadata, "ocr_required_page_count")
    total_page_count = _required_positive_int(metadata, "page_count")
    preclassified_pages = _preclassified_page_numbers(watermark_receipt)
    if any(page > total_page_count for page in preclassified_pages):
        _fail("A preclassified PDF page exceeds its document page count")

    if locator_proof is None:
        ocr_pages: tuple[int, ...] = ()
        locator_span_count = 0
        locator_contract_sha256 = _canonical_sequence_of_mappings_sha256([])
        rescue_page_count = 0
        rescue_span_count = 0
        rescue_receipt_sha256 = _canonical_sequence_of_mappings_sha256([])
        span_admissions: list[dict[str, object]] = []
        span_admission_count = 0
        confidences: list[float] = []
        mean_confidence: float | None = None
    else:
        raw_ocr_pages = locator_proof.get("ocr_page_numbers")
        if not isinstance(raw_ocr_pages, list):
            _fail("A PDF OCR page census is missing")
        ocr_pages = tuple(_required_page_number(value) for value in raw_ocr_pages)
        if ocr_pages != tuple(sorted(set(ocr_pages))):
            _fail("A PDF OCR page census is duplicated or unordered")
        locator_page_count = _required_positive_int(locator_proof, "ocr_page_count")
        locator_span_count = _required_positive_int(locator_proof, "ocr_span_count")
        locator_contract_sha256 = _required_sha256(
            locator_proof,
            "locator_contract_sha256",
        )
        rescue_page_count = _required_nonnegative_int(locator_proof, "ocr_rescue_page_count")
        rescue_span_count = _required_nonnegative_int(locator_proof, "ocr_rescue_span_count")
        rescue_receipt_sha256 = _required_sha256(
            locator_proof,
            "ocr_rescue_receipt_sha256",
        )
        span_admissions = _validated_page_bound_render_admissions(
            locator_proof.get("ocr_render_admissions"),
            required_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        )
        span_admission_count = _required_nonnegative_int(
            locator_proof,
            "ocr_render_admission_span_count",
        )
        if (
            locator_page_count != len(ocr_pages)
            or span_admission_count != locator_span_count
            or any(page > total_page_count for page in ocr_pages)
        ):
            _fail("A PDF OCR page census is inconsistent")
        raw_confidences = metadata.get("ocr_confidence_by_page")
        if not isinstance(raw_confidences, list):
            _fail("A PDF OCR confidence census is incomplete")
        confidences = [_required_confidence(value) for value in raw_confidences]
        mean_confidence = _required_confidence(metadata.get("ocr_mean_confidence"))
        if (
            len(confidences) != len(ocr_pages)
            or metadata.get("ocr_page_count") != len(ocr_pages)
            or metadata.get("ocr_rescue_page_count") != rescue_page_count
            or metadata.get("ocr_rescue_span_count") != rescue_span_count
            or metadata.get("ocr_rescue_receipt_sha256") != rescue_receipt_sha256
            or not math.isclose(
                mean_confidence,
                round(math.fsum(confidences) / len(confidences), 4),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            _fail("A PDF OCR confidence census does not cover its OCR pages")

    if set(ocr_pages) & set(preclassified_pages):
        _fail("A PDF page claims both OCR and preclassification")
    resolved_pages = set(ocr_pages) | set(preclassified_pages)
    if len(resolved_pages) != required_page_count:
        _fail("A PDF OCR/preclassification census does not cover every required page")

    metadata_admissions = _validated_page_bound_render_admissions(
        metadata.get("ocr_render_admissions"),
        required_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    )
    metadata_admissions_sha256 = _required_sha256(
        metadata,
        "ocr_render_admissions_sha256",
    )
    if (
        [admission["page"] for admission in metadata_admissions] != list(ocr_pages)
        or metadata_admissions != span_admissions
        or metadata_admissions_sha256 != _canonical_sequence_of_mappings_sha256(metadata_admissions)
        or (
            locator_proof is not None
            and locator_proof.get("ocr_render_admissions_sha256") != metadata_admissions_sha256
        )
    ):
        _fail("A PDF OCR render-admission receipt drifted from its durable spans")

    return {
        "schema_version": OCR_TERMINAL_PRIOR_EVIDENCE_SCHEMA_VERSION,
        "ocr_page_count": len(ocr_pages),
        "ocr_page_numbers": list(ocr_pages),
        "preclassified_page_numbers": list(preclassified_pages),
        "ocr_required_page_count": required_page_count,
        "ocr_span_count": locator_span_count,
        "confidence_count": len(confidences),
        "mean_confidence": mean_confidence,
        "minimum_confidence": min(confidences) if confidences else None,
        "maximum_confidence": max(confidences) if confidences else None,
        "locator_contract_sha256": locator_contract_sha256,
        "ocr_rescue_page_count": rescue_page_count,
        "ocr_rescue_span_count": rescue_span_count,
        "ocr_rescue_receipt_sha256": rescue_receipt_sha256,
        "ocr_render_admissions": metadata_admissions,
        "ocr_render_admissions_sha256": metadata_admissions_sha256,
    }


def _safe_confirmed_ocr_evidence_from_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    receipt_execution_sha256 = (
        _required_sha256(receipt, "watermark_page_classifications_sha256")
        if receipt.get("schema_version") == NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
        else _required_sha256(receipt, "ocr_result_sha256")
    )
    return {
        "schema_version": OCR_TERMINAL_LEGACY_EVIDENCE_SCHEMA_VERSION,
        "ocr_page_count": _required_positive_int(receipt, "page_count"),
        "ocr_span_count": 0,
        "confidence_count": 0,
        "mean_confidence": None,
        "minimum_confidence": None,
        "maximum_confidence": None,
        "locator_contract_sha256": _canonical_sequence_of_mappings_sha256([]),
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": receipt_execution_sha256,
    }


def _required_string_list(row: Mapping[str, Any], key: str) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _fail("A durable evidence row omitted a required code list")
    if len(value) != len(set(value)):
        _fail("A durable evidence row duplicated a code-list member")
    return sorted(value)


def reconcile_terminal_ingestion(
    *,
    source_companies: Sequence[CompanySourceContract],
    terminal_rows: Sequence[Mapping[str, Any]],
    expected_company_counts: Mapping[str, int],
    expected_format_counts: Mapping[str, int],
    expected_total_files: int,
    expected_ocr_files: int,
    expected_stt_files: int,
    expected_large_files: int,
    large_file_threshold_bytes: int,
    expected_pdf_encryption_dispositions: Mapping[str, int],
    expected_media_runtime_provenance: Mapping[str, Any],
    expected_pdf_ocr_content_sha256s: Collection[str],
    expected_pdf_ocr_required_pages_by_content_sha256: (Mapping[str, Sequence[int]] | None) = None,
    expected_pdf_ocr_render_admissions_by_content_sha256: (
        Mapping[str, Sequence[Mapping[str, object]]] | None
    ) = None,
    expected_pdf_page_counts_by_content_sha256: Mapping[str, int] | None = None,
    verified_watermark_manifest_runtime_summary: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Reconcile every approved source row to a successful durable parse.

    The returned value is aggregate-only.  Any malformed, missing, duplicate,
    cross-deal, non-success, or non-durable row raises a stable safe error.
    """
    _validate_expected_contract(
        expected_company_counts=expected_company_counts,
        expected_format_counts=expected_format_counts,
        expected_total_files=expected_total_files,
        expected_ocr_files=expected_ocr_files,
        expected_stt_files=expected_stt_files,
        expected_large_files=expected_large_files,
        large_file_threshold_bytes=large_file_threshold_bytes,
        expected_pdf_encryption_dispositions=expected_pdf_encryption_dispositions,
        expected_media_runtime_provenance=expected_media_runtime_provenance,
        expected_pdf_ocr_content_sha256s=expected_pdf_ocr_content_sha256s,
        expected_pdf_ocr_required_pages_by_content_sha256=(
            expected_pdf_ocr_required_pages_by_content_sha256
        ),
        expected_pdf_ocr_render_admissions_by_content_sha256=(
            expected_pdf_ocr_render_admissions_by_content_sha256
        ),
    )
    admitted_media_runtime = _safe_media_runtime_provenance(expected_media_runtime_provenance)
    expected_pdf_ocr_hashes = frozenset(expected_pdf_ocr_content_sha256s)
    exact_pdf_ocr_contract = _normalize_exact_pdf_ocr_contract(
        expected_execution_content_sha256s=expected_pdf_ocr_hashes,
        required_pages_by_content_sha256=(expected_pdf_ocr_required_pages_by_content_sha256),
        render_admissions_by_content_sha256=(expected_pdf_ocr_render_admissions_by_content_sha256),
    )
    source_by_key, source_counts, source_formats = _source_index(source_companies)
    if (
        source_counts != dict(expected_company_counts)
        or source_formats != Counter(expected_format_counts)
        or len(source_by_key) != expected_total_files
    ):
        _fail("The approved source inventory does not match its terminal contract")
    source_pdf_hashes = {
        source.content_sha256 for source in source_by_key.values() if source.extension == "pdf"
    }
    normalized_pdf_page_counts: dict[str, int] | None = None
    if expected_pdf_page_counts_by_content_sha256 is not None:
        if (
            not isinstance(expected_pdf_page_counts_by_content_sha256, Mapping)
            or set(expected_pdf_page_counts_by_content_sha256) != source_pdf_hashes
            or any(
                not isinstance(content_sha256, str)
                or _SHA256_RE.fullmatch(content_sha256) is None
                or type(page_count) is not int
                or page_count < 1
                for content_sha256, page_count in (
                    expected_pdf_page_counts_by_content_sha256.items()
                )
            )
        ):
            _fail("The complete source-bound PDF page census is invalid")
        normalized_pdf_page_counts = dict(expected_pdf_page_counts_by_content_sha256)
    if (
        not expected_pdf_ocr_hashes <= source_pdf_hashes
        or (
            exact_pdf_ocr_contract is not None
            and not set(exact_pdf_ocr_contract[0]) <= source_pdf_hashes
        )
        or len(expected_pdf_ocr_hashes) + expected_format_counts.get("png", 0) != expected_ocr_files
    ):
        _fail("The approved PDF OCR contract does not match its source inventory")
    if len(terminal_rows) != expected_total_files:
        _fail("The durable terminal row count does not match the source inventory")

    _reject_legacy_oversize_failures(terminal_rows)

    terminal_keys: set[tuple[str, int]] = set()
    document_ids: set[str] = set()
    artifact_ids: set[str] = set()
    content_hashes: set[str] = set()
    object_version_ids: set[str] = set()
    object_uri_hashes: set[str] = set()
    object_store_backends: set[str] = set()
    span_ids: set[str] = set()
    deals_by_company: dict[str, set[str]] = {alias: set() for alias in expected_company_counts}
    counts_by_company: Counter[str] = Counter()
    counts_by_extension: Counter[str] = Counter()
    parsed_file_count = 0
    confirmed_no_substantive_content_file_count = 0
    confirmed_no_substantive_content_variant_counts: Counter[str] = Counter(
        dict.fromkeys(NO_SUBSTANTIVE_CONTENT_VARIANTS, 0)
    )
    manual_v1_confirmed_document_count = 0
    watermark_classifier_document_count = 0
    watermark_classified_page_count = 0
    watermark_batch_attested_page_count = 0
    watermark_runtime_unseen_page_count = 0
    watermark_classified_pages_by_company: Counter[str] = Counter(
        dict.fromkeys(expected_company_counts, 0)
    )
    durable_span_file_count = 0
    durable_object_file_count = 0
    ocr_file_count = 0
    pdf_ocr_file_count = 0
    stt_file_count = 0
    stt_positive_file_count = 0
    frame_ocr_positive_file_count = 0
    media_dual_receipt_file_count = 0
    large_streamed_file_count = 0
    pdf_encryption_dispositions: Counter[str] = Counter()
    confirmed_no_substantive_content_receipts: list[Mapping[str, Any]] = []
    watermark_classification_receipts: list[Mapping[str, Any]] = []
    pdf_page_disposition_class_counts: Counter[str] = Counter(
        dict.fromkeys(_PDF_PAGE_DISPOSITION_CLASSES, 0)
    )
    pdf_page_census_bindings: list[dict[str, Any]] = []

    for raw_row in terminal_rows:
        if not isinstance(raw_row, Mapping):
            _fail("A durable terminal row is malformed")
        company_alias = _required_string(raw_row, "company_alias")
        sequence = _required_nonnegative_int(raw_row, "source_sequence")
        key = (company_alias, sequence)
        if key in terminal_keys:
            _fail("A source row has more than one durable terminal receipt")
        terminal_keys.add(key)
        source = source_by_key.get(key)
        if source is None:
            _fail("A durable terminal row is outside the approved source inventory")

        deal_id = _required_uuid(raw_row, "deal_id")
        document_id = _required_uuid(raw_row, "document_id")
        artifact_id = _required_uuid(raw_row, "doc_id")
        content_hash = _required_sha256(raw_row, "content_sha256")
        extension = _required_extension(raw_row, "extension")
        parsed_doc_type = _required_string(raw_row, "parsed_doc_type")
        size_bytes = _required_nonnegative_int(raw_row, "size_bytes")

        if (
            raw_row.get("generated_upload_name") != source.generated_upload_name
            or content_hash != source.content_sha256
            or extension != source.extension
            or parsed_doc_type != _PARSED_DOCUMENT_TYPE_BY_EXTENSION.get(source.extension)
            or size_bytes != source.size_bytes
        ):
            _fail("A durable terminal row has drifted from its source binding")
        _admit_unique(document_ids, document_id, "document")
        _admit_unique(artifact_ids, artifact_id, "artifact")
        _admit_unique(content_hashes, content_hash, "content hash")
        deals_by_company[company_alias].add(deal_id)

        object_integrity = _validate_durable_object_integrity(
            raw_row.get("durable_object_integrity"),
            content_sha256=content_hash,
            size_bytes=size_bytes,
        )
        _admit_unique(
            object_version_ids,
            str(object_integrity["version_id"]),
            "object version",
        )
        _admit_unique(
            object_uri_hashes,
            str(object_integrity["uri_sha256"]),
            "object URI hash",
        )
        object_store_backends.add(str(object_integrity["backend"]))

        confirmed_receipt = _confirmed_exclusion_receipt(
            raw_row,
            generated_upload_name=source.generated_upload_name,
            content_sha256=content_hash,
            extension=extension,
        )
        if (
            confirmed_receipt is not None
            and confirmed_receipt.get("schema_version")
            == NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
        ):
            _validate_current_watermark_manifest_binding(
                confirmed_receipt,
                verified_manifest_runtime_summary=(verified_watermark_manifest_runtime_summary),
                safe_alias=source.generated_upload_name,
                content_sha256=content_hash,
            )
        is_whole_document_terminal = _receipt_claims_whole_document_terminal(confirmed_receipt)
        if not is_whole_document_terminal:
            _require_success_state(raw_row, extension=extension)
            spans = _validate_spans(
                raw_row,
                document_id=document_id,
                extension=extension,
                seen_span_ids=span_ids,
            )
            if confirmed_receipt is not None and _required_nonnegative_int(
                confirmed_receipt,
                "substantive_span_count",
            ) != len(spans):
                _fail("A mixed watermark receipt drifted from its durable spans")
            parsed_file_count += 1
            durable_span_file_count += 1
        else:
            if confirmed_receipt is None:
                _fail("A whole-document terminal row omitted its receipt")
            spans = _validate_confirmed_no_substantive_content_spans(raw_row)
            confirmed_no_substantive_content_file_count += 1
            confirmed_no_substantive_content_variant_counts[str(confirmed_receipt["variant"])] += 1
            confirmed_no_substantive_content_receipts.append(confirmed_receipt)
        if confirmed_receipt is not None:
            if confirmed_receipt["schema_version"] == NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION:
                manual_v1_confirmed_document_count += 1
            else:
                watermark_classifier_document_count += 1
                watermark_classification_receipts.append(confirmed_receipt)
                classifications = confirmed_receipt["watermark_page_classifications"]
                assert isinstance(classifications, list)
                classified_page_count = len(classifications)
                watermark_classified_page_count += classified_page_count
                watermark_classified_pages_by_company[company_alias] += classified_page_count
                for classification in classifications:
                    assert isinstance(classification, Mapping)
                    if classification["batch_membership"] == "ATTESTED":
                        watermark_batch_attested_page_count += 1
                    else:
                        watermark_runtime_unseen_page_count += 1
        durable_object_file_count += 1

        ocr_performed = _required_bool(raw_row, "ocr_performed")
        media_performed = _required_bool(raw_row, "media_transcription_performed")
        streaming_verified = _required_bool(raw_row, "bounded_streaming_verified")
        preclassified_pages = _preclassified_page_numbers(confirmed_receipt)
        ocr_evidence = raw_row.get("ocr_evidence")
        current_pdf_evidence = (
            extension == "pdf"
            and isinstance(ocr_evidence, Mapping)
            and ocr_evidence.get("schema_version") == OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION
        )
        if extension == "pdf" and current_pdf_evidence:
            assert isinstance(ocr_evidence, Mapping)
            _validate_safe_ocr_evidence(
                ocr_evidence,
                extension=extension,
                durable_span_count=len(spans),
                watermark_receipt=confirmed_receipt,
            )
            census = _validated_pdf_page_census(
                ocr_evidence.get("pdf_page_census"),
                expected_page_count=(
                    normalized_pdf_page_counts.get(content_hash)
                    if normalized_pdf_page_counts is not None
                    else None
                ),
            )
            text_pages = {
                *census["native_text_page_numbers"],
                *census["ocr_text_page_numbers"],
            }
            if (not is_whole_document_terminal and not text_pages) or (
                is_whole_document_terminal
                and (
                    text_pages
                    or census["watermark_only_page_numbers"]
                    != list(range(1, int(census["page_count"]) + 1))
                )
            ):
                _fail("A PDF document-level disposition contradicts its page census")
            class_counts = census["class_counts"]
            assert isinstance(class_counts, Mapping)
            pdf_page_disposition_class_counts.update(
                {key: int(class_counts[key]) for key in _PDF_PAGE_DISPOSITION_CLASSES}
            )
            pdf_page_census_bindings.append(
                {
                    "company_alias": company_alias,
                    "source_sequence": sequence,
                    "content_sha256": content_hash,
                    "census_sha256": census["census_sha256"],
                }
            )
        elif extension == "pdf" and normalized_pdf_page_counts is not None:
            _fail("A fresh source-bound PDF omitted its v5 page census")
        elif ocr_performed:
            if confirmed_receipt is None or not is_whole_document_terminal:
                _validate_safe_ocr_evidence(
                    ocr_evidence,
                    extension=extension,
                    durable_span_count=len(spans),
                    watermark_receipt=confirmed_receipt,
                )
            else:
                _validate_confirmed_no_substantive_content_ocr_evidence(
                    ocr_evidence,
                    receipt=confirmed_receipt,
                )
        elif preclassified_pages:
            _validate_safe_ocr_evidence(
                ocr_evidence,
                extension=extension,
                durable_span_count=len(spans),
                watermark_receipt=confirmed_receipt,
            )
        elif "ocr_evidence" in raw_row:
            _fail("An OCR evidence record is bound to a non-OCR row")
        if extension == "png":
            if not ocr_performed or any(span.get("span_type") != "PAGE_TEXT" for span in spans):
                _fail("An image row lacks durable OCR proof")
        elif extension == "pdf":
            pdf_requires_ocr_execution = content_hash in expected_pdf_ocr_hashes
            if pdf_requires_ocr_execution != ocr_performed:
                _fail("A PDF OCR execution does not match its admitted runtime census")
            if ocr_performed and any(span.get("span_type") != "PAGE_TEXT" for span in spans):
                _fail("A PDF OCR row contains an invalid durable span")
            if exact_pdf_ocr_contract is not None:
                _validate_source_bound_pdf_ocr_evidence(
                    ocr_evidence,
                    content_sha256=content_hash,
                    required_pages_by_content_sha256=exact_pdf_ocr_contract[0],
                    render_admissions_by_content_sha256=exact_pdf_ocr_contract[1],
                    expected_page_count=(
                        normalized_pdf_page_counts.get(content_hash)
                        if normalized_pdf_page_counts is not None
                        else None
                    ),
                )
        elif ocr_performed:
            _fail("An OCR proof is bound to an unexpected source format")
        if extension == "mp4":
            media_frame_ocr_performed = _required_bool(
                raw_row,
                "media_frame_ocr_performed",
            )
            if not media_performed or not media_frame_ocr_performed:
                _fail("A media row lacks one required execution proof")
            row_runtime = _safe_media_runtime_provenance(raw_row.get(MEDIA_RUNTIME_PROVENANCE_KEY))
            if row_runtime != admitted_media_runtime:
                _fail("A media row has drifted from its admitted runtime")
            _stt_receipt, _frame_receipt, stt_positive, frame_positive = (
                _validate_media_dual_track_receipts(
                    stt_receipt=raw_row.get(MEDIA_STT_RECEIPT_KEY),
                    frame_receipt=raw_row.get(MEDIA_FRAME_OCR_RECEIPT_KEY),
                    spans=spans,
                    runtime_provenance=row_runtime,
                )
            )
            stt_positive_file_count += int(stt_positive)
            frame_ocr_positive_file_count += int(frame_positive)
            media_dual_receipt_file_count += 1
        elif media_performed:
            _fail("A media proof is bound to an unexpected source format")
        elif (
            raw_row.get("media_frame_ocr_performed") is True
            or MEDIA_RUNTIME_PROVENANCE_KEY in raw_row
            or MEDIA_STT_RECEIPT_KEY in raw_row
            or MEDIA_FRAME_OCR_RECEIPT_KEY in raw_row
        ):
            _fail("A media runtime proof is bound to a non-media row")

        is_large = size_bytes > large_file_threshold_bytes
        if is_large != streaming_verified:
            _fail("A large-file streaming proof does not match its source row")

        if extension == "pdf":
            encryption_disposition = _required_string(
                raw_row,
                "pdf_encryption_disposition",
            )
            if encryption_disposition not in _PDF_ENCRYPTION_DISPOSITION_SET:
                _fail("A PDF row has an invalid encryption disposition")
            pdf_encryption_dispositions[encryption_disposition] += 1
        elif "pdf_encryption_disposition" in raw_row:
            _fail("A PDF encryption disposition is bound to a non-PDF row")

        resolved_pdf_ocr = extension == "pdf" and content_hash in expected_pdf_ocr_hashes
        ocr_file_count += int((extension == "png" and ocr_performed) or resolved_pdf_ocr)
        pdf_ocr_file_count += int(resolved_pdf_ocr)
        stt_file_count += int(media_performed)
        large_streamed_file_count += int(streaming_verified)
        counts_by_company[company_alias] += 1
        counts_by_extension[extension] += 1

    if terminal_keys != set(source_by_key):
        _fail("One or more approved source rows lack a durable terminal receipt")
    _validate_deal_isolation(deals_by_company)
    if counts_by_company != Counter(expected_company_counts):
        _fail("The durable company counts do not match the approved source split")
    if counts_by_extension != Counter(expected_format_counts):
        _fail("The durable format census does not match the approved source inventory")
    if len(object_store_backends) != 1:
        _fail("The durable object rows do not share one persistent backend")
    if manual_v1_confirmed_document_count > 1:
        _fail("More than one source row claims the one-document no-substantive-content manifest")
    for company_alias, company_file_count in expected_company_counts.items():
        classified_page_count = watermark_classified_pages_by_company[company_alias]
        if (
            100 * classified_page_count
            > WATERMARK_CLASSIFIER_SANITY_CAP_PERCENT * company_file_count
        ):
            _fail_with_code(
                WATERMARK_PAGE_SANITY_CAP_EXCEEDED,
                "The watermark classifier exceeded its per-company sanity cap",
            )
    if (
        ocr_file_count != expected_ocr_files
        or stt_file_count != expected_stt_files
        or media_dual_receipt_file_count != expected_stt_files
        or large_streamed_file_count != expected_large_files
    ):
        _fail("The durable parser/runtime proof census is incomplete")
    actual_pdf_dispositions = {
        disposition: pdf_encryption_dispositions[disposition]
        for disposition in PDF_ENCRYPTION_DISPOSITIONS
    }
    if actual_pdf_dispositions != dict(expected_pdf_encryption_dispositions):
        _fail("The durable PDF encryption census does not match the approved source")

    expected_pdf_file_count = expected_format_counts.get("pdf", 0)
    if pdf_page_census_bindings and len(pdf_page_census_bindings) != expected_pdf_file_count:
        _fail("Fresh and historical PDF page evidence cannot be mixed")
    has_complete_page_census = len(pdf_page_census_bindings) == expected_pdf_file_count
    if has_complete_page_census:
        pdf_page_census_bindings.sort(
            key=lambda row: (str(row["company_alias"]), int(row["source_sequence"]))
        )
        if normalized_pdf_page_counts is not None and sum(
            normalized_pdf_page_counts.values()
        ) != sum(pdf_page_disposition_class_counts.values()):
            _fail("The durable PDF page total drifted from its source-bound census")

    result: dict[str, Any] = {
        "schema_version": (
            TERMINAL_INGESTION_RECONCILIATION_SCHEMA_VERSION
            if has_complete_page_census
            else TERMINAL_INGESTION_PRIOR_RECONCILIATION_SCHEMA_VERSION
        ),
        "status": "ready",
        "total_file_count": expected_total_files,
        "counts_by_company": dict(sorted(counts_by_company.items())),
        "counts_by_extension": dict(expected_format_counts),
        "parsed_file_count": parsed_file_count,
        "parsed_span_bearing_file_count": parsed_file_count,
        "confirmed_no_substantive_content_file_count": confirmed_no_substantive_content_file_count,
        "confirmed_no_substantive_content_variant_counts": dict(
            sorted(confirmed_no_substantive_content_variant_counts.items())
        ),
        "confirmed_no_substantive_content_receipts_sha256": (
            _canonical_receipt_sequence_sha256(confirmed_no_substantive_content_receipts)
        ),
        "manual_v1_confirmed_document_count": manual_v1_confirmed_document_count,
        "watermark_classifier_document_count": watermark_classifier_document_count,
        "watermark_manifest_bound_receipt_count": watermark_classifier_document_count,
        "watermark_manifest_runtime_summary_sha256": _canonical_sha256(
            verified_watermark_manifest_runtime_summary or {}
        ),
        "watermark_classified_page_count": watermark_classified_page_count,
        "watermark_batch_attested_page_count": watermark_batch_attested_page_count,
        "watermark_runtime_unseen_page_count": watermark_runtime_unseen_page_count,
        "watermark_classified_pages_by_company": dict(
            sorted(watermark_classified_pages_by_company.items())
        ),
        "runtime_review_required": watermark_runtime_unseen_page_count > 0,
        "watermark_classification_receipts_sha256": (
            _canonical_receipt_sequence_sha256(watermark_classification_receipts)
        ),
        "durable_span_file_count": durable_span_file_count,
        "durable_object_file_count": durable_object_file_count,
        "object_store_backend": next(iter(object_store_backends)),
        "ocr_file_count": ocr_file_count,
        "pdf_ocr_file_count": pdf_ocr_file_count,
        "pdf_ocr_contract_sha256": _canonical_sequence_sha256(sorted(expected_pdf_ocr_hashes)),
        "stt_file_count": stt_file_count,
        "media_dual_receipt_file_count": media_dual_receipt_file_count,
        "stt_positive_file_count": stt_positive_file_count,
        "frame_ocr_positive_file_count": frame_ocr_positive_file_count,
        "large_streamed_file_count": large_streamed_file_count,
        "pdf_encryption_dispositions": actual_pdf_dispositions,
        "media_runtime_provenance_sha256": _canonical_sha256(admitted_media_runtime),
    }
    if has_complete_page_census:
        result.update(
            {
                "pdf_page_disposition_census_schema_version": (
                    PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION
                ),
                "pdf_page_disposition_class_counts": {
                    disposition: pdf_page_disposition_class_counts[disposition]
                    for disposition in _PDF_PAGE_DISPOSITION_CLASSES
                },
                "pdf_page_disposition_censuses_sha256": (
                    _canonical_sequence_of_mappings_sha256(pdf_page_census_bindings)
                ),
            }
        )
    return result


def _validate_durable_object_integrity(
    value: Any,
    *,
    content_sha256: str,
    size_bytes: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "content_sha256",
        "size_bytes",
        "uri_sha256",
        "version_id",
        "backend",
        "streaming_verified",
    }:
        _fail("A durable terminal row omitted its object integrity proof")
    if (
        value.get("schema_version") != DURABLE_OBJECT_INTEGRITY_SCHEMA_VERSION
        or _required_sha256(value, "content_sha256") != content_sha256
        or _required_nonnegative_int(value, "size_bytes") != size_bytes
        or value.get("streaming_verified") is not True
    ):
        _fail("A durable object integrity proof drifted from its terminal row")
    return {
        "schema_version": DURABLE_OBJECT_INTEGRITY_SCHEMA_VERSION,
        "content_sha256": content_sha256,
        "size_bytes": size_bytes,
        "uri_sha256": _required_sha256(value, "uri_sha256"),
        "version_id": _required_uuid(value, "version_id"),
        "backend": _required_persistent_backend(value, "backend"),
        "streaming_verified": True,
    }


def _normalize_exact_pdf_ocr_contract(
    *,
    expected_execution_content_sha256s: Collection[str],
    required_pages_by_content_sha256: Mapping[str, Sequence[int]] | None,
    render_admissions_by_content_sha256: (Mapping[str, Sequence[Mapping[str, object]]] | None),
) -> (
    tuple[
        dict[str, tuple[int, ...]],
        dict[str, tuple[dict[str, object], ...]],
    ]
    | None
):
    """Validate the immutable-source PDF page/admission expectation."""

    if required_pages_by_content_sha256 is None and render_admissions_by_content_sha256 is None:
        return None
    if not isinstance(required_pages_by_content_sha256, Mapping) or not isinstance(
        render_admissions_by_content_sha256,
        Mapping,
    ):
        _fail("The exact PDF OCR source contract is incomplete")
    if (
        not required_pages_by_content_sha256
        or set(required_pages_by_content_sha256) != set(render_admissions_by_content_sha256)
        or not set(expected_execution_content_sha256s) <= set(required_pages_by_content_sha256)
    ):
        _fail("The exact PDF OCR source contract is inconsistent")

    normalized_pages: dict[str, tuple[int, ...]] = {}
    normalized_admissions: dict[str, tuple[dict[str, object], ...]] = {}
    for content_sha256, raw_pages in required_pages_by_content_sha256.items():
        if (
            not isinstance(content_sha256, str)
            or _SHA256_RE.fullmatch(content_sha256) is None
            or isinstance(raw_pages, (str, bytes))
            or not isinstance(raw_pages, Sequence)
        ):
            _fail("The exact PDF OCR page contract is malformed")
        pages = tuple(_required_page_number(page) for page in raw_pages)
        if not pages or pages != tuple(sorted(set(pages))):
            _fail("The exact PDF OCR page contract is duplicated or unordered")

        raw_admissions = render_admissions_by_content_sha256.get(content_sha256)
        if isinstance(raw_admissions, (str, bytes)) or not isinstance(
            raw_admissions,
            Sequence,
        ):
            _fail("The exact PDF OCR render-admission contract is malformed")
        admissions = _validated_page_bound_render_admissions(
            list(raw_admissions),
            required_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
            source_plans=True,
        )
        if [admission["page"] for admission in admissions] != list(pages):
            _fail("The exact PDF OCR render admissions do not cover their page set")
        normalized_pages[content_sha256] = pages
        normalized_admissions[content_sha256] = tuple(admissions)
    return normalized_pages, normalized_admissions


def _validate_source_bound_pdf_ocr_evidence(
    value: object,
    *,
    content_sha256: str,
    required_pages_by_content_sha256: Mapping[str, tuple[int, ...]],
    render_admissions_by_content_sha256: Mapping[str, tuple[Mapping[str, object], ...]],
    expected_page_count: int | None = None,
) -> None:
    """Bind one fresh durable PDF row to exact source pages and geometry."""

    required_pages = required_pages_by_content_sha256.get(content_sha256)
    if (
        isinstance(value, Mapping)
        and value.get("schema_version") == OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION
    ):
        census = _validated_pdf_page_census(
            value.get("pdf_page_census"),
            expected_page_count=expected_page_count,
        )
        non_native_pages = tuple(
            sorted(
                (
                    *census["ocr_text_page_numbers"],
                    *census["image_only_page_numbers"],
                    *census["watermark_only_page_numbers"],
                )
            )
        )
        if required_pages is None:
            if non_native_pages or value.get("ocr_required_page_count") != 0:
                _fail("A native-text PDF carries unexpected OCR page evidence")
            return
        if non_native_pages != required_pages or value.get("ocr_required_page_count") != len(
            required_pages
        ):
            _fail("A durable PDF page disposition drifted across its source binding")
        actual_admissions = _validated_page_bound_render_admissions(
            value.get("ocr_render_admissions"),
            required_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        )
        watermark_pages = set(census["watermark_only_page_numbers"])
        expected_by_page = {
            _required_positive_int(admission, "page"): dict(admission)
            for admission in render_admissions_by_content_sha256[content_sha256]
        }
        expected_admissions = [
            expected_by_page[page] for page in required_pages if page not in watermark_pages
        ]
        actual_plans = [
            {key: admission[key] for key in ("page", *_OCR_RENDER_PLAN_V2_KEYS)}
            for admission in actual_admissions
        ]
        if actual_plans != expected_admissions:
            _fail("A durable PDF OCR render admission drifted from source geometry")
        return
    if required_pages is None:
        if value is not None:
            _fail("A native-text PDF carries unexpected OCR page evidence")
        return
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != OCR_TERMINAL_PRIOR_EVIDENCE_SCHEMA_VERSION
    ):
        _fail("A source-bound PDF omitted fresh exact OCR page evidence")
    raw_ocr_pages = value.get("ocr_page_numbers")
    raw_preclassified_pages = value.get("preclassified_page_numbers")
    if not isinstance(raw_ocr_pages, list) or not isinstance(
        raw_preclassified_pages,
        list,
    ):
        _fail("A source-bound PDF omitted its exact OCR page sets")
    ocr_pages = tuple(_required_page_number(page) for page in raw_ocr_pages)
    preclassified_pages = tuple(_required_page_number(page) for page in raw_preclassified_pages)
    if tuple(sorted((*ocr_pages, *preclassified_pages))) != required_pages or value.get(
        "ocr_required_page_count"
    ) != len(required_pages):
        _fail("A durable PDF OCR page set drifted across its source binding")

    raw_admissions = value.get("ocr_render_admissions")
    actual_admissions = _validated_page_bound_render_admissions(
        raw_admissions,
        required_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    )
    actual_admissions_sha256 = _canonical_sequence_of_mappings_sha256(actual_admissions)
    if _required_sha256(value, "ocr_render_admissions_sha256") != actual_admissions_sha256:
        _fail("A durable PDF OCR render-admission receipt digest drifted")
    expected_by_page = {
        _required_positive_int(admission, "page"): dict(admission)
        for admission in render_admissions_by_content_sha256[content_sha256]
    }
    expected_admissions = [expected_by_page[page] for page in ocr_pages]
    actual_plans = [
        {key: admission[key] for key in ("page", *_OCR_RENDER_PLAN_V2_KEYS)}
        for admission in actual_admissions
    ]
    if actual_plans != expected_admissions or _canonical_sequence_of_mappings_sha256(
        actual_plans
    ) != _canonical_sequence_of_mappings_sha256(expected_admissions):
        _fail("A durable PDF OCR render admission drifted from source geometry")


def _validate_expected_contract(
    *,
    expected_company_counts: Mapping[str, int],
    expected_format_counts: Mapping[str, int],
    expected_total_files: int,
    expected_ocr_files: int,
    expected_stt_files: int,
    expected_large_files: int,
    large_file_threshold_bytes: int,
    expected_pdf_encryption_dispositions: Mapping[str, int],
    expected_media_runtime_provenance: Mapping[str, Any],
    expected_pdf_ocr_content_sha256s: Collection[str],
    expected_pdf_ocr_required_pages_by_content_sha256: (Mapping[str, Sequence[int]] | None),
    expected_pdf_ocr_render_admissions_by_content_sha256: (
        Mapping[str, Sequence[Mapping[str, object]]] | None
    ),
) -> None:
    company_counts = dict(expected_company_counts)
    format_counts = dict(expected_format_counts)
    pdf_dispositions = dict(expected_pdf_encryption_dispositions)
    if (
        not company_counts
        or any(not _valid_company_alias(alias) for alias in company_counts)
        or any(not _is_positive_int(count) for count in company_counts.values())
        or any(not _SAFE_EXTENSION_RE.fullmatch(extension) for extension in format_counts)
        or any(not _is_positive_int(count) for count in format_counts.values())
        or not _is_positive_int(expected_total_files)
        or sum(company_counts.values()) != expected_total_files
        or sum(format_counts.values()) != expected_total_files
        or not _is_nonnegative_int(expected_ocr_files)
        or not _is_nonnegative_int(expected_stt_files)
        or not _is_nonnegative_int(expected_large_files)
        or not _is_positive_int(large_file_threshold_bytes)
        or set(pdf_dispositions) != _PDF_ENCRYPTION_DISPOSITION_SET
        or any(not _is_nonnegative_int(count) for count in pdf_dispositions.values())
        or sum(pdf_dispositions.values()) != format_counts.get("pdf", 0)
        or isinstance(expected_pdf_ocr_content_sha256s, (str, bytes))
        or any(
            not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None
            for digest in expected_pdf_ocr_content_sha256s
        )
    ):
        _fail("The expected terminal reconciliation contract is invalid")
    _normalize_exact_pdf_ocr_contract(
        expected_execution_content_sha256s=frozenset(expected_pdf_ocr_content_sha256s),
        required_pages_by_content_sha256=(expected_pdf_ocr_required_pages_by_content_sha256),
        render_admissions_by_content_sha256=(expected_pdf_ocr_render_admissions_by_content_sha256),
    )
    _safe_media_runtime_provenance(expected_media_runtime_provenance)


def _safe_media_runtime_provenance(value: Any) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _MEDIA_RUNTIME_PROVENANCE_KEYS:
        _fail("A media runtime provenance record is invalid")
    normalized: dict[str, object] = {}
    string_keys = _MEDIA_RUNTIME_PROVENANCE_KEYS - {"cpu_threads", "num_workers"}
    for key in sorted(string_keys):
        item = value.get(key)
        if not isinstance(item, str) or not item:
            _fail("A media runtime provenance record is invalid")
        normalized[key] = item
    cpu_threads = value.get("cpu_threads")
    num_workers = value.get("num_workers")
    if (
        type(cpu_threads) is not int
        or cpu_threads != FASTER_WHISPER_CPU_THREADS
        or type(num_workers) is not int
        or num_workers != FASTER_WHISPER_NUM_WORKERS
    ):
        _fail("A media runtime provenance record is invalid")
    normalized["cpu_threads"] = cpu_threads
    normalized["num_workers"] = num_workers
    if (
        normalized["schema_version"] != FASTER_WHISPER_RUNTIME_PROVENANCE_SCHEMA_VERSION
        or normalized["adapter"] != "faster-whisper"
        or normalized["adapter_contract_version"] != FASTER_WHISPER_ADAPTER_CONTRACT_VERSION
        or normalized["model_source"] != "local"
        or _SAFE_VERSION_RE.fullmatch(str(normalized["runtime_package_version"])) is None
        or _SHA256_RE.fullmatch(str(normalized["model_files_sha256"])) is None
        or _SHA256_RE.fullmatch(str(normalized["config_sha256"])) is None
    ):
        _fail("A media runtime provenance record is invalid")
    return normalized


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_sequence_sha256(value: Sequence[str]) -> str:
    encoded = json.dumps(
        list(value),
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_sequence_of_mappings_sha256(value: Sequence[Mapping[str, Any]]) -> str:
    encoded = json.dumps(
        [dict(row) for row in value],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_receipt_sequence_sha256(value: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(
        value,
        key=lambda row: (
            str(row.get("safe_alias", "")),
            str(row.get("content_sha256", "")),
            str(row.get("schema_version", "")),
        ),
    )
    return _canonical_sequence_of_mappings_sha256(ordered)


def _source_index(
    source_companies: Sequence[CompanySourceContract],
) -> tuple[dict[tuple[str, int], SourceFileContract], dict[str, int], Counter[str]]:
    source_by_key: dict[tuple[str, int], SourceFileContract] = {}
    counts: dict[str, int] = {}
    formats: Counter[str] = Counter()
    source_hashes: set[str] = set()
    generated_names: set[str] = set()
    for company in source_companies:
        alias = company.company_alias
        if not _valid_company_alias(alias) or alias in counts:
            _fail("The source company contract is malformed")
        if company.file_count != len(company.files):
            _fail("A source company count does not match its rows")
        if company.total_bytes != sum(row.size_bytes for row in company.files):
            _fail("A source company byte count does not match its rows")
        if [row.sequence for row in company.files] != list(range(len(company.files))):
            _fail("A source company sequence is not contiguous")
        counts[alias] = company.file_count
        for source in company.files:
            key = (alias, source.sequence)
            if (
                key in source_by_key
                or not _SAFE_EXTENSION_RE.fullmatch(source.extension)
                or not _SHA256_RE.fullmatch(source.content_sha256)
                or source.content_sha256 in source_hashes
                or source.generated_upload_name in generated_names
                or source.size_bytes < 0
            ):
                _fail("A source file contract is malformed or duplicated")
            source_by_key[key] = source
            source_hashes.add(source.content_sha256)
            generated_names.add(source.generated_upload_name)
            formats[source.extension] += 1
    return source_by_key, counts, formats


def _receipt_claims_whole_document_terminal(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, Mapping):
        _fail("A no-substantive-content receipt is malformed")
    schema_version = value.get("schema_version")
    if schema_version == NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION:
        return True
    if schema_version != NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION:
        _fail("A no-substantive-content receipt has an invalid version")
    whole_document_terminal = value.get("whole_document_terminal")
    if type(whole_document_terminal) is not bool:
        _fail("A watermark receipt omitted its terminal scope")
    return whole_document_terminal


def _receipt_terminal_error_codes(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        _fail("A no-substantive-content receipt is malformed")
    if value.get("schema_version") == NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION:
        return [_required_string(value, "parser_error_code")]
    raw_classifications = value.get("watermark_page_classifications")
    if not isinstance(raw_classifications, list) or not raw_classifications:
        _fail("A watermark receipt omitted its page classifications")
    schemas = {
        classification.get("schema_version")
        for classification in raw_classifications
        if isinstance(classification, Mapping)
    }
    if len(schemas) != 1 or len(raw_classifications) != sum(
        isinstance(classification, Mapping) for classification in raw_classifications
    ):
        _fail("A watermark receipt mixes page-classification schemas")
    if schemas == {WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION}:
        return ["ocr_no_text_extracted"]
    if schemas == {WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION}:
        return ["ocr_rescue_no_consensus"]
    _fail("A watermark receipt has an invalid page-classification schema")


def _validate_current_watermark_manifest_binding(
    receipt: Mapping[str, Any],
    *,
    verified_manifest_runtime_summary: Mapping[str, object] | None,
    safe_alias: str,
    content_sha256: str,
) -> None:
    """Rebind a durable v2 receipt to the manifest verified for this Gate run.

    Structural receipt checks alone cannot distinguish a stale signed batch from
    the currently configured one.  The production Gate supplies the exact
    runtime summary from its verified manifest; this reuses the ingestion
    validator so governance hashes, exemplar identity, and every reviewed
    candidate observation are compared at the final durable boundary too.
    """

    if verified_manifest_runtime_summary is None:
        _fail("A watermark receipt is not bound to the current verified manifest")
    try:
        validate_document_no_substantive_content_receipt_v2(
            receipt=receipt,
            verified_manifest=verified_manifest_runtime_summary,
            tenant_id=_required_string(verified_manifest_runtime_summary, "tenant_id"),
            source_tree_fingerprint=_required_sha256(
                verified_manifest_runtime_summary,
                "source_tree_fingerprint",
            ),
            safe_alias=safe_alias,
            content_sha256=content_sha256,
            now=datetime.now(UTC),
        )
    except DocumentNoSubstantiveContentAttestationError:
        _fail("A watermark receipt drifted from the current verified manifest")


def _confirmed_exclusion_receipt(
    row: Mapping[str, Any],
    *,
    generated_upload_name: str,
    content_sha256: str,
    extension: str,
) -> Mapping[str, Any] | None:
    raw_receipt = row.get("document_no_substantive_content_receipt")
    if raw_receipt is None:
        return None
    if not isinstance(raw_receipt, Mapping):
        _fail("A confirmed-no-substantive-content row omitted its exact signed receipt")
    if raw_receipt.get("schema_version") == NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION:
        return _confirmed_exclusion_receipt_v2(
            row,
            raw_receipt=raw_receipt,
            generated_upload_name=generated_upload_name,
            content_sha256=content_sha256,
            extension=extension,
        )
    if set(raw_receipt) != _DOCUMENT_NO_SUBSTANTIVE_CONTENT_RECEIPT_KEYS:
        _fail("A confirmed-no-substantive-content row omitted its exact signed receipt")
    if (
        extension != "pdf"
        or row.get("terminal_status") != "CONFIRMED_EXCLUSION"
        or row.get("file_status") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        or row.get("support_status") != "scanned_or_image_only"
        or row.get("triage_status") != "excluded"
        or row.get("parse_status") != "FAILED"
        or row.get("reason_codes") != [DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED]
        or not _empty_code_list(row.get("warning_codes"))
        or row.get("error_codes") not in (["ocr_rescue_no_consensus"], ["ocr_no_text_extracted"])
    ):
        _fail("A confirmed-no-substantive-content row contains a contradictory terminal state")
    if (
        raw_receipt.get("schema_version") != NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION
        or raw_receipt.get("disposition") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        or raw_receipt.get("variant") not in NO_SUBSTANTIVE_CONTENT_VARIANTS
        or raw_receipt.get("safe_alias") != generated_upload_name
        or raw_receipt.get("content_sha256") != content_sha256
        or raw_receipt.get("signature_verified") is not True
        or raw_receipt.get("parser_error_code") != "ocr_rescue_no_consensus"
        or raw_receipt.get("parser_runtime") != "tesseract"
        or raw_receipt.get("parser_runtime_status") != "completed_no_extractable_text"
    ):
        _fail("A confirmed-no-substantive-content receipt drifted from its terminal row")
    for digest_key in (
        "content_sha256",
        "source_tree_fingerprint",
        "attestation_text_sha256",
        "payload_sha256",
        "envelope_sha256",
        "public_key_sha256",
        "ocr_result_sha256",
    ):
        _required_sha256(raw_receipt, digest_key)
    reviewer_id = _required_string(raw_receipt, "reviewer_id")
    signer_id = _required_string(raw_receipt, "signer_id")
    if not reviewer_id.strip() or not signer_id.strip():
        _fail("A confirmed-no-substantive-content receipt omitted its reviewer or signer")
    approved_at = _parse_utc_timestamp(raw_receipt.get("approved_at"))
    expires_at = _parse_utc_timestamp(raw_receipt.get("expires_at"))
    if approved_at is None or expires_at is None or expires_at <= approved_at:
        _fail("A confirmed-no-substantive-content receipt has an invalid approval window")
    page_count = _required_positive_int(raw_receipt, "page_count")
    if (
        _required_positive_int(raw_receipt, "ocr_required_page_count") != page_count
        or _required_positive_int(raw_receipt, "primary_completed_page_count") != page_count
        or _required_positive_int(raw_receipt, "rescue_completed_page_count") != page_count
        or _required_nonnegative_int(raw_receipt, "accepted_text_page_count") != 0
        or _required_nonnegative_int(raw_receipt, "span_count") != 0
    ):
        _fail("A confirmed-no-substantive-content receipt lacks complete zero-text OCR proof")
    return dict(raw_receipt)


def _confirmed_exclusion_receipt_v2(
    row: Mapping[str, Any],
    *,
    raw_receipt: Mapping[str, Any],
    generated_upload_name: str,
    content_sha256: str,
    extension: str,
) -> Mapping[str, Any]:
    if set(raw_receipt) != _DOCUMENT_NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_KEYS:
        _fail("A watermark-classified row omitted its exact terminal receipt")
    whole_document_terminal = raw_receipt.get("whole_document_terminal")
    if type(whole_document_terminal) is not bool or extension != "pdf":
        _fail("A watermark-classified receipt has an invalid terminal scope")
    if whole_document_terminal:
        valid_row_state = (
            row.get("terminal_status") == "CONFIRMED_EXCLUSION"
            and row.get("file_status") == DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
            and row.get("support_status") == "scanned_or_image_only"
            and row.get("triage_status") == "excluded"
            and row.get("parse_status") == "FAILED"
            and row.get("reason_codes") == [DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED]
            and _empty_code_list(row.get("warning_codes"))
        )
    else:
        valid_row_state = (
            row.get("terminal_status") == "SUCCEEDED"
            and row.get("file_status") == "SUPPORTED"
            and row.get("support_status") == "supported"
            and row.get("triage_status") == "ready"
            and row.get("parse_status") == "PARSED"
            and _has_exact_completion_reason("pdf", row.get("reason_codes"))
            and _empty_code_list(row.get("warning_codes"))
            and _empty_code_list(row.get("error_codes"))
        )
    if not valid_row_state:
        _fail("A watermark-classified row contains a contradictory terminal state")
    if (
        raw_receipt.get("schema_version") != NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
        or raw_receipt.get("disposition") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        or raw_receipt.get("variant") != "watermark_only"
        or raw_receipt.get("safe_alias") != generated_upload_name
        or raw_receipt.get("content_sha256") != content_sha256
        or raw_receipt.get("manifest_schema_version") != WATERMARK_MANIFEST_SCHEMA_VERSION
        or raw_receipt.get("classifier_policy_id") != WATERMARK_CLASSIFIER_POLICY_ID
        or raw_receipt.get("signature_verified") is not True
    ):
        _fail("A watermark-classified receipt drifted from its terminal row")
    for digest_key in (
        "content_sha256",
        "source_tree_fingerprint",
        "manifest_sha256",
        "batch_envelope_sha256",
        "candidate_set_sha256",
        "classifier_policy_sha256",
        "exemplar_envelope_sha256",
        "exemplar_canonical_bitmap_sha256",
        "public_key_sha256",
        "watermark_page_classifications_sha256",
    ):
        _required_sha256(raw_receipt, digest_key)

    page_count = _required_positive_int(raw_receipt, "page_count")
    classified_page_count = _required_positive_int(raw_receipt, "classified_page_count")
    substantive_span_count = _required_nonnegative_int(raw_receipt, "substantive_span_count")
    if whole_document_terminal:
        if classified_page_count != page_count or substantive_span_count != 0:
            _fail("A watermark-classified receipt is not a whole-document zero-span proof")
    elif classified_page_count >= page_count or substantive_span_count < 1:
        _fail("A mixed watermark receipt has no substantive parent evidence")
    raw_classifications = raw_receipt.get("watermark_page_classifications")
    if (
        not isinstance(raw_classifications, list)
        or len(raw_classifications) != classified_page_count
    ):
        _fail("A watermark-classified receipt omitted one or more page proofs")
    if (
        _canonical_sequence_of_mappings_sha256(_classification_mappings(raw_classifications))
        != raw_receipt["watermark_page_classifications_sha256"]
    ):
        _fail("A watermark-classified receipt page digest is inconsistent")

    expected_policy_sha256 = str(raw_receipt["classifier_policy_sha256"])
    expected_exemplar_envelope_sha256 = str(raw_receipt["exemplar_envelope_sha256"])
    expected_exemplar_bitmap_sha256 = str(raw_receipt["exemplar_canonical_bitmap_sha256"])
    page_numbers: set[int] = set()
    exemplar_identity: tuple[str, str, int] | None = None
    for raw_classification in raw_classifications:
        classification, current_exemplar_identity = _validate_watermark_page_classification(
            raw_classification,
            content_sha256=content_sha256,
            classifier_policy_sha256=expected_policy_sha256,
            exemplar_envelope_sha256=expected_exemplar_envelope_sha256,
            exemplar_bitmap_sha256=expected_exemplar_bitmap_sha256,
        )
        page_number = _required_positive_int(classification, "page_number")
        _required_sha256(classification, "canonical_render_sha256")
        if page_number in page_numbers:
            _fail("A watermark-classified receipt contains a duplicate page claim")
        page_numbers.add(page_number)
        if exemplar_identity is None:
            exemplar_identity = current_exemplar_identity
        elif exemplar_identity != current_exemplar_identity:
            _fail("A watermark-classified receipt mixes exemplar identities")
    if (
        len(page_numbers) != classified_page_count
        or not page_numbers <= set(range(1, page_count + 1))
        or (whole_document_terminal and page_numbers != set(range(1, page_count + 1)))
    ):
        _fail("A watermark-classified receipt page sequence is incomplete")
    classification_schemas = {
        classification.get("schema_version")
        for classification in _classification_mappings(raw_classifications)
    }
    if classification_schemas == {WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION}:
        if whole_document_terminal and row.get("error_codes") != ["ocr_no_text_extracted"]:
            _fail("A pre-OCR watermark terminal row claims post-OCR execution")
    elif classification_schemas == {WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION}:
        if whole_document_terminal and row.get("error_codes") != ["ocr_rescue_no_consensus"]:
            _fail("A post-OCR watermark terminal row lacks its OCR failure code")
    else:
        _fail("A watermark terminal receipt mixes classification schemas")
    return dict(raw_receipt)


def _classification_mappings(value: Sequence[Any]) -> list[Mapping[str, Any]]:
    if any(not isinstance(item, Mapping) for item in value):
        _fail("A watermark-classified receipt contains a malformed page proof")
    return [item for item in value if isinstance(item, Mapping)]


def _validate_watermark_page_classification(
    value: Any,
    *,
    content_sha256: str,
    classifier_policy_sha256: str,
    exemplar_envelope_sha256: str,
    exemplar_bitmap_sha256: str,
) -> tuple[Mapping[str, Any], tuple[str, str, int]]:
    if not isinstance(value, Mapping):
        _fail("A watermark page classification omitted exact provenance")
    if value.get("schema_version") == WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION:
        return _validate_pre_ocr_watermark_page_classification(
            value,
            content_sha256=content_sha256,
            classifier_policy_sha256=classifier_policy_sha256,
            exemplar_envelope_sha256=exemplar_envelope_sha256,
            exemplar_bitmap_sha256=exemplar_bitmap_sha256,
        )
    if set(value) != _WATERMARK_PAGE_CLASSIFICATION_KEYS:
        _fail("A watermark page classification omitted exact provenance")
    if (
        value.get("schema_version") != WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION
        or value.get("classification") != "watermark_only"
        or value.get("content_sha256") != content_sha256
        or value.get("policy_id") != WATERMARK_CLASSIFIER_POLICY_ID
        or value.get("policy_sha256") != classifier_policy_sha256
        or value.get("transform_chain") != _WATERMARK_TRANSFORM_CHAIN
        or value.get("transform_bounds") != _WATERMARK_TRANSFORM_BOUNDS
        or value.get("similarity_metric") != "foreground_dice"
    ):
        _fail("A watermark page classification drifted from its admitted policy")
    _required_positive_int(value, "page_number")
    _required_sha256(value, "content_sha256")
    _required_sha256(value, "policy_sha256")
    _required_sha256(value, "canonical_render_sha256")
    _required_sha256(value, "ocr_terminal_proof_sha256")

    confidence = value.get("confidence_distribution")
    if (
        not isinstance(confidence, Mapping)
        or set(confidence) != _WATERMARK_CONFIDENCE_DISTRIBUTION_KEYS
    ):
        _fail("A watermark page classification omitted its confidence distribution")
    near_white_fraction = _fixed_six_decimal(confidence.get("near_white_fraction"))
    foreground_ink_fraction = _fixed_six_decimal(confidence.get("foreground_ink_fraction"))
    if not (
        Decimal("0.970000") <= near_white_fraction <= Decimal("0.985000")
        and Decimal("0.015000") <= foreground_ink_fraction <= Decimal("0.030000")
    ):
        _fail("A watermark page classification is outside its pinned faint bounds")
    similarity_score = _fixed_six_decimal(value.get("similarity_score"))
    numerator = _required_positive_int(value, "similarity_numerator")
    denominator = _required_positive_int(value, "similarity_denominator")
    if similarity_score < Decimal("0.800000") or numerator > denominator:
        _fail("A watermark page classification is below its admitted similarity floor")

    exemplar = value.get("exemplar_reference")
    if not isinstance(exemplar, Mapping) or set(exemplar) != _WATERMARK_EXEMPLAR_REFERENCE_KEYS:
        _fail("A watermark page classification omitted its signed exemplar reference")
    exemplar_id = _required_string(exemplar, "exemplar_id")
    exemplar_content_sha256 = _required_sha256(exemplar, "content_sha256")
    exemplar_page_number = _required_positive_int(exemplar, "page_number")
    if (
        not exemplar_id.strip()
        or exemplar.get("attestation_envelope_sha256") != exemplar_envelope_sha256
        or exemplar.get("canonical_bitmap_sha256") != exemplar_bitmap_sha256
        or exemplar.get("classifier_policy_sha256") != classifier_policy_sha256
    ):
        _fail("A watermark page classification drifted from its signed exemplar")
    _required_sha256(exemplar, "attestation_envelope_sha256")
    _required_sha256(exemplar, "canonical_bitmap_sha256")
    _required_sha256(exemplar, "classifier_policy_sha256")

    membership = value.get("batch_membership")
    if membership == "ATTESTED":
        expected_membership_proof = ("reviewer_batch", False, "BATCH_ATTESTED")
    elif membership == "RUNTIME_UNSEEN":
        expected_membership_proof = (
            "signed_exemplar_runtime_match",
            True,
            "WATERMARK_RUNTIME_CANDIDATE_REVIEW_REQUIRED",
        )
    else:
        _fail("A watermark page classification has an invalid batch membership")
    if (
        value.get("confirmation_source"),
        value.get("post_run_review_required"),
        value.get("review_status"),
    ) != expected_membership_proof:
        _fail("A watermark page classification contradicts its batch membership")
    return value, (exemplar_id, exemplar_content_sha256, exemplar_page_number)


def _validate_pre_ocr_watermark_page_classification(
    value: Mapping[str, Any],
    *,
    content_sha256: str,
    classifier_policy_sha256: str,
    exemplar_envelope_sha256: str,
    exemplar_bitmap_sha256: str,
) -> tuple[Mapping[str, Any], tuple[str, str, int]]:
    if set(value) != _WATERMARK_PAGE_CLASSIFICATION_V2_KEYS:
        _fail("A pre-OCR watermark classification omitted exact provenance")
    if (
        value.get("classification") != "watermark_only"
        or value.get("classification_phase") != "pre_ocr"
        or value.get("full_resolution_ocr_attempted") is not False
        or value.get("content_sha256") != content_sha256
        or value.get("signed_exemplar_policy_id") != WATERMARK_CLASSIFIER_POLICY_ID
        or value.get("signed_exemplar_policy_sha256") != classifier_policy_sha256
        or value.get("runtime_candidate_policy_id") != WATERMARK_RUNTIME_CANDIDATE_RENDER_POLICY_ID
        or value.get("runtime_candidate_policy_sha256")
        != default_watermark_runtime_candidate_render_policy().policy_sha256
        or value.get("transform_chain") != list(WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN)
        or value.get("transform_bounds") != _WATERMARK_TRANSFORM_BOUNDS
        or value.get("similarity_metric") != "foreground_dice"
        or value.get("actual_width_pixels") != 512
        or value.get("actual_height_pixels") != 512
        or value.get("actual_mode") not in {"L", "RGB"}
    ):
        _fail("A pre-OCR watermark classification drifted from its admitted policy")
    _required_positive_int(value, "page_number")
    _required_sha256(value, "content_sha256")
    _required_sha256(value, "signed_exemplar_policy_sha256")
    _required_sha256(value, "runtime_candidate_policy_sha256")
    _required_sha256(value, "canonical_render_sha256")
    confidence = value.get("confidence_distribution")
    if (
        not isinstance(confidence, Mapping)
        or set(confidence) != _WATERMARK_CONFIDENCE_DISTRIBUTION_KEYS
    ):
        _fail("A pre-OCR watermark classification omitted its confidence distribution")
    near_white_fraction = _fixed_six_decimal(confidence.get("near_white_fraction"))
    foreground_ink_fraction = _fixed_six_decimal(confidence.get("foreground_ink_fraction"))
    similarity_score = _fixed_six_decimal(value.get("similarity_score"))
    numerator = _required_positive_int(value, "similarity_numerator")
    denominator = _required_positive_int(value, "similarity_denominator")
    if not (
        Decimal("0.970000") <= near_white_fraction <= Decimal("0.985000")
        and Decimal("0.015000") <= foreground_ink_fraction <= Decimal("0.030000")
        and similarity_score >= Decimal("0.800000")
        and numerator <= denominator
    ):
        _fail("A pre-OCR watermark classification is outside its pinned bounds")
    exemplar = value.get("exemplar_reference")
    if not isinstance(exemplar, Mapping) or set(exemplar) != _WATERMARK_EXEMPLAR_REFERENCE_KEYS:
        _fail("A pre-OCR watermark classification omitted its signed exemplar reference")
    exemplar_id = _required_string(exemplar, "exemplar_id")
    exemplar_content_sha256 = _required_sha256(exemplar, "content_sha256")
    exemplar_page_number = _required_positive_int(exemplar, "page_number")
    if (
        not exemplar_id.strip()
        or exemplar.get("attestation_envelope_sha256") != exemplar_envelope_sha256
        or exemplar.get("canonical_bitmap_sha256") != exemplar_bitmap_sha256
        or exemplar.get("classifier_policy_sha256") != classifier_policy_sha256
    ):
        _fail("A pre-OCR watermark classification drifted from its signed exemplar")
    if value.get("batch_membership") == "ATTESTED":
        _required_sha256(value, "batch_candidate_canonical_render_sha256")
        _fixed_six_decimal(value.get("batch_candidate_similarity_score"))
        expected_membership = ("reviewer_batch_identity", False, "BATCH_ATTESTED")
    elif value.get("batch_membership") == "RUNTIME_UNSEEN":
        if (
            value.get("batch_candidate_canonical_render_sha256") is not None
            or value.get("batch_candidate_similarity_score") is not None
        ):
            _fail("A runtime-unseen watermark classification claims a batch observation")
        expected_membership = (
            "signed_exemplar_runtime_match",
            True,
            "WATERMARK_RUNTIME_CANDIDATE_REVIEW_REQUIRED",
        )
    else:
        _fail("A pre-OCR watermark classification has an invalid batch membership")
    if (
        value.get("confirmation_source"),
        value.get("post_run_review_required"),
        value.get("review_status"),
    ) != expected_membership:
        _fail("A pre-OCR watermark classification contradicts its batch membership")
    return value, (exemplar_id, exemplar_content_sha256, exemplar_page_number)


def _fixed_six_decimal(value: Any) -> Decimal:
    if not isinstance(value, str) or _FIXED_SIX_DECIMAL_RE.fullmatch(value) is None:
        _fail("A watermark page classification contains a malformed fixed decimal")
    try:
        normalized = Decimal(value)
    except InvalidOperation:
        _fail("A watermark page classification contains a malformed fixed decimal")
    if not Decimal(0) <= normalized <= Decimal(1):
        _fail("A watermark page classification contains a malformed fixed decimal")
    return normalized


def _validate_confirmed_no_substantive_content_spans(
    row: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    value = row.get("durable_spans")
    if value != []:
        _fail("A confirmed-no-substantive-content document must have no durable spans")
    return []


def _validate_confirmed_no_substantive_content_ocr_evidence(
    value: Any,
    *,
    receipt: Mapping[str, Any],
) -> None:
    receipt_schema_version = receipt.get("schema_version")
    receipt_execution_sha256 = (
        _required_sha256(receipt, "ocr_result_sha256")
        if receipt_schema_version == NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION
        else _required_sha256(receipt, "watermark_page_classifications_sha256")
    )
    if not isinstance(value, Mapping) or set(value) != _OCR_EVIDENCE_KEYS:
        _fail("A confirmed-no-substantive-content row omitted its exact OCR execution evidence")
    if (
        value.get("schema_version") != OCR_TERMINAL_LEGACY_EVIDENCE_SCHEMA_VERSION
        or _required_positive_int(value, "ocr_page_count")
        != _required_positive_int(receipt, "page_count")
        or _required_nonnegative_int(value, "ocr_span_count") != 0
        or _required_nonnegative_int(value, "confidence_count") != 0
        or value.get("mean_confidence") is not None
        or value.get("minimum_confidence") is not None
        or value.get("maximum_confidence") is not None
        or _required_sha256(value, "locator_contract_sha256")
        != _canonical_sequence_of_mappings_sha256([])
        or _required_nonnegative_int(value, "ocr_rescue_page_count") != 0
        or _required_nonnegative_int(value, "ocr_rescue_span_count") != 0
        or _required_sha256(value, "ocr_rescue_receipt_sha256") != receipt_execution_sha256
    ):
        _fail("A confirmed-no-substantive-content OCR execution receipt is inconsistent")


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _require_success_state(row: Mapping[str, Any], *, extension: str) -> None:
    reason_codes = row.get("reason_codes")
    if (
        row.get("terminal_status") != "SUCCEEDED"
        or row.get("file_status") != "SUPPORTED"
        or row.get("support_status") != "supported"
        or row.get("triage_status") != "ready"
        or row.get("parse_status") != "PARSED"
        or not _has_exact_completion_reason(extension, reason_codes)
        or not _empty_code_list(row.get("warning_codes"))
        or not _empty_code_list(row.get("error_codes"))
    ):
        _fail("A durable terminal row contains a non-success state")


def _has_exact_completion_reason(extension: str, value: Any) -> bool:
    allowed = _COMPLETION_REASONS_BY_EXTENSION.get(extension)
    return (
        allowed is not None
        and isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], str)
        and value[0] in allowed
    )


def _validate_spans(
    row: Mapping[str, Any],
    *,
    document_id: str,
    extension: str,
    seen_span_ids: set[str],
) -> list[Mapping[str, Any]]:
    raw_spans = row.get("durable_spans")
    if not isinstance(raw_spans, list) or (not raw_spans and extension != "mp4"):
        _fail("A parsed document has no durable spans")
    spans: list[Mapping[str, Any]] = []
    previous_end_by_media_track = {
        "media_transcript": -1,
        "video_frame_ocr": -1,
    }
    seen_frame_sample_indices: set[int] = set()
    for raw_span in raw_spans:
        if not isinstance(raw_span, Mapping):
            _fail("A durable span receipt is malformed")
        span_id = _required_uuid(raw_span, "span_id")
        _admit_unique(seen_span_ids, span_id, "span")
        if _required_uuid(raw_span, "document_id") != document_id:
            _fail("A durable span crossed its document boundary")
        _required_sha256(raw_span, "content_hash")
        locator = raw_span.get("locator")
        if not isinstance(locator, Mapping) or not locator:
            _fail("A durable span has no locator")
        span_type = raw_span.get("span_type")
        if extension == "mp4":
            if span_type != "TIMECODE":
                _fail("A media document has a non-timecode durable span")
            source = locator.get("source")
            if source == "media_transcript":
                if set(locator) != {"source", "start_ms", "end_ms"}:
                    _fail("A transcript locator was not safely reduced")
            elif source == "video_frame_ocr":
                if set(locator) != {
                    "source",
                    "sample_index",
                    "start_ms",
                    "end_ms",
                    "confidence",
                }:
                    _fail("A frame-OCR locator was not safely reduced")
                sample_index = _strict_int(locator.get("sample_index"))
                confidence = _optional_confidence(locator.get("confidence"))
                if (
                    sample_index is None
                    or sample_index < 0
                    or sample_index in seen_frame_sample_indices
                    or confidence is None
                ):
                    _fail("A frame-OCR locator is invalid")
                seen_frame_sample_indices.add(sample_index)
            else:
                _fail("A media timecode locator omitted its evidence track")
            start_ms = _strict_int(locator.get("start_ms"))
            end_ms = _strict_int(locator.get("end_ms"))
            if (
                start_ms is None
                or end_ms is None
                or start_ms < 0
                or end_ms <= start_ms
                or start_ms < previous_end_by_media_track[source]
            ):
                _fail("A media timecode locator is invalid")
            previous_end_by_media_track[source] = end_ms
        else:
            if span_type not in _NON_MEDIA_SPAN_TYPES:
                _fail("A document contains an invalid durable span type")
            if set(locator) != {"locator_sha256"}:
                _fail("A non-media locator was not safely reduced")
            _required_sha256(locator, "locator_sha256")
        spans.append(raw_span)
    return spans


def _validate_media_dual_track_receipts(
    *,
    stt_receipt: Any,
    frame_receipt: Any,
    spans: Sequence[Mapping[str, Any]],
    runtime_provenance: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool, bool]:
    """Rebind both completed media receipts to the durable per-track spans."""
    if not isinstance(stt_receipt, Mapping) or set(stt_receipt) != _MEDIA_STT_RECEIPT_KEYS:
        _fail("A media row omitted its exact STT receipt")
    if (
        not isinstance(frame_receipt, Mapping)
        or set(frame_receipt) != _MEDIA_FRAME_OCR_RECEIPT_KEYS
    ):
        _fail("A media row omitted its exact frame-OCR receipt")

    stt_spans = [
        span
        for span in spans
        if isinstance(span.get("locator"), Mapping)
        and span["locator"].get("source") == "media_transcript"
    ]
    frame_spans = [
        span
        for span in spans
        if isinstance(span.get("locator"), Mapping)
        and span["locator"].get("source") == "video_frame_ocr"
    ]
    if len(stt_spans) + len(frame_spans) != len(spans):
        _fail("A media row contains an unbound evidence track")

    stt_evidence = [
        {
            "source": "media_transcript",
            "start_ms": span["locator"]["start_ms"],
            "end_ms": span["locator"]["end_ms"],
            "content_hash": span["content_hash"],
        }
        for span in stt_spans
    ]
    frame_evidence = [
        {
            "source": "video_frame_ocr",
            "sample_index": span["locator"]["sample_index"],
            "start_ms": span["locator"]["start_ms"],
            "end_ms": span["locator"]["end_ms"],
            "confidence": span["locator"]["confidence"],
            "content_hash": span["content_hash"],
        }
        for span in frame_spans
    ]

    stt_segment_count = _required_nonnegative_int(stt_receipt, "segment_count")
    stt_evidence_count = _required_nonnegative_int(stt_receipt, "evidence_span_count")
    if (
        stt_receipt.get("schema_version") != MEDIA_STT_RECEIPT_SCHEMA_VERSION
        or stt_receipt.get("status") != "completed"
        or stt_segment_count < len(stt_evidence)
        or stt_evidence_count != len(stt_evidence)
        or _required_sha256(stt_receipt, "evidence_sha256")
        != _canonical_sequence_of_mappings_sha256(stt_evidence)
        or _required_sha256(stt_receipt, "runtime_provenance_sha256")
        != _canonical_sha256(runtime_provenance)
    ):
        _fail("A media STT receipt drifted from its durable evidence")

    duration_ms = _required_positive_int(frame_receipt, "duration_ms")
    scheduled_count = _required_positive_int(frame_receipt, "scheduled_sample_count")
    completed_count = _required_nonnegative_int(frame_receipt, "completed_sample_count")
    nonempty_count = _required_nonnegative_int(frame_receipt, "nonempty_sample_count")
    frame_evidence_count = _required_nonnegative_int(frame_receipt, "evidence_span_count")
    confidence_count = _required_nonnegative_int(frame_receipt, "confidence_count")
    if (
        frame_receipt.get("schema_version") != MEDIA_FRAME_OCR_RECEIPT_SCHEMA_VERSION
        or frame_receipt.get("status") != "completed"
        or frame_receipt.get("sampling_policy") != MEDIA_FRAME_SAMPLING_POLICY
        or frame_receipt.get("scene_threshold") != MEDIA_FRAME_SCENE_THRESHOLD
        or frame_receipt.get("max_sample_count") != MEDIA_FRAME_MAX_SAMPLE_COUNT
        or frame_receipt.get("max_frame_width") != MEDIA_FRAME_MAX_WIDTH
        or frame_receipt.get("max_frame_height") != MEDIA_FRAME_MAX_HEIGHT
        or scheduled_count > MEDIA_FRAME_MAX_SAMPLE_COUNT
        or completed_count != scheduled_count
        or nonempty_count != len(frame_evidence)
        or frame_evidence_count != len(frame_evidence)
        or confidence_count != len(frame_evidence)
        or _required_sha256(frame_receipt, "evidence_sha256")
        != _canonical_sequence_of_mappings_sha256(frame_evidence)
    ):
        _fail("A media frame-OCR receipt drifted from its durable evidence")
    _required_sha256(frame_receipt, "schedule_sha256")

    for evidence in stt_evidence:
        if evidence["end_ms"] > duration_ms:
            _fail("A transcript timecode exceeds the admitted media duration")
    for evidence in frame_evidence:
        if (
            evidence["sample_index"] >= scheduled_count
            or evidence["start_ms"] >= duration_ms
            or evidence["end_ms"] != min(duration_ms, evidence["start_ms"] + 1)
        ):
            _fail("A frame-OCR timecode exceeds its deterministic schedule")

    confidences = [float(evidence["confidence"]) for evidence in frame_evidence]
    if confidences:
        expected_mean = round(math.fsum(confidences) / len(confidences), 4)
        if (
            not _same_optional_confidence(frame_receipt.get("mean_confidence"), expected_mean)
            or not _same_optional_confidence(
                frame_receipt.get("minimum_confidence"), min(confidences)
            )
            or not _same_optional_confidence(
                frame_receipt.get("maximum_confidence"), max(confidences)
            )
        ):
            _fail("A media frame-OCR confidence receipt has drifted")
    elif any(
        frame_receipt.get(key) is not None
        for key in ("mean_confidence", "minimum_confidence", "maximum_confidence")
    ):
        _fail("An empty media frame-OCR receipt carries confidence evidence")

    if not stt_evidence and not frame_evidence:
        raise TerminalIngestionReconciliationError(
            MEDIA_NO_EXTRACTABLE_EVIDENCE,
            "Both required media evidence tracks completed without evidence",
        )
    return dict(stt_receipt), dict(frame_receipt), bool(stt_evidence), bool(frame_evidence)


def _validate_safe_ocr_evidence(
    value: Any,
    *,
    extension: str,
    durable_span_count: int,
    watermark_receipt: Mapping[str, Any] | None = None,
) -> None:
    if not isinstance(value, Mapping):
        _fail("An OCR row omitted its exact confidence and locator evidence")
    schema_version = value.get("schema_version")
    if schema_version == OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION:
        _validate_safe_ocr_evidence_v5(
            value,
            extension=extension,
            durable_span_count=durable_span_count,
            watermark_receipt=watermark_receipt,
        )
        return
    if schema_version == OCR_TERMINAL_PRIOR_EVIDENCE_SCHEMA_VERSION:
        _validate_safe_ocr_evidence_page_resolution(
            value,
            extension=extension,
            durable_span_count=durable_span_count,
            watermark_receipt=watermark_receipt,
            required_render_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        )
        return
    if schema_version == OCR_TERMINAL_PREVIOUS_EVIDENCE_SCHEMA_VERSION:
        _validate_safe_ocr_evidence_page_resolution(
            value,
            extension=extension,
            durable_span_count=durable_span_count,
            watermark_receipt=watermark_receipt,
            required_render_schema_version=LEGACY_OCR_RENDER_ADMISSION_SCHEMA_VERSION,
        )
        return
    if (
        schema_version != OCR_TERMINAL_LEGACY_EVIDENCE_SCHEMA_VERSION
        or set(value) != _OCR_EVIDENCE_KEYS
    ):
        _fail("An OCR evidence record has an invalid version")
    page_count = _required_positive_int(value, "ocr_page_count")
    ocr_span_count = _required_positive_int(value, "ocr_span_count")
    confidence_count = _required_positive_int(value, "confidence_count")
    mean_confidence = _required_confidence(value.get("mean_confidence"))
    minimum_confidence = _required_confidence(value.get("minimum_confidence"))
    maximum_confidence = _required_confidence(value.get("maximum_confidence"))
    _required_sha256(value, "locator_contract_sha256")
    rescue_page_count = _required_nonnegative_int(value, "ocr_rescue_page_count")
    rescue_span_count = _required_nonnegative_int(value, "ocr_rescue_span_count")
    rescue_receipt_sha256 = _required_sha256(value, "ocr_rescue_receipt_sha256")
    if (
        extension not in {"png", "pdf"}
        or confidence_count != page_count
        or ocr_span_count > durable_span_count
        or rescue_page_count > page_count
        or rescue_span_count > ocr_span_count
        or minimum_confidence > mean_confidence
        or mean_confidence > maximum_confidence
    ):
        _fail("An OCR confidence and locator evidence census is inconsistent")
    if not rescue_page_count and (
        rescue_span_count != 0
        or rescue_receipt_sha256 != _canonical_sequence_of_mappings_sha256([])
    ):
        _fail("A standard OCR row carries unexpected rescue evidence")
    if rescue_page_count and not rescue_span_count:
        _fail("A rescued OCR page has no admitted evidence span")
    if extension == "png" and (page_count != 1 or ocr_span_count != durable_span_count):
        _fail("An image OCR evidence census is inconsistent")


def _validate_safe_ocr_evidence_v5(
    value: Mapping[str, Any],
    *,
    extension: str,
    durable_span_count: int,
    watermark_receipt: Mapping[str, Any] | None,
) -> None:
    if extension != "pdf" or set(value) != _OCR_EVIDENCE_PAGE_CENSUS_KEYS:
        _fail("A fresh PDF OCR row omitted its page-disposition evidence")
    census = _validated_pdf_page_census(value.get("pdf_page_census"))
    ocr_pages = tuple(census["ocr_text_page_numbers"])
    image_pages = tuple(census["image_only_page_numbers"])
    watermark_pages = tuple(census["watermark_only_page_numbers"])
    raw_ocr_pages = value.get("ocr_page_numbers")
    raw_preclassified_pages = value.get("preclassified_page_numbers")
    if raw_ocr_pages != list(ocr_pages) or raw_preclassified_pages != list(watermark_pages):
        _fail("A fresh PDF OCR row drifted from its page-disposition census")
    if watermark_pages != _preclassified_page_numbers(watermark_receipt):
        _fail("A fresh PDF watermark page set drifted from its signed receipt")
    required_pages = set(ocr_pages) | set(image_pages) | set(watermark_pages)
    if _required_nonnegative_int(value, "ocr_required_page_count") != len(
        required_pages
    ) or _required_nonnegative_int(value, "ocr_page_count") != len(ocr_pages):
        _fail("A fresh PDF OCR page-resolution census is inconsistent")
    ocr_span_count = _required_nonnegative_int(value, "ocr_span_count")
    confidence_count = _required_nonnegative_int(value, "confidence_count")
    if (
        ocr_span_count > durable_span_count
        or bool(ocr_pages) != bool(ocr_span_count)
        or confidence_count != len(ocr_pages)
    ):
        _fail("A fresh PDF OCR locator/confidence census is inconsistent")
    if ocr_pages:
        mean_confidence = _required_confidence(value.get("mean_confidence"))
        minimum_confidence = _required_confidence(value.get("minimum_confidence"))
        maximum_confidence = _required_confidence(value.get("maximum_confidence"))
        if minimum_confidence > mean_confidence or mean_confidence > maximum_confidence:
            _fail("A fresh PDF OCR confidence census is inconsistent")
    elif any(
        value.get(key) is not None
        for key in ("mean_confidence", "minimum_confidence", "maximum_confidence")
    ):
        _fail("A zero-text PDF OCR row carries fabricated confidence")
    admissions = _validated_page_bound_render_admissions(
        value.get("ocr_render_admissions"),
        required_schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    )
    if [admission["page"] for admission in admissions] != sorted(
        (*ocr_pages, *image_pages)
    ) or _required_sha256(
        value, "ocr_render_admissions_sha256"
    ) != _canonical_sequence_of_mappings_sha256(admissions):
        _fail("A fresh PDF OCR render-admission census is inconsistent")
    rescue_page_count = _required_nonnegative_int(value, "ocr_rescue_page_count")
    rescue_span_count = _required_nonnegative_int(value, "ocr_rescue_span_count")
    rescue_receipt_sha256 = _required_sha256(value, "ocr_rescue_receipt_sha256")
    _required_sha256(value, "locator_contract_sha256")
    if (
        rescue_page_count > len(ocr_pages)
        or rescue_span_count > ocr_span_count
        or (
            rescue_page_count == 0
            and (
                rescue_span_count != 0
                or rescue_receipt_sha256 != _canonical_sequence_of_mappings_sha256([])
            )
        )
        or (rescue_page_count > 0 and rescue_span_count == 0)
    ):
        _fail("A fresh PDF OCR rescue census is inconsistent")
    if value.get("pdf_page_census") != census:
        _fail("A fresh PDF page-disposition census was not canonically persisted")


def _validate_safe_ocr_evidence_page_resolution(
    value: Mapping[str, Any],
    *,
    extension: str,
    durable_span_count: int,
    watermark_receipt: Mapping[str, Any] | None,
    required_render_schema_version: str,
) -> None:
    if extension != "pdf" or set(value) != _OCR_EVIDENCE_PAGE_RESOLUTION_KEYS:
        _fail("A fresh PDF OCR row omitted its exact page-resolution evidence")
    raw_ocr_pages = value.get("ocr_page_numbers")
    raw_preclassified_pages = value.get("preclassified_page_numbers")
    if not isinstance(raw_ocr_pages, list) or not isinstance(
        raw_preclassified_pages,
        list,
    ):
        _fail("A fresh PDF OCR row omitted its page sets")
    ocr_pages = tuple(_required_page_number(page) for page in raw_ocr_pages)
    preclassified_pages = tuple(_required_page_number(page) for page in raw_preclassified_pages)
    if (
        ocr_pages != tuple(sorted(set(ocr_pages)))
        or preclassified_pages != tuple(sorted(set(preclassified_pages)))
        or set(ocr_pages) & set(preclassified_pages)
        or preclassified_pages != _preclassified_page_numbers(watermark_receipt)
    ):
        _fail("A fresh PDF OCR row has overlapping or drifted page sets")
    required_page_count = _required_positive_int(value, "ocr_required_page_count")
    page_count = _required_nonnegative_int(value, "ocr_page_count")
    ocr_span_count = _required_nonnegative_int(value, "ocr_span_count")
    confidence_count = _required_nonnegative_int(value, "confidence_count")
    if (
        page_count != len(ocr_pages)
        or confidence_count != page_count
        or len(set(ocr_pages) | set(preclassified_pages)) != required_page_count
        or ocr_span_count > durable_span_count
        or bool(ocr_pages) != bool(ocr_span_count)
    ):
        _fail("A fresh PDF OCR page-resolution census is inconsistent")

    if ocr_pages:
        mean_confidence = _required_confidence(value.get("mean_confidence"))
        minimum_confidence = _required_confidence(value.get("minimum_confidence"))
        maximum_confidence = _required_confidence(value.get("maximum_confidence"))
        if minimum_confidence > mean_confidence or mean_confidence > maximum_confidence:
            _fail("A fresh PDF OCR confidence census is inconsistent")
    elif any(
        value.get(key) is not None
        for key in ("mean_confidence", "minimum_confidence", "maximum_confidence")
    ):
        _fail("A preclassified-only PDF carries fabricated OCR confidence")

    admissions = _validated_page_bound_render_admissions(
        value.get("ocr_render_admissions"),
        required_schema_version=required_render_schema_version,
    )
    admissions_sha256 = _required_sha256(value, "ocr_render_admissions_sha256")
    if [admission["page"] for admission in admissions] != list(
        ocr_pages
    ) or admissions_sha256 != _canonical_sequence_of_mappings_sha256(admissions):
        _fail("A fresh PDF OCR render-admission census is inconsistent")

    rescue_page_count = _required_nonnegative_int(value, "ocr_rescue_page_count")
    rescue_span_count = _required_nonnegative_int(value, "ocr_rescue_span_count")
    rescue_receipt_sha256 = _required_sha256(value, "ocr_rescue_receipt_sha256")
    _required_sha256(value, "locator_contract_sha256")
    if (
        rescue_page_count > page_count
        or rescue_span_count > ocr_span_count
        or (
            not rescue_page_count
            and (
                rescue_span_count != 0
                or rescue_receipt_sha256 != _canonical_sequence_of_mappings_sha256([])
            )
        )
        or (rescue_page_count and not rescue_span_count)
    ):
        _fail("A fresh PDF OCR rescue census is inconsistent")


def _reject_legacy_oversize_failures(rows: Sequence[Mapping[str, Any]]) -> None:
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        codes = _code_set(row.get("reason_codes")) | _code_set(row.get("error_codes"))
        if codes & _MAX_SIZE_REASON_CODES:
            raise TerminalIngestionReconciliationError(
                INGEST_ADMISSION_MAX_SIZE_EXCEEDED,
                "A legacy maximum-size failure remains in the durable corpus",
            )


def _validate_deal_isolation(deals_by_company: Mapping[str, set[str]]) -> None:
    if any(len(deal_ids) != 1 for deal_ids in deals_by_company.values()):
        _fail("A source company is not isolated to exactly one durable deal")
    deal_ids = [next(iter(values)) for values in deals_by_company.values()]
    if len(set(deal_ids)) != len(deal_ids):
        _fail("Two source companies share a durable deal")


def _required_string(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        _fail("A durable terminal row omitted a required string")
    return value


def _required_persistent_backend(row: Mapping[str, Any], key: str) -> str:
    value = _required_string(row, key)
    lowered = value.lower()
    if _SAFE_BACKEND_RE.fullmatch(value) is None or any(
        marker in lowered for marker in _NON_PERSISTENT_BACKEND_MARKERS
    ):
        _fail("A durable object proof does not name a persistent backend")
    return value


def _required_nonnegative_int(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    if type(value) is not int or value < 0:
        _fail("A durable terminal row omitted a required integer")
    return value


def _required_positive_int(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    if type(value) is not int or value <= 0:
        _fail("A durable evidence row omitted a required positive integer")
    return value


def _required_page_number(value: Any) -> int:
    if type(value) is not int or value < 1:
        _fail("A durable OCR page set contains an invalid page number")
    return value


def _required_bool(row: Mapping[str, Any], key: str) -> bool:
    value = row.get(key)
    if type(value) is not bool:
        _fail("A durable terminal row omitted a required boolean proof")
    return value


def _required_uuid(row: Mapping[str, Any], key: str) -> str:
    value = _required_string(row, key)
    try:
        parsed = str(uuid.UUID(value))
    except ValueError:
        _fail("A durable terminal row contains an invalid identifier")
    if parsed != value:
        _fail("A durable terminal row identifier is not canonical")
    return value


def _required_sha256(row: Mapping[str, Any], key: str) -> str:
    value = _required_string(row, key)
    if not _SHA256_RE.fullmatch(value):
        _fail("A durable terminal row contains an invalid digest")
    return value


def _required_extension(row: Mapping[str, Any], key: str) -> str:
    value = _required_string(row, key)
    if not _SAFE_EXTENSION_RE.fullmatch(value):
        _fail("A durable terminal row contains an invalid extension")
    return value


def _admit_unique(seen: set[str], value: str, label: str) -> None:
    if value in seen:
        _fail(f"A durable {label} is bound to more than one source row")
    seen.add(value)


def _empty_code_list(value: Any) -> bool:
    return isinstance(value, list) and not value


def _code_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _strict_int(value: Any) -> int | None:
    return value if type(value) is int else None


def _required_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("An OCR confidence proof is missing or invalid")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        _fail("An OCR confidence proof is missing or invalid")
    return normalized


def _optional_confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        return None
    return normalized


def _same_optional_confidence(value: Any, expected: float) -> bool:
    normalized = _optional_confidence(value)
    return normalized is not None and math.isclose(
        normalized,
        expected,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def _is_nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _is_positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _valid_company_alias(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"company-[0-9]{3}", value) is not None


def _fail(message: str) -> Never:
    raise TerminalIngestionReconciliationError(
        DATA_ROOM_RECONCILIATION_INCOMPLETE,
        message,
    )


def _fail_with_code(code: str, message: str) -> Never:
    raise TerminalIngestionReconciliationError(code, message)


__all__ = [
    "DATA_ROOM_RECONCILIATION_INCOMPLETE",
    "INGEST_ADMISSION_MAX_SIZE_EXCEEDED",
    "MEDIA_NO_EXTRACTABLE_EVIDENCE",
    "OCR_TERMINAL_LEGACY_EVIDENCE_SCHEMA_VERSION",
    "OCR_TERMINAL_PRIOR_EVIDENCE_SCHEMA_VERSION",
    "OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION",
    "PDF_OCR_REQUIREMENTS_SCHEMA_VERSION",
    "TERMINAL_INGESTION_RECONCILIATION_SCHEMA_VERSION",
    "WATERMARK_PAGE_SANITY_CAP_EXCEEDED",
    "PdfOcrRequirementsCensus",
    "TerminalIngestionReconciliationError",
    "collect_durable_terminal_rows",
    "inspect_pdf_ocr_requirements",
    "reconcile_terminal_ingestion",
]
