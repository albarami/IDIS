"""Data-room package + file-ledger repository — tenant-scoped persistence (Slice77).

Provides Postgres (RLS via SET LOCAL idis.tenant_id) and in-memory implementations,
mirroring the RunStep repository pattern. Package aggregate counts are NOT stored;
they are derived from the file rows by higher layers, so reads return packages with
default (empty) counts.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from idis.models.data_room_package import DataRoomPackage, DataRoomPackageFile
from idis.persistence.db import is_postgres_configured, set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)

_data_room_packages_store: dict[str, dict[str, Any]] = {}
"""Global in-memory store keyed by package_id."""

_data_room_package_files_store: dict[str, dict[str, Any]] = {}
"""Global in-memory store keyed by file_entry_id."""

_PACKAGE_COLUMNS = (
    "package_id, tenant_id, deal_id, status, created_by_actor_id, "
    "created_by_actor_type, manifest_uri, metadata, created_at, updated_at"
)
_FILE_COLUMNS = (
    "file_entry_id, tenant_id, package_id, deal_id, sequence, path_hash, extension, "
    "sha256, file_status, support_status, triage_status, parse_status, reason_codes, "
    "error_codes, doc_id, document_id, storage_uri, created_at"
)


def _package_from_data(data: Any) -> DataRoomPackage:
    """Build a package header from a stored dict or DB row (counts default to empty)."""
    metadata = _get(data, "metadata")
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    # model_validate (mirroring _file_from_data) keeps str->UUID runtime coercion
    # while mypy no longer sees str passed to UUID-typed constructor params.
    return DataRoomPackage.model_validate(
        {
            "package_id": str(_get(data, "package_id")),
            "tenant_id": str(_get(data, "tenant_id")),
            "deal_id": str(_get(data, "deal_id")),
            "status": _get(data, "status"),
            "created_by_actor_id": _get(data, "created_by_actor_id"),
            "created_by_actor_type": _get(data, "created_by_actor_type"),
            "manifest_uri": _get(data, "manifest_uri"),
            "metadata": metadata or {},
            "created_at": _get(data, "created_at"),
            "updated_at": _get(data, "updated_at"),
        }
    )


def _file_from_data(data: Any) -> DataRoomPackageFile:
    """Build a file-ledger row from a stored dict or DB row."""
    reason_codes = _get(data, "reason_codes")
    if isinstance(reason_codes, str):
        reason_codes = json.loads(reason_codes)
    error_codes = _get(data, "error_codes")
    if isinstance(error_codes, str):
        error_codes = json.loads(error_codes)
    return DataRoomPackageFile.model_validate(
        {
            "file_entry_id": str(_get(data, "file_entry_id")),
            "tenant_id": str(_get(data, "tenant_id")),
            "package_id": str(_get(data, "package_id")),
            "deal_id": str(_get(data, "deal_id")),
            "sequence": _get(data, "sequence"),
            "path_hash": _get(data, "path_hash"),
            "extension": _get(data, "extension"),
            "sha256": _get(data, "sha256"),
            "file_status": _get(data, "file_status"),
            "support_status": _get(data, "support_status"),
            "triage_status": _get(data, "triage_status"),
            "parse_status": _get(data, "parse_status"),
            "reason_codes": reason_codes or [],
            "error_codes": error_codes or [],
            "doc_id": _opt_str(_get(data, "doc_id")),
            "document_id": _opt_str(_get(data, "document_id")),
            "storage_uri": _get(data, "storage_uri"),
            "created_at": _get(data, "created_at"),
        }
    )


def _get(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        return data.get(key)
    return getattr(data, key)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


class InMemoryDataRoomPackagesRepository:
    """Tenant-scoped in-memory repository. All reads filter by tenant_id (no oracle)."""

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = str(tenant_id)

    def create_package(self, package: DataRoomPackage) -> DataRoomPackage:
        if str(package.tenant_id) != self._tenant_id:
            raise ValueError("Tenant mismatch in data-room package creation")
        _data_room_packages_store[str(package.package_id)] = package.model_dump(mode="json")
        return _package_from_data(_data_room_packages_store[str(package.package_id)])

    def get_package(self, package_id: str, deal_id: str) -> DataRoomPackage | None:
        data = _data_room_packages_store.get(str(package_id))
        if data is None or data["tenant_id"] != self._tenant_id or data["deal_id"] != str(deal_id):
            return None
        return _package_from_data(data)

    def list_packages_by_deal(self, deal_id: str) -> list[DataRoomPackage]:
        packages = [
            _package_from_data(data)
            for data in _data_room_packages_store.values()
            if data["tenant_id"] == self._tenant_id and data["deal_id"] == str(deal_id)
        ]
        packages.sort(key=lambda p: (p.created_at, str(p.package_id)))
        return packages

    def add_file(self, file: DataRoomPackageFile) -> DataRoomPackageFile:
        if str(file.tenant_id) != self._tenant_id:
            raise ValueError("Tenant mismatch in data-room package file creation")
        for data in _data_room_package_files_store.values():
            if (
                data["tenant_id"] == self._tenant_id
                and data["package_id"] == str(file.package_id)
                and data["path_hash"] == file.path_hash
            ):
                return _file_from_data(data)
        _data_room_package_files_store[str(file.file_entry_id)] = file.model_dump(mode="json")
        return _file_from_data(_data_room_package_files_store[str(file.file_entry_id)])

    def list_files_by_package(self, package_id: str, deal_id: str) -> list[DataRoomPackageFile]:
        files = [
            _file_from_data(data)
            for data in _data_room_package_files_store.values()
            if data["tenant_id"] == self._tenant_id
            and data["package_id"] == str(package_id)
            and data["deal_id"] == str(deal_id)
        ]
        files.sort(key=lambda f: f.sequence)
        return files


class PostgresDataRoomPackagesRepository:
    """Tenant-scoped Postgres repository. RLS enforced via SET LOCAL idis.tenant_id."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        self._conn = conn
        self._tenant_id = str(tenant_id)
        set_tenant_local(conn, tenant_id)

    def create_package(self, package: DataRoomPackage) -> DataRoomPackage:
        if str(package.tenant_id) != self._tenant_id:
            raise ValueError("Tenant mismatch in data-room package creation")
        self._conn.execute(
            text(
                f"""
                INSERT INTO data_room_packages ({_PACKAGE_COLUMNS})
                VALUES
                    (:package_id, :tenant_id, :deal_id, :status, :created_by_actor_id,
                     :created_by_actor_type, :manifest_uri, CAST(:metadata AS JSONB),
                     :created_at, :updated_at)
                """
            ),
            {
                "package_id": str(package.package_id),
                "tenant_id": str(package.tenant_id),
                "deal_id": str(package.deal_id),
                "status": package.status.value,
                "created_by_actor_id": package.created_by_actor_id,
                "created_by_actor_type": package.created_by_actor_type,
                "manifest_uri": package.manifest_uri,
                "metadata": json.dumps(package.metadata),
                "created_at": package.created_at,
                "updated_at": package.updated_at,
            },
        )
        return _package_from_data(
            {
                "package_id": str(package.package_id),
                "tenant_id": str(package.tenant_id),
                "deal_id": str(package.deal_id),
                "status": package.status.value,
                "created_by_actor_id": package.created_by_actor_id,
                "created_by_actor_type": package.created_by_actor_type,
                "manifest_uri": package.manifest_uri,
                "metadata": package.metadata,
                "created_at": package.created_at,
                "updated_at": package.updated_at,
            }
        )

    def get_package(self, package_id: str, deal_id: str) -> DataRoomPackage | None:
        result = self._conn.execute(
            text(
                f"""
                SELECT {_PACKAGE_COLUMNS}
                FROM data_room_packages
                WHERE package_id = :package_id AND deal_id = :deal_id
                """
            ),
            {"package_id": str(package_id), "deal_id": str(deal_id)},
        )
        row = result.fetchone()
        if row is None:
            return None
        return _package_from_data(row)

    def list_packages_by_deal(self, deal_id: str) -> list[DataRoomPackage]:
        result = self._conn.execute(
            text(
                f"""
                SELECT {_PACKAGE_COLUMNS}
                FROM data_room_packages
                WHERE deal_id = :deal_id
                ORDER BY created_at, package_id
                """
            ),
            {"deal_id": str(deal_id)},
        )
        return [_package_from_data(row) for row in result.fetchall()]

    def add_file(self, file: DataRoomPackageFile) -> DataRoomPackageFile:
        if str(file.tenant_id) != self._tenant_id:
            raise ValueError("Tenant mismatch in data-room package file creation")
        self._conn.execute(
            text(
                f"""
                INSERT INTO data_room_package_files ({_FILE_COLUMNS})
                VALUES
                    (:file_entry_id, :tenant_id, :package_id, :deal_id, :sequence,
                     :path_hash, :extension, :sha256, :file_status, :support_status,
                     :triage_status, :parse_status, CAST(:reason_codes AS JSONB),
                     CAST(:error_codes AS JSONB), :doc_id, :document_id, :storage_uri,
                     :created_at)
                ON CONFLICT (tenant_id, package_id, path_hash) DO NOTHING
                """
            ),
            {
                "file_entry_id": str(file.file_entry_id),
                "tenant_id": str(file.tenant_id),
                "package_id": str(file.package_id),
                "deal_id": str(file.deal_id),
                "sequence": file.sequence,
                "path_hash": file.path_hash,
                "extension": file.extension,
                "sha256": file.sha256,
                "file_status": file.file_status.value,
                "support_status": file.support_status.value,
                "triage_status": file.triage_status.value,
                "parse_status": file.parse_status.value,
                "reason_codes": json.dumps(list(file.reason_codes)),
                "error_codes": json.dumps(list(file.error_codes)),
                "doc_id": _opt_str(file.doc_id),
                "document_id": _opt_str(file.document_id),
                "storage_uri": file.storage_uri,
                "created_at": file.created_at,
            },
        )
        # Return the persisted row: the inserted one on first add, or the existing
        # one when ON CONFLICT DO NOTHING skipped the insert (parity with in-memory).
        result = self._conn.execute(
            text(
                f"""
                SELECT {_FILE_COLUMNS}
                FROM data_room_package_files
                WHERE package_id = :package_id AND path_hash = :path_hash
                """
            ),
            {"package_id": str(file.package_id), "path_hash": file.path_hash},
        )
        row = result.fetchone()
        return _file_from_data(row) if row is not None else file

    def list_files_by_package(self, package_id: str, deal_id: str) -> list[DataRoomPackageFile]:
        result = self._conn.execute(
            text(
                f"""
                SELECT {_FILE_COLUMNS}
                FROM data_room_package_files
                WHERE package_id = :package_id AND deal_id = :deal_id
                ORDER BY sequence
                """
            ),
            {"package_id": str(package_id), "deal_id": str(deal_id)},
        )
        return [_file_from_data(row) for row in result.fetchall()]


def clear_data_room_packages_store() -> None:
    """Clear the in-memory data-room package + file stores. For testing only."""
    _data_room_packages_store.clear()
    _data_room_package_files_store.clear()


def get_data_room_packages_repository(
    conn: Connection | None,
    tenant_id: str,
) -> PostgresDataRoomPackagesRepository | InMemoryDataRoomPackagesRepository:
    """Return the Postgres repository when configured, else the in-memory fallback."""
    if conn is not None and is_postgres_configured():
        return PostgresDataRoomPackagesRepository(conn, tenant_id)
    return InMemoryDataRoomPackagesRepository(tenant_id)
