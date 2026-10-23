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
from idis.parsers.ocr import (
    OcrConfig,
    TesseractOcrAdapter,
    TrustedImageOnlyPageAdmissionSet,
    build_trusted_image_only_page_admission_set,
)
from idis.parsers.poppler_runtime import (
    GovernedPopplerRuntime,
    resolve_pinned_poppler_runtime,
)
from idis.parsers.watermark_page_classifier import (
    WatermarkPageExemplarReference,
    WatermarkPageRuntimeConfig,
    default_watermark_page_policy,
)
from idis.services.ingestion.no_substantive_content_attestation import (
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    load_document_no_substantive_content_attestation,
)
from idis.services.ingestion.password_manifest import load_document_password_manifest
from idis.services.ingestion.service import IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

IDIS_OBJECT_STORE_BACKEND_ENV = "IDIS_OBJECT_STORE_BACKEND"
FILESYSTEM_OBJECT_STORE_BACKEND = "filesystem"
IDIS_INGEST_MAX_BYTES_ENV = "IDIS_INGEST_MAX_BYTES"
APPROVED_STRICT_INGEST_MAX_BYTES = 512 * 1024 * 1024
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


def build_default_ingestion_service(
    audit_sink: AuditSink | None = None,
    *,
    env: Mapping[str, str] | None = None,
    strict: bool = False,
) -> IngestionService:
    """Build the default production ingestion service for public API upload.

    Strict Slice100 startup admits only the reviewed 512 MiB value.  Non-strict
    callers retain the historical default when the variable is absent, while an
    explicitly configured value is still validated and shared by intake and every
    parser through one ``ParseLimits`` instance.
    """
    from idis.parsers.base import ParseLimits
    from idis.services.ingestion.service import DEFAULT_MAX_BYTES

    values = os.environ if env is None else env
    max_bytes = _ingest_max_bytes(values, strict=strict, default=DEFAULT_MAX_BYTES)
    no_substantive_content_attestation = load_document_no_substantive_content_attestation(
        env=values
    )
    watermark_page_config = (
        _watermark_page_runtime_config(no_substantive_content_attestation)
        if _truthy(values.get(IDIS_OCR_ENABLED_ENV))
        else None
    )
    poppler_runtime = (
        resolve_pinned_poppler_runtime()
        if strict and _truthy(values.get(IDIS_OCR_ENABLED_ENV))
        else None
    )
    ocr_config = build_default_ocr_config(
        values,
        image_only_page_admissions=_reviewed_image_only_page_admission_set(
            no_substantive_content_attestation
        ),
        poppler_runtime=poppler_runtime,
    )
    return IngestionService(
        compliant_store=build_default_compliance_store(),
        audit_sink=audit_sink,
        max_bytes=max_bytes,
        parse_limits=ParseLimits(max_bytes=max_bytes),
        ocr_config=ocr_config,
        media_config=build_default_media_config(values, ocr_config=ocr_config),
        password_manifest=load_document_password_manifest(env=values),
        no_substantive_content_attestation=no_substantive_content_attestation,
        watermark_page_config=watermark_page_config,
    )


def build_default_ocr_config(
    env: Mapping[str, str] | None = None,
    *,
    image_only_page_admissions: TrustedImageOnlyPageAdmissionSet | None = None,
    poppler_runtime: GovernedPopplerRuntime | None = None,
) -> OcrConfig | None:
    """Build explicit OCR config from runtime environment."""
    values = os.environ if env is None else env
    if not _truthy(values.get(IDIS_OCR_ENABLED_ENV)):
        return None
    adapter_name = values.get(IDIS_OCR_ADAPTER_ENV, "tesseract").strip().lower()
    if adapter_name != "tesseract":
        raise ValueError(f"Unsupported OCR adapter for ingestion: {adapter_name}")
    return OcrConfig(
        enabled=True,
        adapter=TesseractOcrAdapter(
            dpi=_int_env(values, IDIS_OCR_DPI_ENV, 200),
            language=values.get("IDIS_OCR_LANGUAGE", "eng").strip() or "eng",
            poppler_runtime=poppler_runtime,
        ),
        max_pages=_int_env(values, IDIS_OCR_MAX_PAGES_ENV, 72),
        timeout_seconds=_float_env(values, IDIS_OCR_TIMEOUT_SECONDS_ENV, 30.0),
        image_only_page_admissions=image_only_page_admissions,
    )


def _reviewed_image_only_page_admission_set(
    verified_attestation: object,
) -> TrustedImageOnlyPageAdmissionSet | None:
    """Bind only records already exposed by the verified signed-manifest type."""

    if not isinstance(
        verified_attestation,
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
    ):
        return None
    records = verified_attestation.reviewed_image_only_pages
    reviewed_set_sha256 = verified_attestation.reviewed_image_only_set_sha256
    if not records and reviewed_set_sha256 is None:
        return None
    if not isinstance(reviewed_set_sha256, str):
        raise ValueError("Reviewed image-only page admission digest is malformed")
    return build_trusted_image_only_page_admission_set(
        records=records,
        expected_set_sha256=reviewed_set_sha256,
    )


def _watermark_page_runtime_config(
    verified_attestation: object,
) -> WatermarkPageRuntimeConfig | None:
    """Derive parser material only from the cryptographically verified v2 type."""

    if not isinstance(
        verified_attestation,
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
    ):
        return None
    policy = default_watermark_page_policy()
    payload = verified_attestation.batch.payload
    if (
        payload.get("classifier_policy_id") != policy.policy_id
        or payload.get("classifier_policy_sha256") != policy.policy_sha256
    ):
        raise ValueError("Verified watermark manifest uses an unsupported classifier policy")
    exemplar = verified_attestation.exemplar
    return WatermarkPageRuntimeConfig(
        policy=policy,
        exemplar_reference=WatermarkPageExemplarReference(
            exemplar_id=f"{exemplar.safe_alias}:page-1",
            content_sha256=exemplar.content_sha256,
            page_number=1,
            attestation_envelope_sha256=verified_attestation.exemplar_envelope_sha256,
            canonical_bitmap_sha256=(verified_attestation.exemplar_canonical_bitmap_sha256),
            classifier_policy_sha256=policy.policy_sha256,
        ),
        exemplar_canonical_bitmap=verified_attestation.exemplar_canonical_bitmap,
    )


def build_default_media_config(
    env: Mapping[str, str] | None = None,
    *,
    ocr_config: OcrConfig | None = None,
) -> MediaConfig | None:
    """Build explicit media transcription config from runtime environment."""
    values = os.environ if env is None else env
    adapter_name = values.get(IDIS_MEDIA_ADAPTER_ENV, "").strip().lower()
    if not adapter_name:
        return None
    if adapter_name != FASTER_WHISPER_ADAPTER_NAME:
        raise ValueError(f"Unsupported media adapter for ingestion: {adapter_name}")
    admitted_ocr_config = ocr_config or build_default_ocr_config(values)
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
        frame_ocr_config=admitted_ocr_config,
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


def _ingest_max_bytes(
    values: Mapping[str, str],
    *,
    strict: bool,
    default: int,
) -> int:
    raw_value = values.get(IDIS_INGEST_MAX_BYTES_ENV)
    if raw_value is None or not raw_value.strip():
        if strict:
            raise ValueError("IDIS_INGEST_MAX_BYTES is required in strict mode")
        return default
    try:
        max_bytes = int(raw_value)
    except ValueError:
        raise ValueError("IDIS_INGEST_MAX_BYTES must be an integer") from None
    if max_bytes <= 0:
        raise ValueError("IDIS_INGEST_MAX_BYTES must be positive")
    if strict and max_bytes != APPROVED_STRICT_INGEST_MAX_BYTES:
        raise ValueError("IDIS_INGEST_MAX_BYTES is not the reviewed strict value")
    return max_bytes
