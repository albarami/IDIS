"""Shared safe-projection sanitizer for publicly-exposed run/step summaries.

Single source of truth for reducing persisted step/run summaries (and any run-derived data emitted
across a trust boundary) to a scalar / allowlisted safe projection: no raw bytes, paths, URIs,
base64 blobs, excerpts, transcripts, or exception text. ``idis.api.routes.runs`` re-exports these so
the API and the Slice97 webhook payload builder share ONE implementation (acceptance A2). The logic
is lifted verbatim from the original ``routes/runs.py`` definitions to avoid behavior drift.
"""

from __future__ import annotations

from typing import Any

SAFE_PUBLIC_STEP_ERROR_MESSAGE = "Run step failed; see error code for details."
SENSITIVE_SUMMARY_KEY_PARTS = frozenset(
    {
        "base64",
        "bytes",
        "content_b64",
        "artifact",
        "excerpt",
        "file",
        "file_content",
        "filename",
        "hash",
        "header",
        "html",
        "local_path",
        "path",
        "raw",
        "sha",
        "span",
        "text",
        "transcript",
        "uri",
    }
)
SENSITIVE_SUMMARY_VALUE_PARTS = frozenset(
    {
        "content_b64",
        "confidential",
        "raw bytes",
        "raw_bytes",
        "raw text",
        "raw_text",
        "parsed text",
        "parsed_text",
        "text_excerpt",
        "_marker",
        "revenue was 10m",
        "ebitda was 2m",
    }
)
SAFE_PUBLIC_SUMMARY_KEYS = frozenset({"artifact_count", "manifest_uri"})


def safe_public_summary_dict(value: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized public summary object (always a dict)."""
    safe = safe_public_summary(value)
    return safe if isinstance(safe, dict) else {}


def safe_public_summary(value: object) -> object:
    """Sanitize persisted step summaries before exposing them publicly."""
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.startswith("_"):
                continue
            if key_text == "blocked_candidates":
                continue
            if key_text == "manifest_uri":
                if isinstance(item, str):
                    from idis.persistence.repositories.deliverables import (
                        safe_public_deliverable_uri,
                    )

                    if safe_uri := safe_public_deliverable_uri(item):
                        sanitized[key_text] = safe_uri
                continue
            if key_text == "artifact_count":
                if isinstance(item, int | float) and not isinstance(item, bool):
                    sanitized[key_text] = item
                continue
            if key_text == "reproducibility_hashes":
                if isinstance(item, list):
                    hashes = [value for value in item if is_sha256_hex(value)]
                    if hashes:
                        sanitized[key_text] = hashes
                    elif item == []:
                        sanitized[key_text] = []
                continue
            if key_text == "blocked_candidate_reason_counts":
                if isinstance(item, dict):
                    reason_counts = safe_reason_counts(item)
                    if reason_counts:
                        sanitized[key_text] = reason_counts
                    elif item == {}:
                        sanitized[key_text] = {}
                continue
            if key_text not in SAFE_PUBLIC_SUMMARY_KEYS and is_sensitive_summary_key(key_text):
                continue
            safe_item = safe_public_summary(item)
            if safe_item is not None:
                sanitized[key_text] = safe_item
        return sanitized
    if isinstance(value, list):
        return [safe_item for item in value if (safe_item := safe_public_summary(item)) is not None]
    if isinstance(value, str):
        return value if is_safe_public_summary_string(value) else None
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)


def is_sensitive_summary_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SENSITIVE_SUMMARY_KEY_PARTS)


def is_safe_public_summary_string(value: str) -> bool:
    normalized = value.lower()
    if any(part in normalized for part in SENSITIVE_SUMMARY_VALUE_PARTS):
        return False
    if looks_like_base64_blob(value):
        return False
    if "://" in value or "\\" in value or "/" in value:
        return False
    if len(value) > 512:
        return False
    if (
        "error:" in normalized
        or "exception:" in normalized
        or normalized.startswith(("valueerror", "runtimeerror", "traceback"))
    ):
        return False
    return not (len(value) > 1 and value[1] == ":")


def is_sha256_hex(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def safe_reason_counts(value: dict[object, object]) -> dict[str, int]:
    safe: dict[str, int] = {}
    for key, item in value.items():
        key_text = str(key)
        if not key_text.replace("_", "").isalnum():
            continue
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            safe[key_text] = item
    return safe


def looks_like_base64_blob(value: str) -> bool:
    """Return True for likely opaque base64 payloads."""
    import base64
    import binascii

    stripped = value.strip()
    if len(stripped) < 16 or len(stripped) % 4 != 0:
        return False
    try:
        base64.b64decode(stripped, validate=True)
    except (binascii.Error, ValueError):
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
    return all(char in allowed for char in stripped)
