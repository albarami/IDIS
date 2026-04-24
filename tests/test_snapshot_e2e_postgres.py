"""Sprint 1 SNAPSHOT end-to-end gate test (Sprint 1 Wave 3, Task 8).

One automated Postgres-backed proof that the core Sprint 1 main path
works end-to-end:

1. create a deal through POST /v1/deals,
2. pre-stage document bytes in the compliance-enforced store,
3. attach the document via POST /v1/deals/{dealId}/documents,
4. ingest it via POST /v1/documents/{docId}/ingest (this writes the
   durable document_artifacts / documents / document_spans rows
   through the Task 6-wired route and IngestionService),
5. start a SNAPSHOT run via POST /v1/deals/{dealId}/runs,
6. assert the run reports SUCCEEDED via the API,
7. assert the durable outputs (document_artifacts, documents,
   document_spans, runs, run_steps, claims) are persisted in Postgres.

To keep the assertions honest, before the final SELECT sweep the test
wipes the in-memory mirrors (_DocumentStore, IngestionService._*) so a
false-positive via the in-memory fallback cannot rescue a broken durable
path.

Scope: one happy path. Skips cleanly when IDIS_DATABASE_URL /
IDIS_DATABASE_ADMIN_URL are unset; fails under IDIS_REQUIRE_POSTGRES=1
without them — same pattern as the existing *_postgres.py suites.
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
from idis.api.routes.documents import clear_document_store
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


TENANT_ID = "e2e1e2e1-e2e1-e2e1-e2e1-e2e1e2e1e2e1"
ACTOR_ID = "actor-snapshot-gate"
API_KEY = "snapshot-gate-key"


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
    """A minimal valid single-page PDF the existing parsers accept."""
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


class TestSnapshotEndToEndGate:
    """Single Sprint 1 gate: the durable SNAPSHOT main path works."""

    def test_snapshot_e2e_persists_all_core_entities(
        self,
        _pg_admin_engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        admin_engine = _pg_admin_engine
        monkeypatch.setenv(
            IDIS_API_KEYS_ENV,
            json.dumps(
                {
                    API_KEY: {
                        "tenant_id": TENANT_ID,
                        "actor_id": ACTOR_ID,
                        "name": "Snapshot Gate",
                        "timezone": "UTC",
                        "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                    }
                }
            ),
        )

        # Real wired app: audit + compliant store + ingestion service,
        # with Postgres auto-wired by DBTransactionMiddleware because
        # IDIS_DATABASE_URL is set.
        tmpdir = tempfile.mkdtemp(prefix="idis_snapshot_gate_")
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

        headers = {"X-IDIS-API-Key": API_KEY, "Content-Type": "application/json"}

        # --- 1. Create deal ---
        deal_resp = client.post(
            "/v1/deals",
            headers=headers,
            json={"name": "Snapshot Gate Deal", "company_name": "Gate Corp"},
        )
        assert deal_resp.status_code == 201, deal_resp.text
        deal_id = deal_resp.json()["deal_id"]

        # Honesty assertion: the gate refuses to continue if the durable
        # deal-creation path is broken. A direct SELECT against the
        # deals table — not a seed_deal repair — proves POST /v1/deals
        # actually persisted the row via the real DealsRepository path.
        with admin_engine.begin() as conn:
            deal_row = conn.execute(
                text("SELECT deal_id, tenant_id FROM deals WHERE deal_id = :d"),
                {"d": deal_id},
            ).fetchone()
        assert deal_row is not None, (
            "POST /v1/deals returned 201 but no deals row is durably persisted"
        )
        assert str(deal_row.deal_id) == deal_id
        assert str(deal_row.tenant_id) == TENANT_ID

        # --- 2. Pre-stage document bytes in the compliant store. ---
        # This is what the real upload flow produces (the /v1/documents/{id}
        # GET path reads back through the same store). For the gate test
        # the stage-and-attach-uri pattern matches the existing e2e tests
        # and keeps bytes out of the deal creation body.
        pdf = _minimal_pdf()
        pdf_sha = hashlib.sha256(pdf).hexdigest()
        storage_key = f"gate/{uuid.uuid4()}.pdf"
        tenant_ctx = TenantContext(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            name="Snapshot Gate",
            timezone="UTC",
            data_region="me-south-1",
        )
        compliant_store.put(tenant_ctx=tenant_ctx, key=storage_key, data=pdf)

        # --- 3. Attach document via API ---
        doc_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers=headers,
            json={
                "doc_type": "PITCH_DECK",
                # Title doubles as the filename the ingestion service writes
                # into its deterministic storage key; keep it safe-chars.
                "title": "gate-deck.pdf",
                "source_system": "api",
                "uri": f"file://{storage_key}",
                "sha256": pdf_sha,
                "auto_ingest": False,
            },
        )
        assert doc_resp.status_code == 201, doc_resp.text
        doc_id = doc_resp.json()["doc_id"]

        # --- 4. Ingest via API ---
        ingest_resp = client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers=headers,
            json={},
        )
        assert ingest_resp.status_code == 202, ingest_resp.text
        assert ingest_resp.json()["status"] == "SUCCEEDED", ingest_resp.text

        # --- 5. Start SNAPSHOT run ---
        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            headers=headers,
            json={"mode": "SNAPSHOT"},
        )
        assert run_resp.status_code == 202, run_resp.text
        run_id = run_resp.json()["run_id"]
        # Sprint 2 Task 11: POST /runs is enqueue-only. The API returns
        # QUEUED immediately; the worker advances the run asynchronously.
        assert run_resp.json()["status"] == "QUEUED", run_resp.text

        # Snapshot honesty check (pre-worker): durable state must reflect
        # QUEUED. If a revert to inline execution ever ships, this would
        # observe SUCCEEDED here before the worker runs.
        with admin_engine.begin() as conn:
            queued_row = conn.execute(
                text("SELECT status FROM runs WHERE run_id = :r"),
                {"r": run_id},
            ).fetchone()
        assert queued_row is not None
        assert queued_row.status == "QUEUED", (
            f"POST /runs must not execute inline; expected QUEUED durably, "
            f"got {queued_row.status!r}"
        )

        # Drive the worker to advance the run (this is what the live
        # deployment's background loop does).
        import asyncio as _asyncio

        from idis.pipeline.worker import PipelineWorker

        _asyncio.run(PipelineWorker(poll_interval=0)._process_queued_runs())

        # Honesty check: wipe every in-memory mirror that could serve
        # false positives in the remaining assertions. If the durable
        # path weren't actually writing these rows, the direct SELECTs
        # below would fail.
        clear_document_store()
        ingestion_service._artifacts.clear()
        ingestion_service._documents.clear()
        ingestion_service._spans.clear()

        # --- 6. Direct SELECTs prove durable rows exist ---
        # NOTE: scope by deal_id, not by the route-level doc_id. POST
        # /v1/deals/{id}/documents writes one document_artifacts row, and
        # POST /v1/documents/{id}/ingest internally generates a *fresh*
        # artifact_id + document_id inside IngestionService.ingest_bytes,
        # so the ingest-produced Document links to a different
        # document_artifacts row than the route created. This is a
        # pre-existing Task 6 design leak (reported in the task summary);
        # it's not a durability gap, so this gate test verifies durability
        # by deal_id rather than by route doc_id.
        with admin_engine.begin() as conn:
            art_rows = conn.execute(
                text(
                    "SELECT doc_id, tenant_id, deal_id, sha256 "
                    "FROM document_artifacts WHERE deal_id = :d"
                ),
                {"d": deal_id},
            ).fetchall()
            document_rows = conn.execute(
                text(
                    "SELECT document_id, tenant_id, doc_id, parse_status "
                    "FROM documents WHERE deal_id = :d"
                ),
                {"d": deal_id},
            ).fetchall()
            span_rows = conn.execute(
                text(
                    "SELECT span_id, tenant_id, document_id FROM document_spans "
                    "WHERE document_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [str(r.document_id) for r in document_rows]},
            ).fetchall()
            run_row = conn.execute(
                text(
                    "SELECT run_id, tenant_id, deal_id, status FROM runs "
                    "WHERE run_id = :r"
                ),
                {"r": run_id},
            ).fetchone()
            step_rows = conn.execute(
                text(
                    "SELECT step_name, status FROM run_steps WHERE run_id = :r"
                ),
                {"r": run_id},
            ).fetchall()
            claim_rows = conn.execute(
                text(
                    "SELECT claim_id, tenant_id, deal_id FROM claims "
                    "WHERE deal_id = :d"
                ),
                {"d": deal_id},
            ).fetchall()

        # document_artifacts — at least one row for the deal, with sha256
        # matching the bytes we uploaded.
        assert len(art_rows) >= 1, "document_artifacts row must be persisted"
        assert all(str(r.tenant_id) == TENANT_ID for r in art_rows)
        assert all(str(r.deal_id) == deal_id for r in art_rows)
        assert any(r.sha256 == pdf_sha for r in art_rows), (
            "at least one artifact must carry the uploaded sha256"
        )

        # documents (>=1, tenant-scoped, PARSED, linked to a persisted artifact)
        assert len(document_rows) >= 1, "at least one documents row must exist"
        assert all(str(r.tenant_id) == TENANT_ID for r in document_rows)
        artifact_ids = {str(r.doc_id) for r in art_rows}
        assert all(str(r.doc_id) in artifact_ids for r in document_rows), (
            "each documents row must link to a persisted document_artifacts row"
        )
        assert any(r.parse_status == "PARSED" for r in document_rows)

        # document_spans (>=1, tenant-scoped)
        assert len(span_rows) >= 1, "at least one document_spans row must exist"
        assert all(str(r.tenant_id) == TENANT_ID for r in span_rows)

        # runs
        assert run_row is not None, "runs row must be persisted"
        assert str(run_row.tenant_id) == TENANT_ID
        assert str(run_row.deal_id) == deal_id
        assert run_row.status == "SUCCEEDED"

        # run_steps — SNAPSHOT emits INGEST_CHECK, EXTRACT, GRADE, CALC.
        step_names = {r.step_name for r in step_rows}
        assert "INGEST_CHECK" in step_names
        assert "EXTRACT" in step_names
        assert "GRADE" in step_names
        assert "CALC" in step_names, "SNAPSHOT must have a CALC step row"

        # claims — deterministic extractor stub produces at least one claim
        # on a non-empty PDF text excerpt. If this ever drops to zero on a
        # happy-path PDF the pipeline is silently broken.
        assert len(claim_rows) >= 1, "SNAPSHOT must produce at least one claim"
        assert all(str(r.tenant_id) == TENANT_ID for r in claim_rows)
        assert all(str(r.deal_id) == deal_id for r in claim_rows)
