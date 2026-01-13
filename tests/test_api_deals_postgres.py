"""Postgres persistence integration tests for Deals API routes.

Tests that prove deals API routes properly read/write from Postgres with RLS:
- Test A: API reads deal inserted directly into Postgres
- Test B: API writes deal to Postgres (verified by direct query)
- Test C: Tenant isolation fail-closed (cross-tenant access blocked)

These tests require a real PostgreSQL instance and use:
- IDIS_DATABASE_ADMIN_URL for migrations and admin operations
- IDIS_DATABASE_URL for app-role operations

Run with: pytest -q tests/test_api_deals_postgres.py
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.persistence.db import set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Engine

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"

API_KEY_TENANT_A = "test-key-tenant-a-deals"
API_KEY_TENANT_B = "test-key-tenant-b-deals"
ACTOR_A_ID = "actor-deals-a"
ACTOR_B_ID = "actor-deals-b"


def _skip_or_fail_if_no_postgres() -> None:
    """Skip or fail test if PostgreSQL is not configured."""
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require_postgres = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"

    if not admin_url or not app_url:
        msg = f"PostgreSQL integration tests require {ADMIN_URL_ENV} and {APP_URL_ENV} env vars"
        if require_postgres:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        else:
            pytest.skip(msg)


@pytest.fixture(scope="module")
def admin_engine() -> Generator[Engine, None, None]:
    """Create admin engine for migrations and test setup."""
    _skip_or_fail_if_no_postgres()

    from idis.persistence.db import get_admin_engine, reset_engines

    engine = get_admin_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def app_engine() -> Generator[Engine, None, None]:
    """Create app engine for non-superuser operations."""
    _skip_or_fail_if_no_postgres()

    from idis.persistence.db import get_app_engine, reset_engines

    engine = get_app_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def migrated_db(admin_engine: Engine) -> Generator[None, None, None]:
    """Run migrations to set up schema before tests."""
    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg

    migrations_dir = os.path.dirname(migrations_pkg.__file__)

    config = Config()
    config.set_main_option("script_location", migrations_dir)

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")

    yield

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")


@pytest.fixture
def clean_deals_table(admin_engine: Engine, migrated_db: None) -> Generator[None, None, None]:
    """Clean deals table before and after each test."""
    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE deals CASCADE"))

    yield

    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE deals CASCADE"))


@pytest.fixture
def api_keys_config() -> dict[str, dict[str, str | list[str]]]:
    """Create API keys configuration for both tenants."""
    return {
        API_KEY_TENANT_A: {
            "tenant_id": TENANT_A_ID,
            "actor_id": ACTOR_A_ID,
            "name": "Test Tenant A",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST", "ADMIN"],
        },
        API_KEY_TENANT_B: {
            "tenant_id": TENANT_B_ID,
            "actor_id": ACTOR_B_ID,
            "name": "Test Tenant B",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST", "ADMIN"],
        },
    }


@pytest.fixture
def client_with_postgres(
    api_keys_config: dict[str, dict[str, str | list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    migrated_db: None,
) -> TestClient:
    """Create a test client with Postgres configured."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
    app = create_app()
    return TestClient(app)


class TestDealsAPIPostgresReadPath:
    """Tests proving API reads from Postgres."""

    def test_api_reads_deal_inserted_directly_into_postgres(
        self,
        app_engine: Engine,
        clean_deals_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """API GET /v1/deals/{deal_id} returns deal inserted directly into Postgres.

        This proves the route is reading from Postgres, not in-memory store.
        """
        deal_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, company_name, status, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :company_name, :status, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Postgres Inserted Deal",
                    "company_name": "Direct Insert Corp",
                    "status": "NEW",
                    "created_at": now,
                },
            )

        response = client_with_postgres.get(
            f"/v1/deals/{deal_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body["deal_id"] == deal_id
        assert body["name"] == "Postgres Inserted Deal"
        assert body["company_name"] == "Direct Insert Corp"
        assert body["status"] == "NEW"

    def test_api_list_deals_returns_postgres_data(
        self,
        app_engine: Engine,
        clean_deals_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """API GET /v1/deals returns deals from Postgres."""
        deal_ids = [str(uuid.uuid4()) for _ in range(3)]
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            for i, deal_id in enumerate(deal_ids):
                conn.execute(
                    text(
                        """
                        INSERT INTO deals (
                            deal_id, tenant_id, name, company_name, status, created_at
                        ) VALUES (
                            :deal_id, :tenant_id, :name, :company_name, :status, :created_at
                        )
                        """
                    ),
                    {
                        "deal_id": deal_id,
                        "tenant_id": TENANT_A_ID,
                        "name": f"Deal {i}",
                        "company_name": f"Company {i}",
                        "status": "NEW",
                        "created_at": now,
                    },
                )

        response = client_with_postgres.get(
            "/v1/deals",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 3
        returned_ids = {item["deal_id"] for item in body["items"]}
        assert returned_ids == set(deal_ids)


class TestDealsAPIPostgresWritePath:
    """Tests proving API writes to Postgres."""

    def test_api_creates_deal_in_postgres(
        self,
        app_engine: Engine,
        clean_deals_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """API POST /v1/deals creates deal that exists in Postgres.

        This proves the write path is DB-backed.
        """
        response = client_with_postgres.post(
            "/v1/deals",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
            json={
                "name": "API Created Deal",
                "company_name": "API Corp",
                "stage": "SEED",
                "tags": ["fintech", "saas"],
            },
        )

        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )
        body = response.json()
        created_deal_id = body["deal_id"]

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            result = conn.execute(
                text(
                    "SELECT deal_id, tenant_id, name, company_name, status "
                    "FROM deals WHERE deal_id = :deal_id"
                ),
                {"deal_id": created_deal_id},
            ).fetchone()

        assert result is not None, "Deal should exist in Postgres"
        assert str(result.deal_id) == created_deal_id
        assert str(result.tenant_id) == TENANT_A_ID
        assert result.name == "API Created Deal"
        assert result.company_name == "API Corp"
        assert result.status == "NEW"


class TestDealsAPITenantIsolation:
    """Tests proving RLS tenant isolation via API."""

    def test_cross_tenant_get_returns_404(
        self,
        app_engine: Engine,
        clean_deals_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Deal created under tenant A is not found when queried as tenant B.

        This proves RLS blocks cross-tenant reads at the API level.
        """
        deal_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, company_name, status, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :company_name, :status, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Tenant A Secret Deal",
                    "company_name": "Secret Corp",
                    "status": "NEW",
                    "created_at": now,
                },
            )

        response = client_with_postgres.get(
            f"/v1/deals/{deal_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )

        assert response.status_code == 404
        body = response.json()
        assert "not found" in body.get("message", "").lower() or body.get("code") == "not_found"

    def test_cross_tenant_list_returns_empty(
        self,
        app_engine: Engine,
        clean_deals_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Deals created under tenant A are not visible in tenant B's list.

        This proves RLS isolation on list operations.
        """
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            for i in range(3):
                conn.execute(
                    text(
                        """
                        INSERT INTO deals (
                            deal_id, tenant_id, name, company_name, status, created_at
                        ) VALUES (
                            :deal_id, :tenant_id, :name, :company_name, :status, :created_at
                        )
                        """
                    ),
                    {
                        "deal_id": str(uuid.uuid4()),
                        "tenant_id": TENANT_A_ID,
                        "name": f"Tenant A Deal {i}",
                        "company_name": f"Corp A {i}",
                        "status": "NEW",
                        "created_at": now,
                    },
                )

        response = client_with_postgres.get(
            "/v1/deals",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 0, "Tenant B should see no deals from tenant A"

    def test_tenant_sees_only_own_deals(
        self,
        app_engine: Engine,
        clean_deals_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Each tenant sees only their own deals in list."""
        now = datetime.now(UTC)
        tenant_a_deal_id = str(uuid.uuid4())
        tenant_b_deal_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, company_name, status, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :company_name, :status, :created_at)
                    """
                ),
                {
                    "deal_id": tenant_a_deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Tenant A Only",
                    "company_name": "A Corp",
                    "status": "NEW",
                    "created_at": now,
                },
            )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_B_ID)
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, company_name, status, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :company_name, :status, :created_at)
                    """
                ),
                {
                    "deal_id": tenant_b_deal_id,
                    "tenant_id": TENANT_B_ID,
                    "name": "Tenant B Only",
                    "company_name": "B Corp",
                    "status": "NEW",
                    "created_at": now,
                },
            )

        response_a = client_with_postgres.get(
            "/v1/deals",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert response_a.status_code == 200
        items_a = response_a.json()["items"]
        assert len(items_a) == 1
        assert items_a[0]["deal_id"] == tenant_a_deal_id

        response_b = client_with_postgres.get(
            "/v1/deals",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )
        assert response_b.status_code == 200
        items_b = response_b.json()["items"]
        assert len(items_b) == 1
        assert items_b[0]["deal_id"] == tenant_b_deal_id
