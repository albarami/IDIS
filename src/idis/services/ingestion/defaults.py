"""Default ingestion service wiring for production API startup."""

from __future__ import annotations

import os
from collections.abc import Mapping

from idis.audit.sink import AuditSink
from idis.parsers.media import (
    FASTER_WHISPER_ADAPTER_NAME,
    FasterWhisperMediaAdapter,
    FasterWhisperMediaConfig,
    MediaConfig,
)
from idis.parsers.ocr import OcrConfig, TesseractOcrAdapter
from idis.services.ingestion.service import IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

IDIS_OBJECT_STORE_BACKEND_ENV = "IDIS_OBJECT_STORE_BACKEND"
FILESYSTEM_OBJECT_STORE_BACKEND = "filesystem"
IDIS_OCR_ENABLED_ENV = "IDIS_OCR_ENABLED"
IDIS_OCR_ADAPTER_ENV = "IDIS_OCR_ADAPTER"
IDIS_OCR_MAX_PAGES_ENV = "IDIS_OCR_MAX_PAGES"
IDIS_OCR_TIMEOUT_SECONDS_ENV = "IDIS_OCR_TIMEOUT_SECONDS"
IDIS_OCR_DPI_ENV = "IDIS_OCR_DPI"
IDIS_MEDIA_ADAPTER_ENV = "IDIS_MEDIA_ADAPTER"
IDIS_MEDIA_STT_MODEL_PATH_ENV = "IDIS_MEDIA_STT_MODEL_PATH"
IDIS_MEDIA_STT_MODEL_NAME_ENV = "IDIS_MEDIA_STT_MODEL_NAME"
IDIS_MEDIA_STT_ALLOW_DOWNLOAD_ENV = "IDIS_MEDIA_STT_ALLOW_DOWNLOAD"
IDIS_MEDIA_TIMEOUT_SECONDS_ENV = "IDIS_MEDIA_TIMEOUT_SECONDS"
IDIS_MEDIA_LANGUAGE_ENV = "IDIS_MEDIA_LANGUAGE"
IDIS_MEDIA_COMPUTE_TYPE_ENV = "IDIS_MEDIA_COMPUTE_TYPE"
IDIS_MEDIA_MAX_DURATION_SECONDS_ENV = "IDIS_MEDIA_MAX_DURATION_SECONDS"


def build_default_compliance_store() -> ComplianceEnforcedStore:
    """Build the configured compliance-enforced object store.

    The current production-safe backend is the existing filesystem object store.
    Unsupported backend names fail closed instead of silently bypassing storage
    compliance controls.
    """
    backend = os.environ.get(IDIS_OBJECT_STORE_BACKEND_ENV, FILESYSTEM_OBJECT_STORE_BACKEND)
    if backend != FILESYSTEM_OBJECT_STORE_BACKEND:
        msg = f"Unsupported object store backend for ingestion: {backend}"
        raise ValueError(msg)

    return ComplianceEnforcedStore(inner_store=FilesystemObjectStore())


def build_default_ingestion_service(audit_sink: AuditSink | None = None) -> IngestionService:
    """Build the default production ingestion service for public API upload."""
    return IngestionService(
        compliant_store=build_default_compliance_store(),
        audit_sink=audit_sink,
        ocr_config=build_default_ocr_config(),
        media_config=build_default_media_config(),
    )


def build_default_ocr_config(env: Mapping[str, str] | None = None) -> OcrConfig | None:
    """Build explicit OCR config from runtime environment."""
    values = os.environ if env is None else env
    if not _truthy(values.get(IDIS_OCR_ENABLED_ENV)):
        return None
    adapter_name = values.get(IDIS_OCR_ADAPTER_ENV, "tesseract").strip().lower()
    if adapter_name != "tesseract":
        raise ValueError(f"Unsupported OCR adapter for ingestion: {adapter_name}")
    return OcrConfig(
        enabled=True,
        adapter=TesseractOcrAdapter(dpi=_int_env(values, IDIS_OCR_DPI_ENV, 200)),
        max_pages=_int_env(values, IDIS_OCR_MAX_PAGES_ENV, 10),
        timeout_seconds=_float_env(values, IDIS_OCR_TIMEOUT_SECONDS_ENV, 30.0),
    )


def build_default_media_config(env: Mapping[str, str] | None = None) -> MediaConfig | None:
    """Build explicit media transcription config from runtime environment."""
    values = os.environ if env is None else env
    adapter_name = values.get(IDIS_MEDIA_ADAPTER_ENV, "").strip().lower()
    if not adapter_name:
        return None
    if adapter_name != FASTER_WHISPER_ADAPTER_NAME:
        raise ValueError(f"Unsupported media adapter for ingestion: {adapter_name}")
    faster_whisper_config = FasterWhisperMediaConfig(
        model_name=_optional_env(values, IDIS_MEDIA_STT_MODEL_NAME_ENV),
        model_path=_optional_env(values, IDIS_MEDIA_STT_MODEL_PATH_ENV),
        allow_model_download=_truthy(values.get(IDIS_MEDIA_STT_ALLOW_DOWNLOAD_ENV)),
        language=values.get(IDIS_MEDIA_LANGUAGE_ENV, "en").strip() or "en",
        compute_type=values.get(IDIS_MEDIA_COMPUTE_TYPE_ENV, "int8").strip() or "int8",
        max_duration_seconds=_float_env(
            values,
            IDIS_MEDIA_MAX_DURATION_SECONDS_ENV,
            600.0,
        ),
    )
    return MediaConfig(
        enabled=True,
        adapter=FasterWhisperMediaAdapter(config=faster_whisper_config),
        timeout_seconds=_float_env(values, IDIS_MEDIA_TIMEOUT_SECONDS_ENV, 30.0),
    )


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _optional_env(values: Mapping[str, str], key: str) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _int_env(values: Mapping[str, str], key: str, default: int) -> int:
    raw_value = values.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    return int(raw_value)


def _float_env(values: Mapping[str, str], key: str, default: float) -> float:
    raw_value = values.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    return float(raw_value)
