"""Disposable-PostgreSQL proof for the production signed-policy budget store."""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import idis.persistence.migrations as migrations_pkg
from idis.persistence.db import set_tenant_local
from idis.providers.budget import PostgresProviderBudgetStore
from idis.runtime.dependency_probes import (
    build_postgres_budget_probe_store_factory,
    collect_provider_budget_facts,
)
from tests.slice100_task2._support import budget_payload, error_code
from tests.slice100_task2.test_durable_governance_migration import (
    _create_disposable_engine,
    _run_alembic,
)


def _require_postgres() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL"):
        return
    message = "governed budget proof requires IDIS_DATABASE_ADMIN_URL"
    if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1":
        pytest.fail(f"REQUIRED: {message}")
    pytest.skip(message)


def _insert_policy(
    engine: object,
    *,
    tenant_id: str,
    policy_hash: str,
    imported_at: datetime | None = None,
) -> None:
    from sqlalchemy import text

    payload = budget_payload()
    payload["tenant_id"] = tenant_id
    rows = []
    resolved_imported_at = imported_at or datetime.now(UTC)
    for component_id, component in payload["components"].items():
        rows.append(
            {
                "tenant_id": tenant_id,
                "policy_hash": policy_hash,
                "component_id": component_id,
                "policy_version": payload["policy_version"],
                "currency": "USD",
                # Deliberately lower than the per-component cap to prove the global USD limit.
                "total_max_usd": Decimal("0.50"),
                "max_calls": component["max_calls"],
                "max_usd": Decimal(str(component["max_usd"])),
                "max_input_tokens": component.get("max_input_tokens"),
                "max_output_tokens": component.get("max_output_tokens"),
                "max_embedding_tokens": component.get("max_embedding_tokens"),
                "signer_id": "slice100-governance-key-v1",
                "imported_by_actor_id": "slice100-governance-admin",
                "envelope_hash": "b" * 64,
                "payload_hash": policy_hash,
                "public_key_hash": "c" * 64,
                "import_audit_event_id": str(uuid.uuid4()),
                "imported_at": resolved_imported_at,
            }
        )
    # One import event binds the complete row set.
    audit_id = str(uuid.uuid4())
    for row in rows:
        row["import_audit_event_id"] = audit_id
    statement = text(
        """
        INSERT INTO provider_budget_policies (
            tenant_id, policy_hash, component_id, policy_version, currency,
            total_max_usd, max_calls, max_usd, max_input_tokens,
            max_output_tokens, max_embedding_tokens, signer_id,
            imported_by_actor_id, envelope_hash, payload_hash, public_key_hash,
            import_audit_event_id, imported_at
        ) VALUES (
            CAST(:tenant_id AS UUID), :policy_hash, :component_id, :policy_version,
            :currency, :total_max_usd, :max_calls, :max_usd, :max_input_tokens,
            :max_output_tokens, :max_embedding_tokens, :signer_id,
            :imported_by_actor_id, :envelope_hash, :payload_hash, :public_key_hash,
            CAST(:import_audit_event_id AS UUID), :imported_at
        )
        """
    )
    with engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(statement, rows)


def _values(tenant_id: str, policy_hash: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": tenant_id,
        "policy_hash": policy_hash,
        "component_id": "anthropic",
        "reservation_id": str(uuid.uuid4()),
        "calls": 1,
        "usd": Decimal("0.20"),
        "input_tokens": 10,
        "output_tokens": 5,
        "embedding_tokens": 0,
        "global_max_calls": 50,
    }
    values.update(overrides)
    return values


def test_production_governed_store_reserves_consumes_refunds_and_survives_restart() -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "a" * 64
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        store = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )

        first = _values(tenant_id, policy_hash)
        assert store.reserve(**first) is True
        assert store.consume(**first) is True
        refundable = _values(tenant_id, policy_hash, usd=Decimal("0.10"))
        assert store.reserve(**refundable) is True
        assert store.refund(**refundable) is True

        restarted = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )
        snapshot = restarted.snapshot(
            tenant_id=tenant_id,
            policy_hash=policy_hash,
            component_id="anthropic",
        )
        assert snapshot["reserved_calls"] == 0
        assert snapshot["used"] == 1
        assert snapshot["consumed_usd"] == Decimal("0.20")

        assert restarted.reserve(**_values(tenant_id, policy_hash, usd=Decimal("0.31"))) is False
        assert (
            restarted.reserve(**_values(tenant_id, policy_hash, calls=8, usd=Decimal("0"))) is False
        )
        with pytest.raises(Exception) as cap_mismatch:
            restarted.reserve(
                **_values(tenant_id, policy_hash, global_max_calls=1, usd=Decimal("0"))
            )
        assert error_code(cap_mismatch.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO provider_budget_usage "
                    "(tenant_id, provider, used) "
                    "VALUES (CAST(:tenant_id AS UUID), 'sec_edgar', 1)"
                ),
                {"tenant_id": tenant_id},
            )
        with pytest.raises(Exception) as exc_info:
            restarted.reserve(**_values(tenant_id, policy_hash, usd=Decimal("0")))
        assert error_code(exc_info.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"
    finally:
        cleanup()


def test_global_call_hard_cap_cannot_be_escalated_between_reservation_ids() -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "9" * 64
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        store = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=1,
        )
        first = _values(
            tenant_id,
            policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000091",
            global_max_calls=1,
            usd=Decimal("0"),
        )
        escalated = _values(
            tenant_id,
            policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000092",
            global_max_calls=100,
            usd=Decimal("0"),
        )

        assert store.reserve(**first) is True
        with pytest.raises(Exception) as exc_info:
            store.reserve(**escalated)
        assert error_code(exc_info.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"
    finally:
        cleanup()


def test_policy_replacement_preserves_exact_retries_and_old_reserved_completion() -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    old_policy_hash = "1" * 64
    new_policy_hash = "2" * 64
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(
            engine,
            tenant_id=tenant_id,
            policy_hash=old_policy_hash,
            imported_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        store = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )
        consumable = _values(
            tenant_id,
            old_policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000081",
        )
        refundable = _values(
            tenant_id,
            old_policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000082",
        )
        assert store.reserve(**consumable) is True
        assert store.reserve(**refundable) is True

        _insert_policy(
            engine,
            tenant_id=tenant_id,
            policy_hash=new_policy_hash,
            imported_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        restarted = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )

        # Exact current-state retries and terminal completion use the immutable reservation
        # ledger; replacing the signed policy must not strand already-admitted spend.
        assert restarted.reserve(**consumable) is True
        assert restarted.reserve(**refundable) is True
        assert restarted.consume(**consumable) is True
        assert restarted.consume(**consumable) is True
        assert restarted.refund(**refundable) is True
        assert restarted.refund(**refundable) is True

        with pytest.raises(Exception) as inactive_policy:
            restarted.reserve(
                **_values(
                    tenant_id,
                    old_policy_hash,
                    reservation_id="40000000-0000-4000-8000-000000000083",
                )
            )
        assert error_code(inactive_policy.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"

        current = _values(
            tenant_id,
            new_policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000084",
        )
        assert restarted.reserve(**current) is True
        assert restarted.refund(**current) is True
        # Terminal retries remain exact even after the aggregate row advances to the new policy.
        assert restarted.consume(**consumable) is True
        assert restarted.refund(**refundable) is True

        with engine.connect() as connection:
            events = (
                connection.execute(
                    text(
                        "SELECT event FROM audit_events "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                        "AND event_type LIKE 'integration.provider_budget.%'"
                    ),
                    {"tenant_id": tenant_id},
                )
                .scalars()
                .all()
            )
        assert len(events) == 6
        assert (
            sum(event["payload"]["safe"]["policy_hash"] == old_policy_hash for event in events) == 4
        )
        assert (
            sum(event["payload"]["safe"]["policy_hash"] == new_policy_hash for event in events) == 2
        )
    finally:
        cleanup()


def test_cap_replacement_preserves_exact_retries_and_old_reserved_completion() -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "4" * 64
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        old_store = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )
        consumable = _values(
            tenant_id,
            policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000071",
            global_max_calls=50,
        )
        refundable = _values(
            tenant_id,
            policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000072",
            global_max_calls=50,
        )
        assert old_store.reserve(**consumable) is True
        assert old_store.reserve(**refundable) is True

        restarted = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=60,
        )
        assert restarted.reserve(**consumable) is True
        assert restarted.reserve(**refundable) is True
        assert restarted.consume(**consumable) is True
        assert restarted.consume(**consumable) is True
        assert restarted.refund(**refundable) is True
        assert restarted.refund(**refundable) is True

        with pytest.raises(Exception) as stale_cap_new_reservation:
            restarted.reserve(
                **_values(
                    tenant_id,
                    policy_hash,
                    reservation_id="40000000-0000-4000-8000-000000000073",
                    global_max_calls=50,
                )
            )
        assert error_code(stale_cap_new_reservation.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"

        current = _values(
            tenant_id,
            policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000074",
            global_max_calls=60,
        )
        assert restarted.reserve(**current) is True
        assert restarted.refund(**current) is True
        assert restarted.consume(**consumable) is True
        assert restarted.refund(**refundable) is True

        with engine.connect() as connection:
            events = (
                connection.execute(
                    text(
                        "SELECT event FROM audit_events "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                        "AND event_type LIKE 'integration.provider_budget.%'"
                    ),
                    {"tenant_id": tenant_id},
                )
                .scalars()
                .all()
            )
        assert len(events) == 6
        assert sum(event["payload"]["safe"]["global_max_calls"] == 50 for event in events) == 4
        assert sum(event["payload"]["safe"]["global_max_calls"] == 60 for event in events) == 2
    finally:
        cleanup()


def test_budget_readiness_cleanup_preserves_exact_append_only_ledger_accounting() -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "8" * 64
    payload = budget_payload()
    policy = SimpleNamespace(
        tenant_id=tenant_id,
        policy_hash=policy_hash,
        signature_verified=True,
        currency="USD",
        components=payload["components"],
    )
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        store_factory = build_postgres_budget_probe_store_factory(
            engine=engine,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )
        facts = collect_provider_budget_facts(
            policy_loader=lambda _tenant_id: policy,
            store_factory=store_factory,
            tenant_id=tenant_id,
            global_max_calls=50,
        )
        restarted_facts = collect_provider_budget_facts(
            policy_loader=lambda _tenant_id: policy,
            store_factory=store_factory,
            tenant_id=tenant_id,
            global_max_calls=50,
        )
        assert facts.consume_ok is True
        assert facts.refund_ok is True
        assert restarted_facts == facts

        with engine.connect() as connection:
            usage = (
                connection.execute(
                    text(
                        "SELECT used, reserved_calls, reserved_usd, consumed_usd, "
                        "reserved_input_tokens, consumed_input_tokens, "
                        "reserved_output_tokens, consumed_output_tokens, "
                        "reserved_embedding_tokens, consumed_embedding_tokens "
                        "FROM provider_budget_usage "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                        "AND provider = 'anthropic'"
                    ),
                    {"tenant_id": tenant_id},
                )
                .mappings()
                .one_or_none()
            )
            events = (
                connection.execute(
                    text(
                        "SELECT event FROM audit_events "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                        "AND event_type LIKE 'integration.provider_budget.%'"
                    ),
                    {"tenant_id": tenant_id},
                )
                .scalars()
                .all()
            )

        consumed = [
            event["payload"]["safe"]
            for event in events
            if event["event_type"] == "integration.provider_budget.consumed"
        ]
        assert len(events) == 4
        assert len(consumed) == 1
        assert usage is not None
        assert usage["used"] == sum(item["calls"] for item in consumed)
        assert usage["consumed_usd"] == sum(
            (Decimal(item["usd"]) for item in consumed),
            Decimal("0"),
        )
        assert usage["consumed_input_tokens"] == sum(item["input_tokens"] for item in consumed)
        assert usage["consumed_output_tokens"] == sum(item["output_tokens"] for item in consumed)
        assert usage["consumed_embedding_tokens"] == sum(
            item["embedding_tokens"] for item in consumed
        )
        assert usage["reserved_calls"] == 0
        assert usage["reserved_usd"] == 0
        assert usage["reserved_input_tokens"] == 0
        assert usage["reserved_output_tokens"] == 0
        assert usage["reserved_embedding_tokens"] == 0
    finally:
        cleanup()


def test_budget_readiness_cap_change_preserves_old_events_and_is_idempotent() -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "3" * 64
    payload = budget_payload()
    policy = SimpleNamespace(
        tenant_id=tenant_id,
        policy_hash=policy_hash,
        signature_verified=True,
        currency="USD",
        components=payload["components"],
    )

    def run(cap: int) -> object:
        return collect_provider_budget_facts(
            policy_loader=lambda _tenant_id: policy,
            store_factory=build_postgres_budget_probe_store_factory(
                engine=engine,
                tenant_setter=set_tenant_local,
                global_max_calls=cap,
            ),
            tenant_id=tenant_id,
            global_max_calls=cap,
        )

    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        first = run(50)
        with engine.connect() as connection:
            old_events = {
                str(row["event_id"]): row["event"]
                for row in connection.execute(
                    text(
                        "SELECT event_id, event FROM audit_events "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                        "AND event_type LIKE 'integration.provider_budget.%'"
                    ),
                    {"tenant_id": tenant_id},
                ).mappings()
            }

        changed = run(60)
        repeated = run(60)
        with engine.connect() as connection:
            usage = (
                connection.execute(
                    text(
                        "SELECT used, reserved_calls FROM provider_budget_usage "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) AND provider = 'anthropic'"
                    ),
                    {"tenant_id": tenant_id},
                )
                .mappings()
                .one()
            )
            events = {
                str(row["event_id"]): row["event"]
                for row in connection.execute(
                    text(
                        "SELECT event_id, event FROM audit_events "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                        "AND event_type LIKE 'integration.provider_budget.%'"
                    ),
                    {"tenant_id": tenant_id},
                ).mappings()
            }

        assert first.restart_read_ok is True
        assert changed.restart_read_ok is True
        assert repeated == changed
        assert all(events[event_id] == event for event_id, event in old_events.items())
        assert len(old_events) == 4
        assert len(events) == 8
        assert usage == {"used": 2, "reserved_calls": 0}
        ids_by_cap = {
            cap: {
                event["payload"]["safe"]["reservation_id"]
                for event in events.values()
                if event["payload"]["safe"]["global_max_calls"] == cap
            }
            for cap in (50, 60)
        }
        assert len(ids_by_cap[50]) == len(ids_by_cap[60]) == 2
        assert ids_by_cap[50].isdisjoint(ids_by_cap[60])
    finally:
        cleanup()


def test_budget_readiness_preserves_nonzero_preledger_used_baseline() -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "7" * 64
    payload = budget_payload()
    policy = SimpleNamespace(
        tenant_id=tenant_id,
        policy_hash=policy_hash,
        signature_verified=True,
        currency="USD",
        components=payload["components"],
    )
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO provider_budget_usage (tenant_id, provider, used) "
                    "VALUES (CAST(:tenant_id AS UUID), 'anthropic', 7)"
                ),
                {"tenant_id": tenant_id},
            )

        facts = collect_provider_budget_facts(
            policy_loader=lambda _tenant_id: policy,
            store_factory=build_postgres_budget_probe_store_factory(
                engine=engine,
                tenant_setter=set_tenant_local,
                global_max_calls=50,
            ),
            tenant_id=tenant_id,
            global_max_calls=50,
        )

        assert facts.call_reservation_ok is False
        assert facts.consume_ok is False
        with engine.connect() as connection:
            usage = (
                connection.execute(
                    text(
                        "SELECT used, reserved_calls, reserved_usd, consumed_usd, "
                        "reserved_input_tokens, consumed_input_tokens, "
                        "reserved_output_tokens, consumed_output_tokens, "
                        "reserved_embedding_tokens, consumed_embedding_tokens "
                        "FROM provider_budget_usage "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                        "AND provider = 'anthropic'"
                    ),
                    {"tenant_id": tenant_id},
                )
                .mappings()
                .one()
            )
            event_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                    "AND event_type LIKE 'integration.provider_budget.%'"
                ),
                {"tenant_id": tenant_id},
            ).scalar_one()

        assert usage["used"] == 7
        assert all(value == 0 for name, value in usage.items() if name != "used")
        assert event_count == 0
    finally:
        cleanup()


def test_reservation_ledger_is_exact_idempotent_and_restart_safe() -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from idis.validators.audit_event_validator import validate_audit_event

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "d" * 64
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        first = _values(
            tenant_id,
            policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000001",
        )
        second = _values(
            tenant_id,
            policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000002",
        )
        store = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )
        assert store.reserve(**first) is True
        assert store.reserve(**second) is True

        restarted = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )
        assert restarted.consume(**first) is True
        assert restarted.consume(**first) is True
        with pytest.raises(Exception) as wrong_terminal:
            restarted.refund(**first)
        assert error_code(wrong_terminal.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"
        with pytest.raises(Exception) as mismatched_retry:
            restarted.reserve(**{**second, "usd": Decimal("0.02")})
        assert error_code(mismatched_retry.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"
        assert restarted.refund(**second) is True
        assert restarted.refund(**second) is True

        snapshot = restarted.snapshot(
            tenant_id=tenant_id,
            policy_hash=policy_hash,
            component_id="anthropic",
        )
        assert snapshot["reserved_calls"] == 0
        assert snapshot["used"] == 1
        assert snapshot["consumed_usd"] == Decimal("0.20")
        with engine.connect() as connection:
            events = (
                connection.execute(
                    text(
                        "SELECT event FROM audit_events "
                        "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                        "AND event_type LIKE 'integration.provider_budget.%'"
                    ),
                    {"tenant_id": tenant_id},
                )
                .scalars()
                .all()
            )
        assert len(events) == 4
        assert all(validate_audit_event(event).passed for event in events)
        assert {event["event_type"] for event in events} == {
            "integration.provider_budget.reserved",
            "integration.provider_budget.consumed",
            "integration.provider_budget.refunded",
        }
        expected_safe_keys = {
            "reservation_id",
            "policy_hash",
            "component_id",
            "calls",
            "usd",
            "input_tokens",
            "output_tokens",
            "embedding_tokens",
            "global_max_calls",
            "state",
        }
        for event in events:
            safe = event["payload"]["safe"]
            assert set(safe) == expected_safe_keys
            assert str(uuid.UUID(safe["reservation_id"])) == safe["reservation_id"]
        rendered = json.dumps(events, sort_keys=True)
        for forbidden in ("v2:test", "api_key", "prompt", "provider_payload", "ciphertext"):
            assert forbidden not in rendered
    finally:
        cleanup()


def test_concurrent_terminal_retries_mutate_one_reservation_once() -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "e" * 64
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        reservation = _values(
            tenant_id,
            policy_hash,
            reservation_id="40000000-0000-4000-8000-000000000003",
        )
        store = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )
        assert store.reserve(**reservation) is True

        def consume_once() -> bool:
            restarted = PostgresProviderBudgetStore(
                connection_factory=engine.begin,
                tenant_setter=set_tenant_local,
                global_max_calls=50,
            )
            return bool(restarted.consume(**reservation))

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: consume_once(), range(2)))
        assert results == [True, True]
        snapshot = store.snapshot(
            tenant_id=tenant_id,
            policy_hash=policy_hash,
            component_id="anthropic",
        )
        assert snapshot["reserved_calls"] == 0
        assert snapshot["used"] == 1
        with engine.connect() as connection:
            consumed_events = connection.execute(
                text(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                    "AND event_type = 'integration.provider_budget.consumed'"
                ),
                {"tenant_id": tenant_id},
            ).scalar_one()
        assert consumed_events == 1
    finally:
        cleanup()


def test_reservation_aggregate_rolls_back_when_audit_append_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_postgres()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from idis.audit.postgres_sink import PostgresAuditSink

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "f" * 64
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        monkeypatch.setattr(
            PostgresAuditSink,
            "emit_in_tx",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected")),
        )
        store = PostgresProviderBudgetStore(
            connection_factory=engine.begin,
            tenant_setter=set_tenant_local,
            global_max_calls=50,
        )
        with pytest.raises(Exception) as exc_info:
            store.reserve(
                **_values(
                    tenant_id,
                    policy_hash,
                    reservation_id="40000000-0000-4000-8000-000000000004",
                )
            )
        assert error_code(exc_info.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"
        with engine.connect() as connection:
            usage_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM provider_budget_usage "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID)"
                ),
                {"tenant_id": tenant_id},
            ).scalar_one()
            event_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE tenant_id = CAST(:tenant_id AS UUID) "
                    "AND event_type LIKE 'integration.provider_budget.%'"
                ),
                {"tenant_id": tenant_id},
            ).scalar_one()
        assert usage_count == 0
        assert event_count == 0
    finally:
        cleanup()
