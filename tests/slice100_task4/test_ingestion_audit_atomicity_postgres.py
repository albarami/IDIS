"""Real-Postgres proof that ingestion rows and audits share one transaction."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Connection, text

import idis.persistence.db as db_module
from idis.audit.postgres_sink import PostgresAuditSink
from idis.persistence.db import set_tenant_local
from idis.services.ingestion import IngestionContext, IngestionResult, IngestionService
from idis.services.ingestion.service import RouteValidatedSha256
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
DEAL_ID = UUID("33333333-3333-3333-3333-333333333333")


def _create_deal(connection: Connection) -> None:
    set_tenant_local(connection, str(TENANT_ID))
    connection.execute(
        text(
            """
            INSERT INTO deals (deal_id, tenant_id, name, company_name, status, created_at)
            VALUES (:deal_id, :tenant_id, :name, :name, 'ACTIVE', :created_at)
            """
        ),
        {
            "deal_id": str(DEAL_ID),
            "tenant_id": str(TENANT_ID),
            "name": "Fictional Atomicity Deal",
            "created_at": datetime.now(UTC),
        },
    )


def _ingest(
    *,
    mode: str,
    service: IngestionService,
    connection: Connection,
    tmp_path: Path,
    request_id: str,
) -> IngestionResult:
    payload = b"Fictional durable transaction evidence."
    context = IngestionContext(
        tenant_id=TENANT_ID,
        actor_id="fictional-task4-operator",
        request_id=request_id,
    )
    if mode == "bytes":
        return service.ingest_bytes(
            ctx=context,
            deal_id=DEAL_ID,
            filename="fictional-evidence.txt",
            media_type="text/plain",
            data=payload,
            db_conn=connection,
        )

    staged_path = tmp_path / f"fictional-evidence-{mode}.txt"
    staged_path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    return service.ingest_file(
        ctx=context,
        deal_id=DEAL_ID,
        filename="fictional-evidence.txt",
        media_type="text/plain",
        source_path=staged_path,
        size_bytes=len(payload),
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=digest,
            expected_sha256=digest,
        ),
        db_conn=connection,
    )


def test_request_rollback_removes_ingestion_rows_and_completion_audits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Byte and streamed ingestion cannot commit audit success outside the request."""
    with disposable_postgres() as database:
        migrate(database.admin_engine, "head")
        grant_application_access(database)

        @contextmanager
        def separate_app_transaction() -> Iterator[Connection]:
            with database.app_engine.begin() as separate_connection:
                yield separate_connection

        # Before the fix, PostgresAuditSink.emit() reaches this separate committed
        # transaction. The desired emit_in_tx() path never calls it.
        monkeypatch.setattr(db_module, "begin_app_conn", separate_app_transaction)
        store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path))

        for mode in ("bytes", "stream"):
            request_id = f"task4-audit-atomicity-{mode}"
            with database.app_engine.connect() as connection:
                transaction = connection.begin()
                try:
                    _create_deal(connection)
                    service = IngestionService(
                        compliant_store=store,
                        audit_sink=PostgresAuditSink(),
                    )
                    result = _ingest(
                        mode=mode,
                        service=service,
                        connection=connection,
                        tmp_path=tmp_path,
                        request_id=request_id,
                    )
                    assert result.success is True
                    assert (
                        connection.execute(
                            text("SELECT count(*) FROM documents WHERE document_id = :document_id"),
                            {"document_id": str(result.document_id)},
                        ).scalar_one()
                        == 1
                    )
                    assert (
                        connection.execute(
                            text(
                                "SELECT count(*) FROM audit_events WHERE request_id = :request_id"
                            ),
                            {"request_id": request_id},
                        ).scalar_one()
                        == 2
                    )
                finally:
                    transaction.rollback()

            with database.admin_engine.begin() as connection:
                durable_document_count = connection.execute(
                    text("SELECT count(*) FROM documents WHERE document_id = :document_id"),
                    {"document_id": str(result.document_id)},
                ).scalar_one()
                durable_audit_count = connection.execute(
                    text("SELECT count(*) FROM audit_events WHERE request_id = :request_id"),
                    {"request_id": request_id},
                ).scalar_one()

            assert (durable_document_count, durable_audit_count) == (0, 0)
