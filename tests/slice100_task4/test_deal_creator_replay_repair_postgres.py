"""Real-Postgres proof for the createDeal replay compatibility repair.

The test creates and drops a disposable database.  It never uses the configured
application database and never calls the running API.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

import idis.persistence.migrations as migrations_pkg
from idis.api.deal_creator_replay import repair_create_deal_replay
from idis.audit.postgres_sink import PostgresAuditSink
from idis.idempotency.postgres_store import PostgresIdempotencyStore
from idis.idempotency.store import ScopeKey
from idis.persistence.db import set_tenant_local
from tests.slice100_task2.test_durable_governance_migration import (
    _create_disposable_engine,
    _run_alembic,
)

TENANT_ID = "51000000-0000-4000-8000-000000000001"
OTHER_TENANT_ID = "51000000-0000-4000-8000-000000000002"
DEAL_ID = "52000000-0000-4000-8000-000000000001"
ACTOR_ID = "task4-owner-service"
RAW_KEY = "task4-disposable-create-deal-key"
PAYLOAD_SHA256 = "sha256:" + "b" * 64
DEAL_CREATED_AT = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)
IDEMPOTENCY_CREATED_AT = datetime(2026, 7, 17, 9, 0, 1, tzinfo=UTC)
AUDIT_CREATED_AT = datetime(2026, 7, 17, 9, 0, 2, tzinfo=UTC)


def _require_disposable_postgres() -> None:
    required = ("IDIS_DATABASE_ADMIN_URL", "IDIS_DATABASE_URL")
    if all(os.environ.get(name) for name in required):
        return
    message = "Task 4 replay repair requires admin and app Postgres URLs"
    if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1":
        pytest.fail(f"REQUIRED: {message}")
    pytest.skip(message)


def _response_body() -> bytes:
    return json.dumps(
        {
            "deal_id": DEAL_ID,
            "name": "company-001-deal",
            "company_name": "company-001",
            "status": "NEW",
            "stage": None,
            "tags": [],
            "created_at": DEAL_CREATED_AT.isoformat().replace("+00:00", "Z"),
            "updated_at": None,
        },
        separators=(",", ":"),
    ).encode()


def _creation_event(event_id: str) -> dict[str, Any]:
    key_hash = sha256(RAW_KEY.encode()).hexdigest()
    return {
        "event_id": event_id,
        "occurred_at": AUDIT_CREATED_AT.isoformat().replace("+00:00", "Z"),
        "tenant_id": TENANT_ID,
        "actor": {
            "actor_type": "SERVICE",
            "actor_id": ACTOR_ID,
            "roles": ["INTEGRATION_SERVICE"],
            "ip": "127.0.0.1",
            "user_agent": "task4-disposable-test",
        },
        "request": {
            "request_id": "request-original",
            "method": "POST",
            "path": "/v1/deals",
            "status_code": 201,
            "idempotency_key_sha256": key_hash,
        },
        "resource": {"resource_type": "deal", "resource_id": DEAL_ID},
        "event_type": "deal.created",
        "severity": "MEDIUM",
        "summary": "deal.created via POST /v1/deals",
        "payload": {"hashes": [PAYLOAD_SHA256], "refs": []},
    }


def _repair_event(event_id: str) -> dict[str, Any]:
    event = _creation_event(event_id)
    event["occurred_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    event["request"] = {
        **event["request"],
        "request_id": "request-repair",
    }
    event["event_type"] = "rbac.assignment.created"
    event["summary"] = "rbac.assignment.created via POST /v1/deals"
    return event


def _grant_app_role(admin_engine: Any, app_user: str) -> None:
    quoted = admin_engine.dialect.identifier_preparer.quote(app_user)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {quoted}")
        connection.exec_driver_sql(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {quoted}"
        )
        connection.exec_driver_sql(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted}"
        )


def _seed_historical_gap(admin_engine: Any) -> None:
    event_id = str(uuid.uuid4())
    event = _creation_event(event_id)
    key_hash = sha256(RAW_KEY.encode()).hexdigest()
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO deals (
                    deal_id, tenant_id, name, company_name, status, stage,
                    tags, created_at, updated_at
                ) VALUES (
                    CAST(:deal_id AS uuid), CAST(:tenant_id AS uuid), :name,
                    :company_name, 'NEW', NULL, CAST('[]' AS jsonb),
                    :created_at, NULL
                )
                """
            ),
            {
                "deal_id": DEAL_ID,
                "tenant_id": TENANT_ID,
                "name": "company-001-deal",
                "company_name": "company-001",
                "created_at": DEAL_CREATED_AT,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO idempotency_records (
                    tenant_id, actor_id, method, operation_id, idempotency_key,
                    payload_sha256, status_code, media_type, body_bytes, created_at
                ) VALUES (
                    CAST(:tenant_id AS uuid), :actor_id, 'POST', 'createDeal',
                    :idempotency_key, :payload_sha256, 201, 'application/json',
                    :body_bytes, :created_at
                )
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "actor_id": ACTOR_ID,
                "idempotency_key": RAW_KEY,
                "payload_sha256": PAYLOAD_SHA256,
                "body_bytes": _response_body(),
                "created_at": IDEMPOTENCY_CREATED_AT,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO audit_events (
                    event_id, tenant_id, occurred_at, event_type, request_id,
                    idempotency_key, event
                ) VALUES (
                    CAST(:event_id AS uuid), CAST(:tenant_id AS uuid), :occurred_at,
                    'deal.created', 'request-original', :idempotency_key,
                    CAST(:event AS jsonb)
                )
                """
            ),
            {
                "event_id": event_id,
                "tenant_id": TENANT_ID,
                "occurred_at": AUDIT_CREATED_AT,
                "idempotency_key": key_hash,
                "event": json.dumps(event, sort_keys=True, separators=(",", ":")),
            },
        )


def test_real_postgres_repair_honors_rls_and_is_idempotent() -> None:
    _require_disposable_postgres()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    admin_engine, cleanup = _create_disposable_engine()
    app_engine = None
    try:
        _run_alembic(config, admin_engine, command.upgrade, "head")
        app_url = make_url(os.environ["IDIS_DATABASE_URL"]).set(database=admin_engine.url.database)
        assert app_url.username is not None
        _grant_app_role(admin_engine, app_url.username)
        app_engine = create_engine(app_url)

        with admin_engine.connect() as connection:
            role = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :role"),
                {"role": app_url.username},
            ).one()
        assert role == (False, False)
        _seed_historical_gap(admin_engine)

        scope = ScopeKey(TENANT_ID, ACTOR_ID, "POST", "createDeal", RAW_KEY)
        store = PostgresIdempotencyStore()
        with app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            record = store.get(scope, conn=connection)
            assert record is not None
            outcome = repair_create_deal_replay(
                connection,
                scope_key=scope,
                record=record,
            )
            assert outcome.deal_id == DEAL_ID
            assert outcome.repaired is True
            PostgresAuditSink().emit_in_tx(connection, _repair_event(str(uuid.uuid4())))

        with app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            record = store.get(scope, conn=connection)
            assert record is not None
            assert (
                repair_create_deal_replay(
                    connection,
                    scope_key=scope,
                    record=record,
                ).repaired
                is False
            )

        with app_engine.begin() as connection:
            set_tenant_local(connection, OTHER_TENANT_ID)
            assert (
                store.get(
                    ScopeKey(
                        OTHER_TENANT_ID,
                        ACTOR_ID,
                        "POST",
                        "createDeal",
                        RAW_KEY,
                    ),
                    conn=connection,
                )
                is None
            )

        with admin_engine.connect() as connection:
            assignment_count = connection.execute(
                text(
                    """
                    SELECT count(*) FROM deal_assignments
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND assignee_type = 'ACTOR'
                      AND assignee_id = :actor_id
                    """
                ),
                {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "actor_id": ACTOR_ID},
            ).scalar_one()
            event_types = connection.execute(
                text(
                    """
                    SELECT event_type, count(*)
                    FROM audit_events
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND event -> 'resource' ->> 'resource_id' = :deal_id
                    GROUP BY event_type
                    ORDER BY event_type
                    """
                ),
                {"tenant_id": TENANT_ID, "deal_id": DEAL_ID},
            ).all()
        assert assignment_count == 1
        assert event_types == [("deal.created", 1), ("rbac.assignment.created", 1)]
    finally:
        if app_engine is not None:
            app_engine.dispose()
        cleanup()
