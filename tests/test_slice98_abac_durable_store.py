"""Slice98 Task 1 - durable ABAC assignment store: seam + default selection (hermetic).

RED-first. Production must not silently rely on the in-memory assignment store: the existing
``get_deal_assignment_store`` seam must build the Postgres-backed twin when Postgres is configured
and keep the in-memory store for dev/tests. The Postgres twin satisfies the existing
``DealAssignmentStore`` protocol and fails CLOSED (403 ``ABAC_RESOLUTION_FAILED``, the module's own
precedent) when the database errors - never a silent allow. Durable/RLS behavior is proven
env-gated in ``test_slice98_abac_durable_store_postgres.py``. PYTHONPATH pinned to this worktree's
src.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from idis.api.abac import (
    InMemoryDealAssignmentStore,
    PostgresDealAssignmentStore,
    build_default_deal_assignment_store,
    get_deal_assignment_store,
    reset_deal_assignment_store,
    set_deal_assignment_store,
)
from idis.api.errors import IdisHttpError

_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DEAL = "dddddddd-dddd-dddd-dddd-dddddddddddd"


@pytest.fixture(autouse=True)
def _reset_seam() -> Iterator[None]:
    reset_deal_assignment_store()
    yield
    reset_deal_assignment_store()


def test_default_store_is_in_memory_without_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    import idis.persistence.db as db_mod

    monkeypatch.setattr(db_mod, "is_postgres_configured", lambda: False)
    store = get_deal_assignment_store()
    assert isinstance(store, InMemoryDealAssignmentStore)  # dev/test fallback preserved


def test_default_store_is_postgres_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import idis.persistence.db as db_mod

    monkeypatch.setattr(db_mod, "is_postgres_configured", lambda: True)
    store = build_default_deal_assignment_store()
    assert isinstance(store, PostgresDealAssignmentStore)  # production never relies on memory
    # and the lazy seam caches the same selection
    cached = get_deal_assignment_store()
    assert isinstance(cached, PostgresDealAssignmentStore)


def test_set_and_reset_seam_still_work() -> None:
    injected = InMemoryDealAssignmentStore()
    set_deal_assignment_store(injected)
    assert get_deal_assignment_store() is injected  # explicit test injection unchanged
    reset_deal_assignment_store()
    assert get_deal_assignment_store() is not injected


def test_postgres_store_satisfies_assignment_store_protocol() -> None:
    store = PostgresDealAssignmentStore()
    assert callable(store.is_actor_assigned)
    assert callable(store.is_actor_in_deal_group)


def test_postgres_store_fails_closed_on_db_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A DB failure during an access check must DENY loudly (403 ABAC_RESOLUTION_FAILED, matching
    # the module's resolver precedent) - never allow, never silently swallow.
    import idis.persistence.db as db_mod

    def _boom() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(db_mod, "begin_app_conn", _boom)
    store = PostgresDealAssignmentStore()
    with pytest.raises(IdisHttpError) as exc_info:
        store.is_actor_assigned(_TENANT, _DEAL, "actor-1")
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "ABAC_RESOLUTION_FAILED"
    with pytest.raises(IdisHttpError):
        store.is_actor_in_deal_group(_TENANT, _DEAL, "actor-1")
