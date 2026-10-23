"""Transaction-scoped PostgreSQL port for Task 7 completion and finalization.

This port never owns a transaction and never reads ambient configuration.  It
executes only generated selector authority and code-owned SQL against the
caller-supplied connection.  An absent selector relation is an empty producer
set only when it is in the reviewed future-relation census, the catalog is
dev 0039, and every present relation of that selector is in the 0038 profile.
Every other absence, including a usage-reference relation, raises
PIPELINE_CONTRACT_UNAVAILABLE.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Final, Never, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from idis.models.run_step import RunStep, StepName, StepStatus
from idis.persistence.db import DatabaseConfigError, set_tenant_local
from idis.persistence.repositories.task7_run_graph import (
    PostgresTask7RunGraphRepository,
    RunApplicabilityDecisionRecord,
    RunConsumerReceiptRecord,
    RunExpectedConsumerEdgeRecord,
    RunOutputObjectRecord,
    RunStepOutputRecord,
    Task7RunGraphRepositoryError,
)
from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH
from idis.pipeline.task7_census import CENSUS_SCHEMA_REGISTRY
from idis.pipeline.task7_edges import (
    ApplicabilityDecision,
    EdgeDerivationRequest,
    ExpectedConsumerEdge,
    ImmutableProducerFact,
    ProducerSelectionRequest,
    ProducerSelectorSpec,
    derive_expected_edges,
    derive_rule_bound_producers,
)
from idis.pipeline.task7_native_registry import NATIVE_OUTPUT_REGISTRY
from idis.pipeline.task7_semantics import (
    PIPELINE_CONTRACT_HASH_MISMATCH,
    PIPELINE_CONTRACT_UNAVAILABLE,
    STEP_SEMANTIC_REGISTRY,
    ApplicabilityDecisionEvidence,
    NativeOutputEvidence,
    SemanticValidationResult,
    Step5ApplicabilityEvidenceV1,
    Step16ProviderApplicabilityEvidenceV1,
)
from idis.pipeline.task7_usage import (
    CONSUMER_RECEIPT_INCOMPLETE,
    USAGE_ROUTE_REGISTRY,
    ConsumerReceiptRootV1,
    ProducerRefV1,
    ReferenceOccurrenceV1,
    UsageExtractionRequest,
    extract_usage_evidence,
)
from idis.pipeline.task7_uuid import Task7UuidError, derive_task7_uuid
from idis.runtime.dependency_probes import TENANT_RLS_TABLES
from idis.runtime.dependency_readiness import TASK7_V21_FUTURE_ERASURE_RELATIONS

WORKER_LEASE_FENCED: Final = "WORKER_LEASE_FENCED"
STEP_REHYDRATION_FAILED: Final = "STEP_REHYDRATION_FAILED"
STEP_OUTPUT_MISSING: Final = "STEP_OUTPUT_MISSING"
WORKER_LEASE_BOUNDARY_COMMIT_FAILED: Final = "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"

_FULL_STRICT_V21_VERSION: Final = "full-strict-v21"
_RESUMABLE: Final = "RESUMABLE"
_IDENTIFIER_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_LEASE_SECONDS_MIN: Final = 1
_LEASE_SECONDS_MAX: Final = 86_400
# The 38-name preimage census, bound to dependency_readiness
# TASK7_V21_FUTURE_ERASURE_RELATIONS (equal to
# contracts/full_strict_v21_reviewed_preimage_v1.json
# erasure_execution_registry.rows[0].future_activation_census_tuple).
_REVIEWED_FUTURE_RELATIONS: Final = frozenset(TASK7_V21_FUTURE_ERASURE_RELATIONS)
# The 64 tables of the installed 0038 profile.
_CATALOG_0038_PROFILE_TABLES: Final = TENANT_RLS_TABLES
_DEV_0039_REVISION: Final = "0039"
_ROW_14_RELATION: Final = "step_receipt_input_references"
_STEP5_EVIDENCE_SCHEMA: Final = (
    "step5-applicability-evidence",
    "task7-step5-applicability-evidence-v1",
    "771953c017501a9729ce558074092bac8c34ee491ebe4a0d7de16c246f2296a2",
)
_STEP16_EVIDENCE_SCHEMA: Final = (
    "step16-provider-applicability-evidence",
    "task7-step16-provider-applicability-evidence-v1",
    "954bfcc671a0baed07c0a4a9bdf85da8eb3880a10dc1fda2b1ceee4b15631eda",
)
_CURRENT_NATIVE_HASHES_SQL: Final = """
SELECT output_kind,
       expected_native_identity_sha256,
       expected_native_output_sha256
FROM public.idis_task7_current_native_hashes_v1(
    CAST(:tenant_id AS uuid),
    CAST(:deal_id AS uuid),
    CAST(:run_id AS uuid),
    CAST(:output_object_id AS uuid)
)
"""

_TENANT_RLS_SQL: Final = "SELECT set_config('idis.tenant_id', :tenant_id, true)"
_RUN_LOCK_SQL: Final = """
SELECT tenant_id, deal_id, run_id, status, pipeline_contract_version,
       pipeline_contract_hash, pipeline_resume_state, lease_owner_id,
       lease_fencing_token, lease_expires_at,
       (lease_expires_at > statement_timestamp()) AS lease_is_live
FROM runs
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND deal_id = CAST(:deal_id AS uuid)
  AND run_id = CAST(:run_id AS uuid)
FOR UPDATE
"""
_LEASE_RENEW_SQL: Final = """
UPDATE runs
SET last_heartbeat_at = statement_timestamp(),
    lease_expires_at = statement_timestamp()
        + make_interval(secs => :lease_seconds)
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND deal_id = CAST(:deal_id AS uuid)
  AND run_id = CAST(:run_id AS uuid)
  AND status = 'RUNNING'
  AND lease_owner_id = CAST(:lease_owner_id AS uuid)
  AND lease_fencing_token = :lease_fencing_token
  AND lease_expires_at > statement_timestamp()
RETURNING tenant_id, deal_id, run_id, status,
          pipeline_contract_version, pipeline_contract_hash,
          pipeline_resume_state, lease_owner_id, lease_fencing_token,
          lease_expires_at,
          (lease_expires_at > statement_timestamp()) AS lease_is_live
"""
_STEP_LOCK_SQL: Final = """
SELECT tenant_id, run_id, step_id, step_name, step_order, status,
       started_at, finished_at, error_code, error_message, retry_count,
       result_summary, pipeline_contract_version, pipeline_contract_hash,
       semantic_rule_version, semantic_rule_hash
FROM run_steps
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND run_id = CAST(:run_id AS uuid)
  AND step_id = CAST(:step_id AS uuid)
  AND step_name = :step_name
FOR UPDATE
"""
_PREDECESSOR_SQL: Final = """
SELECT step_id, step_order, status
FROM run_steps
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND run_id = CAST(:run_id AS uuid)
  AND step_order < :step_order
ORDER BY step_order, step_id
"""
_CANONICAL_STEPS_SQL: Final = """
SELECT tenant_id, run_id, step_id, step_name, step_order, status,
       started_at, finished_at, error_code, error_message, retry_count,
       result_summary, pipeline_contract_version, pipeline_contract_hash,
       semantic_rule_version, semantic_rule_hash
FROM run_steps
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND run_id = CAST(:run_id AS uuid)
  AND step_name <> 'RUN_LIFECYCLE'
ORDER BY step_order, step_id
"""


class Task7PostgresPortError(RuntimeError):
    """Leakage-safe stable PostgreSQL boundary error."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> Never:
    raise Task7PostgresPortError(code)


def _census_reference_ordinal(refs: object, producer: ProducerRefV1) -> int:
    """Return the producer's index in the canonical census input_refs array."""

    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
        _fail(CONSUMER_RECEIPT_INCOMPLETE)
    matches = [
        index
        for index, item in enumerate(refs)
        if isinstance(item, Mapping)
        and item.get("producer_type") == producer.producer_type
        and str(item.get("producer_id")) == str(producer.producer_id)
        and item.get("producer_identity_sha256") == producer.producer_identity_sha256
        and item.get("producer_value_sha256") == producer.producer_value_sha256
    ]
    if len(matches) != 1:
        _fail(CONSUMER_RECEIPT_INCOMPLETE)
    return matches[0]


def required_usage_reference_relations() -> frozenset[str]:
    """Derive the complete reference-relation dependency from route authority."""

    return frozenset(route.native_reference_relation for route in USAGE_ROUTE_REGISTRY)


def _scope_params(scope: object) -> dict[str, object]:
    values = {
        name: getattr(scope, name, None)
        for name in (
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract_version",
            "pipeline_contract_hash",
            "pipeline_resume_state",
            "lease_owner_id",
            "lease_fencing_token",
        )
    }
    try:
        for name in (
            "tenant_id",
            "deal_id",
            "run_id",
            "lease_owner_id",
        ):
            parsed = str(UUID(cast(str, values[name])))
            if parsed != values[name]:
                _fail(STEP_REHYDRATION_FAILED)
    except (TypeError, ValueError):
        _fail(STEP_REHYDRATION_FAILED)
    if (
        values["pipeline_contract_version"] != _FULL_STRICT_V21_VERSION
        or values["pipeline_contract_hash"] != FULL_STRICT_V21_CONTRACT_HASH
        or values["pipeline_resume_state"] != _RESUMABLE
        or type(values["lease_fencing_token"]) is not int
        or values["lease_fencing_token"] <= 0
    ):
        _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
    return values


def _mapping(row: object | None, *, code: str) -> Mapping[str, object]:
    if row is None:
        _fail(code)
    value = getattr(row, "_mapping", row)
    if not isinstance(value, Mapping):
        _fail(code)
    return cast(Mapping[str, object], value)


def _stringify_uuid_fields(
    row: Mapping[str, object],
) -> dict[str, object]:
    result = dict(row)
    for key in (
        "tenant_id",
        "deal_id",
        "run_id",
        "lease_owner_id",
        "step_id",
    ):
        if result.get(key) is not None:
            result[key] = str(result[key])
    return result


def _hash(value: object) -> str:
    from idis.pipeline.task7_edges import _canonical_hash

    return _canonical_hash(value)


def _step_from_row(row: object) -> RunStep:
    values = _stringify_uuid_fields(_mapping(row, code=STEP_REHYDRATION_FAILED))
    try:
        summary = values.get("result_summary")
        if not isinstance(summary, dict):
            summary = dict(summary) if isinstance(summary, Mapping) else {}
        return RunStep(
            step_id=cast(str, values["step_id"]),
            run_id=cast(str, values["run_id"]),
            tenant_id=cast(str, values["tenant_id"]),
            step_name=StepName(str(values["step_name"])),
            step_order=cast(int, values["step_order"]),
            status=StepStatus(str(values["status"])),
            started_at=(
                str(values["started_at"]) if values.get("started_at") is not None else None
            ),
            finished_at=(
                str(values["finished_at"]) if values.get("finished_at") is not None else None
            ),
            error_code=cast(str | None, values.get("error_code")),
            error_message=cast(str | None, values.get("error_message")),
            retry_count=cast(int, values.get("retry_count", 0)),
            result_summary=summary,
            pipeline_contract_version=cast(str, values["pipeline_contract_version"]),
            pipeline_contract_hash=cast(str, values["pipeline_contract_hash"]),
            semantic_rule_version=cast(str, values["semantic_rule_version"]),
            semantic_rule_hash=cast(str, values["semantic_rule_hash"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Task7PostgresPortError(STEP_REHYDRATION_FAILED) from exc


class PostgresTask7CompletionPort:
    """Concrete implementation of the Task 7 durable service protocol."""

    def __init__(
        self,
        connection: Any,
        tenant_id: str,
        audit_sink: Any,
        lease_seconds: int,
    ) -> None:
        try:
            canonical_tenant = str(UUID(tenant_id))
        except (TypeError, ValueError) as exc:
            raise Task7PostgresPortError(STEP_REHYDRATION_FAILED) from exc
        if (
            canonical_tenant != tenant_id
            or type(lease_seconds) is not int
            or not _LEASE_SECONDS_MIN <= lease_seconds <= _LEASE_SECONDS_MAX
            or not callable(getattr(audit_sink, "emit_in_tx", None))
        ):
            _fail(WORKER_LEASE_BOUNDARY_COMMIT_FAILED)
        self._connection = connection
        self._tenant_id = tenant_id
        self._audit_sink = audit_sink
        self._lease_seconds = lease_seconds
        self._graph = PostgresTask7RunGraphRepository(
            connection,
            tenant_id,
        )
        self._step_roots: tuple[tuple[str, str, int, str, str, str], ...] = ()

    def establish_tenant_rls(self, **kwargs: object) -> None:
        scope = kwargs.get("scope")
        params = _scope_params(scope)
        if params["tenant_id"] != self._tenant_id:
            _fail(STEP_REHYDRATION_FAILED)
        try:
            set_tenant_local(self._connection, self._tenant_id)
            self._connection.execute(
                text(_TENANT_RLS_SQL),
                {"tenant_id": self._tenant_id},
            )
        except (DatabaseConfigError, SQLAlchemyError) as exc:
            raise Task7PostgresPortError(WORKER_LEASE_BOUNDARY_COMMIT_FAILED) from exc

    def lock_run_for_step_completion(
        self,
        **kwargs: object,
    ) -> Mapping[str, object]:
        return self._lock_run(kwargs.get("scope"))

    def renew_live_lease(
        self,
        **kwargs: object,
    ) -> Mapping[str, object]:
        params = _scope_params(kwargs.get("scope"))
        params["lease_seconds"] = self._lease_seconds
        row = self._execute(text(_LEASE_RENEW_SQL), params).one_or_none()
        return _stringify_uuid_fields(_mapping(row, code=WORKER_LEASE_FENCED))

    def lock_run_step(self, **kwargs: object) -> RunStep:
        params = _scope_params(kwargs.get("scope"))
        params.update(
            step_id=kwargs.get("step_id"),
            step_name=kwargs.get("step_name"),
        )
        row = self._execute(text(_STEP_LOCK_SQL), params).one_or_none()
        return _step_from_row(_mapping(row, code=STEP_REHYDRATION_FAILED))

    def lock_existing_native_rows(self, **kwargs: object) -> None:
        scope = kwargs.get("scope")
        params = _scope_params(scope)
        lock_keys = kwargs.get("lock_keys")
        if not isinstance(lock_keys, tuple):
            _fail(STEP_OUTPUT_MISSING)
        allowed = {spec.relation_name for spec in NATIVE_OUTPUT_REGISTRY.values()}
        for relation in sorted(lock_keys):
            if (
                not isinstance(relation, str)
                or relation not in allowed
                or _IDENTIFIER_RE.fullmatch(relation) is None
            ):
                _fail(PIPELINE_CONTRACT_UNAVAILABLE)
            columns = self._relation_columns(relation)
            if not {"tenant_id", "deal_id", "run_id"} <= columns:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            key_columns = self._primary_key_columns(relation)
            if not key_columns:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            projection = ", ".join(key_columns)
            ordering = ", ".join(f"{column} ASC" for column in key_columns)
            self._execute(
                text(
                    f"""
                    SELECT {projection}
                    FROM {relation}
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                    ORDER BY {ordering}
                    FOR UPDATE
                    """
                ),
                params,
            ).fetchall()

    def validate_step_predecessors(self, **kwargs: object) -> bool:
        scope = kwargs.get("scope")
        step = kwargs.get("step")
        if not isinstance(step, RunStep):
            _fail(STEP_REHYDRATION_FAILED)
        params = _scope_params(scope)
        params["step_order"] = step.step_order
        rows = self._execute(text(_PREDECESSOR_SQL), params).fetchall()
        return len(rows) == step.step_order and all(
            _mapping(row, code=STEP_REHYDRATION_FAILED).get("status") == "COMPLETED"
            and _mapping(row, code=STEP_REHYDRATION_FAILED).get("step_order") == ordinal
            for ordinal, row in enumerate(rows)
        )

    def persist_output_roots(self, **kwargs: object) -> None:
        scope = kwargs.get("scope")
        step = kwargs.get("step")
        result = kwargs.get("semantic_result")
        if not isinstance(step, RunStep) or not isinstance(result, SemanticValidationResult):
            _fail(STEP_OUTPUT_MISSING)
        params = _scope_params(scope)
        roots: list[tuple[str, str, int, str, str, str]] = []

        native_rows = sorted(
            result.native_outputs,
            key=lambda row: (row.output_kind, row.output_object_id.bytes),
        )
        for ordinal, native in enumerate(native_rows):
            self._insert_output_root(
                scope=scope,
                output_object_id=native.output_object_id,
                output_class="NATIVE_ROW",
                output_kind=native.output_kind,
                schema_version=result.semantic_rule_version,
                identity_sha256=native.native_identity_sha256,
                value_sha256=native.native_output_sha256,
            )
            roots.append(
                (
                    "__step__",
                    native.output_kind,
                    ordinal,
                    str(native.output_object_id),
                    native.native_identity_sha256,
                    native.native_output_sha256,
                )
            )

        subroles = sorted(result.subrole_receipts, key=lambda row: row.subrole)
        for subrole in subroles:
            output_id = self._derive_output_id(
                scope=scope,
                output_class="STEP_RECEIPT",
                output_kind=result.aggregate_receipt_kind,
                identity_sha256=subrole.native_identity_sha256,
                value_sha256=subrole.native_output_sha256,
            )
            self._insert_output_root(
                scope=scope,
                output_object_id=output_id,
                output_class="STEP_RECEIPT",
                output_kind=result.aggregate_receipt_kind,
                schema_version=result.semantic_rule_version,
                identity_sha256=subrole.native_identity_sha256,
                value_sha256=subrole.native_output_sha256,
            )
            roots.append(
                (
                    subrole.subrole,
                    result.aggregate_receipt_kind,
                    0,
                    str(output_id),
                    subrole.native_identity_sha256,
                    subrole.native_output_sha256,
                )
            )

        aggregate_identity = _hash(
            [
                "step-receipt-identity-v1",
                params["tenant_id"],
                params["deal_id"],
                params["run_id"],
                params["pipeline_contract_version"],
                params["pipeline_contract_hash"],
                step.step_id,
                step.step_name.value,
                "__step__",
                result.aggregate_receipt_kind,
                0,
            ]
        )
        aggregate_value = _hash(
            [
                "step-receipt-output-v1",
                aggregate_identity,
                result.semantic_rule_version,
                result.semantic_rule_hash,
                result.terminal_status,
                result.census.schema_id,
                result.census.schema_version,
                result.census.schema_sha256,
                result.census.canonical_sha256,
                [
                    [
                        row[0],
                        row[1],
                        row[2],
                        row[4],
                        row[5],
                    ]
                    for row in roots
                ],
            ]
        )
        aggregate_id = self._derive_output_id(
            scope=scope,
            output_class="STEP_RECEIPT",
            output_kind=result.aggregate_receipt_kind,
            identity_sha256=aggregate_identity,
            value_sha256=aggregate_value,
        )
        self._insert_output_root(
            scope=scope,
            output_object_id=aggregate_id,
            output_class="STEP_RECEIPT",
            output_kind=result.aggregate_receipt_kind,
            schema_version=result.semantic_rule_version,
            identity_sha256=aggregate_identity,
            value_sha256=aggregate_value,
        )
        roots.append(
            (
                "__step__",
                result.aggregate_receipt_kind,
                0,
                str(aggregate_id),
                aggregate_identity,
                aggregate_value,
            )
        )
        self._step_roots = tuple(roots)

    def corroborate_persisted_native_outputs(self, **kwargs: object) -> None:
        """Rederive every current native NK/P through the frozen PostgreSQL helper."""

        scope = kwargs.get("scope")
        step = kwargs.get("step")
        supplied = kwargs.get("expected_native_outputs")
        completion_phase = supplied is not None
        failure_code = STEP_OUTPUT_MISSING if completion_phase else STEP_REHYDRATION_FAILED
        if not isinstance(step, RunStep):
            _fail(failure_code)
        params = _scope_params(scope)
        persisted_roots = {
            row.output_object_id: row
            for row in self._graph.list_run_output_objects(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }

        expectations: tuple[NativeOutputEvidence, ...]
        if completion_phase:
            if not isinstance(supplied, tuple) or any(
                not isinstance(row, NativeOutputEvidence) for row in supplied
            ):
                _fail(failure_code)
            expectations = cast(tuple[NativeOutputEvidence, ...], supplied)
        else:
            step_rows = tuple(
                row
                for row in self._graph.list_run_step_outputs(
                    deal_id=cast(str, params["deal_id"]),
                    run_id=cast(str, params["run_id"]),
                )
                if row.step_id == step.step_id
            )
            native_rows: list[NativeOutputEvidence] = []
            for row in step_rows:
                root = persisted_roots.get(row.output_object_id)
                if root is None:
                    _fail(failure_code)
                if root.output_class != "NATIVE_ROW":
                    continue
                try:
                    output_object_id = UUID(root.output_object_id)
                except (TypeError, ValueError) as exc:
                    raise Task7PostgresPortError(failure_code) from exc
                native_rows.append(
                    NativeOutputEvidence(
                        output_kind=root.output_kind,
                        output_object_id=output_object_id,
                        native_identity_sha256=root.native_identity_sha256,
                        native_output_sha256=root.native_output_sha256,
                        persisted=True,
                    )
                )
            expectations = tuple(
                sorted(
                    native_rows,
                    key=lambda row: (row.output_kind, row.output_object_id.bytes),
                )
            )

        seen: set[str] = set()
        for expected in expectations:
            expected_output_object_id = str(expected.output_object_id)
            if expected_output_object_id in seen:
                _fail(failure_code)
            seen.add(expected_output_object_id)
            root = persisted_roots.get(expected_output_object_id)
            if (
                root is None
                or root.output_class != "NATIVE_ROW"
                or root.output_kind != expected.output_kind
                or root.native_identity_sha256 != expected.native_identity_sha256
                or root.native_output_sha256 != expected.native_output_sha256
            ):
                _fail(failure_code)
            helper_params = {
                "tenant_id": params["tenant_id"],
                "deal_id": params["deal_id"],
                "run_id": params["run_id"],
                "output_object_id": expected_output_object_id,
            }
            try:
                rows = self._connection.execute(
                    text(_CURRENT_NATIVE_HASHES_SQL),
                    helper_params,
                ).fetchall()
            except SQLAlchemyError as exc:
                raise Task7PostgresPortError(failure_code) from exc
            if len(rows) != 1:
                _fail(failure_code)
            helper = _mapping(rows[0], code=failure_code)
            if (
                helper.get("output_kind") != expected.output_kind
                or helper.get("expected_native_identity_sha256") != expected.native_identity_sha256
                or helper.get("expected_native_output_sha256") != expected.native_output_sha256
            ):
                _fail(failure_code)

    def persist_step_outputs(self, **kwargs: object) -> None:
        scope = kwargs.get("scope")
        step = kwargs.get("step")
        result = kwargs.get("semantic_result")
        if (
            not isinstance(step, RunStep)
            or not isinstance(result, SemanticValidationResult)
            or not self._step_roots
        ):
            _fail(STEP_OUTPUT_MISSING)
        params = _scope_params(scope)
        for subrole, output_kind, ordinal, output_id, identity, value in self._step_roots:
            step_output_id = self._derive_step_output_id(
                scope=scope,
                step=step,
                subrole=subrole,
                output_kind=output_kind,
                ordinal=ordinal,
                output_object_id=output_id,
            )
            summary = _hash(
                [
                    "step-output-summary-v1",
                    step.step_name.value,
                    subrole,
                    output_kind,
                    result.census.canonical_sha256,
                    identity,
                    value,
                ]
            )
            self._graph.insert_run_step_output(
                RunStepOutputRecord(
                    step_output_id=str(step_output_id),
                    tenant_id=cast(str, params["tenant_id"]),
                    deal_id=cast(str, params["deal_id"]),
                    run_id=cast(str, params["run_id"]),
                    step_id=step.step_id,
                    output_object_id=output_id,
                    step_name=step.step_name.value,
                    subrole=subrole,
                    output_kind=output_kind,
                    output_ordinal=ordinal,
                    semantic_rule_version=result.semantic_rule_version,
                    semantic_rule_hash=result.semantic_rule_hash,
                    summary_sha256=summary,
                    lease_owner_id=cast(str, params["lease_owner_id"]),
                    lease_fencing_token=cast(int, params["lease_fencing_token"]),
                )
            )

    def persist_applicability_decisions(self, **kwargs: object) -> None:
        scope = kwargs.get("scope")
        step = kwargs.get("step")
        result = kwargs.get("semantic_result")
        if not isinstance(step, RunStep) or not isinstance(
            result,
            SemanticValidationResult,
        ):
            _fail(STEP_OUTPUT_MISSING)
        if not result.applicability_decisions:
            return
        aggregate = next(
            (
                row
                for row in self._step_roots
                if row[0] == "__step__" and row[1] == result.aggregate_receipt_kind
            ),
            None,
        )
        if aggregate is None:
            _fail(STEP_OUTPUT_MISSING)
        deciding_step_output_id = self._derive_step_output_id(
            scope=scope,
            step=step,
            subrole=aggregate[0],
            output_kind=aggregate[1],
            ordinal=aggregate[2],
            output_object_id=aggregate[3],
        )
        for decision in result.applicability_decisions:
            self._graph.insert_run_applicability_decision(
                self._applicability_record(
                    scope=scope,
                    step=step,
                    decision=decision,
                    deciding_step_output_id=deciding_step_output_id,
                )
            )

    def persist_new_outgoing_edges(self, **kwargs: object) -> None:
        scope = kwargs.get("scope")
        step = kwargs.get("step")
        if not isinstance(step, RunStep):
            _fail(STEP_OUTPUT_MISSING)
        params = _scope_params(scope)
        derived = {str(edge.expected_edge_id): edge for edge in self._derive_edges(scope)}
        persisted = {
            row.expected_edge_id: row
            for row in self._graph.list_run_expected_consumer_edges(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        for edge_id, stored in persisted.items():
            expected = derived.get(edge_id)
            if expected is None or not self._stored_edge_matches(stored, expected):
                _fail(CONSUMER_RECEIPT_INCOMPLETE)

        unlocked = self._current_step_unlocked_edge_ids(
            scope=scope,
            step=step,
            derived=tuple(derived.values()),
        )
        for edge_id, edge in derived.items():
            if edge_id in persisted:
                continue
            if edge_id not in unlocked:
                # A later boundary may not repair a missing admission or prior
                # producer edge; rehydration must surface the exact gap.
                _fail(CONSUMER_RECEIPT_INCOMPLETE)
            self._graph.insert_run_expected_consumer_edge(self._edge_record(edge, params))

    def persist_step_receipt_input_references(
        self,
        *,
        scope: object,
        step: RunStep,
        semantic_result: SemanticValidationResult,
    ) -> None:
        """Write one row-14 reference for each incoming STEP_RECEIPT edge.

        Incoming edges come from ``_derive_edges(scope)``, limited to this
        step's ``__step__`` consumer and ``step_receipt_input_references``.
        ``reference_ordinal`` is the producer's index in the canonical census
        ``input_refs`` array. The live lease is copied from the scope. Returns
        None.
        """

        if not isinstance(step, RunStep) or not isinstance(
            semantic_result, SemanticValidationResult
        ):
            _fail(STEP_OUTPUT_MISSING)
        params = _scope_params(scope)
        incoming = tuple(
            edge
            for edge in self._derive_edges(scope)
            if edge.consumer_step_name == step.step_name.value
            and edge.consumer_subrole == "__step__"
            and USAGE_ROUTE_REGISTRY[edge.route_ordinal].native_reference_relation
            == "step_receipt_input_references"
        )
        if not incoming:
            return
        output_roots = {
            row.output_object_id: row
            for row in self._graph.list_run_output_objects(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        consumers = tuple(
            row
            for row in self._graph.list_run_step_outputs(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
            if row.step_name == step.step_name.value
            and row.subrole == "__step__"
            and row.output_object_id in output_roots
            and output_roots[row.output_object_id].output_class == "STEP_RECEIPT"
        )
        if len(consumers) != 1:
            _fail(STEP_OUTPUT_MISSING)
        consumer = consumers[0]
        root = output_roots[consumer.output_object_id]
        planned = tuple(
            (
                edge,
                _census_reference_ordinal(
                    semantic_result.census.payload.get("input_refs"), edge.producer
                ),
            )
            for edge in incoming
        )
        if len({ordinal for _edge, ordinal in planned}) != len(planned):
            _fail(CONSUMER_RECEIPT_INCOMPLETE)
        for edge, ordinal in planned:
            self._insert_step_receipt_reference(
                params=params,
                edge=edge,
                ordinal=ordinal,
                step_output_id=consumer.step_output_id,
                consumer_output_object_id=root.output_object_id,
            )

    def _insert_step_receipt_reference(
        self,
        *,
        params: Mapping[str, object],
        edge: ExpectedConsumerEdge,
        ordinal: int,
        step_output_id: str,
        consumer_output_object_id: str,
    ) -> None:
        route = USAGE_ROUTE_REGISTRY[edge.route_ordinal]
        producer = edge.producer
        if producer.producer_type == "INPUT_BINDING":
            binding_id: str | None = str(producer.producer_id)
            output_id: str | None = None
        elif producer.producer_type == "OUTPUT_OBJECT":
            binding_id = None
            output_id = str(producer.producer_id)
        else:
            _fail(CONSUMER_RECEIPT_INCOMPLETE)
        try:
            reference_id = derive_task7_uuid(
                id_kind="future_input_reference_id",
                values={
                    "tenant_id": params["tenant_id"],
                    "deal_id": params["deal_id"],
                    "run_id": params["run_id"],
                    "reference_table": "step_receipt_input_references",
                    "consumer_id": step_output_id,
                    "consumer_output_object_id": consumer_output_object_id,
                    "producer_type": producer.producer_type,
                    "producer_id": str(producer.producer_id),
                    "reference_kind": route.reference_kind,
                    "reference_ordinal": ordinal,
                    "producer_identity_sha256": producer.producer_identity_sha256,
                    "producer_value_sha256": producer.producer_value_sha256,
                },
            ).value
        except Task7UuidError as exc:
            raise Task7PostgresPortError(PIPELINE_CONTRACT_UNAVAILABLE) from exc
        self._execute(
            text(
                """
                INSERT INTO step_receipt_input_references (
                    receipt_reference_id, tenant_id, deal_id, run_id,
                    step_output_id, consumer_output_object_id, producer_type,
                    producer_input_binding_id, producer_output_object_id,
                    producer_identity_sha256, producer_value_sha256,
                    reference_kind, reference_ordinal, lease_owner_id,
                    lease_fencing_token
                ) VALUES (
                    CAST(:receipt_reference_id AS uuid),
                    CAST(:tenant_id AS uuid), CAST(:deal_id AS uuid),
                    CAST(:run_id AS uuid), CAST(:step_output_id AS uuid),
                    CAST(:consumer_output_object_id AS uuid), :producer_type,
                    CAST(:producer_input_binding_id AS uuid),
                    CAST(:producer_output_object_id AS uuid),
                    :producer_identity_sha256, :producer_value_sha256,
                    :reference_kind, :reference_ordinal,
                    CAST(:lease_owner_id AS uuid), :lease_fencing_token
                )
                """
            ),
            {
                **params,
                "receipt_reference_id": str(reference_id),
                "step_output_id": step_output_id,
                "consumer_output_object_id": consumer_output_object_id,
                "producer_type": producer.producer_type,
                "producer_input_binding_id": binding_id,
                "producer_output_object_id": output_id,
                "producer_identity_sha256": producer.producer_identity_sha256,
                "producer_value_sha256": producer.producer_value_sha256,
                "reference_kind": route.reference_kind,
                "reference_ordinal": ordinal,
            },
        )

    def derive_and_persist_usage_receipts(self, **kwargs: object) -> None:
        scope = kwargs.get("scope")
        step = kwargs.get("step")
        if not isinstance(step, RunStep):
            _fail(STEP_OUTPUT_MISSING)
        params = _scope_params(scope)
        edges = tuple(
            edge
            for edge in self._derive_edges(scope)
            if edge.consumer_step_name == step.step_name.value
        )
        if not edges:
            return
        step_outputs = self._graph.list_run_step_outputs(
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
        )
        output_roots = {
            row.output_object_id: row
            for row in self._graph.list_run_output_objects(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        for edge in edges:
            consumer = next(
                (
                    row
                    for row in step_outputs
                    if row.step_name == edge.consumer_step_name
                    and row.subrole == edge.consumer_subrole
                    and output_roots[row.output_object_id].output_class == "STEP_RECEIPT"
                ),
                None,
            )
            if consumer is None:
                _fail(CONSUMER_RECEIPT_INCOMPLETE)
            root = output_roots[consumer.output_object_id]
            route = USAGE_ROUTE_REGISTRY[edge.route_ordinal]
            occurrences = self._load_usage_occurrences(
                scope=scope,
                edge=edge,
                consumer_output_object_id=root.output_object_id,
                consumer_identity_sha256=root.native_identity_sha256,
                route_ordinal=edge.route_ordinal,
                consumer_step_output_id=consumer.step_output_id,
            )
            evidence = extract_usage_evidence(
                UsageExtractionRequest(
                    tenant_id=UUID(cast(str, params["tenant_id"])),
                    deal_id=UUID(cast(str, params["deal_id"])),
                    run_id=UUID(cast(str, params["run_id"])),
                    pipeline_contract_version=cast(str, params["pipeline_contract_version"]),
                    pipeline_contract_hash=cast(str, params["pipeline_contract_hash"]),
                    expected_edge_id=edge.expected_edge_id,
                    edge_tuple_sha256=edge.edge_tuple_sha256,
                    route_ordinal=edge.route_ordinal,
                    usage_kind=edge.usage_kind,
                    consumer_step_name=edge.consumer_step_name,
                    consumer_subrole=edge.consumer_subrole,
                    producer=edge.producer,
                    consumer_receipt=ConsumerReceiptRootV1(
                        step_output_id=UUID(consumer.step_output_id),
                        output_object_id=UUID(root.output_object_id),
                        native_identity_sha256=root.native_identity_sha256,
                        native_output_sha256=root.native_output_sha256,
                    ),
                    native_reference_relation=route.native_reference_relation,
                    consumer_fk=route.consumer_fk,
                    reference_kind=route.reference_kind,
                    occurrences=occurrences,
                )
            )
            receipt_id = derive_task7_uuid(
                id_kind="receipt_id",
                values={
                    "tenant_id": params["tenant_id"],
                    "deal_id": params["deal_id"],
                    "run_id": params["run_id"],
                    "pipeline_contract_hash": params["pipeline_contract_hash"],
                    "expected_edge_id": str(edge.expected_edge_id),
                    "consumer_step_output_id": consumer.step_output_id,
                    "consumer_output_object_id": root.output_object_id,
                    "usage_evidence_sha256": (evidence.usage_evidence_sha256),
                },
            ).value
            consumed_at = self._execute(
                text("SELECT statement_timestamp() AS consumed_at"),
                {},
            ).scalar_one()
            self._graph.insert_run_consumer_receipt(
                RunConsumerReceiptRecord(
                    receipt_id=str(receipt_id),
                    tenant_id=cast(str, params["tenant_id"]),
                    deal_id=cast(str, params["deal_id"]),
                    run_id=cast(str, params["run_id"]),
                    expected_edge_id=str(edge.expected_edge_id),
                    consumer_step_output_id=consumer.step_output_id,
                    consumer_output_object_id=root.output_object_id,
                    consumer_step_name=edge.consumer_step_name,
                    consumer_subrole=edge.consumer_subrole,
                    usage_evidence_sha256=evidence.usage_evidence_sha256,
                    consumed_at=consumed_at,
                    lease_owner_id=cast(str, params["lease_owner_id"]),
                    lease_fencing_token=cast(int, params["lease_fencing_token"]),
                )
            )

    def prove_incoming_receipt_equality(self, **kwargs: object) -> bool:
        scope = kwargs.get("scope")
        step = kwargs.get("step")
        if not isinstance(step, RunStep):
            _fail(STEP_REHYDRATION_FAILED)
        expected = {
            str(row.expected_edge_id)
            for row in self._derive_edges(scope)
            if row.consumer_step_name == step.step_name.value
        }
        params = _scope_params(scope)
        actual = {
            row.expected_edge_id
            for row in self._graph.list_run_consumer_receipts(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
            if row.consumer_step_name == step.step_name.value
        }
        return expected == actual

    def mark_step_completed(self, **kwargs: object) -> bool:
        params = _scope_params(kwargs.get("scope"))
        params.update(
            step_id=kwargs.get("step_id"),
            step_name=kwargs.get("step_name"),
            result_summary=json.dumps(
                kwargs.get("result_summary"),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        result = self._execute(
            text(
                """
                UPDATE run_steps AS s
                SET status = 'COMPLETED',
                    finished_at = statement_timestamp(),
                    error_code = NULL,
                    error_message = NULL,
                    result_summary = CAST(:result_summary AS jsonb)
                FROM runs AS r
                WHERE s.tenant_id = CAST(:tenant_id AS uuid)
                  AND s.run_id = CAST(:run_id AS uuid)
                  AND s.step_id = CAST(:step_id AS uuid)
                  AND s.step_name = :step_name
                  AND s.status = 'RUNNING'
                  AND r.tenant_id = s.tenant_id
                  AND r.run_id = s.run_id
                  AND r.deal_id = CAST(:deal_id AS uuid)
                  AND r.status = 'RUNNING'
                  AND r.lease_owner_id =
                      CAST(:lease_owner_id AS uuid)
                  AND r.lease_fencing_token =
                      :lease_fencing_token
                  AND r.lease_expires_at > statement_timestamp()
                RETURNING s.step_id
                """
            ),
            params,
        ).scalar_one_or_none()
        return result is not None

    def append_step_completion_audit(self, **kwargs: object) -> None:
        scope = kwargs.get("scope")
        params = _scope_params(scope)
        self._emit_audit(
            event_type="deal.run.step.completed",
            scope=scope,
            safe={
                "step_id": kwargs.get("step_id"),
                "step_name": kwargs.get("step_name"),
                "lease_fencing_token": params["lease_fencing_token"],
            },
        )

    def load_resumable_run(
        self,
        **kwargs: object,
    ) -> Mapping[str, object]:
        return self._load_run(kwargs.get("scope"), for_update=False)

    def list_canonical_steps(self, **kwargs: object) -> Sequence[RunStep]:
        params = _scope_params(kwargs.get("scope"))
        rows = self._execute(text(_CANONICAL_STEPS_SQL), params).fetchall()
        return tuple(_step_from_row(row) for row in rows)

    def validate_completed_step_graph(self, **kwargs: object) -> bool:
        scope = kwargs.get("scope")
        step = kwargs.get("step")
        if not isinstance(step, RunStep) or step.status is not StepStatus.COMPLETED:
            return False
        try:
            self._rederive_completed_step_graph(scope=scope, step=step)
            self._rederive_usage_evidence_for_step(scope=scope, step=step)
        except Task7PostgresPortError:
            return False
        return True

    def _rederive_completed_step_graph(
        self,
        *,
        scope: object,
        step: RunStep,
    ) -> None:
        params = _scope_params(scope)
        spec = STEP_SEMANTIC_REGISTRY.get(step.step_name.value)
        if spec is None:
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
        all_outputs = self._graph.list_run_step_outputs(
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
        )
        roots = {
            row.output_object_id: row
            for row in self._graph.list_run_output_objects(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        step_rows = tuple(row for row in all_outputs if row.step_id == step.step_id)
        expected_coordinates = {
            ("__step__", output_kind, ordinal, "NATIVE_ROW")
            for ordinal, output_kind in enumerate(sorted(spec.native_output_kinds))
        }
        expected_coordinates.update(
            (
                subrole,
                spec.aggregate_receipt_kind,
                0,
                "STEP_RECEIPT",
            )
            for subrole in spec.mandatory_subroles
        )
        expected_coordinates.add(
            (
                "__step__",
                spec.aggregate_receipt_kind,
                0,
                "STEP_RECEIPT",
            )
        )
        actual_coordinates: set[tuple[str, str, int, str]] = set()
        for row in step_rows:
            root = roots.get(row.output_object_id)
            if (
                root is None
                or row.step_name != step.step_name.value
                or row.semantic_rule_version != spec.semantic_rule_version
                or row.semantic_rule_hash != spec.semantic_rule_hash
                or root.output_kind != row.output_kind
                or root.output_schema_version != spec.semantic_rule_version
                or root.pipeline_contract_version != cast(str, params["pipeline_contract_version"])
                or root.pipeline_contract_hash != cast(str, params["pipeline_contract_hash"])
                or root.write_origin != "RUNTIME"
                or _HASH_RE.fullmatch(root.native_identity_sha256) is None
                or _HASH_RE.fullmatch(root.native_output_sha256) is None
                or _HASH_RE.fullmatch(row.summary_sha256) is None
            ):
                _fail(STEP_REHYDRATION_FAILED)
            expected_root_id = self._derive_output_id(
                scope=scope,
                output_class=root.output_class,
                output_kind=root.output_kind,
                identity_sha256=root.native_identity_sha256,
                value_sha256=root.native_output_sha256,
            )
            expected_step_output_id = self._derive_step_output_id(
                scope=scope,
                step=step,
                subrole=row.subrole,
                output_kind=row.output_kind,
                ordinal=row.output_ordinal,
                output_object_id=row.output_object_id,
            )
            if (
                str(expected_root_id) != root.output_object_id
                or str(expected_step_output_id) != row.step_output_id
            ):
                _fail(STEP_REHYDRATION_FAILED)
            coordinate = (
                row.subrole,
                row.output_kind,
                row.output_ordinal,
                root.output_class,
            )
            if coordinate in actual_coordinates:
                _fail(STEP_REHYDRATION_FAILED)
            actual_coordinates.add(coordinate)
        if actual_coordinates != expected_coordinates:
            _fail(STEP_REHYDRATION_FAILED)
        self.corroborate_persisted_native_outputs(
            scope=scope,
            step=step,
        )

        derived = {str(edge.expected_edge_id): edge for edge in self._derive_edges(scope)}
        persisted_rows = self._graph.list_run_expected_consumer_edges(
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
        )
        persisted = {row.expected_edge_id: row for row in persisted_rows}
        if len(persisted) != len(persisted_rows):
            _fail(CONSUMER_RECEIPT_INCOMPLETE)
        for edge_id, stored in persisted.items():
            expected = derived.get(edge_id)
            if expected is None or not self._stored_edge_matches(stored, expected):
                _fail(CONSUMER_RECEIPT_INCOMPLETE)

        current_output_ids = frozenset(row.output_object_id for row in step_rows)
        current_step_output_ids = frozenset(row.step_output_id for row in step_rows)
        current_decision_ids = frozenset(
            row.applicability_decision_id
            for row in self._graph.list_run_applicability_decisions(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
            if row.deciding_step_output_id in current_step_output_ids
        )
        expected_outgoing = {
            edge_id
            for edge_id, edge in derived.items()
            if (
                edge.producer.producer_type == "OUTPUT_OBJECT"
                and str(edge.producer.producer_id) in current_output_ids
            )
            or (
                edge.applicability_decision_id is not None
                and str(edge.applicability_decision_id) in current_decision_ids
            )
        }
        actual_outgoing = {
            edge_id
            for edge_id, row in persisted.items()
            if row.producer_output_object_id in current_output_ids
            or (
                row.applicability_decision_id is not None
                and row.applicability_decision_id in current_decision_ids
            )
        }
        if actual_outgoing != expected_outgoing:
            _fail(CONSUMER_RECEIPT_INCOMPLETE)

    def _rederive_usage_evidence_for_step(
        self,
        *,
        scope: object,
        step: RunStep,
    ) -> None:
        params = _scope_params(scope)
        derived = {str(edge.expected_edge_id): edge for edge in self._derive_edges(scope)}
        incoming = {
            edge_id: edge
            for edge_id, edge in derived.items()
            if edge.consumer_step_name == step.step_name.value
        }
        receipts = {
            row.expected_edge_id: row
            for row in self._graph.list_run_consumer_receipts(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
            if row.consumer_step_name == step.step_name.value
        }
        if set(receipts) != set(incoming):
            _fail(CONSUMER_RECEIPT_INCOMPLETE)
        rederived_usage = self.derive_usage_evidence_map(scope=scope)
        for edge_id, edge in incoming.items():
            receipt = receipts[edge_id]
            if rederived_usage.get(edge.edge_tuple_sha256) != receipt.usage_evidence_sha256:
                _fail(CONSUMER_RECEIPT_INCOMPLETE)

    def lock_run_for_finalization(
        self,
        **kwargs: object,
    ) -> Mapping[str, object]:
        return self._lock_run(kwargs.get("scope"))

    def derive_contract_edge_map(
        self,
        **kwargs: object,
    ) -> Mapping[str, object]:
        return {
            row.edge_tuple_sha256: self._edge_projection(row)
            for row in self._derive_edges(kwargs.get("scope"))
        }

    def load_persisted_edge_map(
        self,
        **kwargs: object,
    ) -> Mapping[str, object]:
        scope = kwargs.get("scope")
        params = _scope_params(scope)
        derived = {str(row.expected_edge_id): row for row in self._derive_edges(scope)}
        result: dict[str, object] = {}
        for stored in self._graph.list_run_expected_consumer_edges(
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
        ):
            expected = derived.get(stored.expected_edge_id)
            if expected is None or not self._stored_edge_matches(stored, expected):
                _fail(CONSUMER_RECEIPT_INCOMPLETE)
            result[expected.edge_tuple_sha256] = self._edge_projection(expected)
        return result

    def derive_receipt_edge_map(
        self,
        **kwargs: object,
    ) -> Mapping[str, object]:
        scope = kwargs.get("scope")
        params = _scope_params(scope)
        persisted = self.load_persisted_edge_map(scope=scope)
        edge_key_by_id = {
            row.expected_edge_id: row.edge_tuple_sha256
            for row in self._graph.list_run_expected_consumer_edges(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        result: dict[str, object] = {}
        for receipt in self._graph.list_run_consumer_receipts(
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
        ):
            key = edge_key_by_id.get(receipt.expected_edge_id)
            if key is None or key not in persisted:
                _fail(CONSUMER_RECEIPT_INCOMPLETE)
            projection = dict(cast(Mapping[str, object], persisted[key]))
            projection["usage_evidence_sha256"] = receipt.usage_evidence_sha256
            result[key] = projection
        return result

    def derive_usage_evidence_map(
        self,
        **kwargs: object,
    ) -> Mapping[str, str]:
        scope = kwargs.get("scope")
        params = _scope_params(scope)
        derived_by_id = {str(row.expected_edge_id): row for row in self._derive_edges(scope)}
        step_outputs = {
            row.step_output_id: row
            for row in self._graph.list_run_step_outputs(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        output_roots = {
            row.output_object_id: row
            for row in self._graph.list_run_output_objects(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        result: dict[str, str] = {}
        for receipt in self._graph.list_run_consumer_receipts(
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
        ):
            edge = derived_by_id.get(receipt.expected_edge_id)
            consumer = step_outputs.get(receipt.consumer_step_output_id)
            root = output_roots.get(receipt.consumer_output_object_id)
            if (
                edge is None
                or consumer is None
                or root is None
                or consumer.output_object_id != root.output_object_id
                or consumer.step_name != edge.consumer_step_name
                or consumer.subrole != edge.consumer_subrole
                or receipt.consumer_step_name != edge.consumer_step_name
                or receipt.consumer_subrole != edge.consumer_subrole
            ):
                _fail(CONSUMER_RECEIPT_INCOMPLETE)
            route = USAGE_ROUTE_REGISTRY[edge.route_ordinal]
            occurrences = self._load_usage_occurrences(
                scope=scope,
                edge=edge,
                consumer_output_object_id=root.output_object_id,
                consumer_identity_sha256=root.native_identity_sha256,
                route_ordinal=edge.route_ordinal,
                consumer_step_output_id=consumer.step_output_id,
            )
            evidence = extract_usage_evidence(
                UsageExtractionRequest(
                    tenant_id=UUID(cast(str, params["tenant_id"])),
                    deal_id=UUID(cast(str, params["deal_id"])),
                    run_id=UUID(cast(str, params["run_id"])),
                    pipeline_contract_version=cast(str, params["pipeline_contract_version"]),
                    pipeline_contract_hash=cast(str, params["pipeline_contract_hash"]),
                    expected_edge_id=edge.expected_edge_id,
                    edge_tuple_sha256=edge.edge_tuple_sha256,
                    route_ordinal=edge.route_ordinal,
                    usage_kind=edge.usage_kind,
                    consumer_step_name=edge.consumer_step_name,
                    consumer_subrole=edge.consumer_subrole,
                    producer=edge.producer,
                    consumer_receipt=ConsumerReceiptRootV1(
                        step_output_id=UUID(consumer.step_output_id),
                        output_object_id=UUID(root.output_object_id),
                        native_identity_sha256=root.native_identity_sha256,
                        native_output_sha256=root.native_output_sha256,
                    ),
                    native_reference_relation=route.native_reference_relation,
                    consumer_fk=route.consumer_fk,
                    reference_kind=route.reference_kind,
                    occurrences=occurrences,
                )
            )
            result[edge.edge_tuple_sha256] = evidence.usage_evidence_sha256
        return result

    def load_contract_cardinality_equations(
        self,
        **kwargs: object,
    ) -> Mapping[str, int]:
        return self._independent_producer_census(kwargs.get("scope"))

    def validate_native_and_subrole_roots(self, **kwargs: object) -> None:
        scope = kwargs.get("scope")
        params = _scope_params(scope)
        rows = self._graph.list_run_step_outputs(
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
        )
        roots = {
            row.output_object_id: row
            for row in self._graph.list_run_output_objects(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        if not rows or any(row.output_object_id not in roots for row in rows):
            _fail(STEP_OUTPUT_MISSING)
        completed = self.list_canonical_steps(scope=scope)
        for step in completed:
            if not self.validate_completed_step_graph(
                scope=scope,
                step=step,
            ):
                _fail(STEP_REHYDRATION_FAILED)

    def mark_run_succeeded(self, **kwargs: object) -> bool:
        params = {
            key: kwargs.get(key)
            for key in (
                "tenant_id",
                "deal_id",
                "run_id",
                "lease_owner_id",
                "lease_fencing_token",
                "status",
            )
        }
        result = self._execute(
            text(
                """
                UPDATE runs
                SET status = :status,
                    finished_at = statement_timestamp(),
                    lease_owner_id = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = statement_timestamp()
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND status = 'RUNNING'
                  AND pipeline_contract_version = 'full-strict-v21'
                  AND pipeline_contract_hash =
                      '0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178'
                  AND pipeline_resume_state = 'RESUMABLE'
                  AND lease_owner_id =
                      CAST(:lease_owner_id AS uuid)
                  AND lease_fencing_token = :lease_fencing_token
                  AND lease_expires_at > statement_timestamp()
                RETURNING run_id
                """
            ),
            params,
        ).scalar_one_or_none()
        return result is not None

    def append_run_finalization_audit(self, **kwargs: object) -> None:
        self._emit_audit(
            event_type="deal.run.finalized",
            scope=kwargs.get("scope"),
            safe={
                "status": kwargs.get("status"),
                "edge_count": kwargs.get("edge_count"),
                "receipt_count": kwargs.get("receipt_count"),
            },
        )

    def _lock_run(self, scope: object) -> Mapping[str, object]:
        params = _scope_params(scope)
        row = self._execute(text(_RUN_LOCK_SQL), params).one_or_none()
        return _stringify_uuid_fields(_mapping(row, code=WORKER_LEASE_FENCED))

    def _load_run(
        self,
        scope: object,
        *,
        for_update: bool,
    ) -> Mapping[str, object]:
        params = _scope_params(scope)
        suffix = " FOR UPDATE" if for_update else ""
        row = self._execute(
            text(
                """
                SELECT tenant_id, deal_id, run_id, status,
                       pipeline_contract_version,
                       pipeline_contract_hash, pipeline_resume_state,
                       lease_owner_id, lease_fencing_token,
                       lease_expires_at,
                       (lease_expires_at > statement_timestamp())
                           AS lease_is_live
                FROM runs
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                """
                + suffix
            ),
            params,
        ).one_or_none()
        return _stringify_uuid_fields(_mapping(row, code=STEP_REHYDRATION_FAILED))

    def _insert_output_root(
        self,
        *,
        scope: object,
        output_object_id: UUID,
        output_class: str,
        output_kind: str,
        schema_version: str,
        identity_sha256: str,
        value_sha256: str,
    ) -> None:
        params = _scope_params(scope)
        self._graph.insert_run_output_object(
            RunOutputObjectRecord(
                output_object_id=str(output_object_id),
                tenant_id=cast(str, params["tenant_id"]),
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
                output_class=output_class,
                output_kind=output_kind,
                output_schema_version=schema_version,
                native_identity_sha256=identity_sha256,
                native_output_sha256=value_sha256,
                pipeline_contract_version=cast(str, params["pipeline_contract_version"]),
                pipeline_contract_hash=cast(str, params["pipeline_contract_hash"]),
                write_origin="RUNTIME",
                lease_owner_id=cast(str, params["lease_owner_id"]),
                lease_fencing_token=cast(int, params["lease_fencing_token"]),
            )
        )

    def _derive_output_id(
        self,
        *,
        scope: object,
        output_class: str,
        output_kind: str,
        identity_sha256: str,
        value_sha256: str,
    ) -> UUID:
        params = _scope_params(scope)
        try:
            return derive_task7_uuid(
                id_kind="output_object_id",
                values={
                    "tenant_id": params["tenant_id"],
                    "deal_id": params["deal_id"],
                    "run_id": params["run_id"],
                    "pipeline_contract_version": params["pipeline_contract_version"],
                    "pipeline_contract_hash": params["pipeline_contract_hash"],
                    "output_class": output_class,
                    "output_kind": output_kind,
                    "native_identity_sha256": identity_sha256,
                    "native_output_sha256": value_sha256,
                },
            ).value
        except Task7UuidError as exc:
            raise Task7PostgresPortError(STEP_OUTPUT_MISSING) from exc

    def _derive_step_output_id(
        self,
        *,
        scope: object,
        step: RunStep,
        subrole: str,
        output_kind: str,
        ordinal: int,
        output_object_id: str,
    ) -> UUID:
        params = _scope_params(scope)
        try:
            return derive_task7_uuid(
                id_kind="step_output_id",
                values={
                    "tenant_id": params["tenant_id"],
                    "deal_id": params["deal_id"],
                    "run_id": params["run_id"],
                    "pipeline_contract_hash": params["pipeline_contract_hash"],
                    "step_id": step.step_id,
                    "step_name": step.step_name.value,
                    "subrole": subrole,
                    "output_kind": output_kind,
                    "output_ordinal": ordinal,
                    "output_object_id": output_object_id,
                },
            ).value
        except Task7UuidError as exc:
            raise Task7PostgresPortError(STEP_OUTPUT_MISSING) from exc

    def _applicability_record(
        self,
        *,
        scope: object,
        step: RunStep,
        decision: ApplicabilityDecisionEvidence,
        deciding_step_output_id: UUID,
    ) -> RunApplicabilityDecisionRecord:
        from idis.pipeline.task7_edges import EDGE_RULE_REGISTRY

        params = _scope_params(scope)
        producer = decision.upstream_producer
        roots = {
            row.output_object_id: row
            for row in self._graph.list_run_output_objects(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        upstream = roots.get(str(producer.producer_id))
        if (
            upstream is None
            or upstream.native_identity_sha256 != producer.producer_identity_sha256
            or upstream.native_output_sha256 != producer.producer_value_sha256
        ):
            _fail(STEP_OUTPUT_MISSING)

        evidence = decision.evidence
        if isinstance(evidence, Step5ApplicabilityEvidenceV1):
            if step.step_name is not StepName.METHODOLOGY_COVERAGE_INIT:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            decision_phase = "STEP_5"
            decision_kind = "METHODOLOGY_SUBJECT_APPLICABILITY"
            subject_kind = "COVERAGE_SUBJECT"
            subject_key = evidence.subject_key
            schema_id, schema_version, schema_sha256 = _STEP5_EVIDENCE_SCHEMA
            evidence_payload: dict[str, object] = {
                "coverage_record_id": evidence.coverage_record_id,
                "subject_key": evidence.subject_key,
                "methodology_type": evidence.methodology_type,
                "selection_hash": evidence.selection_hash,
                "question_semantic_hash": evidence.question_semantic_hash,
                "outcome": evidence.outcome,
                "basis_code": evidence.basis_code,
                "deciding_step_output_id": str(deciding_step_output_id),
            }
        elif isinstance(evidence, Step16ProviderApplicabilityEvidenceV1):
            if step.step_name is not StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            decision_phase = "STEP_16"
            decision_kind = "PROVIDER_APPLICABILITY"
            subject_kind = "ENRICHMENT_PROVIDER"
            subject_key = evidence.provider_id
            schema_id, schema_version, schema_sha256 = _STEP16_EVIDENCE_SCHEMA
            evidence_payload = {
                "provider_id": evidence.provider_id,
                "identity_output_ref": {
                    "producer_type": producer.producer_type,
                    "producer_id": str(producer.producer_id),
                    "producer_identity_sha256": producer.producer_identity_sha256,
                    "producer_value_sha256": producer.producer_value_sha256,
                },
                "listing_state": evidence.listing_state,
                "verified_identifier_kind": evidence.verified_identifier_kind,
                "verified_identifier_hash": evidence.verified_identifier_hash,
                "applicability_rule_id": evidence.applicability_rule_id,
                "applicability_rule_version": evidence.applicability_rule_version,
                "applicability_rule_hash": evidence.applicability_rule_hash,
                "outcome": evidence.outcome,
                "basis_code": evidence.basis_code,
                "deciding_step_output_id": str(deciding_step_output_id),
            }
        else:
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)

        rule = EDGE_RULE_REGISTRY.get(decision.contract_rule_id)
        if (
            rule is None
            or decision.contract_rule_version != rule.rule_version
            or decision.contract_rule_hash != rule.rule_sha256
        ):
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
        evidence_sha256 = _hash(
            [
                "applicability-evidence-v1",
                params["tenant_id"],
                params["deal_id"],
                params["run_id"],
                params["pipeline_contract_version"],
                params["pipeline_contract_hash"],
                decision_phase,
                decision_kind,
                subject_kind,
                subject_key,
                producer.producer_type,
                str(producer.producer_id),
                producer.producer_identity_sha256,
                producer.producer_value_sha256,
                schema_id,
                schema_version,
                schema_sha256,
                evidence_payload,
            ]
        )
        try:
            decision_id = derive_task7_uuid(
                id_kind="applicability_decision_id",
                values={
                    "tenant_id": params["tenant_id"],
                    "deal_id": params["deal_id"],
                    "run_id": params["run_id"],
                    "pipeline_contract_hash": params["pipeline_contract_hash"],
                    "decision_phase": decision_phase,
                    "decision_kind": decision_kind,
                    "subject_kind": subject_kind,
                    "subject_key": subject_key,
                    "outcome": evidence.outcome,
                    "upstream_type": producer.producer_type,
                    "upstream_id": str(producer.producer_id),
                    "deciding_step_output_id": str(deciding_step_output_id),
                    "contract_rule_id": rule.rule_id,
                    "contract_rule_version": rule.rule_version,
                    "contract_rule_hash": rule.rule_sha256,
                    "evidence_sha256": evidence_sha256,
                },
            ).value
        except Task7UuidError as exc:
            raise Task7PostgresPortError(PIPELINE_CONTRACT_HASH_MISMATCH) from exc
        decision_sha256 = _hash(
            [
                "applicability-decision-digest-v1",
                params["tenant_id"],
                params["deal_id"],
                params["run_id"],
                params["pipeline_contract_version"],
                params["pipeline_contract_hash"],
                decision_phase,
                decision_kind,
                subject_kind,
                subject_key,
                evidence.outcome,
                producer.producer_type,
                str(producer.producer_id),
                producer.producer_identity_sha256,
                producer.producer_value_sha256,
                str(deciding_step_output_id),
                rule.rule_id,
                rule.rule_version,
                rule.rule_sha256,
                evidence_sha256,
            ]
        )
        return RunApplicabilityDecisionRecord(
            applicability_decision_id=str(decision_id),
            tenant_id=cast(str, params["tenant_id"]),
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
            decision_phase=decision_phase,
            decision_kind=decision_kind,
            subject_kind=subject_kind,
            subject_key=subject_key,
            outcome=evidence.outcome,
            upstream_input_binding_id=None,
            upstream_output_object_id=str(producer.producer_id),
            deciding_step_output_id=str(deciding_step_output_id),
            contract_rule_id=rule.rule_id,
            contract_rule_version=rule.rule_version,
            contract_rule_hash=rule.rule_sha256,
            evidence_sha256=evidence_sha256,
            decision_sha256=decision_sha256,
            lease_owner_id=cast(str, params["lease_owner_id"]),
            lease_fencing_token=cast(int, params["lease_fencing_token"]),
        )

    def _derive_edges(
        self,
        scope: object,
    ) -> tuple[ExpectedConsumerEdge, ...]:
        params = _scope_params(scope)
        producers = derive_rule_bound_producers(
            ProducerSelectionRequest(
                tenant_id=UUID(cast(str, params["tenant_id"])),
                deal_id=UUID(cast(str, params["deal_id"])),
                run_id=UUID(cast(str, params["run_id"])),
            ),
            row_loader=self._selector_rows,
        )
        decisions = self._applicability_decisions(params)
        bound: list[ImmutableProducerFact] = []
        for producer in producers:
            subject_key = self._producer_applicability_subject(
                producer=producer,
                decisions=decisions,
            )
            if subject_key is not None:
                producer = replace(
                    producer,
                    applicability_subject_key=subject_key,
                )
            bound.append(producer)
        return derive_expected_edges(
            EdgeDerivationRequest(
                tenant_id=UUID(cast(str, params["tenant_id"])),
                deal_id=UUID(cast(str, params["deal_id"])),
                run_id=UUID(cast(str, params["run_id"])),
                pipeline_contract_version=cast(str, params["pipeline_contract_version"]),
                pipeline_contract_hash=cast(str, params["pipeline_contract_hash"]),
                producers=tuple(bound),
                applicability_decisions=tuple(decision for decision, _, _ in decisions),
            )
        )

    def _independent_producer_census(
        self,
        scope: object,
    ) -> dict[str, int]:
        from idis.pipeline.task7_edges import EDGE_RULE_REGISTRY

        params = _scope_params(scope)
        producers = derive_rule_bound_producers(
            ProducerSelectionRequest(
                tenant_id=UUID(cast(str, params["tenant_id"])),
                deal_id=UUID(cast(str, params["deal_id"])),
                run_id=UUID(cast(str, params["run_id"])),
            ),
            row_loader=self._selector_rows,
        )
        decisions = self._applicability_decisions(params)
        counts: Counter[str] = Counter()
        for producer in producers:
            rule = EDGE_RULE_REGISTRY.get(producer.rule_id)
            if rule is None:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            route_count = sum(
                1
                for route in USAGE_ROUTE_REGISTRY
                if route.rule_id == rule.rule_id
                and route.producer_condition == producer.producer_condition
            )
            if route_count <= 0:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            if rule.predicate_kind == "APPEND_ONLY_APPLICABILITY_DECISION":
                subject_key = self._producer_applicability_subject(
                    producer=producer,
                    decisions=decisions,
                )
                matches = tuple(
                    decision
                    for decision, _, _ in decisions
                    if decision.contract_rule_id == rule.rule_id
                    and decision.predicate_id == rule.predicate_id
                    and decision.subject_key == subject_key
                )
                if len(matches) != 1:
                    _fail(CONSUMER_RECEIPT_INCOMPLETE)
                if matches[0].outcome == "NOT_APPLICABLE":
                    continue
            elif rule.predicate_kind == "IMMUTABLE_PROVIDER_OUTCOME_FIELD":
                if producer.external_context_index_allowed is not True:
                    if producer.external_context_index_allowed is not False:
                        _fail(CONSUMER_RECEIPT_INCOMPLETE)
                    continue
            elif rule.predicate_kind != "UNCONDITIONAL":
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            counts[rule.rule_id] += route_count
        return dict(sorted(counts.items()))

    @staticmethod
    def _producer_applicability_subject(
        *,
        producer: ImmutableProducerFact,
        decisions: tuple[tuple[ApplicabilityDecision, str, UUID], ...],
    ) -> str | None:
        if producer.applicability_subject_key:
            return producer.applicability_subject_key
        for part in producer.producer_condition.split(";"):
            if part.startswith("provider_id="):
                provider_id = part.removeprefix("provider_id=")
                if provider_id:
                    return provider_id
        candidates = tuple(
            decision.subject_key
            for decision, producer_type, producer_id in decisions
            if decision.contract_rule_id == producer.rule_id
            and producer_type == producer.producer.producer_type
            and producer_id == producer.producer.producer_id
        )
        if len(set(candidates)) == 1:
            return candidates[0]
        rule_candidates = tuple(
            decision.subject_key
            for decision, _, _ in decisions
            if decision.contract_rule_id == producer.rule_id
        )
        if len(set(rule_candidates)) == 1:
            return rule_candidates[0]
        return None

    def _selector_rows(
        self,
        spec: ProducerSelectorSpec,
        **scope: object,
    ) -> Sequence[Mapping[str, object]]:
        absent = tuple(
            relation for relation in spec.relations if not self._relation_exists(relation)
        )
        if absent:
            if not self._tolerates_absent_future_relations(spec, absent):
                self._require_relation(absent[0])
            return ()
        sql, params = self._compile_selector(spec, scope)
        rows = self._execute(text(sql), params).fetchall()
        return tuple(_mapping(row, code=STEP_OUTPUT_MISSING) for row in rows)

    def _tolerates_absent_future_relations(
        self,
        spec: ProducerSelectorSpec,
        absent: tuple[str, ...],
    ) -> bool:
        """Tolerate absence only for the reviewed future set on dev 0039.

        All three must hold: every absent relation is in
        ``_REVIEWED_FUTURE_RELATIONS``; ``alembic_version`` is exactly
        ``['0039']`` and row 14 ``step_receipt_input_references`` is present;
        every relation of this selector that is present is one of the 64
        tables in ``_CATALOG_0038_PROFILE_TABLES``.
        """

        if not self._is_dev_0039_catalog():
            return False
        if any(relation not in _REVIEWED_FUTURE_RELATIONS for relation in absent):
            return False
        present = tuple(relation for relation in spec.relations if relation not in absent)
        return all(relation in _CATALOG_0038_PROFILE_TABLES for relation in present)

    def _is_dev_0039_catalog(self) -> bool:
        versions = [
            str(value)
            for value in self._execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num"),
                {},
            ).scalars()
        ]
        if versions != [_DEV_0039_REVISION]:
            return False
        row_14 = self._execute(
            text("SELECT to_regclass(:relation)"),
            {"relation": f"public.{_ROW_14_RELATION}"},
        ).scalar_one_or_none()
        return row_14 is not None

    def _compile_selector(
        self,
        spec: ProducerSelectorSpec,
        scope: Mapping[str, object],
    ) -> tuple[str, dict[str, object]]:
        columns = {relation: self._relation_columns(relation) for relation in spec.relations}
        aliases = {relation: f"t{ordinal}" for ordinal, relation in enumerate(spec.relations)}

        def resolve(identifier: str) -> str:
            if "." in identifier:
                relation, column = identifier.split(".", 1)
                if relation not in columns or column not in columns[relation]:
                    _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
                return f"{aliases[relation]}.{column}"
            owners = [relation for relation in spec.relations if identifier in columns[relation]]
            if not owners:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            return f"{aliases[owners[0]]}.{identifier}"

        from_sql = f"{spec.relations[0]} AS {aliases[spec.relations[0]]}"
        joined: set[str] = {spec.relations[0]}
        for left, right in spec.joins:
            left_relation = left.split(".", 1)[0]
            right_relation = right.split(".", 1)[0]
            if left_relation in joined and right_relation not in joined:
                relation = right_relation
            elif right_relation in joined and left_relation not in joined:
                relation = left_relation
            else:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            from_sql += (
                f" JOIN {relation} AS {aliases[relation]} ON {resolve(left)} = {resolve(right)}"
            )
            joined.add(relation)
        if joined != set(spec.relations):
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)

        bind: dict[str, object] = {}
        bind_ordinal = 0

        def predicate(node: object) -> str:
            nonlocal bind_ordinal
            if not isinstance(node, tuple) or not node:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            operator = node[0]
            if operator == "AND" and len(node) > 1:
                return "(" + " AND ".join(predicate(child) for child in node[1:]) + ")"
            if operator in {"IS_NULL", "IS_NOT_NULL"} and len(node) == 2:
                suffix = "IS NULL" if operator == "IS_NULL" else "IS NOT NULL"
                return f"{resolve(cast(str, node[1]))} {suffix}"
            if operator == "EQ" and len(node) == 3:
                column = resolve(cast(str, node[1]))
                value = node[2]
                if isinstance(value, str) and value.startswith(":"):
                    key = value[1:]
                    if key not in scope:
                        _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
                    bind[key] = scope[key]
                    return f"{column} = :{key}"
                key = f"selector_value_{bind_ordinal}"
                bind_ordinal += 1
                bind[key] = value
                return f"{column} = :{key}"
            if operator == "IN_ORDERED" and len(node) == 3:
                values = node[2]
                if not isinstance(values, tuple) or not values:
                    _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
                names: list[str] = []
                for value in values:
                    key = f"selector_value_{bind_ordinal}"
                    bind_ordinal += 1
                    bind[key] = value
                    names.append(f":{key}")
                return f"{resolve(cast(str, node[1]))} IN ({', '.join(names)})"
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)

        # projection[0] is the producer-type literal, not a table column.
        projected_columns = spec.projection[1:]
        if not projected_columns:
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
        projection = ", ".join(f"{resolve(column)} AS {column}" for column in projected_columns)
        order = ", ".join(f"{resolve(column)} ASC" for column in spec.order_by)
        sql = (
            f"SELECT {projection} FROM {from_sql} "
            f"WHERE {predicate(spec.predicate_ast)} ORDER BY {order}"
        )
        return sql, bind

    def _relation_columns(self, relation: str) -> frozenset[str]:
        if _IDENTIFIER_RE.fullmatch(relation) is None:
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
        self._require_relation(relation)
        rows = self._execute(
            text(
                """
                SELECT a.attname
                FROM pg_catalog.pg_attribute AS a
                WHERE a.attrelid = to_regclass(:relation)
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                ORDER BY a.attnum
                """
            ),
            {"relation": f"public.{relation}"},
        ).scalars()
        columns = frozenset(str(value) for value in rows)
        if not columns:
            _fail(PIPELINE_CONTRACT_UNAVAILABLE)
        return columns

    def _primary_key_columns(self, relation: str) -> tuple[str, ...]:
        if _IDENTIFIER_RE.fullmatch(relation) is None:
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
        rows = self._execute(
            text(
                """
                SELECT a.attname
                FROM pg_catalog.pg_index AS i
                JOIN pg_catalog.pg_attribute AS a
                  ON a.attrelid = i.indrelid
                 AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = to_regclass(:relation)
                  AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
                """
            ),
            {"relation": f"public.{relation}"},
        ).scalars()
        columns = tuple(str(value) for value in rows)
        if (
            not columns
            or len(set(columns)) != len(columns)
            or any(_IDENTIFIER_RE.fullmatch(column) is None for column in columns)
        ):
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
        return columns

    def _relation_exists(self, relation: str) -> bool:
        if _IDENTIFIER_RE.fullmatch(relation) is None:
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
        exists = self._execute(
            text("SELECT to_regclass(:relation)"),
            {"relation": f"public.{relation}"},
        ).scalar_one_or_none()
        return exists is not None

    def _require_relation(self, relation: str) -> None:
        if not self._relation_exists(relation):
            _fail(PIPELINE_CONTRACT_UNAVAILABLE)

    def _applicability_decisions(
        self,
        params: Mapping[str, object],
    ) -> tuple[tuple[ApplicabilityDecision, str, UUID], ...]:
        rows = self._graph.list_run_applicability_decisions(
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
        )
        result: list[tuple[ApplicabilityDecision, str, UUID]] = []
        from idis.pipeline.task7_edges import EDGE_RULE_REGISTRY

        roots = {
            row.output_object_id: row
            for row in self._graph.list_run_output_objects(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        step_outputs = {
            row.step_output_id: row
            for row in self._graph.list_run_step_outputs(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
        }
        seen: set[tuple[str, str]] = set()
        for row in rows:
            rule = EDGE_RULE_REGISTRY.get(row.contract_rule_id)
            if (
                rule is None
                or row.contract_rule_version != rule.rule_version
                or row.contract_rule_hash != rule.rule_sha256
                or rule.predicate_kind != "APPEND_ONLY_APPLICABILITY_DECISION"
                or row.outcome not in {"APPLICABLE", "NOT_APPLICABLE"}
                or _HASH_RE.fullmatch(row.evidence_sha256) is None
            ):
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            producer_type = (
                "INPUT_BINDING" if row.upstream_input_binding_id is not None else "OUTPUT_OBJECT"
            )
            if producer_type != "OUTPUT_OBJECT":
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            producer_id = UUID(
                row.upstream_input_binding_id or cast(str, row.upstream_output_object_id)
            )
            upstream = roots.get(str(producer_id))
            deciding = step_outputs.get(row.deciding_step_output_id)
            if (
                upstream is None
                or deciding is None
                or deciding.subrole != "__step__"
                or roots.get(deciding.output_object_id) is None
                or roots[deciding.output_object_id].output_class != "STEP_RECEIPT"
            ):
                _fail(STEP_REHYDRATION_FAILED)
            if row.decision_phase == "STEP_5":
                expected_step = StepName.METHODOLOGY_COVERAGE_INIT.value
                expected_kind = "METHODOLOGY_SUBJECT_APPLICABILITY"
                expected_subject_kind = "COVERAGE_SUBJECT"
                expected_predicate = "STEP5_APPLICABLE"
            elif row.decision_phase == "STEP_16":
                expected_step = StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE.value
                expected_kind = "PROVIDER_APPLICABILITY"
                expected_subject_kind = "ENRICHMENT_PROVIDER"
                expected_predicate = "STEP16_PROVIDER_APPLICABLE"
            else:
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            if (
                deciding.step_name != expected_step
                or row.decision_kind != expected_kind
                or row.subject_kind != expected_subject_kind
                or rule.predicate_id != expected_predicate
            ):
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            expected_decision_sha256 = _hash(
                [
                    "applicability-decision-digest-v1",
                    params["tenant_id"],
                    params["deal_id"],
                    params["run_id"],
                    params["pipeline_contract_version"],
                    params["pipeline_contract_hash"],
                    row.decision_phase,
                    row.decision_kind,
                    row.subject_kind,
                    row.subject_key,
                    row.outcome,
                    producer_type,
                    str(producer_id),
                    upstream.native_identity_sha256,
                    upstream.native_output_sha256,
                    row.deciding_step_output_id,
                    rule.rule_id,
                    rule.rule_version,
                    rule.rule_sha256,
                    row.evidence_sha256,
                ]
            )
            try:
                expected_id = derive_task7_uuid(
                    id_kind="applicability_decision_id",
                    values={
                        "tenant_id": params["tenant_id"],
                        "deal_id": params["deal_id"],
                        "run_id": params["run_id"],
                        "pipeline_contract_hash": params["pipeline_contract_hash"],
                        "decision_phase": row.decision_phase,
                        "decision_kind": row.decision_kind,
                        "subject_kind": row.subject_kind,
                        "subject_key": row.subject_key,
                        "outcome": row.outcome,
                        "upstream_type": producer_type,
                        "upstream_id": str(producer_id),
                        "deciding_step_output_id": (row.deciding_step_output_id),
                        "contract_rule_id": rule.rule_id,
                        "contract_rule_version": rule.rule_version,
                        "contract_rule_hash": rule.rule_sha256,
                        "evidence_sha256": row.evidence_sha256,
                    },
                ).value
            except Task7UuidError as exc:
                raise Task7PostgresPortError(PIPELINE_CONTRACT_HASH_MISMATCH) from exc
            unique_key = (row.contract_rule_id, row.subject_key)
            if (
                row.decision_sha256 != expected_decision_sha256
                or row.applicability_decision_id != str(expected_id)
                or unique_key in seen
            ):
                _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
            seen.add(unique_key)
            result.append(
                (
                    ApplicabilityDecision(
                        applicability_decision_id=UUID(row.applicability_decision_id),
                        predicate_id=rule.predicate_id,
                        subject_key=row.subject_key,
                        outcome=row.outcome,
                        contract_rule_id=row.contract_rule_id,
                        contract_rule_version=row.contract_rule_version,
                        contract_rule_hash=row.contract_rule_hash,
                        evidence_sha256=row.evidence_sha256,
                    ),
                    producer_type,
                    producer_id,
                )
            )
        return tuple(result)

    def _edge_record(
        self,
        edge: ExpectedConsumerEdge,
        params: Mapping[str, object],
    ) -> RunExpectedConsumerEdgeRecord:
        return RunExpectedConsumerEdgeRecord(
            expected_edge_id=str(edge.expected_edge_id),
            tenant_id=cast(str, params["tenant_id"]),
            deal_id=cast(str, params["deal_id"]),
            run_id=cast(str, params["run_id"]),
            producer_input_binding_id=(
                str(edge.producer.producer_id)
                if edge.producer.producer_type == "INPUT_BINDING"
                else None
            ),
            producer_output_object_id=(
                str(edge.producer.producer_id)
                if edge.producer.producer_type == "OUTPUT_OBJECT"
                else None
            ),
            consumer_step_name=edge.consumer_step_name,
            consumer_subrole=edge.consumer_subrole,
            usage_kind=edge.usage_kind,
            applicability_decision_id=(
                str(edge.applicability_decision_id)
                if edge.applicability_decision_id is not None
                else None
            ),
            contract_rule_id=edge.contract_rule_id,
            contract_rule_version=edge.contract_rule_version,
            contract_rule_hash=edge.contract_rule_hash,
            edge_tuple_sha256=edge.edge_tuple_sha256,
            materialization_origin="WORKER",
            lease_owner_id=cast(str, params["lease_owner_id"]),
            lease_fencing_token=cast(int, params["lease_fencing_token"]),
        )

    def _current_step_unlocked_edge_ids(
        self,
        *,
        scope: object,
        step: RunStep,
        derived: tuple[ExpectedConsumerEdge, ...],
    ) -> frozenset[str]:
        params = _scope_params(scope)
        current_output_ids = frozenset(row[3] for row in self._step_roots)
        step_output_ids = {
            row.step_output_id
            for row in self._graph.list_run_step_outputs(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
            if row.step_id == step.step_id
        }
        current_decision_ids = {
            row.applicability_decision_id
            for row in self._graph.list_run_applicability_decisions(
                deal_id=cast(str, params["deal_id"]),
                run_id=cast(str, params["run_id"]),
            )
            if row.deciding_step_output_id in step_output_ids
        }
        return frozenset(
            str(edge.expected_edge_id)
            for edge in derived
            if (
                edge.producer.producer_type == "OUTPUT_OBJECT"
                and str(edge.producer.producer_id) in current_output_ids
            )
            or (
                edge.applicability_decision_id is not None
                and str(edge.applicability_decision_id) in current_decision_ids
            )
        )

    def _load_usage_occurrences(
        self,
        *,
        scope: object,
        edge: ExpectedConsumerEdge,
        consumer_output_object_id: str,
        consumer_identity_sha256: str,
        route_ordinal: int,
        consumer_step_output_id: str | None = None,
    ) -> tuple[ReferenceOccurrenceV1, ...]:
        params = _scope_params(scope)
        route = USAGE_ROUTE_REGISTRY[route_ordinal]
        relation = route.native_reference_relation
        self._require_relation(relation)
        columns = self._relation_columns(relation)
        required = {
            "tenant_id",
            "deal_id",
            "run_id",
            "consumer_output_object_id",
            "producer_type",
            "producer_input_binding_id",
            "producer_output_object_id",
            "producer_identity_sha256",
            "producer_value_sha256",
            "reference_kind",
            "reference_ordinal",
        }
        if not required <= columns:
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
        producer_column = (
            "producer_input_binding_id"
            if edge.producer.producer_type == "INPUT_BINDING"
            else "producer_output_object_id"
        )
        query_params = {
            **params,
            "producer_id": str(edge.producer.producer_id),
            "reference_kind": route.reference_kind,
        }
        consumer_predicate = ""
        if relation == "step_receipt_input_references":
            if consumer_step_output_id is None:
                _fail(CONSUMER_RECEIPT_INCOMPLETE)
            consumer_predicate = """
                  AND consumer_output_object_id =
                      CAST(:consumer_output_object_id AS uuid)
                  AND step_output_id = CAST(:consumer_step_output_id AS uuid)
            """
            query_params["consumer_output_object_id"] = consumer_output_object_id
            query_params["consumer_step_output_id"] = consumer_step_output_id
        rows = self._execute(
            text(
                f"""
                SELECT producer_type, producer_input_binding_id,
                       producer_output_object_id,
                       producer_identity_sha256, producer_value_sha256,
                       reference_kind, reference_ordinal
                FROM {relation}
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND {producer_column} =
                      CAST(:producer_id AS uuid)
                  AND reference_kind = :reference_kind
                  {consumer_predicate}
                ORDER BY reference_ordinal,
                         producer_identity_sha256,
                         producer_value_sha256
                """
            ),
            query_params,
        ).fetchall()
        try:
            schema = CENSUS_SCHEMA_REGISTRY[route.census_schema_id]
        except KeyError as exc:
            raise Task7PostgresPortError(PIPELINE_CONTRACT_UNAVAILABLE) from exc
        return tuple(
            ReferenceOccurrenceV1(
                consumer_receipt_output_object_id=UUID(consumer_output_object_id),
                consumer_receipt_identity_sha256=(consumer_identity_sha256),
                reference_schema_id=route.census_schema_id,
                reference_schema_version=schema.schema_version,
                reference_schema_sha256=schema.schema_sha256,
                reference_path=route.json_pointer,
                reference_ordinal=cast(
                    int,
                    _mapping(row, code=CONSUMER_RECEIPT_INCOMPLETE)["reference_ordinal"],
                ),
                producer_ref=edge.producer,
            )
            for row in rows
            if (
                _mapping(row, code=CONSUMER_RECEIPT_INCOMPLETE)["producer_type"]
                == edge.producer.producer_type
                and _mapping(row, code=CONSUMER_RECEIPT_INCOMPLETE)["producer_identity_sha256"]
                == edge.producer.producer_identity_sha256
                and _mapping(row, code=CONSUMER_RECEIPT_INCOMPLETE)["producer_value_sha256"]
                == edge.producer.producer_value_sha256
            )
        )

    @staticmethod
    def _edge_projection(edge: ExpectedConsumerEdge) -> dict[str, object]:
        return {
            "expected_edge_id": str(edge.expected_edge_id),
            "edge_tuple_sha256": edge.edge_tuple_sha256,
            "producer_type": edge.producer.producer_type,
            "producer_id": str(edge.producer.producer_id),
            "producer_identity_sha256": (edge.producer.producer_identity_sha256),
            "producer_value_sha256": edge.producer.producer_value_sha256,
            "consumer_step_name": edge.consumer_step_name,
            "consumer_subrole": edge.consumer_subrole,
            "usage_kind": edge.usage_kind,
            "applicability_decision_id": (
                str(edge.applicability_decision_id)
                if edge.applicability_decision_id is not None
                else None
            ),
            "contract_rule_id": edge.contract_rule_id,
            "contract_rule_version": edge.contract_rule_version,
            "contract_rule_hash": edge.contract_rule_hash,
        }

    @staticmethod
    def _stored_edge_matches(
        stored: RunExpectedConsumerEdgeRecord,
        expected: ExpectedConsumerEdge,
    ) -> bool:
        stored_producer_id = stored.producer_input_binding_id or stored.producer_output_object_id
        return (
            stored.expected_edge_id == str(expected.expected_edge_id)
            and stored_producer_id == str(expected.producer.producer_id)
            and stored.consumer_step_name == expected.consumer_step_name
            and stored.consumer_subrole == expected.consumer_subrole
            and stored.usage_kind == expected.usage_kind
            and stored.applicability_decision_id
            == (
                str(expected.applicability_decision_id)
                if expected.applicability_decision_id is not None
                else None
            )
            and stored.contract_rule_id == expected.contract_rule_id
            and stored.contract_rule_version == expected.contract_rule_version
            and stored.contract_rule_hash == expected.contract_rule_hash
            and stored.edge_tuple_sha256 == expected.edge_tuple_sha256
        )

    def _emit_audit(
        self,
        *,
        event_type: str,
        scope: object,
        safe: Mapping[str, object],
    ) -> None:
        params = _scope_params(scope)
        occurred_at = self._execute(
            text("SELECT statement_timestamp() AS occurred_at"),
            {},
        ).scalar_one()
        event = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": occurred_at.isoformat(),
            "tenant_id": params["tenant_id"],
            "actor": {
                "actor_type": "SERVICE",
                "actor_id": "idis-pipeline-worker",
            },
            "request": {
                "request_id": params["run_id"],
                "method": "POST",
                "path": f"/v1/runs/{params['run_id']}/task7-boundary",
                "status_code": 200,
            },
            "resource": {
                "resource_type": "deal",
                "resource_id": params["deal_id"],
                "deal_id": params["deal_id"],
            },
            "event_type": event_type,
            "severity": "MEDIUM",
            "summary": event_type,
            "payload": {
                "safe": dict(safe),
                "hashes": [],
                "refs": [],
            },
        }
        try:
            self._audit_sink.emit_in_tx(self._connection, event)
        except Exception as exc:
            raise Task7PostgresPortError(WORKER_LEASE_BOUNDARY_COMMIT_FAILED) from exc

    def _execute(self, statement: object, params: Mapping[str, object]) -> Any:
        try:
            return self._connection.execute(statement, dict(params))
        except Task7PostgresPortError:
            raise
        except Task7RunGraphRepositoryError as exc:
            raise Task7PostgresPortError(exc.code) from exc
        except SQLAlchemyError as exc:
            code = (
                WORKER_LEASE_FENCED
                if WORKER_LEASE_FENCED in str(exc)
                else WORKER_LEASE_BOUNDARY_COMMIT_FAILED
            )
            raise Task7PostgresPortError(code) from exc
