"""Slice100 Task 2: strict-path producers emit canonical, safe audit events."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any

from idis.validators.audit_event_validator import validate_audit_event

TENANT_ID = "10000000-0000-4000-8000-000000000002"
DEAL_ID = "30000000-0000-4000-8000-000000000002"
RUN_ID = "40000000-0000-4000-8000-000000000002"
RESOURCE_ID = "50000000-0000-4000-8000-000000000002"


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _assert_canonical(event: dict[str, Any]) -> None:
    result = validate_audit_event(event)
    assert result.passed, [(error.code, error.path) for error in result.errors]


def _assert_no_raw_content(event: dict[str, Any], raw: str) -> None:
    assert raw not in json.dumps(event, sort_keys=True)


def test_runtime_signal_is_canonical_even_though_emission_is_best_effort() -> None:
    from idis.observability.runtime_signals import RUN_CLAIMED, emit_run_signal

    sink = RecordingSink()
    emit_run_signal(
        sink,
        event_type=RUN_CLAIMED,
        tenant_id=TENANT_ID,
        details={"run_id": RUN_ID, "mode": "SNAPSHOT"},
    )

    _assert_canonical(sink.events[0])
    assert sink.events[0]["resource"]["resource_id"] == RUN_ID


def test_document_route_producers_are_canonical_and_do_not_leak_titles_or_errors() -> None:
    from idis.api.routes.documents import (
        _emit_document_created_audit,
        _emit_ingestion_audit,
        delete_document,
    )

    sink = RecordingSink()
    request = SimpleNamespace(
        state=SimpleNamespace(
            request_id="request-doc-1",
            tenant_context=SimpleNamespace(
                actor_id="user-1", actor_type="HUMAN", roles={"ANALYST"}
            ),
        ),
        app=SimpleNamespace(state=SimpleNamespace(audit_sink=sink)),
        url=SimpleNamespace(path="/v1/documents/resource-1"),
    )
    title = "Highly Confidential Acquisition Target"
    raw_error = "parser leaked c:\\secret\\target.pdf"
    _emit_document_created_audit(
        request,
        TENANT_ID,
        {
            "doc_id": RESOURCE_ID,
            "deal_id": DEAL_ID,
            "title": title,
            "doc_type": "PDF",
            "sha256": "a" * 64,
        },
        "idempotency-1",
    )
    _emit_ingestion_audit(
        request,
        TENANT_ID,
        RESOURCE_ID,
        DEAL_ID,
        RUN_ID,
        "FAILED",
        "idempotency-1",
        raw_error,
    )

    assert all(validate_audit_event(event).passed for event in sink.events)
    for event in sink.events:
        _assert_no_raw_content(event, title)
        _assert_no_raw_content(event, raw_error)
    # One helper owns the canonical delete contract; the route must not hand-roll it.
    assert "_emit_document_deleted_audit(" in inspect.getsource(delete_document)


def test_ingestion_service_producers_are_canonical_and_redact_raw_parse_messages() -> None:
    from idis.services.ingestion.service import IngestionService

    sink = RecordingSink()
    service = object.__new__(IngestionService)
    service._audit_sink = sink
    ctx = SimpleNamespace(
        tenant_id=TENANT_ID,
        actor_id="user-1",
        actor_type="HUMAN",
        actor_roles=("ANALYST",),
        request_id="request-ingest-1",
        idempotency_key="idempotency-1",
    )
    artifact = SimpleNamespace(
        doc_id=RESOURCE_ID,
        deal_id=DEAL_ID,
        title="Secret target title",
        doc_type=SimpleNamespace(value="PDF"),
    )
    document = SimpleNamespace(
        document_id=RESOURCE_ID,
        deal_id=DEAL_ID,
        doc_type=SimpleNamespace(value="PDF"),
        parse_status=SimpleNamespace(value="FAILED"),
    )
    raw_error = "OCR exposed raw customer text"
    service._emit_document_created(ctx, artifact, "a" * 64)
    service._emit_ingestion_completed(ctx, document, 7, "a" * 64)
    service._emit_ingestion_failed(
        ctx,
        document,
        [SimpleNamespace(code=SimpleNamespace(value="PARSE_FAILED"), message=raw_error)],
        "a" * 64,
    )

    assert len(sink.events) == 3
    for event in sink.events:
        _assert_canonical(event)
        _assert_no_raw_content(event, artifact.title)
        _assert_no_raw_content(event, raw_error)


def test_claim_defect_and_sanad_service_producers_are_canonical() -> None:
    from idis.services.claims.service import ClaimService
    from idis.services.defects.service import DefectService
    from idis.services.sanad.service import SanadService

    cases = [
        (ClaimService, "claim.created", "claim_id"),
        (DefectService, "defect.created", "defect_id"),
        (SanadService, "sanad.created", "sanad_id"),
    ]
    for service_type, event_type, detail_key in cases:
        sink = RecordingSink()
        service = object.__new__(service_type)
        service._tenant_id = TENANT_ID
        service._db_conn = None
        service._audit_sink = sink
        service._emit_audit_event(
            event_type,
            RESOURCE_ID,
            details={detail_key: RESOURCE_ID, "deal_id": DEAL_ID},
            request_id="request-service-1",
        )
        _assert_canonical(sink.events[0])


def test_extraction_and_auto_grade_producers_are_canonical() -> None:
    from idis.services.extraction.pipeline import ExtractionPipeline
    from idis.services.sanad.auto_grade import _emit_audit

    sink = RecordingSink()
    pipeline = object.__new__(ExtractionPipeline)
    pipeline._audit_sink = sink
    pipeline._emit_audit(
        "extraction.pipeline.completed",
        tenant_id=TENANT_ID,
        details={"run_id": RUN_ID, "deal_id": DEAL_ID, "claim_count": 4},
    )
    _emit_audit(
        sink,
        "sanad.graded",
        TENANT_ID,
        {"run_id": RUN_ID, "deal_id": DEAL_ID, "sanad_id": RESOURCE_ID, "grade": "B"},
    )

    assert len(sink.events) == 2
    assert all(validate_audit_event(event).passed for event in sink.events)


def test_graph_and_enrichment_producers_are_canonical_and_error_safe() -> None:
    from idis.persistence.graph_consistency import ProjectionStatus, _build_audit_event
    from idis.services.enrichment.rights_gate import RightsClass, _emit_deny_audit
    from idis.services.enrichment.service import EnrichmentService

    raw_error = "neo4j://admin:password@private-host/raw-title"
    graph_event = _build_audit_event(
        tenant_id=TENANT_ID,
        entity_type="deal",
        entity_id=DEAL_ID,
        status=ProjectionStatus.FAILED,
        error=raw_error,
    )
    _assert_canonical(graph_event)
    _assert_no_raw_content(graph_event, raw_error)

    sink = RecordingSink()
    _emit_deny_audit(
        audit_sink=sink,
        provider_id="provider-1",
        tenant_id=TENANT_ID,
        rights_class=RightsClass.RED,
        reason="raw contractual reason must not leak",
        request_id="request-enrichment-1",
    )
    service = object.__new__(EnrichmentService)
    service._audit_sink = sink
    service._emit_audit_event(
        event_type="enrichment.completed",
        tenant_id=TENANT_ID,
        provider_id="provider-1",
        request_id="request-enrichment-1",
        severity="LOW",
        details={"fact_count": 3},
    )
    assert all(validate_audit_event(event).passed for event in sink.events)
    _assert_no_raw_content(sink.events[0], "raw contractual reason must not leak")


def test_analysis_scoring_and_deliverable_producers_are_canonical_and_error_safe() -> None:
    from idis.analysis.runner import AnalysisEngine
    from idis.analysis.scoring.engine import ScoringEngine
    from idis.deliverables.generator import DeliverablesGenerator

    raw_error = "upstream leaked a document title"
    cases = [
        (AnalysisEngine, "analysis.failed"),
        (ScoringEngine, "analysis.scoring.failed"),
        (DeliverablesGenerator, "deliverable.generation.failed"),
    ]
    for producer_type, event_type in cases:
        sink = RecordingSink()
        producer = object.__new__(producer_type)
        producer._audit_sink = sink
        producer._emit_audit(
            event_type,
            {
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "error": raw_error,
                "error_type": "UPSTREAM_FAILURE",
            },
        )
        _assert_canonical(sink.events[0])
        _assert_no_raw_content(sink.events[0], raw_error)
