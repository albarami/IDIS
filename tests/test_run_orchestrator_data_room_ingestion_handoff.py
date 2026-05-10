"""Run orchestrator tests for Slice 18 data-room ingestion handoff wiring."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from idis.audit.sink import InMemoryAuditSink
from idis.models.data_room_ingestion_handoff import (
    DataRoomIngestionHandoffRunResult,
    DataRoomIngestionHandoffStatus,
)
from idis.models.run_step import FULL_STEPS, SNAPSHOT_STEPS, StepName
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator
from tests.test_run_methodology_claim_materialization_service import TENANT_ID
from tests.test_run_orchestrator_methodology_claim_materialization import _ctx
from tests.test_xlsx_parser import create_test_xlsx


def setup_function() -> None:
    clear_run_steps_store()


def test_step_order_places_data_room_handoff_before_ingest_check() -> None:
    assert StepName.DATA_ROOM_INGESTION_HANDOFF in FULL_STEPS
    assert StepName.DATA_ROOM_INGESTION_HANDOFF in SNAPSHOT_STEPS
    assert FULL_STEPS.index(StepName.DATA_ROOM_INVENTORY_PACKAGE) < FULL_STEPS.index(
        StepName.DATA_ROOM_INGESTION_HANDOFF
    )
    assert FULL_STEPS.index(StepName.DATA_ROOM_INGESTION_HANDOFF) < FULL_STEPS.index(
        StepName.INGEST_CHECK
    )
    assert SNAPSHOT_STEPS.index(StepName.DATA_ROOM_INVENTORY_PACKAGE) < SNAPSHOT_STEPS.index(
        StepName.DATA_ROOM_INGESTION_HANDOFF
    )
    assert SNAPSHOT_STEPS.index(StepName.DATA_ROOM_INGESTION_HANDOFF) < SNAPSHOT_STEPS.index(
        StepName.INGEST_CHECK
    )


def test_default_handoff_step_defers_without_changing_inventory_corpus(tmp_path: Path) -> None:
    (tmp_path / "Finance.xlsx").write_bytes(create_test_xlsx({"Sheet1": [["ARR was $5M"]]}))
    ctx = _ctx(str(uuid.uuid4()))
    ctx.documents = []
    ctx.preflight_corpus = []
    ctx.data_room_root_path = tmp_path
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    inventory_summary = orchestrator._execute_data_room_inventory_package(ctx)  # noqa: SLF001
    handoff_summary = orchestrator._execute_data_room_ingestion_handoff(ctx)  # noqa: SLF001

    assert inventory_summary["supported_document_ids"]
    assert handoff_summary["handoff_status"] == "deferred"
    assert handoff_summary["reason_codes"] == ["durable_dependencies_not_configured"]
    assert ctx.preflight_corpus
    assert ctx.documents
    assert "ARR was $5M" not in str(handoff_summary)
    assert "text_excerpt" not in str(handoff_summary)


def test_injected_handoff_step_can_replace_context_with_durable_corpus(tmp_path: Path) -> None:
    (tmp_path / "Finance.xlsx").write_bytes(create_test_xlsx({"Sheet1": [["ARR was $5M"]]}))
    durable_corpus = [
        {
            "tenant_id": str(TENANT_ID),
            "deal_id": "deal-durable",
            "document_id": "durable-doc-1",
            "doc_type": "XLSX",
            "parse_status": "PARSED",
            "document_name": "Finance.xlsx",
            "metadata": {},
            "spans": [],
        }
    ]
    ctx = _ctx(str(uuid.uuid4()))
    ctx.deal_id = "deal-durable"
    ctx.documents = []
    ctx.preflight_corpus = []
    ctx.data_room_root_path = tmp_path

    def handoff_fn(
        **_kwargs: Any,
    ) -> tuple[DataRoomIngestionHandoffRunResult, list[dict[str, Any]]]:
        return (
            DataRoomIngestionHandoffRunResult(
                tenant_id=str(TENANT_ID),
                deal_id="deal-durable",
                run_id=ctx.run_id,
                handoff_status=DataRoomIngestionHandoffStatus.DURABLE_INGESTED,
                supported_file_count=1,
                deferred_file_count=0,
                blocked_file_count=0,
                durable_ingested_file_count=1,
                durable_reused_file_count=0,
                in_memory_fallback_file_count=0,
                file_results=[],
                reason_codes=[],
            ),
            durable_corpus,
        )

    ctx.data_room_ingestion_handoff_fn = handoff_fn
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    orchestrator._execute_data_room_inventory_package(ctx)  # noqa: SLF001
    handoff_summary = orchestrator._execute_data_room_ingestion_handoff(ctx)  # noqa: SLF001

    assert handoff_summary["handoff_status"] == "durable_ingested"
    assert ctx.preflight_corpus == durable_corpus
    assert ctx.documents == durable_corpus
