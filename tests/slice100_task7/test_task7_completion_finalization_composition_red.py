"""RED-first composition contracts for Task 7 completion, resume, and finalization.

These tests deliberately wish one small production API at
``idis.pipeline.task7_completion``.  The existing Task 7 modules prove the
individual semantic, graph-row, edge, usage, and prefix invariants; this suite
proves that the canonical worker composes them at the reviewed transaction
boundaries in design sections 14.2-14.4.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from datetime import UTC, datetime
from types import MappingProxyType, ModuleType
from typing import Any
from uuid import UUID

import pytest

from idis.models.run_step import RunStep, StepName, StepStatus
from idis.pipeline import contract_registry
from idis.pipeline.task7_census import build_census_envelope
from idis.pipeline.task7_semantics import (
    STEP_SEMANTIC_REGISTRY,
    NativeOutputEvidence,
    SemanticValidationInput,
    SemanticValidationResult,
    SubroleReceiptEvidence,
)
from idis.services.runs.steps import (
    NativeOutputContract,
    NativeOutputWriteBinding,
    StagedNativeOutputWrite,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
STEP_ID = "44444444-4444-4444-8444-444444444444"
LEASE_OWNER_ID = "55555555-5555-4555-8555-555555555555"
LEASE_FENCING_TOKEN = 19

_STABLE_CODES = {
    "WORKER_LEASE_FENCED",
    "WORKER_LEASE_BOUNDARY_COMMIT_FAILED",
    "STEP_REHYDRATION_FAILED",
    "STEP_OUTPUT_MISSING",
    "CONSUMER_RECEIPT_INCOMPLETE",
}
_SUSPICIOUS_EXTERNAL_NAMES = {
    "http_client",
    "llm_client",
    "object_store",
    "provider_client",
    "filesystem",
    "url",
    "path",
}


def _module() -> ModuleType:
    try:
        return importlib.import_module("idis.pipeline.task7_completion")
    except ModuleNotFoundError as exc:
        if exc.name != "idis.pipeline.task7_completion":
            raise
        pytest.fail(
            "Task 7 completion/finalization composition module is missing",
            pytrace=False,
        )


def _v7_contract() -> Any:
    try:
        return contract_registry.PIPELINE_CONTRACTS["full-strict-v21"]
    except KeyError:
        pytest.fail(
            "full-strict-v21 is absent from the code-owned pipeline registry",
            pytrace=False,
        )


def _scope(module: ModuleType, **changes: object) -> Any:
    contract = _v7_contract()
    # PipelineContractSpec's canonical typed field is contract_hash.
    values: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "pipeline_contract_version": "full-strict-v21",
        "pipeline_contract_hash": contract.contract_hash,
        "pipeline_resume_state": "RESUMABLE",
        "lease_owner_id": LEASE_OWNER_ID,
        "lease_fencing_token": LEASE_FENCING_TOKEN,
    }
    values.update(changes)
    return module.Task7LeaseScope(**values)


def _semantic_input() -> SemanticValidationInput:
    semantic = STEP_SEMANTIC_REGISTRY["EXTRACT"]
    census = build_census_envelope(
        schema_id=semantic.census_schema_id,
        payload={
            "schema_version": "1",
            "input_refs": [],
            "generic_output_roots": [],
            "methodology_claim_ids": [],
            "generic_new_claim_ids": [],
            "reused_claim_ids": [],
            "provider_hash": "1" * 64,
            "prompt_hash": "2" * 64,
            "request_hash": "3" * 64,
            "scope_hash": "4" * 64,
            "governed_result_count": 3,
            "failed_count": 0,
            "partial_count": 0,
        },
    )
    return SemanticValidationInput(
        step_name="EXTRACT",
        semantic_rule_version=semantic.semantic_rule_version,
        semantic_rule_hash=semantic.semantic_rule_hash,
        terminal_status="COMPLETED",
        result_payload=MappingProxyType(
            {"status": "COMPLETED", "persistence": {"status": "persisted"}}
        ),
        census=census,
        native_outputs=tuple(
            NativeOutputEvidence(
                output_kind=output_kind,
                output_object_id=UUID(int=index + 1),
                native_identity_sha256=f"{index + 1:064x}",
                native_output_sha256=f"{index + 101:064x}",
                persisted=True,
            )
            for index, output_kind in enumerate(semantic.native_output_kinds)
        ),
        subrole_receipts=tuple(
            SubroleReceiptEvidence(
                subrole=subrole,
                native_identity_sha256=f"{index + 201:064x}",
                native_output_sha256=f"{index + 301:064x}",
                persisted=True,
            )
            for index, subrole in enumerate(semantic.mandatory_subroles)
        ),
        producer_persistence_confirmed=True,
    )


def _staged_native_write(
    events: list[str],
    *,
    fail: bool = False,
) -> StagedNativeOutputWrite:
    binding = NativeOutputWriteBinding(
        row_model_type=dict,
        repository_type=_Port,
        persist_method="insert_native_row",
        load_methods=("load_native_row",),
        sql_targets=frozenset({"native_rows"}),
    )
    contract = NativeOutputContract(
        step_name=StepName.EXTRACT,
        output_type_id="task7-test-native-v1",
        persistence_target="postgres:native_rows",
        output_model_type=dict,
        write_bindings=(binding,),
        stage=lambda **_kwargs: (),
    )

    def persist(
        *,
        db_conn: object,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        assert db_conn is not None
        assert lease_owner_id == LEASE_OWNER_ID
        assert lease_fencing_token == LEASE_FENCING_TOKEN
        events.append("write:native")
        if fail:
            raise RuntimeError("injected native persistence failure")

    return StagedNativeOutputWrite(
        contract=contract,
        binding=binding,
        output={"native_id": "native-1"},
        persist=persist,
    )


def _completion_command(
    module: ModuleType,
    events: list[str],
    *,
    fail_native: bool = False,
    semantic_input: SemanticValidationInput | None = None,
) -> Any:
    return module.Task7StepCompletionCommand(
        scope=_scope(module),
        step_id=STEP_ID,
        step_name="EXTRACT",
        semantic_input=semantic_input or _semantic_input(),
        staged_native_writes=(_staged_native_write(events, fail=fail_native),),
    )


def _resume_command(module: ModuleType) -> Any:
    return module.Task7RunResumeCommand(scope=_scope(module))


def _finalization_command(module: ModuleType) -> Any:
    return module.Task7RunFinalizationCommand(scope=_scope(module))


def _steps(
    *,
    completed_count: int = 28,
    include_lifecycle: bool = False,
    status_override: tuple[int, StepStatus] | None = None,
) -> tuple[RunStep, ...]:
    contract = _v7_contract()
    rows: list[RunStep] = []
    for spec in contract.steps:
        status = StepStatus.COMPLETED if spec.step_order < completed_count else StepStatus.PENDING
        if status_override is not None and spec.step_order == status_override[0]:
            status = status_override[1]
        rows.append(
            RunStep(
                step_id=f"00000000-0000-4000-8000-{spec.step_order:012d}",
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                step_name=StepName(spec.step_name),
                step_order=spec.step_order,
                status=status,
                pipeline_contract_version="full-strict-v21",
                pipeline_contract_hash=contract.contract_hash,
                semantic_rule_version=spec.semantic_rule_version,
                semantic_rule_hash=spec.semantic_rule_hash,
            )
        )
    if include_lifecycle:
        rows.append(
            RunStep(
                step_id="00000000-0000-4000-8000-000000000028",
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                step_name=StepName.RUN_LIFECYCLE,
                step_order=28,
                status=StepStatus.COMPLETED,
                pipeline_contract_version="full-strict-v21",
                pipeline_contract_hash=contract.contract_hash,
                semantic_rule_version="run-lifecycle-v1",
                semantic_rule_hash="c" * 64,
            )
        )
    return tuple(rows)


class _InjectedFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Connection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.driver_statements: list[str] = []

    def exec_driver_sql(self, statement: str) -> None:
        normalized = " ".join(statement.split())
        self.driver_statements.append(normalized)
        self.events.append(f"driver:{normalized}")


class _ConnectionContext:
    def __init__(
        self,
        *,
        events: list[str],
        port: _Port,
    ) -> None:
        self.events = events
        self.port = port
        self.connection = _Connection(events)

    def __enter__(self) -> _Connection:
        self.events.append("transaction:begin")
        return self.connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        _ = (exc, traceback)
        if exc_type is None:
            self.port.commit_staged()
            self.events.append("transaction:commit")
        else:
            self.port.rollback_staged()
            self.events.append("transaction:rollback")
        return False


class _ConnectionFactory:
    def __init__(self, events: list[str], port: _Port) -> None:
        self.events = events
        self.port = port
        self.contexts: list[_ConnectionContext] = []

    def __call__(self) -> _ConnectionContext:
        context = _ConnectionContext(events=self.events, port=self.port)
        self.contexts.append(context)
        return context


class _Port:
    """Stateful durable-port double; it never substitutes for a production proof."""

    def __init__(
        self,
        events: list[str],
        *,
        fail_at: str | None = None,
        fail_code: str = "WORKER_LEASE_BOUNDARY_COMMIT_FAILED",
        run_row: Mapping[str, object] | None = None,
        steps: Sequence[RunStep] | None = None,
        contract_map: Mapping[str, object] | None = None,
        persisted_map: Mapping[str, object] | None = None,
        receipt_map: Mapping[str, object] | None = None,
        usage_map: Mapping[str, str] | None = None,
        cardinalities: Mapping[str, int] | None = None,
    ) -> None:
        contract = _v7_contract()
        self.events = events
        self.fail_at = fail_at
        self.fail_code = fail_code
        self.run_row = dict(
            run_row
            or {
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "status": "RUNNING",
                "pipeline_contract_version": "full-strict-v21",
                "pipeline_contract_hash": contract.contract_hash,
                "pipeline_resume_state": "RESUMABLE",
                "lease_owner_id": LEASE_OWNER_ID,
                "lease_fencing_token": LEASE_FENCING_TOKEN,
                "lease_expires_at": datetime(2099, 1, 1, tzinfo=UTC),
                "lease_is_live": True,
            }
        )
        self.steps = tuple(steps if steps is not None else _steps())
        self.locked_step: RunStep | None = None
        default_contract_map = {
            "edge-a": {
                "contract_rule_id": "E001_SOURCE_TO_HANDOFF",
                "value_sha256": "a" * 64,
            },
            "edge-b": {
                "contract_rule_id": "E001_SOURCE_TO_HANDOFF",
                "value_sha256": "b" * 64,
            },
        }
        self.contract_map = dict(default_contract_map if contract_map is None else contract_map)
        self.persisted_map = dict(self.contract_map if persisted_map is None else persisted_map)
        self.usage_map = dict(
            {key: f"{index + 1:064x}" for index, key in enumerate(self.contract_map)}
            if usage_map is None
            else usage_map
        )
        self.receipt_map = dict(
            {
                key: {
                    **dict(value),
                    "usage_evidence_sha256": self.usage_map.get(key, "0" * 64),
                }
                for key, value in self.contract_map.items()
            }
            if receipt_map is None
            else receipt_map
        )
        self.cardinalities = dict(
            {"E001_SOURCE_TO_HANDOFF": len(self.contract_map)}
            if cardinalities is None
            else cardinalities
        )
        self.pending_writes: list[str] = []
        self.committed_writes: list[str] = []
        self.completed_step_orders: list[int] = []
        self.mark_completed_kwargs: dict[str, object] | None = None
        self.mark_succeeded_kwargs: dict[str, object] | None = None

    def _record(self, event: str, *, write: bool = False) -> None:
        self.events.append(event)
        if write:
            self.pending_writes.append(event)
        if event == self.fail_at:
            raise _InjectedFailure(self.fail_code)

    def commit_staged(self) -> None:
        self.committed_writes.extend(self.pending_writes)
        self.pending_writes.clear()

    def rollback_staged(self) -> None:
        self.pending_writes.clear()

    # Completion port
    def establish_tenant_rls(self, **_kwargs: object) -> None:
        self._record("rls")

    def lock_run_for_step_completion(self, **_kwargs: object) -> Mapping[str, object]:
        self._record("lock:run")
        return dict(self.run_row)

    def renew_live_lease(self, **_kwargs: object) -> Mapping[str, object]:
        self._record("lease:renew", write=True)
        return dict(self.run_row)

    def lock_run_step(self, **kwargs: object) -> RunStep:
        self._record("lock:step")
        if self.locked_step is not None:
            return self.locked_step
        step = next(step for step in self.steps if step.step_name is StepName.EXTRACT)
        return step.model_copy(
            update={
                "step_id": kwargs["step_id"],
                "status": StepStatus.RUNNING,
            }
        )

    def lock_existing_native_rows(
        self,
        *,
        lock_keys: tuple[str, ...],
        **_kwargs: object,
    ) -> None:
        assert lock_keys == ("native_rows",)
        self._record("lock:native")

    def validate_staged_semantics(
        self,
        *,
        semantic_result: SemanticValidationResult,
        **_kwargs: object,
    ) -> SemanticValidationResult:
        self._record("validate:semantic")
        return semantic_result

    def validate_step_predecessors(self, **_kwargs: object) -> bool:
        self._record("validate:predecessors")
        return True

    def persist_output_roots(self, **_kwargs: object) -> None:
        self._record("write:output_roots", write=True)

    def corroborate_persisted_native_outputs(self, **_kwargs: object) -> None:
        self._record("prove:native_authority")

    def persist_step_outputs(self, **_kwargs: object) -> None:
        self._record("write:step_outputs", write=True)

    def persist_applicability_decisions(self, **_kwargs: object) -> None:
        self._record("write:decisions", write=True)

    def persist_new_outgoing_edges(self, **_kwargs: object) -> None:
        self._record("write:outgoing_edges", write=True)

    def persist_step_receipt_input_references(self, **_kwargs: object) -> None:
        self._record("write:receipt_inputs", write=True)

    def derive_and_persist_usage_receipts(self, **_kwargs: object) -> None:
        self._record("write:usage_receipts", write=True)

    def prove_incoming_receipt_equality(self, **_kwargs: object) -> bool:
        self._record("prove:incoming_equality")
        return True

    def mark_step_completed(self, **kwargs: object) -> bool:
        self.mark_completed_kwargs = dict(kwargs)
        self._record("write:step_completed", write=True)
        return True

    def append_step_completion_audit(self, **_kwargs: object) -> None:
        self._record("write:completion_audit", write=True)

    # Resume/read-only port
    def load_resumable_run(self, **_kwargs: object) -> Mapping[str, object]:
        self._record("read:run")
        return dict(self.run_row)

    def list_canonical_steps(self, **_kwargs: object) -> tuple[RunStep, ...]:
        self._record("read:steps")
        return tuple(self.steps)

    def validate_completed_step_graph(
        self,
        *,
        step: RunStep,
        **_kwargs: object,
    ) -> bool:
        self._record(f"read:completed:{step.step_order}")
        self.completed_step_orders.append(step.step_order)
        return True

    # Finalization port
    def lock_run_for_finalization(self, **_kwargs: object) -> Mapping[str, object]:
        sql = (
            "SELECT run_id, status, pipeline_contract_version, "
            "pipeline_contract_hash, pipeline_resume_state, lease_owner_id, "
            "lease_fencing_token, lease_expires_at FROM runs "
            "WHERE tenant_id=:tenant_id AND deal_id=:deal_id AND run_id=:run_id "
            "FOR UPDATE"
        )
        self._record(f"data:{sql}")
        return dict(self.run_row)

    def derive_contract_edge_map(self, **_kwargs: object) -> dict[str, object]:
        self._record("derive:contract_map")
        return dict(self.contract_map)

    def load_persisted_edge_map(self, **_kwargs: object) -> dict[str, object]:
        self._record("derive:persisted_map")
        return dict(self.persisted_map)

    def derive_receipt_edge_map(self, **_kwargs: object) -> dict[str, object]:
        self._record("derive:receipt_map")
        return dict(self.receipt_map)

    def derive_usage_evidence_map(self, **_kwargs: object) -> dict[str, str]:
        self._record("derive:usage_map")
        return dict(self.usage_map)

    def load_contract_cardinality_equations(self, **_kwargs: object) -> dict[str, int]:
        self._record("derive:cardinality")
        return dict(self.cardinalities)

    def validate_native_and_subrole_roots(self, **_kwargs: object) -> None:
        self._record("prove:native_subroles")

    def mark_run_succeeded(self, **kwargs: object) -> bool:
        self.mark_succeeded_kwargs = dict(kwargs)
        self._record("write:run_succeeded", write=True)
        return True

    def append_run_finalization_audit(self, **_kwargs: object) -> None:
        self._record("write:finalization_audit", write=True)


def _service(
    module: ModuleType,
    events: list[str],
    port: _Port,
) -> tuple[Any, _ConnectionFactory]:
    factory = _ConnectionFactory(events, port)
    service = module.Task7CompletionFinalizationService(
        connection_factory=factory,
        port_factory=lambda _connection, _tenant_id: port,
    )
    return service, factory


def _error_code(error: BaseException) -> str | None:
    value = getattr(error, "code", None)
    return value if isinstance(value, str) else None


def test_public_composition_api_is_small_typed_and_uses_stable_codes() -> None:
    module = _module()

    assert tuple(field.name for field in fields(module.Task7LeaseScope)) == (
        "tenant_id",
        "deal_id",
        "run_id",
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "pipeline_resume_state",
        "lease_owner_id",
        "lease_fencing_token",
    )
    assert tuple(field.name for field in fields(module.Task7StepCompletionCommand)) == (
        "scope",
        "step_id",
        "step_name",
        "semantic_input",
        "staged_native_writes",
    )
    assert tuple(field.name for field in fields(module.Task7RunResumeCommand)) == ("scope",)
    assert tuple(field.name for field in fields(module.Task7RunFinalizationCommand)) == ("scope",)
    assert tuple(field.name for field in fields(module.Task7StepCompletionResult)) == (
        "step_id",
        "step_name",
        "completed",
    )
    assert tuple(field.name for field in fields(module.Task7RunFinalizationResult)) == (
        "run_id",
        "status",
        "edge_count",
        "receipt_count",
    )
    assert {
        module.WORKER_LEASE_FENCED,
        module.WORKER_LEASE_BOUNDARY_COMMIT_FAILED,
        module.STEP_REHYDRATION_FAILED,
        module.STEP_OUTPUT_MISSING,
        module.CONSUMER_RECEIPT_INCOMPLETE,
    } == _STABLE_CODES
    assert {
        "complete_step",
        "rehydrate_run",
        "validate_completed_step",
        "finalize_run",
    } <= {
        name
        for name, value in inspect.getmembers(
            module.Task7CompletionFinalizationService,
            inspect.isfunction,
        )
        if not name.startswith("_") and callable(value)
    }


def test_completion_is_one_fenced_atomic_uow_in_fixed_lock_and_write_order() -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events)
    service, factory = _service(module, events, port)

    result = service.complete_step(_completion_command(module, events))

    assert result == module.Task7StepCompletionResult(
        step_id=STEP_ID,
        step_name="EXTRACT",
        completed=True,
    )
    assert len(factory.contexts) == 1
    assert events == [
        "transaction:begin",
        "rls",
        "lock:run",
        "lease:renew",
        "lock:step",
        "lock:native",
        "validate:predecessors",
        "write:output_roots",
        "write:native",
        "prove:native_authority",
        "write:step_outputs",
        "write:decisions",
        "write:outgoing_edges",
        "write:receipt_inputs",
        "write:usage_receipts",
        "prove:incoming_equality",
        "write:step_completed",
        "write:completion_audit",
        "transaction:commit",
    ]
    assert port.committed_writes == [
        "lease:renew",
        "write:output_roots",
        "write:step_outputs",
        "write:decisions",
        "write:outgoing_edges",
        "write:receipt_inputs",
        "write:usage_receipts",
        "write:step_completed",
        "write:completion_audit",
    ]
    assert port.mark_completed_kwargs is not None
    assert port.mark_completed_kwargs["result_summary"] == _semantic_input().result_payload


@pytest.mark.parametrize(
    "failure_stage",
    (
        "lock:run",
        "lease:renew",
        "lock:step",
        "lock:native",
        "validate:predecessors",
        "write:output_roots",
        "prove:native_authority",
        "write:step_outputs",
        "write:decisions",
        "write:outgoing_edges",
        "write:receipt_inputs",
        "write:usage_receipts",
        "prove:incoming_equality",
        "write:step_completed",
        "write:completion_audit",
    ),
)
def test_any_completion_failure_rolls_back_every_staged_fragment(
    failure_stage: str,
) -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events, fail_at=failure_stage)
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.complete_step(_completion_command(module, events))

    assert _error_code(caught.value) == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"
    assert events[-1] == "transaction:rollback"
    assert "transaction:commit" not in events
    assert port.pending_writes == []
    assert port.committed_writes == []


def test_completion_preserves_fencing_and_receipt_failure_codes() -> None:
    module = _module()
    cases = (
        ("lease:renew", "WORKER_LEASE_FENCED"),
        ("write:output_roots", "STEP_OUTPUT_MISSING"),
        ("prove:native_authority", "STEP_OUTPUT_MISSING"),
        ("prove:incoming_equality", "CONSUMER_RECEIPT_INCOMPLETE"),
    )
    for failure_stage, expected_code in cases:
        events: list[str] = []
        port = _Port(
            events,
            fail_at=failure_stage,
            fail_code=expected_code,
        )
        service, _factory = _service(module, events, port)
        with pytest.raises(module.Task7CompletionError) as caught:
            service.complete_step(_completion_command(module, events))
        assert _error_code(caught.value) == expected_code
        assert events[-1] == "transaction:rollback"


def test_completion_validates_raw_semantics_itself_and_rejects_forged_drift() -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events)
    service, _factory = _service(module, events, port)
    forged = replace(_semantic_input(), semantic_rule_hash="f" * 64)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.complete_step(
            _completion_command(module, events, semantic_input=forged),
        )

    assert _error_code(caught.value) == "PIPELINE_CONTRACT_HASH_MISMATCH"
    assert "write:native" not in events
    assert events[-1] == "transaction:rollback"


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("status", StepStatus.COMPLETED),
        ("tenant_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("run_id", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("pipeline_contract_version", "full-strict-v5"),
        ("pipeline_contract_hash", "f" * 64),
        ("semantic_rule_version", "wrong"),
        ("semantic_rule_hash", "f" * 64),
    ),
)
def test_completion_rejects_a_drifted_or_already_terminal_locked_step(
    field_name: str,
    value: object,
) -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events)
    extract_index = next(
        index for index, step in enumerate(port.steps) if step.step_name is StepName.EXTRACT
    )
    changed = list(port.steps)
    changed[extract_index] = changed[extract_index].model_copy(update={field_name: value})
    port.steps = tuple(changed)
    port.locked_step = changed[extract_index].model_copy(
        update={
            "step_id": STEP_ID,
            "status": (value if field_name == "status" else StepStatus.RUNNING),
        }
    )
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.complete_step(_completion_command(module, events))

    assert _error_code(caught.value) in {
        "STEP_OUTPUT_MISSING",
        "PIPELINE_CONTRACT_HASH_MISMATCH",
        "STEP_REHYDRATION_FAILED",
    }
    assert "write:native" not in events
    assert events[-1] == "transaction:rollback"


def test_native_persistence_failure_rolls_back_roots_step_and_audit() -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events)
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.complete_step(
            _completion_command(module, events, fail_native=True),
        )

    assert _error_code(caught.value) == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"
    assert events[-3:] == [
        "write:output_roots",
        "write:native",
        "transaction:rollback",
    ]
    assert "write:step_completed" not in events
    assert "write:completion_audit" not in events
    assert port.committed_writes == []


def test_composition_surface_has_no_external_io_dependency_or_ambient_lookup() -> None:
    module = _module()
    constructor_names = set(inspect.signature(module.Task7CompletionFinalizationService).parameters)
    method_names = set()
    for name in (
        "complete_step",
        "rehydrate_run",
        "validate_completed_step",
        "finalize_run",
    ):
        method_names.update(
            inspect.signature(getattr(module.Task7CompletionFinalizationService, name)).parameters
        )

    assert constructor_names == {"connection_factory", "port_factory"}
    assert not (_SUSPICIOUS_EXTERNAL_NAMES & (constructor_names | method_names))
    source = inspect.getsource(module)
    assert all(
        forbidden not in source
        for forbidden in (
            "import httpx",
            "import requests",
            "import boto",
            "subprocess.",
            "Path(",
            "os.environ",
        )
    )


def test_completed_step_validation_is_strictly_read_only() -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events, steps=_steps(completed_count=1))
    service, _factory = _service(module, events, port)
    step = port.steps[0]

    accepted = service.validate_completed_step(
        _resume_command(module),
        step=step,
    )

    assert accepted is True
    assert events == [
        "transaction:begin",
        "driver:SET TRANSACTION READ ONLY",
        "rls",
        "read:run",
        "read:completed:0",
        "transaction:commit",
    ]
    assert not port.committed_writes
    assert not any(event.startswith(("write:", "lease:", "lock:")) for event in events)


@pytest.mark.parametrize("cut", range(1, 28))
def test_all_27_restart_cuts_use_the_read_only_graph_composition(cut: int) -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events, steps=_steps(completed_count=cut))
    service, factory = _service(module, events, port)

    plan = service.rehydrate_run(_resume_command(module))

    assert plan.completed_count == cut
    assert plan.next_step_order == cut
    assert plan.finalization_required is False
    assert port.completed_step_orders == list(range(cut))
    assert len(factory.contexts) == 1
    assert factory.contexts[0].connection.driver_statements == ["SET TRANSACTION READ ONLY"]
    assert not port.committed_writes
    assert not any(event.startswith(("write:", "lease:renew", "lock:native")) for event in events)


def test_resume_never_repairs_a_missing_completed_step_graph() -> None:
    module = _module()
    events: list[str] = []
    port = _Port(
        events,
        steps=_steps(completed_count=3),
        fail_at="read:completed:1",
        fail_code="STEP_OUTPUT_MISSING",
    )
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.rehydrate_run(_resume_command(module))

    assert _error_code(caught.value) == "STEP_OUTPUT_MISSING"
    assert events[-1] == "transaction:rollback"
    assert not port.committed_writes
    assert not any(event.startswith("write:") for event in events)


def test_finalization_begins_repeatable_read_before_the_scoped_run_lock() -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events)
    service, factory = _service(module, events, port)

    service.finalize_run(_finalization_command(module))

    assert len(factory.contexts) == 1
    statements = factory.contexts[0].connection.driver_statements
    # SQLAlchemy's factory has already begun the UoW, so SET TRANSACTION is the
    # executable way to establish the reviewed snapshot before its first query.
    assert statements[0] == ("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ WRITE")
    first_data_read = next(event for event in events if event.startswith("data:"))
    assert first_data_read == (
        "data:SELECT run_id, status, pipeline_contract_version, "
        "pipeline_contract_hash, pipeline_resume_state, lease_owner_id, "
        "lease_fencing_token, lease_expires_at FROM runs "
        "WHERE tenant_id=:tenant_id AND deal_id=:deal_id AND run_id=:run_id "
        "FOR UPDATE"
    )
    assert events.index("rls") < events.index(first_data_read)
    assert events.index("driver:SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ WRITE") < (
        events.index("rls")
    )


@pytest.mark.parametrize(
    ("run_change", "scope_change", "expected_code"),
    (
        ({"status": "QUEUED"}, {}, "WORKER_LEASE_FENCED"),
        (
            {"pipeline_contract_version": "full-strict-v5"},
            {},
            "STEP_REHYDRATION_FAILED",
        ),
        (
            {"pipeline_contract_hash": "f" * 64},
            {},
            "STEP_REHYDRATION_FAILED",
        ),
        (
            {"pipeline_resume_state": "NON_RESUMABLE"},
            {},
            "STEP_REHYDRATION_FAILED",
        ),
        ({"lease_owner_id": "different-owner"}, {}, "WORKER_LEASE_FENCED"),
        ({"lease_fencing_token": 20}, {}, "WORKER_LEASE_FENCED"),
        (
            {"lease_is_live": False},
            {},
            "WORKER_LEASE_FENCED",
        ),
        ({"tenant_id": None}, {}, "STEP_REHYDRATION_FAILED"),
        ({"deal_id": None}, {}, "STEP_REHYDRATION_FAILED"),
        ({}, {"pipeline_contract_version": "full-strict-v5"}, "STEP_REHYDRATION_FAILED"),
    ),
)
def test_finalization_requires_exact_live_v7_resumable_owner_and_token(
    run_change: Mapping[str, object],
    scope_change: Mapping[str, object],
    expected_code: str,
) -> None:
    module = _module()
    events: list[str] = []
    baseline = _Port(events).run_row
    port = _Port(events, run_row={**baseline, **run_change})
    service, _factory = _service(module, events, port)
    command = module.Task7RunFinalizationCommand(scope=_scope(module, **dict(scope_change)))

    with pytest.raises(module.Task7CompletionError) as caught:
        service.finalize_run(command)

    assert _error_code(caught.value) == expected_code
    assert events[-1] == "transaction:rollback"
    assert "derive:contract_map" not in events
    assert "write:run_succeeded" not in events


@pytest.mark.parametrize(
    "mismatch",
    ("persisted_missing", "persisted_changed", "receipt_missing", "receipt_changed"),
)
def test_finalization_independently_compares_contract_persisted_and_receipt_maps(
    mismatch: str,
) -> None:
    module = _module()
    events: list[str] = []
    reference = _Port(events).contract_map
    persisted = dict(reference)
    receipts = dict(reference)
    if mismatch == "persisted_missing":
        persisted.pop("edge-b")
    elif mismatch == "persisted_changed":
        persisted["edge-b"] = {
            "contract_rule_id": "E001_SOURCE_TO_HANDOFF",
            "value_sha256": "f" * 64,
        }
    elif mismatch == "receipt_missing":
        receipts.pop("edge-b")
    else:
        receipts["edge-b"] = {
            "contract_rule_id": "E001_SOURCE_TO_HANDOFF",
            "value_sha256": "f" * 64,
        }
    port = _Port(
        events,
        contract_map=reference,
        persisted_map=persisted,
        receipt_map=receipts,
    )
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.finalize_run(_finalization_command(module))

    assert _error_code(caught.value) == "CONSUMER_RECEIPT_INCOMPLETE"
    assert {
        "derive:contract_map",
        "derive:persisted_map",
        "derive:receipt_map",
        "derive:usage_map",
    } <= set(events)
    assert events[-1] == "transaction:rollback"
    assert "write:run_succeeded" not in events


def test_finalization_checks_hash_bound_cardinality_independently_of_map_equality() -> None:
    module = _module()
    events: list[str] = []
    port = _Port(
        events,
        cardinalities={"E001_SOURCE_TO_HANDOFF": 3},
    )
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.finalize_run(_finalization_command(module))

    assert _error_code(caught.value) == "CONSUMER_RECEIPT_INCOMPLETE"
    assert "derive:cardinality" in events
    assert events[-1] == "transaction:rollback"
    assert "write:run_succeeded" not in events


@pytest.mark.parametrize(
    ("empty_name", "port_changes"),
    (
        (
            "contract",
            {
                "contract_map": {},
                "persisted_map": {},
                "receipt_map": {},
                "usage_map": {},
                "cardinalities": {},
            },
        ),
        ("persisted", {"persisted_map": {}}),
        ("receipt", {"receipt_map": {}}),
        ("usage", {"usage_map": {}}),
        ("cardinality", {"cardinalities": {}}),
    ),
)
def test_finalization_rejects_every_empty_or_vacuous_proof_map(
    empty_name: str,
    port_changes: Mapping[str, object],
) -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events, **dict(port_changes))
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.finalize_run(_finalization_command(module))

    assert _error_code(caught.value) == "CONSUMER_RECEIPT_INCOMPLETE", empty_name
    assert "write:run_succeeded" not in events
    assert events[-1] == "transaction:rollback"


@pytest.mark.parametrize(
    "usage_map",
    (
        {"edge-a": "1" * 64},
        {"edge-a": "1" * 64, "edge-b": "2" * 64, "edge-c": "3" * 64},
        {"edge-a": "1" * 64, "edge-b": "not-a-hash"},
    ),
)
def test_finalization_independently_rederives_exact_usage_map(
    usage_map: Mapping[str, str],
) -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events, usage_map=usage_map)
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.finalize_run(_finalization_command(module))

    assert _error_code(caught.value) == "CONSUMER_RECEIPT_INCOMPLETE"
    assert "derive:usage_map" in events
    assert "write:run_succeeded" not in events


def test_boundary_wraps_unexpected_exception_without_leaking_private_text() -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events, fail_at="derive:contract_map")

    def leaking_record(event: str, *, write: bool = False) -> None:
        port.events.append(event)
        if write:
            port.pending_writes.append(event)
        if event == "derive:contract_map":
            raise RuntimeError("private-company-name and SQL details")

    port._record = leaking_record  # type: ignore[method-assign]
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.finalize_run(_finalization_command(module))

    assert _error_code(caught.value) == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"
    assert str(caught.value) == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"
    assert "private-company-name" not in str(caught.value)


def test_finalization_requires_28_completed_canonical_rows_and_ignores_lifecycle() -> None:
    module = _module()
    success_events: list[str] = []
    success_port = _Port(
        success_events,
        steps=_steps(completed_count=28, include_lifecycle=True),
    )
    success_service, _factory = _service(module, success_events, success_port)

    result = success_service.finalize_run(_finalization_command(module))

    assert result.status == "SUCCEEDED"
    assert "write:run_succeeded" in success_events

    failure_events: list[str] = []
    failure_port = _Port(
        failure_events,
        steps=_steps(
            completed_count=28,
            include_lifecycle=True,
            status_override=(27, StepStatus.RUNNING),
        ),
    )
    failure_service, _factory = _service(module, failure_events, failure_port)
    with pytest.raises(module.Task7CompletionError) as caught:
        failure_service.finalize_run(_finalization_command(module))
    assert _error_code(caught.value) == "STEP_REHYDRATION_FAILED"
    assert "write:run_succeeded" not in failure_events
    assert failure_events[-1] == "transaction:rollback"


def test_successful_finalization_fences_succeeded_and_audit_in_the_same_uow() -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events)
    service, _factory = _service(module, events, port)

    result = service.finalize_run(_finalization_command(module))

    assert result == module.Task7RunFinalizationResult(
        run_id=RUN_ID,
        status="SUCCEEDED",
        edge_count=2,
        receipt_count=2,
    )
    assert port.mark_succeeded_kwargs is not None
    assert {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "lease_owner_id": LEASE_OWNER_ID,
        "lease_fencing_token": LEASE_FENCING_TOKEN,
        "status": "SUCCEEDED",
    }.items() <= port.mark_succeeded_kwargs.items()
    assert events[-3:] == [
        "write:run_succeeded",
        "write:finalization_audit",
        "transaction:commit",
    ]
    assert port.committed_writes[-2:] == [
        "write:run_succeeded",
        "write:finalization_audit",
    ]


@pytest.mark.parametrize(
    "failure_stage",
    (
        "derive:contract_map",
        "derive:persisted_map",
        "derive:receipt_map",
        "derive:cardinality",
        "prove:native_subroles",
        "read:steps",
        "write:run_succeeded",
        "write:finalization_audit",
    ),
)
def test_any_finalization_failure_rolls_back_status_and_audit(
    failure_stage: str,
) -> None:
    module = _module()
    events: list[str] = []
    port = _Port(events, fail_at=failure_stage)
    service, _factory = _service(module, events, port)

    with pytest.raises(module.Task7CompletionError) as caught:
        service.finalize_run(_finalization_command(module))

    assert _error_code(caught.value) == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"
    assert events[-1] == "transaction:rollback"
    assert port.committed_writes == []


def test_postgres_composition_source_binds_rls_fencing_locking_and_zero_io() -> None:
    module = _module()
    source = " ".join(inspect.getsource(module).split())

    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ WRITE" in source
    assert "SET LOCAL" in source or "set_tenant_local" in source
    assert "FOR UPDATE" in source
    assert "statement_timestamp()" in source
    assert "lease_owner_id" in source
    assert "lease_fencing_token" in source
    assert "lease_expires_at" in source
    assert source.index("lock_run_for_step_completion") < source.index("lock_run_step")
    assert source.index("lock_run_step") < source.index("lock_existing_native_rows")
    assert all(
        forbidden not in source
        for forbidden in (
            "httpx.",
            "requests.",
            "boto3.",
            "subprocess.",
            "open(",
            "Path(",
        )
    )
