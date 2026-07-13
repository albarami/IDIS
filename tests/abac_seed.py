"""Shared test helper: seed deal-scoped ABAC assignments (Slice98 Task 2.6).

After Task 2.5, deal-scoped endpoints are correctly ABAC-gated (deny-by-default). Tests that
exercise AUTHORIZED deal workflows must grant the operating actor an assignment first. This seeds
it through the EXACT default store the app/middleware consults (``get_deal_assignment_store``) - no
side store, no ABAC bypass, no production auto-assignment. Tests that intentionally cover
unassigned/unauthorized/cross-tenant access must NOT call this (they keep the new 403).
"""

from __future__ import annotations


def seed_deal_access(tenant_id: str, deal_id: str, *actor_ids: str) -> None:
    """Assign the given actor(s) to the deal under their tenant, via the app's default store."""
    from idis.api.abac import get_deal_assignment_store

    store = get_deal_assignment_store()
    for actor_id in actor_ids:
        store.add_assignment(tenant_id, deal_id, actor_id)
