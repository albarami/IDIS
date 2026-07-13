"""Slice98 fix - deal-scoped ABAC must not turn a missing/cross-tenant deal into a 403.

RED-first. Task 2.5 gated plain deal-scoped endpoints, but the deal-scoped path applied the
break-glass/no-assignment 403 WITHOUT first checking whether the path deal_id is visible in the
caller's tenant. That regressed ADR-011: a cross-tenant or nonexistent deal returned
``ABAC_DENIED_BREAK_GLASS_REQUIRED`` (403) instead of the route's uniform 404, leaking that the
deal exists somewhere / distinguishing an out-of-scope id from a missing one.

Hybrid contract proven here:
- a VISIBLE same-tenant deal that the actor is not assigned to still denies deny-by-default (403,
  break-glass affordance preserved for ADMIN) - Slice98 tightening is NOT weakened; and
- a cross-tenant or nonexistent path deal falls through to the route's 404 (no existence oracle,
  no break-glass detail leaked).

PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.abac import (
    InMemoryDealAssignmentStore,
    reset_deal_assignment_store,
    set_deal_assignment_store,
)
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_ADMIN_A = "admin-key-a"
_ANALYST_A = "analyst-key-a"
_ADMIN_B = "admin-key-b"


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
def ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, InMemoryDealAssignmentStore]]:
    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys())
    clear_deals_store()
    store = InMemoryDealAssignmentStore()
    set_deal_assignment_store(store)
    app = create_app(service_region="us-east-1")
    yield TestClient(app, raise_server_exceptions=False), store
    clear_deals_store()
    reset_deal_assignment_store()


def _hdr(key: str) -> dict[str, str]:
    return {"X-IDIS-API-Key": key, "Content-Type": "application/json"}


def _create_deal(api: TestClient, key: str = _ADMIN_A) -> str:
    resp = api.post("/v1/deals", json={"name": "D", "company_name": "Acme"}, headers=_hdr(key))
    assert resp.status_code == 201, resp.text
    return resp.json()["deal_id"]


# --- Direction 1 guards: a VISIBLE same-tenant unassigned deal still denies (deny-by-default) ---


def test_visible_unassigned_deal_still_403_for_analyst(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    api, _store = ctx
    deal_id = _create_deal(api)
    resp = api.get(f"/v1/deals/{deal_id}", headers=_hdr(_ANALYST_A))
    assert resp.status_code == 403, resp.text  # exists + unassigned -> deny-by-default


def test_visible_unassigned_deal_still_403_break_glass_for_admin(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    api, _store = ctx
    deal_id = _create_deal(api)
    resp = api.get(f"/v1/deals/{deal_id}", headers=_hdr(_ADMIN_A))
    assert resp.status_code == 403, resp.text
    assert resp.json().get("details", {}).get("requires_break_glass") is True


# --- Direction 2 (RED before fix): missing / cross-tenant path deals must be 404, not 403 ---


def test_nonexistent_deal_returns_404_for_analyst(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    api, _store = ctx
    resp = api.get(f"/v1/deals/{uuid.uuid4()}", headers=_hdr(_ANALYST_A))
    assert resp.status_code == 404, resp.text


def test_nonexistent_deal_returns_404_for_admin_without_break_glass_leak(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    api, _store = ctx
    resp = api.get(f"/v1/deals/{uuid.uuid4()}", headers=_hdr(_ADMIN_A))
    assert resp.status_code == 404, resp.text
    # A missing deal must not advertise the break-glass affordance (that would leak existence).
    assert (resp.json().get("details") or {}).get("requires_break_glass") is None


def test_cross_tenant_deal_get_returns_404_not_403(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    api, _store = ctx
    deal_id = _create_deal(api, key=_ADMIN_A)  # owned by tenant A
    resp = api.get(f"/v1/deals/{deal_id}", headers=_hdr(_ADMIN_B))  # tenant B cannot see it
    assert resp.status_code == 404, resp.text
    assert (resp.json().get("details") or {}).get("requires_break_glass") is None


def test_cross_tenant_deal_subresource_is_not_denied_as_403(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    # A deal-scoped LIST subresource on an invisible deal must not emit the deal-scoped ABAC 403;
    # it falls through to the route, which returns its uniform non-leaking outcome (an empty list -
    # identical to an empty own-deal and to a nonexistent deal, so no existence oracle).
    api, _store = ctx
    deal_id = _create_deal(api, key=_ADMIN_A)
    resp = api.get(f"/v1/deals/{deal_id}/human-gates", headers=_hdr(_ADMIN_B))
    assert resp.status_code != 403, resp.text
    assert resp.json().get("code") != "ABAC_DENIED_BREAK_GLASS_REQUIRED"
    assert resp.status_code == 200 and resp.json().get("items") == []
