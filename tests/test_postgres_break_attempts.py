"""Postgres break-attempt integration tests.

Tests that prove the Postgres layer handles adversarial inputs correctly:
- JSONB round-trip with deeply nested structures
- SQL injection safety inside JSON fields
- Tenant isolation break attempts (cross-tenant access blocked)

These tests require a real PostgreSQL instance and use:
- IDIS_DATABASE_ADMIN_URL for migrations and admin operations
- IDIS_DATABASE_URL for app-role operations

Run with: pytest -q tests/test_postgres_break_attempts.py
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

if TYPE_CHECKING:
    from sqlalchemy import Engine

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_A_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

API_KEY_TENANT_A = "test-key-break-tenant-a"
API_KEY_TENANT_B = "test-key-break-tenant-b"
ACTOR_A_ID = "actor-break-a"
ACTOR_B_ID = "actor-break-b"


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
def clean_tables(admin_engine: Engine, migrated_db: None) -> Generator[None, None, None]:
    """Clean tables before and after each test."""
    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE sanads, claims, deals CASCADE"))

    yield

    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE sanads, claims, deals CASCADE"))


@pytest.fixture
def api_keys_config() -> dict[str, dict[str, str | list[str]]]:
    """Create API keys configuration for both tenants."""
    return {
        API_KEY_TENANT_A: {
            "tenant_id": TENANT_A_ID,
            "actor_id": ACTOR_A_ID,
            "name": "Break Test Tenant A",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST", "ADMIN"],
        },
        API_KEY_TENANT_B: {
            "tenant_id": TENANT_B_ID,
            "actor_id": ACTOR_B_ID,
            "name": "Break Test Tenant B",
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


def _create_deal_in_postgres(conn: object, tenant_id: str, deal_id: str) -> None:
    """Helper to create a deal in Postgres."""
    now = datetime.now(UTC)
    conn.execute(
        text(
            """
            INSERT INTO deals (deal_id, tenant_id, name, company_name, status, created_at)
            VALUES (:deal_id, :tenant_id, :name, :company_name, :status, :created_at)
            """
        ),
        {
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "name": "Test Deal for Break Attempts",
            "company_name": "Break Test Corp",
            "status": "NEW",
            "created_at": now,
        },
    )


class TestJSONBRoundTrip:
    """Tests proving JSONB round-trip with deeply nested structures."""

    def test_deeply_nested_corroboration_round_trips(
        self,
        client_with_postgres: TestClient,
        admin_engine: Engine,
        clean_tables: None,
    ) -> None:
        """Create a claim with deeply nested JSONB and verify round-trip."""
        deal_id = str(uuid.uuid4())
        with admin_engine.begin() as conn:
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)

        deeply_nested = {
            "level": "MUTAWATIR",
            "independent_chain_count": 3,
            "metadata": {
                "layer1": {
                    "layer2": {
                        "layer3": {
                            "layer4": {
                                "layer5": {
                                    "value": "deeply_nested_value",
                                    "numbers": [1, 2, 3, 4, 5],
                                    "booleans": {"true_val": True, "false_val": False},
                                    "null_val": None,
                                }
                            }
                        }
                    }
                }
            },
            "arrays": [[1, 2], [3, [4, 5, [6, 7]]]],
            "special_chars": {"unicode": "\u00e9\u00e8\u00ea", "newlines": "a\nb\nc"},
        }

        response = client_with_postgres.post(
            f"/v1/deals/{deal_id}/claims",
            json={
                "claim_class": "FINANCIAL",
                "claim_text": "Test deeply nested JSONB round-trip",
                "materiality": "HIGH",
                "ic_bound": False,
            },
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert response.status_code == 201, response.text
        claim_id = response.json()["claim_id"]

        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE claims SET corroboration = CAST(:corroboration AS JSONB)
                    WHERE claim_id = :claim_id
                    """
                ),
                {"claim_id": claim_id, "corroboration": json.dumps(deeply_nested)},
            )

        get_response = client_with_postgres.get(
            f"/v1/claims/{claim_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert get_response.status_code == 200, get_response.text

        with admin_engine.begin() as conn:
            result = conn.execute(
                text("SELECT corroboration FROM claims WHERE claim_id = :claim_id"),
                {"claim_id": claim_id},
            )
            row = result.fetchone()
            assert row is not None
            stored = row[0]
            if isinstance(stored, str):
                stored = json.loads(stored)

            assert stored["level"] == deeply_nested["level"]
            assert (
                stored["metadata"]["layer1"]["layer2"]["layer3"]["layer4"]["layer5"]["value"]
                == "deeply_nested_value"
            )
            assert stored["arrays"] == deeply_nested["arrays"]


class TestInjectionSafety:
    """Tests proving SQL injection safety inside JSON fields."""

    def test_injection_string_stored_as_data(
        self,
        client_with_postgres: TestClient,
        admin_engine: Engine,
        clean_tables: None,
    ) -> None:
        """Injection string in JSON field is stored as data, not executed."""
        deal_id = str(uuid.uuid4())
        with admin_engine.begin() as conn:
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)

        injection_string = "'; DROP TABLE claims; --"

        response = client_with_postgres.post(
            f"/v1/deals/{deal_id}/claims",
            json={
                "claim_class": "FINANCIAL",
                "claim_text": f"Test with injection: {injection_string}",
                "materiality": "HIGH",
                "ic_bound": False,
            },
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert response.status_code == 201, response.text
        claim_id = response.json()["claim_id"]

        get_response = client_with_postgres.get(
            f"/v1/claims/{claim_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert get_response.status_code == 200
        assert injection_string in get_response.json()["claim_text"]

        with admin_engine.begin() as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'claims'")
            )
            count = result.scalar()
            assert count == 1, "claims table should still exist after injection attempt"

        second_claim_response = client_with_postgres.post(
            f"/v1/deals/{deal_id}/claims",
            json={
                "claim_class": "OTHER",
                "claim_text": "Claim after injection attempt",
                "materiality": "LOW",
                "ic_bound": False,
            },
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert second_claim_response.status_code == 201, (
            "Should be able to create claims after injection attempt"
        )


class TestTenantIsolationBreakAttempts:
    """Tests proving tenant isolation prevents cross-tenant access."""

    def test_cross_tenant_get_returns_404_not_forbidden(
        self,
        client_with_postgres: TestClient,
        admin_engine: Engine,
        clean_tables: None,
    ) -> None:
        """Accessing tenant A's claim with tenant B's key returns 404 (no existence leak)."""
        deal_id = str(uuid.uuid4())
        with admin_engine.begin() as conn:
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)

        create_response = client_with_postgres.post(
            f"/v1/deals/{deal_id}/claims",
            json={
                "claim_class": "FINANCIAL",
                "claim_text": "Tenant A secret claim",
                "materiality": "HIGH",
                "ic_bound": False,
            },
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert create_response.status_code == 201
        claim_id = create_response.json()["claim_id"]

        cross_tenant_response = client_with_postgres.get(
            f"/v1/claims/{claim_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )

        assert cross_tenant_response.status_code == 404
        error_detail = cross_tenant_response.json().get("detail", "")
        assert "forbidden" not in error_detail.lower()
        assert "access" not in error_detail.lower()
        assert "permission" not in error_detail.lower()

    def test_cross_tenant_list_returns_empty_not_filtered(
        self,
        client_with_postgres: TestClient,
        admin_engine: Engine,
        clean_tables: None,
    ) -> None:
        """Listing with tenant B's key returns empty list, not filtered list."""
        deal_id_a = str(uuid.uuid4())
        deal_id_b = str(uuid.uuid4())

        with admin_engine.begin() as conn:
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id_a)
            _create_deal_in_postgres(conn, TENANT_B_ID, deal_id_b)

        for i in range(3):
            client_with_postgres.post(
                f"/v1/deals/{deal_id_a}/claims",
                json={
                    "claim_class": "FINANCIAL",
                    "claim_text": f"Tenant A claim {i}",
                    "materiality": "HIGH",
                    "ic_bound": False,
                },
                headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
            )

        list_response_a = client_with_postgres.get(
            f"/v1/deals/{deal_id_a}/claims",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert list_response_a.status_code == 200
        assert len(list_response_a.json()["items"]) == 3

        cross_tenant_deal_response = client_with_postgres.get(
            f"/v1/deals/{deal_id_a}/claims",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )
        assert cross_tenant_deal_response.status_code == 404

        list_response_b = client_with_postgres.get(
            f"/v1/deals/{deal_id_b}/claims",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )
        assert list_response_b.status_code == 200
        assert len(list_response_b.json()["items"]) == 0

    def test_tenant_isolation_no_cross_tenant_modification(
        self,
        client_with_postgres: TestClient,
        admin_engine: Engine,
        clean_tables: None,
    ) -> None:
        """Tenant B cannot see or infer existence of tenant A's deal."""
        deal_id = str(uuid.uuid4())
        with admin_engine.begin() as conn:
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)

        get_deal_b = client_with_postgres.get(
            f"/v1/deals/{deal_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )
        assert get_deal_b.status_code == 404

        create_claim_b = client_with_postgres.post(
            f"/v1/deals/{deal_id}/claims",
            json={
                "claim_class": "FINANCIAL",
                "claim_text": "Tenant B trying to add to A's deal",
                "materiality": "LOW",
                "ic_bound": False,
            },
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )
        assert create_claim_b.status_code == 404

        get_deal_a = client_with_postgres.get(
            f"/v1/deals/{deal_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert get_deal_a.status_code == 200
