"""RED: watermark-only and reviewed image-only pages must compose disjointly.

The successful case enters through the default production ingestion builder and
staged-file service path.  It contains one signed-exemplar watermark match, one
separately signed ``image_only_page``, and one native evidence page.  No parser
metadata is injected.  Missing or overlapping authorizations must remain closed.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pdf2image import convert_from_path  # type: ignore[import-untyped]
from PIL import Image
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from idis.models.document import ParseStatus
from idis.services.ingestion.no_substantive_content_attestation import (
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    verify_document_no_substantive_content_attestation_manifest,
)
from idis.services.ingestion.service import (
    IngestionContext,
    IngestionResult,
    IngestionService,
    RouteValidatedSha256,
)
from tests.slice100_task4.test_ocr_image_only_composition_root_red import (
    DEAL_ID,
    _default_service,
    _review_records,
)
from tests.slice100_task4.test_watermark_composition_root_red import (
    SIGNER_ID as WATERMARK_SIGNER_ID,
)
from tests.slice100_task4.test_watermark_composition_root_red import (
    SOURCE_TREE_FINGERPRINT,
    TENANT_ID,
    _fixture_page,
    _fixture_pdf,
    _packed_canonical_bitmap,
    _sha256_json,
    _sign,
    _signed_manifest,
)

SAFE_ALIAS = "company-002-document-00123.pdf"


def _mixed_disposition_pdf(path: Path) -> None:
    with _fixture_page() as exemplar_page:
        exemplar_png = io.BytesIO()
        exemplar_page.save(exemplar_png, format="PNG")
        exemplar_bytes = exemplar_png.getvalue()
    image_only_png = io.BytesIO()
    Image.new("L", (1000, 1000), color=192).save(image_only_png, format="PNG")
    image_only_bytes = image_only_png.getvalue()

    document = canvas.Canvas(
        str(path),
        pagesize=(360, 360),
        pageCompression=0,
        invariant=1,
    )
    document.drawImage(
        ImageReader(io.BytesIO(exemplar_bytes)),
        0,
        0,
        width=360,
        height=360,
        preserveAspectRatio=False,
        mask=None,
    )
    document.showPage()
    document.drawImage(
        ImageReader(io.BytesIO(image_only_bytes)),
        0,
        0,
        width=360,
        height=360,
        preserveAspectRatio=False,
        mask=None,
    )
    document.showPage()
    document.drawString(36, 180, "FICTIONAL INDEPENDENT NATIVE EVIDENCE")
    document.showPage()
    document.save()


def _page_one_packed_bitmap(source_path: Path) -> bytes:
    pages = convert_from_path(
        str(source_path),
        dpi=200,
        first_page=1,
        last_page=1,
        fmt="png",
        thread_count=1,
    )
    assert len(pages) == 1
    with pages[0] as page:
        return _packed_canonical_bitmap(page)


def _verified_combined_manifest(
    *,
    source_path: Path,
    reviewed_records: list[dict[str, object]],
) -> VerifiedDocumentNoSubstantiveContentAttestationManifest:
    manifest, public_pem = _signed_manifest(_fixture_pdf())
    prescan = cast(dict[str, object], manifest["watermark_prescan_receipt"]).copy()
    batch_envelope = cast(
        dict[str, object],
        manifest["watermark_batch_attestation_envelope"],
    )
    batch_payload = cast(dict[str, object], batch_envelope["payload"]).copy()
    base_candidates = cast(list[dict[str, object]], batch_payload["candidates"])

    content_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    page_one_bitmap = _page_one_packed_bitmap(source_path)
    exemplar_bitmap_sha256 = cast(str, batch_payload["exemplar_canonical_bitmap_sha256"])
    assert hashlib.sha256(page_one_bitmap).hexdigest() == exemplar_bitmap_sha256
    candidates = sorted(
        [
            *base_candidates,
            {
                "safe_alias": SAFE_ALIAS,
                "content_sha256": content_sha256,
                "page_number": 1,
                "canonical_render_sha256": exemplar_bitmap_sha256,
                "similarity_score": "1.000000",
            },
        ],
        key=lambda row: (cast(str, row["safe_alias"]), cast(int, row["page_number"])),
    )
    candidate_set_sha256 = _sha256_json(candidates)
    prescan.update(
        {
            "candidate_count": len(candidates),
            "candidate_set_sha256": candidate_set_sha256,
            "sample_set_sha256": candidate_set_sha256,
        }
    )
    batch_payload.update(
        {
            "candidate_set_sha256": candidate_set_sha256,
            "candidates": candidates,
            "sample_set_sha256": candidate_set_sha256,
        }
    )
    if reviewed_records:
        reviewed_set_sha256 = _sha256_json(reviewed_records)
        prescan.update(
            {
                "prior_blocked_prescan_receipt_sha256": hashlib.sha256(
                    b"fictional-disjoint-prior-prescan-receipt"
                ).hexdigest(),
                "prior_blocked_prescan_file_sha256": hashlib.sha256(
                    b"fictional-disjoint-prior-prescan-file"
                ).hexdigest(),
                "prior_unresolved_set_sha256": hashlib.sha256(
                    b"fictional-disjoint-prior-unresolved-set"
                ).hexdigest(),
                "reviewed_image_only_page_count": len(reviewed_records),
                "reviewed_image_only_pages": reviewed_records,
                "reviewed_image_only_set_sha256": reviewed_set_sha256,
                "reviewer_approval": batch_payload["reviewer_approval"],
            }
        )
    batch_payload["prescan_receipt_sha256"] = _sha256_json(prescan)
    private_key = Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"fictional-watermark-composition-ed25519-key").digest()
    )
    exact_manifest = {
        **manifest,
        "watermark_prescan_receipt": prescan,
        "watermark_batch_attestation_envelope": _sign(
            private_key,
            schema_version=("document-no-substantive-content-watermark-batch-attestation-v1"),
            payload=batch_payload,
        ),
    }
    verified = verify_document_no_substantive_content_attestation_manifest(
        manifest=exact_manifest,
        trusted_signer_id=WATERMARK_SIGNER_ID,
        trusted_public_key_pem=public_pem,
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )
    assert isinstance(verified, VerifiedDocumentNoSubstantiveContentAttestationManifest)
    return verified


def _ingest(service: IngestionService, source_path: Path) -> IngestionResult:
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return service.ingest_file(
        ctx=IngestionContext(
            tenant_id=TENANT_ID,
            actor_id="fictional-disjoint-disposition-worker",
            request_id="fictional-disjoint-disposition-request",
        ),
        deal_id=DEAL_ID,
        filename=SAFE_ALIAS,
        media_type="application/pdf",
        source_path=source_path,
        size_bytes=source_path.stat().st_size,
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=digest,
            expected_sha256=digest,
        ),
        metadata={
            "upload_filename": SAFE_ALIAS,
            "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        },
    )


def _assert_closed(
    result: IngestionResult,
    service: IngestionService,
    *,
    expected_parse_error_code: str = "ocr_failed",
) -> None:
    assert result.success is False
    assert result.parse_status == ParseStatus.FAILED
    assert result.span_count == 0
    assert result.errors
    assert result.errors[0].details["parse_error_code"] == expected_parse_error_code
    assert result.document_id is not None
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    assert "document_no_substantive_content_receipt" not in document.metadata
    assert "image_only_page_receipts" not in document.metadata


def test_disjoint_signed_watermark_and_image_only_pages_compose_with_native_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / SAFE_ALIAS
    _mixed_disposition_pdf(source_path)
    image_only_records = _review_records(
        source_path=source_path,
        safe_alias=SAFE_ALIAS,
        reviewed_pages=(2,),
    )
    service = _default_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        verified_manifest=_verified_combined_manifest(
            source_path=source_path,
            reviewed_records=image_only_records,
        ),
    )

    result = _ingest(service, source_path)

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    assert result.document_id is not None
    spans = service.get_spans(TENANT_ID, result.document_id)
    assert spans and {span.locator.get("page") for span in spans} == {3}
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    watermark_receipt = cast(
        dict[str, object],
        document.metadata["document_no_substantive_content_receipt"],
    )
    watermark_pages = {
        row["page_number"]
        for row in cast(
            list[dict[str, object]], watermark_receipt["watermark_page_classifications"]
        )
    }
    image_only_pages = {
        row["page_number"]
        for row in cast(
            list[dict[str, object]],
            document.metadata["image_only_page_receipts"],
        )
    }
    assert watermark_pages == {1}
    assert image_only_pages == {2}
    assert watermark_pages.isdisjoint(image_only_pages)


def test_missing_image_only_authorization_stays_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / SAFE_ALIAS
    _mixed_disposition_pdf(source_path)
    service = _default_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        verified_manifest=_verified_combined_manifest(
            source_path=source_path,
            reviewed_records=[],
        ),
    )

    # Classifier-first removes the signed watermark page before OCR, so the
    # remaining unauthorized image page now retains its precise terminal code.
    _assert_closed(
        _ingest(service, source_path),
        service,
        expected_parse_error_code="ocr_rescue_no_consensus",
    )


def test_overlapping_watermark_and_image_only_authorizations_stay_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / SAFE_ALIAS
    _mixed_disposition_pdf(source_path)
    overlapping_records = _review_records(
        source_path=source_path,
        safe_alias=SAFE_ALIAS,
        reviewed_pages=(1, 2),
    )
    service = _default_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        verified_manifest=_verified_combined_manifest(
            source_path=source_path,
            reviewed_records=overlapping_records,
        ),
    )

    _assert_closed(_ingest(service, source_path), service)
