"""Audit event sink implementations for IDIS.

Provides append-only sinks for audit event persistence.
All sinks implement the AuditSink protocol.

Design requirements:
- Append-only: never truncate/overwrite
- Fail closed: any IO failure raises AuditSinkError
- Deterministic: consistent JSON serialization (sorted keys, no extra whitespace)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

AUDIT_LOG_PATH_ENV = "IDIS_AUDIT_LOG_PATH"
DEFAULT_AUDIT_LOG_PATH = "./var/audit/audit_events.jsonl"


class AuditSinkError(Exception):
    """Raised when audit event emission fails.

    This exception should be caught by middleware and converted to a 500 response.
    """

    pass


@runtime_checkable
class AuditSink(Protocol):
    """Protocol for audit event sinks.

    All implementations must be append-only and fail closed on errors.
    """

    def emit(self, event: dict[str, Any]) -> None:
        """Emit an audit event to the sink.

        Args:
            event: Validated audit event dict

        Raises:
            AuditSinkError: If emission fails for any reason
        """
        ...


class JsonlFileAuditSink:
    """Append-only JSONL file sink for audit events.

    Configuration:
    - File path from env IDIS_AUDIT_LOG_PATH (default: ./var/audit/audit_events.jsonl)
    - Creates parent directories if missing
    - Appends one line per event: json.dumps(event, sort_keys=True, separators=(",", ":")) + "\\n"
    - Never truncates/overwrites existing content

    Fail-closed behavior:
    - Any IO error raises AuditSinkError
    - Directory creation failure raises AuditSinkError
    - Serialization failure raises AuditSinkError
    """

    def __init__(self, file_path: str | None = None) -> None:
        """Initialize the JSONL file sink.

        Args:
            file_path: Override path for the audit log file.
                       If None, reads from IDIS_AUDIT_LOG_PATH env var,
                       falling back to DEFAULT_AUDIT_LOG_PATH.
        """
        if file_path is not None:
            self._file_path = Path(file_path)
        else:
            env_path = os.environ.get(AUDIT_LOG_PATH_ENV)
            if env_path:
                self._file_path = Path(env_path)
            else:
                self._file_path = Path(DEFAULT_AUDIT_LOG_PATH)

    @property
    def file_path(self) -> Path:
        """Return the configured file path."""
        return self._file_path

    def _ensure_parent_directory(self) -> None:
        """Create parent directories if they don't exist.

        Raises:
            AuditSinkError: If directory creation fails
        """
        parent = self._file_path.parent
        if not parent.exists():
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise AuditSinkError(f"Failed to create audit log directory {parent}: {e}") from e

    def emit(self, event: dict[str, Any]) -> None:
        """Emit an audit event to the JSONL file.

        Serializes the event as a single JSON line and appends to the file.
        Uses sorted keys and minimal separators for deterministic output.

        Args:
            event: Validated audit event dict

        Raises:
            AuditSinkError: If serialization or file write fails
        """
        try:
            line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        except (TypeError, ValueError) as e:
            raise AuditSinkError(f"Failed to serialize audit event: {e}") from e

        self._ensure_parent_directory()

        try:
            with open(self._file_path, mode="a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            raise AuditSinkError(f"Failed to write audit event to {self._file_path}: {e}") from e


class InMemoryAuditSink:
    """In-memory audit sink for testing (no disk writes).

    Stores emitted events in a list for later inspection.
    Thread-safe for concurrent test usage.
    """

    def __init__(self) -> None:
        """Initialize the in-memory sink."""
        self._events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        """Emit an audit event to memory.

        Args:
            event: Validated audit event dict
        """
        # Serialize and deserialize to ensure JSON compatibility
        try:
            line = json.dumps(event, sort_keys=True, separators=(",", ":"))
            self._events.append(json.loads(line))
        except (TypeError, ValueError) as e:
            raise AuditSinkError(f"Failed to serialize audit event: {e}") from e

    @property
    def events(self) -> list[dict[str, Any]]:
        """Return all emitted events."""
        return list(self._events)

    def clear(self) -> None:
        """Clear all stored events."""
        self._events.clear()


def get_audit_sink() -> AuditSink:
    """Factory function to get the configured audit sink.

    Returns:
        Configured AuditSink instance (currently JsonlFileAuditSink)
    """
    return JsonlFileAuditSink()
