"""Tests for Slice 16 data-room inventory package models."""

from __future__ import annotations

import json

from idis.models.data_room_inventory_package_materialization import (
    DataRoomInventoryFileStatus,
    DataRoomInventoryPackageConstructionStatus,
    DataRoomInventoryReason,
    RunScopedDataRoomInventoryBlocker,
    RunScopedDataRoomInventoryFileRecord,
    RunScopedDataRoomInventoryPackageRecord,
    RunScopedDataRoomInventoryPackageSummary,
    deterministic_data_room_file_id,
    deterministic_data_room_inventory_package_id,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)


def test_deterministic_inventory_ids_are_stable_for_relative_path_ordering() -> None:
    first_file_id = deterministic_data_room_file_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        relative_path="Finance/Model.xlsx",
        sha256="a" * 64,
    )
    second_file_id = deterministic_data_room_file_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        relative_path="Finance\\Model.xlsx",
        sha256="a" * 64,
    )
    first_package_id = deterministic_data_room_inventory_package_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        file_ids=["file-b", "file-a"],
    )
    second_package_id = deterministic_data_room_inventory_package_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        file_ids=["file-a", "file-b"],
    )

    assert first_file_id == second_file_id
    assert first_package_id == second_package_id


def test_record_shell_and_summary_are_safe_and_do_not_expose_raw_content() -> None:
    record = _inventory_record()

    shell = record.to_shell()
    run_summary = record.to_run_step_summary()
    serialized = json.dumps(run_summary, sort_keys=True)

    assert shell.inventory_package_id == "inventory-package-001"
    assert shell.supported_document_ids == ["document-001"]
    assert shell.deferred_file_ids == ["file-video"]
    assert run_summary["construction_status"] == "completed"
    assert run_summary["inventory_package_ids"] == ["inventory-package-001"]
    assert run_summary["supported_document_ids"] == ["document-001"]
    assert run_summary["summary"]["by_extension"] == {".mp4": 1, ".xlsx": 1}
    assert "Finance/Model.xlsx" in serialized
    assert "Video/Demo.mp4" in serialized
    assert "Revenue was $5M" not in serialized
    assert "text_excerpt" not in serialized
    assert "file_contents" not in serialized
    assert "raw" not in serialized


def test_summary_counts_are_stable_and_sorted() -> None:
    summary = _inventory_record().to_summary()

    assert summary == RunScopedDataRoomInventoryPackageSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        package_count=1,
        file_count=2,
        supported_file_count=1,
        deferred_file_count=1,
        blocked_file_count=0,
        supported_document_count=1,
        construction_status=DataRoomInventoryPackageConstructionStatus.COMPLETED,
        by_extension={".mp4": 1, ".xlsx": 1},
        by_file_status={"deferred": 1, "supported": 1},
        by_reason={"conversion_required": 1, "supported_parser_available": 1},
    )


def _inventory_record() -> RunScopedDataRoomInventoryPackageRecord:
    supported = RunScopedDataRoomInventoryFileRecord(
        file_id="file-model",
        relative_path="Finance/Model.xlsx",
        path_hash="1" * 64,
        extension=".xlsx",
        size_bytes=128,
        sha256="a" * 64,
        file_status=DataRoomInventoryFileStatus.SUPPORTED,
        support_status="partially_supported",
        triage_status="partial",
        reason_codes=[DataRoomInventoryReason.SUPPORTED_PARSER_AVAILABLE.value],
        artifact_id="artifact-001",
        document_id="document-001",
    )
    deferred = RunScopedDataRoomInventoryFileRecord(
        file_id="file-video",
        relative_path="Video/Demo.mp4",
        path_hash="2" * 64,
        extension=".mp4",
        size_bytes=256,
        sha256="b" * 64,
        file_status=DataRoomInventoryFileStatus.DEFERRED,
        support_status="conversion_required",
        triage_status="conversion_required",
        reason_codes=[DataRoomInventoryReason.CONVERSION_REQUIRED.value],
        artifact_id=None,
        document_id=None,
    )
    return RunScopedDataRoomInventoryPackageRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        inventory_package_id="inventory-package-001",
        root_path_hash="0" * 64,
        files=[deferred, supported],
        blockers=[
            RunScopedDataRoomInventoryBlocker(
                blocker_id="blocker-video",
                file_id="file-video",
                reason=DataRoomInventoryReason.CONVERSION_REQUIRED,
                severity="deferred",
            )
        ],
        construction_status=DataRoomInventoryPackageConstructionStatus.COMPLETED,
    )
