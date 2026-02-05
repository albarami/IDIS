"""Doc-consistency tests for orchestration audit matrix.

These tests validate that the audit event matrix in the orchestration spec
covers all state transitions defined in the state machine section.

Per IDIS v6.3 trust invariants: every state transition MUST emit an audit event.
"""

import re
from pathlib import Path

import pytest

ORCHESTRATION_SPEC_PATH = (
    Path(__file__).parent.parent / "docs" / "rebuild_pack" / "03_pipeline_orchestration_spec.md"
)


def load_orchestration_spec() -> str:
    """Load the orchestration spec document."""
    if not ORCHESTRATION_SPEC_PATH.exists():
        pytest.fail(f"Orchestration spec not found at {ORCHESTRATION_SPEC_PATH}")
    return ORCHESTRATION_SPEC_PATH.read_text(encoding="utf-8")


def extract_state_machine_transitions(content: str) -> set[str]:
    """Extract transitions from §3.1 State Machine section.

    Looks for patterns like `STATE → STATE` in the state machine diagram.
    """
    transitions = set()

    # Match STATE → STATE patterns (using both arrow types)
    # Use (?:→|->) to match either arrow type
    pattern = r"(`?)([A-Z_]+)\s*(?:→|->)\s*([A-Z_]+)(`?)"

    for match in re.finditer(pattern, content):
        from_state = match.group(2)
        to_state = match.group(3)
        # Skip header-like entries and generic "Any" transitions
        if from_state not in ("Transition", "STATE") and to_state not in ("STATE",):
            transitions.add(f"{from_state} → {to_state}")

    return transitions


def extract_audit_matrix_transitions(content: str) -> set[str]:
    """Extract transitions from §6.1 Audit Event Matrix.

    Parses the markdown table to find documented transitions.
    """
    transitions = set()

    # Find section 6.1
    section_match = re.search(r"### 6\.1.*?(?=###|\Z)", content, re.DOTALL)
    if not section_match:
        return transitions

    section_content = section_match.group(0)

    # Match table rows with transitions
    # Pattern: | `FROM → TO` | event_type | fields |
    pattern = r"\|\s*`?([A-Z_]+(?:\s*(?:→|->)\s*[A-Z_]+)?)`?\s*\|"

    for match in re.finditer(pattern, section_content):
        transition_text = match.group(1)
        # Normalize arrow format
        if "→" in transition_text or "->" in transition_text:
            normalized = re.sub(r"\s*(?:→|->)\s*", " → ", transition_text)
            transitions.add(normalized)

    return transitions


def has_transition_in_matrix(content: str, from_state: str, to_state: str) -> bool:
    """Check if a specific transition exists in the audit matrix."""
    matrix_transitions = extract_audit_matrix_transitions(content)
    target = f"{from_state} → {to_state}"

    # Also check for "Any → STATE" patterns
    any_pattern = f"Any → {to_state}"

    return target in matrix_transitions or any_pattern in matrix_transitions


class TestAuditMatrixCoversStateMachine:
    """Test that audit matrix covers all state machine transitions."""

    def test_audit_matrix_covers_state_machine(self) -> None:
        """Verify §6.1 audit matrix covers every transition in §3.1 state machine.

        This is a structural doc-consistency check per Codex requirements.
        """
        content = load_orchestration_spec()

        # Get all transitions from state machine
        sm_transitions = extract_state_machine_transitions(content)

        # Get all transitions from audit matrix
        audit_transitions = extract_audit_matrix_transitions(content)

        # Filter out "Any → X" from state machine (these are covered by generic handlers)
        specific_sm_transitions = {t for t in sm_transitions if not t.startswith("Any →")}

        # Check coverage: every specific transition should be in audit matrix
        # OR covered by "Any → STATE" pattern
        missing = set()
        for transition in specific_sm_transitions:
            parts = transition.split(" → ")
            if len(parts) == 2:
                from_state, to_state = parts
                # Check if transition exists directly or via "Any" pattern
                if transition not in audit_transitions:
                    any_pattern = f"Any → {to_state}"
                    if any_pattern not in audit_transitions:
                        missing.add(transition)

        assert not missing, (
            f"Audit matrix missing transitions: {missing}. "
            "Per v6.3 trust invariants, every state transition MUST emit an audit event."
        )

    def test_audit_event_enriched_to_debating(self) -> None:
        """Verify ENRICHED → DEBATING transition has audit event defined."""
        content = load_orchestration_spec()
        assert has_transition_in_matrix(content, "ENRICHED", "DEBATING"), (
            "Missing audit event for ENRICHED → DEBATING transition"
        )

    def test_audit_event_generating_to_generated(self) -> None:
        """Verify GENERATING → GENERATED transition has audit event defined."""
        content = load_orchestration_spec()
        assert has_transition_in_matrix(content, "GENERATING", "GENERATED"), (
            "Missing audit event for GENERATING → GENERATED transition"
        )

    def test_audit_event_blocked_to_extracting(self) -> None:
        """Verify BLOCKED → EXTRACTING transition has audit event defined."""
        content = load_orchestration_spec()
        assert has_transition_in_matrix(content, "BLOCKED", "EXTRACTING"), (
            "Missing audit event for BLOCKED → EXTRACTING transition"
        )

    def test_audit_event_blocked_to_grading(self) -> None:
        """Verify BLOCKED → GRADING transition has audit event defined."""
        content = load_orchestration_spec()
        assert has_transition_in_matrix(content, "BLOCKED", "GRADING"), (
            "Missing audit event for BLOCKED → GRADING transition"
        )
