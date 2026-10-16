"""Durable, tenant-bound, one-use admission proofs for strict FULL runs.

The proof and the run are deliberately updated in one database savepoint.  A
run row is inserted before the proof is consumed so an active-run conflict (or
any other insert failure) leaves the proof reusable instead of burning it.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from idis.persistence.db import is_postgres_configured, set_tenant_local
from idis.persistence.repositories.runs import RunAlreadyActiveError
from idis.persistence.repositories.task7_run_graph import (
    PostgresTask7RunGraphRepository,
    RunExpectedConsumerEdgeRecord,
    RunInputBindingRecord,
    Task7RunGraphRepositoryError,
)
from idis.pipeline.contract_registry import (
    PIPELINE_CONTRACTS,
    PipelineContractError,
    PipelineContractSpec,
    PipelineStepSpec,
    require_pipeline_contract_for_admission,
    validate_admission_step_rows,
)
from idis.pipeline.task7_edges import (
    EdgeDerivationError,
    EdgeDerivationRequest,
    ProducerSelectionRequest,
    ProducerSelectorSpec,
    derive_expected_edges,
    derive_rule_bound_producers,
)
from idis.pipeline.task7_uuid import (
    Task7UuidError,
    derive_step_id_for_admission,
    derive_task7_uuid,
)

if TYPE_CHECKING:
    from sqlalchemy import Connection


_ACTIVE_RUN_INDEX = "ux_runs_one_active_per_deal"
_ADMISSION_EDGE_RULES = frozenset(
    {
        "E001_SOURCE_INVENTORY_ADMISSION",
        "E002_SOURCE_HANDOFF_ADMISSION",
    }
)
_BINDING_PURPOSE_BY_KIND = {
    "PACKAGE": "INVENTORY",
    "PACKAGE_FILE": "INVENTORY",
    "ARTIFACT": "HANDOFF",
    "DOCUMENT": "HANDOFF",
    "PARSE_ATTEMPT": "HANDOFF",
    "SPAN": "EVIDENCE",
}


class StrictPreflightProofError(Exception):
    """A stable, leakage-safe strict-proof admission failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _task7_normalize_json(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = unicodedata.normalize("NFC", str(key))
            if normalized_key in normalized:
                raise ValueError("canonical mapping keys collide after normalization")
            normalized[normalized_key] = _task7_normalize_json(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_task7_normalize_json(item) for item in value]
    return value


def _task7_sha256(value: object) -> str:
    encoded = json.dumps(
        _task7_normalize_json(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_uuid_text(value: object) -> str:
    parsed = UUID(str(value))
    canonical = str(parsed)
    if str(value) != canonical:
        raise ValueError("UUID is not canonical")
    return canonical


@dataclass(frozen=True)
class StrictPreflightBinding:
    """Every immutable input a strict preflight proof authorizes."""

    tenant_id: str
    deal_id: str
    corpus_hash: str
    strict_profile_generation: str
    strict_profile_hmac: str
    pipeline_contract_version: str
    pipeline_contract_hash: str
    expected_consumer_edge_rule_hash: str
    static_decision_set_hash: str
    methodology_version_hashes: tuple[str, ...]
    promoted_pointer_hash: str
    provider_receipt_set_hash: str
    dependency_receipt_set_hash: str

    def digest(self) -> str:
        """Return the canonical SHA-256 digest covering every binding field."""
        payload = {
            "corpus_hash": self.corpus_hash,
            "deal_id": self.deal_id,
            "dependency_receipt_set_hash": self.dependency_receipt_set_hash,
            "expected_consumer_edge_rule_hash": self.expected_consumer_edge_rule_hash,
            "methodology_version_hashes": list(self.methodology_version_hashes),
            "pipeline_contract_hash": self.pipeline_contract_hash,
            "pipeline_contract_version": self.pipeline_contract_version,
            "promoted_pointer_hash": self.promoted_pointer_hash,
            "provider_receipt_set_hash": self.provider_receipt_set_hash,
            "static_decision_set_hash": self.static_decision_set_hash,
            "strict_profile_generation": self.strict_profile_generation,
            "strict_profile_hmac": self.strict_profile_hmac,
            "tenant_id": self.tenant_id,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class StrictPreflightProofRecord:
    """A persisted strict preflight proof and its consumption state."""

    proof_id: str
    binding: StrictPreflightBinding
    binding_hash: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    consumed_by_run_id: str | None
    expired_at_lock: bool = False


class PostgresStrictPreflightProofsRepository:
    """Tenant-scoped repository that atomically consumes proofs into runs."""

    def __init__(
        self,
        conn: Connection,
        tenant_id: str,
        *,
        pipeline_contract_registry: Mapping[str, PipelineContractSpec] = PIPELINE_CONTRACTS,
    ) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        self._pipeline_contract_registry = pipeline_contract_registry
        # Lightweight contract fakes used by repository unit tests expose only
        # begin_nested(); real SQLAlchemy connections always expose execute().
        if callable(getattr(conn, "execute", None)):
            set_tenant_local(conn, tenant_id)

    def consume_and_admit_contract_run(
        self,
        *,
        proof_id: str,
        expected_binding: StrictPreflightBinding,
        run_id: str,
        deal_id: str,
        mode: str,
        idempotency_key: str | None,
        source: dict[str, Any] | None,
        created_by_actor_id: str | None,
        created_by_actor_type: str | None,
        selected_source_package_ids: Sequence[str],
    ) -> dict[str, Any]:
        """Atomically admit a reviewed contract, source graph, steps, and proof."""
        try:
            proof_id = str(uuid.UUID(proof_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_INVALID") from exc
        if not isinstance(expected_binding, StrictPreflightBinding):
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")
        try:
            package_ids = tuple(
                sorted(
                    {_canonical_uuid_text(package_id) for package_id in selected_source_package_ids}
                )
            )
        except (TypeError, ValueError) as exc:
            raise StrictPreflightProofError("PIPELINE_CONTRACT_UNAVAILABLE") from exc
        if not package_ids or len(package_ids) != len(tuple(selected_source_package_ids)):
            raise StrictPreflightProofError("PIPELINE_CONTRACT_UNAVAILABLE")

        with self._conn.begin_nested():
            source_roots = self._lock_task7_source_roots(
                deal_id=deal_id,
                package_ids=package_ids,
            )
            record = self._load_for_update(proof_id)
            self._validate_for_consumption(
                record,
                expected_binding=expected_binding,
                deal_id=deal_id,
            )
            if mode != "FULL":
                raise StrictPreflightProofError("PIPELINE_CONTRACT_UNAVAILABLE")
            try:
                contract = require_pipeline_contract_for_admission(
                    registry=self._pipeline_contract_registry,
                    contract_version=expected_binding.pipeline_contract_version,
                    proof_contract_hash=expected_binding.pipeline_contract_hash,
                )
                step_rows = self._stage_task7_steps(
                    contract=contract,
                    deal_id=deal_id,
                    run_id=run_id,
                )
                input_binding_rows = self._stage_task7_input_bindings(
                    contract=contract,
                    deal_id=deal_id,
                    run_id=run_id,
                    source_roots=source_roots,
                )
            except (PipelineContractError, Task7UuidError, ValueError) as exc:
                code = getattr(exc, "code", "PIPELINE_CONTRACT_HASH_MISMATCH")
                raise StrictPreflightProofError(str(code)) from exc

            run = self._insert_task7_run(
                run_id=run_id,
                deal_id=deal_id,
                mode=mode,
                idempotency_key=idempotency_key,
                source=source,
                created_by_actor_id=created_by_actor_id,
                created_by_actor_type=created_by_actor_type,
                strict_preflight_proof_id=proof_id,
                pipeline_contract_version=contract.contract_version,
                pipeline_contract_hash=expected_binding.pipeline_contract_hash,
                pipeline_resume_state="RESUMABLE",
                pipeline_resume_error_code=None,
            )
            self._insert_task7_steps(rows=step_rows)
            reloaded_rows = self._load_task7_steps(run_id=run_id)
            try:
                self._validate_task7_step_reload(
                    contract=contract,
                    deal_id=deal_id,
                    run_id=run_id,
                    rows=reloaded_rows,
                )
            except (PipelineContractError, Task7UuidError) as exc:
                raise StrictPreflightProofError(exc.code) from exc
            if not self._mark_consumed(proof_id=proof_id, run_id=run_id):
                raise StrictPreflightProofError(self._consumption_failure_code(proof_id))
            try:
                self._insert_task7_input_bindings(rows=input_binding_rows)
                reloaded_bindings = self._load_task7_input_bindings(
                    deal_id=deal_id,
                    run_id=run_id,
                )
                self._validate_task7_input_binding_reload(
                    expected=input_binding_rows,
                    actual=reloaded_bindings,
                )
                admission_edges = self._derive_task7_admission_edges(
                    deal_id=deal_id,
                    run_id=run_id,
                    bindings=reloaded_bindings,
                )
                self._insert_task7_admission_edges(rows=admission_edges)
                reloaded_edges = self._load_task7_admission_edges(
                    deal_id=deal_id,
                    run_id=run_id,
                )
                self._validate_task7_admission_edge_reload(
                    expected=admission_edges,
                    actual=reloaded_edges,
                )
            except (
                EdgeDerivationError,
                Task7RunGraphRepositoryError,
                Task7UuidError,
                ValueError,
            ) as exc:
                code = getattr(exc, "code", "PIPELINE_CONTRACT_HASH_MISMATCH")
                raise StrictPreflightProofError(str(code)) from exc
            return run

    def _lock_task7_source_roots(
        self,
        *,
        deal_id: str,
        package_ids: tuple[str, ...],
    ) -> tuple[dict[str, Any], ...]:
        """Lock and reload the complete already-frozen source-root set."""

        checked_deal_id = _canonical_uuid_text(deal_id)
        package_rows = self._conn.execute(
            text(
                """
                SELECT package_id, status, source_binding_state,
                       source_lineage_object_id
                FROM data_room_packages
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND package_id = ANY(CAST(:package_ids AS uuid[]))
                ORDER BY package_id
                FOR KEY SHARE
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": checked_deal_id,
                "package_ids": list(package_ids),
            },
        ).fetchall()
        if len(package_rows) != len(package_ids):
            raise StrictPreflightProofError("PIPELINE_CONTRACT_UNAVAILABLE")
        package_root_ids: dict[str, str] = {}
        for row in package_rows:
            package_id = _canonical_uuid_text(_row_value(row, "package_id"))
            root_id = _canonical_uuid_text(_row_value(row, "source_lineage_object_id"))
            if (
                package_id not in package_ids
                or _row_value(row, "status") != "SEALED"
                or _row_value(row, "source_binding_state") != "BOUND"
            ):
                raise StrictPreflightProofError("PIPELINE_CONTRACT_UNAVAILABLE")
            package_root_ids[package_id] = root_id

        root_rows = self._conn.execute(
            text(
                """
                SELECT source_lineage_object_id, tenant_id, deal_id,
                       corpus_receipt_id, object_kind, source_version,
                       source_disposition, object_identity_sha256,
                       content_sha256, corpus_receipt_sha256
                FROM source_lineage_objects
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND corpus_receipt_id = ANY(CAST(:package_ids AS uuid[]))
                ORDER BY corpus_receipt_id, object_kind,
                         source_lineage_object_id
                FOR KEY SHARE
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": checked_deal_id,
                "package_ids": list(package_ids),
            },
        ).fetchall()
        roots = tuple(
            {
                "source_lineage_object_id": _canonical_uuid_text(
                    _row_value(row, "source_lineage_object_id")
                ),
                "tenant_id": _canonical_uuid_text(_row_value(row, "tenant_id")),
                "deal_id": _canonical_uuid_text(_row_value(row, "deal_id")),
                "corpus_receipt_id": _canonical_uuid_text(_row_value(row, "corpus_receipt_id")),
                "object_kind": str(_row_value(row, "object_kind")),
                "source_version": str(_row_value(row, "source_version")),
                "source_disposition": str(_row_value(row, "source_disposition")),
                "object_identity_sha256": str(_row_value(row, "object_identity_sha256")),
                "content_sha256": str(_row_value(row, "content_sha256")),
                "corpus_receipt_sha256": str(_row_value(row, "corpus_receipt_sha256")),
            }
            for row in root_rows
        )
        if not roots:
            raise StrictPreflightProofError("PIPELINE_CONTRACT_UNAVAILABLE")
        package_roots = {
            row["corpus_receipt_id"]: row["source_lineage_object_id"]
            for row in roots
            if row["object_kind"] == "PACKAGE"
        }
        if package_roots != package_root_ids:
            raise StrictPreflightProofError("PIPELINE_CONTRACT_UNAVAILABLE")
        return roots

    def _stage_task7_input_bindings(
        self,
        *,
        contract: PipelineContractSpec,
        deal_id: str,
        run_id: str,
        source_roots: Sequence[Mapping[str, Any]],
    ) -> tuple[RunInputBindingRecord, ...]:
        """Derive every immutable selected-source binding from frozen roots."""

        contract_hash = contract.contract_hash
        if contract.contract_version != "full-strict-v21" or contract_hash is None:
            raise PipelineContractError(
                "PIPELINE_CONTRACT_HASH_MISMATCH",
                "input bindings require the reviewed strict contract",
            )
        checked_scope = {
            "tenant_id": _canonical_uuid_text(self._tenant_id),
            "deal_id": _canonical_uuid_text(deal_id),
            "run_id": _canonical_uuid_text(run_id),
        }
        records: list[RunInputBindingRecord] = []
        for source in source_roots:
            if (
                source.get("tenant_id") != checked_scope["tenant_id"]
                or source.get("deal_id") != checked_scope["deal_id"]
            ):
                raise ValueError("source root escaped the admission scope")
            kind = str(source.get("object_kind"))
            disposition = str(source.get("source_disposition"))
            purpose = _BINDING_PURPOSE_BY_KIND.get(kind)
            if (
                purpose is None
                or (kind == "PACKAGE" and disposition != "CORPUS")
                or (kind == "SPAN" and disposition != "EVIDENCE_BEARING")
                or (
                    kind not in {"PACKAGE", "SPAN"}
                    and disposition not in {"EVIDENCE_BEARING", "CONFIRMED_EXCLUSION"}
                )
            ):
                raise ValueError("source root has no reviewed binding recipe")
            source_id = _canonical_uuid_text(source.get("source_lineage_object_id"))
            corpus_id = _canonical_uuid_text(source.get("corpus_receipt_id"))
            source_version = str(source.get("source_version"))
            identity_sha256 = str(source.get("object_identity_sha256"))
            content_sha256 = str(source.get("content_sha256"))
            corpus_sha256 = str(source.get("corpus_receipt_sha256"))
            digest_payload = [
                "run-input-binding-digest-v2",
                checked_scope["tenant_id"],
                checked_scope["deal_id"],
                checked_scope["run_id"],
                contract.contract_version,
                contract_hash,
                corpus_id,
                source_id,
                kind,
                disposition,
                purpose,
                source_version,
                identity_sha256,
                content_sha256,
                corpus_sha256,
            ]
            binding_sha256 = _task7_sha256(digest_payload)
            binding_id = derive_task7_uuid(
                id_kind="binding_id",
                values={
                    **checked_scope,
                    "pipeline_contract_version": contract.contract_version,
                    "pipeline_contract_hash": contract_hash,
                    "corpus_receipt_id": corpus_id,
                    "source_lineage_object_id": source_id,
                    "source_object_kind": kind,
                    "source_disposition": disposition,
                    "binding_purpose": purpose,
                    "selected_source_version": source_version,
                    "selected_identity_sha256": identity_sha256,
                    "selected_content_sha256": content_sha256,
                    "corpus_receipt_sha256": corpus_sha256,
                },
            ).value
            records.append(
                RunInputBindingRecord(
                    binding_id=str(binding_id),
                    tenant_id=checked_scope["tenant_id"],
                    deal_id=checked_scope["deal_id"],
                    run_id=checked_scope["run_id"],
                    source_lineage_object_id=source_id,
                    source_object_kind=kind,
                    source_disposition=disposition,
                    binding_purpose=purpose,
                    corpus_receipt_id=corpus_id,
                    selected_source_version=source_version,
                    selected_identity_sha256=identity_sha256,
                    selected_content_sha256=content_sha256,
                    corpus_receipt_sha256=corpus_sha256,
                    pipeline_contract_version=contract.contract_version,
                    pipeline_contract_hash=contract_hash,
                    binding_sha256=binding_sha256,
                )
            )
        ordered = tuple(
            sorted(
                records,
                key=lambda row: (
                    row.source_object_kind,
                    UUID(row.source_lineage_object_id).bytes,
                    UUID(row.binding_id).bytes,
                ),
            )
        )
        if (
            not ordered
            or len({row.binding_id for row in ordered}) != len(ordered)
            or len({row.source_lineage_object_id for row in ordered}) != len(ordered)
        ):
            raise ValueError("input-binding identity set is invalid")
        return ordered

    def _insert_task7_input_bindings(
        self,
        *,
        rows: tuple[RunInputBindingRecord, ...],
    ) -> None:
        repository = PostgresTask7RunGraphRepository(
            self._conn,
            self._tenant_id,
        )
        for row in rows:
            repository.insert_run_input_binding(row)

    def _load_task7_input_bindings(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[RunInputBindingRecord, ...]:
        return PostgresTask7RunGraphRepository(
            self._conn,
            self._tenant_id,
        ).list_run_input_bindings(deal_id=deal_id, run_id=run_id)

    @staticmethod
    def _validate_task7_input_binding_reload(
        *,
        expected: Sequence[RunInputBindingRecord],
        actual: Sequence[RunInputBindingRecord],
    ) -> None:
        if {row.binding_id: row for row in expected} != {row.binding_id: row for row in actual}:
            raise ValueError("persisted input bindings differ from derivation")

    @staticmethod
    def _admission_selector_predicate(
        node: object,
        *,
        row: Mapping[str, object],
        scope: Mapping[str, object],
    ) -> bool:
        if not isinstance(node, tuple) or not node:
            raise ValueError("admission selector predicate is malformed")
        operator = node[0]
        if operator == "AND" and len(node) > 1:
            return all(
                PostgresStrictPreflightProofsRepository._admission_selector_predicate(
                    child,
                    row=row,
                    scope=scope,
                )
                for child in node[1:]
            )
        if operator == "EQ" and len(node) == 3:
            column = node[1]
            expected: object = node[2]
            if not isinstance(column, str):
                raise ValueError("admission selector column is malformed")
            if isinstance(expected, str) and expected.startswith(":"):
                expected = scope.get(expected[1:])
            if isinstance(expected, UUID):
                expected = str(expected)
            return row.get(column) == expected
        if operator == "IN_ORDERED" and len(node) == 3:
            column = node[1]
            values = node[2]
            if not isinstance(column, str) or not isinstance(values, tuple):
                raise ValueError("admission selector set is malformed")
            return row.get(column) in values
        raise ValueError("admission selector operator is unavailable")

    def _derive_task7_admission_edges(
        self,
        *,
        deal_id: str,
        run_id: str,
        bindings: Sequence[RunInputBindingRecord],
    ) -> tuple[RunExpectedConsumerEdgeRecord, ...]:
        """Derive E001/E002 only through generated selector/rule/route bytes."""

        tenant_uuid = UUID(_canonical_uuid_text(self._tenant_id))
        deal_uuid = UUID(_canonical_uuid_text(deal_id))
        run_uuid = UUID(_canonical_uuid_text(run_id))
        source_rows = tuple(
            {
                "binding_id": UUID(row.binding_id),
                "tenant_id": row.tenant_id,
                "deal_id": row.deal_id,
                "run_id": row.run_id,
                "source_object_kind": row.source_object_kind,
                "selected_identity_sha256": row.selected_identity_sha256,
                "binding_sha256": row.binding_sha256,
            }
            for row in bindings
        )

        def row_loader(
            spec: ProducerSelectorSpec,
            **selector_scope: object,
        ) -> Sequence[Mapping[str, object]]:
            if spec.rule_id not in _ADMISSION_EDGE_RULES:
                return ()
            if (
                spec.relations != ("run_input_bindings",)
                or spec.joins
                or spec.producer_type != "INPUT_BINDING"
                or spec.projection
                != (
                    "INPUT_BINDING",
                    "binding_id",
                    "selected_identity_sha256",
                    "binding_sha256",
                )
                or spec.producer_condition != "ALWAYS"
            ):
                raise ValueError("generated admission selector shape drifted")
            selected = [
                row
                for row in source_rows
                if self._admission_selector_predicate(
                    spec.predicate_ast,
                    row=row,
                    scope=selector_scope,
                )
            ]
            return tuple(
                sorted(
                    selected,
                    key=lambda row: tuple(str(row.get(column)) for column in spec.order_by),
                )
            )

        producers = derive_rule_bound_producers(
            ProducerSelectionRequest(
                tenant_id=tenant_uuid,
                deal_id=deal_uuid,
                run_id=run_uuid,
            ),
            row_loader=row_loader,
        )
        if (
            not producers
            or {row.rule_id for row in producers} != _ADMISSION_EDGE_RULES
            or len(producers) != len(bindings)
        ):
            raise ValueError("admission producer projection is incomplete")
        edges = derive_expected_edges(
            EdgeDerivationRequest(
                tenant_id=tenant_uuid,
                deal_id=deal_uuid,
                run_id=run_uuid,
                pipeline_contract_version="full-strict-v21",
                pipeline_contract_hash=(
                    self._pipeline_contract_registry["full-strict-v21"].contract_hash or ""
                ),
                producers=producers,
            )
        )
        rows = tuple(
            RunExpectedConsumerEdgeRecord(
                expected_edge_id=str(edge.expected_edge_id),
                tenant_id=str(edge.tenant_id),
                deal_id=str(edge.deal_id),
                run_id=str(edge.run_id),
                producer_input_binding_id=str(edge.producer.producer_id),
                producer_output_object_id=None,
                consumer_step_name=edge.consumer_step_name,
                consumer_subrole=edge.consumer_subrole,
                usage_kind=edge.usage_kind,
                applicability_decision_id=None,
                contract_rule_id=edge.contract_rule_id,
                contract_rule_version=edge.contract_rule_version,
                contract_rule_hash=edge.contract_rule_hash,
                edge_tuple_sha256=edge.edge_tuple_sha256,
                materialization_origin="ADMISSION",
                lease_owner_id=None,
                lease_fencing_token=0,
            )
            for edge in edges
        )
        if not rows or {row.contract_rule_id for row in rows} != _ADMISSION_EDGE_RULES:
            raise ValueError("admission edge derivation is incomplete")
        return rows

    def _insert_task7_admission_edges(
        self,
        *,
        rows: tuple[RunExpectedConsumerEdgeRecord, ...],
    ) -> None:
        repository = PostgresTask7RunGraphRepository(
            self._conn,
            self._tenant_id,
        )
        for row in rows:
            repository.insert_run_expected_consumer_edge(row)

    def _load_task7_admission_edges(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> tuple[RunExpectedConsumerEdgeRecord, ...]:
        rows = PostgresTask7RunGraphRepository(
            self._conn,
            self._tenant_id,
        ).list_run_expected_consumer_edges(
            deal_id=deal_id,
            run_id=run_id,
        )
        return tuple(row for row in rows if row.materialization_origin == "ADMISSION")

    @staticmethod
    def _validate_task7_admission_edge_reload(
        *,
        expected: Sequence[RunExpectedConsumerEdgeRecord],
        actual: Sequence[RunExpectedConsumerEdgeRecord],
    ) -> None:
        if {row.expected_edge_id: row for row in expected} != {
            row.expected_edge_id: row for row in actual
        }:
            raise ValueError("persisted admission edges differ from derivation")

    def _load_task7_run_for_worker(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        row = self._conn.execute(
            text(
                """
                SELECT status, pipeline_contract_version,
                       pipeline_contract_hash, pipeline_resume_state
                FROM runs
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
            },
        ).one_or_none()
        if row is None:
            raise StrictPreflightProofError("STEP_REHYDRATION_FAILED")
        return {
            "status": str(_row_value(row, "status")),
            "pipeline_contract_version": str(_row_value(row, "pipeline_contract_version")),
            "pipeline_contract_hash": str(_row_value(row, "pipeline_contract_hash")),
            "pipeline_resume_state": str(_row_value(row, "pipeline_resume_state")),
        }

    def _load_task7_source_roots_read_only(
        self,
        *,
        deal_id: str,
        package_ids: tuple[str, ...],
    ) -> tuple[dict[str, Any], ...]:
        """Reload a BOUND source graph without acquiring or changing locks."""

        package_rows = self._conn.execute(
            text(
                """
                SELECT package_id, status, source_binding_state,
                       source_lineage_object_id
                FROM data_room_packages
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND package_id = ANY(CAST(:package_ids AS uuid[]))
                ORDER BY package_id
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "package_ids": list(package_ids),
            },
        ).fetchall()
        if len(package_rows) != len(package_ids):
            raise StrictPreflightProofError("STEP_REHYDRATION_FAILED")
        package_root_ids: dict[str, str] = {}
        for row in package_rows:
            package_id = _canonical_uuid_text(_row_value(row, "package_id"))
            if (
                _row_value(row, "status") != "SEALED"
                or _row_value(row, "source_binding_state") != "BOUND"
            ):
                raise StrictPreflightProofError("STEP_REHYDRATION_FAILED")
            package_root_ids[package_id] = _canonical_uuid_text(
                _row_value(row, "source_lineage_object_id")
            )

        rows = self._conn.execute(
            text(
                """
                SELECT source_lineage_object_id, tenant_id, deal_id,
                       corpus_receipt_id, object_kind, source_version,
                       source_disposition, object_identity_sha256,
                       content_sha256, corpus_receipt_sha256
                FROM source_lineage_objects
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND corpus_receipt_id = ANY(CAST(:package_ids AS uuid[]))
                ORDER BY corpus_receipt_id, object_kind,
                         source_lineage_object_id
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "package_ids": list(package_ids),
            },
        ).fetchall()
        roots = tuple(
            {
                "source_lineage_object_id": _canonical_uuid_text(
                    _row_value(row, "source_lineage_object_id")
                ),
                "tenant_id": _canonical_uuid_text(_row_value(row, "tenant_id")),
                "deal_id": _canonical_uuid_text(_row_value(row, "deal_id")),
                "corpus_receipt_id": _canonical_uuid_text(_row_value(row, "corpus_receipt_id")),
                "object_kind": str(_row_value(row, "object_kind")),
                "source_version": str(_row_value(row, "source_version")),
                "source_disposition": str(_row_value(row, "source_disposition")),
                "object_identity_sha256": str(_row_value(row, "object_identity_sha256")),
                "content_sha256": str(_row_value(row, "content_sha256")),
                "corpus_receipt_sha256": str(_row_value(row, "corpus_receipt_sha256")),
            }
            for row in rows
        )
        package_roots = {
            row["corpus_receipt_id"]: row["source_lineage_object_id"]
            for row in roots
            if row["object_kind"] == "PACKAGE"
        }
        if package_roots != package_root_ids:
            raise StrictPreflightProofError("STEP_REHYDRATION_FAILED")
        return roots

    def require_task7_admission_graph_for_run(
        self,
        *,
        deal_id: str,
        run_id: str,
    ) -> None:
        """Read-only worker proof of BOUND sources, bindings, and E001/E002."""

        try:
            run = self._load_task7_run_for_worker(
                deal_id=deal_id,
                run_id=run_id,
            )
            contract = self._pipeline_contract_registry["full-strict-v21"]
            if (
                run["status"] != "RUNNING"
                or run["pipeline_contract_version"] != "full-strict-v21"
                or run["pipeline_contract_hash"] != contract.contract_hash
                or run["pipeline_resume_state"] != "RESUMABLE"
            ):
                raise ValueError("worker run is outside strict v7")
            bindings = self._load_task7_input_bindings(
                deal_id=deal_id,
                run_id=run_id,
            )
            package_ids = tuple(
                sorted(
                    row.corpus_receipt_id for row in bindings if row.source_object_kind == "PACKAGE"
                )
            )
            if not package_ids or len(set(package_ids)) != len(package_ids):
                raise ValueError("worker input package census is invalid")
            roots = self._load_task7_source_roots_read_only(
                deal_id=deal_id,
                package_ids=package_ids,
            )
            expected_bindings = self._stage_task7_input_bindings(
                contract=contract,
                deal_id=deal_id,
                run_id=run_id,
                source_roots=roots,
            )
            self._validate_task7_input_binding_reload(
                expected=expected_bindings,
                actual=bindings,
            )
            expected_edges = self._derive_task7_admission_edges(
                deal_id=deal_id,
                run_id=run_id,
                bindings=bindings,
            )
            actual_edges = self._load_task7_admission_edges(
                deal_id=deal_id,
                run_id=run_id,
            )
            self._validate_task7_admission_edge_reload(
                expected=expected_edges,
                actual=actual_edges,
            )
        except StrictPreflightProofError:
            raise
        except (
            EdgeDerivationError,
            KeyError,
            PipelineContractError,
            Task7RunGraphRepositoryError,
            Task7UuidError,
            TypeError,
            ValueError,
        ) as exc:
            raise StrictPreflightProofError("STEP_REHYDRATION_FAILED") from exc

    def _stage_task7_steps(
        self,
        *,
        contract: PipelineContractSpec,
        deal_id: str,
        run_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Build the exact immutable step set before admission DML begins."""
        staged: list[dict[str, Any]] = []
        contract_hash = contract.contract_hash
        if contract_hash is None:
            raise PipelineContractError(
                "PIPELINE_CONTRACT_HASH_MISMATCH",
                "strict contract has no immutable hash",
            )
        for step in validate_admission_step_rows(contract=contract, rows=contract.steps):
            identity_values = {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "pipeline_contract_version": contract.contract_version,
                "pipeline_contract_hash": contract_hash,
                "step_name": step.step_name,
                "step_order": step.step_order,
            }
            step_id = derive_step_id_for_admission(
                pipeline_contract_version=contract.contract_version,
                values=identity_values,
            ).value
            staged.append(
                {
                    "step_id": str(step_id),
                    "tenant_id": self._tenant_id,
                    "run_id": run_id,
                    "step_name": step.step_name,
                    "step_order": step.step_order,
                    "status": "PENDING",
                    "pipeline_contract_version": contract.contract_version,
                    "pipeline_contract_hash": contract_hash,
                    "semantic_rule_version": step.semantic_rule_version,
                    "semantic_rule_hash": step.semantic_rule_hash,
                }
            )
        return tuple(staged)

    def _insert_task7_run(
        self,
        *,
        run_id: str,
        deal_id: str,
        mode: str,
        idempotency_key: str | None,
        source: dict[str, Any] | None,
        created_by_actor_id: str | None,
        created_by_actor_type: str | None,
        strict_preflight_proof_id: str,
        pipeline_contract_version: str,
        pipeline_contract_hash: str,
        pipeline_resume_state: str,
        pipeline_resume_error_code: str | None,
    ) -> dict[str, Any]:
        """Insert the contract-bearing run inside the proof transaction."""
        now = datetime.now(UTC)
        try:
            self._conn.execute(
                text(
                    """
                    INSERT INTO runs
                        (run_id, tenant_id, deal_id, mode, status, started_at,
                         idempotency_key, source, created_at,
                         created_by_actor_id, created_by_actor_type,
                         strict_preflight_proof_id, pipeline_contract_version,
                         pipeline_contract_hash, pipeline_resume_state,
                         pipeline_resume_error_code)
                    VALUES
                        (:run_id, :tenant_id, :deal_id, :mode, 'QUEUED', :started_at,
                         :idempotency_key, CAST(:source AS JSONB), :created_at,
                         :created_by_actor_id, :created_by_actor_type,
                         :strict_preflight_proof_id, :pipeline_contract_version,
                         :pipeline_contract_hash, :pipeline_resume_state,
                         :pipeline_resume_error_code)
                    """
                ),
                {
                    "run_id": run_id,
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "mode": mode,
                    "started_at": now,
                    "idempotency_key": idempotency_key,
                    "source": json.dumps(source) if source is not None else None,
                    "created_at": now,
                    "created_by_actor_id": created_by_actor_id,
                    "created_by_actor_type": created_by_actor_type,
                    "strict_preflight_proof_id": strict_preflight_proof_id,
                    "pipeline_contract_version": pipeline_contract_version,
                    "pipeline_contract_hash": pipeline_contract_hash,
                    "pipeline_resume_state": pipeline_resume_state,
                    "pipeline_resume_error_code": pipeline_resume_error_code,
                },
            )
        except IntegrityError as exc:
            constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint_name == _ACTIVE_RUN_INDEX or _ACTIVE_RUN_INDEX in str(exc.orig or exc):
                raise RunAlreadyActiveError(deal_id) from exc
            raise
        iso_now = now.isoformat().replace("+00:00", "Z")
        return {
            "run_id": run_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "mode": mode,
            "status": "QUEUED",
            "started_at": iso_now,
            "finished_at": None,
            "source": source,
            "created_at": iso_now,
            "created_by_actor_id": created_by_actor_id,
            "created_by_actor_type": created_by_actor_type,
            "strict_preflight_proof_id": strict_preflight_proof_id,
            "pipeline_contract_version": pipeline_contract_version,
            "pipeline_contract_hash": pipeline_contract_hash,
            "pipeline_resume_state": pipeline_resume_state,
            "pipeline_resume_error_code": pipeline_resume_error_code,
        }

    def _insert_task7_steps(self, *, rows: tuple[dict[str, Any], ...]) -> None:
        """Insert every canonical step as one executemany operation."""
        result = self._conn.execute(
            text(
                """
                INSERT INTO run_steps
                    (step_id, tenant_id, run_id, step_name, step_order, status,
                     pipeline_contract_version, pipeline_contract_hash,
                     semantic_rule_version, semantic_rule_hash)
                VALUES
                    (CAST(:step_id AS uuid), CAST(:tenant_id AS uuid),
                     CAST(:run_id AS uuid), :step_name, :step_order, :status,
                     :pipeline_contract_version, :pipeline_contract_hash,
                     :semantic_rule_version, :semantic_rule_hash)
                """
            ),
            list(rows),
        )
        if result.rowcount not in {-1, len(rows)}:
            raise StrictPreflightProofError("PIPELINE_CONTRACT_HASH_MISMATCH")

    def _load_task7_steps(self, *, run_id: str) -> tuple[dict[str, Any], ...]:
        """Reload canonical steps with a unique total ordering."""
        result = self._conn.execute(
            text(
                """
                SELECT step_id, tenant_id, run_id, step_name, step_order, status,
                       pipeline_contract_version, pipeline_contract_hash,
                       semantic_rule_version, semantic_rule_hash
                FROM run_steps
                WHERE tenant_id = :tenant_id AND run_id = :run_id
                ORDER BY step_order, step_id
                """
            ),
            {"tenant_id": self._tenant_id, "run_id": run_id},
        )
        return tuple(
            {
                "step_id": str(_row_value(row, "step_id")),
                "tenant_id": str(_row_value(row, "tenant_id")),
                "run_id": str(_row_value(row, "run_id")),
                "step_name": str(_row_value(row, "step_name")),
                "step_order": int(_row_value(row, "step_order")),
                "status": str(_row_value(row, "status")),
                "pipeline_contract_version": str(_row_value(row, "pipeline_contract_version")),
                "pipeline_contract_hash": str(_row_value(row, "pipeline_contract_hash")),
                "semantic_rule_version": str(_row_value(row, "semantic_rule_version")),
                "semantic_rule_hash": str(_row_value(row, "semantic_rule_hash")),
            }
            for row in result.fetchall()
        )

    def _validate_task7_step_reload(
        self,
        *,
        contract: PipelineContractSpec,
        deal_id: str,
        run_id: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        """Require the committed row shape to equal the immutable code registry."""
        normalized = tuple(rows)
        specs = tuple(
            PipelineStepSpec(
                step_order=int(row["step_order"]),
                step_name=str(row["step_name"]),
                semantic_rule_version=str(row["semantic_rule_version"]),
                semantic_rule_hash=str(row["semantic_rule_hash"]),
            )
            for row in normalized
        )
        validate_admission_step_rows(contract=contract, rows=specs)
        expected = self._stage_task7_steps(
            contract=contract,
            deal_id=deal_id,
            run_id=run_id,
        )
        if tuple(dict(row) for row in normalized) != expected:
            raise PipelineContractError(
                "PIPELINE_CONTRACT_HASH_MISMATCH",
                "persisted admission steps differ from the reviewed contract",
            )

    def consume_and_create_run(
        self,
        *,
        proof_id: str,
        expected_binding: StrictPreflightBinding,
        run_id: str,
        deal_id: str,
        mode: str,
        idempotency_key: str | None,
        source: dict[str, Any] | None,
        created_by_actor_id: str | None,
        created_by_actor_type: str | None,
    ) -> dict[str, Any]:
        """Lock and validate a proof, create its run, then consume it atomically."""
        try:
            proof_id = str(uuid.UUID(proof_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_INVALID") from exc
        if not isinstance(expected_binding, StrictPreflightBinding):
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")

        with self._conn.begin_nested():
            record = self._load_for_update(proof_id)
            self._validate_for_consumption(
                record,
                expected_binding=expected_binding,
                deal_id=deal_id,
            )
            run = self._insert_run(
                run_id=run_id,
                deal_id=deal_id,
                mode=mode,
                idempotency_key=idempotency_key,
                source=source,
                created_by_actor_id=created_by_actor_id,
                created_by_actor_type=created_by_actor_type,
                strict_preflight_proof_id=proof_id,
            )
            if not self._mark_consumed(proof_id=proof_id, run_id=run_id):
                raise StrictPreflightProofError(self._consumption_failure_code(proof_id))
            return run

    def require_consumed_for_run(
        self,
        *,
        proof_id: str,
        run_id: str,
        deal_id: str,
        strict_profile_generation: str | None = None,
        strict_profile_hmac: str | None = None,
    ) -> None:
        """Require the durable reciprocal proof/run link before worker execution.

        The worker does not trust the proof ID copied from its claimed run row.
        This query requires the proof to have been consumed by that exact run and
        requires the run to point back to the same proof under the same tenant and
        deal.  Missing and cross-tenant rows deliberately share a leakage-safe code.
        """
        if (strict_profile_generation is None) != (strict_profile_hmac is None):
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")
        try:
            proof_id = str(uuid.UUID(str(proof_id)))
            run_id = str(uuid.UUID(str(run_id)))
            deal_id = str(uuid.UUID(str(deal_id)))
        except (AttributeError, TypeError, ValueError) as exc:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_INVALID") from exc

        result = self._conn.execute(
            text(
                """
                SELECT p.proof_id
                FROM strict_preflight_proofs AS p
                JOIN runs AS r
                  ON r.tenant_id = p.tenant_id
                 AND r.deal_id = p.deal_id
                 AND r.strict_preflight_proof_id = p.proof_id
                WHERE p.tenant_id = :tenant_id
                  AND p.proof_id = :proof_id
                  AND p.deal_id = :deal_id
                  AND p.consumed_at IS NOT NULL
                  AND p.consumed_by_run_id = :run_id
                  AND r.run_id = :run_id
                  AND (
                      :strict_profile_generation IS NULL
                      OR (
                          p.strict_profile_generation = :strict_profile_generation
                          AND p.strict_profile_hmac = :strict_profile_hmac
                      )
                  )
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "proof_id": proof_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "strict_profile_generation": strict_profile_generation,
                "strict_profile_hmac": strict_profile_hmac,
            },
        )
        if result.fetchone() is None:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")

    def _validate_for_consumption(
        self,
        record: StrictPreflightProofRecord | None,
        *,
        expected_binding: StrictPreflightBinding,
        deal_id: str,
    ) -> None:
        if record is None or record.binding.tenant_id != self._tenant_id:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_INVALID")

        stored_digest = record.binding.digest()
        if record.binding_hash != stored_digest:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_INVALID")

        if record.consumed_at is not None or record.consumed_by_run_id is not None:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_ALREADY_CONSUMED")

        if record.expired_at_lock:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_EXPIRED")

        if (
            expected_binding.tenant_id != self._tenant_id
            or expected_binding.deal_id != deal_id
            or record.binding.deal_id != deal_id
            or expected_binding.digest() != record.binding_hash
        ):
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")

    def _load_for_update(self, proof_id: str) -> StrictPreflightProofRecord | None:
        row = self._conn.execute(
            text(
                """
                SELECT proof_id, tenant_id, deal_id, corpus_hash,
                       strict_profile_generation, strict_profile_hmac,
                       pipeline_contract_version, pipeline_contract_hash,
                       expected_consumer_edge_rule_hash, static_decision_set_hash,
                       methodology_version_hashes, promoted_pointer_hash,
                       provider_receipt_set_hash, dependency_receipt_set_hash,
                       binding_hash, created_at, expires_at, consumed_at,
                       consumed_by_run_id,
                       expires_at <= statement_timestamp() AS expired_at_lock
                FROM strict_preflight_proofs
                WHERE tenant_id = :tenant_id AND proof_id = :proof_id
                FOR UPDATE
                """
            ),
            {"tenant_id": self._tenant_id, "proof_id": proof_id},
        ).fetchone()
        if row is None:
            return None

        binding = StrictPreflightBinding(
            tenant_id=str(_row_value(row, "tenant_id")),
            deal_id=str(_row_value(row, "deal_id")),
            corpus_hash=str(_row_value(row, "corpus_hash")),
            strict_profile_generation=str(_row_value(row, "strict_profile_generation")),
            strict_profile_hmac=str(_row_value(row, "strict_profile_hmac")),
            pipeline_contract_version=str(_row_value(row, "pipeline_contract_version")),
            pipeline_contract_hash=str(_row_value(row, "pipeline_contract_hash")),
            expected_consumer_edge_rule_hash=str(
                _row_value(row, "expected_consumer_edge_rule_hash")
            ),
            static_decision_set_hash=str(_row_value(row, "static_decision_set_hash")),
            methodology_version_hashes=_methodology_hashes(
                _row_value(row, "methodology_version_hashes")
            ),
            promoted_pointer_hash=str(_row_value(row, "promoted_pointer_hash")),
            provider_receipt_set_hash=str(_row_value(row, "provider_receipt_set_hash")),
            dependency_receipt_set_hash=str(_row_value(row, "dependency_receipt_set_hash")),
        )
        return StrictPreflightProofRecord(
            proof_id=str(_row_value(row, "proof_id")),
            binding=binding,
            binding_hash=str(_row_value(row, "binding_hash")),
            created_at=_row_value(row, "created_at"),
            expires_at=_row_value(row, "expires_at"),
            consumed_at=_row_value(row, "consumed_at"),
            consumed_by_run_id=_optional_string(_row_value(row, "consumed_by_run_id")),
            expired_at_lock=bool(_row_value(row, "expired_at_lock")),
        )

    def _insert_run(
        self,
        *,
        run_id: str,
        deal_id: str,
        mode: str,
        idempotency_key: str | None,
        source: dict[str, Any] | None,
        created_by_actor_id: str | None,
        created_by_actor_type: str | None,
        strict_preflight_proof_id: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        try:
            self._conn.execute(
                text(
                    """
                    INSERT INTO runs
                        (run_id, tenant_id, deal_id, mode, status, started_at,
                         idempotency_key, source, created_at,
                         created_by_actor_id, created_by_actor_type,
                         strict_preflight_proof_id)
                    VALUES
                        (:run_id, :tenant_id, :deal_id, :mode, 'QUEUED', :started_at,
                         :idempotency_key, CAST(:source AS JSONB), :created_at,
                         :created_by_actor_id, :created_by_actor_type,
                         :strict_preflight_proof_id)
                    """
                ),
                {
                    "run_id": run_id,
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "mode": mode,
                    "started_at": now,
                    "idempotency_key": idempotency_key,
                    "source": json.dumps(source) if source is not None else None,
                    "created_at": now,
                    "created_by_actor_id": created_by_actor_id,
                    "created_by_actor_type": created_by_actor_type,
                    "strict_preflight_proof_id": strict_preflight_proof_id,
                },
            )
        except IntegrityError as exc:
            constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint_name == _ACTIVE_RUN_INDEX or _ACTIVE_RUN_INDEX in str(exc.orig or exc):
                raise RunAlreadyActiveError(deal_id) from exc
            raise

        iso_now = now.isoformat().replace("+00:00", "Z")
        return {
            "run_id": run_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "mode": mode,
            "status": "QUEUED",
            "started_at": iso_now,
            "finished_at": None,
            "source": source,
            "created_at": iso_now,
            "created_by_actor_id": created_by_actor_id,
            "created_by_actor_type": created_by_actor_type,
            "strict_preflight_proof_id": strict_preflight_proof_id,
        }

    def _mark_consumed(self, *, proof_id: str, run_id: str) -> bool:
        result = self._conn.execute(
            text(
                """
                UPDATE strict_preflight_proofs
                SET consumed_at = statement_timestamp(), consumed_by_run_id = :run_id
                WHERE tenant_id = :tenant_id
                  AND proof_id = :proof_id
                  AND consumed_at IS NULL
                  AND consumed_by_run_id IS NULL
                  AND expires_at > CURRENT_TIMESTAMP
                  AND expires_at > statement_timestamp()
                RETURNING proof_id
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "proof_id": proof_id,
                "run_id": run_id,
            },
        )
        return result.fetchone() is not None

    def _consumption_failure_code(self, proof_id: str) -> str:
        """Classify a failed conditional consume while the proof lock is still held."""
        row = self._conn.execute(
            text(
                """
                SELECT consumed_at IS NOT NULL OR consumed_by_run_id IS NOT NULL AS consumed,
                       expires_at <= statement_timestamp() AS expired
                FROM strict_preflight_proofs
                WHERE tenant_id = :tenant_id AND proof_id = :proof_id
                """
            ),
            {"tenant_id": self._tenant_id, "proof_id": proof_id},
        ).fetchone()
        if row is None:
            return "STRICT_PREFLIGHT_PROOF_INVALID"
        if bool(_row_value(row, "consumed")):
            return "STRICT_PREFLIGHT_PROOF_ALREADY_CONSUMED"
        if bool(_row_value(row, "expired")):
            return "STRICT_PREFLIGHT_PROOF_EXPIRED"
        return "STRICT_PREFLIGHT_PROOF_INVALID"


def get_strict_preflight_proofs_repository(
    conn: Connection | None,
    tenant_id: str,
) -> PostgresStrictPreflightProofsRepository:
    """Return the durable proof repository; strict admission has no fallback."""
    if conn is None or not is_postgres_configured():
        raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_INVALID")
    return PostgresStrictPreflightProofsRepository(conn, tenant_id)


def _row_value(row: Any, key: str) -> Any:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key)


def _methodology_hashes(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = json.loads(value)
    return tuple(str(item) for item in value)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)
