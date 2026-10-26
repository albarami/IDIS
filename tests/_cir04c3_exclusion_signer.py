"""Disposable Ed25519 authority for a genuine watermark-only exclusion.

The signed manifest is bound to the bytes, alias, tenant, and fingerprint the
caller will ingest. It does not sign an encrypted or unreadable file.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from reportlab.pdfgen import canvas

from idis.models.source_package_manifest import (
    SourcePackageManifest,
    canonical_source_manifest_sha256,
)
from idis.parsers.watermark_page_classifier import (
    build_watermark_page_canonical_exemplar_bitmap,
    default_watermark_page_policy,
)
from idis.runtime.governance_envelopes import (
    NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION,
    WATERMARK_BATCH_ATTESTATION_SCHEMA_VERSION,
    canonicalize_json,
    sign_envelope,
)
from idis.runtime.secure_files import protect_path_for_current_user
from idis.services.ingestion.no_substantive_content_attestation import (
    NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV,
    NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION,
)

SIGNER_ID = "fictional-watermark-governance-signer-v1"
REVIEWER_ID = "fictional-watermark-api-reviewer"
APPROVED_AT = "2020-01-01T00:00:00Z"
EXPIRES_AT = "2099-01-01T00:00:00Z"
EXCLUSION_UPLOAD_NAME = "company-001-document-00001.pdf"
PARSED_UPLOAD_NAME = "company-001-document-00002.pdf"
COMPANY_FILE_COUNTS = {"company-001": 58, "company-002": 209}
WATERMARK_OCR_ENV = {
    "IDIS_OCR_ENABLED": "true",
    "IDIS_OCR_ADAPTER": "tesseract",
    "IDIS_OCR_DPI": "200",
    "IDIS_OCR_MAX_PAGES": "2",
    "IDIS_OCR_TIMEOUT_SECONDS": "60",
}


def watermark_only_pdf() -> bytes:
    """Return one square page whose solid mark is the classifier's 2% exemplar."""
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(500, 500), pageCompression=0, invariant=1)
    document.setFillColorRGB(0, 0, 0)
    document.rect(200, 225, 100, 50, stroke=0, fill=1)
    document.showPage()
    document.save()
    return output.getvalue()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonicalize_json(value)).hexdigest()


def _canonical_bitmap(pdf_bytes: bytes) -> tuple[bytes, str]:
    from pdf2image import convert_from_bytes

    from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime

    binding = resolve_pinned_poppler_runtime()
    pages = convert_from_bytes(
        pdf_bytes,
        dpi=200,
        first_page=1,
        last_page=1,
        poppler_path=str(binding.binary_dir),
    )
    if len(pages) != 1:
        raise RuntimeError("WATERMARK_RENDER_PAGE_COUNT")
    policy = default_watermark_page_policy()
    with pages[0] as page:
        packed = build_watermark_page_canonical_exemplar_bitmap(
            page_image=page,
            render_dpi=policy.render_dpi,
            policy=policy,
        )
    return packed, hashlib.sha256(packed).hexdigest()


def _private_and_public_pem() -> tuple[bytes, bytes]:
    key = Ed25519PrivateKey.generate()
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def _seal(path: Path) -> None:
    protect_path_for_current_user(path.parent, directory=True)
    protect_path_for_current_user(path)
    path.chmod(stat.S_IREAD if os.name == "nt" else stat.S_IRUSR)


def write_signed_watermark_authority(
    tmp_path: Path,
    *,
    tenant_id: str,
    safe_alias: str,
    source_tree_fingerprint: str,
    pdf_bytes: bytes,
) -> dict[str, str]:
    """Write a sealed v2 manifest and disposable public key for one watermark PDF."""
    private_pem, public_pem = _private_and_public_pem()
    content_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    packed_bitmap, canonical_render_sha256 = _canonical_bitmap(pdf_bytes)
    policy = default_watermark_page_policy()
    approval = {
        "decision": "APPROVED",
        "reviewer_id": REVIEWER_ID,
        "approved_at": APPROVED_AT,
        "expires_at": EXPIRES_AT,
    }
    exemplar_payload = {
        "tenant_id": tenant_id,
        "source_tree_fingerprint": source_tree_fingerprint,
        "safe_alias": safe_alias,
        "content_sha256": content_sha256,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "attestation_text_sha256": hashlib.sha256(
            b"fictional deterministic watermark-only fixture"
        ).hexdigest(),
        "reviewer_approval": approval,
    }
    exemplar = sign_envelope(
        schema_version=NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=exemplar_payload,
        private_key_pem=private_pem,
    )
    candidates = [
        {
            "safe_alias": safe_alias,
            "content_sha256": content_sha256,
            "page_number": 1,
            "canonical_render_sha256": canonical_render_sha256,
            "similarity_score": "1.000000",
        }
    ]
    candidate_set_sha256 = _sha256_json(candidates)
    prescan = {
        "schema_version": "watermark-page-prescan-v1",
        "source_tree_fingerprint": source_tree_fingerprint,
        "classifier_policy_id": policy.policy_id,
        "classifier_policy_sha256": policy.policy_sha256,
        "total_file_count": 267,
        "company_file_counts": dict(COMPANY_FILE_COUNTS),
        "candidate_count": 1,
        "candidate_set_sha256": candidate_set_sha256,
        "sample_set_sha256": candidate_set_sha256,
        "unresolved_count": 0,
        "status": "COMPLETE",
        "blocker_code": None,
    }
    batch_payload = {
        "tenant_id": tenant_id,
        "source_tree_fingerprint": source_tree_fingerprint,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "classifier_policy_id": policy.policy_id,
        "classifier_policy_sha256": policy.policy_sha256,
        "exemplar_safe_alias": safe_alias,
        "exemplar_content_sha256": content_sha256,
        "exemplar_envelope_sha256": _sha256_json(exemplar),
        "exemplar_canonical_bitmap_sha256": hashlib.sha256(packed_bitmap).hexdigest(),
        "prescan_receipt_sha256": _sha256_json(prescan),
        "candidate_set_sha256": candidate_set_sha256,
        "candidates": candidates,
        "sample_set_sha256": candidate_set_sha256,
        "attestation_text_sha256": hashlib.sha256(
            b"fictional one-page watermark batch"
        ).hexdigest(),
        "reviewer_approval": approval,
    }
    batch = sign_envelope(
        schema_version=WATERMARK_BATCH_ATTESTATION_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=batch_payload,
        private_key_pem=private_pem,
    )
    manifest = {
        "schema_version": NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION,
        "exemplar_attestation_envelope": exemplar,
        "exemplar_canonical_bitmap_base64": base64.b64encode(packed_bitmap).decode("ascii"),
        "watermark_prescan_receipt": prescan,
        "watermark_batch_attestation_envelope": batch,
    }
    root = tmp_path / "watermark-authority"
    root.mkdir()
    manifest_path = root / "watermark-manifest-v2.json"
    public_key_path = root / "watermark-public.pem"
    manifest_path.write_bytes(canonicalize_json(manifest))
    public_key_path.write_bytes(public_pem)
    _seal(manifest_path)
    _seal(public_key_path)
    return {
        NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV: str(manifest_path),
        "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": SIGNER_ID,
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_key_path),
    }


def watermark_service_options(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Load the verified v2 authority and the OCR runtime that can classify it."""
    from idis.services.ingestion.defaults import (
        _watermark_page_runtime_config,
        build_default_ocr_config,
    )
    from idis.services.ingestion.no_substantive_content_attestation import (
        load_document_no_substantive_content_attestation,
    )

    values = {**os.environ, **WATERMARK_OCR_ENV}
    if env is not None:
        values.update(env)
    verified = load_document_no_substantive_content_attestation(env=values)
    return {
        "ocr_config": build_default_ocr_config(values),
        "no_substantive_content_attestation": verified,
        "watermark_page_config": _watermark_page_runtime_config(verified),
    }


def configure_watermark_runtime(monkeypatch: Any, authority: Mapping[str, str]) -> None:
    """Install signer and OCR settings before the app builds its ingestion service."""
    for key, value in {**WATERMARK_OCR_ENV, **authority}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("IDIS_MEDIA_ADAPTER", raising=False)
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)


def build_bound_source_manifest(
    documents: Sequence[Mapping[str, Any]],
    *,
    source_tree_fingerprint: str,
) -> SourcePackageManifest:
    """Bind already-uploaded rows whose names are company-001-document-NNNNN.pdf."""
    ordered = sorted(
        documents,
        key=lambda document: str(document["source_metadata"]["upload_filename"]),
    )
    files: list[dict[str, object]] = []
    for sequence, document in enumerate(ordered):
        metadata = document["source_metadata"]
        name = str(metadata["upload_filename"])
        extension = name.rsplit(".", 1)[-1]
        size_bytes = metadata["size_bytes"]
        if type(size_bytes) is not int:
            raise ValueError("source size_bytes must be an int")
        files.append(
            {
                "sequence": sequence,
                "generated_upload_name": name,
                "source_locator_sha256": hashlib.sha256(
                    canonicalize_json(
                        [
                            "c3-exclusion-source-locator-v1",
                            str(document["tenant_id"]),
                            str(document["deal_id"]),
                            sequence,
                        ]
                    )
                ).hexdigest(),
                "content_sha256": str(document["sha256"]),
                "size_bytes": size_bytes,
                "extension": extension,
                "document_id": str(document["document_id"]),
                "doc_id": str(document["doc_id"]),
            }
        )
    total_bytes = 0
    for row in files:
        row_size = row["size_bytes"]
        if type(row_size) is not int:
            raise ValueError("source size_bytes must be an int")
        total_bytes += row_size
    payload: dict[str, object] = {
        "schema_version": "source-package-manifest-v1",
        "company_alias": "company-001",
        "deal_alias": "deal-001",
        "document_set_alias": "documents-001",
        "run_alias": "run-001",
        "ledger_alias": "ledger-001",
        "package_alias": "package-001",
        "source_tree_fingerprint": source_tree_fingerprint,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
    }
    payload["manifest_sha256"] = canonical_source_manifest_sha256(payload)
    return SourcePackageManifest.model_validate(payload)
