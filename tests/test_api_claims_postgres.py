"""Postgres persistence integration tests for Claims API routes.

Tests that prove claims API routes properly read/write from Postgres with RLS:
- Test A: API reads claim inserted directly into Postgres
- Test B: API writes claim to Postgres (verified by direct query)
- Test C: Tenant isolation fail-closed (cross-tenant access blocked)

These tests require a real PostgreSQL instance and use:
- IDIS_DATABASE_ADMIN_URL for migrations and admin operations
- IDIS_DATABASE_URL for app-role operations

Run with: pytest -q tests/test_api_claims_postgres.py
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

from idis.api.abac import (
    InMemoryDealAssignmentStore,
    get_deal_assignment_store,
)
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.persistence.db import set_tenant_local


def _assign_actor_to_deal(tenant_id: str, deal_id: str, actor_id: str) -> None:
    """Helper to assign an actor to a deal for ABAC access."""
    store = get_deal_assignment_store()
    if isinstance(store, InMemoryDealAssignmentStore):
        store.add_assignment(tenant_id, deal_id, actor_id)


if TYPE_CHECKING:
    from sqlalchemy import Engine

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"

API_KEY_TENANT_A = "test-key-tenant-a-claims"
API_KEY_TENANT_B = "test-key-tenant-b-claims"
ACTOR_A_ID = "actor-claims-a"
ACTOR_B_ID = "actor-claims-b"


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
    """Clean claims and deals tables before and after each test."""
    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE sanads, claims, deals CASCADE"))

    yield

    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE sanads, claims, deals CASCADE"))


@pytest.fixture
def api_keys_config(test_tenant_data_region: str) -> dict[str, dict[str, str | list[str]]]:
    """Create API keys configuration for both tenants."""
    return {
        API_KEY_TENANT_A: {
            "tenant_id": TENANT_A_ID,
            "actor_id": ACTOR_A_ID,
            "name": "Test Tenant A",
            "timezone": "UTC",
            "data_region": test_tenant_data_region,
            "roles": ["ANALYST", "ADMIN"],
        },
        API_KEY_TENANT_B: {
            "tenant_id": TENANT_B_ID,
            "actor_id": ACTOR_B_ID,
            "name": "Test Tenant B",
            "timezone": "UTC",
            "data_region": test_tenant_data_region,
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
    """Helper to create a deal in Postgres (required FK for claims)."""
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
            "name": "Test Deal for Claims",
            "company_name": "Test Corp",
            "status": "NEW",
            "created_at": now,
        },
    )


def _create_claim_in_postgres(
    conn: object,
    tenant_id: str,
    claim_id: str,
    deal_id: str,
    claim_text: str = "Test claim text",
    claim_class: str = "QUANTITATIVE",
    claim_grade: str = "B",
    claim_verdict: str = "VERIFIED",
) -> None:
    """Helper to create a claim directly in Postgres."""
    now = datetime.now(UTC)
    corroboration = json.dumps({"level": "AHAD", "independent_chain_count": 1})

    conn.execute(
        text(
            """
            INSERT INTO claims (
                claim_id, tenant_id, deal_id, claim_class, claim_text,
                claim_grade, corroboration, claim_verdict, claim_action,
                materiality, ic_bound, created_at
            ) VALUES (
                :claim_id, :tenant_id, :deal_id, :claim_class, :claim_text,
                :claim_grade, CAST(:corroboration AS JSONB), :claim_verdict,
                :claim_action, :materiality, :ic_bound, :created_at
            )
            """
        ),
        {
            "claim_id": claim_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "claim_class": claim_class,
            "claim_text": claim_text,
            "claim_grade": claim_grade,
            "corroboration": corroboration,
            "claim_verdict": claim_verdict,
            "claim_action": "ACCEPT",
            "materiality": "MEDIUM",
            "ic_bound": False,
            "created_at": now,
        },
    )


class TestClaimsRepositoryPostgresReadPath:
    """Tests proving claims repository reads from Postgres."""

    def test_claims_repository_reads_from_postgres(
        self,
        app_engine: Engine,
        clean_tables: None,
    ) -> None:
        """ClaimsRepository.get returns claim inserted directly into Postgres.

        This proves the repository is reading from Postgres, not in-memory.
        """
        from idis.persistence.repositories.claims import ClaimsRepository

        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)
            _create_claim_in_postgres(
                conn,
                TENANT_A_ID,
                claim_id,
                deal_id,
                claim_text="Postgres Direct Insert Claim",
                claim_grade="A",
            )

        with app_engine.begin() as conn:
            repo = ClaimsRepository(conn, TENANT_A_ID)
            result = repo.get(claim_id)

        assert result is not None, "Claim should be found via repository"
        assert result["claim_id"] == claim_id
        assert result["claim_text"] == "Postgres Direct Insert Claim"
        assert result["claim_grade"] == "A"

    def test_claims_repository_list_by_deal_from_postgres(
        self,
        app_engine: Engine,
        clean_tables: None,
    ) -> None:
        """ClaimsRepository.list_by_deal returns claims from Postgres."""
        from idis.persistence.repositories.claims import ClaimsRepository

        deal_id = str(uuid.uuid4())
        claim_ids = [str(uuid.uuid4()) for _ in range(3)]

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)
            for i, claim_id in enumerate(claim_ids):
                _create_claim_in_postgres(
                    conn,
                    TENANT_A_ID,
                    claim_id,
                    deal_id,
                    claim_text=f"Claim {i}",
                )

        with app_engine.begin() as conn:
            repo = ClaimsRepository(conn, TENANT_A_ID)
            claims, next_cursor = repo.list_by_deal(deal_id)

        assert len(claims) == 3
        returned_ids = {c["claim_id"] for c in claims}
        assert returned_ids == set(claim_ids)


class TestClaimsRepositoryPostgresWritePath:
    """Tests proving claims repository writes to Postgres."""

    def test_claims_repository_creates_in_postgres(
        self,
        app_engine: Engine,
        clean_tables: None,
    ) -> None:
        """ClaimsRepository.create persists claim to Postgres.

        This proves the write path is DB-backed.
        """
        from idis.persistence.repositories.claims import ClaimsRepository

        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)

        with app_engine.begin() as conn:
            repo = ClaimsRepository(conn, TENANT_A_ID)
            repo.create(
                claim_id=claim_id,
                deal_id=deal_id,
                claim_class="QUANTITATIVE",
                claim_text="Repository Created Claim",
                claim_grade="B",
                claim_verdict="VERIFIED",
            )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            result = conn.execute(
                text(
                    "SELECT claim_id, tenant_id, claim_text FROM claims WHERE claim_id = :claim_id"
                ),
                {"claim_id": claim_id},
            ).fetchone()

        assert result is not None, "Claim should exist in Postgres"
        assert str(result.claim_id) == claim_id
        assert str(result.tenant_id) == TENANT_A_ID
        assert result.claim_text == "Repository Created Claim"


class TestClaimsRepositoryTenantIsolation:
    """Tests proving RLS tenant isolation for claims."""

    def test_cross_tenant_claim_get_returns_none(
        self,
        app_engine: Engine,
        clean_tables: None,
    ) -> None:
        """Claim created under tenant A is not visible to tenant B via repository.

        This proves RLS blocks cross-tenant reads.
        """
        from idis.persistence.repositories.claims import ClaimsRepository

        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)
            _create_claim_in_postgres(
                conn,
                TENANT_A_ID,
                claim_id,
                deal_id,
                claim_text="Tenant A Secret Claim",
            )

        with app_engine.begin() as conn:
            repo_b = ClaimsRepository(conn, TENANT_B_ID)
            result = repo_b.get(claim_id)

        assert result is None, "Tenant B should not see tenant A's claim"

    def test_cross_tenant_list_by_deal_returns_empty(
        self,
        app_engine: Engine,
        clean_tables: None,
    ) -> None:
        """Claims created under tenant A are not visible in tenant B's list.

        This proves RLS isolation on list operations.
        """
        from idis.persistence.repositories.claims import ClaimsRepository

        deal_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)
            for i in range(3):
                _create_claim_in_postgres(
                    conn,
                    TENANT_A_ID,
                    str(uuid.uuid4()),
                    deal_id,
                    claim_text=f"Tenant A Claim {i}",
                )

        with app_engine.begin() as conn:
            repo_b = ClaimsRepository(conn, TENANT_B_ID)
            claims, _ = repo_b.list_by_deal(deal_id)

        assert len(claims) == 0, "Tenant B should see no claims from tenant A"

    def test_tenant_sees_only_own_claims(
        self,
        app_engine: Engine,
        clean_tables: None,
    ) -> None:
        """Each tenant sees only their own claims."""
        from idis.persistence.repositories.claims import ClaimsRepository

        deal_id_a = str(uuid.uuid4())
        deal_id_b = str(uuid.uuid4())
        claim_id_a = str(uuid.uuid4())
        claim_id_b = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id_a)
            _create_claim_in_postgres(
                conn,
                TENANT_A_ID,
                claim_id_a,
                deal_id_a,
                claim_text="Tenant A Only Claim",
            )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_B_ID)
            _create_deal_in_postgres(conn, TENANT_B_ID, deal_id_b)
            _create_claim_in_postgres(
                conn,
                TENANT_B_ID,
                claim_id_b,
                deal_id_b,
                claim_text="Tenant B Only Claim",
            )

        with app_engine.begin() as conn:
            repo_a = ClaimsRepository(conn, TENANT_A_ID)
            result_a = repo_a.get(claim_id_a)

        assert result_a is not None
        assert result_a["claim_text"] == "Tenant A Only Claim"

        with app_engine.begin() as conn:
            repo_b = ClaimsRepository(conn, TENANT_B_ID)
            result_b = repo_b.get(claim_id_b)

        assert result_b is not None
        assert result_b["claim_text"] == "Tenant B Only Claim"

        with app_engine.begin() as conn:
            repo_a = ClaimsRepository(conn, TENANT_A_ID)
            cross_result = repo_a.get(claim_id_b)

        assert cross_result is None, "Tenant A should not see tenant B's claim"


class TestSanadsRepositoryPostgres:
    """Tests for Sanads repository Postgres operations."""

    def test_sanads_repository_creates_and_reads_from_postgres(
        self,
        app_engine: Engine,
        clean_tables: None,
    ) -> None:
        """SanadsRepository creates and retrieves sanad from Postgres."""
        from idis.persistence.repositories.claims import SanadsRepository

        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        sanad_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)
            _create_claim_in_postgres(conn, TENANT_A_ID, claim_id, deal_id)

        with app_engine.begin() as conn:
            repo = SanadsRepository(conn, TENANT_A_ID)
            repo.create(
                sanad_id=sanad_id,
                claim_id=claim_id,
                deal_id=deal_id,
                primary_evidence_id="evidence-001",
                corroborating_evidence_ids=["evidence-002"],
                transmission_chain=[
                    {
                        "node_id": "node-001",
                        "node_type": "EXTRACTION",
                        "actor_type": "SERVICE",
                        "actor_id": "extractor",
                        "input_refs": [],
                        "output_refs": [],
                        "timestamp": "2026-01-13T00:00:00Z",
                    }
                ],
                computed={
                    "grade": "B",
                    "corroboration_level": "AHAD",
                    "independent_chain_count": 1,
                },
            )

        with app_engine.begin() as conn:
            repo = SanadsRepository(conn, TENANT_A_ID)
            result = repo.get(sanad_id)

        assert result is not None
        assert result["sanad_id"] == sanad_id
        assert result["primary_evidence_id"] == "evidence-001"
        assert result["computed"]["grade"] == "B"

    def test_sanads_cross_tenant_isolation(
        self,
        app_engine: Engine,
        clean_tables: None,
    ) -> None:
        """Sanad created under tenant A is not visible to tenant B."""
        from idis.persistence.repositories.claims import SanadsRepository

        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        sanad_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)
            _create_claim_in_postgres(conn, TENANT_A_ID, claim_id, deal_id)

        with app_engine.begin() as conn:
            repo = SanadsRepository(conn, TENANT_A_ID)
            repo.create(
                sanad_id=sanad_id,
                claim_id=claim_id,
                deal_id=deal_id,
                primary_evidence_id="evidence-001",
            )

        with app_engine.begin() as conn:
            repo_b = SanadsRepository(conn, TENANT_B_ID)
            result = repo_b.get(sanad_id)

        assert result is None, "Tenant B should not see tenant A's sanad"


class TestClaimsAPIPostgresReadPath:
    """Tests proving Claims API reads from Postgres via HTTP endpoints."""

    def test_api_reads_claim_inserted_into_postgres(
        self,
        app_engine: Engine,
        clean_tables: None,
        client_with_postgres: TestClient,
    ) -> None:
        """GET /v1/claims/{claim_id} returns claim inserted directly into Postgres.

        This proves the API route is reading from Postgres, not in-memory.
        """
        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)
            _create_claim_in_postgres(
                conn,
                TENANT_A_ID,
                claim_id,
                deal_id,
                claim_text="API Postgres Read Test Claim",
                claim_grade="A",
                claim_verdict="VERIFIED",
            )

        # Assign actor to deal for ABAC access
        _assign_actor_to_deal(TENANT_A_ID, deal_id, ACTOR_A_ID)

        response = client_with_postgres.get(
            f"/v1/claims/{claim_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body["claim_id"] == claim_id
        assert body["claim_text"] == "API Postgres Read Test Claim"
        assert body["claim_grade"] == "A"
        assert body["claim_verdict"] == "VERIFIED"

    def test_api_list_claims_returns_postgres_data(
        self,
        app_engine: Engine,
        clean_tables: None,
        client_with_postgres: TestClient,
    ) -> None:
        """GET /v1/deals/{deal_id}/claims returns claims from Postgres."""
        deal_id = str(uuid.uuid4())
        claim_ids = [str(uuid.uuid4()) for _ in range(3)]

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)
            for i, claim_id in enumerate(claim_ids):
                _create_claim_in_postgres(
                    conn,
                    TENANT_A_ID,
                    claim_id,
                    deal_id,
                    claim_text=f"Claim {i}",
                )

        response = client_with_postgres.get(
            f"/v1/deals/{deal_id}/claims",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 3
        returned_ids = {item["claim_id"] for item in body["items"]}
        assert returned_ids == set(claim_ids)


class TestClaimsAPIPostgresWritePath:
    """Tests proving Claims API writes to Postgres via HTTP endpoints."""

    def test_api_creates_claim_in_postgres(
        self,
        app_engine: Engine,
        clean_tables: None,
        client_with_postgres: TestClient,
    ) -> None:
        """POST /v1/deals/{deal_id}/claims creates claim that exists in Postgres.

        This proves the write path is DB-backed.
        """
        deal_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)

        response = client_with_postgres.post(
            f"/v1/deals/{deal_id}/claims",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
            json={
                "claim_class": "FINANCIAL",
                "claim_text": "API Created Claim for Postgres Test",
                "materiality": "HIGH",
            },
        )

        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )
        body = response.json()
        created_claim_id = body["claim_id"]

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            result = conn.execute(
                text(
                    "SELECT claim_id, tenant_id, claim_text, materiality "
                    "FROM claims WHERE claim_id = :claim_id"
                ),
                {"claim_id": created_claim_id},
            ).fetchone()

        assert result is not None, "Claim should exist in Postgres"
        assert str(result.claim_id) == created_claim_id
        assert str(result.tenant_id) == TENANT_A_ID
        assert result.claim_text == "API Created Claim for Postgres Test"
        assert result.materiality == "HIGH"


class TestClaimsAPITenantIsolation:
    """Tests proving RLS tenant isolation via Claims API endpoints."""

    def test_cross_tenant_get_returns_404(
        self,
        app_engine: Engine,
        clean_tables: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Claim created under tenant A returns 404 when queried as tenant B.

        This proves RLS blocks cross-tenant reads at the API level.
        """
        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id)
            _create_claim_in_postgres(
                conn,
                TENANT_A_ID,
                claim_id,
                deal_id,
                claim_text="Tenant A Secret Claim",
            )

        response = client_with_postgres.get(
            f"/v1/claims/{claim_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )

        assert response.status_code == 404
        body = response.json()
        assert "not found" in body.get("message", "").lower()

    def test_cross_tenant_list_returns_empty(
        self,
        app_engine: Engine,
        clean_tables: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Claims created under tenant A are not visible in tenant B's list.

        This proves RLS isolation on list operations via API.
        """
        deal_id_a = str(uuid.uuid4())
        deal_id_b = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id_a)
            for i in range(3):
                _create_claim_in_postgres(
                    conn,
                    TENANT_A_ID,
                    str(uuid.uuid4()),
                    deal_id_a,
                    claim_text=f"Tenant A Claim {i}",
                )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_B_ID)
            _create_deal_in_postgres(conn, TENANT_B_ID, deal_id_b)

        response = client_with_postgres.get(
            f"/v1/deals/{deal_id_b}/claims",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 0, "Tenant B should see no claims"

    def test_tenant_sees_only_own_claims_via_api(
        self,
        app_engine: Engine,
        clean_tables: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Each tenant sees only their own claims via API list endpoint."""
        deal_id_a = str(uuid.uuid4())
        deal_id_b = str(uuid.uuid4())
        claim_id_a = str(uuid.uuid4())
        claim_id_b = str(uuid.uuid4())

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _create_deal_in_postgres(conn, TENANT_A_ID, deal_id_a)
            _create_claim_in_postgres(
                conn,
                TENANT_A_ID,
                claim_id_a,
                deal_id_a,
                claim_text="Tenant A Only",
            )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_B_ID)
            _create_deal_in_postgres(conn, TENANT_B_ID, deal_id_b)
            _create_claim_in_postgres(
                conn,
                TENANT_B_ID,
                claim_id_b,
                deal_id_b,
                claim_text="Tenant B Only",
            )

        response_a = client_with_postgres.get(
            f"/v1/deals/{deal_id_a}/claims",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert response_a.status_code == 200
        items_a = response_a.json()["items"]
        assert len(items_a) == 1
        assert items_a[0]["claim_id"] == claim_id_a
        assert items_a[0]["claim_text"] == "Tenant A Only"

        response_b = client_with_postgres.get(
            f"/v1/deals/{deal_id_b}/claims",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )
        assert response_b.status_code == 200
        items_b = response_b.json()["items"]
        assert len(items_b) == 1
        assert items_b[0]["claim_id"] == claim_id_b
        assert items_b[0]["claim_text"] == "Tenant B Only"
