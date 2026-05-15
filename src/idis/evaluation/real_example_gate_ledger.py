"""Hash-keyed private ledger helpers for the real_example gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEDGER_VERSION = 1
RETRYABLE_REASON_CODES = frozenset(
    {
        "internal_error",
        "max_memory_exceeded",
        "max_runtime_exceeded",
        "parse_timeout",
        "parser_failed",
    }
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
    by_extension[extension] = {
        "extension": extension,
        "size_bytes": size_bytes,
        "status": status,
        "parser_outcome": parser_outcome,
        "reason_code": reason_code,
    }


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
