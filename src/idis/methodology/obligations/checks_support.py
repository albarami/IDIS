"""Private helpers for the GL-02 invariant checks."""

from __future__ import annotations

import re
from importlib.resources import files

from idis.methodology.obligations.findings import make_finding
from idis.methodology.obligations.models_authored import ChildObligation, ResolvedChild
from idis.methodology.obligations.models_records import CellRef, Finding, ParentRecord, SourceCell
from idis.methodology.obligations.quote import resolve_in_text
from idis.methodology.obligations.quote_ids import child_quote_findings, quote_finding_ids
from idis.methodology.obligations.register_io import Register, parent_shard, resolve_child
from idis.methodology.obligations.vocab import (
    COORDINATE_KEYS,
    DERIVATIONS_REQUIRING_QUOTES,
    DERIVATIONS_REQUIRING_RATIONALE,
    ApplicabilityDecision,
    CalculationKind,
    ChildStatus,
    Code,
    Derivation,
    DerivedRuleCode,
    FindingSeverity,
    Independence,
    ParentKind,
    Shard,
    plan_code_shard,
)
from idis.models.document_classification import CddDocumentCategory, FddDocumentCategory

_CHILD_ID = re.compile(r"^GL02-(FDD-(?:PL|CF|LIAB|ASSET)-\d{3}|CDD-[A-Z0-9_]+)-C\d{3}$")
_FDD_CATEGORIES = frozenset(
    category.value
    for category in FddDocumentCategory
    if category.value not in {"unsupported", "unknown"}
)
_CDD_CATEGORIES = frozenset(
    category.value
    for category in CddDocumentCategory
    if category.value not in {"unsupported", "unknown"}
)
_FORBIDDEN_CATEGORIES = frozenset({"unsupported", "unknown"})
_INHERITED_FIELDS = (
    "coordinates",
    "producer",
    "consumer",
    "responsible_specialist",
    "analyst_review",
    "output_destination",
    "capability",
    "fund_feedback",
)


def _one_child(register: Register, parent: ParentRecord, child: ChildObligation) -> list[Finding]:
    findings: list[Finding] = []
    expected = f"GL02-{parent.public_coordinate}-C"
    if _CHILD_ID.fullmatch(child.child_id) is None or not child.child_id.startswith(expected):
        findings.append(make_finding(Code.GL02_CHILD_ID_INVALID.value, ids=(child.child_id,)))
    if child.status is ChildStatus.RETIRED and not (
        child.retired_reason and child.retired_reason.strip()
    ):
        findings.append(make_finding(Code.GL02_RETIRED_REASON.value, ids=(child.child_id,)))
    if child.status is ChildStatus.ACTIVE and child.retired_reason is not None:
        findings.append(make_finding(Code.GL02_RETIRED_REASON.value, ids=(child.child_id,)))
    if child.derivation in DERIVATIONS_REQUIRING_RATIONALE and not _text(
        child.derivation_rationale
    ):
        findings.append(make_finding(Code.GL02_DERIVATION_RATIONALE.value, ids=(child.child_id,)))
    if child.derivation in DERIVATIONS_REQUIRING_QUOTES and not child.source_quotes:
        findings.append(make_finding(Code.GL02_QUOTE_REQUIRED.value, ids=(child.child_id,)))
    if (
        child.derivation is Derivation.CROSS_REFERENCE_APPLIED
        and not child.cross_row_instruction_ids
    ):
        findings.append(
            make_finding(Code.GL02_QUOTE_REQUIRED.value, ids=(child.child_id, "cross-row"))
        )
    if (
        not child.required_facts
        and not child.calculation_rules
        and not _text(child.judgment_sought)
    ):
        findings.append(make_finding(Code.GL02_FACT_RULE.value, ids=(child.child_id,)))
    if (
        not child.acceptable_evidence
        or not child.completeness_conditions
        or not _text(child.obligation_statement)
    ):
        findings.append(
            make_finding(Code.GL02_FIELD_UNRESOLVED.value, ids=(child.child_id, "required"))
        )
    if not _text(child.acceptance_case):
        findings.append(
            make_finding(Code.GL02_FIELD_UNRESOLVED.value, ids=(child.child_id, "acceptance_case"))
        )
    profile = next(
        (item for _shard, item in register.profiles if item.parent_id == parent.parent_id), None
    )
    resolved = resolve_child(child, profile)
    findings.extend(_unresolved(resolved))
    findings.extend(_quotes(register, parent, child))
    findings.extend(_rules(register, child))
    findings.extend(_categories(parent, child))
    for instruction_id in child.shared_instruction_ids:
        known = {item.instruction_id for item in register.merged_instructions}
        if instruction_id not in known:
            findings.append(
                make_finding(
                    Code.GL02_SHARED_INSTRUCTION_UNAPPLIED.value,
                    ids=(child.child_id, instruction_id),
                )
            )
    return findings


def _unresolved(resolved: ResolvedChild) -> list[Finding]:
    missing = [name for name in _INHERITED_FIELDS if getattr(resolved, name) is None]
    if (
        resolved.responsible_specialist is not None
        and not resolved.responsible_specialist.registered_types
    ):
        missing.append("responsible_specialist")
    if not missing:
        return []
    return [make_finding(Code.GL02_FIELD_UNRESOLVED.value, ids=(resolved.child_id, *missing))]


def _quotes(register: Register, _parent: ParentRecord, child: ChildObligation) -> list[Finding]:
    findings = child_quote_findings(register, child)
    if child.applicability.decision is not ApplicabilityDecision.ALWAYS and not _text(
        child.applicability.condition
    ):
        findings.append(
            make_finding(Code.GL02_FIELD_UNRESOLVED.value, ids=(child.child_id, "applicability"))
        )
    for fact in child.required_facts:
        if any(need not in COORDINATE_KEYS for need in fact.needs):
            findings.append(
                make_finding(Code.GL02_FIELD_UNRESOLVED.value, ids=(child.child_id, fact.fact_id))
            )
    return findings


def _rules(register: Register, child: ChildObligation) -> list[Finding]:
    findings: list[Finding] = []
    view = next((item for item in register.policy_views if item.parent_id == child.parent_id), None)
    anchors = set() if view is None else {item.formula_ordinal for item in view.anchors}
    bindings = (
        set()
        if view is None
        else {(item.formula_ordinal, item.binding_ordinal) for item in view.bindings}
    )
    for rule in child.calculation_rules:
        if rule.kind is CalculationKind.DERIVED_GL08:
            if rule.rule_code not in {item.value for item in DerivedRuleCode}:
                findings.append(
                    make_finding(Code.GL02_DERIVED_RULE_UNKNOWN.value, ids=(child.child_id,))
                )
        elif rule.kind is CalculationKind.POLICY_V2_ANCHOR:
            if rule.formula_ordinal not in anchors:
                findings.append(
                    make_finding(Code.GL02_POLICY_RULE_UNKNOWN.value, ids=(child.child_id,))
                )
        elif (
            rule.kind is CalculationKind.POLICY_V2_BINDING
            and (rule.formula_ordinal, rule.binding_ordinal) not in bindings
        ):
            findings.append(
                make_finding(Code.GL02_POLICY_RULE_UNKNOWN.value, ids=(child.child_id,))
            )
    return findings


def _categories(parent: ParentRecord, child: ChildObligation) -> list[Finding]:
    allowed = _FDD_CATEGORIES if parent.parent_kind is ParentKind.FINANCIAL else _CDD_CATEGORIES
    findings: list[Finding] = []
    for evidence in child.acceptable_evidence:
        for category in evidence.document_categories:
            if category in _FORBIDDEN_CATEGORIES or category not in allowed:
                findings.append(
                    make_finding(Code.GL02_CATEGORY_FORBIDDEN.value, ids=(child.child_id, category))
                )
    return findings


def _commercial_parent(register: Register, parent: ParentRecord) -> list[Finding]:
    assert parent.commercial_context is not None
    findings: list[Finding] = []
    children = [_resolve(register, child) for child in _active(register, parent.parent_id)]
    mapping = parent.commercial_context.report_mapping
    governed = set(parent.commercial_context.governed_categories)
    for child in children:
        destination = child.output_destination
        if destination is None:
            continue
        same = (
            destination.report_section == mapping.report_section
            and destination.report_subsection == mapping.report_subsection
        )
        if not same and not _text(destination.deviation_reason):
            findings.append(
                make_finding(Code.GL02_COMMERCIAL_DESTINATION.value, ids=(child.child_id,))
            )
        for evidence in child.acceptable_evidence:
            if evidence.independence is Independence.EXTERNAL_INDEPENDENT:
                continue
            if any(category not in governed for category in evidence.document_categories):
                findings.append(
                    make_finding(Code.GL02_COMMERCIAL_CATEGORY.value, ids=(child.child_id,))
                )
    if children and not any(
        child.calculation_rules or _text(child.judgment_sought) for child in children
    ):
        findings.append(
            make_finding(Code.GL02_COMMERCIAL_COLUMN.value, ids=(parent.parent_id, "judgment"))
        )
    if children and not any(
        child.responsible_specialist and child.output_destination for child in children
    ):
        findings.append(
            make_finding(Code.GL02_COMMERCIAL_COLUMN.value, ids=(parent.parent_id, "destination"))
        )
    if children and not any(
        _text(child.acceptance_case) and child.acceptable_evidence for child in children
    ):
        findings.append(
            make_finding(Code.GL02_COMMERCIAL_COLUMN.value, ids=(parent.parent_id, "acceptance"))
        )
    return findings


def _cross_row_findings(
    register: Register, limit: frozenset[str] | None, shards: frozenset[Shard] | None
) -> list[Finding]:
    findings: list[Finding] = []
    for file_shard, instruction in register.cross_rows:
        if not instruction.target_parent_ids:
            findings.append(
                make_finding(
                    Code.GL02_CROSS_ROW_TARGET_EMPTY.value, ids=(instruction.instruction_id,)
                )
            )
            continue
        for target_id in instruction.target_parent_ids:
            parent = register.parent(target_id)
            if parent is None:
                findings.append(
                    make_finding(
                        Code.GL02_CROSS_ROW_UNAPPLIED.value,
                        ids=(instruction.instruction_id, target_id),
                    )
                )
                continue
            if parent_shard(parent) is not file_shard:
                findings.append(
                    make_finding(
                        Code.GL02_CROSS_ROW_SHARD.value, ids=(instruction.instruction_id, target_id)
                    )
                )
                continue
            source = register.parent(instruction.source_parent_id)
            out_of_scope = shards is not None and (
                parent_shard(parent) not in shards
                or source is None
                or parent_shard(source) not in shards
            )
            if not _in_limit(target_id, limit) or out_of_scope:
                findings.append(
                    make_finding(
                        Code.PENDING_CROSS_SHARD.value,
                        severity=FindingSeverity.INFO,
                        ids=(instruction.instruction_id, target_id),
                    )
                )
                continue
            applied = _cross_row_applied(
                register, instruction.instruction_id, instruction.source_parent_id, target_id
            )
            if not applied:
                findings.append(
                    make_finding(
                        Code.GL02_CROSS_ROW_UNAPPLIED.value,
                        ids=(instruction.instruction_id, target_id),
                    )
                )
        source_parent = register.parent(instruction.source_parent_id)
        if source_parent is None:
            findings.append(
                make_finding(Code.GL02_CROSS_ROW_UNAPPLIED.value, ids=(instruction.instruction_id,))
            )
            continue
        text = {
            "question": source_parent.question_raw,
            "line_item": source_parent.line_item_raw,
            "term": source_parent.term_raw,
            "nature": source_parent.nature_raw,
        }.get(instruction.source_field)
        quote = instruction.source_quote
        if not isinstance(text, str):
            findings.append(
                make_finding(
                    Code.GL02_QUOTE_NOT_FOUND.value,
                    ids=quote_finding_ids(
                        instruction.instruction_id,
                        field=instruction.source_field,
                        text=None,
                        needle=quote.text,
                        occurrence=quote.occurrence,
                    ),
                )
            )
            continue
        resolved = resolve_in_text(text, quote.text, quote.occurrence)
        if isinstance(resolved, str):
            findings.append(
                make_finding(
                    resolved,
                    ids=quote_finding_ids(
                        instruction.instruction_id,
                        field=instruction.source_field,
                        text=text,
                        needle=quote.text,
                        occurrence=quote.occurrence,
                    ),
                )
            )
    return findings


def _cross_row_applied(
    register: Register, instruction_id: str, source_parent_id: str, target_id: str
) -> bool:
    for child in _active(register, target_id):
        if child.derivation is not Derivation.CROSS_REFERENCE_APPLIED:
            continue
        if instruction_id not in child.cross_row_instruction_ids:
            continue
        if any(
            (quote.parent_id or child.parent_id) == source_parent_id
            for quote in child.source_quotes
        ):
            return True
    return False


def _ambiguity_link(register: Register, parent_id: str, topic: str) -> bool:
    for _shard, record in register.ambiguities:
        if record.topic_code.value != topic:
            continue
        if parent_id in record.affected_parent_ids:
            return True
        for _child_shard, child in register.children:
            if child.parent_id == parent_id and record.ambiguity_id in child.ambiguity_ids:
                return True
    return False


def _closure(register: Register, primaries: list[str], closure: str) -> frozenset[str]:
    closed = set(primaries)
    if closure != "policy_and_cross_reference":
        return frozenset(closed)
    by_ordinal_anchors: dict[int, set[str]] = {}
    by_ordinal_bindings: dict[int, set[str]] = {}
    for view in register.policy_views:
        for anchor in view.anchors:
            by_ordinal_anchors.setdefault(anchor.formula_ordinal, set()).add(view.parent_id)
        for binding in view.bindings:
            by_ordinal_bindings.setdefault(binding.formula_ordinal, set()).add(view.parent_id)
    changed = True
    while changed:
        changed = False
        snapshot = set(closed)
        for _shard, instruction in register.cross_rows:
            involved = {instruction.source_parent_id, *instruction.target_parent_ids}
            if involved & snapshot:
                closed.update(involved)
        for ordinal, anchors in by_ordinal_anchors.items():
            bindings = by_ordinal_bindings.get(ordinal, set())
            if (anchors | bindings) & snapshot:
                closed.update(anchors)
                closed.update(bindings)
        for _shard, child in register.children:
            if child.parent_id not in snapshot:
                continue
            for quote in child.source_quotes:
                if quote.parent_id is not None:
                    closed.add(quote.parent_id)
        changed = closed != snapshot
    return frozenset(closed)


def _resolve(register: Register, child: ChildObligation) -> ResolvedChild:
    profile = next(
        (item for _shard, item in register.profiles if item.parent_id == child.parent_id), None
    )
    return resolve_child(child, profile)


def _active(register: Register, parent_id: str) -> list[ChildObligation]:
    return [
        child
        for _shard, child in register.children
        if child.parent_id == parent_id and child.status is ChildStatus.ACTIVE
    ]


def _raw_matches_cell(parent: ParentRecord, cells: dict[str, SourceCell]) -> list[Finding]:

    findings: list[Finding] = []
    pairs = (
        ("term", parent.term_raw, parent.term_cell),
        ("nature", parent.nature_raw, parent.nature_cell),
        ("line_item", parent.line_item_raw, parent.line_item_cell),
        ("question", parent.question_raw, parent.question_cell),
    )
    for _name, raw, ref in pairs:
        if not isinstance(ref, CellRef) or parent.sheet is None:
            continue
        holder = f"{parent.sheet}!{ref.merge_anchor}" if ref.merge_anchor else ref.cell_id
        cell = cells.get(holder)
        if cell is None:
            if raw is not None:
                findings.append(
                    make_finding(Code.GL02_RAW_TEXT_INTEGRITY.value, ids=(parent.parent_id,))
                )
            continue
        if cell.raw_text != raw:
            findings.append(
                make_finding(Code.GL02_RAW_TEXT_INTEGRITY.value, ids=(parent.parent_id,))
            )
    return findings


def _resolve_ref(register: Register, ref: str) -> str | None:
    parent = register.parent(ref)
    if parent is not None:
        return parent.parent_id
    located = register.parents_by_coordinate.get(ref)
    if located is None:
        return None
    return located.parent_id


def _plan_shard(code: str) -> Shard:

    return plan_code_shard(code)


def _packaged_template() -> bytes:

    return files("idis.methodology").joinpath("templates/commercial_dd_v1.json").read_bytes()


def _in_limit(parent_id: str, limit: frozenset[str] | None) -> bool:
    return limit is None or parent_id in limit


def _text(value: str | None) -> bool:
    return value is not None and value.strip() != ""
