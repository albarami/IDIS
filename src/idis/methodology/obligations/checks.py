"""Invariant checks I-01 through I-14, I-17 and I-18.

I-16 is reserved and is not implemented.
"""

from __future__ import annotations

from pathlib import Path

from idis.methodology.category_mapping import load_commercial_dd_question_category_map
from idis.methodology.ids import normalize_text
from idis.methodology.importers.cdd_json import import_cdd_template
from idis.methodology.obligations.checks_support import (
    _active,
    _commercial_parent,
    _cross_row_findings,
    _in_limit,
    _one_child,
    _packaged_template,
    _plan_shard,
    _raw_matches_cell,
)
from idis.methodology.obligations.extract import extract_register
from idis.methodology.obligations.findings import make_finding
from idis.methodology.obligations.jsonl import sha256_text
from idis.methodology.obligations.models_records import (
    ExtractionReport,
    Finding,
)
from idis.methodology.obligations.register_io import (
    DERIVED_NAMES,
    Register,
    parent_shard,
)
from idis.methodology.obligations.runs import account_parent
from idis.methodology.obligations.runs_focus import joint_reason_findings
from idis.methodology.obligations.vocab import (
    EXPECTED_DATA_ROW_COUNTS,
    EXPECTED_MERGED_RANGE_COUNTS,
    LIAB_SCOPE_FIRST_ROW,
    LIAB_SCOPE_LAST_ROW,
    NAMED_AMBIGUITY_TOPICS,
    PINNED_WORKBOOK_BYTE_COUNT,
    PINNED_WORKBOOK_SHA256,
    SHEET_CODES,
    SHEET_NAMES,
    ApplicabilityDecision,
    CalculationKind,
    ChildStatus,
    Code,
    FindingSeverity,
    ParentKind,
    PlanMinimumCode,
    Shard,
)


def check_source_coverage(report: ExtractionReport) -> list[Finding]:
    """I-01. Structural census, feature census, other-role cells and the production pin."""
    findings: list[Finding] = []
    row_counts = {item.sheet: item.count for item in report.data_row_counts}
    merge_counts = {item.sheet: item.count for item in report.merged_range_counts}
    if tuple(report.sheet_names) != SHEET_NAMES or row_counts != EXPECTED_DATA_ROW_COUNTS:
        findings.append(
            make_finding(Code.GL02_SOURCE_COVERAGE.value, scope="workbook", ids=("rows",))
        )
    if merge_counts != EXPECTED_MERGED_RANGE_COUNTS:
        findings.append(
            make_finding(Code.GL02_SOURCE_COVERAGE.value, scope="workbook", ids=("merges",))
        )
    census = report.feature_census
    if any(
        (
            census.comments,
            census.hyperlinks,
            census.hidden_rows,
            census.hidden_columns,
            census.data_validations,
            census.conditional_formats,
            census.images,
            census.defined_names,
            census.formula_cells,
            census.non_string_stored_values,
            census.non_visible_sheets,
        )
    ):
        findings.append(make_finding(Code.GL02_SOURCE_FEATURE_PRESENT.value, scope="workbook"))
    if census.other_role_stored_cells:
        findings.append(
            make_finding(
                Code.GL02_SOURCE_OTHER_CELL.value,
                scope="workbook",
                count=census.other_role_stored_cells,
            )
        )
    if (
        report.workbook.sha256 != PINNED_WORKBOOK_SHA256
        or report.workbook.byte_count != PINNED_WORKBOOK_BYTE_COUNT
    ):
        findings.append(
            make_finding(Code.GL02_SOURCE_PIN_MISMATCH.value, scope="workbook", count=1)
        )
    return findings


def check_importer_parity(report: ExtractionReport) -> list[Finding]:
    """I-02. Parity failures are an error. Edge whitespace stays on the report."""
    if not report.parity_failure_ids:
        return []
    return [
        make_finding(
            Code.GL02_IMPORTER_PARITY_FAILED.value,
            scope="workbook",
            ids=report.parity_failure_ids,
        )
    ]


def check_parent_coverage(register: Register) -> list[Finding]:
    """I-03. 79 financial and 21 commercial parents, unique ids, commercial order preserved."""
    financial = [
        parent for parent in register.parents if parent.parent_kind is ParentKind.FINANCIAL
    ]
    commercial = [
        parent for parent in register.parents if parent.parent_kind is ParentKind.COMMERCIAL
    ]
    findings: list[Finding] = []
    ids = [parent.parent_id for parent in register.parents]
    if len(financial) != 79 or len(commercial) != 21 or len(set(ids)) != len(ids):
        findings.append(
            make_finding(Code.GL02_PARENT_COVERAGE.value, scope="parents", count=len(ids))
        )
    template_ids = tuple(
        question.methodology_question_id
        for question in import_cdd_template(_packaged_template()).current_version.questions
    )
    map_ids = tuple(
        entry.methodology_question_id
        for entry in load_commercial_dd_question_category_map().questions
    )
    if tuple(parent.parent_id for parent in commercial) != template_ids or template_ids != map_ids:
        findings.append(
            make_finding(Code.GL02_PARENT_COVERAGE.value, scope="commercial", ids=("order",))
        )
    for parent in financial:
        if parent.sheet is None or parent.source_row is None:
            findings.append(
                make_finding(
                    Code.GL02_PARENT_COVERAGE.value, scope="financial", ids=(parent.parent_id,)
                )
            )
            continue
        expected = f"FDD-{SHEET_CODES[parent.sheet]}-{parent.source_row:03d}"
        if parent.public_coordinate != expected:
            findings.append(
                make_finding(
                    Code.GL02_PARENT_COVERAGE.value, scope="financial", ids=(parent.parent_id,)
                )
            )
    return findings


def check_raw_text_integrity(register: Register) -> list[Finding]:
    """I-05. Hashes and normalized question text recompute from the source cell."""
    findings: list[Finding] = []
    cells = {cell.cell_id: cell for cell in register.source_cells}
    for cell in register.source_cells:
        if cell.raw_text is None:
            if cell.raw_sha256 is not None:
                findings.append(
                    make_finding(Code.GL02_RAW_TEXT_INTEGRITY.value, ids=(cell.cell_id,))
                )
            continue
        if cell.raw_sha256 != sha256_text(cell.raw_text):
            findings.append(make_finding(Code.GL02_RAW_TEXT_INTEGRITY.value, ids=(cell.cell_id,)))
    for parent in register.parents:
        if parent.question_raw_sha256 != sha256_text(parent.question_raw):
            findings.append(
                make_finding(Code.GL02_RAW_TEXT_INTEGRITY.value, ids=(parent.parent_id,))
            )
        normalized = normalize_text(parent.question_raw)
        if (
            parent.question_normalized != normalized
            or parent.question_normalized_sha256 != sha256_text(normalized)
        ):
            findings.append(
                make_finding(Code.GL02_RAW_TEXT_INTEGRITY.value, ids=(parent.parent_id,))
            )
        findings.extend(_raw_matches_cell(parent, cells))
    return findings


def check_determinism(
    register: Register, workbook_path: Path | None, policy_path: Path | None
) -> list[Finding]:
    """I-14. A supplied workbook is re-extracted into a temporary directory and compared.

    When the stored extraction used a policy file and that file was not supplied,
    the caller records ``SKIPPED_POLICY_ABSENT`` instead of comparing.
    """
    if workbook_path is None:
        return []
    if policy_path is None and register.extraction_report.policy is not None:
        return []
    return _stale_findings(register, workbook_path, policy_path)


def check_active_children(register: Register, limit: frozenset[str] | None) -> list[Finding]:
    """I-04. Every parent in scope has an active child."""
    findings: list[Finding] = []
    active = {
        child.parent_id for _shard, child in register.children if child.status is ChildStatus.ACTIVE
    }
    for parent in register.parents:
        if not _in_limit(parent.parent_id, limit):
            continue
        if parent.parent_id not in active:
            findings.append(
                make_finding(
                    Code.GL02_PARENT_WITHOUT_CHILD.value,
                    scope=parent.public_coordinate,
                    ids=(parent.parent_id,),
                )
            )
    return findings


def check_accounting(register: Register, limit: frozenset[str] | None) -> list[Finding]:
    """I-06, I-06b and the marker half of I-07."""
    findings: list[Finding] = []
    children = tuple(child for _shard, child in register.children)
    dispositions = tuple(item for _shard, item in register.dispositions)
    for parent in register.parents:
        if not _in_limit(parent.parent_id, limit):
            continue
        for field in account_parent(parent, children, dispositions):
            findings.extend(field.findings)
    return findings


def check_shared_instructions(
    register: Register, limit: frozenset[str] | None, shards: frozenset[Shard] | None
) -> list[Finding]:
    """I-07 shared-instruction and cross-row propagation."""
    findings: list[Finding] = []
    for instruction in register.merged_instructions:
        for parent_id in instruction.covered_parent_ids:
            if not _in_limit(parent_id, limit):
                if limit is not None or shards is not None:
                    findings.append(
                        make_finding(
                            Code.PENDING_CROSS_SHARD.value,
                            severity=FindingSeverity.INFO,
                            scope=instruction.instruction_id,
                            ids=(parent_id,),
                        )
                    )
                continue
            linked = any(
                instruction.instruction_id in child.shared_instruction_ids
                for child in _active(register, parent_id)
            )
            if not linked:
                findings.append(
                    make_finding(
                        Code.GL02_SHARED_INSTRUCTION_UNAPPLIED.value,
                        scope=instruction.instruction_id,
                        ids=(parent_id,),
                    )
                )
    findings.extend(_cross_row_findings(register, limit, shards))
    return findings


def check_field_completeness(register: Register, limit: frozenset[str] | None) -> list[Finding]:
    """I-08. Resolved fields, derivation rules, quotes, specialists, categories and reach."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for _shard, child in register.children:
        if child.child_id in seen:
            findings.append(make_finding(Code.GL02_CHILD_ID_DUPLICATE.value, ids=(child.child_id,)))
        seen.add(child.child_id)
        parent = register.parent(child.parent_id)
        if parent is None or not _in_limit(parent.parent_id, limit):
            continue
        findings.extend(_one_child(register, parent, child))
    findings.extend(
        joint_reason_findings(
            tuple(register.parents),
            tuple(child for _shard, child in register.children),
            limit=limit,
        )
    )
    return findings


def check_plan_minimum(register: Register, shards: frozenset[Shard] | None) -> list[Finding]:
    """I-09. Every plan-minimum code is carried, or pending outside the shard."""
    carried: set[str] = set()
    findings: list[Finding] = []
    for _shard, child in register.children:
        if child.status is not ChildStatus.ACTIVE:
            continue
        for code in child.plan_minimum_codes:
            if code not in {item.value for item in PlanMinimumCode}:
                findings.append(
                    make_finding(Code.GL02_PLAN_MINIMUM_UNKNOWN.value, ids=(child.child_id, code))
                )
                continue
            carried.add(code)
    for code in PlanMinimumCode:
        owner = _plan_shard(code.value)
        if code.value in carried:
            continue
        if shards is not None and owner not in shards:
            findings.append(
                make_finding(
                    Code.PENDING_CROSS_SHARD.value,
                    severity=FindingSeverity.INFO,
                    scope=owner.value,
                    ids=(code.value,),
                )
            )
            continue
        findings.append(
            make_finding(Code.GL02_PLAN_MINIMUM_MISSING.value, scope=owner.value, ids=(code.value,))
        )
    return findings


def check_policy_coverage(register: Register, policy_present: bool) -> list[Finding]:
    """I-10. Every anchor and source binding is carried by an active child of the cited parent."""
    if not policy_present:
        return []
    findings: list[Finding] = []
    for view in register.policy_views:
        children = _active(register, view.parent_id)
        for anchor in view.anchors:
            if not any(
                rule.kind is CalculationKind.POLICY_V2_ANCHOR
                and rule.formula_ordinal == anchor.formula_ordinal
                for child in children
                for rule in child.calculation_rules
            ):
                findings.append(
                    make_finding(
                        Code.GL02_POLICY_UNCARRIED.value,
                        scope="anchor",
                        ids=(view.parent_id, str(anchor.formula_ordinal)),
                    )
                )
        for binding in view.bindings:
            if not any(
                rule.kind is CalculationKind.POLICY_V2_BINDING
                and rule.formula_ordinal == binding.formula_ordinal
                and rule.binding_ordinal == binding.binding_ordinal
                for child in children
                for rule in child.calculation_rules
            ):
                findings.append(
                    make_finding(
                        Code.GL02_POLICY_UNCARRIED.value,
                        scope="binding",
                        ids=(
                            view.parent_id,
                            str(binding.formula_ordinal),
                            str(binding.binding_ordinal),
                        ),
                    )
                )
    return findings


def check_named_clarifications(
    register: Register, shards: frozenset[Shard] | None
) -> list[Finding]:
    """I-11. Each named ambiguity topic has a record."""
    present = {
        record.topic_code
        for shard, record in register.ambiguities
        if shards is None or shard in shards or shard is Shard.XS
    }
    if shards is not None and Shard.XS not in shards:
        present = {record.topic_code for shard, record in register.ambiguities if shard in shards}
        missing = [topic.value for topic in NAMED_AMBIGUITY_TOPICS if topic not in present]
        if missing:
            return [
                make_finding(
                    Code.PENDING_CROSS_SHARD.value,
                    severity=FindingSeverity.INFO,
                    scope="ambiguity",
                    ids=tuple(missing),
                )
            ]
        return []
    missing = [topic.value for topic in NAMED_AMBIGUITY_TOPICS if topic not in present]
    if not missing:
        return []
    return [
        make_finding(Code.GL02_AMBIGUITY_TOPIC_MISSING.value, scope="ambiguity", ids=tuple(missing))
    ]


def check_scope_retention(register: Register, limit: frozenset[str] | None) -> list[Finding]:
    """I-12. Liabilities rows 17 to 24 keep a child that still applies."""
    findings: list[Finding] = []
    for parent in register.parents:
        if parent.sheet != "Liabilities" or parent.source_row is None:
            continue
        if not LIAB_SCOPE_FIRST_ROW <= parent.source_row <= LIAB_SCOPE_LAST_ROW:
            continue
        if not _in_limit(parent.parent_id, limit):
            continue
        kept = False
        for child in _active(register, parent.parent_id):
            if child.applicability.decision is not ApplicabilityDecision.NOT_APPLICABLE_BY_DEFAULT:
                kept = True
        if not kept:
            findings.append(
                make_finding(
                    Code.GL02_SCOPE_NOT_RETAINED.value,
                    scope=parent.public_coordinate,
                    ids=(parent.parent_id,),
                )
            )
    return findings


def check_commercial_alignment(register: Register, limit: frozenset[str] | None) -> list[Finding]:
    """I-13. Destinations, categories, master-plan columns and role aliases."""
    findings: list[Finding] = []
    roles: set[str] = set()
    for parent in register.parents:
        if parent.parent_kind is not ParentKind.COMMERCIAL or parent.commercial_context is None:
            continue
        roles.update(parent.commercial_context.template_role)
        if not _in_limit(parent.parent_id, limit):
            continue
        findings.extend(_commercial_parent(register, parent))
    if limit is None or any(
        parent_shard(register.parents_by_id[item]) is Shard.CDD
        for item in limit
        if item in register.parents_by_id
    ):
        aliased = {
            alias.template_role
            for _shard, alias in register.role_aliases
            if alias.proposed_registered_types
        }
        missing = tuple(sorted(roles - aliased))
        if missing:
            findings.append(
                make_finding(Code.GL02_ROLE_ALIAS_MISSING.value, scope="commercial", ids=missing)
            )
    return findings


def fresh_report(
    register: Register,
    workbook_path: Path | None,
    policy_path: Path | None,
) -> tuple[ExtractionReport | None, list[Finding]]:
    """Re-extract when a workbook is supplied. Never writes into the private root."""
    if workbook_path is None:
        return None, []
    return None, _stale_findings(register, workbook_path, policy_path)


def _stale_findings(
    register: Register, workbook_path: Path, policy_path: Path | None
) -> list[Finding]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="gl02-derived-") as temporary:
        fresh_root = Path(temporary)
        extract_register(
            workbook_path,
            fresh_root,
            policy_path=policy_path,
        )
        for name in DERIVED_NAMES:
            current = (register.root / "derived" / name).read_bytes()
            fresh = (fresh_root / "derived" / name).read_bytes()
            if current != fresh:
                return [make_finding(Code.GL02_DERIVED_STALE.value, scope="derived", ids=(name,))]
    return []
