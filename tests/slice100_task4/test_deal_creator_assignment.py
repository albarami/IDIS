"""Deal creation must bootstrap deny-by-default ABAC without a second transaction."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from idis.api.abac import (
    InMemoryDealAssignmentStore,
    set_deal_assignment_store,
)
from idis.api.auth import TenantContext
from idis.api.policy import Role
from idis.api.routes.deals import CreateDealRequest, clear_deals_store, create_deal
from idis.persistence.repositories.deals import InMemoryDealsRepository

TENANT_ID = "10000000-0000-4000-8000-000000000001"
ACTOR_ID = "task4-bootstrap-service"
DEAL_ID = "20000000-0000-4000-8000-000000000001"


class _RecordingConnection:
    def __init__(self, *, fail_assignment: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_assignment = fail_assignment

    def execute(self, statement: object, params: dict[str, Any]) -> SimpleNamespace:
        sql = str(statement)
        self.calls.append((sql, params))
        if self.fail_assignment and "INSERT INTO deal_assignments" in sql:
            raise RuntimeError("assignment insert failed")
        return SimpleNamespace(rowcount=1)


class _UnexpectedGlobalAssignmentStore(InMemoryDealAssignmentStore):
    def add_assignment(self, tenant_id: str, deal_id: str, actor_id: str) -> None:
        raise AssertionError("durable creation must not open/use a second assignment store")


class _FailingInMemoryAssignmentStore(InMemoryDealAssignmentStore):
    def add_assignment(self, tenant_id: str, deal_id: str, actor_id: str) -> None:
        raise RuntimeError("in-memory assignment failed")


@pytest.fixture(autouse=True)
def _isolate_stores() -> None:
    clear_deals_store()
    set_deal_assignment_store(InMemoryDealAssignmentStore())
    yield
    clear_deals_store()
    set_deal_assignment_store(InMemoryDealAssignmentStore())


def _tenant_context(*, role: Role = Role.INTEGRATION_SERVICE) -> TenantContext:
    return TenantContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        name="Task 4 bootstrap",
        timezone="UTC",
        data_region="QA",
        roles=frozenset({role.value}),
        actor_type="SERVICE",
    )


def _request(db_conn: object | None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(db_conn=db_conn))


def _request_body() -> CreateDealRequest:
    return CreateDealRequest(name="Task 4 deal", company_name="Fictional Company")


def test_durable_deal_creation_assigns_creator_on_request_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecordingConnection()
    monkeypatch.setattr("idis.persistence.repositories.deals.set_tenant_local", lambda *_: None)
    set_deal_assignment_store(_UnexpectedGlobalAssignmentStore())

    created = create_deal(_request_body(), _request(connection), _tenant_context())

    assignment_calls = [
        (sql, params) for sql, params in connection.calls if "INSERT INTO deal_assignments" in sql
    ]
    assert len(assignment_calls) == 1
    _, params = assignment_calls[0]
    assert params == {
        "tenant_id": TENANT_ID,
        "deal_id": created.deal_id,
        "assignee_id": ACTOR_ID,
    }


def test_durable_assignment_failure_aborts_deal_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecordingConnection(fail_assignment=True)
    monkeypatch.setattr("idis.persistence.repositories.deals.set_tenant_local", lambda *_: None)

    with pytest.raises(RuntimeError, match="assignment insert failed"):
        create_deal(_request_body(), _request(connection), _tenant_context())

    assert any("INSERT INTO deals" in sql for sql, _ in connection.calls)
    assert any("INSERT INTO deal_assignments" in sql for sql, _ in connection.calls)


def test_in_memory_deal_creation_assigns_only_the_creator() -> None:
    store = InMemoryDealAssignmentStore()
    set_deal_assignment_store(store)

    created = create_deal(_request_body(), _request(None), _tenant_context())

    assert store.is_actor_assigned(TENANT_ID, created.deal_id, ACTOR_ID)
    assert not store.is_actor_assigned(TENANT_ID, created.deal_id, "unassigned-actor")


@pytest.mark.parametrize(
    "role",
    [Role.ANALYST, Role.PARTNER, Role.IC_MEMBER, Role.ADMIN],
)
def test_every_authorized_creator_gets_durable_bootstrap_assignment(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
) -> None:
    connection = _RecordingConnection()
    monkeypatch.setattr("idis.persistence.repositories.deals.set_tenant_local", lambda *_: None)

    created = create_deal(
        _request_body(),
        _request(connection),
        _tenant_context(role=role),
    )

    assignment_calls = [
        params for sql, params in connection.calls if "INSERT INTO deal_assignments" in sql
    ]
    assert assignment_calls == [
        {
            "tenant_id": TENANT_ID,
            "deal_id": created.deal_id,
            "assignee_id": ACTOR_ID,
        }
    ]


@pytest.mark.parametrize(
    "role",
    [Role.ANALYST, Role.PARTNER, Role.IC_MEMBER, Role.ADMIN],
)
def test_every_authorized_creator_gets_in_memory_bootstrap_assignment(role: Role) -> None:
    store = InMemoryDealAssignmentStore()
    set_deal_assignment_store(store)

    created = create_deal(
        _request_body(),
        _request(None),
        _tenant_context(role=role),
    )

    assert store.is_actor_assigned(TENANT_ID, created.deal_id, ACTOR_ID)


def test_in_memory_assignment_failure_compensates_deal_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idis.api.routes.deals.uuid.uuid4", lambda: DEAL_ID)
    set_deal_assignment_store(_FailingInMemoryAssignmentStore())

    with pytest.raises(RuntimeError, match="in-memory assignment failed"):
        create_deal(_request_body(), _request(None), _tenant_context())

    assert InMemoryDealsRepository(TENANT_ID).get(DEAL_ID) is None
