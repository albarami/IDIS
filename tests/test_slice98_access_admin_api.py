"""Slice98 Task 2 - assignment/group management API (hermetic full-app).

RED-first. ADMIN-only routes manage the durable ABAC state through the EXISTING store seam
(``get_deal_assignment_store``): direct deal assignments, groups, group membership, and
group-to-deal assignment. Tenancy comes ONLY from ``RequireTenantContext`` (never the body/path);
non-ADMIN roles are denied by the existing RBAC policy; missing/cross-tenant resources return the
same 404 (no existence oracle); every mutation flows through the existing AuditMiddleware with
``rbac.*`` events. Durable Postgres behavior is proven env-gated in
``test_slice98_access_admin_api_postgres.py``. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.abac import (
    AbacDecisionCode,
    check_deal_access,
    get_deal_assignment_store,
    reset_deal_assignment_store,
)
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.audit.sink import InMemoryAuditSink

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_ADMIN_A = "admin-key-a-98"
_ANALYST_A = "analyst-key-a-98"
_ADMIN_B = "admin-key-b-98"


def _api_keys() -> str:
    def _entry(tenant: str, actor: str, roles: list[str]) -> dict[str, Any]:
        return {
            "tenant_id": tenant,
            "actor_id": actor,
            "name": actor,
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": roles,
        }

    return json.dumps(
        {
            _ADMIN_A: _entry(_TENANT_A, "admin-a", ["ADMIN"]),
            _ANALYST_A: _entry(_TENANT_A, "analyst-a", ["ANALYST"]),
            _ADMIN_B: _entry(_TENANT_B, "admin-b", ["ADMIN"]),
        }
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, InMemoryAuditSink]]:
    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys())
    clear_deals_store()
    reset_deal_assignment_store()
    sink = InMemoryAuditSink()
    app = create_app(audit_sink=sink, service_region="us-east-1")
    yield TestClient(app), sink
    clear_deals_store()
    reset_deal_assignment_store()


def _hdr(key: str) -> dict[str, str]:
    return {"X-IDIS-API-Key": key, "Content-Type": "application/json"}


def _create_deal(client: TestClient, key: str = _ADMIN_A) -> str:
    resp = client.post(
        "/v1/deals", json={"name": "Access Deal", "company_name": "Acme"}, headers=_hdr(key)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["deal_id"]


def _create_group(client: TestClient, key: str = _ADMIN_A, name: str = "team") -> str:
    resp = client.post("/v1/groups", json={"name": name}, headers=_hdr(key))
    assert resp.status_code == 201, resp.text
    return resp.json()["group_id"]


def test_non_admin_is_denied_on_every_management_operation(
    client: tuple[TestClient, InMemoryAuditSink],
) -> None:
    api, _ = client
    deal_id = _create_deal(api)
    group_id = str(uuid.uuid4())
    attempts = [
        ("POST", f"/v1/deals/{deal_id}/assignments", {"actor_id": "analyst-a"}),
        ("DELETE", f"/v1/deals/{deal_id}/assignments/analyst-a", None),
        ("POST", "/v1/groups", {"name": "x"}),
        ("POST", f"/v1/groups/{group_id}/members", {"actor_id": "analyst-a"}),
        ("DELETE", f"/v1/groups/{group_id}/members/analyst-a", None),
        ("POST", f"/v1/deals/{deal_id}/group-assignments", {"group_id": group_id}),
        ("DELETE", f"/v1/deals/{deal_id}/group-assignments/{group_id}", None),
    ]
    for method, path, body in attempts:
        resp = api.request(method, path, json=body, headers=_hdr(_ANALYST_A))
        assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}: {resp.text}"


def test_unauthenticated_is_denied(client: tuple[TestClient, InMemoryAuditSink]) -> None:
    api, _ = client
    resp = api.post(f"/v1/deals/{uuid.uuid4()}/assignments", json={"actor_id": "x"})
    assert resp.status_code == 401


def _analyst_allowed(deal_id: str) -> bool:
    # The EXACT decision function RBACMiddleware calls, resolving the DEFAULT store the routes wrote
    # to (store=None). Proves the route mutations flip real ABAC access without depending on the
    # pre-existing middleware quirk that deal_id is not extracted for plain GET /v1/deals/{id}.
    decision = check_deal_access(
        tenant_id=_TENANT_A,
        actor_id="analyst-a",
        roles={"ANALYST"},
        deal_id=deal_id,
        is_mutation=True,
    )
    return decision.allow and decision.code == AbacDecisionCode.ALLOWED


def test_assignment_lifecycle_flips_real_abac_access(
    client: tuple[TestClient, InMemoryAuditSink],
) -> None:
    api, _ = client
    deal_id = _create_deal(api)
    assert _analyst_allowed(deal_id) is False  # deny-by-default before assignment

    created = api.post(
        f"/v1/deals/{deal_id}/assignments", json={"actor_id": "analyst-a"}, headers=_hdr(_ADMIN_A)
    )
    assert created.status_code == 201, created.text
    duplicate = api.post(
        f"/v1/deals/{deal_id}/assignments", json={"actor_id": "analyst-a"}, headers=_hdr(_ADMIN_A)
    )
    assert duplicate.status_code == 201  # idempotent duplicate

    assert _analyst_allowed(deal_id) is True  # assignment flips access

    removed = api.delete(f"/v1/deals/{deal_id}/assignments/analyst-a", headers=_hdr(_ADMIN_A))
    assert removed.status_code == 204
    assert _analyst_allowed(deal_id) is False  # revoked


def test_group_membership_lifecycle_flips_real_abac_access(
    client: tuple[TestClient, InMemoryAuditSink],
) -> None:
    api, _ = client
    deal_id = _create_deal(api)
    group_id = _create_group(api)

    assert (
        api.post(
            f"/v1/groups/{group_id}/members",
            json={"actor_id": "analyst-a"},
            headers=_hdr(_ADMIN_A),
        ).status_code
        == 201
    )
    assert (
        api.post(
            f"/v1/deals/{deal_id}/group-assignments",
            json={"group_id": group_id},
            headers=_hdr(_ADMIN_A),
        ).status_code
        == 201
    )
    assert _analyst_allowed(deal_id) is True  # member of a group assigned to the deal

    assert (
        api.delete(f"/v1/groups/{group_id}/members/analyst-a", headers=_hdr(_ADMIN_A)).status_code
        == 204
    )
    assert _analyst_allowed(deal_id) is False  # membership removed

    # re-add membership, then unassign the group from the deal -> denied again
    api.post(
        f"/v1/groups/{group_id}/members", json={"actor_id": "analyst-a"}, headers=_hdr(_ADMIN_A)
    )
    assert _analyst_allowed(deal_id) is True
    assert (
        api.delete(
            f"/v1/deals/{deal_id}/group-assignments/{group_id}", headers=_hdr(_ADMIN_A)
        ).status_code
        == 204
    )
    assert _analyst_allowed(deal_id) is False


def test_missing_resources_return_not_found(
    client: tuple[TestClient, InMemoryAuditSink],
) -> None:
    api, _ = client
    deal_id = _create_deal(api)
    unknown_deal = str(uuid.uuid4())
    unknown_group = str(uuid.uuid4())

    resp = api.post(
        f"/v1/deals/{unknown_deal}/assignments", json={"actor_id": "x"}, headers=_hdr(_ADMIN_A)
    )
    assert resp.status_code == 404
    resp = api.post(
        f"/v1/groups/{unknown_group}/members", json={"actor_id": "x"}, headers=_hdr(_ADMIN_A)
    )
    assert resp.status_code == 404
    resp = api.post(
        f"/v1/deals/{deal_id}/group-assignments",
        json={"group_id": unknown_group},
        headers=_hdr(_ADMIN_A),
    )
    assert resp.status_code == 404


def test_cross_tenant_resources_look_identical_to_missing(
    client: tuple[TestClient, InMemoryAuditSink],
) -> None:
    api, _ = client
    deal_a = _create_deal(api, key=_ADMIN_A)
    group_a = _create_group(api, key=_ADMIN_A)

    # tenant B admin acting on tenant A's ids -> the same 404 as nonexistent (no oracle)
    resp = api.post(
        f"/v1/deals/{deal_a}/assignments", json={"actor_id": "x"}, headers=_hdr(_ADMIN_B)
    )
    assert resp.status_code == 404
    resp = api.post(f"/v1/groups/{group_a}/members", json={"actor_id": "x"}, headers=_hdr(_ADMIN_B))
    assert resp.status_code == 404

    # tenant is derived from auth only: a tenant_id in the body is rejected by the schema
    # (additionalProperties: false -> 422 for the unexpected property).
    resp = api.post(
        f"/v1/deals/{deal_a}/assignments",
        json={"actor_id": "x", "tenant_id": _TENANT_B},
        headers=_hdr(_ADMIN_A),
    )
    assert resp.status_code == 422


def test_management_mutations_emit_rbac_audit_events(
    client: tuple[TestClient, InMemoryAuditSink],
) -> None:
    api, sink = client
    deal_id = _create_deal(api)
    group_id = _create_group(api)
    api.post(
        f"/v1/deals/{deal_id}/assignments", json={"actor_id": "analyst-a"}, headers=_hdr(_ADMIN_A)
    )
    api.post(
        f"/v1/groups/{group_id}/members", json={"actor_id": "analyst-a"}, headers=_hdr(_ADMIN_A)
    )
    api.post(
        f"/v1/deals/{deal_id}/group-assignments",
        json={"group_id": group_id},
        headers=_hdr(_ADMIN_A),
    )
    api.delete(f"/v1/deals/{deal_id}/assignments/analyst-a", headers=_hdr(_ADMIN_A))

    events = {
        e["event_type"]: e for e in sink.events if str(e.get("event_type", "")).startswith("rbac.")
    }
    assert "rbac.group.created" in events
    assert "rbac.assignment.created" in events
    assert "rbac.group.member_added" in events
    assert "rbac.group.assigned" in events
    assert "rbac.assignment.deleted" in events
    assert events["rbac.assignment.created"]["resource"]["resource_id"] == deal_id
    assert events["rbac.group.created"]["resource"]["resource_id"] == group_id
    assert events["rbac.assignment.created"]["tenant_id"] == _TENANT_A


def test_routes_write_through_the_shared_store_seam(
    client: tuple[TestClient, InMemoryAuditSink],
) -> None:
    api, _ = client
    deal_id = _create_deal(api)
    api.post(
        f"/v1/deals/{deal_id}/assignments", json={"actor_id": "analyst-a"}, headers=_hdr(_ADMIN_A)
    )
    # the routes must use the SAME default store the middleware consults (no side store)
    store = get_deal_assignment_store()
    assert store.is_actor_assigned(_TENANT_A, deal_id, "analyst-a") is True
    assert store.is_actor_assigned(_TENANT_B, deal_id, "analyst-a") is False
