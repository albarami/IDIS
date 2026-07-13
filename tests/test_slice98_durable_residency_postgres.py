"""Slice98 Task 3 (durable) - Postgres residency source of truth: migration + real read path.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves what the in-memory twin cannot:
migration 0027 adds a nullable ``tenants.data_region`` column, ``PostgresTenantRegionStore`` reads
the durable value (None for a NULL column or a missing row), and the REAL request path enforces the
durable region as the source of truth (claim ignored) with fail-closed deny on mismatch/unset.
PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.compliance.residency import IDIS_SERVICE_REGION_ENV
from idis.compliance.tenant_region import (
    PostgresTenantRegionStore,
    reset_tenant_region_store,
)

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"
_DURABLE_FLAG = "IDIS_ENABLE_DURABLE_RESIDENCY"
_API_KEY = "test-key-residency-pg"


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres residency integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    """Migrate to head (idempotent) so migration 0027's tenants.data_region column exists."""
    _skip_or_fail_if_no_postgres()

    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg
    from idis.persistence.db import get_admin_engine, reset_engines

    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    with get_admin_engine().begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
    yield
    reset_engines()


@pytest.fixture
def pg_tenant(_pg_schema: None) -> Generator[str, None, None]:
    """A unique tenant per test; clean up its tenants row; reset the region-store seam."""
    from idis.persistence.db import get_admin_engine

    tenant_id = str(uuid.uuid4())

    def _cleanup() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(
                text("DELETE FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
                {"tid": tenant_id},
            )

    reset_tenant_region_store()
    _cleanup()
    yield tenant_id
    _cleanup()
    reset_tenant_region_store()


def _seed_tenant(tenant_id: str, region: str | None) -> None:
    """Insert (admin, bypassing any policy) a tenants row with the given durable region."""
    from idis.persistence.db import get_admin_engine

    with get_admin_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, data_region) "
                "VALUES (CAST(:tid AS uuid), :name, :region)"
            ),
            {"tid": tenant_id, "name": "PG Residency Tenant", "region": region},
        )


def test_tenants_has_nullable_data_region_column(_pg_schema: None) -> None:
    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        row = conn.execute(
            text(
                "SELECT is_nullable, data_type FROM information_schema.columns "
                "WHERE table_name = 'tenants' AND column_name = 'data_region'"
            )
        ).fetchone()
    assert row is not None, "tenants.data_region column missing (migration 0027 not applied)"
    assert row.is_nullable == "YES"


def test_postgres_store_reads_seeded_region(pg_tenant: str) -> None:
    _seed_tenant(pg_tenant, "me-south-1")
    assert PostgresTenantRegionStore().get_data_region(pg_tenant) == "me-south-1"


def test_postgres_store_returns_none_for_null_region(pg_tenant: str) -> None:
    _seed_tenant(pg_tenant, None)
    assert PostgresTenantRegionStore().get_data_region(pg_tenant) is None


def test_postgres_store_returns_none_for_missing_tenant(pg_tenant: str) -> None:
    assert PostgresTenantRegionStore().get_data_region(pg_tenant) is None


def test_durable_region_survives_across_store_instances(pg_tenant: str) -> None:
    _seed_tenant(pg_tenant, "eu-west-1")
    # A second instance stands in for a fresh replica/restart: durability is in the DB, not memory.
    assert PostgresTenantRegionStore().get_data_region(pg_tenant) == "eu-west-1"


def _durable_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tenant_id: str,
    service_region: str,
    claim_region: str,
) -> TestClient:
    """Full app with durable residency ON and the default (Postgres) region store rebuilt."""
    keys = {
        _API_KEY: {
            "tenant_id": tenant_id,
            "actor_id": "actor-pg",
            "name": "PG Actor",
            "timezone": "UTC",
            "data_region": claim_region,
            "roles": ["ANALYST"],
        }
    }
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(keys))
    monkeypatch.setenv(IDIS_SERVICE_REGION_ENV, service_region)
    monkeypatch.setenv(_DURABLE_FLAG, "1")
    reset_tenant_region_store()  # rebuild default -> PostgresTenantRegionStore (DB configured)
    return TestClient(create_app(service_region=service_region), raise_server_exceptions=False)


def test_wire_and_prove_durable_allows_on_match_ignoring_claim(
    pg_tenant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Durable region matches the service region; the (deliberately wrong) claim is ignored.
    _seed_tenant(pg_tenant, "me-south-1")
    client = _durable_client(
        monkeypatch, tenant_id=pg_tenant, service_region="me-south-1", claim_region="us-east-1"
    )
    resp = client.get("/v1/tenants/me", headers={"X-IDIS-API-Key": _API_KEY})
    assert resp.status_code == 200, resp.text


def test_wire_and_prove_durable_denies_on_mismatch(
    pg_tenant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Durable region mismatches the service region even though the claim would have matched.
    _seed_tenant(pg_tenant, "us-east-1")
    client = _durable_client(
        monkeypatch, tenant_id=pg_tenant, service_region="me-south-1", claim_region="me-south-1"
    )
    resp = client.get("/v1/tenants/me", headers={"X-IDIS-API-Key": _API_KEY})
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "RESIDENCY_REGION_MISMATCH"


def test_wire_and_prove_durable_denies_when_region_unset(
    pg_tenant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Durable region is NULL (tenant not provisioned): fail closed despite a matching claim.
    _seed_tenant(pg_tenant, None)
    client = _durable_client(
        monkeypatch, tenant_id=pg_tenant, service_region="me-south-1", claim_region="me-south-1"
    )
    resp = client.get("/v1/tenants/me", headers={"X-IDIS-API-Key": _API_KEY})
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "RESIDENCY_TENANT_REGION_UNSET"
