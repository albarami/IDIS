"""Tests for Slice 18 durable data-room ingestion handoff service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from idis.models.data_room_ingestion_handoff import DataRoomIngestionHandoffStatus
from idis.models.data_room_inventory_package_materialization import (
    DataRoomInventoryFileStatus,
)
from idis.services.runs.data_room_ingestion_handoff import (
    InMemoryRunDataRoomIngestionHandoffService,
)
from idis.services.runs.data_room_inventory_package import (
    InMemoryRunDataRoomInventoryPackageService,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)
from tests.test_xlsx_parser import create_test_xlsx


def test_handoff_ingests_only_supported_files_and_keeps_summary_safe(tmp_path: Path) -> None:
    """Only supported parsed files should call the injected durable ingestion adapter."""
    package, inventory_corpus = _inventory_package_for_mixed_room(tmp_path)
    ingest_calls: list[dict[str, Any]] = []

    def ingest_bytes(**kwargs: Any) -> dict[str, Any]:
        ingest_calls.append(kwargs)
        file_record = kwargs["file_record"]
        return {
            "artifact_id": f"durable-artifact-{file_record.file_id}",
            "document_id": f"durable-document-{file_record.file_id}",
            "storage_uri": f"object://{file_record.sha256}",
            "parse_status": "PARSED",
        }

    result, corpus = InMemoryRunDataRoomIngestionHandoffService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=tmp_path,
        inventory_package=package,
        inventory_corpus=inventory_corpus,
        ingest_bytes_fn=ingest_bytes,
        existing_document_lookup_fn=lambda _file: None,
    )
    summary = result.to_run_step_summary()
    supported_file = next(
        file for file in package.files if file.file_status == DataRoomInventoryFileStatus.SUPPORTED
    )

    assert result.handoff_status == DataRoomIngestionHandoffStatus.DURABLE_INGESTED
    assert summary["handoff_status"] == "durable_ingested"
    assert summary["supported_file_count"] == 2
    assert summary["durable_ingested_file_count"] == 2
    assert summary["durable_reused_file_count"] == 0
    assert summary["deferred_file_count"] == 2
    assert summary["blocked_file_count"] == 1
    assert len(ingest_calls) == 2
    assert ingest_calls[0]["file_record"].file_status == DataRoomInventoryFileStatus.SUPPORTED
    assert ingest_calls[0]["metadata"]["inventory_package_id"] == package.inventory_package_id
    assert ingest_calls[0]["metadata"]["inventory_file_id"] == supported_file.file_id
    assert ingest_calls[0]["metadata"]["source_system"] == "data_room_inventory"
    assert corpus == inventory_corpus
    assert "ARR was $5M" not in str(summary)
    assert "text_excerpt" not in str(summary)
    assert "file_contents" not in str(summary)


def test_handoff_reuses_existing_inventory_mapping_before_ingestion(tmp_path: Path) -> None:
    """A rerun must check inventory provenance before calling ingestion again."""
    package, inventory_corpus = _inventory_package_for_mixed_room(tmp_path)

    def fail_if_called(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("ingest_bytes_fn should not run for reused inventory files")

    result, _corpus = InMemoryRunDataRoomIngestionHandoffService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=tmp_path,
        inventory_package=package,
        inventory_corpus=inventory_corpus,
        ingest_bytes_fn=fail_if_called,
        existing_document_lookup_fn=lambda file_record: {
            "artifact_id": f"existing-artifact-{file_record.file_id}",
            "document_id": f"existing-document-{file_record.file_id}",
            "storage_uri": f"object://existing/{file_record.sha256}",
            "parse_status": "PARSED",
        },
    )
    summary = result.to_run_step_summary()

    assert result.handoff_status == DataRoomIngestionHandoffStatus.DURABLE_REUSED
    assert summary["handoff_status"] == "durable_reused"
    assert summary["durable_ingested_file_count"] == 0
    assert summary["durable_reused_file_count"] == 2
    reused_results = [
        file for file in summary["file_results"] if file["handoff_status"] == "durable_reused"
    ]
    assert len(reused_results) == 2


def test_handoff_defers_without_durable_dependencies(tmp_path: Path) -> None:
    """The default service boundary must be explicit when durable dependencies are absent."""
    package, inventory_corpus = _inventory_package_for_mixed_room(tmp_path)

    result, corpus = InMemoryRunDataRoomIngestionHandoffService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=tmp_path,
        inventory_package=package,
        inventory_corpus=inventory_corpus,
    )
    summary = result.to_run_step_summary()

    assert result.handoff_status == DataRoomIngestionHandoffStatus.DEFERRED
    assert summary["handoff_status"] == "deferred"
    assert summary["reason_codes"] == ["durable_dependencies_not_configured"]
    assert corpus == []


def test_handoff_reports_in_memory_fallback_when_reuse_lookup_is_absent(tmp_path: Path) -> None:
    """Injected ingestion without a reuse lookup must be labeled as in-memory fallback."""
    package, inventory_corpus = _inventory_package_for_mixed_room(tmp_path)

    result, _corpus = InMemoryRunDataRoomIngestionHandoffService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=tmp_path,
        inventory_package=package,
        inventory_corpus=inventory_corpus,
        ingest_bytes_fn=lambda **kwargs: {
            "artifact_id": f"fallback-artifact-{kwargs['file_record'].file_id}",
            "document_id": f"fallback-document-{kwargs['file_record'].file_id}",
            "storage_uri": "memory://fallback",
            "parse_status": "PARSED",
        },
    )
    summary = result.to_run_step_summary()

    assert result.handoff_status == DataRoomIngestionHandoffStatus.IN_MEMORY_FALLBACK
    assert summary["handoff_status"] == "in_memory_fallback"
    assert summary["in_memory_fallback_file_count"] == 2


def _inventory_package_for_mixed_room(tmp_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    (tmp_path / "Finance").mkdir()
    (tmp_path / "Finance" / "Model.xlsx").write_bytes(
        create_test_xlsx({"Sheet1": [["ARR was $5M"]]})
    )
    (tmp_path / "Media").mkdir()
    (tmp_path / "Media" / "Demo.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (tmp_path / "Scans").mkdir()
    (tmp_path / "Scans" / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "overview.html").write_text("<html>secret</html>", encoding="utf-8")
    (tmp_path / "Broken").mkdir()
    (tmp_path / "Broken" / "corrupt.pdf").write_bytes(b"%PDF-corrupt")

    _result, packages, corpus = InMemoryRunDataRoomInventoryPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        root_path=tmp_path,
    )
    return packages[0], corpus
