"""Slice77 Task 1: DataRoomPackage / DataRoomPackageFile models and enums.

RED-first tests for the durable data-room package header + per-file ledger
models. Package is keyed by package_id only (no name). The file ledger stores a
path_hash + safe extension only — raw paths, filenames, storage keys, and
content never reach the safe/public dicts.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import ValidationError

import idis
from idis.models.data_room_package import (
    DataRoomFileStatus,
    DataRoomPackage,
    DataRoomPackageFile,
    DataRoomPackageStatus,
)
from idis.models.document import ParseStatus
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus

TENANT = UUID("11111111-1111-1111-1111-111111111111")
DEAL = UUID("22222222-2222-2222-2222-222222222222")
PACKAGE = UUID("33333333-3333-3333-3333-333333333333")
FILE_ENTRY = UUID("44444444-4444-4444-4444-444444444444")
DOC = UUID("55555555-5555-5555-5555-555555555555")
DOCUMENT = UUID("66666666-6666-6666-6666-666666666666")
_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)
_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _package(**over: Any) -> DataRoomPackage:
    base: dict[str, Any] = {
        "package_id": PACKAGE,
        "tenant_id": TENANT,
        "deal_id": DEAL,
        "status": DataRoomPackageStatus.OPEN,
        "created_by_actor_id": "actor-7",
        "created_by_actor_type": "HUMAN",
        "file_count": 3,
        "counts_by_status": {"supported": 2, "blocked": 1},
        "counts_by_reason_code": {"ocr_required": 1},
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(over)
    return DataRoomPackage(**base)


def _file(**over: Any) -> DataRoomPackageFile:
    base: dict[str, Any] = {
        "file_entry_id": FILE_ENTRY,
        "tenant_id": TENANT,
        "package_id": PACKAGE,
        "deal_id": DEAL,
        "sequence": 1,
        "path_hash": _SHA_A,
        "extension": "pdf",
        "sha256": _SHA_B,
        "file_status": DataRoomFileStatus.DEFERRED,
        "support_status": DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY,
        "triage_status": DocumentTriageStatus.OCR_REQUIRED,
        "parse_status": ParseStatus.PENDING,
        "reason_codes": ["ocr_required"],
        "error_codes": [],
        "doc_id": DOC,
        "document_id": DOCUMENT,
        "created_at": _NOW,
    }
    base.update(over)
    return DataRoomPackageFile(**base)


def test_data_room_package_roundtrips_with_safe_aggregate_counts() -> None:
    pkg = _package()
    restored = DataRoomPackage.model_validate(pkg.model_dump(mode="json"))

    assert restored == pkg
    assert restored.package_id == PACKAGE
    assert restored.tenant_id == TENANT
    assert restored.deal_id == DEAL
    assert restored.status is DataRoomPackageStatus.OPEN
    assert restored.created_by_actor_id == "actor-7"
    assert restored.created_by_actor_type == "HUMAN"
    assert restored.file_count == 3
    assert restored.counts_by_status == {"supported": 2, "blocked": 1}
    assert restored.counts_by_reason_code == {"ocr_required": 1}


def test_data_room_package_file_roundtrips_and_sorts_unique_codes() -> None:
    f = _file(
        reason_codes=["ocr_required", "ocr_required", "file_too_large"],
        error_codes=["ocr_no_text_extracted", "ocr_failed", "ocr_failed"],
    )

    # sorted + unique
    assert f.reason_codes == ["file_too_large", "ocr_required"]
    assert f.error_codes == ["ocr_failed", "ocr_no_text_extracted"]

    restored = DataRoomPackageFile.model_validate(f.model_dump(mode="json"))
    assert restored == f
    assert restored.path_hash == _SHA_A
    assert restored.extension == "pdf"
    assert restored.sha256 == _SHA_B
    assert restored.file_status is DataRoomFileStatus.DEFERRED
    assert restored.support_status is DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY
    assert restored.triage_status is DocumentTriageStatus.OCR_REQUIRED
    assert restored.parse_status is ParseStatus.PENDING
    assert restored.doc_id == DOC
    assert restored.document_id == DOCUMENT


def test_safe_dicts_exclude_internal_and_raw_fields() -> None:
    pkg = _package(manifest_uri="deals/22/packages/33/manifest.json", metadata={"x": 1})
    f = _file(storage_uri="deals/22/artifacts/aaaa/secret-board-pack.pdf")

    pkg_safe = pkg.safe_dict()
    file_safe = f.safe_dict()
    encoded = str(pkg_safe) + str(file_safe)

    # Internal-only fields never appear in the safe payloads.
    for forbidden in (
        "manifest_uri",
        "storage_uri",
        "object_key",
        "tenant_id",
        "text_excerpt",
        "secret-board-pack",
        "deals/22",
        "created_by_actor_id",
    ):
        assert forbidden not in encoded

    # Safe payloads keep only the documented whitelist keys.
    assert set(pkg_safe) == {
        "package_id",
        "deal_id",
        "status",
        "file_count",
        "counts_by_status",
        "counts_by_reason_code",
        "created_at",
    }
    assert set(file_safe) == {
        "file_entry_id",
        "path_hash",
        "extension",
        "file_status",
        "support_status",
        "triage_status",
        "parse_status",
        "reason_codes",
        "error_codes",
        "sha256",
        "doc_id",
        "document_id",
    }


def test_validators_reject_path_like_fields_and_invalid_hashes() -> None:
    with pytest.raises(ValidationError):
        _file(path_hash="not-a-valid-hash")
    with pytest.raises(ValidationError):
        _file(extension="../secret/board-pack.pdf")
    with pytest.raises(ValidationError):
        _file(extension="application/pdf")
    with pytest.raises(ValidationError):
        _file(sha256="xyz")
    with pytest.raises(ValidationError):
        _file(reason_codes=["deals/secret/board-pack"])


def test_enums_accept_only_planned_values() -> None:
    assert {s.value for s in DataRoomPackageStatus} == {"OPEN", "SEALED"}
    assert {s.value for s in DataRoomFileStatus} == {"supported", "deferred", "blocked"}

    with pytest.raises(ValueError):
        DataRoomPackageStatus("ARCHIVED")
    with pytest.raises(ValueError):
        DataRoomFileStatus("partial")
    with pytest.raises(ValidationError):
        _package(created_by_actor_type="ROBOT")


# --- Task 2: migration 0020 + data-room package repositories ---

TENANT_B = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
DEAL_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

_MIGRATION_PATH = (
    Path(idis.__file__).resolve().parent
    / "persistence"
    / "migrations"
    / "versions"
    / "0020_data_room_packages_and_files.py"
)


@pytest.fixture(autouse=True)
def _clear_data_room_stores() -> Any:
    """Isolate the module-global in-memory data-room stores (tolerant during RED)."""
    try:
        from idis.persistence.repositories.data_room_packages import (
            clear_data_room_packages_store,
        )
    except ImportError:
        yield
        return
    clear_data_room_packages_store()
    yield
    clear_data_room_packages_store()


class _RecordingResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


class _RecordingConn:
    """Minimal SQLAlchemy-connection stand-in that records executed SQL + params."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.sql: list[str] = []
        self.params: list[dict[str, Any]] = []
        self._rows = rows or []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _RecordingResult:
        self.sql.append(str(statement))
        self.params.append(dict(params or {}))
        return _RecordingResult(self._rows)


def test_migration_0020_creates_both_tables_rls_and_safe_downgrade() -> None:
    source = _MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'revision = "0020"' in source
    assert 'down_revision = "0019"' in source
    # Both tables, FK, cascade, unique, checks, indexes.
    assert "CREATE TABLE IF NOT EXISTS data_room_packages" in source
    assert "CREATE TABLE IF NOT EXISTS data_room_package_files" in source
    assert "REFERENCES deals(deal_id)" in source
    assert "REFERENCES data_room_packages(package_id) ON DELETE CASCADE" in source
    assert "UNIQUE (tenant_id, package_id, path_hash)" in source
    assert "CHECK (status IN ('OPEN', 'SEALED'))" in source
    assert "created_by_actor_type IS NULL" in source
    assert "created_by_actor_type IN ('HUMAN', 'SERVICE')" in source
    assert "metadata JSONB NOT NULL DEFAULT '{}'::jsonb" in source
    assert "reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb" in source
    assert "error_codes JSONB NOT NULL DEFAULT '[]'::jsonb" in source
    # Canonical RLS policy pattern (copied from 0010).
    assert "ALTER TABLE data_room_packages ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE data_room_package_files ENABLE ROW LEVEL SECURITY" in source
    # FORCE so even the table-owner connection cannot bypass tenant RLS.
    assert "ALTER TABLE data_room_packages FORCE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE data_room_package_files FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY tenant_isolation_data_room_packages ON data_room_packages" in source
    assert (
        "CREATE POLICY tenant_isolation_data_room_package_files ON data_room_package_files"
        in source
    )
    assert "NULLIF(current_setting('idis.tenant_id', true), '')::uuid" in source
    # Indexes.
    assert "idx_data_room_packages_tenant" in source
    assert "idx_data_room_packages_tenant_deal" in source
    assert "idx_data_room_package_files_tenant_package" in source
    assert "idx_data_room_package_files_tenant_deal" in source
    # Safe downgrade drops both tables with CASCADE.
    assert "DROP TABLE IF EXISTS data_room_package_files CASCADE" in source
    assert "DROP TABLE IF EXISTS data_room_packages CASCADE" in source


def test_inmemory_package_create_get_list_scoped_by_tenant_and_deal() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    repo.create_package(_package())

    got = repo.get_package(str(PACKAGE), str(DEAL))
    assert got is not None
    assert got.package_id == PACKAGE
    assert got.tenant_id == TENANT
    assert got.deal_id == DEAL
    assert got.status is DataRoomPackageStatus.OPEN

    listed = repo.list_packages_by_deal(str(DEAL))
    assert [p.package_id for p in listed] == [PACKAGE]


def test_inmemory_files_add_list_dedupe_and_code_roundtrip() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    repo.create_package(_package())

    file_a = _file(file_entry_id=FILE_ENTRY, path_hash="a" * 64, sequence=1)
    file_b = _file(
        file_entry_id=UUID("77777777-7777-7777-7777-777777777777"),
        path_hash="c" * 64,
        sequence=2,
        reason_codes=["unsupported_format", "unsupported_format"],
        error_codes=["corrupted_file"],
    )
    repo.add_file(file_a)
    repo.add_file(file_b)
    # Duplicate path_hash (different entry id) must not create a second row.
    repo.add_file(
        _file(
            file_entry_id=UUID("88888888-8888-8888-8888-888888888888"),
            path_hash="a" * 64,
            sequence=3,
        )
    )

    files = repo.list_files_by_package(str(PACKAGE), str(DEAL))
    assert [f.path_hash for f in files] == ["a" * 64, "c" * 64]  # sorted by sequence, deduped
    assert files[1].reason_codes == ["unsupported_format"]  # sorted + unique list roundtrip
    assert files[1].error_codes == ["corrupted_file"]


def test_cross_tenant_and_cross_deal_reads_are_isolated() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    repo.create_package(_package())
    repo.add_file(_file())

    other_tenant = InMemoryDataRoomPackagesRepository(str(TENANT_B))
    assert other_tenant.get_package(str(PACKAGE), str(DEAL)) is None
    assert other_tenant.list_packages_by_deal(str(DEAL)) == []
    assert other_tenant.list_files_by_package(str(PACKAGE), str(DEAL)) == []

    # Same tenant, wrong deal → masked None / empty (no cross-deal oracle).
    assert repo.get_package(str(PACKAGE), str(DEAL_B)) is None
    assert repo.list_packages_by_deal(str(DEAL_B)) == []


def test_postgres_repo_sql_shape() -> None:
    from idis.persistence.repositories.data_room_packages import (
        PostgresDataRoomPackagesRepository,
    )

    conn = _RecordingConn()
    with patch(
        "idis.persistence.repositories.data_room_packages.set_tenant_local"
    ) as set_tenant_local:
        repo = PostgresDataRoomPackagesRepository(conn, str(TENANT))
        set_tenant_local.assert_called_once_with(conn, str(TENANT))
        repo.create_package(_package())
        repo.add_file(_file())
        repo.get_package(str(PACKAGE), str(DEAL))
        repo.list_packages_by_deal(str(DEAL))
        repo.list_files_by_package(str(PACKAGE), str(DEAL))

    all_sql = "\n".join(conn.sql)
    assert "INSERT INTO data_room_packages" in all_sql
    assert "CAST(:metadata AS JSONB)" in all_sql
    assert "INSERT INTO data_room_package_files" in all_sql
    assert "CAST(:reason_codes AS JSONB)" in all_sql
    assert "CAST(:error_codes AS JSONB)" in all_sql
    assert "ON CONFLICT" in all_sql
    assert "DO NOTHING" in all_sql
    assert "FROM data_room_packages" in all_sql
    assert "FROM data_room_package_files" in all_sql
    assert "deal_id = :deal_id" in all_sql
    assert "package_id = :package_id" in all_sql


def test_factory_selects_inmemory_or_postgres_backend() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
        PostgresDataRoomPackagesRepository,
        get_data_room_packages_repository,
    )

    assert isinstance(
        get_data_room_packages_repository(None, str(TENANT)),
        InMemoryDataRoomPackagesRepository,
    )

    conn = _RecordingConn()
    with (
        patch(
            "idis.persistence.repositories.data_room_packages.is_postgres_configured",
            return_value=True,
        ),
        patch("idis.persistence.repositories.data_room_packages.set_tenant_local"),
    ):
        repo = get_data_room_packages_repository(conn, str(TENANT))
    assert isinstance(repo, PostgresDataRoomPackagesRepository)


def test_inmemory_add_file_duplicate_returns_existing_persisted_row() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    repo.create_package(_package())
    original = repo.add_file(_file(file_entry_id=FILE_ENTRY, path_hash="a" * 64, sequence=1))
    duplicate = repo.add_file(
        _file(
            file_entry_id=UUID("88888888-8888-8888-8888-888888888888"),
            path_hash="a" * 64,
            sequence=9,
        )
    )

    # A duplicate path_hash returns the originally-persisted row, not the attempted new one.
    assert duplicate.file_entry_id == original.file_entry_id == FILE_ENTRY
    assert duplicate.sequence == original.sequence == 1


def test_postgres_add_file_duplicate_returns_persisted_existing_row() -> None:
    from idis.persistence.repositories.data_room_packages import (
        PostgresDataRoomPackagesRepository,
    )

    existing_entry_id = UUID("99999999-9999-9999-9999-999999999999")
    existing_row = {
        "file_entry_id": str(existing_entry_id),
        "tenant_id": str(TENANT),
        "package_id": str(PACKAGE),
        "deal_id": str(DEAL),
        "sequence": 1,
        "path_hash": "a" * 64,
        "extension": "pdf",
        "sha256": "b" * 64,
        "file_status": "deferred",
        "support_status": "scanned_or_image_only",
        "triage_status": "ocr_required",
        "parse_status": "PENDING",
        "reason_codes": ["ocr_required"],
        "error_codes": [],
        "doc_id": str(DOC),
        "document_id": str(DOCUMENT),
        "storage_uri": None,
        "created_at": _NOW,
    }
    conn = _RecordingConn(rows=[existing_row])
    with patch("idis.persistence.repositories.data_room_packages.set_tenant_local"):
        repo = PostgresDataRoomPackagesRepository(conn, str(TENANT))
        returned = repo.add_file(
            _file(
                file_entry_id=UUID("88888888-8888-8888-8888-888888888888"),
                path_hash="a" * 64,
                sequence=9,
            )
        )

    # Must return the persisted (existing) row, not the attempted duplicate.
    assert returned.file_entry_id == existing_entry_id
    assert returned.sequence == 1
    # add_file must do a conflict-safe INSERT and then SELECT the persisted row back.
    all_sql = "\n".join(conn.sql)
    assert "ON CONFLICT" in all_sql
    assert "DO NOTHING" in all_sql
    assert all_sql.count("data_room_package_files") >= 2
    assert "SELECT" in all_sql


# --- Task 3: package service (create-from-document_ids, triage, grouping) ---

DOC1 = "d0c00001-0000-0000-0000-000000000001"
DOC2 = "d0c00002-0000-0000-0000-000000000002"
DOC3 = "d0c00003-0000-0000-0000-000000000003"


def _corpus_doc(
    document_id: str,
    *,
    doc_type: str = "PDF",
    parse_status: str = "PARSED",
    support_status: str = "partially_supported",
    triage_status: str = "partial",
    reason_codes: tuple[str, ...] = ("pdf_text_only_no_ocr",),
    error_codes: tuple[str, ...] = (),
    sha256: str | None = _SHA_B,
    uri: str | None = None,
) -> dict[str, Any]:
    """A deal preflight-corpus doc dict with raw name/uri that must never leak."""
    return {
        "document_id": document_id,
        "doc_id": document_id,
        "doc_type": doc_type,
        "parse_status": parse_status,
        "document_name": "SECRET-board-pack.pdf",
        "sha256": sha256,
        "uri": uri or f"deals/SECRET-DEAL/artifacts/{document_id}/SECRET-board-pack.pdf",
        "metadata": {
            "parser_support_status": support_status,
            "parser_triage_status": triage_status,
            "parser_reason_codes": list(reason_codes),
            "parse_error_codes": list(error_codes),
        },
        "spans": [],
    }


def _create(repo: Any, document_ids: list[str], documents: list[dict[str, Any]]) -> dict[str, Any]:
    from idis.services.data_room.package_service import create_data_room_package

    return create_data_room_package(
        repo=repo,
        tenant_id=str(TENANT),
        deal_id=str(DEAL),
        created_by_actor_id="actor-7",
        created_by_actor_type="HUMAN",
        document_ids=document_ids,
        documents=documents,
    )


def test_service_creates_package_with_one_ledger_row_per_unique_file() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    corpus = [_corpus_doc(DOC1, uri="deals/x/a.pdf"), _corpus_doc(DOC2, uri="deals/x/b.pdf")]
    summary = _create(repo, [DOC1, DOC2], corpus)

    files = repo.list_files_by_package(summary["package_id"], str(DEAL))
    assert len(files) == 2
    assert summary["file_count"] == 2
    assert summary["status"] == "OPEN"
    assert summary["deal_id"] == str(DEAL)


def test_service_blocks_missing_or_foreign_document_ids_without_leak() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )
    from idis.services.data_room.package_service import DataRoomPackageError

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    foreign = "d0c0ffff-0000-0000-0000-00000000ffff"
    with pytest.raises(DataRoomPackageError) as exc:
        _create(repo, [DOC1, foreign], [_corpus_doc(DOC1)])

    assert exc.value.reason_code == "INVALID_DOCUMENT_SELECTION"
    # Masked: does not echo which id / whether it exists elsewhere; no package created.
    assert foreign not in str(exc.value)
    assert repo.list_packages_by_deal(str(DEAL)) == []


def test_supported_deferred_blocked_documents_map_to_deterministic_status() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    corpus = [
        _corpus_doc(
            DOC1,
            doc_type="DOCX",
            parse_status="PARSED",
            support_status="supported",
            triage_status="ready",
            reason_codes=("docx_text_parser_available",),
        ),
        _corpus_doc(
            DOC2,
            parse_status="PENDING",
            support_status="scanned_or_image_only",
            triage_status="ocr_required",
            reason_codes=("ocr_required",),
        ),
        _corpus_doc(
            DOC3,
            support_status="unsupported",
            triage_status="unsupported_source",
            reason_codes=("unsupported_format",),
        ),
    ]
    summary = _create(repo, [DOC1, DOC2, DOC3], corpus)
    by_doc = {
        str(f.document_id): f for f in repo.list_files_by_package(summary["package_id"], str(DEAL))
    }

    assert by_doc[DOC1].file_status.value == "supported"
    assert by_doc[DOC1].reason_codes == ["docx_text_parser_available"]
    assert by_doc[DOC2].file_status.value == "deferred"
    assert by_doc[DOC2].reason_codes == ["ocr_required"]
    assert by_doc[DOC3].file_status.value == "blocked"
    assert by_doc[DOC3].reason_codes == ["unsupported_format"]
    assert summary["counts_by_status"] == {"supported": 1, "deferred": 1, "blocked": 1}


def test_text_parser_supported_document_rolls_up_to_supported_ledger() -> None:
    """Slice78 Task 2: an HTML/TXT doc (text_parser_available) packages as supported."""
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    corpus = [
        _corpus_doc(
            DOC1,
            doc_type="HTML",
            parse_status="PARSED",
            support_status="supported",
            triage_status="ready",
            reason_codes=("text_parser_available",),
        )
    ]
    summary = _create(repo, [DOC1], corpus)

    files = repo.list_files_by_package(summary["package_id"], str(DEAL))
    assert len(files) == 1
    entry = files[0]
    assert entry.file_status.value == "supported"
    assert entry.support_status.value == "supported"
    assert entry.triage_status.value == "ready"
    assert "text_parser_available" in entry.reason_codes
    assert summary["counts_by_status"] == {"supported": 1}


def test_unsupported_csv_style_document_rolls_up_to_blocked_ledger() -> None:
    """Slice78 Task 3: an unsupported (CSV-style) doc packages as a visible blocker."""
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    corpus = [
        _corpus_doc(
            DOC1,
            doc_type="CSV",
            parse_status="FAILED",
            support_status="unsupported",
            triage_status="unsupported_source",
            reason_codes=("unsupported_format",),
        )
    ]
    summary = _create(repo, [DOC1], corpus)

    files = repo.list_files_by_package(summary["package_id"], str(DEAL))
    assert len(files) == 1
    entry = files[0]
    assert entry.file_status.value == "blocked"
    assert entry.support_status.value == "unsupported"
    assert entry.triage_status.value == "unsupported_source"
    assert entry.reason_codes == ["unsupported_format"]
    assert summary["counts_by_status"] == {"blocked": 1}


def test_duplicate_document_ids_and_path_hashes_do_not_duplicate_rows() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    # Duplicate document_ids collapse to one row each.
    summary = _create(
        repo,
        [DOC1, DOC1, DOC2],
        [_corpus_doc(DOC1, uri="deals/x/a.pdf"), _corpus_doc(DOC2, uri="deals/x/b.pdf")],
    )
    assert len(repo.list_files_by_package(summary["package_id"], str(DEAL))) == 2

    # Distinct document_ids sharing the same uri (same path_hash) collapse to one row.
    summary2 = _create(
        repo,
        [DOC1, DOC3],
        [_corpus_doc(DOC1, uri="deals/x/same.pdf"), _corpus_doc(DOC3, uri="deals/x/same.pdf")],
    )
    assert len(repo.list_files_by_package(summary2["package_id"], str(DEAL))) == 1


def test_returned_summary_and_ledger_are_leakage_safe() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    summary = _create(repo, [DOC1], [_corpus_doc(DOC1)])
    files = repo.list_files_by_package(summary["package_id"], str(DEAL))
    encoded = json.dumps(summary) + json.dumps([f.safe_dict() for f in files])

    for forbidden in (
        "SECRET-board-pack",
        "SECRET-DEAL",
        "storage_uri",
        "manifest_uri",
        "object_key",
        "document_name",
        "text_excerpt",
        "://",
    ):
        assert forbidden not in encoded


def test_service_does_not_execute_parsers_or_providers() -> None:
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    with patch("idis.parsers.registry.parse_bytes", side_effect=AssertionError("no live parse")):
        summary = _create(repo, [DOC1], [_corpus_doc(DOC1)])
    assert summary["file_count"] == 1


# --- Task 4: public API routes + OpenAPI + RBAC/audit wiring ---

_OPENAPI_PATH = Path(idis.__file__).resolve().parents[2] / "openapi" / "IDIS_OpenAPI_v6_3.yaml"


def _dr_api_keys(tenant_id: str) -> str:
    common = {
        "tenant_id": tenant_id,
        "name": "DR Tenant",
        "timezone": "UTC",
        "data_region": "us-east-1",
    }
    return json.dumps(
        {
            "dr-analyst-key": {**common, "actor_id": "analyst-1", "roles": ["ANALYST"]},
            "dr-auditor-key": {**common, "actor_id": "auditor-1", "roles": ["AUDITOR"]},
        }
    )


def _setup_data_room_api(monkeypatch: Any, corpus: list[dict[str, Any]]) -> tuple[Any, str, Any]:
    from fastapi.testclient import TestClient

    from idis.api.main import create_app
    from idis.audit.sink import InMemoryAuditSink

    tenant_id = str(uuid.uuid4())
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _dr_api_keys(tenant_id))
    sink = InMemoryAuditSink()
    app = create_app(audit_sink=sink, service_region="us-east-1")
    app.state.deal_documents = {}
    client = TestClient(app, raise_server_exceptions=False)

    create = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": "dr-analyst-key", "Content-Type": "application/json"},
        content=json.dumps({"name": "DR Deal", "company_name": "Acme"}),
    )
    assert create.status_code == 201, create.text
    deal_id = create.json()["deal_id"]
    app.state.deal_documents[deal_id] = corpus
    return client, deal_id, sink


_ANALYST = {"X-IDIS-API-Key": "dr-analyst-key", "Content-Type": "application/json"}
_AUDITOR = {"X-IDIS-API-Key": "dr-auditor-key", "Content-Type": "application/json"}


def test_api_create_returns_201_safe_ref_and_creates_ledger_rows(monkeypatch: Any) -> None:
    client, deal_id, _sink = _setup_data_room_api(
        monkeypatch, [_corpus_doc(DOC1), _corpus_doc(DOC2)]
    )
    resp = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [DOC1, DOC2]}),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {
        "package_id",
        "deal_id",
        "status",
        "file_count",
        "counts_by_status",
        "counts_by_reason_code",
        "created_at",
    }
    assert body["deal_id"] == deal_id
    assert body["file_count"] == 2


def test_api_list_returns_refs_scoped_to_deal(monkeypatch: Any) -> None:
    client, deal_id, _sink = _setup_data_room_api(monkeypatch, [_corpus_doc(DOC1)])
    created = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [DOC1]}),
    ).json()

    resp = client.get(f"/v1/deals/{deal_id}/data-room-packages", headers=_ANALYST)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [p["package_id"] for p in items] == [created["package_id"]]


def test_api_get_returns_safe_record_with_file_ledger(monkeypatch: Any) -> None:
    client, deal_id, _sink = _setup_data_room_api(
        monkeypatch, [_corpus_doc(DOC1), _corpus_doc(DOC2)]
    )
    created = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [DOC1, DOC2]}),
    ).json()

    resp = client.get(
        f"/v1/deals/{deal_id}/data-room-packages/{created['package_id']}", headers=_ANALYST
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()
    assert record["package_id"] == created["package_id"]
    assert len(record["files"]) == 2
    file_keys = set(record["files"][0])
    assert "path_hash" in file_keys
    assert "storage_uri" not in file_keys


def test_api_create_from_supported_fixture_marks_file_supported(monkeypatch: Any) -> None:
    """Acceptance: public API creates a package from a SUPPORTED generated descriptor.

    Locks the master-plan acceptance ("create a data-room package from supported
    generated fixtures") at the API boundary: a supported+ready+PARSED corpus doc
    rolls up to file_status 'supported' end-to-end through create + get.
    """
    supported = _corpus_doc(
        DOC1,
        doc_type="DOCX",
        support_status="supported",
        triage_status="ready",
        parse_status="PARSED",
        reason_codes=("docx_text_parser_available",),
    )
    client, deal_id, _sink = _setup_data_room_api(monkeypatch, [supported])

    create = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [DOC1]}),
    )
    assert create.status_code == 201, create.text
    assert create.json()["counts_by_status"] == {"supported": 1}

    record = client.get(
        f"/v1/deals/{deal_id}/data-room-packages/{create.json()['package_id']}",
        headers=_ANALYST,
    )
    assert record.status_code == 200, record.text
    body = record.json()
    assert body["counts_by_status"] == {"supported": 1}
    entry = body["files"][0]
    assert entry["file_status"] == "supported"
    assert entry["support_status"] == "supported"
    assert entry["triage_status"] == "ready"
    assert entry["parse_status"] == "PARSED"


def test_api_get_masks_cross_deal_and_unknown_package_as_404(monkeypatch: Any) -> None:
    client, deal_id, _sink = _setup_data_room_api(monkeypatch, [_corpus_doc(DOC1)])
    created = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [DOC1]}),
    ).json()

    other_deal = client.post(
        "/v1/deals",
        headers=_ANALYST,
        content=json.dumps({"name": "Other", "company_name": "Other"}),
    ).json()["deal_id"]

    cross = client.get(
        f"/v1/deals/{other_deal}/data-room-packages/{created['package_id']}", headers=_ANALYST
    )
    unknown = client.get(
        f"/v1/deals/{deal_id}/data-room-packages/d0c0ffff-0000-0000-0000-00000000ffff",
        headers=_ANALYST,
    )
    assert cross.status_code == 404
    assert unknown.status_code == 404
    assert cross.json()["code"] == "DATA_ROOM_PACKAGE_NOT_FOUND"
    assert unknown.json()["code"] == "DATA_ROOM_PACKAGE_NOT_FOUND"


def test_api_auditor_cannot_create_but_can_read(monkeypatch: Any) -> None:
    client, deal_id, _sink = _setup_data_room_api(monkeypatch, [_corpus_doc(DOC1)])
    client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [DOC1]}),
    )

    denied = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_AUDITOR,
        content=json.dumps({"document_ids": [DOC1]}),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "RBAC_DENIED"

    read = client.get(f"/v1/deals/{deal_id}/data-room-packages", headers=_AUDITOR)
    assert read.status_code == 200


def test_api_create_emits_safe_audit_event(monkeypatch: Any) -> None:
    client, deal_id, sink = _setup_data_room_api(monkeypatch, [_corpus_doc(DOC1)])
    created = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [DOC1]}),
    ).json()

    events = [e for e in sink.events if e.get("event_type") == "data_room_package.created"]
    assert len(events) == 1
    assert events[0]["resource"]["resource_type"] == "data_room_package"
    assert events[0]["resource"]["resource_id"] == created["package_id"]
    encoded = json.dumps(sink.events)
    for forbidden in ("SECRET-board-pack", "SECRET-DEAL", "storage_uri", "manifest_uri"):
        assert forbidden not in encoded


def test_api_invalid_document_ids_blocked_safely_without_creating_package(monkeypatch: Any) -> None:
    client, deal_id, _sink = _setup_data_room_api(monkeypatch, [_corpus_doc(DOC1)])
    foreign = "d0c0ffff-0000-0000-0000-00000000ffff"
    resp = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [foreign]}),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_DOCUMENT_SELECTION"
    assert foreign not in resp.text

    listed = client.get(f"/v1/deals/{deal_id}/data-room-packages", headers=_ANALYST)
    assert listed.json()["items"] == []


def test_openapi_declares_deal_scoped_paths_and_rejects_unsafe_fields(monkeypatch: Any) -> None:
    import yaml

    spec = yaml.safe_load(_OPENAPI_PATH.read_text(encoding="utf-8"))
    paths = spec["paths"]
    assert "/v1/deals/{dealId}/data-room-packages" in paths
    assert "/v1/deals/{dealId}/data-room-packages/{packageId}" in paths
    assert paths["/v1/deals/{dealId}/data-room-packages"]["post"]["operationId"] == (
        "createDataRoomPackage"
    )
    assert paths["/v1/deals/{dealId}/data-room-packages"]["get"]["operationId"] == (
        "listDataRoomPackages"
    )
    assert paths["/v1/deals/{dealId}/data-room-packages/{packageId}"]["get"]["operationId"] == (
        "getDataRoomPackage"
    )
    req_schema = spec["components"]["schemas"]["CreateDataRoomPackageRequest"]
    assert req_schema["additionalProperties"] is False
    assert req_schema["required"] == ["document_ids"]

    # Runtime: a path/object-key field is rejected by the route's strict request model.
    client, deal_id, _sink = _setup_data_room_api(monkeypatch, [_corpus_doc(DOC1)])
    resp = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [DOC1], "uri": "deals/x/secret.pdf"}),
    )
    assert resp.status_code == 400


# --- Task 5: redaction/leakage safety regression guard (T7) ---


def test_api_get_record_and_list_responses_are_leakage_safe(monkeypatch: Any) -> None:
    """T7 regression guard: public GET-record + LIST responses leak no raw markers.

    Green-on-arrival by design — Tasks 1/3/4 already redact via ``safe_dict()``
    whitelists, so this cannot fail red against the current code. It locks the
    API-layer behaviour (incl. the GET ``files`` ledger array, the richest leak
    surface) so a future change cannot reintroduce a storage/path/name leak.
    """
    client, deal_id, _sink = _setup_data_room_api(
        monkeypatch, [_corpus_doc(DOC1), _corpus_doc(DOC2)]
    )
    created = client.post(
        f"/v1/deals/{deal_id}/data-room-packages",
        headers=_ANALYST,
        content=json.dumps({"document_ids": [DOC1, DOC2]}),
    ).json()

    record = client.get(
        f"/v1/deals/{deal_id}/data-room-packages/{created['package_id']}", headers=_ANALYST
    )
    listing = client.get(f"/v1/deals/{deal_id}/data-room-packages", headers=_ANALYST)
    assert record.status_code == 200, record.text
    assert listing.status_code == 200, listing.text

    # Seeded raw markers (from _corpus_doc) must not survive into either response.
    encoded = json.dumps(record.json()) + json.dumps(listing.json())
    for forbidden in (
        "storage_uri",
        "manifest_uri",
        "object_key",
        "document_name",
        "text_excerpt",
        "://",
        "SECRET-board-pack",
        "SECRET-DEAL",
    ):
        assert forbidden not in encoded

    # File-ledger rows expose only the documented safe whitelist keys.
    safe_file_keys = {
        "file_entry_id",
        "path_hash",
        "extension",
        "file_status",
        "support_status",
        "triage_status",
        "parse_status",
        "reason_codes",
        "error_codes",
        "sha256",
        "doc_id",
        "document_id",
    }
    files = record.json()["files"]
    assert len(files) == 2
    for entry in files:
        assert set(entry) <= safe_file_keys


# --- Task 6: tenant/RLS/deal-scoping hardening + SQL-shape coverage ---


def test_list_files_by_package_is_deal_scoped_in_memory() -> None:
    """Defense-in-depth: file-ledger reads require the owning deal, not package_id alone.

    RLS isolates by tenant, not by deal; without a deal filter a same-tenant caller
    who guessed a package_id from another deal could read its file ledger. So
    list_files_by_package must be deal-scoped, exactly like get_package.
    """
    from idis.persistence.repositories.data_room_packages import (
        InMemoryDataRoomPackagesRepository,
    )

    repo = InMemoryDataRoomPackagesRepository(str(TENANT))
    repo.create_package(_package())
    repo.add_file(_file())

    # Owning deal returns the ledger.
    owning = repo.list_files_by_package(str(PACKAGE), str(DEAL))
    assert [f.path_hash for f in owning] == [_SHA_A]
    # Same tenant, wrong deal -> empty (no cross-deal file oracle).
    assert repo.list_files_by_package(str(PACKAGE), str(DEAL_B)) == []
    # Cross-tenant -> empty.
    other_tenant = InMemoryDataRoomPackagesRepository(str(TENANT_B))
    assert other_tenant.list_files_by_package(str(PACKAGE), str(DEAL)) == []


def test_postgres_list_files_by_package_sql_is_deal_scoped() -> None:
    """The Postgres file-ledger SELECT is scoped by package_id AND deal_id (RLS adds tenant)."""
    from idis.persistence.repositories.data_room_packages import (
        PostgresDataRoomPackagesRepository,
    )

    conn = _RecordingConn()
    with patch("idis.persistence.repositories.data_room_packages.set_tenant_local"):
        repo = PostgresDataRoomPackagesRepository(conn, str(TENANT))
        repo.list_files_by_package(str(PACKAGE), str(DEAL))

    file_select = next(s for s in conn.sql if "SELECT" in s and "FROM data_room_package_files" in s)
    assert "package_id = :package_id" in file_select
    assert "deal_id = :deal_id" in file_select
    assert conn.params[-1] == {"package_id": str(PACKAGE), "deal_id": str(DEAL)}


def test_migration_0020_rls_policy_is_canonical_for_both_tables() -> None:
    """Both new tables get the canonical fail-closed tenant_isolation policy."""
    source = _MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'down_revision = "0019"' in source
    canonical = "NULLIF(current_setting('idis.tenant_id', true), '')::uuid"
    # Present once per table (packages + files), fail-closed when the GUC is unset.
    assert source.count(canonical) == 2
    assert source.count("ENABLE ROW LEVEL SECURITY") == 2
    assert source.count("FORCE ROW LEVEL SECURITY") == 2
    assert source.count("current_setting('idis.tenant_id', true), '') IS NOT NULL") == 2


def test_migration_0020_stores_no_raw_path_or_filename_columns() -> None:
    """R3 redact-only: only path_hash + safe extension; no raw path/name/retention column."""
    source = _MIGRATION_PATH.read_text(encoding="utf-8")

    assert "path_hash VARCHAR(64) NOT NULL" in source
    assert "extension VARCHAR(16)" in source
    # Assert no raw-path/filename/retention *column* is declared. Match a column
    # declaration ("<name> <SQL type>"), not prose -- the docstring legitimately
    # says "no raw paths or filenames", which a bare substring check would flag.
    sql_type = r"(?:VARCHAR|TEXT|UUID|INTEGER|JSONB|TIMESTAMPTZ|BOOLEAN|BIGINT|NUMERIC|SERIAL)"
    for forbidden in (
        "folder_path",
        "relative_path",
        "raw_path",
        "file_path",
        "file_name",
        "filename",
        "original_name",
        "display_name",
        "retain_raw_path",
        "raw_path_retention",
    ):
        assert not re.search(rf"\b{re.escape(forbidden)}\b\s+{sql_type}", source), (
            f"raw column must not be declared in migration 0020: {forbidden}"
        )


# --- Task 7: private real_example safe inventory hook ---


def _write_private_inventory_tree(root: Path) -> None:
    """Synthetic 'private' tree whose raw paths/names/content must never leak."""
    (root / "Finance").mkdir(parents=True)
    (root / "Finance" / "SECRET-board-pack.pdf").write_bytes(b"%PDF-1.4 secret_customer")
    (root / "Media").mkdir()
    (root / "Media" / "founder-demo.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (root / "Scans").mkdir()
    (root / "Scans" / "passport-scan.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "Notes").mkdir()
    (root / "Notes" / "cap-table.html").write_text("Nexx_founder_roster", encoding="utf-8")


_PRIVATE_MARKERS = (
    "SECRET-board-pack",
    "secret_customer",
    "founder-demo",
    "passport-scan",
    "cap-table",
    "Nexx_founder_roster",
    "storage_uri",
    "manifest_uri",
    "object_key",
    "text_excerpt",
)


def test_real_example_inventory_hook_emits_safe_package_aggregate(tmp_path: Path) -> None:
    """Private tree via the inventory-only gate yields a safe package-ledger aggregate."""
    from idis.evaluation.real_example_gate import build_data_room_package_inventory_summary

    root = tmp_path / "real_example"
    ledger = tmp_path / "ledger.json"
    _write_private_inventory_tree(root)

    summary = build_data_room_package_inventory_summary(root=root, ledger_path=ledger)

    # Safe aggregates compatible with the durable data-room package ledger summary.
    assert summary["safe_summary"] is True
    assert summary["file_count"] == 4
    assert isinstance(summary["ledger_entry_count"], int)
    assert summary["counts_by_extension"] == {".pdf": 1, ".mp4": 1, ".png": 1, ".html": 1}
    assert summary["counts_by_status"] == {"inventoried": 4}
    assert set(summary) >= {
        "safe_summary",
        "file_count",
        "ledger_entry_count",
        "counts_by_extension",
        "counts_by_status",
        "counts_by_reason_code",
    }

    # No raw path / filename / content / object-key leaks (incl. the absolute root path).
    encoded = json.dumps(summary, sort_keys=True)
    assert str(root) not in encoded
    for marker in _PRIVATE_MARKERS:
        assert marker not in encoded
    # Gate-private key shapes never surface either.
    for raw_key in ('"path"', '"filename"', '"root_path"', '"sha256"', '"local_path"'):
        assert raw_key not in encoded


def test_real_example_inventory_hook_runs_no_parser_ocr_media_or_provider(tmp_path: Path) -> None:
    """Inventory-only hook performs no parse/OCR/media/provider execution."""
    from idis.evaluation.real_example_gate import build_data_room_package_inventory_summary

    root = tmp_path / "real_example"
    ledger = tmp_path / "ledger.json"
    _write_private_inventory_tree(root)

    boom = AssertionError("inventory-only must not parse/OCR/transcribe")
    with (
        patch("idis.evaluation.real_example_gate.capability_for_document", side_effect=boom),
        patch("idis.evaluation.real_example_gate.probe_faster_whisper_model", side_effect=boom),
        patch(
            "idis.evaluation.real_example_gate.run_injected_parse_with_timeout", side_effect=boom
        ),
        patch("idis.evaluation.real_example_gate.run_parse_subprocess", side_effect=boom),
    ):
        summary = build_data_room_package_inventory_summary(root=root, ledger_path=ledger)

    assert summary["file_count"] == 4
    assert summary["safe_summary"] is True


def test_real_example_inventory_hook_does_not_touch_readiness(tmp_path: Path) -> None:
    """Inventory-only hook does not assess or clear readiness, nor claim VC-ready."""
    from idis.evaluation.real_example_gate import build_data_room_package_inventory_summary

    root = tmp_path / "real_example"
    ledger = tmp_path / "ledger.json"
    _write_private_inventory_tree(root)

    with patch(
        "idis.services.runs.strict_full_live.build_strict_full_live_readiness_report",
        side_effect=AssertionError("inventory-only must not touch readiness"),
    ):
        summary = build_data_room_package_inventory_summary(root=root, ledger_path=ledger)

    assert summary["file_count"] == 4
    encoded = json.dumps(summary, sort_keys=True).lower()
    for forbidden in ("readiness", "vc_ready", "vc-ready"):
        assert forbidden not in encoded


# --- Slice78 Task 4: INVENTORY_ONLY safe aggregate after canonical HTML/TXT support ---


def _write_slice78_inventory_tree(root: Path) -> None:
    """Private tree spanning text-like + blocker classes; raw markers must never leak."""
    (root / "Site").mkdir(parents=True)
    (root / "Site" / "index.html").write_text(
        "<html><head><title>SECRET_HTML_TITLE</title></head>"
        "<body><p>SECRET_CUSTOMER</p></body></html>",
        encoding="utf-8",
    )
    (root / "Notes").mkdir()
    (root / "Notes" / "memo.txt").write_text(
        "SECRET_TXT_BODY s3://secret-bucket/key\n", encoding="utf-8"
    )
    (root / "Docs").mkdir()
    (root / "Docs" / "board-pack.pdf").write_bytes(b"%PDF-1.4 SECRET_CUSTOMER")
    (root / "Media").mkdir()
    (root / "Media" / "demo.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (root / "Scans").mkdir()
    (root / "Scans" / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "Exports").mkdir()
    (root / "Exports" / "data.csv").write_text("SECRET_CUSTOMER,123\n", encoding="utf-8")


_SLICE78_MARKERS = (
    "SECRET_HTML_TITLE",
    "SECRET_TXT_BODY",
    "SECRET_CUSTOMER",
    "s3://secret-bucket/key",
    "index.html",
    "memo.txt",
    "board-pack",
    "storage_uri",
    "manifest_uri",
    "object_key",
    "text_excerpt",
)


def test_real_example_inventory_includes_html_txt_and_stays_safe(tmp_path: Path) -> None:
    """INVENTORY_ONLY safe aggregate over a text-like + blocker tree (Slice78 Task 4)."""
    from idis.evaluation.real_example_gate import build_data_room_package_inventory_summary

    root = tmp_path / "real_example"
    ledger = tmp_path / "ledger.json"
    _write_slice78_inventory_tree(root)

    summary = build_data_room_package_inventory_summary(root=root, ledger_path=ledger)

    # Safe aggregates only. INVENTORY_ONLY never parses, so every file is "inventoried"
    # (it does NOT classify per-format) -- html/txt are inventoried like everything else.
    assert summary["safe_summary"] is True
    assert summary["file_count"] == 6
    assert summary["counts_by_extension"] == {
        ".csv": 1,
        ".html": 1,
        ".mp4": 1,
        ".pdf": 1,
        ".png": 1,
        ".txt": 1,
    }
    assert summary["counts_by_status"] == {"inventoried": 6}
    # No per-format support is asserted in INVENTORY_ONLY; nothing is flagged unsupported here.
    assert "unsupported_format" not in summary["counts_by_reason_code"]

    encoded = json.dumps(summary, sort_keys=True)
    assert str(root) not in encoded
    for marker in _SLICE78_MARKERS:
        assert marker not in encoded
    for raw_key in ('"path"', '"filename"', '"root_path"', '"sha256"', '"local_path"'):
        assert raw_key not in encoded
    lowered = encoded.lower()
    for forbidden in ("readiness", "vc_ready", "vc-ready", "cleared", "full_run"):
        assert forbidden not in lowered


def test_real_example_inventory_text_tree_runs_no_execution(tmp_path: Path) -> None:
    """INVENTORY_ONLY over text-like classes runs no parser/OCR/media/provider/readiness work."""
    from idis.evaluation.real_example_gate import build_data_room_package_inventory_summary

    root = tmp_path / "real_example"
    ledger = tmp_path / "ledger.json"
    _write_slice78_inventory_tree(root)

    boom = AssertionError("inventory-only must not parse/transcribe/assess readiness")
    with (
        patch("idis.parsers.registry.parse_bytes", side_effect=boom),
        patch("idis.evaluation.real_example_gate.capability_for_document", side_effect=boom),
        patch("idis.evaluation.real_example_gate.probe_faster_whisper_model", side_effect=boom),
        patch(
            "idis.evaluation.real_example_gate.run_injected_parse_with_timeout", side_effect=boom
        ),
        patch("idis.evaluation.real_example_gate.run_parse_subprocess", side_effect=boom),
        patch(
            "idis.services.runs.strict_full_live.build_strict_full_live_readiness_report",
            side_effect=boom,
        ),
    ):
        summary = build_data_room_package_inventory_summary(root=root, ledger_path=ledger)

    assert summary["file_count"] == 6
    assert summary["safe_summary"] is True
    assert summary["counts_by_status"] == {"inventoried": 6}
