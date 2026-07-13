"""Slice98 Task 6 (durable) - Postgres BYOK policies + legal holds: migration 0029, RLS.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves what the in-memory twins
cannot: the 0029 schema stores NO raw key aliases (hash+length columns only - pinned by
asserting the column set), policies and holds survive across fresh store and app instances
(restart/replica durability, including route-driven configure -> revoke -> deny and
apply -> block -> lift flows), and RLS makes cross-tenant rows invisible (uniform miss,
no existence oracle). PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.errors import IdisHttpError
from idis.api.main import create_app
from idis.audit.sink import InMemoryAuditSink
from idis.compliance.byok import (
    PostgresBYOKPolicyRegistry,
    reset_byok_policy_registry,
)
from idis.compliance.retention import (
    HoldTarget,
    PostgresLegalHoldRegistry,
    apply_hold,
    block_deletion_if_held,
    lift_hold,
    reset_legal_hold_registry,
)

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_ADMIN_A_KEY = "byok-pg-admin-a"
_ALIAS = "tenant-a-pg-kms-alias"
_REASON = "Litigation hold pending case 2026-CV-1138 discovery."
_TABLES = ("byok_policies", "legal_holds")


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres BYOK/legal-hold integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    """Migrate to head (idempotent) so migration 0029's tables exist."""
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
def pg_clean(_pg_schema: None) -> Generator[None, None, None]:
    """Truncate compliance tables (admin bypasses RLS); reset both seams."""
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(_TABLES)}"))

    reset_byok_policy_registry()
    reset_legal_hold_registry()
    _truncate()
    yield
    _truncate()
    reset_byok_policy_registry()
    reset_legal_hold_registry()


def _ctx(tenant_id: str = _TENANT_A) -> object:
    from idis.api.auth import TenantContext

    return TenantContext(
        tenant_id=tenant_id,
        actor_id="pg-admin-a",
        name="PG Compliance Admin",
        timezone="UTC",
        data_region="us-east-1",
        roles=frozenset({"ADMIN"}),
    )


def test_0029_schema_stores_no_raw_aliases_and_forces_rls(pg_clean: None) -> None:
    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        byok_columns = {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'byok_policies'"
                )
            )
        }
        assert {
            "tenant_id",
            "key_alias_sha256",
            "key_alias_length",
            "key_state",
            "created_at",
            "rotated_at",
            "revoked_at",
        } <= byok_columns
        assert "key_alias" not in byok_columns  # raw aliases are NEVER persisted

        hold_columns = {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'legal_holds'"
                )
            )
        }
        assert {
            "tenant_id",
            "hold_id",
            "target_type",
            "target_id",
            "reason_hash",
            "reason_length",
            "applied_at",
            "applied_by",
            "lifted_at",
            "lifted_by",
        } <= hold_columns
        assert "reason" not in hold_columns  # plaintext reasons are NEVER persisted

        indexes = [
            row.indexdef
            for row in conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE tablename = 'legal_holds'")
            )
        ]
        active_index = [d for d in indexes if "lifted_at IS NULL" in d]
        assert active_index, f"missing partial active-hold index: {indexes}"

        for table in _TABLES:
            rls = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    f"WHERE relname = '{table}'"
                )
            ).fetchone()
            assert rls is not None
            assert rls.relrowsecurity, f"RLS not enabled on {table}"
            assert rls.relforcerowsecurity, f"RLS not forced on {table}"


def test_byok_policy_durable_across_store_instances(pg_clean: None) -> None:
    import hashlib

    from idis.compliance.byok import (
        BYOKKeyState,
        DataClass,
        configure_key,
        require_key_active,
        revoke_key,
    )

    sink = InMemoryAuditSink()
    configure_key(_ctx(), _ALIAS, sink, PostgresBYOKPolicyRegistry())

    # a second instance stands in for a fresh replica/restart
    loaded = PostgresBYOKPolicyRegistry().get(_TENANT_A)
    assert loaded is not None
    assert loaded.key_state == BYOKKeyState.ACTIVE
    assert loaded.key_alias == ""  # raw alias not persisted
    assert loaded.key_alias_sha256 == hashlib.sha256(_ALIAS.encode()).hexdigest()
    assert loaded.key_alias_length == len(_ALIAS)

    revoke_key(_ctx(), sink, PostgresBYOKPolicyRegistry())
    with pytest.raises(IdisHttpError) as exc_info:
        require_key_active(_ctx(), DataClass.CLASS_2, PostgresBYOKPolicyRegistry())
    assert exc_info.value.code == "BYOK_KEY_REVOKED"


def test_byok_rls_blocks_cross_tenant_policy(pg_clean: None) -> None:
    from idis.compliance.byok import configure_key

    configure_key(_ctx(_TENANT_A), _ALIAS, InMemoryAuditSink(), PostgresBYOKPolicyRegistry())
    assert PostgresBYOKPolicyRegistry().get(_TENANT_B) is None


def test_legal_hold_durable_and_blocks_across_store_instances(pg_clean: None) -> None:
    sink = InMemoryAuditSink()
    hold = apply_hold(
        _ctx(), HoldTarget.DOCUMENT, "doc-pg-1", _REASON, sink, PostgresLegalHoldRegistry()
    )

    fresh = PostgresLegalHoldRegistry()  # replica stand-in
    assert fresh.has_active_hold(_TENANT_A, HoldTarget.DOCUMENT, "doc-pg-1") is True
    with pytest.raises(IdisHttpError) as exc_info:
        block_deletion_if_held(_ctx(), HoldTarget.DOCUMENT, "doc-pg-1", fresh)
    assert exc_info.value.code == "DELETION_BLOCKED_BY_HOLD"

    lift_hold(_ctx(), hold.hold_id, sink, PostgresLegalHoldRegistry())
    third = PostgresLegalHoldRegistry()
    assert third.has_active_hold(_TENANT_A, HoldTarget.DOCUMENT, "doc-pg-1") is False
    block_deletion_if_held(_ctx(), HoldTarget.DOCUMENT, "doc-pg-1", third)  # no raise


def test_legal_hold_rls_uniform_cross_tenant_miss(pg_clean: None) -> None:
    sink = InMemoryAuditSink()
    hold = apply_hold(
        _ctx(_TENANT_A), HoldTarget.DEAL, "deal-pg-1", _REASON, sink, PostgresLegalHoldRegistry()
    )

    store = PostgresLegalHoldRegistry()
    assert store.get_for_tenant(_TENANT_B, hold.hold_id) is None
    assert store.has_active_hold(_TENANT_B, HoldTarget.DEAL, "deal-pg-1") is False
    with pytest.raises(IdisHttpError) as exc_info:
        lift_hold(_ctx(_TENANT_B), hold.hold_id, sink, store)
    assert exc_info.value.status_code == 404  # same as nonexistent - no oracle
    # tenant A still holds it
    assert store.has_active_hold(_TENANT_A, HoldTarget.DEAL, "deal-pg-1") is True


def test_wire_and_prove_routes_govern_durable_state_across_app_instances(
    pg_clean: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REAL path: configure via app1, revoke via a brand-new app2, holds likewise."""
    from idis.compliance.byok import BYOKKeyState, DataClass, require_key_active

    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                _ADMIN_A_KEY: {
                    "tenant_id": _TENANT_A,
                    "actor_id": "pg-admin-a",
                    "name": "PG Admin",
                    "timezone": "UTC",
                    "data_region": "us-east-1",
                    "roles": ["ADMIN"],
                }
            }
        ),
    )
    headers = {"X-IDIS-API-Key": _ADMIN_A_KEY, "Content-Type": "application/json"}

    client1 = TestClient(create_app(audit_sink=InMemoryAuditSink(), service_region="us-east-1"))
    configured = client1.post("/v1/byok/key", json={"key_alias": _ALIAS}, headers=headers)
    assert configured.status_code == 201, configured.text
    assert _ALIAS not in configured.text

    applied = client1.post(
        "/v1/legal-holds",
        json={"target_type": "ARTIFACT", "target_id": "artifact-pg-9", "reason": _REASON},
        headers=headers,
    )
    assert applied.status_code == 201, applied.text
    hold_id = applied.json()["hold_id"]

    # a brand-new app instance (fresh seams -> fresh Postgres stores) sees the durable state
    reset_byok_policy_registry()
    reset_legal_hold_registry()
    client2 = TestClient(create_app(audit_sink=InMemoryAuditSink(), service_region="us-east-1"))

    revoked = client2.post("/v1/byok/key/revoke", headers=headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["key_state"] == BYOKKeyState.REVOKED.value

    with pytest.raises(IdisHttpError) as exc_info:
        require_key_active(_ctx(), DataClass.CLASS_2, PostgresBYOKPolicyRegistry())
    assert exc_info.value.code == "BYOK_KEY_REVOKED"

    with pytest.raises(IdisHttpError) as blocked:
        block_deletion_if_held(
            _ctx(), HoldTarget.ARTIFACT, "artifact-pg-9", PostgresLegalHoldRegistry()
        )
    assert blocked.value.code == "DELETION_BLOCKED_BY_HOLD"

    lifted = client2.post(f"/v1/legal-holds/{hold_id}/lift", headers=headers)
    assert lifted.status_code == 200, lifted.text
    block_deletion_if_held(
        _ctx(), HoldTarget.ARTIFACT, "artifact-pg-9", PostgresLegalHoldRegistry()
    )  # no raise after lift
