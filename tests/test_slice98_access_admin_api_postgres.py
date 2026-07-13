"""Slice98 Task 2 (durable) - assignment/group management API persists through Postgres.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves the admin routes write durable,
RLS-scoped rows (the default store is Postgres when configured) and that a granted assignment
survives into a brand-new app instance (restart/replica). PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.abac import (
    AbacDecisionCode,
    check_deal_access,
    reset_deal_assignment_store,
)
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_ADMIN_A = "admin-key-a-98pg"
_ANALYST_A = "analyst-key-a-98pg"
_ABAC_TABLES = ("group_memberships", "groups", "deal_assignments")


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres access-admin integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
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
def api_keys_env(monkeypatch: pytest.MonkeyPatch) -> None:
    def _entry(actor: str, roles: list[str]) -> dict[str, Any]:
        return {
            "tenant_id": _TENANT_A,
            "actor_id": actor,
            "name": actor,
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": roles,
        }

    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {_ADMIN_A: _entry("admin-a", ["ADMIN"]), _ANALYST_A: _entry("analyst-a", ["ANALYST"])}
        ),
    )


@pytest.fixture
def pg_ready(_pg_schema: None, api_keys_env: None) -> Generator[None, None, None]:
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(_ABAC_TABLES)} CASCADE"))

    clear_deals_store()
    reset_deal_assignment_store()
    _truncate()
    yield
    _truncate()
    reset_deal_assignment_store()
    clear_deals_store()


def _hdr(key: str) -> dict[str, str]:
    return {"X-IDIS-API-Key": key, "Content-Type": "application/json"}


def test_assignment_route_persists_durably_across_app_instances(pg_ready: None) -> None:
    app1 = create_app(service_region="us-east-1")
    client1 = TestClient(app1)
    deal = client1.post(
        "/v1/deals", json={"name": "D", "company_name": "Acme"}, headers=_hdr(_ADMIN_A)
    )
    assert deal.status_code == 201, deal.text
    deal_id = deal.json()["deal_id"]

    granted = client1.post(
        f"/v1/deals/{deal_id}/assignments", json={"actor_id": "analyst-a"}, headers=_hdr(_ADMIN_A)
    )
    assert granted.status_code == 201, granted.text

    def _analyst_allowed() -> bool:
        # A brand-new default store (Postgres, rebuilt after reset) resolves the durable grant via
        # the exact decision function the middleware uses = restart/replica durability.
        reset_deal_assignment_store()
        decision = check_deal_access(
            tenant_id=_TENANT_A,
            actor_id="analyst-a",
            roles={"ANALYST"},
            deal_id=deal_id,
            is_mutation=True,
        )
        return decision.allow and decision.code == AbacDecisionCode.ALLOWED

    assert _analyst_allowed() is True  # durable grant visible to a fresh store instance

    # revoke via a brand-new app instance; the durable removal is visible to yet another store
    reset_deal_assignment_store()
    app2 = create_app(service_region="us-east-1")
    client2 = TestClient(app2)
    assert (
        client2.delete(
            f"/v1/deals/{deal_id}/assignments/analyst-a", headers=_hdr(_ADMIN_A)
        ).status_code
        == 204
    )
    assert _analyst_allowed() is False
