"""Hosted-only PostgreSQL proof for PostgresBudgetPort.

Skipped unless IDIS_DATABASE_ADMIN_URL names a disposable server. This machine
does not run these nodes.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest

import idis.persistence.migrations as migrations_pkg
from idis.persistence.db import set_tenant_local
from idis.providers.budget import PostgresProviderBudgetStore
from idis.providers.governed_llm_client import PostgresBudgetPort
from tests.slice100_task2._support import error_code
from tests.slice100_task2.test_durable_governance_migration import (
    _create_disposable_engine,
    _run_alembic,
)
from tests.slice100_task2.test_governed_budget_postgres import _insert_policy, _require_postgres

_GLOBAL_MAX_CALLS = 50


def test_postgres_port_reserve_then_consume() -> None:
    """A committed reserve followed by consume clears the reservation and counts the call."""
    _require_postgres()
    _exercise("consume")


def test_postgres_port_reserve_then_refund() -> None:
    """A committed reserve followed by refund releases the reservation."""
    _require_postgres()
    _exercise("refund")


def test_postgres_port_reserve_then_leave_reserved() -> None:
    """Stopping after reserve leaves the ledger row RESERVED."""
    _require_postgres()
    _exercise("leave")


def test_postgres_port_rejects_a_different_consume_vector() -> None:
    """Consume with a different USD vector is rejected and the reservation stays RESERVED."""
    _require_postgres()
    from alembic import command
    from alembic.config import Config

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "a" * 64
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        port = PostgresBudgetPort(engine.begin, tenant_id, _GLOBAL_MAX_CALLS)
        vector = _vector(tenant_id, policy_hash)
        assert port.reserve(vector) is True
        changed = {**vector, "usd": Decimal("0.02")}
        with pytest.raises(Exception) as caught:
            port.consume(changed)
        assert error_code(caught.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"
        snapshot = _snapshot(engine, tenant_id, policy_hash)
        assert snapshot["reserved_calls"] == 1
        assert snapshot["used"] == 0
    finally:
        cleanup()


def _exercise(terminal: str) -> None:
    from alembic import command
    from alembic.config import Config

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    policy_hash = "b" * 64
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        _insert_policy(engine, tenant_id=tenant_id, policy_hash=policy_hash)
        port = PostgresBudgetPort(engine.begin, tenant_id, _GLOBAL_MAX_CALLS)
        vector = _vector(tenant_id, policy_hash)
        assert port.reserve(vector) is True
        if terminal == "consume":
            assert port.consume(vector) is True
        elif terminal == "refund":
            assert port.refund(vector) is True
        snapshot = _snapshot(engine, tenant_id, policy_hash)
        if terminal == "consume":
            assert snapshot["reserved_calls"] == 0
            assert snapshot["used"] == 1
            assert snapshot["consumed_usd"] == Decimal("0.20")
        elif terminal == "refund":
            assert snapshot["reserved_calls"] == 0
            assert snapshot["used"] == 0
        else:
            assert snapshot["reserved_calls"] == 1
            assert snapshot["used"] == 0
            assert snapshot["reserved_usd"] == Decimal("0.20")
    finally:
        cleanup()


def _vector(tenant_id: str, policy_hash: str) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "policy_hash": policy_hash,
        "component_id": "anthropic",
        "reservation_id": str(uuid.uuid4()),
        "calls": 1,
        "usd": Decimal("0.20"),
        "input_tokens": 10,
        "output_tokens": 5,
        "embedding_tokens": 0,
        "global_max_calls": _GLOBAL_MAX_CALLS,
    }


def _snapshot(engine: object, tenant_id: str, policy_hash: str) -> dict[str, object]:
    store = PostgresProviderBudgetStore(
        connection_factory=engine.begin,  # type: ignore[attr-defined]
        tenant_setter=set_tenant_local,
        global_max_calls=_GLOBAL_MAX_CALLS,
    )
    return store.snapshot(
        tenant_id=tenant_id,
        policy_hash=policy_hash,
        component_id="anthropic",
    )
