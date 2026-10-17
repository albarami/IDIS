"""Run-local legacy SNAPSHOT output custody, not canonical Task7 receipts.

Existing producers run once against read-only repositories. Their validated
write arguments are captured without timestamps or synthetic durable parents,
then persisted by the existing repositories in the live-lease COMPLETE UoW.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn, Protocol
from uuid import UUID

from idis.audit.sink import AuditSink, AuditSinkError
from idis.models.calc_sanad import CalcSanad
from idis.models.deterministic_calculation import DeterministicCalculation
from idis.models.run_step import StepName
from idis.persistence.repositories.calculations import PostgresCalculationsRepository
from idis.persistence.repositories.claims import (
    ClaimsRepository,
    DefectsRepository,
    SanadsRepository,
)
from idis.persistence.repositories.evidence import PostgresEvidenceRepository
from idis.validators.audit_event_validator import validate_audit_event

if TYPE_CHECKING:
    from idis.services.runs.orchestrator import RunContext
    from idis.services.runs.steps import (
        NativeOutputWriteBinding,
        StagedNativeOutputWrite,
    )

SNAPSHOT_OUTPUT_STEPS = frozenset({StepName.EXTRACT, StepName.GRADE, StepName.CALC})
FAILURE_AUDIT_TYPES = frozenset(
    {
        "integration.extraction.chunking_failed",
        "integration.extraction.chunk_failed",
        "sanad.integrity.failed",
    }
)


class SnapshotOutputCompositionPort(Protocol):
    """Only the lifecycle hooks the orchestrator needs; no provider SQL port."""

    active: bool

    def activate(self, *, ctx: RunContext) -> None: ...

    def seal_success(
        self, *, ctx: RunContext, step_name: StepName, result_summary: dict[str, Any]
    ) -> None: ...

    def before_complete_step(self, *, ctx: RunContext, step_name: StepName) -> None: ...

    def complete_committed(self, *, ctx: RunContext, step_name: StepName) -> None: ...

    def boundary_failed(self, *, ctx: RunContext, step_name: StepName) -> None: ...

    def abort_step_and_stage_failure_audits(
        self, *, ctx: RunContext, step_name: StepName
    ) -> tuple[StagedNativeOutputWrite, ...]: ...

    def before_failed_step(self, *, ctx: RunContext, step_name: StepName) -> None: ...

    def discard_all(self, *, ctx: RunContext) -> None: ...


def _reject(message: str) -> NoReturn:
    from idis.services.runs.orchestrator import RunStepBlockedError

    raise RunStepBlockedError("PIPELINE_CONTRACT_UNAVAILABLE", message)


def _json(value: Any) -> str:
    """Snapshot JSON by value; reject non-JSON/nonfinite producer arguments."""
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _uuid(value: str) -> None:
    try:
        canonical = str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        _reject("Captured output identity is not a canonical UUID")
    if canonical != value:
        _reject("Captured output identity is not a canonical UUID")


@dataclass(frozen=True)
class OutputScope:
    tenant_id: str
    deal_id: str
    run_id: str
    step_name: StepName
    lease_owner_id: str
    lease_fencing_token: int

    @classmethod
    def from_context(cls, ctx: RunContext, step_name: StepName) -> OutputScope:
        if ctx.lease_owner_id is None or ctx.lease_fencing_token is None:
            _reject("SNAPSHOT output requires the complete claimed lease")
        assert ctx.lease_owner_id is not None and ctx.lease_fencing_token is not None
        return cls(
            ctx.tenant_id,
            ctx.deal_id,
            ctx.run_id,
            step_name,
            ctx.lease_owner_id,
            ctx.lease_fencing_token,
        )


@dataclass(frozen=True)
class PendingClaimCreate:
    scope: OutputScope
    claim_id: str
    deal_id: str
    claim_class: str
    claim_text: str
    predicate: str | None
    value_json: str
    sanad_id: str | None
    claim_grade: str
    corroboration_json: str
    claim_verdict: str
    claim_action: str
    defect_ids: tuple[str, ...] | None
    materiality: str
    ic_bound: bool
    primary_span_id: str | None

    def arguments(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "deal_id": self.deal_id,
            "claim_class": self.claim_class,
            "claim_text": self.claim_text,
            "predicate": self.predicate,
            "value": json.loads(self.value_json),
            "sanad_id": self.sanad_id,
            "claim_grade": self.claim_grade,
            "corroboration": json.loads(self.corroboration_json),
            "claim_verdict": self.claim_verdict,
            "claim_action": self.claim_action,
            "defect_ids": list(self.defect_ids) if self.defect_ids is not None else None,
            "materiality": self.materiality,
            "ic_bound": self.ic_bound,
            "primary_span_id": self.primary_span_id,
        }


@dataclass(frozen=True)
class PendingEvidenceCreate:
    scope: OutputScope
    evidence_id: str
    tenant_id: str
    deal_id: str
    claim_id: str
    source_span_id: str
    source_grade: str
    verification_status: str

    def arguments(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "tenant_id": self.tenant_id,
            "deal_id": self.deal_id,
            "claim_id": self.claim_id,
            "source_span_id": self.source_span_id,
            "source_grade": self.source_grade,
            "verification_status": self.verification_status,
        }


@dataclass(frozen=True)
class PendingSanadCreate:
    scope: OutputScope
    sanad_id: str
    claim_id: str
    deal_id: str
    primary_evidence_id: str
    corroborating_evidence_ids: tuple[str, ...] | None
    transmission_chain_json: str
    computed_json: str

    def arguments(self) -> dict[str, Any]:
        return {
            "sanad_id": self.sanad_id,
            "claim_id": self.claim_id,
            "deal_id": self.deal_id,
            "primary_evidence_id": self.primary_evidence_id,
            "corroborating_evidence_ids": list(self.corroborating_evidence_ids)
            if self.corroborating_evidence_ids is not None
            else None,
            "transmission_chain": json.loads(self.transmission_chain_json),
            "computed": json.loads(self.computed_json),
        }


@dataclass(frozen=True)
class PendingDefectCreate:
    scope: OutputScope
    defect_id: str
    claim_id: str | None
    deal_id: str | None
    defect_type: str
    severity: str
    description: str
    cure_protocol: str
    status: str

    def arguments(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "claim_id": self.claim_id,
            "deal_id": self.deal_id,
            "defect_type": self.defect_type,
            "severity": self.severity,
            "description": self.description,
            "cure_protocol": self.cure_protocol,
            "status": self.status,
        }


@dataclass(frozen=True)
class PendingClaimGradeUpdate:
    scope: OutputScope
    claim_id: str
    claim_grade: str | None
    sanad_id: str | None


@dataclass(frozen=True)
class PendingCalculationPair:
    scope: OutputScope
    calculation_json: str
    calc_sanad_json: str

    @property
    def calculation(self) -> DeterministicCalculation:
        return DeterministicCalculation.model_validate_json(self.calculation_json)

    @property
    def calc_sanad(self) -> CalcSanad:
        return CalcSanad.model_validate_json(self.calc_sanad_json)


@dataclass(frozen=True)
class PendingCanonicalAudit:
    scope: OutputScope
    event_json: str

    @property
    def event(self) -> dict[str, Any]:
        return dict(json.loads(self.event_json))


PendingOperation = (
    PendingClaimCreate
    | PendingEvidenceCreate
    | PendingSanadCreate
    | PendingDefectCreate
    | PendingClaimGradeUpdate
    | PendingCalculationPair
    | PendingCanonicalAudit
)


class _ClaimWriter:
    def __init__(self, draft: SnapshotStepDraft) -> None:
        self._draft = draft

    def create(
        self,
        *,
        claim_id: str,
        deal_id: str,
        claim_class: str,
        claim_text: str,
        predicate: str | None = None,
        value: dict[str, Any] | None = None,
        sanad_id: str | None = None,
        claim_grade: str = "D",
        corroboration: dict[str, Any] | None = None,
        claim_verdict: str = "UNVERIFIED",
        claim_action: str = "VERIFY",
        defect_ids: list[str] | None = None,
        materiality: str = "MEDIUM",
        ic_bound: bool = False,
        primary_span_id: str | None = None,
    ) -> dict[str, Any]:
        d = self._draft
        try:
            d.require_step(StepName.EXTRACT)
            d.require_deal(deal_id)
            d.require_new_id(claim_id)
            if primary_span_id not in d.allowed_span_ids or sanad_id is not None or defect_ids:
                _reject("Captured claim has unsupported source or preexisting output parents")
            row = PendingClaimCreate(
                d.scope,
                claim_id,
                deal_id,
                claim_class,
                claim_text,
                predicate,
                _json(value),
                sanad_id,
                claim_grade,
                _json(corroboration),
                claim_verdict,
                claim_action,
                tuple(defect_ids) if defect_ids is not None else None,
                materiality,
                ic_bound,
                primary_span_id,
            )
            d.operations.append(row)
            return {**row.arguments(), "tenant_id": d.scope.tenant_id}
        except Exception:
            d.poison()
            raise


class _EvidenceRecorder:
    def __init__(self, draft: SnapshotStepDraft) -> None:
        self._draft = draft

    def create(
        self,
        *,
        evidence_id: str,
        tenant_id: str,
        deal_id: str,
        claim_id: str,
        source_span_id: str,
        source_grade: str = "D",
        verification_status: str = "UNVERIFIED",
    ) -> dict[str, Any]:
        d = self._draft
        try:
            d.require_step(StepName.EXTRACT)
            d.require_deal(deal_id)
            d.require_new_id(evidence_id)
            if (
                tenant_id != d.scope.tenant_id
                or source_span_id not in d.allowed_span_ids
                or claim_id not in {r.claim_id for r in d.claim_rows}
            ):
                _reject("Captured evidence is outside this draft's claim/source/tenant scope")
            row = PendingEvidenceCreate(
                d.scope,
                evidence_id,
                tenant_id,
                deal_id,
                claim_id,
                source_span_id,
                source_grade,
                verification_status,
            )
            d.operations.append(row)
            return row.arguments()
        except Exception:
            d.poison()
            raise

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        self._draft.poison()
        _reject("Evidence ID lookup is not an admitted SNAPSHOT read")

    def get_by_claim(self, claim_id: str) -> list[dict[str, Any]]:
        self._draft.require_claim(claim_id)
        rows = self._draft.evidence_reader().get_by_claim(claim_id)
        for row in rows:
            if (
                row.get("tenant_id") != self._draft.scope.tenant_id
                or row.get("deal_id") != self._draft.scope.deal_id
                or row.get("claim_id") != claim_id
                or row.get("source_span_id") not in self._draft.allowed_span_ids
            ):
                self._draft.poison()
                _reject("Committed evidence read escaped the frozen source scope")
        return rows


class _SanadWriter:
    def __init__(self, draft: SnapshotStepDraft) -> None:
        self._draft = draft

    def create(
        self,
        *,
        sanad_id: str,
        claim_id: str,
        deal_id: str,
        primary_evidence_id: str,
        corroborating_evidence_ids: list[str] | None = None,
        transmission_chain: list[dict[str, Any]] | None = None,
        computed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        d = self._draft
        try:
            d.require_step(StepName.GRADE)
            d.require_claim(claim_id)
            d.require_deal(deal_id)
            d.require_new_id(sanad_id)
            evidence = {str(r["evidence_id"]) for r in d.evidence_repo.get_by_claim(claim_id)}
            if (
                primary_evidence_id not in evidence
                or not set(corroborating_evidence_ids or ()) <= evidence
            ):
                _reject("Captured Sanad does not reference this claim's committed evidence")
            row = PendingSanadCreate(
                d.scope,
                sanad_id,
                claim_id,
                deal_id,
                primary_evidence_id,
                tuple(corroborating_evidence_ids)
                if corroborating_evidence_ids is not None
                else None,
                _json(transmission_chain),
                _json(computed),
            )
            d.operations.append(row)
            return {**row.arguments(), "tenant_id": d.scope.tenant_id}
        except Exception:
            d.poison()
            raise


class _DefectWriter:
    def __init__(self, draft: SnapshotStepDraft) -> None:
        self._draft = draft

    def create(
        self,
        *,
        defect_id: str,
        claim_id: str | None = None,
        deal_id: str | None = None,
        defect_type: str,
        severity: str,
        description: str,
        cure_protocol: str,
        status: str = "OPEN",
    ) -> dict[str, Any]:
        d = self._draft
        try:
            d.require_step(StepName.GRADE)
            d.require_claim(claim_id)
            d.require_deal(deal_id)
            d.require_new_id(defect_id)
            row = PendingDefectCreate(
                d.scope,
                defect_id,
                claim_id,
                deal_id,
                defect_type,
                severity,
                description,
                cure_protocol,
                status,
            )
            d.operations.append(row)
            return {**row.arguments(), "tenant_id": d.scope.tenant_id}
        except Exception:
            d.poison()
            raise


class _GradingClaims:
    def __init__(self, draft: SnapshotStepDraft) -> None:
        self._draft = draft

    def get(self, claim_id: str) -> dict[str, Any] | None:
        d = self._draft
        d.require_claim(claim_id)
        row = ClaimsRepository(d.composition.provider_db_conn, d.scope.tenant_id).get(claim_id)
        if (
            row is None
            or row.get("tenant_id") != d.scope.tenant_id
            or row.get("deal_id") != d.scope.deal_id
        ):
            d.poison()
            _reject("Grading claim is absent or outside this run's scope")
        return row

    def update_grade(
        self, claim_id: str, *, claim_grade: str | None = None, sanad_id: str | None = None
    ) -> None:
        d = self._draft
        try:
            d.require_step(StepName.GRADE)
            d.require_claim(claim_id)
            if not any(
                isinstance(r, PendingSanadCreate)
                and r.claim_id == claim_id
                and r.sanad_id == sanad_id
                for r in d.operations
            ):
                _reject("Grade update has no matching captured Sanad")
            if any(
                isinstance(r, PendingClaimGradeUpdate) and r.claim_id == claim_id
                for r in d.operations
            ):
                _reject("Duplicate captured grade update")
            d.operations.append(PendingClaimGradeUpdate(d.scope, claim_id, claim_grade, sanad_id))
        except Exception:
            d.poison()
            raise


class _CalculationWriter:
    def __init__(self, draft: SnapshotStepDraft) -> None:
        self._draft = draft

    def list_by_deal(self, deal_id: str) -> list[dict[str, Any]]:
        self._draft.require_deal(deal_id)
        rows = PostgresCalculationsRepository(
            self._draft.composition.provider_db_conn, self._draft.scope.tenant_id
        ).list_by_deal(deal_id)
        for row in rows:
            if (
                str(row.get("tenant_id")) != self._draft.scope.tenant_id
                or str(row.get("deal_id")) != deal_id
            ):
                self._draft.poison()
                _reject("Committed calculation read escaped the claimed scope")
            self._draft.known_calculation_pairs.add(
                (str(row["calc_id"]), row["reproducibility_hash"])
            )
        return [{**row, "calc_id": str(row["calc_id"])} for row in rows]

    def create(self, *, calculation: DeterministicCalculation, calc_sanad: CalcSanad) -> None:
        d = self._draft
        try:
            d.require_step(StepName.CALC)
            d.require_new_id(calculation.calc_id)
            d.require_new_id(calc_sanad.calc_sanad_id)
            if (
                calculation.tenant_id != d.scope.tenant_id
                or calculation.deal_id != d.scope.deal_id
                or calc_sanad.tenant_id != d.scope.tenant_id
                or calc_sanad.calc_id != calculation.calc_id
                or not set(calc_sanad.input_claim_ids) <= d.claim_ids
                or not set(calculation.inputs.claim_ids) <= d.claim_ids
                or calculation.inputs.claim_ids != calc_sanad.input_claim_ids
            ):
                _reject("Captured calculation pair has foreign scope or input claims")
            d.operations.append(
                PendingCalculationPair(
                    d.scope, calculation.model_dump_json(), calc_sanad.model_dump_json()
                )
            )
        except Exception:
            d.poison()
            raise


class _AuditRecorder:
    """Emit-only: intentionally cannot route a read facade to emit_in_tx."""

    def __init__(self, draft: SnapshotStepDraft) -> None:
        self._draft = draft

    def emit(self, event: dict[str, Any]) -> None:
        d = self._draft
        try:
            d.require_step(d.scope.step_name)
            if not validate_audit_event(event).passed:
                raise AuditSinkError("Captured SNAPSHOT audit is not canonical")
            resource = event["resource"]
            safe = event["payload"]["safe"]
            if (
                event["tenant_id"] != d.scope.tenant_id
                or resource.get("deal_id", d.scope.deal_id) != d.scope.deal_id
                or safe.get("run_id", d.scope.run_id) != d.scope.run_id
            ):
                raise AuditSinkError("Captured SNAPSHOT audit escaped its scope")
            event_type = event["event_type"]
            allowed = (
                {
                    "claim.created",
                    "claim.evidence.created",
                    "integration.extraction.pipeline_started",
                    "integration.extraction.pipeline_completed",
                    "integration.extraction.chunking_failed",
                    "integration.extraction.chunk_failed",
                }
                if d.scope.step_name == StepName.EXTRACT
                else {
                    "sanad.created",
                    "sanad.graded",
                    "sanad.integrity.failed",
                    "defect.created",
                    "defect.detected",
                }
                if d.scope.step_name == StepName.GRADE
                else set()
            )
            if event_type not in allowed:
                raise AuditSinkError("Unexpected SNAPSHOT producer audit channel")
            claims = d.claim_ids | {r.claim_id for r in d.claim_rows}
            if safe.get("claim_id") is not None and safe["claim_id"] not in claims:
                raise AuditSinkError("Captured audit references an unauthorized claim")
            kind, identity = resource["resource_type"], resource["resource_id"]
            _uuid(identity)
            if kind == "run" and identity != d.scope.run_id:
                raise AuditSinkError("Captured audit references a foreign run")
            if kind == "claim" and identity not in claims:
                raise AuditSinkError("Captured audit references a foreign claim")
            if kind not in {"run", "claim", "sanad", "defect"}:
                raise AuditSinkError("Captured audit has an unsupported resource")
            if (
                kind in {"sanad", "defect"}
                and event_type != "sanad.integrity.failed"
                and identity not in d.ids
            ):
                raise AuditSinkError("Captured audit has no matching output")
            if event_type == "sanad.integrity.failed" and safe.get("claim_id") not in claims:
                raise AuditSinkError("Integrity audit has no authorized attempted claim")
            event_id = event["event_id"]
            if event_id in d.audit_ids:
                raise AuditSinkError("Duplicate captured audit identity")
            d.audit_ids.add(event_id)
            d.operations.append(PendingCanonicalAudit(d.scope, _json(event)))
        except Exception:
            d.poison()
            raise


@dataclass
class SnapshotStepDraft:
    composition: SnapshotOutputComposition
    scope: OutputScope
    allowed_span_ids: frozenset[str]
    claim_ids: frozenset[str]
    operations: list[PendingOperation] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)
    audit_ids: set[str] = field(default_factory=set)
    poisoned: bool = False
    ready_summary: str | None = None
    known_calculation_pairs: set[tuple[str, str]] = field(default_factory=set)

    @property
    def claim_rows(self) -> tuple[PendingClaimCreate, ...]:
        return tuple(r for r in self.operations if isinstance(r, PendingClaimCreate))

    @property
    def claim_writer(self) -> _ClaimWriter:
        return _ClaimWriter(self)

    @property
    def evidence_repo(self) -> _EvidenceRecorder:
        return _EvidenceRecorder(self)

    @property
    def sanad_writer(self) -> _SanadWriter:
        return _SanadWriter(self)

    @property
    def defect_writer(self) -> _DefectWriter:
        return _DefectWriter(self)

    @property
    def grading_claims(self) -> _GradingClaims:
        return _GradingClaims(self)

    @property
    def calculations(self) -> _CalculationWriter:
        return _CalculationWriter(self)

    @property
    def audit_sink(self) -> _AuditRecorder:
        return _AuditRecorder(self)

    def evidence_reader(self) -> PostgresEvidenceRepository:
        return PostgresEvidenceRepository(self.composition.provider_db_conn, self.scope.tenant_id)

    def poison(self) -> None:
        self.poisoned = True

    def require_step(self, step_name: StepName) -> None:
        if self.scope.step_name != step_name or self.ready_summary is not None:
            self.poison()
            _reject("Captured write is outside its live producer draft")

    def require_deal(self, deal_id: str | None) -> None:
        if deal_id != self.scope.deal_id:
            self.poison()
            _reject("Captured operation is outside the claimed deal")

    def require_claim(self, claim_id: str | None) -> None:
        if claim_id not in self.claim_ids:
            self.poison()
            _reject("Captured operation is outside the run's extracted claim set")

    def require_new_id(self, identity: str) -> None:
        _uuid(identity)
        if identity in self.ids:
            _reject("Duplicate captured output identity")
        self.ids.add(identity)

    def mark_ready(self, summary: dict[str, Any]) -> None:
        if self.ready_summary is not None:
            self.poison()
            _reject("Producer draft was returned twice")
        self.ready_summary = _json(summary)


@dataclass
class _Packet:
    scope: OutputScope
    operations: tuple[PendingOperation, ...]
    returned: set[int] = field(default_factory=set)
    winner_checked: bool = False


class SnapshotOutputComposition:
    """One explicitly activated worker composition and shared registry/factories."""

    def __init__(self, *, provider_db_conn: Any, audit_sink: AuditSink) -> None:
        from idis.services.runs.steps import (
            build_postgres_native_output_stager,
            build_postgres_native_output_stager_registry,
        )

        self.provider_db_conn = provider_db_conn
        self.audit_sink = audit_sink
        self.active = False
        self.contracts = build_postgres_native_output_stager_registry()
        self.stage = build_postgres_native_output_stager(registry=self.contracts)
        self.repository_factories: dict[StepName, dict[type[Any], Callable[[Any], Any]]] = {}
        self._drafts: dict[StepName, SnapshotStepDraft] = {}
        self._packets: dict[StepName, _Packet] = {}
        self._failure_packets: dict[StepName, _Packet] = {}
        self._scope_identity: tuple[str, str, str, str | None, int | None] | None = None
        self._committed_claim_ids: tuple[str, ...] | None = None

    @staticmethod
    def _identity(ctx: RunContext) -> tuple[str, str, str, str | None, int | None]:
        return (ctx.tenant_id, ctx.deal_id, ctx.run_id, ctx.lease_owner_id, ctx.lease_fencing_token)

    def configure_base_factories(self, tenant_id: str) -> None:
        for step, contract in self.contracts.items():
            self.repository_factories[step] = {
                binding.repository_type: self._factory(binding.repository_type, tenant_id)
                for binding in contract.write_bindings
            }

    @staticmethod
    def _factory(repository_type: type[Any], tenant_id: str) -> Callable[[Any], Any]:
        return lambda conn: repository_type(conn, tenant_id)

    def activate(self, *, ctx: RunContext) -> None:
        from idis.audit.postgres_sink import PostgresAuditSink
        from idis.services.runs.steps import NativeOutputContract, NativeOutputWriteBinding

        if self.active or not callable(getattr(self.audit_sink, "emit_in_tx", None)):
            _reject("Worker SNAPSHOT composition is already active or lacks transactional audit")
        self._scope_identity = self._identity(ctx)
        audit = NativeOutputWriteBinding(
            PendingCanonicalAudit, PostgresAuditSink, "emit_in_tx", (), frozenset({"audit_events"})
        )
        specifications = {
            StepName.EXTRACT: (
                NativeOutputWriteBinding(
                    PendingClaimCreate,
                    ClaimsRepository,
                    "create",
                    ("list_by_deal",),
                    frozenset({"claims"}),
                ),
                NativeOutputWriteBinding(
                    PendingEvidenceCreate,
                    PostgresEvidenceRepository,
                    "create",
                    ("get_by_claim",),
                    frozenset({"evidence_items"}),
                ),
                audit,
            ),
            StepName.GRADE: (
                NativeOutputWriteBinding(
                    PendingClaimGradeUpdate,
                    ClaimsRepository,
                    "update_grade",
                    ("list_by_deal",),
                    frozenset({"claims"}),
                ),
                NativeOutputWriteBinding(
                    PendingSanadCreate,
                    SanadsRepository,
                    "create",
                    ("list_by_deal",),
                    frozenset({"sanads"}),
                ),
                NativeOutputWriteBinding(
                    PendingDefectCreate,
                    DefectsRepository,
                    "create",
                    ("list_by_deal",),
                    frozenset({"defects"}),
                ),
                audit,
            ),
            StepName.CALC: (
                NativeOutputWriteBinding(
                    PendingCalculationPair,
                    PostgresCalculationsRepository,
                    "create",
                    ("list_by_deal", "list_calc_sanads_by_deal"),
                    frozenset({"deterministic_calculations", "calc_sanads"}),
                ),
                audit,
            ),
        }
        for step, bindings in specifications.items():
            self.contracts[step] = NativeOutputContract(
                step,
                "fresh_snapshot_legacy_output",
                "postgres:legacy_snapshot_complete",
                tuple,
                bindings,
                lambda **_kwargs: (),
            )
            factories = self.repository_factories.setdefault(step, {})
            for binding in bindings:
                if binding.repository_type is PostgresAuditSink:
                    factories[binding.repository_type] = lambda _conn: self.audit_sink
                else:
                    factories[binding.repository_type] = self._factory(
                        binding.repository_type, ctx.tenant_id
                    )
        ctx.extract_fn = self._extract
        ctx.grade_fn = self._grade
        ctx.calc_fn = self._calc
        self.active = True
        self._context = ctx

    def begin_step(
        self, *, ctx: RunContext, step_name: StepName, claim_ids: list[str] | None = None
    ) -> SnapshotStepDraft:
        if step_name not in SNAPSHOT_OUTPUT_STEPS or step_name in self._drafts:
            _reject("SNAPSHOT output has no new producer draft")
        if self._scope_identity is not None and self._scope_identity != self._identity(ctx):
            _reject("SNAPSHOT output context changed after activation")
        ids = claim_ids or []
        if len(set(ids)) != len(ids):
            _reject("Duplicate run input claim identity")
        if step_name in {StepName.GRADE, StepName.CALC} and (
            self._committed_claim_ids is None or tuple(ids) != self._committed_claim_ids
        ):
            _reject("Downstream SNAPSHOT claims differ from the committed extraction packet")
        allowed = frozenset(
            str(s["span_id"])
            for d in ctx.documents
            for s in d.get("spans", [])
            if isinstance(s, dict) and s.get("span_id")
        )
        draft = SnapshotStepDraft(
            self, OutputScope.from_context(ctx, step_name), allowed, frozenset(ids)
        )
        self._drafts[step_name] = draft
        return draft

    def _extract(self, **kwargs: Any) -> dict[str, Any]:
        from idis.api.routes.runs import _run_snapshot_extraction

        self._require_producer_scope(kwargs)
        if kwargs["documents"] != self._context.documents:
            _reject("Extraction inputs differ from the pinned frozen documents")
        d = self.begin_step(ctx=self._context, step_name=StepName.EXTRACT)
        summary = _run_snapshot_extraction(
            **kwargs,
            db_conn=self.provider_db_conn,
            claim_create_writer=d.claim_writer,
            evidence_repo=d.evidence_repo,
            staged_audit_sink=d.audit_sink,
            on_persistence_failure=d.poison,
        )
        d.mark_ready(summary)
        return summary

    def _grade(self, **kwargs: Any) -> dict[str, Any]:
        from idis.api.routes.runs import _run_snapshot_auto_grade

        self._require_producer_scope(kwargs)
        d = self.begin_step(
            ctx=self._context, step_name=StepName.GRADE, claim_ids=kwargs["created_claim_ids"]
        )
        summary = _run_snapshot_auto_grade(
            **kwargs,
            db_conn=self.provider_db_conn,
            claims_grading_repo=d.grading_claims,
            sanad_create_writer=d.sanad_writer,
            defect_create_writer=d.defect_writer,
            evidence_repo=d.evidence_repo,
            staged_audit_sink=d.audit_sink,
        )
        d.mark_ready(summary)
        return summary

    def _calc(self, **kwargs: Any) -> dict[str, Any]:
        from idis.api.routes.runs import _run_snapshot_calc

        self._require_producer_scope(kwargs)
        d = self.begin_step(
            ctx=self._context, step_name=StepName.CALC, claim_ids=kwargs["created_claim_ids"]
        )
        summary = _run_snapshot_calc(
            **kwargs, db_conn=self.provider_db_conn, calculations_writer=d.calculations
        )
        d.mark_ready(summary)
        return summary

    def _require_producer_scope(self, kwargs: dict[str, Any]) -> None:
        if any(
            kwargs.get(key) != getattr(self._context, key)
            for key in ("tenant_id", "deal_id", "run_id")
        ):
            _reject("Producer arguments differ from the claimed SNAPSHOT scope")

    def seal_success(
        self, *, ctx: RunContext, step_name: StepName, result_summary: dict[str, Any]
    ) -> None:
        d = self._drafts.get(step_name)
        if d is None or d.poisoned or d.ready_summary is None:
            _reject("SNAPSHOT producer draft is missing, poisoned or changed after return")
        assert d is not None and d.ready_summary is not None
        ready_summary = json.loads(d.ready_summary)
        if step_name == StepName.EXTRACT:
            # The unchanged orchestrator merges via its deterministic sorted set.
            # Match that order while requiring exact captured identities below.
            ready_summary["created_claim_ids"] = sorted(ready_summary.get("created_claim_ids", []))
        if _json(ready_summary) != _json(result_summary):
            _reject("SNAPSHOT producer summary changed outside its deterministic claim merge")
        if d.scope != OutputScope.from_context(ctx, step_name) or step_name in self._packets:
            _reject("SNAPSHOT output packet has foreign scope or was already sealed")
        if step_name == StepName.EXTRACT:
            claims = d.claim_rows
            claim_ids = sorted(r.claim_id for r in claims)
            evidence = [r for r in d.operations if isinstance(r, PendingEvidenceCreate)]
            if (
                result_summary.get("created_claim_ids") != claim_ids
                or result_summary.get("unique_claim_count") != len(claims)
                or any(
                    not any(
                        e.claim_id == c.claim_id and e.source_span_id == c.primary_span_id
                        for e in evidence
                    )
                    for c in claims
                )
            ):
                d.poison()
                _reject("Merged extraction output does not exactly match captured backed claims")
        elif step_name == StepName.GRADE:
            sanads = [row for row in d.operations if isinstance(row, PendingSanadCreate)]
            updates = [row for row in d.operations if isinstance(row, PendingClaimGradeUpdate)]
            defects = [row for row in d.operations if isinstance(row, PendingDefectCreate)]
            graded = result_summary.get("graded_count")
            failed = result_summary.get("failed_count")
            if (
                type(graded) is not int
                or type(failed) is not int
                or graded < 0
                or failed < 0
                or graded + failed != len(d.claim_ids)
                or graded != len(sanads)
                or graded != len(updates)
                or result_summary.get("total_defects") != len(defects)
                or {(row.claim_id, row.sanad_id) for row in sanads}
                != {(row.claim_id, row.sanad_id) for row in updates}
            ):
                d.poison()
                _reject(
                    "Grading summary does not exactly match captured Sanads, grades and defects"
                )
        elif step_name == StepName.CALC:
            pairs = [row for row in d.operations if isinstance(row, PendingCalculationPair)]
            new_pairs = {
                (row.calculation.calc_id, row.calculation.reproducibility_hash) for row in pairs
            }
            ids = result_summary.get("calc_ids", [])
            hashes = result_summary.get("reproducibility_hashes", [])
            returned_pairs = set(zip(ids, hashes, strict=False))
            if (
                len(ids) != len(hashes)
                or result_summary.get("persisted_count") != len(pairs)
                or not new_pairs <= returned_pairs
                or not returned_pairs <= new_pairs | d.known_calculation_pairs
            ):
                d.poison()
                _reject("Calculation summary contains uncaptured or unread output identities")
        packet = _Packet(d.scope, tuple(d.operations))
        self._packets[step_name] = packet
        ctx.methodology_pending_native_output_writes[step_name] = self._writes(packet)

    def _writes(self, packet: _Packet) -> tuple[StagedNativeOutputWrite, ...]:
        from idis.services.runs.steps import StagedNativeOutputWrite

        contract = self.contracts.get(packet.scope.step_name)
        if contract is None:
            _reject("SNAPSHOT packet has no active output contract")
        assert contract is not None
        writes = []
        for index, operation in enumerate(packet.operations):
            binding = next(
                (b for b in contract.write_bindings if type(operation) is b.row_model_type),
                None,
            )
            if binding is None:
                _reject("SNAPSHOT operation has no active output binding")

            def persist(
                *,
                db_conn: Any,
                lease_owner_id: str,
                lease_fencing_token: int,
                index: int = index,
                operation: PendingOperation = operation,
                binding: NativeOutputWriteBinding = binding,
            ) -> None:
                if (
                    lease_owner_id != packet.scope.lease_owner_id
                    or lease_fencing_token != packet.scope.lease_fencing_token
                    or index in packet.returned
                ):
                    _reject("SNAPSHOT output custody lease changed or operation repeated")
                self._persist(packet, operation, binding, db_conn)
                packet.returned.add(index)

            writes.append(StagedNativeOutputWrite(contract, binding, operation, persist))
        return tuple(writes)

    def _persist(
        self,
        packet: _Packet,
        row: PendingOperation,
        binding: NativeOutputWriteBinding,
        db_conn: Any,
    ) -> None:
        if isinstance(row, PendingCanonicalAudit):
            emit = getattr(self.audit_sink, "emit_in_tx", None)
            if not callable(emit):
                _reject("Configured producer audit sink lost its transactional port")
            emit(db_conn, row.event)
            return
        repository = self.repository_factories[packet.scope.step_name][binding.repository_type](
            db_conn
        )
        if isinstance(row, PendingClaimGradeUpdate):
            repository.update_grade(
                row.claim_id, claim_grade=row.claim_grade, sanad_id=row.sanad_id
            )
        elif isinstance(row, PendingCalculationPair):
            if not packet.winner_checked:
                committed = repository.list_by_deal(packet.scope.deal_id)
                for pending in packet.operations:
                    if isinstance(pending, PendingCalculationPair):
                        calculation = pending.calculation
                        if any(
                            r["reproducibility_hash"] == calculation.reproducibility_hash
                            and str(r["calc_id"]) != calculation.calc_id
                            for r in committed
                        ):
                            _reject("An intervening committed calculation won this planned hash")
                packet.winner_checked = True
            repository.create(calculation=row.calculation, calc_sanad=row.calc_sanad)
        else:
            result = repository.create(**row.arguments())
            key = (
                "claim_id"
                if isinstance(row, PendingClaimCreate)
                else "evidence_id"
                if isinstance(row, PendingEvidenceCreate)
                else "sanad_id"
                if isinstance(row, PendingSanadCreate)
                else "defect_id"
            )
            if result.get(key) != row.arguments()[key]:
                _reject("Repository returned an output identity different from its sealed input")

    def before_complete_step(self, *, ctx: RunContext, step_name: StepName) -> None:
        packet = self._packets.get(step_name)
        if (
            packet is None
            or packet.scope != OutputScope.from_context(ctx, step_name)
            or packet.returned != set(range(len(packet.operations)))
        ):
            _reject("SNAPSHOT COMPLETE omitted a sealed row or audit operation")

    def complete_committed(self, *, ctx: RunContext, step_name: StepName) -> None:
        self.before_complete_step(ctx=ctx, step_name=step_name)
        if step_name == StepName.EXTRACT:
            self._committed_claim_ids = tuple(
                sorted(
                    row.claim_id
                    for row in self._packets[step_name].operations
                    if isinstance(row, PendingClaimCreate)
                )
            )
        self._packets.pop(step_name)
        self._drafts.pop(step_name, None)

    def boundary_failed(self, *, ctx: RunContext, step_name: StepName) -> None:
        self._packets.pop(step_name, None)
        self._drafts.pop(step_name, None)
        self._failure_packets.pop(step_name, None)
        ctx.methodology_pending_native_output_writes.pop(step_name, None)

    def abort_step_and_stage_failure_audits(
        self, *, ctx: RunContext, step_name: StepName
    ) -> tuple[StagedNativeOutputWrite, ...]:
        self._packets.pop(step_name, None)
        ctx.methodology_pending_native_output_writes.pop(step_name, None)
        draft = self._drafts.pop(step_name, None)
        rows = (
            tuple(
                r
                for r in draft.operations
                if isinstance(r, PendingCanonicalAudit)
                and r.event["event_type"] in FAILURE_AUDIT_TYPES
            )
            if draft
            else ()
        )
        packet = _Packet(OutputScope.from_context(ctx, step_name), rows)
        self._failure_packets[step_name] = packet
        return self._writes(packet)

    def before_failed_step(self, *, ctx: RunContext, step_name: StepName) -> None:
        packet = self._failure_packets.get(step_name)
        if (
            packet is None
            or packet.scope != OutputScope.from_context(ctx, step_name)
            or packet.returned != set(range(len(packet.operations)))
        ):
            _reject("SNAPSHOT FAILED omitted a captured failure audit")

    def discard_all(self, *, ctx: RunContext) -> None:
        for step_name in SNAPSHOT_OUTPUT_STEPS:
            self.boundary_failed(ctx=ctx, step_name=step_name)
