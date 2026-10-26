"""Fenced Task 7 completion, resume, and finalization composition.

The durable port owns PostgreSQL-specific repositories and exact edge/usage
derivation.  This module owns only the reviewed transaction choreography:
short fenced completion boundaries, read-only completed-prefix validation, and
the terminal repeatable-read equality proof.  It deliberately has no network,
provider, filesystem, or object-store dependency.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Final, NoReturn, Protocol

from idis.models.run_step import RunStep, StepName, StepStatus
from idis.pipeline.contract_registry import (
    FULL_STRICT_V21_CONTRACT_HASH,
    PIPELINE_CONTRACTS,
)
from idis.pipeline.task7_lifecycle import (
    CONSUMER_RECEIPT_INCOMPLETE,
    STEP_OUTPUT_MISSING,
    STEP_REHYDRATION_FAILED,
    Task7LifecycleError,
    Task7ResumePlan,
    rehydrate_strict_run,
)
from idis.pipeline.task7_semantics import (
    PIPELINE_CONTRACT_HASH_MISMATCH,
    PIPELINE_CONTRACT_UNAVAILABLE,
    STEP_SEMANTIC_REGISTRY,
    STEP_SEMANTIC_VALIDATION_FAILED,
    SemanticValidationInput,
    SemanticValidationResult,
    StepSemanticValidator,
)
from idis.services.runs.steps import StagedNativeOutputWrite

WORKER_LEASE_FENCED: Final = "WORKER_LEASE_FENCED"
WORKER_LEASE_BOUNDARY_COMMIT_FAILED: Final = "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"

_FULL_STRICT_V21_VERSION: Final = "full-strict-v21"
_RESUMABLE: Final = "RESUMABLE"
_RUNNING: Final = "RUNNING"
_SUCCEEDED: Final = "SUCCEEDED"
_READ_ONLY_BEGIN_SQL: Final = "SET TRANSACTION READ ONLY"
_FINALIZATION_BEGIN_SQL: Final = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ WRITE"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")

# Literal PostgreSQL boundary contracts retained beside the coordinator so a
# concrete port cannot reinterpret the reviewed RLS, lock, or fencing shape.
_TENANT_RLS_SQL: Final = "SET LOCAL idis.tenant_id = :tenant_id"
_COMPLETION_RUN_LOCK_SQL: Final = """
SELECT run_id, status, pipeline_contract_version, pipeline_contract_hash,
       pipeline_resume_state, lease_owner_id, lease_fencing_token,
       lease_expires_at
FROM runs
WHERE tenant_id=:tenant_id AND deal_id=:deal_id AND run_id=:run_id
FOR UPDATE
"""
_COMPLETION_LEASE_RENEW_SQL: Final = """
UPDATE runs
SET last_heartbeat_at = statement_timestamp(),
    lease_expires_at = statement_timestamp() + make_interval(secs => :lease_seconds)
WHERE tenant_id=:tenant_id
  AND deal_id=:deal_id
  AND run_id=:run_id
  AND status='RUNNING'
  AND lease_owner_id=:lease_owner_id
  AND lease_fencing_token=:lease_fencing_token
  AND lease_expires_at > statement_timestamp()
"""
_FINALIZATION_RUN_LOCK_SQL: Final = """
SELECT run_id, status, pipeline_contract_version, pipeline_contract_hash,
       pipeline_resume_state, lease_owner_id, lease_fencing_token,
       lease_expires_at
FROM runs
WHERE tenant_id=:tenant_id AND deal_id=:deal_id AND run_id=:run_id
FOR UPDATE
"""

_PRESERVED_CODES: Final = frozenset(
    {
        WORKER_LEASE_FENCED,
        WORKER_LEASE_BOUNDARY_COMMIT_FAILED,
        STEP_REHYDRATION_FAILED,
        STEP_OUTPUT_MISSING,
        CONSUMER_RECEIPT_INCOMPLETE,
        PIPELINE_CONTRACT_UNAVAILABLE,
        PIPELINE_CONTRACT_HASH_MISMATCH,
        STEP_SEMANTIC_VALIDATION_FAILED,
    }
)


class Task7CompletionError(RuntimeError):
    """Stable fail-closed error from a Task 7 lifecycle boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Task7LeaseScope:
    """Exact admitted contract and live worker-fence identity."""

    tenant_id: str
    deal_id: str
    run_id: str
    pipeline_contract_version: str
    pipeline_contract_hash: str
    pipeline_resume_state: str
    lease_owner_id: str
    lease_fencing_token: int


@dataclass(frozen=True, slots=True)
class Task7StepCompletionCommand:
    """Typed inputs already staged outside the short completion transaction."""

    scope: Task7LeaseScope
    step_id: str
    step_name: str
    semantic_input: SemanticValidationInput
    staged_native_writes: tuple[StagedNativeOutputWrite, ...]


@dataclass(frozen=True, slots=True)
class Task7RunResumeCommand:
    """Read-only rehydration request for one claimed strict run."""

    scope: Task7LeaseScope


@dataclass(frozen=True, slots=True)
class Task7RunFinalizationCommand:
    """Terminal equality-proof request for one claimed strict run."""

    scope: Task7LeaseScope


@dataclass(frozen=True, slots=True)
class Task7StepCompletionResult:
    """Committed step-boundary result."""

    step_id: str
    step_name: str
    completed: bool


@dataclass(frozen=True, slots=True)
class Task7RunFinalizationResult:
    """Committed terminal-run result."""

    run_id: str
    status: str
    edge_count: int
    receipt_count: int


class _Task7DurablePort(Protocol):
    """PostgreSQL operations required by the reviewed composition boundary."""

    def establish_tenant_rls(self, **kwargs: object) -> None: ...

    def lock_run_for_step_completion(self, **kwargs: object) -> Mapping[str, object]: ...

    def renew_live_lease(self, **kwargs: object) -> Mapping[str, object]: ...

    def lock_run_step(self, **kwargs: object) -> RunStep: ...

    def lock_existing_native_rows(self, **kwargs: object) -> None: ...

    def validate_step_predecessors(self, **kwargs: object) -> bool: ...

    def persist_output_roots(self, **kwargs: object) -> None: ...

    def corroborate_persisted_native_outputs(self, **kwargs: object) -> None: ...

    def persist_step_outputs(self, **kwargs: object) -> None: ...

    def persist_applicability_decisions(self, **kwargs: object) -> None: ...

    def persist_new_outgoing_edges(self, **kwargs: object) -> None: ...

    def persist_step_receipt_input_references(
        self,
        *,
        scope: object,
        step: RunStep,
        semantic_result: SemanticValidationResult,
    ) -> None: ...

    def derive_and_persist_usage_receipts(self, **kwargs: object) -> None: ...

    def prove_incoming_receipt_equality(self, **kwargs: object) -> bool: ...

    def mark_step_completed(self, **kwargs: object) -> bool: ...

    def append_step_completion_audit(self, **kwargs: object) -> None: ...

    def load_resumable_run(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_canonical_steps(self, **kwargs: object) -> Sequence[RunStep]: ...

    def validate_completed_step_graph(self, **kwargs: object) -> bool: ...

    def lock_run_for_finalization(self, **kwargs: object) -> Mapping[str, object]: ...

    def derive_contract_edge_map(self, **kwargs: object) -> Mapping[str, object]: ...

    def load_persisted_edge_map(self, **kwargs: object) -> Mapping[str, object]: ...

    def derive_receipt_edge_map(self, **kwargs: object) -> Mapping[str, object]: ...

    def derive_usage_evidence_map(self, **kwargs: object) -> Mapping[str, str]: ...

    def load_contract_cardinality_equations(
        self,
        **kwargs: object,
    ) -> Mapping[str, int]: ...

    def validate_native_and_subrole_roots(self, **kwargs: object) -> None: ...

    def mark_run_succeeded(self, **kwargs: object) -> bool: ...

    def append_run_finalization_audit(self, **kwargs: object) -> None: ...


_ConnectionFactory = Callable[[], AbstractContextManager[Any]]
_PortFactory = Callable[[Any, str], _Task7DurablePort]


class Task7CompletionFinalizationService:
    """Compose Task 7 durable operations without owning their provider work."""

    def __init__(
        self,
        *,
        connection_factory: _ConnectionFactory,
        port_factory: _PortFactory,
    ) -> None:
        self._connection_factory = connection_factory
        self._port_factory = port_factory
        self._semantic_validator = StepSemanticValidator()

    def complete_step(
        self,
        command: Task7StepCompletionCommand,
    ) -> Task7StepCompletionResult:
        """Commit one semantic step and all its graph evidence atomically."""
        try:
            with self._connection_factory() as connection:
                scope = command.scope
                port = self._port_factory(connection, scope.tenant_id)
                port.establish_tenant_rls(scope=scope)

                locked_run = port.lock_run_for_step_completion(scope=scope)
                _require_live_run(locked_run, scope)
                renewed_run = port.renew_live_lease(scope=scope)
                _require_live_run(renewed_run, scope)
                locked_step = port.lock_run_step(
                    scope=scope,
                    step_id=command.step_id,
                    step_name=command.step_name,
                )
                _require_locked_step(command, locked_step)

                lock_keys = _native_lock_keys(command.staged_native_writes)
                port.lock_existing_native_rows(
                    scope=scope,
                    step=locked_step,
                    lock_keys=lock_keys,
                )
                if (
                    port.validate_step_predecessors(
                        scope=scope,
                        step=locked_step,
                    )
                    is not True
                ):
                    _fail(
                        STEP_REHYDRATION_FAILED,
                        "step predecessors do not form the reviewed completed prefix",
                    )
                semantic_result = self._semantic_validator.validate(command.semantic_input)
                _require_semantic_result(command, semantic_result)

                port.persist_output_roots(
                    scope=scope,
                    step=locked_step,
                    semantic_result=semantic_result,
                )
                for staged in command.staged_native_writes:
                    staged.persist(
                        db_conn=connection,
                        lease_owner_id=scope.lease_owner_id,
                        lease_fencing_token=scope.lease_fencing_token,
                    )
                port.corroborate_persisted_native_outputs(
                    scope=scope,
                    step=locked_step,
                    expected_native_outputs=semantic_result.native_outputs,
                )
                port.persist_step_outputs(
                    scope=scope,
                    step=locked_step,
                    semantic_result=semantic_result,
                )
                port.persist_applicability_decisions(
                    scope=scope,
                    step=locked_step,
                    semantic_result=semantic_result,
                )
                port.persist_new_outgoing_edges(
                    scope=scope,
                    step=locked_step,
                    semantic_result=semantic_result,
                )
                port.persist_step_receipt_input_references(
                    scope=scope,
                    step=locked_step,
                    semantic_result=semantic_result,
                )
                port.derive_and_persist_usage_receipts(
                    scope=scope,
                    step=locked_step,
                    semantic_result=semantic_result,
                )
                if (
                    port.prove_incoming_receipt_equality(
                        scope=scope,
                        step=locked_step,
                        semantic_result=semantic_result,
                    )
                    is not True
                ):
                    _fail(
                        CONSUMER_RECEIPT_INCOMPLETE,
                        "incoming contract, expectation, and receipt maps differ",
                    )
                if (
                    port.mark_step_completed(
                        scope=scope,
                        step_id=command.step_id,
                        step_name=command.step_name,
                        result_summary=command.semantic_input.result_payload,
                    )
                    is not True
                ):
                    _fail(WORKER_LEASE_FENCED, "step completion write was fenced")
                port.append_step_completion_audit(
                    scope=scope,
                    step_id=command.step_id,
                    step_name=command.step_name,
                )
        except Exception as exc:
            _raise_boundary_error(exc)

        return Task7StepCompletionResult(
            step_id=command.step_id,
            step_name=command.step_name,
            completed=True,
        )

    def validate_completed_step(
        self,
        command: Task7RunResumeCommand,
        *,
        step: RunStep,
    ) -> bool:
        """Revalidate one completed step without inserting or repairing rows."""
        try:
            with self._connection_factory() as connection:
                connection.exec_driver_sql(_READ_ONLY_BEGIN_SQL)
                scope = command.scope
                port = self._port_factory(connection, scope.tenant_id)
                port.establish_tenant_rls(scope=scope)
                run_row = port.load_resumable_run(scope=scope)
                _require_live_run(run_row, scope)
                if (
                    port.validate_completed_step_graph(
                        scope=scope,
                        step=step,
                    )
                    is not True
                ):
                    _fail(
                        STEP_REHYDRATION_FAILED,
                        "completed step failed immutable graph validation",
                    )
        except Exception as exc:
            _raise_boundary_error(exc)
        return True

    def rehydrate_run(
        self,
        command: Task7RunResumeCommand,
    ) -> Task7ResumePlan:
        """Validate a completed prefix through one fresh read-only composition."""
        try:
            with self._connection_factory() as connection:
                connection.exec_driver_sql(_READ_ONLY_BEGIN_SQL)
                scope = command.scope
                port = self._port_factory(connection, scope.tenant_id)
                port.establish_tenant_rls(scope=scope)
                run_row = port.load_resumable_run(scope=scope)
                _require_live_run(run_row, scope)
                steps = tuple(port.list_canonical_steps(scope=scope))
                return rehydrate_strict_run(
                    tenant_id=scope.tenant_id,
                    run_id=scope.run_id,
                    pipeline_contract_version=scope.pipeline_contract_version,
                    pipeline_contract_hash=scope.pipeline_contract_hash,
                    pipeline_resume_state=scope.pipeline_resume_state,
                    steps=steps,
                    validate_completed=lambda step: _validate_completed_graph(
                        port=port,
                        scope=scope,
                        step=step,
                    ),
                )
        except Exception as exc:
            _raise_boundary_error(exc)

    def finalize_run(
        self,
        command: Task7RunFinalizationCommand,
    ) -> Task7RunFinalizationResult:
        """Prove exact terminal graph equality and fence the SUCCEEDED write."""
        try:
            with self._connection_factory() as connection:
                connection.exec_driver_sql(_FINALIZATION_BEGIN_SQL)
                scope = command.scope
                port = self._port_factory(connection, scope.tenant_id)
                port.establish_tenant_rls(scope=scope)
                locked_run = port.lock_run_for_finalization(scope=scope)
                _require_live_run(locked_run, scope)

                contract_map = dict(port.derive_contract_edge_map(scope=scope))
                persisted_map = dict(port.load_persisted_edge_map(scope=scope))
                receipt_map = dict(port.derive_receipt_edge_map(scope=scope))
                usage_map = dict(port.derive_usage_evidence_map(scope=scope))
                cardinalities = dict(port.load_contract_cardinality_equations(scope=scope))
                _require_three_way_equality(
                    contract_map=contract_map,
                    persisted_map=persisted_map,
                    receipt_map=receipt_map,
                    usage_map=usage_map,
                    cardinalities=cardinalities,
                )

                port.validate_native_and_subrole_roots(
                    scope=scope,
                    contract_map=contract_map,
                    receipt_map=receipt_map,
                )
                steps = tuple(port.list_canonical_steps(scope=scope))
                _require_complete_canonical_steps(scope, steps)
                if (
                    port.mark_run_succeeded(
                        tenant_id=scope.tenant_id,
                        deal_id=scope.deal_id,
                        run_id=scope.run_id,
                        lease_owner_id=scope.lease_owner_id,
                        lease_fencing_token=scope.lease_fencing_token,
                        status=_SUCCEEDED,
                    )
                    is not True
                ):
                    _fail(WORKER_LEASE_FENCED, "terminal success write was fenced")
                port.append_run_finalization_audit(
                    scope=scope,
                    status=_SUCCEEDED,
                    edge_count=len(contract_map),
                    receipt_count=len(receipt_map),
                )
        except Exception as exc:
            _raise_boundary_error(exc)

        return Task7RunFinalizationResult(
            run_id=command.scope.run_id,
            status=_SUCCEEDED,
            edge_count=len(contract_map),
            receipt_count=len(receipt_map),
        )


def _native_lock_keys(
    staged_writes: Sequence[StagedNativeOutputWrite],
) -> tuple[str, ...]:
    lock_keys: set[str] = set()
    for staged in staged_writes:
        if not isinstance(staged, StagedNativeOutputWrite):
            _fail(
                WORKER_LEASE_BOUNDARY_COMMIT_FAILED,
                "completion received an untyped native write",
            )
        if staged.binding.non_sql_methods:
            _fail(
                WORKER_LEASE_BOUNDARY_COMMIT_FAILED,
                "completion-boundary native write requests external I/O",
            )
        if not staged.contract.persistence_target.startswith("postgres:"):
            _fail(
                WORKER_LEASE_BOUNDARY_COMMIT_FAILED,
                "completion-boundary native write is not PostgreSQL-backed",
            )
        lock_keys.update(staged.binding.sql_targets)
    return tuple(sorted(lock_keys))


def _require_semantic_result(
    command: Task7StepCompletionCommand,
    result: SemanticValidationResult,
) -> None:
    if not isinstance(result, SemanticValidationResult):
        _fail(STEP_OUTPUT_MISSING, "completion lacks a typed semantic result")
    if (
        result.step_name != command.step_name
        or result.terminal_status != StepStatus.COMPLETED.value
    ):
        _fail(STEP_OUTPUT_MISSING, "semantic result does not prove step completion")


def _require_locked_step(
    command: Task7StepCompletionCommand,
    step: RunStep,
) -> None:
    scope = command.scope
    spec = PIPELINE_CONTRACTS[_FULL_STRICT_V21_VERSION].steps[
        STEP_SEMANTIC_REGISTRY[command.step_name].step_order
    ]
    step_name = (
        step.step_name.value if isinstance(step.step_name, StepName) else str(step.step_name)
    )
    if (
        step.step_id != command.step_id
        or step_name != command.step_name
        or step.tenant_id != scope.tenant_id
        or step.run_id != scope.run_id
        or step.step_order != spec.step_order
        or step.status is not StepStatus.RUNNING
    ):
        _fail(
            STEP_REHYDRATION_FAILED,
            "locked step is not the exact live canonical step",
        )
    if (
        step.pipeline_contract_version != scope.pipeline_contract_version
        or step.pipeline_contract_hash != scope.pipeline_contract_hash
        or step.semantic_rule_version != spec.semantic_rule_version
        or step.semantic_rule_hash != spec.semantic_rule_hash
    ):
        _fail(
            PIPELINE_CONTRACT_HASH_MISMATCH,
            "locked step differs from the reviewed full-strict-v21 tuple",
        )


def _require_live_run(
    run_row: Mapping[str, object],
    scope: Task7LeaseScope,
) -> None:
    if (
        scope.pipeline_contract_version != _FULL_STRICT_V21_VERSION
        or scope.pipeline_contract_hash != FULL_STRICT_V21_CONTRACT_HASH
        or scope.pipeline_resume_state != _RESUMABLE
        or run_row.get("tenant_id") != scope.tenant_id
        or run_row.get("deal_id") != scope.deal_id
        or run_row.get("run_id") != scope.run_id
        or run_row.get("pipeline_contract_version") != scope.pipeline_contract_version
        or run_row.get("pipeline_contract_hash") != scope.pipeline_contract_hash
        or run_row.get("pipeline_resume_state") != scope.pipeline_resume_state
    ):
        _fail(
            STEP_REHYDRATION_FAILED,
            "run differs from the exact resumable full-strict-v21 contract",
        )
    if (
        run_row.get("status") != _RUNNING
        or run_row.get("lease_owner_id") != scope.lease_owner_id
        or run_row.get("lease_fencing_token") != scope.lease_fencing_token
        or run_row.get("lease_is_live") is not True
    ):
        _fail(WORKER_LEASE_FENCED, "run lease is not live for the claiming worker")


def _require_three_way_equality(
    *,
    contract_map: Mapping[str, object],
    persisted_map: Mapping[str, object],
    receipt_map: Mapping[str, object],
    usage_map: Mapping[str, str],
    cardinalities: Mapping[str, int],
) -> None:
    if not all((contract_map, persisted_map, receipt_map, usage_map, cardinalities)):
        _fail(
            CONSUMER_RECEIPT_INCOMPLETE,
            "terminal equality proof contains an empty map",
        )
    if contract_map != persisted_map:
        _fail(
            CONSUMER_RECEIPT_INCOMPLETE,
            "derived and persisted edge maps differ",
        )
    if set(contract_map) != set(receipt_map) or set(contract_map) != set(usage_map):
        _fail(
            CONSUMER_RECEIPT_INCOMPLETE,
            "derived, receipted, and usage edge keys differ",
        )
    for key, expected_edge in contract_map.items():
        receipt_edge, receipt_usage_hash = _receipt_projection(receipt_map[key])
        expected_usage_hash = usage_map[key]
        if (
            receipt_edge != expected_edge
            or receipt_usage_hash != expected_usage_hash
            or _SHA256_RE.fullmatch(expected_usage_hash) is None
        ):
            _fail(
                CONSUMER_RECEIPT_INCOMPLETE,
                "receipt or independently derived usage evidence differs",
            )

    derived_counts: Counter[str] = Counter()
    for value in contract_map.values():
        rule_id = _edge_rule_id(value)
        if rule_id is None:
            _fail(
                CONSUMER_RECEIPT_INCOMPLETE,
                "derived edge lacks its contract rule identity",
            )
        derived_counts[rule_id] += 1
    if dict(derived_counts) != dict(cardinalities):
        _fail(
            CONSUMER_RECEIPT_INCOMPLETE,
            "derived edge cardinality differs from the reviewed equations",
        )


def _receipt_projection(value: object) -> tuple[object, str | None]:
    if isinstance(value, Mapping):
        usage_hash = value.get("usage_evidence_sha256")
        edge_projection = {
            key: item for key, item in value.items() if key != "usage_evidence_sha256"
        }
        return edge_projection, usage_hash if isinstance(usage_hash, str) else None
    usage_hash = getattr(value, "usage_evidence_sha256", None)
    attribute_projection = getattr(value, "edge_projection", None)
    return attribute_projection, usage_hash if isinstance(usage_hash, str) else None


def _edge_rule_id(value: object) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("contract_rule_id")
    else:
        candidate = getattr(value, "contract_rule_id", None)
    return candidate if isinstance(candidate, str) and candidate else None


def _require_complete_canonical_steps(
    scope: Task7LeaseScope,
    steps: Sequence[RunStep],
) -> None:
    canonical = tuple(
        step
        for step in steps
        if (step.step_name.value if isinstance(step.step_name, StepName) else str(step.step_name))
        != StepName.RUN_LIFECYCLE.value
    )
    plan = rehydrate_strict_run(
        tenant_id=scope.tenant_id,
        run_id=scope.run_id,
        pipeline_contract_version=scope.pipeline_contract_version,
        pipeline_contract_hash=scope.pipeline_contract_hash,
        pipeline_resume_state=scope.pipeline_resume_state,
        steps=canonical,
        validate_completed=lambda _step: True,
    )
    contract = PIPELINE_CONTRACTS[_FULL_STRICT_V21_VERSION]
    if (
        plan.finalization_required is not True
        or plan.completed_count != contract.required_cardinality
    ):
        _fail(
            STEP_REHYDRATION_FAILED,
            "run lacks all 28 completed canonical steps",
        )


def _validate_completed_graph(
    *,
    port: _Task7DurablePort,
    scope: Task7LeaseScope,
    step: RunStep,
) -> bool:
    """Adapt a durable stable code into the lifecycle validator's typed boundary."""
    try:
        return port.validate_completed_step_graph(scope=scope, step=step)
    except Exception as exc:
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code in _PRESERVED_CODES:
            raise Task7LifecycleError(code, str(exc) or code) from exc
        raise


def _fail(code: str, message: str) -> NoReturn:
    raise Task7CompletionError(code, message)


def _raise_boundary_error(exc: Exception) -> NoReturn:
    if isinstance(exc, Task7CompletionError):
        raise exc
    code = getattr(exc, "code", None)
    if isinstance(exc, Task7LifecycleError):
        code = exc.code
    if not isinstance(code, str) or code not in _PRESERVED_CODES:
        code = WORKER_LEASE_BOUNDARY_COMMIT_FAILED
    raise Task7CompletionError(code, code) from exc
