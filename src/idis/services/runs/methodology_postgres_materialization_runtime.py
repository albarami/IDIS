"""Strict PostgreSQL composition for Task-6 canonical Steps 8-10.

All producers in this module are read-only and deterministic.  Their staged
writes are executed by the ordinary fenced run-step completion transaction, so
canonical rows, typed lineage links, audit, and the COMPLETED transition either
commit together or leave no partial step output.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Never

from idis.models.claim import Materiality
from idis.models.claim_materialization import (
    ClaimMaterializationStatus,
    MaterializedClaimSourceRef,
    MethodologyOutputClaimMapping,
    MethodologyOutputClaimMaterializationRunResult,
    MethodologyOutputClaimMaterializationSummary,
    RunScopedMaterializedClaimShell,
)
from idis.models.defect import CureProtocol, DefectSeverity, DefectStatus, DefectType
from idis.models.evidence_item import EvidenceItem, SourceGrade, VerificationStatus
from idis.models.evidence_item_materialization import (
    EvidenceItemMaterializationStatus,
    MethodologyEvidenceItemMapping,
    MethodologyEvidenceItemMaterializationRunResult,
    MethodologyEvidenceItemMaterializationSummary,
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceProvenanceRef,
    generate_methodology_evidence_item_id,
)
from idis.models.methodology_extraction import (
    CanonicalMethodologyClaimIdentity,
    MethodologyFactualOutput,
    MethodologyResultClaimLink,
    MethodologyResultDefectLink,
    MethodologyResultEvidenceLink,
    MethodologyResultSanadLink,
    generate_methodology_result_link_id,
)
from idis.models.sanad import SanadGrade
from idis.models.sanad_materialization import (
    MethodologySanadMapping,
    MethodologySanadMaterializationRunResult,
    RunScopedSanadDefectShell,
    RunScopedSanadGradeRecord,
    RunScopedSanadLinkRecord,
    RunScopedSanadRecord,
    RunScopedSanadShell,
)
from idis.models.value_structs import parse_value_struct
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.methodology_extraction import (
    DurableCanonicalMaterializationSnapshot,
    PostgresMethodologyExtractionRepository,
)
from idis.services.extraction.canonical_methodology_materialization import (
    CanonicalMethodologyMaterializationError,
    validate_canonical_materialization_completion,
)
from idis.services.runs.methodology_sanad_creation_linking_grading import (
    CanonicalRunMethodologySanadCreationLinkingGradingService,
)
from idis.services.runs.transaction_characteristics import (
    REPEATABLE_READ_WRITE,
    TransactionCharacteristics,
)


class CanonicalClaimClass(str):
    """String-compatible canonical claim class with the legacy ``.value`` seam."""

    @property
    def value(self) -> str:
        return str(self)


@dataclass(frozen=True, slots=True)
class CanonicalMethodologyRunClaim:
    """Downstream-compatible view of one factual-output-to-claim mapping."""

    claim_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    claim_text: str
    claim_type: CanonicalClaimClass
    value_struct: Any
    materiality: Materiality
    source_refs: tuple[MaterializedClaimSourceRef, ...]
    methodology_id: str
    methodology_version_id: str
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    status: str


@dataclass(frozen=True, slots=True)
class _ClaimSpec:
    row: Mapping[str, Any]
    output: MethodologyFactualOutput
    identity: CanonicalMethodologyClaimIdentity
    claim: CanonicalMethodologyRunClaim


@dataclass(frozen=True, slots=True)
class _EvidenceSpec:
    factual_row: Mapping[str, Any]
    claim_row: Mapping[str, Any]
    record: RunScopedEvidenceItemRecord
    locator_sha256: str


@dataclass(frozen=True, slots=True)
class _StagedClaimWrite:
    tenant_id: str
    deal_id: str
    run_id: str
    run_step_id: str
    lease_owner_id: str
    lease_fencing_token: int
    audit_sink: Any
    specs: tuple[_ClaimSpec, ...]
    factual_signature: tuple[tuple[str, str], ...]

    def persist(
        self,
        *,
        db_conn: Any,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        _require_lease(self, lease_owner_id, lease_fencing_token)
        repository = PostgresMethodologyExtractionRepository(db_conn, self.tenant_id)
        _require_factual_signature(repository, self.deal_id, self.run_id, self.factual_signature)
        links: list[MethodologyResultClaimLink] = []
        for spec in self.specs:
            winner, reused = repository.upsert_canonical_claim(
                deal_id=self.deal_id,
                run_id=self.run_id,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
                claim=_canonical_claim_payload(spec),
            )
            if str(winner.get("claim_id")) != spec.identity.claim_id:
                _fail()
            logical_link = {
                "tenant_id": self.tenant_id,
                "deal_id": self.deal_id,
                "run_id": self.run_id,
                "result_id": str(spec.row["result_id"]),
                "factual_output_record_id": str(spec.row["factual_output_record_id"]),
                "coverage_record_id": str(spec.row["coverage_record_id"]),
                "factual_output_id": str(spec.output.factual_output_id),
                "claim_id": spec.identity.claim_id,
                "canonical_key_sha256": spec.identity.canonical_key_sha256,
                "reused_existing_claim": bool(reused),
            }
            derived_fields = {
                "selection_id": str(spec.row["selection_id"]),
                "methodology_version_record_id": str(spec.row["methodology_version_record_id"]),
                "methodology_question_record_id": str(spec.row["methodology_question_record_id"]),
            }
            links.append(
                MethodologyResultClaimLink.model_validate(
                    {
                        "claim_link_id": generate_methodology_result_link_id(
                            table="methodology_result_claim_links",
                            logical_record=logical_link,
                            derived_fields=derived_fields,
                        ),
                        **logical_link,
                    }
                )
            )
        if links:
            repository.persist_claim_links(
                deal_id=self.deal_id,
                run_id=self.run_id,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
                records=links,
            )
        snapshot = repository.load_canonical_materialization_snapshot(
            deal_id=self.deal_id, run_id=self.run_id
        )
        _validate_claim_snapshot(snapshot)
        _emit_audit(
            db_conn,
            self.audit_sink,
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            event_type="deal.run.methodology_claims.materialized",
            run_step_id=self.run_step_id,
            count=len(self.specs),
        )


@dataclass(frozen=True, slots=True)
class _StagedEvidenceWrite:
    tenant_id: str
    deal_id: str
    run_id: str
    run_step_id: str
    lease_owner_id: str
    lease_fencing_token: int
    audit_sink: Any
    specs: tuple[_EvidenceSpec, ...]
    factual_signature: tuple[tuple[str, str], ...]

    def persist(
        self,
        *,
        db_conn: Any,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        _require_lease(self, lease_owner_id, lease_fencing_token)
        repository = PostgresMethodologyExtractionRepository(db_conn, self.tenant_id)
        _require_factual_signature(repository, self.deal_id, self.run_id, self.factual_signature)
        grouped_links: dict[str, list[MethodologyResultEvidenceLink]] = defaultdict(list)
        for spec in self.specs:
            item = spec.record.evidence_item
            inserted = repository.insert_canonical_evidence_item(
                deal_id=self.deal_id,
                run_id=self.run_id,
                task_record_id=str(spec.factual_row["task_record_id"]),
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
                evidence_item=item,
                claim_id=spec.record.claim_id,
            )
            if str(inserted.get("evidence_id")) != item.evidence_id:
                _fail()
            logical_link = {
                "tenant_id": self.tenant_id,
                "deal_id": self.deal_id,
                "run_id": self.run_id,
                "result_id": str(spec.factual_row["result_id"]),
                "factual_output_record_id": str(spec.factual_row["factual_output_record_id"]),
                "claim_link_id": str(spec.claim_row["claim_link_id"]),
                "factual_output_id": spec.record.extraction_output_id,
                "evidence_id": item.evidence_id,
                "document_id": spec.record.source_ref.document_id,
                "source_span_id": spec.record.source_ref.source_span_id,
            }
            grouped_links[str(spec.factual_row["task_record_id"])].append(
                MethodologyResultEvidenceLink.model_validate(
                    {
                        "evidence_link_id": generate_methodology_result_link_id(
                            table="methodology_result_evidence_links",
                            logical_record=logical_link,
                            derived_fields={"locator_sha256": spec.locator_sha256},
                        ),
                        **logical_link,
                    }
                )
            )
        for task_record_id, records in sorted(grouped_links.items()):
            repository.persist_evidence_links(
                deal_id=self.deal_id,
                run_id=self.run_id,
                task_record_id=task_record_id,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
                records=records,
            )
        snapshot = repository.load_canonical_materialization_snapshot(
            deal_id=self.deal_id, run_id=self.run_id
        )
        _validate_evidence_snapshot(snapshot)
        _emit_audit(
            db_conn,
            self.audit_sink,
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            event_type="deal.run.methodology_evidence.materialized",
            run_step_id=self.run_step_id,
            count=len(self.specs),
        )


@dataclass(frozen=True, slots=True)
class _StagedSanadWrite:
    transaction_characteristics: ClassVar[TransactionCharacteristics] = REPEATABLE_READ_WRITE
    tenant_id: str
    deal_id: str
    run_id: str
    run_step_id: str
    lease_owner_id: str
    lease_fencing_token: int
    audit_sink: Any
    records: tuple[RunScopedSanadRecord, ...]
    grades: tuple[RunScopedSanadGradeRecord, ...]
    defects: tuple[Any, ...]
    factual_signature: tuple[tuple[str, str], ...]

    def persist(
        self,
        *,
        db_conn: Any,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        _require_lease(self, lease_owner_id, lease_fencing_token)
        repository = PostgresMethodologyExtractionRepository(db_conn, self.tenant_id)
        _require_factual_signature(repository, self.deal_id, self.run_id, self.factual_signature)
        snapshot = repository.load_canonical_materialization_snapshot(
            deal_id=self.deal_id, run_id=self.run_id
        )
        claim_by_output = _one_by_output(snapshot.claims, snapshot.factual_outputs)
        grade_by_sanad = {grade.sanad_id: grade for grade in self.grades}
        sanad_links: list[MethodologyResultSanadLink] = []
        record_by_sanad: dict[str, RunScopedSanadRecord] = {}
        for record in self.records:
            claim_row = claim_by_output.get(record.extraction_output_id)
            grade = grade_by_sanad.get(record.sanad.sanad_id)
            if claim_row is None or grade is None:
                _fail()
            repository.insert_canonical_sanad(
                deal_id=self.deal_id,
                run_id=self.run_id,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
                sanad=_canonical_sanad_payload(record, grade),
            )
            record_by_sanad[record.sanad.sanad_id] = record
            logical_link = {
                "tenant_id": self.tenant_id,
                "deal_id": self.deal_id,
                "run_id": self.run_id,
                "result_id": str(claim_row["result_id"]),
                "factual_output_record_id": str(claim_row["factual_output_record_id"]),
                "claim_link_id": str(claim_row["claim_link_id"]),
                "factual_output_id": record.extraction_output_id,
                "sanad_id": record.sanad.sanad_id,
                "primary_evidence_id": record.sanad.primary_evidence_id,
                "grade": grade.sanad_grade.value,
                "defect_ids": tuple(sorted(grade.defect_ids)),
            }
            sanad_links.append(
                MethodologyResultSanadLink.model_validate(
                    {
                        "sanad_link_id": generate_methodology_result_link_id(
                            table="methodology_result_sanad_links",
                            logical_record=logical_link,
                        ),
                        **logical_link,
                    }
                )
            )
        if sanad_links:
            repository.persist_sanad_links(
                deal_id=self.deal_id,
                run_id=self.run_id,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
                records=sanad_links,
            )
        after_sanads = repository.load_canonical_materialization_snapshot(
            deal_id=self.deal_id, run_id=self.run_id
        )
        sanad_link_by_sanad = {str(row["sanad_id"]): row for row in after_sanads.sanads}
        defect_links: list[MethodologyResultDefectLink] = []
        for defect_record in self.defects:
            defect = defect_record.defect
            sanad_row = sanad_link_by_sanad.get(str(defect_record.sanad_id))
            sanad_record = record_by_sanad.get(str(defect_record.sanad_id))
            if sanad_row is None or sanad_record is None:
                _fail()
            repository.insert_canonical_defect(
                deal_id=self.deal_id,
                run_id=self.run_id,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
                defect={
                    **defect.model_dump(mode="json"),
                    "claim_id": sanad_record.claim_id,
                },
            )
            logical_link = {
                "tenant_id": self.tenant_id,
                "deal_id": self.deal_id,
                "run_id": self.run_id,
                "result_id": str(sanad_row["result_id"]),
                "factual_output_record_id": str(sanad_row["factual_output_record_id"]),
                "sanad_link_id": str(sanad_row["sanad_link_id"]),
                "defect_id": defect.defect_id,
            }
            defect_links.append(
                MethodologyResultDefectLink.model_validate(
                    {
                        "defect_link_id": generate_methodology_result_link_id(
                            table="methodology_result_defect_links",
                            logical_record=logical_link,
                        ),
                        **logical_link,
                    }
                )
            )
        if defect_links:
            repository.persist_defect_links(
                deal_id=self.deal_id,
                run_id=self.run_id,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
                records=defect_links,
            )
        completed = repository.load_canonical_materialization_snapshot(
            deal_id=self.deal_id, run_id=self.run_id
        )
        _validate_complete_snapshot(completed, rejected_count=0)
        repository.finalize_canonical_coverage_exact_set(
            deal_id=self.deal_id,
            run_id=self.run_id,
            run_step_id=self.run_step_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        _emit_audit(
            db_conn,
            self.audit_sink,
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            event_type="deal.run.methodology_sanads.materialized",
            run_step_id=self.run_step_id,
            count=len(self.records),
        )


class PostgresCanonicalMethodologyMaterializationBoundary:
    """Read-only producers plus completion-transaction staged writers."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        tenant_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        audit_sink: Any,
    ) -> None:
        self._connection_factory = connection_factory
        self._tenant_id = tenant_id
        self._lease_owner_id = lease_owner_id
        self._lease_fencing_token = lease_fencing_token
        self._audit_sink = audit_sink

    def prepare_claim_materialization(self, **values: Any) -> tuple[Any, tuple[Any, ...], Any]:
        scope = _scope(values, self._tenant_id)
        rows, _snapshot = self._load(scope)
        specs = tuple(_claim_spec(scope, row) for row in rows)
        claims = tuple(spec.claim for spec in specs)
        result = _claim_result(scope, claims)
        return (
            result,
            claims,
            _StagedClaimWrite(
                **scope,
                run_step_id=str(values["run_step_id"]),
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
                audit_sink=self._audit_sink,
                specs=specs,
                factual_signature=_factual_signature(rows),
            ),
        )

    def prepare_evidence_materialization(
        self, **values: Any
    ) -> tuple[Any, tuple[RunScopedEvidenceItemRecord, ...], Any]:
        scope = _scope(values, self._tenant_id)
        rows, snapshot = self._load(scope)
        _validate_claim_snapshot(snapshot)
        claims_by_record = {str(row["factual_output_record_id"]): row for row in snapshot.claims}
        specs: list[_EvidenceSpec] = []
        for factual in rows:
            claim_row = claims_by_record.get(str(factual["factual_output_record_id"]))
            if claim_row is None:
                _fail()
            specs.extend(_evidence_specs(scope, factual, claim_row))
        records = tuple(spec.record for spec in specs)
        result = _evidence_result(scope, records, claim_count=len(snapshot.claims))
        return (
            result,
            records,
            _StagedEvidenceWrite(
                **scope,
                run_step_id=str(values["run_step_id"]),
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
                audit_sink=self._audit_sink,
                specs=tuple(specs),
                factual_signature=_factual_signature(rows),
            ),
        )

    def prepare_sanad_materialization(self, **values: Any) -> tuple[Any, ...]:
        scope = _scope(values, self._tenant_id)
        rows, snapshot = self._load(scope)
        _validate_evidence_snapshot(snapshot)
        claims = _claims_from_snapshot(scope, snapshot)
        evidence = _evidence_from_snapshot(scope, snapshot)
        provenance = [record.source_ref for record in evidence]
        result, sanads, links, grades, defects = (
            CanonicalRunMethodologySanadCreationLinkingGradingService().run(
                **scope,
                materialized_claims=[_sanad_claim_shell(claim) for claim in claims],
                evidence_items=list(evidence),
                source_provenance=provenance,
            )
        )
        if result.rejections or len(sanads) != len(rows):
            _fail()
        return (
            result,
            tuple(sanads),
            tuple(links),
            tuple(grades),
            tuple(defects),
            _StagedSanadWrite(
                **scope,
                run_step_id=str(values["run_step_id"]),
                lease_owner_id=self._lease_owner_id,
                lease_fencing_token=self._lease_fencing_token,
                audit_sink=self._audit_sink,
                records=tuple(sanads),
                grades=tuple(grades),
                defects=tuple(defects),
                factual_signature=_factual_signature(rows),
            ),
        )

    def load_snapshot(self, **values: Any) -> DurableCanonicalMaterializationSnapshot:
        scope = _scope(values, self._tenant_id)
        _rows, snapshot = self._load(scope)
        return snapshot

    def load_validated_final_snapshot(
        self, **values: Any
    ) -> DurableCanonicalMaterializationSnapshot:
        """Read final coverage and canonical lineage from one PG snapshot."""
        scoped_values = {**values, "tenant_id": self._tenant_id}
        scope = _scope(scoped_values, self._tenant_id)
        with self._connection_factory() as connection:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            set_tenant_local(connection, self._tenant_id)
            repository = PostgresMethodologyExtractionRepository(connection, self._tenant_id)
            repository.require_coverage_projection(
                deal_id=scope["deal_id"],
                run_id=scope["run_id"],
                stage="step10",
            )
            snapshot = repository.load_canonical_materialization_snapshot(
                deal_id=scope["deal_id"],
                run_id=scope["run_id"],
            )
            _validate_complete_snapshot(snapshot, rejected_count=0)
        return snapshot

    def _load(
        self, scope: Mapping[str, str]
    ) -> tuple[list[dict[str, Any]], DurableCanonicalMaterializationSnapshot]:
        with self._connection_factory() as connection:
            set_tenant_local(connection, self._tenant_id)
            repository = PostgresMethodologyExtractionRepository(connection, self._tenant_id)
            rows = repository.load_factual_output_records(
                deal_id=scope["deal_id"], run_id=scope["run_id"]
            )
            snapshot = repository.load_canonical_materialization_snapshot(
                deal_id=scope["deal_id"], run_id=scope["run_id"]
            )
        return rows, snapshot


def materialize_postgres_methodology_claims(**kwargs: Any) -> Any:
    boundary, values = _boundary_and_values(kwargs)
    return boundary.prepare_claim_materialization(**values)


def materialize_postgres_methodology_evidence(**kwargs: Any) -> Any:
    boundary, values = _boundary_and_values(kwargs)
    return boundary.prepare_evidence_materialization(**values)


def materialize_postgres_methodology_sanads(**kwargs: Any) -> Any:
    boundary, values = _boundary_and_values(kwargs)
    return boundary.prepare_sanad_materialization(**values)


def rehydrate_postgres_methodology_claims(**kwargs: Any) -> Any:
    boundary, values = _rehydration_boundary_and_values(kwargs)
    snapshot = boundary.load_snapshot(**values)
    _validate_claim_snapshot(snapshot)
    claims = _claims_from_snapshot(_scope(values, boundary._tenant_id), snapshot)
    return _claim_result(_scope(values, boundary._tenant_id), claims), claims


def rehydrate_postgres_methodology_evidence(**kwargs: Any) -> Any:
    boundary, values = _rehydration_boundary_and_values(kwargs)
    snapshot = boundary.load_snapshot(**values)
    _validate_evidence_snapshot(snapshot)
    scope = _scope(values, boundary._tenant_id)
    evidence = _evidence_from_snapshot(scope, snapshot)
    return _evidence_result(scope, evidence, claim_count=len(snapshot.claims)), evidence


def rehydrate_postgres_methodology_sanads(**kwargs: Any) -> Any:
    boundary, values = _rehydration_boundary_and_values(kwargs)
    scope = _scope(values, boundary._tenant_id)
    snapshot = boundary.load_validated_final_snapshot(**values)
    sanads, links, grades, defects = _sanad_shells_from_snapshot(scope, snapshot)
    result = _sanad_result_from_shells(scope, sanads, links, grades, defects)
    return result, sanads, links, grades, defects


def _boundary_and_values(
    kwargs: Mapping[str, Any],
) -> tuple[PostgresCanonicalMethodologyMaterializationBoundary, dict[str, Any]]:
    values = dict(kwargs)
    connection_factory = values.pop("connection_factory")
    lease_owner_id = str(values.pop("lease_owner_id"))
    lease_fencing_token = int(values.pop("lease_fencing_token"))
    audit_sink = values.pop("audit_sink")
    tenant_id = str(values.get("tenant_id", ""))
    return (
        PostgresCanonicalMethodologyMaterializationBoundary(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
            audit_sink=audit_sink,
        ),
        values,
    )


def _rehydration_boundary_and_values(
    kwargs: Mapping[str, Any],
) -> tuple[PostgresCanonicalMethodologyMaterializationBoundary, dict[str, Any]]:
    values = dict(kwargs)
    connection_factory = values.pop("connection_factory")
    tenant_id = str(values.get("tenant_id", ""))
    return (
        PostgresCanonicalMethodologyMaterializationBoundary(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
            lease_owner_id="rehydration-only",
            lease_fencing_token=1,
            audit_sink=None,
        ),
        values,
    )


def _scope(values: Mapping[str, Any], configured_tenant_id: str) -> dict[str, str]:
    scope = {key: str(values.get(key, "")).strip() for key in ("tenant_id", "deal_id", "run_id")}
    if any(not value for value in scope.values()) or scope["tenant_id"] != configured_tenant_id:
        _fail()
    return scope


def _claim_spec(scope: Mapping[str, str], row: Mapping[str, Any]) -> _ClaimSpec:
    output = MethodologyFactualOutput.model_validate(_json_object(row["typed_output_payload"]))
    identity = CanonicalMethodologyClaimIdentity.from_factual_output(
        tenant_id=scope["tenant_id"], deal_id=scope["deal_id"], factual_output=output
    )
    source_refs = tuple(
        MaterializedClaimSourceRef(
            document_id=str(row["document_id"]), source_span_id=span_id, locator=None
        )
        for span_id in output.accepted_source_span_ids
    )
    claim = CanonicalMethodologyRunClaim(
        claim_id=identity.claim_id,
        tenant_id=scope["tenant_id"],
        deal_id=scope["deal_id"],
        run_id=scope["run_id"],
        claim_text=output.claim_text,
        claim_type=CanonicalClaimClass(output.claim_class),
        value_struct=output.value,
        materiality=Materiality.MEDIUM,
        source_refs=source_refs,
        methodology_id=str(row["methodology_type"]),
        methodology_version_id=str(row["methodology_version_id"]),
        methodology_question_id=str(row["methodology_question_id"]),
        coverage_record_id=str(row["coverage_record_id"]),
        extraction_task_id=str(row["extraction_task_id"]),
        extraction_output_id=str(output.factual_output_id),
        status="materialized_unverified",
    )
    return _ClaimSpec(row=row, output=output, identity=identity, claim=claim)


def _canonical_claim_payload(spec: _ClaimSpec) -> dict[str, Any]:
    output, identity = spec.output, spec.identity
    return {
        "claim_id": identity.claim_id,
        "claim_class": output.claim_class,
        "claim_text": output.claim_text,
        "predicate": output.predicate,
        "value": output.value.model_dump(mode="json"),
        "normalization_rule_version": identity.normalization_rule_version,
        "normalized_predicate": identity.normalized_predicate,
        "normalized_subject": identity.normalized_subject,
        "normalized_period": identity.normalized_period,
        "normalized_unit": identity.normalized_unit,
        "value_sha256": identity.value_sha256,
        "canonical_key_sha256": identity.canonical_key_sha256,
        "claim_grade": "D",
        "corroboration": {"level": "AHAD", "independent_chain_count": 1},
        "claim_verdict": "UNVERIFIED",
        "claim_action": "VERIFY",
        "defect_ids": [],
        "materiality": "MEDIUM",
        "ic_bound": False,
    }


def _evidence_specs(
    scope: Mapping[str, str],
    factual: Mapping[str, Any],
    claim_row: Mapping[str, Any],
) -> list[_EvidenceSpec]:
    output = MethodologyFactualOutput.model_validate(_json_object(factual["typed_output_payload"]))
    timestamp = _timestamp(factual["created_at"])
    specs: list[_EvidenceSpec] = []
    for span_id, locator_sha256 in zip(
        output.accepted_source_span_ids,
        output.accepted_locator_sha256s,
        strict=True,
    ):
        source_ref = RunScopedEvidenceProvenanceRef(
            document_id=str(factual["document_id"]),
            source_span_id=span_id,
            locator={"locator_sha256": locator_sha256},
        )
        evidence_id = generate_methodology_evidence_item_id(
            tenant_id=scope["tenant_id"],
            deal_id=scope["deal_id"],
            run_id=scope["run_id"],
            claim_id=str(claim_row["claim_id"]),
            extraction_output_id=str(output.factual_output_id),
            extraction_task_id=str(factual["extraction_task_id"]),
            methodology_question_id=str(factual["methodology_question_id"]),
            coverage_record_id=str(factual["coverage_record_id"]),
            source_ref=source_ref,
        )
        item = EvidenceItem(
            evidence_id=evidence_id,
            tenant_id=scope["tenant_id"],
            deal_id=scope["deal_id"],
            source_span_id=span_id,
            source_system="methodology_live_extraction",
            upstream_origin_id=span_id,
            retrieval_timestamp=timestamp,
            verification_status=VerificationStatus.UNVERIFIED,
            source_grade=SourceGrade.D,
            rationale={
                "run_id": scope["run_id"],
                "result_id": str(factual["result_id"]),
                "factual_output_record_id": str(factual["factual_output_record_id"]),
                "methodology_question_record_id": str(factual["methodology_question_record_id"]),
                "task_record_id": str(factual["task_record_id"]),
                "document_id": str(factual["document_id"]),
                "source_span_id": span_id,
                "locator_sha256": locator_sha256,
            },
            created_at=timestamp,
            updated_at=timestamp,
        )
        record = RunScopedEvidenceItemRecord(
            tenant_id=scope["tenant_id"],
            deal_id=scope["deal_id"],
            run_id=scope["run_id"],
            claim_id=str(claim_row["claim_id"]),
            evidence_item=item,
            source_ref=source_ref,
            methodology_question_id=str(factual["methodology_question_id"]),
            coverage_record_id=str(factual["coverage_record_id"]),
            extraction_task_id=str(factual["extraction_task_id"]),
            extraction_output_id=str(output.factual_output_id),
            status="materialized_unverified",
        )
        specs.append(
            _EvidenceSpec(
                factual_row=factual,
                claim_row=claim_row,
                record=record,
                locator_sha256=locator_sha256,
            )
        )
    return specs


def _claim_result(
    scope: Mapping[str, str], claims: Sequence[CanonicalMethodologyRunClaim]
) -> MethodologyOutputClaimMaterializationRunResult:
    mappings = [
        MethodologyOutputClaimMapping(
            extraction_output_id=claim.extraction_output_id,
            claim_id=claim.claim_id,
            extraction_task_id=claim.extraction_task_id,
            methodology_question_id=claim.methodology_question_id,
            coverage_record_id=claim.coverage_record_id,
            document_id=claim.source_refs[0].document_id,
            source_span_ids=[ref.source_span_id for ref in claim.source_refs],
        )
        for claim in claims
    ]
    return MethodologyOutputClaimMaterializationRunResult(
        **scope,
        status=ClaimMaterializationStatus.COMPLETED,
        output_claim_mappings=mappings,
        rejected_outputs=[],
        summary=MethodologyOutputClaimMaterializationSummary(
            **scope,
            total_outputs=len(claims),
            created_claim_count=len(claims),
            rejected_output_count=0,
            by_status={"materialized_unverified": len(claims)} if claims else {},
            by_reason={},
        ),
    )


def _sanad_claim_shell(
    claim: CanonicalMethodologyRunClaim,
) -> RunScopedMaterializedClaimShell:
    """Project a canonical claim onto the established typed Sanad input seam."""

    return RunScopedMaterializedClaimShell(
        claim_id=claim.claim_id,
        tenant_id=claim.tenant_id,
        deal_id=claim.deal_id,
        run_id=claim.run_id,
        source_refs=list(claim.source_refs),
        methodology_question_id=claim.methodology_question_id,
        coverage_record_id=claim.coverage_record_id,
        extraction_task_id=claim.extraction_task_id,
        extraction_output_id=claim.extraction_output_id,
        status=claim.status,
    )


def _evidence_result(
    scope: Mapping[str, str],
    records: Sequence[RunScopedEvidenceItemRecord],
    *,
    claim_count: int,
) -> MethodologyEvidenceItemMaterializationRunResult:
    mappings = [MethodologyEvidenceItemMapping.from_record(record) for record in records]
    return MethodologyEvidenceItemMaterializationRunResult(
        **scope,
        status=EvidenceItemMaterializationStatus.COMPLETED,
        evidence_item_mappings=mappings,
        rejected_source_refs=[],
        summary=MethodologyEvidenceItemMaterializationSummary(
            **scope,
            total_claims=claim_count,
            total_source_refs=len(records),
            created_evidence_count=len(records),
            rejected_source_ref_count=0,
            by_status={"materialized_unverified": len(records)} if records else {},
            by_reason={},
        ),
    )


def _claims_from_snapshot(
    scope: Mapping[str, str], snapshot: DurableCanonicalMaterializationSnapshot
) -> tuple[CanonicalMethodologyRunClaim, ...]:
    factual = {str(row["factual_output_record_id"]): row for row in snapshot.factual_outputs}
    claims: list[CanonicalMethodologyRunClaim] = []
    for row in snapshot.claims:
        source = factual.get(str(row["factual_output_record_id"]))
        if source is None:
            _fail()
        output = MethodologyFactualOutput.model_validate(
            _json_object(source["typed_output_payload"])
        )
        claims.append(
            CanonicalMethodologyRunClaim(
                claim_id=str(row["claim_id"]),
                tenant_id=scope["tenant_id"],
                deal_id=scope["deal_id"],
                run_id=scope["run_id"],
                claim_text=str(row["claim_text"]),
                claim_type=CanonicalClaimClass(str(row["claim_class"])),
                value_struct=parse_value_struct(_json_object(row["value"])),
                materiality=Materiality(str(row["materiality"])),
                source_refs=tuple(
                    MaterializedClaimSourceRef(
                        document_id=str(source["document_id"]),
                        source_span_id=span_id,
                        locator=None,
                    )
                    for span_id in output.accepted_source_span_ids
                ),
                methodology_id=str(source["methodology_type"]),
                methodology_version_id=str(source["methodology_version_id"]),
                methodology_question_id=str(source["methodology_question_id"]),
                coverage_record_id=str(source["coverage_record_id"]),
                extraction_task_id=str(source["extraction_task_id"]),
                extraction_output_id=str(output.factual_output_id),
                status="materialized_unverified",
            )
        )
    return tuple(claims)


def _evidence_from_snapshot(
    scope: Mapping[str, str], snapshot: DurableCanonicalMaterializationSnapshot
) -> tuple[RunScopedEvidenceItemRecord, ...]:
    factual = {str(row["factual_output_record_id"]): row for row in snapshot.factual_outputs}
    records: list[RunScopedEvidenceItemRecord] = []
    for row in snapshot.evidence_items:
        source = factual.get(str(row["factual_output_record_id"]))
        if source is None:
            _fail()
        output = MethodologyFactualOutput.model_validate(
            _json_object(source["typed_output_payload"])
        )
        source_ref = RunScopedEvidenceProvenanceRef(
            document_id=str(row["document_id"]),
            source_span_id=str(row["source_span_id"]),
            locator={"locator_sha256": str(row["locator_sha256"])},
        )
        item = EvidenceItem(
            evidence_id=str(row["evidence_id"]),
            tenant_id=scope["tenant_id"],
            deal_id=scope["deal_id"],
            source_span_id=str(row["canonical_source_span_id"]),
            source_system=str(row["source_system"]),
            upstream_origin_id=str(row["upstream_origin_id"]),
            retrieval_timestamp=_timestamp(row["retrieval_timestamp"]),
            verification_status=VerificationStatus(str(row["verification_status"])),
            source_grade=SourceGrade(str(row["source_grade"])),
            source_rank_subgrade=row.get("source_rank_subgrade"),
            rationale=_json_object(row["rationale"]),
            created_at=_timestamp(row["created_at"]),
            updated_at=_timestamp(row["updated_at"]),
        )
        records.append(
            RunScopedEvidenceItemRecord(
                tenant_id=scope["tenant_id"],
                deal_id=scope["deal_id"],
                run_id=scope["run_id"],
                claim_id=str(row["claim_id"]),
                evidence_item=item,
                source_ref=source_ref,
                methodology_question_id=str(source["methodology_question_id"]),
                coverage_record_id=str(source["coverage_record_id"]),
                extraction_task_id=str(source["extraction_task_id"]),
                extraction_output_id=str(output.factual_output_id),
                status="materialized_unverified",
            )
        )
    return tuple(records)


def _canonical_sanad_payload(
    record: RunScopedSanadRecord, grade: RunScopedSanadGradeRecord
) -> dict[str, Any]:
    sanad = record.sanad
    return {
        "sanad_id": sanad.sanad_id,
        "claim_id": record.claim_id,
        "primary_evidence_id": sanad.primary_evidence_id,
        "corroborating_evidence_ids": list(sanad.corroborating_evidence_ids),
        "transmission_chain": [node.model_dump(mode="json") for node in sanad.transmission_chain],
        "computed": {
            "extraction_confidence": str(sanad.extraction_confidence),
            "dhabt_score": str(sanad.dhabt_score),
            "corroboration_status": sanad.corroboration_status.value,
            "sanad_grade": sanad.sanad_grade.value,
            "grade_explanation": list(sanad.grade_explanation),
            "grade_reason_codes": list(grade.grade_reason_codes),
            "defect_ids": list(grade.defect_ids),
            "fatal_defect_count": grade.fatal_defect_count,
            "major_defect_count": grade.major_defect_count,
            "minor_defect_count": grade.minor_defect_count,
            "source_span_ids": list(record.source_span_ids),
        },
        "created_at": sanad.created_at,
        "updated_at": sanad.updated_at,
    }


def _sanad_shells_from_snapshot(
    scope: Mapping[str, str], snapshot: DurableCanonicalMaterializationSnapshot
) -> tuple[tuple[Any, ...], tuple[Any, ...], tuple[Any, ...], tuple[Any, ...]]:
    factual = {str(row["factual_output_record_id"]): row for row in snapshot.factual_outputs}
    defects_by_sanad: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for persisted_defect in snapshot.defects:
        defects_by_sanad[str(persisted_defect["sanad_link_id"])].append(persisted_defect)
    sanads: list[RunScopedSanadShell] = []
    links: list[RunScopedSanadLinkRecord] = []
    grades: list[RunScopedSanadGradeRecord] = []
    defects: list[RunScopedSanadDefectShell] = []
    for row in snapshot.sanads:
        source = factual.get(str(row["factual_output_record_id"]))
        if source is None:
            _fail()
        computed = _json_object(row["computed"])
        evidence_ids = [
            str(row["primary_evidence_id"]),
            *_json_list(row["corroborating_evidence_ids"]),
        ]
        source_span_ids = [str(item) for item in computed.get("source_span_ids", [])]
        defect_rows = defects_by_sanad.get(str(row["sanad_link_id"]), [])
        defect_ids = sorted(str(item["defect_id"]) for item in defect_rows)
        chain = _json_list(row["transmission_chain"])
        grade = SanadGrade(str(computed["sanad_grade"]))
        sanads.append(
            RunScopedSanadShell(
                **scope,
                claim_id=str(row["claim_id"]),
                sanad_id=str(row["sanad_id"]),
                primary_evidence_id=str(row["primary_evidence_id"]),
                evidence_ids=evidence_ids,
                source_span_ids=source_span_ids,
                sanad_grade=grade,
                defect_ids=defect_ids,
                transmission_chain_node_count=len(chain),
                chain_node_types=[str(item["node_type"]) for item in chain],
                methodology_question_id=str(source["methodology_question_id"]),
                coverage_record_id=str(source["coverage_record_id"]),
                extraction_task_id=str(source["extraction_task_id"]),
                extraction_output_id=str(source["factual_output_id"]),
                status="created_linked_graded",
            )
        )
        links.append(
            RunScopedSanadLinkRecord(
                tenant_id=scope["tenant_id"],
                deal_id=scope["deal_id"],
                run_id=scope["run_id"],
                claim_id=str(row["claim_id"]),
                sanad_id=str(row["sanad_id"]),
                evidence_ids=evidence_ids,
                source_span_ids=source_span_ids,
                claim_link_status="linked_run_scoped",
            )
        )
        grades.append(
            RunScopedSanadGradeRecord(
                **scope,
                claim_id=str(row["claim_id"]),
                sanad_id=str(row["sanad_id"]),
                sanad_grade=grade,
                grade_reason_codes=[str(item) for item in computed["grade_reason_codes"]],
                defect_ids=defect_ids,
                fatal_defect_count=int(computed["fatal_defect_count"]),
                major_defect_count=int(computed["major_defect_count"]),
                minor_defect_count=int(computed["minor_defect_count"]),
            )
        )
        for linked_defect in defect_rows:
            defects.append(
                RunScopedSanadDefectShell(
                    **scope,
                    claim_id=str(linked_defect["claim_id"]),
                    sanad_id=str(row["sanad_id"]),
                    defect_id=str(linked_defect["defect_id"]),
                    defect_type=DefectType(str(linked_defect["defect_type"])),
                    severity=DefectSeverity(str(linked_defect["severity"])),
                    cure_protocol=CureProtocol(str(linked_defect["cure_protocol"])),
                    status=(
                        DefectStatus.WAIVED if bool(linked_defect["waived"]) else DefectStatus.OPEN
                    ),
                )
            )
    return tuple(sanads), tuple(links), tuple(grades), tuple(defects)


def _sanad_result_from_shells(
    scope: Mapping[str, str],
    sanads: Sequence[RunScopedSanadShell],
    links: Sequence[RunScopedSanadLinkRecord],
    grades: Sequence[RunScopedSanadGradeRecord],
    defects: Sequence[RunScopedSanadDefectShell],
) -> MethodologySanadMaterializationRunResult:
    mappings = [
        MethodologySanadMapping(
            claim_id=item.claim_id,
            sanad_id=item.sanad_id,
            primary_evidence_id=item.primary_evidence_id,
            evidence_ids=item.evidence_ids,
            source_span_ids=item.source_span_ids,
            methodology_question_id=item.methodology_question_id,
            coverage_record_id=item.coverage_record_id,
            extraction_task_id=item.extraction_task_id,
            extraction_output_id=item.extraction_output_id,
            sanad_grade=item.sanad_grade,
            defect_ids=item.defect_ids,
            chain_node_types=item.chain_node_types,
            transmission_chain_node_count=item.transmission_chain_node_count,
        )
        for item in sanads
    ]
    from idis.models.sanad_materialization import (
        MethodologySanadMaterializationStatus,
        MethodologySanadMaterializationSummary,
    )

    return MethodologySanadMaterializationRunResult(
        **scope,
        status=MethodologySanadMaterializationStatus.COMPLETED,
        sanad_mappings=mappings,
        claim_sanad_links=list(links),
        grade_records=list(grades),
        defect_shells=list(defects),
        rejections=[],
        summary=MethodologySanadMaterializationSummary(
            **scope,
            total_claims=len(mappings),
            total_evidence_items=sum(len(item.evidence_ids) for item in sanads),
            created_sanad_count=len(mappings),
            linked_claim_count=len(links),
            graded_sanad_count=len(grades),
            defect_count=len(defects),
            rejected_count=0,
            by_status={"created_linked_graded": len(mappings)} if mappings else {},
            by_reason={},
            by_grade=_counts(item.sanad_grade.value for item in grades),
            by_defect_severity=_counts(item.severity.value for item in defects),
        ),
    )


def _factual_signature(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (
                str(row["factual_output_record_id"]),
                str(row["typed_output_sha256"]),
            )
            for row in rows
        )
    )


def _require_factual_signature(
    repository: PostgresMethodologyExtractionRepository,
    deal_id: str,
    run_id: str,
    expected: tuple[tuple[str, str], ...],
) -> None:
    actual = _factual_signature(
        repository.load_factual_output_records(deal_id=deal_id, run_id=run_id)
    )
    if actual != expected:
        _fail()


def _validate_claim_snapshot(snapshot: DurableCanonicalMaterializationSnapshot) -> None:
    factual = [str(row["factual_output_record_id"]) for row in snapshot.factual_outputs]
    claims = [str(row["factual_output_record_id"]) for row in snapshot.claims]
    if len(factual) != len(set(factual)) or sorted(factual) != sorted(claims):
        _fail()


def _validate_evidence_snapshot(snapshot: DurableCanonicalMaterializationSnapshot) -> None:
    _validate_claim_snapshot(snapshot)
    factual = {str(row["factual_output_record_id"]): row for row in snapshot.factual_outputs}
    actual: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in snapshot.evidence_items:
        actual[str(row["factual_output_record_id"])].append(
            (str(row["source_span_id"]), str(row["locator_sha256"]))
        )
    for record_id, row in factual.items():
        expected = list(_source_locator_pairs(row))
        if expected != sorted(actual.get(record_id, [])):
            _fail()
    if set(actual) - set(factual):
        _fail()


def _validate_complete_snapshot(
    snapshot: DurableCanonicalMaterializationSnapshot, *, rejected_count: int
) -> None:
    _validate_evidence_snapshot(snapshot)
    factual_by_record = {
        str(row["factual_output_record_id"]): row for row in snapshot.factual_outputs
    }
    if not factual_by_record:
        if snapshot.claims or snapshot.evidence_items or snapshot.sanads or snapshot.defects:
            _fail()
        return
    factual_ids = [str(row["factual_output_id"]) for row in snapshot.factual_outputs]
    claim_ids = [
        str(factual_by_record[str(row["factual_output_record_id"])]["factual_output_id"])
        for row in snapshot.claims
    ]
    evidence_ids = sorted(
        {
            str(factual_by_record[str(row["factual_output_record_id"])]["factual_output_id"])
            for row in snapshot.evidence_items
        }
    )
    sanad_ids = [
        str(factual_by_record[str(row["factual_output_record_id"])]["factual_output_id"])
        for row in snapshot.sanads
    ]
    accepted = {
        str(row["factual_output_id"]): _source_locator_pairs(row)
        for row in snapshot.factual_outputs
    }
    evidence: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in snapshot.evidence_items:
        output_id = str(
            factual_by_record[str(row["factual_output_record_id"])]["factual_output_id"]
        )
        evidence[output_id].append((str(row["source_span_id"]), str(row["locator_sha256"])))
    sanad_by_link = {str(row["sanad_link_id"]): row for row in snapshot.sanads}
    actual_defects: list[tuple[str, str]] = []
    expected_defects: dict[str, tuple[str, ...]] = {}
    for row in snapshot.sanads:
        output_id = str(
            factual_by_record[str(row["factual_output_record_id"])]["factual_output_id"]
        )
        expected_defects[output_id] = tuple(
            sorted(str(item) for item in _json_object(row["computed"]).get("defect_ids", []))
        )
    for row in snapshot.defects:
        sanad = sanad_by_link.get(str(row["sanad_link_id"]))
        if sanad is None:
            _fail()
        output_id = str(
            factual_by_record[str(sanad["factual_output_record_id"])]["factual_output_id"]
        )
        actual_defects.append((output_id, str(row["defect_id"])))
    validate_canonical_materialization_completion(
        factual_output_ids=factual_ids,
        persisted_factual_output_ids=factual_ids,
        claim_link_output_ids=claim_ids,
        evidence_link_output_ids=evidence_ids,
        sanad_link_output_ids=sanad_ids,
        accepted_source_locator_pairs_by_output=accepted,
        evidence_link_source_locator_pairs_by_output={
            output_id: tuple(sorted(values)) for output_id, values in evidence.items()
        },
        expected_defect_ids_by_output=expected_defects,
        defect_link_pairs=actual_defects,
        rejected_count=rejected_count,
    )


def _one_by_output(
    claim_rows: Sequence[Mapping[str, Any]],
    factual_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    factual = {str(row["factual_output_record_id"]): row for row in factual_rows}
    result: dict[str, Mapping[str, Any]] = {}
    for row in claim_rows:
        source = factual.get(str(row["factual_output_record_id"]))
        if source is None:
            _fail()
        output_id = str(source["factual_output_id"])
        if output_id in result:
            _fail()
        result[output_id] = row
    return result


def _json_object(value: Any) -> dict[str, Any]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, Mapping):
        _fail()
    return dict(parsed)


def _json_list(value: Any) -> list[Any]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list | tuple):
        _fail()
    return list(parsed)


def _source_locator_pairs(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    try:
        pairs = tuple(
            sorted(
                zip(
                    (str(item) for item in _json_list(row["accepted_source_span_ids"])),
                    (str(item) for item in _json_list(row["accepted_locator_sha256s"])),
                    strict=True,
                )
            )
        )
    except (KeyError, ValueError):
        _fail()
    return pairs


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _counts(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value)
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _require_lease(staged: Any, owner: str, token: int) -> None:
    if owner != staged.lease_owner_id or token != staged.lease_fencing_token:
        raise CanonicalMethodologyMaterializationError("WORKER_LEASE_FENCED")


def _emit_audit(
    connection: Any,
    audit_sink: Any,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    event_type: str,
    run_step_id: str,
    count: int,
) -> None:
    from idis.services.runs.methodology_postgres_runtime import (
        _emit_methodology_audit_in_tx,
    )

    _emit_methodology_audit_in_tx(
        connection=connection,
        audit_sink=audit_sink,
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        event_type=event_type,
        summary="Canonical methodology output committed",
        status_code=200,
        safe={"run_step_id": run_step_id, "record_count": count},
    )


def _fail() -> Never:
    raise CanonicalMethodologyMaterializationError(
        "durable canonical methodology materialization is incomplete"
    )


__all__ = [
    "CanonicalMethodologyRunClaim",
    "PostgresCanonicalMethodologyMaterializationBoundary",
    "materialize_postgres_methodology_claims",
    "materialize_postgres_methodology_evidence",
    "materialize_postgres_methodology_sanads",
    "rehydrate_postgres_methodology_claims",
    "rehydrate_postgres_methodology_evidence",
    "rehydrate_postgres_methodology_sanads",
]
