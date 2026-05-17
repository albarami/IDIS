"""Hash-keyed private ledger helpers for the real_example gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from idis.parsers.media import MAX_MEDIA_SEGMENT_TEXT_CHARS, MAX_MEDIA_SEGMENTS
from idis.services.ingestion.service import DEFAULT_MAX_BYTES

LEDGER_VERSION = 1
MEDIA_POLICY_VERSION = "v1"
MEDIA_POLICY_MAX_TIMEOUT_SECONDS = 120.0
RETRYABLE_REASON_CODES = frozenset(
    {
        "internal_error",
        "max_memory_exceeded",
        "max_runtime_exceeded",
        "parse_timeout",
        "parser_failed",
    }
)
NON_PDF_PARSE_EXTENSIONS = frozenset({".docx", ".pptx", ".xlsx"})
OCR_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"})
TEXT_PARSE_EXTENSIONS = frozenset({".html", ".htm", ".txt"})
PDF_OCR_POLICY_SENSITIVE_REASON_CODES = frozenset({"ocr_required", "ocr_no_text_extracted"})
MEDIA_EXTENSIONS = frozenset({".mp4"})
MEDIA_POLICY_SENSITIVE_REASON_CODES = frozenset(
    {
        "conversion_required",
        "media_no_text_extracted",
        "media_transcription_failed",
        "media_transcription_timeout",
        "media_transcription_unavailable",
        "media_duration_exceeded",
    }
)


def ocr_policy_key(
    *,
    ocr_enabled: bool,
    ocr_max_pages: int,
    ocr_timeout_seconds: float,
    ocr_dpi: int,
) -> str | None:
    """Return a safe key for OCR settings that affect PDF extraction results."""
    if not ocr_enabled:
        return None
    return (
        f"pdf-ocr:v1:max_pages={ocr_max_pages}:timeout={float(ocr_timeout_seconds)}:dpi={ocr_dpi}"
    )


def media_policy_key(
    *,
    media_enabled: bool,
    media_adapter_available: bool,
    media_adapter_name: str,
    media_timeout_seconds: float,
    media_model_key: str = "none",
    media_allow_model_download: bool = False,
    media_language: str = "en",
    media_compute_type: str = "int8",
    media_max_duration_seconds: float = 600.0,
) -> str | None:
    """Return a safe key for media settings that affect private transcription results."""
    return (
        f"media:{MEDIA_POLICY_VERSION}:"
        f"enabled={str(media_enabled).lower()}:"
        f"adapter_available={str(media_adapter_available).lower()}:"
        f"adapter={media_adapter_name}:"
        f"model={media_model_key}:"
        f"allow_download={str(media_allow_model_download).lower()}:"
        f"language={media_language}:"
        f"compute={media_compute_type}:"
        f"max_duration={float(media_max_duration_seconds)}:"
        f"timeout={float(media_timeout_seconds)}:"
        f"max_timeout={float(MEDIA_POLICY_MAX_TIMEOUT_SECONDS)}:"
        f"max_bytes={DEFAULT_MAX_BYTES}:"
        f"max_segments={MAX_MEDIA_SEGMENTS}:"
        f"max_segment_text_chars={MAX_MEDIA_SEGMENT_TEXT_CHARS}"
    )


def load_ledger(path: Path) -> dict[str, Any]:
    """Load a private resume ledger, failing closed to an empty ledger."""
    if not path.exists():
        return _empty_ledger()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_ledger()
    if not isinstance(payload, dict) or payload.get("version") != LEDGER_VERSION:
        return _empty_ledger()
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return _empty_ledger()
    return {"version": LEDGER_VERSION, "entries": entries}


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    """Atomically persist a private ledger without exposing local paths in errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(ledger, sort_keys=True, indent=2), encoding="utf-8")
        temp_path.replace(path)
    except OSError:
        raise RuntimeError("real_example gate ledger write failed") from None


def terminal_ledger_entry(
    *,
    entries: dict[str, object],
    sha256: str,
    extension: str,
    ocr_enabled: bool = False,
    ocr_policy_key: str | None = None,
    media_enabled: bool = False,
    media_policy_key: str | None = None,
) -> dict[str, object] | None:
    """Return a terminal resume entry for the same hash and extension."""
    entry = _ledger_entry_for_extension(entries=entries, sha256=sha256, extension=extension)
    if not isinstance(entry, dict):
        return None
    if entry.get("status") == "inventoried":
        return None
    reason_code = entry.get("reason_code")
    if not isinstance(reason_code, str) or reason_code in RETRYABLE_REASON_CODES:
        return None
    if (
        ocr_enabled
        and extension == ".pdf"
        and reason_code in PDF_OCR_POLICY_SENSITIVE_REASON_CODES
        and entry.get("ocr_policy_key") != ocr_policy_key
    ):
        return None
    if (
        extension in MEDIA_EXTENSIONS
        and reason_code in MEDIA_POLICY_SENSITIVE_REASON_CODES
        and entry.get("media_policy_key") != media_policy_key
    ):
        return None
    if ocr_enabled and extension in OCR_IMAGE_EXTENSIONS and reason_code == "ocr_required":
        return None
    if extension in TEXT_PARSE_EXTENSIONS and reason_code == "unsupported_format":
        return None
    if reason_code == "ocr_required" and extension in NON_PDF_PARSE_EXTENSIONS:
        return None
    if entry.get("status") == "failed":
        return None
    return entry


def record_ledger_entry(
    *,
    ledger: dict[str, Any],
    sha256: str,
    extension: str,
    size_bytes: int,
    status: str,
    parser_outcome: str,
    reason_code: str,
    ocr_policy_key: str | None = None,
    media_policy_key: str | None = None,
) -> None:
    """Record one safe per-extension outcome under a SHA256 key."""
    entries = ledger["entries"]
    bucket = entries.get(sha256)
    if isinstance(bucket, dict) and isinstance(bucket.get("by_extension"), dict):
        by_extension = bucket["by_extension"]
    else:
        by_extension = {}
        if isinstance(bucket, dict):
            legacy_extension = bucket.get("extension")
            if isinstance(legacy_extension, str):
                by_extension[legacy_extension] = bucket
        bucket = {"by_extension": by_extension}
        entries[sha256] = bucket
    entry = {
        "extension": extension,
        "size_bytes": size_bytes,
        "status": status,
        "parser_outcome": parser_outcome,
        "reason_code": reason_code,
    }
    if ocr_policy_key is not None:
        entry["ocr_policy_key"] = ocr_policy_key
    if media_policy_key is not None:
        entry["media_policy_key"] = media_policy_key
    by_extension[extension] = entry


def ledger_entry_count(ledger: dict[str, Any]) -> int:
    """Count per-extension ledger entries."""
    count = 0
    for bucket in ledger["entries"].values():
        if isinstance(bucket, dict) and isinstance(bucket.get("by_extension"), dict):
            count += len(bucket["by_extension"])
        elif isinstance(bucket, dict):
            count += 1
    return count


def _ledger_entry_for_extension(
    *,
    entries: dict[str, object],
    sha256: str,
    extension: str,
) -> dict[str, object] | None:
    bucket = entries.get(sha256)
    if not isinstance(bucket, dict):
        return None
    by_extension = bucket.get("by_extension")
    if isinstance(by_extension, dict):
        entry = by_extension.get(extension)
        return entry if isinstance(entry, dict) else None
    if bucket.get("extension") == extension:
        return bucket
    return None


def _empty_ledger() -> dict[str, Any]:
    return {"version": LEDGER_VERSION, "entries": {}}
