"""Append-only safe reconciliation log for private-gate artifacts (Slice99 Task 4, Q1).

Private gates and local reports live under gitignored ``.local_reports/``. This log lets
repo-side claims be reconciled against that private evidence WITHOUT leaking content: each
entry records only a logical artifact type/id, a sha256, a created_at timestamp, safe
aggregate counts, and blocker/status codes.

Hard boundaries (fail-closed, enforced by validation - never sanitized silently):
- NO raw private filenames, paths, drive letters, URLs, or object keys in any field.
- NO free text, screenshots, prompt transcripts, provider payloads, or secrets.
- Counts are integer-valued with safe snake_case keys (secret-named keys rejected).
- Codes are SCREAMING_SNAKE identifiers.

The log is append-only JSONL: one canonical (sorted-keys, compact) JSON object per line.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from idis.validators.audit_event_validator import REDACTION_BLOCKLIST

RECONCILIATION_LOG_FILENAME = "reconciliation_log.jsonl"
DEFAULT_RECONCILIATION_LOG_PATH = Path(".local_reports") / RECONCILIATION_LOG_FILENAME

_SCHEMA_VERSION = 1

_ARTIFACT_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_COUNT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Substrings that indicate a path/URL/drive leak inside a logical identifier.
_PATH_LEAK_MARKERS = ("/", "\\", "://", "..")


class ReconciliationEntryError(ValueError):
    """Raised when a reconciliation entry violates the safe-field contract (fail-closed)."""


def _require_safe_token(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationEntryError(f"{field} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise ReconciliationEntryError(f"{field} must not contain newlines")
    token = value.strip()
    if any(marker in token for marker in _PATH_LEAK_MARKERS):
        raise ReconciliationEntryError(f"{field} must not contain path-like markers")
    if not _ARTIFACT_TOKEN_PATTERN.match(token):
        raise ReconciliationEntryError(
            f"{field} must be a logical identifier (letters, digits, '_', '.', ':', '-')"
        )
    return token


def _require_sha256(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.match(value):
        raise ReconciliationEntryError("sha256 must be 64 lowercase hex characters")
    return value


def _require_created_at(value: Any) -> str:
    if value is None:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str):
        raise ReconciliationEntryError("created_at must be an ISO-8601 string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ReconciliationEntryError(f"created_at must be ISO-8601: {value!r}") from e
    return value


def _require_counts(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ReconciliationEntryError("counts must be a mapping of safe keys to integers")
    counts: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not _COUNT_KEY_PATTERN.match(key):
            raise ReconciliationEntryError(f"counts key must be safe snake_case: {key!r}")
        if key in REDACTION_BLOCKLIST:
            raise ReconciliationEntryError(f"counts key is a redaction-blocklisted name: {key!r}")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise ReconciliationEntryError(
                f"counts values must be non-negative integers: {key!r}={raw!r}"
            )
        counts[key] = raw
    return dict(sorted(counts.items()))


def _require_code(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _CODE_PATTERN.match(value):
        raise ReconciliationEntryError(f"{field} must be a SCREAMING_SNAKE code: {value!r}")
    return value


def build_reconciliation_entry(
    *,
    artifact_type: str,
    artifact_id: str,
    sha256: str,
    counts: dict[str, int] | None = None,
    status_code: str | None = None,
    blocker_codes: list[str] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Validate and build one reconciliation entry (fail-closed)."""
    entry: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": _require_safe_token(artifact_type, field="artifact_type"),
        "artifact_id": _require_safe_token(artifact_id, field="artifact_id"),
        "sha256": _require_sha256(sha256),
        "created_at": _require_created_at(created_at),
        "counts": _require_counts(counts),
        "status_code": (
            _require_code(status_code, field="status_code") if status_code is not None else None
        ),
        "blocker_codes": sorted(
            {_require_code(code, field="blocker_codes") for code in (blocker_codes or [])}
        ),
    }
    return entry


def append_reconciliation_entry(
    *,
    artifact_type: str,
    artifact_id: str,
    sha256: str,
    counts: dict[str, int] | None = None,
    status_code: str | None = None,
    blocker_codes: list[str] | None = None,
    created_at: str | None = None,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate then append one canonical JSONL entry. Rejection leaves the log untouched."""
    entry = build_reconciliation_entry(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        sha256=sha256,
        counts=counts,
        status_code=status_code,
        blocker_codes=blocker_codes,
        created_at=created_at,
    )
    target = Path(log_path) if log_path is not None else DEFAULT_RECONCILIATION_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    with open(target, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    return entry


def read_reconciliation_log(log_path: str | Path) -> list[dict[str, Any]]:
    """Read and parse all entries (tooling/tests). Missing log means no entries."""
    target = Path(log_path)
    if not target.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries
