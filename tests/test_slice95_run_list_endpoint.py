"""Slice95 Task 4 — run-listing endpoint GET /v1/deals/{deal_id}/runs (G4 backend).

A reviewer must be able to enumerate a deal's runs (the monitor entry point) without already
knowing a run_id. Returns a paginated list of safe run summaries (ids/status/mode/timestamps);
per-run blocker detail + step ledger stay in GET /v1/runs/{run_id}.

Injected fakes only — no real LLM, no DB (in-memory runs store), no migration (DEC-E).
PYTHONPATH is pinned to this worktree's src for every run.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.openapi_loader import load_openapi_spec
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    _in_memory_runs_store,
    clear_in_memory_runs_store,
)

_TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DEAL_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_OTHER_DEAL_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_API_KEY = "test-key-run-list-reviewer"
_KEYS = {
    _API_KEY: {
        "tenant_id": _TENANT_ID,
        "actor_id": "actor-run-list",
        "name": "Run List Reviewer",
        "timezone": "UTC",
        "data_region": "us-east-1",
        "roles": ["ADMIN"],
    }
}

_ITEM_FIELDS = {"run_id", "deal_id", "status", "mode", "started_at", "finished_at"}


@pytest.fixture(autouse=True)
def _clear_runs() -> Iterator[None]:
    clear_in_memory_runs_store()
    yield
    clear_in_memory_runs_store()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_KEYS))
    return TestClient(create_app(service_region="us-east-1"))


def _seed_run(run_id: str, deal_id: str, mode: str) -> None:
    # Seed terminal (historical) runs: Slice96 DEC-D allows at most one active run per (tenant,
    # deal), so a deal's listable run history is terminal runs plus at most one active.
    repo = InMemoryRunsRepository(_TENANT_ID)
    repo.create(run_id=run_id, deal_id=deal_id, mode=mode)
    repo.update_status(run_id, status="SUCCEEDED", finished_at=None)


def test_list_deal_runs_requires_auth(client: TestClient) -> None:
    assert client.get(f"/v1/deals/{_DEAL_ID}/runs").status_code == 401


def test_list_deal_runs_returns_safe_items_for_the_deal(client: TestClient) -> None:
    _seed_run("11111111-1111-1111-1111-111111111111", _DEAL_ID, "FULL")
    _seed_run("22222222-2222-2222-2222-222222222222", _DEAL_ID, "SNAPSHOT")
    _seed_run("33333333-3333-3333-3333-333333333333", _OTHER_DEAL_ID, "FULL")

    resp = client.get(f"/v1/deals/{_DEAL_ID}/runs", headers={"X-IDIS-API-Key": _API_KEY})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {"items", "next_cursor"}
    # Only this deal's runs (the other deal's run is excluded).
    assert len(body["items"]) == 2
    assert {item["run_id"] for item in body["items"]} == {
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    }
    for item in body["items"]:
        assert set(item) == _ITEM_FIELDS
        assert item["deal_id"] == _DEAL_ID


def test_list_deal_runs_empty_for_deal_with_no_runs(client: TestClient) -> None:
    resp = client.get(f"/v1/deals/{_DEAL_ID}/runs", headers={"X-IDIS-API-Key": _API_KEY})
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "next_cursor": None}


def test_list_deal_runs_paginates_with_cursor(client: TestClient) -> None:
    for index in range(3):
        _seed_run(f"{index}0000000-0000-0000-0000-000000000000", _DEAL_ID, "FULL")

    first = client.get(
        f"/v1/deals/{_DEAL_ID}/runs?limit=2", headers={"X-IDIS-API-Key": _API_KEY}
    ).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None


def test_pagination_does_not_drop_rows_with_equal_created_at(client: TestClient) -> None:
    # A page boundary must not fall between rows that share created_at. With a created_at-only
    # cursor the next page's "created_at < cursor" drops every tied row; a (created_at, run_id)
    # composite cursor keeps them.
    run_ids = [
        "a1111111-1111-1111-1111-111111111111",
        "b2222222-2222-2222-2222-222222222222",
        "c3333333-3333-3333-3333-333333333333",
    ]
    for run_id in run_ids:
        _seed_run(run_id, _DEAL_ID, "FULL")
    for run_id in run_ids:
        _in_memory_runs_store[run_id]["created_at"] = "2026-01-01T00:00:00.000000Z"

    seen: set[str] = set()
    cursor: str | None = None
    for _ in range(10):  # bounded: each page returns <= limit rows
        params = {"limit": "2"}
        if cursor is not None:
            params["cursor"] = cursor
        body = client.get(
            f"/v1/deals/{_DEAL_ID}/runs", params=params, headers={"X-IDIS-API-Key": _API_KEY}
        ).json()
        seen.update(item["run_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == set(run_ids), f"pagination dropped rows: missing {set(run_ids) - seen}"


def test_run_list_schemas_static_matches_generated_contract() -> None:
    # Lock the FULL static-vs-generated contract for the run-list schemas — required, properties,
    # and additionalProperties — so the YAML source of truth cannot drift from the runtime models.
    static = load_openapi_spec()["components"]["schemas"]
    generated = create_app().openapi()["components"]["schemas"]
    for schema_name in ("RunListItem", "PaginatedRunList"):
        static_schema = static[schema_name]
        generated_schema = generated[schema_name]
        assert set(static_schema.get("required", [])) == set(
            generated_schema.get("required", [])
        ), (
            f"{schema_name} required drift: static={static_schema.get('required')} "
            f"generated={generated_schema.get('required')}"
        )
        assert set(static_schema.get("properties", {})) == set(
            generated_schema.get("properties", {})
        ), f"{schema_name} properties drift"
        assert static_schema.get("additionalProperties") == generated_schema.get(
            "additionalProperties"
        ), f"{schema_name} additionalProperties drift"
