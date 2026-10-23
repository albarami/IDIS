"""RED-first strict-worker claim filtering for post-0038 run classifications."""

from __future__ import annotations

import inspect

import pytest

from idis.persistence.repositories import runs as runs_module
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    PostgresRunsRepository,
)
from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH

TENANT_ID = "11111111-1111-4111-8111-111111111111"
OWNER_ID = "22222222-2222-4222-8222-222222222222"
STRICT_VERSION = "full-strict-contract-test"
STRICT_HASH = "a" * 64


class _Rows:
    def fetchall(self) -> list[object]:
        return []


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement: object, params: dict[str, object]) -> _Rows:
        self.calls.append((str(statement), dict(params)))
        return _Rows()


def _strict_kwargs() -> dict[str, str]:
    return {
        "required_pipeline_contract_version": STRICT_VERSION,
        "required_pipeline_contract_hash": STRICT_HASH,
        "required_pipeline_resume_state": "RESUMABLE",
    }


def test_postgres_claim_has_one_optional_exact_contract_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = _Connection()
    repository = PostgresRunsRepository(connection, TENANT_ID)  # type: ignore[arg-type]

    repository.claim_queued_runs(
        limit=1,
        lease_owner_id=OWNER_ID,
        lease_seconds=60,
        **_strict_kwargs(),
    )

    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    normalized = " ".join(sql.upper().split())
    assert "PIPELINE_CONTRACT_VERSION = :REQUIRED_PIPELINE_CONTRACT_VERSION" in normalized
    assert "PIPELINE_CONTRACT_HASH = :REQUIRED_PIPELINE_CONTRACT_HASH" in normalized
    assert "PIPELINE_RESUME_STATE = :REQUIRED_PIPELINE_RESUME_STATE" in normalized
    assert params["required_pipeline_contract_version"] == STRICT_VERSION
    assert params["required_pipeline_contract_hash"] == STRICT_HASH
    assert params["required_pipeline_resume_state"] == "RESUMABLE"


def test_postgres_claim_requires_complete_bound_v7_input_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strict worker may not claim an admitted-looking but partial source graph."""

    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = _Connection()
    repository = PostgresRunsRepository(connection, TENANT_ID)  # type: ignore[arg-type]

    repository.claim_queued_runs(
        limit=1,
        lease_owner_id=OWNER_ID,
        lease_seconds=60,
        required_pipeline_contract_version="full-strict-v21",
        required_pipeline_contract_hash=FULL_STRICT_V21_CONTRACT_HASH,
        required_pipeline_resume_state="RESUMABLE",
    )

    sql, params = connection.calls[0]
    normalized = " ".join(sql.upper().split())
    assert "RUN_INPUT_BINDINGS" in normalized
    assert "SOURCE_LINEAGE_OBJECTS" in normalized
    assert "DATA_ROOM_PACKAGES" in normalized
    assert "SOURCE_BINDING_STATE = 'BOUND'" in normalized
    assert "STATUS = 'SEALED'" in normalized
    assert "NOT EXISTS" in normalized
    assert params["superseded_full_strict_v16_version"] == "full-strict-v16"
    assert params["superseded_full_strict_v17_version"] == "full-strict-v17"
    assert params["superseded_full_strict_v18_version"] == "full-strict-v18"
    assert params["superseded_full_strict_v20_version"] == "full-strict-v20"
    assert params["full_strict_v21_version"] == "full-strict-v21"
    assert params["full_strict_v21_hash"] == FULL_STRICT_V21_CONTRACT_HASH


def test_contract_filter_is_all_or_nothing_and_worker_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    repository = PostgresRunsRepository(_Connection(), TENANT_ID)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="complete tuple"):
        repository.claim_queued_runs(
            limit=1,
            lease_owner_id=OWNER_ID,
            lease_seconds=60,
            required_pipeline_contract_version=STRICT_VERSION,
        )
    with pytest.raises(ValueError, match="worker"):
        repository.claim_queued_runs(limit=1, **_strict_kwargs())


def test_in_memory_strict_claim_skips_non_resumable_legacy_row() -> None:
    runs_module._in_memory_runs_store.clear()
    repository = InMemoryRunsRepository(TENANT_ID)
    legacy = repository.create(
        run_id="33333333-3333-4333-8333-333333333333",
        deal_id="44444444-4444-4444-8444-444444444444",
        mode="FULL",
    )
    assert legacy["pipeline_resume_state"] == "NON_RESUMABLE"

    claimed = repository.claim_queued_runs(
        limit=1,
        lease_owner_id=OWNER_ID,
        lease_seconds=60,
        **_strict_kwargs(),
    )

    assert claimed == []
    assert legacy["status"] == "QUEUED"


def test_generic_claim_signature_remains_backward_compatible() -> None:
    parameters = inspect.signature(InMemoryRunsRepository.claim_queued_runs).parameters
    assert parameters["required_pipeline_contract_version"].default is None
    assert parameters["required_pipeline_contract_hash"].default is None
    assert parameters["required_pipeline_resume_state"].default is None
