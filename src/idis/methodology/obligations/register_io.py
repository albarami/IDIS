"""Load a private register and resolve child fields against parent profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.findings import make_finding
from idis.methodology.obligations.jsonl import read_json, read_jsonl
from idis.methodology.obligations.models_authored import (
    Ambiguity,
    BaselineExpectation,
    ChildObligation,
    CrossRowInstruction,
    Disposition,
    FundFeedback,
    LedgerRecord,
    OmissionAuditRecord,
    ParentProfile,
    ResolvedChild,
    RoleAlias,
    SealRecord,
    ViewRecord,
)
from idis.methodology.obligations.models_records import (
    ExtractionReport,
    Finding,
    MergedInstruction,
    ParentRecord,
    PolicyViewRecord,
    SourceCell,
)
from idis.methodology.obligations.vocab import SHARD_ORDER, SHEET_SHARDS, Code, ParentKind, Shard

M = TypeVar("M", bound=BaseModel)
T = TypeVar("T")

DERIVED_NAMES: tuple[str, ...] = (
    "source_cells.jsonl",
    "parents.jsonl",
    "merged_instructions.jsonl",
    "policy_view.jsonl",
    "extraction_report.private.json",
)


@dataclass(frozen=True, slots=True)
class Register:
    """Every record the validator and the manifest read."""

    root: Path
    source_cells: tuple[SourceCell, ...]
    parents: tuple[ParentRecord, ...]
    merged_instructions: tuple[MergedInstruction, ...]
    policy_views: tuple[PolicyViewRecord, ...]
    extraction_report: ExtractionReport
    profiles: tuple[tuple[Shard, ParentProfile], ...]
    children: tuple[tuple[Shard, ChildObligation], ...]
    cross_rows: tuple[tuple[Shard, CrossRowInstruction], ...]
    dispositions: tuple[tuple[Shard, Disposition], ...]
    ambiguities: tuple[tuple[Shard, Ambiguity], ...]
    omission_audits: tuple[tuple[Shard, OmissionAuditRecord], ...]
    role_aliases: tuple[tuple[Shard, RoleAlias], ...]
    baseline: tuple[tuple[Shard, BaselineExpectation], ...]
    baseline_present: bool
    views: tuple[tuple[Shard, ViewRecord], ...]
    views_present: bool
    ledger: tuple[LedgerRecord, ...]
    seals: tuple[SealRecord, ...]
    shard_findings: tuple[Finding, ...]

    def parent(self, parent_id: str) -> ParentRecord | None:
        """Return the parent with this id."""
        return self.parents_by_id.get(parent_id)

    @property
    def parents_by_id(self) -> dict[str, ParentRecord]:
        """Parents keyed by identifier."""
        return {parent.parent_id: parent for parent in self.parents}

    @property
    def parents_by_coordinate(self) -> dict[str, ParentRecord]:
        """Parents keyed by public coordinate."""
        return {parent.public_coordinate: parent for parent in self.parents}


def load_register(private_root: Path | str) -> Register:
    """Load derived files and every authored shard that exists."""
    root = Path(private_root)
    derived = root / "derived"
    try:
        source_cells = read_jsonl(derived / "source_cells.jsonl", SourceCell)
        parents = read_jsonl(derived / "parents.jsonl", ParentRecord)
        merged = read_jsonl(derived / "merged_instructions.jsonl", MergedInstruction)
        policy = read_jsonl(derived / "policy_view.jsonl", PolicyViewRecord)
        report_payload = read_json(derived / "extraction_report.private.json")
        report = ExtractionReport.model_validate(report_payload)
    except Gl02Error:
        raise
    except (OSError, ValueError) as exc:
        raise Gl02Error(Code.GL02_IO.value) from exc
    by_id = {parent.parent_id: parent for parent in parents}
    findings: list[Finding] = []
    profiles = _load_authored(root, "profiles", ParentProfile, by_id, findings)
    children = _load_authored(root, "children", ChildObligation, by_id, findings)
    cross_rows = _load_authored(root, "cross_row", CrossRowInstruction, by_id, findings)
    dispositions = _load_authored(root, "dispositions", Disposition, by_id, findings)
    ambiguities = _load_authored(root, "ambiguities", Ambiguity, by_id, findings)
    audits = _load_authored(root, "omission_audit", OmissionAuditRecord, by_id, findings)
    aliases = _load_authored(root, "role_aliases", RoleAlias, by_id, findings)
    baseline_path = root / "authored" / "baseline" / "XS.jsonl"
    views_path = root / "authored" / "views" / "XS.jsonl"
    baseline = _load_authored(root, "baseline", BaselineExpectation, by_id, findings)
    views = _load_authored(root, "views", ViewRecord, by_id, findings)
    ledger_path = root / "ledger" / "issued_ids.jsonl"
    seals_path = root / "ledger" / "seals.jsonl"
    ledger = read_jsonl(ledger_path, LedgerRecord) if ledger_path.is_file() else ()
    seals = read_jsonl(seals_path, SealRecord) if seals_path.is_file() else ()
    return Register(
        root=root,
        source_cells=source_cells,
        parents=parents,
        merged_instructions=merged,
        policy_views=policy,
        extraction_report=report,
        profiles=profiles,
        children=children,
        cross_rows=cross_rows,
        dispositions=dispositions,
        ambiguities=ambiguities,
        omission_audits=audits,
        role_aliases=aliases,
        baseline=baseline,
        baseline_present=baseline_path.is_file(),
        views=views,
        views_present=views_path.is_file(),
        ledger=ledger,
        seals=seals,
        shard_findings=tuple(findings),
    )


def parent_shard(parent: ParentRecord) -> Shard:
    """Shard that owns a parent."""
    if parent.parent_kind is ParentKind.COMMERCIAL:
        return Shard.CDD
    if parent.sheet is None:
        raise Gl02Error(Code.GL02_RECORD_SHARD_MISMATCH.value)
    return SHEET_SHARDS[parent.sheet]


def resolve_child(child: ChildObligation, profile: ParentProfile | None) -> ResolvedChild:
    """Fill null child fields from the parent profile."""
    fund = child.fund_feedback
    if fund is None and profile is not None and profile.default_fund_feedback_status is not None:
        fund = FundFeedback(status=profile.default_fund_feedback_status, note=None)
    return ResolvedChild(
        child_id=child.child_id,
        parent_id=child.parent_id,
        status=child.status,
        retired_reason=child.retired_reason,
        derivation=child.derivation,
        derivation_rationale=child.derivation_rationale,
        source_quotes=child.source_quotes,
        joint_answer_reason=child.joint_answer_reason,
        shared_instruction_ids=child.shared_instruction_ids,
        cross_row_instruction_ids=child.cross_row_instruction_ids,
        plan_minimum_codes=child.plan_minimum_codes,
        obligation_statement=child.obligation_statement,
        required_facts=child.required_facts,
        calculation_rules=child.calculation_rules,
        judgment_sought=child.judgment_sought,
        acceptable_evidence=child.acceptable_evidence,
        completeness_conditions=child.completeness_conditions,
        applicability=child.applicability,
        coordinates=_pick(
            child.coordinates, None if profile is None else profile.default_coordinates
        ),
        producer=_pick(child.producer, None if profile is None else profile.default_producer),
        consumer=_pick(child.consumer, None if profile is None else profile.default_consumer),
        responsible_specialist=_pick(
            child.responsible_specialist,
            None if profile is None else profile.default_specialists,
        ),
        analyst_review=_pick(
            child.analyst_review,
            None if profile is None else profile.default_analyst_review,
        ),
        output_destination=_pick(
            child.output_destination,
            None if profile is None else profile.default_output_destination,
        ),
        capability=_pick(child.capability, None if profile is None else profile.default_capability),
        acceptance_case=child.acceptance_case,
        acceptance_evidence_refs=child.acceptance_evidence_refs,
        ambiguity_ids=child.ambiguity_ids,
        fund_feedback=fund,
    )


def _pick(explicit: T | None, default: T | None) -> T | None:
    return explicit if explicit is not None else default


def _load_authored(
    root: Path,
    kind: str,
    model: type[M],
    parents: dict[str, ParentRecord],
    findings: list[Finding],
) -> tuple[tuple[Shard, M], ...]:
    loaded: list[tuple[Shard, M]] = []
    for shard in SHARD_ORDER:
        path = root / "authored" / kind / f"{shard.value}.jsonl"
        if not path.is_file():
            continue
        for record in read_jsonl(path, model):
            loaded.append((shard, record))
            _shard_check(kind, shard, record, parents, findings)
    return tuple(loaded)


def _shard_check(
    kind: str,
    shard: Shard,
    record: BaseModel,
    parents: dict[str, ParentRecord],
    findings: list[Finding],
) -> None:
    if kind == "role_aliases" and shard is not Shard.CDD:
        findings.append(
            make_finding(Code.GL02_RECORD_SHARD_MISMATCH.value, scope=shard.value, ids=(kind,))
        )
        return
    if kind in {"baseline", "views"} and shard is not Shard.XS:
        findings.append(
            make_finding(Code.GL02_RECORD_SHARD_MISMATCH.value, scope=shard.value, ids=(kind,))
        )
        return
    if shard is Shard.XS and kind in {"ambiguities", "omission_audit", "baseline", "views"}:
        return
    parent_ids = _record_parent_ids(kind, record)
    for parent_id in parent_ids:
        parent = parents.get(parent_id)
        if parent is None:
            findings.append(
                make_finding(
                    Code.GL02_RECORD_SHARD_MISMATCH.value, scope=shard.value, ids=(parent_id,)
                )
            )
            continue
        expected = parent_shard(parent)
        if kind == "cross_row" and parent_id == getattr(record, "source_parent_id", None):
            continue
        if expected is not shard:
            findings.append(
                make_finding(
                    Code.GL02_RECORD_SHARD_MISMATCH.value,
                    scope=shard.value,
                    ids=(parent_id,),
                )
            )


def _record_parent_ids(kind: str, record: BaseModel) -> tuple[str, ...]:
    if kind == "ambiguities":
        return tuple(getattr(record, "affected_parent_ids", ()))
    if kind == "cross_row":
        source = getattr(record, "source_parent_id", None)
        targets = getattr(record, "target_parent_ids", ())
        if isinstance(source, str):
            return (source, *[item for item in targets if isinstance(item, str)])
        return ()
    if kind in {"baseline", "views", "role_aliases"}:
        return ()
    parent_id = getattr(record, "parent_id", None)
    if isinstance(parent_id, str):
        return (parent_id,)
    return ()
