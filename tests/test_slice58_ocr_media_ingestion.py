"""Slice 58 OCR and media ingestion wiring tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from idis.parsers.base import ParseErrorCode
from idis.parsers.media import MediaConfig, MediaSegmentText
from idis.parsers.ocr import OcrConfig, OcrPageText
from idis.parsers.registry import parse_bytes
from idis.services.ingestion import IngestionContext, IngestionService
from idis.services.ingestion.defaults import build_default_ingestion_service
from idis.services.runs.strict_full_live import (
    StrictComponentStatus,
    build_strict_full_live_readiness_report,
)
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
DEAL_ID = UUID("33333333-3333-4333-8333-333333333333")


class RecordingOcrAdapter:
    """Test OCR adapter returning deterministic text without reading real images."""

    def __init__(self, pages: list[OcrPageText] | None = None) -> None:
        self.pages = pages or [OcrPageText(page_number=1, text="OCR revenue was 10M")]
        self.calls: list[dict[str, object]] = []

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        self.calls.append(
            {
                "kind": "pdf",
                "byte_count": len(data),
                "max_pages": max_pages,
                "timeout": timeout_seconds,
            }
        )
        return self.pages

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        self.calls.append({"kind": "image", "byte_count": len(data), "timeout": timeout_seconds})
        return self.pages


class RecordingMediaAdapter:
    """Test media adapter returning deterministic timecode text."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        self.calls.append({"byte_count": len(data), "timeout": timeout_seconds})
        return [MediaSegmentText(start_ms=1000, end_ms=2500, text="Media ARR transcript")]


@pytest.fixture
def ingestion_service(tmp_path: Path) -> IngestionService:
    return IngestionService(
        compliant_store=ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=tmp_path / "store")
        )
    )


def test_default_ingestion_service_wires_ocr_config_from_runtime_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("IDIS_OCR_ENABLED", "1")
    monkeypatch.setenv("IDIS_OCR_ADAPTER", "tesseract")
    monkeypatch.setenv("IDIS_OCR_MAX_PAGES", "3")
    monkeypatch.setenv("IDIS_OCR_TIMEOUT_SECONDS", "7.5")

    service = build_default_ingestion_service()

    assert service._ocr_config is not None
    assert service._ocr_config.enabled is True
    assert service._ocr_config.adapter is not None
    assert service._ocr_config.max_pages == 3
    assert service._ocr_config.timeout_seconds == 7.5


def test_default_ingestion_service_wires_media_config_from_runtime_env(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "model.bin").write_text("synthetic", encoding="utf-8")
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("IDIS_MEDIA_ADAPTER", "faster-whisper")
    monkeypatch.setenv("IDIS_MEDIA_STT_MODEL_PATH", str(model_path))
    monkeypatch.setenv("IDIS_MEDIA_TIMEOUT_SECONDS", "8")

    service = build_default_ingestion_service()

    assert service._media_config is not None
    assert service._media_config.enabled is True
    assert service._media_config.adapter is not None
    assert service._media_config.timeout_seconds == 8


def test_configured_parser_dispatch_defers_image_and_media_without_adapters() -> None:
    image_result = parse_bytes(b"synthetic image bytes", filename="scan.png")
    media_result = parse_bytes(b"\x00\x00\x00\x18ftypmp42", filename="demo.mp4")

    assert image_result.success is False
    assert image_result.doc_type == "IMAGE"
    assert [error.code for error in image_result.errors] == [ParseErrorCode.OCR_UNAVAILABLE]
    assert media_result.success is False
    assert media_result.doc_type == "MEDIA"
    assert [error.code for error in media_result.errors] == [
        ParseErrorCode.MEDIA_TRANSCRIPTION_UNAVAILABLE
    ]


def test_strict_readiness_blocks_ocr_required_docs_when_ocr_runtime_missing() -> None:
    report = build_strict_full_live_readiness_report(
        preflight_corpus=[
            {
                "document_id": "doc-ocr",
                "metadata": {"parser_requires_ocr": True, "parser_reason_codes": ["ocr_required"]},
            }
        ],
        env={},
        binary_resolver=lambda _binary: None,
        load_byol_env_credentials=False,
    )

    ocr = report.component("ocr")
    assert ocr.status == StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert ocr.may_proceed is False
    assert "OCR-required documents" in ocr.blocker_message


def test_strict_readiness_blocks_image_extension_evidence_when_ocr_runtime_missing() -> None:
    report = build_strict_full_live_readiness_report(
        data_room_file_extensions=[".png"],
        env={},
        binary_resolver=lambda _binary: None,
        load_byol_env_credentials=False,
    )

    ocr = report.component("ocr")
    assert ocr.status == StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert ocr.may_proceed is False


def test_strict_readiness_blocks_media_docs_when_runtime_or_model_missing() -> None:
    report = build_strict_full_live_readiness_report(
        preflight_corpus=[
            {
                "document_id": "doc-media",
                "doc_type": "MEDIA",
                "document_name": "redacted.mp4",
                "metadata": {"parser_reason_codes": ["media_transcription_unavailable"]},
            }
        ],
        env={"IDIS_MEDIA_ADAPTER": "faster-whisper"},
        binary_resolver=lambda _binary: None,
        load_byol_env_credentials=False,
    )

    media = report.component("mp4_stt")
    assert media.status == StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert media.may_proceed is False
    assert "MP4 files are present" in media.blocker_message


def test_strict_readiness_blocks_video_preflight_docs_when_media_runtime_missing() -> None:
    report = build_strict_full_live_readiness_report(
        preflight_corpus=[{"document_id": "doc-video", "doc_type": "VIDEO", "metadata": {}}],
        env={},
        binary_resolver=lambda _binary: None,
        load_byol_env_credentials=False,
    )

    media = report.component("mp4_stt")
    assert media.status == StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert media.may_proceed is False


def test_strict_readiness_does_not_claim_ocr_or_media_live_without_evidence() -> None:
    report = build_strict_full_live_readiness_report(
        env={},
        binary_resolver=lambda _binary: None,
        load_byol_env_credentials=False,
    )

    assert report.component("ocr").may_proceed is True
    assert report.component("ocr").status == StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED
    assert report.component("mp4_stt").may_proceed is True
    assert report.component("mp4_stt").status == StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED


def test_strict_readiness_ocr_and_media_can_clear_with_configured_healthy_runtimes(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "model.bin").write_text("synthetic", encoding="utf-8")
    (model_path / "config.json").write_text("{}", encoding="utf-8")

    report = build_strict_full_live_readiness_report(
        preflight_corpus=[
            {
                "document_id": "doc-ocr",
                "metadata": {"parser_requires_ocr": True, "parser_reason_codes": ["ocr_required"]},
            },
            {"document_id": "doc-media", "doc_type": "MEDIA", "document_name": "demo.mp4"},
        ],
        env={
            "IDIS_OCR_ENABLED": "1",
            "IDIS_OCR_ADAPTER": "tesseract",
            "IDIS_MEDIA_ADAPTER": "faster-whisper",
            "IDIS_MEDIA_STT_MODEL_PATH": str(model_path),
        },
        binary_resolver=lambda binary: (
            f"synthetic-{binary}" if binary in {"tesseract", "ffmpeg", "ffprobe"} else None
        ),
        load_byol_env_credentials=False,
    )

    assert report.component("ocr").may_proceed is True
    assert report.component("ocr").status == StrictComponentStatus.LIVE_WIRED_AND_USED
    assert report.component("mp4_stt").may_proceed is True
    assert report.component("mp4_stt").status == StrictComponentStatus.LIVE_WIRED_AND_USED
    assert report.may_proceed is False


def test_durable_documents_schema_allows_image_parser_doc_type() -> None:
    migration = Path(
        "src/idis/persistence/migrations/versions/0016_allow_image_document_type.py"
    ).read_text(encoding="utf-8")

    assert "'IMAGE'" in migration
    assert "valid_document_doc_type" in migration


def test_ocr_and_media_spans_persist_with_safe_parser_metadata(
    ingestion_service: IngestionService,
) -> None:
    ocr_adapter = RecordingOcrAdapter()
    media_adapter = RecordingMediaAdapter()
    service = IngestionService(
        compliant_store=ingestion_service._compliant_store,
        ocr_config=OcrConfig(enabled=True, adapter=ocr_adapter, max_pages=1, timeout_seconds=3),
        media_config=MediaConfig(enabled=True, adapter=media_adapter, timeout_seconds=4),
    )
    ctx = IngestionContext(tenant_id=TENANT_ID, actor_id="tester", request_id="req-slice58")

    image_result = service.ingest_bytes(
        ctx=ctx,
        deal_id=DEAL_ID,
        filename="scan.png",
        media_type="image/png",
        data=b"synthetic image bytes",
    )
    media_result = service.ingest_bytes(
        ctx=ctx,
        deal_id=DEAL_ID,
        filename="demo.mp4",
        media_type="video/mp4",
        data=b"\x00\x00\x00\x18ftypmp42",
    )

    assert image_result.success is True
    assert media_result.success is True
    image_spans = service.get_spans(TENANT_ID, image_result.document_id)
    media_spans = service.get_spans(TENANT_ID, media_result.document_id)
    assert [span.span_type.value for span in image_spans] == ["PAGE_TEXT"]
    assert image_spans[0].locator["source"] == "ocr_image"
    assert [span.span_type.value for span in media_spans] == ["TIMECODE"]
    assert media_spans[0].locator["source"] == "media_transcript"

    image_document = service.get_document(TENANT_ID, image_result.document_id)
    media_document = service.get_document(TENANT_ID, media_result.document_id)
    assert image_document is not None
    assert media_document is not None
    assert image_document.metadata["parser_mode"] == "ocr"
    assert image_document.metadata["parser_source_type"] == "image_ocr"
    assert image_document.metadata["parser_runtime_status"] == "completed"
    assert image_document.metadata["source_document_id"] == str(image_result.document_id)
    assert media_document.metadata["parser_mode"] == "media_stt"
    assert media_document.metadata["parser_runtime"] == "configured_media_adapter"
    assert media_document.metadata["parser_runtime_status"] == "completed"
    assert media_document.metadata["source_document_id"] == str(media_result.document_id)
    encoded = str(image_document.metadata) + str(media_document.metadata)
    assert "synthetic image bytes" not in encoded
    assert "Media ARR transcript" not in encoded
    assert "model.bin" not in encoded
