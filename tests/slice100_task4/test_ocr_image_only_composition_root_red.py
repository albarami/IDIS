"""RED-only production-composition coverage for OCR rescue and image-only pages.

These tests deliberately enter through ``build_default_ingestion_service`` and
``IngestionService.ingest_file``.  Parser-only tests cannot prove that the
verified review set, the real OCR adapter, staged-file parsing, and durable
materialization are connected at the production composition root.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter  # type: ignore[import-untyped]
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from idis.audit.sink import InMemoryAuditSink
from idis.models.document import ParseStatus
from idis.parsers import ocr
from idis.services.ingestion import defaults
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
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.slice100_task4.test_watermark_composition_root_red import (
    SIGNER_ID as WATERMARK_SIGNER_ID,
)
from tests.slice100_task4.test_watermark_composition_root_red import (
    _fixture_pdf as _watermark_exemplar_pdf,
)
from tests.slice100_task4.test_watermark_composition_root_red import (
    _sha256_json,
    _sign,
    _signed_manifest,
)

TENANT_ID = UUID("91000000-0000-4000-8000-000000000001")
DEAL_ID = UUID("92000000-0000-4000-8000-000000000001")
STRICT_MAX_BYTES = 512 * 1024 * 1024
FAINT_RESCUE_FIXTURE_SHA256 = "6796a429ad659e1eb3841059cbdfe32ad7e1afc872b35cbd4d9133f207bc87fc"


def _strict_ocr_env() -> dict[str, str]:
    return {
        "IDIS_INGEST_MAX_BYTES": str(STRICT_MAX_BYTES),
        "IDIS_OCR_ENABLED": "true",
        "IDIS_OCR_ADAPTER": "tesseract",
        "IDIS_OCR_DPI": "200",
        "IDIS_OCR_MAX_PAGES": "72",
        "IDIS_OCR_TIMEOUT_SECONDS": "60",
    }


def _faint_content_pdf(path: Path) -> None:
    image_buffer = io.BytesIO()
    with Image.new("L", (1400, 240), color=248) as faint_scan:
        draw = ImageDraw.Draw(faint_scan)
        # The pinned border is ordinary page structure, not a test stub: default
        # PSM 3 returns zero tokens while the fixed PSM 6/11 rescue matrix reaches
        # one real-Tesseract consensus for the faint content.
        draw.rectangle((0, 0, 1399, 239), outline=0, width=5)
        draw.text(
            (25, 94),
            "fictional faint evidence alpha beta",
            font=ImageFont.load_default(size=52),
            fill=190,
        )
        fixture_digest = hashlib.sha256()
        fixture_digest.update(faint_scan.mode.encode("ascii"))
        fixture_digest.update(b"\0")
        fixture_digest.update(faint_scan.width.to_bytes(4, "big"))
        fixture_digest.update(faint_scan.height.to_bytes(4, "big"))
        fixture_digest.update(faint_scan.tobytes())
        assert fixture_digest.hexdigest() == FAINT_RESCUE_FIXTURE_SHA256
        faint_scan.save(image_buffer, format="PNG")
    image_buffer.seek(0)

    # At 200 DPI this page renders back to the fixture's exact 1400x240 extent.
    width_points = 1400 * 72 / 200
    height_points = 240 * 72 / 200
    document = canvas.Canvas(str(path), pagesize=(width_points, height_points))
    document.drawImage(
        ImageReader(image_buffer),
        0,
        0,
        width=width_points,
        height=height_points,
    )
    document.save()


def _reviewed_image_pdf(path: Path, *page_kinds: str) -> None:
    image_buffer = io.BytesIO()
    Image.new("RGB", (400, 240), color=(192, 192, 192)).save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()

    document = canvas.Canvas(str(path), pagesize=letter)
    for page_number, page_kind in enumerate(page_kinds, start=1):
        if page_kind == "native":
            document.drawString(
                72,
                720,
                f"FICTIONAL NATIVE EVIDENCE PAGE {page_number}",
            )
        elif page_kind == "reviewed_image":
            document.drawImage(
                ImageReader(io.BytesIO(image_bytes)),
                106,
                276,
                width=400,
                height=240,
            )
        else:  # pragma: no cover - fixture authoring guard
            raise AssertionError(page_kind)
        document.showPage()
    document.save()


def _review_records(
    *,
    source_path: Path,
    safe_alias: str,
    reviewed_pages: tuple[int, ...],
) -> list[dict[str, object]]:
    """Create signed-review inputs from a completed real OCR terminal proof."""

    try:
        ocr.TesseractOcrAdapter(dpi=200, language="eng").extract_pdf_file_with_partial_results(
            source_path,
            max_pages=72,
            timeout_seconds=60,
        )
    except ocr.OcrRescueNoConsensusError as error:
        proof = error.file_proof
        assert proof is not None
        receipts = {receipt.page_number: receipt for receipt in proof.page_receipts}
        page_hashes = dict(error.page_content_sha256s)
    else:  # pragma: no cover - the fixture must exercise terminal review admission
        pytest.fail("reviewed image fixture unexpectedly produced OCR text", pytrace=False)

    content_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    records: list[dict[str, object]] = []
    for page_number in reviewed_pages:
        receipt = receipts[page_number]
        assert receipt.outcome == "no_consensus"
        records.append(
            {
                "safe_alias": safe_alias,
                "content_sha256": content_sha256,
                "page_number": page_number,
                "page_content_sha256": page_hashes[page_number],
                "ocr_terminal_proof_sha256": ocr.ocr_page_execution_receipt_sha256(receipt),
                "watermark_classifier_receipt_sha256": hashlib.sha256(
                    f"composition-root-below-threshold:{content_sha256}:{page_number}".encode(
                        "ascii"
                    )
                ).hexdigest(),
                "reviewer_disposition": "SUBSTANTIVE_IMAGE",
            }
        )
    return records


def _verified_review_manifest(
    records: list[dict[str, object]],
) -> VerifiedDocumentNoSubstantiveContentAttestationManifest:
    # Start from the coherent default-policy fixture: unlike the older batch
    # contract fixture, its classifier hash and signed packed exemplar are valid
    # inputs to the production runtime composition root.
    manifest, public_pem = _signed_manifest(_watermark_exemplar_pdf())
    prescan = cast(dict[str, object], manifest["watermark_prescan_receipt"]).copy()
    batch_envelope = cast(
        dict[str, object],
        manifest["watermark_batch_attestation_envelope"],
    )
    batch_payload = cast(dict[str, object], batch_envelope["payload"]).copy()
    reviewed_set_sha256 = _sha256_json(records)
    prescan.update(
        {
            "prior_blocked_prescan_receipt_sha256": hashlib.sha256(
                b"fictional-prior-blocked-prescan-receipt"
            ).hexdigest(),
            "prior_blocked_prescan_file_sha256": hashlib.sha256(
                b"fictional-prior-blocked-prescan-file"
            ).hexdigest(),
            "prior_unresolved_set_sha256": hashlib.sha256(
                b"fictional-prior-unresolved-set"
            ).hexdigest(),
            "reviewed_image_only_page_count": len(records),
            "reviewed_image_only_pages": records,
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


def _default_service(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verified_manifest: VerifiedDocumentNoSubstantiveContentAttestationManifest | None = None,
) -> IngestionService:
    store = ComplianceEnforcedStore(
        inner_store=FilesystemObjectStore(base_dir=tmp_path / "composition-root-objects")
    )
    monkeypatch.setattr(defaults, "build_default_compliance_store", lambda: store)
    if verified_manifest is not None:
        monkeypatch.setattr(
            defaults,
            "load_document_no_substantive_content_attestation",
            lambda *, env: verified_manifest,
        )
    service = defaults.build_default_ingestion_service(
        audit_sink=InMemoryAuditSink(),
        env=_strict_ocr_env(),
        strict=True,
    )
    assert isinstance(service._ocr_config, ocr.OcrConfig)  # noqa: SLF001
    assert type(service._ocr_config.adapter) is ocr.TesseractOcrAdapter  # noqa: SLF001
    return service


def _ingest_file(
    service: IngestionService,
    *,
    source_path: Path,
    safe_alias: str,
) -> IngestionResult:
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return service.ingest_file(
        ctx=IngestionContext(
            tenant_id=TENANT_ID,
            actor_id="fictional-composition-root-worker",
            request_id=f"composition-root:{safe_alias}",
        ),
        deal_id=DEAL_ID,
        filename=safe_alias,
        media_type="application/pdf",
        source_path=source_path,
        size_bytes=source_path.stat().st_size,
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=digest,
            expected_sha256=digest,
        ),
        metadata={"upload_filename": safe_alias},
    )


def _assert_image_only_receipt_v2(
    metadata: dict[str, object],
    *,
    page_number: int,
) -> None:
    receipts = cast(list[dict[str, object]], metadata["image_only_page_receipts"])
    receipt = next(row for row in receipts if row["page_number"] == page_number)
    assert receipt["schema_version"] == "image-only-page-receipt-v2"
    execution = cast(dict[str, object], receipt["ocr_page_execution_receipt"])
    assert execution["page_number"] == page_number
    assert execution["outcome"] == "no_consensus"
    assert execution["rescue_attempted_candidate_count"] == len(ocr.OCR_RESCUE_MATRIX)
    assert execution["rescue_completed_candidate_count"] == len(ocr.OCR_RESCUE_MATRIX)
    assert receipt["ocr_terminal_proof_sha256"] == ocr.ocr_page_execution_receipt_sha256(
        ocr.validate_ocr_page_execution_receipt_payload(execution)
    )
    render = cast(dict[str, object], receipt["ocr_render_admission"])
    assert render["schema_version"] == ocr.OCR_RENDER_ADMISSION_SCHEMA_VERSION
    assert render["actual_render_pixel_count"] == (
        cast(int, render["actual_render_width"]) * cast(int, render["actual_render_height"])
    )
    metadata_admissions = cast(list[dict[str, object]], metadata["ocr_render_admissions"])
    assert {row["page"] for row in metadata_admissions} >= {page_number}
    metadata_render = next(row for row in metadata_admissions if row["page"] == page_number)
    assert {key: value for key, value in metadata_render.items() if key != "page"} == render


def test_default_ingestion_composition_executes_real_faint_content_rescue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "company-002-document-00120.pdf"
    _faint_content_pdf(source_path)
    service = _default_service(tmp_path=tmp_path, monkeypatch=monkeypatch)

    result = _ingest_file(
        service,
        source_path=source_path,
        safe_alias=source_path.name,
    )

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    assert result.document_id is not None
    spans = service.get_spans(TENANT_ID, result.document_id)
    rescue_spans = [span for span in spans if span.locator.get("source") == "ocr_rescue"]
    assert rescue_spans
    assert "fictional faint evidence alpha beta" in " ".join(
        span.text_excerpt.casefold() for span in rescue_spans
    )
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    assert document.metadata["ocr_rescue_page_count"] == 1
    assert document.metadata["ocr_rescue_span_count"] == len(rescue_spans)


def test_default_ingestion_composition_retains_mixed_document_spans_beside_reviewed_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_alias = "company-002-document-00121.pdf"
    source_path = tmp_path / safe_alias
    _reviewed_image_pdf(source_path, "native", "reviewed_image")
    records = _review_records(
        source_path=source_path,
        safe_alias=safe_alias,
        reviewed_pages=(2,),
    )
    service = _default_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        verified_manifest=_verified_review_manifest(records),
    )

    result = _ingest_file(service, source_path=source_path, safe_alias=safe_alias)

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    assert result.document_id is not None
    spans = service.get_spans(TENANT_ID, result.document_id)
    assert spans
    assert {span.locator.get("page") for span in spans} == {1}
    assert "fictional native evidence" in " ".join(span.text_excerpt.casefold() for span in spans)
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    _assert_image_only_receipt_v2(document.metadata, page_number=2)
    evaluation = cast(dict[str, object], document.metadata["image_only_page_evaluation"])
    assert evaluation["parse_status"] == "PARSED"
    assert evaluation["may_proceed"] is True
    assert evaluation["image_only_page_count"] == 1
    assert evaluation["substantive_span_count"] == len(spans)


def test_default_ingestion_composition_fails_all_reviewed_image_only_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_alias = "company-002-document-00122.pdf"
    source_path = tmp_path / safe_alias
    _reviewed_image_pdf(source_path, "reviewed_image", "reviewed_image")
    records = _review_records(
        source_path=source_path,
        safe_alias=safe_alias,
        reviewed_pages=(1, 2),
    )
    service = _default_service(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        verified_manifest=_verified_review_manifest(records),
    )

    result = _ingest_file(service, source_path=source_path, safe_alias=safe_alias)

    assert result.success is False
    assert result.parse_status == ParseStatus.FAILED
    assert result.span_count == 0
    assert result.errors
    assert result.errors[0].details == {
        "parse_error_code": "ocr_no_text_extracted",
        "terminal_code": "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED",
    }
    assert result.document_id is not None
    assert service.get_spans(TENANT_ID, result.document_id) == []
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    _assert_image_only_receipt_v2(document.metadata, page_number=1)
    _assert_image_only_receipt_v2(document.metadata, page_number=2)
    evaluation = cast(dict[str, object], document.metadata["image_only_page_evaluation"])
    assert evaluation["parse_status"] == "FAILED"
    assert evaluation["may_proceed"] is False
    assert evaluation["terminal_code"] == "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED"
    assert evaluation["image_only_page_count"] == evaluation["page_count"] == 2
