"""RED: the default API must turn signed watermark admission into a terminal 201."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from pdf2image import convert_from_bytes
from reportlab.pdfgen import canvas

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.idempotency.store import SqliteIdempotencyStore
from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime
from idis.parsers.watermark_page_classifier import (
    build_watermark_page_canonical_exemplar_bitmap,
    default_watermark_page_policy,
)
from idis.runtime.governance_envelopes import canonicalize_json
from idis.runtime.secure_files import protect_path_for_current_user
from tests._linux_poppler_gate import requires_linux_governed_poppler
from tests.abac_seed import seed_deal_access
from tests.slice100_task4 import test_watermark_batch_attestation_red as batch_contract

TENANT_ID = batch_contract.TENANT_ID
ACTOR_ID = "fictional-watermark-api-actor"
API_KEY = "fictional-watermark-api-key"
DEAL_ID = "41000000-0000-4000-8000-000000000001"
SAFE_ALIAS = "company-002-document-00054.pdf"
SOURCE_TREE_FINGERPRINT = hashlib.sha256(b"fictional-watermark-api-source-tree").hexdigest()
APPROVED_AT = "2020-01-01T00:00:00Z"
EXPIRES_AT = "2099-01-01T00:00:00Z"


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonicalize_json(value)).hexdigest()


def _watermark_pdf() -> bytes:
    """Create one invariant square page with exactly 2% solid foreground area."""

    output = io.BytesIO()
    document = canvas.Canvas(
        output,
        pagesize=(500, 500),
        pageCompression=0,
        invariant=1,
    )
    document.setFillColorRGB(0, 0, 0)
    document.rect(200, 225, 100, 50, stroke=0, fill=1)
    document.showPage()
    document.save()
    return output.getvalue()


def _canonical_bitmap(pdf_bytes: bytes) -> tuple[bytes, str]:
    binding = resolve_pinned_poppler_runtime()
    pages = convert_from_bytes(
        pdf_bytes,
        dpi=200,
        first_page=1,
        last_page=1,
        poppler_path=str(binding.binary_dir),
    )
    assert len(pages) == 1
    policy = default_watermark_page_policy()
    with pages[0] as page:
        packed = build_watermark_page_canonical_exemplar_bitmap(
            page_image=page,
            render_dpi=policy.render_dpi,
            policy=policy,
        )
    # Manifest-v2 signs the pinned 2,048-byte one-bit representation, not raw pixels.
    return packed, hashlib.sha256(packed).hexdigest()


def _signed_manifest(pdf_bytes: bytes) -> tuple[dict[str, object], bytes]:
    private_pem, public_pem = batch_contract._private_and_public_pem()
    content_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    packed_bitmap, canonical_render_sha256 = _canonical_bitmap(pdf_bytes)
    policy = default_watermark_page_policy()
    approval = {
        "decision": "APPROVED",
        "reviewer_id": "fictional-watermark-api-reviewer",
        "approved_at": APPROVED_AT,
        "expires_at": EXPIRES_AT,
    }
    exemplar_payload = {
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "safe_alias": SAFE_ALIAS,
        "content_sha256": content_sha256,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "attestation_text_sha256": hashlib.sha256(
            b"fictional deterministic watermark-only fixture"
        ).hexdigest(),
        "reviewer_approval": approval,
    }
    exemplar = batch_contract._sign(
        schema_version=batch_contract.EXEMPLAR_SCHEMA_VERSION,
        payload=exemplar_payload,
        private_pem=private_pem,
    )
    candidates = [
        {
            "safe_alias": SAFE_ALIAS,
            "content_sha256": content_sha256,
            "page_number": 1,
            "canonical_render_sha256": canonical_render_sha256,
            "similarity_score": "1.000000",
        }
    ]
    candidate_set_sha256 = _sha256_json(candidates)
    prescan = {
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
    batch_payload = {
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "classifier_policy_id": policy.policy_id,
        "classifier_policy_sha256": policy.policy_sha256,
        "exemplar_safe_alias": SAFE_ALIAS,
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
    batch = batch_contract._sign(
        schema_version=batch_contract.BATCH_SCHEMA_VERSION,
        payload=batch_payload,
        private_pem=private_pem,
    )
    return (
        {
            "schema_version": batch_contract.MANIFEST_SCHEMA_VERSION,
            "exemplar_attestation_envelope": exemplar,
            "exemplar_canonical_bitmap_base64": base64.b64encode(packed_bitmap).decode("ascii"),
            "watermark_prescan_receipt": prescan,
            "watermark_batch_attestation_envelope": batch,
        },
        public_pem,
    )


def _seal(path: Path) -> None:
    protect_path_for_current_user(path.parent, directory=True)
    protect_path_for_current_user(path)
    path.chmod(stat.S_IREAD if os.name == "nt" else stat.S_IRUSR)


def _write_governance(tmp_path: Path, pdf_bytes: bytes) -> dict[str, str]:
    manifest, public_pem = _signed_manifest(pdf_bytes)
    root = tmp_path / "offline-governance"
    root.mkdir()
    manifest_path = root / "watermark-manifest-v2.json"
    public_key_path = root / "watermark-public.pem"
    manifest_path.write_bytes(canonicalize_json(manifest))
    public_key_path.write_bytes(public_pem)
    _seal(manifest_path)
    _seal(public_key_path)
    return {
        "IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH": str(manifest_path),
        "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": batch_contract.SIGNER_ID,
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_key_path),
    }


@requires_linux_governed_poppler
def test_default_api_upload_admits_signed_watermark_only_document(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clear_deals_store()
    clear_document_store()
    pdf_bytes = _watermark_pdf()
    assert pdf_bytes == _watermark_pdf()
    for name, value in _write_governance(tmp_path, pdf_bytes).items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    monkeypatch.delenv("IDIS_MEDIA_ADAPTER", raising=False)
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    monkeypatch.setenv("IDIS_OCR_ENABLED", "true")
    monkeypatch.setenv("IDIS_OCR_ADAPTER", "tesseract")
    monkeypatch.setenv("IDIS_OCR_DPI", "200")
    monkeypatch.setenv("IDIS_OCR_MAX_PAGES", "2")
    monkeypatch.setenv("IDIS_OCR_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("IDIS_SERVICE_REGION", "me-south-1")
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY: {
                    "tenant_id": TENANT_ID,
                    "actor_id": ACTOR_ID,
                    "name": "Fictional Watermark Tenant",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )
    seed_deal_access(TENANT_ID, DEAL_ID, ACTOR_ID)
    app = create_app(idempotency_store=SqliteIdempotencyStore(in_memory=True))
    assert app.state.ingestion_service.__class__.__name__ == "IngestionService"
    client = TestClient(app, raise_server_exceptions=False)
    persisted_receipt: object = None
    try:
        response = client.post(
            f"/v1/deals/{DEAL_ID}/documents/upload",
            headers={
                "X-IDIS-API-Key": API_KEY,
                "Content-Type": "application/octet-stream",
                "X-IDIS-Source-Tree-Fingerprint": SOURCE_TREE_FINGERPRINT,
            },
            params={
                "filename": SAFE_ALIAS,
                "doc_type": "DATA_ROOM_FILE",
                "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "source_system": "slice100-offline-watermark-red",
            },
            content=pdf_bytes,
        )
        if response.status_code == 201:
            response_document = app.state.ingestion_service.get_document(
                UUID(TENANT_ID), UUID(response.json()["document_id"])
            )
            assert response_document is not None
            # The public response intentionally omits the internal governance receipt.
            persisted_receipt = response_document.metadata.get(
                "document_no_substantive_content_receipt"
            )
    finally:
        client.close()
        clear_deals_store()
        clear_document_store()

    observed_body = response.json()
    observed_parser_codes = [
        error["code"] for error in observed_body.get("details", {}).get("errors", [])
    ]
    assert response.status_code == 201, {
        "observed_status": response.status_code,
        "observed_code": observed_body.get("code"),
        "observed_parser_codes": observed_parser_codes,
    }
    body = response.json()
    assert body["parse_status"] == "FAILED"
    assert body["terminal_disposition"] == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
    assert isinstance(persisted_receipt, dict)
    receipt = persisted_receipt
    assert receipt["schema_version"] == "document-no-substantive-content-receipt-v2"
    assert receipt["variant"] == "watermark_only"
    assert receipt["whole_document_terminal"] is True
    assert receipt["watermark_page_classifications"][0]["batch_membership"] == "ATTESTED"
