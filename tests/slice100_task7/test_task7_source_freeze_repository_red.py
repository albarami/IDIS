"""RED-first contracts for the Task 7 runtime source-freeze boundary."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
import struct
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PIL import Image, ImageDraw

from idis.models.source_package_manifest import canonical_source_manifest_sha256
from idis.parsers import ocr as ocr_module
from idis.parsers import watermark_page_classifier
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult
from idis.parsers.video_frame_ocr import (
    VideoFrameOcrSegment,
    build_frame_ocr_receipt,
)
from idis.persistence.repositories.task7_source_freeze import (
    LOCKED_SOURCE_QUERY_ORDER,
    PostgresTask7SourceFreezeRepository,
    SourceFreezeObservation,
    SourceFreezeSnapshot,
    Task7SourceFreezeError,
    _validated_span_locator,
    build_source_freeze_plan,
    freeze_source_package,
    validate_locked_source_snapshot,
    validate_reloaded_source_freeze,
)
from idis.runtime.governance_envelopes import (
    NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION,
    canonicalize_json,
    sign_envelope,
)
from idis.runtime.secure_files import protect_path_for_current_user
from idis.services.ingestion import defaults as ingestion_defaults
from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV,
    build_document_no_substantive_content_receipt,
    load_document_no_substantive_content_attestation,
    validate_document_no_substantive_content_receipt_v2,
    verify_document_no_substantive_content_attestation_manifest,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
PACKAGE_ID = "33333333-3333-3333-3333-333333333333"
FILE_ID = "44444444-4444-4444-4444-444444444444"
DOC_ID = "55555555-5555-5555-5555-555555555555"
DOCUMENT_ID = "66666666-6666-6666-6666-666666666666"
ATTEMPT_ID = "77777777-7777-7777-7777-777777777777"
SPAN_ID = "88888888-8888-8888-8888-888888888888"
VERSION_ID = "99999999-9999-9999-9999-999999999999"
UPDATED_AT = datetime(2026, 7, 23, 8, 30, 0, 123456, tzinfo=UTC)
STARTED_AT = datetime(2026, 7, 23, 8, 29, 58, tzinfo=UTC)
FINISHED_AT = datetime(2026, 7, 23, 8, 29, 59, tzinfo=UTC)
CONTENT_SHA256 = hashlib.sha256(b"fictional source bytes").hexdigest()
LOCATOR_SHA256 = hashlib.sha256(b"fictional source locator").hexdigest()
SPAN_TEXT = "Fictional, non-private evidence."
SPAN_SHA256 = hashlib.sha256(SPAN_TEXT.encode()).hexdigest()
OBJECT_URI = "object://safe-bucket/opaque-object-id"
URI_SHA256 = hashlib.sha256(OBJECT_URI.encode()).hexdigest()
ACTIVE_NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
APPROVED_AT = "2026-07-22T00:00:00Z"
EXPIRES_AT = "2026-10-15T00:00:00Z"
SIGNER_ID = "fictional-task7-governance-signer-v1"
REVIEWER_ID = "fictional-task7-reviewer-v1"

_FORMAT_LOCATOR_CASES: tuple[tuple[str, str, Mapping[str, object]], ...] = (
    ("PDF", "PAGE_TEXT", {"page": 1, "line": 1}),
    ("IMAGE", "PAGE_TEXT", {"page": 1, "line": 1, "source": "ocr_image"}),
    (
        "VIDEO",
        "TIMECODE",
        {
            "start_ms": 0,
            "end_ms": 1,
            "source": "video_frame_ocr",
            "sample_index": 0,
            "confidence": 0.75,
        },
    ),
    ("XLSX", "CELL", {"sheet": "Sheet1", "cell": "A1", "row": 1, "col": 1}),
    ("DOCX", "PARAGRAPH", {"paragraph": 0}),
    ("DOCX", "CELL", {"table": 0, "row": 0, "col": 0}),
    ("PPTX", "PARAGRAPH", {"slide": 0, "shape": 0, "paragraph": 0}),
    ("PPTX", "CELL", {"slide": 0, "table": 0, "row": 0, "col": 0}),
    ("HTML", "PARAGRAPH", {"node": 1, "source": "html"}),
    ("TEXT", "PARAGRAPH", {"line": 1, "source": "text"}),
)
_FORMAT_DOCUMENT_TYPES = (
    "PDF",
    "IMAGE",
    "VIDEO",
    "XLSX",
    "DOCX",
    "PPTX",
    "HTML",
    "TEXT",
    "AUDIO",
)
_CROSS_FORMAT_LOCATOR_CASES = tuple(
    (source_doc_type, target_doc_type, span_type, locator)
    for source_doc_type, span_type, locator in _FORMAT_LOCATOR_CASES
    for target_doc_type in _FORMAT_DOCUMENT_TYPES
    if target_doc_type != source_doc_type
)

_SCHEMA_AUTHORITIES = {
    "ocr-terminal-evidence": (
        "ocr-terminal-evidence-v5",
        "5e94193a427aa45530b450e2c3c2711580e7f52d3a56bc68c076fcfd21acf81f",
    ),
    "pdf-page-disposition-census": (
        "pdf-page-disposition-census-v1",
        "8897c621c5bddc43aafb6642978c6516708c11222ffcdf2a14c3346f47545576",
    ),
    "ocr-render-admission": (
        "ocr-render-admission-v2",
        "00339ad79fc75cb508413fe23e467c98ba0d69d439396780187a29192b2f4f23",
    ),
    "media-runtime-provenance": (
        "faster-whisper-runtime-provenance-v2",
        "e620ac826d2e04e2dbf53a5e5dde0179133bf416fa1bbf9fd8699b495c9f5cc6",
    ),
    "media-stt-receipt": (
        "media-stt-receipt-v1",
        "41a6c94a8461e9057e6ac2144b8084f8c91a0b8d5d2ae2b6ea7503d2280b249c",
    ),
    "media-frame-ocr-receipt": (
        "media-frame-ocr-receipt-v1",
        "499643a5deb4f7c70ef4cabe0daf0255a73df34651b5dd059a41b2036199e2e3",
    ),
}


def _canonical_sha256(value: object) -> str:
    def normalize(item: object) -> object:
        if isinstance(item, float):
            normalized = 0.0 if item == 0.0 else item
            return {"float8_be": struct.pack(">d", normalized).hex()}
        if isinstance(item, Mapping):
            return {str(key): normalize(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(nested) for nested in item]
        return item

    encoded = json.dumps(
        normalize(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _schema_ref(schema_id: str, payload: object) -> dict[str, object]:
    schema_version, schema_sha256 = _SCHEMA_AUTHORITIES[schema_id]
    return {
        "schema_id": schema_id,
        "schema_version": schema_version,
        "schema_sha256": schema_sha256,
        "validated_payload": payload,
    }


def _terminal_hash(
    *,
    doc_type: str,
    parse_status: str,
    support_status: str,
    triage_status: str,
    refs: list[Mapping[str, object]],
) -> str:
    return _canonical_sha256(
        [
            "terminal-document-metadata-v2",
            doc_type,
            parse_status,
            {
                "parser_support_status": support_status,
                "parser_triage_status": triage_status,
                "applicable_schema_refs": [dict(ref) for ref in refs],
            },
        ]
    )


def _complete_no_consensus_result() -> ParseResult:
    page_receipt = ocr_module._no_consensus_page_execution_receipt(
        page_number=1,
        candidate_receipt_sha256=hashlib.sha256(b"fictional-task7-candidate").hexdigest(),
    )
    proof = ocr_module._ocr_no_consensus_file_proof((page_receipt,))
    return ParseResult(
        doc_type="PDF",
        success=False,
        spans=[],
        metadata={
            "page_count": 1,
            "span_count": 0,
            "total_text_length": 0,
            "ocr_performed": True,
            "ocr_page_count": 1,
            "ocr_required_page_count": 1,
            "native_text_page_count": 0,
            "blank_page_count": 1,
            "primary_completed_page_count": 1,
            "rescue_completed_page_count": 1,
            "rescue_candidate_attempt_count": len(ocr_module.OCR_RESCUE_MATRIX),
            "rescue_candidate_completed_count": len(ocr_module.OCR_RESCUE_MATRIX),
            "rescue_accepted_clique_page_count": 0,
            "accepted_text_page_count": 0,
            "parser_runtime": "tesseract",
            "parser_runtime_status": "completed_no_extractable_text",
            "ocr_rescue_policy_version": ocr_module.OCR_RESCUE_POLICY_VERSION,
            "ocr_rescue_policy_sha256": ocr_module.OCR_RESCUE_POLICY_SHA256,
            "ocr_no_consensus_page_receipts": [page_receipt.to_payload()],
            "ocr_result_sha256": proof.ocr_result_sha256,
        },
        errors=[
            ParseError(
                code=ParseErrorCode.OCR_RESCUE_NO_CONSENSUS,
                message="Fictional complete no-consensus proof",
                details={
                    "page_count": 1,
                    "attempted_candidate_count": len(ocr_module.OCR_RESCUE_MATRIX),
                    "completed_candidate_count": len(ocr_module.OCR_RESCUE_MATRIX),
                    "no_consensus_page_count": 1,
                },
            )
        ],
        warnings=[],
    )


def _signed_exclusion(
    tmp_path: Path,
    *,
    approved_at: str = APPROVED_AT,
    expires_at: str = EXPIRES_AT,
    active_at: datetime = ACTIVE_NOW,
) -> tuple[dict[str, object], object]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    payload = {
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": _manifest()["source_tree_fingerprint"],
        "safe_alias": "company-001-document-00001.pdf",
        "content_sha256": CONTENT_SHA256,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "attestation_text_sha256": hashlib.sha256(b"fictional watermark-only review").hexdigest(),
        "reviewer_approval": {
            "decision": "APPROVED",
            "reviewer_id": REVIEWER_ID,
            "approved_at": approved_at,
            "expires_at": expires_at,
        },
    }
    envelope = sign_envelope(
        schema_version=NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=payload,
        private_key_pem=private_pem,
    )
    fixture_dir = tmp_path / str(uuid4())
    fixture_dir.mkdir()
    public_path = fixture_dir / "public.pem"
    envelope_path = fixture_dir / "attestation.json"
    public_path.write_bytes(public_pem)
    envelope_path.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
    protect_path_for_current_user(fixture_dir, directory=True)
    protect_path_for_current_user(public_path)
    protect_path_for_current_user(envelope_path)
    public_path.chmod(stat.S_IREAD if os.name == "nt" else stat.S_IRUSR)
    envelope_path.chmod(stat.S_IREAD if os.name == "nt" else stat.S_IRUSR)
    verified = load_document_no_substantive_content_attestation(
        env={
            NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV: str(envelope_path.resolve()),
            "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": SIGNER_ID,
            "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_path.resolve()),
        },
        now=active_at,
    )
    receipt = build_document_no_substantive_content_receipt(
        attestation=verified,
        tenant_id=TENANT_ID,
        source_tree_fingerprint=str(payload["source_tree_fingerprint"]),
        safe_alias=str(payload["safe_alias"]),
        content_sha256=CONTENT_SHA256,
        parse_result=_complete_no_consensus_result(),
        now=active_at,
    )
    return receipt, verified


def _signed_v2_exclusion() -> tuple[dict[str, object], object]:
    """Build the current batch authority through the production exemplar pipeline."""

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    source_tree_fingerprint = str(_manifest()["source_tree_fingerprint"])
    safe_alias = "company-001-document-00001.pdf"
    policy = watermark_page_classifier.default_watermark_page_policy()
    page = Image.new("L", (1000, 1000), color=255)
    draw = ImageDraw.Draw(page)
    draw.rectangle((400, 450, 599, 549), fill=0)
    canonical_bitmap = watermark_page_classifier.build_watermark_page_canonical_exemplar_bitmap(
        page_image=page,
        render_dpi=policy.render_dpi,
        policy=policy,
    )
    canonical_render_sha256 = hashlib.sha256(canonical_bitmap).hexdigest()
    approval = {
        "decision": "APPROVED",
        "reviewer_id": REVIEWER_ID,
        "approved_at": APPROVED_AT,
        "expires_at": EXPIRES_AT,
    }
    exemplar_payload = {
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": source_tree_fingerprint,
        "safe_alias": safe_alias,
        "content_sha256": CONTENT_SHA256,
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": "watermark_only",
        "attestation_text_sha256": hashlib.sha256(
            b"fictional v2 watermark-only review"
        ).hexdigest(),
        "reviewer_approval": approval,
    }
    exemplar_envelope = sign_envelope(
        schema_version=NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=exemplar_payload,
        private_key_pem=private_pem,
    )
    candidates = [
        {
            "safe_alias": safe_alias,
            "content_sha256": CONTENT_SHA256,
            "page_number": 1,
            "canonical_render_sha256": canonical_render_sha256,
            "similarity_score": "1.000000",
        }
    ]
    candidate_set_sha256 = hashlib.sha256(canonicalize_json(candidates)).hexdigest()
    prescan_receipt = {
        "schema_version": "watermark-page-prescan-v1",
        "source_tree_fingerprint": source_tree_fingerprint,
        "classifier_policy_id": policy.policy_id,
        "classifier_policy_sha256": policy.policy_sha256,
        "total_file_count": 267,
        "company_file_counts": {"company-001": 58, "company-002": 209},
        "candidate_count": 1,
        "candidate_set_sha256": candidate_set_sha256,
        "sample_set_sha256": candidate_set_sha256,
        "unresolved_count": 0,
        "status": "COMPLETE",
        "blocker_code": None,
    }
    batch_payload = {
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": source_tree_fingerprint,
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": "watermark_only",
        "classifier_policy_id": policy.policy_id,
        "classifier_policy_sha256": policy.policy_sha256,
        "exemplar_safe_alias": safe_alias,
        "exemplar_content_sha256": CONTENT_SHA256,
        "exemplar_envelope_sha256": hashlib.sha256(
            canonicalize_json(exemplar_envelope)
        ).hexdigest(),
        "exemplar_canonical_bitmap_sha256": canonical_render_sha256,
        "prescan_receipt_sha256": hashlib.sha256(canonicalize_json(prescan_receipt)).hexdigest(),
        "candidate_set_sha256": candidate_set_sha256,
        "candidates": candidates,
        "sample_set_sha256": candidate_set_sha256,
        "attestation_text_sha256": hashlib.sha256(b"fictional v2 batch approval").hexdigest(),
        "reviewer_approval": approval,
    }
    manifest = {
        "schema_version": "document-no-substantive-content-attestation-manifest-v2",
        "exemplar_attestation_envelope": exemplar_envelope,
        "exemplar_canonical_bitmap_base64": base64.b64encode(canonical_bitmap).decode("ascii"),
        "watermark_prescan_receipt": prescan_receipt,
        "watermark_batch_attestation_envelope": sign_envelope(
            schema_version="document-no-substantive-content-watermark-batch-attestation-v1",
            signer_id=SIGNER_ID,
            payload=batch_payload,
            private_key_pem=private_pem,
        ),
    }
    verified = verify_document_no_substantive_content_attestation_manifest(
        manifest=manifest,
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_pem,
        now=ACTIVE_NOW,
    )
    runtime_config = ingestion_defaults._watermark_page_runtime_config(verified)  # noqa: SLF001
    assert runtime_config is not None
    with page.resize((512, 512), Image.Resampling.LANCZOS) as resized_candidate:
        encoded_candidate = io.BytesIO()
        resized_candidate.save(encoded_candidate, format="PNG")
    encoded_candidate.seek(0)
    candidate = Image.open(encoded_candidate)
    try:
        classification = watermark_page_classifier.classify_pre_ocr_runtime_watermark_candidate(
            candidate_image=candidate,
            page_number=1,
            content_sha256=CONTENT_SHA256,
            native_text_character_count=0,
            runtime_config=runtime_config,
        ).to_dict()
    finally:
        candidate.close()
        encoded_candidate.close()
        page.close()
    classification.update(
        {
            "batch_membership": "ATTESTED",
            "confirmation_source": "reviewer_batch_identity",
            "post_run_review_required": False,
            "review_status": "BATCH_ATTESTED",
            "batch_candidate_canonical_render_sha256": canonical_render_sha256,
            "batch_candidate_similarity_score": "1.000000",
        }
    )
    summary = verified.runtime_summary()
    receipt = {
        "schema_version": "document-no-substantive-content-receipt-v2",
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": "watermark_only",
        "safe_alias": safe_alias,
        "content_sha256": CONTENT_SHA256,
        "source_tree_fingerprint": source_tree_fingerprint,
        "manifest_schema_version": summary["schema_version"],
        "manifest_sha256": summary["manifest_sha256"],
        "batch_envelope_sha256": summary["batch_envelope_sha256"],
        "candidate_set_sha256": summary["candidate_set_sha256"],
        "classifier_policy_id": summary["classifier_policy_id"],
        "classifier_policy_sha256": summary["classifier_policy_sha256"],
        "exemplar_envelope_sha256": summary["exemplar_envelope_sha256"],
        "exemplar_canonical_bitmap_sha256": summary["exemplar_canonical_bitmap_sha256"],
        "public_key_sha256": summary["public_key_sha256"],
        "signature_verified": True,
        "page_count": 1,
        "classified_page_count": 1,
        "substantive_span_count": 0,
        "whole_document_terminal": True,
        "watermark_page_classifications": [classification],
        "watermark_page_classifications_sha256": hashlib.sha256(
            canonicalize_json([classification])
        ).hexdigest(),
    }
    validate_document_no_substantive_content_receipt_v2(
        receipt=receipt,
        verified_manifest=summary,
        tenant_id=TENANT_ID,
        source_tree_fingerprint=source_tree_fingerprint,
        safe_alias=safe_alias,
        content_sha256=CONTENT_SHA256,
        now=ACTIVE_NOW,
    )
    return receipt, verified


def _manifest() -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "source-package-manifest-v1",
        "company_alias": "company-001",
        "deal_alias": "deal-001",
        "document_set_alias": "documents-001",
        "run_alias": "run-001",
        "ledger_alias": "ledger-001",
        "package_alias": "package-001",
        "source_tree_fingerprint": hashlib.sha256(b"fictional tree").hexdigest(),
        "file_count": 1,
        "total_bytes": 24,
        "files": [
            {
                "sequence": 0,
                "generated_upload_name": "company-001-document-00001.docx",
                "source_locator_sha256": LOCATOR_SHA256,
                "content_sha256": CONTENT_SHA256,
                "size_bytes": 24,
                "extension": "docx",
                "document_id": DOCUMENT_ID,
                "doc_id": DOC_ID,
            }
        ],
    }
    value["manifest_sha256"] = canonical_source_manifest_sha256(value)
    return value


def _terminal_projection() -> dict[str, Any]:
    return {
        "parser_support_status": "supported",
        "parser_triage_status": "ready",
        "applicable_schema_refs": [],
    }


def _terminal_receipt_sha256() -> str:
    return _canonical_sha256(
        [
            "terminal-document-metadata-v2",
            "DOCX",
            "PARSED",
            _terminal_projection(),
        ]
    )


def _exclusion_snapshot(
    trusted_receipt: Mapping[str, object] | None = None,
) -> SourceFreezeSnapshot:
    manifest = _manifest()
    manifest["files"][0].update(
        {
            "generated_upload_name": "company-001-document-00001.pdf",
            "extension": "pdf",
        }
    )
    manifest["manifest_sha256"] = canonical_source_manifest_sha256(manifest)
    fabricated_receipt: dict[str, object] = {
        "schema_version": "document-no-substantive-content-receipt-v1",
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "safe_alias": "company-001-document-00001.pdf",
        "content_sha256": CONTENT_SHA256,
        "source_tree_fingerprint": manifest["source_tree_fingerprint"],
        "attestation_text_sha256": "1" * 64,
        "reviewer_id": "fictional-reviewer",
        "approved_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-10-01T00:00:00Z",
        "signer_id": "fictional-signer",
        "payload_sha256": "2" * 64,
        "envelope_sha256": "3" * 64,
        "public_key_sha256": "4" * 64,
        "signature_verified": True,
        "parser_error_code": "ocr_rescue_no_consensus",
        "parser_runtime": "tesseract",
        "parser_runtime_status": "completed_no_extractable_text",
        "page_count": 1,
        "ocr_required_page_count": 1,
        "primary_completed_page_count": 1,
        "rescue_completed_page_count": 1,
        "accepted_text_page_count": 0,
        "span_count": 0,
        "ocr_result_sha256": "5" * 64,
    }
    receipt = dict(trusted_receipt or fabricated_receipt)
    ref = {
        "schema_id": "no-substantive-content-receipt",
        "schema_version": "document-no-substantive-content-receipt-v1",
        "schema_sha256": "d706bf06bd6df5b6185a327297f70258bbca56bc723941a8e352ba9836f3d8d3",
        "validated_payload": receipt,
    }
    projection = {
        "parser_support_status": "scanned_or_image_only",
        "parser_triage_status": "ocr_required",
        "applicable_schema_refs": [ref],
    }
    terminal_hash = _canonical_sha256(
        ["terminal-document-metadata-v2", "PDF", "FAILED", projection]
    )
    base = _snapshot(manifest=manifest, spans=())
    return replace(
        base,
        files=(
            {
                **base.files[0],
                "extension": "pdf",
                "file_status": "blocked",
                "support_status": "scanned_or_image_only",
                "triage_status": "ocr_required",
                "parse_status": "FAILED",
                "reason_codes": ["ocr_required"],
                "error_codes": ["ocr_rescue_no_consensus"],
            },
        ),
        documents=(
            {
                **base.documents[0],
                "doc_type": "PDF",
                "parse_status": "FAILED",
                "metadata": {
                    "parser_runtime": "tesseract",
                    "parser_runtime_status": "failed",
                    "parser_support_status": "scanned_or_image_only",
                    "parser_triage_status": "ocr_required",
                    "parser_reason_codes": ["ocr_required"],
                    "parse_warning_codes": [],
                    "parse_error_codes": ["ocr_rescue_no_consensus"],
                    "applicable_schema_refs": [ref],
                    "span_count": 0,
                    "document_no_substantive_content_receipt": receipt,
                },
            },
        ),
        attempts=(
            {
                **base.attempts[0],
                "parser_name": "tesseract",
                "parser_version": "runtime-parser-v1",
                "receipt_sha256": terminal_hash,
                "status": "FAILED",
            },
        ),
    )


def _snapshot(
    *,
    package_status: str = "OPEN",
    package_binding_state: str = "UNBOUND",
    child_binding_state: str = "UNBOUND",
    package_updated_at: datetime = UPDATED_AT,
    manifest: Mapping[str, Any] | None = None,
    attempt_status: str = "PARSED",
    attempt_receipt_sha256: str | None = None,
    spans: tuple[Mapping[str, Any], ...] | None = None,
) -> SourceFreezeSnapshot:
    source_manifest = dict(manifest or _manifest())
    bound = child_binding_state == "BOUND"
    package_bound = package_binding_state == "BOUND"
    package_root = "aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa" if package_bound else None
    child_root = "bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb" if bound else None
    corpus = PACKAGE_ID if bound else None
    parse_attempt_id = ATTEMPT_ID if bound else None
    document_metadata = {
        "parser_runtime": "built_in",
        "parser_runtime_status": "completed",
        "parser_support_status": "supported",
        "parser_triage_status": "ready",
        "parser_reason_codes": ["docx_parse_completed"],
        "parse_warning_codes": [],
        "parse_error_codes": [],
        "applicable_schema_refs": [],
        "span_count": 1,
    }
    artifact_metadata = {
        "durable_object_integrity": {
            "schema_version": "durable-object-integrity-v1",
            "doc_id": DOC_ID,
            "content_sha256": CONTENT_SHA256,
            "size_bytes": 24,
            "uri_sha256": URI_SHA256,
            "version_id": VERSION_ID,
            "backend": "compliant:filesystem",
            "streaming_verified": True,
        }
    }
    return SourceFreezeSnapshot(
        package={
            "package_id": PACKAGE_ID,
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "status": package_status,
            "metadata": {"source_manifest": source_manifest},
            "updated_at": package_updated_at,
            "source_binding_state": package_binding_state,
            "source_lineage_object_id": package_root,
            "source_corpus_receipt_id": PACKAGE_ID,
        },
        files=(
            {
                "file_entry_id": FILE_ID,
                "tenant_id": TENANT_ID,
                "package_id": PACKAGE_ID,
                "deal_id": DEAL_ID,
                "sequence": 0,
                "path_hash": LOCATOR_SHA256,
                "extension": "docx",
                "sha256": CONTENT_SHA256,
                "file_status": "supported",
                "support_status": "supported",
                "triage_status": "ready",
                "parse_status": "PARSED",
                "reason_codes": ["docx_parse_completed"],
                "error_codes": [],
                "doc_id": DOC_ID if bound else None,
                "document_id": DOCUMENT_ID if bound else None,
                "storage_uri": None,
                "source_binding_state": child_binding_state,
                "source_lineage_object_id": child_root,
                "source_corpus_receipt_id": PACKAGE_ID,
            },
        ),
        artifacts=(
            {
                "doc_id": DOC_ID,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "doc_type": "DATA_ROOM_FILE",
                "title": "Fictional source title",
                "source_system": "task7-test",
                "version_id": VERSION_ID,
                "ingested_at": STARTED_AT,
                "sha256": CONTENT_SHA256,
                "uri": OBJECT_URI,
                "metadata": artifact_metadata,
                "source_binding_state": child_binding_state,
                "source_lineage_object_id": child_root,
                "source_corpus_receipt_id": corpus,
            },
        ),
        documents=(
            {
                "document_id": DOCUMENT_ID,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "doc_id": DOC_ID,
                "doc_type": "DOCX",
                "parse_status": "PARSED",
                "metadata": document_metadata,
                "created_at": STARTED_AT,
                "updated_at": FINISHED_AT,
                "source_binding_state": child_binding_state,
                "source_lineage_object_id": child_root,
                "source_corpus_receipt_id": corpus,
            },
        ),
        attempts=(
            {
                "parse_attempt_id": ATTEMPT_ID,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "document_id": DOCUMENT_ID,
                "attempt_ordinal": 1,
                "parser_name": "built_in",
                "parser_version": "runtime-parser-v1",
                "input_sha256": CONTENT_SHA256,
                "receipt_sha256": attempt_receipt_sha256 or _terminal_receipt_sha256(),
                "status": attempt_status,
                "started_at": STARTED_AT,
                "finished_at": FINISHED_AT,
                "source_binding_state": child_binding_state,
                "source_lineage_object_id": child_root,
                "source_corpus_receipt_id": corpus,
                "write_origin": "RUNTIME",
            },
        ),
        spans=spans
        if spans is not None
        else (
            {
                "span_id": SPAN_ID,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "document_id": DOCUMENT_ID,
                "span_type": "PARAGRAPH",
                "locator": {"paragraph": 0},
                "text_excerpt": SPAN_TEXT,
                "content_hash": SPAN_SHA256,
                "parse_attempt_id": parse_attempt_id,
                "source_binding_state": child_binding_state,
                "source_lineage_object_id": child_root,
                "source_corpus_receipt_id": corpus,
            },
        ),
    )


def _pdf_refs() -> list[dict[str, object]]:
    from idis.evaluation.real_example_terminal_reconciliation import (
        OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION,
        PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION,
        _build_pdf_page_census,
    )

    empty_sha256 = _canonical_sha256([])
    census = _build_pdf_page_census(
        page_count=1,
        native_text_pages=(1,),
        ocr_text_pages=(),
        image_only_pages=(),
        watermark_only_pages=(),
        evidence_bindings={1: SPAN_SHA256},
    )
    assert census["schema_version"] == PDF_PAGE_DISPOSITION_CENSUS_SCHEMA_VERSION
    evidence = {
        "schema_version": OCR_TERMINAL_EVIDENCE_SCHEMA_VERSION,
        "ocr_page_count": 0,
        "ocr_page_numbers": [],
        "preclassified_page_numbers": [],
        "ocr_required_page_count": 0,
        "ocr_span_count": 0,
        "confidence_count": 0,
        "mean_confidence": None,
        "minimum_confidence": None,
        "maximum_confidence": None,
        "locator_contract_sha256": empty_sha256,
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": empty_sha256,
        "ocr_render_admissions": [],
        "ocr_render_admissions_sha256": empty_sha256,
        "pdf_page_census": census,
    }
    return [
        _schema_ref("ocr-terminal-evidence", evidence),
        _schema_ref("pdf-page-disposition-census", census),
    ]


def _pdf_snapshot(
    *,
    refs: list[Mapping[str, object]] | None = None,
    locator: Mapping[str, object] | None = None,
) -> SourceFreezeSnapshot:
    source_manifest = _manifest()
    source_manifest["files"][0].update(
        {
            "generated_upload_name": "company-001-document-00001.pdf",
            "extension": "pdf",
        }
    )
    source_manifest["manifest_sha256"] = canonical_source_manifest_sha256(source_manifest)
    applicable_refs = [dict(ref) for ref in (refs if refs is not None else _pdf_refs())]
    terminal_hash = _terminal_hash(
        doc_type="PDF",
        parse_status="PARSED",
        support_status="supported",
        triage_status="ready",
        refs=applicable_refs,
    )
    base = _snapshot(manifest=source_manifest)
    return replace(
        base,
        files=(
            {
                **base.files[0],
                "extension": "pdf",
                "reason_codes": ["pdf_parse_completed"],
            },
        ),
        documents=(
            {
                **base.documents[0],
                "doc_type": "PDF",
                "metadata": {
                    **base.documents[0]["metadata"],
                    "parser_runtime": "pypdf-tesseract",
                    "parser_reason_codes": ["pdf_parse_completed"],
                    "applicable_schema_refs": applicable_refs,
                },
            },
        ),
        attempts=(
            {
                **base.attempts[0],
                "parser_name": "pypdf-tesseract",
                "receipt_sha256": terminal_hash,
            },
        ),
        spans=(
            {
                **base.spans[0],
                "span_type": "PAGE_TEXT",
                "locator": dict(locator or {"page": 1, "line": 1}),
            },
        ),
    )


def _media_refs(confidence: float) -> list[dict[str, object]]:
    from idis.parsers.media import (
        FASTER_WHISPER_ADAPTER_CONTRACT_VERSION,
        FASTER_WHISPER_CPU_THREADS,
        FASTER_WHISPER_NUM_WORKERS,
        FASTER_WHISPER_RUNTIME_PROVENANCE_SCHEMA_VERSION,
    )

    runtime = {
        "schema_version": FASTER_WHISPER_RUNTIME_PROVENANCE_SCHEMA_VERSION,
        "adapter": "faster-whisper",
        "adapter_contract_version": FASTER_WHISPER_ADAPTER_CONTRACT_VERSION,
        "runtime_package_version": "1.2.3",
        "model_source": "local",
        "model_files_sha256": hashlib.sha256(b"fictional-local-model").hexdigest(),
        "cpu_threads": FASTER_WHISPER_CPU_THREADS,
        "num_workers": FASTER_WHISPER_NUM_WORKERS,
        "config_sha256": hashlib.sha256(b"fictional-media-config").hexdigest(),
    }
    segment = VideoFrameOcrSegment(
        sample_index=0,
        start_ms=0,
        end_ms=1,
        text=SPAN_TEXT,
        confidence=confidence,
    )
    frame_receipt = build_frame_ocr_receipt(
        duration_ms=1_000,
        sample_ms=(0,),
        segments=(segment,),
        completed_sample_count=1,
    )
    stt_receipt = {
        "schema_version": "media-stt-receipt-v1",
        "status": "completed",
        "segment_count": 0,
        "evidence_span_count": 0,
        "evidence_sha256": _canonical_sha256([]),
        "runtime_provenance_sha256": _canonical_sha256(runtime),
    }
    return sorted(
        (
            _schema_ref("media-runtime-provenance", runtime),
            _schema_ref("media-stt-receipt", stt_receipt),
            _schema_ref("media-frame-ocr-receipt", frame_receipt),
        ),
        key=lambda ref: str(ref["schema_id"]),
    )


def _media_snapshot(
    *,
    confidence: float,
    refs: list[Mapping[str, object]] | None = None,
) -> SourceFreezeSnapshot:
    source_manifest = _manifest()
    source_manifest["files"][0].update(
        {
            "generated_upload_name": "company-001-document-00001.mp4",
            "extension": "mp4",
        }
    )
    source_manifest["manifest_sha256"] = canonical_source_manifest_sha256(source_manifest)
    applicable_refs = [dict(ref) for ref in (refs if refs is not None else _media_refs(confidence))]
    terminal_hash = _terminal_hash(
        doc_type="VIDEO",
        parse_status="PARSED",
        support_status="supported",
        triage_status="ready",
        refs=applicable_refs,
    )
    base = _snapshot(manifest=source_manifest)
    return replace(
        base,
        files=(
            {
                **base.files[0],
                "extension": "mp4",
                "reason_codes": ["media_evidence_completed"],
            },
        ),
        documents=(
            {
                **base.documents[0],
                "doc_type": "VIDEO",
                "metadata": {
                    **base.documents[0]["metadata"],
                    "parser_runtime": "faster-whisper-frame-ocr",
                    "parser_reason_codes": ["media_evidence_completed"],
                    "applicable_schema_refs": applicable_refs,
                },
            },
        ),
        attempts=(
            {
                **base.attempts[0],
                "parser_name": "faster-whisper-frame-ocr",
                "receipt_sha256": terminal_hash,
            },
        ),
        spans=(
            {
                **base.spans[0],
                "span_type": "TIMECODE",
                "locator": {
                    "source": "video_frame_ocr",
                    "sample_index": 0,
                    "start_ms": 0,
                    "end_ms": 1,
                    "confidence": confidence,
                },
            },
        ),
    )


def _image_refs() -> list[dict[str, object]]:
    from idis.evaluation.real_example_terminal_reconciliation import (
        OCR_TERMINAL_LEGACY_EVIDENCE_SCHEMA_VERSION,
    )

    empty_sha256 = _canonical_sha256([])
    evidence = {
        "schema_version": OCR_TERMINAL_LEGACY_EVIDENCE_SCHEMA_VERSION,
        "ocr_page_count": 1,
        "ocr_span_count": 1,
        "confidence_count": 1,
        "mean_confidence": 0.75,
        "minimum_confidence": 0.75,
        "maximum_confidence": 0.75,
        "locator_contract_sha256": hashlib.sha256(
            b"production-image-page-text-locator-v1"
        ).hexdigest(),
        "ocr_rescue_page_count": 0,
        "ocr_rescue_span_count": 0,
        "ocr_rescue_receipt_sha256": empty_sha256,
    }
    return [_schema_ref("ocr-terminal-evidence", evidence)]


def _format_snapshot(
    *,
    doc_type: str,
    extension: str,
    span_type: str,
    locator: Mapping[str, object],
    refs: list[Mapping[str, object]] | None = None,
) -> SourceFreezeSnapshot:
    source_manifest = _manifest()
    source_manifest["files"][0].update(
        {
            "generated_upload_name": f"company-001-document-00001.{extension}",
            "extension": extension,
        }
    )
    source_manifest["manifest_sha256"] = canonical_source_manifest_sha256(source_manifest)
    applicable_refs = [dict(ref) for ref in (refs or [])]
    reason_code = f"{extension}_parse_completed"
    terminal_hash = _terminal_hash(
        doc_type=doc_type,
        parse_status="PARSED",
        support_status="supported",
        triage_status="ready",
        refs=applicable_refs,
    )
    base = _snapshot(manifest=source_manifest)
    return replace(
        base,
        files=(
            {
                **base.files[0],
                "extension": extension,
                "reason_codes": [reason_code],
            },
        ),
        documents=(
            {
                **base.documents[0],
                "doc_type": doc_type,
                "metadata": {
                    **base.documents[0]["metadata"],
                    "parser_runtime": f"production-{extension}-parser",
                    "parser_reason_codes": [reason_code],
                    "applicable_schema_refs": applicable_refs,
                },
            },
        ),
        attempts=(
            {
                **base.attempts[0],
                "parser_name": f"production-{extension}-parser",
                "receipt_sha256": terminal_hash,
            },
        ),
        spans=(
            {
                **base.spans[0],
                "span_type": span_type,
                "locator": dict(locator),
            },
        ),
    )


def _all_format_snapshot(
    doc_type: str,
    span_type: str,
    locator: Mapping[str, object],
) -> SourceFreezeSnapshot:
    if doc_type == "PDF":
        return _pdf_snapshot(locator=locator)
    if doc_type == "VIDEO":
        return _media_snapshot(confidence=float(locator["confidence"]))
    extension = {
        "IMAGE": "png",
        "XLSX": "xlsx",
        "DOCX": "docx",
        "PPTX": "pptx",
        "HTML": "html",
        "TEXT": "txt",
    }[doc_type]
    refs = _image_refs() if doc_type == "IMAGE" else []
    return _format_snapshot(
        doc_type=doc_type,
        extension=extension,
        span_type=span_type,
        locator=locator,
        refs=refs,
    )


def _observation(snapshot: SourceFreezeSnapshot | None = None) -> SourceFreezeObservation:
    value = snapshot or _snapshot()
    manifest = value.package["metadata"]["source_manifest"]
    return SourceFreezeObservation(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        package_id=PACKAGE_ID,
        observed_updated_at=UPDATED_AT,
        observed_source_manifest_sha256=canonical_source_manifest_sha256(manifest),
        observed_status=str(value.package["status"]),
        observed_source_binding_state=str(value.package["source_binding_state"]),
    )


def test_open_unbound_snapshot_builds_all_six_hash_bound_root_kinds() -> None:
    plan = build_source_freeze_plan(_snapshot())

    assert [root.object_kind for root in plan.roots] == [
        "PACKAGE",
        "PACKAGE_FILE",
        "ARTIFACT",
        "DOCUMENT",
        "PARSE_ATTEMPT",
        "SPAN",
    ]
    assert all(UUID(root.source_lineage_object_id).version == 5 for root in plan.roots)
    assert {root.write_origin for root in plan.roots} == {"RUNTIME"}
    assert {root.corpus_receipt_id for root in plan.roots} == {PACKAGE_ID}
    assert plan.manifest_sha256 == _manifest()["manifest_sha256"]
    assert plan.selected_parse_attempts == {DOCUMENT_ID: ATTEMPT_ID}
    assert plan.root_set_sha256 == _canonical_sha256(
        [
            "task7-source-freeze-root-set-v1",
            [root.canonical_tuple() for root in plan.roots],
        ]
    )


def test_root_derivation_is_repeatable_and_identity_sensitive() -> None:
    baseline = build_source_freeze_plan(_snapshot())
    repeated = build_source_freeze_plan(_snapshot())
    changed_snapshot = _snapshot(
        spans=({**_snapshot().spans[0], "locator": {"paragraph": 1}},),
    )
    changed = build_source_freeze_plan(changed_snapshot)

    assert repeated.roots == baseline.roots
    assert repeated.root_set_sha256 == baseline.root_set_sha256
    baseline_span = next(root for root in baseline.roots if root.object_kind == "SPAN")
    changed_span = next(root for root in changed.roots if root.object_kind == "SPAN")
    assert changed_span.source_lineage_object_id != baseline_span.source_lineage_object_id
    assert changed.root_set_sha256 != baseline.root_set_sha256


def test_current_trust_root_verified_zero_span_exclusion_builds_non_evidence_roots(
    tmp_path: Path,
) -> None:
    receipt, authority = _signed_exclusion(tmp_path)
    plan = build_source_freeze_plan(
        _exclusion_snapshot(receipt),
        terminal_exclusion_authority=authority,
    )

    assert [root.object_kind for root in plan.roots] == [
        "PACKAGE",
        "PACKAGE_FILE",
        "ARTIFACT",
        "DOCUMENT",
        "PARSE_ATTEMPT",
    ]
    assert [root.source_disposition for root in plan.roots] == [
        "CORPUS",
        "CONFIRMED_EXCLUSION",
        "CONFIRMED_EXCLUSION",
        "CONFIRMED_EXCLUSION",
        "CONFIRMED_EXCLUSION",
    ]
    assert not any(binding.object_kind == "SPAN" for binding in plan.bindings)


def test_current_signed_batch_authority_admits_production_built_v2_receipt() -> None:
    receipt, authority = _signed_v2_exclusion()

    plan = build_source_freeze_plan(
        _exclusion_snapshot(receipt),
        terminal_exclusion_authority=authority,
    )

    assert [root.object_kind for root in plan.roots] == [
        "PACKAGE",
        "PACKAGE_FILE",
        "ARTIFACT",
        "DOCUMENT",
        "PARSE_ATTEMPT",
    ]
    assert all(root.source_disposition in {"CORPUS", "CONFIRMED_EXCLUSION"} for root in plan.roots)


def test_fabricated_signature_verified_boolean_cannot_admit_exclusion() -> None:
    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(_exclusion_snapshot())

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_boolean_only_verifier_cannot_substitute_for_signed_authority() -> None:
    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(
            _exclusion_snapshot(),
            terminal_exclusion_authority=lambda _document: True,  # type: ignore[arg-type]
        )

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_expired_signed_exclusion_is_revalidated_at_freeze_time(tmp_path: Path) -> None:
    receipt, expired_authority = _signed_exclusion(
        tmp_path,
        approved_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
        active_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )

    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(
            _exclusion_snapshot(receipt),
            terminal_exclusion_authority=expired_authority,
        )

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_receipt_from_untrusted_signing_key_cannot_admit_exclusion(tmp_path: Path) -> None:
    receipt, _trusted_for_receipt = _signed_exclusion(tmp_path)
    _other_receipt, other_authority = _signed_exclusion(tmp_path)

    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(
            _exclusion_snapshot(receipt),
            terminal_exclusion_authority=other_authority,
        )

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_parsed_pdf_requires_typed_ocr_and_page_census_authorities() -> None:
    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(_pdf_snapshot(refs=[]))

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_parsed_media_requires_all_three_dual_track_authorities() -> None:
    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(_media_snapshot(confidence=0.75, refs=[]))

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_schema_dispatch_rejects_semantically_invalid_pdf_census_payload() -> None:
    refs = _pdf_refs()
    invalid_census_ref = dict(refs[1])
    invalid_census = dict(invalid_census_ref["validated_payload"])
    invalid_census["class_counts"] = {
        "native_text": 0,
        "ocr_text": 0,
        "image_only_page": 0,
        "watermark_only": 0,
    }
    invalid_census_ref["validated_payload"] = invalid_census

    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(_pdf_snapshot(refs=[refs[0], invalid_census_ref]))

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_schema_dispatch_rejects_semantically_invalid_supplemental_payload() -> None:
    refs = [
        *_pdf_refs(),
        _schema_ref(
            "ocr-render-admission",
            {"schema_version": "ocr-render-admission-v2"},
        ),
    ]
    refs.sort(key=lambda ref: str(ref["schema_id"]))

    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(_pdf_snapshot(refs=refs))

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_valid_pdf_and_media_payloads_pass_real_schema_dispatch() -> None:
    pdf_plan = build_source_freeze_plan(_pdf_snapshot())
    media_plan = build_source_freeze_plan(_media_snapshot(confidence=0.75))

    assert {root.object_kind for root in pdf_plan.roots} == {
        "PACKAGE",
        "PACKAGE_FILE",
        "ARTIFACT",
        "DOCUMENT",
        "PARSE_ATTEMPT",
        "SPAN",
    }
    assert {root.object_kind for root in media_plan.roots} == {
        "PACKAGE",
        "PACKAGE_FILE",
        "ARTIFACT",
        "DOCUMENT",
        "PARSE_ATTEMPT",
        "SPAN",
    }


@pytest.mark.parametrize(
    ("doc_type", "span_type", "locator"),
    _FORMAT_LOCATOR_CASES,
)
def test_each_format_accepts_only_its_production_span_locator_variants(
    doc_type: str,
    span_type: str,
    locator: Mapping[str, object],
) -> None:
    normalized = _validated_span_locator(
        span_type,
        locator,
        doc_type=doc_type,
    )
    assert all(normalized.get(key) == value for key, value in locator.items())


@pytest.mark.parametrize(
    ("source_doc_type", "target_doc_type", "span_type", "locator"),
    _CROSS_FORMAT_LOCATOR_CASES,
)
def test_cross_format_span_locator_matrix_fails_closed(
    source_doc_type: str,
    target_doc_type: str,
    span_type: str,
    locator: Mapping[str, object],
) -> None:
    with pytest.raises(Task7SourceFreezeError) as caught:
        _validated_span_locator(
            span_type,
            locator,
            doc_type=target_doc_type,
        )

    assert source_doc_type != target_doc_type
    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


@pytest.mark.parametrize(
    ("doc_type", "span_type", "locator"),
    _FORMAT_LOCATOR_CASES,
)
def test_all_eight_formats_pass_complete_source_dispatch(
    doc_type: str,
    span_type: str,
    locator: Mapping[str, object],
) -> None:
    plan = build_source_freeze_plan(_all_format_snapshot(doc_type, span_type, locator))

    assert any(root.object_kind == "SPAN" for root in plan.roots)


def test_image_ocr_requires_its_real_terminal_evidence_authority() -> None:
    snapshot = _format_snapshot(
        doc_type="IMAGE",
        extension="png",
        span_type="PAGE_TEXT",
        locator={"page": 1, "line": 1, "source": "ocr_image"},
        refs=[],
    )

    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(snapshot)

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_image_ocr_rejects_pdf_only_page_census_authority() -> None:
    refs = [*_image_refs(), _pdf_refs()[1]]
    refs.sort(key=lambda ref: str(ref["schema_id"]))
    snapshot = _format_snapshot(
        doc_type="IMAGE",
        extension="png",
        span_type="PAGE_TEXT",
        locator={"page": 1, "line": 1, "source": "ocr_image"},
        refs=refs,
    )

    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(snapshot)

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


@pytest.mark.parametrize(
    "locator",
    [
        {"page": 1, "line": None},
        {
            "page": 1,
            "line": 1,
            "source": "ocr",
            "dimension_tolerance_used": False,
        },
        {
            "page": 1,
            "line": 1,
            "source": "ocr",
            "poppler_runtime_identity_sha256": "not-a-sha256",
        },
        {
            "page": 1,
            "line": 1,
            "source": "ocr_rescue",
            "ocr_rescue_transform_chain": "not-an-ordered-transform-list",
            "ocr_rescue_agreement_score": 0.9,
        },
        {
            "page": 1,
            "line": 1,
            "source": "ocr_rescue",
            "ocr_rescue_confidence_distribution": True,
        },
    ],
    ids=(
        "missing-line",
        "orphan-boolean",
        "malformed-runtime-hash",
        "malformed-transform",
        "boolean-confidence",
    ),
)
def test_page_text_locator_uses_exact_production_shape(
    locator: Mapping[str, object],
) -> None:
    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(_pdf_snapshot(locator=locator))

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_negative_and_positive_zero_have_identical_span_root_identity() -> None:
    negative = build_source_freeze_plan(_media_snapshot(confidence=-0.0))
    positive = build_source_freeze_plan(_media_snapshot(confidence=0.0))
    negative_span = next(root for root in negative.roots if root.object_kind == "SPAN")
    positive_span = next(root for root in positive.roots if root.object_kind == "SPAN")

    assert negative_span.object_identity_sha256 == positive_span.object_identity_sha256
    assert negative_span.source_lineage_object_id == positive_span.source_lineage_object_id


def test_integer_and_float_timecode_confidence_have_identical_span_root_identity() -> None:
    integer = build_source_freeze_plan(_media_snapshot(confidence=0))  # type: ignore[arg-type]
    binary64 = build_source_freeze_plan(_media_snapshot(confidence=0.0))
    integer_span = next(root for root in integer.roots if root.object_kind == "SPAN")
    binary64_span = next(root for root in binary64.roots if root.object_kind == "SPAN")

    assert integer_span.object_identity_sha256 == binary64_span.object_identity_sha256
    assert integer_span.source_lineage_object_id == binary64_span.source_lineage_object_id


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_binary64_locator_values_fail_closed(confidence: float) -> None:
    snapshot = _media_snapshot(confidence=0.75)
    span = dict(snapshot.spans[0])
    span["locator"] = {**span["locator"], "confidence": confidence}

    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(replace(snapshot, spans=(span,)))

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("updated_at", UPDATED_AT.replace(microsecond=123457), "TASK7_SOURCE_FREEZE_FENCED"),
        ("status", "SEALED", "TASK7_SOURCE_FREEZE_FENCED"),
        ("source_binding_state", "BOUND", "TASK7_SOURCE_FREEZE_FENCED"),
    ],
)
def test_observed_row_fence_rejects_lock_wait_drift(
    field: str,
    replacement: object,
    code: str,
) -> None:
    snapshot = _snapshot()
    package = dict(snapshot.package)
    package[field] = replacement
    changed = replace(snapshot, package=package)

    with pytest.raises(Task7SourceFreezeError) as caught:
        validate_locked_source_snapshot(_observation(snapshot), changed)

    assert caught.value.code == code


def test_observed_manifest_digest_fence_rejects_metadata_drift() -> None:
    original = _snapshot()
    changed_manifest = _manifest()
    changed_manifest["source_tree_fingerprint"] = hashlib.sha256(b"changed tree").hexdigest()
    changed_manifest["manifest_sha256"] = canonical_source_manifest_sha256(changed_manifest)
    changed = _snapshot(manifest=changed_manifest)

    with pytest.raises(Task7SourceFreezeError) as caught:
        validate_locked_source_snapshot(_observation(original), changed)

    assert caught.value.code == "TASK7_SOURCE_FREEZE_FENCED"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: replace(s, files=()),
        lambda s: replace(
            s,
            files=({**s.files[0], "path_hash": hashlib.sha256(b"drift").hexdigest()},),
        ),
        lambda s: replace(s, spans=()),
        lambda s: replace(
            s,
            spans=({**s.spans[0], "text_excerpt": "content hash drift"},),
        ),
        lambda s: replace(
            s,
            documents=({**s.documents[0], "parse_status": "PENDING"},),
        ),
        lambda s: replace(
            s,
            files=({**s.files[0], "file_status": "deferred"},),
        ),
        lambda s: replace(
            s,
            artifacts=({**s.artifacts[0], "uri": None},),
        ),
        lambda s: replace(
            s,
            documents=(
                {
                    **s.documents[0],
                    "metadata": {
                        **s.documents[0]["metadata"],
                        "parse_warning_codes": ["unsafe/private/value"],
                    },
                },
            ),
        ),
    ],
    ids=(
        "missing-file",
        "membership-drift",
        "no-text-span",
        "span-hash-drift",
        "nonterminal-document",
        "deferred-file",
        "missing-object-proof",
        "unsafe-parser-code",
    ),
)
def test_closed_bindability_predicate_rejects_nonterminal_graph(
    mutate: Any,
) -> None:
    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(mutate(_snapshot()))

    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


@pytest.mark.parametrize(
    "attempts",
    [
        (),
        (
            {
                **_snapshot().attempts[0],
                "receipt_sha256": hashlib.sha256(b"wrong receipt").hexdigest(),
            },
        ),
        (_snapshot().attempts[0], _snapshot().attempts[0]),
    ],
    ids=("missing", "receipt-drift", "nonunique"),
)
def test_terminal_attempt_must_be_uniquely_reconstructible(
    attempts: tuple[Mapping[str, Any], ...],
) -> None:
    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(replace(_snapshot(), attempts=attempts))

    assert caught.value.code == "TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE"


def test_error_surface_never_echoes_private_title_uri_or_metadata() -> None:
    private_title = "PRIVATE-COMPANY-NAME"
    private_uri = "object://bucket/private/customer/path.pdf"
    snapshot = _snapshot()
    bad_artifact = {
        **snapshot.artifacts[0],
        "title": private_title,
        "uri": private_uri,
        "metadata": {"private": "recipient@example.invalid"},
    }

    with pytest.raises(Task7SourceFreezeError) as caught:
        build_source_freeze_plan(replace(snapshot, artifacts=(bad_artifact,)))

    rendered = f"{caught.value!s} {caught.value!r}"
    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"
    assert private_title not in rendered
    assert private_uri not in rendered
    assert "recipient@example.invalid" not in rendered


class _Mappings:
    def __init__(self, rows: tuple[Mapping[str, Any], ...]) -> None:
        self._rows = rows

    def all(self) -> list[Mapping[str, Any]]:
        return list(self._rows)


class _Result:
    def __init__(self, rows: tuple[Mapping[str, Any], ...]) -> None:
        self._rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)

    def fetchone(self) -> Mapping[str, Any] | None:
        return None if not self._rows else self._rows[0]


class _RecordingConnection:
    def __init__(self, snapshot: SourceFreezeSnapshot) -> None:
        self.snapshot = snapshot
        self.statements: list[tuple[str, Mapping[str, Any] | None]] = []

    def execute(
        self,
        statement: object,
        parameters: Mapping[str, Any] | None = None,
    ) -> _Result:
        sql = " ".join(str(statement).split())
        self.statements.append((sql, parameters))
        if "FROM data_room_packages" in sql:
            return _Result((self.snapshot.package,))
        if "FROM data_room_package_files" in sql:
            return _Result(self.snapshot.files)
        if "FROM document_artifacts" in sql:
            return _Result(self.snapshot.artifacts)
        if "FROM documents" in sql:
            return _Result(self.snapshot.documents)
        if "FROM document_parse_attempts" in sql:
            return _Result(self.snapshot.attempts)
        if "FROM document_spans" in sql:
            return _Result(self.snapshot.spans)
        return _Result(())


class _WriteResult:
    rowcount = 1


class _WriteConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, Mapping[str, Any] | None]] = []

    def execute(
        self,
        statement: object,
        parameters: Mapping[str, Any] | None = None,
    ) -> _WriteResult:
        self.statements.append((" ".join(str(statement).split()), parameters))
        return _WriteResult()


def test_repository_locks_the_six_relations_in_the_reviewed_total_order() -> None:
    connection = _RecordingConnection(_snapshot())
    repository = PostgresTask7SourceFreezeRepository(
        connection=connection,  # type: ignore[arg-type]
        tenant_id=TENANT_ID,
    )

    loaded = repository.lock_source_graph(_observation())

    assert loaded == _snapshot()
    lock_sql = [sql for sql, _ in connection.statements if "FOR UPDATE" in sql]
    assert len(lock_sql) == 6
    observed_order = tuple(
        next(table for table in LOCKED_SOURCE_QUERY_ORDER if f"FROM {table}" in sql)
        for sql in lock_sql
    )
    assert observed_order == LOCKED_SOURCE_QUERY_ORDER
    assert "ORDER BY sequence, file_entry_id" in lock_sql[1]
    assert "ORDER BY doc_id" in lock_sql[2]
    assert "ORDER BY document_id" in lock_sql[3]
    assert "ORDER BY document_id, attempt_ordinal, parse_attempt_id" in lock_sql[4]
    assert "ORDER BY document_id, span_id" in lock_sql[5]


def test_stale_package_fence_stops_before_the_first_child_lock() -> None:
    current = _snapshot(package_updated_at=UPDATED_AT.replace(microsecond=123457))
    connection = _RecordingConnection(current)
    repository = PostgresTask7SourceFreezeRepository(
        connection=connection,  # type: ignore[arg-type]
        tenant_id=TENANT_ID,
    )

    with pytest.raises(Task7SourceFreezeError) as caught:
        repository.lock_source_graph(_observation())

    lock_sql = [sql for sql, _ in connection.statements if "FOR UPDATE" in sql]
    assert caught.value.code == "TASK7_SOURCE_FREEZE_FENCED"
    assert len(lock_sql) == 1
    assert "FROM data_room_packages" in lock_sql[0]
    assert all(
        f"FROM {child_table}" not in lock_sql[0] for child_table in LOCKED_SOURCE_QUERY_ORDER[1:]
    )


def test_native_updates_mutate_only_binding_fields_with_scoped_state_guards() -> None:
    connection = _WriteConnection()
    repository = object.__new__(PostgresTask7SourceFreezeRepository)
    repository._connection = connection  # type: ignore[attr-defined]
    repository._tenant_id = TENANT_ID  # type: ignore[attr-defined]
    plan = build_source_freeze_plan(_snapshot())

    repository._bind_native_rows(plan, _observation())  # type: ignore[attr-defined]

    statements = [sql for sql, _ in connection.statements]
    assert [sql.split(" SET", maxsplit=1)[0] for sql in statements] == [
        "UPDATE data_room_package_files",
        "UPDATE document_artifacts",
        "UPDATE documents",
        "UPDATE document_parse_attempts",
        "UPDATE document_spans",
        "UPDATE data_room_packages",
    ]
    forbidden_assignments = {
        "metadata=",
        "sha256=",
        "uri=",
        "path_hash=",
        "locator=",
        "text_excerpt=",
        "content_hash=",
        "parser_name=",
        "parser_version=",
        "receipt_sha256=",
    }
    for sql in statements:
        assignments = sql.split(" SET", maxsplit=1)[1].split(" WHERE", maxsplit=1)[0]
        assert not any(token in assignments for token in forbidden_assignments)
        assert "tenant_id=CAST(:tenant_id AS uuid)" in sql
        assert "deal_id=CAST(:deal_id AS uuid)" in sql
    # Migration 0038 requires the staged null document pair to bind atomically here.
    assert "doc_id=expected.doc_id" in statements[0]
    assert "document_id=expected.document_id" in statements[0]
    for sql in statements[1:]:
        assignments = sql.split(" SET", maxsplit=1)[1].split(" WHERE", maxsplit=1)[0]
        assert "doc_id=" not in assignments
        assert "document_id=" not in assignments
    assert all("unnest(" in sql for sql in statements[:-1])
    assert "updated_at=:observed_updated_at" in statements[-1]
    assert "status='OPEN'" in statements[-1]
    assert "source_binding_state='UNBOUND'" in statements[-1]


def test_sealed_byte_identical_retry_performs_no_insert_or_update(monkeypatch: Any) -> None:
    initial_plan = build_source_freeze_plan(_snapshot())
    bound = initial_plan.expected_bound_snapshot()
    retry_plan = build_source_freeze_plan(bound)
    observation = replace(
        _observation(),
        observed_status="SEALED",
        observed_source_binding_state="BOUND",
    )
    repository = object.__new__(PostgresTask7SourceFreezeRepository)
    repository._tenant_id = TENANT_ID  # type: ignore[attr-defined]
    monkeypatch.setattr(repository, "lock_source_graph", lambda _: bound)
    monkeypatch.setattr(repository, "_reload_source_graph", lambda _: bound)
    monkeypatch.setattr(
        repository,
        "_load_roots",
        lambda _: tuple(root.as_row() for root in retry_plan.roots),
    )
    monkeypatch.setattr(
        repository,
        "_insert_roots",
        lambda _: pytest.fail("retry inserted roots"),
    )
    monkeypatch.setattr(
        repository,
        "_bind_native_rows",
        lambda *_: pytest.fail("retry updated native rows"),
    )

    receipt = repository.freeze_observed_package(observation)

    assert receipt.root_set_sha256 == retry_plan.root_set_sha256
    assert receipt.status == "SEALED"


class _Transaction:
    def __init__(self, connection: object, events: list[str], ordinal: int) -> None:
        self.connection = connection
        self.events = events
        self.ordinal = ordinal

    def __enter__(self) -> object:
        self.events.append(f"begin-{self.ordinal}")
        return self.connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.events.append(f"end-{self.ordinal}")


class _Engine:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.connections = [object(), object()]
        self._ordinal = 0

    def begin(self) -> _Transaction:
        ordinal = self._ordinal
        self._ordinal += 1
        return _Transaction(self.connections[ordinal], self.events, ordinal)


class _CompositionRepository:
    instances: list[_CompositionRepository] = []

    def __init__(
        self,
        connection: object,
        tenant_id: str,
        *,
        terminal_exclusion_authority: object = None,
    ) -> None:
        self.connection = connection
        self.tenant_id = tenant_id
        assert terminal_exclusion_authority is None
        self.instances.append(self)

    def observe_source_package(
        self,
        *,
        deal_id: str,
        package_id: str,
    ) -> SourceFreezeObservation:
        assert deal_id == DEAL_ID
        assert package_id == PACKAGE_ID
        return _observation()

    def freeze_observed_package(
        self,
        observation: SourceFreezeObservation,
    ) -> object:
        assert observation == _observation()
        return {"status": "SEALED"}


def test_composition_observes_before_opening_the_locking_transaction() -> None:
    _CompositionRepository.instances.clear()
    engine = _Engine()

    result = freeze_source_package(
        engine=engine,  # type: ignore[arg-type]
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        package_id=PACKAGE_ID,
        repository_factory=_CompositionRepository,  # type: ignore[arg-type]
    )

    assert result == {"status": "SEALED"}
    assert engine.events == ["begin-0", "end-0", "begin-1", "end-1"]
    assert [row.connection for row in _CompositionRepository.instances] == engine.connections


def test_reload_requires_exact_roots_bindings_and_selected_attempt() -> None:
    plan = build_source_freeze_plan(_snapshot())
    reloaded = plan.expected_bound_snapshot(_snapshot())
    root_rows = tuple(root.as_row() for root in plan.roots)

    validate_reloaded_source_freeze(plan, reloaded, root_rows)

    drifted = replace(
        reloaded,
        spans=({**reloaded.spans[0], "parse_attempt_id": str(uuid4())},),
    )
    with pytest.raises(Task7SourceFreezeError) as caught:
        validate_reloaded_source_freeze(plan, drifted, root_rows)
    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_byte_identical_retry_accepts_only_the_same_persisted_rows() -> None:
    plan = build_source_freeze_plan(_snapshot())
    bound = plan.expected_bound_snapshot(_snapshot())
    retry_plan = build_source_freeze_plan(bound)
    roots = tuple(root.as_row() for root in plan.roots)

    validate_reloaded_source_freeze(retry_plan, bound, roots)

    changed_roots = tuple(
        {**row, "content_sha256": hashlib.sha256(b"retry drift").hexdigest()}
        if row["object_kind"] == "SPAN"
        else row
        for row in roots
    )
    with pytest.raises(Task7SourceFreezeError) as caught:
        validate_reloaded_source_freeze(retry_plan, bound, changed_roots)
    assert caught.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"


def test_module_has_no_network_object_store_or_filesystem_io() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "idis"
        / "persistence"
        / "repositories"
        / "task7_source_freeze.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "requests",
        "httpx",
        "urllib",
        "boto",
        "object_store",
        "open(",
        "Path(",
        "InMemory",
    )
    assert all(token not in source for token in forbidden)
