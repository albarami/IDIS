"""Honest Task 7 checkpoint boundary derived from the frozen contract artifact.

Task 7 installs and validates the current persistence spine, but ten producer
rules depend on relations that are deliberately future-native.  Those rules,
the 27-cut restart proof, and final three-way equality therefore belong to
Task 11 rather than the Task 7 completion claim.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, cast

TASK11_REQUIRED_RESTART_CUTS: Final = 27
TASK11_REQUIRED_EDGE_RULE_COUNT: Final = 48
TASK11_REQUIRES_THREE_WAY_EQUALITY: Final = True
TASK7_CLAIMS_COMPLETE_RESTART_EQUALITY: Final = False

TASK11_PRODUCER_BLOCKED_RULE_IDS: Final = (
    "E028_PROVIDER_OUTCOME_COMMON",
    "E029_PROVIDER_FACT_SEMANTIC",
    "E030_PROVIDER_FACT_DELIVERABLES",
    "E031_GRAPH_INTERNAL_DOWNSTREAM",
    "E032_RAG_INTERNAL_DOWNSTREAM",
    "E033_GRAPH_EXTERNAL_DOWNSTREAM",
    "E034_RAG_EXTERNAL_DOWNSTREAM",
    "E035_CONFLICT_INTERNAL_DOWNSTREAM",
    "E036_CONFLICT_EXTERNAL_DOWNSTREAM",
    "E048_PROVIDER_FACT_EXTERNAL_CONTEXT",
)


class Task7CheckpointBoundaryError(ValueError):
    """Raised when a candidate artifact cannot prove the Task 11 boundary."""

    code = "TASK7_CHECKPOINT_BOUNDARY_INVALID"


def _rows(
    artifact: Mapping[str, Any],
    section_name: str,
) -> tuple[Mapping[str, Any], ...]:
    section = artifact.get(section_name)
    if not isinstance(section, Mapping):
        raise Task7CheckpointBoundaryError(f"{section_name}: section missing")
    rows = section.get("rows")
    if not isinstance(rows, list) or not rows:
        raise Task7CheckpointBoundaryError(f"{section_name}: rows missing")
    if not all(isinstance(row, Mapping) for row in rows):
        raise Task7CheckpointBoundaryError(f"{section_name}: row is not an object")
    return tuple(cast(Mapping[str, Any], row) for row in rows)


def _relation_names(
    rows: tuple[Mapping[str, Any], ...],
    field_name: str,
    *,
    expected_count: int,
) -> tuple[str, ...]:
    values = tuple(row.get(field_name) for row in rows)
    if len(values) != expected_count or not all(
        isinstance(value, str) and value for value in values
    ):
        raise Task7CheckpointBoundaryError(
            f"{field_name}: expected {expected_count} nonblank relation names"
        )
    return cast(tuple[str, ...], values)


def _future_relations(artifact: Mapping[str, Any]) -> frozenset[str]:
    native = _relation_names(
        _rows(artifact, "future_native_authority_registry"),
        "planned_table",
        expected_count=18,
    )
    references = _relation_names(
        _rows(artifact, "future_reference_authority_registry"),
        "table_name",
        expected_count=15,
    )
    children = _relation_names(
        _rows(artifact, "future_child_authority_registry"),
        "table_name",
        expected_count=5,
    )
    relations = frozenset((*native, *references, *children))
    if len(relations) != 38:
        raise Task7CheckpointBoundaryError(
            "future authority relations must be disjoint and total 38"
        )
    return relations


def _selector_relations(instance: object) -> tuple[str, ...]:
    if not isinstance(instance, Mapping):
        raise Task7CheckpointBoundaryError("producer selector instance is not an object")
    selector_ast = instance.get("selector_ast")
    if (
        not isinstance(selector_ast, list)
        or len(selector_ast) != 3
        or selector_ast[0] != "concrete-producer-selector-v2"
    ):
        raise Task7CheckpointBoundaryError("producer selector AST is not v2")
    spec = selector_ast[1]
    if not isinstance(spec, list) or len(spec) != 9 or spec[0] != "producer-selector-spec-v2":
        raise Task7CheckpointBoundaryError("producer selector spec is not v2")
    relations = spec[4]
    if (
        not isinstance(relations, list)
        or not relations
        or not all(isinstance(relation, str) and relation for relation in relations)
    ):
        raise Task7CheckpointBoundaryError("producer selector relations are invalid")
    return tuple(cast(list[str], relations))


def derive_task11_producer_blocked_rule_ids(
    artifact: Mapping[str, Any],
) -> tuple[str, ...]:
    """Derive producer rules blocked on the artifact's 38 future relations."""

    future_relations = _future_relations(artifact)
    selector_rows = _rows(artifact, "producer_selector_registry")
    if len(selector_rows) != TASK11_REQUIRED_EDGE_RULE_COUNT:
        raise Task7CheckpointBoundaryError("producer selector registry must contain 48 rules")

    blocked_rule_ids: list[str] = []
    all_rule_ids: list[str] = []
    for row in selector_rows:
        rule_id = row.get("rule_id")
        instances = row.get("instances")
        if not isinstance(rule_id, str) or not rule_id:
            raise Task7CheckpointBoundaryError("producer selector rule_id is invalid")
        if not isinstance(instances, list) or not instances:
            raise Task7CheckpointBoundaryError(f"{rule_id}: instances missing")
        relations = {
            relation for instance in instances for relation in _selector_relations(instance)
        }
        all_rule_ids.append(rule_id)
        if relations & future_relations:
            blocked_rule_ids.append(rule_id)

    edge_rule_ids = tuple(row.get("rule_id") for row in _rows(artifact, "edge_rule_registry"))
    if (
        len(edge_rule_ids) != TASK11_REQUIRED_EDGE_RULE_COUNT
        or tuple(all_rule_ids) != edge_rule_ids
    ):
        raise Task7CheckpointBoundaryError(
            "edge and producer-selector registries must align over all 48 rules"
        )
    return tuple(blocked_rule_ids)
