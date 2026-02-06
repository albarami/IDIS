"""Regression test: Gate 3 blocker strings from gate_3_blocked_status.json
must appear verbatim in both README.md and 09_phase_gated_rebuild_tasks.md,
with mapping columns (Rebuild Pack Doc, Task ID, Test Hook).

Fail-closed: if any blocker is missing or docs drift, this test fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
GATE3_JSON = REPO_ROOT / "docs" / "gates" / "gate_3_blocked_status.json"
README = REPO_ROOT / "docs" / "rebuild_pack" / "README.md"
PHASE_TASKS = REPO_ROOT / "docs" / "rebuild_pack" / "09_phase_gated_rebuild_tasks.md"

REQUIRED_HEADERS = ["Blocker", "Task", "Test Hook"]


@pytest.fixture(scope="module")
def gate3_blockers() -> list[str]:
    assert GATE3_JSON.exists(), f"Gate 3 status file missing: {GATE3_JSON}"
    data = json.loads(GATE3_JSON.read_text(encoding="utf-8"))
    blockers = data.get("blockers", [])
    assert len(blockers) == 5, f"Expected 5 blockers, got {len(blockers)}"
    return blockers


@pytest.fixture(scope="module")
def readme_text() -> str:
    assert README.exists(), f"README missing: {README}"
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def phase_tasks_text() -> str:
    assert PHASE_TASKS.exists(), f"Phase tasks doc missing: {PHASE_TASKS}"
    return PHASE_TASKS.read_text(encoding="utf-8")


class TestGate3BlockerMapping:
    """Every blocker from gate_3_blocked_status.json must appear verbatim
    in both mapping docs."""

    def test_all_blockers_in_readme(self, gate3_blockers: list[str], readme_text: str) -> None:
        for blocker in gate3_blockers:
            assert blocker in readme_text, f"Missing blocker in README.md: '{blocker}'"

    def test_all_blockers_in_phase_tasks(
        self, gate3_blockers: list[str], phase_tasks_text: str
    ) -> None:
        for blocker in gate3_blockers:
            assert blocker in phase_tasks_text, (
                f"Missing blocker in 09_phase_gated_rebuild_tasks.md: '{blocker}'"
            )

    def test_readme_has_mapping_headers(self, readme_text: str) -> None:
        for header in REQUIRED_HEADERS:
            assert header in readme_text, f"README.md missing mapping column header: '{header}'"

    def test_phase_tasks_has_mapping_headers(self, phase_tasks_text: str) -> None:
        for header in REQUIRED_HEADERS:
            assert header in phase_tasks_text, (
                f"09_phase_gated_rebuild_tasks.md missing mapping column header: '{header}'"
            )

    def test_blocker_count_matches_json(self, gate3_blockers: list[str], readme_text: str) -> None:
        """Ensure no blocker was silently dropped."""
        found = sum(1 for b in gate3_blockers if b in readme_text)
        assert found == len(gate3_blockers), f"README has {found}/{len(gate3_blockers)} blockers"
