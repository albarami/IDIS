"""Sanad Integrity Validator - enforces evidence chain integrity.

HARD GATE: Enforces:
1. Claim has sanad
2. Sanad has primary evidence
3. Transmission nodes are well-formed
4. Grade/verdict/action separation is valid
5. Defect structure is valid
6. Chain linkage validity (no cycles, orphans, or multiple roots)
7. UUID format validation for identifiers
"""

from __future__ import annotations

import re
from typing import Any

from idis.validators.schema_validator import ValidationError, ValidationResult

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Valid enumerations from spec
VALID_GRADES = {"A", "B", "C", "D"}
VALID_VERDICTS = {"VERIFIED", "INFLATED", "CONTRADICTED", "UNVERIFIED", "SUBJECTIVE"}
VALID_ACTIONS = {
    "NONE",
    "REQUEST_DATA",
    "FLAG",
    "RED_FLAG",
    "HUMAN_GATE",
    "PARTNER_OVERRIDE_REQUIRED",
}
VALID_CORROBORATION_STATUS = {"NONE", "AHAD_1", "AHAD_2", "MUTAWATIR"}
VALID_NODE_TYPES = {
    "INGEST",
    "EXTRACT",
    "NORMALIZE",
    "RECONCILE",
    "CALCULATE",
    "INFER",
    "HUMAN_VERIFY",
    "EXPORT",
}
VALID_ACTOR_TYPES = {"AGENT", "HUMAN", "SYSTEM"}
VALID_DEFECT_TYPES = {
    "BROKEN_CHAIN",
    "MISSING_LINK",
    "UNKNOWN_SOURCE",
    "CONCEALMENT",
    "INCONSISTENCY",
    "ANOMALY_VS_STRONGER_SOURCES",
    "CHRONO_IMPOSSIBLE",
    "CHAIN_GRAFTING",
    "CIRCULARITY",
    "STALENESS",
    "UNIT_MISMATCH",
    "TIME_WINDOW_MISMATCH",
    "SCOPE_DRIFT",
    "IMPLAUSIBILITY",
}
VALID_DEFECT_SEVERITIES = {"FATAL", "MAJOR", "MINOR"}
VALID_CURE_PROTOCOLS = {
    "REQUEST_SOURCE",
    "REQUIRE_REAUDIT",
    "HUMAN_ARBITRATION",
    "RECONSTRUCT_CHAIN",
    "DISCARD_CLAIM",
}


def _is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID format."""
    if not isinstance(value, str):
        return False
    return bool(UUID_PATTERN.match(value))


class SanadIntegrityValidator:
    """Validates Sanad chains for integrity and completeness.

    Rules:
    1. Sanad must have primary_evidence_id
    2. Transmission chain must be non-empty and well-formed
    3. All grades/verdicts/actions must be valid enums
    4. Defects must have valid type/severity/cure_protocol
    5. Grade must be consistent with defects (FATAL → D)
    6. Chain linkage: no cycles, orphans, or multiple roots
    7. UUID format validation for identifiers
    """

    def __init__(self) -> None:
        """Initialize the validator."""
        pass

    def _validate_uuid_field(
        self, data: dict[str, Any], field: str, path: str
    ) -> list[ValidationError]:
        """Validate that a field contains a valid UUID."""
        errors: list[ValidationError] = []
        value = data.get(field)
        if value is not None and value != "" and not _is_valid_uuid(str(value)):
            errors.append(
                ValidationError(
                    code="INVALID_UUID_FORMAT",
                    message=f"{field} must be a valid UUID format, got: {value}",
                    path=path,
                )
            )
        return errors

    def _validate_transmission_node(
        self, node: dict[str, Any], index: int
    ) -> list[ValidationError]:
        """Validate a single transmission node."""
        errors: list[ValidationError] = []
        base_path = f"$.transmission_chain[{index}]"

        # Required fields
        if not node.get("node_id"):
            errors.append(
                ValidationError(
                    code="MISSING_NODE_ID",
                    message="Transmission node missing node_id",
                    path=f"{base_path}.node_id",
                )
            )

        node_type = node.get("node_type")
        if not node_type:
            errors.append(
                ValidationError(
                    code="MISSING_NODE_TYPE",
                    message="Transmission node missing node_type",
                    path=f"{base_path}.node_type",
                )
            )
        elif node_type not in VALID_NODE_TYPES:
            errors.append(
                ValidationError(
                    code="INVALID_NODE_TYPE",
                    message=f"Invalid node_type: {node_type}. Must be one of: {VALID_NODE_TYPES}",
                    path=f"{base_path}.node_type",
                )
            )

        actor_type = node.get("actor_type")
        if not actor_type:
            errors.append(
                ValidationError(
                    code="MISSING_ACTOR_TYPE",
                    message="Transmission node missing actor_type",
                    path=f"{base_path}.actor_type",
                )
            )
        elif actor_type not in VALID_ACTOR_TYPES:
            errors.append(
                ValidationError(
                    code="INVALID_ACTOR_TYPE",
                    message=(
                        f"Invalid actor_type: {actor_type}. Must be one of: {VALID_ACTOR_TYPES}"
                    ),
                    path=f"{base_path}.actor_type",
                )
            )

        if not node.get("actor_id"):
            errors.append(
                ValidationError(
                    code="MISSING_ACTOR_ID",
                    message="Transmission node missing actor_id",
                    path=f"{base_path}.actor_id",
                )
            )

        if not node.get("timestamp"):
            errors.append(
                ValidationError(
                    code="MISSING_TIMESTAMP",
                    message="Transmission node missing timestamp",
                    path=f"{base_path}.timestamp",
                )
            )

        # UUID format check for node_id
        node_id = node.get("node_id")
        if node_id and not _is_valid_uuid(str(node_id)):
            errors.append(
                ValidationError(
                    code="INVALID_NODE_ID_FORMAT",
                    message=f"node_id must be a valid UUID format, got: {node_id}",
                    path=f"{base_path}.node_id",
                )
            )

        # Confidence range check
        confidence = node.get("confidence")
        if confidence is not None and (
            not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1
        ):
            errors.append(
                ValidationError(
                    code="INVALID_CONFIDENCE",
                    message=f"Confidence must be between 0 and 1, got: {confidence}",
                    path=f"{base_path}.confidence",
                )
            )

        return errors

    def _validate_defect(self, defect: dict[str, Any], index: int) -> list[ValidationError]:
        """Validate a single defect."""
        errors: list[ValidationError] = []
        base_path = f"$.defects[{index}]"

        # Required fields
        if not defect.get("defect_id"):
            errors.append(
                ValidationError(
                    code="MISSING_DEFECT_ID",
                    message="Defect missing defect_id",
                    path=f"{base_path}.defect_id",
                )
            )

        defect_type = defect.get("defect_type")
        if not defect_type:
            errors.append(
                ValidationError(
                    code="MISSING_DEFECT_TYPE",
                    message="Defect missing defect_type",
                    path=f"{base_path}.defect_type",
                )
            )
        elif defect_type not in VALID_DEFECT_TYPES:
            errors.append(
                ValidationError(
                    code="INVALID_DEFECT_TYPE",
                    message=(
                        f"Invalid defect_type: {defect_type}. Must be one of: {VALID_DEFECT_TYPES}"
                    ),
                    path=f"{base_path}.defect_type",
                )
            )

        severity = defect.get("severity")
        if not severity:
            errors.append(
                ValidationError(
                    code="MISSING_SEVERITY",
                    message="Defect missing severity",
                    path=f"{base_path}.severity",
                )
            )
        elif severity not in VALID_DEFECT_SEVERITIES:
            errors.append(
                ValidationError(
                    code="INVALID_SEVERITY",
                    message=(
                        f"Invalid severity: {severity}. Must be one of: {VALID_DEFECT_SEVERITIES}"
                    ),
                    path=f"{base_path}.severity",
                )
            )

        cure_protocol = defect.get("cure_protocol")
        if not cure_protocol:
            errors.append(
                ValidationError(
                    code="MISSING_CURE_PROTOCOL",
                    message="Defect missing cure_protocol",
                    path=f"{base_path}.cure_protocol",
                )
            )
        elif cure_protocol not in VALID_CURE_PROTOCOLS:
            errors.append(
                ValidationError(
                    code="INVALID_CURE_PROTOCOL",
                    message=(
                        f"Invalid cure_protocol: {cure_protocol}. "
                        f"Must be one of: {VALID_CURE_PROTOCOLS}"
                    ),
                    path=f"{base_path}.cure_protocol",
                )
            )

        if not defect.get("description"):
            errors.append(
                ValidationError(
                    code="MISSING_DESCRIPTION",
                    message="Defect missing description",
                    path=f"{base_path}.description",
                )
            )

        status = defect.get("status")
        if status and status not in {"OPEN", "CURED", "WAIVED"}:
            errors.append(
                ValidationError(
                    code="INVALID_DEFECT_STATUS",
                    message=f"Invalid status: {status}. Must be OPEN, CURED, or WAIVED",
                    path=f"{base_path}.status",
                )
            )

        return errors

    def _validate_chain_linkage(
        self, transmission_chain: list[dict[str, Any]]
    ) -> list[ValidationError]:
        """Validate chain linkage: no cycles, orphans, or multiple roots.

        Rules enforced:
        - If nodes include prev_node_id/parent_id, every referenced parent must exist
        - Exactly one root node (no parent)
        - No cycles (detected via DFS)
        - All nodes must be connected (no orphans)
        """
        errors: list[ValidationError] = []

        if not transmission_chain:
            return errors

        node_ids: set[str] = set()
        parent_refs: dict[str, str | None] = {}
        children: dict[str, list[str]] = {}

        for node in transmission_chain:
            node_id = node.get("node_id")
            if not node_id:
                continue

            node_id_str = str(node_id)
            node_ids.add(node_id_str)

            parent_id = node.get("prev_node_id") or node.get("parent_id")
            parent_refs[node_id_str] = str(parent_id) if parent_id else None

            if parent_id:
                parent_str = str(parent_id)
                if parent_str not in children:
                    children[parent_str] = []
                children[parent_str].append(node_id_str)

        root_nodes: list[str] = []
        for node_id, parent_id in parent_refs.items():
            if parent_id is None:
                root_nodes.append(node_id)
            elif parent_id not in node_ids:
                errors.append(
                    ValidationError(
                        code="SANAD_ORPHAN_REFERENCE",
                        message=(f"Node {node_id} references non-existent parent {parent_id}"),
                        path="$.transmission_chain",
                    )
                )

        if len(root_nodes) == 0 and len(node_ids) > 0:
            has_any_parent_ref = any(v is not None for v in parent_refs.values())
            if has_any_parent_ref:
                errors.append(
                    ValidationError(
                        code="SANAD_NO_ROOT",
                        message="Transmission chain has no root node (all nodes have parents)",
                        path="$.transmission_chain",
                    )
                )
        elif len(root_nodes) > 1:
            errors.append(
                ValidationError(
                    code="SANAD_MULTIPLE_ROOTS",
                    message=f"Transmission chain has multiple roots: {root_nodes}",
                    path="$.transmission_chain",
                )
            )

        def detect_cycle(node_id: str, visited: set[str], rec_stack: set[str]) -> bool:
            """DFS cycle detection."""
            visited.add(node_id)
            rec_stack.add(node_id)

            for child_id in children.get(node_id, []):
                if child_id not in visited:
                    if detect_cycle(child_id, visited, rec_stack):
                        return True
                elif child_id in rec_stack:
                    return True

            rec_stack.discard(node_id)
            return False

        visited: set[str] = set()
        for node_id in node_ids:
            if node_id not in visited and detect_cycle(node_id, visited, set()):
                errors.append(
                    ValidationError(
                        code="SANAD_CYCLE_DETECTED",
                        message="Cycle detected in transmission chain",
                        path="$.transmission_chain",
                    )
                )
                break

        if root_nodes and len(root_nodes) == 1:
            reachable: set[str] = set()

            def collect_reachable(node_id: str) -> None:
                if node_id in reachable:
                    return
                reachable.add(node_id)
                for child_id in children.get(node_id, []):
                    collect_reachable(child_id)

            collect_reachable(root_nodes[0])

            orphaned = node_ids - reachable
            if orphaned:
                errors.append(
                    ValidationError(
                        code="SANAD_ORPHAN_NODE",
                        message=f"Orphaned nodes not connected to root: {sorted(orphaned)}",
                        path="$.transmission_chain",
                    )
                )

        return errors

    def validate_sanad(self, data: Any) -> ValidationResult:
        """Validate a Sanad record.

        Args:
            data: Sanad JSON data

        Returns:
            ValidationResult - FAILS CLOSED on integrity violations
        """
        # Fail closed on None or non-dict
        if data is None:
            return ValidationResult.fail_closed("Data is None - cannot validate")

        if not isinstance(data, dict):
            return ValidationResult.fail_closed("Data must be a dictionary")

        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        # Required identifiers
        if not data.get("sanad_id"):
            errors.append(
                ValidationError(
                    code="MISSING_SANAD_ID",
                    message="sanad_id is required",
                    path="$.sanad_id",
                )
            )
        else:
            errors.extend(self._validate_uuid_field(data, "sanad_id", "$.sanad_id"))

        if not data.get("claim_id"):
            errors.append(
                ValidationError(
                    code="MISSING_CLAIM_ID",
                    message="claim_id is required",
                    path="$.claim_id",
                )
            )
        else:
            errors.extend(self._validate_uuid_field(data, "claim_id", "$.claim_id"))

        # PRIMARY EVIDENCE IS REQUIRED
        if not data.get("primary_evidence_id"):
            errors.append(
                ValidationError(
                    code="MISSING_PRIMARY_EVIDENCE",
                    message="primary_evidence_id is required - claim must have primary evidence",
                    path="$.primary_evidence_id",
                )
            )

        # Extraction confidence
        extraction_confidence = data.get("extraction_confidence")
        if extraction_confidence is None:
            errors.append(
                ValidationError(
                    code="MISSING_EXTRACTION_CONFIDENCE",
                    message="extraction_confidence is required",
                    path="$.extraction_confidence",
                )
            )
        elif not isinstance(extraction_confidence, (int, float)):
            errors.append(
                ValidationError(
                    code="INVALID_EXTRACTION_CONFIDENCE",
                    message="extraction_confidence must be a number",
                    path="$.extraction_confidence",
                )
            )
        elif extraction_confidence < 0 or extraction_confidence > 1:
            errors.append(
                ValidationError(
                    code="EXTRACTION_CONFIDENCE_OUT_OF_RANGE",
                    message=f"extraction_confidence must be 0-1, got: {extraction_confidence}",
                    path="$.extraction_confidence",
                )
            )

        # Corroboration status
        corroboration_status = data.get("corroboration_status")
        if not corroboration_status:
            errors.append(
                ValidationError(
                    code="MISSING_CORROBORATION_STATUS",
                    message="corroboration_status is required",
                    path="$.corroboration_status",
                )
            )
        elif corroboration_status not in VALID_CORROBORATION_STATUS:
            errors.append(
                ValidationError(
                    code="INVALID_CORROBORATION_STATUS",
                    message=(
                        f"Invalid corroboration_status: {corroboration_status}. "
                        f"Must be one of: {VALID_CORROBORATION_STATUS}"
                    ),
                    path="$.corroboration_status",
                )
            )

        # Sanad grade
        sanad_grade = data.get("sanad_grade")
        if not sanad_grade:
            errors.append(
                ValidationError(
                    code="MISSING_SANAD_GRADE",
                    message="sanad_grade is required",
                    path="$.sanad_grade",
                )
            )
        elif sanad_grade not in VALID_GRADES:
            errors.append(
                ValidationError(
                    code="INVALID_SANAD_GRADE",
                    message=f"Invalid sanad_grade: {sanad_grade}. Must be A, B, C, or D",
                    path="$.sanad_grade",
                )
            )

        # TRANSMISSION CHAIN REQUIRED AND WELL-FORMED
        transmission_chain = data.get("transmission_chain")
        if transmission_chain is None:
            errors.append(
                ValidationError(
                    code="MISSING_TRANSMISSION_CHAIN",
                    message="transmission_chain is required and must be non-empty",
                    path="$.transmission_chain",
                )
            )
        elif not isinstance(transmission_chain, list):
            errors.append(
                ValidationError(
                    code="INVALID_TRANSMISSION_CHAIN",
                    message="transmission_chain must be an array",
                    path="$.transmission_chain",
                )
            )
        elif len(transmission_chain) == 0:
            errors.append(
                ValidationError(
                    code="EMPTY_TRANSMISSION_CHAIN",
                    message="transmission_chain must have at least one node",
                    path="$.transmission_chain",
                )
            )
        else:
            for i, node in enumerate(transmission_chain):
                if not isinstance(node, dict):
                    errors.append(
                        ValidationError(
                            code="INVALID_NODE",
                            message="Transmission node must be an object",
                            path=f"$.transmission_chain[{i}]",
                        )
                    )
                else:
                    errors.extend(self._validate_transmission_node(node, i))

            errors.extend(self._validate_chain_linkage(transmission_chain))

        # Validate defects if present
        defects = data.get("defects", [])
        has_fatal_defect = False

        if isinstance(defects, list):
            for i, defect in enumerate(defects):
                if not isinstance(defect, dict):
                    errors.append(
                        ValidationError(
                            code="INVALID_DEFECT",
                            message="Defect must be an object",
                            path=f"$.defects[{i}]",
                        )
                    )
                else:
                    errors.extend(self._validate_defect(defect, i))

                    # Check for FATAL defects
                    if defect.get("severity") == "FATAL" and defect.get("status") == "OPEN":
                        has_fatal_defect = True

        # CONSISTENCY CHECK: FATAL defect requires grade D
        if has_fatal_defect and sanad_grade and sanad_grade != "D":
            errors.append(
                ValidationError(
                    code="GRADE_DEFECT_MISMATCH",
                    message=(
                        f"Sanad has FATAL defect but grade is {sanad_grade}. "
                        f"FATAL defects require grade D."
                    ),
                    path="$.sanad_grade",
                )
            )

        if errors:
            return ValidationResult.fail(errors)

        return ValidationResult.success(warnings if warnings else None)

    def validate_claim(self, data: Any) -> ValidationResult:
        """Validate a Claim record for grade/verdict/action consistency.

        Args:
            data: Claim JSON data

        Returns:
            ValidationResult - FAILS CLOSED on integrity violations
        """
        if data is None:
            return ValidationResult.fail_closed("Data is None - cannot validate")

        if not isinstance(data, dict):
            return ValidationResult.fail_closed("Data must be a dictionary")

        errors: list[ValidationError] = []

        # Required identifiers
        if not data.get("claim_id"):
            errors.append(
                ValidationError(
                    code="MISSING_CLAIM_ID",
                    message="claim_id is required",
                    path="$.claim_id",
                )
            )

        # Grade validation
        claim_grade = data.get("claim_grade")
        if not claim_grade:
            errors.append(
                ValidationError(
                    code="MISSING_CLAIM_GRADE",
                    message="claim_grade is required",
                    path="$.claim_grade",
                )
            )
        elif claim_grade not in VALID_GRADES:
            errors.append(
                ValidationError(
                    code="INVALID_CLAIM_GRADE",
                    message=f"Invalid claim_grade: {claim_grade}. Must be A, B, C, or D",
                    path="$.claim_grade",
                )
            )

        # Verdict validation
        claim_verdict = data.get("claim_verdict")
        if not claim_verdict:
            errors.append(
                ValidationError(
                    code="MISSING_CLAIM_VERDICT",
                    message="claim_verdict is required",
                    path="$.claim_verdict",
                )
            )
        elif claim_verdict not in VALID_VERDICTS:
            errors.append(
                ValidationError(
                    code="INVALID_CLAIM_VERDICT",
                    message=(
                        f"Invalid claim_verdict: {claim_verdict}. Must be one of: {VALID_VERDICTS}"
                    ),
                    path="$.claim_verdict",
                )
            )

        # Action validation
        claim_action = data.get("claim_action")
        if not claim_action:
            errors.append(
                ValidationError(
                    code="MISSING_CLAIM_ACTION",
                    message="claim_action is required",
                    path="$.claim_action",
                )
            )
        elif claim_action not in VALID_ACTIONS:
            errors.append(
                ValidationError(
                    code="INVALID_CLAIM_ACTION",
                    message=(
                        f"Invalid claim_action: {claim_action}. Must be one of: {VALID_ACTIONS}"
                    ),
                    path="$.claim_action",
                )
            )

        # CONSISTENCY: Grade D should typically have non-NONE action
        if claim_grade == "D" and claim_action == "NONE":
            errors.append(
                ValidationError(
                    code="GRADE_ACTION_MISMATCH",
                    message="Grade D claims should have a non-NONE action (FLAG, RED_FLAG, etc.)",
                    path="$.claim_action",
                )
            )

        # CONSISTENCY: CONTRADICTED verdict should have action
        if claim_verdict == "CONTRADICTED" and claim_action == "NONE":
            errors.append(
                ValidationError(
                    code="VERDICT_ACTION_MISMATCH",
                    message="CONTRADICTED claims should have a non-NONE action",
                    path="$.claim_action",
                )
            )

        if errors:
            return ValidationResult.fail(errors)

        return ValidationResult.success()

    def validate(self, data: Any, record_type: str = "sanad") -> ValidationResult:
        """Validate Sanad or Claim data.

        Args:
            data: JSON data to validate
            record_type: "sanad" or "claim"

        Returns:
            ValidationResult
        """
        if record_type == "sanad":
            return self.validate_sanad(data)
        elif record_type == "claim":
            return self.validate_claim(data)
        else:
            return ValidationResult.fail_closed(f"Unknown record_type: {record_type}")


def validate_sanad_integrity(sanad: dict[str, Any]) -> ValidationResult:
    """Validate Sanad integrity - public API function.

    This is the required public interface for Sanad integrity validation.
    Enforces fail-closed behavior on all integrity violations.

    Args:
        sanad: Sanad dictionary to validate

    Returns:
        ValidationResult with pass/fail and any errors.
        FAILS CLOSED on any integrity violation.

    Error codes:
        - FAIL_CLOSED: Non-dict or None input
        - MISSING_SANAD_ID: sanad_id field missing
        - MISSING_CLAIM_ID: claim_id field missing
        - MISSING_TRANSMISSION_CHAIN: transmission_chain field missing
        - EMPTY_TRANSMISSION_CHAIN: transmission_chain is empty list
        - MISSING_PRIMARY_EVIDENCE: primary_evidence_id field missing
        - INVALID_UUID_FORMAT: ID field not in valid UUID format
        - INVALID_NODE_ID_FORMAT: node_id not in valid UUID format
        - MISSING_NODE_ID: transmission node missing node_id
        - MISSING_NODE_TYPE: transmission node missing node_type
        - INVALID_NODE_TYPE: node_type not in valid enum
        - MISSING_ACTOR_TYPE: transmission node missing actor_type
        - INVALID_ACTOR_TYPE: actor_type not in valid enum
        - MISSING_ACTOR_ID: transmission node missing actor_id
        - MISSING_TIMESTAMP: transmission node missing timestamp
        - SANAD_CYCLE_DETECTED: cycle detected in transmission chain
        - SANAD_MULTIPLE_ROOTS: more than one root node in chain
        - SANAD_ORPHAN_REFERENCE: parent reference points to non-existent node
        - SANAD_ORPHAN_NODE: nodes not connected to root
        - SANAD_NO_ROOT: all nodes have parents (no root)
        - GRADE_DEFECT_MISMATCH: FATAL defect with non-D grade
    """
    validator = SanadIntegrityValidator()
    return validator.validate_sanad(sanad)
