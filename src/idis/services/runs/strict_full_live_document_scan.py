"""Safe document metadata scanning helpers for strict full-live preflight."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def safe_extensions(
    *,
    data_room_root_path: str | Path | None,
    data_room_file_extensions: Sequence[str] | None,
) -> list[str]:
    """Return lower-cased file extensions without exposing filenames."""
    extensions = [str(extension).lower() for extension in data_room_file_extensions or []]
    if data_room_root_path is None:
        return extensions
    root = Path(data_room_root_path)
    if not root.exists() or not root.is_dir():
        return extensions
    return sorted(
        {path.suffix.lower() for path in root.rglob("*") if path.is_file()} | set(extensions)
    )


def preflight_has_ocr_required_document(
    preflight_corpus: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """Return whether safe preflight metadata indicates OCR is required."""
    for document in preflight_corpus or []:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        capability = metadata.get("parser_capability")
        reason_codes = (
            _string_set(metadata.get("parser_reason_codes"))
            | _string_set(metadata.get("reason_codes"))
            | _string_set(metadata.get("parse_error_codes"))
        )
        if metadata.get("parser_requires_ocr") is True or metadata.get("requires_ocr") is True:
            return True
        if isinstance(capability, Mapping) and capability.get("requires_ocr") is True:
            return True
        if "ocr_required" in reason_codes:
            return True
    return False


def preflight_has_media_document(
    preflight_corpus: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """Return whether safe preflight metadata indicates media/STT is required."""
    for document in preflight_corpus or []:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        document_name = str(document.get("document_name") or "")
        doc_type = str(document.get("doc_type") or "").lower()
        metadata_types = {
            str(metadata.get(key) or "").lower()
            for key in ("detected_format", "parser_doc_type", "file_type")
        }
        reason_codes = (
            _string_set(metadata.get("parser_reason_codes"))
            | _string_set(metadata.get("reason_codes"))
            | _string_set(metadata.get("parse_error_codes"))
        )
        if Path(document_name).suffix.lower() == ".mp4":
            return True
        if doc_type in {"media", "mp4"} or metadata_types & {"media", "mp4"}:
            return True
        if (
            "media_transcription_unavailable" in reason_codes
            or "conversion_required" in reason_codes
        ):
            return True
    return False


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return set()
    return {str(item) for item in value}
