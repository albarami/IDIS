"""Typed append-only persistence for the seven-table Task 7 run graph.

The repository is bound to a caller-owned PostgreSQL transaction.  It sets
transaction-local tenant context but never begins, commits, or rolls back.
Worker-authored writes validate and lock the exact live run lease before
setting the transaction-local fencing context used by database guards.

Every insert is idempotent only when the stored immutable row is byte-for-byte
equivalent to the requested typed projection: it uses ``ON CONFLICT DO
NOTHING``, reloads by scoped primary key, and rejects any difference.  There is
no process-local persistence path and no mutation API.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Never, TypeAlias, cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from idis.persistence.db import DatabaseConfigError, set_tenant_local
from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.sql.base import Executable

WORKER_LEASE_FENCED: Final = "WORKER_LEASE_FENCED"
PIPELINE_CONTRACT_HASH_MISMATCH: Final = "PIPELINE_CONTRACT_HASH_MISMATCH"
TASK7_RUN_GRAPH_SCOPE_MISMATCH: Final = "TASK7_RUN_GRAPH_SCOPE_MISMATCH"
TASK7_RUN_GRAPH_ROW_INVALID: Final = "TASK7_RUN_GRAPH_ROW_INVALID"
TASK7_RUN_GRAPH_ROW_MISMATCH: Final = "TASK7_RUN_GRAPH_ROW_MISMATCH"
TASK7_RUN_GRAPH_PERSISTENCE_REJECTED: Final = "TASK7_RUN_GRAPH_PERSISTENCE_REJECTED"

_FULL_STRICT_V21_VERSION: Final = "full-strict-v21"
_POSTGRES_BIGINT_MAX: Final = (1 << 63) - 1
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class Task7RunGraphRepositoryError(RuntimeError):
    """Leakage-safe repository failure with a stable machine-readable code."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> Never:
    raise Task7RunGraphRepositoryError(code)


@dataclass(frozen=True, slots=True)
class RunInputBindingRecord:
    """Immutable selected-source binding admitted into one strict run."""

    binding_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    source_lineage_object_id: str
    source_object_kind: str
    source_disposition: str
    binding_purpose: str
    corpus_receipt_id: str
    selected_source_version: str
    selected_identity_sha256: str
    selected_content_sha256: str
    corpus_receipt_sha256: str
    pipeline_contract_version: str
    pipeline_contract_hash: str
    binding_sha256: str


@dataclass(frozen=True, slots=True)
class RunOutputObjectRecord:
    """Immutable semantic output root for a strict run."""

    output_object_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    output_class: str
    output_kind: str
    output_schema_version: str
    native_identity_sha256: str
    native_output_sha256: str
    pipeline_contract_version: str
    pipeline_contract_hash: str
    write_origin: str
    lease_owner_id: str | None
    lease_fencing_token: int


@dataclass(frozen=True, slots=True)
class RunStepOutputRecord:
    """Immutable link from a canonical step/subrole to one output root."""

    step_output_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    step_id: str
    output_object_id: str
    step_name: str
    subrole: str
    output_kind: str
    output_ordinal: int
    semantic_rule_version: str
    semantic_rule_hash: str
    summary_sha256: str
    lease_owner_id: str
    lease_fencing_token: int


@dataclass(frozen=True, slots=True)
class RunApplicabilityDecisionRecord:
    """Immutable runtime applicability decision bound to exact upstream evidence."""

    applicability_decision_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    decision_phase: str
    decision_kind: str
    subject_kind: str
    subject_key: str
    outcome: str
    upstream_input_binding_id: str | None
    upstream_output_object_id: str | None
    deciding_step_output_id: str
    contract_rule_id: str
    contract_rule_version: str
    contract_rule_hash: str
    evidence_sha256: str
    decision_sha256: str
    lease_owner_id: str
    lease_fencing_token: int


@dataclass(frozen=True, slots=True)
class RunExpectedConsumerEdgeRecord:
    """One immutable derived producer-to-consumer obligation."""

    expected_edge_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    producer_input_binding_id: str | None
    producer_output_object_id: str | None
    consumer_step_name: str
    consumer_subrole: str
    usage_kind: str
    applicability_decision_id: str | None
    contract_rule_id: str
    contract_rule_version: str
    contract_rule_hash: str
    edge_tuple_sha256: str
    materialization_origin: str
    lease_owner_id: str | None
    lease_fencing_token: int


@dataclass(frozen=True, slots=True)
class RunConsumerReceiptRecord:
    """Immutable proof that one consumer used one expected edge."""

    receipt_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    expected_edge_id: str
    consumer_step_output_id: str
    consumer_output_object_id: str
    consumer_step_name: str
    consumer_subrole: str
    usage_evidence_sha256: str
    consumed_at: datetime
    lease_owner_id: str
    lease_fencing_token: int


@dataclass(frozen=True, slots=True)
class DeliverableManifestReferenceRecord:
    """Immutable manifest membership proof for one deliverable member."""

    manifest_reference_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    manifest_deliverable_id: str
    manifest_output_object_id: str
    run_output_object_id: str
    member_kind: str
    catalog_ordinal: int | None
    member_step_output_id: str
    member_output_object_id: str
    member_identity_sha256: str
    member_value_sha256: str
    member_content_sha256: str | None
    reference_sha256: str
    lease_owner_id: str
    lease_fencing_token: int


Task7RunGraphRecord: TypeAlias = (
    RunInputBindingRecord
    | RunOutputObjectRecord
    | RunStepOutputRecord
    | RunApplicabilityDecisionRecord
    | RunExpectedConsumerEdgeRecord
    | RunConsumerReceiptRecord
    | DeliverableManifestReferenceRecord
)


class _LeasePolicy(StrEnum):
    NONE = "NONE"
    REQUIRED = "REQUIRED"
    ORIGIN = "ORIGIN"


@dataclass(frozen=True, slots=True)
class Task7RunGraphTableSpec:
    """Code-owned SQL and type authority for one run-graph relation."""

    table_name: str
    row_type: type[Task7RunGraphRecord]
    primary_key: str
    write_columns: tuple[str, ...]
    read_order_by: str
    uuid_columns: frozenset[str]
    hash_columns: frozenset[str]
    nullable_columns: frozenset[str]
    lease_policy: _LeasePolicy
    insert_sql: str
    reload_sql: str
    list_sql: str


def _insert_expression(column: str, uuid_columns: frozenset[str]) -> str:
    if column in uuid_columns:
        return f"CAST(:{column} AS uuid)"
    return f":{column}"


def _make_spec(
    *,
    table_name: str,
    row_type: type[Task7RunGraphRecord],
    primary_key: str,
    write_columns: tuple[str, ...],
    read_order_by: str,
    uuid_columns: frozenset[str],
    hash_columns: frozenset[str],
    nullable_columns: frozenset[str] = frozenset(),
    lease_policy: _LeasePolicy,
) -> Task7RunGraphTableSpec:
    projection = ", ".join(write_columns)
    values = ", ".join(_insert_expression(column, uuid_columns) for column in write_columns)
    insert_sql = f"INSERT INTO {table_name} ({projection}) VALUES ({values}) ON CONFLICT DO NOTHING"
    reload_sql = (
        f"SELECT {projection} FROM {table_name} "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) "
        "AND deal_id = CAST(:deal_id AS uuid) "
        "AND run_id = CAST(:run_id AS uuid) "
        f"AND {primary_key} = CAST(:{primary_key} AS uuid) "
        f"ORDER BY {primary_key}"
    )
    list_sql = (
        f"SELECT {projection} FROM {table_name} "
        "WHERE tenant_id = CAST(:tenant_id AS uuid) "
        "AND deal_id = CAST(:deal_id AS uuid) "
        "AND run_id = CAST(:run_id AS uuid) "
        f"ORDER BY {read_order_by}"
    )
    return Task7RunGraphTableSpec(
        table_name=table_name,
        row_type=row_type,
        primary_key=primary_key,
        write_columns=write_columns,
        read_order_by=read_order_by,
        uuid_columns=uuid_columns,
        hash_columns=hash_columns,
        nullable_columns=nullable_columns,
        lease_policy=lease_policy,
        insert_sql=insert_sql,
        reload_sql=reload_sql,
        list_sql=list_sql,
    )


_SCOPE_UUIDS: Final = frozenset({"tenant_id", "deal_id", "run_id"})

_SPEC_ROWS: Final[tuple[Task7RunGraphTableSpec, ...]] = (
    _make_spec(
        table_name="run_input_bindings",
        row_type=RunInputBindingRecord,
        primary_key="binding_id",
        write_columns=(
            "binding_id",
            "tenant_id",
            "deal_id",
            "run_id",
            "source_lineage_object_id",
            "source_object_kind",
            "source_disposition",
            "binding_purpose",
            "corpus_receipt_id",
            "selected_source_version",
            "selected_identity_sha256",
            "selected_content_sha256",
            "corpus_receipt_sha256",
            "pipeline_contract_version",
            "pipeline_contract_hash",
            "binding_sha256",
        ),
        read_order_by=("source_object_kind, source_lineage_object_id, binding_id"),
        uuid_columns=_SCOPE_UUIDS
        | {
            "binding_id",
            "source_lineage_object_id",
            "corpus_receipt_id",
        },
        hash_columns=frozenset(
            {
                "selected_identity_sha256",
                "selected_content_sha256",
                "corpus_receipt_sha256",
                "pipeline_contract_hash",
                "binding_sha256",
            }
        ),
        lease_policy=_LeasePolicy.NONE,
    ),
    _make_spec(
        table_name="run_output_objects",
        row_type=RunOutputObjectRecord,
        primary_key="output_object_id",
        write_columns=(
            "output_object_id",
            "tenant_id",
            "deal_id",
            "run_id",
            "output_class",
            "output_kind",
            "output_schema_version",
            "native_identity_sha256",
            "native_output_sha256",
            "pipeline_contract_version",
            "pipeline_contract_hash",
            "write_origin",
            "lease_owner_id",
            "lease_fencing_token",
        ),
        read_order_by=("output_kind, native_identity_sha256, output_object_id"),
        uuid_columns=_SCOPE_UUIDS | {"output_object_id", "lease_owner_id"},
        hash_columns=frozenset(
            {
                "native_identity_sha256",
                "native_output_sha256",
                "pipeline_contract_hash",
            }
        ),
        nullable_columns=frozenset({"lease_owner_id"}),
        lease_policy=_LeasePolicy.REQUIRED,
    ),
    _make_spec(
        table_name="run_step_outputs",
        row_type=RunStepOutputRecord,
        primary_key="step_output_id",
        write_columns=(
            "step_output_id",
            "tenant_id",
            "deal_id",
            "run_id",
            "step_id",
            "output_object_id",
            "step_name",
            "subrole",
            "output_kind",
            "output_ordinal",
            "semantic_rule_version",
            "semantic_rule_hash",
            "summary_sha256",
            "lease_owner_id",
            "lease_fencing_token",
        ),
        read_order_by=("step_name, subrole, output_kind, output_ordinal, step_output_id"),
        uuid_columns=_SCOPE_UUIDS
        | {
            "step_output_id",
            "step_id",
            "output_object_id",
            "lease_owner_id",
        },
        hash_columns=frozenset({"semantic_rule_hash", "summary_sha256"}),
        lease_policy=_LeasePolicy.REQUIRED,
    ),
    _make_spec(
        table_name="run_applicability_decisions",
        row_type=RunApplicabilityDecisionRecord,
        primary_key="applicability_decision_id",
        write_columns=(
            "applicability_decision_id",
            "tenant_id",
            "deal_id",
            "run_id",
            "decision_phase",
            "decision_kind",
            "subject_kind",
            "subject_key",
            "outcome",
            "upstream_input_binding_id",
            "upstream_output_object_id",
            "deciding_step_output_id",
            "contract_rule_id",
            "contract_rule_version",
            "contract_rule_hash",
            "evidence_sha256",
            "decision_sha256",
            "lease_owner_id",
            "lease_fencing_token",
        ),
        read_order_by=(
            "decision_phase, decision_kind, subject_kind, subject_key, "
            "contract_rule_id, contract_rule_version, applicability_decision_id"
        ),
        uuid_columns=_SCOPE_UUIDS
        | {
            "applicability_decision_id",
            "upstream_input_binding_id",
            "upstream_output_object_id",
            "deciding_step_output_id",
            "lease_owner_id",
        },
        hash_columns=frozenset({"contract_rule_hash", "evidence_sha256", "decision_sha256"}),
        nullable_columns=frozenset({"upstream_input_binding_id", "upstream_output_object_id"}),
        lease_policy=_LeasePolicy.REQUIRED,
    ),
    _make_spec(
        table_name="run_expected_consumer_edges",
        row_type=RunExpectedConsumerEdgeRecord,
        primary_key="expected_edge_id",
        write_columns=(
            "expected_edge_id",
            "tenant_id",
            "deal_id",
            "run_id",
            "producer_input_binding_id",
            "producer_output_object_id",
            "consumer_step_name",
            "consumer_subrole",
            "usage_kind",
            "applicability_decision_id",
            "contract_rule_id",
            "contract_rule_version",
            "contract_rule_hash",
            "edge_tuple_sha256",
            "materialization_origin",
            "lease_owner_id",
            "lease_fencing_token",
        ),
        read_order_by=(
            "consumer_step_name, consumer_subrole, usage_kind, contract_rule_id, "
            "contract_rule_version, expected_edge_id"
        ),
        uuid_columns=_SCOPE_UUIDS
        | {
            "expected_edge_id",
            "producer_input_binding_id",
            "producer_output_object_id",
            "applicability_decision_id",
            "lease_owner_id",
        },
        hash_columns=frozenset({"contract_rule_hash", "edge_tuple_sha256"}),
        nullable_columns=frozenset(
            {
                "producer_input_binding_id",
                "producer_output_object_id",
                "applicability_decision_id",
                "lease_owner_id",
            }
        ),
        lease_policy=_LeasePolicy.ORIGIN,
    ),
    _make_spec(
        table_name="run_consumer_receipts",
        row_type=RunConsumerReceiptRecord,
        primary_key="receipt_id",
        write_columns=(
            "receipt_id",
            "tenant_id",
            "deal_id",
            "run_id",
            "expected_edge_id",
            "consumer_step_output_id",
            "consumer_output_object_id",
            "consumer_step_name",
            "consumer_subrole",
            "usage_evidence_sha256",
            "consumed_at",
            "lease_owner_id",
            "lease_fencing_token",
        ),
        read_order_by=("consumer_step_name, consumer_subrole, expected_edge_id, receipt_id"),
        uuid_columns=_SCOPE_UUIDS
        | {
            "receipt_id",
            "expected_edge_id",
            "consumer_step_output_id",
            "consumer_output_object_id",
            "lease_owner_id",
        },
        hash_columns=frozenset({"usage_evidence_sha256"}),
        lease_policy=_LeasePolicy.REQUIRED,
    ),
    _make_spec(
        table_name="deliverable_manifest_references",
        row_type=DeliverableManifestReferenceRecord,
        primary_key="manifest_reference_id",
        write_columns=(
            "manifest_reference_id",
            "tenant_id",
            "deal_id",
            "run_id",
            "manifest_deliverable_id",
            "manifest_output_object_id",
            "run_output_object_id",
            "member_kind",
            "catalog_ordinal",
            "member_step_output_id",
            "member_output_object_id",
            "member_identity_sha256",
            "member_value_sha256",
            "member_content_sha256",
            "reference_sha256",
            "lease_owner_id",
            "lease_fencing_token",
        ),
        read_order_by=(
            "manifest_deliverable_id, member_kind, catalog_ordinal NULLS LAST, "
            "manifest_reference_id"
        ),
        uuid_columns=_SCOPE_UUIDS
        | {
            "manifest_reference_id",
            "manifest_deliverable_id",
            "manifest_output_object_id",
            "run_output_object_id",
            "member_step_output_id",
            "member_output_object_id",
            "lease_owner_id",
        },
        hash_columns=frozenset(
            {
                "member_identity_sha256",
                "member_value_sha256",
                "member_content_sha256",
                "reference_sha256",
            }
        ),
        nullable_columns=frozenset({"catalog_ordinal", "member_content_sha256"}),
        lease_policy=_LeasePolicy.REQUIRED,
    ),
)

TASK7_RUN_GRAPH_SPECS: Final[Mapping[str, Task7RunGraphTableSpec]] = MappingProxyType(
    {spec.table_name: spec for spec in _SPEC_ROWS}
)


class PostgresTask7RunGraphRepository:
    """Tenant-bound append/read repository for the Task 7 run graph."""

    def __init__(self, connection: Connection, tenant_id: str) -> None:
        self._connection = connection
        self._tenant_id = _validated_uuid(
            tenant_id,
            code=TASK7_RUN_GRAPH_SCOPE_MISMATCH,
        )
        try:
            set_tenant_local(connection, self._tenant_id)
        except DatabaseConfigError as exc:
            raise Task7RunGraphRepositoryError(TASK7_RUN_GRAPH_SCOPE_MISMATCH) from exc
        except SQLAlchemyError as exc:
            raise Task7RunGraphRepositoryError(TASK7_RUN_GRAPH_PERSISTENCE_REJECTED) from exc

    def insert_run_input_binding(
        self,
        record: RunInputBindingRecord,
    ) -> RunInputBindingRecord:
        return cast(
            RunInputBindingRecord,
            self._insert_and_reload(
                TASK7_RUN_GRAPH_SPECS["run_input_bindings"],
                record,
            ),
        )

    def insert_run_output_object(
        self,
        record: RunOutputObjectRecord,
    ) -> RunOutputObjectRecord:
        return cast(
            RunOutputObjectRecord,
            self._insert_and_reload(
                TASK7_RUN_GRAPH_SPECS["run_output_objects"],
                record,
            ),
        )

    def insert_run_step_output(
        self,
        record: RunStepOutputRecord,
    ) -> RunStepOutputRecord:
        return cast(
            RunStepOutputRecord,
            self._insert_and_reload(
                TASK7_RUN_GRAPH_SPECS["run_step_outputs"],
                record,
            ),
        )

    def insert_run_applicability_decision(
        self,
        record: RunApplicabilityDecisionRecord,
    ) -> RunApplicabilityDecisionRecord:
        return cast(
            RunApplicabilityDecisionRecord,
            self._insert_and_reload(
                TASK7_RUN_GRAPH_SPECS["run_applicability_decisions"],
                record,
            ),
        )

    def insert_run_expected_consumer_edge(
        self,
        record: RunExpectedConsumerEdgeRecord,
    ) -> RunExpectedConsumerEdgeRecord:
        return cast(
            RunExpectedConsumerEdgeRecord,
            self._insert_and_reload(
                TASK7_RUN_GRAPH_SPECS["run_expected_consumer_edges"],
                record,
            ),
        )

    def insert_run_consumer_receipt(
        self,
        record: RunConsumerReceiptRecord,
    ) -> RunConsumerReceiptRecord:
        return cast(
            RunConsumerReceiptRecord,
            self._insert_and_reload(
                TASK7_RUN_GRAPH_SPECS["run_consumer_receipts"],
                record,
            ),
        )

    def insert_deliverable_manifest_reference(
        self,
        record: DeliverableManifestReferenceRecord,
    ) -> DeliverableManifestReferenceRecord:
        return cast(
            DeliverableManifestReferenceRecord,
            self._insert_and_reload(
                TASK7_RUN_GRAPH_SPECS["deliverable_manifest_references"],
                record,
            ),
        )

    def list_run_input_bindings(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[RunInputBindingRecord, ...]:
        return cast(
            tuple[RunInputBindingRecord, ...],
            self._list(
                TASK7_RUN_GRAPH_SPECS["run_input_bindings"],
                deal_id=deal_id,
                run_id=run_id,
            ),
        )

    def list_run_output_objects(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[RunOutputObjectRecord, ...]:
        return cast(
            tuple[RunOutputObjectRecord, ...],
            self._list(
                TASK7_RUN_GRAPH_SPECS["run_output_objects"],
                deal_id=deal_id,
                run_id=run_id,
            ),
        )

    def list_run_step_outputs(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[RunStepOutputRecord, ...]:
        return cast(
            tuple[RunStepOutputRecord, ...],
            self._list(
                TASK7_RUN_GRAPH_SPECS["run_step_outputs"],
                deal_id=deal_id,
                run_id=run_id,
            ),
        )

    def list_run_applicability_decisions(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[RunApplicabilityDecisionRecord, ...]:
        return cast(
            tuple[RunApplicabilityDecisionRecord, ...],
            self._list(
                TASK7_RUN_GRAPH_SPECS["run_applicability_decisions"],
                deal_id=deal_id,
                run_id=run_id,
            ),
        )

    def list_run_expected_consumer_edges(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[RunExpectedConsumerEdgeRecord, ...]:
        return cast(
            tuple[RunExpectedConsumerEdgeRecord, ...],
            self._list(
                TASK7_RUN_GRAPH_SPECS["run_expected_consumer_edges"],
                deal_id=deal_id,
                run_id=run_id,
            ),
        )

    def list_run_consumer_receipts(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[RunConsumerReceiptRecord, ...]:
        return cast(
            tuple[RunConsumerReceiptRecord, ...],
            self._list(
                TASK7_RUN_GRAPH_SPECS["run_consumer_receipts"],
                deal_id=deal_id,
                run_id=run_id,
            ),
        )

    def list_deliverable_manifest_references(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[DeliverableManifestReferenceRecord, ...]:
        return cast(
            tuple[DeliverableManifestReferenceRecord, ...],
            self._list(
                TASK7_RUN_GRAPH_SPECS["deliverable_manifest_references"],
                deal_id=deal_id,
                run_id=run_id,
            ),
        )

    def _insert_and_reload(
        self,
        spec: Task7RunGraphTableSpec,
        record: Task7RunGraphRecord,
    ) -> Task7RunGraphRecord:
        payload = self._validated_payload(spec, record)
        if spec.lease_policy is _LeasePolicy.NONE:
            self._require_admission_scope(
                deal_id=record.deal_id,
                run_id=record.run_id,
            )
        elif spec.lease_policy is _LeasePolicy.REQUIRED:
            self._require_fence(
                deal_id=record.deal_id,
                run_id=record.run_id,
                lease_owner_id=cast(str, payload["lease_owner_id"]),
                lease_fencing_token=cast(
                    int,
                    payload["lease_fencing_token"],
                ),
            )
        else:
            self._require_edge_scope(cast(RunExpectedConsumerEdgeRecord, record))

        self._execute(text(spec.insert_sql), payload)
        reloaded = self._execute(
            text(spec.reload_sql),
            {
                "tenant_id": record.tenant_id,
                "deal_id": record.deal_id,
                "run_id": record.run_id,
                spec.primary_key: payload[spec.primary_key],
            },
        ).one_or_none()
        if reloaded is None:
            _fail(TASK7_RUN_GRAPH_ROW_MISMATCH)
        stored = self._row_to_record(spec, reloaded)
        if stored != record:
            _fail(TASK7_RUN_GRAPH_ROW_MISMATCH)
        return stored

    def _list(
        self,
        spec: Task7RunGraphTableSpec,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[Task7RunGraphRecord, ...]:
        checked_deal = _validated_uuid(
            deal_id,
            code=TASK7_RUN_GRAPH_SCOPE_MISMATCH,
        )
        checked_run = _validated_uuid(
            run_id,
            code=TASK7_RUN_GRAPH_SCOPE_MISMATCH,
        )
        rows = self._execute(
            text(spec.list_sql),
            {
                "tenant_id": self._tenant_id,
                "deal_id": checked_deal,
                "run_id": checked_run,
            },
        ).fetchall()
        return tuple(self._row_to_record(spec, row) for row in rows)

    def _validated_payload(
        self,
        spec: Task7RunGraphTableSpec,
        record: Task7RunGraphRecord,
    ) -> dict[str, object]:
        if type(record) is not spec.row_type:
            _fail(TASK7_RUN_GRAPH_ROW_INVALID)
        if record.tenant_id != self._tenant_id:
            _fail(TASK7_RUN_GRAPH_SCOPE_MISMATCH)
        payload = {field.name: getattr(record, field.name) for field in fields(record)}
        if tuple(payload) != spec.write_columns:
            _fail(TASK7_RUN_GRAPH_ROW_INVALID)

        for column in spec.uuid_columns:
            value = payload[column]
            if value is None and column in spec.nullable_columns:
                continue
            payload[column] = _validated_uuid(
                value,
                code=(
                    TASK7_RUN_GRAPH_SCOPE_MISMATCH
                    if column in _SCOPE_UUIDS
                    else TASK7_RUN_GRAPH_ROW_INVALID
                ),
            )
        for column in spec.hash_columns:
            value = payload[column]
            if value is None and column in spec.nullable_columns:
                continue
            if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
                _fail(TASK7_RUN_GRAPH_ROW_INVALID)

        if isinstance(
            record,
            (RunInputBindingRecord, RunOutputObjectRecord),
        ) and (
            record.pipeline_contract_version != _FULL_STRICT_V21_VERSION
            or record.pipeline_contract_hash != FULL_STRICT_V21_CONTRACT_HASH
        ):
            _fail(PIPELINE_CONTRACT_HASH_MISMATCH)
        if isinstance(record, RunOutputObjectRecord) and record.write_origin != "RUNTIME":
            _fail(TASK7_RUN_GRAPH_ROW_INVALID)
        if isinstance(record, RunApplicabilityDecisionRecord) and (
            (record.upstream_input_binding_id is None) == (record.upstream_output_object_id is None)
        ):
            _fail(TASK7_RUN_GRAPH_ROW_INVALID)
        if isinstance(record, RunExpectedConsumerEdgeRecord) and (
            (record.producer_input_binding_id is None) == (record.producer_output_object_id is None)
        ):
            _fail(TASK7_RUN_GRAPH_ROW_INVALID)
        if isinstance(record, RunConsumerReceiptRecord) and (
            record.consumed_at.tzinfo is None or record.consumed_at.utcoffset() is None
        ):
            _fail(TASK7_RUN_GRAPH_ROW_INVALID)

        self._validate_lease_shape(spec, payload)
        return payload

    def _validate_lease_shape(
        self,
        spec: Task7RunGraphTableSpec,
        payload: Mapping[str, object],
    ) -> None:
        if spec.lease_policy is _LeasePolicy.NONE:
            return
        owner = payload["lease_owner_id"]
        token = payload["lease_fencing_token"]
        if spec.lease_policy is _LeasePolicy.ORIGIN:
            origin = payload["materialization_origin"]
            if origin == "ADMISSION":
                if owner is not None or token != 0:
                    _fail(WORKER_LEASE_FENCED)
                return
            if origin != "WORKER":
                _fail(TASK7_RUN_GRAPH_ROW_INVALID)
        if (
            not isinstance(owner, str)
            or type(token) is not int
            or token <= 0
            or token > _POSTGRES_BIGINT_MAX
        ):
            _fail(WORKER_LEASE_FENCED)

    def _require_edge_scope(
        self,
        record: RunExpectedConsumerEdgeRecord,
    ) -> None:
        if record.materialization_origin == "ADMISSION":
            self._require_admission_scope(
                deal_id=record.deal_id,
                run_id=record.run_id,
            )
            return
        self._require_fence(
            deal_id=record.deal_id,
            run_id=record.run_id,
            lease_owner_id=cast(str, record.lease_owner_id),
            lease_fencing_token=record.lease_fencing_token,
        )

    def _require_admission_scope(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> None:
        admitted = self._execute(
            text(
                """
                SELECT 1 FROM runs
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND status = 'QUEUED'
                  AND pipeline_contract_version = :pipeline_contract_version
                  AND pipeline_contract_hash = :pipeline_contract_hash
                  AND pipeline_resume_state = 'RESUMABLE'
                FOR KEY SHARE
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "pipeline_contract_version": _FULL_STRICT_V21_VERSION,
                "pipeline_contract_hash": FULL_STRICT_V21_CONTRACT_HASH,
            },
        ).scalar_one_or_none()
        if admitted != 1:
            _fail(TASK7_RUN_GRAPH_SCOPE_MISMATCH)

    def _require_fence(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        valid = self._execute(
            text(
                """
                SELECT 1 FROM runs
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND status = 'RUNNING'
                  AND pipeline_contract_version = :pipeline_contract_version
                  AND pipeline_contract_hash = :pipeline_contract_hash
                  AND pipeline_resume_state = 'RESUMABLE'
                  AND lease_owner_id = CAST(:lease_owner_id AS uuid)
                  AND lease_fencing_token = :lease_fencing_token
                  AND lease_expires_at > statement_timestamp()
                FOR UPDATE
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "pipeline_contract_version": _FULL_STRICT_V21_VERSION,
                "pipeline_contract_hash": FULL_STRICT_V21_CONTRACT_HASH,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": lease_fencing_token,
            },
        ).scalar_one_or_none()
        if valid != 1:
            _fail(WORKER_LEASE_FENCED)
        self._execute(
            text(
                """
                SELECT
                    set_config('idis.worker_lease_tenant_id', :tenant_id, true),
                    set_config('idis.worker_lease_deal_id', :deal_id, true),
                    set_config('idis.worker_lease_run_id', :run_id, true),
                    set_config(
                        'idis.worker_lease_owner_id',
                        :lease_owner_id,
                        true
                    ),
                    set_config(
                        'idis.worker_lease_fencing_token',
                        CAST(:lease_fencing_token AS text),
                        true
                    )
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": lease_fencing_token,
            },
        )

    def _row_to_record(
        self,
        spec: Task7RunGraphTableSpec,
        row: object,
    ) -> Task7RunGraphRecord:
        mapping = getattr(row, "_mapping", None)
        if mapping is None:
            _fail(TASK7_RUN_GRAPH_ROW_MISMATCH)
        values: dict[str, object] = {}
        for column in spec.write_columns:
            if column not in mapping:
                _fail(TASK7_RUN_GRAPH_ROW_MISMATCH)
            value = mapping[column]
            if value is not None and column in spec.uuid_columns:
                value = str(value)
            values[column] = value
        try:
            record = cast(Any, spec.row_type)(**values)
        except (TypeError, ValueError) as exc:
            raise Task7RunGraphRepositoryError(TASK7_RUN_GRAPH_ROW_MISMATCH) from exc
        self._validated_payload(spec, record)
        return cast(Task7RunGraphRecord, record)

    def _execute(
        self,
        statement: Executable,
        params: Mapping[str, object],
    ) -> Any:
        try:
            return self._connection.execute(statement, dict(params))
        except SQLAlchemyError as exc:
            code = (
                WORKER_LEASE_FENCED
                if WORKER_LEASE_FENCED in str(exc)
                else TASK7_RUN_GRAPH_PERSISTENCE_REJECTED
            )
            raise Task7RunGraphRepositoryError(code) from exc


def _validated_uuid(value: object, *, code: str) -> str:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        _fail(code)
    canonical = str(parsed)
    if value != canonical:
        _fail(code)
    return canonical
