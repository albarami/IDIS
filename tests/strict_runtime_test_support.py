"""Hermetic support for tests that compose the strict API runtime.

The production composition root requires both a reviewed runtime profile and a
``PostgresAuditSink``.  These helpers satisfy those type/configuration contracts
without opening a database connection or weakening production admission.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from idis.audit.postgres_sink import PostgresAuditSink


class RecordingPostgresAuditSink(PostgresAuditSink):
    """PostgreSQL sink-shaped recorder for hermetic API middleware tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.transactional_events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    def emit_in_tx(self, _connection: object, event: dict[str, Any]) -> None:
        copied = dict(event)
        self.transactional_events.append(copied)
        self.events.append(copied)


def reviewed_strict_api_profile(tmp_path: Path) -> Any:
    """Build the canonical reviewed Task 2 API profile under ``tmp_path``."""
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    return _reviewed_profile(tmp_path, "api")
