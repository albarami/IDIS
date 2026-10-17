"""Real app-role one-use/rollback and retired-entry coverage, synthetic mechanics only."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from idis.persistence.db import get_app_engine, set_tenant_local
from idis.persistence.repositories.runs import RunAlreadyActiveError
from idis.persistence.repositories.strict_preflight_proofs import (
    PostgresStrictPreflightProofsRepository,
    StrictPreflightProofError,
)
from idis.pipeline import contract_registry
from tests._cir04c2_admission_helpers import (
    VERSION,
    SyntheticAdmissionProof,
    SyntheticFrozenSource,
    admission_repository,
    admit_synthetic_proof,
    insert_synthetic_proof,
    seed_synthetic_frozen_source,
)
from tests.test_slice98_erasure_export_postgres import _pg_schema as _pg_schema


@pytest.fixture
def source(_pg_schema: None) -> SyntheticFrozenSource:
    return seed_synthetic_frozen_source(get_app_engine(), label="proof-admission")


def _snapshot(source: SyntheticFrozenSource) -> dict[str, tuple[Any, ...]]:
    """Fresh app connection captures row content, not merely counts."""
    result = {}
    with get_app_engine().begin() as conn:
        set_tenant_local(conn, source.tenant_id)
        for table in (
            "document_artifacts",
            "documents",
            "document_spans",
            "document_parse_attempts",
            "data_room_packages",
            "data_room_package_files",
            "source_lineage_objects",
            "strict_preflight_proofs",
            "runs",
            "run_steps",
            "run_input_bindings",
            "run_expected_consumer_edges",
        ):
            rows = (
                conn.execute(
                    text(
                        f"SELECT row_to_json(r)::text FROM {table} r "
                        "WHERE tenant_id=CAST(:tenant_id AS uuid) ORDER BY row_to_json(r)::text"
                    ),
                    {"tenant_id": source.tenant_id},
                )
                .scalars()
                .all()
            )
            result[table] = tuple(rows)
    return result


def _proof_state(proof: SyntheticAdmissionProof) -> tuple[Any, ...]:
    with get_app_engine().begin() as conn:
        set_tenant_local(conn, proof.source.tenant_id)
        return tuple(
            conn.execute(
                text(
                    "SELECT consumed_at,consumed_by_run_id::text FROM strict_preflight_proofs "
                    "WHERE proof_id=CAST(:proof_id AS uuid)"
                ),
                {"proof_id": proof.proof_id},
            ).one()
        )


def _old_consume(
    repository: PostgresStrictPreflightProofsRepository, proof: SyntheticAdmissionProof
) -> dict[str, Any]:
    return repository.consume_and_create_run(
        proof_id=proof.proof_id,
        expected_binding=proof.binding,
        run_id=str(uuid.uuid4()),
        deal_id=proof.source.deal_id,
        mode="FULL",
        idempotency_key="retired-entry-probe",
        source=None,
        created_by_actor_id="synthetic-test",
        created_by_actor_type="SERVICE",
    )


@pytest.mark.parametrize(
    "version",
    [
        "full-strict-v2",
        VERSION,
        "full-strict-v16",
        "full-strict-v17",
        "full-strict-v18",
        "full-strict-v20",
        "full-v1",
        "unknown-contract",
    ],
)
def test_old_entry_rejects_every_valid_proof_without_mutation(
    source: SyntheticFrozenSource,
    version: str,
) -> None:
    proof = insert_synthetic_proof(get_app_engine(), source, version=version)
    before = _snapshot(source)
    with get_app_engine().begin() as conn, pytest.raises(StrictPreflightProofError) as caught:
        _old_consume(PostgresStrictPreflightProofsRepository(conn, source.tenant_id), proof)
    assert caught.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert _proof_state(proof) == (None, None)
    assert _snapshot(source) == before
    assert before["runs"] == before["run_steps"] == ()


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize(
    ("condition", "code"),
    [
        ("expired", "STRICT_PREFLIGHT_PROOF_EXPIRED"),
        ("mismatch", "STRICT_PREFLIGHT_PROOF_MISMATCH"),
        ("missing", "STRICT_PREFLIGHT_PROOF_INVALID"),
    ],
)
def test_validation_precedence_is_retained(
    source: SyntheticFrozenSource,
    modern: bool,
    condition: str,
    code: str,
) -> None:
    proof = insert_synthetic_proof(
        get_app_engine(),
        source,
        expires_at=datetime.now(UTC) - timedelta(seconds=1) if condition == "expired" else None,
    )
    expected = proof
    if condition == "mismatch":
        expected = replace(proof, binding=replace(proof.binding, corpus_hash="f" * 64))
    if condition == "missing":
        expected = replace(proof, proof_id=str(uuid.uuid4()))
    before = _snapshot(source)
    with get_app_engine().begin() as conn:
        repo = admission_repository(conn, source.tenant_id)
        with pytest.raises(StrictPreflightProofError) as caught:
            if modern:
                admit_synthetic_proof(repo, expected, str(uuid.uuid4()))
            else:
                _old_consume(repo, expected)
    assert caught.value.code == code
    assert _proof_state(proof) == (None, None)
    assert _snapshot(source) == before


def test_modern_admission_is_durable_one_use_and_retains_global_registry(
    source: SyntheticFrozenSource,
) -> None:
    registry_before = dict(contract_registry.PIPELINE_CONTRACTS)
    proof = insert_synthetic_proof(get_app_engine(), source)
    before = _snapshot(source)
    run_id = str(uuid.uuid4())
    with get_app_engine().begin() as conn:
        run = admit_synthetic_proof(admission_repository(conn, source.tenant_id), proof, run_id)
    assert run["pipeline_contract_version"] == VERSION
    assert run["pipeline_resume_state"] == "RESUMABLE"
    assert run["strict_preflight_proof_id"] == proof.proof_id
    assert _proof_state(proof)[1] == run_id
    with get_app_engine().begin() as conn:
        admission_repository(conn, source.tenant_id).require_consumed_for_run(
            proof_id=proof.proof_id,
            run_id=run_id,
            deal_id=source.deal_id,
        )
    admitted = _snapshot(source)
    assert len(admitted["runs"]) == 1
    assert len(admitted["run_steps"]) == len(contract_registry.PIPELINE_CONTRACTS[VERSION].steps)
    assert len(admitted["run_input_bindings"]) == 6
    assert len(admitted["run_expected_consumer_edges"]) > 0
    for table in before.keys() - {
        "strict_preflight_proofs",
        "runs",
        "run_steps",
        "run_input_bindings",
        "run_expected_consumer_edges",
    }:
        assert admitted[table] == before[table]
    for modern in (False, True):
        with get_app_engine().begin() as conn:
            repo = admission_repository(conn, source.tenant_id)
            with pytest.raises(StrictPreflightProofError) as caught:
                if modern:
                    admit_synthetic_proof(repo, proof, str(uuid.uuid4()))
                else:
                    _old_consume(repo, proof)
        assert caught.value.code == "STRICT_PREFLIGHT_PROOF_ALREADY_CONSUMED"
        assert _snapshot(source) == admitted
    assert dict(contract_registry.PIPELINE_CONTRACTS) == registry_before


def test_modern_active_conflict_leaves_second_proof_and_graph_unchanged(
    source: SyntheticFrozenSource,
) -> None:
    first = insert_synthetic_proof(get_app_engine(), source)
    second = insert_synthetic_proof(get_app_engine(), source)
    with get_app_engine().begin() as conn:
        admit_synthetic_proof(
            admission_repository(conn, source.tenant_id), first, str(uuid.uuid4())
        )
    before = _snapshot(source)
    with get_app_engine().begin() as conn, pytest.raises(RunAlreadyActiveError):
        admit_synthetic_proof(
            admission_repository(conn, source.tenant_id), second, str(uuid.uuid4())
        )
    assert _proof_state(second) == (None, None)
    assert _snapshot(source) == before


@pytest.mark.parametrize("failure", ["mark_false", "mark_raise", "after_edges", "final_expiry"])
def test_modern_real_savepoint_rolls_back_every_admission_write(
    source: SyntheticFrozenSource,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    proof = insert_synthetic_proof(
        get_app_engine(),
        source,
        expires_at=datetime.now(UTC) + timedelta(seconds=2) if failure == "final_expiry" else None,
    )
    before = _snapshot(source)
    observed = []
    run_id = str(uuid.uuid4())
    with get_app_engine().begin() as conn:
        repo = admission_repository(conn, source.tenant_id)
        original_mark = repo._mark_consumed

        def fail_mark(*, proof_id: str, run_id: str) -> bool:
            # Observe actual SQL inserts inside the still-live savepoint.
            observed.append(
                conn.execute(
                    text("SELECT count(*) FROM run_steps WHERE run_id=CAST(:run_id AS uuid)"),
                    {"run_id": run_id},
                ).scalar_one()
            )
            if failure == "mark_raise":
                raise StrictPreflightProofError("INJECTED_ADMISSION_FAILURE")
            if failure == "final_expiry":
                # Proofs are immutable. Let the real statement clock pass the
                # pre-existing short expiry; never modify or bypass that guard.
                delay = float(
                    conn.execute(
                        text(
                            "SELECT GREATEST(0,EXTRACT(EPOCH FROM "
                            "expires_at-statement_timestamp())) "
                            "FROM strict_preflight_proofs WHERE proof_id=CAST(:proof_id AS uuid)"
                        ),
                        {"proof_id": proof_id},
                    ).scalar_one()
                )
                assert 0 <= delay <= 3  # Bound host/database clock skew as well as the wait.
                conn.execute(text("SELECT pg_sleep(:delay)"), {"delay": delay + 0.02})
                return original_mark(proof_id=proof_id, run_id=run_id)
            return False

        if failure != "after_edges":
            monkeypatch.setattr(repo, "_mark_consumed", fail_mark)
        else:
            original_load = repo._load_task7_admission_edges

            def fail_after_edges(**kwargs: Any) -> tuple[Any, ...]:
                rows = original_load(**kwargs)
                assert rows
                assert (
                    conn.execute(
                        text(
                            "SELECT consumed_by_run_id::text FROM strict_preflight_proofs "
                            "WHERE proof_id=CAST(:proof_id AS uuid)"
                        ),
                        {"proof_id": proof.proof_id},
                    ).scalar_one()
                    == run_id
                )
                observed.append(len(rows))
                raise StrictPreflightProofError("INJECTED_ADMISSION_FAILURE")

            monkeypatch.setattr(repo, "_load_task7_admission_edges", fail_after_edges)
        with pytest.raises(StrictPreflightProofError) as caught:
            admit_synthetic_proof(repo, proof, run_id)
    assert (
        caught.value.code
        == {
            "mark_false": "STRICT_PREFLIGHT_PROOF_INVALID",
            "mark_raise": "INJECTED_ADMISSION_FAILURE",
            "after_edges": "INJECTED_ADMISSION_FAILURE",
            "final_expiry": "STRICT_PREFLIGHT_PROOF_EXPIRED",
        }[failure]
    )
    assert observed
    if failure != "after_edges":
        assert observed == [len(contract_registry.PIPELINE_CONTRACTS[VERSION].steps)]
    assert _proof_state(proof) == (None, None)
    assert _snapshot(source) == before


def test_modern_cross_tenant_source_and_proof_are_invisible(source: SyntheticFrozenSource) -> None:
    proof = insert_synthetic_proof(get_app_engine(), source)
    before = _snapshot(source)
    with get_app_engine().begin() as conn:
        repo = admission_repository(conn, str(uuid.uuid4()))
        assert repo._load_for_update(proof.proof_id) is None
        assert (
            conn.execute(
                text(
                    "UPDATE strict_preflight_proofs SET binding_hash=:replacement "
                    "WHERE proof_id=CAST(:proof_id AS uuid)"
                ),
                {"replacement": "f" * 64, "proof_id": proof.proof_id},
            ).rowcount
            == 0
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM source_lineage_objects "
                    "WHERE corpus_receipt_id=CAST(:id AS uuid)"
                ),
                {"id": source.package_id},
            ).scalar_one()
            == 0
        )
        with pytest.raises(StrictPreflightProofError) as caught:
            admit_synthetic_proof(repo, proof, str(uuid.uuid4()))
    assert caught.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert _snapshot(source) == before
