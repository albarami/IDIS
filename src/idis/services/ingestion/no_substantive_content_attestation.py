"""Signed, one-document admission for confirmed non-substantive content.

The attestation never creates evidence text or a parsed document.  It permits
only a durable, zero-span terminal exclusion after the complete admitted OCR
and rescue policy has finished without an accepted text clique.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Never

from idis.parsers.base import ParseResult
from idis.parsers.ocr import (
    OCR_NO_CONSENSUS_FILE_PROOF_SCHEMA_VERSION,
    OCR_RESCUE_MATRIX,
    OCR_RESCUE_POLICY_SHA256,
    OCR_RESCUE_POLICY_VERSION,
    OcrError,
    validate_ocr_no_consensus_file_proof_payload,
)
from idis.parsers.watermark_page_classifier import (
    WATERMARK_PAGE_CLASSIFICATION_V2_FIELDS,
    WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION,
    WATERMARK_RUNTIME_CANDIDATE_RENDER_POLICY_ID,
    WATERMARK_RUNTIME_CANDIDATE_TRANSFORM_CHAIN,
    default_watermark_runtime_candidate_render_policy,
)
from idis.runtime.governance_envelopes import (
    GOVERNANCE_SCHEMA_INVALID,
    NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION,
    NO_SUBSTANTIVE_CONTENT_VARIANTS,
    WATERMARK_BATCH_ATTESTATION_SCHEMA_VERSION,
    GovernanceEnvelopeError,
    VerifiedGovernanceEnvelope,
    canonicalize_json,
    verify_envelope,
)
from idis.runtime.secure_files import SecureFileError, secure_read_integrity_file

NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV = (
    "IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH"
)
NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION = "document-no-substantive-content-receipt-v1"
NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION = "document-no-substantive-content-receipt-v2"
WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION = "watermark-page-classification-v1"
DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED = "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION = (
    "document-no-substantive-content-attestation-manifest-v2"
)
WATERMARK_BATCH_PAYLOAD_FIELDS = (
    "tenant_id",
    "source_tree_fingerprint",
    "disposition",
    "variant",
    "classifier_policy_id",
    "classifier_policy_sha256",
    "exemplar_safe_alias",
    "exemplar_content_sha256",
    "exemplar_envelope_sha256",
    "exemplar_canonical_bitmap_sha256",
    "prescan_receipt_sha256",
    "candidate_set_sha256",
    "candidates",
    "sample_set_sha256",
    "attestation_text_sha256",
    "reviewer_approval",
)
WATERMARK_BATCH_CANDIDATE_FIELDS = (
    "safe_alias",
    "content_sha256",
    "page_number",
    "canonical_render_sha256",
    "similarity_score",
)
NO_SUBSTANTIVE_CONTENT_MANIFEST_FIELDS = (
    "schema_version",
    "exemplar_attestation_envelope",
    "exemplar_canonical_bitmap_base64",
    "watermark_prescan_receipt",
    "watermark_batch_attestation_envelope",
)

WATERMARK_BATCH_ATTESTATION_INVALID = "WATERMARK_BATCH_ATTESTATION_INVALID"
WATERMARK_BATCH_ATTESTATION_MISMATCH = "WATERMARK_BATCH_ATTESTATION_MISMATCH"
WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE = "WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE"
WATERMARK_PRESCAN_INCOMPLETE = "WATERMARK_PRESCAN_INCOMPLETE"
WATERMARK_PAGE_SANITY_CAP_EXCEEDED = "WATERMARK_PAGE_SANITY_CAP_EXCEEDED"

DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID = (
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID"
)
DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED = (
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED"
)
DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH = (
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH"
)
DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_NOT_ACTIVE = (
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_NOT_ACTIVE"
)
DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE = (
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"
)
DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED = (
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED"
)

_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_PUBLIC_KEY_BYTES = 16 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ALIAS_RE = re.compile(r"^company-00[12]-document-[0-9]{5}\.pdf$")


@dataclass(frozen=True, slots=True)
class NoSubstantiveContentManifestV2SafeSummaryContract:
    """One immutable field and value contract for every manifest-v2 consumer."""

    base_fields: frozenset[str] = frozenset(
        {
            "schema_version",
            "status",
            "entry_count",
            "manual_attestation_count",
            "watermark_batch_candidate_count",
            "disposition",
            "variant",
            "classifier_policy_id",
            "classifier_policy_sha256",
            "manifest_sha256",
            "exemplar_envelope_sha256",
            "exemplar_canonical_bitmap_sha256",
            "prescan_receipt_sha256",
            "candidate_set_sha256",
            "sample_set_sha256",
            "batch_envelope_sha256",
            "public_key_sha256",
            "signature_verified",
        }
    )
    reviewed_image_fields: frozenset[str] = frozenset(
        {
            "reviewed_image_only_page_count",
            "reviewed_image_only_set_sha256",
        }
    )
    hash_fields: frozenset[str] = frozenset(
        {
            "classifier_policy_sha256",
            "manifest_sha256",
            "exemplar_envelope_sha256",
            "exemplar_canonical_bitmap_sha256",
            "prescan_receipt_sha256",
            "candidate_set_sha256",
            "sample_set_sha256",
            "batch_envelope_sha256",
            "public_key_sha256",
        }
    )

    def is_valid(self, value: object) -> bool:
        """Return whether ``value`` is exactly one of the two approved projections."""

        if not isinstance(value, Mapping):
            return False
        fields = frozenset(value)
        has_reviewed_images = fields == self.base_fields | self.reviewed_image_fields
        if fields != self.base_fields and not has_reviewed_images:
            return False
        if (
            value.get("schema_version") != NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION
            or value.get("status") != "verified"
            or type(value.get("entry_count")) is not int
            or value.get("entry_count") != 2
            or type(value.get("manual_attestation_count")) is not int
            or value.get("manual_attestation_count") != 1
            or type(value.get("watermark_batch_candidate_count")) is not int
            or value.get("watermark_batch_candidate_count") != 2
            or value.get("disposition") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
            or value.get("variant") != "watermark_only"
            or value.get("classifier_policy_id") != "exemplar-watermark-page-v1"
            or value.get("signature_verified") is not True
            or any(
                not isinstance(value.get(name), str)
                or _SHA256_RE.fullmatch(str(value[name])) is None
                for name in self.hash_fields
            )
        ):
            return False
        if not has_reviewed_images:
            return True
        reviewed_count = value.get("reviewed_image_only_page_count")
        reviewed_set_sha256 = value.get("reviewed_image_only_set_sha256")
        return (
            type(reviewed_count) is int
            and reviewed_count > 0
            and isinstance(reviewed_set_sha256, str)
            and _SHA256_RE.fullmatch(reviewed_set_sha256) is not None
        )


NO_SUBSTANTIVE_CONTENT_MANIFEST_V2_SAFE_SUMMARY_CONTRACT = (
    NoSubstantiveContentManifestV2SafeSummaryContract()
)
_WATERMARK_PRESCAN_FIELDS = frozenset(
    {
        "schema_version",
        "source_tree_fingerprint",
        "classifier_policy_id",
        "classifier_policy_sha256",
        "total_file_count",
        "company_file_counts",
        "candidate_count",
        "candidate_set_sha256",
        "sample_set_sha256",
        "unresolved_count",
        "status",
        "blocker_code",
    }
)
_WATERMARK_RESOLVED_PRESCAN_EXTENSION_FIELDS = frozenset(
    {
        "prior_blocked_prescan_receipt_sha256",
        "prior_blocked_prescan_file_sha256",
        "prior_unresolved_set_sha256",
        "reviewed_image_only_page_count",
        "reviewed_image_only_pages",
        "reviewed_image_only_set_sha256",
        "reviewer_approval",
    }
)
_WATERMARK_REVIEWED_IMAGE_PAGE_FIELDS = frozenset(
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
_REVIEWER_APPROVAL_FIELDS = frozenset({"decision", "reviewer_id", "approved_at", "expires_at"})
NO_SUBSTANTIVE_CONTENT_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "disposition",
        "variant",
        "safe_alias",
        "content_sha256",
        "source_tree_fingerprint",
        "attestation_text_sha256",
        "reviewer_id",
        "approved_at",
        "expires_at",
        "signer_id",
        "payload_sha256",
        "envelope_sha256",
        "public_key_sha256",
        "signature_verified",
        "parser_error_code",
        "parser_runtime",
        "parser_runtime_status",
        "page_count",
        "ocr_required_page_count",
        "primary_completed_page_count",
        "rescue_completed_page_count",
        "accepted_text_page_count",
        "span_count",
        "ocr_result_sha256",
    }
)
NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_FIELDS = frozenset(
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
WATERMARK_PAGE_CLASSIFICATION_FIELDS = frozenset(
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
WATERMARK_PAGE_CLASSIFICATION_V2_RECEIPT_FIELDS = frozenset(
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
_WATERMARK_EXEMPLAR_REFERENCE_FIELDS = frozenset(
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
_WATERMARK_CONFIDENCE_DISTRIBUTION_FIELDS = frozenset(
    {"near_white_fraction", "foreground_ink_fraction"}
)
_WATERMARK_MANIFEST_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "tenant_id",
        "source_tree_fingerprint",
        "disposition",
        "variant",
        "classifier_policy_id",
        "classifier_policy_sha256",
        "exemplar_safe_alias",
        "exemplar_page_number",
        "exemplar_content_sha256",
        "exemplar_envelope_sha256",
        "exemplar_canonical_bitmap_sha256",
        "manifest_sha256",
        "batch_envelope_sha256",
        "candidate_set_sha256",
        "public_key_sha256",
        "candidate_count",
        "candidates",
        "company_file_counts",
        "signature_verified",
    }
)
_FIXED_SIX_DECIMAL_RE = re.compile(r"^(?:0|1)\.[0-9]{6}$")


class DocumentNoSubstantiveContentAttestationError(RuntimeError):
    """Secret-safe failure at the signed terminal-exclusion boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> Never:
    raise DocumentNoSubstantiveContentAttestationError(code) from None


def _utc_instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _active_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_NOT_ACTIVE)
    return value.astimezone(UTC)


def _same(expected: str, actual: object) -> bool:
    return isinstance(actual, str) and hmac.compare_digest(expected, actual)


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedDocumentNoSubstantiveContentAttestation:
    """Verified binding retained without signature bytes, paths, or review text."""

    schema_version: str
    signer_id: str
    tenant_id: str
    source_tree_fingerprint: str
    safe_alias: str
    content_sha256: str
    disposition: str
    variant: str
    attestation_text_sha256: str
    reviewer_id: str
    approved_at_text: str
    expires_at_text: str
    approved_at: datetime
    expires_at: datetime
    payload_sha256: str
    envelope_sha256: str
    public_key_sha256: str

    def require_active(self, *, now: datetime) -> VerifiedDocumentNoSubstantiveContentAttestation:
        """Require the signed half-open approval interval at the use instant."""

        instant = _active_utc(now)
        if instant < self.approved_at or instant >= self.expires_at:
            _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_NOT_ACTIVE)
        return self

    def require_match(
        self,
        *,
        tenant_id: str,
        source_tree_fingerprint: str,
        safe_alias: str,
        content_sha256: str,
    ) -> VerifiedDocumentNoSubstantiveContentAttestation:
        """Require all four immutable source/package bindings."""

        if not all(
            (
                _same(self.tenant_id, tenant_id),
                _same(self.source_tree_fingerprint, source_tree_fingerprint),
                _same(self.safe_alias, safe_alias),
                _same(self.content_sha256, content_sha256),
            )
        ):
            _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH)
        return self

    def safe_summary(self) -> dict[str, object]:
        """Return only admission status and governance integrity hashes."""

        return {
            "schema_version": self.schema_version,
            "status": "verified",
            "entry_count": 1,
            "disposition": self.disposition,
            "variant": self.variant,
            "payload_sha256": self.payload_sha256,
            "envelope_sha256": self.envelope_sha256,
            "public_key_sha256": self.public_key_sha256,
            "signature_verified": True,
        }

    def __repr__(self) -> str:
        return (
            "VerifiedDocumentNoSubstantiveContentAttestation("
            f"schema_version={self.schema_version!r}, status='verified', "
            f"disposition={self.disposition!r}, "
            f"variant={self.variant!r}, "
            f"payload_sha256={self.payload_sha256!r}, "
            f"envelope_sha256={self.envelope_sha256!r}, "
            f"public_key_sha256={self.public_key_sha256!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedWatermarkBatchAttestation:
    """Verified batch metadata without signatures, bitmap bytes, or review prose."""

    schema_version: str
    signer_id: str
    payload: Mapping[str, object]
    candidates: tuple[Mapping[str, object], ...]
    candidate_set_sha256: str
    prescan_receipt_sha256: str
    sample_set_sha256: str
    payload_sha256: str
    envelope_sha256: str
    public_key_sha256: str
    approved_at: datetime
    expires_at: datetime

    def __repr__(self) -> str:
        return (
            "VerifiedWatermarkBatchAttestation("
            f"schema_version={self.schema_version!r}, status='verified', "
            f"candidate_count={len(self.candidates)}, "
            f"candidate_set_sha256={self.candidate_set_sha256!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedDocumentNoSubstantiveContentAttestationManifest:
    """Verified v2 manifest joining the manual exemplar and reviewed batch."""

    schema_version: str
    exemplar: VerifiedDocumentNoSubstantiveContentAttestation
    batch: VerifiedWatermarkBatchAttestation
    exemplar_envelope_sha256: str
    exemplar_canonical_bitmap_sha256: str
    manifest_sha256: str
    company_file_counts: Mapping[str, int]
    exemplar_canonical_bitmap: bytes = dataclass_field(repr=False, compare=False)
    reviewed_image_only_pages: tuple[Mapping[str, object], ...] = ()
    reviewed_image_only_set_sha256: str | None = None

    def safe_summary(self) -> dict[str, object]:
        """Project only governance hashes and safe aggregate admission facts."""

        payload = self.batch.payload
        summary: dict[str, object] = {
            "schema_version": self.schema_version,
            "status": "verified",
            "entry_count": len(self.batch.candidates),
            "manual_attestation_count": 1,
            "watermark_batch_candidate_count": len(self.batch.candidates),
            "disposition": self.exemplar.disposition,
            "variant": "watermark_only",
            "classifier_policy_id": payload["classifier_policy_id"],
            "classifier_policy_sha256": payload["classifier_policy_sha256"],
            "manifest_sha256": self.manifest_sha256,
            "exemplar_envelope_sha256": self.exemplar_envelope_sha256,
            "exemplar_canonical_bitmap_sha256": self.exemplar_canonical_bitmap_sha256,
            "candidate_set_sha256": self.batch.candidate_set_sha256,
            "prescan_receipt_sha256": self.batch.prescan_receipt_sha256,
            "sample_set_sha256": self.batch.sample_set_sha256,
            "batch_envelope_sha256": self.batch.envelope_sha256,
            "public_key_sha256": self.batch.public_key_sha256,
            "signature_verified": True,
        }
        if self.reviewed_image_only_set_sha256 is not None:
            summary.update(
                {
                    "reviewed_image_only_page_count": len(self.reviewed_image_only_pages),
                    "reviewed_image_only_set_sha256": self.reviewed_image_only_set_sha256,
                }
            )
        return summary

    def runtime_summary(self) -> dict[str, object]:
        """Return the exact hash-bound admission view used by durable receipts."""

        payload = self.batch.payload
        return {
            "schema_version": self.schema_version,
            "status": "verified",
            "tenant_id": self.exemplar.tenant_id,
            "source_tree_fingerprint": self.exemplar.source_tree_fingerprint,
            "disposition": self.exemplar.disposition,
            "variant": self.exemplar.variant,
            "classifier_policy_id": payload["classifier_policy_id"],
            "classifier_policy_sha256": payload["classifier_policy_sha256"],
            "exemplar_safe_alias": self.exemplar.safe_alias,
            "exemplar_page_number": 1,
            "exemplar_content_sha256": self.exemplar.content_sha256,
            "exemplar_envelope_sha256": self.exemplar_envelope_sha256,
            "exemplar_canonical_bitmap_sha256": (self.exemplar_canonical_bitmap_sha256),
            "manifest_sha256": self.manifest_sha256,
            "batch_envelope_sha256": self.batch.envelope_sha256,
            "candidate_set_sha256": self.batch.candidate_set_sha256,
            "public_key_sha256": self.batch.public_key_sha256,
            "candidate_count": len(self.batch.candidates),
            "candidates": [dict(candidate) for candidate in self.batch.candidates],
            "company_file_counts": dict(self.company_file_counts),
            "signature_verified": True,
        }

    def __repr__(self) -> str:
        return (
            "VerifiedDocumentNoSubstantiveContentAttestationManifest("
            f"schema_version={self.schema_version!r}, status='verified', "
            f"candidate_count={len(self.batch.candidates)})"
        )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonicalize_json(value)).hexdigest()


def _attestation_from_verified(
    verified: VerifiedGovernanceEnvelope,
    *,
    invalid_code: str,
) -> VerifiedDocumentNoSubstantiveContentAttestation:
    if verified.schema_version != NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION:
        _fail(invalid_code)
    payload = verified.payload
    approval = payload.get("reviewer_approval")
    if not isinstance(approval, Mapping):
        _fail(invalid_code)
    approved_at_text = approval.get("approved_at")
    expires_at_text = approval.get("expires_at")
    approved_at = _utc_instant(approved_at_text)
    expires_at = _utc_instant(expires_at_text)
    required_strings = {
        "tenant_id": payload.get("tenant_id"),
        "source_tree_fingerprint": payload.get("source_tree_fingerprint"),
        "safe_alias": payload.get("safe_alias"),
        "content_sha256": payload.get("content_sha256"),
        "disposition": payload.get("disposition"),
        "variant": payload.get("variant"),
        "attestation_text_sha256": payload.get("attestation_text_sha256"),
        "reviewer_id": approval.get("reviewer_id"),
        "approved_at_text": approved_at_text,
        "expires_at_text": expires_at_text,
    }
    if (
        approved_at is None
        or expires_at is None
        or any(not isinstance(value, str) for value in required_strings.values())
    ):
        _fail(invalid_code)
    return VerifiedDocumentNoSubstantiveContentAttestation(
        schema_version=verified.schema_version,
        signer_id=verified.signer_id,
        tenant_id=str(required_strings["tenant_id"]),
        source_tree_fingerprint=str(required_strings["source_tree_fingerprint"]),
        safe_alias=str(required_strings["safe_alias"]),
        content_sha256=str(required_strings["content_sha256"]),
        disposition=str(required_strings["disposition"]),
        variant=str(required_strings["variant"]),
        attestation_text_sha256=str(required_strings["attestation_text_sha256"]),
        reviewer_id=str(required_strings["reviewer_id"]),
        approved_at_text=str(required_strings["approved_at_text"]),
        expires_at_text=str(required_strings["expires_at_text"]),
        approved_at=approved_at,
        expires_at=expires_at,
        payload_sha256=verified.payload_sha256,
        envelope_sha256=verified.envelope_sha256,
        public_key_sha256=verified.public_key_sha256,
    )


def _verify_governance_for_batch(
    envelope: object,
    *,
    trusted_signer_id: str,
    trusted_public_key_pem: bytes,
) -> VerifiedGovernanceEnvelope:
    if not isinstance(envelope, Mapping):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    try:
        return verify_envelope(
            envelope=envelope,
            trusted_signer_id=trusted_signer_id,
            trusted_public_key_pem=trusted_public_key_pem,
        )
    except GovernanceEnvelopeError as exc:
        code = (
            WATERMARK_BATCH_ATTESTATION_INVALID
            if exc.code == GOVERNANCE_SCHEMA_INVALID
            else exc.code
        )
        raise DocumentNoSubstantiveContentAttestationError(code) from None
    except RecursionError:
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)


def _valid_reviewed_image_only_pages(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or not value:
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    rows: list[Mapping[str, object]] = []
    identities: set[tuple[str, int]] = set()
    previous_identity: tuple[str, int] | None = None
    for value_row in value:
        if (
            not isinstance(value_row, Mapping)
            or frozenset(value_row) != _WATERMARK_REVIEWED_IMAGE_PAGE_FIELDS
        ):
            _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
        alias = value_row.get("safe_alias")
        page_number = value_row.get("page_number")
        if (
            not isinstance(alias, str)
            or _SAFE_ALIAS_RE.fullmatch(alias) is None
            or isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or page_number < 1
            or value_row.get("reviewer_disposition") != "SUBSTANTIVE_IMAGE"
            or any(
                not isinstance(value_row.get(name), str)
                or _SHA256_RE.fullmatch(str(value_row.get(name))) is None
                for name in (
                    "content_sha256",
                    "page_content_sha256",
                    "ocr_terminal_proof_sha256",
                    "watermark_classifier_receipt_sha256",
                )
            )
        ):
            _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
        identity = (alias, page_number)
        if identity in identities or (
            previous_identity is not None and identity <= previous_identity
        ):
            _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
        identities.add(identity)
        previous_identity = identity
        rows.append(value_row)
    return tuple(rows)


def _valid_resolution_approval(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or frozenset(value) != _REVIEWER_APPROVAL_FIELDS:
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    approved_at = _utc_instant(value.get("approved_at"))
    expires_at = _utc_instant(value.get("expires_at"))
    reviewer_id = value.get("reviewer_id")
    if (
        value.get("decision") != "APPROVED"
        or not isinstance(reviewer_id, str)
        or not reviewer_id
        or approved_at is None
        or expires_at is None
        or expires_at <= approved_at
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    return value


def _valid_prescan_receipt(receipt: object) -> Mapping[str, object]:
    if not isinstance(receipt, Mapping):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    fields = frozenset(receipt)
    if fields not in {
        _WATERMARK_PRESCAN_FIELDS,
        _WATERMARK_PRESCAN_FIELDS | _WATERMARK_RESOLVED_PRESCAN_EXTENSION_FIELDS,
    }:
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    company_counts = receipt.get("company_file_counts")
    if (
        receipt.get("schema_version") != "watermark-page-prescan-v1"
        or not isinstance(receipt.get("source_tree_fingerprint"), str)
        or _SHA256_RE.fullmatch(str(receipt.get("source_tree_fingerprint"))) is None
        or receipt.get("classifier_policy_id") != "exemplar-watermark-page-v1"
        or not isinstance(receipt.get("classifier_policy_sha256"), str)
        or _SHA256_RE.fullmatch(str(receipt.get("classifier_policy_sha256"))) is None
        or receipt.get("total_file_count") != 267
        or not isinstance(company_counts, Mapping)
        or company_counts != {"company-001": 58, "company-002": 209}
        or isinstance(receipt.get("candidate_count"), bool)
        or not isinstance(receipt.get("candidate_count"), int)
        or int(receipt["candidate_count"]) < 1
        or not isinstance(receipt.get("candidate_set_sha256"), str)
        or _SHA256_RE.fullmatch(str(receipt.get("candidate_set_sha256"))) is None
        or not isinstance(receipt.get("sample_set_sha256"), str)
        or _SHA256_RE.fullmatch(str(receipt.get("sample_set_sha256"))) is None
        or isinstance(receipt.get("unresolved_count"), bool)
        or not isinstance(receipt.get("unresolved_count"), int)
        or int(receipt["unresolved_count"]) < 0
        or receipt.get("status") not in {"COMPLETE", "BLOCKED_PENDING_REVIEW"}
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    if receipt.get("status") == "COMPLETE":
        if receipt.get("unresolved_count") != 0 or receipt.get("blocker_code") is not None:
            _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    elif (
        int(receipt["unresolved_count"]) < 1
        or receipt.get("blocker_code") != WATERMARK_PRESCAN_INCOMPLETE
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    if fields == _WATERMARK_PRESCAN_FIELDS | _WATERMARK_RESOLVED_PRESCAN_EXTENSION_FIELDS:
        if receipt.get("status") != "COMPLETE" or receipt.get("unresolved_count") != 0:
            _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
        if any(
            not isinstance(receipt.get(name), str)
            or _SHA256_RE.fullmatch(str(receipt.get(name))) is None
            for name in (
                "prior_blocked_prescan_receipt_sha256",
                "prior_blocked_prescan_file_sha256",
                "prior_unresolved_set_sha256",
                "reviewed_image_only_set_sha256",
            )
        ):
            _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
        reviewed_pages = _valid_reviewed_image_only_pages(receipt.get("reviewed_image_only_pages"))
        if receipt.get("reviewed_image_only_page_count") != len(reviewed_pages) or not _same(
            _sha256_json(list(reviewed_pages)),
            receipt.get("reviewed_image_only_set_sha256"),
        ):
            _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
        _valid_resolution_approval(receipt.get("reviewer_approval"))
    return receipt


def _batch_review_interval(payload: Mapping[str, object]) -> tuple[datetime, datetime]:
    approval = payload.get("reviewer_approval")
    if not isinstance(approval, Mapping):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    approved_at = _utc_instant(approval.get("approved_at"))
    expires_at = _utc_instant(approval.get("expires_at"))
    if approved_at is None or expires_at is None:
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    return approved_at, expires_at


def _require_batch_active(
    *,
    now: datetime,
    batch_approved_at: datetime,
    batch_expires_at: datetime,
    exemplar: VerifiedDocumentNoSubstantiveContentAttestation,
) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None:
        _fail(WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE)
    instant = now.astimezone(UTC)
    if (
        instant < batch_approved_at
        or instant >= batch_expires_at
        or instant < exemplar.approved_at
        or instant >= exemplar.expires_at
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE)


def verify_document_no_substantive_content_attestation_manifest(
    *,
    manifest: Mapping[str, object],
    trusted_signer_id: str,
    trusted_public_key_pem: bytes,
    now: datetime,
) -> VerifiedDocumentNoSubstantiveContentAttestationManifest:
    """Verify an exact v2 exemplar-plus-batch manifest and all cross-bindings."""

    if (
        not isinstance(manifest, Mapping)
        # JSON object member order is not semantic, and canonical governance
        # serialization sorts members before durable storage.
        or frozenset(manifest) != frozenset(NO_SUBSTANTIVE_CONTENT_MANIFEST_FIELDS)
        or manifest.get("schema_version") != NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)

    bitmap_base64 = manifest.get("exemplar_canonical_bitmap_base64")
    if not isinstance(bitmap_base64, str):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    try:
        canonical_bitmap = base64.b64decode(bitmap_base64, validate=True)
    except (binascii.Error, ValueError):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    if len(canonical_bitmap) != 2048:
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)

    exemplar_verified = _verify_governance_for_batch(
        manifest.get("exemplar_attestation_envelope"),
        trusted_signer_id=trusted_signer_id,
        trusted_public_key_pem=trusted_public_key_pem,
    )
    exemplar = _attestation_from_verified(
        exemplar_verified,
        invalid_code=WATERMARK_BATCH_ATTESTATION_INVALID,
    )
    batch_verified = _verify_governance_for_batch(
        manifest.get("watermark_batch_attestation_envelope"),
        trusted_signer_id=trusted_signer_id,
        trusted_public_key_pem=trusted_public_key_pem,
    )
    if batch_verified.schema_version != WATERMARK_BATCH_ATTESTATION_SCHEMA_VERSION:
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)

    prescan = _valid_prescan_receipt(manifest.get("watermark_prescan_receipt"))
    payload = batch_verified.payload
    candidates_value = payload.get("candidates")
    if not isinstance(candidates_value, list) or any(
        not isinstance(candidate, Mapping) for candidate in candidates_value
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_INVALID)
    candidates = tuple(
        candidate for candidate in candidates_value if isinstance(candidate, Mapping)
    )
    candidate_set_sha256 = _sha256_json(candidates_value)
    prescan_receipt_sha256 = _sha256_json(prescan)
    exemplar_bitmap_sha256 = hashlib.sha256(canonical_bitmap).hexdigest()

    matching_exemplar_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("safe_alias") == exemplar.safe_alias
        and candidate.get("content_sha256") == exemplar.content_sha256
        and candidate.get("page_number") == 1
        and _same(
            exemplar_bitmap_sha256,
            candidate.get("canonical_render_sha256"),
        )
        and candidate.get("similarity_score") == "1.000000"
    ]
    exemplar_is_candidate = len(matching_exemplar_candidates) == 1
    if not all(
        (
            _same(exemplar.tenant_id, payload.get("tenant_id")),
            _same(exemplar.source_tree_fingerprint, payload.get("source_tree_fingerprint")),
            _same(exemplar.source_tree_fingerprint, prescan.get("source_tree_fingerprint")),
            _same(exemplar.safe_alias, payload.get("exemplar_safe_alias")),
            _same(exemplar.content_sha256, payload.get("exemplar_content_sha256")),
            _same(exemplar.envelope_sha256, payload.get("exemplar_envelope_sha256")),
            _same(exemplar_bitmap_sha256, payload.get("exemplar_canonical_bitmap_sha256")),
            _same(prescan_receipt_sha256, payload.get("prescan_receipt_sha256")),
            _same(candidate_set_sha256, payload.get("candidate_set_sha256")),
            _same(candidate_set_sha256, prescan.get("candidate_set_sha256")),
            _same(str(payload.get("sample_set_sha256")), prescan.get("sample_set_sha256")),
            _same(str(payload.get("classifier_policy_id")), prescan.get("classifier_policy_id")),
            _same(
                str(payload.get("classifier_policy_sha256")),
                prescan.get("classifier_policy_sha256"),
            ),
            prescan.get("candidate_count") == len(candidates),
            exemplar_is_candidate,
        )
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
    if prescan.get("status") != "COMPLETE" or prescan.get("unresolved_count") != 0:
        _fail(WATERMARK_PRESCAN_INCOMPLETE)
    reviewed_image_only_pages: tuple[Mapping[str, object], ...] = ()
    reviewed_image_only_set_sha256: str | None = None
    if "reviewed_image_only_pages" in prescan:
        reviewed_image_only_pages = _valid_reviewed_image_only_pages(
            prescan.get("reviewed_image_only_pages")
        )
        reviewed_image_only_set_sha256 = str(prescan.get("reviewed_image_only_set_sha256"))
        if prescan.get("reviewer_approval") != payload.get("reviewer_approval"):
            _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)

    approved_at, expires_at = _batch_review_interval(payload)
    _require_batch_active(
        now=now,
        batch_approved_at=approved_at,
        batch_expires_at=expires_at,
        exemplar=exemplar,
    )
    batch = VerifiedWatermarkBatchAttestation(
        schema_version=batch_verified.schema_version,
        signer_id=batch_verified.signer_id,
        payload=payload,
        candidates=candidates,
        candidate_set_sha256=candidate_set_sha256,
        prescan_receipt_sha256=prescan_receipt_sha256,
        sample_set_sha256=str(payload["sample_set_sha256"]),
        payload_sha256=batch_verified.payload_sha256,
        envelope_sha256=batch_verified.envelope_sha256,
        public_key_sha256=batch_verified.public_key_sha256,
        approved_at=approved_at,
        expires_at=expires_at,
    )
    return VerifiedDocumentNoSubstantiveContentAttestationManifest(
        schema_version=NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION,
        exemplar=exemplar,
        batch=batch,
        exemplar_envelope_sha256=exemplar.envelope_sha256,
        exemplar_canonical_bitmap_sha256=exemplar_bitmap_sha256,
        manifest_sha256=_sha256_json(manifest),
        company_file_counts=MappingProxyType({"company-001": 58, "company-002": 209}),
        exemplar_canonical_bitmap=canonical_bitmap,
        reviewed_image_only_pages=reviewed_image_only_pages,
        reviewed_image_only_set_sha256=reviewed_image_only_set_sha256,
    )


class _DuplicateJsonMember(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonMember
        result[key] = value
    return result


def _reject_constant(_value: str) -> Never:
    raise _DuplicateJsonMember


def _read_manifest(path_value: str) -> Mapping[str, object]:
    try:
        raw = secure_read_integrity_file(
            path_value,
            max_bytes=_MAX_MANIFEST_BYTES,
            require_readonly=True,
            require_secure_parent=True,
            require_owner_only=True,
        )
        parsed = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(parsed, Mapping):
            raise _DuplicateJsonMember
        return parsed
    except (
        SecureFileError,
        UnicodeError,
        json.JSONDecodeError,
        _DuplicateJsonMember,
        RecursionError,
        TypeError,
        ValueError,
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID)


def _read_public_key(path_value: str) -> bytes:
    try:
        return secure_read_integrity_file(
            path_value,
            max_bytes=_MAX_PUBLIC_KEY_BYTES,
            require_readonly=True,
            require_secure_parent=True,
        )
    except SecureFileError:
        _fail("GOVERNANCE_SIGNATURE_INVALID")


def load_document_no_substantive_content_attestation(
    *,
    env: Mapping[str, str],
    now: datetime | None = None,
) -> (
    VerifiedDocumentNoSubstantiveContentAttestation
    | VerifiedDocumentNoSubstantiveContentAttestationManifest
    | None
):
    """Securely load and authenticate the optional v1 envelope or v2 manifest."""

    path_value = str(env.get(NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV, "")).strip()
    if not path_value:
        return None
    envelope = _read_manifest(path_value)
    trusted_signer_id = str(env.get("IDIS_GOVERNANCE_TRUSTED_SIGNER_ID", ""))
    public_pem = _read_public_key(
        str(env.get("IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH", ""))
    )
    effective_now = now or datetime.now(UTC)
    if envelope.get("schema_version") == NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION:
        return verify_document_no_substantive_content_attestation_manifest(
            manifest=envelope,
            trusted_signer_id=trusted_signer_id,
            trusted_public_key_pem=public_pem,
            now=effective_now,
        )
    try:
        verified = verify_envelope(
            envelope=envelope,
            trusted_signer_id=trusted_signer_id,
            trusted_public_key_pem=public_pem,
        )
    except GovernanceEnvelopeError as exc:
        code = (
            DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID
            if exc.code == GOVERNANCE_SCHEMA_INVALID
            else exc.code
        )
        raise DocumentNoSubstantiveContentAttestationError(code) from None
    except RecursionError:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID)
    if verified.schema_version != NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID)

    payload = verified.payload
    approval = payload.get("reviewer_approval")
    if not isinstance(approval, Mapping):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID)
    approved_at_text = approval.get("approved_at")
    expires_at_text = approval.get("expires_at")
    approved_at = _utc_instant(approved_at_text)
    expires_at = _utc_instant(expires_at_text)
    required_strings = {
        "tenant_id": payload.get("tenant_id"),
        "source_tree_fingerprint": payload.get("source_tree_fingerprint"),
        "safe_alias": payload.get("safe_alias"),
        "content_sha256": payload.get("content_sha256"),
        "disposition": payload.get("disposition"),
        "variant": payload.get("variant"),
        "attestation_text_sha256": payload.get("attestation_text_sha256"),
        "reviewer_id": approval.get("reviewer_id"),
        "approved_at_text": approved_at_text,
        "expires_at_text": expires_at_text,
    }
    if (
        approved_at is None
        or expires_at is None
        or any(not isinstance(value, str) for value in required_strings.values())
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID)

    attestation = VerifiedDocumentNoSubstantiveContentAttestation(
        schema_version=verified.schema_version,
        signer_id=verified.signer_id,
        tenant_id=str(required_strings["tenant_id"]),
        source_tree_fingerprint=str(required_strings["source_tree_fingerprint"]),
        safe_alias=str(required_strings["safe_alias"]),
        content_sha256=str(required_strings["content_sha256"]),
        disposition=str(required_strings["disposition"]),
        variant=str(required_strings["variant"]),
        attestation_text_sha256=str(required_strings["attestation_text_sha256"]),
        reviewer_id=str(required_strings["reviewer_id"]),
        approved_at_text=str(required_strings["approved_at_text"]),
        expires_at_text=str(required_strings["expires_at_text"]),
        approved_at=approved_at,
        expires_at=expires_at,
        payload_sha256=verified.payload_sha256,
        envelope_sha256=verified.envelope_sha256,
        public_key_sha256=verified.public_key_sha256,
    )
    return attestation.require_active(now=effective_now)


@dataclass(frozen=True, slots=True)
class _CompleteNoSubstantiveContentProof:
    page_count: int
    ocr_required_page_count: int
    primary_completed_page_count: int
    rescue_completed_page_count: int
    accepted_text_page_count: int
    span_count: int
    ocr_result_sha256: str


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _zero(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _parse_error_code(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _complete_proof(parse_result: ParseResult) -> _CompleteNoSubstantiveContentProof:
    if not isinstance(parse_result, ParseResult):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    metadata = parse_result.metadata
    if (
        not isinstance(metadata, Mapping)
        or not isinstance(parse_result.spans, list)
        or not isinstance(parse_result.warnings, list)
        or not isinstance(parse_result.errors, list)
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    semantic_counts = (
        metadata.get("span_count"),
        metadata.get("total_text_length"),
        metadata.get("native_text_page_count"),
        metadata.get("accepted_text_page_count"),
        metadata.get("rescue_accepted_clique_page_count"),
    )
    if (
        parse_result.success is True
        or bool(parse_result.spans)
        or any(_positive(value) for value in semantic_counts)
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    if parse_result.success is not False:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if parse_result.warnings or len(parse_result.errors) != 1:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    error = parse_result.errors[0]
    if _parse_error_code(getattr(error, "code", None)) != "ocr_rescue_no_consensus":
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)

    page_count = _positive_int(metadata.get("page_count"))
    if page_count is None:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    required = page_count
    expected_candidates = page_count * len(OCR_RESCUE_MATRIX)
    expected_metadata_fields = {
        "accepted_text_page_count",
        "blank_page_count",
        "native_text_page_count",
        "ocr_no_consensus_page_receipts",
        "ocr_page_count",
        "ocr_performed",
        "ocr_required_page_count",
        "ocr_rescue_policy_sha256",
        "ocr_rescue_policy_version",
        "ocr_result_sha256",
        "page_count",
        "parser_runtime",
        "parser_runtime_status",
        "primary_completed_page_count",
        "rescue_accepted_clique_page_count",
        "rescue_candidate_attempt_count",
        "rescue_candidate_completed_count",
        "rescue_completed_page_count",
        "span_count",
        "total_text_length",
    }
    if (
        parse_result.doc_type != "PDF"
        or set(metadata) != expected_metadata_fields
        or metadata.get("ocr_performed") is not True
        or metadata.get("ocr_page_count") != required
        or metadata.get("ocr_required_page_count") != required
        or metadata.get("primary_completed_page_count") != required
        or metadata.get("rescue_completed_page_count") != required
        or metadata.get("rescue_candidate_attempt_count") != expected_candidates
        or metadata.get("rescue_candidate_completed_count") != expected_candidates
        or not all(_zero(value) for value in semantic_counts)
        or metadata.get("parser_runtime") != "tesseract"
        or metadata.get("parser_runtime_status") != "completed_no_extractable_text"
        or metadata.get("ocr_rescue_policy_version") != OCR_RESCUE_POLICY_VERSION
        or metadata.get("ocr_rescue_policy_sha256") != OCR_RESCUE_POLICY_SHA256
        or not isinstance(metadata.get("ocr_result_sha256"), str)
        or _SHA256_RE.fullmatch(str(metadata.get("ocr_result_sha256"))) is None
        or not isinstance(metadata.get("blank_page_count"), int)
        or isinstance(metadata.get("blank_page_count"), bool)
        or not 0 <= int(metadata["blank_page_count"]) <= page_count
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    proof_payload = {
        "schema_version": OCR_NO_CONSENSUS_FILE_PROOF_SCHEMA_VERSION,
        "ocr_rescue_policy_version": metadata["ocr_rescue_policy_version"],
        "ocr_rescue_policy_sha256": metadata["ocr_rescue_policy_sha256"],
        "page_receipts": metadata["ocr_no_consensus_page_receipts"],
        "ocr_result_sha256": metadata["ocr_result_sha256"],
    }
    try:
        file_proof = validate_ocr_no_consensus_file_proof_payload(proof_payload)
    except OcrError:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if any(
        receipt.accepted_text
        or receipt.accepted_text_token_count > 0
        or receipt.accepted_text_span_count > 0
        or receipt.accepted_text_codepoint_count > 0
        for receipt in file_proof.page_receipts
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    if (
        len(file_proof.page_receipts) != page_count
        or any(receipt.outcome != "no_consensus" for receipt in file_proof.page_receipts)
        or sum(receipt.primary_completed for receipt in file_proof.page_receipts) != page_count
        or sum(receipt.rescue_attempted_candidate_count for receipt in file_proof.page_receipts)
        != expected_candidates
        or sum(receipt.rescue_completed_candidate_count for receipt in file_proof.page_receipts)
        != expected_candidates
        or file_proof.ocr_result_sha256 != metadata["ocr_result_sha256"]
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    details = getattr(error, "details", None)
    if (
        not isinstance(details, Mapping)
        or set(details)
        != {
            "attempted_candidate_count",
            "completed_candidate_count",
            "no_consensus_page_count",
            "page_count",
        }
        or details.get("page_count") != page_count
        or details.get("attempted_candidate_count") != expected_candidates
        or details.get("completed_candidate_count") != expected_candidates
        or details.get("no_consensus_page_count") != page_count
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)

    return _CompleteNoSubstantiveContentProof(
        page_count=page_count,
        ocr_required_page_count=required,
        primary_completed_page_count=required,
        rescue_completed_page_count=required,
        accepted_text_page_count=0,
        span_count=0,
        ocr_result_sha256=str(metadata["ocr_result_sha256"]),
    )


def _required_attestation(
    value: object,
) -> VerifiedDocumentNoSubstantiveContentAttestation:
    if not isinstance(value, VerifiedDocumentNoSubstantiveContentAttestation):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED)
    return value


def build_document_no_substantive_content_receipt(
    *,
    attestation: VerifiedDocumentNoSubstantiveContentAttestation | None,
    tenant_id: str,
    source_tree_fingerprint: str,
    safe_alias: str,
    content_sha256: str,
    parse_result: ParseResult,
    now: datetime,
) -> dict[str, object]:
    """Build the exact safe receipt only from a complete contradiction-free proof."""

    reviewed = _required_attestation(attestation)
    reviewed.require_active(now=now).require_match(
        tenant_id=tenant_id,
        source_tree_fingerprint=source_tree_fingerprint,
        safe_alias=safe_alias,
        content_sha256=content_sha256,
    )
    proof = _complete_proof(parse_result)
    return {
        "schema_version": NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION,
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": reviewed.variant,
        "safe_alias": reviewed.safe_alias,
        "content_sha256": reviewed.content_sha256,
        "source_tree_fingerprint": reviewed.source_tree_fingerprint,
        "attestation_text_sha256": reviewed.attestation_text_sha256,
        "reviewer_id": reviewed.reviewer_id,
        "approved_at": reviewed.approved_at_text,
        "expires_at": reviewed.expires_at_text,
        "signer_id": reviewed.signer_id,
        "payload_sha256": reviewed.payload_sha256,
        "envelope_sha256": reviewed.envelope_sha256,
        "public_key_sha256": reviewed.public_key_sha256,
        "signature_verified": True,
        "parser_error_code": "ocr_rescue_no_consensus",
        "parser_runtime": "tesseract",
        "parser_runtime_status": "completed_no_extractable_text",
        "page_count": proof.page_count,
        "ocr_required_page_count": proof.ocr_required_page_count,
        "primary_completed_page_count": proof.primary_completed_page_count,
        "rescue_completed_page_count": proof.rescue_completed_page_count,
        "accepted_text_page_count": proof.accepted_text_page_count,
        "span_count": proof.span_count,
        "ocr_result_sha256": proof.ocr_result_sha256,
    }


def validate_document_no_substantive_content_receipt(
    *,
    receipt: Mapping[str, object],
    attestation: VerifiedDocumentNoSubstantiveContentAttestation,
    tenant_id: str,
    source_tree_fingerprint: str,
    safe_alias: str,
    content_sha256: str,
    now: datetime,
) -> None:
    """Revalidate a durable receipt against the current signed trust root."""

    reviewed = _required_attestation(attestation)
    reviewed.require_active(now=now).require_match(
        tenant_id=tenant_id,
        source_tree_fingerprint=source_tree_fingerprint,
        safe_alias=safe_alias,
        content_sha256=content_sha256,
    )
    if (
        not isinstance(receipt, Mapping)
        or frozenset(receipt) != NO_SUBSTANTIVE_CONTENT_RECEIPT_FIELDS
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if _positive(receipt.get("span_count")) or _positive(receipt.get("accepted_text_page_count")):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    expected_binding: dict[str, str] = {
        "schema_version": NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION,
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": reviewed.variant,
        "safe_alias": reviewed.safe_alias,
        "content_sha256": reviewed.content_sha256,
        "source_tree_fingerprint": reviewed.source_tree_fingerprint,
        "attestation_text_sha256": reviewed.attestation_text_sha256,
        "reviewer_id": reviewed.reviewer_id,
        "approved_at": reviewed.approved_at_text,
        "expires_at": reviewed.expires_at_text,
        "signer_id": reviewed.signer_id,
        "payload_sha256": reviewed.payload_sha256,
        "envelope_sha256": reviewed.envelope_sha256,
        "public_key_sha256": reviewed.public_key_sha256,
    }
    if any(not _same(expected, receipt.get(field)) for field, expected in expected_binding.items()):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH)

    page_count = _positive_int(receipt.get("page_count"))
    if (
        receipt.get("signature_verified") is not True
        or receipt.get("parser_error_code") != "ocr_rescue_no_consensus"
        or receipt.get("parser_runtime") != "tesseract"
        or receipt.get("parser_runtime_status") != "completed_no_extractable_text"
        or page_count is None
        or receipt.get("ocr_required_page_count") != page_count
        or receipt.get("primary_completed_page_count") != page_count
        or receipt.get("rescue_completed_page_count") != page_count
        or not _zero(receipt.get("accepted_text_page_count"))
        or not _zero(receipt.get("span_count"))
        or not isinstance(receipt.get("ocr_result_sha256"), str)
        or _SHA256_RE.fullmatch(str(receipt.get("ocr_result_sha256"))) is None
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)


def _required_v2_digest(value: Mapping[str, object], field: str, *, code: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or _SHA256_RE.fullmatch(raw) is None:
        _fail(code)
    return raw


def _fixed_six_decimal(value: object, *, code: str) -> Decimal:
    if not isinstance(value, str) or _FIXED_SIX_DECIMAL_RE.fullmatch(value) is None:
        _fail(code)
    try:
        normalized = Decimal(value)
    except InvalidOperation:
        _fail(code)
    if not Decimal(0) <= normalized <= Decimal(1):
        _fail(code)
    return normalized


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _validate_watermark_manifest_summary(
    value: object,
) -> tuple[Mapping[str, object], tuple[Mapping[str, object], ...]]:
    if not isinstance(value, Mapping) or frozenset(value) != _WATERMARK_MANIFEST_SUMMARY_FIELDS:
        _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
    if (
        value.get("schema_version") != NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION
        or value.get("status") != "verified"
        or value.get("disposition") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        or value.get("variant") != "watermark_only"
        or value.get("classifier_policy_id") != "exemplar-watermark-page-v1"
        or value.get("signature_verified") is not True
        or not isinstance(value.get("tenant_id"), str)
        or not str(value["tenant_id"]).strip()
        or not isinstance(value.get("exemplar_safe_alias"), str)
        or _SAFE_ALIAS_RE.fullmatch(str(value["exemplar_safe_alias"])) is None
        or value.get("exemplar_page_number") != 1
        or not isinstance(value.get("company_file_counts"), Mapping)
        or value.get("company_file_counts") != {"company-001": 58, "company-002": 209}
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
    for field in (
        "source_tree_fingerprint",
        "classifier_policy_sha256",
        "exemplar_content_sha256",
        "exemplar_envelope_sha256",
        "exemplar_canonical_bitmap_sha256",
        "manifest_sha256",
        "batch_envelope_sha256",
        "candidate_set_sha256",
        "public_key_sha256",
    ):
        _required_v2_digest(value, field, code=WATERMARK_BATCH_ATTESTATION_MISMATCH)
    raw_candidates = value.get("candidates")
    candidate_count = _nonnegative_int(value.get("candidate_count"))
    if not isinstance(raw_candidates, list) or candidate_count != len(raw_candidates):
        _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
    candidates: list[Mapping[str, object]] = []
    identities: set[tuple[str, str, int]] = set()
    for candidate in raw_candidates:
        if not isinstance(candidate, Mapping) or frozenset(candidate) != frozenset(
            WATERMARK_BATCH_CANDIDATE_FIELDS
        ):
            _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
        alias = candidate.get("safe_alias")
        content_sha256 = _required_v2_digest(
            candidate,
            "content_sha256",
            code=WATERMARK_BATCH_ATTESTATION_MISMATCH,
        )
        page_number = _positive_int(candidate.get("page_number"))
        _required_v2_digest(
            candidate,
            "canonical_render_sha256",
            code=WATERMARK_BATCH_ATTESTATION_MISMATCH,
        )
        _fixed_six_decimal(
            candidate.get("similarity_score"),
            code=WATERMARK_BATCH_ATTESTATION_MISMATCH,
        )
        if (
            not isinstance(alias, str)
            or _SAFE_ALIAS_RE.fullmatch(alias) is None
            or page_number is None
        ):
            _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
        identity = (alias, content_sha256, page_number)
        if identity in identities:
            _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
        identities.add(identity)
        candidates.append(candidate)
    return value, tuple(candidates)


def _validate_exemplar_reference_identity_shape(
    exemplar: Mapping[str, object],
) -> tuple[str, int]:
    """Validate syntax only; signed-manifest identity is bound by the full validator."""

    exemplar_id = exemplar.get("exemplar_id")
    page_number = _positive_int(exemplar.get("page_number"))
    match = (
        re.fullmatch(
            r"(company-00[12]-document-[0-9]{5}\.pdf):page-([1-9][0-9]*)",
            exemplar_id,
        )
        if isinstance(exemplar_id, str)
        else None
    )
    if match is None or page_number is None or int(match.group(2)) != page_number:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    return match.group(1), page_number


def _validate_watermark_page_classification(
    value: object,
    *,
    receipt_content_sha256: str,
    classifier_policy_id: str,
    classifier_policy_sha256: str,
    require_membership_consistency: bool,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if value.get("schema_version") == WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION:
        return _validate_pre_ocr_watermark_page_classification(
            value,
            receipt_content_sha256=receipt_content_sha256,
            classifier_policy_id=classifier_policy_id,
            classifier_policy_sha256=classifier_policy_sha256,
            require_membership_consistency=require_membership_consistency,
        )
    if frozenset(value) != WATERMARK_PAGE_CLASSIFICATION_FIELDS:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if (
        value.get("schema_version") != WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION
        or value.get("classification") != "watermark_only"
        or value.get("content_sha256") != receipt_content_sha256
        or value.get("policy_id") != classifier_policy_id
        or value.get("policy_sha256") != classifier_policy_sha256
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
    page_number = _positive_int(value.get("page_number"))
    numerator = _positive_int(value.get("similarity_numerator"))
    denominator = _positive_int(value.get("similarity_denominator"))
    if (
        page_number is None
        or numerator is None
        or denominator is None
        or numerator > denominator
        or numerator * 5 < denominator * 4
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    for field in (
        "content_sha256",
        "policy_sha256",
        "canonical_render_sha256",
        "ocr_terminal_proof_sha256",
    ):
        _required_v2_digest(value, field, code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if (
        value.get("transform_chain") != _WATERMARK_TRANSFORM_CHAIN
        or value.get("transform_bounds") != _WATERMARK_TRANSFORM_BOUNDS
        or value.get("similarity_metric") != "foreground_dice"
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    confidence = value.get("confidence_distribution")
    if (
        not isinstance(confidence, Mapping)
        or frozenset(confidence) != _WATERMARK_CONFIDENCE_DISTRIBUTION_FIELDS
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    near_white = _fixed_six_decimal(
        confidence.get("near_white_fraction"),
        code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
    )
    foreground_ink = _fixed_six_decimal(
        confidence.get("foreground_ink_fraction"),
        code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
    )
    similarity_score = _fixed_six_decimal(
        value.get("similarity_score"),
        code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
    )
    if not (
        Decimal("0.970000") <= near_white <= Decimal("0.985000")
        and Decimal("0.015000") <= foreground_ink <= Decimal("0.030000")
        and similarity_score >= Decimal("0.800000")
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)

    exemplar = value.get("exemplar_reference")
    if not isinstance(exemplar, Mapping) or frozenset(exemplar) != (
        _WATERMARK_EXEMPLAR_REFERENCE_FIELDS
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    _validate_exemplar_reference_identity_shape(exemplar)
    for field in (
        "content_sha256",
        "attestation_envelope_sha256",
        "canonical_bitmap_sha256",
        "classifier_policy_sha256",
    ):
        _required_v2_digest(
            exemplar,
            field,
            code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
        )

    membership = value.get("batch_membership")
    if membership not in {"ATTESTED", "RUNTIME_UNSEEN"}:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    if require_membership_consistency:
        _validate_watermark_membership_consistency(value)
    return value


def _validate_pre_ocr_watermark_page_classification(
    value: Mapping[str, object],
    *,
    receipt_content_sha256: str,
    classifier_policy_id: str,
    classifier_policy_sha256: str,
    require_membership_consistency: bool,
) -> Mapping[str, object]:
    if frozenset(value) != WATERMARK_PAGE_CLASSIFICATION_V2_RECEIPT_FIELDS:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if (
        value.get("classification") != "watermark_only"
        or value.get("classification_phase") != "pre_ocr"
        or value.get("full_resolution_ocr_attempted") is not False
        or value.get("content_sha256") != receipt_content_sha256
        or value.get("signed_exemplar_policy_id") != classifier_policy_id
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
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    page_number = _positive_int(value.get("page_number"))
    numerator = _positive_int(value.get("similarity_numerator"))
    denominator = _positive_int(value.get("similarity_denominator"))
    if (
        page_number is None
        or numerator is None
        or denominator is None
        or numerator > denominator
        or numerator * 5 < denominator * 4
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    for field in (
        "content_sha256",
        "signed_exemplar_policy_sha256",
        "runtime_candidate_policy_sha256",
        "canonical_render_sha256",
    ):
        _required_v2_digest(
            value,
            field,
            code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
        )
    confidence = value.get("confidence_distribution")
    if (
        not isinstance(confidence, Mapping)
        or frozenset(confidence) != _WATERMARK_CONFIDENCE_DISTRIBUTION_FIELDS
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    near_white = _fixed_six_decimal(
        confidence.get("near_white_fraction"),
        code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
    )
    foreground_ink = _fixed_six_decimal(
        confidence.get("foreground_ink_fraction"),
        code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
    )
    similarity_score = _fixed_six_decimal(
        value.get("similarity_score"),
        code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
    )
    if not (
        Decimal("0.970000") <= near_white <= Decimal("0.985000")
        and Decimal("0.015000") <= foreground_ink <= Decimal("0.030000")
        and similarity_score >= Decimal("0.800000")
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    exemplar = value.get("exemplar_reference")
    if not isinstance(exemplar, Mapping) or frozenset(exemplar) != (
        _WATERMARK_EXEMPLAR_REFERENCE_FIELDS
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if exemplar.get("classifier_policy_sha256") != classifier_policy_sha256:
        _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
    _validate_exemplar_reference_identity_shape(exemplar)
    for field in (
        "content_sha256",
        "attestation_envelope_sha256",
        "canonical_bitmap_sha256",
        "classifier_policy_sha256",
    ):
        _required_v2_digest(
            exemplar,
            field,
            code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
        )
    membership = value.get("batch_membership")
    if membership == "ATTESTED":
        _required_v2_digest(
            value,
            "batch_candidate_canonical_render_sha256",
            code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
        )
        _fixed_six_decimal(
            value.get("batch_candidate_similarity_score"),
            code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
        )
    elif membership == "RUNTIME_UNSEEN":
        if (
            value.get("batch_candidate_canonical_render_sha256") is not None
            or value.get("batch_candidate_similarity_score") is not None
        ):
            _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    else:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    if require_membership_consistency:
        _validate_watermark_membership_consistency(value)
    return value


def _validate_watermark_membership_consistency(value: Mapping[str, object]) -> None:
    membership = value.get("batch_membership")
    if membership == "ATTESTED":
        expected_membership = (
            (
                "reviewer_batch_identity"
                if value.get("schema_version") == WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION
                else "reviewer_batch"
            ),
            False,
            "BATCH_ATTESTED",
        )
    elif membership == "RUNTIME_UNSEEN":
        expected_membership = (
            "signed_exemplar_runtime_match",
            True,
            "WATERMARK_RUNTIME_CANDIDATE_REVIEW_REQUIRED",
        )
    else:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
    if (
        value.get("confirmation_source"),
        value.get("post_run_review_required"),
        value.get("review_status"),
    ) != expected_membership:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)


def _validate_v2_receipt_structure(
    receipt: Mapping[str, object],
    *,
    require_membership_consistency: bool = True,
) -> tuple[Mapping[str, object], ...]:
    if frozenset(receipt) != NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_FIELDS:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if receipt.get("signature_verified") is not True:
        _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
    if (
        receipt.get("schema_version") != NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
        or receipt.get("disposition") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        or receipt.get("variant") != "watermark_only"
        or receipt.get("manifest_schema_version") != NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION
        or receipt.get("classifier_policy_id") != "exemplar-watermark-page-v1"
        or not isinstance(receipt.get("safe_alias"), str)
        or _SAFE_ALIAS_RE.fullmatch(str(receipt.get("safe_alias"))) is None
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    for field in (
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
        _required_v2_digest(
            receipt,
            field,
            code=DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
        )
    page_count = _positive_int(receipt.get("page_count"))
    classified_page_count = _positive_int(receipt.get("classified_page_count"))
    substantive_span_count = _nonnegative_int(receipt.get("substantive_span_count"))
    whole_document_terminal = receipt.get("whole_document_terminal")
    raw_classifications = receipt.get("watermark_page_classifications")
    if (
        page_count is None
        or classified_page_count is None
        or substantive_span_count is None
        or not isinstance(whole_document_terminal, bool)
        or not isinstance(raw_classifications, list)
        or classified_page_count != len(raw_classifications)
        or classified_page_count > page_count
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if whole_document_terminal:
        if substantive_span_count != 0:
            _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)
        if classified_page_count != page_count:
            _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    elif substantive_span_count < 1 or classified_page_count >= page_count:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED)

    content_sha256 = str(receipt["content_sha256"])
    classifier_policy_id = str(receipt["classifier_policy_id"])
    classifier_policy_sha256 = str(receipt["classifier_policy_sha256"])
    classifications = tuple(
        _validate_watermark_page_classification(
            classification,
            receipt_content_sha256=content_sha256,
            classifier_policy_id=classifier_policy_id,
            classifier_policy_sha256=classifier_policy_sha256,
            require_membership_consistency=require_membership_consistency,
        )
        for classification in raw_classifications
    )
    if _sha256_json(raw_classifications) != receipt["watermark_page_classifications_sha256"]:
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    page_numbers: set[int] = set()
    for classification in classifications:
        page_number = _positive_int(classification.get("page_number"))
        assert page_number is not None
        if page_number > page_count or page_number in page_numbers:
            _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
        page_numbers.add(page_number)
    return classifications


def validate_document_no_substantive_content_receipt_v2(
    *,
    receipt: Mapping[str, object],
    verified_manifest: Mapping[str, object],
    tenant_id: str,
    source_tree_fingerprint: str,
    safe_alias: str,
    content_sha256: str,
    now: datetime,
) -> None:
    """Validate page-level watermark provenance against the current signed batch."""

    if not isinstance(receipt, Mapping):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE)
    if not isinstance(now, datetime) or now.tzinfo is None:
        _fail(WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE)
    classifications = _validate_v2_receipt_structure(
        receipt,
        require_membership_consistency=False,
    )
    manifest, candidates = _validate_watermark_manifest_summary(verified_manifest)
    if (
        not _same(tenant_id, manifest.get("tenant_id"))
        or not _same(source_tree_fingerprint, manifest.get("source_tree_fingerprint"))
        or not _same(source_tree_fingerprint, receipt.get("source_tree_fingerprint"))
        or not _same(safe_alias, receipt.get("safe_alias"))
        or not _same(content_sha256, receipt.get("content_sha256"))
    ):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH)
    governance_bindings = {
        "manifest_sha256": "manifest_sha256",
        "batch_envelope_sha256": "batch_envelope_sha256",
        "candidate_set_sha256": "candidate_set_sha256",
        "classifier_policy_id": "classifier_policy_id",
        "classifier_policy_sha256": "classifier_policy_sha256",
        "exemplar_envelope_sha256": "exemplar_envelope_sha256",
        "exemplar_canonical_bitmap_sha256": "exemplar_canonical_bitmap_sha256",
        "public_key_sha256": "public_key_sha256",
    }
    if any(
        not _same(str(manifest[manifest_field]), receipt.get(receipt_field))
        for receipt_field, manifest_field in governance_bindings.items()
    ):
        _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)

    for classification in classifications:
        exemplar = classification["exemplar_reference"]
        assert isinstance(exemplar, Mapping)
        exemplar_safe_alias, exemplar_page_number = _validate_exemplar_reference_identity_shape(
            exemplar
        )
        if (
            not _same(
                str(manifest["exemplar_safe_alias"]),
                exemplar_safe_alias,
            )
            or exemplar_page_number != manifest["exemplar_page_number"]
            or not _same(
                str(manifest["exemplar_content_sha256"]),
                exemplar.get("content_sha256"),
            )
            or not _same(
                str(manifest["exemplar_envelope_sha256"]),
                exemplar.get("attestation_envelope_sha256"),
            )
            or not _same(
                str(manifest["exemplar_canonical_bitmap_sha256"]),
                exemplar.get("canonical_bitmap_sha256"),
            )
            or not _same(
                str(manifest["classifier_policy_sha256"]),
                exemplar.get("classifier_policy_sha256"),
            )
        ):
            _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
        page_number = _positive_int(classification.get("page_number"))
        assert page_number is not None
        candidate_identity = (
            safe_alias,
            content_sha256,
            page_number,
        )
        matching_identity = [
            candidate
            for candidate in candidates
            if (
                candidate.get("safe_alias"),
                candidate.get("content_sha256"),
                candidate.get("page_number"),
            )
            == candidate_identity
        ]
        if classification["batch_membership"] == "ATTESTED":
            if len(matching_identity) != 1:
                _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
            admitted = matching_identity[0]
            if (
                classification.get("schema_version")
                == WATERMARK_PRE_OCR_CLASSIFICATION_SCHEMA_VERSION
            ):
                if admitted.get("canonical_render_sha256") != classification.get(
                    "batch_candidate_canonical_render_sha256"
                ) or admitted.get("similarity_score") != classification.get(
                    "batch_candidate_similarity_score"
                ):
                    _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
            elif admitted.get("canonical_render_sha256") != classification.get(
                "canonical_render_sha256"
            ) or admitted.get("similarity_score") != classification.get("similarity_score"):
                _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
        elif matching_identity:
            _fail(WATERMARK_BATCH_ATTESTATION_MISMATCH)
        _validate_watermark_membership_consistency(classification)


def build_document_no_substantive_content_receipt_verifier(
    attestation: VerifiedDocumentNoSubstantiveContentAttestation,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Callable[[Mapping[str, Any]], bool]:
    """Bind document preflight to the current signed one-document receipt."""

    reviewed = _required_attestation(attestation)

    def verify(document: Mapping[str, Any]) -> bool:
        try:
            metadata = document.get("metadata")
            source_metadata = document.get("source_metadata")
            if not isinstance(metadata, Mapping) or not isinstance(source_metadata, Mapping):
                return False
            receipt = metadata.get("document_no_substantive_content_receipt")
            tenant_id = document.get("tenant_id")
            safe_alias = document.get("document_name")
            upload_filename = source_metadata.get("upload_filename")
            source_tree_fingerprint = source_metadata.get("source_tree_fingerprint")
            content_sha256 = document.get("sha256")
            if (
                not isinstance(receipt, Mapping)
                or not isinstance(tenant_id, str)
                or not isinstance(safe_alias, str)
                or upload_filename != safe_alias
                or not isinstance(source_tree_fingerprint, str)
                or not isinstance(content_sha256, str)
            ):
                return False
            validate_document_no_substantive_content_receipt(
                receipt=receipt,
                attestation=reviewed,
                tenant_id=tenant_id,
                source_tree_fingerprint=source_tree_fingerprint,
                safe_alias=safe_alias,
                content_sha256=content_sha256,
                now=clock(),
            )
        except Exception:
            return False
        return True

    return verify


def build_document_no_substantive_content_receipt_v2_verifier(
    manifest: VerifiedDocumentNoSubstantiveContentAttestationManifest,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Callable[[Mapping[str, Any]], bool]:
    """Bind document preflight to the current signed watermark batch manifest."""

    if not isinstance(manifest, VerifiedDocumentNoSubstantiveContentAttestationManifest):
        _fail(DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED)
    verified_manifest = manifest.runtime_summary()

    def verify(document: Mapping[str, Any]) -> bool:
        try:
            metadata = document.get("metadata")
            source_metadata = document.get("source_metadata")
            if not isinstance(metadata, Mapping) or not isinstance(source_metadata, Mapping):
                return False
            receipt = metadata.get("document_no_substantive_content_receipt")
            tenant_id = document.get("tenant_id")
            safe_alias = document.get("document_name")
            upload_filename = source_metadata.get("upload_filename")
            source_tree_fingerprint = source_metadata.get("source_tree_fingerprint")
            content_sha256 = document.get("sha256")
            if (
                not isinstance(receipt, Mapping)
                or not isinstance(tenant_id, str)
                or not isinstance(safe_alias, str)
                or upload_filename != safe_alias
                or not isinstance(source_tree_fingerprint, str)
                or not isinstance(content_sha256, str)
            ):
                return False
            now = clock()
            _require_batch_active(
                now=now,
                batch_approved_at=manifest.batch.approved_at,
                batch_expires_at=manifest.batch.expires_at,
                exemplar=manifest.exemplar,
            )
            validate_document_no_substantive_content_receipt_v2(
                receipt=receipt,
                verified_manifest=verified_manifest,
                tenant_id=tenant_id,
                source_tree_fingerprint=source_tree_fingerprint,
                safe_alias=safe_alias,
                content_sha256=content_sha256,
                now=now,
            )
        except Exception:
            return False
        return True

    return verify


def project_safe_document_no_substantive_content_receipt(
    value: object,
) -> dict[str, object] | None:
    """Project only an exact primitive receipt shape for authenticated summaries.

    Current-signature and source-binding validation remains the responsibility of
    ``validate_document_no_substantive_content_receipt``.  This narrower projector prevents a
    malformed durable metadata object from manufacturing a terminal response or
    smuggling parser text through the otherwise generic metadata sanitizer.
    """

    if not isinstance(value, Mapping):
        return None
    if value.get("schema_version") == NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION:
        try:
            _validate_v2_receipt_structure(value)
            projected = json.loads(canonicalize_json(value))
        except (DocumentNoSubstantiveContentAttestationError, TypeError, ValueError):
            return None
        return projected if isinstance(projected, dict) else None
    if frozenset(value) != NO_SUBSTANTIVE_CONTENT_RECEIPT_FIELDS:
        return None
    string_fields = NO_SUBSTANTIVE_CONTENT_RECEIPT_FIELDS - {
        "signature_verified",
        "page_count",
        "ocr_required_page_count",
        "primary_completed_page_count",
        "rescue_completed_page_count",
        "accepted_text_page_count",
        "span_count",
    }
    if any(
        not isinstance(value.get(field), str) or len(str(value[field])) > 256
        for field in string_fields
    ):
        return None
    if (
        value.get("schema_version") != NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION
        or value.get("disposition") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        or value.get("variant") not in NO_SUBSTANTIVE_CONTENT_VARIANTS
        or value.get("signature_verified") is not True
        or value.get("parser_error_code") != "ocr_rescue_no_consensus"
        or value.get("parser_runtime") != "tesseract"
        or value.get("parser_runtime_status") != "completed_no_extractable_text"
    ):
        return None
    page_count = _positive_int(value.get("page_count"))
    if (
        page_count is None
        or value.get("ocr_required_page_count") != page_count
        or value.get("primary_completed_page_count") != page_count
        or value.get("rescue_completed_page_count") != page_count
        or not _zero(value.get("accepted_text_page_count"))
        or not _zero(value.get("span_count"))
    ):
        return None
    for field in (
        "content_sha256",
        "source_tree_fingerprint",
        "attestation_text_sha256",
        "payload_sha256",
        "envelope_sha256",
        "public_key_sha256",
        "ocr_result_sha256",
    ):
        if _SHA256_RE.fullmatch(str(value[field])) is None:
            return None
    approved_at = _utc_instant(value["approved_at"])
    expires_at = _utc_instant(value["expires_at"])
    if (
        _SAFE_ALIAS_RE.fullmatch(str(value["safe_alias"])) is None
        or not str(value["reviewer_id"]).strip()
        or not str(value["signer_id"]).strip()
        or approved_at is None
        or expires_at is None
        or expires_at <= approved_at
    ):
        return None
    return {field: value[field] for field in NO_SUBSTANTIVE_CONTENT_RECEIPT_FIELDS}


__all__ = [
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID",
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH",
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_NOT_ACTIVE",
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED",
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED",
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE",
    "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
    "DocumentNoSubstantiveContentAttestationError",
    "NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV",
    "NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION",
    "NO_SUBSTANTIVE_CONTENT_MANIFEST_FIELDS",
    "NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION",
    "NO_SUBSTANTIVE_CONTENT_MANIFEST_V2_SAFE_SUMMARY_CONTRACT",
    "NO_SUBSTANTIVE_CONTENT_VARIANTS",
    "NO_SUBSTANTIVE_CONTENT_RECEIPT_FIELDS",
    "NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_FIELDS",
    "NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION",
    "NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION",
    "NoSubstantiveContentManifestV2SafeSummaryContract",
    "VerifiedDocumentNoSubstantiveContentAttestation",
    "VerifiedDocumentNoSubstantiveContentAttestationManifest",
    "VerifiedWatermarkBatchAttestation",
    "WATERMARK_BATCH_ATTESTATION_INVALID",
    "WATERMARK_BATCH_ATTESTATION_MISMATCH",
    "WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE",
    "WATERMARK_BATCH_ATTESTATION_SCHEMA_VERSION",
    "WATERMARK_BATCH_CANDIDATE_FIELDS",
    "WATERMARK_PAGE_SANITY_CAP_EXCEEDED",
    "WATERMARK_BATCH_PAYLOAD_FIELDS",
    "WATERMARK_PAGE_CLASSIFICATION_FIELDS",
    "WATERMARK_PAGE_CLASSIFICATION_SCHEMA_VERSION",
    "WATERMARK_PRESCAN_INCOMPLETE",
    "build_document_no_substantive_content_receipt",
    "build_document_no_substantive_content_receipt_v2_verifier",
    "build_document_no_substantive_content_receipt_verifier",
    "load_document_no_substantive_content_attestation",
    "project_safe_document_no_substantive_content_receipt",
    "validate_document_no_substantive_content_receipt",
    "validate_document_no_substantive_content_receipt_v2",
    "verify_document_no_substantive_content_attestation_manifest",
]
