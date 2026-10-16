"""RED regression for JSONB-stable strict document-preflight resume."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import text

from idis.models.extraction_task import SourceSpanReference
from idis.models.run_step import STEP_ORDER, RunStep, StepName, StepStatus
from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
from idis.services.runs.document_preflight import InMemoryRunDocumentPreflightService
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task6._support import DEAL_ID, RUN_ID, TENANT_ID
from tests.slice100_task6.test_genuine_preflight_admission_red import (
    _document,
    _durable_service,
)

_CUSTOMER_ID = "a-very-long-supported-customer-document"
_FINANCIAL_ID = "b-very-long-supported-financial-document"
_EXCLUDED_ID = "z"
_CUSTOMER_SPAN_ID = "41414141-4141-4414-8414-414141414141"
_FINANCIAL_SPAN_ID = "42424242-4242-4424-8424-424242424242"
_STEP_ID = "43434343-4343-4434-8434-434343434343"
_CUSTOMER_TEXT = "Fictional customer contract renewal schedule."
_FINANCIAL_TEXT = "Fictional financial model revenue schedule."


def _mixed_supported_and_excluded_envelope() -> tuple[
    dict[str, Any],
    dict[str, list[SourceSpanReference]],
]:
    customer = _document(
        document_id=_CUSTOMER_ID,
        filename="fictional customer contract.docx",
        text=_CUSTOMER_TEXT,
    )
    customer["spans"][0]["span_id"] = _CUSTOMER_SPAN_ID
    financial = _document(
        document_id=_FINANCIAL_ID,
        filename="fictional financial model.xlsx",
        text=_FINANCIAL_TEXT,
    )
    financial["spans"][0]["span_id"] = _FINANCIAL_SPAN_ID
    excluded = _document(
        document_id=_EXCLUDED_ID,
        filename="fictional reviewed watermark.pdf",
        text="Fictional non-substantive stamp.",
    )
    excluded["spans"] = []
    excluded["metadata"]["span_count"] = 0
    excluded["metadata"]["document_no_substantive_content_receipt"] = {
        "schema_version": "fixture-v1"
    }

    result, eligible = InMemoryRunDocumentPreflightService(
        no_substantive_content_receipt_verifier=lambda _document: True
    ).run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=[customer, financial, excluded],
    )
    assert len(eligible) == 2
    envelope = result.to_run_step_summary()
    live_spans: dict[str, list[SourceSpanReference]] = {}
    text_by_document = {
        _CUSTOMER_ID: _CUSTOMER_TEXT,
        _FINANCIAL_ID: _FINANCIAL_TEXT,
    }
    for document_id in envelope["eligible_document_ids"]:
        safe_span = envelope["source_spans_by_document_id"][document_id][0]
        live_spans[document_id] = [
            SourceSpanReference(
                document_id=document_id,
                span_id=safe_span["span_id"],
                evidence_tags=[],
                locator=safe_span["locator"],
                span_type=safe_span["span_type"],
                content_hash=hashlib.sha256(
                    text_by_document[document_id].encode("utf-8")
                ).hexdigest(),
                text_excerpt=text_by_document[document_id],
            )
        ]
    return envelope, live_spans


class _JsonbRoundTripTasks:
    is_durable = True
    backend = "postgresql"

    def __init__(self, live_spans: dict[str, list[SourceSpanReference]]) -> None:
        self.live_spans = live_spans

    def load_strict_planning_source_spans(
        self,
        *,
        deal_id: str,
        source_span_references_by_document_id: dict[str, list[SourceSpanReference]],
    ) -> dict[str, list[SourceSpanReference]]:
        assert deal_id == DEAL_ID
        assert set(source_span_references_by_document_id) == set(self.live_spans)
        return self.live_spans


def test_postgres_jsonb_preflight_envelope_resumes_into_strict_planning() -> None:
    original, live_spans = _mixed_supported_and_excluded_envelope()
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO tenants (tenant_id, name)
                    VALUES (CAST(:tenant_id AS uuid), 'JSONB preflight tenant');
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (
                        CAST(:deal_id AS uuid), CAST(:tenant_id AS uuid),
                        'Fictional JSONB resume deal', now()
                    );
                    INSERT INTO runs (
                        run_id, tenant_id, deal_id, mode, status, created_at
                    ) VALUES (
                        CAST(:run_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid), 'FULL', 'QUEUED', now()
                    )
                    """
                ),
                {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID},
            )

        completed_preflight = RunStep(
            step_id=_STEP_ID,
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            step_name=StepName.DOCUMENT_PREFLIGHT,
            step_order=STEP_ORDER[StepName.DOCUMENT_PREFLIGHT],
            status=StepStatus.COMPLETED,
            result_summary=original,
        )
        with database.app_engine.begin() as connection:
            PostgresRunStepsRepository(connection, TENANT_ID).create(completed_preflight)
        with database.app_engine.begin() as connection:
            reloaded = PostgresRunStepsRepository(connection, TENANT_ID).get_step(
                RUN_ID,
                StepName.DOCUMENT_PREFLIGHT,
            )
        assert reloaded is not None
        persisted = reloaded.result_summary
        assert list(persisted["summary"]["by_support_status"]) != list(
            original["summary"]["by_support_status"]
        )
        assert list(persisted["source_spans_by_document_id"]) != list(
            original["source_spans_by_document_id"]
        )

        tasks = _JsonbRoundTripTasks(live_spans)
        durable = _durable_service(tasks=tasks)  # type: ignore[arg-type]
        captured: dict[str, Any] = {}

        def planning_callback(**kwargs: Any) -> Any:
            captured.update(kwargs)
            kwargs.pop("run_step_id")
            return durable.plan_for_run(**kwargs)

        # This wrapper represents the durable non-provider planning composition root.
        planning_callback.persistence_backend = "postgresql"  # type: ignore[attr-defined]
        planning_callback.external_calls_enabled = False  # type: ignore[attr-defined]

        context = RunContext(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            mode="FULL",
            documents=[],
            extract_fn=lambda **_kwargs: {},
            grade_fn=lambda **_kwargs: {},
            strict_resume_validation_required=True,
            active_run_step_id="rs-jsonb-preflight-resume",
            methodology_extraction_task_planning_fn=planning_callback,
        )
        orchestrator = object.__new__(RunOrchestrator)
        orchestrator._rehydrate_document_preflight_summary(context, persisted)

        result = orchestrator._execute_methodology_extraction_task_planning(
            context,
            {"status": "coverage_completed", "summary": {"coverage_rows": 2}},
        )

    assert captured["document_preflight_summary"] == persisted
    assert result["status"] == "COMPLETED"
    assert result["summary"]["total_tasks"] == 2
