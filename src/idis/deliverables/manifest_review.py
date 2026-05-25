"""Safe product bundle manifest review helpers."""

from __future__ import annotations

from typing import Any

from idis.deliverables.product_bundle import (
    SENSITIVE_ARTIFACT_KEY_PARTS,
    _is_sensitive_artifact_string,
)
from idis.persistence.repositories.deliverables import safe_public_deliverable_uri

_MANIFEST_ARTIFACT_STRIP_KEYS = frozenset({"object_key", *SENSITIVE_ARTIFACT_KEY_PARTS})


def sanitize_product_bundle_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a public-safe manifest payload with storage internals removed."""
    sanitized: dict[str, Any] = {}
    for key, value in manifest.items():
        key_text = str(key)
        if key_text in _MANIFEST_ARTIFACT_STRIP_KEYS:
            continue
        if isinstance(value, str) and _is_sensitive_artifact_string(value):
            continue
        if key_text == "artifacts" and isinstance(value, list):
            sanitized[key_text] = _sanitize_manifest_artifacts(value)
            continue
        sanitized[key_text] = _sanitize_manifest_scalar(value)
    return _finalize_manifest_review_payload(sanitized)


def _sanitize_manifest_artifacts(items: list[Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sanitized = _sanitize_manifest_artifact(item)
        if sanitized:
            artifacts.append(sanitized)
    return artifacts


def _finalize_manifest_review_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Ensure review output has only safe artifacts and a matching count."""
    raw_artifacts = payload.get("artifacts")
    if isinstance(raw_artifacts, list):
        safe_artifacts = [
            artifact for artifact in raw_artifacts if isinstance(artifact, dict) and artifact
        ]
    else:
        safe_artifacts = []
    payload["artifacts"] = safe_artifacts
    payload["artifact_count"] = len(safe_artifacts)
    return payload


def _sanitize_manifest_artifact(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text in _MANIFEST_ARTIFACT_STRIP_KEYS:
            continue
        if key_text == "uri":
            safe_uri = safe_public_deliverable_uri(str(item) if item is not None else None)
            if safe_uri is not None:
                sanitized[key_text] = safe_uri
            continue
        if isinstance(item, str) and _is_sensitive_artifact_string(item):
            continue
        sanitized[key_text] = _sanitize_manifest_scalar(item)
    return sanitized


def _sanitize_manifest_scalar(value: object) -> object:
    if isinstance(value, dict):
        return sanitize_product_bundle_manifest(value)
    if isinstance(value, list):
        return [_sanitize_manifest_scalar(item) for item in value]
    if isinstance(value, str) and _is_sensitive_artifact_string(value):
        return ""
    return value
