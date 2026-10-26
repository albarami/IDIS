"""RED contracts for the Task 2 durable-governance migration.

The schema assertions are intentionally migration-facing: the approved plan fixes the
two new tables and the two existing-table changes, and a real PostgreSQL round trip is
the only reliable proof that a downgrade preserves legacy rows.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import idis.persistence.migrations as migrations_pkg
from tests.slice100_task2._support import (
    ACCEPTANCE_TENANT_ID,
    BUDGET_SCHEMA_VERSION,
    RIGHTS_SCHEMA_VERSION,
    SIGNER_ID,
    budget_payload,
    rights_payload,
    strict_auth_env,
)

MIGRATIONS_DIR = Path(migrations_pkg.__file__).parent / "versions"
REQUIRED_USAGE_COLUMNS = {
    "policy_hash",
    "reserved_calls",
    "reserved_usd",
    "consumed_usd",
    "reserved_input_tokens",
    "consumed_input_tokens",
    "reserved_output_tokens",
    "consumed_output_tokens",
    "reserved_embedding_tokens",
    "consumed_embedding_tokens",
}
GOVERNANCE_LAUNCH_IDENTITY: dict[str, object] = {
    "base_profile_hmac": "d" * 64,
    "command": "governance",
    "generation": "bootstrap-v1",
    "process_role": None,
}


def _migration_path() -> Path:
    matches = sorted(MIGRATIONS_DIR.glob("0033_*.py"))
    assert len(matches) == 1, "Task 2 must add one linear migration after revision 0032"
    return matches[0]


def _load_migration() -> Any:
    path = _migration_path()
    spec = importlib.util.spec_from_file_location("slice100_task2_migration_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().upper()


def test_task2_migration_is_one_linear_revision_after_0032() -> None:
    module = _load_migration()

    assert module.revision == "0033"
    assert module.down_revision == "0032"


def test_task2_migration_contains_only_the_approved_table_inventory() -> None:
    source = _migration_path().read_text(encoding="utf-8")
    normalized = _normalized(source)

    created = set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)? ([A-Z0-9_]+)", normalized))
    assert created == {"ENRICHMENT_RIGHTS_APPROVALS", "PROVIDER_BUDGET_POLICIES"}
    assert "CREDENTIAL_VERSION_ID" in normalized
    assert "ENRICHMENT_CREDENTIALS" in normalized
    assert "PROVIDER_BUDGET_USAGE" in normalized
    for column in REQUIRED_USAGE_COLUMNS:
        assert column.upper() in normalized
    for forbidden in (
        "RUNTIME_PROFILES",
        "AUTH_REGISTRY",
        "GOVERNANCE_SIGNERS",
        "BUDGET_COMPONENTS",
        "CREDENTIAL_VERSIONS",
        "CONSUMER_RECEIPTS",
        "CONSUMER_EDGES",
    ):
        assert forbidden not in created


@pytest.mark.parametrize(
    "table",
    ["enrichment_rights_approvals", "provider_budget_policies"],
)
def test_new_governance_tables_force_null_guarded_rls(table: str) -> None:
    source = _normalized(_migration_path().read_text(encoding="utf-8"))
    table_name = table.upper()

    assert f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY" in source
    assert f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY" in source
    assert re.search(
        rf"CREATE POLICY [A-Z0-9_]+ ON {table_name}.*?USING \(.*?"
        rf"NULLIF\(CURRENT_SETTING\('IDIS\.TENANT_ID', TRUE\), ''\).*?"
        rf"WITH CHECK \(.*?NULLIF\(CURRENT_SETTING\('IDIS\.TENANT_ID', TRUE\), ''\)",
        source,
    )


def test_governance_keys_and_credential_lineage_are_database_enforced() -> None:
    source = _normalized(_migration_path().read_text(encoding="utf-8"))

    assert all(
        token in source
        for token in (
            "RIGHTS_APPROVAL_ID",
            "TENANT_ID",
            "PROVIDER_ID",
            "ENVIRONMENT",
            "TERMS_VERSION_HASH",
            "CREDENTIAL_VERSION_ID",
            "POLICY_HASH",
            "COMPONENT_ID",
            "ENVELOPE_HASH",
            "PAYLOAD_HASH",
            "PUBLIC_KEY_HASH",
        )
    )
    assert re.search(
        r"UNIQUE\s*\(\s*TENANT_ID\s*,\s*PROVIDER_ID\s*,\s*ENVIRONMENT\s*,\s*"
        r"TERMS_VERSION_HASH\s*\)",
        source,
    )
    assert re.search(
        r"PRIMARY KEY\s*\(\s*TENANT_ID\s*,\s*POLICY_HASH\s*,\s*COMPONENT_ID\s*\)",
        source,
    )
    assert re.search(
        r"PRIMARY KEY\s*\(\s*TENANT_ID\s*,\s*CONNECTOR_ID\s*,\s*"
        r"CREDENTIAL_VERSION_ID\s*\)",
        source,
    )
    assert "WHERE ROTATED_AT IS NULL AND REVOKED_AT IS NULL" in source


def test_budget_policy_schema_is_append_only_without_an_unplanned_effective_window() -> None:
    """Plan §9.1 makes replacement order authoritative; it does not authorize revocation time."""
    source = _normalized(_migration_path().read_text(encoding="utf-8"))
    table = re.search(
        r"CREATE TABLE PROVIDER_BUDGET_POLICIES \((.*?)\);",
        source,
    )

    assert table is not None
    assert "REVOKED_AT" not in table.group(1)
    assert "UX_PROVIDER_BUDGET_POLICY_ONE_ACTIVE_COMPONENT" not in source


def test_governance_lifecycle_triggers_guard_rights_identity_and_delete() -> None:
    """Rights updates preserve identity; deletes and all budget mutations are rejected."""
    source = _normalized(_migration_path().read_text(encoding="utf-8"))

    assert re.search(
        r"CREATE TRIGGER TRG_ENRICHMENT_RIGHTS_APPROVALS_IMMUTABLE "
        r"BEFORE UPDATE OR DELETE ON ENRICHMENT_RIGHTS_APPROVALS",
        source,
    )
    assert "TG_OP = 'DELETE'" in source
    assert all(
        field in source
        for field in (
            "NEW.RIGHTS_APPROVAL_ID",
            "OLD.RIGHTS_APPROVAL_ID",
            "NEW.TENANT_ID",
            "OLD.TENANT_ID",
            "NEW.PROVIDER_ID",
            "OLD.PROVIDER_ID",
            "NEW.ENVIRONMENT",
            "OLD.ENVIRONMENT",
            "NEW.TERMS_VERSION_HASH",
            "OLD.TERMS_VERSION_HASH",
        )
    )
    assert "RETURN NEW" in source
    assert re.search(
        r"CREATE TRIGGER TRG_PROVIDER_BUDGET_POLICIES_IMMUTABLE "
        r"BEFORE UPDATE OR DELETE ON PROVIDER_BUDGET_POLICIES",
        source,
    )


def test_downgrade_locks_governance_tables_before_losslessness_guard() -> None:
    """A concurrent import must not race the zero-row guard and then be dropped."""
    source = _normalized(_migration_path().read_text(encoding="utf-8"))
    downgrade = source[source.index("DEF DOWNGRADE") :]
    guard_position = downgrade.index("ASSERT_DOWNGRADE_IS_LOSSLESS")
    prefix = downgrade[:guard_position]
    # The lock lives in a named helper so upgrade does not reference not-yet-created tables.
    lock_helper = source[source.index("DEF _LOCK_DOWNGRADE_TABLES") : source.index("DEF _BACKFILL")]

    assert "_LOCK_DOWNGRADE_TABLES" in prefix
    assert "ENRICHMENT_RIGHTS_APPROVALS" in lock_helper
    assert "PROVIDER_BUDGET_POLICIES" in lock_helper
    assert "ACCESS EXCLUSIVE" in lock_helper


def _skip_or_fail_if_no_disposable_postgres() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL"):
        return
    message = "Task 2 migration round trip requires IDIS_DATABASE_ADMIN_URL"
    if os.environ.get("IDIS_REQUIRE_POSTGRES", "0") == "1":
        pytest.fail(f"REQUIRED: {message} (IDIS_REQUIRE_POSTGRES=1)")
    pytest.skip(message)


def _create_disposable_engine() -> tuple[Any, Callable[[], None]]:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    configured_url = make_url(os.environ["IDIS_DATABASE_ADMIN_URL"])
    database_name = f"idis_slice100_task2_udu_{uuid.uuid4().hex}"
    maintenance_database = "postgres" if configured_url.database != "postgres" else "template1"
    maintenance = create_engine(
        configured_url.set(database=maintenance_database), isolation_level="AUTOCOMMIT"
    )
    with maintenance.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}" TEMPLATE template0')
    engine = create_engine(configured_url.set(database=database_name))

    def cleanup() -> None:
        engine.dispose()
        try:
            with maintenance.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": database_name},
                )
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            maintenance.dispose()

    return engine, cleanup


def _run_alembic(
    config: Any,
    engine: Any,
    action: Callable[[Any, str], None],
    target: str,
) -> None:
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        action(config, target)


def test_task2_upgrade_downgrade_upgrade_preserves_legacy_rows_on_disposable_postgres() -> None:
    _skip_or_fail_if_no_disposable_postgres()

    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect, text

    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    assert ScriptDirectory.from_config(config).get_current_head() == "0035"

    engine, cleanup = _create_disposable_engine()
    tenant_id = str(uuid.uuid4())
    try:
        _run_alembic(config, engine, command.upgrade, "0032")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO enrichment_credentials "
                    "(tenant_id, connector_id, ciphertext) "
                    "VALUES (CAST(:tenant_id AS UUID), 'finnhub', 'v2:legacy')"
                ),
                {"tenant_id": tenant_id},
            )
            connection.execute(
                text(
                    "INSERT INTO provider_budget_usage (tenant_id, provider, used) "
                    "VALUES (CAST(:tenant_id AS UUID), 'anthropic', 7)"
                ),
                {"tenant_id": tenant_id},
            )

        for cycle in range(2):
            _run_alembic(config, engine, command.upgrade, "0033")
            with engine.connect() as connection:
                assert MigrationContext.configure(connection).get_current_revision() == "0033"
                inspector = inspect(connection)
                assert {
                    "enrichment_rights_approvals",
                    "provider_budget_policies",
                } <= set(inspector.get_table_names())
                credential_columns = {
                    item["name"] for item in inspector.get_columns("enrichment_credentials")
                }
                usage_columns = {
                    item["name"] for item in inspector.get_columns("provider_budget_usage")
                }
                assert "credential_version_id" in credential_columns
                assert usage_columns >= REQUIRED_USAGE_COLUMNS
                version_id = connection.execute(
                    text(
                        "SELECT credential_version_id FROM enrichment_credentials "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) AND connector_id = 'finnhub'"
                    ),
                    {"tenant_id": tenant_id},
                ).scalar_one()
                assert version_id is not None
                assert (
                    connection.execute(
                        text(
                            "SELECT used FROM provider_budget_usage "
                            "WHERE tenant_id = CAST(:tenant_id AS UUID) AND provider = 'anthropic'"
                        ),
                        {"tenant_id": tenant_id},
                    ).scalar_one()
                    == 7
                )

            if cycle == 0:
                _run_alembic(config, engine, command.downgrade, "0032")
                with engine.connect() as connection:
                    assert MigrationContext.configure(connection).get_current_revision() == "0032"
                    inspector = inspect(connection)
                    assert "enrichment_rights_approvals" not in inspector.get_table_names()
                    assert "provider_budget_policies" not in inspector.get_table_names()
                    assert "credential_version_id" not in {
                        item["name"] for item in inspector.get_columns("enrichment_credentials")
                    }
                    assert REQUIRED_USAGE_COLUMNS.isdisjoint(
                        {item["name"] for item in inspector.get_columns("provider_budget_usage")}
                    )
                    assert (
                        connection.execute(
                            text(
                                "SELECT COUNT(*) FROM enrichment_credentials "
                                "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                                "AND connector_id = 'finnhub'"
                            ),
                            {"tenant_id": tenant_id},
                        ).scalar_one()
                        == 1
                    )
                    assert (
                        connection.execute(
                            text(
                                "SELECT used FROM provider_budget_usage "
                                "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                                "AND provider = 'anthropic'"
                            ),
                            {"tenant_id": tenant_id},
                        ).scalar_one()
                        == 7
                    )
    finally:
        cleanup()


def test_task2_downgrade_refuses_to_discard_governance_or_version_history() -> None:
    """The migration must expose a callable, fail-closed downgrade guard."""
    module = _load_migration()
    guard = getattr(module, "assert_downgrade_is_lossless", None)

    assert callable(guard)


def test_rights_and_budget_lifecycles_are_independent_on_disposable_postgres(
    tmp_path: Path,
) -> None:
    """Real SQL proves replay/replacement/transition lineage without schema expansion."""
    _skip_or_fail_if_no_disposable_postgres()

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from idis.persistence.db import set_tenant_local
    from idis.providers.budget import PostgresProviderBudgetStore
    from idis.runtime.governance_envelopes import sign_envelope
    from idis.runtime.governance_import import import_governance_documents
    from idis.tools.governance_signing import generate_ed25519_keypair

    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    engine, cleanup = _create_disposable_engine()
    key_dir = tmp_path / "operator-keys"
    key_dir.mkdir()
    private_path = key_dir / "trusted-private.pem"
    public_path = key_dir / "trusted-public.pem"
    rights_path = tmp_path / "rights.json"
    budget_path = tmp_path / "budget.json"
    generate_ed25519_keypair(
        private_key_path=private_path,
        public_key_path=public_path,
    )
    private_pem = private_path.read_bytes()
    env = strict_auth_env()
    env.update(
        {
            "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": SIGNER_ID,
            "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_path),
            "IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH": str(rights_path),
            "IDIS_PROVIDER_BUDGET_POLICY_PATH": str(budget_path),
        }
    )
    original_rights = rights_payload()
    original_budget = budget_payload()

    def write_signed_documents(rights: dict[str, object], budget: dict[str, object]) -> None:
        rights_path.write_text(
            json.dumps(
                sign_envelope(
                    schema_version=RIGHTS_SCHEMA_VERSION,
                    signer_id=SIGNER_ID,
                    payload=rights,
                    private_key_pem=private_pem,
                )
            ),
            encoding="utf-8",
        )
        budget_path.write_text(
            json.dumps(
                sign_envelope(
                    schema_version=BUDGET_SCHEMA_VERSION,
                    signer_id=SIGNER_ID,
                    payload=budget,
                    private_key_pem=private_pem,
                )
            ),
            encoding="utf-8",
        )

    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO enrichment_credentials "
                    "(tenant_id, connector_id, credential_version_id, ciphertext) VALUES "
                    "(CAST(:tenant_id AS UUID), 'finnhub', CAST(:finnhub AS UUID), 'v2:test'), "
                    "(CAST(:tenant_id AS UUID), 'fmp', CAST(:fmp AS UUID), 'v2:test')"
                ),
                {
                    "tenant_id": ACCEPTANCE_TENANT_ID,
                    "finnhub": "20000000-0000-4000-8000-000000000001",
                    "fmp": "20000000-0000-4000-8000-000000000002",
                },
            )

        write_signed_documents(original_rights, original_budget)
        imported_at = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        with engine.begin() as connection:
            initial = import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at,
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )
        assert (initial.rights_disposition, initial.budget_disposition) == (
            "created",
            "created",
        )

        replacement_budget = copy.deepcopy(original_budget)
        replacement_budget["policy_version"] = "slice100-acceptance-v2"
        write_signed_documents(original_rights, replacement_budget)
        with engine.begin() as connection:
            budget_replacement = import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=1),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )
        assert (
            budget_replacement.rights_disposition,
            budget_replacement.budget_disposition,
        ) == ("replayed", "created")
        assert budget_replacement.rights_audit_event_id == initial.rights_audit_event_id
        with engine.connect() as connection:
            active_policy_hash = connection.execute(
                text(
                    "SELECT policy_hash FROM provider_budget_policies "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                    "GROUP BY policy_hash ORDER BY MAX(imported_at) DESC, policy_hash DESC "
                    "LIMIT 1"
                ),
                {"tenant_id": ACCEPTANCE_TENANT_ID},
            ).scalar_one()
        assert active_policy_hash == budget_replacement.budget_payload_sha256
        budget_store = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=100,
        )
        budget_values = {
            "tenant_id": ACCEPTANCE_TENANT_ID,
            "policy_hash": budget_replacement.budget_payload_sha256,
            "component_id": "anthropic",
            "reservation_id": "40000000-0000-4000-8000-000000000020",
            "calls": 1,
            "usd": Decimal("0.01"),
            "input_tokens": 1,
            "output_tokens": 1,
            "embedding_tokens": 0,
            "global_max_calls": 100,
        }
        assert budget_store.reserve(**budget_values) is True
        assert budget_store.refund(**budget_values) is True
        with pytest.raises(Exception) as inactive_exc:
            budget_store.reserve(
                **{
                    **budget_values,
                    "policy_hash": initial.budget_payload_sha256,
                }
            )
        assert getattr(inactive_exc.value, "code", None) == "PROVIDER_BUDGET_CONFIG_BLOCKED"

        revoked_rights = copy.deepcopy(original_rights)
        approvals = revoked_rights["approvals"]
        assert isinstance(approvals, list)
        approvals.reverse()
        revoked = approvals[0]
        assert isinstance(revoked, dict)
        revoked["status"] = "REVOKED"
        revoked["revoked_at"] = "2026-07-15T12:00:02Z"
        write_signed_documents(revoked_rights, original_budget)
        with pytest.raises(Exception) as exc_info, engine.begin() as connection:
            import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=2),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )
        assert getattr(exc_info.value, "code", None) == "PROVIDER_BUDGET_CONFIG_BLOCKED"
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM enrichment_rights_approvals "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) AND status = 'REVOKED'"
                    ),
                    {"tenant_id": ACCEPTANCE_TENANT_ID},
                ).scalar_one()
                == 0
            )

        write_signed_documents(revoked_rights, replacement_budget)
        with engine.begin() as connection:
            rights_transition = import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=3),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )
        assert (
            rights_transition.rights_disposition,
            rights_transition.budget_disposition,
        ) == ("transitioned", "replayed")
        assert rights_transition.budget_audit_event_id == budget_replacement.budget_audit_event_id

        # A valid historical envelope is not fresh authorization.  Replaying the
        # original still-unexpired approval after its revocation must roll back the
        # whole import before either governance rows or audit lineage can change.
        write_signed_documents(original_rights, replacement_budget)
        with pytest.raises(Exception) as rollback_exc, engine.begin() as connection:
            import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=4),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )
        assert getattr(rollback_exc.value, "code", None) == "GOVERNANCE_SCHEMA_INVALID"

        with engine.connect() as connection:
            rights_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM enrichment_rights_approvals "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID)"
                ),
                {"tenant_id": ACCEPTANCE_TENANT_ID},
            ).scalar_one()
            budget_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM provider_budget_policies "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID)"
                ),
                {"tenant_id": ACCEPTANCE_TENANT_ID},
            ).scalar_one()
            audit_events = (
                connection.execute(
                    text(
                        "SELECT event FROM audit_events "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                        "ORDER BY occurred_at, event_id"
                    ),
                    {"tenant_id": ACCEPTANCE_TENANT_ID},
                )
                .scalars()
                .all()
            )
            revoked_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM enrichment_rights_approvals "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID) AND status = 'REVOKED'"
                ),
                {"tenant_id": ACCEPTANCE_TENANT_ID},
            ).scalar_one()
        assert rights_count == 4
        assert revoked_count == 1
        assert budget_count == 34
        governance_events = [
            event
            for event in audit_events
            if event["event_type"].startswith("integration.governance.")
        ]
        reservation_events = [
            event
            for event in audit_events
            if event["event_type"].startswith("integration.provider_budget.")
        ]
        assert len(governance_events) == 3
        assert len(reservation_events) == 2
        final_safe = governance_events[-1]["payload"]["safe"]
        assert final_safe["rights_payload"] == revoked_rights
        assert final_safe["previous_rights_payload"] == original_rights
        assert final_safe["rights_disposition"] == "transitioned"
        assert final_safe["budget_disposition"] == "replayed"

        # Revocation authority is provider/environment scoped, not terms-hash scoped.
        # Once every current provider row is revoked, rotating all terms hashes must
        # not admit a backdated approval as a fresh snapshot.
        all_revoked_rights = copy.deepcopy(revoked_rights)
        all_revoked_approvals = all_revoked_rights["approvals"]
        assert isinstance(all_revoked_approvals, list)
        for approval in all_revoked_approvals:
            assert isinstance(approval, dict)
            approval["status"] = "REVOKED"
            approval["revoked_at"] = "2026-07-15T12:00:05Z"
        write_signed_documents(all_revoked_rights, replacement_budget)
        with engine.begin() as connection:
            all_revoked = import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=5),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )
        assert all_revoked.rights_disposition == "transitioned"

        rotated_backdated = copy.deepcopy(original_rights)
        rotated_approvals = rotated_backdated["approvals"]
        assert isinstance(rotated_approvals, list)
        for index, approval in enumerate(rotated_approvals, start=1):
            assert isinstance(approval, dict)
            approval["terms_version_hash"] = f"{index:064x}"
        write_signed_documents(rotated_backdated, replacement_budget)
        with pytest.raises(Exception) as rotated_exc, engine.begin() as connection:
            import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=6),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )
        assert getattr(rotated_exc.value, "code", None) == "GOVERNANCE_SCHEMA_INVALID"
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM enrichment_rights_approvals "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID)"
                    ),
                    {"tenant_id": ACCEPTANCE_TENANT_ID},
                ).scalar_one()
                == 4
            )

        rotated_fresh = copy.deepcopy(rotated_backdated)
        fresh_approvals = rotated_fresh["approvals"]
        assert isinstance(fresh_approvals, list)
        for approval in fresh_approvals:
            assert isinstance(approval, dict)
            approval["approved_at"] = "2026-07-15T12:00:06Z"
        write_signed_documents(rotated_fresh, replacement_budget)
        with engine.begin() as connection:
            fresh_reapproval = import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=7),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )
        assert fresh_reapproval.rights_disposition == "created"
        with engine.connect() as connection:
            active_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM enrichment_rights_approvals "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID) AND status = 'APPROVED'"
                ),
                {"tenant_id": ACCEPTANCE_TENANT_ID},
            ).scalar_one()
        assert active_count == 4

        # The row is mutable across a valid same-terms reapproval, but its
        # append-only revocation clock is not.  A second terms rotation cannot
        # use an approval after an older table revocation but at/before the
        # newer revocation retained by governance audit history.
        rotated_revoked = copy.deepcopy(rotated_fresh)
        rotated_revoked_approvals = rotated_revoked["approvals"]
        assert isinstance(rotated_revoked_approvals, list)
        for approval in rotated_revoked_approvals:
            assert isinstance(approval, dict)
            approval["status"] = "REVOKED"
            approval["revoked_at"] = "2026-07-15T12:00:08Z"
        write_signed_documents(rotated_revoked, replacement_budget)
        with engine.begin() as connection:
            import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=8),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )

        rotated_reapproved = copy.deepcopy(rotated_fresh)
        rotated_reapproved_approvals = rotated_reapproved["approvals"]
        assert isinstance(rotated_reapproved_approvals, list)
        for approval in rotated_reapproved_approvals:
            assert isinstance(approval, dict)
            approval["approved_at"] = "2026-07-15T12:00:09Z"
        write_signed_documents(rotated_reapproved, replacement_budget)
        with engine.begin() as connection:
            import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=9),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )

        second_rotation_backdated = copy.deepcopy(rotated_fresh)
        second_rotation_approvals = second_rotation_backdated["approvals"]
        assert isinstance(second_rotation_approvals, list)
        for index, approval in enumerate(second_rotation_approvals, start=11):
            assert isinstance(approval, dict)
            approval["terms_version_hash"] = f"{index:064x}"
            approval["approved_at"] = "2026-07-15T12:00:08Z"
        write_signed_documents(second_rotation_backdated, replacement_budget)
        with pytest.raises(Exception) as history_exc, engine.begin() as connection:
            import_governance_documents(
                env=env,
                connection=connection,
                now=imported_at + timedelta(seconds=10),
                launch_identity=GOVERNANCE_LAUNCH_IDENTITY,
            )
        assert getattr(history_exc.value, "code", None) == "GOVERNANCE_SCHEMA_INVALID"
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM enrichment_rights_approvals "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID)"
                    ),
                    {"tenant_id": ACCEPTANCE_TENANT_ID},
                ).scalar_one()
                == 8
            )

        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM enrichment_rights_approvals "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID)"
                ),
                {"tenant_id": ACCEPTANCE_TENANT_ID},
            )
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE provider_budget_policies SET max_calls = max_calls "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID)"
                ),
                {"tenant_id": ACCEPTANCE_TENANT_ID},
            )
    finally:
        cleanup()
