"""Slice98 Task 2.5 - RBACMiddleware deal_id extraction for plain deal-scoped endpoints.

RED-first. Regression fix: ``_extract_resource_context`` did not extract ``deal_id`` from the URL
path (only ``claim_id``/``run_id``), and ``request.path_params`` is empty during Starlette
``BaseHTTPMiddleware`` - so plain deal-scoped endpoints like ``GET /v1/deals/{dealId}`` were NOT
ABAC-gated (an unassigned same-tenant ANALYST got 200). This proves the fix: unassigned -> 403,
assigned -> 200, unassigned ADMIN -> break-glass-required 403, while the Task 2 access-admin routes
(ADMIN-only, NOT deal-scoped) remain unblocked. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
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
_ADMIN = "admin-key-25"
_ANALYST = "analyst-key-25"


def _api_keys() -> str:
    def _entry(actor: str, roles: list[str]) -> dict[str, Any]:
        return {
            "tenant_id": _TENANT_A,
            "actor_id": actor,
            "name": actor,
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": roles,
        }

    return json.dumps(
        {_ADMIN: _entry("admin-a", ["ADMIN"]), _ANALYST: _entry("analyst-a", ["ANALYST"])}
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


def _create_deal(api: TestClient) -> str:
    resp = api.post("/v1/deals", json={"name": "D", "company_name": "Acme"}, headers=_hdr(_ADMIN))
    assert resp.status_code == 201, resp.text
    return resp.json()["deal_id"]


def test_unassigned_analyst_get_deal_is_denied(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    api, _store = ctx
    deal_id = _create_deal(api)
    resp = api.get(f"/v1/deals/{deal_id}", headers=_hdr(_ANALYST))
    assert resp.status_code == 403, resp.text  # deny-by-default deal-scoped ABAC


def test_assigned_analyst_get_deal_is_allowed(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    api, store = ctx
    deal_id = _create_deal(api)
    store.add_assignment(_TENANT_A, deal_id, "analyst-a")
    resp = api.get(f"/v1/deals/{deal_id}", headers=_hdr(_ANALYST))
    assert resp.status_code == 200, resp.text


def test_unassigned_admin_get_deal_requires_break_glass(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    api, _store = ctx
    deal_id = _create_deal(api)
    resp = api.get(f"/v1/deals/{deal_id}", headers=_hdr(_ADMIN))
    assert resp.status_code == 403  # unassigned ADMIN -> break-glass-required (unchanged behavior)
    body = resp.json()
    assert body.get("details", {}).get("requires_break_glass") is True


def test_deal_subresource_is_also_gated(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    # /v1/deals/{uuid}/... deal-scoped GETs are gated too (unassigned -> 403), no existence oracle.
    api, _store = ctx
    deal_id = _create_deal(api)
    resp = api.get(f"/v1/deals/{deal_id}/human-gates", headers=_hdr(_ANALYST))
    assert resp.status_code == 403, resp.text


def test_access_admin_route_not_blocked_by_deal_id_fallback(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    # Task 2 invariant: access-admin routes are ADMIN-only and NOT deal-scoped, so the new deal_id
    # fallback must NOT ABAC-gate them - an unassigned ADMIN can still assign into the deal.
    api, _store = ctx
    deal_id = _create_deal(api)
    resp = api.post(
        f"/v1/deals/{deal_id}/assignments", json={"actor_id": "analyst-a"}, headers=_hdr(_ADMIN)
    )
    assert resp.status_code == 201, resp.text
    # and that assignment now flips the analyst's deal read to allowed
    assert api.get(f"/v1/deals/{deal_id}", headers=_hdr(_ANALYST)).status_code == 200


def test_unassigned_analyst_unknown_deal_returns_404_no_oracle(
    ctx: tuple[TestClient, InMemoryDealAssignmentStore],
) -> None:
    # A nonexistent (or cross-tenant) path deal falls through to the route's uniform 404 - the
    # deal-scoped ABAC 403 applies only to deals the caller can actually see (ADR-011). This keeps
    # a missing deal indistinguishable from another tenant's, and never leaks a break-glass
    # affordance for a deal that does not exist in the caller's tenant.
    api, _store = ctx
    import uuid

    resp = api.get(f"/v1/deals/{uuid.uuid4()}", headers=_hdr(_ANALYST))
    assert resp.status_code == 404, resp.text
