"""Slice96 Task 2 — duplicate-run safety (DEC-D): one active run per (tenant, deal).

RED-first. A second startRun while a QUEUED/RUNNING run exists for the deal must return a safe
RUN_ALREADY_ACTIVE (409). Enforced race-safely in Postgres by a partial UNIQUE index (migration
0023) and mirrored in the in-memory repo. Pinned PYTHONPATH=C:/Projects/IDIS/IDIS-slice96/src.
"""

from __future__ import annotations

import contextlib
import inspect
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    PostgresRunsRepository,
    RunAlreadyActiveError,
)

_TENANT = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_API_KEY = "test-api-key-tenant-a"
_DEAL = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_OTHER_DEAL = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_MIGRATION = Path("src/idis/persistence/migrations/versions/0023_runs_one_active_run_per_deal.py")


@pytest.fixture(autouse=True)
def _reset_stores() -> Iterator[None]:
    clear_deals_store()
    clear_runs_store()
    yield
    clear_deals_store()
    clear_runs_store()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    keys = {
        _API_KEY: {
            "tenant_id": _TENANT,
            "actor_id": "actor-a",
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST"],
        }
    }
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(keys))
    return TestClient(create_app(service_region="us-east-1"))


# --- repo-level enforcement (the in-memory mirror of the Postgres unique index) ---


def test_repo_rejects_second_active_run_for_same_deal() -> None:
    from idis.persistence.repositories.runs import RunAlreadyActiveError

    repo = InMemoryRunsRepository(_TENANT)
    repo.create(run_id="r1", deal_id=_DEAL, mode="FULL")
    assert repo.has_active_run(_DEAL) is True
    with pytest.raises(RunAlreadyActiveError):
        repo.create(run_id="r2", deal_id=_DEAL, mode="FULL")
    # A terminal run frees the deal for a new run.
    repo.try_cancel_active("r1")
    assert repo.has_active_run(_DEAL) is False
    repo.create(run_id="r3", deal_id=_DEAL, mode="FULL")  # no raise


def test_repo_active_run_scoping_is_per_tenant_and_deal() -> None:
    repo_a = InMemoryRunsRepository(_TENANT)
    repo_a.create(run_id="r1", deal_id=_DEAL, mode="FULL")
    repo_a.create(run_id="r2", deal_id=_OTHER_DEAL, mode="FULL")  # different deal -> allowed
    assert repo_a.has_active_run(_DEAL) is True
    assert repo_a.has_active_run(_OTHER_DEAL) is True
    # Different tenant, same deal id -> isolated, allowed.
    InMemoryRunsRepository(_TENANT_B).create(run_id="r3", deal_id=_DEAL, mode="FULL")


# --- API: a second startRun while an active run exists returns RUN_ALREADY_ACTIVE (409) ---


def test_start_run_returns_409_when_active_run_exists(client: TestClient) -> None:
    created = client.post(
        "/v1/deals",
        json={"name": "D", "company_name": "C"},
        headers={"X-IDIS-API-Key": _API_KEY},
    )
    assert created.status_code == 201
    deal_id = created.json()["deal_id"]
    # Seed an active QUEUED run for the deal (the concurrent-active-run condition).
    InMemoryRunsRepository(_TENANT).create(run_id="seed-run", deal_id=deal_id, mode="FULL")
    dup = client.post(
        f"/v1/deals/{deal_id}/runs",
        json={"mode": "SNAPSHOT"},
        headers={"X-IDIS-API-Key": _API_KEY},
    )
    assert dup.status_code == 409
    assert dup.json()["code"] == "RUN_ALREADY_ACTIVE"


# --- migration: race-safe partial UNIQUE index on active runs ---


def test_migration_pins_partial_unique_index_on_active_runs() -> None:
    src = _MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0023"' in src and 'down_revision = "0022"' in src
    assert "UNIQUE INDEX" in src
    assert "runs" in src and "tenant_id" in src and "deal_id" in src
    # Partial: only QUEUED/RUNNING (active) runs are constrained.
    assert "QUEUED" in src and "RUNNING" in src and "WHERE" in src


# --- concurrent Postgres race loser -> RUN_ALREADY_ACTIVE, transaction stays usable ---


class _FakeResult:
    def fetchone(self) -> None:
        return None  # has_active_run pre-check -> no active run


class _RaceLoserConn:
    """Fake SQLAlchemy connection: the has_active_run SELECT finds no active run, but the INSERT
    raises a unique-violation IntegrityError (as a concurrent race loser would). Records savepoint
    use and stays usable afterward, so a test can prove the outer transaction is not poisoned."""

    def __init__(self, constraint: str) -> None:
        self._constraint = constraint
        self.begin_nested_calls = 0
        self.executes_after_error = 0
        self._insert_failed = False

    def execute(self, statement: object, params: object = None) -> _FakeResult:
        if "INSERT INTO runs" in str(statement):
            self._insert_failed = True
            raise IntegrityError(
                "INSERT INTO runs ...",
                params,
                Exception(f'duplicate key value violates unique constraint "{self._constraint}"'),
            )
        if self._insert_failed:
            self.executes_after_error += 1  # proves the connection is still usable
        return _FakeResult()

    def begin_nested(self) -> contextlib.AbstractContextManager[None]:
        self.begin_nested_calls += 1
        return contextlib.nullcontext()


def test_postgres_create_maps_active_run_index_race_loser_to_run_already_active() -> None:
    conn = _RaceLoserConn("ux_runs_one_active_per_deal")
    repo = PostgresRunsRepository(conn, _TENANT)
    with pytest.raises(RunAlreadyActiveError):
        repo.create(run_id="r1", deal_id=_DEAL, mode="FULL")
    assert conn.begin_nested_calls == 1  # the INSERT ran inside a savepoint (outer tx not poisoned)
    assert repo.has_active_run(_DEAL) is False  # connection still usable after the caught violation
    assert conn.executes_after_error >= 1


def test_postgres_create_reraises_unrelated_integrity_errors() -> None:
    # Only the one-active-run index maps to RunAlreadyActiveError; any other constraint violation
    # (e.g. a duplicate run_id primary key) must propagate unchanged.
    conn = _RaceLoserConn("runs_pkey")
    repo = PostgresRunsRepository(conn, _TENANT)
    with pytest.raises(IntegrityError):
        repo.create(run_id="r1", deal_id=_DEAL, mode="FULL")


def test_postgres_create_uses_savepoint_and_maps_index_violation_source_pin() -> None:
    src = inspect.getsource(PostgresRunsRepository.create)
    assert "begin_nested" in src  # savepoint so the outer transaction is not poisoned
    assert "IntegrityError" in src
    assert "RunAlreadyActiveError" in src
