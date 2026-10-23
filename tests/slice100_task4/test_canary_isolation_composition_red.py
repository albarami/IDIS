"""RED: the Task-4 isolation canary must reach durable deal-scope validation.

This test uses the production ``create_app`` composition root against a fresh,
disposable PostgreSQL database and its NOSUPERUSER/NOBYPASSRLS application role.
It deliberately calls the gate's complete isolation matrix, rather than copying
the upload request into a test-only seam, so request-model drift cannot be masked.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.postgres_sink import PostgresAuditSink
from idis.evaluation import real_example_gate as gate
from idis.idempotency.postgres_store import PostgresIdempotencyStore
from idis.persistence.db import reset_engines
from idis.persistence.repositories.data_room_packages import clear_data_room_packages_store
from tests.abac_seed import seed_deal_access
from tests.slice100_task2b._support import (
    TENANT_TABLES,
    disposable_postgres,
    grant_application_access,
    migrate,
)

OWNER_TENANT_ID = "61000000-0000-4000-8000-000000000001"
CANARY_TENANT_ID = "61000000-0000-4000-8000-000000000002"
OWNER_ACTOR_ID = "task4-composition-owner"
CANARY_ACTOR_ID = "task4-composition-isolation-canary"
OWNER_KEY = "task4-composition-owner-key"
CANARY_KEY = "task4-composition-canary-key"


class _RecordingProductionClient:
    """Delegate to TestClient while retaining only safe request/status metadata."""

    def __init__(self, client: TestClient) -> None:
        self._client = client
        self.posts: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._client.get(url, **kwargs)

    def stream(self, method: str, url: str, **kwargs: Any) -> Any:
        return self._client.stream(method, url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        response = self._client.post(url, **kwargs)
        self.posts.append(
            {
                "path": url,
                "status_code": response.status_code,
                "params": dict(kwargs.get("params") or {}),
                "response_code": response.json().get("code")
                if response.headers.get("content-type", "").startswith("application/json")
                and isinstance(response.json(), dict)
                else None,
            }
        )
        return response

    def delete(self, url: str, **kwargs: Any) -> Any:
        return self._client.delete(url, **kwargs)


def _auth_registry() -> str:
    return json.dumps(
        {
            OWNER_KEY: {
                "tenant_id": OWNER_TENANT_ID,
                "actor_id": OWNER_ACTOR_ID,
                "name": "Fictional Task4 Owner",
                "timezone": "UTC",
                "data_region": "me-south-1",
                "roles": ["ADMIN", "ANALYST"],
            },
            CANARY_KEY: {
                "tenant_id": CANARY_TENANT_ID,
                "actor_id": CANARY_ACTOR_ID,
                "name": "Fictional Task4 Isolation Canary",
                "timezone": "UTC",
                "data_region": "me-south-1",
                "roles": ["ADMIN", "ANALYST"],
            },
        },
        sort_keys=True,
    )


def _tenant_database_snapshot(admin_engine: Any) -> tuple[tuple[str, int], ...]:
    """Count every tenant-bearing table for exactly the two test tenants."""

    snapshot: list[tuple[str, int]] = []
    with admin_engine.connect() as connection:
        for table in TENANT_TABLES:
            # ``table`` comes only from the reviewed static migration inventory above.
            count = connection.execute(
                text(
                    f'SELECT COUNT(*) FROM "{table}" '
                    "WHERE tenant_id::text IN (:owner_tenant_id, :canary_tenant_id)"
                ),
                {
                    "owner_tenant_id": OWNER_TENANT_ID,
                    "canary_tenant_id": CANARY_TENANT_ID,
                },
            ).scalar_one()
            snapshot.append((table, int(count)))
    return tuple(snapshot)


def _object_store_snapshot(root: Path) -> tuple[tuple[str, int, str], ...]:
    if not root.exists():
        return ()
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
    )


def _seed_owner_resources(client: TestClient) -> tuple[dict[str, Any], list[dict[str, str]]]:
    companies: list[dict[str, str]] = []
    terminal_rows: list[dict[str, str]] = []
    for sequence, alias in enumerate(("company-001", "company-002"), start=1):
        deal_response = client.post(
            "/v1/deals",
            headers={"X-IDIS-API-Key": OWNER_KEY},
            json={"name": f"{alias}-deal", "company_name": alias},
        )
        assert deal_response.status_code == 201, deal_response.text
        deal_id = str(deal_response.json()["deal_id"])
        seed_deal_access(OWNER_TENANT_ID, deal_id, OWNER_ACTOR_ID)

        payload = f"Fictional durable evidence {sequence}.".encode()
        upload_response = client.post(
            f"/v1/deals/{deal_id}/documents/upload",
            headers={
                "X-IDIS-API-Key": OWNER_KEY,
                "Content-Type": "application/octet-stream",
            },
            params={
                "filename": f"{alias}-document-00001.txt",
                "doc_type": "DATA_ROOM_FILE",
                "source_system": "slice100-canary-composition-red",
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            content=payload,
        )
        assert upload_response.status_code == 201, upload_response.text
        upload = upload_response.json()

        package_response = client.post(
            f"/v1/deals/{deal_id}/data-room-packages",
            headers={"X-IDIS-API-Key": OWNER_KEY},
            json={"document_ids": [str(upload["document_id"])]},
        )
        assert package_response.status_code == 201, package_response.text
        companies.append(
            {
                "company_alias": alias,
                "deal_id": deal_id,
                "package_id": str(package_response.json()["package_id"]),
            }
        )
        terminal_rows.append(
            {
                "company_alias": alias,
                "document_id": str(upload["document_id"]),
                "doc_id": str(upload["doc_id"]),
            }
        )
    return {"companies": companies}, terminal_rows


def test_isolation_canary_upload_reaches_real_rls_scope_check_without_mutation(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """The former HTTP 422 must become an RLS-backed 404 with no mutation."""

    object_root = tmp_path / "durable-objects"
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0035")
        grant_application_access(database)
        monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
        monkeypatch.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)
        monkeypatch.setenv(
            "IDIS_DATABASE_ADMIN_URL",
            database.admin_engine.url.render_as_string(hide_password=False),
        )
        monkeypatch.setenv(
            "IDIS_DATABASE_URL",
            database.app_engine.url.render_as_string(hide_password=False),
        )
        monkeypatch.setenv(IDIS_API_KEYS_ENV, _auth_registry())
        monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(object_root))
        reset_engines()
        clear_deals_store()
        clear_document_store()
        clear_data_room_packages_store()

        audit_sink = PostgresAuditSink()
        app = create_app(
            audit_sink=audit_sink,
            postgres_audit_sink=audit_sink,
            postgres_idempotency_store=PostgresIdempotencyStore(),
        )
        client = TestClient(app, raise_server_exceptions=False)
        preparation, terminal_rows = _seed_owner_resources(client)
        database_before = _tenant_database_snapshot(database.admin_engine)
        objects_before = _object_store_snapshot(object_root)

        recording_client = _RecordingProductionClient(client)
        caught: gate.DurableIngestionGateError | None = None
        receipt: dict[str, Any] | None = None
        try:
            receipt = gate._verify_tenant_isolation(
                api_client=recording_client,
                canary_api_key=CANARY_KEY,
                preparation_summary=preparation,
                terminal_rows=terminal_rows,
            )
        except gate.DurableIngestionGateError as exc:
            caught = exc

        database_after = _tenant_database_snapshot(database.admin_engine)
        objects_after = _object_store_snapshot(object_root)
        assert database_after == database_before
        assert objects_after == objects_before
        assert dict(database_after)["audit_events"] == dict(database_before)["audit_events"]
        assert (
            dict(database_after)["idempotency_records"]
            == dict(database_before)["idempotency_records"]
        )

        uploads = [
            call for call in recording_client.posts if call["path"].endswith("/documents/upload")
        ]
        assert len(uploads) == 2
        for upload in uploads:
            assert upload["status_code"] == 404, (
                "production isolation canary must pass request-model validation and reach the "
                "RLS-backed non-enumerating deal-scope check; observed "
                f"HTTP {upload['status_code']} code={upload['response_code']} "
                f"doc_type={upload['params'].get('doc_type')}"
            )
            assert upload["response_code"] == "NOT_FOUND"
            assert upload["params"]["doc_type"] == "DATA_ROOM_FILE"
        assert caught is None
        assert receipt == {
            "status": "passed",
            "company_count": 2,
            "denial_count": 18,
            "surface_count": 9,
        }

        client.close()
        reset_engines()
