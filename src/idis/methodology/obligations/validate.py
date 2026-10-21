"""Validate a private obligation register.

``validate_register`` never writes into the private root. I-16 is reserved.
"""

from __future__ import annotations

from pathlib import Path

from idis.methodology.obligations.checks import (
    check_accounting,
    check_active_children,
    check_commercial_alignment,
    check_determinism,
    check_field_completeness,
    check_importer_parity,
    check_named_clarifications,
    check_parent_coverage,
    check_plan_minimum,
    check_policy_coverage,
    check_raw_text_integrity,
    check_scope_retention,
    check_shared_instructions,
    check_source_coverage,
)
from idis.methodology.obligations.checks_links import check_baseline, check_views
from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.findings import make_finding
from idis.methodology.obligations.models_records import Finding, ValidationReport
from idis.methodology.obligations.register_io import Register, load_register, parent_shard
from idis.methodology.obligations.vocab import (
    SKIPPED_BASELINE_ABSENT,
    SKIPPED_LEDGER_LATER,
    SKIPPED_POLICY_ABSENT,
    SKIPPED_VIEWS_ABSENT,
    SKIPPED_WORKBOOK_ABSENT,
    Code,
    FindingSeverity,
    InvariantStatus,
    Shard,
)

_INVARIANT_CODES: dict[str, frozenset[str]] = {
    "I-01": frozenset(
        {
            Code.GL02_SOURCE_COVERAGE.value,
            Code.GL02_SOURCE_PIN_MISMATCH.value,
            Code.GL02_SOURCE_FEATURE_PRESENT.value,
            Code.GL02_SOURCE_OTHER_CELL.value,
        }
    ),
    "I-02": frozenset({Code.GL02_IMPORTER_PARITY_FAILED.value}),
    "I-03": frozenset({Code.GL02_PARENT_COVERAGE.value}),
    "I-04": frozenset({Code.GL02_PARENT_WITHOUT_CHILD.value}),
    "I-05": frozenset({Code.GL02_RAW_TEXT_INTEGRITY.value}),
    "I-06": frozenset(
        {
            Code.GL02_RUN_UNDISPOSITIONED.value,
            Code.GL02_DISPOSITION_STALE.value,
            Code.GL02_DISPOSITION_DUPLICATE.value,
            Code.GL02_DISPOSITION_KIND_INVALID.value,
            Code.GL02_DISPOSITION_RESTATES_UNLINKED.value,
        }
    ),
    "I-06b": frozenset({Code.GL02_SEGMENT_NOT_FOCUSED.value}),
    "I-07": frozenset(
        {
            Code.GL02_SHARED_INSTRUCTION_UNAPPLIED.value,
            Code.GL02_CROSS_ROW_UNAPPLIED.value,
            Code.GL02_CROSS_ROW_TARGET_EMPTY.value,
            Code.GL02_CROSS_ROW_SHARD.value,
            Code.GL02_MARKER_UNACCOUNTED.value,
        }
    ),
    "I-08": frozenset(
        {
            Code.GL02_FIELD_UNRESOLVED.value,
            Code.GL02_DERIVATION_RATIONALE.value,
            Code.GL02_QUOTE_REQUIRED.value,
            Code.GL02_QUOTE_NOT_FOUND.value,
            Code.GL02_QUOTE_FOLD_MATCH_ONLY.value,
            Code.GL02_QUOTE_FIELD_INVALID.value,
            Code.GL02_CHILD_ID_INVALID.value,
            Code.GL02_CHILD_ID_DUPLICATE.value,
            Code.GL02_RETIRED_REASON.value,
            Code.GL02_FACT_RULE.value,
            Code.GL02_JOINT_REASON_MISSING.value,
            Code.GL02_SPECIALIST_INVALID.value,
            Code.GL02_CATEGORY_FORBIDDEN.value,
            Code.GL02_EVIDENCE_TYPE.value,
            Code.GL02_DERIVED_RULE_UNKNOWN.value,
            Code.GL02_POLICY_RULE_UNKNOWN.value,
            Code.GL02_RECORD_SHARD_MISMATCH.value,
        }
    ),
    "I-09": frozenset({Code.GL02_PLAN_MINIMUM_MISSING.value, Code.GL02_PLAN_MINIMUM_UNKNOWN.value}),
    "I-10": frozenset({Code.GL02_POLICY_UNCARRIED.value, Code.GL02_POLICY_ROW_MISMATCH.value}),
    "I-11": frozenset({Code.GL02_AMBIGUITY_TOPIC_MISSING.value}),
    "I-12": frozenset({Code.GL02_SCOPE_NOT_RETAINED.value}),
    "I-13": frozenset(
        {
            Code.GL02_COMMERCIAL_DESTINATION.value,
            Code.GL02_COMMERCIAL_CATEGORY.value,
            Code.GL02_COMMERCIAL_COLUMN.value,
            Code.GL02_ROLE_ALIAS_MISSING.value,
        }
    ),
    "I-14": frozenset({Code.GL02_DERIVED_STALE.value}),
    "I-15": frozenset({Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value}),
    "I-16": frozenset({Code.GL02_LEDGER_INTEGRITY.value}),
    "I-17": frozenset({Code.GL02_BASELINE_UNMET.value}),
    "I-18": frozenset({Code.GL02_VIEW_INVALID.value}),
}
_SKIP_FOR = {
    "I-10": SKIPPED_POLICY_ABSENT,
    "I-14": SKIPPED_WORKBOOK_ABSENT,
    "I-16": SKIPPED_LEDGER_LATER,
    "I-17": SKIPPED_BASELINE_ABSENT,
    "I-18": SKIPPED_VIEWS_ABSENT,
}


def validate_register(
    private_root: Path | str,
    *,
    workbook_path: Path | str | None = None,
    policy_path: Path | str | None = None,
    shards: list[str] | None = None,
    view: str | None = None,
) -> ValidationReport:
    """Validate ``private_root``. A skipped required check is not a pass."""
    register = load_register(private_root)
    shard_set = None if shards is None else frozenset(Shard(item) for item in shards)
    workbook = None if workbook_path is None else Path(workbook_path)
    policy = None if policy_path is None else Path(policy_path)
    findings: list[Finding] = list(register.shard_findings)
    skipped: list[str] = [SKIPPED_LEDGER_LATER]
    skipped_invariants: list[tuple[str, str]] = [("I-16", SKIPPED_LEDGER_LATER)]
    view_findings, closures = check_views(register) if register.views_present else ([], {})
    if not register.views_present:
        skipped.append(SKIPPED_VIEWS_ABSENT)
        skipped_invariants.append(("I-18", SKIPPED_VIEWS_ABSENT))
    elif view is not None and view not in closures:
        findings.append(make_finding(Code.GL02_VIEW_INVALID.value, ids=(view,)))
    limit = _limit(register, shard_set, None if view is None else closures.get(view))
    if register.views_present:
        findings.extend(view_findings)
    findings.extend(check_source_coverage(register.extraction_report))
    findings.extend(check_importer_parity(register.extraction_report))
    findings.extend(check_parent_coverage(register))
    findings.extend(check_raw_text_integrity(register))
    policy_compare_skipped = (
        workbook is not None and policy is None and register.extraction_report.policy is not None
    )
    if workbook is None:
        skipped.append(SKIPPED_WORKBOOK_ABSENT)
        skipped_invariants.append(("I-14", SKIPPED_WORKBOOK_ABSENT))
    elif policy_compare_skipped:
        skipped.append(SKIPPED_POLICY_ABSENT)
        skipped_invariants.append(("I-14", SKIPPED_POLICY_ABSENT))
    else:
        findings.extend(check_determinism(register, workbook, policy))
    findings.extend(check_active_children(register, limit))
    findings.extend(check_accounting(register, limit))
    findings.extend(check_shared_instructions(register, limit, shard_set))
    findings.extend(check_field_completeness(register, limit))
    findings.extend(check_plan_minimum(register, shard_set))
    policy_present = policy is not None or register.extraction_report.policy is not None
    if policy_present:
        findings.extend(check_policy_coverage(register, True))
        findings.extend(
            make_finding(code, scope="policy", ids=(code,))
            for code in register.extraction_report.finding_codes
            if code == Code.GL02_POLICY_ROW_MISMATCH.value
        )
    else:
        skipped.append(SKIPPED_POLICY_ABSENT)
        skipped_invariants.append(("I-10", SKIPPED_POLICY_ABSENT))
    findings.extend(check_named_clarifications(register, shard_set))
    findings.extend(check_scope_retention(register, limit))
    findings.extend(check_commercial_alignment(register, limit))
    if register.baseline_present:
        findings.extend(check_baseline(register, limit))
    else:
        skipped.append(SKIPPED_BASELINE_ABSENT)
        skipped_invariants.append(("I-17", SKIPPED_BASELINE_ABSENT))
    findings.extend(_public_safe(register, findings, skipped, skipped_invariants))
    ordered = tuple(
        sorted(findings, key=lambda item: (item.code, item.severity.value, item.scope, item.ids))
    )
    skip_tuple = tuple(sorted(set(skipped)))
    skip_pairs = tuple(sorted(set(skipped_invariants)))
    return ValidationReport(
        findings=ordered,
        skipped_checks=skip_tuple,
        register_complete=_complete(ordered, skip_tuple),
        skipped_invariants=skip_pairs,
    )


def invariant_status(invariant_id: str, report: ValidationReport) -> str:
    """Return pass, fail or skipped for one invariant."""
    codes = _INVARIANT_CODES.get(invariant_id, frozenset())
    if any(
        finding.code in codes and finding.severity is FindingSeverity.ERROR
        for finding in report.findings
    ):
        return InvariantStatus.FAIL.value
    if report.skipped_invariants:
        if any(item_id == invariant_id for item_id, _skip in report.skipped_invariants):
            return InvariantStatus.SKIPPED.value
    else:
        skip = _SKIP_FOR.get(invariant_id)
        if skip is not None and skip in report.skipped_checks:
            return InvariantStatus.SKIPPED.value
    if invariant_id in {"I-09", "I-11"} and any(
        finding.code == Code.PENDING_CROSS_SHARD.value for finding in report.findings
    ):
        return InvariantStatus.SKIPPED.value
    return InvariantStatus.PASS.value


def _public_safe(
    register: Register,
    findings: list[Finding],
    skipped: list[str],
    skipped_invariants: list[tuple[str, str]],
) -> list[Finding]:
    from idis.methodology.obligations.manifest import assemble_public_manifest, assert_public_safe

    provisional = ValidationReport(
        findings=tuple(findings),
        skipped_checks=tuple(sorted(set(skipped))),
        register_complete=False,
        skipped_invariants=tuple(sorted(set(skipped_invariants))),
    )
    try:
        assert_public_safe(assemble_public_manifest(register, provisional))
    except Gl02Error:
        return [make_finding(Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value, scope="manifest")]
    return []


def _limit(
    register: Register,
    shards: frozenset[Shard] | None,
    view_ids: frozenset[str] | None,
) -> frozenset[str] | None:
    if shards is None and view_ids is None:
        return None
    selected = {parent.parent_id for parent in register.parents}
    if view_ids is not None:
        selected &= set(view_ids)
    if shards is not None:
        selected = {
            parent_id
            for parent_id in selected
            if parent_shard(register.parents_by_id[parent_id]) in shards
        }
    return frozenset(selected)


def _complete(findings: tuple[Finding, ...], skipped: tuple[str, ...]) -> bool:
    """False while an error, a cross-shard pending item, or any skipped check remains.

    Rule I-16 is required, so ``SKIPPED_LEDGER_LATER`` keeps the register incomplete.
    """
    if any(finding.severity is FindingSeverity.ERROR for finding in findings):
        return False
    if any(finding.code == Code.PENDING_CROSS_SHARD.value for finding in findings):
        return False
    return not skipped
