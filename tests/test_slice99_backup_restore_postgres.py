"""Slice99 Task 7 - backup/restore drill against real Postgres (RED-first, env-gated).

Runs under IDIS_REQUIRE_POSTGRES=1 against the disposable container (idis-slice99-pgtest,
port 15499). Pins the recovery contract:

1. Backup FAILS CLOSED when required env vars are missing, and its output NEVER contains
   DSNs or passwords (secret-safe: connection info via env only).
2. Backup produces per-table data dumps plus a SAFE manifest (schema revision, table row
   counts, sha256 - no hosts, users, passwords, or URLs).
3-6. Restore after a full wipe (alembic downgrade to base) preserves tenant-scoped row
   counts, leaves guarded tables with RLS ENABLED + FORCED, preserves audit_events content,
   and the app role (non-superuser, NOBYPASSRLS) remains tenant-scoped: no context = no rows,
   cross-tenant writes rejected.
7. RB-11 runbook exists and names the SLO RPO/RTO targets, drill cadence, restore
   verification steps, and secret-handling rules.

PYTHONPATH pinned to this worktree's src. No migration files are touched (0031 stays free).
"""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from idis.persistence.db import set_tenant_local

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNBOOK = _REPO_ROOT / "docs" / "runbooks" / "RB-11_backup_restore.md"

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

_GUARDED_TABLES = ("deals", "claims", "audit_events")


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Backup/restore drill requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location(
        "db_backup_restore", _REPO_ROOT / "scripts" / "db_backup_restore.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pg_schema() -> Generator[None, None, None]:
    """Migrate the disposable database to head for this module."""
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


def _admin_engine() -> Any:
    from idis.persistence.db import get_admin_engine

    return get_admin_engine()


def _wipe_tables() -> None:
    with _admin_engine().begin() as conn:
        conn.execute(text("TRUNCATE deals, claims, audit_events CASCADE"))


def _seed_two_tenants() -> dict[str, Any]:
    """Seed deals + audit events for two tenants; return the expected post-restore state."""
    now = datetime.now(UTC)
    audit_event_id = str(uuid.uuid4())
    audit_payload = {"safe": {"drill": "slice99-task7"}, "hashes": [], "refs": []}
    with _admin_engine().begin() as conn:
        for tenant, names in ((TENANT_A, ("alpha-one", "alpha-two")), (TENANT_B, ("beta-one",))):
            for name in names:
                conn.execute(
                    text(
                        """
                        INSERT INTO deals (deal_id, tenant_id, name, company_name, status,
                                           created_at)
                        VALUES (:deal_id, :tenant_id, :name, :company, 'NEW', :created_at)
                        """
                    ),
                    {
                        "deal_id": str(uuid.uuid4()),
                        "tenant_id": tenant,
                        "name": name,
                        "company": f"{name}-co",
                        "created_at": now,
                    },
                )
        conn.execute(
            text(
                """
                INSERT INTO audit_events (event_id, tenant_id, occurred_at, event_type,
                                          request_id, event)
                VALUES (:event_id, :tenant_id, :occurred_at, 'deal.created', :request_id,
                        CAST(:event AS JSONB))
                """
            ),
            {
                "event_id": audit_event_id,
                "tenant_id": TENANT_A,
                "occurred_at": now,
                "request_id": "req-drill-1",
                "event": json.dumps(audit_payload),
            },
        )
    return {
        "deal_counts": {TENANT_A: 2, TENANT_B: 1},
        "audit_event_id": audit_event_id,
        "audit_payload": audit_payload,
    }


def _tenant_deal_counts() -> dict[str, int]:
    with _admin_engine().begin() as conn:
        rows = conn.execute(
            text("SELECT tenant_id::text, COUNT(*) FROM deals GROUP BY tenant_id")
        ).fetchall()
    return {row[0]: int(row[1]) for row in rows}


_SECRET_MARKERS = ("idis_app_pw", "postgres:postgres", "postgresql://", "@127.0.0.1")


def _assert_secret_safe(blob: str) -> None:
    lowered = blob.lower()
    for marker in _SECRET_MARKERS:
        assert marker not in lowered, f"output leaked connection material: {marker!r}"


# ---------------------------------------------------------------------------
# 1. fail-closed env handling, no secret leakage
# ---------------------------------------------------------------------------


def test_backup_fails_closed_without_env_and_never_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.delenv(ADMIN_URL_ENV, raising=False)

    exit_code = module.main(["backup", "--out", str(tmp_path / "bk")])

    captured = capsys.readouterr()
    assert exit_code != 0, "backup must fail closed without the admin database env"
    _assert_secret_safe(captured.out + captured.err)


def test_backup_output_is_secret_safe_when_configured(
    pg_schema: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _wipe_tables()
    _seed_two_tenants()
    module = _load_script()

    exit_code = module.main(["backup", "--out", str(tmp_path / "bk")])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    _assert_secret_safe(captured.out + captured.err)


# ---------------------------------------------------------------------------
# 2. backup artifacts: data dumps + safe manifest
# ---------------------------------------------------------------------------


def test_backup_creates_dump_and_safe_manifest(pg_schema: None, tmp_path: Path) -> None:
    _wipe_tables()
    _seed_two_tenants()
    module = _load_script()
    out_dir = tmp_path / "bk"

    assert module.main(["backup", "--out", str(out_dir)]) == 0

    manifest_path = out_dir / "manifest.json"
    assert manifest_path.is_file(), "backup must write a manifest"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_revision"], "manifest must pin the alembic schema revision"
    assert manifest["tables"]["deals"]["rows"] == 3
    assert len(manifest["tables"]["deals"]["sha256"]) == 64
    assert (out_dir / "tables" / "deals.csv").is_file()
    assert (out_dir / "tables" / "audit_events.csv").is_file()

    _assert_secret_safe(manifest_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 3-6. the drill: wipe -> restore -> verify tenancy, RLS, audit, app-role denial
# ---------------------------------------------------------------------------


def test_restore_after_wipe_preserves_tenancy_rls_and_audit(
    pg_schema: None, tmp_path: Path
) -> None:
    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg
    from idis.persistence.db import get_app_engine

    _wipe_tables()
    expected = _seed_two_tenants()
    assert _tenant_deal_counts() == expected["deal_counts"]

    module = _load_script()
    out_dir = tmp_path / "bk"
    assert module.main(["backup", "--out", str(out_dir)]) == 0

    # WIPE: downgrade the schema to base (tables gone) - a genuine disaster simulation.
    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    with _admin_engine().begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")
    with _admin_engine().begin() as conn:
        remaining = conn.execute(
            text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'deals'")
        ).scalar()
    assert remaining == 0, "wipe must actually remove the tables"

    # RESTORE
    assert module.main(["restore", "--from", str(out_dir)]) == 0

    # 3. tenant-scoped row counts survive
    assert _tenant_deal_counts() == expected["deal_counts"]

    # 4. guarded tables keep RLS ENABLED + FORCED
    with _admin_engine().begin() as conn:
        for table in _GUARDED_TABLES:
            row = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = :table"
                ),
                {"table": table},
            ).fetchone()
            assert row is not None, f"{table} must exist after restore"
            assert row[0] is True and row[1] is True, f"{table} must keep ENABLE+FORCE RLS"

    # 5. audit_events survive with content intact
    with _admin_engine().begin() as conn:
        event_row = conn.execute(
            text("SELECT event FROM audit_events WHERE event_id = :event_id"),
            {"event_id": expected["audit_event_id"]},
        ).fetchone()
    assert event_row is not None, "audit event must survive the restore"
    assert event_row[0] == expected["audit_payload"]

    # 6. app role stays tenant-scoped: context-bound reads, zero rows without context,
    #    cross-tenant writes rejected by the RLS WITH CHECK.
    app_engine = get_app_engine()
    with app_engine.begin() as conn:
        set_tenant_local(conn, TENANT_A)
        visible = conn.execute(text("SELECT COUNT(*) FROM deals")).scalar()
    assert visible == expected["deal_counts"][TENANT_A]

    with app_engine.begin() as conn:
        no_context = conn.execute(text("SELECT COUNT(*) FROM deals")).scalar()
    assert no_context == 0, "app role without tenant context must see nothing"

    with pytest.raises(DBAPIError), app_engine.begin() as conn:
        set_tenant_local(conn, TENANT_A)
        conn.execute(
            text(
                """
                    INSERT INTO deals (deal_id, tenant_id, name, company_name, status,
                                       created_at)
                    VALUES (:deal_id, :tenant_id, 'cross', 'cross-co', 'NEW', :created_at)
                    """
            ),
            {
                "deal_id": str(uuid.uuid4()),
                "tenant_id": TENANT_B,
                "created_at": datetime.now(UTC),
            },
        )


# ---------------------------------------------------------------------------
# 7. RB-11 runbook (hermetic)
# ---------------------------------------------------------------------------


def test_rb11_runbook_names_targets_cadence_verification_and_secret_rules() -> None:
    assert _RUNBOOK.is_file(), "docs/runbooks/RB-11_backup_restore.md must exist"
    text_lower = _RUNBOOK.read_text(encoding="utf-8").lower()

    assert "rpo" in text_lower and "15 minutes" in text_lower
    assert "rto" in text_lower and "2 hours" in text_lower
    assert "quarterly" in text_lower, "drill cadence must be stated"
    assert "row counts" in text_lower, "restore verification must be described"
    assert "rls" in text_lower
    assert "environment" in text_lower and "password" in text_lower, (
        "secret-handling rules must be stated"
    )
    assert "idis_database_admin_url" in text_lower
