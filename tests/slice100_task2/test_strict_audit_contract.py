"""Slice100 Task 2: durable audit events are canonical and fail closed."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import AuditSinkError

_TENANT_ID = "10000000-0000-4000-8000-000000000002"


class _RecordingConnection:
    def __init__(self) -> None:
        self.parameters: dict[str, Any] | None = None

    def execute(self, _statement: Any, parameters: dict[str, Any]) -> None:
        self.parameters = parameters


class _SqlFailingConnection:
    def execute(self, _statement: Any, _parameters: dict[str, Any]) -> None:
        raise SQLAlchemyError("driver echoed SECRET_AUDIT_SENTINEL in bound parameters")


class _FailingAuditSink:
    def emit(self, _event: dict[str, Any]) -> None:
        raise AuditSinkError("durable audit unavailable")


def test_postgres_sink_rejects_noncanonical_event_before_insert() -> None:
    """The sink must expose producer defects instead of fabricating provenance."""
    conn = _RecordingConnection()
    source_event = {
        "event_type": "analysis.started",
        "tenant_id": _TENANT_ID,
        "timestamp": "2026-07-15T10:11:12Z",
        "deal_id": "30000000-0000-4000-8000-000000000002",
    }

    with pytest.raises(AuditSinkError, match="validation"):
        PostgresAuditSink().emit_in_tx(conn, source_event)  # type: ignore[arg-type]

    assert conn.parameters is None
    assert "event_id" not in source_event
    assert "occurred_at" not in source_event


def test_postgres_sink_inserts_unchanged_validated_event() -> None:
    conn = _RecordingConnection()
    source_event = {
        "event_id": "20000000-0000-4000-8000-000000000002",
        "occurred_at": "2026-07-15T10:11:12Z",
        "tenant_id": _TENANT_ID,
        "actor": {"actor_type": "SERVICE", "actor_id": "analysis-runner"},
        "request": {
            "request_id": "run-2",
            "method": "POST",
            "path": "/internal/analysis/runs/run-2",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "run",
            "resource_id": "run-2",
            "deal_id": "30000000-0000-4000-8000-000000000002",
        },
        "event_type": "integration.analysis_started",
        "severity": "LOW",
        "summary": "Analysis run started",
        "payload": {"safe": {"agent_count": 8}, "hashes": [], "refs": []},
    }

    PostgresAuditSink().emit_in_tx(conn, source_event)  # type: ignore[arg-type]

    assert conn.parameters is not None
    assert json.loads(conn.parameters["event"]) == source_event


def test_postgres_sink_rejects_timezone_naive_occurrence_before_insert() -> None:
    conn = _RecordingConnection()
    source_event = {
        "event_id": "20000000-0000-4000-8000-000000000002",
        "occurred_at": "2026-07-15T10:11:12",
        "tenant_id": _TENANT_ID,
        "actor": {"actor_type": "SERVICE", "actor_id": "analysis-runner"},
        "request": {
            "request_id": "run-2",
            "method": "POST",
            "path": "/internal/analysis/runs/run-2",
            "status_code": 200,
        },
        "resource": {"resource_type": "run", "resource_id": "run-2"},
        "event_type": "integration.analysis_started",
        "severity": "LOW",
        "summary": "Analysis run started",
        "payload": {"safe": {}, "hashes": [], "refs": []},
    }

    with pytest.raises(AuditSinkError, match="validation"):
        PostgresAuditSink().emit_in_tx(conn, source_event)  # type: ignore[arg-type]

    assert conn.parameters is None


def test_postgres_sink_does_not_expose_driver_parameters_on_insert_failure() -> None:
    event = {
        "event_id": "20000000-0000-4000-8000-000000000002",
        "occurred_at": "2026-07-15T10:11:12Z",
        "tenant_id": _TENANT_ID,
        "actor": {"actor_type": "SERVICE", "actor_id": "analysis-runner"},
        "request": {
            "request_id": "run-2",
            "method": "POST",
            "path": "/internal/analysis/runs/run-2",
            "status_code": 200,
        },
        "resource": {"resource_type": "run", "resource_id": "run-2"},
        "event_type": "integration.analysis.started",
        "severity": "LOW",
        "summary": "Analysis run started",
        "payload": {"safe": {}, "hashes": [], "refs": []},
    }

    with pytest.raises(AuditSinkError) as exc_info:
        PostgresAuditSink().emit_in_tx(_SqlFailingConnection(), event)  # type: ignore[arg-type]

    assert str(exc_info.value) == "AUDIT_EMIT_FAILED"
    assert "SECRET_AUDIT_SENTINEL" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, SQLAlchemyError)


def test_postgres_sink_rejects_event_without_tenant() -> None:
    conn = _RecordingConnection()

    with pytest.raises(AuditSinkError, match="MISSING_TENANT_ID"):
        PostgresAuditSink().emit_in_tx(
            conn,  # type: ignore[arg-type]
            {"event_type": "analysis.started", "timestamp": "2026-07-15T10:11:12Z"},
        )

    assert conn.parameters is None


def _claim_emission() -> None:
    from idis.services.claims.service import ClaimService

    service = object.__new__(ClaimService)
    service._tenant_id = _TENANT_ID
    service._audit_sink = _FailingAuditSink()
    service._emit_audit_event("claim.created", "claim-1")


def _extraction_pipeline_emission() -> None:
    from idis.services.extraction.pipeline import ExtractionPipeline

    pipeline = object.__new__(ExtractionPipeline)
    pipeline._audit_sink = _FailingAuditSink()
    # Canonical producers require the real run/deal correlation before reaching the sink.
    pipeline._emit_audit(
        "extraction.pipeline.completed",
        tenant_id=_TENANT_ID,
        details={
            "run_id": "40000000-0000-4000-8000-000000000002",
            "deal_id": "30000000-0000-4000-8000-000000000002",
        },
    )


def _extraction_service_emission() -> None:
    from idis.services.extraction.service import ExtractionService

    service = object.__new__(ExtractionService)
    service._tenant_id = _TENANT_ID
    service._audit_sink = _FailingAuditSink()
    service._emit_audit_event("extraction.completed", "request-1")


def _sanad_emission() -> None:
    from idis.services.sanad.service import SanadService

    service = object.__new__(SanadService)
    service._tenant_id = _TENANT_ID
    service._audit_sink = _FailingAuditSink()
    service._emit_audit_event("sanad.created", "sanad-1")


def _defect_emission() -> None:
    from idis.services.defects.service import DefectService

    service = object.__new__(DefectService)
    service._tenant_id = _TENANT_ID
    service._audit_sink = _FailingAuditSink()
    service._emit_audit_event("defect.created", "defect-1")


def _auto_grade_emission() -> None:
    from idis.services.sanad.auto_grade import _emit_audit

    _emit_audit(
        _FailingAuditSink(),
        "sanad.graded",
        _TENANT_ID,
        {
            "sanad_id": "60000000-0000-4000-8000-000000000002",
            "claim_id": "50000000-0000-4000-8000-000000000002",
            "deal_id": "30000000-0000-4000-8000-000000000002",
        },
    )


@pytest.mark.parametrize(
    "emit_component_event",
    [
        _claim_emission,
        _extraction_pipeline_emission,
        _extraction_service_emission,
        _sanad_emission,
        _defect_emission,
        _auto_grade_emission,
    ],
    ids=["claim", "extraction-pipeline", "extraction-service", "sanad", "defect", "grade"],
)
def test_component_audit_failure_is_fatal(
    emit_component_event: Callable[[], None],
) -> None:
    with pytest.raises(AuditSinkError, match="durable audit unavailable"):
        emit_component_event()
