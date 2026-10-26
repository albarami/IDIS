"""Tenant registry reads must establish their own transaction-local RLS context."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from idis.compliance.tenant_region import PostgresTenantRegionStore


class _Result:
    def fetchone(self) -> tuple[str]:
        return ("qa",)


class _Connection:
    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self.calls = calls

    def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append(("select", (str(statement), params)))
        return _Result()


def test_postgres_tenant_region_sets_context_on_the_query_connection(
    monkeypatch: Any,
) -> None:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    calls: list[tuple[str, Any]] = []
    connection = _Connection(calls)

    @contextmanager
    def begin_app_conn() -> Any:
        yield connection

    def set_tenant_local(actual_connection: Any, actual_tenant_id: str) -> None:
        assert actual_connection is connection
        calls.append(("set", actual_tenant_id))

    monkeypatch.setattr("idis.persistence.db.begin_app_conn", begin_app_conn)
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", set_tenant_local)

    assert PostgresTenantRegionStore().get_data_region(tenant_id) == "qa"
    assert [kind for kind, _value in calls] == ["set", "select"]
    assert calls[0] == ("set", tenant_id)
