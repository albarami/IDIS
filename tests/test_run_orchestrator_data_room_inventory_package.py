"""Run orchestrator tests for Slice 16 data-room inventory package wiring."""

from __future__ import annotations

import uuid
from pathlib import Path

from idis.audit.sink import InMemoryAuditSink
from idis.models.data_room_inventory_package_materialization import (
    DataRoomInventoryPackageConstructionStatus,
    RunScopedDataRoomInventoryPackageShell,
)
from idis.models.document_preflight import DocumentPreflightStatus
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
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


def test_step_order_places_data_room_inventory_before_ingest_check() -> None:
    assert StepName.DATA_ROOM_INVENTORY_PACKAGE in FULL_STEPS
    assert StepName.DATA_ROOM_INVENTORY_PACKAGE in SNAPSHOT_STEPS
    assert StepName.DATA_ROOM_INVENTORY_PACKAGE not in FULL_ONLY_STEPS
    assert FULL_STEPS.index(StepName.DATA_ROOM_INVENTORY_PACKAGE) < FULL_STEPS.index(
        StepName.INGEST_CHECK
    )
    assert SNAPSHOT_STEPS.index(StepName.DATA_ROOM_INVENTORY_PACKAGE) < SNAPSHOT_STEPS.index(
        StepName.INGEST_CHECK
    )


def test_inventory_step_hands_supported_documents_into_preflight(tmp_path: Path) -> None:
    (tmp_path / "Finance").mkdir()
    (tmp_path / "Finance" / "Model.xlsx").write_bytes(
        create_test_xlsx({"Sheet1": [["ARR was $5M"]]})
    )
    (tmp_path / "Media").mkdir()
    (tmp_path / "Media" / "Demo.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    ctx = _ctx(str(uuid.uuid4()))
    ctx.documents = []
    ctx.preflight_corpus = []
    ctx.data_room_root_path = tmp_path
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    inventory_summary = orchestrator._execute_data_room_inventory_package(ctx)  # noqa: SLF001
    preflight_summary = orchestrator._execute_document_preflight(ctx)  # noqa: SLF001

    assert ctx.data_room_inventory_package is not None
    assert inventory_summary["construction_status"] == "completed"
    assert inventory_summary["supported_document_ids"]
    assert len(ctx.documents) == 1
    assert ctx.documents[0]["document_id"] == inventory_summary["supported_document_ids"][0]
    assert preflight_summary["status"] in {
        DocumentPreflightStatus.COMPLETED.value,
        DocumentPreflightStatus.PARTIAL.value,
    }
    assert "ARR was $5M" not in str(inventory_summary)
    assert "text_excerpt" not in str(inventory_summary)


def test_rehydrate_data_room_inventory_package_uses_safe_shell_only(tmp_path: Path) -> None:
    (tmp_path / "Finance.xlsx").write_bytes(create_test_xlsx({"Sheet1": [["ARR was $5M"]]}))
    ctx = _ctx(str(uuid.uuid4()))
    ctx.documents = []
    ctx.preflight_corpus = []
    ctx.data_room_root_path = tmp_path
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_data_room_inventory_package(ctx)  # noqa: SLF001
    ctx2 = _ctx(ctx.run_id)
    orchestrator._rehydrate_data_room_inventory_package(ctx2, summary)  # noqa: SLF001

    assert isinstance(ctx2.data_room_inventory_package, RunScopedDataRoomInventoryPackageShell)
    assert ctx2.data_room_inventory_package.construction_status == (
        DataRoomInventoryPackageConstructionStatus.COMPLETED
    )
    assert ctx2.data_room_inventory_package.supported_document_ids
    assert not hasattr(ctx2.data_room_inventory_package, "file_contents")
    assert not hasattr(ctx2.data_room_inventory_package, "text_excerpt")


def test_inventory_noop_preserves_existing_document_flow() -> None:
    ctx = _ctx(str(uuid.uuid4()))
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_data_room_inventory_package(ctx)  # noqa: SLF001

    assert summary["construction_status"] == "completed"
    assert summary["inventory_package_ids"] == []
    assert ctx.documents
    assert ctx.preflight_corpus == []
