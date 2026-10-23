"""Required caller fixtures over a receipt-bound disposable app-role database.

This composes existing producers and freeze; it is not portable frozen evidence,
an acceptance corpus, a bootstrapper or authority to reuse a configured database.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from idis.api import abac
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import InMemoryAuditSink
from idis.models.source_package_manifest import (
    SourcePackageManifest,
    canonical_source_manifest_sha256,
)
from idis.persistence import db
from idis.persistence.repositories import webhook_outbox
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.persistence.repositories.task7_source_freeze import freeze_source_package
from idis.services.ingestion import IngestionContext
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from idis.tools.preview_source_preparation import PreviewSourceFile, prepare_preview_source_package
from tests.test_pdf_parser import REPORTLAB_AVAILABLE, create_test_pdf

URL_ENV = "IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL"
RECEIPT_ENV = "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT"
RECEIPT_HASH_ENV = "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT_SHA256"
RECEIPT_VERSION = "cir04c3-required-frozen-preview-db-v1"


class RequiredFrozenPreviewDatabaseError(RuntimeError):
    """Safe mandatory fixture error, never an optional integration-test skip."""


class CapturingRequiredPostgresAuditSink(PostgresAuditSink):
    """Capture through the observer API only after the actual durable INSERT."""

    def __init__(self, observer: InMemoryAuditSink) -> None:
        super().__init__()
        self._observer = observer

    def _insert_event(self, conn: Connection, event: dict[str, Any]) -> None:
        super()._insert_event(conn, event)
        # InMemoryAuditSink.events returns a copy, not a writable capture port.
        self._observer.emit(event)


def _fail() -> NoReturn:
    raise RequiredFrozenPreviewDatabaseError(
        "REQUIRED_FROZEN_PREVIEW_DATABASE_UNQUALIFIED"
    ) from None


def qualify_required_database(env: Mapping[str, str]) -> Engine:
    """Read-only qualification before any fixture write or production override."""
    try:
        value, receipt_path, expected_hash = (
            env[name]
            for name in (
                URL_ENV,
                RECEIPT_ENV,
                RECEIPT_HASH_ENV,
            )
        )
        raw = Path(receipt_path).read_bytes()
        receipt = json.loads(raw)
        url = make_url(value)
        if (
            not value
            or hashlib.sha256(raw).hexdigest() != expected_hash
            or receipt["schema_version"] != RECEIPT_VERSION
            or receipt["owner"] != "CIR-04 required-caller disposable fixture"
            or receipt["absent_before_create"] is not True
            or not receipt["service_container_id"]
            or url.get_backend_name() != "postgresql"
            or url.host != "127.0.0.1"
            or (url.host, url.port, url.database, url.username)
            != (receipt["host"], receipt["port"], receipt["database"], receipt["app_role"])
            or not str(url.database).startswith("idis_c3_callers_")
            or receipt["revision"] != "0038"
            or receipt["role_flags"] != {"rolsuper": False, "rolbypassrls": False}
            or env.get(db.IDIS_DATABASE_URL_ENV) == value
        ):
            _fail()
    except (KeyError, TypeError, ValueError, OSError, SQLAlchemyError):
        _fail()
    engine = None
    try:
        engine = create_engine(value, echo=False)
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            actual = (
                conn.execute(
                    text(
                        "SELECT current_database() AS database,current_user AS app_role,"
                        "d.oid AS database_oid,r.rolsuper,r.rolbypassrls "
                        "FROM pg_database d JOIN pg_roles r ON r.rolname=current_user "
                        "WHERE d.datname=current_database()"
                    )
                )
                .mappings()
                .one()
            )
            if (
                any(actual[key] != receipt[key] for key in ("database", "app_role", "database_oid"))
                or actual["rolsuper"] is not False
                or actual["rolbypassrls"] is not False
                or list(
                    conn.execute(text("SELECT version_num FROM public.alembic_version")).scalars()
                )
                != ["0038"]
            ):
                _fail()
    except (
        SQLAlchemyError,
        ImportError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        RequiredFrozenPreviewDatabaseError,
    ):
        if engine is not None:
            engine.dispose()
        # Do not expose a URL/driver exception in a missing prerequisite failure.
        _fail()
    return engine


@dataclass(frozen=True)
class RequiredFrozenPreview:
    """Only the qualified app engine; no admin or migration interface."""

    engine: Engine

    def add_tenant(self, tenant_id: str, region: str) -> None:
        with self.engine.begin() as conn:
            db.set_tenant_local(conn, tenant_id)
            inserted = conn.execute(
                text(
                    "INSERT INTO tenants(tenant_id,name,data_region) "
                    "VALUES(CAST(:tenant AS uuid),'Public synthetic caller fixture',:region) "
                    "RETURNING tenant_id::text,data_region"
                ),
                {"tenant": tenant_id, "region": region},
            ).one()
            assert tuple(inserted) == (tenant_id, region)
        with self.engine.begin() as conn:
            db.set_tenant_local(conn, tenant_id)
            assert (
                conn.execute(
                    text("SELECT data_region FROM tenants WHERE tenant_id=CAST(:tenant AS uuid)"),
                    {"tenant": tenant_id},
                ).scalar_one()
                == region
            )

    def prepare_pdf(self, tenant_id: str, deal_id: str, root: Path) -> str:
        if not REPORTLAB_AVAILABLE:
            pytest.fail("Required public PDF fixture dependency is unavailable")
        prepared = prepare_preview_source_package(
            engine=self.engine,
            ctx=IngestionContext(
                tenant_id=UUID(tenant_id), actor_id="actor-a", request_id=str(uuid4())
            ),
            deal_id=UUID(deal_id),
            files=[
                PreviewSourceFile(
                    "pdf",
                    "application/pdf",
                    create_test_pdf(
                        [
                            "PUBLIC SYNTHETIC CALLER PREVIEW",
                            "Revenue was $5M in 2024.",
                        ]
                    ),
                )
            ],
            compliant_store=ComplianceEnforcedStore(
                inner_store=FilesystemObjectStore(base_dir=root)
            ),
            audit_sink=PostgresAuditSink(),
        )
        self.freeze(tenant_id, deal_id, prepared.package_id)
        return prepared.package_id

    def freeze(self, tenant_id: str, deal_id: str, package_id: str) -> None:
        freeze_source_package(
            engine=self.engine, tenant_id=tenant_id, deal_id=deal_id, package_id=package_id
        )
        assert self.package_rows(tenant_id, deal_id, package_id)[0]["status"] == "SEALED"

    def package_rows(self, tenant_id: str, deal_id: str, package_id: str) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            db.set_tenant_local(conn, tenant_id)
            rows = list(
                map(
                    dict,
                    conn.execute(
                        text(
                            "SELECT DISTINCT p.package_id,p.status,p.source_binding_state,"
                            "f.file_entry_id,f.doc_id,f.document_id,a.parse_attempt_id,"
                            "f.source_binding_state AS file_binding,"
                            "d.source_binding_state AS document_binding,to_jsonb(p) AS package_row,"
                            "to_jsonb(f) AS file_row "
                            "FROM data_room_packages p "
                            "JOIN data_room_package_files f USING(package_id) "
                            "JOIN documents d ON d.document_id=f.document_id "
                            "JOIN document_spans s ON s.document_id=d.document_id "
                            "JOIN document_parse_attempts a "
                            "ON a.parse_attempt_id=s.parse_attempt_id "
                            "WHERE p.package_id=CAST(:package AS uuid) "
                            "AND p.deal_id=CAST(:deal AS uuid) "
                            "ORDER BY f.file_entry_id"
                        ),
                        {"package": package_id, "deal": deal_id},
                    ).mappings(),
                )
            )
            assert rows and all(
                row["status"] == "SEALED"
                and row["source_binding_state"] == "BOUND"
                and row["file_binding"] == row["document_binding"] == "BOUND"
                and row["doc_id"] is not None
                and row["document_id"] is not None
                and row["parse_attempt_id"] is not None
                for row in rows
            )
            return rows

    def package_public_uploads(
        self,
        client: TestClient,
        *,
        api_key: str,
        tenant_id: str,
        deal_id: str,
        uploads: Sequence[Mapping[str, Any]],
        seal: bool = True,
    ) -> str:
        # Existing public manifest contract: opaque fixture-domain declarations,
        # not production preview/path provenance or acceptance producer identity.
        with self.engine.begin() as conn:
            repo = PostgresDocumentsRepository(conn, tenant_id)
            documents = [repo.get_document(str(upload["document_id"])) for upload in uploads]
        assert all(document is not None for document in documents)
        files = []
        for sequence, (upload, document) in enumerate(zip(uploads, documents, strict=True)):
            assert document is not None
            source = document["source_metadata"]
            assert source["source_system"] == "api-upload"
            assert str(document["doc_id"]) == str(upload["doc_id"])
            files.append(
                {
                    "sequence": sequence,
                    "generated_upload_name": source["upload_filename"],
                    "source_locator_sha256": _fixture_hash(
                        [
                            "cir04c3-public-upload-component-locator-v1",
                            tenant_id,
                            deal_id,
                            sequence,
                            str(document["document_id"]),
                        ]
                    ),
                    "content_sha256": document["sha256"],
                    "size_bytes": source["size_bytes"],
                    "extension": "pdf",
                    "document_id": document["document_id"],
                    "doc_id": document["doc_id"],
                }
            )
        payload = {
            "schema_version": "source-package-manifest-v1",
            "company_alias": "company-999",
            "deal_alias": "deal-999",
            "document_set_alias": "documents-999",
            "run_alias": "run-999",
            "ledger_alias": "ledger-999",
            "package_alias": "package-999",
            "source_tree_fingerprint": _fixture_hash(
                ["cir04c3-public-upload-component-tree-v1", files]
            ),
            "file_count": len(files),
            "total_bytes": sum(row["size_bytes"] for row in files),
            "files": files,
        }
        manifest = SourcePackageManifest.model_validate(
            {
                **payload,
                "manifest_sha256": canonical_source_manifest_sha256(payload),
            }
        )
        response = client.post(
            f"/v1/deals/{deal_id}/data-room-packages",
            headers={"X-IDIS-API-Key": api_key},
            json={
                "document_ids": [str(file.document_id) for file in manifest.files],
                "source_manifest": manifest.model_dump(mode="json"),
            },
        )
        assert response.status_code == 201
        package_id = str(response.json()["package_id"])
        if seal:
            self.freeze(tenant_id, deal_id, package_id)
        return package_id


def _fixture_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@contextmanager
def required_frozen_preview(monkeypatch: pytest.MonkeyPatch) -> Iterator[RequiredFrozenPreview]:
    """Scoped override; restore the exact prior cached engines and environment."""
    engine = qualify_required_database(os.environ)
    previous = (db._app_engine, db._admin_engine, db._erasure_engine)
    db._app_engine, db._admin_engine, db._erasure_engine = engine, None, None
    try:
        with monkeypatch.context() as scoped:
            # The default assignment store is lazy and otherwise survives a
            # PG→memory fixture boundary even after the engine/URL is restored.
            scoped.setattr(abac, "_default_store", None)
            scoped.setattr(webhook_outbox, "_DEFAULT_OUTBOX", None)
            scoped.setenv(db.IDIS_DATABASE_URL_ENV, os.environ[URL_ENV])
            scoped.delenv(db.IDIS_DATABASE_ADMIN_URL_ENV, raising=False)
            scoped.delenv(db.IDIS_ERASURE_DATABASE_URL_ENV, raising=False)
            scoped.delenv("IDIS_REQUIRE_POSTGRES", raising=False)
            yield RequiredFrozenPreview(engine)
    finally:
        db.reset_engines()
        db._app_engine, db._admin_engine, db._erasure_engine = previous
        assert (db._app_engine, db._admin_engine, db._erasure_engine) == previous
