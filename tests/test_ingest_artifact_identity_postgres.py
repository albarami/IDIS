"""Identity consistency regression for upload → ingest (Sprint 2, Task 9).

Proves the fix to the duplicate artifact-id leak: one logical upload
must produce exactly one document_artifacts row, and the resulting
`documents` row must link to the route-created artifact identity —
not a fresh artifact generated inside IngestionService.ingest_bytes.

Postgres-gated, mirrors the style of tests/test_documents_live_path_postgres.py.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV, TenantContext
from idis.api.main import create_app
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.services.ingestion import IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests._postgres_support import (
    admin_engine_generator,
    migrated_db_generator,
    postgres_configured,
    truncate_all,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine


TENANT_ID = "f1de7717-f1de-7717-f1de-f1de7717f1de"
ACTOR_ID = "actor-identity-fix"
API_KEY = "identity-fix-key"


@pytest.fixture(scope="module")
def _pg_admin_engine() -> Generator[Engine, None, None]:
    yield from admin_engine_generator()


@pytest.fixture(scope="module")
def _pg_migrated(_pg_admin_engine: Engine) -> Generator[None, None, None]:
    yield from migrated_db_generator(_pg_admin_engine)


@pytest.fixture(autouse=True)
def _pg_clean_state(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    if not postgres_configured():
        pytest.skip("Postgres not configured")
    admin_engine = request.getfixturevalue("_pg_admin_engine")
    request.getfixturevalue("_pg_migrated")
    truncate_all(admin_engine)
    yield
    truncate_all(admin_engine)


def _minimal_pdf() -> bytes:
    return b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT
/F1 12 Tf
100 700 Td
(Revenue was $5M in 2024.) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000214 00000 n
trailer
<< /Size 5 /Root 1 0 R >>
startxref
307
%%EOF
"""


def _build_wired_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, ComplianceEnforcedStore, TenantContext]:
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY: {
                    "tenant_id": TENANT_ID,
                    "actor_id": ACTOR_ID,
                    "name": "Identity Fix",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )

    tmpdir = tempfile.mkdtemp(prefix="idis_identity_fix_")
    inner_store = FilesystemObjectStore(base_dir=Path(tmpdir))
    compliant_store = ComplianceEnforcedStore(inner_store=inner_store)
    audit_sink = InMemoryAuditSink()
    ingestion_service = IngestionService(
        compliant_store=compliant_store,
        audit_sink=audit_sink,
    )
    idem_store = SqliteIdempotencyStore(in_memory=True)
    app = create_app(
        audit_sink=audit_sink,
        idempotency_store=idem_store,
        ingestion_service=ingestion_service,
        service_region="me-south-1",
    )
    client = TestClient(app, raise_server_exceptions=False)
    tenant_ctx = TenantContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        name="Identity Fix",
        timezone="UTC",
        data_region="me-south-1",
    )
    return client, compliant_store, tenant_ctx


class TestUploadIngestIdentityIsPreserved:
    """One upload → ingest must produce exactly one document_artifacts row,
    and the resulting documents row must reference the route-created
    artifact identity.
    """

    def test_upload_then_ingest_produces_single_artifact_row(
        self,
        _pg_admin_engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        admin_engine = _pg_admin_engine
        client, compliant_store, tenant_ctx = _build_wired_client(monkeypatch)
        headers = {"X-IDIS-API-Key": API_KEY, "Content-Type": "application/json"}

        # 1. Create deal (durable via DealsRepository).
        deal_resp = client.post(
            "/v1/deals",
            headers=headers,
            json={"name": "Identity Fix Deal", "company_name": "Fix Co"},
        )
        assert deal_resp.status_code == 201, deal_resp.text
        deal_id = deal_resp.json()["deal_id"]

        # 2. Stage bytes, attach via route.
        pdf = _minimal_pdf()
        storage_key = f"fix/{uuid.uuid4()}.pdf"
        compliant_store.put(tenant_ctx=tenant_ctx, key=storage_key, data=pdf)
        doc_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers=headers,
            json={
                "doc_type": "PITCH_DECK",
                "title": "fix-deck.pdf",
                "source_system": "api",
                "uri": f"file://{storage_key}",
                "sha256": hashlib.sha256(pdf).hexdigest(),
                "auto_ingest": False,
            },
        )
        assert doc_resp.status_code == 201, doc_resp.text
        route_doc_id = doc_resp.json()["doc_id"]

        # 3. Ingest (the previously-broken path generated a fresh artifact).
        ingest_resp = client.post(
            f"/v1/documents/{route_doc_id}/ingest",
            headers=headers,
            json={},
        )
        assert ingest_resp.status_code == 202, ingest_resp.text
        assert ingest_resp.json()["status"] == "SUCCEEDED", ingest_resp.text

        # 4. Identity assertions against durable state.
        with admin_engine.begin() as conn:
            art_rows = conn.execute(
                text(
                    "SELECT doc_id FROM document_artifacts WHERE deal_id = :d"
                ),
                {"d": deal_id},
            ).fetchall()
            document_rows = conn.execute(
                text(
                    "SELECT document_id, doc_id FROM documents "
                    "WHERE deal_id = :d"
                ),
                {"d": deal_id},
            ).fetchall()

        # Exactly one artifact row for this logical upload.
        assert len(art_rows) == 1, (
            f"upload → ingest must produce exactly one document_artifacts "
            f"row; got {len(art_rows)}"
        )
        assert str(art_rows[0].doc_id) == route_doc_id, (
            f"artifact row must carry the route-level doc_id "
            f"({route_doc_id!r}), got {str(art_rows[0].doc_id)!r}"
        )

        # Every documents row for this deal links to the same, route-level
        # artifact identity — not a service-generated fresh id.
        assert len(document_rows) >= 1, "ingest must produce at least one document row"
        assert all(str(r.doc_id) == route_doc_id for r in document_rows), (
            "documents.doc_id must reference the route-created artifact "
            "identity; the service must not invent a second artifact"
        )

    def test_auto_ingest_also_uses_single_artifact_identity(
        self,
        _pg_admin_engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auto_ingest=True on POST /v1/deals/{id}/documents takes a
        different code path (inline trigger). It must behave the same:
        one artifact, documents linked to the route-created identity.
        """
        admin_engine = _pg_admin_engine
        client, compliant_store, tenant_ctx = _build_wired_client(monkeypatch)
        headers = {"X-IDIS-API-Key": API_KEY, "Content-Type": "application/json"}

        deal_resp = client.post(
            "/v1/deals",
            headers=headers,
            json={"name": "Auto Ingest Deal", "company_name": "Auto Co"},
        )
        deal_id = deal_resp.json()["deal_id"]

        pdf = _minimal_pdf()
        storage_key = f"auto/{uuid.uuid4()}.pdf"
        compliant_store.put(tenant_ctx=tenant_ctx, key=storage_key, data=pdf)

        doc_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers=headers,
            json={
                "doc_type": "PITCH_DECK",
                "title": "auto-deck.pdf",
                "source_system": "api",
                "uri": f"file://{storage_key}",
                "sha256": hashlib.sha256(pdf).hexdigest(),
                "auto_ingest": True,
            },
        )
        assert doc_resp.status_code == 201, doc_resp.text
        route_doc_id = doc_resp.json()["doc_id"]

        with admin_engine.begin() as conn:
            art_rows = conn.execute(
                text(
                    "SELECT doc_id FROM document_artifacts WHERE deal_id = :d"
                ),
                {"d": deal_id},
            ).fetchall()
            document_rows = conn.execute(
                text(
                    "SELECT document_id, doc_id FROM documents "
                    "WHERE deal_id = :d"
                ),
                {"d": deal_id},
            ).fetchall()

        assert len(art_rows) == 1, (
            f"auto_ingest must produce exactly one document_artifacts "
            f"row; got {len(art_rows)}"
        )
        assert str(art_rows[0].doc_id) == route_doc_id
        assert len(document_rows) >= 1
        assert all(str(r.doc_id) == route_doc_id for r in document_rows)


class TestStandaloneIngestStillOwnsArtifactCreation:
    """Calling IngestionService.ingest_bytes *without* existing_artifact_id
    (the non-route path used by direct service tests) must keep the old
    behavior: generate its own artifact_id and persist the row itself.
    This guards against a regression where the service starts silently
    expecting an external caller to write the artifact row.
    """

    def test_no_existing_artifact_id_generates_and_persists_fresh_row(
        self,
        _pg_admin_engine: Engine,
    ) -> None:
        from idis.persistence.db import get_app_engine, set_tenant_local

        admin_engine = _pg_admin_engine
        # Seed a deal (admin) so FK is satisfied.
        deal_id = str(uuid.uuid4())
        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO deals (
                        deal_id, tenant_id, name, company_name, status,
                        stage, tags, created_at, updated_at
                    ) VALUES (
                        :deal_id, :tenant_id, 'd', 'c', 'NEW',
                        NULL, CAST('[]' AS JSONB), now(), NULL
                    )
                    """
                ),
                {"deal_id": deal_id, "tenant_id": TENANT_ID},
            )

        tmpdir = tempfile.mkdtemp(prefix="idis_standalone_")
        compliant_store = ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=Path(tmpdir))
        )
        service = IngestionService(compliant_store=compliant_store)

        from idis.services.ingestion import IngestionContext

        ctx = IngestionContext(
            tenant_id=uuid.UUID(TENANT_ID),
            actor_id=ACTOR_ID,
            request_id="req-standalone",
        )

        app_engine = get_app_engine()
        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_ID)
            result = service.ingest_bytes(
                ctx,
                uuid.UUID(deal_id),
                filename="standalone.pdf",
                media_type="application/pdf",
                data=_minimal_pdf(),
                db_conn=conn,
            )

        assert result.success is True
        assert result.artifact_id is not None

        with admin_engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT doc_id FROM document_artifacts "
                    "WHERE doc_id = :d AND deal_id = :deal"
                ),
                {"d": str(result.artifact_id), "deal": deal_id},
            ).fetchone()
        assert row is not None, (
            "standalone ingest (no existing_artifact_id) must still write "
            "the document_artifacts row itself"
        )
