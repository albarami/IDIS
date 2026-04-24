"""API enqueue → worker execute cutover (Sprint 2, Task 11).

Proves POST /v1/deals/{deal_id}/runs is strictly enqueue-only:

* After POST, the durable `runs` row is present with status='QUEUED'
  and finished_at is NULL — the API did not execute inline.
* Invoking the worker advances the run to a terminal state
  (SUCCEEDED) and writes `run_steps` rows via the real RunOrchestrator.
* Reading GET /v1/runs/{runId} right after POST (before the worker
  runs) reflects QUEUED; after the worker runs, it reflects the
  terminal state.

If someone reverts the route to inline execution, the pre-worker
QUEUED assertion fails immediately.
"""

from __future__ import annotations

import asyncio
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
from idis.pipeline.worker import PipelineWorker
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


TENANT_ID = "11aa11aa-11aa-11aa-11aa-11aa11aa11aa"
ACTOR_ID = "actor-enqueue-cutover"
API_KEY = "enqueue-cutover-key"


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


def _client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, ComplianceEnforcedStore, TenantContext]:
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY: {
                    "tenant_id": TENANT_ID,
                    "actor_id": ACTOR_ID,
                    "name": "Enqueue Cutover",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )
    tmpdir = tempfile.mkdtemp(prefix="idis_enqueue_")
    compliant_store = ComplianceEnforcedStore(
        inner_store=FilesystemObjectStore(base_dir=Path(tmpdir))
    )
    audit_sink = InMemoryAuditSink()
    svc = IngestionService(compliant_store=compliant_store, audit_sink=audit_sink)
    app = create_app(
        audit_sink=audit_sink,
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        ingestion_service=svc,
        service_region="me-south-1",
    )
    tenant_ctx = TenantContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        name="Enqueue Cutover",
        timezone="UTC",
        data_region="me-south-1",
    )
    return TestClient(app, raise_server_exceptions=False), compliant_store, tenant_ctx


def _ingest_one_document(
    client: TestClient,
    compliant_store: ComplianceEnforcedStore,
    tenant_ctx: TenantContext,
    deal_id: str,
) -> str:
    headers = {"X-IDIS-API-Key": API_KEY, "Content-Type": "application/json"}
    pdf = _minimal_pdf()
    storage_key = f"enqueue/{uuid.uuid4()}.pdf"
    compliant_store.put(tenant_ctx=tenant_ctx, key=storage_key, data=pdf)
    resp = client.post(
        f"/v1/deals/{deal_id}/documents",
        headers=headers,
        json={
            "doc_type": "PITCH_DECK",
            "title": "enqueue-deck.pdf",
            "source_system": "api",
            "uri": f"file://{storage_key}",
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "auto_ingest": False,
        },
    )
    doc_id = resp.json()["doc_id"]
    assert (
        client.post(
            f"/v1/documents/{doc_id}/ingest", headers=headers, json={}
        ).status_code
        == 202
    )
    return doc_id


class TestEnqueueThenWorkerExecutes:
    def test_api_enqueues_and_worker_drives_to_terminal(
        self,
        _pg_admin_engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        admin_engine = _pg_admin_engine
        client, compliant_store, tenant_ctx = _client(monkeypatch)
        headers = {"X-IDIS-API-Key": API_KEY, "Content-Type": "application/json"}

        deal_resp = client.post(
            "/v1/deals",
            headers=headers,
            json={"name": "Enqueue Deal", "company_name": "Q Co"},
        )
        deal_id = deal_resp.json()["deal_id"]
        _ingest_one_document(client, compliant_store, tenant_ctx, deal_id)

        # --- Phase 1: API enqueue ---
        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            headers=headers,
            json={"mode": "SNAPSHOT"},
        )
        assert run_resp.status_code == 202, run_resp.text
        body = run_resp.json()
        run_id = body["run_id"]
        assert body["status"] == "QUEUED", (
            "POST /runs must be enqueue-only. A revert to inline execution "
            f"would return SUCCEEDED/FAILED here; got {body['status']!r}"
        )
        assert body["steps"] == []

        # Durable state post-enqueue: exactly one QUEUED run, no steps,
        # no finished_at.
        with admin_engine.begin() as conn:
            durable = conn.execute(
                text(
                    "SELECT status, finished_at FROM runs WHERE run_id = :r"
                ),
                {"r": run_id},
            ).fetchone()
            early_steps = conn.execute(
                text("SELECT step_name FROM run_steps WHERE run_id = :r"),
                {"r": run_id},
            ).fetchall()
        assert durable is not None
        assert durable.status == "QUEUED", (
            "durable runs row must stay QUEUED until the worker runs; "
            f"got {durable.status!r}"
        )
        assert durable.finished_at is None
        assert early_steps == [], (
            "run_steps must be empty until the worker executes"
        )

        # GET /v1/runs/{id} between enqueue and worker run also shows QUEUED.
        pre_get = client.get(
            f"/v1/runs/{run_id}", headers={"X-IDIS-API-Key": API_KEY}
        )
        assert pre_get.status_code == 200
        assert pre_get.json()["status"] == "QUEUED"

        # --- Phase 2: Worker drives the run ---
        asyncio.run(PipelineWorker(poll_interval=0)._process_queued_runs())

        # Durable state post-worker: SUCCEEDED, finished_at set,
        # run_steps populated with the full SNAPSHOT sequence.
        with admin_engine.begin() as conn:
            final = conn.execute(
                text(
                    "SELECT status, finished_at FROM runs WHERE run_id = :r"
                ),
                {"r": run_id},
            ).fetchone()
            step_rows = conn.execute(
                text("SELECT step_name FROM run_steps WHERE run_id = :r"),
                {"r": run_id},
            ).fetchall()

        assert final is not None
        assert final.status == "SUCCEEDED", (
            f"worker must advance the run to SUCCEEDED; got {final.status!r}"
        )
        assert final.finished_at is not None
        step_names = {r.step_name for r in step_rows}
        for expected in ("INGEST_CHECK", "EXTRACT", "GRADE", "CALC"):
            assert expected in step_names, (
                f"worker must write the {expected} step via RunOrchestrator; "
                f"saw {sorted(step_names)!r}"
            )

        # And the API reflects it now.
        post_get = client.get(
            f"/v1/runs/{run_id}", headers={"X-IDIS-API-Key": API_KEY}
        )
        assert post_get.status_code == 200
        assert post_get.json()["status"] == "SUCCEEDED"
