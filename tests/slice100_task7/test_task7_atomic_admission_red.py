"""Contracts for atomic full-strict-v21 run admission."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

import idis.api.routes.runs as runs_route
from idis.persistence.repositories.strict_preflight_proofs import (
    PostgresStrictPreflightProofsRepository,
    StrictPreflightBinding,
    StrictPreflightProofError,
    StrictPreflightProofRecord,
)
from idis.pipeline import contract_registry

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
PROOF_ID = "33333333-3333-4333-8333-333333333333"
RUN_ID = "44444444-4444-4444-8444-444444444444"
PACKAGE_ID = "77777777-7777-4777-8777-777777777777"
PACKAGE_ROOT_ID = "88888888-8888-4888-8888-888888888888"
DOCUMENT_ROOT_ID = "99999999-9999-4999-8999-999999999999"
DOCUMENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CONTRACT_HASH = contract_registry.FULL_STRICT_V21_CONTRACT_HASH


class _Savepoint(AbstractContextManager[None]):
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __enter__(self) -> None:
        self._events.append("transaction_begin")
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self._events.append("transaction_rollback" if exc_type else "transaction_commit")
        return False


class _Connection:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self.events)


def _binding(*, contract_hash: str = CONTRACT_HASH) -> StrictPreflightBinding:
    return StrictPreflightBinding(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        corpus_hash="1" * 64,
        strict_profile_generation="bootstrap-v1",
        strict_profile_hmac="2" * 64,
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=contract_hash,
        expected_consumer_edge_rule_hash="3" * 64,
        static_decision_set_hash="4" * 64,
        methodology_version_hashes=("5" * 64, "6" * 64),
        promoted_pointer_hash="7" * 64,
        provider_receipt_set_hash="8" * 64,
        dependency_receipt_set_hash="9" * 64,
    )


def _record(binding: StrictPreflightBinding) -> StrictPreflightProofRecord:
    now = datetime.now(UTC)
    return StrictPreflightProofRecord(
        proof_id=PROOF_ID,
        binding=binding,
        binding_hash=binding.digest(),
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
        consumed_at=None,
        consumed_by_run_id=None,
    )


def _activated_registry() -> Any:
    registry = dict(contract_registry.PIPELINE_CONTRACTS)
    registry["full-strict-v21"] = replace(
        registry["full-strict-v21"],
        state=contract_registry.PipelineContractState.ACTIVE,
    )
    return MappingProxyType(registry)


def _source_roots() -> tuple[dict[str, Any], ...]:
    return (
        {
            "source_lineage_object_id": PACKAGE_ROOT_ID,
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "corpus_receipt_id": PACKAGE_ID,
            "object_kind": "PACKAGE",
            "source_version": "source-package-manifest-v1:" + "a" * 64,
            "source_disposition": "CORPUS",
            "object_identity_sha256": "b" * 64,
            "content_sha256": "c" * 64,
            "corpus_receipt_sha256": "a" * 64,
        },
        {
            "source_lineage_object_id": DOCUMENT_ROOT_ID,
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "corpus_receipt_id": PACKAGE_ID,
            "object_kind": "DOCUMENT",
            "source_version": "parsed-document-v1:" + "d" * 64,
            "source_disposition": "EVIDENCE_BEARING",
            "object_identity_sha256": "e" * 64,
            "content_sha256": "f" * 64,
            "corpus_receipt_sha256": "a" * 64,
        },
    )


def _repository(
    *,
    registry: Any,
    loaded_rows: tuple[dict[str, Any], ...] | None = None,
    loaded_bindings: tuple[Any, ...] | None = None,
    loaded_edges: tuple[Any, ...] | None = None,
) -> tuple[
    PostgresStrictPreflightProofsRepository,
    list[str],
    list[dict[str, Any]],
    list[Any],
    list[Any],
]:
    events: list[str] = []
    staged_rows: list[dict[str, Any]] = []
    staged_bindings: list[Any] = []
    staged_edges: list[Any] = []
    binding = _binding()

    class Repository(PostgresStrictPreflightProofsRepository):
        def _lock_task7_source_roots(
            self,
            *,
            deal_id: str,
            package_ids: tuple[str, ...],
        ) -> tuple[dict[str, Any], ...]:
            assert deal_id == DEAL_ID
            assert package_ids == (PACKAGE_ID,)
            events.append("bound_sources_locked")
            return _source_roots()

        def _load_task7_source_roots_read_only(
            self,
            *,
            deal_id: str,
            package_ids: tuple[str, ...],
        ) -> tuple[dict[str, Any], ...]:
            assert deal_id == DEAL_ID
            assert package_ids == (PACKAGE_ID,)
            events.append("bound_sources_read")
            return _source_roots()

        def _load_task7_run_for_worker(
            self,
            *,
            deal_id: str,
            run_id: str,
        ) -> dict[str, Any]:
            assert deal_id == DEAL_ID
            assert run_id == RUN_ID
            events.append("run_read")
            return {
                "status": "RUNNING",
                "pipeline_contract_version": "full-strict-v21",
                "pipeline_contract_hash": CONTRACT_HASH,
                "pipeline_resume_state": "RESUMABLE",
            }

        def _load_for_update(self, proof_id: str) -> StrictPreflightProofRecord:
            assert proof_id == PROOF_ID
            events.append("proof_locked")
            return _record(binding)

        def _insert_task7_run(self, **values: Any) -> dict[str, Any]:
            events.append("run_inserted")
            assert values["pipeline_contract_version"] == "full-strict-v21"
            assert values["pipeline_contract_hash"] == CONTRACT_HASH
            assert values["pipeline_resume_state"] == "RESUMABLE"
            assert values["pipeline_resume_error_code"] is None
            return dict(values, tenant_id=TENANT_ID, status="QUEUED")

        def _insert_task7_steps(self, *, rows: tuple[dict[str, Any], ...]) -> None:
            events.append("steps_inserted")
            staged_rows.extend(rows)

        def _load_task7_steps(self, *, run_id: str) -> tuple[dict[str, Any], ...]:
            assert run_id == RUN_ID
            events.append("steps_reloaded")
            return loaded_rows if loaded_rows is not None else tuple(staged_rows)

        def _insert_task7_input_bindings(self, *, rows: tuple[Any, ...]) -> None:
            events.append("bindings_inserted")
            staged_bindings.extend(rows)

        def _load_task7_input_bindings(
            self,
            *,
            deal_id: str,
            run_id: str,
        ) -> tuple[Any, ...]:
            assert deal_id == DEAL_ID
            assert run_id == RUN_ID
            events.append("bindings_reloaded")
            return loaded_bindings if loaded_bindings is not None else tuple(staged_bindings)

        def _insert_task7_admission_edges(self, *, rows: tuple[Any, ...]) -> None:
            events.append("admission_edges_inserted")
            staged_edges.extend(rows)

        def _load_task7_admission_edges(
            self,
            *,
            deal_id: str,
            run_id: str,
        ) -> tuple[Any, ...]:
            assert deal_id == DEAL_ID
            assert run_id == RUN_ID
            events.append("admission_edges_reloaded")
            return loaded_edges if loaded_edges is not None else tuple(staged_edges)

        def _mark_consumed(self, *, proof_id: str, run_id: str) -> bool:
            assert proof_id == PROOF_ID
            assert run_id == RUN_ID
            events.append("proof_consumed")
            return True

    repository = Repository(  # type: ignore[arg-type]
        _Connection(events),
        TENANT_ID,
        pipeline_contract_registry=registry,
    )
    return repository, events, staged_rows, staged_bindings, staged_edges


def _admit(repository: PostgresStrictPreflightProofsRepository) -> dict[str, Any]:
    return repository.consume_and_admit_contract_run(
        proof_id=PROOF_ID,
        expected_binding=_binding(),
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        idempotency_key="task7-admission",
        source={"type": "deal_documents"},
        created_by_actor_id="actor-1",
        created_by_actor_type="SERVICE",
        selected_source_package_ids=(PACKAGE_ID,),
    )


def test_inactive_v5_fails_before_any_run_or_step_mutation() -> None:
    repository, events, _rows, _bindings, _edges = _repository(
        registry=contract_registry.PIPELINE_CONTRACTS
    )

    with pytest.raises(StrictPreflightProofError) as exc_info:
        _admit(repository)

    assert exc_info.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert events == [
        "transaction_begin",
        "bound_sources_locked",
        "proof_locked",
        "transaction_rollback",
    ]


def test_atomic_admission_inserts_run_and_all_28_steps_before_consuming_proof() -> None:
    repository, events, rows, bindings, edges = _repository(registry=_activated_registry())

    run = _admit(repository)

    assert run["pipeline_contract_version"] == "full-strict-v21"
    assert len(rows) == 28
    assert [row["step_order"] for row in rows] == list(range(28))
    assert len({row["step_id"] for row in rows}) == 28
    assert all(row["pipeline_contract_hash"] == CONTRACT_HASH for row in rows)
    assert all(row["semantic_rule_version"] == "2" for row in rows)
    assert {row.source_object_kind for row in bindings} == {"PACKAGE", "DOCUMENT"}
    assert {row.binding_purpose for row in bindings} == {"INVENTORY", "HANDOFF"}
    assert {row.contract_rule_id for row in edges} == {
        "E001_SOURCE_INVENTORY_ADMISSION",
        "E002_SOURCE_HANDOFF_ADMISSION",
    }
    assert all(row.materialization_origin == "ADMISSION" for row in edges)
    assert all(row.lease_owner_id is None for row in edges)
    assert all(row.lease_fencing_token == 0 for row in edges)
    assert events == [
        "transaction_begin",
        "bound_sources_locked",
        "proof_locked",
        "run_inserted",
        "steps_inserted",
        "steps_reloaded",
        "proof_consumed",
        "bindings_inserted",
        "bindings_reloaded",
        "admission_edges_inserted",
        "admission_edges_reloaded",
        "transaction_commit",
    ]


def test_reload_mismatch_rolls_back_run_steps_and_proof_consumption() -> None:
    repository, events, _rows, _bindings, _edges = _repository(
        registry=_activated_registry(),
        loaded_rows=(),
    )

    with pytest.raises(StrictPreflightProofError) as exc_info:
        _admit(repository)

    assert exc_info.value.code == "PIPELINE_CONTRACT_HASH_MISMATCH"
    assert "proof_consumed" not in events
    assert events[-1] == "transaction_rollback"


def test_admission_edge_reload_mismatch_rolls_back_entire_atomic_admission() -> None:
    repository, events, _rows, _bindings, _edges = _repository(
        registry=_activated_registry(),
        loaded_edges=(),
    )

    with pytest.raises(StrictPreflightProofError) as exc_info:
        _admit(repository)

    assert exc_info.value.code == "PIPELINE_CONTRACT_HASH_MISMATCH"
    assert {
        "run_inserted",
        "steps_inserted",
        "bindings_inserted",
        "admission_edges_inserted",
    } <= set(events)
    assert events.index("proof_consumed") < events.index("bindings_inserted")
    assert events[-1] == "transaction_rollback"


def test_worker_revalidation_rederives_bound_graph_without_any_write() -> None:
    repository, events, _rows, _bindings, _edges = _repository(registry=_activated_registry())
    _admit(repository)
    events.clear()

    repository.require_task7_admission_graph_for_run(
        deal_id=DEAL_ID,
        run_id=RUN_ID,
    )

    assert events == [
        "run_read",
        "bindings_reloaded",
        "bound_sources_read",
        "admission_edges_reloaded",
    ]
    assert not any("inserted" in event for event in events)


def test_proof_contract_hash_mismatch_fails_before_run_mutation() -> None:
    repository, events, _rows, _bindings, _edges = _repository(registry=_activated_registry())

    with pytest.raises(StrictPreflightProofError) as exc_info:
        repository.consume_and_admit_contract_run(
            proof_id=PROOF_ID,
            expected_binding=_binding(contract_hash="a" * 64),
            run_id=RUN_ID,
            deal_id=DEAL_ID,
            mode="FULL",
            idempotency_key=None,
            source=None,
            created_by_actor_id=None,
            created_by_actor_type=None,
            selected_source_package_ids=(PACKAGE_ID,),
        )

    assert exc_info.value.code == "STRICT_PREFLIGHT_PROOF_MISMATCH"
    assert events == [
        "transaction_begin",
        "bound_sources_locked",
        "proof_locked",
        "transaction_rollback",
    ]


def test_strict_api_composition_uses_atomic_contract_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production route must not retain the run-only proof-consumption seam."""
    connection = object()
    calls: list[dict[str, Any]] = []
    events: list[str] = []

    class RunsRepository:
        def deal_exists(self, _deal_id: str) -> bool:
            return True

        def has_active_run(self, _deal_id: str) -> bool:
            return False

    class ProofRepository:
        def consume_and_admit_contract_run(self, **values: Any) -> dict[str, Any]:
            events.append("atomic_admission")
            calls.append(dict(values))
            return {"run_id": values["run_id"], "status": "QUEUED"}

    class MethodologiesRepository:
        def create_run_selection(self, **_values: Any) -> None:
            return None

    class Request:
        def __init__(self) -> None:
            self.state = SimpleNamespace(db_conn=connection)
            self.app = SimpleNamespace(state=SimpleNamespace(extractor_configured=True))
            self.headers: dict[str, str] = {}

        async def json(self) -> dict[str, Any]:
            return {
                "mode": "FULL",
                "strict_preflight_proof_id": PROOF_ID,
                "methodology_selection": {
                    "commercial_dd_version_record_id": ("55555555-5555-4555-8555-555555555555"),
                    "financial_dd_version_record_id": ("66666666-6666-4666-8666-666666666666"),
                },
            }

    monkeypatch.setattr(
        runs_route,
        "get_runs_repository",
        lambda conn, _tenant_id: (
            RunsRepository() if conn is connection else pytest.fail("wrong connection")
        ),
    )
    monkeypatch.setattr(
        runs_route,
        "_validate_full_methodology_selection",
        lambda **_values: MethodologiesRepository(),
    )
    monkeypatch.setattr(
        runs_route,
        "_gather_preflight_corpus",
        lambda *_args, **_kwargs: [{"document_id": DOCUMENT_ID}],
    )
    monkeypatch.setattr(
        runs_route,
        "_extraction_ready_documents_from_preflight_corpus",
        lambda _corpus: [],
    )
    monkeypatch.setattr(
        runs_route,
        "is_strict_full_live_required",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        runs_route,
        "build_strict_full_live_admission_report",
        lambda **_kwargs: SimpleNamespace(may_proceed=True),
    )
    monkeypatch.setattr(
        runs_route,
        "resolve_strict_preflight_binding",
        lambda **_kwargs: _binding(),
    )
    monkeypatch.setattr(
        runs_route,
        "get_strict_preflight_proofs_repository",
        lambda conn, _tenant_id: (
            ProofRepository() if conn is connection else pytest.fail("wrong connection")
        ),
    )
    monkeypatch.setattr(
        runs_route,
        "_freeze_task7_selected_source_packages",
        lambda **values: (
            (
                events.append("source_freeze"),
                (PACKAGE_ID,),
            )[1]
            if values["selected_document_ids"] == (DOCUMENT_ID,)
            else pytest.fail("wrong selected documents")
        ),
    )

    response = asyncio.run(
        runs_route.start_run(
            DEAL_ID,
            Request(),  # type: ignore[arg-type]
            SimpleNamespace(
                tenant_id=TENANT_ID,
                actor_id="task7-service",
                actor_type="SERVICE",
            ),
        )
    )

    assert response.status == "QUEUED"
    assert len(calls) == 1
    assert calls[0]["expected_binding"].pipeline_contract_version == "full-strict-v21"
    assert calls[0]["selected_source_package_ids"] == (PACKAGE_ID,)
    assert events == ["source_freeze", "atomic_admission"]
