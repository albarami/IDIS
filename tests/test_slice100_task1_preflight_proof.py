"""Slice100 Task 1 RED contracts for durable, bound, one-use preflight proofs."""

from __future__ import annotations

import importlib
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from idis.persistence.repositories.runs import RunAlreadyActiveError

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
PROOF_ID = "33333333-3333-4333-8333-333333333333"
RUN_ID = "44444444-4444-4444-8444-444444444444"


def _proof_module() -> Any:
    return importlib.import_module("idis.persistence.repositories.strict_preflight_proofs")


def _binding(module: Any, **overrides: Any) -> Any:
    values = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "corpus_hash": "1" * 64,
        "strict_profile_generation": "acceptance-v1",
        "strict_profile_hmac": "2" * 64,
        "pipeline_contract_version": "full-strict-v2",
        "pipeline_contract_hash": "3" * 64,
        "expected_consumer_edge_rule_hash": "4" * 64,
        "static_decision_set_hash": "5" * 64,
        "methodology_version_hashes": ("6" * 64, "7" * 64),
        "promoted_pointer_hash": "8" * 64,
        "provider_receipt_set_hash": "9" * 64,
        "dependency_receipt_set_hash": "a" * 64,
    }
    values.update(overrides)
    return module.StrictPreflightBinding(**values)


def _record(module: Any, *, binding: Any, **overrides: Any) -> Any:
    now = datetime.now(UTC)
    values = {
        "proof_id": PROOF_ID,
        "binding": binding,
        "binding_hash": binding.digest(),
        "created_at": now - timedelta(minutes=1),
        "expires_at": now + timedelta(minutes=5),
        "consumed_at": None,
        "consumed_by_run_id": None,
    }
    values.update(overrides)
    return module.StrictPreflightProofRecord(**values)


def test_binding_digest_covers_every_approved_binding_field() -> None:
    module = _proof_module()
    base = _binding(module)

    mutations = {
        "tenant_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "deal_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "corpus_hash": "b" * 64,
        "strict_profile_generation": "acceptance-v2",
        "strict_profile_hmac": "c" * 64,
        "pipeline_contract_version": "full-strict-v3",
        "pipeline_contract_hash": "d" * 64,
        "expected_consumer_edge_rule_hash": "e" * 64,
        "static_decision_set_hash": "f" * 64,
        "methodology_version_hashes": ("0" * 64,),
        "promoted_pointer_hash": "0" * 64,
        "provider_receipt_set_hash": "b" * 64,
        "dependency_receipt_set_hash": "c" * 64,
    }

    for field, value in mutations.items():
        assert _binding(module, **{field: value}).digest() != base.digest(), field


class _Savepoint(AbstractContextManager[None]):
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __enter__(self) -> None:
        self._events.append("savepoint_begin")
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self._events.append("savepoint_rollback" if exc_type is not None else "savepoint_release")
        return False


class _Connection:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self.events)


def _repository(
    module: Any,
    *,
    record: Any,
    insert_error: Exception | None = None,
    mark_consumed: bool = True,
    consumption_failure_code: str = "STRICT_PREFLIGHT_PROOF_EXPIRED",
) -> tuple[Any, list[str]]:
    events: list[str] = []

    class Repository(module.PostgresStrictPreflightProofsRepository):
        def _load_for_update(self, proof_id: str) -> Any:
            assert proof_id == PROOF_ID
            events.append("proof_locked")
            return record

        def _insert_run(self, **_kwargs: Any) -> dict[str, Any]:
            events.append("run_insert")
            if insert_error is not None:
                raise insert_error
            return {"run_id": RUN_ID, "status": "QUEUED"}

        def _mark_consumed(self, *, proof_id: str, run_id: str) -> bool:
            assert proof_id == PROOF_ID
            assert run_id == RUN_ID
            events.append("proof_consumed")
            return mark_consumed

        def _consumption_failure_code(self, proof_id: str) -> str:
            assert proof_id == PROOF_ID
            events.append("consumption_failure_classified")
            return consumption_failure_code

    return Repository(_Connection(events), TENANT_ID), events


def _consume(repo: Any, binding: Any) -> dict[str, Any]:
    return repo.consume_and_create_run(
        proof_id=PROOF_ID,
        expected_binding=binding,
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        idempotency_key="task1-proof",
        source={"type": "deal_documents", "document_ids": ["doc-1"]},
        created_by_actor_id="actor-1",
        created_by_actor_type="SERVICE",
    )


def test_valid_proof_inserts_run_before_marking_proof_consumed() -> None:
    module = _proof_module()
    binding = _binding(module)
    repo, events = _repository(module, record=_record(module, binding=binding))

    result = _consume(repo, binding)

    assert result == {"run_id": RUN_ID, "status": "QUEUED"}
    assert events == [
        "savepoint_begin",
        "proof_locked",
        "run_insert",
        "proof_consumed",
        "savepoint_release",
    ]


def test_active_run_conflict_rolls_back_without_consuming_proof() -> None:
    module = _proof_module()
    binding = _binding(module)
    repo, events = _repository(
        module,
        record=_record(module, binding=binding),
        insert_error=RunAlreadyActiveError(DEAL_ID),
    )

    with pytest.raises(RunAlreadyActiveError):
        _consume(repo, binding)

    assert events == [
        "savepoint_begin",
        "proof_locked",
        "run_insert",
        "savepoint_rollback",
    ]


def test_expiry_at_final_consume_rolls_back_inserted_run() -> None:
    module = _proof_module()
    binding = _binding(module)
    repo, events = _repository(
        module,
        record=_record(module, binding=binding),
        mark_consumed=False,
        consumption_failure_code="STRICT_PREFLIGHT_PROOF_EXPIRED",
    )

    with pytest.raises(module.StrictPreflightProofError) as exc_info:
        _consume(repo, binding)

    assert exc_info.value.code == "STRICT_PREFLIGHT_PROOF_EXPIRED"
    assert events == [
        "savepoint_begin",
        "proof_locked",
        "run_insert",
        "proof_consumed",
        "consumption_failure_classified",
        "savepoint_rollback",
    ]


@pytest.mark.parametrize(
    ("record_overrides", "binding_overrides", "expected_code"),
    [
        (
            {"expired_at_lock": True},
            {},
            "STRICT_PREFLIGHT_PROOF_EXPIRED",
        ),
        (
            {"consumed_at": datetime.now(UTC), "consumed_by_run_id": "old-run"},
            {},
            "STRICT_PREFLIGHT_PROOF_ALREADY_CONSUMED",
        ),
        ({}, {"corpus_hash": "f" * 64}, "STRICT_PREFLIGHT_PROOF_MISMATCH"),
    ],
)
def test_invalid_proof_never_reaches_run_insert_or_consumption(
    record_overrides: dict[str, Any],
    binding_overrides: dict[str, Any],
    expected_code: str,
) -> None:
    module = _proof_module()
    stored_binding = _binding(module)
    repo, events = _repository(
        module,
        record=_record(module, binding=stored_binding, **record_overrides),
    )

    with pytest.raises(module.StrictPreflightProofError) as exc_info:
        _consume(repo, _binding(module, **binding_overrides))

    assert exc_info.value.code == expected_code
    assert events == ["savepoint_begin", "proof_locked", "savepoint_rollback"]


def test_missing_or_cross_tenant_proof_uses_leakage_safe_invalid_code() -> None:
    module = _proof_module()
    binding = _binding(module)
    repo, events = _repository(module, record=None)

    with pytest.raises(module.StrictPreflightProofError) as exc_info:
        _consume(repo, binding)

    assert exc_info.value.code == "STRICT_PREFLIGHT_PROOF_INVALID"
    assert events == ["savepoint_begin", "proof_locked", "savepoint_rollback"]


class _SqlResult:
    def fetchone(self) -> None:
        return None


class _SqlCaptureConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: Any, _params: dict[str, Any]) -> _SqlResult:
        self.statements.append(" ".join(str(statement).split()))
        return _SqlResult()


def test_proof_expiry_is_adjudicated_in_postgres_at_lock_and_consume() -> None:
    module = _proof_module()
    conn = _SqlCaptureConnection()
    repo = object.__new__(module.PostgresStrictPreflightProofsRepository)
    repo._conn = conn
    repo._tenant_id = TENANT_ID

    assert repo._load_for_update(PROOF_ID) is None
    assert repo._mark_consumed(proof_id=PROOF_ID, run_id=RUN_ID) is False

    lock_sql, consume_sql = conn.statements
    assert "expires_at <= statement_timestamp() AS expired_at_lock" in lock_sql
    assert "FOR UPDATE" in lock_sql
    assert "SET consumed_at = statement_timestamp()" in consume_sql
    assert "expires_at > CURRENT_TIMESTAMP" in consume_sql
    assert "expires_at > statement_timestamp()" in consume_sql


def test_migration_0031_has_rls_immutable_bindings_and_run_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = importlib.import_module(
        "idis.persistence.migrations.versions.0031_strict_preflight_proofs"
    )
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda sql: statements.append(str(sql)))

    migration.upgrade()

    ddl = "\n".join(statements).lower()
    assert "create table" in ddl and "strict_preflight_proofs" in ddl
    for field in (
        "corpus_hash",
        "strict_profile_generation",
        "strict_profile_hmac",
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "expected_consumer_edge_rule_hash",
        "static_decision_set_hash",
        "methodology_version_hashes",
        "promoted_pointer_hash",
        "provider_receipt_set_hash",
        "dependency_receipt_set_hash",
        "binding_hash",
        "expires_at",
        "consumed_at",
        "consumed_by_run_id",
    ):
        assert field in ddl
    assert "enable row level security" in ddl
    assert "force row level security" in ddl
    assert "strict_preflight_proof_id" in ddl and "alter table runs" in ddl
    assert "conrelid = 'runs'::regclass" in ddl
    assert "prevent" in ddl or "immutable" in ddl
    assert "before insert or update or delete" in ddl
    assert "from deals" in ddl
    assert "for key share" in ddl
    assert "deals.tenant_id = new.tenant_id" in ddl
    assert "deals.deal_id = new.deal_id" in ddl
    assert "runs.deal_id = new.deal_id" in ddl
    assert "old.expires_at > statement_timestamp()" in ddl
    assert "new.consumed_at <= old.expires_at" in ddl
    assert "deals.tenant_id = old.tenant_id" in ddl
    assert "deals.deal_id = old.deal_id" in ddl
    assert "consumed_by_run_id uuid references runs" not in ddl
    assert "references deals" not in ddl
    assert "idis_is_nonempty_sha256_jsonb_array" in ddl
    assert "jsonb_array_elements(value)" in ddl
    assert "jsonb_typeof(element.item) is distinct from 'string'" in ddl
    assert "^[0-9a-f]{64}$" in ddl
    for task7_table in (
        "run_output_objects",
        "run_consumer_receipts",
        "run_expected_consumer_edges",
    ):
        assert task7_table not in ddl
