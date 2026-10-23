"""RED integration contract for production watermark-classifier composition.

The fixture, governance key, and signed v2 manifest are generated locally and
contain only fictional identities.  The test enters through the production
``build_default_ingestion_service`` composition root and the ordinary
``IngestionService.ingest_bytes`` path.  It substitutes only the OCR adapter so
the primary-plus-24-candidate no-consensus terminal proof is deterministic; it
does not inject parser metadata or replace the service parse method.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pdf2image import convert_from_bytes
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from idis.audit.sink import InMemoryAuditSink
from idis.parsers import ocr
from idis.parsers import watermark_page_classifier as watermark_classifier
from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime
from idis.runtime.governance_envelopes import canonicalize_json
from idis.runtime.secure_files import protect_path_for_current_user
from idis.services.ingestion import defaults
from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    verify_document_no_substantive_content_attestation_manifest,
)
from idis.services.ingestion.service import IngestionContext
from tests._linux_poppler_gate import requires_linux_governed_poppler

TENANT_ID = UUID("30000000-0000-4000-8000-000000000001")
DEAL_ID = UUID("40000000-0000-4000-8000-000000000001")
SAFE_ALIAS = "company-002-document-00053.pdf"
SOURCE_TREE_FINGERPRINT = hashlib.sha256(b"fictional-watermark-source-tree").hexdigest()
SIGNER_ID = "fictional-watermark-composition-signer-v1"
APPROVED_AT = "2020-01-01T00:00:00Z"
EXPIRES_AT = "2099-01-01T00:00:00Z"
INGEST_MAX_BYTES = 512 * 1024 * 1024


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonicalize_json(value)).hexdigest()


def _sign(
    private_key: Ed25519PrivateKey,
    *,
    schema_version: str,
    payload: dict[str, object],
) -> dict[str, object]:
    signable: dict[str, object] = {
        "schema_version": schema_version,
        "signer_id": SIGNER_ID,
        "payload": payload,
    }
    return {
        **signable,
        "signature_base64": base64.b64encode(private_key.sign(canonicalize_json(signable))).decode(
            "ascii"
        ),
    }


def _fixture_page() -> Image.Image:
    """Return a deterministic, text-free, 2%-ink fictional stamp page."""

    page = Image.new("L", (1000, 1000), color=255)
    draw = ImageDraw.Draw(page)
    draw.rectangle((400, 450, 599, 549), fill=0)
    return page


def _fixture_pdf() -> bytes:
    """Embed the fixture at exactly 1,000 pixels when rendered at 200 DPI."""

    with _fixture_page() as page:
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


def _packed_canonical_bitmap(page: Image.Image) -> bytes:
    policy = watermark_classifier.default_watermark_page_policy()
    packed = watermark_classifier.build_watermark_page_canonical_exemplar_bitmap(
        page_image=page,
        render_dpi=policy.render_dpi,
        policy=policy,
    )
    assert len(packed) == 2048
    return packed


def _signed_manifest(pdf_bytes: bytes) -> tuple[dict[str, object], bytes]:
    """Build one coherent signed exemplar/batch manifest entirely offline."""

    private_key = Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"fictional-watermark-composition-ed25519-key").digest()
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    binding = resolve_pinned_poppler_runtime()
    rendered_pages = convert_from_bytes(
        pdf_bytes,
        dpi=200,
        first_page=1,
        last_page=1,
        fmt="png",
        thread_count=1,
        poppler_path=str(binding.binary_dir),
    )
    assert len(rendered_pages) == 1
    with rendered_pages[0] as rendered_page:
        canonical_bitmap = _packed_canonical_bitmap(rendered_page)

    content_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    canonical_render_sha256 = hashlib.sha256(canonical_bitmap).hexdigest()
    policy = watermark_classifier.default_watermark_page_policy()
    approval = {
        "decision": "APPROVED",
        "reviewer_id": "fictional-watermark-reviewer-v1",
        "approved_at": APPROVED_AT,
        "expires_at": EXPIRES_AT,
    }
    exemplar_payload: dict[str, object] = {
        "tenant_id": str(TENANT_ID),
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "safe_alias": SAFE_ALIAS,
        "content_sha256": content_sha256,
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": "watermark_only",
        "attestation_text_sha256": hashlib.sha256(
            b"fictional reviewer confirmed a text-free watermark-only page"
        ).hexdigest(),
        "reviewer_approval": approval,
    }
    exemplar_envelope = _sign(
        private_key,
        schema_version="document-no-substantive-content-attestation-v1",
        payload=exemplar_payload,
    )
    candidates: list[dict[str, object]] = [
        {
            "safe_alias": SAFE_ALIAS,
            "content_sha256": content_sha256,
            "page_number": 1,
            "canonical_render_sha256": canonical_render_sha256,
            "similarity_score": "1.000000",
        }
    ]
    candidate_set_sha256 = _sha256_json(candidates)
    prescan_receipt: dict[str, object] = {
        "schema_version": "watermark-page-prescan-v1",
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
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
    batch_payload: dict[str, object] = {
        "tenant_id": str(TENANT_ID),
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": "watermark_only",
        "classifier_policy_id": policy.policy_id,
        "classifier_policy_sha256": policy.policy_sha256,
        "exemplar_safe_alias": SAFE_ALIAS,
        "exemplar_content_sha256": content_sha256,
        "exemplar_envelope_sha256": _sha256_json(exemplar_envelope),
        "exemplar_canonical_bitmap_sha256": canonical_render_sha256,
        "prescan_receipt_sha256": _sha256_json(prescan_receipt),
        "candidate_set_sha256": candidate_set_sha256,
        "candidates": candidates,
        "sample_set_sha256": candidate_set_sha256,
        "attestation_text_sha256": hashlib.sha256(
            b"fictional batch approval for the enumerated fixture hash"
        ).hexdigest(),
        "reviewer_approval": approval,
    }
    return (
        {
            "schema_version": "document-no-substantive-content-attestation-manifest-v2",
            "exemplar_attestation_envelope": exemplar_envelope,
            "exemplar_canonical_bitmap_base64": base64.b64encode(canonical_bitmap).decode("ascii"),
            "watermark_prescan_receipt": prescan_receipt,
            "watermark_batch_attestation_envelope": _sign(
                private_key,
                schema_version=("document-no-substantive-content-watermark-batch-attestation-v1"),
                payload=batch_payload,
            ),
        },
        public_pem,
    )


def _verified_runtime_config(pdf_bytes: bytes) -> watermark_classifier.WatermarkPageRuntimeConfig:
    """Derive runtime material through render, manifest signing, and verification."""

    manifest, public_pem = _signed_manifest(pdf_bytes)
    verified = verify_document_no_substantive_content_attestation_manifest(
        manifest=manifest,
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_pem,
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )
    runtime_config = defaults._watermark_page_runtime_config(verified)  # noqa: SLF001
    assert runtime_config is not None
    return runtime_config


def _seal(path: Path, *, directory: bool = False) -> None:
    protect_path_for_current_user(path, directory=directory)
    if not directory:
        path.chmod(stat.S_IREAD if os.name == "nt" else stat.S_IRUSR)


def _strict_env(tmp_path: Path, pdf_bytes: bytes) -> dict[str, str]:
    manifest, public_pem = _signed_manifest(pdf_bytes)
    governance_root = tmp_path / "fictional-governance"
    governance_root.mkdir()
    manifest_path = governance_root / "watermark-manifest-v2.json"
    public_key_path = governance_root / "trusted-ed25519-public.pem"
    manifest_path.write_bytes(canonicalize_json(manifest))
    public_key_path.write_bytes(public_pem)
    _seal(governance_root, directory=True)
    _seal(manifest_path)
    _seal(public_key_path)
    return {
        "IDIS_INGEST_MAX_BYTES": str(INGEST_MAX_BYTES),
        "IDIS_OCR_ENABLED": "true",
        "IDIS_OCR_ADAPTER": "tesseract",
        "IDIS_OCR_MAX_PAGES": "72",
        "IDIS_OCR_TIMEOUT_SECONDS": "30",
        "IDIS_OCR_DPI": "200",
        "IDIS_OCR_LANGUAGE": "eng",
        "IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH": str(manifest_path),
        "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": SIGNER_ID,
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_key_path),
    }


class _DeterministicNoConsensusOcrAdapter:
    """Return a complete, content-free one-page OCR terminal proof."""

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[ocr.OcrPageText]:
        assert data.startswith(b"%PDF-")
        assert max_pages >= 1
        assert timeout_seconds > 0
        page_receipt = ocr._no_consensus_page_execution_receipt(  # noqa: SLF001
            page_number=1,
            candidate_receipt_sha256=hashlib.sha256(
                b"fictional-complete-24-candidate-receipt"
            ).hexdigest(),
        )
        file_proof = ocr._ocr_no_consensus_file_proof((page_receipt,))  # noqa: SLF001
        raise ocr.OcrRescueNoConsensusError(
            "OCR rescue produced no unique consensus",
            file_proof=file_proof,
        )

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[ocr.OcrPageText]:
        del data, timeout_seconds
        raise AssertionError("PDF composition test must not use the image OCR entry point")


def _deterministic_adapter_factory(**_kwargs: Any) -> _DeterministicNoConsensusOcrAdapter:
    return _DeterministicNoConsensusOcrAdapter()


@requires_linux_governed_poppler
def test_signed_v2_manifest_composition_persists_watermark_terminal_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production composition root must carry v2 admission into PDF OCR."""

    pdf_bytes = _fixture_pdf()
    env = _strict_env(tmp_path, pdf_bytes)
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    monkeypatch.setattr(defaults, "TesseractOcrAdapter", _deterministic_adapter_factory)

    service = defaults.build_default_ingestion_service(
        audit_sink=InMemoryAuditSink(),
        env=env,
        strict=True,
    )
    result = service.ingest_bytes(
        IngestionContext(
            tenant_id=TENANT_ID,
            actor_id="fictional-slice100-composition-worker",
            request_id="fictional-watermark-composition-request",
            idempotency_key="fictional-watermark-composition-key",
        ),
        DEAL_ID,
        filename=SAFE_ALIAS,
        media_type="application/pdf",
        data=pdf_bytes,
        metadata={
            "upload_filename": SAFE_ALIAS,
            "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        },
    )

    assert result.document_id is not None
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    receipt = document.metadata.get("document_no_substantive_content_receipt")
    parse_error_codes = [error.details.get("parse_error_code") for error in result.errors]
    assert isinstance(receipt, dict), (
        "expected the durable watermark_only terminal receipt, but the production "
        "composition returned "
        f"terminal_disposition={result.terminal_disposition!r}, "
        f"parse_error_codes={parse_error_codes!r}"
    )
    assert result.success is False
    assert result.terminal_disposition == DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
    assert result.span_count == 0
    assert receipt["schema_version"] == "document-no-substantive-content-receipt-v2"
    assert receipt["variant"] == "watermark_only"
    assert receipt["whole_document_terminal"] is True
    assert receipt["classified_page_count"] == 1
    assert receipt["substantive_span_count"] == 0
    assert receipt["watermark_page_classifications"][0]["classification"] == "watermark_only"
